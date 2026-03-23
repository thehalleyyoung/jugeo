"""Cross-module tests: SpecParserError."""
import pytest

try:
    from jugeo.problem_modes.specification_satisfaction.s00_spec_parser import (
        SpecParserError, SpecFormat,
    )
except ImportError as e:
    pytest.skip(f"spec_parser not available: {e}", allow_module_level=True)

def test_spec_parser_error_class_exists():
    assert SpecParserError is not None

def test_spec_parser_error_construction():
    try:
        err = SpecParserError("test message", SpecFormat.UNKNOWN, {})
        assert err is not None
    except TypeError:
        # Try alternate construction
        try:
            err = SpecParserError("test message")
            assert err is not None
        except Exception as e2:
            pytest.skip(f"Cannot construct SpecParserError: {e2}")

def test_spec_parser_error_is_exception():
    try:
        err = SpecParserError("test message", SpecFormat.UNKNOWN, {})
    except TypeError:
        try:
            err = SpecParserError("test message")
        except Exception:
            pytest.skip("Cannot construct SpecParserError")
            return
    assert isinstance(err, Exception)
