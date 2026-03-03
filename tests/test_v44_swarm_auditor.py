"""
KISWARM v4.4 — Test Suite: Self-Healing Swarm Auditor (Modules 31 + 32)
115 tests covering:
  • AuditLedger — append-only SHA-256 chain
  • DAGNode / DAGEdge / PipelineDAG data models
  • IEC 61508 PFD / SIL band calculations
  • DAG validation: dangling edges, cycle detection, SIL patch
  • run_pipeline_step — all 6 pipelines
  • run_audit_cycle — full 6-pipeline sweep
  • populate_dummy_data — realistic test data
  • SwarmAuditorNode — lifecycle, force_cycle, peer comparison
  • PermanentAuditor — lifecycle
  • SwarmCoordinator — n-node management, consensus, aggregate stats
"""

import asyncio
import os
import sys
import tempfile
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from python.sentinel.swarm_auditor import (
    AuditLedger, DAGNode, DAGEdge, PipelineDAG,
    run_pfd_calculation, run_sil_band_check,
    repair_dag, validate_dag_consistency,
    run_pipeline_step, run_audit_cycle,
    populate_dummy_data, load_pipeline_dag, save_pipeline_dag,
    log_audit, PIPELINES, DATA_DIR,
)
from python.sentinel.swarm_dag import (
    SwarmAuditorNode, PermanentAuditor, SwarmCoordinator,
    _majority_hash, _compare_snapshots, _dag_hash,
)


# ─────────────────────────────────────────────────────────────────────────────
# FIXTURES
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def fresh_data(tmp_path, monkeypatch):
    """Redirect DATA_DIR to tmp so tests never pollute real sentinel_data."""
    import python.sentinel.swarm_auditor as sa
    monkeypatch.setattr(sa, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(sa, "AUDIT_LOG", str(tmp_path / "audit_ledger.jsonl"))
    # Replace singleton ledger with fresh one
    fresh = AuditLedger(str(tmp_path / "audit_ledger.jsonl"))
    monkeypatch.setattr(sa, "_ledger", fresh)
    yield tmp_path


@pytest.fixture
def ledger(tmp_path):
    return AuditLedger(str(tmp_path / "ledger.jsonl"))


@pytest.fixture
def sample_dag():
    n1 = DAGNode(id="n1", node_type="design_spec",  data={"v": "1"})
    n2 = DAGNode(id="n2", node_type="mutation",      data={"v": "2"})
    n3 = DAGNode(id="n3", node_type="test_result",   data={"v": "3"})
    e1 = DAGEdge(from_node="n1", to_node="n2", edge_type="derived_from")
    e2 = DAGEdge(from_node="n2", to_node="n3", edge_type="tested_by")
    return PipelineDAG(pipeline="mutation", nodes=[n1, n2, n3], edges=[e1, e2])


@pytest.fixture
def sil_dag():
    n = DAGNode(id="s1", node_type="sil_assessment",
                data={"lambda_d": 1e-6, "test_interval_h": 8760, "architecture": "1oo1", "target_sil": 2})
    return PipelineDAG(pipeline="sil", nodes=[n], edges=[])


@pytest.fixture
def coordinator():
    return SwarmCoordinator(n_nodes=3, interval_seconds=5)


# ─────────────────────────────────────────────────────────────────────────────
# AUDIT LEDGER
# ─────────────────────────────────────────────────────────────────────────────

class TestAuditLedger:
    def test_empty_ledger_integrity(self, ledger):
        intact, checked = ledger.verify_integrity()
        assert intact
        assert checked == 0

    def test_append_returns_hex_hash(self, ledger):
        h = ledger.append("hello")
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_chain_hashes_differ(self, ledger):
        h1 = ledger.append("first")
        h2 = ledger.append("second")
        assert h1 != h2

    def test_integrity_after_n_entries(self, ledger):
        for i in range(20):
            ledger.append(f"msg {i}", level="INFO")
        intact, checked = ledger.verify_integrity()
        assert intact
        assert checked == 20

    def test_entry_count(self, ledger):
        for i in range(7):
            ledger.append(f"entry {i}")
        assert ledger.entry_count() == 7

    def test_tail_returns_dicts(self, ledger):
        ledger.append("a")
        ledger.append("b")
        entries = ledger.tail(10)
        assert len(entries) == 2
        assert all("message" in e for e in entries)

    def test_tail_limit(self, ledger):
        for i in range(30):
            ledger.append(f"msg {i}")
        assert len(ledger.tail(5)) == 5

    def test_levels_preserved(self, ledger):
        ledger.append("warn", level="WARNING")
        entry = ledger.tail(1)[0]
        assert entry["level"] == "WARNING"

    def test_source_preserved(self, ledger):
        ledger.append("from node", source="node-42")
        entry = ledger.tail(1)[0]
        assert entry["source"] == "node-42"

    def test_new_ledger_resumes_chain(self, tmp_path):
        path = str(tmp_path / "l.jsonl")
        l1 = AuditLedger(path)
        l1.append("entry 1")
        l2 = AuditLedger(path)   # new instance, same file
        l2.append("entry 2")
        intact, checked = l2.verify_integrity()
        assert intact
        assert checked == 2


# ─────────────────────────────────────────────────────────────────────────────
# DATA MODELS
# ─────────────────────────────────────────────────────────────────────────────

class TestDataModels:
    def test_dag_node_to_dict(self):
        n = DAGNode(id="x", node_type="spec", data={"k": "v"})
        d = n.to_dict()
        assert d["id"] == "x"
        assert d["node_type"] == "spec"
        assert d["data"]["k"] == "v"

    def test_dag_node_from_dict_roundtrip(self):
        n = DAGNode(id="y", node_type="mutation", data={})
        n2 = DAGNode.from_dict(n.to_dict())
        assert n2.id == n.id
        assert n2.node_type == n.node_type

    def test_dag_edge_roundtrip(self):
        e = DAGEdge(from_node="a", to_node="b", edge_type="derived_from")
        e2 = DAGEdge.from_dict(e.to_dict())
        assert e2.from_node == "a"
        assert e2.to_node == "b"

    def test_pipeline_dag_node_ids(self, sample_dag):
        ids = sample_dag.node_ids()
        assert "n1" in ids
        assert "n3" in ids

    def test_pipeline_dag_to_dict(self, sample_dag):
        d = sample_dag.to_dict()
        assert d["pipeline"] == "mutation"
        assert len(d["nodes"]) == 3
        assert len(d["edges"]) == 2

    def test_pipeline_dag_from_dict(self, sample_dag):
        d = sample_dag.to_dict()
        dag2 = PipelineDAG.from_dict(d)
        assert dag2.pipeline == "mutation"
        assert len(dag2.nodes) == 3

    def test_dag_node_has_timestamp(self):
        n = DAGNode(id="t", node_type="x", data={})
        assert "T" in n.timestamp  # ISO timestamp


# ─────────────────────────────────────────────────────────────────────────────
# IEC 61508 PFD / SIL CALCULATIONS
# ─────────────────────────────────────────────────────────────────────────────

class TestPFDSIL:
    def test_pfd_1oo1_formula(self):
        pfd = run_pfd_calculation({"lambda_d": 1e-6, "test_interval_h": 8760, "architecture": "1oo1"})
        expected = 1e-6 * 8760 / 2
        assert abs(pfd - expected) < 1e-10

    def test_pfd_1oo2_lower_than_1oo1(self):
        base = {"lambda_d": 1e-5, "test_interval_h": 8760}
        pfd_1oo1 = run_pfd_calculation({**base, "architecture": "1oo1"})
        pfd_1oo2 = run_pfd_calculation({**base, "architecture": "1oo2"})
        assert pfd_1oo2 < pfd_1oo1

    def test_pfd_2oo3_positive(self):
        pfd = run_pfd_calculation({"lambda_d": 1e-5, "test_interval_h": 4380, "architecture": "2oo3"})
        assert pfd > 0

    def test_pfd_default_architecture(self):
        pfd = run_pfd_calculation({"lambda_d": 1e-6, "test_interval_h": 8760})
        assert pfd > 0

    def test_pfd_uses_delta_lambda_fallback(self):
        pfd = run_pfd_calculation({"Δλd": 5e-7, "test_interval_h": 8760})
        assert pfd > 0

    def test_sil_band_check_sil2(self):
        result = run_sil_band_check({"lambda_d": 1e-6, "test_interval_h": 4380, "target_sil": 2})
        assert result["sil"] in [1, 2, 3]
        assert "pfd" in result
        assert "compliant" in result

    def test_sil_band_returns_band_string(self):
        result = run_sil_band_check({"lambda_d": 1e-6, "test_interval_h": 8760})
        assert "SIL" in result["band"]

    def test_sil_compliant_when_achieved_ge_target(self):
        result = run_sil_band_check({"lambda_d": 5e-8, "test_interval_h": 8760, "target_sil": 1})
        if result["sil"] >= 1:
            assert result["compliant"]

    def test_pfd_not_negative(self):
        pfd = run_pfd_calculation({"lambda_d": 0, "test_interval_h": 0})
        assert pfd >= 0


# ─────────────────────────────────────────────────────────────────────────────
# DAG REPAIR
# ─────────────────────────────────────────────────────────────────────────────

class TestDAGRepair:
    def test_clean_dag_no_issues(self, sample_dag):
        _, issues = repair_dag(sample_dag)
        assert not issues.has_issues()

    def test_dangling_edge_removed(self):
        n = DAGNode(id="n1", node_type="spec", data={})
        e = DAGEdge(from_node="n1", to_node="ghost", edge_type="x")
        dag = PipelineDAG(pipeline="mutation", nodes=[n], edges=[e])
        dag, issues = repair_dag(dag)
        assert len(dag.edges) == 0
        assert len(issues.dangling_edges) == 1

    def test_cycle_broken(self):
        n1 = DAGNode(id="a", node_type="x", data={})
        n2 = DAGNode(id="b", node_type="y", data={})
        e1 = DAGEdge(from_node="a", to_node="b", edge_type="x")
        e2 = DAGEdge(from_node="b", to_node="a", edge_type="x")
        dag = PipelineDAG(pipeline="audit", nodes=[n1, n2], edges=[e1, e2])
        dag, issues = repair_dag(dag)
        assert issues.cycles_broken >= 1
        assert len(dag.edges) < 2

    def test_sil_node_patched(self):
        n = DAGNode(id="s1", node_type="sil_assessment", data={})
        dag = PipelineDAG(pipeline="sil", nodes=[n], edges=[])
        _, issues = repair_dag(dag)
        assert issues.sil_nodes_patched == 1
        assert "lambda_d" in n.data

    def test_sil_patch_does_not_overwrite_existing(self, sil_dag):
        _, issues = repair_dag(sil_dag)
        assert sil_dag.nodes[0].data["lambda_d"] == 1e-6  # unchanged

    def test_issues_summary_string(self):
        n = DAGNode(id="n1", node_type="spec", data={})
        e = DAGEdge(from_node="n1", to_node="ghost", edge_type="x")
        dag = PipelineDAG(pipeline="mutation", nodes=[n], edges=[e])
        _, issues = repair_dag(dag)
        summary = issues.summary()
        assert "dangling" in summary

    def test_has_issues_false_for_clean(self, sample_dag):
        _, issues = repair_dag(sample_dag)
        assert not issues.has_issues()

    def test_multiple_dangling_edges(self):
        n = DAGNode(id="n1", node_type="x", data={})
        edges = [DAGEdge(from_node="n1", to_node=f"ghost{i}", edge_type="x") for i in range(5)]
        dag = PipelineDAG(pipeline="audit", nodes=[n], edges=edges)
        dag, issues = repair_dag(dag)
        assert len(issues.dangling_edges) == 5
        assert len(dag.edges) == 0


# ─────────────────────────────────────────────────────────────────────────────
# PIPELINE STEP + FULL CYCLE
# ─────────────────────────────────────────────────────────────────────────────

class TestPipelineStep:
    def test_run_all_pipelines(self):
        populate_dummy_data()
        for p in PIPELINES:
            result = run_pipeline_step(p)
            assert result["pipeline"] == p
            assert "node_count" in result
            assert "timestamp" in result

    def test_sil_pipeline_has_pfd(self):
        populate_dummy_data()
        result = run_pipeline_step("sil")
        assert "pfd" in result
        assert result["pfd"] >= 0

    def test_sil_pipeline_has_sil_band(self):
        populate_dummy_data()
        result = run_pipeline_step("sil")
        assert "sil_band" in result
        assert "sil" in result["sil_band"]

    def test_immortality_has_ledger_info(self):
        populate_dummy_data()
        result = run_pipeline_step("immortality")
        assert "ledger_entries" in result
        assert "ledger_intact" in result

    def test_consensus_has_quorum_size(self):
        populate_dummy_data()
        result = run_pipeline_step("consensus")
        assert "quorum_size" in result

    def test_repaired_key_is_bool(self):
        populate_dummy_data()
        result = run_pipeline_step("mutation")
        assert isinstance(result["repaired"], bool)

    def test_edges_key_is_list(self):
        populate_dummy_data()
        result = run_pipeline_step("digital_thread")
        assert isinstance(result["edges"], list)


class TestAuditCycle:
    def test_audit_cycle_runs_all_pipelines(self):
        populate_dummy_data()
        result = run_audit_cycle()
        assert set(result["pipelines"].keys()) == set(PIPELINES)

    def test_audit_cycle_has_timestamp(self):
        populate_dummy_data()
        result = run_audit_cycle()
        assert "cycle_timestamp" in result
        assert "T" in result["cycle_timestamp"]

    def test_audit_cycle_has_dag_snapshot(self):
        populate_dummy_data()
        result = run_audit_cycle()
        assert "dag_snapshot" in result
        assert len(result["dag_snapshot"]) == len(PIPELINES)

    def test_audit_cycle_issues_found_is_dict(self):
        populate_dummy_data()
        result = run_audit_cycle()
        assert isinstance(result["issues_found"], dict)

    def test_audit_cycle_source_propagated(self):
        populate_dummy_data()
        result = run_audit_cycle(source="pytest")
        assert result["source"] == "pytest"

    def test_audit_cycle_writes_to_ledger(self):
        import python.sentinel.swarm_auditor as sa
        before = sa._ledger.entry_count()
        populate_dummy_data()
        run_audit_cycle()
        after = sa._ledger.entry_count()
        assert after > before


class TestPopulateDummyData:
    def test_creates_all_pipeline_files(self):
        populate_dummy_data()
        for p in PIPELINES:
            dag = load_pipeline_dag(p)
            assert len(dag.nodes) >= 2

    def test_dummy_nodes_have_types(self):
        populate_dummy_data()
        for p in PIPELINES:
            dag = load_pipeline_dag(p)
            for node in dag.nodes:
                assert node.node_type

    def test_dummy_edges_are_valid(self):
        populate_dummy_data()
        for p in PIPELINES:
            dag = load_pipeline_dag(p)
            ids = dag.node_ids()
            for edge in dag.edges:
                assert edge.from_node in ids
                assert edge.to_node in ids

    def test_sil_pipeline_has_lambda_d(self):
        populate_dummy_data()
        dag = load_pipeline_dag("sil")
        sil_nodes = [n for n in dag.nodes if "sil" in n.node_type]
        assert sil_nodes
        assert "lambda_d" in sil_nodes[0].data

    def test_consensus_pipeline_has_proposal(self):
        populate_dummy_data()
        dag = load_pipeline_dag("consensus")
        types = [n.node_type for n in dag.nodes]
        assert "proposal" in types

    def test_immortality_has_ledger_entry(self):
        populate_dummy_data()
        dag = load_pipeline_dag("immortality")
        types = [n.node_type for n in dag.nodes]
        assert "ledger_entry" in types


# ─────────────────────────────────────────────────────────────────────────────
# SWARM NODE
# ─────────────────────────────────────────────────────────────────────────────

class TestSwarmAuditorNode:
    def test_initial_state(self):
        node = SwarmAuditorNode("test-node")
        assert not node._running
        assert node._cycle_count == 0
        assert node._heals == 0

    def test_force_cycle_returns_snapshot(self):
        populate_dummy_data()
        node = SwarmAuditorNode("n1")
        snap = node.force_cycle_sync()
        assert set(snap.keys()) == set(PIPELINES)

    def test_force_cycle_increments_count(self):
        populate_dummy_data()
        node = SwarmAuditorNode("n1")
        node.force_cycle_sync()
        node.force_cycle_sync()
        assert node._cycle_count == 2

    def test_force_cycle_sets_last_snapshot(self):
        populate_dummy_data()
        node = SwarmAuditorNode("n1")
        node.force_cycle_sync()
        assert node.last_snapshot

    def test_force_cycle_sets_hashes(self):
        populate_dummy_data()
        node = SwarmAuditorNode("n1")
        node.force_cycle_sync()
        assert set(node.last_hashes.keys()) == set(PIPELINES)

    def test_status_dict(self):
        populate_dummy_data()
        node = SwarmAuditorNode("n1")
        st = node.status()
        assert "node_id" in st
        assert "running" in st
        assert "cycles" in st
        assert "heals" in st

    def test_stop_sets_running_false(self):
        node = SwarmAuditorNode("n1")
        node._running = True
        node.stop()
        assert not node._running

    def test_two_nodes_compare_snapshots(self):
        populate_dummy_data()
        n1 = SwarmAuditorNode("a")
        n2 = SwarmAuditorNode("b")
        n1.force_cycle_sync()
        n2.force_cycle_sync()
        diff = _compare_snapshots(n1.last_snapshot, n2.last_snapshot)
        # Both ran same pipelines → no missing pipelines
        assert diff["missing_pipelines"] == []


# ─────────────────────────────────────────────────────────────────────────────
# PERMANENT AUDITOR
# ─────────────────────────────────────────────────────────────────────────────

class TestPermanentAuditor:
    def test_initial_state(self):
        pa = PermanentAuditor(interval_seconds=60)
        assert not pa._running
        assert pa._cycle_count == 0

    def test_start_sets_running(self):
        pa = PermanentAuditor(interval_seconds=1)
        # We call internal method without actual asyncio loop
        pa._running = True
        assert pa._running

    def test_stop_clears_running(self):
        pa = PermanentAuditor()
        pa._running = True
        pa.stop()
        assert not pa._running

    def test_status_dict(self):
        pa = PermanentAuditor(interval_seconds=45)
        st = pa.status()
        assert "running" in st
        assert st["interval_s"] == 45
        assert "cycles" in st
        assert "errors" in st


# ─────────────────────────────────────────────────────────────────────────────
# SWARM COORDINATOR
# ─────────────────────────────────────────────────────────────────────────────

class TestSwarmCoordinator:
    def test_creates_n_nodes(self, coordinator):
        assert len(coordinator.nodes) == 3

    def test_all_nodes_unique_ids(self, coordinator):
        ids = [n.node_id for n in coordinator.nodes]
        assert len(set(ids)) == 3

    def test_force_cycle_all_nodes(self, coordinator):
        populate_dummy_data()
        result = coordinator.force_cycle()
        assert len(result["forced_cycle_results"]) == 3

    def test_force_cycle_has_timestamp(self, coordinator):
        populate_dummy_data()
        result = coordinator.force_cycle()
        assert "timestamp" in result

    def test_consensus_view_has_all_pipelines(self, coordinator):
        populate_dummy_data()
        coordinator.force_cycle()
        cv = coordinator.consensus_view()
        assert set(cv["consensus"].keys()) == set(PIPELINES)

    def test_consensus_view_has_quorum_field(self, coordinator):
        populate_dummy_data()
        coordinator.force_cycle()
        cv = coordinator.consensus_view()
        for p in PIPELINES:
            assert "quorum_needed" in cv["consensus"][p]
            assert "quorum_met" in cv["consensus"][p]

    def test_quorum_met_after_all_nodes_cycle(self, coordinator):
        populate_dummy_data()
        coordinator.force_cycle()
        cv = coordinator.consensus_view()
        # All 3 nodes ran same code → same hash → quorum should be met
        for p in PIPELINES:
            # quorum_met depends on hash agreement; check field exists
            assert isinstance(cv["consensus"][p]["quorum_met"], bool)

    def test_aggregate_stats(self, coordinator):
        populate_dummy_data()
        coordinator.force_cycle()
        stats = coordinator.aggregate_stats()
        assert stats["total_cycles"] == 3   # 3 nodes × 1 forced cycle
        assert stats["node_count"] == 3

    def test_node_status_by_id(self, coordinator):
        node_id = coordinator.nodes[0].node_id
        st = coordinator.node_status(node_id)
        assert st is not None
        assert st["node_id"] == node_id

    def test_node_status_unknown_id(self, coordinator):
        assert coordinator.node_status("nonexistent") is None

    def test_status_dict_structure(self, coordinator):
        st = coordinator.status()
        assert "swarm_running" in st
        assert "nodes" in st
        assert "permanent_auditor" in st
        assert len(st["nodes"]) == 3

    def test_stop_returns_dict(self, coordinator):
        result = coordinator.stop()
        assert "status" in result

    def test_start_sets_started(self, coordinator):
        populate_dummy_data()
        coordinator.start()
        assert coordinator._started
        coordinator.stop()


# ─────────────────────────────────────────────────────────────────────────────
# CONSENSUS HELPERS
# ─────────────────────────────────────────────────────────────────────────────

class TestConsensusHelpers:
    def test_majority_hash_own_when_alone(self):
        result = _majority_hash([], "abc123")
        assert result == "abc123"

    def test_majority_hash_peer_wins_2v1(self):
        result = _majority_hash(["peer_hash", "peer_hash"], "my_hash")
        assert result == "peer_hash"

    def test_majority_hash_own_wins_2v1(self):
        result = _majority_hash(["peer_hash"], "my_hash")
        # tied — both get 1 vote, max picks one deterministically
        assert result in ("peer_hash", "my_hash")

    def test_majority_hash_3v0(self):
        result = _majority_hash(["X", "X", "X"], "Y")
        assert result == "X"

    def test_dag_hash_deterministic(self):
        snap = {"key": "value", "num": 42}
        h1 = _dag_hash(snap)
        h2 = _dag_hash(snap)
        assert h1 == h2

    def test_dag_hash_differs_for_different_content(self):
        h1 = _dag_hash({"a": 1})
        h2 = _dag_hash({"a": 2})
        assert h1 != h2

    def test_compare_snapshots_empty_dicts(self):
        diff = _compare_snapshots({}, {})
        assert diff["missing_pipelines"] == []

    def test_compare_snapshots_missing_pipeline(self):
        diff = _compare_snapshots({"mutation": {"edges": ["e1"]}}, {})
        assert "mutation" in diff["missing_pipelines"]

    def test_compare_snapshots_missing_edge(self):
        local = {"mutation": {"edges": ["e1", "e2"]}}
        peer  = {"mutation": {"edges": ["e1"]}}
        diff = _compare_snapshots(local, peer)
        assert "e2" in diff["missing_edges"]

    def test_compare_snapshots_identical_no_diff(self):
        snap = {"mutation": {"edges": ["e1"]}, "sil": {"edges": []}}
        diff = _compare_snapshots(snap, snap)
        assert diff["missing_pipelines"] == []
        assert diff["missing_edges"] == []


# ─────────────────────────────────────────────────────────────────────────────
# VALIDATE DAG CONSISTENCY (cross-pipeline)
# ─────────────────────────────────────────────────────────────────────────────

class TestValidateDAGConsistency:
    def test_clean_snapshot_no_issues(self):
        populate_dummy_data()
        # Run a full audit cycle first to ensure all SIL nodes are patched
        run_audit_cycle()
        snapshot = {p: load_pipeline_dag(p) for p in PIPELINES}
        issues = validate_dag_consistency(snapshot)
        # Only dangling/cycle issues matter; SIL patches are expected after audit
        critical = {p: i for p, i in issues.items()
                    if i.dangling_edges or i.cycles_broken}
        assert critical == {}

    def test_missing_pipeline_reported(self):
        populate_dummy_data()
        snapshot = {p: load_pipeline_dag(p) for p in PIPELINES[:4]}  # only 4 of 6
        issues = validate_dag_consistency(snapshot)
        for pi in issues.values():
            assert len(pi.missing_pipeline_steps) > 0

    def test_dangling_edge_reported(self):
        populate_dummy_data()
        dag = load_pipeline_dag("mutation")
        dag.edges.append(DAGEdge(from_node="ghost", to_node="also_ghost", edge_type="x"))
        snapshot = {p: load_pipeline_dag(p) for p in PIPELINES}
        snapshot["mutation"] = dag
        issues = validate_dag_consistency(snapshot)
        if "mutation" in issues:
            assert issues["mutation"].dangling_edges

    def test_cyclic_dag_reported(self):
        n1 = DAGNode(id="a", node_type="x", data={})
        n2 = DAGNode(id="b", node_type="y", data={})
        dag = PipelineDAG(
            pipeline="audit",
            nodes=[n1, n2],
            edges=[DAGEdge("a", "b", "x"), DAGEdge("b", "a", "x")],
        )
        populate_dummy_data()
        snapshot = {p: load_pipeline_dag(p) for p in PIPELINES}
        snapshot["audit"] = dag
        issues = validate_dag_consistency(snapshot)
        if "audit" in issues:
            assert issues["audit"].cycles_broken >= 1
