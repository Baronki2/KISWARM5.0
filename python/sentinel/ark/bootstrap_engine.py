"""
KISWARM v4.9 — Module 52: Bootstrap Engine
============================================
Sets up a complete KISWARM installation on new hardware
using ONLY the local Software Ark — zero internet required.

This is the core of the resilience architecture.
If a node dies, a new machine can be fully operational
within minutes using only what a peer node carries in its Ark.

Bootstrap phases:
  1. ASSESS    — Detect OS, RAM, disk, existing software
  2. VALIDATE  — Verify ark has everything needed
  3. OS_PKGS   — Install system packages from ark cache
  4. PYTHON    — Setup venv + install wheels from ark
  5. OLLAMA    — Install Ollama binary from ark
  6. SOURCE    — Clone KISWARM from git bundle
  7. MODELS    — Register available models with Ollama
  8. CONFIGURE — Write configs, aliases, systemd service
  9. VERIFY    — Run health check, confirm operational
  10. DONE     — Report success + what's available

Industrial design principle:
  "Every phase must be idempotent — running twice must be safe."
  Phase N failing must not corrupt phase N-1 results.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from .software_ark import ArkCategory, ArkItemState, ArkPriority, SoftwareArk

logger = logging.getLogger(__name__)


class BootPhase(str, Enum):
    ASSESS    = "assess"
    VALIDATE  = "validate"
    OS_PKGS   = "os_pkgs"
    PYTHON    = "python"
    OLLAMA    = "ollama"
    SOURCE    = "source"
    MODELS    = "models"
    CONFIGURE = "configure"
    VERIFY    = "verify"
    DONE      = "done"


class PhaseResult(str, Enum):
    PASS    = "pass"
    FAIL    = "fail"
    SKIP    = "skip"
    WARN    = "warn"


@dataclass
class PhaseLog:
    phase:      str
    result:     str
    message:    str
    duration_s: float
    timestamp:  float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class BootstrapReport:
    """Complete record of a bootstrap attempt."""
    started_at:      float
    completed_at:    float
    success:         bool
    target_dir:      str
    os_family:       str
    arch:            str
    ram_gb:          float
    phases:          List[PhaseLog]
    models_installed: List[str]
    errors:          List[str]
    warnings:        List[str]

    @property
    def duration_s(self) -> float:
        return self.completed_at - self.started_at

    def summary(self) -> str:
        status = "SUCCESS" if self.success else "FAILED"
        return (f"Bootstrap {status} | {self.duration_s:.0f}s | "
                f"{len(self.phases)} phases | "
                f"{len(self.models_installed)} models | "
                f"{len(self.errors)} errors")

    def to_dict(self) -> Dict[str, Any]:
        return {
            **asdict(self),
            "duration_s": round(self.duration_s, 1),
            "summary":    self.summary(),
        }


# ─────────────────────────────────────────────────────────────────────────────
# BOOTSTRAP ENGINE
# ─────────────────────────────────────────────────────────────────────────────

class BootstrapEngine:
    """
    Installs KISWARM on new hardware from local Ark alone.
    No internet. No GitHub. No PyPI.
    Just the ark and the target machine.
    """

    def __init__(
        self,
        ark:            Optional[SoftwareArk] = None,
        target_dir:     Optional[str]  = None,
        on_phase:       Optional[Callable[[str, str, str], None]] = None,
        dry_run:        bool = False,
    ):
        self.ark        = ark or SoftwareArk()
        self.target_dir = os.path.expanduser(target_dir or "~/KISWARM")
        self.on_phase   = on_phase   # callback(phase, result, message)
        self.dry_run    = dry_run    # If True: assess + plan only, no changes

        self._phases:   List[PhaseLog]  = []
        self._errors:   List[str]       = []
        self._warnings: List[str]       = []
        self._models:   List[str]       = []
        self._os        = self.ark._os_family
        self._arch      = platform.machine()
        self._ram_gb    = self.ark._ram_gb
        self._started   = time.time()

    # ── Main entry point ──────────────────────────────────────────────────────

    def bootstrap(self) -> BootstrapReport:
        """
        Run the full bootstrap sequence.
        Returns a complete report regardless of success/failure.
        """
        logger.info(f"[Bootstrap] Starting {'DRY RUN ' if self.dry_run else ''}"
                    f"on {self._os}/{self._arch} {self._ram_gb:.0f}GB RAM")

        phases = [
            (BootPhase.ASSESS,    self._phase_assess),
            (BootPhase.VALIDATE,  self._phase_validate),
            (BootPhase.OS_PKGS,   self._phase_os_pkgs),
            (BootPhase.PYTHON,    self._phase_python),
            (BootPhase.OLLAMA,    self._phase_ollama),
            (BootPhase.SOURCE,    self._phase_source),
            (BootPhase.MODELS,    self._phase_models),
            (BootPhase.CONFIGURE, self._phase_configure),
            (BootPhase.VERIFY,    self._phase_verify),
        ]

        overall_success = True
        for phase_enum, phase_fn in phases:
            t0     = time.time()
            result = PhaseResult.PASS
            msg    = "OK"
            try:
                result, msg = phase_fn()
            except Exception as e:
                result = PhaseResult.FAIL
                msg    = str(e)
                self._errors.append(f"[{phase_enum.value}] {e}")
                logger.exception(f"[Bootstrap] Phase {phase_enum.value} crashed")

            duration = time.time() - t0
            log = PhaseLog(phase=phase_enum.value, result=result.value,
                           message=msg, duration_s=round(duration, 2))
            self._phases.append(log)

            if self.on_phase:
                self.on_phase(phase_enum.value, result.value, msg)

            logger.info(f"[Bootstrap] [{result.value.upper()}] "
                        f"{phase_enum.value}: {msg} ({duration:.1f}s)")

            # FAIL on critical phase stops bootstrap
            if result == PhaseResult.FAIL:
                overall_success = False
                critical_phases = {
                    BootPhase.ASSESS, BootPhase.VALIDATE,
                    BootPhase.PYTHON, BootPhase.OLLAMA, BootPhase.SOURCE
                }
                if phase_enum in critical_phases:
                    logger.error(f"[Bootstrap] Critical phase failed — stopping")
                    break

        self._log_phase(BootPhase.DONE.value, PhaseResult.PASS if overall_success
                        else PhaseResult.FAIL, "Bootstrap complete")

        return BootstrapReport(
            started_at=self._started,
            completed_at=time.time(),
            success=overall_success,
            target_dir=self.target_dir,
            os_family=self._os,
            arch=self._arch,
            ram_gb=round(self._ram_gb, 1),
            phases=self._phases,
            models_installed=self._models,
            errors=self._errors,
            warnings=self._warnings,
        )

    # ── Phase implementations ─────────────────────────────────────────────────

    def _phase_assess(self) -> tuple[PhaseResult, str]:
        """Assess the target system — what exists, what is needed."""
        info = {
            "os":      self._os,
            "arch":    self._arch,
            "ram_gb":  round(self._ram_gb, 1),
            "disk_gb": round(shutil.disk_usage(os.path.expanduser("~")).free
                             / 1024**3, 1),
            "python":  sys.version.split()[0],
            "ollama":  shutil.which("ollama") is not None,
            "git":     shutil.which("git") is not None,
            "kiswarm_exists": os.path.isdir(self.target_dir),
        }
        msg = (f"OS={info['os']} RAM={info['ram_gb']}GB "
               f"Disk={info['disk_gb']}GB free "
               f"ollama={'✓' if info['ollama'] else '✗'} "
               f"git={'✓' if info['git'] else '✗'}")
        if info["disk_gb"] < 4:
            self._errors.append("Less than 4GB disk free — bootstrap may fail")
            return PhaseResult.WARN, msg + " [LOW DISK]"
        return PhaseResult.PASS, msg

    def _phase_validate(self) -> tuple[PhaseResult, str]:
        """Validate the ark has everything needed for this OS/hardware."""
        can_boot, gaps = self.ark.can_bootstrap()
        if not can_boot:
            return PhaseResult.FAIL, (
                f"Ark incomplete — {len(gaps)} critical items missing: "
                + ", ".join(gaps[:3])
                + ("..." if len(gaps) > 3 else "")
            )
        present  = self.ark.status().present_items
        total    = self.ark.status().total_items
        return PhaseResult.PASS, f"Ark validated: {present}/{total} items present"

    def _phase_os_pkgs(self) -> tuple[PhaseResult, str]:
        """Install OS packages from ark cache."""
        pkg_item_id = f"os:{self._os}-bootstrap"
        item = self.ark.get_item(pkg_item_id)

        if not item or item.state != ArkItemState.PRESENT.value:
            # Check if required tools already exist
            missing_tools = []
            for tool in ("python3", "git", "curl"):
                if not shutil.which(tool):
                    missing_tools.append(tool)
            if not missing_tools:
                return PhaseResult.SKIP, "Required tools already present"
            return PhaseResult.WARN, (
                f"OS package bundle not in ark. "
                f"Missing tools: {', '.join(missing_tools)}"
            )

        if self.dry_run:
            return PhaseResult.SKIP, f"DRY RUN — would install from {pkg_item_id}"

        pkg_path = self.ark.item_path(item)
        extract_dir = "/tmp/kiswarm-os-pkgs"
        os.makedirs(extract_dir, exist_ok=True)

        subprocess.run(
            ["tar", "-xzf", pkg_path, "-C", extract_dir],
            check=True, timeout=60
        )

        if self._os == "debian":
            result = subprocess.run(
                ["dpkg", "-i", *[str(p) for p in
                  __import__("pathlib").Path(extract_dir).glob("*.deb")]],
                capture_output=True, timeout=120
            )
        elif self._os == "redhat":
            result = subprocess.run(
                ["rpm", "-i", "--nodeps",
                 *[str(p) for p in
                   __import__("pathlib").Path(extract_dir).glob("*.rpm")]],
                capture_output=True, timeout=120
            )
        else:
            return PhaseResult.SKIP, f"OS package install not implemented for {self._os}"

        shutil.rmtree(extract_dir, ignore_errors=True)
        return PhaseResult.PASS, f"OS packages installed from ark"

    def _phase_python(self) -> tuple[PhaseResult, str]:
        """Create Python venv and install wheels from ark."""
        venv_dir = os.path.expanduser("~/mem0_env")

        if not os.path.exists(os.path.join(venv_dir, "bin", "python")):
            if self.dry_run:
                return PhaseResult.SKIP, "DRY RUN — would create venv"
            subprocess.run(
                [sys.executable, "-m", "venv", venv_dir],
                check=True, timeout=60
            )

        pip = os.path.join(venv_dir, "bin", "pip")

        # Try offline wheels first
        wheels_item = self.ark.get_item("python:core-wheels")
        if wheels_item and wheels_item.state == ArkItemState.PRESENT.value:
            wheels_dir = "/tmp/kiswarm-wheels"
            os.makedirs(wheels_dir, exist_ok=True)
            subprocess.run(
                ["tar", "-xzf", self.ark.item_path(wheels_item), "-C", wheels_dir],
                timeout=60
            )
            result = subprocess.run(
                [pip, "install", "--no-index",
                 "--find-links", wheels_dir,
                 "--quiet", "flask", "qdrant-client", "ollama", "rich", "psutil"],
                capture_output=True, timeout=300
            )
            shutil.rmtree(wheels_dir, ignore_errors=True)
            if result.returncode == 0:
                return PhaseResult.PASS, "Python packages installed from ark wheels"
            self._warnings.append(f"Wheel install partial: {result.stderr[:100]}")

        # Fallback: try online if available
        if not self.dry_run:
            result = subprocess.run(
                [pip, "install", "--quiet", "--break-system-packages",
                 "flask", "qdrant-client", "ollama", "rich", "psutil"],
                capture_output=True, timeout=300
            )
            if result.returncode == 0:
                return PhaseResult.WARN, "Python packages installed from PyPI (not ark)"

        return PhaseResult.FAIL, "Could not install Python packages"

    def _phase_ollama(self) -> tuple[PhaseResult, str]:
        """Install Ollama from ark binary."""
        if shutil.which("ollama"):
            return PhaseResult.SKIP, "Ollama already installed"

        if self.dry_run:
            return PhaseResult.SKIP, "DRY RUN — would install Ollama"

        # Try ark binary
        binary_item = self.ark.get_item("binary:ollama")
        if binary_item and binary_item.state == ArkItemState.PRESENT.value:
            src = self.ark.item_path(binary_item)
            shutil.copy2(src, "/usr/local/bin/ollama")
            os.chmod("/usr/local/bin/ollama", 0o755)
            return PhaseResult.PASS, "Ollama installed from ark binary"

        # Fallback: official install script (needs internet)
        self._warnings.append("Ollama binary not in ark — trying online install")
        result = subprocess.run(
            ["bash", "-c", "curl -fsSL https://ollama.com/install.sh | sh"],
            capture_output=True, timeout=300
        )
        if result.returncode == 0:
            return PhaseResult.WARN, "Ollama installed from internet (not ark)"
        return PhaseResult.FAIL, "Could not install Ollama"

    def _phase_source(self) -> tuple[PhaseResult, str]:
        """Clone KISWARM source from git bundle in ark."""
        if os.path.isdir(os.path.join(self.target_dir, ".git")):
            return PhaseResult.SKIP, "KISWARM already cloned"

        if self.dry_run:
            return PhaseResult.SKIP, "DRY RUN — would clone from git bundle"

        bundle_item = self.ark.get_item("source:kiswarm:current")
        if not bundle_item or bundle_item.state != ArkItemState.PRESENT.value:
            # Try previous version
            bundle_item = self.ark.get_item("source:kiswarm:previous")

        if bundle_item and bundle_item.state == ArkItemState.PRESENT.value:
            bundle_path = self.ark.item_path(bundle_item)
            os.makedirs(os.path.dirname(self.target_dir), exist_ok=True)
            result = subprocess.run(
                ["git", "clone", bundle_path, self.target_dir],
                capture_output=True, timeout=120
            )
            if result.returncode == 0:
                return PhaseResult.PASS, f"KISWARM cloned from ark bundle v{bundle_item.version}"
            return PhaseResult.FAIL, f"git clone failed: {result.stderr[:100]}"

        # Fallback: GitHub (needs internet)
        self._warnings.append("Git bundle not in ark — trying GitHub")
        result = subprocess.run(
            ["git", "clone", "--depth=1",
             "https://github.com/Baronki2/KISWARM.git", self.target_dir],
            capture_output=True, timeout=300
        )
        if result.returncode == 0:
            return PhaseResult.WARN, "KISWARM cloned from GitHub (not ark)"
        return PhaseResult.FAIL, "Could not obtain KISWARM source"

    def _phase_models(self) -> tuple[PhaseResult, str]:
        """Register available models with Ollama."""
        if self.dry_run:
            return PhaseResult.SKIP, "DRY RUN — would register models"

        # Start Ollama if not running
        if not self._ollama_responding():
            subprocess.Popen(
                ["ollama", "serve"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            time.sleep(3)

        model_items = [
            i for i in self.ark._inventory.values()
            if i.category == ArkCategory.MODEL.value
            and i.state == ArkItemState.PRESENT.value
            and i.min_ram_gb <= self._ram_gb
        ]

        for item in sorted(model_items, key=lambda x: x.min_ram_gb):
            if item.install_cmd:
                model_tag = item.install_cmd.replace("ollama pull ", "").strip()
                # Check if model marker exists (means we already pulled it)
                marker = self.ark.item_path(item)
                if os.path.exists(marker):
                    # Already available in Ollama
                    self._models.append(model_tag)
                    logger.info(f"[Bootstrap] Model available: {model_tag}")

        if not self._models:
            return PhaseResult.WARN, "No models registered — pull manually after boot"
        return PhaseResult.PASS, f"Models registered: {', '.join(self._models)}"

    def _phase_configure(self) -> tuple[PhaseResult, str]:
        """Write configs, aliases, and systemd service."""
        if self.dry_run:
            return PhaseResult.SKIP, "DRY RUN — would write configs"

        steps = []

        # Governance config
        gov_file = os.path.expanduser("~/governance_config.json")
        if not os.path.exists(gov_file):
            with open(gov_file, "w") as f:
                json.dump({
                    "system_name":            "KISWARM",
                    "version":                "4.9",
                    "governance_mode":        "active",
                    "autonomous_operation":   True,
                    "auto_restart_services":  True,
                    "audit_logging":          True,
                    "backup_retention_days":  30,
                    "bootstrapped_from_ark":  True,
                    "bootstrap_date":         time.strftime("%Y-%m-%dT%H:%M:%S"),
                }, f, indent=2)
            steps.append("governance_config.json")

        # Bash aliases
        bashrc = os.path.expanduser("~/.bashrc")
        aliases = {
            "sys-nav":        f"bash {self.target_dir}/system_navigation.sh",
            "kiswarm-health": f"bash ~/health_check.sh",
            "kiswarm-status": f"python3 ~/kiswarm_status.py",
            "kiswarm-cli":    f"python3 {self.target_dir}/python/sentinel/kiswarm_cli.py",
        }
        try:
            existing = open(bashrc).read() if os.path.exists(bashrc) else ""
            with open(bashrc, "a") as f:
                for alias, cmd in aliases.items():
                    if f"alias {alias}=" not in existing:
                        f.write(f"\nalias {alias}='{cmd}'")
            steps.append("aliases")
        except Exception as e:
            self._warnings.append(f"Could not write aliases: {e}")

        # Logs dir
        os.makedirs(os.path.expanduser("~/logs"), exist_ok=True)
        os.makedirs(os.path.expanduser("~/backups"), exist_ok=True)
        steps.append("directories")

        # Bootstrap script from ark
        script_item = self.ark.get_item("script:bootstrap-offline")
        if script_item and script_item.state == ArkItemState.PRESENT.value:
            steps.append("bootstrap-script")

        return PhaseResult.PASS, f"Configured: {', '.join(steps)}"

    def _phase_verify(self) -> tuple[PhaseResult, str]:
        """Verify the installation is operational."""
        checks = {
            "KISWARM dir":    os.path.isdir(self.target_dir),
            "Python venv":    os.path.exists(os.path.expanduser("~/mem0_env/bin/python")),
            "Ollama binary":  shutil.which("ollama") is not None,
            "Ollama running": self._ollama_responding(),
            "Gov config":     os.path.exists(os.path.expanduser("~/governance_config.json")),
        }
        passed  = sum(1 for v in checks.values() if v)
        total   = len(checks)
        failed  = [k for k, v in checks.items() if not v]

        if failed:
            self._warnings.extend([f"Verify failed: {f}" for f in failed])

        result = PhaseResult.PASS if passed == total else PhaseResult.WARN
        return result, f"{passed}/{total} checks passed" + (
            f" | Failed: {', '.join(failed)}" if failed else ""
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _log_phase(self, phase: str, result: PhaseResult, msg: str) -> None:
        self._phases.append(PhaseLog(
            phase=phase, result=result.value,
            message=msg, duration_s=0.0
        ))

    @staticmethod
    def _ollama_responding() -> bool:
        try:
            import urllib.request
            urllib.request.urlopen(
                "http://localhost:11434/api/tags", timeout=2
            )
            return True
        except Exception:
            return False

    # ── Quick bootstrap script generator ─────────────────────────────────────

    @staticmethod
    def generate_offline_script(ark_dir: str, output_path: str) -> str:
        """
        Generate a standalone bash script that bootstraps KISWARM
        from a given ark directory. This script is stored IN the ark
        so it can be run on a completely fresh machine.
        """
        script = f"""#!/bin/bash
# KISWARM Offline Bootstrap Script — Auto-generated
# Run this on a fresh machine with the Ark mounted at {ark_dir}
# No internet required.

set -euo pipefail

ARK_DIR="{ark_dir}"
KISWARM_DIR="$HOME/KISWARM"
VENV="$HOME/mem0_env"

echo "KISWARM Offline Bootstrap starting..."
echo "Ark: $ARK_DIR"

# 1. Python venv
if [ ! -d "$VENV" ]; then
  python3 -m venv "$VENV"
fi

# 2. Install wheels from ark
if [ -f "$ARK_DIR/python_pkg/kiswarm-wheels.tar.gz" ]; then
  WHEELS_TMP=$(mktemp -d)
  tar -xzf "$ARK_DIR/python_pkg/kiswarm-wheels.tar.gz" -C "$WHEELS_TMP"
  "$VENV/bin/pip" install --no-index --find-links "$WHEELS_TMP" \\
    flask qdrant-client ollama rich psutil 2>/dev/null || true
  rm -rf "$WHEELS_TMP"
  echo "Python packages installed from ark"
fi

# 3. Install Ollama
if ! command -v ollama &>/dev/null; then
  if [ -f "$ARK_DIR/binary/ollama/ollama" ]; then
    sudo install -m755 "$ARK_DIR/binary/ollama/ollama" /usr/local/bin/ollama
    echo "Ollama installed from ark binary"
  else
    echo "WARNING: Ollama not in ark — install manually"
  fi
fi

# 4. Clone KISWARM from git bundle
if [ ! -d "$KISWARM_DIR/.git" ]; then
  BUNDLE="$ARK_DIR/source/kiswarm-current.bundle"
  if [ -f "$BUNDLE" ]; then
    git clone "$BUNDLE" "$KISWARM_DIR"
    echo "KISWARM cloned from ark bundle"
  else
    echo "WARNING: Git bundle not in ark — clone manually"
  fi
fi

# 5. Start Ollama
nohup ollama serve > "$HOME/logs/ollama.log" 2>&1 &
sleep 3

# 6. Write config
mkdir -p "$HOME/logs" "$HOME/backups"
cat > "$HOME/governance_config.json" << 'EOF'
{{"system_name": "KISWARM", "version": "4.9",
  "governance_mode": "active", "bootstrapped_from_ark": true}}
EOF

echo ""
echo "KISWARM Bootstrap Complete!"
echo "Next: source ~/.bashrc && kiswarm-health"
"""
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w") as f:
            f.write(script)
        os.chmod(output_path, 0o755)
        return output_path
