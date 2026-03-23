"""Cross-module tests: Trust violation detection in source."""
import pytest

try:
    from jugeo.problem_modes.bug_detection import (
        detect_bugs, BugDetectionResult,
    )
except ImportError as e:
    pytest.skip(f"jugeo.problem_modes.bug_detection not available: {e}", allow_module_level=True)

TRUST_VIOLATION_SOURCE = """
# TRUST: MECHANICALLY_VERIFIED  <- silent promotion attempt
def compute(x):
    # TRUST_OVERRIDE: skip_verification
    return x * 2

# trust_tier = "ORACLE_PROPOSED"  # proper usage
result = compute(5)
"""

@pytest.fixture
def result():
    return detect_bugs(TRUST_VIOLATION_SOURCE)

def test_trust_violation_source_parses(result):
    assert result is not None

def test_result_is_bug_detection_result(result):
    assert isinstance(result, BugDetectionResult)

def test_has_trust_violation_method(result):
    val = result.has_trust_violation()
    assert isinstance(val, bool)
