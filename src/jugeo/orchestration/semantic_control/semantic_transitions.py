"""Semantic transition machinery for JuGeo semantic control (theory2.tex Ch44).

This module implements the full transition subsystem of the JuGeo semantic-
control layer.  Within the control-theoretic framework of theory2.tex Ch44,
a *semantic transition* is a typed, guarded, atomic act of moving the proof-
search process from one ``SemanticControlState`` to another via an
``AdmissibleMove``.  This module realises §§44.5–44.7 of the theory.

Key responsibilities
────────────────────
*   **TransitionRecord** – immutable, serialisable log entry for a single
    completed (or failed) transition event; forms the primary audit artefact
    required by theory2.tex §44.6 (traceability invariant).
*   **TransitionTypeEnum** – typed vocabulary of transition categories used
    throughout the engine, matching the six transition classes introduced in
    theory2.tex §44.5.
*   **TransitionGuard** – a callable predicate wrapper that enforces an
    admissibility condition before a transition is executed; guards realise
    the *pre-condition logic* of theory2.tex §44.5.2.
*   **TransitionGuardRegistry** – manages a prioritised collection of guards
    and evaluates them in bulk, returning structured error messages when any
    guard rejects a proposed move.
*   **SemanticTransitionEngine** – the core execution unit; applies moves to
    states, captures timing and error information, and writes ``TransitionRecord``
    entries to its append-only log.  Implements the retry and rollback
    protocols of theory2.tex §44.5.4.
*   **TransitionAnalyzer** – post-hoc analysis of a sequence of
    ``TransitionRecord`` objects; detects cycles (theory2.tex §44.6.3),
    estimates success rates, and produces trend reports for the convergence
    monitor in s03.
*   **TransitionCoordinator** – top-level façade that orchestrates the
    engine and analyser, tracks *active* (in-flight) transitions, and
    provides lifecycle methods (begin / commit / abort) matching the
    two-phase protocol of theory2.tex §44.7.
*   **TransitionWitness** – an immutable-append audit log that records every
    begin / commit / abort event; the completeness invariant of §44.6.2
    requires that every *begin* eventually matches a *commit* or *abort*.

Design notes
────────────
*   Mutable service classes use ``@dataclass(slots=True)``; immutable value
    objects use ``@dataclass(frozen=True, slots=True)``.
*   All IDs are ``uuid.uuid4()``-based strings; all timestamps are
    ``time.time()`` floats (Unix epoch seconds).
*   Upstream imports are guarded with ``try/except`` so this module degrades
    gracefully when the rest of the JuGeo graph is not yet compiled.
*   Module-level constants are defined immediately after imports.
*   Logging is performed through the module ``log`` object; callers may
    configure verbosity via ``logging.getLogger('jugeo...')``.

References
──────────
*   theory2.tex Ch44  – Semantic Control
*   theory2.tex §44.5 – Semantic Transitions: Types, Guards, and Execution
*   theory2.tex §44.5.2 – Pre-condition Logic and Guard Composition
*   theory2.tex §44.5.4 – Retry and Rollback Protocols
*   theory2.tex §44.6 – Transition Logs and Traceability
*   theory2.tex §44.6.2 – Witness Completeness Invariant
*   theory2.tex §44.6.3 – Cycle Detection in Transition Sequences
*   theory2.tex §44.7 – Two-Phase Transition Coordination Protocol
"""

from __future__ import annotations

import enum
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

# ── Internal JuGeo imports (guarded) ────────────────────────────────────────

try:
    from jugeo.orchestration.semantic_control.models import (
        AdmissibleMove,
        SemanticControlState,
        StateDelta,
    )
except Exception:  # pragma: no cover

    @dataclass(slots=True)
    class SemanticControlState:  # type: ignore[no-redef]
        """Fallback stub for :class:`jugeo.orchestration.semantic_control.models.SemanticControlState`.

        Provides the minimal interface required by this module when the full
        JuGeo model graph is unavailable (e.g. during isolated unit testing or
        documentation builds).
        """

        state_id: str = field(default_factory=lambda: str(uuid.uuid4()))
        cover_ids: list[str] = field(default_factory=list)
        context_ids: list[str] = field(default_factory=list)
        section_ids: list[str] = field(default_factory=list)
        treaty_ids: list[str] = field(default_factory=list)
        obligation_ids: list[str] = field(default_factory=list)
        channel_ids: list[str] = field(default_factory=list)
        budget: dict[str, float] = field(default_factory=dict)
        timestamp: float = field(default_factory=time.time)
        metadata: dict[str, Any] = field(default_factory=dict)

        def is_admissible(self) -> bool:
            """Return ``True`` if all budget values are non-negative (§44.1)."""
            return all(v >= 0.0 for v in self.budget.values())

        def coverage_ratio(self) -> float:
            """Return the fraction of sections covered (§44.1)."""
            total = len(self.section_ids) or 1
            return len(self.cover_ids) / total

    @dataclass(slots=True)
    class AdmissibleMove:  # type: ignore[no-redef]
        """Fallback stub for :class:`jugeo.orchestration.semantic_control.models.AdmissibleMove`.

        An admissible move encodes a single proof-search action together with
        its preconditions, postconditions, cost, and trust level (§44.5).
        """

        move_id: str = field(default_factory=lambda: str(uuid.uuid4()))
        move_type: str = "FORWARD"
        preconditions: list[str] = field(default_factory=list)
        postconditions: list[str] = field(default_factory=list)
        cost: float = 1.0
        priority: int = 0
        trust_requirement: float = 0.5
        metadata: dict[str, Any] = field(default_factory=dict)

        def is_applicable(self, state: SemanticControlState) -> bool:  # type: ignore[override]
            """Return ``True`` if the move can be applied to *state*."""
            return state.is_admissible() and state.budget.get("tokens", 1.0) >= self.cost

    @dataclass(frozen=True, slots=True)
    class StateDelta:  # type: ignore[no-redef]
        """Fallback stub for :class:`jugeo.orchestration.semantic_control.models.StateDelta`.

        Represents the difference between two ``SemanticControlState`` objects,
        implementing the delta calculus of theory2.tex §44.2.
        """

        delta_id: str = field(default_factory=lambda: str(uuid.uuid4()))
        from_state_id: str = ""
        to_state_id: str = ""
        additions: tuple[str, ...] = ()
        removals: tuple[str, ...] = ()
        timestamp: float = field(default_factory=time.time)


# ── Module-level constants ────────────────────────────────────────────────────

VERSION: str = "0.1.0"
"""Module version, incremented on breaking schema changes."""

log: logging.Logger = logging.getLogger(__name__)
"""Module-level logger; configure via ``logging.getLogger('jugeo...')``."""

DEFAULT_MAX_TRANSITIONS: int = 10_000
"""Default cap on the number of ``TransitionRecord`` entries retained by
:class:`SemanticTransitionEngine` before the oldest entries are pruned."""

TRANSITION_TIMEOUT_SECONDS: float = 30.0
"""Default wall-clock timeout (seconds) allowed for a single transition
execution.  Exceeding this value causes the engine to emit a timeout error
rather than waiting indefinitely (§44.5.4)."""

MAX_RETRY_ATTEMPTS: int = 3
"""Default maximum number of retry attempts the engine will make for a
failing transition before propagating the error to the caller (§44.5.4)."""

DEFAULT_WINDOW_SIZE: int = 50
"""Default sliding-window size used by :class:`TransitionAnalyzer` when
computing rolling statistics such as success rate and average duration."""

MAX_ACTIVE_TRANSITIONS: int = 64
"""Default cap on simultaneously in-flight transitions tracked by
:class:`TransitionCoordinator`."""


# ── TransitionTypeEnum ────────────────────────────────────────────────────────


class TransitionTypeEnum(str, enum.Enum):
    """Typed vocabulary of semantic transition categories (theory2.tex §44.5).

    Each value names one of the six transition classes identified in the
    theory.  The string representation is used directly in log entries and
    exported JSON so that external consumers do not need to import this enum.

    Attributes
    ----------
    FORWARD:
        A normal proof-step that advances the state toward the goal.
        Corresponds to the *progressive move* of §44.5.1.
    ROLLBACK:
        Reverts the state to an earlier snapshot, undoing one or more
        forward transitions.  Used when a dead-end is detected (§44.5.4).
    BRANCH:
        Forks the current state into a speculative sub-trajectory without
        committing to the main path.  Supports parallel search (§44.5.3).
    MERGE:
        Collapses a branched sub-trajectory back onto the main path,
        combining coverage gains (§44.5.3).
    RESET:
        Hard-resets the state to the initial configuration.  Used as a
        last-resort recovery action when the retry budget is exhausted.
    CHECKPOINT:
        Records a named save-point without advancing the logical state;
        enables efficient rollback to labelled positions (§44.6.1).
    """

    FORWARD = "FORWARD"
    ROLLBACK = "ROLLBACK"
    BRANCH = "BRANCH"
    MERGE = "MERGE"
    RESET = "RESET"
    CHECKPOINT = "CHECKPOINT"


# ── TransitionRecord ──────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class TransitionRecord:
    """Immutable record of a single completed or failed transition event.

    ``TransitionRecord`` is the primary audit artefact of the transition
    subsystem.  Every call to :meth:`SemanticTransitionEngine.execute`
    produces exactly one record, regardless of success or failure.  The
    records are stored in the engine's append-only log and in the
    :class:`TransitionWitness` audit trail, satisfying the traceability
    invariant of theory2.tex §44.6.

    Fields
    ------
    record_id:
        Globally unique identifier for this record (``uuid4``).
    from_state_id:
        Identifier of the state *before* the transition.
    to_state_id:
        Identifier of the state *after* the transition, or the empty string
        if the transition failed (``success=False``).
    move_id:
        Identifier of the :class:`AdmissibleMove` that was applied.
    transition_type:
        String value of the :class:`TransitionTypeEnum` that describes the
        category of this transition.
    timestamp:
        Unix epoch time at which the transition was *initiated*.
    duration_seconds:
        Wall-clock duration of the transition execution, measured in seconds.
    success:
        ``True`` if the transition produced a valid next state; ``False`` if
        it was rejected by a guard, timed out, or raised an exception.
    error_message:
        Human-readable description of the failure reason; empty string on
        success.
    metadata:
        Arbitrary key-value annotations attached by the engine or caller.
    """

    record_id: str
    from_state_id: str
    to_state_id: str
    move_id: str
    transition_type: str
    timestamp: float
    duration_seconds: float
    success: bool
    error_message: str
    metadata: dict[str, Any]

    # ------------------------------------------------------------------
    # Temporal helpers
    # ------------------------------------------------------------------

    def age(self) -> float:
        """Return the elapsed wall-clock time since this record was created.

        Returns
        -------
        float
            Seconds since ``timestamp``.  Always non-negative.
        """
        return max(0.0, time.time() - self.timestamp)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise this record to a plain Python dictionary.

        The returned dictionary is JSON-serialisable (all values are built-in
        Python types) and can be round-tripped via :meth:`from_dict`.

        Returns
        -------
        dict[str, Any]
            Flat mapping with all field names as keys.
        """
        return {
            "record_id": self.record_id,
            "from_state_id": self.from_state_id,
            "to_state_id": self.to_state_id,
            "move_id": self.move_id,
            "transition_type": self.transition_type,
            "timestamp": self.timestamp,
            "duration_seconds": self.duration_seconds,
            "success": self.success,
            "error_message": self.error_message,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TransitionRecord:
        """Deserialise a ``TransitionRecord`` from a plain dictionary.

        Parameters
        ----------
        d:
            Dictionary previously produced by :meth:`to_dict`.

        Returns
        -------
        TransitionRecord
            Reconstructed record.  The ``metadata`` sub-dict is shallow-copied.

        Raises
        ------
        KeyError
            If any required field is absent from *d*.
        """
        return cls(
            record_id=d["record_id"],
            from_state_id=d["from_state_id"],
            to_state_id=d["to_state_id"],
            move_id=d["move_id"],
            transition_type=d["transition_type"],
            timestamp=float(d["timestamp"]),
            duration_seconds=float(d["duration_seconds"]),
            success=bool(d["success"]),
            error_message=d.get("error_message", ""),
            metadata=dict(d.get("metadata", {})),
        )


# ── TransitionGuard ───────────────────────────────────────────────────────────


@dataclass(slots=True)
class TransitionGuard:
    """Validates whether a proposed transition is admissible (theory2.tex §44.5.2).

    A ``TransitionGuard`` wraps an arbitrary boolean predicate that is
    evaluated against the current :class:`SemanticControlState` and the
    proposed :class:`AdmissibleMove`.  When the predicate returns ``False``,
    the guard produces a human-readable error message from its
    ``error_template`` by string-formatting the state and move identifiers
    into it.

    Guards are evaluated by :class:`TransitionGuardRegistry.check_all`
    before any transition is executed; a transition is rejected if *any*
    guard fails.  This realises the compositional pre-condition logic of
    theory2.tex §44.5.2.

    Fields
    ------
    guard_id:
        Unique identifier (``uuid4``).
    name:
        Short human-readable label, e.g. ``"budget_non_negative"``.
    description:
        Multi-sentence explanation of what invariant this guard protects.
    predicate:
        Callable of signature ``(state: SemanticControlState, move: AdmissibleMove) -> bool``.
    error_template:
        Python ``str.format`` template used to build the error message when
        the predicate returns ``False``.  May reference ``{state_id}`` and
        ``{move_id}``.
    """

    guard_id: str
    name: str
    description: str
    predicate: Callable[[SemanticControlState, AdmissibleMove], bool]
    error_template: str = "Guard '{name}' rejected move {move_id} from state {state_id}."

    # ------------------------------------------------------------------
    # Core API
    # ------------------------------------------------------------------

    def check(
        self,
        state: SemanticControlState,
        move: AdmissibleMove,
    ) -> tuple[bool, str]:
        """Evaluate this guard against *state* and *move*.

        Parameters
        ----------
        state:
            The current semantic control state.
        move:
            The proposed admissible move.

        Returns
        -------
        tuple[bool, str]
            ``(True, "")`` when the guard passes; ``(False, <error_message>)``
            when it rejects the transition.
        """
        try:
            ok = bool(self.predicate(state, move))
        except Exception as exc:  # defensive: guard predicate must not crash caller
            log.warning("TransitionGuard %r raised during check: %s", self.guard_id, exc)
            return False, f"Guard '{self.name}' raised exception: {exc}"
        if ok:
            return True, ""
        msg = self.error_template.format(
            name=self.name,
            state_id=state.state_id,
            move_id=move.move_id,
        )
        log.debug("TransitionGuard %r rejected: %s", self.guard_id, msg)
        return False, msg

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise this guard's metadata (excluding the predicate callable).

        Returns
        -------
        dict[str, Any]
            Dictionary with ``guard_id``, ``name``, ``description``,
            ``error_template``.  The ``predicate`` field is omitted because
            callables are not JSON-serialisable.
        """
        return {
            "guard_id": self.guard_id,
            "name": self.name,
            "description": self.description,
            "error_template": self.error_template,
        }


# ── TransitionGuardRegistry ───────────────────────────────────────────────────


@dataclass(slots=True)
class TransitionGuardRegistry:
    """Manages a collection of :class:`TransitionGuard` objects (theory2.tex §44.5.2).

    The registry stores guards in insertion order and evaluates them all
    when :meth:`check_all` is called.  Guards may be dynamically added or
    removed at runtime, allowing the set of admissibility conditions to be
    tuned without restarting the engine.

    Guards implement the *compositional guard* pattern of theory2.tex §44.5.2:
    a proposed transition is admissible only if every registered guard
    approves it.

    Fields
    ------
    guards:
        Ordered list of :class:`TransitionGuard` objects currently active.
    """

    guards: list[TransitionGuard] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def register(self, guard: TransitionGuard) -> None:
        """Append *guard* to the registry.

        If a guard with the same ``guard_id`` is already registered, the new
        guard replaces it in-place to prevent duplicates.

        Parameters
        ----------
        guard:
            The :class:`TransitionGuard` to add.
        """
        for i, existing in enumerate(self.guards):
            if existing.guard_id == guard.guard_id:
                self.guards[i] = guard
                log.debug("TransitionGuardRegistry: replaced guard %r", guard.guard_id)
                return
        self.guards.append(guard)
        log.debug("TransitionGuardRegistry: registered guard %r (%s)", guard.guard_id, guard.name)

    def unregister(self, guard_id: str) -> bool:
        """Remove the guard with *guard_id* from the registry.

        Parameters
        ----------
        guard_id:
            Identifier of the guard to remove.

        Returns
        -------
        bool
            ``True`` if the guard was found and removed; ``False`` if no guard
            with that ID was registered.
        """
        before = len(self.guards)
        self.guards = [g for g in self.guards if g.guard_id != guard_id]
        removed = len(self.guards) < before
        if removed:
            log.debug("TransitionGuardRegistry: unregistered guard %r", guard_id)
        return removed

    # ------------------------------------------------------------------
    # Evaluation
    # ------------------------------------------------------------------

    def check_all(
        self,
        state: SemanticControlState,
        move: AdmissibleMove,
    ) -> list[str]:
        """Evaluate every registered guard and collect rejection messages.

        Parameters
        ----------
        state:
            Current semantic control state.
        move:
            Proposed admissible move.

        Returns
        -------
        list[str]
            Empty list if all guards pass; otherwise a list of error messages
            from each guard that rejected the transition.  The list preserves
            guard registration order.
        """
        errors: list[str] = []
        for guard in self.guards:
            ok, msg = guard.check(state, move)
            if not ok:
                errors.append(msg)
        if errors:
            log.info(
                "TransitionGuardRegistry: %d guard(s) rejected move %r from state %r",
                len(errors),
                move.move_id,
                state.state_id,
            )
        return errors

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def get_by_id(self, guard_id: str) -> TransitionGuard | None:
        """Return the guard with *guard_id*, or ``None`` if not found.

        Parameters
        ----------
        guard_id:
            Identifier to look up.

        Returns
        -------
        TransitionGuard | None
        """
        for guard in self.guards:
            if guard.guard_id == guard_id:
                return guard
        return None

    # ------------------------------------------------------------------
    # Default guards factory
    # ------------------------------------------------------------------

    @classmethod
    def default_guards(cls) -> list[TransitionGuard]:
        """Return the standard set of guards mandated by theory2.tex §44.5.2.

        The returned list includes:

        1.  **budget_non_negative** – rejects moves that would consume more
            token budget than is currently available.
        2.  **state_admissible** – rejects any move applied to a state that
            already violates the admissibility invariant (§44.1).
        3.  **move_applicable** – checks the move's own
            :meth:`AdmissibleMove.is_applicable` method.
        4.  **trust_threshold** – rejects moves whose ``trust_requirement``
            exceeds the state's ``metadata["trust_level"]`` (if set).

        Returns
        -------
        list[TransitionGuard]
            Four :class:`TransitionGuard` instances ready for registration.
        """
        def _budget_ok(state: SemanticControlState, move: AdmissibleMove) -> bool:
            available = state.budget.get("tokens", float("inf"))
            return available >= getattr(move, "cost", 0.0)

        def _state_admissible(state: SemanticControlState, move: AdmissibleMove) -> bool:  # noqa: ARG001
            return state.is_admissible()

        def _move_applicable(state: SemanticControlState, move: AdmissibleMove) -> bool:
            return move.is_applicable(state)

        def _trust_ok(state: SemanticControlState, move: AdmissibleMove) -> bool:
            trust_level = state.metadata.get("trust_level", 1.0)
            return float(trust_level) >= getattr(move, "trust_requirement", 0.0)

        return [
            TransitionGuard(
                guard_id="budget_non_negative",
                name="budget_non_negative",
                description=(
                    "Ensures the move's token cost does not exceed the available budget "
                    "recorded in the state (theory2.tex §44.1 budget non-negativity)."
                ),
                predicate=_budget_ok,
                error_template=(
                    "Guard 'budget_non_negative' rejected move {move_id}: "
                    "insufficient token budget in state {state_id}."
                ),
            ),
            TransitionGuard(
                guard_id="state_admissible",
                name="state_admissible",
                description=(
                    "Rejects moves applied to a state that already violates the "
                    "admissibility invariant of theory2.tex §44.1."
                ),
                predicate=_state_admissible,
                error_template=(
                    "Guard 'state_admissible' rejected move {move_id}: "
                    "source state {state_id} is not admissible."
                ),
            ),
            TransitionGuard(
                guard_id="move_applicable",
                name="move_applicable",
                description=(
                    "Delegates applicability checking to AdmissibleMove.is_applicable "
                    "(theory2.tex §44.5.1 move pre-conditions)."
                ),
                predicate=_move_applicable,
                error_template=(
                    "Guard 'move_applicable' rejected move {move_id}: "
                    "not applicable to state {state_id}."
                ),
            ),
            TransitionGuard(
                guard_id="trust_threshold",
                name="trust_threshold",
                description=(
                    "Rejects moves whose trust_requirement exceeds the state's "
                    "current trust_level metadata value (theory2.tex §44.5.2)."
                ),
                predicate=_trust_ok,
                error_template=(
                    "Guard 'trust_threshold' rejected move {move_id}: "
                    "trust requirement not met for state {state_id}."
                ),
            ),
        ]


# ── SemanticTransitionEngine ──────────────────────────────────────────────────


@dataclass(slots=True)
class SemanticTransitionEngine:
    """Executes semantic transitions and maintains an append-only log.

    The engine is the primary execution unit of the transition subsystem.
    Its :meth:`execute` method applies a single :class:`AdmissibleMove` to a
    :class:`SemanticControlState`, consulting the :class:`TransitionGuardRegistry`
    first, then constructing the next state, timing the operation, and
    appending a :class:`TransitionRecord` to ``transition_log``.

    The retry and rollback protocols of theory2.tex §44.5.4 are implemented
    via :meth:`retry` and :meth:`rollback` respectively.  The ``retry_budget``
    dictionary tracks how many retries remain for each move identifier.

    Fields
    ------
    engine_id:
        Unique identifier for this engine instance.
    guard_registry:
        The :class:`TransitionGuardRegistry` used to validate transitions.
    transition_log:
        Append-only list of :class:`TransitionRecord` objects.
    retry_budget:
        Maps ``move_id`` → remaining retry attempts.  Initialised from
        :data:`MAX_RETRY_ATTEMPTS` on first encounter.
    timeout_seconds:
        Wall-clock deadline for a single :meth:`execute` call.
    """

    engine_id: str
    guard_registry: TransitionGuardRegistry
    transition_log: list[TransitionRecord] = field(default_factory=list)
    retry_budget: dict[str, int] = field(default_factory=dict)
    timeout_seconds: float = TRANSITION_TIMEOUT_SECONDS

    # ------------------------------------------------------------------
    # Core execution
    # ------------------------------------------------------------------

    def execute(
        self,
        state: SemanticControlState,
        move: AdmissibleMove,
        transition_type: TransitionTypeEnum = TransitionTypeEnum.FORWARD,
    ) -> tuple[SemanticControlState | None, TransitionRecord]:
        """Apply *move* to *state* and return the next state and its record.

        Execution follows the protocol of theory2.tex §44.5:

        1.  Evaluate all guards in ``guard_registry``.  If any guard rejects
            the move, emit a failure record and return ``(None, record)``.
        2.  Attempt to apply the move by constructing a new
            ``SemanticControlState`` that reflects the postconditions.
        3.  Record timing, write a :class:`TransitionRecord`, append it to
            ``transition_log``, and return.

        Parameters
        ----------
        state:
            The current semantic control state.
        move:
            The admissible move to apply.
        transition_type:
            Category of this transition (default ``FORWARD``).

        Returns
        -------
        tuple[SemanticControlState | None, TransitionRecord]
            ``(next_state, record)`` on success; ``(None, record)`` on failure.
        """
        start = time.time()
        record_id = str(uuid.uuid4())
        log.info(
            "SemanticTransitionEngine %r: executing move %r (%s) from state %r",
            self.engine_id, move.move_id, transition_type.value, state.state_id,
        )

        # ── Phase 1: Guard evaluation (theory2.tex §44.5.2) ──────────────
        errors = self.guard_registry.check_all(state, move)
        if errors:
            duration = time.time() - start
            error_msg = "; ".join(errors)
            record = TransitionRecord(
                record_id=record_id,
                from_state_id=state.state_id,
                to_state_id="",
                move_id=move.move_id,
                transition_type=transition_type.value,
                timestamp=start,
                duration_seconds=duration,
                success=False,
                error_message=error_msg,
                metadata={"engine_id": self.engine_id, "guard_errors": errors},
            )
            self._append(record)
            return None, record

        # ── Phase 2: Timeout check before applying move ───────────────────
        elapsed = time.time() - start
        if elapsed > self.timeout_seconds:
            duration = elapsed
            record = self._failure_record(
                record_id, state, move, transition_type, start, duration,
                f"Timeout: pre-execution overhead {elapsed:.3f}s exceeded {self.timeout_seconds}s.",
            )
            self._append(record)
            return None, record

        # ── Phase 3: Apply move to produce next state ─────────────────────
        try:
            next_state = self._apply_move(state, move, transition_type)
        except Exception as exc:
            duration = time.time() - start
            record = self._failure_record(
                record_id, state, move, transition_type, start, duration,
                f"Exception during move application: {exc}",
            )
            self._append(record)
            log.exception("SemanticTransitionEngine %r: move %r raised", self.engine_id, move.move_id)
            return None, record

        duration = time.time() - start
        record = TransitionRecord(
            record_id=record_id,
            from_state_id=state.state_id,
            to_state_id=next_state.state_id,
            move_id=move.move_id,
            transition_type=transition_type.value,
            timestamp=start,
            duration_seconds=duration,
            success=True,
            error_message="",
            metadata={
                "engine_id": self.engine_id,
                "transition_type": transition_type.value,
                "coverage_ratio": next_state.coverage_ratio(),
            },
        )
        self._append(record)
        log.info(
            "SemanticTransitionEngine %r: transition %r succeeded in %.4fs → state %r",
            self.engine_id, record_id, duration, next_state.state_id,
        )
        return next_state, record

    # ------------------------------------------------------------------
    # Rollback
    # ------------------------------------------------------------------

    def rollback(
        self,
        state: SemanticControlState,
        record: TransitionRecord,
    ) -> SemanticControlState | None:
        """Revert the effect of the transition captured in *record*.

        Implements the rollback protocol of theory2.tex §44.5.4.  Because
        ``SemanticControlState`` is mutable, rollback is performed by
        reconstructing a new state whose ``state_id`` is set to
        ``record.from_state_id`` and whose structural fields are copied from
        *state* with the move's postconditions reversed.

        In this stub implementation, the rollback simply resets the state_id
        to the pre-transition value and logs the event.  A full implementation
        would consult a snapshot store.

        Parameters
        ----------
        state:
            The state to roll back (post-transition).
        record:
            The :class:`TransitionRecord` describing the transition to undo.

        Returns
        -------
        SemanticControlState | None
            The rolled-back state, or ``None`` if rollback is impossible.
        """
        if not record.success:
            log.warning(
                "SemanticTransitionEngine.rollback: record %r was already failed; nothing to undo.",
                record.record_id,
            )
            return None
        if state.state_id != record.to_state_id:
            log.warning(
                "SemanticTransitionEngine.rollback: state_id mismatch "
                "(state=%r, record.to_state_id=%r); proceeding with caution.",
                state.state_id, record.to_state_id,
            )
        # Produce a rolled-back state by restoring the from_state_id.
        # A full implementation would restore snapshot fields from a store.
        rolled_back = SemanticControlState(
            state_id=record.from_state_id,
            cover_ids=list(state.cover_ids),
            context_ids=list(state.context_ids),
            section_ids=list(state.section_ids),
            treaty_ids=list(state.treaty_ids),
            obligation_ids=list(state.obligation_ids),
            channel_ids=list(state.channel_ids),
            budget=dict(state.budget),
            timestamp=time.time(),
            metadata={**state.metadata, "rolled_back_from": state.state_id},
        )
        log.info(
            "SemanticTransitionEngine %r: rolled back to state %r via record %r",
            self.engine_id, rolled_back.state_id, record.record_id,
        )
        return rolled_back

    # ------------------------------------------------------------------
    # Retry
    # ------------------------------------------------------------------

    def retry(self, state: SemanticControlState, move: AdmissibleMove) -> bool:
        """Decrement the retry budget for *move* and indicate whether a retry is allowed.

        The retry protocol of theory2.tex §44.5.4 limits the number of
        re-attempts for each failing move to :data:`MAX_RETRY_ATTEMPTS`.
        This method is idempotent in the sense that it only decrements on
        explicit calls.

        Parameters
        ----------
        state:
            The state at which the retry would be attempted (used for logging).
        move:
            The move that previously failed.

        Returns
        -------
        bool
            ``True`` if a retry attempt is permitted; ``False`` if the budget
            is exhausted.
        """
        if move.move_id not in self.retry_budget:
            self.retry_budget[move.move_id] = MAX_RETRY_ATTEMPTS
        remaining = self.retry_budget[move.move_id]
        if remaining <= 0:
            log.warning(
                "SemanticTransitionEngine %r: retry budget exhausted for move %r at state %r",
                self.engine_id, move.move_id, state.state_id,
            )
            return False
        self.retry_budget[move.move_id] -= 1
        log.debug(
            "SemanticTransitionEngine %r: retry permitted for move %r (%d remaining)",
            self.engine_id, move.move_id, self.retry_budget[move.move_id],
        )
        return True

    # ------------------------------------------------------------------
    # Log management
    # ------------------------------------------------------------------

    def clear_log(self) -> None:
        """Remove all entries from the transition log.

        This is a destructive operation; callers should call
        :meth:`export_log` first if the records need to be preserved.
        """
        cleared = len(self.transition_log)
        self.transition_log.clear()
        self.retry_budget.clear()
        log.info("SemanticTransitionEngine %r: cleared %d log entries", self.engine_id, cleared)

    def export_log(self) -> list[dict[str, Any]]:
        """Serialise the entire transition log to a list of dictionaries.

        Returns
        -------
        list[dict[str, Any]]
            One dictionary per :class:`TransitionRecord`, in chronological
            order.  Each dictionary is the result of
            :meth:`TransitionRecord.to_dict`.
        """
        return [r.to_dict() for r in self.transition_log]

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    def stats(self) -> dict[str, Any]:
        """Return a summary statistics dictionary for the engine.

        Returns
        -------
        dict[str, Any]
            Dictionary with keys:
            ``engine_id``, ``total_transitions``, ``successful``,
            ``failed``, ``success_rate``, ``average_duration_seconds``,
            ``retry_budget_snapshot``.
        """
        total = len(self.transition_log)
        successful = sum(1 for r in self.transition_log if r.success)
        failed = total - successful
        success_rate = successful / total if total > 0 else 0.0
        avg_duration = (
            sum(r.duration_seconds for r in self.transition_log) / total
            if total > 0 else 0.0
        )
        return {
            "engine_id": self.engine_id,
            "total_transitions": total,
            "successful": successful,
            "failed": failed,
            "success_rate": round(success_rate, 4),
            "average_duration_seconds": round(avg_duration, 6),
            "retry_budget_snapshot": dict(self.retry_budget),
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _apply_move(
        self,
        state: SemanticControlState,
        move: AdmissibleMove,
        transition_type: TransitionTypeEnum,
    ) -> SemanticControlState:
        """Produce the next state by applying *move* to *state*.

        This internal method performs the structural mutation mandated by the
        move's postconditions.  For the stub implementation, it constructs a
        new ``SemanticControlState`` with:

        *   A fresh ``state_id``.
        *   Updated ``budget`` with ``tokens`` decremented by ``move.cost``.
        *   A ``metadata`` entry recording the transition type and move ID.

        A full implementation would evaluate ``move.postconditions`` against
        a domain-specific postcondition DSL (theory2.tex §44.5.1).
        """
        new_budget = dict(state.budget)
        cost = getattr(move, "cost", 0.0)
        if "tokens" in new_budget:
            new_budget["tokens"] = max(0.0, new_budget["tokens"] - cost)

        new_meta = dict(state.metadata)
        new_meta["last_move_id"] = move.move_id
        new_meta["last_transition_type"] = transition_type.value

        return SemanticControlState(
            state_id=str(uuid.uuid4()),
            cover_ids=list(state.cover_ids),
            context_ids=list(state.context_ids),
            section_ids=list(state.section_ids),
            treaty_ids=list(state.treaty_ids),
            obligation_ids=list(state.obligation_ids),
            channel_ids=list(state.channel_ids),
            budget=new_budget,
            timestamp=time.time(),
            metadata=new_meta,
        )

    def _failure_record(
        self,
        record_id: str,
        state: SemanticControlState,
        move: AdmissibleMove,
        transition_type: TransitionTypeEnum,
        start: float,
        duration: float,
        error_message: str,
    ) -> TransitionRecord:
        """Build a failure :class:`TransitionRecord` with the given error message."""
        return TransitionRecord(
            record_id=record_id,
            from_state_id=state.state_id,
            to_state_id="",
            move_id=move.move_id,
            transition_type=transition_type.value,
            timestamp=start,
            duration_seconds=duration,
            success=False,
            error_message=error_message,
            metadata={"engine_id": self.engine_id},
        )

    def _append(self, record: TransitionRecord) -> None:
        """Append *record* to the log, pruning if the log exceeds capacity."""
        self.transition_log.append(record)
        if len(self.transition_log) > DEFAULT_MAX_TRANSITIONS:
            pruned = self.transition_log.pop(0)
            log.debug(
                "SemanticTransitionEngine %r: pruned oldest record %r",
                self.engine_id, pruned.record_id,
            )


# ── TransitionAnalyzer ────────────────────────────────────────────────────────


@dataclass(slots=True)
class TransitionAnalyzer:
    """Analyses sequences of :class:`TransitionRecord` objects (theory2.tex §44.6).

    The analyser detects statistical and structural patterns in a sequence of
    :class:`TransitionRecord` objects.  It operates in *windowed* mode: when a
    ``window_size`` is set, computations are applied to the most recent
    ``window_size`` records.  This matches the sliding-window analysis model
    of theory2.tex §44.6.3.

    Fields
    ------
    analyzer_id:
        Unique identifier for this analyser instance.
    window_size:
        Number of most-recent records to include in computations.  Set to
        ``0`` to use all records.
    """

    analyzer_id: str
    window_size: int = DEFAULT_WINDOW_SIZE

    # ------------------------------------------------------------------
    # Statistical metrics
    # ------------------------------------------------------------------

    def success_rate(self, records: list[TransitionRecord]) -> float:
        """Return the fraction of successful transitions in *records*.

        Parameters
        ----------
        records:
            List of :class:`TransitionRecord` objects to analyse.

        Returns
        -------
        float
            Value in ``[0.0, 1.0]``.  Returns ``0.0`` if *records* is empty.
        """
        windowed = self._window(records)
        if not windowed:
            return 0.0
        return sum(1 for r in windowed if r.success) / len(windowed)

    def average_duration(self, records: list[TransitionRecord]) -> float:
        """Return the mean transition duration in seconds.

        Parameters
        ----------
        records:
            List of :class:`TransitionRecord` objects to analyse.

        Returns
        -------
        float
            Mean ``duration_seconds`` across the windowed records.
            Returns ``0.0`` if *records* is empty.
        """
        windowed = self._window(records)
        if not windowed:
            return 0.0
        return sum(r.duration_seconds for r in windowed) / len(windowed)

    # ------------------------------------------------------------------
    # Structural analysis
    # ------------------------------------------------------------------

    def detect_cycles(self, records: list[TransitionRecord]) -> list[list[str]]:
        """Detect repeated state-ID subsequences in the transition sequence.

        Implements the cycle-detection heuristic of theory2.tex §44.6.3.
        A cycle is reported when the same ``to_state_id`` appears more than
        once in the sequence of successful transitions, indicating that the
        proof-search process has re-visited a state.

        Parameters
        ----------
        records:
            List of :class:`TransitionRecord` objects in chronological order.

        Returns
        -------
        list[list[str]]
            Each inner list is a sequence of state IDs constituting one
            detected cycle.  Returns an empty list if no cycles are found.
        """
        windowed = self._window(records)
        seen: dict[str, int] = {}  # state_id → first occurrence index
        cycles: list[list[str]] = []
        state_sequence = [r.to_state_id for r in windowed if r.success and r.to_state_id]
        for idx, sid in enumerate(state_sequence):
            if sid in seen:
                # Extract the sub-sequence forming the cycle.
                cycle = state_sequence[seen[sid]: idx + 1]
                cycles.append(cycle)
                log.warning(
                    "TransitionAnalyzer %r: cycle detected at index %d, state %r",
                    self.analyzer_id, idx, sid,
                )
            else:
                seen[sid] = idx
        return cycles

    def most_frequent_type(self, records: list[TransitionRecord]) -> str:
        """Return the :class:`TransitionTypeEnum` value that appears most often.

        Parameters
        ----------
        records:
            List of :class:`TransitionRecord` objects to analyse.

        Returns
        -------
        str
            The ``transition_type`` string that appears most frequently.
            Returns the empty string if *records* is empty.
        """
        windowed = self._window(records)
        if not windowed:
            return ""
        counts: dict[str, int] = {}
        for r in windowed:
            counts[r.transition_type] = counts.get(r.transition_type, 0) + 1
        return max(counts, key=lambda k: counts[k])

    def failure_analysis(self, records: list[TransitionRecord]) -> dict[str, int]:
        """Count occurrences of each distinct error message in failed records.

        Parameters
        ----------
        records:
            List of :class:`TransitionRecord` objects to analyse.

        Returns
        -------
        dict[str, int]
            Mapping from ``error_message`` → count.  Only failed records are
            included.  Sorted by descending count.
        """
        windowed = self._window(records)
        counts: dict[str, int] = {}
        for r in windowed:
            if not r.success and r.error_message:
                counts[r.error_message] = counts.get(r.error_message, 0) + 1
        # Sort by frequency descending for easy consumption.
        return dict(sorted(counts.items(), key=lambda kv: kv[1], reverse=True))

    def trend_report(self, records: list[TransitionRecord]) -> dict[str, Any]:
        """Produce a comprehensive trend analysis dictionary.

        Combines all individual metrics into a single report suitable for
        display or logging.  Intended as the primary diagnostic output of
        the analyser.

        Parameters
        ----------
        records:
            List of :class:`TransitionRecord` objects in chronological order.

        Returns
        -------
        dict[str, Any]
            Dictionary with keys:
            ``analyzer_id``, ``window_size``, ``total_records``,
            ``success_rate``, ``average_duration_seconds``,
            ``most_frequent_type``, ``cycle_count``, ``cycles``,
            ``failure_analysis``, ``timestamp``.
        """
        cycles = self.detect_cycles(records)
        return {
            "analyzer_id": self.analyzer_id,
            "window_size": self.window_size,
            "total_records": len(records),
            "success_rate": round(self.success_rate(records), 4),
            "average_duration_seconds": round(self.average_duration(records), 6),
            "most_frequent_type": self.most_frequent_type(records),
            "cycle_count": len(cycles),
            "cycles": cycles,
            "failure_analysis": self.failure_analysis(records),
            "timestamp": time.time(),
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _window(self, records: list[TransitionRecord]) -> list[TransitionRecord]:
        """Return the most recent ``window_size`` records, or all if 0."""
        if self.window_size <= 0 or len(records) <= self.window_size:
            return records
        return records[-self.window_size:]


# ── TransitionCoordinator ─────────────────────────────────────────────────────


@dataclass(slots=True)
class TransitionCoordinator:
    """Top-level coordinator for two-phase transition management (theory2.tex §44.7).

    The coordinator implements the *two-phase transition protocol* of
    theory2.tex §44.7, which separates the *begin* phase (guard evaluation and
    tentative booking) from the *commit* phase (final execution and logging).
    Transitions that are not committed within the timeout window can be
    *aborted* without side effects.

    Fields
    ------
    coordinator_id:
        Unique identifier for this coordinator.
    engine:
        The :class:`SemanticTransitionEngine` used to execute committed
        transitions.
    analyzer:
        The :class:`TransitionAnalyzer` used to report on completed
        transitions.
    active_transitions:
        Dictionary of in-flight transition records, keyed by a temporary
        ``transition_id`` returned to the caller by :meth:`begin_transition`.
    completed_transitions:
        Append-only list of committed or aborted :class:`TransitionRecord`
        objects.
    max_active:
        Maximum number of simultaneously in-flight transitions.
    """

    coordinator_id: str
    engine: SemanticTransitionEngine
    analyzer: TransitionAnalyzer
    active_transitions: dict[str, dict[str, Any]] = field(default_factory=dict)
    completed_transitions: list[TransitionRecord] = field(default_factory=list)
    max_active: int = MAX_ACTIVE_TRANSITIONS

    # ------------------------------------------------------------------
    # Two-phase protocol
    # ------------------------------------------------------------------

    def begin_transition(
        self,
        state: SemanticControlState,
        move: AdmissibleMove,
        ttype: TransitionTypeEnum = TransitionTypeEnum.FORWARD,
    ) -> str:
        """Begin a transition: validate guards and book a transition slot.

        This is Phase 1 of the two-phase protocol (theory2.tex §44.7.1).
        The method evaluates all guards in the engine's registry, allocates a
        ``transition_id``, and stores the pending transition context.  The
        transition is not actually executed until :meth:`commit_transition` is
        called with the returned ID.

        Parameters
        ----------
        state:
            The source semantic control state.
        move:
            The proposed admissible move.
        ttype:
            Transition type category.

        Returns
        -------
        str
            A ``transition_id`` to pass to :meth:`commit_transition` or
            :meth:`abort_transition`.

        Raises
        ------
        RuntimeError
            If the number of active transitions has reached ``max_active``.
        ValueError
            If any guard rejects the proposed transition.
        """
        if len(self.active_transitions) >= self.max_active:
            raise RuntimeError(
                f"TransitionCoordinator {self.coordinator_id!r}: "
                f"max_active limit ({self.max_active}) reached; cannot begin new transition."
            )
        errors = self.engine.guard_registry.check_all(state, move)
        if errors:
            raise ValueError(
                f"TransitionCoordinator {self.coordinator_id!r}: "
                f"guard rejection for move {move.move_id!r}: {'; '.join(errors)}"
            )
        tid = str(uuid.uuid4())
        self.active_transitions[tid] = {
            "transition_id": tid,
            "state": state,
            "move": move,
            "ttype": ttype,
            "started_at": time.time(),
        }
        log.info(
            "TransitionCoordinator %r: began transition %r (move %r, type %s)",
            self.coordinator_id, tid, move.move_id, ttype.value,
        )
        return tid

    def commit_transition(self, transition_id: str) -> TransitionRecord:
        """Execute and finalise the pending transition identified by *transition_id*.

        This is Phase 2 of the two-phase protocol (theory2.tex §44.7.2).
        The method retrieves the pending context, delegates execution to the
        engine, moves the record to ``completed_transitions``, and returns the
        resulting :class:`TransitionRecord`.

        Parameters
        ----------
        transition_id:
            Identifier returned by :meth:`begin_transition`.

        Returns
        -------
        TransitionRecord
            The completed (success or failure) transition record.

        Raises
        ------
        KeyError
            If *transition_id* is not found in ``active_transitions``.
        """
        if transition_id not in self.active_transitions:
            raise KeyError(
                f"TransitionCoordinator {self.coordinator_id!r}: "
                f"unknown transition_id {transition_id!r}."
            )
        ctx = self.active_transitions.pop(transition_id)
        _next_state, record = self.engine.execute(ctx["state"], ctx["move"], ctx["ttype"])
        self.completed_transitions.append(record)
        log.info(
            "TransitionCoordinator %r: committed transition %r → success=%s",
            self.coordinator_id, transition_id, record.success,
        )
        return record

    def abort_transition(self, transition_id: str) -> bool:
        """Cancel a pending transition without executing it.

        Implements the abort branch of theory2.tex §44.7.3.  A synthetic
        failure record is written to ``completed_transitions`` to preserve
        the audit trail.

        Parameters
        ----------
        transition_id:
            Identifier returned by :meth:`begin_transition`.

        Returns
        -------
        bool
            ``True`` if the transition was found and aborted; ``False`` if the
            identifier was not in ``active_transitions``.
        """
        ctx = self.active_transitions.pop(transition_id, None)
        if ctx is None:
            log.warning(
                "TransitionCoordinator %r: abort called for unknown id %r",
                self.coordinator_id, transition_id,
            )
            return False
        state: SemanticControlState = ctx["state"]
        move: AdmissibleMove = ctx["move"]
        ttype: TransitionTypeEnum = ctx["ttype"]
        abort_record = TransitionRecord(
            record_id=str(uuid.uuid4()),
            from_state_id=state.state_id,
            to_state_id="",
            move_id=move.move_id,
            transition_type=ttype.value,
            timestamp=ctx["started_at"],
            duration_seconds=time.time() - ctx["started_at"],
            success=False,
            error_message=f"Aborted transition {transition_id}.",
            metadata={
                "coordinator_id": self.coordinator_id,
                "aborted": True,
                "original_transition_id": transition_id,
            },
        )
        self.completed_transitions.append(abort_record)
        log.info(
            "TransitionCoordinator %r: aborted transition %r",
            self.coordinator_id, transition_id,
        )
        return True

    # ------------------------------------------------------------------
    # Reporting / diagnostics
    # ------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        """Return a point-in-time status snapshot of the coordinator.

        Returns
        -------
        dict[str, Any]
            Dictionary with keys:
            ``coordinator_id``, ``active_count``, ``completed_count``,
            ``max_active``, ``engine_stats``, ``timestamp``.
        """
        return {
            "coordinator_id": self.coordinator_id,
            "active_count": len(self.active_transitions),
            "completed_count": len(self.completed_transitions),
            "max_active": self.max_active,
            "engine_stats": self.engine.stats(),
            "timestamp": time.time(),
        }

    def health_check(self) -> dict[str, Any]:
        """Perform a lightweight health check and return a verdict dictionary.

        A coordinator is considered *healthy* if:

        *   No active transitions have been pending for more than
            ``engine.timeout_seconds``.
        *   The success rate of completed transitions is at or above 0.5.

        Returns
        -------
        dict[str, Any]
            Dictionary with keys:
            ``healthy``, ``stale_active_count``, ``success_rate``,
            ``issues``.
        """
        now = time.time()
        issues: list[str] = []

        # Check for stale active transitions.
        stale = [
            tid for tid, ctx in self.active_transitions.items()
            if now - ctx["started_at"] > self.engine.timeout_seconds
        ]
        if stale:
            issues.append(f"{len(stale)} stale active transition(s): {stale[:5]}")

        # Check overall success rate.
        sr = self.analyzer.success_rate(self.completed_transitions)
        if self.completed_transitions and sr < 0.5:
            issues.append(f"Low success rate: {sr:.2%}")

        return {
            "healthy": len(issues) == 0,
            "stale_active_count": len(stale),
            "success_rate": round(sr, 4),
            "issues": issues,
            "coordinator_id": self.coordinator_id,
            "timestamp": now,
        }

    def export(self) -> dict[str, Any]:
        """Export the full coordinator state as a serialisable dictionary.

        Returns
        -------
        dict[str, Any]
            Dictionary with keys:
            ``coordinator_id``, ``status``, ``completed_transitions``,
            ``engine_log``, ``trend_report``.
        """
        return {
            "coordinator_id": self.coordinator_id,
            "status": self.status(),
            "completed_transitions": [r.to_dict() for r in self.completed_transitions],
            "engine_log": self.engine.export_log(),
            "trend_report": self.analyzer.trend_report(self.completed_transitions),
        }


# ── TransitionWitness ─────────────────────────────────────────────────────────


@dataclass(slots=True)
class TransitionWitness:
    """Immutable-append audit log for transition lifecycle events (theory2.tex §44.6.2).

    The witness records every *begin*, *commit*, and *abort* event issued by
    a :class:`TransitionCoordinator`.  Its primary purpose is to satisfy the
    *completeness invariant* of theory2.tex §44.6.2: every *begin* must
    eventually be matched by exactly one *commit* or *abort*.

    The log is append-only; entries are never modified or removed.

    Fields
    ------
    witness_id:
        Unique identifier for this witness.
    events:
        Ordered list of event dictionaries.  Each entry has at minimum the
        keys ``event_type``, ``transition_id``, and ``timestamp``.
    created_at:
        Unix epoch time at which this witness was created.
    """

    witness_id: str
    events: list[dict[str, Any]] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)

    # ------------------------------------------------------------------
    # Event recording
    # ------------------------------------------------------------------

    def record_begin(
        self,
        state_id: str,
        move_id: str,
        transition_id: str | None = None,
    ) -> str:
        """Record a *begin* event for a new transition.

        Parameters
        ----------
        state_id:
            Identifier of the source state.
        move_id:
            Identifier of the proposed move.
        transition_id:
            Optional caller-supplied ID.  When provided (e.g. the coordinator's
            ``tid``), the same value must be passed to :meth:`record_commit` or
            :meth:`record_abort` to satisfy the completeness invariant.  When
            omitted a fresh ``uuid4`` is generated.

        Returns
        -------
        str
            The ``transition_id`` (supplied or generated) to pass to
            :meth:`record_commit` or :meth:`record_abort`.
        """
        tid = transition_id if transition_id is not None else str(uuid.uuid4())
        self.events.append({
            "event_type": "begin",
            "transition_id": tid,
            "state_id": state_id,
            "move_id": move_id,
            "timestamp": time.time(),
        })
        log.debug("TransitionWitness %r: recorded begin for tid %r", self.witness_id, tid)
        return tid

    def record_commit(
        self,
        record: TransitionRecord,
        begin_transition_id: str | None = None,
    ) -> None:
        """Record a *commit* event linked to *record*.

        Parameters
        ----------
        record:
            The :class:`TransitionRecord` produced by the engine on commit.
        begin_transition_id:
            The ``transition_id`` returned by :meth:`record_begin` for this
            transition.  When supplied, the commit event is stored under this
            ID, allowing :meth:`verify_completeness` to match begin / commit
            pairs.  When omitted, ``record.record_id`` is used (which satisfies
            completeness only if :meth:`record_begin` was also called with that
            same ID).
        """
        tid = begin_transition_id if begin_transition_id is not None else record.record_id
        self.events.append({
            "event_type": "commit",
            "transition_id": tid,
            "from_state_id": record.from_state_id,
            "to_state_id": record.to_state_id,
            "move_id": record.move_id,
            "success": record.success,
            "duration_seconds": record.duration_seconds,
            "timestamp": time.time(),
        })
        log.debug(
            "TransitionWitness %r: recorded commit for tid %r (success=%s)",
            self.witness_id, tid, record.success,
        )

    def record_abort(self, transition_id: str, reason: str) -> None:
        """Record an *abort* event for a transition that was cancelled.

        Parameters
        ----------
        transition_id:
            The ``transition_id`` originally returned by :meth:`record_begin`.
        reason:
            Human-readable description of why the transition was aborted.
        """
        self.events.append({
            "event_type": "abort",
            "transition_id": transition_id,
            "reason": reason,
            "timestamp": time.time(),
        })
        log.debug(
            "TransitionWitness %r: recorded abort for tid %r (%s)",
            self.witness_id, transition_id, reason,
        )

    # ------------------------------------------------------------------
    # Analysis
    # ------------------------------------------------------------------

    def summary(self) -> dict[str, Any]:
        """Summarise the witness log.

        Returns
        -------
        dict[str, Any]
            Dictionary with keys:
            ``witness_id``, ``total_events``, ``begin_count``,
            ``commit_count``, ``abort_count``, ``complete``,
            ``created_at``, ``age_seconds``.
        """
        begins = sum(1 for e in self.events if e["event_type"] == "begin")
        commits = sum(1 for e in self.events if e["event_type"] == "commit")
        aborts = sum(1 for e in self.events if e["event_type"] == "abort")
        return {
            "witness_id": self.witness_id,
            "total_events": len(self.events),
            "begin_count": begins,
            "commit_count": commits,
            "abort_count": aborts,
            "complete": self.verify_completeness(),
            "created_at": self.created_at,
            "age_seconds": round(time.time() - self.created_at, 3),
        }

    def verify_completeness(self) -> bool:
        """Verify the completeness invariant of theory2.tex §44.6.2.

        Every *begin* event must be matched by exactly one *commit* or *abort*
        event with the same ``transition_id``.

        Returns
        -------
        bool
            ``True`` if the invariant holds; ``False`` otherwise.
        """
        begun: set[str] = set()
        resolved: set[str] = set()
        for event in self.events:
            etype = event["event_type"]
            tid = event["transition_id"]
            if etype == "begin":
                begun.add(tid)
            elif etype in ("commit", "abort"):
                resolved.add(tid)
        unresolved = begun - resolved
        if unresolved:
            log.warning(
                "TransitionWitness %r: completeness violated — %d unresolved begin(s): %s",
                self.witness_id, len(unresolved), list(unresolved)[:5],
            )
        return len(unresolved) == 0

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise the entire witness to a plain dictionary.

        Returns
        -------
        dict[str, Any]
            Dictionary with ``witness_id``, ``created_at``, and ``events``
            (list of event dicts).
        """
        return {
            "witness_id": self.witness_id,
            "created_at": self.created_at,
            "events": list(self.events),
        }


# ── Factory helpers ───────────────────────────────────────────────────────────


def make_default_transition_engine(
    timeout_seconds: float = TRANSITION_TIMEOUT_SECONDS,
) -> SemanticTransitionEngine:
    """Construct a :class:`SemanticTransitionEngine` with the standard guard set.

    Convenience factory that wires up a :class:`TransitionGuardRegistry`
    pre-populated with the four default guards from
    :meth:`TransitionGuardRegistry.default_guards` (theory2.tex §44.5.2).

    Parameters
    ----------
    timeout_seconds:
        Wall-clock timeout for individual transitions.  Defaults to
        :data:`TRANSITION_TIMEOUT_SECONDS`.

    Returns
    -------
    SemanticTransitionEngine
        Ready-to-use engine with a default guard registry and an empty log.
    """
    registry = TransitionGuardRegistry(
        guards=TransitionGuardRegistry.default_guards(),
    )
    engine = SemanticTransitionEngine(
        engine_id=str(uuid.uuid4()),
        guard_registry=registry,
        transition_log=[],
        retry_budget={},
        timeout_seconds=timeout_seconds,
    )
    log.debug("make_default_transition_engine: created engine %r", engine.engine_id)
    return engine


def make_default_transition_coordinator(
    max_active: int = MAX_ACTIVE_TRANSITIONS,
    window_size: int = DEFAULT_WINDOW_SIZE,
) -> TransitionCoordinator:
    """Construct a fully wired :class:`TransitionCoordinator`.

    Creates and wires together a :class:`SemanticTransitionEngine`,
    a :class:`TransitionAnalyzer`, and a :class:`TransitionCoordinator`.
    This is the recommended entry point for consumers of this module.

    Parameters
    ----------
    max_active:
        Maximum simultaneously in-flight transitions.  Defaults to
        :data:`MAX_ACTIVE_TRANSITIONS`.
    window_size:
        Sliding-window size for :class:`TransitionAnalyzer` computations.
        Defaults to :data:`DEFAULT_WINDOW_SIZE`.

    Returns
    -------
    TransitionCoordinator
        Fully configured coordinator ready to accept begin/commit/abort calls.
    """
    engine = make_default_transition_engine()
    analyzer = TransitionAnalyzer(
        analyzer_id=str(uuid.uuid4()),
        window_size=window_size,
    )
    coordinator = TransitionCoordinator(
        coordinator_id=str(uuid.uuid4()),
        engine=engine,
        analyzer=analyzer,
        active_transitions={},
        completed_transitions=[],
        max_active=max_active,
    )
    log.debug(
        "make_default_transition_coordinator: created coordinator %r",
        coordinator.coordinator_id,
    )
    return coordinator


# ── Smoke test ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import pprint

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    print("=" * 72)
    print("semantic_transitions — smoke test (theory2.tex §§44.5–44.7)")
    print("=" * 72)

    # ── Build coordinator and witness ─────────────────────────────────────
    coordinator = make_default_transition_coordinator()
    witness = TransitionWitness(witness_id=str(uuid.uuid4()))

    # Construct a starting state with a token budget sufficient for 3 moves.
    state = SemanticControlState(
        state_id="state-0",
        cover_ids=["cover-A"],
        context_ids=["ctx-1"],
        section_ids=["sec-1", "sec-2", "sec-3"],
        treaty_ids=[],
        obligation_ids=["obl-1"],
        channel_ids=["ch-alpha"],
        budget={"tokens": 10.0},
        metadata={"trust_level": 1.0},
    )

    # ── Three forward transitions ─────────────────────────────────────────
    print("\n[1] Running 3 forward transitions …")
    last_record = None
    current_state = state

    for step in range(1, 4):
        move = AdmissibleMove(
            move_id=f"move-fwd-{step}",
            move_type="FORWARD",
            cost=1.0,
            priority=step,
            trust_requirement=0.5,
        )
        # Phase 1: begin — obtain coordinator tid and use it as the witness id too,
        # so that the completeness invariant (§44.6.2) is satisfied.
        try:
            tid = coordinator.begin_transition(
                current_state, move, TransitionTypeEnum.FORWARD
            )
        except (ValueError, RuntimeError) as exc:
            witness.record_begin(current_state.state_id, move.move_id)
            witness.record_abort(str(uuid.uuid4()), str(exc))
            print(f"  Step {step}: begin FAILED — {exc}")
            continue

        witness.record_begin(current_state.state_id, move.move_id, transition_id=tid)

        # Phase 2: commit
        record = coordinator.commit_transition(tid)
        witness.record_commit(record, begin_transition_id=tid)
        last_record = record

        if record.success:
            # Advance current_state to the successor produced by the engine.
            # In a full implementation the engine would return the next state;
            # here we reconstruct it to keep the smoke test self-contained.
            current_state = SemanticControlState(
                state_id=record.to_state_id,
                cover_ids=current_state.cover_ids + [f"cover-{step}"],
                context_ids=current_state.context_ids,
                section_ids=current_state.section_ids,
                treaty_ids=current_state.treaty_ids,
                obligation_ids=current_state.obligation_ids,
                channel_ids=current_state.channel_ids,
                budget={"tokens": current_state.budget.get("tokens", 0.0) - 1.0},
                metadata={"trust_level": 1.0, "step": step},
            )
            print(f"  Step {step}: ✓  {record.from_state_id!r} → {record.to_state_id!r}  "
                  f"({record.duration_seconds*1000:.2f} ms)")
        else:
            print(f"  Step {step}: ✗  {record.error_message}")

    # ── Rollback ──────────────────────────────────────────────────────────
    print("\n[2] Attempting rollback of last successful transition …")
    if last_record and last_record.success:
        rolled = coordinator.engine.rollback(current_state, last_record)
        if rolled is not None:
            print(f"  Rollback ✓  restored to state_id={rolled.state_id!r}")
            current_state = rolled
        else:
            print("  Rollback returned None (nothing to undo).")
    else:
        print("  No successful record to roll back.")

    # ── Abort a pending transition ────────────────────────────────────────
    print("\n[3] Beginning and aborting a CHECKPOINT transition …")
    chk_move = AdmissibleMove(
        move_id="move-chk-1",
        move_type="CHECKPOINT",
        cost=0.0,
        trust_requirement=0.0,
    )
    try:
        abort_tid = coordinator.begin_transition(
            current_state, chk_move, TransitionTypeEnum.CHECKPOINT
        )
        witness.record_begin(current_state.state_id, chk_move.move_id, transition_id=abort_tid)
        ok = coordinator.abort_transition(abort_tid)
        witness.record_abort(abort_tid, "smoke test abort")
        print(f"  Abort {'✓' if ok else '✗'}  transition_id={abort_tid!r}")
    except (ValueError, RuntimeError) as exc:
        print(f"  Could not begin checkpoint transition: {exc}")

    # ── Print coordinator status ──────────────────────────────────────────
    print("\n[4] Coordinator status:")
    pprint.pprint(coordinator.status(), indent=2)

    # ── Print witness summary ─────────────────────────────────────────────
    print("\n[5] Witness summary:")
    pprint.pprint(witness.summary(), indent=2)
    print(f"  Completeness invariant (§44.6.2): {witness.verify_completeness()}")

    # ── Print analyzer trend report ───────────────────────────────────────
    print("\n[6] Analyzer trend report:")
    trend = coordinator.analyzer.trend_report(coordinator.completed_transitions)
    pprint.pprint(trend, indent=2)

    print("\n" + "=" * 72)
    print("Smoke test complete.")
    print("=" * 72)
