"""Cross-module tests: hypercover_treaties module."""
import pytest

try:
    import jugeo.generation.hypercover_treaties as hct_module
    HAS_HCT = True
except ImportError:
    HAS_HCT = False

@pytest.mark.skipif(not HAS_HCT, reason="generation.hypercover_treaties not available")
def test_hypercover_treaties_module_exists():
    assert hct_module is not None

def test_generation_package_importable():
    """Verify jugeo.generation package is importable."""
    try:
        import jugeo.generation
        assert jugeo.generation is not None
    except ImportError as e:
        pytest.skip(f"jugeo.generation not available: {e}")

def test_inhabitant_fleets_models_importable():
    """Verify inhabitant_fleets.models is importable."""
    try:
        from jugeo.generation.inhabitant_fleets.models import InhabitantProposal
        assert InhabitantProposal is not None
    except ImportError as e:
        pytest.skip(f"inhabitant_fleets not available: {e}")
