"""Cross-module tests: Parsed obligations carry trust tier strings."""
import pytest

try:
    from jugeo.problem_modes.specification_satisfaction.s00_spec_parser import (
        SpecParser, ParsedSpecification,
    )
except ImportError as e:
    pytest.skip(f"spec_parser not available: {e}", allow_module_level=True)

@pytest.fixture
def spec_with_obligations():
    parser = SpecParser({})
    return parser.parse_docstring(
        "Precondition: x > 0. Postcondition: result >= 0. Invariant: loop terminates."
    )

def test_obligation_has_trust_tier(spec_with_obligations):
    for obligation in spec_with_obligations.obligations:
        assert hasattr(obligation, "trust_tier")

def test_obligation_trust_tier_is_str(spec_with_obligations):
    for obligation in spec_with_obligations.obligations:
        assert isinstance(obligation.trust_tier, str)

def test_obligation_severity_is_str(spec_with_obligations):
    for obligation in spec_with_obligations.obligations:
        if hasattr(obligation, "severity"):
            assert isinstance(obligation.severity, str)
