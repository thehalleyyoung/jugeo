from pathlib import Path
import sys

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from jugeo.evidence.certificates import CertificateStatus, emit_certificate
from jugeo.evidence.channels import EvidenceKind, EvidenceRecord, build_channel
from jugeo.evidence.manifests import build_evidence_manifest
from jugeo.evidence.provenance import ProvenanceTrace
from jugeo.evidence.trust import TrustProfile, TrustTier
from jugeo.geometry.site import CoordinateKind, CoordinateObject
from jugeo.geometry.supports import SupportRegion
from jugeo.judgments.contexts import SemanticContext
from jugeo.judgments.exports import export_section
from jugeo.judgments.judgment_terms import JudgmentStatus, LocalJudgment
from jugeo.judgments.sections import JudgmentSection


def test_certificate_status_tracks_manifest_residuals() -> None:
    coordinate = CoordinateObject('coord', CoordinateKind.REGION, ('coord',))
    judgment = LocalJudgment(coordinate, 'P', {'artifact': 'x'}, status=JudgmentStatus.SETTLED)
    section = JudgmentSection(coordinate, SemanticContext(coordinate), judgment, SupportRegion(coordinate, frozenset({'p'})), 'p')
    export = export_section(section)
    record = EvidenceRecord(build_channel('proof', EvidenceKind.PROOF), 'P')
    manifest = build_evidence_manifest('coord', 'P', (record,), trust_profiles=(TrustProfile(TrustTier.VERIFIED),), provenance=ProvenanceTrace('root'))
    certificate = emit_certificate(manifest, export, issuer='tester')
    assert certificate.status is CertificateStatus.SETTLED
