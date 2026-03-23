"""Unstable object surface theory for JuGeo unstable protocols (Ch22 §3).

The unstable surface is the boundary where a protocol section's support is
actively retracting.

Theory alignment (Ch22, theory2.tex)
-------------------------------------
* §3  Unstable surfaces – a protocol section's *surface* is the set of methods
      that remain accessible but whose support is shrinking.  When a method
      leaves the surface, a *retraction event* is emitted.
* §3  Retraction events – each event is an immutable record capturing the
      method name, the interface it belonged to, the timestamp, and a
      human-readable reason.  Events cannot be deleted from the log; they can
      only be archived.
* §3  Obstruction cohomology – when retraction is *blocked* (e.g. a downstream
      consumer depends on the method), an obstruction is recorded as a
      first-class cohomology class.  Obstructions must be explicitly resolved;
      they cannot be silently erased.  This mirrors the sheaf-cohomology
      obstruction to extending a local section to a global one.
* §3  Surface stabilisation – in an emergency, a surface may be *frozen*:
      all retraction events are suppressed until the freeze is lifted or its
      timeout expires.  Freezing is a temporary measure; it does not resolve
      obstructions.

The classes in this module implement the four sub-theories above.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field, replace as dc_replace
from typing import Any

# ---------------------------------------------------------------------------
# Local model imports
# ---------------------------------------------------------------------------
try:
    from jugeo.python_runtime.unstable_protocols.models import (
        ProtocolSection,
        StabilityLevel,
        ProxyRecord,
        ProxyRestriction,
        DelegationChain,
        DelegationKind,
        UnstableInterface,
    )
except ImportError:  # pragma: no cover
    class ProtocolSection:  # type: ignore[no-redef]
        pass
    class StabilityLevel:  # type: ignore[no-redef]
        pass
    class ProxyRecord:  # type: ignore[no-redef]
        pass
    class ProxyRestriction:  # type: ignore[no-redef]
        pass
    class DelegationChain:  # type: ignore[no-redef]
        pass
    class DelegationKind:  # type: ignore[no-redef]
        pass
    class UnstableInterface:  # type: ignore[no-redef]
        pass

# ---------------------------------------------------------------------------
# Cross-package stubs
# ---------------------------------------------------------------------------
try:
    from jugeo.geometry.supports import SupportRegion, SupportSet, SupportTracker
except ImportError:  # pragma: no cover
    class SupportRegion:  # type: ignore[no-redef]
        pass
    class SupportSet:  # type: ignore[no-redef]
        pass
    class SupportTracker:  # type: ignore[no-redef]
        pass

try:
    from jugeo.judgments.judgment_terms import LocalJudgment, JudgmentStatus, TrustTier
except ImportError:  # pragma: no cover
    class LocalJudgment:  # type: ignore[no-redef]
        pass
    class JudgmentStatus:  # type: ignore[no-redef]
        pass
    class TrustTier:  # type: ignore[no-redef]
        pass

try:
    from jugeo.evidence.channels import EvidenceChannel, EvidenceRecord, ChannelRouter
except ImportError:  # pragma: no cover
    class EvidenceChannel:  # type: ignore[no-redef]
        pass
    class EvidenceRecord:  # type: ignore[no-redef]
        pass
    class ChannelRouter:  # type: ignore[no-redef]
        pass

try:
    from jugeo.orchestration.fleet import Fleet, FleetBid, FleetMember
except ImportError:  # pragma: no cover
    class Fleet:  # type: ignore[no-redef]
        pass
    class FleetBid:  # type: ignore[no-redef]
        pass
    class FleetMember:  # type: ignore[no-redef]
        pass


# ---------------------------------------------------------------------------
# SurfaceTracker
# ---------------------------------------------------------------------------


@dataclass
class SurfaceTracker:
    """Tracks the unstable object surface of protocol sections.

    The tracker maintains the current set of surface methods for each
    registered :class:`UnstableInterface`, detects retraction events when
    methods leave the surface, and provides statistics at both the individual
    interface and aggregate levels.

    Snapshots are taken automatically when :meth:`snapshot` is called and
    whenever more than :attr:`snapshot_interval` seconds have elapsed since
    the last snapshot.

    Parameters
    ----------
    surfaces:
        Mapping from interface_id to :class:`UnstableInterface`.
    retraction_history:
        Ordered list of retraction event records.
    snapshot_interval:
        Minimum seconds between automatic snapshots.
    last_snapshot:
        Unix timestamp of the most recent snapshot.
    """

    surfaces: dict[str, UnstableInterface] = field(default_factory=dict)
    retraction_history: list[dict[str, Any]] = field(default_factory=list)
    snapshot_interval: float = 60.0
    last_snapshot: float = field(default_factory=time.time)

    def register_interface(self, interface: UnstableInterface) -> None:
        """Register a new :class:`UnstableInterface` for tracking.

        Parameters
        ----------
        interface:
            The interface to track.
        """
        self.surfaces[interface.interface_id] = interface

    def update_surface(
        self,
        interface_id: str,
        new_surface_methods: tuple[str, ...],
    ) -> list[str]:
        """Update the surface methods for an interface and emit retraction events.

        Retraction events are emitted for every method that was in the previous
        surface but is absent from ``new_surface_methods``.

        Parameters
        ----------
        interface_id:
            The interface to update.
        new_surface_methods:
            The new set of surface methods.

        Returns
        -------
        list[str]
            List of method names that were retracted (left the surface).
        """
        interface = self.surfaces.get(interface_id)
        if interface is None:
            return []

        old_methods = set(interface.surface_methods)
        new_methods = set(new_surface_methods)
        retracted = list(old_methods - new_methods)

        # Compute new retraction rate (methods retracted / elapsed second)
        now = time.time()
        elapsed = max(now - interface.last_retraction, 1e-3)
        new_rate = len(retracted) / elapsed if retracted else max(0.0, interface.retraction_rate * 0.9)

        updated_interface = dc_replace(
            interface,
            surface_methods=new_surface_methods,
            retraction_rate=new_rate,
            last_retraction=now if retracted else interface.last_retraction,
        )
        self.surfaces[interface_id] = updated_interface

        for method in retracted:
            self.retraction_history.append(
                {
                    "interface_id": interface_id,
                    "method": method,
                    "timestamp": now,
                    "reason": "surface update",
                    "previous_rate": interface.retraction_rate,
                    "new_rate": new_rate,
                }
            )

        # Auto-snapshot if interval elapsed
        if now - self.last_snapshot >= self.snapshot_interval:
            self.snapshot()

        return retracted

    def snapshot(self) -> dict[str, Any]:
        """Capture the current surface state of all interfaces.

        Returns
        -------
        dict[str, Any]
            Contains ``timestamp``, ``interface_count``, ``total_surface_size``,
            and ``surfaces`` (mapping of interface_id → surface data).
        """
        now = time.time()
        self.last_snapshot = now
        return {
            "timestamp": now,
            "interface_count": len(self.surfaces),
            "total_surface_size": self.total_surface_size(),
            "surfaces": {
                iid: {
                    "surface_size": iface.surface_size(),
                    "retraction_rate": iface.retraction_rate,
                    "stability": iface.effective_stability().value,
                }
                for iid, iface in self.surfaces.items()
            },
        }

    def surface_size(self, interface_id: str) -> int:
        """Return the current surface size for a specific interface.

        Parameters
        ----------
        interface_id:
            The interface to query.
        """
        interface = self.surfaces.get(interface_id)
        return interface.surface_size() if interface else 0

    def total_surface_size(self) -> int:
        """Return the sum of surface sizes across all tracked interfaces."""
        return sum(iface.surface_size() for iface in self.surfaces.values())

    def retracting_interfaces(self) -> list[UnstableInterface]:
        """Return all interfaces whose retraction rate is positive."""
        return [iface for iface in self.surfaces.values() if iface.is_retracting()]

    def retraction_rate(self, interface_id: str) -> float:
        """Return the current retraction rate for an interface.

        Parameters
        ----------
        interface_id:
            The interface to query.
        """
        interface = self.surfaces.get(interface_id)
        return interface.retraction_rate if interface else 0.0

    def surface_history(self, interface_id: str) -> list[dict[str, Any]]:
        """Return all retraction history entries for a specific interface.

        Parameters
        ----------
        interface_id:
            The interface to filter by.
        """
        return [
            e for e in self.retraction_history if e["interface_id"] == interface_id
        ]

    def export_state(self) -> dict[str, Any]:
        """Serialise tracker state to a plain dictionary."""
        return {
            "surfaces": {iid: iface.to_dict() for iid, iface in self.surfaces.items()},
            "retraction_history_count": len(self.retraction_history),
            "snapshot_interval": self.snapshot_interval,
            "last_snapshot": self.last_snapshot,
        }

    def clear_history(self) -> None:
        """Clear the retraction history list."""
        self.retraction_history.clear()


# ---------------------------------------------------------------------------
# RetractionEventLog
# ---------------------------------------------------------------------------


@dataclass
class RetractionEventLog:
    """Immutable append-only log of retraction events.

    A retraction event is emitted whenever a method leaves the unstable
    surface.  Events are append-only: they may never be deleted, only
    archived via :meth:`clear` (which is a destructive operation and should
    only be used for testing).

    Parameters
    ----------
    events:
        Ordered list of event dictionaries.
    max_events:
        Maximum number of events to retain before oldest are dropped.
    """

    events: list[dict[str, Any]] = field(default_factory=list)
    max_events: int = 50_000

    def record(
        self,
        interface_id: str,
        method: str,
        timestamp: float,
        reason: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        """Append a retraction event record.

        Parameters
        ----------
        interface_id:
            The interface from which the method was retracted.
        method:
            The retracted method name.
        timestamp:
            Unix timestamp of the event.
        reason:
            Human-readable reason for the retraction.
        metadata:
            Arbitrary extra metadata.

        Returns
        -------
        dict[str, Any]
            The event record as stored.
        """
        event = {
            "event_id": str(uuid.uuid4()),
            "interface_id": interface_id,
            "method": method,
            "timestamp": timestamp,
            "reason": reason,
            "metadata": dict(metadata),
        }
        self.events.append(event)
        if len(self.events) > self.max_events:
            self.events = self.events[-self.max_events :]
        return event

    def events_for(self, interface_id: str) -> list[dict[str, Any]]:
        """Return all events for a specific interface.

        Parameters
        ----------
        interface_id:
            The interface to filter by.
        """
        return [e for e in self.events if e["interface_id"] == interface_id]

    def events_since(self, timestamp: float) -> list[dict[str, Any]]:
        """Return all events recorded at or after ``timestamp``.

        Parameters
        ----------
        timestamp:
            Unix timestamp lower bound (inclusive).
        """
        return [e for e in self.events if e["timestamp"] >= timestamp]

    def method_retraction_count(self, method: str) -> int:
        """Return the total number of times a method has been retracted across all interfaces.

        Parameters
        ----------
        method:
            The method name to count.
        """
        return sum(1 for e in self.events if e["method"] == method)

    def most_retracted_methods(self, top_n: int = 10) -> list[tuple[str, int]]:
        """Return the top N most frequently retracted methods.

        Parameters
        ----------
        top_n:
            Number of results to return.

        Returns
        -------
        list[tuple[str, int]]
            Ordered list of ``(method_name, count)`` descending by count.
        """
        counts: dict[str, int] = defaultdict(int)
        for e in self.events:
            counts[e["method"]] += 1
        return sorted(counts.items(), key=lambda x: x[1], reverse=True)[:top_n]

    def retraction_rate_per_interface(self) -> dict[str, float]:
        """Compute events-per-hour for each interface seen in the log.

        Returns
        -------
        dict[str, float]
            Mapping from interface_id to events-per-hour rate.
        """
        if not self.events:
            return {}

        by_interface: dict[str, list[float]] = defaultdict(list)
        for e in self.events:
            by_interface[e["interface_id"]].append(e["timestamp"])

        rates: dict[str, float] = {}
        for iid, timestamps in by_interface.items():
            if len(timestamps) < 2:
                rates[iid] = 0.0
                continue
            span_hours = (max(timestamps) - min(timestamps)) / 3600.0
            if span_hours < 1e-6:
                rates[iid] = 0.0
            else:
                rates[iid] = len(timestamps) / span_hours
        return rates

    def export(self) -> list[dict[str, Any]]:
        """Return a copy of all events."""
        return list(self.events)

    def clear(self) -> None:
        """Clear all events from the log (destructive – use for testing only)."""
        self.events.clear()

    def count(self) -> int:
        """Return the total number of recorded events."""
        return len(self.events)


# ---------------------------------------------------------------------------
# ObstructionInjector
# ---------------------------------------------------------------------------


@dataclass
class ObstructionInjector:
    """Injects and manages obstructions in the retraction process.

    An obstruction is a first-class record of a blocked retraction: a method
    that *should* leave the surface but cannot because of a downstream
    dependency or a policy constraint.  Obstructions must be explicitly
    resolved; they cannot be silently erased.

    In sheaf-cohomological terms, each active obstruction is a generator of
    H¹ of the nerve of the cover restricted to the protocol section's surface.

    Parameters
    ----------
    obstructions:
        Mapping from obstruction_id to obstruction record dict.
    injection_log:
        Ordered list of injection event records.
    """

    obstructions: dict[str, dict[str, Any]] = field(default_factory=dict)
    injection_log: list[dict[str, Any]] = field(default_factory=list)

    def inject(
        self,
        interface_id: str,
        method: str,
        reason: str,
        metadata: dict[str, Any],
    ) -> str:
        """Record a new obstruction blocking the retraction of ``method``.

        Parameters
        ----------
        interface_id:
            The interface on whose surface the method is blocked.
        method:
            The method name whose retraction is blocked.
        reason:
            Human-readable explanation of the blockage.
        metadata:
            Arbitrary extra context.

        Returns
        -------
        str
            The newly assigned obstruction_id.
        """
        obstruction_id = str(uuid.uuid4())
        now = time.time()
        obstruction = {
            "obstruction_id": obstruction_id,
            "interface_id": interface_id,
            "method": method,
            "reason": reason,
            "metadata": dict(metadata),
            "injected_at": now,
            "resolved": False,
            "resolution": None,
            "resolved_at": None,
        }
        self.obstructions[obstruction_id] = obstruction
        self.injection_log.append(
            {
                "event": "inject",
                "obstruction_id": obstruction_id,
                "interface_id": interface_id,
                "method": method,
                "timestamp": now,
            }
        )
        return obstruction_id

    def resolve(self, obstruction_id: str, resolution: str) -> bool:
        """Mark an obstruction as resolved.

        Parameters
        ----------
        obstruction_id:
            The obstruction to resolve.
        resolution:
            Human-readable description of how the obstruction was resolved.

        Returns
        -------
        bool
            ``True`` if found and resolved; ``False`` if already resolved or not found.
        """
        obs = self.obstructions.get(obstruction_id)
        if obs is None or obs["resolved"]:
            return False
        obs["resolved"] = True
        obs["resolution"] = resolution
        obs["resolved_at"] = time.time()
        self.injection_log.append(
            {
                "event": "resolve",
                "obstruction_id": obstruction_id,
                "resolution": resolution,
                "timestamp": obs["resolved_at"],
            }
        )
        return True

    def list_active(self, interface_id: str | None = None) -> list[dict[str, Any]]:
        """Return all unresolved obstructions, optionally filtered by interface.

        Parameters
        ----------
        interface_id:
            When provided, only return obstructions for this interface.
        """
        result = [o for o in self.obstructions.values() if not o["resolved"]]
        if interface_id is not None:
            result = [o for o in result if o["interface_id"] == interface_id]
        return result

    def list_resolved(self) -> list[dict[str, Any]]:
        """Return all resolved obstructions."""
        return [o for o in self.obstructions.values() if o["resolved"]]

    def obstruction_count(self, interface_id: str | None = None) -> int:
        """Return the count of active obstructions, optionally for one interface.

        Parameters
        ----------
        interface_id:
            When provided, count only obstructions for this interface.
        """
        return len(self.list_active(interface_id))

    def is_blocked(self, interface_id: str, method: str) -> bool:
        """Return True when an active obstruction exists for the given method.

        Parameters
        ----------
        interface_id:
            The interface to check.
        method:
            The method name to check.
        """
        return any(
            o["interface_id"] == interface_id and o["method"] == method
            for o in self.list_active(interface_id)
        )

    def cohomology_class(self, interface_id: str) -> dict[str, Any]:
        """Compute a representation of the obstruction cohomology for an interface.

        The cohomology class is characterised by the set of blocked methods,
        the total number of active obstructions, and the earliest injection
        timestamp (representing how long the class has been non-trivial).

        Parameters
        ----------
        interface_id:
            The interface to compute the class for.

        Returns
        -------
        dict[str, Any]
            Keys: ``interface_id``, ``active_count``, ``blocked_methods``,
            ``earliest_injection``, ``is_trivial``.
        """
        active = self.list_active(interface_id)
        blocked_methods = sorted(set(o["method"] for o in active))
        earliest = min((o["injected_at"] for o in active), default=None)
        return {
            "interface_id": interface_id,
            "active_count": len(active),
            "blocked_methods": blocked_methods,
            "earliest_injection": earliest,
            "is_trivial": len(active) == 0,
        }

    def export_obstructions(self) -> dict[str, Any]:
        """Serialise all obstruction records to a plain dictionary."""
        return {
            "obstructions": dict(self.obstructions),
            "injection_log_count": len(self.injection_log),
        }

    def clear_resolved(self) -> int:
        """Remove all resolved obstructions from the registry.

        Returns
        -------
        int
            Number of obstructions removed.
        """
        resolved_ids = [
            oid for oid, o in self.obstructions.items() if o["resolved"]
        ]
        for oid in resolved_ids:
            del self.obstructions[oid]
        return len(resolved_ids)


# ---------------------------------------------------------------------------
# SurfaceStabilizer
# ---------------------------------------------------------------------------


@dataclass
class SurfaceStabilizer:
    """Emergency mechanism to freeze an unstable surface.

    Freezing prevents further retraction events from being emitted for the
    frozen interface.  It is a temporary measure: if :attr:`stabilization_timeout`
    seconds elapse after the freeze, the freeze expires and must be renewed.

    Frozen state is tracked in :attr:`frozen_interfaces`, a set of interface IDs.
    :attr:`freeze_log` records every freeze/unfreeze event for audit.

    Parameters
    ----------
    frozen_interfaces:
        Set of interface IDs currently frozen.
    freeze_log:
        Ordered list of freeze/unfreeze event records.
    stabilization_timeout:
        Seconds after which a freeze automatically expires.
    """

    frozen_interfaces: set[str] = field(default_factory=set)
    freeze_log: list[dict[str, Any]] = field(default_factory=list)
    stabilization_timeout: float = 3600.0
    # Internal: maps interface_id -> freeze_timestamp
    _freeze_times: dict[str, float] = field(default_factory=dict, repr=False)

    def freeze(self, interface_id: str, reason: str) -> bool:
        """Freeze the surface of an interface.

        Parameters
        ----------
        interface_id:
            The interface to freeze.
        reason:
            Human-readable reason for the freeze.

        Returns
        -------
        bool
            ``True`` if successfully frozen; ``False`` if already frozen.
        """
        if interface_id in self.frozen_interfaces:
            return False
        now = time.time()
        self.frozen_interfaces.add(interface_id)
        self._freeze_times[interface_id] = now
        self.freeze_log.append(
            {
                "event": "freeze",
                "interface_id": interface_id,
                "reason": reason,
                "timestamp": now,
                "expires_at": now + self.stabilization_timeout,
            }
        )
        return True

    def unfreeze(self, interface_id: str) -> bool:
        """Unfreeze the surface of an interface.

        Parameters
        ----------
        interface_id:
            The interface to unfreeze.

        Returns
        -------
        bool
            ``True`` if found and unfrozen; ``False`` if not currently frozen.
        """
        if interface_id not in self.frozen_interfaces:
            return False
        self.frozen_interfaces.discard(interface_id)
        self._freeze_times.pop(interface_id, None)
        self.freeze_log.append(
            {
                "event": "unfreeze",
                "interface_id": interface_id,
                "timestamp": time.time(),
            }
        )
        return True

    def is_frozen(self, interface_id: str) -> bool:
        """Return True when the interface is currently frozen.

        Automatically unfreezes expired interfaces before checking.

        Parameters
        ----------
        interface_id:
            The interface to check.
        """
        # check for timeout
        freeze_time = self._freeze_times.get(interface_id)
        if freeze_time is not None:
            if time.time() - freeze_time >= self.stabilization_timeout:
                self.unfreeze(interface_id)
                return False
        return interface_id in self.frozen_interfaces

    def force_stabilize(self, interface: UnstableInterface) -> UnstableInterface:
        """Return a copy of ``interface`` with the retraction rate set to zero.

        This is an emergency operation that produces a new frozen-rate interface.
        It does *not* register the interface as frozen in :attr:`frozen_interfaces`;
        call :meth:`freeze` separately to prevent further retraction events.

        Parameters
        ----------
        interface:
            The interface to stabilise.

        Returns
        -------
        UnstableInterface
            New interface instance with ``retraction_rate=0.0``.
        """
        return dc_replace(interface, retraction_rate=0.0)

    def freeze_all(self, interface_ids: list[str], reason: str) -> int:
        """Freeze multiple interfaces at once.

        Parameters
        ----------
        interface_ids:
            List of interfaces to freeze.
        reason:
            Shared human-readable reason.

        Returns
        -------
        int
            Number of interfaces newly frozen (excludes already-frozen ones).
        """
        count = 0
        for iid in interface_ids:
            if self.freeze(iid, reason):
                count += 1
        return count

    def check_timeout(self) -> list[str]:
        """Return interface IDs whose freeze has expired.

        Expired interfaces are automatically unfrozen.

        Returns
        -------
        list[str]
            Interface IDs that were unfrozen due to timeout.
        """
        now = time.time()
        expired: list[str] = []
        for iid, freeze_time in list(self._freeze_times.items()):
            if now - freeze_time >= self.stabilization_timeout:
                self.unfreeze(iid)
                expired.append(iid)
        return expired

    def stabilization_report(self) -> dict[str, Any]:
        """Return a structured report of the current stabilisation state.

        Returns
        -------
        dict[str, Any]
            Keys: ``frozen_count``, ``frozen_interfaces``, ``timeout``,
            ``freeze_log_count``.
        """
        return {
            "frozen_count": len(self.frozen_interfaces),
            "frozen_interfaces": sorted(self.frozen_interfaces),
            "timeout": self.stabilization_timeout,
            "freeze_log_count": len(self.freeze_log),
        }

    def export_log(self) -> list[dict[str, Any]]:
        """Return a copy of the freeze/unfreeze event log."""
        return list(self.freeze_log)


# ---------------------------------------------------------------------------

__all__ = [
    "SurfaceTracker",
    "RetractionEventLog",
    "ObstructionInjector",
    "SurfaceStabilizer",
]

# copilot: unstable_surfaces.py – surface tracker, retraction log, obstruction injector, and surface stabilizer (Ch22 §3)
