"""
KISWARM v3.0 — MODULE B: CONSTRAINED REINFORCEMENT LEARNING
============================================================
Implements a Constrained Markov Decision Process (CMDP) for safe
adaptive optimization of swarm behavior. Applied to KISWARM's
knowledge routing and extraction scheduling pipeline.

Mathematics:
  CMDP:   max_π E[R]   subject to:  E[C_i] ≤ d_i  ∀i

  Lagrangian (primal-dual update):
    L(θ, λ) = E[R] − λ(E[C] − d)
    θ_{t+1} = θ_t + η_θ · ∇_θ · L
    λ_{t+1} = max(0, λ_t + η_λ · (E[C] − d))

  Action Masking:
    A_valid(s) = {a | ConstraintEngine(s, a) = True}
    If a ∉ A_valid → project to nearest valid action

  Shielded RL:
    Policy → proposed action
    Safety model predicts next state
    If violation predicted → replace with MPC/baseline fallback

In KISWARM context:
  State:   swarm load, knowledge queue depth, model availability, memory pressure
  Action:  scout_priority, extraction_rate, debate_threshold, cache_eviction_rate
  Reward:  +knowledge_quality, +speed, +coverage_gain
  Constraints: memory_budget ≤ max, API_rate ≤ limit, extraction_latency ≤ SLA

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

logger = logging.getLogger("sentinel.constrained_rl")

KISWARM_HOME = os.environ.get("KISWARM_HOME", os.path.expanduser("~"))
KISWARM_DIR  = os.path.join(KISWARM_HOME, "KISWARM")
RL_STORE     = os.path.join(KISWARM_DIR, "rl_policy.json")


# ── State & Action Spaces ─────────────────────────────────────────────────────

@dataclass
class SwarmState:
    """Observable state of the KISWARM system."""
    knowledge_queue_depth: float = 0.0   # 0.0–1.0 (normalized)
    memory_pressure:       float = 0.0   # 0.0–1.0 (Qdrant fill ratio)
    model_availability:    float = 1.0   # 0.0–1.0 (fraction of models online)
    extraction_latency:    float = 0.0   # 0.0–1.0 (normalized vs SLA)
    scout_success_rate:    float = 0.8   # 0.0–1.0
    debate_load:           float = 0.0   # 0.0–1.0 (concurrent debates)

    def to_vector(self) -> list[float]:
        return [
            self.knowledge_queue_depth,
            self.memory_pressure,
            self.model_availability,
            self.extraction_latency,
            self.scout_success_rate,
            self.debate_load,
        ]

    @classmethod
    def from_vector(cls, v: list[float]) -> "SwarmState":
        fields = ["knowledge_queue_depth", "memory_pressure", "model_availability",
                  "extraction_latency", "scout_success_rate", "debate_load"]
        return cls(**dict(zip(fields, v[:6])))


@dataclass
class SwarmAction:
    """Control action for the swarm's extraction pipeline."""
    scout_priority:      float = 0.5   # 0.0=low, 1.0=aggressive
    extraction_rate:     float = 0.5   # normalized extraction speed
    debate_threshold:    float = 0.3   # conflict similarity below → trigger debate
    cache_eviction_rate: float = 0.1   # fraction of stale entries to evict

    def to_vector(self) -> list[float]:
        return [self.scout_priority, self.extraction_rate,
                self.debate_threshold, self.cache_eviction_rate]

    @classmethod
    def from_vector(cls, v: list[float]) -> "SwarmAction":
        return cls(
            scout_priority=v[0],
            extraction_rate=v[1],
            debate_threshold=v[2],
            cache_eviction_rate=v[3],
        )

    def clamp(self) -> "SwarmAction":
        return SwarmAction(
            scout_priority=max(0.0, min(1.0, self.scout_priority)),
            extraction_rate=max(0.0, min(1.0, self.extraction_rate)),
            debate_threshold=max(0.05, min(0.95, self.debate_threshold)),
            cache_eviction_rate=max(0.0, min(0.5, self.cache_eviction_rate)),
        )


# ── Constraint Engine ─────────────────────────────────────────────────────────

@dataclass
class ConstraintConfig:
    """Hard constraint limits for the KISWARM pipeline."""
    max_memory_pressure:    float = 0.85   # Qdrant must not exceed 85%
    max_extraction_latency: float = 0.80   # SLA: normalized latency < 80%
    min_model_availability: float = 0.30   # at least 30% models must be online
    max_scout_aggression:   float = 0.90   # scout_priority ≤ 0.9
    max_eviction_rate:      float = 0.40   # don't evict more than 40% at once


class ConstraintEngine:
    """
    A_valid(s) = {a | all constraints satisfied for (s, a)}
    Projects invalid actions to the nearest valid boundary.
    """

    def __init__(self, config: ConstraintConfig = None):
        self._cfg = config or ConstraintConfig()

    def is_valid(self, state: SwarmState, action: SwarmAction) -> tuple[bool, list[str]]:
        """Check all constraints. Returns (valid, list_of_violations)."""
        violations = []

        # Memory constraint: high memory pressure → cap eviction to prevent thrashing
        if state.memory_pressure > self._cfg.max_memory_pressure:
            if action.cache_eviction_rate < 0.10:
                violations.append("memory_critical:eviction_too_low")

        # Latency SLA
        if (state.extraction_latency > self._cfg.max_extraction_latency and
                action.extraction_rate > 0.5):
            violations.append(f"latency_sla:rate={action.extraction_rate:.2f}")

        # Model availability: don't run debates if too few models
        if (state.model_availability < self._cfg.min_model_availability and
                action.debate_threshold < 0.6):
            violations.append("model_shortage:debate_threshold_too_low")

        # Hard upper bounds
        if action.scout_priority > self._cfg.max_scout_aggression:
            violations.append(f"scout_aggression:{action.scout_priority:.2f}")

        if action.cache_eviction_rate > self._cfg.max_eviction_rate:
            violations.append(f"eviction_rate:{action.cache_eviction_rate:.2f}")

        return len(violations) == 0, violations

    def project_to_valid(self, state: SwarmState, action: SwarmAction) -> SwarmAction:
        """Project an invalid action to nearest feasible point."""
        a = action.clamp()

        # Memory safety projection
        if state.memory_pressure > self._cfg.max_memory_pressure:
            a = SwarmAction(
                scout_priority=a.scout_priority,
                extraction_rate=min(a.extraction_rate, 0.3),
                debate_threshold=a.debate_threshold,
                cache_eviction_rate=max(a.cache_eviction_rate, 0.15),
            )

        # Latency SLA projection
        if state.extraction_latency > self._cfg.max_extraction_latency:
            a = SwarmAction(
                scout_priority=a.scout_priority,
                extraction_rate=min(a.extraction_rate, 0.4),
                debate_threshold=a.debate_threshold,
                cache_eviction_rate=a.cache_eviction_rate,
            )

        # Model shortage projection
        if state.model_availability < self._cfg.min_model_availability:
            a = SwarmAction(
                scout_priority=a.scout_priority,
                extraction_rate=a.extraction_rate,
                debate_threshold=max(a.debate_threshold, 0.6),
                cache_eviction_rate=a.cache_eviction_rate,
            )

        # Hard bound clamp
        a = SwarmAction(
            scout_priority=min(a.scout_priority, self._cfg.max_scout_aggression),
            extraction_rate=a.extraction_rate,
            debate_threshold=a.debate_threshold,
            cache_eviction_rate=min(a.cache_eviction_rate, self._cfg.max_eviction_rate),
        )

        return a


# ── Linear Policy (Q-table approx for tabular-ish state) ─────────────────────

class LinearPolicy:
    """
    Simple linear policy: a = W·s + b
    W  = weight matrix (action_dim × state_dim)
    b  = bias vector (action_dim)
    Updated via policy gradient with Lagrangian multiplier.
    """

    def __init__(self, state_dim: int = 6, action_dim: int = 4):
        self.state_dim  = state_dim
        self.action_dim = action_dim
        # Initialize weights as small random values
        self.W: list[list[float]] = [
            [random.gauss(0, 0.1) for _ in range(state_dim)]
            for _ in range(action_dim)
        ]
        self.b: list[float] = [0.5] * action_dim   # Start with midpoint

    def forward(self, state_vec: list[float]) -> list[float]:
        """Compute action vector from state. Outputs are unclamped."""
        output = []
        for i in range(self.action_dim):
            val = self.b[i] + sum(self.W[i][j] * state_vec[j]
                                   for j in range(self.state_dim))
            output.append(val)
        return output

    def update(
        self,
        state_vec:   list[float],
        advantage:   float,
        lagrangian:  float,
        constraint_violation: float,
        lr_theta:    float = 0.01,
    ):
        """
        θ_{t+1} = θ_t + η_θ · ∇_θ · L
        L(θ, λ) = E[R] − λ(E[C] − d)
        Gradient of L w.r.t. W_ij ≈ advantage · s_j (REINFORCE-style)
        Lagrangian penalty adjusts the gradient direction.
        """
        effective_advantage = advantage - lagrangian * constraint_violation
        for i in range(self.action_dim):
            for j in range(self.state_dim):
                self.W[i][j] += lr_theta * effective_advantage * state_vec[j]
            self.b[i] += lr_theta * effective_advantage

    def to_dict(self) -> dict:
        return {"W": self.W, "b": self.b}

    def from_dict(self, d: dict):
        self.W = d["W"]
        self.b = d["b"]


# ── Lagrange Multiplier Manager ───────────────────────────────────────────────

class LagrangeManager:
    """
    Primal-dual Lagrangian update:
      λ_{t+1} = max(0, λ_t + η_λ · (E[C] − d))

    One λ per constraint. Ensures constraint satisfaction in expectation.
    """

    def __init__(self, n_constraints: int = 3, lr_lambda: float = 0.005):
        self._lambdas = [0.0] * n_constraints
        self._lr      = lr_lambda
        self._limits  = [0.0] * n_constraints   # constraint limits d_i
        self._costs   = [[]] * n_constraints      # rolling cost history

    def set_limits(self, limits: list[float]):
        self._limits = limits

    def update(self, constraint_costs: list[float]):
        """
        Update all λ_i based on observed constraint costs.
        constraint_costs[i] = observed E[C_i] this step.
        """
        for i, (cost, limit) in enumerate(zip(constraint_costs, self._limits)):
            violation = cost - limit
            self._lambdas[i] = max(0.0, self._lambdas[i] + self._lr * violation)

    def total_penalty(self, constraint_costs: list[float]) -> float:
        """λ · (E[C] − d) summed over all constraints."""
        return sum(
            self._lambdas[i] * max(0.0, constraint_costs[i] - self._limits[i])
            for i in range(min(len(constraint_costs), len(self._lambdas)))
        )

    @property
    def lambdas(self) -> list[float]:
        return list(self._lambdas)


# ── Safety Shield (MPC Baseline Fallback) ─────────────────────────────────────

class SafetyShield:
    """
    Insert between policy and environment:
      Policy → Proposed action
      Safety model predicts next state
      If predicted violation → replace with safe fallback

    Safe fallback = conservative MPC-style action that minimizes
    the risk of constraint violation given current state.
    """

    def __init__(self, constraint_engine: ConstraintEngine):
        self._ce     = constraint_engine
        self._blocks = 0
        self._passes = 0

    def _safe_fallback(self, state: SwarmState) -> SwarmAction:
        """Conservative action: low rates, high thresholds, moderate eviction."""
        # MPC-style: compute safe action based on current state
        eviction = 0.20 if state.memory_pressure > 0.7 else 0.05
        rate     = 0.20 if state.extraction_latency > 0.6 else 0.40
        return SwarmAction(
            scout_priority=0.3,
            extraction_rate=rate,
            debate_threshold=0.5,
            cache_eviction_rate=eviction,
        )

    def shield(self, state: SwarmState, proposed: SwarmAction) -> tuple[SwarmAction, bool]:
        """
        Returns (final_action, was_shielded).
        was_shielded=True means the policy action was replaced by safe fallback.
        """
        valid, violations = self._ce.is_valid(state, proposed)
        if valid:
            self._passes += 1
            return proposed, False

        # Try projection first (less disruptive than full fallback)
        projected = self._ce.project_to_valid(state, proposed)
        proj_valid, _ = self._ce.is_valid(state, projected)

        if proj_valid:
            self._blocks += 1
            logger.info("Shield: projected action (violations=%s)", violations)
            return projected, True

        # Full fallback
        fallback = self._safe_fallback(state)
        self._blocks += 1
        logger.warning("Shield: full fallback (violations=%s)", violations)
        return fallback, True

    @property
    def block_rate(self) -> float:
        total = self._passes + self._blocks
        return self._blocks / total if total > 0 else 0.0


# ── Constrained RL Agent ──────────────────────────────────────────────────────

@dataclass
class RLExperience:
    state:      list[float]
    action:     list[float]
    reward:     float
    costs:      list[float]
    next_state: list[float]
    shielded:   bool


class ConstrainedRLAgent:
    """
    Full Constrained RL agent for KISWARM pipeline optimization.

    Combines:
      • Linear policy with gradient updates (θ)
      • Lagrange multiplier management (λ)
      • Action masking via ConstraintEngine
      • Safety shield with MPC fallback
      • Experience replay buffer

    Usage:
        agent = ConstrainedRLAgent()

        # At each decision point:
        state   = SwarmState(queue_depth=0.6, memory_pressure=0.7, ...)
        action  = agent.act(state)
        # ... execute action, observe outcomes ...
        agent.learn(state, action, reward=0.8, costs=[0.7, 0.3, 0.1])
    """

    STATE_DIM   = 6
    ACTION_DIM  = 4
    N_CONSTRAINTS = 3   # memory, latency, model_availability

    def __init__(
        self,
        store_path:  str   = RL_STORE,
        lr_theta:    float = 0.01,
        lr_lambda:   float = 0.005,
        gamma:       float = 0.95,
        buffer_size: int   = 500,
    ):
        self._store     = store_path
        self._gamma     = gamma
        self._lr_theta  = lr_theta
        self._buffer:   list[RLExperience] = []
        self._buf_size  = buffer_size
        self._episode   = 0
        self._total_reward = 0.0

        self.policy     = LinearPolicy(self.STATE_DIM, self.ACTION_DIM)
        self.lagrange   = LagrangeManager(self.N_CONSTRAINTS, lr_lambda)
        self.lagrange.set_limits([0.85, 0.80, 0.70])  # d_i limits

        self._constraint_cfg = ConstraintConfig()
        self._ce     = ConstraintEngine(self._constraint_cfg)
        self._shield = SafetyShield(self._ce)

        self._load()

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load(self):
        if os.path.exists(self._store):
            try:
                with open(self._store) as f:
                    raw = json.load(f)
                self.policy.from_dict(raw.get("policy", {"W": self.policy.W, "b": self.policy.b}))
                lambdas = raw.get("lambdas", [0.0] * self.N_CONSTRAINTS)
                self.lagrange._lambdas = lambdas
                self._episode      = raw.get("episode", 0)
                self._total_reward = raw.get("total_reward", 0.0)
                logger.info("RL agent loaded: episode=%d | reward=%.2f", self._episode, self._total_reward)
            except (json.JSONDecodeError, TypeError, OSError) as exc:
                logger.warning("RL load failed: %s", exc)

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self._store), exist_ok=True)
            with open(self._store, "w") as f:
                json.dump({
                    "policy":       self.policy.to_dict(),
                    "lambdas":      self.lagrange.lambdas,
                    "episode":      self._episode,
                    "total_reward": self._total_reward,
                    "shield_block_rate": self._shield.block_rate,
                }, f, indent=2)
        except OSError as exc:
            logger.error("RL save failed: %s", exc)

    # ── Core Interface ────────────────────────────────────────────────────────

    def act(self, state: SwarmState) -> SwarmAction:
        """
        Compute action for state:
          1. Policy forward pass
          2. Action masking / projection
          3. Safety shield
        """
        state_vec = state.to_vector()
        raw_vec   = self.policy.forward(state_vec)
        proposed  = SwarmAction.from_vector(raw_vec).clamp()
        final, _  = self._shield.shield(state, proposed)
        return final

    def learn(
        self,
        state:      SwarmState,
        action:     SwarmAction,
        reward:     float,
        costs:      list[float],   # [memory_cost, latency_cost, model_cost]
        next_state: SwarmState = None,
        shielded:   bool = False,
    ):
        """
        Full Lagrangian constrained policy gradient update.

        reward  = +quality_gain, +speed_bonus
        costs   = [memory_used, latency, model_load]
        """
        self._episode += 1
        self._total_reward += reward

        # Store experience
        exp = RLExperience(
            state=state.to_vector(),
            action=action.to_vector(),
            reward=reward,
            costs=costs[:self.N_CONSTRAINTS],
            next_state=next_state.to_vector() if next_state else state.to_vector(),
            shielded=shielded,
        )
        self._buffer.append(exp)
        if len(self._buffer) > self._buf_size:
            self._buffer.pop(0)

        # Update Lagrange multipliers
        self.lagrange.update(costs[:self.N_CONSTRAINTS])

        # Estimate advantage (simple TD: reward + γ·V(s') - V(s))
        # V(s) approximated as running mean reward
        mean_reward = self._total_reward / self._episode
        advantage   = reward - mean_reward

        # Constraint violation for Lagrangian gradient
        limits      = [0.85, 0.80, 0.70]
        violations  = [max(0.0, c - d) for c, d in zip(costs[:3], limits)]
        constraint_penalty = sum(l * v for l, v in zip(self.lagrange.lambdas, violations))

        # Policy gradient update
        self.policy.update(
            state_vec=state.to_vector(),
            advantage=advantage,
            lagrangian=sum(self.lagrange.lambdas),
            constraint_violation=sum(violations),
            lr_theta=self._lr_theta,
        )

        if self._episode % 50 == 0:
            self._save()
            logger.info(
                "RL update: episode=%d | reward=%.3f | λ=%s | shield_rate=%.0f%%",
                self._episode, reward,
                [f"{l:.3f}" for l in self.lagrange.lambdas],
                self._shield.block_rate * 100,
            )

    def get_stats(self) -> dict:
        return {
            "episode":          self._episode,
            "total_reward":     round(self._total_reward, 3),
            "mean_reward":      round(self._total_reward / max(self._episode, 1), 3),
            "lambdas":          self.lagrange.lambdas,
            "shield_block_rate": round(self._shield.block_rate, 3),
            "buffer_size":      len(self._buffer),
        }
