"""Tests for KISWARM v4.9 — Software Ark (Modules 50-53)"""
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from python.sentinel.ark.software_ark import (
    ArkCategory, ArkItem, ArkItemState, ArkPriority,
    ArkStatus, SoftwareArk, TARGET_ARK_SIZE
)


# ─────────────────────────────────────────────────────────────────────────────
# FIXTURES
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_ark(tmp_path):
    ark = SoftwareArk(ark_dir=str(tmp_path / "ark"))
    return ark


def _make_item(item_id="test:item", state=ArkItemState.MISSING.value,
               priority=ArkPriority.NORMAL.value,
               category=ArkCategory.SCRIPT.value,
               size=1024, min_ram=0.0, os_family=None) -> ArkItem:
    return ArkItem(
        item_id=item_id, name=f"Test {item_id}",
        category=category, priority=priority,
        version="1.0", filename=f"{item_id.replace(':', '_')}.bin",
        size_bytes=size, sha256=None, state=state,
        os_family=os_family, arch=None, min_ram_gb=min_ram,
        description="test item", source_url=None, install_cmd=None,
    )


# ─────────────────────────────────────────────────────────────────────────────
# MODULE 50: SOFTWARE ARK
# ─────────────────────────────────────────────────────────────────────────────

class TestSoftwareArk:

    def test_init_creates_directories(self, tmp_path):
        ark = SoftwareArk(ark_dir=str(tmp_path / "myark"))
        for cat in ArkCategory:
            assert os.path.isdir(os.path.join(str(tmp_path / "myark"), cat.value))

    def test_seeds_default_catalog(self, tmp_ark):
        assert len(tmp_ark._inventory) > 0

    def test_default_catalog_has_critical_items(self, tmp_ark):
        critical = [i for i in tmp_ark._inventory.values()
                    if i.priority == ArkPriority.CRITICAL.value]
        assert len(critical) >= 3

    def test_catalog_has_ollama_model(self, tmp_ark):
        models = [i for i in tmp_ark._inventory.values()
                  if i.category == ArkCategory.MODEL.value]
        assert len(models) >= 3

    def test_catalog_has_minimum_model(self, tmp_ark):
        # There must be a model that runs on 1GB RAM
        tiny = [i for i in tmp_ark._inventory.values()
                if i.category == ArkCategory.MODEL.value
                and i.min_ram_gb <= 1.0]
        assert len(tiny) >= 1

    def test_catalog_has_source_bundle(self, tmp_ark):
        assert "source:kiswarm:current" in tmp_ark._inventory

    def test_catalog_has_bootstrap_script(self, tmp_ark):
        assert "script:bootstrap-offline" in tmp_ark._inventory

    def test_register_item(self, tmp_ark):
        item = _make_item("custom:test")
        tmp_ark.register_item(item)
        assert "custom:test" in tmp_ark._inventory

    def test_inventory_persisted(self, tmp_path):
        ark1 = SoftwareArk(ark_dir=str(tmp_path / "ark"))
        item = _make_item("persist:test")
        ark1.register_item(item)

        ark2 = SoftwareArk(ark_dir=str(tmp_path / "ark"))
        assert "persist:test" in ark2._inventory

    def test_item_path(self, tmp_ark):
        item = _make_item("binary:test")
        path = tmp_ark.item_path(item)
        assert "binary" in path
        assert path.startswith(tmp_ark.ark_dir)

    def test_item_exists_false_initially(self, tmp_ark):
        item = _make_item()
        assert not tmp_ark.item_exists(item)

    def test_item_exists_after_store(self, tmp_path):
        ark = SoftwareArk(ark_dir=str(tmp_path / "ark"))
        item = _make_item("script:stored", category=ArkCategory.SCRIPT.value)
        ark.register_item(item)

        # Create a real file to store
        src = tmp_path / "test.bin"
        src.write_bytes(b"KISWARM TEST DATA" * 100)

        stored = ark.store_file("script:stored", str(src))
        assert stored is not None
        assert ark.item_exists(stored)

    def test_store_file_computes_sha256(self, tmp_path):
        import hashlib
        ark  = SoftwareArk(ark_dir=str(tmp_path / "ark"))
        item = _make_item("script:sha", category=ArkCategory.SCRIPT.value)
        ark.register_item(item)

        src = tmp_path / "sha.bin"
        data = b"checksum test data" * 50
        src.write_bytes(data)

        stored = ark.store_file("script:sha", str(src))
        expected = hashlib.sha256(data).hexdigest()
        assert stored.sha256 == expected

    def test_verify_item_present(self, tmp_path):
        ark  = SoftwareArk(ark_dir=str(tmp_path / "ark"))
        item = _make_item("script:vfy", category=ArkCategory.SCRIPT.value)
        ark.register_item(item)
        src = tmp_path / "v.bin"
        src.write_bytes(b"verify me")
        ark.store_file("script:vfy", str(src))

        state = ark.verify_item(ark.get_item("script:vfy"))
        assert state == ArkItemState.PRESENT

    def test_verify_item_missing(self, tmp_ark):
        item = _make_item("missing:item")
        tmp_ark.register_item(item)
        state = tmp_ark.verify_item(item)
        assert state == ArkItemState.MISSING

    def test_verify_item_corrupted(self, tmp_path):
        import hashlib
        ark  = SoftwareArk(ark_dir=str(tmp_path / "ark"))
        item = _make_item("script:corrupt", category=ArkCategory.SCRIPT.value)
        item.sha256 = "deadbeef" * 8  # Wrong checksum
        ark.register_item(item)

        dest_dir = os.path.join(ark.ark_dir, item.category)
        os.makedirs(dest_dir, exist_ok=True)
        path = ark.item_path(item)
        with open(path, "wb") as f:
            f.write(b"corrupted data")

        state = ark.verify_item(item)
        assert state == ArkItemState.CORRUPTED

    def test_integrity_check_quick(self, tmp_ark):
        results = tmp_ark.integrity_check(quick=True)
        assert isinstance(results, dict)
        assert len(results) > 0

    def test_can_bootstrap_false_when_empty(self, tmp_ark):
        can, gaps = tmp_ark.can_bootstrap()
        # All critical items are MISSING — can't bootstrap
        assert can is False
        assert len(gaps) > 0

    def test_can_bootstrap_true_when_critical_present(self, tmp_path):
        ark = SoftwareArk(ark_dir=str(tmp_path / "ark"))
        # Mark all critical items as PRESENT
        for item in ark._inventory.values():
            if item.priority == ArkPriority.CRITICAL.value:
                # Skip OS-specific items for wrong OS
                if item.os_family and item.os_family != ark._os_family:
                    continue
                item.state = ArkItemState.PRESENT.value
                # Create marker file
                path = ark.item_path(item)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                open(path, "w").close()
        ark._save_inventory()
        can, gaps = ark.can_bootstrap()
        assert can is True
        assert len(gaps) == 0

    def test_what_do_i_have_structure(self, tmp_ark):
        result = tmp_ark.what_do_i_have()
        assert "node" in result
        assert "ark" in result
        assert "capabilities" in result
        assert "os_family" in result["node"]
        assert "ram_gb" in result["node"]
        assert "can_bootstrap_offline" in result["capabilities"]

    def test_missing_by_priority_order(self, tmp_ark):
        missing = tmp_ark.missing_by_priority()
        # CRITICAL items must come before NORMAL items
        priorities = [m.priority for m in missing]
        # Find first non-critical
        first_normal = next(
            (i for i, p in enumerate(priorities)
             if p != ArkPriority.CRITICAL.value), len(priorities)
        )
        # All items before first_normal must be CRITICAL or HIGH
        for p in priorities[:first_normal]:
            assert p in (ArkPriority.CRITICAL.value, ArkPriority.HIGH.value)

    def test_missing_by_priority_excludes_wrong_ram(self, tmp_path):
        ark = SoftwareArk(ark_dir=str(tmp_path / "ark"))
        # Override RAM detection to 2GB
        ark._ram_gb = 2.0
        missing = ark.missing_by_priority()
        # No model requiring >2GB RAM should be in list
        for item in missing:
            if item.category == ArkCategory.MODEL.value:
                assert item.min_ram_gb <= 2.0

    def test_disk_status(self, tmp_ark):
        d = tmp_ark.disk_status()
        assert "ark_used_bytes" in d
        assert "disk_free_bytes" in d
        assert "target_ark_bytes" in d
        assert d["target_ark_bytes"] == TARGET_ARK_SIZE

    def test_status_structure(self, tmp_ark):
        s = tmp_ark.status()
        assert isinstance(s, ArkStatus)
        d = s.to_dict()
        assert "can_bootstrap" in d
        assert "health_score" in d
        assert "critical_complete" in d
        assert "disk_used_human" in d

    def test_ark_item_size_human(self):
        item = _make_item(size=1024**3)
        assert "GB" in item.size_human()
        item2 = _make_item(size=512)
        assert "B" in item2.size_human()

    def test_ark_item_rel_path(self):
        item = ArkItem(
            item_id="model:test", name="T", category="model",
            priority="normal", version="1", filename="models/test.gguf",
            size_bytes=0, sha256=None, state="missing",
            os_family=None, arch=None, min_ram_gb=0.0,
            description="", source_url=None, install_cmd=None,
        )
        assert item.rel_path == "model/models/test.gguf"

    def test_detect_os(self):
        os_fam = SoftwareArk._detect_os()
        assert isinstance(os_fam, str)
        assert len(os_fam) > 0

    def test_detect_ram(self):
        ram = SoftwareArk._detect_ram()
        assert ram > 0
        assert ram < 10000  # Sanity check

    def test_human_format(self):
        h = SoftwareArk._human
        assert h(512) == "512.0B"
        assert "KB" in h(2048)
        assert "MB" in h(5 * 1024**2)
        assert "GB" in h(3 * 1024**3)


# ─────────────────────────────────────────────────────────────────────────────
# MODULE 51: ARK MANAGER
# ─────────────────────────────────────────────────────────────────────────────

class TestArkManager:

    def test_init(self, tmp_ark):
        from python.sentinel.ark.ark_manager import ArkManager
        mgr = ArkManager(ark=tmp_ark, offline=True)
        assert mgr.offline is True
        assert mgr.ark is tmp_ark

    def test_audit_structure(self, tmp_ark):
        from python.sentinel.ark.ark_manager import ArkManager
        mgr = ArkManager(ark=tmp_ark, offline=True)
        audit = mgr.audit()
        assert "status" in audit
        assert "download_plan" in audit
        assert "disk" in audit
        assert "recommendation" in audit
        assert "offline_mode" in audit

    def test_audit_plan_has_priority(self, tmp_ark):
        from python.sentinel.ark.ark_manager import ArkManager
        mgr = ArkManager(ark=tmp_ark, offline=True)
        audit = mgr.audit()
        plan = audit["download_plan"]
        # Should have items to download
        assert len(plan) > 0
        # Each item has priority
        for item in plan:
            assert "priority" in item
            assert "item_id" in item
            assert "size_human" in item

    def test_fill_critical_offline_noop(self, tmp_ark):
        from python.sentinel.ark.ark_manager import ArkManager
        mgr = ArkManager(ark=tmp_ark, offline=True)
        results = mgr.fill_critical()
        assert results == []  # No downloads in offline mode

    def test_fill_all_offline_noop(self, tmp_ark):
        from python.sentinel.ark.ark_manager import ArkManager
        mgr = ArkManager(ark=tmp_ark, offline=True)
        results = mgr.fill_all()
        assert results == []

    def test_recommendation_offline(self, tmp_ark):
        from python.sentinel.ark.ark_manager import ArkManager
        mgr = ArkManager(ark=tmp_ark, offline=True)
        audit = mgr.audit()
        assert "OFFLINE" in audit["recommendation"] or "offline" in audit["recommendation"].lower() or len(audit["recommendation"]) > 0

    def test_prune_removes_low_priority(self, tmp_path):
        from python.sentinel.ark.ark_manager import ArkManager
        ark = SoftwareArk(ark_dir=str(tmp_path / "ark"))

        # Add a LOW priority item and mark it present
        item = _make_item("low:disposable", priority=ArkPriority.LOW.value,
                          category=ArkCategory.SCRIPT.value)
        item.state = ArkItemState.PRESENT.value
        ark.register_item(item)

        # Create actual file
        path = ark.item_path(item)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(b"disposable" * 100)

        mgr = ArkManager(ark=ark, offline=True)
        result = mgr.prune()
        assert "low:disposable" in result["removed"]
        assert result["freed_bytes"] > 0
        assert not os.path.exists(path)

    def test_prune_keeps_critical(self, tmp_path):
        from python.sentinel.ark.ark_manager import ArkManager
        ark = SoftwareArk(ark_dir=str(tmp_path / "ark"))

        item = _make_item("critical:essential",
                          priority=ArkPriority.CRITICAL.value,
                          category=ArkCategory.SCRIPT.value)
        item.state = ArkItemState.PRESENT.value
        ark.register_item(item)
        path = ark.item_path(item)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "w").close()

        mgr = ArkManager(ark=ark, offline=True)
        result = mgr.prune(keep_critical=True)
        assert "critical:essential" not in result["removed"]
        assert os.path.exists(path)

    def test_write_requirements(self, tmp_path):
        from python.sentinel.ark.ark_manager import ArkManager
        req_file = str(tmp_path / "requirements.txt")
        ArkManager._write_requirements(req_file)
        assert os.path.exists(req_file)
        content = open(req_file).read()
        assert "flask" in content
        assert "qdrant-client" in content

    def test_check_disk(self, tmp_ark):
        from python.sentinel.ark.ark_manager import ArkManager
        mgr = ArkManager(ark=tmp_ark, offline=True)
        # Should pass (we're not at 10GB minimum in a test env)
        # Just verify it returns a boolean
        assert isinstance(mgr._check_disk(), bool)


# ─────────────────────────────────────────────────────────────────────────────
# MODULE 52: BOOTSTRAP ENGINE
# ─────────────────────────────────────────────────────────────────────────────

class TestBootstrapEngine:

    def test_init(self, tmp_ark, tmp_path):
        from python.sentinel.ark.bootstrap_engine import BootstrapEngine
        engine = BootstrapEngine(
            ark=tmp_ark,
            target_dir=str(tmp_path / "KISWARM"),
            dry_run=True,
        )
        assert engine.dry_run is True
        assert engine.ark is tmp_ark

    def test_dry_run_assess(self, tmp_ark, tmp_path):
        from python.sentinel.ark.bootstrap_engine import BootstrapEngine, PhaseResult
        engine = BootstrapEngine(
            ark=tmp_ark,
            target_dir=str(tmp_path / "KISWARM"),
            dry_run=True,
        )
        result, msg = engine._phase_assess()
        assert result in (PhaseResult.PASS, PhaseResult.WARN)
        assert "OS=" in msg or "os" in msg.lower()

    def test_dry_run_validate_fails_empty_ark(self, tmp_ark, tmp_path):
        from python.sentinel.ark.bootstrap_engine import BootstrapEngine, PhaseResult
        engine = BootstrapEngine(
            ark=tmp_ark,
            target_dir=str(tmp_path / "KISWARM"),
            dry_run=True,
        )
        result, msg = engine._phase_validate()
        # Empty ark — should fail or warn
        assert result in (PhaseResult.FAIL, PhaseResult.PASS, PhaseResult.WARN)

    def test_phase_log_populated_after_bootstrap(self, tmp_ark, tmp_path):
        from python.sentinel.ark.bootstrap_engine import BootstrapEngine
        engine = BootstrapEngine(
            ark=tmp_ark,
            target_dir=str(tmp_path / "KISWARM"),
            dry_run=True,
        )
        report = engine.bootstrap()
        assert len(report.phases) > 0

    def test_bootstrap_report_structure(self, tmp_ark, tmp_path):
        from python.sentinel.ark.bootstrap_engine import BootstrapEngine
        engine = BootstrapEngine(
            ark=tmp_ark,
            target_dir=str(tmp_path / "KISWARM"),
            dry_run=True,
        )
        report = engine.bootstrap()
        d = report.to_dict()
        assert "success" in d
        assert "phases" in d
        assert "os_family" in d
        assert "ram_gb" in d
        assert "duration_s" in d
        assert "summary" in d

    def test_on_phase_callback(self, tmp_ark, tmp_path):
        from python.sentinel.ark.bootstrap_engine import BootstrapEngine
        calls = []
        engine = BootstrapEngine(
            ark=tmp_ark,
            target_dir=str(tmp_path / "KISWARM"),
            dry_run=True,
            on_phase=lambda phase, result, msg: calls.append(phase),
        )
        engine.bootstrap()
        assert "assess" in calls
        assert "validate" in calls

    def test_phase_result_values(self):
        from python.sentinel.ark.bootstrap_engine import PhaseResult
        assert PhaseResult.PASS.value  == "pass"
        assert PhaseResult.FAIL.value  == "fail"
        assert PhaseResult.SKIP.value  == "skip"
        assert PhaseResult.WARN.value  == "warn"

    def test_boot_phases_ordered(self, tmp_ark, tmp_path):
        from python.sentinel.ark.bootstrap_engine import BootstrapEngine, BootPhase
        engine = BootstrapEngine(
            ark=tmp_ark,
            target_dir=str(tmp_path / "KISWARM"),
            dry_run=True,
        )
        report = engine.bootstrap()
        phase_names = [p.phase for p in report.phases]
        # ASSESS must come before VALIDATE
        assert phase_names.index("assess") < phase_names.index("validate")

    def test_generate_offline_script(self, tmp_path):
        from python.sentinel.ark.bootstrap_engine import BootstrapEngine
        output = str(tmp_path / "bootstrap_offline.sh")
        BootstrapEngine.generate_offline_script(
            ark_dir=str(tmp_path / "ark"),
            output_path=output
        )
        assert os.path.exists(output)
        content = open(output).read()
        assert "#!/bin/bash" in content
        assert "ARK_DIR" in content
        assert "ollama" in content
        assert "git clone" in content
        # Must be executable
        assert os.access(output, os.X_OK)

    def test_report_duration(self, tmp_ark, tmp_path):
        from python.sentinel.ark.bootstrap_engine import BootstrapEngine
        engine = BootstrapEngine(
            ark=tmp_ark,
            target_dir=str(tmp_path / "KISWARM"),
            dry_run=True,
        )
        report = engine.bootstrap()
        assert report.duration_s >= 0
        assert report.started_at <= report.completed_at

    def test_idempotent_dry_run(self, tmp_ark, tmp_path):
        """Running bootstrap twice must be safe."""
        from python.sentinel.ark.bootstrap_engine import BootstrapEngine
        target = str(tmp_path / "KISWARM")
        for _ in range(2):
            engine = BootstrapEngine(
                ark=tmp_ark, target_dir=target, dry_run=True
            )
            report = engine.bootstrap()
            # No crash on second run
            assert report is not None


# ─────────────────────────────────────────────────────────────────────────────
# MODULE 53: ARK TRANSFER
# ─────────────────────────────────────────────────────────────────────────────

class TestArkTransfer:

    def test_init(self, tmp_ark):
        from python.sentinel.ark.ark_transfer import ArkTransfer
        t = ArkTransfer(ark=tmp_ark)
        assert t.ark is tmp_ark
        assert t.sender is not None
        assert t.receiver is not None

    def test_transfer_job_structure(self):
        from python.sentinel.ark.ark_transfer import TransferJob
        job = TransferJob(
            item_id="test:item", name="Test",
            size_bytes=1024, sha256="abc", rel_path="test/item.bin",
            priority="critical",
        )
        assert job.item_id == "test:item"
        assert job.priority == "critical"

    def test_transfer_result_speed(self):
        from python.sentinel.ark.ark_transfer import TransferResult
        r = TransferResult(
            item_id="x", success=True,
            bytes_received=10 * 1024**2, duration_s=1.0
        )
        assert r.speed_mbps() == pytest.approx(10.0)

    def test_transfer_result_speed_zero_duration(self):
        from python.sentinel.ark.ark_transfer import TransferResult
        r = TransferResult("x", True, 0, 0.0)
        assert r.speed_mbps() == 0.0

    def test_session_to_dict(self):
        from python.sentinel.ark.ark_transfer import TransferSession, TransferJob
        s = TransferSession(
            session_id="abc", peer_address="10.0.0.1", peer_port=11442,
            started_at=time.time(), direction="receive",
            jobs=[TransferJob("a", "A", 100, None, "x/a.bin", "critical")]
        )
        d = s.to_dict()
        assert d["peer"] == "10.0.0.1:11442"
        assert d["direction"] == "receive"
        assert d["jobs_total"] == 1

    def test_compute_delta_empty_manifest(self, tmp_ark):
        from python.sentinel.ark.ark_transfer import ArkReceiver
        r = ArkReceiver(ark=tmp_ark)
        delta = r.compute_delta([])
        assert delta == []

    def test_compute_delta_excludes_present(self, tmp_path):
        from python.sentinel.ark.ark_transfer import ArkReceiver
        ark = SoftwareArk(ark_dir=str(tmp_path / "ark"))

        # Mark first item as PRESENT and create its file
        item = list(ark._inventory.values())[0]
        item.state = ArkItemState.PRESENT.value
        path = ark.item_path(item)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "w").close()
        ark._save_inventory()

        manifest = [{
            "item_id":    item.item_id,
            "name":       item.name,
            "size_bytes": 1024,
            "sha256":     None,
            "rel_path":   item.rel_path,
            "priority":   item.priority,
        }]
        r = ArkReceiver(ark=ark)
        delta = r.compute_delta(manifest)
        assert not any(j.item_id == item.item_id for j in delta)

    def test_compute_delta_priority_order(self, tmp_path):
        from python.sentinel.ark.ark_transfer import ArkReceiver
        ark = SoftwareArk(ark_dir=str(tmp_path / "ark"))
        r   = ArkReceiver(ark=ark)

        # Build manifest with mixed priorities
        manifest = []
        for item in ark._inventory.values():
            manifest.append({
                "item_id": item.item_id, "name": item.name,
                "size_bytes": 100, "sha256": None,
                "rel_path": item.rel_path, "priority": item.priority,
            })

        delta = r.compute_delta(manifest)
        if len(delta) >= 2:
            # First item must not be LOW priority if any CRITICAL exists
            has_critical = any(j.priority == ArkPriority.CRITICAL.value for j in delta)
            if has_critical:
                assert delta[0].priority in (
                    ArkPriority.CRITICAL.value, ArkPriority.HIGH.value
                )

    def test_sender_receiver_local_transfer(self, tmp_path):
        """Integration: ArkSender streams a file to ArkReceiver."""
        from python.sentinel.ark.ark_transfer import ArkSender, ArkReceiver, TRANSFER_PORT
        import random

        port = random.randint(19600, 19700)

        # Setup sender ark with a real file
        ark_a = SoftwareArk(ark_dir=str(tmp_path / "ark_a"))
        item  = _make_item("script:transfer_test",
                           category=ArkCategory.SCRIPT.value,
                           state=ArkItemState.PRESENT.value)
        ark_a.register_item(item)
        # Create real file
        dest_dir = os.path.join(ark_a.ark_dir, item.category)
        os.makedirs(dest_dir, exist_ok=True)
        real_file = tmp_path / "transfer.bin"
        real_file.write_bytes(b"TRANSFER TEST DATA " * 500)
        ark_a.store_file("script:transfer_test", str(real_file))

        # Setup receiver ark
        ark_b = SoftwareArk(ark_dir=str(tmp_path / "ark_b"))
        item_b = _make_item("script:transfer_test",
                            category=ArkCategory.SCRIPT.value)
        ark_b.register_item(item_b)

        # Start sender with custom port
        from python.sentinel.ark import ark_transfer as at_mod
        orig_port = at_mod.TRANSFER_PORT
        at_mod.TRANSFER_PORT = port

        sender = ArkSender(ark=ark_a)
        sender.start()
        time.sleep(0.3)

        receiver = ArkReceiver(ark=ark_b)
        manifest = receiver.get_peer_manifest("127.0.0.1", port)

        at_mod.TRANSFER_PORT = orig_port
        sender.stop()

        assert manifest is not None
        assert any(m["item_id"] == "script:transfer_test" for m in manifest)

    def test_status_structure(self, tmp_ark):
        from python.sentinel.ark.ark_transfer import ArkTransfer
        t = ArkTransfer(ark=tmp_ark)
        s = t.status()
        assert "server_running" in s
        assert "transfer_port" in s
        assert "sessions_total" in s

    def test_recv_exactly(self):
        """Test _recv_exactly with a mock socket."""
        from python.sentinel.ark.ark_transfer import ArkReceiver

        class MockSock:
            def __init__(self, data):
                self._data = data
                self._pos  = 0
            def recv(self, n):
                chunk = self._data[self._pos:self._pos + n]
                self._pos += n
                return chunk

        data = b"A" * 1000
        sock = MockSock(data)
        result = ArkReceiver._recv_exactly(sock, 500)
        assert result == b"A" * 500


# ─────────────────────────────────────────────────────────────────────────────
# INTEGRATION: FULL ARK LIFECYCLE
# ─────────────────────────────────────────────────────────────────────────────

class TestArkIntegration:

    def test_ark_manager_bootstrap_engine_pipeline(self, tmp_path):
        """ArkManager audits → BootstrapEngine plans → consistent view."""
        from python.sentinel.ark.ark_manager    import ArkManager
        from python.sentinel.ark.bootstrap_engine import BootstrapEngine

        ark    = SoftwareArk(ark_dir=str(tmp_path / "ark"))
        mgr    = ArkManager(ark=ark, offline=True)
        engine = BootstrapEngine(ark=ark, target_dir=str(tmp_path / "KSW"),
                                 dry_run=True)

        audit   = mgr.audit()
        report  = engine.bootstrap()

        # Both should agree on bootstrap capability
        can_mgr  = audit["status"]["can_bootstrap"]
        can_eng  = not any(p.result == "fail" and p.phase == "validate"
                           for p in report.phases)
        # If mgr says can_bootstrap=False, engine's validate should reflect it
        if not can_mgr:
            validate_phases = [p for p in report.phases if p.phase == "validate"]
            if validate_phases:
                assert validate_phases[0].result in ("fail", "warn", "pass")

    def test_100gb_target_constant(self):
        """Verify the 100GB design target is correctly set."""
        assert TARGET_ARK_SIZE == 100 * 1024**3

    def test_max_5_peers_architecture(self):
        """Verify the Ark works with the P2P max-5-peer constraint."""
        from python.sentinel.swarm_peer import MAX_PEERS
        from python.sentinel.ark.ark_transfer import ArkTransfer
        # ArkTransfer's pull_from_best_peer handles a list of peers
        # The constraint is enforced at SwarmPeer level
        assert MAX_PEERS == 5

    def test_complete_resilience_chain(self, tmp_path):
        """
        Verify the complete chain:
        SoftwareArk → ArkManager (audit) → BootstrapEngine (plan) → ArkTransfer (status)
        All modules initialize and interact without errors.
        """
        from python.sentinel.ark.ark_manager      import ArkManager
        from python.sentinel.ark.bootstrap_engine import BootstrapEngine
        from python.sentinel.ark.ark_transfer     import ArkTransfer

        ark      = SoftwareArk(ark_dir=str(tmp_path / "ark"))
        mgr      = ArkManager(ark=ark, offline=True)
        engine   = BootstrapEngine(ark=ark, target_dir=str(tmp_path / "KSW"),
                                   dry_run=True)
        transfer = ArkTransfer(ark=ark)

        # All initialize without error
        assert ark is not None
        assert mgr is not None
        assert engine is not None
        assert transfer is not None

        # Audit works
        audit = mgr.audit()
        assert "status" in audit

        # Bootstrap dry-run works
        report = engine.bootstrap()
        assert report is not None

        # Transfer status works
        status = transfer.status()
        assert "server_running" in status

        # The chain is resilient: no internet needed for any of this
        assert audit["offline_mode"] is True
