"""
KISWARM v4.9 — Module 50: Software Ark
========================================
The local software depot. Every KISWARM node carries its own complete
installation universe — independent of PyPI, GitHub, apt servers, or
Ollama registry.

Design principles (from industrial embedded systems):
  "A system that cannot recover itself from local media is not resilient.
   It is merely tolerant — until the network dies."

The Ark stores:
  1. Ollama binary + core AI models (the brain)
  2. Python packages as wheel files (the runtime)
  3. OS packages as cached files (the foundation)
  4. KISWARM source snapshots as git bundles (the system)
  5. Bootstrap scripts (the installer without internet)

100GB allocation strategy:
  ├── models/          ~45GB  Ollama models (tiered by RAM requirement)
  ├── python_wheels/   ~8GB   pip-installable offline
  ├── os_packages/     ~5GB   apt/dnf/pacman cache
  ├── source/          ~0.5GB KISWARM git bundles (3 versions)
  ├── docker/          ~15GB  Container images (optional)
  └── buffer/          ~26GB  Free for new models, expansion

Every item has:
  - SHA-256 checksum (integrity)
  - Version tag
  - Size in bytes
  - Compatibility metadata (OS, arch, RAM requirement)
  - Priority (CRITICAL / HIGH / NORMAL / LOW)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import shutil
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

ARK_VERSION     = "4.9"
TARGET_ARK_SIZE = 100 * 1024**3   # 100 GB
MIN_ARK_SIZE    =   4 * 1024**3   # 4 GB  — absolute minimum for bootstrap

# Default ark location — can be overridden via KISWARM_ARK_DIR env var
DEFAULT_ARK_DIR = os.path.expanduser("~/KISWARM/.ark")


class ArkPriority(str, Enum):
    CRITICAL = "critical"   # Without this, bootstrap fails
    HIGH     = "high"       # Needed for standard operation
    NORMAL   = "normal"     # Improves capability
    LOW      = "low"        # Nice to have


class ArkCategory(str, Enum):
    MODEL       = "model"        # Ollama AI models
    BINARY      = "binary"       # Executables (ollama, git, python3)
    PYTHON_PKG  = "python_pkg"   # Python wheel files
    OS_PKG      = "os_pkg"       # System package cache
    SOURCE      = "source"       # KISWARM git bundles
    DOCKER      = "docker"       # Container images
    CONFIG      = "config"       # Default configurations
    SCRIPT      = "script"       # Bootstrap/install scripts


class ArkItemState(str, Enum):
    PRESENT     = "present"      # File exists and checksum matches
    MISSING     = "missing"      # Not in ark yet
    CORRUPTED   = "corrupted"    # File exists but checksum fails
    OUTDATED    = "outdated"     # Newer version available
    DOWNLOADING = "downloading"  # Currently being fetched


# ─────────────────────────────────────────────────────────────────────────────
# DATA STRUCTURES
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ArkItem:
    """A single item managed by the Software Ark."""
    item_id:        str             # Unique identifier e.g. "model:qwen2.5:3b"
    name:           str             # Human-readable name
    category:       str             # ArkCategory value
    priority:       str             # ArkPriority value
    version:        str
    filename:       str             # Relative path within ark dir
    size_bytes:     int             # Expected size (0 = unknown)
    sha256:         Optional[str]   # Expected checksum (None = skip check)
    state:          str             # ArkItemState value
    os_family:      Optional[str]   # None = universal, "debian"/"redhat"/etc
    arch:           Optional[str]   # None = any, "x86_64"/"arm64"
    min_ram_gb:     float           # Minimum RAM to use this item
    description:    str
    source_url:     Optional[str]   # Where to download if missing (may be None offline)
    install_cmd:    Optional[str]   # How to install once unpacked
    tags:           List[str]       = field(default_factory=list)
    added_at:       float           = field(default_factory=time.time)
    last_verified:  float           = 0.0

    @property
    def rel_path(self) -> str:
        return os.path.join(self.category, self.filename)

    def size_human(self) -> str:
        b = self.size_bytes
        for unit in ("B", "KB", "MB", "GB"):
            if b < 1024:
                return f"{b:.1f}{unit}"
            b /= 1024
        return f"{b:.1f}TB"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ArkItem":
        return cls(**{k: v for k, v in d.items()
                      if k in cls.__dataclass_fields__})


@dataclass
class ArkStatus:
    """Current state of the Software Ark."""
    ark_dir:            str
    total_items:        int
    present_items:      int
    missing_items:      int
    corrupted_items:    int
    critical_present:   int
    critical_total:     int
    disk_used_bytes:    int
    disk_free_bytes:    int
    can_bootstrap:      bool
    bootstrap_gaps:     List[str]   # Names of missing CRITICAL items
    last_integrity_check: float
    os_family:          str
    arch:               str
    ram_gb:             float

    @property
    def health_score(self) -> float:
        if self.total_items == 0:
            return 0.0
        return self.present_items / self.total_items

    @property
    def critical_complete(self) -> bool:
        return self.critical_present >= self.critical_total

    def to_dict(self) -> Dict[str, Any]:
        return {
            **asdict(self),
            "health_score":       round(self.health_score, 2),
            "critical_complete":  self.critical_complete,
            "disk_used_human":    self._human(self.disk_used_bytes),
            "disk_free_human":    self._human(self.disk_free_bytes),
        }

    @staticmethod
    def _human(b: int) -> str:
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if b < 1024:
                return f"{b:.1f}{unit}"
            b //= 1024
        return f"{b}PB"


# ─────────────────────────────────────────────────────────────────────────────
# SOFTWARE ARK
# ─────────────────────────────────────────────────────────────────────────────

class SoftwareArk:
    """
    The local software depot for a KISWARM node.
    
    Manages the complete inventory of software needed to:
    1. Run KISWARM fully (operational mode)
    2. Bootstrap a new node from scratch (recovery mode)
    3. Transfer capabilities to a peer (mesh mode)
    
    The ark is self-describing: inventory.json contains everything
    needed to understand, verify and use the depot contents.
    """

    INVENTORY_FILE = "inventory.json"

    def __init__(self, ark_dir: Optional[str] = None):
        self.ark_dir = os.path.expanduser(
            ark_dir or os.environ.get("KISWARM_ARK_DIR", DEFAULT_ARK_DIR)
        )
        self._inventory: Dict[str, ArkItem] = {}
        self._os_family  = self._detect_os()
        self._arch       = platform.machine()
        self._ram_gb     = self._detect_ram()

        # Create directory structure
        for cat in ArkCategory:
            os.makedirs(os.path.join(self.ark_dir, cat.value), exist_ok=True)

        self._load_inventory()
        self._seed_default_catalog()

    # ── OS / Hardware detection ───────────────────────────────────────────────

    @staticmethod
    def _detect_os() -> str:
        if platform.system() == "Darwin":
            return "macos"
        try:
            content = Path("/etc/os-release").read_text().lower()
            if "ubuntu" in content or "debian" in content:  return "debian"
            if "fedora" in content or "rhel"   in content:  return "redhat"
            if "arch"   in content or "manjaro" in content: return "arch"
        except Exception:
            pass
        return "unknown"

    @staticmethod
    def _detect_ram() -> float:
        try:
            import psutil
            return psutil.virtual_memory().total / 1024**3
        except Exception:
            try:
                with open("/proc/meminfo") as f:
                    for line in f:
                        if line.startswith("MemTotal"):
                            kb = int(line.split()[1])
                            return kb / 1024 / 1024
            except Exception:
                pass
        return 8.0  # Conservative default

    # ── Inventory management ──────────────────────────────────────────────────

    def _inv_path(self) -> str:
        return os.path.join(self.ark_dir, self.INVENTORY_FILE)

    def _load_inventory(self) -> None:
        path = self._inv_path()
        if not os.path.exists(path):
            self._inventory = {}
            return
        try:
            with open(path) as f:
                data = json.load(f)
            self._inventory = {
                k: ArkItem.from_dict(v)
                for k, v in data.get("items", {}).items()
            }
            logger.info(f"[Ark] Loaded {len(self._inventory)} items from inventory")
        except Exception as e:
            logger.error(f"[Ark] Inventory load failed: {e}")
            self._inventory = {}

    def _save_inventory(self) -> None:
        path = self._inv_path()
        data = {
            "ark_version":  ARK_VERSION,
            "saved_at":     time.time(),
            "node_os":      self._os_family,
            "node_arch":    self._arch,
            "node_ram_gb":  round(self._ram_gb, 1),
            "items":        {k: v.to_dict() for k, v in self._inventory.items()},
        }
        with open(path, "w") as f:
            json.dump(data, f, indent=2)

    def _seed_default_catalog(self) -> None:
        """Populate the catalog with known items — even if not yet downloaded."""
        defaults = self._default_catalog()
        added = 0
        for item in defaults:
            if item.item_id not in self._inventory:
                self._inventory[item.item_id] = item
                added += 1
        if added:
            self._save_inventory()
            logger.info(f"[Ark] Seeded {added} catalog entries")

    def _default_catalog(self) -> List[ArkItem]:
        """
        The canonical KISWARM ark catalog.
        Items listed here are KNOWN — they may not yet be PRESENT.
        Priority CRITICAL = needed for any bootstrap.
        """
        return [
            # ── Ollama binary ─────────────────────────────────────────────────
            ArkItem(
                item_id="binary:ollama",
                name="Ollama Runtime",
                category=ArkCategory.BINARY.value,
                priority=ArkPriority.CRITICAL.value,
                version="latest",
                filename="ollama/ollama",
                size_bytes=50 * 1024**2,   # ~50MB
                sha256=None,
                state=ArkItemState.MISSING.value,
                os_family=None, arch=None, min_ram_gb=2.0,
                description="Ollama binary for local LLM inference",
                source_url="https://ollama.com/install.sh",
                install_cmd="install -m755 {file} /usr/local/bin/ollama",
            ),
            # ── AI Models (tiered by RAM requirement) ─────────────────────────
            ArkItem(
                item_id="model:qwen2.5:0.5b",
                name="Qwen2.5 0.5B (Ultra-light)",
                category=ArkCategory.MODEL.value,
                priority=ArkPriority.CRITICAL.value,
                version="qwen2.5:0.5b",
                filename="models/qwen2.5-0.5b.gguf",
                size_bytes=400 * 1024**2,   # ~400MB
                sha256=None,
                state=ArkItemState.MISSING.value,
                os_family=None, arch=None, min_ram_gb=1.0,
                description="Minimum viable AI — runs on 1GB RAM, always present",
                source_url="ollama://qwen2.5:0.5b",
                install_cmd="ollama pull qwen2.5:0.5b",
                tags=["bootstrap", "minimum", "always"],
            ),
            ArkItem(
                item_id="model:qwen2.5:3b",
                name="Qwen2.5 3B (Standard)",
                category=ArkCategory.MODEL.value,
                priority=ArkPriority.HIGH.value,
                version="qwen2.5:3b",
                filename="models/qwen2.5-3b.gguf",
                size_bytes=2 * 1024**3,   # ~2GB
                sha256=None,
                state=ArkItemState.MISSING.value,
                os_family=None, arch=None, min_ram_gb=4.0,
                description="Standard model — good quality, reasonable RAM",
                source_url="ollama://qwen2.5:3b",
                install_cmd="ollama pull qwen2.5:3b",
                tags=["standard", "installer-agent"],
            ),
            ArkItem(
                item_id="model:qwen2.5:7b",
                name="Qwen2.5 7B (Full)",
                category=ArkCategory.MODEL.value,
                priority=ArkPriority.NORMAL.value,
                version="qwen2.5:7b",
                filename="models/qwen2.5-7b.gguf",
                size_bytes=4_500 * 1024**2,
                sha256=None,
                state=ArkItemState.MISSING.value,
                os_family=None, arch=None, min_ram_gb=8.0,
                description="Full capability model for 8GB+ RAM systems",
                source_url="ollama://qwen2.5:7b",
                install_cmd="ollama pull qwen2.5:7b",
                tags=["full", "advisory"],
            ),
            ArkItem(
                item_id="model:qwen2.5:14b",
                name="Qwen2.5 14B (High-Performance)",
                category=ArkCategory.MODEL.value,
                priority=ArkPriority.NORMAL.value,
                version="qwen2.5:14b",
                filename="models/qwen2.5-14b.gguf",
                size_bytes=9 * 1024**3,
                sha256=None,
                state=ArkItemState.MISSING.value,
                os_family=None, arch=None, min_ram_gb=16.0,
                description="High-performance model for 16GB+ RAM systems",
                source_url="ollama://qwen2.5:14b",
                install_cmd="ollama pull qwen2.5:14b",
                tags=["high-performance"],
            ),
            ArkItem(
                item_id="model:nomic-embed-text",
                name="Nomic Embed Text (Embeddings)",
                category=ArkCategory.MODEL.value,
                priority=ArkPriority.HIGH.value,
                version="nomic-embed-text:latest",
                filename="models/nomic-embed-text.gguf",
                size_bytes=274 * 1024**2,
                sha256=None,
                state=ArkItemState.MISSING.value,
                os_family=None, arch=None, min_ram_gb=1.0,
                description="Embedding model for semantic memory search",
                source_url="ollama://nomic-embed-text",
                install_cmd="ollama pull nomic-embed-text",
                tags=["embedding", "memory", "qdrant"],
            ),
            # ── Python wheels ─────────────────────────────────────────────────
            ArkItem(
                item_id="python:core-wheels",
                name="KISWARM Core Python Wheels",
                category=ArkCategory.PYTHON_PKG.value,
                priority=ArkPriority.CRITICAL.value,
                version="4.9",
                filename="python_pkg/kiswarm-wheels.tar.gz",
                size_bytes=500 * 1024**2,
                sha256=None,
                state=ArkItemState.MISSING.value,
                os_family=None, arch=None, min_ram_gb=0.0,
                description="All KISWARM Python dependencies as offline wheels",
                source_url=None,
                install_cmd="pip install --no-index --find-links={dir} -r requirements.txt",
                tags=["offline", "pip", "bootstrap"],
            ),
            # ── OS packages ───────────────────────────────────────────────────
            ArkItem(
                item_id="os:debian-bootstrap",
                name="Debian/Ubuntu Bootstrap Packages",
                category=ArkCategory.OS_PKG.value,
                priority=ArkPriority.CRITICAL.value,
                version="current",
                filename="os_pkg/debian-bootstrap.tar.gz",
                size_bytes=200 * 1024**2,
                sha256=None,
                state=ArkItemState.MISSING.value,
                os_family="debian", arch=None, min_ram_gb=0.0,
                description="git curl python3 python3-venv build-essential",
                source_url=None,
                install_cmd="dpkg -i {dir}/*.deb",
                tags=["offline", "apt", "bootstrap"],
            ),
            ArkItem(
                item_id="os:redhat-bootstrap",
                name="RedHat/Fedora Bootstrap Packages",
                category=ArkCategory.OS_PKG.value,
                priority=ArkPriority.HIGH.value,
                version="current",
                filename="os_pkg/redhat-bootstrap.tar.gz",
                size_bytes=200 * 1024**2,
                sha256=None,
                state=ArkItemState.MISSING.value,
                os_family="redhat", arch=None, min_ram_gb=0.0,
                description="git curl python3 python3-venv gcc",
                source_url=None,
                install_cmd="rpm -i {dir}/*.rpm",
                tags=["offline", "dnf", "bootstrap"],
            ),
            # ── KISWARM source snapshots ──────────────────────────────────────
            ArkItem(
                item_id="source:kiswarm:current",
                name="KISWARM Source Bundle (current)",
                category=ArkCategory.SOURCE.value,
                priority=ArkPriority.CRITICAL.value,
                version=ARK_VERSION,
                filename="source/kiswarm-current.bundle",
                size_bytes=20 * 1024**2,
                sha256=None,
                state=ArkItemState.MISSING.value,
                os_family=None, arch=None, min_ram_gb=0.0,
                description="Git bundle — cloneable without GitHub",
                source_url="https://github.com/Baronki2/KISWARM",
                install_cmd="git clone {file} ~/KISWARM",
                tags=["source", "offline", "git-bundle", "bootstrap"],
            ),
            ArkItem(
                item_id="source:kiswarm:previous",
                name="KISWARM Source Bundle (previous)",
                category=ArkCategory.SOURCE.value,
                priority=ArkPriority.HIGH.value,
                version="4.8",
                filename="source/kiswarm-previous.bundle",
                size_bytes=19 * 1024**2,
                sha256=None,
                state=ArkItemState.MISSING.value,
                os_family=None, arch=None, min_ram_gb=0.0,
                description="Fallback to previous version if current fails",
                source_url=None,
                install_cmd="git clone {file} ~/KISWARM",
                tags=["source", "fallback"],
            ),
            # ── Bootstrap script ──────────────────────────────────────────────
            ArkItem(
                item_id="script:bootstrap-offline",
                name="Offline Bootstrap Script",
                category=ArkCategory.SCRIPT.value,
                priority=ArkPriority.CRITICAL.value,
                version=ARK_VERSION,
                filename="script/bootstrap_offline.sh",
                size_bytes=50 * 1024,
                sha256=None,
                state=ArkItemState.MISSING.value,
                os_family=None, arch=None, min_ram_gb=0.0,
                description="Sets up KISWARM from local Ark without any internet",
                source_url=None,
                install_cmd="bash {file}",
                tags=["bootstrap", "offline", "critical"],
            ),
        ]

    # ── Item operations ───────────────────────────────────────────────────────

    def register_item(self, item: ArkItem) -> None:
        """Add or update an item in the inventory."""
        self._inventory[item.item_id] = item
        self._save_inventory()

    def get_item(self, item_id: str) -> Optional[ArkItem]:
        return self._inventory.get(item_id)

    def item_path(self, item: ArkItem) -> str:
        return os.path.join(self.ark_dir, item.rel_path)

    def item_exists(self, item: ArkItem) -> bool:
        return os.path.exists(self.item_path(item))

    # ── Integrity verification ────────────────────────────────────────────────

    def verify_item(self, item: ArkItem) -> ArkItemState:
        """Check if item is present and valid."""
        path = self.item_path(item)

        if not os.path.exists(path):
            item.state = ArkItemState.MISSING.value
            return ArkItemState.MISSING

        if item.sha256:
            actual = self._sha256(path)
            if actual != item.sha256:
                item.state = ArkItemState.CORRUPTED.value
                logger.warning(f"[Ark] Checksum mismatch: {item.item_id}")
                return ArkItemState.CORRUPTED

        item.state      = ArkItemState.PRESENT.value
        item.last_verified = time.time()
        return ArkItemState.PRESENT

    def integrity_check(self, quick: bool = False) -> Dict[str, ArkItemState]:
        """
        Verify all items in the inventory.
        quick=True: only check file existence, skip checksum
        """
        results: Dict[str, ArkItemState] = {}
        for item_id, item in self._inventory.items():
            if quick:
                state = (ArkItemState.PRESENT if self.item_exists(item)
                         else ArkItemState.MISSING)
                item.state = state.value
            else:
                state = self.verify_item(item)
            results[item_id] = state
        self._save_inventory()
        return results

    @staticmethod
    def _sha256(path: str, chunk: int = 65536) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            while True:
                data = f.read(chunk)
                if not data:
                    break
                h.update(data)
        return h.hexdigest()

    # ── Bootstrap capability assessment ──────────────────────────────────────

    def can_bootstrap(self) -> Tuple[bool, List[str]]:
        """
        Can this node bootstrap a new KISWARM installation
        from local ark alone, without any internet?

        Returns: (can_bootstrap, list_of_gaps)
        """
        gaps: List[str] = []
        self.integrity_check(quick=True)

        critical = [item for item in self._inventory.values()
                    if item.priority == ArkPriority.CRITICAL.value]

        for item in critical:
            if item.state != ArkItemState.PRESENT.value:
                # Check if this item is relevant for our OS
                if item.os_family and item.os_family != self._os_family:
                    continue  # Not needed for this OS
                gaps.append(f"{item.item_id} ({item.name})")

        return len(gaps) == 0, gaps

    def what_do_i_have(self) -> Dict[str, Any]:
        """
        Complete capability summary — what can this node do right now?
        Called by other modules to understand local capabilities.
        """
        self.integrity_check(quick=True)

        present  = [i for i in self._inventory.values()
                    if i.state == ArkItemState.PRESENT.value]
        models   = [i for i in present if i.category == ArkCategory.MODEL.value]
        can_boot, gaps = self.can_bootstrap()

        # Best model available for our RAM
        best_model = None
        for m in sorted(models, key=lambda x: x.size_bytes, reverse=True):
            if m.min_ram_gb <= self._ram_gb:
                best_model = m.name
                break

        return {
            "node": {
                "os_family": self._os_family,
                "arch":      self._arch,
                "ram_gb":    round(self._ram_gb, 1),
            },
            "ark": {
                "dir":           self.ark_dir,
                "total_items":   len(self._inventory),
                "present_items": len(present),
                "disk_used":     self._disk_used(),
            },
            "capabilities": {
                "can_bootstrap_offline":  can_boot,
                "bootstrap_gaps":         gaps,
                "models_available":       [m.name for m in models],
                "best_model_for_ram":     best_model,
                "python_wheels_present":  any(
                    i.state == ArkItemState.PRESENT.value
                    for i in self._inventory.values()
                    if i.category == ArkCategory.PYTHON_PKG.value
                ),
                "source_bundle_present":  any(
                    i.state == ArkItemState.PRESENT.value
                    for i in self._inventory.values()
                    if i.category == ArkCategory.SOURCE.value
                ),
            },
        }

    # ── Disk management ───────────────────────────────────────────────────────

    def _disk_used(self) -> int:
        """Total bytes used by ark directory."""
        total = 0
        for root, _, files in os.walk(self.ark_dir):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except Exception:
                    pass
        return total

    def disk_status(self) -> Dict[str, Any]:
        usage = shutil.disk_usage(self.ark_dir)
        used  = self._disk_used()
        return {
            "ark_used_bytes":    used,
            "ark_used_human":    self._human(used),
            "disk_free_bytes":   usage.free,
            "disk_free_human":   self._human(usage.free),
            "disk_total_bytes":  usage.total,
            "target_ark_bytes":  TARGET_ARK_SIZE,
            "ark_fill_percent":  round(used / TARGET_ARK_SIZE * 100, 1),
        }

    @staticmethod
    def _human(b: int) -> str:
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if b < 1024:
                return f"{b:.1f}{unit}"
            b //= 1024
        return f"{b:.1f}PB"

    # ── Priority-ordered missing items ────────────────────────────────────────

    def missing_by_priority(self) -> List[ArkItem]:
        """
        What should we download next?
        Returns missing items ordered by priority,
        filtered to what is compatible with this node.
        """
        self.integrity_check(quick=True)
        priority_order = {
            ArkPriority.CRITICAL.value: 0,
            ArkPriority.HIGH.value:     1,
            ArkPriority.NORMAL.value:   2,
            ArkPriority.LOW.value:      3,
        }
        missing = []
        for item in self._inventory.values():
            if item.state != ArkItemState.PRESENT.value:
                # Skip OS-specific items for wrong OS
                if item.os_family and item.os_family != self._os_family:
                    continue
                # Skip models requiring more RAM than available
                if item.category == ArkCategory.MODEL.value:
                    if item.min_ram_gb > self._ram_gb:
                        continue
                missing.append(item)

        return sorted(missing, key=lambda x: (
            priority_order.get(x.priority, 9),
            x.size_bytes  # Smaller first within same priority
        ))

    # ── Status ────────────────────────────────────────────────────────────────

    def status(self) -> ArkStatus:
        self.integrity_check(quick=True)
        can_boot, gaps = self.can_bootstrap()
        disk = shutil.disk_usage(self.ark_dir)

        items     = list(self._inventory.values())
        present   = [i for i in items if i.state == ArkItemState.PRESENT.value]
        missing   = [i for i in items if i.state == ArkItemState.MISSING.value]
        corrupted = [i for i in items if i.state == ArkItemState.CORRUPTED.value]
        critical  = [i for i in items if i.priority == ArkPriority.CRITICAL.value]
        crit_pres = [i for i in critical if i.state == ArkItemState.PRESENT.value]

        return ArkStatus(
            ark_dir=self.ark_dir,
            total_items=len(items),
            present_items=len(present),
            missing_items=len(missing),
            corrupted_items=len(corrupted),
            critical_present=len(crit_pres),
            critical_total=len(critical),
            disk_used_bytes=self._disk_used(),
            disk_free_bytes=disk.free,
            can_bootstrap=can_boot,
            bootstrap_gaps=gaps,
            last_integrity_check=time.time(),
            os_family=self._os_family,
            arch=self._arch,
            ram_gb=round(self._ram_gb, 1),
        )

    # ── Copy a file INTO the ark ──────────────────────────────────────────────

    def store_file(
        self,
        item_id: str,
        source_path: str,
        compute_checksum: bool = True,
    ) -> Optional[ArkItem]:
        """
        Store an existing file into the ark.
        Used by ArkManager after a successful download.
        """
        item = self._inventory.get(item_id)
        if not item:
            logger.error(f"[Ark] Unknown item_id: {item_id}")
            return None

        dest_dir  = os.path.join(self.ark_dir, item.category)
        dest_path = os.path.join(self.ark_dir, item.rel_path)
        os.makedirs(dest_dir, exist_ok=True)

        shutil.copy2(source_path, dest_path)

        item.size_bytes = os.path.getsize(dest_path)
        if compute_checksum:
            item.sha256 = self._sha256(dest_path)
        item.state      = ArkItemState.PRESENT.value
        item.last_verified = time.time()

        self._save_inventory()
        logger.info(f"[Ark] Stored: {item_id} ({item.size_human()})")
        return item
