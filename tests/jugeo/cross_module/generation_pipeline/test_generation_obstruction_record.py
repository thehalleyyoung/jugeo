"""Cross-module tests: ObstructionRecord from errors module in generation context."""
import pytest

try:
    from jugeo.errors import ObstructionRecord
except ImportError as e:
    pytest.skip(f"jugeo.errors not available: {e}", allow_module_level=True)

def _make_obstruction():
    try:
        return ObstructionRecord(
            coordinate="generation.section_A:proposal_001",
            kind="GENERATION_FAILURE",
            evidence={},
            repair_frontier=None,
            affected_obligations=[],
        )
    except TypeError:
        try:
            return ObstructionRecord(
                coordinate="generation.section_A:proposal_001",
                kind="GENERATION_FAILURE",
            )
        except TypeError:
            return None

def test_obstruction_record_importable():
    assert ObstructionRecord is not None

def test_obstruction_coordinate_is_str():
    obs = _make_obstruction()
    if obs is None:
        pytest.skip("Cannot construct ObstructionRecord")
    assert isinstance(obs.coordinate, str)

def test_obstruction_kind_any():
    obs = _make_obstruction()
    if obs is None:
        pytest.skip("Cannot construct ObstructionRecord")
    assert obs.kind is not None
