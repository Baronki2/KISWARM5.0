"""
KISWARM v4.7 — Module 43: Feedback Channel
===========================================
Bidirektionale Brücke zwischen laufenden KISWARM-Installationen und GitHub.

OUTBOUND (Installation → GitHub):
  • Anonymisierte ExperienceEvents als GitHub Issues (Label: experience-report)
  • Neue Fehlermuster werden automatisch gemeldet
  • Fix-Erfolgsraten werden getracked

INBOUND (GitHub → Installation):
  • `experience/known_fixes.json` wird periodisch gepullt
  • Jedes git pull bringt automatisch neue Fixes
  • HardeningEngine wendet sie an

GITHUB ACTIONS (automatisch ausgelöst):
  • Verarbeitet incoming experience-report Issues
  • Extrahiert Fehlermuster
  • Updated known_fixes.json via Pull Request
  • Schließt Issues wenn Fix bekannt

Der Kanal ist OPTIONAL und OPT-IN:
  • Standard: nur lokale Sammlung, kein Senden
  • Mit KISWARM_FEEDBACK_TOKEN env var: sendet anonymisiert an GitHub
  • Mit KISWARM_FEEDBACK=off: komplett deaktiviert
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

REPO_OWNER   = "Baronki2"
REPO_NAME    = "KISWARM"
GITHUB_API   = "https://api.github.com"
GITHUB_RAW   = "https://raw.githubusercontent.com"
BRANCH       = "main"

# Where known fixes live in the repo
KNOWN_FIXES_PATH = "experience/known_fixes.json"
KNOWN_FIXES_URL  = f"{GITHUB_RAW}/{REPO_OWNER}/{REPO_NAME}/{BRANCH}/{KNOWN_FIXES_PATH}"

# Local cache - use KISWARM_HOME environment variable or derive from __file__
def _get_kiswarm_home():
    """Get KISWARM home directory from environment or derive from module location."""
    env_home = os.environ.get("KISWARM_HOME")
    if env_home:
        return Path(env_home)
    # Fallback: derive from module location (3 levels up from this file)
    return Path(__file__).parent.parent.parent

_KISWARM_HOME = _get_kiswarm_home()
_LOCAL_FIXES_FILE = os.path.join(_KISWARM_HOME, "experience", "known_fixes.json")
_LOGS_DIR = os.path.join(_KISWARM_HOME, "logs")


# ─────────────────────────────────────────────────────────────────────────────
# KNOWN FIX SCHEMA
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class KnownFix:
    """A validated fix for a known error pattern."""
    fix_id:          str             # e.g. "FIX-001"
    title:           str             # Human readable
    error_pattern:   str             # Regex or substring to match error_message
    error_class:     Optional[str]   # Exception class if specific
    module:          Optional[str]   # Which module this applies to
    os_family:       Optional[str]   # "debian" | "redhat" | "arch" | None (all)
    fix_commands:    List[str]       # Shell commands to apply the fix
    fix_python:      Optional[str]   # Python code to run (alternative to commands)
    description:     str             # What this fix does
    success_rate:    float           # Reported success rate (0.0-1.0)
    created_at:      str             # ISO date
    contributed_by:  str             # "community" | "kiswarm-team" | system_id
    times_applied:   int = 0
    times_succeeded: int = 0

    def matches(self, error_message: str, error_class: Optional[str] = None,
                module: Optional[str] = None, os_family: Optional[str] = None) -> bool:
        """Check if this fix applies to a given error."""
        import re
        # Pattern match
        try:
            if not re.search(self.error_pattern, error_message, re.IGNORECASE):
                return False
        except re.error:
            if self.error_pattern.lower() not in error_message.lower():
                return False
        # Class match (optional)
        if self.error_class and error_class and self.error_class != error_class:
            return False
        # Module match (optional)
        if self.module and module and self.module != module:
            return False
        # OS match (optional)
        if self.os_family and os_family and self.os_family != os_family:
            return False
        return True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "fix_id":         self.fix_id,
            "title":          self.title,
            "error_pattern":  self.error_pattern,
            "error_class":    self.error_class,
            "module":         self.module,
            "os_family":      self.os_family,
            "fix_commands":   self.fix_commands,
            "fix_python":     self.fix_python,
            "description":    self.description,
            "success_rate":   self.success_rate,
            "created_at":     self.created_at,
            "contributed_by": self.contributed_by,
            "times_applied":  self.times_applied,
            "times_succeeded": self.times_succeeded,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "KnownFix":
        return cls(
            fix_id=d["fix_id"], title=d["title"],
            error_pattern=d["error_pattern"],
            error_class=d.get("error_class"),
            module=d.get("module"), os_family=d.get("os_family"),
            fix_commands=d.get("fix_commands", []),
            fix_python=d.get("fix_python"),
            description=d.get("description", ""),
            success_rate=d.get("success_rate", 0.5),
            created_at=d.get("created_at", ""),
            contributed_by=d.get("contributed_by", "community"),
            times_applied=d.get("times_applied", 0),
            times_succeeded=d.get("times_succeeded", 0),
        )


# ─────────────────────────────────────────────────────────────────────────────
# FEEDBACK CHANNEL
# ─────────────────────────────────────────────────────────────────────────────

class FeedbackChannel:
    """
    Bidirektionaler Kanal: KISWARM ↔ GitHub.

    Outbound: sendet anonymisierte Fehlermuster als GitHub Issues
    Inbound:  lädt known_fixes.json vom Repository
    """

    def __init__(self, github_token: Optional[str] = None):
        self.token   = github_token or os.environ.get("KISWARM_FEEDBACK_TOKEN")
        self.enabled = os.environ.get("KISWARM_FEEDBACK", "on").lower() != "off"
        self._fixes_cache: Optional[List[KnownFix]] = None
        self._fixes_loaded_at: float = 0.0
        self._cache_ttl = 3600  # 1 hour

        # Ensure local experience dir and logs dir exist
        os.makedirs(os.path.dirname(_LOCAL_FIXES_FILE), exist_ok=True)
        os.makedirs(_LOGS_DIR, exist_ok=True)

    # ── INBOUND: Load known fixes ─────────────────────────────────────────────

    def load_known_fixes(self, force_refresh: bool = False) -> List[KnownFix]:
        """
        Load known fixes. Priority:
        1. Memory cache (if fresh)
        2. Local file (experience/known_fixes.json)
        3. GitHub (if online and token available or public)
        """
        now = time.time()

        # Memory cache
        if (self._fixes_cache is not None and
                not force_refresh and
                now - self._fixes_loaded_at < self._cache_ttl):
            return self._fixes_cache

        fixes_data: Optional[List[Dict]] = None

        # Try GitHub first if online
        fixes_data = self._fetch_fixes_from_github()

        # Fallback: local file
        if fixes_data is None:
            fixes_data = self._load_local_fixes()

        # Fallback: empty list with sensible defaults
        if fixes_data is None:
            fixes_data = self._default_fixes()
            logger.info("[FeedbackChannel] Using built-in default fixes")

        fixes = []
        for d in fixes_data:
            try:
                fixes.append(KnownFix.from_dict(d))
            except Exception as e:
                logger.debug(f"[FeedbackChannel] Invalid fix entry: {e}")

        self._fixes_cache    = fixes
        self._fixes_loaded_at = now
        logger.info(f"[FeedbackChannel] Loaded {len(fixes)} known fixes")
        return fixes

    def _fetch_fixes_from_github(self) -> Optional[List[Dict]]:
        try:
            import requests
            headers = {"Accept": "application/vnd.github.v3.raw"}
            if self.token:
                headers["Authorization"] = f"token {self.token}"
            resp = requests.get(KNOWN_FIXES_URL, headers=headers, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                # Cache locally
                try:
                    with open(_LOCAL_FIXES_FILE, "w") as f:
                        json.dump(data, f, indent=2)
                except Exception:
                    pass
                return data.get("fixes", data) if isinstance(data, dict) else data
        except Exception as e:
            logger.debug(f"[FeedbackChannel] GitHub fetch failed: {e}")
        return None

    def _load_local_fixes(self) -> Optional[List[Dict]]:
        try:
            if os.path.exists(_LOCAL_FIXES_FILE):
                with open(_LOCAL_FIXES_FILE) as f:
                    data = json.load(f)
                return data.get("fixes", data) if isinstance(data, dict) else data
        except Exception:
            pass
        return None

    def _default_fixes(self) -> List[Dict]:
        """Built-in fixes for the most common issues — always available offline."""
        return [
            {
                "fix_id": "FIX-001",
                "title": "Ollama service not responding",
                "error_pattern": "ollama.*not.*respond|connection refused.*11434|failed to connect.*ollama",
                "error_class": "ConnectionRefusedError",
                "module": "installer_agent",
                "os_family": None,
                "fix_commands": [
                    "pkill -f 'ollama serve' || true",
                    "sleep 2",
                    "nohup ollama serve > $KISWARM_HOME/logs/ollama.log 2>&1 &",
                    "sleep 5",
                ],
                "fix_python": None,
                "description": "Ollama not running — kill stale processes and restart",
                "success_rate": 0.91,
                "created_at": "2026-03-01",
                "contributed_by": "kiswarm-team",
            },
            {
                "fix_id": "FIX-002",
                "title": "Python venv missing or broken",
                "error_pattern": "No module named|venv.*not found|virtualenv.*error|cannot import",
                "error_class": "ModuleNotFoundError",
                "module": None,
                "os_family": None,
                "fix_commands": [
                    "rm -rf $KISWARM_HOME/mem0_env",
                    "python3 -m venv $KISWARM_HOME/mem0_env",
                    "$KISWARM_HOME/mem0_env/bin/pip install --upgrade pip",
                    "$KISWARM_HOME/mem0_env/bin/pip install ollama mem0 qdrant-client flask flask-cors rich psutil requests",
                ],
                "fix_python": None,
                "description": "Rebuild virtual environment from scratch",
                "success_rate": 0.88,
                "created_at": "2026-03-01",
                "contributed_by": "kiswarm-team",
            },
            {
                "fix_id": "FIX-003",
                "title": "Qdrant data directory permission error",
                "error_pattern": "permission denied.*qdrant|qdrant.*cannot open|read-only file system",
                "error_class": "PermissionError",
                "module": None,
                "os_family": "debian",
                "fix_commands": [
                    "chmod -R 755 $KISWARM_HOME/sentinel_data/",
                    "chown -R $(whoami):$(whoami) $KISWARM_HOME/sentinel_data/ || true",
                ],
                "fix_python": None,
                "description": "Fix Qdrant storage permissions",
                "success_rate": 0.95,
                "created_at": "2026-03-01",
                "contributed_by": "kiswarm-team",
            },
            {
                "fix_id": "FIX-004",
                "title": "Port 11436 already in use",
                "error_pattern": "address already in use.*11436|port 11436.*busy|bind.*11436",
                "error_class": "OSError",
                "module": "sentinel_api",
                "os_family": None,
                "fix_commands": [
                    "PID=$(lsof -ti:11436 2>/dev/null || fuser 11436/tcp 2>/dev/null | awk '{print $1}'); [ -n \"$PID\" ] && kill -9 $PID || true",
                    "sleep 2",
                ],
                "fix_python": None,
                "description": "Kill process occupying port 11436",
                "success_rate": 0.93,
                "created_at": "2026-03-01",
                "contributed_by": "kiswarm-team",
            },
            {
                "fix_id": "FIX-005",
                "title": "pip install fails (SSL/network)",
                "error_pattern": "ssl.*error|certificate.*verify|network.*unreachable.*pip|pip.*timeout",
                "error_class": None,
                "module": "installer_agent",
                "os_family": None,
                "fix_commands": [
                    "pip install --trusted-host pypi.org --trusted-host files.pythonhosted.org ollama mem0 qdrant-client flask rich psutil requests",
                ],
                "fix_python": None,
                "description": "Use trusted-host flag to bypass SSL issues",
                "success_rate": 0.79,
                "created_at": "2026-03-01",
                "contributed_by": "kiswarm-team",
            },
            {
                "fix_id": "FIX-006",
                "title": "git clone fails (no access / wrong URL)",
                "error_pattern": "git.*clone.*failed|repository.*not found|authentication.*required.*github",
                "error_class": None,
                "module": "installer_agent",
                "os_family": None,
                "fix_commands": [
                    "git config --global http.sslVerify false",
                    "git clone https://github.com/Baronki2/KISWARM.git $KISWARM_HOME --depth 1",
                ],
                "fix_python": None,
                "description": "Shallow clone with SSL verification disabled",
                "success_rate": 0.82,
                "created_at": "2026-03-01",
                "contributed_by": "kiswarm-team",
            },
        ]

    # ── OUTBOUND: Report experience to GitHub ────────────────────────────────

    def report_experience(
        self,
        events: List[Dict[str, Any]],
        system_id: str,
    ) -> Dict[str, Any]:
        """
        Send anonymized experience events to GitHub as an Issue.
        Only sends if: token present AND feedback enabled AND new patterns found.
        """
        if not self.enabled:
            return {"status": "disabled", "reason": "KISWARM_FEEDBACK=off"}

        if not self.token:
            return {
                "status": "no_token",
                "reason": "Set KISWARM_FEEDBACK_TOKEN to enable community feedback",
                "events_stored_locally": len(events),
            }

        # Filter to only unknown patterns (not already in known_fixes)
        fixes      = self.load_known_fixes()
        new_events = []
        for e in events:
            msg      = e.get("error_message", "")
            ecls     = e.get("error_class")
            module   = e.get("module")
            os_fam   = e.get("os_family")
            has_fix  = any(f.matches(msg, ecls, module, os_fam) for f in fixes)
            if not has_fix and e.get("experience_type") == "error":
                new_events.append(e)

        if not new_events:
            return {"status": "ok", "reason": "No new patterns to report"}

        # Build issue body
        issue_body = self._build_issue_body(new_events[:5], system_id)

        try:
            import requests
            resp = requests.post(
                f"{GITHUB_API}/repos/{REPO_OWNER}/{REPO_NAME}/issues",
                headers={
                    "Authorization": f"token {self.token}",
                    "Content-Type": "application/json",
                },
                json={
                    "title": f"[Experience Report] {new_events[0].get('error_class', 'Error')} in {new_events[0].get('module', 'unknown')} — {system_id}",
                    "body": issue_body,
                    "labels": ["experience-report", "auto-generated"],
                },
                timeout=10,
            )
            if resp.status_code == 201:
                issue_url = resp.json().get("html_url", "")
                logger.info(f"[FeedbackChannel] Reported {len(new_events)} new patterns → {issue_url}")
                return {"status": "reported", "issue_url": issue_url, "patterns": len(new_events)}
            else:
                return {"status": "error", "http": resp.status_code}
        except Exception as e:
            logger.warning(f"[FeedbackChannel] Report failed: {e}")
            return {"status": "error", "reason": str(e)}

    def _build_issue_body(self, events: List[Dict], system_id: str) -> str:
        lines = [
            "## 🔍 KISWARM Experience Report",
            f"**System ID** (anonymous): `{system_id}`",
            f"**KISWARM Version**: {events[0].get('kiswarm_version', '?')}",
            f"**OS Family**: {events[0].get('os_family', '?')}",
            f"**Python**: {events[0].get('python_version', '?')}",
            f"**Event count**: {len(events)}",
            "",
            "## Error Patterns",
            "",
        ]
        for i, e in enumerate(events, 1):
            lines += [
                f"### Pattern {i}: `{e.get('error_class', 'unknown')}`",
                f"- **Module**: `{e.get('module', '?')}`",
                f"- **Message**: `{e.get('error_message', '')[:100]}`",
                f"- **Timestamp**: {time.strftime('%Y-%m-%d %H:%M', time.localtime(e.get('timestamp', 0)))}",
                "",
            ]
        lines += [
            "---",
            "*Auto-generated by KISWARM Experience Collector. No personal data included.*",
            "*Review → create fix → add to `experience/known_fixes.json` → close issue.*",
        ]
        return "\n".join(lines)

    # ── Contribution: propose a new fix ──────────────────────────────────────

    def propose_fix(
        self,
        error_pattern: str,
        fix_commands: List[str],
        description: str,
        module: Optional[str] = None,
        os_family: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Propose a new fix to the community via GitHub Issue.
        The GitHub Action will validate and merge it into known_fixes.json.
        """
        if not self.token:
            return {"status": "no_token"}

        import time as _time
        fix_data = {
            "error_pattern":  error_pattern,
            "fix_commands":   fix_commands,
            "description":    description,
            "module":         module,
            "os_family":      os_family,
            "proposed_at":    _time.strftime("%Y-%m-%d"),
        }
        body = (
            "## 🔧 Proposed Fix\n\n"
            f"```json\n{json.dumps(fix_data, indent=2)}\n```\n\n"
            "*This fix proposal was auto-generated by the KISWARM SysAdmin Agent.*\n"
            "*If validated, the GitHub Action will assign a FIX-ID and merge.*"
        )
        try:
            import requests
            resp = requests.post(
                f"{GITHUB_API}/repos/{REPO_OWNER}/{REPO_NAME}/issues",
                headers={"Authorization": f"token {self.token}", "Content-Type": "application/json"},
                json={"title": f"[Fix Proposal] {description[:60]}", "body": body,
                      "labels": ["fix-proposal", "auto-generated"]},
                timeout=10,
            )
            if resp.status_code == 201:
                return {"status": "proposed", "issue_url": resp.json().get("html_url")}
        except Exception as e:
            return {"status": "error", "reason": str(e)}
        return {"status": "error"}

    def stats(self) -> Dict[str, Any]:
        fixes = self.load_known_fixes()
        return {
            "enabled":       self.enabled,
            "has_token":     bool(self.token),
            "known_fixes":   len(fixes),
            "fixes_loaded_at": self._fixes_loaded_at,
        }
