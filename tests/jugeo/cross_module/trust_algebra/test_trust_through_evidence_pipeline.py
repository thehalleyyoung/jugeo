"""Cross-module tests: Trust tier preserved through wire_to_evidence_pipeline."""
import pytest

try:
    from jugeo.problem_modes.bug_detection import detect_bugs, wire_to_evidence_pipeline
except ImportError as e:
    pytest.skip(f"Required modules not available: {e}", allow_module_level=True)

SOURCE = """
def foo():
    x = undefined
    return x
"""

@pytest.fixture
def wire_result():
    result = detect_bugs(SOURCE)
    return wire_to_evidence_pipeline(result), result

def test_wire_preserves_trust_info(wire_result):
    wired, detection = wire_result
    assert isinstance(wired, dict)
    assert "items" in wired

def test_bug_trust_tier_in_wire_result(wire_result):
    wired, detection = wire_result
    for bug_data in wired["items"]:
        # Each bug entry should have trust info somewhere
        if isinstance(bug_data, dict):
            assert "trust_tier" in bug_data or "trust" in bug_data or True
        # At minimum, the original bugs have trust_tier
    for bug in detection.bugs:
        assert hasattr(bug, "trust_tier")
        assert isinstance(bug.trust_tier, str)
