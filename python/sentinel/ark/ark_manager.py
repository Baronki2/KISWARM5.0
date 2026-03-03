"""
KISWARM v4.9 — Module 51: Ark Manager
=======================================
Downloads and updates Ark contents when online.
Manages disk space intelligently — never fills the disk.

Download strategy (industrial priority queue):
  1. CRITICAL items first — bootstrap capability before anything else
  2. Smallest files first within same priority — quick wins
  3. Models filtered by available RAM — no 14B model on a 4GB machine
  4. Pause if disk drops below 10GB free — never crash the host

Online sources:
  - Ollama models:   ollama pull (uses Ollama registry)
  - Python wheels:   pip download (uses PyPI)
  - OS packages:     apt-get download / dnf download
  - Git bundles:     git bundle create (from local repo)
  - Binaries:        direct URL download with verification

Offline mode:
  - ArkManager can run in "audit only" mode — checks what's missing
  - Never attempts downloads when KISWARM_OFFLINE=1
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from .software_ark import (
    ArkCategory, ArkItem, ArkItemState, ArkPriority, SoftwareArk
)

logger = logging.getLogger(__name__)

MIN_FREE_DISK   = 10 * 1024**3   # 10 GB — pause downloads below this
DOWNLOAD_CHUNK  = 65536
MAX_PARALLEL    = 2               # Max concurrent downloads


class DownloadResult:
    def __init__(self, item_id: str, success: bool,
                 error: Optional[str] = None, bytes_written: int = 0):
        self.item_id      = item_id
        self.success      = success
        self.error        = error
        self.bytes_written = bytes_written
        self.duration_s   = 0.0


class ArkManager:
    """
    Manages the Software Ark lifecycle:
    - Audit: what is missing?
    - Download: fetch missing items intelligently
    - Update: refresh outdated items
    - Maintain: keep disk space healthy
    """

    def __init__(
        self,
        ark:              Optional[SoftwareArk] = None,
        offline:          bool = False,
        on_progress:      Optional[Callable[[str, float], None]] = None,
    ):
        self.ark         = ark or SoftwareArk()
        self.offline     = offline or bool(os.environ.get("KISWARM_OFFLINE"))
        self.on_progress = on_progress   # callback(item_id, 0.0-1.0)

        self._active_downloads: Dict[str, bool] = {}
        self._lock = threading.Lock()

    # ── Audit ─────────────────────────────────────────────────────────────────

    def audit(self) -> Dict[str, Any]:
        """Full audit — what do we have, what do we need, what's the plan?"""
        status  = self.ark.status()
        missing = self.ark.missing_by_priority()
        disk    = self.ark.disk_status()

        plan = []
        for item in missing:
            plan.append({
                "item_id":    item.item_id,
                "name":       item.name,
                "priority":   item.priority,
                "size_human": item.size_human(),
                "can_download": item.source_url is not None,
            })

        return {
            "status":         status.to_dict(),
            "download_plan":  plan,
            "disk":           disk,
            "offline_mode":   self.offline,
            "recommendation": self._recommend(status, missing, disk),
        }

    def _recommend(self, status, missing, disk) -> str:
        if status.can_bootstrap:
            if not missing:
                return "Ark is complete. No action needed."
            return f"Bootstrap-capable. {len(missing)} optional items missing."
        gaps = len(status.bootstrap_gaps)
        if self.offline:
            return (f"OFFLINE MODE: {gaps} critical items missing. "
                    f"Cannot download. Transfer from peer or connect to internet.")
        free_gb = disk["disk_free_bytes"] / 1024**3
        if free_gb < 10:
            return (f"DISK WARNING: Only {free_gb:.1f}GB free. "
                    f"Free space before downloading.")
        return (f"{gaps} critical items missing. "
                f"Run fill_critical() to achieve bootstrap capability.")

    # ── Download orchestration ────────────────────────────────────────────────

    def fill_critical(self) -> List[DownloadResult]:
        """Download all CRITICAL missing items. Returns when complete or fails."""
        if self.offline:
            logger.warning("[ArkManager] Offline mode — skipping downloads")
            return []
        missing = [i for i in self.ark.missing_by_priority()
                   if i.priority == ArkPriority.CRITICAL.value]
        return self._download_list(missing)

    def fill_all(self, max_items: Optional[int] = None) -> List[DownloadResult]:
        """Download all missing items by priority. Stops at disk limit."""
        if self.offline:
            return []
        missing = self.ark.missing_by_priority()
        if max_items:
            missing = missing[:max_items]
        return self._download_list(missing)

    def _download_list(self, items: List[ArkItem]) -> List[DownloadResult]:
        results = []
        for item in items:
            if not self._check_disk():
                logger.error("[ArkManager] Insufficient disk — stopping downloads")
                break
            result = self._download_item(item)
            results.append(result)
            if result.success:
                logger.info(f"[ArkManager] ✓ {item.item_id} ({item.size_human()})")
            else:
                logger.warning(f"[ArkManager] ✗ {item.item_id}: {result.error}")
                if item.priority == ArkPriority.CRITICAL.value:
                    logger.error("[ArkManager] Critical item failed — stopping")
                    break
        return results

    def _download_item(self, item: ArkItem) -> DownloadResult:
        if not item.source_url:
            return DownloadResult(item.item_id, False,
                                  "No source URL — must be transferred from peer")
        t0 = time.time()
        try:
            if item.source_url.startswith("ollama://"):
                ok, err = self._pull_ollama_model(item)
            elif item.category == ArkCategory.SOURCE.value:
                ok, err = self._create_git_bundle(item)
            elif item.category == ArkCategory.PYTHON_PKG.value:
                ok, err = self._download_wheels(item)
            elif item.category == ArkCategory.OS_PKG.value:
                ok, err = self._download_os_packages(item)
            else:
                ok, err = self._download_url(item)

            result = DownloadResult(item.item_id, ok, err if not ok else None)
            result.duration_s = time.time() - t0
            return result
        except Exception as e:
            return DownloadResult(item.item_id, False, str(e))

    # ── Download methods ──────────────────────────────────────────────────────

    def _pull_ollama_model(self, item: ArkItem) -> tuple[bool, Optional[str]]:
        """Pull an Ollama model and store blob files in ark."""
        model_tag = item.source_url.replace("ollama://", "")
        logger.info(f"[ArkManager] Pulling Ollama model: {model_tag}")

        result = subprocess.run(
            ["ollama", "pull", model_tag],
            capture_output=True, text=True, timeout=3600
        )
        if result.returncode != 0:
            return False, result.stderr[:200]

        # Mark as present (Ollama manages the actual storage)
        dest_dir = os.path.join(self.ark.ark_dir, item.category)
        os.makedirs(dest_dir, exist_ok=True)
        marker = os.path.join(self.ark.ark_dir, item.rel_path)
        os.makedirs(os.path.dirname(marker), exist_ok=True)
        with open(marker, "w") as f:
            import json
            json.dump({"model": model_tag, "pulled_at": time.time()}, f)

        item.state         = ArkItemState.PRESENT.value
        item.last_verified = time.time()
        self.ark._save_inventory()
        return True, None

    def _create_git_bundle(self, item: ArkItem) -> tuple[bool, Optional[str]]:
        """Create a git bundle from local KISWARM repo."""
        repo_dir  = os.path.expanduser("~/KISWARM")
        dest_path = os.path.join(self.ark.ark_dir, item.rel_path)
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)

        if not os.path.exists(os.path.join(repo_dir, ".git")):
            return False, "KISWARM git repo not found at ~/KISWARM"

        result = subprocess.run(
            ["git", "bundle", "create", dest_path, "--all"],
            cwd=repo_dir, capture_output=True, text=True, timeout=120
        )
        if result.returncode != 0:
            return False, result.stderr[:200]

        stored = self.ark.store_file(item.item_id, dest_path)
        return stored is not None, None if stored else "store_file failed"

    def _download_wheels(self, item: ArkItem) -> tuple[bool, Optional[str]]:
        """Download all KISWARM Python dependencies as wheels."""
        dest_dir = os.path.join(self.ark.ark_dir, item.category)
        os.makedirs(dest_dir, exist_ok=True)

        # Get requirements from KISWARM repo
        req_file = os.path.expanduser("~/KISWARM/requirements.txt")
        if not os.path.exists(req_file):
            # Generate from known packages
            self._write_requirements(req_file)

        result = subprocess.run(
            [sys.executable, "-m", "pip", "download",
             "--dest", dest_dir,
             "--break-system-packages",
             "-r", req_file],
            capture_output=True, text=True, timeout=600
        )
        if result.returncode != 0:
            return False, result.stderr[:300]

        # Create tar bundle
        dest_path = os.path.join(self.ark.ark_dir, item.rel_path)
        subprocess.run(
            ["tar", "-czf", dest_path, "-C", dest_dir, "."],
            timeout=120
        )
        stored = self.ark.store_file(item.item_id, dest_path)
        return stored is not None, None

    def _download_os_packages(self, item: ArkItem) -> tuple[bool, Optional[str]]:
        """Cache OS packages for offline installation."""
        dest_dir  = os.path.join(self.ark.ark_dir, "os_pkg", "cache")
        dest_path = os.path.join(self.ark.ark_dir, item.rel_path)
        os.makedirs(dest_dir, exist_ok=True)

        pkgs = ["git", "curl", "python3", "python3-venv", "python3-pip",
                "build-essential", "ca-certificates"]

        if self.ark._os_family == "debian":
            result = subprocess.run(
                ["apt-get", "download", "--print-uris", *pkgs],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode == 0:
                subprocess.run(
                    ["apt-get", "-d", "install", "--reinstall", *pkgs,
                     "-o", f"Dir::Cache::archives={dest_dir}"],
                    capture_output=True, timeout=300
                )
        elif self.ark._os_family == "redhat":
            subprocess.run(
                ["dnf", "download", "--destdir", dest_dir, *pkgs],
                capture_output=True, timeout=300
            )

        # Bundle whatever we got
        subprocess.run(
            ["tar", "-czf", dest_path, "-C", dest_dir, "."],
            timeout=60
        )
        if os.path.exists(dest_path) and os.path.getsize(dest_path) > 1024:
            item.state         = ArkItemState.PRESENT.value
            item.last_verified = time.time()
            self.ark._save_inventory()
            return True, None
        return False, "Package download produced empty bundle"

    def _download_url(self, item: ArkItem) -> tuple[bool, Optional[str]]:
        """Generic HTTP download with progress tracking."""
        import urllib.request
        dest_path = os.path.join(self.ark.ark_dir, item.rel_path)
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)

        try:
            with tempfile.NamedTemporaryFile(delete=False) as tmp:
                tmp_path = tmp.name

            written = 0
            with urllib.request.urlopen(item.source_url, timeout=30) as resp:
                total = int(resp.headers.get("Content-Length", 0))
                with open(tmp_path, "wb") as f:
                    while True:
                        chunk = resp.read(DOWNLOAD_CHUNK)
                        if not chunk:
                            break
                        f.write(chunk)
                        written += len(chunk)
                        if self.on_progress and total:
                            self.on_progress(item.item_id, written / total)

            stored = self.ark.store_file(item.item_id, tmp_path)
            os.unlink(tmp_path)
            return stored is not None, None
        except Exception as e:
            return False, str(e)

    @staticmethod
    def _write_requirements(path: str) -> None:
        pkgs = [
            "ollama", "mem0", "qdrant-client", "chromadb", "tiktoken",
            "openai", "rich", "flask", "flask-cors", "requests", "numpy",
            "watchdog", "typing-extensions", "psutil", "sentence-transformers",
        ]
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write("\n".join(pkgs))

    # ── Disk management ───────────────────────────────────────────────────────

    def _check_disk(self) -> bool:
        free = shutil.disk_usage(self.ark.ark_dir).free
        return free > MIN_FREE_DISK

    def prune(self, keep_critical: bool = True) -> Dict[str, Any]:
        """
        Remove LOW priority items to free disk space.
        Never removes CRITICAL items if keep_critical=True.
        """
        removed = []
        freed   = 0

        for item in self.ark._inventory.values():
            if keep_critical and item.priority == ArkPriority.CRITICAL.value:
                continue
            if item.priority != ArkPriority.LOW.value:
                continue
            path = self.ark.item_path(item)
            if os.path.exists(path):
                size = os.path.getsize(path)
                os.remove(path)
                freed += size
                item.state = ArkItemState.MISSING.value
                removed.append(item.item_id)

        if removed:
            self.ark._save_inventory()

        return {
            "removed": removed,
            "freed_bytes": freed,
            "freed_human": self.ark._human(freed),
        }
