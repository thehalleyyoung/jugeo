"""Tests for jugeo.se_theory.debt."""
from __future__ import annotations

import pytest

from jugeo.se_theory.debt.models import (
    DebtAlert,
    DebtKind,
    DebtMetric,
    DebtPrioritization,
    DebtReport,
    DebtThreshold,
    DebtTrend,
)
from jugeo.se_theory.debt.algorithms import (
    DebtAnalyzer,
    DebtGatekeeper,
    DebtPrioritizer,
    DebtTracker,
)


# ===================================================================
# TestDebtAnalyzer
# ===================================================================


class TestDebtAnalyzer:
    """Tests for DebtAnalyzer."""

    def test_compute_obstruction_density(self) -> None:
        analyzer = DebtAnalyzer()
        result = analyzer.compute_obstruction_density(["o1", "o2", "o3"], 10)
        assert result == pytest.approx(0.3)

    def test_compute_obstruction_density_zero_coords(self) -> None:
        analyzer = DebtAnalyzer()
        result = analyzer.compute_obstruction_density(["o1"], 0)
        assert result == 1.0

    def test_compute_trust_floor(self) -> None:
        analyzer = DebtAnalyzer()
        levels = {"c1": "proof", "c2": "claim", "c3": "heuristic"}
        result = analyzer.compute_trust_floor(levels)
        assert result == "claim"

    def test_compute_trust_floor_empty(self) -> None:
        analyzer = DebtAnalyzer()
        result = analyzer.compute_trust_floor({})
        assert result == "claim"

    def test_compute_trust_floor_all_high(self) -> None:
        analyzer = DebtAnalyzer()
        levels = {"c1": "verified", "c2": "proof"}
        result = analyzer.compute_trust_floor(levels)
        assert result == "proof"

    def test_compute_evidence_staleness(self) -> None:
        analyzer = DebtAnalyzer()
        # Evidence was gathered at time 100, code changed at time 200
        evidence_ts = {"c1": 100.0, "c2": 200.0}
        code_ts = {"c1": 200.0, "c2": 200.0}
        result = analyzer.compute_evidence_staleness(evidence_ts, code_ts)
        # c1 is stale by 100 seconds = 100/86400 days
        # c2 is not stale
        assert result > 0.0

    def test_compute_cover_quality(self) -> None:
        analyzer = DebtAnalyzer()
        morphisms = {"A": ["B"], "B": ["A"]}
        coupling, cohesion = analyzer.compute_cover_quality(["A", "B"], morphisms)
        assert 0.0 <= coupling <= 1.0
        assert 0.0 <= cohesion <= 1.0
        assert coupling + cohesion == pytest.approx(1.0)

    def test_compute_repair_backlog(self) -> None:
        analyzer = DebtAnalyzer()
        frontiers = {"obs1": ["c1", "c2"], "obs2": ["c3"]}
        result = analyzer.compute_repair_backlog(frontiers)
        assert result == 3

    def test_full_debt_report(self) -> None:
        analyzer = DebtAnalyzer()
        report = analyzer.full_debt_report(
            obstructions=["o1", "o2"],
            trust_levels={"c1": "proof", "c2": "claim"},
            evidence={"c1": 100.0, "c2": 100.0},
            covers=["c1", "c2"],
            morphisms={"c1": ["c2"], "c2": ["c1"]},
            code_changes={"c1": 200.0, "c2": 200.0},
        )
        assert isinstance(report, DebtReport)
        assert len(report.metrics) >= 3
        assert 0.0 <= report.total_debt_score <= 100.0
        assert report.obstruction_density > 0.0

    def test_debt_by_package(self) -> None:
        analyzer = DebtAnalyzer()
        data = {
            "pkg.a.c1": 0.5,
            "pkg.a.c2": 0.7,
            "pkg.b.c1": 0.3,
        }
        result = analyzer.debt_by_package(data, ["pkg.a", "pkg.b"])
        assert result["pkg.a"] == pytest.approx(0.6)
        assert result["pkg.b"] == pytest.approx(0.3)


# ===================================================================
# TestDebtTracker
# ===================================================================


class TestDebtTracker:
    """Tests for DebtTracker."""

    def _make_report(self, obstruction_value: float) -> DebtReport:
        return DebtReport(
            metrics=[
                DebtMetric(kind=DebtKind.OBSTRUCTION_ACCUMULATION, value=obstruction_value),
            ],
            total_debt_score=obstruction_value * 100,
        )

    def test_record_snapshot(self) -> None:
        tracker = DebtTracker()
        report = self._make_report(0.5)
        tracker.record_snapshot(report)
        assert len(tracker._snapshots) == 1

    def test_compute_trends_insufficient_data(self) -> None:
        tracker = DebtTracker()
        tracker.record_snapshot(self._make_report(0.5))
        trends = tracker.compute_trends()
        # With < 2 data points, slope should be 0
        for trend in trends:
            if trend.metric_kind == DebtKind.OBSTRUCTION_ACCUMULATION:
                assert trend.slope == 0.0

    def test_compute_trends_improving(self) -> None:
        tracker = DebtTracker()
        for val in [1.0, 0.9, 0.8, 0.7, 0.6]:
            tracker.record_snapshot(self._make_report(val))
        trends = tracker.compute_trends()
        for trend in trends:
            if trend.metric_kind == DebtKind.OBSTRUCTION_ACCUMULATION:
                assert trend.slope < 0
                assert trend.is_improving is True

    def test_compute_trends_worsening(self) -> None:
        tracker = DebtTracker()
        for val in [0.1, 0.2, 0.3, 0.4, 0.5]:
            tracker.record_snapshot(self._make_report(val))
        trends = tracker.compute_trends()
        for trend in trends:
            if trend.metric_kind == DebtKind.OBSTRUCTION_ACCUMULATION:
                assert trend.slope > 0
                assert trend.is_improving is False

    def test_projected_value(self) -> None:
        tracker = DebtTracker()
        for val in [1.0, 2.0, 3.0]:
            tracker.record_snapshot(self._make_report(val))
        projected = tracker.projected_value(
            DebtKind.OBSTRUCTION_ACCUMULATION, steps_ahead=2
        )
        # slope=1.0, last_value=3.0, projected = 3.0 + 1.0*2 = 5.0
        assert projected == pytest.approx(5.0)

    def test_is_improving(self) -> None:
        tracker = DebtTracker()
        for val in [1.0, 0.8, 0.6]:
            tracker.record_snapshot(self._make_report(val))
        assert tracker.is_improving(DebtKind.OBSTRUCTION_ACCUMULATION) is True


# ===================================================================
# TestDebtPrioritizer
# ===================================================================


class TestDebtPrioritizer:
    """Tests for DebtPrioritizer."""

    def test_prioritize_repairs_sorted_by_roi(self) -> None:
        prioritizer = DebtPrioritizer()
        obstructions = [
            {"coordinate_id": "c1", "severity": "high"},
            {"coordinate_id": "c2", "severity": "low"},
        ]
        morphisms = {"c1": [], "c2": []}
        complexity = {"c1": 1.0, "c2": 1.0}
        result = prioritizer.prioritize_repairs(obstructions, morphisms, complexity)
        assert result[0].coordinate_id == "c1"
        assert result[0].roi > result[1].roi

    def test_budget_allocation_greedy(self) -> None:
        prioritizer = DebtPrioritizer()
        priorities = [
            DebtPrioritization(coordinate_id="c1", repair_cost=3.0, roi=2.0),
            DebtPrioritization(coordinate_id="c2", repair_cost=5.0, roi=1.5),
            DebtPrioritization(coordinate_id="c3", repair_cost=4.0, roi=1.0),
        ]
        allocated = prioritizer.budget_allocation(7.0, priorities)
        assert "c1" in allocated
        assert allocated["c1"] == 3.0
        assert "c2" in allocated
        assert allocated["c2"] == 4.0  # remaining budget
        assert "c3" not in allocated

    def test_quick_wins(self) -> None:
        prioritizer = DebtPrioritizer()
        priorities = [
            DebtPrioritization(coordinate_id="c1", repair_cost=1.0, roi=5.0),
            DebtPrioritization(coordinate_id="c2", repair_cost=10.0, roi=3.0),
            DebtPrioritization(coordinate_id="c3", repair_cost=2.0, roi=4.0),
        ]
        wins = prioritizer.quick_wins(priorities, max_cost=3.0)
        assert len(wins) == 2
        assert wins[0].coordinate_id == "c1"
        assert wins[1].coordinate_id == "c3"


# ===================================================================
# TestDebtGatekeeper
# ===================================================================


class TestDebtGatekeeper:
    """Tests for DebtGatekeeper."""

    def _make_report_with_metric(self, kind: DebtKind, value: float) -> DebtReport:
        return DebtReport(metrics=[DebtMetric(kind=kind, value=value)])

    def test_check_thresholds_no_breach(self) -> None:
        gatekeeper = DebtGatekeeper()
        report = self._make_report_with_metric(
            DebtKind.OBSTRUCTION_ACCUMULATION, 0.1
        )
        thresholds = [
            DebtThreshold(kind=DebtKind.OBSTRUCTION_ACCUMULATION, warning_level=0.3)
        ]
        alerts = gatekeeper.check_thresholds(report, thresholds)
        assert len(alerts) == 0

    def test_check_thresholds_warning(self) -> None:
        gatekeeper = DebtGatekeeper()
        report = self._make_report_with_metric(
            DebtKind.OBSTRUCTION_ACCUMULATION, 0.4
        )
        thresholds = [
            DebtThreshold(kind=DebtKind.OBSTRUCTION_ACCUMULATION, warning_level=0.3)
        ]
        alerts = gatekeeper.check_thresholds(report, thresholds)
        assert len(alerts) == 1
        assert alerts[0].level == "WARNING"

    def test_check_thresholds_block(self) -> None:
        gatekeeper = DebtGatekeeper()
        report = self._make_report_with_metric(
            DebtKind.OBSTRUCTION_ACCUMULATION, 0.95
        )
        thresholds = [
            DebtThreshold(kind=DebtKind.OBSTRUCTION_ACCUMULATION, block_level=0.9)
        ]
        alerts = gatekeeper.check_thresholds(report, thresholds)
        assert len(alerts) == 1
        assert alerts[0].level == "BLOCK"

    def test_should_block_release_true(self) -> None:
        gatekeeper = DebtGatekeeper()
        alerts = [
            DebtAlert(kind=DebtKind.OBSTRUCTION_ACCUMULATION, level="BLOCK"),
        ]
        assert gatekeeper.should_block_release(alerts) is True

    def test_should_block_release_false(self) -> None:
        gatekeeper = DebtGatekeeper()
        alerts = [
            DebtAlert(kind=DebtKind.OBSTRUCTION_ACCUMULATION, level="WARNING"),
        ]
        assert gatekeeper.should_block_release(alerts) is False

    def test_gate_report_passes(self) -> None:
        gatekeeper = DebtGatekeeper()
        report = self._make_report_with_metric(
            DebtKind.OBSTRUCTION_ACCUMULATION, 0.1
        )
        thresholds = [
            DebtThreshold(kind=DebtKind.OBSTRUCTION_ACCUMULATION, warning_level=0.3)
        ]
        result = gatekeeper.gate_report(report, thresholds)
        assert result["passed"] is True
        assert result["blocked"] is False


# ===================================================================
# TestModels
# ===================================================================


class TestModels:
    """Serialisation round-trip tests for debt models."""

    def test_debt_metric_serialization(self) -> None:
        m = DebtMetric(
            kind=DebtKind.EVIDENCE_STALENESS,
            value=3.5,
            threshold=5.0,
            exceeds_threshold=False,
            coordinate_scope="pkg.a",
            details="Some details",
        )
        d = m.to_dict()
        m2 = DebtMetric.from_dict(d)
        assert m2.kind == DebtKind.EVIDENCE_STALENESS
        assert m2.value == 3.5
        assert m2.to_dict() == d

    def test_debt_report_serialization(self) -> None:
        report = DebtReport(
            site_id="site1",
            metrics=[DebtMetric(kind=DebtKind.OBSTRUCTION_ACCUMULATION, value=0.5)],
            total_debt_score=42.0,
        )
        d = report.to_dict()
        report2 = DebtReport.from_dict(d)
        assert report2.site_id == "site1"
        assert len(report2.metrics) == 1
        assert report2.total_debt_score == 42.0

    def test_debt_trend_serialization(self) -> None:
        trend = DebtTrend(
            metric_kind=DebtKind.TRUST_FLOOR_EROSION,
            timestamps=["t1", "t2"],
            values=[0.5, 0.4],
            slope=-0.1,
            is_improving=True,
        )
        d = trend.to_dict()
        trend2 = DebtTrend.from_dict(d)
        assert trend2.metric_kind == DebtKind.TRUST_FLOOR_EROSION
        assert trend2.is_improving is True
        assert trend2.to_dict() == d

    def test_debt_threshold_serialization(self) -> None:
        t = DebtThreshold(
            kind=DebtKind.REPAIR_BACKLOG,
            warning_level=0.3,
            error_level=0.6,
            block_level=0.9,
            scope="pkg.b",
        )
        d = t.to_dict()
        t2 = DebtThreshold.from_dict(d)
        assert t2.kind == DebtKind.REPAIR_BACKLOG
        assert t2.scope == "pkg.b"
        assert t2.to_dict() == d

    def test_debt_alert_serialization(self) -> None:
        a = DebtAlert(
            kind=DebtKind.BOUNDARY_EROSION,
            level="ERROR",
            current_value=0.7,
            threshold_value=0.6,
            scope="global",
            message="Boundary erosion above threshold",
            suggested_action="Fix boundaries",
        )
        d = a.to_dict()
        a2 = DebtAlert.from_dict(d)
        assert a2.kind == DebtKind.BOUNDARY_EROSION
        assert a2.level == "ERROR"
        assert a2.to_dict() == d

    def test_debt_prioritization_serialization(self) -> None:
        p = DebtPrioritization(
            coordinate_id="c1",
            debt_score=2.5,
            repair_cost=1.5,
            roi=1.67,
            recommended_action="Fix it",
        )
        d = p.to_dict()
        p2 = DebtPrioritization.from_dict(d)
        assert p2.coordinate_id == "c1"
        assert p2.roi == 1.67
        assert p2.to_dict() == d
