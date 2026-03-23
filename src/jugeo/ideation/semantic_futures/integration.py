"""
jugeo.ideation.semantic_futures.integration
=============================================

Integration layer connecting the *Semantic Futures* subsystem to the broader
JuGeo ideation pipeline.

Overview
--------
This module provides two complementary concerns:

1. **Pub/Sub Event Bus** — a lightweight, synchronous event bus
   (:class:`FuturesEventBus`) that decouples semantic-future producers
   (generators, search algorithms, archivers) from consumers (schedulers,
   novelty checkers, UI copilots).  Every state change that matters to
   external components is published as a :class:`FutureEvent`.

2. **Integration Adapters** — :class:`SemanticFuturesIntegration` wires a
   live scheduler and a novelty searcher into the event bus, forwarding
   relevant events in both directions.  :class:`IntegrationHealthCheck`
   probes all connected components and aggregates an overall
   :class:`IntegrationStatus`.  :class:`CopilotFuturesAdvisor` turns raw
   :class:`~jugeo.ideation.semantic_futures.models.IdeationState` objects
   into human-readable guidance for an AI copilot or dashboard.

Event Flow
----------
The typical lifecycle of a semantic-future search session::

    Scheduler
        |  SCHEDULER_CONNECTED
        v
    FuturesEventBus ─── FUTURE_GENERATED ──> NoveltySearcher
        |                                         |
        |  SEARCH_COMPLETED <─────────────────────┘
        |
        ├─ FUTURE_ARCHIVED (each archived future)
        ├─ FUTURE_SELECTED (when user/scheduler picks a future)
        ├─ STATE_ADVANCED   (after each search step)
        └─ BUDGET_EXHAUSTED (when allocation is spent)

Threading Model
---------------
The event bus is **not** thread-safe by design; use it from a single event
loop or protect with an external lock if needed.

Usage Example
-------------
.. code-block:: python

    from jugeo.ideation.semantic_futures.integration import (
        FuturesEventBus, EventKind, SemanticFuturesIntegration,
        CopilotFuturesAdvisor, IntegrationHealthCheck,
    )

    bus = FuturesEventBus()
    integration = SemanticFuturesIntegration(bus=bus)

    # Subscribe to all completed searches
    def on_search_done(event):
        print("search finished:", event.payload)

    sub_id = bus.subscribe(EventKind.SEARCH_COMPLETED, on_search_done)

    # Connect live components
    integration.connect_to_scheduler(my_scheduler)
    integration.connect_to_novelty_searcher(my_searcher)

    # Run search …
    result = my_algorithm.run(state)
    integration.on_search_completed(result)

    # Health check
    checker = IntegrationHealthCheck(bus=bus)
    healths = checker.check_all(scheduler=my_scheduler, searcher=my_searcher)
    print(checker.report(healths))

    # Copilot advisory
    advisor = CopilotFuturesAdvisor(bus=bus)
    print(advisor.full_advisory(state))
"""
from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from enum import Enum
from dataclasses import dataclass, field
from typing import Callable, Optional

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cross-module imports (guarded)
# ---------------------------------------------------------------------------
try:
    from jugeo.ideation.ideas import IdeaProposal
    from jugeo.ideation.regimes import IdeationRegime
    from jugeo.ideation.novelty import NoveltyScore
    from jugeo.ideation.scheduling import IdeationSchedule
except ImportError:
    IdeaProposal = None
    IdeationRegime = None
    NoveltyScore = None
    IdeationSchedule = None

try:
    from jugeo.ideation.semantic_futures.models import (
        SemanticFuture,
        FutureState,
        PurposeFunction,
        IdeationState,
        FutureValuation,
    )
    from jugeo.ideation.semantic_futures.algorithms import (
        FutureSearchAlgorithm,
        SearchResult,
        SearchConfig,
    )
    from jugeo.ideation.semantic_futures.manifest import SemanticFuturesManifest
except ImportError:
    SemanticFuture = None  # type: ignore[assignment,misc]
    FutureState = None  # type: ignore[assignment,misc]
    PurposeFunction = None  # type: ignore[assignment,misc]
    IdeationState = None  # type: ignore[assignment,misc]
    FutureValuation = None  # type: ignore[assignment,misc]
    FutureSearchAlgorithm = None  # type: ignore[assignment,misc]
    SearchResult = None  # type: ignore[assignment,misc]
    SearchConfig = None  # type: ignore[assignment,misc]
    SemanticFuturesManifest = None  # type: ignore[assignment,misc]

__all__ = [
    # Enums
    "EventKind",
    "IntegrationStatus",
    # Dataclasses
    "FutureEvent",
    "EventSubscription",
    "ComponentHealth",
    # Classes
    "FuturesEventBus",
    "IntegrationHealthCheck",
    "CopilotFuturesAdvisor",
    "SemanticFuturesIntegration",
    # Helpers
    "_make_event_id",
    "_format_payload",
    "_truncate",
]


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _make_event_id() -> str:
    """Return a short, unique event identifier.

    Uses :func:`uuid.uuid4` and takes the first 12 hex characters to keep
    identifiers readable in log output::

        >>> eid = _make_event_id()
        >>> len(eid) == 12
        True
    """
    return uuid.uuid4().hex[:12]


def _format_payload(payload: dict) -> str:
    """Render *payload* as a compact key=value string for logging.

    Only the first 5 keys are shown; excess keys are noted with an ellipsis::

        >>> _format_payload({"a": 1, "b": 2})
        'a=1 b=2'
    """
    items = list(payload.items())[:5]
    parts = [f"{k}={v!r}" for k, v in items]
    if len(payload) > 5:
        parts.append(f"…+{len(payload) - 5} more")
    return " ".join(parts)


def _truncate(s: str, max_len: int = 80) -> str:
    """Truncate *s* to *max_len* characters, appending ``…`` if shortened.

    :param s: The string to truncate.
    :param max_len: Maximum allowed length (default 80).
    :returns: Truncated string.

    Example::

        >>> _truncate("hello world", 5)
        'hell…'
    """
    if max_len <= 0:
        return ""
    if len(s) <= max_len:
        return s
    return s[: max_len - 1] + "…"


# ---------------------------------------------------------------------------
# EventKind
# ---------------------------------------------------------------------------

class EventKind(str, Enum):
    """Enumeration of all event types published on :class:`FuturesEventBus`.

    Each variant corresponds to a distinct lifecycle transition in the
    semantic-futures pipeline.  Subscribers may register for individual
    kinds or iterate over :pyattr:`EventKind.__members__` to subscribe to
    all.

    Variants
    --------
    FUTURE_GENERATED
        A new :class:`~jugeo.ideation.semantic_futures.models.SemanticFuture`
        has been created by a generator or expander.
    FUTURE_ARCHIVED
        A future has been moved to the long-term archive (budget spent or
        explicitly archived by the scheduler).
    FUTURE_SELECTED
        The user or scheduler has chosen a future as the next ideation target.
    STATE_ADVANCED
        The :class:`~jugeo.ideation.semantic_futures.models.IdeationState`
        has advanced by one search step.
    BUDGET_EXHAUSTED
        The remaining ideation budget has reached zero.
    SEARCH_COMPLETED
        A :class:`~jugeo.ideation.semantic_futures.algorithms.SearchResult`
        is ready.
    HEALTH_CHECK
        A periodic or on-demand health probe has been completed.
    SCHEDULER_CONNECTED
        A scheduler has been wired into the integration layer.
    NOVELTY_CONNECTED
        A novelty searcher has been wired into the integration layer.
    """

    FUTURE_GENERATED = "future_generated"
    FUTURE_ARCHIVED = "future_archived"
    FUTURE_SELECTED = "future_selected"
    STATE_ADVANCED = "state_advanced"
    BUDGET_EXHAUSTED = "budget_exhausted"
    SEARCH_COMPLETED = "search_completed"
    HEALTH_CHECK = "health_check"
    SCHEDULER_CONNECTED = "scheduler_connected"
    NOVELTY_CONNECTED = "novelty_connected"
    SEARCHER_CONNECTED = "novelty_connected"


# ---------------------------------------------------------------------------
# FutureEvent
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class FutureEvent:
    """An immutable record of a single event on the semantic-futures bus.

    :param event_id: Unique identifier (see :func:`_make_event_id`).
    :param kind: The :class:`EventKind` of this event.
    :param payload: Arbitrary key-value metadata attached to this event.
    :param timestamp: UTC datetime when the event was created.
    :param source: Human-readable name of the component that produced it.

    Serialisation
    -------------
    Use :meth:`to_dict` / :meth:`from_dict` for JSON-compatible round-trips.
    """

    event_id: str
    kind: EventKind
    payload: dict
    timestamp: datetime
    source: str

    def to_dict(self) -> dict:
        """Serialise to a JSON-compatible dictionary.

        :returns: Dict with string keys and JSON-safe values.
        """
        return {
            "event_id": self.event_id,
            "kind": self.kind.value,
            "payload": self.payload,
            "timestamp": self.timestamp.isoformat(),
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: dict) -> FutureEvent:
        """Deserialise from a dictionary produced by :meth:`to_dict`.

        :param data: Dictionary with keys matching :meth:`to_dict` output.
        :returns: Reconstructed :class:`FutureEvent`.
        :raises KeyError: If a required key is missing.
        :raises ValueError: If the *kind* value is not a valid :class:`EventKind`.
        """
        return cls(
            event_id=data["event_id"],
            kind=EventKind(data["kind"]),
            payload=dict(data.get("payload", {})),
            timestamp=datetime.fromisoformat(data["timestamp"]),
            source=data["source"],
        )

    def __str__(self) -> str:  # noqa: D105
        return (
            f"FutureEvent({self.kind.value} id={self.event_id} "
            f"src={self.source} at={self.timestamp.strftime('%H:%M:%S')} "
            f"payload={_format_payload(self.payload)})"
        )


# ---------------------------------------------------------------------------
# EventSubscription
# ---------------------------------------------------------------------------

@dataclass(frozen=True, init=False)
class EventSubscription:
    """Metadata record created when a handler is registered on the bus.

    :param subscription_id: Unique identifier returned by
        :meth:`FuturesEventBus.subscribe`.
    :param event_kind: Which :class:`EventKind` this subscription listens to.
    :param handler_name: ``__qualname__`` of the registered callable.
    :param created_at: UTC datetime of registration.
    """

    subscription_id: str
    event_kind: EventKind
    handler_name: str
    created_at: datetime

    def __init__(
        self,
        subscription_id: str,
        event_kind: EventKind | None = None,
        handler_name: str = "",
        created_at: datetime | None = None,
        *,
        kind: EventKind | None = None,
    ) -> None:
        resolved_kind = event_kind if event_kind is not None else kind
        if resolved_kind is None:
            raise TypeError("event_kind or kind is required")
        object.__setattr__(self, "subscription_id", subscription_id)
        object.__setattr__(self, "event_kind", resolved_kind)
        object.__setattr__(self, "handler_name", handler_name)
        object.__setattr__(self, "created_at", created_at or datetime.now())

    @property
    def kind(self) -> EventKind:
        return self.event_kind

    def to_dict(self) -> dict:
        """Serialise to a JSON-compatible dictionary."""
        return {
            "subscription_id": self.subscription_id,
            "event_kind": self.event_kind.value,
            "handler_name": self.handler_name,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> EventSubscription:
        """Deserialise from a dictionary produced by :meth:`to_dict`."""
        return cls(
            subscription_id=data["subscription_id"],
            event_kind=EventKind(data["event_kind"]),
            handler_name=data["handler_name"],
            created_at=datetime.fromisoformat(data["created_at"]),
        )


# ---------------------------------------------------------------------------
# FuturesEventBus
# ---------------------------------------------------------------------------

class FuturesEventBus:
    """Synchronous publish-subscribe event bus for the semantic-futures pipeline.

    Handlers are called in registration order.  If a handler raises an
    exception it is caught and logged via :meth:`_handle_error`; remaining
    handlers still execute.

    The bus keeps an in-memory history of all published events (capped at
    *history_limit* entries) so that late subscribers can inspect past events.

    :param history_limit: Maximum number of events retained in
        :attr:`_history`.  Defaults to 1000.

    Example::

        bus = FuturesEventBus()

        def my_handler(event: FutureEvent) -> None:
            print(event)

        sub_id = bus.subscribe(EventKind.FUTURE_GENERATED, my_handler)
        bus.publish_kind(EventKind.FUTURE_GENERATED, {"future_id": "abc"})
        bus.unsubscribe(sub_id)
    """

    def __init__(self, history_limit: int = 1000) -> None:
        self._history_limit: int = history_limit
        # kind -> list of (subscription_id, handler)
        self._handlers: dict[EventKind, list[tuple[str, Callable]]] = defaultdict(list)
        # subscription_id -> EventSubscription
        self._subscriptions: dict[str, EventSubscription] = {}
        self._history: list[FutureEvent] = []
        _log.debug("FuturesEventBus initialised (history_limit=%d)", history_limit)

    # ------------------------------------------------------------------
    # Subscription management
    # ------------------------------------------------------------------

    def subscribe(
        self,
        kind: EventKind,
        handler: Callable[[FutureEvent], None],
    ) -> str:
        """Register *handler* to be called whenever an event of *kind* is published.

        :param kind: The :class:`EventKind` to listen for.
        :param handler: A callable accepting a single :class:`FutureEvent`.
        :returns: A *subscription_id* string that can be passed to
            :meth:`unsubscribe`.

        The same callable may be registered multiple times; each registration
        produces a distinct subscription ID.
        """
        sub_id = _make_event_id()
        self._handlers[kind].append((sub_id, handler))
        sub = EventSubscription(
            subscription_id=sub_id,
            event_kind=kind,
            handler_name=getattr(handler, "__qualname__", repr(handler)),
            created_at=datetime.now(tz=timezone.utc),
        )
        self._subscriptions[sub_id] = sub
        _log.debug(
            "Subscribed %s to %s (sub_id=%s)",
            sub.handler_name,
            kind.value,
            sub_id,
        )
        return sub_id

    def unsubscribe(self, subscription_id: str) -> bool:
        """Remove the subscription identified by *subscription_id*.

        :param subscription_id: The ID returned by :meth:`subscribe`.
        :returns: ``True`` if the subscription was found and removed,
            ``False`` otherwise.
        """
        if subscription_id not in self._subscriptions:
            _log.warning("unsubscribe: unknown subscription_id=%s", subscription_id)
            return False
        sub = self._subscriptions.pop(subscription_id)
        handlers = self._handlers[sub.event_kind]
        self._handlers[sub.event_kind] = [
            (sid, h) for sid, h in handlers if sid != subscription_id
        ]
        _log.debug("Unsubscribed %s (sub_id=%s)", sub.handler_name, subscription_id)
        return True

    # ------------------------------------------------------------------
    # Publishing
    # ------------------------------------------------------------------

    def publish(self, event: FutureEvent) -> None:
        """Publish *event* to all registered handlers for ``event.kind``.

        The event is appended to the internal history before dispatching.
        Handler exceptions are swallowed and logged; they do **not** propagate.

        :param event: The :class:`FutureEvent` to dispatch.
        """
        self._history.append(event)
        if len(self._history) > self._history_limit:
            self._history = self._history[-self._history_limit :]

        handlers = self._handlers.get(event.kind, [])
        _log.debug(
            "Publishing %s to %d handler(s): %s",
            event.kind.value,
            len(handlers),
            _truncate(_format_payload(event.payload), 120),
        )
        for sub_id, handler in list(handlers):
            try:
                handler(event)
            except Exception as exc:  # noqa: BLE001
                sub = self._subscriptions.get(sub_id)
                name = sub.handler_name if sub else sub_id
                self._handle_error(name, exc, event)

    def publish_kind(
        self,
        kind: EventKind,
        payload: dict,
        source: str = "system",
    ) -> FutureEvent:
        """Convenience wrapper: build a :class:`FutureEvent` and publish it.

        :param kind: The :class:`EventKind`.
        :param payload: Arbitrary metadata dict.
        :param source: Identifier of the publishing component.
        :returns: The :class:`FutureEvent` that was published.
        """
        event = FutureEvent(
            event_id=_make_event_id(),
            kind=kind,
            payload=payload,
            timestamp=datetime.now(tz=timezone.utc),
            source=source,
        )
        self.publish(event)
        return event

    # ------------------------------------------------------------------
    # History & introspection
    # ------------------------------------------------------------------

    def history(
        self,
        kind: Optional[EventKind] = None,
        limit: int = 100,
    ) -> list[FutureEvent]:
        """Return recent events from the internal history.

        :param kind: If given, filter to events of this :class:`EventKind`.
        :param limit: Maximum number of events to return (most-recent first).
        :returns: List of :class:`FutureEvent` objects.
        """
        events = self._history if kind is None else [
            e for e in self._history if e.kind == kind
        ]
        return list(reversed(events[-limit:]))

    def clear_history(self) -> None:
        """Discard all stored events from the history buffer."""
        self._history.clear()
        _log.debug("Event history cleared.")

    def subscriber_count(self, kind: Optional[EventKind] = None) -> int:
        """Return the number of active subscriptions.

        :param kind: If given, count only subscriptions for this
            :class:`EventKind`.
        :returns: Integer count.
        """
        if kind is not None:
            return len(self._handlers.get(kind, []))
        return sum(len(v) for v in self._handlers.values())

    # ------------------------------------------------------------------
    # Error handling
    # ------------------------------------------------------------------

    def _handle_error(
        self,
        handler_name: str,
        error: Exception,
        event: FutureEvent,
    ) -> None:
        """Log a handler exception without re-raising it.

        Subclasses may override this to forward errors to an error tracker.

        :param handler_name: The ``__qualname__`` of the failing handler.
        :param error: The exception that was raised.
        :param event: The :class:`FutureEvent` being dispatched when the
            error occurred.
        """
        _log.error(
            "Handler %r raised %s while processing event %s(%s): %s",
            handler_name,
            type(error).__name__,
            event.kind.value,
            event.event_id,
            error,
            exc_info=True,
        )


# ---------------------------------------------------------------------------
# IntegrationStatus
# ---------------------------------------------------------------------------

class IntegrationStatus(str, Enum):
    """High-level health status for an integrated component.

    Variants
    --------
    CONNECTED
        Component is reachable and responding normally.
    DISCONNECTED
        Component has not been wired in, or has cleanly disconnected.
    DEGRADED
        Component is reachable but responding slowly or returning errors.
    UNKNOWN
        Status has not yet been determined (e.g. before first health check).
    """

    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    DEGRADED = "degraded"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# ComponentHealth
# ---------------------------------------------------------------------------

@dataclass(frozen=True, init=False)
class ComponentHealth:
    """Snapshot health record for a single integrated component.

    :param component_name: Human-readable component identifier.
    :param status: Current :class:`IntegrationStatus`.
    :param last_checked: UTC datetime of the most recent health probe.
    :param details: Free-text description of the health finding.
    :param latency_ms: Round-trip latency of the last probe in milliseconds.
        ``-1.0`` indicates that no probe has been performed.

    Example::

        health = ComponentHealth(
            component_name="scheduler",
            status=IntegrationStatus.CONNECTED,
            last_checked=datetime.now(tz=timezone.utc),
            details="Responded in 2.1 ms",
            latency_ms=2.1,
        )
        assert health.is_healthy()
    """

    component_name: str
    status: IntegrationStatus
    last_checked: datetime
    details: str
    latency_ms: float = -1.0

    def __init__(
        self,
        component_name: str | None = None,
        status: IntegrationStatus = IntegrationStatus.UNKNOWN,
        last_checked: datetime | None = None,
        details: str = "",
        latency_ms: float = -1.0,
        *,
        name: str | None = None,
    ) -> None:
        object.__setattr__(self, "component_name", component_name or name or "")
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "last_checked", last_checked or datetime.now())
        object.__setattr__(self, "details", details)
        object.__setattr__(self, "latency_ms", float(latency_ms))

    @property
    def name(self) -> str:
        return self.component_name

    def is_healthy(self) -> bool:
        """Return ``True`` iff the component status is
        :attr:`~IntegrationStatus.CONNECTED`.
        """
        return self.status == IntegrationStatus.CONNECTED

    def to_dict(self) -> dict:
        """Serialise to a JSON-compatible dictionary."""
        return {
            "name": self.component_name,
            "component_name": self.component_name,
            "status": self.status.value,
            "last_checked": self.last_checked.isoformat(),
            "details": self.details,
            "latency_ms": self.latency_ms,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ComponentHealth:
        """Deserialise from a dictionary produced by :meth:`to_dict`."""
        return cls(
            component_name=data.get("component_name", data.get("name", "")),
            status=IntegrationStatus(data["status"]),
            last_checked=datetime.fromisoformat(data["last_checked"]),
            details=data["details"],
            latency_ms=float(data.get("latency_ms", -1.0)),
        )


# ---------------------------------------------------------------------------
# IntegrationHealthCheck
# ---------------------------------------------------------------------------

class IntegrationHealthCheck:
    """Probes connected components and produces a consolidated health report.

    :param bus: Optional :class:`FuturesEventBus` to which
        :attr:`~EventKind.HEALTH_CHECK` events are published after each
        :meth:`check_all` call.

    The checker uses duck-typing to inspect arbitrary component objects:
    it looks for ``is_alive()``, ``ping()``, ``health()``, or ``status``
    attributes to determine whether a component is healthy.
    """

    def __init__(self, bus: Optional[FuturesEventBus] = None) -> None:
        self._bus = bus
        self._components: dict[str, ComponentHealth] = {}
        _log.debug("IntegrationHealthCheck initialised.")

    def register_component(self, name: str, health: ComponentHealth) -> None:
        self._components[name] = health

    # ------------------------------------------------------------------
    # Individual checks
    # ------------------------------------------------------------------

    def check_scheduler(self, scheduler: object) -> ComponentHealth:
        """Probe *scheduler* and return a :class:`ComponentHealth`.

        Checks for ``is_alive()``, ``ping()``, or a ``status`` attribute.

        :param scheduler: The scheduler object to probe.
        :returns: :class:`ComponentHealth` reflecting current state.
        """
        return self._probe("scheduler", scheduler)

    def check_novelty_searcher(self, searcher: object) -> ComponentHealth:
        """Probe *searcher* and return a :class:`ComponentHealth`.

        :param searcher: The novelty-searcher object to probe.
        :returns: :class:`ComponentHealth` reflecting current state.
        """
        return self._probe("novelty_searcher", searcher)

    def _probe(self, name: str, component: object) -> ComponentHealth:
        """Generic duck-typed health probe for *component*.

        :meta private:
        """
        import time

        if component is None:
            return ComponentHealth(
                component_name=name,
                status=IntegrationStatus.DISCONNECTED,
                last_checked=datetime.now(tz=timezone.utc),
                details="Component not connected.",
                latency_ms=-1.0,
            )
        t0 = time.perf_counter()
        try:
            if hasattr(component, "ping"):
                component.ping()  # type: ignore[union-attr]
            elif hasattr(component, "is_alive"):
                component.is_alive()  # type: ignore[union-attr]
            elif hasattr(component, "health"):
                component.health()  # type: ignore[union-attr]
            latency_ms = (time.perf_counter() - t0) * 1000.0
            status = IntegrationStatus.CONNECTED
            details = f"Responded in {latency_ms:.2f} ms."
            if latency_ms > 500:
                status = IntegrationStatus.DEGRADED
                details = f"High latency: {latency_ms:.1f} ms."
        except Exception as exc:  # noqa: BLE001
            latency_ms = (time.perf_counter() - t0) * 1000.0
            status = IntegrationStatus.DEGRADED
            details = f"Probe raised {type(exc).__name__}: {exc}"
            _log.warning("Health probe for %r failed: %s", name, exc)
        return ComponentHealth(
            component_name=name,
            status=status,
            last_checked=datetime.now(tz=timezone.utc),
            details=details,
            latency_ms=latency_ms,
        )

    # ------------------------------------------------------------------
    # Aggregate checks
    # ------------------------------------------------------------------

    def check_all(self, **components: object) -> dict[str, ComponentHealth]:
        """Probe every component passed as a keyword argument.

        :param components: Mapping of component names to component objects.
        :returns: Dict mapping component names to :class:`ComponentHealth`.

        Example::

            healths = checker.check_all(
                scheduler=my_scheduler,
                searcher=my_searcher,
            )
        """
        if components:
            healths = {name: self._probe(name, component) for name, component in components.items()}
            self._components.update(healths)
        else:
            healths = dict(self._components)
        overall = self.overall_health(healths)
        if self._bus is not None:
            self._bus.publish_kind(
                EventKind.HEALTH_CHECK,
                {
                    "overall": overall.value,
                    "components": {n: h.status.value for n, h in healths.items()},
                },
                source="IntegrationHealthCheck",
            )
        return healths

    def overall_health(self, healths: dict[str, ComponentHealth] | None = None) -> IntegrationStatus:
        """Compute the aggregate :class:`IntegrationStatus` from *healths*.

        Rules (in priority order):

        1. If any component is ``DEGRADED`` → ``DEGRADED``.
        2. If all components are ``CONNECTED`` → ``CONNECTED``.
        3. If all components are ``DISCONNECTED`` → ``DISCONNECTED``.
        4. Otherwise → ``UNKNOWN``.

        :param healths: Dict produced by :meth:`check_all`.
        :returns: A single :class:`IntegrationStatus`.
        """
        healths = self._components if healths is None else healths
        if not healths:
            return IntegrationStatus.UNKNOWN
        statuses = {h.status for h in healths.values()}
        if IntegrationStatus.DEGRADED in statuses:
            return IntegrationStatus.DEGRADED
        if statuses == {IntegrationStatus.CONNECTED}:
            return IntegrationStatus.CONNECTED
        if statuses == {IntegrationStatus.DISCONNECTED}:
            return IntegrationStatus.DISCONNECTED
        if IntegrationStatus.CONNECTED in statuses and IntegrationStatus.DISCONNECTED in statuses:
            return IntegrationStatus.DEGRADED
        return IntegrationStatus.UNKNOWN

    def report(self, healths: dict[str, ComponentHealth] | None = None) -> str:
        """Format *healths* as a multi-line human-readable report string.

        :param healths: Dict produced by :meth:`check_all`.
        :returns: Formatted report string.
        """
        healths = self._components if healths is None else healths
        overall = self.overall_health(healths)
        lines = [
            f"── Integration Health Report ──────────────────────",
            f"   Overall : {overall.value.upper()}",
            f"   Checked : {datetime.now(tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
            "",
        ]
        for name, health in sorted(healths.items()):
            icon = "✓" if health.is_healthy() else "✗"
            latency = (
                f"{health.latency_ms:.1f} ms"
                if health.latency_ms >= 0
                else "n/a"
            )
            lines.append(
                f"  [{icon}] {name:<24} {health.status.value:<14} {latency}"
            )
            if health.details:
                lines.append(f"       {_truncate(health.details, 70)}")
        lines.append("──────────────────────────────────────────────────")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# CopilotFuturesAdvisor
# ---------------------------------------------------------------------------

class CopilotFuturesAdvisor:
    """Translates raw :class:`IdeationState` data into copilot-friendly text.

    Intended for use by AI assistants, dashboards, or CLI outputs that need
    natural-language summaries of the current semantic-futures search session.

    :param bus: Optional :class:`FuturesEventBus` used for context (e.g.
        to check recent events when formulating advice).

    All methods gracefully handle ``None`` inputs and missing attributes,
    returning a descriptive placeholder string rather than raising.
    """

    def __init__(self, bus: Optional[FuturesEventBus] = None, state: object | None = None) -> None:
        self._bus = bus
        self._default_state = state
        _log.debug("CopilotFuturesAdvisor initialised.")

    # ------------------------------------------------------------------
    # Public advisory methods
    # ------------------------------------------------------------------

    def top_futures_summary(self, state: object | None = None, n: int = 5) -> str:
        """Return a human-readable summary of the top-*n* valued futures.

        :param state: An :class:`IdeationState` (or duck-typed equivalent).
        :param n: Number of top futures to include.
        :returns: Formatted multi-line string.
        """
        state = self._default_state if state is None else state
        if state is None:
            return "No ideation state available."
        frontier = getattr(state, "frontier", None) or getattr(state, "reachable_futures", None) or []
        if not frontier:
            return "The frontier is empty — no futures have been generated yet."
        top = sorted(
            frontier,
            key=lambda f: (
                f.value() if callable(getattr(f, "value", None)) else getattr(getattr(f, "value", None), "total", 0.0)
            ),
            reverse=True,
        )[:n]
        lines = [f"Top {min(n, len(top))} semantic futures by value:"]
        for i, f in enumerate(top, 1):
            lines.append(f"  {i}. {self.format_future(f)}")
        return "\n".join(lines)

    def next_step_advice(self, state: object | None = None) -> str:
        """Return a short actionable recommendation for the next search step.

        :param state: An :class:`IdeationState` (or duck-typed equivalent).
        :returns: One-sentence advisory string.
        """
        state = self._default_state if state is None else state
        if state is None:
            return "Initialise an IdeationState to begin searching."
        budget = getattr(state, "remaining_budget", None)
        if budget is None:
            budget = getattr(state, "budget_remaining", None)
        frontier = getattr(state, "frontier", None) or getattr(state, "reachable_futures", None) or []
        step = getattr(state, "step", None)
        if budget is not None and budget <= 0:
            return "Budget is exhausted. Archive the best futures and start a new session."
        if not frontier:
            return "Generate initial futures using a FutureGenerator before searching."
        if budget is not None and budget < 5:
            return (
                f"Budget is critically low ({budget} steps remaining). "
                "Select the best future now."
            )
        return (
            f"Continue search (step {step}, {len(frontier)} futures on frontier, "
            f"budget={budget}). Expand the highest-value future next."
        )

    def budget_warning(self, state: object | None = None) -> Optional[str]:
        """Return a warning string if budget is low, otherwise ``None``.

        :param state: An :class:`IdeationState` (or duck-typed equivalent).
        :returns: Warning string or ``None``.
        """
        state = self._default_state if state is None else state
        budget = getattr(state, "remaining_budget", None)
        if budget is None and state is not None:
            budget = getattr(state, "budget_remaining", None)
        if budget is None:
            return None
        if budget <= 0:
            return "⚠ Budget exhausted: ideation search has halted."
        if budget <= 3:
            return f"⚠ Budget critically low: {budget} step(s) remaining."
        if budget <= 5:
            return f"⚡ Budget low: {budget} step(s) remaining."
        return None

    def archive_summary(self, state: object | None = None) -> str:
        """Summarise the contents of the ideation archive.

        :param state: An :class:`IdeationState` (or duck-typed equivalent).
        :returns: Human-readable archive summary.
        """
        state = self._default_state if state is None else state
        archive = getattr(state, "archive", None) or []
        if not archive:
            return "Archive is empty."
        lines = [f"Archive contains {len(archive)} future(s):"]
        for f in archive[:5]:
            lines.append(f"  • {self.format_future(f)}")
        if len(archive) > 5:
            lines.append(f"  … and {len(archive) - 5} more.")
        return "\n".join(lines)

    def format_future(self, f: object) -> str:
        """Format a single :class:`SemanticFuture` as a one-line string.

        :param f: A :class:`SemanticFuture` (or duck-typed equivalent).
        :returns: Compact string representation.
        """
        if f is None:
            return "<null future>"
        fid = getattr(f, "future_id", "?")
        label = getattr(f, "label", None) or getattr(f, "delta", None) or getattr(f, "description", "")
        state = getattr(f, "state", None)
        state_str = state.value if hasattr(state, "value") else str(state or "?")
        val = getattr(f, "value", None)
        total = f"{val():.3f}" if callable(val) else (f"{getattr(val, 'total', 0.0):.3f}" if val else "?")
        return _truncate(
            f"[{fid}] {label!r} state={state_str} V={total}",
            100,
        )

    def full_advisory(self, state: object | None = None) -> str:
        """Produce a comprehensive copilot message for *state*.

        Combines :meth:`top_futures_summary`, :meth:`next_step_advice`,
        :meth:`budget_warning`, and :meth:`archive_summary` into a single
        formatted block.

        :param state: An :class:`IdeationState` (or duck-typed equivalent).
        :returns: Multi-section advisory string.
        """
        state = self._default_state if state is None else state
        sections: list[str] = []
        sections.append("═══ Semantic Futures Advisory ════════════════════")
        sections.append(self.top_futures_summary(state))
        sections.append("")
        sections.append("── Next Step ──────────────────────────────────────")
        sections.append(self.next_step_advice(state))
        warning = self.budget_warning(state)
        if warning:
            sections.append("")
            sections.append(warning)
        sections.append("")
        sections.append("── Archive ────────────────────────────────────────")
        sections.append(self.archive_summary(state))
        sections.append("═══════════════════════════════════════════════════")
        return "\n".join(sections)


# ---------------------------------------------------------------------------
# SemanticFuturesIntegration
# ---------------------------------------------------------------------------

class SemanticFuturesIntegration:
    """Top-level wiring adapter for the semantic-futures subsystem.

    Manages references to an external scheduler and novelty searcher, routes
    events through the :class:`FuturesEventBus`, and exposes push/pull
    operations that bridge the semantic-futures pipeline with the rest of the
    JuGeo ideation engine.

    :param bus: :class:`FuturesEventBus` to use.  A new bus is created if
        ``None`` is passed.

    Lifecycle::

        integration = SemanticFuturesIntegration()
        integration.connect_to_scheduler(scheduler)
        integration.connect_to_novelty_searcher(searcher)
        # … run ideation …
        integration.disconnect_all()
    """

    def __init__(self, bus: Optional[FuturesEventBus] = None, event_bus: Optional[FuturesEventBus] = None) -> None:
        bus = bus if bus is not None else event_bus
        self._bus: FuturesEventBus = bus if bus is not None else FuturesEventBus()
        self._scheduler: object = None
        self._novelty_searcher: object = None
        _log.debug("SemanticFuturesIntegration initialised.")

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def connect_to_scheduler(self, scheduler: object) -> None:
        """Wire *scheduler* into the integration layer.

        Stores a reference and publishes a
        :attr:`~EventKind.SCHEDULER_CONNECTED` event on the bus.

        :param scheduler: Any object acting as a JuGeo ideation scheduler.
        """
        self._scheduler = scheduler
        name = type(scheduler).__name__ if scheduler is not None else "None"
        self._bus.publish_kind(
            EventKind.SCHEDULER_CONNECTED,
            {"scheduler_type": name},
            source="SemanticFuturesIntegration",
        )
        _log.info("Scheduler connected: %s", name)

    def connect_to_novelty_searcher(self, searcher: object) -> None:
        """Wire *searcher* into the integration layer.

        Stores a reference and publishes a
        :attr:`~EventKind.NOVELTY_CONNECTED` event on the bus.

        :param searcher: Any object acting as a JuGeo novelty searcher.
        """
        self._novelty_searcher = searcher
        name = type(searcher).__name__ if searcher is not None else "None"
        self._bus.publish_kind(
            EventKind.NOVELTY_CONNECTED,
            {"searcher_type": name},
            source="SemanticFuturesIntegration",
        )
        _log.info("Novelty searcher connected: %s", name)

    def disconnect_all(self) -> None:
        """Release all component references and clear the bus history.

        After calling this method, :meth:`status` will return
        ``DISCONNECTED`` for all components.
        """
        self._scheduler = None
        self._novelty_searcher = None
        self._bus.clear_history()
        _log.info("SemanticFuturesIntegration: all components disconnected.")

    # ------------------------------------------------------------------
    # Push / pull operations
    # ------------------------------------------------------------------

    def push_futures_to_archive(self, futures: list) -> None:
        """Publish a :attr:`~EventKind.FUTURE_ARCHIVED` event for each future.

        :param futures: List of :class:`SemanticFuture` (or duck-typed)
            objects to archive.
        """
        for f in futures:
            fid = getattr(f, "future_id", str(id(f)))
            label = getattr(f, "label", "")
            self._bus.publish_kind(
                EventKind.FUTURE_ARCHIVED,
                {"future_id": fid, "label": _truncate(label, 60)},
                source="SemanticFuturesIntegration",
            )
        _log.info("Pushed %d future(s) to archive event stream.", len(futures))

    def pull_current_state(self) -> Optional[object]:
        """Attempt to retrieve the current :class:`IdeationState` from the scheduler.

        Tries ``scheduler.current_state``, ``scheduler.state()``, or
        ``scheduler.get_state()`` in order.

        :returns: The current state object, or ``None`` if unavailable.
        """
        s = self._scheduler
        if s is None:
            _log.debug("pull_current_state: no scheduler connected.")
            return None
        for attr in ("current_state", "state", "get_state"):
            obj = getattr(s, attr, None)
            if obj is None:
                continue
            if callable(obj):
                try:
                    return obj()
                except Exception as exc:  # noqa: BLE001
                    _log.warning("pull_current_state via %s() failed: %s", attr, exc)
            else:
                return obj
        _log.warning("pull_current_state: scheduler has no recognised state accessor.")
        return None

    def on_search_completed(self, result: object) -> None:
        """Publish a :attr:`~EventKind.SEARCH_COMPLETED` event for *result*.

        :param result: A :class:`SearchResult` (or duck-typed equivalent).
        """
        payload: dict = {}
        for key in ("best_future", "iterations", "cost_spent", "frontier_size"):
            val = getattr(result, key, None)
            if val is not None:
                payload[key] = str(val)
        self._bus.publish_kind(
            EventKind.SEARCH_COMPLETED,
            payload,
            source="SemanticFuturesIntegration",
        )
        _log.info("Search completed event published: %s", _format_payload(payload))

    def on_future_selected(self, future: object) -> None:
        """Publish a :attr:`~EventKind.FUTURE_SELECTED` event for *future*.

        :param future: A :class:`SemanticFuture` (or duck-typed equivalent).
        """
        fid = getattr(future, "future_id", str(id(future)))
        label = getattr(future, "label", "")
        self._bus.publish_kind(
            EventKind.FUTURE_SELECTED,
            {"future_id": fid, "label": _truncate(label, 60)},
            source="SemanticFuturesIntegration",
        )
        _log.info("Future selected: %s (%s)", fid, _truncate(label, 40))

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def status(self) -> dict[str, IntegrationStatus]:
        """Return a dict mapping component names to their current status.

        Uses simple presence checks rather than live probes.  For live probes,
        use :class:`IntegrationHealthCheck`.

        :returns: Dict with keys ``"scheduler"`` and ``"novelty_searcher"``.
        """
        return {
            "scheduler": (
                IntegrationStatus.CONNECTED
                if self._scheduler is not None
                else IntegrationStatus.DISCONNECTED
            ),
            "novelty_searcher": (
                IntegrationStatus.CONNECTED
                if self._novelty_searcher is not None
                else IntegrationStatus.DISCONNECTED
            ),
        }

    # ------------------------------------------------------------------
    # Bus property (read-only access)
    # ------------------------------------------------------------------

    @property
    def bus(self) -> FuturesEventBus:
        """The :class:`FuturesEventBus` used by this integration instance."""
        return self._bus

    @property
    def scheduler(self) -> object:
        return self._scheduler

    @property
    def novelty_searcher(self) -> object:
        return self._novelty_searcher
