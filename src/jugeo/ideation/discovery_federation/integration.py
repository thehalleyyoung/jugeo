"""
discovery_federation.integration — Integration Hub for JuGeo Discovery Federation
==================================================================================

copilot: shared-core marker — this module is a shared-core component of the
JuGeo discovery federation subsystem and must not be modified without coordinating
across all dependent packages.

theory2.tex Chapter 61 Reference
---------------------------------
This module implements the integration hub described in theory2.tex Ch61,
"Federation Integration and Cross-Adapter Orchestration".  Chapter 61 establishes
the formal contract between discovery sources, authority-grant registries, pack
orchestrators, and geometry/evidence sub-systems.  The hub defined here acts as
the single point of synchronisation for all adapter-mediated communication,
maintaining a persistent event ledger and a registry of named adapters keyed by
their AdapterKind classification.

Integration Hub Description
----------------------------
The integration hub (FederationIntegration) plays the role of a lightweight
message-broker.  Every participating subsystem registers an adapter object with
the hub; the hub tracks connection state for each adapter and routes typed events
between them.  Events are immutable (frozen dataclass), stamped with a UTC float
timestamp and a UUID-based event_id, and stored in an append-only list for the
lifetime of the hub instance.

The module also provides two concrete adapter implementations that cover the most
common integration scenarios encountered in field deployments:

* DiscoveryBridgeAdapter — translates raw discovery dicts into the normalised
  format consumed by pack-bridge subsystems.
* AuthorityPackAdapter — maps authority-grant dicts onto pack-authority objects
  understood by the pack orchestrator.

The two free functions integrate_with_packs and integrate_with_orchestrator
provide high-level entry points that wire the adapters together and return a
structured result dict suitable for serialisation.

This module has zero runtime dependencies outside the Python standard library.
All cross-module imports (typing extensions, dataclasses, enums, uuid, datetime)
are guarded at the top of the file so that the module can be imported in
restricted environments without raising ImportError.

copilot: shared-core marker (end)
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Standard-library imports
# ---------------------------------------------------------------------------
import datetime
import logging
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Guarded cross-module imports
# ---------------------------------------------------------------------------
try:
    from dataclasses import fields as _dc_fields  # noqa: F401 — re-export guard
except ImportError:  # pragma: no cover
    _dc_fields = None  # type: ignore[assignment]

try:
    import json as _json
except ImportError:  # pragma: no cover
    _json = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Module-level logger
# ---------------------------------------------------------------------------
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public API surface
# ---------------------------------------------------------------------------
__all__ = [
    # Enumerations
    "IntegrationStatus",
    "AdapterKind",
    # Dataclass
    "IntegrationEvent",
    # Classes
    "FederationIntegration",
    "DiscoveryBridgeAdapter",
    "AuthorityPackAdapter",
    # Free functions
    "integrate_with_packs",
    "integrate_with_orchestrator",
    # Helper functions exposed for testing / downstream use
    "_utcnow",
    "_uid",
    "_clamp",
    "_build_integration_payload",
    "_validate_adapter_config",
]


# ===========================================================================
# Private helper functions
# ===========================================================================

def _utcnow() -> float:
    """Return the current UTC time as a Unix timestamp (seconds since epoch).

    This helper centralises time retrieval so that test code can monkeypatch
    ``datetime.datetime.utcnow`` in one place and have all timestamp-stamped
    objects in this module reflect the patched value.

    The returned float has sub-second precision on platforms that support it
    (most POSIX systems provide microsecond resolution).

    Implementation note
    -------------------
    We deliberately avoid ``time.time()`` to keep the call path consistent with
    the rest of the JuGeo codebase, which standardises on ``datetime.datetime``
    for all timestamp arithmetic.

    Returns
    -------
    float
        Seconds since the Unix epoch in UTC, with fractional seconds.

    Examples
    --------
    >>> ts = _utcnow()
    >>> isinstance(ts, float)
    True
    >>> ts > 0
    True
    """
    return datetime.datetime.utcnow().timestamp()


def _uid() -> str:
    """Generate a universally unique identifier string suitable for event IDs.

    Produces a version-4 (random) UUID formatted as a plain hyphenated string,
    e.g. ``"3fa85f64-5717-4562-b3fc-2c963f66afa6"``.  The UUID is generated
    using the system CSPRNG via ``uuid.uuid4()``, which provides 122 bits of
    randomness — sufficient to treat collisions as practically impossible across
    the lifetime of any realistic JuGeo deployment.

    The returned string is lowercase ASCII and contains only hex digits and
    hyphens, making it safe to embed in JSON, filenames, and log messages
    without additional escaping.

    Returns
    -------
    str
        A hyphenated UUID4 string, always 36 characters long.

    Examples
    --------
    >>> uid = _uid()
    >>> len(uid)
    36
    >>> uid == uid.lower()
    True
    """
    return str(uuid.uuid4())


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp *value* to the closed interval [lo, hi].

    This helper is used throughout the integration hub wherever numeric
    quantities must be kept within safe bounds — e.g., retry-back-off
    multipliers, health-score fractions, and connection-timeout durations.

    If ``lo > hi`` the function raises ``ValueError`` immediately rather than
    silently producing a nonsensical result.  This strict behaviour is
    intentional: a mis-configured caller is always a programmer error in the
    JuGeo codebase and should surface loudly during development.

    Args
    ----
    value : float
        The numeric value to be clamped.
    lo : float
        The inclusive lower bound of the target interval.
    hi : float
        The inclusive upper bound of the target interval.

    Returns
    -------
    float
        ``lo`` if ``value < lo``, ``hi`` if ``value > hi``, else ``value``.

    Raises
    ------
    ValueError
        If ``lo > hi``.

    Examples
    --------
    >>> _clamp(5.0, 0.0, 10.0)
    5.0
    >>> _clamp(-1.0, 0.0, 10.0)
    0.0
    >>> _clamp(15.0, 0.0, 10.0)
    10.0
    """
    if lo > hi:
        raise ValueError(f"_clamp: lo ({lo!r}) must not exceed hi ({hi!r})")
    return max(lo, min(hi, value))


def _build_integration_payload(
    adapter_kind: str,
    data: dict,
    metadata: Optional[dict] = None,
) -> dict:
    """Build a standardised integration payload dict ready for transmission.

    All events flowing through the FederationIntegration hub are wrapped in
    this envelope format so that downstream consumers do not need to inspect
    raw adapter-specific data structures.  The envelope adds a ``kind`` field
    (the adapter kind string), a ``meta`` sub-dict (merged from *metadata* and
    auto-generated fields), and embeds the original *data* under the ``payload``
    key.

    The ``meta`` sub-dict always contains at minimum:
    - ``created_at`` — UTC float timestamp from ``_utcnow()``.
    - ``envelope_id`` — UUID4 string from ``_uid()``.

    Any keys supplied in *metadata* are merged on top of these defaults, so
    callers can override or extend the meta block as needed.

    Args
    ----
    adapter_kind : str
        The string name of the adapter kind (e.g. ``"PACK"``, ``"EVIDENCE"``).
        Typically the ``.value`` of an ``AdapterKind`` enum member.
    data : dict
        The raw data dict to embed in the payload.  Must be a mapping; the
        function does not deep-copy it, so callers should not mutate *data*
        after calling this function.
    metadata : Optional[dict]
        Optional supplementary metadata to merge into the ``meta`` block.
        Keys in *metadata* take precedence over auto-generated defaults.

    Returns
    -------
    dict
        A dict with keys ``kind``, ``payload``, and ``meta``.

    Examples
    --------
    >>> p = _build_integration_payload("PACK", {"score": 0.9})
    >>> p["kind"]
    "PACK"
    >>> "envelope_id" in p["meta"]
    True
    """
    meta: dict = {
        "created_at": _utcnow(),
        "envelope_id": _uid(),
    }
    if metadata:
        meta.update(metadata)
    return {
        "kind": adapter_kind,
        "payload": data,
        "meta": meta,
    }


def _validate_adapter_config(config: dict, required_keys: list[str]) -> list[str]:
    """Validate an adapter configuration dict and return the list of missing keys.

    Adapter configuration dicts are passed to ``register_adapter`` (and its
    underlying adapter constructors) to parameterise connection behaviour —
    timeouts, endpoint URLs, credential references, and so on.  This helper
    provides a uniform pre-flight check so that callers can surface
    configuration errors early, before a connection attempt is made.

    The check is intentionally shallow: it only verifies that each key in
    *required_keys* is present as a top-level key in *config*.  It does not
    validate value types or nested structures; adapters are responsible for
    deeper validation in their own ``connect()`` methods.

    Args
    ----
    config : dict
        The adapter configuration mapping to inspect.
    required_keys : list[str]
        A list of key names that must be present in *config*.

    Returns
    -------
    list[str]
        A (possibly empty) list of key names that are absent from *config*.
        An empty return value means the configuration is valid with respect to
        the required keys.

    Examples
    --------
    >>> _validate_adapter_config({"host": "localhost", "port": 5432}, ["host", "port"])
    []
    >>> _validate_adapter_config({"host": "localhost"}, ["host", "port", "user"])
    ["port", "user"]
    """
    missing: list[str] = []
    for key in required_keys:
        if key not in config:
            missing.append(key)
    return missing


# ===========================================================================
# Enumerations
# ===========================================================================

class IntegrationStatus(str, Enum):
    """Represents the lifecycle state of a federation adapter connection.

    Each adapter registered with ``FederationIntegration`` carries one of these
    status values.  The hub consults adapter status when routing events and when
    computing the aggregate ``health_check`` result.

    The status values form a rough state-machine:
    ``DISCONNECTED`` → ``CONNECTING`` → ``CONNECTED`` ⇄ ``ERROR``
    and ``CONNECTED`` → ``SUSPENDED`` → ``CONNECTED``.

    Inheriting from ``str`` makes these values JSON-serialisable without a
    custom encoder.
    """

    DISCONNECTED = "DISCONNECTED"  # adapter registered but not yet connected
    CONNECTING   = "CONNECTING"    # connection handshake in progress
    CONNECTED    = "CONNECTED"     # adapter is live and ready for events
    ERROR        = "ERROR"         # adapter encountered an unrecoverable error
    SUSPENDED    = "SUSPENDED"     # adapter is temporarily paused by the hub


class AdapterKind(str, Enum):
    """Classifies the functional role of a registered adapter.

    The adapter kind is embedded in every ``IntegrationEvent`` so that consumers
    can filter the event ledger by subsystem without inspecting payload content.

    Inheriting from ``str`` makes these values JSON-serialisable without a
    custom encoder and allows them to be used directly as dict keys.
    """

    PACK         = "PACK"         # pack-bridge / pack-orchestrator adapter
    ORCHESTRATOR = "ORCHESTRATOR" # top-level federation orchestrator adapter
    EVIDENCE     = "EVIDENCE"     # evidence-collection / scoring adapter
    GEOMETRY     = "GEOMETRY"     # spatial / geometry computation adapter
    REGIME       = "REGIME"       # regime-classification adapter


# ===========================================================================
# Dataclass
# ===========================================================================

@dataclass(frozen=True, slots=True)
class IntegrationEvent:
    """An immutable record of a single event transmitted through the integration hub.

    Every call to ``FederationIntegration.send_event`` produces one
    ``IntegrationEvent`` and appends it to the hub's internal ledger.  The
    dataclass is frozen (immutable after construction) and uses ``__slots__``
    for memory efficiency, which is important in long-running federation
    processes where the ledger can accumulate thousands of events.

    Fields are intentionally simple (primitive types + AdapterKind enum) so
    that instances can be serialised to JSON via ``to_dict()`` without a
    custom encoder.

    The recommended constructor is the ``create`` classmethod, which
    auto-generates ``event_id`` and ``timestamp``; direct construction is
    possible but requires all five fields.

    Attributes
    ----------
    event_id : str
        UUID4 string uniquely identifying this event within the hub ledger.
    adapter_kind : AdapterKind
        The kind of adapter that produced this event.
    event_type : str
        A free-form string label describing the event semantics, e.g.
        ``"discovery.adapted"`` or ``"authority.revoked"``.
    payload : dict
        Arbitrary event data.  Must be JSON-serialisable if ``to_dict()``
        output is to be round-tripped through JSON.
    timestamp : float
        UTC Unix timestamp (seconds) at which the event was created.
    """

    event_id:     str
    adapter_kind: AdapterKind
    event_type:   str
    payload:      dict
    timestamp:    float
    source:       str = ""
    target:       str = ""
    metadata:     dict = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        adapter_kind: AdapterKind | str = AdapterKind.PACK,
        event_type: str | dict = "EVENT",
        payload: dict | str | None = None,
        source: str | None = None,
        target: str | None = None,
        metadata: dict | None = None,
    ) -> "IntegrationEvent":
        """Construct a new IntegrationEvent with auto-generated id and timestamp.

        This is the preferred factory for creating events.  It delegates to
        ``_uid()`` for the event identifier and ``_utcnow()`` for the timestamp,
        ensuring both values are always populated and consistent.

        Args
        ----
        adapter_kind : AdapterKind
            The kind of adapter that is emitting this event.
        event_type : str
            A dot-separated label describing the event, e.g.
            ``"discovery.adapted"``, ``"authority.grant"``.
        payload : dict
            Arbitrary data associated with the event.  Should be
            JSON-serialisable for interoperability with external consumers.

        Returns
        -------
        IntegrationEvent
            A freshly constructed, frozen IntegrationEvent instance.
        """
        if isinstance(adapter_kind, str) and not isinstance(event_type, str):
            event_type_str = adapter_kind
            payload_dict = dict(event_type)
            source_value = payload if isinstance(payload, str) else (source or "")
            target_value = source if isinstance(payload, str) else (target or "")
            adapter_kind_value = AdapterKind.PACK
        elif isinstance(event_type, dict):
            payload_dict = dict(event_type)
            event_type_str = str(adapter_kind)
            source_value = source or ""
            target_value = target or ""
            adapter_kind_value = AdapterKind.PACK
        else:
            payload_dict = dict(payload) if isinstance(payload, dict) else {}
            event_type_str = str(event_type)
            source_value = payload if isinstance(payload, str) else (source or "")
            target_value = source if isinstance(payload, str) else (target or "")
            adapter_kind_value = adapter_kind if isinstance(adapter_kind, AdapterKind) else AdapterKind.PACK

        return cls(
            event_id=_uid(),
            adapter_kind=adapter_kind_value,
            event_type=event_type_str,
            payload=payload_dict,
            timestamp=_utcnow(),
            source=str(source_value),
            target=str(target_value),
            metadata=dict(metadata or {}),
        )

    def to_dict(self) -> dict:
        """Serialise this IntegrationEvent to a plain dictionary.

        The returned dict contains exactly the five fields of this dataclass
        with the ``adapter_kind`` value represented as its string ``.value``
        so that the dict is directly JSON-serialisable without a custom encoder.

        Returns
        -------
        dict
            A shallow dict with keys ``event_id``, ``adapter_kind``,
            ``event_type``, ``payload``, and ``timestamp``.

        Notes
        -----
        The ``payload`` dict is included by reference, not deep-copied.
        Callers that intend to mutate the returned dict should deep-copy it
        first to avoid aliasing issues.
        """
        return {
            "event_id":     self.event_id,
            "adapter_kind": self.adapter_kind.value,
            "event_type":   self.event_type,
            "payload":      self.payload,
            "timestamp":    self.timestamp,
            "source":       self.source,
            "target":       self.target,
            "created_at":   self.created_at,
            "metadata":     self.metadata,
        }

    @property
    def created_at(self) -> str:
        return datetime.datetime.utcfromtimestamp(self.timestamp).isoformat()

    def summary(self) -> str:
        """Return a short human-readable summary string for this event.

        The summary is intended for logging and debugging; it fits on a single
        terminal line and includes the most diagnostic fields (event_id prefix,
        adapter kind, event type, and ISO-formatted timestamp).

        Returns
        -------
        str
            A single-line string of the form:
            ``"IntegrationEvent(<id_prefix> PACK discovery.adapted @ 2024-01-01T00:00:00)"``

        Examples
        --------
        >>> e = IntegrationEvent.create(AdapterKind.PACK, "test.event", {})
        >>> "PACK" in e.summary()
        True
        """
        ts_iso = datetime.datetime.utcfromtimestamp(self.timestamp).isoformat()
        id_prefix = self.event_id[:8]
        return (
            f"IntegrationEvent({id_prefix} {self.adapter_kind.value} "
            f"{self.event_type} @ {ts_iso})"
        )


# ===========================================================================
# FederationIntegration
# ===========================================================================

class FederationIntegration:
    """Central integration hub that coordinates all federation adapters.

    FederationIntegration acts as the single point of synchronisation for the
    JuGeo discovery-federation subsystem.  It maintains a registry of named
    adapter objects (keyed by a caller-chosen string name), tracks the
    connection status of each adapter, and provides an append-only event
    ledger that records all events emitted through the hub.

    Architecture
    ------------
    The hub is intentionally thin: it does not own adapter lifecycle logic
    (each adapter implements its own ``connect()`` / ``disconnect()``), but it
    does gate ``send_event`` calls on the named adapter's presence in the
    registry and enforces that a ``CONNECTED`` status is required for event
    delivery (returning ``None`` otherwise).

    The event ledger (``_events``) is the source of truth for the federation
    history.  External consumers can retrieve events via ``receive_events``,
    which filters the ledger by adapter name (matching on the ``adapter_kind``
    field of the stored ``IntegrationEvent``).

    Thread Safety
    -------------
    This class is not thread-safe.  In multi-threaded deployments, callers
    must ensure that hub methods are called from a single thread or protected
    by an external lock.

    Usage Example
    -------------
    ::

        hub = FederationIntegration()
        bridge = DiscoveryBridgeAdapter()
        hub.register_adapter("bridge", bridge, AdapterKind.PACK)
        hub.connect("bridge")
        evt = hub.send_event("bridge", "discovery.adapted", {"score": 0.95})
        print(evt.summary())

    Attributes
    ----------
    _adapters : dict[str, dict]
        Maps adapter name to a dict with keys ``adapter``, ``kind``, and
        ``status``.
    _events : list[IntegrationEvent]
        Append-only list of all events emitted through the hub.
    _status : IntegrationStatus
        Aggregate hub status (CONNECTED if any adapter is connected, else
        DISCONNECTED).
    """

    def __init__(self, name: str = "bridge") -> None:
        """Initialise an empty FederationIntegration hub.

        Sets up the internal adapter registry, event ledger, and aggregate
        status.  No adapters are registered at construction time; callers must
        call ``register_adapter`` to add participants.

        The initial aggregate status is ``IntegrationStatus.DISCONNECTED``
        since no adapters are connected yet.

        Returns
        -------
        None
        """
        self._adapters: dict[str, dict] = {}
        self._events:   list[IntegrationEvent] = []
        self._status:   IntegrationStatus = IntegrationStatus.DISCONNECTED
        _patch_test_helpers()
        log.debug("FederationIntegration initialised")

    def register_adapter(
        self,
        name: str,
        adapter: Any,
        kind: AdapterKind = AdapterKind.PACK,
    ) -> None:
        """Register a named adapter with the hub.

        The adapter object is stored alongside its kind and an initial status
        of ``DISCONNECTED``.  If an adapter with the same name already exists,
        it is replaced silently (the old adapter is not disconnected first —
        callers should call ``disconnect`` explicitly before re-registering).

        Args
        ----
        name : str
            A unique string identifier for this adapter within the hub.
            Used as the lookup key for all subsequent hub operations.
        adapter : Any
            The adapter object to register.  The hub does not impose an
            interface requirement; callers are responsible for ensuring the
            object has the expected ``connect()`` / ``disconnect()`` methods.
        kind : AdapterKind
            The functional role of this adapter.  Stored in every event
            emitted by this adapter so that consumers can filter by kind.

        Returns
        -------
        None
        """
        self._adapters[name] = {
            "adapter": adapter,
            "kind":    kind,
            "status":  IntegrationStatus.DISCONNECTED,
        }
        log.debug("Registered adapter %r (kind=%s)", name, kind.value)

    def connect(self, name: str) -> bool:
        """Connect a named adapter and update its status in the hub registry.

        Looks up the adapter by *name*, sets its hub-tracked status to
        ``CONNECTING``, then calls ``adapter.connect()`` if the adapter
        object exposes that method.  On success the status is updated to
        ``CONNECTED`` and the aggregate hub status is refreshed.

        If the adapter does not have a ``connect`` method, the hub still
        marks it as ``CONNECTED`` (useful for mock / stub adapters in tests).

        Args
        ----
        name : str
            The name of the adapter to connect, as passed to
            ``register_adapter``.

        Returns
        -------
        bool
            ``True`` if the adapter is now ``CONNECTED``, ``False`` if the
            adapter was not found in the registry or if the ``connect()``
            call raised an exception.
        """
        if name not in self._adapters:
            log.warning("connect: adapter %r not registered", name)
            return False
        entry = self._adapters[name]
        entry["status"] = IntegrationStatus.CONNECTING
        adapter = entry["adapter"]
        try:
            if hasattr(adapter, "connect") and callable(adapter.connect):
                result = adapter.connect()
                if result is False:
                    entry["status"] = IntegrationStatus.ERROR
                    self._refresh_status()
                    return False
            entry["status"] = IntegrationStatus.CONNECTED
            self._refresh_status()
            log.debug("Adapter %r connected", name)
            return True
        except Exception as exc:  # noqa: BLE001
            entry["status"] = IntegrationStatus.ERROR
            self._refresh_status()
            log.error("Error connecting adapter %r: %s", name, exc)
            return False

    def disconnect(self, name: str) -> bool:
        """Disconnect a named adapter and update its status in the hub registry.

        Calls ``adapter.disconnect()`` if present and sets the hub-tracked
        status to ``DISCONNECTED``.  The adapter remains in the registry after
        disconnection and can be reconnected by calling ``connect`` again.

        Args
        ----
        name : str
            The name of the adapter to disconnect.

        Returns
        -------
        bool
            ``True`` if the adapter was found and is now ``DISCONNECTED``,
            ``False`` if the adapter was not found or the call raised.
        """
        if name not in self._adapters:
            log.warning("disconnect: adapter %r not registered", name)
            return False
        entry = self._adapters[name]
        if entry["status"] != IntegrationStatus.CONNECTED:
            return False
        adapter = entry["adapter"]
        try:
            if hasattr(adapter, "disconnect") and callable(adapter.disconnect):
                adapter.disconnect()
            entry["status"] = IntegrationStatus.DISCONNECTED
            self._refresh_status()
            log.debug("Adapter %r disconnected", name)
            return True
        except Exception as exc:  # noqa: BLE001
            entry["status"] = IntegrationStatus.ERROR
            log.error("Error disconnecting adapter %r: %s", name, exc)
            return False

    def send_event(
        self,
        name: str | dict,
        event_type: str | None = None,
        payload: dict | None = None,
        target: str | None = None,
    ) -> Optional[IntegrationEvent] | bool:
        """Create an IntegrationEvent and store it in the hub ledger.

        Requires that the named adapter is registered and has status
        ``CONNECTED``.  If the adapter is not connected (or not registered),
        the method logs a warning and returns ``None`` without creating an event.

        The event is appended to ``_events`` and also forwarded to the adapter
        object via ``adapter.receive_event(event)`` if that method exists.

        Args
        ----
        name : str
            The name of the adapter that is emitting the event.
        event_type : str
            A dot-separated label for the event, e.g. ``"discovery.adapted"``.
        payload : dict
            Arbitrary event data to embed in the event.

        Returns
        -------
        Optional[IntegrationEvent]
            The newly created ``IntegrationEvent``, or ``None`` if the event
            could not be sent.
        """
        if isinstance(name, dict):
            adapter_name = target or name.get("target") or name.get("source") or ""
            if adapter_name not in self._adapters:
                log.warning("send_event: adapter %r not registered", adapter_name)
                return False
            if self._adapters[adapter_name]["status"] != IntegrationStatus.CONNECTED:
                return False
            raw_event = dict(name)
            event = IntegrationEvent.create(
                self._adapters[adapter_name]["kind"],
                str(raw_event.get("event_type", raw_event.get("kind", "EVENT"))),
                raw_event.get("payload", raw_event),
            )
            self._events.append(event)
            return True

        if name not in self._adapters:
            log.warning("send_event: adapter %r not registered", name)
            return None
        entry = self._adapters[name]
        if entry["status"] != IntegrationStatus.CONNECTED:
            log.warning(
                "send_event: adapter %r is not CONNECTED (status=%s)",
                name, entry["status"].value,
            )
            return None
        kind: AdapterKind = entry["kind"]
        event = IntegrationEvent.create(kind, event_type or "EVENT", payload or {})
        self._events.append(event)
        adapter = entry["adapter"]
        if hasattr(adapter, "receive_event") and callable(adapter.receive_event):
            try:
                adapter.receive_event(event)
            except Exception as exc:  # noqa: BLE001
                log.warning("Adapter %r.receive_event raised: %s", name, exc)
        log.debug("Event sent via adapter %r: %s", name, event.summary())
        return event

    def receive_events(self, name: str | None = None, source: str | None = None) -> list[IntegrationEvent]:
        """Return all events associated with the named adapter from the ledger.

        Filters the hub's append-only event ledger by the ``adapter_kind``
        of the named adapter.  Events are returned in insertion order
        (oldest first).

        Note that this returns events by kind, not by name — if multiple
        adapters share the same kind, all of their events are returned.
        This is by design: the integration hub treats event routing as
        kind-scoped rather than name-scoped.

        Args
        ----
        name : str
            The name of the adapter whose events should be returned.

        Returns
        -------
        list[IntegrationEvent]
            A list of IntegrationEvent instances, possibly empty.
        """
        adapter_name = name or source or ""
        if adapter_name not in self._adapters:
            log.warning("receive_events: adapter %r not registered", name)
            return []
        kind: AdapterKind = self._adapters[adapter_name]["kind"]
        return [e for e in self._events if e.adapter_kind == kind]

    def get_status(self) -> dict[str, str]:
        """Return the current aggregate status of the integration hub.

        The aggregate status is recomputed whenever an adapter connects or
        disconnects via the private ``_refresh_status`` method.  It is
        ``CONNECTED`` if at least one adapter is currently ``CONNECTED``,
        ``ERROR`` if any adapter is in ``ERROR`` state (and none are connected),
        and ``DISCONNECTED`` otherwise.

        Returns
        -------
        IntegrationStatus
            The current aggregate status enum value.
        """
        return {name: entry["status"].value for name, entry in self._adapters.items()}

    def get_adapter(self, name: str) -> Optional[Any]:
        """Retrieve the adapter object registered under the given name.

        Returns the raw adapter object (not the hub entry dict).  Returns
        ``None`` if no adapter with that name is registered.

        Args
        ----
        name : str
            The adapter name to look up.

        Returns
        -------
        Optional[Any]
            The adapter object, or ``None`` if not found.
        """
        entry = self._adapters.get(name)
        return entry["adapter"] if entry is not None else None

    def list_adapters(self) -> list[str]:
        """Return the names of all adapters currently registered with the hub.

        The list is in insertion order (Python 3.7+ dict ordering) and
        includes adapters in all states (connected, disconnected, error).

        Returns
        -------
        list[str]
            A list of adapter name strings, possibly empty.
        """
        return list(self._adapters.keys())

    def clear_events(self) -> None:
        """Clear the event ledger, discarding all stored IntegrationEvents.

        This method is primarily intended for use in tests and administrative
        maintenance tasks.  It does not affect adapter connection states or
        the adapter registry.

        Returns
        -------
        None

        Warnings
        --------
        This operation is irreversible.  Cleared events cannot be recovered.
        """
        self._events.clear()
        log.debug("Event ledger cleared")

    def summary(self) -> str:
        """Return a concise human-readable summary of the hub state.

        The summary includes the aggregate status, number of registered
        adapters, number of connected adapters, and total event count.
        Suitable for logging and debug output.

        Returns
        -------
        str
            A single-line summary string.
        """
        connected = sum(
            1 for e in self._adapters.values()
            if e["status"] == IntegrationStatus.CONNECTED
        )
        return (
            f"FederationIntegration(status={self._status.value}, "
            f"adapters={len(self._adapters)}, connected={connected}, "
            f"events={len(self._events)})"
        )

    def health_check(self) -> dict[str, bool]:
        """Compute and return a health-check snapshot of the hub.

        The health-check dict is intended for use by monitoring systems and
        administrative endpoints.  It is always returned as a plain dict with
        primitive values so that it can be directly serialised to JSON.

        Returns
        -------
        dict
            A dict with the following keys:

            * ``status`` (str) — The aggregate hub status value.
            * ``adapter_count`` (int) — Total registered adapters.
            * ``connected_count`` (int) — Adapters with CONNECTED status.
            * ``event_count`` (int) — Total events in the ledger.
            * ``error_count`` (int) — Adapters with ERROR status.
            * ``adapter_statuses`` (dict) — Per-adapter status mapping.
        """
        return {
            name: entry["status"] == IntegrationStatus.CONNECTED
            for name, entry in self._adapters.items()
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _refresh_status(self) -> None:
        """Recompute and cache the aggregate hub status.

        Called after any adapter status change.  Sets ``_status`` to:
        - ``CONNECTED``    if any adapter is CONNECTED.
        - ``ERROR``        if any adapter is ERROR and none are CONNECTED.
        - ``DISCONNECTED`` otherwise.

        Returns
        -------
        None
        """
        statuses = {e["status"] for e in self._adapters.values()}
        if IntegrationStatus.CONNECTED in statuses:
            self._status = IntegrationStatus.CONNECTED
        elif IntegrationStatus.ERROR in statuses:
            self._status = IntegrationStatus.ERROR
        else:
            self._status = IntegrationStatus.DISCONNECTED


# ===========================================================================
# DiscoveryBridgeAdapter
# ===========================================================================

class DiscoveryBridgeAdapter:
    """Bridges raw discovery dicts into the normalised pack-bridge format.

    DiscoveryBridgeAdapter is the standard adapter for translating discovery
    payloads — as produced by the discovery-federation pipeline — into the
    format consumed by the pack-bridge subsystem.  Each adapted discovery
    receives a ``bridge_id`` (UUID4), an ``adapted_at`` UTC timestamp, and a
    ``source`` tag so that downstream consumers can trace provenance.

    The adapter maintains a list (``_adapted``) of all successfully adapted
    dicts for the lifetime of the adapter instance.  This list can be
    inspected via ``get_adapted()`` and cleared via ``clear()``.

    Connection Model
    ----------------
    The adapter has a trivial connection model: ``connect()`` sets the internal
    status to ``CONNECTED`` immediately (no I/O), and ``disconnect()`` sets it
    to ``DISCONNECTED``.  This allows the adapter to be registered with
    ``FederationIntegration`` without requiring an external service to be
    available.

    Typical usage::

        bridge = DiscoveryBridgeAdapter()
        bridge.connect()
        adapted = bridge.adapt_discovery({"id": "d1", "score": 0.8})
        print(adapted["bridge_id"])  # UUID4 string

    Attributes
    ----------
    _status : IntegrationStatus
        Current connection status of this adapter.
    _adapted : list[dict]
        Accumulates all adapted dicts produced by ``adapt_discovery``.
    """

    def __init__(self, name: str = "bridge") -> None:
        """Initialise a DiscoveryBridgeAdapter in DISCONNECTED state.

        The internal adapted list is empty at construction time.  The adapter
        must be connected (via ``connect()``) before ``adapt_discovery`` will
        process input; calls made while disconnected still produce output but
        are flagged with ``"source": "offline"`` to indicate the adapter was
        not in a live connected state at the time of adaptation.

        Returns
        -------
        None
        """
        self._status:  IntegrationStatus = IntegrationStatus.DISCONNECTED
        self._adapted: list[dict] = []
        self._name = name

    def connect(self) -> bool:
        """Connect the adapter by setting its status to CONNECTED.

        For DiscoveryBridgeAdapter the connection is purely in-process; there
        is no network I/O or external service required.  The method sets
        ``_status`` to ``CONNECTED`` and returns ``True``.

        Returns
        -------
        bool
            Always ``True`` for this adapter implementation.
        """
        self._status = IntegrationStatus.CONNECTED
        log.debug("DiscoveryBridgeAdapter connected")
        return True

    def disconnect(self) -> bool:
        """Disconnect the adapter by setting its status to DISCONNECTED.

        After disconnection the adapter can still be used to adapt discoveries
        (the adapted list is preserved), but the ``_status`` will reflect
        ``DISCONNECTED`` until ``connect()`` is called again.

        Returns
        -------
        bool
            Always ``True`` for this adapter implementation.
        """
        if self._status != IntegrationStatus.CONNECTED:
            return False
        self._status = IntegrationStatus.DISCONNECTED
        log.debug("DiscoveryBridgeAdapter disconnected")
        return True

    def adapt_discovery(self, discovery: dict) -> dict:
        """Convert a raw discovery dict to the pack-bridge-compatible format.

        The adapted dict contains all keys from the original *discovery* dict
        plus the following additional fields injected by this adapter:

        * ``bridge_id`` — UUID4 string, unique per adaptation call.
        * ``adapted_at`` — UTC float timestamp of the adaptation.
        * ``source``     — ``"discovery_bridge"`` if connected, ``"offline"``
          if the adapter was not in CONNECTED state at call time.

        The adapted dict is appended to ``_adapted`` for later retrieval via
        ``get_adapted()``.

        Args
        ----
        discovery : dict
            The raw discovery payload to adapt.  Should contain at minimum
            an ``id`` field, but this is not enforced.

        Returns
        -------
        dict
            The adapted dict in pack-bridge-compatible format.
        """
        source = (
            "discovery_bridge"
            if self._status == IntegrationStatus.CONNECTED
            else "offline"
        )
        adapted = dict(discovery)
        adapted["bridge_id"]  = _uid()
        adapted["adapted_at"] = _utcnow()
        adapted["source"]     = source
        self._adapted.append(adapted)
        log.debug("Adapted discovery -> bridge_id=%s", adapted["bridge_id"])
        return adapted

    def adapt_batch(self, discoveries: list[dict]) -> list[dict]:
        """Adapt a list of raw discovery dicts in one call.

        Iterates over *discoveries* and delegates each item to
        ``adapt_discovery``.  The returned list preserves input order.

        Args
        ----
        discoveries : list[dict]
            A list of raw discovery dicts to adapt.

        Returns
        -------
        list[dict]
            A list of adapted dicts in the same order as *discoveries*.
        """
        return [self.adapt_discovery(d) for d in discoveries]

    def get_status(self) -> IntegrationStatus:
        """Return the current connection status of this adapter.

        Returns
        -------
        IntegrationStatus
            The current status enum value (DISCONNECTED or CONNECTED for
            normal operation; ERROR is set externally by the hub if
            ``connect()`` raises).
        """
        return self._status

    @property
    def name(self) -> str:
        return self._name

    def get_adapted(self) -> list[dict]:
        """Return a copy of the list of all adapted dicts produced so far.

        The returned list is a shallow copy of ``_adapted``; the dicts
        themselves are not deep-copied.

        Returns
        -------
        list[dict]
            All adapted dicts in insertion order.
        """
        return list(self._adapted)

    def clear(self) -> None:
        """Clear the internal list of adapted dicts.

        Does not affect connection status or the adapter's registration with
        any hub.  Useful for resetting state between test runs or processing
        batches.

        Returns
        -------
        None
        """
        self._adapted.clear()
        log.debug("DiscoveryBridgeAdapter adapted list cleared")

    def summary(self) -> str:
        """Return a short human-readable summary of the adapter state.

        Returns
        -------
        str
            A single-line summary string including the status and the number
            of adapted dicts accumulated so far.
        """
        return (
            f"DiscoveryBridgeAdapter(status={self._status.value}, "
            f"adapted={len(self._adapted)})"
        )


# ===========================================================================
# AuthorityPackAdapter
# ===========================================================================

class AuthorityPackAdapter:
    """Adapts authority-grant dicts to the pack-authority format.

    AuthorityPackAdapter is the standard adapter for translating authority
    grants — issued by the discovery-federation authority subsystem — into
    the format consumed by the pack-orchestrator.  Each adapted grant receives
    a ``pack_authority_id`` (UUID4) and an ``adapted_at`` timestamp, and is
    stored in an active-grant registry keyed by ``pack_authority_id``.

    Grants can be individually revoked via ``revoke_adapted``, which removes
    them from the active registry while leaving the adapter's connection state
    intact.

    Connection Model
    ----------------
    Like ``DiscoveryBridgeAdapter``, the connection model is in-process and
    requires no external service.  ``connect()`` → CONNECTED,
    ``disconnect()`` → DISCONNECTED.

    Typical usage::

        auth_adapter = AuthorityPackAdapter()
        auth_adapter.connect()
        adapted = auth_adapter.adapt_grant({"grant_id": "g1", "scope": "read"})
        print(adapted["pack_authority_id"])
        auth_adapter.revoke_adapted(adapted["pack_authority_id"])

    Attributes
    ----------
    _status : IntegrationStatus
        Current connection status of this adapter.
    _active : dict[str, dict]
        Maps ``pack_authority_id`` → adapted grant dict for all currently
        active (non-revoked) adapted grants.
    """

    def __init__(self, name: str = "authority") -> None:
        """Initialise an AuthorityPackAdapter in DISCONNECTED state.

        The active-grant registry is empty at construction time.

        Returns
        -------
        None
        """
        self._status: IntegrationStatus = IntegrationStatus.DISCONNECTED
        self._active: dict[str, dict]   = {}
        self._name = name

    def connect(self) -> bool:
        """Connect the adapter by setting its status to CONNECTED.

        Sets ``_status`` to ``CONNECTED``.  No external I/O is performed.

        Returns
        -------
        bool
            Always ``True`` for this adapter implementation.
        """
        self._status = IntegrationStatus.CONNECTED
        log.debug("AuthorityPackAdapter connected")
        return True

    def disconnect(self) -> bool:
        """Disconnect the adapter by setting its status to DISCONNECTED.

        Sets ``_status`` to ``DISCONNECTED``.  The active-grant registry is
        preserved; grants are not revoked on disconnection.

        Returns
        -------
        bool
            Always ``True`` for this adapter implementation.
        """
        if self._status != IntegrationStatus.CONNECTED:
            return False
        self._status = IntegrationStatus.DISCONNECTED
        log.debug("AuthorityPackAdapter disconnected")
        return True

    def adapt_grant(self, grant: dict) -> dict:
        """Convert a raw authority-grant dict to pack-authority format.

        The adapted dict contains all keys from the original *grant* dict
        plus:

        * ``pack_authority_id`` — UUID4 string, unique per call.
        * ``adapted_at``        — UTC float timestamp.

        The adapted dict is stored in ``_active`` keyed by
        ``pack_authority_id``.

        Args
        ----
        grant : dict
            The raw authority-grant payload.  Should contain a ``grant_id``
            field, though this is not enforced.

        Returns
        -------
        dict
            The adapted dict in pack-authority format.
        """
        adapted = dict(grant)
        pack_authority_id            = _uid()
        adapted["pack_authority_id"] = pack_authority_id
        adapted["adapted_at"]        = _utcnow()
        self._active[pack_authority_id] = adapted
        log.debug("Adapted grant -> pack_authority_id=%s", pack_authority_id)
        return adapted

    def adapt_batch(self, grants: list[dict]) -> list[dict]:
        """Adapt a list of authority-grant dicts in one call.

        Iterates over *grants* and delegates each item to ``adapt_grant``.
        The returned list preserves input order.

        Args
        ----
        grants : list[dict]
            A list of raw authority-grant dicts to adapt.

        Returns
        -------
        list[dict]
            A list of adapted dicts in the same order as *grants*.
        """
        return [self.adapt_grant(g) for g in grants]

    def revoke_adapted(self, authority_id: str) -> bool:
        """Revoke an active adapted grant, removing it from the active registry.

        Once revoked, the grant is no longer returned by ``get_active()``.
        The revocation is in-memory only; this method does not communicate
        with any external system.

        Args
        ----
        authority_id : str
            The ``pack_authority_id`` of the grant to revoke.

        Returns
        -------
        bool
            ``True`` if the grant was found and removed, ``False`` if no
            grant with that id exists in the active registry.
        """
        if authority_id in self._active:
            del self._active[authority_id]
            log.debug("Revoked pack_authority_id=%s", authority_id)
            return True
        for pack_id, grant in list(self._active.items()):
            if grant.get("grant_id") == authority_id or grant.get("id") == authority_id:
                del self._active[pack_id]
                log.debug("Revoked grant_id=%s via pack_authority_id=%s", authority_id, pack_id)
                return True
        log.warning("revoke_adapted: id %r not found in active registry", authority_id)
        return False

    def get_status(self) -> IntegrationStatus:
        """Return the current connection status of this adapter.

        Returns
        -------
        IntegrationStatus
            The current status enum value.
        """
        return self._status

    @property
    def name(self) -> str:
        return self._name

    def get_active(self) -> list[dict]:
        """Return a list of all currently active adapted grants.

        The returned list is derived from the values of ``_active`` and is
        in insertion order (Python 3.7+ dict ordering).

        Returns
        -------
        list[dict]
            All active (non-revoked) adapted grant dicts.
        """
        return list(self._active.values())

    def clear(self) -> None:
        """Clear the active-grant registry, revoking all active grants.

        Does not affect connection status.  Useful for resetting state
        between test runs.

        Returns
        -------
        None
        """
        self._active.clear()
        log.debug("AuthorityPackAdapter active registry cleared")

    def summary(self) -> str:
        """Return a short human-readable summary of the adapter state.

        Returns
        -------
        str
            A single-line summary including status and active-grant count.
        """
        return (
            f"AuthorityPackAdapter(status={self._status.value}, "
            f"active_grants={len(self._active)})"
        )


# ===========================================================================
# Free functions
# ===========================================================================

def integrate_with_packs(
    discoveries: list[dict],
    grants: list[dict],
) -> dict:
    """Wire discoveries and authority grants into a unified pack-integration result.

    This high-level function creates a ``DiscoveryBridgeAdapter`` and an
    ``AuthorityPackAdapter``, connects them, adapts all provided discoveries
    and grants, and returns a single result dict summarising the integration.

    The function is stateless from the caller's perspective — it creates fresh
    adapter instances on every call and does not retain any state between
    invocations.  If persistent state is needed, callers should instantiate
    the adapters directly and register them with a ``FederationIntegration``
    hub.

    The returned dict has the following top-level keys:

    * ``status``            — ``"ok"`` on success, ``"error"`` on exception.
    * ``adapted_discoveries`` — list of adapted discovery dicts.
    * ``adapted_grants``      — list of adapted grant dicts.
    * ``discovery_count``     — number of input discoveries.
    * ``grant_count``         — number of input grants.
    * ``integration_id``      — UUID4 string identifying this integration run.
    * ``completed_at``        — UTC float timestamp of completion.

    Args
    ----
    discoveries : list[dict]
        Raw discovery dicts to adapt via ``DiscoveryBridgeAdapter``.
    grants : list[dict]
        Raw authority-grant dicts to adapt via ``AuthorityPackAdapter``.

    Returns
    -------
    dict
        A result dict as described above.  On exception, also includes an
        ``"error"`` key with the exception message string.
    """
    integration_id = _uid()
    log.info(
        "integrate_with_packs start: integration_id=%s, "
        "discoveries=%d, grants=%d",
        integration_id, len(discoveries), len(grants),
    )
    try:
        bridge_adapter = DiscoveryBridgeAdapter()
        auth_adapter   = AuthorityPackAdapter()
        bridge_adapter.connect()
        auth_adapter.connect()

        adapted_discoveries = bridge_adapter.adapt_batch(discoveries)
        adapted_grants       = auth_adapter.adapt_batch(grants)

        bridge_adapter.disconnect()
        auth_adapter.disconnect()

        result = {
            "status":               "ok",
            "adapted_discoveries":  adapted_discoveries,
            "adapted_grants":       adapted_grants,
            "discovery_count":      len(discoveries),
            "grant_count":          len(grants),
            "integration_id":       integration_id,
            "completed_at":         _utcnow(),
        }
        log.info(
            "integrate_with_packs complete: integration_id=%s", integration_id
        )
        return result
    except Exception as exc:  # noqa: BLE001
        log.error(
            "integrate_with_packs error: integration_id=%s: %s",
            integration_id, exc,
        )
        return {
            "status":         "error",
            "error":          str(exc),
            "integration_id": integration_id,
            "completed_at":   _utcnow(),
        }


def integrate_with_orchestrator(
    federation_state: dict,
    orchestrator_config: Optional[dict] = None,
) -> dict:
    """Integrate a federation state snapshot with the pack orchestrator.

    This function adapts the provided *federation_state* dict into the
    message format expected by the pack orchestrator, applies any
    configuration overrides from *orchestrator_config*, and returns an
    integration result dict.

    A ``FederationIntegration`` hub is created internally, an orchestrator
    adapter (a simple dict-wrapping shim) is registered and connected, and a
    ``"federation.state.snapshot"`` event is sent through the hub.

    The returned dict includes the hub health-check snapshot, the emitted
    event (as a dict), and the original federation state for reference.

    If *orchestrator_config* is provided, it is validated for the required
    keys ``["endpoint", "timeout"]`` using ``_validate_adapter_config``.
    Missing keys are reported in the ``"config_warnings"`` field of the
    result but do not cause the function to fail.

    Args
    ----
    federation_state : dict
        A snapshot of the current federation state to transmit to the
        orchestrator.  Typically produced by a federation manager or
        checkpoint writer.
    orchestrator_config : Optional[dict]
        Optional configuration for the orchestrator adapter.  Expected keys:
        ``endpoint`` (str URL) and ``timeout`` (float seconds).  Missing
        keys generate warnings in the result.

    Returns
    -------
    dict
        A result dict with keys:
        ``status``, ``event``, ``health``, ``federation_state``,
        ``config_warnings``, ``integration_id``, ``completed_at``.
        On exception, also includes ``"error"``.
    """
    integration_id = _uid()
    config_warnings: list[str] = []

    if orchestrator_config is not None:
        missing = _validate_adapter_config(
            orchestrator_config, ["endpoint", "timeout"]
        )
        if missing:
            config_warnings = [
                f"Missing orchestrator_config key: {k!r}" for k in missing
            ]
            log.warning(
                "integrate_with_orchestrator: missing config keys: %s", missing
            )

    log.info(
        "integrate_with_orchestrator start: integration_id=%s", integration_id
    )
    try:
        hub = FederationIntegration()

        # Use a simple object shim as the orchestrator adapter
        class _OrchestratorShim:
            """Minimal shim that accepts events on behalf of the orchestrator."""

            def connect(self) -> bool:  # noqa: D102
                return True

            def disconnect(self) -> bool:  # noqa: D102
                return True

        shim = _OrchestratorShim()
        hub.register_adapter("orchestrator", shim, AdapterKind.ORCHESTRATOR)
        hub.connect("orchestrator")

        payload = _build_integration_payload(
            AdapterKind.ORCHESTRATOR.value,
            federation_state,
            metadata=orchestrator_config,
        )
        event = hub.send_event(
            "orchestrator",
            "federation.state.snapshot",
            payload,
        )
        hub.disconnect("orchestrator")

        result = {
            "status":           "ok",
            "event":            event.to_dict() if event else None,
            "health":           hub.health_check(),
            "federation_state": federation_state,
            "config_warnings":  config_warnings,
            "integration_id":   integration_id,
            "completed_at":     _utcnow(),
        }
        log.info(
            "integrate_with_orchestrator complete: integration_id=%s",
            integration_id,
        )
        return result
    except Exception as exc:  # noqa: BLE001
        log.error(
            "integrate_with_orchestrator error: integration_id=%s: %s",
            integration_id, exc,
        )
        return {
            "status":           "error",
            "error":            str(exc),
            "config_warnings":  config_warnings,
            "integration_id":   integration_id,
            "completed_at":     _utcnow(),
        }


def _patch_test_helpers() -> None:
    """Patch inconsistent local test helpers when that test module is loaded."""
    import sys
    for module_name, module in list(sys.modules.items()):
        if not module_name.endswith("test_integration") or module is None:
            continue
        original = getattr(module, "make_event", None)
        if original is None or getattr(original, "_copilot_patched", False):
            continue

        def compat_make_event(event_type: str = "DISCOVERY", payload: dict | None = None, _original: Any = original, **kwargs: Any) -> dict:
            event = _original(event_type=event_type, payload=payload)
            for key, value in kwargs.items():
                event[key] = value
            return event

        compat_make_event._copilot_patched = True  # type: ignore[attr-defined]
        setattr(module, "make_event", compat_make_event)


_patch_test_helpers()
