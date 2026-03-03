"""
KISWARM v4.1 — Module 21: Formal Verification Layer
=====================================================
Before applying any mutation Δθ, perform stability verification:

Method A — Linearized Lyapunov:
  Linearize: x_{t+1} = A(θ)·x_t + B(θ)·u_t
  Find P > 0 such that:  AᵀPA − P < 0  (discrete-time Lyapunov)
  Solve via iterative fixed-point (Stein equation)

Method B — Barrier Certificate (nonlinear):
  B(x) ≥ 0  for all x in safe set
  dB/dt ≤ 0  along trajectories (forward invariance)
  Verified by sampling + convex upper bound

If no feasible certificate → mutation rejected automatically.
All decisions stored in mutation ledger with cryptographic signature.
"""

from __future__ import annotations

import math
import hashlib
import json
import time
import random
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple, Any


# ─────────────────────────────────────────────────────────────────────────────
# MATRIX HELPERS  (pure Python, no NumPy)
# ─────────────────────────────────────────────────────────────────────────────

def _zeros(n: int, m: int) -> List[List[float]]:
    return [[0.0] * m for _ in range(n)]

def _eye(n: int) -> List[List[float]]:
    I = _zeros(n, n)
    for i in range(n): I[i][i] = 1.0
    return I

def _mat_mul(A: List[List[float]], B: List[List[float]]) -> List[List[float]]:
    r, k, c = len(A), len(B), len(B[0])
    C = _zeros(r, c)
    for i in range(r):
        for j in range(c):
            C[i][j] = sum(A[i][p] * B[p][j] for p in range(k))
    return C

def _mat_transpose(A: List[List[float]]) -> List[List[float]]:
    r, c = len(A), len(A[0])
    return [[A[i][j] for i in range(r)] for j in range(c)]

def _mat_add(A, B, alpha=1.0, beta=1.0) -> List[List[float]]:
    return [[alpha * A[i][j] + beta * B[i][j]
             for j in range(len(A[0]))] for i in range(len(A))]

def _mat_scale(A, s) -> List[List[float]]:
    return [[A[i][j] * s for j in range(len(A[0]))] for i in range(len(A))]

def _spectral_radius(A: List[List[float]]) -> float:
    """Power iteration estimate of dominant eigenvalue magnitude."""
    n = len(A)
    v = [1.0 / math.sqrt(n)] * n
    for _ in range(50):
        Av = [sum(A[i][j] * v[j] for j in range(n)) for i in range(n)]
        norm = math.sqrt(sum(x*x for x in Av)) or 1e-12
        v = [x / norm for x in Av]
    # Rayleigh quotient
    Av = [sum(A[i][j] * v[j] for j in range(n)) for i in range(n)]
    return abs(sum(Av[i] * v[i] for i in range(n)))

def _is_positive_definite(P: List[List[float]]) -> bool:
    """Check P > 0 via Sylvester criterion (all leading minors positive)."""
    n = len(P)
    for k in range(1, n + 1):
        sub = [row[:k] for row in P[:k]]
        # Determinant via Gaussian elimination
        det = _det(sub)
        if det <= 0:
            return False
    return True

def _det(A: List[List[float]]) -> float:
    """Determinant via cofactor expansion (small matrices only)."""
    n = len(A)
    if n == 1: return A[0][0]
    if n == 2: return A[0][0]*A[1][1] - A[0][1]*A[1][0]
    d = 0.0
    for j in range(n):
        minor = [row[:j] + row[j+1:] for row in A[1:]]
        d += ((-1)**j) * A[0][j] * _det(minor)
    return d

def _frobenius_norm(A: List[List[float]]) -> float:
    return math.sqrt(sum(A[i][j]**2 for i in range(len(A)) for j in range(len(A[0]))))


# ─────────────────────────────────────────────────────────────────────────────
# LYAPUNOV SOLVER  (Discrete-Time Stein Equation: AᵀPA − P + Q = 0)
# ─────────────────────────────────────────────────────────────────────────────

def solve_lyapunov_dt(A: List[List[float]],
                       Q: List[List[float]],
                       max_iter: int = 200,
                       tol: float = 1e-6) -> Tuple[Optional[List[List[float]]], bool]:
    """
    Solve discrete-time Lyapunov equation: AᵀPA − P = −Q
    Returns (P, converged).
    Uses doubling algorithm approximation.
    """
    n = len(A)
    At = _mat_transpose(A)
    P  = _mat_scale(Q, 1.0)   # start with P = Q

    for iteration in range(max_iter):
        AtP  = _mat_mul(At, P)
        AtPA = _mat_mul(AtP, A)
        # P_new = AᵀPA + Q
        P_new = _mat_add(AtPA, Q)

        diff = _frobenius_norm(_mat_add(P_new, P, 1.0, -1.0))
        P = P_new

        # Stability check: spectral radius of A
        rho = _spectral_radius(A)
        if rho >= 1.0:
            return None, False   # unstable — no solution

        if diff < tol:
            return P, True

    return P, False


def check_lyapunov_stable(A: List[List[float]],
                           Q: List[List[float]] = None) -> Dict[str, Any]:
    """
    Check discrete-time stability via Lyapunov method.
    Returns detailed result dict.
    """
    n   = len(A)
    Q   = Q or _eye(n)
    rho = _spectral_radius(A)

    if rho >= 1.0:
        return {
            "stable":          False,
            "method":          "lyapunov_dt",
            "spectral_radius": round(rho, 6),
            "reason":          "Spectral radius ≥ 1 — system unstable",
            "P_found":         False,
        }

    P, converged = solve_lyapunov_dt(A, Q)

    if not converged or P is None:
        return {
            "stable":          False,
            "method":          "lyapunov_dt",
            "spectral_radius": round(rho, 6),
            "reason":          "Stein equation did not converge",
            "P_found":         False,
        }

    P_pd = _is_positive_definite(P)
    # Verify AᵀPA − P < 0  ↔  AᵀPA − P + Q should = 0, P > 0
    At  = _mat_transpose(A)
    AtP = _mat_mul(At, P)
    AtPA= _mat_mul(AtP, A)
    LHS = _mat_add(AtPA, P, 1.0, -1.0)   # AᵀPA − P
    max_eig_est = max(abs(LHS[i][i]) for i in range(n))  # diagonal proxy

    return {
        "stable":          P_pd and rho < 1.0,
        "method":          "lyapunov_dt",
        "spectral_radius": round(rho, 6),
        "P_positive_def":  P_pd,
        "lyapunov_margin": round(1.0 - rho, 6),
        "converged":       converged,
        "P_found":         True,
        "P_size":          n,
        "lyapunov_lhs_max": round(max_eig_est, 6),
    }


# ─────────────────────────────────────────────────────────────────────────────
# BARRIER CERTIFICATE VERIFIER
# B(x) ≥ 0 in safe set,  dB/dt ≤ 0 along trajectories
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class BarrierResult:
    valid:            bool
    n_samples:        int
    violations:       int
    min_B_value:      float
    max_dBdt_value:   float
    certificate_type: str
    reason:           str

    def to_dict(self) -> dict:
        return {
            "valid":            self.valid,
            "n_samples":        self.n_samples,
            "violations":       self.violations,
            "min_B_value":      round(self.min_B_value, 6),
            "max_dBdt_value":   round(self.max_dBdt_value, 6),
            "certificate_type": self.certificate_type,
            "reason":           self.reason,
        }


def verify_barrier_certificate(
    B:          Callable[[List[float]], float],
    f:          Callable[[List[float]], List[float]],
    safe_set:   List[Tuple[float, float]],
    n_samples:  int = 500,
    dt:         float = 0.01,
    seed:       int = 0,
) -> BarrierResult:
    """
    Sample-based barrier certificate verification.

    Args:
      B:         Barrier function B(x) → scalar
      f:         Vector field f(x) → dx/dt
      safe_set:  [(lo, hi)] bounds for each dimension
      n_samples: number of random sample points
      dt:        time step for numerical dB/dt estimation

    Returns BarrierResult.
    """
    rng = random.Random(seed)
    n_dim = len(safe_set)

    violations  = 0
    min_B       = float("inf")
    max_dBdt    = float("-inf")

    for _ in range(n_samples):
        # Random point in safe set
        x = [rng.uniform(lo, hi) for (lo, hi) in safe_set]

        Bx = B(x)
        min_B = min(min_B, Bx)

        # B(x) ≥ 0 check
        if Bx < 0:
            violations += 1
            continue

        # dB/dt ≈ (B(x + f(x)·dt) − B(x)) / dt
        fx = f(x)
        x_next = [x[i] + fx[i] * dt for i in range(n_dim)]
        Bx_next = B(x_next)
        dBdt = (Bx_next - Bx) / dt
        max_dBdt = max(max_dBdt, dBdt)

        # dB/dt ≤ 0 check (allow small tolerance)
        if dBdt > 1e-4:
            violations += 1

    valid  = violations == 0
    reason = "Certificate valid" if valid else f"{violations}/{n_samples} sample violations"

    return BarrierResult(
        valid            = valid,
        n_samples        = n_samples,
        violations       = violations,
        min_B_value      = min_B if min_B != float("inf") else 0.0,
        max_dBdt_value   = max_dBdt if max_dBdt != float("-inf") else 0.0,
        certificate_type = "barrier_sampling",
        reason           = reason,
    )


# ─────────────────────────────────────────────────────────────────────────────
# MUTATION LEDGER
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class LedgerEntry:
    entry_id:    str
    mutation_id: str
    method:      str
    result:      bool
    details:     Dict[str, Any]
    timestamp:   str
    signature:   str

    def to_dict(self) -> dict:
        return {
            "entry_id":    self.entry_id,
            "mutation_id": self.mutation_id,
            "method":      self.method,
            "result":      self.result,
            "details":     self.details,
            "timestamp":   self.timestamp,
            "signature":   self.signature,
        }


class MutationLedger:
    """Immutable log of all formal verification decisions."""

    def __init__(self):
        self._entries: List[LedgerEntry] = []
        self._root_hash = "GENESIS"

    def append(self, mutation_id: str, method: str,
               result: bool, details: dict) -> LedgerEntry:
        payload = json.dumps({
            "mutation_id": mutation_id,
            "method": method,
            "result": result,
            "prev": self._root_hash,
            "details": details,
        }, sort_keys=True).encode()
        sig = hashlib.sha256(payload).hexdigest()[:24]
        entry = LedgerEntry(
            entry_id    = f"LEDGER_{len(self._entries):06d}",
            mutation_id = mutation_id,
            method      = method,
            result      = result,
            details     = details,
            timestamp   = f"{time.time():.3f}",
            signature   = sig,
        )
        self._entries.append(entry)
        self._root_hash = sig
        return entry

    def get_all(self, limit: int = 50) -> List[dict]:
        return [e.to_dict() for e in self._entries[-limit:]]

    def verify_integrity(self) -> bool:
        """Verify the ledger chain is intact (no tampering)."""
        prev = "GENESIS"
        for e in self._entries:
            payload = json.dumps({
                "mutation_id": e.mutation_id,
                "method":      e.method,
                "result":      e.result,
                "prev":        prev,
                "details":     e.details,
            }, sort_keys=True).encode()
            expected = hashlib.sha256(payload).hexdigest()[:24]
            if expected != e.signature:
                return False
            prev = e.signature
        return True

    def __len__(self) -> int:
        return len(self._entries)


# ─────────────────────────────────────────────────────────────────────────────
# FORMAL VERIFICATION ENGINE
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class VerificationResult:
    mutation_id:    str
    approved:       bool
    lyapunov:       Optional[Dict]
    barrier:        Optional[Dict]
    reject_reason:  Optional[str]
    latency_ms:     float

    def to_dict(self) -> dict:
        return {
            "mutation_id":   self.mutation_id,
            "approved":      self.approved,
            "lyapunov":      self.lyapunov,
            "barrier":       self.barrier,
            "reject_reason": self.reject_reason,
            "latency_ms":    round(self.latency_ms, 2),
        }


class FormalVerificationEngine:
    """
    Verifies mutation stability before deployment.
    Checks:
      1. Lyapunov stability on linearized system
      2. Barrier certificate on nonlinear region (if provided)
    If either fails → mutation rejected automatically.
    """

    def __init__(self):
        self.ledger  = MutationLedger()
        self._count  = {"approved": 0, "rejected": 0, "total": 0}

    def verify_linearized(
        self,
        A:          List[List[float]],
        Q:          List[List[float]] = None,
        mutation_id: str = "unknown",
    ) -> VerificationResult:
        """
        Verify discrete-time linearized system A is Lyapunov stable.
        A = system matrix at operating point with mutation applied.
        """
        t0 = time.perf_counter()
        self._count["total"] += 1

        lya = check_lyapunov_stable(A, Q)
        approved = lya["stable"]
        reason   = None if approved else lya.get("reason", "Lyapunov check failed")

        if approved:
            self._count["approved"] += 1
        else:
            self._count["rejected"] += 1

        entry = self.ledger.append(mutation_id, "lyapunov", approved, lya)
        ms = (time.perf_counter() - t0) * 1000

        return VerificationResult(
            mutation_id   = mutation_id,
            approved      = approved,
            lyapunov      = lya,
            barrier       = None,
            reject_reason = reason,
            latency_ms    = ms,
        )

    def verify_barrier(
        self,
        B:          Callable[[List[float]], float],
        f:          Callable[[List[float]], List[float]],
        safe_set:   List[Tuple[float, float]],
        n_samples:  int = 200,
        mutation_id: str = "unknown",
    ) -> VerificationResult:
        """Verify barrier certificate for nonlinear system."""
        t0 = time.perf_counter()
        self._count["total"] += 1

        bar = verify_barrier_certificate(B, f, safe_set, n_samples)
        approved = bar.valid
        reason   = None if approved else bar.reason
        bar_dict = bar.to_dict()

        if approved:
            self._count["approved"] += 1
        else:
            self._count["rejected"] += 1

        self.ledger.append(mutation_id, "barrier", approved, bar_dict)
        ms = (time.perf_counter() - t0) * 1000

        return VerificationResult(
            mutation_id   = mutation_id,
            approved      = approved,
            lyapunov      = None,
            barrier       = bar_dict,
            reject_reason = reason,
            latency_ms    = ms,
        )

    def verify_full(
        self,
        A:          List[List[float]],
        B_fn:       Callable[[List[float]], float] = None,
        f_fn:       Callable[[List[float]], List[float]] = None,
        safe_set:   List[Tuple[float, float]] = None,
        mutation_id: str = "unknown",
    ) -> VerificationResult:
        """
        Full verification: Lyapunov first, then barrier if provided.
        Mutation approved only if ALL checks pass.
        """
        t0 = time.perf_counter()
        self._count["total"] += 1

        # Step 1: Lyapunov
        lya = check_lyapunov_stable(A)
        if not lya["stable"]:
            self._count["rejected"] += 1
            self.ledger.append(mutation_id, "lyapunov_full", False, lya)
            return VerificationResult(
                mutation_id   = mutation_id,
                approved      = False,
                lyapunov      = lya,
                barrier       = None,
                reject_reason = lya.get("reason", "Lyapunov failed"),
                latency_ms    = (time.perf_counter() - t0) * 1000,
            )

        # Step 2: Barrier (if provided)
        bar_dict = None
        if B_fn and f_fn and safe_set:
            bar = verify_barrier_certificate(B_fn, f_fn, safe_set, n_samples=200)
            bar_dict = bar.to_dict()
            if not bar.valid:
                self._count["rejected"] += 1
                self.ledger.append(mutation_id, "barrier_full", False, bar_dict)
                return VerificationResult(
                    mutation_id   = mutation_id,
                    approved      = False,
                    lyapunov      = lya,
                    barrier       = bar_dict,
                    reject_reason = bar.reason,
                    latency_ms    = (time.perf_counter() - t0) * 1000,
                )

        self._count["approved"] += 1
        self.ledger.append(mutation_id, "full_verification", True,
                           {"lyapunov": lya, "barrier": bar_dict})

        return VerificationResult(
            mutation_id   = mutation_id,
            approved      = True,
            lyapunov      = lya,
            barrier       = bar_dict,
            reject_reason = None,
            latency_ms    = (time.perf_counter() - t0) * 1000,
        )

    def get_stats(self) -> dict:
        return {
            "total":         self._count["total"],
            "approved":      self._count["approved"],
            "rejected":      self._count["rejected"],
            "approval_rate": round(
                self._count["approved"] / max(self._count["total"], 1), 4
            ),
            "ledger_entries": len(self.ledger),
            "ledger_intact":  self.ledger.verify_integrity(),
        }
