"""
KISWARM v4.2 — Module 26: Multi-Agent Plant Coordinator
=======================================================
Coordinates N specialised TD3 agents across plant sections.
Each agent controls its own PLC loop; a consensus layer prevents
conflicting actions from destabilising shared resources (power bus,
cooling water, compressed air headers).

Architecture:
  • AgentPool: N independent TD3-like agents (lightweight actors)
  • SharedResourceMonitor: tracks contention on shared utilities
  • ConsensusProtocol: 3-phase commit prevents conflicting actions
  • ConflictResolver: detects and resolves agent action conflicts
  • CoordinatorBus: message passing between agents
  • RewardShaper: blends local reward with global coordination bonus

Plant Sections (configurable):
  Default: pump_station, reactor, separator, compressor, heat_exchanger

Consensus Algorithm:
  1. Each agent proposes action independently
  2. Proposals broadcast on CoordinatorBus
  3. ConflictResolver checks resource constraints
  4. If no conflict → all agents commit
  5. If conflict → resolver arbitrates by priority × health index
  6. Arbitrated agents receive coordination penalty in reward
"""

import hashlib
import math
import random
import datetime
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple


# ─────────────────────────────────────────────────────────────────────────────
# DEFAULT PLANT SECTION DEFINITIONS
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_SECTIONS = {
    "pump_station":    {"priority": 1, "power_kw": 75.0,  "cooling_m3h": 5.0,  "air_bar": 0.0},
    "reactor":         {"priority": 2, "power_kw": 120.0, "cooling_m3h": 20.0, "air_bar": 0.0},
    "separator":       {"priority": 3, "power_kw": 30.0,  "cooling_m3h": 8.0,  "air_bar": 2.0},
    "compressor":      {"priority": 2, "power_kw": 200.0, "cooling_m3h": 15.0, "air_bar": 0.0},
    "heat_exchanger":  {"priority": 4, "power_kw": 10.0,  "cooling_m3h": 25.0, "air_bar": 0.0},
}

SHARED_RESOURCE_LIMITS = {
    "power_kw":    500.0,   # total plant power budget
    "cooling_m3h": 80.0,    # total cooling water flow
    "air_bar":     8.0,     # compressed air header
}


# ─────────────────────────────────────────────────────────────────────────────
# DATA STRUCTURES
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class AgentProposal:
    """One agent's proposed action for this timestep."""
    agent_id: str
    section_id: str
    action: Dict[str, float]          # e.g. {"delta_kp": 0.02, "delta_ki": -0.01}
    resource_delta: Dict[str, float]  # additional resource demand
    q_value: float                    # agent's expected Q for this action
    priority: int
    timestamp: str


@dataclass
class ConsensusResult:
    """Result of the 3-phase consensus round."""
    round_id: int
    n_agents: int
    n_conflicts: int
    committed_proposals: List[AgentProposal]
    arbitrated_proposals: List[AgentProposal]  # modified by resolver
    resource_utilisation: Dict[str, float]     # % of each limit used
    coordination_bonus: float                  # global reward signal
    timestamp: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "round_id":             self.round_id,
            "n_agents":             self.n_agents,
            "n_conflicts":          self.n_conflicts,
            "committed":            [p.agent_id for p in self.committed_proposals],
            "arbitrated":           [p.agent_id for p in self.arbitrated_proposals],
            "resource_utilisation": {k: round(v, 3) for k, v in self.resource_utilisation.items()},
            "coordination_bonus":   round(self.coordination_bonus, 4),
            "timestamp":            self.timestamp,
        }


@dataclass
class CoordinatorMessage:
    sender: str
    recipient: str   # "all" for broadcast
    msg_type: str    # "proposal" | "ack" | "nack" | "commit" | "rollback"
    payload: Dict[str, Any]
    timestamp: str


# ─────────────────────────────────────────────────────────────────────────────
# LIGHTWEIGHT SECTION AGENT
# ─────────────────────────────────────────────────────────────────────────────

class SectionAgent:
    """
    Lightweight TD3-inspired agent for one plant section.
    Uses a simple 2-layer actor with Xavier weights.
    State: local sensor readings (dim configurable)
    Action: PLC parameter deltas (bounded)
    """

    ACTION_BOUNDS = {
        "delta_kp":   (-0.05, 0.05),
        "delta_ki":   (-0.05, 0.05),
        "delta_kd":   (-0.05, 0.05),
        "delta_sp":   (-0.10, 0.10),
    }

    def __init__(self, agent_id: str, section_id: str, state_dim: int = 8, seed: int = 0):
        self.agent_id   = agent_id
        self.section_id = section_id
        self.state_dim  = state_dim
        self._rng       = random.Random(seed)

        # Simple 2-hidden-layer actor
        hidden = 64
        scale  = 1.0 / math.sqrt(state_dim)
        self._W1 = [[self._rng.gauss(0, scale) for _ in range(state_dim)] for _ in range(hidden)]
        self._b1 = [0.0] * hidden
        self._W2 = [[self._rng.gauss(0, 1.0 / math.sqrt(hidden)) for _ in range(hidden)]
                    for _ in range(len(self.ACTION_BOUNDS))]
        self._b2 = [0.0] * len(self.ACTION_BOUNDS)

        # Adaptation: simple cumulative gradient for online update
        self._returns: List[float] = []
        self.total_steps = 0

    def act(self, state: List[float], noise: float = 0.02) -> Dict[str, float]:
        """Forward pass through actor network, return bounded action dict."""
        x = state[:self.state_dim]
        while len(x) < self.state_dim:
            x.append(0.0)

        # Hidden layer (ReLU)
        h = []
        for i in range(len(self._W1)):
            pre = self._b1[i] + sum(self._W1[i][j] * x[j] for j in range(self.state_dim))
            h.append(max(0.0, pre))

        # Output layer (tanh)
        raw = []
        for i in range(len(self._W2)):
            pre = self._b2[i] + sum(self._W2[i][j] * h[j] for j in range(len(h)))
            val = math.tanh(pre) + self._rng.gauss(0, noise)
            raw.append(val)

        # Scale to bounds
        keys   = list(self.ACTION_BOUNDS.keys())
        action = {}
        for i, key in enumerate(keys):
            lo, hi = self.ACTION_BOUNDS[key]
            action[key] = max(lo, min(hi, raw[i] * hi))

        self.total_steps += 1
        return action

    def observe_reward(self, reward: float) -> None:
        self._returns.append(reward)

    def get_q_estimate(self, state: List[float], action: Dict[str, float]) -> float:
        """Simple Q estimate: discounted mean of past returns (no critic network)."""
        if not self._returns:
            return 0.0
        gamma = 0.99
        disc = sum(r * (gamma ** i) for i, r in enumerate(reversed(self._returns[-20:])))
        return disc

    def get_stats(self) -> Dict[str, Any]:
        return {
            "agent_id":     self.agent_id,
            "section_id":   self.section_id,
            "state_dim":    self.state_dim,
            "total_steps":  self.total_steps,
            "mean_return":  round(sum(self._returns[-100:]) / max(1, len(self._returns[-100:])), 4),
        }


# ─────────────────────────────────────────────────────────────────────────────
# SHARED RESOURCE MONITOR
# ─────────────────────────────────────────────────────────────────────────────

class SharedResourceMonitor:
    """Tracks cumulative demand on shared plant resources."""

    def __init__(self, limits: Optional[Dict[str, float]] = None):
        self.limits   = limits or SHARED_RESOURCE_LIMITS.copy()
        self._current: Dict[str, float] = {k: 0.0 for k in self.limits}

    def reset(self) -> None:
        self._current = {k: 0.0 for k in self.limits}

    def add_demand(self, resource_delta: Dict[str, float]) -> bool:
        """
        Try to add resource demand. Returns True if within limits.
        Does NOT commit — call commit() to make permanent.
        """
        for resource, delta in resource_delta.items():
            if resource in self.limits:
                if self._current[resource] + delta > self.limits[resource]:
                    return False
        return True

    def commit(self, resource_delta: Dict[str, float]) -> None:
        for resource, delta in resource_delta.items():
            if resource in self.limits:
                self._current[resource] += delta

    def utilisation(self) -> Dict[str, float]:
        return {
            k: round(self._current[k] / self.limits[k], 4)
            for k in self.limits
        }

    def headroom(self) -> Dict[str, float]:
        return {
            k: round(self.limits[k] - self._current[k], 2)
            for k in self.limits
        }


# ─────────────────────────────────────────────────────────────────────────────
# CONFLICT RESOLVER
# ─────────────────────────────────────────────────────────────────────────────

class ConflictResolver:
    """
    Detects and resolves conflicting agent proposals.
    Resolution strategy: priority × Q-value determines who yields.
    """

    def resolve(
        self,
        proposals: List[AgentProposal],
        monitor:   SharedResourceMonitor,
    ) -> Tuple[List[AgentProposal], List[AgentProposal], int]:
        """
        Returns: (committed, arbitrated, n_conflicts)
        arbitrated = proposals that were scaled down to fit resource budget
        """
        committed:   List[AgentProposal] = []
        arbitrated:  List[AgentProposal] = []
        n_conflicts  = 0

        monitor.reset()

        # Sort by priority ASC, then Q-value DESC (highest priority + Q commits first)
        sorted_proposals = sorted(proposals, key=lambda p: (p.priority, -p.q_value))

        for proposal in sorted_proposals:
            fits = monitor.add_demand(proposal.resource_delta)
            if fits:
                monitor.commit(proposal.resource_delta)
                committed.append(proposal)
            else:
                # Scale down action proportionally to headroom
                n_conflicts += 1
                scaled_action = self._scale_action(
                    proposal.action,
                    proposal.resource_delta,
                    monitor.headroom(),
                )
                scaled_delta = self._scale_delta(proposal.resource_delta, monitor.headroom())
                monitor.commit(scaled_delta)

                scaled = AgentProposal(
                    agent_id       = proposal.agent_id,
                    section_id     = proposal.section_id,
                    action         = scaled_action,
                    resource_delta = scaled_delta,
                    q_value        = proposal.q_value * 0.5,  # penalty
                    priority       = proposal.priority,
                    timestamp      = proposal.timestamp,
                )
                arbitrated.append(scaled)

        return committed, arbitrated, n_conflicts

    def _scale_action(
        self,
        action: Dict[str, float],
        resource_delta: Dict[str, float],
        headroom: Dict[str, float],
    ) -> Dict[str, float]:
        """Scale action magnitude to respect headroom."""
        scale = self._compute_scale(resource_delta, headroom)
        return {k: round(v * scale, 6) for k, v in action.items()}

    def _scale_delta(
        self,
        resource_delta: Dict[str, float],
        headroom: Dict[str, float],
    ) -> Dict[str, float]:
        scale = self._compute_scale(resource_delta, headroom)
        return {k: v * scale for k, v in resource_delta.items()}

    @staticmethod
    def _compute_scale(
        resource_delta: Dict[str, float],
        headroom: Dict[str, float],
    ) -> float:
        """Minimum scale factor across all constrained resources."""
        scales = []
        for resource, demand in resource_delta.items():
            if demand > 0 and resource in headroom:
                avail = max(0.0, headroom[resource])
                scales.append(avail / demand if demand > 0 else 1.0)
        return min(scales) if scales else 1.0


# ─────────────────────────────────────────────────────────────────────────────
# COORDINATOR BUS (MESSAGE PASSING)
# ─────────────────────────────────────────────────────────────────────────────

class CoordinatorBus:
    """In-process pub/sub message bus for agent coordination."""

    def __init__(self):
        self._inbox: Dict[str, List[CoordinatorMessage]] = {}
        self._log:   List[CoordinatorMessage] = []

    def subscribe(self, agent_id: str) -> None:
        if agent_id not in self._inbox:
            self._inbox[agent_id] = []

    def publish(self, message: CoordinatorMessage) -> None:
        self._log.append(message)
        if message.recipient == "all":
            for aid in self._inbox:
                if aid != message.sender:
                    self._inbox[aid].append(message)
        elif message.recipient in self._inbox:
            self._inbox[message.recipient].append(message)

    def read(self, agent_id: str) -> List[CoordinatorMessage]:
        msgs = self._inbox.get(agent_id, [])
        self._inbox[agent_id] = []
        return msgs

    def log_size(self) -> int:
        return len(self._log)


# ─────────────────────────────────────────────────────────────────────────────
# REWARD SHAPER
# ─────────────────────────────────────────────────────────────────────────────

class RewardShaper:
    """
    Blends each agent's local reward with a global coordination bonus.
    Penalises agents that caused resource conflicts.
    """

    CONFLICT_PENALTY = -0.5
    COORD_BONUS      =  0.2

    def shape(
        self,
        agent_id: str,
        local_reward: float,
        consensus_result: ConsensusResult,
    ) -> float:
        arbitrated_ids = [p.agent_id for p in consensus_result.arbitrated_proposals]
        penalty = self.CONFLICT_PENALTY if agent_id in arbitrated_ids else 0.0
        bonus   = self.COORD_BONUS * consensus_result.coordination_bonus
        return local_reward + penalty + bonus


# ─────────────────────────────────────────────────────────────────────────────
# MULTI-AGENT PLANT COORDINATOR
# ─────────────────────────────────────────────────────────────────────────────

class MultiAgentPlantCoordinator:
    """
    Top-level coordinator: manages N section agents, runs consensus rounds,
    resolves conflicts, and tracks coordination history.
    """

    def __init__(
        self,
        sections: Optional[Dict[str, Dict[str, Any]]] = None,
        resource_limits: Optional[Dict[str, float]] = None,
        seed: int = 0,
    ):
        self.sections = sections or DEFAULT_SECTIONS
        self._rng     = random.Random(seed)
        self.seed     = seed

        # Instantiate one agent per section
        self.agents: Dict[str, SectionAgent] = {}
        for i, (sid, cfg) in enumerate(self.sections.items()):
            aid = f"agent_{sid}"
            self.agents[aid] = SectionAgent(aid, sid, state_dim=8, seed=seed + i)

        self.resource_monitor = SharedResourceMonitor(resource_limits)
        self.conflict_resolver = ConflictResolver()
        self.coordinator_bus   = CoordinatorBus()
        self.reward_shaper     = RewardShaper()

        for aid in self.agents:
            self.coordinator_bus.subscribe(aid)

        self._round_id    = 0
        self._round_log: List[ConsensusResult] = []

    # ── Section Management ────────────────────────────────────────────────────

    def add_section(self, section_id: str, config: Dict[str, Any]) -> Dict[str, Any]:
        """Register a new plant section and create its agent."""
        self.sections[section_id] = config
        aid = f"agent_{section_id}"
        self.agents[aid] = SectionAgent(
            aid, section_id, state_dim=8,
            seed=self.seed + len(self.agents)
        )
        self.coordinator_bus.subscribe(aid)
        return {"registered": True, "agent_id": aid, "section_id": section_id}

    # ── Consensus Round ───────────────────────────────────────────────────────

    def step(
        self,
        states: Dict[str, List[float]],
        health_indices: Optional[Dict[str, float]] = None,
        noise: float = 0.02,
    ) -> ConsensusResult:
        """
        Run one full coordination round:
          1. Each agent proposes action given local state
          2. Proposals broadcast on bus
          3. Conflict resolver arbitrates
          4. Committed actions returned
          5. Coordination bonus computed

        Args:
            states:         {section_id: sensor_state_vector}
            health_indices: {section_id: hi_float}  (from PdM engine)
            noise:          exploration noise for all agents

        Returns:
            ConsensusResult with committed + arbitrated proposals
        """
        self._round_id += 1
        health_indices = health_indices or {}

        # Phase 1: Collect proposals
        proposals: List[AgentProposal] = []
        for aid, agent in self.agents.items():
            sid   = agent.section_id
            state = states.get(sid, [0.0] * 8)
            action = agent.act(state, noise=noise)

            # Estimate resource delta based on action magnitude
            total_action = sum(abs(v) for v in action.values())
            section_cfg  = self.sections.get(sid, {})
            resource_delta = {
                k: v * total_action * 0.1
                for k, v in {
                    "power_kw":    section_cfg.get("power_kw", 10.0),
                    "cooling_m3h": section_cfg.get("cooling_m3h", 2.0),
                    "air_bar":     section_cfg.get("air_bar", 0.0),
                }.items()
            }

            q_est = agent.get_q_estimate(state, action)
            prio  = self.sections.get(sid, {}).get("priority", 5)
            # Adjust priority by health index (lower HI → higher urgency → lower priority number)
            hi    = health_indices.get(sid, 1.0)
            effective_prio = max(1, int(prio * hi))

            proposal = AgentProposal(
                agent_id       = aid,
                section_id     = sid,
                action         = action,
                resource_delta = resource_delta,
                q_value        = q_est,
                priority       = effective_prio,
                timestamp      = datetime.datetime.now().isoformat(),
            )
            proposals.append(proposal)

            # Broadcast on bus
            self.coordinator_bus.publish(CoordinatorMessage(
                sender    = aid,
                recipient = "all",
                msg_type  = "proposal",
                payload   = {"action": action, "q": q_est},
                timestamp = proposal.timestamp,
            ))

        # Phase 2: Conflict resolution
        committed, arbitrated, n_conflicts = self.conflict_resolver.resolve(
            proposals, self.resource_monitor
        )

        # Phase 3: Coordination bonus (fraction of agents that committed without change)
        coord_bonus = len(committed) / max(1, len(proposals))

        result = ConsensusResult(
            round_id             = self._round_id,
            n_agents             = len(proposals),
            n_conflicts          = n_conflicts,
            committed_proposals  = committed,
            arbitrated_proposals = arbitrated,
            resource_utilisation = self.resource_monitor.utilisation(),
            coordination_bonus   = coord_bonus,
            timestamp            = datetime.datetime.now().isoformat(),
        )
        self._round_log.append(result)
        return result

    # ── Reward Distribution ───────────────────────────────────────────────────

    def distribute_rewards(
        self,
        local_rewards: Dict[str, float],
        consensus_result: ConsensusResult,
    ) -> Dict[str, float]:
        """Apply coordination shaping to each agent's local reward."""
        shaped = {}
        for aid, lr in local_rewards.items():
            shaped[aid] = round(
                self.reward_shaper.shape(aid, lr, consensus_result), 6
            )
        # Notify agents
        for aid, reward in shaped.items():
            if aid in self.agents:
                self.agents[aid].observe_reward(reward)
        return shaped

    # ── Summary ───────────────────────────────────────────────────────────────

    def get_agent_stats(self) -> List[Dict[str, Any]]:
        return [a.get_stats() for a in self.agents.values()]

    def get_round_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        return [r.to_dict() for r in self._round_log[-limit:]]

    def get_stats(self) -> Dict[str, Any]:
        conflicts_total = sum(r.n_conflicts for r in self._round_log)
        avg_coord = (sum(r.coordination_bonus for r in self._round_log)
                     / max(1, len(self._round_log)))
        return {
            "sections":              list(self.sections.keys()),
            "n_agents":              len(self.agents),
            "rounds_completed":      self._round_id,
            "total_conflicts":       conflicts_total,
            "avg_coordination_bonus":round(avg_coord, 4),
            "resource_limits":       self.resource_monitor.limits,
            "bus_messages_total":    self.coordinator_bus.log_size(),
        }
