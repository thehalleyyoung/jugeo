"""Cross-module tests: InhabitantProposal competition."""
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
        proposal_id="comp-prop-001",
        patch_id="patch-001",
        section_label="section_B",
        semantic_content="def bar(): return 0",
        proposer_id="agent-001",
        trust_tier=TrustTier.PROPOSAL,
    )

def test_proposal_reject(proposal):
    rejected = proposal.reject("Superseded by better proposal")
    assert rejected is not None

def test_proposal_rejected_status(proposal):
    rejected = proposal.reject("reason")
    # After reject, status should be REJECTED
    if hasattr(rejected, "status"):
        assert rejected.status == ProposalStatus.REJECTED
    else:
        # In-place mutation case
        assert proposal.status == ProposalStatus.REJECTED

def test_competing_proposals_list(proposal):
    assert isinstance(proposal.competing_proposals, list)
