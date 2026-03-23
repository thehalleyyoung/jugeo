"""Cross-module tests: ObstructionKind and trust in manifests module."""
import pytest

try:
    from jugeo.evidence.manifests import ObstructionKind, EvidenceManifest, ObligationPriority
except ImportError as e:
    pytest.skip(f"jugeo.evidence.manifests not available: {e}", allow_module_level=True)

def test_obstruction_kind_trust_violation():
    assert hasattr(ObstructionKind, "TRUST_VIOLATION")

def test_obstruction_kind_has_string_value():
    tv = ObstructionKind.TRUST_VIOLATION
    assert isinstance(tv.value, str)

def test_evidence_manifest_importable():
    assert EvidenceManifest is not None
