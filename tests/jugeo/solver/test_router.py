from pathlib import Path
import sys

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from jugeo.evidence.trust import TrustProfile, TrustTier
from jugeo.solver.fragments import SolverFragment, LogicalFragment
from jugeo.solver.router import SolverRouter


def test_router_demands_review_for_fallback_under_proposal_trust() -> None:
    route = SolverRouter().route(SolverFragment('forall x', LogicalFragment.UNKNOWN), TrustProfile(TrustTier.PROPOSAL))
    assert route.engine == 'review-required'
