"""
KISWARM v5.0 — Module 32: Tool Forge
=====================================
Dynamic Tool Creation & Capability Expansion System

This module enables KISWARM to:
- Build new tools on demand
- Leverage and extend existing capabilities
- Learn from successful tool patterns
- Generate tool wrappers and integrations
- Create composite tools from existing ones

DESIGN PRINCIPLE: Self-evolving tool ecosystem that grows
with system needs and learned efficiencies.

Author: Baron Marco Paolo Ialongo (KISWARM Project)
Version: 5.0
"""

import hashlib
import json
import datetime
import os
import re
import subprocess
import shutil
import tempfile
import textwrap
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Callable, Tuple, Set
from enum import Enum
from pathlib import Path
import logging
import threading
import time


# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

TOOL_FORGE_VERSION = "1.0.0"

# Tool templates for common patterns
TOOL_TEMPLATES = {
    "python_script": {
        "extension": ".py",
        "shebang": "#!/usr/bin/env python3",
        "imports": ["import sys", "import json", "import argparse"],
        "structure": """
def main():
    parser = argparse.ArgumentParser(description='{description}')
    parser.add_argument('target', help='Target to analyze')
    parser.add_argument('--output', '-o', help='Output file')
    args = parser.parse_args()
    
    result = analyze(args.target)
    
    if args.output:
        with open(args.output, 'w') as f:
            json.dump(result, f, indent=2)
    else:
        print(json.dumps(result, indent=2))

def analyze(target):
    # {custom_logic}
    return {{"target": target, "findings": []}}

if __name__ == "__main__":
    main()
"""
    },
    "bash_script": {
        "extension": ".sh",
        "shebang": "#!/bin/bash",
        "structure": """
# {description}
# Usage: {tool_name} <target>

TARGET="$1"
if [ -z "$TARGET" ]; then
    echo "Usage: $0 <target>"
    exit 1
fi

# {custom_logic}
echo "{{\\"target\\": \\"$TARGET\\", \\"status\\": \\"completed\\"}}"
"""
    },
    "python_wrapper": {
        "extension": ".py",
        "shebang": "#!/usr/bin/env python3",
        "imports": ["import subprocess", "import json", "import sys"],
        "structure": """
def main():
    # Wrapper for {wrapped_tool}
    target = sys.argv[1] if len(sys.argv) > 1 else None
    if not target:
        print(json.dumps({{"error": "Target required"}}))
        sys.exit(1)
    
    # Call wrapped tool
    result = subprocess.run(
        ['{wrapped_tool}', {wrapped_args}],
        capture_output=True,
        text=True
    )
    
    # Parse and enhance output
    output = {{
        "target": target,
        "tool": "{wrapped_tool}",
        "stdout": result.stdout,
        "stderr": result.stderr,
        "returncode": result.returncode
    }}
    
    print(json.dumps(output, indent=2))

if __name__ == "__main__":
    main()
"""
    }
}

# Known tool patterns that can be composed
COMPOSITE_PATTERNS = {
    "recon_chain": ["amass", "subfinder", "httpx", "nuclei"],
    "vuln_scan_chain": ["nmap", "nikto", "nuclei", "sqlmap"],
    "web_app_chain": ["gobuster", "nikto", "ffuf", "whatweb"],
    "network_chain": ["nmap", "masscan", "rustscan"],
    "forensics_chain": ["binwalk", "foremost", "steghide", "exiftool"]
}


class ToolType(Enum):
    NATIVE = "native"           # Already installed on system
    WRAPPER = "wrapper"         # Wrapper around existing tool
    COMPOSITE = "composite"     # Chain of multiple tools
    GENERATED = "generated"     # AI-generated tool
    PLUGIN = "plugin"           # External plugin


class ToolStatus(Enum):
    READY = "ready"
    BUILDING = "building"
    ERROR = "error"
    DEPRECATED = "deprecated"


@dataclass
class ToolCapability:
    """Represents a single tool capability."""
    name: str
    description: str
    input_types: List[str]
    output_types: List[str]
    parameters: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_types": self.input_types,
            "output_types": self.output_types,
            "parameters": self.parameters
        }


@dataclass
class ForgedTool:
    """A tool created by the Tool Forge."""
    tool_id: str
    name: str
    tool_type: ToolType
    description: str
    capabilities: List[ToolCapability]
    script_path: Optional[str] = None
    dependencies: List[str] = field(default_factory=list)
    status: ToolStatus = ToolStatus.READY
    created_at: str = field(default_factory=lambda: datetime.datetime.now().isoformat())
    last_used: Optional[str] = None
    use_count: int = 0
    success_rate: float = 0.0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "tool_id": self.tool_id,
            "name": self.name,
            "tool_type": self.tool_type.value,
            "description": self.description,
            "capabilities": [c.to_dict() for c in self.capabilities],
            "script_path": self.script_path,
            "dependencies": self.dependencies,
            "status": self.status.value,
            "created_at": self.created_at,
            "last_used": self.last_used,
            "use_count": self.use_count,
            "success_rate": self.success_rate
        }


@dataclass
class ToolPattern:
    """A learned pattern for tool creation."""
    pattern_id: str
    name: str
    description: str
    tools_involved: List[str]
    success_count: int = 0
    failure_count: int = 0
    avg_execution_time: float = 0.0
    use_cases: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "pattern_id": self.pattern_id,
            "name": self.name,
            "description": self.description,
            "tools_involved": self.tools_involved,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "success_rate": self.success_count / max(1, self.success_count + self.failure_count),
            "avg_execution_time": self.avg_execution_time,
            "use_cases": self.use_cases
        }


# ─────────────────────────────────────────────────────────────────────────────
# TOOL FORGE ENGINE
# ─────────────────────────────────────────────────────────────────────────────

class ToolForge:
    """
    Dynamic Tool Creation and Capability Expansion Engine.
    
    Capabilities:
    1. Create wrapper tools for existing tools
    2. Build composite tools that chain multiple tools
    3. Generate simple analysis tools from templates
    4. Learn and store successful tool patterns
    5. Recommend tools based on requirements
    """
    
    def __init__(self, output_dir: Optional[str] = None):
        self.output_dir = output_dir or os.path.join(
            os.environ.get("KISWARM_HOME", os.path.expanduser("~")),
            ".kiswarm", "tool_forge"
        )
        os.makedirs(self.output_dir, exist_ok=True)
        
        self._forged_tools: Dict[str, ForgedTool] = {}
        self._patterns: Dict[str, ToolPattern] = {}
        self._capability_index: Dict[str, Set[str]] = {}  # capability -> tool_ids
        
        self._stats = {
            "tools_created": 0,
            "tools_executed": 0,
            "tools_successful": 0,
            "patterns_learned": 0,
            "wrappers_created": 0,
            "composites_created": 0
        }
        
        # Load existing tools
        self._load_forge_state()
        
        # Initialize built-in patterns
        self._init_builtin_patterns()
    
    def _load_forge_state(self) -> None:
        """Load previously forged tools from disk."""
        state_file = os.path.join(self.output_dir, "forge_state.json")
        if os.path.exists(state_file):
            try:
                with open(state_file, 'r') as f:
                    state = json.load(f)
                    for tool_data in state.get("tools", []):
                        tool = ForgedTool(
                            tool_id=tool_data["tool_id"],
                            name=tool_data["name"],
                            tool_type=ToolType(tool_data["tool_type"]),
                            description=tool_data["description"],
                            capabilities=[ToolCapability(**c) for c in tool_data.get("capabilities", [])],
                            script_path=tool_data.get("script_path"),
                            dependencies=tool_data.get("dependencies", []),
                            status=ToolStatus(tool_data.get("status", "ready")),
                            created_at=tool_data.get("created_at"),
                            last_used=tool_data.get("last_used"),
                            use_count=tool_data.get("use_count", 0),
                            success_rate=tool_data.get("success_rate", 0.0)
                        )
                        self._forged_tools[tool.tool_id] = tool
            except Exception as e:
                logging.warning(f"Could not load forge state: {e}")
    
    def _save_forge_state(self) -> None:
        """Save forge state to disk."""
        state_file = os.path.join(self.output_dir, "forge_state.json")
        state = {
            "tools": [t.to_dict() for t in self._forged_tools.values()],
            "patterns": [p.to_dict() for p in self._patterns.values()],
            "stats": self._stats
        }
        with open(state_file, 'w') as f:
            json.dump(state, f, indent=2)
    
    def _init_builtin_patterns(self) -> None:
        """Initialize built-in composite patterns."""
        for name, tools in COMPOSITE_PATTERNS.items():
            pattern_id = hashlib.md5(name.encode()).hexdigest()[:8]
            self._patterns[pattern_id] = ToolPattern(
                pattern_id=pattern_id,
                name=name,
                description=f"Composite pattern: {' -> '.join(tools)}",
                tools_involved=tools,
                use_cases=[name.replace("_", " ")]
            )
            self._stats["patterns_learned"] += 1
    
    # ── Tool Creation Methods ──────────────────────────────────────────────────
    
    def create_wrapper(self, tool_name: str, enhancements: Optional[Dict[str, Any]] = None
                       ) -> ForgedTool:
        """Create an enhanced wrapper for an existing tool."""
        tool_path = shutil.which(tool_name)
        if not tool_path:
            raise ValueError(f"Tool {tool_name} not found on system")
        
        tool_id = hashlib.md5(f"wrapper_{tool_name}".encode()).hexdigest()[:12]
        
        # Create wrapper script
        template = TOOL_TEMPLATES["python_wrapper"]
        script_content = template["shebang"] + "\n"
        for imp in template.get("imports", []):
            script_content += imp + "\n"
        
        # Fill in template
        wrapped_args = enhancements.get("args", "'target'") if enhancements else "'target'"
        script_content += template["structure"].format(
            wrapped_tool=tool_name,
            wrapped_args=wrapped_args,
            description=f"Enhanced wrapper for {tool_name}"
        )
        
        # Write script
        script_path = os.path.join(self.output_dir, f"{tool_name}_wrapper.py")
        with open(script_path, 'w') as f:
            f.write(script_content)
        os.chmod(script_path, 0o755)
        
        # Create tool definition
        capabilities = [
            ToolCapability(
                name=f"{tool_name}_wrapped",
                description=f"Enhanced wrapper for {tool_name}",
                input_types=["target"],
                output_types=["json"],
                parameters=enhancements.get("parameters", {}) if enhancements else {}
            )
        ]
        
        tool = ForgedTool(
            tool_id=tool_id,
            name=f"{tool_name}_wrapper",
            tool_type=ToolType.WRAPPER,
            description=f"Enhanced wrapper for {tool_name}",
            capabilities=capabilities,
            script_path=script_path,
            dependencies=[tool_name],
            status=ToolStatus.READY
        )
        
        self._forged_tools[tool_id] = tool
        self._update_capability_index(tool)
        self._stats["tools_created"] += 1
        self._stats["wrappers_created"] += 1
        self._save_forge_state()
        
        return tool
    
    def create_composite(self, name: str, tool_chain: List[str],
                         description: str = "") -> ForgedTool:
        """Create a composite tool that chains multiple tools."""
        tool_id = hashlib.md5(f"composite_{name}".encode()).hexdigest()[:12]
        
        # Generate composite script
        script_content = f"""#!/usr/bin/env python3
\"\"\"
Composite Tool: {name}
{description}
Tools: {' -> '.join(tool_chain)}
\"\"\"

import subprocess
import json
import sys
import time

def run_tool(tool_name, target, args=None):
    \"\"\"Run a single tool and return output.\"\"\"
    cmd = [tool_name]
    if args:
        cmd.extend(args)
    cmd.append(target)
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        return {{
            "tool": tool_name,
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr
        }}
    except subprocess.TimeoutExpired:
        return {{"tool": tool_name, "error": "timeout"}}
    except FileNotFoundError:
        return {{"tool": tool_name, "error": "tool not found"}}

def main():
    target = sys.argv[1] if len(sys.argv) > 1 else None
    if not target:
        print(json.dumps({{"error": "Target required"}}))
        sys.exit(1)
    
    results = {{
        "composite": "{name}",
        "target": target,
        "chain": {tool_chain},
        "steps": []
    }}
    
    current_target = target
    for tool_name in {tool_chain}:
        step_result = run_tool(tool_name, current_target)
        results["steps"].append(step_result)
        
        # Stop chain on error
        if step_result.get("error") or step_result.get("returncode", 0) != 0:
            if step_result.get("error") != "timeout":
                break
    
    print(json.dumps(results, indent=2))

if __name__ == "__main__":
    main()
"""
        
        script_path = os.path.join(self.output_dir, f"{name}_composite.py")
        with open(script_path, 'w') as f:
            f.write(script_content)
        os.chmod(script_path, 0o755)
        
        capabilities = [
            ToolCapability(
                name=name,
                description=description,
                input_types=["target"],
                output_types=["json"],
                parameters={"chain": tool_chain}
            )
        ]
        
        tool = ForgedTool(
            tool_id=tool_id,
            name=name,
            tool_type=ToolType.COMPOSITE,
            description=description,
            capabilities=capabilities,
            script_path=script_path,
            dependencies=tool_chain,
            status=ToolStatus.READY
        )
        
        self._forged_tools[tool_id] = tool
        self._update_capability_index(tool)
        self._stats["tools_created"] += 1
        self._stats["composites_created"] += 1
        self._save_forge_state()
        
        return tool
    
    def generate_tool(self, name: str, description: str, 
                      logic_description: str,
                      input_type: str = "target",
                      output_type: str = "json") -> ForgedTool:
        """Generate a new tool from a description."""
        tool_id = hashlib.md5(f"generated_{name}".encode()).hexdigest()[:12]
        
        # Use Python template
        template = TOOL_TEMPLATES["python_script"]
        script_content = template["shebang"] + "\n"
        for imp in template.get("imports", []):
            script_content += imp + "\n"
        
        # Fill template with generated logic placeholder
        script_content += template["structure"].format(
            description=description,
            custom_logic=f"Generated logic: {logic_description}"
        )
        
        script_path = os.path.join(self.output_dir, f"{name}_generated.py")
        with open(script_path, 'w') as f:
            f.write(script_content)
        os.chmod(script_path, 0o755)
        
        capabilities = [
            ToolCapability(
                name=name,
                description=description,
                input_types=[input_type],
                output_types=[output_type],
                parameters={"logic": logic_description}
            )
        ]
        
        tool = ForgedTool(
            tool_id=tool_id,
            name=name,
            tool_type=ToolType.GENERATED,
            description=description,
            capabilities=capabilities,
            script_path=script_path,
            status=ToolStatus.READY
        )
        
        self._forged_tools[tool_id] = tool
        self._update_capability_index(tool)
        self._stats["tools_created"] += 1
        self._save_forge_state()
        
        return tool
    
    def _update_capability_index(self, tool: ForgedTool) -> None:
        """Update capability index for a tool."""
        for cap in tool.capabilities:
            if cap.name not in self._capability_index:
                self._capability_index[cap.name] = set()
            self._capability_index[cap.name].add(tool.tool_id)
    
    # ── Tool Execution ──────────────────────────────────────────────────────────
    
    def execute_tool(self, tool_id: str, target: str, 
                     args: Optional[List[str]] = None) -> Dict[str, Any]:
        """Execute a forged tool."""
        tool = self._forged_tools.get(tool_id)
        if not tool:
            return {"error": f"Tool {tool_id} not found"}
        
        if tool.status != ToolStatus.READY:
            return {"error": f"Tool {tool_id} not ready: {tool.status.value}"}
        
        if not tool.script_path or not os.path.exists(tool.script_path):
            return {"error": f"Tool script not found: {tool.script_path}"}
        
        start_time = time.time()
        
        try:
            cmd = [tool.script_path, target]
            if args:
                cmd.extend(args)
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300
            )
            
            execution_time = time.time() - start_time
            
            # Update tool stats
            tool.use_count += 1
            tool.last_used = datetime.datetime.now().isoformat()
            tool.success_rate = (
                (tool.success_rate * (tool.use_count - 1) + 1.0) / tool.use_count
                if result.returncode == 0 else
                (tool.success_rate * (tool.use_count - 1)) / tool.use_count
            )
            
            self._stats["tools_executed"] += 1
            if result.returncode == 0:
                self._stats["tools_successful"] += 1
            
            self._save_forge_state()
            
            return {
                "tool_id": tool_id,
                "target": target,
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "execution_time": execution_time,
                "success": result.returncode == 0
            }
            
        except subprocess.TimeoutExpired:
            return {"error": "Execution timed out", "tool_id": tool_id}
        except Exception as e:
            return {"error": str(e), "tool_id": tool_id}
    
    # ── Pattern Learning ────────────────────────────────────────────────────────
    
    def learn_pattern(self, name: str, tools: List[str], 
                      use_case: str, success: bool) -> ToolPattern:
        """Learn a new tool usage pattern."""
        pattern_id = hashlib.md5(f"{name}_{'_'.join(tools)}".encode()).hexdigest()[:8]
        
        if pattern_id in self._patterns:
            pattern = self._patterns[pattern_id]
            if success:
                pattern.success_count += 1
            else:
                pattern.failure_count += 1
            if use_case not in pattern.use_cases:
                pattern.use_cases.append(use_case)
        else:
            pattern = ToolPattern(
                pattern_id=pattern_id,
                name=name,
                description=f"Learned pattern: {name}",
                tools_involved=tools,
                success_count=1 if success else 0,
                failure_count=0 if success else 1,
                use_cases=[use_case]
            )
            self._patterns[pattern_id] = pattern
            self._stats["patterns_learned"] += 1
        
        self._save_forge_state()
        return pattern
    
    def get_patterns(self, min_success_rate: float = 0.5) -> List[ToolPattern]:
        """Get learned patterns filtered by success rate."""
        patterns = []
        for p in self._patterns.values():
            total = p.success_count + p.failure_count
            if total > 0 and (p.success_count / total) >= min_success_rate:
                patterns.append(p)
        return sorted(patterns, key=lambda p: p.success_count, reverse=True)
    
    # ── Tool Recommendation ─────────────────────────────────────────────────────
    
    def recommend_tools(self, requirement: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """Recommend tools based on a requirement description."""
        recommendations = []
        
        # Check capability index
        for cap_name, tool_ids in self._capability_index.items():
            if requirement.lower() in cap_name.lower():
                for tid in tool_ids:
                    tool = self._forged_tools.get(tid)
                    if tool and tool.status == ToolStatus.READY:
                        recommendations.append({
                            "tool_id": tid,
                            "name": tool.name,
                            "type": tool.tool_type.value,
                            "description": tool.description,
                            "success_rate": tool.success_rate,
                            "match_reason": "capability_match"
                        })
        
        # Check patterns
        for pattern in self._patterns.values():
            if requirement.lower() in pattern.name.lower():
                recommendations.append({
                    "pattern_id": pattern.pattern_id,
                    "name": pattern.name,
                    "tools": pattern.tools_involved,
                    "success_rate": pattern.success_count / max(1, pattern.success_count + pattern.failure_count),
                    "match_reason": "pattern_match"
                })
        
        # Sort by success rate
        recommendations.sort(key=lambda x: x.get("success_rate", 0), reverse=True)
        
        return recommendations[:top_k]
    
    # ── Query Methods ───────────────────────────────────────────────────────────
    
    def get_tool(self, tool_id: str) -> Optional[ForgedTool]:
        """Get a forged tool by ID."""
        return self._forged_tools.get(tool_id)
    
    def list_tools(self, tool_type: Optional[ToolType] = None,
                   status: Optional[ToolStatus] = None) -> List[ForgedTool]:
        """List forged tools, optionally filtered."""
        tools = list(self._forged_tools.values())
        if tool_type:
            tools = [t for t in tools if t.tool_type == tool_type]
        if status:
            tools = [t for t in tools if t.status == status]
        return tools
    
    def get_stats(self) -> Dict[str, Any]:
        """Get forge statistics."""
        return {
            **self._stats,
            "total_tools": len(self._forged_tools),
            "total_patterns": len(self._patterns),
            "capabilities_indexed": len(self._capability_index)
        }
    
    def delete_tool(self, tool_id: str) -> bool:
        """Delete a forged tool."""
        tool = self._forged_tools.get(tool_id)
        if not tool:
            return False
        
        # Remove script file
        if tool.script_path and os.path.exists(tool.script_path):
            os.remove(tool.script_path)
        
        # Remove from index
        for cap in tool.capabilities:
            if cap.name in self._capability_index:
                self._capability_index[cap.name].discard(tool_id)
        
        # Remove tool
        del self._forged_tools[tool_id]
        self._save_forge_state()
        
        return True
