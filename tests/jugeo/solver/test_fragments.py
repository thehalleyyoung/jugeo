from pathlib import Path
import sys

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from jugeo.solver.fragments import LogicalFragment, classify_fragment


def test_classify_fragment_detects_propositional_logic() -> None:
    fragment = classify_fragment('p and not q')
    assert fragment.fragment is LogicalFragment.PROPOSITIONAL
