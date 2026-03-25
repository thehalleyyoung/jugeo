"""Partition management for JuGeo distributed verification.

The :class:`PartitionManager` divides a set of coordinates and morphisms
into sub-graphs (partitions) that can be verified independently by worker
processes.  Three partitioning strategies are provided:

- **SCC** (strongly connected components): partitions are maximal sets of
  mutually-reachable coordinates, ideal for dependency-aware verification.
- **Level**: partitions are determined by a ``level`` attribute on each
  coordinate (e.g. ``"function"``, ``"class"``, ``"module"``).
- **Balanced**: k-way balanced partition that minimises the maximum cost
  assigned to any single worker.

After creating partitions, :meth:`assign_to_workers` produces a
``{partition_id: worker_id}`` mapping.  Cross-partition morphisms are
turned into :class:`~jugeo.scaling.workers.models.Task` objects with kind
:attr:`~jugeo.scaling.workers.models.TaskKind.DESCENT_CHECK`.
"""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from typing import Dict, List, Optional, Set, Tuple

from jugeo.scaling.workers.models import (
    PartitionDef,
    Task,
    TaskKind,
    WorkerInfo,
)

logger = logging.getLogger(__name__)


def _uid() -> str:
    return uuid.uuid4().hex


# ---------------------------------------------------------------------------
# PartitionManager
# ---------------------------------------------------------------------------

class PartitionManager:
    """Utility class for creating and managing verification partitions.

    This class is stateless: all partition state is carried in the returned
    :class:`~jugeo.scaling.workers.models.PartitionDef` objects.

    Examples
    --------
    >>> pm = PartitionManager()
    >>> coords = [{"id": "c1", "level": "function"}, {"id": "c2", "level": "module"}]
    >>> morphisms = [{"id": "m1", "source_id": "c1", "target_id": "c2"}]
    >>> partitions = pm.create_partitions(coords, morphisms, strategy="scc")
    >>> len(partitions) >= 1
    True
    """

    # ------------------------------------------------------------------
    # Public API: partition creation
    # ------------------------------------------------------------------

    def create_partitions(
        self,
        coordinates: list,
        morphisms: list,
        strategy: str = "scc",
        max_size: int = 1000,
    ) -> List[PartitionDef]:
        """Create a list of :class:`~jugeo.scaling.workers.models.PartitionDef` objects.

        Parameters
        ----------
        coordinates:
            List of coordinate dicts, each with at least ``"id"`` and
            optionally ``"level"``, ``"package"``, and a numeric
            ``"cost"`` hint.
        morphisms:
            List of morphism dicts, each with at least ``"id"``,
            ``"source_id"``, and ``"target_id"``.
        strategy:
            One of ``"scc"``, ``"level"``, ``"balanced"``.
        max_size:
            Maximum number of coordinates in a single partition before it
            is split.  Only used by the SCC strategy.

        Returns
        -------
        list of :class:`~jugeo.scaling.workers.models.PartitionDef`
        """
        if not coordinates:
            return []

        coord_ids = [c["id"] for c in coordinates]

        if strategy == "scc":
            groups = self._scc_partition(coordinates, morphisms)
        elif strategy == "level":
            levels = {c["id"]: c.get("level", "unknown") for c in coordinates}
            groups = self._level_partition(coordinates, levels)
        elif strategy == "balanced":
            k = max(1, len(coordinates) // max(1, max_size))
            groups = self._balanced_partition(coordinates, k)
        else:
            raise ValueError(f"Unknown partition strategy: {strategy!r}")

        # Build morphism index for quick lookup.
        morph_by_source: Dict[str, list] = defaultdict(list)
        for m in morphisms:
            morph_by_source[m["source_id"]].append(m)

        partitions = []
        for group in groups:
            group_set = set(group)
            relevant_morphisms = [
                m["id"]
                for m in morphisms
                if m["source_id"] in group_set and m["target_id"] in group_set
            ]
            # Estimate cost as number of coordinates + number of morphisms.
            cost = float(len(group) + len(relevant_morphisms))
            # Infer level / package from first coordinate if homogeneous.
            sample_coord = next(
                (c for c in coordinates if c["id"] in group_set), None
            )
            level = sample_coord.get("level") if sample_coord else None
            package = sample_coord.get("package") if sample_coord else None

            partitions.append(
                PartitionDef.create(
                    coordinate_ids=sorted(group),
                    morphism_ids=relevant_morphisms,
                    estimated_cost=cost,
                    level=level,
                    package=package,
                )
            )

        # Split over-large partitions.
        result = []
        for p in partitions:
            if len(p.coordinate_ids) > max_size:
                result.extend(
                    self._split_partition(p, coordinates, morphisms, max_size)
                )
            else:
                result.append(p)
        return result

    def _split_partition(
        self,
        partition: PartitionDef,
        all_coordinates: list,
        all_morphisms: list,
        max_size: int,
    ) -> List[PartitionDef]:
        """Split an over-large partition into smaller chunks."""
        coord_ids = list(partition.coordinate_ids)
        chunks = [
            coord_ids[i: i + max_size]
            for i in range(0, len(coord_ids), max_size)
        ]
        result = []
        for chunk in chunks:
            chunk_set = set(chunk)
            morphism_ids = [
                m["id"]
                for m in all_morphisms
                if m["source_id"] in chunk_set and m["target_id"] in chunk_set
            ]
            result.append(
                PartitionDef.create(
                    coordinate_ids=chunk,
                    morphism_ids=morphism_ids,
                    estimated_cost=float(len(chunk) + len(morphism_ids)),
                    level=partition.level,
                    package=partition.package,
                )
            )
        return result

    # ------------------------------------------------------------------
    # Partitioning strategies
    # ------------------------------------------------------------------

    def _scc_partition(
        self,
        coordinates: list,
        morphisms: list,
    ) -> List[List[str]]:
        """Partition coordinates by strongly connected components.

        Coordinates not involved in any cycle form singleton SCCs.
        """
        # Build adjacency list (id -> list of target ids).
        adjacency: Dict[str, List[str]] = {c["id"]: [] for c in coordinates}
        for m in morphisms:
            src = m.get("source_id", "")
            tgt = m.get("target_id", "")
            if src in adjacency:
                adjacency[src].append(tgt)

        sccs = self._tarjan_scc(adjacency)
        # Filter out empty SCCs and ensure only known coordinate IDs are included.
        known = {c["id"] for c in coordinates}
        return [
            [n for n in scc if n in known]
            for scc in sccs
            if any(n in known for n in scc)
        ]

    def _level_partition(
        self,
        coordinates: list,
        levels: Dict[str, str],
    ) -> List[List[str]]:
        """Partition coordinates by their level attribute."""
        buckets: Dict[str, List[str]] = defaultdict(list)
        for c in coordinates:
            cid = c["id"]
            level = levels.get(cid, "unknown")
            buckets[level].append(cid)
        return list(buckets.values())

    def _balanced_partition(
        self,
        coordinates: list,
        k: int,
    ) -> List[List[str]]:
        """Divide coordinates into *k* roughly equal groups.

        Uses cost hints if available, otherwise treats all coordinates as
        having equal cost.
        """
        if k <= 0:
            k = 1
        # Sort by cost descending for a greedy bin-packing approach.
        sorted_coords = sorted(
            coordinates,
            key=lambda c: float(c.get("cost", 1.0)),
            reverse=True,
        )
        bins: List[List[str]] = [[] for _ in range(k)]
        bin_costs = [0.0] * k
        for c in sorted_coords:
            # Assign to the bin with the lowest current cost.
            idx = bin_costs.index(min(bin_costs))
            bins[idx].append(c["id"])
            bin_costs[idx] += float(c.get("cost", 1.0))
        return [b for b in bins if b]

    # ------------------------------------------------------------------
    # Public API: balancing and assignment
    # ------------------------------------------------------------------

    def balance_partitions(
        self,
        partitions: List[PartitionDef],
        factor: float = 0.8,
    ) -> List[PartitionDef]:
        """Rebalance partitions so no partition is disproportionately large.

        Partitions with a cost exceeding ``factor * mean_cost`` are
        eligible for splitting (if they contain more than one coordinate).

        Parameters
        ----------
        partitions:
            Existing partition list.
        factor:
            Imbalance threshold relative to mean cost.

        Returns
        -------
        Rebalanced list of :class:`~jugeo.scaling.workers.models.PartitionDef`.
        """
        if not partitions:
            return []
        mean_cost = sum(p.estimated_cost for p in partitions) / len(partitions)
        threshold = factor * mean_cost if mean_cost > 0 else 1.0
        result = []
        for p in partitions:
            if p.estimated_cost > threshold and len(p.coordinate_ids) > 1:
                # Split evenly.
                half = len(p.coordinate_ids) // 2
                left_ids = p.coordinate_ids[:half]
                right_ids = p.coordinate_ids[half:]
                left_morphisms = [
                    m for m in p.morphism_ids if True  # kept for simplicity
                ]
                left_cost = p.estimated_cost * (half / len(p.coordinate_ids))
                right_cost = p.estimated_cost - left_cost
                result.append(
                    PartitionDef.create(
                        coordinate_ids=left_ids,
                        morphism_ids=[],
                        estimated_cost=left_cost,
                        level=p.level,
                        package=p.package,
                    )
                )
                result.append(
                    PartitionDef.create(
                        coordinate_ids=right_ids,
                        morphism_ids=[],
                        estimated_cost=right_cost,
                        level=p.level,
                        package=p.package,
                    )
                )
            else:
                result.append(p)
        return result

    def assign_to_workers(
        self,
        partitions: List[PartitionDef],
        workers: List[WorkerInfo],
        strategy: str = "cost_balanced",
    ) -> Dict[str, str]:
        """Assign partitions to workers.

        Parameters
        ----------
        partitions:
            Partitions to assign.
        workers:
            Available workers.
        strategy:
            ``"cost_balanced"`` or ``"round_robin"``.

        Returns
        -------
        ``{partition_id: worker_id}`` mapping.
        """
        if not workers or not partitions:
            return {}
        if strategy == "cost_balanced":
            return self._cost_balanced_assignment(partitions, workers)
        elif strategy == "round_robin":
            return self._round_robin_assignment(partitions, workers)
        else:
            raise ValueError(f"Unknown assignment strategy: {strategy!r}")

    def _cost_balanced_assignment(
        self,
        partitions: List[PartitionDef],
        workers: List[WorkerInfo],
    ) -> Dict[str, str]:
        """Assign partitions greedily to minimise max worker cost."""
        # Sort partitions by cost descending.
        sorted_partitions = sorted(
            partitions, key=lambda p: p.estimated_cost, reverse=True
        )
        worker_loads = {w.id: 0.0 for w in workers}
        assignment: Dict[str, str] = {}
        for partition in sorted_partitions:
            # Assign to worker with lowest current load.
            worker_id = min(worker_loads, key=worker_loads.__getitem__)
            assignment[partition.id] = worker_id
            worker_loads[worker_id] += partition.estimated_cost
        return assignment

    def _round_robin_assignment(
        self,
        partitions: List[PartitionDef],
        workers: List[WorkerInfo],
    ) -> Dict[str, str]:
        """Assign partitions to workers in round-robin order."""
        assignment: Dict[str, str] = {}
        worker_ids = [w.id for w in workers]
        for i, partition in enumerate(partitions):
            assignment[partition.id] = worker_ids[i % len(worker_ids)]
        return assignment

    # ------------------------------------------------------------------
    # Cross-partition tasks
    # ------------------------------------------------------------------

    def cross_partition_tasks(
        self,
        partitions: List[PartitionDef],
        morphisms: list,
    ) -> List[Task]:
        """Create :class:`~jugeo.scaling.workers.models.Task` objects for cross-partition edges.

        For each morphism whose source and target reside in *different*
        partitions, a :attr:`~jugeo.scaling.workers.models.TaskKind.DESCENT_CHECK`
        task is created so the coordinator can verify the gluing condition.

        Parameters
        ----------
        partitions:
            The current partition list.
        morphisms:
            All morphisms (including cross-partition ones).

        Returns
        -------
        List of :class:`~jugeo.scaling.workers.models.Task` objects.
        """
        # Build coordinate-to-partition index.
        coord_to_partition: Dict[str, str] = {}
        for p in partitions:
            for cid in p.coordinate_ids:
                coord_to_partition[cid] = p.id

        tasks = []
        seen_pairs: Set[Tuple[str, str]] = set()
        for m in morphisms:
            src = m.get("source_id", "")
            tgt = m.get("target_id", "")
            src_part = coord_to_partition.get(src)
            tgt_part = coord_to_partition.get(tgt)
            if src_part is None or tgt_part is None:
                continue
            if src_part == tgt_part:
                continue
            pair = (min(src_part, tgt_part), max(src_part, tgt_part))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)

            # Collect all overlapping coordinate IDs for this pair.
            src_set = set(
                next(p.coordinate_ids for p in partitions if p.id == src_part)
            )
            tgt_set = set(
                next(p.coordinate_ids for p in partitions if p.id == tgt_part)
            )
            overlap_morphisms = [
                mu
                for mu in morphisms
                if (
                    mu.get("source_id") in src_set
                    and mu.get("target_id") in tgt_set
                )
                or (
                    mu.get("source_id") in tgt_set
                    and mu.get("target_id") in src_set
                )
            ]
            overlap_ids = list(
                {
                    mid
                    for mu in overlap_morphisms
                    for mid in [mu.get("source_id", ""), mu.get("target_id", "")]
                    if mid
                }
            )
            tasks.append(
                Task.create(
                    kind=TaskKind.DESCENT_CHECK,
                    payload={
                        "source_partition": src_part,
                        "target_partition": tgt_part,
                        "overlap_ids": overlap_ids,
                    },
                    priority=2.0,  # Higher priority than regular tasks.
                )
            )
        return tasks

    # ------------------------------------------------------------------
    # Rebalance on worker change
    # ------------------------------------------------------------------

    def rebalance_on_worker_change(
        self,
        partitions: List[PartitionDef],
        old_workers: List[WorkerInfo],
        new_workers: List[WorkerInfo],
    ) -> Dict[str, str]:
        """Reassign partitions when workers join or leave.

        Uses a cost-balanced strategy with the new worker set.

        Parameters
        ----------
        partitions:
            Current partition list.
        old_workers:
            Previous worker list (used only for logging).
        new_workers:
            Updated worker list.

        Returns
        -------
        ``{partition_id: worker_id}`` mapping.
        """
        logger.info(
            "Rebalancing %d partitions across %d workers (was %d)",
            len(partitions),
            len(new_workers),
            len(old_workers),
        )
        return self._cost_balanced_assignment(partitions, new_workers)

    # ------------------------------------------------------------------
    # Tarjan SCC
    # ------------------------------------------------------------------

    def _tarjan_scc(
        self,
        adjacency: Dict[str, List[str]],
    ) -> List[List[str]]:
        """Tarjan's algorithm for finding all SCCs in a directed graph.

        Parameters
        ----------
        adjacency:
            ``{node_id: [neighbour_ids]}`` mapping.

        Returns
        -------
        List of SCCs, each SCC is a list of node IDs.  SCCs are returned
        in reverse topological order.
        """
        index_counter = [0]
        stack: List[str] = []
        lowlinks: Dict[str, int] = {}
        index: Dict[str, int] = {}
        on_stack: Dict[str, bool] = {}
        sccs: List[List[str]] = []

        def strongconnect(v: str) -> None:
            index[v] = index_counter[0]
            lowlinks[v] = index_counter[0]
            index_counter[0] += 1
            stack.append(v)
            on_stack[v] = True

            for w in adjacency.get(v, []):
                if w not in index:
                    strongconnect(w)
                    lowlinks[v] = min(lowlinks[v], lowlinks[w])
                elif on_stack.get(w, False):
                    lowlinks[v] = min(lowlinks[v], index[w])

            if lowlinks[v] == index[v]:
                scc: List[str] = []
                while True:
                    w = stack.pop()
                    on_stack[w] = False
                    scc.append(w)
                    if w == v:
                        break
                sccs.append(scc)

        # Iterative version to avoid Python's recursion limit on large graphs.
        # We use an explicit call stack.
        def strongconnect_iterative(start: str) -> None:
            call_stack: List[Tuple] = [(start, iter(adjacency.get(start, [])))]
            index[start] = index_counter[0]
            lowlinks[start] = index_counter[0]
            index_counter[0] += 1
            stack.append(start)
            on_stack[start] = True

            while call_stack:
                v, neighbours = call_stack[-1]
                try:
                    w = next(neighbours)
                    if w not in index:
                        index[w] = index_counter[0]
                        lowlinks[w] = index_counter[0]
                        index_counter[0] += 1
                        stack.append(w)
                        on_stack[w] = True
                        call_stack.append(
                            (w, iter(adjacency.get(w, [])))
                        )
                    elif on_stack.get(w, False):
                        lowlinks[v] = min(lowlinks[v], index[w])
                except StopIteration:
                    call_stack.pop()
                    if call_stack:
                        parent, _ = call_stack[-1]
                        lowlinks[parent] = min(lowlinks[parent], lowlinks[v])
                    if lowlinks[v] == index[v]:
                        scc: List[str] = []
                        while True:
                            w = stack.pop()
                            on_stack[w] = False
                            scc.append(w)
                            if w == v:
                                break
                        sccs.append(scc)

        for node in adjacency:
            if node not in index:
                strongconnect_iterative(node)

        return sccs

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    def partition_statistics(self, partitions: List[PartitionDef]) -> dict:
        """Return summary statistics for a list of partitions.

        Parameters
        ----------
        partitions:
            The partition list to summarise.

        Returns
        -------
        dict
            ``count``, ``total_coordinates``, ``total_morphisms``,
            ``min_cost``, ``max_cost``, ``mean_cost``,
            ``levels`` (set of level strings).
        """
        if not partitions:
            return {
                "count": 0,
                "total_coordinates": 0,
                "total_morphisms": 0,
                "min_cost": 0.0,
                "max_cost": 0.0,
                "mean_cost": 0.0,
                "levels": [],
            }
        costs = [p.estimated_cost for p in partitions]
        levels = sorted({p.level for p in partitions if p.level})
        return {
            "count": len(partitions),
            "total_coordinates": sum(len(p.coordinate_ids) for p in partitions),
            "total_morphisms": sum(len(p.morphism_ids) for p in partitions),
            "min_cost": min(costs),
            "max_cost": max(costs),
            "mean_cost": sum(costs) / len(costs),
            "levels": levels,
        }
