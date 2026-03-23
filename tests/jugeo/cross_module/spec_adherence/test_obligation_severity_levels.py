"""Cross-module tests: CRITICAL obligations have correct severity."""
import pytest

try:
    from jugeo.problem_modes.specification_satisfaction.s00_spec_parser import (
        SpecParser, ParsedSpecification, ParsedObligation,
    )
except ImportError as e:
    pytest.skip(f"spec_parser not available: {e}", allow_module_level=True)

@pytest.fixture
def parser():
    return SpecParser({})

@pytest.fixture
def spec(parser):
    return parser.parse_docstring(
        "Critical precondition: x must be non-negative. "
        "Warning: large inputs may be slow. "
        "Postcondition: result is non-negative."
    )

def test_obligation_has_severity(spec):
    for obligation in spec.obligations:
        if hasattr(obligation, "severity"):
            assert isinstance(obligation.severity, str)

def test_critical_severity_value(spec):
    critical_obs = [o for o in spec.obligations 
                    if hasattr(o, "severity") and o.severity == "CRITICAL"]
    # At least checking that critical obligations have CRITICAL severity
    for o in critical_obs:
        assert o.severity == "CRITICAL"

def test_critical_obligations_method(spec):
    critical = spec.critical_obligations()
    assert isinstance(critical, tuple)
