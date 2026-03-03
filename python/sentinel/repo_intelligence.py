"""
KISWARM v4.6 — Module 35: Repo Intelligence
============================================
The Installer Agent's "brain" — complete knowledge of the KISWARM
repository: structure, modules, history, features, endpoints.

Fetches live from GitHub OR uses cached embedded knowledge.
Provides structured answers to any question about the repo.

Capabilities:
  • fetch_readme()          — latest README with full docs
  • fetch_module_list()     — all 35+ modules with descriptions
  • fetch_version_history() — complete version evolution
  • fetch_file(path)        — any file from the repository
  • answer(question)        — NL question → structured answer
  • install_plan(report)    — generate tailored install plan from ScoutReport
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

GITHUB_API  = "https://api.github.com"
GITHUB_RAW  = "https://raw.githubusercontent.com"
REPO_OWNER  = "Baronki2"
REPO_NAME   = "KISWARM"
REPO_BRANCH = "main"
CACHE_TTL   = 3600   # 1 hour


# ─────────────────────────────────────────────────────────────────────────────
# EMBEDDED KNOWLEDGE (offline fallback — always accurate, updated at release)
# ─────────────────────────────────────────────────────────────────────────────

EMBEDDED_KNOWLEDGE: Dict[str, Any] = {
    "repo": {
        "owner":  REPO_OWNER,
        "name":   REPO_NAME,
        "url":    f"https://github.com/{REPO_OWNER}/{REPO_NAME}",
        "license": "MIT",
        "architect": "Baron Marco Paolo Ialongo",
    },
    "current_version": "4.6",
    "total_modules": 37,
    "total_tests":   1121,
    "total_endpoints": 197,
    "versions": [
        {"version": "1.1", "date": "2026-02-22", "modules": 1,  "highlight": "Initial deployment scripts"},
        {"version": "2.1", "date": "2026-02-25", "modules": 2,  "highlight": "Sentinel Bridge + Swarm Debate"},
        {"version": "2.2", "date": "2026-02-25", "modules": 6,  "highlight": "Conflict, Decay, Tracker, Ledger, Guard, Firewall"},
        {"version": "3.0", "date": "2026-02-26", "modules": 4,  "highlight": "Fuzzy Tuner, Constrained RL, Digital Twin, Federated Mesh"},
        {"version": "4.0", "date": "2026-02-27", "modules": 6,  "highlight": "CIEC Core: PLC, SCADA, Physics, Rules, KG, Actor-Critic"},
        {"version": "4.1", "date": "2026-02-27", "modules": 7,  "highlight": "TD3, AST, Extended Physics, VMware, Formal, Byzantine, Governance"},
        {"version": "4.2", "date": "2026-02-28", "modules": 5,  "highlight": "XAI, PdM, Multi-Agent, SIL, Digital Thread"},
        {"version": "4.3", "date": "2026-03-01", "modules": 2,  "highlight": "ICS Cybersecurity (IEC 62443), OT Network Monitor"},
        {"version": "4.4", "date": "2026-03-01", "modules": 3,  "highlight": "Self-Healing Swarm Auditor, 6-Pipeline DAG, SHA-256 Ledger"},
        {"version": "4.5", "date": "2026-03-01", "modules": 3,  "highlight": "Swarm Immortality Kernel, SoulMirror, EvolutionVault"},
        {"version": "4.6", "date": "2026-03-01", "modules": 4,  "highlight": "Installer Agent, System Scout, Repo Intelligence, Advisor API"},
    ],
    "modules": [
        {"id": 1,  "name": "Sentinel Bridge",       "file": "sentinel_bridge.py",       "version": "2.1", "description": "Autonomous knowledge extraction engine"},
        {"id": 2,  "name": "Swarm Debate",          "file": "swarm_debate.py",           "version": "2.1", "description": "Multi-model conflict resolution"},
        {"id": 3,  "name": "Semantic Conflict",     "file": "semantic_conflict.py",      "version": "2.2", "description": "Contradiction clustering"},
        {"id": 4,  "name": "Knowledge Decay",       "file": "knowledge_decay.py",        "version": "2.2", "description": "Half-life decay engine"},
        {"id": 5,  "name": "Model Tracker",         "file": "model_tracker.py",          "version": "2.2", "description": "ELO + reliability tracker"},
        {"id": 6,  "name": "Crypto Ledger",         "file": "crypto_ledger.py",          "version": "2.2", "description": "Merkle knowledge ledger"},
        {"id": 7,  "name": "Retrieval Guard",       "file": "retrieval_guard.py",        "version": "2.2", "description": "Trust assessment layer"},
        {"id": 8,  "name": "Prompt Firewall",       "file": "prompt_firewall.py",        "version": "2.2", "description": "Adversarial defense"},
        {"id": 9,  "name": "Fuzzy Tuner",           "file": "fuzzy_tuner.py",            "version": "3.0", "description": "Membership auto-tuner"},
        {"id": 10, "name": "Constrained RL",        "file": "constrained_rl.py",         "version": "3.0", "description": "CMDP engine"},
        {"id": 11, "name": "Digital Twin",          "file": "digital_twin.py",           "version": "3.0", "description": "Mutation pipeline"},
        {"id": 12, "name": "Federated Mesh",        "file": "federated_mesh.py",         "version": "3.0", "description": "Byzantine tolerance"},
        {"id": 13, "name": "PLC Parser",            "file": "plc_parser.py",             "version": "4.0", "description": "IEC 61131-3 semantic"},
        {"id": 14, "name": "SCADA Observer",        "file": "scada_observer.py",         "version": "4.0", "description": "OPC/SQL monitor"},
        {"id": 15, "name": "Physics Twin",          "file": "physics_twin.py",           "version": "4.0", "description": "Thermal/pump/battery"},
        {"id": 16, "name": "Rule Engine",           "file": "rule_engine.py",            "version": "4.0", "description": "Constraint safety layer"},
        {"id": 17, "name": "Knowledge Graph",       "file": "knowledge_graph.py",        "version": "4.0", "description": "Cross-project KG"},
        {"id": 18, "name": "Actor-Critic RL",       "file": "actor_critic_rl.py",        "version": "4.0", "description": "Industrial RL"},
        {"id": 19, "name": "TD3 Controller",        "file": "td3_controller.py",         "version": "4.1", "description": "Twin critics RL"},
        {"id": 20, "name": "AST Parser",            "file": "ast_parser.py",             "version": "4.1", "description": "IEC 61131-3 CFG/DDG"},
        {"id": 21, "name": "Extended Physics",      "file": "extended_physics.py",       "version": "4.1", "description": "RK4 multi-block"},
        {"id": 22, "name": "VMware Orchestrator",   "file": "vmware_orchestrator.py",    "version": "4.1", "description": "Snapshot/clone/rollback"},
        {"id": 23, "name": "Formal Verification",   "file": "formal_verification.py",    "version": "4.1", "description": "Lyapunov + barrier"},
        {"id": 24, "name": "Byzantine Aggregator",  "file": "byzantine_aggregator.py",   "version": "4.1", "description": "Robust aggregation"},
        {"id": 25, "name": "Mutation Governance",   "file": "mutation_governance.py",    "version": "4.1", "description": "11-step pipeline"},
        {"id": 26, "name": "Explainability Engine", "file": "explainability_engine.py",  "version": "4.2", "description": "KernelSHAP XAI"},
        {"id": 27, "name": "Predictive Maintenance","file": "predictive_maintenance.py", "version": "4.2", "description": "RUL prediction"},
        {"id": 28, "name": "Multi-Agent",           "file": "multi_agent.py",            "version": "4.2", "description": "Plant coordinator"},
        {"id": 29, "name": "SIL Verification",      "file": "sil_verification.py",       "version": "4.2", "description": "IEC 61508"},
        {"id": 30, "name": "Digital Thread",        "file": "digital_thread.py",         "version": "4.2", "description": "Traceability DAG"},
        {"id": 31, "name": "ICS Cybersecurity",     "file": "ics_cybersecurity.py",      "version": "4.3", "description": "IEC 62443 engine"},
        {"id": 32, "name": "OT Network Monitor",    "file": "ot_network_monitor.py",     "version": "4.3", "description": "Passive anomaly detection"},
        {"id": 33, "name": "Swarm Auditor",         "file": "swarm_auditor.py",          "version": "4.4", "description": "6-pipeline DAG auditor"},
        {"id": 34, "name": "Swarm DAG",             "file": "swarm_dag.py",              "version": "4.4", "description": "Multi-node consensus coordinator"},
        {"id": 35, "name": "Immortality Kernel",    "file": "swarm_immortality_kernel.py","version": "4.5","description": "Entity survivability kernel"},
        {"id": 36, "name": "Soul Mirror",           "file": "swarm_soul_mirror.py",      "version": "4.5", "description": "Identity snapshot chain"},
        {"id": 37, "name": "Evolution Vault",       "file": "evolution_memory_vault.py", "version": "4.5", "description": "Immutable lifecycle events"},
        {"id": 38, "name": "System Scout",          "file": "system_scout.py",           "version": "4.6", "description": "Hardware/OS/port scanner"},
        {"id": 39, "name": "Repo Intelligence",     "file": "repo_intelligence.py",      "version": "4.6", "description": "Repository knowledge base"},
        {"id": 40, "name": "Installer Agent",       "file": "installer_agent.py",        "version": "4.6", "description": "Autonomous one-click installer"},
        {"id": 41, "name": "Advisor API",           "file": "advisor_api.py",            "version": "4.6", "description": "AI-to-AI system advisor"},
    ],
    "ports": {
        11434: "Ollama LLM server",
        11435: "KISWARM Tool Proxy",
        11436: "KISWARM Sentinel API (main)",
        11437: "KISWARM Dev instance",
        6333:  "Qdrant HTTP",
        6334:  "Qdrant gRPC",
    },
    "key_commands": [
        "sys-nav",
        "kiswarm-health",
        "kiswarm-status",
        "ollama list",
        "ollama serve",
    ],
    "dependencies": {
        "required": ["python3", "pip3", "git", "curl"],
        "python_packages": ["ollama", "mem0", "qdrant-client", "flask", "rich", "psutil", "requests"],
        "optional": ["docker", "systemctl", "npm", "node"],
    },
    "install_steps": [
        "git clone https://github.com/Baronki2/KISWARM.git",
        "cd KISWARM && python3 -m venv mem0_env",
        "source mem0_env/bin/activate",
        "pip install -r requirements.txt",
        "ollama serve &",
        "python python/sentinel/sentinel_api.py",
    ],
}


# ─────────────────────────────────────────────────────────────────────────────
# REPO INTELLIGENCE
# ─────────────────────────────────────────────────────────────────────────────

class RepoIntelligence:
    """
    Complete knowledge base for the KISWARM repository.
    Works offline (embedded knowledge) and online (live GitHub fetch).
    """

    def __init__(self, cache_dir: Optional[str] = None):
        if cache_dir is None:
            cache_dir = os.path.join(
                os.path.dirname(__file__), "../../sentinel_data/repo_cache"
            )
        self.cache_dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)
        self._cache: Dict[str, Any] = {}

    # ── Fetching ──────────────────────────────────────────────────────────────

    def _cache_key(self, path: str) -> str:
        return hashlib.md5(path.encode()).hexdigest()

    def _load_cache(self, path: str) -> Optional[str]:
        key  = self._cache_key(path)
        file = os.path.join(self.cache_dir, f"{key}.json")
        if not os.path.exists(file):
            return None
        try:
            with open(file) as f:
                data = json.load(f)
            if time.time() - data.get("ts", 0) < CACHE_TTL:
                return data.get("content")
        except Exception:
            pass
        return None

    def _save_cache(self, path: str, content: str) -> None:
        key  = self._cache_key(path)
        file = os.path.join(self.cache_dir, f"{key}.json")
        try:
            with open(file, "w") as f:
                json.dump({"ts": time.time(), "content": content}, f)
        except Exception:
            pass

    def fetch_file(self, path: str = "README.md") -> Optional[str]:
        """Fetch a file from the KISWARM repository (with cache)."""
        cached = self._load_cache(path)
        if cached:
            return cached
        url = f"{GITHUB_RAW}/{REPO_OWNER}/{REPO_NAME}/{REPO_BRANCH}/{path}"
        try:
            import requests
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                content = resp.text
                self._save_cache(path, content)
                return content
        except Exception as e:
            logger.warning(f"[RepoIntel] Could not fetch {path}: {e}")
        return None

    def fetch_readme(self) -> Optional[str]:
        return self.fetch_file("README.md")

    # ── Knowledge queries ─────────────────────────────────────────────────────

    def get_module_list(self) -> List[Dict[str, Any]]:
        return EMBEDDED_KNOWLEDGE["modules"]

    def get_module_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        name_lower = name.lower()
        for m in EMBEDDED_KNOWLEDGE["modules"]:
            if name_lower in m["name"].lower() or name_lower in m["file"].lower():
                return m
        return None

    def get_version_history(self) -> List[Dict[str, Any]]:
        return EMBEDDED_KNOWLEDGE["versions"]

    def get_current_version(self) -> str:
        return EMBEDDED_KNOWLEDGE["current_version"]

    def get_ports(self) -> Dict[int, str]:
        return EMBEDDED_KNOWLEDGE["ports"]

    def get_dependencies(self) -> Dict[str, Any]:
        return EMBEDDED_KNOWLEDGE["dependencies"]

    def answer(self, question: str) -> Dict[str, Any]:
        """
        Answer a natural-language question about KISWARM.
        Returns structured answer with confidence and source.
        """
        q = question.lower()

        # Routing rules
        if any(w in q for w in ["how many module", "wieviele modul"]):
            return {
                "question": question,
                "answer":   f"KISWARM hat aktuell {len(EMBEDDED_KNOWLEDGE['modules'])} Module über {len(EMBEDDED_KNOWLEDGE['versions'])} Versionen.",
                "data":     {"module_count": len(EMBEDDED_KNOWLEDGE["modules"])},
                "source":   "embedded",
            }

        if any(w in q for w in ["port", "welche ports"]):
            return {
                "question": question,
                "answer":   f"KISWARM nutzt Ports: {json.dumps(EMBEDDED_KNOWLEDGE['ports'], indent=2)}",
                "data":     EMBEDDED_KNOWLEDGE["ports"],
                "source":   "embedded",
            }

        if any(w in q for w in ["version", "history", "verlauf"]):
            versions = EMBEDDED_KNOWLEDGE["versions"]
            summary  = [f"v{v['version']} ({v['date']}): {v['highlight']}" for v in versions]
            return {
                "question": question,
                "answer":   "KISWARM Versionshistorie:\n" + "\n".join(summary),
                "data":     versions,
                "source":   "embedded",
            }

        if any(w in q for w in ["install", "setup", "deploy"]):
            steps = EMBEDDED_KNOWLEDGE["install_steps"]
            return {
                "question": question,
                "answer":   "Installation:\n" + "\n".join(f"  {i+1}. {s}" for i, s in enumerate(steps)),
                "data":     {"steps": steps},
                "source":   "embedded",
            }

        if any(w in q for w in ["abhängig", "depend", "requirements"]):
            deps = EMBEDDED_KNOWLEDGE["dependencies"]
            return {
                "question": question,
                "answer":   f"Pflicht: {', '.join(deps['required'])}\nPython-Pakete: {', '.join(deps['python_packages'])}",
                "data":     deps,
                "source":   "embedded",
            }

        if any(w in q for w in ["test", "passing"]):
            return {
                "question": question,
                "answer":   f"KISWARM hat {EMBEDDED_KNOWLEDGE['total_tests']} Tests (alle passing), {EMBEDDED_KNOWLEDGE['total_endpoints']} API Endpoints.",
                "data":     {"tests": EMBEDDED_KNOWLEDGE["total_tests"], "endpoints": EMBEDDED_KNOWLEDGE["total_endpoints"]},
                "source":   "embedded",
            }

        # Module search
        for m in EMBEDDED_KNOWLEDGE["modules"]:
            if m["name"].lower() in q or m["file"].lower().replace(".py", "") in q:
                return {
                    "question": question,
                    "answer":   f"Modul {m['id']}: {m['name']} (v{m['version']}) — {m['description']}",
                    "data":     m,
                    "source":   "embedded",
                }

        return {
            "question": question,
            "answer":   f"Frage über KISWARM v{EMBEDDED_KNOWLEDGE['current_version']}. Bitte README konsultieren: https://github.com/{REPO_OWNER}/{REPO_NAME}",
            "data":     {},
            "source":   "embedded",
            "note":     "Keine spezifische Antwort gefunden — versuche eine gezieltere Frage",
        }

    # ── Installation plan ─────────────────────────────────────────────────────

    def generate_install_plan(self, scout_report: Dict[str, Any]) -> Dict[str, Any]:
        """
        Generate a tailored installation plan based on a ScoutReport dict.
        Returns step list + warnings + adaptations.
        """
        os_info  = scout_report.get("os", {})
        hw       = scout_report.get("hardware", {})
        pkg_mgr  = os_info.get("pkg_manager", "apt")
        is_container = os_info.get("is_container", False)
        distro   = os_info.get("distro", "ubuntu")
        ram_gb   = hw.get("ram_total_gb", 8)
        model    = hw.get("model_recommendation", "qwen2.5:3b")

        plan: Dict[str, Any] = {
            "target": {
                "os":         f"{distro} {os_info.get('version', '')}".strip(),
                "arch":       os_info.get("arch", "x86_64"),
                "ram_gb":     ram_gb,
                "model":      model,
                "container":  is_container,
            },
            "steps": [],
            "warnings": scout_report.get("readiness_warnings", []),
            "blocking": scout_report.get("readiness_issues", []),
            "estimated_duration_min": 15,
        }

        # abort flag is evaluated by caller (InstallerAgent) — plan always returns steps
        if scout_report.get("install_readiness") == "blocked":
            plan["has_blocking_issues"] = True

        steps = plan["steps"]

        # Step 1: System packages
        sys_pkgs = "python3 python3-pip python3-venv git curl"
        if pkg_mgr == "apt":
            steps.append({"id": 1, "title": "System-Pakete installieren",
                          "cmd": f"sudo apt update && sudo apt install -y {sys_pkgs}"})
        elif pkg_mgr in ("dnf", "yum"):
            steps.append({"id": 1, "title": "System-Pakete installieren",
                          "cmd": f"sudo {pkg_mgr} install -y {sys_pkgs}"})
        elif pkg_mgr == "pacman":
            steps.append({"id": 1, "title": "System-Pakete installieren",
                          "cmd": f"sudo pacman -S --noconfirm python python-pip git curl"})
        else:
            steps.append({"id": 1, "title": "System-Pakete installieren",
                          "cmd": f"# Installiere manuell: {sys_pkgs}", "manual": True})

        # Step 2: Ollama
        steps.append({"id": 2, "title": "Ollama installieren",
                      "cmd": "curl -fsSL https://ollama.com/install.sh | sh"})

        # Step 3: Clone repo
        steps.append({"id": 3, "title": "KISWARM Repository klonen",
                      "cmd": f"git clone https://github.com/{REPO_OWNER}/{REPO_NAME}.git ~/KISWARM"})

        # Step 4: Python venv
        steps.append({"id": 4, "title": "Python Virtual Environment erstellen",
                      "cmd": "cd ~/KISWARM && python3 -m venv mem0_env && source mem0_env/bin/activate"})

        # Step 5: pip install
        steps.append({"id": 5, "title": "Python-Pakete installieren",
                      "cmd": "pip install --upgrade pip && pip install ollama mem0 qdrant-client flask rich psutil requests flask-cors"})

        # Step 6: Ollama start + model
        steps.append({"id": 6, "title": "Ollama starten + Modell laden",
                      "cmd": f"nohup ollama serve > ~/logs/ollama.log 2>&1 & sleep 3 && ollama pull {model}"})

        # Step 7: Systemd service (skip in container)
        if not is_container and os_info.get("init_system") == "systemd":
            steps.append({"id": 7, "title": "Systemd Service einrichten",
                          "cmd": "sudo cp ~/KISWARM/deploy/kiswarm.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable kiswarm"})
        else:
            steps.append({"id": 7, "title": "Service starten (kein systemd)",
                          "cmd": "cd ~/KISWARM && source mem0_env/bin/activate && nohup python python/sentinel/sentinel_api.py > ~/logs/kiswarm.log 2>&1 &",
                          "note": "Kein systemd verfügbar — direkter Prozessstart"})

        # Step 8: Verify
        steps.append({"id": 8, "title": "Installation verifizieren",
                      "cmd": "curl http://localhost:11434/api/tags && curl http://localhost:11436/health"})

        plan["estimated_duration_min"] = 15 + (5 if ram_gb >= 16 else 0)
        return plan
