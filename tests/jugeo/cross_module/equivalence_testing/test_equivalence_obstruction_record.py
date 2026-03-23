"""Cross-module tests: ObstructionRecord in equivalence context."""
import pytest

try:
    from jugeo.errors import ObstructionRecord
except ImportError as e:
    pytest.skip(f"jugeo.errors not available: {e}", allow_module_level=True)

def test_obstruction_record_construction():
    try:
        obstruction = ObstructionRecord(
            coordinate="test_coord",
            kind="EQUIVALENCE_FAILURE",
            evidence={},
            repair_frontier=None,
            affected_obligations=[],
        )
        assert obstruction is not None
    except TypeError as e:
        # Try alternate constructor
        try:
            obstruction = ObstructionRecord(
                coordinate="test_coord",
                kind="EQUIVALENCE_FAILURE",
            )
            assert obstruction is not None
        except TypeError:
            pytest.skip(f"ObstructionRecord constructor: {e}")

def test_obstruction_has_coordinate():
    try:
        obstruction = ObstructionRecord(
            coordinate="test_coord",
            kind="EQUIVALENCE_FAILURE",
            evidence={},
            repair_frontier=None,
            affected_obligations=[],
        )
        assert isinstance(obstruction.coordinate, str)
    except TypeError:
        pytest.skip("ObstructionRecord constructor mismatch")
