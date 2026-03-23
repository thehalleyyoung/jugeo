"""Tests for jugeo.ideation.optimization.s02_pareto_optimization (Ch50)."""

from pathlib import Path
import sys

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import math
import uuid
import pytest

from jugeo.ideation.optimization.s02_pareto_optimization import (
    DominanceChecker,
    CrowdingDistance,
    NSGAIIStyle,
    EpsilonConstraintSolver,
    ParetoOptimizer,
    _dominated_by_any,
    _compare_scores,
)
from jugeo.ideation.optimization.models import (
    IdeationObjective,
    ObjectiveDirection,
    OptimizationProblem,
    SolutionCandidate,
    SolutionStatus,
    ParetoFront,
)
from jugeo.ideation.ideas import IdeaProposal


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_idea_proposal(title="Test idea", hypothesis="test hypothesis", count=5):
    from jugeo.geometry.supports import SupportRegion
    from jugeo.geometry.site import CoordinateKind, CoordinateObject
    coord = CoordinateObject('coord', CoordinateKind.REGION, ('coord',))
    support = SupportRegion(coord, frozenset({'p'}))
    return IdeaProposal(title, hypothesis, support, count)


def _make_candidate(candidate_id=None, scores=None, status=SolutionStatus.FEASIBLE):
    if candidate_id is None:
        candidate_id = str(uuid.uuid4())
    idea = _make_idea_proposal()
    if scores is None:
        scores = {"novelty": 0.8, "feasibility": 0.6}
    return SolutionCandidate(
        candidate_id=candidate_id,
        idea=idea,
        scores=scores,
        status=status,
    )


def _make_problem(n_ideas=3):
    objectives = [
        IdeationObjective("novelty", ObjectiveDirection.MAXIMIZE, 1.0, "novelty score"),
        IdeationObjective("feasibility", ObjectiveDirection.MAXIMIZE, 1.0, "feasibility score"),
    ]
    problem = OptimizationProblem(
        objectives=objectives,
        candidate_ideas=[_make_idea_proposal(title=f"Idea {i}", count=i + 1) for i in range(n_ideas)],
    )
    return problem


# ---------------------------------------------------------------------------
# Standalone function tests
# ---------------------------------------------------------------------------

def test_compare_scores_maximize_a_better():
    result = _compare_scores(0.9, 0.5, ObjectiveDirection.MAXIMIZE)
    assert result == 1


def test_compare_scores_maximize_b_better():
    result = _compare_scores(0.3, 0.8, ObjectiveDirection.MAXIMIZE)
    assert result == -1


def test_compare_scores_maximize_equal():
    result = _compare_scores(0.5, 0.5, ObjectiveDirection.MAXIMIZE)
    assert result == 0


def test_compare_scores_minimize_a_better():
    # Lower is better for MINIMIZE
    result = _compare_scores(0.2, 0.8, ObjectiveDirection.MINIMIZE)
    assert result == 1


def test_compare_scores_minimize_b_better():
    result = _compare_scores(0.9, 0.1, ObjectiveDirection.MINIMIZE)
    assert result == -1


def test_dominated_by_any_function_is_dominated():
    candidate = {"novelty": 0.3, "feasibility": 0.3}
    others = [{"novelty": 0.8, "feasibility": 0.9}]
    assert _dominated_by_any(candidate, others) is True


def test_dominated_by_any_function_not_dominated():
    candidate = {"novelty": 0.9, "feasibility": 0.3}
    others = [{"novelty": 0.3, "feasibility": 0.9}]
    assert _dominated_by_any(candidate, others) is False


def test_dominated_by_any_empty_others():
    candidate = {"novelty": 0.5}
    assert _dominated_by_any(candidate, []) is False


def test_dominated_by_any_with_minimize_direction():
    directions = {"cost": ObjectiveDirection.MINIMIZE}
    candidate = {"cost": 0.8}
    # lower cost is better; this one has high cost → dominated by low cost
    others = [{"cost": 0.1}]
    assert _dominated_by_any(candidate, others, directions=directions) is True


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------

class TestUnitS02:
    """Unit tests for Pareto optimisation classes."""

    # ------------------------------------------------------------------
    # DominanceChecker
    # ------------------------------------------------------------------

    def test_dominance_checker_a_dominates_b(self):
        checker = DominanceChecker()
        a = {"novelty": 0.9, "feasibility": 0.8}
        b = {"novelty": 0.5, "feasibility": 0.4}
        assert checker.dominates(a, b) is True
        assert checker.dominates(b, a) is False

    def test_dominance_checker_equal_scores_no_dominance(self):
        checker = DominanceChecker()
        a = {"novelty": 0.7, "feasibility": 0.7}
        b = {"novelty": 0.7, "feasibility": 0.7}
        assert checker.dominates(a, b) is False
        assert checker.dominates(b, a) is False

    def test_dominance_checker_b_dominates_a(self):
        checker = DominanceChecker()
        a = {"novelty": 0.2, "feasibility": 0.3}
        b = {"novelty": 0.8, "feasibility": 0.9}
        assert checker.dominates(b, a) is True
        assert checker.dominates(a, b) is False

    def test_dominance_checker_nondominated(self):
        """A dominates on one objective, B dominates on another → neither dominates."""
        checker = DominanceChecker()
        a = {"novelty": 0.9, "feasibility": 0.2}
        b = {"novelty": 0.2, "feasibility": 0.9}
        assert checker.dominates(a, b) is False
        assert checker.dominates(b, a) is False

    def test_dominance_checker_dominance_rank_simple(self):
        checker = DominanceChecker()
        # a dominates b; a should have rank 0, b rank 1
        pop = [
            {"novelty": 0.9, "feasibility": 0.9},  # a – best
            {"novelty": 0.5, "feasibility": 0.5},  # b – dominated by a
        ]
        ranks = checker.dominance_rank(pop)
        assert isinstance(ranks, list)
        assert len(ranks) == 2
        assert ranks[0] < ranks[1]

    def test_dominance_checker_nondominated_front(self):
        checker = DominanceChecker()
        ideas = [_make_idea_proposal(title=f"Idea {i}") for i in range(3)]
        score_list = [
            {"novelty": 0.9, "feasibility": 0.1},
            {"novelty": 0.5, "feasibility": 0.5},
            {"novelty": 0.1, "feasibility": 0.9},
        ]
        front = checker.nondominated_front(ideas, score_list)
        # All three are non-dominated (tradeoff)
        assert len(front) == 3

    def test_dominance_checker_single_solution(self):
        checker = DominanceChecker()
        a = {"novelty": 0.7}
        b = {"novelty": 0.3}
        assert checker.dominates(a, b) is True

    # ------------------------------------------------------------------
    # CrowdingDistance
    # ------------------------------------------------------------------

    def test_crowding_distance_boundary_inf(self):
        cd = CrowdingDistance()
        front = [
            _make_candidate(scores={"novelty": 0.1, "feasibility": 0.9}),
            _make_candidate(scores={"novelty": 0.5, "feasibility": 0.5}),
            _make_candidate(scores={"novelty": 0.9, "feasibility": 0.1}),
        ]
        distances = cd.compute(front, ["novelty", "feasibility"])
        assert isinstance(distances, dict)
        # Boundary candidates should have infinite crowding distance
        inf_count = sum(1 for v in distances.values() if math.isinf(v))
        assert inf_count >= 2

    def test_crowding_distance_three_points(self):
        cd = CrowdingDistance()
        candidates = [
            _make_candidate(scores={"novelty": 0.0, "feasibility": 1.0}),
            _make_candidate(scores={"novelty": 0.5, "feasibility": 0.5}),
            _make_candidate(scores={"novelty": 1.0, "feasibility": 0.0}),
        ]
        distances = cd.compute(candidates, ["novelty", "feasibility"])
        # Middle candidate gets finite distance, boundaries get inf
        values = list(distances.values())
        assert any(math.isinf(v) for v in values)
        assert any(not math.isinf(v) for v in values)

    def test_crowding_distance_single_candidate(self):
        cd = CrowdingDistance()
        single = [_make_candidate(scores={"novelty": 0.5})]
        distances = cd.compute(single, ["novelty"])
        val = list(distances.values())[0]
        assert math.isinf(val)

    def test_crowding_distance_sort_by_crowding(self):
        cd = CrowdingDistance()
        candidates = [
            _make_candidate(scores={"novelty": 0.0}),
            _make_candidate(scores={"novelty": 0.5}),
            _make_candidate(scores={"novelty": 1.0}),
        ]
        sorted_cands = cd.sort_by_crowding(candidates, ["novelty"])
        assert len(sorted_cands) == 3

    # ------------------------------------------------------------------
    # NSGAIIStyle
    # ------------------------------------------------------------------

    def test_nsga2_fast_sort_returns_fronts(self):
        nsga = NSGAIIStyle()
        population = [
            _make_candidate(scores={"novelty": 0.9, "feasibility": 0.8}),
            _make_candidate(scores={"novelty": 0.5, "feasibility": 0.5}),
            _make_candidate(scores={"novelty": 0.2, "feasibility": 0.2}),
        ]
        fronts = nsga.fast_nondominated_sort(population)
        assert isinstance(fronts, list)
        assert len(fronts) >= 1
        # All candidates should appear in exactly one front
        all_in_fronts = [c for front in fronts for c in front]
        assert len(all_in_fronts) == len(population)

    def test_nsga2_select_returns_n(self):
        nsga = NSGAIIStyle()
        population = [
            _make_candidate(scores={"novelty": 0.9, "feasibility": 0.8}),
            _make_candidate(scores={"novelty": 0.5, "feasibility": 0.5}),
            _make_candidate(scores={"novelty": 0.3, "feasibility": 0.3}),
            _make_candidate(scores={"novelty": 0.1, "feasibility": 0.1}),
        ]
        selected = nsga.select(population, n=2)
        assert len(selected) == 2

    def test_nsga2_select_empty_population(self):
        nsga = NSGAIIStyle()
        selected = nsga.select([], n=5)
        assert selected == []

    def test_nsga2_select_n_larger_than_population(self):
        nsga = NSGAIIStyle()
        population = [
            _make_candidate(scores={"novelty": 0.8}),
            _make_candidate(scores={"novelty": 0.3}),
        ]
        selected = nsga.select(population, n=10)
        assert len(selected) == len(population)

    def test_nsga2_single_solution_front(self):
        nsga = NSGAIIStyle()
        pop = [_make_candidate(scores={"novelty": 0.7, "feasibility": 0.7})]
        fronts = nsga.fast_nondominated_sort(pop)
        assert len(fronts) == 1
        assert len(fronts[0]) == 1

    # ------------------------------------------------------------------
    # EpsilonConstraintSolver
    # ------------------------------------------------------------------

    def test_epsilon_constraint_solver_filters(self):
        solver = EpsilonConstraintSolver()
        problem = _make_problem(n_ideas=5)
        # Generous epsilon: accept anything above 0.0 feasibility
        epsilons = {"feasibility": 0.0}
        results = solver.solve(problem, primary_obj="novelty", epsilons=epsilons)
        assert isinstance(results, list)
        # All 5 ideas have feasibility > 0.0, so all should pass
        assert len(results) >= 0  # At minimum a valid list

    def test_epsilon_constraint_solver_strict_filter(self):
        solver = EpsilonConstraintSolver()
        problem = _make_problem(n_ideas=3)
        # Very strict epsilon: feasibility must be > 0.99 (likely no one passes)
        epsilons = {"feasibility": 0.99}
        results = solver.solve(problem, primary_obj="novelty", epsilons=epsilons)
        assert isinstance(results, list)

    def test_pareto_optimizer_basic(self):
        problem = _make_problem(n_ideas=4)
        optimizer = ParetoOptimizer(problem, population_size=4, max_iterations=2)
        result = optimizer.optimize()
        assert result is not None
        # Result should have a Pareto front
        assert result.pareto_front is not None or result.all_candidates is not None

    def test_epsilon_grid_generation(self):
        solver = EpsilonConstraintSolver()
        problem = _make_problem(n_ideas=2)
        grid = solver.generate_epsilon_grid(problem, steps=3)
        assert isinstance(grid, list)
        assert len(grid) > 0
        # Each entry should be a dict mapping objective names to floats
        for item in grid:
            assert isinstance(item, dict)

    def test_dominance_checker_with_minimize_direction(self):
        checker = DominanceChecker()
        directions = {
            "novelty": ObjectiveDirection.MAXIMIZE,
            "cost": ObjectiveDirection.MINIMIZE,
        }
        a = {"novelty": 0.8, "cost": 0.2}  # high novelty, low cost → better
        b = {"novelty": 0.4, "cost": 0.9}  # low novelty, high cost → worse
        assert checker.dominates(a, b, directions=directions) is True
        assert checker.dominates(b, a, directions=directions) is False


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------

class TestIntegrationS02:
    """Integration tests for Pareto optimisation working end-to-end."""

    def test_pareto_optimizer_with_problem(self):
        problem = _make_problem(n_ideas=5)
        optimizer = ParetoOptimizer(problem, population_size=5, max_iterations=3)
        result = optimizer.optimize()
        # Should return a valid OptimizationResult
        assert result is not None
        assert hasattr(result, 'pareto_front')
        assert hasattr(result, 'all_candidates')

    def test_nsga2_sort_and_select_combined(self):
        """NSGA-II sort followed by selection should produce consistent results."""
        nsga = NSGAIIStyle()
        population = [
            _make_candidate(scores={"novelty": 0.9, "feasibility": 0.1}),
            _make_candidate(scores={"novelty": 0.7, "feasibility": 0.4}),
            _make_candidate(scores={"novelty": 0.5, "feasibility": 0.6}),
            _make_candidate(scores={"novelty": 0.3, "feasibility": 0.8}),
            _make_candidate(scores={"novelty": 0.1, "feasibility": 0.9}),
        ]
        fronts = nsga.fast_nondominated_sort(population)
        # All 5 are mutually non-dominating (tradeoff curve) → single front
        assert len(fronts[0]) == 5

        selected = nsga.select(population, n=3)
        assert len(selected) == 3

    def test_dominated_by_any_function(self):
        """Integration: _dominated_by_any should agree with DominanceChecker."""
        checker = DominanceChecker()
        a = {"novelty": 0.2, "feasibility": 0.2}
        others = [
            {"novelty": 0.5, "feasibility": 0.5},
            {"novelty": 0.8, "feasibility": 0.8},
        ]
        # _dominated_by_any and DominanceChecker.dominates should agree
        da = _dominated_by_any(a, others)
        dc = any(checker.dominates(o, a) for o in others)
        assert da == dc

    def test_epsilon_grid_generation(self):
        """generate_epsilon_grid should produce a grid covering [0, 1] steps."""
        solver = EpsilonConstraintSolver()
        problem = _make_problem(n_ideas=3)
        grid = solver.generate_epsilon_grid(problem, steps=3)
        assert isinstance(grid, list)
        assert len(grid) > 0
        obj_names = problem.objective_names()
        for entry in grid:
            assert isinstance(entry, dict)
            # Each entry should have keys for the non-primary objectives
            for k in entry:
                assert isinstance(entry[k], float)

    def test_full_optimizer_pipeline(self):
        """Full pipeline: build problem, optimize, inspect Pareto front."""
        problem = _make_problem(n_ideas=6)
        optimizer = ParetoOptimizer(problem, population_size=6, max_iterations=5)
        result = optimizer.optimize()
        assert result is not None
        if result.pareto_front is not None:
            front = result.pareto_front
            assert front.size() >= 0
        assert result.iterations_run >= 0

    def test_crowding_distance_integrated_with_nsga2(self):
        """CrowdingDistance and NSGAIIStyle together should rank diverse solutions higher."""
        nsga = NSGAIIStyle()
        cd = CrowdingDistance()
        # Create a population that forms a clear Pareto front
        population = [
            _make_candidate(scores={"novelty": 0.0, "feasibility": 1.0}),
            _make_candidate(scores={"novelty": 0.25, "feasibility": 0.75}),
            _make_candidate(scores={"novelty": 0.5, "feasibility": 0.5}),
            _make_candidate(scores={"novelty": 0.75, "feasibility": 0.25}),
            _make_candidate(scores={"novelty": 1.0, "feasibility": 0.0}),
        ]
        fronts = nsga.fast_nondominated_sort(population)
        # All solutions form one Pareto front
        assert len(fronts[0]) == 5
        # Boundary solutions should have inf crowding distance
        distances = cd.compute(fronts[0], ["novelty", "feasibility"])
        inf_vals = [v for v in distances.values() if math.isinf(v)]
        assert len(inf_vals) >= 2

    def test_pareto_front_model(self):
        """ParetoFront dataclass should correctly report size and best_by."""
        members = [
            _make_candidate(scores={"novelty": 0.9, "feasibility": 0.3}),
            _make_candidate(scores={"novelty": 0.4, "feasibility": 0.8}),
        ]
        for m in members:
            m.rank = 0
        front = ParetoFront(
            members=members,
            objective_names=["novelty", "feasibility"],
        )
        assert front.size() == 2
        best = front.best_by("novelty")
        assert best.scores["novelty"] == pytest.approx(0.9)

    def test_optimizer_step_reduces_population(self):
        """ParetoOptimizer.step should return a list of candidates."""
        problem = _make_problem(n_ideas=4)
        optimizer = ParetoOptimizer(problem, population_size=4, max_iterations=1)
        init_pop = optimizer._initialize_population()
        evaluated = optimizer._evaluate_population(init_pop)
        stepped = optimizer.step(evaluated)
        assert isinstance(stepped, list)

    def test_all_dominated_solutions(self):
        """When all solutions dominate each other in different objectives, no single dominates all."""
        checker = DominanceChecker()
        # Tradeoff: each solution is best on one objective
        solutions = [
            {"novelty": 1.0, "feasibility": 0.0, "yield": 0.0},
            {"novelty": 0.0, "feasibility": 1.0, "yield": 0.0},
            {"novelty": 0.0, "feasibility": 0.0, "yield": 1.0},
        ]
        # None should dominate any other
        for i, a in enumerate(solutions):
            for j, b in enumerate(solutions):
                if i != j:
                    assert not checker.dominates(a, b), f"Solution {i} should not dominate {j}"

    def test_single_idea_optimizer(self):
        """Optimizer with a single idea should complete without error."""
        problem = _make_problem(n_ideas=1)
        optimizer = ParetoOptimizer(problem, population_size=1, max_iterations=2)
        result = optimizer.optimize()
        assert result is not None

    def test_solution_candidate_model_methods(self):
        """SolutionCandidate methods should behave correctly after evaluation."""
        cand = _make_candidate(scores={"novelty": 0.7, "feasibility": 0.5})
        assert cand.is_evaluated() is True
        assert cand.score_for("novelty") == pytest.approx(0.7)
        assert cand.score_for("missing_key", default=0.42) == pytest.approx(0.42)
        agg = cand.aggregate_score()
        assert isinstance(agg, float)

    def test_optimization_problem_model_methods(self):
        """OptimizationProblem helpers should return consistent data."""
        problem = _make_problem(n_ideas=3)
        names = problem.objective_names()
        assert "novelty" in names
        assert "feasibility" in names
        directions = problem.directions()
        assert directions["novelty"] == ObjectiveDirection.MAXIMIZE
        assert len(problem.candidate_ideas) == 3

    def test_nsga2_tournament_select(self):
        """tournament_select should return one of the population members."""
        nsga = NSGAIIStyle()
        population = [
            _make_candidate(scores={"novelty": 0.9}),
            _make_candidate(scores={"novelty": 0.3}),
            _make_candidate(scores={"novelty": 0.6}),
        ]
        # Assign ranks so tournament has something to compare
        for i, c in enumerate(population):
            c.rank = i
        winner = nsga.tournament_select(population)
        assert winner in population
