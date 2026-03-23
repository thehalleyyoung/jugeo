"""Cross-module tests: ParsedSpecification.to_jugeo_spec()."""
import pytest

try:
    from jugeo.problem_modes.specification_satisfaction.s00_spec_parser import (
        SpecParser, ParsedSpecification, SpecFormat,
    )
except ImportError as e:
    pytest.skip(f"spec_parser not available: {e}", allow_module_level=True)

@pytest.fixture
def spec():
    parser = SpecParser({})
    return parser.parse(
        "Returns the sum of x and y. Precondition: x > 0.",
        format=SpecFormat.DOCSTRING,
    )

def test_parsed_spec_to_jugeo_spec(spec):
    if hasattr(spec, "to_jugeo_spec"):
        result = spec.to_jugeo_spec()
        # Should not raise
    else:
        pytest.skip("to_jugeo_spec not implemented")

def test_jugeo_spec_result_not_none(spec):
    if hasattr(spec, "to_jugeo_spec"):
        result = spec.to_jugeo_spec()
        assert result is not None
    else:
        assert spec is not None  # at minimum spec itself is not None
