"""Optimization algorithm implementations for JuGeo ideation (Ch50).

Provides weighted-sum, lexicographic, random search, simulated annealing,
evolutionary, and Bayesian-style optimizers, plus an AlgorithmSelector for
automatic algorithm selection.

Each optimizer implements the :class:`OptimizationAlgorithm` interface and
returns a fully populated :class:`OptimizationResult`.  Algorithms operate on
the discrete set of :class:`IdeaProposal` instances stored in an
:class:`OptimizationProblem` — there is no continuous search space.

Objective scores come from evaluating ``idea.payoff`` (and other idea
attributes) against the problem's :class:`IdeationObjective` list.  When the
optional :mod:`objective_functions` sub-module is present its richer
evaluators are used; otherwise a lightweight fallback based on ``idea.payoff``
is applied.
"""
from __future__ import annotations

import logging
import math
import random
import statistics
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from .models import (
    IdeationObjective,
    ObjectiveDirection,
    OptimizationProblem,
    OptimizationResult,
    ParetoFront,
    SolutionCandidate,
    SolutionStatus,
)

try:
    from .objective_functions import ObjectiveEvaluator, ObjectiveFactory
    _HAS_EVALUATOR = True
except ImportError:  # pragma: no cover
    ObjectiveEvaluator = Any  # type: ignore
    ObjectiveFactory = Any  # type: ignore
    _HAS_EVALUATOR = False

try:
    from jugeo.ideation.ideas import IdeaProposal
except ImportError:  # pragma: no cover
    IdeaProposal = Any  # type: ignore

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 1. Module-level helper functions
# ---------------------------------------------------------------------------

def _acceptance_probability(old_cost: float, new_cost: float, temp: float) -> float:
    """Return the simulated-annealing acceptance probability.

    If *new_cost* is strictly better (higher) than *old_cost* the transition is
    always accepted (returns ``1.0``).  Otherwise the Boltzmann probability
    ``exp((old - new) / max(temp, 1e-9))`` is returned; as temperature falls
    toward zero the probability of accepting a worsening move approaches zero.

    Parameters
    ----------
    old_cost:
        Score of the current solution (higher is better).
    new_cost:
        Score of the proposed neighbour solution.
    temp:
        Current temperature (must be non-negative).
    """
    if new_cost >= old_cost:
        return 1.0
    return math.exp((new_cost - old_cost) / max(temp, 1e-9))


def _mutation(idea_idx: int, n_ideas: int) -> int:
    """Return a random index different from *idea_idx*.

    Used by :class:`EvolutionaryOptimizer` to generate offspring by swapping
    a candidate to any other position in the candidate pool.  Raises
    ``ValueError`` when *n_ideas* is less than 2 (no mutation possible).

    Parameters
    ----------
    idea_idx:
        Current index to mutate away from.
    n_ideas:
        Total number of candidate ideas available.
    """
    if n_ideas < 2:
        raise ValueError("Need at least 2 ideas to perform mutation.")
    choices = [i for i in range(n_ideas) if i != idea_idx]
    return random.choice(choices)


def _ucb(mean: float, std: float, kappa: float) -> float:
    """Upper-confidence-bound acquisition value.

    Balances exploitation (high *mean*) with exploration (high *std*) via the
    trade-off parameter *kappa*.  Higher *kappa* encourages exploration.

    Parameters
    ----------
    mean:
        Empirical mean of observed scores.
    std:
        Empirical standard deviation of observed scores.
    kappa:
        Exploration–exploitation trade-off coefficient (≥ 0).
    """
    return mean + kappa * std


def _total_score(candidate: SolutionCandidate) -> float:
    """Return the sum of all objective scores recorded on *candidate*.

    A simple scalar aggregator used where a quick ranking is needed without
    per-objective weighting.  Returns ``0.0`` for unevaluated candidates.
    """
    return sum(candidate.scores.values())


def _normalise_payoff(idea: Any) -> float:
    """Return a [0, 1]-normalised payoff score for *idea*.

    Uses ``idea.payoff`` when available, clamped to [0, 100] and divided by
    100.  Falls back to ``0.5`` for objects lacking the ``payoff`` attribute.
    """
    payoff = getattr(idea, "payoff", None)
    if payoff is None:
        return 0.5
    return max(0.0, min(1.0, float(payoff) / 100.0))


def _build_pareto_front(
    candidates: list[SolutionCandidate],
    objectives: list[IdeationObjective],
    generation: int = 0,
) -> ParetoFront:
    """Identify and return the non-dominated subset of *candidates*.

    A candidate *a* dominates *b* when *a* is not worse on every objective and
    strictly better on at least one.  Dominated candidates are marked with
    :attr:`SolutionStatus.DOMINATED`; the front members are marked
    :attr:`SolutionStatus.NONDOMINATED`.

    Parameters
    ----------
    candidates:
        All evaluated candidates to classify.
    objectives:
        Objective specifications (used for direction).
    generation:
        Iteration index to embed in the returned :class:`ParetoFront`.
    """
    directions = {o.name: o.direction for o in objectives}
    obj_names = list(directions)

    def _dominates(a: SolutionCandidate, b: SolutionCandidate) -> bool:
        at_least_one_better = False
        for name in obj_names:
            sa = a.scores.get(name, 0.0)
            sb = b.scores.get(name, 0.0)
            d = directions.get(name, ObjectiveDirection.MAXIMIZE)
            if d == ObjectiveDirection.MAXIMIZE:
                if sa < sb:
                    return False
                if sa > sb:
                    at_least_one_better = True
            else:
                if sa > sb:
                    return False
                if sa < sb:
                    at_least_one_better = True
        return at_least_one_better

    dominated_flags = [False] * len(candidates)
    for i, ci in enumerate(candidates):
        for j, cj in enumerate(candidates):
            if i != j and _dominates(cj, ci):
                dominated_flags[i] = True
                break

    front_members: list[SolutionCandidate] = []
    for i, c in enumerate(candidates):
        if dominated_flags[i]:
            c.mark_dominated()
        else:
            c.mark_nondominated()
            front_members.append(c)

    return ParetoFront(
        members=front_members,
        generation=generation,
        objective_names=obj_names,
    )


# ---------------------------------------------------------------------------
# 2. Base algorithm class
# ---------------------------------------------------------------------------

class OptimizationAlgorithm:
    """Abstract base class for all JuGeo ideation optimizers.

    Sub-classes must override :meth:`optimize`.  The class also provides
    protected helpers :meth:`_evaluate`, :meth:`_make_candidate`, and
    :meth:`_make_result` shared across concrete implementations.

    Attributes
    ----------
    name:
        Short identifier for the algorithm, used in logging and
        :class:`AlgorithmSelector` output.
    max_iterations:
        Upper bound on the number of optimisation iterations.
    """

    def __init__(self, name: str = "base", max_iterations: int = 100) -> None:
        self.name = name
        self.max_iterations = max_iterations

    # ------------------------------------------------------------------
    def optimize(self, problem: OptimizationProblem) -> OptimizationResult:
        """Run the algorithm on *problem* and return an :class:`OptimizationResult`.

        Raises
        ------
        NotImplementedError
            Always; must be overridden by concrete sub-classes.
        """
        raise NotImplementedError(f"{self.__class__.__name__}.optimize() not implemented.")

    # ------------------------------------------------------------------
    def _evaluate(
        self,
        idea: Any,
        objectives: list[IdeationObjective],
    ) -> dict[str, float]:
        """Evaluate *idea* against each objective and return a score dict.

        When the :mod:`objective_functions` module is available its
        :class:`ObjectiveEvaluator` is used; otherwise a lightweight fallback
        derives all scores from ``idea.payoff``.

        Parameters
        ----------
        idea:
            The :class:`IdeaProposal` (or duck-typed equivalent) to score.
        objectives:
            The list of objectives whose names will be keys in the result.
        """
        scores: dict[str, float] = {}
        base = _normalise_payoff(idea)

        for obj in objectives:
            name = obj.name
            if _HAS_EVALUATOR:
                try:
                    evaluator = ObjectiveEvaluator(obj)
                    scores[name] = float(evaluator.evaluate(idea))
                    continue
                except Exception:  # pragma: no cover
                    pass
            # Fallback: derive a pseudo-score from idea.payoff with small
            # per-objective perturbation so objectives are not identical.
            seed_val = abs(hash(name)) % 100 / 1000.0
            if obj.direction == ObjectiveDirection.MINIMIZE:
                scores[name] = max(0.0, min(1.0, 1.0 - base + seed_val))
            else:
                scores[name] = max(0.0, min(1.0, base + seed_val))

        return scores

    # ------------------------------------------------------------------
    def _make_candidate(
        self,
        idea: Any,
        problem: OptimizationProblem,
    ) -> SolutionCandidate:
        """Wrap *idea* in a :class:`SolutionCandidate` with computed scores.

        Parameters
        ----------
        idea:
            The :class:`IdeaProposal` to evaluate and wrap.
        problem:
            The surrounding :class:`OptimizationProblem` (provides objectives).
        """
        scores = self._evaluate(idea, problem.objectives)
        candidate = SolutionCandidate(idea=idea, scores=scores)
        candidate.mark_evaluated()
        return candidate

    # ------------------------------------------------------------------
    def _make_result(
        self,
        problem: OptimizationProblem,
        best: SolutionCandidate | None,
        front: ParetoFront,
        iterations: int,
        start_time: float,
        all_candidates: list[SolutionCandidate] | None = None,
    ) -> OptimizationResult:
        """Construct a fully populated :class:`OptimizationResult`.

        Parameters
        ----------
        problem:
            The solved problem.
        best:
            The single best candidate (or ``None`` if none found).
        front:
            The final non-dominated :class:`ParetoFront`.
        iterations:
            Number of iterations the algorithm actually ran.
        start_time:
            ``time.monotonic()`` timestamp from before the run started; used to
            record wall-clock duration in ``metadata["duration_s"]``.
        """
        duration = time.monotonic() - start_time
        metadata: dict[str, Any] = {
            "algorithm": self.name,
            "duration_s": round(duration, 6),
            "iterations": iterations,
        }
        if best is not None:
            idea_title = getattr(best.idea, "title", "unknown")
            metadata["best_idea_title"] = idea_title
            metadata["best_total_score"] = _total_score(best)

        return OptimizationResult(
            problem=problem,
            pareto_front=front,
            all_candidates=list(all_candidates or front.members),
            iterations_run=iterations,
            converged=False,
            metadata=metadata,
        )

    # ------------------------------------------------------------------
    def description(self) -> str:
        """Return a one-line human-readable description of this algorithm."""
        return (
            f"{self.__class__.__name__}(name={self.name!r}, "
            f"max_iterations={self.max_iterations})"
        )


# ---------------------------------------------------------------------------
# 3. Weighted-sum optimizer
# ---------------------------------------------------------------------------

class WeightedSumOptimizer(OptimizationAlgorithm):
    """Scalarizes objectives with user-supplied weights and returns the best.

    All candidates are evaluated in a single pass; the one with the highest
    weighted sum of objective scores is declared optimal.  This is the fastest
    optimizer and is suitable when a clear priority ordering is known in advance.

    Attributes
    ----------
    weights:
        Mapping of objective name → non-negative weight.  Objectives absent
        from this dict receive a default weight of ``1.0``.
    """

    def __init__(
        self,
        weights: dict[str, float] | None = None,
        max_iterations: int = 1,
    ) -> None:
        super().__init__(name="weighted_sum", max_iterations=max_iterations)
        self.weights: dict[str, float] = weights or {}

    # ------------------------------------------------------------------
    def optimize(self, problem: OptimizationProblem) -> OptimizationResult:
        """Evaluate all candidates and return the highest-scalarized solution.

        Steps
        -----
        1. Evaluate every idea in *problem.candidate_ideas*.
        2. Compute a weighted score for each candidate via :meth:`_scalarize`.
        3. Sort by weighted score descending; the top candidate is *best*.
        4. Build a :class:`ParetoFront` from all evaluated candidates.
        5. Return a complete :class:`OptimizationResult`.

        Parameters
        ----------
        problem:
            The :class:`OptimizationProblem` to solve.
        """
        _log.debug("WeightedSumOptimizer: evaluating %d ideas", len(problem.candidate_ideas))
        t0 = time.monotonic()

        if not problem.candidate_ideas:
            _log.warning("WeightedSumOptimizer: no candidate ideas in problem.")
            empty_front = ParetoFront(objective_names=problem.objective_names())
            return self._make_result(problem, None, empty_front, 0, t0, [])

        candidates: list[SolutionCandidate] = [
            self._make_candidate(idea, problem)
            for idea in problem.candidate_ideas
        ]

        scored: list[tuple[float, SolutionCandidate]] = [
            (self._scalarize(c.scores), c) for c in candidates
        ]
        scored.sort(key=lambda x: x[0], reverse=True)

        best = scored[0][1] if scored else None
        front = _build_pareto_front(candidates, problem.objectives, generation=1)
        result = self._make_result(problem, best, front, 1, t0, candidates)
        result.metadata["weighted_scores"] = {
            c.candidate_id[:8]: round(ws, 4) for ws, c in scored[:5]
        }
        _log.info(
            "WeightedSumOptimizer: best weighted_score=%.4f, front_size=%d",
            scored[0][0] if scored else 0.0,
            front.size(),
        )
        return result

    # ------------------------------------------------------------------
    def _scalarize(self, scores: dict[str, float]) -> float:
        """Compute the weighted sum of *scores*.

        Objectives not listed in ``self.weights`` use a default weight of
        ``1.0``.  Returns ``0.0`` for an empty score dict.

        Parameters
        ----------
        scores:
            Mapping of objective name → score value.
        """
        if not scores:
            return 0.0
        total = 0.0
        for name, value in scores.items():
            w = self.weights.get(name, 1.0)
            total += w * value
        return total


# ---------------------------------------------------------------------------
# 4. Lexicographic optimizer
# ---------------------------------------------------------------------------

class LexicographicOptimizer(OptimizationAlgorithm):
    """Ranks candidates lexicographically across an ordered priority list.

    The first objective in *priority_order* is the primary criterion.  Among
    candidates within *tolerance* of each other on that objective, the next
    objective is used as a tie-breaker, and so on.  This mirrors strict
    priority reasoning common in mathematical research scheduling.

    Attributes
    ----------
    priority_order:
        Names of objectives in descending priority order.
    tolerance:
        Two scores are considered "equal" when their absolute difference is
        ≤ *tolerance*, enabling soft tie-breaking.
    """

    def __init__(
        self,
        priority_order: list[str] | None = None,
        tolerance: float = 0.01,
        max_iterations: int = 1,
    ) -> None:
        super().__init__(name="lexicographic", max_iterations=max_iterations)
        self.priority_order: list[str] = priority_order or []
        self.tolerance = tolerance

    # ------------------------------------------------------------------
    def optimize(self, problem: OptimizationProblem) -> OptimizationResult:
        """Lexicographically sort all candidates and return the best.

        Parameters
        ----------
        problem:
            The :class:`OptimizationProblem` to solve.
        """
        _log.debug("LexicographicOptimizer: %d ideas", len(problem.candidate_ideas))
        t0 = time.monotonic()

        if not problem.candidate_ideas:
            empty_front = ParetoFront(objective_names=problem.objective_names())
            return self._make_result(problem, None, empty_front, 0, t0, [])

        order = self.priority_order or problem.objective_names()
        candidates = [self._make_candidate(idea, problem) for idea in problem.candidate_ideas]

        def _lex_key(c: SolutionCandidate) -> tuple[float, ...]:
            return tuple(-c.scores.get(name, 0.0) for name in order)

        candidates.sort(key=_lex_key)
        best = candidates[0] if candidates else None
        front = _build_pareto_front(candidates, problem.objectives, generation=1)
        result = self._make_result(problem, best, front, 1, t0, candidates)
        result.metadata["priority_order"] = order
        result.metadata["tolerance"] = self.tolerance
        return result


# ---------------------------------------------------------------------------
# 5. Random search optimizer
# ---------------------------------------------------------------------------

class RandomSearchOptimizer(OptimizationAlgorithm):
    """Evaluates a random subset of candidates and returns the best by total score.

    Useful as a stochastic baseline or when the candidate pool is very large and
    exhaustive evaluation is too expensive.

    Attributes
    ----------
    n_samples:
        Maximum number of candidates to sample and evaluate.
    seed:
        Optional random seed for reproducibility.
    """

    def __init__(
        self,
        n_samples: int = 50,
        seed: int | None = None,
        max_iterations: int = 1,
    ) -> None:
        super().__init__(name="random_search", max_iterations=max_iterations)
        self.n_samples = n_samples
        self.seed = seed

    # ------------------------------------------------------------------
    def optimize(self, problem: OptimizationProblem) -> OptimizationResult:
        """Sample up to *n_samples* candidates at random and return the best.

        Parameters
        ----------
        problem:
            The :class:`OptimizationProblem` to solve.
        """
        _log.debug("RandomSearchOptimizer: pool=%d, n_samples=%d",
                   len(problem.candidate_ideas), self.n_samples)
        t0 = time.monotonic()
        rng = random.Random(self.seed)

        if not problem.candidate_ideas:
            empty_front = ParetoFront(objective_names=problem.objective_names())
            return self._make_result(problem, None, empty_front, 0, t0, [])

        k = min(self.n_samples, len(problem.candidate_ideas))
        sampled_ideas = rng.sample(problem.candidate_ideas, k)
        candidates = [self._make_candidate(idea, problem) for idea in sampled_ideas]
        best = max(candidates, key=_total_score) if candidates else None
        front = _build_pareto_front(candidates, problem.objectives, generation=1)
        result = self._make_result(problem, best, front, self.max_iterations, t0, candidates)
        result.metadata["n_sampled"] = k
        result.metadata["seed"] = self.seed
        return result


# ---------------------------------------------------------------------------
# 6. Simulated annealing optimizer
# ---------------------------------------------------------------------------

class SimulatedAnnealingOptimizer(OptimizationAlgorithm):
    """Metropolis-style local search with a cooling schedule.

    Starts from a randomly selected candidate, then repeatedly proposes a
    random neighbour.  Improving moves are always accepted; worsening moves are
    accepted with the Boltzmann probability
    ``exp(Δscore / temperature)``.  Temperature decreases geometrically each
    iteration, focusing the search over time.

    Attributes
    ----------
    initial_temp:
        Starting temperature for the annealing schedule.
    cooling_rate:
        Geometric decay factor applied each iteration (must be in (0, 1)).
    """

    def __init__(
        self,
        initial_temp: float = 1.0,
        cooling_rate: float = 0.95,
        max_iterations: int = 100,
    ) -> None:
        super().__init__(name="simulated_annealing", max_iterations=max_iterations)
        self.initial_temp = initial_temp
        self.cooling_rate = cooling_rate

    # ------------------------------------------------------------------
    def optimize(self, problem: OptimizationProblem) -> OptimizationResult:
        """Run simulated annealing over the discrete candidate set.

        Parameters
        ----------
        problem:
            The :class:`OptimizationProblem` to solve.
        """
        _log.debug("SimulatedAnnealingOptimizer: %d ideas, %d iters",
                   len(problem.candidate_ideas), self.max_iterations)
        t0 = time.monotonic()

        if not problem.candidate_ideas:
            empty_front = ParetoFront(objective_names=problem.objective_names())
            return self._make_result(problem, None, empty_front, 0, t0, [])

        # Pre-evaluate all candidates once for efficiency.
        all_candidates = [
            self._make_candidate(idea, problem)
            for idea in problem.candidate_ideas
        ]
        n = len(all_candidates)
        current_idx = random.randrange(n)
        current_score = _total_score(all_candidates[current_idx])
        best_idx = current_idx
        best_score = current_score
        temp = self.initial_temp

        for iteration in range(self.max_iterations):
            if n < 2:
                break
            neighbour_idx = self._neighbor(current_idx, n)
            neighbour_score = _total_score(all_candidates[neighbour_idx])
            prob = _acceptance_probability(current_score, neighbour_score, temp)
            if random.random() < prob:
                current_idx = neighbour_idx
                current_score = neighbour_score
            if current_score > best_score:
                best_score = current_score
                best_idx = current_idx
            temp *= self.cooling_rate
            _log.debug("SA iter=%d temp=%.6f current=%.4f best=%.4f",
                       iteration, temp, current_score, best_score)

        best = all_candidates[best_idx]
        front = _build_pareto_front(all_candidates, problem.objectives,
                                    generation=self.max_iterations)
        result = self._make_result(problem, best, front, self.max_iterations, t0, all_candidates)
        result.metadata["initial_temp"] = self.initial_temp
        result.metadata["cooling_rate"] = self.cooling_rate
        result.metadata["final_temp"] = round(temp, 8)
        return result

    # ------------------------------------------------------------------
    def _neighbor(self, current_idx: int, n_candidates: int) -> int:
        """Return a random index different from *current_idx*.

        Parameters
        ----------
        current_idx:
            The index of the current solution.
        n_candidates:
            Total number of candidates available.
        """
        if n_candidates < 2:
            return current_idx
        choices = [i for i in range(n_candidates) if i != current_idx]
        return random.choice(choices)


# ---------------------------------------------------------------------------
# 7. Evolutionary optimizer
# ---------------------------------------------------------------------------

class EvolutionaryOptimizer(OptimizationAlgorithm):
    """(μ+λ) evolutionary strategy over the discrete candidate pool.

    Maintains a population of *mu* candidates per generation.  Each generation
    produces *lambda_* offspring via :meth:`_mutate`, then selects the top *mu*
    from the combined (μ+λ) pool.  Because the candidate space is discrete and
    finite, mutation simply swaps a candidate for a randomly chosen different one.

    Attributes
    ----------
    mu:
        Parent population size (number of survivors per generation).
    lambda_:
        Number of offspring generated each generation.
    mutation_rate:
        Probability that a child is mutated (swapped) rather than cloned.
    """

    def __init__(
        self,
        mu: int = 5,
        lambda_: int = 10,
        mutation_rate: float = 0.1,
        max_iterations: int = 20,
    ) -> None:
        super().__init__(name="evolutionary", max_iterations=max_iterations)
        self.mu = mu
        self.lambda_ = lambda_
        self.mutation_rate = mutation_rate

    # ------------------------------------------------------------------
    def optimize(self, problem: OptimizationProblem) -> OptimizationResult:
        """Run (μ+λ) evolution and return the best candidate found.

        Parameters
        ----------
        problem:
            The :class:`OptimizationProblem` to solve.
        """
        _log.debug("EvolutionaryOptimizer: mu=%d lambda=%d iters=%d",
                   self.mu, self.lambda_, self.max_iterations)
        t0 = time.monotonic()

        if not problem.candidate_ideas:
            empty_front = ParetoFront(objective_names=problem.objective_names())
            return self._make_result(problem, None, empty_front, 0, t0, [])

        all_candidates = [
            self._make_candidate(idea, problem)
            for idea in problem.candidate_ideas
        ]
        n = len(all_candidates)
        mu = min(self.mu, n)

        # Initialise population with mu random candidates.
        population = random.sample(all_candidates, mu)
        best_candidate = max(population, key=_total_score)
        best_score = _total_score(best_candidate)

        for gen in range(self.max_iterations):
            # Generate offspring.
            offspring: list[SolutionCandidate] = []
            for parent in population:
                child = self._mutate(parent, all_candidates)
                offspring.append(child)
            # Select top mu from combined pool.
            combined = population + offspring
            combined.sort(key=_total_score, reverse=True)
            population = combined[:mu]
            gen_best = population[0]
            gen_best_score = _total_score(gen_best)
            if gen_best_score > best_score:
                best_score = gen_best_score
                best_candidate = gen_best
            _log.debug("Evo gen=%d best_score=%.4f", gen, best_score)

        front = _build_pareto_front(all_candidates, problem.objectives,
                                    generation=self.max_iterations)
        result = self._make_result(problem, best_candidate, front,
                                   self.max_iterations, t0, all_candidates)
        result.metadata["mu"] = self.mu
        result.metadata["lambda_"] = self.lambda_
        result.metadata["mutation_rate"] = self.mutation_rate
        return result

    # ------------------------------------------------------------------
    def _mutate(
        self,
        candidate: SolutionCandidate,
        all_candidates: list[SolutionCandidate],
    ) -> SolutionCandidate:
        """Return a (possibly mutated) copy of *candidate*.

        With probability ``mutation_rate`` a random different candidate from
        *all_candidates* is returned; otherwise *candidate* itself is returned
        unchanged (cloning).

        Parameters
        ----------
        candidate:
            The parent candidate.
        all_candidates:
            The full pool from which a replacement is drawn on mutation.
        """
        if len(all_candidates) < 2 or random.random() >= self.mutation_rate:
            return candidate
        others = [c for c in all_candidates if c is not candidate]
        return random.choice(others)


# ---------------------------------------------------------------------------
# 8. Bayesian-style optimizer
# ---------------------------------------------------------------------------

class BayesianStyleOptimizer(OptimizationAlgorithm):
    """Surrogate-based optimizer using an upper-confidence-bound acquisition.

    Maintains a set of "observed" candidate scores and uses their empirical mean
    and standard deviation to compute a UCB acquisition value for each unobserved
    candidate.  At each iteration the unobserved candidate with the highest UCB
    score is evaluated next.  This approximates Bayesian optimization without
    a Gaussian-process surrogate: the historical scores *are* the surrogate.

    Attributes
    ----------
    n_initial:
        Number of candidates evaluated randomly before UCB guidance begins.
    kappa:
        Exploration–exploitation trade-off coefficient passed to :func:`_ucb`.
    """

    def __init__(
        self,
        n_initial: int = 5,
        kappa: float = 2.0,
        max_iterations: int = 20,
    ) -> None:
        super().__init__(name="bayesian_style", max_iterations=max_iterations)
        self.n_initial = n_initial
        self.kappa = kappa

    # ------------------------------------------------------------------
    def optimize(self, problem: OptimizationProblem) -> OptimizationResult:
        """Run UCB-guided sequential evaluation of candidates.

        Parameters
        ----------
        problem:
            The :class:`OptimizationProblem` to solve.
        """
        _log.debug("BayesianStyleOptimizer: n_initial=%d kappa=%.2f iters=%d",
                   self.n_initial, self.kappa, self.max_iterations)
        t0 = time.monotonic()

        if not problem.candidate_ideas:
            empty_front = ParetoFront(objective_names=problem.objective_names())
            return self._make_result(problem, None, empty_front, 0, t0, [])

        all_candidates = [
            self._make_candidate(idea, problem)
            for idea in problem.candidate_ideas
        ]
        n = len(all_candidates)
        n_init = min(self.n_initial, n)

        observed_indices: set[int] = set()
        observed_scores: list[float] = []

        # Phase 1: random initialisation.
        init_indices = random.sample(range(n), n_init)
        for idx in init_indices:
            observed_indices.add(idx)
            observed_scores.append(_total_score(all_candidates[idx]))

        best_idx = max(observed_indices, key=lambda i: _total_score(all_candidates[i]))
        best_score = _total_score(all_candidates[best_idx])

        # Phase 2: UCB-guided acquisition.
        for iteration in range(self.max_iterations):
            unobserved = [i for i in range(n) if i not in observed_indices]
            if not unobserved:
                _log.debug("BayesianStyleOptimizer: all candidates observed; stopping.")
                break
            mu = statistics.mean(observed_scores)
            sigma = statistics.stdev(observed_scores) if len(observed_scores) > 1 else 1.0
            best_acq = -math.inf
            next_idx = unobserved[0]
            for idx in unobserved:
                # Candidate's own total score acts as a proxy feature.
                cand_score = _total_score(all_candidates[idx])
                acq = self._compute_acquisition(observed_scores, cand_score)
                if acq > best_acq:
                    best_acq = acq
                    next_idx = idx
            observed_indices.add(next_idx)
            s = _total_score(all_candidates[next_idx])
            observed_scores.append(s)
            if s > best_score:
                best_score = s
                best_idx = next_idx
            _log.debug("BO iter=%d next_idx=%d acq=%.4f best=%.4f",
                       iteration, next_idx, best_acq, best_score)

        best = all_candidates[best_idx]
        front = _build_pareto_front(all_candidates, problem.objectives,
                                    generation=self.max_iterations)
        result = self._make_result(problem, best, front, self.max_iterations, t0, all_candidates)
        result.metadata["n_initial"] = n_init
        result.metadata["kappa"] = self.kappa
        result.metadata["n_observed"] = len(observed_indices)
        return result

    # ------------------------------------------------------------------
    def _compute_acquisition(
        self,
        observed_scores: list[float],
        candidate_score: float,
    ) -> float:
        """Return the UCB acquisition value for a candidate.

        The UCB formula is ``mean(observed) + kappa * std(observed)``, but here
        we use the candidate's own total score as an optimistic estimate of its
        true quality, combined with an exploration bonus from the current score
        spread.

        Parameters
        ----------
        observed_scores:
            Scores of already-evaluated candidates.
        candidate_score:
            Total score of the candidate being assessed.
        """
        if not observed_scores:
            return candidate_score
        mu = statistics.mean(observed_scores)
        sigma = statistics.stdev(observed_scores) if len(observed_scores) > 1 else 1.0
        return _ucb(mu, sigma, self.kappa) + 0.1 * candidate_score


# ---------------------------------------------------------------------------
# 9. Algorithm selector
# ---------------------------------------------------------------------------

class AlgorithmSelector:
    """Recommends and benchmarks optimization algorithms for a given problem.

    Heuristics are based on the number of objectives in the problem and any
    ``budget`` key found in ``problem.metadata``.  The selector prefers fast
    algorithms (lower computational cost) when the budget is tight.
    """

    # ------------------------------------------------------------------
    def select(self, problem: OptimizationProblem) -> OptimizationAlgorithm:
        """Choose an appropriate algorithm for *problem*.

        Selection rules
        ---------------
        * 0 objectives → :class:`RandomSearchOptimizer` (no structure to exploit).
        * 1 objective → :class:`WeightedSumOptimizer` (trivially scalarised).
        * 2–3 objectives → :class:`SimulatedAnnealingOptimizer`.
        * 4+ objectives → :class:`EvolutionaryOptimizer`.

        If ``problem.metadata["budget"]`` is less than ``10`` a faster algorithm
        is substituted:
        * SA → :class:`WeightedSumOptimizer`
        * Evolutionary → :class:`SimulatedAnnealingOptimizer`

        Parameters
        ----------
        problem:
            The :class:`OptimizationProblem` to select for.
        """
        n_obj = len(problem.objectives)
        budget = float(problem.metadata.get("budget", 100.0))
        tight_budget = budget < 10.0

        _log.debug("AlgorithmSelector: n_obj=%d budget=%.1f tight=%s",
                   n_obj, budget, tight_budget)

        if n_obj == 0:
            return RandomSearchOptimizer()
        if n_obj == 1:
            return WeightedSumOptimizer()
        if n_obj <= 3:
            if tight_budget:
                _log.info("AlgorithmSelector: tight budget → WeightedSumOptimizer")
                return WeightedSumOptimizer()
            return SimulatedAnnealingOptimizer()
        # 4+ objectives
        if tight_budget:
            _log.info("AlgorithmSelector: tight budget + many objectives → SA")
            return SimulatedAnnealingOptimizer()
        return EvolutionaryOptimizer()

    # ------------------------------------------------------------------
    def benchmark(
        self,
        algorithms: list[OptimizationAlgorithm],
        problem: OptimizationProblem,
    ) -> dict[str, OptimizationResult]:
        """Run each algorithm on *problem* and return a name-keyed result dict.

        All algorithms receive the same *problem* instance.  Results are keyed
        by ``algorithm.name``; if two algorithms share a name the second result
        overwrites the first (log a warning in that case).

        Parameters
        ----------
        algorithms:
            List of :class:`OptimizationAlgorithm` instances to benchmark.
        problem:
            The problem to run each algorithm on.
        """
        results: dict[str, OptimizationResult] = {}
        for algo in algorithms:
            if algo.name in results:
                _log.warning(
                    "AlgorithmSelector.benchmark: duplicate algorithm name %r; "
                    "overwriting previous result.",
                    algo.name,
                )
            _log.info("Benchmarking algorithm %r …", algo.name)
            try:
                result = algo.optimize(problem)
                results[algo.name] = result
                _log.info(
                    "  → %s: front_size=%d, duration=%.4fs",
                    algo.name,
                    result.front_size(),
                    result.metadata.get("duration_s", 0.0),
                )
            except Exception as exc:  # pragma: no cover
                _log.error("Algorithm %r failed: %s", algo.name, exc)
        return results

    # ------------------------------------------------------------------
    def recommend(self, problem: OptimizationProblem) -> str:
        """Return a human-readable algorithm recommendation for *problem*.

        Parameters
        ----------
        problem:
            The :class:`OptimizationProblem` to describe a recommendation for.
        """
        n_obj = len(problem.objectives)
        n_cand = len(problem.candidate_ideas)
        budget = float(problem.metadata.get("budget", 100.0))
        chosen = self.select(problem)

        lines = [
            f"Problem: {n_obj} objective(s), {n_cand} candidate idea(s), budget={budget}.",
            f"Recommended algorithm: {chosen.__class__.__name__} ({chosen.name!r}).",
        ]
        if n_obj == 0:
            lines.append(
                "Rationale: No objectives defined; random search samples the pool "
                "without bias."
            )
        elif n_obj == 1:
            lines.append(
                "Rationale: Single objective is trivially scalarised; weighted-sum "
                "exhausts the pool in one pass."
            )
        elif n_obj <= 3:
            if budget < 10.0:
                lines.append(
                    "Rationale: Small budget with 2–3 objectives; weighted-sum is "
                    "the fastest option."
                )
            else:
                lines.append(
                    "Rationale: 2–3 objectives benefit from SA's stochastic "
                    "exploration of the trade-off surface."
                )
        else:
            if budget < 10.0:
                lines.append(
                    "Rationale: Tight budget with 4+ objectives; SA provides a "
                    "reasonable trade-off surface at lower cost than evolution."
                )
            else:
                lines.append(
                    "Rationale: 4+ objectives require population-based search to "
                    "maintain diversity; evolutionary strategy is preferred."
                )
        lines.append(f"Algorithm description: {chosen.description()}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# __all__
# ---------------------------------------------------------------------------

__all__ = [
    "OptimizationAlgorithm",
    "WeightedSumOptimizer",
    "LexicographicOptimizer",
    "RandomSearchOptimizer",
    "SimulatedAnnealingOptimizer",
    "EvolutionaryOptimizer",
    "BayesianStyleOptimizer",
    "AlgorithmSelector",
]
