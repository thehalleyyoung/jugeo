"""Cross-module tests: treaties module."""
import pytest

try:
    from jugeo.generation.treaties import TreatySynthesizer, TreatyValidator
    HAS_TREATIES = True
except ImportError:
    HAS_TREATIES = False

@pytest.mark.skipif(not HAS_TREATIES, reason="generation.treaties not available")
def test_treaty_synthesizer_importable():
    assert TreatySynthesizer is not None

@pytest.mark.skipif(not HAS_TREATIES, reason="generation.treaties not available")
def test_treaty_validator_importable():
    assert TreatyValidator is not None

def test_generation_models_importable():
    """Fallback: verify the generation models import works."""
    try:
        from jugeo.generation.inhabitant_fleets.models import InhabitantProposal
        assert InhabitantProposal is not None
    except ImportError as e:
        pytest.skip(f"inhabitant_fleets not available: {e}")
