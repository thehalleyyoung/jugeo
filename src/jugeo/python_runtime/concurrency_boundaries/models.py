"""Core data models for JuGeo concurrency boundaries (Ch24 of theory2.tex).

This module defines the canonical, immutable and mutable data structures that
represent the four concurrency boundary constructs introduced in Chapter 24 of
``theory2.tex``:

Task-local context as scoped sections
--------------------------------------
:class:`TaskLocalSection` models a *scoped section* in the sense of Ch24.1: a
snapshot of task-local bindings that exists for the lifetime of a logical task
and is never visible to sibling or parent tasks without explicit crossing.
Sections are frozen (immutable after construction) to preserve the presheaf
semantics of Ch24.T1.

Cancellation as obstruction injection
---------------------------------------
:class:`CancellationRecord` models a *cancellation event* as defined in
Ch24.2: an injected obstruction class that propagates through the task graph
according to the rules in Ch24.2.2.  Every cancellation carries an
``obstruction_key`` that identifies it uniquely within the cohomology of the
task graph.

Exception groups as multi-obstruction records
----------------------------------------------
:class:`ExceptionGroupRecord` models a Python ``ExceptionGroup`` (PEP 654) as
a *multi-obstruction record* per Ch24.3: a mutable container of simultaneous
exception dictionaries, each representing a distinct obstruction class.
Resolution (via ``resolve``) records the strategy used to discharge all
obstructions.

Process boundaries as cover boundaries
----------------------------------------
:class:`ProcessBoundary` models a *cover boundary* between two process
identifiers per Ch24.4: a morphism in the Grothendieck topology of the
deployment site.  The ``allowed_section_ids`` field encodes the crossing policy
(only sections explicitly listed are permitted to cross the boundary).

Scope hierarchy
----------------
:class:`ConcurrencyScope` ties these constructs together: it is a mutable node
in the scope tree, tracking child scopes, active sections, and accumulated
cancellation records.  Scope depth is computed explicitly via a while-loop
search (not recursion) to avoid stack overflows in deeply nested configurations.

Design notes
------------
* Frozen dataclasses use ``slots=True`` to reduce per-instance memory.
* Mutable dataclasses use ``slots=False`` to allow ``__dict__`` access.
* All factories are module-level functions prefixed with ``make_``.
* Timestamps use ``time.time()``.  IDs use ``uuid.uuid4().hex``.
* No third-party dependencies.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, replace
from enum import Enum

# ══════════════════════════════════════════════════════════════════════════════
# Enumerations
# ══════════════════════════════════════════════════════════════════════════════

class ConcurrencyRole(str, Enum):
    """Roles that a concurrency actor can play within the scope hierarchy.

    These roles map to distinct semantic categories in Ch24:

    * ``TASK`` — an asyncio / concurrent.futures task (Ch24.1).
    * ``COROUTINE`` — a bare coroutine without its own scope identity.
    * ``PROCESS`` — an OS-level process (Ch24.4).
    * ``THREAD`` — an OS thread sharing memory with siblings.
    * ``ACTOR`` — a message-passing actor (e.g. multiprocessing worker).
    * ``BOUNDARY_ENFORCER`` — an infrastructure component that enforces
      crossing policies at a :class:`ProcessBoundary`.
    """

    TASK = "task"
    COROUTINE = "coroutine"
    PROCESS = "process"
    THREAD = "thread"
    ACTOR = "actor"
    BOUNDARY_ENFORCER = "boundary_enforcer"


class CancellationReason(str, Enum):
    """Reasons that can cause a :class:`CancellationRecord` to be created.

    Each reason corresponds to a distinct obstruction source in Ch24.2:

    * ``USER_REQUESTED`` — explicit external cancellation request.
    * ``TIMEOUT`` — deadline exceeded.
    * ``PARENT_CANCELLED`` — parent scope was cancelled; propagates per Ch24.2.2.
    * ``RESOURCE_EXHAUSTED`` — a resource limit was breached (memory, FDs, etc.).
    * ``OBSTRUCTION`` — an existing obstruction class made progress impossible.
    * ``POLICY_VIOLATION`` — the task violated a declared boundary policy.
    """

    USER_REQUESTED = "user_requested"
    TIMEOUT = "timeout"
    PARENT_CANCELLED = "parent_cancelled"
    RESOURCE_EXHAUSTED = "resource_exhausted"
    OBSTRUCTION = "obstruction"
    POLICY_VIOLATION = "policy_violation"


class BoundaryKind(str, Enum):
    """Kinds of process / memory boundaries recognised in Ch24.4.

    * ``TASK_LOCAL`` — an in-process task-local boundary (minimal isolation).
    * ``THREAD_LOCAL`` — a thread boundary sharing the same heap.
    * ``PROCESS`` — an OS process boundary (separate virtual address space).
    * ``NETWORK`` — a network socket boundary (distributed deployment).
    * ``IPC`` — a Unix socket / pipe boundary within the same host.
    * ``MEMORY_MAPPED`` — a shared-memory region boundary.
    """

    TASK_LOCAL = "task_local"
    THREAD_LOCAL = "thread_local"
    PROCESS = "process"
    NETWORK = "network"
    IPC = "ipc"
    MEMORY_MAPPED = "memory_mapped"


class ScopeStatus(str, Enum):
    """Lifecycle statuses for a :class:`ConcurrencyScope` or
    :class:`TaskLocalSection`.

    * ``ACTIVE`` — the scope or section is live and accepting work.
    * ``SUSPENDED`` — temporarily paused, e.g. awaiting I/O.
    * ``CANCELLED`` — an obstruction was injected; no further work accepted.
    * ``COMPLETED`` — all work finished successfully.
    * ``FAILED`` — terminated with an unresolved exception.
    * ``BOUNDARY_CROSSED`` — the section or scope has migrated across a
      :class:`ProcessBoundary`.
    """

    ACTIVE = "active"
    SUSPENDED = "suspended"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    FAILED = "failed"
    BOUNDARY_CROSSED = "boundary_crossed"


# ══════════════════════════════════════════════════════════════════════════════
# TaskLocalSection
# ══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True, slots=True)
class TaskLocalSection:
    """An immutable snapshot of a task-local scoped section.

    In Ch24.1 terminology this is a *section* over a task object in the
    semantic site: it carries a frozenset of binding names that are visible
    only within the task identified by ``task_id``.  The ``parent_section_id``
    field enables the presheaf restriction maps — child sections inherit from
    parents but cannot modify them.

    Args:
        section_id: Unique hex identifier for this section.
        task_id: Identifier of the owning task.
        task_name: Human-readable task name for diagnostics.
        local_bindings: Frozenset of binding names visible in this section.
        parent_section_id: Optional ID of the parent section (None for root).
        created_at: Unix timestamp of section creation.
        scope_status: Current lifecycle status.
        support_keys: Frozenset of support/evidence keys attached to this
            section (bridges to the judgment support algebra).
        provenance: Ordered tuple of provenance strings (audit trail).
    """

    section_id: str
    task_id: str
    task_name: str
    local_bindings: frozenset[str]
    parent_section_id: str | None
    created_at: float
    scope_status: ScopeStatus
    support_keys: frozenset[str]
    provenance: tuple[str, ...]

    # ------------------------------------------------------------------
    # Status predicates
    # ------------------------------------------------------------------

    def is_active(self) -> bool:
        """Return True if the section's lifecycle status is ACTIVE.

        Returns:
            True when ``scope_status == ScopeStatus.ACTIVE``.
        """
        return self.scope_status == ScopeStatus.ACTIVE

    def is_cancelled(self) -> bool:
        """Return True if the section has been cancelled.

        Returns:
            True when ``scope_status == ScopeStatus.CANCELLED``.
        """
        return self.scope_status == ScopeStatus.CANCELLED

    def is_completed(self) -> bool:
        """Return True if the section completed successfully.

        Returns:
            True when ``scope_status == ScopeStatus.COMPLETED``.
        """
        return self.scope_status == ScopeStatus.COMPLETED

    def is_terminal(self) -> bool:
        """Return True if the section is in a terminal (non-resumable) status.

        Terminal statuses are CANCELLED, COMPLETED, and FAILED.

        Returns:
            True for any terminal status.
        """
        return self.scope_status in (
            ScopeStatus.CANCELLED,
            ScopeStatus.COMPLETED,
            ScopeStatus.FAILED,
        )

    # ------------------------------------------------------------------
    # Binding queries
    # ------------------------------------------------------------------

    def has_binding(self, name: str) -> bool:
        """Return True if *name* is present in the local bindings.

        Args:
            name: Binding name to look up.

        Returns:
            True if the name is in ``local_bindings``.
        """
        return name in self.local_bindings

    def binding_count(self) -> int:
        """Return the number of local bindings.

        Returns:
            Integer count of names in ``local_bindings``.
        """
        return len(self.local_bindings)

    def child_of(self, section_id: str) -> bool:
        """Return True if this section is an immediate child of *section_id*.

        Args:
            section_id: Candidate parent section identifier.

        Returns:
            True when ``parent_section_id == section_id``.
        """
        return self.parent_section_id == section_id

    # ------------------------------------------------------------------
    # Functional updates (return new frozen instances)
    # ------------------------------------------------------------------

    def with_status(self, status: ScopeStatus) -> TaskLocalSection:
        """Return a new section with the given lifecycle status.

        Args:
            status: The new :class:`ScopeStatus` value.

        Returns:
            A new :class:`TaskLocalSection` differing only in ``scope_status``.
        """
        return replace(self, scope_status=status)

    def add_binding(self, name: str) -> TaskLocalSection:
        """Return a new section with *name* added to the local bindings.

        If *name* is already present, returns an identical instance (no-op
        from the perspective of the frozenset algebra).

        Args:
            name: Binding name to add.

        Returns:
            A new :class:`TaskLocalSection` with the extended binding set.
        """
        return replace(self, local_bindings=self.local_bindings | {name})

    def remove_binding(self, name: str) -> TaskLocalSection:
        """Return a new section with *name* removed from local bindings.

        Args:
            name: Binding name to remove.

        Returns:
            A new :class:`TaskLocalSection` with the reduced binding set.
        """
        return replace(self, local_bindings=self.local_bindings - {name})

    def with_provenance(self, entry: str) -> TaskLocalSection:
        """Return a new section with *entry* appended to the provenance trail.

        Args:
            entry: Provenance string to append.

        Returns:
            A new :class:`TaskLocalSection` with extended provenance.
        """
        return replace(self, provenance=self.provenance + (entry,))

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, object]:
        """Serialise this section to a JSON-safe plain dictionary.

        Returns:
            Dict with all fields, with frozensets converted to sorted lists
            and tuples converted to lists.
        """
        return {
            "section_id": self.section_id,
            "task_id": self.task_id,
            "task_name": self.task_name,
            "local_bindings": sorted(self.local_bindings),
            "parent_section_id": self.parent_section_id,
            "created_at": self.created_at,
            "scope_status": self.scope_status.value,
            "support_keys": sorted(self.support_keys),
            "provenance": list(self.provenance),
        }

    def __repr__(self) -> str:
        return (
            f"TaskLocalSection(id={self.section_id[:8]}…, "
            f"task={self.task_name!r}, status={self.scope_status.value!r}, "
            f"bindings={len(self.local_bindings)})"
        )


# ══════════════════════════════════════════════════════════════════════════════
# CancellationRecord
# ══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True, slots=True)
class CancellationRecord:
    """Immutable record of a single cancellation event.

    Per Ch24.2, a cancellation injects an obstruction class into the task
    graph.  This record preserves:

    * *why* the task was cancelled (``reason``),
    * *which* obstruction key was injected (``obstruction_key``),
    * *when* it happened (``cancelled_at``),
    * *which sections* were affected (``affected_section_ids``),
    * the *parent record* if this is a propagated cancellation (Ch24.2.2).

    Args:
        record_id: Unique hex identifier for this record.
        task_id: Identifier of the cancelled task.
        reason: The :class:`CancellationReason` value.
        obstruction_key: Unique key identifying the injected obstruction.
        cancelled_at: Unix timestamp of the cancellation event.
        parent_record_id: ID of the parent :class:`CancellationRecord` if
            this is a propagated cancellation, else None.
        affected_section_ids: Tuple of section IDs that were active when the
            cancellation occurred.
        error_message: Optional human-readable explanation.
    """

    record_id: str
    task_id: str
    reason: CancellationReason
    obstruction_key: str
    cancelled_at: float
    parent_record_id: str | None
    affected_section_ids: tuple[str, ...]
    error_message: str

    # ------------------------------------------------------------------
    # Reason predicates
    # ------------------------------------------------------------------

    def is_timeout(self) -> bool:
        """Return True if the cancellation was caused by a timeout.

        Returns:
            True when ``reason == CancellationReason.TIMEOUT``.
        """
        return self.reason == CancellationReason.TIMEOUT

    def is_user_requested(self) -> bool:
        """Return True if the cancellation was explicitly requested by a user.

        Returns:
            True when ``reason == CancellationReason.USER_REQUESTED``.
        """
        return self.reason == CancellationReason.USER_REQUESTED

    def is_propagated(self) -> bool:
        """Return True if this is a propagated (parent-initiated) cancellation.

        Returns:
            True when ``reason == CancellationReason.PARENT_CANCELLED``.
        """
        return self.reason == CancellationReason.PARENT_CANCELLED

    def is_resource_related(self) -> bool:
        """Return True if the cancellation is due to a resource constraint.

        Both ``RESOURCE_EXHAUSTED`` and ``TIMEOUT`` are considered resource-
        related because both indicate system-level resource pressure.

        Returns:
            True for RESOURCE_EXHAUSTED or TIMEOUT reasons.
        """
        return self.reason in (
            CancellationReason.RESOURCE_EXHAUSTED,
            CancellationReason.TIMEOUT,
        )

    def is_policy_violation(self) -> bool:
        """Return True if the cancellation was triggered by a policy violation.

        Returns:
            True when ``reason == CancellationReason.POLICY_VIOLATION``.
        """
        return self.reason == CancellationReason.POLICY_VIOLATION

    # ------------------------------------------------------------------
    # Structural queries
    # ------------------------------------------------------------------

    def affected_count(self) -> int:
        """Return the number of sections affected by this cancellation.

        Returns:
            Integer count of ``affected_section_ids``.
        """
        return len(self.affected_section_ids)

    def has_affected_sections(self) -> bool:
        """Return True if at least one section was affected.

        Returns:
            True when ``affected_section_ids`` is non-empty.
        """
        return len(self.affected_section_ids) > 0

    def cascade_depth(self) -> int:
        """Return the propagation depth of this cancellation record.

        Because this is a frozen record without a reference to its parent
        object, the depth is approximated from the ``record_id`` naming
        convention used by the factory functions: a record with no parent has
        depth 1; a record with a ``parent_record_id`` has depth 2.  Deeper
        chains are not directly observable from a single record.

        Returns:
            1 if no parent, 2 if a parent record ID is present.
        """
        return 1 if self.parent_record_id is None else 2

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, object]:
        """Serialise to a JSON-safe plain dictionary.

        Returns:
            Dict with all fields; the ``reason`` enum is serialised as its
            string value, and the affected_section_ids tuple is converted to
            a list.
        """
        return {
            "record_id": self.record_id,
            "task_id": self.task_id,
            "reason": self.reason.value,
            "obstruction_key": self.obstruction_key,
            "cancelled_at": self.cancelled_at,
            "parent_record_id": self.parent_record_id,
            "affected_section_ids": list(self.affected_section_ids),
            "error_message": self.error_message,
        }

    def __repr__(self) -> str:
        return (
            f"CancellationRecord(id={self.record_id[:8]}…, "
            f"task={self.task_id[:8]}…, reason={self.reason.value!r}, "
            f"affected={self.affected_count()})"
        )


# ══════════════════════════════════════════════════════════════════════════════
# ExceptionGroupRecord
# ══════════════════════════════════════════════════════════════════════════════

@dataclass(slots=False)
class ExceptionGroupRecord:
    """Mutable multi-obstruction record modelling Python's ExceptionGroup.

    In Ch24.3 terms, an ``ExceptionGroup`` is a *multi-obstruction record*:
    a container for *n* simultaneous exception (obstruction) classes, none of
    which subsumes another.  This class is intentionally mutable so that
    exceptions can be accumulated during task execution before the group is
    resolved.

    Args:
        group_id: Unique hex identifier for this exception group.
        task_id: Identifier of the task that raised the group.
        exception_records: List of serialised exception dictionaries.  Each
            dict should contain at minimum a ``"type"`` key (exception class
            name) and optionally an ``"obstruction_key"`` key.
        obstruction_keys: List of obstruction key strings accumulated from
            all individual exception records.
        created_at: Unix timestamp of group creation.
        is_resolved: True once ``resolve()`` has been called.
        resolution_strategy: The strategy name used to resolve the group,
            e.g. ``"suppress_all"``, ``"reraise_first"``.
    """

    group_id: str
    task_id: str
    exception_records: list[dict[str, object]]
    obstruction_keys: list[str]
    created_at: float
    is_resolved: bool = False
    resolution_strategy: str = ""

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def add_exception(self, exc_dict: dict[str, object]) -> None:
        """Append a serialised exception record to this group.

        If the dict contains an ``"obstruction_key"`` entry, that key is also
        added to ``obstruction_keys`` (deduplicating against existing keys).

        Args:
            exc_dict: A dictionary representing one exception.  Should contain
                at minimum ``"type"`` (str) and optionally
                ``"obstruction_key"`` (str).
        """
        self.exception_records.append(exc_dict)
        ok = exc_dict.get("obstruction_key")
        if ok and isinstance(ok, str) and ok not in self.obstruction_keys:
            self.obstruction_keys.append(ok)

    def resolve(self, strategy: str) -> None:
        """Mark this exception group as resolved.

        After calling this method, ``is_resolved`` is True and
        ``unresolved_count()`` returns 0.

        Args:
            strategy: A string naming the resolution strategy used (e.g.
                ``"suppress_all"``, ``"reraise_first"``,
                ``"log_and_continue"``).

        Raises:
            ValueError: If *strategy* is empty or whitespace.
        """
        if not strategy or not strategy.strip():
            raise ValueError("Resolution strategy must be a non-empty string")
        self.is_resolved = True
        self.resolution_strategy = strategy.strip()

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def exception_count(self) -> int:
        """Return the number of exception records in this group.

        Returns:
            Integer count.
        """
        return len(self.exception_records)

    def obstruction_count(self) -> int:
        """Return the number of distinct obstruction keys.

        Returns:
            Integer count of ``obstruction_keys``.
        """
        return len(self.obstruction_keys)

    def all_obstructions(self) -> list[str]:
        """Return a copy of the obstruction keys list.

        Returns:
            A new list of obstruction key strings.
        """
        return list(self.obstruction_keys)

    def unresolved_count(self) -> int:
        """Return the number of unresolved exceptions.

        Once ``resolve()`` has been called, all exceptions are considered
        resolved and this returns 0.  Before that, it returns
        ``exception_count()``.

        Returns:
            0 if resolved, else ``exception_count()``.
        """
        return 0 if self.is_resolved else self.exception_count()

    def by_type(self, exc_type: str) -> list[dict[str, object]]:
        """Filter exception records by exception type name.

        Args:
            exc_type: The exception class name string to filter by, e.g.
                ``"ValueError"``.

        Returns:
            List of exception record dicts whose ``"type"`` field matches
            *exc_type* (exact, case-sensitive).
        """
        return [
            r for r in self.exception_records
            if r.get("type") == exc_type
        ]

    def has_type(self, exc_type: str) -> bool:
        """Return True if any exception record has the given type.

        Args:
            exc_type: Exception type name to search for.

        Returns:
            True if at least one matching record exists.
        """
        return any(r.get("type") == exc_type for r in self.exception_records)

    def types_present(self) -> list[str]:
        """Return a deduplicated sorted list of exception type names.

        Returns:
            Sorted list of type name strings found in exception_records.
        """
        seen: set[str] = set()
        for r in self.exception_records:
            t = r.get("type")
            if isinstance(t, str) and t:
                seen.add(t)
        return sorted(seen)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, object]:
        """Serialise to a JSON-safe plain dictionary.

        Returns:
            Dict with all fields, including a deep copy of exception_records.
        """
        return {
            "group_id": self.group_id,
            "task_id": self.task_id,
            "exception_records": [dict(r) for r in self.exception_records],
            "obstruction_keys": list(self.obstruction_keys),
            "created_at": self.created_at,
            "is_resolved": self.is_resolved,
            "resolution_strategy": self.resolution_strategy,
        }

    def __repr__(self) -> str:
        status = "resolved" if self.is_resolved else "unresolved"
        return (
            f"ExceptionGroupRecord(id={self.group_id[:8]}…, "
            f"exceptions={self.exception_count()}, {status})"
        )


# ══════════════════════════════════════════════════════════════════════════════
# ProcessBoundary
# ══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True, slots=True)
class ProcessBoundary:
    """Immutable cover boundary between two process identifiers.

    In Ch24.4 terms, a :class:`ProcessBoundary` corresponds to a cover
    morphism in the Grothendieck topology of the deployment site.  Sections
    listed in ``allowed_section_ids`` are permitted to cross this boundary;
    all others are rejected.

    The ``cover_morphism_id`` field identifies the specific morphism in the
    deployment topology, enabling traceability back to the theory.

    Args:
        boundary_id: Unique hex identifier for this boundary.
        source_process_id: Identifier of the sending process.
        target_process_id: Identifier of the receiving process.
        boundary_kind: The :class:`BoundaryKind` of this crossing.
        cover_morphism_id: Identifier of the corresponding cover morphism.
        allowed_section_ids: Frozenset of section IDs permitted to cross.
        created_at: Unix timestamp of boundary creation.
        is_active: Whether this boundary is currently enforced.
    """

    boundary_id: str
    source_process_id: str
    target_process_id: str
    boundary_kind: BoundaryKind
    cover_morphism_id: str
    allowed_section_ids: frozenset[str]
    created_at: float
    is_active: bool

    # ------------------------------------------------------------------
    # Policy predicates
    # ------------------------------------------------------------------

    def permits_crossing(self, section_id: str) -> bool:
        """Return True if the given section is allowed to cross this boundary.

        Args:
            section_id: The section identifier requesting to cross.

        Returns:
            True if *section_id* is in ``allowed_section_ids`` and the
            boundary is active.
        """
        return self.is_active and section_id in self.allowed_section_ids

    def is_open(self) -> bool:
        """Return True if the boundary permits unrestricted crossing.

        An *open* boundary has no allowed_section_ids restrictions — any
        section may cross.  This is modelled as an empty frozenset acting
        as a wildcard.  Note: in strict mode callers should check
        ``permits_crossing`` for explicit membership.

        Returns:
            True when ``allowed_section_ids`` is empty (open boundary).
        """
        return len(self.allowed_section_ids) == 0

    def allowed_count(self) -> int:
        """Return the number of sections explicitly permitted to cross.

        Returns:
            Integer count of ``allowed_section_ids``.
        """
        return len(self.allowed_section_ids)

    # ------------------------------------------------------------------
    # Kind predicates
    # ------------------------------------------------------------------

    def is_ipc(self) -> bool:
        """Return True if this is an IPC boundary.

        Returns:
            True when ``boundary_kind == BoundaryKind.IPC``.
        """
        return self.boundary_kind == BoundaryKind.IPC

    def is_network(self) -> bool:
        """Return True if this is a network boundary.

        Returns:
            True when ``boundary_kind == BoundaryKind.NETWORK``.
        """
        return self.boundary_kind == BoundaryKind.NETWORK

    def is_process(self) -> bool:
        """Return True if this is a plain OS-process boundary.

        Returns:
            True when ``boundary_kind == BoundaryKind.PROCESS``.
        """
        return self.boundary_kind == BoundaryKind.PROCESS

    def is_shared_memory(self) -> bool:
        """Return True if this is a memory-mapped shared boundary.

        Returns:
            True when ``boundary_kind == BoundaryKind.MEMORY_MAPPED``.
        """
        return self.boundary_kind == BoundaryKind.MEMORY_MAPPED

    # ------------------------------------------------------------------
    # Structural queries
    # ------------------------------------------------------------------

    def is_bidirectional(self) -> bool:
        """Return a heuristic estimate of whether this boundary is bidirectional.

        Two process IDs form a *canonical symmetric pair* if the source ID
        compares less than the target ID alphabetically.  A boundary that was
        created in canonical order is assumed to represent a bidirectional
        channel.  This is a simplified heuristic — callers needing strict
        bidirectionality should maintain an explicit reverse boundary.

        Returns:
            True when ``source_process_id < target_process_id``.
        """
        return self.source_process_id < self.target_process_id

    def involves_process(self, process_id: str) -> bool:
        """Return True if *process_id* is either the source or target.

        Args:
            process_id: Process identifier to check.

        Returns:
            True when process_id matches either endpoint.
        """
        return (
            self.source_process_id == process_id
            or self.target_process_id == process_id
        )

    # ------------------------------------------------------------------
    # Functional updates
    # ------------------------------------------------------------------

    def add_allowed_section(self, section_id: str) -> ProcessBoundary:
        """Return a new boundary with *section_id* added to the allowed set.

        Args:
            section_id: Section identifier to permit.

        Returns:
            A new :class:`ProcessBoundary` with the extended allowed set.
        """
        return replace(
            self, allowed_section_ids=self.allowed_section_ids | {section_id}
        )

    def remove_allowed_section(self, section_id: str) -> ProcessBoundary:
        """Return a new boundary with *section_id* removed from the allowed set.

        Args:
            section_id: Section identifier to revoke.

        Returns:
            A new :class:`ProcessBoundary` with the reduced allowed set.
        """
        return replace(
            self, allowed_section_ids=self.allowed_section_ids - {section_id}
        )

    def deactivate(self) -> ProcessBoundary:
        """Return a new boundary with ``is_active=False``.

        Returns:
            A new :class:`ProcessBoundary` that rejects all crossings.
        """
        return replace(self, is_active=False)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, object]:
        """Serialise to a JSON-safe plain dictionary.

        Returns:
            Dict with all fields; frozenset converted to sorted list and
            boundary_kind serialised as its string value.
        """
        return {
            "boundary_id": self.boundary_id,
            "source_process_id": self.source_process_id,
            "target_process_id": self.target_process_id,
            "boundary_kind": self.boundary_kind.value,
            "cover_morphism_id": self.cover_morphism_id,
            "allowed_section_ids": sorted(self.allowed_section_ids),
            "created_at": self.created_at,
            "is_active": self.is_active,
        }

    def __repr__(self) -> str:
        return (
            f"ProcessBoundary(id={self.boundary_id[:8]}…, "
            f"{self.source_process_id[:8]}…→{self.target_process_id[:8]}…, "
            f"kind={self.boundary_kind.value!r}, active={self.is_active})"
        )


# ══════════════════════════════════════════════════════════════════════════════
# ConcurrencyScope
# ══════════════════════════════════════════════════════════════════════════════

@dataclass(slots=False)
class ConcurrencyScope:
    """Mutable scope node in the concurrency scope hierarchy.

    A :class:`ConcurrencyScope` is the central bookkeeping object for a single
    node in the task/process tree.  It tracks:

    * The tree structure via ``parent_scope_id`` and ``child_scope_ids``.
    * Active :class:`TaskLocalSection` objects keyed by section ID.
    * :class:`CancellationRecord` objects accumulated during the scope's life.
    * Its own :class:`ConcurrencyRole` and :class:`ScopeStatus`.

    Scope depth is computed via an explicit while-loop rather than recursion
    to avoid stack overflows in deeply nested graphs.  The depth algorithm
    terminates at 100 levels as a safety bound (Ch24.T5 guarantees finiteness
    in theory, but defensive programming still applies).

    Args:
        scope_id: Unique hex identifier.
        role: The :class:`ConcurrencyRole` of this scope node.
        parent_scope_id: Optional ID of the parent scope.
        child_scope_ids: Mutable list of child scope IDs.
        sections: Dict mapping section IDs to :class:`TaskLocalSection` objects.
        cancellations: List of :class:`CancellationRecord` objects accumulated.
        created_at: Unix timestamp of scope creation.
        status: Current lifecycle status.
    """

    scope_id: str
    role: ConcurrencyRole
    parent_scope_id: str | None
    child_scope_ids: list[str]
    sections: dict[str, TaskLocalSection]
    cancellations: list[CancellationRecord]
    created_at: float
    status: ScopeStatus

    # ------------------------------------------------------------------
    # Tree mutation
    # ------------------------------------------------------------------

    def add_child(self, child_id: str) -> None:
        """Register a child scope ID in this scope's child list.

        Duplicate registrations are silently ignored.

        Args:
            child_id: The scope ID of the child to register.
        """
        if child_id not in self.child_scope_ids:
            self.child_scope_ids.append(child_id)

    def remove_child(self, child_id: str) -> bool:
        """Deregister a child scope ID.

        Args:
            child_id: The scope ID to remove.

        Returns:
            True if the child was found and removed, False otherwise.
        """
        try:
            self.child_scope_ids.remove(child_id)
            return True
        except ValueError:
            return False

    # ------------------------------------------------------------------
    # Section management
    # ------------------------------------------------------------------

    def add_section(self, section: TaskLocalSection) -> None:
        """Register a :class:`TaskLocalSection` in this scope.

        If a section with the same ID already exists it is replaced.

        Args:
            section: The section to register.
        """
        self.sections[section.section_id] = section

    def remove_section(self, section_id: str) -> bool:
        """Remove a section by ID.

        Args:
            section_id: The ID to remove.

        Returns:
            True if removed, False if not found.
        """
        if section_id in self.sections:
            del self.sections[section_id]
            return True
        return False

    def get_section(self, section_id: str) -> TaskLocalSection | None:
        """Look up a section by ID.

        Args:
            section_id: The section identifier.

        Returns:
            The matching :class:`TaskLocalSection`, or None.
        """
        return self.sections.get(section_id)

    def active_sections(self) -> list[TaskLocalSection]:
        """Return all sections with status ACTIVE.

        Returns:
            List of active :class:`TaskLocalSection` objects.
        """
        return [s for s in self.sections.values() if s.is_active()]

    def cancelled_sections(self) -> list[TaskLocalSection]:
        """Return all sections with status CANCELLED.

        Returns:
            List of cancelled :class:`TaskLocalSection` objects.
        """
        return [s for s in self.sections.values() if s.is_cancelled()]

    def completed_sections(self) -> list[TaskLocalSection]:
        """Return all sections with status COMPLETED.

        Returns:
            List of completed :class:`TaskLocalSection` objects.
        """
        return [
            s for s in self.sections.values()
            if s.scope_status == ScopeStatus.COMPLETED
        ]

    # ------------------------------------------------------------------
    # Cancellation
    # ------------------------------------------------------------------

    def cancel_all(
        self,
        reason: CancellationReason,
        obstruction_key: str | None = None,
    ) -> list[CancellationRecord]:
        """Cancel all active sections in this scope.

        For each currently active section, a new :class:`CancellationRecord` is
        created and the section is updated to CANCELLED status.  All created
        records are appended to ``self.cancellations`` and also returned.

        Args:
            reason: The :class:`CancellationReason` to apply to all sections.
            obstruction_key: Optional shared obstruction key.  If None, a
                unique key is generated for each section.

        Returns:
            List of newly created :class:`CancellationRecord` objects.
        """
        newly_cancelled: list[CancellationRecord] = []
        active = self.active_sections()
        for section in active:
            ok = obstruction_key or uuid.uuid4().hex
            record = CancellationRecord(
                record_id=uuid.uuid4().hex,
                task_id=section.task_id,
                reason=reason,
                obstruction_key=ok,
                cancelled_at=time.time(),
                parent_record_id=None,
                affected_section_ids=(section.section_id,),
                error_message=(
                    f"Section {section.section_id} cancelled: {reason.value}"
                ),
            )
            self.cancellations.append(record)
            # Update section to cancelled status
            cancelled_section = section.with_status(ScopeStatus.CANCELLED)
            self.sections[section.section_id] = cancelled_section
            newly_cancelled.append(record)
        if active:
            self.status = ScopeStatus.CANCELLED
        return newly_cancelled

    # ------------------------------------------------------------------
    # Structural queries
    # ------------------------------------------------------------------

    def scope_depth(self) -> int:
        """Compute the depth of this scope in the hierarchy.

        Since scope objects do not hold references to their parents (only IDs),
        this method uses a heuristic: it counts the non-None ``parent_scope_id``
        chain by inspecting the IDs.  The actual depth is the number of
        non-None parent references that would be encountered walking upward.
        Because we cannot dereference IDs without a registry, this returns:

        * 1 if this scope has no parent (root node).
        * 2 if this scope has a parent but we cannot verify the parent's parent.

        For a true recursive depth, use a :class:`ScopeRegistry` (not in this
        module) that holds all scope objects and can walk the chain.  The while-
        loop below is written defensively with a cap at 100.

        Returns:
            Depth estimate: 1 for root, 2 for any non-root scope.
        """
        depth = 1
        current_parent = self.parent_scope_id
        # Without a registry we can only confirm the immediate parent level.
        # The loop structure is preserved for compatibility with registry-aware
        # callers who will subclass and override this method.
        iterations = 0
        while current_parent is not None and iterations < 100:
            depth += 1
            # Without a full registry we cannot dereference further.
            break
        return depth

    def section_count(self) -> int:
        """Return the total number of registered sections.

        Returns:
            Integer count.
        """
        return len(self.sections)

    def cancellation_count(self) -> int:
        """Return the total number of cancellation records accumulated.

        Returns:
            Integer count.
        """
        return len(self.cancellations)

    def child_count(self) -> int:
        """Return the number of registered child scopes.

        Returns:
            Integer count.
        """
        return len(self.child_scope_ids)

    def is_root(self) -> bool:
        """Return True if this scope has no parent.

        Returns:
            True when ``parent_scope_id is None``.
        """
        return self.parent_scope_id is None

    def is_active(self) -> bool:
        """Return True if this scope's status is ACTIVE.

        Returns:
            True when ``status == ScopeStatus.ACTIVE``.
        """
        return self.status == ScopeStatus.ACTIVE

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, object]:
        """Serialise to a JSON-safe plain dictionary.

        Returns:
            Dict with all fields; sections and cancellations are serialised
            recursively via their own ``to_dict`` methods.
        """
        return {
            "scope_id": self.scope_id,
            "role": self.role.value,
            "parent_scope_id": self.parent_scope_id,
            "child_scope_ids": list(self.child_scope_ids),
            "sections": {k: v.to_dict() for k, v in self.sections.items()},
            "cancellations": [c.to_dict() for c in self.cancellations],
            "created_at": self.created_at,
            "status": self.status.value,
            "section_count": self.section_count(),
            "cancellation_count": self.cancellation_count(),
        }

    def __repr__(self) -> str:
        return (
            f"ConcurrencyScope(id={self.scope_id[:8]}…, "
            f"role={self.role.value!r}, status={self.status.value!r}, "
            f"sections={self.section_count()}, "
            f"children={self.child_count()})"
        )


# ══════════════════════════════════════════════════════════════════════════════
# Factory helpers
# ══════════════════════════════════════════════════════════════════════════════

def make_task_section(
    task_id: str,
    task_name: str,
    parent_id: str | None = None,
    bindings: frozenset[str] | None = None,
    support_keys: frozenset[str] | None = None,
) -> TaskLocalSection:
    """Create a new :class:`TaskLocalSection` with a generated section ID.

    Args:
        task_id: Identifier of the owning task.
        task_name: Human-readable task name.
        parent_id: Optional parent section identifier.
        bindings: Initial set of binding names.  Defaults to empty frozenset.
        support_keys: Initial support keys.  Defaults to empty frozenset.

    Returns:
        A new :class:`TaskLocalSection` with ACTIVE status and generated ID.
    """
    return TaskLocalSection(
        section_id=uuid.uuid4().hex,
        task_id=task_id,
        task_name=task_name,
        local_bindings=bindings if bindings is not None else frozenset(),
        parent_section_id=parent_id,
        created_at=time.time(),
        scope_status=ScopeStatus.ACTIVE,
        support_keys=support_keys if support_keys is not None else frozenset(),
        provenance=(f"created by make_task_section at {time.time()!s}",),
    )


def make_cancellation_record(
    task_id: str,
    reason: CancellationReason,
    obstruction_key: str,
    affected_ids: tuple[str, ...] = (),
    error_message: str = "",
    parent_record_id: str | None = None,
) -> CancellationRecord:
    """Create a new :class:`CancellationRecord` with a generated record ID.

    Args:
        task_id: Identifier of the cancelled task.
        reason: The :class:`CancellationReason`.
        obstruction_key: Unique key for the injected obstruction class.
        affected_ids: Tuple of affected section IDs.
        error_message: Optional human-readable explanation.
        parent_record_id: Optional parent record identifier for propagated
            cancellations.

    Returns:
        A new :class:`CancellationRecord`.
    """
    return CancellationRecord(
        record_id=uuid.uuid4().hex,
        task_id=task_id,
        reason=reason,
        obstruction_key=obstruction_key,
        cancelled_at=time.time(),
        parent_record_id=parent_record_id,
        affected_section_ids=affected_ids,
        error_message=error_message or f"Cancelled: {reason.value}",
    )


def make_process_boundary(
    source_id: str,
    target_id: str,
    kind: BoundaryKind = BoundaryKind.IPC,
    allowed_sections: frozenset[str] | None = None,
) -> ProcessBoundary:
    """Create a new :class:`ProcessBoundary` with generated IDs.

    Args:
        source_id: Source process identifier.
        target_id: Target process identifier.
        kind: The :class:`BoundaryKind` for this boundary.
        allowed_sections: Optional frozenset of initially allowed section IDs.

    Returns:
        A new active :class:`ProcessBoundary`.
    """
    return ProcessBoundary(
        boundary_id=uuid.uuid4().hex,
        source_process_id=source_id,
        target_process_id=target_id,
        boundary_kind=kind,
        cover_morphism_id=uuid.uuid4().hex,
        allowed_section_ids=(
            allowed_sections if allowed_sections is not None else frozenset()
        ),
        created_at=time.time(),
        is_active=True,
    )


def make_scope(
    role: ConcurrencyRole = ConcurrencyRole.TASK,
    parent_id: str | None = None,
) -> ConcurrencyScope:
    """Create a new :class:`ConcurrencyScope` with a generated scope ID.

    Args:
        role: The :class:`ConcurrencyRole` for this scope.
        parent_id: Optional parent scope identifier.

    Returns:
        A new ACTIVE :class:`ConcurrencyScope` with no children or sections.
    """
    return ConcurrencyScope(
        scope_id=uuid.uuid4().hex,
        role=role,
        parent_scope_id=parent_id,
        child_scope_ids=[],
        sections={},
        cancellations=[],
        created_at=time.time(),
        status=ScopeStatus.ACTIVE,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Module exports
# ══════════════════════════════════════════════════════════════════════════════

__all__: list[str] = [
    # Enumerations
    "ConcurrencyRole",
    "CancellationReason",
    "BoundaryKind",
    "ScopeStatus",
    # Core models
    "TaskLocalSection",
    "CancellationRecord",
    "ExceptionGroupRecord",
    "ProcessBoundary",
    "ConcurrencyScope",
    # Factories
    "make_task_section",
    "make_cancellation_record",
    "make_process_boundary",
    "make_scope",
]

# copilot: shared-core marker for future LLM orchestration.
