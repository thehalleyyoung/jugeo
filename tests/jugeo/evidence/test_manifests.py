from pathlib import Path
import sys

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from jugeo.evidence.channels import EvidenceKind, EvidenceRecord, build_channel
from jugeo.evidence.manifests import build_evidence_manifest
from jugeo.evidence.provenance import ProvenanceTrace
from jugeo.evidence.trust import TrustProfile, TrustTier


def test_manifest_collects_residuals() -> None:
    record = EvidenceRecord(build_channel('runtime', EvidenceKind.RUNTIME), 'ok', obligations=('todo',))
    manifest = build_evidence_manifest('coord', 'claim', (record,), trust_profiles=(TrustProfile(TrustTier.REVIEWED),), provenance=ProvenanceTrace('root'))
    assert manifest.residuals == ('todo',)
