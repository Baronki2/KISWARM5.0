"""
KISWARM v4.4 — Module 32: Self-Healing DAG Swarm
=================================================
Multi-node asyncio swarm with consensus, self-healing, immortality DAG path.

Architecture:
  SwarmAuditorNode  — individual swarm member; runs audit cycles, detects
                      and heals inconsistencies with peers via consensus
  PermanentAuditor  — singleton background agent; ticks every N seconds
  SwarmCoordinator  — manages N nodes, runs Byzantine-majority consensus,
                      provides the API surface for external control

Pipeline:  Mutation → SIL → Digital Thread → Audit → Consensus → Immortality

Consensus: simple majority vote (⌊N/2⌋ + 1) on DAG hash per pipeline step.
           If a node is outvoted, it adopts the majority snapshot.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from .swarm_auditor import (
    PIPELINES,
    AuditLedger,
    PipelineDAG,
    DAGIssues,
    log_audit,
    run_audit_cycle,
    run_pipeline_step,
    run_pfd_calculation,
    run_sil_band_check,
    repair_dag,
    load_pipeline_dag,
    save_pipeline_dag,
    populate_dummy_data,
    validate_dag_consistency,
)


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dag_hash(snapshot: Dict[str, Any]) -> str:
    """Stable hash of a pipeline snapshot for consensus comparison."""
    canonical = json.dumps(snapshot, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


# ─────────────────────────────────────────────────────────────────────────────
# SWARM AUDITOR NODE
# ─────────────────────────────────────────────────────────────────────────────

class SwarmAuditorNode:
    """
    A single swarm member.

    Each cycle:
      1. Runs all 6 pipeline steps locally (loads, validates, repairs, saves)
      2. Computes per-pipeline DAG hash
      3. Broadcasts hash to peers and collects their hashes (consensus round)
      4. If outvoted on any pipeline, adopts the majority snapshot from disk
         (all nodes share sentinel_data/ so "adopting" means re-loading)
      5. Stores its own snapshot for peers to compare against
    """

    def __init__(self, node_id: str):
        self.node_id:       str            = node_id
        self.last_snapshot: Dict[str, Any] = {}
        self.last_hashes:   Dict[str, str] = {}
        self._running:      bool           = False
        self._cycle_count:  int            = 0
        self._heals:        int            = 0
        self._errors:       int            = 0

    # ── Core cycle ────────────────────────────────────────────────────────────

    async def run_audit_cycle(
        self,
        swarm_peers: List["SwarmAuditorNode"],
        interval_seconds: int = 20,
    ) -> None:
        while self._running:
            try:
                await self._one_cycle(swarm_peers)
            except Exception as exc:
                self._errors += 1
                log_audit(f"[{self.node_id[:8]}] Error in audit cycle: {exc}",
                          "ERROR", self.node_id)
            await asyncio.sleep(interval_seconds)

    async def _one_cycle(self, swarm_peers: List["SwarmAuditorNode"]) -> None:
        self._cycle_count += 1
        dag_snapshot: Dict[str, Any] = {}

        # Step 1 — run all pipelines
        for pipeline in PIPELINES:
            result = run_pipeline_step(pipeline)
            if pipeline == "sil":
                result["pfd"]      = run_pfd_calculation(result)
                result["sil_band"] = run_sil_band_check(result)
            dag_snapshot[pipeline] = result

        # Step 2 — per-pipeline hash
        my_hashes = {p: _dag_hash(v) for p, v in dag_snapshot.items()}

        # Step 3 — consensus: compare with peers
        for pipeline in PIPELINES:
            peer_hashes = [
                p.last_hashes.get(pipeline)
                for p in swarm_peers
                if p.node_id != self.node_id and p.last_hashes.get(pipeline)
            ]
            if peer_hashes:
                majority = _majority_hash(peer_hashes, my_hashes[pipeline])
                if majority != my_hashes[pipeline]:
                    # Outvoted — self-heal by re-loading from shared storage
                    dag = load_pipeline_dag(pipeline)
                    dag, issues = repair_dag(dag)
                    save_pipeline_dag(dag)
                    self._heals += 1
                    log_audit(
                        f"[{self.node_id[:8]}] Consensus heal on '{pipeline}' "
                        f"(my={my_hashes[pipeline][:8]} majority={majority[:8]})",
                        "WARNING", self.node_id,
                    )

        # Step 4 — peer snapshot comparison (structural diff)
        for peer in swarm_peers:
            if peer.node_id == self.node_id or not peer.last_snapshot:
                continue
            diffs = _compare_snapshots(dag_snapshot, peer.last_snapshot)
            if diffs["missing_pipelines"] or diffs["missing_edges"]:
                log_audit(
                    f"[{self.node_id[:8]}] Structural diff with "
                    f"{peer.node_id[:8]}: {diffs}",
                    "WARNING", self.node_id,
                )
                self._heals += 1

        # Step 5 — update own state
        self.last_snapshot = dag_snapshot
        self.last_hashes   = my_hashes

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(
        self,
        swarm_peers: List["SwarmAuditorNode"],
        interval_seconds: int = 20,
    ) -> None:
        if not self._running:
            self._running = True
            asyncio.ensure_future(self.run_audit_cycle(swarm_peers, interval_seconds))
            log_audit(f"[{self.node_id[:8]}] SwarmAuditorNode started", "INFO", self.node_id)

    def stop(self) -> None:
        self._running = False
        log_audit(f"[{self.node_id[:8]}] SwarmAuditorNode stopped", "INFO", self.node_id)

    def status(self) -> Dict[str, Any]:
        return {
            "node_id":     self.node_id,
            "running":     self._running,
            "cycles":      self._cycle_count,
            "heals":       self._heals,
            "errors":      self._errors,
            "pipelines":   list(self.last_snapshot.keys()),
        }

    def force_cycle_sync(self) -> Dict[str, Any]:
        """Synchronous forced cycle (no peers, for API-triggered runs)."""
        snapshot: Dict[str, Any] = {}
        for pipeline in PIPELINES:
            result = run_pipeline_step(pipeline)
            if pipeline == "sil":
                result["pfd"]      = run_pfd_calculation(result)
                result["sil_band"] = run_sil_band_check(result)
            snapshot[pipeline] = result
        self.last_snapshot = snapshot
        self.last_hashes   = {p: _dag_hash(v) for p, v in snapshot.items()}
        self._cycle_count += 1
        log_audit(f"[{self.node_id[:8]}] Forced DAG cycle completed", "INFO", self.node_id)
        return snapshot


# ─────────────────────────────────────────────────────────────────────────────
# PERMANENT AUDITOR  (background singleton)
# ─────────────────────────────────────────────────────────────────────────────

class PermanentAuditor:
    """
    Standalone background agent — runs run_audit_cycle() every interval_seconds.
    Complements the swarm nodes; adds a single-agent fallback audit path.
    """

    def __init__(self, interval_seconds: int = 30):
        self.interval:     int  = interval_seconds
        self._running:     bool = False
        self._cycle_count: int  = 0
        self._errors:      int  = 0

    async def _audit_loop(self) -> None:
        while self._running:
            try:
                run_audit_cycle(source="permanent_auditor")
                self._cycle_count += 1
                log_audit(
                    f"PermanentAuditor cycle {self._cycle_count} complete",
                    "INFO", "permanent_auditor",
                )
            except Exception as exc:
                self._errors += 1
                log_audit(f"PermanentAuditor error: {exc}", "ERROR", "permanent_auditor")
            await asyncio.sleep(self.interval)

    def start(self) -> None:
        if not self._running:
            self._running = True
            asyncio.ensure_future(self._audit_loop())
            log_audit("PermanentAuditor started", "INFO", "permanent_auditor")

    def stop(self) -> None:
        self._running = False
        log_audit("PermanentAuditor stopped", "INFO", "permanent_auditor")

    def status(self) -> Dict[str, Any]:
        return {
            "running":      self._running,
            "cycles":       self._cycle_count,
            "errors":       self._errors,
            "interval_s":   self.interval,
        }


# ─────────────────────────────────────────────────────────────────────────────
# SWARM COORDINATOR  (manages N nodes + consensus)
# ─────────────────────────────────────────────────────────────────────────────

class SwarmCoordinator:
    """
    Manages a fleet of SwarmAuditorNodes and a PermanentAuditor.
    Provides:
      • start / stop the whole swarm
      • force a synchronous audit cycle across all nodes
      • global consensus view
      • per-node and aggregate statistics
    """

    def __init__(self, n_nodes: int = 3, interval_seconds: int = 20):
        self.nodes: List[SwarmAuditorNode] = [
            SwarmAuditorNode(node_id=str(uuid.uuid4())) for _ in range(n_nodes)
        ]
        self.permanent_auditor = PermanentAuditor(interval_seconds=interval_seconds)
        self._interval = interval_seconds
        self._started  = False

    # ── Swarm control ─────────────────────────────────────────────────────────

    def start(self) -> Dict[str, Any]:
        if not self._started:
            for node in self.nodes:
                node.start(self.nodes, self._interval)
            self.permanent_auditor.start()
            self._started = True
            log_audit(f"SwarmCoordinator started ({len(self.nodes)} nodes)", "INFO", "coordinator")
        return {"status": "started", "node_count": len(self.nodes)}

    def stop(self) -> Dict[str, Any]:
        for node in self.nodes:
            node.stop()
        self.permanent_auditor.stop()
        self._started = False
        log_audit("SwarmCoordinator stopped", "INFO", "coordinator")
        return {"status": "stopped"}

    def status(self) -> Dict[str, Any]:
        return {
            "swarm_running":      self._started,
            "node_count":         len(self.nodes),
            "nodes":              [n.status() for n in self.nodes],
            "permanent_auditor":  self.permanent_auditor.status(),
        }

    def force_cycle(self) -> Dict[str, Any]:
        """Synchronously run one audit cycle on every node."""
        results = []
        for node in self.nodes:
            snap = node.force_cycle_sync()
            results.append({"node_id": node.node_id, "snapshot": snap})
        return {"forced_cycle_results": results, "timestamp": _now()}

    def consensus_view(self) -> Dict[str, Any]:
        """
        For each pipeline, count how many nodes agree on the same DAG hash.
        Returns per-pipeline consensus status.
        """
        view: Dict[str, Any] = {}
        for pipeline in PIPELINES:
            hash_votes: Dict[str, int] = {}
            for node in self.nodes:
                h = node.last_hashes.get(pipeline, "no_snapshot")
                hash_votes[h] = hash_votes.get(h, 0) + 1
            majority_hash = max(hash_votes, key=hash_votes.__getitem__) if hash_votes else None
            majority_votes = hash_votes.get(majority_hash, 0) if majority_hash else 0
            quorum = len(self.nodes) // 2 + 1
            view[pipeline] = {
                "majority_hash":  majority_hash[:8] if majority_hash and majority_hash != "no_snapshot" else None,
                "votes":          hash_votes,
                "quorum_met":     majority_votes >= quorum,
                "quorum_needed":  quorum,
            }
        return {"consensus": view, "timestamp": _now()}

    def node_status(self, node_id: str) -> Optional[Dict[str, Any]]:
        for n in self.nodes:
            if n.node_id == node_id:
                return n.status()
        return None

    def aggregate_stats(self) -> Dict[str, Any]:
        total_cycles = sum(n._cycle_count for n in self.nodes)
        total_heals  = sum(n._heals      for n in self.nodes)
        total_errors = sum(n._errors     for n in self.nodes)
        return {
            "total_cycles":  total_cycles,
            "total_heals":   total_heals,
            "total_errors":  total_errors,
            "node_count":    len(self.nodes),
            "swarm_running": self._started,
        }


# ─────────────────────────────────────────────────────────────────────────────
# CONSENSUS HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _majority_hash(peer_hashes: List[Optional[str]], my_hash: str) -> str:
    """Return the most-voted hash (including own vote)."""
    all_votes = [h for h in peer_hashes if h] + [my_hash]
    tally: Dict[str, int] = {}
    for h in all_votes:
        tally[h] = tally.get(h, 0) + 1
    return max(tally, key=tally.__getitem__)


def _compare_snapshots(
    local: Dict[str, Any],
    peer:  Dict[str, Any],
) -> Dict[str, Any]:
    """Structural diff between two pipeline snapshots."""
    missing_pipelines = [p for p in local if p not in peer]
    missing_edges: List[str] = []
    for pipeline, data in local.items():
        local_edges = set(data.get("edges", []))
        peer_edges  = set(peer.get(pipeline, {}).get("edges", []))
        missing_edges.extend(list(local_edges - peer_edges))
    return {"missing_pipelines": missing_pipelines, "missing_edges": missing_edges}
