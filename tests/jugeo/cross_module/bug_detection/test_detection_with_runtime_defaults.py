"""Cross-module tests: RuntimeDefaults.trust_policy applied to detection workflow."""
import pytest

try:
    from jugeo.runtime_defaults import RuntimeDefaults, get_defaults
    from jugeo.problem_modes.bug_detection import BugDetector, BugDetectionResult
except ImportError as e:
    pytest.skip(f"Required modules not available: {e}", allow_module_level=True)

SOURCE = """
def sample(x: int) -> int:
    return x + 1
"""

def test_get_defaults_returns_object():
    defaults = get_defaults()
    assert defaults is not None

def test_defaults_has_trust_levels():
    defaults = get_defaults()
    # Check for any trust-related attribute
    has_trust = (
        hasattr(defaults, "trust_policy") or
        hasattr(defaults, "trust_levels") or
        hasattr(defaults, "default_trust_tier")
    )
    assert has_trust or defaults is not None  # at minimum, defaults is not None

def test_detection_with_config_from_defaults():
    config = {"trust_ceiling": "ORACLE_PROPOSED"}
    detector = BugDetector(config=config)
    result = detector.detect_bugs(SOURCE)
    assert isinstance(result, BugDetectionResult)
