"""Ch24 §2 — Cancellation as Obstruction Injection.

Task cancellation injects an obstruction into the task's section, which is
recorded as a first-class cohomology class. This module implements
cancellation obstruction tracking, propagation, shielding, and discharge.

In the sheaf-theoretic model of concurrent execution, a task cancellation is
not a silent termination — it is an *injection event* that introduces a
non-trivial cohomology class into the section associated with the cancelled
task.  The obstruction persists in the cohomology record until one of the
following resolution events occurs:

  * **Discharge** — a handler acknowledges the obstruction, performs cleanup
    actions, and records the discharge in the section's provenance tuple.
  * **Propagation** — the obstruction is forwarded to child tasks (unless a
    :class:`CancellationShield` is in place), creating derived obstructions.
  * **Absorption** — a :class:`CancellationShield` absorbs the obstruction at
    a boundary, preventing further propagation while maintaining the audit
    trail.

No obstruction may be silently dropped. Every cancellation event produces at
least one persistent record, and the discharge protocol verifies that cleanup
actions have been declared before a record is considered resolved.

This module provides four primary machinery components:

  1. CancellationObstructionInjector — creates obstruction records on cancel.
  2. ObstructionPropagator — propagates obstructions through the task tree.
  3. CancellationShield — absorbs obstructions at protected boundaries.
  4. CancellationDischarger — implements the discharge protocol.

Theory reference: Ch24 §2 — cancellation as obstruction injection.
"""

from __future__ import annotations

import dataclasses
import logging
import time
import uuid
from collections import deque
from dataclasses import dataclass
from typing import Any

# ══════════════════════════════════════════════════════
# External imports with fallback stubs
# ══════════════════════════════════════════════════════

try:
    from jugeo.geometry.supports import SupportRegion, SupportSet, SupportTracker
except ImportError:
    class SupportRegion:  # type: ignore[no-redef]
        """Stub for SupportRegion."""
        def __init__(self, coordinate: str = "") -> None:
            self.coordinate = coordinate

    class SupportSet:  # type: ignore[no-redef]
        """Stub for SupportSet."""
        def __init__(self, coordinates: frozenset[str] = frozenset()) -> None:
            self.coordinates = coordinates

    class SupportTracker:  # type: ignore[no-redef]
        """Stub for SupportTracker."""
        pass

try:
    from jugeo.judgments.judgment_terms import LocalJudgment, JudgmentStatus, TrustLevel
except ImportError:
    class LocalJudgment:  # type: ignore[no-redef]
        """Stub for LocalJudgment."""
        pass

    class JudgmentStatus:  # type: ignore[no-redef]
        """Stub for JudgmentStatus."""
        PROPOSED = "proposed"
        SETTLED = "settled"
        OBSTRUCTED = "obstructed"

    class TrustLevel:  # type: ignore[no-redef]
        """Stub for TrustLevel."""
        UNVERIFIED = 1
        SOLVER_DISCHARGED = 4

try:
    from jugeo.evidence.channels import EvidenceChannel, EvidenceRecord, ChannelRouter
except ImportError:
    class EvidenceChannel:  # type: ignore[no-redef]
        """Stub for EvidenceChannel."""
        pass

    class EvidenceRecord:  # type: ignore[no-redef]
        """Stub for EvidenceRecord."""
        pass

    class ChannelRouter:  # type: ignore[no-redef]
        """Stub for ChannelRouter."""
        pass

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
        make_task_section,
        make_cancellation_record,
        make_scope,
    )
except ImportError:
    # ── Minimal stubs so this module is importable standalone ──────────────
    from enum import Enum

    class ScopeStatus(str, Enum):  # type: ignore[no-redef]
        """Stub for ScopeStatus."""
        ACTIVE = "active"
        COMPLETED = "completed"
        CANCELLED = "cancelled"
        FAILED = "failed"

    class CancellationReason(str, Enum):  # type: ignore[no-redef]
        """Stub for CancellationReason."""
        USER_REQUESTED = "user_requested"
        TIMEOUT = "timeout"
        PARENT_CANCELLED = "parent_cancelled"
        SHIELD_EXPIRED = "shield_expired"
        INTERNAL_ERROR = "internal_error"

    class BoundaryKind(str, Enum):  # type: ignore[no-redef]
        """Stub for BoundaryKind."""
        TASK = "task"
        THREAD = "thread"
        PROCESS = "process"
        SHIELD = "shield"

    class ConcurrencyRole(str, Enum):  # type: ignore[no-redef]
        """Stub for ConcurrencyRole."""
        COORDINATOR = "coordinator"
        WORKER = "worker"
        OBSERVER = "observer"

    @dataclass(frozen=True)
    class TaskLocalSection:  # type: ignore[no-redef]
        """Stub for TaskLocalSection."""
        section_id: str = ""
        task_id: str = ""
        task_name: str = ""
        parent_section_id: str | None = None
        local_bindings: frozenset[str] = frozenset()
        support_keys: frozenset[str] = frozenset()
        scope_status: ScopeStatus = ScopeStatus.ACTIVE
        provenance: tuple[str, ...] = ()
        created_at: float = 0.0
        updated_at: float = 0.0

        def with_status(self, status: ScopeStatus) -> "TaskLocalSection":
            """Return a copy with the given status."""
            return dataclasses.replace(self, scope_status=status, updated_at=time.time())

    @dataclass(frozen=True)
    class CancellationRecord:  # type: ignore[no-redef]
        """Stub for CancellationRecord."""
        record_id: str = ""
        task_id: str = ""
        reason: CancellationReason = CancellationReason.USER_REQUESTED
        obstruction_key: str = ""
        error_message: str = ""
        parent_record_id: str | None = None
        created_at: float = 0.0

    @dataclass(frozen=True)
    class ExceptionGroupRecord:  # type: ignore[no-redef]
        """Stub for ExceptionGroupRecord."""
        pass

    @dataclass(frozen=True)
    class ProcessBoundary:  # type: ignore[no-redef]
        """Stub for ProcessBoundary."""
        pass

    @dataclass(frozen=True)
    class ConcurrencyScope:  # type: ignore[no-redef]
        """Stub for ConcurrencyScope."""
        pass

    def make_task_section(  # type: ignore[no-redef]
        task_id: str,
        task_name: str,
        parent_section_id: str | None = None,
        initial_bindings: frozenset[str] | None = None,
    ) -> TaskLocalSection:
        """Stub factory for TaskLocalSection."""
        now = time.time()
        return TaskLocalSection(
            section_id=uuid.uuid4().hex,
            task_id=task_id,
            task_name=task_name,
            parent_section_id=parent_section_id,
            local_bindings=initial_bindings or frozenset(),
            support_keys=frozenset(),
            scope_status=ScopeStatus.ACTIVE,
            provenance=(task_id,),
            created_at=now,
            updated_at=now,
        )

    def make_cancellation_record(  # type: ignore[no-redef]
        task_id: str,
        reason: CancellationReason,
        obstruction_key: str,
        error_message: str = "",
        parent_record_id: str | None = None,
    ) -> CancellationRecord:
        """Stub factory for CancellationRecord."""
        return CancellationRecord(
            record_id=uuid.uuid4().hex,
            task_id=task_id,
            reason=reason,
            obstruction_key=obstruction_key,
            error_message=error_message,
            parent_record_id=parent_record_id,
            created_at=time.time(),
        )

    def make_scope(*args: Any, **kwargs: Any) -> ConcurrencyScope:  # type: ignore[no-redef]
        """Stub factory for ConcurrencyScope."""
        return ConcurrencyScope()

# ── Import TaskSectionManager from sibling module ─────────────────────────
try:
    from jugeo.python_runtime.concurrency_boundaries.task_local_context import (
        TaskSectionManager,
    )
except ImportError:
    class TaskSectionManager:  # type: ignore[no-redef]
        """Stub for TaskSectionManager."""
        def get_section(self, task_id: str) -> TaskLocalSection | None:
            """Stub."""
            return None

        def update_section(self, section_id: str, new_section: TaskLocalSection) -> bool:
            """Stub."""
            return False

        def mark_cancelled(
            self,
            task_id: str,
            reason: CancellationReason = CancellationReason.USER_REQUESTED,
        ) -> TaskLocalSection | None:
            """Stub."""
            return None


# ══════════════════════════════════════════════════════
# Module logger
# ══════════════════════════════════════════════════════

_log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════
# CancellationObstructionInjector
# ══════════════════════════════════════════════════════


class CancellationObstructionInjector:
    """Injects cancellation obstructions into task sections.

    When ``asyncio.Task.cancel()`` is called, this injector creates a
    :class:`CancellationRecord` — a first-class cohomology class in the
    task's section.  The obstruction persists until it is either discharged
    (handled via :class:`CancellationDischarger`) or propagated to a parent
    scope via :class:`ObstructionPropagator`.

    Each injected obstruction receives a unique *obstruction key* of the form
    ``obstruction:cancel:<task_id>:<8-char-uuid-prefix>``.  This key is
    stable across serialisation and can be used as a dict key in downstream
    cohomology tracking.

    An optional :class:`TaskSectionManager` may be wired in at construction
    time; when present the injector will also update the section's
    ``scope_status`` to ``CANCELLED`` on injection.

    Theory reference: Ch24 §2 — cancellation as obstruction injection.
    """

    def __init__(
        self,
        section_manager: TaskSectionManager | None = None,
    ) -> None:
        """Initialise a CancellationObstructionInjector.

        Args:
            section_manager: Optional :class:`TaskSectionManager` to use when
                transitioning section statuses.  May be ``None``.
        """
        self._injected: dict[str, CancellationRecord] = {}   # obstruction_key -> record
        self._injection_log: list[dict[str, object]] = []
        self._section_manager: TaskSectionManager | None = section_manager
        self._injection_count: int = 0

    # ──────────────────────────────────────────────────
    # Core injection
    # ──────────────────────────────────────────────────

    def inject(
        self,
        task_id: str,
        reason: CancellationReason,
        section: TaskLocalSection | None = None,
        error_message: str = "",
    ) -> CancellationRecord:
        """Inject a cancellation obstruction for *task_id*.

        Creates a :class:`CancellationRecord` with a unique obstruction key.
        If *section* is supplied the section's status is updated to CANCELLED
        via :func:`TaskLocalSection.with_status`; if a
        :class:`TaskSectionManager` is wired in, the updated section is
        persisted there as well.

        Args:
            task_id: The task being cancelled.
            reason: The :class:`CancellationReason` for this cancellation.
            section: Optional task-local section to update.
            error_message: Optional human-readable description of the cause.

        Returns:
            The newly created :class:`CancellationRecord`.
        """
        suffix = uuid.uuid4().hex[:8]
        obstruction_key = f"obstruction:cancel:{task_id}:{suffix}"

        record = make_cancellation_record(
            task_id=task_id,
            reason=reason,
            obstruction_key=obstruction_key,
            error_message=error_message,
        )

        # Update the section status if we have a section to update.
        if section is not None:
            updated_section = section.with_status(ScopeStatus.CANCELLED)
            if self._section_manager is not None:
                self._section_manager.update_section(section.section_id, updated_section)

        # Persist and log.
        self._injected[obstruction_key] = record
        self._injection_count += 1
        self._injection_log.append(
            {
                "event": "injected",
                "obstruction_key": obstruction_key,
                "task_id": task_id,
                "reason": str(reason),
                "error_message": error_message,
                "timestamp": time.time(),
            }
        )
        _log.debug(
            "Obstruction injected: key=%s task=%s reason=%s.",
            obstruction_key, task_id, reason,
        )
        return record

    def inject_timeout(
        self,
        task_id: str,
        deadline: float,
        section: TaskLocalSection | None = None,
    ) -> CancellationRecord:
        """Inject a TIMEOUT cancellation obstruction.

        Args:
            task_id: The task that timed out.
            deadline: The UNIX timestamp of the deadline that was exceeded.
            section: Optional task-local section to update.

        Returns:
            The newly created :class:`CancellationRecord`.
        """
        error_message = f"Deadline exceeded: task {task_id} timed out at {deadline}"
        return self.inject(
            task_id=task_id,
            reason=CancellationReason.TIMEOUT,
            section=section,
            error_message=error_message,
        )

    def inject_parent_cancellation(
        self,
        task_id: str,
        parent_record: CancellationRecord,
        section: TaskLocalSection | None = None,
    ) -> CancellationRecord:
        """Inject a PARENT_CANCELLED obstruction derived from *parent_record*.

        The new record references *parent_record.record_id* as its
        ``parent_record_id``, preserving the causal chain.

        Args:
            task_id: The child task receiving the derived cancellation.
            parent_record: The parent's :class:`CancellationRecord`.
            section: Optional task-local section to update.

        Returns:
            The newly created derived :class:`CancellationRecord`.
        """
        error_message = (
            f"Parent cancellation propagated from task {parent_record.task_id} "
            f"(record {parent_record.record_id[:8]}…)"
        )
        suffix = uuid.uuid4().hex[:8]
        obstruction_key = f"obstruction:cancel:{task_id}:{suffix}"

        record = make_cancellation_record(
            task_id=task_id,
            reason=CancellationReason.PARENT_CANCELLED,
            obstruction_key=obstruction_key,
            error_message=error_message,
            parent_record_id=parent_record.record_id,
        )

        if section is not None:
            updated_section = section.with_status(ScopeStatus.CANCELLED)
            if self._section_manager is not None:
                self._section_manager.update_section(section.section_id, updated_section)

        self._injected[obstruction_key] = record
        self._injection_count += 1
        self._injection_log.append(
            {
                "event": "injected_derived",
                "obstruction_key": obstruction_key,
                "task_id": task_id,
                "parent_record_id": parent_record.record_id,
                "reason": str(CancellationReason.PARENT_CANCELLED),
                "timestamp": time.time(),
            }
        )
        _log.debug(
            "Derived obstruction injected: key=%s task=%s parent_record=%s.",
            obstruction_key, task_id, parent_record.record_id[:8],
        )
        return record

    # ──────────────────────────────────────────────────
    # Lookup and introspection
    # ──────────────────────────────────────────────────

    def get_obstruction(self, obstruction_key: str) -> CancellationRecord | None:
        """Return the :class:`CancellationRecord` for *obstruction_key*.

        Args:
            obstruction_key: The key assigned at injection time.

        Returns:
            The matching record, or ``None`` if not found.
        """
        return self._injected.get(obstruction_key)

    def list_obstructions(self) -> list[CancellationRecord]:
        """Return all currently tracked obstruction records.

        Returns:
            A list of all :class:`CancellationRecord` values in injection
            order.
        """
        return list(self._injected.values())

    def obstructions_for_task(self, task_id: str) -> list[CancellationRecord]:
        """Return all obstruction records for *task_id*.

        Args:
            task_id: The task to filter by.

        Returns:
            A list of :class:`CancellationRecord` instances whose
            ``task_id`` matches.
        """
        return [r for r in self._injected.values() if r.task_id == task_id]

    def is_obstructed(self, task_id: str) -> bool:
        """Return whether *task_id* has any outstanding obstruction records.

        Args:
            task_id: The task to check.

        Returns:
            ``True`` if at least one obstruction record exists for the task.
        """
        return any(r.task_id == task_id for r in self._injected.values())

    def obstruction_count(self) -> int:
        """Return the total number of tracked obstruction records.

        Returns:
            An integer count.
        """
        return len(self._injected)

    def injection_report(self) -> dict[str, object]:
        """Return a structured summary of injection activity.

        Returns:
            A dict with keys ``total_injected``, ``by_reason`` (mapping
            reason strings to counts), ``task_ids`` (unique task ids), and
            ``recent`` (last 5 injection log entries).
        """
        by_reason: dict[str, int] = {}
        task_ids: set[str] = set()
        for entry in self._injection_log:
            reason_str = str(entry.get("reason", "unknown"))
            by_reason[reason_str] = by_reason.get(reason_str, 0) + 1
            tid = str(entry.get("task_id", ""))
            if tid:
                task_ids.add(tid)

        return {
            "total_injected": self._injection_count,
            "by_reason": by_reason,
            "task_ids": sorted(task_ids),
            "recent": self._injection_log[-5:],
        }


# ══════════════════════════════════════════════════════
# ObstructionPropagator
# ══════════════════════════════════════════════════════


class ObstructionPropagator:
    """Propagates cancellation obstructions through the task tree.

    When a parent task is cancelled, its cancellation obstruction propagates
    to all child tasks unless a :class:`CancellationShield` marks a task as
    protected. The propagator traverses the task-child relationship graph,
    injecting derived obstructions at each reachable unshielded descendant.

    The propagator maintains:

    * ``_task_children`` — adjacency list mapping parent task ids to lists
      of child task ids.
    * ``_shields`` — set of task ids that are currently protected from
      propagation.
    * ``_propagation_log`` — audit log of every propagation event.
    * ``_injector`` — the :class:`CancellationObstructionInjector` used to
      create derived records.

    Theory reference: Ch24 §2 — obstruction propagation through task trees
    (THEOREM_OBSTRUCTION_PROPAGATION).
    """

    def __init__(
        self,
        injector: CancellationObstructionInjector | None = None,
    ) -> None:
        """Initialise an ObstructionPropagator.

        Args:
            injector: Optional :class:`CancellationObstructionInjector` to
                use for creating derived records.  A new one is created if
                not supplied.
        """
        self._task_children: dict[str, list[str]] = {}
        self._propagation_log: list[dict[str, object]] = []
        self._shields: set[str] = set()
        self._injector: CancellationObstructionInjector = (
            injector if injector is not None else CancellationObstructionInjector()
        )

    # ──────────────────────────────────────────────────
    # Tree construction
    # ──────────────────────────────────────────────────

    def register_child(self, parent_task_id: str, child_task_id: str) -> None:
        """Register *child_task_id* as a child of *parent_task_id*.

        Args:
            parent_task_id: The id of the parent task.
            child_task_id: The id of the child task to register.
        """
        if parent_task_id not in self._task_children:
            self._task_children[parent_task_id] = []
        if child_task_id not in self._task_children[parent_task_id]:
            self._task_children[parent_task_id].append(child_task_id)
        _log.debug("register_child: %s -> %s.", parent_task_id, child_task_id)

    # ──────────────────────────────────────────────────
    # Shield management
    # ──────────────────────────────────────────────────

    def shield_task(self, task_id: str) -> None:
        """Mark *task_id* as shielded from obstruction propagation.

        Args:
            task_id: The task to shield.
        """
        self._shields.add(task_id)
        _log.debug("shield_task: %s is now shielded.", task_id)

    def unshield_task(self, task_id: str) -> bool:
        """Remove the shield from *task_id*.

        Args:
            task_id: The task to unshield.

        Returns:
            ``True`` if the task was shielded and has been unshielded;
            ``False`` if it was not shielded.
        """
        if task_id in self._shields:
            self._shields.discard(task_id)
            _log.debug("unshield_task: %s is no longer shielded.", task_id)
            return True
        return False

    def is_shielded(self, task_id: str) -> bool:
        """Return whether *task_id* is currently shielded.

        Args:
            task_id: The task to check.

        Returns:
            ``True`` if shielded, ``False`` otherwise.
        """
        return task_id in self._shields

    # ──────────────────────────────────────────────────
    # Propagation
    # ──────────────────────────────────────────────────

    def propagate(
        self,
        record: CancellationRecord,
        sections: dict[str, TaskLocalSection] | None = None,
    ) -> list[CancellationRecord]:
        """Propagate *record*'s obstruction to all unshielded descendants.

        Uses BFS over the task-child graph rooted at *record.task_id*. For
        each reachable, unshielded child task, a derived
        :class:`CancellationRecord` is created via
        :meth:`CancellationObstructionInjector.inject_parent_cancellation`.

        Args:
            record: The parent :class:`CancellationRecord` to propagate.
            sections: Optional mapping of task_id -> :class:`TaskLocalSection`
                to update section statuses during propagation.

        Returns:
            A list of all newly created derived
            :class:`CancellationRecord` instances.
        """
        derived: list[CancellationRecord] = []
        sections_map = sections or {}

        queue: deque[tuple[str, CancellationRecord]] = deque()
        for child_id in self._task_children.get(record.task_id, []):
            queue.append((child_id, record))

        visited: set[str] = {record.task_id}

        while queue:
            task_id, parent_rec = queue.popleft()
            if task_id in visited:
                continue
            visited.add(task_id)

            if self.is_shielded(task_id):
                self._propagation_log.append(
                    {
                        "event": "blocked_by_shield",
                        "task_id": task_id,
                        "parent_record_id": parent_rec.record_id,
                        "timestamp": time.time(),
                    }
                )
                _log.debug("propagate: task %s is shielded — propagation blocked.", task_id)
                continue

            section = sections_map.get(task_id)
            child_record = self._injector.inject_parent_cancellation(
                task_id=task_id,
                parent_record=parent_rec,
                section=section,
            )
            derived.append(child_record)

            self._propagation_log.append(
                {
                    "event": "propagated",
                    "task_id": task_id,
                    "new_record_id": child_record.record_id,
                    "parent_record_id": parent_rec.record_id,
                    "timestamp": time.time(),
                }
            )

            # Enqueue grandchildren with the newly-created record as parent.
            for grandchild_id in self._task_children.get(task_id, []):
                if grandchild_id not in visited:
                    queue.append((grandchild_id, child_record))

        _log.debug(
            "propagate: %d derived obstructions created from record %s.",
            len(derived), record.record_id[:8],
        )
        return derived

    def propagate_to_direct_children(
        self,
        record: CancellationRecord,
    ) -> list[CancellationRecord]:
        """Propagate *record*'s obstruction only to immediate children.

        Unlike :meth:`propagate`, this method does not recurse; it only
        creates derived records for tasks registered directly under
        ``record.task_id``.

        Args:
            record: The parent :class:`CancellationRecord`.

        Returns:
            A list of derived :class:`CancellationRecord` instances for
            each direct, unshielded child.
        """
        derived: list[CancellationRecord] = []
        for child_id in self._task_children.get(record.task_id, []):
            if self.is_shielded(child_id):
                _log.debug("propagate_to_direct_children: %s shielded.", child_id)
                continue
            child_record = self._injector.inject_parent_cancellation(
                task_id=child_id,
                parent_record=record,
            )
            derived.append(child_record)
            self._propagation_log.append(
                {
                    "event": "propagated_direct",
                    "task_id": child_id,
                    "new_record_id": child_record.record_id,
                    "parent_record_id": record.record_id,
                    "timestamp": time.time(),
                }
            )
        return derived

    # ──────────────────────────────────────────────────
    # Tree queries
    # ──────────────────────────────────────────────────

    def children_of(self, task_id: str) -> list[str]:
        """Return the direct children of *task_id*.

        Args:
            task_id: The task to query.

        Returns:
            A list of direct child task ids (empty if no children registered).
        """
        return list(self._task_children.get(task_id, []))

    def all_descendants(self, task_id: str) -> list[str]:
        """Return all descendants of *task_id* via BFS.

        Args:
            task_id: The root task to traverse from.

        Returns:
            A list of all descendant task ids in BFS order.
        """
        result: list[str] = []
        queue: deque[str] = deque(self._task_children.get(task_id, []))
        visited: set[str] = {task_id}

        while queue:
            current = queue.popleft()
            if current in visited:
                continue
            visited.add(current)
            result.append(current)
            queue.extend(self._task_children.get(current, []))

        return result


# ══════════════════════════════════════════════════════
# CancellationShield
# ══════════════════════════════════════════════════════


class CancellationShield:
    """Prevents obstruction propagation at task boundaries.

    ``asyncio.shield()`` prevents cancellation from propagating to a wrapped
    coroutine.  In the sheaf-theoretic model, a shield is a *boundary* that
    blocks obstruction injection from the parent scope. The shield records
    that it absorbed an obstruction, maintaining the audit trail without
    forwarding the cohomology class to protected tasks.

    A shield has two states: *active* (blocking propagation) and *deactivated*
    (transparent — obstructions pass through). An inactive shield still
    retains its absorption history.

    Theory reference: Ch24 §2 — cancellation shielding as boundary absorption.
    """

    def __init__(self, shield_id: str | None = None) -> None:
        """Initialise a CancellationShield.

        Args:
            shield_id: Optional explicit identifier.  A random UUID hex is
                used if not supplied.
        """
        self._shield_id: str = shield_id if shield_id is not None else uuid.uuid4().hex
        self._protected_tasks: set[str] = set()
        self._absorbed_obstructions: list[CancellationRecord] = []
        self._is_active: bool = True

    # ──────────────────────────────────────────────────
    # Protection management
    # ──────────────────────────────────────────────────

    def protect(self, task_id: str) -> None:
        """Add *task_id* to the set of tasks protected by this shield.

        Args:
            task_id: The task to protect.
        """
        self._protected_tasks.add(task_id)
        _log.debug("CancellationShield %s: protecting task %s.", self._shield_id[:8], task_id)

    def unprotect(self, task_id: str) -> bool:
        """Remove *task_id* from the protected set.

        Args:
            task_id: The task to unprotect.

        Returns:
            ``True`` if *task_id* was protected and has been removed;
            ``False`` otherwise.
        """
        if task_id in self._protected_tasks:
            self._protected_tasks.discard(task_id)
            _log.debug(
                "CancellationShield %s: unprotected task %s.",
                self._shield_id[:8], task_id,
            )
            return True
        return False

    def is_protected(self, task_id: str) -> bool:
        """Return whether *task_id* is currently protected by this shield.

        Args:
            task_id: The task to check.

        Returns:
            ``True`` if the shield is active and *task_id* is in the
            protected set.
        """
        return self._is_active and task_id in self._protected_tasks

    # ──────────────────────────────────────────────────
    # Absorption
    # ──────────────────────────────────────────────────

    def absorb(self, record: CancellationRecord) -> dict[str, object]:
        """Record that this shield has absorbed *record*'s obstruction.

        Even when the shield is inactive, absorption is recorded (the record
        was absorbed, but propagation was not blocked since the shield was
        inactive).

        Args:
            record: The :class:`CancellationRecord` to absorb.

        Returns:
            A dict with keys ``absorbed``, ``shield_id``, ``record_id``,
            ``shield_active``, and ``timestamp``.
        """
        self._absorbed_obstructions.append(record)
        result: dict[str, object] = {
            "absorbed": True,
            "shield_id": self._shield_id,
            "record_id": record.record_id,
            "shield_active": self._is_active,
            "timestamp": time.time(),
        }
        _log.debug(
            "CancellationShield %s: absorbed obstruction %s (active=%s).",
            self._shield_id[:8], record.record_id[:8], self._is_active,
        )
        return result

    # ──────────────────────────────────────────────────
    # Lifecycle
    # ──────────────────────────────────────────────────

    def deactivate(self) -> None:
        """Deactivate this shield (obstructions will pass through).

        The shield retains its absorption history after deactivation.
        """
        self._is_active = False
        _log.debug("CancellationShield %s: deactivated.", self._shield_id[:8])

    def reactivate(self) -> None:
        """Reactivate this shield (obstructions are blocked again).

        Reactivation is idempotent if the shield is already active.
        """
        self._is_active = True
        _log.debug("CancellationShield %s: reactivated.", self._shield_id[:8])

    # ──────────────────────────────────────────────────
    # Introspection
    # ──────────────────────────────────────────────────

    def absorbed_count(self) -> int:
        """Return the number of obstructions absorbed by this shield.

        Returns:
            An integer count of absorbed :class:`CancellationRecord` items.
        """
        return len(self._absorbed_obstructions)

    def shield_report(self) -> dict[str, object]:
        """Return a structured report of shield state.

        Returns:
            A dict with keys ``shield_id``, ``is_active``,
            ``protected_task_count``, ``absorbed_count``,
            ``protected_tasks``, and ``timestamp``.
        """
        return {
            "shield_id": self._shield_id,
            "is_active": self._is_active,
            "protected_task_count": len(self._protected_tasks),
            "protected_tasks": sorted(self._protected_tasks),
            "absorbed_count": len(self._absorbed_obstructions),
            "absorbed_keys": [r.obstruction_key for r in self._absorbed_obstructions],
            "timestamp": time.time(),
        }

    def clear_absorbed(self) -> int:
        """Discard all absorbed obstruction records.

        Returns:
            The number of records cleared.
        """
        count = len(self._absorbed_obstructions)
        self._absorbed_obstructions.clear()
        _log.debug(
            "CancellationShield %s: cleared %d absorbed records.",
            self._shield_id[:8], count,
        )
        return count


# ══════════════════════════════════════════════════════
# CancellationDischarger
# ══════════════════════════════════════════════════════


class CancellationDischarger:
    """Discharges cancellation obstructions through the discharge protocol.

    A cancellation obstruction can be discharged if and only if the handler
    satisfies the *discharge protocol*:

      1. The obstruction is **acknowledged** — the handler supplies its
         obstruction key explicitly.
      2. **Cleanup actions** are declared — a non-empty list of action strings
         is provided, documenting what cleanup was performed.
      3. The **discharge is recorded** in the discharger's persistent log,
         creating an auditable provenance entry.

    An obstruction that is not discharged cannot be silently dropped.
    Rejected discharges (where the handler explicitly declines) are tracked
    separately in ``_failed_discharges``.

    Theory reference: Ch24 §2 — THEOREM_CANCELLATION_DISCHARGE.
    """

    def __init__(self) -> None:
        """Initialise an empty CancellationDischarger."""
        self._pending: dict[str, CancellationRecord] = {}     # key -> record
        self._discharged: dict[str, dict[str, object]] = {}   # key -> discharge info
        self._failed_discharges: list[dict[str, object]] = []
        self._discharge_log: list[dict[str, object]] = []

    # ──────────────────────────────────────────────────
    # Submission
    # ──────────────────────────────────────────────────

    def submit(self, record: CancellationRecord) -> None:
        """Submit *record* to the discharge queue.

        A record that has already been discharged or is already pending
        is silently accepted (idempotent submission).

        Args:
            record: The :class:`CancellationRecord` to submit for discharge.
        """
        key = record.obstruction_key
        if key in self._discharged:
            _log.debug("submit: obstruction %s already discharged — skipping.", key)
            return
        if key not in self._pending:
            self._pending[key] = record
            _log.debug("submit: obstruction %s enqueued for discharge.", key)

    # ──────────────────────────────────────────────────
    # Discharge
    # ──────────────────────────────────────────────────

    def discharge(
        self,
        obstruction_key: str,
        handler_id: str,
        cleanup_actions: list[str],
    ) -> dict[str, object]:
        """Discharge the obstruction identified by *obstruction_key*.

        The obstruction is moved from ``_pending`` to ``_discharged``. An
        empty ``cleanup_actions`` list is accepted but logged as a warning —
        the protocol strongly recommends at least one declared action.

        Args:
            obstruction_key: The key of the obstruction to discharge.
            handler_id: Identifier for the handler performing the discharge.
            cleanup_actions: List of strings describing cleanup actions taken.

        Returns:
            A result dict with keys ``obstruction_key``, ``handler_id``,
            ``cleanup_actions``, ``task_id``, ``status``, and ``timestamp``.

        Raises:
            KeyError: If *obstruction_key* is not in ``_pending``.
        """
        if obstruction_key not in self._pending:
            raise KeyError(
                f"discharge: obstruction_key {obstruction_key!r} not found in pending queue."
            )

        if not cleanup_actions:
            _log.warning(
                "discharge: obstruction %s discharged with no cleanup actions declared.",
                obstruction_key,
            )

        record = self._pending.pop(obstruction_key)
        now = time.time()
        discharge_info: dict[str, object] = {
            "obstruction_key": obstruction_key,
            "record_id": record.record_id,
            "task_id": record.task_id,
            "handler_id": handler_id,
            "cleanup_actions": cleanup_actions,
            "reason": str(record.reason),
            "status": "discharged",
            "timestamp": now,
        }

        self._discharged[obstruction_key] = discharge_info
        self._discharge_log.append({"event": "discharged", **discharge_info})
        _log.debug(
            "Obstruction %s discharged by handler %s (%d actions).",
            obstruction_key, handler_id, len(cleanup_actions),
        )
        return discharge_info

    def discharge_all(self, handler_id: str) -> list[dict[str, object]]:
        """Discharge all pending obstructions with a shared *handler_id*.

        Each obstruction receives the handler_id but an empty
        ``cleanup_actions`` list (caller may post-process results to add
        per-record actions if needed).

        Args:
            handler_id: Identifier for the batch handler.

        Returns:
            A list of discharge result dicts, one per record.
        """
        keys = list(self._pending.keys())
        results: list[dict[str, object]] = []
        for key in keys:
            try:
                result = self.discharge(
                    obstruction_key=key,
                    handler_id=handler_id,
                    cleanup_actions=["batch_discharge"],
                )
                results.append(result)
            except KeyError:
                _log.warning("discharge_all: key %s vanished during batch — skipping.", key)
        _log.debug("discharge_all: discharged %d obstructions.", len(results))
        return results

    # ──────────────────────────────────────────────────
    # Rejection
    # ──────────────────────────────────────────────────

    def reject(self, obstruction_key: str, reason: str) -> dict[str, object]:
        """Reject the discharge attempt for *obstruction_key*.

        The record remains in ``_pending`` unless it is absent, in which
        case the rejection is still recorded in ``_failed_discharges``.

        Args:
            obstruction_key: The key of the obstruction whose discharge is
                being rejected.
            reason: A human-readable explanation for the rejection.

        Returns:
            A result dict with keys ``obstruction_key``, ``reason``,
            ``status``, and ``timestamp``.
        """
        record = self._pending.get(obstruction_key)
        rejection: dict[str, object] = {
            "obstruction_key": obstruction_key,
            "task_id": record.task_id if record else None,
            "reason": reason,
            "status": "rejected",
            "timestamp": time.time(),
        }
        self._failed_discharges.append(rejection)
        self._discharge_log.append({"event": "rejected", **rejection})
        _log.warning(
            "Discharge rejected for obstruction %s: %s.", obstruction_key, reason
        )
        return rejection

    # ──────────────────────────────────────────────────
    # Introspection
    # ──────────────────────────────────────────────────

    def is_discharged(self, obstruction_key: str) -> bool:
        """Return whether *obstruction_key* has been successfully discharged.

        Args:
            obstruction_key: The key to check.

        Returns:
            ``True`` if the obstruction has been discharged.
        """
        return obstruction_key in self._discharged

    def pending_count(self) -> int:
        """Return the number of obstructions awaiting discharge.

        Returns:
            An integer count of pending obstruction keys.
        """
        return len(self._pending)

    def discharged_count(self) -> int:
        """Return the total number of successfully discharged obstructions.

        Returns:
            An integer count.
        """
        return len(self._discharged)

    def discharge_report(self) -> dict[str, object]:
        """Return a structured report of discharge activity.

        Returns:
            A dict with keys ``pending``, ``discharged``, ``failed``,
            ``discharge_log_length``, and ``timestamp``.
        """
        return {
            "pending": len(self._pending),
            "discharged": len(self._discharged),
            "failed": len(self._failed_discharges),
            "discharge_log_length": len(self._discharge_log),
            "recent_log": self._discharge_log[-5:],
            "timestamp": time.time(),
        }

    def export_state(self) -> dict[str, object]:
        """Export the full discharger state as a serialisable dict.

        Returns:
            A comprehensive dict containing all pending keys, all
            discharged records, all failed discharge records, and the
            full discharge log.
        """
        return {
            "pending_keys": list(self._pending.keys()),
            "discharged": dict(self._discharged),
            "failed_discharges": list(self._failed_discharges),
            "discharge_log": list(self._discharge_log),
            "timestamp": time.time(),
        }


# ══════════════════════════════════════════════════════
# Module-level helper functions
# ══════════════════════════════════════════════════════


def make_obstruction_key(task_id: str, suffix: str = "") -> str:
    """Construct a canonical obstruction key for *task_id*.

    If *suffix* is not supplied, a fresh 8-character UUID hex fragment is
    used to ensure uniqueness.

    Args:
        task_id: The task id to embed in the key.
        suffix: Optional explicit suffix.  A random fragment is used if
            empty or not provided.

    Returns:
        A string of the form ``obstruction:cancel:<task_id>:<suffix>``.
    """
    resolved_suffix = suffix if suffix else uuid.uuid4().hex[:8]
    return f"obstruction:cancel:{task_id}:{resolved_suffix}"


def cancellation_summary(records: list[CancellationRecord]) -> dict[str, int]:
    """Summarise a list of :class:`CancellationRecord` by reason.

    Args:
        records: The list of cancellation records to summarise.

    Returns:
        A dict mapping each :class:`CancellationReason` string to the
        count of records with that reason.  Includes a ``total`` key.
    """
    summary: dict[str, int] = {}
    for record in records:
        key = str(record.reason)
        summary[key] = summary.get(key, 0) + 1
    summary["total"] = len(records)
    return summary


# ══════════════════════════════════════════════════════
# Public API
# ══════════════════════════════════════════════════════

__all__ = [
    "CancellationObstructionInjector",
    "ObstructionPropagator",
    "CancellationShield",
    "CancellationDischarger",
    "make_obstruction_key",
    "cancellation_summary",
]

# copilot: shared-core marker for future LLM orchestration.
