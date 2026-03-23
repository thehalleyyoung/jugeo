"""Core optimization models for JuGeo mathematical ideation (Ch50).

Defines objectives, problems, solution candidates, Pareto fronts, and
optimization results that form the mathematical backbone of the
optimization subsystem.
"""
from __future__ import annotations

import logging
import math
import statistics
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

try:
    from jugeo.ideation.ideas import IdeaProposal
except ImportError:
    IdeaProposal = Any  # type: ignore

# ---------------------------------------------------------------------------
# 1. Module-level setup
# ---------------------------------------------------------------------------

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 2. Helper functions
# ---------------------------------------------------------------------------


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp *v* to the closed interval [*lo*, *hi*].

    Args:
        v:  Value to clamp.
        lo: Lower bound (inclusive).  Defaults to ``0.0``.
        hi: Upper bound (inclusive).  Defaults to ``1.0``.

    Returns:
        Clamped float.
    """
    return max(lo, min(hi, v))


def _make_result_id() -> str:
    """Return a fresh UUID4 string suitable for use as a result identifier.

    Returns:
        Hex UUID string (no hyphens).
    """
    return uuid.uuid4().hex


def _format_score(score: float) -> str:
    """Return *score* formatted to four decimal places.

    Args:
        score: Numeric score to format.

    Returns:
        String representation, e.g. ``'0.7321'``.
    """
    return f"{score:.4f}"


# ---------------------------------------------------------------------------
# 3. Enumerations
# ---------------------------------------------------------------------------


class ObjectiveDirection(str, Enum):
    """Direction of optimisation for a single objective.

    Attributes:
        MINIMIZE: Lower objective values are preferred.
        MAXIMIZE: Higher objective values are preferred.
    """

    MINIMIZE = "minimize"
    MAXIMIZE = "maximize"


class SolutionStatus(str, Enum):
    """Classification status of an optimisation solution candidate.

    Attributes:
        FEASIBLE:   Candidate satisfies all constraints but is not known optimal.
        INFEASIBLE: Candidate violates at least one constraint.
        DOMINATED:  Candidate is Pareto-dominated by at least one other candidate.
        OPTIMAL:    Candidate is (locally) optimal and non-dominated.
    """

    PENDING = "pending"
    EVALUATED = "evaluated"
    DOMINATED = "dominated"
    NONDOMINATED = "nondominated"


SolutionStatus.FEASIBLE = SolutionStatus.EVALUATED  # type: ignore[attr-defined]
SolutionStatus.INFEASIBLE = SolutionStatus.DOMINATED  # type: ignore[attr-defined]
SolutionStatus.OPTIMAL = SolutionStatus.NONDOMINATED  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# 4. IdeationObjective
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, init=False)
class IdeationObjective:
    """Immutable specification of a single optimisation objective.

    Wraps an evaluation function name, a direction of optimisation, and a
    weight that is used when combining objectives into a scalar score.  The
    evaluation logic is dispatched via :meth:`evaluate`, which interprets
    the :attr:`evaluation_fn_name` to compute a raw score for any idea.

    Attributes:
        objective_id:       Unique identifier string.
        name:               Human-readable name.
        weight:             Non-negative importance weight.
        direction:          :class:`ObjectiveDirection` flag.
        evaluation_fn_name: Key used to dispatch the evaluation function.
        description:        Optional prose description.
    """

    objective_id: str
    name: str
    weight: float
    direction: ObjectiveDirection
    evaluation_fn_name: str
    description: str = ""

    def __init__(
        self,
        *args: Any,
        objective_id: str | None = None,
        name: str | None = None,
        weight: float | None = None,
        direction: ObjectiveDirection | str | None = None,
        evaluation_fn_name: str | None = None,
        description: str = "",
    ) -> None:
        """Initialise an objective while supporting legacy constructor shapes.

        Supported forms:

        * ``IdeationObjective(name, direction, weight, description)``
        * ``IdeationObjective(name, direction, weight, description, fn_name)``
        * keyword-based modern construction with explicit ``objective_id``.
        """
        if args:
            if len(args) == 4:
                name, direction, weight, description = args
            elif len(args) == 5:
                name, direction, weight, description, evaluation_fn_name = args
            else:
                raise TypeError(
                    "IdeationObjective() supports 4 or 5 positional arguments: "
                    "(name, direction, weight, description[, evaluation_fn_name])."
                )

        if name is None:
            raise TypeError("IdeationObjective() missing required argument: 'name'")
        if direction is None:
            direction = ObjectiveDirection.MAXIMIZE
        if weight is None:
            weight = 1.0

        if isinstance(direction, str):
            direction = ObjectiveDirection(direction)

        resolved_objective_id = objective_id or str(name)
        resolved_fn_name = evaluation_fn_name or str(name)

        object.__setattr__(self, "objective_id", resolved_objective_id)
        object.__setattr__(self, "name", str(name))
        object.__setattr__(self, "weight", float(weight))
        object.__setattr__(self, "direction", direction)
        object.__setattr__(self, "evaluation_fn_name", str(resolved_fn_name))
        object.__setattr__(self, "description", description)

    def evaluate(self, idea: Any) -> float:
        """Evaluate *idea* under this objective and return a raw score.

        Dispatches on :attr:`evaluation_fn_name`:

        * ``"novelty"``      - Approximates novelty from title word count.
        * ``"feasibility"``  - Inverse function of ``idea.payoff``.
        * ``"purpose"``      - Derived from ``idea.hypothesis`` length modulo.
        * ``"yield"``        - Normalised ``idea.payoff`` capped at 1.0.
        * ``"cost"``         - Proportional cost proxy from ``idea.payoff``.
        * Any other name     - Returns the neutral score ``0.5``.

        Args:
            idea: An :class:`IdeaProposal` (or duck-typed equivalent)
                  exposing at least ``title``, ``hypothesis``, and
                  ``payoff`` attributes.

        Returns:
            Raw float score (not yet normalised).
        """
        fn = self.evaluation_fn_name

        if fn == "novelty":
            return min(1.0, max(0.0, len(idea.title.split()) * 0.05 + 0.3))

        if fn == "feasibility":
            return 1.0 / (1.0 + idea.payoff * 0.1)

        if fn == "purpose":
            return 0.5 + (len(idea.hypothesis) % 10) * 0.05

        if fn == "yield":
            return min(1.0, idea.payoff / 20.0)

        if fn == "cost":
            return min(1.0, idea.payoff * 0.08)

        return 0.5

    def normalize_score(self, raw: float) -> float:
        """Clamp *raw* to the unit interval [0, 1].

        Args:
            raw: Unclamped score value.

        Returns:
            Score guaranteed to lie in ``[0.0, 1.0]``.
        """
        return _clamp(raw)

    def is_better(self, a: float, b: float) -> bool:
        """Return ``True`` if score *a* is strictly better than *b*.

        "Better" is defined by :attr:`direction`: for MAXIMIZE, larger is
        better; for MINIMIZE, smaller is better.

        Args:
            a: First score.
            b: Second score to compare against.

        Returns:
            Boolean comparison result.
        """
        if self.direction == ObjectiveDirection.MAXIMIZE:
            return a > b
        return a < b

    def summary(self) -> str:
        """Return a concise one-line description of this objective.

        Returns:
            Formatted string including ID, name, direction, and weight.
        """
        return (
            f"[{self.objective_id}] {self.name} | {self.direction.value}"
            f" | weight={self.weight:.3f} | fn={self.evaluation_fn_name}"
        )


# ---------------------------------------------------------------------------
# 5. OptimizationProblem
# ---------------------------------------------------------------------------


@dataclass(slots=True, init=False)
class OptimizationProblem:
    """Defines a multi-objective optimisation problem over idea candidates.

    Encapsulates the set of objectives, optional string constraints, a
    budget cap, and the pool of candidate :class:`IdeaProposal` objects to
    be optimised.

    Attributes:
        problem_id:       Unique identifier.
        objectives:       List of :class:`IdeationObjective` instances.
        constraints:      Human-readable constraint descriptions.
        budget:           Maximum permissible total cost for a solution.
        candidate_ideas:  Pool of ideas to be scored and ranked.
        description:      Prose description of the problem being solved.
    """

    problem_id: str
    objectives: list[IdeationObjective] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    budget: float = 100.0
    candidate_ideas: list[Any] = field(default_factory=list)
    description: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __init__(
        self,
        problem_id: str | None = None,
        objectives: list[IdeationObjective] | None = None,
        constraints: list[str] | None = None,
        budget: float = 100.0,
        candidate_ideas: list[Any] | None = None,
        description: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.problem_id = problem_id or str(uuid.uuid4())
        self.objectives = list(objectives or [])
        self.constraints = list(constraints or [])
        self.metadata = dict(metadata or {})
        self.budget = float(self.metadata.get("budget", budget))
        self.candidate_ideas = list(candidate_ideas or [])
        self.description = description

    def is_feasible(self, solution: dict[str, Any]) -> bool:
        """Return ``True`` if *solution* satisfies budget and basic constraints.

        Checks that the ``'cost'`` key in *solution* does not exceed
        :attr:`budget`, and that *solution* contains at least an ``'idea'``
        key.

        Args:
            solution: Dictionary with at least a ``'cost'`` and ``'idea'`` key.

        Returns:
            Boolean feasibility flag.
        """
        cost = solution.get("cost", 0.0)
        if cost > self.budget:
            _log.debug(
                "Problem '%s': solution infeasible (cost %.2f > budget %.2f).",
                self.problem_id,
                cost,
                self.budget,
            )
            return False
        if "idea" not in solution:
            return False
        return True

    def evaluate(self, solution: dict[str, Any]) -> dict[str, float]:
        """Evaluate *solution* against all objectives and return per-objective scores.

        Args:
            solution: Dictionary containing at least an ``'idea'`` entry.

        Returns:
            Mapping of objective ID to normalised float score.
        """
        idea = solution.get("idea")
        scores: dict[str, float] = {}
        for obj in self.objectives:
            raw = obj.evaluate(idea) if idea is not None else 0.5
            scores[obj.objective_id] = obj.normalize_score(raw)
        return scores

    def total_objectives(self) -> int:
        """Return the number of objectives in this problem.

        Returns:
            Integer count.
        """
        return len(self.objectives)

    def objective_names(self) -> list[str]:
        """Return objective names in declaration order."""
        return [obj.name for obj in self.objectives]

    def directions(self) -> dict[str, ObjectiveDirection]:
        """Return objective directions keyed by objective name."""
        return {obj.name: obj.direction for obj in self.objectives}

    def add_objective(self, objective: IdeationObjective) -> None:
        """Append *objective* to this problem."""
        self.objectives.append(objective)

    def add_idea(self, idea: Any) -> None:
        """Append *idea* to the candidate pool.

        Args:
            idea: Idea object to add (duck-typed, must expose ``payoff``).
        """
        self.candidate_ideas.append(idea)
        _log.debug("Problem '%s': added idea to candidate pool.", self.problem_id)

    def summary(self) -> str:
        """Return a multi-line summary of this optimisation problem.

        Returns:
            Formatted string with problem metadata and objectives.
        """
        lines = [
            f"OptimizationProblem '{self.problem_id}'",
            f"  description : {self.description or '(none)'}",
            f"  budget      : {self.budget:.2f}",
            f"  objectives  : {self.total_objectives()}",
            f"  candidates  : {len(self.candidate_ideas)}",
            f"  constraints : {len(self.constraints)}",
        ]
        for obj in self.objectives:
            lines.append(f"    * {obj.summary()}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 6. SolutionCandidate
# ---------------------------------------------------------------------------


@dataclass(slots=True, init=False)
class SolutionCandidate:
    """A scored idea candidate produced during an optimisation run.

    Stores the evaluated scores per objective alongside the idea itself,
    a feasibility/dominance status, and the cost attributed to this
    candidate.

    Attributes:
        candidate_id: Unique identifier for this candidate instance.
        idea:         The underlying idea (duck-typed, must expose ``payoff``).
        scores:       Per-objective float scores keyed by objective ID.
        status:       :class:`SolutionStatus` classification.
        cost:         Estimated cost of realising this idea.
    """

    candidate_id: str
    idea: Any
    scores: dict[str, float] = field(default_factory=dict)
    status: SolutionStatus = SolutionStatus.PENDING
    cost: float = 0.0
    label: str = ""
    rank: int = 0
    crowding_distance: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def __init__(
        self,
        *args: Any,
        idea: Any = None,
        scores: dict[str, float] | None = None,
        status: SolutionStatus | str = SolutionStatus.PENDING,
        cost: float = 0.0,
        candidate_id: str | None = None,
        label: str = "",
        rank: int = 0,
        crowding_distance: float = 0.0,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if args:
            if len(args) == 1:
                if candidate_id is None and isinstance(args[0], str):
                    candidate_id = args[0]
                elif idea is None:
                    idea = args[0]
                else:
                    raise TypeError("SolutionCandidate() received conflicting positional idea")
            elif len(args) == 2:
                candidate_id, idea = args
            elif len(args) == 3:
                candidate_id, idea, scores = args
            else:
                raise TypeError(
                    "SolutionCandidate() supports up to 3 positional arguments: "
                    "(candidate_id, idea, scores)"
                )

        self.candidate_id = candidate_id or str(uuid.uuid4())
        self.idea = idea
        self.scores = dict(scores or {})
        self.status = SolutionStatus(status)
        self.cost = float(cost)
        self.label = label
        self.rank = int(rank)
        self.crowding_distance = float(crowding_distance)
        self.metadata = dict(metadata or {})

    def weighted_score(self, weights: dict[str, float]) -> float:
        """Return the weighted linear combination of all objective scores.

        Objectives absent from *weights* are skipped.  Returns ``0.0`` if
        the combined weight denominator is zero.

        Args:
            weights: Mapping of objective ID to weight.

        Returns:
            Weighted average score in ``[0.0, 1.0]``.
        """
        total_weight = 0.0
        total_score = 0.0
        for obj_id, weight in weights.items():
            if obj_id in self.scores:
                total_score += self.scores[obj_id] * weight
                total_weight += weight
        if total_weight == 0.0:
            return 0.0
        return total_score / total_weight

    def is_feasible(self) -> bool:
        """Return ``True`` if the candidate's status is not INFEASIBLE.

        Returns:
            Boolean feasibility flag.
        """
        return not bool(self.metadata.get("infeasible", False))

    def summary(self) -> str:
        """Return a one-line human-readable summary of this candidate.

        Returns:
            Formatted string including ID, status, cost, and total score.
        """
        return (
            f"Candidate[{self.candidate_id[:8]}] status={self.status.value}"
            f" cost={self.cost:.2f} total_score={_format_score(self.total_score())}"
        )

    def total_score(self) -> float:
        """Return the sum of all objective scores.

        Returns:
            Non-negative float.
        """
        return sum(self.scores.values())

    def aggregate_score(self) -> float:
        """Return the mean of all recorded objective scores."""
        if not self.scores:
            return 0.0
        return statistics.fmean(self.scores.values())

    def score_for(self, objective_name: str, default: float = 0.0) -> float:
        """Return the score recorded for *objective_name*."""
        return self.scores.get(objective_name, default)

    def is_evaluated(self) -> bool:
        """Return ``True`` when at least one objective score is populated."""
        return bool(self.scores)

    def mark_evaluated(self) -> None:
        """Mark the candidate as having been evaluated."""
        if self.metadata.get("infeasible", False):
            return
        self.status = SolutionStatus.EVALUATED

    def mark_dominated(self) -> None:
        """Mark the candidate as Pareto-dominated."""
        self.status = SolutionStatus.DOMINATED

    def mark_nondominated(self) -> None:
        """Mark the candidate as non-dominated / currently optimal."""
        if not self.metadata.get("infeasible", False):
            self.status = SolutionStatus.NONDOMINATED


# ---------------------------------------------------------------------------
# 7. ParetoFront
# ---------------------------------------------------------------------------


@dataclass(slots=True, init=False)
class ParetoFront:
    """Maintains a set of solution candidates and their Pareto-dominance status.

    On each :meth:`add` call dominance relationships among all current
    candidates are recomputed so :meth:`nondominated` stays current.

    Attributes:
        solutions:        All candidate solutions (dominated or not).
        objective_names:  Ordered list of objective IDs used for scoring.
    """

    solutions: list[SolutionCandidate] = field(default_factory=list)
    objective_names: list[str] = field(default_factory=list)
    generation: int = 0
    front_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    hypervolume: float = 0.0

    def __init__(
        self,
        solutions: list[SolutionCandidate] | None = None,
        objective_names: list[str] | None = None,
        members: list[SolutionCandidate] | None = None,
        generation: int = 0,
        front_id: str | None = None,
        hypervolume: float = 0.0,
    ) -> None:
        resolved_solutions = list(solutions if solutions is not None else (members or []))
        self.solutions = resolved_solutions
        self.objective_names = list(objective_names or [])
        self.generation = generation
        self.front_id = front_id or str(uuid.uuid4())
        self.hypervolume = float(hypervolume)

    def add(self, candidate: SolutionCandidate) -> None:
        """Add *candidate* and refresh all dominance statuses.

        Args:
            candidate: :class:`SolutionCandidate` to add.
        """
        self.solutions.append(candidate)
        self._update_dominance()
        _log.debug("ParetoFront: added candidate '%s'.", candidate.candidate_id)

    def _update_dominance(self) -> None:
        """Recompute dominance for all candidates."""
        for i, b in enumerate(self.solutions):
            if b.metadata.get("infeasible", False):
                continue
            dominated = False
            for j, a in enumerate(self.solutions):
                if i == j:
                    continue
                if a.metadata.get("infeasible", False):
                    continue
                if self.dominates(a, b):
                    dominated = True
                    break
            b.status = SolutionStatus.DOMINATED if dominated else SolutionStatus.NONDOMINATED

    def dominates(self, a: SolutionCandidate, b: SolutionCandidate) -> bool:
        """Return ``True`` if *a* Pareto-dominates *b* under MAXIMIZE semantics.

        *a* dominates *b* when it is at least as good on every objective
        and strictly better on at least one.

        Args:
            a: Candidate assumed to be the dominator.
            b: Candidate potentially being dominated.

        Returns:
            Boolean dominance result.
        """
        names = self.objective_names or list(a.scores.keys())
        strictly_better = False
        for name in names:
            score_a = a.scores.get(name, 0.0)
            score_b = b.scores.get(name, 0.0)
            if score_a < score_b:
                return False
            if score_a > score_b:
                strictly_better = True
        return strictly_better

    def nondominated(self) -> list[SolutionCandidate]:
        """Return the subset of solutions that are not Pareto-dominated.

        Returns:
            List of non-dominated :class:`SolutionCandidate` objects.
        """
        return [
            s for s in self.solutions
            if s.status != SolutionStatus.DOMINATED
            and not s.metadata.get("infeasible", False)
        ]

    def crowding_distances(self) -> list[float]:
        """Compute the NSGA-II crowding distance for each solution.

        Boundary solutions receive ``math.inf``; interior solutions receive
        the sum of normalised distances to their neighbours across all
        objectives.

        Returns:
            List of floats parallel to :attr:`solutions`.
        """
        n = len(self.solutions)
        distances = [0.0] * n

        if n <= 2:
            return [math.inf] * n

        names = self.objective_names or (
            list(self.solutions[0].scores.keys()) if self.solutions else []
        )

        for name in names:
            sorted_indices = sorted(
                range(n),
                key=lambda i: self.solutions[i].scores.get(name, 0.0),
            )
            distances[sorted_indices[0]] = math.inf
            distances[sorted_indices[-1]] = math.inf

            lo = self.solutions[sorted_indices[0]].scores.get(name, 0.0)
            hi = self.solutions[sorted_indices[-1]].scores.get(name, 0.0)
            span = hi - lo if hi != lo else 1.0

            for rank in range(1, n - 1):
                prev_score = self.solutions[sorted_indices[rank - 1]].scores.get(name, 0.0)
                next_score = self.solutions[sorted_indices[rank + 1]].scores.get(name, 0.0)
                if distances[sorted_indices[rank]] != math.inf:
                    distances[sorted_indices[rank]] += (next_score - prev_score) / span
        for solution, distance in zip(self.solutions, distances):
            solution.crowding_distance = distance
        return distances

    def size(self) -> int:
        """Return the total number of solutions (dominated and non-dominated).

        Returns:
            Integer count.
        """
        return len(self.solutions)

    @property
    def members(self) -> list[SolutionCandidate]:
        """Legacy alias for :attr:`solutions`."""
        return self.solutions

    def summary(self) -> str:
        """Return a human-readable summary of the Pareto front.

        Returns:
            Multi-line string with counts and top candidates.
        """
        nd = self.nondominated()
        lines = [f"ParetoFront: {self.size()} total / {len(nd)} non-dominated"]
        for cand in nd[:5]:
            lines.append(f"  + {cand.summary()}")
        if len(nd) > 5:
            lines.append(f"  ... and {len(nd) - 5} more non-dominated solutions.")
        return "\n".join(lines)

    def best_by_objective(self, objective_name: str) -> SolutionCandidate | None:
        """Return the candidate with the highest score for *objective_name*.

        Args:
            objective_name: Objective ID to rank by.

        Returns:
            :class:`SolutionCandidate` with highest score, or ``None``.
        """
        if not self.solutions:
            return None
        return max(
            self.solutions,
            key=lambda s: s.scores.get(objective_name, float("-inf")),
        )

    def best_by(self, objective_name: str) -> SolutionCandidate | None:
        """Legacy alias for :meth:`best_by_objective`."""
        return self.best_by_objective(objective_name)

    def score_ranges(self) -> dict[str, tuple[float, float]]:
        """Return per-objective ``(min, max)`` ranges across current members."""
        names = self.objective_names or (
            list(self.solutions[0].scores.keys()) if self.solutions else []
        )
        ranges: dict[str, tuple[float, float]] = {}
        for name in names:
            values = [solution.score_for(name, 0.0) for solution in self.solutions]
            ranges[name] = (min(values), max(values)) if values else (0.0, 0.0)
        return ranges


# ---------------------------------------------------------------------------
# 8. ObjectiveWeight
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ObjectiveWeight:
    """Immutable pairing of an objective identifier with its importance weight.

    Attributes:
        objective_id: The ID of the objective this weight applies to.
        weight:       Non-negative importance weight.
        rationale:    Optional human-readable reason for this weight value.
    """

    objective_id: str
    weight: float
    rationale: str = ""

    @classmethod
    def normalize(cls, weights: list[ObjectiveWeight]) -> list[ObjectiveWeight]:
        """Return a new list where all weights sum to 1.0.

        If the total weight is zero every weight is set to ``1 / len(weights)``.

        Args:
            weights: Input list of :class:`ObjectiveWeight` objects.

        Returns:
            New list of :class:`ObjectiveWeight` objects with normalised weights.
        """
        total = sum(w.weight for w in weights)
        if total == 0.0:
            even = 1.0 / len(weights) if weights else 0.0
            return [cls(w.objective_id, even, w.rationale) for w in weights]
        return [cls(w.objective_id, w.weight / total, w.rationale) for w in weights]

    def summary(self) -> str:
        """Return a one-line description of this weight.

        Returns:
            Formatted string including objective ID and weight value.
        """
        rationale_part = f" ({self.rationale})" if self.rationale else ""
        return f"ObjectiveWeight[{self.objective_id}]={self.weight:.4f}{rationale_part}"


# ---------------------------------------------------------------------------
# 9. OptimizationResult
# ---------------------------------------------------------------------------


@dataclass(slots=True, init=False)
class OptimizationResult:
    """Captures the full output of a completed optimisation run.

    Attributes:
        result_id:           Unique identifier for this result.
        problem_id:          ID of the :class:`OptimizationProblem` that was solved.
        pareto_front:        The computed :class:`ParetoFront`.
        best_solution:       The single best candidate (or ``None``).
        iteration_count:     Number of algorithm iterations performed.
        convergence_metric:  Scalar convergence measure (lower = more converged).
        runtime_seconds:     Wall-clock time taken by the optimisation run.
    """

    result_id: str
    problem_id: str
    pareto_front: ParetoFront = field(default_factory=ParetoFront)
    best_solution: SolutionCandidate | None = None
    iteration_count: int = 0
    convergence_metric: float = 0.0
    runtime_seconds: float = 0.0
    problem: OptimizationProblem | None = None
    all_candidates: list[SolutionCandidate] = field(default_factory=list)
    iterations_run: int = 0
    converged: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def __init__(
        self,
        result_id: str | None = None,
        problem_id: str | None = None,
        pareto_front: ParetoFront | None = None,
        best_solution: SolutionCandidate | None = None,
        iteration_count: int = 0,
        convergence_metric: float = 0.0,
        runtime_seconds: float = 0.0,
        *,
        problem: OptimizationProblem | None = None,
        all_candidates: list[SolutionCandidate] | None = None,
        iterations_run: int | None = None,
        converged: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.problem = problem
        self.result_id = result_id or _make_result_id()
        self.problem_id = problem_id or (problem.problem_id if problem is not None else "")
        self.pareto_front = pareto_front or ParetoFront()
        self.best_solution = (
            best_solution
            if best_solution is not None
            else (self.pareto_front.members[0] if self.pareto_front.members else None)
        )
        self.all_candidates = list(
            all_candidates if all_candidates is not None else self.pareto_front.members
        )
        self.iterations_run = iteration_count if iterations_run is None else iterations_run
        self.iteration_count = self.iterations_run
        self.convergence_metric = float(convergence_metric)
        self.runtime_seconds = float(runtime_seconds)
        self.converged = converged
        self.metadata = dict(metadata or {})

    def summary(self) -> str:
        """Return a concise multi-line summary of this result.

        Returns:
            Formatted string with result metadata.
        """
        best_str = self.best_solution.summary() if self.best_solution else "None"
        return (
            f"OptimizationResult '{self.result_id}'\n"
            f"  problem_id         : {self.problem_id}\n"
            f"  pareto front size  : {self.pareto_front.size()}\n"
            f"  non-dominated      : {len(self.pareto_front.nondominated())}\n"
            f"  best solution      : {best_str}\n"
            f"  iterations         : {self.iteration_count}\n"
            f"  convergence metric : {_format_score(self.convergence_metric)}\n"
            f"  runtime (s)        : {self.runtime_seconds:.3f}\n"
            f"  converged          : {self.is_converged()}"
        )

    def copilot_report(self) -> str:
        """Return a rich Markdown-formatted report suitable for inline display.

        Returns:
            Multi-line Markdown string.
        """
        nd = self.pareto_front.nondominated()
        best_str = self.best_solution.summary() if self.best_solution else "_none_"
        converged_sym = "yes" if self.is_converged() else "no"
        header = (
            f"## OptimizationResult Report\n"
            f"- **Result ID**: `{self.result_id}`\n"
            f"- **Problem ID**: `{self.problem_id}`\n"
            f"- **Pareto front**: {self.pareto_front.size()} total, {len(nd)} non-dominated\n"
            f"- **Best solution**: {best_str}\n"
            f"- **Iterations**: {self.iteration_count}\n"
            f"- **Convergence metric**: {_format_score(self.convergence_metric)}\n"
            f"- **Runtime**: {self.runtime_seconds:.3f}s\n"
            f"- **Converged**: {converged_sym}\n\n"
            f"### Non-dominated Solutions\n"
        )
        rows = [
            "| Candidate | Status | Cost | Total Score |",
            "|-----------|--------|------|-------------|",
        ]
        for cand in nd[:10]:
            rows.append(
                f"| `{cand.candidate_id[:8]}` | {cand.status.value}"
                f" | {cand.cost:.2f} | {_format_score(cand.total_score())} |"
            )
        if len(nd) > 10:
            rows.append(f"| _(+{len(nd) - 10} more)_ | | | |")
        return header + "\n".join(rows)

    def is_converged(self, threshold: float = 0.01) -> bool:
        """Return ``True`` if :attr:`convergence_metric` is below *threshold*.

        Args:
            threshold: Maximum convergence metric value to consider converged.
                       Defaults to ``0.01``.

        Returns:
            Boolean convergence flag.
        """
        return self.converged or self.convergence_metric <= threshold

    def front_size(self) -> int:
        """Return the number of members in the Pareto front."""
        return self.pareto_front.size()

    def n_evaluated(self) -> int:
        """Return the number of evaluated candidates tracked by the result."""
        return sum(1 for candidate in self.all_candidates if candidate.is_evaluated())


# ---------------------------------------------------------------------------
# 10. WeightedObjective
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WeightedObjective:
    """An :class:`IdeationObjective` bundled with its importance weight.

    Attributes:
        objective: Underlying :class:`IdeationObjective`.
        weight:    Scalar importance weight.
    """

    objective: IdeationObjective
    weight: float

    def evaluate(self, idea: Any) -> float:
        """Return the weighted score for *idea* under this objective.

        Args:
            idea: Idea object to evaluate.

        Returns:
            Weighted float score.
        """
        raw = self.objective.evaluate(idea)
        return self.objective.normalize_score(raw) * self.weight

    def summary(self) -> str:
        """Return a description combining the objective summary and this weight.

        Returns:
            Formatted string.
        """
        return f"WeightedObjective(weight={self.weight:.4f}) wrapping {self.objective.summary()}"


# ---------------------------------------------------------------------------
# 11. ConstraintSatisfaction
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ConstraintSatisfaction:
    """Records whether a single constraint was satisfied by a solution.

    Attributes:
        constraint_id: Unique identifier for the constraint.
        satisfied:     ``True`` if the constraint is satisfied.
        value:         Actual value of the constrained quantity.
        bound:         Upper bound that must not be exceeded.
        description:   Optional human-readable description.
    """

    constraint_id: str
    satisfied: bool
    value: float
    bound: float
    description: str = ""

    def violation_amount(self) -> float:
        """Return the amount by which the constraint is violated.

        Returns:
            Non-negative float (``max(0, value - bound)``).
        """
        return max(0.0, self.value - self.bound)

    def summary(self) -> str:
        """Return a one-line description of this constraint result.

        Returns:
            Formatted string including ID, status, value, and bound.
        """
        status = "satisfied" if self.satisfied else f"violated (+{self.violation_amount():.4f})"
        return (
            f"Constraint[{self.constraint_id}]: {status} "
            f"(value={self.value:.4f}, bound={self.bound:.4f})"
        )


# ---------------------------------------------------------------------------
# 12. ObjectiveNormalizer
# ---------------------------------------------------------------------------


class ObjectiveNormalizer:
    """Fits a min-max normalisation transform over objective score samples.

    Must be fitted via :meth:`fit` before :meth:`normalize` can be called.

    Attributes:
        _mins: Per-objective minimum values observed during fitting.
        _maxs: Per-objective maximum values observed during fitting.
    """

    def __init__(self) -> None:
        """Initialise an unfitted normaliser."""
        self._mins: dict[str, float] = {}
        self._maxs: dict[str, float] = {}

    def fit(self, samples: list[dict[str, float]]) -> None:
        """Record per-objective min and max from *samples*.

        Args:
            samples: List of score dictionaries, one per evaluated candidate.
        """
        if not samples:
            return
        all_values: dict[str, list[float]] = {}
        for sample in samples:
            for obj_id, score in sample.items():
                all_values.setdefault(obj_id, []).append(score)
        self._mins = {k: min(v) for k, v in all_values.items()}
        self._maxs = {k: max(v) for k, v in all_values.items()}
        _log.debug("ObjectiveNormalizer fitted on %d samples.", len(samples))

    def normalize(self, scores: dict[str, float]) -> dict[str, float]:
        """Map *scores* to [0, 1] using the fitted min-max range.

        Args:
            scores: Raw scores keyed by objective ID.

        Returns:
            Normalised scores in the same key structure.

        Raises:
            RuntimeError: If :meth:`fit` has not been called yet.
        """
        if not self.is_fitted():
            raise RuntimeError(
                "ObjectiveNormalizer.normalize() called before fit(). "
                "Call fit() with sample score dictionaries first."
            )
        result: dict[str, float] = {}
        for obj_id, raw in scores.items():
            lo = self._mins.get(obj_id, 0.0)
            hi = self._maxs.get(obj_id, 1.0)
            span = hi - lo
            if span == 0.0:
                result[obj_id] = 0.5
            else:
                result[obj_id] = _clamp((raw - lo) / span)
        return result

    def is_fitted(self) -> bool:
        """Return ``True`` if :meth:`fit` has been successfully called.

        Returns:
            Boolean flag.
        """
        return bool(self._mins)

    def reset(self) -> None:
        """Clear all fitted statistics, returning the normaliser to an unfitted state."""
        self._mins.clear()
        self._maxs.clear()
        _log.debug("ObjectiveNormalizer reset.")


# ---------------------------------------------------------------------------
# 13. Factory and convenience helpers
# ---------------------------------------------------------------------------


def _build_default_objectives() -> list[IdeationObjective]:
    """Construct the standard set of objectives used by default problems.

    Returns five :class:`IdeationObjective` instances covering novelty,
    feasibility, purpose alignment, theoretical yield, and implementation cost.

    Returns:
        List of five :class:`IdeationObjective` instances.
    """
    return [
        IdeationObjective(
            objective_id="novelty",
            name="Novelty",
            weight=0.25,
            direction=ObjectiveDirection.MAXIMIZE,
            evaluation_fn_name="novelty",
            description="Approximates how novel or original the idea is.",
        ),
        IdeationObjective(
            objective_id="feasibility",
            name="Feasibility",
            weight=0.20,
            direction=ObjectiveDirection.MAXIMIZE,
            evaluation_fn_name="feasibility",
            description="Estimates practical feasibility given payoff.",
        ),
        IdeationObjective(
            objective_id="purpose",
            name="Purpose Alignment",
            weight=0.20,
            direction=ObjectiveDirection.MAXIMIZE,
            evaluation_fn_name="purpose",
            description="Measures alignment with the stated research purpose.",
        ),
        IdeationObjective(
            objective_id="yield",
            name="Theoretical Yield",
            weight=0.20,
            direction=ObjectiveDirection.MAXIMIZE,
            evaluation_fn_name="yield",
            description="Estimates expected theoretical output relative to payoff.",
        ),
        IdeationObjective(
            objective_id="cost",
            name="Implementation Cost",
            weight=0.15,
            direction=ObjectiveDirection.MINIMIZE,
            evaluation_fn_name="cost",
            description="Proxy for implementation effort derived from payoff.",
        ),
    ]


def _score_statistics(
    candidates: list[SolutionCandidate], objective_id: str
) -> dict[str, float]:
    """Compute descriptive statistics for a single objective across candidates.

    Args:
        candidates:   List of :class:`SolutionCandidate` objects.
        objective_id: The objective whose scores are to be summarised.

    Returns:
        Dictionary with keys ``'mean'``, ``'stdev'``, ``'min'``, and ``'max'``.
    """
    vals = [c.scores.get(objective_id, 0.0) for c in candidates if objective_id in c.scores]
    if not vals:
        return {"mean": 0.0, "stdev": 0.0, "min": 0.0, "max": 0.0}
    return {
        "mean": statistics.mean(vals),
        "stdev": statistics.stdev(vals) if len(vals) > 1 else 0.0,
        "min": min(vals),
        "max": max(vals),
    }


# ---------------------------------------------------------------------------
# 14. Public API surface
# ---------------------------------------------------------------------------

__all__ = [
    "ConstraintSatisfaction",
    "IdeationObjective",
    "ObjectiveDirection",
    "ObjectiveNormalizer",
    "ObjectiveWeight",
    "OptimizationProblem",
    "OptimizationResult",
    "ParetoFront",
    "SolutionCandidate",
    "SolutionStatus",
    "WeightedObjective",
]
