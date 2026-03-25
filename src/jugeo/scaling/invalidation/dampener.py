from __future__ import annotations

import random
import time
from collections import deque
from typing import Any, Dict, List, Optional, Set

from .models import ContractBoundary, DampeningConfig, InvalidationStrategy


class InvalidationDampener:
    """Implements multiple invalidation-dampening strategies.

    ``dependency_graph`` arguments map a node ID to the list of node IDs
    that *depend on* that node (forward/downstream edges).
    """

    def __init__(self, config: DampeningConfig) -> None:
        self._config = config
        self._contracts: Dict[str, ContractBoundary] = {}
        self._stats: Dict[str, Any] = {
            "invalidations": 0,
            "by_strategy": {},
            "total_coords_invalidated": 0,
        }

    # ------------------------------------------------------------------
    # Contract management
    # ------------------------------------------------------------------

    def add_contract(self, boundary: ContractBoundary) -> None:
        self._contracts[boundary.coordinate_id] = boundary

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def invalidate(
        self,
        coordinate_id: str,
        dependency_graph: Dict[str, List[str]],
        change_detail: Optional[dict] = None,
    ) -> dict:
        strategy = self.choose_strategy(coordinate_id, dependency_graph)
        result: dict

        if strategy == InvalidationStrategy.FULL_CASCADE:
            inv = self._full_cascade(coordinate_id, dependency_graph)
            result = {
                "strategy": strategy.value,
                "invalidated": sorted(inv),
                "timestamp": time.time(),
            }
        elif strategy == InvalidationStrategy.CONTRACT_BOUNDED:
            inv = self._contract_bounded(coordinate_id, dependency_graph)
            result = {
                "strategy": strategy.value,
                "invalidated": sorted(inv),
                "timestamp": time.time(),
            }
        elif strategy == InvalidationStrategy.TIERED:
            tiers = self._tiered(coordinate_id, dependency_graph)
            all_inv: Set[str] = set()
            for s in tiers.values():
                all_inv |= s
            result = {
                "strategy": strategy.value,
                "invalidated": sorted(all_inv),
                "tiered": {k: sorted(v) for k, v in tiers.items()},
                "timestamp": time.time(),
            }
        elif strategy == InvalidationStrategy.PROBABILISTIC:
            inv = self._probabilistic(
                coordinate_id,
                dependency_graph,
                self._config.probabilistic_sample_rate,
            )
            result = {
                "strategy": strategy.value,
                "invalidated": sorted(inv),
                "timestamp": time.time(),
            }
        elif strategy == InvalidationStrategy.SEMANTIC:
            inv = self._semantic_analysis(
                coordinate_id, change_detail, dependency_graph
            )
            result = {
                "strategy": strategy.value,
                "invalidated": sorted(inv),
                "timestamp": time.time(),
            }
        else:
            inv = self._full_cascade(coordinate_id, dependency_graph)
            result = {
                "strategy": "FULL_CASCADE",
                "invalidated": sorted(inv),
                "timestamp": time.time(),
            }

        self._record_stats(strategy, result)
        return result

    # ------------------------------------------------------------------
    # Strategies
    # ------------------------------------------------------------------

    def _full_cascade(
        self, coord: str, graph: Dict[str, List[str]]
    ) -> Set[str]:
        visited: Set[str] = set()
        queue: deque[tuple[str, int]] = deque()
        queue.append((coord, 0))
        while queue:
            node, depth = queue.popleft()
            if node in visited:
                continue
            if depth > self._config.max_depth:
                continue
            visited.add(node)
            for dep in graph.get(node, []):
                if dep not in visited:
                    queue.append((dep, depth + 1))
        return visited

    def _contract_bounded(
        self, coord: str, graph: Dict[str, List[str]]
    ) -> Set[str]:
        visited: Set[str] = set()
        queue: deque[tuple[str, int]] = deque()
        queue.append((coord, 0))
        while queue:
            node, depth = queue.popleft()
            if node in visited:
                continue
            if depth > self._config.max_depth:
                continue
            # Stop at contract boundaries (but still include the source)
            if node != coord and node in self._contracts:
                visited.add(node)
                continue
            visited.add(node)
            for dep in graph.get(node, []):
                if dep not in visited:
                    queue.append((dep, depth + 1))
        return visited

    def _tiered(
        self, coord: str, graph: Dict[str, List[str]]
    ) -> Dict[str, Set[str]]:
        depths = self._config.tiered_depths  # e.g. [1, 3, 10]
        tier_names = ["immediate", "deferred", "lazy"]

        # BFS tracking depth
        depth_map: Dict[str, int] = {}
        queue: deque[tuple[str, int]] = deque()
        queue.append((coord, 0))
        while queue:
            node, d = queue.popleft()
            if node in depth_map:
                continue
            if d > self._config.max_depth:
                continue
            depth_map[node] = d
            for dep in graph.get(node, []):
                if dep not in depth_map:
                    queue.append((dep, d + 1))

        tiers: Dict[str, Set[str]] = {name: set() for name in tier_names}
        prev_limit = 0
        for i, limit in enumerate(depths):
            name = tier_names[i] if i < len(tier_names) else tier_names[-1]
            for node, d in depth_map.items():
                if node == coord:
                    continue
                if prev_limit < d <= limit:
                    tiers[name].add(node)
            prev_limit = limit

        return tiers

    def _probabilistic(
        self,
        coord: str,
        graph: Dict[str, List[str]],
        sample_rate: float,
    ) -> Set[str]:
        visited: Set[str] = set()
        queue: deque[tuple[str, int]] = deque()
        queue.append((coord, 0))
        while queue:
            node, depth = queue.popleft()
            if node in visited:
                continue
            if depth > self._config.max_depth:
                continue
            # Always include source and depth <= 3; probabilistically after
            if depth > 3 and random.random() > sample_rate:
                continue
            visited.add(node)
            for dep in graph.get(node, []):
                if dep not in visited:
                    queue.append((dep, depth + 1))
        return visited

    def _semantic_analysis(
        self,
        coord: str,
        change_detail: Optional[dict],
        graph: Dict[str, List[str]],
    ) -> Set[str]:
        if not change_detail:
            return self._contract_bounded(coord, graph)

        affects = set(change_detail.get("affects", []))
        if not affects:
            return self._contract_bounded(coord, graph)

        # Only include nodes that are in the affects set
        all_reachable = self._full_cascade(coord, graph)
        return all_reachable & affects | {coord}

    # ------------------------------------------------------------------
    # Strategy selection heuristics
    # ------------------------------------------------------------------

    def choose_strategy(
        self, coord: str, graph: Dict[str, List[str]]
    ) -> InvalidationStrategy:
        if coord in self._contracts:
            return InvalidationStrategy.CONTRACT_BOUNDED

        # Estimate reachable size with BFS up to a limit
        visited: Set[str] = set()
        queue: deque[str] = deque([coord])
        limit = 101
        while queue and len(visited) < limit:
            node = queue.popleft()
            if node in visited:
                continue
            visited.add(node)
            for dep in graph.get(node, []):
                if dep not in visited:
                    queue.append(dep)

        if len(visited) >= limit:
            return InvalidationStrategy.PROBABILISTIC

        if self._contracts:
            return InvalidationStrategy.CONTRACT_BOUNDED

        return InvalidationStrategy.FULL_CASCADE

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    def _record_stats(self, strategy: InvalidationStrategy, result: dict) -> None:
        self._stats["invalidations"] += 1
        s = strategy.value
        self._stats["by_strategy"][s] = self._stats["by_strategy"].get(s, 0) + 1
        self._stats["total_coords_invalidated"] += len(result.get("invalidated", []))

    def statistics(self) -> dict:
        return dict(self._stats)
