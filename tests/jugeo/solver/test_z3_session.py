from pathlib import Path
import sys

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from jugeo.solver.fragments import classify_fragment
from jugeo.solver.z3_session import SolveOutcome, Z3Session


def test_builtin_session_detects_boolean_contradiction() -> None:
    result = Z3Session().solve(classify_fragment('p & not p'))
    assert result.outcome is SolveOutcome.UNSAT
