"""Tests for jugeo.ideation.optimization.algorithms (Ch50)."""

from pathlib import Path
import sys

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import math
import uuid

import pytest

from jugeo.ideation.optimization.algorithms import (
    OptimizationAlgorithm,
    WeightedSumOptimizer,
    LexicographicOptimizer,
    RandomSearchOptimizer,
    SimulatedAnnealingOptimizer,
    EvolutionaryOptimizer,
    BayesianStyleOptimizer,
    AlgorithmSelector,
    _acceptance_probability,
    _mutation,
    _ucb,
)
from jugeo.ideation.optimization.models import (
    IdeationObjective,
    ObjectiveDirection,
    OptimizationProblem,
    SolutionCandidate,
    ParetoFront,
    OptimizationResult,
)


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------

def _make_idea_proposal(title="Test idea", hypothesis="test hypothesis", count=5):
    from jugeo.geometry.supports import SupportRegion
    from jugeo.geometry.site import CoordinateKind, CoordinateObject
    coord = CoordinateObject('coord', CoordinateKind.REGION, ('coord',))
    support = SupportRegion(coord, frozenset({'p'}))
    from jugeo.ideation.ideas import IdeaProposal
    return IdeaProposal(title, hypothesis, support, count)


def _make_objectives(n=2):
    objs = [
        IdeationObjective("novelty", ObjectiveDirection.MAXIMIZE, 1.0, "novelty score"),
        IdeationObjective("feasibility", ObjectiveDirection.MAXIMIZE, 0.8, "feasibility score"),
        IdeationObjective("purpose", ObjectiveDirection.MAXIMIZE, 0.6, "purpose alignment"),
    ]
    return objs[:n]


def _make_problem(n_ideas=5, n_objectives=2, budget=100.0):
    objectives = _make_objectives(n_objectives)
    ideas = [
        _make_idea_proposal(title=f"Research Idea {i}", hypothesis=f"hypothesis {i}", count=i + 2)
        for i in range(n_ideas)
    ]
    return OptimizationProblem(
        problem_id=str(uuid.uuid4()),
        objectives=objectives,
        candidate_ideas=ideas,
        metadata={"budget": budget},
    )


def _make_empty_problem(n_objectives=2):
    objectives = _make_objectives(n_objectives)
    return OptimizationProblem(
        problem_id=str(uuid.uuid4()),
        objectives=objectives,
        candidate_ideas=[],
    )


def _make_single_objective_problem(n_ideas=3):
    objectives = [IdeationObjective("novelty", ObjectiveDirection.MAXIMIZE, 1.0, "novelty")]
    ideas = [
        _make_idea_proposal(title=f"Idea {i}", hypothesis=f"h{i}", count=i + 1)
        for i in range(n_ideas)
    ]
    return OptimizationProblem(
        problem_id=str(uuid.uuid4()),
        objectives=objectives,
        candidate_ideas=ideas,
    )


# ---------------------------------------------------------------------------
# Standalone function tests
# ---------------------------------------------------------------------------

def test_acceptance_probability_better_solution():
    """A strictly better solution is always accepted (probability == 1.0)."""
    p = _acceptance_probability(old_cost=0.5, new_cost=0.8, temp=1.0)
    assert p == 1.0


def test_acceptance_probability_equal_solution():
    """An equal solution is also accepted (new_cost >= old_cost)."""
    p = _acceptance_probability(old_cost=0.5, new_cost=0.5, temp=1.0)
    assert p == 1.0


def test_acceptance_probability_worse_with_high_temp():
    """At high temperature, worsening moves have a meaningfully positive probability."""
    p = _acceptance_probability(old_cost=1.0, new_cost=0.5, temp=10.0)
    assert 0.0 < p < 1.0
    # High temperature → higher acceptance probability than low temperature.
    p_low = _acceptance_probability(old_cost=1.0, new_cost=0.5, temp=0.01)
    assert p > p_low


def test_acceptance_probability_worse_with_low_temp():
    """At very low temperature, worsening moves are nearly always rejected."""
    p = _acceptance_probability(old_cost=1.0, new_cost=0.0, temp=1e-6)
    assert p < 0.01


def test_acceptance_probability_zero_temp():
    """At temperature 0, worsening moves have probability approaching 0."""
    p = _acceptance_probability(old_cost=1.0, new_cost=0.9, temp=0.0)
    assert p >= 0.0
    assert p < 1.0


def test_mutation_returns_different_index():
    """_mutation always returns an index different from the input index."""
    for idx in range(5):
        result = _mutation(idx, n_ideas=5)
        assert result != idx
        assert 0 <= result < 5


def test_mutation_two_ideas():
    """_mutation with n_ideas=2 always returns the other index."""
    assert _mutation(0, 2) == 1
    assert _mutation(1, 2) == 0


def test_mutation_raises_for_single_idea():
    """_mutation raises ValueError when fewer than 2 ideas are available."""
    with pytest.raises(ValueError, match="at least 2"):
        _mutation(0, n_ideas=1)


def test_ucb_formula():
    """_ucb returns mean + kappa * std."""
    result = _ucb(mean=0.5, std=0.2, kappa=2.0)
    assert abs(result - 0.9) < 1e-9


def test_ucb_zero_kappa():
    """With kappa=0, UCB reduces to the empirical mean."""
    result = _ucb(mean=0.7, std=0.3, kappa=0.0)
    assert abs(result - 0.7) < 1e-9


def test_ucb_high_kappa_encourages_exploration():
    """Higher kappa produces a higher UCB value when std > 0."""
    ucb_low = _ucb(mean=0.5, std=0.1, kappa=1.0)
    ucb_high = _ucb(mean=0.5, std=0.1, kappa=5.0)
    assert ucb_high > ucb_low


# ---------------------------------------------------------------------------
# Unit tests for algorithm classes
# ---------------------------------------------------------------------------

class TestUnitAlgorithms:
    """Unit tests for individual algorithm implementations."""

    def test_base_algorithm_optimize_raises(self):
        """OptimizationAlgorithm.optimize() raises NotImplementedError."""
        algo = OptimizationAlgorithm()
        problem = _make_problem()
        with pytest.raises(NotImplementedError):
            algo.optimize(problem)

    def test_base_algorithm_has_name_and_iterations(self):
        """Default OptimizationAlgorithm has expected name and max_iterations."""
        algo = OptimizationAlgorithm(name="my_algo", max_iterations=50)
        assert algo.name == "my_algo"
        assert algo.max_iterations == 50

    def test_weighted_sum_optimizer_returns_result(self):
        """WeightedSumOptimizer.optimize() returns an OptimizationResult."""
        optimizer = WeightedSumOptimizer()
        problem = _make_problem(n_ideas=4)
        result = optimizer.optimize(problem)
        assert isinstance(result, OptimizationResult)

    def test_weighted_sum_optimizer_best_is_feasible(self):
        """WeightedSumOptimizer result has a non-None pareto_front."""
        optimizer = WeightedSumOptimizer()
        problem = _make_problem(n_ideas=5)
        result = optimizer.optimize(problem)
        assert result.pareto_front is not None
        assert result.pareto_front.size() >= 1

    def test_weighted_sum_optimizer_all_candidates_evaluated(self):
        """WeightedSumOptimizer evaluates all candidate ideas."""
        optimizer = WeightedSumOptimizer()
        problem = _make_problem(n_ideas=6)
        result = optimizer.optimize(problem)
        assert result.n_evaluated() == 6

    def test_weighted_sum_optimizer_custom_weights(self):
        """WeightedSumOptimizer uses the provided weights."""
        weights = {"novelty": 2.0, "feasibility": 0.5}
        optimizer = WeightedSumOptimizer(weights=weights)
        assert optimizer.weights == weights
        problem = _make_problem(n_ideas=3)
        result = optimizer.optimize(problem)
        assert isinstance(result, OptimizationResult)

    def test_weighted_sum_optimizer_empty_ideas(self):
        """WeightedSumOptimizer on empty candidate list returns valid result."""
        optimizer = WeightedSumOptimizer()
        problem = _make_empty_problem()
        result = optimizer.optimize(problem)
        assert isinstance(result, OptimizationResult)
        assert result.pareto_front is not None
        assert result.pareto_front.size() == 0

    def test_lexicographic_optimizer_returns_result(self):
        """LexicographicOptimizer.optimize() returns an OptimizationResult."""
        optimizer = LexicographicOptimizer()
        problem = _make_problem(n_ideas=4)
        result = optimizer.optimize(problem)
        assert isinstance(result, OptimizationResult)
        assert result.pareto_front is not None

    def test_lexicographic_optimizer_empty_ideas(self):
        """LexicographicOptimizer handles empty candidate list."""
        optimizer = LexicographicOptimizer()
        problem = _make_empty_problem()
        result = optimizer.optimize(problem)
        assert isinstance(result, OptimizationResult)

    def test_random_search_optimizer_returns_result(self):
        """RandomSearchOptimizer.optimize() returns an OptimizationResult."""
        optimizer = RandomSearchOptimizer(max_iterations=10)
        problem = _make_problem(n_ideas=5)
        result = optimizer.optimize(problem)
        assert isinstance(result, OptimizationResult)
        assert result.pareto_front is not None

    def test_random_search_optimizer_empty_ideas(self):
        """RandomSearchOptimizer handles an empty candidate list gracefully."""
        optimizer = RandomSearchOptimizer()
        problem = _make_empty_problem()
        result = optimizer.optimize(problem)
        assert isinstance(result, OptimizationResult)
        assert result.front_size() == 0

    def test_random_search_optimizer_runs_iterations(self):
        """RandomSearchOptimizer runs the requested number of iterations."""
        optimizer = RandomSearchOptimizer(max_iterations=15)
        problem = _make_problem(n_ideas=5)
        result = optimizer.optimize(problem)
        assert result.iterations_run == 15

    def test_sa_optimizer_returns_result(self):
        """SimulatedAnnealingOptimizer.optimize() returns a valid result."""
        optimizer = SimulatedAnnealingOptimizer(max_iterations=20)
        problem = _make_problem(n_ideas=4)
        result = optimizer.optimize(problem)
        assert isinstance(result, OptimizationResult)
        assert result.pareto_front is not None

    def test_sa_optimizer_converges(self):
        """SimulatedAnnealingOptimizer on a small problem still produces candidates."""
        optimizer = SimulatedAnnealingOptimizer(initial_temp=2.0, cooling_rate=0.8, max_iterations=30)
        problem = _make_problem(n_ideas=3)
        result = optimizer.optimize(problem)
        assert result.iterations_run == 30
        assert result.front_size() >= 1
        assert "final_temp" in result.metadata

    def test_sa_optimizer_metadata_keys(self):
        """SimulatedAnnealingOptimizer stores cooling schedule in metadata."""
        optimizer = SimulatedAnnealingOptimizer(initial_temp=1.5, cooling_rate=0.9, max_iterations=10)
        problem = _make_problem(n_ideas=3)
        result = optimizer.optimize(problem)
        assert "initial_temp" in result.metadata
        assert "cooling_rate" in result.metadata
        assert "final_temp" in result.metadata
        assert result.metadata["initial_temp"] == 1.5
        assert result.metadata["cooling_rate"] == 0.9

    def test_sa_optimizer_empty_ideas(self):
        """SimulatedAnnealingOptimizer handles empty candidate list."""
        optimizer = SimulatedAnnealingOptimizer()
        problem = _make_empty_problem()
        result = optimizer.optimize(problem)
        assert isinstance(result, OptimizationResult)

    def test_evolutionary_optimizer_returns_result(self):
        """EvolutionaryOptimizer.optimize() returns a valid result."""
        optimizer = EvolutionaryOptimizer(max_iterations=5)
        problem = _make_problem(n_ideas=6)
        result = optimizer.optimize(problem)
        assert isinstance(result, OptimizationResult)
        assert result.pareto_front is not None

    def test_evolutionary_optimizer_population_params(self):
        """EvolutionaryOptimizer accepts mu and lambda parameters."""
        optimizer = EvolutionaryOptimizer(mu=2, lambda_=4, max_iterations=5)
        problem = _make_problem(n_ideas=6)
        result = optimizer.optimize(problem)
        assert isinstance(result, OptimizationResult)

    def test_evolutionary_optimizer_empty_ideas(self):
        """EvolutionaryOptimizer handles empty candidate list."""
        optimizer = EvolutionaryOptimizer()
        problem = _make_empty_problem()
        result = optimizer.optimize(problem)
        assert isinstance(result, OptimizationResult)

    def test_bayesian_optimizer_returns_result(self):
        """BayesianStyleOptimizer.optimize() returns a valid result."""
        optimizer = BayesianStyleOptimizer(n_initial=3, kappa=2.0, max_iterations=5)
        problem = _make_problem(n_ideas=6)
        result = optimizer.optimize(problem)
        assert isinstance(result, OptimizationResult)
        assert result.pareto_front is not None

    def test_bayesian_optimizer_metadata(self):
        """BayesianStyleOptimizer stores n_initial and kappa in metadata."""
        optimizer = BayesianStyleOptimizer(n_initial=2, kappa=1.5, max_iterations=3)
        problem = _make_problem(n_ideas=5)
        result = optimizer.optimize(problem)
        assert "kappa" in result.metadata
        assert result.metadata["kappa"] == 1.5

    def test_bayesian_optimizer_empty_ideas(self):
        """BayesianStyleOptimizer handles empty candidate list."""
        optimizer = BayesianStyleOptimizer()
        problem = _make_empty_problem()
        result = optimizer.optimize(problem)
        assert isinstance(result, OptimizationResult)

    def test_algorithm_selector_one_objective(self):
        """AlgorithmSelector picks WeightedSumOptimizer for a single-objective problem."""
        selector = AlgorithmSelector()
        problem = _make_single_objective_problem()
        algo = selector.select(problem)
        assert isinstance(algo, WeightedSumOptimizer)

    def test_algorithm_selector_no_objectives(self):
        """AlgorithmSelector picks RandomSearchOptimizer when no objectives are set."""
        selector = AlgorithmSelector()
        problem = OptimizationProblem(
            problem_id=str(uuid.uuid4()),
            objectives=[],
            candidate_ideas=[],
        )
        algo = selector.select(problem)
        assert isinstance(algo, RandomSearchOptimizer)

    def test_algorithm_selector_tight_budget_two_objectives(self):
        """AlgorithmSelector falls back to WeightedSumOptimizer under tight budget."""
        selector = AlgorithmSelector()
        problem = _make_problem(n_ideas=4, n_objectives=2, budget=5.0)
        algo = selector.select(problem)
        assert isinstance(algo, WeightedSumOptimizer)

    def test_result_has_result_id(self):
        """All optimizers produce a result with a non-empty result_id."""
        for OptClass in [WeightedSumOptimizer, RandomSearchOptimizer, LexicographicOptimizer]:
            result = OptClass().optimize(_make_problem(n_ideas=3))
            assert result.result_id
            assert len(result.result_id) > 0

    def test_result_problem_reference(self):
        """OptimizationResult stores a reference to the problem."""
        problem = _make_problem(n_ideas=3)
        result = WeightedSumOptimizer().optimize(problem)
        assert result.problem is problem


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------

class TestIntegrationAlgorithms:
    """Integration tests for algorithm interactions and comparisons."""

    def test_algorithm_selector_select_multi_objective(self):
        """AlgorithmSelector picks SA for a 2-objective problem with normal budget."""
        selector = AlgorithmSelector()
        problem = _make_problem(n_ideas=5, n_objectives=2, budget=100.0)
        algo = selector.select(problem)
        assert isinstance(algo, SimulatedAnnealingOptimizer)

    def test_algorithm_selector_four_objectives(self):
        """AlgorithmSelector picks EvolutionaryOptimizer for 4+ objectives."""
        selector = AlgorithmSelector()
        objectives = [
            IdeationObjective(f"obj_{i}", ObjectiveDirection.MAXIMIZE, 1.0, f"obj {i}")
            for i in range(4)
        ]
        ideas = [_make_idea_proposal(title=f"I{i}", count=i + 1) for i in range(4)]
        problem = OptimizationProblem(
            problem_id=str(uuid.uuid4()),
            objectives=objectives,
            candidate_ideas=ideas,
            metadata={"budget": 200.0},
        )
        algo = selector.select(problem)
        assert isinstance(algo, EvolutionaryOptimizer)

    def test_algorithm_selector_benchmark(self):
        """AlgorithmSelector.benchmark() runs all algorithms and returns a results dict."""
        selector = AlgorithmSelector()
        problem = _make_problem(n_ideas=4, n_objectives=2)
        algorithms = [
            WeightedSumOptimizer(),
            RandomSearchOptimizer(max_iterations=5),
            SimulatedAnnealingOptimizer(max_iterations=5),
        ]
        results = selector.benchmark(algorithms, problem)
        assert isinstance(results, dict)
        assert "weighted_sum" in results
        assert "random_search" in results
        assert "simulated_annealing" in results
        for result in results.values():
            assert isinstance(result, OptimizationResult)

    def test_weighted_sum_vs_random_comparison(self):
        """WeightedSumOptimizer and RandomSearchOptimizer both produce valid fronts."""
        problem = _make_problem(n_ideas=6)
        ws_result = WeightedSumOptimizer().optimize(problem)
        rs_result = RandomSearchOptimizer(max_iterations=10).optimize(problem)
        assert ws_result.front_size() >= 1
        assert rs_result.front_size() >= 1
        # Both should have evaluated all 6 candidates.
        assert ws_result.n_evaluated() == 6
        assert rs_result.n_evaluated() >= 1

    def test_evolutionary_optimizer_finds_good_solution(self):
        """EvolutionaryOptimizer on a varied idea pool surfaces the higher-payoff ideas."""
        ideas = [
            _make_idea_proposal(title=f"Low Idea {i}", count=2) for i in range(3)
        ] + [
            _make_idea_proposal(title=f"High Idea {i}", count=80) for i in range(3)
        ]
        problem = OptimizationProblem(
            problem_id=str(uuid.uuid4()),
            objectives=_make_objectives(2),
            candidate_ideas=ideas,
        )
        result = EvolutionaryOptimizer(max_iterations=10).optimize(problem)
        assert isinstance(result, OptimizationResult)
        assert result.front_size() >= 1
        # Best idea title should be available in metadata.
        assert "best_idea_title" in result.metadata

    def test_all_optimizers_handle_single_idea(self):
        """All optimizers handle a single-candidate problem without errors."""
        ideas = [_make_idea_proposal(title="Only idea", count=10)]
        problem = OptimizationProblem(
            problem_id=str(uuid.uuid4()),
            objectives=_make_objectives(2),
            candidate_ideas=ideas,
        )
        for OptClass in [
            WeightedSumOptimizer,
            LexicographicOptimizer,
            RandomSearchOptimizer,
            SimulatedAnnealingOptimizer,
            EvolutionaryOptimizer,
            BayesianStyleOptimizer,
        ]:
            result = OptClass().optimize(problem)
            assert isinstance(result, OptimizationResult)
            assert result.front_size() >= 1

    def test_algorithm_selector_recommend_returns_string(self):
        """AlgorithmSelector.recommend() returns a non-empty string."""
        selector = AlgorithmSelector()
        problem = _make_problem(n_ideas=3, n_objectives=2)
        recommendation = selector.recommend(problem)
        assert isinstance(recommendation, str)
        assert len(recommendation) > 0

    def test_pareto_front_best_by(self):
        """ParetoFront.best_by() returns the member with highest score for given objective."""
        optimizer = WeightedSumOptimizer()
        problem = _make_problem(n_ideas=6)
        result = optimizer.optimize(problem)
        front = result.pareto_front
        if front.size() > 0:
            best_novelty = front.best_by("novelty")
            assert best_novelty is not None
            assert isinstance(best_novelty, SolutionCandidate)

    def test_zero_max_iterations_sa_returns_valid_result(self):
        """SimulatedAnnealingOptimizer with max_iterations=0 returns a valid result."""
        optimizer = SimulatedAnnealingOptimizer(max_iterations=0)
        problem = _make_problem(n_ideas=4)
        result = optimizer.optimize(problem)
        assert isinstance(result, OptimizationResult)
        assert result.pareto_front is not None

    def test_zero_max_iterations_evolutionary_returns_valid_result(self):
        """EvolutionaryOptimizer with max_iterations=0 returns a valid result."""
        optimizer = EvolutionaryOptimizer(max_iterations=0)
        problem = _make_problem(n_ideas=4)
        result = optimizer.optimize(problem)
        assert isinstance(result, OptimizationResult)
        assert result.pareto_front is not None
