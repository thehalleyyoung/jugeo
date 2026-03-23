"""Core algorithms for the live_mutation package.

This module provides higher-level algorithmic components that orchestrate the
lower-level operations of exec injection, monkey patching, and hot reloading.
In sheaf-theoretic terms, these algorithms manage the consistency and lifecycle
of dynamic sections across the full session.

Theory alignment: Ch23 of theory2.tex.
"""

from __future__ import annotations

import ast
import hashlib
import json
import math
import re
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

try:
    from jugeo.python_runtime.live_mutation.models import (
        MutationKind,
        InvalidationScope,
        ReloadStatus,
        DynamicSection,
        ExecContext,
        EvalResult,
        MonkeyPatchRecord,
        HotReloadEvent,
    )
except ImportError:
    MutationKind = InvalidationScope = ReloadStatus = None  # type: ignore[assignment]
    DynamicSection = ExecContext = EvalResult = MonkeyPatchRecord = HotReloadEvent = None  # type: ignore[assignment]


@dataclass
class LiveMutationTracker:
    """Tracks all live mutations in a session — exec injections, eval queries,
    monkey patches, and hot reloads.

    Provides a unified view of the mutation state, history, and rollback
    capability. Acts as the top-level coordinator for the live_mutation session.
    Each mutation record is stamped with a timestamp and unique record ID so the
    full session timeline can be replayed or audited at any time.
    """

    _exec_records: list[dict] = field(default_factory=list)
    _eval_records: list[dict] = field(default_factory=list)
    _patch_records: list[dict] = field(default_factory=list)
    _reload_records: list[dict] = field(default_factory=list)
    _session_id: str = field(default_factory=lambda: f"session-{uuid.uuid4().hex[:8]}")
    _created_at: float = field(default_factory=time.time)

    def track_exec(
        self,
        section_id: str,
        source_code: str,
        defined_names: list[str],
        context_id: str,
        trust_level: str = "PROPOSAL",
    ) -> dict:
        """Create and append an exec record.

        Builds a record dict containing a unique record_id, the mutation type
        'exec', the section_id, a SHA-256 hash of the source_code for integrity
        tracking, the list of defined_names, the owning context_id, the
        trust_level, and a tracked_at timestamp. The record is appended to the
        internal exec log and returned to the caller.

        Args:
            section_id: Unique identifier of the dynamic section being injected.
            source_code: Raw Python source code for the section.
            defined_names: List of top-level names the code introduces.
            context_id: Identifier of the ExecContext in which this runs.
            trust_level: Trust tier, defaults to 'PROPOSAL'.

        Returns:
            The newly created exec record dict.
        """
        source_hash = hashlib.sha256(source_code.encode()).hexdigest()
        record: dict = {
            "record_id": f"exec-{uuid.uuid4().hex[:8]}",
            "type": "exec",
            "section_id": section_id,
            "source_code_hash": source_hash,
            "defined_names": list(defined_names),
            "context_id": context_id,
            "trust_level": trust_level,
            "tracked_at": time.time(),
            "rolled_back": False,
        }
        self._exec_records.append(record)
        return record

    def track_eval(
        self,
        result_id: str,
        expression: str,
        context_id: str,
        trust_level: str = "PROPOSAL",
        error: str | None = None,
    ) -> dict:
        """Create and append an eval record.

        Records an eval expression query, including the result_id, expression
        text, owning context_id, trust_level, any error string, and a
        tracked_at timestamp.

        Args:
            result_id: Unique identifier for the eval result.
            expression: Python expression that was evaluated.
            context_id: Identifier of the context in which eval ran.
            trust_level: Trust tier for the resulting value.
            error: Error message if eval raised an exception, else None.

        Returns:
            The newly created eval record dict.
        """
        record: dict = {
            "record_id": f"eval-{uuid.uuid4().hex[:8]}",
            "type": "eval",
            "result_id": result_id,
            "expression": expression,
            "expression_hash": hashlib.sha256(expression.encode()).hexdigest(),
            "context_id": context_id,
            "trust_level": trust_level,
            "error": error,
            "tracked_at": time.time(),
            "rolled_back": False,
        }
        self._eval_records.append(record)
        return record

    def track_patch(
        self,
        patch_id: str,
        module_name: str,
        attribute: str,
        scope: str = "MODULE",
    ) -> dict:
        """Create and append a monkey-patch record.

        Records the application of a monkey patch including the patch_id,
        target module_name, attribute being replaced, the invalidation scope,
        and a tracked_at timestamp.

        Args:
            patch_id: Unique identifier for the patch operation.
            module_name: Fully-qualified module whose attribute is patched.
            attribute: Name of the attribute being replaced.
            scope: Invalidation scope string (e.g. 'MODULE', 'GLOBAL').

        Returns:
            The newly created patch record dict.
        """
        record: dict = {
            "record_id": f"patch-{uuid.uuid4().hex[:8]}",
            "type": "patch",
            "patch_id": patch_id,
            "module_name": module_name,
            "attribute": attribute,
            "scope": scope,
            "tracked_at": time.time(),
            "rolled_back": False,
        }
        self._patch_records.append(record)
        return record

    def track_reload(
        self,
        event_id: str,
        module_name: str,
        sections_replaced: list[str],
    ) -> dict:
        """Create and append a hot-reload record.

        Records a hot reload event including the event_id, the module being
        reloaded, the list of section IDs that were replaced, and a
        tracked_at timestamp.

        Args:
            event_id: Unique identifier for the reload event.
            module_name: Fully-qualified module being reloaded.
            sections_replaced: Section IDs replaced during the reload.

        Returns:
            The newly created reload record dict.
        """
        record: dict = {
            "record_id": f"reload-{uuid.uuid4().hex[:8]}",
            "type": "reload",
            "event_id": event_id,
            "module_name": module_name,
            "sections_replaced": list(sections_replaced),
            "sections_count": len(sections_replaced),
            "tracked_at": time.time(),
            "rolled_back": False,
            "in_progress": True,
        }
        self._reload_records.append(record)
        return record

    def active_mutations(self) -> dict:
        """Return a summary of currently active (non-rolled-back) mutations.

        Counts exec records, eval records, patch records not marked as
        rolled_back, reload records still marked in_progress, and the current
        session age in seconds.

        Returns:
            Dict with active_execs, active_evals, active_patches,
            active_reloads, and session_age_seconds.
        """
        active_execs = sum(1 for r in self._exec_records if not r.get("rolled_back"))
        active_evals = sum(1 for r in self._eval_records if not r.get("rolled_back"))
        active_patches = sum(1 for r in self._patch_records if not r.get("rolled_back"))
        active_reloads = sum(
            1 for r in self._reload_records if r.get("in_progress") and not r.get("rolled_back")
        )
        return {
            "active_execs": active_execs,
            "active_evals": active_evals,
            "active_patches": active_patches,
            "active_reloads": active_reloads,
            "session_age_seconds": round(time.time() - self._created_at, 3),
        }

    def mutation_history(self, kind: str | None = None) -> list[dict]:
        """Return all mutation records, optionally filtered by mutation type.

        Aggregates exec, eval, patch, and reload records into a single timeline
        and sorts them by their tracked_at timestamp. If *kind* is supplied,
        only records whose ``type`` field matches are returned.

        Args:
            kind: Optional type filter — one of 'exec', 'eval', 'patch',
                  'reload'. If None, all records are returned.

        Returns:
            List of record dicts sorted ascending by tracked_at.
        """
        all_records = (
            self._exec_records
            + self._eval_records
            + self._patch_records
            + self._reload_records
        )
        if kind is not None:
            all_records = [r for r in all_records if r.get("type") == kind]
        return sorted(all_records, key=lambda r: r.get("tracked_at", 0.0))

    def rollback_last(self, kind: str | None = None) -> dict | None:
        """Find and roll back the most recent mutation record.

        Scans the mutation history for the most recent non-rolled-back record
        of the given *kind* (or any kind when *kind* is None). Marks it as
        rolled_back=True in-place and returns it. Returns None if no eligible
        record is found.

        Args:
            kind: Optional type filter for which mutation kind to roll back.

        Returns:
            The rolled-back record dict, or None if no record was found.
        """
        candidates = self.mutation_history(kind=kind)
        for record in reversed(candidates):
            if not record.get("rolled_back"):
                record["rolled_back"] = True
                record["rolled_back_at"] = time.time()
                return record
        return None

    def export_state(self) -> dict:
        """Return the full session state as a serialisable dict.

        Includes the session_id, created_at timestamp, current age, counts for
        each mutation kind, and the complete list of all mutation records in
        chronological order.

        Returns:
            Dict describing the complete session state.
        """
        return {
            "session_id": self._session_id,
            "created_at": self._created_at,
            "age_seconds": round(time.time() - self._created_at, 3),
            "exec_count": len(self._exec_records),
            "eval_count": len(self._eval_records),
            "patch_count": len(self._patch_records),
            "reload_count": len(self._reload_records),
            "all_records": self.mutation_history(),
        }

    def session_summary(self) -> str:
        """Return a multi-line human-readable summary of the session.

        Includes the session ID, age, total mutation count broken down by kind,
        and a note about how many records have been rolled back.

        Returns:
            A formatted multi-line string.
        """
        state = self.export_state()
        rolled_back = sum(
            1 for r in state["all_records"] if r.get("rolled_back")
        )
        total = (
            state["exec_count"]
            + state["eval_count"]
            + state["patch_count"]
            + state["reload_count"]
        )
        lines = [
            f"Session: {self._session_id}",
            f"  Age:     {state['age_seconds']:.1f}s",
            f"  Total mutations: {total}",
            f"    exec:   {state['exec_count']}",
            f"    eval:   {state['eval_count']}",
            f"    patch:  {state['patch_count']}",
            f"    reload: {state['reload_count']}",
            f"  Rolled back: {rolled_back}",
        ]
        return "\n".join(lines)


@dataclass
class InvalidationEngine:
    """Computes and fires invalidation cascades triggered by monkey patches and
    section replacements.

    Implements BFS/DFS cascade computation, circular dependency detection, and
    invalidation reporting. In sheaf-theoretic terms, this engine enforces the
    consistency conditions that must hold when a section is replaced: if σ at
    coordinate e depends on attribute A and A is patched, then σ is no longer
    guaranteed consistent and must be re-evaluated or quarantined.

    Theory alignment: Ch23 §2 of theory2.tex.
    """

    _dependency_graph: dict[str, set[str]] = field(
        default_factory=lambda: defaultdict(set)
    )  # attribute -> set of section_ids
    _reverse_graph: dict[str, set[str]] = field(
        default_factory=lambda: defaultdict(set)
    )  # section_id -> set of attributes
    _invalidation_events: list[dict] = field(default_factory=list)
    _cascade_limit: int = 50

    def register_dependency(self, section_id: str, attribute: str) -> None:
        """Register that a section depends on a module attribute.

        Updates both the forward dependency graph (attribute -> section_ids)
        and the reverse graph (section_id -> attributes) to keep both indexes
        consistent.

        Args:
            section_id: The section that uses the attribute.
            attribute: Fully-qualified attribute path, e.g. 'math.sqrt'.
        """
        self._dependency_graph[attribute].add(section_id)
        self._reverse_graph[section_id].add(attribute)

    def compute_cascade(self, patched_attribute: str) -> list[str]:
        """BFS from patched_attribute to collect all transitively affected sections.

        Starting from *patched_attribute*, performs a breadth-first traversal
        through the dependency graph. Each attribute node expands to the
        section_ids that depend on it. The cascade is bounded by
        *_cascade_limit* total section visits to prevent runaway propagation.

        Args:
            patched_attribute: The attribute that was monkey-patched.

        Returns:
            Sorted list of unique section_ids that are transitively affected.
        """
        visited_sections: set[str] = set()
        visited_attrs: set[str] = set()
        queue: deque[str] = deque([patched_attribute])
        visited_attrs.add(patched_attribute)

        while queue and len(visited_sections) < self._cascade_limit:
            attr = queue.popleft()
            for section_id in self._dependency_graph.get(attr, set()):
                if section_id in visited_sections:
                    continue
                visited_sections.add(section_id)
                # Each section may depend on further attributes → expand
                for dep_attr in self._reverse_graph.get(section_id, set()):
                    if dep_attr not in visited_attrs:
                        visited_attrs.add(dep_attr)
                        queue.append(dep_attr)

        return sorted(visited_sections)

    def fire_invalidation(
        self,
        patched_attribute: str,
        patch_id: str,
        fired_by: str = "system",
    ) -> dict:
        """Fire an invalidation cascade for a patched attribute.

        Computes the full cascade, then records the invalidation event with all
        metadata needed for auditing and downstream processing.

        Args:
            patched_attribute: The attribute whose replacement triggers the cascade.
            patch_id: Identifier of the monkey-patch operation.
            fired_by: Actor that triggered the invalidation (default 'system').

        Returns:
            The invalidation event dict appended to _invalidation_events.
        """
        affected = self.compute_cascade(patched_attribute)
        event: dict = {
            "event_id": f"inv-{uuid.uuid4().hex[:8]}",
            "patch_id": patch_id,
            "patched_attribute": patched_attribute,
            "affected_sections": affected,
            "cascade_size": len(affected),
            "fired_at": time.time(),
            "fired_by": fired_by,
        }
        self._invalidation_events.append(event)
        return event

    def record_invalidation(
        self, section_id: str, patch_id: str, reason: str
    ) -> None:
        """Manually record an out-of-band invalidation for a specific section.

        Used when the caller knows a section is invalidated by some external
        condition not captured by the standard dependency graph traversal.

        Args:
            section_id: The section being invalidated.
            patch_id: The patch operation responsible.
            reason: Human-readable explanation of why the section is invalidated.
        """
        self._invalidation_events.append(
            {
                "event_id": f"inv-manual-{uuid.uuid4().hex[:8]}",
                "patch_id": patch_id,
                "section_id": section_id,
                "manual": True,
                "reason": reason,
                "fired_at": time.time(),
            }
        )

    def cascade_depth(self, patched_attribute: str) -> int:
        """Compute the BFS level depth of the cascade from patched_attribute.

        Counts how many BFS levels are reachable from the patched_attribute,
        where each level corresponds to one hop through the dependency graph
        (attribute → sections → attributes → sections …).

        Args:
            patched_attribute: Starting attribute for the depth computation.

        Returns:
            Integer number of BFS levels, 0 if nothing is affected.
        """
        visited_attrs: set[str] = {patched_attribute}
        visited_sections: set[str] = set()
        frontier: set[str] = {patched_attribute}
        depth = 0

        while frontier:
            next_sections: set[str] = set()
            for attr in frontier:
                for sec in self._dependency_graph.get(attr, set()):
                    if sec not in visited_sections:
                        visited_sections.add(sec)
                        next_sections.add(sec)
            if not next_sections:
                break
            depth += 1
            next_attrs: set[str] = set()
            for sec in next_sections:
                for attr in self._reverse_graph.get(sec, set()):
                    if attr not in visited_attrs:
                        visited_attrs.add(attr)
                        next_attrs.add(attr)
            frontier = next_attrs

        return depth

    def affected_modules(self, patched_attribute: str) -> set[str]:
        """Return unique module prefixes of all affected sections.

        After computing the full cascade, extracts the module prefix from each
        section_id by splitting on the first '/' or '-' separator.

        Args:
            patched_attribute: The attribute to cascade from.

        Returns:
            Set of unique module-prefix strings.
        """
        affected = self.compute_cascade(patched_attribute)
        modules: set[str] = set()
        for sid in affected:
            # Extract prefix before first '/' or '-'
            prefix = re.split(r"[/\-]", sid, maxsplit=1)[0]
            if prefix:
                modules.add(prefix)
        return modules

    def check_circular(self, section_id: str, new_attribute: str) -> bool:
        """Detect whether adding section_id → new_attribute creates a dependency cycle.

        Uses DFS through the _dependency_graph treating attribute→section edges
        as a directed graph. If *section_id* is reachable from *new_attribute*
        via the existing graph, adding the proposed edge would create a cycle.

        Args:
            section_id: The section proposing to depend on new_attribute.
            new_attribute: The attribute the section wants to depend on.

        Returns:
            True if a cycle would be introduced, False otherwise.
        """
        # Check if section_id is reachable from new_attribute through the graph
        visited: set[str] = set()
        stack: list[str] = [new_attribute]

        while stack:
            node = stack.pop()
            if node in visited:
                continue
            visited.add(node)
            # Expand attribute → sections
            for sec in self._dependency_graph.get(node, set()):
                if sec == section_id:
                    return True
                # Expand section → attributes
                for attr in self._reverse_graph.get(sec, set()):
                    if attr not in visited:
                        stack.append(attr)
        return False

    def invalidation_report(self) -> dict:
        """Generate an aggregate invalidation statistics report.

        Computes totals, average and maximum cascade sizes, and identifies the
        attribute that has triggered the most invalidation events.

        Returns:
            Dict with total_events, total_sections_invalidated, avg_cascade_size,
            max_cascade_size, and most_invalidating_attribute.
        """
        if not self._invalidation_events:
            return {
                "total_events": 0,
                "total_sections_invalidated": 0,
                "avg_cascade_size": 0.0,
                "max_cascade_size": 0,
                "most_invalidating_attribute": None,
            }

        cascade_sizes = [
            e.get("cascade_size", 0) for e in self._invalidation_events
        ]
        attr_counts: dict[str, int] = defaultdict(int)
        for e in self._invalidation_events:
            attr = e.get("patched_attribute")
            if attr:
                attr_counts[attr] += e.get("cascade_size", 0)

        most_invalidating = max(attr_counts, key=lambda a: attr_counts[a]) if attr_counts else None

        return {
            "total_events": len(self._invalidation_events),
            "total_sections_invalidated": sum(cascade_sizes),
            "avg_cascade_size": round(sum(cascade_sizes) / len(cascade_sizes), 2),
            "max_cascade_size": max(cascade_sizes),
            "most_invalidating_attribute": most_invalidating,
        }

    def export_triggers(self) -> list[dict]:
        """Return all recorded invalidation events.

        Returns:
            List of all invalidation event dicts in insertion order.
        """
        return list(self._invalidation_events)


@dataclass
class HotReloadPlanner:
    """Plans and validates hot reload sequences for modules.

    A reload plan specifies the topological order in which sections should be
    replaced during a hot reload, estimates the cost, and validates that all
    dependencies are satisfied. The planner maintains a log of executed and
    aborted plans for post-mortem analysis.

    Theory alignment: Ch23 §3 (incremental descent planning) of theory2.tex.
    """

    _module_deps: dict[str, list[str]] = field(default_factory=dict)
    _reload_plans: dict[str, dict] = field(default_factory=dict)
    _execution_log: list[dict] = field(default_factory=list)

    def plan_reload(
        self,
        module_name: str,
        sections: list[str],
        force_order: list[str] | None = None,
    ) -> dict:
        """Create a reload plan for the given module and sections.

        Computes the topological ordering of sections respecting declared
        dependencies (or uses *force_order* if provided), estimates the reload
        cost, and records the plan.

        Args:
            module_name: Fully-qualified name of the module to reload.
            sections: List of section IDs to replace.
            force_order: Optional explicit ordering, bypassing topological sort.

        Returns:
            The reload plan dict stored in _reload_plans.
        """
        plan_id = f"plan-{uuid.uuid4().hex[:8]}"
        section_deps = self._module_deps.get(module_name, {})
        if isinstance(section_deps, list):
            section_deps = {}
        ordered = force_order if force_order else self.topological_sort(sections, section_deps)
        cost = self.estimate_cost(sections)
        plan: dict = {
            "plan_id": plan_id,
            "module_name": module_name,
            "ordered_sections": ordered,
            "estimated_cost": cost,
            "created_at": time.time(),
            "is_valid": bool(sections),
            "status": "PLANNED",
            "section_count": len(sections),
        }
        self._reload_plans[plan_id] = plan
        return plan

    def execute_plan(self, plan_id: str) -> dict:
        """Simulate execution of a reload plan.

        Steps through each section in the plan's ordered_sections, recording
        a simulated execution step for each. Marks the plan status as
        EXECUTED on completion.

        Args:
            plan_id: The plan to execute.

        Returns:
            Execution result dict with plan_id, executed_steps, started_at,
            completed_at, and success flag.
        """
        plan = self._reload_plans.get(plan_id)
        if not plan:
            return {
                "plan_id": plan_id,
                "success": False,
                "error": "Plan not found",
                "executed_steps": [],
            }

        started_at = time.time()
        steps = []
        for i, section_id in enumerate(plan.get("ordered_sections", [])):
            step = {
                "step_index": i,
                "section_id": section_id,
                "executed_at": time.time(),
                "status": "REPLACED",
            }
            steps.append(step)
            self._execution_log.append({"plan_id": plan_id, **step})

        completed_at = time.time()
        plan["status"] = "EXECUTED"
        plan["executed_at"] = completed_at

        result = {
            "plan_id": plan_id,
            "executed_steps": steps,
            "started_at": started_at,
            "completed_at": completed_at,
            "success": True,
        }
        return result

    def validate_plan(self, plan_id: str) -> list[str]:
        """Validate a reload plan for structural issues.

        Checks for an empty section list, sections whose dependencies fall
        outside the plan, and estimated cost exceeding a sanity threshold.

        Args:
            plan_id: Identifier of the plan to validate.

        Returns:
            List of issue strings; empty list means the plan is valid.
        """
        issues: list[str] = []
        plan = self._reload_plans.get(plan_id)
        if plan is None:
            return [f"Plan '{plan_id}' not found"]

        sections = plan.get("ordered_sections", [])
        if not sections:
            issues.append("Plan has an empty section list")

        for sec in sections:
            if not self.check_dependencies(sec, plan_id):
                issues.append(f"Section '{sec}' has unresolved dependencies outside the plan")

        cost = plan.get("estimated_cost", 0)
        if cost > 100:
            issues.append(f"Estimated cost {cost} exceeds safety threshold of 100")

        return issues

    def topological_sort(
        self,
        sections: list[str],
        section_deps: dict[str, list[str]] | None = None,
    ) -> list[str]:
        """Topological sort of sections using Kahn's algorithm.

        Produces a valid linear ordering of *sections* that respects the
        dependency constraints in *section_deps*. Sections with no declared
        dependencies are ordered by name for determinism.

        Args:
            sections: List of section IDs to sort.
            section_deps: Dict mapping section_id -> list of dependency section_ids.
                          Defaults to no dependencies if None.

        Returns:
            Topologically sorted list of section IDs.
        """
        if not sections:
            return []
        deps = section_deps or {}
        section_set = set(sections)

        in_degree: dict[str, int] = {s: 0 for s in sections}
        adjacency: dict[str, list[str]] = {s: [] for s in sections}

        for sec in sections:
            for dep in deps.get(sec, []):
                if dep in section_set:
                    in_degree[sec] += 1
                    adjacency[dep].append(sec)

        queue: deque[str] = deque(
            sorted(s for s in sections if in_degree[s] == 0)
        )
        result: list[str] = []

        while queue:
            node = queue.popleft()
            result.append(node)
            for neighbour in sorted(adjacency[node]):
                in_degree[neighbour] -= 1
                if in_degree[neighbour] == 0:
                    queue.append(neighbour)

        # If cycle detected, append remaining nodes in stable order
        if len(result) < len(sections):
            remaining = sorted(set(sections) - set(result))
            result.extend(remaining)

        return result

    def check_dependencies(self, section_id: str, plan_id: str) -> bool:
        """Check that all dependencies of section_id are included in the plan.

        Looks up the module's dependency map for section_id and verifies that
        each dependency appears in the plan's ordered_sections list.

        Args:
            section_id: Section whose dependencies should be checked.
            plan_id: Plan whose ordered_sections provides the inclusion set.

        Returns:
            True if all dependencies are satisfied within the plan.
        """
        plan = self._reload_plans.get(plan_id)
        if not plan:
            return False

        plan_sections = set(plan.get("ordered_sections", []))
        module_name = plan.get("module_name", "")
        deps_map = self._module_deps.get(module_name, {})
        if not isinstance(deps_map, dict):
            return True

        for dep in deps_map.get(section_id, []):
            if dep not in plan_sections:
                return False
        return True

    def reload_report(self, plan_id: str) -> str:
        """Return a multi-line textual report for a reload plan.

        Includes plan_id, module name, number of sections, estimated cost,
        execution steps recorded, and any validation issues.

        Args:
            plan_id: Identifier of the plan to report on.

        Returns:
            A formatted multi-line string.
        """
        plan = self._reload_plans.get(plan_id)
        if not plan:
            return f"Plan '{plan_id}' not found"

        issues = self.validate_plan(plan_id)
        steps = [e for e in self._execution_log if e.get("plan_id") == plan_id]
        lines = [
            f"Reload Plan: {plan_id}",
            f"  Module:           {plan.get('module_name')}",
            f"  Sections:         {plan.get('section_count', 0)}",
            f"  Ordered sections: {plan.get('ordered_sections')}",
            f"  Estimated cost:   {plan.get('estimated_cost')}",
            f"  Status:           {plan.get('status')}",
            f"  Executed steps:   {len(steps)}",
            f"  Validation issues: {issues if issues else 'None'}",
        ]
        return "\n".join(lines)

    def estimate_cost(self, sections: list[str]) -> int:
        """Estimate the cost of reloading a list of sections.

        The base cost is 1 per section. An additional penalty of 2 is added
        for each section that has registered dependency entries in any module's
        dependency map.

        Args:
            sections: List of section IDs to estimate cost for.

        Returns:
            Integer cost estimate.
        """
        cost = len(sections)
        all_deps: set[str] = set()
        for module_dep_map in self._module_deps.values():
            if isinstance(module_dep_map, dict):
                for sec, dep_list in module_dep_map.items():
                    all_deps.update(dep_list)
        for sec in sections:
            if sec in all_deps:
                cost += 2
        return cost

    def abort_reload(self, plan_id: str, reason: str) -> bool:
        """Abort a reload plan in progress.

        Marks the plan's status as ABORTED and appends an abort event to the
        execution log.

        Args:
            plan_id: Identifier of the plan to abort.
            reason: Human-readable reason for aborting.

        Returns:
            True if the plan was found and marked as aborted, False otherwise.
        """
        plan = self._reload_plans.get(plan_id)
        if not plan:
            return False
        plan["status"] = "ABORTED"
        plan["aborted_at"] = time.time()
        plan["abort_reason"] = reason
        self._execution_log.append(
            {
                "plan_id": plan_id,
                "event": "ABORT",
                "reason": reason,
                "aborted_at": time.time(),
            }
        )
        return True

    def planner_stats(self) -> dict:
        """Return aggregate statistics for the planner.

        Returns:
            Dict with total_plans, executed_plans, aborted_plans, and
            avg_plan_size (mean number of sections per plan).
        """
        plans = list(self._reload_plans.values())
        executed = sum(1 for p in plans if p.get("status") == "EXECUTED")
        aborted = sum(1 for p in plans if p.get("status") == "ABORTED")
        total = len(plans)
        avg_size = (
            sum(p.get("section_count", 0) for p in plans) / total if total else 0.0
        )
        return {
            "total_plans": total,
            "executed_plans": executed,
            "aborted_plans": aborted,
            "avg_plan_size": round(avg_size, 2),
        }


@dataclass
class DynamicSectionValidator:
    """Validates dynamically injected sections for semantic correctness.

    Checks trust level appropriateness, namespace cleanliness, and
    conflict-free symbol introduction. Provides quarantine functionality for
    sections that fail validation so they cannot be executed until manually
    released by a trusted actor.

    Theory alignment: Ch23 §1 of theory2.tex.
    """

    _quarantined: set[str] = field(default_factory=set)
    _released: set[str] = field(default_factory=set)
    _validation_log: list[dict] = field(default_factory=list)
    _symbol_registry: dict[str, str] = field(default_factory=dict)  # symbol -> section_id

    def validate_exec_section(
        self,
        section_id: str,
        source_code: str,
        defined_names: list[str],
        trust_level: str,
    ) -> dict:
        """Run all validation checks on an exec section.

        Performs five checks: syntactic validity (ast.parse), absence of
        dangerous patterns (exec/eval/os.system calls), trust level
        appropriateness (PROPOSAL for new sections), non-empty defined names,
        and absence of symbol conflicts with the existing registry.

        Args:
            section_id: Unique identifier of the section being validated.
            source_code: Raw Python source code.
            defined_names: List of top-level names the code introduces.
            trust_level: Trust tier string.

        Returns:
            Validation result dict with 'passed', 'checks', and 'issues' keys.
        """
        checks: dict[str, bool] = {}
        issues: list[str] = []

        # Syntactic check
        try:
            ast.parse(source_code)
            checks["syntax_ok"] = True
        except SyntaxError as exc:
            checks["syntax_ok"] = False
            issues.append(f"SyntaxError: {exc}")

        # Dangerous pattern check
        dangerous = re.compile(
            r"\b(exec|eval|os\.system|subprocess\.call|subprocess\.run|__import__)\s*\("
        )
        if dangerous.search(source_code):
            checks["no_dangerous_patterns"] = False
            issues.append("Source contains potentially dangerous call patterns")
        else:
            checks["no_dangerous_patterns"] = True

        # Trust appropriateness
        checks["trust_appropriate"] = trust_level == "PROPOSAL"
        if not checks["trust_appropriate"]:
            issues.append(f"Expected trust_level='PROPOSAL', got '{trust_level}'")

        # Non-empty defined names
        checks["names_non_empty"] = len(defined_names) > 0
        if not checks["names_non_empty"]:
            issues.append("defined_names is empty — section introduces no symbols")

        # Symbol conflict check
        conflicts = self.symbol_conflict_check(defined_names, section_id)
        checks["no_conflicts"] = len(conflicts) == 0
        if conflicts:
            issues.append(f"Symbol conflicts with existing registry: {conflicts}")

        passed = all(checks.values())

        result: dict = {
            "validation_id": f"val-{uuid.uuid4().hex[:8]}",
            "section_id": section_id,
            "passed": passed,
            "checks": checks,
            "issues": issues,
            "validated_at": time.time(),
        }
        self._validation_log.append(result)

        # Register symbols if passed
        if passed:
            for name in defined_names:
                self._symbol_registry[name] = section_id

        return result

    def validate_eval_result(
        self,
        result_id: str,
        expression: str,
        trust_level: str,
        error: str | None,
    ) -> dict:
        """Validate an eval result record.

        Checks that the expression is non-empty, trust level is appropriate,
        no error was raised, and the expression is within a reasonable length
        limit.

        Args:
            result_id: Unique identifier for the eval result.
            expression: The Python expression that was evaluated.
            trust_level: Trust tier string.
            error: Error message if eval failed, else None.

        Returns:
            Validation result dict with 'passed', 'checks', and 'issues' keys.
        """
        checks: dict[str, bool] = {}
        issues: list[str] = []

        checks["expression_non_empty"] = bool(expression.strip())
        if not checks["expression_non_empty"]:
            issues.append("Expression is empty")

        checks["trust_appropriate"] = trust_level in ("PROPOSAL", "VERIFIED")
        if not checks["trust_appropriate"]:
            issues.append(f"Unexpected trust_level: '{trust_level}'")

        checks["no_error"] = error is None
        if error is not None:
            issues.append(f"Eval raised an error: {error}")

        checks["expression_length_ok"] = len(expression) < 4096
        if not checks["expression_length_ok"]:
            issues.append(f"Expression length {len(expression)} exceeds 4096 chars")

        passed = all(checks.values())
        result: dict = {
            "validation_id": f"eval-val-{uuid.uuid4().hex[:8]}",
            "result_id": result_id,
            "passed": passed,
            "checks": checks,
            "issues": issues,
            "validated_at": time.time(),
        }
        self._validation_log.append(result)
        return result

    def check_namespace_pollution(
        self,
        section_id: str,
        new_symbols: list[str],
        existing_symbols: set[str],
    ) -> dict:
        """Assess how much namespace pollution a section would introduce.

        Computes the intersection of new_symbols with existing_symbols to
        identify conflicts and expresses the pollution as a ratio.

        Args:
            section_id: Section whose symbols are being evaluated.
            new_symbols: Symbols the section wants to introduce.
            existing_symbols: Symbols already present in the target namespace.

        Returns:
            Dict with new_symbols count, conflicts list, pollution_ratio, and
            is_clean flag.
        """
        new_set = set(new_symbols)
        conflicts = list(new_set & existing_symbols)
        pollution_ratio = len(conflicts) / len(existing_symbols) if existing_symbols else 0.0
        return {
            "section_id": section_id,
            "new_symbols": len(new_symbols),
            "conflicts": sorted(conflicts),
            "pollution_ratio": round(pollution_ratio, 4),
            "is_clean": len(conflicts) == 0,
        }

    def trust_analysis(
        self,
        section_id: str,
        source_code: str,
        corroboration_count: int = 0,
    ) -> dict:
        """Analyse a section's code to recommend a trust tier.

        Counts code lines, detects dangerous patterns, considers corroboration
        count, and returns a recommended trust tier with human-readable
        reasoning.

        Args:
            section_id: Section being analysed.
            source_code: Raw Python source code.
            corroboration_count: Number of independent corroborating sources.

        Returns:
            Analysis dict with code_lines, has_dangerous_patterns,
            corroboration_count, recommended_tier, and reasoning.
        """
        dangerous = re.compile(
            r"\b(exec|eval|os\.system|subprocess\.call|subprocess\.run|__import__)\s*\("
        )
        has_dangerous = bool(dangerous.search(source_code))
        code_lines = len([l for l in source_code.splitlines() if l.strip()])

        if has_dangerous:
            recommended_tier = "QUARANTINE"
            reasoning = (
                "Source code contains dangerous call patterns (exec/eval/os.system). "
                "Automatic trust elevation is blocked. Manual review required."
            )
        elif corroboration_count >= 3:
            recommended_tier = "VERIFIED"
            reasoning = (
                f"Section has {corroboration_count} independent corroborating sources, "
                "which satisfies the minimum threshold for VERIFIED tier."
            )
        elif corroboration_count >= 1:
            recommended_tier = "CORROBORATED"
            reasoning = (
                f"Section has {corroboration_count} corroborating source(s). "
                "Eligible for CORROBORATED tier but not yet VERIFIED."
            )
        else:
            recommended_tier = "PROPOSAL"
            reasoning = (
                "Section has no external corroboration. Bounded at PROPOSAL tier "
                "until independently corroborated."
            )

        return {
            "section_id": section_id,
            "code_lines": code_lines,
            "has_dangerous_patterns": has_dangerous,
            "corroboration_count": corroboration_count,
            "recommended_tier": recommended_tier,
            "reasoning": reasoning,
        }

    def symbol_conflict_check(
        self, new_symbols: list[str], owner_section: str
    ) -> list[str]:
        """Return symbols in new_symbols already owned by a different section.

        Checks the internal symbol registry and identifies any symbols whose
        current owner differs from owner_section.

        Args:
            new_symbols: Proposed new symbol names.
            owner_section: Section claiming ownership of these symbols.

        Returns:
            List of conflicting symbol names.
        """
        conflicts: list[str] = []
        for sym in new_symbols:
            existing_owner = self._symbol_registry.get(sym)
            if existing_owner is not None and existing_owner != owner_section:
                conflicts.append(sym)
        return conflicts

    def validation_report(self) -> str:
        """Return a multi-line string summarising all recorded validations.

        Includes total count, pass rate, quarantine count, and the most
        frequently occurring validation issues.

        Returns:
            Formatted multi-line report string.
        """
        total = len(self._validation_log)
        if total == 0:
            return "No validations recorded."

        passed_count = sum(1 for r in self._validation_log if r.get("passed"))
        pass_rate = round(100.0 * passed_count / total, 1)
        quarantined_count = len(self._quarantined)

        issue_counter: dict[str, int] = defaultdict(int)
        for record in self._validation_log:
            for issue in record.get("issues", []):
                # Normalise to the first 60 chars for bucketing
                key = issue[:60]
                issue_counter[key] += 1

        top_issues = sorted(issue_counter.items(), key=lambda x: -x[1])[:5]

        lines = [
            f"Validation Report",
            f"  Total validations: {total}",
            f"  Passed:            {passed_count} ({pass_rate}%)",
            f"  Failed:            {total - passed_count}",
            f"  Quarantined:       {quarantined_count}",
            f"  Symbol registry:   {len(self._symbol_registry)} symbols",
            "  Top issues:",
        ]
        for issue, count in top_issues:
            lines.append(f"    [{count:>3}x] {issue}")
        if not top_issues:
            lines.append("    (none)")
        return "\n".join(lines)

    def quarantine_section(self, section_id: str, reason: str) -> None:
        """Move a section into quarantine, preventing execution.

        Adds section_id to the quarantined set, removes it from released if
        present, and logs the quarantine event with timestamp and reason.

        Args:
            section_id: The section to quarantine.
            reason: Human-readable reason for quarantining.
        """
        self._quarantined.add(section_id)
        self._released.discard(section_id)
        self._validation_log.append(
            {
                "validation_id": f"quarantine-{uuid.uuid4().hex[:8]}",
                "section_id": section_id,
                "event": "QUARANTINE",
                "reason": reason,
                "quarantined_at": time.time(),
            }
        )

    def release_section(self, section_id: str) -> bool:
        """Release a quarantined section for execution.

        Moves section_id from _quarantined to _released. Logs the release
        event.

        Args:
            section_id: The section to release from quarantine.

        Returns:
            True if section was quarantined and has now been released,
            False if it was not in quarantine.
        """
        if section_id not in self._quarantined:
            return False
        self._quarantined.discard(section_id)
        self._released.add(section_id)
        self._validation_log.append(
            {
                "validation_id": f"release-{uuid.uuid4().hex[:8]}",
                "section_id": section_id,
                "event": "RELEASE",
                "released_at": time.time(),
            }
        )
        return True

    def validator_stats(self) -> dict:
        """Return aggregate validator statistics.

        Returns:
            Dict with total_validations, passed, failed, quarantined count,
            released count, and total registered symbol count.
        """
        total = len(self._validation_log)
        passed = sum(
            1 for r in self._validation_log if r.get("passed") and "event" not in r
        )
        failed = sum(
            1
            for r in self._validation_log
            if "passed" in r and not r.get("passed") and "event" not in r
        )
        return {
            "total_validations": total,
            "passed": passed,
            "failed": failed,
            "quarantined": len(self._quarantined),
            "released": len(self._released),
            "symbol_count": len(self._symbol_registry),
        }


__all__ = [
    "LiveMutationTracker",
    "InvalidationEngine",
    "HotReloadPlanner",
    "DynamicSectionValidator",
]

# copilot: core algorithms for live_mutation Ch23
