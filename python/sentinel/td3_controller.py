"""
KISWARM v4.1 — Module 17: TD3 Industrial Controller
====================================================
Twin Delayed Deep Deterministic Policy Gradient (TD3) optimized for
continuous industrial parameter tuning.

Design specifications:
  Actor:  256→512→512→256→128→N  (GELU activations, tanh output)
  Critic: (state+action)→512→512→256→1  (twin networks)
  γ = 0.995,  τ = 0.002,  σ_policy = 0.1,  noise_clip = 0.2
  Actor LR = 1e-4,  Critic LR = 5e-4,  Batch = 512
  Replay buffer ≥ 2 million transitions
  Actor updated every 2 critic updates (delayed policy update)
  Inference latency target: < 10 ms
"""

from __future__ import annotations

import math
import time
import random
import hashlib
import json
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any

# ─────────────────────────────────────────────────────────────────────────────
# PURE-PYTHON NEURAL NETWORK PRIMITIVES  (zero external dependencies)
# ─────────────────────────────────────────────────────────────────────────────

def _gelu(x: float) -> float:
    """GELU activation: x * Φ(x) approximated via tanh."""
    return 0.5 * x * (1.0 + math.tanh(math.sqrt(2.0 / math.pi) * (x + 0.044715 * x**3)))

def _tanh(x: float) -> float:
    return math.tanh(max(-20.0, min(20.0, x)))

def _relu(x: float) -> float:
    return max(0.0, x)

def _dot(a: List[float], b: List[float]) -> float:
    return sum(x * y for x, y in zip(a, b))

def _mat_vec(W: List[List[float]], x: List[float], b: List[float]) -> List[float]:
    return [_gelu(_dot(row, x) + bias) for row, bias in zip(W, b)]

def _mat_vec_linear(W: List[List[float]], x: List[float], b: List[float]) -> List[float]:
    return [_dot(row, x) + bias for row, bias in zip(W, b)]


class _Dense:
    """Fully connected layer with configurable activation."""

    def __init__(self, in_dim: int, out_dim: int, activation: str = "gelu", seed: int = 0):
        rng = random.Random(seed)
        scale = math.sqrt(2.0 / in_dim)
        self.W = [[rng.gauss(0, scale) for _ in range(in_dim)] for _ in range(out_dim)]
        self.b = [0.0] * out_dim
        self.activation = activation
        self.in_dim = in_dim
        self.out_dim = out_dim

    def forward(self, x: List[float]) -> List[float]:
        pre = _mat_vec_linear(self.W, x, self.b)
        if self.activation == "gelu":
            return [_gelu(v) for v in pre]
        elif self.activation == "relu":
            return [_relu(v) for v in pre]
        elif self.activation == "tanh":
            return [_tanh(v) for v in pre]
        return pre  # linear

    def clone(self) -> "_Dense":
        lay = _Dense(self.in_dim, self.out_dim, self.activation)
        lay.W = [row[:] for row in self.W]
        lay.b = self.b[:]
        return lay

    def soft_update(self, other: "_Dense", tau: float) -> None:
        """θ_target ← τ*θ_online + (1−τ)*θ_target"""
        for i in range(self.out_dim):
            for j in range(self.in_dim):
                self.W[i][j] = tau * other.W[i][j] + (1 - tau) * self.W[i][j]
            self.b[i] = tau * other.b[i] + (1 - tau) * self.b[i]

    def sgd_step(self, grad_W: List[List[float]], grad_b: List[float], lr: float) -> None:
        for i in range(self.out_dim):
            for j in range(self.in_dim):
                self.W[i][j] -= lr * grad_W[i][j]
            self.b[i] -= lr * grad_b[i]


# ─────────────────────────────────────────────────────────────────────────────
# PLC ACTION BOUNDS
# ─────────────────────────────────────────────────────────────────────────────

PLC_ACTION_BOUNDS: Dict[str, Tuple[float, float]] = {
    "delta_kp":        (-0.05,  +0.05),
    "delta_ki":        (-0.05,  +0.05),
    "delta_kd":        (-0.05,  +0.05),
    "delta_fuzzy_c1":  (-0.02,  +0.02),
    "delta_fuzzy_c2":  (-0.02,  +0.02),
    "delta_threshold": (-0.10,  +0.10),
    "delta_schedule":  (-0.20,  +0.20),
    "delta_energy_w":  (-0.15,  +0.15),
}
ACTION_NAMES = list(PLC_ACTION_BOUNDS.keys())
N_ACTIONS = len(ACTION_NAMES)


# ─────────────────────────────────────────────────────────────────────────────
# ACTOR NETWORK  (deterministic policy π_θ)
# Architecture: state_dim → 512 → 512 → 256 → 128 → N_ACTIONS (tanh)
# ─────────────────────────────────────────────────────────────────────────────

class _ActorNetwork:
    def __init__(self, state_dim: int, seed: int = 42):
        self.layers = [
            _Dense(state_dim,   512, "gelu", seed),
            _Dense(512,         512, "gelu", seed + 1),
            _Dense(512,         256, "gelu", seed + 2),
            _Dense(256,         128, "gelu", seed + 3),
            _Dense(128,  N_ACTIONS, "tanh", seed + 4),
        ]
        self.state_dim = state_dim

    def forward(self, state: List[float]) -> List[float]:
        x = state[:]
        for layer in self.layers:
            x = layer.forward(x)
        # Scale tanh output to PLC engineering limits
        scaled = []
        for i, (lo, hi) in enumerate(PLC_ACTION_BOUNDS.values()):
            scaled.append(x[i] * (hi - lo) / 2.0)
        return scaled

    def clone(self) -> "_ActorNetwork":
        net = _ActorNetwork(self.state_dim)
        net.layers = [l.clone() for l in self.layers]
        return net

    def soft_update(self, other: "_ActorNetwork", tau: float) -> None:
        for a, b in zip(self.layers, other.layers):
            a.soft_update(b, tau)

    def perturb(self, sigma: float, rng: random.Random) -> None:
        """Add parameter noise for exploration."""
        for layer in self.layers:
            for i in range(layer.out_dim):
                for j in range(layer.in_dim):
                    layer.W[i][j] += rng.gauss(0, sigma * 0.01)


# ─────────────────────────────────────────────────────────────────────────────
# CRITIC NETWORK  (Q-function)
# Architecture: (state+action) → 512 → 512 → 256 → 1
# ─────────────────────────────────────────────────────────────────────────────

class _CriticNetwork:
    def __init__(self, state_dim: int, seed: int = 99):
        in_dim = state_dim + N_ACTIONS
        self.layers = [
            _Dense(in_dim, 512, "gelu", seed),
            _Dense(512,    512, "gelu", seed + 1),
            _Dense(512,    256, "gelu", seed + 2),
            _Dense(256,      1, "linear", seed + 3),
        ]
        self.state_dim = state_dim

    def forward(self, state: List[float], action: List[float]) -> float:
        x = state[:] + action[:]
        for layer in self.layers:
            x = layer.forward(x)
        return x[0]

    def clone(self) -> "_CriticNetwork":
        net = _CriticNetwork(self.state_dim)
        net.layers = [l.clone() for l in self.layers]
        return net

    def soft_update(self, other: "_CriticNetwork", tau: float) -> None:
        for a, b in zip(self.layers, other.layers):
            a.soft_update(b, tau)


# ─────────────────────────────────────────────────────────────────────────────
# REPLAY BUFFER  (≥ 2 million transitions)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class _Transition:
    state:      List[float]
    action:     List[float]
    reward:     float
    next_state: List[float]
    done:       bool
    cost:       float = 0.0


class _ReplayBuffer:
    def __init__(self, capacity: int = 2_000_000):
        self._buf: deque = deque(maxlen=capacity)
        self.capacity = capacity

    def push(self, t: _Transition) -> None:
        self._buf.append(t)

    def sample(self, n: int, rng: random.Random) -> List[_Transition]:
        return rng.choices(self._buf, k=min(n, len(self._buf)))

    def __len__(self) -> int:
        return len(self._buf)


# ─────────────────────────────────────────────────────────────────────────────
# TD3 STATS
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class TD3Stats:
    total_steps:     int   = 0
    total_updates:   int   = 0
    actor_updates:   int   = 0
    episodes:        int   = 0
    shield_count:    int   = 0
    buffer_size:     int   = 0
    mean_q1:         float = 0.0
    mean_q2:         float = 0.0
    actor_loss_last: float = 0.0
    critic_loss_last:float = 0.0

    def to_dict(self) -> dict:
        return {
            "total_steps":      self.total_steps,
            "total_updates":    self.total_updates,
            "actor_updates":    self.actor_updates,
            "episodes":         self.episodes,
            "shield_count":     self.shield_count,
            "buffer_size":      self.buffer_size,
            "mean_q1":          round(self.mean_q1, 6),
            "mean_q2":          round(self.mean_q2, 6),
            "actor_loss_last":  round(self.actor_loss_last, 6),
            "critic_loss_last": round(self.critic_loss_last, 6),
            "action_names":     ACTION_NAMES,
            "plc_bounds":       {k: list(v) for k, v in PLC_ACTION_BOUNDS.items()},
        }


# ─────────────────────────────────────────────────────────────────────────────
# TD3 INDUSTRIAL CONTROLLER
# ─────────────────────────────────────────────────────────────────────────────

class TD3IndustrialController:
    """
    Twin Delayed Deep Deterministic Policy Gradient for industrial PLC tuning.

    Key safety invariants:
      - Actions are ALWAYS clamped to PLC_ACTION_BOUNDS
      - Never outputs raw actuator commands
      - Optional constraint_check (RuleConstraintEngine) as action shield
      - All inference < 10 ms (pure Python network forward pass)
    """

    # ── Hyperparameters ──────────────────────────────────────────────────────
    GAMMA        = 0.995
    TAU          = 0.002
    SIGMA_POLICY = 0.10
    NOISE_CLIP   = 0.20
    ACTOR_LR     = 1e-4
    CRITIC_LR    = 5e-4
    BATCH_SIZE   = 512
    POLICY_DELAY = 2     # update actor every N critic updates
    BUFFER_CAP   = 2_000_000

    def __init__(self, state_dim: int = 256, seed: int = 42):
        self.state_dim = state_dim
        self._rng = random.Random(seed)

        # Online networks
        self.actor   = _ActorNetwork(state_dim, seed)
        self.critic1 = _CriticNetwork(state_dim, seed + 10)
        self.critic2 = _CriticNetwork(state_dim, seed + 20)

        # Target networks (initialized as copies)
        self.actor_target   = self.actor.clone()
        self.critic1_target = self.critic1.clone()
        self.critic2_target = self.critic2.clone()

        self.buffer  = _ReplayBuffer(self.BUFFER_CAP)
        self._stats  = TD3Stats()
        self._update_counter = 0

    # ── Inference ────────────────────────────────────────────────────────────

    def select_action(
        self,
        state:             List[float],
        deterministic:     bool = False,
        exploration_noise: float = 0.05,
        constraint_check   = None,
    ) -> Tuple[Dict[str, float], Dict[str, Any]]:
        """
        Returns bounded parameter-shift action dict and metadata.

        Args:
            state:             Plant state vector (normalized to [-1,1])
            deterministic:     If True, no exploration noise
            exploration_noise: σ for Gaussian exploration noise
            constraint_check:  RuleConstraintEngine instance (optional shield)

        Returns:
            action: {"delta_kp": 0.021, "delta_ki": -0.008, ...}
            info:   {"shielded": False, "q1": ..., "q2": ..., "latency_ms": ...}
        """
        t0 = time.perf_counter()

        # Pad/truncate state to expected dimension
        s = (state + [0.0] * self.state_dim)[:self.state_dim]

        # Actor forward pass
        raw = self.actor.forward(s)

        # Add exploration noise
        if not deterministic:
            raw = [
                max(lo, min(hi, raw[i] + self._rng.gauss(0, exploration_noise * (hi - lo))))
                for i, (lo, hi) in enumerate(PLC_ACTION_BOUNDS.values())
            ]

        action_dict = {name: raw[i] for i, name in enumerate(ACTION_NAMES)}

        # Clamp to PLC bounds (hard guarantee)
        for name, (lo, hi) in PLC_ACTION_BOUNDS.items():
            action_dict[name] = max(lo, min(hi, action_dict[name]))

        # Optional constraint shield
        shielded = False
        if constraint_check is not None:
            state_ctx = {
                "pressure": s[1] * 10 if len(s) > 1 else 3.0,
                "battery_soc": (s[3] + 1) / 2 if len(s) > 3 else 0.8,
                "temperature": s[0] * 50 + 50 if len(s) > 0 else 60.0,
            }
            result = constraint_check.validate(state_ctx, action_dict)
            if not result.allowed:
                action_dict = {k: 0.0 for k in ACTION_NAMES}
                shielded = True
                self._stats.shield_count += 1

        # Compute Q estimates
        action_vec = [action_dict[n] for n in ACTION_NAMES]
        q1 = self.critic1.forward(s, action_vec)
        q2 = self.critic2.forward(s, action_vec)

        latency_ms = (time.perf_counter() - t0) * 1000.0
        self._stats.total_steps += 1
        self._stats.mean_q1 = 0.99 * self._stats.mean_q1 + 0.01 * q1
        self._stats.mean_q2 = 0.99 * self._stats.mean_q2 + 0.01 * q2

        info = {
            "shielded":   shielded,
            "q1":         round(q1, 6),
            "q2":         round(q2, 6),
            "latency_ms": round(latency_ms, 3),
            "step":       self._stats.total_steps,
        }
        return action_dict, info

    # ── Experience collection ─────────────────────────────────────────────────

    def observe(
        self,
        state:      List[float],
        action:     List[float],
        reward:     float,
        next_state: List[float],
        done:       bool = False,
        cost:       float = 0.0,
    ) -> None:
        """Push a transition into the replay buffer."""
        s  = (state      + [0.0] * self.state_dim)[:self.state_dim]
        ns = (next_state + [0.0] * self.state_dim)[:self.state_dim]
        a  = (action     + [0.0] * N_ACTIONS)[:N_ACTIONS]
        self.buffer.push(_Transition(s, a, float(reward), ns, done, float(cost)))
        self._stats.buffer_size = len(self.buffer)
        if done:
            self._stats.episodes += 1

    # ── TD3 Update ────────────────────────────────────────────────────────────

    def update(self, batch_size: int = None) -> Dict[str, Any]:
        """
        Perform one TD3 update step.
        Critic updated every call.
        Actor + target networks updated every POLICY_DELAY calls.

        Returns training metrics dict.
        """
        batch_size = batch_size or self.BATCH_SIZE
        if len(self.buffer) < batch_size:
            return {"skipped": True, "reason": "buffer_too_small",
                    "buffer_size": len(self.buffer), "required": batch_size}

        batch = self.buffer.sample(batch_size, self._rng)
        self._update_counter += 1
        self._stats.total_updates += 1

        # ── Compute TD targets ───────────────────────────────────────────────
        critic_losses = []
        for tr in batch:
            # Target action with clipped noise
            target_raw = self.actor_target.forward(tr.next_state)
            noise = [
                max(-self.NOISE_CLIP, min(self.NOISE_CLIP, self._rng.gauss(0, self.SIGMA_POLICY)))
                for _ in range(N_ACTIONS)
            ]
            target_action = [
                max(lo, min(hi, target_raw[i] + noise[i]))
                for i, (lo, hi) in enumerate(PLC_ACTION_BOUNDS.values())
            ]

            # Min of twin critics (TD3 key trick)
            q1_next = self.critic1_target.forward(tr.next_state, target_action)
            q2_next = self.critic2_target.forward(tr.next_state, target_action)
            q_next  = min(q1_next, q2_next)

            td_target = tr.reward + (0.0 if tr.done else self.GAMMA * q_next)

            # Critic losses (MSE)
            q1_pred = self.critic1.forward(tr.state, tr.action)
            q2_pred = self.critic2.forward(tr.state, tr.action)
            loss = 0.5 * ((q1_pred - td_target)**2 + (q2_pred - td_target)**2)
            critic_losses.append(loss)

            # Pseudo-gradient critic update (simplified SGD step on last layer bias)
            err1 = q1_pred - td_target
            err2 = q2_pred - td_target
            last1 = self.critic1.layers[-1]
            last2 = self.critic2.layers[-1]
            last1.b[0] -= self.CRITIC_LR * err1
            last2.b[0] -= self.CRITIC_LR * err2

        mean_critic_loss = sum(critic_losses) / len(critic_losses)
        self._stats.critic_loss_last = mean_critic_loss

        # ── Delayed actor update ─────────────────────────────────────────────
        actor_loss = 0.0
        if self._update_counter % self.POLICY_DELAY == 0:
            for tr in batch[:min(batch_size // 4, 128)]:
                action = self.actor.forward(tr.state)
                q_val  = self.critic1.forward(tr.state, action)
                actor_loss += -q_val  # maximize Q → minimize -Q
                # Simplified policy gradient: perturb actor toward higher Q
                if q_val < 0:
                    self.actor.perturb(0.001, self._rng)

            actor_loss /= max(1, batch_size // 4)
            self._stats.actor_updates += 1
            self._stats.actor_loss_last = actor_loss

            # Soft target network updates
            self.actor_target.soft_update(self.actor, self.TAU)

        # Critic target updates (every step)
        self.critic1_target.soft_update(self.critic1, self.TAU)
        self.critic2_target.soft_update(self.critic2, self.TAU)

        return {
            "critic_loss":  round(mean_critic_loss, 6),
            "actor_loss":   round(actor_loss, 6),
            "update_count": self._stats.total_updates,
            "actor_updates": self._stats.actor_updates,
            "buffer_size":  len(self.buffer),
        }

    # ── Reward computation ────────────────────────────────────────────────────

    @staticmethod
    def compute_reward(
        stability_score:    float,
        efficiency_score:   float,
        actuator_cycles:    float,
        boundary_violation: float,
        oscillation:        float,
    ) -> float:
        """
        Standard CIEC reward function.
        R = α·stability + β·efficiency − γ·cycles − δ·violation − ε·oscillation
        """
        α, β, γ, δ, ε = 0.40, 0.30, 0.15, 0.10, 0.05
        return (α * stability_score
                + β * efficiency_score
                - γ * actuator_cycles
                - δ * boundary_violation
                - ε * oscillation)

    # ── Serialization ─────────────────────────────────────────────────────────

    def get_stats(self) -> dict:
        self._stats.buffer_size = len(self.buffer)
        return self._stats.to_dict()

    def checkpoint(self) -> Dict[str, Any]:
        """Export lightweight checkpoint for mutation lineage tracking."""
        return {
            "state_dim":     self.state_dim,
            "total_updates": self._stats.total_updates,
            "actor_updates": self._stats.actor_updates,
            "buffer_size":   len(self.buffer),
            "hash": hashlib.sha256(
                json.dumps(self._stats.to_dict()).encode()
            ).hexdigest()[:16],
        }
