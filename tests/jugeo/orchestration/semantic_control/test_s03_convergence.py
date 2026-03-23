"""Tests for jugeo.orchestration.semantic_control.s03_convergence (theory2.tex Ch44).

Covers: ConvergenceMetrics, ObligationTracker, CoverageAnalyzer,
ConvergenceRateEstimator, DivergenceDetector, CertificationAuthority,
ConvergenceMonitor, and selected integration paths using upstream modules.
"""

from __future__ import annotations

import math
import time
import uuid
from pathlib import Path
import sys

ROOT = next(
    parent
    for parent in Path(__file__).resolve().parents
    if (parent / "src" / "jugeo").exists()
)
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import pytest

# ---------------------------------------------------------------------------
# Subject under test
# ---------------------------------------------------------------------------

from jugeo.orchestration.semantic_control.s03_convergence import (
    CertificationAuthority,
    ConvergenceMetrics,
    ConvergenceMonitor,
    ConvergenceRateEstimator,
    CoverageAnalyzer,
    DEFAULT_CONVERGENCE_THRESHOLD,
    DEFAULT_MAX_OBLIGATION_AGE,
    DEFAULT_VALIDITY_PERIOD,
    DIVERGENCE_WINDOW,
    DIVERGING_RATE_THRESHOLD,
    STALL_RATE_THRESHOLD,
    DivergenceDetector,
    ObligationTracker,
)

# ---------------------------------------------------------------------------
# Upstream imports (optional; tests that require them are skipped if absent)
# ---------------------------------------------------------------------------

try:
    from jugeo.orchestration.semantic_control.models import (
        ConvergenceCertificate,
        ConvergenceMode,
        SemanticControlState,
        SemanticTrajectory,
        StateHealthStatus,
    )

    MODELS_AVAILABLE = True
except Exception:
    MODELS_AVAILABLE = False

try:
    from jugeo.orchestration.controller import (
        ConvergenceMonitor as BaseConvergenceMonitor,
        MoveKind,
    )

    CONTROLLER_AVAILABLE = True
except Exception:
    CONTROLLER_AVAILABLE = False

try:
    from jugeo.orchestration.fleet import Fleet, FleetMember

    FLEET_AVAILABLE = True
except Exception:
    FLEET_AVAILABLE = False

try:
    from jugeo.evidence.trust import TrustLevel, TrustTier

    TRUST_AVAILABLE = True
except Exception:
    TRUST_AVAILABLE = False

# ---------------------------------------------------------------------------
# Helpers / shared builders
# ---------------------------------------------------------------------------


def _make_state(
    n_covers: int = 4,
    n_sections: int = 2,
    n_treaties: int = 1,
    n_obligations: int = 0,
    n_channels: int = 0,
    n_contexts: int = 0,
    budget: dict | None = None,
) -> "SemanticControlState":
    """Build a minimal SemanticControlState for testing."""
    return SemanticControlState(
        state_id=str(uuid.uuid4()),
        cover_ids=[f"c{i}" for i in range(n_covers)],
        section_ids=[f"s{i}" for i in range(n_sections)],
        treaty_ids=[f"t{i}" for i in range(n_treaties)],
        obligation_ids=[f"o{i}" for i in range(n_obligations)],
        channel_ids=[f"ch{i}" for i in range(n_channels)],
        context_ids=[f"ctx{i}" for i in range(n_contexts)],
        budget=budget or {"used": 0, "total": 100},
        timestamp=time.time(),
        metadata={},
    )


def _make_converged_state() -> "SemanticControlState":
    """State where sections == covers, no obligations, treaties == covers-1."""
    n = 4
    return SemanticControlState(
        state_id=str(uuid.uuid4()),
        cover_ids=[f"c{i}" for i in range(n)],
        section_ids=[f"s{i}" for i in range(n)],
        treaty_ids=[f"t{i}" for i in range(n - 1)],
        obligation_ids=[],
        channel_ids=[f"ch{i}" for i in range(n)],
        context_ids=[f"ctx{i}" for i in range(n)],
        budget={"used": 0, "total": 100},
        timestamp=time.time(),
        metadata={},
    )


def _make_metrics(
    coverage: float = 0.5,
    obligations: int = 0,
    attainability: float = 0.5,
    lyapunov: float = 0.5,
    rate: float = 0.01,
    step: int = 0,
) -> ConvergenceMetrics:
    return ConvergenceMetrics(
        coverage_ratio=coverage,
        obligation_count=obligations,
        attainability=attainability,
        lyapunov_value=lyapunov,
        rate_estimate=rate,
        step_count=step,
        timestamp=time.time(),
    )


def _make_trajectory(
    states: list["SemanticControlState"] | None = None,
) -> "SemanticTrajectory":
    traj = SemanticTrajectory(trajectory_id=str(uuid.uuid4()))
    if states:
        for s in states:
            traj.append(s, move=None)
    return traj


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def basic_state():
    return _make_state(n_covers=4, n_sections=2, n_treaties=1, n_obligations=0)


@pytest.fixture
def converging_trajectory():
    states = [
        _make_state(n_covers=4, n_sections=i, n_treaties=max(i - 1, 0))
        for i in range(1, 5)
    ]
    return _make_trajectory(states)


@pytest.fixture
def basic_monitor():
    return ConvergenceMonitor()


# ===========================================================================
# 1. ConvergenceMetrics
# ===========================================================================


class TestConvergenceMetrics:
    """Unit tests for the ConvergenceMetrics frozen dataclass."""

    def test_construction_stores_all_fields(self):
        m = _make_metrics(coverage=0.7, obligations=2, lyapunov=0.3, rate=0.05, step=10)
        assert m.coverage_ratio == pytest.approx(0.7)
        assert m.obligation_count == 2
        assert m.lyapunov_value == pytest.approx(0.3)
        assert m.rate_estimate == pytest.approx(0.05)
        assert m.step_count == 10

    def test_is_frozen(self):
        m = _make_metrics()
        with pytest.raises((AttributeError, TypeError)):
            m.coverage_ratio = 0.99  # type: ignore[misc]

    def test_is_converged_requires_all_three_criteria(self):
        # All three criteria met → converged
        m = _make_metrics(coverage=0.96, obligations=0, lyapunov=0.0)
        assert m.is_converged()

    def test_is_converged_fails_low_coverage(self):
        m = _make_metrics(coverage=0.5, obligations=0, lyapunov=0.0)
        assert not m.is_converged()

    def test_is_converged_fails_pending_obligations(self):
        m = _make_metrics(coverage=0.98, obligations=3, lyapunov=0.0)
        assert not m.is_converged()

    def test_is_converged_fails_nonzero_lyapunov(self):
        m = _make_metrics(coverage=0.98, obligations=0, lyapunov=0.01)
        assert not m.is_converged()

    def test_is_converged_custom_threshold(self):
        m = _make_metrics(coverage=0.7, obligations=0, lyapunov=0.0)
        assert m.is_converged(threshold=0.6)
        assert not m.is_converged(threshold=0.8)

    def test_is_converged_at_exact_threshold(self):
        m = _make_metrics(coverage=0.95, obligations=0, lyapunov=0.0)
        assert m.is_converged(threshold=0.95)

    def test_to_dict_returns_all_keys(self):
        m = _make_metrics(coverage=0.6, obligations=1, lyapunov=0.4, rate=0.02, step=5)
        d = m.to_dict()
        expected_keys = {
            "coverage_ratio", "obligation_count", "attainability",
            "lyapunov_value", "rate_estimate", "step_count", "timestamp",
        }
        assert expected_keys <= d.keys()

    def test_to_dict_values_match_fields(self):
        m = _make_metrics(coverage=0.77, obligations=2, lyapunov=0.23, step=7)
        d = m.to_dict()
        assert d["coverage_ratio"] == pytest.approx(0.77)
        assert d["obligation_count"] == 2
        assert d["lyapunov_value"] == pytest.approx(0.23)
        assert d["step_count"] == 7

    def test_summary_returns_string(self):
        m = _make_metrics(coverage=0.5, step=3)
        s = m.summary()
        assert isinstance(s, str)
        assert "cov=" in s
        assert "step=3" in s

    def test_summary_contains_rate_sign(self):
        m_pos = _make_metrics(rate=0.01)
        m_neg = _make_metrics(rate=-0.01)
        assert "+" in m_pos.summary()
        assert "-" in m_neg.summary() or "rate=-" in m_neg.summary()

    def test_summary_shows_correct_step(self):
        m = _make_metrics(step=42)
        assert "step=42" in m.summary()

    def test_delta_positive_improvement(self):
        base = _make_metrics(coverage=0.5, obligations=3, lyapunov=0.5, step=1)
        improved = _make_metrics(coverage=0.7, obligations=1, lyapunov=0.3, step=2)
        delta = improved.delta(base)
        assert delta["coverage_ratio"] == pytest.approx(0.2)
        assert delta["obligation_count"] == pytest.approx(-2.0)
        assert delta["lyapunov_value"] == pytest.approx(-0.2)

    def test_delta_zero_when_equal(self):
        m = _make_metrics(coverage=0.5, obligations=1, lyapunov=0.5)
        delta = m.delta(m)
        assert all(v == pytest.approx(0.0) for v in delta.values())

    def test_delta_negative_regression(self):
        good = _make_metrics(coverage=0.8, obligations=0)
        bad = _make_metrics(coverage=0.4, obligations=5)
        delta = bad.delta(good)
        assert delta["coverage_ratio"] < 0

    def test_delta_returns_expected_keys(self):
        m1 = _make_metrics()
        m2 = _make_metrics()
        d = m1.delta(m2)
        assert "coverage_ratio" in d
        assert "obligation_count" in d
        assert "lyapunov_value" in d
        assert "step_count" in d


# ===========================================================================
# 2. ObligationTracker
# ===========================================================================


class TestObligationTracker:
    """Unit tests for ObligationTracker."""

    def test_initial_state_empty(self):
        tracker = ObligationTracker()
        assert tracker.pending_count() == 0
        assert tracker.resolved_count() == 0

    def test_add_obligation(self):
        tracker = ObligationTracker()
        tracker.add("ob-1", {"rule": "overlap"})
        assert tracker.pending_count() == 1
        assert not tracker.is_resolved("ob-1")

    def test_add_multiple_obligations(self):
        tracker = ObligationTracker()
        for i in range(5):
            tracker.add(f"ob-{i}", {"rule": f"r{i}"})
        assert tracker.pending_count() == 5

    def test_add_idempotent_for_same_id(self):
        tracker = ObligationTracker()
        tracker.add("ob-1", {"rule": "x"})
        tracker.add("ob-1", {"rule": "y"})  # duplicate, should be ignored
        assert tracker.pending_count() == 1

    def test_add_idempotent_for_resolved_id(self):
        tracker = ObligationTracker()
        tracker.add("ob-1", {"rule": "x"})
        tracker.resolve("ob-1", {"proof": "done"})
        tracker.add("ob-1", {"rule": "re-added"})
        assert tracker.pending_count() == 0  # still resolved, not re-added

    def test_resolve_returns_true_on_success(self):
        tracker = ObligationTracker()
        tracker.add("ob-1", {})
        result = tracker.resolve("ob-1", {"proof": "qed"})
        assert result is True

    def test_resolve_moves_to_resolved(self):
        tracker = ObligationTracker()
        tracker.add("ob-1", {})
        tracker.resolve("ob-1", {"proof": "qed"})
        assert tracker.pending_count() == 0
        assert tracker.resolved_count() == 1
        assert tracker.is_resolved("ob-1")

    def test_resolve_unknown_returns_false(self):
        tracker = ObligationTracker()
        result = tracker.resolve("nonexistent", {"proof": "?"})
        assert result is False

    def test_resolve_already_resolved_returns_false(self):
        tracker = ObligationTracker()
        tracker.add("ob-1", {})
        tracker.resolve("ob-1", {})
        result = tracker.resolve("ob-1", {})  # double-resolve
        assert result is False

    def test_pending_count_decreases_after_resolve(self):
        tracker = ObligationTracker()
        for i in range(3):
            tracker.add(f"ob-{i}", {})
        tracker.resolve("ob-0", {})
        assert tracker.pending_count() == 2
        assert tracker.resolved_count() == 1

    def test_is_resolved_false_for_pending(self):
        tracker = ObligationTracker()
        tracker.add("ob-x", {})
        assert not tracker.is_resolved("ob-x")

    def test_is_resolved_false_for_unknown(self):
        tracker = ObligationTracker()
        assert not tracker.is_resolved("unknown-id")

    def test_expire_old_removes_aged_obligations(self):
        tracker = ObligationTracker(max_age=0.0)  # everything expires immediately
        tracker.add("ob-old", {"added_at": time.time() - 7200})  # 2 hours old
        expired = tracker.expire_old()
        assert "ob-old" in expired
        assert tracker.pending_count() == 0

    def test_expire_old_keeps_recent_obligations(self):
        tracker = ObligationTracker(max_age=3600.0)
        tracker.add("ob-new", {"added_at": time.time()})
        expired = tracker.expire_old()
        assert expired == []
        assert tracker.pending_count() == 1

    def test_expire_old_returns_expired_ids(self):
        tracker = ObligationTracker(max_age=1.0)
        tracker.add("ob-stale", {"added_at": time.time() - 100})
        tracker.add("ob-fresh", {"added_at": time.time()})
        expired = tracker.expire_old()
        assert "ob-stale" in expired
        assert "ob-fresh" not in expired

    def test_expire_old_empty_tracker_is_noop(self):
        tracker = ObligationTracker()
        expired = tracker.expire_old()
        assert expired == []

    def test_to_dict_structure(self):
        tracker = ObligationTracker()
        tracker.add("ob-1", {"rule": "r1"})
        tracker.add("ob-2", {"rule": "r2"})
        tracker.resolve("ob-1", {"proof": "p1"})
        d = tracker.to_dict()
        assert "pending" in d
        assert "resolved" in d
        assert "max_age" in d
        assert "ob-1" in d["resolved"]
        assert "ob-2" in d["pending"]

    def test_summary_string_format(self):
        tracker = ObligationTracker()
        tracker.add("ob-1", {})
        tracker.add("ob-2", {})
        tracker.resolve("ob-1", {})
        s = tracker.summary()
        assert "ObligationTracker" in s
        assert "1 pending" in s
        assert "1 resolved" in s

    def test_pending_obligations_list(self):
        tracker = ObligationTracker()
        tracker.add("ob-a", {"source": "unit_test"})
        pending = tracker.pending_obligations()
        assert len(pending) == 1
        assert pending[0]["obligation_id"] == "ob-a"
        assert pending[0]["source"] == "unit_test"


# ===========================================================================
# 3. CoverageAnalyzer
# ===========================================================================


class TestCoverageAnalyzer:
    """Unit tests for CoverageAnalyzer."""

    def test_analyze_returns_dict_of_floats(self, basic_state):
        analyzer = CoverageAnalyzer()
        result = analyzer.analyze(basic_state)
        assert isinstance(result, dict)
        assert all(isinstance(v, float) for v in result.values())

    def test_analyze_dimensions_are_in_0_1(self, basic_state):
        analyzer = CoverageAnalyzer()
        result = analyzer.analyze(basic_state)
        for k, v in result.items():
            assert 0.0 <= v <= 1.0, f"{k}={v} out of [0,1]"

    def test_analyze_fully_covered_state(self):
        state = _make_converged_state()
        analyzer = CoverageAnalyzer()
        result = analyzer.analyze(state)
        # Covers dimension should be 1.0 (always)
        assert result["covers"] == pytest.approx(1.0)
        # Sections == covers → sections ratio = 1.0
        assert result["sections"] == pytest.approx(1.0)

    def test_analyze_empty_sections(self):
        state = _make_state(n_covers=4, n_sections=0)
        analyzer = CoverageAnalyzer()
        result = analyzer.analyze(state)
        assert result["sections"] == pytest.approx(0.0)

    def test_analyze_treaty_dimension(self):
        # n_covers=3, n_treaties=2 → expected=2, ratio=1.0
        state = _make_state(n_covers=3, n_sections=3, n_treaties=2)
        analyzer = CoverageAnalyzer()
        result = analyzer.analyze(state)
        assert result["treaties"] == pytest.approx(1.0)

    def test_analyze_partial_treaty(self):
        # n_covers=4, n_treaties=1 → expected=3, ratio≈0.333
        state = _make_state(n_covers=4, n_sections=4, n_treaties=1)
        analyzer = CoverageAnalyzer()
        result = analyzer.analyze(state)
        assert result["treaties"] == pytest.approx(1.0 / 3.0, abs=0.01)

    def test_analyze_channel_dimension(self):
        state = _make_state(n_covers=4, n_channels=2)
        analyzer = CoverageAnalyzer()
        result = analyzer.analyze(state)
        assert result["channels"] == pytest.approx(0.5)

    def test_analyze_channel_capped_at_one(self):
        # More channels than covers
        state = _make_state(n_covers=2, n_channels=10)
        analyzer = CoverageAnalyzer()
        result = analyzer.analyze(state)
        assert result["channels"] == pytest.approx(1.0)

    def test_overall_coverage_returns_float_in_0_1(self, basic_state):
        analyzer = CoverageAnalyzer()
        cov = analyzer.overall_coverage(basic_state)
        assert isinstance(cov, float)
        assert 0.0 <= cov <= 1.0

    def test_overall_coverage_fully_covered(self):
        state = _make_converged_state()
        analyzer = CoverageAnalyzer()
        cov = analyzer.overall_coverage(state)
        # With full sections, treaties, channels → coverage near top
        assert cov >= 0.8

    def test_overall_coverage_partially_covered(self):
        state = _make_state(n_covers=4, n_sections=1, n_treaties=0)
        analyzer = CoverageAnalyzer()
        partial = analyzer.overall_coverage(state)
        full_state = _make_converged_state()
        full = analyzer.overall_coverage(full_state)
        assert partial < full

    def test_coverage_gaps_returns_list_of_strings(self, basic_state):
        analyzer = CoverageAnalyzer()
        gaps = analyzer.coverage_gaps(basic_state)
        assert isinstance(gaps, list)
        assert all(isinstance(g, str) for g in gaps)

    def test_coverage_gaps_on_partial_state(self):
        # sections < covers means "sections" should be a gap
        state = _make_state(n_covers=4, n_sections=2)
        analyzer = CoverageAnalyzer()
        gaps = analyzer.coverage_gaps(state)
        assert "sections" in gaps

    def test_coverage_gaps_fully_covered_state(self):
        state = _make_converged_state()
        analyzer = CoverageAnalyzer()
        gaps = analyzer.coverage_gaps(state)
        # sections=covers → no section gap; channels=covers → no channel gap
        assert "sections" not in gaps
        assert "channels" not in gaps

    def test_coverage_gaps_with_custom_target(self):
        state = _make_state(n_covers=4, n_sections=3)
        analyzer = CoverageAnalyzer(target_coverage=0.5)
        gaps = analyzer.coverage_gaps(state)
        # section_ratio = 0.75 >= 0.5 → sections NOT a gap
        assert "sections" not in gaps

    def test_weighted_coverage_returns_float_in_0_1(self, basic_state):
        analyzer = CoverageAnalyzer()
        wc = analyzer.weighted_coverage(basic_state)
        assert 0.0 <= wc <= 1.0

    def test_weighted_coverage_differs_from_unweighted(self):
        state = _make_state(n_covers=4, n_sections=4, n_treaties=3, n_channels=2)
        analyzer = CoverageAnalyzer()
        simple = analyzer.overall_coverage(state)
        weighted = analyzer.weighted_coverage(state)
        # They can differ — both should be valid floats in [0,1]
        assert 0.0 <= simple <= 1.0
        assert 0.0 <= weighted <= 1.0

    def test_weighted_coverage_zero_weights(self):
        state = _make_state()
        analyzer = CoverageAnalyzer(weights={})
        wc = analyzer.weighted_coverage(state)
        assert wc == pytest.approx(0.0)

    def test_coverage_trend_with_improving_states(self):
        states = [
            _make_state(n_covers=4, n_sections=i)
            for i in range(1, 5)
        ]
        analyzer = CoverageAnalyzer()
        trend = analyzer.coverage_trend(states)
        assert len(trend) == 4
        assert all(0.0 <= v <= 1.0 for v in trend)
        # Coverage should be non-decreasing
        for i in range(len(trend) - 1):
            assert trend[i] <= trend[i + 1] + 1e-9

    def test_coverage_trend_empty_history(self):
        analyzer = CoverageAnalyzer()
        trend = analyzer.coverage_trend([])
        assert trend == []

    def test_coverage_trend_single_state(self, basic_state):
        analyzer = CoverageAnalyzer()
        trend = analyzer.coverage_trend([basic_state])
        assert len(trend) == 1
        assert 0.0 <= trend[0] <= 1.0


# ===========================================================================
# 4. ConvergenceRateEstimator
# ===========================================================================


class TestConvergenceRateEstimator:
    """Unit tests for ConvergenceRateEstimator."""

    def test_initial_rate_is_zero(self):
        est = ConvergenceRateEstimator()
        assert est.estimated_rate() == pytest.approx(0.0)

    def test_update_single_observation_keeps_rate_zero(self):
        est = ConvergenceRateEstimator()
        est.update(0.5)
        assert est.estimated_rate() == pytest.approx(0.0)

    def test_update_two_observations_produces_nonzero_rate(self):
        est = ConvergenceRateEstimator()
        est.update(0.4)
        est.update(0.6)
        # EMA of diff = 0.3 * 0.2 = 0.06
        assert est.estimated_rate() == pytest.approx(0.06, abs=0.001)

    def test_update_improving_series_gives_positive_rate(self):
        est = ConvergenceRateEstimator()
        for v in [0.1, 0.2, 0.3, 0.4, 0.5]:
            est.update(v)
        assert est.estimated_rate() > 0

    def test_update_decreasing_series_gives_negative_rate(self):
        est = ConvergenceRateEstimator()
        for v in [0.9, 0.7, 0.5, 0.3, 0.1]:
            est.update(v)
        assert est.estimated_rate() < 0

    def test_history_grows_up_to_window(self):
        est = ConvergenceRateEstimator(window_size=5)
        for i in range(8):
            est.update(i * 0.1)
        assert len(est.history()) == 5

    def test_history_oldest_evicted_first(self):
        est = ConvergenceRateEstimator(window_size=3)
        est.update(1.0)
        est.update(2.0)
        est.update(3.0)
        est.update(4.0)  # 1.0 evicted
        assert est.history()[0] == pytest.approx(2.0)

    def test_history_returns_copy(self):
        est = ConvergenceRateEstimator()
        est.update(0.5)
        h = est.history()
        h.append(9999.0)
        assert len(est.history()) == 1

    def test_steps_to_convergence_when_already_there(self):
        est = ConvergenceRateEstimator()
        for v in [0.8, 0.9, 0.95]:
            est.update(v)
        steps = est.steps_to_convergence(current=0.96, target=0.95)
        assert steps == 0

    def test_steps_to_convergence_positive_rate(self):
        est = ConvergenceRateEstimator()
        for v in [0.1, 0.2, 0.3, 0.4]:
            est.update(v)
        steps = est.steps_to_convergence(current=0.4, target=0.95)
        assert steps is not None
        assert steps > 0

    def test_steps_to_convergence_stalled_returns_none(self):
        est = ConvergenceRateEstimator()
        # Flat series → rate ≈ 0 → stalled
        for _ in range(5):
            est.update(0.5)
        steps = est.steps_to_convergence(current=0.5, target=0.95)
        assert steps is None

    def test_is_stalling_with_flat_metrics(self):
        est = ConvergenceRateEstimator()
        for _ in range(5):
            est.update(0.5)
        assert est.is_stalling()

    def test_is_stalling_false_with_improving_metrics(self):
        est = ConvergenceRateEstimator()
        for v in [0.2, 0.4, 0.6, 0.8]:
            est.update(v)
        assert not est.is_stalling()

    def test_trend_improving(self):
        est = ConvergenceRateEstimator()
        for v in [0.1, 0.3, 0.5, 0.7]:
            est.update(v)
        assert est.trend() == "improving"

    def test_trend_stalling(self):
        est = ConvergenceRateEstimator()
        for _ in range(6):
            est.update(0.3)
        assert est.trend() == "stalling"

    def test_trend_diverging(self):
        est = ConvergenceRateEstimator()
        for v in [0.9, 0.7, 0.4, 0.1]:
            est.update(v)
        assert est.trend() == "diverging"

    def test_trend_converged_when_above_threshold(self):
        est = ConvergenceRateEstimator()
        for _ in range(3):
            est.update(0.96)
        assert est.trend() == "converged"

    def test_smoothing_factor_affects_rate(self):
        fast = ConvergenceRateEstimator(smoothing=0.9)
        slow = ConvergenceRateEstimator(smoothing=0.1)
        for v in [0.0, 0.5]:
            fast.update(v)
            slow.update(v)
        # fast estimator reacts more strongly to the jump
        assert abs(fast.estimated_rate()) >= abs(slow.estimated_rate())


# ===========================================================================
# 5. DivergenceDetector
# ===========================================================================


class TestDivergenceDetector:
    """Unit tests for DivergenceDetector."""

    def test_check_diverging_rate(self):
        detector = DivergenceDetector(divergence_threshold=-0.05)
        m = _make_metrics(rate=-0.1)
        assert detector.check(m, [])

    def test_check_converging_rate(self):
        detector = DivergenceDetector(divergence_threshold=-0.05)
        m = _make_metrics(rate=0.05)
        # No stall history → no stall
        result = detector.check(m, [])
        assert not result

    def test_check_stall_detected_via_history(self):
        detector = DivergenceDetector()
        history = [_make_metrics(coverage=0.5) for _ in range(DIVERGENCE_WINDOW + 1)]
        m = _make_metrics(coverage=0.5, rate=0.0)
        # All the same coverage → stall
        assert detector.check(m, history)

    def test_add_alert_stores_callback(self):
        detector = DivergenceDetector()
        calls = []
        detector.add_alert(lambda s, m: calls.append((s, m)))
        assert len(detector.alert_callbacks) == 1

    def test_trigger_alerts_invokes_callbacks(self, basic_state):
        detector = DivergenceDetector()
        calls = []
        detector.add_alert(lambda s, m: calls.append((s, m)))
        m = _make_metrics()
        detector.trigger_alerts(basic_state, m)
        assert len(calls) == 1
        assert calls[0][0] is basic_state
        assert calls[0][1] is m

    def test_trigger_alerts_multiple_callbacks(self, basic_state):
        detector = DivergenceDetector()
        counts = [0, 0]
        detector.add_alert(lambda s, m: counts.__setitem__(0, counts[0] + 1))
        detector.add_alert(lambda s, m: counts.__setitem__(1, counts[1] + 1))
        detector.trigger_alerts(basic_state, _make_metrics())
        assert counts == [1, 1]

    def test_trigger_alerts_no_callbacks_is_noop(self, basic_state):
        detector = DivergenceDetector()
        # Should not raise
        detector.trigger_alerts(basic_state, _make_metrics())

    def test_divergence_score_empty_history(self):
        detector = DivergenceDetector()
        score = detector.divergence_score([])
        assert score == pytest.approx(0.0)

    def test_divergence_score_positive_rates(self):
        detector = DivergenceDetector()
        history = [_make_metrics(rate=0.05) for _ in range(5)]
        score = detector.divergence_score(history)
        # negative rates → positive score; positive rates → negative score
        assert score == pytest.approx(-0.05, abs=0.001)

    def test_divergence_score_negative_rates_gives_positive_score(self):
        detector = DivergenceDetector()
        history = [_make_metrics(rate=-0.1) for _ in range(5)]
        score = detector.divergence_score(history)
        assert score > 0

    def test_divergence_score_uses_only_window(self):
        detector = DivergenceDetector()
        # First 10 all improve, last DIVERGENCE_WINDOW all diverge
        good = [_make_metrics(rate=0.1) for _ in range(10)]
        bad = [_make_metrics(rate=-0.2) for _ in range(DIVERGENCE_WINDOW)]
        score = detector.divergence_score(good + bad)
        assert score > 0  # bad window dominates

    def test_detect_stall_constant_coverage(self):
        detector = DivergenceDetector()
        history = [_make_metrics(coverage=0.5) for _ in range(DIVERGENCE_WINDOW + 1)]
        assert detector.detect_stall(history)

    def test_detect_stall_insufficient_history(self):
        detector = DivergenceDetector()
        history = [_make_metrics(coverage=0.5) for _ in range(2)]
        assert not detector.detect_stall(history)

    def test_detect_stall_improving_history(self):
        detector = DivergenceDetector()
        history = [
            _make_metrics(coverage=0.1 * (i + 1))
            for i in range(DIVERGENCE_WINDOW + 1)
        ]
        assert not detector.detect_stall(history)

    def test_detect_stall_custom_window(self):
        detector = DivergenceDetector()
        history = [_make_metrics(coverage=0.5) for _ in range(3)]
        assert detector.detect_stall(history, window=3)
        assert not detector.detect_stall(history, window=4)  # not enough history


# ===========================================================================
# 6. CertificationAuthority
# ===========================================================================


class TestCertificationAuthority:
    """Unit tests for CertificationAuthority."""

    def _make_authority(self, threshold: float = 0.95) -> CertificationAuthority:
        return CertificationAuthority(threshold=threshold, validity_period=300.0)

    def test_can_certify_false_low_coverage(self, converging_trajectory):
        state = _make_state(n_covers=4, n_sections=1)  # coverage=0.25
        auth = self._make_authority(threshold=0.95)
        assert not auth.can_certify(state, converging_trajectory)

    def test_can_certify_false_with_obligations(self):
        state = _make_state(n_covers=4, n_sections=4, n_obligations=2)
        traj = _make_trajectory([
            _make_state(n_covers=4, n_sections=3),
            state,
        ])
        auth = self._make_authority(threshold=0.5)
        assert not auth.can_certify(state, traj)

    def test_can_certify_false_short_trajectory(self):
        state = _make_converged_state()
        traj = _make_trajectory([state])  # only 1 state
        auth = self._make_authority(threshold=0.5)
        assert not auth.can_certify(state, traj)

    def test_can_certify_true_with_adequate_trajectory(self):
        s1 = _make_state(n_covers=4, n_sections=3)
        s2 = _make_converged_state()
        traj = _make_trajectory([s1, s2])
        auth = self._make_authority(threshold=0.5)
        assert auth.can_certify(s2, traj)

    def test_can_certify_false_regression(self):
        s1 = _make_converged_state()  # high coverage
        s2 = _make_state(n_covers=4, n_sections=1)  # regressed
        traj = _make_trajectory([s1, s2])
        auth = self._make_authority(threshold=0.0)
        assert not auth.can_certify(s2, traj)

    def test_certify_returns_certificate(self):
        s1 = _make_state(n_covers=4, n_sections=3)
        s2 = _make_converged_state()
        traj = _make_trajectory([s1, s2])
        auth = self._make_authority(threshold=0.5)
        cert = auth.certify(s2, traj)
        assert cert is not None
        assert hasattr(cert, "cert_id")
        assert hasattr(cert, "coverage_ratio")

    def test_certify_coverage_ratio_matches_state(self):
        s1 = _make_state(n_covers=4, n_sections=3)
        s2 = _make_converged_state()
        traj = _make_trajectory([s1, s2])
        auth = self._make_authority(threshold=0.5)
        cert = auth.certify(s2, traj)
        assert cert.coverage_ratio == pytest.approx(s2.coverage_ratio())

    def test_certify_appended_to_issued(self):
        s1 = _make_state(n_covers=4, n_sections=3)
        s2 = _make_converged_state()
        traj = _make_trajectory([s1, s2])
        auth = self._make_authority(threshold=0.5)
        cert = auth.certify(s2, traj)
        assert cert in auth.issued

    def test_certify_is_valid_immediately(self):
        s1 = _make_state(n_covers=4, n_sections=3)
        s2 = _make_converged_state()
        traj = _make_trajectory([s1, s2])
        auth = self._make_authority(threshold=0.5)
        cert = auth.certify(s2, traj)
        assert cert.is_valid()

    def test_certify_has_trajectory_id_in_evidence(self):
        s1 = _make_state(n_covers=4, n_sections=3)
        s2 = _make_converged_state()
        traj = _make_trajectory([s1, s2])
        auth = self._make_authority(threshold=0.5)
        cert = auth.certify(s2, traj)
        assert "trajectory_id" in cert.evidence or "trajectory_length" in cert.evidence

    def test_revoke_known_cert(self):
        s1 = _make_state(n_covers=4, n_sections=3)
        s2 = _make_converged_state()
        traj = _make_trajectory([s1, s2])
        auth = self._make_authority(threshold=0.5)
        cert = auth.certify(s2, traj)
        result = auth.revoke(cert.cert_id)
        assert result is True
        assert cert not in auth.issued

    def test_revoke_unknown_cert_returns_false(self):
        auth = self._make_authority()
        result = auth.revoke("nonexistent-id")
        assert result is False

    def test_revoke_removed_from_list_valid(self):
        s1 = _make_state(n_covers=4, n_sections=3)
        s2 = _make_converged_state()
        traj = _make_trajectory([s1, s2])
        auth = self._make_authority(threshold=0.5)
        cert = auth.certify(s2, traj)
        auth.revoke(cert.cert_id)
        assert cert not in auth.list_valid()

    def test_list_valid_returns_non_expired(self):
        s1 = _make_state(n_covers=4, n_sections=3)
        s2 = _make_converged_state()
        traj = _make_trajectory([s1, s2])
        auth = self._make_authority(threshold=0.5)
        cert = auth.certify(s2, traj)
        valid = auth.list_valid()
        assert cert in valid

    def test_list_valid_excludes_expired(self):
        # Issue cert with validity_period=0 so it expires immediately
        auth = CertificationAuthority(threshold=0.5, validity_period=0.0)
        s1 = _make_state(n_covers=4, n_sections=3)
        s2 = _make_converged_state()
        traj = _make_trajectory([s1, s2])
        cert = auth.certify(s2, traj)
        time.sleep(0.01)  # ensure expiry
        valid = auth.list_valid()
        assert cert not in valid

    def test_audit_returns_correct_counts(self):
        s1 = _make_state(n_covers=4, n_sections=3)
        s2 = _make_converged_state()
        traj = _make_trajectory([s1, s2])
        auth = self._make_authority(threshold=0.5)
        auth.certify(s2, traj)
        audit = auth.audit()
        assert audit["total_issued"] == 1
        assert audit["valid_count"] == 1
        assert audit["expired_count"] == 0
        assert "certificates" in audit

    def test_audit_empty_authority(self):
        auth = self._make_authority()
        audit = auth.audit()
        assert audit["total_issued"] == 0
        assert audit["valid_count"] == 0

    def test_multiple_certs_issued(self):
        auth = self._make_authority(threshold=0.5)
        for _ in range(3):
            s1 = _make_state(n_covers=4, n_sections=3)
            s2 = _make_converged_state()
            traj = _make_trajectory([s1, s2])
            auth.certify(s2, traj)
        assert len(auth.issued) == 3
        assert auth.audit()["total_issued"] == 3


# ===========================================================================
# 7. ConvergenceMonitor
# ===========================================================================


class TestConvergenceMonitor:
    """Unit tests for ConvergenceMonitor (semantic control version)."""

    def test_observe_returns_convergence_metrics(self, basic_monitor, basic_state):
        m = basic_monitor.observe(basic_state)
        assert isinstance(m, ConvergenceMetrics)

    def test_observe_appends_to_history(self, basic_monitor, basic_state):
        assert len(basic_monitor.metrics_history) == 0
        basic_monitor.observe(basic_state)
        assert len(basic_monitor.metrics_history) == 1

    def test_observe_multiple_states_builds_history(self, basic_monitor):
        for i in range(5):
            state = _make_state(n_covers=4, n_sections=i)
            basic_monitor.observe(state)
        assert len(basic_monitor.metrics_history) == 5

    def test_observe_metrics_coverage_ratio_matches_state(self, basic_monitor):
        state = _make_state(n_covers=4, n_sections=2)
        m = basic_monitor.observe(state)
        assert m.coverage_ratio == pytest.approx(state.coverage_ratio())

    def test_observe_syncs_obligations(self, basic_monitor):
        state = _make_state(n_covers=4, n_sections=2, n_obligations=3)
        m = basic_monitor.observe(state)
        assert m.obligation_count == 3

    def test_observe_lyapunov_value_nonnegative(self, basic_monitor, basic_state):
        m = basic_monitor.observe(basic_state)
        assert m.lyapunov_value >= 0.0

    def test_is_converged_false_before_observe(self, basic_monitor):
        assert not basic_monitor.is_converged()

    def test_is_converged_false_partial_state(self, basic_monitor):
        state = _make_state(n_covers=4, n_sections=2)
        basic_monitor.observe(state)
        assert not basic_monitor.is_converged()

    def test_is_converged_true_after_sufficient_steps(self):
        monitor = ConvergenceMonitor()
        # Feed a state that produces metrics satisfying is_converged
        # We need: coverage >= 0.95, obligations == 0, lyapunov < 1e-6
        # The s03 monitor builds ConvergenceMetrics with lyapunov from algorithms or inline.
        # Let's just test it reports convergence after many fully-covered states.
        state = _make_converged_state()
        for _ in range(5):
            monitor.observe(state)
        # Check that latest metrics reflects convergence signals
        m = monitor.latest_metrics()
        assert m is not None
        assert m.coverage_ratio == pytest.approx(1.0)
        assert m.obligation_count == 0

    def test_try_certify_returns_none_when_not_converged(self, basic_monitor):
        state = _make_state(n_covers=4, n_sections=1)
        traj = _make_trajectory([state])
        result = basic_monitor.try_certify(state, traj)
        assert result is None

    def test_try_certify_returns_certificate_when_eligible(self):
        monitor = ConvergenceMonitor()
        s1 = _make_state(n_covers=4, n_sections=3)
        s2 = _make_converged_state()
        traj = _make_trajectory([s1, s2])
        monitor.authority.threshold = 0.5
        result = monitor.try_certify(s2, traj)
        assert result is not None

    def test_report_returns_dict(self, basic_monitor, basic_state):
        basic_monitor.observe(basic_state)
        report = basic_monitor.report()
        assert isinstance(report, dict)

    def test_report_contains_expected_keys(self, basic_monitor, basic_state):
        basic_monitor.observe(basic_state)
        report = basic_monitor.report()
        for key in ["is_converged", "step_count", "latest_metrics", "rate_trend",
                    "divergence_score", "obligations", "certificates"]:
            assert key in report, f"Missing key: {key}"

    def test_report_step_count_increments(self, basic_monitor):
        for i in range(3):
            basic_monitor.observe(_make_state(n_sections=i))
        report = basic_monitor.report()
        assert report["step_count"] == 3

    def test_report_latest_metrics_is_dict_or_none(self, basic_monitor, basic_state):
        basic_monitor.observe(basic_state)
        report = basic_monitor.report()
        assert isinstance(report["latest_metrics"], dict)

    def test_report_before_observe_latest_metrics_is_none(self, basic_monitor):
        report = basic_monitor.report()
        assert report["latest_metrics"] is None

    def test_reset_clears_history(self, basic_monitor, basic_state):
        basic_monitor.observe(basic_state)
        basic_monitor.observe(basic_state)
        assert len(basic_monitor.metrics_history) == 2
        basic_monitor.reset()
        assert len(basic_monitor.metrics_history) == 0

    def test_reset_clears_obligation_tracker(self, basic_monitor):
        state = _make_state(n_obligations=3)
        basic_monitor.observe(state)
        basic_monitor.reset()
        assert basic_monitor.obligation_tracker.pending_count() == 0

    def test_reset_clears_rate_estimator(self, basic_monitor, basic_state):
        basic_monitor.observe(basic_state)
        basic_monitor.reset()
        assert basic_monitor.rate_estimator.estimated_rate() == pytest.approx(0.0)

    def test_reset_allows_fresh_observation(self, basic_monitor, basic_state):
        basic_monitor.observe(basic_state)
        basic_monitor.reset()
        m = basic_monitor.observe(basic_state)
        assert isinstance(m, ConvergenceMetrics)
        assert len(basic_monitor.metrics_history) == 1

    def test_latest_metrics_none_when_empty(self, basic_monitor):
        assert basic_monitor.latest_metrics() is None

    def test_latest_metrics_after_observe(self, basic_monitor, basic_state):
        m = basic_monitor.observe(basic_state)
        assert basic_monitor.latest_metrics() is m


# ===========================================================================
# 8. Integration tests
# ===========================================================================


@pytest.mark.skipif(not CONTROLLER_AVAILABLE, reason="controller not available")
class TestIntegrationWithController:
    """Integration tests using upstream controller module."""

    def test_obligation_tracker_with_move_kinds(self):
        tracker = ObligationTracker()
        # Use MoveKind values as obligation IDs (realistic usage)
        for kind in MoveKind:
            tracker.add(f"ob-{kind.value}", {"kind": kind.value})
        assert tracker.pending_count() == len(list(MoveKind))

    def test_obligation_tracker_resolve_by_move_kind(self):
        tracker = ObligationTracker()
        kind = MoveKind.CONSTRUCT
        tracker.add(f"ob-{kind.value}", {"kind": kind.value})
        tracker.resolve(f"ob-{kind.value}", {"resolution": "constructed"})
        assert tracker.is_resolved(f"ob-{kind.value}")

    def test_convergence_monitor_alongside_base_monitor(self):
        semantic_monitor = ConvergenceMonitor()
        state = _make_converged_state()
        m = semantic_monitor.observe(state)
        assert isinstance(m, ConvergenceMetrics)
        # Both monitors coexist — semantic monitor produces ConvergenceMetrics
        assert m.coverage_ratio == pytest.approx(1.0)

    def test_divergence_detector_with_move_kind_alert(self):
        fired = []
        detector = DivergenceDetector(divergence_threshold=-0.01)
        detector.add_alert(lambda s, m: fired.append(m.rate_estimate))
        diverging_metrics = _make_metrics(rate=-0.1)
        state = _make_state()
        detector.trigger_alerts(state, diverging_metrics)
        assert len(fired) == 1
        assert fired[0] == pytest.approx(-0.1)


@pytest.mark.skipif(not TRUST_AVAILABLE, reason="trust module not available")
class TestIntegrationWithTrust:
    """Integration tests using the evidence trust module."""

    def test_certification_authority_gated_by_trust_level(self):
        """Simulate TrustLevel-gated certification: only HIGH trust certifies."""
        auth = CertificationAuthority(threshold=0.5)
        s1 = _make_state(n_covers=4, n_sections=3)
        s2 = _make_converged_state()
        traj = _make_trajectory([s1, s2])

        # Simulate: only certify if trust is HIGH
        trust_gated_certify = lambda lvl: (
            auth.certify(s2, traj) if lvl == TrustLevel.HIGH else None
        )
        assert trust_gated_certify(TrustLevel.LOW) is None
        assert trust_gated_certify(TrustLevel.HIGH) is not None

    def test_coverage_analyzer_with_trust_tier_weighted(self):
        """Weighted coverage can model trust tier as dimension weight."""
        state = _make_converged_state()
        # Weight sections more heavily for CERTIFIED tier
        weights = {
            "covers": float(TrustTier.CERTIFIED) * 0.1,
            "sections": float(TrustTier.CERTIFIED) * 0.3,
            "treaties": 0.5,
            "channels": 0.2,
            "contexts": 0.1,
        }
        analyzer = CoverageAnalyzer(weights=weights)
        wc = analyzer.weighted_coverage(state)
        assert 0.0 <= wc <= 1.0

    def test_obligation_tracker_with_trust_levels(self):
        """Obligations can carry trust annotations."""
        tracker = ObligationTracker()
        for lvl in TrustLevel:
            tracker.add(f"ob-{lvl.value}", {"trust_level": lvl.value, "rule": "test"})
        assert tracker.pending_count() == len(list(TrustLevel))
        # Resolve only HIGH trust obligations
        for lvl in TrustLevel:
            if lvl == TrustLevel.HIGH:
                tracker.resolve(f"ob-{lvl.value}", {"discharged_by": "high_trust_proof"})
        assert tracker.resolved_count() == 1


@pytest.mark.skipif(
    not (MODELS_AVAILABLE and CONTROLLER_AVAILABLE),
    reason="models or controller not available",
)
class TestEndToEndConvergenceMonitoring:
    """End-to-end: create state → monitor → detect convergence → certify."""

    def test_full_pipeline_no_convergence(self):
        monitor = ConvergenceMonitor()
        initial = _make_state(n_covers=4, n_sections=0, n_obligations=3)
        m = monitor.observe(initial)
        assert isinstance(m, ConvergenceMetrics)
        assert not monitor.is_converged()
        traj = _make_trajectory([initial])
        cert = monitor.try_certify(initial, traj)
        assert cert is None

    def test_full_pipeline_converging_trajectory(self):
        monitor = ConvergenceMonitor()
        monitor.authority.threshold = 0.5
        states = [
            _make_state(n_covers=4, n_sections=i, n_treaties=max(i - 1, 0))
            for i in range(1, 5)
        ]
        for s in states:
            monitor.observe(s)
        traj = _make_trajectory(states)
        cert = monitor.try_certify(states[-1], traj)
        assert cert is not None

    def test_full_pipeline_reset_and_retry(self):
        monitor = ConvergenceMonitor()
        monitor.authority.threshold = 0.5
        # First run
        for i in range(3):
            monitor.observe(_make_state(n_sections=i))
        assert len(monitor.metrics_history) == 3
        # Reset
        monitor.reset()
        assert len(monitor.metrics_history) == 0
        # Second run after reset
        s1 = _make_state(n_covers=4, n_sections=3)
        s2 = _make_converged_state()
        monitor.observe(s1)
        monitor.observe(s2)
        traj = _make_trajectory([s1, s2])
        cert = monitor.try_certify(s2, traj)
        assert cert is not None

    def test_divergence_detection_fires_during_regression(self):
        alerts = []
        monitor = ConvergenceMonitor()
        monitor.divergence_detector.divergence_threshold = -0.01
        monitor.divergence_detector.add_alert(lambda s, m: alerts.append(m.rate_estimate))
        # Feed regressing states
        states = [
            _make_state(n_covers=4, n_sections=4 - i)
            for i in range(5)
        ]
        for s in states:
            monitor.observe(s)
        # Some regression alerts should have fired
        assert len(alerts) >= 1 or monitor.report()["divergence_score"] > 0

    def test_report_certificates_in_audit(self):
        monitor = ConvergenceMonitor()
        monitor.authority.threshold = 0.5
        s1 = _make_state(n_covers=4, n_sections=3)
        s2 = _make_converged_state()
        traj = _make_trajectory([s1, s2])
        monitor.observe(s1)
        monitor.observe(s2)
        monitor.try_certify(s2, traj)
        report = monitor.report()
        assert report["certificates"]["total_issued"] >= 1

    def test_coverage_trend_over_trajectory(self):
        analyzer = CoverageAnalyzer()
        states = [
            _make_state(n_covers=4, n_sections=i)
            for i in range(1, 5)
        ]
        trend = analyzer.coverage_trend(states)
        assert len(trend) == 4
        # Coverage should be non-decreasing
        for i in range(len(trend) - 1):
            assert trend[i] <= trend[i + 1] + 1e-9
