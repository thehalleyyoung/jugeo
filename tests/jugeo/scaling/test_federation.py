from __future__ import annotations

import time
import uuid

import pytest

from src.jugeo.scaling.federation.models import (
    Bridge,
    BridgeIndex,
    BridgeKind,
    ComposedBridge,
    FederationQuery,
    FederationResult,
    FederationStatistics,
)
from src.jugeo.scaling.federation.algorithms import (
    BridgeIndexer,
    FederationCache,
    FederationRouter,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_bridge(
    source: str = "pack_a",
    target: str = "pack_b",
    kind: BridgeKind = BridgeKind.TYPE_SAFETY,
    pattern: str = "type_safe_*",
    trust: str = "STRONG",
) -> Bridge:
    return Bridge(
        id=str(uuid.uuid4()),
        source_pack=source,
        target_pack=target,
        kind=kind,
        proposition_pattern=pattern,
        transport_function_name="transport_fn",
        trust_attenuation=trust,
        created_at=time.time(),
    )


# ===================================================================
# BridgeIndexer tests
# ===================================================================


class TestBridgeIndexer:
    def test_build_index(self) -> None:
        indexer = BridgeIndexer()
        bridges = [_make_bridge(), _make_bridge(source="pack_c", target="pack_d")]
        index = indexer.build_index(bridges)
        assert len(index.bridges_by_pack_pair) >= 2

    def test_lookup_by_pack_pair(self) -> None:
        indexer = BridgeIndexer()
        b = _make_bridge()
        indexer.build_index([b])
        results = indexer.lookup("pack_a", "pack_b")
        assert len(results) == 1
        assert results[0].id == b.id

    def test_lookup_by_pattern(self) -> None:
        indexer = BridgeIndexer()
        b = _make_bridge(pattern="type_safe_*")
        indexer.build_index([b])
        results = indexer.lookup_by_pattern("type_safe_int")
        assert len(results) >= 1

    def test_add_and_remove_bridge(self) -> None:
        indexer = BridgeIndexer()
        indexer.build_index([])
        b = _make_bridge()
        indexer.add_bridge(b)
        results = indexer.lookup("pack_a", "pack_b")
        assert len(results) == 1

        indexer.remove_bridge(b.id)
        results = indexer.lookup("pack_a", "pack_b")
        assert len(results) == 0


# ===================================================================
# FederationRouter tests
# ===================================================================


class TestFederationRouter:
    def test_route_direct_bridge(self) -> None:
        indexer = BridgeIndexer()
        b = _make_bridge(pattern="type_safe_*")
        index = indexer.build_index([b])
        router = FederationRouter()
        query = FederationQuery(
            source_judgment_id="j1",
            source_pack="pack_a",
            target_pack="pack_b",
            proposition="type_safe_int",
        )
        result = router.route(query, index, indexer)
        assert result is not None
        assert result.bridge_id == b.id
        assert result.trust_level == "STRONG"

    def test_route_composed_bridge(self) -> None:
        indexer = BridgeIndexer()
        b1 = _make_bridge(source="pack_a", target="pack_b", pattern="*", trust="STRONG")
        b2 = _make_bridge(source="pack_b", target="pack_c", pattern="*", trust="PARTIAL")
        index = indexer.build_index([b1, b2])
        router = FederationRouter()
        query = FederationQuery(
            source_judgment_id="j1",
            source_pack="pack_a",
            target_pack="pack_c",
            proposition="anything",
        )
        result = router.route(query, index, indexer)
        assert result is not None
        # Trust should be min(STRONG, PARTIAL) = PARTIAL
        assert result.trust_level == "PARTIAL"

    def test_apply_bridge_metadata(self) -> None:
        router = FederationRouter()
        b = _make_bridge()
        transported = router._apply_bridge(b, {"judgment_id": "j1"})
        assert "_transported_by" in transported
        assert transported["_transported_by"] == b.id
        assert "_transport_function" in transported
        assert "_source_pack" in transported
        assert "_target_pack" in transported

    def test_composition_trust(self) -> None:
        router = FederationRouter()
        b1 = _make_bridge(trust="FULL")
        b2 = _make_bridge(trust="WEAK")
        b3 = _make_bridge(trust="STRONG")
        trust = router._compute_composition_trust([b1, b2, b3])
        assert trust == "WEAK"

    def test_route_no_match(self) -> None:
        indexer = BridgeIndexer()
        index = indexer.build_index([])
        router = FederationRouter()
        query = FederationQuery(
            source_judgment_id="j1",
            source_pack="pack_x",
            target_pack="pack_y",
            proposition="anything",
        )
        result = router.route(query, index, indexer)
        assert result is None


# ===================================================================
# FederationCache tests
# ===================================================================


class TestFederationCache:
    def test_cache_miss(self) -> None:
        cache = FederationCache()
        result = cache.get("j1", "hash1")
        assert result is None

    def test_cache_hit(self) -> None:
        cache = FederationCache()
        fr = FederationResult(
            query_id="j1",
            bridge_id="b1",
            transported_judgment={"x": 1},
            trust_level="STRONG",
            cached=True,
        )
        cache.put(("j1", "hash1"), fr)
        result = cache.get("j1", "hash1")
        assert result is not None
        assert result.cached is True

    def test_invalidate_for_pack(self) -> None:
        cache = FederationCache()
        fr = FederationResult(
            query_id="j1",
            bridge_id="b1",
            transported_judgment={},
            trust_level="STRONG",
        )
        cache.put(("pack_a:j1", "hash1"), fr)
        cache.put(("pack_b:j2", "hash2"), fr)
        cache.invalidate_for_pack("pack_a")
        assert cache.get("pack_a:j1", "hash1") is None
        # pack_b entry should still be there
        assert cache.get("pack_b:j2", "hash2") is not None

    def test_statistics(self) -> None:
        cache = FederationCache()
        cache.get("j1", "h")
        stats = cache.statistics()
        assert stats.total_queries == 1
        assert stats.cache_hits == 0
