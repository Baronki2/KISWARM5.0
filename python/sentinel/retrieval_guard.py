"""
KISWARM v2.2 — MODULE 5: DIFFERENTIAL RETRIEVAL GUARD
======================================================
Guards knowledge retrieval by detecting drift and epistemic divergence.

When the swarm retrieves knowledge from Qdrant, this module:
  1. Re-runs similarity cross-check vs original sources
  2. Detects semantic drift from the original injection
  3. Flags epistemic divergence (facts changed in external world)
  4. Returns RetrievalGuardReport with trust level and drift score

Drift Detection:
  • Compare retrieved content against original ledger entry content
  • Cosine similarity drop > threshold → drift detected
  • Compare against fresh scout fetch → divergence detected

Epistemic Divergence:
  Unlike drift (internal mutation), divergence means the external world
  has changed — the knowledge was true when injected but may no longer
  be current. Detected by re-querying scouts and comparing similarity.

Author: KISWARM Project (Baron Marco Paolo Ialongo)
Version: 2.2
"""

import hashlib
import logging
import math
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from .semantic_conflict import SemanticConflictDetector, cosine_similarity

logger = logging.getLogger("sentinel.retrieval_guard")


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class DriftResult:
    """Result of comparing retrieved content to its original version."""
    hash_id:            str
    similarity_to_original: float      # 1.0 = identical, 0.0 = completely different
    drift_detected:     bool
    drift_severity:     str             # "NONE" | "MINOR" | "MODERATE" | "SEVERE"
    original_hash:      str
    current_hash:       str
    hash_match:         bool            # True if content bit-for-bit identical


@dataclass
class DivergenceResult:
    """Result of comparing swarm knowledge vs freshly fetched external data."""
    hash_id:              str
    query:                str
    swarm_similarity:     float         # similarity of stored vs fresh data
    divergence_detected:  bool
    divergence_level:     str           # "NONE" | "LOW" | "MODERATE" | "HIGH" | "CRITICAL"
    recommendation:       str           # "TRUST" | "REVALIDATE" | "REPLACE"
    fresh_content_len:    int
    timestamp:            str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class RetrievalGuardReport:
    """Complete retrieval trust assessment."""
    hash_id:            str
    trust_level:        str     # "TRUSTED" | "CAUTION" | "STALE" | "COMPROMISED"
    trust_score:        float   # 0.0–1.0
    drift:              Optional[DriftResult]
    divergence:         Optional[DivergenceResult]
    ledger_valid:       bool    # Cryptographic integrity check
    decay_confidence:   float   # Current decayed confidence
    recommendation:     str     # Action to take
    flags:              list[str] = field(default_factory=list)
    timestamp:          str = field(default_factory=lambda: datetime.now().isoformat())


# ── Drift Detector ────────────────────────────────────────────────────────────

class DriftDetector:
    """
    Detects semantic drift by comparing current retrieved content
    against the original content stored in the cryptographic ledger.
    """

    DRIFT_THRESHOLDS = {
        "SEVERE":   0.50,   # similarity < 0.50 → severe drift
        "MODERATE": 0.70,   # similarity 0.50–0.70
        "MINOR":    0.85,   # similarity 0.70–0.85
        "NONE":     1.00,   # similarity > 0.85 → no drift
    }

    def __init__(self, encoder=None):
        self._detector = SemanticConflictDetector(encoder=encoder)

    def check(
        self,
        hash_id:          str,
        original_content: str,
        retrieved_content: str,
    ) -> DriftResult:
        """
        Compare original content (from ledger) against retrieved content.
        """
        original_hash  = hashlib.sha256(original_content.encode()).hexdigest()
        current_hash   = hashlib.sha256(retrieved_content.encode()).hexdigest()
        hash_match     = original_hash == current_hash

        if hash_match:
            return DriftResult(
                hash_id=hash_id,
                similarity_to_original=1.0,
                drift_detected=False,
                drift_severity="NONE",
                original_hash=original_hash,
                current_hash=current_hash,
                hash_match=True,
            )

        # Content differs — compute semantic similarity
        sim, _severity = self._detector.quick_check(original_content, retrieved_content)

        drift_severity = "NONE"
        for severity, threshold in sorted(
            self.DRIFT_THRESHOLDS.items(),
            key=lambda kv: kv[1],
        ):
            if sim < threshold:
                drift_severity = severity
                break

        drift_detected = drift_severity not in ("NONE",)

        if drift_detected:
            logger.warning(
                "Drift detected: hash=%s | similarity=%.2f | severity=%s",
                hash_id, sim, drift_severity,
            )

        return DriftResult(
            hash_id=hash_id,
            similarity_to_original=round(sim, 4),
            drift_detected=drift_detected,
            drift_severity=drift_severity,
            original_hash=original_hash,
            current_hash=current_hash,
            hash_match=False,
        )


# ── Divergence Detector ───────────────────────────────────────────────────────

class DivergenceDetector:
    """
    Detects epistemic divergence: swarm knowledge that was accurate
    when injected but the external world has since changed.
    """

    DIVERGENCE_THRESHOLDS = {
        "CRITICAL": 0.25,   # similarity < 0.25 → world has fundamentally changed
        "HIGH":     0.45,
        "MODERATE": 0.60,
        "LOW":      0.75,
        "NONE":     1.00,
    }

    def __init__(self, encoder=None):
        self._detector = SemanticConflictDetector(encoder=encoder)

    def check(
        self,
        hash_id:        str,
        query:          str,
        stored_content: str,
        fresh_content:  str,
    ) -> DivergenceResult:
        """
        Compare stored knowledge against freshly fetched content.
        """
        if not fresh_content or len(fresh_content.strip()) < 20:
            return DivergenceResult(
                hash_id=hash_id,
                query=query,
                swarm_similarity=1.0,
                divergence_detected=False,
                divergence_level="NONE",
                recommendation="TRUST",
                fresh_content_len=0,
            )

        sim, _ = self._detector.quick_check(stored_content, fresh_content)

        divergence_level = "NONE"
        for level, threshold in sorted(
            self.DIVERGENCE_THRESHOLDS.items(),
            key=lambda kv: kv[1],
        ):
            if sim < threshold:
                divergence_level = level
                break

        divergence_detected = divergence_level not in ("NONE", "LOW")

        recommendation = "TRUST"
        if divergence_level == "CRITICAL":
            recommendation = "REPLACE"
        elif divergence_level in ("HIGH", "MODERATE"):
            recommendation = "REVALIDATE"

        if divergence_detected:
            logger.warning(
                "Epistemic divergence: hash=%s | query='%s' | sim=%.2f | level=%s → %s",
                hash_id, query[:40], sim, divergence_level, recommendation,
            )

        return DivergenceResult(
            hash_id=hash_id,
            query=query,
            swarm_similarity=round(sim, 4),
            divergence_detected=divergence_detected,
            divergence_level=divergence_level,
            recommendation=recommendation,
            fresh_content_len=len(fresh_content),
        )


# ── Differential Retrieval Guard ─────────────────────────────────────────────

class DifferentialRetrievalGuard:
    """
    Full retrieval trust pipeline — combines:
      • Drift detection (internal mutation check)
      • Divergence detection (external world change check)
      • Cryptographic ledger verification
      • Decay confidence score

    Usage:
        guard = DifferentialRetrievalGuard(ledger, decay_engine)

        report = guard.assess(
            hash_id         = "a3f2b91c",
            query           = "quantum computing",
            retrieved_content = qdrant_result["content"],
            fresh_content   = fresh_scout_result,   # optional
        )

        if report.trust_level == "COMPROMISED":
            raise SecurityError("Knowledge integrity violation")
        if report.trust_level == "STALE":
            sentinel.extract(query, force=True)
    """

    def __init__(self, ledger=None, decay_engine=None, encoder=None):
        self._ledger  = ledger
        self._decay   = decay_engine
        self._drift   = DriftDetector(encoder=encoder)
        self._diverg  = DivergenceDetector(encoder=encoder)

    def assess(
        self,
        hash_id:           str,
        query:             str,
        retrieved_content: str,
        original_content:  Optional[str] = None,
        fresh_content:     Optional[str] = None,
    ) -> RetrievalGuardReport:
        """
        Full trust assessment for a retrieved knowledge entry.

        Args:
            hash_id:            Knowledge hash ID.
            query:              Original query.
            retrieved_content:  Content as returned from Qdrant.
            original_content:   Content from ledger (if available).
            fresh_content:      Freshly fetched content (if available).

        Returns:
            RetrievalGuardReport with trust level and recommendations.
        """
        flags: list[str] = []

        # ── Cryptographic integrity ───────────────────────────────────────────
        ledger_valid = True
        if self._ledger:
            entry = self._ledger.get_entry(hash_id)
            if entry:
                if not entry.verify_signature():
                    ledger_valid = False
                    flags.append("LEDGER_SIGNATURE_INVALID")
                    logger.critical("Ledger signature invalid for hash=%s", hash_id)
            else:
                flags.append("NOT_IN_LEDGER")

        # ── Decay confidence ──────────────────────────────────────────────────
        decay_conf = 1.0
        if self._decay:
            decay_conf = self._decay.get_confidence(hash_id)
            if decay_conf < 0.40:
                flags.append(f"DECAYED:{decay_conf:.0%}")

        # ── Drift detection ───────────────────────────────────────────────────
        drift_result = None
        if original_content and original_content != retrieved_content:
            drift_result = self._drift.check(hash_id, original_content, retrieved_content)
            if drift_result.drift_detected:
                flags.append(f"DRIFT_{drift_result.drift_severity}")

        # ── Divergence detection ──────────────────────────────────────────────
        divergence_result = None
        if fresh_content:
            divergence_result = self._diverg.check(
                hash_id, query, retrieved_content, fresh_content
            )
            if divergence_result.divergence_detected:
                flags.append(f"DIVERGENCE_{divergence_result.divergence_level}")

        # ── Trust level computation ───────────────────────────────────────────
        trust_score = decay_conf

        if not ledger_valid:
            trust_score *= 0.0      # Cryptographic failure → zero trust
        elif drift_result and drift_result.drift_severity == "SEVERE":
            trust_score *= 0.3
        elif drift_result and drift_result.drift_severity == "MODERATE":
            trust_score *= 0.6
        elif divergence_result and divergence_result.divergence_level == "CRITICAL":
            trust_score *= 0.2
        elif divergence_result and divergence_result.divergence_level == "HIGH":
            trust_score *= 0.5

        trust_score = round(max(0.0, min(1.0, trust_score)), 3)

        if not ledger_valid:
            trust_level = "COMPROMISED"
            recommendation = "REJECT — cryptographic integrity failure"
        elif trust_score < 0.25:
            trust_level = "COMPROMISED"
            recommendation = "REJECT and re-extract from sources"
        elif trust_score < 0.50:
            trust_level = "STALE"
            recommendation = "Trigger forced re-extraction"
        elif trust_score < 0.75:
            trust_level = "CAUTION"
            recommendation = "Use with caution; schedule revalidation"
        else:
            trust_level = "TRUSTED"
            recommendation = "Knowledge is current and verified"

        report = RetrievalGuardReport(
            hash_id=hash_id,
            trust_level=trust_level,
            trust_score=trust_score,
            drift=drift_result,
            divergence=divergence_result,
            ledger_valid=ledger_valid,
            decay_confidence=decay_conf,
            recommendation=recommendation,
            flags=flags,
        )

        logger.info(
            "Retrieval guard: hash=%s | trust=%s (%.0f%%) | flags=%s",
            hash_id, trust_level, trust_score * 100, flags or "none",
        )
        return report
