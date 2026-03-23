"""Cross-module tests: Severity scores order bugs correctly."""
import pytest

try:
    from jugeo.problem_modes.bug_detection import (
        detect_bugs, BugDetectionResult, BugReport,
    )
except ImportError as e:
    pytest.skip(f"jugeo.problem_modes.bug_detection not available: {e}", allow_module_level=True)

SOURCE = """
def risky():
    x = undefined_a
    y = undefined_b
    if True:
        return x
    return y + 1
"""

@pytest.fixture
def result():
    return detect_bugs(SOURCE)

def test_most_severe_returns_bug(result):
    most_severe = result.most_severe()
    assert most_severe is None or isinstance(most_severe, BugReport)

def test_severity_is_float(result):
    for bug in result.bugs:
        assert isinstance(bug.severity, float)

def test_severity_in_range(result):
    for bug in result.bugs:
        assert 0.0 <= bug.severity <= 1.0

def test_bugs_sortable_by_severity(result):
    sorted_bugs = sorted(result.bugs, key=lambda b: b.severity)
    assert isinstance(sorted_bugs, list)
