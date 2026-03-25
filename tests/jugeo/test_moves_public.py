from pathlib import Path
import sys

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from jugeo.moves import (
    BalancedControlLaw,
    MoveEngine,
    MoveKind,
    MoveResult,
    MoveStatus,
    OrchestratorState,
    SemanticMove,
)


def test_public_moves_module_exports_engine_surface() -> None:
    move = SemanticMove(MoveKind.CONSTRUCT, "pkg.module.fn", expected_gain=0.2)
    assert move.kind is MoveKind.CONSTRUCT
    assert BalancedControlLaw().__class__.__name__ == "BalancedControl"

    engine = MoveEngine(
        state=OrchestratorState(
            frontier_nodes=["pkg.module.fn"],
            evidence_channels={"solver": True, "runtime": True, "copilot": True},
        )
    )
    candidates = engine.applicable_moves()
    assert candidates
    assert any(candidate.kind is MoveKind.CONSTRUCT for candidate in candidates)


def test_move_engine_apply_returns_public_result() -> None:
    engine = MoveEngine(
        state=OrchestratorState(
            frontier_nodes=["pkg.module.fn"],
            evidence_channels={"solver": True, "runtime": True, "copilot": True},
        )
    )
    move = engine.select_move()
    assert move is not None

    result = engine.apply(move)
    assert isinstance(result, MoveResult)
    assert result.status in {MoveStatus.APPLIED, MoveStatus.FAILED}
    assert result.target_coordinate == move.target_coordinate
