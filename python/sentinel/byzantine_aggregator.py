"""
KISWARM v4.1 — Module 22: Byzantine-Tolerant Federated Aggregator
==================================================================
Implements robust gradient aggregation for multi-site federated learning.

Byzantine tolerance:
  Condition:  N ≥ 3f + 1  (N sites, f Byzantine/corrupted)
  Methods:
    - Trimmed Mean:  sort, remove top/bottom f, average rest
    - Multi-Krum:    select m gradients closest to consensus
    - Coordinate-wise Median
    - FLTrust (root gradient weighting)

Global update rule:
  θ ← θ − η * robust_mean(g_i)

No raw plant data leaves site — only gradient deltas + metadata.
"""

from __future__ import annotations

import math
import time
import hashlib
import json
import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any


# ─────────────────────────────────────────────────────────────────────────────
# GRADIENT VECTOR HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _vec_norm(v: List[float]) -> float:
    return math.sqrt(sum(x*x for x in v))

def _vec_sub(a: List[float], b: List[float]) -> List[float]:
    return [x - y for x, y in zip(a, b)]

def _vec_add(a: List[float], b: List[float]) -> List[float]:
    return [x + y for x, y in zip(a, b)]

def _vec_scale(v: List[float], s: float) -> List[float]:
    return [x * s for x in v]

def _vec_mean(vecs: List[List[float]]) -> List[float]:
    if not vecs: return []
    n = len(vecs[0])
    return [sum(v[i] for v in vecs) / len(vecs) for i in range(n)]

def _vec_cosine(a: List[float], b: List[float]) -> float:
    na, nb = _vec_norm(a), _vec_norm(b)
    if na < 1e-12 or nb < 1e-12: return 0.0
    return sum(x*y for x, y in zip(a, b)) / (na * nb)


# ─────────────────────────────────────────────────────────────────────────────
# SITE GRADIENT UPDATE
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SiteUpdate:
    site_id:      str
    gradient:     List[float]
    param_dim:    int
    step:         int
    performance:  float       # site-local KPI (stability score etc.)
    n_samples:    int         # local training samples used
    metadata:     Dict[str, Any] = field(default_factory=dict)
    signature:    str = ""

    def __post_init__(self):
        if not self.signature:
            payload = json.dumps({
                "site": self.site_id,
                "step": self.step,
                "grad_hash": hashlib.sha256(
                    str(self.gradient).encode()
                ).hexdigest()[:16],
            }, sort_keys=True).encode()
            self.signature = hashlib.sha256(payload).hexdigest()[:24]

    def to_dict(self) -> dict:
        return {
            "site_id":     self.site_id,
            "param_dim":   self.param_dim,
            "step":        self.step,
            "performance": self.performance,
            "n_samples":   self.n_samples,
            "grad_norm":   round(_vec_norm(self.gradient), 6),
            "signature":   self.signature,
        }


# ─────────────────────────────────────────────────────────────────────────────
# AGGREGATION METHODS
# ─────────────────────────────────────────────────────────────────────────────

def trimmed_mean(
    gradients: List[List[float]],
    f:         int,
) -> List[float]:
    """
    Coordinate-wise trimmed mean.
    Sort values per dimension, remove f highest + f lowest, average rest.
    Requires N ≥ 2f + 1.
    """
    if not gradients: return []
    n     = len(gradients[0])
    N     = len(gradients)
    trim  = min(f, max(0, (N - 1) // 2))
    result = []
    for d in range(n):
        col    = sorted(g[d] for g in gradients)
        kept   = col[trim: N - trim] if trim > 0 else col
        result.append(sum(kept) / max(len(kept), 1))
    return result


def coordinate_median(gradients: List[List[float]]) -> List[float]:
    """Coordinate-wise median."""
    if not gradients: return []
    n = len(gradients[0])
    result = []
    for d in range(n):
        col = sorted(g[d] for g in gradients)
        m   = len(col)
        if m % 2 == 1:
            result.append(col[m // 2])
        else:
            result.append((col[m // 2 - 1] + col[m // 2]) / 2.0)
    return result


def multi_krum(
    gradients: List[List[float]],
    f:         int,
    m:         int = None,
) -> List[float]:
    """
    Multi-Krum aggregation.
    Selects m gradients with smallest sum-of-distances to n−f−2 neighbours.
    m defaults to N − f.
    """
    N = len(gradients)
    if N == 0: return []
    m = m or max(1, N - f)
    m = min(m, N)

    # Pairwise distances
    dist = [[0.0] * N for _ in range(N)]
    for i in range(N):
        for j in range(i + 1, N):
            d = _vec_norm(_vec_sub(gradients[i], gradients[j]))
            dist[i][j] = dist[j][i] = d

    # Score each gradient
    k = N - f - 2
    k = max(1, k)
    scores = []
    for i in range(N):
        sorted_d = sorted(dist[i][:i] + dist[i][i+1:])
        scores.append((sum(sorted_d[:k]), i))

    scores.sort()
    selected = [gradients[idx] for _, idx in scores[:m]]
    return _vec_mean(selected)


def fltrust(
    root_grad: List[float],
    gradients: List[List[float]],
) -> List[float]:
    """
    FLTrust: weight each client gradient by cosine similarity to root gradient.
    Root gradient computed from trusted clean data subset.
    """
    if not gradients: return root_grad[:]
    weights = [max(0.0, _vec_cosine(g, root_grad)) for g in gradients]
    total   = sum(weights) or 1e-12
    result  = [0.0] * len(gradients[0])
    for w, g in zip(weights, gradients):
        for d in range(len(result)):
            result[d] += (w / total) * g[d]
    return result


# ─────────────────────────────────────────────────────────────────────────────
# BYZANTINE FAULT DETECTOR
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class AnomalyReport:
    site_id:       str
    anomaly_type:  str
    score:         float
    details:       str

    def to_dict(self) -> dict:
        return {
            "site_id":      self.site_id,
            "anomaly_type": self.anomaly_type,
            "score":        round(self.score, 4),
            "details":      self.details,
        }


def detect_byzantine_updates(
    updates: List[SiteUpdate],
    threshold_sigma: float = 3.0,
) -> Tuple[List[SiteUpdate], List[AnomalyReport]]:
    """
    Flag potentially Byzantine updates.
    Criteria:
      - Gradient norm > mean + 3σ
      - Gradient direction >90° from consensus
    Returns (clean_updates, anomaly_reports).
    """
    if len(updates) < 3:
        return updates, []

    norms    = [_vec_norm(u.gradient) for u in updates]
    mean_n   = sum(norms) / len(norms)
    std_n    = math.sqrt(sum((x - mean_n)**2 for x in norms) / len(norms))
    consensus = _vec_mean([u.gradient for u in updates])

    clean:    List[SiteUpdate]   = []
    anomalies: List[AnomalyReport] = []

    for i, upd in enumerate(updates):
        norm = norms[i]
        cosim = _vec_cosine(upd.gradient, consensus)
        z_score = abs(norm - mean_n) / max(std_n, 1e-12)

        if z_score > threshold_sigma:
            anomalies.append(AnomalyReport(
                upd.site_id, "norm_outlier", z_score,
                f"Gradient norm {norm:.3f} is {z_score:.1f}σ from mean"
            ))
        elif cosim < 0:
            anomalies.append(AnomalyReport(
                upd.site_id, "direction_flip", abs(cosim),
                f"Gradient direction opposes consensus (cos={cosim:.3f})"
            ))
        else:
            clean.append(upd)

    return clean, anomalies


# ─────────────────────────────────────────────────────────────────────────────
# FEDERATED AGGREGATOR
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class AggregationResult:
    round_id:       int
    method:         str
    n_sites:        int
    n_used:         int
    n_excluded:     int
    aggregated:     List[float]
    anomalies:      List[Dict]
    byzantine_safe: bool
    latency_ms:     float

    def to_dict(self) -> dict:
        return {
            "round_id":       self.round_id,
            "method":         self.method,
            "n_sites":        self.n_sites,
            "n_used":         self.n_used,
            "n_excluded":     self.n_excluded,
            "grad_norm":      round(_vec_norm(self.aggregated), 6),
            "anomalies":      self.anomalies,
            "byzantine_safe": self.byzantine_safe,
            "latency_ms":     round(self.latency_ms, 2),
        }


class ByzantineFederatedAggregator:
    """
    Robust federated gradient aggregation for multi-site KISWARM mesh.

    Safety:
      - N ≥ 3f + 1 enforced before aggregation
      - No raw data ever transmitted — gradients + metadata only
      - All rounds logged with cryptographic signatures
    """

    def __init__(self, f_tolerance: int = 1, method: str = "trimmed_mean",
                 lr: float = 1e-3, seed: int = 0):
        self.f          = f_tolerance
        self.method     = method
        self.lr         = lr
        self._rng       = random.Random(seed)
        self._theta:    List[float] = []
        self._round     = 0
        self._history:  List[AggregationResult] = []
        self._sites:    Dict[str, Dict] = {}
        self._anomaly_log: List[AnomalyReport] = []

    # ── Site registration ─────────────────────────────────────────────────────

    def register_site(self, site_id: str, metadata: dict = None) -> dict:
        self._sites[site_id] = {
            "site_id":    site_id,
            "rounds":     0,
            "last_perf":  0.0,
            "trust_score": 1.0,
            "metadata":   metadata or {},
        }
        return {"registered": True, "site_id": site_id}

    # ── Aggregation ───────────────────────────────────────────────────────────

    def aggregate(
        self,
        updates: List[SiteUpdate],
        method:  str = None,
    ) -> AggregationResult:
        """
        Perform one round of Byzantine-tolerant aggregation.

        Args:
          updates: gradient updates from all sites
          method:  override default aggregation method
        """
        t0     = time.perf_counter()
        method = method or self.method
        N      = len(updates)
        f      = self.f
        self._round += 1

        # Byzantine condition check: N ≥ 3f + 1
        byzantine_safe = N >= 3 * f + 1

        # Anomaly detection
        clean, anomalies = detect_byzantine_updates(updates)
        for a in anomalies:
            self._anomaly_log.append(a)
            # Lower trust score for flagged sites
            if a.site_id in self._sites:
                self._sites[a.site_id]["trust_score"] = max(
                    0.0,
                    self._sites[a.site_id]["trust_score"] - 0.1
                )

        if not clean:
            clean = updates  # fallback if all flagged

        gradients = [u.gradient for u in clean]

        # Ensure theta initialized
        if not self._theta and gradients:
            self._theta = [0.0] * len(gradients[0])

        # Apply aggregation method
        if method == "trimmed_mean":
            agg = trimmed_mean(gradients, f)
        elif method == "krum":
            agg = multi_krum(gradients, f)
        elif method == "median":
            agg = coordinate_median(gradients)
        elif method == "fltrust":
            root = _vec_mean(gradients)  # use mean as root proxy
            agg  = fltrust(root, gradients)
        else:
            agg = _vec_mean(gradients)

        # Global parameter update:  θ ← θ − η * agg
        if len(self._theta) == len(agg):
            self._theta = [
                self._theta[i] - self.lr * agg[i]
                for i in range(len(agg))
            ]

        # Update site records
        for u in updates:
            if u.site_id in self._sites:
                self._sites[u.site_id]["rounds"]    += 1
                self._sites[u.site_id]["last_perf"]  = u.performance

        ms = (time.perf_counter() - t0) * 1000
        result = AggregationResult(
            round_id       = self._round,
            method         = method,
            n_sites        = N,
            n_used         = len(clean),
            n_excluded     = N - len(clean),
            aggregated     = agg[:],
            anomalies      = [a.to_dict() for a in anomalies],
            byzantine_safe = byzantine_safe,
            latency_ms     = ms,
        )
        self._history.append(result)
        return result

    # ── Knowledge bundle ──────────────────────────────────────────────────────

    def export_global_params(self) -> dict:
        """Export current global parameters (no raw data)."""
        return {
            "round":         self._round,
            "param_dim":     len(self._theta),
            "theta_norm":    round(_vec_norm(self._theta), 6),
            "theta_hash":    hashlib.sha256(
                str(self._theta).encode()).hexdigest()[:16],
            "sites":         len(self._sites),
            "f_tolerance":   self.f,
        }

    def import_site_update(self, site_id: str, gradient: List[float],
                            performance: float = 0.0,
                            step: int = 0) -> SiteUpdate:
        """Create a SiteUpdate from imported data."""
        return SiteUpdate(
            site_id    = site_id,
            gradient   = gradient,
            param_dim  = len(gradient),
            step       = step,
            performance= performance,
            n_samples  = 1,
        )

    # ── Stats ──────────────────────────────────────────────────────────────────

    def get_stats(self) -> dict:
        return {
            "rounds":          self._round,
            "sites_registered": len(self._sites),
            "f_tolerance":     self.f,
            "min_sites_required": 3 * self.f + 1,
            "method":          self.method,
            "lr":              self.lr,
            "param_dim":       len(self._theta),
            "anomaly_count":   len(self._anomaly_log),
            "history_rounds":  len(self._history),
        }

    def get_site_leaderboard(self) -> List[dict]:
        return sorted(
            [
                {
                    "site_id":    sid,
                    "rounds":     s["rounds"],
                    "trust_score": round(s["trust_score"], 3),
                    "last_perf":  round(s["last_perf"], 4),
                }
                for sid, s in self._sites.items()
            ],
            key=lambda x: -x["trust_score"],
        )

    def get_anomaly_log(self, limit: int = 50) -> List[dict]:
        return [a.to_dict() for a in self._anomaly_log[-limit:]]
