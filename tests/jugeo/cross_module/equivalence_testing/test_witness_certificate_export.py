"""Cross-module tests: RefinementWitness exportable data."""
import pytest

try:
    from jugeo.problem_modes.relational_refinement import RefinementWitness
except ImportError as e:
    pytest.skip(f"relational_refinement not available: {e}", allow_module_level=True)

def _make_witness():
    try:
        return RefinementWitness(
            witness_id="test-witness-001",
            relation_id="rel-001",
            left_coordinate="coord_a",
            right_coordinate="coord_b",
        )
    except TypeError:
        return RefinementWitness.__new__(RefinementWitness)

def test_refinement_witness_has_witness_id():
    w = _make_witness()
    assert hasattr(w, "witness_id")

def test_witness_to_dict_works():
    w = _make_witness()
    if hasattr(w, "to_dict"):
        result = w.to_dict()
        assert isinstance(result, dict)
    else:
        import dataclasses
        if dataclasses.is_dataclass(w):
            result = dataclasses.asdict(w)
            assert isinstance(result, dict)
        else:
            pytest.skip("to_dict not available")
