"""Ch24 §1 — Task-Local Context as Scoped Sections.

Each asyncio task carries its own local section scoped to its execution context.
This module implements the machinery for creating, inheriting, managing, and
cleaning up task-local sections according to sheaf-theoretic semantics.

In the sheaf-theoretic model of concurrent execution, each asyncio task
corresponds to a coordinate in the execution site. The task's local context
variables — those bound to asyncio.Task-specific copies via contextvars —
form a section at that coordinate. Sections can be restricted (inherited by
child tasks), merged (when compatible), or obstructed (when a task is
cancelled before resolving all bindings).

This module provides four primary machinery components:

  1. TaskSectionManager — lifecycle management for task-local sections.
  2. ContextVarBridge — maps Python ContextVar instances to section bindings.
  3. SectionInheritanceEngine — models child-task inheritance as restriction
     morphisms in the sheaf.
  4. TaskSectionCleanup — guarantees section disposal on task termination.

Theory reference: Ch24 §1 — task-local context as scoped sections.
"""

from __future__ import annotations

import dataclasses
import logging
import time
import uuid
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


# ══════════════════════════════════════════════════════
# Module logger
# ══════════════════════════════════════════════════════

_log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════
# TaskSectionManager
# ══════════════════════════════════════════════════════


class TaskSectionManager:
    """Manages the lifecycle of task-local sections.

    In the sheaf-theoretic model, each asyncio task corresponds to a coordinate
    in the execution site. The task's local context variables form a section at
    that coordinate. This manager creates, tracks, and destroys these sections.

    Sections are identified by two keys: the opaque ``section_id`` (a UUID
    hex string generated at creation time) and the ``task_id`` supplied by
    the caller (typically ``id(asyncio.current_task())`` formatted as a
    string, or a human-readable label in tests). The manager maintains
    bidirectional mappings between both keys.

    A history log is maintained for audit and debugging purposes. All
    mutations append a structured entry; the log is never truncated
    automatically.

    Theory reference: Ch24 §1 — task-local context as scoped sections.
    """

    def __init__(self) -> None:
        """Initialise an empty TaskSectionManager."""
        self._sections: dict[str, TaskLocalSection] = {}
        self._task_to_section: dict[str, str] = {}
        self._section_history: list[dict[str, object]] = []
        self._creation_count: int = 0
        self._cleanup_count: int = 0

    # ──────────────────────────────────────────────────
    # Creation
    # ──────────────────────────────────────────────────

    def create_section(
        self,
        task_id: str,
        task_name: str,
        parent_task_id: str | None = None,
        initial_bindings: frozenset[str] | None = None,
    ) -> TaskLocalSection:
        """Create a new task-local section and register it.

        If *parent_task_id* is supplied and a section for that task already
        exists, the new section's ``parent_section_id`` is set accordingly,
        establishing the restriction morphism in the sheaf.

        Args:
            task_id: Unique identifier for the asyncio task.
            task_name: Human-readable name for the task (used in logs).
            parent_task_id: Optional task_id of the spawning parent task.
            initial_bindings: Optional initial set of binding keys for the
                section's ``local_bindings`` field.

        Returns:
            The newly created and registered :class:`TaskLocalSection`.
        """
        parent_section_id: str | None = None
        if parent_task_id is not None:
            parent_sid = self._task_to_section.get(parent_task_id)
            if parent_sid is not None:
                parent_section_id = parent_sid
            else:
                _log.debug(
                    "create_section: parent_task_id=%r not found in registry; "
                    "creating section without parent link.",
                    parent_task_id,
                )

        section = make_task_section(
            task_id=task_id,
            task_name=task_name,
            parent_section_id=parent_section_id,
            initial_bindings=initial_bindings,
        )

        self._sections[section.section_id] = section
        self._task_to_section[task_id] = section.section_id
        self._creation_count += 1

        self._section_history.append(
            {
                "event": "created",
                "section_id": section.section_id,
                "task_id": task_id,
                "task_name": task_name,
                "parent_section_id": parent_section_id,
                "timestamp": time.time(),
            }
        )
        _log.debug("Section %s created for task %s.", section.section_id, task_id)
        return section

    # ──────────────────────────────────────────────────
    # Lookup
    # ──────────────────────────────────────────────────

    def get_section(self, task_id: str) -> TaskLocalSection | None:
        """Return the section for *task_id*, or ``None`` if absent.

        Args:
            task_id: The task identifier used when the section was created.

        Returns:
            The :class:`TaskLocalSection` bound to *task_id*, or ``None``.
        """
        sid = self._task_to_section.get(task_id)
        if sid is None:
            return None
        return self._sections.get(sid)

    def get_section_by_id(self, section_id: str) -> TaskLocalSection | None:
        """Return the section identified by *section_id* directly.

        Args:
            section_id: The opaque UUID-hex identifier of the section.

        Returns:
            The matching :class:`TaskLocalSection`, or ``None``.
        """
        return self._sections.get(section_id)

    # ──────────────────────────────────────────────────
    # Mutation
    # ──────────────────────────────────────────────────

    def update_section(self, section_id: str, new_section: TaskLocalSection) -> bool:
        """Replace the stored section with *new_section*.

        The replacement is keyed by *section_id*. If no section with that id
        is currently registered, the update is rejected and ``False`` is
        returned.  This method does **not** update the task-to-section index
        because section_ids are immutable; only the value changes.

        Args:
            section_id: The section to replace.
            new_section: The replacement :class:`TaskLocalSection` value.

        Returns:
            ``True`` if the section existed and was replaced; ``False`` otherwise.
        """
        if section_id not in self._sections:
            _log.warning("update_section: section_id=%r not found.", section_id)
            return False
        self._sections[section_id] = new_section
        self._section_history.append(
            {
                "event": "updated",
                "section_id": section_id,
                "new_status": str(new_section.scope_status),
                "timestamp": time.time(),
            }
        )
        return True

    def mark_cancelled(
        self,
        task_id: str,
        reason: CancellationReason = CancellationReason.USER_REQUESTED,
    ) -> TaskLocalSection | None:
        """Transition the section for *task_id* to CANCELLED status.

        Args:
            task_id: The task whose section should be marked cancelled.
            reason: The :class:`CancellationReason` for the cancellation.

        Returns:
            The updated :class:`TaskLocalSection`, or ``None`` if not found.
        """
        section = self.get_section(task_id)
        if section is None:
            _log.warning("mark_cancelled: no section for task_id=%r.", task_id)
            return None

        updated = section.with_status(ScopeStatus.CANCELLED)
        self._sections[section.section_id] = updated
        self._section_history.append(
            {
                "event": "cancelled",
                "section_id": section.section_id,
                "task_id": task_id,
                "reason": str(reason),
                "timestamp": time.time(),
            }
        )
        _log.debug(
            "Section %s (task=%s) marked CANCELLED (reason=%s).",
            section.section_id, task_id, reason,
        )
        return updated

    def mark_completed(self, task_id: str) -> TaskLocalSection | None:
        """Transition the section for *task_id* to COMPLETED status.

        Args:
            task_id: The task whose section should be marked completed.

        Returns:
            The updated :class:`TaskLocalSection`, or ``None`` if not found.
        """
        section = self.get_section(task_id)
        if section is None:
            _log.warning("mark_completed: no section for task_id=%r.", task_id)
            return None

        updated = section.with_status(ScopeStatus.COMPLETED)
        self._sections[section.section_id] = updated
        self._section_history.append(
            {
                "event": "completed",
                "section_id": section.section_id,
                "task_id": task_id,
                "timestamp": time.time(),
            }
        )
        _log.debug("Section %s (task=%s) marked COMPLETED.", section.section_id, task_id)
        return updated

    # ──────────────────────────────────────────────────
    # Queries
    # ──────────────────────────────────────────────────

    def list_active(self) -> list[TaskLocalSection]:
        """Return all sections currently in ACTIVE status.

        Returns:
            A list of :class:`TaskLocalSection` instances with
            ``scope_status == ScopeStatus.ACTIVE``.
        """
        return [s for s in self._sections.values() if s.scope_status == ScopeStatus.ACTIVE]

    def list_cancelled(self) -> list[TaskLocalSection]:
        """Return all sections currently in CANCELLED status.

        Returns:
            A list of :class:`TaskLocalSection` instances with
            ``scope_status == ScopeStatus.CANCELLED``.
        """
        return [
            s for s in self._sections.values() if s.scope_status == ScopeStatus.CANCELLED
        ]

    # ──────────────────────────────────────────────────
    # Cleanup
    # ──────────────────────────────────────────────────

    def cleanup_completed(self) -> int:
        """Remove all COMPLETED sections from the live registry.

        Sections in COMPLETED status have no further obligations; removing
        them from the live registry frees memory. The history log is
        untouched so audit trails are preserved.

        Returns:
            The number of sections removed.
        """
        completed_ids = [
            sid
            for sid, s in self._sections.items()
            if s.scope_status == ScopeStatus.COMPLETED
        ]
        for sid in completed_ids:
            section = self._sections.pop(sid)
            # Remove the forward task→section mapping.
            if section.task_id in self._task_to_section:
                if self._task_to_section[section.task_id] == sid:
                    del self._task_to_section[section.task_id]

        removed = len(completed_ids)
        self._cleanup_count += removed
        if removed:
            _log.debug("cleanup_completed: removed %d COMPLETED sections.", removed)
        return removed

    def stats(self) -> dict[str, int]:
        """Return a snapshot of key lifecycle counters.

        Returns:
            A dict with keys ``total_created``, ``total_cleaned``,
            ``current_active``, ``current_cancelled``, and
            ``current_completed``.
        """
        by_status: dict[str, int] = {}
        for s in self._sections.values():
            key = str(s.scope_status)
            by_status[key] = by_status.get(key, 0) + 1

        active_key = str(ScopeStatus.ACTIVE)
        cancelled_key = str(ScopeStatus.CANCELLED)
        completed_key = str(ScopeStatus.COMPLETED)

        return {
            "total_created": self._creation_count,
            "total_cleaned": self._cleanup_count,
            "current_active": by_status.get(active_key, 0),
            "current_cancelled": by_status.get(cancelled_key, 0),
            "current_completed": by_status.get(completed_key, 0),
        }


# ══════════════════════════════════════════════════════
# ContextVarBridge
# ══════════════════════════════════════════════════════


class ContextVarBridge:
    """Bridges Python contextvars.ContextVar to task-local section bindings.

    In Python's asyncio model, ``contextvars.ContextVar`` instances carry
    values that are local to each task's execution context. The
    ``ContextVarBridge`` maps these ``ContextVar`` names to binding keys in
    the task's scoped section, making them first-class objects in the
    sheaf-theoretic framework.

    Each registered variable is assigned a *binding key* of the form
    ``cv:<var_name>``. The bridge does not hold actual ``ContextVar``
    objects (to avoid circular imports with asyncio internals); it works
    purely at the level of names and opaque value objects.

    An access log records every ``set_value`` and ``get_value`` call with a
    timestamp, enabling replay and debugging of context state evolution.

    Theory reference: Ch24 §1 — ContextVar semantics as scoped section
    bindings.
    """

    def __init__(self) -> None:
        """Initialise an empty ContextVarBridge."""
        self._var_registry: dict[str, str] = {}          # var_name -> binding_key
        self._binding_values: dict[str, object] = {}     # binding_key -> value
        self._access_log: list[dict[str, object]] = []
        self._var_count: int = 0

    # ──────────────────────────────────────────────────
    # Registration
    # ──────────────────────────────────────────────────

    def register_var(self, var_name: str, binding_key: str | None = None) -> str:
        """Register a ContextVar name with an optional explicit binding key.

        If *binding_key* is not supplied, a canonical key of the form
        ``cv:<var_name>`` is used. Re-registering an already-registered name
        with the same binding key is idempotent; re-registering with a
        *different* key logs a warning and overwrites the old mapping.

        Args:
            var_name: The name of the ``ContextVar`` to register.
            binding_key: Optional explicit binding key override.

        Returns:
            The binding key assigned to *var_name*.
        """
        resolved_key = binding_key if binding_key is not None else f"cv:{var_name}"
        if var_name in self._var_registry:
            existing = self._var_registry[var_name]
            if existing != resolved_key:
                _log.warning(
                    "register_var: overwriting binding key for %r: %r -> %r.",
                    var_name, existing, resolved_key,
                )
        else:
            self._var_count += 1

        self._var_registry[var_name] = resolved_key
        return resolved_key

    # ──────────────────────────────────────────────────
    # Value management
    # ──────────────────────────────────────────────────

    def set_value(self, var_name: str, value: object) -> str:
        """Set the current value for a registered ContextVar.

        If *var_name* has not been registered yet, it is auto-registered with
        the default binding key before the value is stored.

        Args:
            var_name: The name of the ContextVar to set.
            value: The new value to associate with *var_name*.

        Returns:
            The binding key used to store the value.
        """
        if var_name not in self._var_registry:
            _log.debug("set_value: auto-registering var %r.", var_name)
            self.register_var(var_name)

        binding_key = self._var_registry[var_name]
        self._binding_values[binding_key] = value
        self._access_log.append(
            {
                "action": "set",
                "var_name": var_name,
                "binding_key": binding_key,
                "timestamp": time.time(),
            }
        )
        return binding_key

    def get_value(self, var_name: str) -> object | None:
        """Retrieve the stored value for *var_name*, or ``None`` if absent.

        Args:
            var_name: The name of the ContextVar to look up.

        Returns:
            The stored value, or ``None`` if *var_name* is not registered or
            has no value set.
        """
        binding_key = self._var_registry.get(var_name)
        value = self._binding_values.get(binding_key) if binding_key else None
        self._access_log.append(
            {
                "action": "get",
                "var_name": var_name,
                "binding_key": binding_key,
                "found": binding_key is not None and binding_key in self._binding_values,
                "timestamp": time.time(),
            }
        )
        return value

    # ──────────────────────────────────────────────────
    # Section integration
    # ──────────────────────────────────────────────────

    def snapshot_to_section(self, section: TaskLocalSection) -> TaskLocalSection:
        """Return a new section whose local_bindings include all bridge keys.

        The binding *keys* (not values — values are opaque and not stored in
        the sheaf section directly) are unioned into the section's
        ``local_bindings`` frozenset.

        Args:
            section: The base :class:`TaskLocalSection` to extend.

        Returns:
            A new :class:`TaskLocalSection` with all registered binding keys
            merged into ``local_bindings``.
        """
        all_keys = frozenset(self._var_registry.values())
        merged = section.local_bindings | all_keys
        return dataclasses.replace(section, local_bindings=merged, updated_at=time.time())

    def restore_from_section(self, section: TaskLocalSection) -> int:
        """Re-populate _binding_values from a section's local_bindings.

        Because section ``local_bindings`` contains only key names (not
        values), the restored entries are marked with the sentinel string
        ``'__restored__'``. This is sufficient to re-establish the key
        presence for downstream lookups.

        Args:
            section: The :class:`TaskLocalSection` to restore from.

        Returns:
            The number of binding keys restored.
        """
        restored = 0
        for key in section.local_bindings:
            if key not in self._binding_values:
                self._binding_values[key] = "__restored__"
                restored += 1
        _log.debug("restore_from_section: restored %d bindings.", restored)
        return restored

    # ──────────────────────────────────────────────────
    # Introspection
    # ──────────────────────────────────────────────────

    def list_vars(self) -> list[str]:
        """Return all registered ContextVar names.

        Returns:
            A sorted list of registered variable names.
        """
        return sorted(self._var_registry.keys())

    def binding_keys(self) -> list[str]:
        """Return all binding keys currently in use.

        Returns:
            A sorted list of binding key strings.
        """
        return sorted(self._var_registry.values())

    def access_count(self, var_name: str) -> int:
        """Return the total number of set/get accesses recorded for *var_name*.

        Args:
            var_name: The variable name to count accesses for.

        Returns:
            The number of log entries matching *var_name*.
        """
        return sum(1 for entry in self._access_log if entry.get("var_name") == var_name)

    def clear_var(self, var_name: str) -> bool:
        """Unregister *var_name* and remove its stored value.

        Args:
            var_name: The variable name to remove.

        Returns:
            ``True`` if *var_name* was registered and has been removed;
            ``False`` if it was not registered.
        """
        if var_name not in self._var_registry:
            return False
        binding_key = self._var_registry.pop(var_name)
        self._binding_values.pop(binding_key, None)
        _log.debug("clear_var: removed %r (binding_key=%r).", var_name, binding_key)
        return True


# ══════════════════════════════════════════════════════
# SectionInheritanceEngine
# ══════════════════════════════════════════════════════


class SectionInheritanceEngine:
    """Handles section inheritance when child tasks are spawned.

    When a new asyncio task is created, it inherits a copy of the parent's
    context (via contextvars). In the sheaf-theoretic model, this inheritance
    is a *restriction morphism*: the child's section is the parent's section
    restricted to the child's coordinate, with possible new bindings added.

    The engine enforces that child sections are always sub-sections of parent
    sections — they may refine bindings but may not contradict inherited ones.

    Internal state:

    * ``_inheritance_map`` — maps child ``section_id`` to parent ``section_id``.
    * ``_restriction_log`` — records every restriction event with timestamps.
    * ``_conflict_log`` — records detected binding conflicts for audit.

    Theory reference: Ch24 §1 — section inheritance and restriction morphisms.
    """

    def __init__(self) -> None:
        """Initialise an empty SectionInheritanceEngine."""
        self._inheritance_map: dict[str, str] = {}           # child_id -> parent_id
        self._restriction_log: list[dict[str, object]] = []
        self._conflict_log: list[dict[str, object]] = []

    # ──────────────────────────────────────────────────
    # Inheritance recording
    # ──────────────────────────────────────────────────

    def inherit(
        self,
        child_section: TaskLocalSection,
        parent_section: TaskLocalSection,
    ) -> TaskLocalSection:
        """Record the parent→child inheritance relationship.

        Validates that the child's ``parent_section_id`` field matches the
        supplied *parent_section*. If they disagree the discrepancy is logged
        but not raised (the call site is authoritative here).

        Args:
            child_section: The newly created child section.
            parent_section: The parent section from which the child inherits.

        Returns:
            The *child_section* unchanged (inheritance is captured in the
            ``_inheritance_map``).
        """
        if (
            child_section.parent_section_id is not None
            and child_section.parent_section_id != parent_section.section_id
        ):
            _log.warning(
                "inherit: child.parent_section_id=%r does not match "
                "parent.section_id=%r — proceeding with supplied parent.",
                child_section.parent_section_id,
                parent_section.section_id,
            )

        self._inheritance_map[child_section.section_id] = parent_section.section_id
        self._restriction_log.append(
            {
                "event": "inherit",
                "child_id": child_section.section_id,
                "parent_id": parent_section.section_id,
                "child_task": child_section.task_id,
                "parent_task": parent_section.task_id,
                "timestamp": time.time(),
            }
        )
        return child_section

    # ──────────────────────────────────────────────────
    # Binding analysis
    # ──────────────────────────────────────────────────

    def compute_inherited_bindings(
        self,
        child_section: TaskLocalSection,
        parent_section: TaskLocalSection,
    ) -> frozenset[str]:
        """Compute the effective bindings for a child after inheritance.

        The child inherits all of the parent's ``local_bindings`` and may
        add its own. The effective binding set is the union.

        Args:
            child_section: The child :class:`TaskLocalSection`.
            parent_section: The parent :class:`TaskLocalSection`.

        Returns:
            A :class:`frozenset` of all inherited + new binding keys.
        """
        inherited = parent_section.local_bindings | child_section.local_bindings
        _log.debug(
            "compute_inherited_bindings: parent=%d, child_extra=%d, total=%d.",
            len(parent_section.local_bindings),
            len(child_section.local_bindings - parent_section.local_bindings),
            len(inherited),
        )
        return inherited

    def check_binding_conflict(
        self,
        child_section: TaskLocalSection,
        parent_section: TaskLocalSection,
    ) -> list[str]:
        """Identify bindings that appear in both parent and child.

        A binding key present in *both* parent and child is reported as a
        potential conflict (the child is re-asserting a binding that was
        inherited). In a well-formed section graph this should be rare; when
        it occurs it is recorded in ``_conflict_log``.

        Args:
            child_section: The child :class:`TaskLocalSection`.
            parent_section: The parent :class:`TaskLocalSection`.

        Returns:
            A list of binding key strings that appear in both sections.
        """
        overlap = list(child_section.local_bindings & parent_section.local_bindings)
        if overlap:
            self._conflict_log.append(
                {
                    "event": "binding_overlap",
                    "child_id": child_section.section_id,
                    "parent_id": parent_section.section_id,
                    "overlapping_keys": overlap,
                    "timestamp": time.time(),
                }
            )
            _log.debug(
                "check_binding_conflict: %d overlapping keys detected.", len(overlap)
            )
        return overlap

    # ──────────────────────────────────────────────────
    # Restriction
    # ──────────────────────────────────────────────────

    def restrict_to_child(
        self,
        parent_section: TaskLocalSection,
        child_task_id: str,
        child_task_name: str,
        additional_bindings: frozenset[str] | None = None,
    ) -> TaskLocalSection:
        """Create a new child section as a restriction of *parent_section*.

        The child section:
        * Has a new UUID ``section_id``.
        * References ``parent_section.section_id`` as its parent.
        * Inherits a subset of the parent's ``support_keys`` (here the full
          set is inherited; refinement is left to the caller).
        * Extends the parent's provenance tuple with ``'inherited'``.
        * Merges *additional_bindings* into ``local_bindings``.

        Args:
            parent_section: The section to restrict from.
            child_task_id: Task id for the new child section.
            child_task_name: Human-readable name for the new child task.
            additional_bindings: Extra binding keys to add beyond inherited.

        Returns:
            A freshly created :class:`TaskLocalSection` representing the
            restricted child section.
        """
        extra = additional_bindings or frozenset()
        now = time.time()
        child_section = dataclasses.replace(
            parent_section,
            section_id=uuid.uuid4().hex,
            task_id=child_task_id,
            task_name=child_task_name,
            parent_section_id=parent_section.section_id,
            local_bindings=parent_section.local_bindings | extra,
            provenance=parent_section.provenance + ("inherited",),
            scope_status=ScopeStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        )

        self._inheritance_map[child_section.section_id] = parent_section.section_id
        self._restriction_log.append(
            {
                "event": "restrict",
                "child_id": child_section.section_id,
                "parent_id": parent_section.section_id,
                "additional_bindings": sorted(extra),
                "timestamp": now,
            }
        )
        return child_section

    # ──────────────────────────────────────────────────
    # Tree traversal
    # ──────────────────────────────────────────────────

    def lineage(self, section_id: str) -> list[str]:
        """Return the ancestry chain from the root down to *section_id*.

        Traverses the ``_inheritance_map`` walking upward until a section
        with no registered parent is reached. The result is reversed so
        the root is first and *section_id* is last.

        Args:
            section_id: The leaf section to start from.

        Returns:
            An ordered list of section ids from root to *section_id*.
            Stops at depth 50 to guard against cycles.
        """
        chain: list[str] = []
        current = section_id
        for _ in range(50):
            chain.append(current)
            parent = self._inheritance_map.get(current)
            if parent is None:
                break
            current = parent
        chain.reverse()
        return chain

    def depth(self, section_id: str) -> int:
        """Return the depth of *section_id* in the inheritance tree.

        The root section has depth 0.

        Args:
            section_id: The section whose depth is requested.

        Returns:
            An integer depth (0 for root, N for N levels below root).
        """
        return len(self.lineage(section_id)) - 1

    def is_descendant(self, section_id: str, ancestor_id: str) -> bool:
        """Return whether *ancestor_id* appears in the lineage of *section_id*.

        Args:
            section_id: The potential descendant section.
            ancestor_id: The potential ancestor section.

        Returns:
            ``True`` if *ancestor_id* is an ancestor of *section_id*.
        """
        return ancestor_id in self.lineage(section_id)

    def siblings(self, section_id: str) -> list[str]:
        """Return the other sections that share *section_id*'s parent.

        A section with no registered parent has no siblings in this engine
        (even if other root sections exist).

        Args:
            section_id: The section whose siblings are requested.

        Returns:
            A list of section ids that share the same parent, excluding
            *section_id* itself.
        """
        parent = self._inheritance_map.get(section_id)
        if parent is None:
            return []
        return [
            child
            for child, p in self._inheritance_map.items()
            if p == parent and child != section_id
        ]


# ══════════════════════════════════════════════════════
# TaskSectionCleanup
# ══════════════════════════════════════════════════════


class TaskSectionCleanup:
    """Handles cleanup of task-local sections on task completion.

    When a task completes (successfully, with an exception, or via
    cancellation), its task-local section must be properly cleaned up. In
    the sheaf-theoretic model, this means either:

    * **Vacuous satisfaction** — if the task completed successfully, all
      pending obligations in the section are trivially discharged.
    * **Obstruction recording** — if the task was cancelled, an obstruction
      cohomology class is injected into the section record.
    * **Failure recording** — if the task failed with an exception, the
      failure is recorded in the section's provenance.

    Sections are never silently dropped. Every section that reaches an
    end-state passes through this cleanup machinery.

    The ``_cleanup_policy`` dict maps ``ScopeStatus`` string values to
    action names. Callers may override the policy before scheduling
    cleanups.

    Theory reference: Ch24 §1 — section cleanup guarantees
    (THEOREM_SCOPE_SECTION_CLEANUP).
    """

    def __init__(self) -> None:
        """Initialise a TaskSectionCleanup with default policy."""
        self._pending_cleanups: list[str] = []
        self._completed_cleanups: list[dict[str, object]] = []
        self._failed_cleanups: list[dict[str, object]] = []
        self._cleanup_policy: dict[str, str] = {
            str(ScopeStatus.COMPLETED): "vacuous_satisfaction",
            str(ScopeStatus.CANCELLED): "obstruction_recording",
            str(ScopeStatus.FAILED): "failure_recording",
            str(ScopeStatus.ACTIVE): "noop",
        }

    # ──────────────────────────────────────────────────
    # Scheduling
    # ──────────────────────────────────────────────────

    def schedule_cleanup(self, section_id: str) -> None:
        """Enqueue *section_id* for cleanup.

        Re-enqueueing an already-pending section is a no-op.

        Args:
            section_id: The section to schedule for cleanup.
        """
        if section_id not in self._pending_cleanups:
            self._pending_cleanups.append(section_id)

    # ──────────────────────────────────────────────────
    # Execution
    # ──────────────────────────────────────────────────

    def execute_cleanup(self, section: TaskLocalSection) -> dict[str, object]:
        """Execute the appropriate cleanup action for *section*.

        Consults ``_cleanup_policy`` to decide which action to take based on
        the section's current ``scope_status``.  The section_id is removed
        from ``_pending_cleanups`` if present.

        Args:
            section: The :class:`TaskLocalSection` to clean up.

        Returns:
            A result dict with at minimum the keys ``section_id``,
            ``action``, ``status``, and ``timestamp``.
        """
        # Remove from pending if present.
        if section.section_id in self._pending_cleanups:
            self._pending_cleanups.remove(section.section_id)

        action = self._cleanup_policy.get(str(section.scope_status), "noop")

        if action == "vacuous_satisfaction":
            result = self.vacuously_satisfy(section)
        elif action == "obstruction_recording":
            result = self.record_obstruction(section)
        elif action == "failure_recording":
            result = {
                "section_id": section.section_id,
                "action": "failure_recording",
                "task_id": section.task_id,
                "status": "recorded",
                "timestamp": time.time(),
            }
            self._failed_cleanups.append(result)
        else:
            result = {
                "section_id": section.section_id,
                "action": "noop",
                "status": "skipped",
                "timestamp": time.time(),
            }

        self._completed_cleanups.append(result)
        return result

    def batch_cleanup(
        self, sections: list[TaskLocalSection]
    ) -> list[dict[str, object]]:
        """Execute cleanup for each section in *sections*.

        Args:
            sections: A list of :class:`TaskLocalSection` instances to clean.

        Returns:
            A list of result dicts, one per section in order.
        """
        return [self.execute_cleanup(s) for s in sections]

    # ──────────────────────────────────────────────────
    # Actions
    # ──────────────────────────────────────────────────

    def vacuously_satisfy(self, section: TaskLocalSection) -> dict[str, object]:
        """Produce a vacuous-satisfaction record for a completed section.

        In the sheaf model, a section that completed without obligation
        failure satisfies all its pending judgments vacuously — there are
        no outstanding cohomology classes to discharge.

        Args:
            section: The successfully completed :class:`TaskLocalSection`.

        Returns:
            A result dict documenting the vacuous satisfaction.
        """
        return {
            "section_id": section.section_id,
            "action": "vacuous_satisfaction",
            "task_id": section.task_id,
            "bindings_discharged": len(section.local_bindings),
            "status": "ok",
            "timestamp": time.time(),
        }

    def record_obstruction(self, section: TaskLocalSection) -> dict[str, object]:
        """Produce an obstruction record for a cancelled section.

        The obstruction key encodes the section id so it is globally unique
        and traceable back to its origin.

        Args:
            section: The cancelled :class:`TaskLocalSection`.

        Returns:
            A result dict documenting the obstruction injection.
        """
        obstruction_key = f"cleanup:{section.section_id}"
        return {
            "section_id": section.section_id,
            "action": "obstruction_recorded",
            "task_id": section.task_id,
            "obstruction_key": obstruction_key,
            "status": "recorded",
            "timestamp": time.time(),
        }

    # ──────────────────────────────────────────────────
    # Introspection
    # ──────────────────────────────────────────────────

    def pending_count(self) -> int:
        """Return the number of sections awaiting cleanup.

        Returns:
            An integer count of pending section ids.
        """
        return len(self._pending_cleanups)

    def completed_count(self) -> int:
        """Return the total number of cleanups executed so far.

        Returns:
            An integer count of completed cleanup records.
        """
        return len(self._completed_cleanups)

    def cleanup_report(self) -> dict[str, object]:
        """Return a summary report of cleanup activity.

        Returns:
            A dict with keys ``pending``, ``completed``, ``failed``,
            ``actions_by_type``, and ``timestamp``.
        """
        actions: dict[str, int] = {}
        for entry in self._completed_cleanups:
            a = str(entry.get("action", "unknown"))
            actions[a] = actions.get(a, 0) + 1

        return {
            "pending": len(self._pending_cleanups),
            "completed": len(self._completed_cleanups),
            "failed": len(self._failed_cleanups),
            "actions_by_type": actions,
            "timestamp": time.time(),
        }

    def clear_completed(self) -> int:
        """Discard all completed cleanup records and return the count removed.

        Returns:
            The number of completed cleanup records that were removed.
        """
        count = len(self._completed_cleanups)
        self._completed_cleanups.clear()
        return count


# ══════════════════════════════════════════════════════
# Module-level helper functions
# ══════════════════════════════════════════════════════


def create_section_for_task(
    task_id: str,
    task_name: str,
    parent_id: str | None = None,
) -> TaskLocalSection:
    """Create a :class:`TaskLocalSection` for *task_id* without a manager.

    This is a lightweight factory wrapper around :func:`make_task_section`
    for callers that do not need the full lifecycle machinery of
    :class:`TaskSectionManager`.

    Args:
        task_id: Unique identifier for the task.
        task_name: Human-readable task name.
        parent_id: Optional parent section id (not parent task id).

    Returns:
        A freshly created :class:`TaskLocalSection` in ACTIVE status.
    """
    return make_task_section(
        task_id=task_id,
        task_name=task_name,
        parent_section_id=parent_id,
    )


def section_summary(section: TaskLocalSection) -> str:
    """Return a concise one-line summary of *section*.

    Args:
        section: The :class:`TaskLocalSection` to summarise.

    Returns:
        A human-readable string suitable for logging or debug output.
    """
    binding_count = len(section.local_bindings)
    return (
        f"Section({section.section_id[:8]}…"
        f" task={section.task_name!r}"
        f" status={section.scope_status}"
        f" bindings={binding_count}"
        f" parent={'yes' if section.parent_section_id else 'none'})"
    )


# ══════════════════════════════════════════════════════
# Public API
# ══════════════════════════════════════════════════════

__all__ = [
    "TaskSectionManager",
    "ContextVarBridge",
    "SectionInheritanceEngine",
    "TaskSectionCleanup",
    "create_section_for_task",
    "section_summary",
]

# copilot: shared-core marker for future LLM orchestration.
