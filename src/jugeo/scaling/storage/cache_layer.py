"""LRU cache layer wrapping any :class:`~jugeo.scaling.storage.store.Store`.

:class:`CachedStore` is a transparent write-through cache.  All reads first
check an in-memory LRU cache before falling through to the backing store.
Writes always propagate to the backing store and invalidate the corresponding
cache entries.

Each entity type has its own independent LRU cache so that a burst of evidence
inserts cannot evict coordinate entries.

Cache entries can be *pinned* (never evicted on size overflow) by calling
:meth:`CachedStore.pin`.

Cache statistics are tracked per entity type and are accessible via
:attr:`CachedStore.stats`.

Usage example::

    from jugeo.scaling.storage.sqlite_backend import SQLiteBackend
    from jugeo.scaling.storage.cache_layer import CachedStore

    backend = SQLiteBackend("state.db")
    store = CachedStore(backend, coordinate_cache_size=2048)
    coord = store.put_coordinate(...)   # writes through to SQLite
    coord2 = store.get_coordinate(coord.id)  # cache hit
    print(store.stats["coordinates"])
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Generic, Iterable, Optional, TypeVar

from jugeo.scaling.storage.models import (
    StoredCertificate,
    StoredCoordinate,
    StoredEvidence,
    StoredJudgment,
    StoredMorphism,
    StoredObligation,
    StoredObstruction,
    StoredTreaty,
)
from jugeo.scaling.storage.store import Store


# ---------------------------------------------------------------------------
# Generic LRU cache
# ---------------------------------------------------------------------------

_V = TypeVar("_V")


@dataclass
class CacheStats:
    """Hit/miss/eviction counters for a single cache."""

    hits: int = 0
    misses: int = 0
    evictions: int = 0
    invalidations: int = 0
    pins: int = 0

    @property
    def total_requests(self) -> int:
        return self.hits + self.misses

    @property
    def hit_rate(self) -> float:
        if self.total_requests == 0:
            return 0.0
        return self.hits / self.total_requests

    def to_dict(self) -> dict[str, Any]:
        return {
            "hits": self.hits,
            "misses": self.misses,
            "evictions": self.evictions,
            "invalidations": self.invalidations,
            "pins": self.pins,
            "total_requests": self.total_requests,
            "hit_rate": round(self.hit_rate, 4),
        }


class LRUCache(Generic[_V]):
    """Thread-safe LRU cache backed by :class:`~collections.OrderedDict`.

    Parameters
    ----------
    max_size:
        Maximum number of un-pinned entries to keep.  Pinned entries are
        stored separately and do not count toward *max_size*.
    """

    def __init__(self, max_size: int = 512) -> None:
        self._max_size = max_size
        self._data: OrderedDict[str, _V] = OrderedDict()
        self._pinned: dict[str, _V] = {}
        self._lock = threading.Lock()
        self.stats = CacheStats()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, key: str) -> Optional[_V]:
        with self._lock:
            # Check pinned first (always hits)
            if key in self._pinned:
                self.stats.hits += 1
                return self._pinned[key]
            if key in self._data:
                self._data.move_to_end(key)
                self.stats.hits += 1
                return self._data[key]
            self.stats.misses += 1
            return None

    def put(self, key: str, value: _V) -> None:
        with self._lock:
            if key in self._pinned:
                self._pinned[key] = value
                return
            if key in self._data:
                self._data.move_to_end(key)
                self._data[key] = value
                return
            self._data[key] = value
            self._data.move_to_end(key)
            self._evict_if_needed()

    def invalidate(self, key: str) -> None:
        with self._lock:
            if key in self._data:
                del self._data[key]
                self.stats.invalidations += 1
            if key in self._pinned:
                del self._pinned[key]
                self.stats.invalidations += 1

    def pin(self, key: str, value: _V) -> None:
        """Store *value* under *key* in the pinned tier (never evicted)."""
        with self._lock:
            # Remove from regular LRU if present
            self._data.pop(key, None)
            self._pinned[key] = value
            self.stats.pins += 1

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
            self._pinned.clear()
            self.stats = CacheStats()

    def warm(self, items: Iterable[tuple[str, _V]]) -> int:
        """Pre-load *items* into the cache.  Returns count loaded."""
        count = 0
        for key, value in items:
            self.put(key, value)
            count += 1
        return count

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._data) + len(self._pinned)

    @property
    def max_size(self) -> int:
        return self._max_size

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _evict_if_needed(self) -> None:
        """Evict the LRU entry if the cache exceeds *max_size*."""
        while len(self._data) > self._max_size:
            self._data.popitem(last=False)
            self.stats.evictions += 1


# ---------------------------------------------------------------------------
# CachedStore
# ---------------------------------------------------------------------------

class CachedStore(Store):
    """Write-through LRU cache wrapping a backing :class:`Store`.

    Parameters
    ----------
    backing:
        The concrete store to delegate cache misses and writes to.
    coordinate_cache_size:
        Maximum coordinate entries in cache.
    morphism_cache_size:
        Maximum morphism entries in cache.
    judgment_cache_size:
        Maximum judgment entries in cache.
    evidence_cache_size:
        Maximum evidence entries in cache.
    obligation_cache_size:
        Maximum obligation entries in cache.
    obstruction_cache_size:
        Maximum obstruction entries in cache.
    treaty_cache_size:
        Maximum treaty entries in cache.
    certificate_cache_size:
        Maximum certificate entries in cache.
    """

    def __init__(
        self,
        backing: Store,
        *,
        coordinate_cache_size: int = 1024,
        morphism_cache_size: int = 512,
        judgment_cache_size: int = 512,
        evidence_cache_size: int = 2048,
        obligation_cache_size: int = 256,
        obstruction_cache_size: int = 256,
        treaty_cache_size: int = 128,
        certificate_cache_size: int = 256,
    ) -> None:
        self._backing = backing
        self._caches: dict[str, LRUCache[Any]] = {
            "coordinates": LRUCache(coordinate_cache_size),
            "morphisms": LRUCache(morphism_cache_size),
            "judgments": LRUCache(judgment_cache_size),
            "evidence": LRUCache(evidence_cache_size),
            "obligations": LRUCache(obligation_cache_size),
            "obstructions": LRUCache(obstruction_cache_size),
            "treaties": LRUCache(treaty_cache_size),
            "certificates": LRUCache(certificate_cache_size),
        }

    # ------------------------------------------------------------------
    # Cache access helpers
    # ------------------------------------------------------------------

    @property
    def stats(self) -> dict[str, dict[str, Any]]:
        """Per-entity-type cache statistics."""
        return {name: cache.stats.to_dict() for name, cache in self._caches.items()}

    def cache_sizes(self) -> dict[str, int]:
        """Current number of entries per cache."""
        return {name: cache.size for name, cache in self._caches.items()}

    def clear_cache(self, entity_type: str | None = None) -> None:
        """Clear one or all caches."""
        if entity_type is not None:
            self._caches[entity_type].clear()
        else:
            for cache in self._caches.values():
                cache.clear()

    def pin(self, entity_type: str, key: str, value: Any) -> None:
        """Pin *value* under *key* in the *entity_type* cache."""
        self._caches[entity_type].pin(key, value)

    # ------------------------------------------------------------------
    # Warm-up
    # ------------------------------------------------------------------

    def warm_coordinates(self, limit: int = 500) -> int:
        """Pre-load the most recently created coordinates into the cache."""
        coords = self._backing.query_coordinates(limit=limit)
        return self._caches["coordinates"].warm((c.id, c) for c in coords)

    def warm_judgments(self, limit: int = 200) -> int:
        """Pre-load open judgments into the cache."""
        judgments = self._backing.query_judgments(status="open", limit=limit)
        return self._caches["judgments"].warm((j.id, j) for j in judgments)

    def warm_obligations(self, limit: int = 200) -> int:
        """Pre-load pending obligations into the cache."""
        obls = self._backing.pending_obligations()[:limit]
        return self._caches["obligations"].warm((o.id, o) for o in obls)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def initialize(self) -> None:
        self._backing.initialize()

    def close(self) -> None:
        self.clear_cache()
        self._backing.close()

    def is_healthy(self) -> bool:
        return self._backing.is_healthy()

    # ------------------------------------------------------------------
    # Coordinates
    # ------------------------------------------------------------------

    def put_coordinate(self, coord: StoredCoordinate) -> StoredCoordinate:
        result = self._backing.put_coordinate(coord)
        self._caches["coordinates"].put(result.id, result)
        return result

    def get_coordinate(self, coord_id: str) -> Optional[StoredCoordinate]:
        cached = self._caches["coordinates"].get(coord_id)
        if cached is not None:
            return cached
        result = self._backing.get_coordinate(coord_id)
        if result is not None:
            self._caches["coordinates"].put(coord_id, result)
        return result

    def query_coordinates(self, **kwargs: Any) -> list[StoredCoordinate]:
        return self._backing.query_coordinates(**kwargs)

    def count_coordinates(self, **kwargs: Any) -> int:
        return self._backing.count_coordinates(**kwargs)

    def delete_coordinate(self, coord_id: str) -> bool:
        self._caches["coordinates"].invalidate(coord_id)
        return self._backing.delete_coordinate(coord_id)

    def bulk_put_coordinates(self, coords: Iterable[StoredCoordinate]) -> list[StoredCoordinate]:
        results = self._backing.bulk_put_coordinates(coords)
        for c in results:
            self._caches["coordinates"].put(c.id, c)
        return results

    # ------------------------------------------------------------------
    # Morphisms
    # ------------------------------------------------------------------

    def put_morphism(self, morphism: StoredMorphism) -> StoredMorphism:
        result = self._backing.put_morphism(morphism)
        self._caches["morphisms"].put(result.id, result)
        return result

    def get_morphism(self, morphism_id: str) -> Optional[StoredMorphism]:
        cached = self._caches["morphisms"].get(morphism_id)
        if cached is not None:
            return cached
        result = self._backing.get_morphism(morphism_id)
        if result is not None:
            self._caches["morphisms"].put(morphism_id, result)
        return result

    def query_morphisms(self, **kwargs: Any) -> list[StoredMorphism]:
        return self._backing.query_morphisms(**kwargs)

    def morphisms_from(self, coord_id: str) -> list[StoredMorphism]:
        return self._backing.morphisms_from(coord_id)

    def morphisms_to(self, coord_id: str) -> list[StoredMorphism]:
        return self._backing.morphisms_to(coord_id)

    def count_morphisms(self, **kwargs: Any) -> int:
        return self._backing.count_morphisms(**kwargs)

    def bulk_put_morphisms(self, morphisms: Iterable[StoredMorphism]) -> list[StoredMorphism]:
        results = self._backing.bulk_put_morphisms(morphisms)
        for m in results:
            self._caches["morphisms"].put(m.id, m)
        return results

    # ------------------------------------------------------------------
    # Judgments
    # ------------------------------------------------------------------

    def put_judgment(self, judgment: StoredJudgment) -> StoredJudgment:
        result = self._backing.put_judgment(judgment)
        self._caches["judgments"].put(result.id, result)
        return result

    def get_judgment(self, judgment_id: str) -> Optional[StoredJudgment]:
        cached = self._caches["judgments"].get(judgment_id)
        if cached is not None:
            return cached
        result = self._backing.get_judgment(judgment_id)
        if result is not None:
            self._caches["judgments"].put(judgment_id, result)
        return result

    def query_judgments(self, **kwargs: Any) -> list[StoredJudgment]:
        return self._backing.query_judgments(**kwargs)

    def count_judgments(self, **kwargs: Any) -> int:
        return self._backing.count_judgments(**kwargs)

    # ------------------------------------------------------------------
    # Evidence
    # ------------------------------------------------------------------

    def put_evidence(self, evidence: StoredEvidence) -> StoredEvidence:
        result = self._backing.put_evidence(evidence)
        self._caches["evidence"].put(result.id, result)
        return result

    def get_evidence(self, evidence_id: str) -> Optional[StoredEvidence]:
        cached = self._caches["evidence"].get(evidence_id)
        if cached is not None:
            return cached
        result = self._backing.get_evidence(evidence_id)
        if result is not None:
            self._caches["evidence"].put(evidence_id, result)
        return result

    def query_evidence(self, **kwargs: Any) -> list[StoredEvidence]:
        return self._backing.query_evidence(**kwargs)

    def count_evidence(self, **kwargs: Any) -> int:
        return self._backing.count_evidence(**kwargs)

    def bulk_put_evidence(self, records: Iterable[StoredEvidence]) -> list[StoredEvidence]:
        results = self._backing.bulk_put_evidence(records)
        for r in results:
            self._caches["evidence"].put(r.id, r)
        return results

    # ------------------------------------------------------------------
    # Obligations
    # ------------------------------------------------------------------

    def put_obligation(self, obligation: StoredObligation) -> StoredObligation:
        result = self._backing.put_obligation(obligation)
        self._caches["obligations"].put(result.id, result)
        return result

    def get_obligation(self, obligation_id: str) -> Optional[StoredObligation]:
        cached = self._caches["obligations"].get(obligation_id)
        if cached is not None:
            return cached
        result = self._backing.get_obligation(obligation_id)
        if result is not None:
            self._caches["obligations"].put(obligation_id, result)
        return result

    def query_obligations(self, **kwargs: Any) -> list[StoredObligation]:
        return self._backing.query_obligations(**kwargs)

    def pending_obligations(self) -> list[StoredObligation]:
        return self._backing.pending_obligations()

    def overdue_obligations(self, now: float | None = None) -> list[StoredObligation]:
        return self._backing.overdue_obligations(now=now)

    def count_obligations(self, **kwargs: Any) -> int:
        return self._backing.count_obligations(**kwargs)

    # ------------------------------------------------------------------
    # Obstructions
    # ------------------------------------------------------------------

    def put_obstruction(self, obstruction: StoredObstruction) -> StoredObstruction:
        result = self._backing.put_obstruction(obstruction)
        self._caches["obstructions"].put(result.id, result)
        return result

    def get_obstruction(self, obstruction_id: str) -> Optional[StoredObstruction]:
        cached = self._caches["obstructions"].get(obstruction_id)
        if cached is not None:
            return cached
        result = self._backing.get_obstruction(obstruction_id)
        if result is not None:
            self._caches["obstructions"].put(obstruction_id, result)
        return result

    def query_obstructions(self, **kwargs: Any) -> list[StoredObstruction]:
        return self._backing.query_obstructions(**kwargs)

    def active_obstructions(self) -> list[StoredObstruction]:
        return self._backing.active_obstructions()

    def count_obstructions(self, **kwargs: Any) -> int:
        return self._backing.count_obstructions(**kwargs)

    # ------------------------------------------------------------------
    # Treaties
    # ------------------------------------------------------------------

    def put_treaty(self, treaty: StoredTreaty) -> StoredTreaty:
        result = self._backing.put_treaty(treaty)
        self._caches["treaties"].put(result.id, result)
        return result

    def get_treaty(self, treaty_id: str) -> Optional[StoredTreaty]:
        cached = self._caches["treaties"].get(treaty_id)
        if cached is not None:
            return cached
        result = self._backing.get_treaty(treaty_id)
        if result is not None:
            self._caches["treaties"].put(treaty_id, result)
        return result

    def query_treaties(self, **kwargs: Any) -> list[StoredTreaty]:
        return self._backing.query_treaties(**kwargs)

    def count_treaties(self, **kwargs: Any) -> int:
        return self._backing.count_treaties(**kwargs)

    # ------------------------------------------------------------------
    # Certificates
    # ------------------------------------------------------------------

    def put_certificate(self, certificate: StoredCertificate) -> StoredCertificate:
        result = self._backing.put_certificate(certificate)
        self._caches["certificates"].put(result.id, result)
        return result

    def get_certificate(self, cert_id: str) -> Optional[StoredCertificate]:
        cached = self._caches["certificates"].get(cert_id)
        if cached is not None:
            return cached
        result = self._backing.get_certificate(cert_id)
        if result is not None:
            self._caches["certificates"].put(cert_id, result)
        return result

    def query_certificates(self, **kwargs: Any) -> list[StoredCertificate]:
        return self._backing.query_certificates(**kwargs)

    def count_certificates(self, **kwargs: Any) -> int:
        return self._backing.count_certificates(**kwargs)

    # ------------------------------------------------------------------
    # Transactions (delegated)
    # ------------------------------------------------------------------

    def begin_transaction(self) -> None:
        self._backing.begin_transaction()

    def commit(self) -> None:
        self._backing.commit()

    def rollback(self) -> None:
        self._backing.rollback()

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    def table_statistics(self) -> dict[str, Any]:
        stats = self._backing.table_statistics()
        stats["_cache"] = self.stats
        return stats

    def storage_size(self) -> int:
        return self._backing.storage_size()


__all__ = ["CachedStore", "LRUCache", "CacheStats"]
