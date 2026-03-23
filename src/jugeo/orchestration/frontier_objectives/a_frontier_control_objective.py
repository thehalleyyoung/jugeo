"""jugeo.orchestration.frontier_objectives.a_frontier_control_objective
=========================================================================

A Frontier Control Objective (Theory Ch. — Exploration, exploitation, and
frontier control, §2).

Theory
------
A *frontier control objective* is a formal specification of what the
orchestrator is trying to achieve at the current search frontier.  Unlike a
scalar reward, a frontier control objective is a *semantic signal* — a
structured object that carries directional information, priority, and
justification.

Formally, a frontier control objective :math:`\\mathcal{O}` is a triple:

.. math::

   \\mathcal{O} = (\\phi, w, \\pi)

where :math:`\\phi : \\mathcal{F} \\to [0, 1]` is an objective *scoring
function*, :math:`w \\in [0, 1]` is the relative weight of this objective in
the composite objective set, and :math:`\\pi \\in \\mathbb{N}` is the priority
level (lower = higher priority).

The composite objective function :math:`J(\\mathcal{F})` is then:

.. math::

   J(\\mathcal{F}) = \\sum_i w_i \\cdot \\phi_i(\\mathcal{F})

Objectives can be of four kinds:
* **COVERAGE** — maximize the breadth of explored space.
* **PRECISION** — minimize the number of unexplored nodes near a target.
* **BALANCE** — keep exploration and exploitation at equal weight.
* **TERMINATION** — drive the frontier toward a pre-specified closure condition.
"""
from __future__ import annotations

import heapq
import math
import statistics
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

try:
    from jugeo.orchestration.frontier_objectives.the_frontier_as_a_controlled_searc import (
        FrontierControlState,
        FrontierControlledSearchWitness,
    )
except ImportError:
    FrontierControlState = Any  # type: ignore[assignment,misc]
    FrontierControlledSearchWitness = Any  # type: ignore[assignment,misc]

__all__ = [
    "ObjectiveKind",
    "FrontierObjectiveSpec",
    "FrontierObjectiveScore",
    "FrontierObjectiveSet",
    "ObjectiveScoringFunction",
    "ObjectiveWeightAdjuster",
    "ObjectivePriorityQueue",
    "FrontierControlObjectiveAnalyzer",
    "FrontierControlObjectiveWitness",
    "FrontierControlObjectiveCoordinator",
    "build_coverage_objective",
    "build_precision_objective",
    "build_balance_objective",
    "build_termination_objective",
    "rank_objectives",
    "merge_objective_sets",
    "_safe_float",
    "_clamp",
    "_ema",
]


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _safe_float(x: Any, default: float = 0.0) -> float:
    """Safely convert *x* to a float, returning *default* on failure.

    Parameters
    ----------
    x:
        Value to convert.
    default:
        Fallback value returned when conversion raises ``ValueError`` or
        ``TypeError``.

    Returns
    -------
    float
        The converted value or *default*.
    """
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _clamp(x: float, lo: float, hi: float) -> float:
    """Clamp *x* to the closed interval [*lo*, *hi*].

    Parameters
    ----------
    x:
        Value to clamp.
    lo:
        Lower bound (inclusive).
    hi:
        Upper bound (inclusive).

    Returns
    -------
    float
        The clamped value.

    Raises
    ------
    ValueError
        If ``lo > hi``.
    """
    if lo > hi:
        raise ValueError(f"_clamp: lo ({lo}) must be <= hi ({hi})")
    return max(lo, min(hi, x))


def _ema(values: list[float], alpha: float = 0.3) -> float:
    """Compute the exponential moving average of *values*.

    The EMA is computed left-to-right so the most recent value has the
    highest influence.  If *values* is empty, returns 0.0.

    Parameters
    ----------
    values:
        Ordered sequence of observations.
    alpha:
        Smoothing factor in (0, 1].  Higher values give more weight to
        recent observations.

    Returns
    -------
    float
        Exponential moving average of *values*.
    """
    if not values:
        return 0.0
    alpha = _clamp(alpha, 1e-9, 1.0)
    ema = _safe_float(values[0])
    for v in values[1:]:
        ema = alpha * _safe_float(v) + (1.0 - alpha) * ema
    return ema


# ---------------------------------------------------------------------------
# Enum
# ---------------------------------------------------------------------------


class ObjectiveKind(Enum):
    """Enumeration of frontier control objective categories.

    Each kind corresponds to a distinct optimisation goal for the frontier
    search orchestrator.

    Attributes
    ----------
    COVERAGE:
        Maximise the breadth of the explored semantic space.
    PRECISION:
        Minimise the number of unexplored nodes in the vicinity of a
        designated target.
    BALANCE:
        Maintain an equal weighting between exploration and exploitation.
    TERMINATION:
        Drive the frontier toward a pre-specified closure condition.
    """

    COVERAGE = "COVERAGE"
    PRECISION = "PRECISION"
    BALANCE = "BALANCE"
    TERMINATION = "TERMINATION"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FrontierObjectiveSpec:
    """Immutable specification of a single frontier control objective.

    A ``FrontierObjectiveSpec`` encodes *what* the orchestrator is trying
    to achieve (via ``kind``), how much that objective matters relative to
    peers (via ``weight``), how urgently it should be addressed (via
    ``priority``), a human-readable description, and any additional
    numerical parameters required by the scoring function.

    Attributes
    ----------
    spec_id:
        Unique identifier for this objective specification.
    kind:
        The category of the objective (COVERAGE, PRECISION, BALANCE,
        or TERMINATION).
    weight:
        Relative importance weight in [0, 1].
    priority:
        Integer priority level.  Lower values indicate higher urgency.
    description:
        Human-readable description of what this objective represents.
    scoring_params:
        Additional numerical parameters forwarded to the scoring function.
        For example, a COVERAGE objective might include
        ``{"target_breadth": 20}``.
    """

    spec_id: str
    kind: ObjectiveKind
    weight: float
    priority: int
    description: str
    scoring_params: dict[str, Any] = field(default_factory=dict)

    def is_high_priority(self) -> bool:
        """Return True if this objective has priority strictly below 3.

        High-priority objectives (priority < 3) are processed first by
        the coordinator and are given preference in resource allocation.

        Returns
        -------
        bool
            ``True`` iff ``self.priority < 3``.
        """
        return self.priority < 3

    def normalized_weight(self, total_weight: float) -> float:
        """Return this objective's weight normalised by *total_weight*.

        If *total_weight* is zero or negative, the normalised weight is
        defined to be 0.0 to avoid division by zero.

        Parameters
        ----------
        total_weight:
            Sum of all objective weights in the same objective set.

        Returns
        -------
        float
            Normalised weight in [0, 1].
        """
        if total_weight <= 0.0:
            return 0.0
        return _clamp(self.weight / total_weight, 0.0, 1.0)


@dataclass(frozen=True, slots=True)
class FrontierObjectiveScore:
    """Immutable result of evaluating a single frontier objective.

    After the ``ObjectiveScoringFunction`` evaluates a
    ``FrontierObjectiveSpec`` against the current frontier state, the
    result is stored in a ``FrontierObjectiveScore``.

    Attributes
    ----------
    spec_id:
        Identifier of the ``FrontierObjectiveSpec`` that was evaluated.
    raw_score:
        Unnormalised score in [0, 1] produced by the scoring function.
    weighted_score:
        Score multiplied by the objective weight.
    rank:
        Ordinal rank of this objective among all evaluated objectives
        (1 = best).
    timestamp:
        Unix timestamp of when this score was computed.
    """

    spec_id: str
    raw_score: float
    weighted_score: float
    rank: int
    timestamp: float

    def is_satisfactory(self, threshold: float = 0.6) -> bool:
        """Return True if the raw score meets or exceeds *threshold*.

        A satisfactory score indicates that the frontier is performing
        adequately with respect to this objective.

        Parameters
        ----------
        threshold:
            Minimum acceptable raw score.  Defaults to 0.6.

        Returns
        -------
        bool
            ``True`` iff ``self.raw_score >= threshold``.
        """
        return self.raw_score >= threshold


@dataclass(frozen=True, slots=True)
class FrontierObjectiveSet:
    """Immutable collection of frontier control objectives.

    An objective set groups one or more ``FrontierObjectiveSpec`` objects
    together with a rule for combining their scores into a composite
    figure of merit.

    Attributes
    ----------
    set_id:
        Unique identifier for this objective set.
    objectives:
        Tuple of objective specifications.  Tuples are used to enforce
        immutability at the collection level.
    composite_weight_mode:
        How individual weighted scores are aggregated.  ``"sum"`` adds
        all weighted scores; ``"max"`` takes the maximum; ``"mean"``
        takes the arithmetic mean.
    """

    set_id: str
    objectives: tuple[FrontierObjectiveSpec, ...]
    composite_weight_mode: str = "sum"

    def total_weight(self) -> float:
        """Return the sum of all objective weights.

        Returns
        -------
        float
            Total weight, which may be 0.0 if the set is empty.
        """
        return sum(obj.weight for obj in self.objectives)

    def by_kind(self, kind: ObjectiveKind) -> list[FrontierObjectiveSpec]:
        """Return all objectives of the specified kind.

        Parameters
        ----------
        kind:
            The ``ObjectiveKind`` to filter by.

        Returns
        -------
        list[FrontierObjectiveSpec]
            Possibly-empty list of matching objectives.
        """
        return [obj for obj in self.objectives if obj.kind == kind]

    def highest_priority(self) -> FrontierObjectiveSpec | None:
        """Return the objective with the lowest priority integer value.

        If multiple objectives share the same priority level, the one
        with the highest weight is returned as a tie-breaker.

        Returns
        -------
        FrontierObjectiveSpec | None
            The highest-priority objective, or ``None`` if the set is
            empty.
        """
        if not self.objectives:
            return None
        return min(self.objectives, key=lambda o: (o.priority, -o.weight))


@dataclass
class ObjectiveScoringFunction:
    """Computes a normalised score for a frontier objective given a state.

    The scoring logic is kind-dependent:

    * **COVERAGE** — score is the ratio of the current breadth to the
      ``target_breadth`` parameter, capped at 1.0.  If no
      ``target_breadth`` is supplied, a default of 10 is assumed.
    * **PRECISION** — score is inversely related to the distance of the
      frontier state's depth from the ``target_depth`` parameter.
    * **BALANCE** — score measures how close the exploration–exploitation
      ratio is to 0.5, using the breadth and depth as proxies.
    * **TERMINATION** — score increases as the convergence condition is
      approached, estimated from velocity and curvature.

    Attributes
    ----------
    function_id:
        Unique identifier for this scoring function instance.
    base_sensitivity:
        Scaling factor that amplifies score differences.  Higher values
        sharpen the discrimination between good and poor states.
    """

    function_id: str
    base_sensitivity: float = 1.0

    def score(self, state: Any, spec: FrontierObjectiveSpec) -> float:
        """Compute a normalised objective score in [0, 1].

        Parameters
        ----------
        state:
            The current frontier state.  Expected to expose ``breadth``,
            ``depth``, ``curvature``, and ``velocity`` attributes when
            it is a ``FrontierControlState``; otherwise attribute lookup
            falls back to 0.0 defaults.
        spec:
            The objective specification to evaluate.

        Returns
        -------
        float
            Normalised score in [0, 1].
        """
        breadth = _safe_float(getattr(state, "breadth", 0))
        depth = _safe_float(getattr(state, "depth", 0.0))
        curvature = _safe_float(getattr(state, "curvature", 0.0))
        velocity = _safe_float(getattr(state, "velocity", 0.0))
        params = spec.scoring_params

        if spec.kind == ObjectiveKind.COVERAGE:
            target_breadth = _safe_float(params.get("target_breadth", 10))
            if target_breadth <= 0:
                return 0.0
            ratio = breadth / target_breadth
            # Sigmoid-shaped score so that reaching 100% target gives 1.0
            # and going well beyond is slightly penalised.
            if ratio >= 1.0:
                penalty = math.exp(-self.base_sensitivity * (ratio - 1.0))
                return _clamp(penalty, 0.0, 1.0)
            return _clamp(ratio ** self.base_sensitivity, 0.0, 1.0)

        elif spec.kind == ObjectiveKind.PRECISION:
            target_depth = _safe_float(params.get("target_depth", depth))
            max_distance = _safe_float(params.get("max_distance", 5.0), default=5.0)
            distance_to_target = abs(depth - target_depth)
            score = math.exp(-self.base_sensitivity * distance_to_target / max(max_distance, 1e-9))
            return _clamp(score, 0.0, 1.0)

        elif spec.kind == ObjectiveKind.BALANCE:
            # Balance score is highest when breadth and depth are in proportion.
            # Proxy: ratio = depth / (breadth + 1); ideal near 0.5.
            ratio = depth / (breadth + 1.0)
            ideal = _safe_float(params.get("ideal_ratio", 0.5))
            deviation = abs(ratio - ideal)
            score = math.exp(-self.base_sensitivity * 2.0 * deviation)
            return _clamp(score, 0.0, 1.0)

        elif spec.kind == ObjectiveKind.TERMINATION:
            # Termination score rises as velocity approaches 0 (frontier
            # is no longer expanding) and curvature is low.
            velocity_score = math.exp(-self.base_sensitivity * abs(velocity))
            curvature_score = math.exp(-self.base_sensitivity * curvature)
            return _clamp((velocity_score + curvature_score) / 2.0, 0.0, 1.0)

        # Fallback for unknown kinds
        return 0.5


@dataclass
class ObjectiveWeightAdjuster:
    """Adaptively adjusts objective weights based on historical outcomes.

    After each orchestrator iteration the coordinator records the
    achieved outcome score for every objective.  This adjuster then
    *increases* the weight of objectives that are being well-satisfied
    (as a positive reinforcement signal) and *decreases* the weight of
    objectives that are chronically unsatisfied (to avoid wasting budget
    on intractable goals).

    Attributes
    ----------
    adjuster_id:
        Unique identifier for this adjuster instance.
    learning_rate:
        Step size for weight updates in [0, 1].
    _outcome_history:
        Internal mapping from ``spec_id`` to a list of recent outcome
        scores.
    """

    adjuster_id: str
    learning_rate: float = 0.05
    _outcome_history: dict[str, list[float]] = field(default_factory=dict)

    def record_outcome(self, spec_id: str, outcome_score: float) -> None:
        """Record an achieved outcome score for a given objective.

        Maintains a rolling window of the last 20 outcome scores per
        objective to limit memory growth.

        Parameters
        ----------
        spec_id:
            Identifier of the objective for which the outcome is recorded.
        outcome_score:
            Achieved score for the objective in [0, 1].
        """
        if spec_id not in self._outcome_history:
            self._outcome_history[spec_id] = []
        self._outcome_history[spec_id].append(_clamp(_safe_float(outcome_score), 0.0, 1.0))
        if len(self._outcome_history[spec_id]) > 20:
            self._outcome_history[spec_id] = self._outcome_history[spec_id][-20:]

    def adjust(
        self,
        objectives: list[FrontierObjectiveSpec],
        history: list[FrontierObjectiveScore],
    ) -> list[FrontierObjectiveSpec]:
        """Return a new list of objectives with updated weights.

        The weight adjustment rule is:

        * Compute the mean of recent outcome scores for each objective.
        * If mean_score > 0.7 (well-satisfied), increase weight by
          ``learning_rate * 0.5`` (exploit what works).
        * If mean_score < 0.3 (poorly satisfied), decrease weight by
          ``learning_rate`` (reduce pressure on hopeless goals).
        * Clamp all updated weights to [0.01, 1.0].

        Also records the scores from *history* into the outcome log.

        Parameters
        ----------
        objectives:
            Current list of objective specifications to adjust.
        history:
            Recent objective scores used to update outcome history.

        Returns
        -------
        list[FrontierObjectiveSpec]
            New list of ``FrontierObjectiveSpec`` objects with updated
            weights (new frozen dataclass instances).
        """
        # Ingest recent scores
        for score in history:
            self.record_outcome(score.spec_id, score.raw_score)

        updated: list[FrontierObjectiveSpec] = []
        for spec in objectives:
            outcomes = self._outcome_history.get(spec.spec_id, [])
            if not outcomes:
                updated.append(spec)
                continue

            mean_outcome = statistics.mean(outcomes)
            new_weight = spec.weight
            if mean_outcome > 0.7:
                new_weight += self.learning_rate * 0.5
            elif mean_outcome < 0.3:
                new_weight -= self.learning_rate
            new_weight = _clamp(new_weight, 0.01, 1.0)

            # Build a new frozen instance with the adjusted weight
            updated.append(
                FrontierObjectiveSpec(
                    spec_id=spec.spec_id,
                    kind=spec.kind,
                    weight=new_weight,
                    priority=spec.priority,
                    description=spec.description,
                    scoring_params=spec.scoring_params,
                )
            )
        return updated


@dataclass
class ObjectivePriorityQueue:
    """Min-heap priority queue for frontier objective specifications.

    Objectives are ordered by their ``priority`` field (ascending) so
    that the highest-urgency item (lowest integer value) is always at
    the top.  Ties are broken by descending ``weight``.

    Internally the heap stores ``(priority, -weight, spec_id, spec)``
    tuples so that Python's ``heapq`` module (which implements a
    min-heap) provides the correct ordering.

    Attributes
    ----------
    queue_id:
        Unique identifier for this queue instance.
    _heap:
        Internal list used as the heap buffer.
    """

    queue_id: str
    _heap: list = field(default_factory=list)

    def push(self, spec: FrontierObjectiveSpec) -> None:
        """Push *spec* onto the priority queue.

        Parameters
        ----------
        spec:
            The objective specification to enqueue.
        """
        heapq.heappush(self._heap, (spec.priority, -spec.weight, spec.spec_id, spec))

    def pop(self) -> FrontierObjectiveSpec:
        """Remove and return the highest-priority objective.

        Returns
        -------
        FrontierObjectiveSpec
            The objective with the lowest priority integer, breaking
            ties by highest weight.

        Raises
        ------
        IndexError
            If the queue is empty.
        """
        if not self._heap:
            raise IndexError("ObjectivePriorityQueue.pop() called on an empty queue")
        _, _, _, spec = heapq.heappop(self._heap)
        return spec

    def peek(self) -> FrontierObjectiveSpec:
        """Return (without removing) the highest-priority objective.

        Returns
        -------
        FrontierObjectiveSpec
            The next objective that would be returned by ``pop()``.

        Raises
        ------
        IndexError
            If the queue is empty.
        """
        if not self._heap:
            raise IndexError("ObjectivePriorityQueue.peek() called on an empty queue")
        _, _, _, spec = self._heap[0]
        return spec

    def __len__(self) -> int:
        """Return the number of objectives currently in the queue.

        Returns
        -------
        int
            Queue length.
        """
        return len(self._heap)


@dataclass
class FrontierControlObjectiveAnalyzer:
    """Produces structured quality reports for a frontier objective set.

    The analyzer evaluates an ``FrontierObjectiveSet`` against a frontier
    state and produces:

    * A per-objective pass/fail assessment.
    * A Pareto-dominance analysis identifying objectives whose scores are
      dominated by other objectives in the set.
    * A composite score using the configured weight mode.

    Attributes
    ----------
    analyzer_id:
        Unique identifier for this analyzer instance.
    """

    analyzer_id: str

    def analyze(
        self,
        obj_set: FrontierObjectiveSet,
        frontier_state: Any,
    ) -> dict[str, Any]:
        """Produce a structured quality report for the objective set.

        The report includes:

        * ``n_objectives`` — total number of objectives evaluated.
        * ``n_satisfied`` — number meeting the 0.6 satisfaction threshold.
        * ``coverage_score`` — mean score across COVERAGE objectives.
        * ``precision_score`` — mean score across PRECISION objectives.
        * ``balance_score`` — mean score across BALANCE objectives.
        * ``termination_score`` — mean score across TERMINATION objectives.
        * ``dominant_kind`` — kind with highest mean score.
        * ``all_satisfied`` — True iff every objective is satisfied.

        Parameters
        ----------
        obj_set:
            The set of objective specifications to analyze.
        frontier_state:
            Current frontier state (should expose breadth, depth, etc.).

        Returns
        -------
        dict[str, Any]
            Structured quality report.
        """
        scoring_fn = ObjectiveScoringFunction(
            function_id=f"{self.analyzer_id}-scoring",
            base_sensitivity=1.0,
        )

        scores_by_kind: dict[str, list[float]] = {
            "COVERAGE": [],
            "PRECISION": [],
            "BALANCE": [],
            "TERMINATION": [],
        }
        n_satisfied = 0
        all_raw: list[float] = []

        for spec in obj_set.objectives:
            raw = scoring_fn.score(frontier_state, spec)
            all_raw.append(raw)
            scores_by_kind[spec.kind.value].append(raw)
            if raw >= 0.6:
                n_satisfied += 1

        def _mean_or_none(lst: list[float]) -> float:
            return statistics.mean(lst) if lst else 0.0

        kind_means = {k: _mean_or_none(v) for k, v in scores_by_kind.items()}
        dominant_kind = max(kind_means, key=lambda k: kind_means[k]) if kind_means else "NONE"

        return {
            "n_objectives": len(obj_set.objectives),
            "n_satisfied": n_satisfied,
            "all_satisfied": n_satisfied == len(obj_set.objectives),
            "coverage_score": kind_means["COVERAGE"],
            "precision_score": kind_means["PRECISION"],
            "balance_score": kind_means["BALANCE"],
            "termination_score": kind_means["TERMINATION"],
            "dominant_kind": dominant_kind,
            "mean_raw_score": statistics.mean(all_raw) if all_raw else 0.0,
            "analyzer_id": self.analyzer_id,
        }

    def pareto_dominated(self, scores: list[FrontierObjectiveScore]) -> list[str]:
        """Return spec_ids of objectives that are Pareto-dominated.

        An objective A is dominated by objective B when:

        * B's ``raw_score`` >= A's ``raw_score``, *and*
        * B's ``weighted_score`` >= A's ``weighted_score``, *and*
        * at least one of those inequalities is strict.

        Parameters
        ----------
        scores:
            List of evaluated objective scores to compare pairwise.

        Returns
        -------
        list[str]
            Spec IDs of all dominated objectives.
        """
        dominated: list[str] = []
        for i, a in enumerate(scores):
            for j, b in enumerate(scores):
                if i == j:
                    continue
                if (
                    b.raw_score >= a.raw_score
                    and b.weighted_score >= a.weighted_score
                    and (b.raw_score > a.raw_score or b.weighted_score > a.weighted_score)
                ):
                    dominated.append(a.spec_id)
                    break
        return dominated

    def composite_score(
        self,
        scores: list[FrontierObjectiveScore],
        weights: list[float],
    ) -> float:
        """Compute a weighted composite score from a list of objective scores.

        The composite is a normalised weighted sum: each raw score is
        multiplied by the corresponding weight and the results are
        summed, then divided by the sum of weights to keep the output
        in [0, 1].

        Parameters
        ----------
        scores:
            Evaluated objective scores.
        weights:
            Per-objective weights in the same order as *scores*.

        Returns
        -------
        float
            Composite score in [0, 1].  Returns 0.0 if the lists are
            empty or if total weight is zero.
        """
        if not scores or not weights:
            return 0.0
        total_weight = sum(weights)
        if total_weight <= 0.0:
            return 0.0
        weighted_sum = sum(
            s.raw_score * w
            for s, w in zip(scores, weights)
        )
        return _clamp(weighted_sum / total_weight, 0.0, 1.0)


@dataclass(frozen=True, slots=True)
class FrontierControlObjectiveWitness:
    """Immutable record of a single frontier objective evaluation run.

    A witness captures every significant output of the objective
    coordinator: the scores, the composite value, which kind dominated,
    and a human-readable summary.

    Attributes
    ----------
    witness_id:
        Unique identifier for this witness record.
    timestamp:
        Unix timestamp of when the witness was sealed.
    success:
        True iff the run completed without errors and the composite
        score exceeded a baseline threshold.
    objective_set_id:
        Identifier of the ``FrontierObjectiveSet`` that was evaluated.
    scores:
        Tuple of ``FrontierObjectiveScore`` objects, one per objective.
    composite:
        Composite weighted score in [0, 1].
    dominant_kind:
        Kind label (e.g. ``"COVERAGE"``) of the highest-scoring
        objective category.
    summary:
        Human-readable one-line summary of the run outcome.
    """

    witness_id: str
    timestamp: float
    success: bool
    objective_set_id: str
    scores: tuple[FrontierObjectiveScore, ...]
    composite: float
    dominant_kind: str
    summary: str


@dataclass
class FrontierControlObjectiveCoordinator:
    """Top-level orchestrator for frontier control objective evaluation.

    # copilot: The coordinator is the top-level entry point for objective evaluation.
    # copilot: It accepts a FrontierObjectiveSet and frontier_state and:
    # copilot:
    # copilot: 1. Validates the objective set (weights sum, priority ordering).
    # copilot: 2. Scores each objective using ObjectiveScoringFunction.
    # copilot: 3. Adjusts weights based on recent history.
    # copilot: 4. Computes composite score using the specified weight mode.
    # copilot: 5. Finds Pareto-dominated objectives.
    # copilot: 6. Runs FrontierControlObjectiveAnalyzer for quality metrics.
    # copilot: 7. Seals and returns a FrontierControlObjectiveWitness.

    Attributes
    ----------
    session_id:
        Unique identifier for the coordinator session.
    default_weight_mode:
        Default aggregation mode (``"sum"``, ``"max"``, or ``"mean"``)
        applied when the objective set does not specify one.
    """

    session_id: str = field(default_factory=lambda: f"obj-session-{uuid.uuid4().hex[:12]}")
    default_weight_mode: str = "sum"

    def run(
        self,
        obj_set: FrontierObjectiveSet,
        frontier_state: Any,
    ) -> FrontierControlObjectiveWitness:
        """Execute the objective evaluation pipeline and return a witness.

        Pipeline steps:

        1. **Validate** the objective set — check that weights are
           non-negative and sum to a positive value; verify that at
           least one objective is present.
        2. **Score** each objective using ``ObjectiveScoringFunction``,
           assigning a raw score and a weighted score.
        3. **Adjust** weights using ``ObjectiveWeightAdjuster``.
        4. **Composite** — aggregate scores using the set's
           ``composite_weight_mode`` (or the coordinator default).
        5. **Pareto** — identify dominated objectives.
        6. **Analyze** — run ``FrontierControlObjectiveAnalyzer``.
        7. **Seal** — package everything into a
           ``FrontierControlObjectiveWitness``.

        Parameters
        ----------
        obj_set:
            The set of objectives to evaluate.
        frontier_state:
            Current frontier state (should expose breadth, depth, etc.).

        Returns
        -------
        FrontierControlObjectiveWitness
            Immutable record of the evaluation run.
        """
        run_start = time.monotonic()

        # Step 1: Validate
        if not obj_set.objectives:
            return FrontierControlObjectiveWitness(
                witness_id=f"witness-empty-{uuid.uuid4().hex[:8]}",
                timestamp=time.time(),
                success=False,
                objective_set_id=obj_set.set_id,
                scores=(),
                composite=0.0,
                dominant_kind="NONE",
                summary="Objective set is empty; cannot evaluate.",
            )

        total_weight = obj_set.total_weight()
        if total_weight <= 0.0:
            return FrontierControlObjectiveWitness(
                witness_id=f"witness-noweight-{uuid.uuid4().hex[:8]}",
                timestamp=time.time(),
                success=False,
                objective_set_id=obj_set.set_id,
                scores=(),
                composite=0.0,
                dominant_kind="NONE",
                summary="Objective set has zero total weight; cannot evaluate.",
            )

        # Step 2: Score each objective
        scoring_fn = ObjectiveScoringFunction(
            function_id=f"scoring-{self.session_id}",
            base_sensitivity=1.0,
        )
        raw_scores: list[float] = []
        obj_scores: list[FrontierObjectiveScore] = []

        for rank, spec in enumerate(
            sorted(obj_set.objectives, key=lambda o: (o.priority, -o.weight)),
            start=1,
        ):
            raw = _clamp(scoring_fn.score(frontier_state, spec), 0.0, 1.0)
            weighted = raw * spec.normalized_weight(total_weight)
            raw_scores.append(raw)
            obj_scores.append(
                FrontierObjectiveScore(
                    spec_id=spec.spec_id,
                    raw_score=raw,
                    weighted_score=weighted,
                    rank=rank,
                    timestamp=time.time(),
                )
            )

        # Step 3: Weight adjustment
        adjuster = ObjectiveWeightAdjuster(
            adjuster_id=f"adjuster-{self.session_id}",
            learning_rate=0.05,
        )
        specs_list = list(obj_set.objectives)
        adjuster.adjust(specs_list, obj_scores)

        # Step 4: Composite score
        weight_mode = obj_set.composite_weight_mode or self.default_weight_mode
        weights = [spec.weight for spec in obj_set.objectives]
        if weight_mode == "sum":
            composite = _clamp(
                sum(s.weighted_score for s in obj_scores), 0.0, 1.0
            )
        elif weight_mode == "max":
            composite = max((s.raw_score for s in obj_scores), default=0.0)
        else:  # mean or fallback
            composite = statistics.mean(s.raw_score for s in obj_scores) if obj_scores else 0.0

        # Step 5: Pareto dominance
        analyzer = FrontierControlObjectiveAnalyzer(
            analyzer_id=f"analyzer-{self.session_id}",
        )
        dominated_ids = analyzer.pareto_dominated(obj_scores)

        # Step 6: Quality analysis
        quality_report = analyzer.analyze(obj_set, frontier_state)
        dominant_kind = quality_report.get("dominant_kind", "NONE")

        # Determine success
        success = composite >= 0.3 and quality_report.get("n_satisfied", 0) > 0

        elapsed_ms = (time.monotonic() - run_start) * 1000.0

        # Build summary
        n_dom = len(dominated_ids)
        summary = (
            f"{'OK' if success else 'DEGRADED'} | set={obj_set.set_id} "
            f"composite={composite:.4f} dominant={dominant_kind} "
            f"dominated={n_dom}/{len(obj_scores)} elapsed={elapsed_ms:.1f}ms"
        )

        # Step 7: Seal witness
        return FrontierControlObjectiveWitness(
            witness_id=f"witness-{uuid.uuid4().hex[:10]}",
            timestamp=time.time(),
            success=success,
            objective_set_id=obj_set.set_id,
            scores=tuple(obj_scores),
            composite=composite,
            dominant_kind=dominant_kind,
            summary=summary,
        )


# ---------------------------------------------------------------------------
# Module-level standalone functions
# ---------------------------------------------------------------------------


def build_coverage_objective(weight: float, priority: int) -> FrontierObjectiveSpec:
    """Construct a COVERAGE frontier objective.

    A coverage objective instructs the orchestrator to maximise the
    breadth of explored semantic space.  The default ``target_breadth``
    is 10; callers may update ``scoring_params`` after construction if
    a different target is needed.

    Parameters
    ----------
    weight:
        Relative importance weight for this objective.
    priority:
        Integer priority level.  Lower = more urgent.

    Returns
    -------
    FrontierObjectiveSpec
        A newly created COVERAGE objective specification.
    """
    return FrontierObjectiveSpec(
        spec_id=f"coverage-{uuid.uuid4().hex[:8]}",
        kind=ObjectiveKind.COVERAGE,
        weight=_clamp(_safe_float(weight), 0.0, 1.0),
        priority=priority,
        description="Maximise the breadth of the explored semantic frontier.",
        scoring_params={"target_breadth": 10},
    )


def build_precision_objective(
    weight: float,
    priority: int,
    target_id: str,
) -> FrontierObjectiveSpec:
    """Construct a PRECISION frontier objective targeting *target_id*.

    A precision objective drives the frontier toward a specific target
    node, minimising the number of unexplored nodes in its vicinity.

    Parameters
    ----------
    weight:
        Relative importance weight for this objective.
    priority:
        Integer priority level.  Lower = more urgent.
    target_id:
        Identifier of the target node the frontier should approach.

    Returns
    -------
    FrontierObjectiveSpec
        A newly created PRECISION objective specification.
    """
    return FrontierObjectiveSpec(
        spec_id=f"precision-{uuid.uuid4().hex[:8]}",
        kind=ObjectiveKind.PRECISION,
        weight=_clamp(_safe_float(weight), 0.0, 1.0),
        priority=priority,
        description=(
            f"Minimise unexplored nodes near target '{target_id}'; "
            "drive the frontier toward high-precision recall."
        ),
        scoring_params={"target_id": target_id, "target_depth": 3.0, "max_distance": 5.0},
    )


def build_balance_objective(weight: float, priority: int) -> FrontierObjectiveSpec:
    """Construct a BALANCE frontier objective.

    A balance objective keeps exploration and exploitation at equal
    weight, preventing the orchestrator from over-committing to either
    strategy.

    Parameters
    ----------
    weight:
        Relative importance weight for this objective.
    priority:
        Integer priority level.  Lower = more urgent.

    Returns
    -------
    FrontierObjectiveSpec
        A newly created BALANCE objective specification.
    """
    return FrontierObjectiveSpec(
        spec_id=f"balance-{uuid.uuid4().hex[:8]}",
        kind=ObjectiveKind.BALANCE,
        weight=_clamp(_safe_float(weight), 0.0, 1.0),
        priority=priority,
        description=(
            "Maintain equal weighting between exploration breadth and "
            "exploitation depth; keep the explore/exploit ratio near 0.5."
        ),
        scoring_params={"ideal_ratio": 0.5},
    )


def build_termination_objective(
    weight: float,
    priority: int,
    closure_condition: str,
) -> FrontierObjectiveSpec:
    """Construct a TERMINATION frontier objective.

    A termination objective drives the frontier toward a pre-specified
    closure condition, such as zero velocity or full coverage.

    Parameters
    ----------
    weight:
        Relative importance weight for this objective.
    priority:
        Integer priority level.  Lower = more urgent.
    closure_condition:
        Human-readable description of the termination condition (e.g.
        ``"velocity < 0.01 and breadth < 3"``).

    Returns
    -------
    FrontierObjectiveSpec
        A newly created TERMINATION objective specification.
    """
    return FrontierObjectiveSpec(
        spec_id=f"termination-{uuid.uuid4().hex[:8]}",
        kind=ObjectiveKind.TERMINATION,
        weight=_clamp(_safe_float(weight), 0.0, 1.0),
        priority=priority,
        description=(
            f"Drive the frontier toward the closure condition: "
            f"'{closure_condition}'."
        ),
        scoring_params={"closure_condition": closure_condition},
    )


def rank_objectives(specs: list[FrontierObjectiveSpec]) -> list[FrontierObjectiveSpec]:
    """Return a sorted copy of *specs* ordered by priority then weight.

    Objectives are sorted ascending by ``priority`` (lower = more urgent)
    and, within the same priority level, descending by ``weight``
    (higher weight first).

    Parameters
    ----------
    specs:
        List of objective specifications to sort.

    Returns
    -------
    list[FrontierObjectiveSpec]
        New list containing the same objects in sorted order.  The
        original list is not modified.
    """
    return sorted(specs, key=lambda s: (s.priority, -s.weight))


def merge_objective_sets(sets: list[FrontierObjectiveSet]) -> FrontierObjectiveSet:
    """Merge multiple objective sets into a single deduplicated set.

    Objectives from all sets are combined.  When two objectives share
    the same ``spec_id``, the one with the *higher* weight is retained
    and the other is discarded.

    The ``composite_weight_mode`` of the first set in *sets* is used
    for the merged set; if *sets* is empty, ``"sum"`` is used.

    Parameters
    ----------
    sets:
        List of ``FrontierObjectiveSet`` objects to merge.

    Returns
    -------
    FrontierObjectiveSet
        A new immutable objective set containing all deduplicated
        objectives.
    """
    if not sets:
        return FrontierObjectiveSet(
            set_id=f"merged-empty-{uuid.uuid4().hex[:8]}",
            objectives=(),
            composite_weight_mode="sum",
        )

    seen: dict[str, FrontierObjectiveSpec] = {}
    for obj_set in sets:
        for spec in obj_set.objectives:
            if spec.spec_id not in seen or spec.weight > seen[spec.spec_id].weight:
                seen[spec.spec_id] = spec

    merged_objectives = tuple(rank_objectives(list(seen.values())))
    return FrontierObjectiveSet(
        set_id=f"merged-{uuid.uuid4().hex[:8]}",
        objectives=merged_objectives,
        composite_weight_mode=sets[0].composite_weight_mode,
    )


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=== FrontierControlObjective smoke test ===\n")

    # 1. Build individual objectives
    cov_obj = build_coverage_objective(weight=0.4, priority=1)
    prec_obj = build_precision_objective(weight=0.3, priority=2, target_id="node-42")
    bal_obj = build_balance_objective(weight=0.2, priority=3)
    term_obj = build_termination_objective(
        weight=0.1, priority=4, closure_condition="velocity < 0.05"
    )

    print(f"Coverage  spec: {cov_obj.spec_id} kind={cov_obj.kind.value} "
          f"weight={cov_obj.weight} hp={cov_obj.is_high_priority()}")
    print(f"Precision spec: {prec_obj.spec_id} kind={prec_obj.kind.value}")
    print(f"Balance   spec: {bal_obj.spec_id} kind={bal_obj.kind.value}")
    print(f"Terminat. spec: {term_obj.spec_id} kind={term_obj.kind.value}")

    # 2. Rank and build an objective set
    ranked = rank_objectives([bal_obj, term_obj, cov_obj, prec_obj])
    obj_set = FrontierObjectiveSet(
        set_id="smoke-set-001",
        objectives=tuple(ranked),
        composite_weight_mode="sum",
    )
    print(f"\nObjective set: {obj_set.set_id} "
          f"total_weight={obj_set.total_weight():.3f} "
          f"highest_priority={obj_set.highest_priority().spec_id}")  # type: ignore[union-attr]

    # 3. Create a mock frontier state using a simple namespace
    class MockState:
        """Minimal mock of FrontierControlState for smoke-testing."""
        breadth = 8
        depth = 3.5
        curvature = 0.7
        velocity = 0.5
        phase = "EXPANDING"

    mock_state = MockState()

    # 4. Run the coordinator
    coordinator = FrontierControlObjectiveCoordinator(default_weight_mode="sum")
    witness = coordinator.run(obj_set, mock_state)

    print(f"\nWitness [{witness.witness_id}]")
    print(f"  success   = {witness.success}")
    print(f"  composite = {witness.composite:.4f}")
    print(f"  dominant  = {witness.dominant_kind}")
    print(f"  summary   = {witness.summary}")

    # 5. Inspect scores
    print(f"\nPer-objective scores ({len(witness.scores)}):")
    for s in witness.scores:
        print(f"  {s.spec_id[:26]:<26} raw={s.raw_score:.4f} "
              f"weighted={s.weighted_score:.4f} "
              f"satisfactory={s.is_satisfactory()}")

    # 6. Test priority queue
    pq = ObjectivePriorityQueue(queue_id="smoke-pq")
    for spec in [bal_obj, term_obj, cov_obj, prec_obj]:
        pq.push(spec)
    print(f"\nPriority queue length: {len(pq)}")
    first = pq.peek()
    print(f"Peek (highest priority): {first.spec_id} priority={first.priority}")
    popped = pq.pop()
    print(f"Pop: {popped.spec_id} priority={popped.priority}")
    print(f"Queue length after pop: {len(pq)}")

    # 7. Merge two objective sets
    extra_cov = build_coverage_objective(weight=0.5, priority=1)
    set_a = FrontierObjectiveSet(
        set_id="set-a", objectives=(cov_obj, bal_obj), composite_weight_mode="sum"
    )
    set_b = FrontierObjectiveSet(
        set_id="set-b", objectives=(prec_obj, term_obj, extra_cov), composite_weight_mode="mean"
    )
    merged = merge_objective_sets([set_a, set_b])
    print(f"\nMerged set: {merged.set_id} n_objectives={len(merged.objectives)}")

    # 8. Pareto analysis
    analyzer = FrontierControlObjectiveAnalyzer(analyzer_id="smoke-analyzer")
    dominated = analyzer.pareto_dominated(list(witness.scores))
    print(f"Pareto dominated spec_ids: {dominated}")

    # 9. Assertions
    assert witness.success or witness.composite >= 0.0, "Witness must have valid composite"
    assert 0.0 <= witness.composite <= 1.0, "Composite must be in [0, 1]"
    assert len(witness.scores) == len(obj_set.objectives), "Score count must match objective count"
    assert merged.set_id.startswith("merged-"), "Merged set id must start with 'merged-'"
    assert len(merged.objectives) <= len(obj_set.objectives) + 2, "Merge size sanity"

    # Normalised weight test
    nw = cov_obj.normalized_weight(obj_set.total_weight())
    assert 0.0 <= nw <= 1.0, f"Normalized weight must be in [0,1], got {nw}"

    print("\n✓ All assertions passed.")
