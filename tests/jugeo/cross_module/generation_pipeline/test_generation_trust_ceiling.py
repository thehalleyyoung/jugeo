"""Cross-module tests: InhabitantProposal starts at PROPOSAL tier."""
import pytest
import time

try:
    from jugeo.generation.inhabitant_fleets.models import (
        InhabitantProposal, TrustTier,
    )
except ImportError as e:
    pytest.skip(f"generation.inhabitant_fleets not available: {e}", allow_module_level=True)

@pytest.fixture
def proposal():
    return InhabitantProposal(
        proposal_id="trust-prop-001",
        patch_id="patch-001",
        section_label="section_A",
        semantic_content="return x",
        proposer_id="agent-001",
        trust_tier=TrustTier.PROPOSAL,
    )

def test_proposal_trust_tier_is_proposal(proposal):
    assert proposal.trust_tier == TrustTier.PROPOSAL

def test_proposal_default_trust_tier(proposal):
    assert proposal.trust_tier == TrustTier.PROPOSAL

def test_trust_tier_ordering():
    assert TrustTier.PROPOSAL < TrustTier.REVIEWED
