from pathlib import Path
import sys

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from jugeo.ideation.regimes import RegimeKind, RegimeProposal, choose_regime


def test_choose_regime_prefers_bigger_obstruction_drop() -> None:
    best = choose_regime((RegimeProposal(RegimeKind.COVER_REFINEMENT, 'a', 1), RegimeProposal(RegimeKind.THEORY_EXTENSION, 'b', 3)))
    assert best.kind is RegimeKind.THEORY_EXTENSION
