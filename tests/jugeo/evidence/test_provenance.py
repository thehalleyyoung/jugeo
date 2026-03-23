from pathlib import Path
import sys

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from jugeo.evidence.provenance import ProvenanceStep, ProvenanceTrace


def test_provenance_round_trip() -> None:
    trace = ProvenanceTrace('root').append(ProvenanceStep('tester', 'check', 'coord'))
    restored = ProvenanceTrace.from_dict(trace.to_dict())
    assert restored == trace
