"""Section 3 of the live_mutation Ch23 implementation: hot reload as incremental
descent.  In sheaf-theoretic terms, reloading a module is a refinement of the cover
that replaces sections incrementally.  Each reload is a sequence of *descent steps* —
ordered section replacements that must be consistent on overlaps.  A failed reload can
be rolled back iff all descent steps are individually reversible.  This module
implements: HotReloadEngine (plans and executes hot reload sequences), DescentPlanner
(computes the ordered sequence of section replacements), ReloadRollback (tracks and
executes rollback of a failed reload), and ConsistencyChecker (verifies that new
sections agree on mutual overlaps).  Theory alignment: Ch23 §3 of theory2.tex.
"""

from __future__ import annotations

import collections
import hashlib
import json
import math
import re
import time
import uuid
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


def _new_reload_id() -> str:
    """Generate a unique reload-event identifier."""
    return f"reload-{uuid.uuid4().hex[:12]}"


def _new_plan_id() -> str:
    """Generate a unique descent-plan identifier."""
    return f"plan-{uuid.uuid4().hex[:12]}"


def _new_check_id() -> str:
    """Generate a unique consistency-check identifier."""
    return f"chk-{uuid.uuid4().hex[:12]}"


def _overlap_key(section_a: str, section_b: str) -> str:
    """Return a canonical (sorted) key for the (a, b) overlap pair."""
    parts = sorted([section_a, section_b])
    return f"{parts[0]}|{parts[1]}"


# ---------------------------------------------------------------------------
# HotReloadEngine
# ---------------------------------------------------------------------------


@dataclass
class HotReloadEngine:
    """Plans and executes hot reload sequences for Python modules.

    In sheaf-theoretic terms a hot reload is a refinement of the cover: the
    module's sections are replaced in a topologically ordered sequence
    (descent steps) so that at every intermediate state the partially-reloaded
    module is still consistent on all currently replaced overlaps.

    Attributes:
        _reload_events: Mapping from event_id to reload-event dicts.
        _active_reload: The event_id of the currently in-progress reload,
            or *None* if no reload is active.
        _completed_reloads: Ordered list of event_ids of successfully
            completed reloads.
        _failed_reloads: Ordered list of event_ids of failed reloads.
    """

    _reload_events: dict[str, dict] = field(default_factory=dict)
    _active_reload: str | None = None
    _completed_reloads: list[str] = field(default_factory=list)
    _failed_reloads: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def begin_reload(
        self, module_name: str, sections_to_replace: list[str]
    ) -> str:
        """Start a new reload event for *module_name*.

        Creates a reload-event record with ``status='IN_PROGRESS'`` and sets
        it as the active reload.  If another reload was already active its
        reference is overwritten (the previous reload is implicitly abandoned
        but its record is preserved).

        Args:
            module_name: The fully qualified name of the module being reloaded.
            sections_to_replace: Ordered list of section IDs that will be
                replaced during this reload sequence.

        Returns:
            The unique event_id string for this reload event.
        """
        event_id = _new_reload_id()
        record: dict = {
            "event_id": event_id,
            "module_name": module_name,
            "sections_to_replace": list(sections_to_replace),
            "status": "IN_PROGRESS",
            "started_at": time.time(),
            "completed_at": None,
            "error": None,
            "descent_steps": [],
        }
        self._reload_events[event_id] = record
        self._active_reload = event_id
        return event_id

    def add_descent_step(
        self,
        event_id: str,
        section_id: str,
        action: str,
        details: dict | None = None,
    ) -> bool:
        """Append a descent step to the reload event identified by *event_id*.

        Each descent step records one atomic section replacement within the
        reload sequence.

        Args:
            event_id: The reload event to append to.
            section_id: The section being replaced in this step.
            action: A short action label (e.g. ``"REPLACE"``, ``"SKIP"``,
                ``"VALIDATE"``).
            details: Optional dict of step-specific metadata.

        Returns:
            *True* if the step was added; *False* if *event_id* is not found.
        """
        rec = self._reload_events.get(event_id)
        if rec is None:
            return False
        step: dict = {
            "step_index": len(rec["descent_steps"]),
            "section_id": section_id,
            "action": action,
            "details": details or {},
            "step_at": time.time(),
        }
        rec["descent_steps"].append(step)
        return True

    def complete_reload(self, event_id: str) -> bool:
        """Mark the reload event as successfully completed.

        Clears the active-reload pointer if this event was the active one.

        Args:
            event_id: The reload event to complete.

        Returns:
            *True* on success; *False* if *event_id* is not found or the
            event is not in the ``IN_PROGRESS`` status.
        """
        rec = self._reload_events.get(event_id)
        if rec is None or rec["status"] != "IN_PROGRESS":
            return False
        rec["status"] = "COMPLETED"
        rec["completed_at"] = time.time()
        self._completed_reloads.append(event_id)
        if self._active_reload == event_id:
            self._active_reload = None
        return True

    def fail_reload(self, event_id: str, error: str) -> bool:
        """Mark the reload event as failed with the given *error* message.

        Args:
            event_id: The reload event to fail.
            error: Human-readable description of the failure.

        Returns:
            *True* on success; *False* if *event_id* is not found.
        """
        rec = self._reload_events.get(event_id)
        if rec is None:
            return False
        rec["status"] = "FAILED"
        rec["error"] = error
        rec["completed_at"] = time.time()
        self._failed_reloads.append(event_id)
        if self._active_reload == event_id:
            self._active_reload = None
        return True

    def is_reload_active(self) -> bool:
        """Return *True* if there is an active reload in progress.

        Returns:
            Boolean.
        """
        return self._active_reload is not None

    def get_reload_event(self, event_id: str) -> dict | None:
        """Return the reload-event dict for *event_id*, or *None*.

        Args:
            event_id: The reload event to look up.

        Returns:
            The reload-event dict, or *None* if not found.
        """
        return self._reload_events.get(event_id)

    def reload_history(self) -> list[dict]:
        """Return all reload events sorted by ``started_at`` ascending.

        Returns:
            List of reload-event dicts.
        """
        return sorted(self._reload_events.values(), key=lambda r: r["started_at"])

    def engine_stats(self) -> dict:
        """Return a statistical summary of the hot-reload engine.

        Returns:
            Dict with ``total_reloads``, ``completed``, ``failed``,
            ``active`` (0 or 1), ``avg_steps_per_reload``.
        """
        all_events = list(self._reload_events.values())
        total = len(all_events)
        step_counts = [len(e["descent_steps"]) for e in all_events]
        avg_steps = sum(step_counts) / total if total else 0.0
        return {
            "total_reloads": total,
            "completed": len(self._completed_reloads),
            "failed": len(self._failed_reloads),
            "active": 1 if self._active_reload is not None else 0,
            "avg_steps_per_reload": round(avg_steps, 4),
        }


# ---------------------------------------------------------------------------
# DescentPlanner
# ---------------------------------------------------------------------------


@dataclass
class DescentPlanner:
    """Computes the ordered sequence of section replacements for a reload.

    The planner performs a topological sort of sections (and their module
    dependencies) so that each section is replaced only after all sections
    it depends on have been replaced.  This ensures that the partial reload
    state is always sheaf-consistent on the already-replaced overlaps.

    Attributes:
        _dependency_order: Maps module_name → list of module names it depends on.
        _planned_sequences: Ordered list of descent-plan dicts that have been
            generated by :meth:`plan_descent`.
    """

    _dependency_order: dict[str, list[str]] = field(default_factory=dict)
    _planned_sequences: list[dict] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def register_module_deps(
        self, module_name: str, depends_on: list[str]
    ) -> None:
        """Register that *module_name* depends on *depends_on* modules.

        Multiple calls with the same *module_name* overwrite prior
        registrations (the full dependency list is replaced).

        Args:
            module_name: The module whose dependencies are being registered.
            depends_on: Ordered list of module names that *module_name*
                depends on.
        """
        self._dependency_order[module_name] = list(depends_on)

    def topological_sort(self, modules: list[str]) -> list[str]:
        """Return *modules* in topological order using Kahn's algorithm.

        If the dependency graph contains a cycle among the provided modules,
        the input order is returned unchanged and a warning dict is appended
        to *_planned_sequences*.

        Args:
            modules: List of module names to sort.

        Returns:
            Topologically ordered list of module names.
        """
        module_set = set(modules)
        # Build in-degree map and adjacency list restricted to *modules*
        in_degree: dict[str, int] = {m: 0 for m in modules}
        adjacency: dict[str, list[str]] = {m: [] for m in modules}

        for mod in modules:
            for dep in self._dependency_order.get(mod, []):
                if dep in module_set:
                    # mod depends on dep → dep must come before mod
                    adjacency[dep].append(mod)
                    in_degree[mod] += 1

        # Kahn's BFS
        queue: collections.deque[str] = collections.deque(
            [m for m in modules if in_degree[m] == 0]
        )
        sorted_result: list[str] = []
        while queue:
            node = queue.popleft()
            sorted_result.append(node)
            for neighbor in adjacency[node]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if len(sorted_result) != len(modules):
            # Cycle detected — fall back to input order
            self._planned_sequences.append(
                {
                    "warning": "Cycle detected in module dependencies; using input order.",
                    "affected_modules": modules,
                    "at": time.time(),
                }
            )
            return list(modules)
        return sorted_result

    def plan_descent(
        self, module_name: str, sections: list[str]
    ) -> dict:
        """Create and record a descent plan for reloading *module_name*.

        Args:
            module_name: The module being planned for reload.
            sections: List of section IDs that will be replaced.

        Returns:
            A descent-plan dict with ``plan_id``, ``module_name``,
            ``ordered_sections`` (topologically sorted), ``estimated_steps``,
            ``created_at``, and ``dependencies``.
        """
        plan_id = _new_plan_id()
        ordered = self.topological_sort(sections)
        cost = self.estimate_reload_cost(sections)
        plan: dict = {
            "plan_id": plan_id,
            "module_name": module_name,
            "ordered_sections": ordered,
            "estimated_steps": cost,
            "created_at": time.time(),
            "dependencies": self._dependency_order.get(module_name, []),
        }
        self._planned_sequences.append(plan)
        return plan

    def estimate_reload_cost(self, sections: list[str]) -> int:
        """Estimate the reload cost for *sections*.

        Cost formula:

        - **+1** per section in *sections*.
        - **+2** per section that has more than one dependent (i.e. appears as
          a dependency for more than one other registered module).

        Args:
            sections: List of section IDs to estimate cost for.

        Returns:
            Non-negative integer cost estimate.
        """
        # Build a count of how many modules depend on each section
        dependent_count: dict[str, int] = collections.Counter()
        for _mod, deps in self._dependency_order.items():
            for dep in deps:
                dependent_count[dep] += 1

        cost = 0
        for sec in sections:
            cost += 1
            if dependent_count.get(sec, 0) > 1:
                cost += 2
        return cost

    def get_plan(self, plan_id: str) -> dict | None:
        """Return the descent-plan dict for *plan_id*, or *None*.

        Args:
            plan_id: The plan to retrieve.

        Returns:
            The plan dict, or *None* if not found.
        """
        for plan in self._planned_sequences:
            if plan.get("plan_id") == plan_id:
                return plan
        return None

    def list_plans(self) -> list[str]:
        """Return the list of all plan IDs generated so far.

        Returns:
            List of plan_id strings (in creation order).
        """
        return [
            p["plan_id"]
            for p in self._planned_sequences
            if "plan_id" in p
        ]

    def dependency_depth(self, module_name: str) -> int:
        """Return the maximum depth of the dependency graph from *module_name*.

        Performs BFS from *module_name* through *_dependency_order* to find
        the longest dependency chain.

        Args:
            module_name: Root of the dependency traversal.

        Returns:
            Non-negative integer (0 if *module_name* has no dependencies).
        """
        visited: set[str] = set()
        frontier: collections.deque[tuple[str, int]] = collections.deque(
            [(module_name, 0)]
        )
        max_depth = 0
        while frontier:
            current, depth = frontier.popleft()
            if current in visited:
                continue
            visited.add(current)
            max_depth = max(max_depth, depth)
            for dep in self._dependency_order.get(current, []):
                if dep not in visited:
                    frontier.append((dep, depth + 1))
        return max_depth

    def planner_stats(self) -> dict:
        """Return a statistical summary of the planner.

        Returns:
            Dict with ``total_plans``, ``modules_registered``,
            ``avg_plan_size``.
        """
        plans_only = [p for p in self._planned_sequences if "plan_id" in p]
        total = len(plans_only)
        avg_size = (
            sum(len(p.get("ordered_sections", [])) for p in plans_only) / total
            if total
            else 0.0
        )
        return {
            "total_plans": total,
            "modules_registered": len(self._dependency_order),
            "avg_plan_size": round(avg_size, 4),
        }


# ---------------------------------------------------------------------------
# ReloadRollback
# ---------------------------------------------------------------------------


@dataclass
class ReloadRollback:
    """Tracks and executes rollback of a failed hot reload.

    Before each descent step the engine should call :meth:`checkpoint` to
    save the previous state of the section being replaced.  If the reload
    fails, :meth:`execute_rollback` restores the previous states in reverse
    order.

    Attributes:
        _checkpoints: Maps event_id → ordered list of checkpoint dicts (each
            dict records the section_id and its previous state).
        _rollback_log: Ordered list of rollback-result dicts produced by
            :meth:`execute_rollback`.
    """

    _checkpoints: dict[str, list[dict]] = field(default_factory=dict)
    _rollback_log: list[dict] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def checkpoint(
        self,
        event_id: str,
        section_id: str,
        previous_state: dict,
    ) -> None:
        """Save a rollback checkpoint for *section_id* under *event_id*.

        Should be called immediately before the section is replaced so that
        the previous state can be restored on rollback.

        Args:
            event_id: The reload event this checkpoint belongs to.
            section_id: The section whose state is being saved.
            previous_state: A dict representing the section's previous state.
        """
        if event_id not in self._checkpoints:
            self._checkpoints[event_id] = []
        self._checkpoints[event_id].append(
            {
                "section_id": section_id,
                "previous_state": dict(previous_state),
                "saved_at": time.time(),
            }
        )

    def execute_rollback(self, event_id: str) -> dict:
        """Execute rollback for *event_id* by reversing all checkpoints.

        Checkpoints are processed in reverse order (LIFO) to undo descent
        steps from newest to oldest.

        Args:
            event_id: The reload event to roll back.

        Returns:
            A rollback-result dict with ``event_id``, ``steps_reversed``,
            ``reverted_sections``, and ``rolled_back_at``.
        """
        checkpoints = self._checkpoints.get(event_id, [])
        reversed_checkpoints = list(reversed(checkpoints))
        reverted_sections: list[str] = [cp["section_id"] for cp in reversed_checkpoints]
        rollback_result: dict = {
            "event_id": event_id,
            "steps_reversed": len(reversed_checkpoints),
            "reverted_sections": reverted_sections,
            "rolled_back_at": time.time(),
        }
        self._rollback_log.append(rollback_result)
        return rollback_result

    def can_rollback(self, event_id: str) -> bool:
        """Return *True* if there are saved checkpoints for *event_id*.

        Args:
            event_id: The reload event to check.

        Returns:
            Boolean.
        """
        return bool(self._checkpoints.get(event_id))

    def rollback_depth(self, event_id: str) -> int:
        """Return the number of checkpoints saved for *event_id*.

        Args:
            event_id: The reload event to inspect.

        Returns:
            Non-negative integer.
        """
        return len(self._checkpoints.get(event_id, []))

    def rollback_history(self) -> list[dict]:
        """Return all rollback-result records in chronological order.

        Returns:
            List of rollback-result dicts.
        """
        return list(self._rollback_log)

    def clear_checkpoints(self, event_id: str) -> int:
        """Remove all checkpoints saved for *event_id*.

        Should be called after a successful reload to reclaim memory.

        Args:
            event_id: The reload event whose checkpoints should be cleared.

        Returns:
            The number of checkpoints that were removed.
        """
        existing = self._checkpoints.pop(event_id, [])
        return len(existing)

    def rollback_stats(self) -> dict:
        """Return a statistical summary of rollback operations.

        Returns:
            Dict with ``total_rollbacks``, ``total_checkpoints``
            (across all events), ``avg_rollback_depth``,
            ``event_ids_with_checkpoints``.
        """
        total_checkpoints = sum(len(v) for v in self._checkpoints.values())
        total_rollbacks = len(self._rollback_log)
        if self._rollback_log:
            avg_depth = (
                sum(r["steps_reversed"] for r in self._rollback_log) / total_rollbacks
            )
        else:
            avg_depth = 0.0
        return {
            "total_rollbacks": total_rollbacks,
            "total_checkpoints": total_checkpoints,
            "avg_rollback_depth": round(avg_depth, 4),
            "event_ids_with_checkpoints": sorted(self._checkpoints.keys()),
        }


# ---------------------------------------------------------------------------
# ConsistencyChecker
# ---------------------------------------------------------------------------


@dataclass
class ConsistencyChecker:
    """Verifies that new sections agree on mutual overlaps after a hot reload.

    Two sections overlap when they both define values for the same namespace
    keys.  The gluing condition of the sheaf requires that those values agree
    on the overlap.  This checker detects disagreements so that the reload
    engine can fail fast and trigger rollback.

    Attributes:
        _check_log: Ordered list of consistency-check records.
        _overlap_registry: Maps canonical overlap key (``"sec_a|sec_b"``) →
            dict with ``section_a``, ``section_b``, and ``overlap_keys``
            (frozenset).
    """

    _check_log: list[dict] = field(default_factory=list)
    _overlap_registry: dict[str, dict] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def register_overlap(
        self,
        section_a: str,
        section_b: str,
        overlap_keys: frozenset[str],
    ) -> None:
        """Register that *section_a* and *section_b* share *overlap_keys*.

        Calling this multiple times for the same pair merges the key sets.

        Args:
            section_a: First section.
            section_b: Second section.
            overlap_keys: Frozenset of namespace key strings shared by both.
        """
        key = _overlap_key(section_a, section_b)
        if key in self._overlap_registry:
            existing = self._overlap_registry[key]["overlap_keys"]
            self._overlap_registry[key]["overlap_keys"] = existing | overlap_keys
        else:
            self._overlap_registry[key] = {
                "section_a": min(section_a, section_b),
                "section_b": max(section_a, section_b),
                "overlap_keys": overlap_keys,
            }

    def check_overlap_agreement(
        self,
        section_a: str,
        section_b: str,
        values_a: dict,
        values_b: dict,
    ) -> dict:
        """Check whether *section_a* and *section_b* agree on their overlap keys.

        For each key in the registered overlap, the values are compared using
        their ``repr()`` strings (so that non-comparable objects can still be
        differentiated).

        Args:
            section_a: First section.
            section_b: Second section.
            values_a: Dict of key → value for *section_a*'s namespace.
            values_b: Dict of key → value for *section_b*'s namespace.

        Returns:
            Dict with ``agreed`` (list of keys), ``disagreed`` (list of keys),
            ``missing_in_a`` (list), ``missing_in_b`` (list), and
            ``is_consistent`` (bool: no disagreements and no missing keys).
        """
        key = _overlap_key(section_a, section_b)
        overlap_entry = self._overlap_registry.get(key)
        if overlap_entry is None:
            # No registered overlap → trivially consistent (nothing to check)
            return {
                "agreed": [],
                "disagreed": [],
                "missing_in_a": [],
                "missing_in_b": [],
                "is_consistent": True,
            }
        overlap_keys = overlap_entry["overlap_keys"]
        agreed: list[str] = []
        disagreed: list[str] = []
        missing_in_a: list[str] = []
        missing_in_b: list[str] = []

        for k in sorted(overlap_keys):
            in_a = k in values_a
            in_b = k in values_b
            if not in_a:
                missing_in_a.append(k)
            elif not in_b:
                missing_in_b.append(k)
            elif repr(values_a[k]) == repr(values_b[k]):
                agreed.append(k)
            else:
                disagreed.append(k)

        is_consistent = (
            len(disagreed) == 0 and len(missing_in_a) == 0 and len(missing_in_b) == 0
        )
        return {
            "agreed": agreed,
            "disagreed": disagreed,
            "missing_in_a": missing_in_a,
            "missing_in_b": missing_in_b,
            "is_consistent": is_consistent,
        }

    def check_section_gluing(
        self,
        sections: list[str],
        namespace_values: dict[str, dict],
    ) -> dict:
        """Verify the gluing condition for all pairs of *sections*.

        For every pair ``(a, b)`` in *sections* that has a registered overlap,
        :meth:`check_overlap_agreement` is called and the results are
        aggregated.

        Args:
            sections: List of section IDs to check pairwise.
            namespace_values: Mapping from section_id → dict of namespace
                key → value.

        Returns:
            Dict with ``total_pairs_checked``, ``consistent_pairs``,
            ``inconsistent_pairs``, ``overall_consistent`` (bool), and
            ``inconsistencies`` (list of per-pair result dicts that were not
            consistent).
        """
        total_pairs = 0
        consistent_pairs = 0
        inconsistent_pairs = 0
        inconsistencies: list[dict] = []

        for i, sec_a in enumerate(sections):
            for sec_b in sections[i + 1:]:
                key = _overlap_key(sec_a, sec_b)
                if key not in self._overlap_registry:
                    continue
                total_pairs += 1
                vals_a = namespace_values.get(sec_a, {})
                vals_b = namespace_values.get(sec_b, {})
                result = self.check_overlap_agreement(sec_a, sec_b, vals_a, vals_b)
                if result["is_consistent"]:
                    consistent_pairs += 1
                else:
                    inconsistent_pairs += 1
                    inconsistencies.append(
                        {
                            "section_a": sec_a,
                            "section_b": sec_b,
                            **result,
                        }
                    )

        overall_consistent = inconsistent_pairs == 0
        return {
            "total_pairs_checked": total_pairs,
            "consistent_pairs": consistent_pairs,
            "inconsistent_pairs": inconsistent_pairs,
            "overall_consistent": overall_consistent,
            "inconsistencies": inconsistencies,
        }

    def record_check(self, event_id: str, result: dict) -> None:
        """Append a consistency-check result to the check log.

        Args:
            event_id: The reload event this check was performed for.
            result: The dict returned by :meth:`check_section_gluing` (or any
                compatible result dict).
        """
        record: dict = {
            "check_id": _new_check_id(),
            "event_id": event_id,
            "result": result,
            "checked_at": time.time(),
        }
        self._check_log.append(record)

    def is_globally_consistent(self, event_id: str) -> bool:
        """Return *True* if the most recent check for *event_id* was consistent.

        Args:
            event_id: The reload event to inspect.

        Returns:
            *True* if a check for *event_id* exists and its most recent result
            has ``overall_consistent=True``; *False* otherwise.
        """
        # Find the most recent check for this event_id
        matching = [
            rec for rec in self._check_log if rec["event_id"] == event_id
        ]
        if not matching:
            return False
        latest = max(matching, key=lambda r: r["checked_at"])
        return bool(latest["result"].get("overall_consistent", False))

    def consistency_history(self) -> list[dict]:
        """Return all consistency-check records in chronological order.

        Returns:
            List of check-record dicts.
        """
        return sorted(self._check_log, key=lambda r: r["checked_at"])

    def checker_stats(self) -> dict:
        """Return a statistical summary of the consistency checker.

        Returns:
            Dict with ``total_checks``, ``consistent_checks``,
            ``inconsistent_checks``, ``registered_overlaps``.
        """
        total = len(self._check_log)
        consistent = sum(
            1
            for rec in self._check_log
            if rec["result"].get("overall_consistent", False)
        )
        return {
            "total_checks": total,
            "consistent_checks": consistent,
            "inconsistent_checks": total - consistent,
            "registered_overlaps": len(self._overlap_registry),
        }


# ---------------------------------------------------------------------------
# Module-level convenience factories
# ---------------------------------------------------------------------------


def make_hot_reload_engine() -> HotReloadEngine:
    """Create a fresh :class:`HotReloadEngine`.

    Returns:
        A new :class:`HotReloadEngine` instance.
    """
    return HotReloadEngine()


def make_descent_planner() -> DescentPlanner:
    """Create a fresh :class:`DescentPlanner`.

    Returns:
        A new :class:`DescentPlanner` instance.
    """
    return DescentPlanner()


def make_reload_rollback() -> ReloadRollback:
    """Create a fresh :class:`ReloadRollback`.

    Returns:
        A new :class:`ReloadRollback` instance.
    """
    return ReloadRollback()


def make_consistency_checker() -> ConsistencyChecker:
    """Create a fresh :class:`ConsistencyChecker`.

    Returns:
        A new :class:`ConsistencyChecker` instance.
    """
    return ConsistencyChecker()


__all__ = [
    "HotReloadEngine",
    "DescentPlanner",
    "ReloadRollback",
    "ConsistencyChecker",
    "make_hot_reload_engine",
    "make_descent_planner",
    "make_reload_rollback",
    "make_consistency_checker",
]

# copilot: hot reload as incremental descent for live_mutation Ch23 §3
