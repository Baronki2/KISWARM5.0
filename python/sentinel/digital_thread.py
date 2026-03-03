"""
KISWARM v4.2 — Module 28: Digital Thread Tracker
=================================================
End-to-end traceability from design intent → simulation → formal proof →
mutation governance → test → production deployment.

The "Digital Thread" is the continuous linkage of all data, decisions,
and evidence across the entire asset lifecycle. It enables:
  • Root-cause analysis: trace any production behaviour back to its design
  • Regulatory compliance: NAMUR NE 175, IEC 62443, IEC 61508 audit
  • Change impact analysis: what else does this mutation affect?
  • Complete provenance for AI-generated mutations

Thread Model:
  ThreadNode  — any artefact (design doc, SIL cert, test result, PLC build)
  ThreadEdge  — traceability link between nodes (derived_from, verified_by, etc.)
  ThreadGraph — full lineage DAG (Directed Acyclic Graph)
  ChangeSet   — atomic bundle of nodes + edges added in one operation
  ThreadQuery — find ancestors, descendants, or impact paths for any node

Node Types:
  design_spec    — original engineering design document
  simulation     — digital twin run result
  formal_cert    — Lyapunov / SIL / barrier certificate
  governance     — governance pipeline record
  mutation       — parameter change proposal
  plc_build      — compiled PLC code + parameters
  test_result    — acceptance test outcome
  deployment     — production deployment record
  alert          — runtime alarm or anomaly
"""

import hashlib
import json
import datetime
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple


# ─────────────────────────────────────────────────────────────────────────────
# NODE TYPES AND EDGE TYPES
# ─────────────────────────────────────────────────────────────────────────────

VALID_NODE_TYPES = {
    "design_spec", "simulation", "formal_cert", "governance",
    "mutation", "plc_build", "test_result", "deployment", "alert",
    "sil_assessment", "xai_explanation", "physics_episode", "ast_parse",
}

VALID_EDGE_TYPES = {
    "derived_from",     # B was derived from A
    "verified_by",      # A is verified by B (formal cert)
    "tested_by",        # A was tested by B
    "deployed_as",      # A was deployed as B
    "approved_by",      # A was approved by governance record B
    "caused",           # A caused B (alert tracing)
    "supersedes",       # A replaces B
    "implements",       # A implements design spec B
    "references",       # A references B (non-causal)
}


# ─────────────────────────────────────────────────────────────────────────────
# GRAPH NODES AND EDGES
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ThreadNode:
    """One artefact in the digital thread."""
    node_id: str
    node_type: str
    title: str
    payload: Dict[str, Any]       # type-specific data (cert params, test scores, etc.)
    author: str
    version: str
    timestamp: str
    signature: str
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_id":   self.node_id,
            "node_type": self.node_type,
            "title":     self.title,
            "author":    self.author,
            "version":   self.version,
            "timestamp": self.timestamp,
            "signature": self.signature,
            "tags":      self.tags,
            "payload_keys": list(self.payload.keys()),
        }


@dataclass
class ThreadEdge:
    """Directed traceability link: source → target."""
    edge_id: str
    edge_type: str
    source_id: str
    target_id: str
    annotation: str
    timestamp: str
    signature: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "edge_id":    self.edge_id,
            "edge_type":  self.edge_type,
            "source_id":  self.source_id,
            "target_id":  self.target_id,
            "annotation": self.annotation,
            "timestamp":  self.timestamp,
            "signature":  self.signature,
        }


@dataclass
class ChangeSet:
    """Atomic bundle of thread mutations (nodes + edges) with rollback support."""
    changeset_id: str
    description: str
    nodes_added: List[str]
    edges_added: List[str]
    author: str
    timestamp: str
    committed: bool
    signature: str


# ─────────────────────────────────────────────────────────────────────────────
# COMPLIANCE MATRIX
# ─────────────────────────────────────────────────────────────────────────────

COMPLIANCE_REQUIREMENTS = {
    "iec_61508": {
        "description": "IEC 61508 Functional Safety",
        "required_nodes": ["design_spec", "simulation", "sil_assessment",
                           "formal_cert", "test_result", "deployment"],
        "required_edges": ["verified_by", "tested_by", "deployed_as"],
        "mandatory_fields": {
            "sil_assessment": ["sil_achieved", "sil_required", "compliant"],
            "formal_cert":    ["approved", "spectral_radius"],
        },
    },
    "iec_62443": {
        "description": "IEC 62443 Industrial Cybersecurity",
        "required_nodes": ["design_spec", "governance", "test_result", "deployment"],
        "required_edges": ["approved_by", "tested_by", "deployed_as"],
        "mandatory_fields": {
            "governance": ["approved_by", "production_key"],
        },
    },
    "namur_ne175": {
        "description": "NAMUR NE 175 AI in Process Control",
        "required_nodes": ["design_spec", "simulation", "formal_cert",
                           "xai_explanation", "governance", "deployment"],
        "required_edges": ["verified_by", "approved_by", "derived_from"],
        "mandatory_fields": {
            "xai_explanation": ["top_features", "confidence", "natural_language"],
        },
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# DIGITAL THREAD TRACKER
# ─────────────────────────────────────────────────────────────────────────────

class DigitalThreadTracker:
    """
    Full digital thread DAG with lineage queries, compliance checks,
    and impact analysis.
    """

    def __init__(self):
        self._nodes: Dict[str, ThreadNode] = {}
        self._edges: Dict[str, ThreadEdge] = {}
        # Adjacency: source → [target, ...]
        self._adj:   Dict[str, List[str]]  = {}
        # Reverse adjacency: target → [source, ...]
        self._radj:  Dict[str, List[str]]  = {}

        self._changesets: List[ChangeSet] = []
        self._node_counter = 0
        self._edge_counter = 0

    # ── Node Operations ───────────────────────────────────────────────────────

    def add_node(
        self,
        node_type: str,
        title: str,
        payload: Dict[str, Any],
        author: str = "kiswarm",
        version: str = "1.0",
        tags: Optional[List[str]] = None,
        node_id: Optional[str] = None,
    ) -> ThreadNode:
        if node_type not in VALID_NODE_TYPES:
            raise ValueError(f"Unknown node type {node_type!r}")

        ts  = datetime.datetime.now().isoformat()
        nid = node_id or self._gen_node_id(node_type)
        sig = hashlib.sha256(f"{nid}:{title}:{ts}".encode()).hexdigest()[:24]

        node = ThreadNode(
            node_id   = nid,
            node_type = node_type,
            title     = title,
            payload   = payload,
            author    = author,
            version   = version,
            timestamp = ts,
            signature = sig,
            tags      = tags or [],
        )
        self._nodes[nid] = node
        self._adj[nid]   = []
        self._radj[nid]  = []
        self._node_counter += 1
        return node

    def get_node(self, node_id: str) -> Optional[Dict[str, Any]]:
        n = self._nodes.get(node_id)
        if n is None:
            return None
        return {**n.to_dict(), "payload": n.payload}

    # ── Edge Operations ───────────────────────────────────────────────────────

    def add_edge(
        self,
        source_id: str,
        target_id: str,
        edge_type: str,
        annotation: str = "",
    ) -> ThreadEdge:
        if edge_type not in VALID_EDGE_TYPES:
            raise ValueError(f"Unknown edge type {edge_type!r}")
        if source_id not in self._nodes:
            raise ValueError(f"Source node {source_id!r} not found")
        if target_id not in self._nodes:
            raise ValueError(f"Target node {target_id!r} not found")

        ts  = datetime.datetime.now().isoformat()
        eid = f"EDGE_{self._edge_counter:06d}"
        self._edge_counter += 1
        sig = hashlib.sha256(f"{eid}:{source_id}→{target_id}:{edge_type}".encode()).hexdigest()[:24]

        edge = ThreadEdge(
            edge_id    = eid,
            edge_type  = edge_type,
            source_id  = source_id,
            target_id  = target_id,
            annotation = annotation,
            timestamp  = ts,
            signature  = sig,
        )
        self._edges[eid] = edge
        self._adj[source_id].append(target_id)
        self._radj[target_id].append(source_id)
        return edge

    # ── Lineage Queries ───────────────────────────────────────────────────────

    def ancestors(self, node_id: str, max_depth: int = 20) -> List[Dict[str, Any]]:
        """Return all ancestors (upstream lineage) of a node via BFS."""
        visited: Set[str] = set()
        queue   = [(node_id, 0)]
        result  = []
        while queue:
            nid, depth = queue.pop(0)
            if nid in visited or depth > max_depth:
                continue
            visited.add(nid)
            if nid != node_id:
                n = self._nodes.get(nid)
                if n:
                    result.append({**n.to_dict(), "depth": depth})
            for parent_id in self._adj.get(nid, []):
                if parent_id not in visited:
                    queue.append((parent_id, depth + 1))
        return result

    def descendants(self, node_id: str, max_depth: int = 20) -> List[Dict[str, Any]]:
        """Return all descendants (downstream impact) of a node via BFS."""
        visited: Set[str] = set()
        queue   = [(node_id, 0)]
        result  = []
        while queue:
            nid, depth = queue.pop(0)
            if nid in visited or depth > max_depth:
                continue
            visited.add(nid)
            if nid != node_id:
                n = self._nodes.get(nid)
                if n:
                    result.append({**n.to_dict(), "depth": depth})
            for child_id in self._radj.get(nid, []):
                if child_id not in visited:
                    queue.append((child_id, depth + 1))
        return result

    def impact_path(self, from_id: str, to_id: str) -> List[str]:
        """Find shortest path from from_id to to_id (BFS on reverse adjacency)."""
        if from_id == to_id:
            return [from_id]
        visited = {from_id}
        queue   = [(from_id, [from_id])]
        while queue:
            current, path = queue.pop(0)
            for nxt in self._radj.get(current, []):
                if nxt == to_id:
                    return path + [nxt]
                if nxt not in visited:
                    visited.add(nxt)
                    queue.append((nxt, path + [nxt]))
        return []   # no path found

    def mutation_lineage(self, mutation_node_id: str) -> Dict[str, Any]:
        """
        Full lineage report for a mutation node:
        what it was derived from → what it produced → where it was deployed.
        """
        if mutation_node_id not in self._nodes:
            return {"error": f"Node {mutation_node_id!r} not found"}

        return {
            "node":        self._nodes[mutation_node_id].to_dict(),
            "ancestors":   self.ancestors(mutation_node_id),
            "descendants": self.descendants(mutation_node_id),
        }

    # ── Change Sets ───────────────────────────────────────────────────────────

    def begin_changeset(self, description: str, author: str = "kiswarm") -> str:
        """Open a new changeset (returns changeset_id)."""
        ts  = datetime.datetime.now().isoformat()
        cid = f"CS_{len(self._changesets):05d}_{ts[:10].replace('-','')}"
        sig = hashlib.sha256(f"{cid}:{description}".encode()).hexdigest()[:16]
        cs  = ChangeSet(
            changeset_id = cid,
            description  = description,
            nodes_added  = [],
            edges_added  = [],
            author       = author,
            timestamp    = ts,
            committed    = False,
            signature    = sig,
        )
        self._changesets.append(cs)
        return cid

    def commit_changeset(self, changeset_id: str) -> bool:
        for cs in self._changesets:
            if cs.changeset_id == changeset_id and not cs.committed:
                cs.committed = True
                return True
        return False

    # ── Compliance Checks ─────────────────────────────────────────────────────

    def check_compliance(
        self,
        standard: str,
        scope_node_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Check compliance of the digital thread against a given standard.
        scope_node_ids: if provided, only check nodes in this list.
        """
        if standard not in COMPLIANCE_REQUIREMENTS:
            return {"error": f"Unknown standard {standard!r}. Valid: {list(COMPLIANCE_REQUIREMENTS.keys())}"}

        req  = COMPLIANCE_REQUIREMENTS[standard]
        scope_nodes = (
            {nid: self._nodes[nid] for nid in scope_node_ids if nid in self._nodes}
            if scope_node_ids
            else self._nodes
        )

        # Check required node types exist
        present_types = {n.node_type for n in scope_nodes.values()}
        missing_types = [t for t in req["required_nodes"] if t not in present_types]

        # Check required edge types exist
        present_etypes = {e.edge_type for e in self._edges.values()}
        missing_etypes = [t for t in req["required_edges"] if t not in present_etypes]

        # Check mandatory fields
        field_issues = []
        for node_type, required_fields in req.get("mandatory_fields", {}).items():
            nodes_of_type = [n for n in scope_nodes.values() if n.node_type == node_type]
            for node in nodes_of_type:
                for field in required_fields:
                    if field not in node.payload:
                        field_issues.append(
                            f"{node.node_id} ({node_type}) missing field '{field}'"
                        )

        compliant = (not missing_types and not missing_etypes and not field_issues)

        return {
            "standard":        standard,
            "description":     req["description"],
            "compliant":       compliant,
            "missing_node_types": missing_types,
            "missing_edge_types": missing_etypes,
            "field_issues":    field_issues,
            "scope_size":      len(scope_nodes),
            "present_node_types": sorted(present_types),
        }

    # ── Search ────────────────────────────────────────────────────────────────

    def find_nodes(
        self,
        node_type: Optional[str] = None,
        tag: Optional[str] = None,
        author: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        results = []
        for node in self._nodes.values():
            if node_type and node.node_type != node_type:
                continue
            if tag and tag not in node.tags:
                continue
            if author and node.author != author:
                continue
            results.append(node.to_dict())
            if len(results) >= limit:
                break
        return results

    # ── Stats ─────────────────────────────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        type_count: Dict[str, int] = {}
        for n in self._nodes.values():
            type_count[n.node_type] = type_count.get(n.node_type, 0) + 1

        edge_count: Dict[str, int] = {}
        for e in self._edges.values():
            edge_count[e.edge_type] = edge_count.get(e.edge_type, 0) + 1

        return {
            "total_nodes":       len(self._nodes),
            "total_edges":       len(self._edges),
            "changesets":        len(self._changesets),
            "node_type_counts":  type_count,
            "edge_type_counts":  edge_count,
            "supported_standards": list(COMPLIANCE_REQUIREMENTS.keys()),
        }

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _gen_node_id(self, node_type: str) -> str:
        ts = datetime.datetime.now().strftime("%Y%m%d%H%M%S%f")[:18]
        return f"NODE_{node_type.upper()[:6]}_{self._node_counter:06d}_{ts}"
