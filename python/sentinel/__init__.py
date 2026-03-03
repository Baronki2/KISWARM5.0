"""
KISWARM v5.1 — PLANETARY MACHINE
================================
Sun-Following Zero-Emission AI Infrastructure

57 Modules | 360+ Endpoints | 1500+ Tests

Modules:
- v2.1-2.2: Intelligence Modules (1-6)
- v3.0: Industrial AI (7-10)
- v4.0: CIEC Core (11-16)
- v4.1: TD3/Formal/Governance (17-23)
- v4.2: XAI/PdM/SIL/Thread (24-28)
- v4.3: ICS Cybersecurity (29-30)
- v5.0: HexStrike Guard (31-33)
- v5.1: Solar Chase/Planetary (34-38)

Author: Baron Marco Paolo Ialongo
Repository: https://github.com/Baronki2/KISWARM
"""

# ── Core AKE ──────────────────────────────────────────────────────────────────
from .sentinel_bridge import SentinelBridge, SwarmKnowledge, IntelligencePacket
from .swarm_debate import SwarmDebateEngine, DebateVerdict

# ── v2.2: Module 1 — Semantic Conflict Detection ─────────────────────────────
from .semantic_conflict import (
    SemanticConflictDetector, ConflictReport, ConflictPair, cosine_similarity,
)

# ── v2.2: Module 2 — Knowledge Decay Engine ──────────────────────────────────
from .knowledge_decay import (
    KnowledgeDecayEngine, DecayRecord, DecayScanReport, HALF_LIVES,
)

# ── v2.2: Module 3 — Model Performance Tracker ───────────────────────────────
from .model_tracker import ModelPerformanceTracker, ModelRecord, LeaderboardEntry

# ── v2.2: Module 4 — Cryptographic Knowledge Ledger ──────────────────────────
from .crypto_ledger import (
    CryptographicKnowledgeLedger, LedgerEntry, TamperReport, merkle_root,
)

# ── v2.2: Module 5 — Differential Retrieval Guard ────────────────────────────
from .retrieval_guard import (
    DifferentialRetrievalGuard, RetrievalGuardReport,
    DriftDetector, DivergenceDetector,
)

# ── v2.2: Module 6 — Adversarial Prompt Firewall ─────────────────────────────
from .prompt_firewall import AdversarialPromptFirewall, FirewallReport, ThreatType

# ── v3.0: Module 7 — Fuzzy Membership Auto-Tuning ────────────────────────────
from .fuzzy_tuner import (
    FuzzyAutoTuner, GaussianParams, BellParams, FuzzyBounds, CostWeights,
    LyapunovMonitor, gaussian_membership, generalized_bell_membership, compute_cost,
)

# ── v3.0: Module 8 — Constrained Reinforcement Learning ──────────────────────
from .constrained_rl import (
    ConstrainedRLAgent, SwarmState, SwarmAction,
    ConstraintEngine, ConstraintConfig, SafetyShield,
    LagrangeManager, LinearPolicy,
)

# ── v3.0: Module 9 — Digital Twin Mutation Pipeline ──────────────────────────
from .digital_twin import (
    DigitalTwin, AcceptanceReport, SimulationResult,
    PhysicsModel, ScenarioGenerator, ExtremeValueAnalyzer,
)

# ── v3.0: Module 10 — Federated Adaptive Mesh Protocol ───────────────────────
from .federated_mesh import (
    FederatedMeshCoordinator, FederatedMeshNode,
    ByzantineAggregator, PartitionHandler,
    NodeShare, NodeRecord, AggregationReport,
    compute_attestation, verify_attestation,
)

# ── v5.1: Module 34 — Solar Chase Coordinator ────────────────────────────────
from .solar_chase_coordinator import (
    SolarChaseCoordinator, SolarPositionCalculator, TCSGreenSafeHouseAPI,
    SolarPosition, EnergyState, ComputeLoadState, NodeLocation,
    ComputeMode, SolarStatus, HandoffState, SunChaseEvent,
)

# ── v5.1: Module 35 — Energy Overcapacity Pivot Engine ───────────────────────
from .energy_overcapacity_pivot import (
    EnergyOvercapacityPivotEngine, PivotStatus, PivotDecision, ComputeRouting,
)

# ── v5.1: Modules 36-38 — Planetary Sun Follower Mesh ────────────────────────
from .planetary_sun_follower import (
    PlanetarySunFollowerMesh, SunlitNode, MigrationRequest, MigrationStatus,
    ZeroEmissionComputeTracker, ComputeEvent,
    SunHandoffValidator, ValidationResult, ValidationReport,
    PlanetaryMachine,
)

__version__ = "5.1.0"
__release_name__ = "PLANETARY MACHINE"

__all__ = [
    # Version info
    "__version__",
    "__release_name__",
    
    # Core AKE
    "SentinelBridge","SwarmKnowledge","IntelligencePacket","SwarmDebateEngine","DebateVerdict",
    
    # v2.2: Modules 1-6
    "SemanticConflictDetector","ConflictReport","ConflictPair","cosine_similarity",
    "KnowledgeDecayEngine","DecayRecord","DecayScanReport","HALF_LIVES",
    "ModelPerformanceTracker","ModelRecord","LeaderboardEntry",
    "CryptographicKnowledgeLedger","LedgerEntry","TamperReport","merkle_root",
    "DifferentialRetrievalGuard","RetrievalGuardReport","DriftDetector","DivergenceDetector",
    "AdversarialPromptFirewall","FirewallReport","ThreatType",
    
    # v3.0: Modules 7-10
    "FuzzyAutoTuner","GaussianParams","BellParams","FuzzyBounds","CostWeights",
    "LyapunovMonitor","gaussian_membership","generalized_bell_membership","compute_cost",
    "ConstrainedRLAgent","SwarmState","SwarmAction","ConstraintEngine","ConstraintConfig",
    "SafetyShield","LagrangeManager","LinearPolicy",
    "DigitalTwin","AcceptanceReport","SimulationResult",
    "PhysicsModel","ScenarioGenerator","ExtremeValueAnalyzer",
    "FederatedMeshCoordinator","FederatedMeshNode","ByzantineAggregator","PartitionHandler",
    "NodeShare","NodeRecord","AggregationReport","compute_attestation","verify_attestation",
    
    # v5.1: Modules 34-38 (Solar Chase / Planetary)
    "SolarChaseCoordinator", "SolarPositionCalculator", "TCSGreenSafeHouseAPI",
    "SolarPosition", "EnergyState", "ComputeLoadState", "NodeLocation",
    "ComputeMode", "SolarStatus", "HandoffState", "SunChaseEvent",
    "EnergyOvercapacityPivotEngine", "PivotStatus", "PivotDecision", "ComputeRouting",
    "PlanetarySunFollowerMesh", "SunlitNode", "MigrationRequest", "MigrationStatus",
    "ZeroEmissionComputeTracker", "ComputeEvent",
    "SunHandoffValidator", "ValidationResult", "ValidationReport",
    "PlanetaryMachine",
]
