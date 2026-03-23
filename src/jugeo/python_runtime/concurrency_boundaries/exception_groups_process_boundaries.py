"""Ch24 §3 — Exception Groups and Process Boundaries.

ExceptionGroup (Python 3.11+) wraps multiple concurrent failures; each exception
is an independent obstruction (cohomology class). Process boundaries are cover
boundaries in the semantic site; IPC channels are morphisms between sections
across those boundaries.

Sheaf-theoretic framing
-----------------------
Consider a task-tree whose nodes are concurrent execution units. Each node
carries a *section* of the presheaf ``F`` of local program states. When a
group of tasks fails simultaneously (e.g. inside ``asyncio.TaskGroup``), we
obtain an ``ExceptionGroup`` whose sub-exceptions are *independent obstructions*
in the Čech cohomology group ``H^1(U, F)`` — one class per failed node.

A process boundary partitions this task-tree into two or more open sets
``{U_i}`` in the semantic site. Sections in different ``U_i`` cannot be
*directly* identified; the identification requires an explicit cover morphism
(an IPC channel with its serialisation/deserialisation round-trip). The
present module enforces this discipline at runtime.

Key theorems referenced
-----------------------
* THEOREM_EXCEPTION_GROUP_MULTI_OBSTRUCTION — Ch24 §3
* THEOREM_PROCESS_BOUNDARY_COVER            — Ch24 §3
* THEOREM_IPC_MORPHISM                      — Ch24 §3
"""

from __future__ import annotations

import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any

# ══════════════════════════════════════════════════════
# Local imports — models
# ══════════════════════════════════════════════════════
try:
    from jugeo.python_runtime.concurrency_boundaries.models import (
        BoundaryKind,
        CancellationReason,
        CancellationRecord,
        ConcurrencyRole,
        ConcurrencyScope,
        ExceptionGroupRecord,
        ProcessBoundary,
        ScopeStatus,
        TaskLocalSection,
        make_cancellation_record,
        make_process_boundary,
        make_scope,
        make_task_section,
    )
except ImportError:

    class BoundaryKind:  # type: ignore[no-redef]
        PROCESS = "PROCESS"
        THREAD = "THREAD"
        COROUTINE = "COROUTINE"

        def __init__(self, value: str = "PROCESS") -> None:
            self.value = value

    class CancellationReason:  # type: ignore[no-redef]
        TIMEOUT = "TIMEOUT"
        USER_REQUESTED = "USER_REQUESTED"
        PARENT_CANCELLED = "PARENT_CANCELLED"
        RESOURCE_EXHAUSTED = "RESOURCE_EXHAUSTED"
        OBSTRUCTION = "OBSTRUCTION"
        POLICY_VIOLATION = "POLICY_VIOLATION"

        def __init__(self, value: str = "OBSTRUCTION") -> None:
            self.value = value

    class CancellationRecord:  # type: ignore[no-redef]
        def __init__(
            self,
            record_id: str = "",
            task_id: str = "",
            obstruction_key: str = "",
            reason: Any = None,
            message: str = "",
            timestamp: float = 0.0,
        ) -> None:
            self.record_id = record_id or uuid.uuid4().hex
            self.task_id = task_id
            self.obstruction_key = obstruction_key
            self.reason = reason
            self.message = message
            self.timestamp = timestamp or time.time()
            self.is_discharged: bool = False

        def discharge(self) -> None:
            self.is_discharged = True

        def to_dict(self) -> dict[str, Any]:
            return {
                "record_id": self.record_id,
                "task_id": self.task_id,
                "obstruction_key": self.obstruction_key,
                "reason": str(self.reason),
                "message": self.message,
                "timestamp": self.timestamp,
                "is_discharged": self.is_discharged,
            }

    class ConcurrencyRole:  # type: ignore[no-redef]
        ORCHESTRATOR = "ORCHESTRATOR"
        WORKER = "WORKER"

        def __init__(self, value: str = "WORKER") -> None:
            self.value = value

    class ScopeStatus:  # type: ignore[no-redef]
        ACTIVE = "ACTIVE"
        COMPLETED = "COMPLETED"
        CANCELLED = "CANCELLED"
        FAILED = "FAILED"

        def __init__(self, value: str = "ACTIVE") -> None:
            self.value = value

    class TaskLocalSection:  # type: ignore[no-redef]
        def __init__(
            self,
            section_id: str = "",
            task_id: str = "",
            support_keys: list[str] | None = None,
            provenance: dict[str, Any] | None = None,
            status: str = "ACTIVE",
            created_at: float = 0.0,
        ) -> None:
            self.section_id = section_id or uuid.uuid4().hex
            self.task_id = task_id
            self.support_keys: list[str] = support_keys or []
            self.provenance: dict[str, Any] = provenance or {}
            self.status = status
            self.created_at = created_at or time.time()

        def to_dict(self) -> dict[str, Any]:
            return {
                "section_id": self.section_id,
                "task_id": self.task_id,
                "support_keys": self.support_keys,
                "provenance": self.provenance,
                "status": self.status,
                "created_at": self.created_at,
            }

    class ExceptionGroupRecord:  # type: ignore[no-redef]
        def __init__(
            self,
            group_id: str = "",
            task_id: str = "",
            exception_records: list[dict[str, Any]] | None = None,
            obstruction_keys: list[str] | None = None,
            is_resolved: bool = False,
            resolution_strategy: str = "",
            created_at: float = 0.0,
        ) -> None:
            self.group_id = group_id or uuid.uuid4().hex
            self.task_id = task_id
            self.exception_records: list[dict[str, Any]] = exception_records or []
            self.obstruction_keys: list[str] = obstruction_keys or []
            self.is_resolved = is_resolved
            self.resolution_strategy = resolution_strategy
            self.created_at = created_at or time.time()

        def resolve(self, strategy: str) -> None:
            self.is_resolved = True
            self.resolution_strategy = strategy

        def to_dict(self) -> dict[str, Any]:
            return {
                "group_id": self.group_id,
                "task_id": self.task_id,
                "exception_records": self.exception_records,
                "obstruction_keys": self.obstruction_keys,
                "is_resolved": self.is_resolved,
                "resolution_strategy": self.resolution_strategy,
                "created_at": self.created_at,
            }

    class ProcessBoundary:  # type: ignore[no-redef]
        def __init__(
            self,
            boundary_id: str = "",
            source_process_id: str = "",
            target_process_id: str = "",
            kind: Any = None,
            allowed_section_ids: list[str] | None = None,
            cover_morphism_id: str = "",
            is_active: bool = True,
        ) -> None:
            self.boundary_id = boundary_id or uuid.uuid4().hex
            self.source_process_id = source_process_id
            self.target_process_id = target_process_id
            self.kind = kind
            self.allowed_section_ids: list[str] = allowed_section_ids or []
            self.cover_morphism_id = cover_morphism_id
            self.is_active = is_active

        def permits_crossing(self, section_id: str) -> bool:
            return section_id in self.allowed_section_ids

        def to_dict(self) -> dict[str, Any]:
            return {
                "boundary_id": self.boundary_id,
                "source_process_id": self.source_process_id,
                "target_process_id": self.target_process_id,
                "kind": str(self.kind),
                "allowed_section_ids": self.allowed_section_ids,
                "cover_morphism_id": self.cover_morphism_id,
                "is_active": self.is_active,
            }

    class ConcurrencyScope:  # type: ignore[no-redef]
        def __init__(
            self,
            scope_id: str = "",
            sections: list[TaskLocalSection] | None = None,
            status: str = "ACTIVE",
            child_scope_ids: list[str] | None = None,
            scope_depth_value: int = 0,
        ) -> None:
            self.scope_id = scope_id or uuid.uuid4().hex
            self.sections: list[TaskLocalSection] = sections or []
            self.status = status
            self.child_scope_ids: list[str] = child_scope_ids or []
            self._depth = scope_depth_value

        def scope_depth(self) -> int:
            return self._depth

        def to_dict(self) -> dict[str, Any]:
            return {
                "scope_id": self.scope_id,
                "sections": [s.to_dict() for s in self.sections],
                "status": self.status,
                "child_scope_ids": self.child_scope_ids,
            }

    def make_task_section(task_id: str, support_keys: list[str] | None = None) -> TaskLocalSection:  # type: ignore[no-redef]
        return TaskLocalSection(section_id=uuid.uuid4().hex, task_id=task_id, support_keys=support_keys or [])

    def make_cancellation_record(task_id: str, obstruction_key: str, reason: Any = None, message: str = "") -> CancellationRecord:  # type: ignore[no-redef]
        return CancellationRecord(record_id=uuid.uuid4().hex, task_id=task_id, obstruction_key=obstruction_key, reason=reason, message=message)

    def make_process_boundary(source: str, target: str, allowed_sections: list[str] | None = None) -> ProcessBoundary:  # type: ignore[no-redef]
        return ProcessBoundary(source_process_id=source, target_process_id=target, allowed_section_ids=allowed_sections or [])

    def make_scope(scope_id: str | None = None) -> ConcurrencyScope:  # type: ignore[no-redef]
        return ConcurrencyScope(scope_id=scope_id or uuid.uuid4().hex)


# ══════════════════════════════════════════════════════
# Cross-package imports — geometry
# ══════════════════════════════════════════════════════
try:
    from jugeo.geometry.supports import SupportRegion, SupportSet, SupportTracker
except ImportError:

    class SupportRegion:  # type: ignore[no-redef]
        def __init__(self, region_id: str = "", keys: list[str] | None = None) -> None:
            self.region_id = region_id or uuid.uuid4().hex
            self.keys: list[str] = keys or []

        def contains(self, key: str) -> bool:
            return key in self.keys

    class SupportSet:  # type: ignore[no-redef]
        def __init__(self, elements: list[str] | None = None) -> None:
            self.elements: list[str] = elements or []

        def intersects(self, other: "SupportSet") -> bool:
            return bool(set(self.elements) & set(other.elements))

    class SupportTracker:  # type: ignore[no-redef]
        def __init__(self) -> None:
            self._tracked: dict[str, SupportRegion] = {}

        def track(self, region: SupportRegion) -> None:
            self._tracked[region.region_id] = region

        def get(self, region_id: str) -> SupportRegion | None:
            return self._tracked.get(region_id)


# ══════════════════════════════════════════════════════
# Cross-package imports — judgments
# ══════════════════════════════════════════════════════
try:
    from jugeo.judgments.judgment_terms import JudgmentStatus, LocalJudgment, TrustLevel
except ImportError:

    class LocalJudgment:  # type: ignore[no-redef]
        def __init__(self, judgment_id: str = "", value: Any = None) -> None:
            self.judgment_id = judgment_id or uuid.uuid4().hex
            self.value = value

        def is_valid(self) -> bool:
            return self.value is not None

    class JudgmentStatus:  # type: ignore[no-redef]
        PENDING = "PENDING"
        CONFIRMED = "CONFIRMED"
        REJECTED = "REJECTED"

        def __init__(self, value: str = "PENDING") -> None:
            self.value = value

    class TrustLevel:  # type: ignore[no-redef]
        LOW = "LOW"
        MEDIUM = "MEDIUM"
        HIGH = "HIGH"

        def __init__(self, value: str = "MEDIUM") -> None:
            self.value = value


# ══════════════════════════════════════════════════════
# Cross-package imports — evidence
# ══════════════════════════════════════════════════════
try:
    from jugeo.evidence.channels import ChannelRouter, EvidenceChannel, EvidenceRecord
except ImportError:

    class EvidenceChannel:  # type: ignore[no-redef]
        def __init__(self, channel_id: str = "", kind: str = "pipe") -> None:
            self.channel_id = channel_id or uuid.uuid4().hex
            self.kind = kind

        def is_open(self) -> bool:
            return True

    class EvidenceRecord:  # type: ignore[no-redef]
        def __init__(self, record_id: str = "", payload: Any = None) -> None:
            self.record_id = record_id or uuid.uuid4().hex
            self.payload = payload

        def to_dict(self) -> dict[str, Any]:
            return {"record_id": self.record_id, "payload": self.payload}

    class ChannelRouter:  # type: ignore[no-redef]
        def __init__(self) -> None:
            self._routes: dict[str, EvidenceChannel] = {}

        def add_route(self, key: str, channel: EvidenceChannel) -> None:
            self._routes[key] = channel

        def route(self, key: str) -> EvidenceChannel | None:
            return self._routes.get(key)


# ── compatibility helpers ──────────────────────────────────────────────────


def _section_status(section: TaskLocalSection) -> str:
    """Return section status as an uppercase string, compatible with both real and stub models."""
    val = getattr(section, "scope_status", None)
    if val is None:
        val = getattr(section, "status", "")
    return str(val).upper().split(".")[-1]


def _scope_sections(scope: ConcurrencyScope) -> list[TaskLocalSection]:
    """Return sections as a list, compatible with dict-sections (real) or list-sections (stub)."""
    sections = scope.sections
    if isinstance(sections, dict):
        return list(sections.values())
    return list(sections)  # type: ignore[arg-type]


def _provenance_get(section: TaskLocalSection, key: str) -> Any:
    """Safely retrieve a key from provenance, handling dict or tuple provenance."""
    prov = getattr(section, "provenance", None)
    if isinstance(prov, dict):
        return prov.get(key)
    return None


# ══════════════════════════════════════════════════════
# CLASS: ExceptionGroupProcessor
# ══════════════════════════════════════════════════════


class ExceptionGroupProcessor:
    """Processes ExceptionGroup instances as multi-obstruction records.

    Python 3.11 introduced ExceptionGroup to represent multiple concurrent
    exceptions. In the sheaf-theoretic model, each exception in the group is
    an independent obstruction (cohomology class) at its task's coordinate.
    The ExceptionGroupProcessor decomposes groups into individual obstructions,
    filters them by type, and tracks their resolution status.

    Theory reference: Ch24 §3 — THEOREM_EXCEPTION_GROUP_MULTI_OBSTRUCTION.
    """

    def __init__(self) -> None:
        self._groups: dict[str, ExceptionGroupRecord] = {}
        self._processing_log: list[dict[str, Any]] = []
        self._resolution_strategies: dict[str, str] = {}
        self._processed_count: int = 0

    # ── core registration ──────────────────────────────

    def register_group(
        self,
        group_id: str,
        task_id: str,
        exceptions: list[dict[str, Any]],
    ) -> ExceptionGroupRecord:
        """Create and register an ExceptionGroupRecord from raw exception dicts.

        Args:
            group_id: Stable identifier for this exception group.
            task_id: The task whose TaskGroup produced the group.
            exceptions: List of dicts, each with at minimum keys ``type``,
                ``message``, and ``obstruction_key``.

        Returns:
            The newly created and stored ExceptionGroupRecord.

        Raises:
            ValueError: If *exceptions* is empty.
        """
        if not exceptions:
            raise ValueError(f"Cannot register empty exception list for group_id={group_id!r}")

        exception_records: list[dict[str, Any]] = []
        obstruction_keys: list[str] = []

        for exc in exceptions:
            exc_type = exc.get("type", "UnknownError")
            message = exc.get("message", "")
            obstruction_key = exc.get("obstruction_key", f"obs:{group_id}:{exc_type}")

            record: dict[str, Any] = {
                "type": exc_type,
                "message": message,
                "obstruction_key": obstruction_key,
                "raw": exc,
                "registered_at": time.time(),
            }
            exception_records.append(record)
            if obstruction_key not in obstruction_keys:
                obstruction_keys.append(obstruction_key)

        group = ExceptionGroupRecord(
            group_id=group_id,
            task_id=task_id,
            exception_records=exception_records,
            obstruction_keys=obstruction_keys,
            created_at=time.time(),
        )
        self._groups[group_id] = group
        self._processed_count += 1

        self._processing_log.append(
            {
                "event": "register_group",
                "group_id": group_id,
                "task_id": task_id,
                "exception_count": len(exceptions),
                "timestamp": time.time(),
            }
        )
        return group

    # ── analysis helpers ───────────────────────────────

    def split_by_type(self, group_id: str) -> dict[str, list[dict[str, Any]]]:
        """Return a mapping from exception type string to list of exception dicts.

        Args:
            group_id: The group to split.

        Returns:
            Dict of ``{exc_type: [exc_dict, ...]}`` for every exception in the
            group.  Returns an empty dict if the group is not found.
        """
        group = self._groups.get(group_id)
        if group is None:
            return {}

        by_type: dict[str, list[dict[str, Any]]] = {}
        for record in group.exception_records:
            exc_type = record.get("type", "UnknownError")
            by_type.setdefault(exc_type, []).append(record)
        return by_type

    def filter_obstructions(self, group_id: str, obstruction_pattern: str) -> list[str]:
        """Return obstruction keys that contain *obstruction_pattern* as a substring.

        Args:
            group_id: The group to filter.
            obstruction_pattern: Substring pattern to match against keys.

        Returns:
            List of matching obstruction key strings.  Empty list if not found.
        """
        group = self._groups.get(group_id)
        if group is None:
            return []

        return [
            key
            for key in group.obstruction_keys
            if obstruction_pattern in key
        ]

    def merge_groups(self, group_ids: list[str]) -> ExceptionGroupRecord | None:
        """Merge multiple ExceptionGroupRecords into a single new record.

        The merged group receives a fresh UUID as its group_id and inherits all
        exception records and obstruction keys from the source groups (deduped
        on obstruction_key).

        Args:
            group_ids: Identifiers of groups to merge.

        Returns:
            A new ExceptionGroupRecord containing the union of all records, or
            ``None`` if none of the given IDs are found.
        """
        found = [self._groups[gid] for gid in group_ids if gid in self._groups]
        if not found:
            return None

        merged_id = uuid.uuid4().hex
        merged_exceptions: list[dict[str, Any]] = []
        merged_obstruction_keys: list[str] = []
        seen_keys: set[str] = set()

        for grp in found:
            for rec in grp.exception_records:
                merged_exceptions.append(rec)
                okey = rec.get("obstruction_key", "")
                if okey and okey not in seen_keys:
                    merged_obstruction_keys.append(okey)
                    seen_keys.add(okey)

        merged = ExceptionGroupRecord(
            group_id=merged_id,
            task_id=found[0].task_id,
            exception_records=merged_exceptions,
            obstruction_keys=merged_obstruction_keys,
            created_at=time.time(),
        )
        self._groups[merged_id] = merged
        self._processing_log.append(
            {
                "event": "merge_groups",
                "source_ids": group_ids,
                "merged_id": merged_id,
                "total_exceptions": len(merged_exceptions),
                "timestamp": time.time(),
            }
        )
        return merged

    def flatten_group(self, group_id: str) -> list[dict[str, Any]]:
        """Return a fully-flattened list of exception dicts for a group.

        Handles nested 'sub_group' entries using a BFS traversal, so arbitrarily
        deep nesting is resolved without recursion stack overflow.

        Args:
            group_id: The group to flatten.

        Returns:
            Flat list of exception dicts.  Empty list if group not found.
        """
        group = self._groups.get(group_id)
        if group is None:
            return []

        result: list[dict[str, Any]] = []
        queue: deque[dict[str, Any]] = deque(group.exception_records)

        while queue:
            item = queue.popleft()
            sub = item.get("sub_group")
            if sub and isinstance(sub, list):
                # nested exceptions — enqueue children, skip the wrapper
                for child in sub:
                    if isinstance(child, dict):
                        queue.append(child)
            else:
                result.append(item)
        return result

    def obstruction_summary(self, group_id: str) -> dict[str, Any]:
        """Return a structured summary of the obstruction landscape for a group.

        Args:
            group_id: The group to summarise.

        Returns:
            Dict with keys: ``group_id``, ``total_exceptions``,
            ``total_obstructions``, ``by_type``, ``is_resolved``,
            ``resolution_strategy``.
        """
        group = self._groups.get(group_id)
        if group is None:
            return {
                "group_id": group_id,
                "total_exceptions": 0,
                "total_obstructions": 0,
                "by_type": {},
                "is_resolved": False,
                "resolution_strategy": None,
                "error": "group_not_found",
            }

        by_type = self.split_by_type(group_id)
        return {
            "group_id": group_id,
            "total_exceptions": len(group.exception_records),
            "total_obstructions": len(group.obstruction_keys),
            "by_type": {k: len(v) for k, v in by_type.items()},
            "is_resolved": group.is_resolved,
            "resolution_strategy": self._resolution_strategies.get(group_id, group.resolution_strategy),
        }

    # ── resolution ─────────────────────────────────────

    def resolve_group(self, group_id: str, strategy: str) -> bool:
        """Resolve a group using the specified strategy.

        Args:
            group_id: The group to resolve.
            strategy: Human-readable strategy name (e.g., ``"retry"``, ``"abort"``).

        Returns:
            ``True`` if the group was found and resolved, ``False`` otherwise.
        """
        group = self._groups.get(group_id)
        if group is None:
            return False

        group.resolve(strategy)
        self._resolution_strategies[group_id] = strategy
        self._processing_log.append(
            {
                "event": "resolve_group",
                "group_id": group_id,
                "strategy": strategy,
                "timestamp": time.time(),
            }
        )
        return True

    # ── export & reporting ─────────────────────────────

    def export_groups(self) -> dict[str, dict[str, Any]]:
        """Return a dict of ``{group_id: to_dict()}`` for all registered groups.

        Returns:
            Serialisable snapshot of the processor's group registry.
        """
        return {gid: grp.to_dict() for gid, grp in self._groups.items()}

    def unresolved_groups(self) -> list[ExceptionGroupRecord]:
        """Return all registered groups whose ``is_resolved`` flag is ``False``.

        Returns:
            List of unresolved ExceptionGroupRecord instances.
        """
        return [grp for grp in self._groups.values() if not grp.is_resolved]

    def group_count(self) -> int:
        """Return the total number of registered groups.

        Returns:
            Integer count.
        """
        return len(self._groups)


# ══════════════════════════════════════════════════════
# CLASS: MultiObstructionRecord
# ══════════════════════════════════════════════════════


class MultiObstructionRecord:
    """A record tracking multiple concurrent obstructions from an ExceptionGroup.

    When an ExceptionGroup arises, we need to track not just the individual
    exceptions but also their relationships: are they independent, do they share
    a common cause, do they conflict? The MultiObstructionRecord models these
    relationships as a small graph over obstruction keys.

    Theory reference: Ch24 §3 — multi-obstruction records as cohomology classes.
    """

    def __init__(self, record_id: str | None = None) -> None:
        self._record_id: str = record_id or uuid.uuid4().hex
        self._obstructions: dict[str, dict[str, Any]] = {}
        self._relationships: list[tuple[str, str, str]] = []
        self._common_causes: dict[str, list[str]] = {}
        self._created_at: float = time.time()

    # ── construction ───────────────────────────────────

    def add_obstruction(
        self,
        key: str,
        exc_type: str,
        message: str,
        task_id: str,
    ) -> None:
        """Register a single obstruction in this record.

        Args:
            key: Unique obstruction key, e.g. ``"obs:task-42:TimeoutError"``.
            exc_type: Python exception type name.
            message: Human-readable error message.
            task_id: The task that produced this obstruction.
        """
        self._obstructions[key] = {
            "key": key,
            "exc_type": exc_type,
            "message": message,
            "task_id": task_id,
            "added_at": time.time(),
        }

    def add_relationship(self, key1: str, key2: str, relation: str) -> None:
        """Declare a relationship between two obstruction keys.

        Args:
            key1: First obstruction key.
            key2: Second obstruction key.
            relation: One of ``'independent'``, ``'caused_by'``,
                ``'conflicts_with'``.

        Raises:
            ValueError: If *relation* is not one of the accepted values.
        """
        valid_relations = {"independent", "caused_by", "conflicts_with"}
        if relation not in valid_relations:
            raise ValueError(
                f"Unknown relation {relation!r}. Must be one of {valid_relations}."
            )
        self._relationships.append((key1, key2, relation))

    def add_common_cause(self, cause_key: str, obstruction_keys: list[str]) -> None:
        """Record that *cause_key* is a common cause of multiple obstructions.

        Args:
            cause_key: Identifier for the common cause (may itself be an
                obstruction key or an external root-cause descriptor).
            obstruction_keys: List of keys that share this common cause.
        """
        existing = self._common_causes.get(cause_key, [])
        for okey in obstruction_keys:
            if okey not in existing:
                existing.append(okey)
        self._common_causes[cause_key] = existing

    # ── queries ────────────────────────────────────────

    def are_independent(self, key1: str, key2: str) -> bool:
        """Return ``True`` if *key1* and *key2* are independent obstructions.

        Two keys are independent when no relationship entry connects them OR
        when the only relationship entry between them has type 'independent'.

        Args:
            key1: First obstruction key.
            key2: Second obstruction key.

        Returns:
            Boolean independence result.
        """
        for k1, k2, relation in self._relationships:
            if (k1 == key1 and k2 == key2) or (k1 == key2 and k2 == key1):
                return relation == "independent"
        # No relationship declared → default to independent
        return True

    def get_related(self, key: str) -> list[tuple[str, str]]:
        """Return all obstructions related to *key*.

        Args:
            key: Obstruction key to look up.

        Returns:
            List of ``(other_key, relation)`` tuples for every relationship
            involving *key*.
        """
        result: list[tuple[str, str]] = []
        for k1, k2, relation in self._relationships:
            if k1 == key:
                result.append((k2, relation))
            elif k2 == key:
                result.append((k1, relation))
        return result

    def common_cause_keys(self, obstruction_key: str) -> list[str]:
        """Return all cause keys that list *obstruction_key* as an effect.

        Args:
            obstruction_key: The obstruction to look up.

        Returns:
            List of cause key strings.
        """
        return [
            cause_key
            for cause_key, effects in self._common_causes.items()
            if obstruction_key in effects
        ]

    def obstruction_count(self) -> int:
        """Return the total number of registered obstructions.

        Returns:
            Integer count.
        """
        return len(self._obstructions)

    # ── serialisation ──────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """Serialise this record to a plain dict.

        Returns:
            Dict with ``record_id``, ``obstructions``, ``relationships``,
            ``common_causes``, ``created_at``.
        """
        return {
            "record_id": self._record_id,
            "obstructions": dict(self._obstructions),
            "relationships": [
                {"key1": k1, "key2": k2, "relation": rel}
                for k1, k2, rel in self._relationships
            ],
            "common_causes": dict(self._common_causes),
            "created_at": self._created_at,
        }

    def cohomology_class_repr(self) -> str:
        """Return a string representation of this record as a cohomology class.

        The format ``H^1({key1, key2, ...})`` is purely notational, matching the
        Čech cohomology framing in Ch24 §3.

        Returns:
            String like ``"H^1({obs:task1:Timeout, obs:task2:OSError})"``.
        """
        keys_repr = ", ".join(sorted(self._obstructions.keys()))
        return f"H^1({{{keys_repr}}})"


# ══════════════════════════════════════════════════════
# CLASS: ProcessBoundaryEnforcer
# ══════════════════════════════════════════════════════


class ProcessBoundaryEnforcer:
    """Enforces process boundary conditions in the semantic site.

    A process boundary is a cover of the semantic site. Sections cannot cross
    a process boundary without an explicit cover morphism. The enforcer maintains
    a registry of active boundaries, validates crossing requests, and records
    violations.

    Theory reference: Ch24 §3 — THEOREM_PROCESS_BOUNDARY_COVER.
    """

    def __init__(self) -> None:
        self._boundaries: dict[str, ProcessBoundary] = {}
        self._violation_log: list[dict[str, Any]] = []
        self._crossing_log: list[dict[str, Any]] = []
        self._violation_count: int = 0

    # ── registry ───────────────────────────────────────

    def register_boundary(self, boundary: ProcessBoundary) -> None:
        """Add a ProcessBoundary to the enforcer's registry.

        Args:
            boundary: The boundary to register.  Its ``boundary_id`` is used
                as the key.
        """
        self._boundaries[boundary.boundary_id] = boundary

    def deregister_boundary(self, boundary_id: str) -> bool:
        """Remove a boundary from the registry.

        Args:
            boundary_id: The boundary to remove.

        Returns:
            ``True`` if the boundary existed and was removed, ``False`` otherwise.
        """
        if boundary_id in self._boundaries:
            del self._boundaries[boundary_id]
            return True
        return False

    def update_boundary(self, boundary_id: str, new_boundary: ProcessBoundary) -> bool:
        """Replace an existing boundary entry.

        Args:
            boundary_id: The boundary to replace.
            new_boundary: The replacement ProcessBoundary object.

        Returns:
            ``True`` if the original boundary existed, ``False`` otherwise.
        """
        existed = boundary_id in self._boundaries
        self._boundaries[boundary_id] = new_boundary
        return existed

    def find_boundary(self, source_id: str, target_id: str) -> ProcessBoundary | None:
        """Find the first boundary that connects *source_id* to *target_id*.

        Args:
            source_id: Source process identifier.
            target_id: Target process identifier.

        Returns:
            Matching ProcessBoundary or ``None`` if no match is found.
        """
        for boundary in self._boundaries.values():
            if (
                boundary.source_process_id == source_id
                and boundary.target_process_id == target_id
            ):
                return boundary
        return None

    def active_boundaries(self) -> list[ProcessBoundary]:
        """Return all boundaries whose ``is_active`` flag is ``True``.

        Returns:
            List of active ProcessBoundary instances.
        """
        return [b for b in self._boundaries.values() if b.is_active]

    # ── crossing checks ────────────────────────────────

    def check_crossing(
        self,
        section_id: str,
        source_process_id: str,
        target_process_id: str,
    ) -> dict[str, Any]:
        """Check whether a section is permitted to cross a process boundary.

        Locates the boundary connecting *source_process_id* to
        *target_process_id* and queries it for the given *section_id*.

        Args:
            section_id: Identifier of the section attempting to cross.
            source_process_id: Origin process.
            target_process_id: Destination process.

        Returns:
            Dict with ``allowed`` (bool) and either ``boundary_id`` and
            ``cover_morphism_id`` (when allowed) or ``reason`` and
            ``violation_key`` (when denied).
        """
        boundary = self.find_boundary(source_process_id, target_process_id)
        if boundary is None:
            violation_key = f"violation:{source_process_id}:{target_process_id}:{section_id}"
            return {
                "allowed": False,
                "reason": "no_boundary_registered",
                "violation_key": violation_key,
                "section_id": section_id,
            }

        if not boundary.is_active:
            violation_key = f"violation:{source_process_id}:{target_process_id}:{section_id}"
            return {
                "allowed": False,
                "reason": "boundary_inactive",
                "violation_key": violation_key,
                "section_id": section_id,
            }

        if boundary.permits_crossing(section_id):
            self._crossing_log.append(
                {
                    "event": "crossing_allowed",
                    "section_id": section_id,
                    "source": source_process_id,
                    "target": target_process_id,
                    "boundary_id": boundary.boundary_id,
                    "timestamp": time.time(),
                }
            )
            return {
                "allowed": True,
                "boundary_id": boundary.boundary_id,
                "cover_morphism_id": boundary.cover_morphism_id,
                "section_id": section_id,
            }

        violation_key = f"violation:{source_process_id}:{target_process_id}:{section_id}"
        return {
            "allowed": False,
            "reason": "section_not_in_allowed_list",
            "violation_key": violation_key,
            "section_id": section_id,
        }

    def enforce_boundary(
        self,
        section: TaskLocalSection,
        target_process_id: str,
        source_process_id: str = "local",
    ) -> dict[str, Any]:
        """Enforce boundary rules for a section attempting to cross processes.

        Calls :meth:`check_crossing` and, if crossing is denied, records the
        violation in *_violation_log* and increments *_violation_count*.

        Args:
            section: The TaskLocalSection attempting to cross.
            target_process_id: Destination process.
            source_process_id: Origin process (defaults to ``"local"``).

        Returns:
            The result dict from :meth:`check_crossing` augmented with
            ``enforced_at`` timestamp.
        """
        result = self.check_crossing(
            section.section_id,
            source_process_id,
            target_process_id,
        )

        if not result.get("allowed", False):
            violation_key = result.get("violation_key", "violation:unknown")
            self.record_violation(
                section.section_id,
                source_process_id,
                target_process_id,
                violation_key,
            )
            self._violation_count += 1

        result["enforced_at"] = time.time()
        return result

    def record_violation(
        self,
        section_id: str,
        source_id: str,
        target_id: str,
        violation_key: str,
    ) -> None:
        """Append a detailed violation record to the violation log.

        Args:
            section_id: The section that attempted the illegal crossing.
            source_id: Origin process.
            target_id: Destination process.
            violation_key: Unique key identifying this violation.
        """
        self._violation_log.append(
            {
                "violation_key": violation_key,
                "section_id": section_id,
                "source_process_id": source_id,
                "target_process_id": target_id,
                "total_violations_at_time": self._violation_count + 1,
                "timestamp": time.time(),
            }
        )

    def boundary_report(self) -> dict[str, Any]:
        """Return a summary report of all boundaries and recent violations.

        Returns:
            Dict with ``total_boundaries``, ``active_boundaries``,
            ``total_violations``, ``recent_violations`` (last 10).
        """
        return {
            "total_boundaries": len(self._boundaries),
            "active_boundaries": len(self.active_boundaries()),
            "total_violations": self._violation_count,
            "total_crossings": len(self._crossing_log),
            "recent_violations": self._violation_log[-10:],
            "recent_crossings": self._crossing_log[-10:],
        }


# ══════════════════════════════════════════════════════
# CLASS: IPCMorphismBuilder
# ══════════════════════════════════════════════════════


class IPCMorphismBuilder:
    """Builds cover morphisms for IPC channels between process boundaries.

    IPC channels (pipes, sockets, queues) are morphisms in the semantic site
    between sections across a process boundary cover. The IPCMorphismBuilder
    constructs these morphisms with proper provenance, validates that they
    respect boundary conditions, and manages the morphism registry.

    Theory reference: Ch24 §3 — THEOREM_IPC_MORPHISM.
    """

    def __init__(self) -> None:
        self._morphisms: dict[str, dict[str, Any]] = {}
        self._build_log: list[dict[str, Any]] = []
        self._validation_log: list[dict[str, Any]] = []

    # ── building ───────────────────────────────────────

    def build_morphism(
        self,
        source_process_id: str,
        target_process_id: str,
        channel_kind: str,
        source_section_id: str,
        target_section_id: str,
    ) -> dict[str, Any]:
        """Construct and register a cover morphism for an IPC channel.

        The morphism captures full provenance: which processes are connected,
        what channel kind is used, and the mapping between the source and target
        section IDs.

        Args:
            source_process_id: Origin process identifier.
            target_process_id: Destination process identifier.
            channel_kind: IPC mechanism (e.g. ``"pipe"``, ``"socket"``,
                ``"queue"``).
            source_section_id: Section ID on the source side.
            target_section_id: Section ID on the target side.

        Returns:
            Dict representing the morphism, keyed by ``morphism_id``.
        """
        morphism_id = uuid.uuid4().hex
        morphism: dict[str, Any] = {
            "morphism_id": morphism_id,
            "source_process_id": source_process_id,
            "target_process_id": target_process_id,
            "channel_kind": channel_kind,
            "section_mapping": {
                "source": source_section_id,
                "target": target_section_id,
            },
            "created_at": time.time(),
            "provenance": {
                "builder": "IPCMorphismBuilder",
                "channel_kind": channel_kind,
                "source_process": source_process_id,
                "target_process": target_process_id,
                "theory_ref": "Ch24_§3_THEOREM_IPC_MORPHISM",
            },
        }
        self._morphisms[morphism_id] = morphism
        self._build_log.append(
            {
                "event": "build_morphism",
                "morphism_id": morphism_id,
                "source": source_process_id,
                "target": target_process_id,
                "channel_kind": channel_kind,
                "timestamp": time.time(),
            }
        )
        return morphism

    # ── validation ─────────────────────────────────────

    def validate_morphism(
        self,
        morphism_id: str,
        boundary: ProcessBoundary,
    ) -> dict[str, Any]:
        """Validate that a registered morphism is consistent with a boundary.

        Checks that the morphism's source and target process IDs match those of
        the boundary, and that the boundary is active.

        Args:
            morphism_id: The morphism to validate.
            boundary: The ProcessBoundary against which to validate.

        Returns:
            Dict with ``valid`` (bool), ``morphism_id``, and ``issues`` (list
            of human-readable strings describing any problems found).
        """
        morphism = self._morphisms.get(morphism_id)
        issues: list[str] = []

        if morphism is None:
            issues.append(f"morphism_id={morphism_id!r} not found in registry")
            result: dict[str, Any] = {
                "valid": False,
                "morphism_id": morphism_id,
                "issues": issues,
            }
            self._validation_log.append(result | {"timestamp": time.time()})
            return result

        if morphism["source_process_id"] != boundary.source_process_id:
            issues.append(
                f"source mismatch: morphism has {morphism['source_process_id']!r}, "
                f"boundary expects {boundary.source_process_id!r}"
            )
        if morphism["target_process_id"] != boundary.target_process_id:
            issues.append(
                f"target mismatch: morphism has {morphism['target_process_id']!r}, "
                f"boundary expects {boundary.target_process_id!r}"
            )
        if not boundary.is_active:
            issues.append(f"boundary {boundary.boundary_id!r} is not active")

        result = {
            "valid": len(issues) == 0,
            "morphism_id": morphism_id,
            "issues": issues,
        }
        self._validation_log.append(result | {"timestamp": time.time()})
        return result

    def validate_ipc(
        self,
        channel_kind: str,
        source_section: TaskLocalSection,
        target_section: TaskLocalSection,
    ) -> dict[str, Any]:
        """Check that source and target sections are compatible for IPC transport.

        Compatibility requires overlapping support keys (ensuring that both
        sections reference at least some shared data domain) and compatible
        provenance metadata.

        Args:
            channel_kind: IPC channel type being used.
            source_section: Section on the sending side.
            target_section: Section on the receiving side.

        Returns:
            Dict with ``compatible`` (bool), ``channel_kind``, and ``issues``
            (list of compatibility problems).
        """
        issues: list[str] = []

        source_keys = set(source_section.support_keys)
        target_keys = set(target_section.support_keys)
        if not source_keys & target_keys:
            issues.append(
                "no overlapping support_keys between source and target sections"
            )

        src_schema = _provenance_get(source_section, "schema_version")
        tgt_schema = _provenance_get(target_section, "schema_version")
        if src_schema and tgt_schema and src_schema != tgt_schema:
            issues.append(
                f"schema_version mismatch: source={src_schema!r}, target={tgt_schema!r}"
            )

        valid_channel_kinds = {"pipe", "socket", "queue", "shared_memory", "rpc"}
        if channel_kind not in valid_channel_kinds:
            issues.append(
                f"unrecognised channel_kind={channel_kind!r}; "
                f"known kinds: {valid_channel_kinds}"
            )

        return {
            "compatible": len(issues) == 0,
            "channel_kind": channel_kind,
            "source_section_id": source_section.section_id,
            "target_section_id": target_section.section_id,
            "issues": issues,
        }

    # ── query & export ─────────────────────────────────

    def get_morphism(self, morphism_id: str) -> dict[str, Any] | None:
        """Retrieve a morphism by ID.

        Args:
            morphism_id: The morphism to retrieve.

        Returns:
            Morphism dict or ``None`` if not found.
        """
        return self._morphisms.get(morphism_id)

    def morphisms_for_process(self, process_id: str) -> list[dict[str, Any]]:
        """Return all morphisms where *process_id* is source or target.

        Args:
            process_id: Process identifier to look up.

        Returns:
            List of matching morphism dicts.
        """
        return [
            m
            for m in self._morphisms.values()
            if m["source_process_id"] == process_id
            or m["target_process_id"] == process_id
        ]

    def export_morphisms(self) -> dict[str, dict[str, Any]]:
        """Return a shallow copy of the morphism registry.

        Returns:
            Dict of ``{morphism_id: morphism_dict}``.
        """
        return dict(self._morphisms)

    def morphism_count(self) -> int:
        """Return the number of registered morphisms.

        Returns:
            Integer count.
        """
        return len(self._morphisms)

    def build_report(self) -> dict[str, Any]:
        """Return a summary of all morphisms built and validation outcomes.

        Returns:
            Dict with ``total_morphisms``, ``total_validations``,
            ``by_channel_kind``, ``recent_builds``.
        """
        by_kind: dict[str, int] = {}
        for m in self._morphisms.values():
            kind = m.get("channel_kind", "unknown")
            by_kind[kind] = by_kind.get(kind, 0) + 1

        return {
            "total_morphisms": len(self._morphisms),
            "total_validations": len(self._validation_log),
            "by_channel_kind": by_kind,
            "recent_builds": self._build_log[-10:],
            "recent_validations": self._validation_log[-5:],
        }


# ══════════════════════════════════════════════════════
# Module exports
# ══════════════════════════════════════════════════════
__all__ = [
    "ExceptionGroupProcessor",
    "MultiObstructionRecord",
    "ProcessBoundaryEnforcer",
    "IPCMorphismBuilder",
]

# copilot: shared-core marker for future LLM orchestration.
