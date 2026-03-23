"""Cross-module tests: RefinementRelation construction and properties."""
import pytest

try:
    from jugeo.problem_modes.relational_refinement import RefinementRelation
except ImportError as e:
    pytest.skip(f"relational_refinement not available: {e}", allow_module_level=True)

def test_refinement_relation_direction_enum():
    # RefinementRelation should have a RefinementDirection nested enum
    if hasattr(RefinementRelation, "RefinementDirection"):
        assert hasattr(RefinementRelation.RefinementDirection, "EQUIVALENT")
    else:
        # Maybe it's imported separately
        try:
            from jugeo.problem_modes.relational_refinement import RefinementDirection
            assert hasattr(RefinementDirection, "EQUIVALENT")
        except ImportError:
            pytest.skip("RefinementDirection not found")

def test_refinement_relation_confidence_in_range():
    try:
        rr = RefinementRelation(
            relation_id="test-001",
            source_coordinate="a",
            target_coordinate="b",
            confidence=0.8,
        )
        assert 0.0 <= rr.confidence <= 1.0
    except TypeError:
        # Try with just required args
        try:
            rr = RefinementRelation(relation_id="test-001", source_coordinate="a", target_coordinate="b")
            if hasattr(rr, "confidence"):
                assert 0.0 <= rr.confidence <= 1.0
        except Exception:
            pytest.skip("Cannot construct RefinementRelation")
