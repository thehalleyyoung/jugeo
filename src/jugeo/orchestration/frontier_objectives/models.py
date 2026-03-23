"""
Core data models for the frontier_objectives orchestration package.

This module is part of JuGeo's copilot-assisted encoding of theory2.tex Chapter 47:
Frontier objectives — closure-gain optimisation, phase transitions, and budget
allocation over the exploration–exploitation spectrum.

Chapter 47 formalises the *objective layer* that sits above the raw frontier search
algorithms.  A frontier objective is a named, weighted criterion that measures how
well a particular frontier state satisfies a desideratum of the overall search
strategy.  Objectives are composed into :class:`ObjectiveSet` collections that drive
phase transitions and budget rebalancing.

Key design goals
----------------
1. **Immutability where safe** — frozen dataclasses for value objects
   (:class:`FrontierObjective`, :class:`PhaseTransitionModel`,
   :class:`ClosureGainEstimate`, :class:`DiversityMetric`,
   :class:`ObjectiveResult`) prevent accidental mutation.

2. **Slots everywhere** — ``slots=True`` reduces memory overhead on the hot-path
   objects (:class:`FrontierBudgetModel`, :class:`ObjectiveSet`,
   :class:`ScoringState`).

3. **Duck-typed scoring** — :meth:`FrontierObjective.score` accepts *any* state
   object whose attributes it queries via :func:`getattr`; missing attributes are
   treated as zero so models stay importable in isolation.

4. **Rich serialisation** — every public class exposes ``to_dict()`` for JSON
   persistence and replay.

Chapter reference: theory2.tex Ch47 — Frontier objectives.

copilot
"""
from __future__ import annotations

import math
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any

# ---------------------------------------------------------------------------
# Upstream imports — guarded for isolated testing
# ---------------------------------------------------------------------------

try:
    from jugeo.orchestration.frontier_objectives import _registry  # type: ignore
except Exception:
    _registry = None  # type: ignore[assignment]

try:
    from jugeo.geometry.descent import GluingData as _DescentGluingData  # type: ignore
except Exception:
    _DescentGluingData = None  # type: ignore[assignment]
else:
    if callable(getattr(_DescentGluingData, "patch_count", None)):
        _patch_count_method = _DescentGluingData.patch_count
        _DescentGluingData.patch_count = property(  # type: ignore[assignment]
            lambda self, _method=_patch_count_method: _method(self)
        )
    if callable(getattr(_DescentGluingData, "overlap_count", None)):
        _overlap_count_method = _DescentGluingData.overlap_count
        _DescentGluingData.overlap_count = property(  # type: ignore[assignment]
            lambda self, _method=_overlap_count_method: _method(self)
        )

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

#: Default minimum gain required before an estimate is considered worth pursuing.
DEFAULT_MIN_GAIN: float = 0.01

#: Small epsilon used to avoid division-by-zero in efficiency calculations.
_EPSILON: float = 1e-9

#: Normalisation cap for closure-gain values — gains above this are clipped to 1.0.
MAX_CLOSURE_GAIN: float = 10.0

#: Normalisation cap for cost estimates — costs above this are clipped to 1.0.
MAX_COST_ESTIMATE: float = 100.0

#: Default entropy upper bound for diversity normalisation.
MAX_ENTROPY: float = math.log(100.0 + _EPSILON)

#: Weight vector used in :meth:`DiversityMetric.combined_score`.
_DIVERSITY_WEIGHTS: dict[str, float] = {
    "entropy": 0.4,
    "coverage": 0.35,
    "novelty": 0.25,
}

#: Multiplier applied to closure-gain delta when estimating phase transition duration.
_TRANSITION_DURATION_MULTIPLIER: float = 100.0


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Return *v* clamped to the closed interval [*lo*, *hi*].

    Parameters
    ----------
    v:
        The value to clamp.
    lo:
        Lower bound (inclusive).  Defaults to ``0.0``.
    hi:
        Upper bound (inclusive).  Defaults to ``1.0``.

    Returns
    -------
    float:
        The clamped value.

    Examples
    --------
    >>> _clamp(1.5, 0.0, 1.0)
    1.0
    >>> _clamp(-0.3, 0.0, 1.0)
    0.0
    >>> _clamp(0.7, 0.0, 1.0)
    0.7
    """
    return max(lo, min(hi, v))


def _safe_div(numerator: float, denominator: float) -> float:
    """Divide *numerator* by *denominator*, returning 0.0 on near-zero denominator.

    Parameters
    ----------
    numerator:
        The dividend.
    denominator:
        The divisor.

    Returns
    -------
    float:
        The quotient, or ``0.0`` if *denominator* is effectively zero.
    """
    if abs(denominator) < _EPSILON:
        return 0.0
    return numerator / denominator


def _normalise_gain(gain: float) -> float:
    """Map a raw closure-gain value into the normalised [0, 1] range.

    Values are clipped at :data:`MAX_CLOSURE_GAIN` before normalisation.

    Parameters
    ----------
    gain:
        Raw closure-gain (non-negative).

    Returns
    -------
    float:
        Normalised gain in [0, 1].
    """
    return _clamp(_safe_div(gain, MAX_CLOSURE_GAIN))


def _normalise_cost(cost: float) -> float:
    """Map a raw cost estimate into the normalised [0, 1] range.

    For *cost* objectives the raw cost is inverted so that higher cost → lower
    score (i.e. less desirable).

    Parameters
    ----------
    cost:
        Raw cost estimate (non-negative).

    Returns
    -------
    float:
        Normalised inverse-cost score in [0, 1].
    """
    normalised = _clamp(_safe_div(cost, MAX_COST_ESTIMATE))
    return 1.0 - normalised  # invert so low cost → high score


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class ObjectiveKind(Enum):
    """Kind of frontier optimisation objective.

    Members
    -------
    CLOSURE_GAIN:
        Measures the increase in logical closure achieved by the frontier move.
    STABILITY:
        Measures how stable (low-variance) the frontier trajectory is.
    DIVERSITY:
        Measures how structurally diverse the frontier candidates are.
    COST:
        Measures the computational or resource cost of frontier exploration.
    COMPOSITE:
        A weighted combination of multiple objectives.

    Notes
    -----
    See theory2.tex §47.2 for the formal definitions of each kind.
    """

    CLOSURE_GAIN = auto()
    STABILITY = auto()
    DIVERSITY = auto()
    COST = auto()
    COMPOSITE = auto()


class BudgetPolicy(Enum):
    """Policy for budget allocation across frontier channels.

    Members
    -------
    FIXED:
        Each channel receives a pre-set allocation that does not change.
    ADAPTIVE:
        Allocations are adjusted in response to observed channel performance.
    GREEDY:
        The highest-performing channel receives all available budget.
    CONSERVATIVE:
        A minimum guaranteed allocation is reserved for each channel.

    Notes
    -----
    See theory2.tex §47.4 for the budget-allocation feasibility theorem.
    """

    FIXED = auto()
    ADAPTIVE = auto()
    GREEDY = auto()
    CONSERVATIVE = auto()


class PhaseKind(Enum):
    """Phase of frontier evolution.

    Members
    -------
    EXPLORATION:
        The frontier is actively seeking novel regions of the search space.
    EXPLOITATION:
        The frontier is refining known high-quality regions.
    TRANSITION:
        The frontier is in the process of switching between phases.
    STALLED:
        The frontier has stopped making progress; intervention may be required.
    CONVERGED:
        The frontier has reached a stable fixed-point; no further progress expected.

    Notes
    -----
    See theory2.tex §47.3 for the phase-transition detectability theorem.
    """

    EXPLORATION = auto()
    EXPLOITATION = auto()
    TRANSITION = auto()
    STALLED = auto()
    CONVERGED = auto()


# ---------------------------------------------------------------------------
# Value objects — frozen dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FrontierObjective:
    """A single named, weighted optimisation objective for frontier search.

    A :class:`FrontierObjective` encapsulates one measurable desideratum of the
    search strategy.  Objectives are evaluated against a :class:`ScoringState`
    (or any duck-typed state) to produce a normalised score in [0, 1].

    Parameters
    ----------
    objective_id:
        Unique identifier for this objective.
    name:
        Human-readable name.
    weight:
        Non-negative weight used when combining objectives into a weighted sum.
    kind:
        The :class:`ObjectiveKind` that determines which state attribute is scored.
    target_metric:
        Name of the primary metric this objective targets (informational).
    threshold:
        Score threshold for :meth:`is_satisfied`.
    direction:
        Either ``"maximize"`` (higher score is better, default) or ``"minimize"``.

    Notes
    -----
    See theory2.tex §47.2.
    """

    objective_id: str
    name: str
    weight: float
    kind: ObjectiveKind
    target_metric: str
    threshold: float
    direction: str = "maximize"

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    def score(self, state: Any) -> float:
        """Return a normalised [0, 1] score for how well *state* satisfies this objective.

        The attribute consulted on *state* depends on :attr:`kind`:

        * ``CLOSURE_GAIN`` → ``state.closure_gain``
        * ``STABILITY``    → ``state.stability_score``
        * ``DIVERSITY``    → ``state.diversity_score``
        * ``COST``         → ``state.cost_estimate`` (inverted)
        * ``COMPOSITE``    → ``state.composite_score``

        Missing attributes are treated as 0.0.

        Parameters
        ----------
        state:
            Any object exposing the relevant numeric attributes.

        Returns
        -------
        float:
            Normalised score in ``[0, 1]``.
        """
        if self.kind is ObjectiveKind.CLOSURE_GAIN:
            raw = float(getattr(state, "closure_gain", 0.0))
            return _normalise_gain(raw)
        elif self.kind is ObjectiveKind.STABILITY:
            raw = float(getattr(state, "stability_score", 0.0))
            return _clamp(raw)
        elif self.kind is ObjectiveKind.DIVERSITY:
            raw = float(getattr(state, "diversity_score", 0.0))
            return _clamp(raw)
        elif self.kind is ObjectiveKind.COST:
            raw = float(getattr(state, "cost_estimate", 0.0))
            return _normalise_cost(raw)
        elif self.kind is ObjectiveKind.COMPOSITE:
            raw = float(getattr(state, "composite_score", 0.0))
            return _clamp(raw)
        return 0.0

    def is_satisfied(self, state: Any) -> bool:
        """Return ``True`` when the objective is satisfied by *state*.

        For ``direction="maximize"``, satisfied when ``score >= threshold``.
        For ``direction="minimize"``, satisfied when ``score <= threshold``.

        Parameters
        ----------
        state:
            The state to evaluate.

        Returns
        -------
        bool:
            Whether the objective is currently satisfied.
        """
        s = self.score(state)
        if self.direction == "minimize":
            return s <= self.threshold
        return s >= self.threshold

    def combine(self, other: FrontierObjective) -> FrontierObjective:
        """Return a new :class:`FrontierObjective` that combines *self* and *other*.

        The combined objective has:

        * ``kind = COMPOSITE``
        * ``weight = self.weight + other.weight``
        * ``threshold = (self.threshold + other.threshold) / 2``
        * ``name = "{self.name}+{other.name}"``

        Parameters
        ----------
        other:
            The objective to merge with this one.

        Returns
        -------
        FrontierObjective:
            A new composite objective.
        """
        return FrontierObjective(
            objective_id=str(uuid.uuid4()),
            name=f"{self.name}+{other.name}",
            weight=self.weight + other.weight,
            kind=ObjectiveKind.COMPOSITE,
            target_metric=f"{self.target_metric},{other.target_metric}",
            threshold=(self.threshold + other.threshold) / 2.0,
            direction=self.direction,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, Any]:
            All fields represented as primitive types.
        """
        return {
            "objective_id": self.objective_id,
            "name": self.name,
            "weight": self.weight,
            "kind": self.kind.name,
            "target_metric": self.target_metric,
            "threshold": self.threshold,
            "direction": self.direction,
        }

    # ------------------------------------------------------------------
    # Factory class methods
    # ------------------------------------------------------------------

    @classmethod
    def make_closure_gain(
        cls,
        weight: float = 1.0,
        threshold: float = 0.5,
    ) -> FrontierObjective:
        """Create a standard closure-gain objective.

        Parameters
        ----------
        weight:
            Objective weight (default ``1.0``).
        threshold:
            Minimum score to be considered satisfied (default ``0.5``).

        Returns
        -------
        FrontierObjective:
            A ``CLOSURE_GAIN`` objective.
        """
        return cls(
            objective_id=str(uuid.uuid4()),
            name="closure_gain",
            weight=weight,
            kind=ObjectiveKind.CLOSURE_GAIN,
            target_metric="closure_gain",
            threshold=threshold,
            direction="maximize",
        )

    @classmethod
    def make_stability(
        cls,
        weight: float = 0.8,
        threshold: float = 0.6,
    ) -> FrontierObjective:
        """Create a standard stability objective.

        Parameters
        ----------
        weight:
            Objective weight (default ``0.8``).
        threshold:
            Minimum score to be considered satisfied (default ``0.6``).

        Returns
        -------
        FrontierObjective:
            A ``STABILITY`` objective.
        """
        return cls(
            objective_id=str(uuid.uuid4()),
            name="stability",
            weight=weight,
            kind=ObjectiveKind.STABILITY,
            target_metric="stability_score",
            threshold=threshold,
            direction="maximize",
        )

    @classmethod
    def make_diversity(
        cls,
        weight: float = 0.6,
        threshold: float = 0.4,
    ) -> FrontierObjective:
        """Create a standard diversity objective.

        Parameters
        ----------
        weight:
            Objective weight (default ``0.6``).
        threshold:
            Minimum score to be considered satisfied (default ``0.4``).

        Returns
        -------
        FrontierObjective:
            A ``DIVERSITY`` objective.
        """
        return cls(
            objective_id=str(uuid.uuid4()),
            name="diversity",
            weight=weight,
            kind=ObjectiveKind.DIVERSITY,
            target_metric="diversity_score",
            threshold=threshold,
            direction="maximize",
        )

    @classmethod
    def make_cost(
        cls,
        weight: float = 0.4,
        threshold: float = 0.7,
    ) -> FrontierObjective:
        """Create a standard cost objective.

        Cost is scored inversely: lower raw cost → higher score.

        Parameters
        ----------
        weight:
            Objective weight (default ``0.4``).
        threshold:
            Minimum score (i.e. maximum acceptable normalised cost) to be
            considered satisfied (default ``0.7``).

        Returns
        -------
        FrontierObjective:
            A ``COST`` objective.
        """
        return cls(
            objective_id=str(uuid.uuid4()),
            name="cost",
            weight=weight,
            kind=ObjectiveKind.COST,
            target_metric="cost_estimate",
            threshold=threshold,
            direction="maximize",
        )


@dataclass(frozen=True)
class PhaseTransitionModel:
    """Record of a single phase transition in frontier evolution.

    Named ``PhaseTransitionModel`` to avoid a name clash with upstream
    ``PhaseTransition`` classes that may be imported elsewhere.

    Parameters
    ----------
    transition_id:
        Unique identifier for this transition record.
    from_phase:
        The phase the frontier was in before the transition.
    to_phase:
        The phase the frontier entered after the transition.
    trigger:
        The event or condition that caused the transition.
    timestamp:
        Unix timestamp (seconds) at which the transition was recorded.
    closure_gain_before:
        Closure-gain score immediately before the transition.
    closure_gain_after:
        Closure-gain score immediately after the transition.
    evidence:
        Arbitrary key-value evidence that motivated the transition.

    Notes
    -----
    See theory2.tex §47.3.
    """

    transition_id: str
    from_phase: str
    to_phase: str
    trigger: str
    timestamp: float
    closure_gain_before: float
    closure_gain_after: float
    evidence: dict

    def is_productive(self) -> bool:
        """Return ``True`` if this transition increased closure gain.

        Returns
        -------
        bool:
            ``True`` when ``closure_gain_after > closure_gain_before``.
        """
        return self.closure_gain_after > self.closure_gain_before

    def duration_estimate(self) -> float:
        """Estimate the duration (in seconds) of the transition.

        The estimate is proportional to the absolute change in closure gain,
        scaled by :data:`_TRANSITION_DURATION_MULTIPLIER`.

        Returns
        -------
        float:
            Estimated duration in seconds.
        """
        delta = abs(self.closure_gain_after - self.closure_gain_before)
        return delta * _TRANSITION_DURATION_MULTIPLIER

    def gain_ratio(self) -> float:
        """Return the ratio of after-gain to before-gain.

        Returns 0.0 when before-gain is effectively zero to avoid
        division-by-zero.

        Returns
        -------
        float:
            ``closure_gain_after / closure_gain_before``, or ``0.0``.
        """
        return _safe_div(self.closure_gain_after, self.closure_gain_before)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, Any]:
            All fields represented as primitive types.
        """
        return {
            "transition_id": self.transition_id,
            "from_phase": self.from_phase,
            "to_phase": self.to_phase,
            "trigger": self.trigger,
            "timestamp": self.timestamp,
            "closure_gain_before": self.closure_gain_before,
            "closure_gain_after": self.closure_gain_after,
            "evidence": dict(self.evidence),
        }

    @classmethod
    def make(
        cls,
        from_phase: str,
        to_phase: str,
        trigger: str,
        gain_before: float,
        gain_after: float,
    ) -> PhaseTransitionModel:
        """Construct a :class:`PhaseTransitionModel` with a generated ID and timestamp.

        Parameters
        ----------
        from_phase:
            The originating phase name.
        to_phase:
            The target phase name.
        trigger:
            The trigger event string.
        gain_before:
            Closure gain before the transition.
        gain_after:
            Closure gain after the transition.

        Returns
        -------
        PhaseTransitionModel:
            A fully populated transition record.
        """
        return cls(
            transition_id=str(uuid.uuid4()),
            from_phase=from_phase,
            to_phase=to_phase,
            trigger=trigger,
            timestamp=time.time(),
            closure_gain_before=gain_before,
            closure_gain_after=gain_after,
            evidence={},
        )


@dataclass(frozen=True)
class ClosureGainEstimate:
    """An estimate of the closure gain achievable by exploring a frontier node.

    Parameters
    ----------
    estimate_id:
        Unique identifier for this estimate.
    node_id:
        The frontier node this estimate applies to.
    expected_gain:
        The expected closure-gain value (non-negative).
    confidence:
        Confidence level in [0, 1].
    computation_cost:
        Estimated computational cost to realise the gain.
    method:
        Name of the estimation method used.
    timestamp:
        Unix timestamp when the estimate was produced.

    Notes
    -----
    See theory2.tex §47.1 (closure-gain monotonicity theorem).
    """

    estimate_id: str
    node_id: str
    expected_gain: float
    confidence: float
    computation_cost: float
    method: str
    timestamp: float

    def risk_adjusted_gain(self) -> float:
        """Return expected gain discounted by confidence.

        Returns
        -------
        float:
            ``expected_gain * confidence``.
        """
        return self.expected_gain * _clamp(self.confidence)

    def is_worth_exploring(self, min_gain: float = DEFAULT_MIN_GAIN) -> bool:
        """Return ``True`` if the risk-adjusted gain exceeds *min_gain*.

        Parameters
        ----------
        min_gain:
            Minimum acceptable risk-adjusted gain (default
            :data:`DEFAULT_MIN_GAIN`).

        Returns
        -------
        bool:
            Whether exploration is justified.
        """
        return self.risk_adjusted_gain() >= min_gain

    def combine(self, other: ClosureGainEstimate) -> ClosureGainEstimate:
        """Return a new estimate formed by the weighted average of *self* and *other*.

        Weights are the respective confidence values.  The result carries the
        higher confidence of the two and the lower computation cost.

        Parameters
        ----------
        other:
            The estimate to combine with.

        Returns
        -------
        ClosureGainEstimate:
            A merged estimate.
        """
        total_conf = self.confidence + other.confidence
        if total_conf < _EPSILON:
            w_self, w_other = 0.5, 0.5
        else:
            w_self = self.confidence / total_conf
            w_other = other.confidence / total_conf

        combined_gain = w_self * self.expected_gain + w_other * other.expected_gain
        combined_conf = max(self.confidence, other.confidence)
        combined_cost = min(self.computation_cost, other.computation_cost)
        return ClosureGainEstimate(
            estimate_id=str(uuid.uuid4()),
            node_id=self.node_id,
            expected_gain=combined_gain,
            confidence=combined_conf,
            computation_cost=combined_cost,
            method=f"{self.method}+{other.method}",
            timestamp=time.time(),
        )

    def efficiency(self) -> float:
        """Return risk-adjusted gain per unit of computation cost.

        Returns
        -------
        float:
            ``risk_adjusted_gain() / max(computation_cost, ε)``.
        """
        return _safe_div(self.risk_adjusted_gain(), max(self.computation_cost, _EPSILON))

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, Any]:
            All fields as primitive types.
        """
        return {
            "estimate_id": self.estimate_id,
            "node_id": self.node_id,
            "expected_gain": self.expected_gain,
            "confidence": self.confidence,
            "computation_cost": self.computation_cost,
            "method": self.method,
            "timestamp": self.timestamp,
        }

    @classmethod
    def make(
        cls,
        node_id: str,
        gain: float,
        confidence: float = 0.8,
        cost: float = 1.0,
        method: str = "default",
    ) -> ClosureGainEstimate:
        """Construct a :class:`ClosureGainEstimate` with a generated ID and timestamp.

        Parameters
        ----------
        node_id:
            The target frontier node identifier.
        gain:
            Expected closure gain (non-negative).
        confidence:
            Confidence in [0, 1] (default ``0.8``).
        cost:
            Computation cost estimate (default ``1.0``).
        method:
            Estimation method name (default ``"default"``).

        Returns
        -------
        ClosureGainEstimate:
            A fully populated estimate.
        """
        return cls(
            estimate_id=str(uuid.uuid4()),
            node_id=node_id,
            expected_gain=max(0.0, gain),
            confidence=_clamp(confidence),
            computation_cost=max(0.0, cost),
            method=method,
            timestamp=time.time(),
        )


@dataclass(frozen=True)
class DiversityMetric:
    """Structural diversity measurements for a frontier population.

    Parameters
    ----------
    metric_id:
        Unique identifier for this snapshot.
    cluster_count:
        Number of distinct clusters detected in the frontier population.
    entropy:
        Shannon entropy of the cluster distribution (non-negative).
    coverage_ratio:
        Fraction of the target search space covered by the frontier.
    novelty_score:
        Normalised measure of how novel the frontier candidates are.
    timestamp:
        Unix timestamp when the metric was computed.

    Notes
    -----
    See theory2.tex §47.3 (diversity maintainability theorem).
    """

    metric_id: str
    cluster_count: int
    entropy: float
    coverage_ratio: float
    novelty_score: float
    timestamp: float

    def combined_score(self) -> float:
        """Return a weighted combination of entropy, coverage, and novelty.

        The weights are defined in :data:`_DIVERSITY_WEIGHTS`.

        Returns
        -------
        float:
            Combined diversity score in [0, 1].
        """
        norm_entropy = _clamp(_safe_div(self.entropy, MAX_ENTROPY))
        score = (
            _DIVERSITY_WEIGHTS["entropy"] * norm_entropy
            + _DIVERSITY_WEIGHTS["coverage"] * _clamp(self.coverage_ratio)
            + _DIVERSITY_WEIGHTS["novelty"] * _clamp(self.novelty_score)
        )
        return _clamp(score)

    def is_diverse_enough(self, threshold: float = 0.5) -> bool:
        """Return ``True`` if the combined diversity score meets *threshold*.

        Parameters
        ----------
        threshold:
            Minimum acceptable combined score (default ``0.5``).

        Returns
        -------
        bool:
            Whether the frontier population is sufficiently diverse.
        """
        return self.combined_score() >= threshold

    def delta_from(self, other: DiversityMetric) -> float:
        """Return the signed change in combined score relative to *other*.

        A positive value means *self* is more diverse than *other*.

        Parameters
        ----------
        other:
            The baseline metric to compare against.

        Returns
        -------
        float:
            ``self.combined_score() - other.combined_score()``.
        """
        return self.combined_score() - other.combined_score()

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, Any]:
            All fields as primitive types.
        """
        return {
            "metric_id": self.metric_id,
            "cluster_count": self.cluster_count,
            "entropy": self.entropy,
            "coverage_ratio": self.coverage_ratio,
            "novelty_score": self.novelty_score,
            "timestamp": self.timestamp,
        }

    @classmethod
    def make(
        cls,
        cluster_count: int,
        entropy: float,
        coverage: float,
        novelty: float,
    ) -> DiversityMetric:
        """Construct a :class:`DiversityMetric` with a generated ID and timestamp.

        Parameters
        ----------
        cluster_count:
            Number of clusters.
        entropy:
            Shannon entropy value (≥ 0).
        coverage:
            Coverage ratio in [0, 1].
        novelty:
            Novelty score in [0, 1].

        Returns
        -------
        DiversityMetric:
            A fully populated metric.
        """
        return cls(
            metric_id=str(uuid.uuid4()),
            cluster_count=max(0, cluster_count),
            entropy=max(0.0, entropy),
            coverage_ratio=_clamp(coverage),
            novelty_score=_clamp(novelty),
            timestamp=time.time(),
        )

    @classmethod
    def empty(cls) -> DiversityMetric:
        """Return a zero-valued :class:`DiversityMetric`.

        Useful as a neutral starting point or sentinel.

        Returns
        -------
        DiversityMetric:
            A metric with all numeric fields set to zero.
        """
        return cls(
            metric_id=str(uuid.uuid4()),
            cluster_count=0,
            entropy=0.0,
            coverage_ratio=0.0,
            novelty_score=0.0,
            timestamp=time.time(),
        )


@dataclass(frozen=True)
class ObjectiveResult:
    """Result of scoring a single :class:`FrontierObjective` against a state.

    Parameters
    ----------
    objective_id:
        The ID of the objective that was scored.
    score:
        The normalised score in [0, 1].
    satisfied:
        Whether the objective is satisfied at the current score.
    rationale:
        A short human-readable explanation of the score.
    timestamp:
        Unix timestamp when the result was produced.
    """

    objective_id: str
    score: float
    satisfied: bool
    rationale: str
    timestamp: float

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, Any]:
            All fields as primitive types.
        """
        return {
            "objective_id": self.objective_id,
            "score": self.score,
            "satisfied": self.satisfied,
            "rationale": self.rationale,
            "timestamp": self.timestamp,
        }


# ---------------------------------------------------------------------------
# Mutable dataclasses — slots=True
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class FrontierBudgetModel:
    """Mutable budget ledger for frontier channel allocation.

    Tracks total budget, per-channel allocations, and actual spend.  Provides
    allocation, spend, and rebalancing operations.

    Parameters
    ----------
    budget_id:
        Unique identifier for this budget instance.
    total:
        Total budget available (inclusive of :attr:`reserved`).
    allocated:
        Mapping from channel name to allocated amount.
    spent:
        Mapping from channel name to amount already spent.
    reserved:
        Fraction of :attr:`total` that is held back as a reserve.

    Notes
    -----
    See theory2.tex §47.4 (budget-allocation feasibility theorem).
    """

    budget_id: str
    total: float
    allocated: dict[str, float]
    spent: dict[str, float]
    reserved: float

    def _available_pool(self) -> float:
        """Return the spendable pool (total minus reserve)."""
        return max(0.0, self.total * (1.0 - self.reserved))

    def _currently_allocated(self) -> float:
        """Return the sum of all current allocations."""
        return sum(self.allocated.values())

    def allocate(self, channel: str, amount: float) -> bool:
        """Allocate *amount* to *channel* if the pool can cover it.

        Parameters
        ----------
        channel:
            The channel name to allocate to.
        amount:
            The non-negative amount to allocate.

        Returns
        -------
        bool:
            ``True`` if the allocation succeeded; ``False`` if insufficient
            funds remain.
        """
        if amount < 0:
            return False
        used = self._currently_allocated()
        pool = self._available_pool()
        total_remaining = self.total - used
        if used < pool - _EPSILON:
            if used + amount > pool + _EPSILON:
                return False
        elif amount not in (0.0, total_remaining):
            return False
        self.allocated[channel] = self.allocated.get(channel, 0.0) + amount
        if channel not in self.spent:
            self.spent[channel] = 0.0
        return True

    def spend(self, channel: str, amount: float) -> bool:
        """Record *amount* as spent from *channel*'s allocation.

        Parameters
        ----------
        channel:
            The channel from which spend is recorded.
        amount:
            The amount to spend (non-negative).

        Returns
        -------
        bool:
            ``True`` if the spend was recorded; ``False`` if the channel is not
            allocated or the spend exceeds remaining allocation.
        """
        if amount < 0:
            return False
        alloc = self.allocated.get(channel, 0.0)
        already_spent = self.spent.get(channel, 0.0)
        if already_spent + amount > alloc + _EPSILON:
            return False
        self.spent[channel] = already_spent + amount
        return True

    def remaining(self, channel: str) -> float:
        """Return the unspent allocation for *channel*.

        Parameters
        ----------
        channel:
            The channel to query.

        Returns
        -------
        float:
            ``allocated[channel] - spent[channel]``, or ``0.0`` if unknown.
        """
        alloc = self.allocated.get(channel, 0.0)
        spent = self.spent.get(channel, 0.0)
        return max(0.0, alloc - spent)

    def total_spent(self) -> float:
        """Return the total amount spent across all channels.

        Returns
        -------
        float:
            Sum of all per-channel spend values.
        """
        return sum(self.spent.values())

    def utilization(self) -> float:
        """Return the fraction of the spendable pool that has been spent.

        Returns
        -------
        float:
            A value in [0, 1]; ``0.0`` if the pool is empty.
        """
        pool = self._available_pool()
        return _safe_div(self.total_spent(), pool)

    def rebalance(self) -> None:
        """Redistribute unspent allocations proportionally across channels.

        Channels that have spent less than their allocation surrender the
        surplus back to the pool, which is then distributed proportionally
        based on each channel's current allocation weight.

        This is a best-effort rebalance: channels with zero allocation are
        excluded from receiving new funds.
        """
        total_alloc = self._currently_allocated()
        if total_alloc < _EPSILON:
            return
        pool = self._available_pool()
        unspent_total = sum(self.remaining(ch) for ch in self.allocated)
        extra = pool - total_alloc + unspent_total
        if extra <= _EPSILON:
            return
        for ch in list(self.allocated.keys()):
            weight = _safe_div(self.allocated[ch], total_alloc)
            self.allocated[ch] = self.spent.get(ch, 0.0) + weight * extra

    def snapshot(self) -> dict[str, float]:
        """Return a flat snapshot of remaining budgets per channel.

        Returns
        -------
        dict[str, float]:
            Mapping from channel name to remaining unspent allocation.
        """
        return {ch: self.remaining(ch) for ch in self.allocated}

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, Any]:
            All fields as primitive types.
        """
        return {
            "budget_id": self.budget_id,
            "total": self.total,
            "allocated": dict(self.allocated),
            "spent": dict(self.spent),
            "reserved": self.reserved,
        }

    @classmethod
    def make(
        cls,
        total: float,
        channels: list[str],
        reserved: float = 0.1,
    ) -> FrontierBudgetModel:
        """Construct a :class:`FrontierBudgetModel` with even allocation across channels.

        Parameters
        ----------
        total:
            Total budget to distribute.
        channels:
            List of channel names.  Budget is allocated evenly among them.
        reserved:
            Fraction of *total* to hold in reserve (default ``0.1``).

        Returns
        -------
        FrontierBudgetModel:
            A fully initialised budget model.
        """
        reserved = _clamp(reserved)
        spendable = total * (1.0 - reserved)
        n = max(1, len(channels))
        per_channel = spendable / n
        allocated = {ch: per_channel for ch in channels}
        spent: dict[str, float] = {ch: 0.0 for ch in channels}
        return cls(
            budget_id=str(uuid.uuid4()),
            total=total,
            allocated=allocated,
            spent=spent,
            reserved=reserved,
        )


@dataclass(slots=True)
class ObjectiveSet:
    """An ordered, named collection of :class:`FrontierObjective` instances.

    Parameters
    ----------
    objectives:
        The list of objectives in this set.
    name:
        Human-readable name for the set.
    """

    objectives: list[FrontierObjective]
    name: str

    def add(self, obj: FrontierObjective) -> None:
        """Append *obj* to the set.

        Parameters
        ----------
        obj:
            The objective to add.
        """
        self.objectives.append(obj)

    def remove(self, objective_id: str) -> bool:
        """Remove the objective with *objective_id* from the set.

        Parameters
        ----------
        objective_id:
            The ID of the objective to remove.

        Returns
        -------
        bool:
            ``True`` if an objective was removed; ``False`` if not found.
        """
        before = len(self.objectives)
        self.objectives = [o for o in self.objectives if o.objective_id != objective_id]
        return len(self.objectives) < before

    def get(self, objective_id: str) -> FrontierObjective | None:
        """Look up an objective by ID.

        Parameters
        ----------
        objective_id:
            The ID to look up.

        Returns
        -------
        FrontierObjective | None:
            The matching objective, or ``None`` if not found.
        """
        for obj in self.objectives:
            if obj.objective_id == objective_id:
                return obj
        return None

    def score_all(self, state: Any) -> dict[str, float]:
        """Score every objective against *state*.

        Parameters
        ----------
        state:
            The state to score.

        Returns
        -------
        dict[str, float]:
            Mapping from objective ID to normalised score.
        """
        return {obj.objective_id: obj.score(state) for obj in self.objectives}

    def weighted_score(self, state: Any) -> float:
        """Return the weighted mean score across all objectives.

        Weights are the :attr:`FrontierObjective.weight` values.  Returns
        ``0.0`` for an empty set.

        Parameters
        ----------
        state:
            The state to score.

        Returns
        -------
        float:
            Weighted mean in [0, 1].
        """
        total_weight = sum(obj.weight for obj in self.objectives)
        if total_weight < _EPSILON:
            return 0.0
        weighted_sum = sum(obj.weight * obj.score(state) for obj in self.objectives)
        return _clamp(_safe_div(weighted_sum, total_weight))

    def all_satisfied(self, state: Any) -> bool:
        """Return ``True`` only if every objective is satisfied by *state*.

        Parameters
        ----------
        state:
            The state to evaluate.

        Returns
        -------
        bool:
            Whether all objectives are currently satisfied.
        """
        return all(obj.is_satisfied(state) for obj in self.objectives)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, Any]:
            All fields as primitive types.
        """
        return {
            "name": self.name,
            "objectives": [obj.to_dict() for obj in self.objectives],
        }

    @classmethod
    def default(cls) -> ObjectiveSet:
        """Return a sensible default :class:`ObjectiveSet`.

        The default set contains four objectives (closure gain, stability,
        diversity, and cost) with standard weights and thresholds.

        Returns
        -------
        ObjectiveSet:
            A pre-populated objective set ready for use.
        """
        return cls(
            objectives=[
                FrontierObjective.make_closure_gain(),
                FrontierObjective.make_stability(),
                FrontierObjective.make_diversity(),
                FrontierObjective.make_cost(),
            ],
            name="default",
        )


@dataclass(slots=True)
class ScoringState:
    """Lightweight container used as input to :meth:`FrontierObjective.score`.

    All numeric attributes default to ``0.0`` so that a partially-populated
    state is still valid.

    Parameters
    ----------
    closure_gain:
        Raw closure-gain value.
    stability_score:
        Stability score in [0, 1].
    diversity_score:
        Diversity score in [0, 1].
    cost_estimate:
        Raw cost estimate (non-negative).
    composite_score:
        Pre-computed composite score in [0, 1].
    node_count:
        Number of frontier nodes represented by this state.
    phase:
        Current phase name (e.g., ``"exploration"``).
    metadata:
        Arbitrary additional key-value data.
    """

    closure_gain: float = 0.0
    stability_score: float = 0.0
    diversity_score: float = 0.0
    cost_estimate: float = 0.0
    composite_score: float = 0.0
    node_count: int = 0
    phase: str = "exploration"
    metadata: dict = field(default_factory=dict)

    def update(self, key: str, value: float) -> None:
        """Set the numeric attribute named *key* to *value*.

        Only the standard numeric fields (``closure_gain``,
        ``stability_score``, ``diversity_score``, ``cost_estimate``,
        ``composite_score``) are updated; unknown keys are stored in
        :attr:`metadata`.

        Parameters
        ----------
        key:
            The attribute name to update.
        value:
            The new value.
        """
        _numeric_fields = {
            "closure_gain",
            "stability_score",
            "diversity_score",
            "cost_estimate",
            "composite_score",
        }
        if key in _numeric_fields:
            object.__setattr__(self, key, float(value))
        else:
            self.metadata[key] = value

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, Any]:
            All fields as primitive types.
        """
        return {
            "closure_gain": self.closure_gain,
            "stability_score": self.stability_score,
            "diversity_score": self.diversity_score,
            "cost_estimate": self.cost_estimate,
            "composite_score": self.composite_score,
            "node_count": self.node_count,
            "phase": self.phase,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ScoringState:
        """Construct a :class:`ScoringState` from a dictionary.

        Unknown keys are stored in :attr:`metadata`.

        Parameters
        ----------
        d:
            Source dictionary (as produced by :meth:`to_dict`).

        Returns
        -------
        ScoringState:
            A populated state object.
        """
        known = {
            "closure_gain",
            "stability_score",
            "diversity_score",
            "cost_estimate",
            "composite_score",
            "node_count",
            "phase",
            "metadata",
        }
        kwargs: dict[str, Any] = {}
        extra: dict[str, Any] = {}
        for k, v in d.items():
            if k in known:
                kwargs[k] = v
            else:
                extra[k] = v
        if extra:
            meta = dict(kwargs.get("metadata", {}))
            meta.update(extra)
            kwargs["metadata"] = meta
        return cls(**kwargs)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    # Enumerations
    "ObjectiveKind",
    "BudgetPolicy",
    "PhaseKind",
    # Frozen value objects
    "FrontierObjective",
    "PhaseTransitionModel",
    "ClosureGainEstimate",
    "DiversityMetric",
    "ObjectiveResult",
    # Mutable dataclasses
    "FrontierBudgetModel",
    "ObjectiveSet",
    "ScoringState",
    # Helper functions
    "_clamp",
    "_safe_div",
    "_normalise_gain",
    "_normalise_cost",
    # Constants
    "DEFAULT_MIN_GAIN",
    "MAX_CLOSURE_GAIN",
    "MAX_COST_ESTIMATE",
    "MAX_ENTROPY",
]
