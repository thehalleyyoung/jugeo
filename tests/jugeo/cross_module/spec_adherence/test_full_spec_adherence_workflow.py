"""Cross-module tests: Full workflow: parse spec from source, check obligations."""
import pytest

try:
    from jugeo.problem_modes.specification_satisfaction.s00_spec_parser import (
        spec_from_docstring, ParsedSpecification, ParsedObligation,
    )
except ImportError as e:
    pytest.skip(f"spec_parser not available: {e}", allow_module_level=True)

SOURCE_WITH_RICH_DOCSTRING = '''
def factorial(n: int) -> int:
    """
    Compute factorial of n.

    Precondition: n >= 0
    Postcondition: result > 0
    Invariant: each step multiplies result by decreasing positive integer
    Returns: n!
    """
    if n == 0:
        return 1
    return n * factorial(n - 1)
'''

@pytest.fixture
def spec():
    return spec_from_docstring(SOURCE_WITH_RICH_DOCSTRING)

def test_parse_source_file_docstring(spec):
    assert isinstance(spec, ParsedSpecification)

def test_spec_obligations_have_kinds(spec):
    for obligation in spec.obligations:
        assert hasattr(obligation, "kind")

def test_spec_obligations_have_ids(spec):
    for obligation in spec.obligations:
        assert hasattr(obligation, "obligation_id")

def test_spec_obligations_have_text(spec):
    for obligation in spec.obligations:
        assert hasattr(obligation, "text")
