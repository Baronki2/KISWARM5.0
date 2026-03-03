"""
KISWARM v4.4 — Module 31: Swarm Auditor Core
=============================================
Permanent self-healing auditor for all 6 KISWARM pipelines.

Pipeline flow:  Mutation → SIL → Digital Thread → Audit → Consensus → Immortality

Features:
  • DAG structural validation via networkx (cycle detection, dangling edges)
  • IEC 61508 PFD / SIL-band recalculation on every audit cycle
  • Append-only SHA-256 chained audit ledger (tamper-evident)
  • Auto-repair: removes dangling edges, breaks cycles, patches missing Δλd
  • populate_dummy_data() for full simulation without real hardware
  • Zero external API calls — fully air-gapped
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import networkx as nx

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────

DATA_DIR  = os.path.join(os.path.dirname(__file__), "../../sentinel_data")
AUDIT_LOG = os.path.join(DATA_DIR, "audit_ledger.jsonl")   # JSON Lines — append-only

PIPELINES: List[str] = [
    "mutation",
    "sil",
    "digital_thread",
    "audit",
    "consensus",
    "immortality",
]

# IEC 61508-1 Table 2 PFD bands
SIL_PFD_RANGES: Dict[int, Tuple[float, float]] = {
    1: (1e-2, 1e-1),
    2: (1e-3, 1e-2),
    3: (1e-4, 1e-3),
    4: (1e-5, 1e-4),
}

os.makedirs(DATA_DIR, exist_ok=True)


# ─────────────────────────────────────────────────────────────────────────────
# DATA MODELS
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class DAGNode:
    id: str
    node_type: str
    data: Dict[str, Any]
    timestamp: str = field(default_factory=lambda: _now())

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "node_type": self.node_type,
                "data": self.data, "timestamp": self.timestamp}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "DAGNode":
        return cls(id=d["id"], node_type=d.get("node_type", d.get("type", "unknown")),
                   data=d.get("data", {}), timestamp=d.get("timestamp", _now()))


@dataclass
class DAGEdge:
    from_node: str
    to_node: str
    edge_type: str

    def to_dict(self) -> Dict[str, Any]:
        return {"from_node": self.from_node, "to_node": self.to_node, "edge_type": self.edge_type}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "DAGEdge":
        return cls(from_node=d["from_node"], to_node=d["to_node"],
                   edge_type=d.get("edge_type", d.get("type", "unknown")))


@dataclass
class PipelineDAG:
    pipeline: str
    nodes: List[DAGNode] = field(default_factory=list)
    edges: List[DAGEdge] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"pipeline": self.pipeline,
                "nodes": [n.to_dict() for n in self.nodes],
                "edges": [e.to_dict() for e in self.edges]}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "PipelineDAG":
        nodes = [DAGNode.from_dict(n) for n in d.get("nodes", [])]
        edges = [DAGEdge.from_dict(e) for e in d.get("edges", [])]
        return cls(pipeline=d.get("pipeline", "unknown"), nodes=nodes, edges=edges)

    def node_ids(self) -> set:
        return {n.id for n in self.nodes}


# ─────────────────────────────────────────────────────────────────────────────
# APPEND-ONLY AUDIT LEDGER  (SHA-256 chained)
# ─────────────────────────────────────────────────────────────────────────────

class AuditLedger:
    """
    Append-only audit log — every entry chains to previous via SHA-256.
    Written as JSON Lines so the file can be streamed without full parse.
    """

    def __init__(self, path: str = AUDIT_LOG):
        self._path = path
        self._prev_hash = "0" * 64
        # Fast-forward to last known hash on startup
        if os.path.exists(path):
            with open(path, "r") as f:
                for line in f:
                    try:
                        entry = json.loads(line)
                        self._prev_hash = entry.get("hash", self._prev_hash)
                    except json.JSONDecodeError:
                        pass

    def append(self, message: str, level: str = "INFO", source: str = "auditor") -> str:
        entry: Dict[str, Any] = {
            "timestamp": _now(),
            "level":     level,
            "source":    source,
            "message":   message,
            "prev_hash": self._prev_hash,
        }
        raw = json.dumps(entry, sort_keys=True).encode()
        entry["hash"] = hashlib.sha256(raw).hexdigest()
        self._prev_hash = entry["hash"]
        with open(self._path, "a") as f:
            f.write(json.dumps(entry) + "\n")
        return entry["hash"]

    def tail(self, n: int = 100) -> List[Dict[str, Any]]:
        if not os.path.exists(self._path):
            return []
        entries: List[Dict[str, Any]] = []
        with open(self._path, "r") as f:
            for line in f:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return entries[-n:]

    def verify_integrity(self) -> Tuple[bool, int]:
        """Returns (intact, entries_checked)."""
        if not os.path.exists(self._path):
            return True, 0
        prev = "0" * 64
        count = 0
        with open(self._path, "r") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                    check = {k: v for k, v in entry.items() if k != "hash"}
                    raw = json.dumps(check, sort_keys=True).encode()
                    expected = hashlib.sha256(raw).hexdigest()
                    if entry.get("hash") != expected:
                        return False, count
                    if entry.get("prev_hash") != prev:
                        return False, count
                    prev = entry["hash"]
                    count += 1
                except (json.JSONDecodeError, KeyError):
                    pass
        return True, count

    def entry_count(self) -> int:
        if not os.path.exists(self._path):
            return 0
        count = 0
        with open(self._path, "r") as f:
            for line in f:
                if line.strip():
                    count += 1
        return count


# Singleton ledger shared across the module
_ledger = AuditLedger()


def log_audit(message: str, level: str = "INFO", source: str = "auditor") -> str:
    return _ledger.append(message, level=level, source=source)


# ─────────────────────────────────────────────────────────────────────────────
# PERSISTENCE
# ─────────────────────────────────────────────────────────────────────────────

def _pipeline_path(pipeline: str) -> str:
    return os.path.join(DATA_DIR, f"{pipeline}.json")


def load_pipeline_dag(pipeline: str) -> PipelineDAG:
    path = _pipeline_path(pipeline)
    if os.path.exists(path):
        try:
            with open(path) as f:
                return PipelineDAG.from_dict(json.load(f))
        except Exception as e:
            log_audit(f"Load error [{pipeline}]: {e}", "ERROR")
    return PipelineDAG(pipeline=pipeline)


def save_pipeline_dag(dag: PipelineDAG) -> None:
    with open(_pipeline_path(dag.pipeline), "w") as f:
        json.dump(dag.to_dict(), f, indent=2)


# ─────────────────────────────────────────────────────────────────────────────
# IEC 61508 PFD / SIL CALCULATIONS
# ─────────────────────────────────────────────────────────────────────────────

def run_pfd_calculation(node_data: Dict[str, Any]) -> float:
    """
    IEC 61508 PFD for 1oo1 architecture:
        PFD_avg = λ_d × T_I / 2
    where λ_d = dangerous failure rate (1/h), T_I = proof-test interval (h).
    Falls back to safe defaults if data missing.
    """
    lambda_d = float(node_data.get("lambda_d", node_data.get("Δλd", 1e-6)))
    t_i      = float(node_data.get("test_interval_h", 8760))   # default 1 year
    arch     = node_data.get("architecture", "1oo1")

    if arch == "1oo2":
        # PFD_1oo2 ≈ (λ_d × T_I)² / 3
        pfd = (lambda_d * t_i) ** 2 / 3.0
    elif arch == "2oo3":
        pfd = 3 * (lambda_d * t_i / 2) ** 2 - 2 * (lambda_d * t_i / 2) ** 3
    else:  # 1oo1
        pfd = lambda_d * t_i / 2.0

    return round(max(pfd, 1e-10), 12)


def run_sil_band_check(node_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Determine SIL band from PFD value.
    Returns {"sil": int, "pfd": float, "compliant": bool, "target_sil": int}
    """
    pfd = run_pfd_calculation(node_data)
    target = int(node_data.get("target_sil", 2))

    achieved_sil = 0
    for sil, (lo, hi) in sorted(SIL_PFD_RANGES.items(), reverse=True):
        if lo <= pfd < hi:
            achieved_sil = sil
            break

    return {
        "pfd":        pfd,
        "sil":        achieved_sil,
        "target_sil": target,
        "compliant":  achieved_sil >= target,
        "band":       f"SIL {achieved_sil}" if achieved_sil else "Below SIL 1",
    }


# ─────────────────────────────────────────────────────────────────────────────
# DAG VALIDATION & AUTO-REPAIR
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class DAGIssues:
    dangling_edges:   List[DAGEdge]   = field(default_factory=list)
    cycles_broken:    int             = 0
    sil_nodes_patched: int            = 0
    missing_pipeline_steps: List[str] = field(default_factory=list)

    def has_issues(self) -> bool:
        return bool(self.dangling_edges or self.cycles_broken or
                    self.sil_nodes_patched or self.missing_pipeline_steps)

    def summary(self) -> str:
        parts = []
        if self.dangling_edges:
            parts.append(f"{len(self.dangling_edges)} dangling edge(s)")
        if self.cycles_broken:
            parts.append(f"{self.cycles_broken} cycle(s) broken")
        if self.sil_nodes_patched:
            parts.append(f"{self.sil_nodes_patched} SIL node(s) patched")
        if self.missing_pipeline_steps:
            parts.append(f"missing pipelines: {self.missing_pipeline_steps}")
        return "; ".join(parts) if parts else "clean"


def validate_dag_consistency(snapshot: Dict[str, PipelineDAG]) -> Dict[str, DAGIssues]:
    """
    Validate a full swarm snapshot (all 6 pipelines).
    Returns per-pipeline issues dict.
    """
    issues: Dict[str, DAGIssues] = {}
    present = set(snapshot.keys())
    missing_global = [p for p in PIPELINES if p not in present]

    for pipeline, dag in snapshot.items():
        pi = DAGIssues()
        if missing_global:
            pi.missing_pipeline_steps = missing_global

        node_ids = dag.node_ids()
        G = nx.DiGraph()
        for nid in node_ids:
            G.add_node(nid)

        for edge in dag.edges:
            if edge.from_node not in node_ids or edge.to_node not in node_ids:
                pi.dangling_edges.append(edge)
            else:
                G.add_edge(edge.from_node, edge.to_node)

        # Cycle detection
        try:
            list(nx.find_cycle(G))
            pi.cycles_broken += 1
        except nx.NetworkXNoCycle:
            pass

        # SIL node completeness
        for node in dag.nodes:
            if "sil" in node.node_type.lower() and "lambda_d" not in node.data:
                pi.sil_nodes_patched += 1

        if pi.has_issues():
            issues[pipeline] = pi

    return issues


def repair_dag(dag: PipelineDAG) -> Tuple[PipelineDAG, DAGIssues]:
    """
    Auto-repair a single pipeline DAG.
    Returns (repaired_dag, issues_found).
    """
    issues = DAGIssues()
    node_ids = dag.node_ids()
    G = nx.DiGraph()
    for nid in node_ids:
        G.add_node(nid)

    # Remove dangling edges
    clean_edges: List[DAGEdge] = []
    for edge in dag.edges:
        if edge.from_node in node_ids and edge.to_node in node_ids:
            clean_edges.append(edge)
            G.add_edge(edge.from_node, edge.to_node)
        else:
            issues.dangling_edges.append(edge)

    # Break cycles — remove the last edge in each cycle
    while True:
        try:
            cycle = nx.find_cycle(G)
            u, v = cycle[-1][0], cycle[-1][1]
            clean_edges = [e for e in clean_edges
                           if not (e.from_node == u and e.to_node == v)]
            G.remove_edge(u, v)
            issues.cycles_broken += 1
        except nx.NetworkXNoCycle:
            break

    dag.edges = clean_edges

    # Patch SIL nodes
    for node in dag.nodes:
        if "sil" in node.node_type.lower() and "lambda_d" not in node.data:
            node.data["lambda_d"] = 1e-6
            node.data["test_interval_h"] = 8760
            node.data["architecture"] = "1oo1"
            issues.sil_nodes_patched += 1

    return dag, issues


# ─────────────────────────────────────────────────────────────────────────────
# PIPELINE STEP RUNNER
# ─────────────────────────────────────────────────────────────────────────────

def run_pipeline_step(pipeline: str) -> Dict[str, Any]:
    """
    Load, validate, repair, and save a single pipeline DAG.
    Returns a rich result dict including SIL info for "sil" pipeline.
    """
    dag = load_pipeline_dag(pipeline)
    dag, issues = repair_dag(dag)
    save_pipeline_dag(dag)

    result: Dict[str, Any] = {
        "pipeline":    pipeline,
        "node_count":  len(dag.nodes),
        "edge_count":  len(dag.edges),
        "issues":      issues.summary(),
        "repaired":    issues.has_issues(),
        "timestamp":   _now(),
        "edges":       [e.edge_type for e in dag.edges],
        "nodes":       [n.node_type for n in dag.nodes],
    }

    if pipeline == "sil" and dag.nodes:
        # Run SIL/PFD on each SIL node; aggregate worst-case
        worst_pfd = 0.0
        for node in dag.nodes:
            if "sil" in node.node_type.lower() or "lambda_d" in node.data:
                pfd = run_pfd_calculation(node.data)
                worst_pfd = max(worst_pfd, pfd)
        if worst_pfd == 0:
            worst_pfd = 5e-4   # default if no SIL nodes
        result["pfd"]       = worst_pfd
        result["sil_band"]  = run_sil_band_check({"lambda_d": worst_pfd / 4380})
        result["lambda_d"]  = worst_pfd / 4380

    if pipeline == "immortality":
        result["ledger_entries"] = _ledger.entry_count()
        intact, checked = _ledger.verify_integrity()
        result["ledger_intact"]  = intact
        result["ledger_checked"] = checked

    if pipeline == "consensus":
        result["quorum_size"] = 3   # updated by swarm layer
        result["votes"]       = {}

    return result


# ─────────────────────────────────────────────────────────────────────────────
# FULL AUDIT CYCLE
# ─────────────────────────────────────────────────────────────────────────────

def run_audit_cycle(source: str = "auditor") -> Dict[str, Any]:
    """
    Run all 6 pipelines, validate cross-pipeline DAG consistency, repair.
    Returns full snapshot dict.
    """
    snapshot: Dict[str, PipelineDAG] = {}
    results:  Dict[str, Any]         = {}

    for pipeline in PIPELINES:
        step = run_pipeline_step(pipeline)
        results[pipeline] = step
        dag = load_pipeline_dag(pipeline)
        snapshot[pipeline] = dag

    # Cross-pipeline consistency check
    issues = validate_dag_consistency(snapshot)
    for pipeline, pi in issues.items():
        if pi.has_issues():
            log_audit(f"[{pipeline}] DAG issues: {pi.summary()}", "WARNING", source)
            dag, _ = repair_dag(snapshot[pipeline])
            save_pipeline_dag(dag)
            log_audit(f"[{pipeline}] Auto-repaired", "INFO", source)

    log_audit(f"Audit cycle complete — {len(PIPELINES)} pipelines, "
              f"{sum(1 for p, i in issues.items() if i.has_issues())} repaired", "INFO", source)

    return {
        "cycle_timestamp": _now(),
        "pipelines":       results,
        "issues_found":    {p: i.summary() for p, i in issues.items() if i.has_issues()},
        "dag_snapshot":    {p: d.to_dict() for p, d in snapshot.items()},
        "source":          source,
    }


# ─────────────────────────────────────────────────────────────────────────────
# DUMMY DATA POPULATION
# ─────────────────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_node(node_type: str, extra: Optional[Dict] = None) -> DAGNode:
    data = {"value": uuid.uuid4().hex}
    if extra:
        data.update(extra)
    return DAGNode(id=str(uuid.uuid4()), node_type=node_type, data=data)


def populate_dummy_data() -> None:
    """Create realistic test DAGs for all 6 pipelines."""

    pipeline_templates: Dict[str, List[Tuple[str, Optional[Dict]]]] = {
        "mutation": [
            ("design_spec",      None),
            ("mutation_proposal",{"delta_kp": 0.02, "delta_ki": 0.001}),
            ("twin_simulation",  {"episodes": 5, "passed": True}),
            ("mutation_result",  {"approved": True, "prod_key": uuid.uuid4().hex[:16]}),
        ],
        "sil": [
            ("sil_assessment", {"lambda_d": 1e-6, "test_interval_h": 8760,
                                "architecture": "1oo1", "target_sil": 2}),
            ("pfd_calculation", {"lambda_d": 1e-6, "test_interval_h": 8760}),
            ("sil_verdict",    {"sil_achieved": 2, "compliant": True}),
        ],
        "digital_thread": [
            ("design_spec",    None),
            ("plc_code",       {"program": "PumpCtrl", "version": "1.3.2"}),
            ("test_result",    {"passed": 20, "failed": 0}),
            ("deployment",     {"vm": "VM-C", "sha256": uuid.uuid4().hex}),
        ],
        "audit": [
            ("audit_trigger",  {"reason": "scheduled"}),
            ("snapshot",       {"pipelines": PIPELINES}),
            ("audit_report",   {"issues": 0, "hash": uuid.uuid4().hex}),
        ],
        "consensus": [
            ("proposal",   {"param": "delta_kp", "value": 0.02}),
            ("vote_round", {"votes_for": 3, "votes_against": 0, "quorum": 3}),
            ("decision",   {"accepted": True, "commit_hash": uuid.uuid4().hex}),
        ],
        "immortality": [
            ("ledger_entry",  {"prev_hash": "0" * 64, "entry_n": 1}),
            ("snapshot_seal", {"pipelines": PIPELINES, "integrity": True}),
            ("immortal_record", {"immutable": True, "hash": uuid.uuid4().hex}),
        ],
    }

    for pipeline, node_specs in pipeline_templates.items():
        dag = PipelineDAG(pipeline=pipeline)
        nodes: List[DAGNode] = []
        for node_type, extra in node_specs:
            nodes.append(_make_node(node_type, extra))
        dag.nodes = nodes
        # Linear chain of edges
        dag.edges = [
            DAGEdge(from_node=nodes[i].id, to_node=nodes[i + 1].id,
                    edge_type="derived_from")
            for i in range(len(nodes) - 1)
        ]
        save_pipeline_dag(dag)

    log_audit("Populated dummy data for all 6 pipelines", "INFO", "system")
