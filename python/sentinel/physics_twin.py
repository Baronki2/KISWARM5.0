"""
KISWARM v4.0 — Module 13: Digital Twin Physics Engine
======================================================
Modular physics simulation components for industrial processes.

Components:
  1. ThermalModule      – dT/dt = (Q_in − Q_loss − Q_storage) / C
  2. PumpFlowModule     – Flow = k1*sqrt(ΔP), cavitation detection
  3. BatteryECMModule   – Hybrid Equivalent Circuit Model: SOC, voltage, thermal
  4. PowerRoutingModule – Grid coupling, frequency deviation Δf ≈ P_mismatch/(2H)
  5. LatencyNoiseLayer  – u_effective(t) = u_command(t−τ) + ε_noise
  6. FaultInjector      – Sensor/actuator/infrastructure/physical fault injection
  7. PhysicsTwin        – Full integrated simulation engine

Design: Production never mutates live. All evolution happens here first.
"""

from __future__ import annotations

import math
import time
import json
import os
import logging
import random
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# 1. THERMAL MODULE
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ThermalState:
    temperature:     float = 20.0    # °C
    q_in:            float = 0.0     # W — heat input
    q_loss:          float = 0.0     # W — dissipation
    q_storage:       float = 0.0     # W — stored (mass * cp * dT)
    thermal_capacity: float = 5000.0  # J/°C
    k_loss:          float = 10.0    # W/°C — heat loss coefficient
    t_env:           float = 20.0    # °C — ambient

    def step(self, dt: float) -> float:
        """
        Euler step: dT/dt = (Q_in − Q_loss − Q_storage) / C
        Q_loss = k_loss * (T − T_env)
        Returns new temperature.
        """
        self.q_loss = self.k_loss * (self.temperature - self.t_env)
        dT_dt       = (self.q_in - self.q_loss - self.q_storage) / max(self.thermal_capacity, 1.0)
        self.temperature += dt * dT_dt
        return self.temperature

    def to_dict(self) -> dict:
        return {
            "temperature": round(self.temperature, 3),
            "q_in":        round(self.q_in, 2),
            "q_loss":      round(self.q_loss, 2),
        }


# ─────────────────────────────────────────────────────────────────────────────
# 2. PUMP / FLOW MODULE
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PumpState:
    flow_rate:        float = 0.0    # m³/s
    pressure_inlet:   float = 2.0   # bar
    pressure_outlet:  float = 4.0   # bar
    motor_current:    float = 0.0   # A
    k_flow:           float = 1.0   # flow coefficient
    k_torque:         float = 0.5   # torque coefficient
    npsh_available:   float = 5.0   # m (Net Positive Suction Head)
    npsh_required:    float = 3.0   # m
    cavitation_event: bool  = False
    wear_index:       float = 0.0   # 0..1

    def step(self, dp: Optional[float] = None, current: Optional[float] = None) -> dict:
        """
        Bernoulli simplified: Flow = k1 * sqrt(ΔP)
        Motor torque:         T = k2 * current
        Cavitation check:     NPSH_available < NPSH_required
        """
        if dp is not None:
            dp = max(dp, 0.0)
            self.flow_rate = self.k_flow * math.sqrt(dp)
            self.pressure_outlet = self.pressure_inlet + dp

        if current is not None:
            self.motor_current = current
            # Wear: proportional to current * time
            self.wear_index = min(1.0, self.wear_index + abs(current) * 1e-6)

        # Cavitation detection
        self.cavitation_event = self.npsh_available < self.npsh_required

        return self.to_dict()

    def to_dict(self) -> dict:
        return {
            "flow_rate":        round(self.flow_rate, 4),
            "pressure_outlet":  round(self.pressure_outlet, 3),
            "motor_current":    round(self.motor_current, 3),
            "cavitation_event": self.cavitation_event,
            "wear_index":       round(self.wear_index, 6),
            "npsh_margin":      round(self.npsh_available - self.npsh_required, 3),
        }


# ─────────────────────────────────────────────────────────────────────────────
# 3. BATTERY ECM MODULE
# ─────────────────────────────────────────────────────────────────────────────

def _ocv_from_soc(soc: float) -> float:
    """
    Simplified OCV(SOC) curve using quadratic approximation.
    Real systems use lookup tables from cell characterization.
    """
    # Typical Li-ion: 3.0V empty → 4.2V full
    soc_clamp = max(0.0, min(1.0, soc))
    return 3.0 + 1.2 * soc_clamp - 0.3 * (1.0 - soc_clamp) ** 2


@dataclass
class BatteryState:
    soc:              float = 0.8     # 0..1
    capacity_ah:      float = 100.0   # Ah
    r_internal:       float = 0.05    # Ohm
    temperature:      float = 25.0    # °C
    thermal_capacity: float = 1500.0  # J/°C
    cooling_loss:     float = 5.0     # W
    i_charge:         float = 0.0     # A
    i_discharge:      float = 0.0     # A

    @property
    def voltage(self) -> float:
        """V = OCV(SOC) − I*R_internal"""
        net_current = self.i_charge - self.i_discharge
        return _ocv_from_soc(self.soc) - net_current * self.r_internal

    def step(self, dt: float, i_charge: float = 0.0, i_discharge: float = 0.0) -> dict:
        """
        SOC update:      SOC_{t+1} = SOC_t + (I_charge − I_discharge)*dt / (3600 * Capacity)
        Thermal drift:   dT/dt = (I²R − cooling_loss) / C
        """
        self.i_charge    = max(0.0, i_charge)
        self.i_discharge = max(0.0, i_discharge)
        net_i            = self.i_charge - self.i_discharge

        # SOC update
        d_soc = net_i * dt / (3600.0 * max(self.capacity_ah, 1.0))
        self.soc = max(0.0, min(1.0, self.soc + d_soc))

        # Thermal drift
        joule_heat = net_i ** 2 * self.r_internal
        dT_dt      = (joule_heat - self.cooling_loss) / max(self.thermal_capacity, 1.0)
        self.temperature += dT_dt * dt

        return self.to_dict()

    def to_dict(self) -> dict:
        return {
            "soc":         round(self.soc, 4),
            "voltage":     round(self.voltage, 3),
            "temperature": round(self.temperature, 2),
            "r_internal":  self.r_internal,
            "health":      round(1.0 - min(1.0, max(0.0, (self.temperature - 25.0) / 50.0)), 3),
        }


# ─────────────────────────────────────────────────────────────────────────────
# 4. POWER ROUTING MODULE
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PowerRoutingState:
    loads:       list[float] = field(default_factory=list)    # W each load
    generation:  list[float] = field(default_factory=list)    # W each source
    frequency:   float       = 50.0   # Hz
    inertia_h:   float       = 5.0    # seconds (inertia constant)
    nominal_freq: float      = 50.0   # Hz

    @property
    def total_load(self) -> float:
        return sum(self.loads)

    @property
    def total_generation(self) -> float:
        return sum(self.generation)

    @property
    def p_mismatch(self) -> float:
        return self.total_load - self.total_generation

    def step(self, dt: float) -> dict:
        """
        P_total = Σloads − Σgeneration
        Δf ≈ P_mismatch / (2H)
        f_{t+1} = f_t + Δf * correction
        """
        delta_f    = self.p_mismatch / max(2.0 * self.inertia_h, 1.0) * 0.001
        self.frequency = max(45.0, min(55.0, self.frequency - delta_f))
        return self.to_dict()

    def to_dict(self) -> dict:
        return {
            "total_load":       round(self.total_load, 2),
            "total_generation": round(self.total_generation, 2),
            "p_mismatch":       round(self.p_mismatch, 2),
            "frequency":        round(self.frequency, 4),
            "freq_deviation":   round(self.frequency - self.nominal_freq, 4),
        }


# ─────────────────────────────────────────────────────────────────────────────
# 5. LATENCY + NOISE LAYER
# ─────────────────────────────────────────────────────────────────────────────

class LatencyNoiseLayer:
    """
    Models actuator delay and sensor noise.
    u_effective(t) = u_command(t − τ) + ε_noise
    τ random within bounded delay.
    """

    def __init__(
        self,
        min_delay_ms:  float = 20.0,
        max_delay_ms:  float = 200.0,
        noise_std:     float = 0.01,
        seed:          Optional[int] = None,
    ):
        self._min_delay = min_delay_ms / 1000.0    # seconds
        self._max_delay = max_delay_ms / 1000.0
        self._noise_std = noise_std
        self._rng       = random.Random(seed)
        self._queue:    deque[tuple[float, float]] = deque()   # (deliver_at, value)

    def command(self, u: float, current_time: float) -> None:
        """Submit an actuator command. Delivered after random delay."""
        tau         = self._rng.uniform(self._min_delay, self._max_delay)
        deliver_at  = current_time + tau
        self._queue.append((deliver_at, u))

    def get_effective(self, current_time: float) -> Optional[float]:
        """
        Return the effective actuator value at current_time.
        Applies noise to delivered commands.
        """
        delivered = None
        while self._queue and self._queue[0][0] <= current_time:
            _, value  = self._queue.popleft()
            delivered = value

        if delivered is None:
            return None

        # Gaussian noise: ε ~ N(0, noise_std²)
        noise = self._rng.gauss(0, self._noise_std)
        return delivered + noise

    def queue_depth(self) -> int:
        return len(self._queue)


# ─────────────────────────────────────────────────────────────────────────────
# 6. FAULT INJECTOR
# ─────────────────────────────────────────────────────────────────────────────

FAULT_TYPES = {
    # Sensor faults
    "sensor_stuck":    "Sensor stuck at last value",
    "sensor_drift":    "Sensor reading drifts linearly",
    "sensor_spike":    "Instantaneous sensor spike",
    # Actuator faults
    "actuator_delay":  "Actuator response delayed",
    "actuator_partial":"Actuator can only reach 60% of commanded value",
    "actuator_failed": "Actuator fully failed (zero output)",
    # Infrastructure faults
    "vm_freeze":       "VM CPU pause (missed samples)",
    "opc_disconnect":  "OPC UA connection lost",
    "sql_lag":         "Historian query latency spike",
    # Physical faults
    "hx_degradation":  "Heat exchanger efficiency drop",
    "pump_cavitation": "Pump cavitation event",
    "battery_rise":    "Battery internal resistance increase",
}


@dataclass
class ActiveFault:
    fault_type:  str
    tag:         str
    start_time:  float
    end_time:    float      # math.inf = permanent until cleared
    severity:    float      # 0..1
    param:       dict       = field(default_factory=dict)

    @property
    def is_active(self) -> bool:
        now = time.time()
        return self.start_time <= now <= self.end_time

    def to_dict(self) -> dict:
        return {
            "type":      self.fault_type,
            "tag":       self.tag,
            "severity":  self.severity,
            "active":    self.is_active,
            "param":     self.param,
        }


@dataclass
class FaultInjectionReport:
    """Result of evaluating a mutation under fault conditions."""
    mutation_id:         str
    fault_scenarios:     int    = 0
    catastrophic_events: int    = 0
    total_steps:         int    = 0
    time_to_detect:      list[float] = field(default_factory=list)
    time_to_compensate:  list[float] = field(default_factory=list)
    stability_margins:   list[float] = field(default_factory=list)
    actuator_stress:     list[float] = field(default_factory=list)

    @property
    def survival_score(self) -> float:
        """Survival = 1 − catastrophic_events / total_steps"""
        if self.total_steps == 0:
            return 1.0
        return 1.0 - self.catastrophic_events / self.total_steps

    @property
    def mean_detection_time(self) -> float:
        return sum(self.time_to_detect) / len(self.time_to_detect) if self.time_to_detect else 0.0

    @property
    def mean_stability(self) -> float:
        return sum(self.stability_margins) / len(self.stability_margins) if self.stability_margins else 0.0

    def to_dict(self) -> dict:
        return {
            "mutation_id":         self.mutation_id,
            "survival_score":      round(self.survival_score, 4),
            "fault_scenarios":     self.fault_scenarios,
            "catastrophic_events": self.catastrophic_events,
            "total_steps":         self.total_steps,
            "mean_detection_time": round(self.mean_detection_time, 3),
            "mean_stability":      round(self.mean_stability, 4),
        }


class FaultInjector:
    """
    Industrial fault injection engine.
    Applies probabilistic fault schedules to the digital twin simulation.
    """

    def __init__(self, seed: Optional[int] = None):
        self._rng:    random.Random   = random.Random(seed)
        self._active: list[ActiveFault] = []
        self._history: list[ActiveFault] = []
        self._inject_count = 0

    def schedule_random(
        self,
        tags:           list[str],
        duration_steps: int   = 100,
        fault_rate:     float = 0.05,
    ) -> list[ActiveFault]:
        """
        Generate a random fault schedule for a simulation run.
        Each step has fault_rate probability of triggering a fault.
        """
        faults        = []
        fault_types   = list(FAULT_TYPES.keys())
        now           = time.time()

        for step in range(duration_steps):
            if self._rng.random() < fault_rate:
                fault_type = self._rng.choice(fault_types)
                tag        = self._rng.choice(tags) if tags else "UNKNOWN"
                duration   = self._rng.uniform(1.0, 30.0)   # seconds
                fault      = ActiveFault(
                    fault_type = fault_type,
                    tag        = tag,
                    start_time = now + step,
                    end_time   = now + step + duration,
                    severity   = self._rng.uniform(0.3, 1.0),
                    param      = {"drift_rate": self._rng.uniform(0.01, 0.1)},
                )
                faults.append(fault)
                self._inject_count += 1

        self._active.extend(faults)
        return faults

    def apply_sensor_drift(self, real_value: float, tag: str, elapsed: float) -> float:
        """
        Apply sensor drift fault: T_measured = T_real + drift_rate * t
        """
        for fault in self._active:
            if fault.tag == tag and fault.fault_type == "sensor_drift" and fault.is_active:
                drift_rate = fault.param.get("drift_rate", 0.05)
                return real_value + drift_rate * elapsed
        return real_value

    def apply_sensor_stuck(self, real_value: float, last_value: float, tag: str) -> float:
        for fault in self._active:
            if fault.tag == tag and fault.fault_type == "sensor_stuck" and fault.is_active:
                return last_value   # frozen
        return real_value

    def apply_actuator_partial(self, command: float, tag: str) -> float:
        for fault in self._active:
            if fault.tag == tag and fault.fault_type == "actuator_partial" and fault.is_active:
                return command * 0.6 * (1.0 - fault.severity * 0.3)
            if fault.tag == tag and fault.fault_type == "actuator_failed" and fault.is_active:
                return 0.0
        return command

    def is_catastrophic(self, state: dict) -> bool:
        """
        Check if current state is catastrophic.
        Any severe physical exceedance counts as a catastrophic event.
        """
        # Check for severe anomalies
        for key, value in state.items():
            if isinstance(value, (int, float)):
                if "temperature" in key and abs(value) > 150:
                    return True
                if "pressure" in key and value > 15.0:
                    return True
                if "soc" in key and value < 0.02:
                    return True
                if "frequency" in key and abs(value - 50.0) > 4.0:
                    return True
        return False

    def evaluate_mutation(
        self,
        mutation_id:    str,
        simulation_fn:  object,  # callable(params) → list[dict]
        tags:           list[str],
        n_scenarios:    int   = 10,
        fault_rate:     float = 0.1,
    ) -> FaultInjectionReport:
        """
        Evaluate a parameter mutation under multiple fault scenarios.
        Returns a FaultInjectionReport with survival score and metrics.
        """
        report = FaultInjectionReport(mutation_id=mutation_id)

        for _ in range(n_scenarios):
            report.fault_scenarios += 1
            self.schedule_random(tags, duration_steps=50, fault_rate=fault_rate)

            if callable(simulation_fn):
                try:
                    states = simulation_fn()
                    for state in states:
                        report.total_steps += 1
                        if isinstance(state, dict) and self.is_catastrophic(state):
                            report.catastrophic_events += 1
                        # Collect stability proxy (lower actuator stress = more stable)
                        if isinstance(state, dict) and "wear_index" in state:
                            report.actuator_stress.append(state["wear_index"])
                except Exception:
                    report.catastrophic_events += 1
                    report.total_steps += 1

        return report

    def get_active_faults(self) -> list[dict]:
        return [f.to_dict() for f in self._active if f.is_active]

    def clear_faults(self) -> int:
        cleared = sum(1 for f in self._active if f.is_active)
        self._history.extend(self._active)
        self._active = []
        return cleared


# ─────────────────────────────────────────────────────────────────────────────
# 7. INTEGRATED PHYSICS TWIN
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class TwinSimResult:
    """Result of one physics twin simulation run."""
    run_id:             str
    steps:              int
    dt:                 float
    thermal_history:    list[float] = field(default_factory=list)
    pump_history:       list[dict]  = field(default_factory=list)
    battery_history:    list[dict]  = field(default_factory=list)
    power_history:      list[dict]  = field(default_factory=list)
    fault_events:       list[dict]  = field(default_factory=list)
    survival_score:     float       = 1.0
    cavitation_events:  int         = 0
    mean_temperature:   float       = 0.0
    final_soc:          float       = 0.0

    def to_dict(self) -> dict:
        return {
            "run_id":           self.run_id,
            "steps":            self.steps,
            "dt":               self.dt,
            "survival_score":   round(self.survival_score, 4),
            "cavitation_events": self.cavitation_events,
            "mean_temperature": round(self.mean_temperature, 2),
            "final_soc":        round(self.final_soc, 4),
        }


class PhysicsTwin:
    """
    Integrated digital twin simulator.

    Combines all physics modules into one coherent simulation.
    Used for:
    - Evaluating parameter mutations before production deployment
    - Training CIEC constrained RL policy
    - Fault injection evaluation
    - Historical data replay
    """

    def __init__(self, store_path: Optional[str] = None):
        kiswarm_dir = os.environ.get("KISWARM_HOME", os.path.expanduser("~/KISWARM"))
        self._store = store_path or os.path.join(kiswarm_dir, "twin_history.json")

        # Physics modules
        self.thermal  = ThermalState()
        self.pump     = PumpState()
        self.battery  = BatteryState()
        self.power    = PowerRoutingState(loads=[1000.0, 500.0], generation=[1200.0])
        self.latency  = LatencyNoiseLayer()
        self.faults   = FaultInjector(seed=42)

        # History
        self._run_count    = 0
        self._promotions   = 0
        self._rejections   = 0
        self._history:     list[TwinSimResult] = []
        self._load()

    def run(
        self,
        steps:     int   = 200,
        dt:        float = 0.1,
        q_in:      float = 2000.0,
        dp:        float = 2.0,
        i_charge:  float = 10.0,
        i_disch:   float = 8.0,
        inject_faults: bool = False,
    ) -> TwinSimResult:
        """
        Run one integrated physics simulation.

        Args:
            steps:   Number of time steps
            dt:      Time step size in seconds
            q_in:    Thermal input power (W)
            dp:      Pump differential pressure (bar)
            i_charge, i_disch: Battery currents (A)
            inject_faults: Enable random fault injection
        """
        import hashlib as _hs
        run_id = _hs.md5(f"{time.time()}".encode()).hexdigest()[:8]
        self._run_count += 1

        # Reset modules to initial state
        self.thermal  = ThermalState(temperature=20.0, q_in=q_in)
        self.pump     = PumpState()
        self.battery  = BatteryState()
        self.power    = PowerRoutingState(loads=[1000.0, 500.0], generation=[1200.0])

        if inject_faults:
            self.faults.schedule_random(
                ["temperature", "flow", "soc", "pressure"],
                duration_steps=steps,
                fault_rate=0.08,
            )

        result = TwinSimResult(run_id=run_id, steps=steps, dt=dt)
        cat_events = 0

        for step in range(steps):
            t = step * dt

            # Apply fault effects
            effective_q = self.faults.apply_actuator_partial(q_in, "heater")
            effective_dp = self.faults.apply_actuator_partial(dp, "pump")

            # Step all physics modules
            self.thermal.q_in = effective_q
            temp              = self.thermal.step(dt)
            pump_state        = self.pump.step(dp=effective_dp, current=15.0 * effective_dp)
            batt_state        = self.battery.step(dt, i_charge, i_disch)
            power_state       = self.power.step(dt)

            # Apply sensor noise/drift to observed temperature
            obs_temp = self.faults.apply_sensor_drift(temp, "temperature", t)
            obs_temp = self.faults.apply_sensor_stuck(obs_temp, result.thermal_history[-1] if result.thermal_history else obs_temp, "temperature")

            # Latency layer on pump command
            self.latency.command(effective_dp, t)

            result.thermal_history.append(obs_temp)
            result.pump_history.append(pump_state)
            result.battery_history.append(batt_state)
            result.power_history.append(power_state)

            if self.pump.cavitation_event:
                result.cavitation_events += 1

            # Check catastrophe
            combined_state = {
                "temperature": temp,
                "pressure":    self.pump.pressure_outlet,
                "soc":         self.battery.soc,
                "frequency":   self.power.frequency,
                "wear_index":  self.pump.wear_index,
            }
            if self.faults.is_catastrophic(combined_state):
                cat_events += 1

        # Compute summary stats
        if result.thermal_history:
            result.mean_temperature = sum(result.thermal_history) / len(result.thermal_history)
        result.final_soc      = self.battery.soc
        result.survival_score = 1.0 - cat_events / max(steps, 1)
        result.fault_events   = self.faults.get_active_faults()

        self._history.append(result)
        self._save()

        logger.info(
            "Twin run %s: steps=%d | survival=%.3f | cavitations=%d | mean_T=%.1f°C",
            run_id, steps, result.survival_score, result.cavitation_events, result.mean_temperature,
        )
        return result

    def evaluate_mutation(
        self,
        mutation_params: dict,
        baseline_result: Optional[TwinSimResult] = None,
        n_runs:          int   = 5,
    ) -> tuple[bool, dict]:
        """
        Evaluate a parameter mutation by running the twin with modified parameters.
        Returns (promote: bool, metrics: dict).

        Acceptance criteria:
          - survival_score >= baseline (or 0.95 if no baseline)
          - mean_temperature <= baseline + 5°C
          - cavitation_events == 0
          - final_soc >= baseline - 0.05
        """
        baseline_survival = baseline_result.survival_score if baseline_result else 0.95
        baseline_temp     = baseline_result.mean_temperature if baseline_result else 60.0
        baseline_soc      = baseline_result.final_soc if baseline_result else 0.5

        results = [
            self.run(
                steps    = mutation_params.get("steps", 100),
                dt       = mutation_params.get("dt", 0.1),
                q_in     = mutation_params.get("q_in", 2000.0),
                dp       = mutation_params.get("dp", 2.0),
                i_charge = mutation_params.get("i_charge", 10.0),
                i_disch  = mutation_params.get("i_disch", 8.0),
                inject_faults=True,
            )
            for _ in range(n_runs)
        ]

        avg_survival  = sum(r.survival_score for r in results) / n_runs
        avg_temp      = sum(r.mean_temperature for r in results) / n_runs
        total_cav     = sum(r.cavitation_events for r in results)
        avg_soc       = sum(r.final_soc for r in results) / n_runs

        reasons = []
        promote = True

        if avg_survival < baseline_survival - 0.02:
            reasons.append(f"survival_dropped ({avg_survival:.3f} < {baseline_survival:.3f})")
            promote = False
        if avg_temp > baseline_temp + 5.0:
            reasons.append(f"temp_exceeded ({avg_temp:.1f} > {baseline_temp+5:.1f}°C)")
            promote = False
        if total_cav > 0:
            reasons.append(f"cavitation_events ({total_cav})")
            promote = False
        if avg_soc < baseline_soc - 0.05:
            reasons.append(f"soc_dropped ({avg_soc:.3f} < {baseline_soc-0.05:.3f})")
            promote = False

        if promote:
            self._promotions += 1
        else:
            self._rejections += 1

        metrics = {
            "promoted":        promote,
            "reason":          ", ".join(reasons) if reasons else "all_criteria_met",
            "avg_survival":    round(avg_survival, 4),
            "avg_temperature": round(avg_temp, 2),
            "avg_soc":         round(avg_soc, 4),
            "cavitation_total": total_cav,
            "n_runs":          n_runs,
        }
        self._save()
        return promote, metrics

    def get_stats(self) -> dict:
        return {
            "total_runs":   self._run_count,
            "promotions":   self._promotions,
            "rejections":   self._rejections,
            "history_len":  len(self._history),
            "store_path":   self._store,
        }

    def _load(self):
        try:
            if os.path.exists(self._store):
                with open(self._store) as f:
                    raw = json.load(f)
                self._run_count  = raw.get("total_runs", 0)
                self._promotions = raw.get("promotions", 0)
                self._rejections = raw.get("rejections", 0)
        except Exception as exc:
            logger.warning("Physics twin load failed: %s", exc)

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self._store) if os.path.dirname(self._store) else ".", exist_ok=True)
            with open(self._store, "w") as f:
                json.dump({
                    "total_runs":   self._run_count,
                    "promotions":   self._promotions,
                    "rejections":   self._rejections,
                    "last_updated": time.strftime("%Y-%m-%dT%H:%M:%S"),
                }, f, indent=2)
        except Exception as exc:
            logger.error("Physics twin save failed: %s", exc)
