from __future__ import annotations

import copy
import fnmatch
import time
import uuid
from collections import deque
from typing import Any, Dict, List, Optional

from .models import (
    Bridge,
    BridgeIndex,
    BridgeKind,
    ComposedBridge,
    FederationQuery,
    FederationResult,
    FederationStatistics,
)

TRUST_LEVELS = ["NONE", "WEAK", "PARTIAL", "STANDARD", "STRONG", "FULL"]


def _trust_index(level: str) -> int:
    try:
        return TRUST_LEVELS.index(level)
    except ValueError:
        return 0


class BridgeIndexer:
    """Builds and maintains a multi-index over bridges."""

    def __init__(self) -> None:
        self._bridges: Dict[str, Bridge] = {}
        self._index: BridgeIndex = BridgeIndex(
            bridges_by_pack_pair={},
            bridges_by_kind={},
            bridges_by_pattern={},
        )

    def build_index(self, bridges: List[Bridge]) -> BridgeIndex:
        self._bridges.clear()
        self._index = BridgeIndex(
            bridges_by_pack_pair={},
            bridges_by_kind={},
            bridges_by_pattern={},
        )
        for b in bridges:
            self._add_to_index(b)
        return self._index

    def lookup(
        self,
        source_pack: str,
        target_pack: str,
        kind: Optional[str] = None,
    ) -> List[Bridge]:
        key = f"{source_pack}:{target_pack}"
        ids = self._index.bridges_by_pack_pair.get(key, [])
        results = [self._bridges[bid] for bid in ids if bid in self._bridges]
        if kind is not None:
            results = [b for b in results if b.kind.value == kind or b.kind == kind]
        return results

    def lookup_by_pattern(self, proposition: str) -> List[Bridge]:
        results: List[Bridge] = []
        for pattern, ids in self._index.bridges_by_pattern.items():
            if fnmatch.fnmatch(proposition, pattern):
                for bid in ids:
                    bridge = self._bridges.get(bid)
                    if bridge is not None and bridge not in results:
                        results.append(bridge)
        return results

    def add_bridge(self, bridge: Bridge) -> None:
        self._add_to_index(bridge)

    def remove_bridge(self, bridge_id: str) -> None:
        bridge = self._bridges.pop(bridge_id, None)
        if bridge is None:
            return

        # Remove from pack-pair index
        pair_key = f"{bridge.source_pack}:{bridge.target_pack}"
        ids = self._index.bridges_by_pack_pair.get(pair_key, [])
        self._index.bridges_by_pack_pair[pair_key] = [
            bid for bid in ids if bid != bridge_id
        ]

        # Remove from kind index
        kind_val = bridge.kind.value if isinstance(bridge.kind, BridgeKind) else bridge.kind
        ids = self._index.bridges_by_kind.get(kind_val, [])
        self._index.bridges_by_kind[kind_val] = [
            bid for bid in ids if bid != bridge_id
        ]

        # Remove from pattern index
        pat = bridge.proposition_pattern
        ids = self._index.bridges_by_pattern.get(pat, [])
        self._index.bridges_by_pattern[pat] = [
            bid for bid in ids if bid != bridge_id
        ]

    # ------------------------------------------------------------------

    def _add_to_index(self, bridge: Bridge) -> None:
        self._bridges[bridge.id] = bridge

        pair_key = f"{bridge.source_pack}:{bridge.target_pack}"
        self._index.bridges_by_pack_pair.setdefault(pair_key, []).append(bridge.id)

        kind_val = bridge.kind.value if isinstance(bridge.kind, BridgeKind) else bridge.kind
        self._index.bridges_by_kind.setdefault(kind_val, []).append(bridge.id)

        pat = bridge.proposition_pattern
        self._index.bridges_by_pattern.setdefault(pat, []).append(bridge.id)

    @property
    def index(self) -> BridgeIndex:
        return self._index


class FederationRouter:
    """Routes federation queries through bridges."""

    def route(
        self, query: FederationQuery, index: BridgeIndex, indexer: Optional[BridgeIndexer] = None
    ) -> Optional[FederationResult]:
        bridge = self._find_direct_bridge(query, index, indexer)
        if bridge is not None:
            transported = self._apply_bridge(bridge, {"judgment_id": query.source_judgment_id})
            return FederationResult(
                query_id=query.source_judgment_id,
                bridge_id=bridge.id,
                transported_judgment=transported,
                trust_level=bridge.trust_attenuation,
            )

        composed = self._find_composed_bridge(query, index, indexer)
        if composed is not None:
            transported = {"judgment_id": query.source_judgment_id}
            # Apply each bridge in the composition
            if indexer is not None:
                for bid in composed.bridges:
                    b = indexer._bridges.get(bid)
                    if b is not None:
                        transported = self._apply_bridge(b, transported)

            return FederationResult(
                query_id=query.source_judgment_id,
                bridge_id=composed.id,
                transported_judgment=transported,
                trust_level=composed.composition_trust,
            )

        return None

    def _find_direct_bridge(
        self,
        query: FederationQuery,
        index: BridgeIndex,
        indexer: Optional[BridgeIndexer] = None,
    ) -> Optional[Bridge]:
        pair_key = f"{query.source_pack}:{query.target_pack}"
        bridge_ids = index.bridges_by_pack_pair.get(pair_key, [])
        if not bridge_ids or indexer is None:
            return None

        for bid in bridge_ids:
            bridge = indexer._bridges.get(bid)
            if bridge is None:
                continue
            if fnmatch.fnmatch(query.proposition, bridge.proposition_pattern):
                return bridge
        return None

    def _find_composed_bridge(
        self,
        query: FederationQuery,
        index: BridgeIndex,
        indexer: Optional[BridgeIndexer] = None,
        max_hops: int = 3,
    ) -> Optional[ComposedBridge]:
        if indexer is None:
            return None

        # BFS over pack graph
        # Each bridge is an edge: source_pack -> target_pack
        # Build adjacency: pack -> [(target_pack, bridge)]
        adj: Dict[str, List[tuple[str, Bridge]]] = {}
        for bridge in indexer._bridges.values():
            adj.setdefault(bridge.source_pack, []).append((bridge.target_pack, bridge))

        # BFS
        queue: deque[tuple[str, list[Bridge]]] = deque()
        queue.append((query.source_pack, []))
        visited: set[str] = {query.source_pack}

        while queue:
            current_pack, path = queue.popleft()
            if len(path) > max_hops:
                continue

            for target_pack, bridge in adj.get(current_pack, []):
                if target_pack == query.target_pack:
                    final_path = path + [bridge]
                    trust = self._compute_composition_trust(final_path)
                    return ComposedBridge(
                        id=str(uuid.uuid4()),
                        bridges=[b.id for b in final_path],
                        composition_trust=trust,
                    )
                if target_pack not in visited:
                    visited.add(target_pack)
                    queue.append((target_pack, path + [bridge]))

        return None

    def _apply_bridge(self, bridge: Bridge, judgment_data: dict) -> dict:
        result = copy.deepcopy(judgment_data)
        result["_transported_by"] = bridge.id
        result["_transport_function"] = bridge.transport_function_name
        result["_trust_attenuation"] = bridge.trust_attenuation
        result["_source_pack"] = bridge.source_pack
        result["_target_pack"] = bridge.target_pack
        return result

    def _compute_composition_trust(self, bridges: List[Bridge]) -> str:
        if not bridges:
            return TRUST_LEVELS[-1]
        min_idx = len(TRUST_LEVELS) - 1
        for b in bridges:
            idx = _trust_index(b.trust_attenuation)
            if idx < min_idx:
                min_idx = idx
        return TRUST_LEVELS[min_idx]

    def batch_route(
        self,
        queries: List[FederationQuery],
        index: BridgeIndex,
        indexer: Optional[BridgeIndexer] = None,
    ) -> List[Optional[FederationResult]]:
        return [self.route(q, index, indexer) for q in queries]


class FederationCache:
    """Simple cache for federation results."""

    def __init__(self, max_entries: int = 10000) -> None:
        self._cache: Dict[tuple, FederationResult] = {}
        self._stats = FederationStatistics()
        self._max_entries = max_entries

    def get(
        self, source_judgment_id: str, bridge_set_hash: str
    ) -> Optional[FederationResult]:
        key = (source_judgment_id, bridge_set_hash)
        result = self._cache.get(key)
        self._stats.total_queries += 1
        if result is not None:
            self._stats.cache_hits += 1
        return result

    def put(self, key_tuple: tuple, result: FederationResult) -> None:
        if len(self._cache) >= self._max_entries:
            first_key = next(iter(self._cache))
            del self._cache[first_key]
        self._cache[key_tuple] = result

    def invalidate_for_pack(self, pack_id: str) -> None:
        keys_to_remove = [k for k in self._cache if pack_id in str(k)]
        for k in keys_to_remove:
            del self._cache[k]

    def statistics(self) -> FederationStatistics:
        return self._stats
