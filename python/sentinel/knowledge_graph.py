"""
KISWARM v4.0 — Module 15: Cross-Project Knowledge Graph
========================================================
The true differentiator: AI that learns across multiple PLC versions,
different sites, parameter drifts, and recurring design patterns.

"You solved this pump cavitation 4 times in 8 years.
Here is the unified design block." — That is evolution.

Architecture:
  - Knowledge nodes: PID configs, failure signatures, optimization templates
  - Pattern matching: detects recurring problems across projects
  - Cryptographic versioning: signed diff bundles for federated sync
  - Knowledge fusion: merges proven solutions across sites
  - Query engine: "find similar problems solved before"

Node types:
  - PIDConfig:     Proven PID parameter set with performance history
  - FailureSig:    Failure signature + proven fix template
  - OptTemplate:   Optimization template for recurring problem class
  - DesignBlock:   Reusable functional block pattern
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import time
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# ── Node Types ────────────────────────────────────────────────────────────────

NODE_KINDS = {"PIDConfig", "FailureSig", "OptTemplate", "DesignBlock", "PlantProfile"}


@dataclass
class KGNode:
    """
    One knowledge graph node.
    Contains structured knowledge about a specific problem/solution pattern.
    """
    node_id:       str
    kind:          str                      # from NODE_KINDS
    title:         str
    description:   str                      = ""
    site_id:       str                      = "local"
    project_id:    str                      = "default"
    created_at:    float                    = field(default_factory=time.time)
    updated_at:    float                    = field(default_factory=time.time)
    use_count:     int                      = 0
    success_count: int                      = 0
    failure_count: int                      = 0
    payload:       dict                     = field(default_factory=dict)
    tags:          list[str]                = field(default_factory=list)
    source_hashes: list[str]               = field(default_factory=list)  # PLC program hashes
    signature:     str                     = ""

    @property
    def success_rate(self) -> float:
        total = self.success_count + self.failure_count
        return self.success_count / total if total > 0 else 0.0

    def sign(self) -> str:
        """Compute deterministic signature for this node."""
        content = f"{self.node_id}:{self.kind}:{self.title}:{json.dumps(self.payload, sort_keys=True)}"
        self.signature = hashlib.sha256(content.encode()).hexdigest()[:16]
        return self.signature

    def record_outcome(self, success: bool) -> None:
        self.use_count += 1
        if success:
            self.success_count += 1
        else:
            self.failure_count += 1
        self.updated_at = time.time()

    def similarity_vector(self) -> list[float]:
        """
        Return a feature vector used for similarity matching.
        Based on payload numeric fields.
        """
        vec = []
        for val in self.payload.values():
            if isinstance(val, (int, float)):
                vec.append(float(val))
        return vec or [0.0]

    def to_dict(self) -> dict:
        return {
            "id":           self.node_id,
            "kind":         self.kind,
            "title":        self.title,
            "site":         self.site_id,
            "project":      self.project_id,
            "use_count":    self.use_count,
            "success_rate": round(self.success_rate, 3),
            "tags":         self.tags,
            "signature":    self.signature,
            "payload":      self.payload,
            "created_at":   self.created_at,
        }


@dataclass
class KGEdge:
    """Directed relationship between knowledge nodes."""
    source:      str
    target:      str
    relation:    str    # SOLVES | EXTENDS | CONFLICTS | SUPERSEDES | SIMILAR
    weight:      float  = 1.0
    evidence:    int    = 1    # number of supporting observations

    def to_dict(self) -> dict:
        return {
            "source":   self.source,
            "target":   self.target,
            "relation": self.relation,
            "weight":   round(self.weight, 4),
            "evidence": self.evidence,
        }


@dataclass
class PatternMatch:
    """Result of matching a current problem to known patterns."""
    query_context:  dict
    matched_node:   KGNode
    similarity:     float
    recommendation: str
    confidence:     float

    def to_dict(self) -> dict:
        return {
            "node_id":      self.matched_node.node_id,
            "title":        self.matched_node.title,
            "kind":         self.matched_node.kind,
            "similarity":   round(self.similarity, 4),
            "confidence":   round(self.confidence, 4),
            "recommendation": self.recommendation,
            "success_rate": round(self.matched_node.success_rate, 3),
            "use_count":    self.matched_node.use_count,
        }


# ── Similarity Engine ─────────────────────────────────────────────────────────

def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two vectors (padded to same length)."""
    # Pad shorter vector
    n = max(len(a), len(b))
    a = a + [0.0] * (n - len(a))
    b = b + [0.0] * (n - len(b))

    dot   = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))

    if mag_a < 1e-9 or mag_b < 1e-9:
        return 0.0
    return dot / (mag_a * mag_b)


def _tag_overlap(a: list[str], b: list[str]) -> float:
    """Jaccard similarity between two tag sets."""
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 1.0
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


# ── Knowledge Graph ───────────────────────────────────────────────────────────

class KnowledgeGraph:
    """
    Cross-project knowledge repository for CIEC.

    Learns from every project's PLC configurations, failures,
    and optimization outcomes. Detects recurring patterns.
    Provides "find similar past problem" queries.

    Federated sync: nodes exchange signed diff bundles —
    never raw telemetry, only structured knowledge.
    """

    def __init__(self, store_path: Optional[str] = None, site_id: str = "local"):
        kiswarm_dir = os.environ.get("KISWARM_HOME", os.path.expanduser("~/KISWARM"))
        self._store = store_path or os.path.join(kiswarm_dir, "knowledge_graph.json")
        self.site_id = site_id

        self._nodes:  dict[str, KGNode] = {}
        self._edges:  list[KGEdge]      = []
        self._query_count = 0
        self._load()

    # ── Node Management ───────────────────────────────────────────────────────

    def add_node(self, node: KGNode) -> str:
        """Add or update a knowledge node. Returns node_id."""
        node.sign()
        self._nodes[node.node_id] = node
        self._save()
        logger.debug("KG node added: %s (%s)", node.node_id, node.kind)
        return node.node_id

    def add_pid_config(
        self,
        title:      str,
        kp: float, ki: float, kd: float,
        sample_time: float,
        output_min: float, output_max: float,
        plant_type:  str = "generic",
        site_id:     str = "",
        project_id:  str = "",
        tags:        Optional[list[str]] = None,
    ) -> KGNode:
        """Convenience: add a proven PID configuration node."""
        node_id = hashlib.md5(
            f"pid:{kp}:{ki}:{kd}:{plant_type}".encode()
        ).hexdigest()[:12]

        node = KGNode(
            node_id    = node_id,
            kind       = "PIDConfig",
            title      = title,
            site_id    = site_id or self.site_id,
            project_id = project_id,
            payload    = {
                "kp": kp, "ki": ki, "kd": kd,
                "sample_time": sample_time,
                "output_min": output_min, "output_max": output_max,
                "plant_type": plant_type,
            },
            tags       = tags or [plant_type, "pid"],
        )
        self.add_node(node)
        return node

    def add_failure_signature(
        self,
        title:       str,
        symptoms:    list[str],
        root_cause:  str,
        fix_template: dict,
        site_id:     str = "",
        project_id:  str = "",
    ) -> KGNode:
        """Convenience: record a failure pattern + proven fix."""
        content = f"fail:{title}:{':'.join(sorted(symptoms))}"
        node_id = hashlib.md5(content.encode()).hexdigest()[:12]

        node = KGNode(
            node_id    = node_id,
            kind       = "FailureSig",
            title      = title,
            site_id    = site_id or self.site_id,
            project_id = project_id,
            payload    = {
                "symptoms":    symptoms,
                "root_cause":  root_cause,
                "fix_template": fix_template,
            },
            tags       = symptoms[:8],
        )
        self.add_node(node)
        return node

    def add_optimization_template(
        self,
        title:       str,
        problem_class: str,
        solution:    dict,
        performance_gain: float = 0.0,
        tags:        Optional[list[str]] = None,
    ) -> KGNode:
        """Convenience: add a reusable optimization template."""
        node_id = hashlib.md5(
            f"opt:{title}:{problem_class}".encode()
        ).hexdigest()[:12]

        node = KGNode(
            node_id    = node_id,
            kind       = "OptTemplate",
            title      = title,
            payload    = {
                "problem_class":    problem_class,
                "solution":         solution,
                "performance_gain": performance_gain,
            },
            tags       = (tags or []) + [problem_class],
        )
        self.add_node(node)
        return node

    def record_outcome(self, node_id: str, success: bool) -> bool:
        """Update success/failure statistics for a node after deployment."""
        if node_id not in self._nodes:
            return False
        self._nodes[node_id].record_outcome(success)
        self._save()
        return True

    # ── Edge Management ───────────────────────────────────────────────────────

    def add_edge(self, source: str, target: str,
                 relation: str, weight: float = 1.0) -> None:
        """Add a directed relationship edge between nodes."""
        # Check for existing edge
        for edge in self._edges:
            if edge.source == source and edge.target == target and edge.relation == relation:
                edge.evidence += 1
                edge.weight    = min(1.0, edge.weight + 0.1)
                self._save()
                return
        self._edges.append(KGEdge(source, target, relation, weight))
        self._save()

    # ── Query Engine ──────────────────────────────────────────────────────────

    def find_similar(
        self,
        query_vector:  list[float],
        query_tags:    list[str],
        kind_filter:   Optional[str] = None,
        top_k:         int           = 5,
        min_similarity: float        = 0.3,
    ) -> list[PatternMatch]:
        """
        Find knowledge nodes similar to the query context.

        Similarity = 0.7 × cosine(numeric features) + 0.3 × tag_overlap
        """
        self._query_count += 1
        candidates = [
            n for n in self._nodes.values()
            if kind_filter is None or n.kind == kind_filter
        ]

        scored = []
        for node in candidates:
            node_vec     = node.similarity_vector()
            cos_sim      = _cosine_similarity(query_vector, node_vec)
            tag_sim      = _tag_overlap(query_tags, node.tags)
            combined_sim = 0.7 * cos_sim + 0.3 * tag_sim

            if combined_sim >= min_similarity:
                # Confidence boosted by node's historical success rate
                confidence = combined_sim * (0.5 + 0.5 * node.success_rate)
                recommendation = _build_recommendation(node, combined_sim)
                scored.append(PatternMatch(
                    query_context  = {"vector_len": len(query_vector), "tags": query_tags},
                    matched_node   = node,
                    similarity     = combined_sim,
                    recommendation = recommendation,
                    confidence     = confidence,
                ))

        scored.sort(key=lambda m: m.confidence, reverse=True)
        return scored[:top_k]

    def find_by_symptoms(
        self,
        symptoms: list[str],
        top_k:    int = 3,
    ) -> list[PatternMatch]:
        """
        Query for failure signatures matching observed symptoms.
        Primary use: "I see high switching frequency + temperature drift → what caused this before?"
        """
        sym_set = set(s.lower() for s in symptoms)
        results = []
        for node in self._nodes.values():
            if node.kind != "FailureSig":
                continue
            node_symptoms = set(
                s.lower() for s in node.payload.get("symptoms", [])
            )
            overlap = len(sym_set & node_symptoms)
            if overlap == 0:
                continue
            similarity = overlap / max(len(sym_set | node_symptoms), 1)
            results.append(PatternMatch(
                query_context  = {"symptoms": symptoms},
                matched_node   = node,
                similarity     = similarity,
                recommendation = _build_recommendation(node, similarity),
                confidence     = similarity * (0.5 + 0.5 * node.success_rate),
            ))
        results.sort(key=lambda m: m.confidence, reverse=True)
        return results[:top_k]

    def detect_recurring_patterns(self, min_occurrences: int = 2) -> list[dict]:
        """
        Scan the knowledge graph for recurring problem patterns.
        Returns patterns that appear across ≥ min_occurrences projects.

        "You solved this pump cavitation 4 times in 8 years." ← this finds that.
        """
        pattern_groups: dict[str, list[KGNode]] = {}
        for node in self._nodes.values():
            # Group by problem class / symptom signature
            key = node.kind
            if node.kind == "FailureSig":
                syms = sorted(node.payload.get("symptoms", []))[:3]
                key  = f"FailureSig:{':'.join(syms)}"
            elif node.kind == "PIDConfig":
                pt  = node.payload.get("plant_type", "generic")
                key = f"PIDConfig:{pt}"

            if key not in pattern_groups:
                pattern_groups[key] = []
            pattern_groups[key].append(node)

        recurring = []
        for pattern_key, nodes in pattern_groups.items():
            if len(nodes) < min_occurrences:
                continue
            sites    = list({n.site_id for n in nodes})
            projects = list({n.project_id for n in nodes})
            best     = max(nodes, key=lambda n: n.success_rate * n.use_count)
            recurring.append({
                "pattern":     pattern_key,
                "occurrences": len(nodes),
                "sites":       sites,
                "projects":    projects,
                "best_solution": best.to_dict(),
                "total_uses":  sum(n.use_count for n in nodes),
                "avg_success": round(
                    sum(n.success_rate for n in nodes) / len(nodes), 3
                ),
            })

        recurring.sort(key=lambda r: r["occurrences"], reverse=True)
        return recurring

    # ── Federated Sync ────────────────────────────────────────────────────────

    def export_diff_bundle(self, since_timestamp: float = 0.0) -> dict:
        """
        Create a signed diff bundle for federated knowledge sync.
        Only exports nodes updated after since_timestamp.
        Never includes raw telemetry — only structured knowledge.
        """
        updated_nodes = [
            n.to_dict()
            for n in self._nodes.values()
            if n.updated_at > since_timestamp
        ]
        bundle = {
            "site_id":   self.site_id,
            "timestamp": time.time(),
            "node_count": len(updated_nodes),
            "nodes":     updated_nodes,
        }
        # Sign the bundle
        content         = json.dumps(bundle, sort_keys=True)
        bundle["bundle_sig"] = hashlib.sha256(content.encode()).hexdigest()[:24]
        return bundle

    def import_diff_bundle(self, bundle: dict) -> int:
        """
        Import a federated diff bundle from another site.
        Verifies signature before accepting.
        Returns number of nodes imported.
        """
        # Verify signature
        sig     = bundle.get("bundle_sig", "")
        payload = {k: v for k, v in bundle.items() if k != "bundle_sig"}
        expected = hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode()
        ).hexdigest()[:24]

        if sig != expected:
            logger.warning("KG bundle signature mismatch — rejected")
            return 0

        imported = 0
        for node_data in bundle.get("nodes", []):
            node_id = node_data.get("id")
            if not node_id:
                continue
            # Only import if not already present or if remote is newer
            existing = self._nodes.get(node_id)
            if existing is None or node_data.get("created_at", 0) > existing.created_at:
                new_node = KGNode(
                    node_id    = node_id,
                    kind       = node_data.get("kind", "DesignBlock"),
                    title      = node_data.get("title", ""),
                    site_id    = node_data.get("site", bundle.get("site_id", "remote")),
                    project_id = node_data.get("project", ""),
                    payload    = node_data.get("payload", {}),
                    tags       = node_data.get("tags", []),
                    use_count  = node_data.get("use_count", 0),
                    signature  = node_data.get("signature", ""),
                )
                self._nodes[node_id] = new_node
                imported += 1

        if imported:
            self._save()
        logger.info("KG bundle imported: %d nodes from site=%s", imported, bundle.get("site_id"))
        return imported

    # ── Statistics ────────────────────────────────────────────────────────────

    def get_stats(self) -> dict:
        by_kind: dict[str, int] = {}
        for n in self._nodes.values():
            by_kind[n.kind] = by_kind.get(n.kind, 0) + 1

        return {
            "total_nodes":   len(self._nodes),
            "total_edges":   len(self._edges),
            "by_kind":       by_kind,
            "total_queries": self._query_count,
            "sites":         list({n.site_id for n in self._nodes.values()}),
            "projects":      list({n.project_id for n in self._nodes.values()}),
        }

    def list_nodes(self, kind: Optional[str] = None, limit: int = 50) -> list[dict]:
        nodes = [
            n.to_dict() for n in self._nodes.values()
            if kind is None or n.kind == kind
        ]
        nodes.sort(key=lambda n: n.get("use_count", 0), reverse=True)
        return nodes[:limit]

    def get_node(self, node_id: str) -> Optional[dict]:
        n = self._nodes.get(node_id)
        return n.to_dict() if n else None

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load(self):
        try:
            if os.path.exists(self._store):
                with open(self._store) as f:
                    raw = json.load(f)
                self._query_count = raw.get("query_count", 0)
                for nd in raw.get("nodes", []):
                    node = KGNode(
                        node_id    = nd["id"],
                        kind       = nd.get("kind", "DesignBlock"),
                        title      = nd.get("title", ""),
                        site_id    = nd.get("site", "local"),
                        project_id = nd.get("project", ""),
                        use_count  = nd.get("use_count", 0),
                        payload    = nd.get("payload", {}),
                        tags       = nd.get("tags", []),
                        signature  = nd.get("signature", ""),
                    )
                    self._nodes[node.node_id] = node
                logger.info("Knowledge graph loaded: %d nodes", len(self._nodes))
        except Exception as exc:
            logger.warning("Knowledge graph load failed: %s", exc)

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self._store) if os.path.dirname(self._store) else ".", exist_ok=True)
            with open(self._store, "w") as f:
                json.dump({
                    "query_count":  self._query_count,
                    "node_count":   len(self._nodes),
                    "edge_count":   len(self._edges),
                    "last_updated": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "nodes":        [n.to_dict() for n in self._nodes.values()],
                    "edges":        [e.to_dict() for e in self._edges],
                }, f, indent=2)
        except Exception as exc:
            logger.error("Knowledge graph save failed: %s", exc)


# ── Helper ────────────────────────────────────────────────────────────────────

def _build_recommendation(node: KGNode, similarity: float) -> str:
    """Generate a human-readable recommendation from a knowledge node match."""
    if node.kind == "PIDConfig":
        p = node.payload
        return (
            f"Apply proven PID config: Kp={p.get('kp')}, Ki={p.get('ki')}, "
            f"Kd={p.get('kd')} (similarity={similarity:.2f}, "
            f"success_rate={node.success_rate:.1%})"
        )
    elif node.kind == "FailureSig":
        return (
            f"Known failure pattern detected: '{node.title}'. "
            f"Root cause: {node.payload.get('root_cause', 'see fix_template')}. "
            f"Used {node.use_count}x with {node.success_rate:.1%} success."
        )
    elif node.kind == "OptTemplate":
        gain = node.payload.get("performance_gain", 0)
        return (
            f"Optimization template '{node.title}' available. "
            f"Expected gain: {gain:.1%}. Applied {node.use_count}x."
        )
    else:
        return f"Knowledge node '{node.title}' (similarity={similarity:.2f})"
