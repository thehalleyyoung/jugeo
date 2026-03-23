from pathlib import Path
import sys

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from jugeo.kernel.authority import AuthorityTier, build_authority_center
from jugeo.kernel.health import collect_health, render_health_summary
from jugeo.kernel.lifecycle import LifecycleController, LifecycleState
from jugeo.kernel.services import ServiceBinding, ServiceGraph


def test_health_summary_mentions_bound_services() -> None:
    authority = build_authority_center('runtime', capabilities={'run'}, trust_ceiling=AuthorityTier.REVIEWED)
    graph = ServiceGraph({'kernel': ServiceBinding('kernel', object(), authority)})
    lifecycle = LifecycleController(state=LifecycleState.STARTED)
    summary = render_health_summary(collect_health(graph, lifecycle))
    assert 'kernel:healthy' in summary
