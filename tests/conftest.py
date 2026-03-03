"""
KISWARM v1.1 — Test Fixtures (conftest.py)
Shared fixtures for all test modules.
"""
import json
import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest


# ── Directory fixtures ────────────────────────────────────────────────────────

@pytest.fixture
def tmp_kiswarm(tmp_path):
    """Create a full temporary KISWARM directory tree."""
    base = tmp_path / "home"
    kiswarm = base / "KISWARM"
    dirs = [
        kiswarm / "qdrant_data",
        kiswarm / "central_tools_pool",
        kiswarm / "onecontext_system",
        kiswarm / "mcp_servers",
        kiswarm / "logs",
        base / "logs",
        base / "backups",
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)
    return base


@pytest.fixture
def tmp_kiswarm_dir(tmp_kiswarm):
    """Return just the KISWARM sub-directory."""
    return tmp_kiswarm / "KISWARM"


# ── Config fixtures ───────────────────────────────────────────────────────────

@pytest.fixture
def governance_config(tmp_kiswarm):
    """Write a valid governance_config.json and return its path."""
    cfg = {
        "system_name": "KISWARM",
        "version": "1.1",
        "governance_mode": "active",
        "autonomous_operation": True,
        "auto_restart_services": True,
        "tool_injection_enabled": True,
        "audit_logging": True,
        "backup_retention_days": 30,
        "log_retention_days": 60,
        "ollama_port": 11434,
        "tool_proxy_port": 11435,
    }
    cfg_path = tmp_kiswarm / "governance_config.json"
    cfg_path.write_text(json.dumps(cfg, indent=2))
    return cfg_path


@pytest.fixture
def disabled_injection_config(tmp_kiswarm):
    """Governance config with tool injection disabled."""
    cfg = {
        "governance_mode": "active",
        "tool_injection_enabled": False,
        "audit_logging": True,
    }
    cfg_path = tmp_kiswarm / "governance_config.json"
    cfg_path.write_text(json.dumps(cfg, indent=2))
    return cfg_path


# ── Tool fixtures ─────────────────────────────────────────────────────────────

@pytest.fixture
def sample_tool(tmp_kiswarm_dir):
    """Write a sample tool JSON file into central_tools_pool."""
    tool = {
        "name": "echo_tool",
        "description": "Echoes the input back",
        "version": "1.0",
        "enabled": True,
    }
    tool_path = tmp_kiswarm_dir / "central_tools_pool" / "echo_tool.json"
    tool_path.write_text(json.dumps(tool, indent=2))
    return tool_path


# ── Mock HTTP responses ───────────────────────────────────────────────────────

@pytest.fixture
def mock_ollama_running():
    """Mock requests.get to simulate a running Ollama server."""
    mock = MagicMock()
    mock.status_code = 200
    mock.json.return_value = {
        "models": [
            {"name": "llama2", "size": 4_000_000_000},
            {"name": "phi3:mini", "size": 2_600_000_000},
        ]
    }
    return mock


@pytest.fixture
def mock_ollama_offline():
    """Mock requests.get to simulate an offline Ollama server."""
    import requests as req
    mock = MagicMock(side_effect=req.exceptions.ConnectionError("offline"))
    return mock


@pytest.fixture
def mock_proxy_running():
    """Mock requests.get to simulate a running tool proxy."""
    mock = MagicMock()
    mock.status_code = 200
    mock.json.return_value = {"status": "active", "port": 11435}
    return mock


# ── Flask test client ─────────────────────────────────────────────────────────

@pytest.fixture
def proxy_app(tmp_kiswarm, governance_config, monkeypatch):
    """
    Return a Flask test client for tool_proxy.py with env vars pointing
    at the temporary KISWARM directory.
    """
    monkeypatch.setenv("KISWARM_HOME", str(tmp_kiswarm))

    # We need to import after patching the env
    import importlib
    import sys

    # Remove any cached module so env change takes effect
    for mod in list(sys.modules.keys()):
        if "tool_proxy" in mod:
            del sys.modules[mod]

    sys.path.insert(0, str(Path(__file__).parent.parent / "python"))
    import tool_proxy  # noqa: PLC0415

    tool_proxy.app.config["TESTING"] = True
    with tool_proxy.app.test_client() as client:
        yield client
