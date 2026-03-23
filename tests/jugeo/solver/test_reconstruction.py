from pathlib import Path
import sys

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from jugeo.solver.countermodels import Countermodel
from jugeo.solver.reconstruction import reconstruct_countermodel


def test_reconstruct_countermodel_lists_assignments() -> None:
    report = reconstruct_countermodel(Countermodel({'p': True}))
    assert 'p=True' in report.assignments
