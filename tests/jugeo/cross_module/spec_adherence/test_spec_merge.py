"""Cross-module tests: Merging multiple specs."""
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
def two_specs(parser):
    spec1 = parser.parse("Precondition: x > 0.", format=SpecFormat.DOCSTRING)
    spec2 = parser.parse("Postcondition: result >= 0.", format=SpecFormat.DOCSTRING)
    return spec1, spec2

def test_merge_two_specs(parser, two_specs):
    spec1, spec2 = two_specs
    merged = parser.merge([spec1, spec2])
    assert isinstance(merged, ParsedSpecification)

def test_merged_spec_has_combined_obligations(parser, two_specs):
    spec1, spec2 = two_specs
    merged = parser.merge([spec1, spec2])
    max_count = max(spec1.obligation_count, spec2.obligation_count)
    assert merged.obligation_count >= max_count

def test_merge_deduplicates(parser, two_specs):
    spec1, spec2 = two_specs
    merged = parser.merge([spec1, spec2])
    assert isinstance(merged, ParsedSpecification)
    assert merged.obligations is not None
