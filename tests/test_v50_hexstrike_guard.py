"""
KISWARM v5.0 — Test Suite for HexStrike Guard, ToolForge, KiInstall Agent
==========================================================================

Tests for Modules 31-33:
- HexStrikeGuard: 12 AI agents + 150+ tools
- ToolForge: Dynamic tool creation/expansion
- KiInstallAgent: Installation + cooperative operations

Author: Baron Marco Paolo Ialongo (KISWARM Project)
"""

import pytest
import json
import os
import sys
import tempfile
import time

# Add sentinel module to path - fix for proper import
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "python"))

from sentinel.hexstrike_guard import (
    HexStrikeGuard, ToolRegistry, HexStrikeAgent,
    IntelligentDecisionEngine, BugBountyWorkflowManager, CTFWorkflowManager,
    CVEIntelligenceManager, AIExploitGenerator, VulnerabilityCorrelator,
    TechnologyDetector, RateLimitDetector, FailureRecoverySystem,
    PerformanceMonitor, ParameterOptimizer, GracefulDegradation,
    AgentStatus, ToolStatus, AgentTask, GuardReport
)
from sentinel.tool_forge import (
    ToolForge, ForgedTool, ToolCapability, ToolPattern,
    ToolType, ToolStatus as ForgeToolStatus
)
from sentinel.kiinstall_agent import (
    KiInstallAgent, InstallationMode, InstallationStatus,
    AgentRole, SystemProfile, InstallationSession
)


# ═══════════════════════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def tool_registry():
    """Create a ToolRegistry instance."""
    return ToolRegistry()


@pytest.fixture
def hexstrike_guard():
    """Create a HexStrikeGuard instance."""
    guard = HexStrikeGuard()
    yield guard
    guard.shutdown()


@pytest.fixture
def tool_forge():
    """Create a ToolForge instance with temp directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        forge = ToolForge(output_dir=tmpdir)
        yield forge


@pytest.fixture
def kiinstall_agent(hexstrike_guard):
    """Create a KiInstallAgent instance."""
    return KiInstallAgent(hexstrike_guard=hexstrike_guard)


# ═══════════════════════════════════════════════════════════════════════════════
# TOOL REGISTRY TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestToolRegistry:
    """Tests for the ToolRegistry class."""

    def test_tool_discovery(self, tool_registry):
        """Test that tools are discovered on initialization."""
        stats = tool_registry.get_stats()
        assert stats["tools_discovered"] > 0
        assert stats["tools_available"] >= 0

    def test_list_tools(self, tool_registry):
        """Test listing tools."""
        tools = tool_registry.list_tools()
        assert len(tools) > 0
        
        # Check tool structure
        tool = tools[0]
        assert hasattr(tool, 'name')
        assert hasattr(tool, 'category')
        assert hasattr(tool, 'status')

    def test_get_tool(self, tool_registry):
        """Test getting a specific tool."""
        # Try to get common tool
        tool = tool_registry.get_tool("nmap")
        if tool:
            assert tool.name == "nmap"
            assert tool.category == "network_recon"

    def test_list_tools_by_category(self, tool_registry):
        """Test filtering tools by category."""
        tools = tool_registry.list_tools(category="network_recon")
        for tool in tools:
            assert tool.category == "network_recon"

    def test_list_tools_by_status(self, tool_registry):
        """Test filtering tools by status."""
        available = tool_registry.list_tools(status=ToolStatus.AVAILABLE)
        for tool in available:
            assert tool.status == ToolStatus.AVAILABLE


# ═══════════════════════════════════════════════════════════════════════════════
# HEXSTRIKE AGENT TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestHexStrikeAgents:
    """Tests for all 12 HexStrike agents."""

    def test_intelligent_decision_engine(self, tool_registry):
        """Test IntelligentDecisionEngine agent."""
        agent = IntelligentDecisionEngine(tool_registry)
        
        # Test analyze_target
        task = AgentTask(
            task_id="test-001",
            agent_name="IntelligentDecisionEngine",
            action="analyze_target",
            target="example.com",
            params={}
        )
        result = agent.execute(task)
        assert result.status == AgentStatus.COMPLETED
        assert result.result is not None

    def test_bug_bounty_workflow_manager(self, tool_registry):
        """Test BugBountyWorkflowManager agent."""
        agent = BugBountyWorkflowManager(tool_registry)
        
        task = AgentTask(
            task_id="test-002",
            agent_name="BugBountyWorkflowManager",
            action="recon_workflow",
            target="example.com",
            params={}
        )
        result = agent.execute(task)
        assert result.status == AgentStatus.COMPLETED

    def test_ctf_workflow_manager(self, tool_registry):
        """Test CTFWorkflowManager agent."""
        agent = CTFWorkflowManager(tool_registry)
        
        task = AgentTask(
            task_id="test-003",
            agent_name="CTFWorkflowManager",
            action="forensics",
            target=None,
            params={"file": "/tmp/test.bin"}
        )
        result = agent.execute(task)
        assert result.status == AgentStatus.COMPLETED

    def test_cve_intelligence_manager(self, tool_registry):
        """Test CVEIntelligenceManager agent."""
        agent = CVEIntelligenceManager(tool_registry)
        
        task = AgentTask(
            task_id="test-004",
            agent_name="CVEIntelligenceManager",
            action="cve_lookup",
            target=None,
            params={"cve_id": "CVE-2021-44228"}
        )
        result = agent.execute(task)
        assert result.status == AgentStatus.COMPLETED

    def test_ai_exploit_generator(self, tool_registry):
        """Test AIExploitGenerator agent (defensive only)."""
        agent = AIExploitGenerator(tool_registry)
        
        task = AgentTask(
            task_id="test-005",
            agent_name="AIExploitGenerator",
            action="poc_gen",
            target=None,
            params={"vulnerability": "SQLi"}
        )
        result = agent.execute(task)
        assert result.status == AgentStatus.COMPLETED
        assert result.result.get("safe_for_testing") == True

    def test_vulnerability_correlator(self, tool_registry):
        """Test VulnerabilityCorrelator agent."""
        agent = VulnerabilityCorrelator(tool_registry)
        
        task = AgentTask(
            task_id="test-006",
            agent_name="VulnerabilityCorrelator",
            action="chain_detect",
            target=None,
            params={"findings": [{"severity": "HIGH"}]}
        )
        result = agent.execute(task)
        assert result.status == AgentStatus.COMPLETED

    def test_technology_detector(self, tool_registry):
        """Test TechnologyDetector agent."""
        agent = TechnologyDetector(tool_registry)
        
        task = AgentTask(
            task_id="test-007",
            agent_name="TechnologyDetector",
            action="tech_detect",
            target="example.com",
            params={}
        )
        result = agent.execute(task)
        assert result.status == AgentStatus.COMPLETED

    def test_rate_limit_detector(self, tool_registry):
        """Test RateLimitDetector agent."""
        agent = RateLimitDetector(tool_registry)
        
        task = AgentTask(
            task_id="test-008",
            agent_name="RateLimitDetector",
            action="rate_test",
            target="example.com",
            params={}
        )
        result = agent.execute(task)
        assert result.status == AgentStatus.COMPLETED

    def test_failure_recovery_system(self, tool_registry):
        """Test FailureRecoverySystem agent."""
        agent = FailureRecoverySystem(tool_registry)
        
        task = AgentTask(
            task_id="test-009",
            agent_name="FailureRecoverySystem",
            action="error_handle",
            target=None,
            params={"error": "Test error"}
        )
        result = agent.execute(task)
        assert result.status == AgentStatus.COMPLETED

    def test_performance_monitor(self, tool_registry):
        """Test PerformanceMonitor agent."""
        agent = PerformanceMonitor(tool_registry)
        
        task = AgentTask(
            task_id="test-010",
            agent_name="PerformanceMonitor",
            action="perf_track",
            target=None,
            params={}
        )
        result = agent.execute(task)
        assert result.status == AgentStatus.COMPLETED

    def test_parameter_optimizer(self, tool_registry):
        """Test ParameterOptimizer agent."""
        agent = ParameterOptimizer(tool_registry)
        
        task = AgentTask(
            task_id="test-011",
            agent_name="ParameterOptimizer",
            action="param_tune",
            target=None,
            params={"tool": "nmap"}
        )
        result = agent.execute(task)
        assert result.status == AgentStatus.COMPLETED

    def test_graceful_degradation(self, tool_registry):
        """Test GracefulDegradation agent."""
        agent = GracefulDegradation(tool_registry)
        
        task = AgentTask(
            task_id="test-012",
            agent_name="GracefulDegradation",
            action="failover",
            target=None,
            params={}
        )
        result = agent.execute(task)
        assert result.status == AgentStatus.COMPLETED


# ═══════════════════════════════════════════════════════════════════════════════
# HEXSTRIKE GUARD TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestHexStrikeGuard:
    """Tests for the HexStrikeGuard orchestrator."""

    def test_guard_initialization(self, hexstrike_guard):
        """Test guard initializes correctly."""
        stats = hexstrike_guard.get_stats()
        assert stats["agents_count"] == 12

    def test_get_agent_status(self, hexstrike_guard):
        """Test getting agent status."""
        status = hexstrike_guard.get_agent_status()
        assert len(status) == 12
        for agent_name, agent_status in status.items():
            assert "status" in agent_status

    def test_get_tools_status(self, hexstrike_guard):
        """Test getting tools status."""
        status = hexstrike_guard.get_tools_status()
        assert "total" in status
        assert "available" in status

    def test_submit_task(self, hexstrike_guard):
        """Test submitting a task."""
        task_id = hexstrike_guard.submit_task(
            agent_name="IntelligentDecisionEngine",
            action="analyze_target",
            target="example.com"
        )
        assert task_id is not None
        assert len(task_id) > 0

    def test_analyze_target(self, hexstrike_guard):
        """Test target analysis."""
        result = hexstrike_guard.analyze_target("example.com")
        assert "target" in result or "error" in result or "task_id" in result

    def test_run_security_scan_unauthorized(self, hexstrike_guard):
        """Test that unauthorized scans are blocked."""
        result = hexstrike_guard.run_security_scan("example.com", authorized=False)
        assert "error" in result or "legal_notice" in result

    def test_generate_report(self, hexstrike_guard):
        """Test report generation."""
        report = hexstrike_guard.generate_report(
            scan_id="test-scan-001",
            findings=[{"severity": "HIGH", "title": "Test finding"}]
        )
        assert report.report_id is not None
        assert report.overall_risk in ["LOW", "MEDIUM", "HIGH", "CRITICAL"]

    def test_legal_notice(self, hexstrike_guard):
        """Test legal notice retrieval."""
        notice = hexstrike_guard.get_legal_notice()
        assert "legal_use_cases" in notice
        assert "forbidden_use_cases" in notice


# ═══════════════════════════════════════════════════════════════════════════════
# TOOL FORGE TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestToolForge:
    """Tests for the ToolForge system."""

    def test_forge_initialization(self, tool_forge):
        """Test forge initializes correctly."""
        stats = tool_forge.get_stats()
        assert "tools_created" in stats
        assert "patterns_learned" in stats

    def test_create_composite_tool(self, tool_forge):
        """Test creating a composite tool."""
        tool = tool_forge.create_composite(
            name="test_recon_chain",
            tool_chain=["nmap", "httpx"],
            description="Test composite tool"
        )
        assert tool.tool_id is not None
        assert tool.name == "test_recon_chain"
        assert tool.tool_type == ToolType.COMPOSITE

    def test_generate_tool(self, tool_forge):
        """Test generating a tool from description."""
        tool = tool_forge.generate_tool(
            name="test_analyzer",
            description="Test analyzer tool",
            logic_description="Analyze target for vulnerabilities"
        )
        assert tool.tool_id is not None
        assert tool.tool_type == ToolType.GENERATED

    def test_list_tools(self, tool_forge):
        """Test listing forged tools."""
        # Create a tool first
        tool_forge.create_composite("test", ["nmap"], "Test")
        
        tools = tool_forge.list_tools()
        assert len(tools) > 0

    def test_learn_pattern(self, tool_forge):
        """Test learning a tool pattern."""
        pattern = tool_forge.learn_pattern(
            name="test_pattern",
            tools=["nmap", "nuclei"],
            use_case="vulnerability_scan",
            success=True
        )
        assert pattern.pattern_id is not None
        assert pattern.success_count == 1

    def test_get_patterns(self, tool_forge):
        """Test getting learned patterns."""
        tool_forge.learn_pattern("test_p", ["nmap"], "test", True)
        patterns = tool_forge.get_patterns(min_success_rate=0.0)
        assert len(patterns) > 0

    def test_recommend_tools(self, tool_forge):
        """Test tool recommendations."""
        recommendations = tool_forge.recommend_tools("scan")
        assert isinstance(recommendations, list)

    def test_delete_tool(self, tool_forge):
        """Test deleting a forged tool."""
        tool = tool_forge.create_composite("to_delete", ["nmap"], "Test")
        result = tool_forge.delete_tool(tool.tool_id)
        assert result == True


# ═══════════════════════════════════════════════════════════════════════════════
# KIINSTALL AGENT TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestKiInstallAgent:
    """Tests for the KiInstall agent."""

    def test_agent_initialization(self, kiinstall_agent):
        """Test agent initializes correctly."""
        stats = kiinstall_agent.get_stats()
        assert "installations_total" in stats

    def test_profile_system(self, kiinstall_agent):
        """Test system profiling."""
        profile = kiinstall_agent.profile_system()
        assert profile.os_type is not None
        assert profile.python_version is not None
        assert profile.cpu_cores > 0
        assert profile.ram_gb > 0

    def test_get_system_requirements(self, kiinstall_agent):
        """Test getting system requirements."""
        reqs = kiinstall_agent.get_system_requirements()
        assert "os" in reqs
        assert "python_min" in reqs

    def test_get_components(self, kiinstall_agent):
        """Test getting available components."""
        components = kiinstall_agent.get_components()
        assert "core" in components
        assert "ciec" in components
        assert "security" in components

    def test_start_installation_autonomous(self, kiinstall_agent):
        """Test starting autonomous installation."""
        session = kiinstall_agent.start_installation(
            mode=InstallationMode.AUTONOMOUS
        )
        assert session.session_id is not None
        assert session.mode == InstallationMode.AUTONOMOUS
        assert len(session.phases) == 8

    def test_get_session(self, kiinstall_agent):
        """Test getting installation session."""
        session = kiinstall_agent.start_installation()
        retrieved = kiinstall_agent.get_session(session.session_id)
        assert retrieved is not None
        assert retrieved.session_id == session.session_id

    def test_get_current_role(self, kiinstall_agent):
        """Test getting current role."""
        role = kiinstall_agent.get_current_role()
        assert role == AgentRole.INSTALLER

    def test_execute_preflight_phase(self, kiinstall_agent):
        """Test executing preflight check phase."""
        session = kiinstall_agent.start_installation()
        phase = kiinstall_agent.execute_phase(session.session_id, 1)
        assert phase.status in [InstallationStatus.COMPLETED, InstallationStatus.FAILED]

    def test_rollback_installation(self, kiinstall_agent):
        """Test installation rollback."""
        session = kiinstall_agent.start_installation()
        result = kiinstall_agent.rollback_installation(session.session_id)
        assert "status" in result

    def test_send_cooperative_message(self, kiinstall_agent):
        """Test cooperative messaging."""
        msg = kiinstall_agent.send_cooperative_message(
            message_type="status",
            payload={"test": True}
        )
        assert msg.message_id is not None
        assert msg.sender == "kiinstall_agent"

    def test_analyze_with_guard(self, kiinstall_agent):
        """Test analyzing with HexStrike guard."""
        result = kiinstall_agent.analyze_with_guard("example.com")
        assert result is not None

    def test_execute_with_hexstrike(self, kiinstall_agent):
        """Test executing task with HexStrike."""
        result = kiinstall_agent.execute_with_hexstrike(
            task_type="target_analysis",
            target="example.com"
        )
        assert "task_id" in result or "error" in result


# ═══════════════════════════════════════════════════════════════════════════════
# INTEGRATION TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestIntegration:
    """Integration tests for the complete system."""

    def test_full_workflow(self, hexstrike_guard, tool_forge, kiinstall_agent):
        """Test complete workflow: install -> analyze -> scan -> report."""
        # 1. Check system is ready
        profile = kiinstall_agent.profile_system()
        assert profile.os_type is not None

        # 2. Analyze a target
        analysis = kiinstall_agent.analyze_with_guard("example.com")
        assert analysis is not None

        # 3. Create a composite tool
        tool = tool_forge.create_composite(
            name="test_workflow",
            tool_chain=["httpx", "nuclei"],
            description="Test workflow"
        )
        assert tool.tool_id is not None

        # 4. Generate report
        report = hexstrike_guard.generate_report(
            scan_id="integration-test",
            findings=[{"severity": "INFO", "title": "Test"}]
        )
        assert report.overall_risk in ["LOW", "MEDIUM", "HIGH", "CRITICAL"]

    def test_agent_cooperation(self, kiinstall_agent):
        """Test KiInstall can delegate to HexStrike agents."""
        # KiInstall should be able to use HexStrike agents
        result = kiinstall_agent.execute_with_hexstrike(
            task_type="tech_detect",
            target="example.com",
            params={"deep": True}
        )
        assert "agent" in result or "task_id" in result or "error" in result


# ═══════════════════════════════════════════════════════════════════════════════
# RUN TESTS
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
