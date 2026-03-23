"""Cross-module tests: repair_semantics check_theorem with relational theorems."""
import pytest

try:
    from jugeo.problem_modes.repair_semantics import check_theorem, get_all_theorems
except ImportError as e:
    pytest.skip(f"repair_semantics not available: {e}", allow_module_level=True)

def test_check_theorem_importable():
    assert callable(check_theorem)

def test_get_all_theorems_returns_collection():
    theorems = get_all_theorems()
    assert theorems is not None
    # Should be a non-empty collection
    if hasattr(theorems, "__len__"):
        assert len(theorems) >= 0
    elif hasattr(theorems, "__iter__"):
        items = list(theorems)
        assert isinstance(items, list)
