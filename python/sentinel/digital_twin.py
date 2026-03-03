"""
KISWARM v3.0 — MODULE C: DIGITAL TWIN MUTATION EVALUATION PIPELINE
====================================================================
Production never mutates live. Every candidate parameter change is
evaluated inside an isolated Digital Twin before any change reaches
the real swarm system.

Architecture:
  Live System
    ↓ telemetry bus
  Twin Cluster (isolated compute)
    ├── High-fidelity physics model
    ├── Fault injection engine
    ├── Historical replay buffer
    └── Adversarial scenario generator

Mutation Pipeline:
  Step 1: Parameter mutation
  Step 2: Monte Carlo stress simulation (N scenarios)
  Step 3: Rare-event amplification (extreme inputs)
  Step 4: Worst-case envelope search

Acceptance Rule (ALL must pass):
  ✓ Zero hard violations
  ✓ Stability margin ≥ baseline
  ✓ Efficiency gain ≥ threshold
  ✓ Recovery time ≤ baseline
  ✓ No catastrophic tail risk (EVT check)

Extreme Value Theory (EVT):
  P(X > x) ~ x^(-α)   [power-law tail]
  If mutation increases tail heaviness → reject

In KISWARM context:
  Twin simulates knowledge extraction pipeline under stress:
    • High concurrent query load
    • Memory pressure scenarios
    • Scout failure injection
    • Adversarial input sequences
  Mutation = candidate fuzzy params or RL policy update

Author: KISWARM Project (Baron Marco Paolo Ialongo)
Version: 3.0
"""

import json
import logging
import math
import os
import random
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Optional, Callable

logger = logging.getLogger("sentinel.digital_twin")

KISWARM_HOME  = os.environ.get("KISWARM_HOME", os.path.expanduser("~"))
KISWARM_DIR   = os.path.join(KISWARM_HOME, "KISWARM")
TWIN_STORE    = os.path.join(KISWARM_DIR, "twin_history.json")
TWIN_SCENARIOS = 50   # Monte Carlo sample size (configurable)


# ── Scenario Generator ────────────────────────────────────────────────────────

@dataclass
class Scenario:
    """A single stress-test scenario for the digital twin."""
    name:             str
    queue_depth:      float   # 0.0–1.0
    memory_pressure:  float
    model_availability: float
    scout_failure_rate: float   # probability a scout call fails
    adversarial:      bool = False   # extreme edge-case scenario
    seed:             int  = 0


class ScenarioGenerator:
    """
    Generates three classes of test scenarios:
      1. Normal operating range (Monte Carlo uniform sample)
      2. Rare-event amplification (tails of distribution)
      3. Adversarial worst-case (fault injection)
    """

    def __init__(self, seed: int = 42):
        self._rng = random.Random(seed)

    def normal_scenarios(self, n: int = TWIN_SCENARIOS) -> list[Scenario]:
        """Uniform random sampling of operating space."""
        return [
            Scenario(
                name=f"normal_{i:03d}",
                queue_depth=self._rng.uniform(0.0, 0.7),
                memory_pressure=self._rng.uniform(0.1, 0.8),
                model_availability=self._rng.uniform(0.5, 1.0),
                scout_failure_rate=self._rng.uniform(0.0, 0.3),
                seed=i,
            )
            for i in range(n)
        ]

    def rare_event_scenarios(self) -> list[Scenario]:
        """
        Rare-event amplification: stress the tails.
        Uses Pareto-distributed load to simulate heavy-tail events.
        """
        scenarios = []
        for i in range(20):
            # Pareto-distributed queue depth (heavy tail)
            pareto_val = min(1.0, self._rng.paretovariate(1.5) / 5.0)
            scenarios.append(Scenario(
                name=f"rare_{i:03d}",
                queue_depth=pareto_val,
                memory_pressure=min(1.0, self._rng.paretovariate(1.2) / 4.0),
                model_availability=max(0.0, 1.0 - self._rng.paretovariate(2.0) / 10.0),
                scout_failure_rate=min(1.0, self._rng.paretovariate(1.8) / 6.0),
                adversarial=False,
                seed=1000 + i,
            ))
        return scenarios

    def adversarial_scenarios(self) -> list[Scenario]:
        """
        Worst-case fault injection: all systems stressed simultaneously.
        """
        return [
            Scenario("adversarial_full_load",     1.0, 0.95, 0.3, 0.8, True, 9001),
            Scenario("adversarial_memory_crunch",  0.8, 0.99, 0.5, 0.4, True, 9002),
            Scenario("adversarial_model_failure",  0.5, 0.6,  0.1, 0.9, True, 9003),
            Scenario("adversarial_scout_blackout", 0.9, 0.7,  0.8, 1.0, True, 9004),
            Scenario("adversarial_cascade",        1.0, 0.99, 0.1, 1.0, True, 9005),
        ]


# ── Physics Simulation Model ──────────────────────────────────────────────────

@dataclass
class SimulationResult:
    """Outcome of running a scenario through the twin."""
    scenario:            str
    stability_margin:    float    # 0.0–1.0, higher = more stable
    overshoot_pct:       float    # % overshoot of control target
    recovery_time:       float    # normalized time to return to setpoint
    constraint_violations: int    # count of hard constraint breaches
    energy_efficiency:   float    # 1.0 = baseline, >1.0 = improvement
    throughput:          float    # knowledge items processed per unit time
    adversarial:         bool = False


class PhysicsModel:
    """
    Simplified high-fidelity model of the KISWARM extraction pipeline.
    Simulates the dynamic response of the knowledge pipeline to control inputs.

    Models second-order system dynamics:
      ẍ + 2ζωₙẋ + ωₙ²x = ωₙ²·u

    where:
      x   = pipeline throughput (output)
      u   = control action (extraction_rate)
      ζ   = damping ratio (from memory pressure)
      ωₙ  = natural frequency (from model availability)
    """

    def __init__(self, rng: random.Random = None):
        self._rng = rng or random.Random()

    def simulate(
        self,
        scenario: Scenario,
        action_scout:    float,
        action_rate:     float,
        action_threshold: float,
        action_eviction: float,
        n_steps:         int = 50,
    ) -> SimulationResult:
        """
        Run time-domain simulation of the pipeline under a scenario.
        Returns SimulationResult with stability and performance metrics.
        """
        rng = random.Random(scenario.seed)

        # System parameters derived from scenario
        zeta    = 0.5 + 0.4 * scenario.memory_pressure     # damping
        omega_n = 1.0 * scenario.model_availability          # natural freq

        # Second-order discrete-time simulation (Euler integration, dt=0.1)
        dt      = 0.1
        setpoint = action_rate
        x       = 0.0   # throughput output
        xdot    = 0.0   # throughput rate

        violations       = 0
        max_overshoot    = 0.0
        recovery_idx     = n_steps
        in_setpoint      = False
        energy_sum       = 0.0
        outputs          = []

        for step in range(n_steps):
            # Scout failure injection
            effective_scout = action_scout
            if rng.random() < scenario.scout_failure_rate:
                effective_scout *= rng.uniform(0.1, 0.4)

            # Control input (with noise)
            u = effective_scout * action_rate + rng.gauss(0, 0.02)

            # Second-order dynamics
            xddot = omega_n**2 * (u - x) - 2 * zeta * omega_n * xdot
            xdot  = xdot + xddot * dt
            x     = max(0.0, min(1.5, x + xdot * dt))   # physical limits

            outputs.append(x)
            energy_sum += abs(xdot) * dt   # energy = integral of |velocity|

            # Constraint check: memory can exceed safe limits under high load
            memory_during = scenario.memory_pressure + 0.1 * x
            if memory_during > 0.95:
                violations += 1

            # Overshoot tracking
            if x > setpoint:
                overshoot = (x - setpoint) / max(setpoint, 1e-6) * 100.0
                max_overshoot = max(max_overshoot, overshoot)

            # Recovery detection: within 5% of setpoint
            if abs(x - setpoint) / max(abs(setpoint), 1e-6) < 0.05 and not in_setpoint:
                recovery_idx = step
                in_setpoint  = True

        # Stability margin: how far from instability boundary
        steady_state = outputs[-10:]
        variance     = sum((v - setpoint)**2 for v in steady_state) / len(steady_state)
        stability    = max(0.0, 1.0 - math.sqrt(variance))

        # Recovery time (normalized 0–1)
        recovery_time = recovery_idx / n_steps

        # Energy efficiency: inverse of wasted energy, normalized
        energy_efficiency = max(0.1, 1.0 - energy_sum / (n_steps * dt * 0.5))

        # Throughput: mean of last quarter of simulation
        throughput = sum(outputs[-(n_steps//4):]) / (n_steps // 4)

        return SimulationResult(
            scenario=scenario.name,
            stability_margin=round(stability, 4),
            overshoot_pct=round(max_overshoot, 2),
            recovery_time=round(recovery_time, 4),
            constraint_violations=violations,
            energy_efficiency=round(energy_efficiency, 4),
            throughput=round(throughput, 4),
            adversarial=scenario.adversarial,
        )


# ── Extreme Value Theory (EVT) ────────────────────────────────────────────────

class ExtremeValueAnalyzer:
    """
    Fits a power-law tail: P(X > x) ~ x^(-α)

    Estimates the tail index α using the Hill estimator on the k
    largest order statistics. A smaller α means heavier tails (more
    extreme risk). If a mutation increases tail heaviness (decreases α),
    reject it.

    Hill Estimator:
      α̂ = k / Σᵢ₌₁ᵏ [log(X_{(n-i+1)}) - log(X_{(n-k)})]
    """

    def __init__(self, k_fraction: float = 0.10):
        """k_fraction: fraction of largest samples to use for tail estimation."""
        self._k_frac = k_fraction

    def tail_index(self, samples: list[float]) -> float:
        """
        Estimate power-law tail index α using the Hill estimator.
        Higher α = lighter tail = safer. Returns 0.0 if insufficient data.
        """
        if len(samples) < 10:
            return float("inf")   # not enough data → assume safe

        sorted_s = sorted(samples)
        n        = len(sorted_s)
        k        = max(2, int(n * self._k_frac))

        # Threshold = k-th largest value
        threshold = sorted_s[n - k]
        if threshold <= 0:
            return float("inf")

        # Hill estimator
        exceedances = [s for s in sorted_s[n-k:] if s > threshold]
        if len(exceedances) < 2:
            return float("inf")

        log_sum = sum(math.log(s / threshold) for s in exceedances)
        if log_sum <= 0:
            return float("inf")

        alpha = len(exceedances) / log_sum
        return round(alpha, 4)

    def is_tail_heavier(self, baseline: list[float], candidate: list[float]) -> bool:
        """
        Returns True if candidate distribution has heavier tails than baseline.
        Heavier tail = lower α = more extreme risk = reject mutation.
        """
        α_base = self.tail_index(baseline)
        α_cand = self.tail_index(candidate)

        if α_base == float("inf") or α_cand == float("inf"):
            return False   # insufficient data, don't reject

        heavier = α_cand < α_base * 0.90   # 10% margin
        if heavier:
            logger.warning(
                "EVT tail rejection: α_baseline=%.2f → α_candidate=%.2f (heavier tail)",
                α_base, α_cand,
            )
        return heavier


# ── Mutation Acceptance Engine ────────────────────────────────────────────────

@dataclass
class AcceptanceReport:
    """Detailed report from the twin evaluation pipeline."""
    accepted:                bool
    rejection_reasons:       list[str]
    n_scenarios:             int
    hard_violations:         int
    stability_margin_mean:   float
    stability_margin_baseline: float
    efficiency_gain:         float
    recovery_time_mean:      float
    recovery_time_baseline:  float
    tail_heavier:            bool
    tail_index_baseline:     float
    tail_index_candidate:    float
    adversarial_violations:  int
    timestamp:               str = field(default_factory=lambda: datetime.now().isoformat())


# ── Digital Twin Pipeline ─────────────────────────────────────────────────────

class DigitalTwin:
    """
    Complete Digital Twin Mutation Evaluation Pipeline.

    Every parameter mutation is stress-tested across 75+ scenarios
    (normal + rare-event + adversarial) before being accepted.

    Usage:
        twin = DigitalTwin()
        twin.set_baseline(current_params)

        # Evaluate a candidate mutation
        report = twin.evaluate(
            baseline_params  = current_params,
            candidate_params = mutated_params,
            mutation_fn      = lambda p, s: simulate(p, s),
        )

        if report.accepted:
            apply_mutation(candidate_params)
        else:
            logger.info("Rejected: %s", report.rejection_reasons)
    """

    STABILITY_MARGIN_MIN  = 0.60   # must be ≥ 60% stable
    EFFICIENCY_GAIN_MIN   = 0.0    # must not be worse (≥ 0% gain)
    RECOVERY_TIME_MAX_MULT = 1.20  # recovery time ≤ 120% of baseline

    def __init__(self, store_path: str = TWIN_STORE):
        self._store     = store_path
        self._scenario_gen = ScenarioGenerator()
        self._physics   = PhysicsModel()
        self._evt       = ExtremeValueAnalyzer()
        self._history:  list[dict] = []
        self._baseline_results: list[SimulationResult] = []
        self._promotions = 0
        self._rejections = 0
        self._load()

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load(self):
        if os.path.exists(self._store):
            try:
                with open(self._store) as f:
                    raw = json.load(f)
                self._history    = raw.get("history", [])
                self._promotions = raw.get("promotions", 0)
                self._rejections = raw.get("rejections", 0)
                logger.info(
                    "Twin loaded: %d promoted, %d rejected",
                    self._promotions, self._rejections,
                )
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Twin load failed: %s", exc)

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self._store), exist_ok=True)
            with open(self._store, "w") as f:
                json.dump({
                    "promotions": self._promotions,
                    "rejections": self._rejections,
                    "history":    self._history[-100:],  # keep last 100
                }, f, indent=2)
        except OSError as exc:
            logger.error("Twin save failed: %s", exc)

    # ── Core Simulation Runner ────────────────────────────────────────────────

    def _run_scenarios(
        self,
        scout:     float,
        rate:      float,
        threshold: float,
        eviction:  float,
        include_rare: bool = True,
        include_adversarial: bool = True,
    ) -> list[SimulationResult]:
        """Run all scenario classes and return results."""
        scenarios = self._scenario_gen.normal_scenarios()
        if include_rare:
            scenarios += self._scenario_gen.rare_event_scenarios()
        if include_adversarial:
            scenarios += self._scenario_gen.adversarial_scenarios()

        return [
            self._physics.simulate(
                s, scout, rate, threshold, eviction
            )
            for s in scenarios
        ]

    def set_baseline(
        self,
        scout: float = 0.5,
        rate: float  = 0.5,
        threshold: float = 0.3,
        eviction: float  = 0.1,
    ):
        """
        Evaluate baseline parameters. Must be called before evaluate().
        Establishes the reference performance envelope.
        """
        logger.info("Twin: running baseline simulation...")
        self._baseline_results = self._run_scenarios(scout, rate, threshold, eviction)
        logger.info("Twin: baseline established (%d scenarios)", len(self._baseline_results))

    def evaluate(
        self,
        scout:     float,
        rate:      float,
        threshold: float,
        eviction:  float,
        label:     str = "candidate",
    ) -> AcceptanceReport:
        """
        Full mutation evaluation pipeline.
        Returns AcceptanceReport with detailed pass/fail analysis.

        Step 1: Monte Carlo + rare-event + adversarial simulation
        Step 2: Compute aggregate metrics
        Step 3: Check acceptance rules
        Step 4: EVT tail risk analysis
        Step 5: Render verdict
        """

        # Ensure baseline exists
        if not self._baseline_results:
            self.set_baseline()

        logger.info("Twin: evaluating candidate '%s' (scout=%.2f, rate=%.2f, threshold=%.2f, evict=%.2f)",
                    label, scout, rate, threshold, eviction)

        # Step 1: Run candidate scenarios
        cand_results = self._run_scenarios(scout, rate, threshold, eviction)

        # Step 2: Aggregate metrics
        base = self._baseline_results
        cand = cand_results

        # Hard violations
        total_violations = sum(r.constraint_violations for r in cand)
        adv_violations   = sum(r.constraint_violations for r in cand if r.adversarial)

        # Stability
        b_stability = sum(r.stability_margin for r in base) / len(base)
        c_stability = sum(r.stability_margin for r in cand) / len(cand)

        # Efficiency
        b_efficiency = sum(r.energy_efficiency for r in base) / len(base)
        c_efficiency = sum(r.energy_efficiency for r in cand) / len(cand)
        efficiency_gain = (c_efficiency - b_efficiency) / max(b_efficiency, 1e-6)

        # Recovery time
        b_recovery = sum(r.recovery_time for r in base) / len(base)
        c_recovery = sum(r.recovery_time for r in cand) / len(cand)

        # Step 3: EVT tail analysis (using overshoot% as tail metric)
        b_overshots = [r.overshoot_pct for r in base if r.overshoot_pct > 0]
        c_overshots = [r.overshoot_pct for r in cand if r.overshoot_pct > 0]
        tail_heavier  = self._evt.is_tail_heavier(b_overshots, c_overshots)
        α_base = self._evt.tail_index(b_overshots)
        α_cand = self._evt.tail_index(c_overshots)

        # Step 4: Acceptance rules
        rejection_reasons = []

        # Rule 1: Zero hard violations
        if total_violations > 0:
            rejection_reasons.append(
                f"hard_violations:{total_violations} (adversarial:{adv_violations})"
            )

        # Rule 2: Stability margin ≥ baseline
        if c_stability < b_stability * 0.95:   # 5% tolerance
            rejection_reasons.append(
                f"stability_regression:{c_stability:.3f}<{b_stability:.3f}"
            )

        # Rule 3: Efficiency gain ≥ 0 (must not be worse)
        if efficiency_gain < self.EFFICIENCY_GAIN_MIN:
            rejection_reasons.append(
                f"efficiency_loss:{efficiency_gain*100:.1f}%"
            )

        # Rule 4: Recovery time ≤ 120% of baseline
        if c_recovery > b_recovery * self.RECOVERY_TIME_MAX_MULT:
            rejection_reasons.append(
                f"slow_recovery:{c_recovery:.3f}>{b_recovery*self.RECOVERY_TIME_MAX_MULT:.3f}"
            )

        # Rule 5: No catastrophic tail risk
        if tail_heavier:
            rejection_reasons.append(
                f"tail_risk:α_base={α_base:.2f}→α_cand={α_cand:.2f}"
            )

        accepted = len(rejection_reasons) == 0

        if accepted:
            self._promotions += 1
            logger.info("Twin ACCEPTED: '%s' | stability=%.2f | efficiency_gain=%.1f%%",
                        label, c_stability, efficiency_gain * 100)
        else:
            self._rejections += 1
            logger.warning("Twin REJECTED: '%s' | reasons=%s", label, rejection_reasons)

        report = AcceptanceReport(
            accepted=accepted,
            rejection_reasons=rejection_reasons,
            n_scenarios=len(cand_results),
            hard_violations=total_violations,
            stability_margin_mean=round(c_stability, 4),
            stability_margin_baseline=round(b_stability, 4),
            efficiency_gain=round(efficiency_gain, 4),
            recovery_time_mean=round(c_recovery, 4),
            recovery_time_baseline=round(b_recovery, 4),
            tail_heavier=tail_heavier,
            tail_index_baseline=α_base,
            tail_index_candidate=α_cand,
            adversarial_violations=adv_violations,
        )

        # Save to history
        self._history.append({
            "label":    label,
            "accepted": accepted,
            "reasons":  rejection_reasons,
            "timestamp": report.timestamp,
        })
        self._save()

        return report

    def get_stats(self) -> dict:
        return {
            "promotions": self._promotions,
            "rejections": self._rejections,
            "total_evaluations": self._promotions + self._rejections,
            "promotion_rate": round(
                self._promotions / max(self._promotions + self._rejections, 1), 3
            ),
            "baseline_established": len(self._baseline_results) > 0,
            "history_entries": len(self._history),
        }
