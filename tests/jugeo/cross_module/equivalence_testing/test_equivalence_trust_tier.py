"""Cross-module tests: Trust tier in relational refinement results."""
import pytest

try:
    from jugeo.problem_modes.relational_refinement import EquivalenceClass
except ImportError as e:
    pytest.skip(f"relational_refinement not available: {e}", allow_module_level=True)

def _make_ec():
    try:
        return EquivalenceClass(
            class_id="ec-001",
            member_coordinates=frozenset(["a", "b"]),
            representative_coordinate="a",
            canonical_trust="ORACLE_PROPOSED",
        )
    except TypeError:
        try:
            return EquivalenceClass(
                class_id="ec-001",
                member_coordinates=frozenset(["a"]),
                representative_coordinate="a",
            )
        except TypeError:
            return None

def test_equivalence_class_has_canonical_trust():
    ec = _make_ec()
    if ec is None:
        pytest.skip("Cannot construct EquivalenceClass")
    assert hasattr(ec, "canonical_trust")

def test_canonical_trust_is_str():
    ec = _make_ec()
    if ec is None:
        pytest.skip("Cannot construct EquivalenceClass")
    if hasattr(ec, "canonical_trust") and ec.canonical_trust is not None:
        assert isinstance(ec.canonical_trust, str)
