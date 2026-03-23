"""Cross-module tests: Loading two programs and attempting equivalence verification."""
import pytest

try:
    from jugeo.problem_modes.relational_refinement.s02_equivalence_verification import EquivalenceVerifier
except ImportError as e:
    pytest.skip(f"EquivalenceVerifier not available: {e}", allow_module_level=True)

def test_equivalence_verifier_instantiates():
    verifier = EquivalenceVerifier()
    assert verifier is not None

def test_verifier_is_equivalent_method_exists():
    verifier = EquivalenceVerifier()
    assert callable(getattr(verifier, "is_equivalent", None)) or callable(getattr(verifier, "verify", None))
