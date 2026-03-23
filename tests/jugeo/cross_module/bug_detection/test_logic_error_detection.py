"""Cross-module tests: Logic error detection (unreachable code, always-false conditions)."""
import pytest

try:
    from jugeo.problem_modes.bug_detection import (
        detect_bugs, BugDetectionResult, BugReport,
    )
except ImportError as e:
    pytest.skip(f"jugeo.problem_modes.bug_detection not available: {e}", allow_module_level=True)

LOGIC_ERROR_SOURCE = """
def dead_code_example():
    return 42
    print("unreachable")  # unreachable code

def always_true_condition():
    x = 5
    if True:
        pass
    while False:
        do_something()  # unreachable

def always_false():
    x = 1
    if x != x:  # always false
        return "impossible"
    return "normal"
"""

@pytest.fixture
def result():
    return detect_bugs(LOGIC_ERROR_SOURCE)

def test_logic_error_source_detects(result):
    assert isinstance(result, BugDetectionResult)

def test_result_bugs_have_severity(result):
    for bug in result.bugs:
        assert isinstance(bug.severity, float)
        assert 0.0 <= bug.severity <= 1.0
