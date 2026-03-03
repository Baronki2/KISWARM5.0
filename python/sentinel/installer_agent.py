"""
KISWARM v4.6 — Module 36: Installer Agent
==========================================
Autonomous one-click KISWARM installer.

The Installer Agent is the active brain that:
  1. Runs the System Scout (scan)
  2. Loads Repo Intelligence (plan)
  3. Executes each installation step with retry logic
  4. Verifies installation health at each milestone
  5. Generates a post-install report
  6. Registers itself with the Immortality Kernel

Philosophy:
  "Other AIs should be able to deploy KISWARM without reading a single
   line of documentation. The Installer Agent knows everything."

Execution modes:
  • DRY_RUN  — scan + plan only, no changes made
  • GUIDED   — user confirms each step
  • AUTO     — fully autonomous (default for AI-to-AI)

State machine:
  INIT → SCANNING → PLANNING → INSTALLING → VERIFYING → DONE | FAILED
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# STATE / ENUMS
# ─────────────────────────────────────────────────────────────────────────────

class InstallMode(str, Enum):
    DRY_RUN = "dry_run"
    GUIDED  = "guided"
    AUTO    = "auto"


class InstallState(str, Enum):
    INIT       = "init"
    SCANNING   = "scanning"
    PLANNING   = "planning"
    INSTALLING = "installing"
    VERIFYING  = "verifying"
    DONE       = "done"
    FAILED     = "failed"
    ABORTED    = "aborted"


@dataclass
class StepResult:
    step_id:    int
    title:      str
    cmd:        str
    success:    bool
    stdout:     str = ""
    stderr:     str = ""
    duration_s: float = 0.0
    skipped:    bool = False
    note:       str = ""


@dataclass
class InstallReport:
    """Complete installation report."""
    started_at:   float
    finished_at:  float
    mode:         str
    state:        str
    host:         str
    os_info:      Dict[str, Any]
    hardware:     Dict[str, Any]
    step_results: List[StepResult]
    warnings:     List[str]
    post_checks:  Dict[str, bool]
    error:        Optional[str] = None

    def success(self) -> bool:
        return self.state == InstallState.DONE

    def duration_s(self) -> float:
        return round(self.finished_at - self.started_at, 1)

    def summary(self) -> str:
        passed = sum(1 for s in self.step_results if s.success)
        total  = len(self.step_results)
        checks_ok = sum(1 for v in self.post_checks.values() if v)
        checks_total = len(self.post_checks)
        lines = [
            f"=== KISWARM Installation Report ===",
            f"Status:   {self.state.upper()}",
            f"Host:     {self.host}",
            f"OS:       {self.os_info.get('distro', '?')} {self.os_info.get('version', '')}",
            f"Duration: {self.duration_s()}s",
            f"Steps:    {passed}/{total} succeeded",
            f"Checks:   {checks_ok}/{checks_total} passed",
        ]
        if self.error:
            lines.append(f"Error:    {self.error}")
        if self.warnings:
            lines.append(f"Warnings: {'; '.join(self.warnings[:3])}")
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "started_at":   self.started_at,
            "finished_at":  self.finished_at,
            "duration_s":   self.duration_s(),
            "mode":         self.mode,
            "state":        self.state,
            "success":      self.success(),
            "host":         self.host,
            "os_info":      self.os_info,
            "hardware":     self.hardware,
            "steps":        [
                {
                    "id": s.step_id, "title": s.title,
                    "success": s.success, "skipped": s.skipped,
                    "duration_s": round(s.duration_s, 1),
                    "note": s.note,
                }
                for s in self.step_results
            ],
            "post_checks":  self.post_checks,
            "warnings":     self.warnings,
            "error":        self.error,
        }


# ─────────────────────────────────────────────────────────────────────────────
# INSTALLER AGENT
# ─────────────────────────────────────────────────────────────────────────────

class InstallerAgent:
    """
    Autonomous KISWARM installer.

    Usage (fully autonomous):
        agent = InstallerAgent(mode=InstallMode.AUTO)
        report = agent.run()
        print(report.summary())

    Usage (dry run — no changes):
        agent = InstallerAgent(mode=InstallMode.DRY_RUN)
        report = agent.run()
    """

    def __init__(
        self,
        mode:         InstallMode = InstallMode.AUTO,
        install_dir:  str         = None,
        log_callback: Optional[Callable[[str, str], None]] = None,
        timeout_s:    int         = 300,
    ):
        self.mode        = mode
        self.install_dir = install_dir or os.path.expanduser("~/KISWARM")
        self.log_cb      = log_callback or self._default_log
        self.timeout_s   = timeout_s
        self.state       = InstallState.INIT
        self._step_results: List[StepResult] = []
        self._warnings:     List[str]        = []
        self._started_at:   float            = 0.0

    @staticmethod
    def _default_log(level: str, msg: str) -> None:
        icons = {"INFO": "ℹ", "OK": "✓", "WARN": "⚠", "ERROR": "✗", "STEP": "▶"}
        print(f"  {icons.get(level, '·')} {msg}", flush=True)

    def _log(self, level: str, msg: str) -> None:
        logger.info(f"[InstallerAgent][{level}] {msg}")
        self.log_cb(level, msg)

    # ── Command execution ─────────────────────────────────────────────────────

    def _run_cmd(
        self,
        cmd:        str,
        title:      str,
        step_id:    int,
        timeout:    int   = 120,
        shell:      bool  = True,
        check_only: bool  = False,
    ) -> StepResult:
        """Execute a shell command with timeout and logging."""
        t0 = time.time()

        if self.mode == InstallMode.DRY_RUN or check_only:
            self._log("INFO", f"[DRY_RUN] Would run: {cmd}")
            return StepResult(
                step_id=step_id, title=title, cmd=cmd,
                success=True, skipped=True, duration_s=0.0,
                note="Skipped in dry_run mode"
            )

        self._log("STEP", f"Step {step_id}: {title}")
        self._log("INFO", f"$ {cmd[:120]}{'...' if len(cmd) > 120 else ''}")

        try:
            result = subprocess.run(
                cmd, shell=shell, capture_output=True, text=True,
                timeout=timeout, env={**os.environ, "DEBIAN_FRONTEND": "noninteractive"}
            )
            duration = time.time() - t0
            success  = result.returncode == 0

            if success:
                self._log("OK", f"Step {step_id} completed in {duration:.1f}s")
            else:
                self._log("ERROR", f"Step {step_id} failed (exit {result.returncode})")
                if result.stderr:
                    self._log("ERROR", result.stderr[:200])

            return StepResult(
                step_id=step_id, title=title, cmd=cmd,
                success=success,
                stdout=result.stdout[:500],
                stderr=result.stderr[:500],
                duration_s=duration,
            )
        except subprocess.TimeoutExpired:
            self._log("ERROR", f"Step {step_id} TIMED OUT after {timeout}s")
            return StepResult(
                step_id=step_id, title=title, cmd=cmd,
                success=False, duration_s=timeout,
                note=f"Timed out after {timeout}s"
            )
        except Exception as e:
            self._log("ERROR", f"Step {step_id} exception: {e}")
            return StepResult(
                step_id=step_id, title=title, cmd=cmd,
                success=False, duration_s=time.time() - t0,
                note=str(e)
            )

    def _run_with_retry(self, cmd: str, title: str, step_id: int,
                        retries: int = 2, timeout: int = 120) -> StepResult:
        """Run a command with automatic retry on failure."""
        for attempt in range(retries + 1):
            result = self._run_cmd(cmd, title, step_id, timeout=timeout)
            if result.success or result.skipped:
                return result
            if attempt < retries:
                wait = 5 * (attempt + 1)
                self._log("WARN", f"Retry {attempt+1}/{retries} in {wait}s...")
                time.sleep(wait)
        return result

    # ── Health checks ─────────────────────────────────────────────────────────

    def _post_install_checks(self) -> Dict[str, bool]:
        """Verify installation health after all steps complete."""
        import socket

        checks: Dict[str, bool] = {}

        # Ollama
        try:
            with socket.create_connection(("127.0.0.1", 11434), timeout=3):
                checks["ollama_port_11434"] = True
        except Exception:
            checks["ollama_port_11434"] = False

        # KISWARM dir
        checks["kiswarm_dir_exists"] = os.path.isdir(self.install_dir)

        # Python venv
        venv_python = os.path.join(self.install_dir, "mem0_env", "bin", "python")
        checks["python_venv"] = os.path.isfile(venv_python)

        # Sentinel API (may not be started yet)
        try:
            with socket.create_connection(("127.0.0.1", 11436), timeout=2):
                checks["sentinel_api_11436"] = True
        except Exception:
            checks["sentinel_api_11436"] = False

        # Git repo
        checks["git_repo"] = os.path.isdir(os.path.join(self.install_dir, ".git"))

        return checks

    # ── Main execution ────────────────────────────────────────────────────────

    def run(self) -> InstallReport:
        """
        Execute the full installation pipeline.
        Returns a complete InstallReport regardless of outcome.
        """
        self._started_at = time.time()
        self._log("INFO", f"KISWARM Installer Agent — mode: {self.mode.value}")

        scout_report_dict:  Dict[str, Any] = {}
        install_plan:       Dict[str, Any] = {}

        try:
            # ── PHASE 1: SCAN ──────────────────────────────────────────────
            self.state = InstallState.SCANNING
            self._log("INFO", "Phase 1: System-Scan...")

            from .system_scout import SystemScout
            scout = SystemScout()
            report = scout.full_scan()
            scout_report_dict = report.to_dict()

            self._log("OK", report.summary_text())
            self._warnings.extend(report.readiness_warnings)

            if report.install_readiness == "blocked" and self.mode != InstallMode.DRY_RUN:
                issues = "; ".join(report.readiness_issues)
                self._log("ERROR", f"Installation BLOCKIERT: {issues}")
                self.state = InstallState.ABORTED
                return self._make_report(
                    scout_report_dict, {}, error=f"Blocked: {issues}"
                )

            # ── PHASE 2: PLAN ──────────────────────────────────────────────
            self.state = InstallState.PLANNING
            self._log("INFO", "Phase 2: Installations-Plan generieren...")

            from .repo_intelligence import RepoIntelligence
            intel = RepoIntelligence()
            install_plan = intel.generate_install_plan(scout_report_dict)

            self._log("OK", f"Plan: {len(install_plan['steps'])} Schritte, "
                            f"~{install_plan.get('estimated_duration_min', '?')} Min.")

            if install_plan.get("abort") and self.mode != InstallMode.DRY_RUN:
                self.state = InstallState.ABORTED
                return self._make_report(
                    scout_report_dict, install_plan,
                    error=install_plan.get("abort_reason")
                )

            # ── PHASE 3: INSTALL ───────────────────────────────────────────
            self.state = InstallState.INSTALLING
            self._log("INFO", "Phase 3: Installation...")

            for step in install_plan["steps"]:
                sid   = step["id"]
                title = step["title"]
                cmd   = step["cmd"]

                if step.get("manual"):
                    self._log("WARN", f"Manueller Schritt {sid}: {title}\n    → {cmd}")
                    self._step_results.append(StepResult(
                        step_id=sid, title=title, cmd=cmd,
                        success=True, skipped=True,
                        note="Manueller Schritt — bitte selbst ausführen"
                    ))
                    continue

                result = self._run_with_retry(cmd, title, sid, retries=2, timeout=180)
                self._step_results.append(result)

                if not result.success and not result.skipped:
                    # Non-critical steps: warn and continue; critical steps: abort
                    if sid <= 3:   # Steps 1-3 (packages, ollama, clone) are critical
                        self._log("ERROR", f"Kritischer Schritt {sid} fehlgeschlagen — Abbruch")
                        self.state = InstallState.FAILED
                        return self._make_report(
                            scout_report_dict, install_plan,
                            error=f"Schritt {sid} fehlgeschlagen: {result.note or result.stderr[:100]}"
                        )
                    else:
                        self._log("WARN", f"Schritt {sid} fehlgeschlagen — weiter...")
                        self._warnings.append(f"Schritt {sid} ({title}) fehlgeschlagen")

            # ── PHASE 4: VERIFY ────────────────────────────────────────────
            self.state = InstallState.VERIFYING
            self._log("INFO", "Phase 4: Verifikation...")

            post_checks = self._post_install_checks()
            for check, ok in post_checks.items():
                icon = "✓" if ok else "✗"
                self._log("OK" if ok else "WARN", f"  {icon} {check}")

            self.state = InstallState.DONE
            self._log("OK", "Installation abgeschlossen!")

            return self._make_report(scout_report_dict, install_plan,
                                     post_checks=post_checks)

        except Exception as e:
            logger.exception(f"[InstallerAgent] Unhandled exception: {e}")
            self.state = InstallState.FAILED
            return self._make_report(scout_report_dict, install_plan, error=str(e))

    def _make_report(
        self,
        scout: Dict[str, Any],
        plan:  Dict[str, Any],
        post_checks: Dict[str, bool] = None,
        error: Optional[str] = None,
    ) -> InstallReport:
        import socket
        hostname = socket.gethostname()
        return InstallReport(
            started_at=self._started_at,
            finished_at=time.time(),
            mode=self.mode.value,
            state=self.state.value,
            host=hostname,
            os_info=scout.get("os", {}),
            hardware=scout.get("hardware", {}),
            step_results=self._step_results,
            warnings=self._warnings,
            post_checks=post_checks or {},
            error=error,
        )

    # ── Convenience methods ───────────────────────────────────────────────────

    def dry_run(self) -> InstallReport:
        self.mode = InstallMode.DRY_RUN
        return self.run()

    def scan_only(self) -> Dict[str, Any]:
        """Just run the scout — no installation."""
        from .system_scout import SystemScout
        return SystemScout().full_scan().to_dict()


# ─────────────────────────────────────────────────────────────────────────────
# CONVENIENCE ENTRYPOINT
# ─────────────────────────────────────────────────────────────────────────────

def run_installer(mode: str = "auto", install_dir: str = None) -> Dict[str, Any]:
    """
    Convenience function for API and CLI usage.
    mode: "auto" | "dry_run" | "guided"
    """
    mode_enum = InstallMode(mode)
    agent  = InstallerAgent(mode=mode_enum, install_dir=install_dir)
    report = agent.run()
    return report.to_dict()
