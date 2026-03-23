"""Section 2 of the live_mutation Ch23 implementation: monkey patching as section
replacement with invalidation.  In sheaf-theoretic terms, replacing a method or
attribute at runtime replaces a local section in the semantic space and triggers
invalidation of all dependent sections.  This module implements: MonkeyPatcher
(applies and reverts attribute patches), InvalidationTrigger (computes and fires
invalidation cascades from patches), PatchStack (manages layered patches with ordered
application), and PatchAuditor (maintains full audit trail of all patches).  Theory
alignment: Ch23 §2 of theory2.tex.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any

try:
    from jugeo.python_runtime.live_mutation.models import (
        DynamicSection,
        EvalResult,
        ExecContext,
        MutationKind,
        new_context_id,
        new_result_id,
        new_section_id,
    )
except ImportError:  # pragma: no cover - stub for isolated runs
    DynamicSection = EvalResult = ExecContext = MutationKind = None  # type: ignore[assignment,misc]

    def new_section_id() -> str:
        return f"sec-{uuid.uuid4().hex[:12]}"

    def new_context_id() -> str:
        return f"ctx-{uuid.uuid4().hex[:12]}"

    def new_result_id() -> str:
        return f"res-{uuid.uuid4().hex[:12]}"


def _new_patch_id() -> str:
    """Generate a unique patch identifier."""
    return f"patch-{uuid.uuid4().hex[:12]}"


def _new_event_id() -> str:
    """Generate a unique audit event identifier."""
    return f"evt-{uuid.uuid4().hex[:12]}"


def _sha256_short(text: str) -> str:
    """Return the first 16 hex characters of the SHA-256 digest of *text*."""
    return hashlib.sha256(text.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# MonkeyPatcher
# ---------------------------------------------------------------------------


@dataclass
class MonkeyPatcher:
    """Applies and reverts attribute patches at the semantic level.

    In sheaf-theoretic terms each patch replaces one local section value.
    The patcher does *not* modify live Python module objects — it tracks patch
    state in its own data structures so that the sheaf framework can reason
    about what would happen if the patch were applied.

    Attributes:
        _patches: Mapping from patch_id to patch-record dicts.
        _originals: Mapping from patch_id to the identity (``id()``) of the
            new value that was passed in — used as a stable reference token.
        _patch_order: Ordered list of patch_ids in application order.
    """

    _patches: dict[str, dict] = field(default_factory=dict)
    _originals: dict[str, object] = field(default_factory=dict)
    _patch_order: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def apply_patch(
        self,
        module_name: str,
        attribute: str,
        new_value: object,
        patch_id: str | None = None,
    ) -> str:
        """Record a patch that replaces *module_name*.*attribute* with *new_value*.

        The patcher stores the identity of *new_value* as the ``original_id``
        field (a stable reference token) and records a SHA-256 fingerprint of
        the ``module_name + attribute`` string as the ``patch_hash``.

        Args:
            module_name: Fully qualified module name being patched
                (e.g. ``"jugeo.core.utils"``).
            attribute: Name of the attribute being replaced.
            new_value: The replacement value.  Only its identity is stored.
            patch_id: Optional explicit patch identifier.  If *None* a new
                unique ID is generated.

        Returns:
            The patch_id string for this patch record.
        """
        if patch_id is None:
            patch_id = _new_patch_id()
        patch_hash = _sha256_short(module_name + "." + attribute)
        record: dict = {
            "patch_id": patch_id,
            "module_name": module_name,
            "attribute": attribute,
            "original_id": id(new_value),
            "patch_hash": patch_hash,
            "applied_at": time.time(),
            "reverted_at": None,
            "scope": "MODULE",
        }
        self._patches[patch_id] = record
        self._originals[patch_id] = new_value
        self._patch_order.append(patch_id)
        return patch_id

    def revert_patch(self, patch_id: str) -> bool:
        """Mark the patch identified by *patch_id* as reverted.

        Does not actually restore a live module attribute; it records the
        revert timestamp in the patch record so downstream reasoning can
        treat the patch as no longer active.

        Args:
            patch_id: The patch to revert.

        Returns:
            *True* if the patch was found and successfully marked as reverted;
            *False* if not found or if the patch was already reverted.
        """
        rec = self._patches.get(patch_id)
        if rec is None or rec["reverted_at"] is not None:
            return False
        rec["reverted_at"] = time.time()
        return True

    def revert_all(self) -> int:
        """Revert every active (non-reverted) patch.

        Returns:
            The count of patches that were reverted by this call.
        """
        count = 0
        for patch_id, rec in self._patches.items():
            if rec["reverted_at"] is None:
                rec["reverted_at"] = time.time()
                count += 1
        return count

    def is_patched(self, module_name: str, attribute: str) -> bool:
        """Return *True* if there is at least one active patch for *module_name*.*attribute*.

        Args:
            module_name: Module name to check.
            attribute: Attribute name to check.

        Returns:
            Boolean indicating whether an active patch exists.
        """
        for rec in self._patches.values():
            if (
                rec["module_name"] == module_name
                and rec["attribute"] == attribute
                and rec["reverted_at"] is None
            ):
                return True
        return False

    def active_patches(self) -> list[dict]:
        """Return all patch records where ``reverted_at`` is *None*.

        Returns:
            List of active patch-record dicts, ordered by application time.
        """
        return [
            rec
            for rec in sorted(self._patches.values(), key=lambda r: r["applied_at"])
            if rec["reverted_at"] is None
        ]

    def patch_history(self, module_name: str | None = None) -> list[dict]:
        """Return all patch records, optionally filtered by *module_name*.

        Args:
            module_name: If provided, only return patches for this module.

        Returns:
            List of patch-record dicts, ordered by application time.
        """
        records = sorted(self._patches.values(), key=lambda r: r["applied_at"])
        if module_name is not None:
            records = [r for r in records if r["module_name"] == module_name]
        return records

    def patch_count(self) -> int:
        """Return the count of currently active (non-reverted) patches.

        Returns:
            Non-negative integer.
        """
        return sum(1 for rec in self._patches.values() if rec["reverted_at"] is None)

    def patcher_stats(self) -> dict:
        """Return a summary of patcher statistics.

        Returns:
            Dict with ``total_patches``, ``active_patches``,
            ``reverted_patches``, ``modules_patched`` (unique module count).
        """
        total = len(self._patches)
        active = self.patch_count()
        reverted = total - active
        modules = len({r["module_name"] for r in self._patches.values()})
        return {
            "total_patches": total,
            "active_patches": active,
            "reverted_patches": reverted,
            "modules_patched": modules,
        }


# ---------------------------------------------------------------------------
# InvalidationTrigger
# ---------------------------------------------------------------------------


@dataclass
class InvalidationTrigger:
    """Computes and fires invalidation cascades from patches.

    When a module attribute is replaced by a patch, every section that
    depends (directly or transitively) on that attribute must be invalidated.
    This class maintains a dependency graph from attribute names to the
    section IDs that depend on them, and uses BFS to find the full transitive
    closure.

    Attributes:
        _dependency_graph: Maps attribute name → set of section IDs that
            depend on it.
        _invalidation_log: Ordered list of invalidation-event dicts.
        _cascade_depth_limit: Maximum BFS depth to prevent unbounded cascades.
    """

    _dependency_graph: dict[str, set[str]] = field(default_factory=dict)
    _invalidation_log: list[dict] = field(default_factory=list)
    _cascade_depth_limit: int = 20

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def register_dependency(self, section_id: str, depends_on: set[str]) -> None:
        """Register that *section_id* depends on the given attribute names.

        Multiple calls with the same *section_id* accumulate dependencies
        (they do not replace prior registrations).

        Args:
            section_id: The section that has the dependency.
            depends_on: Set of attribute name strings on which the section
                depends.
        """
        for attr in depends_on:
            if attr not in self._dependency_graph:
                self._dependency_graph[attr] = set()
            self._dependency_graph[attr].add(section_id)

    def compute_cascade(self, patched_attribute: str) -> set[str]:
        """Return the set of section IDs transitively affected by patching *patched_attribute*.

        Uses BFS starting from *patched_attribute*, treating each affected
        section ID as a potential attribute name in the next level of the
        graph (i.e. a section whose identifier also appears as an attribute
        key can itself propagate the cascade).

        Args:
            patched_attribute: The attribute name that was patched.

        Returns:
            Set of affected section_id strings (excluding
            *patched_attribute* itself).
        """
        affected: set[str] = set()
        frontier: deque[tuple[str, int]] = deque([(patched_attribute, 0)])
        visited: set[str] = {patched_attribute}

        while frontier:
            current, depth = frontier.popleft()
            if depth >= self._cascade_depth_limit:
                continue
            for sec_id in self._dependency_graph.get(current, set()):
                if sec_id not in visited:
                    visited.add(sec_id)
                    affected.add(sec_id)
                    # A section ID may itself be an attribute key (chained deps)
                    frontier.append((sec_id, depth + 1))
        return affected

    def fire_invalidation(self, patched_attribute: str, patch_id: str) -> dict:
        """Compute and record a full invalidation cascade for *patched_attribute*.

        Args:
            patched_attribute: The attribute name that triggered the cascade.
            patch_id: The patch that caused this invalidation.

        Returns:
            The invalidation-event dict that was appended to the log.
        """
        affected = self.compute_cascade(patched_attribute)
        depth = self.cascade_depth(patched_attribute)
        record: dict = {
            "event_id": _new_event_id(),
            "patched_attribute": patched_attribute,
            "patch_id": patch_id,
            "affected_sections": sorted(affected),
            "cascade_depth": depth,
            "fired_at": time.time(),
        }
        self._invalidation_log.append(record)
        return record

    def record_invalidation(
        self, section_id: str, patch_id: str, reason: str
    ) -> None:
        """Append a manual (non-cascade) invalidation record to the log.

        Args:
            section_id: The section being manually invalidated.
            patch_id: The patch that caused the invalidation.
            reason: Human-readable explanation.
        """
        self._invalidation_log.append(
            {
                "event_id": _new_event_id(),
                "section_id": section_id,
                "patch_id": patch_id,
                "reason": reason,
                "manual": True,
                "fired_at": time.time(),
            }
        )

    def cascade_depth(self, patched_attribute: str) -> int:
        """Return the maximum BFS depth of the cascade from *patched_attribute*.

        Args:
            patched_attribute: The starting attribute.

        Returns:
            Non-negative integer level count.
        """
        max_depth = 0
        frontier: deque[tuple[str, int]] = deque([(patched_attribute, 0)])
        visited: set[str] = {patched_attribute}
        while frontier:
            current, depth = frontier.popleft()
            max_depth = max(max_depth, depth)
            if depth >= self._cascade_depth_limit:
                continue
            for sec_id in self._dependency_graph.get(current, set()):
                if sec_id not in visited:
                    visited.add(sec_id)
                    frontier.append((sec_id, depth + 1))
        return max_depth

    def affected_modules(self, patched_attribute: str) -> set[str]:
        """Return the set of module prefixes for all sections affected by the cascade.

        The module prefix is derived by splitting the section_id on ``/`` and
        ``-`` to extract a meaningful prefix token.

        Args:
            patched_attribute: The attribute name triggering the cascade.

        Returns:
            Set of module-prefix strings.
        """
        affected = self.compute_cascade(patched_attribute)
        prefixes: set[str] = set()
        for sec_id in affected:
            # Try to extract a meaningful prefix (e.g. "sec" from "sec-abc123")
            parts = re.split(r"[-/]", sec_id)
            if parts:
                prefixes.add(parts[0])
        return prefixes

    def check_circular(self, section_id: str, attr: str) -> bool:
        """Return *True* if adding a dependency of *section_id* on *attr* would create a cycle.

        A cycle would exist if *section_id* is already reachable from *attr*
        through the current dependency graph, because that would mean
        *section_id* → *attr* → ... → *section_id*.

        Args:
            section_id: The section that would gain the dependency.
            attr: The attribute on which it would depend.

        Returns:
            Boolean indicating whether the new dependency would be circular.
        """
        # If section_id is already in the cascade of attr, adding the reverse
        # dependency would create a cycle.
        reachable_from_attr = self.compute_cascade(attr)
        return section_id in reachable_from_attr

    def invalidation_report(self) -> dict:
        """Return a statistical summary of all invalidation events.

        Returns:
            Dict with ``total_invalidations``, ``total_cascades``,
            ``avg_cascade_size``, ``deepest_cascade``.
        """
        cascade_events = [
            e for e in self._invalidation_log if "affected_sections" in e
        ]
        total_cascades = len(cascade_events)
        if total_cascades:
            avg_size = sum(
                len(e["affected_sections"]) for e in cascade_events
            ) / total_cascades
            deepest = max(e.get("cascade_depth", 0) for e in cascade_events)
        else:
            avg_size = 0.0
            deepest = 0
        return {
            "total_invalidations": len(self._invalidation_log),
            "total_cascades": total_cascades,
            "avg_cascade_size": round(avg_size, 4),
            "deepest_cascade": deepest,
        }

    def export_triggers(self) -> list[dict]:
        """Return the full invalidation log.

        Returns:
            List of invalidation-event dicts.
        """
        return list(self._invalidation_log)


# ---------------------------------------------------------------------------
# PatchStack
# ---------------------------------------------------------------------------


@dataclass
class PatchStack:
    """Manages layered patches with ordered application.

    Implements a LIFO stack of patch records.  The stack supports introspection
    and selective rollback to a given depth, which allows the sheaf framework
    to reason about ordered section-replacement sequences.

    Attributes:
        _stack: Ordered list of patch-record dicts (index 0 = oldest / bottom).
        _stack_version: Monotonically increasing integer bumped on every push.
    """

    _stack: list[dict] = field(default_factory=list)
    _stack_version: int = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def push(self, patch_record: dict) -> int:
        """Push *patch_record* onto the top of the stack.

        Args:
            patch_record: A patch-record dict (as returned by
                :meth:`MonkeyPatcher.apply_patch`).

        Returns:
            The new *_stack_version* after this push.
        """
        self._stack.append(patch_record)
        self._stack_version += 1
        return self._stack_version

    def pop(self) -> dict | None:
        """Pop and return the top patch record.

        Returns:
            The top patch-record dict, or *None* if the stack is empty.
        """
        if not self._stack:
            return None
        return self._stack.pop()

    def peek(self) -> dict | None:
        """Return the top patch record without removing it.

        Returns:
            The top patch-record dict, or *None* if the stack is empty.
        """
        if not self._stack:
            return None
        return self._stack[-1]

    def depth(self) -> int:
        """Return the current number of entries in the stack.

        Returns:
            Non-negative integer stack depth.
        """
        return len(self._stack)

    def find_patch(self, patch_id: str) -> int:
        """Return the 0-based index of *patch_id* in the stack.

        Searches from bottom (index 0) to top.

        Args:
            patch_id: The ``patch_id`` field value to search for.

        Returns:
            0-based index, or ``-1`` if not found.
        """
        for idx, rec in enumerate(self._stack):
            if rec.get("patch_id") == patch_id:
                return idx
        return -1

    def revert_to_depth(self, target_depth: int) -> list[dict]:
        """Pop patches until the stack depth equals *target_depth*.

        Args:
            target_depth: The desired final stack depth.  Must be less than
                the current depth to have any effect.  If *target_depth* is
                greater than or equal to the current depth, nothing is popped.

        Returns:
            List of popped patch-record dicts (topmost popped first).
        """
        popped: list[dict] = []
        while len(self._stack) > target_depth:
            rec = self._stack.pop()
            popped.append(rec)
        return popped

    def stack_snapshot(self) -> list[dict]:
        """Return a shallow copy of the current stack (bottom to top).

        Returns:
            List of patch-record dicts.
        """
        return list(self._stack)

    def version(self) -> int:
        """Return the current stack version counter.

        Returns:
            Non-negative integer.
        """
        return self._stack_version

    def stack_stats(self) -> dict:
        """Return a summary of stack statistics.

        Returns:
            Dict with ``depth``, ``version``, ``oldest_patch_age_seconds``
            (``None`` if stack is empty), ``newest_patch_age_seconds``
            (``None`` if stack is empty).
        """
        now = time.time()
        oldest_age: float | None = None
        newest_age: float | None = None
        if self._stack:
            applied_times = [
                r.get("applied_at", now) for r in self._stack if "applied_at" in r
            ]
            if applied_times:
                oldest_age = round(now - min(applied_times), 4)
                newest_age = round(now - max(applied_times), 4)
        return {
            "depth": self.depth(),
            "version": self._stack_version,
            "oldest_patch_age_seconds": oldest_age,
            "newest_patch_age_seconds": newest_age,
        }


# ---------------------------------------------------------------------------
# PatchAuditor
# ---------------------------------------------------------------------------


@dataclass
class PatchAuditor:
    """Maintains a full audit trail of all patch events.

    Every apply, revert, query, and cascade event should be recorded through
    the auditor so that the full history of the semantic space can be
    replayed or inspected.

    Attributes:
        _audit_trail: Ordered list of audit-event dicts.
        _module_index: Maps module_name → list of 0-based indices into
            *_audit_trail* for quick module-scoped lookups.
    """

    _audit_trail: list[dict] = field(default_factory=list)
    _module_index: dict[str, list[int]] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record_event(
        self,
        event_type: str,
        patch_id: str,
        module_name: str,
        attribute: str,
        details: dict | None = None,
    ) -> dict:
        """Create, store, and return a new audit event record.

        Args:
            event_type: String classifying the event (e.g. ``"APPLY"``,
                ``"REVERT"``, ``"INVALIDATE"``).
            patch_id: The patch this event relates to.
            module_name: The module being patched.
            attribute: The attribute being patched.
            details: Optional dict of additional event-specific metadata.

        Returns:
            The newly created audit-event dict.
        """
        event_id = _new_event_id()
        record: dict = {
            "event_id": event_id,
            "event_type": event_type,
            "patch_id": patch_id,
            "module_name": module_name,
            "attribute": attribute,
            "details": details or {},
            "timestamp": time.time(),
        }
        idx = len(self._audit_trail)
        self._audit_trail.append(record)
        if module_name not in self._module_index:
            self._module_index[module_name] = []
        self._module_index[module_name].append(idx)
        return record

    def get_events_for_module(self, module_name: str) -> list[dict]:
        """Return all audit events associated with *module_name*.

        Args:
            module_name: Module to filter on.

        Returns:
            List of audit-event dicts, ordered chronologically.
        """
        indices = self._module_index.get(module_name, [])
        return [self._audit_trail[i] for i in sorted(indices)]

    def get_events_for_patch(self, patch_id: str) -> list[dict]:
        """Return all audit events associated with *patch_id*.

        Args:
            patch_id: Patch identifier to filter on.

        Returns:
            List of audit-event dicts, ordered chronologically.
        """
        return [e for e in self._audit_trail if e["patch_id"] == patch_id]

    def timeline(self) -> list[dict]:
        """Return all audit events sorted by timestamp ascending.

        Returns:
            List of audit-event dicts.
        """
        return sorted(self._audit_trail, key=lambda e: e["timestamp"])

    def patch_lifecycle(self, patch_id: str) -> dict:
        """Return a dict describing the full lifecycle of *patch_id*.

        Scans the audit trail for events related to *patch_id* and extracts
        relevant timestamps.

        Args:
            patch_id: The patch to describe.

        Returns:
            Dict with ``patch_id``, ``created_at`` (first event timestamp or
            *None*), ``applied_at`` (timestamp of first APPLY event or *None*),
            ``reverted_at`` (timestamp of first REVERT event or *None*), and
            ``events_count``.
        """
        events = self.get_events_for_patch(patch_id)
        created_at = events[0]["timestamp"] if events else None
        applied_at = next(
            (e["timestamp"] for e in events if e["event_type"] == "APPLY"), None
        )
        reverted_at = next(
            (e["timestamp"] for e in events if e["event_type"] == "REVERT"), None
        )
        return {
            "patch_id": patch_id,
            "created_at": created_at,
            "applied_at": applied_at,
            "reverted_at": reverted_at,
            "events_count": len(events),
        }

    def module_patch_summary(self) -> dict[str, int]:
        """Return a mapping of module name to the count of patch events for that module.

        Returns:
            Dict mapping module_name → event count.
        """
        summary: dict[str, int] = {}
        for e in self._audit_trail:
            mod = e["module_name"]
            summary[mod] = summary.get(mod, 0) + 1
        return summary

    def audit_stats(self) -> dict:
        """Return a statistical summary of the audit trail.

        Returns:
            Dict with ``total_events``, ``unique_patches``,
            ``unique_modules``, ``event_types`` (dict of type → count).
        """
        unique_patches = len({e["patch_id"] for e in self._audit_trail})
        unique_modules = len({e["module_name"] for e in self._audit_trail})
        type_counts: dict[str, int] = {}
        for e in self._audit_trail:
            t = e["event_type"]
            type_counts[t] = type_counts.get(t, 0) + 1
        return {
            "total_events": len(self._audit_trail),
            "unique_patches": unique_patches,
            "unique_modules": unique_modules,
            "event_types": type_counts,
        }

    def export_trail(self) -> list[dict]:
        """Return the full audit trail as a list of event dicts.

        Returns:
            List of audit-event dicts in insertion order.
        """
        return list(self._audit_trail)


# ---------------------------------------------------------------------------
# Module-level convenience factories
# ---------------------------------------------------------------------------


def make_monkey_patcher() -> MonkeyPatcher:
    """Create a fresh :class:`MonkeyPatcher`.

    Returns:
        A new :class:`MonkeyPatcher` instance.
    """
    return MonkeyPatcher()


def make_invalidation_trigger() -> InvalidationTrigger:
    """Create a fresh :class:`InvalidationTrigger`.

    Returns:
        A new :class:`InvalidationTrigger` instance.
    """
    return InvalidationTrigger()


def make_patch_stack() -> PatchStack:
    """Create a fresh :class:`PatchStack`.

    Returns:
        A new :class:`PatchStack` instance.
    """
    return PatchStack()


def make_patch_auditor() -> PatchAuditor:
    """Create a fresh :class:`PatchAuditor`.

    Returns:
        A new :class:`PatchAuditor` instance.
    """
    return PatchAuditor()


__all__ = [
    "MonkeyPatcher",
    "InvalidationTrigger",
    "PatchStack",
    "PatchAuditor",
    "make_monkey_patcher",
    "make_invalidation_trigger",
    "make_patch_stack",
    "make_patch_auditor",
]

# copilot: monkey patching as section replacement for live_mutation Ch23 §2
