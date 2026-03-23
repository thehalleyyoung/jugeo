"""
Doctrine checker for the doctrine_completion encoding package.

This module is part of JuGeo's copilot-assisted encoding of theory2.tex Chapter 37:
Implementation-complete thesis doctrine — every claim has implementation evidence.

It provides the main DoctrineChecker class and supporting classes for
verifying that doctrine statements are adequately grounded in implementation
evidence.  The checker orchestrates the algorithms from algorithms.py and
produces structured reports.

Chapter reference: Ch37 — Implementation-Complete Thesis Doctrine.

copilot
"""
from __future__ import annotations

import json
import time
import uuid
from typing import Any, Optional

from .models import (
    DoctrineStatement,
    ImplementationEvidence,
    CompletenessCheck,
    DoctrineGap,
    DoctrineCompletionReport,
    GapSeverity,
    EvidenceKind,
    StatementStatus,
    ClaimType,
)
from .implementation_evidence import (
    EvidenceAggregator,
    ConfidenceEstimator,
    EvidenceChain,
    EvidenceValidator,
)
from .completeness import (
    CompletenessMetrics,
    CompletionStrategy,
    CompletenessAnalyzer,
    DoctrineGraph,
    GapBridger,
)
from .algorithms import (
    GroundingAlgorithm,
    GapFindingAlgorithm,
    CoverageComputationAlgorithm,
    IncrementalCheckAlgorithm,
    RiskAssessmentAlgorithm,
)

__all__ = [
    "DoctrineChecker",
    "GroundingVerifier",
    "CoverageAnalyzer",
    "GapPrioritizer",
    "DoctrineAuditor",
    "check_doctrine_completeness",
    "quick_check",
]


# ---------------------------------------------------------------------------
# DoctrineChecker
# ---------------------------------------------------------------------------


class DoctrineChecker:
    """Main class for checking doctrine statement completeness.

    DoctrineChecker orchestrates the grounding algorithm, gap finding,
    coverage computation, and report generation.  It accepts an optional
    policy dictionary that can tune thresholds and strategies.

    Example usage::

        checker = DoctrineChecker()
        report = checker.generate_report(
            statements=my_statements,
            evidence_map=my_evidence_map,
            manifest_id="abc-123",
        )
        print(report.summarize())

    Attributes:
        policy: Configuration dictionary with optional keys:
            - 'confidence_threshold': float (default 0.7)
            - 'coverage_threshold': float (default 0.8)
            - 'strategy': str (default 'critical_path')
    """

    def __init__(self, policy: Optional[dict[str, Any]] = None) -> None:
        """Initialise the DoctrineChecker with an optional policy.

        Args:
            policy: Optional configuration dictionary.
        """
        self.policy: dict[str, Any] = policy or {}
        self._conf_threshold: float = float(self.policy.get("confidence_threshold", 0.7))
        self._coverage_threshold: float = float(self.policy.get("coverage_threshold", 0.8))
        strategy_str = self.policy.get("strategy", "critical_path")
        try:
            self._strategy = CompletionStrategy(strategy_str)
        except ValueError:
            self._strategy = CompletionStrategy.CRITICAL_PATH

        self._grounder = GroundingAlgorithm(confidence_threshold=self._conf_threshold)
        self._gap_finder = GapFindingAlgorithm()
        self._coverage_algo = CoverageComputationAlgorithm()
        self._incremental = IncrementalCheckAlgorithm()
        self._risk_algo = RiskAssessmentAlgorithm()
        self._analyzer = CompletenessAnalyzer(strategy=self._strategy)
        self._checker_id: str = str(uuid.uuid4())

    def check_statement(
        self,
        statement: DoctrineStatement,
        available_evidence: list[ImplementationEvidence],
    ) -> CompletenessCheck:
        """Produce a CompletenessCheck for a single statement.

        Runs grounding and gap analysis on the provided statement and
        evidence, then wraps the result in a CompletenessCheck.

        Args:
            statement: The DoctrineStatement to check.
            available_evidence: Evidence items available for grounding.

        Returns:
            A CompletenessCheck for the single statement.
        """
        result = self._grounder.ground(statement, available_evidence)
        is_complete = result["status"] == StatementStatus.COMPLETE.value
        is_partial = result["status"] == StatementStatus.PARTIAL.value
        is_ungrounded = result["status"] == StatementStatus.UNGROUNDED.value

        gaps = self._gap_finder.find_gaps(statement, available_evidence)
        gap_ids = [statement.statement_id] if gaps else []
        critical_gaps = [
            g.statement_id
            for g in gaps
            if g.gap_severity in (GapSeverity.CRITICAL, GapSeverity.BLOCKING)
        ]
        recommendations = self.recommend_fixes(gaps)

        return CompletenessCheck.create(
            total_statements=1,
            complete_count=1 if is_complete else 0,
            partial_count=1 if is_partial else 0,
            ungrounded_count=1 if is_ungrounded else 0,
            gap_list=gap_ids,
            critical_gaps=critical_gaps,
            recommendations=recommendations,
        )

    def check_all(
        self,
        statements: list[DoctrineStatement],
        evidence_map: dict[str, list[ImplementationEvidence]],
    ) -> list[CompletenessCheck]:
        """Check all statements and return one CompletenessCheck per statement.

        Args:
            statements: All DoctrineStatements to check.
            evidence_map: Mapping from statement_id to evidence list.

        Returns:
            List of CompletenessCheck results in the same order as statements.
        """
        checks: list[CompletenessCheck] = []
        for stmt in statements:
            evs = evidence_map.get(stmt.statement_id, [])
            check = self.check_statement(stmt, evs)
            checks.append(check)
        return checks

    def compute_coverage(
        self,
        statements: list[DoctrineStatement],
        checks: list[CompletenessCheck],
    ) -> float:
        """Compute a weighted coverage fraction from statements and checks.

        Coverage = sum of complete_counts / sum of total_statements across checks.

        Args:
            statements: All doctrine statements (used for weight context).
            checks: List of CompletenessCheck results.

        Returns:
            Coverage fraction in [0.0, 1.0].
        """
        if not checks:
            return 0.0
        total = sum(c.total_statements for c in checks)
        complete = sum(c.complete_count for c in checks)
        if total == 0:
            return 0.0
        return complete / total

    def find_gaps(
        self,
        statements: list[DoctrineStatement],
        evidence_map: dict[str, list[ImplementationEvidence]],
    ) -> list[DoctrineGap]:
        """Find all evidence gaps across all statements.

        Args:
            statements: All doctrine statements.
            evidence_map: Mapping from statement_id to evidence list.

        Returns:
            List of DoctrineGap instances for all unsatisfied requirements.
        """
        return self._gap_finder.find_all_gaps(statements, evidence_map)

    def recommend_fixes(self, gaps: list[DoctrineGap]) -> list[str]:
        """Generate actionable fix recommendations for a list of gaps.

        Produces one recommendation string per gap, ordered by severity
        (most severe first).

        Args:
            gaps: List of DoctrineGap instances.

        Returns:
            Ordered list of recommendation strings.
        """
        sorted_gaps = sorted(gaps, key=lambda g: -g.compute_severity_score())
        recommendations: list[str] = []
        for gap in sorted_gaps:
            kinds_str = ", ".join(k.value for k in gap.missing_evidence_kinds)
            rec = (
                f"[{gap.gap_severity.value.upper()}] "
                f"Statement {gap.statement_id[:8]}: collect {kinds_str} evidence. "
                f"Suggestion: {gap.suggested_fix}"
            )
            recommendations.append(rec)
        return recommendations

    def generate_report(
        self,
        statements: list[DoctrineStatement],
        evidence_map: dict[str, list[ImplementationEvidence]],
        manifest_id: str,
    ) -> DoctrineCompletionReport:
        """Generate a full DoctrineCompletionReport.

        Runs check_all() on all statements, computes coverage, finds gaps,
        and assembles everything into a DoctrineCompletionReport.

        Args:
            statements: All doctrine statements to evaluate.
            evidence_map: Mapping from statement_id to evidence list.
            manifest_id: ID of the associated manifest.

        Returns:
            A complete DoctrineCompletionReport.
        """
        checks = self.check_all(statements, evidence_map)
        coverage = self.compute_coverage(statements, checks)
        gaps = self.find_gaps(statements, evidence_map)
        critical_count = sum(
            1 for g in gaps
            if g.gap_severity in (GapSeverity.CRITICAL, GapSeverity.BLOCKING)
        )
        summary = (
            f"Doctrine check: {len(statements)} statements, "
            f"{coverage * 100:.1f}% coverage, "
            f"{len(gaps)} gaps ({critical_count} critical)"
        )
        report = DoctrineCompletionReport.create(
            manifest_id=manifest_id,
            checks=checks,
            summary=summary,
        )
        return report

    def run_incremental_check(
        self,
        new_evidence: list[ImplementationEvidence],
        statements: list[DoctrineStatement],
        prior_checks: list[CompletenessCheck],
    ) -> list[CompletenessCheck]:
        """Re-check only the statements affected by new evidence.

        Delegates to IncrementalCheckAlgorithm for efficiency.

        Args:
            new_evidence: Newly added evidence items.
            statements: All doctrine statements.
            prior_checks: Previously computed checks.

        Returns:
            Updated list of CompletenessCheck results.
        """
        return self._incremental.check_incremental(
            new_evidence=new_evidence,
            all_statements=statements,
            prior_checks=prior_checks,
        )


# ---------------------------------------------------------------------------
# GroundingVerifier
# ---------------------------------------------------------------------------


class GroundingVerifier:
    """Verifies that doctrine statements are adequately grounded.

    GroundingVerifier provides statement-level and batch grounding
    verification, computing grounding scores and returning (bool, reason)
    pairs.

    Attributes:
        min_confidence: Minimum confidence threshold for evidence to count.
        min_depth: Minimum grounding depth for evidence to count.
    """

    def __init__(
        self,
        min_confidence: float = 0.7,
        min_depth: int = 2,
    ) -> None:
        """Initialise the verifier with thresholds.

        Args:
            min_confidence: Minimum confidence for qualifying evidence.
            min_depth: Minimum depth for qualifying evidence.
        """
        self.min_confidence = min_confidence
        self.min_depth = min_depth
        self._grounder = GroundingAlgorithm(confidence_threshold=min_confidence)
        self._verifier_id: str = str(uuid.uuid4())

    def verify(
        self,
        statement: DoctrineStatement,
        evidences: list[ImplementationEvidence],
    ) -> tuple[bool, str]:
        """Verify a single statement against its available evidence.

        Args:
            statement: The statement to verify.
            evidences: Available evidence for grounding.

        Returns:
            (is_verified, reason_string) tuple.
        """
        qualifying = [
            ev for ev in evidences
            if ev.confidence >= self.min_confidence
            and ev.grounding_depth >= self.min_depth
        ]
        is_grounded = self._grounder.is_fully_grounded(statement, qualifying)
        if is_grounded:
            reason = (
                f"Statement {statement.statement_id[:8]} is fully grounded by "
                f"{len(qualifying)} qualifying evidence items."
            )
        else:
            missing = statement.get_gaps([ev.evidence_kind for ev in qualifying])
            reason = (
                f"Statement {statement.statement_id[:8]} is NOT fully grounded. "
                f"Missing: {[k.value for k in missing]}. "
                f"{len(qualifying)}/{len(evidences)} items meet quality thresholds."
            )
        return (is_grounded, reason)

    def verify_batch(
        self,
        pairs: list[tuple[DoctrineStatement, list[ImplementationEvidence]]],
    ) -> dict[str, tuple[bool, str]]:
        """Verify a batch of statement-evidence pairs.

        Args:
            pairs: List of (statement, evidences) tuples.

        Returns:
            Dictionary mapping statement_id to (is_verified, reason).
        """
        return {
            stmt.statement_id: self.verify(stmt, evs)
            for stmt, evs in pairs
        }

    def compute_grounding_score(
        self,
        statement: DoctrineStatement,
        evidences: list[ImplementationEvidence],
    ) -> float:
        """Compute a grounding score for this statement and its evidences.

        Filters to qualifying items then delegates to GroundingAlgorithm.

        Args:
            statement: The statement to score.
            evidences: All available evidence.

        Returns:
            Grounding score in [0.0, 1.0].
        """
        qualifying = [
            ev for ev in evidences
            if ev.confidence >= self.min_confidence
            and ev.grounding_depth >= self.min_depth
        ]
        return self._grounder.compute_grounding_score(statement, qualifying)

    def get_verification_summary(
        self,
        results: dict[str, tuple[bool, str]],
    ) -> dict[str, Any]:
        """Summarise a set of verification results.

        Args:
            results: Dictionary from verify_batch().

        Returns:
            Summary dictionary with counts and overall verdict.
        """
        total = len(results)
        verified_count = sum(1 for is_v, _ in results.values() if is_v)
        failed = [sid for sid, (is_v, _) in results.items() if not is_v]
        return {
            "total": total,
            "verified_count": verified_count,
            "failed_count": total - verified_count,
            "verification_rate": verified_count / total if total > 0 else 0.0,
            "failed_statement_ids": failed,
            "overall_verdict": "PASS" if not failed else "FAIL",
            "summarized_at": time.time(),
        }


# ---------------------------------------------------------------------------
# CoverageAnalyzer
# ---------------------------------------------------------------------------


class CoverageAnalyzer:
    """Analyzes coverage trends, bottlenecks, and per-type breakdowns.

    CoverageAnalyzer provides post-hoc analysis of CompletenessCheck
    results to reveal patterns, bottlenecks, and improvement trends.
    """

    def __init__(self) -> None:
        """Initialise the coverage analyzer.

        Stateless beyond a unique ID.
        """
        self._analyzer_id: str = str(uuid.uuid4())

    def analyze(self, checks: list[CompletenessCheck]) -> dict[str, Any]:
        """Aggregate analysis over a list of completeness checks.

        Computes mean/min/max coverage, total gaps, and critical gap
        concentration.

        Args:
            checks: List of CompletenessCheck results.

        Returns:
            Dictionary of aggregate analytics.
        """
        if not checks:
            return {
                "count": 0,
                "mean_coverage": 0.0,
                "min_coverage": 0.0,
                "max_coverage": 0.0,
                "total_gaps": 0,
                "critical_gap_count": 0,
            }
        coverages = [c.coverage_score for c in checks]
        total_gaps = sum(len(c.gap_list) for c in checks)
        critical_gaps = sum(len(c.critical_gaps) for c in checks)
        return {
            "count": len(checks),
            "mean_coverage": sum(coverages) / len(coverages),
            "min_coverage": min(coverages),
            "max_coverage": max(coverages),
            "total_gaps": total_gaps,
            "critical_gap_count": critical_gaps,
            "passing_count": sum(1 for c in checks if c.is_passing()),
            "pass_rate": sum(1 for c in checks if c.is_passing()) / len(checks),
        }

    def by_claim_type(
        self,
        statements: list[DoctrineStatement],
        checks: list[CompletenessCheck],
    ) -> dict[str, float]:
        """Compute coverage fractions grouped by ClaimType.

        This method pairs statements to checks by position (checks[i]
        corresponds to statements[i]).

        Args:
            statements: Doctrine statements in the same order as checks.
            checks: Corresponding CompletenessCheck results.

        Returns:
            Dictionary mapping claim_type.value to coverage fraction.
        """
        type_complete: dict[str, int] = {}
        type_total: dict[str, int] = {}
        for stmt, check in zip(statements, checks):
            ctype = stmt.claim_type.value
            type_total[ctype] = type_total.get(ctype, 0) + check.total_statements
            type_complete[ctype] = type_complete.get(ctype, 0) + check.complete_count
        return {
            ctype: (type_complete.get(ctype, 0) / type_total[ctype])
            for ctype in type_total
        }

    def trend(
        self,
        historical_checks: list[list[CompletenessCheck]],
    ) -> list[float]:
        """Compute the coverage trend across multiple check snapshots.

        Each snapshot is a list of CompletenessCheck results; we compute
        the average coverage per snapshot.

        Args:
            historical_checks: List of check-result snapshots over time.

        Returns:
            List of mean coverage values (one per snapshot).
        """
        trend_values: list[float] = []
        for snapshot in historical_checks:
            if not snapshot:
                trend_values.append(0.0)
            else:
                mean_cov = sum(c.coverage_score for c in snapshot) / len(snapshot)
                trend_values.append(mean_cov)
        return trend_values

    def bottlenecks(
        self,
        checks: list[CompletenessCheck],
        threshold: float = 0.5,
    ) -> list[str]:
        """Return check IDs whose coverage is below the threshold.

        Args:
            checks: List of CompletenessCheck results.
            threshold: Coverage threshold below which a check is a bottleneck.

        Returns:
            List of check_ids for checks with low coverage.
        """
        return [
            c.check_id
            for c in checks
            if c.coverage_score < threshold
        ]


# ---------------------------------------------------------------------------
# GapPrioritizer
# ---------------------------------------------------------------------------


class GapPrioritizer:
    """Prioritises doctrine gaps for resolution.

    GapPrioritizer provides sorting, filtering, and grouping utilities
    for DoctrineGap instances, as well as heuristic effort estimation.
    """

    def __init__(self) -> None:
        """Initialise the gap prioritizer.

        Stateless beyond a unique ID.
        """
        self._prioritizer_id: str = str(uuid.uuid4())

    def prioritize(self, gaps: list[DoctrineGap]) -> list[DoctrineGap]:
        """Return gaps sorted by severity score descending.

        Args:
            gaps: List of DoctrineGap instances.

        Returns:
            Gaps sorted from most to least severe.
        """
        return sorted(gaps, key=lambda g: (-g.compute_severity_score(), g.created_at))

    def critical_only(self, gaps: list[DoctrineGap]) -> list[DoctrineGap]:
        """Return only CRITICAL or BLOCKING gaps.

        Args:
            gaps: List of DoctrineGap instances.

        Returns:
            Filtered list containing only CRITICAL and BLOCKING gaps.
        """
        return [
            g for g in gaps
            if g.gap_severity in (GapSeverity.CRITICAL, GapSeverity.BLOCKING)
        ]

    def by_statement(
        self, gaps: list[DoctrineGap]
    ) -> dict[str, list[DoctrineGap]]:
        """Group gaps by statement_id.

        Args:
            gaps: List of DoctrineGap instances.

        Returns:
            Dictionary mapping statement_id to list of associated gaps.
        """
        grouped: dict[str, list[DoctrineGap]] = {}
        for gap in gaps:
            grouped.setdefault(gap.statement_id, []).append(gap)
        return grouped

    def estimate_effort(self, gap: DoctrineGap) -> float:
        """Heuristic effort estimate for resolving a single gap.

        Effort = severity_score * 4.0 + len(missing_kinds) * 1.5
        so BLOCKING gaps with many missing kinds are expensive.

        Args:
            gap: The gap to estimate effort for.

        Returns:
            Float effort in arbitrary units.
        """
        return gap.compute_severity_score() * 4.0 + len(gap.missing_evidence_kinds) * 1.5


# ---------------------------------------------------------------------------
# DoctrineAuditor
# ---------------------------------------------------------------------------


class DoctrineAuditor:
    """Records and compares doctrine completion audit results.

    DoctrineAuditor provides a persistent audit trail of DoctrineCompletionReport
    instances, enabling comparison of audit results over time.
    """

    def __init__(self) -> None:
        """Initialise the auditor with an empty history.

        The audit history is a list of DoctrineCompletionReport instances.
        """
        self._audit_history: list[DoctrineCompletionReport] = []
        self._auditor_id: str = str(uuid.uuid4())
        self._checker = DoctrineChecker()

    def audit(
        self,
        statements: list[DoctrineStatement],
        evidence_map: dict[str, list[ImplementationEvidence]],
    ) -> dict[str, Any]:
        """Run a full audit and return a summary dictionary.

        Generates a DoctrineCompletionReport, records it in history,
        and returns a summary dict.

        Args:
            statements: All doctrine statements to audit.
            evidence_map: Mapping from statement_id to evidence list.

        Returns:
            Summary dictionary with report_id, score, gaps, and status.
        """
        report = self._checker.generate_report(
            statements=statements,
            evidence_map=evidence_map,
            manifest_id=f"audit:{self._auditor_id[:8]}",
        )
        self.record_audit(report)
        return {
            "report_id": report.report_id,
            "overall_score": report.overall_score,
            "status": report.status,
            "total_gaps": report.total_gaps,
            "summary": report.summary,
            "audited_at": time.time(),
            "audit_number": len(self._audit_history),
        }

    def record_audit(self, report: DoctrineCompletionReport) -> None:
        """Record a DoctrineCompletionReport in the audit history.

        Args:
            report: The report to record.
        """
        self._audit_history.append(report)

    def get_audit_history(self) -> list[DoctrineCompletionReport]:
        """Return a copy of the audit history.

        Returns:
            List of all recorded DoctrineCompletionReport instances.
        """
        return list(self._audit_history)

    def compare_audits(
        self,
        a: DoctrineCompletionReport,
        b: DoctrineCompletionReport,
    ) -> dict[str, Any]:
        """Compare two audit reports and return a difference summary.

        Args:
            a: The earlier report.
            b: The later report.

        Returns:
            Dictionary describing changes between a and b.
        """
        score_delta = b.overall_score - a.overall_score
        gap_delta = b.total_gaps - a.total_gaps
        check_delta = len(b.checks) - len(a.checks)
        return {
            "from_report_id": a.report_id,
            "to_report_id": b.report_id,
            "score_delta": score_delta,
            "score_improved": score_delta > 0,
            "gap_delta": gap_delta,
            "gaps_reduced": gap_delta < 0,
            "check_count_delta": check_delta,
            "from_status": a.status,
            "to_status": b.status,
            "status_changed": a.status != b.status,
            "new_gaps": [g for g in b.get_all_gaps() if g not in a.get_all_gaps()],
            "resolved_gaps": [g for g in a.get_all_gaps() if g not in b.get_all_gaps()],
        }


# ---------------------------------------------------------------------------
# Module-level functions
# ---------------------------------------------------------------------------


def check_doctrine_completeness(
    statements: list[DoctrineStatement],
    evidence_map: dict[str, list[ImplementationEvidence]],
) -> DoctrineCompletionReport:
    """Convenience function to run a full doctrine completeness check.

    Creates a DoctrineChecker with default policy and generates a report
    for the given statements and evidence map.

    Args:
        statements: All doctrine statements to evaluate.
        evidence_map: Mapping from statement_id to evidence list.

    Returns:
        A DoctrineCompletionReport summarising the completeness check.
    """
    checker = DoctrineChecker()
    return checker.generate_report(
        statements=statements,
        evidence_map=evidence_map,
        manifest_id=str(uuid.uuid4()),
    )


def quick_check(
    statement: DoctrineStatement,
    evidences: list[ImplementationEvidence],
) -> bool:
    """Quickly check if a single statement is fully grounded.

    Uses the default GroundingAlgorithm with confidence_threshold=0.7.

    Args:
        statement: The doctrine statement to check.
        evidences: Available evidence items.

    Returns:
        True if the statement is fully grounded, False otherwise.
    """
    algo = GroundingAlgorithm(confidence_threshold=0.7)
    return algo.is_fully_grounded(statement, evidences)
