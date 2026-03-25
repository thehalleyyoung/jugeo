"""
Component lifecycle: initialization, updates, cleanup, suspense, concurrent
rendering, and hydration — viewed through the lens of sheaf theory.

A component's lifecycle is a *functor* from the category of time-phases
(:class:`LifecyclePhase`) to the category of UI states.  The morphisms in
this functor category are the *phase transitions* (mount → update → unmount).

Key descent-theoretic interpretations:

* **Initialization** — establishing a local section over the "birth" coordinate.
* **Update** — extending the section to new coordinates (new props/state).
* **Cleanup** — retracting the section cleanly (removing side-effects).
* **Suspense** — a *partial section* that is pending completion.
* **Concurrent rendering** — multiple candidate sections in-flight simultaneously.
* **Hydration** — reconciling a server-rendered section with a client-rendered one.

This module provides:

1. :class:`LifecyclePhase` — taxonomy of lifecycle phases.
2. :class:`ComponentCoordinate` — a component instance as a site coordinate.
3. :class:`LifecycleSite` — the lifecycle of a component as a site.
4. :class:`CleanupTheory` — resource cleanup as section retraction.
5. :class:`SuspenseTheory` — suspense as partial section waiting for data.
6. :class:`ConcurrentRenderingTheory` — concurrent rendering as competing sections.
7. :class:`HydrationTheory` — SSR + hydration as section reconciliation.
8. :class:`LifecycleDescentChecker` — full coherence verification via descent.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from jugeo.geometry.site import (
    Site,
    Coordinate,
    CoordinateKind,
    Morphism,
    MorphismKind,
    CoveringFamily,
    GrothendieckTopology,
)
from jugeo.geometry.descent import (
    DescentEngine,
    GlobalSection,
    LocalSection,
    DescentResult,
    DescentObstruction,
)


__all__ = [
    # Enumerations
    "LifecyclePhase",
    "UpdateTrigger",
    "ResourceKind",
    "SuspenseState",
    "RenderPriority",
    "HydrationStatus",
    # Data types
    "ComponentCoordinate",
    "CleanupItem",
    "EffectDescriptor",
    "HydrationCheckResult",
    # Main classes
    "LifecycleSite",
    "CleanupTheory",
    "SuspenseTheory",
    "ConcurrentRenderingTheory",
    "HydrationTheory",
    "LifecycleDescentChecker",
]


# ---------------------------------------------------------------------------
# § 1  LifecyclePhase
# ---------------------------------------------------------------------------


class LifecyclePhase(str, Enum):
    """
    Ordered phases in a component's lifetime.

    The phase graph is a *directed category*: each phase is an object, and
    each allowed transition is a morphism.  Valid transitions are:

    ::

        BEFORE_MOUNT → MOUNTING → MOUNTED
        MOUNTED → BEFORE_UPDATE → UPDATING → UPDATED → MOUNTED  (loop)
        MOUNTED → BEFORE_UNMOUNT → UNMOUNTED
        MOUNTING / MOUNTED / UPDATING → SUSPENDED → MOUNTING / MOUNTED
        MOUNTING / MOUNTED / UPDATING → ERROR
        UNMOUNTED → MOUNTING  (remount after unmount — possible in concurrent mode)
        MOUNTING → HYDRATING → HYDRATED → MOUNTED  (SSR path)

    The ERROR and UNMOUNTED phases are *terminal* in the default lifecycle.
    Only error boundaries can recover from ERROR.
    """

    BEFORE_MOUNT = "before_mount"
    """Component function called for the first time; DOM does not yet exist."""

    MOUNTING = "mounting"
    """Component is being inserted into the DOM."""

    MOUNTED = "mounted"
    """Component is live in the DOM; effects have run."""

    BEFORE_UPDATE = "before_update"
    """An update trigger has fired; render not yet re-run."""

    UPDATING = "updating"
    """Component is re-rendering in response to an update trigger."""

    UPDATED = "updated"
    """Re-render complete; update effects have run."""

    BEFORE_UNMOUNT = "before_unmount"
    """Component is about to be removed from the DOM."""

    UNMOUNTED = "unmounted"
    """Component has been removed; cleanup has run."""

    ERROR = "error"
    """An uncaught error occurred during render or in an effect."""

    SUSPENDED = "suspended"
    """Component threw a Promise during render; waiting for it to resolve."""

    HYDRATING = "hydrating"
    """Client is attaching event handlers to SSR-generated HTML."""

    HYDRATED = "hydrated"
    """Hydration complete; client has taken over from the SSR HTML."""

    # ------------------------------------------------------------------
    # Classification helpers
    # ------------------------------------------------------------------

    def is_live(self) -> bool:
        """True for phases where the component is rendered and interactive."""
        return self in (
            LifecyclePhase.MOUNTED,
            LifecyclePhase.BEFORE_UPDATE,
            LifecyclePhase.UPDATING,
            LifecyclePhase.UPDATED,
            LifecyclePhase.HYDRATED,
        )

    def is_transitional(self) -> bool:
        """True for phases that are momentary transitions (not stable states)."""
        return self in (
            LifecyclePhase.BEFORE_MOUNT,
            LifecyclePhase.MOUNTING,
            LifecyclePhase.BEFORE_UPDATE,
            LifecyclePhase.UPDATING,
            LifecyclePhase.BEFORE_UNMOUNT,
            LifecyclePhase.HYDRATING,
        )

    def is_terminal(self) -> bool:
        """True for phases from which no further transitions occur by default."""
        return self in (LifecyclePhase.UNMOUNTED, LifecyclePhase.ERROR)

    def can_have_effects(self) -> bool:
        """True for phases where side-effects (useEffect) may fire."""
        return self in (
            LifecyclePhase.MOUNTED,
            LifecyclePhase.UPDATED,
            LifecyclePhase.HYDRATED,
        )

    def valid_successors(self) -> list[LifecyclePhase]:
        """Return the list of phases that can legally follow this one."""
        _transitions: dict[LifecyclePhase, list[LifecyclePhase]] = {
            LifecyclePhase.BEFORE_MOUNT: [LifecyclePhase.MOUNTING, LifecyclePhase.ERROR],
            LifecyclePhase.MOUNTING: [
                LifecyclePhase.MOUNTED,
                LifecyclePhase.HYDRATING,
                LifecyclePhase.SUSPENDED,
                LifecyclePhase.ERROR,
            ],
            LifecyclePhase.MOUNTED: [
                LifecyclePhase.BEFORE_UPDATE,
                LifecyclePhase.BEFORE_UNMOUNT,
                LifecyclePhase.SUSPENDED,
                LifecyclePhase.ERROR,
            ],
            LifecyclePhase.BEFORE_UPDATE: [LifecyclePhase.UPDATING, LifecyclePhase.ERROR],
            LifecyclePhase.UPDATING: [
                LifecyclePhase.UPDATED,
                LifecyclePhase.SUSPENDED,
                LifecyclePhase.ERROR,
            ],
            LifecyclePhase.UPDATED: [
                LifecyclePhase.MOUNTED,
                LifecyclePhase.BEFORE_UPDATE,
                LifecyclePhase.BEFORE_UNMOUNT,
                LifecyclePhase.SUSPENDED,
                LifecyclePhase.ERROR,
            ],
            LifecyclePhase.BEFORE_UNMOUNT: [LifecyclePhase.UNMOUNTED, LifecyclePhase.ERROR],
            LifecyclePhase.UNMOUNTED: [LifecyclePhase.BEFORE_MOUNT],  # remount
            LifecyclePhase.ERROR: [],  # terminal — error boundary handles recovery
            LifecyclePhase.SUSPENDED: [
                LifecyclePhase.MOUNTING,
                LifecyclePhase.MOUNTED,
                LifecyclePhase.ERROR,
            ],
            LifecyclePhase.HYDRATING: [
                LifecyclePhase.HYDRATED,
                LifecyclePhase.ERROR,
            ],
            LifecyclePhase.HYDRATED: [
                LifecyclePhase.MOUNTED,
                LifecyclePhase.BEFORE_UPDATE,
                LifecyclePhase.ERROR,
            ],
        }
        return _transitions.get(self, [])

    def can_transition_to(self, target: LifecyclePhase) -> bool:
        """Return True if transitioning from this phase to *target* is valid."""
        return target in self.valid_successors()


# ---------------------------------------------------------------------------
# § 1b  Supporting enumerations
# ---------------------------------------------------------------------------


class UpdateTrigger(str, Enum):
    """What caused a component to re-render."""

    STATE_CHANGE = "state_change"
    """Internal state was updated (e.g. ``setState`` / ``useState`` setter)."""

    PROP_CHANGE = "prop_change"
    """Parent passed different props on this render."""

    PARENT_RERENDER = "parent_rerender"
    """Parent re-rendered; component re-rendered as a consequence."""

    CONTEXT_CHANGE = "context_change"
    """A consumed context value changed."""

    FORCED_UPDATE = "forced_update"
    """Explicit ``forceUpdate()`` call (class components) or external trigger."""

    CONCURRENT_INTERRUPT = "concurrent_interrupt"
    """Previous render was interrupted by a higher-priority update."""

    HYDRATION_RECONCILE = "hydration_reconcile"
    """Mismatch detected during hydration forced a re-render."""


class ResourceKind(str, Enum):
    """
    Kinds of resources that must be cleaned up when a component unmounts.

    Each kind corresponds to a *section* over the component's lifetime
    coordinate.  At unmount, all sections must be *retracted*: the resource
    is released and the section is removed from the presheaf.
    """

    EVENT_LISTENER = "event_listener"
    """DOM event listener registered with ``addEventListener``."""

    TIMER = "timer"
    """``setTimeout`` or ``setInterval`` handle."""

    SUBSCRIPTION = "subscription"
    """Observable or store subscription."""

    ABORT_CONTROLLER = "abort_controller"
    """``AbortController`` for in-flight ``fetch`` requests."""

    ANIMATION_FRAME = "animation_frame"
    """``requestAnimationFrame`` handle."""

    INTERSECTION_OBSERVER = "intersection_observer"
    """``IntersectionObserver`` instance."""

    RESIZE_OBSERVER = "resize_observer"
    """``ResizeObserver`` instance."""

    MUTATION_OBSERVER = "mutation_observer"
    """``MutationObserver`` instance."""

    WEB_SOCKET = "web_socket"
    """WebSocket connection that must be closed."""

    CUSTOM = "custom"
    """Any other resource with a custom cleanup function."""


class RenderPriority(str, Enum):
    """Priority levels for concurrent rendering scheduler."""

    IMMEDIATE = "immediate"
    """Synchronous — must run before the browser paints."""

    USER_BLOCKING = "user_blocking"
    """Must complete within ~100 ms to feel responsive to the user."""

    NORMAL = "normal"
    """Default priority — can be deferred slightly."""

    LOW = "low"
    """Background work; can be interrupted by higher-priority updates."""

    IDLE = "idle"
    """Run only when the browser is idle (off-screen / invisible)."""


class HydrationStatus(str, Enum):
    """Status of a component's hydration."""

    NOT_STARTED = "not_started"
    HYDRATING = "hydrating"
    HYDRATED = "hydrated"
    MISMATCH = "mismatch"
    """Server HTML and client render diverged — full client re-render required."""
    FAILED = "failed"
    """Hydration encountered an unrecoverable error."""


# ---------------------------------------------------------------------------
# § 2  ComponentCoordinate
# ---------------------------------------------------------------------------


@dataclass
class ComponentCoordinate:
    """
    A component instance modelled as a coordinate in the lifecycle site.

    In the site-theoretic view, each *component instance* is a coordinate.
    Time (lifecycle phase) provides the "height" axis, and component identity
    provides the "width" axis.  The lifecycle site is the product:

    ``component_identity × lifecycle_phase → component_state``

    Morphisms between coordinates are:

    * **Phase transitions**: same component, different phase (time-direction).
    * **Parent→child**: parent coordinate covers child (tree direction).
    * **Before→after update**: same component, same phase, updated props/state.

    Parameters
    ----------
    component_id:
        Unique identifier for this component instance (e.g. a React fiber key).
    component_type:
        The component class/function name (e.g. ``"UserProfile"``).
    props_shape:
        Dict describing the prop names and their types (not the values).
    state_shape:
        Dict describing the state keys and their types.
    children_ids:
        Identifiers of child component instances.
    parent_id:
        Identifier of the parent component instance, if any.
    mount_timestamp:
        Logical clock value at mount time (not wall-clock).
    update_count:
        Number of times this instance has re-rendered.
    current_phase:
        Current lifecycle phase.
    """

    component_id: str
    component_type: str
    props_shape: dict[str, str] = field(default_factory=dict)
    state_shape: dict[str, str] = field(default_factory=dict)
    children_ids: list[str] = field(default_factory=list)
    parent_id: str | None = None
    mount_timestamp: int = 0
    update_count: int = 0
    current_phase: LifecyclePhase = LifecyclePhase.BEFORE_MOUNT

    def to_coordinate(self) -> Coordinate:
        """Convert this instance to a JuGeo :class:`~jugeo.geometry.site.Coordinate`."""
        return Coordinate(
            components=("component", self.component_type, self.component_id),
            kind=CoordinateKind.MODULE,
            metadata={
                "component_type": self.component_type,
                "phase": self.current_phase.value,
                "update_count": self.update_count,
                "has_parent": self.parent_id is not None,
                "child_count": len(self.children_ids),
            },
        )

    def phase_coordinate(self) -> Coordinate:
        """Return the coordinate for this component at its current phase."""
        return Coordinate(
            components=(
                "component",
                self.component_type,
                self.component_id,
                self.current_phase.value,
            ),
            kind=CoordinateKind.REGION,
            metadata={
                "phase": self.current_phase.value,
                "update_count": self.update_count,
            },
        )

    def transition_to(self, next_phase: LifecyclePhase) -> bool:
        """
        Attempt to transition to *next_phase*.

        Returns True if the transition is valid; False otherwise (the phase
        is not updated if the transition is invalid).
        """
        if self.current_phase.can_transition_to(next_phase):
            if next_phase in (LifecyclePhase.UPDATED, LifecyclePhase.BEFORE_UPDATE):
                self.update_count += 1
            self.current_phase = next_phase
            return True
        return False

    def is_alive(self) -> bool:
        """Return True while the component is mounted or in transition."""
        return not self.current_phase.is_terminal()

    def to_dict(self) -> dict[str, Any]:
        return {
            "component_id": self.component_id,
            "component_type": self.component_type,
            "props_shape": dict(self.props_shape),
            "state_shape": dict(self.state_shape),
            "children_ids": list(self.children_ids),
            "parent_id": self.parent_id,
            "mount_timestamp": self.mount_timestamp,
            "update_count": self.update_count,
            "current_phase": self.current_phase.value,
        }


# ---------------------------------------------------------------------------
# § 3  LifecycleSite
# ---------------------------------------------------------------------------


@dataclass
class EffectDescriptor:
    """
    Descriptor for a ``useEffect``-style side-effect registration.

    Each effect is a *local section* over the "mounted" coordinate.  The
    cleanup function is the section's *retraction*: it must be called when
    the effect's scope ends.

    Parameters
    ----------
    effect_id:
        Unique identifier for this effect registration.
    dependencies:
        List of dependency identifiers (empty = run every render;
        ``None`` = run once on mount only).
    description:
        Human-readable description of what this effect does.
    cleanup_description:
        Human-readable description of what the cleanup function does.
    has_cleanup:
        True if the effect returns a cleanup function.
    fires_on:
        Which lifecycle phases trigger this effect.
    """

    effect_id: str = field(default_factory=lambda: f"effect_{uuid.uuid4().hex[:8]}")
    dependencies: list[str] | None = None
    description: str = ""
    cleanup_description: str = ""
    has_cleanup: bool = False
    fires_on: list[LifecyclePhase] = field(default_factory=list)

    def runs_every_render(self) -> bool:
        """Return True if this effect runs after every render."""
        return self.dependencies is not None and len(self.dependencies) == 0

    def runs_once(self) -> bool:
        """Return True if this effect runs only on mount."""
        return self.dependencies is None

    def has_correct_dependencies(self, known_deps: set[str]) -> bool:
        """
        Return True if the declared dependencies are a subset of *known_deps*.

        An effect that uses values not in its dependency array is a bug
        (stale closure).
        """
        if self.dependencies is None:
            return True  # mount-only effects have no deps to check
        return set(self.dependencies).issubset(known_deps)


class LifecycleSite:
    """
    A component's lifecycle modelled as a JuGeo site.

    **Site structure**

    The lifecycle site :math:`\\mathcal{L}` has:

    * **Objects** — coordinates for each ``(component_instance, phase)`` pair.
    * **Morphisms** — phase-transition arrows (the time-direction functor), plus
      parent→child inclusion morphisms (the tree-direction functor).
    * **Topology** — each mount phase is covered by its initialization actions;
      each update by its trigger sources; each unmount by its cleanup items.

    **Sections**

    A *section* over a lifecycle coordinate is a consistent assignment of
    component state.  The descent condition for the lifecycle is:

    * The component's state at ``UPDATED`` must be consistent with its state
      at ``MOUNTED`` (no phantom state updates after unmount).
    * Cleanup at ``BEFORE_UNMOUNT`` must retract all sections registered
      during ``MOUNTED`` (no memory leaks).

    Parameters
    ----------
    component:
        The component instance this site describes.
    clock:
        Logical clock (monotonically increasing integer) for ordering events.
    """

    def __init__(
        self,
        component: ComponentCoordinate,
        clock: int = 0,
    ) -> None:
        self.component = component
        self.clock = clock
        self._effects: list[EffectDescriptor] = []
        self._update_triggers: list[dict[str, Any]] = []
        self._phase_log: list[dict[str, Any]] = []

        # Build the underlying JuGeo site
        self._site: Site = Site()
        self._root_coord = component.to_coordinate()
        self._site.add_coordinate(self._root_coord)
        self._phase_coords: dict[LifecyclePhase, Coordinate] = {}

    # ------------------------------------------------------------------
    # Phase management
    # ------------------------------------------------------------------

    def record_phase(self, phase: LifecyclePhase) -> bool:
        """
        Record a phase transition and update the site.

        Returns True if the transition was valid.
        """
        ok = self.component.transition_to(phase)
        if not ok:
            return False

        self.clock += 1
        coord = self.component.phase_coordinate()
        self._phase_coords[phase] = coord
        self._site.add_coordinate(coord)

        # Inclusion morphism from phase coordinate to root component coordinate
        morph = Morphism(
            source=coord,
            target=self._root_coord,
            kind=MorphismKind.INCLUSION,
            label=f"phase_{phase.value}",
        )
        self._site.add_morphism(morph)

        self._phase_log.append(
            {
                "phase": phase.value,
                "clock": self.clock,
                "update_count": self.component.update_count,
            }
        )
        return True

    def current_phase(self) -> LifecyclePhase:
        """Return the component's current lifecycle phase."""
        return self.component.current_phase

    def phase_history(self) -> list[dict[str, Any]]:
        """Return the full phase transition history."""
        return list(self._phase_log)

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    def initialization_phase(
        self,
        initial_state: dict[str, Any] | None = None,
        subscriptions: list[str] | None = None,
        timers: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        Model the component initialization phase.

        Initialization establishes the component's initial *local section*:
        it sets up state, registers subscriptions, and schedules timers.
        All resources registered here must be retracted at cleanup time.

        Parameters
        ----------
        initial_state:
            The component's initial state values.
        subscriptions:
            Descriptions of subscriptions being established.
        timers:
            Descriptions of timers being set.

        Returns
        -------
        dict
            Description of the initialization action.
        """
        self.record_phase(LifecyclePhase.BEFORE_MOUNT)
        self.record_phase(LifecyclePhase.MOUNTING)

        result = {
            "phase": "initialization",
            "component": self.component.component_type,
            "initial_state": initial_state or {},
            "subscriptions": subscriptions or [],
            "timers": timers or [],
            "resources_registered": len(subscriptions or []) + len(timers or []),
        }

        return result

    # ------------------------------------------------------------------
    # Update triggers
    # ------------------------------------------------------------------

    def record_update_trigger(
        self,
        trigger: UpdateTrigger,
        description: str = "",
        priority: RenderPriority = RenderPriority.NORMAL,
    ) -> None:
        """
        Record that an update has been triggered.

        Update triggers are the *morphisms* in the time-direction: they
        are the arrows from the current ``MOUNTED`` coordinate to the
        next ``UPDATING`` coordinate.

        Parameters
        ----------
        trigger:
            What caused this update.
        description:
            Human-readable description.
        priority:
            Rendering priority for the concurrent scheduler.
        """
        self.clock += 1
        self._update_triggers.append(
            {
                "trigger": trigger.value,
                "description": description,
                "priority": priority.value,
                "clock": self.clock,
                "phase": self.component.current_phase.value,
            }
        )

    @property
    def update_triggers(self) -> list[dict[str, Any]]:
        """All recorded update triggers."""
        return list(self._update_triggers)

    def last_trigger(self) -> dict[str, Any] | None:
        """The most recent update trigger, or None."""
        return self._update_triggers[-1] if self._update_triggers else None

    # ------------------------------------------------------------------
    # Effects
    # ------------------------------------------------------------------

    def register_effect(
        self,
        dependencies: list[str] | None = None,
        description: str = "",
        cleanup_description: str = "",
        has_cleanup: bool = False,
    ) -> EffectDescriptor:
        """
        Register a ``useEffect``-style effect.

        An effect is a *section* over the mounted/updated coordinates.
        If it has a cleanup function, that function is the section's
        *retraction map*.

        Parameters
        ----------
        dependencies:
            Dependency array (``None`` = mount-only, ``[]`` = every render,
            ``[dep1, dep2, ...]`` = when those values change).
        description:
            What the effect does.
        cleanup_description:
            What the cleanup function does.
        has_cleanup:
            Whether this effect returns a cleanup function.

        Returns
        -------
        EffectDescriptor
            The registered effect.
        """
        fires_on: list[LifecyclePhase] = [LifecyclePhase.MOUNTED]
        if dependencies is not None:
            fires_on.append(LifecyclePhase.UPDATED)

        effect = EffectDescriptor(
            dependencies=dependencies,
            description=description,
            cleanup_description=cleanup_description,
            has_cleanup=has_cleanup,
            fires_on=fires_on,
        )
        self._effects.append(effect)
        return effect

    def effects(self) -> list[EffectDescriptor]:
        """Return all registered effects."""
        return list(self._effects)

    def effects_with_cleanup(self) -> list[EffectDescriptor]:
        """Return only effects that have cleanup functions."""
        return [e for e in self._effects if e.has_cleanup]

    # ------------------------------------------------------------------
    # Cleanup phase
    # ------------------------------------------------------------------

    def cleanup_phase(self) -> dict[str, Any]:
        """
        Model the component cleanup phase.

        Cleanup is the *retraction* of all sections registered during
        the component's life.  A component that fails to clean up leaves
        behind orphaned sections — these are the memory leaks that
        :class:`CleanupTheory` detects.

        Returns a description of what needs to be cleaned up.
        """
        return {
            "phase": "cleanup",
            "component": self.component.component_type,
            "effects_with_cleanup": len(self.effects_with_cleanup()),
            "total_effects": len(self._effects),
            "cleanup_actions": [
                {
                    "effect_id": e.effect_id,
                    "action": e.cleanup_description or "call cleanup function",
                }
                for e in self.effects_with_cleanup()
            ],
        }

    # ------------------------------------------------------------------
    # Error recovery
    # ------------------------------------------------------------------

    def error_recovery_phase(
        self,
        error: str,
        error_boundary: str | None = None,
    ) -> dict[str, Any]:
        """
        Model the error recovery phase.

        When an uncaught error occurs during render or in an effect,
        the nearest error boundary catches it and shows a fallback UI.
        This is a *section repair*: the obstructed section (the broken
        render) is replaced by a fallback section (the error UI).

        Parameters
        ----------
        error:
            Description of the error.
        error_boundary:
            Name of the error boundary component that caught the error.

        Returns
        -------
        dict
            Description of the error recovery action.
        """
        self.record_phase(LifecyclePhase.ERROR)
        return {
            "phase": "error_recovery",
            "component": self.component.component_type,
            "error": error,
            "caught_by": error_boundary or "nearest error boundary",
            "action": "render fallback UI",
            "sheaf_interpretation": (
                "The broken render section is replaced by a fallback section "
                "provided by the error boundary.  This is a section repair: "
                "the obstruction is acknowledged and routed around."
            ),
        }

    # ------------------------------------------------------------------
    # Site access
    # ------------------------------------------------------------------

    def site(self) -> Site:
        """Return the underlying JuGeo :class:`~jugeo.geometry.site.Site`."""
        return self._site

    def summary(self) -> dict[str, Any]:
        """Return a diagnostic summary of the lifecycle site."""
        return {
            "component_id": self.component.component_id,
            "component_type": self.component.component_type,
            "current_phase": self.component.current_phase.value,
            "update_count": self.component.update_count,
            "effect_count": len(self._effects),
            "effects_with_cleanup": len(self.effects_with_cleanup()),
            "update_trigger_count": len(self._update_triggers),
            "clock": self.clock,
            "phase_history": self.phase_history(),
        }


# ---------------------------------------------------------------------------
# § 4  CleanupTheory
# ---------------------------------------------------------------------------


@dataclass
class CleanupItem:
    """
    A single resource that must be cleaned up when a component unmounts.

    A :class:`CleanupItem` is a *local section* over the component's
    mounted coordinate.  At unmount, every registered section must be
    retracted by calling the corresponding cleanup function.

    Failure to retract is a *memory leak* — an orphaned section that
    persists after the component has left the site.

    Parameters
    ----------
    resource_kind:
        What kind of resource this is.
    resource_id:
        A unique identifier (e.g. the timer ID returned by ``setTimeout``).
    cleanup_fn_desc:
        Human-readable description of the cleanup call
        (e.g. ``"clearTimeout(timerId)"``).
    description:
        Human-readable description of what this resource does.
    is_cleaned_up:
        True once the cleanup function has been called.
    registered_at_phase:
        Which lifecycle phase this resource was registered in.
    """

    resource_kind: ResourceKind
    resource_id: str
    cleanup_fn_desc: str
    description: str = ""
    is_cleaned_up: bool = False
    registered_at_phase: LifecyclePhase = LifecyclePhase.MOUNTED

    def mark_cleaned(self) -> None:
        """Mark this resource as cleaned up."""
        self.is_cleaned_up = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "resource_kind": self.resource_kind.value,
            "resource_id": self.resource_id,
            "cleanup_fn": self.cleanup_fn_desc,
            "description": self.description,
            "is_cleaned_up": self.is_cleaned_up,
            "registered_at_phase": self.registered_at_phase.value,
        }


class CleanupTheory:
    """
    Formalisation of resource cleanup as section retraction.

    **The core invariant**

    Every resource registered during a component's life (event listeners,
    timers, subscriptions, pending requests, animation frames) must be
    cleaned up when the component unmounts.

    In sheaf terms: every *section* registered over the "mounted" coordinate
    must be *retracted* when that coordinate is removed from the site.

    **Memory leak taxonomy**

    1. **Dangling event listener**: ``addEventListener`` without matching
       ``removeEventListener``.  After unmount, the listener still holds a
       reference to the component, preventing garbage collection.

    2. **Uncancelled timer**: ``setTimeout/setInterval`` without
       ``clearTimeout/clearInterval``.  The callback fires after unmount
       and may attempt to update state on a dead component.

    3. **Uncancelled subscription**: Observable subscription without
       ``unsubscribe()``.  The stream keeps pushing values to a ghost.

    4. **Pending request without abort**: ``fetch`` without
       ``AbortController.abort()``.  The response callback updates state
       after unmount.

    5. **Orphaned animation frame**: ``requestAnimationFrame`` without
       ``cancelAnimationFrame()``.  The frame callback runs indefinitely.

    **The "update state after unmount" anti-pattern**

    The canonical leak symptom: a callback fires after the component
    unmounts and calls ``setState()``.  React (before v18) warns:
    "Can't perform a React state update on an unmounted component."
    This is a descent obstruction: the section (state update) references
    a coordinate (the component) that has been removed from the site.

    Parameters
    ----------
    component:
        The component this theory applies to.
    """

    def __init__(self, component: ComponentCoordinate) -> None:
        self.component = component
        self._registered: list[CleanupItem] = []
        self._cleaned_up: list[CleanupItem] = []

    def register(
        self,
        resource_kind: ResourceKind,
        resource_id: str,
        cleanup_fn_desc: str,
        description: str = "",
        phase: LifecyclePhase = LifecyclePhase.MOUNTED,
    ) -> CleanupItem:
        """
        Register a resource that requires cleanup.

        Call this for every resource the component acquires:
        listeners, timers, subscriptions, etc.

        Returns the :class:`CleanupItem` for later reference.
        """
        item = CleanupItem(
            resource_kind=resource_kind,
            resource_id=resource_id,
            cleanup_fn_desc=cleanup_fn_desc,
            description=description,
            registered_at_phase=phase,
        )
        self._registered.append(item)
        return item

    def mark_cleaned(self, resource_id: str) -> bool:
        """
        Mark a registered resource as having been cleaned up.

        Returns True if the resource was found and marked; False otherwise.
        """
        for item in self._registered:
            if item.resource_id == resource_id:
                item.mark_cleaned()
                self._cleaned_up.append(item)
                return True
        return False

    def cleanup_checklist(self) -> list[CleanupItem]:
        """
        Return the list of resources that *still need* cleanup.

        This is the *pending retraction* list: sections that have not yet
        been properly retracted.
        """
        return [item for item in self._registered if not item.is_cleaned_up]

    def verify_cleanup_completeness(self) -> dict[str, Any]:
        """
        Verify that all registered resources have been cleaned up.

        Returns a dict with:
        * ``complete``: True if all resources are cleaned up.
        * ``total``: Total number of registered resources.
        * ``cleaned``: Number of resources cleaned up.
        * ``pending``: Resources still needing cleanup.
        * ``leaks``: Potential memory leaks (uncleaned resources).
        """
        pending = self.cleanup_checklist()
        return {
            "complete": not pending,
            "total": len(self._registered),
            "cleaned": len(self._cleaned_up),
            "pending": [p.to_dict() for p in pending],
            "leaks": [
                {
                    "resource_kind": p.resource_kind.value,
                    "resource_id": p.resource_id,
                    "leak_type": self._classify_leak(p),
                    "severity": self._leak_severity(p),
                    "description": (
                        f"{p.cleanup_fn_desc} was never called for "
                        f"{p.resource_kind.value} '{p.resource_id}'."
                    ),
                }
                for p in pending
            ],
        }

    def _classify_leak(self, item: CleanupItem) -> str:
        """Classify the type of memory leak for a CleanupItem."""
        kinds = {
            ResourceKind.EVENT_LISTENER: "dangling_event_listener",
            ResourceKind.TIMER: "uncancelled_timer",
            ResourceKind.SUBSCRIPTION: "uncancelled_subscription",
            ResourceKind.ABORT_CONTROLLER: "pending_request_without_abort",
            ResourceKind.ANIMATION_FRAME: "orphaned_animation_frame",
            ResourceKind.INTERSECTION_OBSERVER: "orphaned_intersection_observer",
            ResourceKind.RESIZE_OBSERVER: "orphaned_resize_observer",
            ResourceKind.MUTATION_OBSERVER: "orphaned_mutation_observer",
            ResourceKind.WEB_SOCKET: "unclosed_websocket",
            ResourceKind.CUSTOM: "uncleaned_custom_resource",
        }
        return kinds.get(item.resource_kind, "unknown_leak")

    def _leak_severity(self, item: CleanupItem) -> str:
        """Assess the severity of leaving a resource uncleaned."""
        high = {ResourceKind.TIMER, ResourceKind.SUBSCRIPTION, ResourceKind.WEB_SOCKET}
        medium = {ResourceKind.EVENT_LISTENER, ResourceKind.ABORT_CONTROLLER}
        if item.resource_kind in high:
            return "high"
        if item.resource_kind in medium:
            return "medium"
        return "low"

    def generate_cleanup_code_hints(self) -> list[str]:
        """
        Generate pseudocode hints for implementing the required cleanup.

        These hints are for documentation and code review purposes — they
        describe what the component's cleanup function should do.
        """
        hints: list[str] = []
        pending = [item for item in self._registered]  # all, not just pending

        for item in pending:
            hints.append(
                f"// Cleanup: {item.resource_kind.value}\n"
                f"  return () => {{ {item.cleanup_fn_desc}; }};"
            )
        return hints

    def summary(self) -> dict[str, Any]:
        """Return a summary of cleanup status."""
        result = self.verify_cleanup_completeness()
        return {
            "component": self.component.component_type,
            "component_id": self.component.component_id,
            **result,
        }


# ---------------------------------------------------------------------------
# § 5  SuspenseTheory
# ---------------------------------------------------------------------------


class SuspenseState(str, Enum):
    """
    States in a component's suspense lifecycle.

    Suspense introduces a new kind of *partial section*: the component's
    render is paused mid-way, waiting for an async resource to resolve.
    """

    RENDERING = "rendering"
    """Component is rendering normally."""

    SUSPENDED = "suspended"
    """Component threw a Promise; nearest Suspense boundary caught it."""

    RESUMED = "resumed"
    """The Promise resolved; component re-rendered successfully."""

    FAILED = "failed"
    """The Promise rejected; nearest error boundary caught the error."""


@dataclass
class _SuspenseEvent:
    """An event in the suspense lifecycle."""

    event_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    kind: str = "suspend"  # "suspend" | "resume" | "fail"
    resource_id: str = ""
    description: str = ""
    duration_ms: float | None = None


class SuspenseTheory:
    """
    Formalisation of React-style suspense as a partial section.

    **The core insight**

    When a component calls ``use(promise)`` or reads from a Suspense-enabled
    data source that hasn't loaded yet, it *throws the Promise*.  The React
    runtime catches this throw and treats it as a signal to:

    1. Suspend rendering of the component.
    2. Show the fallback UI from the nearest ``<Suspense>`` boundary.
    3. Wait for the Promise to resolve.
    4. Re-render the component from the top.

    In sheaf terms: the component's render function is a *section* over the
    "render" coordinate.  Throwing a Promise means the section is *not yet
    defined* at that coordinate — it's a *partial section*.  The Suspense
    boundary provides a *fallback section* that covers the gap.

    **The waterfall problem**

    If component A suspends on resource 1, and component B (rendered inside A)
    would suspend on resource 2, then B's fetch doesn't start until A's
    fetch completes.  This is a *sequential covering*: each local section
    waits for its predecessor before beginning.

    The fix is *parallel loading*: start all fetches before rendering begins,
    so the covering family is computed simultaneously.

    **The cascade structure**

    ::

        App
        └── Suspense (boundary 1 — fallback: <PageSkeleton />)
            └── UserProfile          ← suspends on user data
                └── Suspense (boundary 2 — fallback: <PostsSkeleton />)
                    └── UserPosts    ← suspends on posts data

    If UserProfile suspends, boundary 1 shows ``<PageSkeleton />``.
    If UserProfile renders but UserPosts suspends, boundary 2 shows
    ``<PostsSkeleton />`` while UserProfile is already visible.

    Parameters
    ----------
    component:
        The component this theory applies to.
    boundary_id:
        The ID of the nearest Suspense boundary (may be None if there is none).
    """

    def __init__(
        self,
        component: ComponentCoordinate,
        boundary_id: str | None = None,
    ) -> None:
        self.component = component
        self.boundary_id = boundary_id
        self.state: SuspenseState = SuspenseState.RENDERING
        self._events: list[_SuspenseEvent] = []
        self._parallel_resources: list[str] = []

    def suspend(self, resource_id: str, description: str = "") -> None:
        """
        Record that the component has suspended while waiting for *resource_id*.

        Parameters
        ----------
        resource_id:
            Identifier for the async resource (e.g. a cache key or URL).
        description:
            Human-readable description of the resource being waited on.
        """
        self.state = SuspenseState.SUSPENDED
        self._events.append(
            _SuspenseEvent(kind="suspend", resource_id=resource_id, description=description)
        )

    def resume(self, resource_id: str, duration_ms: float | None = None) -> None:
        """
        Record that the awaited resource resolved and the component resumed.

        Parameters
        ----------
        resource_id:
            Identifier for the resource that resolved.
        duration_ms:
            How long the component was suspended (milliseconds).
        """
        self.state = SuspenseState.RESUMED
        self._events.append(
            _SuspenseEvent(
                kind="resume",
                resource_id=resource_id,
                duration_ms=duration_ms,
            )
        )

    def fail(self, resource_id: str, error: str) -> None:
        """
        Record that the awaited resource rejected with *error*.

        The error will propagate to the nearest error boundary.
        """
        self.state = SuspenseState.FAILED
        self._events.append(
            _SuspenseEvent(kind="fail", resource_id=resource_id, description=error)
        )

    def register_parallel_resource(self, resource_id: str) -> None:
        """
        Register a resource that should be loaded in *parallel* with others.

        Parallel loading avoids the waterfall problem by starting all
        data fetches before rendering the tree that needs them.
        """
        self._parallel_resources.append(resource_id)

    def has_waterfall_risk(self) -> bool:
        """
        Return True if the component's suspend pattern risks a waterfall.

        A waterfall occurs when child suspenses are *sequential*:
        each child starts loading only after its parent resolves.

        We detect this by checking if the component has children that
        also use Suspense without parallel resource pre-loading.
        """
        return (
            len(self.component.children_ids) > 0
            and len(self._parallel_resources) == 0
        )

    def waterfall_description(self) -> str:
        """Describe the waterfall problem and its fix."""
        return (
            "Waterfall problem: nested Suspense boundaries load sequentially.\n\n"
            "Example:\n"
            "  1. Parent suspends → parent's fallback shown.\n"
            "  2. Parent resolves → parent renders → child starts loading.\n"
            "  3. Child suspends → child's fallback shown.\n"
            "  4. Child resolves → child renders.\n\n"
            "Total loading time = parent_load_time + child_load_time.\n\n"
            "Fix: start both fetches *before* rendering:\n"
            "  const [userData, postsData] = await Promise.all([fetchUser(), fetchPosts()]);\n"
            "  // Now render — neither component will suspend.\n\n"
            "Sheaf interpretation:\n"
            "  Waterfall = sequential covering (each section waits for predecessor).\n"
            "  Parallel loading = simultaneous covering (sections computed concurrently).\n"
            "  The parallel covering satisfies descent more efficiently.\n"
        )

    def partial_section_description(self) -> str:
        """
        Describe the suspense state as a partial section in sheaf terms.
        """
        return (
            f"Component '{self.component.component_type}' is in state: {self.state.value}.\n\n"
            "Sheaf-theoretic view:\n"
            "  - The component's render function is a section over the 'render' coordinate.\n"
            "  - Suspension = the section is not yet defined at this coordinate.\n"
            "  - Fallback UI = a surrogate section that covers the gap.\n"
            "  - Resume = the section becomes defined; the fallback is retracted.\n"
            "  - Failure = the section will never be defined; error boundary provides\n"
            "              a permanent surrogate section.\n\n"
            f"Boundary: {self.boundary_id or 'no boundary (uncaught suspension = error)'}\n"
            f"Parallel resources pre-loaded: {len(self._parallel_resources)}\n"
            f"Waterfall risk: {self.has_waterfall_risk()}\n"
        )

    def events(self) -> list[dict[str, Any]]:
        """Return the suspense event history."""
        return [
            {
                "event_id": e.event_id,
                "kind": e.kind,
                "resource_id": e.resource_id,
                "description": e.description,
                "duration_ms": e.duration_ms,
            }
            for e in self._events
        ]


# ---------------------------------------------------------------------------
# § 6  ConcurrentRenderingTheory
# ---------------------------------------------------------------------------


@dataclass
class _RenderVersion:
    """A single in-flight render attempt."""

    version_id: str = field(default_factory=lambda: f"render_{uuid.uuid4().hex[:8]}")
    priority: RenderPriority = RenderPriority.NORMAL
    trigger: UpdateTrigger = UpdateTrigger.STATE_CHANGE
    is_committed: bool = False
    is_discarded: bool = False
    description: str = ""

    def commit(self) -> None:
        self.is_committed = True

    def discard(self) -> None:
        self.is_discarded = True

    @property
    def is_pending(self) -> bool:
        return not self.is_committed and not self.is_discarded


class ConcurrentRenderingTheory:
    """
    Formalisation of React-style concurrent rendering.

    **The core insight**

    In concurrent mode, React can maintain *multiple versions* of the UI
    in-flight simultaneously.  When a higher-priority update arrives, it
    can *interrupt* a lower-priority render that is already in progress.

    In sheaf terms: each in-flight render is a *candidate global section*.
    When two renders compete, the higher-priority one *preempts* the other:
    the lower-priority candidate section is discarded, and the higher-priority
    one proceeds to commit.

    This is unlike traditional (synchronous) rendering where there is always
    exactly one section in construction.

    **startTransition**

    ``startTransition(() => setState(newValue))`` marks the state update as
    *non-urgent*.  The UI can continue showing the current (stale) state
    while the transition render is in progress.  This is:

    * A *low-priority* candidate section running alongside the current
      *committed* section.
    * The committed section is not replaced until the transition is complete.

    **useDeferredValue**

    ``useDeferredValue(value)`` returns a *deferred copy* of *value* that
    lags behind the latest value.  During a transition:

    * The *urgent* UI (e.g. a text input) updates synchronously with the latest value.
    * The *deferred* UI (e.g. a search results list) shows the previous value.

    This is a *bifurcated section*: two sub-sites have different section
    values, with the deferred sub-site lagging behind.

    **Implications for effects**

    Effects (``useEffect``) fire *after* the browser paints.  In concurrent
    mode, a render may be interrupted and restarted multiple times before
    it commits.  Effects fire only for committed renders, not for discarded
    candidates.

    Parameters
    ----------
    component:
        The component this theory applies to.
    """

    def __init__(self, component: ComponentCoordinate) -> None:
        self.component = component
        self._render_queue: list[_RenderVersion] = []
        self._committed_versions: list[_RenderVersion] = []
        self._discarded_versions: list[_RenderVersion] = []

    def start_render(
        self,
        priority: RenderPriority = RenderPriority.NORMAL,
        trigger: UpdateTrigger = UpdateTrigger.STATE_CHANGE,
        description: str = "",
    ) -> _RenderVersion:
        """
        Enqueue a new render attempt.

        If a lower-priority render is already in-flight, it will be
        interrupted when this render is processed.
        """
        version = _RenderVersion(priority=priority, trigger=trigger, description=description)
        self._render_queue.append(version)

        # Interrupt lower-priority pending renders
        for v in self._render_queue:
            if v is not version and v.is_pending:
                if self._priority_rank(v.priority) < self._priority_rank(priority):
                    v.discard()
                    self._discarded_versions.append(v)

        return version

    def commit_render(self, version_id: str) -> bool:
        """
        Commit a render version (it becomes the new UI).

        Returns True if the version was found and committed.
        """
        for v in self._render_queue:
            if v.version_id == version_id and v.is_pending:
                v.commit()
                self._committed_versions.append(v)
                return True
        return False

    def _priority_rank(self, p: RenderPriority) -> int:
        ranks = {
            RenderPriority.IMMEDIATE: 4,
            RenderPriority.USER_BLOCKING: 3,
            RenderPriority.NORMAL: 2,
            RenderPriority.LOW: 1,
            RenderPriority.IDLE: 0,
        }
        return ranks.get(p, 2)

    def pending_renders(self) -> list[_RenderVersion]:
        """Return all in-flight (not yet committed or discarded) render versions."""
        return [v for v in self._render_queue if v.is_pending]

    def start_transition(
        self,
        description: str = "",
    ) -> _RenderVersion:
        """
        Start a low-priority transition render.

        The current UI continues to be shown while this render is in progress.
        This corresponds to wrapping a state update in ``startTransition()``.
        """
        return self.start_render(
            priority=RenderPriority.LOW,
            trigger=UpdateTrigger.STATE_CHANGE,
            description=f"[transition] {description}",
        )

    def deferred_value_policy(self) -> str:
        """
        Describe the ``useDeferredValue`` policy.

        ``useDeferredValue`` keeps showing the old value while a transition
        is in progress, avoiding the user seeing half-rendered states.
        """
        return (
            "useDeferredValue policy:\n"
            "  - During a transition, urgent UI (e.g. input) updates immediately.\n"
            "  - Deferred UI (e.g. search results) keeps showing the previous value.\n"
            "  - When the transition commits, deferred UI updates to the new value.\n\n"
            "Sheaf interpretation:\n"
            "  - Urgent UI = section over the 'immediate' coordinate.\n"
            "  - Deferred UI = section over the 'deferred' coordinate (one step behind).\n"
            "  - Transition completion = the two coordinates are joined; sections reconciled.\n"
        )

    def effect_firing_policy(self) -> str:
        """
        Describe when effects fire in concurrent mode.

        Effects fire only after a render *commits* to the DOM.  Discarded
        renders do not fire effects.  This is important: if a render is
        interrupted 5 times before committing, effects fire only once.
        """
        return (
            "Effect firing policy in concurrent mode:\n"
            "  1. Renders may be interrupted and restarted multiple times.\n"
            "  2. useEffect fires ONLY after a render commits to the DOM.\n"
            "  3. useLayoutEffect fires synchronously after commit (before paint).\n"
            "  4. Discarded renders do NOT fire effects.\n\n"
            "Implication: do not assume effects fire after every render call.\n"
            "Always use the cleanup return value to handle interrupted effects.\n\n"
            "Sheaf interpretation:\n"
            "  Effects = sections over committed coordinates only.\n"
            "  Discarded renders = candidate sections that were never installed.\n"
        )

    def scheduling_description(self) -> str:
        """Describe the concurrent rendering priority scheduler."""
        return (
            "Concurrent rendering scheduler:\n\n"
            "  Priority levels (high → low):\n"
            "    IMMEDIATE       — synchronous, must complete before paint\n"
            "    USER_BLOCKING   — ~100 ms deadline (user interaction response)\n"
            "    NORMAL          — default; slightly deferrable\n"
            "    LOW             — transitions; can be interrupted\n"
            "    IDLE            — off-screen; prerendering only\n\n"
            "  When a higher-priority update arrives:\n"
            "    1. Current (lower-priority) render is interrupted.\n"
            "    2. Higher-priority render runs to completion.\n"
            "    3. Lower-priority render restarts from scratch.\n\n"
            "  Note: render functions must be *pure* (idempotent) because they\n"
            "  may be called multiple times for a single commit.\n"
        )

    def summary(self) -> dict[str, Any]:
        return {
            "component": self.component.component_type,
            "pending_renders": len(self.pending_renders()),
            "committed_renders": len(self._committed_versions),
            "discarded_renders": len(self._discarded_versions),
            "total_render_attempts": len(self._render_queue),
        }


# ---------------------------------------------------------------------------
# § 7  HydrationTheory
# ---------------------------------------------------------------------------


@dataclass
class HydrationCheckResult:
    """
    Result of a hydration mismatch check.

    Parameters
    ----------
    matches:
        True if server HTML and client render are consistent.
    mismatches:
        List of detected mismatches.
    status:
        Overall hydration status.
    server_html_digest:
        A short digest of the server-rendered HTML (for comparison).
    client_html_digest:
        A short digest of what the client would render.
    """

    matches: bool
    mismatches: list[dict[str, Any]] = field(default_factory=list)
    status: HydrationStatus = HydrationStatus.NOT_STARTED
    server_html_digest: str = ""
    client_html_digest: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "matches": self.matches,
            "mismatch_count": len(self.mismatches),
            "mismatches": self.mismatches,
            "status": self.status.value,
            "server_html_digest": self.server_html_digest,
            "client_html_digest": self.client_html_digest,
        }


class HydrationTheory:
    """
    Formalisation of server-side rendering (SSR) + client-side hydration.

    **The two-phase rendering model**

    1. **Server render** (at request time): The server runs the component
       tree and produces static HTML.  This is a *local section* over the
       "server" coordinate: a consistent assignment of HTML strings to
       component coordinates.

    2. **Client hydration** (after the page loads): The client receives
       the server HTML, reconstructs the virtual DOM, and *attaches event
       handlers*.  This is a *local section* over the "client" coordinate.

    **The descent condition**

    For hydration to succeed, the two local sections must *agree on their
    overlap*.  The overlap is the set of DOM nodes that both the server and
    client see.

    If the server renders ``<p>Hello Alice</p>`` but the client would render
    ``<p>Hello Bob</p>``, the two sections *disagree on their overlap*.  This
    is a **descent obstruction** — a hydration mismatch.

    React handles this by discarding the server HTML and doing a full client
    re-render.  This is expensive: it defeats the purpose of SSR.

    **Causes of hydration mismatches**

    1. **Random values**: ``Math.random()``, ``uuid()`` called during render.
       Server and client generate different values.
    2. **Dates and times**: ``new Date()`` returns different values.
    3. **User-specific content**: checking ``typeof window !== 'undefined'``
       or reading ``localStorage``.  Server has no window; client does.
    4. **Browser APIs**: ``window.innerWidth``, ``navigator.userAgent``.
    5. **CSS-in-JS class names**: deterministic hash functions may depend on
       insertion order, which can differ.
    6. **Third-party scripts**: injected content differs between server and client.

    **Partial hydration and streaming**

    Modern frameworks support *partial hydration* (hydrate only interactive
    components) and *streaming SSR* (stream HTML chunks, hydrate incrementally).
    These are *refinements* of the covering: instead of hydrating the entire
    page at once, each component is a separate coordinate with its own
    hydration check.

    Parameters
    ----------
    component:
        The component being hydrated.
    """

    def __init__(self, component: ComponentCoordinate) -> None:
        self.component = component
        self.status: HydrationStatus = HydrationStatus.NOT_STARTED
        self._mismatch_log: list[dict[str, Any]] = []
        self._safety_checks: list[str] = []

    def check_hydration_safety(
        self,
        server_render: dict[str, Any],
        client_render: dict[str, Any],
    ) -> HydrationCheckResult:
        """
        Check whether the server and client renders would match.

        Parameters
        ----------
        server_render:
            Dict describing what the server rendered (keys = element paths,
            values = content strings).
        client_render:
            Dict describing what the client would render.

        Returns
        -------
        HydrationCheckResult
            Result with mismatch details.
        """
        mismatches: list[dict[str, Any]] = []

        all_keys = set(server_render.keys()) | set(client_render.keys())

        for key in sorted(all_keys):
            server_val = server_render.get(key)
            client_val = client_render.get(key)

            if server_val != client_val:
                mismatches.append(
                    {
                        "element": key,
                        "server_value": server_val,
                        "client_value": client_val,
                        "kind": self._classify_mismatch(key, server_val, client_val),
                    }
                )

        matches = not mismatches
        status = (
            HydrationStatus.HYDRATED if matches
            else HydrationStatus.MISMATCH
        )
        self.status = status
        self._mismatch_log.extend(mismatches)

        return HydrationCheckResult(
            matches=matches,
            mismatches=mismatches,
            status=status,
        )

    def _classify_mismatch(
        self,
        element: str,
        server_val: Any,
        client_val: Any,
    ) -> str:
        """Attempt to classify the root cause of a hydration mismatch."""
        s = str(server_val or "")
        c = str(client_val or "")

        if server_val is None and client_val is not None:
            return "server_missing_element"
        if server_val is not None and client_val is None:
            return "client_missing_element"
        if "date" in element.lower() or "time" in element.lower():
            return "date_time_mismatch"
        if "random" in element.lower() or "uuid" in element.lower():
            return "random_value_mismatch"
        if "window" in element.lower() or "browser" in element.lower():
            return "browser_api_mismatch"
        if "user" in element.lower() or "auth" in element.lower():
            return "user_specific_content_mismatch"
        if len(s) != len(c) and s.replace(" ", "") == c.replace(" ", ""):
            return "whitespace_mismatch"
        return "content_mismatch"

    def mismatch_causes(self) -> list[str]:
        """Return the list of known root causes for hydration mismatches."""
        return [
            "Math.random() or uuid() called during render",
            "new Date() or Date.now() called during render",
            "typeof window !== 'undefined' checks with different branching",
            "localStorage or sessionStorage access during render",
            "window.innerWidth or navigator.userAgent during render",
            "CSS-in-JS class names with insertion-order-dependent hashing",
            "Third-party scripts injecting content",
            "HTML entities encoded differently on server vs client",
            "Whitespace differences in serialised HTML",
            "User-specific data (auth state, preferences) read during render",
        ]

    def register_safety_check(self, description: str) -> None:
        """Register a safety check that has been performed to prevent mismatches."""
        self._safety_checks.append(description)

    def is_hydration_safe(self) -> bool:
        """Return True if no mismatches have been detected."""
        return not self._mismatch_log

    def descent_interpretation(self) -> str:
        """Describe hydration mismatches in sheaf/descent terms."""
        return (
            "Hydration as descent:\n\n"
            "  Server section:  HTML strings over component coordinates (static).\n"
            "  Client section:  Virtual DOM nodes over component coordinates (dynamic).\n"
            "  Overlap:         The DOM nodes that both sections assign content to.\n"
            "  Descent check:   Do server HTML and client VDOM agree on the overlap?\n\n"
            "  Success (no mismatch):\n"
            "    The two local sections agree → gluing succeeds.\n"
            "    The global section = the hydrated, interactive DOM.\n\n"
            "  Failure (mismatch detected):\n"
            "    The sections disagree on at least one node.\n"
            "    Gluing fails → descent obstruction.\n"
            "    React discards server HTML and does a full client re-render.\n"
            "    This is expensive: the point of SSR was to avoid this re-render.\n\n"
            "  Fix:\n"
            "    Ensure every value used in render is deterministic and identical\n"
            "    on server and client.  Use useEffect for browser-only code.\n"
        )

    def streaming_hydration_policy(self) -> dict[str, Any]:
        """
        Describe the policy for streaming SSR and incremental hydration.

        In streaming SSR, the server sends HTML in chunks as they are ready.
        The client hydrates each chunk independently.  This is a *refinement*
        of the covering: the page coordinate is covered by chunk coordinates,
        each hydrated separately.
        """
        return {
            "strategy": "streaming_ssr_with_partial_hydration",
            "description": (
                "Stream HTML from server in document order.  "
                "Hydrate each component independently as its HTML arrives."
            ),
            "covering_structure": (
                "Page coordinate covered by component coordinates.  "
                "Each component = a separate local section.  "
                "Hydration check = descent condition on each component separately."
            ),
            "benefits": [
                "First Contentful Paint (FCP) is not blocked by slow components.",
                "Time To Interactive (TTI) for each component is independent.",
                "Mismatches are localised to individual components.",
            ],
            "caveats": [
                "Requires framework support (React 18+ / Next.js 13+).",
                "JavaScript must be available for hydration to complete.",
                "Order of hydration can affect cumulative layout shift (CLS).",
            ],
        }

    def summary(self) -> dict[str, Any]:
        return {
            "component": self.component.component_type,
            "status": self.status.value,
            "is_safe": self.is_hydration_safe(),
            "mismatch_count": len(self._mismatch_log),
            "mismatches": self._mismatch_log,
            "safety_checks": self._safety_checks,
        }


# ---------------------------------------------------------------------------
# § 8  LifecycleDescentChecker
# ---------------------------------------------------------------------------


class LifecycleDescentChecker:
    """
    Full coherence verification for a component's lifecycle via descent.

    A component's lifecycle is *coherent* when:

    1. **Cleanup completeness**: all resources registered during the
       component's life are cleaned up on unmount.
    2. **No state update after unmount**: async callbacks do not call
       ``setState`` after the component unmounts.
    3. **Correct effect dependencies**: effects declare all values they
       use in their dependency arrays.
    4. **Hydration consistency**: SSR output matches what the client would
       render.
    5. **No memory leaks**: event listeners, timers, and subscriptions are
       all cleaned up.

    Each check is a *local section* over the lifecycle site.  Full coherence
    means the descent engine can glue all local sections into a global section.

    Parameters
    ----------
    lifecycle_site:
        The :class:`LifecycleSite` to check.
    cleanup_theory:
        Optional :class:`CleanupTheory` for resource-cleanup checks.
    suspense_theory:
        Optional :class:`SuspenseTheory` for suspense-related checks.
    concurrent_theory:
        Optional :class:`ConcurrentRenderingTheory` for concurrent checks.
    hydration_theory:
        Optional :class:`HydrationTheory` for SSR/hydration checks.
    """

    def __init__(
        self,
        lifecycle_site: LifecycleSite,
        cleanup_theory: CleanupTheory | None = None,
        suspense_theory: SuspenseTheory | None = None,
        concurrent_theory: ConcurrentRenderingTheory | None = None,
        hydration_theory: HydrationTheory | None = None,
    ) -> None:
        self.lifecycle_site = lifecycle_site
        self.component = lifecycle_site.component
        self.cleanup_theory = cleanup_theory or CleanupTheory(self.component)
        self.suspense_theory = suspense_theory or SuspenseTheory(self.component)
        self.concurrent_theory = concurrent_theory or ConcurrentRenderingTheory(self.component)
        self.hydration_theory = hydration_theory or HydrationTheory(self.component)
        self._engine = DescentEngine()

    # ------------------------------------------------------------------
    # Individual checks
    # ------------------------------------------------------------------

    def check_cleanup_completeness(self) -> dict[str, Any]:
        """
        Check that all registered resources are cleaned up on unmount.

        Returns a dict describing the check result.
        """
        result = self.cleanup_theory.verify_cleanup_completeness()
        return {
            "check": "cleanup_completeness",
            "passed": result["complete"],
            "total_resources": result["total"],
            "cleaned_resources": result["cleaned"],
            "pending_resources": result["pending"],
            "leaks": result["leaks"],
        }

    def check_no_state_update_after_unmount(self) -> dict[str, Any]:
        """
        Check for the anti-pattern of updating state after unmount.

        This is the most common async lifecycle bug: a timer, subscription,
        or fetch callback fires after the component unmounts and calls setState.

        We detect this by checking whether any cleanup item of kind TIMER,
        SUBSCRIPTION, or ABORT_CONTROLLER was registered without a corresponding
        cleanup call, AND the component is in the UNMOUNTED phase.

        Returns
        -------
        dict
            Check result with risk assessment.
        """
        is_unmounted = self.component.current_phase is LifecyclePhase.UNMOUNTED

        at_risk_resources: list[dict[str, Any]] = []
        if is_unmounted:
            async_kinds = {
                ResourceKind.TIMER,
                ResourceKind.SUBSCRIPTION,
                ResourceKind.ABORT_CONTROLLER,
                ResourceKind.WEB_SOCKET,
            }
            for item in self.cleanup_theory.cleanup_checklist():
                if item.resource_kind in async_kinds:
                    at_risk_resources.append(item.to_dict())

        return {
            "check": "no_state_update_after_unmount",
            "passed": not at_risk_resources,
            "is_unmounted": is_unmounted,
            "at_risk_resources": at_risk_resources,
            "description": (
                "Async callbacks (timers, subscriptions, fetches) must not call "
                "setState after the component unmounts.  Use cleanup functions "
                "to cancel or ignore these callbacks."
            ),
        }

    def check_effect_dependencies(self) -> dict[str, Any]:
        """
        Check that effects declare correct dependency arrays.

        An effect with an incorrect (incomplete) dependency array is a
        *stale closure bug*: the effect uses a value from a previous render
        rather than the current one.

        We check:
        * Effects without cleanup that run on every render (no deps array)
          should ideally have a deps array.
        * Effects that declare dependencies should declare all used values.

        Returns
        -------
        dict
            Check result with per-effect analysis.
        """
        effects = self.lifecycle_site.effects()
        issues: list[dict[str, Any]] = []

        for effect in effects:
            if effect.runs_every_render():
                # An empty deps array means "run on every render" — this is
                # usually not what the developer intended.  It should be
                # ``None`` (mount-only) or a list of specific dependencies.
                issues.append(
                    {
                        "effect_id": effect.effect_id,
                        "issue": "empty_deps_array_runs_every_render",
                        "description": (
                            f"Effect '{effect.effect_id}' has an empty dependencies "
                            "array [] which means it runs on every render.  "
                            "If you want it to run once, use no deps array (mount-only); "
                            "if it depends on values, list them."
                        ),
                        "severity": "warning",
                    }
                )

            if not effect.has_cleanup and effect.runs_once():
                # Mount-only effects that acquire resources should have cleanup.
                # We note this as a warning — it may be intentional.
                if effect.description and any(
                    kw in effect.description.lower()
                    for kw in ("subscribe", "listen", "interval", "timeout", "fetch", "connect")
                ):
                    issues.append(
                        {
                            "effect_id": effect.effect_id,
                            "issue": "resource_acquisition_without_cleanup",
                            "description": (
                                f"Effect '{effect.effect_id}' appears to acquire a "
                                "resource but has no cleanup function.  "
                                f"Description: '{effect.description}'."
                            ),
                            "severity": "warning",
                        }
                    )

        return {
            "check": "effect_dependencies",
            "passed": not any(i["severity"] == "error" for i in issues),
            "effect_count": len(effects),
            "issue_count": len(issues),
            "issues": issues,
        }

    def check_hydration_mismatch(
        self,
        server_render: dict[str, Any] | None = None,
        client_render: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Check for SSR/hydration mismatches.

        If *server_render* and *client_render* are both provided, performs
        a concrete check.  Otherwise returns a policy description.

        Parameters
        ----------
        server_render:
            Server-rendered content (element path → content).
        client_render:
            Client-rendered content (element path → content).
        """
        if server_render is not None and client_render is not None:
            result = self.hydration_theory.check_hydration_safety(
                server_render, client_render
            )
            return {
                "check": "hydration_mismatch",
                "passed": result.matches,
                "mismatch_count": len(result.mismatches),
                "mismatches": result.mismatches,
                "status": result.status.value,
            }

        # No concrete renders provided — return policy
        return {
            "check": "hydration_mismatch",
            "passed": self.hydration_theory.is_hydration_safe(),
            "status": self.hydration_theory.status.value,
            "mismatch_count": len(self.hydration_theory._mismatch_log),
            "known_causes": self.hydration_theory.mismatch_causes(),
        }

    def check_memory_leaks(self) -> dict[str, Any]:
        """
        Check for potential memory leaks.

        Memory leaks occur when resources registered during the component's
        life are not cleaned up.  We check:

        * Uncleaned event listeners (``removeEventListener`` not called).
        * Uncancelled timers (``clearTimeout/clearInterval`` not called).
        * Unsubscribed subscriptions (``unsubscribe()`` not called).
        * Pending requests without abort (``AbortController.abort()`` not called).
        * Orphaned animation frames (``cancelAnimationFrame`` not called).

        Returns
        -------
        dict
            Check result with per-resource findings.
        """
        cleanup_result = self.cleanup_theory.verify_cleanup_completeness()
        leaks = cleanup_result["leaks"]

        high_severity_leaks = [l for l in leaks if l.get("severity") == "high"]
        medium_severity_leaks = [l for l in leaks if l.get("severity") == "medium"]
        low_severity_leaks = [l for l in leaks if l.get("severity") == "low"]

        return {
            "check": "memory_leaks",
            "passed": not leaks,
            "total_leaks": len(leaks),
            "high_severity": len(high_severity_leaks),
            "medium_severity": len(medium_severity_leaks),
            "low_severity": len(low_severity_leaks),
            "leaks": leaks,
        }

    def check_concurrent_rendering_safety(self) -> dict[str, Any]:
        """
        Check that the component is safe for concurrent rendering.

        Concurrent rendering requires that render functions are *pure*:
        calling them multiple times with the same inputs produces the same
        output.  Effects that fire based on render call count (rather than
        commit count) are bugs in concurrent mode.

        Returns
        -------
        dict
            Check result with concurrent safety assessment.
        """
        concurrent_summary = self.concurrent_theory.summary()
        issues: list[str] = []

        if concurrent_summary["discarded_renders"] > 0:
            issues.append(
                f"{concurrent_summary['discarded_renders']} render(s) were discarded.  "
                "Ensure the render function is pure and side-effect free."
            )

        return {
            "check": "concurrent_rendering_safety",
            "passed": not issues,
            "issues": issues,
            "render_summary": concurrent_summary,
        }

    def check_suspense_correctness(self) -> dict[str, Any]:
        """
        Check that suspense is used correctly.

        Checks:
        * If the component suspends, a Suspense boundary exists above it.
        * No waterfall risk (nested suspenses loading sequentially).

        Returns
        -------
        dict
            Check result with suspense analysis.
        """
        issues: list[str] = []
        events = self.suspense_theory.events()

        suspend_events = [e for e in events if e["kind"] == "suspend"]

        if suspend_events and self.suspense_theory.boundary_id is None:
            issues.append(
                "Component can suspend but has no Suspense boundary above it.  "
                "This will throw to the nearest error boundary instead."
            )

        if self.suspense_theory.has_waterfall_risk():
            issues.append(
                "Component has children that may also suspend, creating a waterfall.  "
                "Consider parallel data fetching before rendering."
            )

        return {
            "check": "suspense_correctness",
            "passed": not issues,
            "issues": issues,
            "state": self.suspense_theory.state.value,
            "suspend_count": len(suspend_events),
            "waterfall_risk": self.suspense_theory.has_waterfall_risk(),
        }

    # ------------------------------------------------------------------
    # Full descent
    # ------------------------------------------------------------------

    def full_lifecycle_descent(self) -> DescentResult:
        """
        Run all lifecycle checks and express the result as a :class:`DescentResult`.

        This is the top-level descent check for the entire lifecycle theory.
        All individual check results are local sections; the engine attempts
        to glue them into a global section.

        Returns
        -------
        DescentResult
            ``success`` if all checks pass; ``failure`` with obstruction details
            otherwise.
        """
        checks = [
            self.check_cleanup_completeness(),
            self.check_no_state_update_after_unmount(),
            self.check_effect_dependencies(),
            self.check_hydration_mismatch(),
            self.check_memory_leaks(),
            self.check_concurrent_rendering_safety(),
            self.check_suspense_correctness(),
        ]

        failed = [c for c in checks if not c["passed"]]
        coord_key = f"lifecycle_descent.{self.component.component_id}"

        if not failed:
            section = GlobalSection(
                coordinate=coord_key,
                merged_judgment={
                    "status": "all_checks_passed",
                    "component": self.component.component_type,
                    "check_count": len(checks),
                },
                constituent_sections=tuple(c["check"] for c in checks),
            )
            return DescentResult.success(section)

        obstruction = DescentObstruction(
            coordinate=coord_key,
            violated_overlaps=(),
            partial_section={
                c["check"]: {"passed": c["passed"]} for c in checks
            },
        )
        return DescentResult.failure(obstruction)

    def report(self) -> dict[str, Any]:
        """
        Return a human-readable diagnostic report of all lifecycle descent checks.
        """
        checks = {
            "cleanup_completeness": self.check_cleanup_completeness(),
            "no_state_update_after_unmount": self.check_no_state_update_after_unmount(),
            "effect_dependencies": self.check_effect_dependencies(),
            "hydration_mismatch": self.check_hydration_mismatch(),
            "memory_leaks": self.check_memory_leaks(),
            "concurrent_rendering_safety": self.check_concurrent_rendering_safety(),
            "suspense_correctness": self.check_suspense_correctness(),
        }
        all_passed = all(c["passed"] for c in checks.values())
        failed_names = [name for name, c in checks.items() if not c["passed"]]
        return {
            "component": self.component.component_type,
            "component_id": self.component.component_id,
            "current_phase": self.component.current_phase.value,
            "all_checks_passed": all_passed,
            "failed_checks": failed_names,
            "checks": checks,
            "summary": (
                "Component lifecycle is coherent — all descent conditions satisfied."
                if all_passed
                else (
                    f"Component lifecycle has {len(failed_names)} failing check(s): "
                    f"{', '.join(failed_names)}."
                )
            ),
        }
