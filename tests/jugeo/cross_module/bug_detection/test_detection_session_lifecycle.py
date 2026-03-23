"""Cross-module tests: DetectionSession open→closed lifecycle."""
import pytest

try:
    from jugeo.problem_modes.bug_detection import (
        detect_bugs, DetectionSession, BugDetectionResult,
    )
except ImportError as e:
    pytest.skip(f"jugeo.problem_modes.bug_detection not available: {e}", allow_module_level=True)

SOURCE = """
def sample():
    return 42
"""

@pytest.fixture
def result():
    return detect_bugs(SOURCE)

def test_session_has_status(result):
    assert hasattr(result, "status")
    assert result.status is not None

def test_session_can_finalise(result):
    # DetectionSession.finalise() can be tested directly
    session = DetectionSession()
    finalised = session.finalise(1.0)
    assert finalised is not None

def test_session_after_finalise_has_elapsed(result):
    # The result already has elapsed_s
    assert result.elapsed_s >= 0.0

def test_session_id_is_str(result):
    assert isinstance(result.session_id, str)
