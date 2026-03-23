"""Pareto-front multi-objective optimization for JuGeo ideation (Ch50).

Implements dominance checking, crowding distance, NSGA-II-style selection,
epsilon-constraint solving, and a full ParetoOptimizer.

The module is structured in five sections:
  1. Module-level helpers
  2. DominanceChecker  – Pareto dominance and non-domination ranking
  3. CrowdingDistance  – diversity-preserving distance metric
  4. NSGAIIStyle       – fast non-dominated sort + tournament selection
  5. EpsilonConstraintSolver – single-objective optimisation with constraints
  6. ParetoOptimizer   – high-level optimisation loop

All data containers are imported from ``.models``; objective implementations
from ``.objective_functions``.
"""
from __future__ import annotations

import logging
import math
import random
import uuid
from dataclasses import dataclass, field
from typing import Any

from .models import (
    ObjectiveDirection,
    SolutionStatus,
    IdeationObjective,
    OptimizationProblem,
    SolutionCandidate,
    ParetoFront,
    OptimizationResult,
)
from .objective_functions import ObjectiveEvaluator, ObjectiveFactory

try:
    from jugeo.ideation.ideas import IdeaProposal
except ImportError:
    IdeaProposal = Any  # type: ignore

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 1. Module-level helpers
# ---------------------------------------------------------------------------

def _dominated_by_any(
    candidate: dict[str, float],
    others: list[dict[str, float]],
    directions: dict[str, ObjectiveDirection] | None = None,
) -> bool:
    """Check whether *candidate* is Pareto-dominated by any solution in *others*.

    Parameters
    ----------
    candidate:
        Score dictionary for the solution under test.
    others:
        List of score dictionaries to compare against.
    directions:
        Optional mapping of objective name → :class:`ObjectiveDirection`.
        Defaults to MAXIMIZE for all objectives.

    Returns
    -------
    bool
        ``True`` if at least one element of *others* dominates *candidate*.
    """
    _dirs = directions or {}
    for other in others:
        if other is candidate:
            continue
        dominated = True
        at_least_one_strictly_better = False
        for key in candidate:
            a_val = other.get(key, 0.5)
            b_val = candidate.get(key, 0.5)
            direction = _dirs.get(key, ObjectiveDirection.MAXIMIZE)
            if not _at_least_as_good_fn(a_val, b_val, direction):
                dominated = False
                break
            if _strict_better_fn(a_val, b_val, direction):
                at_least_one_strictly_better = True
        if dominated and at_least_one_strictly_better:
            return True
    return False


def _compare_scores(a: float, b: float, direction: ObjectiveDirection) -> int:
    """Compare two scores accounting for objective *direction*.

    Parameters
    ----------
    a:
        Score of solution A.
    b:
        Score of solution B.
    direction:
        MAXIMIZE or MINIMIZE.

    Returns
    -------
    int
        -1 if A is worse than B, 0 if equal, 1 if A is better than B.
    """
    if direction == ObjectiveDirection.MAXIMIZE:
        if a > b:
            return 1
        if a < b:
            return -1
        return 0
    else:  # MINIMIZE
        if a < b:
            return 1
        if a > b:
            return -1
        return 0


def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    """Return ``a / b``, falling back to *default* when *b* is zero.

    Parameters
    ----------
    a:
        Numerator.
    b:
        Denominator.
    default:
        Value returned when *b* is (approximately) zero.

    Returns
    -------
    float
        Division result or *default*.
    """
    if abs(b) < 1e-12:
        return default
    return a / b


# ------------------------------------------------------------------
# Private free functions used by the module (not exposed in __all__)

def _strict_better_fn(a: float, b: float, direction: ObjectiveDirection) -> bool:
    """Return True when *a* strictly outperforms *b* in *direction*."""
    return _compare_scores(a, b, direction) == 1


def _at_least_as_good_fn(a: float, b: float, direction: ObjectiveDirection) -> bool:
    """Return True when *a* is at least as good as *b* in *direction*."""
    return _compare_scores(a, b, direction) >= 0


# ---------------------------------------------------------------------------
# 2. Dominance checker
# ---------------------------------------------------------------------------

class DominanceChecker:
    """Encapsulates Pareto dominance logic for multi-objective comparisons.

    All methods are stateless and can be called repeatedly without any
    setup.  Direction defaults to MAXIMIZE if not supplied.
    """

    # ------------------------------------------------------------------
    def dominates(
        self,
        a_scores: dict[str, float],
        b_scores: dict[str, float],
        directions: dict[str, ObjectiveDirection] | None = None,
    ) -> bool:
        """Return ``True`` if solution *a* Pareto-dominates solution *b*.

        Solution *a* dominates *b* when:
          * for every objective *k*, *a[k]* is at least as good as *b[k]*,
          * and for at least one objective *k*, *a[k]* is strictly better.

        Missing keys in either dictionary are substituted with 0.5.

        Parameters
        ----------
        a_scores:
            Objective scores for candidate A.
        b_scores:
            Objective scores for candidate B.
        directions:
            Per-objective direction mapping.  Defaults to MAXIMIZE.

        Returns
        -------
        bool
            Whether *a* dominates *b*.
        """
        _dirs = directions or {}
        all_keys = set(a_scores) | set(b_scores)
        at_least_one_strict = False
        for key in all_keys:
            a_val = a_scores.get(key, 0.5)
            b_val = b_scores.get(key, 0.5)
            direction = _dirs.get(key, ObjectiveDirection.MAXIMIZE)
            if not _at_least_as_good_fn(a_val, b_val, direction):
                return False
            if _strict_better_fn(a_val, b_val, direction):
                at_least_one_strict = True
        return at_least_one_strict

    def dominance_rank(
        self,
        population: list[dict[str, float]],
        directions: dict[str, ObjectiveDirection] | None = None,
    ) -> list[int]:
        """Assign a dominance rank to every element of *population*.

        Rank 0 = non-dominated (Pareto front).
        Rank 1 = dominated only by rank-0 solutions, and so on.

        This implementation uses an iterative peeling algorithm: the
        current non-dominated front is extracted and assigned the current
        rank, then removed, and the process repeats.

        Parameters
        ----------
        population:
            List of score dictionaries.
        directions:
            Per-objective direction mapping.

        Returns
        -------
        list[int]
            Rank for each element in the same order as *population*.
        """
        n = len(population)
        ranks = [0] * n
        remaining = list(range(n))
        current_rank = 0

        while remaining:
            # Find the non-dominated subset of the remaining indices
            front_indices: list[int] = []
            for i in remaining:
                dominated = False
                for j in remaining:
                    if i == j:
                        continue
                    if self.dominates(population[j], population[i], directions):
                        dominated = True
                        break
                if not dominated:
                    front_indices.append(i)
            for idx in front_indices:
                ranks[idx] = current_rank
            remaining = [r for r in remaining if r not in front_indices]
            current_rank += 1
            if current_rank > n:
                # Safety valve – should never be reached
                _log.warning("dominance_rank safety break at rank %d", current_rank)
                break

        return ranks

    def nondominated_front(
        self,
        population: list[Any],
        scores: list[dict[str, float]],
        directions: dict[str, ObjectiveDirection] | None = None,
    ) -> list[Any]:
        """Return population elements that are on the non-dominated front.

        Parameters
        ----------
        population:
            Arbitrary objects (e.g., :class:`SolutionCandidate` instances).
        scores:
            Score dictionaries aligned with *population* by index.
        directions:
            Per-objective direction mapping.

        Returns
        -------
        list
            Subset of *population* whose elements are non-dominated.

        Raises
        ------
        ValueError
            If *population* and *scores* have different lengths.
        """
        if len(population) != len(scores):
            raise ValueError(
                f"population length {len(population)} != scores length {len(scores)}"
            )
        result: list[Any] = []
        for i, (item, s_i) in enumerate(zip(population, scores)):
            dominated = False
            for j, s_j in enumerate(scores):
                if i == j:
                    continue
                if self.dominates(s_j, s_i, directions):
                    dominated = True
                    break
            if not dominated:
                result.append(item)
        return result

    # ------------------------------------------------------------------
    def _strict_better(
        self,
        a: float,
        b: float,
        direction: ObjectiveDirection,
    ) -> bool:
        """Return True when *a* is strictly better than *b* in *direction*.

        Parameters
        ----------
        a:
            Score of candidate A on a single objective.
        b:
            Score of candidate B on the same objective.
        direction:
            MAXIMIZE or MINIMIZE.

        Returns
        -------
        bool
        """
        return _strict_better_fn(a, b, direction)

    def _at_least_as_good(
        self,
        a: float,
        b: float,
        direction: ObjectiveDirection,
    ) -> bool:
        """Return True when *a* is at least as good as *b* in *direction*.

        Parameters
        ----------
        a:
            Score of candidate A.
        b:
            Score of candidate B.
        direction:
            MAXIMIZE or MINIMIZE.

        Returns
        -------
        bool
        """
        return _at_least_as_good_fn(a, b, direction)


# ---------------------------------------------------------------------------
# 3. Crowding distance
# ---------------------------------------------------------------------------

class CrowdingDistance:
    """Computes NSGA-II crowding distances to preserve diversity on a Pareto front.

    A large crowding distance indicates that a solution occupies a sparse
    region of the objective space and should therefore be preferred during
    selection to maintain a well-spread front.
    """

    # ------------------------------------------------------------------
    def compute(
        self,
        front: list[SolutionCandidate],
        objective_names: list[str],
    ) -> dict[str, float]:
        """Compute crowding distances for all candidates in *front*.

        Algorithm (per objective):
          1. Sort the front by this objective's score.
          2. Assign ``inf`` to the boundary (best and worst) solutions.
          3. Assign the normalised score difference to interior solutions.

        The distance for each candidate is the *sum* over all objectives.

        Parameters
        ----------
        front:
            List of :class:`SolutionCandidate` instances on one Pareto front.
        objective_names:
            Ordered names of objectives to consider.

        Returns
        -------
        dict[str, float]
            Mapping of ``candidate_id → crowding_distance``.
        """
        distances: dict[str, float] = {c.candidate_id: 0.0 for c in front}

        if len(front) <= 2:
            for c in front:
                distances[c.candidate_id] = math.inf
            return distances

        for obj_name in objective_names:
            obj_range = self._objective_range(front, obj_name)
            sorted_front = sorted(front, key=lambda c: c.score_for(obj_name))

            # Boundary candidates receive infinite distance
            distances[sorted_front[0].candidate_id] = math.inf
            distances[sorted_front[-1].candidate_id] = math.inf

            for i in range(1, len(sorted_front) - 1):
                prev_score = sorted_front[i - 1].score_for(obj_name)
                next_score = sorted_front[i + 1].score_for(obj_name)
                delta = _safe_div(next_score - prev_score, obj_range, default=0.0)
                cid = sorted_front[i].candidate_id
                if distances[cid] != math.inf:
                    distances[cid] += delta

        _log.debug(
            "CrowdingDistance.compute: %d candidates, %d objectives",
            len(front),
            len(objective_names),
        )
        for candidate in front:
            candidate.crowding_distance = distances.get(candidate.candidate_id, 0.0)
        return distances

    def sort_by_crowding(
        self,
        candidates: list[SolutionCandidate],
        objective_names: list[str],
    ) -> list[SolutionCandidate]:
        """Return *candidates* sorted in descending crowding distance order.

        Candidates with higher crowding distances are placed first, as they
        are more "isolated" and thus more desirable for diversity.

        Parameters
        ----------
        candidates:
            List of :class:`SolutionCandidate` instances to sort.
        objective_names:
            Objective names to use for distance computation.

        Returns
        -------
        list[SolutionCandidate]
            New list sorted from highest to lowest crowding distance.
        """
        if not candidates:
            return []
        distances = self.compute(candidates, objective_names)
        return sorted(
            candidates,
            key=lambda c: distances.get(c.candidate_id, 0.0),
            reverse=True,
        )

    # ------------------------------------------------------------------
    def _objective_range(
        self,
        front: list[SolutionCandidate],
        obj_name: str,
    ) -> float:
        """Return the score range (max − min) for *obj_name* across *front*.

        Parameters
        ----------
        front:
            Candidates over which to compute the range.
        obj_name:
            Objective whose range should be computed.

        Returns
        -------
        float
            Non-negative range value.  Returns 1.0 (safe) if empty.
        """
        if not front:
            return 1.0
        scores = [c.score_for(obj_name) for c in front]
        return max(scores) - min(scores)


# ---------------------------------------------------------------------------
# 4. NSGA-II-style selection
# ---------------------------------------------------------------------------

class NSGAIIStyle:
    """NSGA-II-inspired multi-objective selection mechanism.

    Combines fast non-dominated sorting with crowding-distance ranking to
    select diverse, high-quality subsets of a population.

    Attributes
    ----------
    _dominance_checker:
        Internal :class:`DominanceChecker` instance.
    _crowding:
        Internal :class:`CrowdingDistance` instance.
    """

    def __init__(self) -> None:
        """Initialise NSGAIIStyle with default helper components."""
        self._dominance_checker: DominanceChecker = DominanceChecker()
        self._crowding: CrowdingDistance = CrowdingDistance()
        _log.debug("NSGAIIStyle initialised")

    # ------------------------------------------------------------------
    def fast_nondominated_sort(
        self,
        population: list[SolutionCandidate],
        directions: dict[str, ObjectiveDirection] | None = None,
    ) -> list[list[SolutionCandidate]]:
        """Partition *population* into a list of non-domination fronts.

        The first element of the returned list is the non-dominated front
        (rank 0), the second element contains solutions dominated only by
        rank-0 solutions, and so on.

        Parameters
        ----------
        population:
            Candidates to sort.
        directions:
            Per-objective direction mapping.

        Returns
        -------
        list[list[SolutionCandidate]]
            Ordered list of fronts; ``fronts[0]`` is the Pareto front.
        """
        if not population:
            return []

        n = len(population)
        scores_list = [c.scores for c in population]
        ranks = self._dominance_checker.dominance_rank(scores_list, directions)

        max_rank = max(ranks) if ranks else 0
        fronts: list[list[SolutionCandidate]] = [[] for _ in range(max_rank + 1)]
        for candidate, rank in zip(population, ranks):
            candidate.rank = rank
            fronts[rank].append(candidate)

        obj_names = list(population[0].scores.keys()) if population else []
        for front in fronts:
            distances = self._crowding.compute(front, obj_names)
            for candidate in front:
                candidate.crowding_distance = distances.get(candidate.candidate_id, 0.0)

        # Mark status accordingly
        for candidate in fronts[0]:
            candidate.mark_nondominated()
        for front in fronts[1:]:
            for candidate in front:
                candidate.mark_dominated()

        _log.debug(
            "fast_nondominated_sort: %d candidates → %d fronts",
            n,
            len(fronts),
        )
        return fronts

    def select(
        self,
        population: list[SolutionCandidate],
        n: int,
        directions: dict[str, ObjectiveDirection] | None = None,
    ) -> list[SolutionCandidate]:
        """Select the top-*n* candidates using NSGA-II selection.

        Fronts are added whole until adding the next front would exceed *n*.
        In that case, the crowding-distance sort determines which candidates
        from the boundary front are included.

        Parameters
        ----------
        population:
            Full candidate population.
        n:
            Number of candidates to select.
        directions:
            Per-objective direction mapping.

        Returns
        -------
        list[SolutionCandidate]
            Selected candidates (at most *n*).
        """
        fronts = self.fast_nondominated_sort(population, directions)
        selected: list[SolutionCandidate] = []
        obj_names = list(population[0].scores.keys()) if population else []

        for front in fronts:
            if len(selected) + len(front) <= n:
                selected.extend(front)
            else:
                needed = n - len(selected)
                if needed <= 0:
                    break
                sorted_by_crowd = self._crowding.sort_by_crowding(
                    front, obj_names
                )
                selected.extend(sorted_by_crowd[:needed])
                break
            if len(selected) >= n:
                break

        _log.debug(
            "NSGAIIStyle.select: selected %d / %d candidates", len(selected), len(population)
        )
        return selected

    def tournament_select(
        self,
        population: list[SolutionCandidate],
        k: int = 2,
    ) -> SolutionCandidate:
        """Select the best of *k* randomly chosen candidates (binary tournament).

        Selection criterion: lower rank wins; ties broken by higher crowding
        distance (if set on the candidate), then at random.

        Parameters
        ----------
        population:
            Pool of candidates to draw from.
        k:
            Tournament size (default 2 for binary tournament).

        Returns
        -------
        SolutionCandidate
            The tournament winner.

        Raises
        ------
        ValueError
            If *population* is empty.
        """
        if not population:
            raise ValueError("Cannot run tournament on empty population")

        actual_k = min(k, len(population))
        contestants = random.sample(population, actual_k)

        def _key(c: SolutionCandidate) -> tuple[int, float]:
            # Lower rank is better; higher crowding distance is better
            return (c.rank, -c.crowding_distance)

        winner = min(contestants, key=_key)
        _log.debug(
            "tournament_select: k=%d, winner rank=%d", k, winner.rank
        )
        return winner


# ---------------------------------------------------------------------------
# 5. Epsilon-constraint solver
# ---------------------------------------------------------------------------

class EpsilonConstraintSolver:
    """Scalarises the multi-objective problem using the ε-constraint method.

    One objective is treated as the primary optimisation target; all
    others must exceed given ε thresholds.  Sweeping ε values across a
    grid recovers a discrete approximation to the Pareto front.

    Attributes
    ----------
    _dominance_checker:
        Internal :class:`DominanceChecker` instance.
    """

    def __init__(self) -> None:
        """Initialise EpsilonConstraintSolver."""
        self._dominance_checker: DominanceChecker = DominanceChecker()
        _log.debug("EpsilonConstraintSolver initialised")

    # ------------------------------------------------------------------
    def solve(
        self,
        problem: OptimizationProblem,
        primary_obj: str,
        epsilons: dict[str, float],
    ) -> list[SolutionCandidate]:
        """Return feasible candidates sorted by the primary objective score.

        Evaluation is performed using an :class:`ObjectiveEvaluator` built
        from the standard objective suite.  Each candidate idea is wrapped
        in a :class:`SolutionCandidate`.

        Feasibility criterion: for every non-primary objective *k*,
        ``score[k] >= epsilons.get(k, 0.0)``.

        Parameters
        ----------
        problem:
            :class:`OptimizationProblem` containing candidate ideas.
        primary_obj:
            Name of the primary objective to maximise.
        epsilons:
            Lower bounds for all non-primary objectives.

        Returns
        -------
        list[SolutionCandidate]
            Feasible candidates, sorted descending by *primary_obj* score.
        """
        evaluator = ObjectiveEvaluator(
            ObjectiveFactory.create_standard_suite()
        )
        results: list[SolutionCandidate] = []

        for idea in problem.candidate_ideas:
            scores = evaluator.evaluate_all(idea)
            # Check epsilon constraints for non-primary objectives
            feasible = True
            for obj_name, eps in epsilons.items():
                if obj_name == primary_obj:
                    continue
                if scores.get(obj_name, 0.0) < eps:
                    feasible = False
                    break
            if not feasible:
                _log.debug(
                    "solve: idea %r infeasible, skipping",
                    getattr(idea, "title", "?")[:30],
                )
                continue

            candidate = SolutionCandidate(
                idea=idea,
                scores=scores,
                status=SolutionStatus.EVALUATED,
            )
            results.append(candidate)

        results.sort(
            key=lambda c: c.score_for(primary_obj),
            reverse=True,
        )
        _log.debug(
            "EpsilonConstraintSolver.solve: %d feasible / %d candidates",
            len(results),
            len(problem.candidate_ideas),
        )
        return results

    def generate_epsilon_grid(
        self,
        problem: OptimizationProblem,
        steps: int = 5,
    ) -> list[dict[str, float]]:
        """Generate a grid of ε value combinations for all objectives.

        Produces ``steps`` linearly spaced values in [0, 1] for each
        objective and returns the Cartesian product as a list of dicts.
        The grid grows exponentially in the number of objectives; for
        large problems use a small *steps* value.

        Parameters
        ----------
        problem:
            :class:`OptimizationProblem` whose objectives define the axes.
        steps:
            Number of equally-spaced epsilon values per axis (min 2).

        Returns
        -------
        list[dict[str, float]]
            Each element is a complete ``{objective_name: epsilon}`` dict.
        """
        steps = max(2, steps)
        obj_names = problem.objective_names()
        if not obj_names:
            return [{}]

        step_size = 1.0 / (steps - 1)
        epsilon_values = {
            name: [round(i * step_size, 6) for i in range(steps)]
            for name in obj_names
        }

        # Build Cartesian product iteratively
        grid: list[dict[str, float]] = [{}]
        for name, values in epsilon_values.items():
            new_grid: list[dict[str, float]] = []
            for existing in grid:
                for v in values:
                    entry = dict(existing)
                    entry[name] = v
                    new_grid.append(entry)
            grid = new_grid

        _log.debug(
            "generate_epsilon_grid: %d objectives × %d steps → %d combinations",
            len(obj_names),
            steps,
            len(grid),
        )
        return grid


# ---------------------------------------------------------------------------
# 6. Pareto optimizer
# ---------------------------------------------------------------------------

class ParetoOptimizer:
    """High-level multi-objective optimiser backed by NSGA-II-style selection.

    Runs an iterative evaluation + selection loop over the candidate idea
    pool defined in an :class:`OptimizationProblem`, building up the Pareto
    front incrementally.

    Attributes
    ----------
    problem:
        The optimisation problem to solve.
    population_size:
        How many candidates to keep between iterations.
    max_iterations:
        Upper bound on the number of evaluation-selection cycles.
    _nsga:
        Internal :class:`NSGAIIStyle` engine.
    _evaluator:
        Internal :class:`ObjectiveEvaluator` built from the standard suite.
    """

    def __init__(
        self,
        problem: OptimizationProblem,
        population_size: int = 20,
        max_iterations: int = 50,
    ) -> None:
        """Initialise ParetoOptimizer.

        Parameters
        ----------
        problem:
            Problem definition, including candidate ideas.
        population_size:
            Maximum population retained each iteration.
        max_iterations:
            Number of optimisation iterations to run.
        """
        self.problem: OptimizationProblem = problem
        self.population_size: int = max(1, population_size)
        self.max_iterations: int = max(1, max_iterations)
        self._nsga: NSGAIIStyle = NSGAIIStyle()
        # Build evaluator from the problem's objective specifications or the
        # standard suite when no explicit objectives are present.
        if problem.objectives:
            obj_names = problem.objective_names()
            suite = ObjectiveFactory.create_standard_suite(
                {name: 1.0 for name in obj_names}
            )
            self._evaluator = ObjectiveEvaluator(suite)
        else:
            self._evaluator = ObjectiveEvaluator(
                ObjectiveFactory.create_standard_suite()
            )
        _log.info(
            "ParetoOptimizer created: pop=%d, max_iter=%d, candidates=%d",
            self.population_size,
            self.max_iterations,
            len(problem.candidate_ideas),
        )

    # ------------------------------------------------------------------
    def optimize(self) -> OptimizationResult:
        """Run the full optimisation loop and return a consolidated result.

        Algorithm outline:
          1. Initialise population from the candidate idea pool.
          2. For each iteration: evaluate scores, apply NSGA-II selection.
          3. Extract the final Pareto front (rank-0 solutions).
          4. Wrap in an :class:`OptimizationResult` and return.

        Returns
        -------
        OptimizationResult
            Contains the Pareto front and all evaluated candidates.
        """
        _log.info("ParetoOptimizer.optimize: starting %d iterations", self.max_iterations)
        population = self._initialize_population()

        for iteration in range(self.max_iterations):
            population = self._evaluate_population(population)
            population = self.step(population)
            _log.debug(
                "Iteration %d: population size = %d", iteration + 1, len(population)
            )

        # Evaluate the final population once more to ensure scores are current
        population = self._evaluate_population(population)

        # Build the Pareto front from rank-0 solutions
        fronts = self._nsga.fast_nondominated_sort(
            population, directions=self.problem.directions()
        )
        pareto_members = fronts[0] if fronts else []

        pareto_front = ParetoFront(
            members=pareto_members,
            generation=self.max_iterations,
            objective_names=self._evaluator.objective_names(),
        )

        result = OptimizationResult(
            problem=self.problem,
            pareto_front=pareto_front,
            all_candidates=population,
            iterations_run=self.max_iterations,
            converged=True,
            metadata={"population_size": self.population_size},
        )
        _log.info(
            "ParetoOptimizer.optimize: done. Front size=%d", pareto_front.size()
        )
        return result

    def step(
        self,
        population: list[SolutionCandidate],
    ) -> list[SolutionCandidate]:
        """Perform one NSGA-II selection step.

        Selects the top-:attr:`population_size` candidates from *population*
        using NSGA-II criteria (non-domination rank + crowding distance).

        Parameters
        ----------
        population:
            Current population with evaluated scores.

        Returns
        -------
        list[SolutionCandidate]
            Reduced population of at most :attr:`population_size` candidates.
        """
        if not population:
            return []
        return self._nsga.select(
            population,
            n=self.population_size,
            directions=self.problem.directions(),
        )

    def _initialize_population(self) -> list[SolutionCandidate]:
        """Create one :class:`SolutionCandidate` per idea in the problem pool.

        Returns
        -------
        list[SolutionCandidate]
            Unevaluated candidate wrappers, one per idea.
        """
        population: list[SolutionCandidate] = []
        for idea in self.problem.candidate_ideas:
            candidate = SolutionCandidate(idea=idea)
            population.append(candidate)
        _log.debug("_initialize_population: %d candidates", len(population))
        return population

    def _evaluate_population(
        self,
        pop: list[SolutionCandidate],
    ) -> list[SolutionCandidate]:
        """Evaluate objective scores for each candidate in *pop*.

        Scores are written directly onto each :class:`SolutionCandidate`
        and the status is updated to EVALUATED.

        Parameters
        ----------
        pop:
            Candidates to evaluate; modified in place.

        Returns
        -------
        list[SolutionCandidate]
            The same list with scores populated.
        """
        for candidate in pop:
            if candidate.idea is None:
                _log.warning("Skipping candidate with no idea")
                continue
            try:
                candidate.scores = self._evaluator.evaluate_all(candidate.idea)
                candidate.mark_evaluated()
            except Exception as exc:  # noqa: BLE001
                _log.error(
                    "Error evaluating candidate %r: %s",
                    candidate.candidate_id[:8],
                    exc,
                )
                candidate.scores = {}
        _log.debug("_evaluate_population: evaluated %d candidates", len(pop))
        return pop

    def __repr__(self) -> str:
        return (
            f"ParetoOptimizer("
            f"pop={self.population_size}, "
            f"max_iter={self.max_iterations}, "
            f"n_ideas={len(self.problem.candidate_ideas)})"
        )


# ---------------------------------------------------------------------------
# __all__
# ---------------------------------------------------------------------------

__all__ = [
    # helpers
    "_dominated_by_any",
    "_compare_scores",
    "_safe_div",
    # classes
    "DominanceChecker",
    "CrowdingDistance",
    "NSGAIIStyle",
    "EpsilonConstraintSolver",
    "ParetoOptimizer",
]
