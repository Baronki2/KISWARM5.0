"""
KISWARM v3.0 — MODULE D: FEDERATED ADAPTIVE MESH PROTOCOL
===========================================================
Decentralized, Byzantine-fault-tolerant parameter aggregation across
swarm nodes. Each node trains locally, shares only parameter deltas
with hardware attestation, and participates in trust-weighted global
aggregation.

Protocol:
  Local Learning:
    • Each node trains locally
    • Maintains trust score
    • Logs stability metrics
    • Never shares raw telemetry

  Parameter Sharing (what nodes send):
    • Parameter delta (Δθ)
    • Performance delta (ΔJ)
    • Stability certificate
    • Hardware attestation signature

  Trust-Weighted Aggregation:
    θ_global = Σ w_i · θ_i
    w_i = f(TrustScore_i, StabilityMargin_i, Uptime_i)
    Compromised node weight → near zero

  Byzantine Protection:
    • Median aggregation (not mean)
    • Krum-style outlier rejection
    • Signature verification
    • Quorum validation before deployment

  Partition Handling:
    • Freeze global updates during network partition
    • Continue bounded local learning
    • Resync only after trust handshake
    • No auto-expansion, no authority escalation

In KISWARM context:
  Nodes = Ollama model instances (different machines or processes)
  Each node learns its own fuzzy/RL parameters
  Mesh aggregates these into global policy improvements
  Byzantine resistance prevents poisoned models from corrupting the swarm

Author: KISWARM Project (Baron Marco Paolo Ialongo)
Version: 3.0
"""

import hashlib
import json
import logging
import math
import os
import random
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Optional

logger = logging.getLogger("sentinel.federated_mesh")

KISWARM_HOME = os.environ.get("KISWARM_HOME", os.path.expanduser("~"))
KISWARM_DIR  = os.path.join(KISWARM_HOME, "KISWARM")
MESH_STORE   = os.path.join(KISWARM_DIR, "federated_mesh.json")

# Quorum: fraction of nodes that must agree before global deployment
QUORUM_THRESHOLD = 0.67   # 2/3 supermajority


# ── Hardware Attestation ──────────────────────────────────────────────────────

def compute_attestation(
    node_id:         str,
    delta:           list[float],
    stability_cert:  float,
    timestamp:       float,
) -> str:
    """
    Compute hardware attestation signature for a node's parameter share.
    In production: replace with TPM-based attestation or ECDSA.
    Here: deterministic HMAC-style hash over payload.
    """
    payload = json.dumps({
        "node_id":       node_id,
        "delta_hash":    hashlib.sha256(str(delta).encode()).hexdigest()[:16],
        "stability":     round(stability_cert, 4),
        "timestamp":     round(timestamp, 2),
    }, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def verify_attestation(
    node_id:        str,
    delta:          list[float],
    stability_cert: float,
    timestamp:      float,
    signature:      str,
) -> bool:
    """Verify a node's attestation signature."""
    expected = compute_attestation(node_id, delta, stability_cert, timestamp)
    return expected == signature


# ── Data Structures ───────────────────────────────────────────────────────────

@dataclass
class NodeShare:
    """Parameter share submitted by a single mesh node."""
    node_id:         str
    param_delta:     list[float]    # Δθ = θ_local - θ_global
    perf_delta:      float          # ΔJ = J_local - J_global (positive = improvement)
    stability_cert:  float          # stability margin (0.0–1.0)
    uptime:          float          # node uptime ratio (0.0–1.0)
    timestamp:       float          = field(default_factory=time.time)
    attestation:     str            = ""

    def sign(self):
        """Compute and attach attestation signature."""
        self.attestation = compute_attestation(
            self.node_id, self.param_delta, self.stability_cert, self.timestamp
        )

    def verify(self) -> bool:
        """Verify this share's attestation."""
        return verify_attestation(
            self.node_id, self.param_delta, self.stability_cert,
            self.timestamp, self.attestation,
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class NodeRecord:
    """Persistent record of a node's trust and performance history."""
    node_id:          str
    trust_score:      float = 0.8
    stability_margin: float = 0.8
    uptime:           float = 1.0
    total_shares:     int   = 0
    rejected_shares:  int   = 0
    last_seen:        float = field(default_factory=time.time)
    active:           bool  = True

    @property
    def weight(self) -> float:
        """
        w_i = f(TrustScore_i, StabilityMargin_i, Uptime_i)
        Geometric mean of three factors, then floored at near-zero
        if trust is very low.
        """
        if self.trust_score < 0.1:
            return 1e-6   # compromised node → near zero weight
        w = (self.trust_score * self.stability_margin * self.uptime) ** (1/3)
        return round(max(0.0, min(1.0, w)), 4)

    def penalize(self, reason: str = ""):
        """Reduce trust score after a rejected share."""
        self.trust_score  = max(0.0, self.trust_score * 0.85)
        self.rejected_shares += 1
        logger.warning("Node %s penalized (trust→%.2f) reason=%s",
                       self.node_id, self.trust_score, reason)

    def reward(self, delta: float = 0.02):
        """Increase trust score after a good share."""
        self.trust_score = min(1.0, self.trust_score + delta)

    def to_dict(self) -> dict:
        return {**asdict(self), "weight": self.weight}


@dataclass
class AggregationReport:
    """Result of one global aggregation round."""
    round_id:         int
    participating:    int
    rejected:         int
    quorum_reached:   bool
    global_delta:     list[float]
    reason_rejected:  list[str]
    timestamp:        str = field(default_factory=lambda: datetime.now().isoformat())


# ── Byzantine-Robust Aggregation ─────────────────────────────────────────────

class ByzantineAggregator:
    """
    Trust-weighted aggregation with Byzantine protection.

    Protection layers:
      1. Signature verification (reject unsigned/invalid)
      2. Krum-style outlier rejection (reject statistical outliers)
      3. Coordinate-wise median (not mean — robust to poisoning)
      4. Quorum validation (≥67% must participate and pass)
    """

    def __init__(self, krum_f: int = 1):
        """
        krum_f: number of Byzantine nodes to tolerate in Krum selection.
        For n nodes, Krum selects the n - f - 2 most central vectors.
        """
        self._krum_f = krum_f

    def _verify_shares(
        self,
        shares:  list[NodeShare],
        nodes:   dict[str, NodeRecord],
    ) -> tuple[list[NodeShare], list[str]]:
        """Layer 1: Signature verification."""
        valid, reasons = [], []
        for share in shares:
            if not share.verify():
                reasons.append(f"{share.node_id}:invalid_attestation")
                if share.node_id in nodes:
                    nodes[share.node_id].penalize("invalid_attestation")
                continue
            valid.append(share)
        return valid, reasons

    @staticmethod
    def _euclidean_distance(a: list[float], b: list[float]) -> float:
        return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))

    def _krum_filter(
        self,
        shares:  list[NodeShare],
        nodes:   dict[str, NodeRecord],
    ) -> tuple[list[NodeShare], list[str]]:
        """
        Layer 2: Krum-style outlier rejection.
        For each vector, compute sum of distances to n-f-2 nearest neighbors.
        Reject those with the highest distance sums (statistical outliers).
        """
        n = len(shares)
        if n <= 2:
            return shares, []

        f        = min(self._krum_f, (n - 2) // 2)
        keep_k   = max(1, n - f)          # Multi-Krum: keep n-f nodes, not 1

        # Compute pairwise distances
        scores = []
        for i, si in enumerate(shares):
            dists = sorted(
                self._euclidean_distance(si.param_delta, sj.param_delta)
                for j, sj in enumerate(shares) if i != j
            )
            # Sum of n-f-2 nearest
            scores.append((sum(dists[:keep_k]), i))

        scores.sort()
        keep_indices = {idx for _, idx in scores[:keep_k]}
        rejected     = [shares[i] for i in range(n) if i not in keep_indices]
        accepted     = [shares[i] for i in keep_indices]

        reasons = []
        for share in rejected:
            reasons.append(f"{share.node_id}:krum_outlier")
            if share.node_id in nodes:
                nodes[share.node_id].penalize("krum_outlier")

        return accepted, reasons

    def _coordinate_median(
        self,
        shares:  list[NodeShare],
        weights: list[float],
    ) -> list[float]:
        """
        Layer 3: Coordinate-wise weighted median.
        For each parameter dimension, take the weighted median (not mean).
        Robust to Byzantine nodes that inject extreme values.
        """
        if not shares:
            return []

        dim = len(shares[0].param_delta)
        total_w = sum(weights)
        if total_w <= 0:
            weights = [1.0] * len(shares)
            total_w = len(shares)

        result = []
        for d in range(dim):
            # Collect (value, weight) pairs for this dimension
            vals = sorted(
                (shares[i].param_delta[d], weights[i])
                for i in range(len(shares))
                if d < len(shares[i].param_delta)
            )
            # Weighted median: find point where cumulative weight crosses 50%
            cum = 0.0
            median_val = vals[0][0] if vals else 0.0
            for val, w in vals:
                cum += w / total_w
                if cum >= 0.5:
                    median_val = val
                    break
            result.append(median_val)

        return result

    def aggregate(
        self,
        shares:  list[NodeShare],
        nodes:   dict[str, NodeRecord],
        round_id: int = 0,
    ) -> AggregationReport:
        """
        Full Byzantine-robust aggregation pipeline.
        Returns AggregationReport with the global parameter delta.
        """
        reasons_all = []

        # Layer 1: Signature verification
        valid, reasons = self._verify_shares(shares, nodes)
        reasons_all.extend(reasons)

        if not valid:
            return AggregationReport(
                round_id=round_id, participating=0,
                rejected=len(shares), quorum_reached=False,
                global_delta=[], reason_rejected=reasons_all,
            )

        # Layer 2: Krum filter
        valid, reasons = self._krum_filter(valid, nodes)
        reasons_all.extend(reasons)

        # Quorum check: absolute minimum + 2/3 ratio
        MIN_NODES = 2
        quorum = len(valid) >= MIN_NODES and (
            len(valid) / max(len(shares), 1) >= QUORUM_THRESHOLD
        )

        if not quorum or not valid:
            return AggregationReport(
                round_id=round_id, participating=len(valid),
                rejected=len(shares) - len(valid), quorum_reached=False,
                global_delta=[], reason_rejected=reasons_all + ["quorum_not_reached"],
            )

        # Layer 3: Compute trust weights
        weights = []
        for share in valid:
            rec = nodes.get(share.node_id)
            if rec:
                weights.append(rec.weight)
            else:
                weights.append(0.5)   # unknown node gets default weight

        # Layer 4: Coordinate-wise median
        global_delta = self._coordinate_median(valid, weights)

        # Reward participating nodes
        for share in valid:
            if share.node_id in nodes:
                nodes[share.node_id].reward(0.01)
                nodes[share.node_id].total_shares += 1

        logger.info(
            "Aggregation round %d: %d/%d accepted | quorum=%s | delta_norm=%.4f",
            round_id, len(valid), len(shares), quorum,
            math.sqrt(sum(d*d for d in global_delta)) if global_delta else 0.0,
        )

        return AggregationReport(
            round_id=round_id,
            participating=len(valid),
            rejected=len(shares) - len(valid),
            quorum_reached=True,
            global_delta=global_delta,
            reason_rejected=reasons_all,
        )


# ── Partition Handler ─────────────────────────────────────────────────────────

class PartitionHandler:
    """
    Handles network partition scenarios:
      • Detects partition (no shares received within timeout)
      • Freezes global updates during partition
      • Allows bounded local learning to continue
      • Enforces trust handshake before resync
      • No auto-expansion, no authority escalation
    """

    def __init__(self, timeout_seconds: float = 300.0, max_local_drift: float = 0.10):
        self._timeout        = timeout_seconds
        self._max_drift      = max_local_drift  # maximum local param drift allowed
        self._last_global    = time.time()
        self._partitioned    = False
        self._local_drift    = 0.0
        self._partition_log: list[dict] = []

    def record_global_update(self):
        """Called after each successful global aggregation round."""
        self._last_global  = time.time()
        self._partitioned  = False
        self._local_drift  = 0.0

    def check_partition(self) -> bool:
        """Returns True if the node appears to be in a network partition."""
        elapsed = time.time() - self._last_global
        if elapsed > self._timeout and not self._partitioned:
            self._partitioned = True
            self._partition_log.append({
                "event":   "partition_detected",
                "elapsed": elapsed,
                "timestamp": datetime.now().isoformat(),
            })
            logger.warning("Network partition detected (no global update for %.0fs)", elapsed)
        return self._partitioned

    def allow_local_update(self, proposed_drift: float) -> tuple[bool, str]:
        """
        During partition: allow bounded local learning.
        Rejects local updates that would exceed max_local_drift.
        """
        if not self._partitioned:
            return True, "ok"

        new_drift = self._local_drift + abs(proposed_drift)
        if new_drift > self._max_drift:
            return False, (
                f"partition_drift_limit:{new_drift:.3f}>{self._max_drift:.3f}"
            )

        self._local_drift = new_drift
        return True, "bounded_local"

    def trust_handshake(self, node_id: str, attestation: str) -> bool:
        """
        Verify trust before allowing resync after partition.
        Returns True if handshake passes.
        In production: would verify against certificate authority.
        """
        # Simplified: verify attestation is a valid hash
        if len(attestation) == 64 and all(c in "0123456789abcdef" for c in attestation):
            self._partition_log.append({
                "event":     "resync_handshake",
                "node_id":   node_id,
                "timestamp": datetime.now().isoformat(),
            })
            logger.info("Trust handshake verified for node %s — resync allowed", node_id)
            return True

        logger.warning("Trust handshake FAILED for node %s", node_id)
        return False

    @property
    def is_partitioned(self) -> bool:
        return self._partitioned

    @property
    def partition_log(self) -> list[dict]:
        return list(self._partition_log)


# ── Federated Mesh Node ───────────────────────────────────────────────────────

class FederatedMeshNode:
    """
    A single participant in the Federated Adaptive Mesh.

    Each node:
      1. Trains its local policy parameters
      2. Produces a NodeShare (delta + attestation)
      3. Participates in aggregation rounds
      4. Applies the global update to its local parameters

    Usage:
        node = FederatedMeshNode("ollama_node_01")
        node.update_local_params(new_params, stability=0.85)
        share = node.create_share(global_params)
        # ... send share to coordinator ...
        node.apply_global(aggregation_report.global_delta)
    """

    def __init__(self, node_id: str, param_dim: int = 8):
        self.node_id    = node_id
        self._dim       = param_dim
        self._params    = [0.5] * param_dim   # local parameters
        self._stability = 0.8
        self._uptime    = 1.0
        self._partition = PartitionHandler()

    def update_local_params(
        self,
        new_params:  list[float],
        stability:   float,
        uptime:      float = None,
    ):
        """Update this node's local parameters after local training."""
        proposed_drift = math.sqrt(
            sum((a - b) ** 2 for a, b in zip(new_params, self._params))
        )

        allowed, reason = self._partition.allow_local_update(proposed_drift)
        if not allowed:
            logger.warning("Node %s: local update blocked (%s)", self.node_id, reason)
            return False

        self._params    = [max(0.0, min(1.0, p)) for p in new_params]
        self._stability = max(0.0, min(1.0, stability))
        if uptime is not None:
            self._uptime = max(0.0, min(1.0, uptime))
        return True

    def create_share(self, global_params: list[float]) -> NodeShare:
        """
        Create a signed NodeShare representing this node's contribution.
        delta = local_params - global_params
        """
        delta = [
            self._params[i] - global_params[i]
            for i in range(min(len(self._params), len(global_params)))
        ]

        share = NodeShare(
            node_id=self.node_id,
            param_delta=delta,
            perf_delta=self._stability - 0.5,   # relative to baseline 0.5
            stability_cert=self._stability,
            uptime=self._uptime,
            timestamp=time.time(),
        )
        share.sign()
        return share

    def apply_global(self, global_delta: list[float]):
        """Apply the aggregated global delta to local parameters."""
        if not global_delta:
            return
        for i in range(min(len(self._params), len(global_delta))):
            self._params[i] = max(0.0, min(1.0, self._params[i] + global_delta[i]))
        self._partition.record_global_update()
        logger.info("Node %s: applied global delta (norm=%.4f)",
                    self.node_id,
                    math.sqrt(sum(d*d for d in global_delta)))

    @property
    def params(self) -> list[float]:
        return list(self._params)

    @property
    def is_partitioned(self) -> bool:
        return self._partition.check_partition()


# ── Federated Mesh Coordinator ────────────────────────────────────────────────

class FederatedMeshCoordinator:
    """
    Central coordinator for the Federated Adaptive Mesh.

    Manages:
      • Node registry with trust scores
      • Aggregation rounds
      • Byzantine protection
      • Global parameter state
      • Partition detection

    The coordinator never stores raw telemetry — only parameter deltas.

    Usage:
        coordinator = FederatedMeshCoordinator(param_dim=8)
        coordinator.register_node("node_01")
        coordinator.register_node("node_02")

        # Each round:
        shares = [node.create_share(coordinator.global_params) for node in nodes]
        report = coordinator.aggregate_round(shares)
        if report.quorum_reached:
            for node in nodes:
                node.apply_global(report.global_delta)
    """

    def __init__(self, param_dim: int = 8, store_path: str = MESH_STORE):
        self._dim         = param_dim
        self._store       = store_path
        self._nodes:      dict[str, NodeRecord] = {}
        self._global:     list[float] = [0.5] * param_dim
        self._round_id    = 0
        self._aggregator  = ByzantineAggregator(krum_f=1)
        self._history:    list[dict] = []
        self._load()

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load(self):
        if os.path.exists(self._store):
            try:
                with open(self._store) as f:
                    raw = json.load(f)
                for nid, ndata in raw.get("nodes", {}).items():
                    self._nodes[nid] = NodeRecord(**{
                        k: v for k, v in ndata.items()
                        if k != "weight"
                    })
                self._global   = raw.get("global_params", self._global)
                self._round_id = raw.get("round_id", 0)
                self._history  = raw.get("history", [])
                logger.info(
                    "Mesh loaded: %d nodes | round=%d",
                    len(self._nodes), self._round_id,
                )
            except (json.JSONDecodeError, TypeError, OSError) as exc:
                logger.warning("Mesh load failed: %s", exc)

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self._store), exist_ok=True)
            with open(self._store, "w") as f:
                json.dump({
                    "nodes":         {nid: n.to_dict() for nid, n in self._nodes.items()},
                    "global_params": self._global,
                    "round_id":      self._round_id,
                    "history":       self._history[-50:],
                }, f, indent=2)
        except OSError as exc:
            logger.error("Mesh save failed: %s", exc)

    # ── Node Management ───────────────────────────────────────────────────────

    def register_node(self, node_id: str, initial_trust: float = 0.8):
        """Register a new node in the mesh."""
        if node_id not in self._nodes:
            self._nodes[node_id] = NodeRecord(
                node_id=node_id,
                trust_score=initial_trust,
            )
            logger.info("Mesh: registered node %s (trust=%.2f)", node_id, initial_trust)
            self._save()

    def set_node_trust(self, node_id: str, trust: float):
        """Manually set trust score for a node (e.g., after audit)."""
        if node_id in self._nodes:
            self._nodes[node_id].trust_score = max(0.0, min(1.0, trust))
            self._save()

    # ── Aggregation ───────────────────────────────────────────────────────────

    def aggregate_round(self, shares: list[NodeShare]) -> AggregationReport:
        """
        Run one full aggregation round with Byzantine protection.
        If accepted, updates global parameters.
        """
        self._round_id += 1

        report = self._aggregator.aggregate(shares, self._nodes, self._round_id)

        if report.quorum_reached and report.global_delta:
            # Apply global delta
            for i in range(min(self._dim, len(report.global_delta))):
                self._global[i] = max(
                    0.0, min(1.0, self._global[i] + report.global_delta[i] * 0.1)
                )   # 0.1 = global learning rate (conservative)

            self._history.append({
                "round":         self._round_id,
                "participating": report.participating,
                "rejected":      report.rejected,
                "accepted":      True,
                "timestamp":     report.timestamp,
            })

        self._save()
        return report

    @property
    def global_params(self) -> list[float]:
        return list(self._global)

    def node_leaderboard(self) -> list[dict]:
        """Return nodes ranked by trust weight."""
        return sorted(
            [n.to_dict() for n in self._nodes.values()],
            key=lambda x: x["weight"],
            reverse=True,
        )

    def get_stats(self) -> dict:
        node_list = list(self._nodes.values())
        avg_trust = (sum(n.trust_score for n in node_list) / len(node_list)
                     if node_list else 0.0)
        return {
            "round_id":          self._round_id,
            "registered_nodes":  len(self._nodes),
            "global_params":     self._global,
            "avg_trust":         round(avg_trust, 3),
            "history_entries":   len(self._history),
            "quorum_threshold":  QUORUM_THRESHOLD,
        }
