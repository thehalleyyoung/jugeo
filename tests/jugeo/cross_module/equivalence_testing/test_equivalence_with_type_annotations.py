"""Cross-module tests: spec_from_annotations and equivalence check together."""
import pytest

try:
    from jugeo.problem_modes.relational_refinement.s02_equivalence_verification import EquivalenceVerifier
    from jugeo.problem_modes.specification_satisfaction.s00_spec_parser import (
        spec_from_annotations, ParsedSpecification,
    )
except ImportError as e:
    pytest.skip(f"Required modules not available: {e}", allow_module_level=True)

ANNOTATED_SOURCE = """
def add(x: int, y: int) -> int:
    return x + y
"""

def test_spec_parser_and_equivalence_verifier_import_together():
    verifier = EquivalenceVerifier()
    assert verifier is not None

def test_spec_from_annotations_produces_spec():
    spec = spec_from_annotations(ANNOTATED_SOURCE)
    assert isinstance(spec, ParsedSpecification)
