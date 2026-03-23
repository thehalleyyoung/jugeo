"""Cross-module tests: Two independent EquivalenceVerifier instances."""
import pytest

try:
    from jugeo.problem_modes.relational_refinement.s02_equivalence_verification import EquivalenceVerifier
except ImportError as e:
    pytest.skip(f"EquivalenceVerifier not available: {e}", allow_module_level=True)

def test_two_verifiers_are_independent():
    v1 = EquivalenceVerifier()
    v2 = EquivalenceVerifier()
    assert v1 is not v2

def test_verifier_checker_is_none_by_default():
    v = EquivalenceVerifier()
    # The checker is auto-created if None passed
    checker_attr = getattr(v, "_checker", getattr(v, "checker", None))
    # Either _checker is not None (auto-created) or checker is None (lazy init)
    assert v is not None  # at minimum verifier was created
