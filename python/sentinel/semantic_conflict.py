"""
KISWARM v2.2 — MODULE 1: SEMANTIC CONFLICT DETECTION
=====================================================
Detects contradiction clusters in intelligence payloads using
embedding cosine similarity — not just surface-level text diff.

Two claims may use entirely different words but be semantically
opposed. This module finds those hidden contradictions before
knowledge is committed to swarm memory.

Algorithm:
  1. Embed each IntelligencePacket into 384-dim vector space
  2. Build pairwise cosine similarity matrix
  3. Flag pairs with similarity in the "contradiction zone" (0.15–0.45)
     — too similar to be unrelated, too different to agree
  4. Cluster contradicting pairs via union-find
  5. Return ConflictReport with severity scores

Author: KISWARM Project (Baron Marco Paolo Ialongo)
Version: 2.2
"""

import hashlib
import math
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("sentinel.conflict")


# ── Vector math (no numpy required) ──────────────────────────────────────────

def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))

def _norm(v: list[float]) -> float:
    return math.sqrt(sum(x * x for x in v))

def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two vectors. Returns -1.0 to 1.0."""
    na, nb = _norm(a), _norm(b)
    if na == 0.0 or nb == 0.0:
        return 0.0
    return max(-1.0, min(1.0, _dot(a, b) / (na * nb)))


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class ConflictPair:
    """A detected semantic contradiction between two intelligence sources."""
    source_a:       str
    source_b:       str
    content_a:      str
    content_b:      str
    similarity:     float       # cosine similarity (lower = more contradictory)
    severity:       str         # "LOW" | "MEDIUM" | "HIGH" | "CRITICAL"
    cluster_id:     int = 0


@dataclass
class ConflictReport:
    """Full conflict analysis for a set of intelligence packets."""
    total_pairs:        int
    conflict_pairs:     list[ConflictPair]
    clusters:           list[list[str]]     # grouped source names
    max_severity:       str                 # highest severity found
    resolution_needed:  bool
    similarity_matrix:  dict = field(default_factory=dict)  # source→source→score

    @property
    def conflict_count(self) -> int:
        return len(self.conflict_pairs)

    @property
    def has_critical(self) -> bool:
        return any(p.severity == "CRITICAL" for p in self.conflict_pairs)


# ── Union-Find for clustering ─────────────────────────────────────────────────

class UnionFind:
    def __init__(self, n: int):
        self.parent = list(range(n))
        self.rank   = [0] * n

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, x: int, y: int):
        rx, ry = self.find(x), self.find(y)
        if rx == ry:
            return
        if self.rank[rx] < self.rank[ry]:
            rx, ry = ry, rx
        self.parent[ry] = rx
        if self.rank[rx] == self.rank[ry]:
            self.rank[rx] += 1


# ── Semantic Conflict Detector ────────────────────────────────────────────────

class SemanticConflictDetector:
    """
    Detects semantic contradictions between intelligence packets
    using embedding cosine similarity clustering.

    Contradiction Zone:
      similarity < 0.20  → CRITICAL  (direct contradiction)
      0.20 – 0.35        → HIGH      (strong disagreement)
      0.35 – 0.50        → MEDIUM    (notable divergence)
      0.50 – 0.65        → LOW       (minor drift)
      > 0.65             → OK        (consistent / corroborating)
    """

    THRESHOLDS = {
        "CRITICAL": 0.20,
        "HIGH":     0.35,
        "MEDIUM":   0.50,
        "LOW":      0.65,
    }

    def __init__(self, encoder=None):
        """
        Args:
            encoder: SentenceTransformer instance or None (uses hash fallback).
        """
        self._encoder = encoder
        self._cache: dict[str, list[float]] = {}

    def _embed(self, text: str) -> list[float]:
        """Generate a 384-dim embedding. Caches by content hash."""
        key = hashlib.md5(text[:512].encode()).hexdigest()
        if key in self._cache:
            return self._cache[key]

        if self._encoder is not None:
            try:
                vec = self._encoder.encode(text[:512]).tolist()
                self._cache[key] = vec
                return vec
            except Exception as exc:
                logger.warning("Encoder failed, using hash fallback: %s", exc)

        # Deterministic hash-based pseudo-embedding (384 dims)
        h = hashlib.sha256(text.encode()).digest()
        vec = ([(b / 255.0) * 2 - 1 for b in h] * 12)[:384]
        self._cache[key] = vec
        return vec

    def _severity(self, similarity: float) -> str:
        """Map cosine similarity to severity label."""
        if similarity < self.THRESHOLDS["CRITICAL"]:
            return "CRITICAL"
        if similarity < self.THRESHOLDS["HIGH"]:
            return "HIGH"
        if similarity < self.THRESHOLDS["MEDIUM"]:
            return "MEDIUM"
        if similarity < self.THRESHOLDS["LOW"]:
            return "LOW"
        return "OK"

    def analyze(self, packets: list) -> ConflictReport:
        """
        Analyze a list of IntelligencePacket objects for semantic conflicts.

        Args:
            packets: List of IntelligencePacket (source, content attributes).

        Returns:
            ConflictReport with all detected contradictions and clusters.
        """
        n = len(packets)
        if n < 2:
            return ConflictReport(
                total_pairs=0, conflict_pairs=[], clusters=[],
                max_severity="OK", resolution_needed=False,
            )

        # Build embeddings
        embeddings = [self._embed(p.content) for p in packets]

        # Compute pairwise similarity matrix
        sim_matrix: dict[str, dict[str, float]] = {}
        conflict_pairs: list[ConflictPair] = []
        uf = UnionFind(n)

        for i in range(n):
            src_i = packets[i].source
            sim_matrix[src_i] = {}
            for j in range(i + 1, n):
                src_j = packets[j].source
                sim = cosine_similarity(embeddings[i], embeddings[j])
                sim_matrix[src_i][src_j] = sim

                severity = self._severity(sim)
                if severity != "OK":
                    conflict_pairs.append(ConflictPair(
                        source_a=src_i,
                        source_b=src_j,
                        content_a=packets[i].content[:300],
                        content_b=packets[j].content[:300],
                        similarity=sim,
                        severity=severity,
                    ))
                    uf.union(i, j)

        # Extract contradiction clusters
        cluster_map: dict[int, list[str]] = {}
        for i in range(n):
            root = uf.find(i)
            cluster_map.setdefault(root, []).append(packets[i].source)

        # Only include clusters with actual conflicts (size > 1)
        clusters = [sources for sources in cluster_map.values() if len(sources) > 1]

        # Tag conflict pairs with cluster IDs
        root_to_cluster = {root: idx for idx, root in enumerate(cluster_map)}
        for pair in conflict_pairs:
            idx_a = next(i for i, p in enumerate(packets) if p.source == pair.source_a)
            pair.cluster_id = root_to_cluster.get(uf.find(idx_a), 0)

        # Determine max severity
        severity_order = ["OK", "LOW", "MEDIUM", "HIGH", "CRITICAL"]
        severities = [p.severity for p in conflict_pairs] or ["OK"]
        max_sev = max(severities, key=lambda s: severity_order.index(s))

        resolution_needed = max_sev in ("HIGH", "CRITICAL")

        if conflict_pairs:
            logger.warning(
                "Semantic conflicts detected: %d pairs | max severity: %s | clusters: %d",
                len(conflict_pairs), max_sev, len(clusters),
            )
        else:
            logger.info("Semantic analysis: no conflicts detected across %d sources", n)

        return ConflictReport(
            total_pairs=n * (n - 1) // 2,
            conflict_pairs=conflict_pairs,
            clusters=clusters,
            max_severity=max_sev,
            resolution_needed=resolution_needed,
            similarity_matrix=sim_matrix,
        )

    def quick_check(self, text_a: str, text_b: str) -> tuple[float, str]:
        """
        Quick two-text contradiction check.
        Returns (cosine_similarity, severity_label).
        """
        vec_a = self._embed(text_a)
        vec_b = self._embed(text_b)
        sim = cosine_similarity(vec_a, vec_b)
        return sim, self._severity(sim)
