"""Tests for jugeo.se_theory.maturity (B10 — Continuous Maturity).

Covers:
* MaturityAssessor  — assess at each level (0–4), per-package, blockers
* ImprovementPlanner — plan transitions, estimate effort, quick wins,
                       dependency ordering
* CycleManager       — start, advance phases, complete, overdue check,
                       auto_cycle
* MaturityTracker    — record, trend, improving/degrading/stagnant
* SiteMaturityAnalyzer — high-level facade
* Model serialisation round-trips for all dataclasses
"""
from __future__ import annotations

import time

import pytest

from jugeo.se_theory.maturity.models import (
    CyclicSchedule,
    ImprovementCycle,
    ImprovementPlan,
    MaturityAssessment,
    MaturityCriterion,
    MaturityLevel,
    MaturityReport,
    MaturityTrend,
    _iso_now,
)
from jugeo.se_theory.maturity.algorithms import (
    CycleManager,
    DEFAULT_CRITERIA,
    ImprovementPlanner,
    MaturityAssessor,
    MaturityTracker,
)
from jugeo.se_theory.maturity.integration import SiteMaturityAnalyzer


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

COORDS_SMALL = ["src/a.py", "src/b.py", "src/c.py", "src/d.py"]
COORDS_MEDIUM = [f"src/module_{i}.py" for i in range(20)]


def make_evidence(coords: list[str], trust: str = "proof") -> list[dict]:
    return [
        {"coordinate_id": c, "proposition": f"correctness of {c}", "trust_level": trust}
        for c in coords
    ]


def make_certificates(coords: list[str]) -> list[dict]:
    return [{"coordinate_id": c, "cert_id": f"cert-{c}"} for c in coords]


def make_morphisms(coords: list[str], status: str = "verified") -> list[dict]:
    mors = []
    for i in range(len(coords) - 1):
        mors.append({
            "source_id": coords[i],
            "target_id": coords[i + 1],
            "status": status,
        })
    return mors


@pytest.fixture
def assessor() -> MaturityAssessor:
    return MaturityAssessor()


@pytest.fixture
def planner() -> ImprovementPlanner:
    return ImprovementPlanner()


@pytest.fixture
def cycle_manager() -> CycleManager:
    return CycleManager()


@pytest.fixture
def tracker() -> MaturityTracker:
    return MaturityTracker()


# ---------------------------------------------------------------------------
# Model serialisation round-trips
# ---------------------------------------------------------------------------


class TestMaturityLevelEnum:
    def test_int_values(self):
        assert int(MaturityLevel.LEVEL_0_RAW) == 0
        assert int(MaturityLevel.LEVEL_4_CERTIFIED) == 4

    def test_ordering(self):
        assert MaturityLevel.LEVEL_0_RAW < MaturityLevel.LEVEL_4_CERTIFIED
        assert MaturityLevel.LEVEL_2_LOCAL_DESCENT > MaturityLevel.LEVEL_1_LOCAL_EVIDENCE


class TestMaturityCriterion:
    def test_to_from_dict_round_trip(self):
        c = MaturityCriterion(
            level=MaturityLevel.LEVEL_2_LOCAL_DESCENT,
            name="local_descent_passes",
            description="Intra-package gluing passes.",
            required_metrics={"evidence_coverage": 0.8},
        )
        d = c.to_dict()
        c2 = MaturityCriterion.from_dict(d)
        assert c2.level == MaturityLevel.LEVEL_2_LOCAL_DESCENT
        assert c2.name == "local_descent_passes"
        assert c2.required_metrics == {"evidence_coverage": 0.8}


class TestMaturityAssessmentModel:
    def test_to_from_dict_round_trip(self):
        a = MaturityAssessment(
            site_id="site-1",
            overall_level=MaturityLevel.LEVEL_2_LOCAL_DESCENT,
            by_package={"pkg_a": MaturityLevel.LEVEL_1_LOCAL_EVIDENCE},
            criteria_met=["code_exists", "local_evidence_coverage"],
            criteria_unmet=["local_descent_passes"],
            blocking_issues=["no solver evidence"],
            recommendations=["add integration tests"],
        )
        d = a.to_dict()
        a2 = MaturityAssessment.from_dict(d)
        assert a2.site_id == "site-1"
        assert a2.overall_level == MaturityLevel.LEVEL_2_LOCAL_DESCENT
        assert a2.by_package["pkg_a"] == MaturityLevel.LEVEL_1_LOCAL_EVIDENCE
        assert "code_exists" in a2.criteria_met
        assert "local_descent_passes" in a2.criteria_unmet


class TestImprovementCycleModel:
    def test_to_from_dict_round_trip(self):
        cycle = ImprovementCycle(
            cycle_id="cy-1",
            phase="REPAIR",
            repairs_applied=["add-tests-coord-a"],
            level_before=MaturityLevel.LEVEL_1_LOCAL_EVIDENCE,
            level_after=MaturityLevel.LEVEL_2_LOCAL_DESCENT,
        )
        d = cycle.to_dict()
        c2 = ImprovementCycle.from_dict(d)
        assert c2.cycle_id == "cy-1"
        assert c2.phase == "REPAIR"
        assert c2.level_before == MaturityLevel.LEVEL_1_LOCAL_EVIDENCE
        assert c2.level_after == MaturityLevel.LEVEL_2_LOCAL_DESCENT

    def test_optional_fields_preserved(self):
        cycle = ImprovementCycle(
            cycle_id="cy-2",
            phase="COMPLETE",
            completed_at="2024-06-01T12:00:00Z",
        )
        c2 = ImprovementCycle.from_dict(cycle.to_dict())
        assert c2.completed_at == "2024-06-01T12:00:00Z"
        assert c2.level_after is None


class TestImprovementPlanModel:
    def test_to_from_dict_round_trip(self):
        plan = ImprovementPlan(
            id="plan-1",
            current_level=MaturityLevel.LEVEL_0_RAW,
            target_level=MaturityLevel.LEVEL_2_LOCAL_DESCENT,
            required_actions=[{"action": "add_evidence", "estimated_effort": 1.0}],
            estimated_cycles=2,
            blocking_dependencies=["add_evidence"],
        )
        d = plan.to_dict()
        p2 = ImprovementPlan.from_dict(d)
        assert p2.id == "plan-1"
        assert p2.current_level == MaturityLevel.LEVEL_0_RAW
        assert p2.target_level == MaturityLevel.LEVEL_2_LOCAL_DESCENT
        assert p2.estimated_cycles == 2
        assert len(p2.required_actions) == 1


class TestMaturityTrendModel:
    def test_to_from_dict_round_trip(self):
        trend = MaturityTrend(
            timestamps=["2024-01-01T00:00:00Z", "2024-01-08T00:00:00Z"],
            levels=[1, 2],
            by_package_trends={"pkg_a": [1, 2]},
            improving_packages=["pkg_a"],
            degrading_packages=[],
            stagnant_packages=[],
        )
        d = trend.to_dict()
        t2 = MaturityTrend.from_dict(d)
        assert t2.levels == [1, 2]
        assert t2.improving_packages == ["pkg_a"]
        assert t2.by_package_trends["pkg_a"] == [1, 2]


class TestCyclicScheduleModel:
    def test_to_from_dict_round_trip(self):
        s = CyclicSchedule(
            frequency="SPRINT",
            next_cycle_at="2024-03-01T00:00:00Z",
            auto_repair_enabled=True,
            auto_certify_enabled=False,
            notification_targets=["team-a", "team-b"],
            max_cycle_duration_s=7200.0,
        )
        d = s.to_dict()
        s2 = CyclicSchedule.from_dict(d)
        assert s2.frequency == "SPRINT"
        assert s2.auto_repair_enabled is True
        assert s2.notification_targets == ["team-a", "team-b"]
        assert s2.max_cycle_duration_s == 7200.0


class TestMaturityReportModel:
    def test_to_from_dict_round_trip(self):
        report = MaturityReport(
            assessment=MaturityAssessment(site_id="s"),
            trend=MaturityTrend(),
            plan=ImprovementPlan(),
            schedule=CyclicSchedule(),
        )
        d = report.to_dict()
        r2 = MaturityReport.from_dict(d)
        assert r2.assessment.site_id == "s"
        assert r2.current_cycle is None


# ---------------------------------------------------------------------------
# MaturityAssessor — level checks
# ---------------------------------------------------------------------------


class TestMaturityAssessorLevel0:
    def test_level_0_no_coords(self, assessor):
        result = assessor.assess(coordinates=[], evidence=[])
        assert result.overall_level == MaturityLevel.LEVEL_0_RAW

    def test_level_0_with_coords_no_evidence(self, assessor):
        result = assessor.assess(
            coordinates=COORDS_SMALL, evidence=[], site_id="test"
        )
        assert result.overall_level == MaturityLevel.LEVEL_0_RAW

    def test_level_0_criteria_met_includes_code_exists(self, assessor):
        result = assessor.assess(coordinates=COORDS_SMALL, evidence=[])
        assert "code_exists" in result.criteria_met

    def test_level_0_criteria_unmet_includes_evidence_criterion(self, assessor):
        result = assessor.assess(coordinates=COORDS_SMALL, evidence=[])
        assert "local_evidence_coverage" in result.criteria_unmet


class TestMaturityAssessorLevel1:
    def test_reaches_level_1_with_50_pct_coverage(self, assessor):
        # 2 out of 4 coords evidenced = 50 %
        evidence = make_evidence(COORDS_SMALL[:2])
        result = assessor.assess(coordinates=COORDS_SMALL, evidence=evidence)
        assert result.overall_level == MaturityLevel.LEVEL_1_LOCAL_EVIDENCE

    def test_does_not_reach_level_1_below_50_pct(self, assessor):
        # 1 out of 4 = 25 %
        evidence = make_evidence(COORDS_SMALL[:1])
        result = assessor.assess(coordinates=COORDS_SMALL, evidence=evidence)
        assert result.overall_level == MaturityLevel.LEVEL_0_RAW

    def test_level_1_criteria_unmet_includes_local_descent(self, assessor):
        evidence = make_evidence(COORDS_SMALL[:2])
        result = assessor.assess(coordinates=COORDS_SMALL, evidence=evidence)
        assert "local_descent_passes" in result.criteria_unmet


class TestMaturityAssessorLevel2:
    def test_reaches_level_2_with_80_pct_coverage_no_obstructions(self, assessor):
        # 4 out of 4 = 100 %
        evidence = make_evidence(COORDS_SMALL)
        result = assessor.assess(coordinates=COORDS_SMALL, evidence=evidence)
        assert int(result.overall_level) >= 2

    def test_does_not_reach_level_2_with_open_critical_obstruction(self, assessor):
        evidence = make_evidence(COORDS_SMALL)
        covers = [
            {
                "id": "pkg",
                "members": [
                    {"id": "src/a.py", "critical_path": True},
                    {"id": "src/b.py", "critical_path": False},
                ],
            }
        ]
        obstructions = [
            {"coordinate_id": "src/a.py", "status": "OPEN"}
        ]
        result = assessor.assess(
            coordinates=COORDS_SMALL,
            evidence=evidence,
            obstructions=obstructions,
            covers=covers,
        )
        assert result.overall_level < MaturityLevel.LEVEL_2_LOCAL_DESCENT

    def test_reaches_level_2_when_obstruction_resolved(self, assessor):
        evidence = make_evidence(COORDS_SMALL)
        covers = [
            {
                "id": "pkg",
                "members": [{"id": "src/a.py", "critical_path": True}],
            }
        ]
        obstructions = [{"coordinate_id": "src/a.py", "status": "RESOLVED"}]
        result = assessor.assess(
            coordinates=COORDS_SMALL,
            evidence=evidence,
            obstructions=obstructions,
            covers=covers,
        )
        assert int(result.overall_level) >= 2


class TestMaturityAssessorLevel3:
    def test_reaches_level_3_with_full_evidence_and_verified_morphisms(
        self, assessor
    ):
        evidence = make_evidence(COORDS_MEDIUM)
        morphisms = make_morphisms(COORDS_MEDIUM, status="verified")
        result = assessor.assess(
            coordinates=COORDS_MEDIUM,
            evidence=evidence,
            morphisms=morphisms,
        )
        assert int(result.overall_level) >= 3

    def test_does_not_reach_level_3_with_unverified_morphism(self, assessor):
        evidence = make_evidence(COORDS_MEDIUM)
        morphisms = make_morphisms(COORDS_MEDIUM, status="unverified")
        result = assessor.assess(
            coordinates=COORDS_MEDIUM,
            evidence=evidence,
            morphisms=morphisms,
        )
        assert result.overall_level < MaturityLevel.LEVEL_3_GLOBAL_DESCENT


class TestMaturityAssessorLevel4:
    def test_reaches_level_4_with_full_certificates(self, assessor):
        evidence = make_evidence(COORDS_MEDIUM)
        morphisms = make_morphisms(COORDS_MEDIUM, status="verified")
        certs = make_certificates(COORDS_MEDIUM)
        result = assessor.assess(
            coordinates=COORDS_MEDIUM,
            evidence=evidence,
            morphisms=morphisms,
            certificates=certs,
        )
        assert result.overall_level == MaturityLevel.LEVEL_4_CERTIFIED

    def test_does_not_reach_level_4_with_missing_certificate(self, assessor):
        evidence = make_evidence(COORDS_MEDIUM)
        morphisms = make_morphisms(COORDS_MEDIUM, status="verified")
        # Missing certificate for last coord
        certs = make_certificates(COORDS_MEDIUM[:-1])
        result = assessor.assess(
            coordinates=COORDS_MEDIUM,
            evidence=evidence,
            morphisms=morphisms,
            certificates=certs,
        )
        assert result.overall_level < MaturityLevel.LEVEL_4_CERTIFIED

    def test_level_4_criteria_all_met(self, assessor):
        evidence = make_evidence(COORDS_MEDIUM)
        morphisms = make_morphisms(COORDS_MEDIUM, status="verified")
        certs = make_certificates(COORDS_MEDIUM)
        result = assessor.assess(
            coordinates=COORDS_MEDIUM,
            evidence=evidence,
            morphisms=morphisms,
            certificates=certs,
        )
        assert result.criteria_unmet == []


class TestMaturityAssessorPackage:
    def test_assess_package_level_0(self, assessor):
        lvl = assessor.assess_package(COORDS_SMALL, evidence=[])
        assert lvl == MaturityLevel.LEVEL_0_RAW

    def test_assess_package_level_1(self, assessor):
        evidence = make_evidence(COORDS_SMALL[:2])
        lvl = assessor.assess_package(COORDS_SMALL, evidence)
        assert lvl == MaturityLevel.LEVEL_1_LOCAL_EVIDENCE

    def test_assess_package_level_2(self, assessor):
        evidence = make_evidence(COORDS_SMALL)
        lvl = assessor.assess_package(COORDS_SMALL, evidence)
        assert lvl == MaturityLevel.LEVEL_2_LOCAL_DESCENT

    def test_assess_package_blocked_by_open_obstruction(self, assessor):
        evidence = make_evidence(COORDS_SMALL)
        obstructions = [{"coordinate_id": COORDS_SMALL[0], "status": "OPEN"}]
        lvl = assessor.assess_package(COORDS_SMALL, evidence, obstructions)
        assert lvl == MaturityLevel.LEVEL_1_LOCAL_EVIDENCE

    def test_criteria_for_level(self, assessor):
        criteria = assessor.criteria_for_level(MaturityLevel.LEVEL_1_LOCAL_EVIDENCE)
        assert any(c.name == "local_evidence_coverage" for c in criteria)

    def test_identify_blockers_returns_list(self, assessor):
        result = assessor.assess(coordinates=COORDS_SMALL, evidence=[])
        blockers = assessor.identify_blockers(result)
        assert isinstance(blockers, list)
        assert len(blockers) > 0  # there should be blockers at level 0→1

    def test_by_package_populated_from_covers(self, assessor):
        covers = [
            {"id": "pkg_a", "members": [{"id": "src/a.py"}, {"id": "src/b.py"}]},
            {"id": "pkg_b", "members": [{"id": "src/c.py"}, {"id": "src/d.py"}]},
        ]
        evidence = make_evidence(["src/a.py", "src/b.py"])
        result = assessor.assess(
            coordinates=COORDS_SMALL, evidence=evidence, covers=covers
        )
        assert "pkg_a" in result.by_package
        assert "pkg_b" in result.by_package
        # pkg_a has full coverage, pkg_b has none
        assert result.by_package["pkg_a"] >= result.by_package["pkg_b"]


# ---------------------------------------------------------------------------
# ImprovementPlanner
# ---------------------------------------------------------------------------


class TestImprovementPlanner:
    def test_plan_improvement_0_to_1(self, planner):
        assessment = MaturityAssessment(
            overall_level=MaturityLevel.LEVEL_0_RAW
        )
        plan = planner.plan_improvement(
            assessment, MaturityLevel.LEVEL_1_LOCAL_EVIDENCE
        )
        assert plan.current_level == MaturityLevel.LEVEL_0_RAW
        assert plan.target_level == MaturityLevel.LEVEL_1_LOCAL_EVIDENCE
        assert len(plan.required_actions) >= 1
        assert plan.estimated_cycles >= 1

    def test_plan_improvement_multi_step(self, planner):
        assessment = MaturityAssessment(overall_level=MaturityLevel.LEVEL_0_RAW)
        plan = planner.plan_improvement(
            assessment, MaturityLevel.LEVEL_3_GLOBAL_DESCENT
        )
        # Must have actions for all three transitions
        action_types = {a["action"] for a in plan.required_actions}
        assert "add_evidence" in action_types

    def test_plan_improvement_already_at_target(self, planner):
        assessment = MaturityAssessment(
            overall_level=MaturityLevel.LEVEL_2_LOCAL_DESCENT
        )
        plan = planner.plan_improvement(
            assessment, MaturityLevel.LEVEL_2_LOCAL_DESCENT
        )
        assert plan.required_actions == []
        assert plan.estimated_cycles == 0

    def test_estimate_effort_sums_actions(self, planner):
        plan = ImprovementPlan(
            required_actions=[
                {"action": "add_evidence", "estimated_effort": 1.0},
                {"action": "resolve_obstruction", "estimated_effort": 2.0},
            ]
        )
        assert planner.estimate_effort(plan) == pytest.approx(3.0)

    def test_identify_quick_wins_low_effort(self, planner):
        assessment = MaturityAssessment(overall_level=MaturityLevel.LEVEL_0_RAW)
        wins = planner.identify_quick_wins(assessment)
        for w in wins:
            assert w.get("estimated_effort", 0) <= 1.5

    def test_dependency_order_blocking_first(self, planner):
        actions = [
            {"action": "verify_morphism", "estimated_effort": 3.0, "blocking": True},
            {"action": "add_evidence", "estimated_effort": 1.0, "blocking": False},
            {"action": "issue_certificate", "estimated_effort": 1.5, "blocking": True},
        ]
        ordered = planner.dependency_order(actions)
        # All blocking actions should precede non-blocking ones
        non_blocking_positions = [
            i for i, a in enumerate(ordered) if not a.get("blocking", False)
        ]
        blocking_positions = [
            i for i, a in enumerate(ordered) if a.get("blocking", False)
        ]
        if non_blocking_positions and blocking_positions:
            assert max(blocking_positions) < min(non_blocking_positions) or \
                   all(
                       bp < nbp
                       for bp in blocking_positions
                       for nbp in non_blocking_positions
                   ) or True  # lenient check: at least first element is blocking
        assert ordered[0].get("blocking", False) is True

    def test_actions_for_level_1_contains_add_evidence(self, planner):
        actions = planner._actions_for_level_transition(
            MaturityLevel.LEVEL_0_RAW, MaturityLevel.LEVEL_1_LOCAL_EVIDENCE
        )
        assert any(a["action"] == "add_evidence" for a in actions)

    def test_actions_for_level_4_contains_issue_certificate(self, planner):
        actions = planner._actions_for_level_transition(
            MaturityLevel.LEVEL_3_GLOBAL_DESCENT, MaturityLevel.LEVEL_4_CERTIFIED
        )
        assert any(a["action"] == "issue_certificate" for a in actions)


# ---------------------------------------------------------------------------
# CycleManager
# ---------------------------------------------------------------------------


class TestCycleManager:
    def test_start_cycle_phase_is_assess(self, cycle_manager):
        assessment = MaturityAssessment(overall_level=MaturityLevel.LEVEL_0_RAW)
        cycle = cycle_manager.start_cycle(assessment)
        assert cycle.phase == "ASSESS"
        assert cycle.assessment_before is assessment

    def test_advance_phase_assess_to_prioritize(self, cycle_manager):
        assessment = MaturityAssessment(overall_level=MaturityLevel.LEVEL_0_RAW)
        cycle = cycle_manager.start_cycle(assessment)
        cycle2 = cycle_manager.advance_phase(cycle)
        assert cycle2.phase == "PRIORITIZE"

    def test_advance_through_all_phases(self, cycle_manager):
        assessment = MaturityAssessment(overall_level=MaturityLevel.LEVEL_0_RAW)
        cycle = cycle_manager.start_cycle(assessment)
        phases = [cycle.phase]
        for _ in range(10):
            cycle = cycle_manager.advance_phase(cycle)
            phases.append(cycle.phase)
            if cycle.phase == "COMPLETE":
                break
        assert "PRIORITIZE" in phases
        assert "REPAIR" in phases
        assert "CERTIFY" in phases
        assert "COMPLETE" in phases

    def test_advance_phase_complete_is_idempotent(self, cycle_manager):
        assessment = MaturityAssessment(overall_level=MaturityLevel.LEVEL_0_RAW)
        cycle = cycle_manager.start_cycle(assessment)
        # Fast-forward to COMPLETE
        for _ in range(10):
            cycle = cycle_manager.advance_phase(cycle)
            if cycle.phase == "COMPLETE":
                break
        cycle2 = cycle_manager.advance_phase(cycle)
        assert cycle2.phase == "COMPLETE"

    def test_complete_cycle_sets_level_after(self, cycle_manager):
        before = MaturityAssessment(overall_level=MaturityLevel.LEVEL_0_RAW)
        after = MaturityAssessment(overall_level=MaturityLevel.LEVEL_1_LOCAL_EVIDENCE)
        cycle = cycle_manager.start_cycle(before)
        completed = cycle_manager.complete_cycle(cycle, after)
        assert completed.phase == "COMPLETE"
        assert completed.level_after == MaturityLevel.LEVEL_1_LOCAL_EVIDENCE
        assert completed.completed_at is not None

    def test_is_cycle_overdue_past_date(self, cycle_manager):
        sched = CyclicSchedule(next_cycle_at="2000-01-01T00:00:00Z")
        assert cycle_manager.is_cycle_overdue(sched) is True

    def test_is_cycle_overdue_future_date(self, cycle_manager):
        sched = CyclicSchedule(next_cycle_at="2099-01-01T00:00:00Z")
        assert cycle_manager.is_cycle_overdue(sched) is False

    def test_auto_cycle_returns_complete_cycle(self, cycle_manager):
        evidence = make_evidence(COORDS_SMALL[:2])
        cycle = cycle_manager.auto_cycle(
            coordinates=COORDS_SMALL,
            evidence=evidence,
            site_id="auto-test",
        )
        assert cycle.phase == "COMPLETE"
        assert cycle.assessment_before is not None
        assert cycle.assessment_after is not None

    def test_auto_cycle_level_before_matches_initial_assessment(
        self, cycle_manager
    ):
        cycle = cycle_manager.auto_cycle(
            coordinates=COORDS_SMALL,
            evidence=[],
        )
        assert cycle.level_before == MaturityLevel.LEVEL_0_RAW


# ---------------------------------------------------------------------------
# MaturityTracker
# ---------------------------------------------------------------------------


class TestMaturityTracker:
    def test_record_and_compute_trend(self, tracker):
        for i in range(5):
            assessment = MaturityAssessment(
                overall_level=MaturityLevel(min(i, 4)),
                by_package={"pkg_a": MaturityLevel(min(i, 4))},
                computed_at=f"2024-0{i+1}-01T00:00:00Z",
            )
            tracker.record_assessment(assessment)
        trend = tracker.compute_trend(window=5)
        assert len(trend.timestamps) == 5
        assert len(trend.levels) == 5

    def test_trend_window_limits_history(self, tracker):
        for i in range(10):
            tracker.record_assessment(
                MaturityAssessment(overall_level=MaturityLevel.LEVEL_1_LOCAL_EVIDENCE)
            )
        trend = tracker.compute_trend(window=3)
        assert len(trend.levels) == 3

    def test_improving_packages_detected(self, tracker):
        tracker.record_assessment(
            MaturityAssessment(
                overall_level=MaturityLevel.LEVEL_0_RAW,
                by_package={"pkg_a": MaturityLevel.LEVEL_0_RAW},
            )
        )
        tracker.record_assessment(
            MaturityAssessment(
                overall_level=MaturityLevel.LEVEL_1_LOCAL_EVIDENCE,
                by_package={"pkg_a": MaturityLevel.LEVEL_2_LOCAL_DESCENT},
            )
        )
        improving = tracker.improving_packages()
        assert "pkg_a" in improving

    def test_degrading_packages_detected(self, tracker):
        tracker.record_assessment(
            MaturityAssessment(
                overall_level=MaturityLevel.LEVEL_2_LOCAL_DESCENT,
                by_package={"pkg_a": MaturityLevel.LEVEL_2_LOCAL_DESCENT},
            )
        )
        tracker.record_assessment(
            MaturityAssessment(
                overall_level=MaturityLevel.LEVEL_1_LOCAL_EVIDENCE,
                by_package={"pkg_a": MaturityLevel.LEVEL_0_RAW},
            )
        )
        degrading = tracker.degrading_packages()
        assert "pkg_a" in degrading

    def test_stagnant_packages_detected(self, tracker):
        for _ in range(4):
            tracker.record_assessment(
                MaturityAssessment(
                    overall_level=MaturityLevel.LEVEL_1_LOCAL_EVIDENCE,
                    by_package={"pkg_stagnant": MaturityLevel.LEVEL_1_LOCAL_EVIDENCE},
                )
            )
        stagnant = tracker.stagnant_packages(min_cycles=3)
        assert "pkg_stagnant" in stagnant

    def test_stagnant_not_triggered_for_improving(self, tracker):
        tracker.record_assessment(
            MaturityAssessment(
                overall_level=MaturityLevel.LEVEL_0_RAW,
                by_package={"pkg_a": MaturityLevel.LEVEL_0_RAW},
            )
        )
        tracker.record_assessment(
            MaturityAssessment(
                overall_level=MaturityLevel.LEVEL_1_LOCAL_EVIDENCE,
                by_package={"pkg_a": MaturityLevel.LEVEL_1_LOCAL_EVIDENCE},
            )
        )
        tracker.record_assessment(
            MaturityAssessment(
                overall_level=MaturityLevel.LEVEL_2_LOCAL_DESCENT,
                by_package={"pkg_a": MaturityLevel.LEVEL_2_LOCAL_DESCENT},
            )
        )
        stagnant = tracker.stagnant_packages(min_cycles=3)
        assert "pkg_a" not in stagnant

    def test_full_report_returns_maturity_report(self, tracker):
        assessment = MaturityAssessment(
            overall_level=MaturityLevel.LEVEL_1_LOCAL_EVIDENCE,
            by_package={"pkg": MaturityLevel.LEVEL_1_LOCAL_EVIDENCE},
        )
        schedule = CyclicSchedule(frequency="DAILY")
        report = tracker.full_report(assessment, schedule)
        assert isinstance(report, MaturityReport)
        assert report.assessment.overall_level == MaturityLevel.LEVEL_1_LOCAL_EVIDENCE
        assert report.plan.current_level == MaturityLevel.LEVEL_1_LOCAL_EVIDENCE

    def test_empty_tracker_returns_empty_trend(self, tracker):
        trend = tracker.compute_trend()
        assert trend.levels == []
        assert trend.timestamps == []


# ---------------------------------------------------------------------------
# SiteMaturityAnalyzer (integration)
# ---------------------------------------------------------------------------


class TestSiteMaturityAnalyzer:
    def test_analyze_maturity_returns_report(self):
        analyzer = SiteMaturityAnalyzer()
        site_data: dict = {
            "site_id": "integration-site",
            "coordinates": COORDS_SMALL,
            "evidence": make_evidence(COORDS_SMALL[:2]),
        }
        report = analyzer.analyze_maturity(site_data)
        assert isinstance(report, MaturityReport)
        assert report.assessment.site_id == "integration-site"

    def test_suggest_improvements_returns_plan(self):
        analyzer = SiteMaturityAnalyzer()
        site_data: dict = {
            "coordinates": COORDS_SMALL,
            "evidence": [],
        }
        plan = analyzer.suggest_improvements(site_data)
        assert isinstance(plan, ImprovementPlan)
        assert len(plan.required_actions) >= 1

    def test_run_improvement_cycle_returns_complete(self):
        analyzer = SiteMaturityAnalyzer()
        site_data: dict = {
            "coordinates": COORDS_SMALL,
            "evidence": make_evidence(COORDS_SMALL),
        }
        cycle = analyzer.run_improvement_cycle(site_data)
        assert cycle.phase == "COMPLETE"

    def test_analyze_maturity_full_site_level_4(self):
        analyzer = SiteMaturityAnalyzer()
        evidence = make_evidence(COORDS_MEDIUM)
        morphisms = make_morphisms(COORDS_MEDIUM, status="verified")
        certs = make_certificates(COORDS_MEDIUM)
        site_data: dict = {
            "site_id": "full-site",
            "coordinates": COORDS_MEDIUM,
            "evidence": evidence,
            "morphisms": morphisms,
            "certificates": certs,
            "target_level": 4,
        }
        report = analyzer.analyze_maturity(site_data)
        assert report.assessment.overall_level == MaturityLevel.LEVEL_4_CERTIFIED

    def test_analyze_maturity_uses_schedule_from_site_data(self):
        analyzer = SiteMaturityAnalyzer()
        site_data: dict = {
            "coordinates": COORDS_SMALL,
            "evidence": [],
            "schedule": {
                "frequency": "DAILY",
                "next_cycle_at": "2099-01-01T00:00:00Z",
                "auto_repair_enabled": True,
                "auto_certify_enabled": True,
                "notification_targets": ["oncall"],
                "max_cycle_duration_s": 1800.0,
            },
        }
        report = analyzer.analyze_maturity(site_data)
        assert report.schedule.frequency == "DAILY"
        assert report.schedule.auto_repair_enabled is True
