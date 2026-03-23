"""Cross-module tests: BugDetectionResult → RepairPlan workflow."""
import pytest

try:
    from jugeo.problem_modes.bug_detection import detect_bugs
    from jugeo.problem_modes.repair_semantics import CounterexampleRecord
except ImportError as e:
    pytest.skip(f"Required modules not available: {e}", allow_module_level=True)

SOURCE = """
def compute(x):
    result = x / 0  # potential division by zero
    return result
"""

@pytest.fixture
def result():
    return detect_bugs(SOURCE)

@pytest.fixture
def cx(result):
    coord = result.bugs[0].coordinate if result.bugs else "test_coord"
    return CounterexampleRecord(
        record_id="test-cx-001",
        coordinate=coord,
        context={},
        assignment={"x": 0},
        witness=None,
        severity=0.8,
        trust_tier="ORACLE_PROPOSED",
    )

def test_bug_result_bugs_are_tuple(result):
    assert isinstance(result.bugs, tuple)

def test_create_counterexample_from_bug(result):
    coord = result.bugs[0].coordinate if result.bugs else "test_coord"
    cx = CounterexampleRecord(
        record_id="test-cx-001",
        coordinate=coord,
        context={},
        assignment={},
        witness=None,
        severity=0.5,
        trust_tier="ORACLE_PROPOSED",
    )
    assert cx is not None

def test_counterexample_to_obstruction(cx):
    obstruction = cx.to_obstruction_record()
    assert obstruction is not None

def test_counterexample_is_genuine_method(cx):
    result = cx.is_genuine()
    assert isinstance(result, bool)
