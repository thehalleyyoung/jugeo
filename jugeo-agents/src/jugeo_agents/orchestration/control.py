"""Phase-Adaptive Control Law — selecting the next agent action based on convergence phase.

This module implements the control-theoretic layer of the orchestration
pipeline.  Given a snapshot of the current pipeline state (coverage gaps,
descent results, unresolved obstructions, budget) it produces a ranked list
of candidate actions and selects the best one.

Three concrete control laws are provided:

* **GreedyControlLaw** — always pick the highest-priority action.
* **BalancedControlLaw** — balance exploration and exploitation via a
  temperature parameter that decays with round number.
* **PhaseAdaptiveControlLaw** — adapt the action strategy to the current
  convergence phase (exploration → assign subtasks, consolidation → descent
  checks, resolution → treaty negotiation, verification → ground claims,
  complete → stop).

Supporting utilities:

* **ActionPrioritizer** — rank and filter candidate actions respecting budget
  constraints.
* **ControlHistory** — record every control decision for later analysis and
  auditability.
"""

from __future__ import annotations

import math
import time
import uuid
from abc import ABC, abstractmethod
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Final

from jugeo_agents.types import (
    ConvergencePhase,
    ConvergenceStatus,
    CoverageReport,
    DescentResult,
    Obstruction,
    TrustLevel,
)

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ActionType(Enum):
    """Enumeration of every discrete action the orchestrator can take."""

    ASSIGN_SUBTASK = auto()
    RUN_DESCENT_CHECK = auto()
    NEGOTIATE_TREATY = auto()
    GROUND_CLAIM = auto()
    CHALLENGE_CLAIM = auto()
    ESCALATE = auto()
    STOP = auto()


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class AgentAction:
    """A recommended next action emitted by a control law."""

    action_type: ActionType
    target_agent: str
    description: str
    priority: float
    estimated_cost: float
    rationale: str

    # Bookkeeping
    action_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    timestamp: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        self.priority = max(0.0, min(1.0, self.priority))


@dataclass(slots=True)
class PipelineState:
    """Immutable snapshot of the pipeline used for control-law decisions."""

    coverage: CoverageReport
    descent: DescentResult
    phase: ConvergencePhase
    round_number: int
    agent_outputs: dict[str, list]
    unresolved_obstructions: list[Obstruction]
    ungrounded_claims: list
    budget_remaining: float

    # Derived helpers -------------------------------------------------------

    @property
    def num_agents(self) -> int:
        """Number of agents that have produced output."""
        return len(self.agent_outputs)

    @property
    def total_outputs(self) -> int:
        """Total number of individual outputs across all agents."""
        return sum(len(v) for v in self.agent_outputs.values())

    @property
    def has_budget(self) -> bool:
        """Whether any budget remains for additional actions."""
        return self.budget_remaining > 0.0

    @property
    def obstruction_count(self) -> int:
        return len(self.unresolved_obstructions)

    @property
    def ungrounded_count(self) -> int:
        return len(self.ungrounded_claims)


# ---------------------------------------------------------------------------
# Abstract control law
# ---------------------------------------------------------------------------


class ControlLaw(ABC):
    """Base class for all control laws.

    A control law examines the current ``PipelineState`` and returns the
    single best ``AgentAction`` to execute next.
    """

    @abstractmethod
    def select_action(self, state: PipelineState) -> AgentAction:
        """Return the recommended next action given *state*."""

    # Convenience -----------------------------------------------------------

    def _candidate_actions(self, state: PipelineState) -> list[AgentAction]:
        """Build the full list of candidate actions for *state*.

        Sub-classes may override this to inject domain-specific candidates.
        The default implementation produces a standard set of candidates
        derived from the current pipeline state.
        """
        candidates: list[AgentAction] = []

        # Subtask assignment — one per coverage gap
        for gap in sorted(state.coverage.gaps):
            candidates.append(
                AgentAction(
                    action_type=ActionType.ASSIGN_SUBTASK,
                    target_agent=_pick_agent_for_gap(gap, state),
                    description=f"Cover gap: {gap}",
                    priority=_gap_priority(gap, state),
                    estimated_cost=1.0,
                    rationale=f"Coverage gap '{gap}' is unaddressed.",
                ),
            )

        # Descent checks — if the last descent was not fully consistent
        if not state.descent.is_consistent:
            candidates.append(
                AgentAction(
                    action_type=ActionType.RUN_DESCENT_CHECK,
                    target_agent="*",
                    description="Run descent consistency check",
                    priority=0.8,
                    estimated_cost=2.0,
                    rationale=(
                        f"Descent score is {state.descent.consistency_score:.2f}; "
                        f"{len(state.descent.obstructions)} obstruction(s) detected."
                    ),
                ),
            )

        # Treaty negotiation — one per unresolved obstruction
        for obs in state.unresolved_obstructions:
            agents_str = ", ".join(obs.agents_involved[:3])
            candidates.append(
                AgentAction(
                    action_type=ActionType.NEGOTIATE_TREATY,
                    target_agent=obs.agents_involved[0] if obs.agents_involved else "*",
                    description=f"Negotiate treaty for obstruction {obs.obstruction_id}",
                    priority=0.7,
                    estimated_cost=2.5,
                    rationale=(
                        f"Obstruction {obs.obstruction_id} ({obs.kind.value}) "
                        f"involves agents [{agents_str}]."
                    ),
                ),
            )

        # Ground claims — one per ungrounded claim
        for idx, claim in enumerate(state.ungrounded_claims):
            candidates.append(
                AgentAction(
                    action_type=ActionType.GROUND_CLAIM,
                    target_agent="*",
                    description=f"Ground unverified claim #{idx}",
                    priority=0.6,
                    estimated_cost=1.5,
                    rationale="Claim has not yet been grounded or verified.",
                ),
            )

        # Challenge — if descent found specific contradictions
        for obs in state.descent.obstructions:
            for contradiction in obs.contradictions:
                candidates.append(
                    AgentAction(
                        action_type=ActionType.CHALLENGE_CLAIM,
                        target_agent=obs.agents_involved[0] if obs.agents_involved else "*",
                        description=f"Challenge contradictory claim in obstruction {obs.obstruction_id}",
                        priority=0.75,
                        estimated_cost=1.0,
                        rationale=(
                            f"Contradiction detected in obstruction {obs.obstruction_id}."
                        ),
                    ),
                )

        # Escalation — when stuck for many rounds with no progress
        if state.round_number > 5 and state.obstruction_count > 0 and state.coverage.coverage_score < 0.5:
            candidates.append(
                AgentAction(
                    action_type=ActionType.ESCALATE,
                    target_agent="*",
                    description="Escalate: pipeline appears stuck",
                    priority=0.9,
                    estimated_cost=0.5,
                    rationale=(
                        f"Round {state.round_number} with coverage "
                        f"{state.coverage.coverage_score:.2f} and "
                        f"{state.obstruction_count} unresolved obstructions."
                    ),
                ),
            )

        # Stop — always present as a fallback
        candidates.append(
            AgentAction(
                action_type=ActionType.STOP,
                target_agent="*",
                description="Stop: no further actions needed",
                priority=0.0,
                estimated_cost=0.0,
                rationale="Fallback stop action.",
            ),
        )

        return candidates


# ---------------------------------------------------------------------------
# Concrete control laws
# ---------------------------------------------------------------------------


class GreedyControlLaw(ControlLaw):
    """Always pick the candidate action with the highest priority."""

    def select_action(self, state: PipelineState) -> AgentAction:
        candidates = self._candidate_actions(state)
        candidates = _filter_by_budget(candidates, state.budget_remaining)
        candidates.sort(key=lambda a: a.priority, reverse=True)
        return candidates[0]


class BalancedControlLaw(ControlLaw):
    """Balance exploration and exploitation using a temperature schedule.

    Early rounds favour exploratory actions (ASSIGN_SUBTASK) even when their
    raw priority is lower.  As rounds increase the temperature cools and the
    law converges to the greedy strategy.
    """

    _EXPLORATION_TYPES: Final[frozenset[ActionType]] = frozenset(
        {ActionType.ASSIGN_SUBTASK, ActionType.RUN_DESCENT_CHECK},
    )
    _EXPLOITATION_TYPES: Final[frozenset[ActionType]] = frozenset(
        {ActionType.NEGOTIATE_TREATY, ActionType.GROUND_CLAIM, ActionType.CHALLENGE_CLAIM},
    )

    def __init__(self, initial_temperature: float = 1.0, decay_rate: float = 0.15) -> None:
        self._initial_temperature = initial_temperature
        self._decay_rate = decay_rate

    def _temperature(self, round_number: int) -> float:
        """Exponentially decaying temperature."""
        return self._initial_temperature * math.exp(-self._decay_rate * round_number)

    def select_action(self, state: PipelineState) -> AgentAction:
        candidates = self._candidate_actions(state)
        candidates = _filter_by_budget(candidates, state.budget_remaining)

        temp = self._temperature(state.round_number)

        def _score(action: AgentAction) -> float:
            bonus = 0.0
            if action.action_type in self._EXPLORATION_TYPES:
                bonus = temp * 0.3
            elif action.action_type in self._EXPLOITATION_TYPES:
                bonus = (1.0 - temp) * 0.2
            return action.priority + bonus

        candidates.sort(key=_score, reverse=True)
        return candidates[0]


class PhaseAdaptiveControlLaw(ControlLaw):
    """Adapt the action selection strategy to the current convergence phase.

    Phase mapping:

    * **EXPLORATION** → ``ASSIGN_SUBTASK`` (maximise parallelism, fill gaps)
    * **CONSOLIDATION** → ``RUN_DESCENT_CHECK`` (surface contradictions)
    * **RESOLUTION** → ``NEGOTIATE_TREATY`` (resolve inter-agent conflicts)
    * **VERIFICATION** → ``GROUND_CLAIM`` (verify ungrounded claims)
    * **COMPLETE** → ``STOP``
    """

    _PHASE_TO_PREFERRED: Final[dict[ConvergencePhase, ActionType]] = {
        ConvergencePhase.EXPLORATION: ActionType.ASSIGN_SUBTASK,
        ConvergencePhase.CONSOLIDATION: ActionType.RUN_DESCENT_CHECK,
        ConvergencePhase.RESOLUTION: ActionType.NEGOTIATE_TREATY,
        ConvergencePhase.VERIFICATION: ActionType.GROUND_CLAIM,
        ConvergencePhase.COMPLETE: ActionType.STOP,
    }

    # How much to boost the preferred action type for the current phase.
    _PHASE_BOOST: Final[float] = 0.35

    def select_action(self, state: PipelineState) -> AgentAction:
        # Short-circuit for the terminal phase.
        if state.phase is ConvergencePhase.COMPLETE:
            return AgentAction(
                action_type=ActionType.STOP,
                target_agent="*",
                description="Pipeline complete — no further actions.",
                priority=1.0,
                estimated_cost=0.0,
                rationale="Convergence phase is COMPLETE.",
            )

        candidates = self._candidate_actions(state)
        candidates = _filter_by_budget(candidates, state.budget_remaining)

        preferred = self._PHASE_TO_PREFERRED.get(state.phase)

        def _phase_score(action: AgentAction) -> float:
            boost = self._PHASE_BOOST if action.action_type is preferred else 0.0
            return action.priority + boost

        candidates.sort(key=_phase_score, reverse=True)
        return candidates[0]


# ---------------------------------------------------------------------------
# Action prioritiser
# ---------------------------------------------------------------------------


class ActionPrioritizer:
    """Rank and filter a list of candidate actions.

    Sorting is by descending priority.  Actions whose estimated cost exceeds
    the remaining budget are pushed to the back unless *allow_over_budget* is
    ``True``.
    """

    def __init__(self, allow_over_budget: bool = False) -> None:
        self._allow_over_budget = allow_over_budget

    def prioritize(
        self,
        actions: list[AgentAction],
        state: PipelineState,
    ) -> list[AgentAction]:
        """Return *actions* sorted by priority, respecting budget constraints.

        Actions that exceed the remaining budget are moved to the end of the
        list (but not removed) so callers can still inspect them.
        """
        affordable: list[AgentAction] = []
        over_budget: list[AgentAction] = []

        for action in actions:
            if action.estimated_cost <= state.budget_remaining or self._allow_over_budget:
                affordable.append(action)
            else:
                over_budget.append(action)

        affordable.sort(key=lambda a: a.priority, reverse=True)
        over_budget.sort(key=lambda a: a.priority, reverse=True)

        return affordable + over_budget


# ---------------------------------------------------------------------------
# Control history
# ---------------------------------------------------------------------------


@dataclass
class _HistoryEntry:
    """Internal record of a single control decision."""

    action: AgentAction
    round_number: int
    recorded_at: float = field(default_factory=time.time)


class ControlHistory:
    """Append-only ledger of control decisions.

    Provides basic analytics (action distribution, filtering by round) that
    are useful for monitoring and debugging the orchestration pipeline.
    """

    def __init__(self) -> None:
        self._entries: list[_HistoryEntry] = []

    # Recording -------------------------------------------------------------

    def record(self, action: AgentAction, round_number: int) -> None:
        """Append *action* to the history for the given *round_number*."""
        self._entries.append(_HistoryEntry(action=action, round_number=round_number))

    # Queries ---------------------------------------------------------------

    def actions_taken(self) -> list[AgentAction]:
        """Return all recorded actions in chronological order."""
        return [e.action for e in self._entries]

    def action_distribution(self) -> dict[str, int]:
        """Return a mapping from action-type name to occurrence count."""
        counter: Counter[str] = Counter()
        for entry in self._entries:
            counter[entry.action.action_type.name] += 1
        return dict(counter)

    def actions_in_round(self, round_number: int) -> list[AgentAction]:
        """Return all actions recorded for a specific *round_number*."""
        return [e.action for e in self._entries if e.round_number == round_number]

    def rounds_recorded(self) -> list[int]:
        """Return the sorted list of distinct round numbers in the history."""
        return sorted({e.round_number for e in self._entries})

    @property
    def total_cost(self) -> float:
        """Sum of estimated costs for every action ever recorded."""
        return sum(e.action.estimated_cost for e in self._entries)

    @property
    def size(self) -> int:
        """Number of entries in the history."""
        return len(self._entries)

    def __len__(self) -> int:
        return self.size

    def __repr__(self) -> str:
        return (
            f"ControlHistory(entries={self.size}, "
            f"rounds={len(self.rounds_recorded())}, "
            f"total_cost={self.total_cost:.2f})"
        )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _filter_by_budget(
    actions: list[AgentAction],
    budget: float,
) -> list[AgentAction]:
    """Return only actions affordable under *budget*, always keeping STOP."""
    affordable = [a for a in actions if a.estimated_cost <= budget]
    if not affordable:
        # Always provide at least the stop action.
        return [
            a for a in actions if a.action_type is ActionType.STOP
        ] or [
            AgentAction(
                action_type=ActionType.STOP,
                target_agent="*",
                description="Budget exhausted — forced stop.",
                priority=1.0,
                estimated_cost=0.0,
                rationale="No affordable actions remain.",
            ),
        ]
    return affordable


def _pick_agent_for_gap(gap: str, state: PipelineState) -> str:
    """Heuristically choose the best agent to cover *gap*.

    If an agent already has assignments for a dimension that lexically
    overlaps with the gap name we prefer that agent (locality heuristic).
    Otherwise we pick the agent with the fewest outputs so far (load
    balancing).
    """
    if not state.agent_outputs:
        return "*"

    # Check dimension_assignments from the coverage report.
    for dim, agents in state.coverage.dimension_assignments.items():
        if gap in dim or dim in gap:
            if agents:
                return agents[0]

    # Fall back to least-loaded agent.
    least_loaded = min(state.agent_outputs, key=lambda aid: len(state.agent_outputs[aid]))
    return least_loaded


def _gap_priority(gap: str, state: PipelineState) -> float:
    """Assign a priority to a coverage gap.

    Gaps that correspond to completely uncovered dimensions receive a higher
    priority than those in partially-covered areas.
    """
    if gap not in state.coverage.covered_dimensions:
        return 0.85  # Entirely uncovered — high priority.
    return 0.5  # Partially addressed — moderate priority.
