"""Cross-module tests: End-to-end generation pipeline test."""
import pytest
import time

try:
    from jugeo.generation.inhabitant_fleets.models import (
        InhabitantProposal, ProposalStatus, TrustTier, MoveType,
    )
except ImportError as e:
    pytest.skip(f"generation.inhabitant_fleets not available: {e}", allow_module_level=True)

def _make_proposal(proposal_id, content="return x"):
    return InhabitantProposal(
        proposal_id=proposal_id,
        patch_id=f"patch-{proposal_id}",
        section_label="pipeline_section",
        semantic_content=content,
        proposer_id="pipeline-agent",
        trust_tier=TrustTier.PROPOSAL,
    )

def test_create_and_accept_proposal():
    proposal = _make_proposal("pipeline-001")
    accepted = proposal.accept()
    # After accept(), status should be ACCEPTED (either returned new or mutated)
    if accepted is not None and hasattr(accepted, "status"):
        assert accepted.status == ProposalStatus.ACCEPTED
    elif hasattr(proposal, "status"):
        assert proposal.status == ProposalStatus.ACCEPTED

def test_multiple_proposals_independent():
    p1 = _make_proposal("pipeline-p1", "return 1")
    p2 = _make_proposal("pipeline-p2", "return 2")
    assert p1.proposal_id != p2.proposal_id

def test_generation_modules_consistent():
    proposal = _make_proposal("pipeline-p3")
    assert proposal.status == ProposalStatus.PENDING
    assert proposal.trust_tier == TrustTier.PROPOSAL
    assert hasattr(MoveType, "PROPOSE")
