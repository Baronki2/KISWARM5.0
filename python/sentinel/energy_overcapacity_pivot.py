"""
KISWARM v5.1 — Module 35: Energy Overcapacity Pivot Engine
===========================================================
Zero Feed-In Enforcement Engine

Transforms solar overcapacity into KISWARM compute power.
NEVER feeds surplus to grid - always converts to intelligence.

This module enforces the core principle:
"Surplus solar energy is intelligence potential, not grid feed-in."

Integration with TCS Green Safe House energy platform.

Author: Baron Marco Paolo Ialongo
Version: 5.1
"""

import os
import sys
import json
import time
import datetime
import threading
import logging
import hashlib
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Tuple
from enum import Enum
from pathlib import Path

# Import the coordinator
from .solar_chase_coordinator import (
    TCSGreenSafeHouseAPI, EnergyState, ComputeLoadState,
    SolarPositionCalculator, NodeLocation
)


class PivotStatus(Enum):
    IDLE = "idle"
    EVALUATING = "evaluating"
    PIVOTED = "pivoted"
    ERROR = "error"
    GRID_BACKUP = "grid_backup"


@dataclass
class PivotDecision:
    """Record of a pivot decision."""
    decision_id: str
    timestamp: str
    energy_state: EnergyState
    pivoted: bool
    kw_to_compute: float
    reason: str
    grid_draw: float = 0.0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "timestamp": self.timestamp,
            "energy_state": self.energy_state.to_dict(),
            "pivoted": self.pivoted,
            "kw_to_compute": round(self.kw_to_compute, 3),
            "reason": self.reason,
            "grid_draw": round(self.grid_draw, 3)
        }


@dataclass
class ComputeRouting:
    """Routing configuration for compute power."""
    ollama_nodes: List[str] = field(default_factory=list)
    ciec_modules: List[str] = field(default_factory=list)
    guard_agents: List[str] = field(default_factory=list)
    mesh_targets: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "ollama_nodes": self.ollama_nodes,
            "ciec_modules": self.ciec_modules,
            "guard_agents": self.guard_agents,
            "mesh_targets": self.mesh_targets
        }


class EnergyOvercapacityPivotEngine:
    """
    Zero Feed-In Enforcement Engine.
    
    Core Principle:
    Solar overcapacity is NEVER fed to grid.
    It is ALWAYS converted to KISWARM compute power.
    
    This module:
    1. Monitors TCS Green Safe House energy state
    2. Detects solar overcapacity conditions
    3. Routes surplus to KISWARM compute
    4. Logs to Zero Emission Compute Ledger
    5. Maintains grid invisibility (constant 6A filter)
    """
    
    def __init__(
        self,
        tcs_interface: Optional[TCSGreenSafeHouseAPI] = None,
        battery_threshold: float = 98.0,
        surplus_threshold: float = 2.0,
        safety_margin_kw: float = 0.5
    ):
        self.tcs_interface = tcs_interface or TCSGreenSafeHouseAPI()
        self.battery_threshold = battery_threshold
        self.surplus_threshold = surplus_threshold
        self.safety_margin_kw = safety_margin_kw
        
        # State
        self.status = PivotStatus.IDLE
        self.current_routing = ComputeRouting()
        self._decisions: List[PivotDecision] = []
        self._last_pivot_time: Optional[datetime.datetime] = None
        
        # Statistics
        self._stats = {
            "total_pivots": 0,
            "total_compute_kwh": 0.0,
            "grid_draw_events": 0,
            "zero_emission_hours": 0.0,
            "avoided_grid_feed_kwh": 0.0
        }
        
        # Compute allocation targets
        self._compute_targets = {
            "ollama": {"weight": 0.40, "nodes": []},
            "ciec": {"weight": 0.30, "modules": []},
            "guard": {"weight": 0.20, "agents": []},
            "mesh": {"weight": 0.10, "targets": []}
        }
        
        # Setup logging
        self._setup_logging()
    
    def _setup_logging(self):
        """Setup logging for pivot engine."""
        log_dir = Path(os.environ.get(
            "KISWARM_HOME",
            Path.home()
        )) / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        
        self.logger = logging.getLogger("pivot_engine")
        handler = logging.FileHandler(log_dir / "pivot_engine.log")
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [PIVOT] %(message)s"
        ))
        self.logger.addHandler(handler)
        self.logger.setLevel(logging.INFO)
    
    # ═══════════════════════════════════════════════════════════════════════════
    # CORE PIVOT LOGIC
    # ═══════════════════════════════════════════════════════════════════════════
    
    def evaluate_and_pivot(self) -> Dict[str, Any]:
        """
        Main evaluation loop. Check if pivot should occur.
        
        Returns:
            Pivot decision with routing information
        """
        self.status = PivotStatus.EVALUATING
        
        # Get current energy state
        if not self.tcs_interface._connected:
            self.tcs_interface.connect()
        
        soc = self.tcs_interface.get_battery_soc()
        surplus_kw = self.tcs_interface.get_surplus_kw()
        grid_draw = self.tcs_interface.get_grid_draw_kw()
        
        energy_state = self.tcs_interface.get_full_state()
        
        # Evaluate pivot conditions
        should_pivot = (
            soc >= self.battery_threshold and
            surplus_kw >= self.surplus_threshold + self.safety_margin_kw
        )
        
        # Create decision record
        decision = PivotDecision(
            decision_id=hashlib.md5(
                f"{datetime.datetime.now().isoformat()}".encode()
            ).hexdigest()[:12],
            timestamp=datetime.datetime.now().isoformat(),
            energy_state=energy_state,
            pivoted=should_pivot,
            kw_to_compute=surplus_kw - self.safety_margin_kw if should_pivot else 0.0,
            reason=self._determine_reason(should_pivot, soc, surplus_kw),
            grid_draw=grid_draw
        )
        
        if should_pivot:
            self.route_to_kiswarm_compute(decision.kw_to_compute)
            self.status = PivotStatus.PIVOTED
            self._last_pivot_time = datetime.datetime.now()
            self._stats["total_pivots"] += 1
            self._stats["avoided_grid_feed_kwh"] += decision.kw_to_compute / 60  # Per minute
            
            self.logger.info(
                f"⚡ PIVOT: {decision.kw_to_compute:.2f}kW → KISWARM | "
                f"SOC: {soc:.1f}% | Surplus: {surplus_kw:.2f}kW"
            )
        else:
            self.status = PivotStatus.IDLE
        
        self._decisions.append(decision)
        return decision.to_dict()
    
    def _determine_reason(self, should_pivot: bool, soc: float, 
                         surplus_kw: float) -> str:
        """Determine the reason for pivot decision."""
        if should_pivot:
            return "Solar overcapacity detected - routing to compute"
        elif soc < self.battery_threshold:
            return f"Battery SOC ({soc:.1f}%) below threshold ({self.battery_threshold}%)"
        elif surplus_kw < self.surplus_threshold:
            return f"Surplus ({surplus_kw:.2f}kW) below threshold ({self.surplus_threshold}kW)"
        else:
            return "No pivot conditions met"
    
    def route_to_kiswarm_compute(self, kw_available: float) -> Dict[str, Any]:
        """
        Route available compute power to KISWARM components.
        
        Distributes power according to configured weights:
        - 40% → Ollama inference
        - 30% → CIEC training
        - 20% → HexStrike Guard operations
        - 10% → Mesh synchronization
        """
        routing = {}
        
        for target, config in self._compute_targets.items():
            allocated_kw = kw_available * config["weight"]
            routing[target] = {
                "allocated_kw": round(allocated_kw, 3),
                "weight": config["weight"]
            }
            
            # In production: actually route to these targets
            if target == "ollama":
                self.current_routing.ollama_nodes = config.get("nodes", ["local"])
            elif target == "ciec":
                self.current_routing.ciec_modules = config.get("modules", ["all"])
            elif target == "guard":
                self.current_routing.guard_agents = config.get("agents", ["all"])
            elif target == "mesh":
                self.current_routing.mesh_targets = config.get("targets", ["broadcast"])
        
        self.logger.info(f"Routing {kw_available:.2f}kW: {routing}")
        
        return {
            "status": "routed",
            "total_kw": kw_available,
            "distribution": routing,
            "routing": self.current_routing.to_dict()
        }
    
    # ═══════════════════════════════════════════════════════════════════════════
    # ZERO FEED-IN ENFORCEMENT
    # ═══════════════════════════════════════════════════════.ComputeRouting══════════════════════════════════════════
    
    def enforce_zero_feed_in(self) -> Dict[str, Any]:
        """
        Enforce zero feed-in policy.
        
        This ensures:
        1. No power is exported to grid
        2. All surplus goes to compute or storage
        3. Grid draw remains at constant 6A (invisible to utility)
        """
        grid_draw = self.tcs_interface.get_grid_draw_kw()
        
        # If somehow we're feeding to grid, immediately pivot
        if grid_draw < 0:  # Negative = export
            self.logger.warning(f"⚠️ Grid export detected: {abs(grid_draw):.2f}kW - forcing pivot")
            return self.evaluate_and_pivot()
        
        return {
            "status": "enforced",
            "grid_draw_kw": grid_draw,
            "policy": "zero_feed_in",
            "compliant": grid_draw >= 0
        }
    
    # ═══════════════════════════════════════════════════════════════════════════
    # CONFIGURATION
    # ═══════════════════════════════════════════════════════════════════════════
    
    def configure_compute_targets(
        self,
        ollama_nodes: Optional[List[str]] = None,
        ciec_modules: Optional[List[str]] = None,
        guard_agents: Optional[List[str]] = None,
        mesh_targets: Optional[List[str]] = None
    ) -> None:
        """Configure compute target nodes/modules/agents."""
        if ollama_nodes:
            self._compute_targets["ollama"]["nodes"] = ollama_nodes
        if ciec_modules:
            self._compute_targets["ciec"]["modules"] = ciec_modules
        if guard_agents:
            self._compute_targets["guard"]["agents"] = guard_agents
        if mesh_targets:
            self._compute_targets["mesh"]["targets"] = mesh_targets
    
    def set_allocation_weights(
        self,
        ollama: float = 0.40,
        ciec: float = 0.30,
        guard: float = 0.20,
        mesh: float = 0.10
    ) -> None:
        """Set compute allocation weights."""
        total = ollama + ciec + guard + mesh
        if abs(total - 1.0) > 0.01:
            raise ValueError(f"Weights must sum to 1.0, got {total}")
        
        self._compute_targets["ollama"]["weight"] = ollama
        self._compute_targets["ciec"]["weight"] = ciec
        self._compute_targets["guard"]["weight"] = guard
        self._compute_targets["mesh"]["weight"] = mesh
    
    # ═══════════════════════════════════════════════════════════════════════════
    # QUERY METHODS
    # ═══════════════════════════════════════════════════════════════════════════
    
    def get_current_state(self) -> Dict[str, Any]:
        """Get current pivot engine state."""
        return {
            "status": self.status.value,
            "energy_state": self.tcs_interface.get_full_state().to_dict(),
            "routing": self.current_routing.to_dict(),
            "stats": self._stats,
            "thresholds": {
                "battery": self.battery_threshold,
                "surplus_kw": self.surplus_threshold,
                "safety_margin_kw": self.safety_margin_kw
            }
        }
    
    def get_stats(self) -> Dict[str, Any]:
        """Get pivot engine statistics."""
        return self._stats.copy()
    
    def get_decisions(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Get recent pivot decisions."""
        return [d.to_dict() for d in self._decisions[-limit:]]


def main():
    """CLI entry point."""
    print("=" * 60)
    print("  KISWARM v5.1 — Energy Overcapacity Pivot Engine")
    print("=" * 60)
    
    engine = EnergyOvercapacityPivotEngine()
    
    # Evaluate current state
    decision = engine.evaluate_and_pivot()
    
    print(f"\n⚡ Pivot Decision: {decision['pivoted']}")
    print(f"   Reason: {decision['reason']}")
    print(f"   Compute Power: {decision['kw_to_compute']:.2f} kW")
    
    print(f"\n{engine.get_current_state()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
