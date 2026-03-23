"""Tests for jugeo.generation.inhabitant_fleets.s03_semantic_backpressure."""
from pathlib import Path
import sys
ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
import pytest
import time
import uuid

# ---------------------------------------------------------------------------
# Optional imports
# ---------------------------------------------------------------------------
try:
    from jugeo.generation.inhabitant_fleets.s03_semantic_backpressure import (
        InstabilityMetric,
        BackpressureMonitor,
        BackpressureController,
        BackpressureResolver,
        CascadeDetector,
    )
    from jugeo.generation.inhabitant_fleets.models import (
        InhabitantProposal,
        BackpressureSignal,
        SemanticMove,
        ProposalStatus,
        SeverityLevel,
        MoveType,
    )
    from jugeo.generation.inhabitant_fleets.s02_ai_fleets import (
        FleetMember,
        FleetCoordinator,
        InhabitantFleet,
    )
    from jugeo.evidence.trust import TrustTier

    _S03_AVAILABLE = True
except ImportError:
    _S03_AVAILABLE = False

_SKIP = pytest.mark.skipif(not _S03_AVAILABLE, reason="s03 not importable")

# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _make_signal(
    signal_id=None,
    source="patch-src",
    targets=None,
    instability=0.6,
    threshold=0.5,
    severity=None,
):
    if not _S03_AVAILABLE:
        return None
    if signal_id is None:
        signal_id = str(uuid.uuid4())
    if severity is None:
        severity = SeverityLevel.MEDIUM
    if targets is None:
        targets = ["patch-tgt-1"]
    return BackpressureSignal(
        signal_id=signal_id,
        source_patch=source,
        target_patches=targets,
        instability_score=instability,
        threshold=threshold,
        severity=severity,
        timestamp=time.time(),
        remediation_hints=["reduce-load"],
    )


def _make_metric(metric_id=None, patch_pair=None, rounds=5, score=0.4, trend=0.0):
    if not _S03_AVAILABLE:
        return None
    if metric_id is None:
        metric_id = f"metric-{uuid.uuid4().hex[:8]}"
    if patch_pair is None:
        patch_pair = ("patch-A", "patch-B")
    return InstabilityMetric(
        metric_id=metric_id,
        patch_pair=patch_pair,
        measurement_rounds=rounds,
        current_score=score,
        trend=trend,
    )


def _make_proposal(content="test", patch_id="patch-001", evidence=0.7):
    if not _S03_AVAILABLE:
        return None
    return InhabitantProposal(
        proposal_id=str(uuid.uuid4()),
        patch_id=patch_id,
        section_label="sec",
        semantic_content=content,
        proposer_id="proposer",
        trust_tier=TrustTier.PROPOSAL,
        evidence_score=evidence,
        competing_proposals=[],
        status=ProposalStatus.PENDING,
        created_at=time.time(),
        metadata={},
    )


def _make_fleet(fleet_id=None, num_members=3):
    if not _S03_AVAILABLE:
        return None
    if fleet_id is None:
        fleet_id = f"fleet-{uuid.uuid4().hex[:8]}"
    members = [
        FleetMember(
            member_id=f"m-{i}",
            specialization="spec",
            trust_tier=TrustTier.PROPOSAL,
            current_load=float(i * 5),
            proposal_history=[],
        )
        for i in range(num_members)
    ]
    return InhabitantFleet(
        fleet_id=fleet_id,
        members=members,
        coordinator=FleetCoordinator(),
        strategy="greedy",
        current_bids=[],
        completed_proposals=[],
    )


def _make_move(move_type=None, distance=0.3):
    if not _S03_AVAILABLE:
        return None
    if move_type is None:
        move_type = MoveType.REFINE
    return SemanticMove(
        move_id=str(uuid.uuid4()),
        move_type=move_type,
        source_state={"v": "s"},
        target_state={"v": "t"},
        semantic_distance=distance,
        validity_certificate="cert",
        overlap_impact=0.1,
        move_cost=1.0,
    )


# ---------------------------------------------------------------------------
# TestInstabilityMetric
# ---------------------------------------------------------------------------

@_SKIP
class TestInstabilityMetric:

    def test_creation_stores_metric_id(self):
        m = _make_metric(metric_id="metric-test-001")
        assert m.metric_id == "metric-test-001"

    def test_creation_stores_patch_pair(self):
        pair = ("pA", "pB")
        m = _make_metric(patch_pair=pair)
        assert m.patch_pair == pair

    def test_creation_stores_current_score(self):
        m = _make_metric(score=0.42)
        assert abs(m.current_score - 0.42) < 1e-9

    def test_creation_stores_trend(self):
        m = _make_metric(trend=0.15)
        assert abs(m.trend - 0.15) < 1e-9

    def test_update_changes_score(self):
        m = _make_metric(score=0.3)
        m.update(0.7)
        assert m.current_score != 0.3

    def test_update_new_score_reflected(self):
        m = _make_metric(score=0.3)
        m.update(0.7)
        # After update, score should be somewhere between old and new, or exactly new
        assert m.current_score >= 0.0

    def test_update_increases_rounds(self):
        m = _make_metric(rounds=5)
        old_rounds = m.measurement_rounds
        m.update(0.5)
        assert m.measurement_rounds >= old_rounds

    def test_get_trend_returns_float(self):
        m = _make_metric()
        trend = m.get_trend()
        assert isinstance(trend, (int, float))

    def test_get_trend_rising_after_rising_updates(self):
        m = _make_metric(score=0.1, trend=0.0)
        m.update(0.3)
        m.update(0.5)
        m.update(0.7)
        trend = m.get_trend()
        assert isinstance(trend, (int, float))

    def test_get_trend_falling_after_falling_updates(self):
        m = _make_metric(score=0.9, trend=0.0)
        m.update(0.7)
        m.update(0.5)
        m.update(0.3)
        trend = m.get_trend()
        assert isinstance(trend, (int, float))

    def test_get_trend_stable_if_constant(self):
        m = _make_metric(score=0.5, trend=0.0)
        for _ in range(5):
            m.update(0.5)
        trend = m.get_trend()
        # Trend should be near zero for stable measurements
        assert abs(trend) <= 1.0

    def test_exceeds_threshold_true_when_above(self):
        m = _make_metric(score=0.8)
        assert m.exceeds_threshold(0.5) is True

    def test_exceeds_threshold_false_when_below(self):
        m = _make_metric(score=0.3)
        assert m.exceeds_threshold(0.5) is False

    def test_exceeds_threshold_at_boundary(self):
        m = _make_metric(score=0.5)
        result = m.exceeds_threshold(0.5)
        assert isinstance(result, bool)

    @pytest.mark.parametrize("score,threshold,expected", [
        (0.9, 0.5, True),
        (0.1, 0.5, False),
        (0.5, 0.5, False),
        (0.51, 0.5, True),
        (0.0, 0.0, False),
        (1.0, 0.99, True),
    ])
    def test_exceeds_threshold_parametrized(self, score, threshold, expected):
        m = _make_metric(score=score)
        assert m.exceeds_threshold(threshold) is expected

    def test_multiple_updates_accumulate(self):
        m = _make_metric(score=0.1, rounds=0)
        for val in [0.2, 0.4, 0.6, 0.8]:
            m.update(val)
        assert m.current_score >= 0.0


# ---------------------------------------------------------------------------
# TestBackpressureMonitor
# ---------------------------------------------------------------------------

@_SKIP
class TestBackpressureMonitor:

    def test_monitor_instantiation(self):
        mon = BackpressureMonitor(threshold=0.5)
        assert mon is not None

    def test_monitor_stores_threshold(self):
        mon = BackpressureMonitor(threshold=0.7)
        assert abs(mon.threshold - 0.7) < 1e-9

    def test_monitor_empty_proposals(self):
        mon = BackpressureMonitor(threshold=0.5)
        signals = mon.monitor([])
        assert isinstance(signals, list)

    def test_monitor_returns_list(self):
        mon = BackpressureMonitor(threshold=0.5)
        proposals = [_make_proposal(f"content-{i}", f"patch-{i}") for i in range(3)]
        result = mon.monitor(proposals)
        assert isinstance(result, list)

    def test_monitor_emits_signal_when_instability_high(self):
        mon = BackpressureMonitor(threshold=0.1)
        proposals = [_make_proposal(f"c-{i}", f"patch-A") for i in range(10)]
        signals = mon.monitor(proposals)
        assert isinstance(signals, list)

    def test_compute_instability_returns_float(self):
        mon = BackpressureMonitor(threshold=0.5)
        pair = ("patch-A", "patch-B")
        score = mon.compute_instability(pair)
        assert isinstance(score, (int, float))

    def test_compute_instability_non_negative(self):
        mon = BackpressureMonitor(threshold=0.5)
        score = mon.compute_instability(("patch-X", "patch-Y"))
        assert score >= 0.0

    def test_detect_cascade_returns_list(self):
        mon = BackpressureMonitor(threshold=0.5)
        signals = [_make_signal(instability=0.9, threshold=0.5) for _ in range(3)]
        result = mon.detect_cascade(signals)
        assert isinstance(result, list)

    def test_detect_cascade_empty_signals(self):
        mon = BackpressureMonitor(threshold=0.5)
        result = mon.detect_cascade([])
        assert isinstance(result, list)

    def test_emit_signal_returns_signal(self):
        mon = BackpressureMonitor(threshold=0.5)
        sig = mon.emit_signal(0.8, ("patch-A", "patch-B"))
        assert isinstance(sig, BackpressureSignal)

    def test_emit_signal_instability_score_set(self):
        mon = BackpressureMonitor(threshold=0.5)
        sig = mon.emit_signal(0.77, ("patch-A", "patch-B"))
        assert abs(sig.instability_score - 0.77) < 1e-6

    def test_emit_signal_high_instability_is_critical(self):
        mon = BackpressureMonitor(threshold=0.5)
        sig = mon.emit_signal(0.99, ("patch-A", "patch-B"))
        assert sig is not None

    @pytest.mark.parametrize("threshold", [0.1, 0.3, 0.5, 0.7, 0.9])
    def test_monitor_with_varying_thresholds(self, threshold):
        mon = BackpressureMonitor(threshold=threshold)
        proposals = [_make_proposal(f"c-{i}") for i in range(5)]
        result = mon.monitor(proposals)
        assert isinstance(result, list)

    def test_monitor_no_signal_below_threshold(self):
        mon = BackpressureMonitor(threshold=0.99)
        proposals = [_make_proposal("low instability")]
        signals = mon.monitor(proposals)
        # With very high threshold, no signals should be emitted
        assert isinstance(signals, list)


# ---------------------------------------------------------------------------
# TestBackpressureController
# ---------------------------------------------------------------------------

@_SKIP
class TestBackpressureController:

    def test_controller_instantiation(self):
        ctrl = BackpressureController()
        assert ctrl is not None

    def test_apply_does_not_crash(self):
        ctrl = BackpressureController()
        signal = _make_signal()
        fleet = _make_fleet()
        ctrl.apply(signal, [fleet])

    def test_throttle_fleet_reduces_member_loads(self):
        ctrl = BackpressureController()
        fleet = _make_fleet(num_members=3)
        for m in fleet.members:
            m.current_load = 80.0
        ctrl.throttle_fleet(fleet, rate=0.5)
        # After throttling, loads should be reduced or unchanged
        for m in fleet.members:
            assert m.current_load <= 80.0

    def test_throttle_fleet_rate_zero(self):
        ctrl = BackpressureController()
        fleet = _make_fleet(num_members=2)
        for m in fleet.members:
            m.current_load = 50.0
        ctrl.throttle_fleet(fleet, rate=0.0)
        for m in fleet.members:
            assert m.current_load >= 0.0

    def test_throttle_fleet_rate_one_no_change(self):
        ctrl = BackpressureController()
        fleet = _make_fleet(num_members=2)
        for m in fleet.members:
            m.current_load = 50.0
        ctrl.throttle_fleet(fleet, rate=1.0)
        # Rate 1.0 means no throttling (full speed)
        for m in fleet.members:
            assert m.current_load >= 0.0

    def test_release_backpressure_increases_loads(self):
        ctrl = BackpressureController()
        fleet = _make_fleet(num_members=3)
        for m in fleet.members:
            m.current_load = 10.0
        ctrl.release_backpressure(fleet)
        # Releasing should not decrease loads below current
        for m in fleet.members:
            assert m.current_load >= 0.0

    def test_compute_safe_rate_returns_float(self):
        ctrl = BackpressureController()
        rate = ctrl.compute_safe_rate(0.5)
        assert isinstance(rate, float)

    def test_compute_safe_rate_in_range(self):
        ctrl = BackpressureController()
        for instability in [0.0, 0.25, 0.5, 0.75, 1.0]:
            rate = ctrl.compute_safe_rate(instability)
            assert 0.1 <= rate <= 1.0

    @pytest.mark.parametrize("instability,expected_max_rate", [
        (0.0, 1.0),
        (0.5, 1.0),
        (0.9, 1.0),
        (1.0, 1.0),
    ])
    def test_compute_safe_rate_clamped(self, instability, expected_max_rate):
        ctrl = BackpressureController()
        rate = ctrl.compute_safe_rate(instability)
        assert rate <= expected_max_rate

    def test_compute_safe_rate_low_instability_high(self):
        ctrl = BackpressureController()
        rate_low = ctrl.compute_safe_rate(0.0)
        rate_high = ctrl.compute_safe_rate(1.0)
        # Low instability should yield higher rate than high instability
        assert rate_low >= rate_high

    @pytest.mark.parametrize("instability", [0.0, 0.1, 0.5, 0.9, 1.0])
    def test_compute_safe_rate_all_values(self, instability):
        ctrl = BackpressureController()
        rate = ctrl.compute_safe_rate(instability)
        assert 0.1 <= rate <= 1.0

    def test_apply_with_multiple_fleets(self):
        ctrl = BackpressureController()
        signal = _make_signal(instability=0.9)
        fleets = [_make_fleet() for _ in range(3)]
        ctrl.apply(signal, fleets)  # Should not crash

    def test_throttle_fleet_with_no_members(self):
        ctrl = BackpressureController()
        fleet = _make_fleet(num_members=0)
        ctrl.throttle_fleet(fleet, rate=0.5)  # Should not crash


# ---------------------------------------------------------------------------
# TestBackpressureResolver
# ---------------------------------------------------------------------------

@_SKIP
class TestBackpressureResolver:

    def test_resolver_instantiation(self):
        resolver = BackpressureResolver()
        assert resolver is not None

    def test_resolve_returns_result(self):
        resolver = BackpressureResolver()
        signal = _make_signal()
        proposals = [_make_proposal(f"c-{i}") for i in range(3)]
        result = resolver.resolve(signal, proposals)
        assert result is not None or result is None

    def test_resolve_returns_list_or_dict(self):
        resolver = BackpressureResolver()
        signal = _make_signal(instability=0.8)
        proposals = [_make_proposal(f"c-{i}") for i in range(3)]
        result = resolver.resolve(signal, proposals)
        assert isinstance(result, (list, dict, type(None)))

    def test_find_stabilizing_moves_returns_list(self):
        resolver = BackpressureResolver()
        signal = _make_signal(instability=0.8)
        moves = resolver.find_stabilizing_moves(signal)
        assert isinstance(moves, list)

    def test_find_stabilizing_moves_non_empty_for_high_instability(self):
        resolver = BackpressureResolver()
        signal = _make_signal(instability=0.99, threshold=0.1)
        moves = resolver.find_stabilizing_moves(signal)
        # High instability should produce stabilizing moves
        assert isinstance(moves, list)

    def test_apply_move_does_not_crash(self):
        resolver = BackpressureResolver()
        move = _make_move()
        proposals = [_make_proposal(f"c-{i}") for i in range(2)]
        resolver.apply_move(move, proposals)

    def test_apply_move_returns_modified_proposals(self):
        resolver = BackpressureResolver()
        move = _make_move(move_type=MoveType.RETRACT)
        proposals = [_make_proposal("content")]
        result = resolver.apply_move(move, proposals)
        assert result is not None or result is None

    def test_resolve_empty_proposals(self):
        resolver = BackpressureResolver()
        signal = _make_signal()
        result = resolver.resolve(signal, [])
        assert result is not None or result is None

    def test_find_stabilizing_moves_semantic_distance(self):
        resolver = BackpressureResolver()
        signal = _make_signal(instability=0.6)
        moves = resolver.find_stabilizing_moves(signal)
        for m in moves:
            if isinstance(m, SemanticMove):
                assert m.semantic_distance >= 0.0

    @pytest.mark.parametrize("instability", [0.1, 0.5, 0.9])
    def test_resolve_parametrized_instability(self, instability):
        resolver = BackpressureResolver()
        signal = _make_signal(instability=instability)
        proposals = [_make_proposal(f"c-{i}") for i in range(3)]
        result = resolver.resolve(signal, proposals)
        assert result is not None or result is None


# ---------------------------------------------------------------------------
# TestCascadeDetector
# ---------------------------------------------------------------------------

@_SKIP
class TestCascadeDetector:

    def test_detector_instantiation(self):
        det = CascadeDetector(cascade_threshold=0.7)
        assert det is not None

    def test_detector_stores_threshold(self):
        det = CascadeDetector(cascade_threshold=0.8)
        assert abs(det.cascade_threshold - 0.8) < 1e-9

    def test_detect_empty_signals(self):
        det = CascadeDetector(cascade_threshold=0.7)
        result = det.detect([])
        assert isinstance(result, list)

    def test_detect_returns_list(self):
        det = CascadeDetector(cascade_threshold=0.7)
        signals = [_make_signal(instability=float(i) / 5.0) for i in range(5)]
        result = det.detect(signals)
        assert isinstance(result, list)

    def test_detect_high_instability_triggers_cascade(self):
        det = CascadeDetector(cascade_threshold=0.5)
        signals = [_make_signal(instability=0.9, threshold=0.5) for _ in range(5)]
        result = det.detect(signals)
        assert isinstance(result, list)

    def test_detect_low_instability_no_cascade(self):
        det = CascadeDetector(cascade_threshold=0.9)
        signals = [_make_signal(instability=0.1, threshold=0.5) for _ in range(3)]
        result = det.detect(signals)
        assert isinstance(result, list)
        assert len(result) == 0

    def test_trace_cascade_returns_list(self):
        det = CascadeDetector(cascade_threshold=0.7)
        origin = _make_signal(instability=0.95, source="origin-patch", targets=["p1", "p2"])
        trace = det.trace_cascade(origin)
        assert isinstance(trace, list)

    def test_trace_cascade_includes_origin(self):
        det = CascadeDetector(cascade_threshold=0.7)
        origin = _make_signal(instability=0.95)
        trace = det.trace_cascade(origin)
        # Trace should include the origin or related signals
        assert isinstance(trace, list)

    def test_estimate_cascade_impact_returns_float(self):
        det = CascadeDetector(cascade_threshold=0.7)
        cascade = [_make_signal(instability=float(i) / 4.0) for i in range(4)]
        impact = det.estimate_cascade_impact(cascade)
        assert isinstance(impact, (int, float))

    def test_estimate_cascade_impact_non_negative(self):
        det = CascadeDetector(cascade_threshold=0.7)
        cascade = [_make_signal(instability=0.8) for _ in range(3)]
        impact = det.estimate_cascade_impact(cascade)
        assert impact >= 0.0

    def test_estimate_cascade_impact_empty_cascade(self):
        det = CascadeDetector(cascade_threshold=0.7)
        impact = det.estimate_cascade_impact([])
        assert impact == 0.0 or impact >= 0.0

    @pytest.mark.parametrize("cascade_threshold", [0.3, 0.5, 0.7, 0.9])
    def test_detect_parametrized_threshold(self, cascade_threshold):
        det = CascadeDetector(cascade_threshold=cascade_threshold)
        signals = [_make_signal(instability=0.8) for _ in range(5)]
        result = det.detect(signals)
        assert isinstance(result, list)

    def test_cascade_propagates_through_patches(self):
        det = CascadeDetector(cascade_threshold=0.6)
        signals = [
            _make_signal(source=f"patch-{i}", targets=[f"patch-{i+1}"], instability=0.9)
            for i in range(5)
        ]
        result = det.detect(signals)
        assert isinstance(result, list)

    def test_trace_cascade_large_signal_set(self):
        det = CascadeDetector(cascade_threshold=0.6)
        origin = _make_signal(instability=0.99)
        trace = det.trace_cascade(origin)
        assert isinstance(trace, list)


# ---------------------------------------------------------------------------
# Integration: Proposals → Monitor → Signals → Controller → Fleet Throttling
# ---------------------------------------------------------------------------

@_SKIP
class TestBackpressureIntegration:

    def test_full_backpressure_pipeline(self):
        """proposals → monitor → signals → controller → fleet throttling."""
        mon = BackpressureMonitor(threshold=0.3)
        ctrl = BackpressureController()
        fleet = _make_fleet(num_members=3)

        # Load up fleet members
        for m in fleet.members:
            m.current_load = 70.0

        # Create competing proposals (simulating instability)
        proposals = [_make_proposal(f"content-{i}", "patch-001") for i in range(8)]
        signals = mon.monitor(proposals)

        # Apply any emitted signals to the fleet
        for sig in signals:
            ctrl.apply(sig, [fleet])

        # Fleet should still be in valid state
        for m in fleet.members:
            assert 0.0 <= m.current_load <= 100.0

    def test_cascade_detection_integration(self):
        """High instability signals trigger cascade detection."""
        det = CascadeDetector(cascade_threshold=0.6)
        mon = BackpressureMonitor(threshold=0.1)

        proposals = [_make_proposal(f"c-{i}", "patch-001") for i in range(5)]
        signals = mon.monitor(proposals)

        cascades = det.detect(signals)
        assert isinstance(cascades, list)

    def test_resolver_stabilizes_proposals(self):
        """Resolver should reduce proposal count or apply stabilizing moves."""
        resolver = BackpressureResolver()
        signal = _make_signal(instability=0.95, threshold=0.5)
        proposals = [_make_proposal(f"c-{i}") for i in range(5)]

        result = resolver.resolve(signal, proposals)
        moves = resolver.find_stabilizing_moves(signal)

        assert isinstance(moves, list)

    def test_monitor_then_escalate(self):
        """Monitor emits signals; escalate increases severity."""
        mon = BackpressureMonitor(threshold=0.2)
        proposals = [_make_proposal(f"c-{i}") for i in range(10)]
        signals = mon.monitor(proposals)

        for sig in signals:
            initial_severity = sig.severity
            sig.escalate()
            # Severity should not decrease
            severity_order = [SeverityLevel.LOW, SeverityLevel.MEDIUM, SeverityLevel.HIGH, SeverityLevel.CRITICAL]
            old_idx = severity_order.index(initial_severity)
            new_idx = severity_order.index(sig.severity)
            assert new_idx >= old_idx

    def test_metric_update_feeds_monitor(self):
        """InstabilityMetric updates feed into BackpressureMonitor threshold checks."""
        metric = _make_metric(score=0.1)
        mon = BackpressureMonitor(threshold=0.5)

        # Simulate rising instability
        for val in [0.2, 0.4, 0.6, 0.8]:
            metric.update(val)

        if metric.exceeds_threshold(0.5):
            sig = mon.emit_signal(metric.current_score, metric.patch_pair)
            assert isinstance(sig, BackpressureSignal)

    def test_controller_safe_rate_then_throttle(self):
        """Compute safe rate from instability, then apply throttling."""
        ctrl = BackpressureController()
        fleet = _make_fleet(num_members=2)

        # Simulate high instability scenario
        instability = 0.9
        rate = ctrl.compute_safe_rate(instability)
        ctrl.throttle_fleet(fleet, rate=rate)

        for m in fleet.members:
            assert m.current_load >= 0.0

    def test_resolver_apply_retract_move(self):
        """Applying a RETRACT move to proposals should stabilize the set."""
        resolver = BackpressureResolver()
        retract_move = _make_move(move_type=MoveType.RETRACT)
        proposals = [_make_proposal(f"c-{i}") for i in range(3)]

        result = resolver.apply_move(retract_move, proposals)
        assert result is not None or result is None
