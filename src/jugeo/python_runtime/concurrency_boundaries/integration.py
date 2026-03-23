"""Ch24 Integration Layer — Concurrency Boundaries to JuGeo Subsystems.

This module connects the concurrency_boundaries package to the geometry
(supports), judgments, evidence (channels), and orchestration (fleet)
subsystems of JuGeo.  Each bridge class handles bidirectional translation
between the concurrency domain model and the target subsystem's domain model.

The integration layer follows a strict one-directional dependency rule: the
concurrency_boundaries package may *import* from the four target subsystems
but must never be imported by them.  All cross-package imports are guarded by
try/except ImportError blocks so that this module is usable in isolation when
the wider JuGeo installation is not present.

Typical usage::

    from jugeo.python_runtime.concurrency_boundaries.integration import (
        ConcurrencyBoundariesIntegration,
        SupportBridge,
        JudgmentBridge,
        FleetBridge,
    )

    integration = ConcurrencyBoundariesIntegration(
        support_bridge=SupportBridge(),
        judgment_bridge=JudgmentBridge(),
        fleet_bridge=FleetBridge(),
    )
    result = integration.full_integration_check(section, scope)
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

# ══════════════════════════════════════════════════════
# Cross-package imports — geometry.supports
# ══════════════════════════════════════════════════════

try:
    from jugeo.geometry.supports import SupportRegion, SupportSet, SupportTracker
except ImportError:
    class SupportRegion:  # type: ignore[no-redef]
        """Stub."""
        def __init__(self, coordinate: str = "") -> None:
            self.coordinate = coordinate

        def to_mapping(self) -> dict:
            return {"coordinate": self.coordinate}

    class SupportSet:  # type: ignore[no-redef]
        """Stub."""
        def __init__(self, coordinates: frozenset[str] = frozenset()) -> None:
            self.coordinates = coordinates

        def union(self, other: SupportSet) -> SupportSet:
            return SupportSet(self.coordinates | other.coordinates)

    class SupportTracker:  # type: ignore[no-redef]
        """Stub."""
        def __init__(self) -> None:
            self._history: list[dict] = []

        def record(self, event: dict) -> None:
            self._history.append(event)

# ══════════════════════════════════════════════════════
# Cross-package imports — judgments.judgment_terms
# ══════════════════════════════════════════════════════

try:
    from jugeo.judgments.judgment_terms import LocalJudgment, JudgmentStatus, TrustLevel
    TrustTier = TrustLevel
except ImportError:
    class LocalJudgment:  # type: ignore[no-redef]
        """Stub."""
        def __init__(self, coordinate: str = "", proposition: str = "") -> None:
            self.coordinate = coordinate
            self.proposition = proposition

    class JudgmentStatus:  # type: ignore[no-redef]
        """Stub."""
        PROPOSED = "proposed"
        SETTLED = "settled"
        OBSTRUCTED = "obstructed"
        CHALLENGED = "challenged"

    class TrustLevel:  # type: ignore[no-redef]
        """Stub."""
        UNVERIFIED = 1
        SOLVER_DISCHARGED = 4
        VERIFIED_PROOF = 5

    TrustTier = TrustLevel

# ══════════════════════════════════════════════════════
# Cross-package imports — evidence.channels
# ══════════════════════════════════════════════════════

try:
    from jugeo.evidence.channels import EvidenceChannel, EvidenceRecord, ChannelRouter
except ImportError:
    class EvidenceChannel:  # type: ignore[no-redef]
        """Stub."""
        SOLVER = "solver"
        RUNTIME = "runtime"

    class EvidenceRecord:  # type: ignore[no-redef]
        """Stub."""
        def __init__(self, channel: str = "", payload: str = "", trust_level: int = 1) -> None:
            self.channel = channel
            self.payload = payload
            self.trust_level = trust_level

    class ChannelRouter:  # type: ignore[no-redef]
        """Stub."""
        def __init__(self) -> None:
            self._routes: dict = {}

        def route(self, request: dict) -> dict:
            return {}

# ══════════════════════════════════════════════════════
# Cross-package imports — orchestration.fleet
# ══════════════════════════════════════════════════════

try:
    from jugeo.orchestration.fleet import Fleet, FleetBid, FleetMember
except ImportError:
    class FleetMember:  # type: ignore[no-redef]
        """Stub."""
        def __init__(self, name: str = "", capacity: int = 1) -> None:
            self.name = name
            self.capacity = capacity
            self.member_id: str | None = None
            self.capabilities: frozenset[str] = frozenset()

    class FleetBid:  # type: ignore[no-redef]
        """Stub."""
        def __init__(self) -> None:
            self.bid_id: str = ""
            self.member_id: str = ""
            self.confidence: float = 0.5

    class Fleet:  # type: ignore[no-redef]
        """Stub."""
        def __init__(self) -> None:
            self._members: dict = {}

        def register_member(self, member: FleetMember) -> None:
            if member.member_id:
                self._members[member.member_id] = member

        def solicit_bids(self, target: str, required_capabilities: frozenset[str]) -> list:
            return []

# ══════════════════════════════════════════════════════
# Local imports — concurrency_boundaries.models
# ══════════════════════════════════════════════════════

try:
    from jugeo.python_runtime.concurrency_boundaries.models import (
        TaskLocalSection,
        CancellationRecord,
        ExceptionGroupRecord,
        ProcessBoundary,
        ConcurrencyScope,
        ConcurrencyRole,
        CancellationReason,
        BoundaryKind,
        ScopeStatus,
    )
except ImportError:
    class TaskLocalSection:  # type: ignore[no-redef]
        """Stub."""
        def __init__(
            self,
            section_id: str = "",
            task_id: str = "",
            task_name: str = "",
            support_keys: frozenset[str] = frozenset(),
            data: dict | None = None,
        ) -> None:
            self.section_id = section_id
            self.task_id = task_id
            self.task_name = task_name
            self.support_keys = support_keys
            self.data: dict = data or {}

    class CancellationRecord:  # type: ignore[no-redef]
        """Stub."""
        def __init__(
            self,
            record_id: str = "",
            task_id: str = "",
            reason: str = "unknown",
            timestamp: float = 0.0,
            propagated: bool = False,
        ) -> None:
            self.record_id = record_id
            self.task_id = task_id
            self.reason = reason
            self.timestamp = timestamp
            self.propagated = propagated

    class ExceptionGroupRecord:  # type: ignore[no-redef]
        """Stub."""
        def __init__(
            self,
            group_id: str = "",
            message: str = "",
            exceptions: list | None = None,
            resolved: bool = False,
        ) -> None:
            self.group_id = group_id
            self.message = message
            self.exceptions: list = exceptions or []
            self.resolved = resolved

    class ProcessBoundary:  # type: ignore[no-redef]
        """Stub."""
        def __init__(
            self,
            boundary_id: str = "",
            kind: str = "subprocess",
            active: bool = True,
            allowed_section_ids: frozenset[str] = frozenset(),
        ) -> None:
            self.boundary_id = boundary_id
            self.kind = kind
            self.active = active
            self.allowed_section_ids = allowed_section_ids

    class ConcurrencyScope:  # type: ignore[no-redef]
        """Stub."""
        def __init__(
            self,
            scope_id: str = "",
            status: str = "active",
            child_scopes: list | None = None,
        ) -> None:
            self.scope_id = scope_id
            self.status = status
            self.child_scopes: list = child_scopes or []

    class ConcurrencyRole:  # type: ignore[no-redef]
        """Stub."""
        OWNER = "owner"
        WORKER = "worker"
        OBSERVER = "observer"

    class CancellationReason:  # type: ignore[no-redef]
        """Stub."""
        TIMEOUT = "timeout"
        USER_REQUEST = "user_request"
        DEPENDENCY_FAILED = "dependency_failed"
        INTERNAL_ERROR = "internal_error"

    class BoundaryKind:  # type: ignore[no-redef]
        """Stub."""
        SUBPROCESS = "subprocess"
        THREAD = "thread"
        REMOTE = "remote"

    class ScopeStatus:  # type: ignore[no-redef]
        """Stub."""
        ACTIVE = "active"
        COMPLETED = "completed"
        CANCELLED = "cancelled"
        FAILED = "failed"

# ══════════════════════════════════════════════════════
# Module logger
# ══════════════════════════════════════════════════════

_log = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════
# SupportBridge
# ══════════════════════════════════════════════════════


class SupportBridge:
    """Bridges task-local sections to geometry support regions.

    In the sheaf-theoretic model, a task-local section's support_keys
    correspond to the coordinates of a SupportRegion.  This bridge translates
    between the two representations, enabling geometry operations on task
    sections and propagating obstruction events to support tracking.

    Theory reference: Ch24 — task sections and geometry supports.
    """

    def __init__(self) -> None:
        self._support_tracker: SupportTracker = SupportTracker(
            initial_support=SupportSet(coordinates=frozenset())
        )
        self._section_to_support: dict[str, str] = {}
        self._support_to_section: dict[str, str] = {}
        self._bridge_log: list[dict] = []
        self._sync_count: int = 0

    # ── core translation ──────────────────────────────

    def task_section_to_support(self, section: TaskLocalSection) -> SupportRegion:
        """Translate a TaskLocalSection into a SupportRegion.

        Uses the first support_key in the section as the coordinate.  If the
        section has no support_keys, falls back to section_id.

        Args:
            section: The task-local section to translate.

        Returns:
            A SupportRegion whose coordinate corresponds to the section.
        """
        if section.support_keys:
            coordinate = next(iter(sorted(section.support_keys)))
        else:
            coordinate = section.section_id

        region = SupportRegion(coordinate=coordinate)
        self._section_to_support[section.section_id] = coordinate
        self._support_to_section[coordinate] = section.section_id

        entry: dict[str, object] = {
            "event": "section_to_support",
            "section_id": section.section_id,
            "coordinate": coordinate,
            "timestamp": time.time(),
        }
        self._bridge_log.append(entry)
        _log.debug("SupportBridge: %s → coordinate=%s", section.section_id, coordinate)
        return region

    def support_to_task_section(
        self,
        support: SupportRegion,
        task_id: str,
        task_name: str,
    ) -> TaskLocalSection:
        """Create a TaskLocalSection from a SupportRegion.

        Derives support_keys from the region's coordinate.

        Args:
            support: The SupportRegion to translate.
            task_id: The task id to assign to the new section.
            task_name: The task name to assign to the new section.

        Returns:
            A TaskLocalSection whose support_keys contain the region's
            coordinate.
        """
        coordinate = support.coordinate
        section_id = str(uuid.uuid4())
        support_keys: frozenset[str] = frozenset({coordinate}) if coordinate else frozenset()

        section = TaskLocalSection(
            section_id=section_id,
            task_id=task_id,
            task_name=task_name,
            support_keys=support_keys,
        )

        self._support_to_section[coordinate] = section_id
        self._section_to_support[section_id] = coordinate

        entry: dict[str, object] = {
            "event": "support_to_section",
            "coordinate": coordinate,
            "section_id": section_id,
            "task_id": task_id,
            "timestamp": time.time(),
        }
        self._bridge_log.append(entry)
        _log.debug("SupportBridge: coordinate=%s → section %s", coordinate, section_id)
        return section

    # ── synchronisation helpers ───────────────────────

    def sync_on_cancellation(
        self,
        section: TaskLocalSection,
        record: CancellationRecord,
    ) -> dict[str, object]:
        """Record a cancellation event in the support tracker.

        Builds a cancellation event dict and passes it to the underlying
        SupportTracker for persistence.

        Args:
            section: The section that was cancelled.
            record: The CancellationRecord describing the cancellation.

        Returns:
            A dict summarising the sync outcome, including the event that was
            recorded and the updated sync count.
        """
        event: dict[str, object] = {
            "event_type": "cancellation",
            "section_id": section.section_id,
            "task_id": section.task_id,
            "record_id": record.record_id,
            "reason": str(record.reason),
            "propagated": record.propagated,
            "timestamp": time.time(),
        }
        self._support_tracker.record(event)
        self._sync_count += 1

        result: dict[str, object] = {
            "synced": True,
            "section_id": section.section_id,
            "sync_count": self._sync_count,
            "event": event,
        }
        self._bridge_log.append({**event, "sync_result": "ok"})
        return result

    def propagate_obstruction(
        self,
        record: CancellationRecord,
        target_sections: list[TaskLocalSection],
    ) -> list[dict]:
        """Propagate a cancellation obstruction to a list of target sections.

        For each target section, creates a support event and logs it to the
        bridge log and the support tracker.

        Args:
            record: The originating CancellationRecord.
            target_sections: Sections that should receive the obstruction.

        Returns:
            A list of propagation result dicts, one per target section.
        """
        results: list[dict] = []
        for section in target_sections:
            event: dict[str, object] = {
                "event_type": "obstruction_propagated",
                "source_record_id": record.record_id,
                "source_task_id": record.task_id,
                "target_section_id": section.section_id,
                "target_task_id": section.task_id,
                "timestamp": time.time(),
            }
            self._support_tracker.record(event)
            self._bridge_log.append(event)
            result: dict[str, object] = {
                "propagated": True,
                "target_section_id": section.section_id,
                "source_record_id": record.record_id,
            }
            results.append(result)
            _log.debug(
                "SupportBridge: obstruction from %s propagated to %s",
                record.task_id,
                section.section_id,
            )
        return results

    def check_boundary_support(self, boundary: ProcessBoundary) -> dict[str, object]:
        """Check whether a ProcessBoundary has valid support coverage.

        A boundary is considered valid if it is active and has at least one
        allowed section id.  Coverage is computed as the ratio of known
        sections to allowed section ids.

        Args:
            boundary: The ProcessBoundary to check.

        Returns:
            A dict with keys 'valid' (bool), 'boundary_id' (str), and
            'coverage' (float in [0, 1]).

        Raises:
            ValueError: If boundary is None.
        """
        if boundary is None:
            raise ValueError("boundary must not be None")

        allowed = boundary.allowed_section_ids
        known_count = sum(
            1 for sid in allowed if sid in self._section_to_support
        )
        total = len(allowed)
        coverage = (known_count / total) if total > 0 else 0.0
        valid = boundary.active and total > 0

        result: dict[str, object] = {
            "valid": valid,
            "boundary_id": boundary.boundary_id,
            "coverage": coverage,
            "allowed_count": total,
            "known_count": known_count,
        }
        self._bridge_log.append({**result, "event": "boundary_check", "timestamp": time.time()})
        return result

    # ── state accessors ───────────────────────────────

    def export_bridge_state(self) -> dict[str, object]:
        """Return a complete snapshot of the bridge's internal state.

        Returns:
            A dict containing all mappings, log entries, and counters.
        """
        return {
            "section_to_support": dict(self._section_to_support),
            "support_to_section": dict(self._support_to_section),
            "bridge_log_count": len(self._bridge_log),
            "bridge_log": list(self._bridge_log),
            "sync_count": self._sync_count,
        }

    def sync_count(self) -> int:
        """Return the number of cancellation syncs performed.

        Returns:
            Integer sync count.
        """
        return self._sync_count

    def clear_logs(self) -> int:
        """Clear all bridge log entries and return the count cleared.

        Returns:
            The number of log entries that were cleared.
        """
        count = len(self._bridge_log)
        self._bridge_log.clear()
        return count


# ══════════════════════════════════════════════════════
# JudgmentBridge
# ══════════════════════════════════════════════════════


class JudgmentBridge:
    """Bridges concurrency events to judgment trust levels.

    Cancellation records and exception groups affect the trust level of
    judgments that depended on the cancelled tasks.  This bridge translates
    concurrency events into judgment updates, ensuring that the trust algebra
    reflects the actual execution outcomes.

    Theory reference: Ch24 — concurrency events and judgment trust.
    """

    def __init__(self) -> None:
        self._judgment_log: list[dict] = []
        self._obligation_log: list[dict] = []
        self._bridge_log: list[dict] = []
        self._judgment_registry: dict[str, LocalJudgment] = {}
        self._event_count: int = 0

    # ── translation ───────────────────────────────────

    def cancellation_to_obstruction(
        self,
        record: CancellationRecord,
        coordinate: str = "",
    ) -> LocalJudgment:
        """Create a LocalJudgment representing an obstruction from a cancellation.

        The judgment captures that the task described by the CancellationRecord
        was cancelled, forming an obstruction at the given coordinate.

        Args:
            record: The CancellationRecord describing the cancellation event.
            coordinate: Coordinate for the judgment; defaults to record.task_id.

        Returns:
            A LocalJudgment representing the cancellation obstruction.
        """
        coord = coordinate if coordinate else record.task_id
        proposition = (
            f"Task {record.task_id} was cancelled (reason={record.reason}); "
            f"obstruction recorded at coordinate '{coord}'."
        )
        judgment = LocalJudgment(coordinate=coord, proposition=proposition)
        self._judgment_registry[record.task_id] = judgment

        event: dict[str, object] = {
            "event": "cancellation_to_obstruction",
            "task_id": record.task_id,
            "coordinate": coord,
            "reason": str(record.reason),
            "timestamp": time.time(),
        }
        self._judgment_log.append(event)
        self._bridge_log.append(event)
        self._event_count += 1
        _log.debug("JudgmentBridge: cancellation obstruction for task %s", record.task_id)
        return judgment

    def exception_group_to_obligations(
        self,
        group_record: ExceptionGroupRecord,
    ) -> list[dict]:
        """Derive obligation dicts from an ExceptionGroupRecord.

        Each unresolved exception in the group becomes an obligation that must
        be discharged before the overall computation can be settled.

        Args:
            group_record: The ExceptionGroupRecord to process.

        Returns:
            A list of obligation dicts, each containing 'obligation_id',
            'exception_key', 'description', and 'status'.
        """
        obligations: list[dict] = []
        for idx, exc in enumerate(group_record.exceptions):
            exc_key = f"{group_record.group_id}:{idx}"
            exc_desc = repr(exc) if exc is not None else f"exception_{idx}"
            obligation: dict[str, object] = {
                "obligation_id": str(uuid.uuid4()),
                "exception_key": exc_key,
                "description": (
                    f"Discharge exception from group '{group_record.group_id}': {exc_desc}"
                ),
                "status": "pending",
                "group_id": group_record.group_id,
                "group_message": group_record.message,
                "created_at": time.time(),
            }
            self._obligation_log.append(obligation)
            obligations.append(obligation)

        self._event_count += 1
        self._bridge_log.append({
            "event": "exception_group_to_obligations",
            "group_id": group_record.group_id,
            "obligation_count": len(obligations),
            "timestamp": time.time(),
        })
        return obligations

    def scope_to_judgment(
        self,
        scope: ConcurrencyScope,
        coordinate: str = "",
    ) -> LocalJudgment:
        """Create a LocalJudgment summarising the health of a ConcurrencyScope.

        Trust levels are assigned based on scope status:
        - ACTIVE → 3 (RUNTIME_WITNESSED)
        - COMPLETED → 4 (SOLVER_DISCHARGED)
        - CANCELLED → 0 (obstructed / CONTRADICTED)
        - FAILED → 0
        - Other → 1 (UNVERIFIED)

        Args:
            scope: The ConcurrencyScope to summarise.
            coordinate: Coordinate for the judgment; defaults to scope.scope_id.

        Returns:
            A LocalJudgment with a proposition and trust level reflecting the
            scope's execution status.
        """
        coord = coordinate if coordinate else scope.scope_id
        status_str = str(scope.status).lower()

        trust_descriptions = {
            "active": "runtime_witnessed (3)",
            "completed": "solver_discharged (4)",
            "cancelled": "contradicted (0)",
            "failed": "contradicted (0)",
        }
        trust_desc = trust_descriptions.get(status_str, "unverified (1)")
        proposition = (
            f"ConcurrencyScope '{scope.scope_id}' has status '{scope.status}'; "
            f"trust_level={trust_desc}."
        )
        judgment = LocalJudgment(coordinate=coord, proposition=proposition)
        self._judgment_registry[scope.scope_id] = judgment
        self._event_count += 1

        entry: dict[str, object] = {
            "event": "scope_to_judgment",
            "scope_id": scope.scope_id,
            "status": str(scope.status),
            "trust_desc": trust_desc,
            "timestamp": time.time(),
        }
        self._judgment_log.append(entry)
        self._bridge_log.append(entry)
        return judgment

    def sync_after_cancellation(self, record: CancellationRecord) -> dict[str, object]:
        """Synchronise judgment registry after a cancellation event.

        Looks up any judgments keyed by the record's task_id and returns a
        summary of the update.

        Args:
            record: The CancellationRecord that triggered the sync.

        Returns:
            A dict with 'task_id', 'judgment_found' (bool), 'updated' (bool),
            and 'sync_time'.
        """
        judgment = self._judgment_registry.get(record.task_id)
        judgment_found = judgment is not None
        updated = False

        if judgment_found:
            # Update the proposition to reflect the post-cancellation state
            new_proposition = (
                f"{judgment.proposition} [SYNC: cancelled at {time.time():.3f}]"
            )
            updated_judgment = LocalJudgment(
                coordinate=judgment.coordinate,
                proposition=new_proposition,
            )
            self._judgment_registry[record.task_id] = updated_judgment
            updated = True

        result: dict[str, object] = {
            "task_id": record.task_id,
            "judgment_found": judgment_found,
            "updated": updated,
            "sync_time": time.time(),
        }
        self._bridge_log.append({**result, "event": "sync_after_cancellation"})
        self._event_count += 1
        return result

    # ── state accessors ───────────────────────────────

    def export_bridge_state(self) -> dict[str, object]:
        """Return a complete snapshot of the judgment bridge's state.

        Returns:
            A dict with judgment log, obligation log, registry size, and
            event count.
        """
        return {
            "judgment_log_count": len(self._judgment_log),
            "obligation_log_count": len(self._obligation_log),
            "bridge_log_count": len(self._bridge_log),
            "registry_size": len(self._judgment_registry),
            "event_count": self._event_count,
            "registered_task_ids": list(self._judgment_registry.keys()),
        }

    def event_count(self) -> int:
        """Return the total number of events processed by this bridge.

        Returns:
            Integer event count.
        """
        return self._event_count

    def pending_obligations(self) -> list[dict]:
        """Return all obligations with status 'pending'.

        Returns:
            Filtered list of obligation dicts.
        """
        return [o for o in self._obligation_log if o.get("status") == "pending"]

    def judgment_count(self) -> int:
        """Return the number of judgments in the registry.

        Returns:
            Integer count.
        """
        return len(self._judgment_registry)


# ══════════════════════════════════════════════════════
# FleetBridge
# ══════════════════════════════════════════════════════


class FleetBridge:
    """Bridges to fleet orchestration for distributed task management.

    In distributed execution, tasks are allocated to fleet members.  This
    bridge registers ConcurrencyScope instances with the Fleet, coordinates
    cancellation across fleet members, and translates fleet task results back
    into section updates.

    Theory reference: Ch24 — distributed task sections and fleet coordination.
    """

    def __init__(self) -> None:
        self._fleet: Fleet = Fleet()
        self._scope_registry: dict[str, ConcurrencyScope] = {}
        self._bid_log: list[dict] = []
        self._bridge_log: list[dict] = []
        self._task_results: dict[str, dict] = {}

    # ── fleet registration ────────────────────────────

    def register_scope_with_fleet(
        self,
        scope: ConcurrencyScope,
        required_capabilities: frozenset[str] | None = None,
    ) -> dict[str, object]:
        """Register a ConcurrencyScope with the Fleet and solicit bids.

        Stores the scope in the local registry, then asks the underlying Fleet
        to solicit bids from its members for the scope's task target.

        Args:
            scope: The ConcurrencyScope to register.
            required_capabilities: Optional set of capability tags required for
                bid eligibility.

        Returns:
            A dict with 'scope_id', 'bids_received' (int), and 'bid_ids'
            (list of str).
        """
        caps: frozenset[str] = required_capabilities if required_capabilities else frozenset()
        self._scope_registry[scope.scope_id] = scope

        bids = self._fleet.solicit_bids(target=scope.scope_id, required_capabilities=caps)
        bid_ids: list[str] = []
        for bid in bids:
            bid_id = getattr(bid, "bid_id", str(uuid.uuid4()))
            bid_ids.append(bid_id)
            self._bid_log.append({
                "bid_id": bid_id,
                "scope_id": scope.scope_id,
                "member_id": getattr(bid, "member_id", ""),
                "confidence": getattr(bid, "confidence", 0.5),
                "timestamp": time.time(),
            })

        result: dict[str, object] = {
            "scope_id": scope.scope_id,
            "bids_received": len(bids),
            "bid_ids": bid_ids,
        }
        self._bridge_log.append({**result, "event": "register_scope", "timestamp": time.time()})
        _log.debug("FleetBridge: scope %s registered, %d bids", scope.scope_id, len(bids))
        return result

    def bid_on_task(
        self,
        task_id: str,
        member_name: str,
        confidence: float = 0.5,
    ) -> FleetBid:
        """Create a FleetBid for a specific task from a named member.

        Instantiates a FleetMember, registers it with the fleet, then
        creates a FleetBid and records it in the bid log.

        Args:
            task_id: The task being bid on.
            member_name: Human-readable name for the bidding member.
            confidence: The bid confidence score (0.0–1.0).

        Returns:
            A FleetBid capturing the bid details.

        Raises:
            ValueError: If confidence is outside [0, 1].
        """
        if not 0.0 <= confidence <= 1.0:
            raise ValueError(f"confidence must be in [0, 1]; got {confidence}")

        member = FleetMember(name=member_name, capacity=1)
        member_id = str(uuid.uuid4())
        member.member_id = member_id
        self._fleet.register_member(member)

        bid = FleetBid()
        bid.bid_id = str(uuid.uuid4())
        bid.member_id = member_id
        bid.confidence = confidence

        log_entry: dict[str, object] = {
            "bid_id": bid.bid_id,
            "task_id": task_id,
            "member_id": member_id,
            "member_name": member_name,
            "confidence": confidence,
            "timestamp": time.time(),
        }
        self._bid_log.append(log_entry)
        self._bridge_log.append({**log_entry, "event": "bid_on_task"})
        return bid

    def coordinate_cancellation(
        self,
        scope: ConcurrencyScope,
        record: CancellationRecord,
    ) -> dict[str, object]:
        """Coordinate cancellation across child scopes in the fleet.

        For each child scope attached to the given scope, creates a
        cancellation coordination event and records it in the bridge log.

        Args:
            scope: The parent ConcurrencyScope being cancelled.
            record: The CancellationRecord describing the cancellation.

        Returns:
            A dict with 'scope_id', 'children_notified' (int), and
            'coordination_events' (list of event dicts).
        """
        events: list[dict] = []
        for child in scope.child_scopes:
            child_id = getattr(child, "scope_id", str(child))
            event: dict[str, object] = {
                "event": "cancellation_coordination",
                "parent_scope_id": scope.scope_id,
                "child_scope_id": child_id,
                "record_id": record.record_id,
                "reason": str(record.reason),
                "timestamp": time.time(),
            }
            events.append(event)
            self._bridge_log.append(event)
            _log.debug(
                "FleetBridge: cancellation coordinated from %s to child %s",
                scope.scope_id,
                child_id,
            )

        result: dict[str, object] = {
            "scope_id": scope.scope_id,
            "children_notified": len(events),
            "coordination_events": events,
        }
        return result

    def receive_fleet_task_result(
        self,
        task_id: str,
        result: dict[str, object],
    ) -> dict[str, object]:
        """Store and acknowledge a task result received from the fleet.

        Args:
            task_id: The id of the task whose result is being received.
            result: Arbitrary result payload from the fleet member.

        Returns:
            An acknowledgement dict with 'task_id', 'ack' (True), and
            'received_at'.
        """
        self._task_results[task_id] = dict(result)
        ack: dict[str, object] = {
            "task_id": task_id,
            "ack": True,
            "received_at": time.time(),
        }
        self._bridge_log.append({**ack, "event": "receive_task_result"})
        return ack

    # ── state accessors ───────────────────────────────

    def export_bridge_state(self) -> dict[str, object]:
        """Return a complete snapshot of the fleet bridge's state.

        Returns:
            A dict with scope registry, bid log, task results, and bridge log
            summary.
        """
        return {
            "scope_ids": list(self._scope_registry.keys()),
            "bid_count": len(self._bid_log),
            "task_result_count": len(self._task_results),
            "bridge_log_count": len(self._bridge_log),
            "bid_log": list(self._bid_log),
        }

    def registered_scopes(self) -> list[str]:
        """Return a list of all registered scope ids.

        Returns:
            List of scope_id strings.
        """
        return list(self._scope_registry.keys())

    def result_count(self) -> int:
        """Return the number of task results received.

        Returns:
            Integer count.
        """
        return len(self._task_results)

    def bid_count(self) -> int:
        """Return the number of bids recorded.

        Returns:
            Integer count.
        """
        return len(self._bid_log)


# ══════════════════════════════════════════════════════
# ConcurrencyBoundariesIntegration
# ══════════════════════════════════════════════════════


class ConcurrencyBoundariesIntegration:
    """Main integration class for the concurrency_boundaries package.

    Orchestrates all bridge classes and provides a unified interface for
    integrating concurrency boundary events with the rest of JuGeo.

    Theory reference: Ch24 — full integration of concurrency boundaries.
    """

    def __init__(
        self,
        support_bridge: SupportBridge | None = None,
        judgment_bridge: JudgmentBridge | None = None,
        fleet_bridge: FleetBridge | None = None,
    ) -> None:
        self._support_bridge: SupportBridge = support_bridge or SupportBridge()
        self._judgment_bridge: JudgmentBridge = judgment_bridge or JudgmentBridge()
        self._fleet_bridge: FleetBridge = fleet_bridge or FleetBridge()
        self._integration_log: list[dict] = []
        self._health_status: dict[str, bool] = {
            "support_bridge": True,
            "judgment_bridge": True,
            "fleet_bridge": True,
            "initialized": False,
        }
        self._initialized: bool = False
        self._mark_initialized()

    def _mark_initialized(self) -> None:
        self._initialized = True
        self._health_status["initialized"] = True
        self._integration_log.append({
            "event": "integration_initialized",
            "timestamp": time.time(),
        })

    # ── per-subsystem integrations ────────────────────

    def integrate_with_supports(self, section: TaskLocalSection) -> dict[str, object]:
        """Integrate a TaskLocalSection with the geometry supports subsystem.

        Translates the section into a SupportRegion and records the mapping.

        Args:
            section: The section to integrate.

        Returns:
            A dict with 'integration', 'section_id', 'coordinate', and
            'timestamp'.
        """
        region = self._support_bridge.task_section_to_support(section)
        result: dict[str, object] = {
            "integration": "supports",
            "section_id": section.section_id,
            "coordinate": region.coordinate,
            "timestamp": time.time(),
        }
        self._integration_log.append(result)
        return result

    def integrate_with_judgments(self, record: CancellationRecord) -> dict[str, object]:
        """Integrate a CancellationRecord with the judgments subsystem.

        Creates an obstruction judgment from the cancellation record.

        Args:
            record: The CancellationRecord to translate into an obstruction.

        Returns:
            A dict with 'integration', 'task_id', 'coordinate', and
            'timestamp'.
        """
        judgment = self._judgment_bridge.cancellation_to_obstruction(record)
        result: dict[str, object] = {
            "integration": "judgments",
            "task_id": record.task_id,
            "coordinate": judgment.coordinate,
            "proposition_length": len(judgment.proposition),
            "timestamp": time.time(),
        }
        self._integration_log.append(result)
        return result

    def integrate_with_channels(
        self, group_record: ExceptionGroupRecord
    ) -> dict[str, object]:
        """Integrate an ExceptionGroupRecord with the evidence channels subsystem.

        Converts the exception group into a list of discharge obligations.

        Args:
            group_record: The ExceptionGroupRecord to process.

        Returns:
            A dict with 'integration', 'group_id', 'obligation_count',
            'obligations', and 'timestamp'.
        """
        obligations = self._judgment_bridge.exception_group_to_obligations(group_record)
        result: dict[str, object] = {
            "integration": "channels",
            "group_id": group_record.group_id,
            "obligation_count": len(obligations),
            "obligations": obligations,
            "timestamp": time.time(),
        }
        self._integration_log.append(result)
        return result

    def integrate_with_fleet(self, scope: ConcurrencyScope) -> dict[str, object]:
        """Integrate a ConcurrencyScope with the fleet orchestration subsystem.

        Registers the scope with the fleet and returns bid information.

        Args:
            scope: The ConcurrencyScope to register.

        Returns:
            The registration result dict from FleetBridge.register_scope_with_fleet.
        """
        result = self._fleet_bridge.register_scope_with_fleet(scope)
        enriched: dict[str, object] = {
            **result,
            "integration": "fleet",
            "timestamp": time.time(),
        }
        self._integration_log.append(enriched)
        return enriched

    # ── composite operations ──────────────────────────

    def full_integration_check(
        self,
        section: TaskLocalSection,
        scope: ConcurrencyScope,
    ) -> dict[str, object]:
        """Run all four subsystem integrations and return a health report.

        Exercises the support, judgment, channel, and fleet integrations in
        sequence.  Any integration that raises an exception is recorded as
        failed in the health report.

        Args:
            section: A TaskLocalSection to use for the supports integration.
            scope: A ConcurrencyScope to use for the fleet integration.

        Returns:
            A comprehensive health report dict with individual integration
            results and an overall 'all_passed' flag.
        """
        report: dict[str, object] = {"timestamp": time.time(), "integrations": {}}
        integrations_dict: dict = {}  # type: ignore[type-arg]

        # Supports
        try:
            supports_result = self.integrate_with_supports(section)
            integrations_dict["supports"] = {"status": "ok", "result": supports_result}
        except Exception as exc:
            integrations_dict["supports"] = {"status": "error", "error": str(exc)}
            self._health_status["support_bridge"] = False

        # Judgments (create a dummy CancellationRecord from the section)
        dummy_record = CancellationRecord(
            record_id=str(uuid.uuid4()),
            task_id=section.task_id or section.section_id,
            reason="health_check",
            timestamp=time.time(),
            propagated=False,
        )
        try:
            judgment_result = self.integrate_with_judgments(dummy_record)
            integrations_dict["judgments"] = {"status": "ok", "result": judgment_result}
        except Exception as exc:
            integrations_dict["judgments"] = {"status": "error", "error": str(exc)}
            self._health_status["judgment_bridge"] = False

        # Channels (create a dummy ExceptionGroupRecord)
        dummy_group = ExceptionGroupRecord(
            group_id=str(uuid.uuid4()),
            message="health_check",
            exceptions=[],
            resolved=True,
        )
        try:
            channels_result = self.integrate_with_channels(dummy_group)
            integrations_dict["channels"] = {"status": "ok", "result": channels_result}
        except Exception as exc:
            integrations_dict["channels"] = {"status": "error", "error": str(exc)}

        # Fleet
        try:
            fleet_result = self.integrate_with_fleet(scope)
            integrations_dict["fleet"] = {"status": "ok", "result": fleet_result}
        except Exception as exc:
            integrations_dict["fleet"] = {"status": "error", "error": str(exc)}
            self._health_status["fleet_bridge"] = False

        all_passed = all(
            v.get("status") == "ok" for v in integrations_dict.values()
        )
        report["integrations"] = integrations_dict
        report["all_passed"] = all_passed
        report["health_status"] = dict(self._health_status)
        self._integration_log.append({**report, "event": "full_integration_check"})
        return report

    def export_integration_state(self) -> dict[str, object]:
        """Return aggregated state from all bridges plus the integration log.

        Returns:
            A dict with 'support_bridge', 'judgment_bridge', 'fleet_bridge',
            'integration_log_count', and 'health_status'.
        """
        return {
            "support_bridge": self._support_bridge.export_bridge_state(),
            "judgment_bridge": self._judgment_bridge.export_bridge_state(),
            "fleet_bridge": self._fleet_bridge.export_bridge_state(),
            "integration_log_count": len(self._integration_log),
            "health_status": dict(self._health_status),
            "initialized": self._initialized,
        }

    def reload_integration(self) -> bool:
        """Reset and re-initialise all bridges.

        Replaces all bridge instances with fresh objects and resets the
        initialized flag.

        Returns:
            True on success.
        """
        self._support_bridge = SupportBridge()
        self._judgment_bridge = JudgmentBridge()
        self._fleet_bridge = FleetBridge()
        self._integration_log.clear()
        self._initialized = False
        self._mark_initialized()
        _log.info("ConcurrencyBoundariesIntegration: reloaded")
        return True

    def health_check(self) -> dict[str, bool]:
        """Return the current health status of all bridges.

        Returns:
            A dict with boolean flags for each bridge and the initialized state.
        """
        return {
            "support_bridge": self._health_status.get("support_bridge", True),
            "judgment_bridge": self._health_status.get("judgment_bridge", True),
            "fleet_bridge": self._health_status.get("fleet_bridge", True),
            "initialized": self._initialized,
        }


# ══════════════════════════════════════════════════════
# Public API
# ══════════════════════════════════════════════════════

__all__ = [
    "SupportBridge",
    "JudgmentBridge",
    "FleetBridge",
    "ConcurrencyBoundariesIntegration",
]

# copilot: shared-core marker for future LLM orchestration.
