from pathlib import Path
import sys

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from jugeo.generation.treaties import OverlapTreaty, TreatyClause, evaluate_treaty


def test_evaluate_treaty_requires_all_clauses() -> None:
    treaty = OverlapTreaty(('a', 'b'), (TreatyClause('a', 'x', True), TreatyClause('b', 'y', True)))
    assert evaluate_treaty(treaty) is True
