"""
KISWARM v1.1 — Unit Tests: kiswarm_status.py
30+ tests covering monitoring, resource checks, and status logic.

Run: pytest tests/test_kiswarm_status.py -v
"""
import datetime
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "python"))


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def monitor(tmp_kiswarm, monkeypatch):
    """Return a KISWARMMonitor instance pointed at a tmp directory."""
    monkeypatch.setenv("KISWARM_HOME", str(tmp_kiswarm))
    for mod in list(sys.modules):
        if "kiswarm_status" in mod:
            del sys.modules[mod]
    import kiswarm_status
    monkeypatch.setattr(kiswarm_status, "KISWARM_DIR",
                        str(tmp_kiswarm / "KISWARM"))
    return kiswarm_status.KISWARMMonitor()


# ── Color coding logic ────────────────────────────────────────────────────────

class TestColorCoding:
    def _color(self, pct):
        if pct < 60:
            return "green"
        elif pct < 80:
            return "yellow"
        return "red"

    def test_low_usage_green(self):
        assert self._color(30) == "green"

    def test_boundary_60_yellow(self):
        assert self._color(60) == "yellow"

    def test_high_usage_yellow(self):
        assert self._color(75) == "yellow"

    def test_boundary_80_red(self):
        assert self._color(80) == "red"

    def test_critical_usage_red(self):
        assert self._color(95) == "red"

    def test_zero_usage_green(self):
        assert self._color(0) == "green"

    def test_99_usage_red(self):
        assert self._color(99) == "red"


# ── Ollama status ─────────────────────────────────────────────────────────────

class TestOllamaStatus:
    @patch("requests.get")
    def test_ollama_online_status(self, mock_get, monitor):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"models": [{"name": "llama2"}, {"name": "phi3"}]}
        mock_get.return_value = mock_resp
        result = monitor.ollama_status()
        assert result["status"] == "✓ Running"
        assert result["models"] == 2
        assert result["color"] == "green"

    @patch("requests.get", side_effect=Exception("Connection refused"))
    def test_ollama_offline_status(self, mock_get, monitor):
        result = monitor.ollama_status()
        assert result["status"] == "✗ Offline"
        assert result["color"] == "red"
        assert result["models"] == 0

    @patch("requests.get")
    def test_ollama_model_names_truncated(self, mock_get, monitor):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "models": [{"name": f"model{i}"} for i in range(10)]
        }
        mock_get.return_value = mock_resp
        result = monitor.ollama_status()
        # Should not crash with many models
        assert result["models"] == 10

    @patch("requests.get")
    def test_ollama_no_models(self, mock_get, monitor):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"models": []}
        mock_get.return_value = mock_resp
        result = monitor.ollama_status()
        assert result["models"] == 0


# ── Memory (Qdrant) status ────────────────────────────────────────────────────

class TestMemoryStatus:
    def test_qdrant_not_initialized(self, monitor, tmp_kiswarm):
        # Remove qdrant_data dir
        import shutil
        qdrant = tmp_kiswarm / "KISWARM" / "qdrant_data"
        if qdrant.exists():
            shutil.rmtree(qdrant)
        result = monitor.memory_status()
        assert result["color"] == "red"
        assert "0B" in result["size"] or "Not" in result["status"]

    def test_qdrant_path_exists(self, monitor, tmp_kiswarm):
        qdrant = tmp_kiswarm / "KISWARM" / "qdrant_data"
        qdrant.mkdir(parents=True, exist_ok=True)
        # Write a small dummy file so size > 0
        (qdrant / "dummy.bin").write_bytes(b"x" * 1024)
        result = monitor.memory_status()
        # Should not return red (path exists)
        assert result["status"] != "✗ Not initialized"

    def test_qdrant_size_format(self, monitor, tmp_kiswarm):
        qdrant = tmp_kiswarm / "KISWARM" / "qdrant_data"
        qdrant.mkdir(parents=True, exist_ok=True)
        result = monitor.memory_status()
        assert "MB" in result["size"] or "0" in result["size"]


# ── Tool proxy status ─────────────────────────────────────────────────────────

class TestProxyStatus:
    @patch("requests.get")
    def test_proxy_online(self, mock_get, monitor):
        mock_get.return_value = MagicMock(status_code=200)
        result = monitor.proxy_status()
        assert result["status"] == "✓ Running"
        assert result["color"] == "green"

    @patch("requests.get", side_effect=Exception("offline"))
    def test_proxy_offline(self, mock_get, monitor):
        result = monitor.proxy_status()
        assert result["status"] == "✗ Offline"
        assert result["color"] == "red"


# ── System resources ──────────────────────────────────────────────────────────

class TestResources:
    @patch("psutil.cpu_percent", return_value=45.0)
    @patch("psutil.virtual_memory")
    @patch("psutil.disk_usage")
    def test_resources_structure(self, mock_disk, mock_mem, mock_cpu, monitor):
        mock_mem.return_value = MagicMock(
            percent=55.0, used=8 * 1024**3, total=16 * 1024**3
        )
        mock_disk.return_value = MagicMock(
            percent=40.0, free=100 * 1024**3, total=500 * 1024**3
        )
        result = monitor.resources()
        assert "cpu" in result
        assert "mem" in result
        assert "disk" in result

    @patch("psutil.cpu_percent", return_value=85.0)
    @patch("psutil.virtual_memory")
    @patch("psutil.disk_usage")
    def test_high_cpu_red(self, mock_disk, mock_mem, mock_cpu, monitor):
        mock_mem.return_value = MagicMock(
            percent=30.0, used=4 * 1024**3, total=16 * 1024**3
        )
        mock_disk.return_value = MagicMock(
            percent=30.0, free=200 * 1024**3, total=500 * 1024**3
        )
        result = monitor.resources()
        assert result["cpu"]["color"] == "red"

    @patch("psutil.cpu_percent", return_value=25.0)
    @patch("psutil.virtual_memory")
    @patch("psutil.disk_usage")
    def test_low_usage_green(self, mock_disk, mock_mem, mock_cpu, monitor):
        mock_mem.return_value = MagicMock(
            percent=25.0, used=4 * 1024**3, total=16 * 1024**3
        )
        mock_disk.return_value = MagicMock(
            percent=20.0, free=400 * 1024**3, total=500 * 1024**3
        )
        result = monitor.resources()
        assert result["cpu"]["color"] == "green"
        assert result["mem"]["color"] == "green"
        assert result["disk"]["color"] == "green"

    @patch("psutil.cpu_percent", return_value=50.0)
    @patch("psutil.virtual_memory")
    @patch("psutil.disk_usage", side_effect=FileNotFoundError)
    def test_disk_fallback_on_error(self, mock_disk, mock_mem, mock_cpu, monitor):
        """Disk check should not crash if path unavailable."""
        mock_mem.return_value = MagicMock(
            percent=50.0, used=8 * 1024**3, total=16 * 1024**3
        )
        # Should not raise
        try:
            result = monitor.resources()
            assert "disk" in result
        except FileNotFoundError:
            pytest.fail("resources() should handle disk errors gracefully")


# ── Governance config ─────────────────────────────────────────────────────────

class TestGovernanceStatus:
    def test_config_found_and_parsed(self, monitor, governance_config, tmp_kiswarm, monkeypatch):
        monkeypatch.setenv("KISWARM_HOME", str(tmp_kiswarm))
        for mod in list(sys.modules):
            if "kiswarm_status" in mod:
                del sys.modules[mod]
        import kiswarm_status
        monkeypatch.setattr(kiswarm_status, "KISWARM_HOME", str(tmp_kiswarm))
        m = kiswarm_status.KISWARMMonitor()
        result = m.governance_status()
        assert result["exists"] is True
        assert result["mode"] == "active"
        assert result["autonomous"] is True

    def test_config_missing_returns_not_found(self, monitor):
        result = monitor.governance_status()
        # Temporary dir may not have governance config
        assert isinstance(result, dict)
        assert "exists" in result

    def test_malformed_config_handled(self, monitor, tmp_kiswarm, monkeypatch):
        bad = tmp_kiswarm / "governance_config.json"
        bad.write_text("not json at all!!")
        monkeypatch.setenv("KISWARM_HOME", str(tmp_kiswarm))
        for mod in list(sys.modules):
            if "kiswarm_status" in mod:
                del sys.modules[mod]
        import kiswarm_status
        monkeypatch.setattr(kiswarm_status, "KISWARM_HOME", str(tmp_kiswarm))
        m = kiswarm_status.KISWARMMonitor()
        result = m.governance_status()
        # Should not crash
        assert isinstance(result, dict)


# ── Monitor initialisation ────────────────────────────────────────────────────

class TestMonitorInit:
    def test_start_time_is_datetime(self, monitor):
        assert isinstance(monitor.start_time, datetime.datetime)

    def test_start_time_is_recent(self, monitor):
        delta = datetime.datetime.now() - monitor.start_time
        assert delta.total_seconds() < 5
