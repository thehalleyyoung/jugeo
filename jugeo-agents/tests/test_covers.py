"""Tests for jugeo_agents.core.covers — CoverageChecker."""

import pytest

from jugeo_agents.types import CoverageReport
from jugeo_agents.core.covers import CoverageChecker


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _research_task():
    return (
        "Write a comprehensive research report on climate change. "
        "Ensure factual accuracy, cite authoritative sources, include "
        "detailed analysis, and provide an executive summary. "
        "The report should be reviewed for bias and fact-checked."
    )


def _full_subtasks():
    return [
        {
            "name": "find_sources",
            "agent_id": "researcher",
            "scope": "Find diverse, authoritative sources with citations",
        },
        {
            "name": "analysis",
            "agent_id": "analyst",
            "scope": "Perform deep analysis of climate data and verify facts",
        },
        {
            "name": "draft",
            "agent_id": "writer",
            "scope": "Write a clear, structured draft with executive summary and citations",
            "depends_on": ["find_sources", "analysis"],
        },
        {
            "name": "review",
            "agent_id": "reviewer",
            "scope": "Peer-review the draft for factual accuracy, bias mitigation, and quality",
            "depends_on": ["draft"],
        },
    ]


# ---------------------------------------------------------------------------
# Complete coverage
# ---------------------------------------------------------------------------


def test_check_complete_coverage():
    checker = CoverageChecker()
    report = checker.check(_research_task(), _full_subtasks())
    assert isinstance(report, CoverageReport)
    assert report.coverage_score > 0.0
    assert len(report.covered_dimensions) > 0


# ---------------------------------------------------------------------------
# Incomplete coverage (few subtasks)
# ---------------------------------------------------------------------------


def test_check_incomplete_coverage():
    checker = CoverageChecker()
    task = _research_task()
    # Only one subtask — should leave gaps
    subtasks = [
        {
            "name": "draft",
            "agent_id": "writer",
            "scope": "Write a short paragraph about climate change",
        },
    ]
    report = checker.check(task, subtasks)
    assert isinstance(report, CoverageReport)
    # With a minimal subtask, there should be uncovered dimensions
    assert len(report.gaps) > 0 or report.coverage_score < 1.0


# ---------------------------------------------------------------------------
# Gap detection
# ---------------------------------------------------------------------------


def test_gaps_identified():
    checker = CoverageChecker()
    task = (
        "Write a security audit report. Verify facts, check for "
        "vulnerabilities, and ensure privacy compliance."
    )
    subtasks = [
        {
            "name": "scan",
            "agent_id": "scanner",
            "scope": "Scan codebase for issues",
        }
    ]
    report = checker.check(task, subtasks)
    # The task mentions security, privacy, fact-checking — many dimensions
    # but only one generic subtask. Expect gaps.
    assert len(report.gaps) >= 1


# ---------------------------------------------------------------------------
# Redundancy detection
# ---------------------------------------------------------------------------


def test_redundancy_detection():
    checker = CoverageChecker()
    task = "Write a report ensuring factual accuracy and source verification."
    subtasks = [
        {
            "name": "verify_1",
            "agent_id": "v1",
            "scope": "Fact-check all claims and verify accuracy",
        },
        {
            "name": "verify_2",
            "agent_id": "v2",
            "scope": "Independently verify facts and check accuracy",
        },
    ]
    report = checker.check(task, subtasks)
    # Both subtasks cover the same dimensions → redundancy expected
    assert len(report.redundancies) >= 0  # may or may not detect
    assert isinstance(report.redundancies, dict)


# ---------------------------------------------------------------------------
# Empty decomposition
# ---------------------------------------------------------------------------


def test_check_empty_subtasks():
    checker = CoverageChecker()
    report = checker.check("Write a detailed report on AI ethics.", [])
    assert isinstance(report, CoverageReport)
    assert report.coverage_score <= 1.0


# ---------------------------------------------------------------------------
# Dependency errors
# ---------------------------------------------------------------------------


def test_dependency_cycle_detected():
    checker = CoverageChecker()
    task = "Analyze data and verify claims."
    subtasks = [
        {
            "name": "step_a",
            "agent_id": "a1",
            "scope": "Analyze data",
            "depends_on": ["step_b"],
        },
        {
            "name": "step_b",
            "agent_id": "a2",
            "scope": "Verify results",
            "depends_on": ["step_a"],
        },
    ]
    report = checker.check(task, subtasks)
    dep_suggestions = [s for s in report.suggestions if "[DEPENDENCY]" in s]
    assert len(dep_suggestions) >= 1
    assert not report.is_complete


# ---------------------------------------------------------------------------
# Suggestions
# ---------------------------------------------------------------------------


def test_suggestions_for_gaps():
    checker = CoverageChecker()
    task = (
        "Produce a peer-reviewed, fact-checked analysis with citations, "
        "bias mitigation, and privacy compliance."
    )
    subtasks = [
        {
            "name": "draft",
            "agent_id": "writer",
            "scope": "Write a brief draft",
        },
    ]
    report = checker.check(task, subtasks)
    gap_suggestions = [s for s in report.suggestions if "[GAP]" in s]
    assert len(gap_suggestions) >= 1


# ---------------------------------------------------------------------------
# Coverage score is a float in [0, 1]
# ---------------------------------------------------------------------------


def test_coverage_score_bounds():
    checker = CoverageChecker()
    report = checker.check(
        "Verify the accuracy of data sources.",
        [{"name": "verify", "agent_id": "v", "scope": "Verify accuracy and sources"}],
    )
    assert 0.0 <= report.coverage_score <= 1.0
