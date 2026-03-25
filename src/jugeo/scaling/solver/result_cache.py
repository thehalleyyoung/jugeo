"""Persistent result cache for the solver scaling layer.

:class:`SolverResultCache` stores :class:`~jugeo.scaling.solver.models.SolverResult`
objects on disk (as JSON) keyed by content hash so that results survive process
restarts.  It also supports:

* LRU eviction when the entry count exceeds *max_entries*,
* invalidation by coordinate id (for incremental re-verification after code
  changes),
* pruning of entries older than a configurable number of days, and
* reporting the on-disk size of the cache directory.
"""

from __future__ import annotations

import json
import os
import time
from collections import OrderedDict
from typing import Any, Optional

from jugeo.scaling.solver.models import SolverResult


# ---------------------------------------------------------------------------
# Cache implementation
# ---------------------------------------------------------------------------

class SolverResultCache:
    """Two-level result cache: in-memory LRU backed by a JSON file on disk."""

    # Name of the single JSON file inside *cache_dir* that stores all entries.
    _INDEX_FILE = "solver_cache.json"

    def __init__(
        self,
        cache_dir: str = ".jugeo_solver_cache",
        max_entries: int = 100_000,
    ) -> None:
        self._cache_dir = cache_dir
        self._max_entries = max_entries

        # In-memory store: content_hash → (SolverResult, stored_at)
        self._store: OrderedDict[str, tuple[SolverResult, float]] = OrderedDict()
        # Reverse index: coordinate_id → set of content hashes
        self._coord_index: dict[str, set[str]] = {}

        self._hits = 0
        self._misses = 0
        self._stores = 0
        self._evictions = 0
        self._invalidations = 0

        # Lazy: only create the dir when we actually read/write to disk.

    # ---------------------------------------------------------------------------
    # Core cache operations
    # ---------------------------------------------------------------------------

    def get(
        self,
        content_hash: str,
        solver_version: Optional[str] = None,
    ) -> Optional[SolverResult]:
        """Return the cached result for *content_hash*, or None if absent.

        If *solver_version* is given the cached result is only returned when
        its ``solver_version`` field matches exactly.
        """
        if content_hash not in self._store:
            self._misses += 1
            return None
        result, _ = self._store[content_hash]
        if solver_version is not None and result.solver_version != solver_version:
            self._misses += 1
            return None
        self._store.move_to_end(content_hash)
        self._hits += 1
        return result

    def put(self, content_hash: str, result: SolverResult) -> None:
        """Store *result* under *content_hash*.

        Also registers a reverse mapping from the result's coordinate id (if
        any; stored in ``result.model`` under the key ``"coordinate_id"`` if
        present, or inferred from context) so that :meth:`invalidate` can
        quickly find related entries.
        """
        self._store[content_hash] = (result, time.time())
        self._store.move_to_end(content_hash)
        self._stores += 1

        # Maintain coordinate reverse index using a hint stored in the model.
        coord_id: Optional[str] = None
        if result.model and isinstance(result.model, dict):
            coord_id = result.model.get("coordinate_id")
        if coord_id:
            self._coord_index.setdefault(coord_id, set()).add(content_hash)

        if len(self._store) > self._max_entries:
            self._evict_lru(len(self._store) - self._max_entries)

    def invalidate(self, coordinate_ids: list[str]) -> int:
        """Remove all entries associated with any of *coordinate_ids*.

        Returns the number of entries removed.
        """
        removed = 0
        for coord_id in coordinate_ids:
            hashes = self._coord_index.pop(coord_id, set())
            for h in hashes:
                if h in self._store:
                    del self._store[h]
                    removed += 1
        self._invalidations += removed
        return removed

    def invalidate_all(self) -> None:
        """Remove every cached entry."""
        self._store.clear()
        self._coord_index.clear()
        self._invalidations += 1

    # ---------------------------------------------------------------------------
    # Persistence
    # ---------------------------------------------------------------------------

    def save_to_disk(self) -> None:
        """Serialise the in-memory cache to ``<cache_dir>/solver_cache.json``."""
        self._ensure_cache_dir()
        path = self._index_path()
        entries: list[dict[str, Any]] = []
        for h, (result, stored_at) in self._store.items():
            entries.append({
                "content_hash": h,
                "result": result.to_dict(),
                "stored_at": stored_at,
            })
        payload = {
            "max_entries": self._max_entries,
            "hits": self._hits,
            "misses": self._misses,
            "stores": self._stores,
            "evictions": self._evictions,
            "invalidations": self._invalidations,
            "entries": entries,
            "coord_index": {k: list(v) for k, v in self._coord_index.items()},
        }
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)

    def load_from_disk(self) -> None:
        """Load the cache from ``<cache_dir>/solver_cache.json`` if it exists."""
        path = self._index_path()
        if not os.path.isfile(path):
            return
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)

        self._max_entries = int(payload.get("max_entries", self._max_entries))
        self._hits = int(payload.get("hits", 0))
        self._misses = int(payload.get("misses", 0))
        self._stores = int(payload.get("stores", 0))
        self._evictions = int(payload.get("evictions", 0))
        self._invalidations = int(payload.get("invalidations", 0))

        self._store.clear()
        for entry in payload.get("entries", []):
            h = entry["content_hash"]
            result = SolverResult.from_dict(entry["result"])
            stored_at = float(entry.get("stored_at", 0.0))
            self._store[h] = (result, stored_at)

        self._coord_index.clear()
        for coord_id, hashes in payload.get("coord_index", {}).items():
            self._coord_index[coord_id] = set(hashes)

    # ---------------------------------------------------------------------------
    # Maintenance
    # ---------------------------------------------------------------------------

    def prune_stale(self, max_age_days: float = 30.0) -> int:
        """Remove entries older than *max_age_days* days.

        Returns the number of entries removed.
        """
        cutoff = time.time() - max_age_days * 86_400
        stale = [h for h, (_, stored_at) in self._store.items() if stored_at < cutoff]
        for h in stale:
            del self._store[h]
            self._evictions += 1
        # Clean up reverse index
        for coord_id in list(self._coord_index):
            self._coord_index[coord_id] -= set(stale)
            if not self._coord_index[coord_id]:
                del self._coord_index[coord_id]
        return len(stale)

    def _evict_lru(self, count: int) -> None:
        """Evict *count* least-recently-used entries."""
        for _ in range(min(count, len(self._store))):
            h, _ = self._store.popitem(last=False)
            self._evictions += 1
            # Clean up reverse index
            for coord_id in list(self._coord_index):
                self._coord_index[coord_id].discard(h)

    def size_on_disk(self) -> int:
        """Return the total size in bytes of all files inside the cache dir."""
        total = 0
        if not os.path.isdir(self._cache_dir):
            return 0
        for entry in os.scandir(self._cache_dir):
            if entry.is_file():
                total += entry.stat().st_size
        return total

    # ---------------------------------------------------------------------------
    # Statistics
    # ---------------------------------------------------------------------------

    def statistics(self) -> dict[str, Any]:
        total = self._hits + self._misses
        return {
            "entries": len(self._store),
            "max_entries": self._max_entries,
            "hits": self._hits,
            "misses": self._misses,
            "stores": self._stores,
            "evictions": self._evictions,
            "invalidations": self._invalidations,
            "hit_rate": self._hits / total if total else 0.0,
            "cache_dir": self._cache_dir,
            "size_on_disk_bytes": self.size_on_disk(),
        }

    # ---------------------------------------------------------------------------
    # Helpers
    # ---------------------------------------------------------------------------

    def __len__(self) -> int:
        return len(self._store)

    def _ensure_cache_dir(self) -> None:
        os.makedirs(self._cache_dir, exist_ok=True)

    def _index_path(self) -> str:
        return os.path.join(self._cache_dir, self._INDEX_FILE)
