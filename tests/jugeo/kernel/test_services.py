from pathlib import Path
import sys

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from jugeo.kernel.authority import AuthorityTier, build_authority_center
from jugeo.kernel.services import ServiceBinding, ServiceGraph, freeze_service_graph


def test_service_graph_orders_dependencies() -> None:
    authority = build_authority_center('runtime', capabilities={'run'}, trust_ceiling=AuthorityTier.REVIEWED)
    graph = ServiceGraph()
    graph.bind(ServiceBinding('a', object(), authority))
    graph.bind(ServiceBinding('b', object(), authority, dependencies=('a',)))
    freeze_service_graph(graph)
    assert graph.startup_order() == ('a', 'b')
