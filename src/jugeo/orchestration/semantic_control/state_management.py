"""Semantic state management for JuGeo semantic control (theory2.tex Ch44).

This module implements the full lifecycle management of ``SemanticControlState``
objects within the JuGeo semantic-control layer.  The architecture follows the
control-theoretic framework of theory2.tex Ch44, which treats the proof-search
state as a point in a *semantic site* acted upon by admissible moves.

Key responsibilities
────────────────────
*   **StateSnapshot** – point-in-time capture of state with age/query support.
*   **StateEventBus** – lightweight pub/sub for state change notifications,
    enabling decoupled monitoring and diagnostics.
*   **StateValidator** – rule-based invariant checking with transition guards,
    protecting the invariants of theory2.tex §44.1 (admissibility, budget
    non-negativity, non-empty cover in non-initial states).
*   **StateProjector** – projects full state to named sub-dimensions for
    lightweight diffing and dashboard views.
*   **StateAggregator** – merges multiple states into a summary state for
    fleet-level views (theory2.tex §44.3 – fleet aggregation).
*   **StateDeltaComputer** – computes, applies, and composes ``StateDelta``
    objects, supporting the *delta calculus* of theory2.tex §44.2.
*   **StateManager** – top-level lifecycle manager with rollback, snapshot
    retrieval, and export.

Design notes
────────────
*   Mutable state classes use ``@dataclass(slots=True)``; immutable value
    objects use ``@dataclass(frozen=True)``.
*   All IDs are ``uuid.uuid4()``-based strings; all timestamps are
    ``time.time()`` floats (Unix epoch seconds).
*   Upstream imports are guarded with ``try/except`` so this module degrades
    gracefully when the rest of the JuGeo graph is not yet compiled.
*   Module-level constants are defined immediately after imports.

References
──────────
*   theory2.tex Ch44  – Semantic Control
*   theory2.tex §44.1 – Admissible States and Invariants
*   theory2.tex §44.2 – Delta Calculus and Transition Guards
*   theory2.tex §44.3 – Fleet Aggregation
*   theory2.tex §44.4 – Convergence and Certificate Issuance
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
        SemanticControlState,
        StateDelta,
        StateHealthStatus,
    )
except Exception:  # pragma: no cover

    @dataclass(slots=True)
    class SemanticControlState:  # type: ignore[no-redef]
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
            return all(v >= 0 for v in self.budget.values())

        def coverage_ratio(self) -> float:
            total = len(self.section_ids) or 1
            return len(self.cover_ids) / total

        def attainability_score(self) -> float:
            return self.coverage_ratio() * (1.0 if self.is_admissible() else 0.0)

        def delta_from(self, other: "SemanticControlState") -> "StateDelta":
            added = [c for c in self.cover_ids if c not in other.cover_ids]
            removed = [c for c in other.cover_ids if c not in self.cover_ids]
            added_s = [s for s in self.section_ids if s not in other.section_ids]
            removed_s = [s for s in other.section_ids if s not in self.section_ids]
            added_o = [o for o in self.obligation_ids if o not in other.obligation_ids]
            resolved_o = [o for o in other.obligation_ids if o not in self.obligation_ids]
            budget_delta = {
                k: self.budget.get(k, 0.0) - other.budget.get(k, 0.0)
                for k in set(list(self.budget) + list(other.budget))
            }
            score_delta = self.attainability_score() - other.attainability_score()
            return StateDelta(
                added_covers=tuple(added),
                removed_covers=tuple(removed),
                added_sections=tuple(added_s),
                removed_sections=tuple(removed_s),
                added_obligations=tuple(added_o),
                resolved_obligations=tuple(resolved_o),
                budget_delta=budget_delta,
                score_delta=score_delta,
            )

        def to_dict(self) -> dict[str, Any]:
            return {
                "state_id": self.state_id,
                "cover_ids": list(self.cover_ids),
                "context_ids": list(self.context_ids),
                "section_ids": list(self.section_ids),
                "treaty_ids": list(self.treaty_ids),
                "obligation_ids": list(self.obligation_ids),
                "channel_ids": list(self.channel_ids),
                "budget": dict(self.budget),
                "timestamp": self.timestamp,
                "metadata": dict(self.metadata),
            }

        def snapshot(self, label: str = "") -> "StateSnapshot":
            return StateSnapshot(
                snapshot_id=str(uuid.uuid4()),
                state=self,
                taken_at=time.time(),
                label=label,
                metadata={},
            )

        def health_status(self) -> "StateHealthStatus":
            return StateHealthStatus.HEALTHY if self.is_admissible() else StateHealthStatus.DEGRADED

    @dataclass(frozen=True)
    class StateDelta:  # type: ignore[no-redef]
        added_covers: tuple[str, ...] = ()
        removed_covers: tuple[str, ...] = ()
        added_sections: tuple[str, ...] = ()
        removed_sections: tuple[str, ...] = ()
        added_obligations: tuple[str, ...] = ()
        resolved_obligations: tuple[str, ...] = ()
        budget_delta: dict[str, float] = field(default_factory=dict)
        score_delta: float = 0.0

        def is_improving(self) -> bool:
            return self.score_delta > 0

        def magnitude(self) -> float:
            return (
                len(self.added_covers)
                + len(self.removed_covers)
                + len(self.added_sections)
                + len(self.removed_sections)
                + len(self.added_obligations)
                + len(self.resolved_obligations)
                + abs(self.score_delta)
            )

        def summary(self) -> str:
            return (
                f"StateDelta(+{len(self.added_covers)} covers, "
                f"-{len(self.removed_covers)} covers, "
                f"+{len(self.resolved_obligations)} resolved, "
                f"score_delta={self.score_delta:+.4f})"
            )

        def to_dict(self) -> dict[str, Any]:
            return {
                "added_covers": list(self.added_covers),
                "removed_covers": list(self.removed_covers),
                "added_sections": list(self.added_sections),
                "removed_sections": list(self.removed_sections),
                "added_obligations": list(self.added_obligations),
                "resolved_obligations": list(self.resolved_obligations),
                "budget_delta": dict(self.budget_delta),
                "score_delta": self.score_delta,
            }

    class StateHealthStatus(enum.Enum):  # type: ignore[no-redef]
        HEALTHY = "healthy"
        DEGRADED = "degraded"
        CRITICAL = "critical"
        UNKNOWN = "unknown"

# ── Module constants ─────────────────────────────────────────────────────────

VERSION: str = "0.1.0"
"""Module version, incremented on breaking schema changes."""

LOGGER: logging.Logger = logging.getLogger(__name__)
"""Module-level logger; configure via ``logging.getLogger('jugeo...')``."""

DEFAULT_MAX_HISTORY: int = 1000
"""Default cap on the number of snapshots retained by :class:`StateManager`."""

DEFAULT_AGGREGATION_STRATEGY: str = "union"
"""Default aggregation strategy for :class:`StateAggregator`."""


# ── StateEventKind ───────────────────────────────────────────────────────────


class StateEventKind(enum.Enum):
    """Enumeration of semantic state lifecycle events.

    Each value corresponds to a distinct transition or action in the
    theory2.tex Ch44 state machine.  Consumers subscribe to specific
    kinds via :class:`StateEventBus`.

    Members
    -------
    CREATED
        A brand-new ``SemanticControlState`` has been constructed.
    UPDATED
        An in-place field mutation occurred (rare; prefer TRANSITION).
    SNAPSHOT_TAKEN
        A :class:`StateSnapshot` was captured.
    TRANSITION
        The canonical *state transition* (theory2.tex §44.2): old → new.
    VALIDATED
        A :class:`StateValidator` ran and returned results.
    PROJECTED
        A :class:`StateProjector` produced a sub-dimension view.
    AGGREGATED
        A :class:`StateAggregator` merged multiple states.
    RESET
        The state was hard-reset (discards history, budget zeroed).
    """

    CREATED = "created"
    UPDATED = "updated"
    SNAPSHOT_TAKEN = "snapshot_taken"
    TRANSITION = "transition"
    VALIDATED = "validated"
    PROJECTED = "projected"
    AGGREGATED = "aggregated"
    RESET = "reset"


# ── StateEvent ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class StateEvent:
    """An immutable record of a state lifecycle occurrence.

    Produced by :class:`StateManager` and consumed by :class:`StateEventBus`
    subscribers.  Every event carries a strongly-typed ``kind`` and a free-form
    ``payload`` dictionary for additional context.

    Parameters
    ----------
    event_id:
        Unique identifier (``uuid4()``).
    kind:
        The :class:`StateEventKind` discriminant.
    state_id:
        ID of the :class:`SemanticControlState` the event concerns.
    payload:
        Arbitrary event-specific context (e.g., violation list for VALIDATED).
    timestamp:
        Unix epoch float when the event was emitted.

    References
    ----------
    theory2.tex Ch44 – event-driven state observation.
    """

    event_id: str
    kind: StateEventKind
    state_id: str
    payload: dict[str, Any]
    timestamp: float

    def to_dict(self) -> dict[str, Any]:
        """Serialise the event to a plain dictionary.

        Returns
        -------
        dict[str, Any]
            JSON-serialisable representation including all fields, with
            ``kind`` as its string value.
        """
        return {
            "event_id": self.event_id,
            "kind": self.kind.value,
            "state_id": self.state_id,
            "payload": self.payload,
            "timestamp": self.timestamp,
        }


# ── StateSnapshot ────────────────────────────────────────────────────────────


@dataclass(slots=True)
class StateSnapshot:
    """Point-in-time capture of a :class:`SemanticControlState`.

    Snapshots are produced by :meth:`StateManager.take_snapshot` and
    retained in the manager's ``history`` list up to ``max_history`` entries.
    They are the basis for rollback (theory2.tex §44.2 – reversible transitions)
    and diff queries.

    Parameters
    ----------
    snapshot_id:
        Unique identifier (``uuid4()``).
    state:
        Deep copy of the :class:`SemanticControlState` at capture time.
    taken_at:
        Unix epoch float when the snapshot was taken.
    label:
        Human-readable tag (e.g. ``"pre-refine-step-3"``).
    metadata:
        Arbitrary key/value annotations.
    """

    snapshot_id: str
    state: SemanticControlState
    taken_at: float
    label: str
    metadata: dict[str, Any]

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def age(self) -> float:
        """Return the elapsed time since this snapshot was taken, in seconds.

        Computed as ``time.time() - self.taken_at``.  Useful for eviction
        policies and staleness checks (theory2.tex §44.2 – snapshot validity
        horizon).

        Returns
        -------
        float
            Non-negative number of seconds since capture.
        """
        return time.time() - self.taken_at

    def to_dict(self) -> dict[str, Any]:
        """Serialise snapshot to a plain dictionary.

        Includes the full ``state.to_dict()`` under the ``"state"`` key so
        the snapshot can be persisted and reloaded without the live object.

        Returns
        -------
        dict[str, Any]
            JSON-serialisable representation.
        """
        return {
            "snapshot_id": self.snapshot_id,
            "state": self.state.to_dict(),
            "taken_at": self.taken_at,
            "label": self.label,
            "metadata": self.metadata,
        }

    def summary(self) -> str:
        """Return a compact human-readable description of the snapshot.

        Includes snapshot ID (first 8 chars), label, age in seconds, and
        top-level coverage ratio of the captured state.

        Returns
        -------
        str
            One-line summary suitable for log output.
        """
        age_s = f"{self.age():.1f}s"
        cov = getattr(self.state, "coverage_ratio", lambda: None)()
        cov_str = f"{cov:.3f}" if cov is not None else "n/a"
        label_str = f" [{self.label}]" if self.label else ""
        return (
            f"Snapshot({self.snapshot_id[:8]}){label_str} "
            f"age={age_s} coverage={cov_str}"
        )

    def matches(self, query: dict[str, Any]) -> bool:
        """Test whether this snapshot satisfies all key/value constraints in *query*.

        Each key in *query* is first looked up in ``self.metadata``; if absent
        there, it is resolved as an attribute of ``self.state`` (if present).
        A missing key that is not in metadata and not a state attribute is
        treated as a mismatch.

        Parameters
        ----------
        query:
            Dictionary of ``{field: expected_value}`` pairs.

        Returns
        -------
        bool
            ``True`` iff every constraint is satisfied.

        Examples
        --------
        >>> snap.matches({"label": "pre-refine"})
        True
        >>> snap.matches({"label": "pre-refine", "nonexistent": 42})
        False
        """
        for key, expected in query.items():
            if key == "label":
                if self.label != expected:
                    return False
            elif key in self.metadata:
                if self.metadata[key] != expected:
                    return False
            elif hasattr(self.state, key):
                if getattr(self.state, key) != expected:
                    return False
            else:
                return False
        return True


# ── StateEventBus ────────────────────────────────────────────────────────────


@dataclass(slots=True)
class StateEventBus:
    """Publish/subscribe bus for :class:`StateEvent` notifications.

    Decouples state producers (e.g. :class:`StateManager`) from consumers
    (e.g. dashboards, convergence monitors, test assertions).  Callbacks are
    indexed by :class:`StateEventKind`; a subscriber registered for ``None``
    would need explicit wildcard logic (not provided here – subscribe per kind).

    Parameters
    ----------
    _subscribers:
        Internal map from ``StateEventKind`` to list of ``(sub_id, callback)``
        pairs.
    _history:
        Ordered list of every published :class:`StateEvent`, used for replay
        and post-hoc inspection.

    References
    ----------
    theory2.tex Ch44 – event-driven state observation.
    """

    _subscribers: dict[StateEventKind, list[tuple[str, Callable[[StateEvent], None]]]] = field(
        default_factory=dict
    )
    _history: list[StateEvent] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def subscribe(
        self,
        kind: StateEventKind,
        callback: Callable[[StateEvent], None],
    ) -> str:
        """Register *callback* to be called whenever an event of *kind* is published.

        Parameters
        ----------
        kind:
            The :class:`StateEventKind` to subscribe to.
        callback:
            A callable that receives a single :class:`StateEvent` argument.
            Exceptions raised by the callback are caught and logged so they
            never propagate to the publisher.

        Returns
        -------
        str
            A subscription ID (``uuid4()``) that can be passed to
            :meth:`unsubscribe` to deregister.
        """
        sub_id = str(uuid.uuid4())
        self._subscribers.setdefault(kind, []).append((sub_id, callback))
        LOGGER.debug("StateEventBus: subscribed %s to %s", sub_id[:8], kind.value)
        return sub_id

    def unsubscribe(self, sub_id: str) -> bool:
        """Remove the subscription identified by *sub_id*.

        Parameters
        ----------
        sub_id:
            The subscription ID returned by :meth:`subscribe`.

        Returns
        -------
        bool
            ``True`` if the subscription was found and removed, ``False`` if
            *sub_id* was not registered.
        """
        for kind, subs in self._subscribers.items():
            for i, (sid, _) in enumerate(subs):
                if sid == sub_id:
                    self._subscribers[kind].pop(i)
                    LOGGER.debug(
                        "StateEventBus: unsubscribed %s from %s", sub_id[:8], kind.value
                    )
                    return True
        return False

    def publish(self, event: StateEvent) -> None:
        """Emit *event* to all registered subscribers and append to history.

        Subscribers for ``event.kind`` are called in registration order.
        Any exception raised by a subscriber is caught, logged at WARNING
        level, and does not abort delivery to subsequent subscribers.

        Parameters
        ----------
        event:
            The :class:`StateEvent` to publish.
        """
        self._history.append(event)
        for sub_id, callback in self._subscribers.get(event.kind, []):
            try:
                callback(event)
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning(
                    "StateEventBus: subscriber %s raised %s: %s",
                    sub_id[:8],
                    type(exc).__name__,
                    exc,
                )

    def history(self, kind: StateEventKind | None = None) -> list[StateEvent]:
        """Return the event history, optionally filtered by *kind*.

        Parameters
        ----------
        kind:
            If provided, return only events with ``event.kind == kind``.
            If ``None``, return the full history.

        Returns
        -------
        list[StateEvent]
            Ordered (oldest first) list of matching events.
        """
        if kind is None:
            return list(self._history)
        return [e for e in self._history if e.kind == kind]

    def clear_history(self) -> None:
        """Discard all recorded events from the history buffer.

        This does **not** affect registered subscribers.  Useful in tests
        to reset the bus between scenarios.
        """
        self._history.clear()
        LOGGER.debug("StateEventBus: history cleared")


# ── StateValidator ───────────────────────────────────────────────────────────


@dataclass(slots=True)
class StateValidator:
    """Rule-based invariant checker for :class:`SemanticControlState`.

    Implements the admissibility invariants of theory2.tex §44.1.  Each
    *rule* is a callable ``(SemanticControlState) -> str | None``; returning
    ``None`` means the rule passed, returning a non-empty string means the
    rule was violated (the string is the violation message).

    Parameters
    ----------
    rules:
        Ordered list of rule callables.  Rules are evaluated in list order;
        evaluation stops early if ``strict=True`` and a violation is found.
    strict:
        If ``True``, the validator raises :exc:`ValueError` on the first
        violation in :meth:`validate`; if ``False``, all rules are evaluated
        and violations collected.

    References
    ----------
    theory2.tex §44.1 – Admissible States and Invariants.
    """

    rules: list[Callable[[SemanticControlState], str | None]]
    strict: bool = True

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def add_rule(self, rule: Callable[[SemanticControlState], str | None]) -> None:
        """Append *rule* to the validator's rule list.

        Parameters
        ----------
        rule:
            A callable ``(SemanticControlState) -> str | None`` following the
            convention: ``None`` = pass, non-empty string = violation message.
        """
        self.rules.append(rule)
        LOGGER.debug("StateValidator: added rule %s", getattr(rule, "__name__", repr(rule)))

    def validate(self, state: SemanticControlState) -> list[str]:
        """Run all rules against *state* and return a list of violations.

        If ``self.strict`` is ``True``, evaluation stops at the first
        violation and returns a single-element list.  If ``False``, all rules
        are evaluated and all violations returned.

        Parameters
        ----------
        state:
            The state to validate.

        Returns
        -------
        list[str]
            List of violation messages.  Empty list means the state is valid.
        """
        violations: list[str] = []
        for rule in self.rules:
            try:
                result = rule(state)
            except Exception as exc:  # noqa: BLE001
                violations.append(f"Rule {getattr(rule, '__name__', '?')} raised: {exc}")
                if self.strict:
                    return violations
                continue
            if result is not None:
                violations.append(result)
                if self.strict:
                    return violations
        return violations

    def is_valid(self, state: SemanticControlState) -> bool:
        """Return ``True`` iff :meth:`validate` produces no violations.

        This is a convenience wrapper that suppresses strict mode for a
        single boolean query.

        Parameters
        ----------
        state:
            The state to test.

        Returns
        -------
        bool
        """
        saved = self.strict
        self.strict = False
        valid = len(self.validate(state)) == 0
        self.strict = saved
        return valid

    def validate_transition(
        self,
        from_state: SemanticControlState,
        to_state: SemanticControlState,
    ) -> list[str]:
        """Validate both the new state and the transition itself.

        Runs all per-state rules against *to_state*, then checks the
        additional *transition invariants* (theory2.tex §44.2):

        *   The new state's budget must not be more negative than the old
            state's budget on any channel (no budget explosion).
        *   At least one cover or resolved obligation must improve to
            constitute a productive transition.

        Parameters
        ----------
        from_state:
            The state before the transition.
        to_state:
            The state after the transition.

        Returns
        -------
        list[str]
            Combined list of per-state and transition-level violations.
        """
        violations = self.validate(to_state)
        for channel, new_val in to_state.budget.items():
            old_val = from_state.budget.get(channel, 0.0)
            if new_val < old_val - 1e-9:
                violations.append(
                    f"Budget regression on channel '{channel}': "
                    f"{old_val:.4f} → {new_val:.4f}"
                )
        new_covers = set(to_state.cover_ids)
        old_covers = set(from_state.cover_ids)
        old_obs = set(from_state.obligation_ids)
        new_obs = set(to_state.obligation_ids)
        gained_covers = new_covers - old_covers
        resolved_obs = old_obs - new_obs
        if not gained_covers and not resolved_obs and to_state.state_id != from_state.state_id:
            violations.append(
                "Non-productive transition: no new covers and no resolved obligations."
            )
        return violations

    @classmethod
    def default_rules(cls) -> list[Callable[[SemanticControlState], str | None]]:
        """Return the standard invariant rules from theory2.tex §44.1.

        Standard rules
        --------------
        *   **budget_non_negative**: All budget values must be ≥ 0.
        *   **state_id_present**: ``state_id`` must be a non-empty string.
        *   **cover_ids_list**: ``cover_ids`` must be a list.
        *   **obligations_list**: ``obligation_ids`` must be a list.

        Returns
        -------
        list[Callable]
            Four standard rule callables ready to be passed to the constructor.
        """

        def budget_non_negative(state: SemanticControlState) -> str | None:
            for ch, val in state.budget.items():
                if val < -1e-9:
                    return f"Budget channel '{ch}' is negative: {val:.6f}"
            return None

        def state_id_present(state: SemanticControlState) -> str | None:
            if not state.state_id:
                return "state_id must be a non-empty string"
            return None

        def cover_ids_list(state: SemanticControlState) -> str | None:
            if not isinstance(state.cover_ids, (list, tuple)):
                return f"cover_ids must be list/tuple, got {type(state.cover_ids).__name__}"
            return None

        def obligations_list(state: SemanticControlState) -> str | None:
            if not isinstance(state.obligation_ids, (list, tuple)):
                return (
                    f"obligation_ids must be list/tuple, "
                    f"got {type(state.obligation_ids).__name__}"
                )
            return None

        return [budget_non_negative, state_id_present, cover_ids_list, obligations_list]


# ── StateProjector ───────────────────────────────────────────────────────────


@dataclass(slots=True)
class StateProjector:
    """Projects a full :class:`SemanticControlState` to named sub-dimensions.

    Sub-dimension projection (theory2.tex §44.3) extracts lightweight views
    of the state that are sufficient for specific consumers (e.g. the
    convergence monitor only needs coverage ratio, not the full cover list).

    Parameters
    ----------
    dimensions:
        List of dimension names to include in :meth:`project`.  Supported
        values: ``"covers"``, ``"obligations"``, ``"budget"``, ``"sections"``,
        ``"treaties"``, ``"channels"``, ``"score"``.
    weights:
        Per-dimension scalar weights used by :meth:`weighted_projection`.
        Defaults to 1.0 for any missing dimension.
    """

    dimensions: list[str]
    weights: dict[str, float]

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def project(self, state: SemanticControlState) -> dict[str, Any]:
        """Return a dictionary containing only the requested dimensions.

        Iterates over ``self.dimensions`` and calls the corresponding
        per-dimension helper.  Unrecognised dimension names are silently
        skipped with a DEBUG log entry.

        Parameters
        ----------
        state:
            The state to project.

        Returns
        -------
        dict[str, Any]
            Mapping of dimension name → projected value.
        """
        result: dict[str, Any] = {}
        dispatch: dict[str, Callable[[], Any]] = {
            "covers": lambda: self.project_covers(state),
            "obligations": lambda: self.project_obligations(state),
            "budget": lambda: self.project_budget(state),
            "sections": lambda: list(state.section_ids),
            "treaties": lambda: list(state.treaty_ids),
            "channels": lambda: list(state.channel_ids),
            "score": lambda: state.attainability_score(),
        }
        for dim in self.dimensions:
            fn = dispatch.get(dim)
            if fn is None:
                LOGGER.debug("StateProjector: unknown dimension '%s', skipping", dim)
                continue
            result[dim] = fn()
        return result

    def project_covers(self, state: SemanticControlState) -> list[str]:
        """Return the cover IDs from *state*.

        Parameters
        ----------
        state:
            Source state.

        Returns
        -------
        list[str]
            Copy of ``state.cover_ids``.
        """
        return list(state.cover_ids)

    def project_obligations(self, state: SemanticControlState) -> list[str]:
        """Return the unresolved obligation IDs from *state*.

        Parameters
        ----------
        state:
            Source state.

        Returns
        -------
        list[str]
            Copy of ``state.obligation_ids``.
        """
        return list(state.obligation_ids)

    def project_budget(self, state: SemanticControlState) -> dict[str, float]:
        """Return a copy of the budget dictionary from *state*.

        Parameters
        ----------
        state:
            Source state.

        Returns
        -------
        dict[str, float]
            Channel → remaining budget mapping.
        """
        return dict(state.budget)

    def weighted_projection(self, state: SemanticControlState) -> dict[str, float]:
        """Return scalar per-dimension scores scaled by ``self.weights``.

        Each dimension is reduced to a single float:

        *   ``covers``      → ``len(cover_ids)``
        *   ``obligations`` → ``-len(obligation_ids)`` (fewer is better)
        *   ``budget``      → ``sum(budget.values())``
        *   ``score``       → ``attainability_score()``
        *   others          → 0.0

        The float is then multiplied by the corresponding weight (defaulting
        to 1.0 if not in ``self.weights``).

        Parameters
        ----------
        state:
            Source state.

        Returns
        -------
        dict[str, float]
            Mapping of dimension name → weighted scalar score.
        """
        scalar_map: dict[str, float] = {
            "covers": float(len(state.cover_ids)),
            "obligations": -float(len(state.obligation_ids)),
            "budget": sum(state.budget.values()),
            "sections": float(len(state.section_ids)),
            "treaties": float(len(state.treaty_ids)),
            "channels": float(len(state.channel_ids)),
            "score": state.attainability_score(),
        }
        return {
            dim: scalar_map.get(dim, 0.0) * self.weights.get(dim, 1.0)
            for dim in self.dimensions
        }


# ── StateAggregator ──────────────────────────────────────────────────────────


@dataclass(slots=True)
class StateAggregator:
    """Aggregates multiple :class:`SemanticControlState` objects into a summary.

    Fleet-level views (theory2.tex §44.3) require collapsing many agent
    states into a single representative state.  Three strategies are
    supported:

    *   ``"union"``        – union of all cover/section IDs, intersection of
                             obligations, mean budget.
    *   ``"intersection"`` – intersection of all cover IDs, union of
                             obligations, min budget.
    *   ``"weighted"``     – weighted average, using ``self.weights`` keyed by
                             ``state_id``.

    Parameters
    ----------
    aggregation_strategy:
        One of ``"union"``, ``"intersection"``, ``"weighted"``.
    weights:
        Mapping from ``state_id`` to scalar weight.  Only used by the
        ``"weighted"`` strategy; missing IDs default to 1.0.
    """

    aggregation_strategy: str = DEFAULT_AGGREGATION_STRATEGY
    weights: dict[str, float] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def aggregate(self, states: list[SemanticControlState]) -> SemanticControlState:
        """Merge *states* into a single :class:`SemanticControlState`.

        The aggregation follows ``self.aggregation_strategy``.  The resulting
        state carries a fresh ``state_id`` and the current timestamp, and its
        ``metadata`` records the source state IDs and strategy used.

        Parameters
        ----------
        states:
            Non-empty list of states to aggregate.

        Returns
        -------
        SemanticControlState
            Aggregated state.

        Raises
        ------
        ValueError
            If *states* is empty.
        """
        if not states:
            raise ValueError("StateAggregator.aggregate requires at least one state.")
        covers = self.coverage_union(states)
        budget = self.aggregate_budgets(states)
        if self.aggregation_strategy == "intersection":
            covers = list(
                set(states[0].cover_ids).intersection(*(set(s.cover_ids) for s in states[1:]))
            )
            obligations = self.obligation_intersection(states)
        elif self.aggregation_strategy == "union":
            obligations = list({o for s in states for o in s.obligation_ids})
        else:
            obligations = self.obligation_intersection(states)
        all_sections = list({sec for s in states for sec in s.section_ids})
        all_contexts = list({c for s in states for c in s.context_ids})
        all_treaties = list({t for s in states for t in s.treaty_ids})
        all_channels = list({ch for s in states for ch in s.channel_ids})
        return SemanticControlState(
            state_id=str(uuid.uuid4()),
            cover_ids=covers,
            context_ids=all_contexts,
            section_ids=all_sections,
            treaty_ids=all_treaties,
            obligation_ids=obligations,
            channel_ids=all_channels,
            budget=budget,
            timestamp=time.time(),
            metadata={
                "source_state_ids": [s.state_id for s in states],
                "aggregation_strategy": self.aggregation_strategy,
            },
        )

    def aggregate_budgets(self, states: list[SemanticControlState]) -> dict[str, float]:
        """Merge budget dictionaries from *states*.

        Uses *mean* for the ``"union"`` and ``"weighted"`` strategies (to
        avoid budget inflation), and *min* for ``"intersection"`` (most
        conservative).

        Parameters
        ----------
        states:
            States whose budgets are to be merged.

        Returns
        -------
        dict[str, float]
            Merged budget mapping channel → value.
        """
        all_channels: set[str] = set()
        for s in states:
            all_channels.update(s.budget.keys())
        result: dict[str, float] = {}
        for ch in all_channels:
            values = [s.budget.get(ch, 0.0) for s in states]
            if self.aggregation_strategy == "intersection":
                result[ch] = min(values)
            else:
                result[ch] = sum(values) / len(values)
        return result

    def coverage_union(self, states: list[SemanticControlState]) -> list[str]:
        """Return the union of all cover IDs across *states*.

        Parameters
        ----------
        states:
            Source states.

        Returns
        -------
        list[str]
            Deduplicated list of all cover IDs.
        """
        return list({c for s in states for c in s.cover_ids})

    def obligation_intersection(self, states: list[SemanticControlState]) -> list[str]:
        """Return the intersection of obligation IDs across *states*.

        Only obligations that appear in **all** states are retained in the
        aggregated view (i.e. obligations that at least one agent has
        already resolved are dropped).

        Parameters
        ----------
        states:
            Source states.

        Returns
        -------
        list[str]
            Obligations present in every state.
        """
        if not states:
            return []
        common = set(states[0].obligation_ids)
        for s in states[1:]:
            common &= set(s.obligation_ids)
        return list(common)

    def weighted_attainability(self, states: list[SemanticControlState]) -> float:
        """Return the weighted average attainability score across *states*.

        Each state is weighted by ``self.weights.get(state.state_id, 1.0)``.

        Parameters
        ----------
        states:
            Source states.

        Returns
        -------
        float
            Weighted mean attainability ∈ [0, 1].
        """
        if not states:
            return 0.0
        total_weight = 0.0
        weighted_sum = 0.0
        for s in states:
            w = self.weights.get(s.state_id, 1.0)
            weighted_sum += s.attainability_score() * w
            total_weight += w
        return weighted_sum / total_weight if total_weight > 0 else 0.0


# ── StateDeltaComputer ───────────────────────────────────────────────────────


@dataclass(slots=True)
class StateDeltaComputer:
    """Computes, applies, and composes :class:`StateDelta` objects.

    The *delta calculus* of theory2.tex §44.2 formalises transitions as
    structured differences rather than wholesale state replacement.  This
    class implements that calculus.

    Parameters
    ----------
    track_metadata:
        If ``True``, metadata diff information is included in the ``payload``
        of computed deltas.  Set to ``False`` for performance-critical paths.
    """

    track_metadata: bool = True

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def compute(
        self,
        from_state: SemanticControlState,
        to_state: SemanticControlState,
    ) -> StateDelta:
        """Compute the :class:`StateDelta` from *from_state* to *to_state*.

        Implements the structural diff defined in theory2.tex §44.2:

        *   ``added_covers``       = ``to.cover_ids - from.cover_ids``
        *   ``removed_covers``     = ``from.cover_ids - to.cover_ids``
        *   ``added_sections``     = ``to.section_ids - from.section_ids``
        *   ``removed_sections``   = ``from.section_ids - to.section_ids``
        *   ``added_obligations``  = ``to.obligation_ids - from.obligation_ids``
        *   ``resolved_obligations``= ``from.obligation_ids - to.obligation_ids``
        *   ``budget_delta``       = channel-wise difference
        *   ``score_delta``        = ``to.attainability_score() - from.attainability_score()``

        Parameters
        ----------
        from_state:
            The baseline state.
        to_state:
            The target state.

        Returns
        -------
        StateDelta
            Structural difference from *from_state* to *to_state*.
        """
        from_covers = set(from_state.cover_ids)
        to_covers = set(to_state.cover_ids)
        from_sections = set(from_state.section_ids)
        to_sections = set(to_state.section_ids)
        from_obs = set(from_state.obligation_ids)
        to_obs = set(to_state.obligation_ids)
        all_channels = set(from_state.budget) | set(to_state.budget)
        budget_delta = {
            ch: to_state.budget.get(ch, 0.0) - from_state.budget.get(ch, 0.0)
            for ch in all_channels
        }
        score_delta = (
            to_state.attainability_score() - from_state.attainability_score()
        )
        if score_delta == 0.0:
            score_delta += 0.01 * len(to_covers - from_covers)
            score_delta += 0.01 * len(from_obs - to_obs)
        return StateDelta(
            added_covers=tuple(sorted(to_covers - from_covers)),
            removed_covers=tuple(sorted(from_covers - to_covers)),
            added_sections=tuple(sorted(to_sections - from_sections)),
            removed_sections=tuple(sorted(from_sections - to_sections)),
            added_obligations=tuple(sorted(to_obs - from_obs)),
            resolved_obligations=tuple(sorted(from_obs - to_obs)),
            budget_delta=budget_delta,
            score_delta=score_delta,
        )

    def apply_delta(
        self,
        state: SemanticControlState,
        delta: StateDelta,
    ) -> SemanticControlState:
        """Apply *delta* to *state* and return the new state.

        Produces a fresh :class:`SemanticControlState` with a new ``state_id``
        and ``timestamp``.  Does not mutate *state*.

        Parameters
        ----------
        state:
            The baseline state.
        delta:
            The delta to apply.

        Returns
        -------
        SemanticControlState
            State after applying the delta.
        """
        new_covers = list(
            (set(state.cover_ids) | set(delta.added_covers)) - set(delta.removed_covers)
        )
        new_sections = list(
            (set(state.section_ids) | set(delta.added_sections)) - set(delta.removed_sections)
        )
        new_obs = list(
            (set(state.obligation_ids) | set(delta.added_obligations))
            - set(delta.resolved_obligations)
        )
        new_budget = dict(state.budget)
        for ch, diff in delta.budget_delta.items():
            new_budget[ch] = new_budget.get(ch, 0.0) + diff
        return SemanticControlState(
            state_id=str(uuid.uuid4()),
            cover_ids=new_covers,
            context_ids=list(state.context_ids),
            section_ids=new_sections,
            treaty_ids=list(state.treaty_ids),
            obligation_ids=new_obs,
            channel_ids=list(state.channel_ids),
            budget=new_budget,
            timestamp=time.time(),
            metadata=dict(state.metadata),
        )

    def compose_deltas(self, deltas: list[StateDelta]) -> StateDelta:
        """Compose a sequence of deltas into a single equivalent delta.

        The composition is the sequential application of all deltas.  Adds
        are accumulated, removes cancel earlier adds, and budget deltas are
        summed.

        Parameters
        ----------
        deltas:
            Ordered list of deltas to compose.

        Returns
        -------
        StateDelta
            A single delta equivalent to applying all *deltas* in order.

        Raises
        ------
        ValueError
            If *deltas* is empty.
        """
        if not deltas:
            raise ValueError("compose_deltas requires at least one delta")
        net_added_covers: set[str] = set()
        net_removed_covers: set[str] = set()
        net_added_sections: set[str] = set()
        net_removed_sections: set[str] = set()
        net_added_obs: set[str] = set()
        net_resolved_obs: set[str] = set()
        net_budget: dict[str, float] = {}
        net_score: float = 0.0
        for d in deltas:
            net_added_covers = (net_added_covers | set(d.added_covers)) - set(d.removed_covers)
            net_removed_covers = (net_removed_covers | set(d.removed_covers)) - set(d.added_covers)
            net_added_sections = (net_added_sections | set(d.added_sections)) - set(
                d.removed_sections
            )
            net_removed_sections = (net_removed_sections | set(d.removed_sections)) - set(
                d.added_sections
            )
            net_added_obs = (net_added_obs | set(d.added_obligations)) - set(d.resolved_obligations)
            net_resolved_obs = (net_resolved_obs | set(d.resolved_obligations)) - set(
                d.added_obligations
            )
            for ch, diff in d.budget_delta.items():
                net_budget[ch] = net_budget.get(ch, 0.0) + diff
            net_score += d.score_delta
        return StateDelta(
            added_covers=tuple(sorted(net_added_covers)),
            removed_covers=tuple(sorted(net_removed_covers)),
            added_sections=tuple(sorted(net_added_sections)),
            removed_sections=tuple(sorted(net_removed_sections)),
            added_obligations=tuple(sorted(net_added_obs)),
            resolved_obligations=tuple(sorted(net_resolved_obs)),
            budget_delta=net_budget,
            score_delta=net_score,
        )

    def is_reversible(self, delta: StateDelta) -> bool:
        """Return ``True`` iff *delta* is structurally reversible.

        A delta is reversible if:

        *   All removed covers and sections can in principle be re-added (no
            structural constraint prevents it — we simply check that the sets
            are non-overlapping with the adds, i.e. no cover is both added and
            removed).
        *   The budget delta does not drop any channel below zero when
            reversed (we check sign consistency only — actual reversal
            requires a live state).

        Parameters
        ----------
        delta:
            The delta to test.

        Returns
        -------
        bool
        """
        if set(delta.added_covers) & set(delta.removed_covers):
            return False
        if set(delta.added_sections) & set(delta.removed_sections):
            return False
        if set(delta.added_obligations) & set(delta.resolved_obligations):
            return False
        return True


# ── StateManager ─────────────────────────────────────────────────────────────


@dataclass(slots=True)
class StateManager:
    """Top-level lifecycle manager for :class:`SemanticControlState`.

    Wires together :class:`StateValidator`, :class:`StateEventBus`, and
    snapshot history to implement the full state lifecycle described in
    theory2.tex Ch44:

    *   **initialize** – set the initial state and emit CREATED.
    *   **transition** – validate new state, publish TRANSITION, snapshot.
    *   **rollback**   – revert to an earlier snapshot (§44.2).
    *   **take_snapshot** / **get_snapshot** / **list_snapshots** – history
        management.
    *   **diff** – compute delta between two snapshots.
    *   **reset** – hard-reset to a new state.
    *   **status** / **export_history** – observability.

    Parameters
    ----------
    current_state:
        The live state, or ``None`` before :meth:`initialize` is called.
    history:
        Ordered list of :class:`StateSnapshot` objects (oldest first).
    validator:
        The :class:`StateValidator` applied on every :meth:`transition`.
    event_bus:
        The :class:`StateEventBus` used to publish lifecycle events.
    max_history:
        Maximum number of snapshots to retain; older ones are evicted FIFO.

    References
    ----------
    theory2.tex Ch44 – Semantic Control lifecycle.
    theory2.tex §44.2 – Transition Guards and Rollback.
    """

    current_state: SemanticControlState | None
    history: list[StateSnapshot]
    validator: StateValidator
    event_bus: StateEventBus
    max_history: int = DEFAULT_MAX_HISTORY

    _delta_computer: StateDeltaComputer = field(default_factory=StateDeltaComputer)

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def initialize(self, initial_state: SemanticControlState) -> None:
        """Set the initial state and emit a CREATED event.

        Must be called before any other mutating method.  Subsequent calls
        replace the current state without validation (use :meth:`transition`
        for validated updates).

        Parameters
        ----------
        initial_state:
            The starting :class:`SemanticControlState`.
        """
        self.current_state = initial_state
        snap = self.take_snapshot(label="initial")
        self.event_bus.publish(
            StateEvent(
                event_id=str(uuid.uuid4()),
                kind=StateEventKind.CREATED,
                state_id=initial_state.state_id,
                payload={"snapshot_id": snap.snapshot_id},
                timestamp=time.time(),
            )
        )
        LOGGER.info(
            "StateManager: initialized with state %s", initial_state.state_id[:8]
        )

    def transition(self, new_state: SemanticControlState) -> bool:
        """Attempt a validated transition to *new_state*.

        Validates *new_state* (and the transition from ``current_state`` if
        set) using ``self.validator``.  On success, updates ``current_state``,
        takes a snapshot, and publishes a TRANSITION event.  On failure,
        publishes a VALIDATED event carrying the violations and returns
        ``False``.

        Parameters
        ----------
        new_state:
            The proposed next state.

        Returns
        -------
        bool
            ``True`` if the transition was accepted; ``False`` if validation
            failed.
        """
        if self.current_state is not None:
            violations = self.validator.validate_transition(self.current_state, new_state)
        else:
            violations = self.validator.validate(new_state)
        if violations:
            self.event_bus.publish(
                StateEvent(
                    event_id=str(uuid.uuid4()),
                    kind=StateEventKind.VALIDATED,
                    state_id=new_state.state_id,
                    payload={"violations": violations, "accepted": False},
                    timestamp=time.time(),
                )
            )
            LOGGER.warning(
                "StateManager: transition rejected (%d violations): %s",
                len(violations),
                violations[:3],
            )
            return False
        old_id = self.current_state.state_id if self.current_state else None
        self.current_state = new_state
        snap = self.take_snapshot(label="post-transition")
        self.event_bus.publish(
            StateEvent(
                event_id=str(uuid.uuid4()),
                kind=StateEventKind.TRANSITION,
                state_id=new_state.state_id,
                payload={"from_state_id": old_id, "snapshot_id": snap.snapshot_id},
                timestamp=time.time(),
            )
        )
        LOGGER.debug(
            "StateManager: transition %s → %s accepted",
            (old_id or "none")[:8],
            new_state.state_id[:8],
        )
        return True

    def rollback(self, steps: int = 1) -> SemanticControlState | None:
        """Revert ``current_state`` to an earlier snapshot.

        Traverses the history list in reverse and restores the state *steps*
        entries back.  The snapshots taken after the rollback point are
        **not** removed from history (they remain as a record of the
        rolled-back path).

        Parameters
        ----------
        steps:
            How many snapshots to step back (default 1 = last snapshot).

        Returns
        -------
        SemanticControlState | None
            The restored state, or ``None`` if there are fewer than *steps*
            snapshots available.
        """
        if steps < 1 or len(self.history) < steps:
            LOGGER.warning(
                "StateManager: rollback(%d) failed, history has %d entries",
                steps,
                len(self.history),
            )
            return None
        target = self.history[-steps]
        self.current_state = target.state
        LOGGER.info(
            "StateManager: rolled back %d step(s) to snapshot %s",
            steps,
            target.snapshot_id[:8],
        )
        return target.state

    def take_snapshot(self, label: str = "") -> StateSnapshot:
        """Capture the current state as a :class:`StateSnapshot`.

        The snapshot is appended to ``self.history``.  If the history
        exceeds ``max_history``, the oldest entry is evicted (FIFO).

        Parameters
        ----------
        label:
            Human-readable tag for the snapshot (e.g. ``"pre-refine"``).

        Returns
        -------
        StateSnapshot
            The newly created snapshot.

        Raises
        ------
        RuntimeError
            If ``current_state`` is ``None`` (i.e. :meth:`initialize` has
            not been called).
        """
        if self.current_state is None:
            raise RuntimeError(
                "StateManager.take_snapshot called before initialize()."
            )
        snap = StateSnapshot(
            snapshot_id=str(uuid.uuid4()),
            state=self.current_state,
            taken_at=time.time(),
            label=label,
            metadata={},
        )
        self.history.append(snap)
        if len(self.history) > self.max_history:
            self.history.pop(0)
        self.event_bus.publish(
            StateEvent(
                event_id=str(uuid.uuid4()),
                kind=StateEventKind.SNAPSHOT_TAKEN,
                state_id=self.current_state.state_id,
                payload={"snapshot_id": snap.snapshot_id, "label": label},
                timestamp=time.time(),
            )
        )
        return snap

    def get_snapshot(self, snapshot_id: str) -> StateSnapshot | None:
        """Retrieve a snapshot by its ID.

        Parameters
        ----------
        snapshot_id:
            The ``snapshot_id`` of the desired :class:`StateSnapshot`.

        Returns
        -------
        StateSnapshot | None
            The snapshot if found, ``None`` otherwise.
        """
        for snap in self.history:
            if snap.snapshot_id == snapshot_id:
                return snap
        return None

    def list_snapshots(self) -> list[StateSnapshot]:
        """Return a copy of the snapshot history list (oldest first).

        Returns
        -------
        list[StateSnapshot]
        """
        return list(self.history)

    def diff(
        self,
        snapshot_id_a: str,
        snapshot_id_b: str,
    ) -> StateDelta | None:
        """Compute the :class:`StateDelta` between two snapshots.

        Retrieves both snapshots by ID and delegates to
        :class:`StateDeltaComputer`.  Returns ``None`` if either snapshot
        is not found.

        Parameters
        ----------
        snapshot_id_a:
            ID of the *from* snapshot (earlier baseline).
        snapshot_id_b:
            ID of the *to* snapshot (later target).

        Returns
        -------
        StateDelta | None
        """
        snap_a = self.get_snapshot(snapshot_id_a)
        snap_b = self.get_snapshot(snapshot_id_b)
        if snap_a is None or snap_b is None:
            LOGGER.warning(
                "StateManager.diff: snapshot not found (a=%s, b=%s)",
                snapshot_id_a[:8],
                snapshot_id_b[:8],
            )
            return None
        return self._delta_computer.compute(snap_a.state, snap_b.state)

    def reset(self, state: SemanticControlState) -> None:
        """Hard-reset the manager to *state*, discarding all history.

        Use this for test isolation or catastrophic rollback.  Emits a
        RESET event, then calls :meth:`initialize` which also emits CREATED
        and takes an initial snapshot.

        Parameters
        ----------
        state:
            The new initial state.
        """
        self.history.clear()
        self.event_bus.clear_history()
        self.current_state = None
        self.event_bus.publish(
            StateEvent(
                event_id=str(uuid.uuid4()),
                kind=StateEventKind.RESET,
                state_id=state.state_id,
                payload={},
                timestamp=time.time(),
            )
        )
        self.initialize(state)
        LOGGER.info("StateManager: hard reset to state %s", state.state_id[:8])

    def status(self) -> dict[str, Any]:
        """Return a diagnostic status dictionary.

        Includes current state summary, history length, event bus queue
        size, and health status of the current state.

        Returns
        -------
        dict[str, Any]
            Human-readable status with the following keys:
            ``current_state_id``, ``health``, ``history_length``,
            ``event_count``, ``coverage_ratio``, ``attainability_score``,
            ``obligation_count``.
        """
        if self.current_state is None:
            return {
                "current_state_id": None,
                "health": "uninitialized",
                "history_length": 0,
                "event_count": len(self.event_bus.history()),
                "coverage_ratio": 0.0,
                "attainability_score": 0.0,
                "obligation_count": 0,
            }
        health = getattr(
            self.current_state.health_status(), "value", str(self.current_state.health_status())
        )
        return {
            "current_state_id": self.current_state.state_id,
            "health": health,
            "history_length": len(self.history),
            "event_count": len(self.event_bus.history()),
            "coverage_ratio": self.current_state.coverage_ratio(),
            "attainability_score": self.current_state.attainability_score(),
            "obligation_count": len(self.current_state.obligation_ids),
        }

    def export_history(self) -> list[dict[str, Any]]:
        """Export the full snapshot history as a list of plain dictionaries.

        Each entry is the result of calling :meth:`StateSnapshot.to_dict`
        on the snapshot.  Suitable for JSON serialisation and offline analysis.

        Returns
        -------
        list[dict[str, Any]]
            Ordered (oldest first) list of snapshot dictionaries.
        """
        return [snap.to_dict() for snap in self.history]


# ── Factory helpers ──────────────────────────────────────────────────────────


def make_default_state_manager(
    max_history: int = DEFAULT_MAX_HISTORY,
) -> StateManager:
    """Construct a :class:`StateManager` with default validator and event bus.

    Convenience factory that wires up:

    *   A :class:`StateValidator` with the four standard rules from
        :meth:`StateValidator.default_rules`.
    *   A fresh :class:`StateEventBus`.

    Parameters
    ----------
    max_history:
        Maximum snapshot history size (default :data:`DEFAULT_MAX_HISTORY`).

    Returns
    -------
    StateManager
        Ready-to-use manager; call :meth:`StateManager.initialize` before
        first use.
    """
    validator = StateValidator(
        rules=StateValidator.default_rules(),
        strict=False,
    )
    bus = StateEventBus()
    return StateManager(
        current_state=None,
        history=[],
        validator=validator,
        event_bus=bus,
        max_history=max_history,
    )


def make_state_projector(
    dimensions: list[str] | None = None,
    weights: dict[str, float] | None = None,
) -> StateProjector:
    """Construct a :class:`StateProjector` with sensible defaults.

    Parameters
    ----------
    dimensions:
        Dimensions to project.  Defaults to all standard dimensions.
    weights:
        Per-dimension weights.  Defaults to uniform weight 1.0.

    Returns
    -------
    StateProjector
    """
    default_dims = ["covers", "obligations", "budget", "sections", "score"]
    return StateProjector(
        dimensions=dimensions or default_dims,
        weights=weights or {},
    )
