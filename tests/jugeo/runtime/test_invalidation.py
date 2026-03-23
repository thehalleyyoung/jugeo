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
from jugeo.geometry.covers import Cover
from jugeo.runtime.cache import SemanticCache
from jugeo.runtime.invalidation import plan_invalidation


def test_invalidation_plan_reopens_star_neighborhood() -> None:
    support = make_support('p')
    cover = Cover(support.coordinate, (support.coordinate,), ((support.coordinate.key, support.coordinate.key),))
    plan = plan_invalidation(SemanticCache(), support, cover)
    assert support.coordinate.key in plan.reopened_patches
