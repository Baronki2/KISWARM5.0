"""
KISWARM v4.3 — Module 30: OT Network Monitor
============================================
Passive industrial protocol traffic analysis.
No packets injected. Observe only.
"""

import math
import hashlib
import datetime
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any

# Supported OT protocols and their safe function codes
PROTOCOL_SAFE_FC: Dict[str, List[int]] = {
    "modbus":    [1, 2, 3, 4],          # read coils/inputs/holding/input registers
    "dnp3":      [1, 2, 3, 4, 13, 20],  # read + direct operate confirm
    "opc_ua":    [0],                    # any (opaque, flagged by rate)
    "profinet":  [0],
    "ethernetip":[0],
    "hart_ip":   [0],
    "iec61850":  [0],
}

SUSPICIOUS_FC: Dict[str, List[int]] = {
    "modbus": [5, 6, 8, 15, 16, 22, 23, 43],  # write / diagnostic / encapsulate
    "dnp3":   [3, 4, 5, 19, 20, 21],           # direct operate / freeze / cold restart
}

OT_ALERT_TYPES = {
    "RATE_ANOMALY":    "Command rate outside 3σ baseline",
    "NEW_SOURCE_IP":   "First-time source IP communicating with asset",
    "SUSPICIOUS_FC":   "Suspicious function code detected",
    "LARGE_PAYLOAD":   "Unusually large payload — possible firmware push",
    "OFF_HOURS":       "Engineering station active outside permitted hours",
    "UNKNOWN_PROTOCOL":"Unknown/unexpected protocol on OT segment",
}


@dataclass
class OTAlert:
    alert_id: str
    segment_id: str
    alert_type: str
    severity: str
    description: str
    recommendation: str
    src: str
    dst: str
    protocol: str
    timestamp: str = field(default_factory=lambda: datetime.datetime.now().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return vars(self)


@dataclass
class OTSegment:
    segment_id: str
    subnet: str
    protocols: List[str]
    permitted_hours: Optional[Dict[str, int]] = None   # {"start": 6, "end": 18}
    registered_at: str = field(default_factory=lambda: datetime.datetime.now().isoformat())


class OTNetworkMonitor:
    """
    KISWARM v4.3 — Passive OT network traffic analyser.
    Ingests packet metadata (NO payload content) and detects anomalies.
    """

    def __init__(self, sigma_threshold: float = 3.0, window: int = 200):
        self._sigma = sigma_threshold
        self._window = window
        self._segments: Dict[str, OTSegment] = {}
        self._known_ips: Dict[str, set] = {}          # segment_id -> {ip}
        self._baselines: Dict[str, Dict[str, List[float]]] = {}   # seg -> {proto:fc -> [rates]}
        self._alerts: List[OTAlert] = []
        self._packet_count: int = 0
        self._stats: Dict[str, int] = {"packets": 0, "alerts": 0, "segments": 0}

    # ── Segment registration ──────────────────────────────────────────────────

    def register_segment(self, segment_id: str, subnet: str,
                         protocols: List[str],
                         permitted_hours: Optional[Dict[str, int]] = None) -> Dict[str, Any]:
        seg = OTSegment(segment_id=segment_id, subnet=subnet,
                        protocols=protocols, permitted_hours=permitted_hours)
        self._segments[segment_id] = seg
        self._known_ips[segment_id] = set()
        self._baselines[segment_id] = {}
        self._stats["segments"] += 1
        return {"registered": segment_id, "subnet": subnet, "protocols": protocols}

    # ── Packet ingestion ──────────────────────────────────────────────────────

    def ingest_packet(self, segment_id: str, protocol: str,
                      function_code: int, src: str, dst: str,
                      payload_bytes: int, rate_hz: float = 1.0) -> List[OTAlert]:
        self._packet_count += 1
        self._stats["packets"] += 1
        alerts: List[OTAlert] = []

        seg = self._segments.get(segment_id)

        # ① Unknown source IP
        if segment_id not in self._known_ips:
            self._known_ips[segment_id] = set()
        if src not in self._known_ips[segment_id]:
            if len(self._known_ips[segment_id]) > 0:  # not the very first
                a = self._make_alert(
                    segment_id, "NEW_SOURCE_IP", "HIGH",
                    f"First-time source {src} on {protocol} to {dst}.",
                    "Verify this IP is an authorised engineering station. Block if unknown.",
                    src, dst, protocol,
                )
                alerts.append(a)
            self._known_ips[segment_id].add(src)

        # ② Suspicious function code
        bad_fcs = SUSPICIOUS_FC.get(protocol.lower(), [])
        if function_code in bad_fcs:
            a = self._make_alert(
                segment_id, "SUSPICIOUS_FC", "HIGH",
                f"{protocol.upper()} FC {function_code} ({self._fc_name(protocol, function_code)}) from {src}.",
                "Confirm write/control command is authorised. Correlate with change management.",
                src, dst, protocol,
            )
            alerts.append(a)

        # ③ Large payload (possible firmware push > 512 bytes to field device)
        if payload_bytes > 512:
            a = self._make_alert(
                segment_id, "LARGE_PAYLOAD", "MEDIUM",
                f"Large {payload_bytes}B payload on {protocol} from {src} to {dst}.",
                "Check if firmware update is scheduled. Unscheduled firmware pushes are high-risk.",
                src, dst, protocol,
            )
            alerts.append(a)

        # ④ Unknown protocol on segment
        if seg and protocol.lower() not in [p.lower() for p in seg.protocols]:
            a = self._make_alert(
                segment_id, "UNKNOWN_PROTOCOL", "MEDIUM",
                f"Unexpected protocol {protocol.upper()} on segment {segment_id}.",
                "Investigate source. Only registered protocols should appear on OT segments.",
                src, dst, protocol,
            )
            alerts.append(a)

        # ⑤ Off-hours activity
        if seg and seg.permitted_hours:
            hour = datetime.datetime.now().hour
            start = seg.permitted_hours.get("start", 6)
            end = seg.permitted_hours.get("end", 18)
            if not (start <= hour < end):
                a = self._make_alert(
                    segment_id, "OFF_HOURS", "MEDIUM",
                    f"Activity from engineering station {src} at hour {hour} (permitted {start}:00–{end}:00).",
                    "Verify authorisation for off-hours access. May indicate compromised account.",
                    src, dst, protocol,
                )
                alerts.append(a)

        # ⑥ Rate anomaly
        rate_alert = self._check_rate(segment_id, protocol, function_code, rate_hz, src, dst)
        if rate_alert:
            alerts.append(rate_alert)

        for a in alerts:
            self._alerts.append(a)
            self._stats["alerts"] += 1

        return alerts

    # ── Queries ───────────────────────────────────────────────────────────────

    def get_alerts(self, segment_id: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        filtered = self._alerts
        if segment_id:
            filtered = [a for a in self._alerts if a.segment_id == segment_id]
        return [a.to_dict() for a in filtered[-limit:]]

    def get_baseline(self, segment_id: str) -> Dict[str, Any]:
        raw = self._baselines.get(segment_id, {})
        result: Dict[str, Any] = {}
        for key, history in raw.items():
            if history:
                mean = sum(history) / len(history)
                var = sum((x - mean) ** 2 for x in history) / len(history)
                result[key] = {
                    "mean_hz": round(mean, 4),
                    "std_hz": round(math.sqrt(var), 4),
                    "samples": len(history),
                }
        return result

    def get_segments(self) -> List[Dict[str, Any]]:
        return [{"segment_id": s.segment_id, "subnet": s.subnet,
                 "protocols": s.protocols, "known_ips": len(self._known_ips.get(s.segment_id, set()))}
                for s in self._segments.values()]

    def get_stats(self) -> Dict[str, Any]:
        return {**self._stats, "total_alerts": len(self._alerts), "packet_count": self._packet_count}

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _check_rate(self, segment_id: str, protocol: str,
                    function_code: int, rate_hz: float,
                    src: str, dst: str) -> Optional[OTAlert]:
        key = f"{protocol}:{function_code}"
        if segment_id not in self._baselines:
            self._baselines[segment_id] = {}
        history = self._baselines[segment_id].setdefault(key, [])
        history.append(rate_hz)
        if len(history) > self._window:
            history.pop(0)

        if len(history) < 10:
            return None

        mean = sum(history) / len(history)
        var = sum((x - mean) ** 2 for x in history) / len(history)
        std = math.sqrt(var) if var > 0 else 0.001
        z = abs(rate_hz - mean) / std

        if z > self._sigma:
            return self._make_alert(
                segment_id, "RATE_ANOMALY",
                "HIGH" if z > 5.0 else "MEDIUM",
                f"{protocol.upper()} FC{function_code} rate {rate_hz:.2f} Hz — {z:.1f}σ from baseline ({mean:.2f} Hz).",
                "Check for replay attack, misconfigured PLC scanner, or rogue automation script.",
                src, dst, protocol,
            )
        return None

    def _make_alert(self, segment_id: str, alert_type: str, severity: str,
                    description: str, recommendation: str,
                    src: str, dst: str, protocol: str) -> OTAlert:
        aid = hashlib.md5(f"{segment_id}{alert_type}{src}{dst}{datetime.datetime.now().isoformat()}".encode()).hexdigest()[:12]
        return OTAlert(
            alert_id=aid,
            segment_id=segment_id,
            alert_type=alert_type,
            severity=severity,
            description=f"[{OT_ALERT_TYPES.get(alert_type, alert_type)}] {description}",
            recommendation=recommendation,
            src=src,
            dst=dst,
            protocol=protocol,
        )

    @staticmethod
    def _fc_name(protocol: str, fc: int) -> str:
        names = {
            "modbus": {5: "Write Single Coil", 6: "Write Single Register",
                       8: "Diagnostics", 15: "Write Multiple Coils",
                       16: "Write Multiple Registers", 43: "Encapsulated Interface Transport"},
            "dnp3":   {3: "Direct Operate", 4: "Direct Operate No Ack",
                       5: "Freeze", 19: "Warm Restart", 20: "Cold Restart"},
        }
        return names.get(protocol.lower(), {}).get(fc, f"FC{fc}")
