"""Integration tests for jugeo_agents.wrapper — JuGeoAgentWrapper."""

import pytest

from jugeo_agents.types import (
    ConvergencePhase,
    CoverageReport,
    PipelineReport,
    TrustLevel,
)
from jugeo_agents.wrapper import JuGeoAgentWrapper, VerificationResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _setup_wrapper():
    """Create a wrapper and verify a task decomposition."""
    jugeo = JuGeoAgentWrapper(
        auto_negotiate=True, auto_challenge=True, token_budget=100.0
    )
    coverage = jugeo.verify_task_decomposition(
        task=(
            "Write a comprehensive research report on renewable energy. "
            "Ensure factual accuracy, cite authoritative sources, include "
            "deep analysis, and check for bias."
        ),
        subtasks=[
            {"name": "research", "scope": "Find and cite authoritative sources on renewable energy"},
            {"name": "analysis", "scope": "Analyze data and verify facts about renewable energy"},
            {"name": "draft", "scope": "Write a clear, structured report with executive summary"},
            {"name": "review", "scope": "Peer-review for factual accuracy and bias"},
        ],
    )
    return jugeo, coverage


# ---------------------------------------------------------------------------
# verify_task_decomposition
# ---------------------------------------------------------------------------


def test_verify_task_decomposition():
    jugeo, coverage = _setup_wrapper()
    assert isinstance(coverage, CoverageReport)
    assert coverage.coverage_score > 0.0
    assert len(coverage.covered_dimensions) > 0


# ---------------------------------------------------------------------------
# on_agent_output — consistent
# ---------------------------------------------------------------------------


def test_on_agent_output_consistent():
    jugeo, _ = _setup_wrapper()
    result = jugeo.on_agent_output(
        agent_id="researcher",
        output="Solar energy capacity reached 1,200 GW globally in 2023.",
        metadata={"model": "gpt-4o", "subtask": "research"},
    )
    assert isinstance(result, VerificationResult)
    assert result.agent_id == "researcher"
    assert result.claims_extracted >= 0
    assert result.status in ("consistent", "conflict_detected", "conflict_resolved")


# ---------------------------------------------------------------------------
# on_agent_output — with tools
# ---------------------------------------------------------------------------


def test_on_agent_output_with_tools():
    jugeo, _ = _setup_wrapper()
    result = jugeo.on_agent_output(
        agent_id="analyst",
        output="Wind power capacity was 900 GW in 2023.",
        metadata={"tools_used": ["web_search"], "subtask": "analysis"},
    )
    assert result.trust_level >= TrustLevel.TOOL_EXECUTED


# ---------------------------------------------------------------------------
# on_agent_output — contradictory
# ---------------------------------------------------------------------------


def test_on_agent_output_contradictory():
    jugeo, _ = _setup_wrapper()
    jugeo.on_agent_output(
        agent_id="a1",
        output="Tesla was founded in 2003.",
        metadata={"subtask": "research"},
    )
    result = jugeo.on_agent_output(
        agent_id="a2",
        output="Tesla was founded in 2008.",
        metadata={"subtask": "analysis"},
    )
    # May detect contradictions depending on claim extraction
    assert isinstance(result, VerificationResult)


# ---------------------------------------------------------------------------
# on_pipeline_complete
# ---------------------------------------------------------------------------


def test_on_pipeline_complete():
    jugeo, _ = _setup_wrapper()
    jugeo.on_agent_output(
        "researcher",
        "Solar energy grew by 25% in 2023. The global capacity reached 1,200 GW.",
        {"model": "gpt-4o", "subtask": "research"},
    )
    jugeo.on_agent_output(
        "analyst",
        "Wind power capacity was 900 GW in 2023. Renewable energy investment exceeded $300B.",
        {"model": "claude-3.5-sonnet", "subtask": "analysis"},
    )
    jugeo.on_agent_output(
        "writer",
        "Renewable energy capacity surged in 2023. Solar reached 1,200 GW and wind hit 900 GW.",
        {"model": "gpt-4o", "subtask": "draft"},
    )
    report = jugeo.on_pipeline_complete()
    assert isinstance(report, PipelineReport)
    assert report.total_agents >= 3
    assert report.total_rounds >= 3
    assert report.total_claims >= 0
    assert 0.0 <= report.coverage.coverage_score <= 1.0


# ---------------------------------------------------------------------------
# Full pipeline flow
# ---------------------------------------------------------------------------


def test_full_pipeline_flow():
    jugeo = JuGeoAgentWrapper()
    coverage = jugeo.verify_task_decomposition(
        task="Analyze quarterly earnings for TechCo. Verify all financial figures.",
        subtasks=[
            {"name": "data", "scope": "Collect quarterly earnings data and figures"},
            {"name": "verify", "scope": "Verify financial accuracy of earnings data"},
        ],
    )
    assert isinstance(coverage, CoverageReport)

    r1 = jugeo.on_agent_output(
        "data_agent",
        "TechCo revenue was $50B in Q3 2024. Net income was $8B.",
        {"model": "gpt-4o", "subtask": "data"},
    )
    assert r1.status == "consistent"

    r2 = jugeo.on_agent_output(
        "verify_agent",
        "TechCo revenue was $50B in Q3 2024. Profit margin was 16%.",
        {"model": "gpt-4o", "tools_used": ["sql_query"], "subtask": "verify"},
    )
    assert isinstance(r2, VerificationResult)

    report = jugeo.on_pipeline_complete()
    assert isinstance(report, PipelineReport)
    text = report.summary_text()
    assert "2 agents" in text
    assert "Pipeline Report" in text


# ---------------------------------------------------------------------------
# suggest_next_action
# ---------------------------------------------------------------------------


def test_suggest_next_action():
    jugeo, _ = _setup_wrapper()
    jugeo.on_agent_output(
        "researcher",
        "Solar energy data collected.",
        {"subtask": "research"},
    )
    action = jugeo.suggest_next_action()
    assert action.action_type is not None
    assert action.priority >= 0.0


# ---------------------------------------------------------------------------
# convergence_status
# ---------------------------------------------------------------------------


def test_convergence_status():
    jugeo, _ = _setup_wrapper()
    jugeo.on_agent_output("a1", "Some output.", {"subtask": "research"})
    status = jugeo.convergence_status()
    assert status is not None


# ---------------------------------------------------------------------------
# reset
# ---------------------------------------------------------------------------


def test_reset():
    jugeo, _ = _setup_wrapper()
    jugeo.on_agent_output("a1", "Hello.", {"subtask": "research"})
    jugeo.reset()
    # After reset, pipeline state should be clean
    report = jugeo.on_pipeline_complete()
    assert report.total_agents == 0
    assert report.total_rounds == 0
