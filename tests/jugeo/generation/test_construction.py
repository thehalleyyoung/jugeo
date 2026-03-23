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
from jugeo.evidence.trust import TrustTier
from jugeo.generation.construction import propose_construction
from jugeo.generation.goals import ConstructionGoal


def test_propose_construction_emits_patch_steps() -> None:
    plan = propose_construction(ConstructionGoal('A', make_support('a'), TrustTier.PROPOSAL))
    assert plan.steps[0].patch == 'a'
