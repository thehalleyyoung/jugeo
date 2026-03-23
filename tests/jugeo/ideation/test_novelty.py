from pathlib import Path
import sys

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from jugeo.geometry.site import CoordinateKind, CoordinateObject
from jugeo.geometry.supports import SupportRegion
from jugeo.ideation.ideas import IdeaProposal
from jugeo.ideation.novelty import score_novelty


def test_novelty_penalizes_seen_titles() -> None:
    coordinate = CoordinateObject('coord', CoordinateKind.REGION, ('coord',))
    idea = IdeaProposal('title', 'hypothesis', SupportRegion(coordinate, frozenset({'p'})), 10)
    assert score_novelty(idea, seen_titles=('title',)).score < score_novelty(idea, seen_titles=()).score
