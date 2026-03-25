"""Public move/tactic API for JuGeo.

This module promotes JuGeo's orchestration controller into a stable public
surface for tactic-style experimentation.  The underlying implementation lives
in :mod:`jugeo.orchestration.controller`; this facade adds engine-oriented
names and a small amount of compatibility sugar so callers can work with a
``MoveEngine`` rather than importing orchestration internals directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable

from jugeo.orchestration.controller import (
    AdaptiveControl,
    BalancedControl,
    ControlLaw,
    ConvergenceMonitor,
    GreedyControl,
    LookaheadControl,
    MoveHistory,
    MoveKind,
    MoveRecord,
    Orchestrator,
    OrchestratorConfiguration,
    OrchestratorDiagnostics,
    OrchestratorEvent,
    OrchestratorEventBus,
    OrchestratorEventKind,
    OrchestratorState,
    ResourceBudget,
    SemanticMove,
    build_control_law,
)


class MoveStatus(str, Enum):
    """User-facing status for applying a move through :class:`MoveEngine`."""

    PENDING = "pending"
    APPLIED = "applied"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


@dataclass(frozen=True, slots=True)
class MoveResult:
    """Result of executing a move through the public engine API."""

    move: SemanticMove
    status: MoveStatus
    success: bool
    actual_gain: float
    target_coordinate: str


class MoveEngine(Orchestrator):
    """Public tactic engine built on top of JuGeo's orchestrator.

    ``MoveEngine`` keeps the underlying orchestration semantics intact while
    exposing a smaller API that matches how users think about tactic engines:
    enumerate candidate moves, select one with a control law, apply it, and run
    until the frontier is closed or a step budget is exhausted.
    """

    def __init__(
        self,
        *,
        config: OrchestratorConfiguration | None = None,
        state: OrchestratorState | None = None,
        control_law: ControlLaw | None = None,
        event_bus: OrchestratorEventBus | None = None,
        max_moves: int | None = None,
        time_budget_s: float | None = None,
    ) -> None:
        resolved = config or OrchestratorConfiguration()
        if max_moves is not None:
            resolved.max_steps = max_moves
        if time_budget_s is not None:
            resolved.move_timeout = time_budget_s
        super().__init__(config=resolved, state=state, event_bus=event_bus)
        if control_law is not None:
            self._control_law = control_law

    def candidate_moves(self, state: OrchestratorState | None = None) -> list[SemanticMove]:
        """Return every generated move before admissibility filtering."""

        active_state = self.state if state is None else state
        return self._generator.generate_all(active_state)

    def applicable_moves(self, state: OrchestratorState | None = None) -> list[SemanticMove]:
        """Return the admissible moves for the current state."""

        active_state = self.state if state is None else state
        return self._generator.filter_admissible(self.candidate_moves(active_state), active_state)

    def select_move(self, state: OrchestratorState | None = None) -> SemanticMove | None:
        """Select the next move according to the configured control law."""

        active_state = self.state if state is None else state
        candidates = self.applicable_moves(active_state)
        if not candidates:
            return None
        return self._control_law.select(active_state, candidates)

    def apply(self, move: SemanticMove) -> MoveResult:
        """Execute *move* and update engine state."""

        success, actual_gain = self.execute_move(move)
        self.evaluate_outcome(move, success, actual_gain)
        self.update_state(move, success)
        self._monitor.update(self.state)
        self.state.epoch += 1
        return MoveResult(
            move=move,
            status=MoveStatus.APPLIED if success else MoveStatus.FAILED,
            success=success,
            actual_gain=actual_gain,
            target_coordinate=move.target_coordinate,
        )

    def run_until_closed(
        self,
        predicate: Callable[[OrchestratorState], bool] | None = None,
        *,
        max_steps: int | None = None,
    ) -> int:
        """Run until the engine reaches a terminal state or *predicate* fires."""

        final_predicate = predicate or (lambda state: state.is_terminal())
        return self.run_until(predicate=final_predicate, max_steps=max_steps)

    @property
    def move_history(self) -> MoveHistory:
        """Alias for the underlying orchestration move history."""

        return self.history


GreedyControlLaw = GreedyControl
LookaheadControlLaw = LookaheadControl
BalancedControlLaw = BalancedControl
AdaptiveControlLaw = AdaptiveControl

__all__ = [
    "MoveKind",
    "MoveStatus",
    "SemanticMove",
    "MoveResult",
    "MoveEngine",
    "ControlLaw",
    "GreedyControl",
    "LookaheadControl",
    "BalancedControl",
    "AdaptiveControl",
    "GreedyControlLaw",
    "LookaheadControlLaw",
    "BalancedControlLaw",
    "AdaptiveControlLaw",
    "OrchestratorState",
    "OrchestratorConfiguration",
    "ResourceBudget",
    "MoveHistory",
    "MoveRecord",
    "ConvergenceMonitor",
    "OrchestratorEvent",
    "OrchestratorEventBus",
    "OrchestratorEventKind",
    "OrchestratorDiagnostics",
    "build_control_law",
]
