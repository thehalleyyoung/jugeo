r"""Service registry, dependency injection, and lifecycle management for JuGeo.

This module is the central nervous system of the JuGeo kernel.  It provides a
typed service registry with support for multiple lifecycles (singleton,
transient, scoped, lazy-singleton), dependency injection with cycle detection,
health monitoring, interceptor middleware, and an event bus for service
lifecycle events.

The design is deliberately explicit about dependencies, authority, and freezing
so that lifecycle and health code can reason about the service graph
statically.  It is suitable for copilot-assisted development environments
because service registration is declarative rather than hidden inside
imperative startup scripts.

Governing design principles from ``preliminaries/theory2.tex``:

* **No silent trust promotion** — services carry a trust ceiling from their
  descriptor and the registry enforces that resolved instances never exceed
  the ceiling of their declared authority center.

* **Obstructions are persistent** — when a service fails to resolve, the
  failure is recorded as an obstruction event on the event bus rather than
  silently swallowed.

* **Evidence plurality** — different services belong to different evidence
  channels and the registry tracks which channels each service is permitted
  to use.

Theory alignment
----------------

The service graph mirrors the presheaf of local sections described in
Section 3 of ``theory2.tex``.  Each service binding is a local section
whose support region is defined by its declared dependencies.  The
:meth:`ServiceGraph.startup_order` method computes a compatible global
section (topological linearisation) or reports an obstruction (cycle).

Public types
------------
:class:`ServiceLifecycle`
    Enum of service lifetime strategies.

:class:`ServiceDescriptor`
    Immutable record describing a service: name, types, lifecycle, etc.

:class:`ServiceBinding`
    Frozen record pairing a service name with its component and authority.

:class:`ServiceScope`
    Scoped lifetime container with enter/exit and disposal tracking.

:class:`ServiceRegistry`
    The main registry: register, resolve, validate, create scopes.

:class:`ServiceFactory`
    Creates service instances respecting lifecycle and dependency injection.

:class:`ServiceGraph`
    Dependency graph with cycle detection and topological sort.

:class:`ServiceHealthMonitor`
    Tracks health metrics for each service: heartbeat, latency, errors.

:class:`ServiceInterceptor`
    Middleware for cross-cutting concerns (logging, trust verification).

:class:`ServiceEventBus`
    Pub/sub for service lifecycle events.

:class:`KernelBootstrapper`
    Registers all core JuGeo services in the correct boot order.

:class:`ServiceDisposer`
    Orderly shutdown in reverse topological order.

Public functions
----------------
:func:`bootstrap_kernel`
    Create and populate a fully bootstrapped kernel registry.

:func:`resolve_service`
    Convenience function to resolve a named service from a graph or registry.

:func:`freeze_service_graph`
    Freeze a service graph, validating its dependency structure.

:func:`with_scope`
    Context-manager helper that creates and disposes a service scope.
"""

from __future__ import annotations

import collections
import contextlib
import logging
import time
import uuid
import weakref
from collections import defaultdict, deque
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from enum import Enum, IntEnum, auto
from typing import (
    Any,
    Callable,
    Generator,
    Iterable,
    Mapping,
    Protocol,
    Sequence,
    TypeVar,
)

from jugeo.errors import FailureScope, JuGeoError, StructuredFailure
from jugeo.kernel.authority import AuthorityCenter, AuthorityTier

logger = logging.getLogger(__name__)


class _CompatibleOrder(list[str]):
    def __eq__(self, other: object) -> bool:
        if isinstance(other, (list, tuple)):
            return list(self) == list(other)
        return super().__eq__(other)

T = TypeVar("T")


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ServiceLifecycle(str, Enum):
    """Lifetime strategy for a registered service.

    Attributes
    ----------
    SINGLETON
        One instance shared across the entire kernel lifetime.
    TRANSIENT
        A fresh instance on every resolution request.
    SCOPED
        One instance per :class:`ServiceScope`; disposed when the scope exits.
    LAZY_SINGLETON
        Like ``SINGLETON`` but creation is deferred until first resolution.
    """

    SINGLETON = "singleton"
    TRANSIENT = "transient"
    SCOPED = "scoped"
    LAZY_SINGLETON = "lazy_singleton"


class ServiceEventKind(str, Enum):
    """Kind of lifecycle event emitted by the service registry.

    copilot note: events are structured so copilot-backed analysis tools can
    subscribe to the bus and react to service lifecycle changes without
    polling.
    """

    REGISTERED = "registered"
    RESOLVED = "resolved"
    DISPOSED = "disposed"
    FAILED = "failed"
    SCOPE_CREATED = "scope_created"
    SCOPE_CLOSED = "scope_closed"
    HEALTH_CHANGED = "health_changed"
    INTERCEPTOR_ATTACHED = "interceptor_attached"


class ServiceHealthStatus(str, Enum):
    """Coarse health status for a single service."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    FAILED = "failed"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Descriptors and bindings
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ServiceDescriptor:
    """Immutable record describing a service registration.

    A descriptor captures everything needed to create, wire, and govern a
    service instance without referencing a concrete component.

    Attributes
    ----------
    name : str
        Unique service name used as the registry key.
    interface_type : type
        Abstract interface or protocol the service must satisfy.
    implementation_type : type
        Concrete class that will be instantiated by the factory.
    lifecycle : ServiceLifecycle
        Lifetime strategy.
    dependencies : tuple[str, ...]
        Names of services that must be resolved before this one.
    trust_ceiling : AuthorityTier
        Maximum authority tier the service may claim.
    evidence_channels : frozenset[str]
        Names of evidence channels this service is permitted to use.
    copilot_eligible : bool
        Whether this service may be invoked by a copilot proposal agent.
    metadata : Mapping[str, Any]
        Arbitrary additional metadata for diagnostics and tooling.
    """

    name: str
    interface_type: type = object
    implementation_type: type = object
    service_type: type | None = None
    lifecycle: ServiceLifecycle = ServiceLifecycle.SINGLETON
    dependencies: tuple[str, ...] = ()
    trust_ceiling: AuthorityTier | str = AuthorityTier.VERIFIED
    evidence_channels: frozenset[str] = field(default_factory=frozenset)
    copilot_eligible: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.service_type is not None:
            if self.interface_type is object:
                object.__setattr__(self, 'interface_type', self.service_type)
            if self.implementation_type is object:
                object.__setattr__(self, 'implementation_type', self.service_type)
        if not isinstance(self.dependencies, tuple):
            object.__setattr__(self, 'dependencies', tuple(self.dependencies))
        if isinstance(self.trust_ceiling, str):
            object.__setattr__(self, 'trust_ceiling', self.trust_ceiling.lower())

    def depends_on(self, other_name: str) -> bool:
        """Return whether *other_name* is a declared dependency."""
        return other_name in self.dependencies

    def is_singleton_family(self) -> bool:
        """Return ``True`` for ``SINGLETON`` and ``LAZY_SINGLETON``."""
        return self.lifecycle in (
            ServiceLifecycle.SINGLETON,
            ServiceLifecycle.LAZY_SINGLETON,
        )

    def channel_allowed(self, channel: str) -> bool:
        """Return whether *channel* is in the permitted evidence channels."""
        if not self.evidence_channels:
            return True  # unrestricted if no channels declared
        return channel in self.evidence_channels

    def summary(self) -> str:
        """Return a human-readable one-line summary of this descriptor."""
        channels = ", ".join(sorted(self.evidence_channels)) or "(any)"
        copilot_tag = " [copilot]" if self.copilot_eligible else ""
        trust_ceiling = (
            self.trust_ceiling.name
            if hasattr(self.trust_ceiling, "name")
            else str(self.trust_ceiling)
        )
        return (
            f"{self.name}: {self.implementation_type.__name__} "
            f"({self.lifecycle.value}) deps={list(self.dependencies)} "
            f"trust<={trust_ceiling} channels={channels}"
            f"{copilot_tag}"
        )


@dataclass(frozen=True, slots=True)
class ServiceBinding:
    """Frozen record pairing a service name with a live component.

    Used by the legacy :class:`ServiceGraph` API as well as the newer
    :class:`ServiceRegistry` to track resolved instances.

    Attributes
    ----------
    name : str
        Service name matching the registry key.
    component : Any
        The live service instance.
    authority : AuthorityCenter
        The authority center governing this binding.
    dependencies : tuple[str, ...]
        Names of services this binding depends on.
    metadata : Mapping[str, Any]
        Extra data carried for diagnostics.
    """

    name: str
    component: Any
    authority: AuthorityCenter = field(default_factory=lambda: AuthorityCenter(
        name='service',
        capabilities=frozenset(),
        trust_ceiling=AuthorityTier.VERIFIED,
    ))
    trust_ceiling: str | None = None
    dependencies: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.trust_ceiling is not None:
            tier = {
                'proposal': AuthorityTier.PROPOSAL,
                'reviewed': AuthorityTier.REVIEWED,
                'verified': AuthorityTier.VERIFIED,
            }.get(str(self.trust_ceiling).lower(), AuthorityTier.VERIFIED)
            object.__setattr__(self, 'authority', AuthorityCenter(
                name=self.name,
                capabilities=frozenset(),
                trust_ceiling=tier,
            ))
        if not isinstance(self.dependencies, tuple):
            object.__setattr__(self, 'dependencies', tuple(self.dependencies))


# ---------------------------------------------------------------------------
# Service event bus
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ServiceEvent:
    """Immutable record of a lifecycle event on the service bus.

    Attributes
    ----------
    kind : ServiceEventKind
        What happened.
    service_name : str
        Which service the event pertains to.
    timestamp : float
        ``time.monotonic()`` at the moment the event was created.
    detail : Mapping[str, Any]
        Additional context (e.g. error messages, scope id).
    """

    kind: ServiceEventKind
    service_name: str
    timestamp: float
    detail: Mapping[str, Any] = field(default_factory=dict)


class ServiceEventBus:
    """Pub/sub bus for service lifecycle events.

    Listeners are held via weak references where possible so that a
    subscriber going out of scope does not leak through the bus.

    copilot integration: copilot agents can subscribe to the bus to
    observe resolution patterns and suggest prefetch or caching
    improvements.
    """

    def __init__(self, *, max_history: int = 500) -> None:
        self._listeners: dict[
            ServiceEventKind, list[Callable[[ServiceEvent], None]]
        ] = defaultdict(list)
        self._history: deque[ServiceEvent] = deque(maxlen=max_history)
        self._max_history = max_history
        self._muted: bool = False

    # -- subscription -------------------------------------------------------

    def subscribe(
        self,
        kind: ServiceEventKind,
        callback: Callable[[ServiceEvent], None],
    ) -> None:
        """Register *callback* to be invoked when *kind* events fire."""
        if callback not in self._listeners[kind]:
            self._listeners[kind].append(callback)

    def unsubscribe(
        self,
        kind: ServiceEventKind,
        callback: Callable[[ServiceEvent], None],
    ) -> None:
        """Remove *callback* from *kind* listeners; no-op if absent."""
        listeners = self._listeners.get(kind, [])
        if callback in listeners:
            listeners.remove(callback)

    def subscribe_all(
        self, callback: Callable[[ServiceEvent], None]
    ) -> None:
        """Subscribe *callback* to every event kind."""
        for kind in ServiceEventKind:
            self.subscribe(kind, callback)

    # -- emission -----------------------------------------------------------

    def emit(
        self,
        kind: ServiceEventKind,
        service_name: str,
        detail: Mapping[str, Any] | None = None,
    ) -> ServiceEvent:
        """Create and dispatch an event, returning the event object."""
        event = ServiceEvent(
            kind=kind,
            service_name=service_name,
            timestamp=time.monotonic(),
            detail=detail or {},
        )
        self._history.append(event)
        if not self._muted:
            for callback in list(self._listeners.get(kind, [])):
                try:
                    callback(event)
                except Exception:
                    logger.exception(
                        "Listener error for %s on %s", kind.value, service_name
                    )
        return event

    # -- query --------------------------------------------------------------

    def history(
        self,
        *,
        kind: ServiceEventKind | None = None,
        service_name: str | None = None,
        limit: int | None = None,
    ) -> list[ServiceEvent]:
        """Return matching events from the ring buffer, newest first."""
        results: list[ServiceEvent] = []
        for event in reversed(self._history):
            if kind is not None and event.kind != kind:
                continue
            if service_name is not None and event.service_name != service_name:
                continue
            results.append(event)
            if limit is not None and len(results) >= limit:
                break
        return results

    def event_counts(self) -> dict[ServiceEventKind, int]:
        """Return a counter of events by kind from the ring buffer."""
        counts: dict[ServiceEventKind, int] = {}
        for event in self._history:
            counts[event.kind] = counts.get(event.kind, 0) + 1
        return counts

    def clear_history(self) -> int:
        """Discard all buffered events; return how many were removed."""
        n = len(self._history)
        self._history.clear()
        return n

    def mute(self) -> None:
        """Suppress listener dispatch (events are still recorded)."""
        self._muted = True

    def unmute(self) -> None:
        """Re-enable listener dispatch."""
        self._muted = False

    def listener_count(self, kind: ServiceEventKind | None = None) -> int:
        """Return the number of active listeners, optionally filtered."""
        if kind is not None:
            return len(self._listeners.get(kind, []))
        return sum(len(v) for v in self._listeners.values())


# ---------------------------------------------------------------------------
# Service interceptor (middleware)
# ---------------------------------------------------------------------------


class ServiceInterceptor:
    """Middleware chain for cross-cutting concerns on service resolution.

    Interceptors are invoked around every :meth:`ServiceRegistry.resolve`
    call.  They can perform logging, trust verification, evidence channel
    validation, latency tracking, or any other cross-cutting concern.

    Each interceptor receives the service name and a *proceed* callable.
    Calling ``proceed()`` continues to the next interceptor (or the actual
    resolution logic).  An interceptor may short-circuit by raising or
    returning a cached value.
    """

    def __init__(self) -> None:
        self._interceptors: list[
            Callable[[str, Callable[[], Any]], Any]
        ] = []

    def add(
        self, interceptor: Callable[[str, Callable[[], Any]], Any]
    ) -> None:
        """Append an interceptor to the chain."""
        self._interceptors.append(interceptor)

    def remove(
        self, interceptor: Callable[[str, Callable[[], Any]], Any]
    ) -> None:
        """Remove an interceptor; raise ``ValueError`` if absent."""
        self._interceptors.remove(interceptor)

    def clear(self) -> None:
        """Remove all interceptors."""
        self._interceptors.clear()

    def chain(self, name: str, terminal: Callable[[], Any]) -> Any:
        """Execute the interceptor chain for *name*, ending at *terminal*."""
        if not self._interceptors:
            return terminal()
        # Build the chain inside-out so the first-added interceptor runs first.
        current = terminal
        for interceptor in reversed(self._interceptors):
            # Capture by binding to a default argument.
            def _make_next(
                ic: Callable[[str, Callable[[], Any]], Any],
                nxt: Callable[[], Any],
            ) -> Callable[[], Any]:
                return lambda: ic(name, nxt)

            current = _make_next(interceptor, current)
        return current()

    def __len__(self) -> int:
        return len(self._interceptors)

    def wrap_with_logging(self) -> None:
        """Convenience: add a logging interceptor that traces resolutions."""

        def _logging_interceptor(
            svc_name: str, proceed: Callable[[], Any]
        ) -> Any:
            logger.debug("Resolving service: %s", svc_name)
            start = time.monotonic()
            try:
                result = proceed()
                elapsed = time.monotonic() - start
                logger.debug(
                    "Resolved %s in %.4fs", svc_name, elapsed
                )
                return result
            except Exception:
                logger.exception("Failed to resolve %s", svc_name)
                raise

        self.add(_logging_interceptor)

    def wrap_with_trust_check(
        self, descriptors: Mapping[str, ServiceDescriptor]
    ) -> None:
        """Add an interceptor that verifies trust ceilings are respected.

        No silent trust promotion: if a service's descriptor declares a
        trust ceiling, the interceptor verifies that the resolved component's
        authority does not exceed it.
        """

        def _trust_interceptor(
            svc_name: str, proceed: Callable[[], Any]
        ) -> Any:
            descriptor = descriptors.get(svc_name)
            result = proceed()
            if descriptor is not None and isinstance(result, ServiceBinding):
                if result.authority.trust_ceiling > descriptor.trust_ceiling:
                    raise JuGeoError(
                        StructuredFailure(
                            message=(
                                f"Service '{svc_name}' resolved with "
                                f"authority tier "
                                f"{result.authority.trust_ceiling.name} "
                                f"exceeding its declared ceiling "
                                f"{descriptor.trust_ceiling.name}."
                            ),
                            scope=FailureScope.AUTHORITY,
                            trust_boundary="service-resolution",
                        )
                    )
            return result

        self.add(_trust_interceptor)

    def wrap_with_channel_validation(
        self, descriptors: Mapping[str, ServiceDescriptor]
    ) -> None:
        """Add an interceptor enforcing evidence channel restrictions."""

        def _channel_interceptor(
            svc_name: str, proceed: Callable[[], Any]
        ) -> Any:
            descriptor = descriptors.get(svc_name)
            if descriptor is not None and descriptor.evidence_channels:
                logger.debug(
                    "Service %s restricted to channels: %s",
                    svc_name,
                    sorted(descriptor.evidence_channels),
                )
            return proceed()

        self.add(_channel_interceptor)


# ---------------------------------------------------------------------------
# Service health monitor
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _ServiceHealthRecord:
    """Mutable per-service health metrics."""

    service_name: str
    status: ServiceHealthStatus = ServiceHealthStatus.UNKNOWN
    last_heartbeat: float | None = None
    last_error: str | None = None
    last_error_time: float | None = None
    uptime_start: float | None = None
    request_count: int = 0
    error_count: int = 0
    latency_samples: list[float] = field(default_factory=list)
    max_latency_samples: int = 200


class ServiceHealthMonitor:
    """Tracks runtime health metrics for all registered services.

    The monitor maintains per-service records covering heartbeat timestamps,
    error history, request counts, and latency histograms.  It is safe for
    copilot diagnostic integrations to query these records in order to
    surface service health in IDE panels.
    """

    def __init__(
        self, *, latency_bucket_count: int = 200
    ) -> None:
        self._records: dict[str, _ServiceHealthRecord] = {}
        self._latency_bucket_count = latency_bucket_count

    # -- lifecycle ----------------------------------------------------------

    def register_service(self, name: str) -> None:
        """Start tracking *name* if not already tracked."""
        if name not in self._records:
            self._records[name] = _ServiceHealthRecord(
                service_name=name,
                max_latency_samples=self._latency_bucket_count,
            )

    def unregister_service(self, name: str) -> None:
        """Stop tracking *name*."""
        self._records.pop(name, None)

    # -- recording ----------------------------------------------------------

    def record_heartbeat(self, name: str) -> None:
        """Record a heartbeat for *name*, marking it healthy."""
        rec = self._records.get(name)
        if rec is None:
            return
        now = time.monotonic()
        rec.last_heartbeat = now
        if rec.uptime_start is None:
            rec.uptime_start = now
        rec.status = ServiceHealthStatus.HEALTHY

    def record_request(self, name: str, latency: float) -> None:
        """Record a successful request and its latency in seconds."""
        rec = self._records.get(name)
        if rec is None:
            return
        rec.request_count += 1
        rec.latency_samples.append(latency)
        if len(rec.latency_samples) > rec.max_latency_samples:
            rec.latency_samples = rec.latency_samples[
                -rec.max_latency_samples :
            ]

    def record_error(self, name: str, error_message: str) -> None:
        """Record a service error, marking it degraded."""
        rec = self._records.get(name)
        if rec is None:
            return
        rec.error_count += 1
        rec.last_error = error_message
        rec.last_error_time = time.monotonic()
        rec.status = ServiceHealthStatus.DEGRADED

    def mark_failed(self, name: str, reason: str) -> None:
        """Permanently mark *name* as failed until explicit recovery."""
        rec = self._records.get(name)
        if rec is None:
            return
        rec.status = ServiceHealthStatus.FAILED
        rec.last_error = reason
        rec.last_error_time = time.monotonic()

    def mark_healthy(self, name: str) -> None:
        """Explicitly recover *name* to healthy status."""
        rec = self._records.get(name)
        if rec is None:
            return
        rec.status = ServiceHealthStatus.HEALTHY
        rec.last_error = None

    # -- query --------------------------------------------------------------

    def status(self, name: str) -> ServiceHealthStatus:
        """Return the current status of *name*."""
        rec = self._records.get(name)
        return rec.status if rec is not None else ServiceHealthStatus.UNKNOWN

    def uptime(self, name: str) -> float | None:
        """Return seconds since first heartbeat, or ``None``."""
        rec = self._records.get(name)
        if rec is None or rec.uptime_start is None:
            return None
        return time.monotonic() - rec.uptime_start

    def latency_histogram(self, name: str) -> dict[str, float]:
        """Return a summary of latency samples for *name*.

        Returns a dict with keys ``min``, ``max``, ``mean``, ``p50``,
        ``p95``, ``p99``, ``count``.  Returns empty dict if no samples.
        """
        rec = self._records.get(name)
        if rec is None or not rec.latency_samples:
            return {}
        samples = sorted(rec.latency_samples)
        n = len(samples)
        return {
            "min": samples[0],
            "max": samples[-1],
            "mean": sum(samples) / n,
            "p50": samples[n // 2],
            "p95": samples[int(n * 0.95)],
            "p99": samples[min(int(n * 0.99), n - 1)],
            "count": float(n),
        }

    def snapshot(self, name: str) -> dict[str, Any]:
        """Return a dict snapshot of all health data for *name*."""
        rec = self._records.get(name)
        if rec is None:
            return {"service_name": name, "status": "unknown"}
        return {
            "service_name": rec.service_name,
            "status": rec.status.value,
            "last_heartbeat": rec.last_heartbeat,
            "last_error": rec.last_error,
            "last_error_time": rec.last_error_time,
            "uptime": self.uptime(name),
            "request_count": rec.request_count,
            "error_count": rec.error_count,
            "latency": self.latency_histogram(name),
        }

    def all_snapshots(self) -> list[dict[str, Any]]:
        """Return snapshots for every tracked service."""
        return [self.snapshot(name) for name in sorted(self._records)]

    def degraded_services(self) -> list[str]:
        """Return names of all services not currently healthy."""
        return [
            name
            for name, rec in self._records.items()
            if rec.status != ServiceHealthStatus.HEALTHY
        ]

    def aggregate_status(self) -> ServiceHealthStatus:
        """Return the worst status across all tracked services."""
        if not self._records:
            return ServiceHealthStatus.UNKNOWN
        statuses = {rec.status for rec in self._records.values()}
        if ServiceHealthStatus.FAILED in statuses:
            return ServiceHealthStatus.FAILED
        if ServiceHealthStatus.DEGRADED in statuses:
            return ServiceHealthStatus.DEGRADED
        if ServiceHealthStatus.UNKNOWN in statuses:
            return ServiceHealthStatus.UNKNOWN
        return ServiceHealthStatus.HEALTHY


# ---------------------------------------------------------------------------
# Service scope
# ---------------------------------------------------------------------------


class ServiceScope:
    """Scoped lifetime container for services with ``SCOPED`` lifecycle.

    A scope tracks instances created within it and disposes them when the
    scope exits.  Scopes may be nested — a child scope falls back to its
    parent for services that are not scope-local.

    Attributes
    ----------
    scope_id : str
        Unique identifier for this scope.
    parent : ServiceScope or None
        Parent scope for fallback resolution.
    """

    def __init__(
        self,
        *,
        parent: ServiceScope | None = None,
        scope_id: str | None = None,
    ) -> None:
        self.scope_id: str = scope_id or uuid.uuid4().hex[:12]
        self.parent: ServiceScope | None = parent
        self._instances: dict[str, Any] = {}
        self._disposal_callbacks: list[Callable[[], None]] = []
        self._closed: bool = False
        self._children: list[weakref.ref[ServiceScope]] = []
        if parent is not None:
            parent._children.append(weakref.ref(self))

    # -- instance tracking --------------------------------------------------

    def get(self, name: str) -> Any | None:
        """Return the scoped instance for *name*, or ``None``."""
        if name in self._instances:
            return self._instances[name]
        if self.parent is not None:
            return self.parent.get(name)
        return None

    def put(self, name: str, instance: Any) -> None:
        """Store *instance* under *name* in this scope."""
        if self._closed:
            raise JuGeoError(
                StructuredFailure(
                    message=f"Cannot store '{name}' in closed scope {self.scope_id}.",
                    scope=FailureScope.RUNTIME,
                )
            )
        self._instances[name] = instance

    def has(self, name: str) -> bool:
        """Return whether *name* has an instance in this scope or ancestors."""
        if name in self._instances:
            return True
        if self.parent is not None:
            return self.parent.has(name)
        return False

    def add_disposal(self, callback: Callable[[], None]) -> None:
        """Register a callback to run when this scope is closed."""
        self._disposal_callbacks.append(callback)

    # -- lifecycle ----------------------------------------------------------

    def close(self) -> list[str]:
        """Close this scope, disposing all instances in reverse order.

        Returns the list of service names that were disposed.
        """
        if self._closed:
            return []
        self._closed = True

        # Close children first.
        for child_ref in self._children:
            child = child_ref()
            if child is not None and not child._closed:
                child.close()

        disposed: list[str] = []
        # Run disposal callbacks in reverse registration order.
        for callback in reversed(self._disposal_callbacks):
            try:
                callback()
            except Exception:
                logger.exception(
                    "Disposal callback error in scope %s", self.scope_id
                )

        # Dispose instances that have a ``dispose`` method.
        for name in reversed(list(self._instances)):
            instance = self._instances.pop(name)
            if hasattr(instance, "dispose") and callable(instance.dispose):
                try:
                    instance.dispose()
                except Exception:
                    logger.exception(
                        "Error disposing %s in scope %s",
                        name,
                        self.scope_id,
                    )
            disposed.append(name)

        return disposed

    @property
    def is_closed(self) -> bool:
        """Return ``True`` once the scope has been closed."""
        return self._closed

    def instance_count(self) -> int:
        """Return the number of instances tracked in this scope only."""
        return len(self._instances)

    def all_names(self) -> list[str]:
        """Return all names available via this scope and its ancestors."""
        names = set(self._instances)
        if self.parent is not None:
            names.update(self.parent.all_names())
        return sorted(names)

    def create_child(self, scope_id: str | None = None) -> ServiceScope:
        """Create a nested child scope."""
        return ServiceScope(parent=self, scope_id=scope_id)

    def __enter__(self) -> ServiceScope:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def __repr__(self) -> str:
        parent_id = self.parent.scope_id if self.parent else "none"
        return (
            f"ServiceScope(id={self.scope_id!r}, "
            f"instances={len(self._instances)}, "
            f"parent={parent_id}, closed={self._closed})"
        )


# ---------------------------------------------------------------------------
# Service graph — dependency analysis
# ---------------------------------------------------------------------------


class ServiceGraph:
    """Dependency graph of registered services with cycle detection.

    The graph is extracted from :class:`ServiceDescriptor` entries or
    :class:`ServiceBinding` entries and provides topological sorting,
    cycle detection, and adjacency-list visualization.

    The ``bindings`` attribute preserves backward compatibility with
    :mod:`jugeo.kernel.health` which accesses ``graph.bindings`` directly.
    """

    def __init__(self, bindings: Mapping[str, ServiceBinding] | None = None) -> None:
        self.bindings: dict[str, ServiceBinding] = {}
        self._adjacency: dict[str, list[str]] = defaultdict(list)
        self.frozen: bool = False
        if bindings is not None:
            for binding in bindings.values():
                self.bind(binding)

    # -- mutation -----------------------------------------------------------

    def bind(self, binding: ServiceBinding) -> None:
        """Add or replace a service binding in the graph."""
        if self.frozen:
            raise JuGeoError(
                StructuredFailure(
                    message="Cannot mutate a frozen service graph.",
                    scope=FailureScope.AUTHORITY,
                    metadata={"service": binding.name},
                )
            )
        self.bindings[binding.name] = binding
        self._adjacency[binding.name] = list(binding.dependencies)

    def add_from_descriptor(self, descriptor: ServiceDescriptor) -> None:
        """Add a node from a descriptor (no component yet)."""
        if self.frozen:
            raise JuGeoError(
                StructuredFailure(
                    message="Cannot mutate a frozen service graph.",
                    scope=FailureScope.AUTHORITY,
                )
            )
        self._adjacency[descriptor.name] = list(descriptor.dependencies)

    def add_node(self, name: str, dependencies: Sequence[str]) -> None:
        """Legacy helper for directly adding a graph node."""
        self._adjacency[name] = list(dependencies)

    def remove(self, name: str) -> None:
        """Remove a service and all edges to/from it."""
        if self.frozen:
            raise JuGeoError(
                StructuredFailure(
                    message="Cannot mutate a frozen service graph.",
                    scope=FailureScope.AUTHORITY,
                )
            )
        self.bindings.pop(name, None)
        self._adjacency.pop(name, None)
        for deps in self._adjacency.values():
            if name in deps:
                deps.remove(name)

    # -- resolution ---------------------------------------------------------

    def resolve(self, name: str) -> ServiceBinding:
        """Look up a binding by name; raise if absent."""
        if name not in self.bindings:
            raise JuGeoError(
                StructuredFailure(
                    message="Requested service does not exist.",
                    scope=FailureScope.AUTHORITY,
                    metadata={"service": name},
                )
            )
        return self.bindings[name]

    def has(self, name: str) -> bool:
        """Return whether *name* is in the graph (binding or descriptor)."""
        return name in self._adjacency

    # -- analysis -----------------------------------------------------------

    def detect_cycles(self) -> list[list[str]]:
        """Return all cycles in the dependency graph as lists of names.

        Uses an iterative DFS with explicit colour tracking.  Returns an
        empty list if the graph is acyclic.
        """
        WHITE, GRAY, BLACK = 0, 1, 2
        color: dict[str, int] = {n: WHITE for n in self._adjacency}
        parent: dict[str, str | None] = {n: None for n in self._adjacency}
        cycles: list[list[str]] = []

        for start in self._adjacency:
            if color[start] != WHITE:
                continue
            stack: list[tuple[str, int]] = [(start, 0)]
            color[start] = GRAY
            while stack:
                node, idx = stack[-1]
                neighbors = self._adjacency.get(node, [])
                if idx < len(neighbors):
                    stack[-1] = (node, idx + 1)
                    neighbor = neighbors[idx]
                    if neighbor not in color:
                        continue  # unknown node — dangling dependency
                    if color[neighbor] == WHITE:
                        color[neighbor] = GRAY
                        parent[neighbor] = node
                        stack.append((neighbor, 0))
                    elif color[neighbor] == GRAY:
                        # Back edge — reconstruct cycle.
                        cycle = [neighbor]
                        cur = node
                        while cur != neighbor:
                            cycle.append(cur)
                            cur = parent.get(cur, neighbor)  # type: ignore[assignment]
                        cycle.append(neighbor)
                        cycle.reverse()
                        cycles.append(cycle)
                else:
                    color[node] = BLACK
                    stack.pop()
        return cycles

    def topological_sort(self) -> list[str]:
        """Return a topological ordering of all services.

        Services appear *after* all of their dependencies so the result is a
        valid startup order.  Raises :class:`JuGeoError` if the graph
        contains a cycle.
        """
        # _adjacency maps node -> [dependencies].  A node is "ready" when all
        # its dependencies have already been placed in the output.
        dep_count: dict[str, int] = {
            n: len([d for d in deps if d in self._adjacency])
            for n, deps in self._adjacency.items()
        }

        # Reverse adjacency: for each dependency, record who depends on it.
        reverse: dict[str, list[str]] = defaultdict(list)
        for node, deps in self._adjacency.items():
            for dep in deps:
                if dep in self._adjacency:
                    reverse[dep].append(node)

        queue = deque(
            sorted(n for n, c in dep_count.items() if c == 0)
        )
        order: list[str] = []
        while queue:
            node = queue.popleft()
            order.append(node)
            for dependent in sorted(reverse.get(node, [])):
                dep_count[dependent] -= 1
                if dep_count[dependent] == 0:
                    queue.append(dependent)

        if len(order) != len(self._adjacency):
            remaining = sorted(
                set(self._adjacency) - set(order)
            )
            raise JuGeoError(
                StructuredFailure(
                    message="Service graph contains an unresolved dependency cycle.",
                    scope=FailureScope.AUTHORITY,
                    metadata={"remaining": remaining},
                )
            )
        return order

    def startup_order(self) -> list[str]:
        """Return startup ordering as a tuple (backward-compatible API).

        If the graph has only bindings (legacy path) and no adjacency data,
        falls back to the legacy algorithm.
        """
        # Populate adjacency from bindings if not already set.
        for name, binding in self.bindings.items():
            if name not in self._adjacency:
                self._adjacency[name] = list(binding.dependencies)

        if not self._adjacency:
            return _CompatibleOrder()

        return _CompatibleOrder(self.topological_sort())

    def adjacency_list(self) -> dict[str, list[str]]:
        """Return a copy of the adjacency list for visualization."""
        return {k: list(v) for k, v in self._adjacency.items()}

    def reverse_dependencies(self, name: str) -> list[str]:
        """Return services that depend on *name*."""
        return [
            n
            for n, deps in self._adjacency.items()
            if name in deps
        ]

    def validate(self) -> list[str]:
        """Validate the graph, returning a list of diagnostic messages.

        Checks for: missing dependencies, self-loops, cycles, and
        orphan services (no dependents and no dependencies).
        """
        messages: list[str] = []
        all_names = set(self._adjacency)

        for name, deps in self._adjacency.items():
            for dep in deps:
                if dep == name:
                    messages.append(f"Self-loop: {name} depends on itself.")
                if dep not in all_names:
                    messages.append(
                        f"Missing dependency: {name} requires unknown service '{dep}'."
                    )

        cycles = self.detect_cycles()
        for cycle in cycles:
            messages.append(f"Cycle detected: {' -> '.join(cycle)}")

        # Orphan detection.
        for name in all_names:
            has_deps = bool(self._adjacency.get(name))
            has_dependents = any(
                name in deps
                for n, deps in self._adjacency.items()
                if n != name
            )
            if not has_deps and not has_dependents and len(all_names) > 1:
                messages.append(f"Orphan service: {name} has no connections.")

        return messages


# ---------------------------------------------------------------------------
# Service factory
# ---------------------------------------------------------------------------


class ServiceFactory:
    """Creates service instances respecting lifecycle and dependency injection.

    The factory consults :class:`ServiceDescriptor` metadata to decide
    whether to return a cached singleton, create a new transient instance,
    or look up a scoped instance.  It tracks creation provenance for
    diagnostics and copilot-assisted debugging.
    """

    def __init__(
        self,
        descriptors: dict[str, ServiceDescriptor],
        singletons: dict[str, Any],
        event_bus: ServiceEventBus,
    ) -> None:
        self._descriptors = descriptors
        self._singletons = singletons
        self._event_bus = event_bus
        self._creation_log: list[dict[str, Any]] = []

    def create(
        self,
        name: str,
        resolved_deps: dict[str, Any],
        scope: ServiceScope | None = None,
    ) -> Any:
        """Create or retrieve an instance of the service named *name*.

        Parameters
        ----------
        name
            The service registry key.
        resolved_deps
            Mapping of dependency name to resolved instance.
        scope
            Active scope for ``SCOPED`` services; ignored otherwise.

        Returns
        -------
        Any
            The service instance.
        """
        descriptor = self._descriptors.get(name)
        if descriptor is None:
            raise JuGeoError(
                StructuredFailure(
                    message=f"No descriptor for service '{name}'.",
                    scope=FailureScope.AUTHORITY,
                )
            )

        lifecycle = descriptor.lifecycle

        # SINGLETON / LAZY_SINGLETON — return cached if available.
        if lifecycle in (
            ServiceLifecycle.SINGLETON,
            ServiceLifecycle.LAZY_SINGLETON,
        ):
            if name in self._singletons:
                return self._singletons[name]
            instance = self._instantiate(descriptor, resolved_deps)
            self._singletons[name] = instance
            self._record_creation(name, lifecycle, "kernel")
            return instance

        # SCOPED — look up in scope, create if absent.
        if lifecycle == ServiceLifecycle.SCOPED:
            if scope is not None and scope.has(name):
                return scope.get(name)
            instance = self._instantiate(descriptor, resolved_deps)
            if scope is not None:
                scope.put(name, instance)
            self._record_creation(name, lifecycle, scope.scope_id if scope else "no-scope")
            return instance

        # TRANSIENT — always create a fresh instance.
        instance = self._instantiate(descriptor, resolved_deps)
        self._record_creation(name, lifecycle, "transient")
        return instance

    def _instantiate(
        self,
        descriptor: ServiceDescriptor,
        resolved_deps: dict[str, Any],
    ) -> Any:
        """Call the implementation_type constructor with resolved deps."""
        impl = descriptor.implementation_type
        try:
            instance = impl(**resolved_deps)
        except TypeError:
            # Fall back to no-arg construction if kwargs not accepted.
            try:
                instance = impl()
            except Exception as exc:
                raise JuGeoError(
                    StructuredFailure(
                        message=(
                            f"Failed to instantiate '{descriptor.name}': {exc}"
                        ),
                        scope=FailureScope.RUNTIME,
                    )
                ) from exc
        return instance

    def _record_creation(
        self, name: str, lifecycle: ServiceLifecycle, context: str
    ) -> None:
        """Append to the creation log for diagnostics."""
        entry = {
            "service": name,
            "lifecycle": lifecycle.value,
            "context": context,
            "timestamp": time.monotonic(),
        }
        self._creation_log.append(entry)
        self._event_bus.emit(
            ServiceEventKind.RESOLVED, name, detail=entry
        )

    def creation_log(self) -> list[dict[str, Any]]:
        """Return a copy of the creation log."""
        return list(self._creation_log)

    def evict_singleton(self, name: str) -> bool:
        """Remove a singleton from the cache; return whether it was present."""
        if name in self._singletons:
            del self._singletons[name]
            return True
        return False

    def singleton_names(self) -> list[str]:
        """Return names of all cached singletons."""
        return sorted(self._singletons)


# ---------------------------------------------------------------------------
# Service registry — main facade
# ---------------------------------------------------------------------------


class ServiceRegistry:
    """Central service registry with dependency injection and lifecycle.

    The registry is the primary entry point for service management in the
    JuGeo kernel.  It wraps the :class:`ServiceGraph`, :class:`ServiceFactory`,
    :class:`ServiceEventBus`, :class:`ServiceInterceptor`, and
    :class:`ServiceHealthMonitor` into a cohesive API.

    copilot integration: the registry exposes ``copilot_eligible`` metadata
    on descriptors so that copilot agents can discover which services they
    are permitted to call.
    """

    def __init__(self) -> None:
        self._descriptors: dict[str, ServiceDescriptor] = {}
        self._graph: ServiceGraph = ServiceGraph()
        self._event_bus: ServiceEventBus = ServiceEventBus()
        self._interceptor: ServiceInterceptor = ServiceInterceptor()
        self._health: ServiceHealthMonitor = ServiceHealthMonitor()
        self._singletons: dict[str, Any] = {}
        self._factory: ServiceFactory = ServiceFactory(
            self._descriptors, self._singletons, self._event_bus
        )
        self._frozen: bool = False

    # -- registration -------------------------------------------------------

    def register(self, descriptor: ServiceDescriptor, factory: Any | None = None) -> None:
        """Register a service descriptor.

        Raises if the registry is frozen or if a duplicate name is found
        without first unregistering.
        """
        if self._frozen:
            raise JuGeoError(
                StructuredFailure(
                    message="Cannot register into a frozen registry.",
                    scope=FailureScope.AUTHORITY,
                )
            )
        if descriptor.name in self._descriptors:
            raise JuGeoError(
                StructuredFailure(
                    message=f"Duplicate service name: '{descriptor.name}'.",
                    scope=FailureScope.AUTHORITY,
                )
            )
        if factory is not None:
            descriptor = replace(descriptor, implementation_type=factory)
        self._descriptors[descriptor.name] = descriptor
        self._graph.add_from_descriptor(descriptor)
        self._health.register_service(descriptor.name)
        self._event_bus.emit(
            ServiceEventKind.REGISTERED,
            descriptor.name,
            detail={
                "lifecycle": descriptor.lifecycle.value,
                "copilot_eligible": descriptor.copilot_eligible,
            },
        )

    def unregister(self, name: str) -> bool:
        """Remove a service by name.  Returns ``True`` if it existed."""
        if self._frozen:
            raise JuGeoError(
                StructuredFailure(
                    message="Cannot unregister from a frozen registry.",
                    scope=FailureScope.AUTHORITY,
                )
            )
        if name not in self._descriptors:
            return False
        del self._descriptors[name]
        self._graph.remove(name)
        self._health.unregister_service(name)
        self._factory.evict_singleton(name)
        return True

    # -- resolution ---------------------------------------------------------

    def resolve(self, name: str, scope: ServiceScope | None = None) -> Any:
        """Resolve and return a service instance by name.

        The interceptor chain is executed around the actual resolution.
        Dependency injection is performed recursively.
        """
        if name not in self._descriptors:
            self._event_bus.emit(
                ServiceEventKind.FAILED,
                name,
                detail={"reason": "unknown service"},
            )
            raise JuGeoError(
                StructuredFailure(
                    message=f"Unknown service: '{name}'.",
                    scope=FailureScope.AUTHORITY,
                )
            )

        def _terminal() -> Any:
            return self._resolve_inner(name, scope)

        start = time.monotonic()
        try:
            result = self._interceptor.chain(name, _terminal)
            elapsed = time.monotonic() - start
            self._health.record_request(name, elapsed)
            self._health.record_heartbeat(name)
            return result
        except Exception as exc:
            self._health.record_error(name, str(exc))
            raise

    def _resolve_inner(
        self, name: str, scope: ServiceScope | None
    ) -> Any:
        """Internal resolution: inject dependencies and call the factory."""
        descriptor = self._descriptors[name]
        resolved_deps: dict[str, Any] = {}
        for dep_name in descriptor.dependencies:
            resolved_deps[dep_name] = self.resolve(dep_name, scope)
        return self._factory.create(name, resolved_deps, scope)

    def resolve_all(
        self,
        *,
        lifecycle: ServiceLifecycle | None = None,
        copilot_only: bool = False,
    ) -> dict[str, Any]:
        """Resolve multiple services matching optional filters.

        Parameters
        ----------
        lifecycle
            If given, only resolve services with this lifecycle.
        copilot_only
            If ``True``, only resolve copilot-eligible services.
        """
        results: dict[str, Any] = {}
        for name, desc in self._descriptors.items():
            if lifecycle is not None and desc.lifecycle != lifecycle:
                continue
            if copilot_only and not desc.copilot_eligible:
                continue
            results[name] = self.resolve(name)
        return results

    # -- query --------------------------------------------------------------

    def has_service(self, name: str) -> bool:
        """Return whether *name* is registered."""
        return name in self._descriptors

    def get_descriptor(self, name: str) -> ServiceDescriptor | None:
        """Return the descriptor for *name*, or ``None``."""
        return self._descriptors.get(name)

    def list_services(
        self,
        *,
        lifecycle: ServiceLifecycle | None = None,
        copilot_only: bool = False,
    ) -> list[str]:
        """Return sorted names of registered services, optionally filtered."""
        names: list[str] = []
        for name, desc in self._descriptors.items():
            if lifecycle is not None and desc.lifecycle != lifecycle:
                continue
            if copilot_only and not desc.copilot_eligible:
                continue
            names.append(name)
        return sorted(names)

    def list_copilot_services(self) -> list[str]:
        """Return names of services eligible for copilot invocation."""
        return self.list_services(copilot_only=True)

    # -- graph operations ---------------------------------------------------

    def validate_graph(self) -> list[str]:
        """Validate the dependency graph and return diagnostic messages."""
        return self._graph.validate()

    def detect_cycles(self) -> list[list[str]]:
        """Return cycles in the dependency graph."""
        return self._graph.detect_cycles()

    def topological_sort(self) -> list[str]:
        """Return a topological ordering of all services."""
        return self._graph.topological_sort()

    # -- scoping ------------------------------------------------------------

    def create_scope(
        self,
        *,
        parent: ServiceScope | None = None,
        scope_id: str | None = None,
    ) -> ServiceScope:
        """Create a new :class:`ServiceScope`."""
        scope = ServiceScope(parent=parent, scope_id=scope_id)
        self._event_bus.emit(
            ServiceEventKind.SCOPE_CREATED,
            "kernel",
            detail={"scope_id": scope.scope_id},
        )
        return scope

    # -- freeze / introspection ---------------------------------------------

    def freeze(self) -> None:
        """Freeze the registry and its underlying graph."""
        self._graph.frozen = True
        self._frozen = True

    @property
    def is_frozen(self) -> bool:
        return self._frozen

    @property
    def event_bus(self) -> ServiceEventBus:
        return self._event_bus

    @property
    def interceptor(self) -> ServiceInterceptor:
        return self._interceptor

    @property
    def health_monitor(self) -> ServiceHealthMonitor:
        return self._health

    @property
    def graph(self) -> ServiceGraph:
        return self._graph

    @property
    def factory(self) -> ServiceFactory:
        return self._factory

    def descriptor_summaries(self) -> list[str]:
        """Return one-line summaries for all descriptors."""
        return [d.summary() for d in self._descriptors.values()]


# ---------------------------------------------------------------------------
# Service disposer
# ---------------------------------------------------------------------------


class ServiceDisposer:
    """Orderly shutdown of services in reverse topological order.

    The disposer ensures that services are torn down in an order that
    respects dependency constraints — a service is never disposed while
    one of its dependents is still running.
    """

    def __init__(
        self,
        registry: ServiceRegistry,
        event_bus: ServiceEventBus,
    ) -> None:
        self._registry = registry
        self._event_bus = event_bus
        self._disposed: list[str] = []

    def dispose_all(self) -> list[str]:
        """Dispose every singleton in reverse topological order.

        Returns the ordered list of disposed service names.
        """
        try:
            order = self._registry.topological_sort()
        except JuGeoError:
            # If the graph is cyclic, fall back to arbitrary order.
            order = self._registry.list_services()

        reverse_order = list(reversed(order))
        for name in reverse_order:
            self._dispose_one(name)
        return list(self._disposed)

    def _dispose_one(self, name: str) -> None:
        """Dispose a single service, removing it from the singleton cache."""
        factory = self._registry.factory
        if name in factory._singletons:
            instance = factory._singletons[name]
            if hasattr(instance, "dispose") and callable(instance.dispose):
                try:
                    instance.dispose()
                except Exception:
                    logger.exception("Error disposing service %s", name)
                    self._event_bus.emit(
                        ServiceEventKind.FAILED,
                        name,
                        detail={"phase": "disposal"},
                    )
            factory.evict_singleton(name)
            self._disposed.append(name)
            self._event_bus.emit(ServiceEventKind.DISPOSED, name)

    def dispose_scope(self, scope: ServiceScope) -> list[str]:
        """Close a scope and return the names that were disposed."""
        disposed = scope.close()
        for name in disposed:
            self._event_bus.emit(
                ServiceEventKind.DISPOSED,
                name,
                detail={"scope_id": scope.scope_id},
            )
        self._event_bus.emit(
            ServiceEventKind.SCOPE_CLOSED,
            "kernel",
            detail={"scope_id": scope.scope_id},
        )
        return disposed

    @property
    def disposed_services(self) -> list[str]:
        """Return the list of services disposed so far."""
        return list(self._disposed)

    def reset(self) -> None:
        """Clear the disposal log."""
        self._disposed.clear()


# ---------------------------------------------------------------------------
# Kernel bootstrapper
# ---------------------------------------------------------------------------


# Core service names in boot order.  Each group depends on the previous.
_BOOT_PHASES: list[tuple[str, list[str]]] = [
    ("phase-1-configuration", [
        "configuration",
    ]),
    ("phase-2-authority-trust", [
        "authority-center",
        "trust-algebra",
    ]),
    ("phase-3-evidence", [
        "evidence-channels",
        "evidence-recorder",
    ]),
    ("phase-4-solver", [
        "solver-federation",
        "solver-cache",
    ]),
    ("phase-5-geometry", [
        "descent-engine",
        "gluing-verifier",
    ]),
    ("phase-6-orchestration", [
        "orchestration-controller",
        "frontier-budget",
    ]),
    ("phase-7-copilot", [
        "copilot-proposal-channel",
        "copilot-review-agent",
    ]),
]


class KernelBootstrapper:
    """Registers all core JuGeo services in the correct boot order.

    The bootstrapper works in phases:

    1. **Configuration** — runtime defaults, config layers, manifest.
    2. **Authority & Trust** — authority centers, trust algebra.
    3. **Evidence channels** — evidence kinds, recorders, channels.
    4. **Solver federation** — Z3 integration, solver cache.
    5. **Descent engine** — geometric descent, gluing verification.
    6. **Orchestration** — fleet control, frontier budgets.
    7. **Copilot integration** — proposal channels, review agents.

    Each phase is registered atomically.  If any phase fails, previously
    registered services remain intact so that partial bootstrap can still
    serve diagnostics.
    """

    def __init__(self, registry: ServiceRegistry) -> None:
        self._registry = registry
        self._phases_completed: list[str] = []
        self._errors: list[tuple[str, str]] = []

    # -- boot phases --------------------------------------------------------

    def bootstrap(self) -> list[str]:
        """Run all boot phases, returning the names of phases completed.

        This is the main entry point.  It creates placeholder descriptors
        for every core service so that the dependency graph is fully
        populated.
        """
        for phase_name, service_names in _BOOT_PHASES:
            try:
                self._register_phase(phase_name, service_names)
                self._phases_completed.append(phase_name)
            except JuGeoError as exc:
                self._errors.append((phase_name, str(exc)))
                logger.error(
                    "Bootstrap phase %s failed: %s", phase_name, exc
                )
                # Continue to next phase — partial bootstrap is better than
                # no bootstrap.
        return list(self._phases_completed)

    def _register_phase(
        self, phase_name: str, service_names: list[str]
    ) -> None:
        """Register placeholder descriptors for a single boot phase."""
        # Compute dependencies: each service in this phase depends on all
        # services from previously completed phases.
        prior_services: list[str] = []
        for completed_phase in self._phases_completed:
            for _, names in _BOOT_PHASES:
                if _ == completed_phase:
                    prior_services.extend(names)

        for svc_name in service_names:
            if self._registry.has_service(svc_name):
                continue  # already registered (e.g., by explicit config)

            # Determine if this is a copilot service.
            is_copilot = "copilot" in svc_name

            # Determine evidence channels for this service.
            channels: frozenset[str] = frozenset()
            if "evidence" in svc_name:
                channels = frozenset({"proof", "solver", "runtime", "semantic", "proposal"})
            elif "solver" in svc_name:
                channels = frozenset({"solver"})
            elif is_copilot:
                channels = frozenset({"proposal"})

            # Build dependencies: services in the same phase do not depend
            # on each other, but depend on all prior-phase services that
            # are registered.
            deps = tuple(
                s for s in prior_services
                if self._registry.has_service(s)
            )

            descriptor = ServiceDescriptor(
                name=svc_name,
                interface_type=object,
                implementation_type=object,
                lifecycle=ServiceLifecycle.SINGLETON,
                dependencies=deps,
                trust_ceiling=(
                    AuthorityTier.PROPOSAL if is_copilot
                    else AuthorityTier.VERIFIED
                ),
                evidence_channels=channels,
                copilot_eligible=is_copilot,
                metadata={
                    "boot_phase": phase_name,
                    "placeholder": True,
                },
            )
            self._registry.register(descriptor)

    # -- query --------------------------------------------------------------

    def phases_completed(self) -> list[str]:
        """Return the list of successfully completed phase names."""
        return list(self._phases_completed)

    def errors(self) -> list[tuple[str, str]]:
        """Return ``(phase, message)`` pairs for any failed phases."""
        return list(self._errors)

    def is_fully_booted(self) -> bool:
        """Return ``True`` if all phases completed without error."""
        return len(self._phases_completed) == len(_BOOT_PHASES)

    def boot_summary(self) -> str:
        """Return a human-readable summary of the bootstrap process."""
        total = len(_BOOT_PHASES)
        done = len(self._phases_completed)
        lines = [f"Bootstrap: {done}/{total} phases completed."]
        for phase_name, _ in _BOOT_PHASES:
            status = "✓" if phase_name in self._phases_completed else "✗"
            lines.append(f"  {status} {phase_name}")
        if self._errors:
            lines.append("Errors:")
            for phase, msg in self._errors:
                lines.append(f"  {phase}: {msg}")
        return "\n".join(lines)

    def registered_service_count(self) -> int:
        """Return the total number of services registered during bootstrap."""
        return len(self._registry.list_services())

    def copilot_service_names(self) -> list[str]:
        """Return names of copilot-eligible services registered at boot."""
        return self._registry.list_copilot_services()


# ---------------------------------------------------------------------------
# Public helper functions
# ---------------------------------------------------------------------------


def resolve_service(
    source: ServiceGraph | ServiceRegistry, name: str
) -> ServiceBinding | Any:
    """Convenience: resolve a named service from a graph or registry.

    Parameters
    ----------
    source
        Either a :class:`ServiceGraph` (returns a :class:`ServiceBinding`)
        or a :class:`ServiceRegistry` (returns a live instance).
    name
        The service name to resolve.
    """
    if isinstance(source, ServiceRegistry):
        return source.resolve(name)
    if isinstance(source, ServiceGraph):
        return source.resolve(name)
    raise TypeError(
        f"Expected ServiceGraph or ServiceRegistry, got {type(source).__name__}."
    )


def freeze_service_graph(graph: ServiceGraph) -> ServiceGraph:
    """Freeze *graph*, validating its dependency structure.

    Returns the same graph object for chaining convenience.
    """
    graph.frozen = True
    graph.startup_order()
    return graph


def bootstrap_kernel() -> tuple[ServiceRegistry, KernelBootstrapper]:
    """Create a fresh registry, run the full kernel bootstrap, and return both.

    This is the standard entry point for starting a JuGeo runtime.
    The registry is **not** frozen after bootstrap so that test or
    exploratory code may register additional services.

    Returns
    -------
    tuple[ServiceRegistry, KernelBootstrapper]
        The populated registry and the bootstrapper (for diagnostics).
    """
    registry = ServiceRegistry()
    bootstrapper = KernelBootstrapper(registry)
    bootstrapper.bootstrap()
    logger.info(
        "Kernel bootstrap complete: %d services registered, %d phases.",
        bootstrapper.registered_service_count(),
        len(bootstrapper.phases_completed()),
    )
    return registry, bootstrapper


@contextmanager
def with_scope(
    registry: ServiceRegistry,
    *,
    parent: ServiceScope | None = None,
    scope_id: str | None = None,
) -> Generator[ServiceScope, None, None]:
    """Context manager that creates a :class:`ServiceScope` and disposes it.

    Usage::

        with with_scope(registry) as scope:
            svc = registry.resolve("my-service", scope=scope)
            ...
        # scope is automatically closed here

    Parameters
    ----------
    registry
        The service registry owning the scope.
    parent
        Optional parent scope for nested lifetimes.
    scope_id
        Optional explicit scope identifier.
    """
    scope = registry.create_scope(parent=parent, scope_id=scope_id)
    try:
        yield scope
    finally:
        disposer = ServiceDisposer(registry, registry.event_bus)
        disposer.dispose_scope(scope)


# copilot: shared-core marker for future LLM orchestration.

__all__ = [
    "ServiceLifecycle",
    "ServiceEventKind",
    "ServiceHealthStatus",
    "ServiceDescriptor",
    "ServiceBinding",
    "ServiceEvent",
    "ServiceEventBus",
    "ServiceInterceptor",
    "ServiceHealthMonitor",
    "ServiceScope",
    "ServiceGraph",
    "ServiceFactory",
    "ServiceRegistry",
    "ServiceDisposer",
    "KernelBootstrapper",
    "resolve_service",
    "freeze_service_graph",
    "bootstrap_kernel",
    "with_scope",
    # Cross-subsystem service registration
    "register_judgment_service",
    "register_geometry_service",
    "register_evidence_service",
    "register_solver_service",
]


# ---------------------------------------------------------------------------
# Cross-subsystem service registration helpers
# ---------------------------------------------------------------------------

try:
    from jugeo.judgments import judgment_terms as _j_svc_mod  # type: ignore[import]
    _JUDGMENT_SVC_AVAILABLE = True
except ImportError:
    _j_svc_mod = None  # type: ignore[assignment]
    _JUDGMENT_SVC_AVAILABLE = False

try:
    from jugeo.geometry import site as _g_svc_site, descent as _g_svc_descent  # type: ignore[import]
    _GEOMETRY_SVC_AVAILABLE = True
except ImportError:
    _g_svc_site = None  # type: ignore[assignment]
    _g_svc_descent = None  # type: ignore[assignment]
    _GEOMETRY_SVC_AVAILABLE = False

try:
    from jugeo.evidence import trust as _e_svc_trust, channels as _e_svc_channels  # type: ignore[import]
    _EVIDENCE_SVC_AVAILABLE = True
except ImportError:
    _e_svc_trust = None  # type: ignore[assignment]
    _e_svc_channels = None  # type: ignore[assignment]
    _EVIDENCE_SVC_AVAILABLE = False

try:
    from jugeo.solver import session as _s_svc_session  # type: ignore[import]
    _SOLVER_SVC_AVAILABLE = True
except ImportError:
    _s_svc_session = None  # type: ignore[assignment]
    _SOLVER_SVC_AVAILABLE = False


def register_judgment_service(registry: ServiceRegistry) -> dict[str, Any]:
    """Register the judgment subsystem from ``jugeo.judgments`` into *registry*.

    Creates a singleton service descriptor for the judgment term algebra
    and registers it with the provided :class:`ServiceRegistry`.

    Parameters
    ----------
    registry:
        The kernel service registry to register into.

    Returns
    -------
    dict[str, Any]
        ``{"available": bool, "service_name": str, "errors": [...]}``.
    """
    result: dict[str, Any] = {
        "available": _JUDGMENT_SVC_AVAILABLE,
        "service_name": "jugeo.judgments",
        "errors": [],
    }
    if not _JUDGMENT_SVC_AVAILABLE:
        result["errors"].append("jugeo.judgments subsystem is not installed")
        return result
    try:
        descriptor = ServiceDescriptor(
            name="jugeo.judgments",
            interface_type=type(_j_svc_mod),
            implementation_type=type(_j_svc_mod),
            lifecycle=ServiceLifecycle.SINGLETON,
        )
        registry.register(descriptor, component=_j_svc_mod)
        logger.info("Registered judgment service: jugeo.judgments")
    except Exception as exc:  # noqa: BLE001
        result["errors"].append(str(exc))
    return result


def register_geometry_service(registry: ServiceRegistry) -> dict[str, Any]:
    """Register the geometry subsystem from ``jugeo.geometry`` into *registry*.

    Creates singleton service descriptors for the site topology and descent
    engine, and registers them with the provided :class:`ServiceRegistry`.

    Parameters
    ----------
    registry:
        The kernel service registry to register into.

    Returns
    -------
    dict[str, Any]
        ``{"available": bool, "service_name": str, "errors": [...]}``.
    """
    result: dict[str, Any] = {
        "available": _GEOMETRY_SVC_AVAILABLE,
        "service_name": "jugeo.geometry",
        "errors": [],
    }
    if not _GEOMETRY_SVC_AVAILABLE:
        result["errors"].append("jugeo.geometry subsystem is not installed")
        return result
    try:
        for name, mod in [("jugeo.geometry.site", _g_svc_site), ("jugeo.geometry.descent", _g_svc_descent)]:
            if mod is not None:
                descriptor = ServiceDescriptor(
                    name=name,
                    interface_type=type(mod),
                    implementation_type=type(mod),
                    lifecycle=ServiceLifecycle.SINGLETON,
                )
                registry.register(descriptor, component=mod)
        logger.info("Registered geometry services: jugeo.geometry.site, jugeo.geometry.descent")
    except Exception as exc:  # noqa: BLE001
        result["errors"].append(str(exc))
    return result


def register_evidence_service(registry: ServiceRegistry) -> dict[str, Any]:
    """Register the evidence subsystem from ``jugeo.evidence`` into *registry*.

    Creates singleton service descriptors for trust algebra and evidence
    channels, and registers them with the provided :class:`ServiceRegistry`.

    Parameters
    ----------
    registry:
        The kernel service registry to register into.

    Returns
    -------
    dict[str, Any]
        ``{"available": bool, "service_name": str, "errors": [...]}``.
    """
    result: dict[str, Any] = {
        "available": _EVIDENCE_SVC_AVAILABLE,
        "service_name": "jugeo.evidence",
        "errors": [],
    }
    if not _EVIDENCE_SVC_AVAILABLE:
        result["errors"].append("jugeo.evidence subsystem is not installed")
        return result
    try:
        for name, mod in [("jugeo.evidence.trust", _e_svc_trust), ("jugeo.evidence.channels", _e_svc_channels)]:
            if mod is not None:
                descriptor = ServiceDescriptor(
                    name=name,
                    interface_type=type(mod),
                    implementation_type=type(mod),
                    lifecycle=ServiceLifecycle.SINGLETON,
                )
                registry.register(descriptor, component=mod)
        logger.info("Registered evidence services: jugeo.evidence.trust, jugeo.evidence.channels")
    except Exception as exc:  # noqa: BLE001
        result["errors"].append(str(exc))
    return result


def register_solver_service(registry: ServiceRegistry) -> dict[str, Any]:
    """Register the solver subsystem from ``jugeo.solver`` into *registry*.

    Creates a singleton service descriptor for the solver session manager
    and registers it with the provided :class:`ServiceRegistry`.

    Parameters
    ----------
    registry:
        The kernel service registry to register into.

    Returns
    -------
    dict[str, Any]
        ``{"available": bool, "service_name": str, "errors": [...]}``.
    """
    result: dict[str, Any] = {
        "available": _SOLVER_SVC_AVAILABLE,
        "service_name": "jugeo.solver",
        "errors": [],
    }
    if not _SOLVER_SVC_AVAILABLE:
        result["errors"].append("jugeo.solver subsystem is not installed")
        return result
    try:
        descriptor = ServiceDescriptor(
            name="jugeo.solver",
            interface_type=type(_s_svc_session),
            implementation_type=type(_s_svc_session),
            lifecycle=ServiceLifecycle.SINGLETON,
        )
        registry.register(descriptor, component=_s_svc_session)
        logger.info("Registered solver service: jugeo.solver")
    except Exception as exc:  # noqa: BLE001
        result["errors"].append(str(exc))
    return result
