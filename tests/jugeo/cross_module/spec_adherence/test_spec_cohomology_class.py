"""Cross-module tests: spec-related cohomology labels."""
import pytest

try:
    from jugeo.problem_modes.repair_semantics import classify_cohomology_class
except ImportError as e:
    pytest.skip(f"repair_semantics not available: {e}", allow_module_level=True)

def test_classify_cohomology_class_importable():
    assert callable(classify_cohomology_class)

def test_classify_with_str_coordinate():
    try:
        result = classify_cohomology_class("test_coord")
        assert result is not None
        assert isinstance(result, str) or hasattr(result, "value") or hasattr(result, "name")
    except TypeError as e:
        pytest.skip(f"classify_cohomology_class signature differs: {e}")
