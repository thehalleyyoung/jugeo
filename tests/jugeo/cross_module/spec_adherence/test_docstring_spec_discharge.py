"""Cross-module tests: Extract obligations from docstring."""
import pytest

try:
    from jugeo.problem_modes.specification_satisfaction.s00_spec_parser import (
        spec_from_docstring, ParsedSpecification, SpecFormat,
    )
except ImportError as e:
    pytest.skip(f"spec_parser not available: {e}", allow_module_level=True)

SOURCE_WITH_DOCSTRING = '''
def compute(x: int) -> int:
    """
    Compute x squared.
    
    :precondition: x >= 0
    :postcondition: result >= 0
    :returns: x * x
    """
    return x * x
'''

def test_spec_from_docstring_fn():
    spec = spec_from_docstring(SOURCE_WITH_DOCSTRING)
    assert isinstance(spec, ParsedSpecification)

def test_docstring_spec_has_format():
    spec = spec_from_docstring(SOURCE_WITH_DOCSTRING)
    assert spec.format == SpecFormat.DOCSTRING

def test_docstring_spec_confidence():
    spec = spec_from_docstring(SOURCE_WITH_DOCSTRING)
    assert 0.0 <= spec.confidence <= 1.0
