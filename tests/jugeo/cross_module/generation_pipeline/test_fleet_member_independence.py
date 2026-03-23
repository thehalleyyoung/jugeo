"""Cross-module tests: ProposalStatus and MoveType enums."""
import pytest

try:
    from jugeo.generation.inhabitant_fleets.models import (
        ProposalStatus, MoveType, SeverityLevel,
    )
except ImportError as e:
    pytest.skip(f"generation.inhabitant_fleets not available: {e}", allow_module_level=True)

def test_proposal_status_values():
    assert hasattr(ProposalStatus, "PENDING")
    assert hasattr(ProposalStatus, "ACCEPTED")
    assert hasattr(ProposalStatus, "REJECTED")

def test_move_type_values():
    assert hasattr(MoveType, "PROPOSE")
    assert hasattr(MoveType, "RETRACT")
    assert hasattr(MoveType, "REFINE")

def test_severity_level_values():
    assert hasattr(SeverityLevel, "LOW")
    assert hasattr(SeverityLevel, "MEDIUM")
    assert hasattr(SeverityLevel, "HIGH")
    assert hasattr(SeverityLevel, "CRITICAL")
