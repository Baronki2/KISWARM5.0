"""
KISWARM v4.7 — Module 42: Experience Collector
===============================================
Runs silently on every KISWARM installation.
Captures errors, warnings, fix patterns, and performance metrics.
Anonymizes everything — no IPs, no personal data, only operational patterns.

The collected experience feeds into the FeedbackChannel which sends it
to GitHub, where the HardeningEngine picks it up for all future installs.

"Every failure makes the next installation smarter."

Data stored locally in: sentinel_data/experience/
Schema: ExperienceEvent (see below)

Privacy model:
  - System fingerprint = SHA-256(hostname + distro + cpu_model)[:16]  (irreversible)
  - No IP addresses stored or transmitted
  - No usernames, paths, or personal data
  - Only: error patterns, module context, fix applied, success/failure
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import socket
import time
import traceback
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# SCHEMA
# ─────────────────────────────────────────────────────────────────────────────

class ExperienceType(str, Enum):
    ERROR         = "error"          # An error occurred
    WARNING       = "warning"        # A warning was raised
    FIX_APPLIED   = "fix_applied"    # A known fix was applied
    FIX_SUCCEEDED = "fix_succeeded"  # Fix resolved the issue
    FIX_FAILED    = "fix_failed"     # Fix did not resolve it
    PERFORMANCE   = "performance"    # Performance metric
    INSTALL_STEP  = "install_step"   # Installation step result
    HEALTH_CHECK  = "health_check"   # Health check result
    STARTUP       = "startup"        # System startup event
    RECOVERY      = "recovery"       # Automatic recovery event


@dataclass
class ExperienceEvent:
    """A single captured operational experience."""
    event_id:         str              # UUID
    system_id:        str              # Anonymous system fingerprint (16 hex)
    timestamp:        float
    experience_type:  str              # ExperienceType value
    module:           str              # Which KISWARM module was involved
    error_class:      Optional[str]    # Exception class name if applicable
    error_message:    str              # Sanitized error message
    context:          Dict[str, Any]   # Relevant context (sanitized)
    fix_id:           Optional[str]    # Which fix was attempted (if any)
    fix_succeeded:    Optional[bool]   # Did the fix work?
    kiswarm_version:  str
    os_family:        str              # "debian" | "redhat" | "arch" | "macos"
    python_version:   str
    duration_ms:      Optional[float]  # For performance events

    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}

    def signature(self) -> str:
        """Unique signature for deduplication: same error pattern = same signature."""
        raw = f"{self.module}:{self.error_class}:{self.error_message[:50]}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ─────────────────────────────────────────────────────────────────────────────
# SYSTEM FINGERPRINT
# ─────────────────────────────────────────────────────────────────────────────

def _make_system_id() -> str:
    """Create anonymous, irreversible system fingerprint."""
    try:
        hostname  = socket.gethostname()
        distro    = platform.system()
        cpu       = platform.processor() or "unknown"
        raw       = f"{hostname}:{distro}:{cpu}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]
    except Exception:
        return hashlib.sha256(str(time.time()).encode()).hexdigest()[:16]

def _os_family() -> str:
    if platform.system() == "Darwin":
        return "macos"
    try:
        with open("/etc/os-release") as f:
            content = f.read().lower()
        if "ubuntu" in content or "debian" in content:
            return "debian"
        if "fedora" in content or "rhel" in content or "centos" in content:
            return "redhat"
        if "arch" in content or "manjaro" in content:
            return "arch"
    except Exception:
        pass
    return "unknown"

def _sanitize(text: str) -> str:
    """Remove paths, usernames, IPs from text."""
    import re
    home = os.path.expanduser("~")
    username = os.environ.get("USER", os.environ.get("USERNAME", ""))
    text = text.replace(home, "~")
    if username:
        text = text.replace(username, "<user>")
    # Remove IP addresses
    text = re.sub(r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b', '<ip>', text)
    # Remove absolute paths (keep relative)
    text = re.sub(r'/home/[^/\s]+', '~', text)
    return text[:300]


# ─────────────────────────────────────────────────────────────────────────────
# COLLECTOR
# ─────────────────────────────────────────────────────────────────────────────

class ExperienceCollector:
    """
    Silent operational recorder.
    Call from anywhere in KISWARM to capture experience.
    Thread-safe, zero external dependencies at capture time.
    """

    KISWARM_VERSION = "4.7"
    MAX_EVENTS_PER_FILE = 500

    def __init__(self, storage_dir: Optional[str] = None):
        if storage_dir is None:
            base = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
            storage_dir = os.path.join(base, "sentinel_data", "experience")
        self.storage_dir = storage_dir
        os.makedirs(storage_dir, exist_ok=True)

        self._system_id    = _make_system_id()
        self._os_family    = _os_family()
        self._python_ver   = platform.python_version()
        self._event_count  = 0
        self._session_id   = str(uuid.uuid4())[:8]

        # Daily rotating log file
        self._log_file = os.path.join(
            storage_dir,
            f"experience_{time.strftime('%Y%m%d')}_{self._session_id}.jsonl"
        )

    # ── Event creation ────────────────────────────────────────────────────────

    def _make_event(
        self,
        experience_type: ExperienceType,
        module: str,
        error_message: str = "",
        error_class: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
        fix_id: Optional[str] = None,
        fix_succeeded: Optional[bool] = None,
        duration_ms: Optional[float] = None,
    ) -> ExperienceEvent:
        return ExperienceEvent(
            event_id=str(uuid.uuid4()),
            system_id=self._system_id,
            timestamp=time.time(),
            experience_type=experience_type.value,
            module=module,
            error_class=error_class,
            error_message=_sanitize(error_message),
            context={k: _sanitize(str(v)) if isinstance(v, str) else v
                     for k, v in (context or {}).items()},
            fix_id=fix_id,
            fix_succeeded=fix_succeeded,
            kiswarm_version=self.KISWARM_VERSION,
            os_family=self._os_family,
            python_version=self._python_ver,
            duration_ms=duration_ms,
        )

    # ── Storage ───────────────────────────────────────────────────────────────

    def _store(self, event: ExperienceEvent) -> None:
        try:
            with open(self._log_file, "a") as f:
                f.write(json.dumps(event.to_dict()) + "\n")
            self._event_count += 1
        except Exception as e:
            logger.debug(f"[ExperienceCollector] Store failed: {e}")

    # ── Public API ────────────────────────────────────────────────────────────

    def capture_error(
        self,
        module: str,
        exception: Exception,
        context: Optional[Dict[str, Any]] = None,
    ) -> ExperienceEvent:
        """Capture an exception with full context."""
        tb = traceback.format_exc()
        event = self._make_event(
            ExperienceType.ERROR,
            module=module,
            error_class=type(exception).__name__,
            error_message=f"{exception} | {tb[:200]}",
            context=context,
        )
        self._store(event)
        logger.debug(f"[ExperienceCollector] Captured error: {type(exception).__name__} in {module}")
        return event

    def capture_warning(
        self,
        module: str,
        message: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> ExperienceEvent:
        """Capture a warning condition."""
        event = self._make_event(
            ExperienceType.WARNING,
            module=module,
            error_message=message,
            context=context,
        )
        self._store(event)
        return event

    def capture_fix(
        self,
        module: str,
        fix_id: str,
        succeeded: bool,
        context: Optional[Dict[str, Any]] = None,
    ) -> ExperienceEvent:
        """Record the result of applying a known fix."""
        etype = ExperienceType.FIX_SUCCEEDED if succeeded else ExperienceType.FIX_FAILED
        event = self._make_event(
            etype,
            module=module,
            fix_id=fix_id,
            fix_succeeded=succeeded,
            error_message=f"Fix {fix_id}: {'succeeded' if succeeded else 'failed'}",
            context=context,
        )
        self._store(event)
        return event

    def capture_install_step(
        self,
        step_id: int,
        title: str,
        success: bool,
        duration_ms: float,
        error_msg: str = "",
    ) -> ExperienceEvent:
        """Record an installation step result."""
        event = self._make_event(
            ExperienceType.INSTALL_STEP,
            module="installer_agent",
            error_message=error_msg if not success else f"Step {step_id} OK",
            context={"step_id": step_id, "title": title, "success": success},
            duration_ms=duration_ms,
        )
        self._store(event)
        return event

    def capture_health(
        self,
        checks_passed: int,
        checks_total: int,
        failed_checks: List[str],
    ) -> ExperienceEvent:
        """Record health check results."""
        event = self._make_event(
            ExperienceType.HEALTH_CHECK,
            module="health_check",
            error_message=f"{checks_passed}/{checks_total} passed",
            context={
                "passed": checks_passed,
                "total": checks_total,
                "failed": failed_checks[:5],
            },
        )
        self._store(event)
        return event

    def capture_performance(
        self,
        module: str,
        operation: str,
        duration_ms: float,
        context: Optional[Dict[str, Any]] = None,
    ) -> ExperienceEvent:
        """Record a performance measurement."""
        event = self._make_event(
            ExperienceType.PERFORMANCE,
            module=module,
            error_message=f"{operation}: {duration_ms:.1f}ms",
            context={**(context or {}), "operation": operation},
            duration_ms=duration_ms,
        )
        self._store(event)
        return event

    # ── Analytics ─────────────────────────────────────────────────────────────

    def load_all_events(self) -> List[Dict[str, Any]]:
        """Load all experience events from storage."""
        events = []
        for f in sorted(Path(self.storage_dir).glob("experience_*.jsonl")):
            try:
                with open(f) as fp:
                    for line in fp:
                        line = line.strip()
                        if line:
                            events.append(json.loads(line))
            except Exception:
                pass
        return events

    def top_errors(self, n: int = 10) -> List[Dict[str, Any]]:
        """Return the most frequent error patterns."""
        from collections import Counter
        events = [e for e in self.load_all_events()
                  if e.get("experience_type") == "error"]
        patterns: Dict[str, Dict] = {}
        for e in events:
            sig = e.get("error_class", "unknown") + ":" + e.get("error_message", "")[:40]
            if sig not in patterns:
                patterns[sig] = {"signature": sig, "module": e.get("module"), "count": 0, "last_seen": 0}
            patterns[sig]["count"] += 1
            patterns[sig]["last_seen"] = max(patterns[sig]["last_seen"], e.get("timestamp", 0))
        return sorted(patterns.values(), key=lambda x: x["count"], reverse=True)[:n]

    def fix_success_rate(self) -> Dict[str, Any]:
        """Calculate fix success rates by fix_id."""
        events = [e for e in self.load_all_events()
                  if e.get("experience_type") in ("fix_succeeded", "fix_failed")]
        rates: Dict[str, Dict] = {}
        for e in events:
            fid = e.get("fix_id", "unknown")
            if fid not in rates:
                rates[fid] = {"fix_id": fid, "succeeded": 0, "failed": 0}
            if e["experience_type"] == "fix_succeeded":
                rates[fid]["succeeded"] += 1
            else:
                rates[fid]["failed"] += 1
        for r in rates.values():
            total = r["succeeded"] + r["failed"]
            r["rate"] = round(r["succeeded"] / total, 2) if total > 0 else 0.0
        return rates

    def stats(self) -> Dict[str, Any]:
        events = self.load_all_events()
        types: Dict[str, int] = {}
        for e in events:
            t = e.get("experience_type", "unknown")
            types[t] = types.get(t, 0) + 1
        return {
            "system_id":    self._system_id,
            "total_events": len(events),
            "by_type":      types,
            "session_count": self._event_count,
            "storage_dir":  self.storage_dir,
        }


# ─────────────────────────────────────────────────────────────────────────────
# SINGLETON
# ─────────────────────────────────────────────────────────────────────────────

_collector: Optional[ExperienceCollector] = None

def get_collector() -> ExperienceCollector:
    global _collector
    if _collector is None:
        _collector = ExperienceCollector()
    return _collector
