"""Cross-module tests: InhabitantProposal construction."""
import pytest
import time

try:
    from jugeo.generation.inhabitant_fleets.models import (
        InhabitantProposal, ProposalStatus, TrustTier,
    )
except ImportError as e:
    pytest.skip(f"generation.inhabitant_fleets not available: {e}", allow_module_level=True)

@pytest.fixture
def proposal():
    return InhabitantProposal(
        proposal_id="prop-001",
        patch_id="patch-001",
        section_label="section_A",
        semantic_content="def foo(): return 42",
        proposer_id="agent-001",
        trust_tier=TrustTier.PROPOSAL,
    )

def test_proposal_construction(proposal):
    assert proposal is not None
    assert proposal.proposal_id == "prop-001"

def test_proposal_default_status_pending(proposal):
    assert proposal.status == ProposalStatus.PENDING

def test_proposal_accept(proposal):
    accepted = proposal.accept()
    assert accepted is not None
