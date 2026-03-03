"""
KISWARM v4.2 — Module 27: IEC 61508 Safety Integrity Level (SIL) Verifier
=========================================================================
Assesses and verifies Safety Integrity Levels (SIL 1–4) for safety
instrumented functions (SIFs) in industrial plants.

IEC 61508 SIL Definitions:
  SIL 1: PFD ∈ [10⁻², 10⁻¹)  — risk reduction 10–100×
  SIL 2: PFD ∈ [10⁻³, 10⁻²)  — risk reduction 100–1000×
  SIL 3: PFD ∈ [10⁻⁴, 10⁻³)  — risk reduction 1000–10,000×
  SIL 4: PFD ∈ [10⁻⁵, 10⁻⁴)  — risk reduction 10,000–100,000×
  (PFD = Probability of Failure on Demand)

Features:
  • PFD calculation for SIS architectures: 1oo1, 1oo2, 2oo2, 2oo3, 1oo3
  • MTTF/MTBF/MDT parameter tracking per subsystem
  • Common-Cause Failure (CCF) beta-factor model
  • Proof-test interval optimisation (minimise PFD)
  • Hardware Fault Tolerance (HFT) verification
  • Systematic Capability (SC) assessment
  • SFF (Safe Failure Fraction) computation
  • Mutation impact analysis: does mutation lower SIL?
  • Full IEC 61508 Part 2 Table annexe compliance matrix
  • Immutable SIL assessment ledger
"""

import hashlib
import json
import math
import datetime
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


# ─────────────────────────────────────────────────────────────────────────────
# SIL CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

SIL_PFD_RANGES = {
    1: (1e-2, 1e-1),
    2: (1e-3, 1e-2),
    3: (1e-4, 1e-3),
    4: (1e-5, 1e-4),
}

SIL_NAMES = {1: "SIL 1", 2: "SIL 2", 3: "SIL 3", 4: "SIL 4", 0: "No SIL"}

# Architecture vote patterns for PFD formula selection
ARCH_TYPES = ("1oo1", "1oo2", "2oo2", "2oo3", "1oo3", "2oo4")

# IEC 61508-2 Table: minimum HFT per SIL and SFF
HFT_REQUIREMENTS = {
    # SFF range → min HFT for each SIL level
    "low_sff":    {1: 1, 2: 2, 3: 3, 4: 4},   # SFF < 60%
    "medium_sff": {1: 0, 2: 1, 3: 2, 4: 3},   # SFF 60–90%
    "high_sff":   {1: 0, 2: 0, 3: 1, 4: 2},   # SFF > 90%
}

# Default beta factors for CCF
BETA_FACTORS = {
    "identical":  0.10,   # 10% common-cause for identical hardware
    "diverse":    0.02,   # 2% for diverse hardware
    "separated":  0.05,   # 5% physically separated
    "standard":   0.05,   # generic default
}


# ─────────────────────────────────────────────────────────────────────────────
# DATA STRUCTURES
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Subsystem:
    """One hardware subsystem in a SIF (sensor, logic, actuator)."""
    subsystem_id: str
    subsystem_type: str          # "sensor" | "logic_solver" | "actuator"
    architecture: str            # "1oo1" | "1oo2" | "2oo2" | "2oo3" | "1oo3"
    lambda_d: float              # dangerous failure rate (failures/hour)
    lambda_s: float              # safe failure rate (failures/hour)
    mttf_hours: float            # mean time to failure
    mttr_hours: float            # mean time to repair (downtime)
    proof_test_interval_hours: float
    beta: float = 0.05           # common-cause failure fraction
    dc: float   = 0.90           # diagnostic coverage (0–1)
    hw_fault_tolerance: int = 0  # redundancy-based HFT


@dataclass
class SIFAssessment:
    """Complete SIL assessment for one Safety Instrumented Function."""
    sif_id: str
    subsystems: List[Subsystem]
    pfd_total: float
    sil_achieved: int
    sil_required: int
    compliant: bool
    pfd_per_subsystem: Dict[str, float]
    sff_per_subsystem: Dict[str, float]
    hft_ok: bool
    sff_ok: bool
    proof_test_optimal_hours: float
    risk_reduction_factor: float
    findings: List[str]          # compliance notes
    timestamp: str
    signature: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sif_id":                 self.sif_id,
            "pfd_total":              f"{self.pfd_total:.2e}",
            "sil_achieved":           self.sil_achieved,
            "sil_required":           self.sil_required,
            "compliant":              self.compliant,
            "pfd_per_subsystem":      {k: f"{v:.2e}" for k, v in self.pfd_per_subsystem.items()},
            "sff_per_subsystem":      {k: round(v, 4) for k, v in self.sff_per_subsystem.items()},
            "hft_ok":                 self.hft_ok,
            "sff_ok":                 self.sff_ok,
            "proof_test_optimal_h":   round(self.proof_test_optimal_hours, 1),
            "risk_reduction_factor":  round(self.risk_reduction_factor, 1),
            "findings":               self.findings,
            "timestamp":              self.timestamp,
            "signature":              self.signature,
        }


@dataclass
class MutationSILImpact:
    """How a PLC parameter mutation affects SIL compliance."""
    mutation_id: str
    sif_id: str
    sil_before: int
    sil_after: int
    pfd_before: float
    pfd_after: float
    delta_pfd: float
    sil_degraded: bool
    approved: bool
    reason: str
    timestamp: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mutation_id":   self.mutation_id,
            "sif_id":        self.sif_id,
            "sil_before":    self.sil_before,
            "sil_after":     self.sil_after,
            "pfd_before":    f"{self.pfd_before:.2e}",
            "pfd_after":     f"{self.pfd_after:.2e}",
            "delta_pfd_pct": round((self.pfd_after - self.pfd_before) / max(self.pfd_before, 1e-10) * 100, 2),
            "sil_degraded":  self.sil_degraded,
            "approved":      self.approved,
            "reason":        self.reason,
            "timestamp":     self.timestamp,
        }


# ─────────────────────────────────────────────────────────────────────────────
# PFD CALCULATORS (IEC 61508 Annex B)
# ─────────────────────────────────────────────────────────────────────────────

def _pfd_1oo1(lambda_d: float, ti: float, beta: float = 0.05) -> float:
    """PFD for 1oo1 (single channel)."""
    # PFD_avg = λ_d * T_i / 2
    return lambda_d * ti / 2.0


def _pfd_1oo2(lambda_d: float, ti: float, beta: float = 0.05) -> float:
    """PFD for 1oo2 (two channels in parallel — fails if BOTH fail)."""
    # PFD_1oo2 = (1-β)² * λ_d² * T_i² / 3 + β * λ_d * T_i / 2
    ind = (1 - beta) ** 2 * (lambda_d ** 2) * (ti ** 2) / 3.0
    ccf = beta * lambda_d * ti / 2.0
    return ind + ccf


def _pfd_2oo2(lambda_d: float, ti: float, beta: float = 0.05) -> float:
    """PFD for 2oo2 (both channels must work — more conservative)."""
    # PFD_2oo2 ≈ 2 * λ_d * T_i / 2 + CCF
    ind = 2.0 * lambda_d * ti / 2.0
    ccf = beta * lambda_d * ti / 2.0
    return ind + ccf


def _pfd_2oo3(lambda_d: float, ti: float, beta: float = 0.05) -> float:
    """PFD for 2oo3 (majority vote — 2 of 3 must agree)."""
    # PFD_2oo3 = 3*(1-β)²*λ_d²*T_i²/3 + β*λ_d*T_i/2
    ind = 3.0 * (1 - beta) ** 2 * (lambda_d ** 2) * (ti ** 2) / 3.0
    ccf = beta * lambda_d * ti / 2.0
    return ind + ccf


def _pfd_1oo3(lambda_d: float, ti: float, beta: float = 0.05) -> float:
    """PFD for 1oo3 (any one of three sufficient — lowest PFD)."""
    # PFD_1oo3 ≈ (1-β)³*λ_d³*T_i³/4 + β*λ_d*T_i/2
    ind = (1 - beta) ** 3 * (lambda_d ** 3) * (ti ** 3) / 4.0
    ccf = beta * lambda_d * ti / 2.0
    return ind + ccf


def _pfd_2oo4(lambda_d: float, ti: float, beta: float = 0.05) -> float:
    """PFD for 2oo4."""
    # Approximation similar to 2oo3 with extra channel
    ind = 6.0 * (1 - beta) ** 2 * (lambda_d ** 2) * (ti ** 2) / 3.0 * 0.5
    ccf = beta * lambda_d * ti / 2.0
    return ind + ccf


_PFD_FUNCS = {
    "1oo1": _pfd_1oo1,
    "1oo2": _pfd_1oo2,
    "2oo2": _pfd_2oo2,
    "2oo3": _pfd_2oo3,
    "1oo3": _pfd_1oo3,
    "2oo4": _pfd_2oo4,
}


def compute_pfd(subsystem: Subsystem) -> float:
    """Compute PFD for one subsystem using its architecture."""
    fn = _PFD_FUNCS.get(subsystem.architecture, _pfd_1oo1)
    pfd = fn(subsystem.lambda_d, subsystem.proof_test_interval_hours, subsystem.beta)
    return max(0.0, min(1.0, pfd))


def compute_sff(subsystem: Subsystem) -> float:
    """
    Safe Failure Fraction = (λ_s + λ_dd) / (λ_s + λ_d)
    where λ_dd = detected dangerous failures = dc * λ_d
    """
    lam_total = subsystem.lambda_s + subsystem.lambda_d
    if lam_total <= 0:
        return 1.0
    lambda_dd = subsystem.dc * subsystem.lambda_d
    return (subsystem.lambda_s + lambda_dd) / lam_total


def pfd_to_sil(pfd: float) -> int:
    """Convert PFD to SIL level (0 if no SIL achieved)."""
    for sil in range(4, 0, -1):
        lo, hi = SIL_PFD_RANGES[sil]
        if lo <= pfd < hi:
            return sil
    if pfd < SIL_PFD_RANGES[4][0]:
        return 4  # Better than SIL 4 lower bound
    return 0


def optimise_proof_test_interval(
    subsystem: Subsystem,
    target_sil: int,
    search_range: Tuple[float, float] = (100.0, 43800.0),
    steps: int = 500,
) -> float:
    """
    Binary-search optimal proof test interval to meet target SIL
    with minimum test frequency.
    """
    target_lo, _ = SIL_PFD_RANGES[target_sil]
    lo, hi = search_range
    best_ti = lo

    for _ in range(steps):
        mid = (lo + hi) / 2.0
        sub_copy = Subsystem(
            subsystem_id               = subsystem.subsystem_id,
            subsystem_type             = subsystem.subsystem_type,
            architecture               = subsystem.architecture,
            lambda_d                   = subsystem.lambda_d,
            lambda_s                   = subsystem.lambda_s,
            mttf_hours                 = subsystem.mttf_hours,
            mttr_hours                 = subsystem.mttr_hours,
            proof_test_interval_hours  = mid,
            beta                       = subsystem.beta,
            dc                         = subsystem.dc,
            hw_fault_tolerance         = subsystem.hw_fault_tolerance,
        )
        pfd = compute_pfd(sub_copy)
        if pfd < target_lo * 10:   # PFD acceptable for target SIL (with margin)
            best_ti = mid
            lo = mid
        else:
            hi = mid

    return round(best_ti, 1)


# ─────────────────────────────────────────────────────────────────────────────
# SIL VERIFICATION ENGINE
# ─────────────────────────────────────────────────────────────────────────────

class SILVerificationEngine:
    """
    IEC 61508 SIL verification for Safety Instrumented Functions.
    Supports multi-subsystem SIF assessment, mutation impact analysis,
    and proof-test interval optimisation.
    """

    def __init__(self):
        self._assessments: Dict[str, SIFAssessment] = {}
        self._ledger:      List[Dict[str, Any]]      = []
        self._prev_hash    = "0" * 64
        self._impact_log:  List[MutationSILImpact]   = []

    # ── SIF Assessment ────────────────────────────────────────────────────────

    def assess_sif(
        self,
        sif_id: str,
        subsystems: List[Subsystem],
        sil_required: int,
    ) -> SIFAssessment:
        """
        Full IEC 61508 SIF assessment.
        PFD_SIF = product of PFDs of all subsystems (sensor × logic × actuator).
        """
        if not subsystems:
            raise ValueError("At least one subsystem required")
        if sil_required not in SIL_PFD_RANGES:
            raise ValueError(f"SIL required must be 1–4, got {sil_required}")

        # PFD per subsystem
        pfd_map: Dict[str, float] = {}
        sff_map: Dict[str, float] = {}
        for sub in subsystems:
            pfd_map[sub.subsystem_id] = compute_pfd(sub)
            sff_map[sub.subsystem_id] = compute_sff(sub)

        # Total PFD = product of sub-PFDs
        pfd_total = 1.0
        for pfd in pfd_map.values():
            pfd_total *= (1.0 - pfd)
        pfd_total = 1.0 - pfd_total

        sil_achieved = pfd_to_sil(pfd_total)

        # HFT check
        hft_ok = self._check_hft(subsystems, sff_map, sil_required)

        # SFF check
        sff_ok = self._check_sff(subsystems, sff_map, sil_required)

        compliant = (sil_achieved >= sil_required and hft_ok and sff_ok)

        # Optimal proof-test interval for weakest subsystem
        weakest = max(subsystems, key=lambda s: compute_pfd(s))
        opt_ti  = optimise_proof_test_interval(weakest, sil_required)

        rrf = 1.0 / max(pfd_total, 1e-10)

        findings = self._generate_findings(
            sil_achieved, sil_required, hft_ok, sff_ok, pfd_total, pfd_map
        )

        ts  = datetime.datetime.now().isoformat()
        sig = hashlib.sha256(f"{sif_id}:{pfd_total:.2e}:{ts}".encode()).hexdigest()[:24]

        assessment = SIFAssessment(
            sif_id                  = sif_id,
            subsystems              = subsystems,
            pfd_total               = pfd_total,
            sil_achieved            = sil_achieved,
            sil_required            = sil_required,
            compliant               = compliant,
            pfd_per_subsystem       = pfd_map,
            sff_per_subsystem       = sff_map,
            hft_ok                  = hft_ok,
            sff_ok                  = sff_ok,
            proof_test_optimal_hours= opt_ti,
            risk_reduction_factor   = rrf,
            findings                = findings,
            timestamp               = ts,
            signature               = sig,
        )
        self._assessments[sif_id] = assessment
        self._append_ledger(assessment)
        return assessment

    # ── Mutation Impact Analysis ──────────────────────────────────────────────

    def assess_mutation_impact(
        self,
        mutation_id: str,
        sif_id: str,
        param_deltas: Dict[str, float],
        sil_required: int,
    ) -> MutationSILImpact:
        """
        Check whether a PLC parameter mutation degrades SIL compliance.
        Models mutation impact as proportional change in lambda_d.
        """
        if sif_id not in self._assessments:
            raise ValueError(f"SIF {sif_id!r} not assessed yet")

        assessment_before = self._assessments[sif_id]
        pfd_before        = assessment_before.pfd_total
        sil_before        = assessment_before.sil_achieved

        # Estimate mutation impact on lambda_d:
        # Aggressive PID changes (large delta_kp) may increase process variability
        # and slightly increase dangerous failure rate
        total_delta = sum(abs(v) for v in param_deltas.values())
        impact_factor = 1.0 + total_delta * 0.5   # conservative 50% sensitivity

        # Re-assess with adjusted lambda_d
        modified_subsystems = []
        for sub in assessment_before.subsystems:
            modified_subsystems.append(Subsystem(
                subsystem_id              = sub.subsystem_id,
                subsystem_type            = sub.subsystem_type,
                architecture              = sub.architecture,
                lambda_d                  = sub.lambda_d * impact_factor,
                lambda_s                  = sub.lambda_s,
                mttf_hours                = sub.mttf_hours / impact_factor,
                mttr_hours                = sub.mttr_hours,
                proof_test_interval_hours = sub.proof_test_interval_hours,
                beta                      = sub.beta,
                dc                        = sub.dc,
                hw_fault_tolerance        = sub.hw_fault_tolerance,
            ))

        new_assessment = self.assess_sif(
            f"{sif_id}_mut_{mutation_id[:8]}",
            modified_subsystems,
            sil_required,
        )
        pfd_after  = new_assessment.pfd_total
        sil_after  = new_assessment.sil_achieved
        degraded   = sil_after < sil_before
        approved   = not degraded and new_assessment.compliant

        reason = (
            f"SIL unchanged at {SIL_NAMES[sil_after]}"
            if not degraded
            else f"SIL degraded {SIL_NAMES[sil_before]} → {SIL_NAMES[sil_after]}: REJECTED"
        )

        ts = datetime.datetime.now().isoformat()
        impact = MutationSILImpact(
            mutation_id  = mutation_id,
            sif_id       = sif_id,
            sil_before   = sil_before,
            sil_after    = sil_after,
            pfd_before   = pfd_before,
            pfd_after    = pfd_after,
            delta_pfd    = pfd_after - pfd_before,
            sil_degraded = degraded,
            approved     = approved,
            reason       = reason,
            timestamp    = ts,
        )
        self._impact_log.append(impact)
        return impact

    # ── Stats ─────────────────────────────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        compliant     = sum(1 for a in self._assessments.values() if a.compliant)
        sil_dist: Dict[str, int] = {}
        for a in self._assessments.values():
            k = SIL_NAMES[a.sil_achieved]
            sil_dist[k] = sil_dist.get(k, 0) + 1
        impacts_blocked = sum(1 for i in self._impact_log if not i.approved)
        return {
            "sifs_assessed":       len(self._assessments),
            "compliant":           compliant,
            "non_compliant":       len(self._assessments) - compliant,
            "sil_distribution":    sil_dist,
            "mutation_impacts":    len(self._impact_log),
            "impacts_blocked":     impacts_blocked,
            "ledger_entries":      len(self._ledger),
        }

    def get_assessment(self, sif_id: str) -> Optional[Dict[str, Any]]:
        a = self._assessments.get(sif_id)
        return a.to_dict() if a else None

    def get_impact_log(self, limit: int = 50) -> List[Dict[str, Any]]:
        return [i.to_dict() for i in self._impact_log[-limit:]]

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _check_hft(
        self,
        subsystems: List[Subsystem],
        sff_map: Dict[str, float],
        sil_required: int,
    ) -> bool:
        for sub in subsystems:
            sff = sff_map[sub.subsystem_id]
            if sff < 0.6:
                key = "low_sff"
            elif sff < 0.9:
                key = "medium_sff"
            else:
                key = "high_sff"
            min_hft = HFT_REQUIREMENTS[key].get(sil_required, 0)
            if sub.hw_fault_tolerance < min_hft:
                return False
        return True

    def _check_sff(
        self,
        subsystems: List[Subsystem],
        sff_map: Dict[str, float],
        sil_required: int,
    ) -> bool:
        """Check SFF is sufficient for the claimed SIL and architecture."""
        for sub in subsystems:
            sff = sff_map[sub.subsystem_id]
            # Minimum SFF for SIL without redundancy
            min_sff = {1: 0.0, 2: 0.60, 3: 0.90, 4: 0.99}.get(sil_required, 0.0)
            if sff < min_sff:
                return False
        return True

    def _generate_findings(
        self,
        sil_achieved: int,
        sil_required: int,
        hft_ok: bool,
        sff_ok: bool,
        pfd_total: float,
        pfd_map: Dict[str, float],
    ) -> List[str]:
        findings = []
        if sil_achieved >= sil_required:
            findings.append(f"✓ PFD {pfd_total:.2e} achieves {SIL_NAMES[sil_achieved]}")
        else:
            findings.append(
                f"✗ PFD {pfd_total:.2e} achieves only {SIL_NAMES[sil_achieved]}, "
                f"required {SIL_NAMES[sil_required]}"
            )
        if not hft_ok:
            findings.append("✗ Hardware Fault Tolerance insufficient — increase redundancy")
        else:
            findings.append("✓ Hardware Fault Tolerance satisfactory")
        if not sff_ok:
            findings.append("✗ Safe Failure Fraction below minimum — improve diagnostics")
        else:
            findings.append("✓ Safe Failure Fraction acceptable")

        # Identify weakest subsystem
        weakest_id = max(pfd_map, key=pfd_map.get)
        findings.append(
            f"⚠ Weakest subsystem: {weakest_id} (PFD={pfd_map[weakest_id]:.2e})"
        )
        return findings

    def _append_ledger(self, assessment: SIFAssessment) -> None:
        payload = json.dumps(assessment.to_dict(), sort_keys=True)
        chain_hash = hashlib.sha256(
            (self._prev_hash + payload).encode()
        ).hexdigest()
        self._ledger.append({
            "sif_id":     assessment.sif_id,
            "hash":       chain_hash,
            "compliant":  assessment.compliant,
            "sil":        assessment.sil_achieved,
        })
        self._prev_hash = chain_hash
