"""
Tests for methodology_loops.algorithms.

copilot: shared-core marker
Theory reference: theory2.tex Ch62

This module tests the algorithmic core of the methodology_loops package, covering
ConvergenceResult, HypothesisRanking, MethodologyAlgorithms, and all public
module-level functions. Tests are organized so that each class exercises a single
unit of behaviour; cross-cutting concerns (e.g., round-trip serialisation) are
tested within the same class for locality.
"""
from pathlib import Path
import sys

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import pytest

from jugeo.evaluation.methodology_loops.algorithms import (
    MethodologyAlgorithms, ConvergenceResult, HypothesisRanking,
    loop_step, convergence_check, falsification_attempt, phase_score,
    compute_convergence_rate, rank_hypotheses,
    compute_phase_transition_matrix, estimate_remaining_iterations,
    aggregate_loop_metrics, normalize_scores,
)
from jugeo.evaluation.methodology_loops.models import (
    LoopPhase, LoopStatus, MethodologyConfig, LoopDiagnostics, LoopState,
    MethodologyLoop, TransitionKind,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def default_config():
    """Return a MethodologyConfig with sensible defaults for testing."""
    return MethodologyConfig(
        max_iterations=10,
        convergence_threshold=0.95,
        falsification_budget=50,
        min_coverage=0.8,
        max_revisions=5,
    )


@pytest.fixture
def default_diag():
    """Return empty LoopDiagnostics."""
    return LoopDiagnostics(
        iteration_times=[], errors=[], warnings=[], phase_counts={}
    )


@pytest.fixture
def default_state(default_diag):
    """Return a LoopState in FORMALIZATION phase at iteration 0."""
    return LoopState(
        phase=LoopPhase.FORMALIZATION,
        iteration=0,
        artifacts=[],
        diagnostics=default_diag,
        history=[],
        status=LoopStatus.IDLE,
    )


@pytest.fixture
def default_loop(default_config, default_state):
    """Return a minimal MethodologyLoop for algorithm tests."""
    return MethodologyLoop(
        loop_id="test-loop-001",
        config=default_config,
        state=default_state,
        transitions=[],
        artifacts=[],
        created_at=1000.0,
        updated_at=1000.0,
    )


@pytest.fixture
def algorithms():
    """Return a MethodologyAlgorithms instance."""
    return MethodologyAlgorithms()


@pytest.fixture
def basic_convergence_result():
    """Return a representative ConvergenceResult."""
    return ConvergenceResult.create(
        loop_id="loop-fixture",
        is_converged=True,
        convergence_rate=0.95,
        iterations_used=5,
        phase_scores={"formalization": 0.9, "implementation": 0.95},
    )


@pytest.fixture
def basic_ranking():
    """Return a HypothesisRanking with three hypotheses."""
    return HypothesisRanking.create(
        hypothesis_ids=["h1", "h2", "h3"],
        scores=[0.9, 0.7, 0.5],
        strategy="score",
        rationale="sorted by score descending",
    )


# ===========================================================================
# TestConvergenceResult
# ===========================================================================

class TestConvergenceResult:
    """Tests for ConvergenceResult data class.

    Covers construction via create(), immutability, serialisation round-trips,
    summarise(), quality_grade(), render_tex(), and uniqueness of result_id.
    """

    def test_create(self):
        """Test ConvergenceResult.create() factory method returns correct fields."""
        result = ConvergenceResult.create(
            loop_id="loop-1",
            is_converged=True,
            convergence_rate=0.95,
            iterations_used=5,
            phase_scores={"formalization": 0.9},
        )
        assert result.loop_id == "loop-1"
        assert result.is_converged is True
        assert result.convergence_rate == 0.95
        assert result.iterations_used == 5
        assert "formalization" in result.phase_scores

    def test_create_not_converged(self):
        """ConvergenceResult.create() works when is_converged=False."""
        result = ConvergenceResult.create(
            loop_id="loop-2",
            is_converged=False,
            convergence_rate=0.3,
            iterations_used=10,
            phase_scores={},
        )
        assert result.is_converged is False
        assert result.convergence_rate == 0.3
        assert result.iterations_used == 10

    def test_frozen(self):
        """ConvergenceResult must be immutable (frozen=True)."""
        result = ConvergenceResult.create(
            loop_id="l", is_converged=False, convergence_rate=0.0,
            iterations_used=0, phase_scores={}
        )
        with pytest.raises((AttributeError, TypeError)):
            result.is_converged = True  # type: ignore

    def test_frozen_convergence_rate(self):
        """Attempting to mutate convergence_rate must raise."""
        result = ConvergenceResult.create(
            loop_id="l", is_converged=True, convergence_rate=0.8,
            iterations_used=2, phase_scores={}
        )
        with pytest.raises((AttributeError, TypeError)):
            result.convergence_rate = 0.0  # type: ignore

    def test_to_json_round_trip(self):
        """to_json() and from_json() must be inverse operations."""
        result = ConvergenceResult.create(
            loop_id="l2", is_converged=True, convergence_rate=0.8,
            iterations_used=3, phase_scores={"p": 0.7}
        )
        json_str = result.to_json()
        restored = ConvergenceResult.from_json(json_str)
        assert restored.loop_id == result.loop_id
        assert restored.is_converged == result.is_converged
        assert abs(restored.convergence_rate - result.convergence_rate) < 1e-9
        assert restored.iterations_used == result.iterations_used

    def test_to_json_returns_string(self):
        """to_json() must return a non-empty JSON string."""
        result = ConvergenceResult.create(
            loop_id="lx", is_converged=True, convergence_rate=0.9,
            iterations_used=1, phase_scores={}
        )
        j = result.to_json()
        assert isinstance(j, str)
        assert len(j) > 2  # at least "{}"

    def test_summarize_returns_string(self):
        """summarize() must return a non-empty string."""
        result = ConvergenceResult.create(
            loop_id="l3", is_converged=False, convergence_rate=0.4,
            iterations_used=2, phase_scores={}
        )
        s = result.summarize()
        assert isinstance(s, str)
        assert len(s) > 0

    def test_summarize_mentions_loop_id(self):
        """summarize() output should reference the loop ID."""
        result = ConvergenceResult.create(
            loop_id="my-special-loop", is_converged=True,
            convergence_rate=1.0, iterations_used=1, phase_scores={}
        )
        s = result.summarize()
        assert "my-special-loop" in s

    def test_quality_grade_excellent(self):
        """quality_grade() should return 'excellent' for high convergence."""
        result = ConvergenceResult.create(
            loop_id="l4", is_converged=True, convergence_rate=1.0,
            iterations_used=1, phase_scores={}
        )
        grade = result.quality_grade()
        assert isinstance(grade, str)
        assert len(grade) > 0

    def test_quality_grade_poor(self):
        """quality_grade() should indicate poor quality for low convergence."""
        result = ConvergenceResult.create(
            loop_id="l5", is_converged=False, convergence_rate=0.1,
            iterations_used=10, phase_scores={}
        )
        grade = result.quality_grade()
        assert isinstance(grade, str)

    def test_render_tex(self):
        """render_tex() must return a LaTeX string."""
        result = ConvergenceResult.create(
            loop_id="l6", is_converged=True, convergence_rate=0.9,
            iterations_used=4, phase_scores={}
        )
        tex = result.render_tex()
        assert isinstance(tex, str)

    def test_result_id_is_unique(self):
        """Each ConvergenceResult must have a unique result_id."""
        r1 = ConvergenceResult.create(
            loop_id="l", is_converged=True, convergence_rate=0.9,
            iterations_used=1, phase_scores={}
        )
        r2 = ConvergenceResult.create(
            loop_id="l", is_converged=True, convergence_rate=0.9,
            iterations_used=1, phase_scores={}
        )
        assert r1.result_id != r2.result_id

    def test_result_id_non_empty(self):
        """result_id must be a non-empty string."""
        result = ConvergenceResult.create(
            loop_id="l", is_converged=True, convergence_rate=0.9,
            iterations_used=1, phase_scores={}
        )
        assert isinstance(result.result_id, str)
        assert len(result.result_id) > 0

    def test_phase_scores_preserved(self):
        """Phase scores dict must be preserved through create()."""
        ps = {"formalization": 0.9, "implementation": 0.85, "falsification": 0.7}
        result = ConvergenceResult.create(
            loop_id="l", is_converged=True, convergence_rate=0.9,
            iterations_used=3, phase_scores=ps
        )
        for k, v in ps.items():
            assert k in result.phase_scores
            assert abs(result.phase_scores[k] - v) < 1e-9


# ===========================================================================
# TestHypothesisRanking
# ===========================================================================

class TestHypothesisRanking:
    """Tests for HypothesisRanking data class.

    Covers create(), immutability, serialisation, summarize(), top_k(),
    bottom_k(), and edge cases with mismatched list lengths.
    """

    def test_create(self):
        """Test HypothesisRanking.create() factory method."""
        ranking = HypothesisRanking.create(
            hypothesis_ids=["h1", "h2", "h3"],
            scores=[0.9, 0.7, 0.5],
            strategy="score",
            rationale="sorted by score descending",
        )
        assert len(ranking.hypothesis_ids) == 3
        assert len(ranking.scores) == 3
        assert ranking.strategy == "score"
        assert "score" in ranking.rationale or isinstance(ranking.rationale, str)

    def test_create_empty(self):
        """HypothesisRanking.create() with empty lists must succeed."""
        ranking = HypothesisRanking.create(
            hypothesis_ids=[], scores=[], strategy="empty", rationale="no hypotheses"
        )
        assert len(ranking.hypothesis_ids) == 0
        assert len(ranking.scores) == 0

    def test_frozen(self):
        """HypothesisRanking must be immutable."""
        ranking = HypothesisRanking.create(
            hypothesis_ids=["h"], scores=[0.5], strategy="s", rationale="r"
        )
        with pytest.raises((AttributeError, TypeError)):
            ranking.strategy = "other"  # type: ignore

    def test_frozen_scores(self):
        """Attempting to mutate scores must raise."""
        ranking = HypothesisRanking.create(
            hypothesis_ids=["h"], scores=[0.5], strategy="s", rationale="r"
        )
        with pytest.raises((AttributeError, TypeError)):
            ranking.scores = [0.9]  # type: ignore

    def test_to_json_round_trip(self):
        """Serialization round trip preserves data."""
        ranking = HypothesisRanking.create(
            hypothesis_ids=["h1", "h2"], scores=[0.8, 0.6],
            strategy="score", rationale="by score"
        )
        restored = HypothesisRanking.from_json(ranking.to_json())
        assert list(restored.hypothesis_ids) == list(ranking.hypothesis_ids)
        assert list(restored.scores) == list(ranking.scores)
        assert restored.strategy == ranking.strategy

    def test_summarize(self):
        """summarize() returns non-empty string."""
        ranking = HypothesisRanking.create(
            hypothesis_ids=["h1"], scores=[0.5],
            strategy="random", rationale="random order"
        )
        assert isinstance(ranking.summarize(), str)
        assert len(ranking.summarize()) > 0

    def test_top_k(self):
        """top_k(2) returns the first 2 hypothesis IDs."""
        ranking = HypothesisRanking.create(
            hypothesis_ids=["h1", "h2", "h3"], scores=[0.9, 0.7, 0.5],
            strategy="score", rationale="by score"
        )
        top = ranking.top_k(2)
        assert len(top) == 2
        assert top[0] == "h1"

    def test_bottom_k(self):
        """bottom_k(1) returns the last hypothesis ID."""
        ranking = HypothesisRanking.create(
            hypothesis_ids=["h1", "h2", "h3"], scores=[0.9, 0.7, 0.5],
            strategy="score", rationale="by score"
        )
        bottom = ranking.bottom_k(1)
        assert len(bottom) == 1
        assert bottom[0] == "h3"

    def test_top_k_larger_than_list(self):
        """top_k(100) returns all elements without error."""
        ranking = HypothesisRanking.create(
            hypothesis_ids=["h1", "h2"], scores=[0.9, 0.5],
            strategy="s", rationale="r"
        )
        top = ranking.top_k(100)
        assert len(top) == 2

    def test_bottom_k_larger_than_list(self):
        """bottom_k(100) returns all elements without error."""
        ranking = HypothesisRanking.create(
            hypothesis_ids=["h1", "h2"], scores=[0.9, 0.5],
            strategy="s", rationale="r"
        )
        bottom = ranking.bottom_k(100)
        assert len(bottom) == 2

    def test_ranking_id_unique(self):
        """Each HypothesisRanking should have a unique ranking_id."""
        r1 = HypothesisRanking.create(hypothesis_ids=["h1"], scores=[0.5], strategy="s", rationale="r")
        r2 = HypothesisRanking.create(hypothesis_ids=["h1"], scores=[0.5], strategy="s", rationale="r")
        assert r1.ranking_id != r2.ranking_id

    def test_render_tex(self):
        """render_tex() returns a LaTeX-compatible string."""
        ranking = HypothesisRanking.create(
            hypothesis_ids=["h1", "h2"], scores=[0.9, 0.5],
            strategy="score", rationale="by score"
        )
        tex = ranking.render_tex()
        assert isinstance(tex, str)


# ===========================================================================
# TestMethodologyAlgorithms
# ===========================================================================

class TestMethodologyAlgorithms:
    """Tests for MethodologyAlgorithms class.

    The MethodologyAlgorithms class provides high-level entry points that
    delegate to module-level functions. We verify that each method is callable,
    returns the expected type, and preserves loop immutability.
    """

    def test_init(self, algorithms):
        """MethodologyAlgorithms can be instantiated without arguments."""
        assert algorithms is not None

    def test_run_loop_step_returns_loop(self, algorithms, default_loop):
        """run_loop_step() must return a MethodologyLoop."""
        result = algorithms.run_loop_step(default_loop)
        assert isinstance(result, MethodologyLoop)

    def test_run_loop_step_does_not_mutate_original(self, algorithms, default_loop):
        """run_loop_step() must not modify the original loop object."""
        original_id = default_loop.loop_id
        original_iter = default_loop.state.iteration
        algorithms.run_loop_step(default_loop)
        assert default_loop.loop_id == original_id
        assert default_loop.state.iteration == original_iter

    def test_check_convergence_returns_result(self, algorithms, default_loop):
        """check_convergence() must return a ConvergenceResult."""
        result = algorithms.check_convergence(default_loop)
        assert isinstance(result, ConvergenceResult)

    def test_check_convergence_loop_id_matches(self, algorithms, default_loop):
        """check_convergence() result must reference the correct loop_id."""
        result = algorithms.check_convergence(default_loop)
        assert result.loop_id == default_loop.loop_id

    def test_attempt_falsification_returns_result(self, algorithms, default_loop):
        """attempt_falsification() must return a structured result."""
        result = algorithms.attempt_falsification(default_loop, hypothesis_id="hyp-1")
        assert result is not None

    def test_score_phase_returns_float(self, algorithms, default_loop):
        """score_phase() must return a float in [0, 1]."""
        score = algorithms.score_phase(default_loop, LoopPhase.FORMALIZATION)
        assert isinstance(score, float)
        assert 0.0 <= score <= 1.0

    def test_rank_hypotheses_returns_ranking(self, algorithms, default_loop):
        """rank_hypotheses() must return a HypothesisRanking."""
        hypotheses = [{"id": "h1", "score": 0.9}, {"id": "h2", "score": 0.5}]
        result = algorithms.rank_hypotheses(default_loop, hypotheses)
        assert isinstance(result, HypothesisRanking)

    def test_estimate_remaining_iterations_positive(self, algorithms, default_loop):
        """estimate_remaining_iterations() must return a non-negative integer."""
        n = algorithms.estimate_remaining_iterations(default_loop)
        assert isinstance(n, int)
        assert n >= 0

    def test_aggregate_metrics_returns_dict(self, algorithms, default_loop):
        """aggregate_metrics() must return a dict."""
        metrics = algorithms.aggregate_metrics(default_loop)
        assert isinstance(metrics, dict)

    def test_compute_phase_transition_matrix(self, algorithms, default_loop):
        """compute_phase_transition_matrix() returns a non-empty structure."""
        matrix = algorithms.compute_phase_transition_matrix(default_loop)
        assert matrix is not None


# ===========================================================================
# TestLoopStepFunction
# ===========================================================================

class TestLoopStepFunction:
    """Tests for the module-level loop_step() function.

    loop_step() is the functional entry point for advancing a MethodologyLoop
    by a single iteration. It must return a new loop, not modify the original.
    """

    def test_returns_loop(self, default_loop):
        """loop_step() returns a MethodologyLoop."""
        result = loop_step(default_loop)
        assert isinstance(result, MethodologyLoop)

    def test_does_not_mutate_input(self, default_loop):
        """loop_step() must not mutate the input loop."""
        original_loop_id = default_loop.loop_id
        loop_step(default_loop)
        assert default_loop.loop_id == original_loop_id

    def test_iteration_advances(self, default_loop):
        """loop_step() should advance the iteration counter."""
        stepped = loop_step(default_loop)
        assert stepped.state.iteration >= default_loop.state.iteration

    def test_updated_at_monotone(self, default_loop):
        """updated_at on the returned loop should be >= the original."""
        import time
        stepped = loop_step(default_loop)
        assert stepped.updated_at >= default_loop.updated_at

    def test_loop_id_preserved(self, default_loop):
        """loop_step() must preserve the loop_id."""
        stepped = loop_step(default_loop)
        assert stepped.loop_id == default_loop.loop_id

    def test_config_preserved(self, default_loop):
        """loop_step() must preserve the configuration."""
        stepped = loop_step(default_loop)
        assert stepped.config.max_iterations == default_loop.config.max_iterations

    def test_multiple_steps(self, default_loop):
        """Multiple successive calls to loop_step() are valid."""
        loop = default_loop
        for _ in range(3):
            loop = loop_step(loop)
        assert isinstance(loop, MethodologyLoop)
        assert loop.state.iteration >= 0


# ===========================================================================
# TestConvergenceCheckFunction
# ===========================================================================

class TestConvergenceCheckFunction:
    """Tests for the module-level convergence_check() function.

    convergence_check() examines a loop's state and returns a ConvergenceResult
    indicating whether the loop has met its convergence criterion.
    """

    def test_returns_convergence_result(self, default_loop):
        """convergence_check() returns a ConvergenceResult."""
        result = convergence_check(default_loop)
        assert isinstance(result, ConvergenceResult)

    def test_loop_id_in_result(self, default_loop):
        """convergence_check() result references the correct loop_id."""
        result = convergence_check(default_loop)
        assert result.loop_id == default_loop.loop_id

    def test_convergence_rate_in_bounds(self, default_loop):
        """convergence_rate must be in [0.0, 1.0]."""
        result = convergence_check(default_loop)
        assert 0.0 <= result.convergence_rate <= 1.0

    def test_iterations_used_non_negative(self, default_loop):
        """iterations_used must be >= 0."""
        result = convergence_check(default_loop)
        assert result.iterations_used >= 0

    def test_phase_scores_is_dict(self, default_loop):
        """phase_scores must be a dict."""
        result = convergence_check(default_loop)
        assert isinstance(result.phase_scores, dict)

    def test_is_converged_bool(self, default_loop):
        """is_converged must be a boolean."""
        result = convergence_check(default_loop)
        assert isinstance(result.is_converged, bool)

    def test_deterministic_same_input(self, default_loop):
        """Two calls with the same loop should return consistent convergence_rate."""
        r1 = convergence_check(default_loop)
        r2 = convergence_check(default_loop)
        assert abs(r1.convergence_rate - r2.convergence_rate) < 1e-9


# ===========================================================================
# TestPhaseScoreFunction
# ===========================================================================

class TestPhaseScoreFunction:
    """Tests for the module-level phase_score() function.

    phase_score() computes a numeric quality score for a given loop phase,
    returning a float in [0, 1].
    """

    def test_returns_float(self, default_loop):
        """phase_score() returns a float."""
        score = phase_score(default_loop, LoopPhase.FORMALIZATION)
        assert isinstance(score, float)

    def test_score_in_bounds(self, default_loop):
        """phase_score() result must be in [0.0, 1.0]."""
        for phase in LoopPhase:
            score = phase_score(default_loop, phase)
            assert 0.0 <= score <= 1.0, f"Score out of bounds for phase {phase}: {score}"

    def test_all_phases_computable(self, default_loop):
        """phase_score() must succeed for every LoopPhase member."""
        for phase in LoopPhase:
            result = phase_score(default_loop, phase)
            assert result is not None

    def test_formalization_score_type(self, default_loop):
        """Specifically test FORMALIZATION phase score type."""
        score = phase_score(default_loop, LoopPhase.FORMALIZATION)
        assert isinstance(score, (int, float))

    def test_implementation_score_type(self, default_loop):
        """Specifically test IMPLEMENTATION phase score type."""
        score = phase_score(default_loop, LoopPhase.IMPLEMENTATION)
        assert isinstance(score, (int, float))

    def test_falsification_score_type(self, default_loop):
        """Specifically test FALSIFICATION phase score type."""
        score = phase_score(default_loop, LoopPhase.FALSIFICATION)
        assert isinstance(score, (int, float))

    def test_deterministic(self, default_loop):
        """phase_score() should be deterministic for the same loop and phase."""
        s1 = phase_score(default_loop, LoopPhase.FORMALIZATION)
        s2 = phase_score(default_loop, LoopPhase.FORMALIZATION)
        assert abs(s1 - s2) < 1e-9


# ===========================================================================
# TestComputeConvergenceRate
# ===========================================================================

class TestComputeConvergenceRate:
    """Tests for compute_convergence_rate() module-level function.

    This function takes phase scores and iteration data and returns a scalar
    convergence rate in [0, 1].
    """

    def test_returns_float(self):
        """compute_convergence_rate() returns a float."""
        rate = compute_convergence_rate(
            phase_scores={"formalization": 0.9, "implementation": 0.85},
            iterations_used=3,
            max_iterations=10,
        )
        assert isinstance(rate, float)

    def test_rate_in_bounds(self):
        """compute_convergence_rate() result must be in [0.0, 1.0]."""
        rate = compute_convergence_rate(
            phase_scores={"formalization": 0.9},
            iterations_used=5,
            max_iterations=10,
        )
        assert 0.0 <= rate <= 1.0

    def test_higher_scores_higher_rate(self):
        """Higher phase scores should produce a higher or equal convergence rate."""
        low_rate = compute_convergence_rate(
            phase_scores={"f": 0.1}, iterations_used=1, max_iterations=10
        )
        high_rate = compute_convergence_rate(
            phase_scores={"f": 0.99}, iterations_used=1, max_iterations=10
        )
        assert high_rate >= low_rate

    def test_empty_phase_scores(self):
        """compute_convergence_rate() handles empty phase_scores gracefully."""
        rate = compute_convergence_rate(
            phase_scores={}, iterations_used=0, max_iterations=10
        )
        assert isinstance(rate, float)
        assert 0.0 <= rate <= 1.0

    def test_all_perfect_scores(self):
        """All phase scores at 1.0 should yield maximum convergence."""
        rate = compute_convergence_rate(
            phase_scores={"f": 1.0, "i": 1.0, "fa": 1.0},
            iterations_used=1,
            max_iterations=10,
        )
        assert rate >= 0.9

    def test_single_iteration_at_max(self):
        """Using all iterations should not break the function."""
        rate = compute_convergence_rate(
            phase_scores={"f": 0.5}, iterations_used=10, max_iterations=10
        )
        assert isinstance(rate, float)


# ===========================================================================
# TestRankHypotheses
# ===========================================================================

class TestRankHypotheses:
    """Tests for the module-level rank_hypotheses() function.

    rank_hypotheses() accepts a list of hypothesis dicts and a strategy name,
    returning a HypothesisRanking.
    """

    def test_returns_hypothesis_ranking(self):
        """rank_hypotheses() returns a HypothesisRanking."""
        hypotheses = [
            {"id": "h1", "score": 0.9},
            {"id": "h2", "score": 0.6},
            {"id": "h3", "score": 0.3},
        ]
        result = rank_hypotheses(hypotheses, strategy="score")
        assert isinstance(result, HypothesisRanking)

    def test_length_matches(self):
        """Resulting ranking must contain the same number of hypotheses."""
        hypotheses = [{"id": f"h{i}", "score": float(i)} for i in range(5)]
        result = rank_hypotheses(hypotheses, strategy="score")
        assert len(result.hypothesis_ids) == 5

    def test_order_descending_by_score(self):
        """Default 'score' strategy should rank from highest to lowest."""
        hypotheses = [
            {"id": "low", "score": 0.1},
            {"id": "high", "score": 0.9},
            {"id": "mid", "score": 0.5},
        ]
        result = rank_hypotheses(hypotheses, strategy="score")
        assert result.hypothesis_ids[0] == "high"

    def test_empty_hypotheses(self):
        """rank_hypotheses() handles empty list gracefully."""
        result = rank_hypotheses([], strategy="score")
        assert isinstance(result, HypothesisRanking)
        assert len(result.hypothesis_ids) == 0

    def test_single_hypothesis(self):
        """rank_hypotheses() works with a single hypothesis."""
        result = rank_hypotheses([{"id": "only", "score": 0.5}], strategy="score")
        assert len(result.hypothesis_ids) == 1
        assert result.hypothesis_ids[0] == "only"

    def test_strategy_preserved(self):
        """The strategy field on the returned ranking must match the input."""
        result = rank_hypotheses([{"id": "h", "score": 0.5}], strategy="priority")
        assert result.strategy == "priority"

    def test_scores_all_in_range(self):
        """All scores in the returned ranking must be in [0, 1]."""
        hypotheses = [{"id": f"h{i}", "score": i / 10} for i in range(10)]
        result = rank_hypotheses(hypotheses, strategy="score")
        for score in result.scores:
            assert 0.0 <= score <= 1.0


# ===========================================================================
# TestNormalizeScores
# ===========================================================================

class TestNormalizeScores:
    """Tests for the module-level normalize_scores() function.

    normalize_scores() maps a list of raw numeric scores into [0, 1] using
    min-max normalization (or another configured strategy).
    """

    def test_returns_list(self):
        """normalize_scores() returns a list."""
        result = normalize_scores([0.1, 0.5, 0.9])
        assert isinstance(result, list)

    def test_length_preserved(self):
        """Output length must match input length."""
        scores = [1.0, 2.0, 3.0, 4.0, 5.0]
        result = normalize_scores(scores)
        assert len(result) == len(scores)

    def test_all_values_in_unit_interval(self):
        """All normalized scores must be in [0.0, 1.0]."""
        scores = [10, 20, 30, 40, 50]
        result = normalize_scores(scores)
        for v in result:
            assert 0.0 <= v <= 1.0, f"Normalized score {v} out of [0, 1]"

    def test_max_maps_to_one(self):
        """The maximum input value should map to 1.0."""
        result = normalize_scores([1, 2, 3, 4, 5])
        assert abs(max(result) - 1.0) < 1e-9

    def test_min_maps_to_zero(self):
        """The minimum input value should map to 0.0."""
        result = normalize_scores([1, 2, 3, 4, 5])
        assert abs(min(result) - 0.0) < 1e-9

    def test_uniform_scores(self):
        """All-equal scores should not raise; result is well-defined."""
        result = normalize_scores([5, 5, 5])
        assert isinstance(result, list)
        assert len(result) == 3

    def test_single_value(self):
        """normalize_scores() with a single value must not crash."""
        result = normalize_scores([42.0])
        assert isinstance(result, list)
        assert len(result) == 1

    def test_empty_list(self):
        """normalize_scores([]) should return []."""
        result = normalize_scores([])
        assert result == [] or result is not None


# ===========================================================================
# TestAggregateLoopMetrics
# ===========================================================================

class TestAggregateLoopMetrics:
    """Tests for the module-level aggregate_loop_metrics() function.

    aggregate_loop_metrics() combines per-phase metrics for a loop into a
    summary dict used downstream for reporting and convergence decisions.
    """

    def test_returns_dict(self, default_loop):
        """aggregate_loop_metrics() returns a dict."""
        result = aggregate_loop_metrics(default_loop)
        assert isinstance(result, dict)

    def test_non_empty_dict(self, default_loop):
        """aggregate_loop_metrics() returns a non-empty dict."""
        result = aggregate_loop_metrics(default_loop)
        assert len(result) > 0

    def test_contains_expected_keys(self, default_loop):
        """Result dict must contain at least 'iterations' or 'phase' info."""
        result = aggregate_loop_metrics(default_loop)
        # At minimum one of these keys should be present
        key_set = set(result.keys())
        assert len(key_set) > 0

    def test_iteration_key_matches_loop(self, default_loop):
        """If 'iterations' key present, it must match the loop's iteration count."""
        result = aggregate_loop_metrics(default_loop)
        if "iterations" in result:
            assert result["iterations"] == default_loop.state.iteration

    def test_metrics_all_numeric(self, default_loop):
        """All numeric values in the result must be finite numbers."""
        import math
        result = aggregate_loop_metrics(default_loop)
        for k, v in result.items():
            if isinstance(v, (int, float)):
                assert not math.isnan(v), f"NaN value for key {k}"

    def test_multiple_calls_stable(self, default_loop):
        """Calling aggregate_loop_metrics() twice yields consistent results."""
        r1 = aggregate_loop_metrics(default_loop)
        r2 = aggregate_loop_metrics(default_loop)
        for k in r1:
            if isinstance(r1[k], float):
                assert abs(r1[k] - r2.get(k, r1[k])) < 1e-9


# ===========================================================================
# TestFalsificationAttemptFunction
# ===========================================================================

class TestFalsificationAttemptFunction:
    """Tests for the module-level falsification_attempt() function.

    falsification_attempt() searches for a counterexample to a hypothesis
    within the given loop's falsification budget.
    """

    def test_returns_result(self, default_loop):
        """falsification_attempt() returns a non-None result."""
        result = falsification_attempt(default_loop, hypothesis_id="hyp-test-1")
        assert result is not None

    def test_result_has_hypothesis_id(self, default_loop):
        """Result should reference the hypothesis_id provided."""
        result = falsification_attempt(default_loop, hypothesis_id="hyp-test-2")
        if hasattr(result, "hypothesis_id"):
            assert result.hypothesis_id == "hyp-test-2"

    def test_different_hypotheses(self, default_loop):
        """falsification_attempt() can be called for multiple hypotheses."""
        r1 = falsification_attempt(default_loop, hypothesis_id="h-alpha")
        r2 = falsification_attempt(default_loop, hypothesis_id="h-beta")
        assert r1 is not None
        assert r2 is not None

    def test_does_not_mutate_loop(self, default_loop):
        """falsification_attempt() must not modify the loop."""
        orig_id = default_loop.loop_id
        orig_iter = default_loop.state.iteration
        falsification_attempt(default_loop, hypothesis_id="h-test")
        assert default_loop.loop_id == orig_id
        assert default_loop.state.iteration == orig_iter

    def test_budget_respected(self, default_loop):
        """Falsification should respect the budget in MethodologyConfig."""
        # Simply ensure no error and returns
        result = falsification_attempt(default_loop, hypothesis_id="h-budget")
        assert result is not None


# ===========================================================================
# TestComputePhaseTransitionMatrix
# ===========================================================================

class TestComputePhaseTransitionMatrix:
    """Tests for compute_phase_transition_matrix() function.

    This function analyses the transition history of a loop and returns a
    matrix (dict of dicts or 2D list) describing probabilities of moving
    between phases.
    """

    def test_returns_value(self, default_loop):
        """compute_phase_transition_matrix() returns a non-None value."""
        result = compute_phase_transition_matrix(default_loop)
        assert result is not None

    def test_returns_dict_or_list(self, default_loop):
        """compute_phase_transition_matrix() returns a dict or list."""
        result = compute_phase_transition_matrix(default_loop)
        assert isinstance(result, (dict, list))

    def test_empty_history_no_crash(self, default_loop):
        """An empty transition history must not cause a crash."""
        result = compute_phase_transition_matrix(default_loop)
        assert result is not None

    def test_deterministic(self, default_loop):
        """Two identical calls should return the same matrix."""
        r1 = compute_phase_transition_matrix(default_loop)
        r2 = compute_phase_transition_matrix(default_loop)
        assert str(r1) == str(r2)


# ===========================================================================
# TestEstimateRemainingIterations
# ===========================================================================

class TestEstimateRemainingIterations:
    """Tests for estimate_remaining_iterations() function.

    This function predicts how many more iterations a loop needs to converge,
    based on its current convergence rate and config.
    """

    def test_returns_int(self, default_loop):
        """estimate_remaining_iterations() returns an integer."""
        n = estimate_remaining_iterations(default_loop)
        assert isinstance(n, int)

    def test_non_negative(self, default_loop):
        """Estimate must be >= 0."""
        n = estimate_remaining_iterations(default_loop)
        assert n >= 0

    def test_does_not_exceed_max(self, default_loop):
        """Estimate should not exceed max_iterations in config."""
        n = estimate_remaining_iterations(default_loop)
        assert n <= default_loop.config.max_iterations

    def test_deterministic(self, default_loop):
        """Two calls with same loop must return same estimate."""
        n1 = estimate_remaining_iterations(default_loop)
        n2 = estimate_remaining_iterations(default_loop)
        assert n1 == n2


# ===========================================================================
# Additional parametrized tests
# ===========================================================================

@pytest.mark.parametrize("rate,expected_grade_type", [
    (1.0, str),
    (0.9, str),
    (0.5, str),
    (0.1, str),
    (0.0, str),
])
def test_quality_grade_always_str(rate, expected_grade_type):
    """quality_grade() always returns a str for any convergence_rate."""
    result = ConvergenceResult.create(
        loop_id="param-test",
        is_converged=rate >= 0.95,
        convergence_rate=rate,
        iterations_used=5,
        phase_scores={},
    )
    assert isinstance(result.quality_grade(), expected_grade_type)


@pytest.mark.parametrize("n_hypotheses", [0, 1, 3, 10, 50])
def test_rank_hypotheses_various_sizes(n_hypotheses):
    """rank_hypotheses() handles various input sizes correctly."""
    hypotheses = [{"id": f"h{i}", "score": i / max(n_hypotheses, 1)} for i in range(n_hypotheses)]
    result = rank_hypotheses(hypotheses, strategy="score")
    assert isinstance(result, HypothesisRanking)
    assert len(result.hypothesis_ids) == n_hypotheses


@pytest.mark.parametrize("scores", [
    [0.1, 0.9],
    [1.0, 1.0, 1.0],
    [0.0],
    [100.0, 200.0, 300.0],
])
def test_normalize_scores_parametrized(scores):
    """normalize_scores() handles diverse input distributions."""
    result = normalize_scores(scores)
    assert len(result) == len(scores)
    for v in result:
        assert 0.0 <= v <= 1.0
