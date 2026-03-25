"""Tests for jugeo.se_theory.refactoring."""
from __future__ import annotations

import pytest

from jugeo.se_theory.refactoring.models import (
    MigrationPlan,
    RefactoringKind,
    RefactoringProposal,
    RefactoringResult,
    RefinementRelation,
)
from jugeo.se_theory.refactoring.algorithms import (
    MigrationPlanner,
    RefinementChecker,
    SafetyScorer,
)


# ===================================================================
# TestRefinementChecker
# ===================================================================


class TestRefinementChecker:
    """Tests for RefinementChecker."""

    def test_check_refinement_basic(self) -> None:
        checker = RefinementChecker()
        before = {"c1": {"trust": "claim", "propositions": ["p1"]}}
        after = {"c1": {"trust": "proof", "propositions": ["p1"]}}
        results = checker.check_refinement(before, after, ["p1"])
        assert len(results) == 1
        assert results[0].is_refinement is True
        assert results[0].is_equivalence is False
        assert "CLAIM->PROOF" in results[0].delta_trust

    def test_check_refinement_regression(self) -> None:
        checker = RefinementChecker()
        before = {"c1": {"trust": "proof", "propositions": ["p1"]}}
        after = {"c1": {"trust": "claim", "propositions": ["p1"]}}
        results = checker.check_refinement(before, after, ["p1"])
        assert len(results) == 1
        assert results[0].is_refinement is False

    def test_check_equivalence(self) -> None:
        checker = RefinementChecker()
        before = {"c1": {"trust": "heuristic", "propositions": ["p1"]}}
        after = {"c1": {"trust": "heuristic", "propositions": ["p1"]}}
        results = checker.check_refinement(before, after, ["p1"])
        assert len(results) == 1
        assert results[0].is_refinement is True
        assert results[0].is_equivalence is True
        assert results[0].delta_trust == "unchanged"

    def test_verify_descent_preservation_stable(self) -> None:
        checker = RefinementChecker()
        overlaps = {"o1": ["c1", "c2"], "o2": ["c2", "c3"]}
        assert checker.verify_descent_preservation(overlaps, overlaps) is True

    def test_verify_descent_preservation_broken(self) -> None:
        checker = RefinementChecker()
        before = {"o1": ["c1", "c2"]}
        after = {"o1": ["c1", "c3"]}
        assert checker.verify_descent_preservation(before, after) is False

    def test_find_regressions(self) -> None:
        checker = RefinementChecker()
        before = {
            "c1": {"trust": "proof"},
            "c2": {"trust": "heuristic"},
            "c3": {"trust": "claim"},
        }
        after = {
            "c1": {"trust": "claim"},
            "c2": {"trust": "verified"},
            "c3": {"trust": "claim"},
        }
        regressions = checker.find_regressions(before, after)
        assert regressions == ["c1"]


# ===================================================================
# TestSafetyScorer
# ===================================================================


class TestSafetyScorer:
    """Tests for SafetyScorer."""

    def test_blast_radius_empty(self) -> None:
        scorer = SafetyScorer()
        result = scorer.blast_radius(["A"], {})
        assert result == 1

    def test_blast_radius_chain(self) -> None:
        scorer = SafetyScorer()
        morphisms = {"A": ["B"], "B": ["C"], "C": []}
        result = scorer.blast_radius(["A"], morphisms)
        assert result == 3

    def test_treaty_impact(self) -> None:
        scorer = SafetyScorer()
        treaties = {
            "t1": {"parties": ["A", "B"]},
            "t2": {"parties": ["C", "D"]},
        }
        result = scorer.treaty_impact(["A"], treaties)
        assert result == ["t1"]

    def test_treaty_impact_none(self) -> None:
        scorer = SafetyScorer()
        treaties = {"t1": {"parties": ["X", "Y"]}}
        result = scorer.treaty_impact(["A"], treaties)
        assert result == []

    def test_score_refactoring_safe(self) -> None:
        scorer = SafetyScorer()
        proposal = RefactoringProposal(
            target_coordinates=["A"],
            affected_treaties=[],
        )
        morphisms: dict[str, list[str]] = {}
        evidence = {"A": ["e1", "e2"]}
        score = scorer.score_refactoring(proposal, morphisms, evidence)
        assert score > 0.8

    def test_score_refactoring_risky(self) -> None:
        scorer = SafetyScorer()
        proposal = RefactoringProposal(
            target_coordinates=["A"],
            affected_treaties=["t1", "t2", "t3", "t4"],
        )
        morphisms = {
            "A": ["B", "C", "D", "E", "F"],
            "B": ["G", "H"],
            "C": ["I", "J"],
            "D": [],
            "E": [],
            "F": [],
            "G": [],
            "H": [],
            "I": [],
            "J": [],
        }
        evidence: dict[str, list] = {}
        score = scorer.score_refactoring(proposal, morphisms, evidence)
        assert score < 0.8


# ===================================================================
# TestMigrationPlanner
# ===================================================================


class TestMigrationPlanner:
    """Tests for MigrationPlanner."""

    def test_fuzzy_match_exact(self) -> None:
        planner = MigrationPlanner()
        mapping = planner._fuzzy_match_coordinates(
            ["user_service", "auth_service"],
            ["user_service", "auth_service"],
        )
        assert mapping == {"user_service": "user_service", "auth_service": "auth_service"}

    def test_fuzzy_match_partial(self) -> None:
        planner = MigrationPlanner()
        mapping = planner._fuzzy_match_coordinates(
            ["user_service"],
            ["user_svc", "payment_svc"],
        )
        assert "user_service" in mapping
        assert mapping["user_service"] == "user_svc"

    def test_identify_unmapped(self) -> None:
        planner = MigrationPlanner()
        unmapped = planner._identify_unmapped(
            ["A", "B", "C"],
            {"A": "X", "B": "Y"},
        )
        assert unmapped == ["C"]

    def test_plan_migration(self) -> None:
        planner = MigrationPlanner()
        plan = planner.plan_migration(
            source_coords=["user_service", "auth_service"],
            target_coords=["user_svc", "auth_svc"],
            morphisms={},
        )
        assert isinstance(plan, MigrationPlan)
        assert len(plan.coordinate_mapping) >= 1
        assert plan.compatibility_score > 0.0

    def test_step_by_step_plan(self) -> None:
        planner = MigrationPlanner()
        mapping = {"A": "X", "B": "Y"}
        steps = planner.step_by_step_plan(mapping)
        assert len(steps) == 2
        for step in steps:
            assert step.kind == RefactoringKind.MOVE_TO_MODULE

    def test_verify_migration_preserves_descent(self) -> None:
        planner = MigrationPlanner()
        plan = MigrationPlan()
        before = {"c1": {"trust": "claim"}}
        after = {"c1": {"trust": "proof"}}
        assert planner.verify_migration_preserves_descent(plan, before, after) is True


# ===================================================================
# TestModels
# ===================================================================


class TestModels:
    """Serialisation round-trip tests for refactoring models."""

    def test_refinement_relation_serialization(self) -> None:
        rr = RefinementRelation(
            source_judgment_id="s1",
            target_judgment_id="t1",
            is_refinement=True,
            is_equivalence=False,
            delta_trust="CLAIM->PROOF",
            affected_propositions=["p1", "p2"],
        )
        d = rr.to_dict()
        rr2 = RefinementRelation.from_dict(d)
        assert rr2.source_judgment_id == "s1"
        assert rr2.target_judgment_id == "t1"
        assert rr2.is_refinement is True
        assert rr2.affected_propositions == ["p1", "p2"]
        assert rr2.to_dict() == d

    def test_refactoring_proposal_serialization(self) -> None:
        prop = RefactoringProposal(
            id="test123",
            kind=RefactoringKind.EXTRACT_FUNCTION,
            target_coordinates=["c1", "c2"],
            description="Extract helper",
            blast_radius=3,
            safety_score=0.85,
            affected_overlaps=["o1"],
            affected_treaties=["t1"],
            estimated_verification_cost=2.5,
            preserves_descent=True,
        )
        d = prop.to_dict()
        prop2 = RefactoringProposal.from_dict(d)
        assert prop2.id == "test123"
        assert prop2.kind == RefactoringKind.EXTRACT_FUNCTION
        assert prop2.preserves_descent is True
        assert prop2.to_dict() == d

    def test_migration_plan_serialization(self) -> None:
        step = RefactoringProposal(
            id="step1",
            kind=RefactoringKind.MOVE_TO_MODULE,
            target_coordinates=["A"],
        )
        plan = MigrationPlan(
            id="plan1",
            source_library="lib_old",
            target_library="lib_new",
            coordinate_mapping={"A": "X"},
            morphism_mapping={"m1": "m2"},
            unmapped_coordinates=["B"],
            compatibility_score=0.8,
            steps=[step],
        )
        d = plan.to_dict()
        plan2 = MigrationPlan.from_dict(d)
        assert plan2.id == "plan1"
        assert plan2.source_library == "lib_old"
        assert len(plan2.steps) == 1
        assert plan2.steps[0].id == "step1"
        assert plan2.to_dict() == d

    def test_refactoring_result_serialization(self) -> None:
        result = RefactoringResult(
            proposal_id="p1",
            applied=True,
            descent_preserved=True,
            regressions=["c1"],
            new_trust_levels={"c2": "proof"},
            verification_duration_ms=42.5,
        )
        d = result.to_dict()
        result2 = RefactoringResult.from_dict(d)
        assert result2.proposal_id == "p1"
        assert result2.applied is True
        assert result2.regressions == ["c1"]
        assert result2.to_dict() == d
