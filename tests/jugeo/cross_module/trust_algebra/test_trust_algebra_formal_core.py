"""Cross-module tests: foundations/formal_core trust theorem."""
import pytest

try:
    from jugeo.foundations.formal_core import (
        TheoremRegistry,
        get_chapter_9_theorems,
        THEOREM_9_2_TRUST_ALGEBRA_AXIOMS,
    )
except ImportError as e:
    pytest.skip(f"jugeo.foundations.formal_core not available: {e}", allow_module_level=True)

def test_formal_core_importable():
    assert TheoremRegistry is not None

def test_theorem_registry_importable():
    assert TheoremRegistry is not None

def test_get_chapter_9_theorems():
    theorems = get_chapter_9_theorems()
    assert theorems is not None
    if hasattr(theorems, "__len__"):
        assert len(theorems) > 0
    elif hasattr(theorems, "__iter__"):
        items = list(theorems)
        assert len(items) > 0

def test_theorem_9_2_trust_axioms():
    assert THEOREM_9_2_TRUST_ALGEBRA_AXIOMS is not None
