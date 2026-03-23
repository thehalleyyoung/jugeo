from pathlib import Path
import sys

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from jugeo.geometry.site import CoordinateKind, CoordinateObject
from jugeo.judgments.judgment_terms import JudgmentStatus, LocalJudgment


def test_local_judgment_settlement_tracks_obligations() -> None:
    coordinate = CoordinateObject('coord', CoordinateKind.REGION, ('coord',))
    judgment = LocalJudgment(coordinate, 'P', {'artifact': 'x'}, status=JudgmentStatus.SETTLED)
    assert judgment.is_settled() is True
    blocked = LocalJudgment(coordinate, 'P', {'artifact': 'x'}, obligations=('todo',), status=JudgmentStatus.SETTLED)
    assert blocked.is_settled() is False
