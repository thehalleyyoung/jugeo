"""Cross-module tests: ObligationPriority from manifests module."""
import pytest

try:
    from jugeo.evidence.manifests import ObligationPriority, ObstructionKind
except ImportError as e:
    pytest.skip(f"jugeo.evidence.manifests not available: {e}", allow_module_level=True)

def test_obligation_priority_importable():
    assert ObligationPriority is not None

def test_obligation_priority_has_critical():
    assert hasattr(ObligationPriority, "CRITICAL")

def test_obstruction_kind_importable():
    assert ObstructionKind is not None
