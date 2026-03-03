"""
KISWARM v2.2 — MODULE 2: KNOWLEDGE DECAY ENGINE
================================================
Implements half-life confidence decay for swarm knowledge entries.
Knowledge is not eternal — facts become stale, models improve,
sources get updated. The decay engine ensures the swarm revalidates
aging knowledge before trusting it.

Decay Model:
  confidence(t) = confidence₀ × 2^(-t / half_life)

  where:
    t         = elapsed time since injection (hours)
    half_life = category-specific decay period (hours)

Half-life Categories:
  "breaking_news"    →   6h   (news changes within hours)
  "current_events"   →  48h   (days-old news degrades fast)
  "technical_specs"  → 720h   (30 days — specs update monthly)
  "scientific"       → 4380h  (6 months — papers are relatively stable)
  "encyclopedic"     → 8760h  (1 year — foundational facts)
  "historical"       → inf    (never decays — past doesn't change)

Scheduled Revalidation:
  When decayed_confidence < revalidation_threshold (default 0.40),
  the entry is flagged for re-extraction by the Sentinel Bridge.

Author: KISWARM Project (Baron Marco Paolo Ialongo)
Version: 2.2
"""

import json
import logging
import math
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("sentinel.decay")

KISWARM_HOME = os.environ.get("KISWARM_HOME", os.path.expanduser("~"))
KISWARM_DIR  = os.path.join(KISWARM_HOME, "KISWARM")
DECAY_STORE  = os.path.join(KISWARM_DIR, "decay_registry.json")


# ── Half-life catalogue (in hours) ───────────────────────────────────────────

HALF_LIVES: dict[str, float] = {
    "breaking_news":   6.0,
    "current_events":  48.0,
    "technical_specs": 720.0,       # 30 days
    "scientific":      4_380.0,     # 6 months
    "encyclopedic":    8_760.0,     # 1 year
    "historical":      float("inf"),
    "default":         720.0,       # 30 days fallback
}


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class DecayRecord:
    """Tracks a single knowledge entry's decay state."""
    hash_id:             str
    query:               str
    original_confidence: float
    injected_at:         float          # Unix timestamp
    category:            str            # half-life category
    half_life_hours:     float
    revalidated_at:      Optional[float] = None
    revalidation_count:  int = 0
    retired:             bool = False   # True when confidence → 0

    def current_confidence(self, now: Optional[float] = None) -> float:
        """Compute decayed confidence at time `now` (default: current time)."""
        if math.isinf(self.half_life_hours):
            return self.original_confidence
        t = now if now is not None else time.time()
        elapsed_hours = (t - self.injected_at) / 3600.0
        decayed = self.original_confidence * math.pow(2.0, -elapsed_hours / self.half_life_hours)
        return round(max(0.0, min(1.0, decayed)), 4)

    def age_hours(self, now: Optional[float] = None) -> float:
        t = now if now is not None else time.time()
        return (t - self.injected_at) / 3600.0

    def needs_revalidation(
        self,
        threshold: float = 0.40,
        now: Optional[float] = None,
    ) -> bool:
        if self.retired:
            return False
        return self.current_confidence(now) < threshold

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DecayScanReport:
    """Summary of a full decay scan across all registered entries."""
    scanned:            int
    healthy:            list[str]       # hash_ids above threshold
    needs_revalidation: list[str]       # hash_ids below threshold
    retired:            list[str]       # hash_ids essentially expired
    average_confidence: float
    oldest_entry_hours: float
    timestamp:          str = field(default_factory=lambda: datetime.now().isoformat())


# ── Knowledge Decay Engine ────────────────────────────────────────────────────

class KnowledgeDecayEngine:
    """
    Manages confidence decay for all swarm knowledge entries.

    Usage:
        engine = KnowledgeDecayEngine()

        # Register a newly injected knowledge entry
        engine.register("abc123", "quantum computing", 0.87, category="scientific")

        # Check current confidence
        conf = engine.get_confidence("abc123")

        # Scan all entries — returns which need revalidation
        report = engine.scan()
        for hash_id in report.needs_revalidation:
            sentinel.extract(engine.get_query(hash_id), force=True)
    """

    RETIRE_THRESHOLD   = 0.10   # Below this → retired
    REVALIDATE_THRESHOLD = 0.40

    def __init__(self, store_path: str = DECAY_STORE):
        self._store_path = store_path
        self._registry: dict[str, DecayRecord] = {}
        self._load()

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load(self):
        if os.path.exists(self._store_path):
            try:
                with open(self._store_path) as f:
                    raw = json.load(f)
                for hash_id, data in raw.items():
                    self._registry[hash_id] = DecayRecord(**data)
                logger.info("Decay registry loaded: %d entries", len(self._registry))
            except (json.JSONDecodeError, TypeError, OSError) as exc:
                logger.warning("Decay registry load failed: %s", exc)
                self._registry = {}

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self._store_path), exist_ok=True)
            with open(self._store_path, "w") as f:
                json.dump(
                    {k: v.to_dict() for k, v in self._registry.items()},
                    f, indent=2,
                )
        except OSError as exc:
            logger.error("Decay registry save failed: %s", exc)

    # ── Core API ──────────────────────────────────────────────────────────────

    def register(
        self,
        hash_id:    str,
        query:      str,
        confidence: float,
        category:   str = "default",
        now:        Optional[float] = None,
    ) -> DecayRecord:
        """
        Register a new knowledge entry for decay tracking.

        Args:
            hash_id:    Unique ID (from SwarmKnowledge.hash_id).
            query:      Original query (for revalidation scheduling).
            confidence: Initial confidence score at injection time.
            category:   Half-life category (see HALF_LIVES dict).
            now:        Unix timestamp override (for testing).
        """
        if category not in HALF_LIVES:
            logger.warning("Unknown category '%s', using 'default'", category)
            category = "default"

        record = DecayRecord(
            hash_id=hash_id,
            query=query,
            original_confidence=confidence,
            injected_at=now if now is not None else time.time(),
            category=category,
            half_life_hours=HALF_LIVES[category],
        )
        self._registry[hash_id] = record
        self._save()
        logger.info(
            "Registered decay: hash=%s | category=%s | half_life=%.0fh",
            hash_id, category, record.half_life_hours,
        )
        return record

    def get_confidence(self, hash_id: str, now: Optional[float] = None) -> float:
        """Get current decayed confidence for a knowledge entry."""
        if hash_id not in self._registry:
            return 0.0
        return self._registry[hash_id].current_confidence(now)

    def get_query(self, hash_id: str) -> Optional[str]:
        """Get the original query for a knowledge entry."""
        rec = self._registry.get(hash_id)
        return rec.query if rec else None

    def mark_revalidated(self, hash_id: str, new_confidence: float):
        """
        Update an entry after successful revalidation.
        Resets the injection timestamp and updates confidence.
        """
        if hash_id not in self._registry:
            logger.warning("Cannot revalidate unknown hash_id: %s", hash_id)
            return
        rec = self._registry[hash_id]
        rec.original_confidence = new_confidence
        rec.injected_at = time.time()
        rec.revalidated_at = time.time()
        rec.revalidation_count += 1
        rec.retired = False
        self._save()
        logger.info(
            "Revalidated: hash=%s | new_confidence=%.0f%% | count=%d",
            hash_id, new_confidence * 100, rec.revalidation_count,
        )

    def scan(self, now: Optional[float] = None) -> DecayScanReport:
        """
        Scan all registered entries and return a revalidation report.
        Also marks entries below RETIRE_THRESHOLD as retired.
        """
        healthy, needs_revalidation, retired = [], [], []
        confidences = []

        for hash_id, rec in self._registry.items():
            conf = rec.current_confidence(now)
            confidences.append(conf)

            if conf < self.RETIRE_THRESHOLD:
                if not rec.retired:
                    rec.retired = True
                    logger.info("Retired: hash=%s | conf=%.1f%%", hash_id, conf * 100)
                retired.append(hash_id)
            elif conf < self.REVALIDATE_THRESHOLD:
                needs_revalidation.append(hash_id)
            else:
                healthy.append(hash_id)

        if self._registry:
            self._save()

        oldest = 0.0
        if self._registry:
            oldest = max(
                rec.age_hours(now) for rec in self._registry.values()
            )

        avg_conf = sum(confidences) / len(confidences) if confidences else 0.0

        report = DecayScanReport(
            scanned=len(self._registry),
            healthy=healthy,
            needs_revalidation=needs_revalidation,
            retired=retired,
            average_confidence=round(avg_conf, 3),
            oldest_entry_hours=round(oldest, 1),
        )

        logger.info(
            "Decay scan: %d healthy | %d need revalidation | %d retired",
            len(healthy), len(needs_revalidation), len(retired),
        )
        return report

    def get_all_records(self) -> list[dict]:
        """Dump all records as dicts (for API/monitoring)."""
        return [
            {**rec.to_dict(), "current_confidence": rec.current_confidence()}
            for rec in self._registry.values()
        ]

    def infer_category(self, sources: list[str], query: str) -> str:
        """
        Heuristically infer the half-life category from scout sources and query.
        """
        query_lower = query.lower()
        sources_lower = [s.lower() for s in sources]

        if any(k in query_lower for k in ("today", "breaking", "latest", "just now")):
            return "breaking_news"
        if any(k in query_lower for k in ("news", "announced", "released", "2026", "2025")):
            return "current_events"
        if "arxiv" in sources_lower:
            return "scientific"
        if "wikipedia" in sources_lower:
            return "encyclopedic"
        if any(k in query_lower for k in ("history", "historical", "ancient", "war of", "born in")):
            return "historical"
        if any(k in query_lower for k in ("spec", "standard", "protocol", "api", "version")):
            return "technical_specs"
        return "default"
