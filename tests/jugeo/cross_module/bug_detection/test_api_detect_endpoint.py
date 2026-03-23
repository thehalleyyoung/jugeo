"""Cross-module tests: api_detect() dict-in/dict-out interface."""
import pytest

try:
    from jugeo.problem_modes.bug_detection import api_detect
except ImportError as e:
    pytest.skip(f"jugeo.problem_modes.bug_detection not available: {e}", allow_module_level=True)

SOURCE = """
def example():
    x = 1
    return x + 1
"""

def test_api_detect_returns_dict():
    result = api_detect({"source": SOURCE})
    assert isinstance(result, dict)

def test_api_detect_has_bugs_key():
    result = api_detect({"source": SOURCE})
    assert "bugs" in result

def test_api_detect_has_status_key():
    result = api_detect({"source": SOURCE})
    assert "status" in result or "ok" in result

def test_api_detect_empty_source():
    result = api_detect({"source": ""})
    assert isinstance(result, dict)
