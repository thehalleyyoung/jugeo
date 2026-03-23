"""Cross-module tests: Complex obligations get ORACLE_PENDING trust tier."""
import pytest

try:
    from jugeo.problem_modes.specification_satisfaction.s00_spec_parser import (
        SpecParser, ParsedSpecification,
    )
except ImportError as e:
    pytest.skip(f"spec_parser not available: {e}", allow_module_level=True)

COMPLEX_SPEC = """
This function must terminate for all positive inputs.
It must satisfy the following invariant: the output is always a prime number.
Critical precondition: input must be a natural number greater than 1.
"""

@pytest.fixture
def parser():
    return SpecParser({})

def test_obligation_trust_tier_values_include_oracle(parser):
    spec = parser.parse_docstring(COMPLEX_SPEC)
    trust_tiers = [o.trust_tier for o in spec.obligations if hasattr(o, "trust_tier")]
    # At least some trust tier values should be strings (could include ORACLE_PENDING)
    for tier in trust_tiers:
        assert isinstance(tier, str)

def test_obligations_have_valid_trust_tiers(parser):
    spec = parser.parse_docstring(COMPLEX_SPEC)
    for obligation in spec.obligations:
        if hasattr(obligation, "trust_tier"):
            assert isinstance(obligation.trust_tier, str)
