#!/usr/bin/env python3
"""
KISWARM v1.1 - Tool Injection Proxy
Flask-based REST API for auto-tool injection (port 11435)
Endpoints: /health  /tools  /execute  /register-tool  /status
"""

import os
import sys
import json
import logging
import datetime
import subprocess
from pathlib import Path

try:
    from flask import Flask, request, jsonify
    from flask_cors import CORS
except ImportError:
    subprocess.run([sys.executable, "-m", "pip", "install",
                    "flask", "flask-cors"], check=True)
    from flask import Flask, request, jsonify
    from flask_cors import CORS

# ── Configuration ────────────────────────────────────────────────────────────
KISWARM_HOME = os.environ.get("KISWARM_HOME", os.path.expanduser("~"))
TOOLS_DIR    = os.path.join(KISWARM_HOME, "KISWARM", "central_tools_pool")
LOGS_DIR     = os.path.join(KISWARM_HOME, "logs")
CONFIG_FILE  = os.path.join(KISWARM_HOME, "governance_config.json")

os.makedirs(TOOLS_DIR, exist_ok=True)
os.makedirs(LOGS_DIR,  exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(LOGS_DIR, "tool_proxy.log")),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

app   = Flask(__name__)
CORS(app)
state = {"start": datetime.datetime.now(), "requests": 0, "executions": 0}

# ── Helpers ──────────────────────────────────────────────────────────────────

def load_config() -> dict:
    """Load governance config. Returns safe defaults on missing/malformed file.
    Uses specific exceptions so real errors are never silently swallowed."""
    _defaults = {"governance_mode": "active", "tool_injection_enabled": True, "audit_logging": True}
    if not os.path.exists(CONFIG_FILE):
        return _defaults
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError(f"Expected JSON object, got {type(data).__name__}")
        return data
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("governance_config.json malformed: %s — using defaults", exc)
    except OSError as exc:
        logger.warning("Cannot read governance_config.json: %s — using defaults", exc)
    return _defaults

def audit(action: str, details: dict):
    cfg = load_config()
    if cfg.get("audit_logging", True):
        entry = {"timestamp": datetime.datetime.now().isoformat(),
                 "action": action, **details}
        logger.info("AUDIT: %s", json.dumps(entry))

def get_tools():
    tools = []
    for f in Path(TOOLS_DIR).glob("*.json"):
        try:
            cfg = json.loads(f.read_text())
            tools.append({
                "name":        cfg.get("name", f.stem),
                "description": cfg.get("description", ""),
                "version":     cfg.get("version", "1.0"),
            })
        except Exception:
            pass
    return tools

def safe_name(name: str) -> bool:
    """Reject names with path-traversal characters."""
    return bool(name) and ".." not in name and "/" not in name and "\\" not in name

# ── Endpoints ────────────────────────────────────────────────────────────────

@app.route("/health")
def health():
    uptime = (datetime.datetime.now() - state["start"]).total_seconds()
    return jsonify({
        "status": "active",
        "service": "KISWARM-TOOL-PROXY",
        "version": "1.1",
        "port": 11435,
        "uptime_seconds": uptime,
        "requests": state["requests"],
        "executions": state["executions"],
        "timestamp": datetime.datetime.now().isoformat(),
    })

@app.route("/tools")
def list_tools():
    state["requests"] += 1
    tools = get_tools()
    audit("tools_listed", {"count": len(tools)})
    return jsonify({"status": "success", "tools": tools, "total": len(tools)})

@app.route("/execute", methods=["POST"])
def execute():
    state["requests"] += 1
    data = request.get_json() or {}
    tool_name = data.get("tool", "")

    if not safe_name(str(tool_name)):
        return jsonify({"status": "error",
                        "error": "Invalid or missing tool name"}), 400

    cfg = load_config()
    if not cfg.get("tool_injection_enabled", True):
        return jsonify({"status": "error",
                        "error": "Tool injection disabled by governance"}), 403

    params = data.get("params", {})
    state["executions"] += 1
    audit("tool_executed", {"tool": tool_name, "params": params})

    return jsonify({
        "status": "success",
        "tool": tool_name,
        "params": params,
        "governance_validated": True,
        "timestamp": datetime.datetime.now().isoformat(),
    })

@app.route("/register-tool", methods=["POST"])
def register_tool():
    state["requests"] += 1
    data = request.get_json() or {}
    name = data.get("name", "")

    if not safe_name(name):
        return jsonify({"status": "error", "error": "Invalid tool name"}), 400

    tool_file = os.path.join(TOOLS_DIR, f"{name}.json")
    with open(tool_file, "w") as f:
        json.dump(data, f, indent=2)

    audit("tool_registered", {"tool": name})
    return jsonify({"status": "success",
                    "message": f"Tool '{name}' registered"}), 201

@app.route("/status")
def status():
    uptime = (datetime.datetime.now() - state["start"]).total_seconds()
    cfg = load_config()
    return jsonify({
        "system":      "KISWARM-TOOL-PROXY",
        "version":     "1.1",
        "status":      "operational",
        "uptime_seconds": uptime,
        "tools_available": len(get_tools()),
        "governance": {
            "mode":      cfg.get("governance_mode"),
            "injection": cfg.get("tool_injection_enabled"),
            "audit":     cfg.get("audit_logging"),
        },
        "repo": "https://github.com/Baronki2/KISWARM",
    })

@app.errorhandler(404)
def not_found(e):
    return jsonify({"status": "error",
                    "endpoints": ["/health", "/tools", "/execute",
                                  "/register-tool", "/status"]}), 404

# ── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logger.info("═" * 55)
    logger.info("KISWARM v1.1 Tool Injection Proxy starting")
    logger.info("Port: 11435  |  Tools dir: %s", TOOLS_DIR)
    logger.info("Repo: https://github.com/Baronki2/KISWARM")
    logger.info("═" * 55)
    app.run(host="127.0.0.1", port=11435, debug=False, threaded=True)
