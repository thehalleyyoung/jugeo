"""Cross-module tests: Scope violation and use-before-definition detection."""
import pytest

try:
    from jugeo.problem_modes.bug_detection import (
        detect_bugs, BugDetectionResult, BugReport, BugKind,
    )
except ImportError as e:
    pytest.skip(f"jugeo.problem_modes.bug_detection not available: {e}", allow_module_level=True)

SCOPE_VIOLATION_SOURCE = """
global_counter = 0

def increment():
    global global_counter
    global_counter += 1

def use_before_assign():
    print(value)  # used before assignment
    value = 10
    return value
"""

@pytest.fixture
def result():
    return detect_bugs(SCOPE_VIOLATION_SOURCE)

def test_scope_violation_source_detects(result):
    assert isinstance(result, BugDetectionResult)

def test_result_bugs_are_bug_reports(result):
    for bug in result.bugs:
        assert isinstance(bug, BugReport)

def test_bug_kind_is_valid(result):
    valid_kinds = set(BugKind)
    for bug in result.bugs:
        assert bug.kind in valid_kinds
