"""Tests for jugeo_agents.core.trust — TrustAlgebra, ModelTrustProfile, TrustCeilingEnforcer."""

import pytest

from jugeo_agents.types import (
    AgentOutput,
    EvidenceChannel,
    FactualClaim,
    TrustLevel,
)
from jugeo_agents.core.trust import (
    ModelTrustProfile,
    PromotionResult,
    TrustAlgebra,
    TrustCeilingEnforcer,
)


# ---------------------------------------------------------------------------
# TrustAlgebra.classify_output
# ---------------------------------------------------------------------------


def test_classify_output_with_tools():
    algebra = TrustAlgebra()
    out = AgentOutput(
        agent_id="a1",
        output_text="Result of computation",
        tools_used=["web_search"],
    )
    level = algebra.classify_output(out)
    assert level == TrustLevel.TOOL_EXECUTED


def test_classify_output_with_rag():
    algebra = TrustAlgebra()
    out = AgentOutput(
        agent_id="a1",
        output_text="Based on retrieved docs",
        rag_sources=["doc1.pdf", "doc2.pdf"],
    )
    level = algebra.classify_output(out)
    assert level == TrustLevel.RAG_GROUNDED


def test_classify_output_with_citations():
    algebra = TrustAlgebra()
    out = AgentOutput(
        agent_id="a1",
        output_text="According to [1]",
        citations=["Smith et al. 2024"],
    )
    level = algebra.classify_output(out)
    assert level == TrustLevel.CITATION_BACKED


def test_classify_output_plain_text():
    algebra = TrustAlgebra()
    out = AgentOutput(agent_id="a1", output_text="Some claim")
    level = algebra.classify_output(out)
    assert level == TrustLevel.UNGROUNDED_CLAIM


def test_classify_output_with_model_name():
    algebra = TrustAlgebra()
    out = AgentOutput(agent_id="a1", output_text="x", model="gpt-4o")
    level = algebra.classify_output(out)
    assert level == TrustLevel.STRONG_MODEL_GENERATED


# ---------------------------------------------------------------------------
# TrustAlgebra.promote
# ---------------------------------------------------------------------------


def test_promote_valid():
    algebra = TrustAlgebra()
    claim = FactualClaim(text="X is 42", trust=TrustLevel.UNGROUNDED_CLAIM)
    result = algebra.promote(
        claim,
        target=TrustLevel.RAG_GROUNDED,
        evidence="Retrieved from knowledge base",
        channel=EvidenceChannel.RAG_RETRIEVAL,
    )
    assert result.success is True
    assert claim.trust == TrustLevel.RAG_GROUNDED


def test_promote_ceiling_violation():
    algebra = TrustAlgebra()
    claim = FactualClaim(text="X is 42", trust=TrustLevel.UNGROUNDED_CLAIM)
    # RAG_RETRIEVAL ceiling is RAG_GROUNDED (60), so TOOL_VERIFIED (80) exceeds it
    result = algebra.promote(
        claim,
        target=TrustLevel.TOOL_VERIFIED,
        evidence="some rag evidence",
        channel=EvidenceChannel.RAG_RETRIEVAL,
    )
    assert result.success is False
    assert claim.trust == TrustLevel.UNGROUNDED_CLAIM  # unchanged


def test_promote_no_evidence():
    algebra = TrustAlgebra()
    claim = FactualClaim(text="X is 42", trust=TrustLevel.UNGROUNDED_CLAIM)
    result = algebra.promote(
        claim,
        target=TrustLevel.TOOL_EXECUTED,
        evidence="",  # empty → rejected
        channel=EvidenceChannel.CODE_EXECUTION,
    )
    assert result.success is False
    assert "No evidence" in result.reason


# ---------------------------------------------------------------------------
# TrustAlgebra.demote
# ---------------------------------------------------------------------------


def test_demote():
    algebra = TrustAlgebra()
    claim = FactualClaim(text="X is 42", trust=TrustLevel.RAG_GROUNDED)
    new_trust = algebra.demote(claim, reason="failed verification")
    assert new_trust < TrustLevel.RAG_GROUNDED
    assert claim.trust == new_trust


def test_demote_at_minimum():
    algebra = TrustAlgebra()
    claim = FactualClaim(text="bad", trust=TrustLevel.SELF_CONTRADICTED)
    new_trust = algebra.demote(claim, reason="already at bottom")
    assert new_trust == TrustLevel.SELF_CONTRADICTED


# ---------------------------------------------------------------------------
# TrustAlgebra.cross_agent_confirm
# ---------------------------------------------------------------------------


def test_cross_agent_confirm_promotes():
    algebra = TrustAlgebra()
    claims = [
        FactualClaim(
            text="Paris is the capital",
            subject="Paris",
            predicate="is_a",
            value="capital",
            source_agent="a1",
            trust=TrustLevel.UNGROUNDED_CLAIM,
        ),
        FactualClaim(
            text="Paris is the capital",
            subject="Paris",
            predicate="is_a",
            value="capital",
            source_agent="a2",
            trust=TrustLevel.WEAK_MODEL_GENERATED,
        ),
    ]
    promoted = algebra.cross_agent_confirm(claims, threshold=2)
    assert len(promoted) == 2
    for c in claims:
        assert c.trust == TrustLevel.CROSS_AGENT_CONFIRMED


def test_cross_agent_confirm_insufficient_agents():
    algebra = TrustAlgebra()
    claims = [
        FactualClaim(
            text="Earth is round",
            subject="Earth",
            predicate="is_a",
            value="round",
            source_agent="a1",
        ),
    ]
    promoted = algebra.cross_agent_confirm(claims, threshold=2)
    assert len(promoted) == 0


# ---------------------------------------------------------------------------
# TrustAlgebra.audit_log
# ---------------------------------------------------------------------------


def test_audit_log_records_actions():
    algebra = TrustAlgebra()
    out = AgentOutput(
        agent_id="a1", output_text="test", tools_used=["code_exec"]
    )
    algebra.classify_output(out)
    log = algebra.audit_log
    assert len(log) >= 1
    assert log[0].action == "classify"


# ---------------------------------------------------------------------------
# ModelTrustProfile
# ---------------------------------------------------------------------------


def test_model_trust_profile_frontier():
    profile = ModelTrustProfile()
    assert profile.base_trust("gpt-4o") == TrustLevel.STRONG_MODEL_GENERATED
    assert profile.base_trust("claude-3.5-sonnet") == TrustLevel.STRONG_MODEL_GENERATED


def test_model_trust_profile_weak():
    profile = ModelTrustProfile()
    assert profile.base_trust("gpt-3.5-turbo") == TrustLevel.WEAK_MODEL_GENERATED
    assert profile.base_trust("llama-3-8b") == TrustLevel.WEAK_MODEL_GENERATED


def test_model_trust_profile_unknown():
    profile = ModelTrustProfile()
    assert profile.base_trust("my-custom-model-v1") == TrustLevel.UNGROUNDED_CLAIM


def test_model_trust_profile_register():
    profile = ModelTrustProfile()
    profile.register("my-custom", TrustLevel.TOOL_VERIFIED)
    assert profile.base_trust("my-custom") == TrustLevel.TOOL_VERIFIED
    profile.unregister("my-custom")
    assert profile.base_trust("my-custom") == TrustLevel.UNGROUNDED_CLAIM


# ---------------------------------------------------------------------------
# TrustCeilingEnforcer
# ---------------------------------------------------------------------------


def test_ceiling_enforcer_no_violation():
    enforcer = TrustCeilingEnforcer()
    claim = FactualClaim(text="x", trust=TrustLevel.RAG_GROUNDED)
    violations = enforcer.check([claim], channel=EvidenceChannel.RAG_RETRIEVAL)
    assert len(violations) == 0


def test_ceiling_enforcer_detects_violation():
    enforcer = TrustCeilingEnforcer()
    claim = FactualClaim(text="x", trust=TrustLevel.HUMAN_VERIFIED)
    violations = enforcer.check([claim], channel=EvidenceChannel.RAG_RETRIEVAL)
    assert len(violations) == 1
    assert violations[0].declared_trust == TrustLevel.HUMAN_VERIFIED
    assert violations[0].ceiling == TrustLevel.RAG_GROUNDED


def test_ceiling_enforcer_per_claim_channel():
    enforcer = TrustCeilingEnforcer()
    claim = FactualClaim(
        text="x",
        trust=TrustLevel.TOOL_VERIFIED,
        metadata={"channel": EvidenceChannel.LLM_GENERATION},
    )
    violations = enforcer.check([claim])  # no channel arg → reads from metadata
    assert len(violations) == 1
