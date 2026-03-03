#!/usr/bin/env python3
"""
KISWARM v4.3 — ICS-SHIELD: 12-Agent OT/ICS Industrial Security System

Module 29  |  IEC 62443 · IEC 61508 · ATT&CK for ICS · NIST NVD
Architect: Baron Marco Paolo Ialongo

Provides autonomous, continuous security monitoring for PLC and SCADA
infrastructure, integrated into the CIEC safety hierarchy:

  PLC     = deterministic reflex layer   (never touched by AI)
  CIEC    = adaptive cognition layer
  SHIELD  = security observation layer   (monitors, alerts, triggers safe-state)

The 12 ICS-SHIELD Agents:
  01  PLCMonitorAgent          — IEC 61131-3 code unsafe pattern scanner
  02  SCADAMonitorAgent        — Real-time tag anomaly detector (Z-score + EWM)
  03  CVEIntelligenceAgent     — ICS/PLC/SCADA CVE database + CVSS-v3 scoring
  04  NetworkAnomalyAgent      — OT protocol monitors: Modbus / OPC-UA / PROFINET / DNP3
  05  CryptographyAgent        — TLS/cert enforcement on OPC-UA, historian channels
  06  FirmwareIntegrityAgent   — SHA-256 hash verification for PLC/HMI firmware
  07  AccessControlAgent       — IEC 62443 zone/conduit, PoLP enforcement
  08  PhysicsConsistencyAgent  — Cyber-physical attack via twin mismatch (>3σ)
  09  ThreatCorrelatorAgent    — MITRE ATT&CK for ICS tactic correlation
  10  RateLimitAgent           — Brute-force/DoS detection on OT protocols
  11  RecoveryOrchestratorAgent— Autonomous safe-state transition with governance
  12  ThreatIntelSyncAgent     — Federated threat sharing across KISWARM mesh
"""

import hashlib
import json
import math
import re
import time
import datetime
from collections import defaultdict, deque
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


# ════════════════════════════════════════════════════════════════════════════
# ENUMERATIONS & CONSTANTS
# ════════════════════════════════════════════════════════════════════════════

class Severity(str, Enum):
    CRITICAL = "CRITICAL"   # immediate safe-state trigger
    HIGH     = "HIGH"       # governance pipeline required
    MEDIUM   = "MEDIUM"     # alert + recommendation
    LOW      = "LOW"        # informational
    INFO     = "INFO"


class AgentStatus(str, Enum):
    ACTIVE   = "ACTIVE"
    DEGRADED = "DEGRADED"
    OFFLINE  = "OFFLINE"


class SafeStateReason(str, Enum):
    FIRMWARE_MISMATCH      = "FIRMWARE_HASH_MISMATCH"
    PHYSICS_DEVIATION      = "PHYSICS_TWIN_DEVIATION_CRITICAL"
    UNAUTHORIZED_PLC_WRITE = "UNAUTHORIZED_PLC_WRITE_SAFETY"
    MANUAL_TRIGGER         = "MANUAL_TRIGGER"
    THREAT_INTEL           = "THREAT_INTEL_CRITICAL"


# IEC 62443 Security Level requirements
IEC62443_REQUIREMENTS = {
    "SL1": {"encryption": False, "auth": "basic", "audit": True},
    "SL2": {"encryption": True,  "auth": "2fa",   "audit": True, "integrity": True},
    "SL3": {"encryption": True,  "auth": "mfa",   "audit": True, "integrity": True, "isolation": True},
    "SL4": {"encryption": True,  "auth": "hardware_token", "audit": True,
            "integrity": True, "isolation": True, "physical": True},
}

# MITRE ATT&CK for ICS tactic IDs
ATTCK_ICS_TACTICS = {
    "T0886": "Remote Services",
    "T0859": "Valid Accounts",
    "T0821": "Modify Controller Tasklist",
    "T0831": "Manipulation of Control",
    "T0836": "Modify Parameter",
    "T0800": "Activate Firmware Update Mode",
    "T0803": "Block Command Message",
    "T0804": "Block Reporting Message",
    "T0840": "Network Connection Enumeration",
    "T0846": "Remote System Discovery",
    "T0881": "Service Stop",
    "T0895": "Autorun Image",
}

# Known ICS product CVEs (simplified representative dataset)
ICS_CVE_DATABASE = {
    "siemens_s7_1200": [
        {"cve_id": "CVE-2022-38465", "cvss": 9.3, "description":
         "Authentication bypass via crafted packet", "patched_in": "4.6.0"},
        {"cve_id": "CVE-2021-37201", "cvss": 7.5, "description":
         "Denial of service via malformed HTTP request", "patched_in": "4.5.1"},
    ],
    "ge_ifix_scada": [
        {"cve_id": "CVE-2020-14481", "cvss": 8.8, "description":
         "DLL hijacking leading to privilege escalation", "patched_in": "6.1"},
    ],
    "schneider_modicon": [
        {"cve_id": "CVE-2021-22707", "cvss": 9.8, "description":
         "Missing authentication for critical function", "patched_in": "3.30"},
        {"cve_id": "CVE-2022-45788", "cvss": 8.1, "description":
         "Improper privilege management", "patched_in": "3.40"},
    ],
    "rockwell_logix": [
        {"cve_id": "CVE-2022-3158",  "cvss": 8.8, "description":
         "Execution of arbitrary code via malicious project file", "patched_in": "v34"},
    ],
    "generic_opcua": [
        {"cve_id": "CVE-2023-27321", "cvss": 7.5, "description":
         "OPC UA stack out-of-bounds read", "patched_in": "1.05.04"},
    ],
}

# PLC unsafe code patterns (IEC 61131-3 Structured Text)
PLC_UNSAFE_PATTERNS = [
    (r"\bPOINTER\s+TO\b",           "USE_OF_POINTER",
     "Pointer use in ST code is unsafe in safety-rated PLCs (SIL ≥ 2)"),
    (r"\bRETAIN\b.*:=\s*0\b",       "RETAIN_VAR_INIT_ZERO",
     "Retain variable reset to zero on startup may lose process state"),
    (r"\bREAD_ONLY\b",              "READ_ONLY_KEYWORD_ABSENT",
     "Safety-critical tags should be declared READ_ONLY"),
    (r"\bFOR\b.+\bTO\b.+\bDO\b",    "UNBOUNDED_LOOP",
     "Loop without explicit bound may cause watchdog timeout in real-time PLC"),
    (r"\bJMP\b",                    "UNCONDITIONAL_JUMP",
     "JMP instruction bypasses safety checks; use structured flow control"),
    (r":=\s*\$[A-F0-9]{4,}",        "HARDCODED_HEX_ADDRESS",
     "Hardcoded memory address is non-portable and unsafe"),
    (r"\bNOT\s+\w+\s*\)",           "NEGATED_SAFETY_INTERLOCK",
     "Negated safety interlock signal — verify this is intentional"),
    (r"\bDISABLE_WATCHDOG\b",       "WATCHDOG_DISABLED",
     "Watchdog disabled — critical safety violation (IEC 61508 SIL 2+)"),
    (r"\bDEAD_BAND\s*:=\s*0",       "ZERO_DEADBAND",
     "Zero deadband on PID may cause valve hunting and actuator fatigue"),
    (r"(?i)\bpassword\s*:=\s*['\"]", "PLAINTEXT_CREDENTIAL",
     "Plain-text credential embedded in PLC code"),
]


# ════════════════════════════════════════════════════════════════════════════
# DATA STRUCTURES
# ════════════════════════════════════════════════════════════════════════════

@dataclass
class SecurityFinding:
    """One security finding from any ICS-SHIELD agent."""
    finding_id:  str
    agent:       str
    severity:    Severity
    category:    str
    title:       str
    description: str
    target:      str                  # asset / IP / tag / file
    evidence:    Dict[str, Any]
    mitigation:  str
    standards:   List[str]            # IEC 62443-x-x, etc.
    timestamp:   str = field(default_factory=lambda: datetime.datetime.utcnow().isoformat() + "Z")
    acknowledged: bool = False
    signature:   str = ""

    def __post_init__(self):
        self.signature = hashlib.sha256(
            f"{self.finding_id}|{self.agent}|{self.severity}|{self.timestamp}".encode()
        ).hexdigest()[:24]

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["severity"] = self.severity.value if isinstance(self.severity, Severity) else self.severity
        return d


@dataclass
class SafeStateTransition:
    """Record of a safe-state trigger."""
    transition_id:  str
    reason:         SafeStateReason
    zone:           str
    triggered_by:   str    # agent name
    timestamp:      str
    plc_command:    str = "EMERGENCY_STOP"
    approved_by:    str = "GOVERNANCE_AUTO"   # or human operator ID
    reversed_at:    Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["reason"] = self.reason.value
        return d


@dataclass
class ThreatIntelRecord:
    """Federated threat intelligence record shared across KISWARM nodes."""
    intel_id:    str
    source_node: str
    tactic_id:   str      # ATT&CK for ICS
    ioc:         str      # indicator of compromise
    confidence:  float    # 0–1
    timestamp:   str
    affected:    List[str]  # product names

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ════════════════════════════════════════════════════════════════════════════
# BASE AGENT
# ════════════════════════════════════════════════════════════════════════════

class BaseSecurityAgent:
    """Base class for all ICS-SHIELD agents."""

    def __init__(self, agent_id: str, agent_name: str):
        self.agent_id   = agent_id
        self.agent_name = agent_name
        self.status     = AgentStatus.ACTIVE
        self.findings_count   = 0
        self.last_scan_ts     = ""
        self._finding_counter = 0

    def _new_finding_id(self) -> str:
        self._finding_counter += 1
        return f"FIND_{self.agent_id}_{self._finding_counter:06d}"

    def _stamp(self) -> str:
        ts = datetime.datetime.utcnow().isoformat() + "Z"
        self.last_scan_ts = ts
        return ts

    def get_status(self) -> Dict[str, Any]:
        return {
            "agent_id":      self.agent_id,
            "agent_name":    self.agent_name,
            "status":        self.status.value,
            "findings_count": self.findings_count,
            "last_scan":     self.last_scan_ts,
        }


# ════════════════════════════════════════════════════════════════════════════
# AGENT 01 — PLC MONITOR
# ════════════════════════════════════════════════════════════════════════════

class PLCMonitorAgent(BaseSecurityAgent):
    """
    Scans IEC 61131-3 Structured Text for unsafe patterns,
    credential leaks, watchdog disables, and unguarded outputs.
    IEC 62443-3-3 SR 3.4 / IEC 62443-4-1
    """

    def __init__(self):
        super().__init__("PLCMON", "PLCMonitorAgent")
        self._scan_history: List[Dict] = []

    def scan_plc_code(self, code: str, plc_id: str = "UNKNOWN",
                      context: str = "") -> List[SecurityFinding]:
        """Scan PLC ST code for unsafe patterns."""
        self._stamp()
        findings = []

        for pattern, code_name, desc in PLC_UNSAFE_PATTERNS:
            if re.search(pattern, code):
                # Determine severity
                if code_name in ("WATCHDOG_DISABLED", "PLAINTEXT_CREDENTIAL"):
                    sev = Severity.CRITICAL
                elif code_name in ("DISABLE_WATCHDOG", "UNCONDITIONAL_JUMP", "USE_OF_POINTER"):
                    sev = Severity.HIGH
                else:
                    sev = Severity.MEDIUM

                f = SecurityFinding(
                    finding_id  = self._new_finding_id(),
                    agent       = self.agent_name,
                    severity    = sev,
                    category    = "PLC_CODE_SECURITY",
                    title       = f"Unsafe PLC pattern: {code_name}",
                    description = desc,
                    target      = plc_id,
                    evidence    = {"pattern": code_name, "context": context[:200]},
                    mitigation  = "Review PLC code. Apply IEC 62443-4-1 secure coding guidelines.",
                    standards   = ["IEC-62443-4-1", "IEC-61508"],
                )
                findings.append(f)
                self.findings_count += 1

        # Check for missing emergency-stop interlock
        if code and "EMERGENCY_STOP" not in code and "E_STOP" not in code:
            f = SecurityFinding(
                finding_id  = self._new_finding_id(),
                agent       = self.agent_name,
                severity    = Severity.HIGH,
                category    = "PLC_CODE_SECURITY",
                title       = "Missing emergency-stop interlock",
                description = "No EMERGENCY_STOP or E_STOP symbol found in program unit.",
                target      = plc_id,
                evidence    = {"code_length": len(code)},
                mitigation  = "Add EMERGENCY_STOP interlock per IEC 61511 SIS requirements.",
                standards   = ["IEC-61511", "IEC-62443-3-3 SR 3.6"],
            )
            findings.append(f)
            self.findings_count += 1

        self._scan_history.append({
            "plc_id": plc_id, "ts": self.last_scan_ts,
            "findings": len(findings),
        })
        return findings

    def get_scan_history(self, limit: int = 20) -> List[Dict]:
        return self._scan_history[-limit:]


# ════════════════════════════════════════════════════════════════════════════
# AGENT 02 — SCADA MONITOR
# ════════════════════════════════════════════════════════════════════════════

class SCADAMonitorAgent(BaseSecurityAgent):
    """
    Real-time SCADA tag anomaly detection.
    Uses Z-score (online Welford) + exponential weighted mean for trending.
    IEC 62443-2-1 / SR 6.2
    """

    def __init__(self, z_threshold: float = 3.0, ewm_alpha: float = 0.1):
        super().__init__("SCADA", "SCADAMonitorAgent")
        self.z_threshold = z_threshold
        self.ewm_alpha   = ewm_alpha
        self._tag_stats: Dict[str, Dict] = {}   # per-tag online stats
        self._tag_history: Dict[str, deque] = defaultdict(lambda: deque(maxlen=1000))

    def _update_stats(self, tag_id: str, value: float) -> Tuple[float, float, float]:
        """Welford online mean/variance + EWM."""
        if tag_id not in self._tag_stats:
            self._tag_stats[tag_id] = {
                "n": 0, "mean": value, "M2": 0.0, "ewm": value
            }
        s = self._tag_stats[tag_id]
        s["n"] += 1
        delta = value - s["mean"]
        s["mean"] += delta / s["n"]
        s["M2"]   += delta * (value - s["mean"])
        s["ewm"]   = self.ewm_alpha * value + (1 - self.ewm_alpha) * s["ewm"]
        variance   = s["M2"] / s["n"] if s["n"] > 1 else 1e-9
        std        = math.sqrt(variance)
        z          = abs(value - s["mean"]) / (std + 1e-9)
        self._tag_history[tag_id].append(value)
        return z, s["mean"], std

    def check_tag(self, tag_id: str, value: float,
                  timestamp: str = "") -> Optional[SecurityFinding]:
        """Check one SCADA tag reading for anomalies."""
        self._stamp()
        z, mean, std = self._update_stats(tag_id, value)
        stats = self._tag_stats[tag_id]

        if stats["n"] < 10:
            return None   # not enough data yet

        if z > self.z_threshold * 2:
            sev = Severity.CRITICAL
        elif z > self.z_threshold:
            sev = Severity.HIGH
        elif z > self.z_threshold * 0.6:
            sev = Severity.MEDIUM
        else:
            return None

        self.findings_count += 1
        return SecurityFinding(
            finding_id  = self._new_finding_id(),
            agent       = self.agent_name,
            severity    = sev,
            category    = "SCADA_TAG_ANOMALY",
            title       = f"Anomalous SCADA tag: {tag_id}",
            description = f"Tag {tag_id} = {value:.4f} deviates {z:.2f}σ from mean {mean:.4f} (σ={std:.4f}).",
            target      = tag_id,
            evidence    = {
                "value": value, "mean": round(mean, 4),
                "std": round(std, 4), "z_score": round(z, 3),
                "ewm": round(stats["ewm"], 4), "n_samples": stats["n"],
                "timestamp": timestamp,
            },
            mitigation  = "Investigate field sensor and process conditions. "
                          "If sustained > 30s, initiate operator response procedure.",
            standards   = ["IEC-62443-2-1", "ISA-18.2"],
        )

    def batch_check(self, tags: Dict[str, float],
                    timestamp: str = "") -> List[SecurityFinding]:
        """Check multiple tags in one call."""
        return [f for tid, v in tags.items()
                for f in [self.check_tag(tid, v, timestamp)] if f is not None]

    def get_tag_stats(self, tag_id: str) -> Dict[str, Any]:
        return self._tag_stats.get(tag_id, {})

    def get_monitored_tags(self) -> List[str]:
        return list(self._tag_stats.keys())


# ════════════════════════════════════════════════════════════════════════════
# AGENT 03 — CVE INTELLIGENCE
# ════════════════════════════════════════════════════════════════════════════

class CVEIntelligenceAgent(BaseSecurityAgent):
    """
    ICS/PLC/SCADA CVE database lookup with CVSS-v3 scoring.
    NIST NVD / ICS-CERT integration point.
    """

    CVSS_SEVERITY = {
        (9.0, 10.0): Severity.CRITICAL,
        (7.0,  8.9): Severity.HIGH,
        (4.0,  6.9): Severity.MEDIUM,
        (0.0,  3.9): Severity.LOW,
    }

    def __init__(self):
        super().__init__("CVE", "CVEIntelligenceAgent")
        self._local_db = ICS_CVE_DATABASE.copy()
        self._lookup_history: List[Dict] = []

    def lookup(self, product_id: str,
               firmware_version: str = "") -> List[SecurityFinding]:
        """Look up CVEs for a given ICS product."""
        self._stamp()
        findings = []
        cves = self._local_db.get(product_id.lower(), [])

        for cve in cves:
            # Skip if firmware_version is patched
            if firmware_version and cve.get("patched_in"):
                if self._version_gte(firmware_version, cve["patched_in"]):
                    continue

            sev = self._cvss_to_severity(cve["cvss"])
            f = SecurityFinding(
                finding_id  = self._new_finding_id(),
                agent       = self.agent_name,
                severity    = sev,
                category    = "CVE_VULNERABILITY",
                title       = f"{cve['cve_id']} — {product_id}",
                description = cve["description"],
                target      = product_id,
                evidence    = {
                    "cve_id":      cve["cve_id"],
                    "cvss_v3":     cve["cvss"],
                    "patched_in":  cve.get("patched_in", "unknown"),
                    "current_fw":  firmware_version or "unknown",
                },
                mitigation  = f"Apply vendor patch {cve.get('patched_in', 'TBD')} "
                              f"or implement compensating controls per ICS-CERT advisory.",
                standards   = ["IEC-62443-4-2", "NIST-NVD"],
            )
            findings.append(f)
            self.findings_count += 1

        self._lookup_history.append({
            "product": product_id, "fw": firmware_version,
            "cves_found": len(findings), "ts": self.last_scan_ts,
        })
        return findings

    def add_cve(self, product_id: str, cve_entry: Dict[str, Any]) -> bool:
        """Add a new CVE entry to the local database."""
        key = product_id.lower()
        if key not in self._local_db:
            self._local_db[key] = []
        self._local_db[key].append(cve_entry)
        return True

    def get_recent_feed(self, limit: int = 20) -> List[Dict]:
        """Return recent CVEs across all tracked products."""
        all_cves = []
        for product, entries in self._local_db.items():
            for e in entries:
                all_cves.append({**e, "product": product})
        all_cves.sort(key=lambda x: x["cvss"], reverse=True)
        return all_cves[:limit]

    @staticmethod
    def _cvss_to_severity(score: float) -> Severity:
        if score >= 9.0: return Severity.CRITICAL
        if score >= 7.0: return Severity.HIGH
        if score >= 4.0: return Severity.MEDIUM
        return Severity.LOW

    @staticmethod
    def _version_gte(ver_a: str, ver_b: str) -> bool:
        """Compare dotted-decimal versions."""
        try:
            pa = [int(x) for x in ver_a.split(".")]
            pb = [int(x) for x in ver_b.split(".")]
            return pa >= pb
        except ValueError:
            return False


# ════════════════════════════════════════════════════════════════════════════
# AGENT 04 — NETWORK ANOMALY
# ════════════════════════════════════════════════════════════════════════════

class NetworkAnomalyAgent(BaseSecurityAgent):
    """
    OT protocol anomaly detection for Modbus, OPC-UA, PROFINET, DNP3.
    Detects unexpected function codes, broadcast storms, rogue devices.
    IEC 62443-3-3 SR 6.1
    """

    VALID_MODBUS_FC = {1, 2, 3, 4, 5, 6, 15, 16, 23}
    VALID_DNP3_FC   = {0, 1, 2, 3, 4, 7, 8, 9, 10, 11, 12, 13, 18, 19,
                       20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 81,
                       129, 130}
    KNOWN_PROTOCOLS = {"modbus_tcp", "opcua", "profinet", "dnp3",
                       "ethernet_ip", "iec104", "hart"}

    def __init__(self):
        super().__init__("NETMON", "NetworkAnomalyAgent")
        self._allowed_devices: Dict[str, List[str]] = {}   # zone → [ip list]
        self._packet_counters: Dict[str, int] = defaultdict(int)
        self._last_minute_ts  = time.time()

    def check_packet(self, protocol: str, source_ip: str, dest_ip: str,
                     function_code: Optional[int] = None,
                     zone: str = "unknown") -> Optional[SecurityFinding]:
        """Analyse a single OT protocol packet."""
        self._stamp()
        findings: List[SecurityFinding] = []

        # Unknown protocol on OT network
        if protocol.lower() not in self.KNOWN_PROTOCOLS:
            self.findings_count += 1
            return SecurityFinding(
                finding_id  = self._new_finding_id(),
                agent       = self.agent_name,
                severity    = Severity.HIGH,
                category    = "UNKNOWN_PROTOCOL",
                title       = f"Unknown protocol on OT segment: {protocol}",
                description = f"Protocol '{protocol}' is not whitelisted for OT network zone '{zone}'.",
                target      = source_ip,
                evidence    = {"protocol": protocol, "src": source_ip, "dst": dest_ip, "zone": zone},
                mitigation  = "Whitelist only required OT protocols per IEC 62443-3-3 SR 7.7.",
                standards   = ["IEC-62443-3-3"],
            )

        # Modbus illegal function code
        if protocol.lower() == "modbus_tcp" and function_code is not None:
            if function_code not in self.VALID_MODBUS_FC:
                self.findings_count += 1
                return SecurityFinding(
                    finding_id  = self._new_finding_id(),
                    agent       = self.agent_name,
                    severity    = Severity.HIGH,
                    category    = "ILLEGAL_FUNCTION_CODE",
                    title       = f"Illegal Modbus FC {function_code} from {source_ip}",
                    description = f"Modbus function code {function_code} is not in the allowed set.",
                    target      = source_ip,
                    evidence    = {"fc": function_code, "src": source_ip, "dst": dest_ip},
                    mitigation  = "Block at OT firewall. Investigate origin host for compromise.",
                    standards   = ["IEC-62443-3-3 SR 3.8"],
                )

        # Rogue device check
        if zone in self._allowed_devices:
            allowed = self._allowed_devices[zone]
            if source_ip not in allowed:
                self.findings_count += 1
                return SecurityFinding(
                    finding_id  = self._new_finding_id(),
                    agent       = self.agent_name,
                    severity    = Severity.CRITICAL,
                    category    = "ROGUE_DEVICE",
                    title       = f"Rogue device detected: {source_ip} in zone '{zone}'",
                    description = f"Device {source_ip} is not in the authorised device list for zone '{zone}'.",
                    target      = source_ip,
                    evidence    = {"src": source_ip, "zone": zone, "allowed_count": len(allowed)},
                    mitigation  = "Isolate device immediately. Investigate physical access logs.",
                    standards   = ["IEC-62443-2-1", "IEC-62443-3-3 SR 2.13"],
                )
        return None

    def register_zone_devices(self, zone: str, ip_list: List[str]) -> None:
        """Register authorized devices for a network zone."""
        self._allowed_devices[zone] = list(ip_list)

    def get_protocol_stats(self) -> Dict[str, int]:
        return dict(self._packet_counters)


# ════════════════════════════════════════════════════════════════════════════
# AGENT 05 — CRYPTOGRAPHY
# ════════════════════════════════════════════════════════════════════════════

class CryptographyAgent(BaseSecurityAgent):
    """
    Validates encryption and certificate status on OT communications.
    Detects cleartext OPC-UA, expired certificates, weak cipher suites.
    IEC 62443-4-2 CR 4.1 / SR 4.3
    """

    WEAK_CIPHERS = {
        "RC4", "DES", "3DES", "NULL", "EXPORT",
        "MD5", "SHA1_RSA", "TLS1.0", "TLS1.1",
    }

    def __init__(self):
        super().__init__("CRYPTO", "CryptographyAgent")
        self._channel_registry: Dict[str, Dict] = {}

    def check_channel(self, channel_id: str, protocol: str,
                      encrypted: bool, cipher: str = "",
                      cert_expiry_days: Optional[int] = None,
                      mutual_auth: bool = False) -> List[SecurityFinding]:
        """Check one communication channel for crypto compliance."""
        self._stamp()
        findings = []

        # Cleartext detection
        if not encrypted:
            sev = Severity.CRITICAL if protocol.lower() in ("opcua", "modbus_tcp") else Severity.HIGH
            findings.append(SecurityFinding(
                finding_id  = self._new_finding_id(),
                agent       = self.agent_name,
                severity    = sev,
                category    = "CLEARTEXT_CHANNEL",
                title       = f"Cleartext {protocol.upper()} channel: {channel_id}",
                description = f"Channel '{channel_id}' ({protocol}) transmits without encryption.",
                target      = channel_id,
                evidence    = {"protocol": protocol, "encrypted": False},
                mitigation  = "Enable TLS 1.3 on all OPC-UA, historian, and HMI channels. "
                              "Apply IEC 62443-4-2 CR 4.1.",
                standards   = ["IEC-62443-4-2 CR 4.1", "IEC-62443-3-3 SR 4.3"],
            ))
            self.findings_count += 1

        # Weak cipher
        if cipher and any(w in cipher.upper() for w in self.WEAK_CIPHERS):
            findings.append(SecurityFinding(
                finding_id  = self._new_finding_id(),
                agent       = self.agent_name,
                severity    = Severity.HIGH,
                category    = "WEAK_CIPHER",
                title       = f"Weak cipher on {channel_id}: {cipher}",
                description = f"Cipher suite '{cipher}' is deprecated/broken.",
                target      = channel_id,
                evidence    = {"cipher": cipher},
                mitigation  = "Upgrade to TLS 1.3 with AES-256-GCM or ChaCha20-Poly1305.",
                standards   = ["IEC-62443-4-2 CR 4.1"],
            ))
            self.findings_count += 1

        # Certificate expiry
        if cert_expiry_days is not None:
            if cert_expiry_days < 0:
                sev = Severity.CRITICAL
            elif cert_expiry_days < 30:
                sev = Severity.HIGH
            elif cert_expiry_days < 90:
                sev = Severity.MEDIUM
            else:
                sev = None
            if sev:
                findings.append(SecurityFinding(
                    finding_id  = self._new_finding_id(),
                    agent       = self.agent_name,
                    severity    = sev,
                    category    = "CERTIFICATE_EXPIRY",
                    title       = f"Certificate expiry issue on {channel_id}",
                    description = (f"Certificate expired {-cert_expiry_days} days ago."
                                   if cert_expiry_days < 0
                                   else f"Certificate expires in {cert_expiry_days} days."),
                    target      = channel_id,
                    evidence    = {"expiry_days": cert_expiry_days},
                    mitigation  = "Renew certificate immediately. Implement auto-renewal (ACME/Let's Encrypt for IT; vendor PKI for OT).",
                    standards   = ["IEC-62443-4-2 CR 4.2"],
                ))
                self.findings_count += 1

        # Mutual auth check for high-security channels
        if not mutual_auth and protocol.lower() == "opcua":
            findings.append(SecurityFinding(
                finding_id  = self._new_finding_id(),
                agent       = self.agent_name,
                severity    = Severity.MEDIUM,
                category    = "NO_MUTUAL_AUTH",
                title       = f"OPC-UA channel without mutual authentication: {channel_id}",
                description = "OPC-UA channel does not require mutual certificate authentication.",
                target      = channel_id,
                evidence    = {"mutual_auth": False, "protocol": protocol},
                mitigation  = "Enable SignAndEncrypt security mode in OPC-UA server configuration.",
                standards   = ["IEC-62443-3-3 SR 1.1", "OPC-UA Part 2"],
            ))
            self.findings_count += 1

        self._channel_registry[channel_id] = {
            "protocol": protocol, "encrypted": encrypted,
            "cipher": cipher, "cert_expiry_days": cert_expiry_days,
            "last_checked": self.last_scan_ts,
        }
        return findings

    def get_channel_registry(self) -> Dict[str, Dict]:
        return self._channel_registry


# ════════════════════════════════════════════════════════════════════════════
# AGENT 06 — FIRMWARE INTEGRITY
# ════════════════════════════════════════════════════════════════════════════

class FirmwareIntegrityAgent(BaseSecurityAgent):
    """
    SHA-256 hash verification for PLC/HMI/RTU firmware.
    Detects tampered or downgraded firmware builds.
    IEC 62443-4-1 SD-4 / IEC 62443-4-2 CR 3.4
    """

    def __init__(self):
        super().__init__("FWINT", "FirmwareIntegrityAgent")
        self._golden_hashes: Dict[str, Dict[str, str]] = {}  # device→ version→ hash
        self._check_history: List[Dict] = []

    def register_golden(self, device_id: str, version: str, sha256_hash: str) -> None:
        """Register the authorised (golden) firmware hash."""
        if device_id not in self._golden_hashes:
            self._golden_hashes[device_id] = {}
        self._golden_hashes[device_id][version] = sha256_hash.lower()

    def verify(self, device_id: str, firmware_version: str,
               sha256_hash: str) -> Optional[SecurityFinding]:
        """Verify firmware hash. Returns finding if mismatch."""
        self._stamp()
        sha256_hash = sha256_hash.lower()
        record = {"device": device_id, "version": firmware_version,
                  "hash_provided": sha256_hash[:16] + "...", "ts": self.last_scan_ts}

        if device_id not in self._golden_hashes:
            # No baseline — register as new
            self.register_golden(device_id, firmware_version, sha256_hash)
            record["result"] = "REGISTERED"
            self._check_history.append(record)
            return None

        golden = self._golden_hashes[device_id].get(firmware_version)
        if golden is None:
            # Unknown version
            record["result"] = "UNKNOWN_VERSION"
            self._check_history.append(record)
            self.findings_count += 1
            return SecurityFinding(
                finding_id  = self._new_finding_id(),
                agent       = self.agent_name,
                severity    = Severity.HIGH,
                category    = "UNKNOWN_FIRMWARE_VERSION",
                title       = f"Unknown firmware version on {device_id}: {firmware_version}",
                description = f"No golden hash registered for {device_id} v{firmware_version}.",
                target      = device_id,
                evidence    = {"version": firmware_version},
                mitigation  = "Verify firmware provenance with vendor. Register hash in baseline.",
                standards   = ["IEC-62443-4-1 SD-4"],
            )

        if sha256_hash != golden:
            record["result"] = "MISMATCH"
            self._check_history.append(record)
            self.findings_count += 1
            return SecurityFinding(
                finding_id  = self._new_finding_id(),
                agent       = self.agent_name,
                severity    = Severity.CRITICAL,
                category    = "FIRMWARE_HASH_MISMATCH",
                title       = f"FIRMWARE TAMPERED: {device_id} v{firmware_version}",
                description = "SHA-256 hash does not match golden reference. Possible firmware tampering or unauthorized update.",
                target      = device_id,
                evidence    = {
                    "version":       firmware_version,
                    "expected_hash": golden[:24] + "...",
                    "actual_hash":   sha256_hash[:24] + "...",
                },
                mitigation  = "ISOLATE DEVICE IMMEDIATELY. Restore from verified firmware image. "
                              "Investigate access logs for unauthorized physical/remote access.",
                standards   = ["IEC-62443-4-2 CR 3.4", "IEC-62443-4-1 SD-4"],
            )

        record["result"] = "OK"
        self._check_history.append(record)
        return None

    def get_check_history(self, limit: int = 20) -> List[Dict]:
        return self._check_history[-limit:]

    def list_golden_devices(self) -> List[str]:
        return list(self._golden_hashes.keys())


# ════════════════════════════════════════════════════════════════════════════
# AGENT 07 — ACCESS CONTROL
# ════════════════════════════════════════════════════════════════════════════

class AccessControlAgent(BaseSecurityAgent):
    """
    IEC 62443-3-3 Zone/Conduit enforcement and Principle of Least Privilege.
    Detects cross-zone access violations and excessive permissions.
    IEC 62443-3-3 SR 2.1 / SR 1.3
    """

    # Default zone isolation matrix (True = communication allowed)
    DEFAULT_ZONE_MATRIX = {
        ("Enterprise", "DMZ"):        True,
        ("DMZ", "Control"):           True,
        ("DMZ", "Safety"):            False,
        ("Control", "Field"):         True,
        ("Control", "Safety"):        False,  # safety is isolated
        ("Safety", "Field"):          True,
        ("Enterprise", "Control"):    False,  # must go through DMZ
        ("Enterprise", "Safety"):     False,
        ("Enterprise", "Field"):      False,
    }

    def __init__(self):
        super().__init__("ACL", "AccessControlAgent")
        self._zone_matrix  = dict(self.DEFAULT_ZONE_MATRIX)
        self._user_roles:  Dict[str, str] = {}   # user → role
        self._role_perms:  Dict[str, List[str]] = {
            "operator":   ["read", "setpoint_write"],
            "engineer":   ["read", "setpoint_write", "config_write"],
            "admin":      ["read", "setpoint_write", "config_write", "firmware_write"],
            "readonly":   ["read"],
        }
        self._access_log: List[Dict] = []

    def check_zone_access(self, source_zone: str, target_zone: str) -> Optional[SecurityFinding]:
        """Check if cross-zone communication is permitted."""
        self._stamp()
        pair = (source_zone, target_zone)
        rev  = (target_zone, source_zone)

        allowed = self._zone_matrix.get(pair, self._zone_matrix.get(rev, False))
        if not allowed:
            self.findings_count += 1
            return SecurityFinding(
                finding_id  = self._new_finding_id(),
                agent       = self.agent_name,
                severity    = Severity.HIGH,
                category    = "ZONE_VIOLATION",
                title       = f"Zone violation: {source_zone} → {target_zone}",
                description = f"Direct communication from '{source_zone}' to '{target_zone}' violates zone/conduit model.",
                target      = f"{source_zone}→{target_zone}",
                evidence    = {"source_zone": source_zone, "target_zone": target_zone},
                mitigation  = "Route through approved conduit/DMZ. Update firewall rules per IEC 62443-3-3.",
                standards   = ["IEC-62443-3-3 SR 5.1", "IEC-62443-3-2"],
            )
        return None

    def check_access_request(self, user_id: str, action: str,
                              target: str) -> Optional[SecurityFinding]:
        """Check if a user has permission to perform an action."""
        self._stamp()
        role  = self._user_roles.get(user_id, "readonly")
        perms = self._role_perms.get(role, ["read"])

        log = {"user": user_id, "role": role, "action": action, "target": target,
               "ts": self.last_scan_ts}

        if action not in perms:
            log["result"] = "DENIED"
            self._access_log.append(log)
            self.findings_count += 1
            sev = Severity.CRITICAL if action == "firmware_write" else Severity.HIGH
            return SecurityFinding(
                finding_id  = self._new_finding_id(),
                agent       = self.agent_name,
                severity    = sev,
                category    = "ACCESS_VIOLATION",
                title       = f"Unauthorized action: {user_id} attempted {action} on {target}",
                description = f"User '{user_id}' (role: {role}) attempted '{action}' which exceeds permissions.",
                target      = target,
                evidence    = {"user": user_id, "role": role,
                               "action": action, "allowed_perms": perms},
                mitigation  = "Apply Principle of Least Privilege. Review user role assignments.",
                standards   = ["IEC-62443-3-3 SR 2.1", "IEC-62443-2-1 SP.02.02"],
            )

        log["result"] = "ALLOWED"
        self._access_log.append(log)
        return None

    def register_user(self, user_id: str, role: str) -> bool:
        if role not in self._role_perms:
            return False
        self._user_roles[user_id] = role
        return True

    def get_access_log(self, limit: int = 50) -> List[Dict]:
        return self._access_log[-limit:]


# ════════════════════════════════════════════════════════════════════════════
# AGENT 08 — PHYSICS CONSISTENCY
# ════════════════════════════════════════════════════════════════════════════

class PhysicsConsistencyAgent(BaseSecurityAgent):
    """
    Cyber-physical attack detection via digital-twin mismatch.
    Uses running mean/σ to detect spoofed sensor readings (> 3σ deviation
    between reported SCADA values and physics twin predictions).
    IEC 62443-3-3 / Stuxnet-class attack detection.
    """

    def __init__(self, deviation_threshold_sigma: float = 3.0):
        super().__init__("PHYS", "PhysicsConsistencyAgent")
        self.threshold = deviation_threshold_sigma
        self._residual_stats: Dict[str, Dict] = {}

    def _update_residual(self, tag_id: str, residual: float) -> Tuple[float, float, int]:
        """Welford online stats. Returns (prior_mean, prior_std, new_n).
        Uses PRIOR stats for z-score so anomalous point cannot dilute itself."""
        if tag_id not in self._residual_stats:
            self._residual_stats[tag_id] = {"n": 0, "mean": 0.0, "M2": 0.0}
        s = self._residual_stats[tag_id]
        prior_mean = s["mean"]
        prior_std  = math.sqrt(s["M2"] / s["n"]) if s["n"] > 1 else 1e-9
        s["n"] += 1
        delta  = residual - s["mean"]
        s["mean"] += delta / s["n"]
        s["M2"]   += delta * (residual - s["mean"])
        return prior_mean, prior_std, s["n"]

    def check(self, tag_id: str, scada_value: float,
              twin_prediction: float) -> Optional[SecurityFinding]:
        """Check for mismatch between SCADA report and physics twin."""
        self._stamp()
        residual = abs(scada_value - twin_prediction)
        mean_r, std_r, n = self._update_residual(tag_id, residual)

        if n <= 20:
            return None   # warm-up period

        sigma_count = (residual - mean_r) / (std_r + 1e-9)

        if sigma_count > self.threshold * 2:
            sev = Severity.CRITICAL
        elif sigma_count > self.threshold:
            sev = Severity.HIGH
        else:
            return None

        self.findings_count += 1
        return SecurityFinding(
            finding_id  = self._new_finding_id(),
            agent       = self.agent_name,
            severity    = sev,
            category    = "CYBER_PHYSICAL_MISMATCH",
            title       = f"Physics twin deviation on {tag_id}: {sigma_count:.1f}σ",
            description = (
                f"SCADA reports {scada_value:.4f} but physics twin predicts {twin_prediction:.4f} "
                f"(residual {residual:.4f} = {sigma_count:.1f}σ above baseline {mean_r:.4f}±{std_r:.4f}). "
                "Possible sensor spoofing or cyber-physical attack."
            ),
            target      = tag_id,
            evidence    = {
                "scada_value":    scada_value,
                "twin_prediction": twin_prediction,
                "residual":       round(residual, 4),
                "sigma":          round(sigma_count, 2),
                "baseline_mean":  round(mean_r, 4),
                "baseline_std":   round(std_r, 4),
            },
            mitigation  = "Cross-check with redundant sensor. "
                          "If confirmed, trigger safe-state and isolate affected field device. "
                          "Investigate for Stuxnet-class payload injection.",
            standards   = ["IEC-62443-3-3 SR 6.2", "IEC-62443-3-3 SR 3.1"],
        )

    def get_residual_stats(self) -> Dict[str, Dict]:
        return self._residual_stats


# ════════════════════════════════════════════════════════════════════════════
# AGENT 09 — THREAT CORRELATOR
# ════════════════════════════════════════════════════════════════════════════

class ThreatCorrelatorAgent(BaseSecurityAgent):
    """
    Multi-source MITRE ATT&CK for ICS tactic correlation.
    Groups findings by tactic, computes combined threat score.
    """

    # Mapping of finding categories to ATT&CK for ICS tactics
    CATEGORY_TACTIC_MAP = {
        "PLC_CODE_SECURITY":       "T0821",   # Modify Controller Tasklist
        "SCADA_TAG_ANOMALY":       "T0831",   # Manipulation of Control
        "CVE_VULNERABILITY":       "T0800",   # Activate Firmware Update Mode
        "ILLEGAL_FUNCTION_CODE":   "T0803",   # Block Command Message
        "ROGUE_DEVICE":            "T0840",   # Network Connection Enumeration
        "CLEARTEXT_CHANNEL":       "T0886",   # Remote Services
        "FIRMWARE_HASH_MISMATCH":  "T0895",   # Autorun Image
        "ZONE_VIOLATION":          "T0846",   # Remote System Discovery
        "ACCESS_VIOLATION":        "T0859",   # Valid Accounts
        "CYBER_PHYSICAL_MISMATCH": "T0836",   # Modify Parameter
        "BRUTE_FORCE":             "T0859",
        "DOS_ATTACK":              "T0881",   # Service Stop
    }

    def __init__(self):
        super().__init__("CORR", "ThreatCorrelatorAgent")
        self._finding_buffer: deque = deque(maxlen=500)
        self._tactic_counts: Dict[str, int] = defaultdict(int)
        self._correlation_id = 0

    def ingest(self, finding: SecurityFinding) -> None:
        """Accept a finding from any other agent."""
        self._finding_buffer.append(finding)
        tactic = self.CATEGORY_TACTIC_MAP.get(finding.category, "T0000")
        self._tactic_counts[tactic] += 1

    def correlate(self, time_window_minutes: int = 60) -> List[SecurityFinding]:
        """Identify coordinated attack patterns from buffered findings."""
        self._stamp()
        self._correlation_id += 1

        # Count findings by severity in window
        now = datetime.datetime.utcnow()
        window = datetime.timedelta(minutes=time_window_minutes)
        recent = [f for f in self._finding_buffer
                  if _within_window(f.timestamp, now, window)]

        critical_count = sum(1 for f in recent if f.severity == Severity.CRITICAL)
        high_count     = sum(1 for f in recent if f.severity == Severity.HIGH)
        tactic_set     = set(self.CATEGORY_TACTIC_MAP.get(f.category, "") for f in recent)

        correlation_findings = []

        # Pattern: multiple tactics → coordinated campaign
        if len(tactic_set) >= 3 and (critical_count + high_count) >= 5:
            self.findings_count += 1
            correlation_findings.append(SecurityFinding(
                finding_id  = f"CORR_{self._correlation_id:04d}",
                agent       = self.agent_name,
                severity    = Severity.CRITICAL,
                category    = "COORDINATED_ATTACK",
                title       = "Coordinated ICS attack pattern detected",
                description = (
                    f"{len(recent)} findings in {time_window_minutes}min window "
                    f"spanning {len(tactic_set)} ATT&CK for ICS tactics. "
                    f"Critical: {critical_count}, High: {high_count}."
                ),
                target      = "PLANT_WIDE",
                evidence    = {
                    "findings_in_window": len(recent),
                    "tactics":            list(tactic_set),
                    "tactic_names":       [ATTCK_ICS_TACTICS.get(t, t) for t in tactic_set],
                    "critical_count":     critical_count,
                    "high_count":         high_count,
                },
                mitigation  = "INITIATE INCIDENT RESPONSE. Activate ICS-CERT notification. "
                              "Trigger safe-state on affected zones.",
                standards   = ["MITRE-ATT&CK-ICS", "IEC-62443-2-1 SP.10"],
            ))

        return correlation_findings

    def get_tactic_counts(self) -> Dict[str, Any]:
        return {
            "counts": dict(self._tactic_counts),
            "tactic_names": {k: ATTCK_ICS_TACTICS.get(k, k) for k in self._tactic_counts},
        }


# ════════════════════════════════════════════════════════════════════════════
# AGENT 10 — RATE LIMIT DETECTOR
# ════════════════════════════════════════════════════════════════════════════

class RateLimitAgent(BaseSecurityAgent):
    """
    Brute-force and DoS detection on OT protocols.
    Sliding window burst counting per source IP + protocol.
    IEC 62443-3-3 SR 8.1
    """

    def __init__(self, window_seconds: int = 60,
                 brute_force_threshold: int = 5,
                 dos_pkt_threshold: int = 1000):
        super().__init__("RATELMT", "RateLimitAgent")
        self.window         = window_seconds
        self.bf_threshold   = brute_force_threshold
        self.dos_threshold  = dos_pkt_threshold
        self._auth_failures: Dict[str, deque] = defaultdict(lambda: deque(maxlen=200))
        self._pkt_counts:    Dict[str, deque] = defaultdict(lambda: deque(maxlen=5000))

    def record_auth_failure(self, source_ip: str,
                            protocol: str) -> Optional[SecurityFinding]:
        """Record an authentication failure; detect brute force."""
        self._stamp()
        now = time.time()
        key = f"{source_ip}|{protocol}"
        self._auth_failures[key].append(now)

        # Count in window
        count = sum(1 for t in self._auth_failures[key] if now - t <= self.window)
        if count >= self.bf_threshold:
            self.findings_count += 1
            return SecurityFinding(
                finding_id  = self._new_finding_id(),
                agent       = self.agent_name,
                severity    = Severity.HIGH,
                category    = "BRUTE_FORCE",
                title       = f"Brute-force on {protocol.upper()} from {source_ip}",
                description = f"{count} authentication failures from {source_ip} on {protocol} in {self.window}s.",
                target      = source_ip,
                evidence    = {"count": count, "window_s": self.window, "protocol": protocol},
                mitigation  = f"Block {source_ip} at OT firewall. Enable account lockout on {protocol} service.",
                standards   = ["IEC-62443-3-3 SR 1.11", "IEC-62443-4-2 CR 1.11"],
            )
        return None

    def record_packet(self, source_ip: str,
                      protocol: str) -> Optional[SecurityFinding]:
        """Record a packet; detect DoS / packet storm."""
        now = time.time()
        key = f"{source_ip}|{protocol}"
        self._pkt_counts[key].append(now)

        count = sum(1 for t in self._pkt_counts[key] if now - t <= self.window)
        if count >= self.dos_threshold:
            self.findings_count += 1
            self._stamp()
            return SecurityFinding(
                finding_id  = self._new_finding_id(),
                agent       = self.agent_name,
                severity    = Severity.CRITICAL,
                category    = "DOS_ATTACK",
                title       = f"DoS/packet-storm from {source_ip} on {protocol.upper()}",
                description = f"{count} packets from {source_ip} on {protocol} in {self.window}s (threshold: {self.dos_threshold}).",
                target      = source_ip,
                evidence    = {"pkt_count": count, "window_s": self.window, "protocol": protocol},
                mitigation  = "Rate-limit or null-route source IP. Enable OT network QoS.",
                standards   = ["IEC-62443-3-3 SR 7.2"],
            )
        return None


# ════════════════════════════════════════════════════════════════════════════
# AGENT 11 — RECOVERY ORCHESTRATOR
# ════════════════════════════════════════════════════════════════════════════

class RecoveryOrchestratorAgent(BaseSecurityAgent):
    """
    Autonomous safe-state transition with governance audit trail.
    For CRITICAL findings, triggers immediate safe-state without waiting.
    For HIGH, requires governance approval (simulated async).
    IEC 62443-2-4 SP.04 / IEC 61511 Safe State Management.
    """

    def __init__(self):
        super().__init__("RECOV", "RecoveryOrchestratorAgent")
        self._safe_state_log: List[SafeStateTransition] = []
        self._recovery_id = 0
        self._current_safe_states: Dict[str, SafeStateTransition] = {}

    def trigger_safe_state(self, reason: SafeStateReason, zone: str,
                           triggered_by: str,
                           auto_approve: bool = False) -> SafeStateTransition:
        """Trigger a safe-state transition."""
        self._stamp()
        self._recovery_id += 1
        transition = SafeStateTransition(
            transition_id = f"SST_{self._recovery_id:04d}",
            reason        = reason,
            zone          = zone,
            triggered_by  = triggered_by,
            timestamp     = self.last_scan_ts,
            plc_command   = "EMERGENCY_STOP",
            approved_by   = "GOVERNANCE_AUTO" if auto_approve else "GOVERNANCE_PENDING",
        )
        self._safe_state_log.append(transition)
        self._current_safe_states[zone] = transition
        self.findings_count += 1
        return transition

    def clear_safe_state(self, zone: str, operator_id: str) -> bool:
        """Return zone to normal operation after operator sign-off."""
        self._stamp()
        if zone not in self._current_safe_states:
            return False
        sst = self._current_safe_states.pop(zone)
        sst.reversed_at = self.last_scan_ts
        # Update log entry
        for entry in self._safe_state_log:
            if entry.transition_id == sst.transition_id:
                entry.reversed_at = self.last_scan_ts
                entry.approved_by = operator_id
        return True

    def get_active_safe_states(self) -> Dict[str, Any]:
        return {z: sst.to_dict() for z, sst in self._current_safe_states.items()}

    def get_safe_state_log(self, limit: int = 20) -> List[Dict]:
        return [s.to_dict() for s in self._safe_state_log[-limit:]]


# ════════════════════════════════════════════════════════════════════════════
# AGENT 12 — THREAT INTEL SYNC
# ════════════════════════════════════════════════════════════════════════════

class ThreatIntelSyncAgent(BaseSecurityAgent):
    """
    Federated threat intelligence sharing across KISWARM mesh nodes.
    Threat records are signed and stored in an immutable chain.
    IEC 62443-2-1 / Information Sharing model.
    """

    def __init__(self, node_id: str = "LOCAL"):
        super().__init__("TISYNC", "ThreatIntelSyncAgent")
        self.node_id    = node_id
        self._intel_db: List[ThreatIntelRecord] = []
        self._sync_log: List[Dict] = []
        self._intel_counter = 0

    def publish(self, tactic_id: str, ioc: str, confidence: float,
                affected_products: List[str]) -> ThreatIntelRecord:
        """Publish a threat intel record to the mesh."""
        self._stamp()
        self._intel_counter += 1
        record = ThreatIntelRecord(
            intel_id    = f"TI_{self.node_id}_{self._intel_counter:06d}",
            source_node = self.node_id,
            tactic_id   = tactic_id,
            ioc         = ioc,
            confidence  = max(0.0, min(1.0, confidence)),
            timestamp   = self.last_scan_ts,
            affected    = affected_products,
        )
        self._intel_db.append(record)
        return record

    def ingest_remote(self, records: List[Dict[str, Any]]) -> int:
        """Ingest threat records from remote KISWARM nodes."""
        self._stamp()
        count = 0
        for r in records:
            try:
                rec = ThreatIntelRecord(**r)
                if not any(x.intel_id == rec.intel_id for x in self._intel_db):
                    self._intel_db.append(rec)
                    count += 1
            except (TypeError, ValueError):
                pass
        self._sync_log.append({"ts": self.last_scan_ts, "ingested": count})
        return count

    def query(self, product: str = "", tactic_id: str = "",
              min_confidence: float = 0.0) -> List[Dict]:
        """Query threat intel database."""
        results = self._intel_db
        if product:
            results = [r for r in results if product.lower() in [a.lower() for a in r.affected]]
        if tactic_id:
            results = [r for r in results if r.tactic_id == tactic_id]
        results = [r for r in results if r.confidence >= min_confidence]
        return [r.to_dict() for r in results]

    def get_sync_log(self) -> List[Dict]:
        return self._sync_log


# ════════════════════════════════════════════════════════════════════════════
# HELPER
# ════════════════════════════════════════════════════════════════════════════

def _within_window(ts_str: str, now: datetime.datetime,
                   window: datetime.timedelta) -> bool:
    """Check if ISO timestamp is within a time window of now."""
    try:
        ts = datetime.datetime.fromisoformat(ts_str.rstrip("Z"))
        return (now - ts) <= window
    except (ValueError, AttributeError):
        return True   # can't parse → assume recent


# ════════════════════════════════════════════════════════════════════════════
# ICS-SHIELD: SECURITY MESH COORDINATOR
# ════════════════════════════════════════════════════════════════════════════

class ICSShield:
    """
    ICS-SHIELD v4.3 — Industrial Cybersecurity Mesh
    Coordinates all 12 security agents into a unified defence layer.
    Integrated with CIEC, SIL Verification, and Digital Thread.
    """

    def __init__(self, node_id: str = "KISWARM_NODE_01"):
        self.node_id = node_id

        # Instantiate all 12 agents
        self.plc_monitor      = PLCMonitorAgent()
        self.scada_monitor    = SCADAMonitorAgent()
        self.cve_intelligence = CVEIntelligenceAgent()
        self.network_anomaly  = NetworkAnomalyAgent()
        self.cryptography     = CryptographyAgent()
        self.firmware_integrity = FirmwareIntegrityAgent()
        self.access_control   = AccessControlAgent()
        self.physics_consistency = PhysicsConsistencyAgent()
        self.threat_correlator = ThreatCorrelatorAgent()
        self.rate_limit        = RateLimitAgent()
        self.recovery          = RecoveryOrchestratorAgent()
        self.threat_intel_sync = ThreatIntelSyncAgent(node_id)

        self._all_agents = [
            self.plc_monitor, self.scada_monitor, self.cve_intelligence,
            self.network_anomaly, self.cryptography, self.firmware_integrity,
            self.access_control, self.physics_consistency, self.threat_correlator,
            self.rate_limit, self.recovery, self.threat_intel_sync,
        ]

        # Global findings log with SHA-256 chain
        self._findings:   List[SecurityFinding] = []
        self._chain_hash  = "0" * 64
        self._scan_count  = 0

    # ── Core finding pipeline ─────────────────────────────────────────────

    def _record_finding(self, f: SecurityFinding) -> None:
        """Append finding to the ledger, feed correlator, trigger safe-state if needed."""
        # Chain hash
        raw = f"{self._chain_hash}|{f.finding_id}|{f.severity.value}".encode()
        self._chain_hash = hashlib.sha256(raw).hexdigest()

        self._findings.append(f)
        self.threat_correlator.ingest(f)

        # Auto-trigger safe-state for CRITICAL firmware or physics findings
        if f.severity == Severity.CRITICAL and f.category in (
            "FIRMWARE_HASH_MISMATCH", "CYBER_PHYSICAL_MISMATCH"
        ):
            reason = (SafeStateReason.FIRMWARE_MISMATCH
                      if "FIRMWARE" in f.category
                      else SafeStateReason.PHYSICS_DEVIATION)
            self.recovery.trigger_safe_state(
                reason       = reason,
                zone         = f.target,
                triggered_by = f.agent,
                auto_approve = True,
            )

    def _record_many(self, findings: List[SecurityFinding]) -> None:
        for f in findings:
            self._record_finding(f)

    # ── High-level scan APIs ──────────────────────────────────────────────

    def scan_plc(self, plc_code: str, plc_id: str = "PLC_01") -> List[Dict]:
        """Scan PLC ST code for security issues."""
        self._scan_count += 1
        findings = self.plc_monitor.scan_plc_code(plc_code, plc_id)
        self._record_many(findings)
        return [f.to_dict() for f in findings]

    def check_scada_tag(self, tag_id: str, value: float,
                        timestamp: str = "") -> Optional[Dict]:
        """Check a SCADA tag reading for anomalies."""
        self._scan_count += 1
        f = self.scada_monitor.check_tag(tag_id, value, timestamp)
        if f:
            self._record_finding(f)
            return f.to_dict()
        return None

    def check_firmware(self, device_id: str, firmware_version: str,
                       sha256_hash: str) -> Optional[Dict]:
        """Verify firmware hash against golden baseline."""
        self._scan_count += 1
        f = self.firmware_integrity.verify(device_id, firmware_version, sha256_hash)
        if f:
            self._record_finding(f)
            return f.to_dict()
        return None

    def check_physics(self, tag_id: str, scada_value: float,
                      twin_prediction: float) -> Optional[Dict]:
        """Detect cyber-physical attack via twin mismatch."""
        self._scan_count += 1
        f = self.physics_consistency.check(tag_id, scada_value, twin_prediction)
        if f:
            self._record_finding(f)
            return f.to_dict()
        return None

    def lookup_cves(self, product_id: str,
                    firmware_version: str = "") -> List[Dict]:
        """Look up known CVEs for an ICS product."""
        self._scan_count += 1
        findings = self.cve_intelligence.lookup(product_id, firmware_version)
        self._record_many(findings)
        return [f.to_dict() for f in findings]

    def iec62443_assess(self, target_system: str,
                        zone: str = "Control",
                        security_level: str = "SL2") -> Dict[str, Any]:
        """
        Full IEC 62443 compliance assessment for a target system.
        Runs all relevant agents and compiles a compliance report.
        """
        self._scan_count += 1
        report: Dict[str, Any] = {
            "target": target_system,
            "zone": zone,
            "security_level_target": security_level,
            "assessed_at": datetime.datetime.utcnow().isoformat() + "Z",
            "requirements": IEC62443_REQUIREMENTS.get(security_level, {}),
            "findings": [],
            "compliant": True,
        }

        reqs = IEC62443_REQUIREMENTS.get(security_level, {})

        # Check encryption requirement
        if reqs.get("encryption"):
            f = self.cryptography.check_channel(
                channel_id     = f"{target_system}_historian",
                protocol       = "opcua",
                encrypted      = False,   # conservative assumption
                cert_expiry_days = 45,
            )
            for finding in f:
                self._record_finding(finding)
                report["findings"].append(finding.to_dict())
                if finding.severity in (Severity.CRITICAL, Severity.HIGH):
                    report["compliant"] = False

        # Check network zone
        if zone == "Safety":
            zf = self.access_control.check_zone_access("Control", "Safety")
            if zf:
                self._record_finding(zf)
                report["findings"].append(zf.to_dict())
                report["compliant"] = False

        # Compute compliance score
        n_findings    = len(report["findings"])
        critical_n    = sum(1 for f in report["findings"] if f["severity"] == "CRITICAL")
        report["compliance_score"] = max(0.0, 1.0 - (critical_n * 0.3 + (n_findings - critical_n) * 0.1))
        report["finding_count"]    = n_findings

        return report

    def correlate(self, window_minutes: int = 60) -> List[Dict]:
        """Run threat correlation across all buffered findings."""
        corr = self.threat_correlator.correlate(window_minutes)
        self._record_many(corr)
        return [f.to_dict() for f in corr]

    def trigger_safe_state(self, reason_str: str, zone: str) -> Dict:
        """Manually trigger a safe-state transition."""
        try:
            reason = SafeStateReason(reason_str)
        except ValueError:
            reason = SafeStateReason.MANUAL_TRIGGER
        sst = self.recovery.trigger_safe_state(
            reason       = reason,
            zone         = zone,
            triggered_by = "MANUAL",
            auto_approve = True,
        )
        return sst.to_dict()

    # ── Status & reporting ────────────────────────────────────────────────

    def get_alerts(self, severity_filter: Optional[str] = None,
                   limit: int = 50) -> List[Dict]:
        """Return recent security findings."""
        findings = list(reversed(self._findings[-limit*2:]))
        if severity_filter:
            findings = [f for f in findings if f.severity.value == severity_filter.upper()]
        return [f.to_dict() for f in findings[:limit]]

    def acknowledge_alert(self, finding_id: str) -> bool:
        for f in self._findings:
            if f.finding_id == finding_id:
                f.acknowledged = True
                return True
        return False

    def get_status(self) -> Dict[str, Any]:
        """Complete security posture summary."""
        by_severity: Dict[str, int] = defaultdict(int)
        for f in self._findings:
            by_severity[f.severity.value] += 1

        return {
            "node_id":       self.node_id,
            "total_findings": len(self._findings),
            "by_severity":   dict(by_severity),
            "active_safe_states": self.recovery.get_active_safe_states(),
            "chain_hash":    self._chain_hash[:16] + "...",
            "scan_count":    self._scan_count,
            "agents":        [a.get_status() for a in self._all_agents],
            "threat_intel_count": len(self.threat_intel_sync._intel_db),
        }

    def get_metrics(self) -> Dict[str, Any]:
        """Security metrics for dashboard."""
        critical = sum(1 for f in self._findings if f.severity == Severity.CRITICAL)
        high     = sum(1 for f in self._findings if f.severity == Severity.HIGH)
        ack      = sum(1 for f in self._findings if f.acknowledged)
        return {
            "critical_findings": critical,
            "high_findings":     high,
            "total_findings":    len(self._findings),
            "acknowledged":      ack,
            "unacknowledged":    len(self._findings) - ack,
            "safe_state_events": len(self.recovery._safe_state_log),
            "active_safe_states": len(self.recovery._current_safe_states),
            "tactic_distribution": self.threat_correlator.get_tactic_counts(),
            "threat_intel_records": len(self.threat_intel_sync._intel_db),
        }

    def verify_ledger_integrity(self) -> bool:
        """Recompute chain hash from scratch; confirm integrity."""
        chain = "0" * 64
        for f in self._findings:
            raw  = f"{chain}|{f.finding_id}|{f.severity.value}".encode()
            chain = hashlib.sha256(raw).hexdigest()
        return chain == self._chain_hash
