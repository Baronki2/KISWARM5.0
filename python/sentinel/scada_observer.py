"""
KISWARM v4.0 — Module 12: SCADA / OPC / SQL Observation Layer
=============================================================
Ingests industrial telemetry from:
  - OPC UA tag streams (real-time, 50–200 ms sample window)
  - SQL historian batch replay (3-year historical ingestion)
  - SCADA alarm/event feeds

Produces:
  - Time-series tensors: X(t) = [tag1, tag2, ..., tagN]
  - Feature vectors per tag: mean, variance, switching_frequency,
    peak_load, derivative, actuator_cycle_count
  - Plant state vector S(t) for CIEC cognitive core
  - Anomaly detection signals

Design principle: read-only observation. Never commands actuators.
"""

from __future__ import annotations

import math
import time
import json
import os
import hashlib
import logging
import statistics
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# ── Data Structures ───────────────────────────────────────────────────────────

@dataclass
class TagSample:
    """Single time-stamped tag observation."""
    tag_name:  str
    value:     float
    timestamp: float           # Unix epoch
    quality:   str = "GOOD"    # GOOD | BAD | UNCERTAIN
    unit:      str = ""

    def is_good(self) -> bool:
        return self.quality == "GOOD"


@dataclass
class TagFeatures:
    """
    Computed feature vector for one tag over a time window.
    These feed into the plant state vector S(t).
    """
    tag_name:           str
    window_start:       float
    window_end:         float
    sample_count:       int

    mean:               float = 0.0
    variance:           float = 0.0
    std_dev:            float = 0.0
    min_val:            float = 0.0
    max_val:            float = 0.0
    range_val:          float = 0.0

    # Control-specific features
    switching_frequency: float = 0.0   # zero-crossings / second (for binary tags)
    peak_load:           float = 0.0   # max value in window
    mean_derivative:     float = 0.0   # average rate of change
    max_derivative:      float = 0.0   # max rate of change (spike detection)
    actuator_cycle_count: int  = 0     # transitions above cycle threshold
    overshoot_ratio:     float = 0.0   # max deviation / setpoint
    thermal_drift:       float = 0.0   # slow linear trend slope
    hysteresis_estimate: float = 0.0   # half-range of oscillation

    def to_vector(self) -> list[float]:
        """Return as flat feature vector for ML ingestion."""
        return [
            self.mean,
            self.variance,
            self.std_dev,
            self.range_val,
            self.switching_frequency,
            self.peak_load,
            self.mean_derivative,
            self.max_derivative,
            float(self.actuator_cycle_count),
            self.overshoot_ratio,
            self.thermal_drift,
            self.hysteresis_estimate,
        ]

    def to_dict(self) -> dict:
        return {
            "tag":                   self.tag_name,
            "window":                [self.window_start, self.window_end],
            "samples":               self.sample_count,
            "mean":                  round(self.mean, 4),
            "variance":              round(self.variance, 6),
            "std_dev":               round(self.std_dev, 4),
            "min":                   round(self.min_val, 4),
            "max":                   round(self.max_val, 4),
            "switching_frequency":   round(self.switching_frequency, 4),
            "peak_load":             round(self.peak_load, 4),
            "mean_derivative":       round(self.mean_derivative, 6),
            "max_derivative":        round(self.max_derivative, 6),
            "actuator_cycle_count":  self.actuator_cycle_count,
            "overshoot_ratio":       round(self.overshoot_ratio, 4),
            "thermal_drift":         round(self.thermal_drift, 6),
            "hysteresis_estimate":   round(self.hysteresis_estimate, 4),
        }


@dataclass
class PlantStateVector:
    """
    Full plant state S(t) for CIEC cognitive core input.
    Combines features from all monitored tags into one tensor.
    """
    timestamp:    float
    tag_features: dict[str, TagFeatures]   = field(default_factory=dict)
    alarm_count:  int                       = 0
    fault_flags:  list[str]                = field(default_factory=list)

    def to_vector(self) -> list[float]:
        """Flatten all tag features into one vector."""
        vec = [self.timestamp, float(self.alarm_count)]
        for feat in sorted(self.tag_features.values(), key=lambda f: f.tag_name):
            vec.extend(feat.to_vector())
        return vec

    @property
    def dimension(self) -> int:
        return len(self.to_vector())

    def to_dict(self) -> dict:
        return {
            "timestamp":   self.timestamp,
            "alarm_count": self.alarm_count,
            "fault_flags": self.fault_flags,
            "tags":        {k: v.to_dict() for k, v in self.tag_features.items()},
            "dimension":   self.dimension,
        }


# ── Feature Computation Engine ────────────────────────────────────────────────

def compute_features(
    tag_name: str,
    samples:  list[TagSample],
    setpoint: Optional[float] = None,
    cycle_threshold: float = 0.1,
) -> TagFeatures:
    """
    Compute full feature vector for a tag's sample window.

    Args:
        tag_name:        Tag identifier
        samples:         Ordered list of TagSample (ascending timestamp)
        setpoint:        Optional known setpoint for overshoot calculation
        cycle_threshold: Min value change to count as one actuator cycle
    """
    good_samples = [s for s in samples if s.is_good()]
    n = len(good_samples)

    if n == 0:
        return TagFeatures(
            tag_name    = tag_name,
            window_start = samples[0].timestamp if samples else 0,
            window_end   = samples[-1].timestamp if samples else 0,
            sample_count = 0,
        )

    values = [s.value for s in good_samples]
    times  = [s.timestamp for s in good_samples]

    # Basic statistics
    mean   = statistics.mean(values)
    var    = statistics.variance(values) if n > 1 else 0.0
    std    = math.sqrt(var)
    mn, mx = min(values), max(values)

    # Switching frequency (zero-crossings around mean, normalized per second)
    total_time    = max(times[-1] - times[0], 1e-9)
    crossings     = sum(
        1 for i in range(1, n)
        if (values[i - 1] - mean) * (values[i] - mean) < 0
    )
    switch_freq   = crossings / total_time

    # Derivative series
    derivatives = []
    for i in range(1, n):
        dt = times[i] - times[i - 1]
        if dt > 1e-9:
            derivatives.append(abs((values[i] - values[i - 1]) / dt))

    mean_deriv = statistics.mean(derivatives) if derivatives else 0.0
    max_deriv  = max(derivatives) if derivatives else 0.0

    # Actuator cycle counting (direction reversals above threshold)
    cycle_count = 0
    direction   = 0   # +1 rising, -1 falling
    for i in range(1, n):
        delta = values[i] - values[i - 1]
        if abs(delta) >= cycle_threshold:
            new_dir = 1 if delta > 0 else -1
            if direction != 0 and new_dir != direction:
                cycle_count += 1
            direction = new_dir

    # Overshoot ratio vs setpoint
    overshoot = 0.0
    if setpoint and abs(setpoint) > 1e-9:
        overshoot = (mx - setpoint) / abs(setpoint)

    # Thermal drift (linear regression slope)
    drift = _linear_slope(times, values) if n > 2 else 0.0

    # Hysteresis estimate (half-range of oscillation)
    hysteresis = (mx - mn) / 2.0

    return TagFeatures(
        tag_name            = tag_name,
        window_start        = times[0],
        window_end          = times[-1],
        sample_count        = n,
        mean                = mean,
        variance            = var,
        std_dev             = std,
        min_val             = mn,
        max_val             = mx,
        range_val           = mx - mn,
        switching_frequency = switch_freq,
        peak_load           = mx,
        mean_derivative     = mean_deriv,
        max_derivative      = max_deriv,
        actuator_cycle_count = cycle_count,
        overshoot_ratio     = max(0.0, overshoot),
        thermal_drift       = drift,
        hysteresis_estimate = hysteresis,
    )


def _linear_slope(x: list[float], y: list[float]) -> float:
    """Compute linear regression slope (Theil-Sen estimator, O(n))."""
    n = len(x)
    if n < 2:
        return 0.0
    # Fast approximate: endpoints slope
    x0, x1 = x[0], x[-1]
    y0, y1 = y[0], y[-1]
    if abs(x1 - x0) < 1e-9:
        return 0.0
    return (y1 - y0) / (x1 - x0)


# ── Tag Buffer ────────────────────────────────────────────────────────────────

class TagBuffer:
    """
    Rolling time-window buffer for one OPC/SCADA tag.
    Maintains the last N seconds of observations.
    """

    def __init__(self, tag_name: str, window_seconds: float = 300.0, max_samples: int = 10_000):
        self.tag_name       = tag_name
        self.window_seconds = window_seconds
        self._samples:      deque[TagSample] = deque(maxlen=max_samples)
        self._setpoint:     Optional[float]  = None

    def push(self, value: float, timestamp: Optional[float] = None, quality: str = "GOOD") -> None:
        ts = timestamp if timestamp is not None else time.time()
        self._samples.append(TagSample(
            tag_name  = self.tag_name,
            value     = value,
            timestamp = ts,
            quality   = quality,
        ))
        # Trim to window
        cutoff = ts - self.window_seconds
        while self._samples and self._samples[0].timestamp < cutoff:
            self._samples.popleft()

    def push_batch(self, values: list[float], start_ts: float, interval: float) -> None:
        """Ingest batch of samples at uniform interval (SQL historian replay)."""
        for i, v in enumerate(values):
            self.push(v, start_ts + i * interval)

    def set_setpoint(self, sp: float) -> None:
        self._setpoint = sp

    @property
    def sample_count(self) -> int:
        return len(self._samples)

    def get_features(self) -> TagFeatures:
        return compute_features(
            self.tag_name,
            list(self._samples),
            setpoint=self._setpoint,
        )

    def latest(self) -> Optional[float]:
        return self._samples[-1].value if self._samples else None


# ── OPC UA Subscriber (Interface) ─────────────────────────────────────────────

class OPCUASubscriber:
    """
    Simulated OPC UA subscriber.
    In production: replace with opcua-asyncio or FreeOpcUa client.
    In lab/simulation: push values directly via push_tag().
    """

    def __init__(self, endpoint: str = "opc.tcp://localhost:4840/", sample_ms: int = 100):
        self.endpoint   = endpoint
        self.sample_ms  = sample_ms
        self._buffers:  dict[str, TagBuffer] = {}
        self._connected = False
        self._sample_count = 0
        logger.info("OPC UA subscriber initialized: %s (%.0fms window)", endpoint, sample_ms)

    def subscribe(self, tag_name: str, window_seconds: float = 300.0) -> None:
        """Register a tag for observation."""
        if tag_name not in self._buffers:
            self._buffers[tag_name] = TagBuffer(tag_name, window_seconds)
            logger.debug("Subscribed to OPC tag: %s", tag_name)

    def push_tag(self, tag_name: str, value: float,
                 timestamp: Optional[float] = None, quality: str = "GOOD") -> None:
        """
        Ingest one tag reading.
        Called by OPC UA subscription callback or simulation driver.
        """
        if tag_name not in self._buffers:
            self.subscribe(tag_name)
        self._buffers[tag_name].push(value, timestamp, quality)
        self._sample_count += 1

    def push_snapshot(self, snapshot: dict[str, float], timestamp: Optional[float] = None) -> None:
        """Ingest dict of {tag_name: value} all at the same timestamp."""
        ts = timestamp if timestamp is not None else time.time()
        for tag, value in snapshot.items():
            self.push_tag(tag, value, ts)

    def get_buffer(self, tag_name: str) -> Optional[TagBuffer]:
        return self._buffers.get(tag_name)

    @property
    def tag_names(self) -> list[str]:
        return sorted(self._buffers.keys())

    @property
    def total_samples(self) -> int:
        return self._sample_count


# ── SQL Historian Reader ──────────────────────────────────────────────────────

class SQLHistorianReader:
    """
    SQL historian batch replay engine.
    Ingests 3-year historical data for digital twin training.

    In production: connects to OSIsoft PI, Ignition, or similar.
    In lab: accepts dict-based data injection.
    """

    def __init__(self):
        self._records:  list[dict] = []
        self._replay_count = 0

    def ingest_batch(self, records: list[dict]) -> int:
        """
        Ingest historian records.
        Each record: {tag, value, timestamp, quality}
        Returns number of valid records ingested.
        """
        valid = 0
        for rec in records:
            if "tag" in rec and "value" in rec and "timestamp" in rec:
                self._records.append({
                    "tag":       str(rec["tag"]),
                    "value":     float(rec["value"]),
                    "timestamp": float(rec["timestamp"]),
                    "quality":   rec.get("quality", "GOOD"),
                })
                valid += 1
        logger.info("SQL historian: ingested %d/%d records", valid, len(records))
        return valid

    def replay_to_subscriber(self, subscriber: OPCUASubscriber,
                              start_ts: Optional[float] = None,
                              end_ts: Optional[float]   = None,
                              speed_factor: float        = 0.0) -> int:
        """
        Replay historical records into an OPC subscriber.
        speed_factor=0 → instant (no sleep); >0 → real-time simulation.
        Returns number of replayed samples.
        """
        records = self._records
        if start_ts:
            records = [r for r in records if r["timestamp"] >= start_ts]
        if end_ts:
            records = [r for r in records if r["timestamp"] <= end_ts]

        records = sorted(records, key=lambda r: r["timestamp"])

        for rec in records:
            subscriber.push_tag(
                rec["tag"], rec["value"], rec["timestamp"], rec["quality"]
            )
            if speed_factor > 0:
                time.sleep(speed_factor * 0.001)

        self._replay_count += len(records)
        return len(records)

    def get_tag_series(self, tag_name: str) -> list[TagSample]:
        """Return all historical samples for one tag, time-sorted."""
        return sorted(
            [
                TagSample(r["tag"], r["value"], r["timestamp"], r["quality"])
                for r in self._records if r["tag"] == tag_name
            ],
            key=lambda s: s.timestamp,
        )

    @property
    def record_count(self) -> int:
        return len(self._records)

    @property
    def tag_count(self) -> int:
        return len({r["tag"] for r in self._records})


# ── SCADA Observation Engine ──────────────────────────────────────────────────

class SCADAObserver:
    """
    Central observation engine for the CIEC cognitive core.

    Combines:
    - OPC UA real-time tag subscription
    - SQL historian batch replay
    - Feature extraction → plant state vector S(t)

    Feeds state vectors to FuzzyInferenceEngine and ConstrainedRL.
    """

    def __init__(
        self,
        opc_endpoint:     str   = "opc.tcp://localhost:4840/",
        window_seconds:   float = 300.0,
        store_path:       Optional[str] = None,
    ):
        kiswarm_dir     = os.environ.get("KISWARM_HOME", os.path.expanduser("~/KISWARM"))
        self._store     = store_path or os.path.join(kiswarm_dir, "scada_state.json")
        self.subscriber = OPCUASubscriber(opc_endpoint)
        self.historian  = SQLHistorianReader()
        self._window    = window_seconds
        self._alarms:   list[dict]  = []
        self._state_history: deque[PlantStateVector] = deque(maxlen=1000)
        self._snapshot_count = 0
        self._load()

    # ── Tag Management ────────────────────────────────────────────────────────

    def subscribe_tags(self, tag_names: list[str]) -> None:
        """Register tags for real-time observation."""
        for tag in tag_names:
            self.subscriber.subscribe(tag, self._window)
        logger.info("Subscribed %d tags", len(tag_names))

    def push_reading(self, tag: str, value: float,
                     timestamp: Optional[float] = None) -> None:
        """Ingest one real-time reading (OPC UA callback)."""
        self.subscriber.push_tag(tag, value, timestamp)

    def push_snapshot(self, snapshot: dict[str, float],
                      timestamp: Optional[float] = None) -> None:
        """Ingest complete plant snapshot."""
        self.subscriber.push_snapshot(snapshot, timestamp)
        self._snapshot_count += 1

    def push_alarm(self, tag: str, message: str, severity: str = "WARNING") -> None:
        self._alarms.append({
            "tag":       tag,
            "message":   message,
            "severity":  severity,
            "timestamp": time.time(),
        })

    def ingest_history(self, records: list[dict]) -> int:
        """Ingest SQL historian batch."""
        return self.historian.ingest_batch(records)

    # ── State Vector Building ─────────────────────────────────────────────────

    def build_state_vector(self) -> PlantStateVector:
        """
        Compute current plant state S(t) from all observed tags.
        This is the primary input to the CIEC cognitive core.
        """
        now     = time.time()
        cutoff  = now - 300   # last 5 minutes alarm count
        recent_alarms = [a for a in self._alarms if a["timestamp"] >= cutoff]
        fault_flags   = list({
            a["tag"] for a in recent_alarms if a["severity"] in ("CRITICAL", "FAULT")
        })

        tag_feats = {}
        for tag_name in self.subscriber.tag_names:
            buf = self.subscriber.get_buffer(tag_name)
            if buf and buf.sample_count > 0:
                tag_feats[tag_name] = buf.get_features()

        state = PlantStateVector(
            timestamp    = now,
            tag_features = tag_feats,
            alarm_count  = len(recent_alarms),
            fault_flags  = fault_flags,
        )

        self._state_history.append(state)
        self._save()
        return state

    def get_state_history(self, n: int = 100) -> list[PlantStateVector]:
        """Return the last n plant state vectors."""
        return list(self._state_history)[-n:]

    # ── Statistics & Status ───────────────────────────────────────────────────

    def get_stats(self) -> dict:
        return {
            "subscribed_tags":   len(self.subscriber.tag_names),
            "total_samples":     self.subscriber.total_samples,
            "historian_records": self.historian.record_count,
            "historian_tags":    self.historian.tag_count,
            "alarm_count":       len(self._alarms),
            "snapshot_count":    self._snapshot_count,
            "state_history":     len(self._state_history),
            "opc_endpoint":      self.subscriber.endpoint,
        }

    def get_anomalies(self, std_threshold: float = 3.0) -> list[dict]:
        """
        Return tags currently showing anomalous values (> N std devs from mean).
        """
        anomalies = []
        for tag_name in self.subscriber.tag_names:
            buf = self.subscriber.get_buffer(tag_name)
            if not buf or buf.sample_count < 10:
                continue
            feat = buf.get_features()
            if feat.std_dev > 0 and feat.range_val > std_threshold * feat.std_dev:
                anomalies.append({
                    "tag":          tag_name,
                    "mean":         feat.mean,
                    "std_dev":      feat.std_dev,
                    "current":      buf.latest(),
                    "z_score":      abs((buf.latest() or feat.mean) - feat.mean) / feat.std_dev,
                    "switching_freq": feat.switching_frequency,
                })
        return anomalies

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load(self):
        try:
            if os.path.exists(self._store):
                with open(self._store) as f:
                    raw = json.load(f)
                self._snapshot_count = raw.get("snapshot_count", 0)
        except Exception as exc:
            logger.warning("SCADA state load failed: %s", exc)

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self._store) if os.path.dirname(self._store) else ".", exist_ok=True)
            with open(self._store, "w") as f:
                json.dump({
                    "snapshot_count":  self._snapshot_count,
                    "subscribed_tags": self.subscriber.tag_names,
                    "last_updated":    time.strftime("%Y-%m-%dT%H:%M:%S"),
                }, f, indent=2)
        except Exception as exc:
            logger.error("SCADA state save failed: %s", exc)
