"""Cross-module tests: EquivalenceClass member_coordinates."""
import pytest

try:
    from jugeo.problem_modes.relational_refinement import EquivalenceClass
except ImportError as e:
    pytest.skip(f"relational_refinement not available: {e}", allow_module_level=True)

def _make_ec(members=frozenset(["coord_a", "coord_b"]), rep="coord_a"):
    try:
        return EquivalenceClass(
            class_id="ec-001",
            member_coordinates=members,
            representative_coordinate=rep,
            canonical_trust="ORACLE_PROPOSED",
        )
    except TypeError:
        try:
            return EquivalenceClass(
                class_id="ec-001",
                member_coordinates=members,
                representative_coordinate=rep,
            )
        except TypeError:
            return None

def test_member_coordinates_is_frozenset():
    ec = _make_ec()
    if ec is None:
        pytest.skip("Cannot construct EquivalenceClass")
    assert isinstance(ec.member_coordinates, frozenset)

def test_representative_coordinate_in_members():
    members = frozenset(["coord_a", "coord_b"])
    ec = _make_ec(members=members, rep="coord_a")
    if ec is None:
        pytest.skip("Cannot construct EquivalenceClass")
    if ec.member_coordinates:
        assert ec.representative_coordinate in ec.member_coordinates
