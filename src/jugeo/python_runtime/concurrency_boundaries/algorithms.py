"""Core algorithms for the concurrency_boundaries package.

Implements scope analysis, cancellation handling, exception group processing,
and boundary enforcement algorithms grounded in Ch24 sheaf-theoretic semantics.

Sheaf-theoretic grounding
--------------------------
The concurrent execution context is modelled as a presheaf ``F`` over a
semantic site whose objects are task-local scopes.  A *ConcurrencyScope* is a
finite open cover ``{U_i}`` of the global scope; each ``TaskLocalSection``
is a section of ``F`` over one ``U_i``.  The algorithms in this module answer
three fundamental questions that arise in this setting:

1. **Coverage** — what fraction of sections are coherent (not cancelled or
   failed)?  A low coverage score signals that the open cover is failing to
   produce a global section (i.e. the sheaf has a non-trivial first cohomology).

2. **Cancellation discharge** — a cancelled task carries an *obstruction*
   (a Čech 1-cocycle).  The discharge protocol converts a pending obstruction
   into a resolved one, allowing the global computation to proceed or gracefully
   degrade.

3. **Boundary enforcement** — a process boundary is a morphism of sites;
   crossing it without an explicit cover morphism violates the locality axiom
   of the sheaf.  The ``BoundaryEnforcer`` prevents such crossings.

Module-level utilities
-----------------------
Two standalone functions support batch analysis:

* :func:`compute_obstruction_score` — weighted severity score across a list
  of CancellationRecords.
* :func:`rank_sections_by_stability` — sort sections by operational status.
"""

from __future__ import annotations

import time
import uuid
from collections import deque
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
        SUSPENDED = "SUSPENDED"
        BOUNDARY_CROSSED = "BOUNDARY_CROSSED"

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


# ══════════════════════════════════════════════════════
# Internal constants
# ══════════════════════════════════════════════════════

# Status ordering for rank_sections_by_stability (lower index = more stable)
_STATUS_ORDER: dict[str, int] = {
    "COMPLETED": 0,
    "ACTIVE": 1,
    "SUSPENDED": 2,
    "FAILED": 3,
    "CANCELLED": 4,
    "BOUNDARY_CROSSED": 5,
}

# Cancellation reason weight map for compute_obstruction_score
_REASON_WEIGHTS: dict[str, float] = {
    "TIMEOUT": 0.3,
    "USER_REQUESTED": 0.1,
    "PARENT_CANCELLED": 0.2,
    "RESOURCE_EXHAUSTED": 0.5,
    "OBSTRUCTION": 0.4,
    "POLICY_VIOLATION": 0.6,
}


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
# CLASS: ConcurrencyAnalyzer
# ══════════════════════════════════════════════════════


class ConcurrencyAnalyzer:
    """Analyzes concurrency scopes for section coverage and boundary compliance.

    The ConcurrencyAnalyzer examines a ConcurrencyScope's sections, cancellation
    records, and boundary crossings to produce quantitative metrics about the
    health and compliance of the concurrent execution context.

    Theory reference: Ch24 — section coverage and boundary analysis.
    """

    def __init__(self) -> None:
        self._analyzed_scopes: dict[str, dict[str, Any]] = {}
        self._analysis_log: list[dict[str, Any]] = []
        self._violation_cache: dict[str, list[dict[str, Any]]] = {}

    # ── single-scope analysis ──────────────────────────

    def analyze_scope(self, scope: ConcurrencyScope) -> dict[str, Any]:
        """Run a comprehensive analysis of a single ConcurrencyScope.

        Computes counts and rates across all sections in the scope, then
        stores the result in the internal cache for later export.

        Args:
            scope: The ConcurrencyScope to analyse.

        Returns:
            Dict containing ``scope_id``, ``total_sections``, ``active_count``,
            ``cancelled_count``, ``completed_count``, ``failed_count``,
            ``cancellation_rate``, ``scope_depth``, ``exception_density``,
            ``coverage_score``, and ``analysed_at``.
        """
        sections = _scope_sections(scope)
        total = len(sections)

        active_count = sum(1 for s in sections if _section_status(s) == "ACTIVE")
        cancelled_count = sum(1 for s in sections if _section_status(s) == "CANCELLED")
        completed_count = sum(1 for s in sections if _section_status(s) == "COMPLETED")
        failed_count = sum(1 for s in sections if _section_status(s) == "FAILED")
        suspended_count = sum(1 for s in sections if _section_status(s) == "SUSPENDED")
        boundary_crossed_count = sum(1 for s in sections if _section_status(s) == "BOUNDARY_CROSSED")

        cancellation_rate = cancelled_count / total if total > 0 else 0.0
        exception_density = cancelled_count / max(total, 1)
        coverage_score = (active_count + completed_count) / total if total > 0 else 0.0
        scope_depth = self.compute_scope_depth(scope)

        result: dict[str, Any] = {
            "scope_id": scope.scope_id,
            "total_sections": total,
            "active_count": active_count,
            "cancelled_count": cancelled_count,
            "completed_count": completed_count,
            "failed_count": failed_count,
            "suspended_count": suspended_count,
            "boundary_crossed_count": boundary_crossed_count,
            "cancellation_rate": cancellation_rate,
            "scope_depth": scope_depth,
            "exception_density": exception_density,
            "coverage_score": coverage_score,
            "analysed_at": time.time(),
        }

        self._analyzed_scopes[scope.scope_id] = result
        self._analysis_log.append(
            {
                "event": "analyze_scope",
                "scope_id": scope.scope_id,
                "coverage_score": coverage_score,
                "timestamp": time.time(),
            }
        )
        return result

    # ── boundary violation detection ───────────────────

    def detect_boundary_violations(
        self,
        scope: ConcurrencyScope,
        boundaries: list[ProcessBoundary],
    ) -> list[dict[str, Any]]:
        """Detect potential boundary violations among cancelled sections.

        For each cancelled section in the scope, this method checks whether the
        section's task_id suggests an illegal cross-boundary operation — i.e.,
        the task_id does NOT appear in any boundary's ``allowed_section_ids``,
        yet the section was cancelled (which may indicate a forced termination
        due to an illegal crossing).

        Args:
            scope: The ConcurrencyScope to inspect.
            boundaries: List of active ProcessBoundary instances.

        Returns:
            List of violation candidate dicts, each with ``section_id``,
            ``task_id``, ``status``, ``potential_violation``, and a
            ``suggested_boundary`` when one is identified.
        """
        violations: list[dict[str, Any]] = []
        cancelled = [s for s in _scope_sections(scope) if _section_status(s) == "CANCELLED"]

        all_allowed: set[str] = set()
        for boundary in boundaries:
            all_allowed.update(boundary.allowed_section_ids)

        for section in cancelled:
            if section.section_id not in all_allowed:
                # Section was cancelled without an explicit boundary permit —
                # flag as a potential violation.
                suggested = None
                for boundary in boundaries:
                    if (
                        section.task_id
                        and section.task_id in boundary.source_process_id
                    ):
                        suggested = boundary.boundary_id
                        break

                violation_candidate: dict[str, Any] = {
                    "section_id": section.section_id,
                    "task_id": section.task_id,
                    "status": _section_status(section),
                    "potential_violation": True,
                    "reason": "cancelled_section_not_in_any_boundary_allowed_list",
                    "suggested_boundary": suggested,
                    "detected_at": time.time(),
                }
                violations.append(violation_candidate)

        self._violation_cache[scope.scope_id] = violations
        return violations

    # ── metric helpers ─────────────────────────────────

    def compute_scope_depth(self, scope: ConcurrencyScope) -> int:
        """Return the nesting depth of the scope.

        Delegates to ``scope.scope_depth()`` — the scope itself knows its
        depth within the task tree.

        Args:
            scope: The scope to query.

        Returns:
            Non-negative integer depth.
        """
        return scope.scope_depth()

    def section_coverage(self, scope: ConcurrencyScope) -> float:
        """Return the fraction of sections that are ACTIVE or COMPLETED.

        A value of 1.0 indicates all sections are healthy; 0.0 indicates all
        are in a terminal or suspended state.

        Args:
            scope: The scope to measure.

        Returns:
            Float in ``[0.0, 1.0]``.
        """
        sections = _scope_sections(scope)
        total = len(sections)
        if total == 0:
            return 0.0
        healthy = sum(
            1
            for s in sections
            if _section_status(s) in ("ACTIVE", "COMPLETED")
        )
        return healthy / total

    def cancellation_rate(self, scope: ConcurrencyScope) -> float:
        """Return the fraction of sections that have been CANCELLED.

        Args:
            scope: The scope to measure.

        Returns:
            Float in ``[0.0, 1.0]``.
        """
        sections = _scope_sections(scope)
        total = len(sections)
        if total == 0:
            return 0.0
        return sum(1 for s in sections if _section_status(s) == "CANCELLED") / total

    def exception_density(self, scope: ConcurrencyScope) -> float:
        """Return the number of cancellations per section (exception density).

        Higher values indicate a more obstruction-dense scope.

        Args:
            scope: The scope to measure.

        Returns:
            Non-negative float.
        """
        sections = _scope_sections(scope)
        cancellation_count = sum(
            1 for s in sections if _section_status(s) == "CANCELLED"
        )
        return cancellation_count / max(len(sections), 1)

    # ── tree analysis ──────────────────────────────────

    def analyze_task_tree(
        self,
        root_scope: ConcurrencyScope,
        all_scopes: dict[str, ConcurrencyScope],
    ) -> dict[str, Any]:
        """Perform a BFS traversal of the task tree and aggregate metrics.

        Starting from *root_scope*, follows ``child_scope_ids`` links to visit
        all reachable scopes and computes aggregate statistics across the entire
        tree.

        Args:
            root_scope: The root of the task tree.
            all_scopes: Mapping from scope_id to ConcurrencyScope for lookup.

        Returns:
            Dict with ``total_scopes``, ``total_sections``, ``avg_coverage``,
            ``max_depth``, ``total_cancellations``, ``scope_ids_visited``.
        """
        visited: set[str] = set()
        queue: deque[ConcurrencyScope] = deque([root_scope])

        total_scopes = 0
        total_sections = 0
        total_cancellations = 0
        total_coverage = 0.0
        max_depth = 0

        while queue:
            scope = queue.popleft()
            if scope.scope_id in visited:
                continue
            visited.add(scope.scope_id)

            analysis = self.analyze_scope(scope)
            total_scopes += 1
            total_sections += analysis["total_sections"]
            total_cancellations += analysis["cancelled_count"]
            total_coverage += analysis["coverage_score"]
            depth = analysis["scope_depth"]
            if depth > max_depth:
                max_depth = depth

            for child_id in scope.child_scope_ids:
                child = all_scopes.get(child_id)
                if child is not None and child.scope_id not in visited:
                    queue.append(child)

        avg_coverage = total_coverage / max(total_scopes, 1)

        return {
            "total_scopes": total_scopes,
            "total_sections": total_sections,
            "avg_coverage": avg_coverage,
            "max_depth": max_depth,
            "total_cancellations": total_cancellations,
            "scope_ids_visited": sorted(visited),
        }

    def export_analysis(self) -> dict[str, dict[str, Any]]:
        """Return a copy of all stored scope analysis results.

        Returns:
            Dict of ``{scope_id: analysis_result}``.
        """
        return dict(self._analyzed_scopes)


# ══════════════════════════════════════════════════════
# CLASS: CancellationHandler
# ══════════════════════════════════════════════════════


class CancellationHandler:
    """Handles cancellation obstructions according to the discharge protocol.

    When a task is cancelled, its obstruction must be properly handled — not
    silently dropped. This handler implements the discharge protocol:
    acknowledge -> cleanup -> record discharge. It also manages propagation
    to child tasks and tracks pending vs discharged obstructions.

    Theory reference: Ch24 §2 — THEOREM_CANCELLATION_DISCHARGE.
    """

    def __init__(self) -> None:
        self._pending: dict[str, CancellationRecord] = {}
        self._discharged: dict[str, dict[str, Any]] = {}
        self._shielded_tasks: set[str] = set()
        self._propagation_graph: dict[str, list[str]] = {}
        self._discharge_log: list[dict[str, Any]] = []

    # ── ingestion ──────────────────────────────────────

    def handle_cancellation(
        self,
        record: CancellationRecord,
        auto_discharge: bool = False,
    ) -> dict[str, Any]:
        """Accept a CancellationRecord and optionally discharge it immediately.

        The record is first registered in the pending queue. If *auto_discharge*
        is True, :meth:`discharge_obstruction` is called immediately so the
        caller does not need to drive the discharge step separately.

        Args:
            record: The CancellationRecord to handle.
            auto_discharge: When True, the obstruction is discharged before
                returning.

        Returns:
            Dict with ``handled`` (True), ``obstruction_key``, ``task_id``,
            ``auto_discharged`` (bool), and an optional ``discharge_result``.
        """
        self._pending[record.obstruction_key] = record
        result: dict[str, Any] = {
            "handled": True,
            "obstruction_key": record.obstruction_key,
            "task_id": record.task_id,
            "auto_discharged": False,
        }

        if auto_discharge:
            discharge_result = self.discharge_obstruction(
                record.obstruction_key,
                handler_id="auto",
                cleanup_actions=["auto_cleanup"],
            )
            result["auto_discharged"] = True
            result["discharge_result"] = discharge_result

        return result

    # ── discharge ──────────────────────────────────────

    def discharge_obstruction(
        self,
        obstruction_key: str,
        handler_id: str = "default",
        cleanup_actions: list[str] | None = None,
    ) -> dict[str, Any]:
        """Discharge a pending obstruction, moving it from pending to discharged.

        Args:
            obstruction_key: The key of the obstruction to discharge.
            handler_id: Identifier of the handler performing the discharge.
            cleanup_actions: Optional list of cleanup action names that were
                performed before discharge.

        Returns:
            Dict with ``discharged`` (True), ``obstruction_key``,
            ``handler_id``, ``actions``, and ``timestamp``.

        Raises:
            KeyError: If *obstruction_key* is not in the pending queue.
        """
        if obstruction_key not in self._pending:
            raise KeyError(
                f"No pending obstruction with key {obstruction_key!r}. "
                "Cannot discharge an obstruction that was never registered."
            )

        record = self._pending.pop(obstruction_key)

        actions = cleanup_actions or []
        metadata: dict[str, Any] = {
            "obstruction_key": obstruction_key,
            "task_id": record.task_id,
            "handler_id": handler_id,
            "actions": actions,
            "discharged_at": time.time(),
            "original_reason": str(record.reason),
        }
        self._discharged[obstruction_key] = metadata
        self.record_discharge(obstruction_key, metadata)

        return {
            "discharged": True,
            "obstruction_key": obstruction_key,
            "handler_id": handler_id,
            "actions": actions,
            "timestamp": metadata["discharged_at"],
        }

    # ── propagation ────────────────────────────────────

    def propagate_to_children(
        self,
        record: CancellationRecord,
        child_task_ids: list[str] | None = None,
    ) -> list[CancellationRecord]:
        """Generate and register child CancellationRecords for sub-tasks.

        Child tasks that have been shielded via :meth:`shield_section` are
        skipped.  The propagation graph is updated with the parent->child
        relationship for later traversal.

        Args:
            record: The parent CancellationRecord driving propagation.
            child_task_ids: Explicit list of child task IDs to notify. If None,
                looks up children from the internal propagation graph.

        Returns:
            List of newly created and pending CancellationRecord instances for
            each non-shielded child.
        """
        resolved_children: list[str] = (
            child_task_ids
            if child_task_ids is not None
            else self._propagation_graph.get(record.task_id, [])
        )

        if child_task_ids is not None:
            existing = self._propagation_graph.get(record.task_id, [])
            for cid in child_task_ids:
                if cid not in existing:
                    existing.append(cid)
            self._propagation_graph[record.task_id] = existing

        new_records: list[CancellationRecord] = []
        for child_id in resolved_children:
            if child_id in self._shielded_tasks:
                continue

            child_key = f"obs:{child_id}:PARENT_CANCELLED:{uuid.uuid4().hex[:8]}"
            child_record = make_cancellation_record(
                task_id=child_id,
                reason=CancellationReason.PARENT_CANCELLED,
                obstruction_key=child_key,
                error_message=(
                    f"Parent task {record.task_id!r} was cancelled; "
                    f"propagating to child {child_id!r}."
                ),
                parent_record_id=record.record_id,
            )
            self._pending[child_key] = child_record
            new_records.append(child_record)

        return new_records

    # ── shielding ──────────────────────────────────────

    def shield_section(self, task_id: str) -> None:
        """Mark a task as shielded from cancellation propagation.

        Shielded tasks will not receive propagated CancellationRecords when
        their parent is cancelled.  This mirrors the semantics of
        ``asyncio.shield()``.

        Args:
            task_id: The task to shield.
        """
        self._shielded_tasks.add(task_id)

    # ── internal recording ─────────────────────────────

    def record_discharge(self, obstruction_key: str, metadata: dict[str, Any]) -> None:
        """Append a discharge event to the internal discharge log.

        Args:
            obstruction_key: The key being discharged.
            metadata: Metadata dict to store alongside the key.
        """
        self._discharge_log.append(
            {
                "obstruction_key": obstruction_key,
                "metadata": metadata,
                "logged_at": time.time(),
            }
        )

    # ── queries & reporting ────────────────────────────

    def pending_discharges(self) -> list[CancellationRecord]:
        """Return all pending (un-discharged) CancellationRecords.

        Returns:
            List of CancellationRecord instances awaiting discharge.
        """
        return list(self._pending.values())

    def cancellation_report(self) -> dict[str, Any]:
        """Return a structured summary of the handler's current state.

        Returns:
            Dict with ``pending``, ``discharged``, ``shielded_tasks`` (count),
            and ``recent_discharges`` (last 5 entries from the discharge log).
        """
        return {
            "pending": len(self._pending),
            "discharged": len(self._discharged),
            "shielded_tasks": len(self._shielded_tasks),
            "recent_discharges": self._discharge_log[-5:],
        }

    def export_state(self) -> dict[str, Any]:
        """Export the full internal state of the handler.

        Returns:
            Dict with serialised pending records, discharged metadata,
            shielded task IDs, propagation graph, and full discharge log.
        """
        return {
            "pending": {k: v.to_dict() for k, v in self._pending.items()},
            "discharged": dict(self._discharged),
            "shielded_tasks": sorted(self._shielded_tasks),
            "propagation_graph": dict(self._propagation_graph),
            "discharge_log": list(self._discharge_log),
        }


# ══════════════════════════════════════════════════════
# CLASS: ExceptionGroupProcessor (algorithms variant)
# ══════════════════════════════════════════════════════


class ExceptionGroupProcessor:
    """Processes exception groups as multi-obstruction records (algorithms variant).

    This is the algorithm-level processor that takes ExceptionGroupRecord instances
    and applies resolution algorithms to them. Complements s03's class which handles
    raw Python ExceptionGroup wrapping.

    Theory reference: Ch24 §3 — multi-obstruction resolution algorithms.
    """

    def __init__(self) -> None:
        self._groups: dict[str, ExceptionGroupRecord] = {}
        self._resolution_log: list[dict[str, Any]] = []
        self._merge_history: list[dict[str, Any]] = []

    # ── full pipeline ──────────────────────────────────

    def process_group(self, group: ExceptionGroupRecord) -> dict[str, Any]:
        """Run the full processing pipeline on an ExceptionGroupRecord.

        The pipeline consists of:
        1. Split exceptions by type.
        2. Compute a severity score based on exception count and type diversity.
        3. Determine the resolution strategy based on the score.
        4. Register the group for later export.

        Args:
            group: The ExceptionGroupRecord to process.

        Returns:
            Dict with ``group_id``, ``by_type``, ``severity_score``,
            ``resolution_strategy``, ``total_exceptions``,
            ``total_obstructions``, and ``processed_at``.
        """
        self._groups[group.group_id] = group

        by_type = self.split_by_type(group)
        type_count = len(by_type)
        total_exceptions = len(group.exception_records)

        # Severity: more types = higher severity; more exceptions = higher score
        severity_score = min(
            1.0,
            (type_count * 0.3 + total_exceptions * 0.07),
        )

        if severity_score >= 0.8:
            resolution_strategy = "abort"
        elif severity_score >= 0.5:
            resolution_strategy = "partial_retry"
        elif severity_score >= 0.2:
            resolution_strategy = "retry"
        else:
            resolution_strategy = "ignore"

        result: dict[str, Any] = {
            "group_id": group.group_id,
            "by_type": {k: len(v) for k, v in by_type.items()},
            "severity_score": severity_score,
            "resolution_strategy": resolution_strategy,
            "total_exceptions": total_exceptions,
            "total_obstructions": len(group.obstruction_keys),
            "processed_at": time.time(),
        }

        self._resolution_log.append(result)
        return result

    # ── analysis helpers ───────────────────────────────

    def split_by_type(
        self, group: ExceptionGroupRecord
    ) -> dict[str, list[dict[str, Any]]]:
        """Split the exception records in *group* by exception type.

        Args:
            group: The ExceptionGroupRecord to split.

        Returns:
            Dict mapping exception type string to list of exception record dicts.
        """
        by_type: dict[str, list[dict[str, Any]]] = {}
        for rec in group.exception_records:
            exc_type = rec.get("type", "UnknownError")
            by_type.setdefault(exc_type, []).append(rec)
        return by_type

    def filter_obstructions(self, group: ExceptionGroupRecord, pattern: str) -> list[str]:
        """Return obstruction keys from *group* that contain *pattern*.

        Args:
            group: The ExceptionGroupRecord to filter.
            pattern: Substring to match.

        Returns:
            List of matching obstruction key strings.
        """
        return [key for key in group.obstruction_keys if pattern in key]

    def merge_groups(self, groups: list[ExceptionGroupRecord]) -> ExceptionGroupRecord:
        """Merge multiple ExceptionGroupRecords into a single combined record.

        The merged group receives a fresh group_id. The ``task_id`` of the
        first group is used as the primary task_id; all other task IDs are
        stored in the provenance of each copied exception record.

        Args:
            groups: List of ExceptionGroupRecords to merge (must be non-empty).

        Returns:
            A new ExceptionGroupRecord containing the union of all records.

        Raises:
            ValueError: If *groups* is empty.
        """
        if not groups:
            raise ValueError("Cannot merge an empty list of ExceptionGroupRecords.")

        merged_id = uuid.uuid4().hex
        merged_exceptions: list[dict[str, Any]] = []
        merged_keys: list[str] = []
        seen_keys: set[str] = set()
        source_task_ids: list[str] = [g.task_id for g in groups]

        for grp in groups:
            for rec in grp.exception_records:
                annotated = dict(rec)
                annotated["source_group_id"] = grp.group_id
                merged_exceptions.append(annotated)
                okey = rec.get("obstruction_key", "")
                if okey and okey not in seen_keys:
                    merged_keys.append(okey)
                    seen_keys.add(okey)

        merged = ExceptionGroupRecord(
            group_id=merged_id,
            task_id=groups[0].task_id,
            exception_records=merged_exceptions,
            obstruction_keys=merged_keys,
            created_at=time.time(),
        )
        self._groups[merged_id] = merged
        self._merge_history.append(
            {
                "event": "merge_groups",
                "merged_id": merged_id,
                "source_groups": [g.group_id for g in groups],
                "source_task_ids": source_task_ids,
                "merged_at": time.time(),
            }
        )
        return merged

    def flatten_group(self, group: ExceptionGroupRecord) -> list[dict[str, Any]]:
        """Return a fully-flattened list of exception records from *group*.

        Handles nested 'sub_group' entries using iterative BFS to avoid
        recursion-depth issues on deeply nested groups.

        Args:
            group: The ExceptionGroupRecord to flatten.

        Returns:
            Flat list of exception record dicts.
        """
        result: list[dict[str, Any]] = []
        queue: deque[dict[str, Any]] = deque(group.exception_records)

        while queue:
            item = queue.popleft()
            sub = item.get("sub_group")
            if sub and isinstance(sub, list):
                for child in sub:
                    if isinstance(child, dict):
                        queue.append(child)
            else:
                result.append(item)
        return result

    def obstruction_summary(self, group: ExceptionGroupRecord) -> dict[str, Any]:
        """Return a detailed obstruction summary for *group*.

        Args:
            group: The ExceptionGroupRecord to summarise.

        Returns:
            Dict with ``group_id``, ``task_id``, ``total_exceptions``,
            ``total_obstructions``, ``by_type``, ``is_resolved``,
            ``resolution_strategy``, ``severity_estimate``.
        """
        by_type = self.split_by_type(group)
        type_count = len(by_type)
        total = len(group.exception_records)
        severity_estimate = min(1.0, type_count * 0.3 + total * 0.07)

        return {
            "group_id": group.group_id,
            "task_id": group.task_id,
            "total_exceptions": total,
            "total_obstructions": len(group.obstruction_keys),
            "by_type": {k: len(v) for k, v in by_type.items()},
            "is_resolved": group.is_resolved,
            "resolution_strategy": group.resolution_strategy,
            "severity_estimate": severity_estimate,
        }

    def resolve_group(self, group: ExceptionGroupRecord, strategy: str = "auto") -> bool:
        """Resolve an ExceptionGroupRecord with the given strategy.

        If *strategy* is ``"auto"``, the resolution strategy is computed via
        :meth:`process_group`; otherwise the provided strategy is used.

        Args:
            group: The group to resolve.
            strategy: Strategy name (default ``"auto"``).

        Returns:
            Always returns ``True`` once the group is resolved.
        """
        if strategy == "auto":
            pipeline_result = self.process_group(group)
            effective_strategy = pipeline_result["resolution_strategy"]
        else:
            effective_strategy = strategy
            self._groups[group.group_id] = group

        group.resolve(effective_strategy)
        self._resolution_log.append(
            {
                "event": "resolve_group",
                "group_id": group.group_id,
                "strategy": effective_strategy,
                "resolved_at": time.time(),
            }
        )
        return True

    def export_groups(self) -> dict[str, dict[str, Any]]:
        """Export all registered groups as serialisable dicts.

        Returns:
            Dict of ``{group_id: to_dict()}``.
        """
        return {gid: grp.to_dict() for gid, grp in self._groups.items()}


# ══════════════════════════════════════════════════════
# CLASS: BoundaryEnforcer (algorithms variant)
# ══════════════════════════════════════════════════════


class BoundaryEnforcer:
    """Enforces process boundary conditions in the semantic site (algorithms variant).

    Maintains the set of active ProcessBoundary instances, evaluates crossing
    requests, constructs cover morphisms, and records all violations with full
    provenance.

    Theory reference: Ch24 §3 — THEOREM_PROCESS_BOUNDARY_COVER.
    """

    def __init__(self) -> None:
        self._boundaries: dict[str, ProcessBoundary] = {}
        self._morphisms: dict[str, dict[str, Any]] = {}
        self._violations: list[dict[str, Any]] = []
        self._crossing_log: list[dict[str, Any]] = []

    # ── main enforcement ───────────────────────────────

    def enforce_boundary(
        self,
        section: TaskLocalSection,
        source_process_id: str,
        target_process_id: str,
    ) -> dict[str, Any]:
        """Enforce process boundary rules for a section attempting to cross.

        Looks up the boundary for the given process pair, verifies whether the
        section is permitted to cross, builds a cover morphism if allowed, or
        records a violation if denied.

        Args:
            section: The TaskLocalSection requesting the crossing.
            source_process_id: Origin process.
            target_process_id: Destination process.

        Returns:
            Dict with ``allowed`` (bool), ``section_id``, ``source``,
            ``target``, and either ``morphism`` (when allowed) or
            ``violation_key`` (when denied).
        """
        allowed = self.check_crossing(section.section_id, source_process_id, target_process_id)

        if allowed:
            morphism = self.build_cover_morphism(
                source_process_id, target_process_id, section.section_id
            )
            self._crossing_log.append(
                {
                    "event": "crossing_allowed",
                    "section_id": section.section_id,
                    "source": source_process_id,
                    "target": target_process_id,
                    "morphism_id": morphism["morphism_id"],
                    "timestamp": time.time(),
                }
            )
            return {
                "allowed": True,
                "section_id": section.section_id,
                "source": source_process_id,
                "target": target_process_id,
                "morphism": morphism,
            }

        violation_key = self.record_violation(
            section.section_id, source_process_id, target_process_id
        )
        return {
            "allowed": False,
            "section_id": section.section_id,
            "source": source_process_id,
            "target": target_process_id,
            "violation_key": violation_key,
        }

    # ── crossing check ─────────────────────────────────

    def check_crossing(
        self, section_id: str, source_id: str, target_id: str
    ) -> bool:
        """Return True if any registered boundary permits this crossing.

        Args:
            section_id: The section attempting to cross.
            source_id: Origin process identifier.
            target_id: Destination process identifier.

        Returns:
            ``True`` if crossing is permitted; ``False`` otherwise.
        """
        for boundary in self._boundaries.values():
            if (
                boundary.source_process_id == source_id
                and boundary.target_process_id == target_id
                and boundary.is_active
                and boundary.permits_crossing(section_id)
            ):
                return True
        return False

    # ── cover morphism ─────────────────────────────────

    def build_cover_morphism(
        self, source_id: str, target_id: str, section_id: str
    ) -> dict[str, Any]:
        """Construct and register a cover morphism for an allowed crossing.

        Args:
            source_id: Source process identifier.
            target_id: Target process identifier.
            section_id: Section being transported.

        Returns:
            Morphism dict with full provenance.
        """
        morphism_id = uuid.uuid4().hex
        morphism: dict[str, Any] = {
            "morphism_id": morphism_id,
            "source_process_id": source_id,
            "target_process_id": target_id,
            "section_id": section_id,
            "created_at": time.time(),
            "provenance": {
                "enforcer": "BoundaryEnforcer",
                "source": source_id,
                "target": target_id,
                "theory_ref": "Ch24_§3_THEOREM_PROCESS_BOUNDARY_COVER",
            },
        }
        self._morphisms[morphism_id] = morphism
        return morphism

    # ── IPC validation ─────────────────────────────────

    def validate_ipc(
        self,
        channel_kind: str,
        source_section: TaskLocalSection,
        target_section: TaskLocalSection,
    ) -> bool:
        """Check that two sections are compatible for IPC transport.

        Compatibility is determined by:
        * Overlapping ``support_keys`` between source and target.
        * Matching ``schema_version`` in provenance (when both are set).

        Args:
            channel_kind: IPC mechanism label (informational only).
            source_section: Sending section.
            target_section: Receiving section.

        Returns:
            ``True`` if compatible; ``False`` otherwise.
        """
        src_keys = set(source_section.support_keys)
        tgt_keys = set(target_section.support_keys)
        if not src_keys & tgt_keys:
            return False

        src_ver = _provenance_get(source_section, "schema_version")
        tgt_ver = _provenance_get(target_section, "schema_version")
        if src_ver and tgt_ver and src_ver != tgt_ver:
            return False

        return True

    # ── violation recording ────────────────────────────

    def record_violation(
        self, section_id: str, source_id: str, target_id: str
    ) -> str:
        """Record a boundary violation and return the violation key.

        Args:
            section_id: The section that attempted the illegal crossing.
            source_id: Origin process.
            target_id: Destination process.

        Returns:
            The generated violation key string.
        """
        violation_key = f"violation:{source_id}:{target_id}:{section_id}"
        self._violations.append(
            {
                "violation_key": violation_key,
                "section_id": section_id,
                "source_id": source_id,
                "target_id": target_id,
                "recorded_at": time.time(),
            }
        )
        return violation_key

    # ── registry management ────────────────────────────

    def update_boundary(self, boundary_id: str, new_boundary: ProcessBoundary) -> bool:
        """Replace an existing boundary in the registry.

        Args:
            boundary_id: The boundary to replace.
            new_boundary: Replacement ProcessBoundary.

        Returns:
            ``True`` if the original boundary existed; ``False`` if this is a
            new insertion.
        """
        existed = boundary_id in self._boundaries
        self._boundaries[boundary_id] = new_boundary
        return existed

    def export_boundaries(self) -> dict[str, dict[str, Any]]:
        """Return all registered boundaries as serialisable dicts.

        Returns:
            Dict of ``{boundary_id: to_dict()}``.
        """
        return {bid: b.to_dict() for bid, b in self._boundaries.items()}

    def boundary_report(self) -> dict[str, Any]:
        """Return a summary report of boundaries, morphisms, and violations.

        Returns:
            Dict with ``total_boundaries``, ``active_boundaries``,
            ``total_morphisms``, ``total_violations``, ``recent_violations``.
        """
        active = sum(1 for b in self._boundaries.values() if b.is_active)
        return {
            "total_boundaries": len(self._boundaries),
            "active_boundaries": active,
            "total_morphisms": len(self._morphisms),
            "total_violations": len(self._violations),
            "total_crossings": len(self._crossing_log),
            "recent_violations": self._violations[-10:],
        }


# ══════════════════════════════════════════════════════
# Module-level algorithm functions
# ══════════════════════════════════════════════════════


def compute_obstruction_score(records: list[CancellationRecord]) -> float:
    """Compute a weighted obstruction severity score across cancellation records.

    Each CancellationRecord contributes a weight based on its cancellation
    reason:

    * ``TIMEOUT``           → 0.3
    * ``USER_REQUESTED``    → 0.1
    * ``PARENT_CANCELLED``  → 0.2
    * ``RESOURCE_EXHAUSTED``→ 0.5
    * ``OBSTRUCTION``       → 0.4
    * ``POLICY_VIOLATION``  → 0.6

    Unknown reasons contribute a default weight of 0.3.

    Args:
        records: List of CancellationRecord instances to score.

    Returns:
        Average weighted score across all records, as a float in
        ``[0.0, 1.0]``.  Returns 0.0 for an empty list.
    """
    if not records:
        return 0.0

    total_weight = 0.0
    for record in records:
        reason_str = str(record.reason).upper() if record.reason is not None else ""
        # Normalise away class path prefix if present (e.g. "CancellationReason.TIMEOUT")
        if "." in reason_str:
            reason_str = reason_str.split(".")[-1]
        weight = _REASON_WEIGHTS.get(reason_str, 0.3)
        total_weight += weight

    return total_weight / len(records)


def rank_sections_by_stability(
    sections: list[TaskLocalSection],
) -> list[TaskLocalSection]:
    """Sort sections from most stable to least stable.

    Stability order:
    1. COMPLETED   (most stable — task finished successfully)
    2. ACTIVE      (healthy and running)
    3. SUSPENDED   (paused but recoverable)
    4. FAILED      (terminal error but known)
    5. CANCELLED   (cancelled by external request)
    6. BOUNDARY_CROSSED (illegal state — requires remediation)

    Within each stability group, sections are sorted by ``created_at``
    ascending (older sections first), providing a deterministic ordering.

    Args:
        sections: List of TaskLocalSection instances to rank.

    Returns:
        New list sorted from most stable to least stable.
    """
    unknown_order = len(_STATUS_ORDER)  # Anything not in the map goes last

    def sort_key(section: TaskLocalSection) -> tuple[int, float]:
        status_upper = _section_status(section)
        order = _STATUS_ORDER.get(status_upper, unknown_order)
        return (order, section.created_at)

    return sorted(sections, key=sort_key)


# ══════════════════════════════════════════════════════
# Module exports
# ══════════════════════════════════════════════════════
__all__ = [
    "ConcurrencyAnalyzer",
    "CancellationHandler",
    "ExceptionGroupProcessor",
    "BoundaryEnforcer",
    "compute_obstruction_score",
    "rank_sections_by_stability",
]

# copilot: shared-core marker for future LLM orchestration.
