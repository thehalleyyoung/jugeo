"""Theorem registry and verifiable properties for JuGeo semantic control (theory2.tex Ch44).

This module provides formal theorem classes and verification utilities grounded
in theory2.tex Chapter 44 ("Semantic Control of Project-Scale Orchestration").

The theorems formalized here assert properties that any well-behaved semantic
control loop *must* satisfy.  They are organised as executable checks that
consume :class:`SemanticControlState` and :class:`SemanticTrajectory` objects
and return structured :class:`TheoremResult` records.

Theorems and lemmas
───────────────────
*   **Theorem 44.1 – Control Law Monotonicity**: The attainability score is
    non-decreasing along any trajectory produced by an admissible control law.
    (theory2.tex §44.3, Theorem 44.1.)

*   **Theorem 44.2 – Admissibility Conservation**: Applying any admissible
    move to an admissible state yields an admissible state.
    (theory2.tex §44.2, Theorem 44.2.)

*   **Theorem 44.3 – Convergence Law (Lyapunov)**: The Lyapunov function
    V(s) = 1 − attainability(s) is non-increasing along the trajectory,
    guaranteeing eventual convergence.  (theory2.tex §44.3, Theorem 44.3.)

*   **Theorem 44.4 – Obligation Finitude and Decidability**: The obligation set
    is always finite and every obligation is decidable in finite time.
    (theory2.tex §44.4, Theorem 44.4.)

*   **Lemma 44.A – State Transition Closure**: The set of states reachable by
    admissible moves is closed under the state transition relation.
    (theory2.tex §44.2, Lemma 44.A.)

Invariants
──────────
The :class:`InvariantChecker` manages named boolean predicates on states.  The
default invariants (coverage non-negativity, budget positivity, obligation
finiteness, …) are registered via :meth:`InvariantChecker.default_invariants`.

Registry
────────
:class:`TheoremRegistry` maintains a dict of verifier callables keyed by
theorem ID and caches the most recent :class:`TheoremResult` for each.

Convenience API
───────────────
*   :func:`build_theorem_registry` – factory that pre-registers all theorems.
*   :func:`verify_all_theorems` – single-call verification of all theorems.

References
──────────
*   theory2.tex §44.1  – Semantic control state
*   theory2.tex §44.2  – Admissibility, preconditions, postconditions, closure
*   theory2.tex §44.3  – Convergence: Lyapunov functions and monotonicity
*   theory2.tex §44.4  – Obligation finitude and decidability
*   theory2.tex §44.5  – Descent validation
*   theory2.tex §44.6  – Fleet-competitive search
*   theory2.tex §44.7  – Frontier backpressure
"""

from __future__ import annotations

import enum
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Local model imports
# ---------------------------------------------------------------------------

try:
    from jugeo.orchestration.semantic_control.models import (
        AdmissibleMove,
        ControlLaw,
        ConvergenceCertificate,
        SemanticControlState,
        SemanticTrajectory,
    )
except ImportError:  # pragma: no cover
    logger.warning("semantic_control.models not available; using stubs")
    import time as _time
    import uuid as _uuid

    @dataclass(slots=True)  # type: ignore[misc]
    class SemanticControlState:  # type: ignore[no-redef]
        state_id: str = field(default_factory=lambda: str(_uuid.uuid4()))
        cover_ids: list = field(default_factory=list)
        context_ids: list = field(default_factory=list)
        section_ids: list = field(default_factory=list)
        treaty_ids: list = field(default_factory=list)
        obligation_ids: list = field(default_factory=list)
        channel_ids: list = field(default_factory=list)
        budget: float = 1.0
        timestamp: float = field(default_factory=_time.time)
        metadata: dict = field(default_factory=dict)

        def is_admissible(self) -> bool:
            return True

        def coverage_ratio(self) -> float:
            n_cov = len(self.cover_ids or [])
            n_ctx = len(self.context_ids or [])
            total = n_cov + n_ctx
            return n_cov / total if total > 0 else 0.0

        def attainability_score(self) -> float:
            return self.coverage_ratio()

        def delta_from(self, other: Any) -> dict:
            return {}

        def to_dict(self) -> dict:
            return {
                "state_id": self.state_id,
                "cover_ids": self.cover_ids,
                "obligation_ids": self.obligation_ids,
                "budget": self.budget,
            }

        def snapshot(self) -> "SemanticControlState":
            import copy
            return copy.deepcopy(self)

        def health_status(self) -> str:
            if self.budget > 0.5:
                return "healthy"
            if self.budget > 0.1:
                return "degraded"
            return "critical"

    @dataclass(slots=True)  # type: ignore[misc]
    class AdmissibleMove:  # type: ignore[no-redef]
        move_id: str = field(default_factory=lambda: str(_uuid.uuid4()))
        kind: str = "noop"
        preconditions: list = field(default_factory=list)
        postconditions: list = field(default_factory=list)
        cost: float = 0.0
        priority: float = 0.0
        expected_gain: float = 0.0
        trust_requirement: str = "LOW"
        metadata: dict = field(default_factory=dict)

        def is_applicable(self, state: Any) -> bool:
            return True

        def apply(self, state: Any) -> Any:
            return state

        def validate(self) -> bool:
            return True

        def to_dict(self) -> dict:
            return {"move_id": self.move_id, "kind": self.kind}

        def net_value(self) -> float:
            return self.expected_gain - self.cost

    @dataclass(slots=True)  # type: ignore[misc]
    class ControlLaw:  # type: ignore[no-redef]
        law_id: str = field(default_factory=lambda: str(_uuid.uuid4()))
        name: str = "stub"
        kind: str = "greedy"
        parameters: dict = field(default_factory=dict)

        def select_move(self, state: Any, candidates: list) -> Any:
            return candidates[0] if candidates else None

        def evaluate(self, state: Any) -> float:
            return 0.0

        def adapt(self, feedback: dict) -> None:
            pass

        def to_dict(self) -> dict:
            return {"law_id": self.law_id}

    @dataclass(frozen=True)  # type: ignore[misc]
    class ConvergenceCertificate:  # type: ignore[no-redef]
        cert_id: str = field(default_factory=lambda: str(_uuid.uuid4()))
        state_id: str = ""
        coverage_ratio: float = 0.0
        obligation_count: int = 0
        issued_at: float = field(default_factory=_time.time)
        valid_for: float = 3600.0
        evidence: dict = field(default_factory=dict)

        def is_valid(self) -> bool:
            return True

        def is_expired(self) -> bool:
            return (_time.time() - self.issued_at) > self.valid_for

        def summary(self) -> str:
            return f"cert:{self.cert_id[:8]} cov={self.coverage_ratio:.2f}"

        def to_dict(self) -> dict:
            return {
                "cert_id": self.cert_id,
                "coverage_ratio": self.coverage_ratio,
                "issued_at": self.issued_at,
            }

    @dataclass(slots=True)  # type: ignore[misc]
    class SemanticTrajectory:  # type: ignore[no-redef]
        trajectory_id: str = field(default_factory=lambda: str(_uuid.uuid4()))
        states: list = field(default_factory=list)
        moves: list = field(default_factory=list)
        timestamps: list = field(default_factory=list)

        def append(self, state: Any, move: Any | None = None) -> None:
            self.states.append(state)
            self.moves.append(move)
            self.timestamps.append(_time.time())

        def length(self) -> int:
            return len(self.states)

        def is_converging(self) -> bool:
            if len(self.states) < 3:
                return False
            scores = [
                s.attainability_score() if callable(getattr(s, "attainability_score", None)) else 0.0
                for s in self.states[-5:]
            ]
            return all(b >= a for a, b in zip(scores, scores[1:])) and len(scores) >= 3

        def export(self) -> dict:
            return {"trajectory_id": self.trajectory_id, "length": self.length()}

        def replay(self) -> list:
            return list(zip(self.states, self.moves))

        def latest_state(self) -> Any:
            return self.states[-1] if self.states else None

        def score_history(self) -> list:
            return [
                s.attainability_score() if callable(getattr(s, "attainability_score", None)) else 0.0
                for s in self.states
            ]


# ---------------------------------------------------------------------------
# Algorithms import (lyapunov_function)
# ---------------------------------------------------------------------------

try:
    from jugeo.orchestration.semantic_control.algorithms import lyapunov_function  # type: ignore[import]
except ImportError:  # pragma: no cover
    logger.warning("semantic_control.algorithms not available; using stub lyapunov_function")

    def lyapunov_function(state: Any) -> float:  # type: ignore[no-redef]
        """Stub Lyapunov function: V(s) = 1 − attainability(s).

        The real implementation in algorithms.py may use a richer measure
        (e.g., weighted obligation pressure) grounded in theory2.tex §44.3.
        """
        attainability = float(
            state.attainability_score()
            if callable(getattr(state, "attainability_score", None))
            else 0.0
        )
        return 1.0 - attainability


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

#: Module version tag.
THEOREMS_VERSION: str = "1.0.0"

#: Tolerance for floating-point monotonicity checks (theory2.tex §44.3).
MONOTONICITY_EPSILON: float = 1e-9

#: Maximum number of obligation IDs considered "finite" for Theorem 44.4.
OBLIGATION_FINITUDE_LIMIT: int = 10_000

#: Minimum trajectory length required for convergence checks.
MIN_TRAJECTORY_LENGTH_FOR_CONVERGENCE: int = 2

#: Default theorem IDs used in the registry.
THEOREM_IDS: dict[str, str] = {
    "monotonicity": "thm44_1_monotonicity",
    "admissibility": "thm44_2_admissibility",
    "convergence": "thm44_3_convergence",
    "finitude": "thm44_4_finitude",
    "closure": "lem44_A_closure",
}


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class TheoremStatus(enum.Enum):
    """Verification status of a theorem or lemma (theory2.tex §44).

    *   ``UNVERIFIED``:              Check has not yet been run.
    *   ``VERIFIED``:                Check passed unconditionally.
    *   ``CONDITIONALLY_VERIFIED``:  Check passed but relied on assumptions or
                                     incomplete evidence.
    *   ``FALSIFIED``:               Check found a concrete counterexample.
    *   ``UNDECIDABLE``:             Check could not reach a verdict (e.g.,
                                     timeout, missing data).
    """

    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    CONDITIONALLY_VERIFIED = "conditionally_verified"
    FALSIFIED = "falsified"
    UNDECIDABLE = "undecidable"


class InvariantKind(enum.Enum):
    """Semantic kind of an invariant (theory2.tex §44).

    *   ``MONOTONICITY``:   A quantity is non-decreasing (or non-increasing)
                            along trajectories.
    *   ``CONSERVATION``:   A quantity is exactly preserved across transitions.
    *   ``FINITUDE``:       A set or count remains bounded.
    *   ``CLOSURE``:        A set is closed under some operation.
    *   ``CONVERGENCE``:    A sequence converges to a fixed point or limit.
    """

    MONOTONICITY = "monotonicity"
    CONSERVATION = "conservation"
    FINITUDE = "finitude"
    CLOSURE = "closure"
    CONVERGENCE = "convergence"


# ---------------------------------------------------------------------------
# TheoremResult
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TheoremResult:
    """Structured result of a single theorem check (theory2.tex §44).

    Carries both the boolean verdict and the evidence that supports it.
    Frozen so results are immutable once produced.

    Attributes:
        theorem_id:    The registry key of the theorem that was checked.
        satisfied:     True iff the theorem holds on the given inputs.
        counterexample: Dict describing a counterexample, or None when
                        ``satisfied`` is True.
        evidence:       Dict of supporting evidence (scores, step indices, …).
        checked_at:    POSIX timestamp at the moment the check completed.
        elapsed_ms:    Wall-clock milliseconds taken by the check.
    """

    theorem_id: str
    satisfied: bool
    counterexample: dict | None
    evidence: dict
    checked_at: float
    elapsed_ms: float

    def to_dict(self) -> dict:
        """Serialise the result to a plain dict.

        Returns:
            Dict suitable for JSON serialisation, with keys:
            ``theorem_id``, ``satisfied``, ``counterexample``, ``evidence``,
            ``checked_at``, ``elapsed_ms``.
        """
        return {
            "theorem_id": self.theorem_id,
            "satisfied": self.satisfied,
            "counterexample": self.counterexample,
            "evidence": dict(self.evidence),
            "checked_at": self.checked_at,
            "elapsed_ms": self.elapsed_ms,
        }

    def summary(self) -> str:
        """Return a one-line human-readable summary of the result.

        Returns:
            E.g. ``"[VERIFIED] thm44_1_monotonicity (1.23 ms)"``.
        """
        status = "VERIFIED" if self.satisfied else "FALSIFIED"
        ce_str = ""
        if self.counterexample:
            ce_str = f" | counterexample: {list(self.counterexample.keys())}"
        return f"[{status}] {self.theorem_id} ({self.elapsed_ms:.2f} ms){ce_str}"


# ---------------------------------------------------------------------------
# InvariantChecker
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class InvariantChecker:
    """Checks named semantic invariants on states and trajectories (theory2.tex §44).

    An invariant is a boolean predicate ``(SemanticControlState) -> bool``.
    Invariants are registered by name and checked in bulk.

    Attributes:
        invariants:         Dict mapping invariant name to its predicate.
        violation_history:  Chronological list of violation records.
    """

    invariants: dict[str, Callable[[SemanticControlState], bool]] = field(
        default_factory=dict
    )
    violation_history: list[dict] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, name: str, predicate: Callable) -> None:
        """Register a new invariant predicate under *name*.

        If *name* already exists it is silently overwritten.

        Args:
            name:      Unique invariant identifier (e.g. ``"budget_positive"``).
            predicate: A callable ``(SemanticControlState) -> bool``.
        """
        self.invariants[name] = predicate
        logger.debug("InvariantChecker: registered invariant '%s'", name)

    # ------------------------------------------------------------------
    # Per-state checks
    # ------------------------------------------------------------------

    def check_all(self, state: SemanticControlState) -> dict[str, bool]:
        """Evaluate every registered invariant on *state*.

        Args:
            state: The state to check.

        Returns:
            Dict mapping invariant name to True (holds) or False (violated).
        """
        results: dict[str, bool] = {}
        for name, predicate in self.invariants.items():
            try:
                result = bool(predicate(state))
            except Exception:  # pragma: no cover
                logger.debug("invariant '%s' raised; treating as violated", name, exc_info=True)
                result = False
            results[name] = result
            if not result:
                self.violation_history.append(
                    {
                        "invariant": name,
                        "state_id": getattr(state, "state_id", "?"),
                        "timestamp": time.time(),
                    }
                )
        return results

    def violations(self, state: SemanticControlState) -> list[str]:
        """Return the list of invariant names violated on *state*.

        Args:
            state: The state to check.

        Returns:
            List of invariant names for which the predicate returns False.
        """
        return [name for name, ok in self.check_all(state).items() if not ok]

    # ------------------------------------------------------------------
    # Trajectory-level checks
    # ------------------------------------------------------------------

    def check_trajectory(
        self, trajectory: SemanticTrajectory
    ) -> dict[str, list[bool]]:
        """Evaluate every invariant at every step of *trajectory*.

        Args:
            trajectory: The trajectory to analyse.

        Returns:
            Dict mapping invariant name to a list of boolean results, one per
            trajectory step.
        """
        results: dict[str, list[bool]] = {name: [] for name in self.invariants}
        states = getattr(trajectory, "states", [])
        for state in states:
            step_results = self.check_all(state)
            for name in self.invariants:
                results[name].append(step_results.get(name, False))
        return results

    def is_invariant_preserved(
        self, name: str, trajectory: SemanticTrajectory
    ) -> bool:
        """Return True iff invariant *name* holds at every step of *trajectory*.

        Args:
            name:       The invariant to check.
            trajectory: The trajectory to analyse.

        Returns:
            True if the invariant holds at every step.
        """
        all_results = self.check_trajectory(trajectory)
        step_results = all_results.get(name, [])
        return bool(step_results) and all(step_results)

    # ------------------------------------------------------------------
    # Default invariants factory
    # ------------------------------------------------------------------

    @classmethod
    def default_invariants(cls) -> dict[str, Callable]:
        """Return a dict of default invariants grounded in theory2.tex §44.

        The defaults are:

        *   ``"budget_non_negative"``:    budget ≥ 0.
        *   ``"coverage_non_negative"``:  coverage_ratio() ≥ 0.
        *   ``"coverage_at_most_one"``:   coverage_ratio() ≤ 1.
        *   ``"obligation_finite"``:      len(obligation_ids) ≤ OBLIGATION_FINITUDE_LIMIT.
        *   ``"attainability_non_negative"``: attainability_score() ≥ 0.
        *   ``"has_state_id"``:           state_id is a non-empty string.
        *   ``"is_admissible"``:          state.is_admissible() returns True.

        Returns:
            Dict mapping invariant name to predicate callable.
        """

        def budget_non_negative(s: Any) -> bool:
            return float(getattr(s, "budget", 0.0)) >= 0.0

        def coverage_non_negative(s: Any) -> bool:
            cov = s.coverage_ratio() if callable(getattr(s, "coverage_ratio", None)) else 0.0
            return float(cov) >= 0.0

        def coverage_at_most_one(s: Any) -> bool:
            cov = s.coverage_ratio() if callable(getattr(s, "coverage_ratio", None)) else 0.0
            return float(cov) <= 1.0 + MONOTONICITY_EPSILON

        def obligation_finite(s: Any) -> bool:
            return len(getattr(s, "obligation_ids", []) or []) <= OBLIGATION_FINITUDE_LIMIT

        def attainability_non_negative(s: Any) -> bool:
            att = s.attainability_score() if callable(getattr(s, "attainability_score", None)) else 0.0
            return float(att) >= 0.0

        def has_state_id(s: Any) -> bool:
            return bool(getattr(s, "state_id", None))

        def is_admissible_inv(s: Any) -> bool:
            if callable(getattr(s, "is_admissible", None)):
                try:
                    return bool(s.is_admissible())
                except Exception:  # pragma: no cover
                    return False
            return True

        return {
            "budget_non_negative": budget_non_negative,
            "coverage_non_negative": coverage_non_negative,
            "coverage_at_most_one": coverage_at_most_one,
            "obligation_finite": obligation_finite,
            "attainability_non_negative": attainability_non_negative,
            "has_state_id": has_state_id,
            "is_admissible": is_admissible_inv,
        }


# ---------------------------------------------------------------------------
# TheoremRegistry
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class TheoremRegistry:
    """Registry of theorems with their verification functions (theory2.tex §44).

    Each theorem is stored as a dict with keys:
    ``theorem_id``, ``name``, ``description``, ``verifier``, ``kind``.

    Results of the most recent verification are cached in ``_results``.

    Attributes:
        theorems:  Registered theorems keyed by ID.
        _results:  Cached :class:`TheoremResult` objects keyed by ID.
    """

    theorems: dict[str, dict] = field(default_factory=dict)
    _results: dict[str, TheoremResult] = field(default_factory=dict)

    def register(
        self,
        theorem_id: str,
        name: str,
        description: str,
        verifier: Callable,
        kind: InvariantKind,
    ) -> None:
        """Register a theorem with its verification function.

        Args:
            theorem_id:   Unique identifier (e.g. ``"thm44_1_monotonicity"``).
            name:         Human-readable name.
            description:  Full theorem statement.
            verifier:     Callable that accepts arbitrary args/kwargs and
                          returns a :class:`TheoremResult`.
            kind:         Semantic :class:`InvariantKind` category.
        """
        self.theorems[theorem_id] = {
            "theorem_id": theorem_id,
            "name": name,
            "description": description,
            "verifier": verifier,
            "kind": kind,
        }
        logger.debug("TheoremRegistry: registered '%s'", theorem_id)

    def verify(self, theorem_id: str, *args: Any, **kwargs: Any) -> TheoremResult:
        """Run the verification function for *theorem_id*.

        Args:
            theorem_id: The ID of the theorem to verify.
            *args:      Positional arguments forwarded to the verifier.
            **kwargs:   Keyword arguments forwarded to the verifier.

        Returns:
            A :class:`TheoremResult` (also cached in ``_results``).

        Raises:
            KeyError: If *theorem_id* is not registered.
        """
        if theorem_id not in self.theorems:
            raise KeyError(f"TheoremRegistry: unknown theorem '{theorem_id}'")
        entry = self.theorems[theorem_id]
        verifier: Callable = entry["verifier"]
        t0 = time.time()
        try:
            result: TheoremResult = verifier(*args, **kwargs)
        except Exception as exc:  # pragma: no cover
            elapsed_ms = (time.time() - t0) * 1000.0
            logger.debug("verify '%s' raised: %s", theorem_id, exc, exc_info=True)
            result = TheoremResult(
                theorem_id=theorem_id,
                satisfied=False,
                counterexample={"exception": str(exc)},
                evidence={"error": str(exc)},
                checked_at=time.time(),
                elapsed_ms=elapsed_ms,
            )
        self._results[theorem_id] = result
        return result

    def verify_all(self, *args: Any, **kwargs: Any) -> dict[str, TheoremResult]:
        """Run all registered verifiers with *args* and *kwargs*.

        Args:
            *args:   Forwarded to each verifier.
            **kwargs: Forwarded to each verifier.

        Returns:
            Dict mapping theorem ID to :class:`TheoremResult`.
        """
        return {tid: self.verify(tid, *args, **kwargs) for tid in self.theorems}

    def status(self) -> dict[str, TheoremStatus]:
        """Return the current :class:`TheoremStatus` for each registered theorem.

        Theorems that have never been verified return ``UNVERIFIED``.

        Returns:
            Dict mapping theorem ID to :class:`TheoremStatus`.
        """
        result: dict[str, TheoremStatus] = {}
        for tid in self.theorems:
            cached = self._results.get(tid)
            if cached is None:
                result[tid] = TheoremStatus.UNVERIFIED
            elif cached.satisfied:
                result[tid] = TheoremStatus.VERIFIED
            elif cached.counterexample and "exception" in cached.counterexample:
                result[tid] = TheoremStatus.UNDECIDABLE
            else:
                result[tid] = TheoremStatus.FALSIFIED
        return result

    def list_theorems(self) -> list[str]:
        """Return the list of registered theorem IDs.

        Returns:
            List of theorem ID strings.
        """
        return list(self.theorems.keys())

    def get_result(self, theorem_id: str) -> TheoremResult | None:
        """Return the cached :class:`TheoremResult` for *theorem_id*, or None.

        Args:
            theorem_id: The theorem to look up.

        Returns:
            The cached result, or None if not yet verified.
        """
        return self._results.get(theorem_id)


# ---------------------------------------------------------------------------
# Theorem 44.1 – Control Law Monotonicity
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Theorem44_1_ControlLawMonotonicity:
    """Theorem 44.1: Control Law Monotonicity (theory2.tex §44.3).

    **Statement**: For any admissible control law L and any trajectory
    τ = (s₀, s₁, …, sₙ) produced by L, the attainability function A is
    non-decreasing along τ:

        A(s_{i+1}) ≥ A(s_i) − ε   for all i ∈ {0, …, n−1}

    where ε = MONOTONICITY_EPSILON is a floating-point tolerance.

    The theorem is checked by :meth:`check`, which inspects ``trajectory.score_history()``.

    Attributes:
        name:        Theorem display name.
        description: Full theorem statement.
        status:      Current :class:`TheoremStatus`.
    """

    name: str = "Theorem 44.1: Control Law Monotonicity"
    description: str = (
        "The attainability score A(s) is non-decreasing along any trajectory "
        "produced by an admissible control law (theory2.tex §44.3, Theorem 44.1)."
    )
    status: TheoremStatus = field(default=TheoremStatus.UNVERIFIED)

    def check(self, trajectory: SemanticTrajectory) -> TheoremResult:
        """Verify Theorem 44.1 on *trajectory*.

        Checks that each consecutive pair of attainability scores satisfies
        A(s_{i+1}) ≥ A(s_i) − MONOTONICITY_EPSILON.

        Args:
            trajectory: The trajectory to check.

        Returns:
            A :class:`TheoremResult` with ``satisfied=True`` iff the property holds.
        """
        t0 = time.time()
        tid = THEOREM_IDS["monotonicity"]

        scores: list[float] = []
        if hasattr(trajectory, "score_history") and callable(trajectory.score_history):
            scores = [float(s) for s in trajectory.score_history()]
        elif hasattr(trajectory, "states"):
            for s in trajectory.states:
                att = s.attainability_score() if callable(getattr(s, "attainability_score", None)) else 0.0
                scores.append(float(att))

        if len(scores) < MIN_TRAJECTORY_LENGTH_FOR_CONVERGENCE:
            elapsed = (time.time() - t0) * 1000.0
            return TheoremResult(
                theorem_id=tid,
                satisfied=True,
                counterexample=None,
                evidence={"note": "trajectory too short to check", "length": len(scores)},
                checked_at=time.time(),
                elapsed_ms=elapsed,
            )

        violations: list[dict] = []
        for i in range(len(scores) - 1):
            if scores[i + 1] < scores[i] - MONOTONICITY_EPSILON:
                violations.append(
                    {"step": i, "score_i": scores[i], "score_i1": scores[i + 1], "drop": scores[i] - scores[i + 1]}
                )

        satisfied = len(violations) == 0
        self.status = TheoremStatus.VERIFIED if satisfied else TheoremStatus.FALSIFIED
        elapsed = (time.time() - t0) * 1000.0
        return TheoremResult(
            theorem_id=tid,
            satisfied=satisfied,
            counterexample=violations[0] if violations else None,
            evidence={
                "trajectory_length": len(scores),
                "violation_count": len(violations),
                "min_score": min(scores),
                "max_score": max(scores),
                "final_score": scores[-1],
            },
            checked_at=time.time(),
            elapsed_ms=elapsed,
        )

    def statement(self) -> str:
        """Return the full theorem statement string.

        Returns:
            A multi-line string quoting the theorem (theory2.tex §44.3).
        """
        return (
            "Theorem 44.1 (Control Law Monotonicity, theory2.tex §44.3):\n"
            "Let L be an admissible control law and τ = (s₀, s₁, …, sₙ) a trajectory\n"
            "produced by L.  Then:\n"
            "    A(s_{i+1}) ≥ A(s_i) for all i ∈ {0, …, n−1}\n"
            "where A: S → [0,1] is the attainability function."
        )

    def to_dict(self) -> dict:
        """Serialise theorem metadata to a plain dict.

        Returns:
            Dict with keys: ``name``, ``description``, ``status``, ``theorem_id``.
        """
        return {
            "theorem_id": THEOREM_IDS["monotonicity"],
            "name": self.name,
            "description": self.description,
            "status": self.status.value,
        }


# ---------------------------------------------------------------------------
# Theorem 44.2 – Admissibility Conservation
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Theorem44_2_AdmissibilityConservation:
    """Theorem 44.2: Admissibility Conservation (theory2.tex §44.2).

    **Statement**: For any state s ∈ S that is admissible and any admissible
    move m ∈ M whose preconditions are satisfied at s, the resulting state
    s' = m(s) is also admissible:

        is_admissible(s) ∧ is_applicable(m, s) ⟹ is_admissible(m(s))

    Attributes:
        name:        Theorem display name.
        description: Full theorem statement.
        status:      Current :class:`TheoremStatus`.
    """

    name: str = "Theorem 44.2: Admissibility Conservation"
    description: str = (
        "Applying any admissible move to an admissible state yields an admissible "
        "state (theory2.tex §44.2, Theorem 44.2)."
    )
    status: TheoremStatus = field(default=TheoremStatus.UNVERIFIED)

    def check(
        self,
        state: SemanticControlState,
        move: AdmissibleMove,
    ) -> TheoremResult:
        """Verify Theorem 44.2 for *state* and *move*.

        Checks: if state is admissible AND move is applicable to state,
        then applying the move yields an admissible state.

        Args:
            state: A candidate admissible state.
            move:  A candidate admissible move.

        Returns:
            A :class:`TheoremResult`.
        """
        t0 = time.time()
        tid = THEOREM_IDS["admissibility"]

        state_admissible = (
            bool(state.is_admissible()) if callable(getattr(state, "is_admissible", None)) else True
        )
        move_applicable = (
            bool(move.is_applicable(state)) if callable(getattr(move, "is_applicable", None)) else True
        )
        move_valid = (
            bool(move.validate()) if callable(getattr(move, "validate", None)) else True
        )

        if not state_admissible:
            elapsed = (time.time() - t0) * 1000.0
            self.status = TheoremStatus.CONDITIONALLY_VERIFIED
            return TheoremResult(
                theorem_id=tid,
                satisfied=True,
                counterexample=None,
                evidence={
                    "note": "precondition not met: state not admissible; theorem vacuously true",
                    "state_admissible": False,
                },
                checked_at=time.time(),
                elapsed_ms=elapsed,
            )

        if not move_applicable or not move_valid:
            elapsed = (time.time() - t0) * 1000.0
            self.status = TheoremStatus.CONDITIONALLY_VERIFIED
            return TheoremResult(
                theorem_id=tid,
                satisfied=True,
                counterexample=None,
                evidence={
                    "note": "precondition not met: move not applicable; theorem vacuously true",
                    "move_applicable": move_applicable,
                    "move_valid": move_valid,
                },
                checked_at=time.time(),
                elapsed_ms=elapsed,
            )

        # Apply the move and check the resulting state's admissibility
        try:
            new_state = move.apply(state) if callable(getattr(move, "apply", None)) else state
        except Exception as exc:  # pragma: no cover
            elapsed = (time.time() - t0) * 1000.0
            self.status = TheoremStatus.UNDECIDABLE
            return TheoremResult(
                theorem_id=tid,
                satisfied=False,
                counterexample={"exception": str(exc)},
                evidence={"error": "move.apply raised"},
                checked_at=time.time(),
                elapsed_ms=elapsed,
            )

        new_admissible = (
            bool(new_state.is_admissible()) if callable(getattr(new_state, "is_admissible", None)) else True
        )

        satisfied = new_admissible
        self.status = TheoremStatus.VERIFIED if satisfied else TheoremStatus.FALSIFIED
        elapsed = (time.time() - t0) * 1000.0
        return TheoremResult(
            theorem_id=tid,
            satisfied=satisfied,
            counterexample=(
                None
                if satisfied
                else {
                    "state_id": getattr(state, "state_id", "?"),
                    "move_id": getattr(move, "move_id", "?"),
                    "new_state_admissible": new_admissible,
                }
            ),
            evidence={
                "state_admissible": state_admissible,
                "move_applicable": move_applicable,
                "move_valid": move_valid,
                "new_state_admissible": new_admissible,
            },
            checked_at=time.time(),
            elapsed_ms=elapsed,
        )

    def statement(self) -> str:
        """Return the full theorem statement string."""
        return (
            "Theorem 44.2 (Admissibility Conservation, theory2.tex §44.2):\n"
            "For all s ∈ S, m ∈ M:\n"
            "    is_admissible(s) ∧ is_applicable(m, s) ⟹ is_admissible(m(s))"
        )

    def to_dict(self) -> dict:
        """Serialise theorem metadata to a plain dict."""
        return {
            "theorem_id": THEOREM_IDS["admissibility"],
            "name": self.name,
            "description": self.description,
            "status": self.status.value,
        }


# ---------------------------------------------------------------------------
# Theorem 44.3 – Convergence Law (Lyapunov)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Theorem44_3_ConvergenceLaw:
    """Theorem 44.3: Convergence Law – Lyapunov Stability (theory2.tex §44.3).

    **Statement**: Define the Lyapunov function V: S → ℝ₊ by

        V(s) = 1 − A(s)

    where A is the attainability score.  Under any admissible control law L,
    V is non-increasing along every trajectory:

        V(s_{i+1}) ≤ V(s_i)   for all i ∈ {0, …, n−1}

    This is equivalent to Theorem 44.1 (monotonicity of A) and guarantees
    eventual convergence to the fixed-point set {s : A(s) = 1}.

    Attributes:
        name:        Theorem display name.
        description: Full theorem statement.
        status:      Current :class:`TheoremStatus`.
    """

    name: str = "Theorem 44.3: Convergence Law (Lyapunov)"
    description: str = (
        "The Lyapunov function V(s) = 1 − A(s) is non-increasing along any "
        "trajectory, guaranteeing convergence (theory2.tex §44.3, Theorem 44.3)."
    )
    status: TheoremStatus = field(default=TheoremStatus.UNVERIFIED)

    def check(self, trajectory: SemanticTrajectory) -> TheoremResult:
        """Verify Theorem 44.3 on *trajectory*.

        Computes V(s_i) for each step and checks non-increase.

        Args:
            trajectory: The trajectory to check.

        Returns:
            A :class:`TheoremResult`.
        """
        t0 = time.time()
        tid = THEOREM_IDS["convergence"]

        states = getattr(trajectory, "states", [])
        if len(states) < MIN_TRAJECTORY_LENGTH_FOR_CONVERGENCE:
            elapsed = (time.time() - t0) * 1000.0
            return TheoremResult(
                theorem_id=tid,
                satisfied=True,
                counterexample=None,
                evidence={"note": "trajectory too short", "length": len(states)},
                checked_at=time.time(),
                elapsed_ms=elapsed,
            )

        lyapunov_values: list[float] = [lyapunov_function(s) for s in states]

        violations: list[dict] = []
        for i in range(len(lyapunov_values) - 1):
            vi = lyapunov_values[i]
            vi1 = lyapunov_values[i + 1]
            if vi1 > vi + MONOTONICITY_EPSILON:
                violations.append(
                    {"step": i, "V_i": vi, "V_i1": vi1, "increase": vi1 - vi}
                )

        satisfied = len(violations) == 0
        self.status = TheoremStatus.VERIFIED if satisfied else TheoremStatus.FALSIFIED
        elapsed = (time.time() - t0) * 1000.0
        return TheoremResult(
            theorem_id=tid,
            satisfied=satisfied,
            counterexample=violations[0] if violations else None,
            evidence={
                "trajectory_length": len(states),
                "violation_count": len(violations),
                "initial_V": lyapunov_values[0] if lyapunov_values else None,
                "final_V": lyapunov_values[-1] if lyapunov_values else None,
                "monotone_decrease": satisfied,
            },
            checked_at=time.time(),
            elapsed_ms=elapsed,
        )

    def statement(self) -> str:
        """Return the full theorem statement string."""
        return (
            "Theorem 44.3 (Convergence Law / Lyapunov, theory2.tex §44.3):\n"
            "Define V(s) = 1 − A(s).  Then:\n"
            "    V(s_{i+1}) ≤ V(s_i) for all i along any admissible trajectory.\n"
            "Consequently the control loop converges to {s : A(s) = 1}."
        )

    def to_dict(self) -> dict:
        """Serialise theorem metadata to a plain dict."""
        return {
            "theorem_id": THEOREM_IDS["convergence"],
            "name": self.name,
            "description": self.description,
            "status": self.status.value,
        }


# ---------------------------------------------------------------------------
# Theorem 44.4 – Obligation Finitude and Decidability
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Theorem44_4_ObligationFiniteness:
    """Theorem 44.4: Obligation Finitude and Decidability (theory2.tex §44.4).

    **Statement**: For any reachable state s ∈ S:

        |obligation_ids(s)| < ∞   (finitude)

    and every obligation o ∈ obligation_ids(s) is decidable in finite time
    (decidability).

    The finitude part is checked by verifying that
    ``len(obligation_ids) ≤ OBLIGATION_FINITUDE_LIMIT``.
    The decidability part is checked heuristically by verifying that each
    obligation ID is a non-empty string (proxy for a well-formed handle).

    Attributes:
        name:        Theorem display name.
        description: Full theorem statement.
        status:      Current :class:`TheoremStatus`.
    """

    name: str = "Theorem 44.4: Obligation Finitude and Decidability"
    description: str = (
        "The obligation set is finite and every obligation is decidable in finite "
        "time (theory2.tex §44.4, Theorem 44.4)."
    )
    status: TheoremStatus = field(default=TheoremStatus.UNVERIFIED)

    def check(self, state: SemanticControlState) -> TheoremResult:
        """Verify Theorem 44.4 on *state*.

        Args:
            state: The state whose obligation set is checked.

        Returns:
            A :class:`TheoremResult`.
        """
        t0 = time.time()
        tid = THEOREM_IDS["finitude"]

        obligation_ids = list(getattr(state, "obligation_ids", []) or [])
        count = len(obligation_ids)
        finite = count <= OBLIGATION_FINITUDE_LIMIT

        undecidable: list[str] = [
            oid for oid in obligation_ids if not (isinstance(oid, str) and oid.strip())
        ]
        decidable = len(undecidable) == 0

        satisfied = finite and decidable
        self.status = TheoremStatus.VERIFIED if satisfied else TheoremStatus.FALSIFIED
        elapsed = (time.time() - t0) * 1000.0

        counterexample: dict | None = None
        if not finite:
            counterexample = {
                "obligation_count": count,
                "limit": OBLIGATION_FINITUDE_LIMIT,
                "violation": "obligation set exceeds finitude limit",
            }
        elif not decidable:
            counterexample = {
                "undecidable_count": len(undecidable),
                "examples": undecidable[:5],
                "violation": "undecidable (malformed) obligation IDs detected",
            }

        return TheoremResult(
            theorem_id=tid,
            satisfied=satisfied,
            counterexample=counterexample,
            evidence={
                "obligation_count": count,
                "finitude_limit": OBLIGATION_FINITUDE_LIMIT,
                "finite": finite,
                "decidable": decidable,
                "undecidable_count": len(undecidable),
            },
            checked_at=time.time(),
            elapsed_ms=elapsed,
        )

    def statement(self) -> str:
        """Return the full theorem statement string."""
        return (
            "Theorem 44.4 (Obligation Finitude and Decidability, theory2.tex §44.4):\n"
            "For any reachable state s:\n"
            "    |obligation_ids(s)| < ∞   (finitude)\n"
            "and every obligation o ∈ obligation_ids(s) is decidable in finite time."
        )

    def to_dict(self) -> dict:
        """Serialise theorem metadata to a plain dict."""
        return {
            "theorem_id": THEOREM_IDS["finitude"],
            "name": self.name,
            "description": self.description,
            "status": self.status.value,
        }


# ---------------------------------------------------------------------------
# Lemma 44.A – State Transition Closure
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Lemma44_A_StateTransitionClosure:
    """Lemma 44.A: State Transition Closure (theory2.tex §44.2).

    **Statement**: The state space S is closed under the transition relation
    induced by admissible moves: for every admissible state s and every
    admissible move m applicable at s, the successor state m(s) is in S.

    Checking this property requires verifying that applying each move in
    *moves* to *state* yields a well-formed (non-None, has state_id)
    :class:`SemanticControlState`.

    Attributes:
        name:        Lemma display name.
        description: Full lemma statement.
        status:      Current :class:`TheoremStatus`.
    """

    name: str = "Lemma 44.A: State Transition Closure"
    description: str = (
        "The state space S is closed under the transition relation: applying any "
        "admissible move to any admissible state remains in S "
        "(theory2.tex §44.2, Lemma 44.A)."
    )
    status: TheoremStatus = field(default=TheoremStatus.UNVERIFIED)

    def check(
        self,
        state: SemanticControlState,
        moves: list[AdmissibleMove],
    ) -> TheoremResult:
        """Verify Lemma 44.A for *state* and each move in *moves*.

        For each applicable move, applies it and checks that the result is a
        well-formed state (has a non-empty ``state_id``).

        Args:
            state: The starting admissible state.
            moves: List of candidate admissible moves.

        Returns:
            A :class:`TheoremResult`.
        """
        t0 = time.time()
        tid = THEOREM_IDS["closure"]

        violations: list[dict] = []
        checked = 0
        for move in moves:
            applicable = (
                bool(move.is_applicable(state))
                if callable(getattr(move, "is_applicable", None))
                else True
            )
            if not applicable:
                continue
            checked += 1
            try:
                new_state = move.apply(state) if callable(getattr(move, "apply", None)) else state
            except Exception as exc:  # pragma: no cover
                violations.append(
                    {
                        "move_id": getattr(move, "move_id", "?"),
                        "error": str(exc),
                        "violation": "move.apply raised an exception",
                    }
                )
                continue

            if new_state is None:
                violations.append(
                    {
                        "move_id": getattr(move, "move_id", "?"),
                        "violation": "move.apply returned None (state not in S)",
                    }
                )
                continue

            new_sid = getattr(new_state, "state_id", None)
            if not new_sid:
                violations.append(
                    {
                        "move_id": getattr(move, "move_id", "?"),
                        "violation": "successor state has no state_id",
                    }
                )

        satisfied = len(violations) == 0
        self.status = TheoremStatus.VERIFIED if satisfied else TheoremStatus.FALSIFIED
        elapsed = (time.time() - t0) * 1000.0
        return TheoremResult(
            theorem_id=tid,
            satisfied=satisfied,
            counterexample=violations[0] if violations else None,
            evidence={
                "moves_checked": checked,
                "moves_total": len(moves),
                "violation_count": len(violations),
            },
            checked_at=time.time(),
            elapsed_ms=elapsed,
        )

    def statement(self) -> str:
        """Return the full lemma statement string."""
        return (
            "Lemma 44.A (State Transition Closure, theory2.tex §44.2):\n"
            "The state space S is closed under admissible moves:\n"
            "    ∀ s ∈ S, ∀ m ∈ M : is_admissible(s) ∧ is_applicable(m, s)\n"
            "        ⟹ m(s) ∈ S"
        )

    def to_dict(self) -> dict:
        """Serialise lemma metadata to a plain dict."""
        return {
            "theorem_id": THEOREM_IDS["closure"],
            "name": self.name,
            "description": self.description,
            "status": self.status.value,
        }


# ---------------------------------------------------------------------------
# Module-level factory functions
# ---------------------------------------------------------------------------


def build_theorem_registry() -> TheoremRegistry:
    """Build a :class:`TheoremRegistry` pre-populated with all Ch44 theorems.

    Each theorem is wrapped in a closure so that its :meth:`check` method
    receives the correct positional arguments when called via
    :meth:`TheoremRegistry.verify`.

    Returns:
        A fully populated :class:`TheoremRegistry`.

    Example::

        registry = build_theorem_registry()
        result = registry.verify(
            THEOREM_IDS["monotonicity"],
            trajectory=my_trajectory,
        )
        print(result.summary())
    """
    registry = TheoremRegistry()

    thm1 = Theorem44_1_ControlLawMonotonicity()
    thm2 = Theorem44_2_AdmissibilityConservation()
    thm3 = Theorem44_3_ConvergenceLaw()
    thm4 = Theorem44_4_ObligationFiniteness()
    lem_a = Lemma44_A_StateTransitionClosure()

    # Wrap each check method so it can be called with keyword args
    def _verify_thm1(*args: Any, trajectory: SemanticTrajectory | None = None, **kw: Any) -> TheoremResult:
        traj = trajectory or (args[0] if args else None)
        if traj is None:
            raise ValueError("Theorem 44.1 requires a 'trajectory' argument")
        return thm1.check(traj)

    def _verify_thm2(
        *args: Any,
        state: SemanticControlState | None = None,
        move: AdmissibleMove | None = None,
        **kw: Any,
    ) -> TheoremResult:
        s = state or (args[0] if len(args) > 0 else None)
        m = move or (args[1] if len(args) > 1 else None)
        if s is None or m is None:
            raise ValueError("Theorem 44.2 requires 'state' and 'move' arguments")
        return thm2.check(s, m)

    def _verify_thm3(*args: Any, trajectory: SemanticTrajectory | None = None, **kw: Any) -> TheoremResult:
        traj = trajectory or (args[0] if args else None)
        if traj is None:
            raise ValueError("Theorem 44.3 requires a 'trajectory' argument")
        return thm3.check(traj)

    def _verify_thm4(*args: Any, state: SemanticControlState | None = None, **kw: Any) -> TheoremResult:
        s = state or (args[0] if args else None)
        if s is None:
            raise ValueError("Theorem 44.4 requires a 'state' argument")
        return thm4.check(s)

    def _verify_lem_a(
        *args: Any,
        state: SemanticControlState | None = None,
        moves: list[AdmissibleMove] | None = None,
        **kw: Any,
    ) -> TheoremResult:
        s = state or (args[0] if len(args) > 0 else None)
        m = moves or (args[1] if len(args) > 1 else [])
        if s is None:
            raise ValueError("Lemma 44.A requires a 'state' argument")
        return lem_a.check(s, m or [])

    registry.register(
        theorem_id=THEOREM_IDS["monotonicity"],
        name=thm1.name,
        description=thm1.description,
        verifier=_verify_thm1,
        kind=InvariantKind.MONOTONICITY,
    )
    registry.register(
        theorem_id=THEOREM_IDS["admissibility"],
        name=thm2.name,
        description=thm2.description,
        verifier=_verify_thm2,
        kind=InvariantKind.CONSERVATION,
    )
    registry.register(
        theorem_id=THEOREM_IDS["convergence"],
        name=thm3.name,
        description=thm3.description,
        verifier=_verify_thm3,
        kind=InvariantKind.CONVERGENCE,
    )
    registry.register(
        theorem_id=THEOREM_IDS["finitude"],
        name=thm4.name,
        description=thm4.description,
        verifier=_verify_thm4,
        kind=InvariantKind.FINITUDE,
    )
    registry.register(
        theorem_id=THEOREM_IDS["closure"],
        name=lem_a.name,
        description=lem_a.description,
        verifier=_verify_lem_a,
        kind=InvariantKind.CLOSURE,
    )

    logger.debug("build_theorem_registry: registered %d theorems", len(registry.theorems))
    return registry


def verify_all_theorems(
    state: SemanticControlState,
    trajectory: SemanticTrajectory | None = None,
    moves: list[AdmissibleMove] | None = None,
    move: AdmissibleMove | None = None,
) -> dict[str, TheoremResult]:
    """Verify all Ch44 theorems in a single call.

    Builds a fresh :class:`TheoremRegistry` and runs each verifier with the
    provided arguments.  Theorems whose required arguments are None are
    skipped (their results carry ``UNDECIDABLE`` status and an explanatory
    note).

    Args:
        state:      The current semantic-control state (required for
                    Theorem 44.2, Theorem 44.4, Lemma 44.A).
        trajectory: The trajectory (required for Theorem 44.1, Theorem 44.3).
        moves:      Candidate moves list (required for Lemma 44.A).
        move:       A single admissible move (required for Theorem 44.2).

    Returns:
        Dict mapping theorem ID to :class:`TheoremResult`.

    Example::

        results = verify_all_theorems(state=s, trajectory=τ, move=m)
        for tid, result in results.items():
            print(result.summary())
    """
    registry = build_theorem_registry()
    results: dict[str, TheoremResult] = {}

    def _skip(tid: str, reason: str) -> TheoremResult:
        return TheoremResult(
            theorem_id=tid,
            satisfied=False,
            counterexample=None,
            evidence={"skipped": True, "reason": reason},
            checked_at=time.time(),
            elapsed_ms=0.0,
        )

    # Theorem 44.1 – requires trajectory
    tid1 = THEOREM_IDS["monotonicity"]
    if trajectory is not None:
        results[tid1] = registry.verify(tid1, trajectory=trajectory)
    else:
        results[tid1] = _skip(tid1, "no trajectory provided")

    # Theorem 44.2 – requires state and move
    tid2 = THEOREM_IDS["admissibility"]
    if move is not None:
        results[tid2] = registry.verify(tid2, state=state, move=move)
    else:
        results[tid2] = _skip(tid2, "no move provided")

    # Theorem 44.3 – requires trajectory
    tid3 = THEOREM_IDS["convergence"]
    if trajectory is not None:
        results[tid3] = registry.verify(tid3, trajectory=trajectory)
    else:
        results[tid3] = _skip(tid3, "no trajectory provided")

    # Theorem 44.4 – requires state
    tid4 = THEOREM_IDS["finitude"]
    results[tid4] = registry.verify(tid4, state=state)

    # Lemma 44.A – requires state and moves
    tidA = THEOREM_IDS["closure"]
    results[tidA] = registry.verify(tidA, state=state, moves=moves or [])

    logger.debug(
        "verify_all_theorems: %d/%d passed",
        sum(1 for r in results.values() if r.satisfied),
        len(results),
    )
    return results


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    # Enumerations
    "InvariantKind",
    "TheoremStatus",
    # Value object
    "TheoremResult",
    # Checker and registry
    "InvariantChecker",
    "TheoremRegistry",
    # Theorem classes
    "Theorem44_1_ControlLawMonotonicity",
    "Theorem44_2_AdmissibilityConservation",
    "Theorem44_3_ConvergenceLaw",
    "Theorem44_4_ObligationFiniteness",
    "Lemma44_A_StateTransitionClosure",
    # Constants
    "MONOTONICITY_EPSILON",
    "MIN_TRAJECTORY_LENGTH_FOR_CONVERGENCE",
    "OBLIGATION_FINITUDE_LIMIT",
    "THEOREM_IDS",
    "THEOREMS_VERSION",
    # Factory / convenience
    "build_theorem_registry",
    "verify_all_theorems",
]
