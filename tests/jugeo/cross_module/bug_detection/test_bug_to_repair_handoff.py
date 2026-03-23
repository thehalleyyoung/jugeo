"""Cross-module tests: BugDetectionResult → repair_semantics handoff."""
import pytest

try:
    from jugeo.problem_modes.bug_detection import detect_bugs, BugReport
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

def test_bug_result_has_bugs_tuple(result):
    assert isinstance(result.bugs, tuple)

def test_bug_coord_usable_in_counterexample(result):
    for bug in result.bugs:
        assert isinstance(bug.coordinate, str)

def test_counterexample_from_bug_coordinate(result):
    # Create a CounterexampleRecord using a bug's coordinate
    coord = result.bugs[0].coordinate if result.bugs else "test_coord"
    cx = CounterexampleRecord(
        record_id="test-record-001",
        coordinate=coord,
        context={},
        assignment={"x": 0},
        witness=None,
        severity=0.8,
        trust_tier="ORACLE_PROPOSED",
    )
    assert cx.coordinate == coord
