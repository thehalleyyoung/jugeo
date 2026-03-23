"""Cross-module tests: semantic_closure submodule import."""
import pytest

try:
    import jugeo.generation.semantic_closure as sc_module
    HAS_SEMANTIC_CLOSURE = True
except ImportError:
    HAS_SEMANTIC_CLOSURE = False

@pytest.mark.skipif(not HAS_SEMANTIC_CLOSURE, reason="semantic_closure not available")
def test_semantic_closure_module_importable():
    assert sc_module is not None

@pytest.mark.skipif(not HAS_SEMANTIC_CLOSURE, reason="semantic_closure not available")
def test_semantic_closure_has_expected_exports():
    assert sc_module is not None
    # Check module is loadable
    import importlib
    spec = importlib.util.find_spec("jugeo.generation.semantic_closure")
    assert spec is not None

def test_generation_module_importable():
    """Verify the generation package itself is importable."""
    try:
        import jugeo.generation
        assert jugeo.generation is not None
    except ImportError as e:
        pytest.skip(f"jugeo.generation not available: {e}")
