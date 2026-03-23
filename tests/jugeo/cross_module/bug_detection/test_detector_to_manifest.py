"""Cross-module tests: BugReport → wire_to_manifest integration."""
import pytest

try:
    from jugeo.problem_modes.bug_detection import (
        detect_bugs, wire_to_manifest,
    )
except ImportError as e:
    pytest.skip(f"jugeo.problem_modes.bug_detection not available: {e}", allow_module_level=True)

SOURCE = """
def foo(x):
    if x == None:
        return True
    return x + 1
"""

@pytest.fixture
def detection_result():
    return detect_bugs(SOURCE)

def test_wire_to_manifest_no_exception(detection_result):
    # Should handle None manifest gracefully
    wire_to_manifest(detection_result, None)

def test_wire_to_manifest_with_dict(detection_result):
    manifest = {}
    wire_to_manifest(detection_result, manifest)
