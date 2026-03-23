"""Cross-module tests: TrustAlgebra and TrustLevel operations."""
import pytest

try:
    from jugeo.evidence.trust import TrustLevel, TrustAlgebra
except ImportError as e:
    pytest.skip(f"jugeo.evidence.trust not available: {e}", allow_module_level=True)

def test_trust_level_importable():
    assert TrustLevel is not None

def test_trust_level_has_expected_values():
    assert hasattr(TrustLevel, "ORACLE_PROPOSED")

def test_trust_level_rank_index():
    result = TrustLevel.ORACLE_PROPOSED.rank_index()
    assert isinstance(result, int)

def test_trust_level_label():
    result = TrustLevel.ORACLE_PROPOSED.label()
    assert isinstance(result, str)

def test_trust_algebra_importable():
    assert TrustAlgebra is not None
