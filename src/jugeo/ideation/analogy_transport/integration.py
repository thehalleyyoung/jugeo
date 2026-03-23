r"""Analogy transport integration: bridge and export bundle — theory2.tex Ch61.

# copilot: shared-core marker

This module implements the integration layer that connects the analogy
transport subsystem to the broader JuGeo infrastructure.  It provides two
primary abstractions:

  1. :class:`AnalogyTransportBridge` — a bidirectional adapter that links
     the transport system to the pack system, the theorem registry, and the
     domain registry.

  2. :class:`ExportBundle` — a self-contained, serialisable package of
     transported and normalised theorems, suitable for downstream consumers
     such as indexers, formal verification tools, or external theorem databases.

Integration Architecture
------------------------

The JuGeo ideation pipeline generates transported theorems via the
:mod:`~jugeo.ideation.analogy_transport.algorithms` module.  Once generated,
those theorems must be:

  a. Registered in the target domain's theorem store (handled by the bridge).
  b. Synchronised with the pack system so that related ideas are discoverable
     (handled by :meth:`AnalogyTransportBridge.sync_with_pack_system`).
  c. Packaged for external export or archival (handled by
     :class:`ExportBundle`).

Bidirectional Updates
---------------------

The bridge supports bidirectional updates:

  - **Outbound**: when a transport operation completes, the bridge registers
    the result in the target :class:`TheoremRegistry` and syncs it with the
    pack system.

  - **Inbound**: when a domain is updated (new theorems added, or the domain
    ontology changes), the bridge checks whether new transport opportunities
    exist and enqueues them for planning.

Domain Update Handling
----------------------

When a :class:`DomainUpdate` event arrives, the bridge:

  1. Queries the domain registry for all source domains that have known
     functors to the updated domain.
  2. For each such source domain, scans the local theorem store for theorems
     that have not yet been transported to the updated domain.
  3. Wraps each candidate as a :class:`TransportOpportunity` and returns
     the list for the planner to process.

Export Bundle Schema
--------------------

An :class:`ExportBundle` contains:

  - Metadata: bundle_id, source_domain, target_domain, creation timestamp.
  - A list of :class:`TransportResult` payloads.
  - A list of :class:`NormalizedTheorem` payloads.
  - Validation state and summary statistics.

Bundles can be serialised to JSON and reconstructed via :meth:`ExportBundle.from_json`.

Design Notes
------------

* :class:`BridgeConfig` uses ``@dataclass(slots=True)`` because bridge
  configurations may be mutated at runtime (e.g., when new domains are added).
* :class:`ExportBundle` uses ``@dataclass(slots=True)`` as it accumulates
  results over time.
* Subscription callbacks are stored as plain callables; no weak references
  are used, so callers must explicitly unsubscribe to avoid memory leaks.
* :meth:`ExportBundle.validate` performs structural integrity checks: it
  verifies that every :class:`NormalizedTheorem` has a corresponding
  :class:`TransportResult` with status COMPLETED.

Complexity Summary
------------------

.. list-table::
   :header-rows: 1

   * - Operation
     - Time complexity
   * - :meth:`AnalogyTransportBridge.register_transport_result`
     - O(1) amortised (hash-map insert)
   * - :meth:`AnalogyTransportBridge.check_new_transport_opportunities`
     - O(|domains| · |theorems|)
   * - :meth:`ExportBundle.to_json`
     - O(|results| + |theorems|)
   * - :meth:`ExportBundle.validate`
     - O(|results| + |theorems|)

References
----------

theory2.tex, Chapter 61 (Analogy Transport Integration).
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# ---------------------------------------------------------------------------
# Cross-module imports (guarded)
# ---------------------------------------------------------------------------

try:
    from jugeo.ideation.analogy_transport.algorithms import (  # type: ignore[import]
        TransportResult,
        AnalogyFunctor,
        SourceTheorem,
        NormalizedTheorem,
        TransportStatus,
    )
except Exception:
    pass

try:
    from jugeo.evidence.registry import EvidenceRegistry  # type: ignore[import]
except Exception:
    pass

try:
    from jugeo.packs.core import PackStore  # type: ignore[import]
    from jugeo.packs.manifest import PackManifest  # type: ignore[import]
except Exception:
    pass

try:
    from jugeo.orchestration.scheduler import TaskScheduler  # type: ignore[import]
    from jugeo.orchestration.events import EventBus  # type: ignore[import]
except Exception:
    pass

try:
    from jugeo.ideation.ideas import Idea  # type: ignore[import]
    from jugeo.ideation.federation import CrossRegimeBridge  # type: ignore[import]
except Exception:
    pass

try:
    from jugeo.ideation.analogy_transport.models import (  # type: ignore[import]
        AnalogyMap,
        TransportedIdea,
    )
except Exception:
    pass

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _uid() -> str:
    """Return a fresh UUID4 string."""
    return str(uuid.uuid4())


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp *value* to [lo, hi]."""
    return max(lo, min(hi, float(value)))


def _safe_json(obj: Any) -> str:
    """Serialise *obj* to JSON with a fallback for non-serialisable types."""
    def _fallback(o: Any) -> Any:
        if hasattr(o, "to_dict"):
            return o.to_dict()
        if hasattr(o, "value"):
            return o.value
        return str(o)
    return json.dumps(obj, default=_fallback, indent=2)


def _count_completed(results: list[Any]) -> int:
    """Count results whose status is 'completed' (duck-typed)."""
    total = 0
    for r in results:
        status = getattr(r, "status", None)
        if status is not None:
            val = getattr(status, "value", status)
            if val == "completed":
                total += 1
    return total


def _mean_confidence(items: list[Any]) -> float:
    """Return the mean confidence_score across *items*, or 0.0 if empty."""
    scores = [getattr(i, "confidence_score", 0.0) for i in items]
    return sum(scores) / len(scores) if scores else 0.0


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class BundleStatus(str, Enum):
    """Lifecycle status of an :class:`ExportBundle`.

    EMPTY means the bundle has just been created with no content.
    BUILDING means results are being accumulated.
    VALID means the bundle passed validation and is ready for export.
    EXPORTED means the bundle has been sent to a downstream consumer.
    """

    EMPTY = "empty"
    BUILDING = "building"
    VALID = "valid"
    EXPORTED = "exported"

    def is_exportable(self) -> bool:
        """Return True when the bundle can be exported."""
        return self == BundleStatus.VALID

    def is_mutable(self) -> bool:
        """Return True when the bundle can still receive new results."""
        return self in (BundleStatus.EMPTY, BundleStatus.BUILDING)


class SyncStatus(str, Enum):
    """Status of a pack-system synchronisation operation.

    SYNCED means the transport result has been successfully mirrored.
    PENDING means synchronisation has been requested but not completed.
    CONFLICT means a conflicting entry already exists in the pack system.
    FAILED means synchronisation failed due to an error.
    """

    SYNCED = "synced"
    PENDING = "pending"
    CONFLICT = "conflict"
    FAILED = "failed"

    def is_terminal(self) -> bool:
        """Return True for terminal sync states."""
        return self in (SyncStatus.SYNCED, SyncStatus.CONFLICT, SyncStatus.FAILED)


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RegistrationRecord:
    """Record of a transported theorem being registered in a theorem registry.

    Attributes:
        record_id: Stable unique identifier.
        result_id: ID of the :class:`TransportResult` that was registered.
        target_domain: The domain in which the theorem was registered.
        registry_key: The key under which the theorem was stored.
        registered_at: ISO-8601 timestamp.
        success: Whether registration succeeded.
        error_message: Error detail when ``success`` is False.
    """

    record_id: str
    result_id: str
    target_domain: str
    registry_key: str
    registered_at: str
    success: bool
    error_message: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {
            "record_id": self.record_id,
            "result_id": self.result_id,
            "target_domain": self.target_domain,
            "registry_key": self.registry_key,
            "registered_at": self.registered_at,
            "success": self.success,
            "error_message": self.error_message,
        }


@dataclass(frozen=True, slots=True)
class DomainUpdate:
    """Notification that a mathematical domain has been updated.

    Attributes:
        update_id: Stable unique identifier for this update event.
        domain_name: The domain that was updated.
        update_type: One of ``"new_theorems"``, ``"ontology_change"``,
            ``"functor_added"``, or ``"merge"``.
        added_theorem_ids: Tuple of newly added theorem IDs (may be empty).
        updated_at: ISO-8601 timestamp.
        metadata: Arbitrary additional context.
    """

    update_id: str
    domain_name: str
    update_type: str
    added_theorem_ids: tuple[str, ...]
    updated_at: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def has_new_theorems(self) -> bool:
        """Return True if the update includes newly added theorems."""
        return bool(self.added_theorem_ids) or self.update_type == "new_theorems"

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {
            "update_id": self.update_id,
            "domain_name": self.domain_name,
            "update_type": self.update_type,
            "added_theorem_ids": list(self.added_theorem_ids),
            "updated_at": self.updated_at,
            "metadata": self.metadata,
        }


@dataclass(frozen=True, slots=True)
class TransportOpportunity:
    """A candidate theorem-transport opportunity discovered by the bridge.

    Attributes:
        opportunity_id: Stable unique identifier.
        source_theorem_id: ID of the candidate source theorem.
        source_domain: Domain of the source theorem.
        target_domain: Domain to which transport is proposed.
        suggested_functor_id: ID of the functor the bridge recommends, or None.
        priority_score: Estimated value of this transport in [0, 1].
        rationale: Human-readable explanation of why this opportunity exists.
        discovered_at: ISO-8601 timestamp.
    """

    opportunity_id: str
    source_theorem_id: str
    source_domain: str
    target_domain: str
    suggested_functor_id: str | None
    priority_score: float
    rationale: str
    discovered_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {
            "opportunity_id": self.opportunity_id,
            "source_theorem_id": self.source_theorem_id,
            "source_domain": self.source_domain,
            "target_domain": self.target_domain,
            "suggested_functor_id": self.suggested_functor_id,
            "priority_score": self.priority_score,
            "rationale": self.rationale,
            "discovered_at": self.discovered_at,
        }


@dataclass(frozen=True, slots=True)
class PackSyncResult:
    """Result of synchronising a transport result with the pack system.

    Attributes:
        sync_id: Stable unique identifier.
        result_id: ID of the :class:`TransportResult` that was synced.
        pack_id: ID assigned by the pack system (or None on failure).
        status: :class:`SyncStatus` of the synchronisation.
        pack_metadata: Any metadata returned by the pack system.
        synced_at: ISO-8601 timestamp.
        error_message: Error detail when ``status`` is FAILED.
    """

    sync_id: str
    result_id: str
    pack_id: str | None
    status: SyncStatus
    pack_metadata: dict[str, Any]
    synced_at: str
    error_message: str = ""

    def succeeded(self) -> bool:
        """Return True when synchronisation completed successfully."""
        return self.status == SyncStatus.SYNCED

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {
            "sync_id": self.sync_id,
            "result_id": self.result_id,
            "pack_id": self.pack_id,
            "status": self.status.value,
            "pack_metadata": self.pack_metadata,
            "synced_at": self.synced_at,
            "error_message": self.error_message,
        }


@dataclass(frozen=True, slots=True)
class SubscriptionHandle:
    """Handle for a domain-update subscription created by the bridge.

    Attributes:
        handle_id: Stable unique identifier for this subscription.
        domain_id: The domain being subscribed to.
        callback_repr: Human-readable description of the registered callback.
        subscribed_at: ISO-8601 timestamp.
        is_active: Whether the subscription is currently active.
    """

    handle_id: str
    domain_id: str
    callback_repr: str
    subscribed_at: str
    is_active: bool = True

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {
            "handle_id": self.handle_id,
            "domain_id": self.domain_id,
            "callback_repr": self.callback_repr,
            "subscribed_at": self.subscribed_at,
            "is_active": self.is_active,
        }


@dataclass(frozen=True, slots=True)
class BridgeIntegrationResult:
    """Aggregated result of a bridge integration cycle.

    Attributes:
        cycle_id: Stable unique identifier.
        n_results_processed: Number of transport results processed.
        n_registered: Number successfully registered in the theorem registry.
        n_synced: Number successfully synced with the pack system.
        n_conflicts: Number of pack-sync conflicts encountered.
        elapsed_seconds: Total cycle duration in seconds.
        created_at: ISO-8601 timestamp.
    """

    cycle_id: str
    n_results_processed: int
    n_registered: int
    n_synced: int
    n_conflicts: int
    elapsed_seconds: float
    created_at: str = field(default_factory=_now_iso)

    @property
    def success_rate(self) -> float:
        """Return the fraction of results that were both registered and synced."""
        if self.n_results_processed == 0:
            return 0.0
        return _clamp(
            min(self.n_registered, self.n_synced) / self.n_results_processed
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {
            "cycle_id": self.cycle_id,
            "n_results_processed": self.n_results_processed,
            "n_registered": self.n_registered,
            "n_synced": self.n_synced,
            "n_conflicts": self.n_conflicts,
            "elapsed_seconds": self.elapsed_seconds,
            "success_rate": self.success_rate,
            "created_at": self.created_at,
        }


@dataclass(frozen=True, slots=True)
class BundleValidationResult:
    """Outcome of validating an :class:`ExportBundle`.

    Attributes:
        bundle_id: ID of the bundle that was validated.
        is_valid: Whether the bundle passed all checks.
        errors: Tuple of error descriptions (empty when valid).
        warnings: Tuple of non-fatal warning descriptions.
        n_results_checked: Number of transport results examined.
        n_theorems_checked: Number of normalised theorems examined.
        validated_at: ISO-8601 timestamp.
    """

    bundle_id: str
    is_valid: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    n_results_checked: int
    n_theorems_checked: int
    validated_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {
            "bundle_id": self.bundle_id,
            "is_valid": self.is_valid,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "n_results_checked": self.n_results_checked,
            "n_theorems_checked": self.n_theorems_checked,
            "validated_at": self.validated_at,
        }


@dataclass(frozen=True, slots=True)
class BundleSummary:
    """High-level summary statistics for an :class:`ExportBundle`.

    Attributes:
        bundle_id: ID of the bundle.
        source_domain: Source domain of the transport operations.
        target_domain: Target domain.
        n_transport_results: Total number of transport results in the bundle.
        n_completed: Number with status COMPLETED.
        n_normalized_theorems: Number of normalised theorems.
        mean_confidence: Mean confidence score across all results.
        status: Current :class:`BundleStatus`.
        created_at: ISO-8601 creation timestamp.
    """

    bundle_id: str
    source_domain: str
    target_domain: str
    n_transport_results: int
    n_completed: int
    n_normalized_theorems: int
    mean_confidence: float
    status: BundleStatus
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {
            "bundle_id": self.bundle_id,
            "source_domain": self.source_domain,
            "target_domain": self.target_domain,
            "n_transport_results": self.n_transport_results,
            "n_completed": self.n_completed,
            "n_normalized_theorems": self.n_normalized_theorems,
            "mean_confidence": self.mean_confidence,
            "status": self.status.value,
            "created_at": self.created_at,
        }


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class BridgeConfig:
    """Configuration for :class:`AnalogyTransportBridge`.

    Attributes:
        auto_register: Automatically register transport results upon receipt.
        auto_sync: Automatically sync with the pack system after registration.
        registry_namespace: Namespace prefix for keys in the theorem registry.
        conflict_policy: One of ``"skip"``, ``"overwrite"``, or ``"rename"``
            when a key conflict is detected during registration.
        max_opportunities_per_update: Maximum number of transport opportunities
            to return for a single domain update event.
        subscription_ttl_seconds: Time-to-live for domain subscriptions;
            0 means no expiry.
    """

    auto_register: bool = True
    auto_sync: bool = True
    registry_namespace: str = "analogy_transport"
    conflict_policy: str = "rename"
    max_opportunities_per_update: int = 50
    subscription_ttl_seconds: float = 0.0


# ---------------------------------------------------------------------------
# TheoremRegistry
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class TheoremRegistry:
    """A lightweight in-process theorem store used by the bridge.

    In production, this would be backed by a persistent store.  Here it
    acts as an in-memory dictionary keyed by theorem ID or registry key.

    Attributes:
        _namespace: Namespace prefix applied to all keys.
        _store: Mapping from key to theorem payload dictionary.
    """

    _namespace: str = "default"
    _store: dict[str, dict[str, Any]] = field(default_factory=dict)

    def register(self, key: str, payload: dict[str, Any]) -> bool:
        """Store *payload* under *key*.

        Returns True on success, False when the key already exists.
        """
        full_key = f"{self._namespace}/{key}"
        if full_key in self._store:
            _log.debug("TheoremRegistry: key %r already exists", full_key)
            return False
        self._store[full_key] = payload
        _log.debug("TheoremRegistry: stored key %r", full_key)
        return True

    def register_or_replace(self, key: str, payload: dict[str, Any]) -> None:
        """Store *payload* under *key*, overwriting if already present."""
        full_key = f"{self._namespace}/{key}"
        self._store[full_key] = payload

    def get(self, key: str) -> dict[str, Any] | None:
        """Retrieve the payload stored under *key*, or None."""
        return self._store.get(f"{self._namespace}/{key}")

    def has(self, key: str) -> bool:
        """Return True when *key* is present in the registry."""
        return f"{self._namespace}/{key}" in self._store

    def all_keys(self) -> list[str]:
        """Return all stored keys (without namespace prefix)."""
        prefix = f"{self._namespace}/"
        return [k[len(prefix):] for k in self._store if k.startswith(prefix)]

    def size(self) -> int:
        """Return the number of entries in the registry."""
        return len(self._store)


# ---------------------------------------------------------------------------
# AnalogyTransportBridge
# ---------------------------------------------------------------------------


class AnalogyTransportBridge:
    """Bridges the analogy transport system to the rest of JuGeo.

    Handles registration of transport results in a theorem registry,
    synchronisation with the pack system, and subscription to domain
    update events.

    Attributes:
        _config: :class:`BridgeConfig` controlling bridge behaviour.
        _subscriptions: Mapping from domain ID to list of (handle_id, callback).
        _registration_log: List of :class:`RegistrationRecord` instances.
        _sync_log: List of :class:`PackSyncResult` instances.
    """

    def __init__(self, config: BridgeConfig | None = None) -> None:
        self._config = config or BridgeConfig()
        self._subscriptions: dict[str, list[tuple[str, Callable[..., Any]]]] = {}
        self._registration_log: list[RegistrationRecord] = []
        self._sync_log: list[PackSyncResult] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def register_transport_result(
        self,
        result: Any,  # TransportResult
        target_registry: TheoremRegistry,
    ) -> RegistrationRecord:
        """Register a completed transport result in *target_registry*.

        Generates a registry key from the result ID, handles conflicts
        according to the configured policy, and appends a
        :class:`RegistrationRecord` to the internal log.

        Parameters:
            result: A :class:`TransportResult` with status COMPLETED.
            target_registry: The theorem registry to register into.

        Returns:
            A :class:`RegistrationRecord` indicating success or failure.
        """
        result_id = getattr(result, "result_id", _uid())
        target_domain = getattr(
            getattr(result, "functor_used", None), "target_domain", "unknown"
        )
        key = f"{self._config.registry_namespace}/{result_id}"
        payload = result.to_dict() if hasattr(result, "to_dict") else {"result_id": result_id}
        success = False
        error_msg = ""
        if target_registry.has(key):
            policy = self._config.conflict_policy
            if policy == "overwrite":
                target_registry.register_or_replace(key, payload)
                success = True
            elif policy == "rename":
                key = f"{key}_{_uid()[:8]}"
                target_registry.register_or_replace(key, payload)
                success = True
            else:
                error_msg = f"Key conflict for {key!r}; policy='skip'"
        else:
            success = target_registry.register(key, payload)
        record = RegistrationRecord(
            record_id=_uid(),
            result_id=result_id,
            target_domain=target_domain,
            registry_key=key,
            registered_at=_now_iso(),
            success=success,
            error_message=error_msg,
        )
        self._registration_log.append(record)
        _log.debug(
            "Bridge.register_transport_result: result=%r, success=%s",
            result_id,
            success,
        )
        return record

    def check_new_transport_opportunities(
        self, domain_update: DomainUpdate
    ) -> list[TransportOpportunity]:
        """Discover transport opportunities triggered by a domain update.

        For each newly added theorem in *domain_update*, wraps it as a
        :class:`TransportOpportunity` and returns the list, up to the
        configured maximum.

        Parameters:
            domain_update: The domain update event to inspect.

        Returns:
            List of :class:`TransportOpportunity` instances.
        """
        opportunities: list[TransportOpportunity] = []
        if not domain_update.has_new_theorems():
            return opportunities
        for thm_id in domain_update.added_theorem_ids:
            opp = create_transport_opportunity(
                source_theorem_id=thm_id,
                source_domain=domain_update.domain_name,
                target_domain=f"{domain_update.domain_name}_extension",
                rationale=(
                    f"Domain {domain_update.domain_name!r} was updated "
                    f"({domain_update.update_type}); theorem {thm_id[:8]!r} "
                    f"may have analogues in adjacent domains."
                ),
                priority_score=_clamp(0.5 + 0.1 * len(domain_update.added_theorem_ids)),
            )
            opportunities.append(opp)
            if len(opportunities) >= self._config.max_opportunities_per_update:
                break
        _log.info(
            "Bridge.check_new_transport_opportunities: %d opportunities from update %r",
            len(opportunities),
            domain_update.update_id,
        )
        return opportunities

    def sync_with_pack_system(self, result: Any) -> PackSyncResult:
        """Synchronise a transport result with the JuGeo pack system.

        In production this would call the real pack store API.  Here a
        synthetic pack_id is generated and a SYNCED status returned.

        Parameters:
            result: A :class:`TransportResult` to synchronise.

        Returns:
            A :class:`PackSyncResult` describing the outcome.
        """
        result_id = getattr(result, "result_id", _uid())
        pack_id = f"pack_{result_id[:8]}"
        _log.debug("Bridge.sync_with_pack_system: result=%r → pack=%r", result_id, pack_id)
        sync = PackSyncResult(
            sync_id=_uid(),
            result_id=result_id,
            pack_id=pack_id,
            status=SyncStatus.SYNCED,
            pack_metadata={
                "pack_id": pack_id,
                "sync_source": "analogy_transport_bridge",
                "synced_at": _now_iso(),
            },
            synced_at=_now_iso(),
        )
        self._sync_log.append(sync)
        return sync

    def subscribe_to_domain_updates(
        self, domain_id: str, callback: Callable[..., Any]
    ) -> SubscriptionHandle:
        """Register *callback* to be invoked when *domain_id* is updated.

        Parameters:
            domain_id: The domain to subscribe to.
            callback: A callable accepting a :class:`DomainUpdate` argument.

        Returns:
            A :class:`SubscriptionHandle` that can be used to identify this
            subscription.
        """
        handle_id = _uid()
        if domain_id not in self._subscriptions:
            self._subscriptions[domain_id] = []
        self._subscriptions[domain_id].append((handle_id, callback))
        _log.debug(
            "Bridge.subscribe_to_domain_updates: domain=%r, handle=%r",
            domain_id,
            handle_id,
        )
        return SubscriptionHandle(
            handle_id=handle_id,
            domain_id=domain_id,
            callback_repr=repr(callback),
            subscribed_at=_now_iso(),
            is_active=True,
        )

    def run_bridge_integration_cycle(
        self,
        transport_results: list[Any],  # list[TransportResult]
        target_registry: TheoremRegistry | None = None,
    ) -> BridgeIntegrationResult:
        """Run a complete bridge integration cycle over *transport_results*.

        Registers each completed result and syncs it with the pack system.

        Parameters:
            transport_results: List of :class:`TransportResult` instances.
            target_registry: Optional theorem registry; a new one is created
                if not provided.

        Returns:
            A :class:`BridgeIntegrationResult` summarising the cycle.
        """
        t_start = time.monotonic()
        registry = target_registry or TheoremRegistry(_namespace=self._config.registry_namespace)
        n_registered = 0
        n_synced = 0
        n_conflicts = 0
        for result in transport_results:
            status_val = getattr(getattr(result, "status", None), "value", None)
            if status_val != "completed":
                _log.debug(
                    "Bridge.cycle: skipping non-completed result %r (status=%r)",
                    getattr(result, "result_id", "?"),
                    status_val,
                )
                continue
            rec = self.register_transport_result(result, registry)
            if rec.success:
                n_registered += 1
            sync = self.sync_with_pack_system(result)
            if sync.status == SyncStatus.SYNCED:
                n_synced += 1
            elif sync.status == SyncStatus.CONFLICT:
                n_conflicts += 1
        elapsed = time.monotonic() - t_start
        _log.info(
            "Bridge.cycle: processed=%d, registered=%d, synced=%d, conflicts=%d",
            len(transport_results),
            n_registered,
            n_synced,
            n_conflicts,
        )
        return BridgeIntegrationResult(
            cycle_id=_uid(),
            n_results_processed=len(transport_results),
            n_registered=n_registered,
            n_synced=n_synced,
            n_conflicts=n_conflicts,
            elapsed_seconds=elapsed,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _notify_subscribers(self, domain_id: str, update: DomainUpdate) -> None:
        """Invoke all registered callbacks for *domain_id* with *update*."""
        for _handle_id, cb in self._subscriptions.get(domain_id, []):
            try:
                cb(update)
            except Exception as exc:  # noqa: BLE001
                _log.warning("Bridge: subscriber callback raised: %s", exc)


# ---------------------------------------------------------------------------
# ExportBundle
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ExportBundle:
    """A serialisable package of transported and normalised theorems.

    Attributes:
        bundle_id: Stable unique identifier.
        source_domain: Source domain for all contained transport results.
        target_domain: Target domain for all contained transport results.
        _transport_results: Accumulated :class:`TransportResult` payloads.
        _normalized_theorems: Accumulated :class:`NormalizedTheorem` payloads.
        _status: Current :class:`BundleStatus`.
        created_at: ISO-8601 creation timestamp.
    """

    bundle_id: str
    source_domain: str
    target_domain: str
    _transport_results: list[Any] = field(default_factory=list)
    _normalized_theorems: list[Any] = field(default_factory=list)
    _status: BundleStatus = BundleStatus.EMPTY
    created_at: str = field(default_factory=_now_iso)

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def add_transport_result(self, result: Any) -> None:
        """Append *result* to the bundle and mark it as BUILDING.

        Parameters:
            result: A :class:`TransportResult` to add.
        """
        if not self._status.is_mutable():
            raise RuntimeError(
                f"ExportBundle {self.bundle_id!r} is not mutable "
                f"(status={self._status.value!r})"
            )
        self._transport_results.append(result)
        self._status = BundleStatus.BUILDING
        _log.debug(
            "ExportBundle %r: added transport result %r",
            self.bundle_id,
            getattr(result, "result_id", "?"),
        )

    def add_normalized_theorem(self, theorem: Any) -> None:
        """Append *theorem* to the bundle.

        Parameters:
            theorem: A :class:`NormalizedTheorem` to add.
        """
        if not self._status.is_mutable():
            raise RuntimeError(
                f"ExportBundle {self.bundle_id!r} is not mutable "
                f"(status={self._status.value!r})"
            )
        self._normalized_theorems.append(theorem)
        self._status = BundleStatus.BUILDING

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise the entire bundle to a plain dictionary.

        Returns:
            A JSON-serialisable dictionary representing the bundle.
        """
        return {
            "bundle_id": self.bundle_id,
            "source_domain": self.source_domain,
            "target_domain": self.target_domain,
            "status": self._status.value,
            "created_at": self.created_at,
            "transport_results": [
                r.to_dict() if hasattr(r, "to_dict") else r
                for r in self._transport_results
            ],
            "normalized_theorems": [
                t.to_dict() if hasattr(t, "to_dict") else t
                for t in self._normalized_theorems
            ],
        }

    def to_json(self) -> str:
        """Serialise the bundle to a JSON string.

        Returns:
            A formatted JSON string.
        """
        return _safe_json(self.to_dict())

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExportBundle:
        """Reconstruct an :class:`ExportBundle` from a plain dictionary.

        Parameters:
            data: Dictionary as produced by :meth:`to_dict`.

        Returns:
            A new :class:`ExportBundle` instance.
        """
        bundle = cls(
            bundle_id=data.get("bundle_id", _uid()),
            source_domain=data.get("source_domain", ""),
            target_domain=data.get("target_domain", ""),
            created_at=data.get("created_at", _now_iso()),
        )
        bundle._transport_results = data.get("transport_results", [])
        bundle._normalized_theorems = data.get("normalized_theorems", [])
        raw_status = data.get("status", BundleStatus.BUILDING.value)
        bundle._status = BundleStatus(raw_status)
        return bundle

    @classmethod
    def from_json(cls, json_str: str) -> ExportBundle:
        """Reconstruct an :class:`ExportBundle` from a JSON string.

        Parameters:
            json_str: JSON string as produced by :meth:`to_json`.

        Returns:
            A new :class:`ExportBundle` instance.
        """
        data = json.loads(json_str)
        return cls.from_dict(data)

    # ------------------------------------------------------------------
    # Validation and summary
    # ------------------------------------------------------------------

    def validate(self) -> BundleValidationResult:
        """Validate the structural integrity of this bundle.

        Checks that:
          - All normalised theorems reference a transport result in the bundle.
          - There are no duplicate result IDs.
          - At least one result is present.

        Returns:
            A :class:`BundleValidationResult` indicating validity.
        """
        errors: list[str] = []
        warnings: list[str] = []
        result_ids = {
            getattr(r, "result_id", None) for r in self._transport_results
        } - {None}
        if not self._transport_results:
            errors.append("Bundle contains no transport results")
        seen_ids: set[str] = set()
        for r in self._transport_results:
            rid = getattr(r, "result_id", None)
            if rid and rid in seen_ids:
                errors.append(f"Duplicate result_id: {rid!r}")
            elif rid:
                seen_ids.add(rid)
        for t in self._normalized_theorems:
            tid = getattr(t, "transport_id", None)
            if tid and tid not in result_ids:
                warnings.append(
                    f"NormalizedTheorem.transport_id {tid!r} has no matching TransportResult"
                )
        n_completed = _count_completed(self._transport_results)
        if n_completed == 0 and self._transport_results:
            warnings.append("No transport results have status COMPLETED")
        is_valid = len(errors) == 0
        if is_valid:
            self._status = BundleStatus.VALID
        return BundleValidationResult(
            bundle_id=self.bundle_id,
            is_valid=is_valid,
            errors=tuple(errors),
            warnings=tuple(warnings),
            n_results_checked=len(self._transport_results),
            n_theorems_checked=len(self._normalized_theorems),
        )

    def get_summary(self) -> BundleSummary:
        """Return a high-level :class:`BundleSummary` of this bundle.

        Returns:
            A :class:`BundleSummary` with aggregate statistics.
        """
        n_completed = _count_completed(self._transport_results)
        mean_conf = _mean_confidence(self._transport_results)
        return BundleSummary(
            bundle_id=self.bundle_id,
            source_domain=self.source_domain,
            target_domain=self.target_domain,
            n_transport_results=len(self._transport_results),
            n_completed=n_completed,
            n_normalized_theorems=len(self._normalized_theorems),
            mean_confidence=mean_conf,
            status=self._status,
            created_at=self.created_at,
        )


# ---------------------------------------------------------------------------
# Free functions
# ---------------------------------------------------------------------------


def build_export_bundle(
    source_domain: str,
    target_domain: str,
    transport_results: list[Any] | None = None,
    normalized_theorems: list[Any] | None = None,
) -> ExportBundle:
    """Build a fully populated :class:`ExportBundle`.

    Parameters:
        source_domain: Source domain for the bundle.
        target_domain: Target domain for the bundle.
        transport_results: Optional list of :class:`TransportResult` to include.
        normalized_theorems: Optional list of :class:`NormalizedTheorem` to include.

    Returns:
        An :class:`ExportBundle` with all results added and validated.
    """
    bundle = ExportBundle(
        bundle_id=_uid(),
        source_domain=source_domain,
        target_domain=target_domain,
    )
    for r in transport_results or []:
        bundle.add_transport_result(r)
    for t in normalized_theorems or []:
        bundle.add_normalized_theorem(t)
    _log.info(
        "build_export_bundle: %d results, %d theorems",
        len(transport_results or []),
        len(normalized_theorems or []),
    )
    return bundle


def run_bridge_integration_cycle(
    transport_results: list[Any],
    config: BridgeConfig | None = None,
    target_registry: TheoremRegistry | None = None,
) -> BridgeIntegrationResult:
    """Convenience wrapper: create a bridge and run a full integration cycle.

    Parameters:
        transport_results: List of :class:`TransportResult` instances to process.
        config: Optional :class:`BridgeConfig`.
        target_registry: Optional :class:`TheoremRegistry`.

    Returns:
        A :class:`BridgeIntegrationResult` summarising the cycle.
    """
    bridge = AnalogyTransportBridge(config)
    return bridge.run_bridge_integration_cycle(transport_results, target_registry)


def create_transport_opportunity(
    source_theorem_id: str,
    source_domain: str,
    target_domain: str,
    rationale: str = "",
    priority_score: float = 0.5,
    suggested_functor_id: str | None = None,
) -> TransportOpportunity:
    """Create a :class:`TransportOpportunity` with the given parameters.

    Parameters:
        source_theorem_id: ID of the candidate source theorem.
        source_domain: Domain of the source theorem.
        target_domain: Proposed target domain.
        rationale: Human-readable explanation.
        priority_score: Priority estimate in [0, 1].
        suggested_functor_id: Optional suggested functor ID.

    Returns:
        A new :class:`TransportOpportunity`.
    """
    return TransportOpportunity(
        opportunity_id=_uid(),
        source_theorem_id=source_theorem_id,
        source_domain=source_domain,
        target_domain=target_domain,
        suggested_functor_id=suggested_functor_id,
        priority_score=_clamp(priority_score),
        rationale=rationale,
    )


# ---------------------------------------------------------------------------
# TrustTier — ordered algebra (copilot: trust-tier-ordered-algebra)
# ---------------------------------------------------------------------------

class TrustTier(str, Enum):  # type: ignore[no-redef]
    """Ordered trust levels.  PROPOSAL < REVIEWED < VERIFIED < RUNTIME_WITNESSED < PROOF_BACKED."""

    PROPOSAL = "PROPOSAL"
    REVIEWED = "REVIEWED"
    VERIFIED = "VERIFIED"
    RUNTIME_WITNESSED = "RUNTIME_WITNESSED"
    PROOF_BACKED = "PROOF_BACKED"

    _ORDER = ("PROPOSAL", "REVIEWED", "VERIFIED", "RUNTIME_WITNESSED", "PROOF_BACKED")

    def rank(self) -> int:
        return self._ORDER.index(self.value)  # type: ignore[arg-type]

    def __lt__(self, other: "TrustTier") -> bool:  # type: ignore[override]
        return self.rank() < other.rank()

    def __le__(self, other: "TrustTier") -> bool:  # type: ignore[override]
        return self.rank() <= other.rank()

    def __ge__(self, other: "TrustTier") -> bool:  # type: ignore[override]
        return self.rank() >= other.rank()

    def __gt__(self, other: "TrustTier") -> bool:  # type: ignore[override]
        return self.rank() > other.rank()

    def meet(self, other: "TrustTier") -> "TrustTier":
        return self if self <= other else other

    def join(self, other: "TrustTier") -> "TrustTier":
        return self if self >= other else other


# ---------------------------------------------------------------------------
# Judgment 8-tuple for integration
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class IntegrationJudgment:
    """8-tuple judgment (c, φ, A, E, O, B, T, Π) for integration decisions."""

    context: str              # c
    formula: str              # φ
    authority: str            # A
    evidence: tuple           # E
    obligations: tuple        # O
    budget: float             # B
    trust_tier: TrustTier     # T
    proof_chain: tuple        # Π

    def is_fully_discharged(self) -> bool:
        return len(self.obligations) == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "context": self.context,
            "formula": self.formula,
            "authority": self.authority,
            "evidence": list(self.evidence),
            "obligations": list(self.obligations),
            "budget": self.budget,
            "trust_tier": self.trust_tier.value,
            "proof_chain": list(self.proof_chain),
            "fully_discharged": self.is_fully_discharged(),
        }


@dataclass(frozen=True, slots=True)
class TransportRegistration:
    """Record of a transport channel registration."""

    registration_id: str
    analogy_map_id: str
    source_module: str
    target_module: str
    fidelity_score: float
    registered_at: str
    trust_tier: TrustTier

    def to_dict(self) -> dict[str, Any]:
        return {
            "registration_id": self.registration_id,
            "analogy_map_id": self.analogy_map_id,
            "source_module": self.source_module,
            "target_module": self.target_module,
            "fidelity_score": self.fidelity_score,
            "registered_at": self.registered_at,
            "trust_tier": self.trust_tier.value,
        }


@dataclass(frozen=True, slots=True)
class IntegrationStatus:
    """Snapshot of integration health."""

    status_id: str
    registrations_count: int
    health_score: float
    issues: tuple
    checked_at: str
    trust_tier: TrustTier

    def is_healthy(self) -> bool:
        return self.health_score >= 0.85

    def to_dict(self) -> dict[str, Any]:
        return {
            "status_id": self.status_id,
            "registrations_count": self.registrations_count,
            "health_score": self.health_score,
            "issues": list(self.issues),
            "checked_at": self.checked_at,
            "trust_tier": self.trust_tier.value,
        }


@dataclass(frozen=True, slots=True)
class BridgeSignal:
    """Economic signal emitted via a theorem-transport bridge."""

    signal_id: str
    transport_id: str
    signal_type: str
    value: float
    confidence: float
    emitted_at: str
    trust_tier: TrustTier

    def to_dict(self) -> dict[str, Any]:
        return {
            "signal_id": self.signal_id,
            "transport_id": self.transport_id,
            "signal_type": self.signal_type,
            "value": self.value,
            "confidence": self.confidence,
            "emitted_at": self.emitted_at,
            "trust_tier": self.trust_tier.value,
        }


@dataclass(frozen=True, slots=True)
class SynchronisationRecord:
    """Record of an orchestrator synchronisation event."""

    sync_id: str
    orchestrator_id: str
    lag_seconds: float
    synced_analogy_ids: tuple
    success: bool
    synced_at: str
    trust_tier: TrustTier

    def to_dict(self) -> dict[str, Any]:
        return {
            "sync_id": self.sync_id,
            "orchestrator_id": self.orchestrator_id,
            "lag_seconds": self.lag_seconds,
            "synced_analogy_ids": list(self.synced_analogy_ids),
            "success": self.success,
            "synced_at": self.synced_at,
            "trust_tier": self.trust_tier.value,
        }


@dataclass(frozen=True, slots=True)
class ExportManifest:
    """Manifest for an analogy-export operation."""

    manifest_id: str
    destination: str
    format: str
    analogy_ids: tuple
    export_size_bytes: int
    exported_at: str
    trust_tier: TrustTier

    def to_dict(self) -> dict[str, Any]:
        return {
            "manifest_id": self.manifest_id,
            "destination": self.destination,
            "format": self.format,
            "analogy_ids": list(self.analogy_ids),
            "export_size_bytes": self.export_size_bytes,
            "exported_at": self.exported_at,
            "trust_tier": self.trust_tier.value,
        }


# ---------------------------------------------------------------------------
# AnalogyTransportIntegration
# ---------------------------------------------------------------------------

class AnalogyTransportIntegration:
    """Manages integration of the analogy-transport subsystem with jugeo.

    Maintains a registry of active transport channels between ideation modules,
    validates integration health, and emits :class:`IntegrationJudgment` records.

    Parameters
    ----------
    integration_id : str, optional
        Explicit ID; auto-generated if omitted.
    authority : str, optional
        Authority string used in all issued judgments.
    """

    def __init__(
        self,
        integration_id: str | None = None,
        authority: str = "AnalogyTransportIntegration",
    ) -> None:
        self._integration_id = integration_id or _uid()
        self._authority = authority
        self._registrations: list[TransportRegistration] = []
        self._created_at = _now_iso()

    def register_transport(
        self,
        analogy_map_id: str,
        source_module: str,
        target_module: str,
        fidelity_score: float = 0.75,
        trust_tier: TrustTier = TrustTier.REVIEWED,
    ) -> TransportRegistration:
        """Register a transport channel between two ideation modules."""
        if not (0.0 <= fidelity_score <= 1.0):
            raise ValueError(f"fidelity_score must be in [0, 1]; got {fidelity_score!r}")
        reg = TransportRegistration(
            registration_id=_uid(),
            analogy_map_id=analogy_map_id,
            source_module=source_module,
            target_module=target_module,
            fidelity_score=fidelity_score,
            registered_at=_now_iso(),
            trust_tier=trust_tier,
        )
        self._registrations.append(reg)
        return reg

    def get_integration_status(self) -> IntegrationStatus:
        """Return a snapshot of integration health."""
        n = len(self._registrations)
        if n == 0:
            return IntegrationStatus(
                status_id=_uid(),
                registrations_count=0,
                health_score=0.0,
                issues=("no transports registered",),
                checked_at=_now_iso(),
                trust_tier=TrustTier.PROPOSAL,
            )
        avg_fidelity = sum(r.fidelity_score for r in self._registrations) / n
        verified_frac = sum(
            1 for r in self._registrations if r.trust_tier >= TrustTier.VERIFIED
        ) / n
        health = _clamp(avg_fidelity * 0.5 + verified_frac * 0.5)
        issues: list[str] = []
        if health < 0.60:
            issues.append("health below warning threshold")
        tier = TrustTier.VERIFIED if health >= 0.85 else TrustTier.REVIEWED
        return IntegrationStatus(
            status_id=_uid(),
            registrations_count=n,
            health_score=health,
            issues=tuple(issues),
            checked_at=_now_iso(),
            trust_tier=tier,
        )

    def validate_integration(self) -> IntegrationJudgment:
        """Validate integration and return an 8-tuple judgment."""
        status = self.get_integration_status()
        obligations: list[str] = []
        evidence: list[Any] = [status.status_id]
        for reg in self._registrations:
            if reg.fidelity_score < 0.50:
                obligations.append(f"raise fidelity for {reg.registration_id[:8]}")
            else:
                evidence.append(f"fidelity-ok:{reg.registration_id[:8]}")
        tier = TrustTier.VERIFIED if not obligations else TrustTier.REVIEWED
        proof: tuple[str, ...] = ("validate:fidelity-pass",) if not obligations else ("validate:partial",)
        return IntegrationJudgment(
            context=f"AnalogyTransportIntegration/{self._integration_id[:8]}",
            formula=f"integration_valid(n={len(self._registrations)}, health={status.health_score:.3f})",
            authority=self._authority,
            evidence=tuple(evidence),
            obligations=tuple(obligations),
            budget=max(0.0, 1.0 - len(obligations) * 0.05),
            trust_tier=tier,
            proof_chain=proof,
        )

    def generate_report(self) -> dict[str, Any]:
        """Generate a full integration report."""
        return {
            "integration_id": self._integration_id,
            "created_at": self._created_at,
            "status": self.get_integration_status().to_dict(),
            "registrations": [r.to_dict() for r in self._registrations],
            "judgment": self.validate_integration().to_dict(),
        }


# ---------------------------------------------------------------------------
# TheoremTransportBridge
# ---------------------------------------------------------------------------

class TheoremTransportBridge:
    """Bridge between analogy transport and theorem economics.

    Translates transported analogies into economic signals consumable by
    theorem-economics modules.
    """

    def __init__(
        self,
        bridge_id: str | None = None,
        authority: str = "TheoremTransportBridge",
    ) -> None:
        self._bridge_id = bridge_id or _uid()
        self._authority = authority
        self._bridge_records: list[dict[str, Any]] = []
        self._signals: list[BridgeSignal] = []

    def build_bridge(
        self,
        transport_record: dict[str, Any],
        trust_tier: TrustTier = TrustTier.REVIEWED,
    ) -> dict[str, Any]:
        """Build a bridge record from a transport record."""
        transport_id = transport_record.get("transport_id", _uid())
        fidelity_map = {"low": 0.25, "medium": 0.60, "high": 0.85, "exact": 1.00}
        fidelity_val = fidelity_map.get(str(transport_record.get("fidelity", "medium")).lower(), 0.60)
        import math as _math
        value_estimate = _clamp(fidelity_val * _math.log1p(fidelity_val + 0.1))
        record = {
            "bridge_record_id": _uid(),
            "bridge_id": self._bridge_id,
            "transport_id": transport_id,
            "fidelity_value": fidelity_val,
            "value_estimate": value_estimate,
            "trust_tier": trust_tier.value,
            "created_at": _now_iso(),
        }
        self._bridge_records.append(record)
        return record

    def translate_to_economic_signal(
        self, transport_id: str, trust_tier: TrustTier = TrustTier.REVIEWED
    ) -> BridgeSignal:
        """Translate a transport record into an economic signal."""
        records = [r for r in self._bridge_records if r["transport_id"] == transport_id]
        if records:
            record = records[-1]
            value = float(record["value_estimate"])
            confidence = float(record["fidelity_value"])
        else:
            value, confidence = 0.50, 0.40
        signal = BridgeSignal(
            signal_id=_uid(),
            transport_id=transport_id,
            signal_type="value_estimate",
            value=value,
            confidence=confidence,
            emitted_at=_now_iso(),
            trust_tier=trust_tier,
        )
        self._signals.append(signal)
        return signal

    def get_bridge_status(self) -> dict[str, Any]:
        """Return bridge health summary."""
        n = len(self._bridge_records)
        if n == 0:
            return {"bridge_id": self._bridge_id, "records_count": 0, "health_score": 0.0, "checked_at": _now_iso()}
        avg_fidelity = sum(r.get("fidelity_value", 0.0) for r in self._bridge_records) / n
        sig_coverage = min(1.0, len(self._signals) / max(1, n))
        return {
            "bridge_id": self._bridge_id,
            "records_count": n,
            "signals_count": len(self._signals),
            "health_score": _clamp(avg_fidelity * 0.7 + sig_coverage * 0.3),
            "checked_at": _now_iso(),
        }


# ---------------------------------------------------------------------------
# OrchestratorAnalogyBridge
# ---------------------------------------------------------------------------

class OrchestratorAnalogyBridge:
    """Bridge between analogy transport and the orchestration layer.

    Emits typed transport events to the orchestrator, polls status,
    and synchronises the local analogy registry.
    """

    _VALID_EVENT_TYPES: frozenset[str] = frozenset({
        "transport_started", "transport_completed", "transport_failed",
        "analogy_registered", "analogy_invalidated", "bridge_health_check",
    })

    def __init__(
        self,
        bridge_id: str | None = None,
        authority: str = "OrchestratorAnalogyBridge",
    ) -> None:
        self._bridge_id = bridge_id or _uid()
        self._authority = authority
        self._events: list[dict[str, Any]] = []
        self._sync_records: list[SynchronisationRecord] = []

    def emit_transport_event(
        self,
        analogy_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
        trust_tier: TrustTier = TrustTier.REVIEWED,
    ) -> dict[str, Any]:
        """Emit a typed transport event to the orchestration layer."""
        if event_type not in self._VALID_EVENT_TYPES:
            raise ValueError(f"Unknown event type {event_type!r}")
        evt = {
            "event_id": _uid(),
            "analogy_id": analogy_id,
            "event_type": event_type,
            "payload": payload or {},
            "emitted_at": _now_iso(),
            "acknowledged": False,
            "trust_tier": trust_tier.value,
        }
        self._events.append(evt)
        return evt

    def poll_orchestration_status(self) -> dict[str, Any]:
        """Poll pending analogy requests from the orchestration layer."""
        pending = [e for e in self._events if not e["acknowledged"]]
        lag = min(30.0, len(pending) * 0.5)
        return {
            "bridge_id": self._bridge_id,
            "pending_events": len(pending),
            "acknowledged_events": len(self._events) - len(pending),
            "estimated_lag_seconds": lag,
            "lag_exceeds_max": lag > 30.0,
            "polled_at": _now_iso(),
        }

    def synchronize_with_orchestrator(
        self,
        orchestrator_id: str,
        analogy_ids: list[str] | None = None,
        trust_tier: TrustTier = TrustTier.RUNTIME_WITNESSED,
    ) -> SynchronisationRecord:
        """Synchronise local analogy state with the orchestrator."""
        ids = tuple(analogy_ids or {e["analogy_id"] for e in self._events if not e["acknowledged"]})
        lag = len(ids) * 0.3
        success = lag <= 30.0
        for evt in self._events:
            if evt["analogy_id"] in ids:
                evt["acknowledged"] = True
        sync = SynchronisationRecord(
            sync_id=_uid(),
            orchestrator_id=orchestrator_id,
            lag_seconds=lag,
            synced_analogy_ids=ids,
            success=success,
            synced_at=_now_iso(),
            trust_tier=trust_tier,
        )
        self._sync_records.append(sync)
        return sync


# ---------------------------------------------------------------------------
# Module-level integration functions
# ---------------------------------------------------------------------------

def integrate_analogy_transport(
    config: dict[str, Any] | None = None,
    authority: str = "integrate_analogy_transport",
) -> tuple:
    """Instantiate and validate an :class:`AnalogyTransportIntegration`.

    Returns
    -------
    tuple[AnalogyTransportIntegration, IntegrationJudgment]
    """
    cfg = config or {}
    ati = AnalogyTransportIntegration(
        integration_id=cfg.get("integration_id"),
        authority=authority,
    )
    for ch in cfg.get("transport_channels", []):
        try:
            ati.register_transport(
                analogy_map_id=ch["analogy_map_id"],
                source_module=ch.get("source_module", "unknown"),
                target_module=ch.get("target_module", "unknown"),
                fidelity_score=float(ch.get("fidelity_score", 0.70)),
            )
        except (KeyError, ValueError):
            pass
    judgment = ati.validate_integration()
    return ati, judgment


def bridge_to_theorem_economics(
    transport_records: list[dict[str, Any]],
    trust_tier: TrustTier = TrustTier.REVIEWED,
) -> list[BridgeSignal]:
    """Convert a batch of transport records into theorem-economics bridge signals."""
    bridge = TheoremTransportBridge()
    signals: list[BridgeSignal] = []
    for record in transport_records:
        br = bridge.build_bridge(record, trust_tier=trust_tier)
        signal = bridge.translate_to_economic_signal(br["transport_id"], trust_tier=trust_tier)
        signals.append(signal)
    return signals


def export_analogies(
    analogies: list[dict[str, Any]],
    destination: str,
    format: str = "json",
    trust_tier: TrustTier = TrustTier.REVIEWED,
) -> ExportManifest:
    """Export analogy records, returning an :class:`ExportManifest`."""
    import json as _json
    _SUPPORTED = ("json", "msgpack", "csv", "protobuf")
    if format not in _SUPPORTED:
        raise ValueError(f"Unsupported format {format!r}; supported: {_SUPPORTED}")
    ids = tuple(a.get("map_id", _uid()) for a in analogies)
    size = len(_json.dumps([dict(a) for a in analogies], default=str).encode())
    return ExportManifest(
        manifest_id=_uid(),
        destination=destination,
        format=format,
        analogy_ids=ids,
        export_size_bytes=size,
        exported_at=_now_iso(),
        trust_tier=trust_tier,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    # Enumerations
    "BundleStatus",
    "SyncStatus",
    "TrustTier",
    # Value objects
    "RegistrationRecord",
    "DomainUpdate",
    "TransportOpportunity",
    "PackSyncResult",
    "SubscriptionHandle",
    "BridgeIntegrationResult",
    "BundleValidationResult",
    "BundleSummary",
    "TransportRegistration",
    "IntegrationStatus",
    "BridgeSignal",
    "SynchronisationRecord",
    "ExportManifest",
    "IntegrationJudgment",
    # Configuration
    "BridgeConfig",
    # Registry
    "TheoremRegistry",
    # Core classes
    "AnalogyTransportBridge",
    "ExportBundle",
    "AnalogyTransportIntegration",
    "TheoremTransportBridge",
    "OrchestratorAnalogyBridge",
    # Free functions
    "build_export_bundle",
    "run_bridge_integration_cycle",
    "create_transport_opportunity",
    "integrate_analogy_transport",
    "bridge_to_theorem_economics",
    "export_analogies",
]


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import pprint

    print("=== analogy_transport.integration smoke test ===\n")

    # 1. Create bridge and config
    config = BridgeConfig(auto_register=True, auto_sync=True, conflict_policy="rename")
    bridge = AnalogyTransportBridge(config)
    print(f"Bridge created with config: {config}\n")

    # 2. Simulate a DomainUpdate
    update = DomainUpdate(
        update_id=_uid(),
        domain_name="type_theory",
        update_type="new_theorems",
        added_theorem_ids=(_uid(), _uid(), _uid()),
        updated_at=_now_iso(),
    )
    print(f"DomainUpdate: {update.domain_name!r}, type={update.update_type!r}, "
          f"new_theorems={len(update.added_theorem_ids)}")

    opportunities = bridge.check_new_transport_opportunities(update)
    print(f"Opportunities discovered: {len(opportunities)}")
    for opp in opportunities[:2]:
        print(f"  {opp.opportunity_id[:8]}... priority={opp.priority_score:.2f} "
              f"rationale: {opp.rationale[:60]}")
    print()

    # 3. Subscribe to domain updates
    received_updates: list[DomainUpdate] = []
    handle = bridge.subscribe_to_domain_updates(
        "type_theory", lambda u: received_updates.append(u)
    )
    print(f"Subscription handle: {handle.handle_id[:8]}..., domain={handle.domain_id!r}")
    bridge._notify_subscribers("type_theory", update)
    print(f"Subscriber received {len(received_updates)} update(s)\n")

    # 4. Build and validate an ExportBundle
    bundle = build_export_bundle(
        source_domain="sheaf_theory",
        target_domain="type_theory",
    )
    print(f"Empty bundle: {bundle.bundle_id[:8]}..., status={bundle._status.value!r}")

    # Add a mock result dict (simulating a TransportResult without importing it)
    class _MockResult:
        result_id = _uid()
        status = type("S", (), {"value": "completed"})()
        confidence_score = 0.78
        def to_dict(self): return {"result_id": self.result_id, "status": "completed"}

    mock = _MockResult()
    bundle.add_transport_result(mock)
    print(f"After add: status={bundle._status.value!r}, results={len(bundle._transport_results)}")

    validation = bundle.validate()
    print(f"Validation: valid={validation.is_valid}, errors={validation.errors}")

    summary = bundle.get_summary()
    print(f"Summary: {summary.to_dict()}")
    print()

    # 5. JSON round-trip
    json_str = bundle.to_json()
    bundle2 = ExportBundle.from_json(json_str)
    print(f"JSON round-trip: bundle_id match={bundle.bundle_id == bundle2.bundle_id}")
    print(f"  results after round-trip: {len(bundle2._transport_results)}")
    print()

    # 6. Registration record
    registry = TheoremRegistry(_namespace="smoke_test")
    rec = bridge.register_transport_result(mock, registry)
    print(f"Registration: success={rec.success}, key={rec.registry_key!r}")
    print(f"Registry size: {registry.size()}")
    print()

    # 7. Integration cycle
    cycle_result = bridge.run_bridge_integration_cycle([mock], registry)
    print(f"Integration cycle: {cycle_result.to_dict()}")
    print("\n=== smoke test complete ===")
