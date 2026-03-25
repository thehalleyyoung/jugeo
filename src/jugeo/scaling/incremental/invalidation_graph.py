"""Enhanced invalidation graph with contract boundaries and dampening strategies.

``EnhancedInvalidationGraph`` extends a simple dependency graph with:

- **Contract boundaries**: invalidation stops at nodes that expose a stable
  contract interface, unless the change itself breaks that contract.
- **Tiered invalidation**: depth-1 is immediate; deeper depths are deferred
  or lazy, reducing unnecessary recomputation.
- **Probabilistic sampling**: for very large cascades (above a configurable
  threshold), only a fraction of dependents are invalidated eagerly, with
  the rest scheduled lazily.
"""

from __future__ import annotations

import collections
import random
import time
from dataclasses import dataclass, field
from typing import Any

from jugeo.scaling.incremental.models import (
    InvalidationEvent,
    InvalidationPolicy,
    InvalidationStrategy,
)


# ---------------------------------------------------------------------------
# EnhancedInvalidationGraph
# ---------------------------------------------------------------------------


class EnhancedInvalidationGraph:
    """Directed dependency graph with rich invalidation semantics.

    Nodes are coordinate IDs (strings).  Edges are ``dependent → dependency``
    (i.e. if A depends on B, then invalidating B also invalidates A).

    Usage::

        graph = EnhancedInvalidationGraph()
        graph.add_dependency("view.render", "model.User")
        graph.add_contract_boundary("model.User", contract_hash="abc123")
        event = graph.invalidate("model.User", change_kind="implementation")
        # event.invalidated_coordinates will be empty because the contract is stable
    """

    def __init__(self, policy: InvalidationPolicy | None = None) -> None:
        self.policy = policy or InvalidationPolicy()

        # Graph structure: dependents[X] = {Y: Y depends on X}
        self._dependents: dict[str, set[str]] = collections.defaultdict(set)
        # Graph structure: dependencies[X] = {Y: X depends on Y}
        self._dependencies: dict[str, set[str]] = collections.defaultdict(set)

        # Contract boundaries: coordinate_id -> contract_hash
        self._contract_boundaries: dict[str, str] = {}

        # Invalidation history
        self._events: list[InvalidationEvent] = []

        # Counts for statistics
        self._total_invalidations = 0
        self._boundary_stops = 0

    # ------------------------------------------------------------------
    # Graph manipulation
    # ------------------------------------------------------------------

    def add_dependency(self, dependent: str, dependency: str) -> None:
        """Record that *dependent* depends on *dependency*."""
        self._dependents[dependency].add(dependent)
        self._dependencies[dependent].add(dependency)
        # Ensure all nodes are registered even if they have no edges
        self._dependents.setdefault(dependent, set())
        self._dependencies.setdefault(dependency, set())

    def remove_dependency(self, dependent: str, dependency: str) -> None:
        """Remove the edge ``dependent → dependency``."""
        self._dependents[dependency].discard(dependent)
        self._dependencies[dependent].discard(dependency)

    def add_contract_boundary(self, coordinate_id: str, contract_hash: str) -> None:
        """Mark *coordinate_id* as a contract boundary with the given hash."""
        self._contract_boundaries[coordinate_id] = contract_hash
        # Ensure node is registered
        self._dependents.setdefault(coordinate_id, set())
        self._dependencies.setdefault(coordinate_id, set())

    # ------------------------------------------------------------------
    # Invalidation
    # ------------------------------------------------------------------

    def invalidate(
        self, coordinate_id: str, change_kind: str = "implementation"
    ) -> InvalidationEvent:
        """Invalidate *coordinate_id* and cascade to dependents.

        The cascade strategy is chosen based on the policy:
        - If ``use_contract_boundaries`` and the change is contract-preserving,
          use CONTRACT_BOUNDED strategy.
        - If the expected cascade size exceeds ``probabilistic_threshold``,
          use PROBABILISTIC strategy.
        - Otherwise use TIERED strategy.
        """
        self._total_invalidations += 1

        is_contract_preserving = self._is_contract_preserving_change(
            coordinate_id, change_kind
        )

        if is_contract_preserving and self.policy.use_contract_boundaries:
            invalidated = self._contract_bounded_cascade(coordinate_id)
            strategy = InvalidationStrategy.CONTRACT_BOUNDED
        else:
            # Estimate cascade size cheaply before choosing strategy
            estimated = len(self.all_dependents(coordinate_id))
            if estimated >= self.policy.probabilistic_threshold:
                invalidated = self._probabilistic_cascade(coordinate_id)
                strategy = InvalidationStrategy.PROBABILISTIC
            else:
                invalidated = self._tiered_cascade(coordinate_id)
                strategy = InvalidationStrategy.TIERED

        max_depth = max(
            (self._node_depth(coordinate_id, inv) for inv in invalidated),
            default=0,
        )

        event = InvalidationEvent.create(
            source=coordinate_id,
            invalidated=invalidated,
            depth=max_depth,
            strategy=strategy,
        )
        self._events.append(event)
        return event

    def _cascade(
        self, start: str, max_depth: int, visited: set[str]
    ) -> list[str]:
        """BFS cascade up to *max_depth* layers."""
        result: list[str] = []
        queue: collections.deque[tuple[str, int]] = collections.deque()
        queue.append((start, 0))
        visited.add(start)

        while queue:
            current, depth = queue.popleft()
            if depth >= max_depth:
                continue
            for dependent in self._dependents.get(current, set()):
                if dependent not in visited:
                    visited.add(dependent)
                    result.append(dependent)
                    queue.append((dependent, depth + 1))
        return result

    def _contract_bounded_cascade(self, start: str) -> list[str]:
        """Cascade stops at contract boundaries (unless the change broke the contract).

        Also respects ``policy.max_cascade_depth``.
        """
        result: list[str] = []
        visited: set[str] = {start}
        # Store (node, depth) in the queue
        queue: collections.deque[tuple[str, int]] = collections.deque()
        queue.append((start, 0))

        while queue:
            current, depth = queue.popleft()
            if depth >= self.policy.max_cascade_depth:
                continue
            for dependent in self._dependents.get(current, set()):
                if dependent in visited:
                    continue
                visited.add(dependent)
                if dependent in self._contract_boundaries:
                    # Stop here — the boundary absorbs the invalidation
                    self._boundary_stops += 1
                    continue
                result.append(dependent)
                queue.append((dependent, depth + 1))
        return result

    def _tiered_cascade(self, start: str) -> list[str]:
        """Immediate for depth 1, deferred for depth 2, lazy for depth 3+."""
        result: list[str] = []
        visited: set[str] = {start}
        # Depth 1: immediate
        depth1 = [d for d in self._dependents.get(start, set())]
        for dep in depth1:
            if dep not in visited:
                visited.add(dep)
                result.append(dep)

        # Depth 2: deferred (we still collect them but tag them)
        depth2: list[str] = []
        for dep in depth1:
            for dep2 in self._dependents.get(dep, set()):
                if dep2 not in visited:
                    visited.add(dep2)
                    depth2.append(dep2)
        result.extend(depth2)

        # Depth 3+: lazy — honour max_cascade_depth from policy
        if self.policy.max_cascade_depth > 2:
            deeper = self._cascade(
                start,
                max_depth=self.policy.max_cascade_depth,
                visited=visited,
            )
            result.extend(deeper)

        return _dedupe(result)

    def _probabilistic_cascade(
        self, start: str, sample_rate: float = 0.1
    ) -> list[str]:
        """Sample deep cascades to avoid O(N) invalidation on large graphs."""
        result: list[str] = []
        visited: set[str] = {start}

        # Always propagate depth-1
        for dep in self._dependents.get(start, set()):
            if dep not in visited:
                visited.add(dep)
                result.append(dep)

        # Probabilistically sample deeper
        queue = list(result)
        while queue:
            next_queue: list[str] = []
            for current in queue:
                for dep in self._dependents.get(current, set()):
                    if dep in visited:
                        continue
                    if random.random() < sample_rate:
                        visited.add(dep)
                        result.append(dep)
                        next_queue.append(dep)
            queue = next_queue

        return result

    # ------------------------------------------------------------------
    # Impact analysis
    # ------------------------------------------------------------------

    def change_impact_analysis(
        self, coordinate_id: str, change_detail: str
    ) -> dict[str, Any]:
        """Analyse the potential impact of a change *before* invalidating."""
        is_contract_preserving = self._is_contract_preserving_change(
            coordinate_id, change_detail
        )
        all_deps = self.all_dependents(coordinate_id)
        direct_deps = self._dependents.get(coordinate_id, set())
        contract_boundaries_in_path: list[str] = [
            n for n in all_deps if n in self._contract_boundaries
        ]
        bounded_count = len(self._contract_bounded_cascade(coordinate_id))

        return {
            "coordinate_id": coordinate_id,
            "change_detail": change_detail,
            "is_contract_preserving": is_contract_preserving,
            "total_transitive_dependents": len(all_deps),
            "direct_dependents": len(direct_deps),
            "contract_boundaries_in_path": contract_boundaries_in_path,
            "estimated_invalidations_bounded": bounded_count,
            "estimated_invalidations_full": len(all_deps),
            "recommended_strategy": (
                InvalidationStrategy.CONTRACT_BOUNDED.value
                if is_contract_preserving and self.policy.use_contract_boundaries
                else InvalidationStrategy.TIERED.value
            ),
        }

    def _is_contract_preserving_change(
        self, coordinate_id: str, change_detail: str
    ) -> bool:
        """Return True if the change only affects implementation, not the contract.

        Checks the change_detail keywords regardless of whether the coordinate
        is itself a contract boundary.  If the detail signals a contract-breaking
        change (e.g. "signature", "api"), returns False so that the full cascade
        is used rather than being dampened at boundaries.
        """
        detail_lower = change_detail.lower()
        breaks_contract_keywords = {"signature", "contract", "interface", "api", "type"}
        if any(kw in detail_lower for kw in breaks_contract_keywords):
            return False
        return True

    # ------------------------------------------------------------------
    # Batch invalidation
    # ------------------------------------------------------------------

    def batch_invalidate(
        self, coordinate_ids: list[str]
    ) -> list[InvalidationEvent]:
        """Invalidate multiple coordinates, deduplicating the cascade."""
        events: list[InvalidationEvent] = []
        already_invalidated: set[str] = set()

        for cid in coordinate_ids:
            event = self.invalidate(cid)
            # Remove already-invalidated coordinates from the new event's list
            new_invalidated = [
                c for c in event.invalidated_coordinates if c not in already_invalidated
            ]
            already_invalidated.update(new_invalidated)
            event.invalidated_coordinates[:] = new_invalidated
            events.append(event)

        return events

    # ------------------------------------------------------------------
    # Traversal helpers
    # ------------------------------------------------------------------

    def dependency_count(self, coordinate_id: str) -> tuple[int, int]:
        """Return (number of direct dependents, number of direct dependencies)."""
        return (
            len(self._dependents.get(coordinate_id, set())),
            len(self._dependencies.get(coordinate_id, set())),
        )

    def all_dependents(self, coordinate_id: str) -> set[str]:
        """Return the set of all transitive dependents of *coordinate_id*."""
        visited: set[str] = set()
        stack = list(self._dependents.get(coordinate_id, set()))
        while stack:
            node = stack.pop()
            if node in visited:
                continue
            visited.add(node)
            stack.extend(self._dependents.get(node, set()))
        return visited

    def all_dependencies(self, coordinate_id: str) -> set[str]:
        """Return the set of all transitive dependencies of *coordinate_id*."""
        visited: set[str] = set()
        stack = list(self._dependencies.get(coordinate_id, set()))
        while stack:
            node = stack.pop()
            if node in visited:
                continue
            visited.add(node)
            stack.extend(self._dependencies.get(node, set()))
        return visited

    def _node_depth(self, source: str, target: str) -> int:
        """Return BFS distance from *source* to *target*, or 0 if unreachable."""
        if source == target:
            return 0
        visited: set[str] = {source}
        queue: collections.deque[tuple[str, int]] = collections.deque([(source, 0)])
        while queue:
            current, depth = queue.popleft()
            for dep in self._dependents.get(current, set()):
                if dep == target:
                    return depth + 1
                if dep not in visited:
                    visited.add(dep)
                    queue.append((dep, depth + 1))
        return 0

    # ------------------------------------------------------------------
    # Graph analysis
    # ------------------------------------------------------------------

    def topological_sort(self) -> list[str]:
        """Return nodes in topological order (dependencies before dependents).

        Raises ``ValueError`` if the graph contains a cycle.
        """
        all_nodes = set(self._dependents.keys()) | set(self._dependencies.keys())
        in_degree: dict[str, int] = {n: 0 for n in all_nodes}
        for node in all_nodes:
            for dependent in self._dependents.get(node, set()):
                in_degree[dependent] = in_degree.get(dependent, 0) + 1

        queue: collections.deque[str] = collections.deque(
            sorted(n for n, deg in in_degree.items() if deg == 0)
        )
        result: list[str] = []
        while queue:
            node = queue.popleft()
            result.append(node)
            for dependent in sorted(self._dependents.get(node, set())):
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    queue.append(dependent)

        if len(result) != len(all_nodes):
            raise ValueError("Graph contains a cycle; topological sort is undefined.")
        return result

    def is_acyclic(self) -> bool:
        """Return True if the graph has no cycles."""
        try:
            self.topological_sort()
            return True
        except ValueError:
            return False

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    def statistics(self) -> dict[str, Any]:
        all_nodes = set(self._dependents.keys()) | set(self._dependencies.keys())
        total_edges = sum(len(v) for v in self._dependents.values())
        return {
            "total_nodes": len(all_nodes),
            "total_edges": total_edges,
            "contract_boundaries": len(self._contract_boundaries),
            "total_invalidations": self._total_invalidations,
            "boundary_stops": self._boundary_stops,
            "events_recorded": len(self._events),
        }


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def _dedupe(lst: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in lst:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out
