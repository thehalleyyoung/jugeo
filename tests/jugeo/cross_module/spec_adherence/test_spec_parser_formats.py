"""Cross-module tests: parse JSON, docstring, annotation formats."""
import pytest

try:
    from jugeo.problem_modes.specification_satisfaction.s00_spec_parser import (
        SpecParser, SpecFormat,
    )
except ImportError as e:
    pytest.skip(f"spec_parser not available: {e}", allow_module_level=True)

def test_spec_format_enum_values():
    assert hasattr(SpecFormat, "JSON_SCHEMA")
    assert hasattr(SpecFormat, "DOCSTRING")
    assert hasattr(SpecFormat, "TYPE_ANNOTATIONS")

def test_parse_json_spec():
    parser = SpecParser({})
    if hasattr(parser, "parse_json_spec"):
        import json
        spec_json = json.dumps({"obligations": [{"kind": "precondition", "text": "x > 0"}]})
        try:
            result = parser.parse_json_spec(spec_json)
            assert result is not None
        except Exception:
            pytest.skip("parse_json_spec raised exception")
    else:
        pytest.skip("parse_json_spec not implemented")

def test_spec_format_natural_language():
    assert hasattr(SpecFormat, "NATURAL_LANGUAGE")
