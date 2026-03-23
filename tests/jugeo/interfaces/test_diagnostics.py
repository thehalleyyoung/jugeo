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
    
from jugeo.generation.backpressure import BackpressureLevel, BackpressureSignal
from jugeo.interfaces.diagnostics import collect_diagnostics
from jugeo.kernel.authority import AuthorityTier, build_authority_center
from jugeo.kernel.health import collect_health
from jugeo.kernel.lifecycle import LifecycleController, LifecycleState
from jugeo.kernel.services import ServiceBinding, ServiceGraph
from jugeo.orchestration.frontier import FrontierItem, FrontierState


def test_collect_diagnostics_reports_backpressure() -> None:
    authority = build_authority_center('runtime', capabilities={'run'}, trust_ceiling=AuthorityTier.REVIEWED)
    graph = ServiceGraph({'kernel': ServiceBinding('kernel', object(), authority)})
    health = collect_health(graph, LifecycleController(state=LifecycleState.STARTED))
    report = collect_diagnostics(health, FrontierState([FrontierItem(make_goal('work'))]), BackpressureSignal(BackpressureLevel.WATCH, ('blocked',)))
    assert any(message.message == 'blocked' for message in report.messages)
