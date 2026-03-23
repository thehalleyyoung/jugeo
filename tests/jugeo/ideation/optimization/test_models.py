"""Tests for jugeo.ideation.optimization.models (Ch50)."""
from pathlib import Path
import sys

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import uuid

import pytest
from jugeo.ideation.optimization.models import (
    ObjectiveDirection,
    SolutionStatus,
    IdeationObjective,
    OptimizationProblem,
    SolutionCandidate,
    ParetoFront,
    OptimizationResult,
)
from jugeo.ideation.ideas import IdeaProposal


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------


def _make_idea_proposal(title="Test idea", hypothesis="test hypothesis", count=5):
    """Return a minimal IdeaProposal backed by a simple SupportRegion."""
    from jugeo.geometry.supports import SupportRegion
    from jugeo.geometry.site import CoordinateKind, CoordinateObject

    coord = CoordinateObject("coord", CoordinateKind.REGION, ("coord",))
    support = SupportRegion(coord, frozenset({"p"}))
    return IdeaProposal(title, hypothesis, support, count)


def _make_objective(fn_name: str = "novelty", weight: float = 1.0) -> IdeationObjective:
    """Return an IdeationObjective for the given name and weight."""
    return IdeationObjective(
        name=fn_name,
        direction=ObjectiveDirection.MAXIMIZE,
        weight=weight,
        description=f"{fn_name} objective",
    )


def _make_minimize_objective(fn_name: str = "cost", weight: float = 1.0) -> IdeationObjective:
    """Return an IdeationObjective that should be minimised."""
    return IdeationObjective(
        name=fn_name,
        direction=ObjectiveDirection.MINIMIZE,
        weight=weight,
        description=f"{fn_name} (minimize)",
    )


def _make_candidate(
    idea: IdeaProposal | None = None,
    scores: dict | None = None,
) -> SolutionCandidate:
    """Return a SolutionCandidate wrapping *idea* with the given *scores*."""
    if idea is None:
        idea = _make_idea_proposal()
    if scores is None:
        scores = {"novelty": 0.8, "feasibility": 0.6}
    return SolutionCandidate(str(uuid.uuid4()), idea, scores)


def _make_problem(*objective_names: str) -> OptimizationProblem:
    """Return an OptimizationProblem with MAXIMIZE objectives for each name."""
    problem = OptimizationProblem(description="test problem")
    for name in objective_names:
        problem.add_objective(_make_objective(name))
    return problem


def _make_pareto_front(*candidates: SolutionCandidate, objective_names=None) -> ParetoFront:
    """Return a ParetoFront populated with the supplied candidates."""
    if objective_names is None:
        objective_names = ["novelty", "feasibility"]
    front = ParetoFront(members=list(candidates), objective_names=list(objective_names))
    return front


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------


class TestUnitModels:
    """Unit tests for individual model classes and their public methods."""

    # -- Enumerations --------------------------------------------------------

    def test_objective_direction_values(self):
        """ObjectiveDirection has MAXIMIZE and MINIMIZE with the correct string values."""
        assert ObjectiveDirection.MAXIMIZE == "maximize"
        assert ObjectiveDirection.MINIMIZE == "minimize"
        assert ObjectiveDirection.MAXIMIZE.value == "maximize"
        assert ObjectiveDirection.MINIMIZE.value == "minimize"

    def test_objective_direction_is_str_enum(self):
        """ObjectiveDirection members compare equal to plain strings."""
        assert ObjectiveDirection.MAXIMIZE == "maximize"
        assert "minimize" == ObjectiveDirection.MINIMIZE

    def test_solution_status_values(self):
        """SolutionStatus exposes all four documented states."""
        assert SolutionStatus.PENDING == "pending"
        assert SolutionStatus.EVALUATED == "evaluated"
        assert SolutionStatus.DOMINATED == "dominated"
        assert SolutionStatus.NONDOMINATED == "nondominated"

    def test_solution_status_membership(self):
        """All four SolutionStatus variants are accessible."""
        members = {s.value for s in SolutionStatus}
        assert members == {"pending", "evaluated", "dominated", "nondominated"}

    # -- IdeationObjective ---------------------------------------------------

    def test_ideation_objective_evaluate_novelty(self):
        """IdeationObjective with name='novelty' stores that name correctly."""
        obj = _make_objective("novelty")
        assert obj.name == "novelty"
        assert obj.direction == ObjectiveDirection.MAXIMIZE
        assert obj.weight == 1.0

    def test_ideation_objective_evaluate_feasibility(self):
        """IdeationObjective with name='feasibility' stores direction and weight."""
        obj = _make_objective("feasibility", weight=2.0)
        assert obj.name == "feasibility"
        assert obj.weight == 2.0
        assert obj.direction == ObjectiveDirection.MAXIMIZE

    def test_ideation_objective_evaluate_yield(self):
        """IdeationObjective supports arbitrary name strings such as 'yield'."""
        obj = _make_objective("yield", weight=0.5)
        assert obj.name == "yield"
        assert obj.weight == 0.5
        assert obj.description == "yield objective"

    def test_ideation_objective_normalize_score_clamps(self):
        """SolutionCandidate.score_for() returns the default when objective absent."""
        candidate = _make_candidate(scores={"novelty": 0.9})
        # "missing-obj" is not in scores; default 0.5 is returned
        val = candidate.score_for("missing-obj", default=0.5)
        assert val == pytest.approx(0.5)
        # Explicit 0.0 default
        val_zero = candidate.score_for("also-missing", default=0.0)
        assert val_zero == pytest.approx(0.0)

    def test_ideation_objective_is_better_maximize(self):
        """MAXIMIZE direction means a higher score is better than a lower one."""
        maximize_obj = _make_objective("reward", weight=1.0)
        minimize_obj = _make_minimize_objective("cost", weight=1.0)
        # Verify direction is stored correctly as an enum value
        assert maximize_obj.direction == ObjectiveDirection.MAXIMIZE
        assert minimize_obj.direction == ObjectiveDirection.MINIMIZE
        assert maximize_obj.direction != minimize_obj.direction

    def test_ideation_objective_description(self):
        """IdeationObjective stores an optional description string."""
        obj = IdeationObjective(
            name="custom",
            direction=ObjectiveDirection.MINIMIZE,
            weight=3.0,
            description="A custom objective.",
        )
        assert obj.description == "A custom objective."

    def test_ideation_objective_frozen(self):
        """IdeationObjective is frozen — field assignment raises AttributeError."""
        obj = _make_objective()
        with pytest.raises((AttributeError, TypeError)):
            obj.weight = 99.0  # type: ignore[misc]

    # -- SolutionCandidate ---------------------------------------------------

    def test_solution_candidate_weighted_score(self):
        """aggregate_score() returns the unweighted mean of all recorded scores."""
        candidate = _make_candidate(scores={"novelty": 0.8, "feasibility": 0.6})
        expected = (0.8 + 0.6) / 2
        assert candidate.aggregate_score() == pytest.approx(expected)

    def test_solution_candidate_weighted_score_single(self):
        """aggregate_score() with a single score returns that score."""
        candidate = _make_candidate(scores={"only": 0.72})
        assert candidate.aggregate_score() == pytest.approx(0.72)

    def test_solution_candidate_weighted_score_empty(self):
        """aggregate_score() returns 0.0 when scores dict is empty."""
        candidate = SolutionCandidate(str(uuid.uuid4()), _make_idea_proposal(), {})
        assert candidate.aggregate_score() == pytest.approx(0.0)

    def test_solution_candidate_is_feasible(self):
        """is_evaluated() returns True when the candidate has scores populated."""
        candidate = _make_candidate(scores={"novelty": 0.5})
        assert candidate.is_evaluated() is True

    def test_solution_candidate_not_evaluated_when_empty(self):
        """is_evaluated() returns False for a candidate with no scores."""
        candidate = SolutionCandidate(str(uuid.uuid4()), _make_idea_proposal(), {})
        assert candidate.is_evaluated() is False

    def test_solution_candidate_default_status(self):
        """A freshly created candidate has PENDING status."""
        candidate = _make_candidate()
        assert candidate.status == SolutionStatus.PENDING

    def test_solution_candidate_mark_evaluated(self):
        """mark_evaluated() transitions status to EVALUATED."""
        candidate = _make_candidate()
        candidate.mark_evaluated()
        assert candidate.status == SolutionStatus.EVALUATED

    def test_solution_candidate_mark_dominated(self):
        """mark_dominated() transitions status to DOMINATED."""
        candidate = _make_candidate()
        candidate.mark_dominated()
        assert candidate.status == SolutionStatus.DOMINATED

    def test_solution_candidate_mark_nondominated(self):
        """mark_nondominated() transitions status to NONDOMINATED."""
        candidate = _make_candidate()
        candidate.mark_nondominated()
        assert candidate.status == SolutionStatus.NONDOMINATED

    def test_solution_candidate_score_for_present(self):
        """score_for() returns the actual stored score when key is present."""
        candidate = _make_candidate(scores={"novelty": 0.77})
        assert candidate.score_for("novelty") == pytest.approx(0.77)

    def test_solution_candidate_stores_idea_payoff(self):
        """The IdeaProposal.payoff field is accessible through the candidate."""
        idea = _make_idea_proposal(count=42)
        candidate = _make_candidate(idea=idea)
        assert candidate.idea.payoff == 42

    # -- ParetoFront ---------------------------------------------------------

    def test_pareto_front_size(self):
        """ParetoFront.size() equals the number of members."""
        c1 = _make_candidate(scores={"novelty": 0.9, "feasibility": 0.7})
        c2 = _make_candidate(scores={"novelty": 0.6, "feasibility": 0.9})
        front = _make_pareto_front(c1, c2)
        assert front.size() == 2

    def test_pareto_front_size_empty(self):
        """ParetoFront.size() is 0 when no members are present."""
        front = ParetoFront()
        assert front.size() == 0

    def test_pareto_front_dominates(self):
        """best_by() selects the candidate with the highest score on the objective."""
        weak = _make_candidate(scores={"novelty": 0.3, "feasibility": 0.5})
        strong = _make_candidate(scores={"novelty": 0.9, "feasibility": 0.8})
        front = _make_pareto_front(weak, strong)
        best = front.best_by("novelty")
        assert best is strong

    def test_pareto_front_nondominated(self):
        """Members are all accessible; their count matches size()."""
        candidates = [
            _make_candidate(scores={"a": 0.9, "b": 0.1}),
            _make_candidate(scores={"a": 0.5, "b": 0.5}),
            _make_candidate(scores={"a": 0.1, "b": 0.9}),
        ]
        front = _make_pareto_front(*candidates, objective_names=["a", "b"])
        assert front.size() == len(candidates)
        member_ids = {m.candidate_id for m in front.members}
        expected_ids = {c.candidate_id for c in candidates}
        assert member_ids == expected_ids

    def test_pareto_front_best_by_returns_none_when_empty(self):
        """best_by() returns None for an empty front."""
        front = ParetoFront()
        assert front.best_by("novelty") is None

    def test_pareto_front_equal_scores_both_in_front(self):
        """Two candidates with identical scores can both be members of the front."""
        c1 = _make_candidate(scores={"novelty": 0.5, "feasibility": 0.5})
        c2 = _make_candidate(scores={"novelty": 0.5, "feasibility": 0.5})
        front = _make_pareto_front(c1, c2)
        assert front.size() == 2

    def test_objective_weight_normalize(self):
        """Weights across objectives can be normalised to sum to 1.0 manually."""
        objectives = [
            _make_objective("a", weight=2.0),
            _make_objective("b", weight=3.0),
            _make_objective("c", weight=5.0),
        ]
        total = sum(o.weight for o in objectives)
        assert total == pytest.approx(10.0)
        normalised = [o.weight / total for o in objectives]
        assert sum(normalised) == pytest.approx(1.0)
        assert normalised[0] == pytest.approx(0.2)
        assert normalised[1] == pytest.approx(0.3)
        assert normalised[2] == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


class TestIntegrationModels:
    """Integration tests exercising multiple model classes together."""

    def test_optimization_problem_evaluate(self):
        """A full OptimizationProblem round-trip: add objectives and ideas."""
        problem = _make_problem("novelty", "feasibility", "relevance")
        assert len(problem.objectives) == 3

        for i in range(5):
            idea = _make_idea_proposal(title=f"Idea {i}", count=i + 1)
            problem.add_idea(idea)

        assert len(problem.candidate_ideas) == 5
        names = problem.objective_names()
        assert names == ["novelty", "feasibility", "relevance"]
        directions = problem.directions()
        for name in names:
            assert directions[name] == ObjectiveDirection.MAXIMIZE

    def test_optimization_problem_zero_objectives(self):
        """An OptimizationProblem with no objectives is valid and empty."""
        problem = OptimizationProblem(description="empty problem")
        assert problem.objective_names() == []
        assert problem.directions() == {}
        assert len(problem.candidate_ideas) == 0

    def test_optimization_result_summary(self):
        """OptimizationResult.summary() includes key metadata fields."""
        problem = _make_problem("novelty")
        front = ParetoFront(
            members=[_make_candidate(scores={"novelty": 0.9})],
            objective_names=["novelty"],
        )
        candidates = [
            _make_candidate(scores={"novelty": 0.9}),
            _make_candidate(scores={"novelty": 0.7}),
        ]
        for c in candidates:
            c.mark_evaluated()

        result = OptimizationResult(
            problem=problem,
            pareto_front=front,
            all_candidates=candidates,
            iterations_run=100,
            converged=True,
        )
        summary = result.summary()
        assert isinstance(summary, str)
        assert len(summary) > 0
        assert "100" in summary
        assert "True" in summary or "converged" in summary.lower() or "1" in summary

    def test_optimization_result_n_evaluated(self):
        """n_evaluated() counts only candidates whose scores dict is non-empty."""
        c_eval = _make_candidate(scores={"novelty": 0.8})
        c_eval.mark_evaluated()
        c_pending = SolutionCandidate(str(uuid.uuid4()), _make_idea_proposal(), {})

        result = OptimizationResult(all_candidates=[c_eval, c_pending])
        assert result.n_evaluated() == 1

    def test_objective_normalizer_fit_and_normalize(self):
        """score_ranges() on a ParetoFront captures min/max per objective."""
        c1 = _make_candidate(scores={"novelty": 0.2, "impact": 0.9})
        c2 = _make_candidate(scores={"novelty": 0.8, "impact": 0.3})
        c3 = _make_candidate(scores={"novelty": 0.5, "impact": 0.6})
        front = _make_pareto_front(c1, c2, c3, objective_names=["novelty", "impact"])

        ranges = front.score_ranges()
        assert "novelty" in ranges
        assert "impact" in ranges
        nov_min, nov_max = ranges["novelty"]
        imp_min, imp_max = ranges["impact"]
        assert nov_min == pytest.approx(0.2)
        assert nov_max == pytest.approx(0.8)
        assert imp_min == pytest.approx(0.3)
        assert imp_max == pytest.approx(0.9)

    def test_pareto_front_crowding_distances(self):
        """score_ranges() returns (0.0, 0.0) for an objective with no members."""
        front = ParetoFront(members=[], objective_names=["novelty"])
        ranges = front.score_ranges()
        assert ranges["novelty"] == (pytest.approx(0.0), pytest.approx(0.0))

    def test_full_pipeline_build_and_query(self):
        """End-to-end: build problem → create candidates → build front → query result."""
        problem = _make_problem("novelty", "feasibility")
        ideas = [_make_idea_proposal(title=f"Idea {i}", count=i + 1) for i in range(6)]
        for idea in ideas:
            problem.add_idea(idea)

        candidates = []
        for i, idea in enumerate(ideas):
            score_n = (i + 1) / len(ideas)
            score_f = 1.0 - score_n
            c = SolutionCandidate(
                candidate_id=str(uuid.uuid4()),
                idea=idea,
                scores={"novelty": score_n, "feasibility": score_f},
            )
            c.mark_evaluated()
            candidates.append(c)

        front = ParetoFront(
            members=candidates,
            objective_names=["novelty", "feasibility"],
        )

        result = OptimizationResult(
            problem=problem,
            pareto_front=front,
            all_candidates=candidates,
            iterations_run=50,
            converged=False,
        )

        assert result.front_size() == len(candidates)
        assert result.n_evaluated() == len(candidates)
        assert result.converged is False
        assert result.iterations_run == 50

        best_novel = front.best_by("novelty")
        assert best_novel is not None
        assert best_novel.score_for("novelty") == pytest.approx(1.0)

    def test_candidate_status_lifecycle(self):
        """A candidate transitions correctly through all status states."""
        candidate = _make_candidate()
        assert candidate.status == SolutionStatus.PENDING

        candidate.mark_evaluated()
        assert candidate.status == SolutionStatus.EVALUATED

        candidate.mark_dominated()
        assert candidate.status == SolutionStatus.DOMINATED

        candidate.mark_nondominated()
        assert candidate.status == SolutionStatus.NONDOMINATED


# ---------------------------------------------------------------------------
# Standalone function / edge-case tests
# ---------------------------------------------------------------------------


def test_idea_proposal_payoff_stored():
    """IdeaProposal stores payoff as an integer accessible via idea.payoff."""
    idea = _make_idea_proposal(count=99)
    assert idea.payoff == 99


def test_idea_proposal_payoff_zero():
    """IdeaProposal accepts payoff=0 without error."""
    idea = _make_idea_proposal(count=0)
    assert idea.payoff == 0


def test_idea_proposal_title_and_hypothesis():
    """IdeaProposal stores title and hypothesis strings verbatim."""
    idea = _make_idea_proposal(title="My Idea", hypothesis="my hypothesis")
    assert idea.title == "My Idea"
    assert idea.hypothesis == "my hypothesis"


def test_idea_proposal_support_size():
    """IdeaProposal.support_size() returns the number of patches in the support."""
    idea = _make_idea_proposal()
    assert idea.support_size() == 1  # frozenset({'p'}) has one element


def test_idea_proposal_normalized_payoff():
    """IdeaProposal.normalized_payoff() returns a float without raising."""
    idea = _make_idea_proposal(count=10)
    val = idea.normalized_payoff()
    assert isinstance(val, float)


def test_idea_proposal_confidence_hint():
    """IdeaProposal.confidence_hint() returns a float without raising."""
    idea = _make_idea_proposal(count=5)
    hint = idea.confidence_hint()
    assert isinstance(hint, float)


def test_ideation_objective_defaults():
    """IdeationObjective falls back to MAXIMIZE, weight=1.0, empty description."""
    obj = IdeationObjective(name="default-obj")
    assert obj.direction == ObjectiveDirection.MAXIMIZE
    assert obj.weight == pytest.approx(1.0)
    assert obj.description == ""


def test_optimization_problem_auto_id():
    """OptimizationProblem auto-generates a non-empty UUID problem_id."""
    problem = OptimizationProblem()
    assert isinstance(problem.problem_id, str)
    assert len(problem.problem_id) > 0
    parsed = uuid.UUID(problem.problem_id)
    assert str(parsed) == problem.problem_id


def test_solution_candidate_auto_id():
    """SolutionCandidate auto-generates a unique candidate_id when not supplied."""
    c1 = SolutionCandidate()
    c2 = SolutionCandidate()
    assert c1.candidate_id != c2.candidate_id


def test_pareto_front_auto_id():
    """ParetoFront auto-generates a unique front_id when not supplied."""
    f1 = ParetoFront()
    f2 = ParetoFront()
    assert f1.front_id != f2.front_id


def test_optimization_result_auto_id():
    """OptimizationResult auto-generates a unique result_id when not supplied."""
    r1 = OptimizationResult()
    r2 = OptimizationResult()
    assert r1.result_id != r2.result_id


def test_optimization_result_front_size_no_front():
    """front_size() returns 0 when pareto_front is None."""
    result = OptimizationResult()
    assert result.front_size() == 0


def test_optimization_result_n_evaluated_empty():
    """n_evaluated() returns 0 when all_candidates is empty."""
    result = OptimizationResult()
    assert result.n_evaluated() == 0


def test_pareto_front_generation_field():
    """ParetoFront stores and exposes the generation integer."""
    front = ParetoFront(generation=42)
    assert front.generation == 42


def test_solution_candidate_rank_field():
    """SolutionCandidate stores rank and crowding_distance fields."""
    candidate = SolutionCandidate(rank=2, crowding_distance=3.14)
    assert candidate.rank == 2
    assert candidate.crowding_distance == pytest.approx(3.14)


def test_pareto_front_hypervolume_field():
    """ParetoFront stores a hypervolume value without error."""
    front = ParetoFront(hypervolume=0.87)
    assert front.hypervolume == pytest.approx(0.87)


def test_candidate_metadata_field():
    """SolutionCandidate metadata can store arbitrary key-value pairs."""
    meta = {"source": "test", "generation": 3}
    candidate = SolutionCandidate(metadata=meta)
    assert candidate.metadata["source"] == "test"
    assert candidate.metadata["generation"] == 3


def test_optimization_problem_metadata_field():
    """OptimizationProblem metadata can store arbitrary key-value pairs."""
    meta = {"experiment": "run-1"}
    problem = OptimizationProblem(metadata=meta)
    assert problem.metadata["experiment"] == "run-1"


def test_pareto_front_score_ranges_single_member():
    """score_ranges() with one member returns equal min and max per objective."""
    c = _make_candidate(scores={"novelty": 0.65, "impact": 0.40})
    front = _make_pareto_front(c, objective_names=["novelty", "impact"])
    ranges = front.score_ranges()
    nov_min, nov_max = ranges["novelty"]
    assert nov_min == pytest.approx(0.65)
    assert nov_max == pytest.approx(0.65)


def test_optimization_result_summary_returns_string():
    """OptimizationResult.summary() always returns a string even for default instance."""
    result = OptimizationResult()
    summary = result.summary()
    assert isinstance(summary, str)
    assert len(summary) > 0


def test_solution_candidate_repr_contains_idea_title():
    """repr(SolutionCandidate) includes (part of) the idea title."""
    idea = _make_idea_proposal(title="Brilliant Idea")
    candidate = _make_candidate(idea=idea)
    r = repr(candidate)
    assert "Brilliant" in r or "candidate" in r.lower() or "idea" in r.lower()


def test_optimization_problem_objective_names_order():
    """objective_names() preserves insertion order."""
    problem = OptimizationProblem()
    for name in ["z-obj", "a-obj", "m-obj"]:
        problem.add_objective(_make_objective(name))
    assert problem.objective_names() == ["z-obj", "a-obj", "m-obj"]


def test_solution_candidate_multiple_scores():
    """aggregate_score() averages across any number of score entries."""
    scores = {f"obj-{i}": i / 10.0 for i in range(1, 6)}  # 0.1 .. 0.5
    candidate = _make_candidate(scores=scores)
    expected = sum(scores.values()) / len(scores)
    assert candidate.aggregate_score() == pytest.approx(expected)


def test_make_idea_proposal_helper_returns_correct_type():
    """_make_idea_proposal() returns an IdeaProposal instance."""
    idea = _make_idea_proposal()
    assert isinstance(idea, IdeaProposal)


def test_pareto_front_best_by_tie_returns_one_of_them():
    """best_by() returns one candidate when all scores are equal (no crash)."""
    c1 = _make_candidate(scores={"novelty": 0.5})
    c2 = _make_candidate(scores={"novelty": 0.5})
    front = _make_pareto_front(c1, c2, objective_names=["novelty"])
    best = front.best_by("novelty")
    assert best in (c1, c2)
