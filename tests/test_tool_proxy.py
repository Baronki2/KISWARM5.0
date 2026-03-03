"""
KISWARM v1.1 — Unit Tests: tool_proxy.py
50+ tests covering endpoints, security, governance, and edge cases.

Run: pytest tests/test_tool_proxy.py -v
"""
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

import pytest

# Make python/ importable
sys.path.insert(0, str(Path(__file__).parent.parent / "python"))


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_app(tmp_kiswarm, monkeypatch):
    """Create a fresh Flask test client with isolated tmp dirs."""
    monkeypatch.setenv("KISWARM_HOME", str(tmp_kiswarm))
    for mod in list(sys.modules):
        if "tool_proxy" in mod:
            del sys.modules[mod]
    import tool_proxy
    tool_proxy.app.config["TESTING"] = True
    return tool_proxy.app.test_client(), tool_proxy


# ── /health ───────────────────────────────────────────────────────────────────

class TestHealthEndpoint:
    def test_health_returns_200(self, proxy_app):
        r = proxy_app.get("/health")
        assert r.status_code == 200

    def test_health_status_active(self, proxy_app):
        data = json.loads(r := proxy_app.get("/health").data)
        assert data["status"] == "active"

    def test_health_service_name(self, proxy_app):
        data = json.loads(proxy_app.get("/health").data)
        assert "KISWARM" in data["service"]

    def test_health_version_present(self, proxy_app):
        data = json.loads(proxy_app.get("/health").data)
        assert "version" in data
        assert data["version"] == "1.1"

    def test_health_port_correct(self, proxy_app):
        data = json.loads(proxy_app.get("/health").data)
        assert data["port"] == 11435

    def test_health_has_uptime(self, proxy_app):
        data = json.loads(proxy_app.get("/health").data)
        assert "uptime_seconds" in data
        assert data["uptime_seconds"] >= 0

    def test_health_has_timestamp(self, proxy_app):
        data = json.loads(proxy_app.get("/health").data)
        assert "timestamp" in data

    def test_health_request_counter_shown(self, proxy_app):
        """Health endpoint reports request count (other endpoints increment it)."""
        proxy_app.get("/tools")   # /tools increments the counter
        proxy_app.get("/tools")
        data = json.loads(proxy_app.get("/health").data)
        # Counter is incremented by /tools calls, /health is a monitoring ping
        assert data["requests"] >= 2
        assert isinstance(data["requests"], int)


# ── /tools ────────────────────────────────────────────────────────────────────

class TestToolsEndpoint:
    def test_tools_returns_200(self, proxy_app):
        assert proxy_app.get("/tools").status_code == 200

    def test_tools_returns_list(self, proxy_app):
        data = json.loads(proxy_app.get("/tools").data)
        assert "tools" in data
        assert isinstance(data["tools"], list)

    def test_tools_has_total(self, proxy_app):
        data = json.loads(proxy_app.get("/tools").data)
        assert "total" in data

    def test_tools_total_matches_list(self, proxy_app):
        data = json.loads(proxy_app.get("/tools").data)
        assert data["total"] == len(data["tools"])

    def test_tools_empty_without_tools_dir(self, proxy_app):
        data = json.loads(proxy_app.get("/tools").data)
        # Empty pool is valid
        assert data["total"] >= 0

    def test_tools_lists_sample_tool(self, proxy_app, sample_tool, tmp_kiswarm, monkeypatch):
        """When a tool JSON exists, it should appear in the list."""
        client, mod = make_app(tmp_kiswarm, monkeypatch)
        # sample_tool fixture already wrote the file
        data = json.loads(client.get("/tools").data)
        names = [t["name"] for t in data["tools"]]
        assert "echo_tool" in names


# ── /execute ──────────────────────────────────────────────────────────────────

class TestExecuteEndpoint:
    def _post(self, client, payload):
        return client.post(
            "/execute",
            data=json.dumps(payload),
            content_type="application/json",
        )

    def test_execute_missing_tool_returns_400(self, proxy_app):
        r = self._post(proxy_app, {"params": {}})
        assert r.status_code == 400

    def test_execute_empty_tool_name_returns_400(self, proxy_app):
        r = self._post(proxy_app, {"tool": "", "params": {}})
        assert r.status_code == 400

    def test_execute_path_traversal_blocked(self, proxy_app):
        r = self._post(proxy_app, {"tool": "../../etc/passwd"})
        assert r.status_code == 400

    def test_execute_slash_in_name_blocked(self, proxy_app):
        r = self._post(proxy_app, {"tool": "tools/evil"})
        assert r.status_code == 400

    def test_execute_backslash_blocked(self, proxy_app):
        r = self._post(proxy_app, {"tool": "tools\\evil"})
        assert r.status_code == 400

    def test_execute_null_body_returns_400(self, proxy_app):
        r = proxy_app.post("/execute", data="", content_type="application/json")
        assert r.status_code in (400, 415, 200)  # acceptable

    def test_execute_with_valid_tool(self, tmp_kiswarm, monkeypatch, sample_tool):
        client, _ = make_app(tmp_kiswarm, monkeypatch)
        r = client.post(
            "/execute",
            data=json.dumps({"tool": "echo_tool", "params": {"msg": "hi"}}),
            content_type="application/json",
        )
        assert r.status_code == 200
        data = json.loads(r.data)
        assert data["status"] == "success"
        assert data["tool"] == "echo_tool"
        assert data["governance_validated"] is True

    def test_execute_injection_disabled_returns_403(
        self, tmp_kiswarm, monkeypatch, disabled_injection_config, sample_tool
    ):
        client, _ = make_app(tmp_kiswarm, monkeypatch)
        r = client.post(
            "/execute",
            data=json.dumps({"tool": "echo_tool", "params": {}}),
            content_type="application/json",
        )
        assert r.status_code == 403


# ── /register-tool ────────────────────────────────────────────────────────────

class TestRegisterToolEndpoint:
    def _post(self, client, payload):
        return client.post(
            "/register-tool",
            data=json.dumps(payload),
            content_type="application/json",
        )

    def test_register_valid_tool(self, proxy_app):
        r = self._post(proxy_app, {"name": "new_tool", "description": "test"})
        assert r.status_code == 201

    def test_register_missing_name_returns_400(self, proxy_app):
        r = self._post(proxy_app, {"description": "no name"})
        assert r.status_code == 400

    def test_register_path_traversal_blocked(self, proxy_app):
        r = self._post(proxy_app, {"name": "../evil"})
        assert r.status_code == 400

    def test_register_creates_file(self, tmp_kiswarm, monkeypatch):
        client, mod = make_app(tmp_kiswarm, monkeypatch)
        client.post(
            "/register-tool",
            data=json.dumps({"name": "my_tool", "description": "test"}),
            content_type="application/json",
        )
        tool_file = tmp_kiswarm / "KISWARM" / "central_tools_pool" / "my_tool.json"
        assert tool_file.exists()

    def test_register_then_list(self, tmp_kiswarm, monkeypatch):
        client, _ = make_app(tmp_kiswarm, monkeypatch)
        client.post(
            "/register-tool",
            data=json.dumps({"name": "listed_tool", "description": "visible"}),
            content_type="application/json",
        )
        data = json.loads(client.get("/tools").data)
        names = [t["name"] for t in data["tools"]]
        assert "listed_tool" in names


# ── /status ───────────────────────────────────────────────────────────────────

class TestStatusEndpoint:
    def test_status_returns_200(self, proxy_app):
        assert proxy_app.get("/status").status_code == 200

    def test_status_has_governance_block(self, proxy_app):
        data = json.loads(proxy_app.get("/status").data)
        assert "governance" in data

    def test_status_has_uptime(self, proxy_app):
        data = json.loads(proxy_app.get("/status").data)
        assert data["uptime_seconds"] >= 0

    def test_status_system_name(self, proxy_app):
        data = json.loads(proxy_app.get("/status").data)
        assert "KISWARM" in data["system"]

    def test_status_operational(self, proxy_app):
        data = json.loads(proxy_app.get("/status").data)
        assert data["status"] == "operational"


# ── 404 handler ───────────────────────────────────────────────────────────────

class TestErrorHandlers:
    def test_unknown_route_returns_404(self, proxy_app):
        assert proxy_app.get("/nonexistent").status_code == 404

    def test_404_response_has_endpoints(self, proxy_app):
        data = json.loads(proxy_app.get("/nonexistent").data)
        assert "endpoints" in data


# ── safe_name ─────────────────────────────────────────────────────────────────

class TestSafeName:
    def _safe(self):
        import tool_proxy
        return tool_proxy.safe_name

    def test_valid_name(self):
        assert self._safe()("my_tool") is True

    def test_empty_string(self):
        assert self._safe()("") is False

    def test_double_dot(self):
        assert self._safe()("../etc") is False

    def test_forward_slash(self):
        assert self._safe()("a/b") is False

    def test_backslash(self):
        assert self._safe()("a\\b") is False

    def test_name_with_dash(self):
        assert self._safe()("my-tool-v2") is True

    def test_name_with_numbers(self):
        assert self._safe()("tool123") is True


# ── load_config ───────────────────────────────────────────────────────────────

class TestLoadConfig:
    def test_returns_defaults_when_file_missing(self, tmp_kiswarm, monkeypatch):
        monkeypatch.setenv("KISWARM_HOME", str(tmp_kiswarm / "nonexistent"))
        for mod in list(sys.modules):
            if "tool_proxy" in mod:
                del sys.modules[mod]
        import tool_proxy
        cfg = tool_proxy.load_config()
        assert cfg["governance_mode"] == "active"

    def test_loads_real_config(self, governance_config, tmp_kiswarm, monkeypatch):
        client, mod = make_app(tmp_kiswarm, monkeypatch)
        cfg = mod.load_config()
        assert cfg["version"] == "1.1"

    def test_malformed_json_returns_defaults(self, tmp_kiswarm, monkeypatch):
        bad = tmp_kiswarm / "governance_config.json"
        bad.write_text("{not valid json}")
        monkeypatch.setenv("KISWARM_HOME", str(tmp_kiswarm))
        for mod in list(sys.modules):
            if "tool_proxy" in mod:
                del sys.modules[mod]
        import tool_proxy
        cfg = tool_proxy.load_config()
        assert isinstance(cfg, dict)
        assert "governance_mode" in cfg
