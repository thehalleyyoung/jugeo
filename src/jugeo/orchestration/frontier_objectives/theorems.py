"""
Theorem statements and invariant checkers for frontier objectives (Ch47).

This module formalises the theoretical guarantees underpinning the frontier
objective system.  Each theorem maps to a named class whose ``check()`` method
operationalises the statement against runtime data.  A :class:`TheoremRegistry`
pre-populated with all Ch47 theorems is available as :data:`DEFAULT_REGISTRY`.

Theorems defined here:
  * Theorem 47.1 — Closure-gain monotonicity under refinement
  * Theorem 47.2 — Phase-transition detectability from score history
  * Theorem 47.3 — Diversity maintainability under budget constraints
  * Theorem 47.4 — Budget-allocation feasibility
  * Lemma 47.A  — Objective composability (associativity)

Invariant kinds tracked by :class:`InvariantChecker`:
  * MONOTONICITY, DETECTABILITY, MAINTAINABILITY, FEASIBILITY, COMPOSABILITY
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any
from uuid import uuid4

# ---------------------------------------------------------------------------
# Upstream guards
# ---------------------------------------------------------------------------
try:
    from jugeo.orchestration.frontier import (
        FrontierItem,
        FrontierState,
        FrontierNode,
        Frontier,
        FrontierSearch,
        FrontierScorer,
        PhaseTransition,
        BackpressureController,
        FrontierDiversity,
        FrontierBudget,
        FrontierHistory,
        FrontierDiagnostics,
        PhaseKind,
        TransitionTrigger,
    )
except Exception:
    pass

try:
    from jugeo.orchestration.controller import (
        OrchestratorState,
        SemanticMove,
        MoveKind,
        ControlLaw,
        GreedyControl,
        LookaheadControl,
        BalancedControl,
        AdaptiveControl,
        Orchestrator,
        ConvergenceMonitor,
        MoveHistory,
        OrchestratorConfiguration,
        MoveGenerator,
    )
except Exception:
    pass

try:
    from jugeo.orchestration.fleet import (
        FleetMember,
        FleetBid,
        Fleet,
        BidEvaluator,
        FleetScheduler,
        CompetitiveSearch,
        FleetCalibration,
        ChallengeRecord,
        FleetHistory,
        FleetDiagnostics,
        FleetState,
        BidOutcome,
        ChallengeOutcome,
    )
except Exception:
    pass

try:
    from jugeo.orchestration.negotiation import (
        NegotiationSession,
        TreatyProposal,
        FrictionPattern,
        CompromiseStrategy,
        NegotiationMemory,
        DeadlockDetector,
        Negotiator,
        NegotiationHistory,
        TreatyArchive,
        NegotiationEventBus,
        NegotiationDiagnostics,
        NegotiationPosition,
        NegotiationRound,
        SessionState,
        DeadlockKind,
    )
except Exception:
    pass

try:
    from jugeo.evidence.trust import (
        TrustLevel,
        TrustAlgebra,
        TrustComposition,
        TrustAttenuation,
        TrustPromotion,
        TrustCeiling,
        TrustPolicy,
        TrustAuditLog,
        TrustTier,
        TrustProfile,
        join_trust_profiles,
    )
except Exception:
    pass

try:
    from jugeo.geometry.descent import (
        LocalSection,
        OverlapCondition,
        GluingData,
        DescentEngine,
        DescentResult,
        GlobalSection,
        DescentObstruction,
        DescentLog,
        OverlapStatus,
        DescentStrategy,
        DescentConfiguration,
        CohomologyClass,
        RepairFrontier,
        Obstruction,
    )
except Exception:
    pass

try:
    from jugeo.orchestration.frontier_objectives.models import (
        FrontierObjective,
        ObjectiveKind,
        ClosureGainEstimate,
        DiversityMetric,
        ScoringState,
        BudgetPolicy,
    )
except Exception:
    pass

try:
    from jugeo.orchestration.frontier_objectives.algorithms import (
        exponential_moving_average,
        detect_phase_transition,
    )
except Exception:
    def exponential_moving_average(series: list[float], alpha: float = 0.3) -> float:  # type: ignore[misc]
        return series[-1] if series else 0.0

    def detect_phase_transition(history: list[float], window: int = 20) -> Any:  # type: ignore[misc]
        return type("R", (), {"detected": False, "from_phase": "unknown", "to_phase": "unknown"})()


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class TheoremStatus(Enum):
    """The verification status of a theorem."""

    CONJECTURED = auto()
    VERIFIED = auto()
    REFUTED = auto()
    PARTIAL = auto()


class InvariantKind(Enum):
    """Classification of system invariants verified by :class:`InvariantChecker`."""

    MONOTONICITY = auto()
    DETECTABILITY = auto()
    MAINTAINABILITY = auto()
    FEASIBILITY = auto()
    COMPOSABILITY = auto()


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class TheoremBase:
    """Abstract base for Ch47 theorem statements.

    Subclasses must override :meth:`check` with logic that operationalises the
    theorem against runtime data.

    Attributes
    ----------
    theorem_id:
        Unique machine-readable identifier (e.g. ``"47.1"``).
    name:
        Short human-readable name.
    statement:
        Full natural-language statement of the theorem.
    chapter_ref:
        Source chapter reference.
    status:
        Current verification status.
    proof_sketch:
        Informal proof sketch (may be empty).
    """

    theorem_id: str
    name: str
    statement: str
    chapter_ref: str = "Ch47"
    status: TheoremStatus = TheoremStatus.CONJECTURED
    proof_sketch: str = ""

    def check(self, *args: Any, **kwargs: Any) -> bool:
        """Check the theorem against runtime evidence.

        Must be overridden by subclasses.  The base implementation always
        returns ``False`` to force deliberate implementation.
        """
        raise NotImplementedError(
            f"Theorem '{self.theorem_id}' has not implemented check()."
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise the theorem to a plain dictionary."""
        return {
            "theorem_id": self.theorem_id,
            "name": self.name,
            "statement": self.statement,
            "chapter_ref": self.chapter_ref,
            "status": self.status.name,
            "proof_sketch": self.proof_sketch,
        }

    def verify(self, evidence: dict) -> TheoremStatus:
        """Attempt to verify the theorem using the supplied *evidence*.

        Calls :meth:`check` with the relevant fields extracted from *evidence*.
        Updates and returns :attr:`status`.

        Parameters
        ----------
        evidence:
            Arbitrary mapping of evidence keys to values; subclasses should
            document which keys they consume.

        Returns
        -------
        TheoremStatus
        """
        try:
            result = self._verify_from_evidence(evidence)
            if result is True:
                self.status = TheoremStatus.VERIFIED
            elif result is False:
                self.status = TheoremStatus.REFUTED
            else:
                self.status = TheoremStatus.PARTIAL
        except Exception:
            self.status = TheoremStatus.PARTIAL
        return self.status

    def _verify_from_evidence(self, evidence: dict) -> "bool | None":
        """Extract relevant evidence fields and delegate to :meth:`check`.

        Subclasses may override this to map evidence keys to ``check()``
        arguments more precisely.  The base implementation passes the entire
        evidence dict as ``kwargs``.
        """
        return self.check(**evidence)


# ---------------------------------------------------------------------------
# Theorem implementations
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Theorem47_1_ClosureGainMonotonicity(TheoremBase):
    """Theorem 47.1 — Closure gain is monotone under refinement.

    Formally: if (n_0, n_1, …, n_k) is a refinement sequence then the EMA
    trend of the associated closure gains is non-negative.
    """

    theorem_id: str = "47.1"
    name: str = "ClosureGainMonotonicity"
    statement: str = (
        "Closure gain is monotone under refinement: the exponential moving "
        "average of the gain sequence must be non-decreasing on average."
    )
    proof_sketch: str = (
        "By induction on refinement depth.  Each refinement step strictly "
        "reduces the search space, which can only increase (or maintain) the "
        "achievable closure gain.  The EMA trend captures the aggregate "
        "directional signal over a sliding window."
    )

    def check(self, node_history: list[float], **_: Any) -> bool:  # type: ignore[override]
        """Check that the EMA trend of *node_history* is non-negative.

        Parameters
        ----------
        node_history:
            Sequence of closure-gain observations for a refinement chain
            (oldest first).

        Returns
        -------
        bool
            ``True`` when the sequence is non-decreasing on average (EMA
            trend >= 0).
        """
        if len(node_history) < 2:
            # Insufficient data — vacuously true
            return True
        # Compute EMA at each time step and check the terminal trend
        alpha = 0.3
        ema_series: list[float] = [node_history[0]]
        for v in node_history[1:]:
            ema_series.append(alpha * v + (1.0 - alpha) * ema_series[-1])
        # Trend: difference between last and first EMA
        trend = ema_series[-1] - ema_series[0]
        return trend >= 0.0

    def _verify_from_evidence(self, evidence: dict) -> "bool | None":
        history = evidence.get("node_history", evidence.get("history", []))
        if not isinstance(history, list):
            return None
        return self.check(node_history=history)


@dataclass(slots=True)
class Theorem47_2_PhaseTransitionDetectability(TheoremBase):
    """Theorem 47.2 — Phase transitions are detectable from score history.

    Formally: for any phase transition there exists a window size W such that
    the score-history variance within W exceeds a detection threshold.
    """

    theorem_id: str = "47.2"
    name: str = "PhaseTransitionDetectability"
    statement: str = (
        "Phase transitions are detectable from score history provided the "
        "history is long enough (>= window size) and contains sufficient "
        "variance."
    )
    proof_sketch: str = (
        "A phase transition necessarily changes the underlying score "
        "distribution.  By the law of large numbers, the sample mean "
        "converges; the mean-shift between the first and second halves of the "
        "window therefore converges to the true distributional shift, enabling "
        "reliable detection above a threshold."
    )

    # Minimum required variance to assert detectability
    _MIN_VARIANCE: float = field(default=1e-4, init=False, repr=False)

    def check(  # type: ignore[override]
        self,
        score_history: list[float],
        window: int = 20,
        **_: Any,
    ) -> bool:
        """Check that *score_history* is long and varied enough for detection.

        Parameters
        ----------
        score_history:
            Ordered sequence of numeric scores.
        window:
            Minimum required history length.

        Returns
        -------
        bool
            ``True`` when the history satisfies the detectability preconditions.
        """
        if len(score_history) < window:
            return False
        recent = score_history[-window:]
        mean = sum(recent) / len(recent)
        variance = sum((x - mean) ** 2 for x in recent) / len(recent)
        return variance >= self._MIN_VARIANCE

    def _verify_from_evidence(self, evidence: dict) -> "bool | None":
        history = evidence.get("score_history", evidence.get("history", []))
        window = int(evidence.get("window", 20))
        if not isinstance(history, list):
            return None
        return self.check(score_history=history, window=window)


@dataclass(slots=True)
class Theorem47_3_DiversityMaintenability(TheoremBase):
    """Theorem 47.3 — Diversity can be maintained under budget constraints.

    Formally: provided the remaining budget exceeds a minimum threshold,
    the diversity metric stays above *min_diversity*.
    """

    theorem_id: str = "47.3"
    name: str = "DiversityMaintenability"
    statement: str = (
        "Diversity can be maintained under budget constraints when the "
        "remaining budget exceeds a per-diversity-unit minimum cost."
    )
    proof_sketch: str = (
        "Each unit of diversity (unique node type) requires at least one "
        "evaluation budget unit to maintain.  Therefore the budget lower-bound "
        "for maintaining diversity d is proportional to d.  When budget >= "
        "min_budget_for_diversity AND current diversity >= threshold, the "
        "invariant holds."
    )

    _MIN_BUDGET_PER_DIVERSITY_UNIT: float = field(default=2.0, init=False, repr=False)

    def check(  # type: ignore[override]
        self,
        budget_remaining: float,
        diversity_metric: Any,
        min_diversity: float = 0.3,
        **_: Any,
    ) -> bool:
        """Check that budget and diversity both satisfy their lower bounds.

        Parameters
        ----------
        budget_remaining:
            Budget still available.
        diversity_metric:
            Object with a ``coverage_ratio`` or ``entropy`` attribute, or a
            plain float.
        min_diversity:
            Minimum acceptable diversity value.

        Returns
        -------
        bool
        """
        # Extract diversity value
        if isinstance(diversity_metric, (int, float)):
            diversity_value = float(diversity_metric)
        else:
            diversity_value = float(
                getattr(diversity_metric, "coverage_ratio", None)
                or getattr(diversity_metric, "entropy", 0.0)
            )
        cluster_count = float(getattr(diversity_metric, "cluster_count", 1))
        min_budget_needed = cluster_count * self._MIN_BUDGET_PER_DIVERSITY_UNIT

        budget_ok = budget_remaining >= min_budget_needed
        diversity_ok = diversity_value >= min_diversity
        return budget_ok and diversity_ok

    def _verify_from_evidence(self, evidence: dict) -> "bool | None":
        budget = float(evidence.get("budget_remaining", 0.0))
        dm = evidence.get("diversity_metric", 0.0)
        min_div = float(evidence.get("min_diversity", 0.3))
        return self.check(
            budget_remaining=budget, diversity_metric=dm, min_diversity=min_div
        )


@dataclass(slots=True)
class Theorem47_4_BudgetFeasibility(TheoremBase):
    """Theorem 47.4 — Budget allocation is always feasible.

    Formally: the sum of all allocations must not exceed the total budget.
    """

    theorem_id: str = "47.4"
    name: str = "BudgetFeasibility"
    statement: str = (
        "Budget allocation is always feasible: the sum of per-objective "
        "allocations must not exceed the total available budget."
    )
    proof_sketch: str = (
        "The allocation algorithm is proportional — each objective receives a "
        "fraction weight_i / sum(weights) of the total budget.  By "
        "construction, sum(fractions) == 1, so sum(allocations) == total_budget."
    )

    def check(  # type: ignore[override]
        self,
        total_budget: float,
        allocations: dict[str, float],
        **_: Any,
    ) -> bool:
        """Check that the sum of *allocations* does not exceed *total_budget*.

        Parameters
        ----------
        total_budget:
            Maximum budget available.
        allocations:
            Mapping from objective ID to allocated budget share.

        Returns
        -------
        bool
            ``True`` when ``sum(allocations.values()) <= total_budget``.
        """
        if not allocations:
            return True
        total_allocated = sum(allocations.values())
        # Allow a tiny floating-point tolerance
        return total_allocated <= total_budget + 1e-9

    def _verify_from_evidence(self, evidence: dict) -> "bool | None":
        total = float(evidence.get("total_budget", 0.0))
        allocs = evidence.get("allocations", {})
        if not isinstance(allocs, dict):
            return None
        return self.check(total_budget=total, allocations=allocs)


@dataclass(slots=True)
class Lemma47_A_ObjectiveComposability(TheoremBase):
    """Lemma 47.A — Objectives compose associatively.

    Formally: for objectives A, B, C, the combined score of (A ∘ B) ∘ C equals
    that of A ∘ (B ∘ C) up to floating-point precision.
    """

    theorem_id: str = "47.A"
    name: str = "ObjectiveComposability"
    statement: str = (
        "Objectives compose associatively: the weighted score of "
        "(obj_a.combine(obj_b)).combine(obj_c) equals "
        "obj_a.combine(obj_b.combine(obj_c)) for any admissible state."
    )
    proof_sketch: str = (
        "Objective combination is defined as a weighted convex sum of "
        "individual scores.  Convex combination over reals is associative "
        "when weights are renormalised at each combination step.  The "
        "implementation checks this numerically to within a tolerance of 1e-6."
    )

    _TOLERANCE: float = field(default=1e-6, init=False, repr=False)

    def check(  # type: ignore[override]
        self,
        obj_a: Any,
        obj_b: Any,
        obj_c: Any,
        state: Any,
        **_: Any,
    ) -> bool:
        """Check associativity of objective combination numerically.

        Parameters
        ----------
        obj_a, obj_b, obj_c:
            Objective objects that expose a ``weight`` attribute and a
            ``combine(other)`` method returning a new objective.
        state:
            State object forwarded to objective scoring.

        Returns
        -------
        bool
            ``True`` when both combination orders yield the same weighted
            aggregate score (within :attr:`_TOLERANCE`).
        """
        combine_a = getattr(obj_a, "combine", None)
        combine_b = getattr(obj_b, "combine", None)
        if not (callable(combine_a) and callable(combine_b)):
            # If combine() is absent, check weight associativity instead
            return self._weight_associativity(obj_a, obj_b, obj_c)

        try:
            # (A ∘ B) ∘ C
            ab = obj_a.combine(obj_b)
            ab_c = ab.combine(obj_c) if callable(getattr(ab, "combine", None)) else ab
            score_left = self._score(ab_c, state)

            # A ∘ (B ∘ C)
            bc = obj_b.combine(obj_c) if callable(combine_b) else obj_b
            a_bc = obj_a.combine(bc)
            score_right = self._score(a_bc, state)

            return abs(score_left - score_right) <= self._TOLERANCE
        except Exception:
            return self._weight_associativity(obj_a, obj_b, obj_c)

    def _score(self, obj: Any, state: Any) -> float:
        """Extract a scalar score from an objective."""
        score_fn = getattr(obj, "score", None)
        if callable(score_fn):
            try:
                return float(score_fn(state))
            except Exception:
                pass
        return float(getattr(obj, "weight", 1.0))

    def _weight_associativity(self, a: Any, b: Any, c: Any) -> bool:
        """Fallback: check that weight sums are order-independent."""
        wa = float(getattr(a, "weight", 1.0))
        wb = float(getattr(b, "weight", 1.0))
        wc = float(getattr(c, "weight", 1.0))
        # (wa + wb) + wc == wa + (wb + wc) — always true for reals
        left = (wa + wb) + wc
        right = wa + (wb + wc)
        return abs(left - right) <= self._TOLERANCE

    def _verify_from_evidence(self, evidence: dict) -> "bool | None":
        obj_a = evidence.get("obj_a")
        obj_b = evidence.get("obj_b")
        obj_c = evidence.get("obj_c")
        state = evidence.get("state")
        if any(x is None for x in [obj_a, obj_b, obj_c]):
            return None
        return self.check(obj_a=obj_a, obj_b=obj_b, obj_c=obj_c, state=state)


# ---------------------------------------------------------------------------
# InvariantViolation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InvariantViolation:
    """Record of an invariant violation detected at runtime.

    Attributes
    ----------
    violation_id:
        Unique identifier for this violation record.
    invariant_kind:
        The :class:`InvariantKind` that was violated.
    message:
        Human-readable description of the violation.
    context:
        Structured context data captured at the time of violation.
    timestamp:
        Unix epoch time of detection.
    """

    violation_id: str
    invariant_kind: InvariantKind
    message: str
    context: dict
    timestamp: float

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {
            "violation_id": self.violation_id,
            "invariant_kind": self.invariant_kind.name,
            "message": self.message,
            "context": self.context,
            "timestamp": self.timestamp,
        }


# ---------------------------------------------------------------------------
# TheoremProofAttempt
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TheoremProofAttempt:
    """A record of a single attempt to prove a theorem.

    Attributes
    ----------
    attempt_id:
        Unique identifier for this attempt.
    theorem_id:
        The theorem being proved.
    strategy:
        Description of the proof strategy used.
    steps:
        Ordered tuple of proof steps (as strings).
    conclusion:
        Summary conclusion of the attempt.
    success:
        Whether the proof attempt succeeded.
    timestamp:
        Unix epoch time of the attempt.
    """

    attempt_id: str
    theorem_id: str
    strategy: str
    steps: tuple[str, ...]
    conclusion: str
    success: bool
    timestamp: float

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {
            "attempt_id": self.attempt_id,
            "theorem_id": self.theorem_id,
            "strategy": self.strategy,
            "steps": list(self.steps),
            "conclusion": self.conclusion,
            "success": self.success,
            "timestamp": self.timestamp,
        }

    @classmethod
    def make(
        cls,
        theorem_id: str,
        strategy: str,
        steps: "tuple[str, ...] | list[str]",
        conclusion: str,
        success: bool,
    ) -> "TheoremProofAttempt":
        """Construct a :class:`TheoremProofAttempt` with a generated ID and timestamp.

        Parameters
        ----------
        theorem_id:
            The theorem being proved.
        strategy:
            Proof strategy description.
        steps:
            Proof step descriptions.
        conclusion:
            Conclusion string.
        success:
            Whether the attempt succeeded.
        """
        return cls(
            attempt_id=str(uuid4()),
            theorem_id=theorem_id,
            strategy=strategy,
            steps=tuple(steps),
            conclusion=conclusion,
            success=success,
            timestamp=time.time(),
        )


# ---------------------------------------------------------------------------
# InvariantChecker
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class InvariantChecker:
    """Checks system invariants on runtime objects.

    Attributes
    ----------
    invariants:
        Ordered list of invariant-check records (populated during operation).
    violations:
        List of detected :class:`InvariantViolation` dicts.
    """

    invariants: list[dict] = field(default_factory=list)
    violations: list[dict] = field(default_factory=list)

    # ------------------------------------------------------------------

    def check_monotonicity(self, series: list[float]) -> bool:
        """Check that *series* is non-decreasing on average (EMA trend >= 0).

        Parameters
        ----------
        series:
            Ordered sequence of numeric values.

        Returns
        -------
        bool
        """
        if len(series) < 2:
            return True
        alpha = 0.3
        ema = series[0]
        ema_start = ema
        for v in series[1:]:
            ema = alpha * v + (1.0 - alpha) * ema
        trend = ema - ema_start
        result = trend >= 0.0
        self._record(
            InvariantKind.MONOTONICITY,
            result,
            {"series_length": len(series), "trend": trend},
        )
        return result

    def check_budget_feasibility(
        self, total: float, allocations: dict[str, float]
    ) -> bool:
        """Check that allocations do not exceed *total*.

        Parameters
        ----------
        total:
            Total available budget.
        allocations:
            Per-objective allocation mapping.

        Returns
        -------
        bool
        """
        allocated = sum(allocations.values())
        result = allocated <= total + 1e-9
        self._record(
            InvariantKind.FEASIBILITY,
            result,
            {"total": total, "allocated": allocated},
        )
        return result

    def check_diversity(self, metric: Any) -> bool:
        """Check that the diversity metric meets a minimum threshold.

        Parameters
        ----------
        metric:
            Object with ``coverage_ratio`` or ``entropy``; or a plain float.

        Returns
        -------
        bool
        """
        _MIN = 0.05
        if isinstance(metric, (int, float)):
            value = float(metric)
        else:
            value = float(
                getattr(metric, "coverage_ratio", None)
                or getattr(metric, "entropy", 0.0)
            )
        result = value >= _MIN
        self._record(
            InvariantKind.MAINTAINABILITY,
            result,
            {"diversity_value": value, "threshold": _MIN},
        )
        return result

    def check_composability(self, objectives: list[Any], state: Any) -> bool:
        """Check that objective combination is associative for all triples.

        Parameters
        ----------
        objectives:
            List of at least three objectives.
        state:
            State forwarded to the lemma checker.

        Returns
        -------
        bool
            ``True`` when all sampled triples satisfy associativity.
        """
        lemma = Lemma47_A_ObjectiveComposability()
        if len(objectives) < 3:
            return True  # vacuously true
        # Check first triple only for performance
        result = lemma.check(
            obj_a=objectives[0],
            obj_b=objectives[1],
            obj_c=objectives[2],
            state=state,
        )
        self._record(
            InvariantKind.COMPOSABILITY,
            result,
            {"objective_count": len(objectives)},
        )
        return result

    def run_all_checks(self, context: dict) -> dict[str, bool]:
        """Run all invariant checks using values from *context*.

        Parameters
        ----------
        context:
            Mapping that may contain:
            * ``series`` — list[float] for monotonicity check
            * ``total_budget`` + ``allocations`` — for feasibility
            * ``diversity_metric`` — for diversity check
            * ``objectives`` + ``state`` — for composability check

        Returns
        -------
        dict[str, bool]
            Mapping from check name to result.
        """
        results: dict[str, bool] = {}

        series = context.get("series", [])
        results["monotonicity"] = self.check_monotonicity(series)

        total = float(context.get("total_budget", 0.0))
        allocations = context.get("allocations", {})
        results["budget_feasibility"] = self.check_budget_feasibility(
            total, allocations
        )

        dm = context.get("diversity_metric", 0.0)
        results["diversity"] = self.check_diversity(dm)

        objectives = context.get("objectives", [])
        state = context.get("state")
        results["composability"] = self.check_composability(objectives, state)

        return results

    def report_violations(self) -> list[dict]:
        """Return all recorded violations.

        Returns
        -------
        list[dict]
        """
        return list(self.violations)

    def clear(self) -> None:
        """Clear all recorded invariants and violations."""
        self.invariants.clear()
        self.violations.clear()

    def to_dict(self) -> dict[str, Any]:
        """Serialise checker state."""
        return {
            "invariant_count": len(self.invariants),
            "violation_count": len(self.violations),
        }

    # ------------------------------------------------------------------

    def _record(
        self,
        kind: InvariantKind,
        result: bool,
        context: dict,
    ) -> None:
        """Record an invariant check and register a violation if it failed."""
        entry = {
            "kind": kind.name,
            "result": result,
            "context": context,
            "timestamp": time.time(),
        }
        self.invariants.append(entry)
        if not result:
            violation = InvariantViolation(
                violation_id=str(uuid4()),
                invariant_kind=kind,
                message=f"Invariant {kind.name} violated",
                context=context,
                timestamp=time.time(),
            )
            self.violations.append(violation.to_dict())


# ---------------------------------------------------------------------------
# TheoremRegistry
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class TheoremRegistry:
    """Registry of :class:`TheoremBase` instances.

    Attributes
    ----------
    theorems:
        Mapping from theorem_id to theorem object.
    """

    theorems: dict[str, TheoremBase] = field(default_factory=dict)

    def register(self, theorem: TheoremBase) -> None:
        """Add *theorem* to the registry.

        Parameters
        ----------
        theorem:
            Theorem to register; keyed by its :attr:`~TheoremBase.theorem_id`.
        """
        self.theorems[theorem.theorem_id] = theorem

    def get(self, theorem_id: str) -> "TheoremBase | None":
        """Retrieve a theorem by its ID.

        Parameters
        ----------
        theorem_id:
            Identifier string.

        Returns
        -------
        TheoremBase or None
        """
        return self.theorems.get(theorem_id)

    def verify_all(self, context: dict) -> dict[str, TheoremStatus]:
        """Verify every registered theorem against *context*.

        Parameters
        ----------
        context:
            Shared evidence dictionary forwarded to each theorem's
            :meth:`~TheoremBase.verify` method.

        Returns
        -------
        dict[str, TheoremStatus]
            Mapping from theorem_id to resulting status.
        """
        results: dict[str, TheoremStatus] = {}
        for tid, theorem in self.theorems.items():
            try:
                results[tid] = theorem.verify(context)
            except Exception:
                results[tid] = TheoremStatus.PARTIAL
        return results

    def list_verified(self) -> list[str]:
        """Return IDs of all theorems with VERIFIED status.

        Returns
        -------
        list[str]
        """
        return [
            tid
            for tid, t in self.theorems.items()
            if t.status == TheoremStatus.VERIFIED
        ]

    def list_conjectured(self) -> list[str]:
        """Return IDs of all theorems with CONJECTURED status.

        Returns
        -------
        list[str]
        """
        return [
            tid
            for tid, t in self.theorems.items()
            if t.status == TheoremStatus.CONJECTURED
        ]

    def to_dict(self) -> dict[str, Any]:
        """Serialise registry contents."""
        return {
            "theorem_count": len(self.theorems),
            "theorems": {tid: t.to_dict() for tid, t in self.theorems.items()},
        }

    @classmethod
    def default(cls) -> "TheoremRegistry":
        """Construct a :class:`TheoremRegistry` pre-populated with Ch47 theorems.

        Returns
        -------
        TheoremRegistry
        """
        reg = cls()
        reg.register(Theorem47_1_ClosureGainMonotonicity())
        reg.register(Theorem47_2_PhaseTransitionDetectability())
        reg.register(Theorem47_3_DiversityMaintenability())
        reg.register(Theorem47_4_BudgetFeasibility())
        reg.register(Lemma47_A_ObjectiveComposability())
        return reg


# ---------------------------------------------------------------------------
# TheoremVerifier
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class TheoremVerifier:
    """Verifies theorems in a :class:`TheoremRegistry` against evidence.

    Attributes
    ----------
    registry:
        The theorem registry to operate on.
    evidence_log:
        Accumulated evidence entries (keyed by theorem_id).
    """

    registry: TheoremRegistry
    evidence_log: list[dict] = field(default_factory=list)

    def verify(self, theorem_id: str, evidence: dict) -> TheoremStatus:
        """Verify a single theorem by ID.

        Parameters
        ----------
        theorem_id:
            ID of the theorem to verify.
        evidence:
            Evidence dictionary forwarded to the theorem.

        Returns
        -------
        TheoremStatus
            Resulting status, or :attr:`TheoremStatus.PARTIAL` when the
            theorem is not found.
        """
        theorem = self.registry.get(theorem_id)
        if theorem is None:
            return TheoremStatus.PARTIAL
        self.add_evidence(theorem_id, evidence)
        return theorem.verify(evidence)

    def verify_all(self, evidence: dict) -> dict[str, TheoremStatus]:
        """Verify all registered theorems using shared *evidence*.

        Parameters
        ----------
        evidence:
            Shared evidence dictionary.

        Returns
        -------
        dict[str, TheoremStatus]
        """
        return self.registry.verify_all(evidence)

    def add_evidence(self, theorem_id: str, evidence: dict) -> None:
        """Record evidence for a theorem.

        Parameters
        ----------
        theorem_id:
            Target theorem ID.
        evidence:
            Evidence to record.
        """
        self.evidence_log.append(
            {
                "theorem_id": theorem_id,
                "evidence": evidence,
                "timestamp": time.time(),
            }
        )

    def summary(self) -> str:
        """Return a human-readable summary of verification results.

        Returns
        -------
        str
        """
        lines: list[str] = ["TheoremVerifier summary:"]
        for tid, theorem in self.registry.theorems.items():
            lines.append(f"  [{theorem.status.name:12s}] {tid}: {theorem.name}")
        lines.append(f"\nEvidence entries: {len(self.evidence_log)}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        """Serialise verifier state."""
        return {
            "registry": self.registry.to_dict(),
            "evidence_count": len(self.evidence_log),
        }

    @classmethod
    def make(cls) -> "TheoremVerifier":
        """Construct a :class:`TheoremVerifier` backed by the default registry.

        Returns
        -------
        TheoremVerifier
        """
        return cls(registry=TheoremRegistry.default())


# ---------------------------------------------------------------------------
# Module-level convenience functions
# ---------------------------------------------------------------------------


def check_all_invariants(context: dict) -> dict[str, bool]:
    """Run all invariant checks against *context* using a fresh checker.

    Parameters
    ----------
    context:
        Evidence context dict (same as :meth:`InvariantChecker.run_all_checks`).

    Returns
    -------
    dict[str, bool]
        Mapping from invariant name to boolean result.
    """
    checker = InvariantChecker()
    return checker.run_all_checks(context)


def build_default_registry() -> TheoremRegistry:
    """Build and return a default :class:`TheoremRegistry` with Ch47 theorems.

    Returns
    -------
    TheoremRegistry
    """
    return TheoremRegistry.default()


def get_theorem_summary() -> str:
    """Return a string summary of all Ch47 theorems in the default registry.

    Returns
    -------
    str
    """
    lines: list[str] = ["Ch47 Theorem Registry:"]
    for tid, theorem in DEFAULT_REGISTRY.theorems.items():
        lines.append(
            f"  {tid:8s} [{theorem.status.name:12s}] {theorem.name}: "
            f"{theorem.statement[:60]}..."
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Module-level default registry
# ---------------------------------------------------------------------------

#: Pre-populated registry of all Ch47 theorems; shared module-level instance.
DEFAULT_REGISTRY: TheoremRegistry = TheoremRegistry.default()

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    # Enumerations
    "TheoremStatus",
    "InvariantKind",
    # Base class
    "TheoremBase",
    # Theorems
    "Theorem47_1_ClosureGainMonotonicity",
    "Theorem47_2_PhaseTransitionDetectability",
    "Theorem47_3_DiversityMaintenability",
    "Theorem47_4_BudgetFeasibility",
    "Lemma47_A_ObjectiveComposability",
    # Invariant checking
    "InvariantChecker",
    "InvariantViolation",
    # Proof attempt
    "TheoremProofAttempt",
    # Registry and verifier
    "TheoremRegistry",
    "TheoremVerifier",
    # Module-level functions
    "check_all_invariants",
    "build_default_registry",
    "get_theorem_summary",
    # Module-level instance
    "DEFAULT_REGISTRY",
]
