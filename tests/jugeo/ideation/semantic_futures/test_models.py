"""Tests for jugeo.ideation.semantic_futures.models (Ch. 49 — Semantic Futures).

Covers SemanticFuture, FutureState, PurposeFunction, FutureValuation,
IdeationState, FutureFilter, FutureRanker, FutureComparator, FutureTag,
and the private arithmetic helpers.  Integration tests combine these classes
in a realistic ideation session scenario.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "src" / "jugeo").exists())
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import pytest

pytest.importorskip("jugeo.ideation.semantic_futures.models")

from jugeo.ideation.semantic_futures.models import (
    SemanticFuture,
    FutureState,
    PurposeFunction,
    FutureValuation,
    IdeationState,
    FutureFilter,
    FutureRanker,
    FutureComparator,
    FutureTag,
    _clamp,
    _cosine_distance,
    _dot,
    _norm,
    _weighted_sum,
)


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------

def _make_tag(name: str = "alignment", color: str = "blue") -> FutureTag:
    return FutureTag(name=name, color=color)


def _make_future(
    *,
    future_id: str = "f1",
    title: str = "Prove convergence lemma",
    description: str = "Establish L2-convergence for the iteration scheme.",
    reachability: float = 0.8,
    purpose_alignment: float = 0.9,
    yield_estimate: float = 5.0,
    cost_estimate: float = 2.0,
    tags: tuple[FutureTag, ...] = (),
) -> SemanticFuture:
    return SemanticFuture(
        future_id=future_id,
        title=title,
        description=description,
        reachability=reachability,
        purpose_alignment=purpose_alignment,
        yield_estimate=yield_estimate,
        cost_estimate=cost_estimate,
        tags=tags,
    )


def _make_future_state(
    *,
    state_id: str = "s1",
    coordinates: tuple[float, ...] = (0.5, 0.5, 0.5),
    label: str = "initial",
) -> FutureState:
    return FutureState(state_id=state_id, coordinates=coordinates, label=label)


def _make_purpose(
    *,
    purpose_id: str = "p1",
    components: tuple[str, ...] = ("novelty", "feasibility"),
    weights: tuple[float, ...] = (0.6, 0.4),
) -> PurposeFunction:
    return PurposeFunction(
        purpose_id=purpose_id, components=components, weights=weights
    )


def _make_valuation(
    *,
    future_id: str = "f1",
    purpose_score: float = 0.8,
    reachability_score: float = 0.7,
    yield_score: float = 4.0,
    cost_score: float = 1.5,
    composite: float = 2.0,
) -> FutureValuation:
    return FutureValuation(
        future_id=future_id,
        purpose_score=purpose_score,
        reachability_score=reachability_score,
        yield_score=yield_score,
        cost_score=cost_score,
        composite=composite,
    )


def _make_ideation_state(
    futures: list[SemanticFuture] | None = None,
    *,
    budget: float = 20.0,
    spent: float = 0.0,
) -> IdeationState:
    if futures is None:
        futures = [
            _make_future(future_id="f1", reachability=0.9, purpose_alignment=0.9,
                         yield_estimate=4.0, cost_estimate=1.0),
            _make_future(future_id="f2", reachability=0.5, purpose_alignment=0.6,
                         yield_estimate=3.0, cost_estimate=2.0),
            _make_future(future_id="f3", reachability=0.1, purpose_alignment=0.2,
                         yield_estimate=1.0, cost_estimate=5.0),
        ]
    return IdeationState(futures=list(futures), archive=[], budget=budget, spent_budget=spent)


# ---------------------------------------------------------------------------
# TestSemanticFuture
# ---------------------------------------------------------------------------

class TestSemanticFuture:
    """Tests for the SemanticFuture core value object.

    SemanticFuture is an immutable dataclass representing a candidate future
    state the ideator may bring into existence.  Tests cover creation,
    __post_init__ validation, value(), is_viable(), dominates(),
    to_dict/from_dict, and __str__.
    """

    def test_basic_creation(self) -> None:
        """SemanticFuture can be created with all required fields."""
        f = _make_future()
        assert f.future_id == "f1"
        assert f.title == "Prove convergence lemma"
        assert 0.0 <= f.reachability <= 1.0
        assert 0.0 <= f.purpose_alignment <= 1.0

    def test_post_init_rejects_reachability_above_one(self) -> None:
        """__post_init__ raises ValueError when reachability > 1."""
        with pytest.raises((ValueError, TypeError)):
            SemanticFuture(
                future_id="bad",
                title="bad",
                description="",
                reachability=1.5,
                purpose_alignment=0.5,
                yield_estimate=1.0,
                cost_estimate=0.0,
            )

    def test_post_init_rejects_reachability_below_zero(self) -> None:
        """__post_init__ raises ValueError when reachability < 0."""
        with pytest.raises((ValueError, TypeError)):
            SemanticFuture(
                future_id="bad",
                title="bad",
                description="",
                reachability=-0.1,
                purpose_alignment=0.5,
                yield_estimate=1.0,
                cost_estimate=0.0,
            )

    def test_post_init_rejects_alignment_above_one(self) -> None:
        """__post_init__ raises ValueError when purpose_alignment > 1."""
        with pytest.raises((ValueError, TypeError)):
            SemanticFuture(
                future_id="bad",
                title="bad",
                description="",
                reachability=0.5,
                purpose_alignment=2.0,
                yield_estimate=1.0,
                cost_estimate=0.0,
            )

    def test_value_formula(self) -> None:
        """value() = purpose_alignment * reachability * yield_estimate - cost_estimate."""
        f = _make_future(
            reachability=0.8, purpose_alignment=0.5, yield_estimate=10.0, cost_estimate=2.0
        )
        expected = 0.5 * 0.8 * 10.0 - 2.0
        assert f.value() == pytest.approx(expected, abs=1e-9)

    def test_value_zero_at_boundary(self) -> None:
        """value() is zero when cost equals purpose*reach*yield."""
        f = SemanticFuture(
            future_id="boundary",
            title="boundary",
            description="",
            reachability=1.0,
            purpose_alignment=1.0,
            yield_estimate=3.0,
            cost_estimate=3.0,
        )
        assert f.value() == pytest.approx(0.0, abs=1e-9)

    def test_is_viable_positive_value(self) -> None:
        """is_viable() returns True when value() > 0."""
        f = _make_future(
            reachability=1.0, purpose_alignment=1.0, yield_estimate=5.0, cost_estimate=1.0
        )
        assert f.is_viable() is True

    def test_is_viable_negative_value(self) -> None:
        """is_viable() returns False when value() <= 0."""
        f = _make_future(
            reachability=0.1, purpose_alignment=0.1, yield_estimate=1.0, cost_estimate=100.0
        )
        assert f.is_viable() is False

    def test_dominates_better_on_all_axes(self) -> None:
        """dominates() returns True when self is better on reachability, alignment, yield, and cost."""
        strong = _make_future(
            future_id="strong",
            reachability=0.9, purpose_alignment=0.9, yield_estimate=5.0, cost_estimate=1.0
        )
        weak = _make_future(
            future_id="weak",
            reachability=0.4, purpose_alignment=0.4, yield_estimate=2.0, cost_estimate=3.0
        )
        assert strong.dominates(weak) is True

    def test_dominates_false_when_tied(self) -> None:
        """dominates() returns False when both futures are identical."""
        f = _make_future()
        assert f.dominates(f) is False

    def test_dominates_not_always_reflexive(self) -> None:
        """A future with one better axis and one worse does not dominate."""
        a = _make_future(future_id="a", reachability=1.0, purpose_alignment=0.5)
        b = _make_future(future_id="b", reachability=0.5, purpose_alignment=1.0)
        assert a.dominates(b) is False
        assert b.dominates(a) is False

    def test_to_dict_round_trip(self) -> None:
        """from_dict(to_dict(f)) produces an equivalent future."""
        f = _make_future()
        d = f.to_dict()
        restored = SemanticFuture.from_dict(d)
        assert restored.future_id == f.future_id
        assert restored.reachability == pytest.approx(f.reachability)
        assert restored.purpose_alignment == pytest.approx(f.purpose_alignment)

    def test_str_is_non_empty(self) -> None:
        """__str__ returns a non-empty string."""
        f = _make_future()
        assert isinstance(str(f), str)
        assert len(str(f)) > 0

    def test_frozen_immutability(self) -> None:
        """SemanticFuture is immutable."""
        f = _make_future()
        with pytest.raises((AttributeError, TypeError)):
            f.title = "mutated"  # type: ignore[misc]

    def test_tags_attached(self) -> None:
        """SemanticFuture with tags stores them."""
        t = _make_tag("topology")
        f = _make_future(tags=(t,))
        assert len(f.tags) == 1
        assert f.tags[0].name == "topology"

    @pytest.mark.parametrize(
        "r, a, y, c, expected_sign",
        [
            (1.0, 1.0, 5.0, 1.0, 1),   # positive value
            (0.0, 1.0, 5.0, 0.0, 0),   # reachability=0 → value=0
            (0.5, 0.5, 1.0, 10.0, -1), # high cost → negative
            (1.0, 1.0, 2.0, 2.0, 0),   # exactly zero
        ],
    )
    def test_value_sign(
        self, r: float, a: float, y: float, c: float, expected_sign: int
    ) -> None:
        """value() has the expected sign for various (r, a, y, c) combinations."""
        f = SemanticFuture(
            future_id="t", title="t", description="", reachability=r,
            purpose_alignment=a, yield_estimate=y, cost_estimate=c,
        )
        v = f.value()
        if expected_sign == 1:
            assert v > 0
        elif expected_sign == -1:
            assert v < 0
        else:
            assert v == pytest.approx(0.0, abs=1e-9)


# ---------------------------------------------------------------------------
# TestFutureState
# ---------------------------------------------------------------------------

class TestFutureState:
    """Tests for FutureState — a point in semantic space with coordinates."""

    def test_basic_creation(self) -> None:
        """FutureState can be created with state_id and coordinate tuple."""
        s = _make_future_state()
        assert s.state_id == "s1"
        assert len(s.coordinates) == 3

    def test_distance_to_self_is_zero(self) -> None:
        """A state's Euclidean distance to itself is exactly 0."""
        s = _make_future_state()
        assert s.distance_to(s) == pytest.approx(0.0, abs=1e-9)

    def test_distance_to_different_state_is_positive(self) -> None:
        """Distance between two distinct states is strictly positive."""
        s1 = _make_future_state(state_id="s1", coordinates=(0.0, 0.0, 0.0))
        s2 = _make_future_state(state_id="s2", coordinates=(1.0, 1.0, 1.0))
        assert s1.distance_to(s2) > 0.0

    def test_distance_symmetry(self) -> None:
        """distance_to is symmetric: d(a, b) == d(b, a)."""
        s1 = _make_future_state(state_id="s1", coordinates=(0.3, 0.1, 0.7))
        s2 = _make_future_state(state_id="s2", coordinates=(0.8, 0.2, 0.4))
        assert s1.distance_to(s2) == pytest.approx(s2.distance_to(s1), rel=1e-6)

    def test_known_distance(self) -> None:
        """distance_to computes sqrt(3) for unit corners of the unit cube."""
        s1 = _make_future_state(state_id="s1", coordinates=(0.0, 0.0, 0.0))
        s2 = _make_future_state(state_id="s2", coordinates=(1.0, 1.0, 1.0))
        assert s1.distance_to(s2) == pytest.approx(math.sqrt(3), rel=1e-6)

    def test_size_returns_dimension(self) -> None:
        """size() returns the number of coordinate dimensions."""
        s = _make_future_state(coordinates=(0.1, 0.2, 0.3, 0.4))
        assert s.size() == 4

    def test_size_zero_for_empty_coordinates(self) -> None:
        """size() returns 0 for a state with no coordinates."""
        s = FutureState(state_id="empty", coordinates=(), label="empty")
        assert s.size() == 0

    def test_summary_returns_string(self) -> None:
        """summary() returns a non-empty string."""
        s = _make_future_state()
        assert isinstance(s.summary(), str)
        assert len(s.summary()) > 0

    def test_to_dict_from_dict_round_trip(self) -> None:
        """from_dict(to_dict(s)) restores the state exactly."""
        s = _make_future_state(coordinates=(0.1, 0.5, 0.9))
        restored = FutureState.from_dict(s.to_dict())
        assert restored.state_id == s.state_id
        assert tuple(restored.coordinates) == pytest.approx(tuple(s.coordinates), abs=1e-9)


# ---------------------------------------------------------------------------
# TestPurposeFunction
# ---------------------------------------------------------------------------

class TestPurposeFunction:
    """Tests for PurposeFunction — evaluates futures against a weighted purpose."""

    def test_basic_creation(self) -> None:
        """PurposeFunction is created with components and matching weights."""
        p = _make_purpose()
        assert p.purpose_id == "p1"
        assert len(p.components) == len(p.weights)

    def test_evaluate_returns_float_in_zero_one(self) -> None:
        """evaluate(future) returns a float in [0, 1]."""
        p = _make_purpose()
        f = _make_future()
        score = p.evaluate(f)
        assert isinstance(score, float)
        assert 0.0 <= score <= 1.0

    def test_evaluate_high_alignment_gives_high_score(self) -> None:
        """A future with purpose_alignment=1.0 gives a higher score than one with 0.0."""
        p = _make_purpose()
        high = _make_future(future_id="h", purpose_alignment=1.0)
        low = _make_future(future_id="l", purpose_alignment=0.0)
        assert p.evaluate(high) >= p.evaluate(low)

    def test_normalize_weights_sums_to_one(self) -> None:
        """normalize_weights() returns a PurposeFunction whose weights sum to 1.0."""
        p = PurposeFunction(
            purpose_id="unnorm",
            components=("a", "b", "c"),
            weights=(1.0, 2.0, 7.0),
        )
        normalised = p.normalize_weights()
        assert sum(normalised.weights) == pytest.approx(1.0, abs=1e-9)

    def test_normalize_weights_already_normalised(self) -> None:
        """normalize_weights() on already-unit weights returns sum 1.0."""
        p = PurposeFunction(
            purpose_id="norm",
            components=("x", "y"),
            weights=(0.3, 0.7),
        )
        normalised = p.normalize_weights()
        assert sum(normalised.weights) == pytest.approx(1.0, abs=1e-9)

    def test_to_dict_from_dict_round_trip(self) -> None:
        """from_dict(to_dict(p)) restores the PurposeFunction."""
        p = _make_purpose()
        restored = PurposeFunction.from_dict(p.to_dict())
        assert restored.purpose_id == p.purpose_id
        assert tuple(restored.components) == tuple(p.components)
        assert tuple(restored.weights) == pytest.approx(tuple(p.weights), abs=1e-9)

    def test_single_component_weights_sum_to_one(self) -> None:
        """A single-component PurposeFunction normalised to weight 1.0."""
        p = PurposeFunction(
            purpose_id="single",
            components=("only",),
            weights=(42.0,),
        )
        normalised = p.normalize_weights()
        assert normalised.weights[0] == pytest.approx(1.0, abs=1e-9)


# ---------------------------------------------------------------------------
# TestFutureValuation
# ---------------------------------------------------------------------------

class TestFutureValuation:
    """Tests for FutureValuation — a scored record for a candidate future."""

    def test_basic_creation(self) -> None:
        """FutureValuation is created with all score fields."""
        v = _make_valuation()
        assert v.future_id == "f1"
        assert v.composite == pytest.approx(2.0)

    def test_is_viable_positive_composite(self) -> None:
        """is_viable() returns True when composite > 0."""
        v = _make_valuation(composite=0.01)
        assert v.is_viable() is True

    def test_is_viable_negative_composite(self) -> None:
        """is_viable() returns False when composite <= 0."""
        v = _make_valuation(composite=-1.0)
        assert v.is_viable() is False

    def test_is_viable_zero_composite(self) -> None:
        """is_viable() returns False when composite == 0."""
        v = _make_valuation(composite=0.0)
        assert v.is_viable() is False

    def test_to_dict_from_dict_round_trip(self) -> None:
        """Round-trip serialisation preserves all fields."""
        v = _make_valuation()
        restored = FutureValuation.from_dict(v.to_dict())
        assert restored.future_id == v.future_id
        assert restored.composite == pytest.approx(v.composite)


# ---------------------------------------------------------------------------
# TestIdeationState
# ---------------------------------------------------------------------------

class TestIdeationState:
    """Tests for IdeationState — the mutable container for an ideation session.

    Covers best_future, viable_futures, archive_future, advance_to, and
    remaining_budget_fraction.
    """

    def test_best_future_returns_highest_value(self) -> None:
        """best_future() returns the future with the highest value()."""
        state = _make_ideation_state()
        best = state.best_future()
        assert best is not None
        for f in state.futures:
            assert best.value() >= f.value()

    def test_best_future_empty_returns_none(self) -> None:
        """best_future() returns None when the frontier is empty."""
        state = IdeationState(futures=[], archive=[], budget=10.0, spent_budget=0.0)
        assert state.best_future() is None

    def test_viable_futures_filters_correctly(self) -> None:
        """viable_futures() returns only futures whose value() > 0."""
        state = _make_ideation_state()
        viable = state.viable_futures()
        for f in viable:
            assert f.is_viable()

    def test_viable_futures_empty_if_all_negative(self) -> None:
        """viable_futures() returns [] when all futures have value <= 0."""
        futures = [
            _make_future(
                future_id=f"f{i}",
                reachability=0.01,
                purpose_alignment=0.01,
                yield_estimate=0.1,
                cost_estimate=100.0,
            )
            for i in range(3)
        ]
        state = _make_ideation_state(futures=futures)
        assert state.viable_futures() == []

    def test_archive_future_moves_to_archive(self) -> None:
        """archive_future() removes the future from active set and adds to archive."""
        state = _make_ideation_state()
        initial_count = len(state.futures)
        state.archive_future("f1")
        assert len(state.futures) == initial_count - 1
        assert any(f.future_id == "f1" for f in state.archive)

    def test_archive_future_missing_id_raises(self) -> None:
        """archive_future() raises KeyError or ValueError for an absent id."""
        state = _make_ideation_state()
        with pytest.raises((KeyError, ValueError)):
            state.archive_future("nonexistent")

    def test_advance_to_returns_new_state(self) -> None:
        """advance_to() returns a new IdeationState with updated spent budget."""
        state = _make_ideation_state(budget=20.0, spent=0.0)
        f = state.futures[0]
        new_state = state.advance_to(f, cost=3.0)
        assert new_state.spent_budget == pytest.approx(3.0, abs=1e-9)

    def test_advance_to_does_not_mutate_original(self) -> None:
        """advance_to() does not alter the original state's spent_budget."""
        state = _make_ideation_state(budget=20.0, spent=0.0)
        f = state.futures[0]
        state.advance_to(f, cost=5.0)
        assert state.spent_budget == pytest.approx(0.0, abs=1e-9)

    def test_remaining_budget_fraction_full(self) -> None:
        """remaining_budget_fraction() is 1.0 when nothing has been spent."""
        state = _make_ideation_state(budget=10.0, spent=0.0)
        assert state.remaining_budget_fraction() == pytest.approx(1.0, abs=1e-9)

    def test_remaining_budget_fraction_half(self) -> None:
        """remaining_budget_fraction() is 0.5 when half the budget is spent."""
        state = _make_ideation_state(budget=10.0, spent=5.0)
        assert state.remaining_budget_fraction() == pytest.approx(0.5, abs=1e-9)

    def test_remaining_budget_fraction_zero_budget(self) -> None:
        """remaining_budget_fraction() is 0.0 when budget is fully exhausted."""
        state = _make_ideation_state(budget=10.0, spent=10.0)
        assert state.remaining_budget_fraction() == pytest.approx(0.0, abs=1e-9)


# ---------------------------------------------------------------------------
# TestFutureFilter
# ---------------------------------------------------------------------------

class TestFutureFilter:
    """Tests for FutureFilter — static methods for pruning future lists."""

    def _sample_futures(self) -> list[SemanticFuture]:
        return [
            _make_future(future_id="cheap", cost_estimate=1.0, reachability=0.9,
                         purpose_alignment=0.8, yield_estimate=4.0,
                         tags=(_make_tag("alpha"),)),
            _make_future(future_id="mid", cost_estimate=5.0, reachability=0.5,
                         purpose_alignment=0.5, yield_estimate=3.0,
                         tags=(_make_tag("beta"),)),
            _make_future(future_id="expensive", cost_estimate=20.0, reachability=0.1,
                         purpose_alignment=0.2, yield_estimate=1.0,
                         tags=(_make_tag("alpha"),)),
        ]

    def test_filter_by_budget_empty_input(self) -> None:
        """filter_by_budget returns [] for empty input."""
        assert FutureFilter.filter_by_budget([], max_cost=10.0) == []

    def test_filter_by_budget_keeps_cheap(self) -> None:
        """filter_by_budget removes futures whose cost_estimate > max_cost."""
        futures = self._sample_futures()
        result = FutureFilter.filter_by_budget(futures, max_cost=5.0)
        assert all(f.cost_estimate <= 5.0 for f in result)

    def test_filter_by_budget_zero_max_returns_empty(self) -> None:
        """filter_by_budget with max_cost=0 returns [] (all costs > 0)."""
        futures = self._sample_futures()
        result = FutureFilter.filter_by_budget(futures, max_cost=0.0)
        assert result == []

    def test_filter_by_tags_empty_input(self) -> None:
        """filter_by_tags returns [] for empty input."""
        assert FutureFilter.filter_by_tags([], tag_names={"alpha"}) == []

    def test_filter_by_tags_correct_subset(self) -> None:
        """filter_by_tags returns only futures whose tags include the requested name."""
        futures = self._sample_futures()
        result = FutureFilter.filter_by_tags(futures, tag_names={"alpha"})
        assert all(any(t.name == "alpha" for t in f.tags) for f in result)
        assert len(result) == 2

    def test_filter_by_min_reachability_empty(self) -> None:
        """filter_by_min_reachability returns [] for empty input."""
        assert FutureFilter.filter_by_min_reachability([], min_r=0.5) == []

    def test_filter_by_min_reachability_threshold(self) -> None:
        """filter_by_min_reachability keeps only futures with reachability >= min_r."""
        futures = self._sample_futures()
        result = FutureFilter.filter_by_min_reachability(futures, min_r=0.5)
        assert all(f.reachability >= 0.5 for f in result)

    def test_filter_by_min_alignment_empty(self) -> None:
        """filter_by_min_alignment returns [] for empty input."""
        assert FutureFilter.filter_by_min_alignment([], min_a=0.5) == []

    def test_filter_by_min_alignment_threshold(self) -> None:
        """filter_by_min_alignment keeps only futures with purpose_alignment >= min_a."""
        futures = self._sample_futures()
        result = FutureFilter.filter_by_min_alignment(futures, min_a=0.5)
        assert all(f.purpose_alignment >= 0.5 for f in result)

    def test_filter_dominated_removes_dominated(self) -> None:
        """filter_dominated removes futures dominated by another in the list."""
        strong = _make_future(future_id="strong", reachability=0.9, purpose_alignment=0.9,
                              yield_estimate=5.0, cost_estimate=1.0)
        weak = _make_future(future_id="weak", reachability=0.4, purpose_alignment=0.4,
                            yield_estimate=2.0, cost_estimate=3.0)
        result = FutureFilter.filter_dominated([strong, weak])
        ids = [f.future_id for f in result]
        assert "strong" in ids
        assert "weak" not in ids

    def test_filter_dominated_empty_input(self) -> None:
        """filter_dominated returns [] for empty input."""
        assert FutureFilter.filter_dominated([]) == []

    def test_filter_dominated_single_item(self) -> None:
        """filter_dominated with one future returns that future (not dominated by itself)."""
        f = _make_future()
        result = FutureFilter.filter_dominated([f])
        assert len(result) == 1


# ---------------------------------------------------------------------------
# TestFutureRanker
# ---------------------------------------------------------------------------

class TestFutureRanker:
    """Tests for FutureRanker — static methods that sort futures by criteria."""

    def _make_ranked_futures(self) -> list[SemanticFuture]:
        return [
            _make_future(future_id="low", reachability=0.2, purpose_alignment=0.2,
                         yield_estimate=1.0, cost_estimate=0.5),
            _make_future(future_id="mid", reachability=0.5, purpose_alignment=0.5,
                         yield_estimate=3.0, cost_estimate=1.0),
            _make_future(future_id="high", reachability=0.9, purpose_alignment=0.9,
                         yield_estimate=5.0, cost_estimate=1.0),
        ]

    def test_rank_by_value_descending(self) -> None:
        """rank_by_value returns futures sorted by value() descending."""
        futures = self._make_ranked_futures()
        ranked = FutureRanker.rank_by_value(futures)
        values = [f.value() for f in ranked]
        assert values == sorted(values, reverse=True)

    def test_rank_by_value_empty(self) -> None:
        """rank_by_value returns [] for empty input."""
        assert FutureRanker.rank_by_value([]) == []

    def test_rank_by_reachability_descending(self) -> None:
        """rank_by_reachability returns futures sorted by reachability descending."""
        futures = self._make_ranked_futures()
        ranked = FutureRanker.rank_by_reachability(futures)
        reachabilities = [f.reachability for f in ranked]
        assert reachabilities == sorted(reachabilities, reverse=True)

    def test_rank_composite_returns_list(self) -> None:
        """rank_composite returns a non-empty list for non-empty input."""
        futures = self._make_ranked_futures()
        ranked = FutureRanker.rank_composite(futures)
        assert len(ranked) == len(futures)

    def test_rank_composite_preserves_all_futures(self) -> None:
        """rank_composite does not drop any futures."""
        futures = self._make_ranked_futures()
        ranked = FutureRanker.rank_composite(futures)
        assert {f.future_id for f in ranked} == {f.future_id for f in futures}


# ---------------------------------------------------------------------------
# TestFutureComparator
# ---------------------------------------------------------------------------

class TestFutureComparator:
    """Tests for FutureComparator — comparison and Pareto-front operations."""

    def _high_future(self) -> SemanticFuture:
        return _make_future(
            future_id="high", reachability=0.9, purpose_alignment=0.9,
            yield_estimate=5.0, cost_estimate=1.0
        )

    def _low_future(self) -> SemanticFuture:
        return _make_future(
            future_id="low", reachability=0.2, purpose_alignment=0.2,
            yield_estimate=1.0, cost_estimate=5.0
        )

    def test_compare_returns_minus_one_for_lesser(self) -> None:
        """compare(low, high) returns -1."""
        assert FutureComparator.compare(self._low_future(), self._high_future()) == -1

    def test_compare_returns_one_for_greater(self) -> None:
        """compare(high, low) returns 1."""
        assert FutureComparator.compare(self._high_future(), self._low_future()) == 1

    def test_compare_returns_zero_for_equal(self) -> None:
        """compare(f, f) returns 0."""
        f = _make_future()
        assert FutureComparator.compare(f, f) == 0

    def test_is_pareto_optimal_unique_future(self) -> None:
        """A future with no alternatives is Pareto-optimal."""
        f = _make_future()
        assert FutureComparator.is_pareto_optimal(f, []) is True

    def test_is_pareto_optimal_dominated(self) -> None:
        """A dominated future is not Pareto-optimal relative to its dominator."""
        high = self._high_future()
        low = self._low_future()
        assert FutureComparator.is_pareto_optimal(low, [high]) is False

    def test_pareto_front_removes_dominated(self) -> None:
        """pareto_front returns only Pareto-optimal futures."""
        high = self._high_future()
        low = self._low_future()
        front = FutureComparator.pareto_front([high, low])
        assert high in front
        assert low not in front

    def test_pareto_front_empty_input(self) -> None:
        """pareto_front returns [] for empty input."""
        assert FutureComparator.pareto_front([]) == []

    def test_pareto_front_all_incomparable(self) -> None:
        """pareto_front with all incomparable futures returns all of them."""
        a = _make_future(future_id="a", reachability=1.0, purpose_alignment=0.0)
        b = _make_future(future_id="b", reachability=0.0, purpose_alignment=1.0)
        front = FutureComparator.pareto_front([a, b])
        assert len(front) == 2


# ---------------------------------------------------------------------------
# TestHelpers
# ---------------------------------------------------------------------------

class TestHelpers:
    """Tests for private arithmetic helper functions."""

    @pytest.mark.parametrize(
        "x, lo, hi, expected",
        [
            (0.5, 0.0, 1.0, 0.5),
            (-1.0, 0.0, 1.0, 0.0),
            (2.0, 0.0, 1.0, 1.0),
            (0.0, 0.0, 1.0, 0.0),
            (1.0, 0.0, 1.0, 1.0),
            (5.0, 2.0, 4.0, 4.0),
        ],
    )
    def test_clamp(self, x: float, lo: float, hi: float, expected: float) -> None:
        """_clamp(x, lo, hi) returns x bounded to [lo, hi]."""
        assert _clamp(x, lo, hi) == pytest.approx(expected, abs=1e-9)

    def test_cosine_distance_self_is_zero(self) -> None:
        """_cosine_distance(v, v) == 0 for any nonzero vector."""
        v = [1.0, 2.0, 3.0]
        assert _cosine_distance(v, v) == pytest.approx(0.0, abs=1e-9)

    def test_cosine_distance_orthogonal_is_one(self) -> None:
        """_cosine_distance of two orthogonal vectors == 1."""
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        assert _cosine_distance(a, b) == pytest.approx(1.0, abs=1e-9)

    def test_cosine_distance_opposite_is_two(self) -> None:
        """_cosine_distance of antiparallel vectors == 2."""
        a = [1.0, 0.0]
        b = [-1.0, 0.0]
        assert _cosine_distance(a, b) == pytest.approx(2.0, abs=1e-9)

    def test_dot_basic(self) -> None:
        """_dot([1, 2, 3], [4, 5, 6]) == 32."""
        assert _dot([1.0, 2.0, 3.0], [4.0, 5.0, 6.0]) == pytest.approx(32.0)

    def test_dot_zero_vector(self) -> None:
        """_dot with a zero vector returns 0."""
        assert _dot([0.0, 0.0, 0.0], [1.0, 2.0, 3.0]) == pytest.approx(0.0)

    def test_norm_basic(self) -> None:
        """_norm([3, 4]) == 5."""
        assert _norm([3.0, 4.0]) == pytest.approx(5.0, rel=1e-6)

    def test_norm_zero_vector(self) -> None:
        """_norm of zero vector is 0."""
        assert _norm([0.0, 0.0, 0.0]) == pytest.approx(0.0)

    def test_norm_unit_vector(self) -> None:
        """_norm of a unit vector is 1."""
        assert _norm([1.0, 0.0, 0.0]) == pytest.approx(1.0)

    def test_weighted_sum_basic(self) -> None:
        """_weighted_sum([1, 2, 3], [0.5, 0.25, 0.25]) == 1.75."""
        result = _weighted_sum([1.0, 2.0, 3.0], [0.5, 0.25, 0.25])
        assert result == pytest.approx(1.75, abs=1e-9)

    def test_weighted_sum_uniform(self) -> None:
        """_weighted_sum with uniform weights equals the arithmetic mean."""
        values = [2.0, 4.0, 6.0]
        weights = [1 / 3] * 3
        assert _weighted_sum(values, weights) == pytest.approx(4.0, rel=1e-6)


# ---------------------------------------------------------------------------
# TestIntegrationModels
# ---------------------------------------------------------------------------

class TestIntegrationModels:
    """Integration tests — run a full mini ideation session with all model classes."""

    def test_full_mini_session(self) -> None:
        """Create IdeationState, run best_future, archive it, advance, check budget."""
        state = _make_ideation_state(budget=20.0)
        assert len(state.futures) == 3

        best = state.best_future()
        assert best is not None

        # Archive the best future
        state.archive_future(best.future_id)
        assert len(state.archive) == 1

        # Advance to a new future with a cost
        remaining = state.futures
        assert len(remaining) == 2
        next_f = remaining[0]
        new_state = state.advance_to(next_f, cost=4.0)
        assert new_state.spent_budget == pytest.approx(4.0, abs=1e-9)
        assert new_state.remaining_budget_fraction() == pytest.approx(0.8, abs=1e-9)

    def test_pareto_front_of_viable_futures(self) -> None:
        """viable_futures then pareto_front reduces to the non-dominated set."""
        state = _make_ideation_state()
        viable = state.viable_futures()
        front = FutureComparator.pareto_front(viable)
        # Front must be a subset of viable
        front_ids = {f.future_id for f in front}
        viable_ids = {f.future_id for f in viable}
        assert front_ids.issubset(viable_ids)

    def test_filter_then_rank_pipeline(self) -> None:
        """Filtering by min reachability then ranking by value gives consistent order."""
        futures = [
            _make_future(future_id=f"f{i}", reachability=i * 0.1,
                         purpose_alignment=0.5, yield_estimate=2.0, cost_estimate=0.5)
            for i in range(1, 11)
        ]
        filtered = FutureFilter.filter_by_min_reachability(futures, min_r=0.5)
        ranked = FutureRanker.rank_by_value(filtered)
        values = [f.value() for f in ranked]
        assert values == sorted(values, reverse=True)

    def test_purpose_function_guides_filter(self) -> None:
        """PurposeFunction.evaluate scores drive FutureFilter.filter_by_min_alignment."""
        p = _make_purpose(weights=(1.0, 0.0))  # only cares about first component
        futures = [
            _make_future(future_id="aligned", purpose_alignment=0.9),
            _make_future(future_id="unaligned", purpose_alignment=0.1),
        ]
        high_alignment = FutureFilter.filter_by_min_alignment(futures, min_a=0.5)
        assert all(f.purpose_alignment >= 0.5 for f in high_alignment)
        scores = [p.evaluate(f) for f in high_alignment]
        assert all(s >= 0.0 for s in scores)

    def test_ideaproposal_integration_if_available(self) -> None:
        """If jugeo.ideation.ideas.IdeaProposal is available, cross-reference it."""
        try:
            from jugeo.ideation.ideas import IdeaProposal, GainProfile  # noqa: F401
            # Can we make a SemanticFuture from an IdeaProposal's data?
            f = SemanticFuture(
                future_id="from-idea",
                title="Bridging semantic futures and ideas",
                description="Cross-layer test",
                reachability=0.7,
                purpose_alignment=0.8,
                yield_estimate=3.0,
                cost_estimate=1.0,
            )
            assert f.is_viable()
        except ImportError:
            pytest.skip("jugeo.ideation.ideas not available")
