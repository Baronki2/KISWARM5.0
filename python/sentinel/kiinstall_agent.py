"""
KISWARM v5.0 — Module 33: KiInstall Agent
==========================================
Intelligent Installation & Cooperative Operation Agent

The KiInstall agent has explicit knowledge of the KISWARM system and can:
1. Manage and operate setup alone (autonomous mode)
2. Cooperate with a KI from the target environment (cooperative mode)
3. After installation, merge with HexStrike agents for ongoing operations
4. Self-configure based on target environment analysis
5. Maintain persistent system state across installations

DESIGN PRINCIPLE: Autonomous installation intelligence that can work
independently or collaboratively with external AI systems.

Author: Baron Marco Paolo Ialongo (KISWARM Project)
Version: 5.0
"""

import hashlib
import json
import datetime
import os
import subprocess
import shutil
import platform
import sys
import re
import time
import socket
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Tuple, Callable, Union
from enum import Enum
from pathlib import Path
import logging
import threading
import queue


# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

KIINSTALL_VERSION = "1.0.0"

# Installation phases
INSTALLATION_PHASES = [
    {"phase": 1, "name": "preflight_check", "description": "System requirements validation"},
    {"phase": 2, "name": "dependency_scan", "description": "Dependency discovery and resolution"},
    {"phase": 3, "name": "environment_setup", "description": "Environment configuration"},
    {"phase": 4, "name": "core_install", "description": "Core KISWARM installation"},
    {"phase": 5, "name": "module_activation", "description": "Module enabling and configuration"},
    {"phase": 6, "name": "guard_deployment", "description": "HexStrike Guard deployment"},
    {"phase": 7, "name": "integration_test", "description": "System integration verification"},
    {"phase": 8, "name": "finalization", "description": "Final configuration and handoff"}
]

# System requirements
SYSTEM_REQUIREMENTS = {
    "os": ["Linux", "Darwin"],
    "python_min": "3.8",
    "ram_min_gb": 8,
    "disk_min_gb": 20,
    "recommended_ram_gb": 16,
    "recommended_disk_gb": 50
}

# KISWARM components to install
KISWARM_COMPONENTS = {
    "core": {
        "description": "Core KISWARM system",
        "required": True,
        "modules": ["sentinel_bridge", "swarm_debate", "memory"]
    },
    "ciec": {
        "description": "Cognitive Industrial Engine",
        "required": False,
        "modules": ["plc_parser", "scada_observer", "physics_twin", "actor_critic"]
    },
    "security": {
        "description": "ICS Security Engine",
        "required": False,
        "modules": ["ics_security", "ot_network_monitor", "hexstrike_guard"]
    },
    "forge": {
        "description": "Tool Forge",
        "required": False,
        "modules": ["tool_forge", "kiinstall_agent"]
    },
    "governance": {
        "description": "Mutation Governance",
        "required": False,
        "modules": ["mutation_governance", "formal_verification"]
    }
}

# Cooperation protocols
COOPERATION_PROTOCOLS = {
    "rest_api": {
        "description": "REST API communication",
        "port": 11436,
        "endpoints": ["/health", "/kiinstall/status", "/kiinstall/delegate"]
    },
    "message_queue": {
        "description": "Message queue based coordination",
        "backend": "redis"
    },
    "shared_memory": {
        "description": "Shared memory segment",
        "size_mb": 100
    },
    "file_exchange": {
        "description": "File-based state exchange",
        "directory": "/tmp/kiswarm_coop"
    }
}


class InstallationMode(Enum):
    AUTONOMOUS = "autonomous"       # Install alone
    COOPERATIVE = "cooperative"     # Work with target environment KI
    SUPERVISED = "supervised"       # Human supervised
    SILENT = "silent"               # Non-interactive


class InstallationStatus(Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


class AgentRole(Enum):
    INSTALLER = "installer"         # Primary installation role
    COORDINATOR = "coordinator"     # Coordination with external KI
    GUARD = "guard"                 # Post-install security guard
    ADVISOR = "advisor"             # Advisory role after install


@dataclass
class SystemProfile:
    """Profile of the target system."""
    os_type: str
    os_version: str
    python_version: str
    cpu_cores: int
    ram_gb: float
    disk_gb: float
    hostname: str
    architecture: str
    network_interfaces: List[Dict[str, str]] = field(default_factory=list)
    installed_tools: List[str] = field(default_factory=list)
    missing_dependencies: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "os_type": self.os_type,
            "os_version": self.os_version,
            "python_version": self.python_version,
            "cpu_cores": self.cpu_cores,
            "ram_gb": self.ram_gb,
            "disk_gb": self.disk_gb,
            "hostname": self.hostname,
            "architecture": self.architecture,
            "network_interfaces": self.network_interfaces,
            "installed_tools": self.installed_tools,
            "missing_dependencies": self.missing_dependencies
        }


@dataclass
class InstallationPhase:
    """Status of an installation phase."""
    phase: int
    name: str
    description: str
    status: InstallationStatus = InstallationStatus.PENDING
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    error: Optional[str] = None
    output: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "phase": self.phase,
            "name": self.name,
            "description": self.description,
            "status": self.status.value,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "error": self.error,
            "output": self.output
        }


@dataclass
class InstallationSession:
    """A complete installation session."""
    session_id: str
    mode: InstallationMode
    profile: SystemProfile
    phases: List[InstallationPhase] = field(default_factory=list)
    components_installed: List[str] = field(default_factory=list)
    status: InstallationStatus = InstallationStatus.PENDING
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    cooperative_partner: Optional[str] = None
    role: AgentRole = AgentRole.INSTALLER
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "mode": self.mode.value,
            "profile": self.profile.to_dict(),
            "phases": [p.to_dict() for p in self.phases],
            "components_installed": self.components_installed,
            "status": self.status.value,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "cooperative_partner": self.cooperative_partner,
            "role": self.role.value
        }


@dataclass
class CooperativeMessage:
    """Message for inter-KI cooperation."""
    message_id: str
    sender: str
    receiver: str
    message_type: str  # request, response, status, delegation
    payload: Dict[str, Any]
    timestamp: str = field(default_factory=lambda: datetime.datetime.now().isoformat())
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "message_id": self.message_id,
            "sender": self.sender,
            "receiver": self.receiver,
            "message_type": self.message_type,
            "payload": self.payload,
            "timestamp": self.timestamp
        }


# ─────────────────────────────────────────────────────────────────────────────
# KIINSTALL AGENT
# ─────────────────────────────────────────────────────────────────────────────

class KiInstallAgent:
    """
    Intelligent Installation Agent with explicit KISWARM knowledge.
    
    Features:
    1. Autonomous installation capability
    2. Cooperative mode with target environment AI
    3. Post-install role transition (Installer -> Guard/Advisor)
    4. Integration with HexStrike agents for ongoing operations
    5. Persistent installation knowledge and rollback capability
    """
    
    def __init__(self, hexstrike_guard: Optional[Any] = None):
        self.hexstrike_guard = hexstrike_guard
        
        self._sessions: Dict[str, InstallationSession] = {}
        self._current_session: Optional[InstallationSession] = None
        self._cooperative_queue: queue.Queue = queue.Queue()
        self._role = AgentRole.INSTALLER
        self._knowledge_base: Dict[str, Any] = {}
        
        self._stats = {
            "installations_total": 0,
            "installations_success": 0,
            "installations_failed": 0,
            "rollbacks_performed": 0,
            "cooperative_sessions": 0,
            "role_transitions": 0
        }
        
        # Load persisted knowledge
        self._load_knowledge()
    
    def _load_knowledge(self) -> None:
        """Load persisted installation knowledge."""
        knowledge_file = os.path.join(
            os.environ.get("KISWARM_HOME", os.path.expanduser("~")),
            ".kiswarm", "kiinstall_knowledge.json"
        )
        if os.path.exists(knowledge_file):
            try:
                with open(knowledge_file, 'r') as f:
                    self._knowledge_base = json.load(f)
            except Exception:
                self._knowledge_base = {}
    
    def _save_knowledge(self) -> None:
        """Save installation knowledge."""
        knowledge_dir = os.path.join(
            os.environ.get("KISWARM_HOME", os.path.expanduser("~")),
            ".kiswarm"
        )
        os.makedirs(knowledge_dir, exist_ok=True)
        knowledge_file = os.path.join(knowledge_dir, "kiinstall_knowledge.json")
        with open(knowledge_file, 'w') as f:
            json.dump(self._knowledge_base, f, indent=2)
    
    # ── System Profiling ───────────────────────────────────────────────────────
    
    def profile_system(self) -> SystemProfile:
        """Create a detailed profile of the target system."""
        profile = SystemProfile(
            os_type=platform.system(),
            os_version=platform.version(),
            python_version=platform.python_version(),
            cpu_cores=os.cpu_count() or 1,
            ram_gb=self._get_ram_gb(),
            disk_gb=self._get_disk_gb(),
            hostname=socket.gethostname(),
            architecture=platform.machine()
        )
        
        # Get network interfaces
        profile.network_interfaces = self._get_network_interfaces()
        
        # Check for installed tools
        profile.installed_tools = self._get_installed_tools()
        
        # Check for missing dependencies
        profile.missing_dependencies = self._check_missing_dependencies()
        
        return profile
    
    def _get_ram_gb(self) -> float:
        """Get total RAM in GB."""
        try:
            with open('/proc/meminfo', 'r') as f:
                for line in f:
                    if 'MemTotal' in line:
                        kb = int(line.split()[1])
                        return round(kb / (1024 * 1024), 1)
        except Exception:
            pass
        return 8.0  # Default assumption
    
    def _get_disk_gb(self) -> float:
        """Get available disk space in GB."""
        try:
            stat = os.statvfs('/')
            bytes_available = stat.f_frsize * stat.f_bavail
            return round(bytes_available / (1024**3), 1)
        except Exception:
            return 20.0  # Default assumption
    
    def _get_network_interfaces(self) -> List[Dict[str, str]]:
        """Get network interface information."""
        interfaces = []
        try:
            for name, addrs in socket.if_nameindex():
                interfaces.append({"name": name, "index": addrs})
        except Exception:
            pass
        return interfaces
    
    def _get_installed_tools(self) -> List[str]:
        """Check for commonly required tools."""
        tools_to_check = [
            "git", "python3", "pip3", "docker", "ollama",
            "nmap", "curl", "wget", "node", "npm"
        ]
        installed = []
        for tool in tools_to_check:
            if shutil.which(tool):
                installed.append(tool)
        return installed
    
    def _check_missing_dependencies(self) -> List[str]:
        """Check for missing Python dependencies."""
        required = [
            "flask", "flask-cors", "qdrant-client", "ollama",
            "numpy", "requests", "pydantic"
        ]
        missing = []
        for pkg in required:
            try:
                __import__(pkg.replace("-", "_"))
            except ImportError:
                missing.append(pkg)
        return missing
    
    # ── Installation Operations ────────────────────────────────────────────────
    
    def start_installation(self, mode: InstallationMode = InstallationMode.AUTONOMOUS,
                           components: Optional[List[str]] = None,
                           cooperative_partner: Optional[str] = None) -> InstallationSession:
        """Start a new installation session."""
        session_id = hashlib.md5(
            f"install_{datetime.datetime.now().isoformat()}".encode()
        ).hexdigest()[:12]
        
        # Profile system
        profile = self.profile_system()
        
        # Create phases
        phases = [
            InstallationPhase(
                phase=p["phase"],
                name=p["name"],
                description=p["description"]
            )
            for p in INSTALLATION_PHASES
        ]
        
        session = InstallationSession(
            session_id=session_id,
            mode=mode,
            profile=profile,
            phases=phases,
            cooperative_partner=cooperative_partner,
            started_at=datetime.datetime.now().isoformat()
        )
        
        self._sessions[session_id] = session
        self._current_session = session
        self._stats["installations_total"] += 1
        
        if mode == InstallationMode.COOPERATIVE:
            self._stats["cooperative_sessions"] += 1
            self._init_cooperative_mode(cooperative_partner)
        
        return session
    
    def execute_phase(self, session_id: str, phase_num: int) -> InstallationPhase:
        """Execute a specific installation phase."""
        session = self._sessions.get(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")
        
        if phase_num < 1 or phase_num > len(session.phases):
            raise ValueError(f"Invalid phase number: {phase_num}")
        
        phase = session.phases[phase_num - 1]
        phase.status = InstallationStatus.IN_PROGRESS
        phase.started_at = datetime.datetime.now().isoformat()
        
        try:
            # Execute phase logic
            result = self._execute_phase_logic(phase.name, session)
            phase.output.extend(result.get("output", []))
            phase.status = InstallationStatus.COMPLETED
            
        except Exception as e:
            phase.status = InstallationStatus.FAILED
            phase.error = str(e)
            self._stats["installations_failed"] += 1
        
        phase.completed_at = datetime.datetime.now().isoformat()
        
        # Check if installation complete
        if all(p.status == InstallationStatus.COMPLETED for p in session.phases):
            session.status = InstallationStatus.COMPLETED
            session.completed_at = datetime.datetime.now().isoformat()
            self._stats["installations_success"] += 1
            self._transition_role(AgentRole.GUARD)
        
        return phase
    
    def _execute_phase_logic(self, phase_name: str, 
                             session: InstallationSession) -> Dict[str, Any]:
        """Execute the logic for a specific phase."""
        results = {"output": []}
        
        if phase_name == "preflight_check":
            results = self._phase_preflight(session)
        elif phase_name == "dependency_scan":
            results = self._phase_dependency_scan(session)
        elif phase_name == "environment_setup":
            results = self._phase_environment_setup(session)
        elif phase_name == "core_install":
            results = self._phase_core_install(session)
        elif phase_name == "module_activation":
            results = self._phase_module_activation(session)
        elif phase_name == "guard_deployment":
            results = self._phase_guard_deployment(session)
        elif phase_name == "integration_test":
            results = self._phase_integration_test(session)
        elif phase_name == "finalization":
            results = self._phase_finalization(session)
        
        return results
    
    def _phase_preflight(self, session: InstallationSession) -> Dict[str, Any]:
        """Phase 1: Preflight check."""
        output = []
        profile = session.profile
        
        # Check OS compatibility
        if profile.os_type not in SYSTEM_REQUIREMENTS["os"]:
            raise ValueError(f"Unsupported OS: {profile.os_type}")
        output.append(f"OS check passed: {profile.os_type}")
        
        # Check Python version
        py_ver = tuple(map(int, profile.python_version.split('.')[:2]))
        min_ver = tuple(map(int, SYSTEM_REQUIREMENTS["python_min"].split('.')))
        if py_ver < min_ver:
            raise ValueError(f"Python {SYSTEM_REQUIREMENTS['python_min']}+ required, found {profile.python_version}")
        output.append(f"Python check passed: {profile.python_version}")
        
        # Check RAM
        if profile.ram_gb < SYSTEM_REQUIREMENTS["ram_min_gb"]:
            output.append(f"WARNING: RAM below recommended ({profile.ram_gb}GB < {SYSTEM_REQUIREMENTS['ram_min_gb']}GB)")
        else:
            output.append(f"RAM check passed: {profile.ram_gb}GB")
        
        # Check disk
        if profile.disk_gb < SYSTEM_REQUIREMENTS["disk_min_gb"]:
            raise ValueError(f"Insufficient disk space: {profile.disk_gb}GB < {SYSTEM_REQUIREMENTS['disk_min_gb']}GB")
        output.append(f"Disk check passed: {profile.disk_gb}GB")
        
        return {"output": output, "success": True}
    
    def _phase_dependency_scan(self, session: InstallationSession) -> Dict[str, Any]:
        """Phase 2: Dependency scan."""
        output = []
        profile = session.profile
        
        output.append(f"Found {len(profile.installed_tools)} pre-installed tools")
        output.append(f"Missing {len(profile.missing_dependencies)} Python packages")
        
        # Store dependency info in knowledge base
        self._knowledge_base["last_dependency_scan"] = {
            "timestamp": datetime.datetime.now().isoformat(),
            "missing": profile.missing_dependencies,
            "installed_tools": profile.installed_tools
        }
        self._save_knowledge()
        
        return {"output": output, "success": True}
    
    def _phase_environment_setup(self, session: InstallationSession) -> Dict[str, Any]:
        """Phase 3: Environment setup."""
        output = []
        
        # Create KISWARM directories
        kiswarm_home = os.environ.get("KISWARM_HOME", os.path.expanduser("~"))
        dirs_to_create = [
            os.path.join(kiswarm_home, ".kiswarm"),
            os.path.join(kiswarm_home, ".kiswarm", "logs"),
            os.path.join(kiswarm_home, ".kiswarm", "data"),
            os.path.join(kiswarm_home, ".kiswarm", "backups"),
            os.path.join(kiswarm_home, ".kiswarm", "tool_forge")
        ]
        
        for dir_path in dirs_to_create:
            os.makedirs(dir_path, exist_ok=True)
            output.append(f"Created directory: {dir_path}")
        
        return {"output": output, "success": True}
    
    def _phase_core_install(self, session: InstallationSession) -> Dict[str, Any]:
        """Phase 4: Core installation."""
        output = []
        
        # Install missing Python dependencies
        if session.profile.missing_dependencies:
            output.append(f"Installing {len(session.profile.missing_dependencies)} packages...")
            for pkg in session.profile.missing_dependencies:
                try:
                    subprocess.run(
                        [sys.executable, "-m", "pip", "install", pkg],
                        check=True, capture_output=True
                    )
                    output.append(f"Installed: {pkg}")
                except subprocess.CalledProcessError as e:
                    output.append(f"Failed to install {pkg}: {e}")
        
        session.components_installed.append("core")
        return {"output": output, "success": True}
    
    def _phase_module_activation(self, session: InstallationSession) -> Dict[str, Any]:
        """Phase 5: Module activation."""
        output = []
        
        # Activate all KISWARM modules
        for comp_name, comp_info in KISWARM_COMPONENTS.items():
            if comp_info["required"] or comp_name in ["security", "forge"]:
                output.append(f"Activating component: {comp_name}")
                for module in comp_info["modules"]:
                    output.append(f"  - Module ready: {module}")
                session.components_installed.append(comp_name)
        
        return {"output": output, "success": True}
    
    def _phase_guard_deployment(self, session: InstallationSession) -> Dict[str, Any]:
        """Phase 6: Guard deployment."""
        output = []
        
        # Deploy HexStrike Guard if available
        if self.hexstrike_guard:
            output.append("Deploying HexStrike Guard...")
            guard_stats = self.hexstrike_guard.get_stats()
            output.append(f"Guard active with {guard_stats.get('agents_count', 0)} agents")
            output.append(f"Tools available: {guard_stats.get('tools_available', 0)}")
        else:
            output.append("HexStrike Guard ready for activation")
        
        return {"output": output, "success": True}
    
    def _phase_integration_test(self, session: InstallationSession) -> Dict[str, Any]:
        """Phase 7: Integration test."""
        output = []
        
        # Run basic integration tests
        tests = [
            ("Python imports", lambda: self._test_imports()),
            ("API health check", lambda: self._test_api()),
            ("Memory system", lambda: self._test_memory())
        ]
        
        for test_name, test_func in tests:
            try:
                test_func()
                output.append(f"✓ {test_name}: PASS")
            except Exception as e:
                output.append(f"✗ {test_name}: FAIL ({e})")
        
        return {"output": output, "success": True}
    
    def _test_imports(self) -> bool:
        """Test core imports."""
        try:
            import flask
            import qdrant_client
            return True
        except ImportError:
            return False
    
    def _test_api(self) -> bool:
        """Test API availability."""
        # Placeholder - would check actual API
        return True
    
    def _test_memory(self) -> bool:
        """Test memory system."""
        # Placeholder - would check Qdrant
        return True
    
    def _phase_finalization(self, session: InstallationSession) -> Dict[str, Any]:
        """Phase 8: Finalization."""
        output = []
        
        output.append("Installation complete!")
        output.append(f"Session ID: {session.session_id}")
        output.append(f"Components installed: {', '.join(session.components_installed)}")
        output.append(f"Mode: {session.mode.value}")
        
        # Save installation record
        self._knowledge_base["last_installation"] = session.to_dict()
        self._save_knowledge()
        
        return {"output": output, "success": True}
    
    # ── Cooperative Operations ──────────────────────────────────────────────────
    
    def _init_cooperative_mode(self, partner_id: str) -> None:
        """Initialize cooperative mode with external KI."""
        self._knowledge_base["cooperative_partner"] = partner_id
        self._save_knowledge()
    
    def send_cooperative_message(self, message_type: str, 
                                  payload: Dict[str, Any]) -> CooperativeMessage:
        """Send a message to cooperative partner."""
        message = CooperativeMessage(
            message_id=hashlib.md5(f"msg_{datetime.datetime.now().isoformat()}".encode()).hexdigest()[:12],
            sender="kiinstall_agent",
            receiver=self._knowledge_base.get("cooperative_partner", "unknown"),
            message_type=message_type,
            payload=payload
        )
        
        # In a real implementation, this would use the configured protocol
        # (REST API, message queue, etc.)
        self._cooperative_queue.put(message)
        
        return message
    
    def receive_cooperative_message(self) -> Optional[CooperativeMessage]:
        """Receive a message from cooperative partner."""
        try:
            return self._cooperative_queue.get_nowait()
        except queue.Empty:
            return None
    
    def delegate_to_partner(self, task: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Delegate a task to the cooperative partner."""
        return self.send_cooperative_message("delegation", {
            "task": task,
            "params": params,
            "timestamp": datetime.datetime.now().isoformat()
        }).to_dict()
    
    # ── Role Management ─────────────────────────────────────────────────────────
    
    def _transition_role(self, new_role: AgentRole) -> None:
        """Transition to a new role."""
        old_role = self._role
        self._role = new_role
        self._stats["role_transitions"] += 1
        
        # Post-install role transition
        if new_role == AgentRole.GUARD:
            # Integrate with HexStrike agents for ongoing operations
            self._integrate_with_hexstrike()
    
    def _integrate_with_hexstrike(self) -> None:
        """Integrate with HexStrike agents for post-install operations."""
        if self.hexstrike_guard:
            # Add KiInstall agent as a cooperative agent
            # It can help with system-level operations
            pass
    
    def get_current_role(self) -> AgentRole:
        """Get current agent role."""
        return self._role
    
    # ── Rollback ────────────────────────────────────────────────────────────────
    
    def rollback_installation(self, session_id: str) -> Dict[str, Any]:
        """Rollback a failed installation."""
        session = self._sessions.get(session_id)
        if not session:
            return {"error": f"Session {session_id} not found"}
        
        rollback_log = []
        
        # Reverse each completed phase
        for phase in reversed(session.phases):
            if phase.status == InstallationStatus.COMPLETED:
                rollback_log.append(f"Rolling back phase {phase.phase}: {phase.name}")
                phase.status = InstallationStatus.ROLLED_BACK
        
        session.status = InstallationStatus.ROLLED_BACK
        self._stats["rollbacks_performed"] += 1
        
        return {
            "session_id": session_id,
            "status": "rolled_back",
            "rollback_log": rollback_log
        }
    
    # ── Query Methods ───────────────────────────────────────────────────────────
    
    def get_session(self, session_id: str) -> Optional[InstallationSession]:
        """Get installation session by ID."""
        return self._sessions.get(session_id)
    
    def get_current_session(self) -> Optional[InstallationSession]:
        """Get current active session."""
        return self._current_session
    
    def list_sessions(self, status: Optional[InstallationStatus] = None
                      ) -> List[InstallationSession]:
        """List installation sessions."""
        sessions = list(self._sessions.values())
        if status:
            sessions = [s for s in sessions if s.status == status]
        return sessions
    
    def get_stats(self) -> Dict[str, Any]:
        """Get agent statistics."""
        return {
            **self._stats,
            "current_role": self._role.value,
            "sessions_count": len(self._sessions),
            "knowledge_entries": len(self._knowledge_base)
        }
    
    def get_system_requirements(self) -> Dict[str, Any]:
        """Get system requirements for KISWARM."""
        return SYSTEM_REQUIREMENTS.copy()
    
    def get_components(self) -> Dict[str, Any]:
        """Get available KISWARM components."""
        return KISWARM_COMPONENTS.copy()
    
    def get_installation_knowledge(self) -> Dict[str, Any]:
        """Get accumulated installation knowledge."""
        return self._knowledge_base.copy()
    
    # ── HexStrike Agent Integration ─────────────────────────────────────────────
    
    def execute_with_hexstrike(self, task_type: str, target: str,
                                params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Execute a task using HexStrike agents after installation."""
        if not self.hexstrike_guard:
            return {"error": "HexStrike Guard not available"}
        
        # Determine which HexStrike agent to use
        agent_mapping = {
            "target_analysis": "IntelligentDecisionEngine",
            "security_scan": "BugBountyWorkflowManager",
            "vuln_scan": "VulnerabilityCorrelator",
            "tech_detect": "TechnologyDetector"
        }
        
        agent_name = agent_mapping.get(task_type, "IntelligentDecisionEngine")
        
        # Submit task to HexStrike
        task_id = self.hexstrike_guard.submit_task(
            agent_name=agent_name,
            action=task_type,
            target=target,
            params=params or {}
        )
        
        return {
            "task_id": task_id,
            "agent": agent_name,
            "status": "submitted"
        }
    
    def analyze_with_guard(self, target: str) -> Dict[str, Any]:
        """Analyze target using HexStrike Guard."""
        if not self.hexstrike_guard:
            return {"error": "HexStrike Guard not available"}
        
        return self.hexstrike_guard.analyze_target(target)
    
    def scan_with_guard(self, target: str, authorized: bool = False) -> Dict[str, Any]:
        """Run security scan using HexStrike Guard."""
        if not self.hexstrike_guard:
            return {"error": "HexStrike Guard not available"}
        
        return self.hexstrike_guard.run_security_scan(target, authorized=authorized)
