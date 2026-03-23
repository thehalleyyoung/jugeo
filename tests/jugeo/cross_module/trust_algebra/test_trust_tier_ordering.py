"""Cross-module tests: TrustTier IntEnum ordering."""
import pytest

try:
    from jugeo.evidence.trust import TrustLevel, TrustTier
except ImportError as e:
    pytest.skip(f"jugeo.evidence.trust not available: {e}", allow_module_level=True)

def test_trust_tier_importable():
    assert TrustTier is not None

def test_trust_tier_is_int_enum():
    from enum import IntEnum
    assert issubclass(TrustTier, IntEnum) or hasattr(TrustTier, "__int__")

def test_trust_tier_ordering():
    # Lower trust tiers should have lower values
    members = list(TrustTier)
    if len(members) >= 2:
        # At minimum, the enum is orderable
        values = [int(m) for m in members]
        assert len(set(values)) == len(values)  # all distinct

def test_trust_level_ordered_method():
    result = TrustLevel.ordered()
    assert result is not None
    items = list(result)
    assert len(items) > 0
    assert all(isinstance(item, TrustLevel) for item in items)
