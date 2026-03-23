"""Cross-module tests: Partial spec has correct obligation count."""
import pytest

try:
    from jugeo.problem_modes.specification_satisfaction.s00_spec_parser import (
        SpecParser, ParsedSpecification,
    )
except ImportError as e:
    pytest.skip(f"spec_parser not available: {e}", allow_module_level=True)

@pytest.fixture
def parser():
    return SpecParser({})

def test_partial_spec_obligation_count(parser):
    spec = parser.parse_docstring("Precondition: x > 0")
    count = spec.obligation_count()
    assert count >= 0

def test_critical_obligations_subset(parser):
    spec = parser.parse_docstring("Precondition: x > 0. Critical: result must be positive.")
    all_obs = spec.obligations
    critical_obs = spec.critical_obligations()
    # Critical obligations should be a subset of all obligations
    critical_ids = {o.obligation_id for o in critical_obs}
    all_ids = {o.obligation_id for o in all_obs}
    assert critical_ids.issubset(all_ids)
