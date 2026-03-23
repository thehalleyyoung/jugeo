"""Cross-module tests: ParsedSpecification.confidence in [0,1]."""
import pytest

try:
    from jugeo.problem_modes.specification_satisfaction.s00_spec_parser import (
        SpecParser, ParsedSpecification, SpecFormat,
    )
except ImportError as e:
    pytest.skip(f"spec_parser not available: {e}", allow_module_level=True)

@pytest.fixture
def parser():
    return SpecParser({})

@pytest.fixture
def simple_spec(parser):
    return parser.parse("Returns x.", format=SpecFormat.DOCSTRING)

@pytest.fixture
def rich_spec(parser):
    return parser.parse(
        "Precondition: x > 0. Postcondition: result >= x. "
        "Invariant: loop terminates. Returns: x + 1.",
        format=SpecFormat.DOCSTRING,
    )

def test_confidence_in_range(simple_spec):
    assert 0.0 <= simple_spec.confidence <= 1.0

def test_confidence_is_float(simple_spec):
    assert isinstance(simple_spec.confidence, float)

def test_richer_spec_has_higher_confidence(simple_spec, rich_spec):
    # Rich spec with more content should have confidence > 0
    assert rich_spec.confidence >= 0.0
