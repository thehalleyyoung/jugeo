"""Cross-module tests: EquivalenceVerifier compute_equivalence_classes."""
import pytest

try:
    from jugeo.problem_modes.relational_refinement.s02_equivalence_verification import EquivalenceVerifier
except ImportError as e:
    pytest.skip(f"EquivalenceVerifier not available: {e}", allow_module_level=True)

def test_compute_equivalence_classes_with_empty():
    verifier = EquivalenceVerifier()
    result = verifier.compute_equivalence_classes([])
    assert result is not None

def test_result_is_tuple():
    verifier = EquivalenceVerifier()
    result = verifier.compute_equivalence_classes([])
    assert isinstance(result, tuple)
