from pathlib import Path
import sys

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from jugeo.solver.countermodels import extract_countermodel
from jugeo.solver.z3_session import SolveOutcome, SolverResult


def test_extract_countermodel_for_sat_result() -> None:
    countermodel = extract_countermodel(SolverResult(SolveOutcome.SAT, 'builtin', {'p': True}))
    assert countermodel is not None
    assert countermodel.assignment['p'] is True
