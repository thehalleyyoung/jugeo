"""Cross-module tests: BugDetectionResult → wire_to_evidence_pipeline."""
import pytest

try:
    from jugeo.problem_modes.bug_detection import (
        detect_bugs, wire_to_evidence_pipeline, BugDetectionResult,
    )
except ImportError as e:
    pytest.skip(f"jugeo.problem_modes.bug_detection not available: {e}", allow_module_level=True)

SOURCE_WITH_BUGS = """
x = undefined_var
def foo():
    pass
"""

CLEAN_SOURCE = """
def add(a, b):
    return a + b
"""

@pytest.fixture
def detection_result():
    return detect_bugs(SOURCE_WITH_BUGS)

@pytest.fixture
def clean_result():
    return detect_bugs(CLEAN_SOURCE)

def test_wire_to_evidence_returns_dict(detection_result):
    result = wire_to_evidence_pipeline(detection_result)
    assert isinstance(result, dict)

def test_wire_result_has_bugs_key(detection_result):
    result = wire_to_evidence_pipeline(detection_result)
    assert "items" in result

def test_wire_result_bugs_is_list(detection_result):
    result = wire_to_evidence_pipeline(detection_result)
    assert isinstance(result["items"], list)

def test_wire_result_no_exception_on_empty(clean_result):
    result = wire_to_evidence_pipeline(clean_result)
    assert isinstance(result, dict)

def test_wire_result_preserves_count(detection_result):
    result = wire_to_evidence_pipeline(detection_result)
    assert len(result["items"]) == len(detection_result.bugs)
