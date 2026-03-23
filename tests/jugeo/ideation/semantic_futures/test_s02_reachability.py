"""Tests for jugeo.ideation.semantic_futures.s02_reachability.

Covers ReachabilityModel, BridgeProbability, ReachabilityEstimator,
TransitionGraph, PathFinder, ReachabilityCache, helper functions, and
integration scenarios.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "src" / "jugeo").exists())
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import pytest

from jugeo.ideation.semantic_futures.s02_reachability import (
    ReachabilityEstimator,
    ReachabilityModel,
    ReachabilityModelType,
    BridgeProbability,
    TransitionGraph,
    PathFinder,
    ReachabilityCache,
    DEFAULT_MODEL,
    _exponential_decay,
    _sigmoid,
    _linear_decay,
    _semantic_distance,
    _create_model_from_type,
)
from jugeo.ideation.semantic_futures.models import (
    SemanticFuture,
    FutureState,
    PurposeFunction,
    FutureTag,
)

try:
    from jugeo.ideation.regimes import IdeationRegime  # type: ignore[import]
    _HAS_REGIMES = True
except ImportError:
    _HAS_REGIMES = False


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_state(
    state_id: str = "s0",
    description: str = "base state",
    domain: str = "mathematics",
    embedding: tuple[float, ...] = (1.0, 0.0, 0.0),
) -> FutureState:
    """Return a minimal :class:`FutureState` suitable for tests."""
    return FutureState(
        state_id=state_id,
        description=description,
        domain=domain,
        embedding=embedding,
    )


def _make_future(
    future_id: str = "f0",
    delta: str = "explore topology",
    source_state_id: str = "s0",
    reachability: float = 0.7,
    purpose_alignment: float = 0.6,
) -> SemanticFuture:
    """Return a minimal :class:`SemanticFuture` suitable for tests."""
    return SemanticFuture(
        future_id=future_id,
        delta=delta,
        source_state_id=source_state_id,
        reachability=reachability,
        purpose_alignment=purpose_alignment,
    )


# ---------------------------------------------------------------------------
# TestReachabilityModel
# ---------------------------------------------------------------------------


class TestReachabilityModel:
    """Tests for :class:`ReachabilityModel` construction, compute, and serialisation."""

    @pytest.mark.parametrize("mt", list(ReachabilityModelType))
    def test_creation_with_each_model_type(self, mt: ReachabilityModelType) -> None:
        """Every ReachabilityModelType should instantiate without error."""
        model = ReachabilityModel(model_type=mt)
        assert model.model_type == mt

    @pytest.mark.parametrize("mt", list(ReachabilityModelType))
    @pytest.mark.parametrize("dist", [0.0, 0.1, 0.5, 1.0, 2.0, 10.0])
    def test_compute_returns_in_range(
        self, mt: ReachabilityModelType, dist: float
    ) -> None:
        """compute() must always return a value in [0, 1] for any distance."""
        model = ReachabilityModel(model_type=mt, params={"lam": 1.0, "slope": 1.0})
        result = model.compute(dist)
        assert 0.0 <= result <= 1.0, f"compute({dist}) = {result} out of [0,1] for {mt}"

    def test_to_dict_from_dict_round_trip(self) -> None:
        """Serialisation round-trip must produce an equivalent model."""
        original = ReachabilityModel(
            model_type=ReachabilityModelType.SIGMOID,
            params={"steepness": 2.0, "midpoint": 0.3},
            baseline=0.4,
        )
        d = original.to_dict()
        restored = ReachabilityModel.from_dict(d)
        assert restored.model_type == original.model_type
        assert restored.baseline == pytest.approx(original.baseline)
        assert restored.params.get("steepness") == pytest.approx(2.0)
        assert restored.params.get("midpoint") == pytest.approx(0.3)

    def test_calibrate_classmethod(self) -> None:
        """calibrate() must return a ReachabilityModel from paired observations."""
        model = ReachabilityModel.calibrate(
            distances=[0.0, 1.0, 2.0],
            targets=[1.0, 0.6, 0.2],
        )
        assert isinstance(model, ReachabilityModel)
        assert model.model_type == ReachabilityModelType.EXPONENTIAL

    def test_calibrate_returns_model_on_empty_input(self) -> None:
        """calibrate() with empty lists must return a default model, not raise."""
        model = ReachabilityModel.calibrate(distances=[], targets=[])
        assert isinstance(model, ReachabilityModel)

    def test_exponential_at_zero_is_one(self) -> None:
        """EXPONENTIAL model must return ~1.0 at distance 0."""
        model = ReachabilityModel(
            model_type=ReachabilityModelType.EXPONENTIAL,
            params={"lam": 1.0},
        )
        assert model.compute(0.0) == pytest.approx(1.0, abs=1e-6)

    def test_linear_at_zero_is_one_or_less(self) -> None:
        """LINEAR model at distance 0 must return a value ≤ 1.0."""
        model = ReachabilityModel(
            model_type=ReachabilityModelType.LINEAR,
            params={"slope": 1.0},
        )
        result = model.compute(0.0)
        assert result <= 1.0
        assert result >= 0.0

    def test_constant_model_returns_baseline(self) -> None:
        """CONSTANT model must always return the baseline regardless of distance."""
        model = ReachabilityModel(
            model_type=ReachabilityModelType.CONSTANT,
            baseline=0.42,
        )
        for dist in [0.0, 0.5, 5.0, 100.0]:
            assert model.compute(dist) == pytest.approx(0.42, abs=1e-6)

    def test_calibrate_linear_model_type(self) -> None:
        """calibrate() should respect a non-default model_type argument."""
        model = ReachabilityModel.calibrate(
            distances=[0.0, 1.0, 2.0],
            targets=[1.0, 0.5, 0.0],
            model_type=ReachabilityModelType.LINEAR,
        )
        assert model.model_type == ReachabilityModelType.LINEAR


# ---------------------------------------------------------------------------
# TestBridgeProbability
# ---------------------------------------------------------------------------


class TestBridgeProbability:
    """Tests for :class:`BridgeProbability`."""

    def test_compute_returns_in_range(self) -> None:
        """compute() must return a value in [0, 1]."""
        bp = BridgeProbability()
        state = _make_state()
        future = _make_future()
        result = bp.compute(state, future)
        assert 0.0 <= result <= 1.0

    def test_compute_with_no_embedding(self) -> None:
        """compute() must work even when the state has an empty embedding."""
        bp = BridgeProbability()
        state = _make_state(embedding=())
        future = _make_future()
        result = bp.compute(state, future)
        assert 0.0 <= result <= 1.0

    def test_available_bridges_nonempty(self) -> None:
        """available_bridges() must return at least one bridge name."""
        bp = BridgeProbability()
        state = _make_state(domain="topology")
        bridges = bp.available_bridges(state)
        assert len(bridges) >= 1
        assert all(isinstance(b, str) for b in bridges)

    def test_available_bridges_always_includes_analogy(self) -> None:
        """'analogy' must always be present in available bridges."""
        bp = BridgeProbability()
        state = _make_state(domain="")
        assert "analogy" in bp.available_bridges(state)

    def test_semantic_gap_is_nonnegative(self) -> None:
        """_semantic_gap() must always return a non-negative float."""
        bp = BridgeProbability()
        state = _make_state()
        future = _make_future()
        gap = bp._semantic_gap(state, future)
        assert gap >= 0.0

    def test_semantic_gap_no_embedding(self) -> None:
        """_semantic_gap() must be non-negative when there is no embedding."""
        bp = BridgeProbability()
        state = _make_state(embedding=(), description="")
        future = _make_future(delta="")
        gap = bp._semantic_gap(state, future)
        assert gap >= 0.0


# ---------------------------------------------------------------------------
# TestReachabilityEstimator
# ---------------------------------------------------------------------------


class TestReachabilityEstimator:
    """Tests for :class:`ReachabilityEstimator`."""

    def test_estimate_returns_in_range(self) -> None:
        """estimate() must return a value in [0, 1]."""
        estimator = ReachabilityEstimator()
        state = _make_state()
        future = _make_future()
        result = estimator.estimate(state, future)
        assert 0.0 <= result <= 1.0

    def test_estimate_with_no_embedding(self) -> None:
        """estimate() must cope with a state that has no embedding."""
        estimator = ReachabilityEstimator()
        state = _make_state(embedding=())
        future = _make_future()
        result = estimator.estimate(state, future)
        assert 0.0 <= result <= 1.0

    def test_estimate_batch_correct_length(self) -> None:
        """estimate_batch() must return a list of the same length as the input."""
        estimator = ReachabilityEstimator()
        state = _make_state()
        futures = [_make_future(future_id=f"f{i}") for i in range(5)]
        results = estimator.estimate_batch(state, futures)
        assert len(results) == 5
        for r in results:
            assert 0.0 <= r <= 1.0

    def test_estimate_batch_empty(self) -> None:
        """estimate_batch() with an empty list must return an empty list."""
        estimator = ReachabilityEstimator()
        state = _make_state()
        assert estimator.estimate_batch(state, []) == []

    def test_update_calibration_does_not_crash(self) -> None:
        """update_calibration() must not raise for any valid input."""
        estimator = ReachabilityEstimator()
        state = _make_state()
        future = _make_future()
        estimator.update_calibration(state, future, observed=0.8)
        estimator.update_calibration(state, future, observed=0.0)
        estimator.update_calibration(state, future, observed=1.0)

    def test_confidence_interval_valid(self) -> None:
        """confidence_interval() must return (lo, hi) with lo ≤ hi, both in [0, 1]."""
        estimator = ReachabilityEstimator()
        state = _make_state()
        future = _make_future()
        lo, hi = estimator.confidence_interval(state, future)
        assert 0.0 <= lo <= hi <= 1.0

    def test_confidence_interval_default_confidence(self) -> None:
        """Default confidence (0.95) must produce a narrower interval than 0.50."""
        estimator = ReachabilityEstimator()
        state = _make_state()
        future = _make_future()
        lo_95, hi_95 = estimator.confidence_interval(state, future, confidence=0.95)
        lo_50, hi_50 = estimator.confidence_interval(state, future, confidence=0.50)
        width_95 = hi_95 - lo_95
        width_50 = hi_50 - lo_50
        assert width_95 <= width_50

    def test_estimate_is_cached(self) -> None:
        """A second call to estimate() for the same inputs should return the same value."""
        cache = ReachabilityCache()
        estimator = ReachabilityEstimator(cache=cache)
        state = _make_state()
        future = _make_future()
        r1 = estimator.estimate(state, future)
        r2 = estimator.estimate(state, future)
        assert r1 == pytest.approx(r2)

    def test_custom_model_used(self) -> None:
        """Estimator should use the model passed to its constructor."""
        constant_model = ReachabilityModel(
            model_type=ReachabilityModelType.CONSTANT,
            baseline=0.99,
        )
        estimator = ReachabilityEstimator(model=constant_model)
        state = _make_state()
        future = _make_future()
        result = estimator.estimate(state, future)
        # Result should be influenced by the constant 0.99 baseline.
        assert result > 0.5


# ---------------------------------------------------------------------------
# TestTransitionGraph
# ---------------------------------------------------------------------------


class TestTransitionGraph:
    """Tests for :class:`TransitionGraph`."""

    def test_add_state_and_transition(self) -> None:
        """States and transitions should be stored without error."""
        g = TransitionGraph()
        g.add_state("A")
        g.add_state("B", embedding=(1.0, 2.0))
        g.add_transition("A", "B", probability=0.8, cost=1.0)
        # No assertion needed beyond no exception; validate via reachable_from.
        assert "B" in g.reachable_from("A", max_hops=1)

    def test_reachable_from_direct(self) -> None:
        """A direct neighbour must appear in reachable_from(max_hops=1)."""
        g = TransitionGraph()
        g.add_transition("A", "B", probability=0.9)
        assert "B" in g.reachable_from("A", max_hops=1)

    def test_reachable_from_multi_hop(self) -> None:
        """A node two hops away must appear in reachable_from(max_hops=2)."""
        g = TransitionGraph()
        g.add_transition("A", "B", probability=0.9)
        g.add_transition("B", "C", probability=0.8)
        reachable = g.reachable_from("A", max_hops=2)
        assert "C" in reachable

    def test_reachable_from_max_hops_limit(self) -> None:
        """max_hops=0 must return an empty set (no transitions taken)."""
        g = TransitionGraph()
        g.add_transition("A", "B", probability=0.9)
        assert g.reachable_from("A", max_hops=0) == set()

    def test_reachable_from_max_hops_one_excludes_two_hop(self) -> None:
        """A node exactly 2 hops away must NOT appear with max_hops=1."""
        g = TransitionGraph()
        g.add_transition("A", "B", probability=0.9)
        g.add_transition("B", "C", probability=0.8)
        reachable = g.reachable_from("A", max_hops=1)
        assert "C" not in reachable

    def test_probability_of_path(self) -> None:
        """Probability of a single-hop path must equal the edge probability."""
        g = TransitionGraph()
        g.add_transition("A", "B", probability=0.7)
        prob = g.probability_of_path(["A", "B"])
        assert prob == pytest.approx(0.7, abs=1e-6)

    def test_probability_of_two_hop_path(self) -> None:
        """Probability of a two-hop path must be the product of edge probabilities."""
        g = TransitionGraph()
        g.add_transition("A", "B", probability=0.8)
        g.add_transition("B", "C", probability=0.5)
        prob = g.probability_of_path(["A", "B", "C"])
        assert prob == pytest.approx(0.4, abs=1e-6)

    def test_probability_of_empty_path(self) -> None:
        """probability_of_path([]) must return 1.0 (vacuously true)."""
        g = TransitionGraph()
        assert g.probability_of_path([]) == pytest.approx(1.0)

    def test_probability_of_single_node_path(self) -> None:
        """probability_of_path(['A']) must return 1.0 (no edges traversed)."""
        g = TransitionGraph()
        g.add_state("A")
        assert g.probability_of_path(["A"]) == pytest.approx(1.0)

    def test_shortest_path_found(self) -> None:
        """shortest_path must return an ordered list of state IDs."""
        g = TransitionGraph()
        g.add_transition("A", "B", probability=0.9)
        g.add_transition("B", "C", probability=0.8)
        path = g.shortest_path("A", "C")
        assert path is not None
        assert path[0] == "A"
        assert path[-1] == "C"

    def test_shortest_path_not_found(self) -> None:
        """shortest_path must return None for disconnected nodes."""
        g = TransitionGraph()
        g.add_state("X")
        g.add_state("Y")
        assert g.shortest_path("X", "Y") is None

    def test_shortest_path_missing_state(self) -> None:
        """shortest_path must return None when a state ID does not exist."""
        g = TransitionGraph()
        g.add_state("A")
        assert g.shortest_path("A", "DOES_NOT_EXIST") is None

    def test_to_dict_from_dict_round_trip(self) -> None:
        """Serialisation round-trip must preserve states and edges."""
        g = TransitionGraph()
        g.add_transition("A", "B", probability=0.7, cost=2.0)
        g.add_transition("B", "C", probability=0.5, cost=1.5)
        d = g.to_dict()
        restored = TransitionGraph.from_dict(d)
        assert "A" in restored._states
        assert "B" in restored._states
        assert "C" in restored._states
        edges_from_a = restored._edges.get("A", [])
        assert any(dst == "B" for dst, _, _ in edges_from_a)

    def test_add_transition_auto_registers_states(self) -> None:
        """add_transition must register both endpoints even if not yet added."""
        g = TransitionGraph()
        g.add_transition("NEW_SRC", "NEW_DST", probability=0.5)
        assert "NEW_SRC" in g._states
        assert "NEW_DST" in g._states


# ---------------------------------------------------------------------------
# TestPathFinder
# ---------------------------------------------------------------------------


class TestPathFinder:
    """Tests for :class:`PathFinder`."""

    def _three_node_graph(self) -> TransitionGraph:
        g = TransitionGraph()
        g.add_transition("A", "B", probability=0.9, cost=1.0)
        g.add_transition("B", "C", probability=0.8, cost=1.0)
        return g

    def test_find_path_found(self) -> None:
        """find_path must return a non-None list of state IDs when a path exists."""
        pf = PathFinder(self._three_node_graph())
        path = pf.find_path("A", "C")
        assert path is not None
        assert path[0] == "A"
        assert path[-1] == "C"

    def test_find_path_not_found(self) -> None:
        """find_path must return None when no path exists."""
        g = TransitionGraph()
        g.add_state("X")
        g.add_state("Y")
        pf = PathFinder(g)
        assert pf.find_path("X", "Y") is None

    def test_find_k_shortest_returns_at_most_k(self) -> None:
        """find_k_shortest must return at most k paths."""
        g = self._three_node_graph()
        g.add_transition("A", "C", probability=0.5, cost=3.0)  # alternate path
        pf = PathFinder(g)
        paths = pf.find_k_shortest("A", "C", k=3)
        assert len(paths) <= 3
        for p in paths:
            assert p[0] == "A"
            assert p[-1] == "C"

    def test_find_k_shortest_empty_graph(self) -> None:
        """find_k_shortest on disconnected nodes must return an empty list."""
        g = TransitionGraph()
        g.add_state("P")
        g.add_state("Q")
        pf = PathFinder(g)
        assert pf.find_k_shortest("P", "Q", k=3) == []

    def test_reachability_via_paths_in_range(self) -> None:
        """reachability_via_paths must return a value in [0, 1]."""
        pf = PathFinder(self._three_node_graph())
        score = pf.reachability_via_paths("A", "C")
        assert 0.0 <= score <= 1.0

    def test_reachability_via_paths_disconnected(self) -> None:
        """reachability_via_paths must return 0.0 when no path exists."""
        g = TransitionGraph()
        g.add_state("M")
        g.add_state("N")
        pf = PathFinder(g)
        assert pf.reachability_via_paths("M", "N") == pytest.approx(0.0)

    def test_find_k_shortest_k_equals_one(self) -> None:
        """find_k_shortest with k=1 must return at most one path."""
        pf = PathFinder(self._three_node_graph())
        paths = pf.find_k_shortest("A", "C", k=1)
        assert len(paths) <= 1


# ---------------------------------------------------------------------------
# TestReachabilityCache
# ---------------------------------------------------------------------------


class TestReachabilityCache:
    """Tests for :class:`ReachabilityCache`."""

    def test_get_miss_returns_none(self) -> None:
        """get() on an absent key must return None."""
        cache = ReachabilityCache()
        assert cache.get("missing_key") is None

    def test_set_then_get(self) -> None:
        """set() followed by get() must return the stored value."""
        cache = ReachabilityCache()
        cache.set("k1", 0.75)
        assert cache.get("k1") == pytest.approx(0.75)

    def test_clear(self) -> None:
        """clear() must remove all entries."""
        cache = ReachabilityCache()
        cache.set("a", 0.1)
        cache.set("b", 0.2)
        cache.clear()
        assert cache.size() == 0
        assert cache.get("a") is None

    def test_size(self) -> None:
        """size() must reflect the number of stored entries."""
        cache = ReachabilityCache()
        assert cache.size() == 0
        cache.set("x", 0.5)
        assert cache.size() == 1
        cache.set("y", 0.6)
        assert cache.size() == 2

    def test_hit_rate_zero_when_empty(self) -> None:
        """hit_rate() must return 0.0 when no lookups have been made."""
        cache = ReachabilityCache()
        assert cache.hit_rate() == pytest.approx(0.0)

    def test_hit_rate_after_hits(self) -> None:
        """hit_rate() must rise after successful gets."""
        cache = ReachabilityCache()
        cache.set("k", 0.9)
        _ = cache.get("k")  # hit
        _ = cache.get("k")  # hit
        _ = cache.get("miss")  # miss
        # 2 hits, 1 miss → rate = 2/3
        assert cache.hit_rate() == pytest.approx(2 / 3, abs=1e-6)

    def test_eviction_at_capacity(self) -> None:
        """Cache must not exceed maxsize; oldest entry should be evicted."""
        cache = ReachabilityCache(maxsize=3)
        cache.set("a", 0.1)
        cache.set("b", 0.2)
        cache.set("c", 0.3)
        cache.set("d", 0.4)  # should evict "a"
        assert cache.size() == 3
        assert cache.get("a") is None  # evicted

    def test_overwrite_existing_key(self) -> None:
        """set() on an existing key must update the value."""
        cache = ReachabilityCache()
        cache.set("k", 0.1)
        cache.set("k", 0.9)
        assert cache.get("k") == pytest.approx(0.9)


# ---------------------------------------------------------------------------
# TestDefaultModel
# ---------------------------------------------------------------------------


class TestDefaultModel:
    """Tests for the module-level :data:`DEFAULT_MODEL` constant."""

    def test_default_model_exists(self) -> None:
        """DEFAULT_MODEL must be a ReachabilityModel instance."""
        assert isinstance(DEFAULT_MODEL, ReachabilityModel)

    def test_default_model_computes_valid_values(self) -> None:
        """DEFAULT_MODEL.compute() must return values in [0, 1]."""
        for dist in [0.0, 0.5, 1.0, 5.0]:
            result = DEFAULT_MODEL.compute(dist)
            assert 0.0 <= result <= 1.0

    def test_default_model_is_exponential(self) -> None:
        """DEFAULT_MODEL must use the EXPONENTIAL decay family."""
        assert DEFAULT_MODEL.model_type == ReachabilityModelType.EXPONENTIAL

    def test_exponential_at_zero_is_one(self) -> None:
        """_exponential_decay(0) must equal exactly 1.0."""
        assert _exponential_decay(0.0) == pytest.approx(1.0)

    def test_exponential_at_large_distance_near_zero(self) -> None:
        """_exponential_decay(1000) must be very close to 0."""
        assert _exponential_decay(1000.0) < 0.01


# ---------------------------------------------------------------------------
# TestHelpers
# ---------------------------------------------------------------------------


class TestHelpers:
    """Tests for module-level helper functions."""

    def test_exponential_decay_monotone_decreasing(self) -> None:
        """_exponential_decay must be strictly decreasing for positive distances."""
        assert _exponential_decay(0.0) > _exponential_decay(1.0) > _exponential_decay(2.0)

    def test_exponential_decay_boundary(self) -> None:
        """_exponential_decay(0) must return exactly 1.0."""
        assert _exponential_decay(0.0) == pytest.approx(1.0)

    def test_exponential_decay_negative_distance_returns_one(self) -> None:
        """Negative distance should be treated as 0 and return 1.0."""
        assert _exponential_decay(-5.0) == pytest.approx(1.0)

    def test_sigmoid_s_shaped(self) -> None:
        """_sigmoid must be in (0, 1) and its output must decrease with distance."""
        low = _sigmoid(0.0, steepness=5.0, midpoint=0.5)
        mid = _sigmoid(0.5, steepness=5.0, midpoint=0.5)
        high = _sigmoid(1.0, steepness=5.0, midpoint=0.5)
        assert 0.0 < high < mid < low < 1.0
        # At the midpoint, value should be close to 0.5.
        assert mid == pytest.approx(0.5, abs=0.01)

    def test_sigmoid_output_in_open_interval(self) -> None:
        """_sigmoid output must always be strictly inside (0, 1)."""
        for x in [-10.0, 0.0, 0.5, 10.0, 100.0]:
            v = _sigmoid(x)
            assert 0.0 < v < 1.0

    def test_linear_decay_clamps_at_zero(self) -> None:
        """_linear_decay(10) with default slope must clamp to 0.0."""
        assert _linear_decay(10.0) == pytest.approx(0.0)

    def test_linear_decay_at_zero(self) -> None:
        """_linear_decay(0) must return 1.0."""
        assert _linear_decay(0.0) == pytest.approx(1.0)

    def test_linear_decay_midpoint(self) -> None:
        """_linear_decay(0.5) with slope=1 must return 0.5."""
        assert _linear_decay(0.5, slope=1.0) == pytest.approx(0.5)

    def test_semantic_distance_identical(self) -> None:
        """_semantic_distance(v, v) must return 0.0 (or very close)."""
        v = (1.0, 2.0, 3.0)
        assert _semantic_distance(v, v) == pytest.approx(0.0, abs=1e-9)

    def test_semantic_distance_orthogonal(self) -> None:
        """Orthogonal vectors must have cosine distance of 0.5."""
        a = (1.0, 0.0)
        b = (0.0, 1.0)
        dist = _semantic_distance(a, b)
        assert dist == pytest.approx(0.5, abs=1e-6)

    def test_semantic_distance_empty(self) -> None:
        """_semantic_distance((), ()) must return a float in [0, 1]."""
        result = _semantic_distance((), ())
        assert isinstance(result, float)
        assert 0.0 <= result <= 1.0

    def test_semantic_distance_mismatched_lengths(self) -> None:
        """Mismatched-length vectors must return 1.0 (fully different)."""
        a = (1.0, 0.0)
        b = (1.0, 0.0, 0.0)
        assert _semantic_distance(a, b) == pytest.approx(1.0)

    def test_create_model_from_type(self) -> None:
        """_create_model_from_type must return a ReachabilityModel."""
        model = _create_model_from_type(ReachabilityModelType.EXPONENTIAL, lam=2.0)
        assert isinstance(model, ReachabilityModel)
        assert model.model_type == ReachabilityModelType.EXPONENTIAL
        assert model.params.get("lam") == pytest.approx(2.0)

    @pytest.mark.parametrize("mt", list(ReachabilityModelType))
    def test_create_model_from_type_all_variants(self, mt: ReachabilityModelType) -> None:
        """_create_model_from_type must succeed for every model type."""
        model = _create_model_from_type(mt)
        assert isinstance(model, ReachabilityModel)
        assert model.model_type == mt


# ---------------------------------------------------------------------------
# TestIntegrationReachability
# ---------------------------------------------------------------------------


class TestIntegrationReachability:
    """End-to-end integration tests combining multiple components."""

    def test_full_pipeline_with_future_state_and_semantic_future(self) -> None:
        """A complete estimate pipeline should produce a valid [0,1] score."""
        state = FutureState(
            state_id="state_math",
            description="Studying algebraic topology",
            domain="mathematics",
            embedding=(0.8, 0.6, 0.0),
        )
        future = SemanticFuture(
            future_id="future_01",
            delta="Explore homotopy theory",
            source_state_id="state_math",
            reachability=0.75,
            purpose_alignment=0.80,
        )
        model = ReachabilityModel(
            model_type=ReachabilityModelType.EXPONENTIAL,
            params={"lam": 1.5},
        )
        cache = ReachabilityCache()
        estimator = ReachabilityEstimator(model=model, cache=cache)

        score = estimator.estimate(state, future)
        assert 0.0 <= score <= 1.0

        # Second call should hit the cache.
        score2 = estimator.estimate(state, future)
        assert score == pytest.approx(score2)
        assert cache.hit_rate() > 0.0

    def test_near_future_higher_reachability_than_far(self) -> None:
        """A 'near' future (high reachability, same domain) should score ≥ far future."""
        state = FutureState(
            state_id="s",
            description="current state in algebra",
            domain="algebra",
            embedding=(1.0, 0.0),
        )
        near_future = SemanticFuture(
            future_id="near",
            delta="refine algebraic structures",
            source_state_id="s",
            reachability=0.9,
            purpose_alignment=0.9,
        )
        far_future = SemanticFuture(
            future_id="far",
            delta="explore unrelated domain",
            source_state_id="s",
            reachability=0.1,
            purpose_alignment=0.1,
        )
        estimator = ReachabilityEstimator()
        near_score = estimator.estimate(state, near_future)
        far_score = estimator.estimate(state, far_future)
        assert near_score >= far_score, (
            f"Expected near ({near_score:.3f}) >= far ({far_score:.3f})"
        )

    def test_graph_pathfinder_integration(self) -> None:
        """TransitionGraph and PathFinder must work together for multi-hop paths."""
        g = TransitionGraph()
        g.add_transition("root", "mid", probability=0.8, cost=1.0)
        g.add_transition("mid", "leaf", probability=0.7, cost=1.0)
        g.add_transition("root", "leaf", probability=0.3, cost=3.0)

        pf = PathFinder(g)
        path = pf.find_path("root", "leaf")
        assert path is not None
        assert path[0] == "root"
        assert path[-1] == "leaf"

        score = pf.reachability_via_paths("root", "leaf")
        assert 0.0 <= score <= 1.0

    def test_batch_estimate_all_in_range(self) -> None:
        """All scores from estimate_batch must lie in [0, 1]."""
        state = _make_state()
        futures = [
            _make_future(
                future_id=f"f{i}",
                reachability=round(i * 0.1, 1),
                purpose_alignment=round(i * 0.1, 1),
            )
            for i in range(1, 10)
        ]
        estimator = ReachabilityEstimator()
        scores = estimator.estimate_batch(state, futures)
        for s in scores:
            assert 0.0 <= s <= 1.0

    def test_update_calibration_then_estimate(self) -> None:
        """After update_calibration, the next estimate must still be in [0, 1]."""
        estimator = ReachabilityEstimator()
        state = _make_state()
        future = _make_future()
        estimator.update_calibration(state, future, observed=0.65)
        score = estimator.estimate(state, future)
        assert 0.0 <= score <= 1.0

    @pytest.mark.skipif(not _HAS_REGIMES, reason="regimes module not available")
    def test_regime_import_does_not_break_module(self) -> None:
        """If IdeationRegime is importable, basic estimation must still work."""
        estimator = ReachabilityEstimator()
        state = _make_state()
        future = _make_future()
        assert 0.0 <= estimator.estimate(state, future) <= 1.0


# ---------------------------------------------------------------------------
# Parametrised standalone test
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "lam,distance,expected_gt",
    [
        (1.0, 0.0, 0.99),  # at origin, value must be > 0.99
        (1.0, 1.0, 0.3),   # moderate distance, still meaningfully non-zero
        (2.0, 0.5, 0.3),   # faster decay, still > 0.3
    ],
)
def test_exponential_decay_parametrized(
    lam: float, distance: float, expected_gt: float
) -> None:
    """_exponential_decay must produce values above the stated lower bounds.

    Parameters
    ----------
    lam:
        Decay rate used for this test case.
    distance:
        Input distance value.
    expected_gt:
        The returned value must be *greater than* this threshold.
    """
    result = _exponential_decay(distance, lam=lam)
    assert result > expected_gt, (
        f"_exponential_decay({distance}, lam={lam}) = {result:.4f} "
        f"expected > {expected_gt}"
    )
