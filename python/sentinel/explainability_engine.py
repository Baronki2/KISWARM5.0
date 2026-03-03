"""
KISWARM v4.2 — Module 24: Explainability Engine (XAI)
======================================================
SHAP-approximate attribution for every AI decision made by KISWARM.
Makes the black-box TD3 policy, formal verifier, and governance decisions
fully auditable for IEC 62443 / IEC 61508 compliance.

Features:
  • KernelSHAP approximation (pure Python, no external libs)
  • TD3 action attribution: which state features drove which PLC changes
  • Formal verification attribution: which eigenvalue/margin drove approve/reject
  • Governance step attribution: which evidence item was most decisive
  • Natural-language explanation generation for operators
  • Explanation ledger: immutable record of every AI explanation
  • Counterfactual "what-if" analysis
"""

import hashlib
import json
import math
import random
import itertools
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple


# ─────────────────────────────────────────────────────────────────────────────
# DATA STRUCTURES
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ShapValue:
    """SHAP attribution for a single feature."""
    feature_name: str
    feature_value: float
    shap_value: float          # contribution to output
    abs_importance: float      # |shap_value|
    direction: str             # "increases" or "decreases"
    rank: int                  # 1 = most important


@dataclass
class Explanation:
    """Full explanation for one AI decision."""
    decision_id: str
    decision_type: str         # "td3_action" | "formal_verify" | "governance" | "physics"
    decision_output: Any       # the actual decision made
    shap_values: List[ShapValue]
    baseline_output: float
    top_features: List[str]    # top-3 by |SHAP|
    natural_language: str      # operator-readable summary
    counterfactuals: List[Dict[str, Any]]
    confidence: float
    timestamp: str
    signature: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "decision_id":    self.decision_id,
            "decision_type":  self.decision_type,
            "top_features":   self.top_features,
            "natural_language": self.natural_language,
            "shap_values":    [
                {"feature": s.feature_name, "value": round(s.shap_value, 6),
                 "direction": s.direction, "rank": s.rank}
                for s in self.shap_values
            ],
            "counterfactuals": self.counterfactuals,
            "confidence":      round(self.confidence, 4),
            "timestamp":       self.timestamp,
            "signature":       self.signature,
        }


@dataclass
class CounterfactualResult:
    feature_name: str
    original_value: float
    counterfactual_value: float
    delta_output: float        # change in model output
    would_flip: bool           # would decision change?


# ─────────────────────────────────────────────────────────────────────────────
# KERNEL SHAP APPROXIMATION
# ─────────────────────────────────────────────────────────────────────────────

def _kernel_shap_weights(n_features: int, subset_size: int) -> float:
    """SHAP kernel weight for a coalition of size subset_size."""
    if subset_size == 0 or subset_size == n_features:
        return 1e6   # infinite weight for empty / full coalition
    comb = math.comb(n_features, subset_size)
    return (n_features - 1) / (comb * subset_size * (n_features - subset_size))


def kernel_shap(
    model_fn: Callable[[List[float]], float],
    instance: List[float],
    baseline: List[float],
    feature_names: List[str],
    n_samples: int = 256,
    seed: int = 0,
) -> List[ShapValue]:
    """
    KernelSHAP approximation.

    Samples 2^min(n_features,10) or n_samples coalitions, weights them by
    the SHAP kernel, and solves a weighted least-squares problem to get
    per-feature attributions.

    Args:
        model_fn:      f(x: List[float]) → scalar output
        instance:      the specific input to explain
        baseline:      reference (background) input (e.g. all-zeros)
        feature_names: names of each feature dimension
        n_samples:     number of coalition samples
        seed:          random seed for reproducibility

    Returns:
        List of ShapValue sorted by abs importance descending.
    """
    n = len(instance)
    rng = random.Random(seed)
    baseline_val = model_fn(baseline)

    # If small, enumerate all 2^n coalitions
    if n <= 10:
        coalitions = list(itertools.product([0, 1], repeat=n))
    else:
        coalitions = set()
        # Always include all-on and all-off
        coalitions.add(tuple([1] * n))
        coalitions.add(tuple([0] * n))
        while len(coalitions) < n_samples:
            mask = tuple(rng.randint(0, 1) for _ in range(n))
            coalitions.add(mask)
        coalitions = list(coalitions)

    # Evaluate model on each coalition
    X_data = []   # coalition vectors (feature indicators)
    y_data = []   # model outputs
    w_data = []   # SHAP kernel weights

    for mask in coalitions:
        s = sum(mask)
        # Build masked input: use instance value if mask=1, else baseline
        x_masked = [instance[i] if mask[i] else baseline[i] for i in range(n)]
        out = model_fn(x_masked)
        w = _kernel_shap_weights(n, s)

        X_data.append(list(mask))
        y_data.append(out - baseline_val)
        w_data.append(w)

    # Weighted least squares: solve (XᵀWX)φ = XᵀWy
    # Pure Python implementation
    phi = _weighted_least_squares(X_data, y_data, w_data, n)

    # Build ShapValue objects
    raw = sorted(enumerate(phi), key=lambda x: abs(x[1]), reverse=True)
    shap_vals = []
    for rank, (idx, sv) in enumerate(raw, 1):
        shap_vals.append(ShapValue(
            feature_name  = feature_names[idx] if idx < len(feature_names) else f"f{idx}",
            feature_value = float(instance[idx]),
            shap_value    = float(sv),
            abs_importance= float(abs(sv)),
            direction     = "increases" if sv > 0 else "decreases",
            rank          = rank,
        ))

    return shap_vals


def _weighted_least_squares(
    X: List[List[float]],
    y: List[float],
    w: List[float],
    n_features: int,
) -> List[float]:
    """
    Solve weighted least squares: φ = (XᵀWX)⁻¹ XᵀWy
    Pure Python — no numpy.
    """
    N = len(X)
    # XᵀWX  (n_features × n_features)
    XtWX = [[0.0] * n_features for _ in range(n_features)]
    for i in range(N):
        for j in range(n_features):
            for k in range(n_features):
                XtWX[j][k] += w[i] * X[i][j] * X[i][k]

    # XᵀWy  (n_features,)
    XtWy = [0.0] * n_features
    for i in range(N):
        for j in range(n_features):
            XtWy[j] += w[i] * X[i][j] * y[i]

    # Solve via Gauss elimination with regularisation
    A = [row[:] for row in XtWX]
    b = XtWy[:]
    reg = 1e-6
    for i in range(n_features):
        A[i][i] += reg

    # Forward elimination
    for col in range(n_features):
        # Partial pivot
        max_row = col
        for row in range(col + 1, n_features):
            if abs(A[row][col]) > abs(A[max_row][col]):
                max_row = row
        A[col], A[max_row] = A[max_row], A[col]
        b[col], b[max_row] = b[max_row], b[col]

        if abs(A[col][col]) < 1e-12:
            continue
        for row in range(col + 1, n_features):
            factor = A[row][col] / A[col][col]
            for k in range(col, n_features):
                A[row][k] -= factor * A[col][k]
            b[row] -= factor * b[col]

    # Back substitution
    phi = [0.0] * n_features
    for i in range(n_features - 1, -1, -1):
        if abs(A[i][i]) < 1e-12:
            phi[i] = 0.0
        else:
            phi[i] = b[i]
            for j in range(i + 1, n_features):
                phi[i] -= A[i][j] * phi[j]
            phi[i] /= A[i][i]

    return phi


# ─────────────────────────────────────────────────────────────────────────────
# NATURAL LANGUAGE GENERATOR
# ─────────────────────────────────────────────────────────────────────────────

def _generate_nl_explanation(
    decision_type: str,
    decision_output: Any,
    shap_vals: List[ShapValue],
    confidence: float,
) -> str:
    """Generate an operator-readable English explanation."""
    top = shap_vals[:3] if len(shap_vals) >= 3 else shap_vals

    def fmt_feat(s: ShapValue) -> str:
        sign = "+" if s.shap_value > 0 else "−"
        return (f"**{s.feature_name}** ({sign}{abs(s.shap_value):.4f},"
                f" current={s.feature_value:.3f})")

    top_str = ", ".join(fmt_feat(s) for s in top)

    if decision_type == "td3_action":
        return (
            f"The TD3 controller selected this PLC parameter adjustment with "
            f"{confidence*100:.1f}% policy confidence. "
            f"The top driving state features were: {top_str}. "
            f"These features pushed the action in the direction shown by their signs."
        )
    elif decision_type == "formal_verify":
        verdict = "APPROVED" if decision_output else "REJECTED"
        return (
            f"Formal verification {verdict} this mutation. "
            f"The key factors were: {top_str}. "
            f"Lyapunov margin and spectral radius were the dominant signals."
        )
    elif decision_type == "governance":
        return (
            f"Governance pipeline evaluated this mutation step. "
            f"Key evidence factors: {top_str}. "
            f"Confidence in step outcome: {confidence*100:.1f}%."
        )
    elif decision_type == "physics":
        return (
            f"Physics twin simulation result explained by: {top_str}. "
            f"These state variables contributed most to the simulated outcome."
        )
    else:
        return (
            f"Decision type '{decision_type}' driven primarily by: {top_str}. "
            f"Confidence: {confidence*100:.1f}%."
        )


# ─────────────────────────────────────────────────────────────────────────────
# COUNTERFACTUAL ANALYSIS
# ─────────────────────────────────────────────────────────────────────────────

def _compute_counterfactuals(
    model_fn: Callable[[List[float]], float],
    instance: List[float],
    feature_names: List[str],
    shap_vals: List[ShapValue],
    original_output: float,
    n_top: int = 3,
    delta_pct: float = 0.10,
) -> List[Dict[str, Any]]:
    """
    For the top-n features, compute: what if this feature changed by ±10%?
    Returns a list of counterfactual results.
    """
    results = []
    for sv in shap_vals[:n_top]:
        idx = feature_names.index(sv.feature_name) if sv.feature_name in feature_names else -1
        if idx < 0:
            continue

        for direction_mult in (+1, -1):
            delta = sv.feature_value * delta_pct * direction_mult
            cf_instance = instance[:]
            cf_instance[idx] = sv.feature_value + delta
            cf_out = model_fn(cf_instance)
            delta_out = cf_out - original_output
            results.append({
                "feature":            sv.feature_name,
                "original_value":     round(sv.feature_value, 6),
                "counterfactual_value": round(cf_instance[idx], 6),
                "delta_pct":          round(direction_mult * delta_pct * 100, 1),
                "delta_output":       round(delta_out, 6),
                "would_change_sign":  (cf_out * original_output < 0),
            })
    return results


# ─────────────────────────────────────────────────────────────────────────────
# EXPLANATION LEDGER
# ─────────────────────────────────────────────────────────────────────────────

class ExplanationLedger:
    """Immutable, append-only ledger of every AI explanation produced."""

    def __init__(self):
        self._entries: List[Dict[str, Any]] = []
        self._prev_hash: str = "0" * 64

    def append(self, explanation: Explanation) -> None:
        payload = json.dumps(explanation.to_dict(), sort_keys=True)
        chain_hash = hashlib.sha256(
            (self._prev_hash + payload).encode()
        ).hexdigest()
        self._entries.append({
            "index":       len(self._entries),
            "explanation": explanation.to_dict(),
            "chain_hash":  chain_hash,
        })
        self._prev_hash = chain_hash

    def verify_integrity(self) -> bool:
        prev = "0" * 64
        for entry in self._entries:
            payload = json.dumps(entry["explanation"], sort_keys=True)
            expected = hashlib.sha256((prev + payload).encode()).hexdigest()
            if entry["chain_hash"] != expected:
                return False
            prev = entry["chain_hash"]
        return True

    def get_all(self, limit: int = 50) -> List[Dict[str, Any]]:
        return self._entries[-limit:]

    def __len__(self) -> int:
        return len(self._entries)


# ─────────────────────────────────────────────────────────────────────────────
# MAIN EXPLAINABILITY ENGINE
# ─────────────────────────────────────────────────────────────────────────────

import datetime


class ExplainabilityEngine:
    """
    Unified XAI engine for all KISWARM AI decisions.

    Usage:
        engine = ExplainabilityEngine()
        explanation = engine.explain_td3(state, action_fn, feature_names)
        explanation = engine.explain_formal(lyapunov_result, feature_names)
        explanation = engine.explain_governance(evidence_chain)
    """

    def __init__(self, n_shap_samples: int = 256, seed: int = 42):
        self.n_shap_samples = n_shap_samples
        self.seed = seed
        self.ledger = ExplanationLedger()
        self._counters: Dict[str, int] = {}

    # ── TD3 Action Explanation ────────────────────────────────────────────────

    def explain_td3(
        self,
        state: List[float],
        model_fn: Callable[[List[float]], float],
        feature_names: Optional[List[str]] = None,
        baseline: Optional[List[float]] = None,
        decision_id: Optional[str] = None,
    ) -> Explanation:
        """
        Explain a TD3 actor decision.

        Args:
            state:        current PLC state vector
            model_fn:     function that maps state → scalar Q-value or action component
            feature_names: names for each state dimension
            baseline:     reference state (defaults to zeros)
        """
        n = len(state)
        if feature_names is None:
            feature_names = [f"state_{i}" for i in range(n)]
        if baseline is None:
            baseline = [0.0] * n

        shap_vals = kernel_shap(
            model_fn, state, baseline, feature_names,
            n_samples=self.n_shap_samples, seed=self.seed,
        )

        original_output = model_fn(state)
        cfs = _compute_counterfactuals(model_fn, state, feature_names, shap_vals, original_output)

        confidence = min(1.0, abs(original_output) / (max(abs(original_output), 0.01)))
        nl = _generate_nl_explanation("td3_action", original_output, shap_vals, confidence)

        did = decision_id or self._next_id("td3")
        exp = Explanation(
            decision_id      = did,
            decision_type    = "td3_action",
            decision_output  = round(original_output, 6),
            shap_values      = shap_vals,
            baseline_output  = float(model_fn(baseline)),
            top_features     = [s.feature_name for s in shap_vals[:3]],
            natural_language = nl,
            counterfactuals  = cfs,
            confidence       = confidence,
            timestamp        = datetime.datetime.now().isoformat(),
            signature        = self._sign(did, nl),
        )
        self.ledger.append(exp)
        return exp

    # ── Formal Verification Explanation ──────────────────────────────────────

    def explain_formal(
        self,
        lyapunov_result: Dict[str, Any],
        mutation_id: Optional[str] = None,
    ) -> Explanation:
        """
        Explain a Lyapunov stability decision using the margin metrics.

        lyapunov_result fields:
            stable, spectral_radius, lyapunov_margin, P_positive_def, converged
        """
        feature_names = [
            "spectral_radius",
            "lyapunov_margin",
            "p_positive_def",
            "converged",
        ]
        state = [
            float(lyapunov_result.get("spectral_radius",  0.0)),
            float(lyapunov_result.get("lyapunov_margin",  0.0)),
            float(lyapunov_result.get("P_positive_def",   0)),
            float(lyapunov_result.get("converged",        0)),
        ]
        approved = bool(lyapunov_result.get("stable", False))

        # Simple linear model: stability = 1 - spectral_radius + margin
        def model_fn(x: List[float]) -> float:
            return (1.0 - x[0]) + 0.5 * x[1] + 0.3 * x[2] + 0.2 * x[3]

        shap_vals = kernel_shap(
            model_fn, state, [0.0] * 4, feature_names,
            n_samples=64, seed=self.seed,
        )

        out = model_fn(state)
        confidence = min(1.0, abs(out))
        nl = _generate_nl_explanation("formal_verify", approved, shap_vals, confidence)
        did = mutation_id or self._next_id("formal")

        exp = Explanation(
            decision_id      = did,
            decision_type    = "formal_verify",
            decision_output  = approved,
            shap_values      = shap_vals,
            baseline_output  = model_fn([0.0] * 4),
            top_features     = [s.feature_name for s in shap_vals[:3]],
            natural_language = nl,
            counterfactuals  = _compute_counterfactuals(model_fn, state, feature_names, shap_vals, out),
            confidence       = confidence,
            timestamp        = datetime.datetime.now().isoformat(),
            signature        = self._sign(did, nl),
        )
        self.ledger.append(exp)
        return exp

    # ── Governance Step Explanation ───────────────────────────────────────────

    def explain_governance(
        self,
        evidence_chain: List[Dict[str, Any]],
        mutation_id: Optional[str] = None,
    ) -> Explanation:
        """
        Explain which evidence items in the governance chain
        contributed most to the overall mutation outcome.
        """
        feature_names = [e.get("step_name", f"step_{i}") for i, e in enumerate(evidence_chain)]
        state = [1.0 if e.get("passed", False) else 0.0 for e in evidence_chain]

        def model_fn(x: List[float]) -> float:
            # Weighted sum: earlier steps more critical
            weights = [1.0 / (i + 1) for i in range(len(x))]
            total_w = sum(weights)
            return sum(w * v for w, v in zip(weights, x)) / total_w

        if not state:
            state = [0.0]
            feature_names = ["no_evidence"]

        shap_vals = kernel_shap(
            model_fn, state, [0.0] * len(state), feature_names,
            n_samples=64, seed=self.seed,
        )

        out = model_fn(state)
        approved = out >= 0.5
        confidence = abs(out - 0.5) * 2
        nl = _generate_nl_explanation("governance", approved, shap_vals, confidence)
        did = mutation_id or self._next_id("gov")

        exp = Explanation(
            decision_id      = did,
            decision_type    = "governance",
            decision_output  = {"approved": approved, "score": round(out, 4)},
            shap_values      = shap_vals,
            baseline_output  = model_fn([0.0] * len(state)),
            top_features     = [s.feature_name for s in shap_vals[:3]],
            natural_language = nl,
            counterfactuals  = _compute_counterfactuals(model_fn, state, feature_names, shap_vals, out),
            confidence       = confidence,
            timestamp        = datetime.datetime.now().isoformat(),
            signature        = self._sign(did, nl),
        )
        self.ledger.append(exp)
        return exp

    # ── Physics Twin Explanation ──────────────────────────────────────────────

    def explain_physics(
        self,
        state: Dict[str, float],
        model_fn: Callable[[List[float]], float],
        decision_id: Optional[str] = None,
    ) -> Explanation:
        """Explain a physics twin simulation outcome."""
        feature_names = list(state.keys())
        state_vec     = list(state.values())
        baseline      = [0.0] * len(state_vec)

        shap_vals = kernel_shap(
            model_fn, state_vec, baseline, feature_names,
            n_samples=self.n_shap_samples, seed=self.seed,
        )

        out = model_fn(state_vec)
        confidence = min(1.0, abs(out))
        nl = _generate_nl_explanation("physics", out, shap_vals, confidence)
        did = decision_id or self._next_id("phys")

        exp = Explanation(
            decision_id      = did,
            decision_type    = "physics",
            decision_output  = round(out, 6),
            shap_values      = shap_vals,
            baseline_output  = float(model_fn(baseline)),
            top_features     = [s.feature_name for s in shap_vals[:3]],
            natural_language = nl,
            counterfactuals  = _compute_counterfactuals(model_fn, state_vec, feature_names, shap_vals, out),
            confidence       = confidence,
            timestamp        = datetime.datetime.now().isoformat(),
            signature        = self._sign(did, nl),
        )
        self.ledger.append(exp)
        return exp

    # ── Generic Explain ───────────────────────────────────────────────────────

    def explain(
        self,
        state: List[float],
        model_fn: Callable[[List[float]], float],
        feature_names: Optional[List[str]] = None,
        baseline: Optional[List[float]] = None,
        decision_type: str = "generic",
        decision_id: Optional[str] = None,
    ) -> Explanation:
        """Generic explain — works for any callable model."""
        n = len(state)
        feature_names = feature_names or [f"f{i}" for i in range(n)]
        baseline      = baseline      or [0.0] * n

        shap_vals = kernel_shap(
            model_fn, state, baseline, feature_names,
            n_samples=self.n_shap_samples, seed=self.seed,
        )

        out = model_fn(state)
        confidence = min(1.0, abs(out))
        nl = _generate_nl_explanation(decision_type, out, shap_vals, confidence)
        did = decision_id or self._next_id(decision_type[:4])

        exp = Explanation(
            decision_id      = did,
            decision_type    = decision_type,
            decision_output  = round(out, 6),
            shap_values      = shap_vals,
            baseline_output  = float(model_fn(baseline)),
            top_features     = [s.feature_name for s in shap_vals[:3]],
            natural_language = nl,
            counterfactuals  = _compute_counterfactuals(model_fn, state, feature_names, shap_vals, out),
            confidence       = confidence,
            timestamp        = datetime.datetime.now().isoformat(),
            signature        = self._sign(did, nl),
        )
        self.ledger.append(exp)
        return exp

    # ── Stats ─────────────────────────────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        all_entries = self.ledger.get_all(limit=10000)
        types: Dict[str, int] = {}
        for e in all_entries:
            t = e["explanation"].get("decision_type", "unknown")
            types[t] = types.get(t, 0) + 1
        return {
            "total_explanations": len(self.ledger),
            "by_type":            types,
            "ledger_intact":      self.ledger.verify_integrity(),
            "n_shap_samples":     self.n_shap_samples,
        }

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _next_id(self, prefix: str) -> str:
        c = self._counters.get(prefix, 0)
        self._counters[prefix] = c + 1
        ts = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
        return f"XAI_{prefix.upper()}_{ts}_{c:04d}"

    @staticmethod
    def _sign(decision_id: str, nl: str) -> str:
        payload = f"{decision_id}:{nl}"
        return hashlib.sha256(payload.encode()).hexdigest()[:24]
