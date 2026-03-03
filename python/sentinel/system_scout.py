"""
KISWARM v4.6 — Module 34: System Scout
=======================================
Autonomous system scanner — the "eyes" of the Installer Agent.

Before any installation decision is made, the Scout gathers:
  • Hardware profile   (CPU cores/speed, RAM total/free, disk)
  • OS fingerprint     (distro, version, kernel, arch)
  • Port landscape     (which ports are free/occupied)
  • Dependency matrix  (git, python3, pip, ollama, docker, systemd, curl, npm)
  • Network reachability (GitHub, Ollama registry, PyPI)
  • Running services   (ollama, qdrant, docker, nginx, any KISWARM processes)
  • Python ecosystem   (venv support, installed packages)
  • Security posture   (sudo access, firewall status)

All results are returned as a structured ScoutReport that the
Installer Agent uses to make tailored installation decisions.

Zero side-effects: Scout only reads, never writes or executes installs.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

KISWARM_PORTS = {
    11434: "ollama",
    11435: "kiswarm-tool-proxy",
    11436: "kiswarm-sentinel-api",
    11437: "kiswarm-dev",
    6333:  "qdrant-http",
    6334:  "qdrant-grpc",
}

REQUIRED_COMMANDS = ["git", "python3", "pip3", "curl", "tar", "unzip"]
OPTIONAL_COMMANDS = ["docker", "npm", "node", "systemctl", "ollama", "jq"]
NETWORK_TARGETS  = [
    ("github.com",            443, "GitHub"),
    ("registry.ollama.ai",    443, "Ollama Registry"),
    ("pypi.org",              443, "PyPI"),
    ("files.pythonhosted.org", 443, "PyPI Files"),
]

REQUIRED_PYTHON_PACKAGES = [
    "pip", "setuptools", "venv",
]

MIN_RAM_GB   = 4
RECOMMEND_RAM_GB = 16
MIN_DISK_GB  = 10
RECOMMEND_DISK_GB = 50


# ─────────────────────────────────────────────────────────────────────────────
# RESULT TYPES
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class HardwareProfile:
    cpu_cores:        int
    cpu_model:        str
    cpu_freq_mhz:     float
    ram_total_gb:     float
    ram_free_gb:      float
    ram_percent_used: float
    disk_total_gb:    float
    disk_free_gb:     float
    disk_percent_used: float
    swap_total_gb:    float
    swap_free_gb:     float
    gpu_info:         List[str] = field(default_factory=list)

    def sufficient_for_kiswarm(self) -> Tuple[bool, List[str]]:
        issues = []
        if self.ram_total_gb < MIN_RAM_GB:
            issues.append(f"RAM {self.ram_total_gb:.1f}GB < minimum {MIN_RAM_GB}GB")
        if self.disk_free_gb < MIN_DISK_GB:
            issues.append(f"Free disk {self.disk_free_gb:.1f}GB < minimum {MIN_DISK_GB}GB")
        return len(issues) == 0, issues

    def model_recommendation(self) -> str:
        """Recommend best Ollama model given available RAM."""
        if self.ram_total_gb >= 32:
            return "qwen2.5:14b"
        elif self.ram_total_gb >= 16:
            return "qwen2.5:7b"
        elif self.ram_total_gb >= 8:
            return "qwen2.5:3b"
        else:
            return "qwen2.5:0.5b"


@dataclass
class OSFingerprint:
    system:       str    # Linux / Darwin / Windows
    distro:       str    # ubuntu / debian / fedora / unknown
    distro_version: str
    kernel:       str
    arch:         str    # x86_64 / arm64 / ...
    hostname:     str
    is_container: bool   # running inside Docker/LXC?
    init_system:  str    # systemd / openrc / unknown
    pkg_manager:  str    # apt / dnf / pacman / brew / unknown

    def is_supported(self) -> bool:
        return self.system == "Linux" and self.distro in (
            "ubuntu", "debian", "linuxmint", "pop", "elementary",
            "kali", "fedora", "centos", "rhel", "alma", "rocky",
            "arch", "manjaro", "opensuse",
        )


@dataclass
class PortStatus:
    port:        int
    name:        str
    free:        bool
    pid:         Optional[int]
    process_name: Optional[str]


@dataclass
class DependencyCheck:
    name:     str
    required: bool
    present:  bool
    version:  Optional[str]
    path:     Optional[str]
    note:     str = ""


@dataclass
class NetworkReachability:
    host:       str
    port:       int
    label:      str
    reachable:  bool
    latency_ms: Optional[float]


@dataclass
class ScoutReport:
    """Complete system intelligence report."""
    scanned_at:      float
    hardware:        HardwareProfile
    os:              OSFingerprint
    ports:           List[PortStatus]
    dependencies:    List[DependencyCheck]
    network:         List[NetworkReachability]
    running_services: List[str]
    sudo_available:  bool
    python_version:  str
    python_has_venv: bool
    install_readiness: str    # "ready" | "warnings" | "blocked"
    readiness_issues: List[str]
    readiness_warnings: List[str]
    recommendations: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scanned_at":       self.scanned_at,
            "hardware": {
                "cpu_cores":         self.hardware.cpu_cores,
                "cpu_model":         self.hardware.cpu_model,
                "ram_total_gb":      round(self.hardware.ram_total_gb, 2),
                "ram_free_gb":       round(self.hardware.ram_free_gb, 2),
                "disk_free_gb":      round(self.hardware.disk_free_gb, 2),
                "disk_total_gb":     round(self.hardware.disk_total_gb, 2),
                "model_recommendation": self.hardware.model_recommendation(),
            },
            "os": {
                "system":        self.os.system,
                "distro":        self.os.distro,
                "version":       self.os.distro_version,
                "arch":          self.os.arch,
                "init_system":   self.os.init_system,
                "pkg_manager":   self.os.pkg_manager,
                "is_container":  self.os.is_container,
                "is_supported":  self.os.is_supported(),
            },
            "ports": [
                {
                    "port": p.port, "name": p.name,
                    "free": p.free, "pid": p.pid,
                    "process": p.process_name,
                }
                for p in self.ports
            ],
            "dependencies": [
                {
                    "name": d.name, "required": d.required,
                    "present": d.present, "version": d.version,
                    "note": d.note,
                }
                for d in self.dependencies
            ],
            "network": [
                {
                    "label": n.label, "reachable": n.reachable,
                    "latency_ms": n.latency_ms,
                }
                for n in self.network
            ],
            "running_services":    self.running_services,
            "sudo_available":      self.sudo_available,
            "python_version":      self.python_version,
            "python_has_venv":     self.python_has_venv,
            "install_readiness":   self.install_readiness,
            "readiness_issues":    self.readiness_issues,
            "readiness_warnings":  self.readiness_warnings,
            "recommendations":     self.recommendations,
        }

    def summary_text(self) -> str:
        hw = self.hardware
        os_ = self.os
        blocked = [d for d in self.dependencies if d.required and not d.present]
        free_ports = [p for p in self.ports if p.free]
        lines = [
            f"=== KISWARM System Scout Report ===",
            f"Host:       {self.os.hostname}",
            f"OS:         {os_.distro} {os_.distro_version} ({os_.arch})",
            f"Kernel:     {os_.kernel}",
            f"CPU:        {hw.cpu_cores}x cores | {hw.cpu_model}",
            f"RAM:        {hw.ram_total_gb:.1f}GB total, {hw.ram_free_gb:.1f}GB free",
            f"Disk:       {hw.disk_free_gb:.1f}GB free / {hw.disk_total_gb:.1f}GB total",
            f"Container:  {'Yes' if os_.is_container else 'No'}",
            f"Init:       {os_.init_system}",
            f"Pkg Mgr:    {os_.pkg_manager}",
            f"Python:     {self.python_version} (venv: {'✓' if self.python_has_venv else '✗'})",
            f"Sudo:       {'✓' if self.sudo_available else '✗'}",
            f"Readiness:  {self.install_readiness.upper()}",
            f"",
            f"Ports (free): {', '.join(str(p.port) for p in free_ports)}",
        ]
        if blocked:
            lines.append(f"MISSING required deps: {', '.join(d.name for d in blocked)}")
        if self.readiness_issues:
            lines.append(f"BLOCKING issues: {'; '.join(self.readiness_issues)}")
        if self.readiness_warnings:
            lines.append(f"Warnings: {'; '.join(self.readiness_warnings)}")
        lines.append(f"Recommended model: {hw.model_recommendation()}")
        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# SYSTEM SCOUT
# ─────────────────────────────────────────────────────────────────────────────

class SystemScout:
    """
    Zero-side-effect system scanner.
    All methods are read-only — nothing is installed or modified.
    """

    def __init__(self, timeout_s: float = 3.0):
        self.timeout = timeout_s

    # ── Hardware ──────────────────────────────────────────────────────────────

    def scan_hardware(self) -> HardwareProfile:
        try:
            import psutil
            cpu_freq = psutil.cpu_freq()
            mem      = psutil.virtual_memory()
            disk     = psutil.disk_usage(os.path.expanduser("~"))
            swap     = psutil.swap_memory()
            freq_mhz = cpu_freq.current if cpu_freq else 0.0
        except ImportError:
            # Fallback without psutil
            freq_mhz = 0.0
            mem      = type("m", (), {"total": 0, "available": 0, "percent": 0})()
            disk     = type("d", (), {"total": 0, "free": 0, "percent": 0})()
            swap     = type("s", (), {"total": 0, "free": 0})()

        # CPU model
        cpu_model = platform.processor() or "unknown"
        if platform.system() == "Linux":
            try:
                with open("/proc/cpuinfo") as f:
                    for line in f:
                        if "model name" in line:
                            cpu_model = line.split(":")[1].strip()
                            break
            except Exception:
                pass

        # GPU detection (optional)
        gpus: List[str] = []
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                capture_output=True, text=True, timeout=2
            )
            if result.returncode == 0:
                gpus = [g.strip() for g in result.stdout.strip().split("\n") if g.strip()]
        except Exception:
            pass

        try:
            import psutil
            mem  = psutil.virtual_memory()
            disk = psutil.disk_usage(os.path.expanduser("~"))
            swap = psutil.swap_memory()
            return HardwareProfile(
                cpu_cores=psutil.cpu_count(logical=True) or 1,
                cpu_model=cpu_model,
                cpu_freq_mhz=freq_mhz,
                ram_total_gb=mem.total / 1e9,
                ram_free_gb=mem.available / 1e9,
                ram_percent_used=mem.percent,
                disk_total_gb=disk.total / 1e9,
                disk_free_gb=disk.free / 1e9,
                disk_percent_used=disk.percent,
                swap_total_gb=swap.total / 1e9,
                swap_free_gb=swap.free / 1e9,
                gpu_info=gpus,
            )
        except Exception:
            return HardwareProfile(
                cpu_cores=os.cpu_count() or 1,
                cpu_model=cpu_model,
                cpu_freq_mhz=0.0,
                ram_total_gb=0.0, ram_free_gb=0.0, ram_percent_used=0.0,
                disk_total_gb=0.0, disk_free_gb=0.0, disk_percent_used=0.0,
                swap_total_gb=0.0, swap_free_gb=0.0, gpu_info=gpus,
            )

    # ── OS ────────────────────────────────────────────────────────────────────

    def scan_os(self) -> OSFingerprint:
        system  = platform.system()
        arch    = platform.machine()
        kernel  = platform.release()
        hostname = socket.gethostname()

        distro, distro_version = "unknown", "unknown"
        if system == "Linux":
            try:
                import distro as _distro
                distro = _distro.id().lower()
                distro_version = _distro.version()
            except ImportError:
                # Fallback: read /etc/os-release
                try:
                    with open("/etc/os-release") as f:
                        info = {}
                        for line in f:
                            k, _, v = line.partition("=")
                            info[k.strip()] = v.strip().strip('"')
                    distro = info.get("ID", "unknown").lower()
                    distro_version = info.get("VERSION_ID", "unknown")
                except Exception:
                    pass
        elif system == "Darwin":
            distro = "macos"
            distro_version = platform.mac_ver()[0]

        # Init system
        init_system = "unknown"
        if shutil.which("systemctl"):
            init_system = "systemd"
        elif shutil.which("rc-status"):
            init_system = "openrc"
        elif shutil.which("launchctl"):
            init_system = "launchd"

        # Package manager
        pkg_manager = "unknown"
        for pm, cmd in [("apt", "apt"), ("dnf", "dnf"), ("yum", "yum"),
                        ("pacman", "pacman"), ("zypper", "zypper"), ("brew", "brew")]:
            if shutil.which(cmd):
                pkg_manager = pm
                break

        # Container detection
        is_container = False
        if os.path.exists("/.dockerenv"):
            is_container = True
        elif os.path.exists("/proc/1/cgroup"):
            try:
                with open("/proc/1/cgroup") as f:
                    content = f.read()
                    if "docker" in content or "lxc" in content or "kubepods" in content:
                        is_container = True
            except Exception:
                pass

        return OSFingerprint(
            system=system, distro=distro, distro_version=distro_version,
            kernel=kernel, arch=arch, hostname=hostname,
            is_container=is_container, init_system=init_system,
            pkg_manager=pkg_manager,
        )

    # ── Ports ─────────────────────────────────────────────────────────────────

    def scan_ports(self) -> List[PortStatus]:
        results: List[PortStatus] = []
        for port, name in KISWARM_PORTS.items():
            free = True
            pid, proc_name = None, None
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.settimeout(0.5)
                    r = s.connect_ex(("127.0.0.1", port))
                    if r == 0:
                        free = False
                        # Try to identify process
                        try:
                            import psutil
                            for conn in psutil.net_connections(kind="inet"):
                                if conn.laddr.port == port and conn.status == "LISTEN":
                                    pid = conn.pid
                                    if pid:
                                        try:
                                            proc_name = psutil.Process(pid).name()
                                        except Exception:
                                            pass
                        except Exception:
                            pass
            except Exception:
                pass
            results.append(PortStatus(port=port, name=name, free=free, pid=pid, process_name=proc_name))
        return results

    # ── Dependencies ──────────────────────────────────────────────────────────

    def scan_dependencies(self) -> List[DependencyCheck]:
        results: List[DependencyCheck] = []

        def check_cmd(name: str, required: bool, version_flag: str = "--version") -> DependencyCheck:
            path = shutil.which(name)
            if path is None:
                return DependencyCheck(name=name, required=required, present=False, version=None, path=None)
            try:
                out = subprocess.run(
                    [name, version_flag], capture_output=True, text=True, timeout=3
                )
                version = (out.stdout or out.stderr).strip().split("\n")[0][:80]
            except Exception:
                version = "unknown"
            return DependencyCheck(name=name, required=required, present=True, version=version, path=path)

        for cmd in REQUIRED_COMMANDS:
            results.append(check_cmd(cmd, required=True))
        for cmd in OPTIONAL_COMMANDS:
            results.append(check_cmd(cmd, required=False))

        # Python venv specifically
        try:
            r = subprocess.run(
                [sys.executable, "-m", "venv", "--help"],
                capture_output=True, timeout=3
            )
            has_venv = r.returncode == 0
        except Exception:
            has_venv = False

        results.append(DependencyCheck(
            name="python3-venv", required=True, present=has_venv,
            version=None, path=None,
            note="Required for Python virtual environment",
        ))

        # pip availability
        try:
            r = subprocess.run([sys.executable, "-m", "pip", "--version"],
                               capture_output=True, text=True, timeout=3)
            pip_ok = r.returncode == 0
            pip_ver = r.stdout.strip()[:60] if pip_ok else None
        except Exception:
            pip_ok, pip_ver = False, None
        results.append(DependencyCheck(
            name="pip", required=True, present=pip_ok,
            version=pip_ver, path=None,
        ))

        return results

    # ── Network ───────────────────────────────────────────────────────────────

    def scan_network(self) -> List[NetworkReachability]:
        results: List[NetworkReachability] = []
        for host, port, label in NETWORK_TARGETS:
            t0 = time.time()
            try:
                with socket.create_connection((host, port), timeout=self.timeout):
                    latency = round((time.time() - t0) * 1000, 1)
                    results.append(NetworkReachability(
                        host=host, port=port, label=label,
                        reachable=True, latency_ms=latency
                    ))
            except Exception:
                results.append(NetworkReachability(
                    host=host, port=port, label=label,
                    reachable=False, latency_ms=None
                ))
        return results

    # ── Running services ──────────────────────────────────────────────────────

    def scan_running_services(self) -> List[str]:
        running: List[str] = []
        patterns = {
            "ollama":          "ollama",
            "qdrant":          "qdrant",
            "kiswarm-sentinel": "sentinel_api",
            "docker":          "dockerd",
            "nginx":           "nginx",
            "postgresql":      "postgres",
        }
        try:
            import psutil
            procs = [p.name() for p in psutil.process_iter(["name"]) if p.info["name"]]
            proc_str = " ".join(procs).lower()
            for label, pattern in patterns.items():
                if pattern.lower() in proc_str:
                    running.append(label)
        except ImportError:
            # Fallback: pgrep
            for label, pattern in patterns.items():
                try:
                    r = subprocess.run(["pgrep", "-f", pattern],
                                       capture_output=True, timeout=2)
                    if r.returncode == 0:
                        running.append(label)
                except Exception:
                    pass
        return running

    # ── Sudo ─────────────────────────────────────────────────────────────────

    def check_sudo(self) -> bool:
        try:
            r = subprocess.run(
                ["sudo", "-n", "true"],
                capture_output=True, timeout=3
            )
            return r.returncode == 0
        except Exception:
            return False

    # ── Master scan ───────────────────────────────────────────────────────────

    def full_scan(self) -> ScoutReport:
        """Run all scans and compile a complete ScoutReport."""
        logger.info("[SystemScout] Starting full system scan...")
        t0 = time.time()

        hardware     = self.scan_hardware()
        os_info      = self.scan_os()
        ports        = self.scan_ports()
        dependencies = self.scan_dependencies()
        network      = self.scan_network()
        services     = self.scan_running_services()
        sudo_ok      = self.check_sudo()

        python_version  = platform.python_version()
        python_has_venv = any(d.name == "python3-venv" and d.present for d in dependencies)

        # Readiness assessment
        issues:   List[str] = []
        warnings: List[str] = []
        recs:     List[str] = []

        hw_ok, hw_issues = hardware.sufficient_for_kiswarm()
        if not hw_ok:
            issues.extend(hw_issues)

        if not os_info.is_supported():
            warnings.append(f"OS '{os_info.distro}' not officially tested with KISWARM")

        missing_required = [d for d in dependencies if d.required and not d.present]
        if missing_required:
            issues.append(f"Missing required dependencies: {', '.join(d.name for d in missing_required)}")

        github_ok = any(n.label == "GitHub" and n.reachable for n in network)
        if not github_ok:
            issues.append("Cannot reach github.com — repository download will fail")

        ollama_reachable = any(n.label == "Ollama Registry" and n.reachable for n in network)
        if not ollama_reachable:
            warnings.append("Cannot reach Ollama registry — model download may fail (offline mode)")

        if hardware.ram_total_gb < RECOMMEND_RAM_GB:
            warnings.append(f"RAM {hardware.ram_total_gb:.1f}GB < recommended {RECOMMEND_RAM_GB}GB — use smaller models")
            recs.append(f"Use model: {hardware.model_recommendation()}")

        if hardware.disk_free_gb < RECOMMEND_DISK_GB:
            warnings.append(f"Only {hardware.disk_free_gb:.1f}GB free disk — limit model downloads")

        if not sudo_ok:
            warnings.append("No passwordless sudo — systemd service installation will require manual step")

        occupied_kiswarm_ports = [p for p in ports if not p.free and p.name.startswith("kiswarm")]
        if occupied_kiswarm_ports:
            warnings.append(f"KISWARM ports occupied: {[p.port for p in occupied_kiswarm_ports]}")

        # Specific recommendations
        if "ollama" not in services:
            recs.append("Ollama not running — will be started by installer")
        if os_info.is_container:
            recs.append("Container detected — systemd service will be skipped, use process manager")
        if not python_has_venv:
            recs.append(f"Install python3-venv: {os_info.pkg_manager} install python3-venv")

        readiness = "ready"
        if issues:
            readiness = "blocked"
        elif warnings:
            readiness = "warnings"

        elapsed = round(time.time() - t0, 2)
        logger.info(f"[SystemScout] Scan complete in {elapsed}s — status: {readiness}")

        return ScoutReport(
            scanned_at=time.time(),
            hardware=hardware,
            os=os_info,
            ports=ports,
            dependencies=dependencies,
            network=network,
            running_services=services,
            sudo_available=sudo_ok,
            python_version=python_version,
            python_has_venv=python_has_venv,
            install_readiness=readiness,
            readiness_issues=issues,
            readiness_warnings=warnings,
            recommendations=recs,
        )
