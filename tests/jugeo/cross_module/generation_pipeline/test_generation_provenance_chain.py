"""Cross-module tests: InhabitantProposal metadata and provenance."""
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
        proposal_id="prov-prop-001",
        patch_id="patch-001",
        section_label="section_C",
        semantic_content="def foo(): pass",
        proposer_id="provenance-agent",
        trust_tier=TrustTier.PROPOSAL,
    )

def test_proposal_has_metadata(proposal):
    assert isinstance(proposal.metadata, dict)

def test_proposal_created_at_is_float(proposal):
    assert isinstance(proposal.created_at, float)
    assert proposal.created_at > 0

def test_proposal_proposer_id_is_str(proposal):
    assert isinstance(proposal.proposer_id, str)
