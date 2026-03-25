"""JavaScript Event Loop modelled as a cyclic execution site.

This module formalises the JS event loop scheduling algebra using the
Grothendieck-site machinery from ``jugeo.geometry``.  Each queue kind
(macrotask, microtask, …) becomes a coordinate in the base category; a
full loop iteration is expressed as a ``CoveringFamily`` whose members
collectively witness everything that happens in one tick.

References
----------
* HTML Living Standard §8.1.6 — The event loop
* ECMA-262 §9.4 — Execution Contexts
* WHATWG Microtask Checkpoint algorithm
"""

from __future__ import annotations

__all__ = [
    "TaskQueueKind",
    "ExecutionContext",
    "EventLoopSite",
    "PromiseSection",
    "AsyncAwaitDesugaring",
]

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from jugeo.geometry.site import (
    Site,
    Coordinate,
    CoordinateKind,
    Morphism,
    MorphismKind,
    CoveringFamily,
)
from jugeo.geometry.descent import (
    LocalSection,
    DescentResult,
    GlobalSection,
    DescentObstruction,
)


# ---------------------------------------------------------------------------
# 1. TaskQueueKind
# ---------------------------------------------------------------------------


class TaskQueueKind(str, Enum):
    """Scheduling queue kinds in the HTML event loop.

    Scheduling rules
    ----------------
    MACROTASK
        One task is dequeued and executed per loop iteration.  Sources:
        ``setTimeout``, ``setInterval``, I/O callbacks, UI events, and
        script evaluation.  After a macrotask completes, a full microtask
        checkpoint is performed before the next macrotask begins.

    MICROTASK
        All pending microtasks are drained *before* the next macrotask is
        selected.  New microtasks enqueued during the drain are also
        processed in the same checkpoint (the queue is not snapshotted).
        Sources: ``Promise.then``, ``queueMicrotask``, ``MutationObserver``.

    ANIMATION_FRAME
        Callbacks registered via ``requestAnimationFrame`` fire after the
        microtask checkpoint and before the browser paints the next frame.
        Only the callbacks registered *before* the checkpoint begins are
        invoked; those registered during the flush run in the next frame.

    IDLE_CALLBACK
        Callbacks registered via ``requestIdleCallback`` fire when the main
        thread is idle — no pending macrotasks or microtasks.  Each callback
        receives a ``deadline`` object indicating remaining idle time.

    MESSAGE_CHANNEL
        ``MessageChannel.port.postMessage`` schedules its handler at
        microtask-equivalent priority (before the next macrotask).  The HTML
        spec categorises it as a task, but most runtime implementations give
        it the same effective priority as a microtask.
    """

    MACROTASK = "macrotask"
    MICROTASK = "microtask"
    ANIMATION_FRAME = "animation_frame"
    IDLE_CALLBACK = "idle_callback"
    MESSAGE_CHANNEL = "message_channel"


# ---------------------------------------------------------------------------
# 2. ExecutionContext
# ---------------------------------------------------------------------------


@dataclass
class ExecutionContext:
    """ECMAScript execution context (ECMA-262 §9.4).

    Each call to a function, evaluation of a module, or execution of a
    top-level script pushes a new execution context onto the running
    context stack.  This dataclass captures the fields relevant to
    event-loop scheduling analysis.

    Attributes
    ----------
    context_id:
        Unique identifier for this execution context instance.
    context_kind:
        One of ``global``, ``function``, ``module``, ``eval``, ``async``.
    realm:
        The realm (origin or window identifier) in which this context
        lives.  Contexts from different realms cannot share mutable state
        directly.
    is_strict:
        Whether the context operates in strict mode.
    current_task:
        The coordinate name of the task currently executing inside this
        context, or ``None`` if the context is suspended (e.g. awaiting a
        Promise) or not yet started.
    """

    context_id: str
    context_kind: str
    realm: str
    is_strict: bool = False
    current_task: str | None = None


# ---------------------------------------------------------------------------
# Internal coordinate name constants
# ---------------------------------------------------------------------------

_LOOP_COORD_NAME = "event_loop"
_MACRO_COORD_NAME = "macrotask_queue"
_MICRO_COORD_NAME = "microtask_queue"
_RAF_COORD_NAME = "animation_frame_queue"
_IDLE_COORD_NAME = "idle_callback_queue"


# ---------------------------------------------------------------------------
# 3. EventLoopSite
# ---------------------------------------------------------------------------


class EventLoopSite:
    """The JavaScript event loop modelled as a Grothendieck site.

    The *base coordinate* of the site is the event loop itself.  Each
    queue kind corresponds to a coordinate in the category.  A full loop
    iteration is expressed as a ``CoveringFamily``: the morphisms from the
    macrotask, microtask, and RAF coordinates together cover the base
    coordinate for that tick.

    Parameters
    ----------
    label:
        A human-readable label for this event loop instance (e.g. the
        origin string or window identifier).
    """

    def __init__(self, label: str = "js_event_loop") -> None:
        self._site: Site = Site(label=label)
        self._label = label

        # Internal queues — store coordinate names (strings) in FIFO order.
        self._macrotask_queue: list[str] = []
        self._microtask_queue: list[str] = []
        self._animation_callbacks: list[str] = []
        self._idle_callbacks: list[str] = []

        # Maps coord_name -> {"reads": set[str], "writes": set[str]} for
        # race-condition analysis.
        self._task_state_access: dict[str, dict[str, set[str]]] = {}

        self._build_base_coordinates()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_base_coordinates(self) -> None:
        """Populate the site with the structural queue coordinates."""
        structural = [
            Coordinate(
                name=_LOOP_COORD_NAME,
                kind=CoordinateKind.REGION,
                metadata={"role": "event_loop_root"},
            ),
            Coordinate(
                name=_MACRO_COORD_NAME,
                kind=CoordinateKind.MODULE,
                metadata={"queue": TaskQueueKind.MACROTASK.value},
            ),
            Coordinate(
                name=_MICRO_COORD_NAME,
                kind=CoordinateKind.MODULE,
                metadata={"queue": TaskQueueKind.MICROTASK.value},
            ),
            Coordinate(
                name=_RAF_COORD_NAME,
                kind=CoordinateKind.MODULE,
                metadata={"queue": TaskQueueKind.ANIMATION_FRAME.value},
            ),
            Coordinate(
                name=_IDLE_COORD_NAME,
                kind=CoordinateKind.MODULE,
                metadata={"queue": TaskQueueKind.IDLE_CALLBACK.value},
            ),
        ]
        for coord in structural:
            self._site.add_coordinate(coord)

    def _make_task_coordinate(
        self,
        task_id: str,
        queue_kind: TaskQueueKind,
        source: str,
        extra_meta: dict[str, Any] | None = None,
    ) -> str:
        """Register a task as a coordinate and return its unique name."""
        coord_name = f"{queue_kind.value}::{task_id}"
        meta: dict[str, Any] = {
            "task_id": task_id,
            "queue": queue_kind.value,
            "source": source,
        }
        if extra_meta:
            meta.update(extra_meta)

        coord = Coordinate(
            name=coord_name,
            kind=CoordinateKind.FUNCTION,
            metadata=meta,
        )
        self._site.add_coordinate(coord)
        self._task_state_access[coord_name] = {"reads": set(), "writes": set()}
        return coord_name

    # ------------------------------------------------------------------
    # Public enqueue methods
    # ------------------------------------------------------------------

    def enqueue_macrotask(
        self,
        task_id: str,
        source: str,
        delay_ms: float = 0,
    ) -> str:
        """Add a macrotask to the macrotask queue.

        Parameters
        ----------
        task_id:
            Logical identifier for the task (e.g. ``"setTimeout_42"``).
        source:
            The Web API that scheduled this task (e.g. ``"setTimeout"``).
        delay_ms:
            Minimum delay in milliseconds before the task is eligible to
            run.  A value of ``0`` means "as soon as possible in the next
            event loop iteration."

        Returns
        -------
        str
            The coordinate name under which the task is registered in the
            site.
        """
        coord_name = self._make_task_coordinate(
            task_id,
            TaskQueueKind.MACROTASK,
            source,
            extra_meta={"delay_ms": delay_ms},
        )
        self._macrotask_queue.append(coord_name)
        return coord_name

    def enqueue_microtask(self, task_id: str, source: str) -> str:
        """Add a microtask to the microtask queue.

        Parameters
        ----------
        task_id:
            Logical identifier for the microtask (e.g.
            ``"promise_then_7"``).
        source:
            The API that scheduled this microtask (e.g.
            ``"Promise.then"``).

        Returns
        -------
        str
            The coordinate name under which the microtask is registered.
        """
        coord_name = self._make_task_coordinate(
            task_id,
            TaskQueueKind.MICROTASK,
            source,
        )
        self._microtask_queue.append(coord_name)
        return coord_name

    def register_animation_callback(self, callback_id: str) -> str:
        """Register a ``requestAnimationFrame`` callback.

        Parameters
        ----------
        callback_id:
            Identifier for the animation frame callback.

        Returns
        -------
        str
            The coordinate name under which the callback is registered.
        """
        coord_name = self._make_task_coordinate(
            callback_id,
            TaskQueueKind.ANIMATION_FRAME,
            source="requestAnimationFrame",
        )
        self._animation_callbacks.append(coord_name)
        return coord_name

    # ------------------------------------------------------------------
    # State-access tracking (feeds the race-condition analysis)
    # ------------------------------------------------------------------

    def record_read(self, coord_name: str, state_key: str) -> None:
        """Record that the task at *coord_name* reads *state_key*."""
        if coord_name in self._task_state_access:
            self._task_state_access[coord_name]["reads"].add(state_key)

    def record_write(self, coord_name: str, state_key: str) -> None:
        """Record that the task at *coord_name* writes *state_key*."""
        if coord_name in self._task_state_access:
            self._task_state_access[coord_name]["writes"].add(state_key)

    # ------------------------------------------------------------------
    # Simulation
    # ------------------------------------------------------------------

    def run_one_iteration(self) -> list[tuple[str, str]]:
        """Simulate one full event-loop iteration.

        Execution order follows HTML Living Standard §8.1.6:

        1. Dequeue and execute **one** macrotask (if available).
        2. Drain the **entire** microtask queue, including any microtasks
           enqueued during the drain.
        3. Execute all RAF callbacks that were queued *before* this
           iteration (those added during the flush run next frame).

        Returns
        -------
        list[tuple[str, str]]
            A list of ``(coord_name, queue_kind_value)`` pairs in the
            order they were executed.
        """
        executed: list[tuple[str, str]] = []

        # Step 1 — one macrotask.
        if self._macrotask_queue:
            macro_coord = self._macrotask_queue.pop(0)
            executed.append((macro_coord, TaskQueueKind.MACROTASK.value))

        # Step 2 — drain all microtasks (including newly enqueued ones).
        while self._microtask_queue:
            micro_coord = self._microtask_queue.pop(0)
            executed.append((micro_coord, TaskQueueKind.MICROTASK.value))

        # Step 3 — flush the RAF callbacks registered before this tick.
        raf_snapshot = list(self._animation_callbacks)
        self._animation_callbacks.clear()
        for raf_coord in raf_snapshot:
            executed.append((raf_coord, TaskQueueKind.ANIMATION_FRAME.value))

        return executed

    # ------------------------------------------------------------------
    # Topology
    # ------------------------------------------------------------------

    def scheduling_covering(self) -> CoveringFamily:
        """Return the covering family for one full event-loop iteration.

        In sheaf-theoretic terms, one loop iteration *covers* the
        ``event_loop`` coordinate: the three restriction morphisms from
        the macrotask, microtask, and RAF queue coordinates collectively
        witness everything the loop does in one tick.

        The covering family is also registered on the site so that it can
        participate in descent calculations.

        Returns
        -------
        CoveringFamily
            A covering family whose members are restriction morphisms from
            each queue coordinate onto the base ``event_loop`` coordinate.
        """
        loop_coord = Coordinate(name=_LOOP_COORD_NAME, kind=CoordinateKind.REGION)
        macro_coord = Coordinate(name=_MACRO_COORD_NAME, kind=CoordinateKind.MODULE)
        micro_coord = Coordinate(name=_MICRO_COORD_NAME, kind=CoordinateKind.MODULE)
        raf_coord = Coordinate(name=_RAF_COORD_NAME, kind=CoordinateKind.MODULE)

        members = [
            Morphism(
                source=macro_coord,
                target=loop_coord,
                kind=MorphismKind.RESTRICTION,
                label="macrotask_step",
            ),
            Morphism(
                source=micro_coord,
                target=loop_coord,
                kind=MorphismKind.RESTRICTION,
                label="microtask_checkpoint",
            ),
            Morphism(
                source=raf_coord,
                target=loop_coord,
                kind=MorphismKind.RESTRICTION,
                label="animation_frame_flush",
            ),
        ]
        family = CoveringFamily(
            base=loop_coord,
            members=members,
            label="one_loop_iteration",
        )
        self._site.add_covering_family(family)
        return family

    def descent_check(self) -> DescentResult:
        """Check whether the current covering family satisfies descent.

        Returns a ``DescentResult`` indicating whether the scheduling
        covering from :meth:`scheduling_covering` constitutes a valid
        cover (all three queue morphisms are present).  An empty macrotask
        queue still produces a valid descent result — idle ticks are legal.

        Returns
        -------
        DescentResult
            A descent result reflecting whether the site topology is
            self-consistent.
        """
        family = self.scheduling_covering()
        all_covered = len(family.members) == 3
        if all_covered:
            section = GlobalSection(
                coordinate=_LOOP_COORD_NAME,
                merged_judgment={
                    "queue_count": 3,
                    "label": self._label,
                    "covers": [m.label for m in family.members],
                },
                constituent_sections=(
                    _MACRO_COORD_NAME,
                    _MICRO_COORD_NAME,
                    _RAF_COORD_NAME,
                ),
            )
            return DescentResult.success(section)
        obstruction = DescentObstruction(
            coordinate=_LOOP_COORD_NAME,
            partial_section={
                "missing_queues": 3 - len(family.members),
                "label": self._label,
            },
        )
        return DescentResult.failure(obstruction)

    # ------------------------------------------------------------------
    # Race-condition analysis
    # ------------------------------------------------------------------

    def has_race_condition(
        self,
        task_a: str,
        task_b: str,
        shared_state: list[str],
    ) -> bool:
        """Detect a potential read/write race between two tasks.

        JavaScript is single-threaded, so true concurrent races are
        impossible within one realm.  However, tasks that execute in
        separate iterations can observe torn state when both interact with
        shared mutable data (e.g. a DOM node or a module-level variable)
        without an explicit ordering guarantee such as a Promise chain.

        A race condition is flagged when:

        * Both tasks access at least one key in *shared_state*, **and**
        * At least one of the two tasks *writes* to that key.

        Parameters
        ----------
        task_a, task_b:
            Coordinate names as returned by the ``enqueue_*`` methods.
        shared_state:
            The list of state keys (e.g. variable names, DOM node ids)
            that may be accessed by both tasks.

        Returns
        -------
        bool
            ``True`` if a potential race condition is detected.
        """
        access_a = self._task_state_access.get(
            task_a, {"reads": set(), "writes": set()}
        )
        access_b = self._task_state_access.get(
            task_b, {"reads": set(), "writes": set()}
        )

        for key in shared_state:
            a_touches = key in access_a["reads"] or key in access_a["writes"]
            b_touches = key in access_b["reads"] or key in access_b["writes"]
            if a_touches and b_touches:
                if key in access_a["writes"] or key in access_b["writes"]:
                    return True
        return False


# ---------------------------------------------------------------------------
# 4. PromiseSection
# ---------------------------------------------------------------------------


@dataclass
class PromiseSection:
    """A Promise modelled as a deferred local section.

    In the sheaf picture a Promise is a *partial section* — defined on its
    coordinate but not yet globally extended — that becomes total only once
    the Promise settles.  Before settlement ``is_partial`` is ``True``; after
    settlement the section can extend to a global section (fulfilled) or
    fails to extend entirely (rejected).

    Attributes
    ----------
    promise_id:
        Unique identifier for this Promise instance.
    state:
        One of ``"pending"``, ``"fulfilled"``, or ``"rejected"``.
    value:
        The fulfillment value, set when *state* transitions to
        ``"fulfilled"``.
    rejection_reason:
        The rejection reason, set when *state* transitions to
        ``"rejected"``.
    then_callbacks:
        Microtask coordinate names scheduled when the Promise resolves.
        These correspond to ``Promise.then`` handlers registered before
        settlement.
    """

    promise_id: str
    state: str = "pending"
    value: Any | None = None
    rejection_reason: Any | None = None
    then_callbacks: list[str] = field(default_factory=list)

    def to_local_section(self) -> LocalSection:
        """Represent this Promise as a ``LocalSection`` in the descent algebra.

        The section is *partial* while the Promise is pending and becomes
        total once it settles.  A settled Promise carries its callbacks as
        evidence and its fulfillment value (or rejection reason) in the
        judgment data.

        Returns
        -------
        LocalSection
            A local section whose ``judgment_data`` encodes the Promise
            state and whose ``is_partial`` flag reflects whether the
            Promise is still pending.
        """
        return LocalSection(
            coordinate=self.promise_id,
            judgment_data={
                "state": self.state,
                "value": self.value,
                "rejection_reason": self.rejection_reason,
                "then_callbacks": list(self.then_callbacks),
            },
            evidence_bundle=tuple(self.then_callbacks),
            trust_level=0.0 if self.state == "pending" else 1.0,
            provenance=(f"promise:{self.promise_id}",),
            is_partial=self.state == "pending",
            residual_obligations=(
                []
                if self.state != "pending"
                else [f"settle:{self.promise_id}"]
            ),
        )

    def resolve(self, value: Any) -> list[str]:
        """Transition the Promise to ``fulfilled`` and schedule continuations.

        If the Promise has already settled this is a no-op and returns an
        empty list (matching the JS specification's ignore-if-resolved
        behaviour).

        Parameters
        ----------
        value:
            The fulfillment value.

        Returns
        -------
        list[str]
            The microtask coordinate names of the ``then`` callbacks
            scheduled as a result of this resolution.  These should be
            enqueued into the event loop's microtask queue.
        """
        if self.state != "pending":
            return []
        self.state = "fulfilled"
        self.value = value
        return list(self.then_callbacks)

    def reject(self, reason: Any) -> list[str]:
        """Transition the Promise to ``rejected`` and schedule rejection handlers.

        If the Promise has already settled this is a no-op and returns an
        empty list.

        Parameters
        ----------
        reason:
            The rejection reason (typically an ``Error``-like object).

        Returns
        -------
        list[str]
            The microtask coordinate names of any ``catch`` /
            ``then(_, onRejected)`` handlers scheduled as a result.  This
            implementation reuses ``then_callbacks``; callers should
            register rejection handlers separately from fulfillment
            handlers in production use.
        """
        if self.state != "pending":
            return []
        self.state = "rejected"
        self.rejection_reason = reason
        return list(self.then_callbacks)


# ---------------------------------------------------------------------------
# 5. AsyncAwaitDesugaring
# ---------------------------------------------------------------------------


class AsyncAwaitDesugaring:
    """Desugaring of ``async``/``await`` into explicit Promise chains.

    An ``async`` function is syntactic sugar for a function that wraps its
    body in a Promise.  Each ``await`` expression suspends the current
    execution context and schedules the *continuation* — everything after
    the ``await`` — as a microtask to be resumed when the awaited Promise
    settles.

    This class makes that transformation explicit so each suspension point
    can be reasoned about in the event-loop site algebra.
    """

    def desugar_async_function(
        self,
        func_name: str,
        await_points: list[str],
    ) -> list[tuple[str, str]]:
        """Model an ``async`` function as an explicit Promise continuation chain.

        The function body is split into ``len(await_points) + 1`` segments:

        * **Segment 0** — code before the first ``await`` (runs synchronously
          in the caller's task).
        * **Segment k** — the continuation after the *k*-th ``await``
          (scheduled as a microtask when that awaited Promise settles).

        Parameters
        ----------
        func_name:
            The name of the ``async`` function being desugared.
        await_points:
            An ordered list of labels for each ``await`` expression in the
            function body, e.g. ``["await_fetch", "await_json"]``.

        Returns
        -------
        list[tuple[str, str]]
            A list of ``(continuation_id, suspended_at)`` pairs.
            ``suspended_at`` is the await-point label at which execution was
            suspended *before* the continuation was scheduled.  The first
            entry uses ``"<entry>"`` because segment 0 begins at function
            entry with no prior suspension.
        """
        continuations: list[tuple[str, str]] = []

        # Segment 0 — runs immediately, not yet suspended by any await.
        continuations.append((f"{func_name}::seg_0", "<entry>"))

        for idx, await_label in enumerate(await_points, start=1):
            continuations.append((f"{func_name}::seg_{idx}", await_label))

        return continuations

    def visualize_suspension_chain(
        self,
        func_name: str,
        continuations: list[tuple[str, str]],
    ) -> str:
        """Render the suspension chain as ASCII art.

        Produces a diagram like::

            async function greet()
            ┌─────────────────────────────────────────────┐
            │  seg_0  [entry]                             │
            └─────────────────────┬───────────────────────┘
                                  │ await: await_fetch
                                  ▼
            ┌─────────────────────────────────────────────┐
            │  seg_1  [resumes here]                      │
            └─────────────────────────────────────────────┘ (function returns Promise)

        Parameters
        ----------
        func_name:
            The name of the ``async`` function.
        continuations:
            The list of ``(continuation_id, suspended_at)`` pairs as
            returned by :meth:`desugar_async_function`.

        Returns
        -------
        str
            A multi-line ASCII diagram of the suspension chain.
        """
        if not continuations:
            return f"async function {func_name}()\n  (no continuations)"

        width = 47
        bar = "─" * (width - 2)
        mid = (width - 2) // 2 - 1  # column of the vertical pipe
        lines: list[str] = [f"async function {func_name}()"]

        for i, (cont_id, suspended_at) in enumerate(continuations):
            seg_label = cont_id.split("::")[-1]
            entry_note = "entry" if suspended_at == "<entry>" else "resumes here"
            inner = f"  {seg_label}  [{entry_note}]".ljust(width - 2)

            lines.append(f"┌{bar}┐")
            lines.append(f"│{inner}│")

            if i < len(continuations) - 1:
                _, next_await = continuations[i + 1]
                # Bottom border with a break for the continuation arrow.
                left_bar = "─" * mid
                right_bar = "─" * (width - 2 - mid - 1)
                lines.append(f"└{left_bar}┬{right_bar}┘")
                pad = " " * mid
                lines.append(f"{pad} │ await: {next_await}")
                lines.append(f"{pad} ▼")
            else:
                lines.append(f"└{bar}┘ (function returns Promise)")

        return "\n".join(lines)
