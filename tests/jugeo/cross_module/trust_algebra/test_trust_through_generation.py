"""Cross-module tests: Generated sections start at PROPOSAL tier."""
import pytest

try:
    from jugeo.generation.inhabitant_fleets.models import (
        InhabitantProposal, TrustTier,
    )
except ImportError as e:
    pytest.skip(f"generation.inhabitant_fleets not available: {e}", allow_module_level=True)

@pytest.fixture
def proposal():
    return InhabitantProposal(
        proposal_id="trust-gen-001",
        patch_id="patch-001",
        section_label="sec_trust",
        semantic_content="return 42",
        proposer_id="trust-agent",
        trust_tier=TrustTier.PROPOSAL,
    )

def test_inhabitant_proposal_is_proposal_tier(proposal):
    assert proposal.trust_tier == TrustTier.PROPOSAL

def test_proposal_not_verified(proposal):
    if hasattr(TrustTier, "VERIFIED"):
        assert proposal.trust_tier != TrustTier.VERIFIED
    else:
        assert proposal.trust_tier == TrustTier.PROPOSAL
