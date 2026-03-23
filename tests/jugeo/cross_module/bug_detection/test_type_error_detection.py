"""Cross-module tests: Type annotation violation detection."""
import pytest

try:
    from jugeo.problem_modes.bug_detection import (
        detect_bugs, BugDetectionResult,
    )
except ImportError as e:
    pytest.skip(f"jugeo.problem_modes.bug_detection not available: {e}", allow_module_level=True)

TYPE_ERROR_SOURCE = """
def greet(name: int) -> str:
    # name should be int but we use it as str
    greeting: str = "Hello, " + name  # type: ignore
    return greeting

def process(items: list) -> int:
    return items  # wrong return type
"""

@pytest.fixture
def result():
    return detect_bugs(TYPE_ERROR_SOURCE)

def test_type_error_source_parses(result):
    assert result is not None

def test_type_error_detection_result_type(result):
    assert isinstance(result, BugDetectionResult)

def test_result_has_elapsed_time(result):
    assert result.elapsed_s >= 0
