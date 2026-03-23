"""Cross-module tests: CounterexampleRecord as first-class witness."""
import pytest

try:
    from jugeo.problem_modes.repair_semantics import CounterexampleRecord
except ImportError as e:
    pytest.skip(f"repair_semantics not available: {e}", allow_module_level=True)

@pytest.fixture
def cx():
    return CounterexampleRecord(
        record_id="witness-cx-001",
        coordinate="module.function:line_42",
        context={"scope": "local"},
        assignment={"x": -1, "y": 0},
        witness={"failing_input": {"x": -1}},
        severity=0.7,
        trust_tier="ORACLE_PROPOSED",
    )

def test_counterexample_record_construction(cx):
    assert cx is not None
    assert cx.record_id == "witness-cx-001"

def test_counterexample_to_repair_hints(cx):
    hints = cx.to_repair_hints()
    assert hints is not None
    # Should be some kind of sequence
    assert hasattr(hints, "__iter__") or isinstance(hints, dict)

def test_counterexample_classify_failure(cx):
    result = cx.classify_failure()
    assert isinstance(result, str)

def test_counterexample_severity_is_float(cx):
    assert isinstance(cx.severity, float)
