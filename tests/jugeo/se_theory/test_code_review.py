"""Tests for jugeo.se_theory.code_review."""
from __future__ import annotations

import pytest

from jugeo.se_theory.code_review.models import (
    ReviewCheck,
    ReviewFinding,
    ReviewScope,
    ReviewVerdict,
    TreatyImpact,
)
from jugeo.se_theory.code_review.algorithms import (
    AutoReviewer,
    ReviewScopeAnalyzer,
    SectionCompatibilityChecker,
)


# ===================================================================
# TestReviewScopeAnalyzer
# ===================================================================


class TestReviewScopeAnalyzer:
    """Tests for ReviewScopeAnalyzer."""

    def test_compute_scope_basic(self) -> None:
        analyzer = ReviewScopeAnalyzer()
        morphisms = {"A": ["B"], "B": []}
        treaties: dict = {}
        teams = {"team1": ["A", "B"]}
        scope = analyzer.compute_scope(["A"], morphisms, treaties, teams)
        assert "A" in scope.changed_coordinates
        assert len(scope.affected_teams) >= 1

    def test_find_affected_overlaps(self) -> None:
        analyzer = ReviewScopeAnalyzer()
        overlaps = {"o1": ["A", "B"], "o2": ["C", "D"]}
        result = analyzer._find_affected_overlaps(["A"], overlaps)
        assert "o1" in result
        assert "o2" not in result

    def test_find_affected_treaties(self) -> None:
        analyzer = ReviewScopeAnalyzer()
        treaties = {
            "t1": {"overlaps": ["o1", "o2"]},
            "t2": {"overlaps": ["o3"]},
        }
        result = analyzer._find_affected_treaties(["o1"], treaties)
        assert "t1" in result
        assert "t2" not in result

    def test_find_affected_teams(self) -> None:
        analyzer = ReviewScopeAnalyzer()
        teams = {
            "backend": ["A", "B"],
            "frontend": ["C", "D"],
        }
        result = analyzer._find_affected_teams(["A"], teams)
        assert result == ["backend"]

    def test_detect_trust_changes(self) -> None:
        analyzer = ReviewScopeAnalyzer()
        before = {"c1": {"trust": "claim"}, "c2": {"trust": "proof"}}
        after = {"c1": {"trust": "proof"}, "c2": {"trust": "proof"}}
        result = analyzer._detect_trust_changes(["c1", "c2"], before, after)
        assert "c1" in result
        assert result["c1"] == ("claim", "proof")
        assert "c2" not in result

    def test_detect_public_changes(self) -> None:
        analyzer = ReviewScopeAnalyzer()
        before = {"c1": {"public": False}, "c2": {"public": True}}
        after = {"c1": {"public": True}, "c2": {"public": True}}
        result = analyzer._detect_public_changes(["c1", "c2"], before, after)
        assert "c1" in result
        assert "c2" not in result


# ===================================================================
# TestSectionCompatibilityChecker
# ===================================================================


class TestSectionCompatibilityChecker:
    """Tests for SectionCompatibilityChecker."""

    def test_check_internal_consistency_valid(self) -> None:
        checker = SectionCompatibilityChecker()
        sections = {"c1": {"trust": "proof", "propositions": ["p1"]}}
        findings = checker.check_internal_consistency(sections, ["p1"])
        assert len(findings) == 0

    def test_check_internal_consistency_unknown_prop(self) -> None:
        checker = SectionCompatibilityChecker()
        sections = {"c1": {"trust": "proof", "propositions": ["unknown_prop"]}}
        findings = checker.check_internal_consistency(sections, ["p1"])
        assert len(findings) >= 1
        assert findings[0].severity == "error"

    def test_check_internal_consistency_claim_no_props(self) -> None:
        checker = SectionCompatibilityChecker()
        sections = {"c1": {"trust": "claim", "propositions": []}}
        findings = checker.check_internal_consistency(sections, ["p1"])
        assert len(findings) >= 1
        assert findings[0].severity == "warning"

    def test_check_overlap_compatibility_agree(self) -> None:
        checker = SectionCompatibilityChecker()
        sections = {
            "c1": {"trust": "proof", "propositions": ["p1"]},
            "c2": {"trust": "proof", "propositions": ["p1"]},
        }
        overlaps = {"o1": ["c1", "c2"]}
        findings = checker.check_overlap_compatibility(sections, {}, overlaps)
        assert len(findings) == 0

    def test_check_overlap_compatibility_disagree(self) -> None:
        checker = SectionCompatibilityChecker()
        sections = {
            "c1": {"trust": "proof", "propositions": ["p1"]},
            "c2": {"trust": "claim", "propositions": ["p2"]},
        }
        overlaps = {"o1": ["c1", "c2"]}
        findings = checker.check_overlap_compatibility(sections, {}, overlaps)
        assert len(findings) >= 1
        assert findings[0].severity == "error"

    def test_check_trust_adequacy_sufficient(self) -> None:
        checker = SectionCompatibilityChecker()
        sections = {"c1": {"trust": "proof"}}
        reqs = {"c1": "heuristic"}
        findings = checker.check_trust_adequacy(sections, reqs)
        assert len(findings) == 0

    def test_check_trust_adequacy_insufficient(self) -> None:
        checker = SectionCompatibilityChecker()
        sections = {"c1": {"trust": "claim"}}
        reqs = {"c1": "proof"}
        findings = checker.check_trust_adequacy(sections, reqs)
        assert len(findings) >= 1
        assert findings[0].severity == "error"

    def test_check_public_honesty(self) -> None:
        checker = SectionCompatibilityChecker()
        sections = {"c1": {"trust": "claim"}}
        claims = {"c1": ["We guarantee safety"]}
        findings = checker.check_public_honesty(sections, claims)
        assert len(findings) >= 1
        assert findings[0].severity == "error"

    def test_check_treaty_compliance(self) -> None:
        checker = SectionCompatibilityChecker()
        treaties = {"t1": {"parties": ["c1", "c2"], "requirements": {}}}
        impacts = checker.check_treaty_compliance(["c1"], treaties)
        assert len(impacts) == 1
        assert impacts[0].treaty_id == "t1"

    def test_full_review_approve(self) -> None:
        checker = SectionCompatibilityChecker()
        scope = ReviewScope(changed_coordinates=["c1"])
        sections = {"c1": {"trust": "proof", "propositions": ["p1"]}}
        treaties: dict = {}
        verdict = checker.full_review(
            scope=scope,
            sections=sections,
            propositions=["p1"],
            treaties=treaties,
            trust_reqs={"c1": "claim"},
        )
        assert verdict.overall == "APPROVE"

    def test_full_review_request_changes(self) -> None:
        checker = SectionCompatibilityChecker()
        scope = ReviewScope(changed_coordinates=["c1"])
        sections = {"c1": {"trust": "claim", "propositions": ["p1"]}}
        treaties: dict = {}
        verdict = checker.full_review(
            scope=scope,
            sections=sections,
            propositions=["p1"],
            treaties=treaties,
            trust_reqs={"c1": "proof"},
        )
        assert verdict.overall == "REQUEST_CHANGES"


# ===================================================================
# TestAutoReviewer
# ===================================================================


class TestAutoReviewer:
    """Tests for AutoReviewer."""

    def test_auto_review_basic(self) -> None:
        reviewer = AutoReviewer()
        sections = {"c1": {"trust": "proof", "propositions": ["p1"]}}
        treaties: dict = {}
        evidence = {"c1": {"trust": "proof"}}
        verdict = reviewer.auto_review(
            changed_coords=["c1"],
            morphisms={},
            sections=sections,
            treaties=treaties,
            evidence=evidence,
        )
        assert isinstance(verdict, ReviewVerdict)

    def test_suggest_reviewers(self) -> None:
        reviewer = AutoReviewer()
        scope = ReviewScope(affected_teams=["backend", "frontend"])
        result = reviewer.suggest_reviewers(scope)
        assert result == ["backend", "frontend"]

    def test_estimate_review_effort(self) -> None:
        reviewer = AutoReviewer()
        scope = ReviewScope(
            changed_coordinates=["c1", "c2"],
            affected_treaties=["t1"],
            affected_overlaps=["o1", "o2"],
        )
        effort = reviewer.estimate_review_effort(scope)
        expected = 2 * 1.0 + 1 * 3.0 + 2 * 0.5
        assert effort == expected


# ===================================================================
# TestModels
# ===================================================================


class TestModels:
    """Serialisation round-trip tests for code-review models."""

    def test_review_scope_serialization(self) -> None:
        scope = ReviewScope(
            pr_id="pr-42",
            changed_coordinates=["c1"],
            affected_overlaps=["o1"],
            affected_treaties=["t1"],
            affected_teams=["team1"],
            trust_changes={"c1": ("claim", "proof")},
            public_projection_changes=["c1"],
        )
        d = scope.to_dict()
        # Tuples should be serialised as lists
        assert d["trust_changes"]["c1"] == ["claim", "proof"]
        scope2 = ReviewScope.from_dict(d)
        assert scope2.trust_changes["c1"] == ("claim", "proof")
        assert scope2.pr_id == "pr-42"

    def test_review_finding_serialization(self) -> None:
        finding = ReviewFinding(
            check=ReviewCheck.TRUST_ADEQUACY,
            coordinate_id="c1",
            severity="error",
            description="Trust too low",
            suggestion="Raise trust",
        )
        d = finding.to_dict()
        finding2 = ReviewFinding.from_dict(d)
        assert finding2.check == ReviewCheck.TRUST_ADEQUACY
        assert finding2.suggestion == "Raise trust"
        assert finding2.to_dict() == d

    def test_review_verdict_serialization(self) -> None:
        finding = ReviewFinding(
            check=ReviewCheck.INTERNAL_CONSISTENCY,
            severity="warning",
        )
        verdict = ReviewVerdict(
            pr_id="pr-1",
            findings=[finding],
            pass_count=5,
            fail_count=0,
            warning_count=1,
            overall="APPROVE",
            required_reviewers=["team1"],
            trust_adequate=True,
            descent_preserved=True,
        )
        d = verdict.to_dict()
        verdict2 = ReviewVerdict.from_dict(d)
        assert verdict2.pr_id == "pr-1"
        assert len(verdict2.findings) == 1
        assert verdict2.to_dict() == d

    def test_treaty_impact_serialization(self) -> None:
        impact = TreatyImpact(
            treaty_id="t1",
            parties=["c1", "c2"],
            change_description="Changed c1",
            renegotiation_needed=True,
            proposed_amendment="Add clause 3",
        )
        d = impact.to_dict()
        impact2 = TreatyImpact.from_dict(d)
        assert impact2.treaty_id == "t1"
        assert impact2.proposed_amendment == "Add clause 3"
        assert impact2.to_dict() == d
