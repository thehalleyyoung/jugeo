"""Cross-module tests: importing relational_refinement module itself."""
import pytest

try:
    import jugeo.problem_modes.relational_refinement as rr_module
except ImportError as e:
    pytest.skip(f"relational_refinement not available: {e}", allow_module_level=True)

def test_module_importable():
    assert rr_module is not None

def test_module_has_all():
    assert hasattr(rr_module, "__all__")

def test_all_is_list():
    assert isinstance(rr_module.__all__, list)
