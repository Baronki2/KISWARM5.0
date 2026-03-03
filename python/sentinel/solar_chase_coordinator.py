"""
KISWARM v5.1 — Module 34: Solar Chase Coordinator
==================================================
Planetary Sun-Following Compute Orchestrator

The core orchestrator that transforms KISWARM into a planetary-scale
sun-following compute infrastructure. When solar overcapacity is detected,
compute activates instead of feeding back to the grid.

Core Principle: "Compute follows the sun, not the other way around."

This implements the TCS Green Safe House Zero Feed-In architecture
for true carbon-neutral AI operations.

Author: Baron Marco Paolo Ialongo
Version: 5.1 (Planetary Machine Release)
"""

import os
import sys
import json
import time
import math
import datetime
import threading
import logging
import hashlib
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Tuple, Callable
from enum import Enum
from pathlib import Path
import socket
import random

# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

SOLAR_CHASE_VERSION = "1.0.0"

# Default thresholds
DEFAULT_ENERGY_THRESHOLD = 98.0        # % battery full before pivot
DEFAULT_SURPLUS_THRESHOLD = 2.0       # kW minimum surplus to trigger compute
DEFAULT_COMPUTE_ALLOCATION = 0.9     # 90% of surplus goes to compute

# Sun position calculation constants
SOLAR_CONSTANT = 1361                 # W/m² at Earth's distance
EARTH_AXIAL_TILT = 23.44             # degrees

# Latitude bands for sun-following optimization
LATITUDE_BANDS = {
    "polar_north": (66.5, 90),
    "temperate_north": (23.5, 66.5),
    "tropical": (-23.5, 23.5),
    "temperate_south": (-66.5, -23.5),
    "polar_south": (-90, -66.5)
}


class ComputeMode(Enum):
    DORMANT = "dormant"           # Waiting for solar overcapacity
    ACTIVE = "active"            # Running on solar surplus
    HANDOFF = "handoff"          # Migrating to sunlit node
    GRID_BACKUP = "grid_backup"  # Emergency grid power (avoid if possible)
    SLEEP = "sleep"              # Night mode, no compute


class SolarStatus(Enum):
    NIGHT = "night"
    TWILIGHT = "twilight"
    SUNRISE = "sunrise"
    DAY = "day"
    SUNSET = "sunset"
    OVERCAPACITY = "overcapacity"


class HandoffState(Enum):
    IDLE = "idle"
    SEARCHING = "searching"
    NEGOTIATING = "negotiating"
    MIGRATING = "migrating"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class SolarPosition:
    """Solar position data for a specific time and location."""
    azimuth: float              # degrees from north
    elevation: float            # degrees above horizon
    solar_flux: float           # W/m² at surface
    is_daylight: bool
    sunrise_time: Optional[datetime.datetime] = None
    sunset_time: Optional[datetime.datetime] = None
    day_length_hours: float = 0.0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "azimuth": round(self.azimuth, 2),
            "elevation": round(self.elevation, 2),
            "solar_flux": round(self.solar_flux, 2),
            "is_daylight": self.is_daylight,
            "sunrise_time": self.sunrise_time.isoformat() if self.sunrise_time else None,
            "sunset_time": self.sunset_time.isoformat() if self.sunset_time else None,
            "day_length_hours": round(self.day_length_hours, 2)
        }


@dataclass
class EnergyState:
    """Current energy state of the TCS Green Safe House."""
    battery_soc: float           # State of charge (0-100%)
    solar_input_kw: float        # Current solar generation
    load_kw: float               # Current load consumption
    grid_draw_kw: float          # Power drawn from grid
    surplus_kw: float            # Available surplus for compute
    supercap_voltage: float      # Supercapacitor bank voltage
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "battery_soc": round(self.battery_soc, 2),
            "solar_input_kw": round(self.solar_input_kw, 3),
            "load_kw": round(self.load_kw, 3),
            "grid_draw_kw": round(self.grid_draw_kw, 3),
            "surplus_kw": round(self.surplus_kw, 3),
            "supercap_voltage": round(self.supercap_voltage, 2)
        }


@dataclass
class ComputeLoadState:
    """Current compute load allocation."""
    ollama_inference_kw: float   # Power for Ollama inference
    ciec_training_kw: float      # Power for CIEC training
    guard_operations_kw: float   # Power for HexStrike Guard
    mesh_sync_kw: float          # Power for mesh synchronization
    total_compute_kw: float      # Total compute power allocation
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "ollama_inference_kw": round(self.ollama_inference_kw, 3),
            "ciec_training_kw": round(self.ciec_training_kw, 3),
            "guard_operations_kw": round(self.guard_operations_kw, 3),
            "mesh_sync_kw": round(self.mesh_sync_kw, 3),
            "total_compute_kw": round(self.total_compute_kw, 3)
        }


@dataclass
class NodeLocation:
    """Geographic location of a KISWARM node."""
    node_id: str
    latitude: float
    longitude: float
    timezone: str
    altitude: float = 0.0
    country: str = "unknown"
    region: str = "unknown"
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "timezone": self.timezone,
            "altitude": self.altitude,
            "country": self.country,
            "region": self.region
        }


@dataclass
class SunChaseEvent:
    """Record of a sun-chase compute activation."""
    event_id: str
    timestamp: str
    energy_state: EnergyState
    compute_allocated: float
    duration_seconds: float
    source: str  # "solar_overcapacity"
    handoff_target: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "timestamp": self.timestamp,
            "energy_state": self.energy_state.to_dict(),
            "compute_allocated": round(self.compute_allocated, 3),
            "duration_seconds": round(self.duration_seconds, 2),
            "source": self.source,
            "handoff_target": self.handoff_target
        }


# ─────────────────────────────────────────────────────────────────────────────
# TCS GREEN SAFE HOUSE API SIMULATOR
# ─────────────────────────────────────────────────────────────────────────────

class TCSGreenSafeHouseAPI:
    """
    Interface to the TCS Green Safe House energy system.
    
    In production, this connects to actual sensors and controllers.
    For development/testing, it simulates realistic energy patterns.
    """
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self._connected = False
        self._last_reading_time = None
        self._simulated_state = {
            "battery_soc": 85.0,
            "solar_input_kw": 0.0,
            "load_kw": 2.5,
            "grid_draw_kw": 0.0,
            "supercap_voltage": 48.0
        }
    
    def connect(self) -> bool:
        """Establish connection to TCS system."""
        # In production: connect to actual TCS API
        self._connected = True
        return True
    
    def disconnect(self) -> None:
        """Disconnect from TCS system."""
        self._connected = False
    
    def get_battery_soc(self) -> float:
        """Get battery state of charge (0-100%)."""
        if not self._connected:
            return self._simulate_battery()
        # Production: return actual sensor reading
        return self._simulate_battery()
    
    def get_solar_input_kw(self) -> float:
        """Get current solar generation in kW."""
        return self._simulate_solar()
    
    def get_load_kw(self) -> float:
        """Get current load consumption in kW."""
        return self._simulated_state["load_kw"]
    
    def get_grid_draw_kw(self) -> float:
        """Get power drawn from grid (should be 0 for zero feed-in)."""
        return self._simulated_state["grid_draw_kw"]
    
    def get_surplus_kw(self) -> float:
        """Get available surplus power for compute."""
        solar = self.get_solar_input_kw()
        load = self.get_load_kw()
        battery_soc = self.get_battery_soc()
        
        # Only surplus if battery is full and solar > load
        if battery_soc >= 98.0 and solar > load:
            return solar - load
        return 0.0
    
    def get_supercap_voltage(self) -> float:
        """Get supercapacitor bank voltage."""
        return self._simulated_state["supercap_voltage"]
    
    def get_full_state(self) -> EnergyState:
        """Get complete energy state."""
        return EnergyState(
            battery_soc=self.get_battery_soc(),
            solar_input_kw=self.get_solar_input_kw(),
            load_kw=self.get_load_kw(),
            grid_draw_kw=self.get_grid_draw_kw(),
            surplus_kw=self.get_surplus_kw(),
            supercap_voltage=self.get_supercap_voltage()
        )
    
    def _simulate_battery(self) -> float:
        """Simulate realistic battery SOC patterns."""
        hour = datetime.datetime.now().hour
        
        # Night discharge, day charge
        if 6 <= hour <= 18:
            # Daytime: battery charging from solar
            base_soc = 85.0 + (hour - 6) * 2.0
            return min(100.0, base_soc + random.uniform(-2, 5))
        else:
            # Nighttime: battery discharging
            discharge = (24 - hour if hour > 18 else 6 - hour) * 1.5
            return max(20.0, 95.0 - discharge + random.uniform(-3, 3))
    
    def _simulate_solar(self) -> float:
        """Simulate realistic solar generation patterns."""
        hour = datetime.datetime.now().hour + datetime.datetime.now().minute / 60.0
        
        # Solar curve: peaks at noon
        if 6 <= hour <= 19:
            # Bell curve centered at noon
            solar_factor = math.exp(-((hour - 12) ** 2) / 18)
            peak_kw = self.config.get("peak_solar_kw", 8.0)
            return peak_kw * solar_factor + random.uniform(-0.5, 0.5)
        else:
            return 0.0


# ─────────────────────────────────────────────────────────────────────────────
# SOLAR POSITION CALCULATOR
# ─────────────────────────────────────────────────────────────────────────────

class SolarPositionCalculator:
    """
    Calculate solar position for any location and time.
    
    Uses simplified astronomical calculations for sun position
    without requiring external APIs.
    """
    
    @staticmethod
    def calculate_position(
        latitude: float,
        longitude: float,
        dt: Optional[datetime.datetime] = None
    ) -> SolarPosition:
        """
        Calculate solar position for given location and time.
        
        Args:
            latitude: Latitude in degrees
            longitude: Longitude in degrees  
            dt: Datetime (UTC if no timezone)
        
        Returns:
            SolarPosition with azimuth, elevation, and flux
        """
        dt = dt or datetime.datetime.utcnow()
        
        # Day of year
        day_of_year = dt.timetuple().tm_yday
        
        # Solar declination (simplified)
        declination = 23.44 * math.sin(math.radians((day_of_year - 81) * 360 / 365))
        
        # Hour angle
        hour = dt.hour + dt.minute / 60
        hour_angle = 15 * (hour - 12)  # 15 degrees per hour from solar noon
        
        # Convert to radians
        lat_rad = math.radians(latitude)
        dec_rad = math.radians(declination)
        ha_rad = math.radians(hour_angle)
        
        # Solar elevation
        sin_elevation = (
            math.sin(lat_rad) * math.sin(dec_rad) +
            math.cos(lat_rad) * math.cos(dec_rad) * math.cos(ha_rad)
        )
        elevation = math.degrees(math.asin(max(-1, min(1, sin_elevation))))
        
        # Solar azimuth
        cos_azimuth = (
            math.sin(dec_rad) - 
            math.sin(lat_rad) * sin_elevation
        ) / (math.cos(lat_rad) * math.cos(math.radians(elevation)) + 0.0001)
        azimuth = math.degrees(math.acos(max(-1, min(1, cos_azimuth))))
        if hour > 12:
            azimuth = 360 - azimuth
        
        # Solar flux at surface (simplified atmospheric model)
        if elevation > 0:
            air_mass = 1 / (math.sin(math.radians(elevation)) + 0.0001)
            solar_flux = SOLAR_CONSTANT * (0.7 ** (air_mass ** 0.678))
        else:
            solar_flux = 0
        
        # Daylight status
        is_daylight = elevation > 0
        
        # Calculate sunrise/sunset (simplified)
        day_length = (2/15) * math.degrees(math.acos(
            -math.tan(lat_rad) * math.tan(dec_rad)
        )) if abs(latitude) < 66.5 else (24 if latitude * declination > 0 else 0)
        
        sunrise_hour = 12 - day_length / 2
        sunset_hour = 12 + day_length / 2
        
        sunrise = dt.replace(
            hour=int(sunrise_hour), 
            minute=int((sunrise_hour % 1) * 60),
            second=0
        ) if is_daylight else None
        
        sunset = dt.replace(
            hour=int(sunset_hour),
            minute=int((sunset_hour % 1) * 60),
            second=0
        ) if is_daylight else None
        
        return SolarPosition(
            azimuth=azimuth,
            elevation=elevation,
            solar_flux=max(0, solar_flux),
            is_daylight=is_daylight,
            sunrise_time=sunrise,
            sunset_time=sunset,
            day_length_hours=day_length
        )
    
    @staticmethod
    def get_sunlit_nodes(
        nodes: List[NodeLocation],
        dt: Optional[datetime.datetime] = None
    ) -> List[Tuple[NodeLocation, SolarPosition]]:
        """
        Get all nodes currently in daylight, sorted by solar flux.
        
        Returns nodes best positioned for solar compute.
        """
        dt = dt or datetime.datetime.utcnow()
        
        sunlit = []
        for node in nodes:
            pos = SolarPositionCalculator.calculate_position(
                node.latitude, node.longitude, dt
            )
            if pos.is_daylight and pos.solar_flux > 100:
                sunlit.append((node, pos))
        
        # Sort by solar flux (highest first)
        sunlit.sort(key=lambda x: x[1].solar_flux, reverse=True)
        return sunlit


# ─────────────────────────────────────────────────────────────────────────────
# SOLAR CHASE COORDINATOR
# ─────────────────────────────────────────────────────────────────────────────

class SolarChaseCoordinator:
    """
    Core orchestrator for planetary sun-following compute.
    
    This module transforms KISWARM into a sun-following compute fabric:
    1. Monitors solar overcapacity from TCS Green Safe House
    2. Activates compute when energy surplus is detected
    3. Coordinates handoff to sunlit nodes when local sun sets
    4. Maintains zero grid draw (true carbon neutrality)
    
    Design Principle:
    "Never feed solar surplus to the grid. Convert it to intelligence."
    """
    
    def __init__(
        self,
        node_location: Optional[NodeLocation] = None,
        tcs_api: Optional[TCSGreenSafeHouseAPI] = None,
        energy_threshold: float = DEFAULT_ENERGY_THRESHOLD,
        surplus_threshold: float = DEFAULT_SURPLUS_THRESHOLD,
        mesh_interface: Optional[Any] = None
    ):
        self.node_location = node_location or self._auto_detect_location()
        self.tcs_api = tcs_api or TCSGreenSafeHouseAPI()
        self.energy_threshold = energy_threshold
        self.surplus_threshold = surplus_threshold
        self.mesh_interface = mesh_interface
        
        # State
        self.compute_mode = ComputeMode.DORMANT
        self.handoff_state = HandoffState.IDLE
        self.active_compute_load = ComputeLoadState(0, 0, 0, 0, 0)
        self._last_pivot_time: Optional[datetime.datetime] = None
        self._events: List[SunChaseEvent] = []
        
        # Configuration
        self._compute_allocation = DEFAULT_COMPUTE_ALLOCATION
        self._max_grid_draw = 0.0  # Zero feed-in enforcement
        self._constant_filter_amps = 6.0  # Grid-invisible constant draw
        
        # Thread management
        self._running = False
        self._monitor_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        
        # Statistics
        self._stats = {
            "total_compute_hours": 0.0,
            "total_kwh_computed": 0.0,
            "total_solar_kwh_used": 0.0,
            "grid_draw_events": 0,
            "handoffs_completed": 0,
            "handoffs_failed": 0,
            "overcapacity_events": 0,
            "zero_emission_percentage": 100.0
        }
        
        # Setup logging
        self._setup_logging()
    
    def _setup_logging(self):
        """Setup logging for solar chase operations."""
        log_dir = Path(os.environ.get(
            "KISWARM_HOME", 
            Path.home()
        )) / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        
        self.logger = logging.getLogger("solar_chase")
        handler = logging.FileHandler(log_dir / "solar_chase.log")
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [SOLAR-CHASE] %(message)s"
        ))
        self.logger.addHandler(handler)
        self.logger.setLevel(logging.INFO)
    
    def _auto_detect_location(self) -> NodeLocation:
        """Auto-detect node location from system."""
        hostname = socket.gethostname()
        
        # Try to get timezone
        try:
            import tzlocal
            tz = tzlocal.get_localzone().key
        except:
            tz = "UTC"
        
        # Default location (can be overridden)
        return NodeLocation(
            node_id=hostname,
            latitude=48.0,  # Default: Central Europe
            longitude=11.0,
            timezone=tz,
            country="unknown",
            region="unknown"
        )
    
    # ═══════════════════════════════════════════════════════════════════════════
    # CORE ORCHESTRATION
    # ═══════════════════════════════════════════════════════════════════════════
    
    def check_overcapacity_pivot(self) -> bool:
        """
        Check if solar overcapacity pivot should be activated.
        
        Returns True if pivot was triggered (compute activated).
        """
        if not self.tcs_api._connected:
            self.tcs_api.connect()
        
        energy_state = self.tcs_api.get_full_state()
        
        # Check pivot conditions
        should_pivot = (
            energy_state.battery_soc >= self.energy_threshold and
            energy_state.solar_input_kw > 0 and
            energy_state.surplus_kw >= self.surplus_threshold
        )
        
        if should_pivot:
            self.activate_compute_mode(energy_state)
            return True
        else:
            # Check if we need to deactivate
            if self.compute_mode == ComputeMode.ACTIVE:
                if energy_state.surplus_kw < self.surplus_threshold:
                    self.deactivate_compute_mode()
            return False
    
    def activate_compute_mode(self, energy_state: EnergyState) -> Dict[str, Any]:
        """
        Activate compute mode using solar surplus.
        
        Routes surplus energy to KISWARM compute operations
        instead of feeding back to grid.
        """
        with self._lock:
            if self.compute_mode == ComputeMode.ACTIVE:
                return {"status": "already_active"}
            
            self.compute_mode = ComputeMode.ACTIVE
            self._last_pivot_time = datetime.datetime.now()
            
            # Calculate compute allocation
            surplus_kw = energy_state.surplus_kw
            compute_kw = surplus_kw * self._compute_allocation
            
            # Distribute compute load
            self.active_compute_load = ComputeLoadState(
                ollama_inference_kw=compute_kw * 0.40,
                ciec_training_kw=compute_kw * 0.30,
                guard_operations_kw=compute_kw * 0.20,
                mesh_sync_kw=compute_kw * 0.10,
                total_compute_kw=compute_kw
            )
            
            # Create event
            event = SunChaseEvent(
                event_id=hashlib.md5(
                    f"{datetime.datetime.now().isoformat()}".encode()
                ).hexdigest()[:12],
                timestamp=datetime.datetime.now().isoformat(),
                energy_state=energy_state,
                compute_allocated=compute_kw,
                duration_seconds=0,
                source="solar_overcapacity"
            )
            self._events.append(event)
            
            # Update stats
            self._stats["overcapacity_events"] += 1
            
            # Log activation
            self.logger.info(
                f"☀️ SOLAR PIVOT ACTIVATED: {compute_kw:.2f}kW → compute | "
                f"Battery: {energy_state.battery_soc:.1f}% | "
                f"Surplus: {surplus_kw:.2f}kW"
            )
            
            # Check if handoff is needed (sun setting soon)
            solar_pos = SolarPositionCalculator.calculate_position(
                self.node_location.latitude,
                self.node_location.longitude
            )
            
            if solar_pos.elevation < 15 and solar_pos.elevation > 0:
                # Sun is low, prepare handoff
                self.request_global_handoff()
            
            return {
                "status": "activated",
                "compute_kw": compute_kw,
                "load_distribution": self.active_compute_load.to_dict(),
                "energy_state": energy_state.to_dict()
            }
    
    def deactivate_compute_mode(self) -> Dict[str, Any]:
        """Deactivate compute mode when solar surplus ends."""
        with self._lock:
            if self.compute_mode != ComputeMode.ACTIVE:
                return {"status": "not_active"}
            
            # Calculate duration
            if self._last_pivot_time:
                duration = (datetime.datetime.now() - self._last_pivot_time).total_seconds()
                self._stats["total_compute_hours"] += duration / 3600
            
            self.compute_mode = ComputeMode.DORMANT
            self.active_compute_load = ComputeLoadState(0, 0, 0, 0, 0)
            
            self.logger.info("🌙 Compute deactivated - solar surplus ended")
            
            return {
                "status": "deactivated",
                "total_compute_hours": self._stats["total_compute_hours"]
            }
    
    def request_global_handoff(self) -> Dict[str, Any]:
        """
        Request handoff to a sunlit node in the mesh.
        
        Broadcasts to mesh: "I have overcapacity ending – who can take load?"
        Mesh responds with best sun-exposed node.
        """
        self.handoff_state = HandoffState.SEARCHING
        
        self.logger.info(
            f"🌅 Handoff requested - sun setting at {self.node_location.node_id}"
        )
        
        # In production: query actual mesh for sunlit nodes
        # For now, simulate finding a sunlit node
        
        # Calculate which longitude band is currently sunlit
        hour_utc = datetime.datetime.utcnow().hour
        
        # Approximate longitude where sun is overhead
        sun_longitude = (hour_utc - 12) * 15  # 15 degrees per hour
        
        # Find candidate nodes (would come from mesh in production)
        # Simulate handoff completion
        self.handoff_state = HandoffState.COMPLETED
        self._stats["handoffs_completed"] += 1
        
        return {
            "status": "handoff_initiated",
            "current_node": self.node_location.node_id,
            "sun_longitude": sun_longitude,
            "state": self.handoff_state.value
        }
    
    # ═══════════════════════════════════════════════════════════════════════════
    # MONITORING LOOP
    # ═══════════════════════════════════════════════════════════════════════════
    
    def start_monitoring(self, interval_seconds: float = 30.0) -> None:
        """Start continuous monitoring loop."""
        if self._running:
            return
        
        self._running = True
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            args=(interval_seconds,),
            daemon=True
        )
        self._monitor_thread.start()
        self.logger.info(f"☀️ Solar Chase monitoring started ({interval_seconds}s interval)")
    
    def stop_monitoring(self) -> None:
        """Stop monitoring loop."""
        self._running = False
        if self._monitor_thread:
            self._monitor_thread.join(timeout=5)
        self.logger.info("Solar Chase monitoring stopped")
    
    def _monitor_loop(self, interval: float) -> None:
        """Background monitoring loop."""
        while self._running:
            try:
                self.check_overcapacity_pivot()
                self._update_statistics()
            except Exception as e:
                self.logger.error(f"Monitor error: {e}")
            
            time.sleep(interval)
    
    def _update_statistics(self) -> None:
        """Update running statistics."""
        if self.compute_mode == ComputeMode.ACTIVE:
            # Accumulate compute time
            pass  # Handled in deactivate
    
    # ═══════════════════════════════════════════════════════════════════════════
    # QUERY METHODS
    # ═══════════════════════════════════════════════════════════════════════════
    
    def get_solar_status(self) -> SolarStatus:
        """Get current solar status for this node."""
        pos = SolarPositionCalculator.calculate_position(
            self.node_location.latitude,
            self.node_location.longitude
        )
        
        energy = self.tcs_api.get_full_state()
        
        if not pos.is_daylight:
            return SolarStatus.NIGHT
        elif pos.elevation < 5:
            return SolarStatus.TWILIGHT
        elif pos.elevation < 15:
            if datetime.datetime.now().hour < 12:
                return SolarStatus.SUNRISE
            else:
                return SolarStatus.SUNSET
        elif energy.surplus_kw >= self.surplus_threshold:
            return SolarStatus.OVERCAPACITY
        else:
            return SolarStatus.DAY
    
    def get_compute_load(self) -> ComputeLoadState:
        """Get current compute load allocation."""
        return self.active_compute_load
    
    def get_energy_state(self) -> EnergyState:
        """Get current energy state."""
        return self.tcs_api.get_full_state()
    
    def get_solar_position(self) -> SolarPosition:
        """Get current solar position for this node."""
        return SolarPositionCalculator.calculate_position(
            self.node_location.latitude,
            self.node_location.longitude
        )
    
    def get_stats(self) -> Dict[str, Any]:
        """Get coordinator statistics."""
        return {
            **self._stats,
            "compute_mode": self.compute_mode.value,
            "handoff_state": self.handoff_state.value,
            "node_location": self.node_location.to_dict(),
            "energy_threshold": self.energy_threshold,
            "surplus_threshold": self.surplus_threshold,
            "events_count": len(self._events)
        }
    
    def get_events(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Get recent solar chase events."""
        return [e.to_dict() for e in self._events[-limit:]]
    
    # ═══════════════════════════════════════════════════════════════════════════
    # CONFIGURATION
    # ═══════════════════════════════════════════════════════════════════════════
    
    def set_location(self, latitude: float, longitude: float, 
                    timezone: str = "UTC") -> None:
        """Update node location."""
        self.node_location.latitude = latitude
        self.node_location.longitude = longitude
        self.node_location.timezone = timezone
    
    def set_thresholds(self, energy_threshold: float, 
                      surplus_threshold: float) -> None:
        """Update pivot thresholds."""
        self.energy_threshold = energy_threshold
        self.surplus_threshold = surplus_threshold
    
    def configure_compute_allocation(
        self,
        ollama_fraction: float = 0.40,
        ciec_fraction: float = 0.30,
        guard_fraction: float = 0.20,
        mesh_fraction: float = 0.10
    ) -> None:
        """Configure compute load distribution."""
        # Validate fractions sum to 1.0
        total = ollama_fraction + ciec_fraction + guard_fraction + mesh_fraction
        if abs(total - 1.0) > 0.01:
            raise ValueError(f"Fractions must sum to 1.0, got {total}")
        
        # Store for use in activate_compute_mode
        self._compute_fractions = {
            "ollama": ollama_fraction,
            "ciec": ciec_fraction,
            "guard": guard_fraction,
            "mesh": mesh_fraction
        }


# ─────────────────────────────────────────────────────────────────────────────
# CLI ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

def main():
    """CLI entry point for testing."""
    print("=" * 60)
    print("  KISWARM v5.1 — Solar Chase Coordinator")
    print("=" * 60)
    
    coordinator = SolarChaseCoordinator()
    
    print(f"\n📍 Node Location: {coordinator.node_location.latitude:.2f}°N, "
          f"{coordinator.node_location.longitude:.2f}°E")
    
    # Get current status
    solar_status = coordinator.get_solar_status()
    energy_state = coordinator.get_energy_state()
    solar_pos = coordinator.get_solar_position()
    
    print(f"\n☀️ Solar Status: {solar_status.value.upper()}")
    print(f"   Elevation: {solar_pos.elevation:.1f}° | Azimuth: {solar_pos.azimuth:.1f}°")
    print(f"   Solar Flux: {solar_pos.solar_flux:.0f} W/m²")
    
    print(f"\n⚡ Energy State:")
    print(f"   Battery SOC: {energy_state.battery_soc:.1f}%")
    print(f"   Solar Input: {energy_state.solar_input_kw:.2f} kW")
    print(f"   Load: {energy_state.load_kw:.2f} kW")
    print(f"   Surplus: {energy_state.surplus_kw:.2f} kW")
    
    # Check pivot
    pivoted = coordinator.check_overcapacity_pivot()
    print(f"\n🔄 Pivot Status: {'ACTIVATED ✅' if pivoted else 'DORMANT'}")
    
    print(f"\n{coordinator.get_stats()}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
