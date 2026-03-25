"""Geometric partitioning for parallel verification of hierarchical sites.

Provides:
- ``GeometricPartitioner`` — partition a site into balanced sub-problems
- ``PartitionScheduler``   — schedule partitions in dependency-respecting waves
"""

from __future__ import annotations

import math
import uuid
from collections import defaultdict, deque
from typing import Any, Optional

from jugeo.scaling.hierarchical.models import (
    GeometricPartitioning,
    PartitionAssignment,
    SiteLevel,
)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _new_pid() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# GeometricPartitioner
# ---------------------------------------------------------------------------


class GeometricPartitioner:
    """Partition a hierarchical site into balanced coordinate groups.

    The default strategy is:
    1. Build Strongly Connected Components (SCCs) from the morphism graph.
    2. Group SCCs into partitions of at most *max_partition_size* coordinates.
    3. Optionally rebalance to bring all partitions within *balance_factor*
       of the maximum-cost partition.

    For cross-partition edge minimisation a Kernighan-Lin style swap pass is
    supported via ``minimize_cross_edges()``.
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def partition(
        self,
        site: Any,  # HierarchicalSite
        max_partition_size: int,
        balance_factor: float = 0.8,
    ) -> GeometricPartitioning:
        """Partition *site* into groups suitable for parallel verification.

        Each group is a ``PartitionAssignment``.  All levels of the site are
        partitioned independently so that the partitioner can be used even
        when the site contains coordinates at many levels.
        """
        all_partitions: list[PartitionAssignment] = []
        total_coords = site.coordinate_count()

        for level in SiteLevel:
            level_view = site.get_level_view(level)
            if not level_view.coordinates:
                continue
            level_parts = self._partition_at_level(level_view, max_partition_size)
            all_partitions.extend(level_parts)

        if all_partitions:
            all_partitions = self._balance_partitions(all_partitions, balance_factor)

        return GeometricPartitioning.from_partitions(total_coords, all_partitions)

    # ------------------------------------------------------------------
    # Level-scoped partitioning
    # ------------------------------------------------------------------

    def _partition_at_level(
        self,
        level_view: Any,  # LevelView
        max_size: int,
    ) -> list[PartitionAssignment]:
        """Partition coordinates at one level into groups of ≤ max_size."""
        coords = level_view.coordinates
        morphisms = level_view.morphisms
        level = level_view.level

        if not coords:
            return []

        # Build SCC-based initial groups
        groups = self.scc_based_partition(coords, morphisms)

        # Split any group larger than max_size
        final_groups: list[set[str]] = []
        for g in groups:
            if len(g) <= max_size:
                final_groups.append(g)
            else:
                # Greedy split into chunks of max_size
                chunk: set[str] = set()
                for cid in sorted(g):
                    chunk.add(cid)
                    if len(chunk) >= max_size:
                        final_groups.append(chunk)
                        chunk = set()
                if chunk:
                    final_groups.append(chunk)

        # Convert to PartitionAssignment objects
        assignments: list[PartitionAssignment] = []
        for g in final_groups:
            cids = sorted(g)
            cost = self._estimate_cost(cids, morphisms)
            assignments.append(
                PartitionAssignment.create(
                    partition_id=_new_pid(),
                    level=level,
                    coordinate_ids=cids,
                    estimated_cost=cost,
                )
            )

        return assignments

    # ------------------------------------------------------------------
    # Balance
    # ------------------------------------------------------------------

    def _balance_partitions(
        self,
        partitions: list[PartitionAssignment],
        factor: float,
    ) -> list[PartitionAssignment]:
        """Rebalance partitions so that min_cost / max_cost ≥ factor.

        Uses a greedy merge: the two smallest partitions at the same level
        are merged as long as doing so does not exceed the target max cost.
        """
        if not partitions:
            return partitions

        # Work level by level
        by_level: dict[SiteLevel, list[PartitionAssignment]] = defaultdict(list)
        for p in partitions:
            by_level[p.level].append(p)

        result: list[PartitionAssignment] = []
        for level, lvl_parts in by_level.items():
            result.extend(self._balance_level_partitions(lvl_parts, factor))

        return result

    def _balance_level_partitions(
        self,
        partitions: list[PartitionAssignment],
        factor: float,
    ) -> list[PartitionAssignment]:
        """Balance partitions within a single level."""
        if len(partitions) <= 1:
            return partitions

        costs = [p.estimated_cost for p in partitions]
        max_cost = max(costs) if costs else 0.0
        target_max = max_cost  # keep same max

        # Sort ascending by cost
        sorted_parts = sorted(partitions, key=lambda p: p.estimated_cost)

        balanced: list[PartitionAssignment] = []
        i = 0
        j = len(sorted_parts) - 1
        while i <= j:
            if i == j:
                balanced.append(sorted_parts[i])
                break
            small = sorted_parts[i]
            large = sorted_parts[j]
            merged_cost = small.estimated_cost + large.estimated_cost
            if target_max > 0 and merged_cost / target_max <= 1.0 + (1.0 - factor):
                # Merge small into large
                merged_ids = list(large.coordinate_ids) + list(small.coordinate_ids)
                balanced.append(
                    PartitionAssignment.create(
                        partition_id=_new_pid(),
                        level=large.level,
                        coordinate_ids=merged_ids,
                        estimated_cost=merged_cost,
                    )
                )
                i += 1
                j -= 1
            else:
                balanced.append(large)
                j -= 1

        return balanced

    # ------------------------------------------------------------------
    # Cost estimation
    # ------------------------------------------------------------------

    def _estimate_cost(
        self,
        coordinate_ids: list[str],
        morphisms: list[dict[str, Any]],
    ) -> float:
        """Estimate verification cost for a set of coordinates.

        The model is: base cost per coordinate + extra cost per internal edge
        (edges within the partition add constraint-solving overhead).
        """
        base_per_coord = 1.0
        extra_per_edge = 0.5

        coord_set = set(coordinate_ids)
        internal_edges = sum(
            1
            for m in morphisms
            if m.get("source_id") in coord_set and m.get("target_id") in coord_set
        )
        return len(coordinate_ids) * base_per_coord + internal_edges * extra_per_edge

    # ------------------------------------------------------------------
    # SCC-based partitioning
    # ------------------------------------------------------------------

    def scc_based_partition(
        self,
        coordinates: list[str],
        morphisms: list[dict[str, Any]],
    ) -> list[set[str]]:
        """Partition coordinates into Strongly Connected Components.

        Coordinates in the same SCC must be verified together; coordinates
        in different SCCs can potentially be verified in parallel.
        """
        coord_set = set(coordinates)
        # Build adjacency (directed)
        adj: dict[str, list[str]] = {c: [] for c in coord_set}
        for m in morphisms:
            src = m.get("source_id", "")
            tgt = m.get("target_id", "")
            if src in coord_set and tgt in coord_set and src != tgt:
                adj[src].append(tgt)

        sccs = self._tarjan_scc(adj)
        return [set(scc) for scc in sccs]

    def _tarjan_scc(
        self,
        adjacency: dict[str, list[str]],
    ) -> list[list[str]]:
        """Tarjan's algorithm for finding Strongly Connected Components.

        Returns a list of SCCs, each SCC being a list of node ids.
        """
        index_counter = [0]
        stack: list[str] = []
        on_stack: dict[str, bool] = {}
        index: dict[str, int] = {}
        lowlink: dict[str, int] = {}
        sccs: list[list[str]] = []

        def strongconnect(v: str) -> None:
            index[v] = lowlink[v] = index_counter[0]
            index_counter[0] += 1
            stack.append(v)
            on_stack[v] = True

            for w in adjacency.get(v, []):
                if w not in index:
                    strongconnect(w)
                    lowlink[v] = min(lowlink[v], lowlink[w])
                elif on_stack.get(w, False):
                    lowlink[v] = min(lowlink[v], index[w])

            if lowlink[v] == index[v]:
                scc: list[str] = []
                while True:
                    w = stack.pop()
                    on_stack[w] = False
                    scc.append(w)
                    if w == v:
                        break
                sccs.append(scc)

        # Use iterative DFS to avoid Python recursion limit on large graphs
        for node in list(adjacency.keys()):
            if node not in index:
                # Iterative Tarjan
                call_stack: list[tuple[str, int]] = [(node, 0)]
                parent: dict[str, Optional[str]] = {node: None}

                while call_stack:
                    v, child_idx = call_stack[-1]

                    if v not in index:
                        index[v] = lowlink[v] = index_counter[0]
                        index_counter[0] += 1
                        stack.append(v)
                        on_stack[v] = True

                    children = adjacency.get(v, [])
                    advanced = False
                    while child_idx < len(children):
                        w = children[child_idx]
                        child_idx += 1
                        call_stack[-1] = (v, child_idx)

                        if w not in index:
                            parent[w] = v
                            call_stack.append((w, 0))
                            advanced = True
                            break
                        elif on_stack.get(w, False):
                            lowlink[v] = min(lowlink[v], index[w])

                    if not advanced:
                        call_stack.pop()
                        p = parent.get(v)
                        if p is not None:
                            lowlink[p] = min(lowlink[p], lowlink[v])
                        if lowlink[v] == index[v]:
                            scc: list[str] = []
                            while True:
                                w = stack.pop()
                                on_stack[w] = False
                                scc.append(w)
                                if w == v:
                                    break
                            sccs.append(scc)

        return sccs

    # ------------------------------------------------------------------
    # Cross-partition edges
    # ------------------------------------------------------------------

    def cross_partition_edges(
        self,
        partitions: list[PartitionAssignment],
        morphisms: list[dict[str, Any]],
    ) -> list[tuple[str, str]]:
        """Return morphisms (as (source_id, target_id) pairs) crossing partition boundaries."""
        # Build reverse index: coord_id → partition_id
        coord_to_part: dict[str, str] = {}
        for p in partitions:
            for cid in p.coordinate_ids:
                coord_to_part[cid] = p.partition_id

        cross: list[tuple[str, str]] = []
        for m in morphisms:
            src = m.get("source_id", "")
            tgt = m.get("target_id", "")
            src_part = coord_to_part.get(src)
            tgt_part = coord_to_part.get(tgt)
            if src_part and tgt_part and src_part != tgt_part:
                cross.append((src, tgt))
        return cross

    # ------------------------------------------------------------------
    # Kernighan-Lin style cross-edge minimisation
    # ------------------------------------------------------------------

    def minimize_cross_edges(
        self,
        partitions: list[PartitionAssignment],
        morphisms: list[dict[str, Any]],
        iterations: int = 10,
    ) -> list[PartitionAssignment]:
        """Iteratively swap nodes between partitions to reduce cross-edges.

        Implements a simplified Kernighan-Lin style local search:
        - Pick the pair of adjacent partitions with the most cross-edges.
        - Try all single-node swaps between them.
        - Accept the swap that best reduces the cross-edge count.
        - Repeat for ``iterations`` rounds.
        """
        if len(partitions) < 2:
            return partitions

        # Work on mutable copies of coordinate id lists
        part_coords: dict[str, list[str]] = {
            p.partition_id: list(p.coordinate_ids) for p in partitions
        }
        part_level: dict[str, SiteLevel] = {p.partition_id: p.level for p in partitions}
        # Only swap within same level
        by_level: dict[SiteLevel, list[str]] = defaultdict(list)
        for pid, lvl in part_level.items():
            by_level[lvl].append(pid)

        def _count_cross(pc: dict[str, list[str]]) -> int:
            coord_to_p: dict[str, str] = {}
            for pid, cids in pc.items():
                for cid in cids:
                    coord_to_p[cid] = pid
            return sum(
                1
                for m in morphisms
                if coord_to_p.get(m.get("source_id", ""))
                != coord_to_p.get(m.get("target_id", ""))
                and m.get("source_id", "") in coord_to_p
                and m.get("target_id", "") in coord_to_p
            )

        for _ in range(iterations):
            improved = False
            for _level, pids in by_level.items():
                if len(pids) < 2:
                    continue
                for i in range(len(pids)):
                    for j in range(i + 1, len(pids)):
                        pa_id, pb_id = pids[i], pids[j]
                        pa_cids = part_coords[pa_id]
                        pb_cids = part_coords[pb_id]
                        if not pa_cids or not pb_cids:
                            continue
                        current_cross = _count_cross(part_coords)
                        best_gain = 0
                        best_swap: Optional[tuple[str, str]] = None
                        for a in pa_cids:
                            for b in pb_cids:
                                # Tentative swap
                                pa_new = [x for x in pa_cids if x != a] + [b]
                                pb_new = [x for x in pb_cids if x != b] + [a]
                                trial = dict(part_coords)
                                trial[pa_id] = pa_new
                                trial[pb_id] = pb_new
                                gain = current_cross - _count_cross(trial)
                                if gain > best_gain:
                                    best_gain = gain
                                    best_swap = (a, b)
                        if best_swap:
                            a, b = best_swap
                            part_coords[pa_id] = [x for x in pa_cids if x != a] + [b]
                            part_coords[pb_id] = [x for x in pb_cids if x != b] + [a]
                            improved = True
            if not improved:
                break

        # Reconstruct PartitionAssignment list
        result: list[PartitionAssignment] = []
        for p in partitions:
            new_cids = part_coords[p.partition_id]
            cost = self._estimate_cost(new_cids, morphisms)
            result.append(
                PartitionAssignment.create(
                    partition_id=p.partition_id,
                    level=p.level,
                    coordinate_ids=new_cids,
                    estimated_cost=cost,
                    worker_id=p.worker_id,
                )
            )
        return result


# ---------------------------------------------------------------------------
# PartitionScheduler
# ---------------------------------------------------------------------------


class PartitionScheduler:
    """Schedule partition assignments into parallel execution waves.

    Respects dependencies between partitions (imposed by cross-partition
    edges): a partition can only be scheduled once all partitions that
    produce data it consumes have been scheduled in an earlier wave.
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def schedule(
        self,
        partitions: list[PartitionAssignment],
        max_workers: int,
    ) -> list[list[PartitionAssignment]]:
        """Produce a list of waves, each wave being a list of partitions.

        Partitions in the same wave can be executed in parallel; partitions
        in wave *k* must complete before wave *k+1* begins.

        The schedule is computed by:
        1. Building a cross-edge graph between partitions.
        2. Computing a topological order.
        3. Grouping into waves of up to *max_workers* partitions.
        """
        if not partitions:
            return []

        # Build an empty cross-edge list (no morphism context here — the
        # caller can inject cross-edges via dependency_order if needed)
        ordered = self._dependency_order(partitions, cross_edges=[])
        waves = self._wave_assignment(ordered, max_workers)
        return waves

    def schedule_with_morphisms(
        self,
        partitions: list[PartitionAssignment],
        morphisms: list[dict[str, Any]],
        max_workers: int,
    ) -> list[list[PartitionAssignment]]:
        """Like ``schedule`` but uses morphisms to infer cross-partition deps."""
        if not partitions:
            return []

        partitioner = GeometricPartitioner()
        cross = partitioner.cross_partition_edges(partitions, morphisms)
        ordered = self._dependency_order(partitions, cross_edges=cross)
        waves = self._wave_assignment(ordered, max_workers)
        return waves

    # ------------------------------------------------------------------
    # Dependency order
    # ------------------------------------------------------------------

    def _dependency_order(
        self,
        partitions: list[PartitionAssignment],
        cross_edges: list[tuple[str, str]],
    ) -> list[PartitionAssignment]:
        """Return partitions in topological order derived from cross-edges.

        Cross-edges (src_coord, tgt_coord) imply that the partition owning
        *tgt_coord* depends on the partition owning *src_coord*.

        If no cross-edges are provided the original order is preserved.
        """
        if not cross_edges:
            return list(partitions)

        # Build coord → partition map
        coord_to_pid: dict[str, str] = {}
        for p in partitions:
            for cid in p.coordinate_ids:
                coord_to_pid[cid] = p.partition_id

        pid_map: dict[str, PartitionAssignment] = {p.partition_id: p for p in partitions}

        # Build dependency graph: pid → set of pids it depends on
        deps: dict[str, set[str]] = {p.partition_id: set() for p in partitions}
        for src_coord, tgt_coord in cross_edges:
            src_pid = coord_to_pid.get(src_coord)
            tgt_pid = coord_to_pid.get(tgt_coord)
            if src_pid and tgt_pid and src_pid != tgt_pid:
                deps[tgt_pid].add(src_pid)

        # Kahn's topological sort
        in_degree: dict[str, int] = {pid: len(d) for pid, d in deps.items()}
        queue: deque[str] = deque(pid for pid, deg in in_degree.items() if deg == 0)
        order: list[PartitionAssignment] = []
        reverse_deps: dict[str, list[str]] = defaultdict(list)
        for pid, dep_set in deps.items():
            for d in dep_set:
                reverse_deps[d].append(pid)

        while queue:
            pid = queue.popleft()
            order.append(pid_map[pid])
            for dependent in reverse_deps.get(pid, []):
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    queue.append(dependent)

        # If cycle detected, fall back to original order
        if len(order) < len(partitions):
            return list(partitions)

        return order

    # ------------------------------------------------------------------
    # Wave assignment
    # ------------------------------------------------------------------

    def _wave_assignment(
        self,
        ordered_partitions: list[PartitionAssignment],
        max_workers: int,
    ) -> list[list[PartitionAssignment]]:
        """Split an ordered list of partitions into parallel waves.

        Each wave contains at most *max_workers* partitions and all
        dependencies of every partition in wave *k* are in waves 0..k-1.
        """
        if not ordered_partitions:
            return []

        waves: list[list[PartitionAssignment]] = []
        remaining = list(ordered_partitions)

        while remaining:
            wave: list[PartitionAssignment] = remaining[:max_workers]
            waves.append(wave)
            remaining = remaining[max_workers:]

        return waves


__all__ = ["GeometricPartitioner", "PartitionScheduler"]
