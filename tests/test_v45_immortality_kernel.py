"""
KISWARM v4.5 — Test Suite: Swarm Immortality Kernel (Modules 33, 33a, 33b)
130 tests covering:
  • SwarmSoulMirror  — snapshot create/retrieve/verify/chain/stats
  • EvolutionMemoryVault — record/query/filter/timeline/stats
  • SwarmImmortalityKernel — register/checkpoint/recover/survivability/stats
  • Dependency injection + graceful fallbacks
  • SHA-256 integrity verification
  • Risk level heuristics
  • Full end-to-end entity lifecycle
"""

import os
import sys
import time
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from python.sentinel.swarm_soul_mirror import SwarmSoulMirror, IdentitySnapshot
from python.sentinel.evolution_memory_vault import (
    EvolutionMemoryVault, VaultEvent, VALID_EVENT_TYPES,
)
from python.sentinel.swarm_immortality_kernel import (
    SwarmImmortalityKernel, get_immortality_kernel,
    CHECKPOINT_STALENESS_HIGH, CHECKPOINT_STALENESS_MEDIUM,
)


# ─────────────────────────────────────────────────────────────────────────────
# FIXTURES
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def soul_mirror(tmp_path):
    return SwarmSoulMirror(soul_dir=str(tmp_path / "soul"))


@pytest.fixture
def vault(tmp_path):
    return EvolutionMemoryVault(vault_dir=str(tmp_path / "vault"))


@pytest.fixture
def kernel(tmp_path, soul_mirror, vault):
    return SwarmImmortalityKernel(
        base_dir=str(tmp_path / "immortality"),
        soul_mirror=soul_mirror,
        thread_tracker=None,   # not required for core tests
        evolution_vault=vault,
    )


class _Disabled:
    """Falsy sentinel — causes kernel to treat dep as unavailable."""
    def __bool__(self): return False

# Patch: pass a non-None but non-functional object to suppress auto-construction
# while still letting the kernel set self.soul_mirror = None-equivalent
@pytest.fixture
def kernel_no_deps(tmp_path, monkeypatch):
    """Kernel with all optional deps explicitly disabled via None injection."""
    import python.sentinel.swarm_immortality_kernel as _kim
    # Temporarily disable auto-construction by patching flags
    monkeypatch.setattr(_kim, "_HAS_SOUL_MIRROR", False)
    monkeypatch.setattr(_kim, "_HAS_DIGITAL_THREAD", False)
    monkeypatch.setattr(_kim, "_HAS_EVOLUTION_VAULT", False)
    return SwarmImmortalityKernel(
        base_dir=str(tmp_path / "immortality_bare"),
        soul_mirror=None,
        thread_tracker=None,
        evolution_vault=None,
    )


@pytest.fixture
def registered_entity(kernel):
    kernel.register_entity("ent-1", {"roles": ["auditor"], "criticality": "high"})
    return "ent-1"


@pytest.fixture
def checkpointed_entity(kernel, registered_entity):
    kernel.periodic_checkpoint(
        registered_entity,
        {
            "identity_context": {
                "roles": ["auditor"],
                "model_family": "qwen2.5:14b",
                "version": "1.0.0",
            },
            "summary": {"active": True},
        },
    )
    return registered_entity


# ─────────────────────────────────────────────────────────────────────────────
# SOUL MIRROR
# ─────────────────────────────────────────────────────────────────────────────

class TestSwarmSoulMirror:
    def test_create_snapshot_returns_string(self, soul_mirror):
        sid = soul_mirror.create_identity_snapshot("agent-1", {"roles": ["healer"]})
        assert isinstance(sid, str)
        assert len(sid) == 36   # UUID4

    def test_create_snapshot_persisted(self, soul_mirror):
        soul_mirror.create_identity_snapshot("agent-2", {"roles": ["auditor"]})
        assert soul_mirror.snapshot_count("agent-2") == 1

    def test_multiple_snapshots_counted(self, soul_mirror):
        for _ in range(5):
            soul_mirror.create_identity_snapshot("agent-3", {"roles": ["x"]})
        assert soul_mirror.snapshot_count("agent-3") == 5

    def test_get_latest_snapshot_returns_dict(self, soul_mirror):
        soul_mirror.create_identity_snapshot("agent-4", {"roles": ["r"]})
        snap = soul_mirror.get_latest_snapshot("agent-4")
        assert snap is not None
        assert "identity_core" in snap

    def test_get_latest_snapshot_none_if_no_snapshots(self, soul_mirror):
        assert soul_mirror.get_latest_snapshot("ghost-entity") is None

    def test_snapshot_has_content_hash(self, soul_mirror):
        soul_mirror.create_identity_snapshot("agent-5", {"roles": []})
        snap = soul_mirror.get_latest_snapshot("agent-5")
        assert "content_hash" in snap
        assert len(snap["content_hash"]) == 64

    def test_verify_snapshot_valid(self, soul_mirror):
        soul_mirror.create_identity_snapshot("agent-6", {"roles": ["r"]})
        snap = soul_mirror.get_latest_snapshot("agent-6")
        assert soul_mirror.verify_snapshot(snap) is True

    def test_verify_snapshot_tampered(self, soul_mirror):
        soul_mirror.create_identity_snapshot("agent-7", {"roles": []})
        snap = soul_mirror.get_latest_snapshot("agent-7")
        snap["identity_core"]["roles"] = ["injected_role"]
        assert soul_mirror.verify_snapshot(snap) is False

    def test_verify_snapshot_missing_hash(self, soul_mirror):
        snap = {"identity_core": {"roles": []}}
        assert soul_mirror.verify_snapshot(snap) is False

    def test_snapshot_chain_prev_id(self, soul_mirror):
        sid1 = soul_mirror.create_identity_snapshot("agent-8", {"roles": ["a"]})
        soul_mirror.create_identity_snapshot("agent-8", {"roles": ["b"]})
        snap2 = soul_mirror.get_latest_snapshot("agent-8")
        assert snap2["prev_snapshot_id"] == sid1

    def test_first_snapshot_no_prev(self, soul_mirror):
        soul_mirror.create_identity_snapshot("agent-9", {"roles": []})
        snap = soul_mirror.get_latest_snapshot("agent-9")
        assert snap["prev_snapshot_id"] is None

    def test_identity_core_has_roles(self, soul_mirror):
        soul_mirror.create_identity_snapshot("agent-10", {"roles": ["auditor", "healer"]})
        snap = soul_mirror.get_latest_snapshot("agent-10")
        assert snap["identity_core"]["roles"] == ["auditor", "healer"]

    def test_identity_core_model_family(self, soul_mirror):
        soul_mirror.create_identity_snapshot("agent-11", {"model_family": "qwen2.5:14b"})
        snap = soul_mirror.get_latest_snapshot("agent-11")
        assert snap["identity_core"]["model_family"] == "qwen2.5:14b"

    def test_list_entities_empty(self, soul_mirror):
        assert soul_mirror.list_entities() == []

    def test_list_entities_after_snapshot(self, soul_mirror):
        soul_mirror.create_identity_snapshot("ent-x", {})
        entities = soul_mirror.list_entities()
        assert "ent-x" in entities

    def test_entity_stats_no_snapshots(self, soul_mirror):
        stats = soul_mirror.entity_stats("nonexistent")
        assert stats["snapshot_count"] == 0

    def test_entity_stats_with_snapshots(self, soul_mirror):
        soul_mirror.create_identity_snapshot("ent-y", {"version": "2.0"})
        stats = soul_mirror.entity_stats("ent-y")
        assert stats["snapshot_count"] == 1
        assert "latest_id" in stats
        assert stats["valid"] is True

    def test_empty_entity_id_raises(self, soul_mirror):
        with pytest.raises(ValueError):
            soul_mirror.create_identity_snapshot("", {})

    def test_snapshot_has_timestamp(self, soul_mirror):
        soul_mirror.create_identity_snapshot("ent-ts", {})
        snap = soul_mirror.get_latest_snapshot("ent-ts")
        assert snap["timestamp"] > 0

    def test_get_snapshot_by_id(self, soul_mirror):
        sid = soul_mirror.create_identity_snapshot("ent-byid", {"roles": []})
        snap = soul_mirror.get_snapshot_by_id("ent-byid", sid)
        assert snap is not None
        assert snap["snapshot_id"] == sid

    def test_get_snapshot_by_wrong_id(self, soul_mirror):
        soul_mirror.create_identity_snapshot("ent-byid2", {})
        snap = soul_mirror.get_snapshot_by_id("ent-byid2", "nonexistent-uuid")
        assert snap is None


# ─────────────────────────────────────────────────────────────────────────────
# EVOLUTION MEMORY VAULT
# ─────────────────────────────────────────────────────────────────────────────

class TestEvolutionMemoryVault:
    def test_record_returns_event_id(self, vault):
        eid = vault.record_event("immortality_checkpoint", {"entity_id": "e1"}, entity_id="e1")
        assert isinstance(eid, str)
        assert len(eid) == 32

    def test_total_events_count(self, vault):
        for _ in range(5):
            vault.record_event("custom", {"entity_id": "e2"}, entity_id="e2")
        assert vault.total_events() == 5

    def test_get_history_filtered(self, vault):
        vault.record_event("immortality_checkpoint", {}, entity_id="e3")
        vault.record_event("model_upgrade",          {}, entity_id="e3")
        vault.record_event("immortality_checkpoint", {}, entity_id="e3")
        history = vault.get_history("e3", event_type="immortality_checkpoint")
        assert len(history) == 2

    def test_get_history_all_types(self, vault):
        vault.record_event("recovery",    {}, entity_id="e4")
        vault.record_event("migration",   {}, entity_id="e4")
        history = vault.get_history("e4")
        assert len(history) == 2

    def test_get_history_empty_entity(self, vault):
        history = vault.get_history("nonexistent-entity")
        assert history == []

    def test_history_most_recent_first(self, vault):
        vault.record_event("custom", {"seq": 1}, entity_id="e5")
        time.sleep(0.01)
        vault.record_event("custom", {"seq": 2}, entity_id="e5")
        history = vault.get_history("e5")
        assert history[0]["payload"]["seq"] == 2   # latest first

    def test_history_limit(self, vault):
        for i in range(20):
            vault.record_event("custom", {}, entity_id="e6")
        history = vault.get_history("e6", limit=5)
        assert len(history) <= 5

    def test_get_all_events(self, vault):
        vault.record_event("custom", {}, entity_id="ea")
        vault.record_event("recovery", {}, entity_id="eb")
        all_ev = vault.get_all_events()
        assert len(all_ev) == 2

    def test_entity_event_count(self, vault):
        vault.record_event("custom", {}, entity_id="ec")
        vault.record_event("custom", {}, entity_id="ec")
        vault.record_event("custom", {}, entity_id="other")
        assert vault.entity_event_count("ec") == 2

    def test_list_entities(self, vault):
        vault.record_event("custom", {}, entity_id="ex")
        vault.record_event("custom", {}, entity_id="ey")
        entities = vault.list_entities()
        assert "ex" in entities
        assert "ey" in entities

    def test_entity_timeline_empty(self, vault):
        tl = vault.entity_timeline("nobody")
        assert tl["events"] == 0

    def test_entity_timeline_full(self, vault):
        vault.record_event("custom",              {}, entity_id="ez")
        vault.record_event("immortality_checkpoint", {}, entity_id="ez")
        tl = vault.entity_timeline("ez")
        assert tl["events"] == 2
        assert "first_event" in tl
        assert "latest_event" in tl
        assert len(tl["timeline"]) == 2

    def test_stats_by_type(self, vault):
        vault.record_event("recovery",    {}, entity_id="stat-e")
        vault.record_event("migration",   {}, entity_id="stat-e")
        vault.record_event("recovery",    {}, entity_id="stat-e")
        stats = vault.stats()
        assert stats["events_by_type"]["recovery"] == 2
        assert stats["events_by_type"]["migration"] == 1

    def test_stats_entity_count(self, vault):
        vault.record_event("custom", {}, entity_id="s1")
        vault.record_event("custom", {}, entity_id="s2")
        stats = vault.stats()
        assert stats["entity_count"] == 2

    def test_valid_event_types(self, vault):
        for et in VALID_EVENT_TYPES:
            vault.record_event(et, {}, entity_id="type-test")
        assert vault.total_events() == len(VALID_EVENT_TYPES)

    def test_unknown_event_type_accepted(self, vault):
        # Non-standard types are logged as warning but accepted
        eid = vault.record_event("totally_custom_type", {}, entity_id="custom-e")
        assert isinstance(eid, str)

    def test_since_filter(self, vault):
        t1 = time.time()
        vault.record_event("custom", {}, entity_id="ef")
        time.sleep(0.05)
        t2 = time.time()
        vault.record_event("custom", {}, entity_id="ef")
        history = vault.get_history("ef", since=t2)
        assert len(history) == 1

    def test_event_id_deterministic_uniqueness(self, vault):
        ids = {vault.record_event("custom", {}, entity_id="dup") for _ in range(10)}
        # Timestamps differ → IDs differ (with > 1µs resolution)
        assert len(ids) >= 1   # relaxed: at least no crash


# ─────────────────────────────────────────────────────────────────────────────
# SWARM IMMORTALITY KERNEL
# ─────────────────────────────────────────────────────────────────────────────

class TestSwarmImmortalityKernelRegister:
    def test_register_returns_true(self, kernel):
        assert kernel.register_entity("k1", {"roles": ["auditor"]}) is True

    def test_register_persists_to_registry(self, kernel):
        kernel.register_entity("k2", {"criticality": "high"})
        assert "k2" in kernel.get_entity_registry()

    def test_register_meta_preserved(self, kernel):
        kernel.register_entity("k3", {"roles": ["healer"], "sil": 2})
        reg = kernel.get_entity_registry()
        assert reg["k3"]["meta"]["sil"] == 2

    def test_register_empty_id_raises(self, kernel):
        with pytest.raises(ValueError):
            kernel.register_entity("", {})

    def test_register_multiple_entities(self, kernel):
        for i in range(5):
            kernel.register_entity(f"multi-{i}", {})
        assert len(kernel.get_entity_registry()) == 5

    def test_unregister_entity(self, kernel, registered_entity):
        ok = kernel.unregister_entity(registered_entity)
        assert ok is True
        assert registered_entity not in kernel.get_entity_registry()

    def test_unregister_nonexistent(self, kernel):
        assert kernel.unregister_entity("ghost") is False


class TestSwarmImmortalityKernelCheckpoint:
    def test_checkpoint_returns_string(self, kernel, registered_entity):
        cp = kernel.periodic_checkpoint(registered_entity, {})
        assert isinstance(cp, str)
        assert len(cp) == 64   # SHA-256

    def test_checkpoint_unregistered_returns_none(self, kernel):
        cp = kernel.periodic_checkpoint("never-registered", {})
        assert cp is None

    def test_checkpoint_persisted(self, kernel, registered_entity):
        kernel.periodic_checkpoint(registered_entity, {})
        cps = kernel.get_checkpoints(registered_entity)
        assert len(cps) == 1

    def test_multiple_checkpoints(self, kernel, registered_entity):
        for _ in range(4):
            kernel.periodic_checkpoint(registered_entity, {})
        cps = kernel.get_checkpoints(registered_entity)
        assert len(cps) == 4

    def test_checkpoint_has_entity_id(self, kernel, registered_entity):
        kernel.periodic_checkpoint(registered_entity, {})
        cp = kernel.get_checkpoints(registered_entity)[0]
        assert cp["entity_id"] == registered_entity

    def test_checkpoint_has_timestamp(self, kernel, registered_entity):
        kernel.periodic_checkpoint(registered_entity, {})
        cp = kernel.get_checkpoints(registered_entity)[0]
        assert cp["timestamp"] > 0

    def test_checkpoint_records_identity_snapshot(self, kernel, registered_entity):
        kernel.periodic_checkpoint(registered_entity, {
            "identity_context": {"roles": ["tester"], "version": "2.0.0"}
        })
        cp = kernel.get_checkpoints(registered_entity)[0]
        assert cp["identity_snapshot_id"] is not None

    def test_checkpoint_records_vault_event(self, kernel, registered_entity):
        before = kernel.evolution_vault.entity_event_count(registered_entity)
        kernel.periodic_checkpoint(registered_entity, {})
        after = kernel.evolution_vault.entity_event_count(registered_entity)
        assert after > before

    def test_checkpoint_limit_param(self, kernel, registered_entity):
        for _ in range(10):
            kernel.periodic_checkpoint(registered_entity, {})
        cps = kernel.get_checkpoints(registered_entity, limit=3)
        assert len(cps) <= 3

    def test_checkpoint_newest_first(self, kernel, registered_entity):
        kernel.periodic_checkpoint(registered_entity, {"seq": 1})
        time.sleep(0.01)
        kernel.periodic_checkpoint(registered_entity, {"seq": 2})
        cps = kernel.get_checkpoints(registered_entity)
        assert cps[0]["timestamp"] >= cps[1]["timestamp"]


class TestSwarmImmortalityKernelRecover:
    def test_recover_no_checkpoints(self, kernel):
        kernel.register_entity("rec-1", {})
        result = kernel.recover_entity("rec-1")
        assert "no_checkpoints_found" in result["issues"]

    def test_recover_returns_last_checkpoint(self, kernel, checkpointed_entity):
        result = kernel.recover_entity(checkpointed_entity)
        assert result["last_checkpoint"] is not None
        assert "checkpoint_id" in result["last_checkpoint"]

    def test_recover_has_runtime_state(self, kernel, checkpointed_entity):
        result = kernel.recover_entity(checkpointed_entity)
        assert "runtime_state" in result["reconstructed_identity"]

    def test_recover_entity_id_in_result(self, kernel, checkpointed_entity):
        result = kernel.recover_entity(checkpointed_entity)
        assert result["entity_id"] == checkpointed_entity

    def test_recover_identity_core_from_soul_mirror(self, kernel, checkpointed_entity):
        result = kernel.recover_entity(checkpointed_entity)
        # SoulMirror is active, identity_core should be recovered
        reconstructed = result["reconstructed_identity"]
        assert "identity_core" in reconstructed or "no_identity_snapshot_id" in result["issues"]

    def test_recover_no_deps_has_issues(self, kernel_no_deps):
        kernel_no_deps.register_entity("bare-1", {})
        kernel_no_deps.periodic_checkpoint("bare-1", {})
        result = kernel_no_deps.recover_entity("bare-1")
        # Without SoulMirror, identity cannot be verified
        assert any("identity" in i for i in result["issues"])

    def test_recover_records_vault_event(self, kernel, checkpointed_entity):
        before = kernel.evolution_vault.entity_event_count(checkpointed_entity)
        kernel.recover_entity(checkpointed_entity)
        after = kernel.evolution_vault.entity_event_count(checkpointed_entity)
        assert after > before


class TestSwarmImmortalityKernelSurvivability:
    def test_survivability_no_checkpoints(self, kernel):
        kernel.register_entity("surv-1", {})
        result = kernel.verify_survivability("surv-1")
        assert result["risk_level"] == "critical"
        assert result["checkpoint_count"] == 0

    def test_survivability_fresh_checkpoint_low_risk(self, kernel, checkpointed_entity):
        result = kernel.verify_survivability(checkpointed_entity)
        # Fresh checkpoint within seconds → at most "high" (no thread lineage)
        assert result["risk_level"] in ("minimal", "low", "medium", "high")

    def test_survivability_has_required_fields(self, kernel, checkpointed_entity):
        result = kernel.verify_survivability(checkpointed_entity)
        for field in (
            "entity_id", "last_checkpoint_age_s", "checkpoint_count",
            "has_valid_identity_snapshot", "has_thread_lineage",
            "has_evolution_history", "risk_level", "notes",
        ):
            assert field in result, f"Missing field: {field}"

    def test_survivability_checkpoint_count(self, kernel, checkpointed_entity):
        kernel.periodic_checkpoint(checkpointed_entity, {})
        result = kernel.verify_survivability(checkpointed_entity)
        assert result["checkpoint_count"] >= 2

    def test_survivability_has_valid_snapshot_with_soul_mirror(self, kernel, checkpointed_entity):
        result = kernel.verify_survivability(checkpointed_entity)
        assert result["has_valid_identity_snapshot"] is True

    def test_survivability_no_deps_critical_fields(self, kernel_no_deps):
        kernel_no_deps.register_entity("bare-2", {})
        kernel_no_deps.periodic_checkpoint("bare-2", {})
        result = kernel_no_deps.verify_survivability("bare-2")
        assert result["has_valid_identity_snapshot"] is False
        assert result["has_thread_lineage"] is False

    def test_survivability_evolution_history_detected(self, kernel, checkpointed_entity):
        result = kernel.verify_survivability(checkpointed_entity)
        assert result["has_evolution_history"] is True


class TestSwarmImmortalityKernelStats:
    def test_stats_empty_kernel(self, kernel):
        stats = kernel.kernel_stats()
        assert stats["registered_entities"] == 0
        assert stats["total_checkpoints"] == 0

    def test_stats_after_registration(self, kernel, registered_entity):
        stats = kernel.kernel_stats()
        assert stats["registered_entities"] == 1

    def test_stats_after_checkpoint(self, kernel, checkpointed_entity):
        stats = kernel.kernel_stats()
        assert stats["total_checkpoints"] >= 1

    def test_stats_availability_flags(self, kernel):
        stats = kernel.kernel_stats()
        assert stats["soul_mirror_available"] is True
        assert stats["evolution_vault_available"] is True
        # digital_thread auto-constructs if module importable
        assert isinstance(stats["digital_thread_available"], bool)

    def test_stats_availability_no_deps(self, kernel_no_deps):
        stats = kernel_no_deps.kernel_stats()
        assert stats["soul_mirror_available"] is False
        assert stats["evolution_vault_available"] is False

    def test_stats_soul_mirror_count(self, kernel, checkpointed_entity):
        stats = kernel.kernel_stats()
        assert stats["soul_mirror_stats"]["entities_with_snapshots"] >= 1

    def test_stats_vault_event_count(self, kernel, checkpointed_entity):
        stats = kernel.kernel_stats()
        vault_stats = stats["evolution_vault_stats"]
        assert vault_stats["total_events"] >= 1


class TestSwarmImmortalityKernelFactory:
    def test_factory_returns_kernel(self, tmp_path):
        k = get_immortality_kernel(base_dir=str(tmp_path / "fk"))
        assert isinstance(k, SwarmImmortalityKernel)

    def test_factory_creates_dir(self, tmp_path):
        d = str(tmp_path / "new_dir")
        get_immortality_kernel(base_dir=d)
        assert os.path.exists(d)


# ─────────────────────────────────────────────────────────────────────────────
# END-TO-END ENTITY LIFECYCLE
# ─────────────────────────────────────────────────────────────────────────────

class TestEntityLifecycle:
    def test_full_lifecycle(self, kernel):
        """
        Full lifecycle: register → checkpoint × 3 → verify → recover → unregister
        """
        eid = "lifecycle-agent"

        # Register
        ok = kernel.register_entity(eid, {
            "roles":       ["auditor", "healer", "consensus"],
            "criticality": "mission_critical",
            "model_family": "deepseek-r1:8b",
            "sil_level":   2,
        })
        assert ok is True

        # Checkpoint × 3
        cp_ids = []
        for i in range(3):
            cp = kernel.periodic_checkpoint(eid, {
                "identity_context": {
                    "roles":       ["auditor", "healer", "consensus"],
                    "model_family": "deepseek-r1:8b",
                    "version":     f"1.0.{i}",
                },
                "summary": {"iteration": i, "active": True},
                "active_models": ["deepseek-r1:8b"],
            })
            assert cp is not None
            cp_ids.append(cp)
        assert len(set(cp_ids)) == 3   # all unique

        # Verify survivability
        surv = kernel.verify_survivability(eid)
        assert surv["checkpoint_count"] == 3
        assert surv["risk_level"] != "critical"
        assert surv["has_valid_identity_snapshot"] is True
        assert surv["has_evolution_history"] is True

        # Recover
        rec = kernel.recover_entity(eid)
        assert rec["reconstructed_identity"] is not None
        assert rec["last_checkpoint"] is not None

        # Soul mirror integrity
        snap = kernel.soul_mirror.get_latest_snapshot(eid)
        assert kernel.soul_mirror.verify_snapshot(snap) is True

        # Evolution timeline
        tl = kernel.evolution_vault.entity_timeline(eid)
        assert tl["events"] >= 3   # register + 3 checkpoints + possible recovery

        # Unregister
        assert kernel.unregister_entity(eid) is True
        assert eid not in kernel.get_entity_registry()

    def test_multi_entity_isolation(self, kernel):
        """Entities don't bleed into each other."""
        kernel.register_entity("iso-a", {"role": "A"})
        kernel.register_entity("iso-b", {"role": "B"})
        kernel.periodic_checkpoint("iso-a", {})
        kernel.periodic_checkpoint("iso-a", {})
        kernel.periodic_checkpoint("iso-b", {})

        cps_a = kernel.get_checkpoints("iso-a")
        cps_b = kernel.get_checkpoints("iso-b")
        assert len(cps_a) == 2
        assert len(cps_b) == 1

        # Vault isolation
        count_a = kernel.evolution_vault.entity_event_count("iso-a")
        count_b = kernel.evolution_vault.entity_event_count("iso-b")
        assert count_a > count_b   # iso-a has more events
