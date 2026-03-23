"""Cross-module tests: RuntimeDefaults integrates with spec parsing."""
import pytest

try:
    from jugeo.runtime_defaults import get_defaults
    from jugeo.problem_modes.specification_satisfaction.s00_spec_parser import SpecParser
except ImportError as e:
    pytest.skip(f"Required modules not available: {e}", allow_module_level=True)

def test_runtime_defaults_importable():
    defaults = get_defaults()
    assert defaults is not None

def test_defaults_is_not_none():
    defaults = get_defaults()
    assert defaults is not None

def test_spec_parser_with_empty_config():
    parser = SpecParser({})
    assert parser is not None
    spec = parser.parse_docstring("Returns x")
    assert spec is not None
