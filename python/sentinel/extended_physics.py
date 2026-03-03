"""
KISWARM v4.1 — Module 19: Extended Physics Twin Engine
=======================================================
Full modular physics library integrating:
  - ThermalBlock  (energy balance: m·c·dT/dt = Q_in − Q_out − hA(T−T_env))
  - PumpBlock     (Bernoulli: Q = k·RPM, H = H0 − a·Q², P = ρgQH/η)
  - ValveBlock    (first-order: τ·dv/dt + v = u)
  - MotorBlock    (J·dω/dt = T_e − T_load − B·ω,  V = L·di/dt + R·i + k_e·ω)
  - BatteryBlock  (V = V_oc(SOC) − I·R_int,  dSOC/dt = −I/C_nom)
  - ElectricalBlock (frequency deviation from power mismatch)

Integration: RK4 (default) or semi-implicit Euler
Fault injection: parameter drift, bias, saturation, delay, stuck
"""

from __future__ import annotations

import math
import random
import hashlib
import json
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple, Any


# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

GRAVITY   = 9.81      # m/s²
WATER_RHO = 1000.0    # kg/m³
KELVIN    = 273.15


# ─────────────────────────────────────────────────────────────────────────────
# BASE PHYSICS BLOCK
# ─────────────────────────────────────────────────────────────────────────────

class PhysicsBlock:
    """Abstract base for all physics blocks."""

    name: str = "Block"

    def state_dot(self, state: Dict[str, float],
                  inputs: Dict[str, float],
                  params: Dict[str, float]) -> Dict[str, float]:
        """Returns derivatives of all state variables."""
        raise NotImplementedError

    def state_names(self) -> List[str]:
        raise NotImplementedError

    def default_state(self) -> Dict[str, float]:
        raise NotImplementedError

    def default_params(self) -> Dict[str, float]:
        raise NotImplementedError


# ─────────────────────────────────────────────────────────────────────────────
# THERMAL BLOCK
# m·c·dT/dt = Q_in − Q_out − h·A·(T − T_env)
# ─────────────────────────────────────────────────────────────────────────────

class ThermalBlock(PhysicsBlock):
    name = "Thermal"

    def state_dot(self, state, inputs, params):
        T     = state.get("T", 25.0)
        Q_in  = inputs.get("Q_in",  1000.0)    # W
        Q_out = inputs.get("Q_out",  200.0)    # W
        m     = params.get("mass",   10.0)     # kg
        c     = params.get("c_heat", 4186.0)   # J/(kg·K) water
        h     = params.get("h_conv", 20.0)     # W/(m²·K)
        A     = params.get("area",   0.5)      # m²
        T_env = params.get("T_env",  20.0)     # °C
        dT_dt = (Q_in - Q_out - h * A * (T - T_env)) / (m * c)
        return {"T": dT_dt}

    def state_names(self): return ["T"]
    def default_state(self): return {"T": 25.0}
    def default_params(self): return {
        "mass": 10.0, "c_heat": 4186.0,
        "h_conv": 20.0, "area": 0.5, "T_env": 20.0
    }


# ─────────────────────────────────────────────────────────────────────────────
# PUMP BLOCK
# Q = k·RPM
# H = H0 − a·Q²          (affinity law head curve)
# P = ρ·g·Q·H / η
# ─────────────────────────────────────────────────────────────────────────────

class PumpBlock(PhysicsBlock):
    name = "Pump"

    def state_dot(self, state, inputs, params):
        RPM   = inputs.get("RPM",  1450.0)
        k     = params.get("k_flow", 0.0001)  # m³/s per RPM
        H0    = params.get("H0",     50.0)    # m
        a     = params.get("a_head", 0.001)
        eta   = params.get("eta",    0.75)
        rho   = params.get("rho",    WATER_RHO)

        Q = k * RPM
        H = max(0.0, H0 - a * Q * Q)
        P = rho * GRAVITY * Q * H / max(eta, 0.01)

        # NPSH check for cavitation
        NPSH_req = params.get("NPSH_req", 3.0)
        NPSH_avail = params.get("NPSH_avail", 6.0)

        # Store computed outputs in state derivatives (0 — steady algebraic)
        return {"Q": 0.0, "H": 0.0, "P": 0.0,
                "cavitation": 1.0 if NPSH_avail < NPSH_req else 0.0}

    def compute(self, RPM: float, params: Dict[str, float]) -> Dict[str, float]:
        """Direct algebraic computation (not ODE)."""
        k  = params.get("k_flow", 0.0001)
        H0 = params.get("H0",     50.0)
        a  = params.get("a_head", 0.001)
        eta = params.get("eta",   0.75)
        rho = params.get("rho",   WATER_RHO)
        Q = k * RPM
        H = max(0.0, H0 - a * Q * Q)
        P = rho * GRAVITY * Q * H / max(eta, 0.01)
        return {"Q": Q, "H": H, "P_hydraulic": P,
                "cavitation": params.get("NPSH_avail", 6.0) < params.get("NPSH_req", 3.0)}

    def state_names(self): return ["Q", "H", "P"]
    def default_state(self): return {"Q": 0.1, "H": 30.0, "P": 5000.0}
    def default_params(self): return {
        "k_flow": 0.0001, "H0": 50.0, "a_head": 0.001,
        "eta": 0.75, "rho": WATER_RHO, "NPSH_req": 3.0, "NPSH_avail": 6.0
    }


# ─────────────────────────────────────────────────────────────────────────────
# VALVE BLOCK
# First-order dynamics:  τ·dv/dt + v = u
#  → dv/dt = (u − v) / τ
# ─────────────────────────────────────────────────────────────────────────────

class ValveBlock(PhysicsBlock):
    name = "Valve"

    def state_dot(self, state, inputs, params):
        v   = state.get("v",   0.0)   # current valve position [0,1]
        u   = inputs.get("u",  0.5)   # command signal [0,1]
        tau = params.get("tau", 2.0)  # time constant (s)
        # Clamp to [0, 1]
        v = max(0.0, min(1.0, v))
        u = max(0.0, min(1.0, u))
        dv_dt = (u - v) / max(tau, 0.001)
        return {"v": dv_dt}

    def state_names(self): return ["v"]
    def default_state(self): return {"v": 0.5}
    def default_params(self): return {"tau": 2.0}


# ─────────────────────────────────────────────────────────────────────────────
# ELECTRIC MOTOR BLOCK
# Mechanical: J·dω/dt = T_e − T_load − B·ω
# Electrical: V = L·di/dt + R·i + k_e·ω  →  di/dt = (V − R·i − k_e·ω) / L
# ─────────────────────────────────────────────────────────────────────────────

class MotorBlock(PhysicsBlock):
    name = "Motor"

    def state_dot(self, state, inputs, params):
        omega  = state.get("omega", 0.0)   # rad/s
        i_a    = state.get("i_a",   0.0)   # armature current (A)

        V      = inputs.get("V",      220.0)   # voltage (V)
        T_load = inputs.get("T_load",  5.0)    # load torque (N·m)

        J    = params.get("J",    0.1)     # moment of inertia kg·m²
        B    = params.get("B",    0.01)    # damping N·m·s/rad
        R    = params.get("R",    1.5)     # resistance Ω
        L    = params.get("L",    0.05)    # inductance H
        k_e  = params.get("k_e",  0.1)    # back-EMF constant V·s/rad
        k_t  = params.get("k_t",  0.1)    # torque constant N·m/A

        T_e    = k_t * i_a
        domega = (T_e - T_load - B * omega) / max(J, 1e-6)
        di_a   = (V - R * i_a - k_e * omega) / max(L, 1e-6)
        return {"omega": domega, "i_a": di_a}

    def state_names(self): return ["omega", "i_a"]
    def default_state(self): return {"omega": 0.0, "i_a": 0.0}
    def default_params(self): return {
        "J": 0.1, "B": 0.01, "R": 1.5, "L": 0.05, "k_e": 0.1, "k_t": 0.1
    }


# ─────────────────────────────────────────────────────────────────────────────
# BATTERY BLOCK (Equivalent Circuit Model)
# V = V_oc(SOC) − I·R_int
# dSOC/dt = −I / C_nom
# Thermal: dT/dt = (I²·R_int − P_cool) / C_th
# ─────────────────────────────────────────────────────────────────────────────

class BatteryBlock(PhysicsBlock):
    name = "Battery"

    @staticmethod
    def _V_oc(SOC: float) -> float:
        """Open-circuit voltage as linear approximation."""
        return 3.0 + 1.2 * max(0.0, min(1.0, SOC))   # 3.0–4.2 V (Li-ion cell)

    def state_dot(self, state, inputs, params):
        SOC  = state.get("SOC",  0.8)
        T_b  = state.get("T_b",  25.0)

        I_charge  = inputs.get("I_charge",  0.0)   # A (positive = charging)
        I_disch   = inputs.get("I_disch",   0.0)   # A (positive = discharging)

        C_nom = params.get("C_nom",    100.0)   # Ah
        R_int = params.get("R_int",    0.05)    # Ω
        C_th  = params.get("C_th",     500.0)   # J/K
        P_cool= params.get("P_cool",   10.0)    # W cooling power

        I_net = I_charge - I_disch
        V_oc  = self._V_oc(SOC)
        V_t   = V_oc - I_net * R_int           # terminal voltage

        dSOC  = I_net / max(C_nom * 3600, 1.0) # per second
        P_loss= I_net * I_net * R_int
        dT_b  = (P_loss - P_cool) / max(C_th, 1.0)

        return {"SOC": dSOC, "T_b": dT_b, "V_t": 0.0}  # V_t algebraic

    def compute_voltage(self, SOC: float, I: float, R_int: float = 0.05) -> float:
        return self._V_oc(SOC) - I * R_int

    def state_names(self): return ["SOC", "T_b"]
    def default_state(self): return {"SOC": 0.8, "T_b": 25.0}
    def default_params(self): return {
        "C_nom": 100.0, "R_int": 0.05, "C_th": 500.0, "P_cool": 10.0
    }


# ─────────────────────────────────────────────────────────────────────────────
# ELECTRICAL GRID BLOCK
# Frequency deviation: Δf ≈ P_mismatch / (2·H·S_nom) × f_nom
# ─────────────────────────────────────────────────────────────────────────────

class ElectricalBlock(PhysicsBlock):
    name = "Electrical"

    def state_dot(self, state, inputs, params):
        f    = state.get("f", 50.0)         # Hz
        P_gen  = inputs.get("P_gen",  1000.0)  # W generation
        P_load = inputs.get("P_load", 950.0)   # W load
        H      = params.get("H",      5.0)     # inertia constant (s)
        S_nom  = params.get("S_nom",  2000.0)  # nominal apparent power (VA)
        f_nom  = params.get("f_nom",  50.0)    # Hz
        D      = params.get("D",      1.0)     # damping (pu)

        P_mismatch = P_gen - P_load
        df = (P_mismatch - D * (f - f_nom)) / (2.0 * H * S_nom / f_nom + 1e-9)
        return {"f": df}

    def state_names(self): return ["f"]
    def default_state(self): return {"f": 50.0}
    def default_params(self): return {
        "H": 5.0, "S_nom": 2000.0, "f_nom": 50.0, "D": 1.0
    }


# ─────────────────────────────────────────────────────────────────────────────
# RK4 INTEGRATOR
# ─────────────────────────────────────────────────────────────────────────────

def rk4_step(
    block:   PhysicsBlock,
    state:   Dict[str, float],
    inputs:  Dict[str, float],
    params:  Dict[str, float],
    dt:      float,
) -> Dict[str, float]:
    """Single RK4 step for one physics block."""
    def f(s): return block.state_dot(s, inputs, params)

    k1 = f(state)
    k2 = f({k: state[k] + 0.5 * dt * k1.get(k, 0.0) for k in state})
    k3 = f({k: state[k] + 0.5 * dt * k2.get(k, 0.0) for k in state})
    k4 = f({k: state[k] +       dt * k3.get(k, 0.0) for k in state})

    return {
        k: state[k] + (dt / 6.0) * (k1.get(k, 0.0) + 2 * k2.get(k, 0.0)
                                      + 2 * k3.get(k, 0.0) + k4.get(k, 0.0))
        for k in state
    }


def semi_implicit_euler_step(
    block:  PhysicsBlock,
    state:  Dict[str, float],
    inputs: Dict[str, float],
    params: Dict[str, float],
    dt:     float,
) -> Dict[str, float]:
    """Semi-implicit Euler: better stability for stiff systems."""
    deriv = block.state_dot(state, inputs, params)
    return {k: state[k] + dt * deriv.get(k, 0.0) for k in state}


# ─────────────────────────────────────────────────────────────────────────────
# EXTENDED FAULT INJECTION MATRIX  (spec §6)
# ─────────────────────────────────────────────────────────────────────────────

FAULT_CATEGORIES = {
    "sensor_bias":       "Sensor measurement offset",
    "sensor_noise":      "Increased Gaussian noise on sensor",
    "actuator_delay":    "Actuator response delay (+ms)",
    "actuator_stuck":    "Actuator frozen at last position",
    "actuator_partial":  "Actuator limited to fraction of range",
    "network_opc_drop":  "OPC UA packet loss probability",
    "plc_scan_delay":    "PLC scan cycle time increase",
    "sql_historian_gap": "Missing historian data window",
    "physical_heat_leak":"Increased thermal loss coefficient",
    "electrical_v_sag":  "Voltage sag (−N%)",
    "parameter_drift":   "Slow parameter drift over time",
    "time_const_increase":"System time constant increase",
}

@dataclass
class FaultConfig:
    category:   str
    target:     str           # which signal or block
    magnitude:  float         # fault magnitude
    onset_step: int = 0       # step when fault activates
    duration:   int = -1      # −1 = permanent
    rng_seed:   int = 0

    def is_active(self, step: int) -> bool:
        if step < self.onset_step:
            return False
        if self.duration < 0:
            return True
        return step < self.onset_step + self.duration


class FaultInjector:
    """Applies fault configurations to state/input dicts."""

    def __init__(self, faults: List[FaultConfig] = None, seed: int = 0):
        self._faults = faults or []
        self._rng    = random.Random(seed)
        self._step   = 0

    def add_fault(self, f: FaultConfig) -> None:
        self._faults.append(f)

    def apply_sensor(self, readings: Dict[str, float]) -> Dict[str, float]:
        """Apply sensor faults to tag readings."""
        result = readings.copy()
        for fc in self._faults:
            if not fc.is_active(self._step):
                continue
            tag = fc.target
            if tag not in result:
                continue
            if fc.category == "sensor_bias":
                result[tag] += fc.magnitude
            elif fc.category == "sensor_noise":
                result[tag] += self._rng.gauss(0, fc.magnitude)
            elif fc.category == "parameter_drift":
                result[tag] *= (1.0 + fc.magnitude * self._step * 0.0001)
        return result

    def apply_actuator(self, commands: Dict[str, float],
                       last_commands: Dict[str, float]) -> Dict[str, float]:
        """Apply actuator faults to command signals."""
        result = commands.copy()
        for fc in self._faults:
            if not fc.is_active(self._step):
                continue
            tag = fc.target
            if tag not in result:
                continue
            if fc.category == "actuator_stuck":
                result[tag] = last_commands.get(tag, result[tag])
            elif fc.category == "actuator_partial":
                result[tag] *= fc.magnitude   # fraction of range
        return result

    def apply_params(self, params: Dict[str, float]) -> Dict[str, float]:
        """Apply physics parameter faults."""
        result = params.copy()
        for fc in self._faults:
            if not fc.is_active(self._step):
                continue
            key = fc.target
            if key not in result:
                continue
            if fc.category == "physical_heat_leak":
                result[key] *= (1.0 + fc.magnitude)
            elif fc.category == "time_const_increase":
                result[key] *= (1.0 + fc.magnitude)
            elif fc.category == "electrical_v_sag":
                result[key] *= (1.0 - fc.magnitude)
        return result

    def tick(self) -> None:
        self._step += 1

    @property
    def step(self) -> int:
        return self._step


# ─────────────────────────────────────────────────────────────────────────────
# MULTI-BLOCK PLANT SIMULATOR
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SimStep:
    step:          int
    t:             float
    state:         Dict[str, float]
    inputs:        Dict[str, float]
    hard_violation:bool
    fault_active:  bool
    metadata:      Dict[str, Any] = field(default_factory=dict)


class ExtendedPhysicsTwin:
    """
    Multi-block plant simulator with RK4 integration, fault injection,
    and hard constraint checking.
    """

    def __init__(self, seed: int = 0):
        self._blocks: Dict[str, PhysicsBlock] = {
            "thermal":    ThermalBlock(),
            "pump":       PumpBlock(),
            "valve":      ValveBlock(),
            "motor":      MotorBlock(),
            "battery":    BatteryBlock(),
            "electrical": ElectricalBlock(),
        }
        self._states: Dict[str, Dict[str, float]] = {
            name: block.default_state()
            for name, block in self._blocks.items()
        }
        self._params: Dict[str, Dict[str, float]] = {
            name: block.default_params()
            for name, block in self._blocks.items()
        }
        self._injector = FaultInjector(seed=seed)
        self._history:  List[SimStep] = []
        self._rng       = random.Random(seed)
        self._run_count = 0

    # ── Hard constraints ─────────────────────────────────────────────────────

    HARD_LIMITS = {
        "T":     (0.0,  95.0),   # °C
        "f":     (47.5, 52.5),   # Hz
        "SOC":   (0.10,  1.0),
        "v":     (0.0,   1.0),
        "omega": (0.0, 500.0),   # rad/s
    }

    def _check_violations(self, all_state: Dict[str, float]) -> bool:
        for var, (lo, hi) in self.HARD_LIMITS.items():
            val = all_state.get(var)
            if val is not None and (val < lo or val > hi):
                return True
        return False

    # ── Simulation ───────────────────────────────────────────────────────────

    def step(self, inputs: Dict[str, float], dt: float = 0.1,
             method: str = "rk4") -> SimStep:
        """Advance all physics blocks by dt seconds."""
        integrate = rk4_step if method == "rk4" else semi_implicit_euler_step
        faulty_inputs  = self._injector.apply_sensor(inputs)
        last_cmds: Dict[str, float] = {}
        faulty_inputs  = self._injector.apply_actuator(faulty_inputs, last_cmds)

        new_states: Dict[str, Dict[str, float]] = {}
        for name, block in self._blocks.items():
            faulty_params = self._injector.apply_params(self._params[name])
            new_states[name] = integrate(block, self._states[name],
                                          faulty_inputs, faulty_params, dt)
        self._states = new_states
        self._injector.tick()

        flat = {f"{blk}_{k}": v
                for blk, s in self._states.items() for k, v in s.items()}
        violation = self._check_violations(
            {k: v for s in self._states.values() for k, v in s.items()}
        )
        t   = len(self._history) * dt
        ss  = SimStep(
            step=len(self._history), t=t, state=flat, inputs=faulty_inputs,
            hard_violation=violation,
            fault_active=any(fc.is_active(self._injector.step)
                             for fc in self._injector._faults),
        )
        self._history.append(ss)
        return ss

    def run_episode(self, n_steps: int = 200, dt: float = 0.1,
                    inputs_fn: Callable = None,
                    faults: List[FaultConfig] = None) -> Dict[str, Any]:
        """Run full episode. Returns summary metrics."""
        self._run_count += 1
        self._injector  = FaultInjector(
            faults=faults or [], seed=self._rng.randint(0, 9999)
        )
        self._history = []
        # Reset states
        self._states = {n: b.default_state() for n, b in self._blocks.items()}

        violations = 0
        for i in range(n_steps):
            inp = inputs_fn(i) if inputs_fn else {
                "Q_in": 1000.0 + self._rng.gauss(0, 50),
                "RPM":  1450.0,
                "u":    0.6,
                "V":    220.0,
                "T_load": 5.0,
                "I_charge": 10.0, "I_disch": 5.0,
                "P_gen": 1000.0,  "P_load": 950.0,
            }
            ss = self.step(inp, dt)
            if ss.hard_violation:
                violations += 1

        survive_rate = 1.0 - violations / max(n_steps, 1)
        final_T   = self._states["thermal"].get("T", 25.0)
        final_SOC = self._states["battery"].get("SOC", 0.8)
        return {
            "steps":         n_steps,
            "violations":    violations,
            "survive_rate":  round(survive_rate, 4),
            "final_T":       round(final_T, 3),
            "final_SOC":     round(final_SOC, 4),
            "fault_count":   len(faults or []),
            "promoted":      violations == 0 and survive_rate >= 0.95,
        }

    def evaluate_mutation(
        self,
        param_deltas: Dict[str, float],
        n_runs: int = 5,
        fault_categories: List[str] = None,
    ) -> Tuple[bool, Dict[str, Any]]:
        """
        Evaluate a parameter mutation across n_runs Monte Carlo episodes.
        Returns (promote, metrics_dict).
        Promotion requires: zero hard violations across ALL runs.
        """
        fault_categories = fault_categories or list(FAULT_CATEGORIES.keys())
        all_results = []

        for run_i in range(n_runs):
            # Apply deltas to a copy of params
            saved = {blk: dict(p) for blk, p in self._params.items()}
            for key, delta in param_deltas.items():
                for blk in self._params:
                    if key in self._params[blk]:
                        self._params[blk][key] *= (1.0 + delta)

            faults = []
            cat = fault_categories[run_i % len(fault_categories)]
            targets_by_cat = {
                "sensor_bias":        ("Q_in",   3.0),
                "actuator_stuck":     ("u",       0.0),
                "physical_heat_leak": ("h_conv",  0.1),
                "electrical_v_sag":   ("V",       0.08),
                "time_const_increase":("tau",     0.2),
            }
            if cat in targets_by_cat:
                tgt, mag = targets_by_cat[cat]
                faults = [FaultConfig(cat, tgt, mag, onset_step=10)]

            result = self.run_episode(n_steps=100, faults=faults)
            all_results.append(result)

            # Restore params
            self._params = saved

        mean_survive = sum(r["survive_rate"] for r in all_results) / n_runs
        total_viol   = sum(r["violations"]   for r in all_results)
        promoted     = total_viol == 0 and mean_survive >= 0.95

        return promoted, {
            "promoted":       promoted,
            "n_runs":         n_runs,
            "mean_survive":   round(mean_survive, 4),
            "total_violations": total_viol,
            "run_details":    all_results,
        }

    def add_fault(self, fault: FaultConfig) -> None:
        self._injector.add_fault(fault)

    def get_stats(self) -> dict:
        return {
            "run_count":     self._run_count,
            "history_steps": len(self._history),
            "blocks":        list(self._blocks.keys()),
            "fault_categories": list(FAULT_CATEGORIES.keys()),
            "hard_limits":   {k: list(v) for k, v in self.HARD_LIMITS.items()},
        }
