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
from jugeo.generation.goals import ConstructionGoal, GoalPriority, prioritize_goals


def test_prioritize_goals_prefers_higher_priority() -> None:
    low = ConstructionGoal('A', make_support('a'), TrustTier.PROPOSAL, GoalPriority.LOW)
    high = ConstructionGoal('B', make_support('b'), TrustTier.PROPOSAL, GoalPriority.HIGH)
    assert prioritize_goals((low, high))[0] == high
