from pathlib import Path
import sys

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from jugeo.geometry.site import CoordinateKind, CoordinateObject
from jugeo.geometry.supports import SupportRegion
from jugeo.ideation.ideas import IdeaProposal
from jugeo.ideation.scheduling import schedule_ideas
from jugeo.orchestration.budgets import BudgetLedger


def test_schedule_ideas_respects_budget() -> None:
    coordinate = CoordinateObject('coord', CoordinateKind.REGION, ('coord',))
    ideas = (IdeaProposal('a', 'h', SupportRegion(coordinate, frozenset({'p'})), 2), IdeaProposal('b', 'h', SupportRegion(coordinate, frozenset({'p'})), 1))
    schedule = schedule_ideas(ideas, BudgetLedger({'ideation': 1}))
    assert schedule.accepted == ('a',)
    assert schedule.deferred == ('b',)
