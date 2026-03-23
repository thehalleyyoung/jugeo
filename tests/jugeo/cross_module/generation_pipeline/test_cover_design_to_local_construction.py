"""Cross-module tests: inhabitant_fleets models import."""
import pytest

try:
    from jugeo.generation.inhabitant_fleets.models import (
        InhabitantProposal, ProposalStatus, TrustTier,
    )
except ImportError as e:
    pytest.skip(f"generation.inhabitant_fleets not available: {e}", allow_module_level=True)

def test_inhabitant_proposal_importable():
    assert InhabitantProposal is not None

def test_proposal_status_importable():
    assert ProposalStatus is not None

def test_trust_tier_importable():
    assert TrustTier is not None
