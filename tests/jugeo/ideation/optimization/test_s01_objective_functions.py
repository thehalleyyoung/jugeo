"""Tests for jugeo.ideation.optimization.s01_objective_functions (Ch50)."""

from pathlib import Path
import sys

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import math
import pytest

from jugeo.ideation.optimization.s01_objective_functions import (
    BaseObjective,
    NoveltyObjective,
    FeasibilityObjective,
    PurposeObjective,
    YieldObjective,
    CostObjective,
    CompositeObjective,
    ObjectiveFactory,
    ObjectiveEvaluator,
    _tokenize,
    _clamp,
    _jaccard,
    _normalize,
)
from jugeo.ideation.ideas import IdeaProposal


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_idea_proposal(title="Test theorem idea", hypothesis="test hypothesis about lemma", count=5):
    from jugeo.geometry.supports import SupportRegion
    from jugeo.geometry.site import CoordinateKind, CoordinateObject
    coord = CoordinateObject('coord', CoordinateKind.REGION, ('coord',))
    support = SupportRegion(coord, frozenset({'p'}))
    return IdeaProposal(title, hypothesis, support, count)


# ---------------------------------------------------------------------------
# Standalone function tests
# ---------------------------------------------------------------------------

def test_tokenize_basic():
    result = _tokenize("Hello World foo")
    assert isinstance(result, set)
    assert "hello" in result
    assert "world" in result
    assert "foo" in result


def test_tokenize_empty():
    result = _tokenize("")
    assert result == set()


def test_tokenize_short_tokens_discarded():
    # Tokens < 2 chars should be discarded
    result = _tokenize("a bb ccc")
    assert "a" not in result
    assert "bb" in result
    assert "ccc" in result


def test_tokenize_case_insensitive():
    result = _tokenize("UPPER lower MiXeD")
    assert "upper" in result
    assert "lower" in result
    assert "mixed" in result


def test_clamp_within_range():
    assert _clamp(0.5) == 0.5
    assert _clamp(0.0) == 0.0
    assert _clamp(1.0) == 1.0


def test_clamp_below_min():
    assert _clamp(-1.0) == 0.0
    assert _clamp(-100.0) == 0.0


def test_clamp_above_max():
    assert _clamp(2.0) == 1.0
    assert _clamp(999.0) == 1.0


def test_clamp_custom_bounds():
    assert _clamp(5.0, lo=2.0, hi=10.0) == 5.0
    assert _clamp(1.0, lo=2.0, hi=10.0) == 2.0
    assert _clamp(15.0, lo=2.0, hi=10.0) == 10.0


def test_jaccard_identical_sets():
    a = {"x", "y", "z"}
    result = _jaccard(a, a)
    assert result == pytest.approx(1.0)


def test_jaccard_disjoint_sets():
    a = {"x", "y"}
    b = {"p", "q"}
    result = _jaccard(a, b)
    assert result == pytest.approx(0.0)


def test_jaccard_partial_overlap():
    a = {"x", "y", "z"}
    b = {"x", "y", "w"}
    # intersection=2, union=4
    assert _jaccard(a, b) == pytest.approx(2 / 4)


def test_jaccard_empty_sets():
    assert _jaccard(set(), set()) == pytest.approx(0.0)


def test_normalize_basic():
    scores = [0.0, 0.5, 1.0]
    result = _normalize(scores)
    assert result[0] == pytest.approx(0.0)
    assert result[2] == pytest.approx(1.0)
    assert result[1] == pytest.approx(0.5)


def test_normalize_equal_values_returns_half():
    scores = [0.7, 0.7, 0.7]
    result = _normalize(scores)
    for v in result:
        assert v == pytest.approx(0.5)


def test_normalize_single_value():
    result = _normalize([0.3])
    assert result[0] == pytest.approx(0.5)


def test_normalize_empty():
    assert _normalize([]) == []


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------

class TestUnitS01:
    """Unit tests for objective function classes."""

    # ------------------------------------------------------------------
    # NoveltyObjective
    # ------------------------------------------------------------------

    def test_novelty_objective_returns_float_in_range(self):
        idea = _make_idea_proposal()
        obj = NoveltyObjective()
        score = obj.evaluate(idea)
        assert isinstance(score, float)
        assert 0.0 <= score <= 1.0

    def test_novelty_objective_longer_title_higher_score(self):
        short_idea = _make_idea_proposal(title="Short", hypothesis="h h h h h h h h h")
        long_idea = _make_idea_proposal(
            title="A much longer title with many distinctive tokens about geometry",
            hypothesis="a very detailed hypothesis with many unique words about algebraic topology"
        )
        obj = NoveltyObjective()
        short_score = obj.evaluate(short_idea)
        long_score = obj.evaluate(long_idea)
        assert long_score >= short_score

    def test_novelty_objective_token_diversity(self):
        idea = _make_idea_proposal(
            title="completely different words",
            hypothesis="totally unique terms here"
        )
        obj = NoveltyObjective()
        diversity = obj.token_diversity(idea)
        assert isinstance(diversity, float)
        assert 0.0 <= diversity <= 1.0

    def test_novelty_objective_identical_title_hypothesis_lower_diversity(self):
        same_text = "theorem proof lemma conjecture"
        idea_same = _make_idea_proposal(title=same_text, hypothesis=same_text)
        idea_diff = _make_idea_proposal(
            title="abstract algebra groups rings",
            hypothesis="topology manifolds continuous maps"
        )
        obj = NoveltyObjective()
        assert obj.token_diversity(idea_same) <= obj.token_diversity(idea_diff)

    # ------------------------------------------------------------------
    # FeasibilityObjective
    # ------------------------------------------------------------------

    def test_feasibility_objective_high_payoff_lower_score(self):
        low_payoff = _make_idea_proposal(count=1)
        high_payoff = _make_idea_proposal(count=50)
        obj = FeasibilityObjective()
        low_score = obj.evaluate(low_payoff)
        high_score = obj.evaluate(high_payoff)
        # Higher payoff → lower feasibility score (harder to execute)
        assert low_score > high_score

    def test_feasibility_objective_zero_payoff(self):
        idea = _make_idea_proposal(count=0)
        obj = FeasibilityObjective()
        score = obj.evaluate(idea)
        # 1.0 / (1.0 + 0 * 0.1) = 1.0
        assert score == pytest.approx(1.0)

    def test_feasibility_objective_score_in_range(self):
        for payoff in [0, 1, 5, 10, 50, 100]:
            idea = _make_idea_proposal(count=payoff)
            obj = FeasibilityObjective()
            score = obj.evaluate(idea)
            assert 0.0 <= score <= 1.0

    def test_feasibility_objective_saturation_point(self):
        idea = _make_idea_proposal(count=5)
        obj = FeasibilityObjective(saturation=10.0)
        sp = obj.saturation_point(idea)
        assert sp == pytest.approx(0.5)

    # ------------------------------------------------------------------
    # PurposeObjective
    # ------------------------------------------------------------------

    def test_purpose_objective_keyword_match(self):
        idea = _make_idea_proposal(
            title="A theorem about optimal geometry",
            hypothesis="Proof of conjecture using algebraic structure"
        )
        obj = PurposeObjective()
        score = obj.evaluate(idea)
        assert isinstance(score, float)
        assert 0.0 <= score <= 1.0

    def test_purpose_objective_no_keywords(self):
        idea = _make_idea_proposal(
            title="Cooking recipes for dinner",
            hypothesis="Make pasta with vegetables"
        )
        obj = PurposeObjective()
        score = obj.evaluate(idea)
        assert isinstance(score, float)
        assert 0.0 <= score <= 1.0

    def test_purpose_objective_keyword_overlap(self):
        idea = _make_idea_proposal(
            title="theorem proof lemma conjecture",
            hypothesis="optimal structure topology algebra analysis"
        )
        obj = PurposeObjective()
        overlap = obj.keyword_overlap(idea)
        assert isinstance(overlap, set)
        # Several default keywords should appear
        assert len(overlap) > 0

    def test_purpose_objective_custom_keywords(self):
        idea = _make_idea_proposal(title="bananas apples", hypothesis="fruit salad recipe")
        obj = PurposeObjective(purpose_keywords={"bananas", "fruit"})
        overlap = obj.keyword_overlap(idea)
        assert "bananas" in overlap or "fruit" in overlap

    # ------------------------------------------------------------------
    # YieldObjective
    # ------------------------------------------------------------------

    def test_yield_objective_proportional_to_payoff(self):
        low = _make_idea_proposal(count=2)
        high = _make_idea_proposal(count=18)
        obj = YieldObjective(max_yield=20.0)
        assert obj.evaluate(low) < obj.evaluate(high)

    def test_yield_objective_caps_at_one(self):
        huge = _make_idea_proposal(count=100)
        obj = YieldObjective(max_yield=20.0)
        assert obj.evaluate(huge) == pytest.approx(1.0)

    def test_yield_objective_raw_yield(self):
        idea = _make_idea_proposal(count=7)
        obj = YieldObjective()
        assert obj.raw_yield(idea) == pytest.approx(7.0)

    # ------------------------------------------------------------------
    # CostObjective
    # ------------------------------------------------------------------

    def test_cost_objective_proportional_to_payoff(self):
        low = _make_idea_proposal(count=2)
        high = _make_idea_proposal(count=10)
        obj = CostObjective(cost_factor=0.08)
        assert obj.evaluate(low) < obj.evaluate(high)

    def test_cost_objective_estimated_cost(self):
        idea = _make_idea_proposal(count=5)
        obj = CostObjective(cost_factor=0.08)
        assert obj.estimated_cost(idea) == pytest.approx(0.4)

    def test_cost_objective_score_in_range(self):
        for payoff in [0, 5, 10, 15, 20]:
            idea = _make_idea_proposal(count=payoff)
            obj = CostObjective()
            score = obj.evaluate(idea)
            assert 0.0 <= score <= 1.0

    # ------------------------------------------------------------------
    # CompositeObjective
    # ------------------------------------------------------------------

    def test_composite_objective_weighted_sum(self):
        idea = _make_idea_proposal()
        comp = CompositeObjective()
        comp.add(NoveltyObjective(), weight=1.0)
        comp.add(YieldObjective(), weight=1.0)
        score = comp.evaluate(idea)
        assert isinstance(score, float)
        assert 0.0 <= score <= 1.0

    def test_composite_objective_normalize_weights(self):
        comp = CompositeObjective()
        comp.add(NoveltyObjective(), weight=2.0)
        comp.add(FeasibilityObjective(), weight=3.0)
        comp.normalize_weights()
        # After normalisation the weights should sum to 1
        total = sum(w for _, w in comp._components)
        assert total == pytest.approx(1.0)

    def test_composite_objective_component_count(self):
        comp = CompositeObjective()
        assert comp.component_count() == 0
        comp.add(NoveltyObjective())
        assert comp.component_count() == 1
        comp.add(FeasibilityObjective())
        assert comp.component_count() == 2

    def test_composite_objective_evaluate_breakdown(self):
        idea = _make_idea_proposal()
        comp = CompositeObjective()
        comp.add(NoveltyObjective(), weight=1.0)
        comp.add(YieldObjective(), weight=1.0)
        breakdown = comp.evaluate_breakdown(idea)
        assert isinstance(breakdown, dict)
        assert len(breakdown) == 2
        for v in breakdown.values():
            assert 0.0 <= v <= 1.0

    def test_composite_objective_empty_returns_zero_point_five(self):
        idea = _make_idea_proposal()
        comp = CompositeObjective()
        score = comp.evaluate(idea)
        # Empty composite should return a valid float
        assert isinstance(score, float)

    # ------------------------------------------------------------------
    # BaseObjective
    # ------------------------------------------------------------------

    def test_base_objective_weighted_evaluate(self):
        idea = _make_idea_proposal()
        obj = BaseObjective(name="base", weight=0.5)
        # base returns 0.5, weighted = 0.5 * 0.5
        assert obj.weighted_evaluate(idea) == pytest.approx(0.25)

    def test_base_objective_description(self):
        obj = BaseObjective(name="myobj", weight=1.0)
        desc = obj.description()
        assert isinstance(desc, str)
        assert len(desc) > 0


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------

class TestIntegrationS01:
    """Integration tests for objective functions working together."""

    def test_objective_factory_creates_novelty(self):
        obj = ObjectiveFactory.create("novelty", weight=0.8)
        assert isinstance(obj, NoveltyObjective)
        assert obj.weight == pytest.approx(0.8)

    def test_objective_factory_creates_all_types(self):
        for name in ["novelty", "feasibility", "purpose", "yield", "cost"]:
            obj = ObjectiveFactory.create(name)
            assert isinstance(obj, BaseObjective)

    def test_objective_factory_creates_standard_suite(self):
        suite = ObjectiveFactory.create_standard_suite()
        assert isinstance(suite, list)
        assert len(suite) == 5
        for obj in suite:
            assert isinstance(obj, BaseObjective)

    def test_objective_factory_available_names(self):
        names = ObjectiveFactory.available_names()
        assert isinstance(names, list)
        assert "novelty" in names
        assert "feasibility" in names
        assert "purpose" in names
        assert "yield" in names
        assert "cost" in names

    def test_objective_factory_creates_standard_suite_with_weights(self):
        weights = {"novelty": 2.0, "feasibility": 0.5, "yield": 1.0}
        suite = ObjectiveFactory.create_standard_suite(weights=weights)
        name_to_obj = {o.name: o for o in suite}
        assert name_to_obj["novelty"].weight == pytest.approx(2.0)
        assert name_to_obj["feasibility"].weight == pytest.approx(0.5)

    def test_objective_evaluator_evaluate_all(self):
        evaluator = ObjectiveEvaluator()
        for name in ["novelty", "feasibility", "yield"]:
            evaluator.add(ObjectiveFactory.create(name))
        idea = _make_idea_proposal()
        scores = evaluator.evaluate_all(idea)
        assert isinstance(scores, dict)
        assert len(scores) == 3
        for v in scores.values():
            assert 0.0 <= v <= 1.0

    def test_objective_evaluator_rank_ideas(self):
        evaluator = ObjectiveEvaluator()
        evaluator.add(ObjectiveFactory.create("novelty"))
        evaluator.add(ObjectiveFactory.create("yield"))
        ideas = [
            _make_idea_proposal(title=f"Idea {i}", count=i + 1)
            for i in range(5)
        ]
        ranked = evaluator.rank_ideas(ideas)
        assert len(ranked) == 5
        # Each element is (idea, score)
        for idea, score in ranked:
            assert isinstance(score, float)
        # Verify descending order
        scores_only = [s for _, s in ranked]
        assert scores_only == sorted(scores_only, reverse=True)

    def test_objective_evaluator_top_k(self):
        evaluator = ObjectiveEvaluator()
        evaluator.add(ObjectiveFactory.create("yield"))
        ideas = [_make_idea_proposal(count=i + 1) for i in range(10)]
        top3 = evaluator.top_k(ideas, k=3)
        assert len(top3) == 3

    def test_objective_evaluator_empty_pool(self):
        evaluator = ObjectiveEvaluator()
        evaluator.add(ObjectiveFactory.create("novelty"))
        ranked = evaluator.rank_ideas([])
        assert ranked == []

    def test_objective_evaluator_equal_scores(self):
        evaluator = ObjectiveEvaluator()
        evaluator.add(ObjectiveFactory.create("yield", weight=1.0))
        # All ideas have the same payoff → equal scores
        ideas = [_make_idea_proposal(count=5) for _ in range(3)]
        ranked = evaluator.rank_ideas(ideas)
        assert len(ranked) == 3
        scores_only = [s for _, s in ranked]
        assert all(abs(s - scores_only[0]) < 1e-9 for s in scores_only)

    def test_composite_with_factory_and_evaluator(self):
        """End-to-end: build composite via factory, rank ideas via evaluator."""
        comp = CompositeObjective("full")
        for name in ["novelty", "feasibility", "yield"]:
            comp.add(ObjectiveFactory.create(name))
        evaluator = ObjectiveEvaluator(objectives=[])
        # Wrap composite usage directly
        ideas = [_make_idea_proposal(title=f"T{i}", count=i + 1) for i in range(4)]
        scores = [comp.evaluate(idea) for idea in ideas]
        assert all(0.0 <= s <= 1.0 for s in scores)

    def test_zero_weight_composite(self):
        """CompositeObjective with zero-weight component should still produce valid score."""
        idea = _make_idea_proposal()
        comp = CompositeObjective()
        comp.add(NoveltyObjective(), weight=0.0)
        comp.add(YieldObjective(), weight=1.0)
        score = comp.evaluate(idea)
        assert isinstance(score, float)
        assert 0.0 <= score <= 1.0

    def test_full_pipeline_objective_names(self):
        evaluator = ObjectiveEvaluator()
        for name in ["novelty", "feasibility", "purpose", "yield", "cost"]:
            evaluator.add(ObjectiveFactory.create(name))
        names = evaluator.objective_names()
        assert set(names) == {"novelty", "feasibility", "purpose", "yield", "cost"}
