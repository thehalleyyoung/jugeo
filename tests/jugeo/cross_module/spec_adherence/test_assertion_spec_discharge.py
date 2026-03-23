"""Cross-module tests: assert statements → parsed obligations."""
import pytest

try:
    from jugeo.problem_modes.specification_satisfaction.s00_spec_parser import (
        SpecParser, ParsedSpecification, SpecFormat,
    )
except ImportError as e:
    pytest.skip(f"spec_parser not available: {e}", allow_module_level=True)

ASSERTION_SOURCE = """
def divide(a: float, b: float) -> float:
    assert b != 0, "Divisor must not be zero"
    result = a / b
    assert isinstance(result, float), "Result must be float"
    return result
"""

@pytest.fixture
def parser():
    return SpecParser({})

def test_parse_assertions_method(parser):
    spec = parser.parse_assertions(ASSERTION_SOURCE)
    assert isinstance(spec, ParsedSpecification)

def test_assertion_spec_format(parser):
    spec = parser.parse_assertions(ASSERTION_SOURCE)
    assert spec.format == SpecFormat.PYTHON_ASSERTIONS
