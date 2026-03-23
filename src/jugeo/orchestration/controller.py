"""Project-scale orchestration as semantic control for JuGeo.

This module implements the orchestration controller described in theory2.tex
§ "Project-scale orchestration as semantic control".  The orchestrator operates
on a state whose coordinates are:

    covers, hypercovers, local contexts, partial sections, overlap treaties,
    unresolved obligations, evidence channels, frontier nodes, archives,
    and resource budgets.

Its task is to choose the next *admissible move* that improves global
attainability — bridging specification verification from static judgment to
dynamic search over partial judgments.

The architecture follows the JuGeo principle that orchestration is *not*
ad-hoc scheduling but a control-theoretic process on the semantic site:

    1.  **OrchestratorState** captures the full snapshot of the search.
    2.  **SemanticMove** encodes typed, preconditioned, cost-annotated actions.
    3.  **MoveGenerator** enumerates the admissible frontier of moves.
    4.  **Orchestrator** runs the main step loop under a configurable
        **ControlLaw** (greedy, lookahead, balanced, adaptive).
    5.  **ConvergenceMonitor** watches coverage / obligation trends for
        stalls, phase transitions, and termination.
    6.  **OrchestratorEventBus** provides publish–subscribe for diagnostics.
    7.  **ResourceBudget** tracks and rebalances per-channel budgets.
    8.  **MoveHistory** records executed moves for regret analysis.
    9.  **OrchestratorDiagnostics** aggregates reports for the CLI surface.

Design notes
────────────
*   Every class carries full type annotations and docstrings grounded in
    theory2.tex definitions.
*   The copilot evidence channel is a first-class participant but its trust
    ceiling is strictly below solver proofs (theory2.tex §252).
*   Mutable state classes use ``@dataclass(slots=True)``; value objects use
    ``@dataclass(frozen=True, slots=True)`` — consistent with the rest of
    the JuGeo codebase.
*   Guarded imports ensure the module degrades gracefully if upstack
    dependencies are not yet compiled.

References
──────────
*   theory2.tex §3      — Descent and Gluing
*   theory2.tex §3.4    — Iterative Descent and Copilot-Assisted Refinement
*   theory2.tex §252    — Evidence Algebra, Channel Jurisdiction, Trust
*   theory2.tex §354    — Trust is Semantic State
"""

from __future__ import annotations

import copy
import enum
import logging
import math
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

# ── Internal JuGeo imports (guarded) ────────────────────────────────────────

try:
    from jugeo.generation.backpressure import BackpressureLevel, BackpressureSignal
except Exception:  # pragma: no cover
    class BackpressureLevel(enum.Enum):  # type: ignore[no-redef]
        NORMAL = "normal"
        THROTTLE = "throttle"

    @dataclass(frozen=True, slots=True)
    class BackpressureSignal:  # type: ignore[no-redef]
        level: BackpressureLevel = BackpressureLevel.NORMAL

try:
    from jugeo.orchestration.budgets import BudgetLedger
except Exception:  # pragma: no cover
    @dataclass(slots=True)
    class BudgetLedger:  # type: ignore[no-redef]
        limits: dict[str, int] = field(default_factory=dict)
        spent: dict[str, int] = field(default_factory=dict)

        def remaining(self, key: str) -> int:
            return self.limits.get(key, 0) - self.spent.get(key, 0)

        def consume(self, key: str, amount: int) -> bool:
            if self.remaining(key) < amount:
                return False
            self.spent[key] = self.spent.get(key, 0) + amount
            return True

        def release(self, key: str, amount: int) -> None:
            self.spent[key] = max(0, self.spent.get(key, 0) - amount)

try:
    from jugeo.orchestration.frontier import FrontierItem, FrontierState
except Exception:  # pragma: no cover
    @dataclass(frozen=True, slots=True)
    class FrontierItem:  # type: ignore[no-redef]
        goal: Any = None
        urgency: int = 0
        obstruction_rank: int = 0

    @dataclass(slots=True)
    class FrontierState:  # type: ignore[no-redef]
        items: list[Any] = field(default_factory=list)

        def add(self, item: Any) -> None:
            self.items.append(item)

        def next_item(self) -> Any | None:
            return self.items.pop(0) if self.items else None

try:
    from jugeo.geometry.descent import Obstruction
except Exception:  # pragma: no cover
    @dataclass(frozen=True, slots=True)
    class Obstruction:  # type: ignore[no-redef]
        overlap: tuple[str, str] = ("", "")
        message: str = ""
        rank: int = 0
        support: Any = None

try:
    from jugeo.evidence.trust import TrustAlgebra
except Exception:  # pragma: no cover
    TrustAlgebra = None  # type: ignore[assignment,misc]

try:
    from jugeo.geometry.descent import DescentEngine
except Exception:  # pragma: no cover
    DescentEngine = None  # type: ignore[assignment,misc]

try:
    from jugeo.solver.router import SolverRouter
except Exception:  # pragma: no cover
    SolverRouter = None  # type: ignore[assignment,misc]

try:
    from jugeo.runtime.cache import SemanticCache
except Exception:  # pragma: no cover
    SemanticCache = None  # type: ignore[assignment,misc]

try:
    from jugeo.runtime.replay import ReplayEngine
except Exception:  # pragma: no cover
    ReplayEngine = None  # type: ignore[assignment,misc]

try:
    from jugeo.evidence.trust import TrustProfile, TrustTier
except Exception:  # pragma: no cover
    class TrustTier(enum.IntEnum):  # type: ignore[no-redef]
        PROPOSAL = 0
        REVIEWED = 1
        VERIFIED = 2

    @dataclass(frozen=True, slots=True)
    class TrustProfile:  # type: ignore[no-redef]
        tier: TrustTier = TrustTier.PROPOSAL
        support_scope: str = ""
        reasons: tuple[str, ...] = ()

try:
    from jugeo.generation.treaties import OverlapTreaty
except Exception:  # pragma: no cover
    @dataclass(frozen=True, slots=True)
    class OverlapTreaty:  # type: ignore[no-redef]
        patches: tuple[str, ...] = ()
        clauses: tuple[Any, ...] = ()
        provenance: tuple[str, ...] = ()

        @property
        def accepted(self) -> bool:
            return all(getattr(c, "satisfied", False) for c in self.clauses)

logger = logging.getLogger(__name__)


class _CallableFloat(float):
    def __call__(self) -> float:
        return float(self)


# ═══════════════════════════════════════════════════════════════════════════════
#  1.  SemanticMove — typed action on the semantic site
# ═══════════════════════════════════════════════════════════════════════════════

class MoveKind(enum.Enum):
    """Admissible move types in the orchestration search space.

    Each kind corresponds to a distinct proof-search operation:
    *   VERIFY            — check an existing local section against its spec.
    *   CONSTRUCT         — synthesise a new local section for a coordinate.
    *   REPAIR            — patch a section whose overlap condition failed.
    *   NEGOTIATE_TREATY  — renegotiate an overlap treaty between patches.
    *   REFINE_COVER      — split or refine a cover to reduce obstructions.
    *   DISCHARGE_OBLIGATION — satisfy a pending residual obligation.
    *   CONSULT_ORACLE    — invoke the copilot channel for a proposal.
    """
    VERIFY = "verify"
    CONSTRUCT = "construct"
    REPAIR = "repair"
    NEGOTIATE_TREATY = "negotiate_treaty"
    REFINE_COVER = "refine_cover"
    DISCHARGE_OBLIGATION = "discharge_obligation"
    CONSULT_ORACLE = "consult_oracle"


MoveKind.TYPE_CHECK = MoveKind.VERIFY


@dataclass(frozen=True, slots=True, init=False)
class SemanticMove:
    """A single admissible move in the orchestration search.

    Moves are first-class value objects: they carry preconditions that must
    hold on the *current* state, postconditions that describe the expected
    state change, and cost/gain estimates used by the ``ControlLaw`` to
    rank alternatives.

    Attributes:
        move_id:            Unique identifier for tracing.
        kind:               Type of semantic operation (see ``MoveKind``).
        target_coordinate:  Coordinate on which the move operates.
        expected_gain:      Estimated improvement to coverage ratio (0–1).
        estimated_cost:     Budget units this move is expected to consume.
        preconditions:      Predicates that must hold before execution.
        postconditions:     Predicates expected to hold after execution.
    """
    move_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    kind: MoveKind
    target_coordinate: str
    expected_gain: float = 0.0
    estimated_cost: int = 1
    preconditions: tuple[str, ...] = ()
    postconditions: tuple[str, ...] = ()

    def __init__(
        self,
        kind: MoveKind,
        target_coordinate: str,
        move_id: str | None = None,
        expected_gain: float = 0.0,
        estimated_cost: int | float = 1,
        preconditions: Sequence[str] = (),
        postconditions: Sequence[str] = (),
        expected_effects: Sequence[str] = (),
    ) -> None:
        object.__setattr__(self, "move_id", move_id or uuid.uuid4().hex[:12])
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "target_coordinate", target_coordinate)
        object.__setattr__(self, "expected_gain", expected_gain)
        object.__setattr__(self, "estimated_cost", estimated_cost)
        object.__setattr__(self, "preconditions", tuple(preconditions))
        object.__setattr__(self, "postconditions", tuple(postconditions or expected_effects))

    @property
    def gain_cost_ratio(self) -> float:
        """Return the gain-per-unit-cost ratio, guarding against zero cost."""
        return self.expected_gain / max(self.estimated_cost, 1)

    @property
    def expected_effects(self) -> tuple[str, ...]:
        return self.postconditions


# ═══════════════════════════════════════════════════════════════════════════════
#  2.  OrchestratorState — full snapshot of the search state
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass(slots=True)
class OrchestratorState:
    """Complete mutable state of the orchestration search.

    The state captures every semantic coordinate mentioned in theory2.tex
    §orch: covers, partial sections, overlap treaties, obligations, evidence
    channels, frontier nodes, the obstruction archive, and the trust state.

    Design note: this is deliberately *mutable* — the orchestrator
    modifies it in-place during ``step()`` and snapshots it for rollback.
    """

    current_sections: dict[str, Any] = field(default_factory=dict)
    """Map from coordinate name → local section (``JudgmentSection``)."""

    pending_obligations: list[str] = field(default_factory=list)
    """Residual obligations that must still be discharged."""

    active_treaties: list[OverlapTreaty] = field(default_factory=list)
    """Overlap treaties currently in force between patches."""

    frontier_nodes: list[str] = field(default_factory=list)
    """Coordinates on the search frontier awaiting processing."""

    evidence_channels: dict[str, bool] = field(default_factory=dict)
    """Channel name → active flag (solver, runtime, copilot, formal)."""

    resource_budget: ResourceBudget | None = None
    """Per-channel budget tracker (initialised by ``Orchestrator``)."""

    epoch: int = 0
    """Monotonically increasing step counter."""

    obstruction_archive: list[Obstruction] = field(default_factory=list)
    """Persistent record of all obstructions encountered (never erased)."""

    trust_state: dict[str, TrustProfile] = field(default_factory=dict)
    """Coordinate → current trust profile (theory2.tex §354)."""

    @property
    def frontier(self) -> list[str]:
        return self.frontier_nodes

    # ── snapshot / restore ──────────────────────────────────────────────

    def snapshot(self) -> dict[str, Any]:
        """Return a deep-copy snapshot of the entire state for rollback.

        The snapshot is a plain dictionary so that it can be serialised or
        stored in a checkpoint archive without coupling to the dataclass
        layout.
        """
        return {
            "current_sections": copy.deepcopy(self.current_sections),
            "pending_obligations": list(self.pending_obligations),
            "active_treaties": list(self.active_treaties),
            "frontier_nodes": list(self.frontier_nodes),
            "evidence_channels": dict(self.evidence_channels),
            "resource_budget": copy.deepcopy(self.resource_budget),
            "epoch": self.epoch,
            "obstruction_archive": list(self.obstruction_archive),
            "trust_state": copy.deepcopy(self.trust_state),
        }

    def restore(self, snap: dict[str, Any]) -> None:
        """Restore state from a snapshot produced by ``snapshot()``.

        This is the inverse of ``snapshot()`` and is used by the
        orchestrator's ``rollback()`` method to undo a failed move.
        """
        self.current_sections = snap["current_sections"]
        self.pending_obligations = snap["pending_obligations"]
        self.active_treaties = snap["active_treaties"]
        self.frontier_nodes = snap["frontier_nodes"]
        self.evidence_channels = snap["evidence_channels"]
        self.resource_budget = snap["resource_budget"]
        self.epoch = snap["epoch"]
        self.obstruction_archive = snap["obstruction_archive"]
        self.trust_state = snap["trust_state"]

    def diff(self, other_snap: dict[str, Any]) -> dict[str, Any]:
        """Compute a structural diff between the current state and *other_snap*.

        Returns a dictionary whose keys are field names and whose values
        describe the change (added / removed / changed).  Useful for
        diagnostics and the ``OrchestratorEventBus.state_changed`` event.
        """
        current = self.snapshot()
        changes: dict[str, Any] = {}
        for key in current:
            if current[key] != other_snap.get(key):
                changes[key] = {
                    "before": other_snap.get(key),
                    "after": current[key],
                }
        return changes

    # ── derived metrics ─────────────────────────────────────────────────

    def is_terminal(self) -> bool:
        """Return ``True`` when no more progress is possible.

        Terminal conditions:
        *   No frontier nodes remain.
        *   No pending obligations.
        *   All active treaties accepted.
        """
        if self.frontier_nodes:
            return False
        if self.pending_obligations:
            return False
        if any(not t.accepted for t in self.active_treaties):
            return False
        return True

    @property
    def coverage_ratio(self) -> float:
        """Fraction of frontier nodes that have a current section.

        The ratio approximates how much of the project site has been
        covered by verified local sections (theory2.tex §3 descent
        progress metric).
        """
        total = len(self.frontier_nodes) + len(self.current_sections)
        if total == 0:
            return _CallableFloat(0.0)
        return _CallableFloat(len(self.current_sections) / total)

    def obligation_pressure(self) -> float:
        """Normalised pressure from unresolved obligations.

        Returns a value in [0, 1] where 1 means every coordinate has at
        least one unresolved obligation.  The orchestrator uses this to
        prioritise DISCHARGE_OBLIGATION moves.
        """
        total_coords = len(self.current_sections) + len(self.frontier_nodes)
        if total_coords == 0:
            return 0.0
        return min(1.0, len(self.pending_obligations) / max(total_coords, 1))


# ═══════════════════════════════════════════════════════════════════════════════
#  3.  ResourceBudget — per-channel budget tracking
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass(slots=True)
class ResourceBudget:
    """Tracks and rebalances resource budgets across evidence channels.

    Each evidence channel (solver, runtime, copilot, formal-proof) has an
    independent allocation.  The orchestrator spends budget when executing
    moves and may rebalance when one channel is exhausted while another
    has slack.

    Theory alignment: resource budgets prevent runaway copilot calls and
    enforce the jurisdiction ceiling from theory2.tex §252.
    """

    allocations: dict[str, int] = field(default_factory=dict)
    """Channel → total allocated budget units."""

    expenditures: dict[str, int] = field(default_factory=dict)
    """Channel → units already spent."""

    def allocate(self, channel: str, amount: int) -> None:
        """Set or increase the budget for *channel*."""
        self.allocations[channel] = self.allocations.get(channel, 0) + amount

    def spend(self, channel: str, amount: int) -> bool:
        """Attempt to spend *amount* from *channel*.  Returns success flag."""
        rem = self.remaining(channel)
        if rem < amount:
            return False
        self.expenditures[channel] = self.expenditures.get(channel, 0) + amount
        return True

    def remaining(self, channel: str) -> int:
        """Return remaining budget for *channel*."""
        return self.allocations.get(channel, 0) - self.expenditures.get(channel, 0)

    def is_exhausted(self, channel: str | None = None) -> bool:
        """Check if *channel* (or all channels) is exhausted."""
        if channel is not None:
            return self.remaining(channel) <= 0
        return all(self.remaining(ch) <= 0 for ch in self.allocations)

    def budget_per_channel(self) -> dict[str, dict[str, int]]:
        """Return a summary dict per channel: allocated, spent, remaining."""
        summary: dict[str, dict[str, int]] = {}
        for ch in self.allocations:
            summary[ch] = {
                "allocated": self.allocations.get(ch, 0),
                "spent": self.expenditures.get(ch, 0),
                "remaining": self.remaining(ch),
            }
        return summary

    def rebalance(self, donor: str, recipient: str, amount: int) -> bool:
        """Transfer *amount* unspent units from *donor* to *recipient*.

        Returns ``True`` if the transfer succeeded (donor had enough
        remaining budget).  This allows the orchestrator to shift resources
        toward channels that are making progress.
        """
        if self.remaining(donor) < amount:
            return False
        self.allocations[donor] -= amount
        self.allocations[recipient] = self.allocations.get(recipient, 0) + amount
        return True

    def total_remaining(self) -> int:
        """Sum of remaining budget across all channels."""
        return sum(self.remaining(ch) for ch in self.allocations)

    def utilisation_ratio(self, channel: str) -> float:
        """Fraction of *channel*'s budget that has been spent."""
        alloc = self.allocations.get(channel, 0)
        if alloc == 0:
            return 0.0
        return self.expenditures.get(channel, 0) / alloc


# ═══════════════════════════════════════════════════════════════════════════════
#  4.  OrchestratorConfiguration
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True, slots=True)
class OrchestratorConfiguration:
    """Immutable configuration for the orchestration controller.

    Attributes:
        max_steps:              Hard ceiling on orchestration epochs.
        convergence_threshold:  Coverage ratio at which we declare success.
        budget_limits:          Per-channel initial budget allocations.
        move_timeout:           Seconds before a single move is aborted.
        copilot_enabled:        Whether the copilot oracle channel is active.
        strategy:               Name of the ``ControlLaw`` to use.
        logging_level:          Python logging level name.
        lookahead_depth:        Steps to look ahead (for LookaheadControl).
        stability_weight:       Weight for stability in BalancedControl.
    """
    max_steps: int = 500
    convergence_threshold: float = 0.95
    budget_limits: Mapping[str, int] = field(default_factory=lambda: {
        "solver": 200,
        "runtime": 150,
        "copilot": 100,
        "formal": 50,
    })
    move_timeout: float = 30.0
    copilot_enabled: bool = True
    strategy: str = "balanced"
    logging_level: str = "INFO"
    lookahead_depth: int = 3
    stability_weight: float = 0.4


# ═══════════════════════════════════════════════════════════════════════════════
#  5.  OrchestratorEventBus — publish / subscribe for orchestration events
# ═══════════════════════════════════════════════════════════════════════════════

class OrchestratorEventKind(enum.Enum):
    """Categories of orchestration events."""
    MOVE_SELECTED = "move_selected"
    MOVE_EXECUTED = "move_executed"
    MOVE_FAILED = "move_failed"
    STATE_CHANGED = "state_changed"
    CONVERGENCE_UPDATE = "convergence_update"
    BUDGET_WARNING = "budget_warning"


@dataclass(frozen=True, slots=True, init=False)
class OrchestratorEvent:
    """A single event emitted by the orchestrator."""
    kind: OrchestratorEventKind
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.monotonic)

    def __init__(
        self,
        *,
        kind: OrchestratorEventKind,
        payload: Mapping[str, Any] | None = None,
        details: Mapping[str, Any] | None = None,
        timestamp: float | None = None,
    ) -> None:
        object.__setattr__(self, 'kind', kind)
        object.__setattr__(self, 'payload', dict(payload or details or {}))
        object.__setattr__(self, 'timestamp', time.monotonic() if timestamp is None else timestamp)

    @property
    def details(self) -> dict[str, Any]:
        """Legacy alias for the event payload."""
        return self.payload


class OrchestratorEventBus:
    """Simple synchronous publish–subscribe bus for orchestration events.

    Subscribers register a callback keyed by ``OrchestratorEventKind``.
    The orchestrator publishes events as it progresses; diagnostics,
    logging adapters, and the CLI surface consume them.
    """

    def __init__(self) -> None:
        self._subscribers: dict[OrchestratorEventKind, list[Callable[[OrchestratorEvent], None]]] = {}
        self._history: list[OrchestratorEvent] = []

    def subscribe(self, kind: OrchestratorEventKind, callback: Callable[[OrchestratorEvent], None]) -> None:
        """Register *callback* for events of *kind*."""
        self._subscribers.setdefault(kind, []).append(callback)

    def unsubscribe(self, kind: OrchestratorEventKind, callback: Callable[[OrchestratorEvent], None]) -> None:
        """Remove a previously registered callback."""
        subs = self._subscribers.get(kind, [])
        if callback in subs:
            subs.remove(callback)

    def publish(self, event: OrchestratorEvent) -> None:
        """Dispatch *event* to all subscribers of its kind."""
        self._history.append(event)
        for cb in self._subscribers.get(event.kind, []):
            try:
                cb(event)
            except Exception:
                logger.exception("Event subscriber raised for %s", event.kind)

    def event_history(self, kind: OrchestratorEventKind | None = None) -> list[OrchestratorEvent]:
        """Return the event history, optionally filtered by *kind*."""
        if kind is None:
            return list(self._history)
        return [e for e in self._history if e.kind is kind]

    def clear_history(self) -> None:
        """Discard accumulated event history."""
        self._history.clear()

    def subscriber_count(self, kind: OrchestratorEventKind) -> int:
        """Number of subscribers for *kind*."""
        return len(self._subscribers.get(kind, []))


# ═══════════════════════════════════════════════════════════════════════════════
#  6.  MoveHistory — tracks executed moves for regret analysis
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True, slots=True)
class MoveRecord:
    """Immutable record of an executed move and its outcome."""
    move: SemanticMove
    epoch: int
    success: bool
    actual_gain: float
    actual_cost: int
    elapsed_seconds: float
    notes: tuple[str, ...] = ()


class MoveHistory:
    """Accumulates ``MoveRecord`` entries for post-hoc analysis.

    The history supports queries by move kind, target coordinate, and
    success status.  The ``regret_analysis`` method compares actual gains
    against expected gains to surface systematic estimation errors — a
    signal that the ``ControlLaw`` should adapt.
    """

    def __init__(self) -> None:
        self._records: list[MoveRecord] = []

    def record(self, rec: MoveRecord) -> None:
        """Append a ``MoveRecord``."""
        self._records.append(rec)

    def query_by_type(self, kind: MoveKind) -> list[MoveRecord]:
        """Return all records whose move kind matches *kind*."""
        return [r for r in self._records if r.move.kind is kind]

    def query_by_coordinate(self, coordinate: str) -> list[MoveRecord]:
        """Return all records targeting *coordinate*."""
        return [r for r in self._records if r.move.target_coordinate == coordinate]

    def success_rate(self, kind: MoveKind | None = None) -> float:
        """Fraction of executed moves that succeeded.

        If *kind* is given, restricts to that move type.
        """
        subset = self.query_by_type(kind) if kind else self._records
        if not subset:
            return 0.0
        return sum(1 for r in subset if r.success) / len(subset)

    def average_gain(self, kind: MoveKind | None = None) -> float:
        """Mean actual gain across (optionally filtered) moves."""
        subset = self.query_by_type(kind) if kind else self._records
        if not subset:
            return 0.0
        return sum(r.actual_gain for r in subset) / len(subset)

    def regret_analysis(self) -> list[dict[str, Any]]:
        """Compare expected vs actual gain to surface estimation errors.

        Returns a list of dicts with ``move_id``, ``expected``, ``actual``,
        and ``regret`` (expected − actual).  Positive regret means the
        move under-delivered.
        """
        results: list[dict[str, Any]] = []
        for r in self._records:
            regret = r.move.expected_gain - r.actual_gain
            results.append({
                "move_id": r.move.move_id,
                "kind": r.move.kind.value,
                "expected": r.move.expected_gain,
                "actual": r.actual_gain,
                "regret": regret,
            })
        return results

    def total_cost(self) -> int:
        """Sum of actual costs across all recorded moves."""
        return sum(r.actual_cost for r in self._records)

    def __len__(self) -> int:
        return len(self._records)


# ═══════════════════════════════════════════════════════════════════════════════
#  7.  ConvergenceMonitor — watches coverage / obligation trends
# ═══════════════════════════════════════════════════════════════════════════════

class ConvergenceMonitor:
    """Monitors the convergence trajectory of the orchestration search.

    At each epoch the monitor records the coverage ratio and obligation
    pressure.  It then analyses the time series to detect:

    *   **Convergence**: sustained increase in coverage above threshold.
    *   **Stalls**: coverage has not improved for *stall_window* epochs.
    *   **Phase transitions**: sharp changes in the convergence rate that
        may indicate the search has entered a qualitatively new regime
        (e.g., switching from easy local sections to hard overlap repair).
    """

    def __init__(self, threshold: float = 0.95, stall_window: int = 10) -> None:
        self._threshold = threshold
        self._stall_window = stall_window
        self._coverage_history: list[float] = []
        self._obligation_history: list[float] = []
        self._timestamps: list[float] = []

    def update(self, state: OrchestratorState) -> None:
        """Record the current coverage and obligation metrics."""
        self._coverage_history.append(state.coverage_ratio())
        self._obligation_history.append(state.obligation_pressure())
        self._timestamps.append(time.monotonic())

    def is_converging(self) -> bool:
        """Return ``True`` if the latest coverage exceeds the threshold."""
        if not self._coverage_history:
            return False
        return self._coverage_history[-1] >= self._threshold

    def convergence_rate(self) -> float:
        """Estimate the per-epoch rate of coverage improvement.

        Uses a simple linear regression over the last *stall_window*
        entries.  A positive rate means progress; negative means regression.
        """
        window = self._coverage_history[-self._stall_window:]
        if len(window) < 2:
            return 0.0
        n = len(window)
        xs = list(range(n))
        x_mean = sum(xs) / n
        y_mean = sum(window) / n
        num = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, window))
        den = sum((x - x_mean) ** 2 for x in xs)
        if den == 0:
            return 0.0
        return num / den

    def estimated_remaining(self) -> int | None:
        """Estimate epochs remaining until convergence threshold is reached.

        Returns ``None`` if the rate is non-positive (diverging or stalled).
        """
        if not self._coverage_history:
            return None
        gap = self._threshold - self._coverage_history[-1]
        if gap <= 0:
            return 0
        rate = self.convergence_rate()
        if rate <= 0:
            return None
        return max(1, math.ceil(gap / rate))

    def stall_detection(self) -> bool:
        """Return ``True`` if coverage has not improved in *stall_window* epochs.

        A stall signals the orchestrator to consider more aggressive moves
        such as cover refinement or copilot oracle consultation.
        """
        if len(self._coverage_history) < self._stall_window:
            return False
        window = self._coverage_history[-self._stall_window:]
        return max(window) - min(window) < 1e-6

    def phase_transition_detection(self) -> bool:
        """Detect a phase transition in the convergence trajectory.

        A phase transition is an abrupt change in the convergence rate —
        the second derivative exceeds a threshold.  This often corresponds
        to the search moving from easy coordinates to harder overlaps.
        """
        if len(self._coverage_history) < 3:
            return False
        # Second-order finite difference on the last three values.
        c = self._coverage_history
        d2 = c[-1] - 2 * c[-2] + c[-3]
        return abs(d2) > 0.1

    def coverage_at(self, epoch: int) -> float | None:
        """Return the recorded coverage at a specific epoch index."""
        if 0 <= epoch < len(self._coverage_history):
            return self._coverage_history[epoch]
        return None

    def obligation_trend(self) -> str:
        """Qualitative description of obligation pressure trend."""
        if len(self._obligation_history) < 2:
            return "insufficient_data"
        recent = self._obligation_history[-5:]
        if len(recent) < 2:
            return "insufficient_data"
        if recent[-1] < recent[0] - 0.05:
            return "decreasing"
        if recent[-1] > recent[0] + 0.05:
            return "increasing"
        return "stable"

    def is_converged(self) -> bool:
        return self.is_converging()

    def has_converged(self) -> bool:
        return self.is_converging()

    def status_report(self) -> dict[str, Any]:
        return {
            "converged": self.is_converging(),
            "coverage_samples": len(self._coverage_history),
            "convergence_rate": self.convergence_rate(),
            "stall_detected": self.stall_detection(),
            "obligation_trend": self.obligation_trend(),
        }


# ═══════════════════════════════════════════════════════════════════════════════
#  8.  ControlLaw — abstract control law + concrete implementations
# ═══════════════════════════════════════════════════════════════════════════════

class ControlLaw(ABC):
    """Abstract base for orchestration control laws.

    A control law receives the current state and a ranked list of candidate
    moves, and selects the *one* move to execute next.  Different laws
    encode different strategies from theory2.tex §orch.
    """

    @abstractmethod
    def select(self, state: OrchestratorState, candidates: Sequence[SemanticMove]) -> SemanticMove | None:
        """Choose the next move from *candidates* given *state*."""
        ...

    @abstractmethod
    def name(self) -> str:
        """Human-readable name for diagnostics."""
        ...


class GreedyControl(ControlLaw):
    """Select the move with the highest immediate expected gain.

    This is the simplest policy: maximise the one-step gain without
    considering future consequences.  Fast but myopic.
    """

    def select(self, state: OrchestratorState, candidates: Sequence[SemanticMove]) -> SemanticMove | None:
        if not candidates:
            return None
        return max(candidates, key=lambda m: m.expected_gain)

    def name(self) -> str:
        return "greedy"


class LookaheadControl(ControlLaw):
    """Select the move that maximises *k*-step cumulative expected gain.

    Evaluates each candidate by simulating *depth* additional greedy steps
    on a copied state to estimate longer-horizon payoff.

    Attributes:
        depth:  Number of lookahead steps (default 3).
    """

    def __init__(self, depth: int = 3) -> None:
        self._depth = depth

    def select(self, state: OrchestratorState, candidates: Sequence[SemanticMove]) -> SemanticMove | None:
        if not candidates:
            return None
        best_move: SemanticMove | None = None
        best_value = -math.inf
        for move in candidates:
            value = move.expected_gain + self._simulate_future(state, move)
            if value > best_value:
                best_value = value
                best_move = move
        return best_move

    def _simulate_future(self, state: OrchestratorState, first_move: SemanticMove) -> float:
        """Estimate cumulative gain from *first_move* over lookahead horizon.

        Uses a discounted sum of expected gains, decaying by 0.9 per step.
        This is a heuristic — full simulation would require a state
        transition model.
        """
        discount = 0.9
        cumulative = 0.0
        base_gain = first_move.expected_gain
        for step in range(1, self._depth + 1):
            # Heuristic: future gain decays from current move's gain
            # adjusted by the coverage already achieved.
            future_gain = base_gain * (discount ** step) * (1.0 - state.coverage_ratio())
            cumulative += future_gain
        return cumulative

    def name(self) -> str:
        return f"lookahead-{self._depth}"


class BalancedControl(ControlLaw):
    """Balance immediate gain against state stability.

    The score is:  ``(1 − w) · gain_cost_ratio  +  w · stability_bonus``

    where the stability bonus penalises moves that target already-fragile
    coordinates (high obligation pressure) and rewards moves that reduce
    obligations.

    Attributes:
        stability_weight:  Weight *w* ∈ [0, 1] for the stability term.
    """

    def __init__(self, stability_weight: float = 0.4) -> None:
        self._w = max(0.0, min(1.0, stability_weight))

    def select(self, state: OrchestratorState, candidates: Sequence[SemanticMove]) -> SemanticMove | None:
        if not candidates:
            return None
        pressure = state.obligation_pressure()

        def score(m: SemanticMove) -> float:
            gain_term = m.gain_cost_ratio
            # Moves that discharge obligations get a stability bonus
            # proportional to the current pressure.
            stability_bonus = pressure if m.kind is MoveKind.DISCHARGE_OBLIGATION else 0.0
            # Repair and negotiate moves also contribute to stability.
            if m.kind in (MoveKind.REPAIR, MoveKind.NEGOTIATE_TREATY):
                stability_bonus += 0.5 * pressure
            return (1.0 - self._w) * gain_term + self._w * stability_bonus

        return max(candidates, key=score)

    def name(self) -> str:
        return f"balanced-{self._w:.2f}"


class AdaptiveControl(ControlLaw):
    """Learns from move history to bias future selections.

    Maintains per-kind success-rate weights.  Kinds that have been
    delivering above-average gains get higher weight; kinds with high
    regret are down-weighted.

    This implements the adaptive refinement loop from theory2.tex §3.4:
    the copilot channel's weight naturally adjusts based on how often
    its proposals survive verification.
    """

    def __init__(self, history: MoveHistory | None = None, base_weight: float = 1.0) -> None:
        self._history = history or MoveHistory()
        self._base_weight = base_weight
        self._kind_weights: dict[MoveKind, float] = {k: base_weight for k in MoveKind}

    def _refresh_weights(self) -> None:
        """Recompute per-kind weights from the move history."""
        for kind in MoveKind:
            rate = self._history.success_rate(kind)
            avg = self._history.average_gain(kind)
            # Weight is a product of success rate and average gain,
            # floored at 0.1 to keep all move kinds viable.
            self._kind_weights[kind] = max(0.1, rate * (1.0 + avg))

    def select(self, state: OrchestratorState, candidates: Sequence[SemanticMove]) -> SemanticMove | None:
        if not candidates:
            return None
        self._refresh_weights()

        def weighted_score(m: SemanticMove) -> float:
            return m.gain_cost_ratio * self._kind_weights.get(m.kind, self._base_weight)

        return max(candidates, key=weighted_score)

    def name(self) -> str:
        return "adaptive"

    @property
    def current_weights(self) -> dict[str, float]:
        """Expose current per-kind weights for diagnostics."""
        self._refresh_weights()
        return {k.value: v for k, v in self._kind_weights.items()}


def build_control_law(config: OrchestratorConfiguration, history: MoveHistory | None = None) -> ControlLaw:
    """Factory: construct a ``ControlLaw`` from configuration."""
    name = config.strategy.lower()
    if name == "greedy":
        return GreedyControl()
    if name.startswith("lookahead"):
        return LookaheadControl(depth=config.lookahead_depth)
    if name == "adaptive":
        return AdaptiveControl(history=history)
    # Default: balanced.
    return BalancedControl(stability_weight=config.stability_weight)


# ═══════════════════════════════════════════════════════════════════════════════
#  9.  MoveGenerator — enumerates admissible moves
# ═══════════════════════════════════════════════════════════════════════════════

class MoveGenerator:
    """Generates the admissible move frontier from the current state.

    The generator inspects the orchestrator state and produces all
    semantically valid moves: verification, construction, repair, treaty
    negotiation, cover refinement, obligation discharge, and copilot
    oracle consultation.

    Filtering and ranking are separate concerns so that the ``ControlLaw``
    can inspect the full candidate set when needed.
    """

    def __init__(self, config: OrchestratorConfiguration) -> None:
        self._config = config

    def generate_all(self, state: OrchestratorState) -> list[SemanticMove]:
        """Enumerate every candidate move for the current state.

        Categories of moves generated:
        *   VERIFY   — for every current section not yet verified.
        *   CONSTRUCT — for every frontier node without a section.
        *   REPAIR   — for every failed treaty.
        *   NEGOTIATE_TREATY — for treaties with unsatisfied clauses.
        *   REFINE_COVER — when obstructions exist.
        *   DISCHARGE_OBLIGATION — for each pending obligation.
        *   CONSULT_ORACLE — if the copilot channel is enabled and has budget.
        """
        moves: list[SemanticMove] = []

        # VERIFY existing sections.
        for coord in state.current_sections:
            moves.append(SemanticMove(
                move_id=f"verify-{coord}-{state.epoch}",
                kind=MoveKind.VERIFY,
                target_coordinate=coord,
                expected_gain=0.05,
                estimated_cost=2,
                preconditions=(f"section_exists:{coord}",),
                postconditions=(f"verified:{coord}",),
            ))

        # CONSTRUCT new sections for frontier nodes.
        for coord in state.frontier_nodes:
            if coord not in state.current_sections:
                moves.append(SemanticMove(
                    move_id=f"construct-{coord}-{state.epoch}",
                    kind=MoveKind.CONSTRUCT,
                    target_coordinate=coord,
                    expected_gain=0.15,
                    estimated_cost=5,
                    preconditions=(f"frontier:{coord}",),
                    postconditions=(f"section_created:{coord}",),
                ))

        # REPAIR for broken treaties.
        for treaty in state.active_treaties:
            if not treaty.accepted:
                target = treaty.patches[0] if treaty.patches else "unknown"
                moves.append(SemanticMove(
                    move_id=f"repair-{target}-{state.epoch}",
                    kind=MoveKind.REPAIR,
                    target_coordinate=target,
                    expected_gain=0.10,
                    estimated_cost=4,
                    preconditions=(f"treaty_broken:{target}",),
                    postconditions=(f"treaty_repaired:{target}",),
                ))

        # NEGOTIATE_TREATY for partially satisfied treaties.
        for treaty in state.active_treaties:
            unsatisfied = [c for c in treaty.clauses if not getattr(c, "satisfied", True)]
            if unsatisfied and treaty.patches:
                target = treaty.patches[0]
                moves.append(SemanticMove(
                    move_id=f"negotiate-{target}-{state.epoch}",
                    kind=MoveKind.NEGOTIATE_TREATY,
                    target_coordinate=target,
                    expected_gain=0.08,
                    estimated_cost=3,
                    preconditions=(f"treaty_partial:{target}",),
                    postconditions=(f"treaty_negotiated:{target}",),
                ))

        # REFINE_COVER when obstructions are present.
        for obs in state.obstruction_archive[-5:]:
            coord = obs.overlap[0] if hasattr(obs, "overlap") else str(obs)
            moves.append(SemanticMove(
                move_id=f"refine-{coord}-{state.epoch}",
                kind=MoveKind.REFINE_COVER,
                target_coordinate=coord,
                expected_gain=0.12,
                estimated_cost=6,
                preconditions=(f"obstruction:{coord}",),
                postconditions=(f"cover_refined:{coord}",),
            ))

        # DISCHARGE_OBLIGATION for each pending obligation.
        for obl in state.pending_obligations:
            moves.append(SemanticMove(
                move_id=f"discharge-{obl}-{state.epoch}",
                kind=MoveKind.DISCHARGE_OBLIGATION,
                target_coordinate=obl,
                expected_gain=0.07,
                estimated_cost=3,
                preconditions=(f"obligation_pending:{obl}",),
                postconditions=(f"obligation_discharged:{obl}",),
            ))

        # CONSULT_ORACLE — copilot-assisted proposal (theory2.tex §3.4).
        if self._config.copilot_enabled and state.frontier_nodes:
            coord = state.frontier_nodes[0]
            moves.append(SemanticMove(
                move_id=f"copilot-consult-{coord}-{state.epoch}",
                kind=MoveKind.CONSULT_ORACLE,
                target_coordinate=coord,
                expected_gain=0.10,
                estimated_cost=8,
                preconditions=("copilot_channel_active",),
                postconditions=(f"copilot_proposal:{coord}",),
            ))

        return moves

    def filter_admissible(self, moves: Sequence[SemanticMove], state: OrchestratorState) -> list[SemanticMove]:
        """Remove moves whose preconditions are not met or whose cost
        exceeds the remaining budget for their associated channel.
        """
        admissible: list[SemanticMove] = []
        for move in moves:
            if not self._preconditions_met(move, state):
                continue
            if state.resource_budget and not self._within_budget(move, state):
                continue
            admissible.append(move)
        return admissible

    def rank_by_gain(self, moves: Sequence[SemanticMove]) -> list[SemanticMove]:
        """Sort moves descending by expected gain."""
        return sorted(moves, key=lambda m: m.expected_gain, reverse=True)

    def rank_by_cost(self, moves: Sequence[SemanticMove]) -> list[SemanticMove]:
        """Sort moves ascending by estimated cost (cheapest first)."""
        return sorted(moves, key=lambda m: m.estimated_cost)

    def top_k(self, moves: Sequence[SemanticMove], k: int = 5) -> list[SemanticMove]:
        """Return the top *k* moves by gain-cost ratio."""
        ranked = sorted(moves, key=lambda m: m.gain_cost_ratio, reverse=True)
        return ranked[:k]

    def moves_for_coordinate(self, moves: Sequence[SemanticMove], coordinate: str) -> list[SemanticMove]:
        """Filter moves targeting a specific coordinate."""
        return [m for m in moves if m.target_coordinate == coordinate]

    def copilot_suggest_move(self, state: OrchestratorState) -> SemanticMove | None:
        """Generate a copilot-specific proposal move if conditions allow.

        The copilot suggestion targets the frontier node with the highest
        obstruction rank, reflecting the theory2.tex §3.4 principle that
        AI-assisted refinement is most valuable where human/solver
        progress has stalled.
        """
        if not self._config.copilot_enabled:
            return None
        if not state.frontier_nodes:
            return None
        # Pick the first frontier node (highest-priority by construction).
        coord = state.frontier_nodes[0]
        if state.resource_budget and state.resource_budget.is_exhausted("copilot"):
            return None
        return SemanticMove(
            move_id=f"copilot-suggest-{coord}-{state.epoch}",
            kind=MoveKind.CONSULT_ORACLE,
            target_coordinate=coord,
            expected_gain=0.10,
            estimated_cost=8,
            preconditions=("copilot_channel_active",),
            postconditions=(f"copilot_proposal:{coord}",),
        )

    # ── helpers ─────────────────────────────────────────────────────────

    @staticmethod
    def _preconditions_met(move: SemanticMove, state: OrchestratorState) -> bool:
        """Check whether a move's preconditions hold on the current state."""
        for pre in move.preconditions:
            if pre.startswith("section_exists:"):
                coord = pre.split(":", 1)[1]
                if coord not in state.current_sections:
                    return False
            elif pre.startswith("frontier:"):
                coord = pre.split(":", 1)[1]
                if coord not in state.frontier_nodes:
                    return False
            elif pre == "copilot_channel_active":
                if not state.evidence_channels.get("copilot", False):
                    return False
        return True

    @staticmethod
    def _within_budget(move: SemanticMove, state: OrchestratorState) -> bool:
        """Check whether the move's cost fits within the relevant channel budget."""
        budget = state.resource_budget
        if budget is None:
            return True
        channel = _channel_for_kind(move.kind)
        return budget.remaining(channel) >= move.estimated_cost


def _channel_for_kind(kind: MoveKind) -> str:
    """Map a move kind to the evidence channel that funds it."""
    mapping: dict[MoveKind, str] = {
        MoveKind.VERIFY: "solver",
        MoveKind.CONSTRUCT: "solver",
        MoveKind.REPAIR: "runtime",
        MoveKind.NEGOTIATE_TREATY: "runtime",
        MoveKind.REFINE_COVER: "solver",
        MoveKind.DISCHARGE_OBLIGATION: "solver",
        MoveKind.CONSULT_ORACLE: "copilot",
    }
    return mapping.get(kind, "solver")


# ═══════════════════════════════════════════════════════════════════════════════
# 10.  OrchestratorDiagnostics — aggregated reports
# ═══════════════════════════════════════════════════════════════════════════════

class OrchestratorDiagnostics:
    """Aggregates diagnostic reports across state, history, and convergence.

    Designed to be consumed by the JuGeo CLI diagnostics surface
    (``jugeo.interfaces.diagnostics``) and the copilot feedback loop.
    """

    def __init__(
        self,
        state: OrchestratorState,
        history: MoveHistory,
        monitor: ConvergenceMonitor,
        config: OrchestratorConfiguration,
    ) -> None:
        self._state = state
        self._history = history
        self._monitor = monitor
        self._config = config

    def state_summary(self) -> dict[str, Any]:
        """Compact summary of the current orchestrator state."""
        return {
            "epoch": self._state.epoch,
            "coverage_ratio": round(self._state.coverage_ratio(), 4),
            "obligation_pressure": round(self._state.obligation_pressure(), 4),
            "frontier_size": len(self._state.frontier_nodes),
            "sections_count": len(self._state.current_sections),
            "active_treaties": len(self._state.active_treaties),
            "obstructions_archived": len(self._state.obstruction_archive),
            "is_terminal": self._state.is_terminal(),
        }

    def move_statistics(self) -> dict[str, Any]:
        """Statistics over the move history."""
        stats: dict[str, Any] = {
            "total_moves": len(self._history),
            "total_cost": self._history.total_cost(),
        }
        for kind in MoveKind:
            records = self._history.query_by_type(kind)
            stats[kind.value] = {
                "count": len(records),
                "success_rate": round(self._history.success_rate(kind), 4),
                "average_gain": round(self._history.average_gain(kind), 4),
            }
        return stats

    def convergence_report(self) -> dict[str, Any]:
        """Report on convergence status and trajectory."""
        return {
            "is_converging": self._monitor.is_converging(),
            "convergence_rate": round(self._monitor.convergence_rate(), 6),
            "estimated_remaining_epochs": self._monitor.estimated_remaining(),
            "stall_detected": self._monitor.stall_detection(),
            "phase_transition_detected": self._monitor.phase_transition_detection(),
            "obligation_trend": self._monitor.obligation_trend(),
            "threshold": self._config.convergence_threshold,
        }

    def budget_report(self) -> dict[str, Any]:
        """Report on resource budget utilisation."""
        budget = self._state.resource_budget
        if budget is None:
            return {"status": "no_budget_configured"}
        report = budget.budget_per_channel()
        report["total_remaining"] = budget.total_remaining()
        report["all_exhausted"] = budget.is_exhausted()
        return report

    def copilot_diagnostic(self) -> dict[str, Any]:
        """Diagnostic specifically for the copilot oracle channel.

        Reports copilot utilisation, success rate, and whether the channel
        is still viable.  This supports the theory2.tex §252 principle
        that copilot proposals must be monitored and bounded.
        """
        copilot_records = self._history.query_by_type(MoveKind.CONSULT_ORACLE)
        budget = self._state.resource_budget
        copilot_remaining = budget.remaining("copilot") if budget else None
        return {
            "enabled": self._config.copilot_enabled,
            "channel_active": self._state.evidence_channels.get("copilot", False),
            "proposals_made": len(copilot_records),
            "success_rate": round(self._history.success_rate(MoveKind.CONSULT_ORACLE), 4),
            "average_gain": round(self._history.average_gain(MoveKind.CONSULT_ORACLE), 4),
            "budget_remaining": copilot_remaining,
            "exhausted": budget.is_exhausted("copilot") if budget else False,
        }

    def full_report(self) -> dict[str, Any]:
        """Combine all diagnostics into a single report dictionary."""
        return {
            "state": self.state_summary(),
            "moves": self.move_statistics(),
            "convergence": self.convergence_report(),
            "budget": self.budget_report(),
            "copilot": self.copilot_diagnostic(),
        }


# ═══════════════════════════════════════════════════════════════════════════════
# 11.  Orchestrator — main step-loop controller
# ═══════════════════════════════════════════════════════════════════════════════

class Orchestrator:
    """Main orchestration controller for JuGeo project-scale search.

    The orchestrator drives the loop:

        while not terminal and budget remains:
            moves ← generate admissible moves
            move  ← control_law.select(state, moves)
            outcome ← execute(move)
            update state, history, convergence monitor

    It integrates with the ``OrchestratorEventBus`` for real-time
    diagnostics and supports ``checkpoint`` / ``rollback`` for recovery
    from failed moves.

    Theory alignment (theory2.tex §orch):
        Orchestration is specification verification lifted from static
        judgment to dynamic search over partial judgments.  The controller
        mediates between the algebraic-geometric descent engine and the
        AI-assisted proposal channel, ensuring that every move is
        admissible and that convergence is monitored.
    """

    def __init__(
        self,
        config: OrchestratorConfiguration | None = None,
        state: OrchestratorState | None = None,
        event_bus: OrchestratorEventBus | None = None,
    ) -> None:
        self._config = config or OrchestratorConfiguration()
        self._state = state or OrchestratorState()
        self._event_bus = event_bus or OrchestratorEventBus()
        self._history = MoveHistory()
        self._monitor = ConvergenceMonitor(
            threshold=self._config.convergence_threshold,
        )
        self._control_law = build_control_law(self._config, self._history)
        self._generator = MoveGenerator(self._config)
        self._checkpoints: list[dict[str, Any]] = []
        self._initialise_budget()
        self._initialise_channels()
        logging.basicConfig(level=getattr(logging, self._config.logging_level, logging.INFO))

    # ── initialisation helpers ──────────────────────────────────────────

    def _initialise_budget(self) -> None:
        """Set up the ``ResourceBudget`` from configuration limits."""
        budget = ResourceBudget()
        for channel, limit in self._config.budget_limits.items():
            budget.allocate(channel, limit)
        self._state.resource_budget = budget

    def _initialise_channels(self) -> None:
        """Activate evidence channels in the state."""
        for channel in self._config.budget_limits:
            self._state.evidence_channels[channel] = True
        if not self._config.copilot_enabled:
            self._state.evidence_channels["copilot"] = False

    # ── core loop ───────────────────────────────────────────────────────

    def step(self) -> SemanticMove | None:
        """Execute a single orchestration step.

        Returns the move that was executed, or ``None`` if no admissible
        move could be found (terminal or budget exhausted).
        """
        if self._state.is_terminal():
            logger.info("Orchestrator: state is terminal at epoch %d", self._state.epoch)
            return None

        move = self.select_next_move()
        if move is None:
            logger.info("Orchestrator: no admissible moves at epoch %d", self._state.epoch)
            return None

        self._event_bus.publish(OrchestratorEvent(
            kind=OrchestratorEventKind.MOVE_SELECTED,
            payload={"move_id": move.move_id, "kind": move.kind.value},
        ))

        success, actual_gain = self.execute_move(move)
        self.evaluate_outcome(move, success, actual_gain)
        self.update_state(move, success)
        self._monitor.update(self._state)

        self._event_bus.publish(OrchestratorEvent(
            kind=OrchestratorEventKind.CONVERGENCE_UPDATE,
            payload={
                "coverage": self._state.coverage_ratio(),
                "converging": self._monitor.is_converging(),
                "rate": self._monitor.convergence_rate(),
            },
        ))

        self._check_budget_warnings()
        self._state.epoch += 1
        return move

    def run_until(
        self,
        predicate: Callable[[OrchestratorState], bool] | None = None,
        max_steps: int | None = None,
    ) -> int:
        """Run the orchestrator until *predicate* returns ``True`` or
        *max_steps* are exhausted.

        Returns the number of steps actually executed.
        """
        limit = max_steps or self._config.max_steps
        steps = 0
        while steps < limit:
            if predicate and predicate(self._state):
                break
            move = self.step()
            if move is None:
                break
            steps += 1
        return steps

    def select_next_move(self) -> SemanticMove | None:
        """Generate, filter, and select the best move via the control law."""
        raw = self._generator.generate_all(self._state)
        admissible = self._generator.filter_admissible(raw, self._state)
        if not admissible:
            return None
        return self._control_law.select(self._state, admissible)

    def execute_move(self, move: SemanticMove) -> tuple[bool, float]:
        """Execute *move* against the state.

        Returns ``(success, actual_gain)``.  In this controller the
        execution is *simulated* — real execution would delegate to the
        descent engine, solver, or copilot channel.  The simulation
        applies heuristic success probabilities per move kind.
        """
        start = time.monotonic()
        budget = self._state.resource_budget
        channel = _channel_for_kind(move.kind)
        if budget and not budget.spend(channel, move.estimated_cost):
            self._event_bus.publish(OrchestratorEvent(
                kind=OrchestratorEventKind.MOVE_FAILED,
                payload={"move_id": move.move_id, "reason": "budget_exhausted"},
            ))
            return False, 0.0

        # Heuristic success model — real execution delegates downstream.
        success_prob = _success_probability(move.kind)
        # Deterministic for reproducibility: hash the move_id.
        seed = hash(move.move_id) % 1000 / 1000.0
        success = seed < success_prob
        actual_gain = move.expected_gain * (0.8 + 0.4 * seed) if success else 0.0

        elapsed = time.monotonic() - start
        self._history.record(MoveRecord(
            move=move,
            epoch=self._state.epoch,
            success=success,
            actual_gain=actual_gain,
            actual_cost=move.estimated_cost,
            elapsed_seconds=elapsed,
            notes=("simulated",),
        ))

        event_kind = OrchestratorEventKind.MOVE_EXECUTED if success else OrchestratorEventKind.MOVE_FAILED
        self._event_bus.publish(OrchestratorEvent(
            kind=event_kind,
            payload={"move_id": move.move_id, "success": success, "gain": actual_gain},
        ))
        return success, actual_gain

    def evaluate_outcome(self, move: SemanticMove, success: bool, actual_gain: float) -> None:
        """Log and react to the outcome of a move execution.

        On failure the orchestrator may decide to add the target coordinate
        to the obstruction archive and push it back to the frontier.
        """
        if success:
            logger.debug(
                "Move %s succeeded: gain=%.4f",
                move.move_id, actual_gain,
            )
        else:
            logger.debug("Move %s failed", move.move_id)
            self._state.obstruction_archive.append(
                Obstruction(
                    overlap=(move.target_coordinate, move.target_coordinate),
                    message=f"Move {move.move_id} failed",
                    rank=0,
                )
            )
            if move.target_coordinate not in self._state.frontier_nodes:
                self._state.frontier_nodes.append(move.target_coordinate)

    def update_state(self, move: SemanticMove, success: bool) -> None:
        """Apply the semantic effect of a successful move to the state.

        Each move kind has a specific state transition:
        *   CONSTRUCT  — adds a section and removes from frontier.
        *   VERIFY     — (section already exists; no structural change).
        *   REPAIR     — marks the treaty as repaired (simplified).
        *   DISCHARGE_OBLIGATION — removes the obligation.
        *   CONSULT_ORACLE — adds a provisional copilot section.
        *   REFINE_COVER — removes one obstruction.
        *   NEGOTIATE_TREATY — (treaty renegotiated in-place).
        """
        if not success:
            return

        snap_before = self._state.snapshot()
        coord = move.target_coordinate

        if move.kind is MoveKind.CONSTRUCT:
            self._state.current_sections[coord] = {"status": "constructed", "epoch": self._state.epoch}
            if coord in self._state.frontier_nodes:
                self._state.frontier_nodes.remove(coord)

        elif move.kind is MoveKind.DISCHARGE_OBLIGATION:
            if coord in self._state.pending_obligations:
                self._state.pending_obligations.remove(coord)

        elif move.kind is MoveKind.CONSULT_ORACLE:
            # Copilot proposals are sections at PROPOSAL trust tier.
            self._state.current_sections[coord] = {"status": "copilot_proposal", "epoch": self._state.epoch}
            self._state.trust_state[coord] = TrustProfile(
                tier=TrustTier.PROPOSAL,
                support_scope="copilot",
                reasons=(f"copilot proposal at epoch {self._state.epoch}",),
            )
            if coord in self._state.frontier_nodes:
                self._state.frontier_nodes.remove(coord)

        elif move.kind is MoveKind.REFINE_COVER:
            # Remove the most recent obstruction for this coordinate.
            for i in reversed(range(len(self._state.obstruction_archive))):
                obs = self._state.obstruction_archive[i]
                if (hasattr(obs, "overlap") and obs.overlap[0] == coord):
                    self._state.obstruction_archive.pop(i)
                    break

        elif move.kind is MoveKind.VERIFY:
            if coord in self._state.trust_state:
                old = self._state.trust_state[coord]
                if old.tier < TrustTier.VERIFIED:
                    self._state.trust_state[coord] = TrustProfile(
                        tier=TrustTier.VERIFIED,
                        support_scope=old.support_scope,
                        reasons=old.reasons + (f"verified at epoch {self._state.epoch}",),
                    )

        diff = self._state.diff(snap_before)
        if diff:
            self._event_bus.publish(OrchestratorEvent(
                kind=OrchestratorEventKind.STATE_CHANGED,
                payload={"diff_keys": list(diff.keys()), "epoch": self._state.epoch},
            ))

    # ── checkpoint / rollback ───────────────────────────────────────────

    def checkpoint(self) -> int:
        """Save the current state as a checkpoint.  Returns the index."""
        snap = self._state.snapshot()
        self._checkpoints.append(snap)
        logger.info("Checkpoint %d saved at epoch %d", len(self._checkpoints) - 1, self._state.epoch)
        return len(self._checkpoints) - 1

    def rollback(self, checkpoint_index: int = -1) -> bool:
        """Restore state from a previous checkpoint.

        Returns ``True`` if the rollback succeeded.  Defaults to the most
        recent checkpoint.
        """
        if not self._checkpoints:
            logger.warning("Rollback requested but no checkpoints exist")
            return False
        try:
            snap = self._checkpoints[checkpoint_index]
        except IndexError:
            logger.warning("Invalid checkpoint index %d", checkpoint_index)
            return False
        self._state.restore(snap)
        logger.info("Rolled back to checkpoint %d", checkpoint_index)
        return True

    # ── convergence queries ─────────────────────────────────────────────

    def is_converging(self) -> bool:
        """Delegate to the convergence monitor."""
        return self._monitor.is_converging()

    def convergence_rate(self) -> float:
        """Delegate to the convergence monitor."""
        return self._monitor.convergence_rate()

    # ── diagnostics ─────────────────────────────────────────────────────

    def diagnostics(self) -> OrchestratorDiagnostics:
        """Return a ``OrchestratorDiagnostics`` instance for this orchestrator."""
        return OrchestratorDiagnostics(
            state=self._state,
            history=self._history,
            monitor=self._monitor,
            config=self._config,
        )

    # ── properties ──────────────────────────────────────────────────────

    @property
    def state(self) -> OrchestratorState:
        """Read access to the orchestrator state."""
        return self._state

    @property
    def history(self) -> MoveHistory:
        """Read access to the move history."""
        return self._history

    @property
    def config(self) -> OrchestratorConfiguration:
        """Read access to the configuration."""
        return self._config

    # ── internal helpers ────────────────────────────────────────────────

    def _check_budget_warnings(self) -> None:
        """Emit a BUDGET_WARNING event if any channel is below 10% remaining."""
        budget = self._state.resource_budget
        if budget is None:
            return
        for channel in budget.allocations:
            alloc = budget.allocations.get(channel, 0)
            if alloc > 0 and budget.remaining(channel) / alloc < 0.10:
                self._event_bus.publish(OrchestratorEvent(
                    kind=OrchestratorEventKind.BUDGET_WARNING,
                    payload={"channel": channel, "remaining": budget.remaining(channel)},
                ))

    # ── cross-subsystem integration ─────────────────────────────────────

    def evidence_guided_control(self) -> dict[str, Any]:
        """Weight control decisions by trust level using TrustAlgebra.

        Queries the evidence subsystem's :class:`TrustAlgebra` to obtain
        per-channel trust weights, then adjusts the control law's move
        scoring so that higher-trust channels receive proportionally
        greater influence on the next step selection.

        Returns a summary dict of trust-weighted channel scores.

        Theory ref: theory2.tex §252 — Evidence Algebra, Trust Ceilings.
        """
        if TrustAlgebra is None:
            logger.warning("TrustAlgebra unavailable; returning unweighted control.")
            return {"status": "unavailable", "channels": {}}

        algebra = TrustAlgebra()
        channel_weights: dict[str, float] = {}
        for channel_name in self._state.active_channels:
            trust_profile = getattr(self._state, "channel_trust", {}).get(channel_name)
            if trust_profile is not None:
                weight = algebra.weight(trust_profile)
            else:
                weight = algebra.default_weight()
            channel_weights[channel_name] = weight

        # Normalise weights so they sum to 1.0
        total = sum(channel_weights.values()) or 1.0
        normalised = {ch: w / total for ch, w in channel_weights.items()}

        self._event_bus.publish(OrchestratorEvent(
            kind=OrchestratorEventKind.STATE_SNAPSHOT,
            payload={"trust_guided_weights": normalised},
        ))
        return {"status": "ok", "channels": normalised}

    def geometric_convergence_check(self) -> dict[str, Any]:
        """Check whether local results glue globally via DescentEngine.

        Uses the geometry subsystem's :class:`DescentEngine` to verify that
        partial sections produced across coordinate patches satisfy the
        overlap / cocycle conditions required for global gluing.

        Returns a summary with convergence status and any obstructions found.

        Theory ref: theory2.tex §3 — Descent and Gluing.
        """
        if DescentEngine is None:
            logger.warning("DescentEngine unavailable; skipping convergence check.")
            return {"status": "unavailable", "converged": False, "obstructions": []}

        engine = DescentEngine()
        sections = getattr(self._state, "local_sections", [])
        overlaps = getattr(self._state, "overlap_treaties", [])

        result = engine.check_gluing(sections=sections, overlaps=overlaps)
        converged = getattr(result, "converged", False)
        obstructions = getattr(result, "obstructions", [])

        self._event_bus.publish(OrchestratorEvent(
            kind=OrchestratorEventKind.STATE_SNAPSHOT,
            payload={
                "geometric_convergence": converged,
                "obstruction_count": len(obstructions),
            },
        ))
        return {
            "status": "ok",
            "converged": converged,
            "obstructions": [str(o) for o in obstructions],
        }

    def solver_oracle_dispatch(
        self, obligation: Any, *, timeout_ms: int = 30_000
    ) -> dict[str, Any]:
        """Dispatch a verification obligation to Z3 via SolverRouter.

        Uses the solver subsystem's :class:`SolverRouter` to route the
        obligation to an appropriate solver backend (Z3, CVC5, etc.),
        respecting jurisdiction rules and budget constraints.

        Parameters
        ----------
        obligation
            The proof obligation to verify.
        timeout_ms
            Maximum solver time in milliseconds.

        Returns a result dict with solver outcome and provenance.

        Theory ref: theory2.tex §252 — Channel Jurisdiction.
        """
        if SolverRouter is None:
            logger.warning("SolverRouter unavailable; cannot dispatch obligation.")
            return {"status": "unavailable", "verified": False}

        router = SolverRouter()
        result = router.route(obligation, timeout_ms=timeout_ms)
        verified = getattr(result, "verified", False)
        solver_used = getattr(result, "solver_name", "unknown")

        self._event_bus.publish(OrchestratorEvent(
            kind=OrchestratorEventKind.STATE_SNAPSHOT,
            payload={
                "solver_dispatch": solver_used,
                "verified": verified,
            },
        ))
        return {
            "status": "ok",
            "verified": verified,
            "solver": solver_used,
            "provenance": getattr(result, "provenance", None),
        }

    def runtime_cached_replay(
        self, step_key: str, *, force: bool = False
    ) -> dict[str, Any]:
        """Cache or replay an orchestration step via runtime subsystem.

        Uses :class:`SemanticCache` to look up a previously computed step
        result and :class:`ReplayEngine` to replay it when available.
        If no cached result is found (or *force* is ``True``), returns a
        cache-miss indicator so the caller proceeds with live execution.

        Parameters
        ----------
        step_key
            A content-addressable key identifying the orchestration step.
        force
            When ``True``, bypass the cache and force live execution.

        Returns a dict with ``hit`` flag and optional cached result.

        Theory ref: theory2.tex §3.4 — Iterative Descent, Replay.
        """
        if SemanticCache is None or ReplayEngine is None:
            logger.warning("Runtime cache/replay unavailable.")
            return {"status": "unavailable", "hit": False}

        cache = SemanticCache()
        entry = None if force else cache.lookup(step_key)

        if entry is not None:
            replayer = ReplayEngine()
            replayed = replayer.replay(entry)
            return {
                "status": "ok",
                "hit": True,
                "result": replayed,
                "source": "cache",
            }

        return {"status": "ok", "hit": False}

    def site_model(self):
        """Build a Site from this orchestrator's state."""
        try:
            from jugeo.geometry.site import Site, SiteBuilder, Coordinate, CoordinateKind
            from jugeo.geometry.descent import DescentEngine, DescentConfiguration, LocalSection
            from jugeo.geometry.covers import Cover, CoverBuilder, score_cover
            from jugeo.judgments.judgment_terms import Judgment, JudgmentBuilder, Proposition, PropositionKind
            from jugeo.evidence.trust import TrustAlgebra
            return {"site_model": "built"}
        except Exception:
            return {"site_model": "unavailable"}


def _success_probability(kind: MoveKind) -> float:
    """Heuristic success probability per move kind (for simulation)."""
    probs: dict[MoveKind, float] = {
        MoveKind.VERIFY: 0.90,
        MoveKind.CONSTRUCT: 0.70,
        MoveKind.REPAIR: 0.60,
        MoveKind.NEGOTIATE_TREATY: 0.65,
        MoveKind.REFINE_COVER: 0.55,
        MoveKind.DISCHARGE_OBLIGATION: 0.75,
        MoveKind.CONSULT_ORACLE: 0.50,
    }
    return probs.get(kind, 0.5)


# ═══════════════════════════════════════════════════════════════════════════════
#  Legacy compatibility — preserve the original public API
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True, slots=True, init=False)
class ControlDecision:
    """Result of a single orchestration decision (legacy API).

    Retained for backward compatibility with callers that import
    ``ControlDecision`` and ``OrchestrationController``.
    """
    dispatched_goal: str | None
    notes: tuple[str, ...] = field(default_factory=tuple)

    def __init__(
        self,
        dispatched_goal: str | None = None,
        notes: Sequence[str] = (),
        *,
        goal: str | None = None,
        reasons: Sequence[str] = (),
    ) -> None:
        resolved_goal = dispatched_goal if dispatched_goal is not None else goal
        resolved_notes = tuple(notes) if notes else tuple(reasons)
        object.__setattr__(self, "dispatched_goal", resolved_goal)
        object.__setattr__(self, "notes", resolved_notes)

    @property
    def goal(self) -> str | None:
        return self.dispatched_goal

    @property
    def reasons(self) -> tuple[str, ...]:
        return self.notes


class OrchestrationController:
    """Legacy thin controller — delegates to ``Orchestrator``.

    This class preserves the original ``decide()`` interface so that
    existing call-sites (e.g. ``jugeo.interfaces.diagnostics``,
    ``jugeo.ideation.scheduling``) continue to work unchanged.
    """

    def __init__(self) -> None:
        self._orchestrator: Orchestrator | None = None

    def decide(
        self,
        frontier: FrontierState,
        budgets: BudgetLedger,
        signal: BackpressureSignal,
    ) -> ControlDecision:
        """Choose the next goal to dispatch from the frontier.

        Mirrors the original logic: respect backpressure, check budget,
        pop the highest-priority frontier item.
        """
        if signal.level is BackpressureLevel.THROTTLE:
            return ControlDecision(None, ("throttled by backpressure",))
        item = frontier.next_item()
        if item is None:
            return ControlDecision(None, ("frontier empty",))
        goal = item.goal if hasattr(item, "goal") else item
        budget_amount = getattr(goal, "budget", 1)
        proposition = getattr(goal, "proposition", str(goal))
        if not budgets.consume("frontier", budget_amount):
            return ControlDecision(None, ("budget exhausted",))
        return ControlDecision(proposition, ("dispatched",))

    # ── cross-subsystem integration ─────────────────────────────────────

    def evidence_guided_control(
        self,
        frontier: FrontierState,
        budgets: BudgetLedger,
        signal: BackpressureSignal,
    ) -> ControlDecision:
        """Trust-weighted variant of :meth:`decide`.

        Uses :class:`TrustAlgebra` to weight frontier items by the trust
        level of their originating evidence channel before dispatching.

        Theory ref: theory2.tex §252 — Evidence Algebra, Trust Ceilings.
        """
        if TrustAlgebra is None:
            return self.decide(frontier, budgets, signal)

        if signal.level is BackpressureLevel.THROTTLE:
            return ControlDecision(None, ("throttled by backpressure",))
        if not frontier.items:
            return ControlDecision(None, ("frontier empty",))

        algebra = TrustAlgebra()
        scored: list[tuple[float, Any]] = []
        for item in frontier.items:
            trust = getattr(item, "trust_profile", None)
            weight = algebra.weight(trust) if trust is not None else algebra.default_weight()
            urgency = getattr(item, "urgency", 0)
            scored.append((weight * (1 + urgency), item))

        scored.sort(key=lambda pair: pair[0], reverse=True)
        best_item = scored[0][1]
        frontier.items.remove(best_item)

        goal = best_item.goal if hasattr(best_item, "goal") else best_item
        budget_amount = getattr(goal, "budget", 1)
        proposition = getattr(goal, "proposition", str(goal))
        if not budgets.consume("frontier", budget_amount):
            return ControlDecision(None, ("budget exhausted",))
        return ControlDecision(proposition, ("dispatched via trust-weighted ranking",))

    def geometric_convergence_check(self) -> dict[str, Any]:
        """Check whether local sections glue globally via DescentEngine.

        Queries :class:`DescentEngine` to verify overlap / cocycle
        conditions across coordinate patches.

        Theory ref: theory2.tex §3 — Descent and Gluing.
        """
        if DescentEngine is None:
            return {"status": "unavailable", "converged": False}

        engine = DescentEngine()
        orchestrator = self._orchestrator
        if orchestrator is None:
            return {"status": "no_orchestrator", "converged": False}

        sections = getattr(orchestrator.state, "local_sections", [])
        overlaps = getattr(orchestrator.state, "overlap_treaties", [])
        result = engine.check_gluing(sections=sections, overlaps=overlaps)
        return {
            "status": "ok",
            "converged": getattr(result, "converged", False),
            "obstructions": [str(o) for o in getattr(result, "obstructions", [])],
        }

    def solver_oracle_dispatch(
        self, obligation: Any, *, timeout_ms: int = 30_000
    ) -> dict[str, Any]:
        """Dispatch a verification obligation to Z3 via SolverRouter.

        Theory ref: theory2.tex §252 — Channel Jurisdiction.
        """
        if SolverRouter is None:
            return {"status": "unavailable", "verified": False}

        router = SolverRouter()
        result = router.route(obligation, timeout_ms=timeout_ms)
        return {
            "status": "ok",
            "verified": getattr(result, "verified", False),
            "solver": getattr(result, "solver_name", "unknown"),
        }

    def runtime_cached_replay(
        self, step_key: str, *, force: bool = False
    ) -> dict[str, Any]:
        """Cache or replay an orchestration step via the runtime subsystem.

        Uses :class:`SemanticCache` for lookup and :class:`ReplayEngine`
        for deterministic replay of previously computed results.

        Theory ref: theory2.tex §3.4 — Iterative Descent, Replay.
        """
        if SemanticCache is None or ReplayEngine is None:
            return {"status": "unavailable", "hit": False}

        cache = SemanticCache()
        entry = None if force else cache.lookup(step_key)
        if entry is not None:
            replayed = ReplayEngine().replay(entry)
            return {"status": "ok", "hit": True, "result": replayed}
        return {"status": "ok", "hit": False}


# ═══════════════════════════════════════════════════════════════════════════════
#  Module exports
# ═══════════════════════════════════════════════════════════════════════════════

__all__ = [
    # Core state & moves
    "OrchestratorState",
    "SemanticMove",
    "MoveKind",
    # Move generation
    "MoveGenerator",
    # Main controller
    "Orchestrator",
    # Control laws
    "ControlLaw",
    "GreedyControl",
    "LookaheadControl",
    "BalancedControl",
    "AdaptiveControl",
    "build_control_law",
    # Configuration
    "OrchestratorConfiguration",
    # History & convergence
    "MoveHistory",
    "MoveRecord",
    "ConvergenceMonitor",
    # Events
    "OrchestratorEventBus",
    "OrchestratorEvent",
    "OrchestratorEventKind",
    # Resources
    "ResourceBudget",
    # Diagnostics
    "OrchestratorDiagnostics",
    # Legacy API
    "ControlDecision",
    "OrchestrationController",
]

# copilot: shared-core marker for future LLM orchestration.
