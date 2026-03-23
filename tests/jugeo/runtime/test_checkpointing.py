from pathlib import Path
import sys

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


from jugeo.evidence.provenance import ProvenanceTrace
from jugeo.evidence.trust import TrustProfile, TrustTier
from jugeo.geometry.site import CoordinateKind, CoordinateObject
from jugeo.geometry.supports import SupportRegion

def make_support(patch: str = 'p'):
    coordinate = CoordinateObject('coord', CoordinateKind.REGION, ('coord',))
    return SupportRegion(coordinate, frozenset({patch}))
from jugeo.runtime.cache import SemanticCache
from jugeo.runtime.checkpointing import CheckpointStore
from jugeo.runtime.memory import SemanticMemory
from jugeo.runtime.replay import ReplayLedger


def test_checkpoint_store_creates_snapshot() -> None:
    store = CheckpointStore()
    checkpoint = store.create('c1', cache=SemanticCache(), memory=SemanticMemory(), replay=ReplayLedger())
    assert store.restore('c1') == checkpoint
