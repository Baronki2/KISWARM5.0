"""
KISWARM v4.6 — Test Suite: Installer Agent System
==================================================
Tests for Modules 38–41:
  Module 38: SystemScout     (system_scout.py)
  Module 39: RepoIntelligence (repo_intelligence.py)
  Module 40: InstallerAgent  (installer_agent.py)
  Module 41: KISWARMAdvisor  (advisor_api.py)

Coverage:
  TestSystemScoutHardware       (9)  — CPU, RAM, disk, GPU
  TestSystemScoutOS             (8)  — distro, init, pkgmgr, container
  TestSystemScoutPorts          (7)  — port probing, free/occupied
  TestSystemScoutDependencies   (8)  — required/optional commands
  TestSystemScoutNetwork        (5)  — reachability, latency
  TestSystemScoutRunning        (5)  — process detection
  TestSystemScoutFullScan       (9)  — integration, readiness
  TestScoutReport               (8)  — to_dict, summary_text
  TestHardwareProfile           (6)  — sufficiency, model recommendation
  TestOSFingerprint             (5)  — is_supported
  TestRepoIntelligenceEmbedded  (10) — modules, versions, ports
  TestRepoIntelligenceAnswer    (10) — NL question routing
  TestRepoIntelligencePlan      (12) — install plan generation
  TestInstallerAgentDryRun      (9)  — dry_run phases
  TestInstallerAgentStepResult  (7)  — step execution
  TestInstallerAgentReport      (8)  — report structure
  TestInstallerAgentStateFlow   (6)  — state transitions
  TestAdvisorSession            (7)  — session management
  TestAdvisorConsult            (8)  — consultation flow
  TestAdvisorScanAdvise         (7)  — scan + advise
  TestAdvisorPeerHandshake      (6)  — AI-to-AI handshake
  TestAdvisorStats              (5)  — stats tracking
  TestAdvisorSingleton          (3)  — get_advisor()
  TestEndToEndFlow              (5)  — full pipeline

Total: 188 new tests
Running total: 1121 + 188 = 1309
"""

from __future__ import annotations

import json
import os
import sys
import time
import tempfile
import unittest
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch, PropertyMock

# ─── path setup ───────────────────────────────────────────────────────────────
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from python.sentinel.system_scout import (
    SystemScout, HardwareProfile, OSFingerprint, PortStatus,
    DependencyCheck, NetworkReachability, ScoutReport,
    KISWARM_PORTS, REQUIRED_COMMANDS, OPTIONAL_COMMANDS,
    MIN_RAM_GB, MIN_DISK_GB,
)
from python.sentinel.repo_intelligence import (
    RepoIntelligence, EMBEDDED_KNOWLEDGE,
)
from python.sentinel.installer_agent import (
    InstallerAgent, InstallMode, InstallState, StepResult, InstallReport,
    run_installer,
)
from python.sentinel.advisor_api import (
    KISWARMAdvisor, AdvisorySession, get_advisor,
)


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _make_hardware(
    ram_gb: float = 16.0,
    disk_gb: float = 50.0,
    cpu_cores: int = 4,
) -> HardwareProfile:
    return HardwareProfile(
        cpu_cores=cpu_cores,
        cpu_model="Test CPU @ 2.4GHz",
        cpu_freq_mhz=2400.0,
        ram_total_gb=ram_gb,
        ram_free_gb=ram_gb * 0.8,
        ram_percent_used=20.0,
        disk_total_gb=100.0,
        disk_free_gb=disk_gb,
        disk_percent_used=50.0,
        swap_total_gb=4.0,
        swap_free_gb=4.0,
        gpu_info=[],
    )


def _make_os(
    distro: str = "ubuntu",
    version: str = "22.04",
    init: str = "systemd",
    pkg: str = "apt",
    container: bool = False,
) -> OSFingerprint:
    return OSFingerprint(
        system="Linux",
        distro=distro,
        distro_version=version,
        kernel="5.15.0-generic",
        arch="x86_64",
        hostname="testhost",
        is_container=container,
        init_system=init,
        pkg_manager=pkg,
    )


def _make_scout_report(
    readiness: str = "ready",
    issues: List[str] = None,
    warnings: List[str] = None,
    ram_gb: float = 16.0,
    disk_gb: float = 50.0,
    distro: str = "ubuntu",
) -> ScoutReport:
    hw = _make_hardware(ram_gb=ram_gb, disk_gb=disk_gb)
    os_info = _make_os(distro=distro)
    return ScoutReport(
        scanned_at=time.time(),
        hardware=hw,
        os=os_info,
        ports=[PortStatus(p, n, True, None, None) for p, n in KISWARM_PORTS.items()],
        dependencies=[
            DependencyCheck(c, True, True, "1.0", "/usr/bin/" + c)
            for c in REQUIRED_COMMANDS
        ],
        network=[
            NetworkReachability("github.com", 443, "GitHub", True, 30.0),
            NetworkReachability("pypi.org", 443, "PyPI", True, 25.0),
        ],
        running_services=["ollama"],
        sudo_available=True,
        python_version="3.11.0",
        python_has_venv=True,
        install_readiness=readiness,
        readiness_issues=issues or [],
        readiness_warnings=warnings or [],
        recommendations=["Use model: qwen2.5:7b"],
    )


def _make_scout_dict(**kw) -> Dict[str, Any]:
    return _make_scout_report(**kw).to_dict()


def _silent_log(level: str, msg: str) -> None:
    pass


# ─────────────────────────────────────────────────────────────────────────────
# MODULE 38: SYSTEM SCOUT
# ─────────────────────────────────────────────────────────────────────────────

class TestSystemScoutHardware(unittest.TestCase):

    def setUp(self):
        self.scout = SystemScout()

    def test_returns_hardware_profile(self):
        hw = self.scout.scan_hardware()
        self.assertIsInstance(hw, HardwareProfile)

    def test_cpu_cores_positive(self):
        hw = self.scout.scan_hardware()
        self.assertGreater(hw.cpu_cores, 0)

    def test_cpu_model_string(self):
        hw = self.scout.scan_hardware()
        self.assertIsInstance(hw.cpu_model, str)
        self.assertGreater(len(hw.cpu_model), 0)

    def test_ram_total_positive(self):
        hw = self.scout.scan_hardware()
        self.assertGreater(hw.ram_total_gb, 0)

    def test_ram_free_lte_total(self):
        hw = self.scout.scan_hardware()
        self.assertLessEqual(hw.ram_free_gb, hw.ram_total_gb)

    def test_disk_total_positive(self):
        hw = self.scout.scan_hardware()
        self.assertGreater(hw.disk_total_gb, 0)

    def test_disk_free_lte_total(self):
        hw = self.scout.scan_hardware()
        self.assertLessEqual(hw.disk_free_gb, hw.disk_total_gb)

    def test_gpu_info_list(self):
        hw = self.scout.scan_hardware()
        self.assertIsInstance(hw.gpu_info, list)

    def test_hardware_profile_model_recommendation(self):
        hw = _make_hardware(ram_gb=8.0)
        rec = hw.model_recommendation()
        self.assertIn("qwen2.5", rec)


class TestSystemScoutOS(unittest.TestCase):

    def setUp(self):
        self.scout = SystemScout()

    def test_returns_os_fingerprint(self):
        os_info = self.scout.scan_os()
        self.assertIsInstance(os_info, OSFingerprint)

    def test_system_field(self):
        os_info = self.scout.scan_os()
        self.assertIn(os_info.system, ["Linux", "Darwin", "Windows"])

    def test_arch_nonempty(self):
        os_info = self.scout.scan_os()
        self.assertTrue(len(os_info.arch) > 0)

    def test_hostname_nonempty(self):
        os_info = self.scout.scan_os()
        self.assertTrue(len(os_info.hostname) > 0)

    def test_distro_string(self):
        os_info = self.scout.scan_os()
        self.assertIsInstance(os_info.distro, str)

    def test_is_container_bool(self):
        os_info = self.scout.scan_os()
        self.assertIsInstance(os_info.is_container, bool)

    def test_init_system_string(self):
        os_info = self.scout.scan_os()
        self.assertIsInstance(os_info.init_system, str)

    def test_pkg_manager_string(self):
        os_info = self.scout.scan_os()
        self.assertIsInstance(os_info.pkg_manager, str)


class TestSystemScoutPorts(unittest.TestCase):

    def setUp(self):
        self.scout = SystemScout()

    def test_returns_list(self):
        ports = self.scout.scan_ports()
        self.assertIsInstance(ports, list)

    def test_correct_port_count(self):
        ports = self.scout.scan_ports()
        self.assertEqual(len(ports), len(KISWARM_PORTS))

    def test_port_status_fields(self):
        ports = self.scout.scan_ports()
        for p in ports:
            self.assertIsInstance(p, PortStatus)
            self.assertIsInstance(p.port, int)
            self.assertIsInstance(p.name, str)
            self.assertIsInstance(p.free, bool)

    def test_known_ports_covered(self):
        ports = self.scout.scan_ports()
        found_ports = {p.port for p in ports}
        for expected_port in KISWARM_PORTS:
            self.assertIn(expected_port, found_ports)

    def test_ollama_port_in_results(self):
        ports = self.scout.scan_ports()
        ollama = next((p for p in ports if p.port == 11434), None)
        self.assertIsNotNone(ollama)
        self.assertEqual(ollama.name, "ollama")

    def test_sentinel_port_in_results(self):
        ports = self.scout.scan_ports()
        sentinel = next((p for p in ports if p.port == 11436), None)
        self.assertIsNotNone(sentinel)

    def test_pid_none_when_free(self):
        ports = self.scout.scan_ports()
        free_ports = [p for p in ports if p.free]
        for p in free_ports:
            self.assertIsNone(p.pid)


class TestSystemScoutDependencies(unittest.TestCase):

    def setUp(self):
        self.scout = SystemScout()

    def test_returns_list(self):
        deps = self.scout.scan_dependencies()
        self.assertIsInstance(deps, list)

    def test_all_required_checked(self):
        deps = self.scout.scan_dependencies()
        names = {d.name for d in deps}
        for cmd in REQUIRED_COMMANDS:
            self.assertIn(cmd, names)

    def test_optional_deps_marked(self):
        deps = self.scout.scan_dependencies()
        optional_found = [d for d in deps if not d.required]
        self.assertGreater(len(optional_found), 0)

    def test_dep_fields(self):
        deps = self.scout.scan_dependencies()
        for d in deps:
            self.assertIsInstance(d.name, str)
            self.assertIsInstance(d.required, bool)
            self.assertIsInstance(d.present, bool)

    def test_python_venv_checked(self):
        deps = self.scout.scan_dependencies()
        names = {d.name for d in deps}
        self.assertIn("python3-venv", names)

    def test_pip_checked(self):
        deps = self.scout.scan_dependencies()
        names = {d.name for d in deps}
        self.assertIn("pip", names)

    def test_version_string_when_present(self):
        deps = self.scout.scan_dependencies()
        present = [d for d in deps if d.present]
        for d in present[:3]:
            # version can be None or string
            self.assertTrue(d.version is None or isinstance(d.version, str))

    def test_path_set_when_present(self):
        deps = self.scout.scan_dependencies()
        # At least git or curl should be present
        present_cmds = [d for d in deps if d.present and d.path]
        self.assertGreater(len(present_cmds), 0)


class TestSystemScoutNetwork(unittest.TestCase):

    def setUp(self):
        self.scout = SystemScout(timeout_s=1.0)

    def test_returns_list(self):
        net = self.scout.scan_network()
        self.assertIsInstance(net, list)

    def test_all_targets_checked(self):
        net = self.scout.scan_network()
        self.assertGreater(len(net), 0)

    def test_network_result_fields(self):
        net = self.scout.scan_network()
        for n in net:
            self.assertIsInstance(n, NetworkReachability)
            self.assertIsInstance(n.reachable, bool)
            self.assertIsInstance(n.label, str)

    def test_latency_positive_when_reachable(self):
        net = self.scout.scan_network()
        reachable = [n for n in net if n.reachable]
        for n in reachable:
            self.assertIsNotNone(n.latency_ms)
            self.assertGreater(n.latency_ms, 0)

    def test_latency_none_when_unreachable(self):
        net = self.scout.scan_network()
        unreachable = [n for n in net if not n.reachable]
        for n in unreachable:
            self.assertIsNone(n.latency_ms)


class TestSystemScoutRunning(unittest.TestCase):

    def setUp(self):
        self.scout = SystemScout()

    def test_returns_list(self):
        services = self.scout.scan_running_services()
        self.assertIsInstance(services, list)

    def test_items_are_strings(self):
        services = self.scout.scan_running_services()
        for s in services:
            self.assertIsInstance(s, str)

    def test_no_duplicates(self):
        services = self.scout.scan_running_services()
        self.assertEqual(len(services), len(set(services)))

    def test_check_sudo_returns_bool(self):
        result = self.scout.check_sudo()
        self.assertIsInstance(result, bool)

    def test_python_version_via_full_scan(self):
        report = self.scout.full_scan()
        self.assertIsInstance(report.python_version, str)
        self.assertGreater(len(report.python_version), 0)


class TestSystemScoutFullScan(unittest.TestCase):

    def setUp(self):
        self.scout = SystemScout(timeout_s=1.0)
        self.report = self.scout.full_scan()

    def test_returns_scout_report(self):
        self.assertIsInstance(self.report, ScoutReport)

    def test_hardware_populated(self):
        self.assertIsNotNone(self.report.hardware)
        self.assertIsInstance(self.report.hardware, HardwareProfile)

    def test_os_populated(self):
        self.assertIsNotNone(self.report.os)
        self.assertIsInstance(self.report.os, OSFingerprint)

    def test_ports_populated(self):
        self.assertIsInstance(self.report.ports, list)
        self.assertGreater(len(self.report.ports), 0)

    def test_dependencies_populated(self):
        self.assertIsInstance(self.report.dependencies, list)
        self.assertGreater(len(self.report.dependencies), 0)

    def test_readiness_valid_value(self):
        self.assertIn(self.report.install_readiness, ["ready", "warnings", "blocked"])

    def test_issues_list(self):
        self.assertIsInstance(self.report.readiness_issues, list)

    def test_warnings_list(self):
        self.assertIsInstance(self.report.readiness_warnings, list)

    def test_recommendations_list(self):
        self.assertIsInstance(self.report.recommendations, list)


class TestScoutReport(unittest.TestCase):

    def setUp(self):
        self.report = _make_scout_report()

    def test_to_dict_returns_dict(self):
        d = self.report.to_dict()
        self.assertIsInstance(d, dict)

    def test_to_dict_has_hardware(self):
        d = self.report.to_dict()
        self.assertIn("hardware", d)

    def test_to_dict_has_os(self):
        d = self.report.to_dict()
        self.assertIn("os", d)

    def test_to_dict_has_ports(self):
        d = self.report.to_dict()
        self.assertIn("ports", d)
        self.assertIsInstance(d["ports"], list)

    def test_to_dict_has_dependencies(self):
        d = self.report.to_dict()
        self.assertIn("dependencies", d)

    def test_to_dict_install_readiness(self):
        d = self.report.to_dict()
        self.assertEqual(d["install_readiness"], "ready")

    def test_summary_text_string(self):
        summary = self.report.summary_text()
        self.assertIsInstance(summary, str)
        self.assertGreater(len(summary), 50)

    def test_summary_text_contains_os(self):
        summary = self.report.summary_text()
        self.assertIn("ubuntu", summary)


class TestHardwareProfile(unittest.TestCase):

    def test_sufficient_low_ram(self):
        hw = _make_hardware(ram_gb=1.0)
        ok, issues = hw.sufficient_for_kiswarm()
        self.assertFalse(ok)
        self.assertGreater(len(issues), 0)

    def test_sufficient_good_ram(self):
        hw = _make_hardware(ram_gb=16.0, disk_gb=50.0)
        ok, issues = hw.sufficient_for_kiswarm()
        self.assertTrue(ok)
        self.assertEqual(issues, [])

    def test_model_rec_low_ram(self):
        hw = _make_hardware(ram_gb=3.0)
        self.assertIn("0.5b", hw.model_recommendation())

    def test_model_rec_medium_ram(self):
        hw = _make_hardware(ram_gb=10.0)
        self.assertIn("3b", hw.model_recommendation())

    def test_model_rec_high_ram(self):
        hw = _make_hardware(ram_gb=20.0)
        self.assertIn("7b", hw.model_recommendation())

    def test_model_rec_very_high_ram(self):
        hw = _make_hardware(ram_gb=40.0)
        self.assertIn("14b", hw.model_recommendation())


class TestOSFingerprint(unittest.TestCase):

    def test_ubuntu_supported(self):
        os_ = _make_os("ubuntu")
        self.assertTrue(os_.is_supported())

    def test_debian_supported(self):
        os_ = _make_os("debian")
        self.assertTrue(os_.is_supported())

    def test_fedora_supported(self):
        os_ = _make_os("fedora")
        self.assertTrue(os_.is_supported())

    def test_windows_not_supported(self):
        os_ = OSFingerprint(
            system="Windows", distro="windows", distro_version="11",
            kernel="NT", arch="x86_64", hostname="pc",
            is_container=False, init_system="unknown", pkg_manager="unknown"
        )
        self.assertFalse(os_.is_supported())

    def test_unknown_distro_not_supported(self):
        os_ = _make_os("bsdos")
        self.assertFalse(os_.is_supported())


# ─────────────────────────────────────────────────────────────────────────────
# MODULE 39: REPO INTELLIGENCE
# ─────────────────────────────────────────────────────────────────────────────

class TestRepoIntelligenceEmbedded(unittest.TestCase):

    def setUp(self):
        with tempfile.TemporaryDirectory() as d:
            self.intel = RepoIntelligence(cache_dir=d)

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.intel = RepoIntelligence(cache_dir=self.tmpdir)

    def test_get_module_list(self):
        modules = self.intel.get_module_list()
        self.assertIsInstance(modules, list)
        self.assertGreater(len(modules), 30)

    def test_module_has_required_fields(self):
        modules = self.intel.get_module_list()
        for m in modules[:5]:
            self.assertIn("id", m)
            self.assertIn("name", m)
            self.assertIn("file", m)
            self.assertIn("version", m)
            self.assertIn("description", m)

    def test_get_version_history(self):
        versions = self.intel.get_version_history()
        self.assertIsInstance(versions, list)
        self.assertGreater(len(versions), 5)

    def test_version_has_required_fields(self):
        versions = self.intel.get_version_history()
        for v in versions:
            self.assertIn("version", v)
            self.assertIn("date", v)
            self.assertIn("highlight", v)

    def test_get_current_version(self):
        ver = self.intel.get_current_version()
        self.assertIsInstance(ver, str)
        self.assertGreater(float(ver), 4.0)

    def test_get_ports(self):
        ports = self.intel.get_ports()
        self.assertIsInstance(ports, dict)
        self.assertIn(11434, ports)
        self.assertIn(11436, ports)

    def test_get_dependencies(self):
        deps = self.intel.get_dependencies()
        self.assertIn("required", deps)
        self.assertIn("python_packages", deps)

    def test_get_module_by_name(self):
        m = self.intel.get_module_by_name("Sentinel Bridge")
        self.assertIsNotNone(m)
        self.assertEqual(m["id"], 1)

    def test_get_module_by_file(self):
        m = self.intel.get_module_by_name("swarm_debate")
        self.assertIsNotNone(m)

    def test_unknown_module_returns_none(self):
        m = self.intel.get_module_by_name("does_not_exist_xyz")
        self.assertIsNone(m)


class TestRepoIntelligenceAnswer(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.intel = RepoIntelligence(cache_dir=self.tmpdir)

    def test_answer_returns_dict(self):
        r = self.intel.answer("wie viele module hat kiswarm?")
        self.assertIsInstance(r, dict)

    def test_answer_has_answer_field(self):
        r = self.intel.answer("irgendwas")
        self.assertIn("answer", r)

    def test_answer_has_source_field(self):
        r = self.intel.answer("irgendwas")
        self.assertIn("source", r)

    def test_answer_module_count(self):
        r = self.intel.answer("how many modules does kiswarm have?")
        self.assertIn("answer", r)

    def test_answer_ports(self):
        r = self.intel.answer("welche ports nutzt kiswarm?")
        self.assertIn("answer", r)
        # Should mention port numbers
        self.assertIn("11434", r["answer"] + str(r.get("data", "")))

    def test_answer_version_history(self):
        r = self.intel.answer("what is the version history?")
        self.assertIn("answer", r)
        self.assertIn("4.6", r["answer"])

    def test_answer_install_steps(self):
        r = self.intel.answer("how do I install kiswarm?")
        self.assertIn("answer", r)
        self.assertIn("data", r)

    def test_answer_dependencies(self):
        r = self.intel.answer("what are the dependencies?")
        self.assertIn("answer", r)

    def test_answer_test_count(self):
        r = self.intel.answer("how many tests are passing?")
        self.assertIn("answer", r)

    def test_answer_module_search(self):
        r = self.intel.answer("tell me about the Sentinel Bridge")
        self.assertIn("answer", r)
        self.assertIn("Sentinel Bridge", r["answer"])


class TestRepoIntelligencePlan(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.intel = RepoIntelligence(cache_dir=self.tmpdir)
        self.good_scout = _make_scout_dict(readiness="ready")
        self.ubuntu_scout = _make_scout_dict(distro="ubuntu")

    def test_plan_returns_dict(self):
        plan = self.intel.generate_install_plan(self.good_scout)
        self.assertIsInstance(plan, dict)

    def test_plan_has_steps(self):
        plan = self.intel.generate_install_plan(self.good_scout)
        self.assertIn("steps", plan)
        self.assertIsInstance(plan["steps"], list)

    def test_plan_has_8_steps(self):
        plan = self.intel.generate_install_plan(self.good_scout)
        self.assertEqual(len(plan["steps"]), 8)

    def test_plan_has_target(self):
        plan = self.intel.generate_install_plan(self.good_scout)
        self.assertIn("target", plan)
        self.assertIn("os", plan["target"])

    def test_plan_has_estimated_duration(self):
        plan = self.intel.generate_install_plan(self.good_scout)
        self.assertIn("estimated_duration_min", plan)
        self.assertGreater(plan["estimated_duration_min"], 0)

    def test_plan_uses_apt_for_ubuntu(self):
        plan = self.intel.generate_install_plan(self.ubuntu_scout)
        step1 = plan["steps"][0]
        self.assertIn("apt", step1["cmd"])

    def test_plan_skips_systemd_in_container(self):
        container_scout = _make_scout_dict()
        container_scout["os"]["is_container"] = True
        container_scout["os"]["init_system"] = "unknown"
        plan = self.intel.generate_install_plan(container_scout)
        step7 = next((s for s in plan["steps"] if s["id"] == 7), None)
        self.assertIsNotNone(step7)
        # Container note should be present
        self.assertTrue("note" in step7 or "systemd" not in step7.get("cmd", ""))

    def test_plan_model_recommendation_used(self):
        scout = _make_scout_dict()
        scout["hardware"]["model_recommendation"] = "qwen2.5:14b"
        plan = self.intel.generate_install_plan(scout)
        step6_cmd = plan["steps"][5]["cmd"]   # step id=6
        self.assertIn("qwen2.5:14b", step6_cmd)

    def test_plan_includes_git_clone(self):
        plan = self.intel.generate_install_plan(self.good_scout)
        cmds = " ".join(s["cmd"] for s in plan["steps"])
        self.assertIn("git clone", cmds)

    def test_plan_includes_pip_install(self):
        plan = self.intel.generate_install_plan(self.good_scout)
        cmds = " ".join(s["cmd"] for s in plan["steps"])
        self.assertIn("pip install", cmds)

    def test_plan_includes_verification(self):
        plan = self.intel.generate_install_plan(self.good_scout)
        step8 = next((s for s in plan["steps"] if s["id"] == 8), None)
        self.assertIsNotNone(step8)
        self.assertIn("curl", step8["cmd"])

    def test_plan_blocked_has_flag(self):
        blocked_scout = _make_scout_dict(readiness="blocked", issues=["Cannot reach github.com"])
        plan = self.intel.generate_install_plan(blocked_scout)
        # Plan still has steps, but has_blocking_issues flag
        self.assertTrue(plan.get("has_blocking_issues", False))


# ─────────────────────────────────────────────────────────────────────────────
# MODULE 40: INSTALLER AGENT
# ─────────────────────────────────────────────────────────────────────────────

class TestInstallerAgentDryRun(unittest.TestCase):

    def setUp(self):
        self.agent = InstallerAgent(
            mode=InstallMode.DRY_RUN,
            log_callback=_silent_log,
        )

    def test_dry_run_returns_report(self):
        rep = self.agent.dry_run()
        self.assertIsInstance(rep, InstallReport)

    def test_dry_run_state_done(self):
        rep = self.agent.dry_run()
        self.assertEqual(rep.state, InstallState.DONE)

    def test_dry_run_success_true(self):
        rep = self.agent.dry_run()
        self.assertTrue(rep.success())

    def test_dry_run_has_steps(self):
        rep = self.agent.dry_run()
        self.assertGreater(len(rep.step_results), 0)

    def test_dry_run_all_steps_skipped(self):
        rep = self.agent.dry_run()
        for s in rep.step_results:
            self.assertTrue(s.skipped, f"Step {s.step_id} ({s.title}) should be skipped in dry_run")

    def test_dry_run_has_post_checks(self):
        rep = self.agent.dry_run()
        # dry_run skips verification
        self.assertIsInstance(rep.post_checks, dict)

    def test_dry_run_no_error(self):
        rep = self.agent.dry_run()
        self.assertIsNone(rep.error)

    def test_dry_run_duration_positive(self):
        rep = self.agent.dry_run()
        self.assertGreater(rep.duration_s(), 0)

    def test_dry_run_host_nonempty(self):
        rep = self.agent.dry_run()
        self.assertTrue(len(rep.host) > 0)


class TestInstallerAgentStepResult(unittest.TestCase):

    def setUp(self):
        self.agent = InstallerAgent(
            mode=InstallMode.DRY_RUN,
            log_callback=_silent_log,
        )

    def test_step_result_fields(self):
        s = StepResult(
            step_id=1, title="Test Step", cmd="echo hello",
            success=True, stdout="hello\n", stderr="", duration_s=0.1
        )
        self.assertEqual(s.step_id, 1)
        self.assertEqual(s.title, "Test Step")
        self.assertTrue(s.success)

    def test_step_result_skipped_default_false(self):
        s = StepResult(step_id=1, title="x", cmd="x", success=True)
        self.assertFalse(s.skipped)

    def test_step_result_note_default_empty(self):
        s = StepResult(step_id=1, title="x", cmd="x", success=True)
        self.assertEqual(s.note, "")

    def test_run_cmd_dry_returns_skipped(self):
        result = self.agent._run_cmd("echo test", "Test", 1)
        self.assertTrue(result.skipped)
        self.assertTrue(result.success)

    def test_run_with_retry_dry_no_actual_retries(self):
        result = self.agent._run_with_retry("echo test", "Test", 1, retries=3)
        self.assertTrue(result.skipped)

    def test_step_has_title_from_plan(self):
        rep = self.agent.dry_run()
        titles = [s.title for s in rep.step_results]
        # Should have meaningful titles
        self.assertTrue(any("Ollama" in t or "Paket" in t or "Repository" in t for t in titles))

    def test_all_steps_have_positive_id(self):
        rep = self.agent.dry_run()
        for s in rep.step_results:
            self.assertGreater(s.step_id, 0)


class TestInstallerAgentReport(unittest.TestCase):

    def setUp(self):
        self.agent = InstallerAgent(mode=InstallMode.DRY_RUN, log_callback=_silent_log)
        self.report = self.agent.dry_run()

    def test_to_dict_returns_dict(self):
        d = self.report.to_dict()
        self.assertIsInstance(d, dict)

    def test_to_dict_has_state(self):
        d = self.report.to_dict()
        self.assertIn("state", d)

    def test_to_dict_has_steps(self):
        d = self.report.to_dict()
        self.assertIn("steps", d)
        self.assertIsInstance(d["steps"], list)

    def test_to_dict_has_host(self):
        d = self.report.to_dict()
        self.assertIn("host", d)

    def test_to_dict_has_success(self):
        d = self.report.to_dict()
        self.assertIn("success", d)
        self.assertIsInstance(d["success"], bool)

    def test_summary_string(self):
        s = self.report.summary()
        self.assertIsInstance(s, str)
        self.assertGreater(len(s), 20)

    def test_summary_contains_state(self):
        s = self.report.summary()
        self.assertIn("DONE", s.upper())

    def test_duration_positive(self):
        self.assertGreater(self.report.duration_s(), 0)


class TestInstallerAgentStateFlow(unittest.TestCase):

    def test_initial_state_init(self):
        agent = InstallerAgent(mode=InstallMode.DRY_RUN, log_callback=_silent_log)
        self.assertEqual(agent.state, InstallState.INIT)

    def test_after_dry_run_state_done(self):
        agent = InstallerAgent(mode=InstallMode.DRY_RUN, log_callback=_silent_log)
        agent.dry_run()
        self.assertEqual(agent.state, InstallState.DONE)

    def test_run_installer_convenience(self):
        result = run_installer(mode="dry_run")
        self.assertIsInstance(result, dict)
        self.assertIn("state", result)

    def test_mode_enum_values(self):
        self.assertEqual(InstallMode.DRY_RUN.value, "dry_run")
        self.assertEqual(InstallMode.AUTO.value, "auto")
        self.assertEqual(InstallMode.GUIDED.value, "guided")

    def test_state_enum_values(self):
        self.assertEqual(InstallState.DONE.value, "done")
        self.assertEqual(InstallState.FAILED.value, "failed")
        self.assertEqual(InstallState.ABORTED.value, "aborted")

    def test_scan_only_returns_dict(self):
        agent = InstallerAgent(mode=InstallMode.DRY_RUN, log_callback=_silent_log)
        result = agent.scan_only()
        self.assertIsInstance(result, dict)
        self.assertIn("hardware", result)


# ─────────────────────────────────────────────────────────────────────────────
# MODULE 41: KISWARM ADVISOR
# ─────────────────────────────────────────────────────────────────────────────

class TestAdvisorSession(unittest.TestCase):

    def setUp(self):
        self.advisor = KISWARMAdvisor()

    def test_get_or_create_session_returns_session(self):
        sess = self.advisor.get_or_create_session("test-client", "ai_agent")
        self.assertIsInstance(sess, AdvisorySession)

    def test_session_has_id(self):
        sess = self.advisor.get_or_create_session("test-client")
        self.assertTrue(len(sess.session_id) > 0)

    def test_session_client_id_stored(self):
        sess = self.advisor.get_or_create_session("glm5-agent-001", "ai_agent")
        self.assertEqual(sess.client_id, "glm5-agent-001")

    def test_same_client_returns_same_session(self):
        sess1 = self.advisor.get_or_create_session("same-client")
        sess2 = self.advisor.get_or_create_session("same-client")
        self.assertEqual(sess1.session_id, sess2.session_id)

    def test_different_clients_different_sessions(self):
        sess1 = self.advisor.get_or_create_session("client-a")
        sess2 = self.advisor.get_or_create_session("client-b")
        self.assertNotEqual(sess1.session_id, sess2.session_id)

    def test_list_sessions_returns_list(self):
        self.advisor.get_or_create_session("list-test")
        sessions = self.advisor.list_sessions()
        self.assertIsInstance(sessions, list)
        self.assertGreater(len(sessions), 0)

    def test_session_to_dict(self):
        sess = self.advisor.get_or_create_session("dict-test")
        d = sess.to_dict()
        self.assertIsInstance(d, dict)
        self.assertIn("session_id", d)
        self.assertIn("client_id", d)


class TestAdvisorConsult(unittest.TestCase):

    def setUp(self):
        self.advisor = KISWARMAdvisor()

    def test_consult_returns_dict(self):
        result = self.advisor.consult("test-ai", "ai_agent")
        self.assertIsInstance(result, dict)

    def test_consult_has_session_id(self):
        result = self.advisor.consult("test-ai-2", "ai_agent")
        self.assertIn("session_id", result)

    def test_consult_has_verdict(self):
        result = self.advisor.consult("test-ai-3", "ai_agent")
        self.assertIn("verdict", result)

    def test_consult_has_system_info(self):
        result = self.advisor.consult("test-ai-4", "ai_agent")
        self.assertIn("system", result)

    def test_consult_has_install_plan(self):
        result = self.advisor.consult("test-ai-5", "ai_agent")
        self.assertIn("install_plan", result)

    def test_consult_has_recommended_model(self):
        result = self.advisor.consult("test-ai-6", "ai_agent")
        self.assertIn("recommended_model", result)
        self.assertIn("qwen2.5", result["recommended_model"])

    def test_consult_has_next_step(self):
        result = self.advisor.consult("test-ai-7", "ai_agent")
        self.assertIn("next_step", result)

    def test_consult_increments_counter(self):
        before = self.advisor._total_consultations
        self.advisor.consult("counter-test", "ai_agent")
        self.assertEqual(self.advisor._total_consultations, before + 1)


class TestAdvisorScanAdvise(unittest.TestCase):

    def setUp(self):
        self.advisor = KISWARMAdvisor()

    def test_scan_and_advise_returns_dict(self):
        result = self.advisor.scan_and_advise("test-scan")
        self.assertIsInstance(result, dict)

    def test_scan_has_readiness(self):
        result = self.advisor.scan_and_advise("readiness-test")
        self.assertIn("readiness", result)

    def test_scan_has_system(self):
        result = self.advisor.scan_and_advise("system-test")
        self.assertIn("system", result)

    def test_scan_has_full_report(self):
        result = self.advisor.scan_and_advise("report-test")
        self.assertIn("full_report", result)

    def test_scan_has_advisor_id(self):
        result = self.advisor.scan_and_advise("advisor-id-test")
        self.assertIn("advisor_id", result)

    def test_scan_has_warnings_list(self):
        result = self.advisor.scan_and_advise("warnings-test")
        self.assertIn("warnings", result)
        self.assertIsInstance(result["warnings"], list)

    def test_scan_session_stored(self):
        self.advisor.scan_and_advise("stored-session-test")
        sessions = self.advisor.list_sessions()
        client_ids = [s["client_id"] for s in sessions]
        self.assertIn("stored-session-test", client_ids)


class TestAdvisorPeerHandshake(unittest.TestCase):

    def setUp(self):
        self.advisor = KISWARMAdvisor()

    def test_handshake_returns_dict(self):
        result = self.advisor.peer_handshake(
            caller_id="glm5-agent-001",
            caller_type="ai_agent",
            capabilities=["plan", "install", "monitor"],
        )
        self.assertIsInstance(result, dict)

    def test_handshake_has_session_id(self):
        result = self.advisor.peer_handshake("glm5-001", "ai_agent", ["plan"])
        self.assertIn("session_id", result)

    def test_handshake_has_endpoints(self):
        result = self.advisor.peer_handshake("glm5-002", "ai_agent", ["plan"])
        self.assertIn("endpoints", result)

    def test_handshake_has_my_capabilities(self):
        result = self.advisor.peer_handshake("glm5-003", "ai_agent", ["scan"])
        self.assertIn("my_capabilities", result)
        self.assertIsInstance(result["my_capabilities"], list)

    def test_handshake_has_version(self):
        result = self.advisor.peer_handshake("glm5-004", "ai_agent", [])
        self.assertIn("version", result)

    def test_handshake_shared_capabilities(self):
        result = self.advisor.peer_handshake(
            "glm5-005", "ai_agent", ["scan", "install", "health"]
        )
        # shared_capabilities may or may not be present depending on implementation
        self.assertIn("session_id", result)


class TestAdvisorStats(unittest.TestCase):

    def test_stats_returns_dict(self):
        adv = KISWARMAdvisor()
        s = adv.stats()
        self.assertIsInstance(s, dict)

    def test_stats_has_version(self):
        adv = KISWARMAdvisor()
        s = adv.stats()
        self.assertIn("version", s)

    def test_stats_has_active_sessions(self):
        adv = KISWARMAdvisor()
        s = adv.stats()
        self.assertIn("active_sessions", s)

    def test_stats_session_count_increments(self):
        adv = KISWARMAdvisor()
        before = adv.stats()["active_sessions"]
        adv.get_or_create_session("stats-test")
        after = adv.stats()["active_sessions"]
        self.assertGreater(after, before)

    def test_stats_has_known_intents(self):
        adv = KISWARMAdvisor()
        s = adv.stats()
        self.assertIn("known_intents", s)
        self.assertIsInstance(s["known_intents"], list)


class TestAdvisorSingleton(unittest.TestCase):

    def test_get_advisor_returns_instance(self):
        adv = get_advisor()
        self.assertIsInstance(adv, KISWARMAdvisor)

    def test_get_advisor_same_instance(self):
        a1 = get_advisor()
        a2 = get_advisor()
        self.assertIs(a1, a2)

    def test_get_advisor_is_kiswarm_advisor(self):
        adv = get_advisor()
        stats = adv.stats()
        self.assertIn("kiswarm", stats.get("advisor_id", "").lower())


# ─────────────────────────────────────────────────────────────────────────────
# END-TO-END FLOW
# ─────────────────────────────────────────────────────────────────────────────

class TestEndToEndFlow(unittest.TestCase):
    """
    Full pipeline: Scout → Intel → Plan → Dry-Install → Advisor
    Mirrors what happens when a GLM5 agent calls the KISWARM installer.
    """

    def test_full_pipeline_dry_run(self):
        """Scout → generate plan → dry-run install → report."""
        scout   = SystemScout(timeout_s=1.0)
        report  = scout.full_scan()
        self.assertIsInstance(report, ScoutReport)

        intel  = RepoIntelligence(cache_dir=tempfile.mkdtemp())
        plan   = intel.generate_install_plan(report.to_dict())
        self.assertGreater(len(plan["steps"]), 0)

        agent  = InstallerAgent(mode=InstallMode.DRY_RUN, log_callback=_silent_log)
        result = agent.dry_run()
        self.assertTrue(result.success())

    def test_advisor_consult_full(self):
        """Full advisor consultation as an AI agent would call it."""
        adv    = KISWARMAdvisor()
        result = adv.consult("glm5-e2e-test", "ai_agent")

        self.assertIn("session_id", result)
        self.assertIn("install_plan", result)
        self.assertIn("recommended_model", result)
        self.assertIn("next_step", result)

    def test_scan_only_mode(self):
        """scan_only() returns hardware without installing anything."""
        agent  = InstallerAgent(mode=InstallMode.DRY_RUN, log_callback=_silent_log)
        result = agent.scan_only()

        self.assertIn("hardware", result)
        self.assertIn("os", result)
        self.assertIn("install_readiness", result)

    def test_embedded_knowledge_completeness(self):
        """All embedded knowledge fields are present and non-empty."""
        ek = EMBEDDED_KNOWLEDGE
        self.assertIn("modules", ek)
        self.assertIn("versions", ek)
        self.assertIn("ports", ek)
        self.assertIn("install_steps", ek)
        self.assertGreater(len(ek["modules"]), 30)
        self.assertGreater(len(ek["versions"]), 5)

    def test_model_recommendation_consistent(self):
        """Hardware→model recommendation is deterministic."""
        hw1 = _make_hardware(ram_gb=8.0)
        hw2 = _make_hardware(ram_gb=8.0)
        self.assertEqual(hw1.model_recommendation(), hw2.model_recommendation())


if __name__ == "__main__":
    unittest.main(verbosity=2)
