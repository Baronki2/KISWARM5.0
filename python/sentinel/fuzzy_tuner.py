"""
KISWARM v3.0 — MODULE A: FUZZY MEMBERSHIP AUTO-TUNING
=======================================================
Implements parameterized fuzzy membership functions with online
adaptation via constrained gradient descent and Lyapunov stability
preservation. Applied to KISWARM's confidence scoring pipeline
so the system automatically calibrates knowledge quality thresholds.

Mathematics:
  Gaussian:       μ(x; c, σ) = exp(−(x−c)² / 2σ²)
  Generalized Bell: μ(x; a, b, c) = 1 / (1 + |((x−c)/a)|^(2b))

  Performance cost:  J = α·E_tracking + β·E_energy + γ·E_oscillation
  Gradient descent:  θ_{t+1} = θ_t − η·∇_θ·J
  Projection:        θ_{t+1} = clip(θ_{t+1}, θ_min, θ_max)
  Lyapunov check:    reject if V(x_{t+1}) − V(x_t) > 0

  Evolutionary micro-mutation:
    θ' = θ + ε
    Accept if: J improved AND no violations AND stability preserved

In KISWARM context:
  x         = incoming confidence score (0.0–1.0)
  c, σ, a, b = auto-tuned parameters (start as hand-tuned)
  μ output   = fuzzy membership → quality classification label
  J          = error vs validated outcomes + oscillation penalty

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
from typing import Optional

logger = logging.getLogger("sentinel.fuzzy_tuner")

KISWARM_HOME = os.environ.get("KISWARM_HOME", os.path.expanduser("~"))
KISWARM_DIR  = os.path.join(KISWARM_HOME, "KISWARM")
FUZZY_STORE  = os.path.join(KISWARM_DIR, "fuzzy_params.json")


# ── Membership Functions ──────────────────────────────────────────────────────

def gaussian_membership(x: float, c: float, sigma: float) -> float:
    """
    μ(x; c, σ) = exp(−(x−c)² / 2σ²)
    c     = center of the fuzzy set
    sigma = spread (larger → wider bell)
    """
    if sigma <= 0:
        return 1.0 if x == c else 0.0
    return math.exp(-((x - c) ** 2) / (2 * sigma ** 2))


def generalized_bell_membership(x: float, a: float, b: float, c: float) -> float:
    """
    μ(x; a, b, c) = 1 / (1 + |((x−c)/a)|^(2b))
    a = width parameter
    b = slope parameter (sharpness)
    c = center
    """
    if a <= 0:
        return 1.0 if x == c else 0.0
    ratio = abs((x - c) / a)
    exponent = 2 * b
    # Clamp to avoid math overflow on extreme values
    clamped = min(ratio, 1e6)
    return 1.0 / (1.0 + clamped ** exponent)


# ── Parameter Bounds ──────────────────────────────────────────────────────────

@dataclass
class FuzzyBounds:
    """Constrained parameter space — prevents drift outside physical meaning."""
    c_min:     float = 0.0
    c_max:     float = 1.0
    sigma_min: float = 0.01
    sigma_max: float = 0.5
    a_min:     float = 0.01
    a_max:     float = 0.5
    b_min:     float = 0.5
    b_max:     float = 5.0

    def clip_gaussian(self, c: float, sigma: float) -> tuple[float, float]:
        c     = max(self.c_min, min(self.c_max, c))
        sigma = max(self.sigma_min, min(self.sigma_max, sigma))
        return c, sigma

    def clip_bell(self, a: float, b: float, c: float) -> tuple[float, float, float]:
        a = max(self.a_min, min(self.a_max, a))
        b = max(self.b_min, min(self.b_max, b))
        c = max(self.c_min, min(self.c_max, c))
        return a, b, c


# ── Fuzzy Set Parameters ──────────────────────────────────────────────────────

@dataclass
class GaussianParams:
    c:     float = 0.5   # center
    sigma: float = 0.15  # spread
    label: str   = "medium"

    def membership(self, x: float) -> float:
        return gaussian_membership(x, self.c, self.sigma)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class BellParams:
    a:     float = 0.2   # width
    b:     float = 2.0   # slope
    c:     float = 0.5   # center
    label: str   = "medium"

    def membership(self, x: float) -> float:
        return generalized_bell_membership(x, self.a, self.b, self.c)

    def to_dict(self) -> dict:
        return asdict(self)


# ── Lyapunov Energy Function ──────────────────────────────────────────────────

class LyapunovMonitor:
    """
    Tracks the system's closed-loop energy V(x).
    In KISWARM context:
      V(x) = weighted sum of recent classification errors² + oscillation term
    Rejects parameter updates that increase system energy.
    """

    def __init__(self, window: int = 20):
        self._errors:  list[float] = []
        self._window   = window

    def record_error(self, error: float):
        self._errors.append(abs(error))
        if len(self._errors) > self._window:
            self._errors.pop(0)

    def energy(self) -> float:
        """V(x) = mean squared error over recent window."""
        if not self._errors:
            return 0.0
        return sum(e ** 2 for e in self._errors) / len(self._errors)

    def is_stable(self, candidate_errors: list[float]) -> bool:
        """
        Lyapunov condition: V(x_{t+1}) - V(x_t) ≤ 0
        Returns True if candidate parameters do NOT increase energy.
        """
        v_current = self.energy()
        if not candidate_errors:
            return True
        v_candidate = sum(e ** 2 for e in candidate_errors) / len(candidate_errors)
        stable = (v_candidate - v_current) <= 0.0
        if not stable:
            logger.info(
                "Lyapunov rejection: V_candidate=%.4f > V_current=%.4f",
                v_candidate, v_current,
            )
        return stable


# ── Performance Cost Function ─────────────────────────────────────────────────

@dataclass
class CostWeights:
    alpha: float = 0.5   # tracking error weight
    beta:  float = 0.3   # energy penalty weight
    gamma: float = 0.2   # oscillation penalty weight


def compute_cost(
    errors:       list[float],
    actuations:   list[float],
    weights:      CostWeights = CostWeights(),
) -> float:
    """
    J = α·E_tracking + β·E_energy + γ·E_oscillation

    tracking error  = mean |setpoint − output|
    energy penalty  = mean |actuation| (actuator cost)
    oscillation     = mean |derivative| of errors
    """
    if not errors:
        return 0.0

    e_tracking   = sum(abs(e) for e in errors) / len(errors)
    e_energy     = sum(abs(a) for a in actuations) / max(len(actuations), 1)

    # Oscillation: successive differences
    if len(errors) > 1:
        diffs      = [abs(errors[i+1] - errors[i]) for i in range(len(errors)-1)]
        e_oscillation = sum(diffs) / len(diffs)
    else:
        e_oscillation = 0.0

    return (weights.alpha * e_tracking
            + weights.beta * e_energy
            + weights.gamma * e_oscillation)


# ── Gradient Estimation (Finite Differences) ──────────────────────────────────

def numerical_gradient(
    param_fn,
    params:   list[float],
    errors:   list[float],
    actuations: list[float],
    delta:    float = 1e-4,
    weights:  CostWeights = CostWeights(),
) -> list[float]:
    """
    Estimate ∇_θ J via central finite differences.
    ∂J/∂θ_i ≈ (J(θ+δe_i) − J(θ−δe_i)) / 2δ
    """
    base_cost = compute_cost(errors, actuations, weights)
    gradients = []

    for i in range(len(params)):
        p_plus  = params.copy()
        p_minus = params.copy()
        p_plus[i]  += delta
        p_minus[i] -= delta

        errors_plus  = param_fn(p_plus,  errors)
        errors_minus = param_fn(p_minus, errors)

        cost_plus  = compute_cost(errors_plus,  actuations, weights)
        cost_minus = compute_cost(errors_minus, actuations, weights)
        gradients.append((cost_plus - cost_minus) / (2 * delta))

    return gradients


# ── Fuzzy Auto-Tuner ──────────────────────────────────────────────────────────

class FuzzyAutoTuner:
    """
    Auto-tunes fuzzy membership function parameters using:
      1. Constrained gradient descent (with parameter bounds)
      2. Lyapunov stability check (reject if energy increases)
      3. Evolutionary micro-mutations (safer for non-linear spaces)

    KISWARM Application:
      Confidence scores from scouts are classified through fuzzy sets:
        LOW (c≈0.25)  → needs revalidation
        MEDIUM (c≈0.5) → acceptable
        HIGH (c≈0.75) → trusted
        ELITE (c≈0.9) → authoritative

      The tuner adjusts these boundaries based on validation outcomes —
      if "HIGH" knowledge is consistently wrong, the threshold is tightened.

    Usage:
        tuner = FuzzyAutoTuner()

        # Classify incoming confidence
        label, mu = tuner.classify(0.72)

        # After validation, feed outcome back for tuning
        tuner.update(confidence=0.72, actual_quality=True, actuation=0.1)

        # Run full tuning cycle
        tuner.tune_cycle()
    """

    def __init__(
        self,
        store_path: str          = FUZZY_STORE,
        learning_rate: float     = 0.01,
        mutation_scale: float    = 0.02,
        use_gradient: bool       = False,   # safer: evolutionary by default
        bounds: FuzzyBounds      = None,
        weights: CostWeights     = None,
    ):
        self._store     = store_path
        self._lr        = learning_rate
        self._mu_scale  = mutation_scale
        self._use_grad  = use_gradient
        self._bounds    = bounds or FuzzyBounds()
        self._weights   = weights or CostWeights()
        self._lyapunov  = LyapunovMonitor()

        # Default fuzzy sets: LOW / MEDIUM / HIGH / ELITE
        self.sets: list[GaussianParams] = [
            GaussianParams(c=0.20, sigma=0.10, label="LOW"),
            GaussianParams(c=0.45, sigma=0.12, label="MEDIUM"),
            GaussianParams(c=0.70, sigma=0.10, label="HIGH"),
            GaussianParams(c=0.90, sigma=0.08, label="ELITE"),
        ]

        # Experience buffer
        self._errors:    list[float] = []
        self._actuations: list[float] = []
        self._tuning_log: list[dict]  = []
        self._iterations: int = 0
        self._improvements: int = 0

        self._load()

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load(self):
        if os.path.exists(self._store):
            try:
                with open(self._store) as f:
                    raw = json.load(f)
                for i, s in enumerate(raw.get("sets", [])):
                    if i < len(self.sets):
                        self.sets[i] = GaussianParams(**s)
                self._iterations  = raw.get("iterations", 0)
                self._improvements = raw.get("improvements", 0)
                logger.info("Fuzzy params loaded: %d sets, %d iterations", len(self.sets), self._iterations)
            except (json.JSONDecodeError, TypeError, OSError) as exc:
                logger.warning("Fuzzy load failed: %s", exc)

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self._store), exist_ok=True)
            with open(self._store, "w") as f:
                json.dump({
                    "sets":         [s.to_dict() for s in self.sets],
                    "iterations":   self._iterations,
                    "improvements": self._improvements,
                    "last_updated": time.strftime("%Y-%m-%dT%H:%M:%S"),
                }, f, indent=2)
        except OSError as exc:
            logger.error("Fuzzy save failed: %s", exc)

    # ── Classification ────────────────────────────────────────────────────────

    def classify(self, x: float) -> tuple[str, float]:
        """
        Return (label, max_membership) for input x.
        Fuzzifies the input and returns the dominant set.
        """
        best_label  = "LOW"
        best_mu     = 0.0
        for fs in self.sets:
            mu = fs.membership(x)
            if mu > best_mu:
                best_mu    = mu
                best_label = fs.label
        return best_label, round(best_mu, 4)

    def all_memberships(self, x: float) -> dict[str, float]:
        """Return membership in ALL sets (not just dominant)."""
        return {fs.label: round(fs.membership(x), 4) for fs in self.sets}

    # ── Experience Buffer ─────────────────────────────────────────────────────

    def update(self, confidence: float, actual_quality: bool, actuation: float = 0.0):
        """
        Record a classification outcome for online tuning.

        Args:
            confidence:     The confidence score (0.0–1.0) that was classified.
            actual_quality: True if knowledge turned out to be correct.
            actuation:      Cost of the action taken (0.0 if no action).
        """
        label, mu = self.classify(confidence)
        # Tracking error: if classified HIGH/ELITE but wrong → large error
        quality_score = 1.0 if actual_quality else 0.0
        error = abs(mu - quality_score)
        self._errors.append(error)
        self._actuations.append(abs(actuation))
        self._lyapunov.record_error(error)

        # Keep buffer bounded
        if len(self._errors) > 200:
            self._errors.pop(0)
            self._actuations.pop(0)

    # ── Evolutionary Micro-Mutation ───────────────────────────────────────────

    def _mutate_sets(self) -> list[GaussianParams]:
        """
        θ' = θ + ε  where ε ~ N(0, mutation_scale)
        Returns candidate parameter set (does not apply yet).
        """
        candidates = []
        for fs in self.sets:
            noise_c     = random.gauss(0, self._mu_scale)
            noise_sigma = random.gauss(0, self._mu_scale * 0.5)
            c_new, sigma_new = self._bounds.clip_gaussian(
                fs.c + noise_c,
                fs.sigma + noise_sigma,
            )
            candidates.append(GaussianParams(c=c_new, sigma=sigma_new, label=fs.label))
        return candidates

    def _evaluate_candidate(self, candidates: list[GaussianParams]) -> tuple[float, list[float]]:
        """Compute J and candidate errors if this parameter set were used."""
        if not self._errors:
            return 0.0, []
        # Recompute membership errors under candidate params
        candidate_errors = []
        for e, a in zip(self._errors, self._actuations):
            # Approximate: treat last error as confidence proxy
            approx_conf = 1.0 - e
            best_mu = max(fs.membership(approx_conf) for fs in candidates)
            quality_score = 1.0 if e < 0.3 else 0.0
            candidate_errors.append(abs(best_mu - quality_score))
        cost = compute_cost(candidate_errors, self._actuations, self._weights)
        return cost, candidate_errors

    def tune_cycle(self, n_mutations: int = 5) -> dict:
        """
        Run one auto-tuning cycle:
          1. Generate n_mutations candidate parameter sets
          2. Evaluate each via cost function J
          3. Lyapunov stability check on best candidate
          4. Accept if improved + stable; save; return report

        Returns tuning report dict.
        """
        self._iterations += 1

        current_cost, _ = self._evaluate_candidate(self.sets)

        best_cost       = current_cost
        best_candidates = None
        best_errors     = []

        for _ in range(n_mutations):
            candidates = self._mutate_sets()
            cost, cand_errors = self._evaluate_candidate(candidates)
            if cost < best_cost:
                best_cost       = cost
                best_candidates = candidates
                best_errors     = cand_errors

        if best_candidates is None:
            self._save()   # persist iteration counter even on no-improvement
            return {
                "accepted": False,
                "reason":   "no_improvement",
                "cost":     current_cost,
                "iterations": self._iterations,
            }

        # Lyapunov stability check
        if not self._lyapunov.is_stable(best_errors):
            self._save()   # persist iteration counter even on rejection
            return {
                "accepted": False,
                "reason":   "lyapunov_rejected",
                "cost":     current_cost,
                "iterations": self._iterations,
            }

        # Accept mutation
        improvement = (current_cost - best_cost) / max(current_cost, 1e-9)
        self.sets = best_candidates
        self._improvements += 1
        self._save()

        report = {
            "accepted":    True,
            "reason":      "improved_and_stable",
            "cost_before": round(current_cost, 5),
            "cost_after":  round(best_cost, 5),
            "improvement": round(improvement * 100, 2),
            "iterations":  self._iterations,
            "improvements": self._improvements,
            "params":      [s.to_dict() for s in self.sets],
        }

        logger.info(
            "Fuzzy tuning accepted: cost %.4f→%.4f (%.1f%% better) | iter=%d",
            current_cost, best_cost, improvement * 100, self._iterations,
        )
        return report

    def get_stats(self) -> dict:
        return {
            "sets":         [s.to_dict() for s in self.sets],
            "iterations":   self._iterations,
            "improvements": self._improvements,
            "buffer_size":  len(self._errors),
            "lyapunov_energy": self._lyapunov.energy(),
            "current_cost": self._evaluate_candidate(self.sets)[0],
        }
