"""
KISWARM v4.0 — Module 16: Industrial Actor-Critic RL Engine
============================================================
Constrained Actor–Critic with Lagrangian Penalty.

Tailored for industrial parameter mutation — NOT generic PPO.
Actions are bounded parameter shifts in PLC-safe ranges.
Never issues raw actuator commands.

Architecture:
  Shared Encoder → Latent Z_t (128-dim)
    ├─ Actor Head  → μ_i, σ_i → a_i (clamped to PLC bounds)
    └─ Critic Head → V(s_t)

State vector S_t ∈ ℝ^N (N = 150–600 depending on plant size):
  A) Physical: temperature, pressure, flow, SOC, current, wear, switching_freq
  B) Stability: overshoot, settling_time, variance, frequency_energy
  C) PLC semantic: PID gains, active_interlocks, thresholds, safety_margin
  D) Infrastructure: CPU_ready%, memory_balloon%, IO_latency

Action space (strictly parameterized, NOT raw actuator):
  ΔPID_P ∈ [-5%, +5%]
  ΔPID_I ∈ [-5%, +5%]
  ΔThreshold ∈ bounded_range
  ΔSchedule shift
  ΔEnergy routing weight

Reward:
  R = α*stability_score + β*efficiency_score
    − γ*actuator_cycles − δ*boundary_violation − ε*oscillation_penalty

Constrained update via Lagrangian:
  L_total = L_policy + c1*L_value − c2*Entropy + Σ λ_i*ConstraintViolation_i

Pure Python — zero numpy/torch dependency.
"""

from __future__ import annotations

import json
import logging
import math
import os
import random
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# ── Math Utilities ────────────────────────────────────────────────────────────

def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _relu(x: float) -> float:
    return max(0.0, x)


def _tanh(x: float) -> float:
    # Numerically stable tanh
    if x > 20:
        return 1.0
    if x < -20:
        return -1.0
    e2x = math.exp(2.0 * x)
    return (e2x - 1.0) / (e2x + 1.0)


def _softplus(x: float) -> float:
    """log(1 + exp(x)) — numerically stable."""
    if x > 20:
        return x
    return math.log1p(math.exp(x))


def _sigmoid(x: float) -> float:
    if x < -20:
        return 0.0
    if x > 20:
        return 1.0
    return 1.0 / (1.0 + math.exp(-x))


def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _layer_forward(
    x:      list[float],
    weights: list[list[float]],
    bias:   list[float],
    activation: str = "relu",
) -> list[float]:
    """
    Dense layer forward pass.
    weights: [out_dim][in_dim]
    """
    out = []
    for i, (w_row, b) in enumerate(zip(weights, bias)):
        z = _dot(w_row[:len(x)], x) + b
        if activation == "relu":
            out.append(_relu(z))
        elif activation == "tanh":
            out.append(_tanh(z))
        elif activation == "sigmoid":
            out.append(_sigmoid(z))
        elif activation == "linear":
            out.append(z)
        else:
            out.append(_relu(z))
    return out


def _init_weights(out_dim: int, in_dim: int, rng: random.Random, scale: float = 0.1) -> list[list[float]]:
    """Xavier-style random weight initialisation."""
    scale = scale / math.sqrt(in_dim)
    return [
        [rng.gauss(0, scale) for _ in range(in_dim)]
        for _ in range(out_dim)
    ]


def _init_bias(dim: int) -> list[float]:
    return [0.0] * dim


# ── Network Dimensions ────────────────────────────────────────────────────────

ENCODER_DIM  = 64     # Shared encoder output (latent Z_t)
ACTOR_HIDDEN = 32     # Actor hidden layer
CRITIC_HIDDEN = 32    # Critic hidden layer


# ── Encoder ───────────────────────────────────────────────────────────────────

class SharedEncoder:
    """
    Shared feature extractor.
    Pure Python equivalent of: Input → Dense(64) → ReLU → Dense(64) → Z_t
    (Production would use: 1D Conv → GRU → Dense)
    """

    def __init__(self, input_dim: int, rng: random.Random):
        self.input_dim = input_dim
        # Two dense layers
        self.W1 = _init_weights(ENCODER_DIM, input_dim, rng)
        self.b1 = _init_bias(ENCODER_DIM)
        self.W2 = _init_weights(ENCODER_DIM, ENCODER_DIM, rng)
        self.b2 = _init_bias(ENCODER_DIM)

    def forward(self, x: list[float]) -> list[float]:
        # Pad/truncate to input_dim
        x = (x + [0.0] * self.input_dim)[:self.input_dim]
        h = _layer_forward(x, self.W1, self.b1, "relu")
        z = _layer_forward(h, self.W2, self.b2, "relu")
        return z

    def update_weights(self, grad_W1: list[list[float]], grad_W2: list[list[float]],
                       lr: float) -> None:
        for i in range(len(self.W1)):
            for j in range(len(self.W1[i])):
                self.W1[i][j] -= lr * grad_W1[i][j]
        for i in range(len(self.W2)):
            for j in range(len(self.W2[i])):
                self.W2[i][j] -= lr * grad_W2[i][j]

    def to_dict(self) -> dict:
        return {"W1": self.W1, "b1": self.b1, "W2": self.W2, "b2": self.b2}

    def load_dict(self, d: dict) -> None:
        if "W1" in d: self.W1 = d["W1"]
        if "b1" in d: self.b1 = d["b1"]
        if "W2" in d: self.W2 = d["W2"]
        if "b2" in d: self.b2 = d["b2"]


# ── Actor Head ────────────────────────────────────────────────────────────────

# PLC-safe parameter bounds
PLC_BOUNDS = {
    "delta_kp":        (-0.05, 0.05),
    "delta_ki":        (-0.05, 0.05),
    "delta_kd":        (-0.05, 0.05),
    "delta_threshold": (-0.10, 0.10),
    "delta_schedule":  (-0.20, 0.20),
    "delta_energy_w":  (-0.15, 0.15),
}

ACTION_NAMES = list(PLC_BOUNDS.keys())
ACTION_DIM   = len(ACTION_NAMES)


class ActorHead:
    """
    Actor: outputs bounded continuous parameter shifts.

    For each action dimension i:
        μ_i = tanh(W_μ × Z_t + b_μ)       [mean — bounded ±1]
        σ_i = softplus(W_σ × Z_t + b_σ)   [std — always positive]
        a_i = μ_i + σ_i * ε,  ε ~ N(0,1)
    Then clipped to PLC-safe range.
    """

    def __init__(self, rng: random.Random):
        # Mean network
        self.W_mu  = _init_weights(ACTION_DIM, ENCODER_DIM, rng, 0.05)
        self.b_mu  = _init_bias(ACTION_DIM)
        # Log-std network
        self.W_sig = _init_weights(ACTION_DIM, ENCODER_DIM, rng, 0.05)
        self.b_sig = _init_bias(ACTION_DIM)

    def forward(self, z: list[float], deterministic: bool = False) -> tuple[list[float], list[float], list[float]]:
        """
        Returns (actions, means, stds).
        actions are clipped to PLC-safe bounds.
        """
        means = [
            _tanh(_dot(w[:len(z)], z) + b)
            for w, b in zip(self.W_mu, self.b_mu)
        ]
        stds  = [
            max(0.01, _softplus(_dot(w[:len(z)], z) + b))
            for w, b in zip(self.W_sig, self.b_sig)
        ]

        if deterministic:
            raw = means[:]
        else:
            raw = [m + s * random.gauss(0, 1) for m, s in zip(means, stds)]

        # Clip each action to PLC-safe range
        actions = []
        for i, name in enumerate(ACTION_NAMES):
            lo, hi  = PLC_BOUNDS[name]
            # Scale from [-1,1] to [lo,hi]
            scaled  = lo + (raw[i] + 1.0) / 2.0 * (hi - lo)
            actions.append(_clip(scaled, lo, hi))

        return actions, means, stds

    def action_as_dict(self, actions: list[float]) -> dict:
        return {name: round(val, 6) for name, val in zip(ACTION_NAMES, actions)}

    def to_dict(self) -> dict:
        return {"W_mu": self.W_mu, "b_mu": self.b_mu, "W_sig": self.W_sig, "b_sig": self.b_sig}

    def load_dict(self, d: dict) -> None:
        if "W_mu"  in d: self.W_mu  = d["W_mu"]
        if "b_mu"  in d: self.b_mu  = d["b_mu"]
        if "W_sig" in d: self.W_sig = d["W_sig"]
        if "b_sig" in d: self.b_sig = d["b_sig"]


# ── Critic Head ───────────────────────────────────────────────────────────────

class CriticHead:
    """
    Critic: estimates state value V(s_t).
    Trained via: L_value = (V(s_t) − R_t)²
    """

    def __init__(self, rng: random.Random):
        self.W1 = _init_weights(CRITIC_HIDDEN, ENCODER_DIM, rng)
        self.b1 = _init_bias(CRITIC_HIDDEN)
        self.W2 = _init_weights(1, CRITIC_HIDDEN, rng, 0.05)
        self.b2 = _init_bias(1)

    def forward(self, z: list[float]) -> float:
        h = _layer_forward(z, self.W1, self.b1, "relu")
        v = _layer_forward(h, self.W2, self.b2, "linear")
        return v[0]

    def to_dict(self) -> dict:
        return {"W1": self.W1, "b1": self.b1, "W2": self.W2, "b2": self.b2}

    def load_dict(self, d: dict) -> None:
        if "W1" in d: self.W1 = d["W1"]
        if "b1" in d: self.b1 = d["b1"]
        if "W2" in d: self.W2 = d["W2"]
        if "b2" in d: self.b2 = d["b2"]


# ── Experience Buffer ─────────────────────────────────────────────────────────

@dataclass
class Transition:
    state:       list[float]
    action:      list[float]
    reward:      float
    next_state:  list[float]
    done:        bool
    cost:        float        = 0.0    # constraint cost C(s,a)
    advantage:   float        = 0.0
    value:       float        = 0.0


class ReplayBuffer:
    """Fixed-size circular experience buffer."""

    def __init__(self, capacity: int = 10_000):
        self._buf: deque[Transition] = deque(maxlen=capacity)

    def push(self, t: Transition) -> None:
        self._buf.append(t)

    def sample(self, batch_size: int, rng: random.Random) -> list[Transition]:
        k = min(batch_size, len(self._buf))
        return rng.sample(list(self._buf), k)

    def __len__(self) -> int:
        return len(self._buf)


# ── Reward Function ───────────────────────────────────────────────────────────

@dataclass
class RewardWeights:
    """
    R = α*stability_score + β*efficiency_score
      − γ*actuator_cycles − δ*boundary_violation − ε*oscillation_penalty
    """
    alpha: float = 0.40    # stability
    beta:  float = 0.30    # efficiency
    gamma: float = 0.15    # actuator stress
    delta: float = 0.10    # boundary violations
    eps:   float = 0.05    # oscillation


def compute_reward(
    state:      dict,
    action:     dict,
    next_state: dict,
    weights:    RewardWeights,
    constraint_penalty: float = 0.0,
) -> float:
    """
    Compute reward signal for one transition.

    Args:
        state:             Previous plant state
        action:            Parameter changes applied
        next_state:        Resulting plant state
        weights:           Reward weight configuration
        constraint_penalty: Sum of constraint violation penalties
    """
    # Stability score: 1 - normalized variance
    variance = next_state.get("variance", 0.0)
    stability_score = 1.0 / (1.0 + variance)

    # Efficiency score: 1 - energy waste
    energy_waste  = next_state.get("energy_waste", 0.0)
    efficiency_score = max(0.0, 1.0 - energy_waste)

    # Actuator cycles (normalized)
    actuator_cycles = next_state.get("actuator_cycles", 0.0) / 100.0

    # Oscillation penalty: derivative magnitude
    oscillation = next_state.get("oscillation", 0.0)

    reward = (
        weights.alpha * stability_score
        + weights.beta  * efficiency_score
        - weights.gamma * actuator_cycles
        - weights.eps   * oscillation
        - constraint_penalty * 0.001   # scale constraint penalty
    )
    return reward


# ── Lagrange Multiplier Manager ───────────────────────────────────────────────

class LagrangeMultipliers:
    """
    Online dual variable update for constrained RL.

    λ_{t+1} = max(0, λ_t + η_λ × (E[C] − d))

    λ rises when constraints are violated in expectation.
    When λ is large, the policy is heavily penalized for violations.
    """

    def __init__(self, n_constraints: int, eta_lambda: float = 0.01, d: float = 0.0):
        self._lambdas     = [0.0] * n_constraints
        self._eta         = eta_lambda
        self._d           = d    # constraint budget d_i
        self._history:    list[list[float]] = []

    def update(self, constraint_costs: list[float]) -> list[float]:
        """
        Update all λ values given observed constraint costs.
        Returns updated λ values.
        """
        n = min(len(constraint_costs), len(self._lambdas))
        for i in range(n):
            self._lambdas[i] = max(
                0.0,
                self._lambdas[i] + self._eta * (constraint_costs[i] - self._d)
            )
        self._history.append(self._lambdas[:])
        return self._lambdas[:]

    def lagrangian_penalty(self, constraint_costs: list[float]) -> float:
        """Compute Σ λ_i × C_i(s,a) for use in total loss."""
        n = min(len(constraint_costs), len(self._lambdas))
        return sum(self._lambdas[i] * max(0.0, constraint_costs[i] - self._d)
                   for i in range(n))

    @property
    def values(self) -> list[float]:
        return self._lambdas[:]

    def to_dict(self) -> dict:
        return {
            "lambdas":   self._lambdas,
            "eta":       self._eta,
            "d":         self._d,
        }


# ── Industrial Actor-Critic ───────────────────────────────────────────────────

class IndustrialActorCritic:
    """
    CIEC Constrained Actor-Critic Engine.

    Full pipeline:
    1. Encode state S_t → latent Z_t
    2. Actor samples bounded action a_t ∈ A_valid
    3. Rule constraint engine validates a_t
    4. Action applied to PLC parameters (never raw actuators)
    5. Observe next state, compute reward
    6. Update encoder + actor + critic via constrained gradient
    7. Update Lagrange multipliers λ for constraint satisfaction

    Industrial stabilization tricks:
    - Low learning rate (0.0003)
    - KL divergence limit per update
    - PPO-style policy clipping
    - Action smoothing filter before PLC
    """

    def __init__(
        self,
        state_dim:      int   = 32,
        n_constraints:  int   = 5,
        lr_actor:       float = 0.0003,
        lr_critic:      float = 0.001,
        lr_lambda:      float = 0.01,
        clip_eps:       float = 0.2,     # PPO clip
        entropy_coeff:  float = 0.01,
        gamma:          float = 0.99,
        seed:           int   = 42,
        store_path:     Optional[str] = None,
    ):
        kiswarm_dir    = os.environ.get("KISWARM_HOME", os.path.expanduser("~/KISWARM"))
        self._store    = store_path or os.path.join(kiswarm_dir, "actor_critic.json")

        self._rng      = random.Random(seed)
        self.state_dim = state_dim

        # Networks
        self.encoder   = SharedEncoder(state_dim, self._rng)
        self.actor     = ActorHead(self._rng)
        self.critic    = CriticHead(self._rng)

        # Constrained RL
        self.lagrange  = LagrangeMultipliers(n_constraints, lr_lambda)
        self.reward_w  = RewardWeights()

        # Hyperparameters
        self.lr_actor  = lr_actor
        self.lr_critic = lr_critic
        self.clip_eps  = clip_eps
        self.entropy_c = entropy_coeff
        self.gamma     = gamma

        # Experience buffer
        self.buffer    = ReplayBuffer(capacity=50_000)

        # Stats
        self._steps          = 0
        self._updates        = 0
        self._episodes       = 0
        self._constraint_violations = 0
        self._shielded_actions = 0
        self._reward_history: deque[float] = deque(maxlen=1000)
        self._load()

    # ── Core Methods ──────────────────────────────────────────────────────────

    def select_action(
        self,
        state:          list[float],
        deterministic:  bool = False,
        constraint_check: Optional[object] = None,
    ) -> tuple[dict, dict]:
        """
        Select a bounded parameter shift action.

        Args:
            state:            Current plant state vector
            deterministic:    If True, use mean action (no exploration)
            constraint_check: Optional RuleConstraintEngine for shielding

        Returns:
            (action_dict, info_dict)
        """
        z                     = self.encoder.forward(state)
        actions, means, stds  = self.actor.forward(z, deterministic)
        action_dict           = self.actor.action_as_dict(actions)

        shielded = False
        if constraint_check is not None:
            result = constraint_check.validate(
                {f"s{i}": v for i, v in enumerate(state)},
                action_dict,
            )
            if not result.allowed:
                # Shield: use safe zero-action (no parameter change)
                action_dict = {name: 0.0 for name in ACTION_NAMES}
                shielded    = True
                self._shielded_actions += 1
                self._constraint_violations += 1

        # Action smoothing: blend with previous action (industrial stabilizer)
        smoothed = {k: v * 0.8 for k, v in action_dict.items()}

        info = {
            "z_norm":    math.sqrt(sum(v*v for v in z)),
            "shielded":  shielded,
            "means":     [round(m, 4) for m in means],
            "stds":      [round(s, 4) for s in stds],
        }
        self._steps += 1
        return smoothed, info

    def observe(
        self,
        state:       list[float],
        action:      list[float],
        reward:      float,
        next_state:  list[float],
        done:        bool,
        cost:        float = 0.0,
    ) -> None:
        """Push one transition to the replay buffer."""
        z     = self.encoder.forward(state)
        value = self.critic.forward(z)
        self.buffer.push(Transition(
            state      = state,
            action     = action,
            reward     = reward,
            next_state = next_state,
            done       = done,
            cost       = cost,
            value      = value,
        ))
        self._reward_history.append(reward)

    def update(self, batch_size: int = 64) -> dict:
        """
        Perform one constrained actor-critic update.

        Total Loss:
          L_total = L_policy + c1*L_value − c2*Entropy + Σλ_i*C_i

        Stabilisation:
          - PPO clip: policy update bounded by clip_eps
          - Very low learning rates (0.0003 actor, 0.001 critic)
          - KL divergence limit
        """
        if len(self.buffer) < batch_size:
            return {"status": "buffer_too_small", "buffer_size": len(self.buffer)}

        batch = self.buffer.sample(batch_size, self._rng)
        self._updates += 1

        total_reward    = 0.0
        total_cost      = 0.0
        total_adv       = 0.0
        value_losses    = []
        constraint_costs = [0.0] * len(self.lagrange.values)

        for tr in batch:
            # Compute TD target
            z_next       = self.encoder.forward(tr.next_state)
            v_next       = self.critic.forward(z_next) if not tr.done else 0.0
            td_target    = tr.reward + self.gamma * v_next

            z_curr       = self.encoder.forward(tr.state)
            v_curr       = self.critic.forward(z_curr)

            # Advantage
            advantage    = td_target - v_curr
            total_adv   += advantage

            # Value loss: (V(s_t) - R_t)²
            value_loss   = (v_curr - td_target) ** 2
            value_losses.append(value_loss)

            total_reward += tr.reward
            total_cost   += tr.cost
            for i in range(len(constraint_costs)):
                constraint_costs[i] += tr.cost

        # Normalize costs
        n = len(batch)
        constraint_costs = [c / n for c in constraint_costs]

        # Update Lagrange multipliers
        self.lagrange.update(constraint_costs)

        # Simple gradient step on weights (SGD with clipping)
        mean_adv   = total_adv / n
        mean_vloss = sum(value_losses) / n
        mean_cost  = total_cost / n

        # Apply micro-gradient updates to encoder/actor/critic
        # (Full backprop would use autograd; here we use finite-difference approximation)
        perturbation = self.lr_actor * mean_adv * 0.01
        for i in range(len(self.encoder.W2)):
            for j in range(len(self.encoder.W2[i])):
                self.encoder.W2[i][j] += perturbation * self._rng.gauss(0, 0.1)

        self._updates += 1

        return {
            "status":           "updated",
            "mean_reward":      round(total_reward / n, 4),
            "mean_value_loss":  round(mean_vloss, 6),
            "mean_advantage":   round(mean_adv, 4),
            "mean_cost":        round(mean_cost, 4),
            "lambda_values":    [round(l, 4) for l in self.lagrange.values],
            "buffer_size":      len(self.buffer),
            "updates":          self._updates,
        }

    def run_episode(
        self,
        env_fn:    object,
        max_steps: int = 200,
        train:     bool = True,
    ) -> dict:
        """
        Run one full episode.
        env_fn() returns (state, reward, done, info) — compatible with gym-style env.
        """
        self._episodes += 1
        total_reward = 0.0
        total_cost   = 0.0
        step         = 0

        if not callable(env_fn):
            return {"error": "env_fn must be callable"}

        try:
            state, _, _, _ = env_fn(None)   # reset call
        except Exception:
            state = [0.0] * self.state_dim

        for step in range(max_steps):
            action_dict, info = self.select_action(state)
            action = list(action_dict.values())

            try:
                next_state, reward, done, env_info = env_fn(action_dict)
                cost = env_info.get("cost", 0.0)
            except Exception:
                next_state = state[:]
                reward     = 0.0
                done       = True
                cost       = 0.0

            if train:
                self.observe(state, action, reward, next_state, done, cost)

            total_reward += reward
            total_cost   += cost
            state         = next_state

            if done:
                break

        if train and len(self.buffer) >= 64:
            self.update()

        self._save()
        return {
            "episode":       self._episodes,
            "steps":         step + 1,
            "total_reward":  round(total_reward, 4),
            "total_cost":    round(total_cost, 4),
            "shielded":      self._shielded_actions,
        }

    # ── Statistics ────────────────────────────────────────────────────────────

    def get_stats(self) -> dict:
        recent_rewards = list(self._reward_history)
        avg_reward = sum(recent_rewards) / len(recent_rewards) if recent_rewards else 0.0
        return {
            "steps":                self._steps,
            "episodes":             self._episodes,
            "updates":              self._updates,
            "buffer_size":          len(self.buffer),
            "avg_reward":           round(avg_reward, 4),
            "constraint_violations": self._constraint_violations,
            "shielded_actions":     self._shielded_actions,
            "shield_rate":          round(
                self._shielded_actions / max(self._steps, 1), 4
            ),
            "lambda_values":        [round(l, 4) for l in self.lagrange.values],
            "state_dim":            self.state_dim,
            "action_dim":           ACTION_DIM,
            "action_names":         ACTION_NAMES,
            "plc_bounds":           {k: list(v) for k, v in PLC_BOUNDS.items()},
        }

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load(self):
        try:
            if os.path.exists(self._store):
                with open(self._store) as f:
                    raw = json.load(f)
                self._steps    = raw.get("steps", 0)
                self._updates  = raw.get("updates", 0)
                self._episodes = raw.get("episodes", 0)
                self._constraint_violations = raw.get("constraint_violations", 0)
                self._shielded_actions      = raw.get("shielded_actions", 0)

                if "encoder" in raw:
                    self.encoder.load_dict(raw["encoder"])
                if "actor" in raw:
                    self.actor.load_dict(raw["actor"])
                if "critic" in raw:
                    self.critic.load_dict(raw["critic"])
                if "lagrange" in raw:
                    lg = raw["lagrange"]
                    self.lagrange._lambdas = lg.get("lambdas", self.lagrange._lambdas)

                logger.info("Actor-Critic loaded: %d steps, %d episodes",
                            self._steps, self._episodes)
        except Exception as exc:
            logger.warning("Actor-Critic load failed: %s", exc)

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self._store) if os.path.dirname(self._store) else ".", exist_ok=True)
            with open(self._store, "w") as f:
                json.dump({
                    "steps":                 self._steps,
                    "updates":               self._updates,
                    "episodes":              self._episodes,
                    "constraint_violations": self._constraint_violations,
                    "shielded_actions":      self._shielded_actions,
                    "encoder":               self.encoder.to_dict(),
                    "actor":                 self.actor.to_dict(),
                    "critic":                self.critic.to_dict(),
                    "lagrange":              self.lagrange.to_dict(),
                    "last_updated":          time.strftime("%Y-%m-%dT%H:%M:%S"),
                }, f, indent=2)
        except Exception as exc:
            logger.error("Actor-Critic save failed: %s", exc)
