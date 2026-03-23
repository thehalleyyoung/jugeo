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
from jugeo.runtime.cache import CacheEntry, SemanticCache


def test_cache_invalidates_by_support() -> None:
    support = make_support('p')
    cache = SemanticCache()
    cache.put(CacheEntry('k', 1, support, TrustProfile(TrustTier.REVIEWED), ProvenanceTrace('root')))
    assert cache.invalidate_by_support(support) == ('k',)
