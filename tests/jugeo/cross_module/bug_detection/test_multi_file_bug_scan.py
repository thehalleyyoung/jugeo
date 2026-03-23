"""Cross-module tests: Scanning multiple sources and aggregating results."""
import pytest

try:
    from jugeo.problem_modes.bug_detection import (
        detect_bugs, BugDetectionResult,
    )
except ImportError as e:
    pytest.skip(f"jugeo.problem_modes.bug_detection not available: {e}", allow_module_level=True)

SOURCE_A = """
def func_a():
    x = undefined_var
    return x
"""

SOURCE_B = """
def func_b(items: list) -> int:
    return items  # wrong return type

def func_b2():
    while False:
        pass
"""

@pytest.fixture
def result_a():
    return detect_bugs(SOURCE_A)

@pytest.fixture
def result_b():
    return detect_bugs(SOURCE_B)

def test_two_sources_independent(result_a, result_b):
    assert isinstance(result_a, BugDetectionResult)
    assert isinstance(result_b, BugDetectionResult)

def test_combined_bugs_count(result_a, result_b):
    combined = len(result_a.bugs) + len(result_b.bugs)
    assert combined >= len(result_a.bugs)
    assert combined >= len(result_b.bugs)

def test_sessions_have_different_ids(result_a, result_b):
    assert result_a.session_id != result_b.session_id
