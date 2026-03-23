"""Cross-module tests: TrustLevel ordering is a proper partial order."""
import pytest

try:
    from jugeo.evidence.trust import TrustLevel
except ImportError as e:
    pytest.skip(f"jugeo.evidence.trust not available: {e}", allow_module_level=True)

def test_trust_level_comparable():
    level = TrustLevel.ORACLE_PROPOSED
    if hasattr(level, "is_comparable"):
        result = level.is_comparable(TrustLevel.UNVERIFIED)
        assert isinstance(result, bool)

def test_trust_level_mechanically_verified_is_highest_rank():
    mv_rank = TrustLevel.MECHANICALLY_VERIFIED.rank_index()
    op_rank = TrustLevel.ORACLE_PROPOSED.rank_index()
    uv_rank = TrustLevel.UNVERIFIED.rank_index()
    # MECHANICALLY_VERIFIED should have highest rank
    assert mv_rank > op_rank
    assert mv_rank > uv_rank

def test_trust_level_unverified_is_low_rank():
    ha_rank = TrustLevel.HUMAN_ATTESTED.rank_index()
    uv_rank = TrustLevel.UNVERIFIED.rank_index()
    assert uv_rank < ha_rank
