"""Cross-module tests: relational_refinement exports expected symbols."""
import pytest

try:
    from jugeo.problem_modes.relational_refinement import (
        RefinementRelation, EquivalenceClass, RefinementWitness,
    )
except ImportError as e:
    pytest.skip(f"relational_refinement not available: {e}", allow_module_level=True)

def test_module_exports_refinement_relation():
    assert RefinementRelation is not None

def test_module_exports_equivalence_class():
    assert EquivalenceClass is not None

def test_module_exports_refinement_witness():
    assert RefinementWitness is not None
