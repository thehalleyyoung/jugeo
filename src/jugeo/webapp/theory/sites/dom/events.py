"""DOM event propagation modelled as morphism chains in the Grothendieck site.

Per theory2.tex §3, DOM propagation is a three-phase traversal of the
capture/at-target/bubble sequence.  Each phase corresponds to a distinct
class of morphisms:

* **Capture** (root → target, exclusive) — RESTRICTION morphisms: the
  listener is restricting its view to a sub-coordinate.
* **At-target** — identity (the event sits exactly on its target coordinate).
* **Bubble** (target → root, exclusive) — INCLUSION morphisms: the event is
  being included/lifted into the enclosing scope.

Event listeners are modelled as :class:`LocalSection` instances and event
delegation validity is checked via :class:`DescentResult`.
"""

from __future__ import annotations

__all__ = [
    "EventPhase",
    "EventCategory",
    "DOMEvent",
    "EventPropagationPath",
    "EventListenerSection",
    "EventDelegationChecker",
]

from dataclasses import dataclass, field
from enum import Enum

from jugeo.geometry.site import Coordinate, Morphism, MorphismKind, CoveringFamily
from jugeo.geometry.descent import LocalSection, DescentEngine, DescentResult


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class EventPhase(str, Enum):
    """The W3C event propagation phases, modelled as site-morphism classes.

    CAPTURE   — root-to-target, RESTRICTION morphisms.
    AT_TARGET — the event sits on its target coordinate.
    BUBBLE    — target-to-root, INCLUSION morphisms.
    NONE      — propagation has not started or has been stopped.
    """

    CAPTURE = "capture"
    AT_TARGET = "at_target"
    BUBBLE = "bubble"
    NONE = "none"


class EventCategory(str, Enum):
    """Semantic classification of DOM event types."""

    MOUSE = "mouse"
    KEYBOARD = "keyboard"
    POINTER = "pointer"
    TOUCH = "touch"
    FOCUS = "focus"
    FORM = "form"
    INPUT = "input"
    CLIPBOARD = "clipboard"
    DRAG = "drag"
    WHEEL = "wheel"
    ANIMATION = "animation"
    TRANSITION = "transition"
    MEDIA = "media"
    CUSTOM = "custom"
    UI = "ui"
    COMPOSITION = "composition"


# ---------------------------------------------------------------------------
# DOMEvent
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DOMEvent:
    """Descriptor for a W3C DOM event type.

    Parameters
    ----------
    event_type:
        The canonical event name, e.g. ``"click"``.
    category:
        Semantic grouping.
    bubbles:
        Whether the event propagates upward through the DOM tree.
    cancelable:
        Whether :meth:`preventDefault` has any effect.
    composed:
        Whether the event crosses shadow-DOM boundaries.
    trusted:
        Whether the event was dispatched by the user-agent (not scripted).
    """

    event_type: str
    category: EventCategory
    bubbles: bool
    cancelable: bool
    composed: bool
    trusted: bool

    @classmethod
    def standard_events(cls) -> dict[str, DOMEvent]:
        """Return a catalogue of ~30 common DOM events with correct attributes.

        Faithfully reflects the W3C UI Events specification.  Notable
        non-bubbling events: *focus*, *blur*, *load*, *unload*, *scroll*,
        *resize*, *error*, *abort*.
        """
        T, F = True, False

        def ev(
            name: str,
            cat: EventCategory,
            bubbles: bool,
            cancelable: bool,
            composed: bool = T,
            trusted: bool = T,
        ) -> tuple[str, DOMEvent]:
            return name, cls(
                event_type=name,
                category=cat,
                bubbles=bubbles,
                cancelable=cancelable,
                composed=composed,
                trusted=trusted,
            )

        M = EventCategory.MOUSE
        K = EventCategory.KEYBOARD
        P = EventCategory.POINTER
        Fo = EventCategory.FOCUS
        Fr = EventCategory.FORM
        I = EventCategory.INPUT
        C = EventCategory.CLIPBOARD
        D = EventCategory.DRAG
        W = EventCategory.WHEEL
        An = EventCategory.ANIMATION
        Tr = EventCategory.TRANSITION
        UI = EventCategory.UI
        Co = EventCategory.COMPOSITION

        return dict([
            # --- Mouse ---
            ev("click",       M,  T, T),
            ev("dblclick",    M,  T, T),
            ev("mousedown",   M,  T, T),
            ev("mouseup",     M,  T, T),
            ev("mousemove",   M,  T, F),
            ev("mouseover",   M,  T, T),
            ev("mouseout",    M,  T, T),
            ev("mouseenter",  M,  F, F),  # does NOT bubble
            ev("mouseleave",  M,  F, F),  # does NOT bubble
            ev("contextmenu", M,  T, T),
            # --- Keyboard ---
            ev("keydown",     K,  T, T),
            ev("keyup",       K,  T, F),
            ev("keypress",    K,  T, T),  # deprecated but still dispatched
            # --- Pointer ---
            ev("pointerdown", P,  T, T),
            ev("pointerup",   P,  T, T),
            ev("pointermove", P,  T, F),
            ev("pointercancel", P, T, F),
            # --- Focus (non-bubbling per spec) ---
            ev("focus",       Fo, F, F),  # does NOT bubble
            ev("blur",        Fo, F, F),  # does NOT bubble
            ev("focusin",     Fo, T, F),  # bubbles (delegatable)
            ev("focusout",    Fo, T, F),  # bubbles (delegatable)
            # --- Form ---
            ev("submit",      Fr, T, T),
            ev("reset",       Fr, T, T),
            ev("change",      Fr, T, F),
            # --- Input ---
            ev("input",       I,  T, F),  # bubbles, NOT cancelable
            ev("beforeinput", I,  T, T),
            # --- Clipboard ---
            ev("copy",        C,  T, T),
            ev("cut",         C,  T, T),
            ev("paste",       C,  T, T),
            # --- Drag ---
            ev("drag",        D,  T, T),
            ev("drop",        D,  T, T),
            ev("dragover",    D,  T, T),
            ev("dragenter",   D,  T, T),
            ev("dragleave",   D,  T, F),
            # --- Wheel ---
            ev("wheel",       W,  T, T),
            # --- Scroll (does NOT bubble) ---
            ev("scroll",      UI, F, F, composed=F),
            # --- Animation ---
            ev("animationstart",  An, T, F),
            ev("animationend",    An, T, F),
            ev("animationiteration", An, T, F),
            # --- Transition ---
            ev("transitionend", Tr, T, F),
            # --- Composition ---
            ev("compositionstart",  Co, T, F),
            ev("compositionupdate", Co, T, F),
            ev("compositionend",    Co, T, F),
        ])


# ---------------------------------------------------------------------------
# EventPropagationPath
# ---------------------------------------------------------------------------


@dataclass
class EventPropagationPath:
    """The full DOM propagation path for a given event and target.

    Parameters
    ----------
    event:
        The event descriptor.
    target_coord:
        The coordinate name of the event target element.
    path:
        Ordered list of coordinate names from the tree root (inclusive) down
        to the target (inclusive).  For example, ``["window", "document",
        "body", "div#app", "button#submit"]``.

    The sheaf-theoretic interpretation is:

    * **Capture** — restriction morphisms from the global scope down to the
      local target; the listener *restricts* its view to a sub-coordinate.
    * **Bubble** — inclusion morphisms from the local target back up;
      the event is *lifted* into the enclosing scope.
    """

    event: DOMEvent
    target_coord: str
    path: list[str] = field(default_factory=list)

    # -- Phase decomposition -------------------------------------------------

    def capture_phase(self) -> list[str]:
        """Coordinates traversed during capture: root down to target (exclusive)."""
        if not self.path:
            return []
        return self.path[:-1]

    def at_target_phase(self) -> list[str]:
        """The single-element list containing the event target."""
        return [self.target_coord]

    def bubble_phase(self) -> list[str]:
        """Coordinates traversed during bubble: target up to root (exclusive).

        Returns an empty list when :attr:`event.bubbles` is ``False``.
        """
        if not self.event.bubbles or not self.path:
            return []
        # Reverse of capture, excluding the target itself
        return list(reversed(self.path[:-1]))

    def full_propagation(self) -> list[tuple[str, EventPhase]]:
        """Complete ordered traversal as (coordinate_name, phase) pairs.

        The sequence follows the W3C specification:
        1. Capture phase (root → target exclusive)
        2. At-target phase
        3. Bubble phase (target exclusive → root), only when event bubbles
        """
        result: list[tuple[str, EventPhase]] = []

        for coord in self.capture_phase():
            result.append((coord, EventPhase.CAPTURE))

        for coord in self.at_target_phase():
            result.append((coord, EventPhase.AT_TARGET))

        for coord in self.bubble_phase():
            result.append((coord, EventPhase.BUBBLE))

        return result

    def as_morphism_chain(
        self, site_objects: dict[str, Coordinate]
    ) -> list[Morphism]:
        """Express the propagation path as a chain of site morphisms.

        * Capture edges are RESTRICTION morphisms (parent → child direction
          in the site: the listener restricts to a sub-coordinate).
        * At-target has no morphism (identity, omitted).
        * Bubble edges are INCLUSION morphisms (child → parent direction:
          the event is included/lifted into the enclosing scope).

        Parameters
        ----------
        site_objects:
            Mapping from coordinate name to :class:`Coordinate` instance.
            Must contain every name appearing in :attr:`path`.

        Returns
        -------
        list[Morphism]
            Morphisms in propagation order (capture first, then bubble).
        """
        morphisms: list[Morphism] = []

        capture = self.capture_phase()
        for i in range(len(capture) - 1):
            parent_name = capture[i]
            child_name = capture[i + 1]
            if parent_name in site_objects and child_name in site_objects:
                morphisms.append(
                    Morphism(
                        source=site_objects[parent_name],
                        target=site_objects[child_name],
                        kind=MorphismKind.RESTRICTION,
                        label=f"capture:{parent_name}->{child_name}",
                    )
                )

        # Final capture step: last ancestor → target
        if capture and self.target_coord in site_objects:
            last_cap = capture[-1]
            if last_cap in site_objects:
                morphisms.append(
                    Morphism(
                        source=site_objects[last_cap],
                        target=site_objects[self.target_coord],
                        kind=MorphismKind.RESTRICTION,
                        label=f"capture:{last_cap}->{self.target_coord}",
                    )
                )

        bubble = self.bubble_phase()
        for i in range(len(bubble) - 1):
            child_name = bubble[i]
            parent_name = bubble[i + 1]
            if child_name in site_objects and parent_name in site_objects:
                morphisms.append(
                    Morphism(
                        source=site_objects[child_name],
                        target=site_objects[parent_name],
                        kind=MorphismKind.INCLUSION,
                        label=f"bubble:{child_name}->{parent_name}",
                    )
                )

        # First bubble step: target → first ancestor
        if bubble and self.target_coord in site_objects:
            first_bub = bubble[0]
            if first_bub in site_objects:
                morphisms.insert(
                    len(capture),
                    Morphism(
                        source=site_objects[self.target_coord],
                        target=site_objects[first_bub],
                        kind=MorphismKind.INCLUSION,
                        label=f"bubble:{self.target_coord}->{first_bub}",
                    ),
                )

        return morphisms


# ---------------------------------------------------------------------------
# EventListenerSection
# ---------------------------------------------------------------------------


@dataclass
class EventListenerSection:
    """An event listener modelled as a :class:`LocalSection` on the site.

    Each listener occupies a single coordinate (the DOM element it is
    attached to) and provides a judgment that the given event type will be
    handled at that coordinate, in the specified phase.

    Parameters
    ----------
    coord_name:
        Coordinate name of the element carrying this listener.
    event_type:
        The event name the listener responds to, e.g. ``"click"``.
    phase:
        Which propagation phase activates this listener.
    handler_id:
        Stable identifier for the handler function (e.g. its qualified name
        or a UUID assigned at registration time).
    passive:
        When ``True``, :meth:`preventDefault` is a no-op (improves scroll
        performance).
    once:
        When ``True``, the listener auto-removes itself after firing once.
    """

    coord_name: str
    event_type: str
    phase: EventPhase
    handler_id: str
    passive: bool = False
    once: bool = False

    def to_local_section(self) -> LocalSection:
        """Lift this listener descriptor into a :class:`LocalSection`.

        The judgment data encodes all listener options so that descent
        computations can reason about handler coverage across the element
        tree.
        """
        return LocalSection(
            coordinate=self.coord_name,
            judgment_data={
                "event_type": self.event_type,
                "phase": self.phase.value,
                "handler_id": self.handler_id,
                "passive": self.passive,
                "once": self.once,
            },
            evidence_bundle=(
                f"listener:{self.handler_id}",
                f"phase:{self.phase.value}",
                f"event:{self.event_type}",
            ),
            trust_level=1.0,
            provenance=("EventListenerSection.to_local_section",),
            is_partial=False,
            residual_obligations=[],
        )


# ---------------------------------------------------------------------------
# EventDelegationChecker
# ---------------------------------------------------------------------------

from jugeo.geometry.descent import GlobalSection, DescentObstruction  # noqa: E402


class EventDelegationChecker:
    """Validates event delegation using descent on the DOM site.

    Event delegation is a pattern where a single listener on an *ancestor*
    element handles events from *descendant* targets.  It is valid when the
    ancestor's covering family includes every matching target — i.e., the
    family of restriction morphisms from the ancestor to each target forms a
    covering in the Grothendieck topology.

    This class checks that condition by constructing a :class:`CoveringFamily`
    and asking whether it is a covering (``is_covering()``).  The result is
    returned as a :class:`DescentResult`.
    """

    @staticmethod
    def _collect_descendants(
        node: str, dom_tree: dict[str, list[str]]
    ) -> list[str]:
        """Return all descendants of *node* (BFS, excluding *node* itself)."""
        visited: list[str] = []
        queue: list[str] = list(dom_tree.get(node, []))
        seen: set[str] = set(queue)
        while queue:
            current = queue.pop(0)
            visited.append(current)
            for child in dom_tree.get(current, []):
                if child not in seen:
                    seen.add(child)
                    queue.append(child)
        return visited

    def check_delegation(
        self,
        ancestor: str,
        target_selectors: list[str],
        event_type: str,
        dom_tree: dict[str, list[str]],
    ) -> DescentResult:
        """Check whether delegating *event_type* from *ancestor* is valid.

        Delegation is valid when every node in *dom_tree* that matches one of
        the *target_selectors* is a descendant of *ancestor* and is therefore
        reachable via a restriction morphism from the ancestor coordinate.

        In sheaf-theoretic terms, the family of restriction morphisms
        ``{ancestor → target_i}`` must form a covering of the ancestor
        coordinate for the given event type.

        Parameters
        ----------
        ancestor:
            Coordinate name of the delegating element.
        target_selectors:
            List of coordinate names (or selector strings) that the delegated
            handler should match.
        event_type:
            The event type being delegated.
        dom_tree:
            Adjacency list ``{parent: [child, ...]}``.  Keys are coordinate
            names; the root has no entry in the values.

        Returns
        -------
        DescentResult
            ``is_success`` is ``True`` when all selectors are covered.
            ``is_failure`` is ``True`` when at least one selector names a node
            that is not a descendant of *ancestor*.
        """
        ancestor_coord = Coordinate(ancestor)
        descendants = set(self._collect_descendants(ancestor, dom_tree))

        # Determine which selectors are reachable from the ancestor
        matching_targets = [s for s in target_selectors if s in descendants]
        uncovered = [s for s in target_selectors if s not in descendants]

        if uncovered:
            # Build an obstruction listing the coordinates that cannot be
            # reached from the ancestor via restriction morphisms.
            obstruction = DescentObstruction(
                coordinate=ancestor,
                violated_overlaps=(),
                partial_section={
                    "event_type": event_type,
                    "covered_targets": matching_targets,
                    "uncovered_targets": uncovered,
                },
            )
            return DescentResult(_obstruction=obstruction)

        if not target_selectors:
            # No targets to cover — trivially successful (vacuous covering).
            global_section = GlobalSection(
                coordinate=ancestor,
                merged_judgment={
                    "event_type": event_type,
                    "delegation_targets": [],
                    "delegation_valid": True,
                },
                constituent_sections=tuple(matching_targets),
                certificate=f"delegation:{ancestor}:{event_type}:vacuous",
                trust_floor=1.0,
            )
            return DescentResult(_global_section=global_section)

        # Construct covering family to confirm all targets are covered.
        # Per theory2.tex §3.3, a covering family {f_i: U_i -> U} has
        # morphisms *targeting* the base U.  Each target element (U_i) maps
        # to the ancestor (U) via an INCLUSION morphism.
        target_coords = [Coordinate(t) for t in matching_targets]
        members = [
            Morphism(
                source=tc,
                target=ancestor_coord,
                kind=MorphismKind.INCLUSION,
                label=f"delegate:{tc.components[-1]}->{ancestor}:{event_type}",
            )
            for tc in target_coords
        ]
        covering = CoveringFamily(
            base=ancestor_coord,
            members=members,
        )

        if covering.is_covering():
            global_section = GlobalSection(
                coordinate=ancestor,
                merged_judgment={
                    "event_type": event_type,
                    "delegation_targets": matching_targets,
                    "delegation_valid": True,
                    "morphism_count": len(members),
                },
                constituent_sections=tuple(matching_targets),
                certificate=(
                    f"delegation:{ancestor}:{event_type}:"
                    f"{len(matching_targets)}targets"
                ),
                trust_floor=1.0,
            )
            return DescentResult(_global_section=global_section)

        # Covering family exists but is_covering() returned False — the
        # topology does not admit this family as a valid cover.
        obstruction = DescentObstruction(
            coordinate=ancestor,
            violated_overlaps=(),
            partial_section={
                "event_type": event_type,
                "covered_targets": matching_targets,
                "reason": "covering_family_not_admitted_by_topology",
            },
        )
        return DescentResult(_obstruction=obstruction)
