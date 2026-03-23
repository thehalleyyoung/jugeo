"""Cross-module tests: BugReport provenance dict and result summary."""
import pytest

try:
    from jugeo.problem_modes.bug_detection import detect_bugs, BugReport
except ImportError as e:
    pytest.skip(f"jugeo.problem_modes.bug_detection not available: {e}", allow_module_level=True)

SOURCE = """
def example():
    a = undeclared
    b = also_undeclared
    return a + b
"""

@pytest.fixture
def result():
    return detect_bugs(SOURCE)

def test_bug_has_provenance(result):
    for bug in result.bugs:
        assert hasattr(bug, "provenance")

def test_provenance_is_dict(result):
    for bug in result.bugs:
        assert isinstance(bug.provenance, dict)

def test_detection_result_summary(result):
    summary = result.summary()
    assert summary is not None
    assert isinstance(summary, (str, dict))
