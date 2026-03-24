"""Tests for jugeo_agents.types — shared types, enums, dataclasses."""

import pytest

from jugeo_agents.types import (
    AgentOutput,
    CohomologyClass,
    ConvergencePhase,
    CoverageReport,
    DescentResult,
    EvidenceChannel,
    FactualClaim,
    Obstruction,
    ObstructionKind,
    PipelineReport,
    ProvenanceChain,
    ProvenanceLink,
    TreatyResolution,
    TrustLevel,
    can_promote,
    conservative_join,
    trust_compose,
)


# ---------------------------------------------------------------------------
# TrustLevel ordering
# ---------------------------------------------------------------------------


def test_trust_level_ordering():
    assert TrustLevel.UNGROUNDED_CLAIM < TrustLevel.TOOL_VERIFIED
    assert TrustLevel.FORMALLY_PROVEN > TrustLevel.HUMAN_VERIFIED
    assert TrustLevel.SELF_CONTRADICTED < TrustLevel.UNGROUNDED_CLAIM


def test_trust_level_is_grounded():
    assert not TrustLevel.UNGROUNDED_CLAIM.is_grounded
    assert not TrustLevel.WEAK_MODEL_GENERATED.is_grounded
    assert TrustLevel.CROSS_AGENT_CONFIRMED.is_grounded
    assert TrustLevel.TOOL_VERIFIED.is_grounded


def test_trust_level_is_verified():
    assert not TrustLevel.RAG_GROUNDED.is_verified
    assert TrustLevel.TOOL_VERIFIED.is_verified
    assert TrustLevel.HUMAN_VERIFIED.is_verified
    assert TrustLevel.FORMALLY_PROVEN.is_verified


# ---------------------------------------------------------------------------
# conservative_join / trust_compose / can_promote
# ---------------------------------------------------------------------------


def test_conservative_join_returns_weakest():
    result = conservative_join(
        TrustLevel.TOOL_VERIFIED, TrustLevel.UNGROUNDED_CLAIM
    )
    assert result == TrustLevel.UNGROUNDED_CLAIM


def test_conservative_join_single():
    assert conservative_join(TrustLevel.RAG_GROUNDED) == TrustLevel.RAG_GROUNDED


def test_conservative_join_empty():
    assert conservative_join() == TrustLevel.UNGROUNDED_CLAIM


def test_trust_compose_is_min():
    assert trust_compose(TrustLevel.TOOL_EXECUTED, TrustLevel.RAG_GROUNDED) == TrustLevel.RAG_GROUNDED


def test_can_promote_requires_evidence():
    assert not can_promote(TrustLevel.UNGROUNDED_CLAIM, TrustLevel.RAG_GROUNDED, "")
    assert can_promote(TrustLevel.UNGROUNDED_CLAIM, TrustLevel.RAG_GROUNDED, "RAG source found")


def test_can_promote_target_must_be_higher():
    assert not can_promote(TrustLevel.TOOL_VERIFIED, TrustLevel.UNGROUNDED_CLAIM, "evidence")


# ---------------------------------------------------------------------------
# FactualClaim
# ---------------------------------------------------------------------------


def test_factual_claim_defaults():
    claim = FactualClaim(text="Paris is the capital of France")
    assert claim.trust == TrustLevel.UNGROUNDED_CLAIM
    assert claim.claim_id  # auto-generated
    assert claim.timestamp > 0


def test_factual_claim_with_trust():
    claim = FactualClaim(text="Pi = 3.14", subject="Pi", value="3.14")
    upgraded = claim.with_trust(TrustLevel.TOOL_VERIFIED)
    assert upgraded.trust == TrustLevel.TOOL_VERIFIED
    assert upgraded.text == claim.text
    assert upgraded.claim_id == claim.claim_id  # preserves id


# ---------------------------------------------------------------------------
# AgentOutput
# ---------------------------------------------------------------------------


def test_agent_output_defaults():
    out = AgentOutput(agent_id="a1", output_text="Hello world")
    assert out.trust == TrustLevel.UNGROUNDED_CLAIM
    assert out.tools_used == []
    assert out.round_number == 0


# ---------------------------------------------------------------------------
# ProvenanceChain
# ---------------------------------------------------------------------------


def test_provenance_chain_overall_trust():
    claim = FactualClaim(text="x", trust=TrustLevel.TOOL_VERIFIED)
    chain = ProvenanceChain(
        claim=claim,
        links=[
            ProvenanceLink(agent_id="a1", action="generate", trust=TrustLevel.TOOL_VERIFIED),
            ProvenanceLink(agent_id="a2", action="relay", trust=TrustLevel.UNGROUNDED_CLAIM),
        ],
    )
    assert chain.overall_trust == TrustLevel.UNGROUNDED_CLAIM
    assert chain.weakest_link is not None
    assert chain.weakest_link.trust == TrustLevel.UNGROUNDED_CLAIM


def test_provenance_chain_empty_links():
    claim = FactualClaim(text="x", trust=TrustLevel.RAG_GROUNDED)
    chain = ProvenanceChain(claim=claim)
    assert chain.overall_trust == TrustLevel.RAG_GROUNDED
    assert chain.weakest_link is None


# ---------------------------------------------------------------------------
# PipelineReport.summary_text
# ---------------------------------------------------------------------------


def test_pipeline_report_summary_text():
    report = PipelineReport(
        descent_result=DescentResult(is_consistent=True, consistency_score=1.0),
        coverage=CoverageReport(is_complete=True, coverage_score=1.0),
        total_agents=3,
        total_rounds=5,
        total_claims=10,
        final_phase=ConvergencePhase.COMPLETE,
        final_lyapunov=0.01,
    )
    text = report.summary_text()
    assert "3 agents" in text
    assert "5 rounds" in text
    assert "100%" in text
    assert "COMPLETE" in text.lower() or "complete" in text


def test_pipeline_report_summary_with_gaps():
    report = PipelineReport(
        descent_result=DescentResult(
            is_consistent=False,
            consistency_score=0.6,
            obstructions=[
                Obstruction(
                    kind=ObstructionKind.QUANTITATIVE_CONTRADICTION,
                    cohomology=CohomologyClass.H1,
                    agents_involved=["a1", "a2"],
                )
            ],
        ),
        coverage=CoverageReport(
            is_complete=False, coverage_score=0.75, gaps={"bias_mitigation"}
        ),
        total_agents=2,
        total_rounds=3,
        total_claims=5,
    )
    text = report.summary_text()
    assert "75%" in text
    assert "Obstructions: 1" in text
