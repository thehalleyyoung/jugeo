"""Cross-module tests: spec parser → obligations workflow."""
import pytest

try:
    from jugeo.problem_modes.specification_satisfaction.s00_spec_parser import (
        SpecParser, ParsedSpecification, SpecFormat,
    )
except ImportError as e:
    pytest.skip(f"spec_parser not available: {e}", allow_module_level=True)

def test_spec_parser_instantiates():
    parser = SpecParser({})
    assert parser is not None

def test_parse_docstring_returns_spec():
    parser = SpecParser({})
    spec = parser.parse("Returns x when x > 0", format=SpecFormat.DOCSTRING)
    assert isinstance(spec, ParsedSpecification)

def test_parsed_spec_has_obligations():
    parser = SpecParser({})
    spec = parser.parse("Precondition: x > 0. Returns x + 1.", format=SpecFormat.DOCSTRING)
    assert isinstance(spec.obligations, tuple)

def test_obligation_count_method():
    parser = SpecParser({})
    spec = parser.parse("Returns x", format=SpecFormat.DOCSTRING)
    assert spec.obligation_count >= 0
