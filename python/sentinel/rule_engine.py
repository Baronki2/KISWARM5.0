"""
KISWARM v4.0 — Module 14: Rule Constraint Engine
=================================================
Absolute safety layer for the CIEC Cognitive Industrial Evolution Core.

These rules are NON-NEGOTIABLE.
They override every RL output, every fuzzy output, every mutation.
PLC = deterministic reflex layer. AI = adaptive cognition layer.
Never invert that hierarchy.

Architecture:
  - Hard constraints with condition functions and penalty values
  - Soft constraints with configurable penalty weights
  - Action validation: block any parameter mutation that violates constraints
  - Pre-flight check before any value reaches PLC parameter space
  - Audit log of every block event

Hard constraint examples:
  IF pressure > 8 bar        → block pump enable, penalty = 10^6
  IF battery < 15%           → block heater high mode
  IF relay switching > X/min → penalize actuator
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# ── Data Structures ───────────────────────────────────────────────────────────

@dataclass
class ConstraintDefinition:
    """
    A single hard or soft constraint.

    condition(state) → True means VIOLATED.
    When violated:
      - hard=True  → block action entirely, apply penalty=1e6
      - hard=False → apply penalty_weight to RL reward
    """
    name:           str
    condition:      Callable[[dict], bool]
    description:    str          = ""
    hard:           bool         = True       # True = absolute block
    penalty_value:  float        = 1e6        # applied when violated
    category:       str          = "safety"   # safety | operational | energy
    tags:           list[str]    = field(default_factory=list)

    def is_violated(self, state: dict) -> bool:
        """Evaluate condition against plant state. Safe: returns False on error."""
        try:
            return bool(self.condition(state))
        except Exception as exc:
            logger.warning("Constraint '%s' eval error: %s", self.name, exc)
            return False   # fail-open: don't block on evaluation error


@dataclass
class ConstraintViolation:
    """Record of one constraint violation event."""
    constraint_name: str
    timestamp:       float
    state_snapshot:  dict
    action_blocked:  dict
    penalty:         float
    hard:            bool
    category:        str

    def to_dict(self) -> dict:
        return {
            "constraint":  self.constraint_name,
            "timestamp":   self.timestamp,
            "penalty":     self.penalty,
            "hard":        self.hard,
            "category":    self.category,
            "state":       {k: round(v, 4) if isinstance(v, float) else v
                           for k, v in self.state_snapshot.items()},
        }


@dataclass
class ValidationResult:
    """Result of validating one action against all constraints."""
    allowed:           bool
    total_penalty:     float
    hard_violations:   list[str]
    soft_violations:   list[str]
    action_after:      dict        # possibly clamped action
    check_time_us:     float       = 0.0

    @property
    def has_violations(self) -> bool:
        return bool(self.hard_violations or self.soft_violations)

    def to_dict(self) -> dict:
        return {
            "allowed":         self.allowed,
            "total_penalty":   self.total_penalty,
            "hard_violations": self.hard_violations,
            "soft_violations": self.soft_violations,
            "check_time_us":   round(self.check_time_us, 2),
        }


# ── Default Industrial Constraint Library ─────────────────────────────────────

def _build_default_constraints() -> list[ConstraintDefinition]:
    """
    Factory: returns a set of typical industrial hard/soft constraints.
    Override or extend these for specific plant configurations.
    """
    return [
        # ── Pressure Safety ───────────────────────────────────────────────────
        ConstraintDefinition(
            name        = "OVERPRESSURE_BLOCK",
            condition   = lambda s: s.get("pressure", 0.0) > 8.0,
            description = "Block all parameter mutations if pressure exceeds 8 bar",
            hard        = True,
            penalty_value = 1e6,
            category    = "safety",
            tags        = ["pressure", "pump"],
        ),
        ConstraintDefinition(
            name        = "PRESSURE_HIGH_WARN",
            condition   = lambda s: s.get("pressure", 0.0) > 6.5,
            description = "Penalize actions when pressure exceeds 6.5 bar",
            hard        = False,
            penalty_value = 500.0,
            category    = "safety",
            tags        = ["pressure"],
        ),

        # ── Battery / Energy ──────────────────────────────────────────────────
        ConstraintDefinition(
            name        = "BATTERY_CRITICAL_BLOCK",
            condition   = lambda s: s.get("battery_soc", 1.0) < 0.15,
            description = "Block heater high mode when battery SOC < 15%",
            hard        = True,
            penalty_value = 1e6,
            category    = "energy",
            tags        = ["battery", "soc"],
        ),
        ConstraintDefinition(
            name        = "BATTERY_LOW_PENALTY",
            condition   = lambda s: s.get("battery_soc", 1.0) < 0.25,
            description = "Penalize energy-intensive actions when battery < 25%",
            hard        = False,
            penalty_value = 2000.0,
            category    = "energy",
            tags        = ["battery"],
        ),

        # ── Temperature ───────────────────────────────────────────────────────
        ConstraintDefinition(
            name        = "OVERTEMP_BLOCK",
            condition   = lambda s: s.get("temperature", 0.0) > 95.0,
            description = "Block all mutations if temperature exceeds 95°C",
            hard        = True,
            penalty_value = 1e6,
            category    = "safety",
            tags        = ["temperature", "thermal"],
        ),
        ConstraintDefinition(
            name        = "TEMP_HIGH_WARN",
            condition   = lambda s: s.get("temperature", 0.0) > 80.0,
            description = "Penalize heating actions above 80°C",
            hard        = False,
            penalty_value = 800.0,
            category    = "safety",
            tags        = ["temperature"],
        ),

        # ── Actuator Cycling ──────────────────────────────────────────────────
        ConstraintDefinition(
            name        = "RELAY_OVERCYCLING_PENALTY",
            condition   = lambda s: s.get("switching_frequency", 0.0) > 2.0,
            description = "Penalize relay switching > 2 times/second",
            hard        = False,
            penalty_value = 300.0,
            category    = "operational",
            tags        = ["actuator", "relay"],
        ),
        ConstraintDefinition(
            name        = "ACTUATOR_WEAR_BLOCK",
            condition   = lambda s: s.get("actuator_wear_index", 0.0) > 0.95,
            description = "Block parameter changes if actuator wear index > 95%",
            hard        = True,
            penalty_value = 5e5,
            category    = "safety",
            tags        = ["actuator"],
        ),

        # ── Grid / Frequency ──────────────────────────────────────────────────
        ConstraintDefinition(
            name        = "FREQUENCY_DEVIATION_BLOCK",
            condition   = lambda s: abs(s.get("grid_frequency", 50.0) - 50.0) > 2.5,
            description = "Block load changes if grid frequency deviation > 2.5 Hz",
            hard        = True,
            penalty_value = 1e6,
            category    = "energy",
            tags        = ["grid", "frequency"],
        ),

        # ── PID Parameter Bounds ──────────────────────────────────────────────
        ConstraintDefinition(
            name        = "PID_KP_BOUND",
            condition   = lambda s: (s.get("delta_kp", 0.0) is not None and
                                     abs(s.get("delta_kp", 0.0)) > 0.05),
            description = "PID Kp mutation must stay within ±5%",
            hard        = True,
            penalty_value = 1e5,
            category    = "operational",
            tags        = ["pid", "kp"],
        ),
        ConstraintDefinition(
            name        = "PID_KI_BOUND",
            condition   = lambda s: (s.get("delta_ki", 0.0) is not None and
                                     abs(s.get("delta_ki", 0.0)) > 0.05),
            description = "PID Ki mutation must stay within ±5%",
            hard        = True,
            penalty_value = 1e5,
            category    = "operational",
            tags        = ["pid", "ki"],
        ),
    ]


# ── Rule Constraint Engine ────────────────────────────────────────────────────

class RuleConstraintEngine:
    """
    Absolute safety layer for the CIEC.

    Every proposed RL action, fuzzy output, or mutation candidate
    passes through here BEFORE reaching PLC parameter space.

    Hard violations → action is blocked entirely.
    Soft violations → penalty added to RL reward signal.

    Usage:
        engine = RuleConstraintEngine()
        result = engine.validate(plant_state, proposed_action)
        if result.allowed:
            apply_to_plc(result.action_after)
    """

    def __init__(
        self,
        constraints: Optional[list[ConstraintDefinition]] = None,
        store_path:  Optional[str] = None,
    ):
        kiswarm_dir  = os.environ.get("KISWARM_HOME", os.path.expanduser("~/KISWARM"))
        self._store  = store_path or os.path.join(kiswarm_dir, "constraint_log.json")
        self._constraints: list[ConstraintDefinition] = (
            constraints if constraints is not None else _build_default_constraints()
        )
        self._violations:  list[ConstraintViolation]  = []
        self._check_count  = 0
        self._block_count  = 0
        self._load()

    # ── Core Validation ───────────────────────────────────────────────────────

    def validate(
        self,
        state:  dict,
        action: dict,
    ) -> ValidationResult:
        """
        Check proposed action against all constraints.

        Args:
            state:  Current plant state vector (dict of tag→value)
            action: Proposed parameter changes (dict of param→delta)

        Returns:
            ValidationResult with allowed flag and total penalty.
        """
        t0 = time.perf_counter()
        self._check_count += 1

        # Merge state + action for condition evaluation
        combined = {**state, **action}

        hard_violations: list[str] = []
        soft_violations: list[str] = []
        total_penalty    = 0.0

        for constraint in self._constraints:
            if not constraint.is_violated(combined):
                continue

            if constraint.hard:
                hard_violations.append(constraint.name)
                total_penalty += constraint.penalty_value
                self._violations.append(ConstraintViolation(
                    constraint_name = constraint.name,
                    timestamp       = time.time(),
                    state_snapshot  = {k: v for k, v in state.items()
                                       if isinstance(v, (int, float, str, bool))},
                    action_blocked  = action,
                    penalty         = constraint.penalty_value,
                    hard            = True,
                    category        = constraint.category,
                ))
            else:
                soft_violations.append(constraint.name)
                total_penalty += constraint.penalty_value

        allowed = len(hard_violations) == 0

        if not allowed:
            self._block_count += 1
            logger.warning(
                "Action BLOCKED: hard violations=%s | state_keys=%s",
                hard_violations, list(state.keys())[:8],
            )

        # Clamp action to safe ranges even if no hard violation
        clamped_action = self._clamp_action(action)

        check_us = (time.perf_counter() - t0) * 1e6

        # Persist periodically
        if self._check_count % 100 == 0:
            self._save()

        return ValidationResult(
            allowed         = allowed,
            total_penalty   = total_penalty,
            hard_violations = hard_violations,
            soft_violations = soft_violations,
            action_after    = clamped_action if allowed else {},
            check_time_us   = check_us,
        )

    def compute_penalty(self, state: dict, action: dict) -> float:
        """
        Quick path: return total penalty without full validation.
        Used by RL training loop for reward shaping.
        """
        combined = {**state, **action}
        penalty  = 0.0
        for c in self._constraints:
            if c.is_violated(combined):
                penalty += c.penalty_value
        return penalty

    def is_safe_state(self, state: dict) -> bool:
        """
        Quick check: is the current plant state safe?
        Returns False if ANY hard constraint is violated by state alone.
        """
        return not any(
            c.hard and c.is_violated(state)
            for c in self._constraints
        )

    # ── Constraint Management ─────────────────────────────────────────────────

    def add_constraint(self, constraint: ConstraintDefinition) -> None:
        """Register a new constraint at runtime."""
        self._constraints.append(constraint)
        logger.info("Constraint added: %s (hard=%s)", constraint.name, constraint.hard)

    def remove_constraint(self, name: str) -> bool:
        """Remove constraint by name. Returns True if found."""
        before = len(self._constraints)
        self._constraints = [c for c in self._constraints if c.name != name]
        return len(self._constraints) < before

    def get_constraints(self) -> list[dict]:
        return [
            {
                "name":        c.name,
                "description": c.description,
                "hard":        c.hard,
                "penalty":     c.penalty_value,
                "category":    c.category,
                "tags":        c.tags,
            }
            for c in self._constraints
        ]

    # ── Action Clamping ───────────────────────────────────────────────────────

    def _clamp_action(self, action: dict) -> dict:
        """
        Clamp parameter mutations to PLC-safe ranges.
        PID changes: ±5%; threshold changes: ±10%.
        """
        clamped = dict(action)
        plc_bounds = {
            "delta_kp":         (-0.05, 0.05),
            "delta_ki":         (-0.05, 0.05),
            "delta_kd":         (-0.05, 0.05),
            "delta_threshold":  (-0.10, 0.10),
            "delta_schedule":   (-0.20, 0.20),
            "delta_energy_w":   (-0.15, 0.15),
        }
        for key, (lo, hi) in plc_bounds.items():
            if key in clamped:
                clamped[key] = max(lo, min(hi, float(clamped[key])))
        return clamped

    # ── Statistics ────────────────────────────────────────────────────────────

    def get_stats(self) -> dict:
        recent = self._violations[-50:]
        category_counts: dict[str, int] = {}
        for v in self._violations:
            category_counts[v.category] = category_counts.get(v.category, 0) + 1

        return {
            "total_checks":         self._check_count,
            "total_blocks":         self._block_count,
            "block_rate":           round(self._block_count / max(self._check_count, 1), 4),
            "total_violations":     len(self._violations),
            "constraint_count":     len(self._constraints),
            "hard_constraints":     sum(1 for c in self._constraints if c.hard),
            "soft_constraints":     sum(1 for c in self._constraints if not c.hard),
            "violations_by_category": category_counts,
            "recent_violations":    [v.to_dict() for v in recent[-10:]],
        }

    def get_violation_history(self, n: int = 100) -> list[dict]:
        return [v.to_dict() for v in self._violations[-n:]]

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load(self):
        try:
            if os.path.exists(self._store):
                with open(self._store) as f:
                    raw = json.load(f)
                self._check_count = raw.get("check_count", 0)
                self._block_count = raw.get("block_count", 0)
                logger.info("Constraint engine loaded: %d checks, %d blocks",
                            self._check_count, self._block_count)
        except Exception as exc:
            logger.warning("Constraint engine load failed: %s", exc)

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self._store) if os.path.dirname(self._store) else ".", exist_ok=True)
            with open(self._store, "w") as f:
                json.dump({
                    "check_count":  self._check_count,
                    "block_count":  self._block_count,
                    "last_updated": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "violations":   [v.to_dict() for v in self._violations[-200:]],
                }, f, indent=2)
        except Exception as exc:
            logger.error("Constraint engine save failed: %s", exc)
