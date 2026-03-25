"""
Application state as a presheaf over the component tree.

In the sheaf-theoretic model of a web application, *state* is not merely a
finite set of discrete modes — it is a *presheaf* on the site whose objects
are component tree coordinates.

Key correspondences
-------------------

* **Local section** — a single component's private state (``useState`` in
  React, ``ref``/``reactive`` in Vue, etc.).
* **Global section** — state that is coherent over the entire component tree
  (a Redux store, a Pinia store, a ``Context`` value that descends through
  every subtree without conflict).
* **Restriction map** — prop passing from parent to child; the presheaf
  functor applied to the inclusion morphism ``child ↪ parent``.
* **Lift** — a child signalling a state change back to a parent (callback /
  ``$emit``).  Categorically this is *not* simply the restriction map in
  reverse; it relies on a separately chosen co-section (an "up-morphism").
* **Functorial image / derived state** — computed/derived state is the image
  of the presheaf under a natural transformation, i.e. a functor applied to
  the underlying values.
* **Finite state machine** — a special case of discrete state where the
  state space is a finite category whose morphisms are labelled by events.
  *But FSMs are not the whole story*: scroll position, slider value, and
  animation progress are continuous (``ℝ``-valued) sections and are equally
  first-class.
* **Optimistic updates** — a tentative section that is not yet a global
  section; it becomes one on server confirmation, or is retracted on failure.

This module provides:

1. :class:`StateKind` — taxonomy of state value types.
2. :class:`StateVariable` — a named state variable with metadata.
3. :class:`StateTransition` — a state change viewed as a morphism.
4. :class:`ComponentStatePresheaf` — the presheaf functor on the component tree.
5. :class:`OptimisticStateManager` — tentative/committed state management.
6. :class:`StateMachineMode` — finite-state-machine sub-structure.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from jugeo.geometry.site import Coordinate, CoveringFamily
from jugeo.geometry.descent import (
    LocalSection,
    DescentEngine,
    DescentResult,
    DescentObstruction,
)

__all__ = [
    # Enumerations
    "StateKind",
    # Data types
    "StateVariable",
    "StateTransition",
    "StateMachineMode",
    # Main classes
    "ComponentStatePresheaf",
    "OptimisticStateManager",
]


# ---------------------------------------------------------------------------
# § 1  StateKind
# ---------------------------------------------------------------------------


class StateKind(str, Enum):
    """
    Taxonomy of state value types.

    The distinction between CONTINUOUS and FLOAT is subtle:

    * ``FLOAT`` — a floating-point number that happens to be the stored type,
      but whose *logical* domain may still be discrete (e.g. a rating stored
      as 0.0 / 0.5 / 1.0).
    * ``CONTINUOUS`` — explicitly modelled as a continuously varying quantity
      (scroll offset, animation progress ∈ [0, 1], slider value, zoom level).
      Interpolation and differentiation are meaningful operations on
      ``CONTINUOUS`` sections.

    ``PENDING`` and ``ERROR`` reflect the lifecycle of async state (data
    fetching, form submission).  They are *not* the same as a finite state
    machine with states ``{loading, success, error}``; they are type-level
    annotations on the *value* of a state variable.
    """

    BOOLEAN = "boolean"
    INTEGER = "integer"
    FLOAT = "float"
    STRING = "string"
    ENUM_STATE = "enum_state"
    LIST = "list"
    DICT = "dict"
    SET = "set"
    NULLABLE = "nullable"
    CONTINUOUS = "continuous"
    PENDING = "pending"
    ERROR = "error"


# ---------------------------------------------------------------------------
# § 2  StateVariable
# ---------------------------------------------------------------------------


@dataclass
class StateVariable:
    """
    A named, typed state variable living at a component coordinate.

    A ``StateVariable`` is a *section generator*: it describes what kind of
    local section a component contributes to the overall state presheaf.

    Attributes
    ----------
    name:
        Unique identifier within the component's state namespace.
    kind:
        The :class:`StateKind` of the variable's values.
    initial_value:
        The value this variable holds when the component mounts (or when no
        persisted value is available).
    is_local:
        ``True`` if this variable is private to the component; ``False`` if it
        is "global" (e.g. stored in an external store and shared across the
        tree).
    is_derived:
        ``True`` if the value is *computed* from one or more base variables
        rather than held independently.  Derived state is the *functorial
        image* of the base variables under a natural transformation.
    derives_from:
        The names of the base state variables that this derived variable
        depends on.  Empty for non-derived variables.
    persistence:
        Where the value survives across component unmounts / page reloads.

        ``"memory"``
            Lost when the component unmounts (default React / Vue behaviour).
        ``"localStorage"``
            Persisted to ``window.localStorage``; survives page reloads and
            browser restarts.
        ``"sessionStorage"``
            Persisted to ``window.sessionStorage``; survives page reloads
            within the same tab session.
        ``"url"``
            Encoded in the URL query string or hash; shareable and
            bookmark-able.
        ``"server"``
            Authoritative copy lives on the server; local value is a cached
            replica (subject to optimistic updates).
    """

    name: str
    kind: StateKind
    initial_value: Any
    is_local: bool = True
    is_derived: bool = False
    derives_from: list[str] = field(default_factory=list)
    persistence: str = "memory"

    # ------------------------------------------------------------------
    # Continuous-state predicate
    # ------------------------------------------------------------------

    def is_continuous(self) -> bool:
        """
        Return ``True`` iff this variable represents a continuously varying
        quantity.

        Both ``FLOAT`` and ``CONTINUOUS`` qualify.  The distinction is
        conceptual: ``CONTINUOUS`` signals that interpolation and rate-of-
        change tracking are *semantically meaningful*, while ``FLOAT`` may
        simply be a numeric type whose values are effectively discrete.
        """
        return self.kind in (StateKind.FLOAT, StateKind.CONTINUOUS)

    # ------------------------------------------------------------------
    # Presheaf section construction
    # ------------------------------------------------------------------

    def to_local_section(self, component_coord: str) -> LocalSection:
        """
        Reify this variable as a :class:`~jugeo.geometry.descent.LocalSection`
        anchored at *component_coord*.

        The ``judgment_data`` carries the variable's metadata so that the
        descent engine can reason about compatibility when attempting to glue
        local sections into a global one.

        Parameters
        ----------
        component_coord:
            The string key of the component in the presheaf's index (e.g.
            ``"app/sidebar/filter-panel"``).

        Returns
        -------
        LocalSection
            A local section whose coordinate is *component_coord* and whose
            judgment data encodes this variable's name, kind, and initial
            value.
        """
        return LocalSection(
            coordinate=component_coord,
            judgment_data={
                "var_name": self.name,
                "kind": self.kind.value,
                "initial_value": self.initial_value,
                "is_local": self.is_local,
                "is_derived": self.is_derived,
                "derives_from": list(self.derives_from),
                "persistence": self.persistence,
            },
            trust_level=1.0,
            is_partial=self.is_derived,
        )


# ---------------------------------------------------------------------------
# § 3  StateTransition
# ---------------------------------------------------------------------------


@dataclass
class StateTransition:
    """
    A state change viewed as a *morphism* in the state category.

    Every state update in a reactive application is a transition from one
    assignment of values to the state variables to another.  Treating
    transitions as morphisms lets us compose them (sequential updates),
    invert them (undo), and detect which parts of the presheaf they affect.

    Attributes
    ----------
    transition_id:
        Unique identifier for this transition instance (useful for logging,
        time-travel debugging, and undo stacks).
    from_state:
        A snapshot of the relevant state variables *before* the transition.
        Keys are variable names; values are their previous values.
    to_state:
        The state *after* the transition.
    trigger:
        The name of the event or action that caused this transition (e.g.
        ``"click"``, ``"input"``, ``"fetch_complete"``, ``"timeout"``).
    is_atomic:
        ``True`` (default) if all variable changes in this transition are
        applied as a single indivisible unit.  ``False`` for multi-step
        transitions that may be observed in intermediate states.
    """

    transition_id: str
    from_state: dict[str, Any]
    to_state: dict[str, Any]
    trigger: str
    is_atomic: bool = True

    # ------------------------------------------------------------------
    # Morphism analysis
    # ------------------------------------------------------------------

    def changed_variables(self) -> list[str]:
        """
        Return the names of variables that differ between ``from_state`` and
        ``to_state``.

        Only keys present in *both* snapshots are compared; keys that appear
        in only one snapshot are also included (they represent additions or
        removals of variables).
        """
        all_keys = set(self.from_state) | set(self.to_state)
        return [
            k
            for k in all_keys
            if self.from_state.get(k) != self.to_state.get(k)
        ]

    def is_reversible(self) -> bool:
        """
        Return ``True`` if we can compute the inverse morphism.

        A transition is reversible when both ``from_state`` and ``to_state``
        record every changed variable — i.e. we have enough information to
        reconstruct the pre-transition state.  In practice this means the
        two snapshots together form a bijection on the affected variables.

        This does *not* guarantee that the inverse is semantically meaningful
        (e.g. re-triggering a ``fetch_complete`` is not the same as
        un-fetching), but it does mean the *data* required to reverse the
        transition is present.
        """
        changed = self.changed_variables()
        return all(k in self.from_state for k in changed)


# ---------------------------------------------------------------------------
# § 4  ComponentStatePresheaf
# ---------------------------------------------------------------------------


@dataclass
class ComponentStatePresheaf:
    """
    The state presheaf on the component tree.

    Formally, let *C* be the category whose objects are component-tree
    coordinates and whose morphisms are the parent–child inclusion maps.
    The state presheaf ``𝓕 : Cᵒᵖ → Set`` assigns to each component
    coordinate the set of local state variable assignments, and to each
    inclusion ``child ↪ parent`` the *restriction map* (prop passing).

    This class provides a concrete, mutable implementation of that functor.

    Structure
    ---------
    ``_state``
        Maps each component coordinate to a dict of :class:`StateVariable`
        objects, indexed by variable name.  This is the *type-level* part of
        the presheaf.
    ``_values``
        Maps each component coordinate to a dict of current values, indexed
        by variable name.  This is the *value-level* part (the actual section
        data).
    """

    _state: dict[str, dict[str, StateVariable]] = field(default_factory=dict)
    _values: dict[str, dict[str, Any]] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Schema management
    # ------------------------------------------------------------------

    def add_state_var(self, component_coord: str, var: StateVariable) -> None:
        """
        Register *var* as a state variable belonging to *component_coord*.

        If this is the first variable at *component_coord*, the coordinate is
        initialised in both ``_state`` and ``_values``.  The variable's
        ``initial_value`` is stored as the current value unless a value is
        already present (allowing re-registration without clobbering live
        state).

        Parameters
        ----------
        component_coord:
            String key identifying the component (e.g.
            ``"app/modal/confirm-button"``).
        var:
            The state variable descriptor to register.
        """
        if component_coord not in self._state:
            self._state[component_coord] = {}
            self._values[component_coord] = {}
        self._state[component_coord][var.name] = var
        if var.name not in self._values[component_coord]:
            self._values[component_coord][var.name] = var.initial_value

    # ------------------------------------------------------------------
    # Value accessors
    # ------------------------------------------------------------------

    def get_value(self, component_coord: str, var_name: str) -> Any | None:
        """
        Return the current value of *var_name* at *component_coord*, or
        ``None`` if either the coordinate or the variable is unknown.
        """
        return self._values.get(component_coord, {}).get(var_name)

    def set_value(self, component_coord: str, var_name: str, value: Any) -> None:
        """
        Update the current value of *var_name* at *component_coord*.

        If *component_coord* does not yet exist in the presheaf, it is created
        on the fly (without a registered :class:`StateVariable` descriptor).
        This mirrors JavaScript's permissive object semantics — components can
        hold ad-hoc state before their schema is formally declared.
        """
        if component_coord not in self._values:
            self._values[component_coord] = {}
        self._values[component_coord][var_name] = value

    # ------------------------------------------------------------------
    # Presheaf morphisms
    # ------------------------------------------------------------------

    def restrict(
        self,
        parent_coord: str,
        child_coord: str,
        var_names: list[str],
    ) -> dict[str, Any]:
        """
        Apply the restriction map: child inherits specified parent state.

        This is the presheaf functor applied to the inclusion morphism
        ``child_coord ↪ parent_coord``.  In UI terms this is *prop passing*:
        the parent's current values for the listed variables are made
        available at the child coordinate.

        The method also *writes* the inherited values into the child's value
        dict so that subsequent ``get_value`` calls at the child coordinate
        reflect the restricted section.

        Parameters
        ----------
        parent_coord:
            The coordinate of the parent component (the source of the values).
        child_coord:
            The coordinate of the child component (the target of the restriction).
        var_names:
            The names of the variables to restrict (pass down).

        Returns
        -------
        dict[str, Any]
            A mapping ``{var_name: value}`` of the restricted values, keyed as
            they appear at the child.
        """
        restricted: dict[str, Any] = {}
        parent_vals = self._values.get(parent_coord, {})
        for name in var_names:
            if name in parent_vals:
                restricted[name] = parent_vals[name]
                self.set_value(child_coord, name, parent_vals[name])
        return restricted

    def lift(
        self,
        child_coord: str,
        parent_coord: str,
        var_name: str,
        value: Any,
    ) -> None:
        """
        Propagate a state change from child to parent (callback / ``$emit``).

        This is the *co-section* direction: a child signals that a value
        should be updated at the parent coordinate.  Categorically this is not
        the inverse of the restriction map — it depends on the parent having
        registered a mutable handler for *var_name* (e.g. a ``setState``
        callback passed as a prop).

        In this implementation we unconditionally write the new *value* at
        *parent_coord*.  Higher-level frameworks may wrap this with validation
        or middleware.

        Parameters
        ----------
        child_coord:
            The component initiating the state change.
        parent_coord:
            The component whose state should be updated.
        var_name:
            The name of the variable to update at the parent.
        value:
            The new value to set.
        """
        self.set_value(parent_coord, var_name, value)

    # ------------------------------------------------------------------
    # Derived / computed state
    # ------------------------------------------------------------------

    def derived_value(
        self,
        component_coord: str,
        var: StateVariable,
    ) -> Any | None:
        """
        Recompute the inputs for a derived state variable.

        Derived state is the *functorial image* of the base variables under a
        natural transformation.  This method gathers the current values of
        every variable listed in ``var.derives_from`` and returns them as a
        dict — the raw material that an externally supplied derivation function
        must transform into the final derived value.

        The split between *data gathering* (this method) and *transformation*
        (external) is intentional: the presheaf knows topology and current
        values; it does not know domain-specific computation rules.

        Parameters
        ----------
        component_coord:
            The coordinate at which the derived variable lives.
        var:
            A :class:`StateVariable` with ``is_derived=True``.

        Returns
        -------
        dict[str, Any] | None
            A mapping ``{base_var_name: current_value}`` for each name in
            ``var.derives_from``.  Returns ``None`` if *var* is not derived or
            has no declared dependencies.
        """
        if not var.is_derived or not var.derives_from:
            return None
        component_vals = self._values.get(component_coord, {})
        return {name: component_vals.get(name) for name in var.derives_from}


# ---------------------------------------------------------------------------
# § 5  OptimisticStateManager
# ---------------------------------------------------------------------------


@dataclass
class OptimisticStateManager:
    """
    Manage tentative (optimistic) state alongside confirmed (committed) state.

    Optimistic UI updates are a common pattern for perceived performance: the
    UI immediately reflects a user action (optimistic update) while waiting
    for server confirmation.  If the server confirms, the optimistic value is
    *committed*.  If the server rejects, the optimistic value is *rolled back*
    to the last confirmed value.

    In sheaf-theoretic terms, an optimistic update is a *tentative section*
    that has not yet been verified to satisfy the descent conditions.  The
    server confirmation is the proof that the section is compatible with the
    global data.

    Attributes
    ----------
    _committed:
        The last server-confirmed state.  This is the ground truth.
    _optimistic:
        The current local (tentative) state.  May diverge from ``_committed``
        while operations are in-flight.
    _pending_ops:
        Ordered list of ``(operation_id, key)`` pairs tracking which
        operations are still awaiting server confirmation.
    """

    _committed: dict[str, Any] = field(default_factory=dict)
    _optimistic: dict[str, Any] = field(default_factory=dict)
    _pending_ops: list[tuple[str, Any]] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def apply_optimistic(self, op_id: str, key: str, value: Any) -> None:
        """
        Apply an optimistic update locally and record the operation as pending.

        The *key* is immediately set to *value* in ``_optimistic``.  The pair
        ``(op_id, key)`` is appended to ``_pending_ops`` so that
        :meth:`commit` and :meth:`rollback` can identify which key to
        finalise.

        Parameters
        ----------
        op_id:
            A unique identifier for the operation (e.g. a UUID or a request
            ID returned by the API client).
        key:
            The state key being updated.
        value:
            The optimistic (tentative) value.
        """
        self._optimistic[key] = value
        self._pending_ops.append((op_id, key))

    def commit(self, op_id: str) -> None:
        """
        Confirm that the server accepted the operation identified by *op_id*.

        Moves the optimistic value for the associated key into ``_committed``.
        The pending record for *op_id* is removed.

        If *op_id* is not in ``_pending_ops`` (e.g. already committed or never
        registered), this is a no-op.
        """
        for i, (oid, key) in enumerate(self._pending_ops):
            if oid == op_id:
                committed_value = self._optimistic.get(key)
                if committed_value is not None or key in self._optimistic:
                    self._committed[key] = committed_value
                self._pending_ops.pop(i)
                return

    def rollback(self, op_id: str) -> None:
        """
        Revert the optimistic update for *op_id* to the last committed value.

        The pending record for *op_id* is removed.  The optimistic value for
        the associated key is reset to the corresponding ``_committed`` value
        (or removed from ``_optimistic`` if the key has no committed value —
        meaning it was new and should disappear on rollback).

        If *op_id* is not in ``_pending_ops``, this is a no-op.
        """
        for i, (oid, key) in enumerate(self._pending_ops):
            if oid == op_id:
                if key in self._committed:
                    self._optimistic[key] = self._committed[key]
                else:
                    self._optimistic.pop(key, None)
                self._pending_ops.pop(i)
                return

    # ------------------------------------------------------------------
    # Value accessors
    # ------------------------------------------------------------------

    def current_value(self, key: str) -> Any:
        """
        Return the most up-to-date value for *key*.

        Returns the *optimistic* value if the key has a pending operation;
        otherwise returns the *committed* value; otherwise returns ``None``.
        """
        pending_keys = {k for _, k in self._pending_ops}
        if key in pending_keys and key in self._optimistic:
            return self._optimistic[key]
        return self._committed.get(key)

    def has_conflict(self) -> bool:
        """
        Return ``True`` if the committed and optimistic states diverge for any
        key that appears in both.

        A conflict indicates that the server has returned a different value
        than the optimistic prediction — typically after a failed rollback
        that was re-applied, or a concurrent update from another client.
        """
        for key in self._committed:
            if key in self._optimistic and self._optimistic[key] != self._committed[key]:
                return True
        return False


# ---------------------------------------------------------------------------
# § 6  StateMachineMode
# ---------------------------------------------------------------------------


@dataclass
class StateMachineMode:
    """
    A finite state machine sub-structure for behaviours with discrete modes.

    While the full application state presheaf supports continuous and
    unbounded value spaces, many UI sub-behaviours *are* naturally finite
    state machines:

    * Modal dialogs: ``{closed, opening, open, closing}``
    * Wizard steps: ``{step1, step2, step3, summary, submitted}``
    * Network request lifecycle: ``{idle, pending, success, error}``
    * Media player: ``{stopped, buffering, playing, paused}``

    This class models such sub-structures without forcing the rest of the
    state presheaf to be FSM-shaped.

    Attributes
    ----------
    mode_name:
        Human-readable name for this machine (e.g. ``"modal_state"``).
    states:
        Exhaustive list of states in this machine.
    transitions:
        Maps each state to a list of ``(event_name, next_state)`` pairs.
        Multiple entries for the same event from the same state represent
        *non-determinism* (e.g. guarded transitions).
    initial_state:
        The state the machine starts in.
    current_state:
        The state the machine is currently in.
    """

    mode_name: str
    states: list[str]
    transitions: dict[str, list[tuple[str, str]]]
    initial_state: str
    current_state: str

    # ------------------------------------------------------------------
    # Transition execution
    # ------------------------------------------------------------------

    def transition(self, event: str) -> str | None:
        """
        Apply *event* to ``current_state`` and advance the machine.

        If a transition ``(event, next_state)`` exists from ``current_state``
        on *event*, ``current_state`` is updated to ``next_state`` and
        ``next_state`` is returned.

        If the machine is non-deterministic and multiple transitions match
        *event* from the current state, the *first* matching transition is
        taken (deterministic preference order).

        Returns ``None`` if no transition is defined for *event* from
        ``current_state`` (the event is silently ignored, consistent with the
        Mealy/Moore machine convention for undefined inputs).

        Parameters
        ----------
        event:
            The event label to dispatch.

        Returns
        -------
        str | None
            The new ``current_state``, or ``None`` if the event caused no
            transition.
        """
        for ev, next_state in self.transitions.get(self.current_state, []):
            if ev == event:
                self.current_state = next_state
                return next_state
        return None

    # ------------------------------------------------------------------
    # Reachability
    # ------------------------------------------------------------------

    def reachable_states(self) -> set[str]:
        """
        Return the set of states reachable from ``initial_state`` via BFS.

        This is the *forward-reachable* sub-machine: states that can never be
        reached from the initial state are dead code and may indicate a
        modelling error.
        """
        visited: set[str] = set()
        queue: deque[str] = deque([self.initial_state])
        while queue:
            state = queue.popleft()
            if state in visited:
                continue
            visited.add(state)
            for _event, next_state in self.transitions.get(state, []):
                if next_state not in visited:
                    queue.append(next_state)
        return visited

    # ------------------------------------------------------------------
    # Determinism check
    # ------------------------------------------------------------------

    def is_deterministic(self) -> bool:
        """
        Return ``True`` if no two transitions from the same state share the
        same event label.

        A deterministic FSM has at most one outgoing transition per
        ``(state, event)`` pair.  Non-determinism (multiple transitions for
        the same event) requires an explicit disambiguation strategy (guards,
        priorities, or external choice).
        """
        for state, arcs in self.transitions.items():
            seen_events: set[str] = set()
            for event, _next in arcs:
                if event in seen_events:
                    return False
                seen_events.add(event)
        return True
