from pathlib import Path
import sys

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


from jugeo.evidence.trust import TrustTier
from jugeo.generation.goals import ConstructionGoal, GoalPriority
from jugeo.geometry.site import CoordinateKind, CoordinateObject
from jugeo.geometry.supports import SupportRegion

def make_goal(name: str = 'goal', patch: str = 'p', budget: int = 1, priority: GoalPriority = GoalPriority.MEDIUM):
    coordinate = CoordinateObject('coord', CoordinateKind.REGION, ('coord',))
    support = SupportRegion(coordinate, frozenset({patch}))
    return ConstructionGoal(name, support, TrustTier.PROPOSAL, priority, budget)
from jugeo.orchestration.frontier import FrontierItem, FrontierState


def test_frontier_next_item_prefers_urgency() -> None:
    frontier = FrontierState([FrontierItem(make_goal('a'), urgency=1), FrontierItem(make_goal('b'), urgency=2)])
    assert frontier.next_item().goal.proposition == 'b'
