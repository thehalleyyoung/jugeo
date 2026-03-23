from pathlib import Path
import sys

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from jugeo.geometry.site import CoordinateKind, CoordinateObject
from jugeo.geometry.supports import SupportRegion
from jugeo.ideation.federation import IdeaFederation
from jugeo.ideation.ideas import IdeaProposal
from jugeo.ideation.regimes import RegimeKind, RegimeProposal


def test_idea_federation_deduplicates_titles() -> None:
    coordinate = CoordinateObject('coord', CoordinateKind.REGION, ('coord',))
    idea = IdeaProposal('same', 'h', SupportRegion(coordinate, frozenset({'p'})), 1)
    federation = IdeaFederation((idea, idea), (RegimeProposal(RegimeKind.COVER_REFINEMENT, 'r', 1),))
    assert federation.deduplicated_titles() == ('same',)
