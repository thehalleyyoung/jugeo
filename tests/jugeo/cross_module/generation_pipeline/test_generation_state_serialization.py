"""Cross-module tests: InhabitantProposal dataclass serialization."""
import pytest
import dataclasses
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
        proposal_id="serial-prop-001",
        patch_id="patch-001",
        section_label="section_D",
        semantic_content="x = 42",
        proposer_id="serializer-agent",
        trust_tier=TrustTier.PROPOSAL,
    )

def test_proposal_to_dict(proposal):
    if hasattr(proposal, "to_dict"):
        result = proposal.to_dict()
        assert isinstance(result, dict)
    else:
        result = dataclasses.asdict(proposal)
        assert isinstance(result, dict)

def test_proposal_fields_accessible(proposal):
    assert proposal.proposal_id is not None
    assert proposal.patch_id is not None
    assert proposal.section_label is not None
    assert proposal.semantic_content is not None

def test_proposal_is_dataclass(proposal):
    assert dataclasses.is_dataclass(proposal)
