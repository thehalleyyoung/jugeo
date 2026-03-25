"""
Automatic phase detection from semantic state.

Replaces Comet-H's hand-designed mode graph with data-driven phase detection
based on multiple signals: obligation trends, coverage trends, drift levels,
progress stalls, budget remaining, and obstruction rates.

Phases: EXPLORATION → EXPLOITATION → HARDENING → TAIL → CONVERGED
        (or recovery back to EXPLOITATION if things go wrong)
"""
from __future__ import annotations

import time
from collections import deque
from typing import Any, Optional

from .models import BudgetUsage, Phase, PhaseSignal, PhaseTransition

__all__ = ["PhaseDetector"]


class PhaseDetector:
    """Data-driven automatic phase detector."""

    def __init__(self, window_size: int = 20) -> None:
        self._window_size = window_size
        self._history: deque[dict[str, Any]] = deque(maxlen=window_size)
        self._current_phase: Phase = Phase.EXPLORATION
        self._transitions: list[PhaseTransition] = []
        self._phase_start_times: dict[Phase, float] = {Phase.EXPLORATION: time.time()}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def detect_phase(self, state: dict[str, Any]) -> Phase:
        """Detect the current phase from *state* and record a transition
        if the phase changed."""
        self.record_state(state)
        signals = self._compute_signals(state)
        new_phase = self._phase_from_signals(signals)

        transition = self.should_transition(self._current_phase, signals)
        if transition is not None:
            self._transitions.append(transition)
            now = time.time()
            self._phase_start_times[transition.to_phase] = now
            self._current_phase = transition.to_phase

        return self._current_phase

    def record_state(self, state: dict[str, Any]) -> None:
        """Append a state snapshot to the rolling window."""
        self._history.append(dict(state))

    def transition_history(self) -> list[PhaseTransition]:
        """Return the full transition history."""
        return list(self._transitions)

    def phase_durations(self) -> dict[Phase, float]:
        """Return how long each phase has lasted (in seconds)."""
        now = time.time()
        durations: dict[Phase, float] = {}
        for phase, start in self._phase_start_times.items():
            durations[phase] = now - start
        return durations

    # ------------------------------------------------------------------
    # Signals
    # ------------------------------------------------------------------

    def _compute_signals(self, state: dict[str, Any]) -> list[PhaseSignal]:
        """Build the signal vector from the current history + state."""
        history_list = list(self._history)
        signals: list[PhaseSignal] = [
            self._signal_obligation_trend(history_list),
            self._signal_coverage_trend(history_list),
            self._signal_drift_level(state),
            self._signal_progress_stall(history_list),
            self._signal_budget_remaining(state),
            self._signal_obstruction_rate(history_list),
        ]
        return signals

    def _signal_obligation_trend(self, history: list[dict[str, Any]]) -> PhaseSignal:
        """Negative value → obligations are decreasing (good)."""
        if len(history) < 2:
            return PhaseSignal(signal_name="obligation_trend", value=0.0, threshold=-0.1)
        recent = [h.get("obligation_count", 0) for h in history]
        first_half = sum(recent[: len(recent) // 2]) / max(1, len(recent) // 2)
        second_half = sum(recent[len(recent) // 2 :]) / max(1, len(recent) - len(recent) // 2)
        trend = second_half - first_half
        return PhaseSignal(
            signal_name="obligation_trend",
            value=trend,
            threshold=-0.1,
            triggered=trend < -0.1,
        )

    def _signal_coverage_trend(self, history: list[dict[str, Any]]) -> PhaseSignal:
        """Positive value → coverage is increasing (good)."""
        if len(history) < 2:
            return PhaseSignal(signal_name="coverage_trend", value=0.0, threshold=0.01)
        recent = [h.get("coverage", 0.0) for h in history]
        first_half = sum(recent[: len(recent) // 2]) / max(1, len(recent) // 2)
        second_half = sum(recent[len(recent) // 2 :]) / max(1, len(recent) - len(recent) // 2)
        trend = second_half - first_half
        return PhaseSignal(
            signal_name="coverage_trend",
            value=trend,
            threshold=0.01,
            triggered=trend > 0.01,
        )

    def _signal_drift_level(self, state: dict[str, Any] | Any) -> PhaseSignal:
        """Current overall drift score."""
        if isinstance(state, dict):
            drift = state.get("overall_drift_score", 0.0)
        else:
            drift = getattr(state, "overall_drift_score", 0.0)
        return PhaseSignal(
            signal_name="drift_level",
            value=drift,
            threshold=0.05,
            triggered=drift < 0.05,
        )

    def _signal_progress_stall(self, history: list[dict[str, Any]]) -> PhaseSignal:
        """True when obligation count hasn't changed in the recent window."""
        if len(history) < 3:
            return PhaseSignal(signal_name="progress_stall", value=0.0, threshold=1.0)
        recent = [h.get("obligation_count", 0) for h in history[-5:]]
        if len(set(recent)) <= 1 and len(recent) >= 3:
            return PhaseSignal(
                signal_name="progress_stall",
                value=1.0,
                threshold=1.0,
                triggered=True,
            )
        return PhaseSignal(signal_name="progress_stall", value=0.0, threshold=1.0)

    def _signal_budget_remaining(self, state: dict[str, Any] | Any) -> PhaseSignal:
        """Fraction of budget remaining."""
        if isinstance(state, dict):
            budget_data = state.get("budget")
            if isinstance(budget_data, BudgetUsage):
                remaining = budget_data.remaining / max(
                    0.001, budget_data.remaining + budget_data.spent
                )
            elif isinstance(budget_data, dict):
                remaining = budget_data.get("remaining", 1.0)
            else:
                remaining = state.get("budget_remaining", 1.0)
        else:
            remaining = getattr(state, "budget_remaining", 1.0)
        return PhaseSignal(
            signal_name="budget_remaining",
            value=remaining,
            threshold=0.2,
            triggered=remaining < 0.2,
        )

    def _signal_obstruction_rate(self, history: list[dict[str, Any]]) -> PhaseSignal:
        """Fraction of recent moves that produced obstructions."""
        if not history:
            return PhaseSignal(signal_name="obstruction_rate", value=0.0, threshold=0.3)
        counts = [h.get("obstruction_count", 0) for h in history[-10:]]
        rate = sum(1 for c in counts if c > 0) / max(1, len(counts))
        return PhaseSignal(
            signal_name="obstruction_rate",
            value=rate,
            threshold=0.3,
            triggered=rate > 0.3,
        )

    # ------------------------------------------------------------------
    # Phase determination
    # ------------------------------------------------------------------

    def _phase_from_signals(self, signals: list[PhaseSignal]) -> Phase:
        """Determine the phase from the signal vector."""
        sig = {s.signal_name: s for s in signals}

        obligation_trend = sig.get("obligation_trend")
        coverage_trend = sig.get("coverage_trend")
        drift_level = sig.get("drift_level")
        progress_stall = sig.get("progress_stall")
        budget_remaining = sig.get("budget_remaining")
        obstruction_rate = sig.get("obstruction_rate")

        # Latest state values
        latest = self._history[-1] if self._history else {}
        obligation_count = latest.get("obligation_count", 999)
        coverage = latest.get("coverage", 0.0)
        drift = drift_level.value if drift_level else 0.0

        # CONVERGED: no pending obligations and drift very low
        if obligation_count == 0 and drift < 0.05:
            return Phase.CONVERGED

        # RECOVERY: high obstruction rate or stalled progress with budget
        if obstruction_rate and obstruction_rate.triggered:
            if budget_remaining and budget_remaining.value > 0.2:
                return Phase.RECOVERY
        if progress_stall and progress_stall.triggered:
            if budget_remaining and budget_remaining.value > 0.2:
                return Phase.RECOVERY

        # HARDENING: high coverage and few new obstructions (before exploitation
        # since exploitation's conditions are a subset when coverage is high)
        if coverage > 0.8:
            if obstruction_rate and not obstruction_rate.triggered:
                return Phase.HARDENING

        # EXPLORATION: low coverage and few obligations
        if coverage < 0.3 and obligation_count < 10:
            return Phase.EXPLORATION

        # EXPLOITATION: coverage growing and obligations decreasing
        if coverage_trend and coverage_trend.triggered:
            if obligation_trend and obligation_trend.triggered:
                return Phase.EXPLOITATION

        # TAIL: low budget remaining
        if budget_remaining and budget_remaining.triggered:
            return Phase.TAIL

        return Phase.EXPLORATION

    def should_transition(
        self,
        current_phase: Phase,
        signals: list[PhaseSignal],
    ) -> Optional[PhaseTransition]:
        """Return a PhaseTransition if the detected phase differs from current."""
        new_phase = self._phase_from_signals(signals)
        if new_phase == current_phase:
            return None

        reason_parts: list[str] = []
        for s in signals:
            if s.triggered:
                reason_parts.append(f"{s.signal_name}={s.value:.3f}")

        return PhaseTransition(
            from_phase=current_phase,
            to_phase=new_phase,
            signals=list(signals),
            timestamp=time.time(),
            reason="; ".join(reason_parts) if reason_parts else "phase shift",
        )
