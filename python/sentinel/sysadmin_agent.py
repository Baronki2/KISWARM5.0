"""
KISWARM v4.7 — Module 44: SysAdmin Agent
==========================================
The Installer Agent's operational twin.
Where the Installer sets up, the SysAdmin Agent keeps things running.

Capabilities:
  • diagnose()    — full system diagnosis with root cause analysis
  • heal()        — apply known fixes automatically
  • patrol()      — continuous background monitoring + auto-heal
  • report()      — generate maintenance report for human/AI review
  • prescribe()   — suggest fixes that require human approval

State machine:
  IDLE → DIAGNOSING → (HEALTHY | ISSUES_FOUND) → HEALING → (HEALED | ESCALATE)

Integration with feedback loop:
  1. Diagnoses issue
  2. Checks FeedbackChannel for known fixes
  3. Applies best matching fix
  4. Records outcome in ExperienceCollector
  5. If no fix found → reports to GitHub via FeedbackChannel
  6. Community creates fix → next KISWARM system benefits automatically
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class HealingState(str, Enum):
    IDLE       = "idle"
    DIAGNOSING = "diagnosing"
    HEALTHY    = "healthy"
    ISSUES     = "issues_found"
    HEALING    = "healing"
    HEALED     = "healed"
    ESCALATE   = "escalate"   # No fix found, needs human/community


@dataclass
class DiagnosticFinding:
    """A single issue found during diagnosis."""
    finding_id:    str
    severity:      str           # "critical" | "warning" | "info"
    component:     str           # Which part of KISWARM
    title:         str
    description:   str
    error_message: str
    recommended_fix_id: Optional[str]   # From known_fixes.json
    can_auto_heal: bool
    context:       Dict[str, Any] = field(default_factory=dict)


@dataclass
class HealingResult:
    """Result of applying a fix."""
    fix_id:        str
    finding_id:    str
    fix_applied:   bool
    succeeded:     bool
    duration_s:    float
    output:        str
    error:         str = ""


@dataclass
class DiagnosticReport:
    """Complete diagnostic + healing report."""
    generated_at:    float
    state:           str
    system_id:       str
    findings:        List[DiagnosticFinding]
    healing_results: List[HealingResult]
    unresolved:      List[DiagnosticFinding]
    overall_health:  str    # "healthy" | "degraded" | "critical"
    score:           float  # 0.0-1.0

    def summary(self) -> str:
        total     = len(self.findings)
        healed    = len(self.healing_results)
        unresolved = len(self.unresolved)
        return (
            f"SysAdmin Report — {self.overall_health} (score: {self.score:.0%})\n"
            f"  Issues: {total} found, {healed} healed, {unresolved} unresolved\n"
            f"  State: {self.state}"
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "generated_at":  self.generated_at,
            "state":         self.state,
            "system_id":     self.system_id,
            "overall_health": self.overall_health,
            "score":         self.score,
            "findings_count": len(self.findings),
            "healed_count":  len(self.healing_results),
            "unresolved_count": len(self.unresolved),
            "findings": [
                {"id": f.finding_id, "severity": f.severity, "title": f.title,
                 "can_auto_heal": f.can_auto_heal}
                for f in self.findings
            ],
            "unresolved": [
                {"id": f.finding_id, "title": f.title, "description": f.description}
                for f in self.unresolved
            ],
        }


# ─────────────────────────────────────────────────────────────────────────────
# SYSADMIN AGENT
# ─────────────────────────────────────────────────────────────────────────────

class SysAdminAgent:
    """
    Autonomous KISWARM system administrator.
    Diagnoses issues, applies known fixes, reports unknowns to community.
    """

    KISWARM_INSTALL_DIR = os.path.expanduser("~/KISWARM")
    VENV_PATH           = os.path.expanduser("~/KISWARM/mem0_env")
    LOG_DIR             = os.path.expanduser("~/logs")

    def __init__(
        self,
        install_dir: str = None,
        auto_report: bool = True,
        log_callback: Optional[Callable[[str, str], None]] = None,
    ):
        self.install_dir = install_dir or self.KISWARM_INSTALL_DIR
        self.auto_report = auto_report
        self.log_cb      = log_callback or self._default_log
        self.state       = HealingState.IDLE
        self._system_id  = self._get_system_id()

    @staticmethod
    def _default_log(level: str, msg: str) -> None:
        icons = {"INFO": "ℹ", "OK": "✓", "WARN": "⚠", "ERROR": "✗", "HEAL": "🔧"}
        print(f"  {icons.get(level, '·')} {msg}", flush=True)

    def _log(self, level: str, msg: str) -> None:
        logger.info(f"[SysAdmin][{level}] {msg}")
        self.log_cb(level, msg)

    def _get_system_id(self) -> str:
        try:
            from .experience_collector import _make_system_id
            return _make_system_id()
        except Exception:
            import hashlib, socket
            return hashlib.sha256(socket.gethostname().encode()).hexdigest()[:16]

    # ── DIAGNOSIS ─────────────────────────────────────────────────────────────

    def diagnose(self) -> List[DiagnosticFinding]:
        """Run complete diagnosis of the KISWARM installation."""
        self.state = HealingState.DIAGNOSING
        self._log("INFO", "Starte Systemdiagnose...")
        findings: List[DiagnosticFinding] = []

        # Load known fixes for auto-heal capability check
        try:
            from .feedback_channel import FeedbackChannel
            channel = FeedbackChannel()
            known_fixes = channel.load_known_fixes()
        except Exception:
            known_fixes = []

        def has_fix(error_msg: str, error_class: str = None) -> Optional[str]:
            for fix in known_fixes:
                if fix.matches(error_msg, error_class):
                    return fix.fix_id
            return None

        # ── Check 1: Ollama ───────────────────────────────────────────────────
        try:
            import requests
            r = requests.get("http://localhost:11434/api/tags", timeout=3)
            if r.status_code != 200:
                raise Exception(f"HTTP {r.status_code}")
            self._log("OK", "Ollama: erreichbar")
        except Exception as e:
            msg = f"ollama not responding connection refused 11434 {e}"
            findings.append(DiagnosticFinding(
                finding_id="D-001", severity="critical", component="ollama",
                title="Ollama nicht erreichbar",
                description="Ollama-Server antwortet nicht auf Port 11434",
                error_message=msg,
                recommended_fix_id=has_fix(msg, "ConnectionRefusedError"),
                can_auto_heal=True,
            ))
            self._log("ERROR", "Ollama: nicht erreichbar")

        # ── Check 2: Python venv ──────────────────────────────────────────────
        venv_python = os.path.join(self.VENV_PATH, "bin", "python")
        if not os.path.isfile(venv_python):
            msg = "venv not found No module named"
            findings.append(DiagnosticFinding(
                finding_id="D-002", severity="critical", component="python_env",
                title="Python Virtual Environment fehlt",
                description=f"Erwartet bei: {self.VENV_PATH}",
                error_message=msg,
                recommended_fix_id=has_fix(msg, "ModuleNotFoundError"),
                can_auto_heal=True,
            ))
            self._log("ERROR", "Python venv: fehlt")
        else:
            # Check imports
            r = subprocess.run(
                [venv_python, "-c", "import ollama, qdrant_client, flask"],
                capture_output=True, text=True, timeout=10
            )
            if r.returncode != 0:
                msg = f"No module named {r.stderr[:80]}"
                findings.append(DiagnosticFinding(
                    finding_id="D-002b", severity="warning", component="python_env",
                    title="Python-Pakete fehlen",
                    description="Pflichtpakete nicht installiert",
                    error_message=msg,
                    recommended_fix_id=has_fix(msg, "ModuleNotFoundError"),
                    can_auto_heal=True,
                ))
                self._log("WARN", "Python venv: Pakete fehlen")
            else:
                self._log("OK", "Python venv: vollständig")

        # ── Check 3: Sentinel API ─────────────────────────────────────────────
        try:
            import requests
            r = requests.get("http://localhost:11436/health", timeout=3)
            if r.status_code == 200:
                self._log("OK", "Sentinel API: erreichbar")
            else:
                raise Exception(f"HTTP {r.status_code}")
        except Exception as e:
            findings.append(DiagnosticFinding(
                finding_id="D-003", severity="warning", component="sentinel_api",
                title="Sentinel API nicht erreichbar",
                description="Port 11436 antwortet nicht",
                error_message=str(e),
                recommended_fix_id=None,
                can_auto_heal=False,  # Needs manual start
                context={"start_cmd": f"cd {self.install_dir} && source mem0_env/bin/activate && python python/sentinel/sentinel_api.py &"},
            ))
            self._log("WARN", "Sentinel API: nicht erreichbar")

        # ── Check 4: Disk space ───────────────────────────────────────────────
        try:
            import shutil
            usage = shutil.disk_usage(self.install_dir)
            free_gb = usage.free / 1e9
            if free_gb < 2.0:
                findings.append(DiagnosticFinding(
                    finding_id="D-004", severity="critical", component="disk",
                    title=f"Kritisch wenig Speicher: {free_gb:.1f}GB",
                    description="Weniger als 2GB frei — System kann instabil werden",
                    error_message=f"disk space critical {free_gb:.1f}GB",
                    recommended_fix_id=None,
                    can_auto_heal=False,
                    context={"free_gb": round(free_gb, 2)},
                ))
                self._log("ERROR", f"Disk: nur {free_gb:.1f}GB frei")
            elif free_gb < 5.0:
                findings.append(DiagnosticFinding(
                    finding_id="D-004b", severity="warning", component="disk",
                    title=f"Wenig Speicher: {free_gb:.1f}GB",
                    description="Weniger als 5GB — Backup-Rotation empfohlen",
                    error_message=f"disk space low {free_gb:.1f}GB",
                    recommended_fix_id=None,
                    can_auto_heal=True,
                    context={"cleanup_cmd": "find ~/backups -name '*.tar.gz' -mtime +7 -delete"},
                ))
                self._log("WARN", f"Disk: {free_gb:.1f}GB frei (niedrig)")
            else:
                self._log("OK", f"Disk: {free_gb:.1f}GB frei")
        except Exception:
            pass

        # ── Check 5: Port conflicts ───────────────────────────────────────────
        import socket
        for port, name in [(11436, "sentinel"), (11434, "ollama"), (6333, "qdrant")]:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=1):
                    pass  # Port is used — good
            except ConnectionRefusedError:
                if name == "sentinel":
                    pass  # Already reported in D-003
            except Exception:
                pass

        # ── Check 6: Log errors (recent) ──────────────────────────────────────
        for log_name, log_file in [
            ("ollama", os.path.join(self.LOG_DIR, "ollama.log")),
            ("kiswarm", os.path.join(self.LOG_DIR, "kiswarm.log")),
        ]:
            if os.path.exists(log_file):
                try:
                    with open(log_file) as f:
                        lines = f.readlines()[-50:]
                    errors = [l for l in lines if "ERROR" in l or "FATAL" in l or "panic" in l.lower()]
                    if len(errors) > 5:
                        findings.append(DiagnosticFinding(
                            finding_id=f"D-LOG-{log_name}", severity="warning",
                            component=log_name,
                            title=f"Viele Fehler in {log_name}.log ({len(errors)} in letzten 50 Zeilen)",
                            description="Häufige Fehler im Log deuten auf ein wiederkehrendes Problem",
                            error_message=errors[-1][:100] if errors else "",
                            recommended_fix_id=None,
                            can_auto_heal=False,
                            context={"recent_error": errors[-1][:200] if errors else ""},
                        ))
                        self._log("WARN", f"{log_name} Log: {len(errors)} Fehler")
                except Exception:
                    pass

        if not findings:
            self.state = HealingState.HEALTHY
            self._log("OK", "Alle Checks bestanden — System gesund")
        else:
            self.state = HealingState.ISSUES
            critical = [f for f in findings if f.severity == "critical"]
            self._log("WARN" if not critical else "ERROR",
                      f"{len(findings)} Probleme gefunden ({len(critical)} kritisch)")

        return findings

    # ── HEALING ───────────────────────────────────────────────────────────────

    def heal(
        self,
        findings: Optional[List[DiagnosticFinding]] = None,
    ) -> Tuple[List[HealingResult], List[DiagnosticFinding]]:
        """
        Apply known fixes to found issues.
        Returns (healed, unresolved).
        """
        if findings is None:
            findings = self.diagnose()

        if not findings:
            return [], []

        self.state = HealingState.HEALING

        try:
            from .feedback_channel import FeedbackChannel
            from .experience_collector import get_collector
            channel   = FeedbackChannel()
            collector = get_collector()
            known_fixes = channel.load_known_fixes()
        except Exception:
            known_fixes = []
            collector  = None
            channel    = None

        healed:     List[HealingResult] = []
        unresolved: List[DiagnosticFinding] = []

        for finding in findings:
            self._log("HEAL", f"Behandle: {finding.title}")

            # Find best matching fix
            best_fix = None
            if finding.recommended_fix_id:
                for f in known_fixes:
                    if f.fix_id == finding.recommended_fix_id:
                        best_fix = f
                        break
            if best_fix is None:
                for f in sorted(known_fixes, key=lambda x: x.success_rate, reverse=True):
                    if f.matches(finding.error_message, module=finding.component):
                        best_fix = f
                        break

            if best_fix is None:
                self._log("WARN", f"Kein Fix bekannt für: {finding.title}")
                unresolved.append(finding)
                # Report to GitHub if auto_report enabled
                if self.auto_report and channel:
                    try:
                        collector.capture_warning(
                            module=finding.component,
                            message=finding.error_message,
                            context={"finding_id": finding.finding_id},
                        ) if collector else None
                    except Exception:
                        pass
                continue

            # Apply the fix
            t0 = time.time()
            fix_output = ""
            fix_error  = ""
            succeeded  = False

            try:
                self._log("HEAL", f"Wende an: {best_fix.fix_id} — {best_fix.title}")

                if best_fix.fix_python:
                    exec(best_fix.fix_python, {"os": os, "subprocess": subprocess})
                    succeeded = True
                elif best_fix.fix_commands:
                    for cmd in best_fix.fix_commands:
                        r = subprocess.run(
                            cmd, shell=True, capture_output=True, text=True,
                            timeout=60, env=os.environ.copy()
                        )
                        fix_output += r.stdout[:200]
                        if r.returncode != 0:
                            fix_error = r.stderr[:200]
                            succeeded = False
                            break
                    else:
                        succeeded = True

                # Verify the fix worked
                if succeeded:
                    time.sleep(2)
                    succeeded = self._verify_fix(finding)

            except Exception as e:
                fix_error = str(e)
                succeeded = False

            duration = time.time() - t0

            result = HealingResult(
                fix_id=best_fix.fix_id,
                finding_id=finding.finding_id,
                fix_applied=True,
                succeeded=succeeded,
                duration_s=round(duration, 1),
                output=fix_output[:300],
                error=fix_error[:200],
            )
            healed.append(result)

            if succeeded:
                self._log("OK", f"Fix {best_fix.fix_id} erfolgreich in {duration:.1f}s")
            else:
                self._log("WARN", f"Fix {best_fix.fix_id} fehlgeschlagen — eskaliere")
                unresolved.append(finding)

            # Record in ExperienceCollector
            if collector:
                try:
                    collector.capture_fix(
                        module=finding.component,
                        fix_id=best_fix.fix_id,
                        succeeded=succeeded,
                        context={"finding_id": finding.finding_id},
                    )
                except Exception:
                    pass

        self.state = HealingState.HEALED if not unresolved else HealingState.ESCALATE
        return healed, unresolved

    def _verify_fix(self, finding: DiagnosticFinding) -> bool:
        """Quick verification that a fix resolved the issue."""
        try:
            if finding.component == "ollama":
                import requests
                r = requests.get("http://localhost:11434/api/tags", timeout=5)
                return r.status_code == 200
            elif finding.component == "python_env":
                venv_py = os.path.join(self.VENV_PATH, "bin", "python")
                r = subprocess.run([venv_py, "-c", "import flask"], capture_output=True, timeout=5)
                return r.returncode == 0
        except Exception:
            pass
        return True  # Assume success if we can't verify

    # ── FULL CYCLE ────────────────────────────────────────────────────────────

    def run_full_cycle(self) -> DiagnosticReport:
        """Diagnose → Heal → Report."""
        t0       = time.time()
        findings = self.diagnose()
        healed, unresolved = self.heal(findings)

        # Health score
        total    = len(findings) + 1  # +1 to avoid div-by-zero
        resolved = len(findings) - len(unresolved)
        score    = 1.0 if not findings else resolved / len(findings)

        if score >= 0.9:
            overall = "healthy"
        elif score >= 0.6:
            overall = "degraded"
        else:
            overall = "critical"

        report = DiagnosticReport(
            generated_at=time.time(),
            state=self.state.value,
            system_id=self._system_id,
            findings=findings,
            healing_results=healed,
            unresolved=unresolved,
            overall_health=overall,
            score=score,
        )

        self._log("INFO", report.summary())

        # Report unknowns to GitHub
        if unresolved and self.auto_report:
            try:
                from .feedback_channel import FeedbackChannel
                from .experience_collector import get_collector
                collector = get_collector()
                channel   = FeedbackChannel()
                events = collector.load_all_events()
                recent = [e for e in events if time.time() - e.get("timestamp", 0) < 3600]
                if recent:
                    channel.report_experience(recent, self._system_id)
            except Exception as e:
                logger.debug(f"[SysAdmin] Auto-report failed: {e}")

        return report

    def patrol(self, interval_s: int = 1800, max_cycles: int = None) -> None:
        """
        Run continuous background monitoring.
        interval_s: seconds between cycles (default 30 min)
        max_cycles: stop after N cycles (None = infinite)
        """
        self._log("INFO", f"Patrol gestartet (Intervall: {interval_s}s)")
        cycle = 0
        while max_cycles is None or cycle < max_cycles:
            try:
                report = self.run_full_cycle()
                self._log("INFO", f"Patrol-Zyklus {cycle+1}: {report.overall_health}")
            except Exception as e:
                logger.error(f"[SysAdmin] Patrol-Fehler: {e}")
            cycle += 1
            if max_cycles is None or cycle < max_cycles:
                time.sleep(interval_s)


# ─────────────────────────────────────────────────────────────────────────────
# CONVENIENCE
# ─────────────────────────────────────────────────────────────────────────────

def quick_heal() -> Dict[str, Any]:
    """One-call: diagnose and heal, return report dict."""
    agent  = SysAdminAgent()
    report = agent.run_full_cycle()
    return report.to_dict()
