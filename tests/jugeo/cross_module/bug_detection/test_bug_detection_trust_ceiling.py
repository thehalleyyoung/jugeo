"""Cross-module tests: Detected bugs stay at ORACLE_PROPOSED trust tier."""
import pytest

try:
    from jugeo.problem_modes.bug_detection import detect_bugs, BugReport
except ImportError as e:
    pytest.skip(f"jugeo.problem_modes.bug_detection not available: {e}", allow_module_level=True)

SOURCE = """
def buggy():
    x = undefined
    if True:
        return x
    return None
"""

@pytest.fixture
def result():
    return detect_bugs(SOURCE)

def test_bugs_have_trust_tier(result):
    for bug in result.bugs:
        assert hasattr(bug, "trust_tier")

def test_bug_trust_tier_is_oracle_proposed(result):
    for bug in result.bugs:
        assert "PROPOSED" in bug.trust_tier.upper() or bug.trust_tier == "ORACLE_PROPOSED"

def test_detection_session_trust_not_promoted(result):
    # No bug should have a trust tier that indicates mechanical verification
    for bug in result.bugs:
        assert "MECHANICALLY_VERIFIED" not in bug.trust_tier.upper()
        assert "SOLVER_DISCHARGED" not in bug.trust_tier.upper()

def test_default_bug_trust_tier():
    bug = BugReport()
    assert bug.trust_tier == "ORACLE_PROPOSED"
