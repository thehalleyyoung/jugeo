"""Cross-module tests: Various source strings don't cause exceptions."""
import pytest

try:
    from jugeo.problem_modes.specification_satisfaction.s00_spec_parser import (
        spec_from_docstring, parse_spec, SpecFormat,
    )
except ImportError as e:
    pytest.skip(f"spec_parser not available: {e}", allow_module_level=True)

def test_empty_source_parses():
    result = spec_from_docstring("")
    assert result is not None

def test_whitespace_source():
    result = parse_spec("   ", SpecFormat.NATURAL_LANGUAGE, "test")
    assert result is not None

def test_parse_spec_fn():
    result = parse_spec("Returns x", SpecFormat.DOCSTRING, "test.py")
    assert result is not None
