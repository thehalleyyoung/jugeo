from __future__ import annotations

"""cancellation_and_exception_group_s — Cancellation and Exception-Group Semantics.

Theory reference: Ch24 §3

asyncio.CancelledError propagation forms a tree of cancellation records, each node
pointing to its parent.  ExceptionGroup objects represent multiple simultaneous
obstruction records in the sheaf — a partial split of one failure event into
independently handleable sub-obstructions.  This module provides the full
Coordinator-Analyzer-Witness pattern for these semantics.
"""

import hashlib
import json
import logging
import math
import re
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from enum import Enum

try:
    from jugeo.core.cancellation import CancellationToken  # type: ignore
except Exception:  # pragma: no cover
    class CancellationToken:  # type: ignore
        """Inline stub for jugeo.core.cancellation.CancellationToken."""
        def __init__(self, token_id: str) -> None:
            self.token_id = token_id
            self.cancelled = False
        def cancel(self) -> None:
            self.cancelled = True

try:
    from jugeo.sheaf.obstruction import ObstructionSet  # type: ignore
except Exception:  # pragma: no cover
    class ObstructionSet:  # type: ignore
        """Inline stub for jugeo.sheaf.obstruction.ObstructionSet."""
        def __init__(self, keys: frozenset[str] | None = None) -> None:
            self.keys: frozenset[str] = keys or frozenset()
        def union(self, other: ObstructionSet) -> ObstructionSet:
            return ObstructionSet(self.keys | other.keys)

try:
    from jugeo.evidence.trail import EvidenceTrail  # type: ignore
except Exception:  # pragma: no cover
    class EvidenceTrail:  # type: ignore
        """Inline stub for jugeo.evidence.trail.EvidenceTrail."""
        def __init__(self) -> None:
            self._entries: list[dict] = []
        def append(self, entry: dict) -> None:
            self._entries.append(entry)
        def to_list(self) -> list[dict]:
            return list(self._entries)

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class CancellationStatus(str, Enum):
    """Status of a cancellation node in the propagation tree.

    Example::

        status = CancellationStatus.PROPAGATING
    """

    PENDING = "PENDING"
    PROPAGATING = "PROPAGATING"
    RESOLVED = "RESOLVED"
    SHIELDED = "SHIELDED"
    TIMED_OUT = "TIMED_OUT"


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _new_node_id() -> str:
    """Return a short unique cancellation node identifier.

    Returns:
        12-character hex string from uuid4.
    """
    return uuid.uuid4().hex[:12]


def _new_group_id() -> str:
    """Return a short unique exception-group identifier.

    Returns:
        12-character hex string from uuid4.
    """
    return uuid.uuid4().hex[:12]


def _fingerprint(data: object) -> str:
    """Produce a SHA-256 hex digest of *data* serialised as JSON.

    Args:
        data: Any JSON-serialisable object.

    Returns:
        64-character hex string.
    """
    raw = json.dumps(data, sort_keys=True, default=str).encode()
    return hashlib.sha256(raw).hexdigest()


def _severity_for_types(exception_types: tuple[str, ...]) -> float:
    """Estimate a severity score for an exception group based on its types.

    Higher-severity exception types bump the score upward.  The scale is
    roughly proportional to the number of distinct exception types present
    and whether any are considered critical.

    Args:
        exception_types: Tuple of exception class name strings.

    Returns:
        Float severity in [0.0, 1.0].
    """
    critical_types = {"RuntimeError", "SystemError", "MemoryError", "KeyboardInterrupt"}
    base = min(len(exception_types) / 10.0, 0.5)
    bonus = 0.5 if any(t in critical_types for t in exception_types) else 0.0
    return min(base + bonus, 1.0)


def _normalise_type_name(name: str) -> str:
    """Strip module prefixes from an exception type name.

    Args:
        name: A potentially fully qualified type name.

    Returns:
        The unqualified class name part.
    """
    return name.split(".")[-1].strip() or "Exception"


# ---------------------------------------------------------------------------
# Frozen record dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class CancellationNode:
    """An immutable record representing one node in a cancellation propagation tree.

    Attributes:
        node_id: Unique identifier for this node.
        task_id: The task that received or originated the cancellation.
        reason: Human-readable explanation of why this task was cancelled.
        parent_node_id: The parent node that triggered this cancellation, or None
            if this is the root cause.
        child_node_ids: Tuple of node_ids directly cancelled by this node.
        obstruction_key: The sheaf obstruction key associated with this cancellation.
        created_at: Monotonic timestamp of record creation.
        resolved_at: Monotonic timestamp when this node was resolved, or None.

    Example::

        node = CancellationNode(
            node_id="n001",
            task_id="task-001",
            reason="timeout exceeded",
            parent_node_id=None,
            child_node_ids=(),
            obstruction_key="obs:timeout",
            created_at=time.monotonic(),
            resolved_at=None,
        )
    """

    node_id: str
    task_id: str
    reason: str
    parent_node_id: str | None
    child_node_ids: tuple[str, ...]
    obstruction_key: str
    created_at: float
    resolved_at: float | None

    def to_dict(self) -> dict[str, object]:
        """Serialise this node to a plain dictionary.

        Returns:
            Dict with all fields as JSON-compatible types.
        """
        return {
            "node_id": self.node_id,
            "task_id": self.task_id,
            "reason": self.reason,
            "parent_node_id": self.parent_node_id,
            "child_node_ids": list(self.child_node_ids),
            "obstruction_key": self.obstruction_key,
            "created_at": self.created_at,
            "resolved_at": self.resolved_at,
        }

    def is_root(self) -> bool:
        """Return True if this node has no parent (is the root cause).

        Returns:
            Boolean.
        """
        return self.parent_node_id is None

    def is_resolved(self) -> bool:
        """Return True if resolved_at has been set.

        Returns:
            Boolean.
        """
        return self.resolved_at is not None

    def fingerprint(self) -> str:
        """Return a deterministic content fingerprint.

        Returns:
            64-character hex string.
        """
        return _fingerprint(self.to_dict())


@dataclass(frozen=True, slots=True)
class ExceptionGroupNode:
    """An immutable record representing an ExceptionGroup as simultaneous obstruction records.

    Attributes:
        group_id: Unique identifier for this group node.
        task_id: The task that raised this ExceptionGroup.
        exception_types: Tuple of exception class name strings.
        obstruction_keys: Tuple of sheaf obstruction keys, one per sub-exception.
        is_partial: True when not all sub-exceptions have been matched/handled.
        created_at: Monotonic timestamp.
        matched_count: Number of sub-exceptions matched to a handler.
        unmatched_count: Number of sub-exceptions that remain unhandled.

    Example::

        eg = ExceptionGroupNode(
            group_id="g001",
            task_id="task-001",
            exception_types=("ValueError", "KeyError"),
            obstruction_keys=("obs:val", "obs:key"),
            is_partial=True,
            created_at=time.monotonic(),
            matched_count=1,
            unmatched_count=1,
        )
    """

    group_id: str
    task_id: str
    exception_types: tuple[str, ...]
    obstruction_keys: tuple[str, ...]
    is_partial: bool
    created_at: float
    matched_count: int
    unmatched_count: int

    def to_dict(self) -> dict[str, object]:
        """Serialise this node to a plain dictionary.

        Returns:
            Dict with all fields as JSON-compatible types.
        """
        return {
            "group_id": self.group_id,
            "task_id": self.task_id,
            "exception_types": list(self.exception_types),
            "obstruction_keys": list(self.obstruction_keys),
            "is_partial": self.is_partial,
            "created_at": self.created_at,
            "matched_count": self.matched_count,
            "unmatched_count": self.unmatched_count,
        }

    def total_exceptions(self) -> int:
        """Return the total number of sub-exceptions in this group.

        Returns:
            Integer sum of matched_count and unmatched_count.
        """
        return self.matched_count + self.unmatched_count

    def resolution_ratio(self) -> float:
        """Return the fraction of sub-exceptions that have been matched.

        Returns:
            Float in [0.0, 1.0]; 0.0 if total is zero.
        """
        total = self.total_exceptions()
        if total == 0:
            return 0.0
        return self.matched_count / total


# ---------------------------------------------------------------------------
# Analyzers
# ---------------------------------------------------------------------------

@dataclass
class CancellationTreeAnalyzer:
    """Analyzes cancellation propagation trees across tasks.

    Maintains a registry of CancellationNode objects and provides tree-walk
    methods, obstruction-set computation, and status management.

    Attributes:
        _nodes: All registered CancellationNode objects keyed by node_id.
        _task_nodes: Maps task_id → list of node_ids for that task.
        _statuses: Current CancellationStatus for each node_id.
        _children_extra: Additional child ids injected after node creation.

    Example::

        cta = CancellationTreeAnalyzer()
        node = cta.create_node("task-1", "timeout", None, "obs:timeout")
    """

    _nodes: dict[str, CancellationNode] = field(default_factory=dict)
    _task_nodes: dict[str, list[str]] = field(default_factory=dict)
    _statuses: dict[str, CancellationStatus] = field(default_factory=dict)
    _children_extra: dict[str, list[str]] = field(default_factory=dict)

    def create_node(
        self,
        task_id: str,
        reason: str,
        parent_id: str | None,
        obstruction_key: str,
    ) -> CancellationNode:
        """Create and register a new CancellationNode.

        Args:
            task_id: The task associated with this cancellation.
            reason: Human-readable reason for the cancellation.
            parent_id: The parent node_id, or None for root.
            obstruction_key: The sheaf obstruction key.

        Returns:
            The newly created CancellationNode.

        Raises:
            ValueError: If *task_id* or *obstruction_key* are empty.

        Example::

            node = cta.create_node("task-1", "deadline exceeded", None, "obs:dl")
        """
        if not task_id:
            raise ValueError("task_id must not be empty")
        if not obstruction_key:
            raise ValueError("obstruction_key must not be empty")

        node = CancellationNode(
            node_id=_new_node_id(),
            task_id=task_id,
            reason=reason or "unspecified",
            parent_node_id=parent_id,
            child_node_ids=(),
            obstruction_key=obstruction_key,
            created_at=time.monotonic(),
            resolved_at=None,
        )
        self._nodes[node.node_id] = node
        self._task_nodes.setdefault(task_id, []).append(node.node_id)
        self._statuses[node.node_id] = (
            CancellationStatus.PROPAGATING if parent_id else CancellationStatus.PENDING
        )
        # Register this node as a child of its parent.
        if parent_id and parent_id in self._nodes:
            self._children_extra.setdefault(parent_id, []).append(node.node_id)
        _log.debug("Created cancellation node %s for task %s", node.node_id, task_id)
        return node

    def resolve_node(self, node_id: str) -> bool:
        """Mark a cancellation node as resolved.

        Args:
            node_id: The node to resolve.

        Returns:
            True if the node was found and updated; False otherwise.

        Example::

            success = cta.resolve_node("n001")
        """
        if node_id not in self._nodes:
            _log.warning("resolve_node: unknown node_id %s", node_id)
            return False
        old_node = self._nodes[node_id]
        resolved = CancellationNode(
            node_id=old_node.node_id,
            task_id=old_node.task_id,
            reason=old_node.reason,
            parent_node_id=old_node.parent_node_id,
            child_node_ids=old_node.child_node_ids,
            obstruction_key=old_node.obstruction_key,
            created_at=old_node.created_at,
            resolved_at=time.monotonic(),
        )
        self._nodes[node_id] = resolved
        self._statuses[node_id] = CancellationStatus.RESOLVED
        _log.debug("Resolved cancellation node %s", node_id)
        return True

    def shield_node(self, node_id: str) -> bool:
        """Mark a cancellation node as shielded from further propagation.

        Args:
            node_id: The node to shield.

        Returns:
            True if the node was found and shielded; False otherwise.

        Example::

            cta.shield_node("n001")
        """
        if node_id not in self._nodes:
            return False
        self._statuses[node_id] = CancellationStatus.SHIELDED
        _log.debug("Shielded cancellation node %s", node_id)
        return True

    def propagation_depth(self, node_id: str) -> int:
        """Return the depth of *node_id* in the cancellation tree.

        The root node has depth 0.  Each parent link adds 1.

        Args:
            node_id: The node to measure.

        Returns:
            Non-negative integer depth.

        Raises:
            RuntimeError: If a cycle is detected in parent pointers.

        Example::

            depth = cta.propagation_depth("n002")
        """
        depth = 0
        visited: set[str] = set()
        current_id: str | None = node_id
        while current_id is not None:
            if current_id in visited:
                raise RuntimeError(f"Cycle detected at node {current_id!r}")
            visited.add(current_id)
            node = self._nodes.get(current_id)
            if node is None:
                break
            current_id = node.parent_node_id
            if current_id is not None:
                depth += 1
        return depth

    def affected_tasks(self, node_id: str) -> list[str]:
        """Return all task_ids in the subtree rooted at *node_id*.

        Args:
            node_id: The root of the subtree to walk.

        Returns:
            Deduplicated list of task_id strings.

        Example::

            tasks = cta.affected_tasks("n001")
        """
        tasks: list[str] = []
        queue: deque[str] = deque([node_id])
        visited: set[str] = set()
        while queue:
            current = queue.popleft()
            if current in visited:
                continue
            visited.add(current)
            node = self._nodes.get(current)
            if node is None:
                continue
            if node.task_id not in tasks:
                tasks.append(node.task_id)
            children = list(node.child_node_ids) + self._children_extra.get(current, [])
            queue.extend(children)
        return tasks

    def find_root_cause(self, node_id: str) -> CancellationNode | None:
        """Walk parent pointers to find the root cause node.

        Args:
            node_id: Any node in the tree.

        Returns:
            The root CancellationNode (parent_node_id is None), or None if the
            starting node is not found.

        Example::

            root = cta.find_root_cause("n003")
        """
        visited: set[str] = set()
        current: str | None = node_id
        last_node: CancellationNode | None = None
        while current is not None:
            if current in visited:
                break
            visited.add(current)
            node = self._nodes.get(current)
            if node is None:
                break
            last_node = node
            if node.parent_node_id is None:
                return node
            current = node.parent_node_id
        return last_node

    def compute_obstruction_set(self, node_id: str) -> frozenset[str]:
        """Collect all obstruction keys in the subtree rooted at *node_id*.

        Args:
            node_id: Root of the subtree to walk.

        Returns:
            Frozenset of obstruction key strings.

        Example::

            obstructions = cta.compute_obstruction_set("n001")
        """
        keys: set[str] = set()
        queue: deque[str] = deque([node_id])
        visited: set[str] = set()
        while queue:
            current = queue.popleft()
            if current in visited:
                continue
            visited.add(current)
            node = self._nodes.get(current)
            if node is None:
                continue
            keys.add(node.obstruction_key)
            children = list(node.child_node_ids) + self._children_extra.get(current, [])
            queue.extend(children)
        return frozenset(keys)

    def cancellation_tree(self, root_id: str) -> dict[str, object]:
        """Build a nested dict representation of the cancellation subtree.

        Args:
            root_id: The node_id of the subtree root.

        Returns:
            Nested dict with ``node``, ``status``, ``children`` fields.

        Example::

            tree = cta.cancellation_tree("n001")
        """
        def _build(nid: str, visited: set[str]) -> dict[str, object]:
            if nid in visited:
                return {"node_id": nid, "error": "cycle"}
            visited.add(nid)
            node = self._nodes.get(nid)
            if node is None:
                return {"node_id": nid, "error": "not_found"}
            children_ids = list(node.child_node_ids) + self._children_extra.get(nid, [])
            return {
                "node": node.to_dict(),
                "status": self._statuses.get(nid, CancellationStatus.PENDING).value,
                "children": [_build(cid, visited) for cid in children_ids],
            }

        return _build(root_id, set())

    def export_nodes(self) -> list[dict[str, object]]:
        """Export all registered cancellation nodes as plain dicts.

        Returns:
            List of serialised CancellationNode dicts each augmented with
            the current status.
        """
        result: list[dict[str, object]] = []
        for node in self._nodes.values():
            d = node.to_dict()
            d["status"] = self._statuses.get(node.node_id, CancellationStatus.PENDING).value
            # Merge in any extra children registered after creation.
            extra = self._children_extra.get(node.node_id, [])
            d["child_node_ids"] = list(node.child_node_ids) + extra
            result.append(d)
        return result

    def stats(self) -> dict[str, object]:
        """Return summary statistics for the cancellation tree analyzer.

        Returns:
            Dict with: ``total_nodes``, ``resolved``, ``shielded``,
            ``propagating``, ``pending``, ``tasks``.
        """
        statuses = list(self._statuses.values())
        return {
            "total_nodes": len(self._nodes),
            "resolved": statuses.count(CancellationStatus.RESOLVED),
            "shielded": statuses.count(CancellationStatus.SHIELDED),
            "propagating": statuses.count(CancellationStatus.PROPAGATING),
            "pending": statuses.count(CancellationStatus.PENDING),
            "tasks": list(self._task_nodes.keys()),
        }


@dataclass
class ExceptionGroupAnalyzer:
    """Analyzes ExceptionGroup nodes as simultaneous obstruction records in the sheaf.

    Provides methods for recording groups, pattern-matching exceptions,
    computing obstruction unions, and partial resolution.

    Attributes:
        _groups: All registered ExceptionGroupNode objects keyed by group_id.
        _task_groups: Maps task_id → list of group_ids.

    Example::

        ega = ExceptionGroupAnalyzer()
        node = ega.record_group("task-1", ("ValueError",), ("obs:val",), False)
    """

    _groups: dict[str, ExceptionGroupNode] = field(default_factory=dict)
    _task_groups: dict[str, list[str]] = field(default_factory=dict)

    def record_group(
        self,
        task_id: str,
        exception_types: tuple[str, ...],
        obstruction_keys: tuple[str, ...],
        is_partial: bool,
    ) -> ExceptionGroupNode:
        """Create and register a new ExceptionGroupNode.

        Args:
            task_id: The task that raised the ExceptionGroup.
            exception_types: Tuple of exception class name strings.
            obstruction_keys: Tuple of sheaf obstruction keys.
            is_partial: True when the group is a partial split.

        Returns:
            The newly created ExceptionGroupNode.

        Raises:
            ValueError: If *exception_types* is empty.

        Example::

            node = ega.record_group("t1", ("ValueError",), ("obs:v",), False)
        """
        if not exception_types:
            raise ValueError("exception_types must contain at least one type")

        norm_types = tuple(_normalise_type_name(t) for t in exception_types)
        matched = 0 if is_partial else len(norm_types)
        unmatched = len(norm_types) - matched

        group = ExceptionGroupNode(
            group_id=_new_group_id(),
            task_id=task_id,
            exception_types=norm_types,
            obstruction_keys=tuple(obstruction_keys),
            is_partial=is_partial,
            created_at=time.monotonic(),
            matched_count=matched,
            unmatched_count=unmatched,
        )
        self._groups[group.group_id] = group
        self._task_groups.setdefault(task_id, []).append(group.group_id)
        _log.debug("Recorded exception group %s for task %s", group.group_id, task_id)
        return group

    def match_exceptions(
        self,
        group_id: str,
        pattern_types: list[str],
    ) -> tuple[list[str], list[str]]:
        """Match exception types in a group against a list of handler patterns.

        Args:
            group_id: The group to match against.
            pattern_types: List of exception type names to handle.

        Returns:
            Tuple of (matched_types, unmatched_types).

        Raises:
            KeyError: If *group_id* is not found.

        Example::

            matched, unmatched = ega.match_exceptions("g001", ["ValueError"])
        """
        group = self._groups[group_id]
        norm_patterns = {_normalise_type_name(p) for p in pattern_types}
        matched = [t for t in group.exception_types if t in norm_patterns]
        unmatched = [t for t in group.exception_types if t not in norm_patterns]
        return matched, unmatched

    def obstruction_union(self, group_ids: list[str]) -> frozenset[str]:
        """Return the union of all obstruction keys across the given groups.

        Args:
            group_ids: List of group_ids to union.

        Returns:
            Frozenset of all obstruction key strings.

        Example::

            keys = ega.obstruction_union(["g001", "g002"])
        """
        keys: set[str] = set()
        for gid in group_ids:
            group = self._groups.get(gid)
            if group is not None:
                keys.update(group.obstruction_keys)
        return frozenset(keys)

    def partial_resolution(
        self,
        group_id: str,
        resolved_types: list[str],
    ) -> ExceptionGroupNode:
        """Produce a new ExceptionGroupNode with some types marked as resolved.

        The original node is replaced in the registry.

        Args:
            group_id: The group to partially resolve.
            resolved_types: Exception type names that have been handled.

        Returns:
            The updated ExceptionGroupNode.

        Raises:
            KeyError: If *group_id* is not found.

        Example::

            updated = ega.partial_resolution("g001", ["ValueError"])
        """
        group = self._groups[group_id]
        norm_resolved = {_normalise_type_name(t) for t in resolved_types}
        new_matched = sum(1 for t in group.exception_types if t in norm_resolved)
        new_unmatched = len(group.exception_types) - new_matched
        updated = ExceptionGroupNode(
            group_id=group.group_id,
            task_id=group.task_id,
            exception_types=group.exception_types,
            obstruction_keys=group.obstruction_keys,
            is_partial=new_unmatched > 0,
            created_at=group.created_at,
            matched_count=new_matched,
            unmatched_count=new_unmatched,
        )
        self._groups[group_id] = updated
        _log.debug("Partially resolved group %s: matched=%d unmatched=%d",
                   group_id, new_matched, new_unmatched)
        return updated

    def group_severity(self, group_id: str) -> float:
        """Compute a severity score for an exception group.

        Args:
            group_id: The group to score.

        Returns:
            Float in [0.0, 1.0].

        Raises:
            KeyError: If *group_id* is not found.

        Example::

            sev = ega.group_severity("g001")
        """
        group = self._groups[group_id]
        base_sev = _severity_for_types(group.exception_types)
        # Partial groups (unmatched remain) get a 10% penalty.
        partial_penalty = 0.1 if group.is_partial and group.unmatched_count > 0 else 0.0
        return min(base_sev + partial_penalty, 1.0)

    def export_groups(self) -> list[dict[str, object]]:
        """Export all registered exception group nodes as plain dicts.

        Returns:
            List of serialised ExceptionGroupNode dicts with severity field.
        """
        result: list[dict[str, object]] = []
        for gid, group in self._groups.items():
            d = group.to_dict()
            try:
                d["severity"] = self.group_severity(gid)
            except Exception:
                d["severity"] = 0.0
            result.append(d)
        return result

    def stats(self) -> dict[str, object]:
        """Return summary statistics for the exception group analyzer.

        Returns:
            Dict with: ``total_groups``, ``partial_groups``,
            ``total_obstruction_keys``, ``tasks``.
        """
        groups = list(self._groups.values())
        partial = sum(1 for g in groups if g.is_partial)
        all_keys: set[str] = set()
        for g in groups:
            all_keys.update(g.obstruction_keys)
        return {
            "total_groups": len(groups),
            "partial_groups": partial,
            "total_obstruction_keys": len(all_keys),
            "tasks": list(self._task_groups.keys()),
        }


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------

@dataclass
class CancellationExceptionGroupSemanticsCoordinator:
    """Orchestrates cancellation and exception-group analysis.

    Combines a CancellationTreeAnalyzer and ExceptionGroupAnalyzer to provide
    a single entry point for the full Coordinator-Analyzer-Witness workflow
    around cancellation and exception-group semantics.

    Attributes:
        _ct_analyzer: The underlying CancellationTreeAnalyzer.
        _eg_analyzer: The underlying ExceptionGroupAnalyzer.
        _session_id: Unique identifier for this coordinator session.
        _witness_log: Ordered log of all witness events.

    Example::

        coord = CancellationExceptionGroupSemanticsCoordinator()
        coord.cancel_task("task-1", "deadline", None, "obs:dl")
    """

    _ct_analyzer: CancellationTreeAnalyzer = field(default_factory=CancellationTreeAnalyzer)
    _eg_analyzer: ExceptionGroupAnalyzer = field(default_factory=ExceptionGroupAnalyzer)
    _session_id: str = field(default_factory=lambda: _new_node_id())
    _witness_log: list[dict[str, object]] = field(default_factory=list)

    def _witness(self, event_type: str, data: dict[str, object]) -> None:
        """Append a witness event to the internal log.

        Args:
            event_type: Short string naming the event kind.
            data: Payload dict for the event.
        """
        self._witness_log.append({
            "event_type": event_type,
            "session_id": self._session_id,
            "timestamp": time.monotonic(),
            **data,
        })

    def cancel_task(
        self,
        task_id: str,
        reason: str,
        parent_id: str | None,
        obstruction_key: str,
    ) -> dict[str, object]:
        """Cancel a task by creating a CancellationNode and witnessing it.

        Args:
            task_id: The task to cancel.
            reason: Why this task is being cancelled.
            parent_id: The parent cancellation node_id, or None.
            obstruction_key: The sheaf obstruction key.

        Returns:
            Dict with ``node_id``, ``task_id``, ``status``, ``depth``.

        Example::

            result = coord.cancel_task("t1", "user_request", None, "obs:user")
        """
        node = self._ct_analyzer.create_node(task_id, reason, parent_id, obstruction_key)
        depth = self._ct_analyzer.propagation_depth(node.node_id)
        status = self._ct_analyzer._statuses.get(node.node_id, CancellationStatus.PENDING)
        self._witness("cancel_task", {
            "node_id": node.node_id,
            "task_id": task_id,
            "depth": depth,
            "status": status.value,
        })
        return {
            "node_id": node.node_id,
            "task_id": task_id,
            "status": status.value,
            "depth": depth,
        }

    def raise_exception_group(
        self,
        task_id: str,
        exc_types: tuple[str, ...],
        obstructions: tuple[str, ...],
        partial: bool,
    ) -> dict[str, object]:
        """Record an ExceptionGroup being raised by a task and witness it.

        Args:
            task_id: The task raising the group.
            exc_types: Exception class name strings.
            obstructions: Sheaf obstruction keys, one per exception.
            partial: Whether this is a partial split (some exceptions unhandled).

        Returns:
            Dict with ``group_id``, ``task_id``, ``severity``,
            ``matched_count``, ``unmatched_count``.

        Example::

            r = coord.raise_exception_group("t1", ("ValueError",), ("obs:v",), False)
        """
        group = self._eg_analyzer.record_group(task_id, exc_types, obstructions, partial)
        severity = self._eg_analyzer.group_severity(group.group_id)
        self._witness("raise_exception_group", {
            "group_id": group.group_id,
            "task_id": task_id,
            "severity": severity,
        })
        return {
            "group_id": group.group_id,
            "task_id": task_id,
            "severity": round(severity, 4),
            "matched_count": group.matched_count,
            "unmatched_count": group.unmatched_count,
        }

    def propagate_cancellation(self, root_id: str) -> list[str]:
        """Propagate a cancellation from *root_id* to all child tasks.

        Marks each non-shielded child node as PROPAGATING and collects
        affected task_ids.

        Args:
            root_id: The root cancellation node_id.

        Returns:
            List of task_ids affected by the propagation.

        Example::

            affected = coord.propagate_cancellation("n001")
        """
        affected = self._ct_analyzer.affected_tasks(root_id)
        all_nodes = list(self._ct_analyzer._nodes.keys())
        for nid in all_nodes:
            current_status = self._ct_analyzer._statuses.get(nid)
            if current_status not in (CancellationStatus.SHIELDED, CancellationStatus.RESOLVED):
                self._ct_analyzer._statuses[nid] = CancellationStatus.PROPAGATING
        self._witness("propagate_cancellation", {"root_id": root_id, "affected": affected})
        return affected

    def resolve_group(
        self,
        group_id: str,
        handled_types: list[str],
    ) -> dict[str, object]:
        """Partially resolve an ExceptionGroup and witness the resolution.

        Args:
            group_id: The group to resolve.
            handled_types: Exception type names being handled.

        Returns:
            Dict with ``group_id``, ``matched_count``, ``unmatched_count``,
            ``is_partial``, ``resolution_ratio``.

        Raises:
            KeyError: If *group_id* is not found.

        Example::

            r = coord.resolve_group("g001", ["ValueError"])
        """
        updated = self._eg_analyzer.partial_resolution(group_id, handled_types)
        ratio = updated.resolution_ratio()
        self._witness("resolve_group", {
            "group_id": group_id,
            "resolution_ratio": ratio,
        })
        return {
            "group_id": group_id,
            "matched_count": updated.matched_count,
            "unmatched_count": updated.unmatched_count,
            "is_partial": updated.is_partial,
            "resolution_ratio": round(ratio, 4),
        }

    def full_report(self) -> dict[str, object]:
        """Produce a comprehensive report combining cancellation and exception-group data.

        Returns:
            Dict with ``session_id``, ``cancellation_stats``,
            ``exception_group_stats``, ``cancellation_nodes``,
            ``exception_groups``, ``witness_log``.

        Example::

            report = coord.full_report()
        """
        return {
            "session_id": self._session_id,
            "cancellation_stats": self._ct_analyzer.stats(),
            "exception_group_stats": self._eg_analyzer.stats(),
            "cancellation_nodes": self._ct_analyzer.export_nodes(),
            "exception_groups": self._eg_analyzer.export_groups(),
            "witness_log": list(self._witness_log),
        }

    def reset(self) -> None:
        """Clear all state in the coordinator.

        Example::

            coord.reset()
        """
        self._ct_analyzer = CancellationTreeAnalyzer()
        self._eg_analyzer = ExceptionGroupAnalyzer()
        self._session_id = _new_node_id()
        self._witness_log.clear()
        _log.info("CancellationExceptionGroupSemanticsCoordinator reset; session=%s", self._session_id)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def make_coordinator() -> CancellationExceptionGroupSemanticsCoordinator:
    """Convenience factory that returns a ready-to-use coordinator.

    Returns:
        A freshly constructed CancellationExceptionGroupSemanticsCoordinator.
    """
    return CancellationExceptionGroupSemanticsCoordinator()


def cancellation_status_names() -> list[str]:
    """Return all CancellationStatus value names.

    Returns:
        Sorted list of status name strings.
    """
    return sorted(s.value for s in CancellationStatus)


def describe_cancellation_node(node: CancellationNode) -> str:
    """Return a human-readable one-liner for a CancellationNode.

    Args:
        node: The CancellationNode to describe.

    Returns:
        Formatted string with node_id, task_id, reason, and resolution state.
    """
    state = "resolved" if node.is_resolved() else "unresolved"
    root_label = " [ROOT]" if node.is_root() else ""
    return (
        f"CancellationNode({node.node_id!r}) task={node.task_id!r} "
        f"reason={node.reason!r} {state}{root_label}"
    )


def exception_group_summary(group: ExceptionGroupNode) -> str:
    """Return a human-readable summary for an ExceptionGroupNode.

    Args:
        group: The ExceptionGroupNode to summarise.

    Returns:
        Formatted string describing the group.
    """
    partial_label = " [partial]" if group.is_partial else " [complete]"
    types_str = ", ".join(group.exception_types)
    return (
        f"ExceptionGroup({group.group_id!r}) task={group.task_id!r} "
        f"types=[{types_str}] matched={group.matched_count} "
        f"unmatched={group.unmatched_count}{partial_label}"
    )


__all__ = [
    "CancellationStatus",
    "CancellationNode",
    "ExceptionGroupNode",
    "CancellationTreeAnalyzer",
    "ExceptionGroupAnalyzer",
    "CancellationExceptionGroupSemanticsCoordinator",
    "make_coordinator",
    "cancellation_status_names",
    "describe_cancellation_node",
    "exception_group_summary",
]

# copilot: s03 — cancellation and exception-group semantics; Ch24 §3
