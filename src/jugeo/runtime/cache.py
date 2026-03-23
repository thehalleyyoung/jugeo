"""Semantic cache infrastructure for JuGeo runtime.

The cache in JuGeo is intentionally *semantic* rather than merely structural.
Entries are keyed by namespace, coordinate, proposition identity, trust floor,
and support hash so that expensive verification work can be reused only when
the mathematical context still matches. Derived sections, solver results,
overlap checks, treaty validations, provenance queries, and copilot-facing
summaries all flow through the same cache surface.

Two concepts dominate the design:

* **Support awareness** — cached work is valid only on the coordinates that
  supported it when it was produced. If any supporting coordinate changes, the
  cached result must become stale.
* **Epoch awareness** — every coordinate carries an epoch that advances when
  the underlying coordinate or region is mutated or invalidated. Cached entries
  are accepted only when their recorded epoch still matches the active epoch.

The module purposely contains richer logic than the original lightweight cache.
It remains backward compatible with the legacy ``CacheEntry('k', value,
support, trust, provenance)`` pattern used by older tests while exposing a
full-featured cache policy, invalidator, diagnostics, warmer, statistics, and
serialization surface for theory2-driven workflows.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Iterable, Mapping, MutableMapping, Sequence

from jugeo.evidence.provenance import ProvenanceTrace
from jugeo.evidence.trust import TrustProfile
from jugeo.geometry.supports import SupportRegion


JsonDict = dict[str, Any]
CacheBuilder = Callable[["CacheKey"], Any]


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _now() -> float:
    """Return the current wall-clock time as a float.

    The cache uses wall-clock seconds rather than monotonic time because the
    values are serialized to JSON and are intended to be human-inspectable.
    """

    return time.time()


def _normalize_text(value: object, *, fallback: str = "") -> str:
    """Coerce *value* to a stripped string.

    ``fallback`` is used when the value normalizes to an empty string. The
    function intentionally keeps normalization small and predictable so that
    cache keys remain stable across serialization round-trips.
    """

    text = str(value).strip()
    return text or fallback


def _normalize_dependencies(dependencies: Iterable[str] | None) -> tuple[str, ...]:
    """Return a deterministic, duplicate-free dependency tuple."""

    if not dependencies:
        return ()
    normalized = {
        _normalize_text(dependency)
        for dependency in dependencies
        if _normalize_text(dependency)
    }
    return tuple(sorted(normalized))


def _support_tokens(scope: Iterable[str] | SupportRegion | None) -> tuple[str, ...]:
    """Extract a stable support token tuple from ``scope``.

    ``SupportRegion`` values contribute the coordinate key, patch keys, and
    labels so that localized invalidation can be more precise than a single
    region identifier.
    """

    if scope is None:
        return ()
    if isinstance(scope, SupportRegion):
        tokens: set[str] = {scope.coordinate.key}
        tokens.update(scope.patch_keys)
        tokens.update(scope.labels)
        return tuple(sorted(token for token in tokens if token))
    normalized = {
        _normalize_text(token)
        for token in scope
        if _normalize_text(token)
    }
    return tuple(sorted(normalized))


def _hash_support_scope(scope: Iterable[str] | SupportRegion | None) -> str:
    """Return a deterministic digest for a support scope.

    The support hash is stored inside :class:`CacheKey` so callers can compare
    the current support footprint with the support footprint recorded when the
    entry was created.
    """

    digest = hashlib.sha256()
    for token in _support_tokens(scope):
        digest.update(token.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def _hash_payload(value: object) -> str:
    """Hash an arbitrary value using a JSON-or-repr fallback strategy."""

    try:
        payload = json.dumps(value, sort_keys=True, default=str)
    except TypeError:
        payload = repr(value)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _legacy_proposition_hash(raw_key: str) -> str:
    """Return the proposition hash marker used for legacy cache entries."""

    return f"legacy::{raw_key}"


def _display_key(cache_key: "CacheKey") -> str:
    """Return the external label used for diagnostics and compatibility.

    Older tests and helper modules historically identified entries by the raw
    string key rather than by the structured key identity. To preserve that
    behavior, legacy keys are displayed using only the coordinate text.
    """

    if (
        cache_key.namespace == "legacy"
        and cache_key.proposition_hash == _legacy_proposition_hash(cache_key.coordinate)
    ):
        return cache_key.coordinate
    return cache_key.identity()


# ---------------------------------------------------------------------------
# Cache key and entry types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CacheKey:
    """Stable identity for semantically cached work.

    Parameters are intentionally explicit:

    ``namespace``
        High-level category such as ``sections``, ``solver``, ``treaties``, or
        ``provenance``.
    ``coordinate``
        The coordinate or region key the work is centered on.
    ``proposition_hash``
        Stable fingerprint of the proposition/query/result identity.
    ``epoch``
        Coordinate epoch captured when the work was produced.
    ``support_hash``
        Digest of the support scope that justified the computation.
    ``trust_floor``
        Small integer describing the minimum trust tier required to reuse the
        result.
    """

    namespace: str
    coordinate: str
    proposition_hash: str
    epoch: int
    support_hash: str
    trust_floor: int

    def __post_init__(self) -> None:
        """Normalize the key into a serialization-friendly form."""

        object.__setattr__(self, "namespace", _normalize_text(self.namespace, fallback="default"))
        object.__setattr__(self, "coordinate", _normalize_text(self.coordinate, fallback="global"))
        object.__setattr__(self, "proposition_hash", _normalize_text(self.proposition_hash, fallback="unknown"))
        object.__setattr__(self, "epoch", max(0, int(self.epoch)))
        object.__setattr__(self, "support_hash", _normalize_text(self.support_hash, fallback=_hash_support_scope(())))
        object.__setattr__(self, "trust_floor", max(0, int(self.trust_floor)))

    def identity(self) -> str:
        """Return a compact textual identity suitable for dictionaries."""

        return (
            f"{self.namespace}|{self.coordinate}|{self.proposition_hash}|"
            f"e{self.epoch}|s{self.support_hash[:16]}|t{self.trust_floor}"
        )

    def matches_namespace(self, namespace: str) -> bool:
        """Return whether this key belongs to ``namespace``."""

        return self.namespace == _normalize_text(namespace, fallback="default")

    def with_epoch(self, epoch: int) -> "CacheKey":
        """Return a copy that targets a newer epoch."""

        return CacheKey(
            namespace=self.namespace,
            coordinate=self.coordinate,
            proposition_hash=self.proposition_hash,
            epoch=epoch,
            support_hash=self.support_hash,
            trust_floor=self.trust_floor,
        )

    def with_support_hash(self, support_scope: Iterable[str] | SupportRegion) -> "CacheKey":
        """Return a copy whose support hash reflects ``support_scope``."""

        return CacheKey(
            namespace=self.namespace,
            coordinate=self.coordinate,
            proposition_hash=self.proposition_hash,
            epoch=self.epoch,
            support_hash=_hash_support_scope(support_scope),
            trust_floor=self.trust_floor,
        )

    def tighten_trust(self, trust_floor: int) -> "CacheKey":
        """Return a key with a stronger trust requirement."""

        return CacheKey(
            namespace=self.namespace,
            coordinate=self.coordinate,
            proposition_hash=self.proposition_hash,
            epoch=self.epoch,
            support_hash=self.support_hash,
            trust_floor=max(self.trust_floor, int(trust_floor)),
        )

    def to_dict(self) -> JsonDict:
        """Serialize the key to a JSON-ready dictionary."""

        return {
            "namespace": self.namespace,
            "coordinate": self.coordinate,
            "proposition_hash": self.proposition_hash,
            "epoch": self.epoch,
            "support_hash": self.support_hash,
            "trust_floor": self.trust_floor,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "CacheKey":
        """Deserialize a :class:`CacheKey` from ``payload``."""

        return cls(
            namespace=str(payload.get("namespace", "default")),
            coordinate=str(payload.get("coordinate", "global")),
            proposition_hash=str(payload.get("proposition_hash", "unknown")),
            epoch=int(payload.get("epoch", 0)),
            support_hash=str(payload.get("support_hash", _hash_support_scope(()))),
            trust_floor=int(payload.get("trust_floor", 0)),
        )


@dataclass(slots=True, init=False)
class CacheEntry:
    """Mutable cache entry with access metadata and staleness logic.

    The dataclass fields are intentionally small and focused on cache behavior.
    ``value`` may be any serializable or in-memory object.

    Backward compatibility note: older JuGeo code constructed entries with the
    signature ``CacheEntry('k', value, support, trust, provenance)``. This
    class still accepts that form and converts it into a structured
    :class:`CacheKey` plus a support scope and dependency set.
    """

    key: CacheKey
    value: Any
    created_at: float
    last_accessed: float
    hit_count: int
    support_scope: tuple[str, ...]
    dependencies: tuple[str, ...]

    def __init__(
        self,
        key: CacheKey | str,
        value: Any,
        created_at: float | SupportRegion | None = None,
        last_accessed: float | TrustProfile | None = None,
        hit_count: int | ProvenanceTrace = 0,
        support_scope: Iterable[str] | SupportRegion | None = None,
        dependencies: Iterable[str] | None = None,
    ) -> None:
        """Initialize a cache entry from either modern or legacy arguments."""

        timestamp = _now()
        if isinstance(key, CacheKey):
            actual_key = key
            actual_created = timestamp if created_at is None else float(created_at)
            actual_accessed = actual_created if last_accessed is None else float(last_accessed)
            actual_hits = int(hit_count)
            actual_scope = _support_tokens(support_scope)
            actual_dependencies = _normalize_dependencies(dependencies)
        else:
            raw_key = _normalize_text(key, fallback="legacy")
            legacy_support = created_at if isinstance(created_at, SupportRegion) else None
            legacy_trust = last_accessed if isinstance(last_accessed, TrustProfile) else None
            legacy_trace = hit_count if isinstance(hit_count, ProvenanceTrace) else None
            actual_scope = _support_tokens(legacy_support)
            dependency_seed: list[str] = list(actual_scope)
            if legacy_trace is not None:
                dependency_seed.append(legacy_trace.origin)
                dependency_seed.extend(step.coordinate for step in legacy_trace.steps)
            actual_dependencies = _normalize_dependencies(dependency_seed)
            actual_key = CacheKey(
                namespace="legacy",
                coordinate=raw_key,
                proposition_hash=_legacy_proposition_hash(raw_key),
                epoch=0,
                support_hash=_hash_support_scope(actual_scope),
                trust_floor=int(legacy_trust.tier) if legacy_trust else 0,
            )
            actual_created = timestamp
            actual_accessed = timestamp
            actual_hits = 0

        self.key = actual_key
        self.value = value
        self.created_at = actual_created
        self.last_accessed = actual_accessed
        self.hit_count = max(0, actual_hits)
        self.support_scope = actual_scope
        self.dependencies = actual_dependencies

    def is_stale(
        self,
        *,
        now: float | None = None,
        ttl: float | None = None,
        current_epoch: int | None = None,
        current_support_hash: str | None = None,
        invalid_dependencies: Iterable[str] = (),
    ) -> bool:
        """Return whether the entry should be treated as stale.

        Staleness is triggered by any of the following:

        * TTL expiration.
        * Epoch mismatch for the focused coordinate.
        * Support hash mismatch for the full support footprint.
        * Any dependency appearing in the invalid dependency set.
        """

        timestamp = _now() if now is None else float(now)
        if ttl is not None and ttl >= 0 and timestamp - self.created_at > ttl:
            return True
        if current_epoch is not None and self.key.epoch != int(current_epoch):
            return True
        if current_support_hash is not None and self.key.support_hash != current_support_hash:
            return True
        invalid = set(_normalize_dependencies(invalid_dependencies))
        return any(dependency in invalid for dependency in self.dependencies)

    def touch(self, *, when: float | None = None) -> "CacheEntry":
        """Update access metadata in place and return ``self`` for chaining."""

        self.last_accessed = _now() if when is None else float(when)
        self.hit_count += 1
        return self

    def age_seconds(self, *, now: float | None = None) -> float:
        """Return how many seconds have elapsed since creation."""

        timestamp = _now() if now is None else float(now)
        return max(0.0, timestamp - self.created_at)

    def depends_on(self, dependency: str) -> bool:
        """Return whether the entry explicitly names ``dependency``."""

        return _normalize_text(dependency) in self.dependencies

    def overlaps_support(self, scope: Iterable[str] | SupportRegion) -> bool:
        """Return whether the entry overlaps the supplied support scope."""

        return bool(set(self.support_scope) & set(_support_tokens(scope)))

    def support_hash_matches(self, scope: Iterable[str] | SupportRegion) -> bool:
        """Return whether ``scope`` hashes to the stored support hash."""

        return self.key.support_hash == _hash_support_scope(scope)

    def to_dict(self) -> JsonDict:
        """Serialize the entry to a JSON-ready dictionary."""

        return {
            "key": self.key.to_dict(),
            "value": self.value,
            "created_at": self.created_at,
            "last_accessed": self.last_accessed,
            "hit_count": self.hit_count,
            "support_scope": list(self.support_scope),
            "dependencies": list(self.dependencies),
        }


# ---------------------------------------------------------------------------
# Policy, strategy, indexing, and statistics
# ---------------------------------------------------------------------------


class EvictionStrategy(str, Enum):
    """Policy used when the semantic cache must discard entries.

    ``TTL`` primarily prioritizes the oldest entries; ``COST_AWARE`` tries to
    retain expensive/high-value entries by preferring victims with low hit
    counts, large support scopes, and many dependencies.
    """

    LRU = "lru"
    LFU = "lfu"
    TTL = "ttl"
    COST_AWARE = "cost-aware"

    def describe(self) -> str:
        """Return a human-readable description of this strategy."""

        descriptions = {
            EvictionStrategy.LRU: "least recently used entry first",
            EvictionStrategy.LFU: "least frequently used entry first",
            EvictionStrategy.TTL: "oldest entry first",
            EvictionStrategy.COST_AWARE: "balance recency, frequency, and support cost",
        }
        return descriptions[self]

    def cost_score(self, entry: CacheEntry, *, now: float | None = None) -> float:
        """Return a sortable score for ``entry``.

        Lower scores are evicted first. Scores are strategy-specific and are
        intentionally simple so they remain predictable in diagnostics.
        """

        timestamp = _now() if now is None else float(now)
        if self is EvictionStrategy.LRU:
            return entry.last_accessed
        if self is EvictionStrategy.LFU:
            return float(entry.hit_count)
        if self is EvictionStrategy.TTL:
            return entry.created_at
        age = max(1.0, timestamp - entry.last_accessed)
        support_cost = max(1, len(entry.support_scope))
        dependency_cost = max(1, len(entry.dependencies))
        benefit = 1.0 + entry.hit_count
        return benefit / (age * support_cost * dependency_cost)

    def rank_entries(
        self,
        entries: Iterable[CacheEntry],
        *,
        now: float | None = None,
    ) -> list[CacheEntry]:
        """Return ``entries`` sorted from most likely victim to least likely."""

        timestamp = _now() if now is None else float(now)
        if self is EvictionStrategy.COST_AWARE:
            return sorted(
                entries,
                key=lambda entry: (
                    self.cost_score(entry, now=timestamp),
                    entry.last_accessed,
                    entry.created_at,
                ),
            )
        return sorted(
            entries,
            key=lambda entry: (
                self.cost_score(entry, now=timestamp),
                entry.created_at,
                _display_key(entry.key),
            ),
        )

    def select_victim(
        self,
        entries: Iterable[CacheEntry],
        *,
        now: float | None = None,
    ) -> CacheEntry | None:
        """Return the next eviction victim or ``None`` when empty."""

        ranked = self.rank_entries(entries, now=now)
        return ranked[0] if ranked else None

    def should_evict(self, *, size: int, max_entries: int) -> bool:
        """Return whether a cache of ``size`` exceeds ``max_entries``."""

        return max_entries >= 0 and size > max_entries


@dataclass(slots=True)
class CachePolicy:
    """Operational policy governing cache freshness and capacity.

    ``copilot_cache_policy`` is a small named preset intended for higher-level
    orchestration. The field is descriptive rather than magical: code can use
    it to decide whether the cache should be conservative, balanced, or eager.
    """

    ttl_seconds: float | None = 900.0
    max_entries: int = 512
    eviction_strategy: EvictionStrategy | str = EvictionStrategy.COST_AWARE
    require_epoch_match: bool = True
    require_support_match: bool = True
    copilot_cache_policy: str = "balanced"

    def __post_init__(self) -> None:
        """Normalize strategy, limits, and copilot preset labels."""

        if not isinstance(self.eviction_strategy, EvictionStrategy):
            self.eviction_strategy = EvictionStrategy(str(self.eviction_strategy))
        if self.ttl_seconds is not None:
            self.ttl_seconds = max(0.0, float(self.ttl_seconds))
        self.max_entries = max(1, int(self.max_entries))
        self.copilot_cache_policy = _normalize_text(
            self.copilot_cache_policy,
            fallback="balanced",
        )

    def strategy(self) -> EvictionStrategy:
        """Return the normalized eviction strategy."""

        return self.eviction_strategy

    def effective_ttl(self, namespace: str) -> float | None:
        """Return the TTL currently in effect for ``namespace``.

        The copilot preset slightly shifts behavior for exploratory namespaces,
        keeping diagnostics around longer while remaining conservative for
        heavyweight solver outputs.
        """

        if self.ttl_seconds is None:
            return None
        base = self.ttl_seconds
        namespace_text = _normalize_text(namespace)
        if self.copilot_cache_policy == "conservative":
            return base * 0.75
        if self.copilot_cache_policy == "eager" and namespace_text in {"diagnostics", "copilot"}:
            return base * 1.5
        if namespace_text == "solver":
            return base * 0.9
        return base

    def expiry_time(self, created_at: float, *, namespace: str) -> float | None:
        """Return the wall-clock expiry timestamp for an entry."""

        ttl = self.effective_ttl(namespace)
        return None if ttl is None else float(created_at) + ttl

    def needs_eviction(self, size: int) -> bool:
        """Return whether a cache with ``size`` entries must evict."""

        return self.strategy().should_evict(size=size, max_entries=self.max_entries)

    def should_store(self, key: CacheKey) -> bool:
        """Return whether the policy permits storing a result for ``key``."""

        if key.trust_floor < 0:
            return False
        if self.copilot_cache_policy == "conservative" and key.namespace == "speculative":
            return False
        return True

    def permits_entry(
        self,
        entry: CacheEntry,
        *,
        now: float | None = None,
        current_epoch: int | None = None,
        current_support_hash: str | None = None,
        invalid_dependencies: Iterable[str] = (),
    ) -> bool:
        """Return whether ``entry`` is usable under this policy."""

        epoch = current_epoch if self.require_epoch_match else None
        support_hash = current_support_hash if self.require_support_match else None
        return not entry.is_stale(
            now=now,
            ttl=self.effective_ttl(entry.key.namespace),
            current_epoch=epoch,
            current_support_hash=support_hash,
            invalid_dependencies=invalid_dependencies,
        )

    def describe(self) -> str:
        """Return a compact textual summary of the current policy."""

        ttl_label = "none" if self.ttl_seconds is None else f"{self.ttl_seconds:.0f}s"
        return (
            f"ttl={ttl_label}, max_entries={self.max_entries}, strategy={self.strategy().value}, "
            f"epoch_match={self.require_epoch_match}, support_match={self.require_support_match}, "
            f"copilot={self.copilot_cache_policy}"
        )

    def as_dict(self) -> JsonDict:
        """Serialize the policy to a JSON-ready dictionary."""

        return {
            "ttl_seconds": self.ttl_seconds,
            "max_entries": self.max_entries,
            "eviction_strategy": self.strategy().value,
            "require_epoch_match": self.require_epoch_match,
            "require_support_match": self.require_support_match,
            "copilot_cache_policy": self.copilot_cache_policy,
        }


@dataclass(slots=True)
class CacheIndex:
    """Secondary indexes used to invalidate and report on cache contents."""

    by_coordinate_map: MutableMapping[str, set[CacheKey]] = field(default_factory=lambda: defaultdict(set))
    by_dependency_map: MutableMapping[str, set[CacheKey]] = field(default_factory=lambda: defaultdict(set))
    by_namespace_map: MutableMapping[str, set[CacheKey]] = field(default_factory=lambda: defaultdict(set))
    by_proposition_map: MutableMapping[str, set[CacheKey]] = field(default_factory=lambda: defaultdict(set))

    def add(self, entry: CacheEntry) -> None:
        """Insert ``entry`` into every secondary index."""

        self.by_coordinate_map[entry.key.coordinate].add(entry.key)
        self.by_namespace_map[entry.key.namespace].add(entry.key)
        self.by_proposition_map[entry.key.proposition_hash].add(entry.key)
        for dependency in entry.dependencies:
            self.by_dependency_map[dependency].add(entry.key)
        for support_token in entry.support_scope:
            self.by_coordinate_map[support_token].add(entry.key)

    def remove(self, entry: CacheEntry) -> None:
        """Remove ``entry`` from every secondary index."""

        def discard(mapping: MutableMapping[str, set[CacheKey]], bucket: str, key: CacheKey) -> None:
            keys = mapping.get(bucket)
            if not keys:
                return
            keys.discard(key)
            if not keys:
                mapping.pop(bucket, None)

        discard(self.by_coordinate_map, entry.key.coordinate, entry.key)
        discard(self.by_namespace_map, entry.key.namespace, entry.key)
        discard(self.by_proposition_map, entry.key.proposition_hash, entry.key)
        for dependency in entry.dependencies:
            discard(self.by_dependency_map, dependency, entry.key)
        for support_token in entry.support_scope:
            discard(self.by_coordinate_map, support_token, entry.key)

    def rebuild(self, entries: Iterable[CacheEntry]) -> None:
        """Discard current indexes and rebuild them from ``entries``."""

        self.by_coordinate_map.clear()
        self.by_dependency_map.clear()
        self.by_namespace_map.clear()
        self.by_proposition_map.clear()
        for entry in entries:
            self.add(entry)

    def by_coordinate(self, coordinate: str) -> tuple[CacheKey, ...]:
        """Return keys related to ``coordinate`` or support token."""

        return tuple(sorted(self.by_coordinate_map.get(coordinate, ()), key=CacheKey.identity))

    def by_dependency(self, dependency: str) -> tuple[CacheKey, ...]:
        """Return keys that explicitly depend on ``dependency``."""

        return tuple(sorted(self.by_dependency_map.get(dependency, ()), key=CacheKey.identity))

    def by_namespace(self, namespace: str) -> tuple[CacheKey, ...]:
        """Return keys stored under ``namespace``."""

        return tuple(sorted(self.by_namespace_map.get(namespace, ()), key=CacheKey.identity))

    def by_proposition_hash(self, proposition_hash: str) -> tuple[CacheKey, ...]:
        """Return keys whose proposition hash matches ``proposition_hash``."""

        return tuple(sorted(self.by_proposition_map.get(proposition_hash, ()), key=CacheKey.identity))

    def related_to_coordinate(self, coordinate: str) -> tuple[CacheKey, ...]:
        """Return the de-duplicated union of direct and support-related keys."""

        return self.by_coordinate(coordinate)

    def counts(self) -> JsonDict:
        """Return index cardinalities for diagnostics."""

        return {
            "coordinates": len(self.by_coordinate_map),
            "dependencies": len(self.by_dependency_map),
            "namespaces": len(self.by_namespace_map),
            "propositions": len(self.by_proposition_map),
        }


@dataclass(slots=True)
class CacheStatistics:
    """Operational counters for cache analysis and tuning."""

    hits: int = 0
    misses: int = 0
    evictions: int = 0
    puts: int = 0
    invalidations: int = 0
    namespace_hits: dict[str, int] = field(default_factory=dict)
    namespace_misses: dict[str, int] = field(default_factory=dict)
    namespace_puts: dict[str, int] = field(default_factory=dict)
    staleness_frequency: dict[str, int] = field(default_factory=dict)
    last_reset_at: float = field(default_factory=_now)

    def record_hit(self, namespace: str) -> None:
        """Record a successful cache lookup."""

        self.hits += 1
        self.namespace_hits[namespace] = self.namespace_hits.get(namespace, 0) + 1

    def record_miss(self, namespace: str, *, stale_reason: str | None = None) -> None:
        """Record a cache miss and optionally note a staleness cause."""

        self.misses += 1
        self.namespace_misses[namespace] = self.namespace_misses.get(namespace, 0) + 1
        if stale_reason:
            self.note_staleness(stale_reason)

    def record_put(self, namespace: str) -> None:
        """Record insertion of a new cache entry."""

        self.puts += 1
        self.namespace_puts[namespace] = self.namespace_puts.get(namespace, 0) + 1

    def record_eviction(self, namespace: str) -> None:
        """Record an eviction event."""

        self.evictions += 1
        self.note_staleness(f"evicted:{namespace}")

    def record_invalidation(self, count: int = 1) -> None:
        """Record one or more explicit invalidations."""

        self.invalidations += max(0, int(count))

    def note_staleness(self, reason: str) -> None:
        """Increment the counter for a staleness ``reason``."""

        label = _normalize_text(reason, fallback="unknown")
        self.staleness_frequency[label] = self.staleness_frequency.get(label, 0) + 1

    def hit_rate(self) -> float:
        """Return the fraction of lookups that were hits."""

        total = self.hits + self.misses
        return 0.0 if total == 0 else self.hits / total

    def miss_rate(self) -> float:
        """Return the fraction of lookups that were misses."""

        total = self.hits + self.misses
        return 0.0 if total == 0 else self.misses / total

    def namespace_usage(self) -> dict[str, JsonDict]:
        """Return per-namespace hit/miss/put counters."""

        namespaces = sorted(
            set(self.namespace_hits)
            | set(self.namespace_misses)
            | set(self.namespace_puts)
        )
        return {
            namespace: {
                "hits": self.namespace_hits.get(namespace, 0),
                "misses": self.namespace_misses.get(namespace, 0),
                "puts": self.namespace_puts.get(namespace, 0),
            }
            for namespace in namespaces
        }

    def summary(self) -> JsonDict:
        """Return a compact diagnostic summary."""

        return {
            "hits": self.hits,
            "misses": self.misses,
            "evictions": self.evictions,
            "puts": self.puts,
            "invalidations": self.invalidations,
            "hit_rate": self.hit_rate(),
            "miss_rate": self.miss_rate(),
            "last_reset_at": self.last_reset_at,
            "staleness_frequency": dict(sorted(self.staleness_frequency.items())),
        }

    def reset(self) -> None:
        """Reset all counters while keeping the object instance stable."""

        self.hits = 0
        self.misses = 0
        self.evictions = 0
        self.puts = 0
        self.invalidations = 0
        self.namespace_hits.clear()
        self.namespace_misses.clear()
        self.namespace_puts.clear()
        self.staleness_frequency.clear()
        self.last_reset_at = _now()

    def to_dict(self) -> JsonDict:
        """Serialize the statistics object to JSON-ready data."""

        return {
            "hits": self.hits,
            "misses": self.misses,
            "evictions": self.evictions,
            "puts": self.puts,
            "invalidations": self.invalidations,
            "namespace_hits": dict(self.namespace_hits),
            "namespace_misses": dict(self.namespace_misses),
            "namespace_puts": dict(self.namespace_puts),
            "staleness_frequency": dict(self.staleness_frequency),
            "last_reset_at": self.last_reset_at,
        }


# ---------------------------------------------------------------------------
# Main semantic cache
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class SemanticCache:
    """Main semantic cache for reusable verification work.

    The cache stores entries under structured :class:`CacheKey` values, tracks
    coordinate epochs, records explicitly invalidated dependencies, and exposes
    compatibility helpers for older runtime modules.
    """

    policy: CachePolicy = field(default_factory=CachePolicy)
    entries: dict[str, CacheEntry] = field(default_factory=dict)
    index: CacheIndex = field(default_factory=CacheIndex)
    statistics: CacheStatistics = field(default_factory=CacheStatistics)
    coordinate_epochs: dict[str, int] = field(default_factory=dict)
    invalidated_dependencies: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        """Rebuild indexes if entries were preloaded."""

        self.index.rebuild(self.entries.values())
        for entry in self.entries.values():
            self.coordinate_epochs[entry.key.coordinate] = max(
                self.coordinate_epochs.get(entry.key.coordinate, 0),
                entry.key.epoch,
            )

    def _resolve_identity(self, key: CacheKey | str) -> str | None:
        """Return the internal dictionary key for ``key`` when present."""

        if isinstance(key, CacheKey):
            identity = key.identity()
            return identity if identity in self.entries else None
        raw = _normalize_text(key)
        if raw in self.entries:
            return raw
        for identity, entry in self.entries.items():
            if _display_key(entry.key) == raw:
                return identity
        return None

    def _remove_entry(self, identity: str, *, record_eviction: bool = False) -> CacheEntry | None:
        """Remove and return the entry at ``identity`` if it exists."""

        entry = self.entries.pop(identity, None)
        if entry is None:
            return None
        self.index.remove(entry)
        if record_eviction:
            self.statistics.record_eviction(entry.key.namespace)
        return entry

    def current_epoch(self, coordinate: str) -> int:
        """Return the active epoch for ``coordinate``."""

        return int(self.coordinate_epochs.get(coordinate, 0))

    def get(
        self,
        key: CacheKey | str,
        *,
        support_scope: Iterable[str] | SupportRegion | None = None,
    ) -> CacheEntry | None:
        """Return the cached entry for ``key`` when it is still valid."""

        identity = self._resolve_identity(key)
        namespace = key.namespace if isinstance(key, CacheKey) else "legacy"
        if identity is None:
            self.statistics.record_miss(namespace)
            return None
        entry = self.entries[identity]
        current_support_hash = (
            _hash_support_scope(support_scope if support_scope is not None else entry.support_scope)
            if self.policy.require_support_match
            else None
        )
        current_epoch = self.current_epoch(entry.key.coordinate) if self.policy.require_epoch_match else None
        stale_reason: str | None = None
        ttl = self.policy.effective_ttl(entry.key.namespace)
        if entry.is_stale(
            ttl=ttl,
            current_epoch=current_epoch,
            current_support_hash=current_support_hash,
            invalid_dependencies=self.invalidated_dependencies,
        ):
            if ttl is not None and entry.age_seconds() > ttl:
                stale_reason = "ttl"
            elif current_epoch is not None and entry.key.epoch != current_epoch:
                stale_reason = "epoch"
            elif current_support_hash is not None and entry.key.support_hash != current_support_hash:
                stale_reason = "support"
            else:
                stale_reason = "dependency"
            self._remove_entry(identity)
            self.statistics.record_miss(entry.key.namespace, stale_reason=stale_reason)
            return None
        entry.touch()
        self.statistics.record_hit(entry.key.namespace)
        return entry

    def put(
        self,
        entry_or_key: CacheEntry | CacheKey | str,
        value: Any | None = None,
        *,
        namespace: str = "default",
        coordinate: str | None = None,
        proposition_hash: str | None = None,
        epoch: int | None = None,
        support_scope: Iterable[str] | SupportRegion | None = None,
        support_hash: str | None = None,
        trust_floor: int = 0,
        dependencies: Iterable[str] | None = None,
        created_at: float | None = None,
    ) -> CacheEntry:
        """Insert or replace a semantic cache entry.

        The method accepts a prebuilt :class:`CacheEntry`, a :class:`CacheKey`,
        or enough raw parts to synthesize both.
        """

        if isinstance(entry_or_key, CacheEntry):
            entry = entry_or_key
        else:
            resolved_scope = support_scope
            if isinstance(entry_or_key, CacheKey):
                key = entry_or_key
                if resolved_scope is None:
                    resolved_scope = (key.coordinate,)
                expected_support_hash = _hash_support_scope(resolved_scope)
                if key.support_hash != expected_support_hash:
                    key = key.with_support_hash(resolved_scope)
                if epoch is not None and key.epoch != int(epoch):
                    key = key.with_epoch(int(epoch))
            elif coordinate is None and proposition_hash is None and namespace == "default":
                key = CacheKey(
                    namespace="legacy",
                    coordinate=_normalize_text(entry_or_key, fallback="legacy"),
                    proposition_hash=_legacy_proposition_hash(_normalize_text(entry_or_key, fallback="legacy")),
                    epoch=0,
                    support_hash=_hash_support_scope(support_scope),
                    trust_floor=trust_floor,
                )
            else:
                actual_scope = _support_tokens(support_scope)
                actual_coordinate = _normalize_text(coordinate or entry_or_key, fallback="global")
                key = CacheKey(
                    namespace=namespace,
                    coordinate=actual_coordinate,
                    proposition_hash=proposition_hash or _hash_payload(value),
                    epoch=self.current_epoch(actual_coordinate) if epoch is None else int(epoch),
                    support_hash=support_hash or _hash_support_scope(actual_scope),
                    trust_floor=trust_floor,
                )
            timestamp = _now() if created_at is None else float(created_at)
            entry = CacheEntry(
                key=key,
                value=value,
                created_at=timestamp,
                last_accessed=timestamp,
                hit_count=0,
                support_scope=resolved_scope,
                dependencies=dependencies,
            )
        if not self.policy.should_store(entry.key):
            return entry
        identity = entry.key.identity()
        if identity in self.entries:
            self.index.remove(self.entries[identity])
        self.entries[identity] = entry
        self.index.add(entry)
        self.coordinate_epochs[entry.key.coordinate] = max(
            self.coordinate_epochs.get(entry.key.coordinate, 0),
            entry.key.epoch,
        )
        self.statistics.record_put(entry.key.namespace)
        while self.policy.needs_eviction(len(self.entries)):
            victim = self.policy.strategy().select_victim(self.entries.values())
            if victim is None:
                break
            self._remove_entry(victim.key.identity(), record_eviction=True)
        return entry

    def invalidate(self, key: CacheKey | str) -> bool:
        """Remove a specific key from the cache."""

        identity = self._resolve_identity(key)
        if identity is None:
            return False
        removed = self._remove_entry(identity)
        if removed is None:
            return False
        self.statistics.record_invalidation()
        return True

    def invalidate_by_coordinate(self, coordinate: str) -> tuple[str, ...]:
        """Invalidate all entries directly or indirectly supported by ``coordinate``."""

        normalized = _normalize_text(coordinate)
        targets = list(self.index.related_to_coordinate(normalized))
        removed: list[str] = []
        if targets:
            for key in targets:
                identity = key.identity()
                if self._remove_entry(identity) is not None:
                    removed.append(_display_key(key))
            self.statistics.record_invalidation(len(removed))
        self.coordinate_epochs[normalized] = self.current_epoch(normalized) + 1
        return tuple(sorted(removed))

    def invalidate_by_dependency(self, dependency: str) -> tuple[str, ...]:
        """Invalidate entries that explicitly depend on ``dependency``."""

        dep = _normalize_text(dependency)
        self.invalidated_dependencies.add(dep)
        removed: list[str] = []
        for key in list(self.index.by_dependency(dep)):
            if self._remove_entry(key.identity()) is not None:
                removed.append(_display_key(key))
        if removed:
            self.statistics.record_invalidation(len(removed))
        return tuple(sorted(removed))

    def invalidate_by_support(self, support: Iterable[str] | SupportRegion) -> tuple[str, ...]:
        """Invalidate entries whose support overlaps ``support``.

        This compatibility method is retained because older runtime helpers and
        tests still call it directly.
        """

        tokens = set(_support_tokens(support))
        removed: list[str] = []
        for identity, entry in list(self.entries.items()):
            if tokens & set(entry.support_scope):
                self._remove_entry(identity)
                removed.append(_display_key(entry.key))
        if isinstance(support, SupportRegion):
            self.coordinate_epochs[support.coordinate.key] = self.current_epoch(support.coordinate.key) + 1
        if removed:
            self.statistics.record_invalidation(len(removed))
        return tuple(sorted(removed))

    def clear(self) -> int:
        """Remove all cached entries and return the number removed."""

        removed = len(self.entries)
        self.entries.clear()
        self.index.rebuild(())
        self.invalidated_dependencies.clear()
        if removed:
            self.statistics.record_invalidation(removed)
        return removed

    def size(self) -> int:
        """Return the number of active cache entries."""

        return len(self.entries)

    def hit_rate(self) -> float:
        """Return the cache hit rate."""

        return self.statistics.hit_rate()

    def miss_rate(self) -> float:
        """Return the cache miss rate."""

        return self.statistics.miss_rate()

    def snapshot(self) -> dict[str, CacheEntry]:
        """Return a shallow copy of the current cache entries."""

        return {_display_key(entry.key): entry for entry in self.entries.values()}

    # -- cross-subsystem integration -----------------------------------------

    def judgment_cache_key(
        self,
        judgment: Any,
        *,
        namespace: str = "judgments",
        epoch: int = 0,
        support_scope: Iterable[str] | SupportRegion | None = None,
        trust_floor: int = 0,
    ) -> CacheKey:
        """Create a :class:`CacheKey` derived from a judgment term.

        Uses ``jugeo.judgments.judgment_terms.Judgment`` to extract the
        coordinate and proposition identity, producing a cache key that
        is semantically tied to the judgment's content rather than an
        arbitrary string label.

        Parameters
        ----------
        judgment:
            A ``Judgment`` instance from ``jugeo.judgments.judgment_terms``.
        namespace:
            Cache namespace (default ``"judgments"``).
        epoch:
            Coordinate epoch at the time of caching.
        support_scope:
            Support region or token iterable for the support hash.
        trust_floor:
            Minimum trust tier required to reuse the cached result.

        Returns
        -------
        CacheKey
        """
        try:
            from jugeo.judgments.judgment_terms import Judgment as JT
        except ImportError:  # pragma: no cover
            JT = None  # type: ignore[assignment,misc]

        if JT is not None and isinstance(judgment, JT):
            coord_str = (
                ".".join(judgment.coordinate.components)
                if hasattr(judgment.coordinate, "components")
                else str(judgment.coordinate)
            )
            prop_hash = _hash_payload(
                judgment.proposition.formula
                if hasattr(judgment.proposition, "formula")
                else str(judgment.proposition)
            )
        else:
            coord_str = str(getattr(judgment, "coordinate", "global"))
            prop_hash = _hash_payload(str(judgment))

        return CacheKey(
            namespace=namespace,
            coordinate=coord_str,
            proposition_hash=prop_hash,
            epoch=epoch,
            support_hash=_hash_support_scope(support_scope),
            trust_floor=trust_floor,
        )

    def trust_invalidation(
        self,
        trust_profile: Any,
        *,
        coordinate: str | None = None,
    ) -> tuple[str, ...]:
        """Invalidate cache entries whose trust floor exceeds the given profile.

        When a ``jugeo.evidence.trust.TrustProfile`` changes (e.g. a trust
        demotion), cached work that assumed a higher trust floor is no longer
        valid and must be evicted.

        Parameters
        ----------
        trust_profile:
            A ``TrustProfile`` from ``jugeo.evidence.trust``.
        coordinate:
            Optional coordinate filter; when provided only entries for that
            coordinate are considered.

        Returns
        -------
        tuple[str, ...]
            Identity strings of evicted entries.
        """
        try:
            from jugeo.evidence.trust import TrustProfile as TP
        except ImportError:  # pragma: no cover
            TP = None  # type: ignore[assignment,misc]

        if TP is not None and isinstance(trust_profile, TP):
            current_tier = int(trust_profile.tier)
        else:
            current_tier = int(getattr(trust_profile, "tier", 0))

        evicted: list[str] = []
        for identity in list(self.entries):
            entry = self.entries[identity]
            if coordinate is not None and entry.key.coordinate != coordinate:
                continue
            if entry.key.trust_floor > current_tier:
                self._remove_entry(identity, record_eviction=True)
                evicted.append(identity)
        return tuple(evicted)

    def site_scoped_cache(
        self,
        site: Any,
        *,
        namespace: str = "site",
    ) -> dict[str, CacheEntry]:
        """Return entries scoped to coordinates present in a ``Site``.

        Uses ``jugeo.geometry.site.Site`` to restrict the cache view to
        only those entries whose coordinate appears in the site's
        coordinate index.

        Parameters
        ----------
        site:
            A ``Site`` from ``jugeo.geometry.site``.
        namespace:
            Optional namespace filter applied *before* coordinate matching.

        Returns
        -------
        dict[str, CacheEntry]
            Subset of cached entries whose coordinate is in the site.
        """
        try:
            from jugeo.geometry.site import Site
        except ImportError:  # pragma: no cover
            Site = None  # type: ignore[assignment,misc]

        if Site is not None and isinstance(site, Site):
            site_coords: set[str] = set()
            for coord in site.coordinates():
                site_coords.add(
                    ".".join(coord.components)
                    if hasattr(coord, "components")
                    else str(coord)
                )
        else:
            site_coords = set(getattr(site, "coordinates", ()) or ())

        result: dict[str, CacheEntry] = {}
        for identity, entry in self.entries.items():
            if namespace and not entry.key.matches_namespace(namespace):
                continue
            if entry.key.coordinate in site_coords:
                result[identity] = entry
        return result

    def encoding_cache(
        self,
        encoding_family: str,
        value: Any,
        *,
        coordinate: str = "global",
        epoch: int = 0,
        support_scope: Iterable[str] | SupportRegion | None = None,
    ) -> CacheEntry:
        """Cache an encoding result keyed by encoding family.

        The ``jugeo.encodings`` package defines multiple encoding families
        (scalar, text, collection, etc.).  This method stores a computed
        encoding result under a namespace derived from the family name so
        that repeated encoding requests can be served from cache.

        Parameters
        ----------
        encoding_family:
            Name of the encoding family (e.g. ``"scalar_encodings"``).
        value:
            The encoding result to cache.
        coordinate:
            Coordinate for the cached entry.
        epoch:
            Epoch at which the encoding was produced.
        support_scope:
            Support scope for the entry.

        Returns
        -------
        CacheEntry
            The newly stored cache entry.
        """
        namespace = f"encoding:{_normalize_text(encoding_family, fallback='unknown')}"
        key = CacheKey(
            namespace=namespace,
            coordinate=coordinate,
            proposition_hash=_hash_payload(value),
            epoch=epoch,
            support_hash=_hash_support_scope(support_scope),
            trust_floor=0,
        )
        entry = CacheEntry(key, value, support_scope=support_scope)
        self.put(entry)
        return entry


# ---------------------------------------------------------------------------
# Invalidation, warming, and diagnostics helpers
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CacheInvalidator:
    """High-level invalidation helper for epoch and support changes."""

    cache: SemanticCache

    def invalidate_stale(self, *, now: float | None = None) -> tuple[str, ...]:
        """Scan the cache and remove entries that are stale *right now*."""

        timestamp = _now() if now is None else float(now)
        removed: list[str] = []
        for identity, entry in list(self.cache.entries.items()):
            current_epoch = self.cache.current_epoch(entry.key.coordinate)
            if entry.is_stale(
                now=timestamp,
                ttl=self.cache.policy.effective_ttl(entry.key.namespace),
                current_epoch=current_epoch,
                current_support_hash=_hash_support_scope(entry.support_scope),
                invalid_dependencies=self.cache.invalidated_dependencies,
            ):
                self.cache._remove_entry(identity)
                removed.append(_display_key(entry.key))
        if removed:
            self.cache.statistics.record_invalidation(len(removed))
        return tuple(sorted(removed))

    def invalidate_for_epoch(self, coordinate: str, epoch: int) -> tuple[str, ...]:
        """Advance ``coordinate`` to ``epoch`` and drop mismatched entries."""

        coord = _normalize_text(coordinate)
        self.cache.coordinate_epochs[coord] = max(int(epoch), self.cache.current_epoch(coord))
        removed: list[str] = []
        for key in list(self.cache.index.by_coordinate(coord)):
            entry = self.cache.entries.get(key.identity())
            if entry is None:
                continue
            if entry.key.epoch != self.cache.current_epoch(coord):
                self.cache._remove_entry(key.identity())
                removed.append(_display_key(key))
        if removed:
            self.cache.statistics.record_invalidation(len(removed))
        return tuple(sorted(removed))

    def invalidate_support_overlap(self, support: Iterable[str] | SupportRegion) -> tuple[str, ...]:
        """Delegate to support-based invalidation for overlapping supports."""

        return self.cache.invalidate_by_support(support)

    def invalidate_transitively(self, dependencies: Iterable[str]) -> tuple[str, ...]:
        """Invalidate dependencies and all entries depending on derived victims.

        The method performs a breadth-first search over explicit dependency
        names. When an entry is removed, its display key and structured
        identity are added back into the queue so downstream entries can be
        invalidated too.
        """

        queue = deque(_normalize_dependencies(dependencies))
        seen: set[str] = set(queue)
        removed: list[str] = []
        while queue:
            dependency = queue.popleft()
            for key in list(self.cache.index.by_dependency(dependency)):
                entry = self.cache.entries.get(key.identity())
                if entry is None:
                    continue
                label = _display_key(key)
                self.cache.invalidated_dependencies.add(dependency)
                self.cache._remove_entry(key.identity())
                removed.append(label)
                for next_dependency in (label, key.identity(), *entry.dependencies):
                    normalized = _normalize_text(next_dependency)
                    if normalized and normalized not in seen:
                        seen.add(normalized)
                        queue.append(normalized)
        if removed:
            self.cache.statistics.record_invalidation(len(removed))
        return tuple(sorted(set(removed)))

    def mark_dependencies_valid(self, dependencies: Iterable[str]) -> None:
        """Remove dependency invalidation marks for recovered dependencies."""

        for dependency in _normalize_dependencies(dependencies):
            self.cache.invalidated_dependencies.discard(dependency)


@dataclass(slots=True)
class CacheWarmer:
    """Proactively precompute likely-needed cache entries."""

    cache: SemanticCache
    builder: CacheBuilder | None = None

    def warm(
        self,
        keys: Iterable[CacheKey],
        *,
        builder: CacheBuilder | None = None,
    ) -> tuple[CacheEntry, ...]:
        """Ensure all ``keys`` are present by computing missing values."""

        build = builder or self.builder or (lambda key: {"key": key.identity(), "status": "warm"})
        warmed: list[CacheEntry] = []
        for key in keys:
            existing = self.cache.get(key)
            if existing is not None:
                warmed.append(existing)
                continue
            value = build(key)
            entry = self.cache.put(
                key,
                value,
                support_scope=(key.coordinate,),
                dependencies=(key.coordinate,),
            )
            warmed.append(entry)
        return tuple(warmed)

    def warm_for_frontier(
        self,
        frontier: Iterable[str],
        *,
        namespace: str = "frontier",
        builder: CacheBuilder | None = None,
    ) -> tuple[CacheEntry, ...]:
        """Warm entries for frontier coordinates that are likely to be explored."""

        keys = [
            CacheKey(
                namespace=namespace,
                coordinate=_normalize_text(coordinate, fallback="frontier"),
                proposition_hash=_hash_payload({"frontier": coordinate}),
                epoch=self.cache.current_epoch(_normalize_text(coordinate, fallback="frontier")),
                support_hash=_hash_support_scope((_normalize_text(coordinate, fallback="frontier"),)),
                trust_floor=0,
            )
            for coordinate in frontier
        ]
        return self.warm(keys, builder=builder)

    def warm_for_goal_tree(
        self,
        goal_tree: Mapping[str, Sequence[str]],
        *,
        builder: CacheBuilder | None = None,
    ) -> tuple[CacheEntry, ...]:
        """Warm a goal tree by computing entries for each node and child cluster."""

        keys: list[CacheKey] = []
        for root, children in goal_tree.items():
            scope = (root, *children)
            keys.append(
                CacheKey(
                    namespace="goal-tree",
                    coordinate=root,
                    proposition_hash=_hash_payload({"root": root, "children": list(children)}),
                    epoch=self.cache.current_epoch(root),
                    support_hash=_hash_support_scope(scope),
                    trust_floor=0,
                )
            )
        return self.warm(keys, builder=builder)

    def predict_next_keys(
        self,
        recent_keys: Sequence[CacheKey],
        *,
        limit: int = 5,
    ) -> tuple[CacheKey, ...]:
        """Predict likely next keys using simple locality and namespace heuristics."""

        if not recent_keys:
            return ()
        namespace_counts = Counter(key.namespace for key in recent_keys)
        dominant_namespace = namespace_counts.most_common(1)[0][0]
        candidates: list[CacheKey] = []
        seen: set[str] = set()
        for key in reversed(recent_keys):
            predicted_coordinate = key.coordinate
            next_key = CacheKey(
                namespace=dominant_namespace,
                coordinate=predicted_coordinate,
                proposition_hash=key.proposition_hash,
                epoch=self.cache.current_epoch(predicted_coordinate),
                support_hash=key.support_hash,
                trust_floor=key.trust_floor,
            )
            identity = next_key.identity()
            if identity not in seen:
                seen.add(identity)
                candidates.append(next_key)
            if len(candidates) >= limit:
                break
        return tuple(candidates)

    def copilot_warm_suggestions(self, recent_keys: Sequence[CacheKey]) -> tuple[str, ...]:
        """Return human-readable warmup suggestions for copilot orchestration."""

        suggestions = []
        for key in self.predict_next_keys(recent_keys):
            suggestions.append(
                f"warm namespace={key.namespace} coordinate={key.coordinate} epoch={key.epoch}"
            )
        return tuple(suggestions)


@dataclass(slots=True)
class CacheDiagnostics:
    """Diagnostic views over cache contents and behavior."""

    cache: SemanticCache

    def summary(self) -> JsonDict:
        """Return a high-level summary of cache state."""

        oldest = None
        if self.cache.entries:
            oldest = min(entry.created_at for entry in self.cache.entries.values())
        return {
            "size": self.cache.size(),
            "policy": self.cache.policy.as_dict(),
            "statistics": self.cache.statistics.summary(),
            "index_counts": self.cache.index.counts(),
            "oldest_entry_age": None if oldest is None else _now() - oldest,
        }

    def stale_report(self, *, now: float | None = None) -> tuple[JsonDict, ...]:
        """Return a report describing entries that would currently be stale."""

        timestamp = _now() if now is None else float(now)
        report: list[JsonDict] = []
        for entry in self.cache.entries.values():
            ttl = self.cache.policy.effective_ttl(entry.key.namespace)
            current_epoch = self.cache.current_epoch(entry.key.coordinate)
            current_support_hash = _hash_support_scope(entry.support_scope)
            stale = entry.is_stale(
                now=timestamp,
                ttl=ttl,
                current_epoch=current_epoch,
                current_support_hash=current_support_hash,
                invalid_dependencies=self.cache.invalidated_dependencies,
            )
            if stale:
                report.append(
                    {
                        "key": _display_key(entry.key),
                        "namespace": entry.key.namespace,
                        "coordinate": entry.key.coordinate,
                        "age_seconds": entry.age_seconds(now=timestamp),
                        "epoch": entry.key.epoch,
                        "current_epoch": current_epoch,
                    }
                )
        return tuple(sorted(report, key=lambda item: str(item["key"])))

    def namespace_report(self) -> tuple[JsonDict, ...]:
        """Return per-namespace entry counts and access counters."""

        sizes: Counter[str] = Counter(entry.key.namespace for entry in self.cache.entries.values())
        usage = self.cache.statistics.namespace_usage()
        namespaces = sorted(set(sizes) | set(usage))
        return tuple(
            {
                "namespace": namespace,
                "entries": sizes.get(namespace, 0),
                **usage.get(namespace, {"hits": 0, "misses": 0, "puts": 0}),
            }
            for namespace in namespaces
        )

    def efficiency_report(self) -> JsonDict:
        """Return quantitative efficiency metrics for the cache."""

        hit_rate = self.cache.hit_rate()
        miss_rate = self.cache.miss_rate()
        evictions = self.cache.statistics.evictions
        invalidations = self.cache.statistics.invalidations
        churn = 0.0 if self.cache.statistics.puts == 0 else (evictions + invalidations) / self.cache.statistics.puts
        return {
            "hit_rate": hit_rate,
            "miss_rate": miss_rate,
            "evictions": evictions,
            "invalidations": invalidations,
            "puts": self.cache.statistics.puts,
            "churn": churn,
            "invalidated_dependencies": sorted(self.cache.invalidated_dependencies),
        }

    def copilot_cache_summary(self) -> str:
        """Return a concise English summary meant for copilot-facing surfaces."""

        stats = self.cache.statistics.summary()
        return (
            f"copilot cache summary: size={self.cache.size()}, hit_rate={stats['hit_rate']:.2%}, "
            f"misses={stats['misses']}, evictions={stats['evictions']}, policy={self.cache.policy.describe()}"
        )

    def victim_preview(self, *, limit: int = 5) -> tuple[str, ...]:
        """Return a preview of likely eviction victims under the active strategy."""

        ranked = self.cache.policy.strategy().rank_entries(self.cache.entries.values())
        return tuple(_display_key(entry.key) for entry in ranked[: max(0, int(limit))])


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


class CacheSerializer:
    """JSON serialization helpers for keys, entries, statistics, and caches."""

    @staticmethod
    def key_to_dict(key: CacheKey) -> JsonDict:
        """Serialize a cache key to a dictionary."""

        return key.to_dict()

    @staticmethod
    def key_from_dict(payload: Mapping[str, Any]) -> CacheKey:
        """Deserialize a cache key from a dictionary."""

        return CacheKey.from_dict(payload)

    @staticmethod
    def entry_to_dict(entry: CacheEntry) -> JsonDict:
        """Serialize a cache entry to a dictionary."""

        return entry.to_dict()

    @staticmethod
    def entry_from_dict(payload: Mapping[str, Any]) -> CacheEntry:
        """Deserialize a cache entry from a dictionary."""

        return CacheEntry(
            key=CacheKey.from_dict(payload["key"]),
            value=payload.get("value"),
            created_at=float(payload.get("created_at", _now())),
            last_accessed=float(payload.get("last_accessed", payload.get("created_at", _now()))),
            hit_count=int(payload.get("hit_count", 0)),
            support_scope=payload.get("support_scope", ()),
            dependencies=payload.get("dependencies", ()),
        )

    @staticmethod
    def statistics_to_dict(statistics: CacheStatistics) -> JsonDict:
        """Serialize cache statistics to a dictionary."""

        return statistics.to_dict()

    @staticmethod
    def statistics_from_dict(payload: Mapping[str, Any]) -> CacheStatistics:
        """Deserialize cache statistics from a dictionary."""

        return CacheStatistics(
            hits=int(payload.get("hits", 0)),
            misses=int(payload.get("misses", 0)),
            evictions=int(payload.get("evictions", 0)),
            puts=int(payload.get("puts", 0)),
            invalidations=int(payload.get("invalidations", 0)),
            namespace_hits=dict(payload.get("namespace_hits", {})),
            namespace_misses=dict(payload.get("namespace_misses", {})),
            namespace_puts=dict(payload.get("namespace_puts", {})),
            staleness_frequency=dict(payload.get("staleness_frequency", {})),
            last_reset_at=float(payload.get("last_reset_at", _now())),
        )

    @classmethod
    def dump_cache(cls, cache: SemanticCache, *, indent: int = 2) -> str:
        """Serialize an entire cache to a JSON string."""

        payload = {
            "policy": cache.policy.as_dict(),
            "entries": [cls.entry_to_dict(entry) for entry in cache.entries.values()],
            "statistics": cls.statistics_to_dict(cache.statistics),
            "coordinate_epochs": dict(cache.coordinate_epochs),
            "invalidated_dependencies": sorted(cache.invalidated_dependencies),
        }
        return json.dumps(payload, indent=indent, sort_keys=True, default=str)

    @classmethod
    def load_cache(cls, payload: str | Mapping[str, Any]) -> SemanticCache:
        """Deserialize an entire :class:`SemanticCache` from JSON or mapping."""

        data = json.loads(payload) if isinstance(payload, str) else dict(payload)
        policy = CachePolicy(**data.get("policy", {}))
        entries = [cls.entry_from_dict(item) for item in data.get("entries", [])]
        cache = SemanticCache(policy=policy)
        cache.statistics = cls.statistics_from_dict(data.get("statistics", {}))
        cache.coordinate_epochs = {
            str(key): int(value)
            for key, value in dict(data.get("coordinate_epochs", {})).items()
        }
        cache.invalidated_dependencies = set(data.get("invalidated_dependencies", ()))
        for entry in entries:
            cache.entries[entry.key.identity()] = entry
        cache.index.rebuild(cache.entries.values())
        for entry in entries:
            cache.coordinate_epochs[entry.key.coordinate] = max(
                cache.coordinate_epochs.get(entry.key.coordinate, 0),
                entry.key.epoch,
            )
        return cache

    @classmethod
    def copilot_cache_summary(cls, cache: SemanticCache) -> str:
        """Serialize a short summary string for copilot-facing transport."""

        diagnostics = CacheDiagnostics(cache)
        return diagnostics.copilot_cache_summary()


__all__ = [
    "CacheDiagnostics",
    "CacheEntry",
    "CacheIndex",
    "CacheInvalidator",
    "CacheKey",
    "CachePolicy",
    "CacheSerializer",
    "CacheStatistics",
    "CacheWarmer",
    "EvictionStrategy",
    "SemanticCache",
]

# copilot: semantic cache surface for support-aware orchestration and reuse.
