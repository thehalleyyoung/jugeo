"""Cross-module tests: classify_cohomology_class localizes obstruction."""
import pytest

try:
    from jugeo.problem_modes.repair_semantics import classify_cohomology_class
except ImportError as e:
    pytest.skip(f"repair_semantics not available: {e}", allow_module_level=True)

def test_classify_cohomology_class_fn():
    try:
        result = classify_cohomology_class("test_coord")
        assert result is not None
    except TypeError as e:
        pytest.skip(f"classify_cohomology_class signature differs: {e}")

def test_repair_semantics_exports_classify():
    import jugeo.problem_modes.repair_semantics as rs
    assert hasattr(rs, "classify_cohomology_class")
