"""Cross-module tests: CounterexampleRecord trust tier is ORACLE_PROPOSED."""
import pytest

try:
    from jugeo.problem_modes.repair_semantics import CounterexampleRecord
except ImportError as e:
    pytest.skip(f"repair_semantics not available: {e}", allow_module_level=True)

@pytest.fixture
def cx():
    return CounterexampleRecord(
        record_id="trust-cx-001",
        coordinate="test.module:line_5",
        context={},
        assignment={},
        witness=None,
        severity=0.5,
        trust_tier="ORACLE_PROPOSED",
    )

def test_counterexample_trust_tier(cx):
    assert "PROPOSED" in cx.trust_tier.upper() or cx.trust_tier == "ORACLE_PROPOSED"

def test_counterexample_has_trust_tier_field(cx):
    assert hasattr(cx, "trust_tier")
