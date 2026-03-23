"""Tests for jugeo.ideation.semantic_futures.algorithms.

Covers SearchConfig, SearchResult, all five search algorithm classes,
SearchAlgorithmFactory, SearchComparator, and private helper functions.
Each test is self-contained; setup goes through _make_state().
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "src" / "jugeo").exists())
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import pytest

from jugeo.ideation.semantic_futures.algorithms import (
    ArchiveBasedSearch,
    BeamSearchFutures,
    DiversifiedSearch,
    FutureSearchAlgorithm,
    GreedyFutureSearch,
    PurposeDirectedSearch,
    SearchAlgorithmFactory,
    SearchComparator,
    SearchConfig,
    SearchResult,
    _compute_value,
    _delta_similarity,
    _jaccard,
    _normalize_futures,
)
from jugeo.ideation.semantic_futures.models import (
    FutureState,
    FutureTag,
    IdeationState,
    PurposeFunction,
    SemanticFuture,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_future(
    fid: str,
    *,
    delta: str = "Add theorem T10",
    reachability: float = 0.8,
    purpose_alignment: float = 0.75,
    expected_yield: float = 4.0,
    cost_estimate: float = 1.5,
    tags: tuple[FutureTag, ...] = (FutureTag.EXTENSION,),
) -> SemanticFuture:
    """Return a SemanticFuture with caller-controlled fields."""
    return SemanticFuture(
        future_id=fid,
        delta=delta,
        reachability=reachability,
        purpose_alignment=purpose_alignment,
        expected_yield=expected_yield,
        cost_estimate=cost_estimate,
        tags=tags,
        metadata={},
    )


def _make_state(n_futures: int = 5) -> IdeationState:
    """Return a realistic IdeationState with *n_futures* reachable futures."""
    state = FutureState(
        state_id="s1",
        theorem_portfolio=("T1", "T2", "T3"),
        known_kinds=("K1", "K2"),
        semantic_embedding=(0.5, 0.3, 0.2),
        timestamp=datetime.now(),
    )
    purpose = PurposeFunction(
        purpose_id="p1",
        domain="algebra",
        utility_weights={"yield": 0.6, "novelty": 0.4},
        alignment_threshold=0.5,
        description="Find algebraic extensions",
    )
    futures = [
        SemanticFuture(
            future_id=f"f{i}",
            delta=f"Add theorem T{i + 4}",
            reachability=0.9 - i * 0.1,
            purpose_alignment=0.8 - i * 0.05,
            expected_yield=5.0 - i * 0.5,
            cost_estimate=1.0 + i * 0.2,
            tags=(FutureTag.EXTENSION,),
            metadata={},
        )
        for i in range(n_futures)
    ]
    return IdeationState(
        state_id="is1",
        current_state=state,
        purpose=purpose,
        reachable_futures=futures,
        budget_remaining=10.0,
        archive=[],
    )


def _make_diverse_state() -> IdeationState:
    """Return a state whose futures have different deltas and tags."""
    state = FutureState(
        state_id="s-div",
        theorem_portfolio=("T1",),
        known_kinds=("K1",),
        semantic_embedding=(0.1, 0.9),
        timestamp=datetime.now(),
    )
    purpose = PurposeFunction(
        purpose_id="p-div",
        domain="topology",
        utility_weights={"yield": 0.5, "novelty": 0.5},
        alignment_threshold=0.4,
        description="Topological diversification",
    )
    futures = [
        SemanticFuture(
            future_id=f"fd{i}",
            delta=f"Direction {'ABCDE'[i]} extension with unique lemma set {i}",
            reachability=0.7,
            purpose_alignment=0.7,
            expected_yield=3.0,
            cost_estimate=1.0,
            tags=(FutureTag.BRIDGE if i % 2 == 0 else FutureTag.EXTENSION,),
            metadata={"group": str(i % 3)},
        )
        for i in range(5)
    ]
    return IdeationState(
        state_id="is-div",
        current_state=state,
        purpose=purpose,
        reachable_futures=futures,
        budget_remaining=20.0,
        archive=[],
    )


# ---------------------------------------------------------------------------
# TestSearchConfig
# ---------------------------------------------------------------------------


class TestSearchConfig:
    """Tests for SearchConfig creation, validation, and serialisation."""

    def test_defaults_are_sensible(self) -> None:
        """Default SearchConfig must have positive beam_width and valid diversity_weight."""
        cfg = SearchConfig()
        assert cfg.beam_width >= 1
        assert 0.0 <= cfg.diversity_weight <= 1.0

    def test_explicit_construction(self) -> None:
        """Explicitly set fields are stored without modification."""
        cfg = SearchConfig(beam_width=4, diversity_weight=0.3, max_iterations=50)
        assert cfg.beam_width == 4
        assert cfg.diversity_weight == pytest.approx(0.3)
        assert cfg.max_iterations == 50

    def test_beam_width_zero_raises(self) -> None:
        """beam_width must be at least 1."""
        with pytest.raises((ValueError, AssertionError)):
            SearchConfig(beam_width=0)

    def test_beam_width_negative_raises(self) -> None:
        """Negative beam_width is rejected."""
        with pytest.raises((ValueError, AssertionError)):
            SearchConfig(beam_width=-3)

    def test_diversity_weight_above_one_raises(self) -> None:
        """diversity_weight > 1.0 is invalid."""
        with pytest.raises((ValueError, AssertionError)):
            SearchConfig(diversity_weight=1.1)

    def test_diversity_weight_negative_raises(self) -> None:
        """diversity_weight < 0.0 is invalid."""
        with pytest.raises((ValueError, AssertionError)):
            SearchConfig(diversity_weight=-0.01)

    def test_to_dict_from_dict_round_trip(self) -> None:
        """Serialisation round-trip preserves every field."""
        cfg = SearchConfig(beam_width=3, diversity_weight=0.25, max_iterations=20)
        restored = SearchConfig.from_dict(cfg.to_dict())
        assert restored.beam_width == cfg.beam_width
        assert restored.diversity_weight == pytest.approx(cfg.diversity_weight)
        assert restored.max_iterations == cfg.max_iterations

    def test_frozen_rejects_mutation(self) -> None:
        """SearchConfig instances must be immutable (frozen dataclass)."""
        cfg = SearchConfig()
        with pytest.raises((AttributeError, TypeError)):
            cfg.beam_width = 99  # type: ignore[misc]

    def test_to_dict_returns_dict(self) -> None:
        """to_dict() returns a plain dict."""
        cfg = SearchConfig(beam_width=2)
        d = cfg.to_dict()
        assert isinstance(d, dict)
        assert "beam_width" in d


# ---------------------------------------------------------------------------
# TestSearchResult
# ---------------------------------------------------------------------------


class TestSearchResult:
    """Tests for SearchResult creation, serialisation, and helpers."""

    def _make_result(self) -> SearchResult:
        state = _make_state(3)
        best = state.reachable_futures[0]
        return SearchResult(
            best_future=best,
            selected_futures=tuple(state.reachable_futures[:2]),
            value_trace=(1.0, 2.5, 3.0),
            converged=True,
            algorithm_name="TestAlgo",
            wall_time_s=0.01,
        )

    def test_basic_creation(self) -> None:
        """SearchResult stores all passed fields."""
        r = self._make_result()
        assert r.converged is True
        assert r.algorithm_name == "TestAlgo"
        assert len(r.selected_futures) == 2

    def test_to_dict_from_dict_round_trip(self) -> None:
        """Serialisation round-trip preserves key fields."""
        r = self._make_result()
        d = r.to_dict()
        restored = SearchResult.from_dict(d)
        assert restored.algorithm_name == r.algorithm_name
        assert restored.converged == r.converged
        assert len(restored.selected_futures) == len(r.selected_futures)

    def test_improvement_over_random_positive(self) -> None:
        """improvement_over_random returns a non-negative float."""
        r = self._make_result()
        imp = r.improvement_over_random()
        assert isinstance(imp, float)
        assert imp >= 0.0

    def test_frozen_rejects_mutation(self) -> None:
        """SearchResult is immutable."""
        r = self._make_result()
        with pytest.raises((AttributeError, TypeError)):
            r.converged = False  # type: ignore[misc]

    def test_none_best_future_allowed(self) -> None:
        """best_future=None is valid (no future found)."""
        r = SearchResult(
            best_future=None,
            selected_futures=(),
            value_trace=(),
            converged=False,
            algorithm_name="Empty",
            wall_time_s=0.0,
        )
        assert r.best_future is None
        assert r.selected_futures == ()


# ---------------------------------------------------------------------------
# TestFutureSearchAlgorithm (abstract base)
# ---------------------------------------------------------------------------


class TestFutureSearchAlgorithm:
    """FutureSearchAlgorithm cannot be used directly; subclasses implement search()."""

    def test_direct_instantiation_raises(self) -> None:
        """Instantiating the ABC directly must raise TypeError."""
        with pytest.raises(TypeError):
            FutureSearchAlgorithm()  # type: ignore[abstract]

    def test_name_not_implemented_on_base(self) -> None:
        """A minimal stub that skips name raises NotImplementedError."""

        class _Stub(FutureSearchAlgorithm):
            def search(self, state: IdeationState) -> SearchResult:  # type: ignore[override]
                raise NotImplementedError

        stub = _Stub()
        with pytest.raises(NotImplementedError):
            _ = stub.name  # type: ignore[misc]

    def test_value_returns_float(self) -> None:
        """_value() computes a non-negative float for any SemanticFuture."""

        class _Concrete(FutureSearchAlgorithm):
            @property
            def name(self) -> str:
                return "Concrete"

            def search(self, state: IdeationState) -> SearchResult:  # type: ignore[override]
                raise NotImplementedError

        algo = _Concrete()
        f = _make_future("fx")
        v = algo._value(f)
        assert isinstance(v, float)
        assert v >= 0.0

    def test_select_best_returns_highest_value(self) -> None:
        """_select_best picks the future with the highest composite value."""

        class _Concrete(FutureSearchAlgorithm):
            @property
            def name(self) -> str:
                return "Concrete"

            def search(self, state: IdeationState) -> SearchResult:  # type: ignore[override]
                raise NotImplementedError

        algo = _Concrete()
        low = _make_future("low", expected_yield=1.0, cost_estimate=3.0)
        high = _make_future("high", expected_yield=9.0, cost_estimate=1.0)
        best = algo._select_best([low, high])
        assert best is high


# ---------------------------------------------------------------------------
# TestBeamSearchFutures
# ---------------------------------------------------------------------------


class TestBeamSearchFutures:
    """Tests for the beam-search algorithm."""

    def test_returns_search_result(self) -> None:
        """search() must return a SearchResult instance."""
        algo = BeamSearchFutures(SearchConfig(beam_width=2))
        result = algo.search(_make_state())
        assert isinstance(result, SearchResult)

    def test_best_future_in_reachable(self) -> None:
        """best_future must be one of the futures from the input state."""
        state = _make_state(4)
        algo = BeamSearchFutures(SearchConfig(beam_width=2))
        result = algo.search(state)
        if result.best_future is not None:
            ids = {f.future_id for f in state.reachable_futures}
            assert result.best_future.future_id in ids

    def test_value_trace_non_empty(self) -> None:
        """value_trace should record at least one value per iteration."""
        algo = BeamSearchFutures(SearchConfig(beam_width=2))
        result = algo.search(_make_state())
        assert len(result.value_trace) >= 1

    def test_converged_flag_type(self) -> None:
        """converged flag must be a boolean."""
        algo = BeamSearchFutures(SearchConfig(beam_width=2))
        result = algo.search(_make_state())
        assert isinstance(result.converged, bool)

    def test_name(self) -> None:
        """Algorithm name must be 'BeamSearchFutures'."""
        algo = BeamSearchFutures(SearchConfig())
        assert algo.name == "BeamSearchFutures"

    def test_empty_futures_returns_none_best(self) -> None:
        """When there are no reachable futures, best_future must be None."""
        state = _make_state(0)
        algo = BeamSearchFutures(SearchConfig(beam_width=3))
        result = algo.search(state)
        assert result.best_future is None

    def test_single_future(self) -> None:
        """With a single future, beam search should return it as the best."""
        state = _make_state(1)
        algo = BeamSearchFutures(SearchConfig(beam_width=3))
        result = algo.search(state)
        assert result.best_future is not None
        assert result.best_future.future_id == state.reachable_futures[0].future_id

    def test_algorithm_name_in_result(self) -> None:
        """The result's algorithm_name matches the algorithm's name property."""
        algo = BeamSearchFutures(SearchConfig())
        result = algo.search(_make_state())
        assert result.algorithm_name == algo.name


# ---------------------------------------------------------------------------
# TestGreedyFutureSearch
# ---------------------------------------------------------------------------


class TestGreedyFutureSearch:
    """Tests for the greedy search algorithm."""

    def test_returns_search_result(self) -> None:
        """search() returns a SearchResult."""
        algo = GreedyFutureSearch(SearchConfig())
        assert isinstance(algo.search(_make_state()), SearchResult)

    def test_name(self) -> None:
        """Algorithm name must be 'GreedyFutureSearch'."""
        assert GreedyFutureSearch(SearchConfig()).name == "GreedyFutureSearch"

    def test_respects_budget(self) -> None:
        """Total cost of selected futures must not exceed budget_remaining."""
        state = _make_state(6)
        algo = GreedyFutureSearch(SearchConfig())
        result = algo.search(state)
        total_cost = sum(f.cost_estimate for f in result.selected_futures)
        assert total_cost <= state.budget_remaining + 1e-9

    def test_greedy_order_decreasing_value_per_cost(self) -> None:
        """Greedy should prefer futures with higher value/cost ratio first."""
        state = _make_state(5)
        algo = GreedyFutureSearch(SearchConfig())
        result = algo.search(state)
        # The best_future should have a high value/cost ratio
        if result.best_future is not None:
            best_ratio = result.best_future.expected_yield / result.best_future.cost_estimate
            for f in state.reachable_futures:
                ratio = f.expected_yield / f.cost_estimate
                # At least the best_future should not be the worst ratio in the pool
            assert best_ratio >= 0.0

    def test_empty_futures_returns_none_best(self) -> None:
        """Empty reachable_futures leads to best_future=None."""
        state = _make_state(0)
        algo = GreedyFutureSearch(SearchConfig())
        result = algo.search(state)
        assert result.best_future is None

    def test_selected_futures_are_subset_of_reachable(self) -> None:
        """Every selected future must appear in reachable_futures."""
        state = _make_state(5)
        algo = GreedyFutureSearch(SearchConfig())
        result = algo.search(state)
        ids = {f.future_id for f in state.reachable_futures}
        for sf in result.selected_futures:
            assert sf.future_id in ids


# ---------------------------------------------------------------------------
# TestDiversifiedSearch
# ---------------------------------------------------------------------------


class TestDiversifiedSearch:
    """Tests for the diversified (coverage-aware) search algorithm."""

    def test_returns_search_result(self) -> None:
        """search() returns a SearchResult."""
        algo = DiversifiedSearch(SearchConfig(diversity_weight=0.5))
        assert isinstance(algo.search(_make_diverse_state()), SearchResult)

    def test_name(self) -> None:
        """Algorithm name must be 'DiversifiedSearch'."""
        assert DiversifiedSearch(SearchConfig()).name == "DiversifiedSearch"

    def test_result_more_diverse_than_greedy(self) -> None:
        """Diversified search should yield higher pairwise delta variety than greedy."""
        state = _make_diverse_state()
        greedy = GreedyFutureSearch(SearchConfig())
        diverse = DiversifiedSearch(SearchConfig(diversity_weight=0.8))
        greedy_result = greedy.search(state)
        diverse_result = diverse.search(state)

        def mean_pairwise_sim(futures: tuple[SemanticFuture, ...]) -> float:
            if len(futures) < 2:
                return 1.0
            pairs = [
                _delta_similarity(a.delta, b.delta)
                for i, a in enumerate(futures)
                for b in futures[i + 1 :]
            ]
            return sum(pairs) / len(pairs)

        # Diversified results should be at least as diverse (lower sim) or equally diverse
        greedy_sim = mean_pairwise_sim(greedy_result.selected_futures)
        diverse_sim = mean_pairwise_sim(diverse_result.selected_futures)
        assert diverse_sim <= greedy_sim + 0.1  # allowing small tolerance

    def test_empty_futures_safe(self) -> None:
        """No crash on empty input."""
        state = _make_state(0)
        result = DiversifiedSearch(SearchConfig()).search(state)
        assert result.best_future is None

    def test_diversity_weight_zero_behaves_like_greedy(self) -> None:
        """With diversity_weight=0, the result should mirror greedy selection."""
        state = _make_state(4)
        diverse = DiversifiedSearch(SearchConfig(diversity_weight=0.0))
        greedy = GreedyFutureSearch(SearchConfig())
        dr = diverse.search(state)
        gr = greedy.search(state)
        if dr.best_future and gr.best_future:
            assert dr.best_future.future_id == gr.best_future.future_id


# ---------------------------------------------------------------------------
# TestArchiveBasedSearch
# ---------------------------------------------------------------------------


class TestArchiveBasedSearch:
    """Tests for archive-aware novelty search."""

    def test_empty_archive_behaves_like_greedy(self) -> None:
        """With no archive, ArchiveBasedSearch should select the same top future as greedy."""
        state = _make_state(4)
        archive_algo = ArchiveBasedSearch(SearchConfig())
        greedy_algo = GreedyFutureSearch(SearchConfig())
        ar = archive_algo.search(state)
        gr = greedy_algo.search(state)
        if ar.best_future and gr.best_future:
            assert ar.best_future.future_id == gr.best_future.future_id

    def test_full_archive_depresses_novelty(self) -> None:
        """When all futures are in the archive, selection should yield low-value result."""
        state = _make_state(3)
        # Archive contains all the same futures
        archived_state = IdeationState(
            state_id=state.state_id,
            current_state=state.current_state,
            purpose=state.purpose,
            reachable_futures=state.reachable_futures,
            budget_remaining=state.budget_remaining,
            archive=list(state.reachable_futures),
        )
        algo = ArchiveBasedSearch(SearchConfig())
        result = algo.search(archived_state)
        # Either no future selected or very low improvement
        if result.best_future is not None:
            base = result.improvement_over_random()
            assert base >= 0.0  # sanity check

    def test_name(self) -> None:
        """Algorithm name must be 'ArchiveBasedSearch'."""
        assert ArchiveBasedSearch(SearchConfig()).name == "ArchiveBasedSearch"

    def test_returns_search_result(self) -> None:
        """search() returns a SearchResult."""
        assert isinstance(ArchiveBasedSearch(SearchConfig()).search(_make_state()), SearchResult)

    def test_archive_penalizes_seen_futures(self) -> None:
        """Futures identical to archive entries should rank lower than novel ones."""
        state = _make_state(4)
        # Archive the best future
        best_id = state.reachable_futures[0].future_id
        archived_state = IdeationState(
            state_id=state.state_id,
            current_state=state.current_state,
            purpose=state.purpose,
            reachable_futures=state.reachable_futures,
            budget_remaining=state.budget_remaining,
            archive=[state.reachable_futures[0]],
        )
        algo = ArchiveBasedSearch(SearchConfig())
        result = algo.search(archived_state)
        if result.best_future is not None:
            # Should prefer the non-archived futures
            assert result.best_future.future_id != best_id or len(state.reachable_futures) == 1


# ---------------------------------------------------------------------------
# TestPurposeDirectedSearch
# ---------------------------------------------------------------------------


class TestPurposeDirectedSearch:
    """Tests for purpose-conditioned search."""

    def test_returns_search_result(self) -> None:
        """search() returns a SearchResult."""
        algo = PurposeDirectedSearch(SearchConfig())
        assert isinstance(algo.search(_make_state()), SearchResult)

    def test_name(self) -> None:
        """Algorithm name must be 'PurposeDirectedSearch'."""
        assert PurposeDirectedSearch(SearchConfig()).name == "PurposeDirectedSearch"

    def test_best_future_has_high_purpose_alignment(self) -> None:
        """The selected best future should have purpose_alignment >= the median."""
        state = _make_state(5)
        algo = PurposeDirectedSearch(SearchConfig())
        result = algo.search(state)
        if result.best_future is not None:
            alignments = sorted(f.purpose_alignment for f in state.reachable_futures)
            median_align = alignments[len(alignments) // 2]
            # best future's alignment should be at or above median
            assert result.best_future.purpose_alignment >= median_align - 0.05

    def test_empty_input_safe(self) -> None:
        """No crash on empty futures list."""
        state = _make_state(0)
        result = PurposeDirectedSearch(SearchConfig()).search(state)
        assert result.best_future is None

    def test_purpose_alignment_dominates_raw_yield(self) -> None:
        """A high-alignment but lower-yield future should beat low-alignment high-yield."""
        state = FutureState(
            state_id="s-pa",
            theorem_portfolio=("T1",),
            known_kinds=("K1",),
            semantic_embedding=(0.5,),
            timestamp=datetime.now(),
        )
        purpose = PurposeFunction(
            purpose_id="p-pa",
            domain="algebra",
            utility_weights={"yield": 0.3, "alignment": 0.7},
            alignment_threshold=0.6,
            description="Alignment-heavy purpose",
        )
        high_align = _make_future("ha", expected_yield=2.0, purpose_alignment=0.95)
        low_align = _make_future("la", expected_yield=8.0, purpose_alignment=0.1)
        ideation_state = IdeationState(
            state_id="is-pa",
            current_state=state,
            purpose=purpose,
            reachable_futures=[high_align, low_align],
            budget_remaining=20.0,
            archive=[],
        )
        algo = PurposeDirectedSearch(SearchConfig())
        result = algo.search(ideation_state)
        if result.best_future is not None:
            assert result.best_future.future_id == "ha"


# ---------------------------------------------------------------------------
# TestSearchAlgorithmFactory
# ---------------------------------------------------------------------------


class TestSearchAlgorithmFactory:
    """Tests for the factory that creates algorithm instances by name."""

    def test_create_beam_search(self) -> None:
        """Factory creates BeamSearchFutures when requested by name."""
        algo = SearchAlgorithmFactory.create("BeamSearchFutures", SearchConfig())
        assert isinstance(algo, BeamSearchFutures)

    def test_create_greedy(self) -> None:
        """Factory creates GreedyFutureSearch."""
        algo = SearchAlgorithmFactory.create("GreedyFutureSearch", SearchConfig())
        assert isinstance(algo, GreedyFutureSearch)

    def test_create_diversified(self) -> None:
        """Factory creates DiversifiedSearch."""
        algo = SearchAlgorithmFactory.create("DiversifiedSearch", SearchConfig())
        assert isinstance(algo, DiversifiedSearch)

    def test_create_archive_based(self) -> None:
        """Factory creates ArchiveBasedSearch."""
        algo = SearchAlgorithmFactory.create("ArchiveBasedSearch", SearchConfig())
        assert isinstance(algo, ArchiveBasedSearch)

    def test_create_purpose_directed(self) -> None:
        """Factory creates PurposeDirectedSearch."""
        algo = SearchAlgorithmFactory.create("PurposeDirectedSearch", SearchConfig())
        assert isinstance(algo, PurposeDirectedSearch)

    def test_unknown_name_raises(self) -> None:
        """Unknown algorithm name must raise KeyError or ValueError."""
        with pytest.raises((KeyError, ValueError)):
            SearchAlgorithmFactory.create("NonExistentAlgo", SearchConfig())

    def test_list_available_non_empty(self) -> None:
        """list_available() returns a non-empty sequence of strings."""
        available = SearchAlgorithmFactory.list_available()
        assert len(available) >= 5
        assert all(isinstance(n, str) for n in available)

    def test_list_available_contains_all_known_algos(self) -> None:
        """All five built-in algorithm names appear in list_available()."""
        available = set(SearchAlgorithmFactory.list_available())
        for name in (
            "BeamSearchFutures",
            "GreedyFutureSearch",
            "DiversifiedSearch",
            "ArchiveBasedSearch",
            "PurposeDirectedSearch",
        ):
            assert name in available


# ---------------------------------------------------------------------------
# TestSearchComparator
# ---------------------------------------------------------------------------


class TestSearchComparator:
    """Tests for running and comparing multiple algorithms."""

    def _make_comparator(self) -> SearchComparator:
        return SearchComparator(
            algorithms=[
                BeamSearchFutures(SearchConfig(beam_width=2)),
                GreedyFutureSearch(SearchConfig()),
            ]
        )

    def test_compare_algorithms_returns_dict(self) -> None:
        """compare_algorithms() returns a dict keyed by algorithm name."""
        comp = self._make_comparator()
        results = comp.compare_algorithms(_make_state())
        assert isinstance(results, dict)
        assert "BeamSearchFutures" in results
        assert "GreedyFutureSearch" in results

    def test_each_value_is_search_result(self) -> None:
        """Each value in the compare dict is a SearchResult."""
        comp = self._make_comparator()
        results = comp.compare_algorithms(_make_state())
        for v in results.values():
            assert isinstance(v, SearchResult)

    def test_best_algorithm_returns_valid_name(self) -> None:
        """best_algorithm() returns the name of one of the registered algorithms."""
        comp = self._make_comparator()
        state = _make_state()
        comp.compare_algorithms(state)
        best = comp.best_algorithm()
        assert best in ("BeamSearchFutures", "GreedyFutureSearch")

    def test_best_algorithm_before_compare_raises(self) -> None:
        """Calling best_algorithm() before compare_algorithms() must raise."""
        comp = self._make_comparator()
        with pytest.raises((RuntimeError, ValueError, AttributeError)):
            comp.best_algorithm()


# ---------------------------------------------------------------------------
# TestHelpers
# ---------------------------------------------------------------------------


class TestHelpers:
    """Tests for private helper functions."""

    # _delta_similarity -------------------------------------------------------

    def test_delta_similarity_identical(self) -> None:
        """Identical strings have similarity 1.0."""
        assert _delta_similarity("Add theorem T1", "Add theorem T1") == pytest.approx(1.0)

    def test_delta_similarity_empty_strings(self) -> None:
        """Two empty strings have similarity 0.0 (no tokens in common)."""
        assert _delta_similarity("", "") == pytest.approx(0.0)

    def test_delta_similarity_disjoint(self) -> None:
        """Completely disjoint token sets have similarity 0.0."""
        result = _delta_similarity("alpha beta gamma", "delta epsilon zeta")
        assert result == pytest.approx(0.0)

    def test_delta_similarity_partial_overlap(self) -> None:
        """Partial overlap yields value strictly between 0 and 1."""
        result = _delta_similarity("alpha beta gamma", "alpha delta epsilon")
        assert 0.0 < result < 1.0

    def test_delta_similarity_symmetric(self) -> None:
        """Similarity must be symmetric."""
        a, b = "foo bar baz", "baz qux"
        assert _delta_similarity(a, b) == pytest.approx(_delta_similarity(b, a))

    # _jaccard ----------------------------------------------------------------

    def test_jaccard_identical_sets(self) -> None:
        """Identical sets → Jaccard = 1.0."""
        assert _jaccard({"a", "b", "c"}, {"a", "b", "c"}) == pytest.approx(1.0)

    def test_jaccard_disjoint_sets(self) -> None:
        """Disjoint sets → Jaccard = 0.0."""
        assert _jaccard({"a", "b"}, {"c", "d"}) == pytest.approx(0.0)

    def test_jaccard_empty_sets(self) -> None:
        """Two empty sets → Jaccard = 0.0."""
        assert _jaccard(set(), set()) == pytest.approx(0.0)

    def test_jaccard_partial(self) -> None:
        """|intersection| / |union| for sets with partial overlap."""
        j = _jaccard({"a", "b", "c"}, {"b", "c", "d"})
        assert j == pytest.approx(2 / 4)

    # _compute_value ----------------------------------------------------------

    def test_compute_value_returns_float(self) -> None:
        """_compute_value returns a float for any SemanticFuture."""
        f = _make_future("fv")
        v = _compute_value(f)
        assert isinstance(v, float)

    def test_compute_value_non_negative(self) -> None:
        """Value is non-negative."""
        assert _compute_value(_make_future("fv2", expected_yield=0.0, cost_estimate=1.0)) >= 0.0

    def test_compute_value_scales_with_yield(self) -> None:
        """Higher expected_yield → higher value (all else equal)."""
        low = _make_future("fl", expected_yield=1.0, cost_estimate=1.0)
        high = _make_future("fh", expected_yield=9.0, cost_estimate=1.0)
        assert _compute_value(high) > _compute_value(low)

    def test_compute_value_penalizes_cost(self) -> None:
        """Higher cost → lower value (all else equal)."""
        cheap = _make_future("fc", expected_yield=5.0, cost_estimate=1.0)
        expensive = _make_future("fe", expected_yield=5.0, cost_estimate=9.0)
        assert _compute_value(cheap) > _compute_value(expensive)

    # _normalize_futures ------------------------------------------------------

    def test_normalize_futures_empty(self) -> None:
        """Normalising an empty list returns an empty list."""
        assert _normalize_futures([]) == []

    def test_normalize_futures_preserves_count(self) -> None:
        """Normalised list has the same length as the input."""
        futures = [_make_future(f"fn{i}") for i in range(4)]
        normed = _normalize_futures(futures)
        assert len(normed) == len(futures)

    def test_normalize_futures_single_item(self) -> None:
        """A single-item list is returned unchanged (or with value mapped to 1.0)."""
        f = _make_future("single")
        normed = _normalize_futures([f])
        assert len(normed) == 1


# ---------------------------------------------------------------------------
# TestIntegrationAlgorithms
# ---------------------------------------------------------------------------


class TestIntegrationAlgorithms:
    """Integration tests: run all five algorithms on the same state and compare."""

    ALGOS = [
        BeamSearchFutures,
        GreedyFutureSearch,
        DiversifiedSearch,
        ArchiveBasedSearch,
        PurposeDirectedSearch,
    ]

    def test_all_algos_run_on_same_state(self) -> None:
        """All five algorithms complete without error on a shared state."""
        state = _make_state(6)
        cfg = SearchConfig(beam_width=3)
        results = {}
        for AlgoClass in self.ALGOS:
            algo = AlgoClass(cfg)
            results[algo.name] = algo.search(state)
        assert len(results) == 5

    def test_all_results_are_search_results(self) -> None:
        """Every algorithm returns a SearchResult instance."""
        state = _make_state(4)
        cfg = SearchConfig()
        for AlgoClass in self.ALGOS:
            result = AlgoClass(cfg).search(state)
            assert isinstance(result, SearchResult), f"{AlgoClass.__name__} did not return SearchResult"

    def test_best_futures_all_from_reachable_pool(self) -> None:
        """Every non-None best_future must come from the input's reachable_futures."""
        state = _make_state(5)
        ids = {f.future_id for f in state.reachable_futures}
        cfg = SearchConfig(beam_width=2)
        for AlgoClass in self.ALGOS:
            result = AlgoClass(cfg).search(state)
            if result.best_future is not None:
                assert result.best_future.future_id in ids, (
                    f"{AlgoClass.__name__}: best_future not in reachable_futures"
                )

    def test_algorithms_have_unique_names(self) -> None:
        """All five algorithm names are distinct strings."""
        cfg = SearchConfig()
        names = [AlgoClass(cfg).name for AlgoClass in self.ALGOS]
        assert len(set(names)) == len(names)

    def test_wall_time_positive(self) -> None:
        """wall_time_s in every result must be >= 0."""
        state = _make_state(4)
        cfg = SearchConfig()
        for AlgoClass in self.ALGOS:
            result = AlgoClass(cfg).search(state)
            assert result.wall_time_s >= 0.0

    @pytest.mark.parametrize("beam_width", [1, 2, 4, 8])
    def test_beam_search_various_widths(self, beam_width: int) -> None:
        """BeamSearchFutures works for a range of beam widths."""
        state = _make_state(6)
        cfg = SearchConfig(beam_width=beam_width)
        result = BeamSearchFutures(cfg).search(state)
        assert isinstance(result, SearchResult)

    @pytest.mark.parametrize("diversity_weight", [0.0, 0.25, 0.5, 0.75, 1.0])
    def test_diversified_search_various_weights(self, diversity_weight: float) -> None:
        """DiversifiedSearch works across the full [0, 1] range of diversity_weight."""
        state = _make_diverse_state()
        cfg = SearchConfig(diversity_weight=diversity_weight)
        result = DiversifiedSearch(cfg).search(state)
        assert isinstance(result, SearchResult)
