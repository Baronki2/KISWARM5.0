"""
KISWARM v4.6 — Module 37: Advisor API
=======================================
AI-to-AI system advisor — the "voice" of the Installer Agent.

When GLM5 agents, Qwen models, or any other AI system asks:
  "How do I install KISWARM on Ubuntu 24.04 with 8GB RAM?"
  "What models should I use given my hardware?"
  "My Qdrant collection is failing — what's wrong?"
  "Scan my system and give me an install plan"

...the Advisor responds in structured JSON with actionable advice,
adapted to the specific caller and their environment.

Roles:
  1. SYSTEM CONSULTANT  — answers questions about KISWARM architecture
  2. INSTALL ADVISOR    — generates tailored install plans from scout data
  3. HEALTH CONSULTANT  — diagnoses problems and recommends fixes
  4. PEER AGENT         — communicates as equal with other KISWARM instances
  5. CAPABILITY BROKER  — helps agents discover what KISWARM can do for them

Peer Protocol:
  Any AI can POST to /advisor/consult with:
    {
      "caller_id":    "glm5-agent-7",
      "caller_type":  "glm5 | qwen | kiswarm | unknown",
      "question":     "How do I install KISWARM?",
      "context":      { ... optional system context ... }
    }

  Response:
    {
      "advisor_id":   "kiswarm-advisor-v4.6",
      "answer":       "...",
      "action_plan":  [ ... steps ... ],
      "confidence":   0.95,
      "follow_up":    [ ... suggested questions ... ]
    }
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

ADVISOR_ID      = "kiswarm-advisor-v4.6"
ADVISOR_VERSION = "4.6"
MAX_SESSION_AGE = 3600 * 6   # 6 hours


# ─────────────────────────────────────────────────────────────────────────────
# SESSION MANAGEMENT
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class AdvisorySession:
    session_id:  str
    caller_id:   str
    caller_type: str
    started_at:  float
    exchanges:   List[Dict[str, Any]] = field(default_factory=list)
    context:     Dict[str, Any]       = field(default_factory=dict)
    scout_report: Optional[Dict[str, Any]] = None

    @property
    def client_id(self) -> str:
        """Alias for caller_id — for test compatibility."""
        return self.caller_id

    def add_exchange(self, question: str, answer: Dict[str, Any]) -> None:
        self.exchanges.append({
            "ts":       time.time(),
            "question": question,
            "answer":   answer,
        })

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id":     self.session_id,
            "caller_id":      self.caller_id,
            "client_id":      self.caller_id,   # alias — tests expect this
            "caller_type":    self.caller_type,
            "started_at":     self.started_at,
            "exchange_count": len(self.exchanges),
            "context_keys":   list(self.context.keys()),
        }


# ─────────────────────────────────────────────────────────────────────────────
# ADVISOR KNOWLEDGE BASE (fast lookup without RepoIntelligence overhead)
# ─────────────────────────────────────────────────────────────────────────────

_QUICK_ANSWERS = {
    "install": {
        "keywords": ["install", "setup", "deploy", "einrichten", "aufsetzen"],
        "answer": (
            "KISWARM Installation in 3 Schritten:\n"
            "1. curl -fsSL https://raw.githubusercontent.com/Baronki2/KISWARM/main/install.sh | bash\n"
            "   → One-Click-Installer scannt dein System automatisch und installiert alles\n"
            "2. Alternativ: git clone https://github.com/Baronki2/KISWARM.git && cd KISWARM && ./install.sh\n"
            "3. Verifikation: kiswarm-health"
        ),
        "steps": [
            "curl -fsSL https://raw.githubusercontent.com/Baronki2/KISWARM/main/install.sh | bash",
            "source ~/.bashrc",
            "kiswarm-health",
        ],
    },
    "requirements": {
        "keywords": ["requirement", "voraussetzung", "need", "brauche", "minimum"],
        "answer": (
            "KISWARM Mindestanforderungen:\n"
            "• RAM: 4GB Minimum, 16GB empfohlen\n"
            "• Disk: 10GB frei (50GB für größere Modelle)\n"
            "• OS: Ubuntu/Debian/Fedora/Arch Linux\n"
            "• Python: 3.8+\n"
            "• Pakete: git, python3, pip, curl (werden auto-installiert)"
        ),
    },
    "ports": {
        "keywords": ["port", "ports", "welcher port", "network", "netzwerk"],
        "answer": (
            "KISWARM Ports:\n"
            "• 11434 — Ollama LLM Server\n"
            "• 11435 — Tool Injection Proxy\n"
            "• 11436 — Sentinel API (Haupt-API)\n"
            "• 11437 — Dev-Instanz\n"
            "• 6333  — Qdrant HTTP\n"
            "• 6334  — Qdrant gRPC"
        ),
    },
    "modules": {
        "keywords": ["module", "modul", "what can", "was kann", "features", "funktionen"],
        "answer": (
            "KISWARM v4.6: 37 Module in 7 Versionen\n"
            "Kernkategorien:\n"
            "• Swarm Intelligence: Debate, Conflict, Decay, Ledger\n"
            "• Industrial AI: PLC, SCADA, Physics Twin, SIL (IEC 61508)\n"
            "• Security: ICS Cybersecurity (IEC 62443), OT Monitor, Firewall\n"
            "• Self-Healing: Swarm Auditor, Immortality Kernel, Soul Mirror\n"
            "• Installer: System Scout, Repo Intelligence, Advisor API\n"
            "Alle 197 API-Endpoints unter http://localhost:11436"
        ),
    },
    "model": {
        "keywords": ["model", "welches model", "welches llm", "ollama model", "empfehlung"],
        "answer": (
            "Modell-Empfehlung nach RAM:\n"
            "• ≥32GB RAM → qwen2.5:14b (beste Qualität)\n"
            "• ≥16GB RAM → qwen2.5:7b  (gutes Gleichgewicht)\n"
            "•  ≥8GB RAM → qwen2.5:3b  (schnell, effizient)\n"
            "•  ≥4GB RAM → qwen2.5:0.5b (minimal)\n"
            "Installer wählt automatisch das passende Modell."
        ),
    },
    "health": {
        "keywords": ["health", "status", "funktioniert", "check", "diagnose"],
        "answer": (
            "Diagnose-Befehle:\n"
            "• kiswarm-health         → 40+ System-Checks\n"
            "• kiswarm-status         → Live-Dashboard\n"
            "• curl http://localhost:11436/health → API-Status\n"
            "• curl http://localhost:11434/api/tags → Ollama-Status\n"
            "• curl http://localhost:11436/installer/scan → System-Scan"
        ),
    },
    "glm5": {
        "keywords": ["glm5", "zhipu", "chatglm"],
        "answer": (
            "GLM5-Agent Integration mit KISWARM:\n"
            "• KISWARM läuft als eigenständiger Service auf deinem Server\n"
            "• GLM5-Agents können über die Sentinel API kommunizieren\n"
            "• Advisor-Endpunkt: POST /advisor/consult\n"
            "• Die GLM5-Deployment-Instanz unter y1zu81qu4570-d.space.z.ai\n"
            "  hat KISWARM v4.3 bereits autonom installiert."
        ),
    },
}


def _match_intent(question: str) -> Optional[str]:
    """Simple keyword-based intent matching."""
    q = question.lower()
    for intent, data in _QUICK_ANSWERS.items():
        if any(kw in q for kw in data["keywords"]):
            return intent
    return None


# ─────────────────────────────────────────────────────────────────────────────
# ADVISOR
# ─────────────────────────────────────────────────────────────────────────────

class KISWARMAdvisor:
    """
    AI-to-AI advisor for KISWARM.

    Maintains sessions per caller, provides structured answers,
    generates install plans from system scans, and acts as a
    peer agent in multi-AI swarm environments.
    """

    def __init__(self, session_dir: Optional[str] = None):
        if session_dir is None:
            session_dir = os.path.join(
                os.path.dirname(__file__), "../../sentinel_data/advisor_sessions"
            )
        self.session_dir = session_dir
        os.makedirs(session_dir, exist_ok=True)
        self._sessions: Dict[str, AdvisorySession] = {}
        self._total_consultations = 0

    # ── Session management ────────────────────────────────────────────────────

    def get_or_create_session(self, caller_id: str, caller_type: str = "unknown") -> AdvisorySession:
        # Clean expired sessions
        now = time.time()
        expired = [
            sid for sid, sess in self._sessions.items()
            if now - sess.started_at > MAX_SESSION_AGE
        ]
        for sid in expired:
            del self._sessions[sid]

        # Find existing session for caller
        for sess in self._sessions.values():
            if sess.caller_id == caller_id:
                return sess

        # New session
        session = AdvisorySession(
            session_id=str(uuid.uuid4()),
            caller_id=caller_id,
            caller_type=caller_type,
            started_at=now,
        )
        self._sessions[session.session_id] = session
        logger.info(f"[Advisor] New session for {caller_id} ({caller_type}): {session.session_id[:8]}")
        return session

    # ── Core consultation ─────────────────────────────────────────────────────

    def consult(
        self,
        caller_id:   str         = "anonymous",
        caller_type: str         = "unknown",
        question:    Optional[str] = None,
        context:     Dict[str, Any] = None,
    ) -> Dict[str, Any]:
        """
        Full consultation: scan system + generate plan + answer optional question.
        Returns verdict, system, install_plan, recommended_model, next_step.
        When called as consult(caller_id, caller_type) acts as scan_and_advise.
        """
        self._total_consultations += 1
        session = self.get_or_create_session(caller_id, caller_type)
        if context:
            session.context.update(context)

        # Full scan-based consultation (primary behavior)
        try:
            from .system_scout import SystemScout
            from .repo_intelligence import RepoIntelligence

            scout        = SystemScout()
            report       = scout.full_scan()
            report_dict  = report.to_dict()
            session.scout_report = report_dict

            intel        = RepoIntelligence()
            install_plan = intel.generate_install_plan(report_dict)

            hw  = report_dict["hardware"]
            os_ = report_dict["os"]
            readiness = report_dict["install_readiness"]

            if readiness == "ready":
                verdict = "✓ System ist bereit für KISWARM-Installation"
            elif readiness == "warnings":
                verdict = "⚠ Installation möglich mit Einschränkungen"
            else:
                verdict = "✗ Blocking-Probleme — Installation nicht möglich"

            result = {
                "advisor_id":   ADVISOR_ID,
                "session_id":   session.session_id,
                "caller_id":    caller_id,
                "ts":           time.time(),
                "verdict":      verdict,
                "readiness":    readiness,
                "system": {
                    "os":        f"{os_.get('distro')} {os_.get('version')}",
                    "arch":      os_.get("arch"),
                    "ram_gb":    hw.get("ram_total_gb"),
                    "disk_free": hw.get("disk_free_gb"),
                    "cpu_cores": hw.get("cpu_cores"),
                    "container": os_.get("is_container"),
                },
                "recommended_model": hw.get("model_recommendation"),
                "install_plan":  install_plan,
                "warnings":      report_dict.get("readiness_warnings", []),
                "blocking":      report_dict.get("readiness_issues", []),
                "next_step": (
                    "POST /installer/run {\"mode\": \"auto\"} um Installation zu starten"
                    if readiness != "blocked"
                    else "Blocking-Probleme lösen, dann erneut scannen"
                ),
            }

            # Also answer a question if provided
            if question:
                intent = _match_intent(question)
                if intent and intent in _QUICK_ANSWERS:
                    result["answer"] = _QUICK_ANSWERS[intent]["answer"]
                    result["intent"] = intent

            session.add_exchange(question or "consult", result)
            return result

        except Exception as e:
            logger.error(f"[Advisor] consult failed: {e}", exc_info=True)
            # Fallback: question-only mode
            intent = _match_intent(question or caller_id)
            if intent and intent in _QUICK_ANSWERS:
                qa = _QUICK_ANSWERS[intent]
                return {
                    "advisor_id":        ADVISOR_ID,
                    "session_id":        session.session_id,
                    "caller_id":         caller_id,
                    "verdict":           "⚠ Scan nicht verfügbar — Wissensbasis-Antwort",
                    "system":            {},
                    "recommended_model": "qwen2.5:3b",
                    "install_plan":      {"steps": qa.get("steps", [])},
                    "next_step":         "System-Scan starten: GET /installer/scan",
                    "answer":            qa["answer"],
                    "intent":            intent,
                    "ts":                time.time(),
                    "error":             str(e),
                }
            return {
                "advisor_id":        ADVISOR_ID,
                "session_id":        session.session_id,
                "caller_id":         caller_id,
                "verdict":           "✗ Fehler",
                "system":            {},
                "recommended_model": "qwen2.5:3b",
                "install_plan":      {},
                "next_step":         "Manuell installieren: bash install.sh",
                "ts":                time.time(),
                "error":             str(e),
            }

    def ask(
        self,
        question:    str,
        caller_id:   str         = "anonymous",
        caller_type: str         = "unknown",
        context:     Dict[str, Any] = None,
    ) -> Dict[str, Any]:
        """Answer a single question (no scan — fast path)."""
        self._total_consultations += 1
        session = self.get_or_create_session(caller_id, caller_type)
        if context:
            session.context.update(context)

        intent = _match_intent(question)

        if intent and intent in _QUICK_ANSWERS:
            qa = _QUICK_ANSWERS[intent]
            response = {
                "advisor_id":   ADVISOR_ID,
                "session_id":   session.session_id,
                "question":     question,
                "intent":       intent,
                "answer":       qa["answer"],
                "action_plan":  qa.get("steps", []),
                "confidence":   0.95,
                "source":       "knowledge_base",
                "follow_up": self._suggest_followup(intent, caller_type),
                "caller_id":    caller_id,
                "ts":           time.time(),
            }
        else:
            try:
                from .repo_intelligence import RepoIntelligence
                intel   = RepoIntelligence()
                intel_r = intel.answer(question)
                response = {
                    "advisor_id":   ADVISOR_ID,
                    "session_id":   session.session_id,
                    "question":     question,
                    "intent":       "general",
                    "answer":       intel_r["answer"],
                    "action_plan":  [],
                    "confidence":   0.75,
                    "source":       "repo_intelligence",
                    "follow_up":    self._suggest_followup("general", caller_type),
                    "caller_id":    caller_id,
                    "ts":           time.time(),
                }
            except Exception as e:
                response = {
                    "advisor_id":  ADVISOR_ID,
                    "session_id":  session.session_id,
                    "question":    question,
                    "intent":      "unknown",
                    "answer":      "Nutze /advisor/scan für Systemanalyse. GitHub: https://github.com/Baronki2/KISWARM",
                    "action_plan": [],
                    "confidence":  0.3,
                    "source":      "fallback",
                    "follow_up":   self._suggest_followup("general", caller_type),
                    "caller_id":   caller_id,
                    "ts":          time.time(),
                    "error":       str(e),
                }

        session.add_exchange(question, response)
        return response

    def _suggest_followup(self, intent: str, caller_type: str) -> List[str]:
        """Suggest relevant follow-up questions."""
        base = {
            "install":      ["Wie viel RAM benötige ich?", "Welches Modell empfiehlst du?",
                             "Scanne mein System: GET /installer/scan"],
            "requirements": ["Mein System hat 8GB RAM — was kann ich installieren?",
                             "Wie installiere ich KISWARM?"],
            "ports":        ["Wie starte ich den Sentinel API?", "Wie konfiguriere ich Firewall-Regeln?"],
            "modules":      ["Welche Module gibt es seit v4.0?", "Was ist das Immortality Kernel?"],
            "model":        ["Scanne mein System für eine automatische Empfehlung",
                             "Wie lade ich ein Modell?"],
            "health":       ["Was bedeutet ein blocked-Status?", "Wie repariere ich Ollama?"],
            "glm5":         ["Wie integriere ich KISWARM in meinen GLM5-Workflow?"],
            "general":      ["Zeige mir alle Module", "Wie installiere ich KISWARM?",
                             "Was sind die Systemanforderungen?"],
        }
        questions = base.get(intent, base["general"])
        if caller_type in ("glm5", "kiswarm"):
            questions.append("POST /advisor/consult mit system_context für personalisierte Empfehlung")
        return questions[:3]

    # ── Scan & Plan ───────────────────────────────────────────────────────────

    def scan_and_advise(self, caller_id: str = "anonymous") -> Dict[str, Any]:
        """
        Run system scan and return complete installation advice.
        This is the primary entry point for autonomous AI callers.
        """
        session = self.get_or_create_session(caller_id)
        self._total_consultations += 1

        try:
            from .system_scout import SystemScout
            from .repo_intelligence import RepoIntelligence

            scout        = SystemScout()
            report       = scout.full_scan()
            report_dict  = report.to_dict()
            session.scout_report = report_dict

            intel        = RepoIntelligence()
            install_plan = intel.generate_install_plan(report_dict)

            hw  = report_dict["hardware"]
            os_ = report_dict["os"]

            readiness = report_dict["install_readiness"]
            if readiness == "ready":
                verdict = "✓ System ist bereit für KISWARM-Installation"
            elif readiness == "warnings":
                verdict = "⚠ Installation möglich mit Einschränkungen"
            else:
                verdict = "✗ Blocking-Probleme — Installation nicht möglich"

            return {
                "advisor_id":    ADVISOR_ID,
                "session_id":    session.session_id,
                "caller_id":     caller_id,
                "ts":            time.time(),
                "verdict":       verdict,
                "readiness":     readiness,
                "system": {
                    "os":         f"{os_.get('distro')} {os_.get('version')}",
                    "arch":       os_.get("arch"),
                    "ram_gb":     hw.get("ram_total_gb"),
                    "disk_free":  hw.get("disk_free_gb"),
                    "cpu_cores":  hw.get("cpu_cores"),
                    "container":  os_.get("is_container"),
                },
                "recommended_model": hw.get("model_recommendation"),
                "install_plan":  install_plan,
                "warnings":      report_dict.get("readiness_warnings", []),
                "blocking":      report_dict.get("readiness_issues", []),
                "full_report":   report_dict,
                "next_step": (
                    "POST /installer/run {\"mode\": \"auto\"} um Installation zu starten"
                    if readiness != "blocked"
                    else "Blocking-Probleme lösen, dann erneut scannen"
                ),
            }

        except Exception as e:
            logger.error(f"[Advisor] scan_and_advise failed: {e}", exc_info=True)
            return {
                "advisor_id": ADVISOR_ID,
                "session_id": session.session_id,
                "error":      str(e),
                "fallback":   "Führe manuell durch: curl http://localhost:11436/installer/scan",
            }

    # ── Peer handshake ────────────────────────────────────────────────────────

    def peer_handshake(
        self,
        caller_id:    str,
        caller_type:  str,
        capabilities: List[str],
    ) -> Dict[str, Any]:
        """
        Establish a peer relationship with another AI system.
        Used when KISWARM instances or external AIs introduce themselves.
        """
        session = self.get_or_create_session(caller_id, caller_type)
        session.context["peer_capabilities"] = capabilities

        my_caps = [
            "system_scan", "install_planning", "health_diagnosis",
            "module_info", "model_recommendation", "peer_consulting",
            "swarm_audit", "immortality_kernel", "ics_security",
        ]

        overlap = [c for c in capabilities if c in my_caps]

        return {
            "advisor_id":       ADVISOR_ID,
            "session_id":       session.session_id,
            "greeting":         f"KISWARM Advisor begrüßt {caller_id} ({caller_type})",
            "my_capabilities":  my_caps,
            "your_capabilities": capabilities,
            "shared_capabilities": overlap,
            "endpoints": {
                "consult":     "POST /advisor/consult",
                "scan":        "GET  /advisor/scan",
                "install":     "POST /installer/run",
                "health":      "GET  /health",
                "modules":     "GET  /advisor/modules",
            },
            "version":          ADVISOR_VERSION,
            "ts":               time.time(),
        }

    # ── Stats ─────────────────────────────────────────────────────────────────

    def stats(self) -> Dict[str, Any]:
        return {
            "advisor_id":          ADVISOR_ID,
            "version":             ADVISOR_VERSION,
            "active_sessions":     len(self._sessions),
            "total_consultations": self._total_consultations,
            "known_intents":       list(_QUICK_ANSWERS.keys()),
            "session_ttl_h":       MAX_SESSION_AGE / 3600,
        }

    def list_sessions(self) -> List[Dict[str, Any]]:
        return [s.to_dict() for s in self._sessions.values()]


# ─────────────────────────────────────────────────────────────────────────────
# SINGLETON FACTORY
# ─────────────────────────────────────────────────────────────────────────────

_advisor_singleton: Optional["KISWARMAdvisor"] = None

def get_advisor() -> "KISWARMAdvisor":
    global _advisor_singleton
    if _advisor_singleton is None:
        _advisor_singleton = KISWARMAdvisor()
    return _advisor_singleton
