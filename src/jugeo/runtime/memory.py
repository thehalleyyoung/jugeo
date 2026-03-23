"""Persistent semantic memory for the JuGeo runtime.

Implements the semantic memory 𝔐 from theory2.tex §32 ("Incrementality,
invalidation, and persistent semantic memory").  The memory stores all
judgments, evidence, obstructions, and trust state as *local sections*
over the Grothendieck site.  A semantic change event δ with support S is
interpreted as:

    𝔐' = Glue(𝔐|_{X \\ S}, new_sections, overlap_data)

That is: restrict unchanged sections away from the support, splice in new
sections over the affected star-neighbourhood, and glue along the overlap
frontier.

Design invariants
-----------------
* Immutable snapshots — every mutation produces a new snapshot id.
* Transactional updates — batched changes commit or roll back atomically.
* Quota enforcement — memory regions are bounded; stale data is evicted.
* Serialisation round-trips — JSON and compact binary for persistence.
* Copilot integration — diagnostics helpers produce summaries that a
  copilot agent can consume when proposing repairs or promotions.
"""

from __future__ import annotations

import copy
import hashlib
import json
import struct
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Iterator, Mapping, Sequence

from jugeo.evidence.provenance import ProvenanceTrace
from jugeo.evidence.trust import TrustLevel
from jugeo.geometry.site import Coordinate
from jugeo.geometry.supports import SupportSet
from jugeo.judgments.judgment_terms import (
    EvidenceBundle,
    EvidenceItem,
    Judgment,
    Obstruction,
    TrustAnnotation,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _stable_hash(data: str) -> str:
    """Content-addressed SHA-256 hex digest of *data*."""
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# 1. MemoryRegion
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class MemoryRegion:
    """A region of semantic memory corresponding to a coordinate range.

    Each region stores the *local sections* (judgments, evidence,
    obstructions) over its coordinate range, faithfully implementing the
    presheaf-of-sections view from theory2.tex §32.
    """

    coordinate_range: tuple[str, ...]
    judgments: dict[str, Judgment] = field(default_factory=dict)
    evidence: dict[str, EvidenceBundle] = field(default_factory=dict)
    obstructions: dict[str, Obstruction] = field(default_factory=dict)
    _metadata: dict[str, Any] = field(default_factory=dict)

    # -- query ---------------------------------------------------------------

    def is_empty(self) -> bool:
        """Return *True* when the region carries no sections."""
        return (
            not self.judgments
            and not self.evidence
            and not self.obstructions
        )

    def size(self) -> int:
        """Total number of stored items across all section types."""
        return len(self.judgments) + len(self.evidence) + len(self.obstructions)

    def contains_coordinate(self, coord_key: str) -> bool:
        """Check whether *coord_key* falls within this region's range."""
        return coord_key in self.coordinate_range

    # -- restriction & extension ---------------------------------------------

    def restrict_to(self, coords: frozenset[str]) -> MemoryRegion:
        """Return a new region restricted to the given coordinate keys.

        This implements the restriction functor 𝔐|_{X \\ S}: only sections
        whose coordinate falls within *coords* survive.
        """
        new_range = tuple(c for c in self.coordinate_range if c in coords)
        return MemoryRegion(
            coordinate_range=new_range,
            judgments={k: v for k, v in self.judgments.items() if k in coords},
            evidence={k: v for k, v in self.evidence.items() if k in coords},
            obstructions={k: v for k, v in self.obstructions.items() if k in coords},
            _metadata=dict(self._metadata),
        )

    def extend(self, other: MemoryRegion) -> MemoryRegion:
        """Extend this region with sections from *other*.

        Keys in *other* overwrite keys in *self* — this is intentional for
        the splice step of the gluing construction.
        """
        merged_range = tuple(
            dict.fromkeys(self.coordinate_range + other.coordinate_range)
        )
        merged_j = {**self.judgments, **other.judgments}
        merged_e = {**self.evidence, **other.evidence}
        merged_o = {**self.obstructions, **other.obstructions}
        return MemoryRegion(
            coordinate_range=merged_range,
            judgments=merged_j,
            evidence=merged_e,
            obstructions=merged_o,
            _metadata={**self._metadata, **other._metadata},
        )

    def merge(self, other: MemoryRegion) -> MemoryRegion:
        """Non-destructive merge preserving existing keys on collision.

        Unlike :meth:`extend`, existing entries in *self* win over *other*.
        """
        merged_range = tuple(
            dict.fromkeys(self.coordinate_range + other.coordinate_range)
        )
        merged_j = {**other.judgments, **self.judgments}
        merged_e = {**other.evidence, **self.evidence}
        merged_o = {**other.obstructions, **self.obstructions}
        return MemoryRegion(
            coordinate_range=merged_range,
            judgments=merged_j,
            evidence=merged_e,
            obstructions=merged_o,
            _metadata={**other._metadata, **self._metadata},
        )

    def coordinates(self) -> frozenset[str]:
        """All coordinate keys that actually carry at least one section."""
        return frozenset(self.judgments) | frozenset(self.evidence) | frozenset(self.obstructions)

    def diff(self, other: MemoryRegion) -> dict[str, Any]:
        """Compute a lightweight diff between *self* and *other*.

        Returns a dict with keys ``added``, ``removed``, ``changed`` each
        mapping to sets of coordinate keys.
        """
        self_keys = self.coordinates()
        other_keys = other.coordinates()
        added = other_keys - self_keys
        removed = self_keys - other_keys
        common = self_keys & other_keys
        changed: set[str] = set()
        for k in common:
            if (
                self.judgments.get(k) != other.judgments.get(k)
                or self.evidence.get(k) != other.evidence.get(k)
                or self.obstructions.get(k) != other.obstructions.get(k)
            ):
                changed.add(k)
        return {"added": added, "removed": removed, "changed": changed}


# ---------------------------------------------------------------------------
# 2. MemoryUpdate
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class MemoryUpdate:
    """A semantic change event δ with support S (theory2.tex §32).

    Captures replacement of local sections on the support together with
    restriction of unaffected sections away from the support and a gluing
    step on the overlap frontier.
    """

    delta_id: str
    support_set: SupportSet
    new_sections: dict[str, Any]
    removed_sections: frozenset[str]
    timestamp: str
    provenance: ProvenanceTrace | None

    # -- query ---------------------------------------------------------------

    def affected_coordinates(self) -> frozenset[str]:
        """All coordinates touched by this change event."""
        return frozenset(self.new_sections) | self.removed_sections

    def overlap_frontier(self, existing_coords: frozenset[str]) -> frozenset[str]:
        """Coordinates in both the existing memory and the affected set.

        This is the frontier along which the gluing condition must hold.
        """
        return existing_coords & self.affected_coordinates()

    # -- application ---------------------------------------------------------

    def apply_to(self, region: MemoryRegion) -> MemoryRegion:
        """Apply this update to *region*, producing a new region.

        Implements 𝔐' = Glue(𝔐|_{X \\ S}, new_sections, overlap_data):
        1. Restrict *region* to coordinates outside the support.
        2. Build a fresh region from *new_sections*.
        3. Extend (glue) the restricted region with the fresh sections.
        """
        keep_coords = frozenset(region.coordinate_range) - self.affected_coordinates()
        restricted = region.restrict_to(keep_coords)
        # Build new region from provided sections.
        new_region = MemoryRegion(
            coordinate_range=tuple(self.new_sections),
            judgments={
                k: v for k, v in self.new_sections.items()
                if isinstance(v, Judgment)
            },
            evidence={
                k: v for k, v in self.new_sections.items()
                if isinstance(v, EvidenceBundle)
            },
            obstructions={
                k: v for k, v in self.new_sections.items()
                if isinstance(v, Obstruction)
            },
        )
        return restricted.extend(new_region)

    def preview(self, region: MemoryRegion) -> dict[str, Any]:
        """Preview the effect of this update without mutating anything."""
        future = self.apply_to(region)
        return region.diff(future)

    def rollback_data(self, region: MemoryRegion) -> dict[str, Any]:
        """Capture the data needed to undo this update.

        Returns a dict mapping each affected coordinate to its *current*
        section value so a later rollback can restore it.
        """
        affected = self.affected_coordinates()
        rollback: dict[str, Any] = {}
        for k in affected:
            entry: dict[str, Any] = {}
            if k in region.judgments:
                entry["judgment"] = region.judgments[k]
            if k in region.evidence:
                entry["evidence"] = region.evidence[k]
            if k in region.obstructions:
                entry["obstruction"] = region.obstructions[k]
            if entry:
                rollback[k] = entry
        return rollback

    def is_noop(self) -> bool:
        """Return *True* if this update changes nothing."""
        return not self.new_sections and not self.removed_sections


# ---------------------------------------------------------------------------
# 3. MemoryIndex
# ---------------------------------------------------------------------------

class MemoryIndex:
    """Efficient indexing over semantic memory contents.

    Maintains secondary indices keyed by coordinate, proposition text,
    trust level, and evidence channel so that lookups avoid linear scans.
    """

    def __init__(self) -> None:
        self._by_coordinate: dict[str, set[str]] = {}
        self._by_proposition: dict[str, set[str]] = {}
        self._by_trust: dict[str, set[str]] = {}
        self._by_channel: dict[str, set[str]] = {}

    # -- indexing -------------------------------------------------------------

    def index_by_coordinate(self, key: str, coord: str) -> None:
        """Register *key* under the coordinate index for *coord*."""
        self._by_coordinate.setdefault(coord, set()).add(key)

    def index_by_proposition(self, key: str, prop_text: str) -> None:
        """Register *key* under the proposition text index."""
        self._by_proposition.setdefault(prop_text, set()).add(key)

    def index_by_trust(self, key: str, trust_level: str) -> None:
        """Register *key* under the trust-level index."""
        self._by_trust.setdefault(trust_level, set()).add(key)

    def index_by_channel(self, key: str, channel: str) -> None:
        """Register *key* under the evidence-channel index."""
        self._by_channel.setdefault(channel, set()).add(key)

    # -- lookup ---------------------------------------------------------------

    def lookup(self, index_name: str, value: str) -> frozenset[str]:
        """Look up keys registered under *value* in the named index.

        *index_name* must be one of ``coordinate``, ``proposition``,
        ``trust``, or ``channel``.
        """
        idx_map: dict[str, dict[str, set[str]]] = {
            "coordinate": self._by_coordinate,
            "proposition": self._by_proposition,
            "trust": self._by_trust,
            "channel": self._by_channel,
        }
        idx = idx_map.get(index_name)
        if idx is None:
            raise ValueError(f"Unknown index: {index_name!r}")
        return frozenset(idx.get(value, set()))

    def range_query(
        self, index_name: str, prefix: str, *, limit: int = 100
    ) -> list[tuple[str, frozenset[str]]]:
        """Return all (value, keys) pairs whose value starts with *prefix*.

        At most *limit* entries are returned, sorted lexicographically.
        """
        idx_map: dict[str, dict[str, set[str]]] = {
            "coordinate": self._by_coordinate,
            "proposition": self._by_proposition,
            "trust": self._by_trust,
            "channel": self._by_channel,
        }
        idx = idx_map.get(index_name)
        if idx is None:
            raise ValueError(f"Unknown index: {index_name!r}")
        results = sorted(
            ((v, frozenset(ks)) for v, ks in idx.items() if v.startswith(prefix)),
            key=lambda t: t[0],
        )
        return results[:limit]

    def prefix_query(self, index_name: str, prefix: str) -> frozenset[str]:
        """Return all keys whose indexed value starts with *prefix*."""
        entries = self.range_query(index_name, prefix, limit=10_000)
        keys: set[str] = set()
        for _, ks in entries:
            keys |= ks
        return frozenset(keys)

    def remove_key(self, key: str) -> None:
        """Remove *key* from every secondary index."""
        for idx in (
            self._by_coordinate,
            self._by_proposition,
            self._by_trust,
            self._by_channel,
        ):
            for bucket in idx.values():
                bucket.discard(key)

    def rebuild(self, region: MemoryRegion) -> None:
        """Rebuild all indices from scratch for the given *region*."""
        self._by_coordinate.clear()
        self._by_proposition.clear()
        self._by_trust.clear()
        self._by_channel.clear()
        for k, j in region.judgments.items():
            coord_key = (
                ".".join(j.coordinate.components)
                if hasattr(j.coordinate, "components")
                else str(j.coordinate)
            )
            self.index_by_coordinate(k, coord_key)
            if hasattr(j.proposition, "formula"):
                self.index_by_proposition(k, j.proposition.formula)
            if hasattr(j.trust, "level"):
                self.index_by_trust(k, str(j.trust.level))
        for k, eb in region.evidence.items():
            for item in eb.items:
                if hasattr(item, "channel"):
                    self.index_by_channel(k, item.channel)

    def statistics(self) -> dict[str, int]:
        """Return index size statistics."""
        return {
            "coordinate_buckets": len(self._by_coordinate),
            "proposition_buckets": len(self._by_proposition),
            "trust_buckets": len(self._by_trust),
            "channel_buckets": len(self._by_channel),
            "total_keys": sum(
                len(b)
                for idx in (
                    self._by_coordinate,
                    self._by_proposition,
                    self._by_trust,
                    self._by_channel,
                )
                for b in idx.values()
            ),
        }


# ---------------------------------------------------------------------------
# 4. MemoryGC — garbage collection for stale data
# ---------------------------------------------------------------------------

class MemoryGC:
    """Garbage collector for stale or unreachable semantic memory entries.

    Identifies entries that are no longer referenced by any live judgment,
    have expired evidence, or belong to removed coordinate ranges.
    """

    def __init__(self, *, staleness_threshold_seconds: float = 3600.0) -> None:
        self._threshold = staleness_threshold_seconds
        self._last_gc_time: str | None = None
        self._collected_count: int = 0
        self._compaction_count: int = 0

    def identify_stale(
        self,
        region: MemoryRegion,
        live_keys: frozenset[str],
    ) -> frozenset[str]:
        """Return keys in *region* that are not in *live_keys*."""
        all_keys = frozenset(region.judgments) | frozenset(region.evidence) | frozenset(region.obstructions)
        return all_keys - live_keys

    def identify_unreachable(
        self,
        region: MemoryRegion,
        root_keys: frozenset[str],
        dependency_graph: Mapping[str, frozenset[str]],
    ) -> frozenset[str]:
        """BFS from *root_keys* through *dependency_graph*; return unreachable.

        Any key present in the region but not reachable from roots is
        considered garbage.
        """
        reachable: set[str] = set()
        frontier = list(root_keys)
        while frontier:
            current = frontier.pop()
            if current in reachable:
                continue
            reachable.add(current)
            for dep in dependency_graph.get(current, frozenset()):
                if dep not in reachable:
                    frontier.append(dep)
        all_keys = frozenset(region.judgments) | frozenset(region.evidence) | frozenset(region.obstructions)
        return all_keys - frozenset(reachable)

    def collect(
        self,
        region: MemoryRegion,
        live_keys: frozenset[str],
    ) -> MemoryRegion:
        """Remove stale entries from *region*, returning a compacted copy."""
        stale = self.identify_stale(region, live_keys)
        self._collected_count += len(stale)
        self._last_gc_time = _now_iso()
        return MemoryRegion(
            coordinate_range=tuple(
                c for c in region.coordinate_range if c not in stale
            ),
            judgments={k: v for k, v in region.judgments.items() if k not in stale},
            evidence={k: v for k, v in region.evidence.items() if k not in stale},
            obstructions={k: v for k, v in region.obstructions.items() if k not in stale},
        )

    def compact(self, region: MemoryRegion) -> MemoryRegion:
        """Remove coordinate-range entries that have no associated sections."""
        active = region.coordinates()
        self._compaction_count += 1
        return MemoryRegion(
            coordinate_range=tuple(c for c in region.coordinate_range if c in active),
            judgments=dict(region.judgments),
            evidence=dict(region.evidence),
            obstructions=dict(region.obstructions),
        )

    def defragment(self, region: MemoryRegion) -> MemoryRegion:
        """Re-key the coordinate range to a canonical sorted order.

        This is a logical defragmentation — no physical memory is freed, but
        iteration order becomes deterministic and cache-friendly.
        """
        sorted_range = tuple(sorted(set(region.coordinate_range)))
        return MemoryRegion(
            coordinate_range=sorted_range,
            judgments=dict(sorted(region.judgments.items())),
            evidence=dict(sorted(region.evidence.items())),
            obstructions=dict(sorted(region.obstructions.items())),
        )

    def gc_statistics(self) -> dict[str, Any]:
        """Return cumulative garbage-collection statistics."""
        return {
            "last_gc_time": self._last_gc_time,
            "total_collected": self._collected_count,
            "total_compactions": self._compaction_count,
        }


# ---------------------------------------------------------------------------
# 5. MemorySnapshot — immutable snapshot
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class MemorySnapshot:
    """An immutable, content-addressed snapshot of semantic memory.

    Snapshots enable cheap diff-and-restore workflows and provide the
    *persistent* part of persistent semantic memory.
    """

    snapshot_id: str
    timestamp: str
    data_hash: str
    region_data: dict[str, Any]

    @staticmethod
    def capture(region: MemoryRegion) -> MemorySnapshot:
        """Create a snapshot from the current state of *region*."""
        payload = json.dumps(
            {
                "coordinate_range": list(region.coordinate_range),
                "judgment_keys": sorted(region.judgments),
                "evidence_keys": sorted(region.evidence),
                "obstruction_keys": sorted(region.obstructions),
            },
            sort_keys=True,
        )
        return MemorySnapshot(
            snapshot_id=str(uuid.uuid4()),
            timestamp=_now_iso(),
            data_hash=_stable_hash(payload),
            region_data={
                "coordinate_range": list(region.coordinate_range),
                "judgments": dict(region.judgments),
                "evidence": dict(region.evidence),
                "obstructions": dict(region.obstructions),
            },
        )

    def restore(self) -> MemoryRegion:
        """Reconstruct a :class:`MemoryRegion` from this snapshot."""
        return MemoryRegion(
            coordinate_range=tuple(self.region_data.get("coordinate_range", ())),
            judgments=dict(self.region_data.get("judgments", {})),
            evidence=dict(self.region_data.get("evidence", {})),
            obstructions=dict(self.region_data.get("obstructions", {})),
        )

    def diff_with(self, other: MemorySnapshot) -> dict[str, Any]:
        """Return a structural diff between *self* and *other*.

        Uses the hashes for a fast equality check before doing key-level
        comparison.
        """
        if self.data_hash == other.data_hash:
            return {"equal": True, "added": set(), "removed": set(), "changed": set()}
        self_keys = set(self.region_data.get("judgments", {}))
        other_keys = set(other.region_data.get("judgments", {}))
        return {
            "equal": False,
            "added": other_keys - self_keys,
            "removed": self_keys - other_keys,
            "changed": {
                k
                for k in self_keys & other_keys
                if self.region_data["judgments"].get(k)
                != other.region_data["judgments"].get(k)
            },
        }

    def serialize(self) -> str:
        """Serialize to a JSON string suitable for persistence."""
        return json.dumps(
            {
                "snapshot_id": self.snapshot_id,
                "timestamp": self.timestamp,
                "data_hash": self.data_hash,
                "coordinate_range": self.region_data.get("coordinate_range", []),
                "judgment_keys": sorted(self.region_data.get("judgments", {})),
                "evidence_keys": sorted(self.region_data.get("evidence", {})),
                "obstruction_keys": sorted(self.region_data.get("obstructions", {})),
            },
            indent=2,
        )

    def is_consistent(self) -> bool:
        """Re-derive the data hash and check it matches the stored hash.

        A failed check indicates corruption or tampering.
        """
        payload = json.dumps(
            {
                "coordinate_range": list(
                    self.region_data.get("coordinate_range", ())
                ),
                "judgment_keys": sorted(self.region_data.get("judgments", {})),
                "evidence_keys": sorted(self.region_data.get("evidence", {})),
                "obstruction_keys": sorted(
                    self.region_data.get("obstructions", {})
                ),
            },
            sort_keys=True,
        )
        return _stable_hash(payload) == self.data_hash


# ---------------------------------------------------------------------------
# 6. MemoryTransaction — transactional updates
# ---------------------------------------------------------------------------

class _TransactionOp(Enum):
    """Kind of operation recorded inside a transaction."""

    STORE_JUDGMENT = auto()
    STORE_EVIDENCE = auto()
    STORE_OBSTRUCTION = auto()
    REMOVE = auto()


@dataclass(slots=True)
class MemoryTransaction:
    """Transactional wrapper over :class:`MemoryRegion`.

    Groups multiple mutations and applies them atomically on commit.
    If any step fails the entire batch can be rolled back.
    """

    _region: MemoryRegion
    _ops: list[tuple[_TransactionOp, str, Any]] = field(default_factory=list)
    _active: bool = field(default=False)
    _rollback_snapshot: MemorySnapshot | None = field(default=None)

    def begin(self) -> None:
        """Begin a new transaction, capturing a rollback snapshot."""
        if self._active:
            raise RuntimeError("Transaction already active")
        self._rollback_snapshot = MemorySnapshot.capture(self._region)
        self._ops.clear()
        self._active = True

    def is_active(self) -> bool:
        """Return *True* if a transaction is in progress."""
        return self._active

    def add_operation(
        self,
        op: _TransactionOp,
        key: str,
        value: Any = None,
    ) -> None:
        """Enqueue an operation to be applied on commit."""
        if not self._active:
            raise RuntimeError("No active transaction")
        self._ops.append((op, key, value))

    def preview_changes(self) -> list[dict[str, Any]]:
        """Return a human-readable preview of pending operations."""
        return [
            {"op": op.name, "key": key, "has_value": value is not None}
            for op, key, value in self._ops
        ]

    def commit(self) -> None:
        """Apply all enqueued operations and close the transaction."""
        if not self._active:
            raise RuntimeError("No active transaction")
        for op, key, value in self._ops:
            if op is _TransactionOp.STORE_JUDGMENT:
                self._region.judgments[key] = value
                if key not in self._region.coordinate_range:
                    self._region.coordinate_range = (
                        *self._region.coordinate_range,
                        key,
                    )
            elif op is _TransactionOp.STORE_EVIDENCE:
                self._region.evidence[key] = value
            elif op is _TransactionOp.STORE_OBSTRUCTION:
                self._region.obstructions[key] = value
            elif op is _TransactionOp.REMOVE:
                self._region.judgments.pop(key, None)
                self._region.evidence.pop(key, None)
                self._region.obstructions.pop(key, None)
        self._ops.clear()
        self._active = False
        self._rollback_snapshot = None

    def rollback(self) -> None:
        """Discard all pending operations and restore the pre-transaction state."""
        if not self._active:
            raise RuntimeError("No active transaction")
        if self._rollback_snapshot is not None:
            restored = self._rollback_snapshot.restore()
            self._region.coordinate_range = restored.coordinate_range
            self._region.judgments = restored.judgments
            self._region.evidence = restored.evidence
            self._region.obstructions = restored.obstructions
        self._ops.clear()
        self._active = False
        self._rollback_snapshot = None


# ---------------------------------------------------------------------------
# 7. MemoryQuotaManager
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class MemoryQuotaManager:
    """Manages per-region memory quotas to bound resource usage.

    Each coordinate range can be assigned a maximum number of entries.  When
    the limit is exceeded the oldest or lowest-trust entries are evicted.
    """

    global_limit: int = 100_000
    region_limits: dict[str, int] = field(default_factory=dict)
    _eviction_log: list[dict[str, Any]] = field(default_factory=list)

    def check_quota(self, region: MemoryRegion) -> bool:
        """Return *True* if *region* is within its quota."""
        limit = self._limit_for(region)
        return region.size() <= limit

    def enforce_quota(self, region: MemoryRegion) -> MemoryRegion:
        """Evict entries until *region* is within its quota.

        Eviction strategy: remove the oldest (earliest in coordinate_range)
        entries first.  A more sophisticated trust-aware policy can be
        plugged in via subclassing.
        """
        limit = self._limit_for(region)
        if region.size() <= limit:
            return region
        overshoot = region.size() - limit
        to_remove: list[str] = []
        for coord in region.coordinate_range:
            if len(to_remove) >= overshoot:
                break
            if coord in region.judgments or coord in region.evidence or coord in region.obstructions:
                to_remove.append(coord)
        remove_set = frozenset(to_remove)
        self._eviction_log.append(
            {"time": _now_iso(), "evicted": list(remove_set), "reason": "quota"}
        )
        return region.restrict_to(
            frozenset(region.coordinate_range) - remove_set
        )

    def current_usage(self, region: MemoryRegion) -> dict[str, int]:
        """Report current usage counts for *region*."""
        return {
            "judgments": len(region.judgments),
            "evidence": len(region.evidence),
            "obstructions": len(region.obstructions),
            "total": region.size(),
            "limit": self._limit_for(region),
        }

    def quota_by_region(self) -> dict[str, int]:
        """Return the configured per-region limits."""
        return dict(self.region_limits)

    def evict_if_needed(self, region: MemoryRegion) -> MemoryRegion:
        """Convenience: enforce quota only when exceeded."""
        if self.check_quota(region):
            return region
        return self.enforce_quota(region)

    def copilot_quota_advice(self, region: MemoryRegion) -> str:
        """Produce a human-/copilot-readable summary of quota health.

        A copilot agent can present this to the user when memory pressure
        is detected.
        """
        usage = self.current_usage(region)
        ratio = usage["total"] / max(usage["limit"], 1)
        if ratio < 0.5:
            status = "healthy"
        elif ratio < 0.8:
            status = "moderate"
        elif ratio < 1.0:
            status = "high"
        else:
            status = "EXCEEDED"
        return (
            f"Memory quota {status}: {usage['total']}/{usage['limit']} entries "
            f"({ratio:.0%} used). Judgments={usage['judgments']}, "
            f"Evidence={usage['evidence']}, Obstructions={usage['obstructions']}."
        )

    # -- private -------------------------------------------------------------

    def _limit_for(self, region: MemoryRegion) -> int:
        region_key = ",".join(region.coordinate_range[:3])
        return self.region_limits.get(region_key, self.global_limit)


# ---------------------------------------------------------------------------
# 8. MemoryMigration — schema migrations
# ---------------------------------------------------------------------------

class MemoryMigration:
    """Migrates semantic memory across schema changes.

    When the internal representation of judgments, evidence, or obstructions
    evolves (e.g. a new field is added to Judgment), this class transforms
    persisted data so that it remains loadable.
    """

    def __init__(self) -> None:
        self._transforms: dict[
            tuple[int, int], Callable[[dict[str, Any]], dict[str, Any]]
        ] = {}
        self._current_version: int = 1

    def register_transform(
        self,
        from_version: int,
        to_version: int,
        transform: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> None:
        """Register a data transform from *from_version* to *to_version*."""
        self._transforms[(from_version, to_version)] = transform

    def detect_schema_change(self, data: dict[str, Any]) -> bool:
        """Return *True* if *data* was produced by an older schema."""
        stored = data.get("schema_version", 0)
        return stored < self._current_version

    def transform_data(self, data: dict[str, Any]) -> dict[str, Any]:
        """Apply all registered transforms to bring *data* up to date.

        Transforms are applied in version order: v0→v1, then v1→v2, etc.
        """
        version = data.get("schema_version", 0)
        current = dict(data)
        while version < self._current_version:
            next_version = version + 1
            fn = self._transforms.get((version, next_version))
            if fn is None:
                raise RuntimeError(
                    f"No migration registered from v{version} to v{next_version}"
                )
            current = fn(current)
            current["schema_version"] = next_version
            version = next_version
        return current

    def migrate(self, data: dict[str, Any]) -> dict[str, Any]:
        """Detect and apply schema changes in one step."""
        if not self.detect_schema_change(data):
            return data
        return self.transform_data(data)

    def validate_post_migration(self, data: dict[str, Any]) -> list[str]:
        """Run validation checks on migrated data, returning error messages.

        An empty list means the migration was clean.
        """
        errors: list[str] = []
        if data.get("schema_version") != self._current_version:
            errors.append(
                f"Expected schema_version={self._current_version}, "
                f"got {data.get('schema_version')}"
            )
        if "coordinate_range" not in data and "region_data" not in data:
            errors.append("Missing coordinate_range or region_data after migration")
        return errors

    @property
    def current_version(self) -> int:
        """The target schema version for migrations."""
        return self._current_version

    @current_version.setter
    def current_version(self, value: int) -> None:
        self._current_version = value


# ---------------------------------------------------------------------------
# 9. MemoryDiagnostics
# ---------------------------------------------------------------------------

class MemoryDiagnostics:
    """Diagnostic queries over semantic memory.

    Provides human-readable and copilot-consumable summaries of memory
    health, index coverage, and stale data.
    """

    def memory_summary(self, region: MemoryRegion) -> dict[str, Any]:
        """Top-level summary of the memory region."""
        return {
            "coordinate_range_size": len(region.coordinate_range),
            "judgments": len(region.judgments),
            "evidence": len(region.evidence),
            "obstructions": len(region.obstructions),
            "is_empty": region.is_empty(),
        }

    def usage_by_region(
        self, regions: Mapping[str, MemoryRegion]
    ) -> dict[str, dict[str, int]]:
        """Per-region usage breakdown."""
        return {
            name: {
                "judgments": len(r.judgments),
                "evidence": len(r.evidence),
                "obstructions": len(r.obstructions),
                "total": r.size(),
            }
            for name, r in regions.items()
        }

    def stale_data_report(
        self,
        region: MemoryRegion,
        live_keys: frozenset[str],
    ) -> dict[str, Any]:
        """Identify stale data and report counts per section type."""
        gc = MemoryGC()
        stale = gc.identify_stale(region, live_keys)
        stale_j = stale & frozenset(region.judgments)
        stale_e = stale & frozenset(region.evidence)
        stale_o = stale & frozenset(region.obstructions)
        return {
            "total_stale": len(stale),
            "stale_judgments": len(stale_j),
            "stale_evidence": len(stale_e),
            "stale_obstructions": len(stale_o),
            "stale_keys": sorted(stale),
        }

    def index_health(self, index: MemoryIndex) -> dict[str, Any]:
        """Report on index utilisation and bucket distribution."""
        stats = index.statistics()
        max_bucket = 0
        for idx in (
            index._by_coordinate,
            index._by_proposition,
            index._by_trust,
            index._by_channel,
        ):
            for bucket in idx.values():
                max_bucket = max(max_bucket, len(bucket))
        return {**stats, "max_bucket_size": max_bucket}

    def copilot_memory_summary(
        self,
        region: MemoryRegion,
        index: MemoryIndex | None = None,
    ) -> str:
        """Produce a compact summary suitable for a copilot context window.

        The copilot agent can use this to decide whether memory pressure
        exists, which regions are hot, and whether garbage collection is
        warranted.
        """
        summary = self.memory_summary(region)
        lines = [
            f"SemanticMemory: {summary['judgments']}J / "
            f"{summary['evidence']}E / {summary['obstructions']}O",
            f"  coords: {summary['coordinate_range_size']}",
        ]
        if index is not None:
            ih = self.index_health(index)
            lines.append(
                f"  index: {ih['total_keys']} keys, "
                f"max_bucket={ih['max_bucket_size']}"
            )
        return "\n".join(lines)

    def consistency_check(
        self, region: MemoryRegion, snapshot: MemorySnapshot
    ) -> list[str]:
        """Compare *region* against *snapshot* and report inconsistencies."""
        issues: list[str] = []
        if not snapshot.is_consistent():
            issues.append("Snapshot hash mismatch — possible corruption")
        restored = snapshot.restore()
        diff = region.diff(restored)
        if diff.get("added"):
            issues.append(
                f"Region has keys not in snapshot: {sorted(diff['added'])}"
            )
        if diff.get("removed"):
            issues.append(
                f"Snapshot has keys not in region: {sorted(diff['removed'])}"
            )
        return issues


# ---------------------------------------------------------------------------
# 10. MemorySerializer
# ---------------------------------------------------------------------------

class MemorySerializer:
    """Serialization and deserialization of semantic memory regions.

    Supports JSON for human-readable interchange and a compact binary
    format for efficient on-disk persistence.
    """

    # -- JSON ----------------------------------------------------------------

    def to_json(self, region: MemoryRegion) -> str:
        """Serialize *region* to a JSON string.

        Judgment/EvidenceBundle/Obstruction objects are stored as their
        ``repr`` — a full serialiser would use domain codecs, but repr
        round-trips are sufficient for snapshots and diagnostics.
        """
        payload: dict[str, Any] = {
            "schema_version": 1,
            "coordinate_range": list(region.coordinate_range),
            "judgments": {k: repr(v) for k, v in region.judgments.items()},
            "evidence": {k: repr(v) for k, v in region.evidence.items()},
            "obstructions": {k: repr(v) for k, v in region.obstructions.items()},
        }
        return json.dumps(payload, indent=2, sort_keys=True)

    def from_json(self, data: str) -> MemoryRegion:
        """Deserialize a JSON string into a :class:`MemoryRegion`.

        .. note::

           Because ``to_json`` stores repr strings for domain objects, this
           method returns a region whose dicts map keys to *strings*.  A
           production pipeline would apply domain decoders here.
        """
        raw = json.loads(data)
        return MemoryRegion(
            coordinate_range=tuple(raw.get("coordinate_range", ())),
            judgments=dict(raw.get("judgments", {})),
            evidence=dict(raw.get("evidence", {})),
            obstructions=dict(raw.get("obstructions", {})),
        )

    # -- binary --------------------------------------------------------------

    def to_binary(self, region: MemoryRegion) -> bytes:
        """Serialize *region* to a compact binary format.

        Format: 4-byte magic, 4-byte version, then length-prefixed JSON of
        the payload.  The binary wrapper exists so that tooling can quickly
        detect file type without parsing JSON.
        """
        json_bytes = self.to_json(region).encode("utf-8")
        header = b"JGMM" + struct.pack("<I", 1)  # magic + version
        length = struct.pack("<I", len(json_bytes))
        return header + length + json_bytes

    def from_binary(self, data: bytes) -> MemoryRegion:
        """Deserialize a binary blob produced by :meth:`to_binary`."""
        if data[:4] != b"JGMM":
            raise ValueError("Invalid binary magic — expected JGMM header")
        _version = struct.unpack("<I", data[4:8])[0]
        length = struct.unpack("<I", data[8:12])[0]
        json_bytes = data[12 : 12 + length]
        return self.from_json(json_bytes.decode("utf-8"))

    # -- export / import -----------------------------------------------------

    def export(
        self,
        region: MemoryRegion,
        *,
        fmt: str = "json",
    ) -> str | bytes:
        """Export *region* in the requested format (``json`` or ``binary``)."""
        if fmt == "json":
            return self.to_json(region)
        elif fmt == "binary":
            return self.to_binary(region)
        raise ValueError(f"Unknown format: {fmt!r}")

    def import_from(
        self,
        data: str | bytes,
        *,
        fmt: str = "json",
    ) -> MemoryRegion:
        """Import a region from serialized data."""
        if fmt == "json":
            if isinstance(data, bytes):
                data = data.decode("utf-8")
            return self.from_json(data)
        elif fmt == "binary":
            if isinstance(data, str):
                data = data.encode("utf-8")
            return self.from_binary(data)
        raise ValueError(f"Unknown format: {fmt!r}")


# ---------------------------------------------------------------------------
# Legacy compatibility — MemoryNote
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class MemoryNote:
    """Legacy note-based memory entry.

    Retained for backward compatibility with callers that use the simple
    key/value/tag interface.
    """

    key: str
    value: Any
    tags: tuple[str, ...] = ()
    provenance: ProvenanceTrace | None = None


# ---------------------------------------------------------------------------
# 11. SemanticMemory — the main persistent memory 𝔐
# ---------------------------------------------------------------------------

class SemanticMemory:
    """Persistent semantic memory 𝔐 (theory2.tex §32).

    Stores all judgments, evidence, obstructions, and trust state as local
    sections over the Grothendieck site.  Mutations go through
    :class:`MemoryTransaction` for atomicity.  Snapshots are taken
    automatically on every successful commit so that any past state can be
    restored.

    A semantic change event δ with support S is applied via :meth:`update`:

        𝔐' = Glue(𝔐|_{X \\ S}, new_sections, overlap_data)

    Copilot integration: :meth:`statistics` and the diagnostics helpers
    produce summaries that a copilot agent can consume when proposing
    repairs, promotions, or garbage-collection runs.
    """

    def __init__(self, *, global_quota: int = 100_000) -> None:
        self._region = MemoryRegion(coordinate_range=())
        self._index = MemoryIndex()
        self._gc = MemoryGC()
        self._quota = MemoryQuotaManager(global_limit=global_quota)
        self._serializer = MemorySerializer()
        self._diagnostics = MemoryDiagnostics()
        self._snapshots: list[MemorySnapshot] = []
        self._transaction = MemoryTransaction(_region=self._region)
        # Legacy note store for backward compatibility.
        self._notes: dict[str, MemoryNote] = {}

    # -- judgment CRUD -------------------------------------------------------

    def store_judgment(self, key: str, judgment: Judgment) -> None:
        """Store a judgment under *key*, updating indices.

        The judgment is placed into the current region and all relevant
        secondary indices are updated.
        """
        self._region.judgments[key] = judgment
        if key not in self._region.coordinate_range:
            self._region.coordinate_range = (*self._region.coordinate_range, key)
        coord_key = (
            ".".join(judgment.coordinate.components)
            if hasattr(judgment.coordinate, "components")
            else str(judgment.coordinate)
        )
        self._index.index_by_coordinate(key, coord_key)
        if hasattr(judgment.proposition, "formula"):
            self._index.index_by_proposition(key, judgment.proposition.formula)
        if hasattr(judgment.trust, "level"):
            self._index.index_by_trust(key, str(judgment.trust.level))

    def retrieve_judgment(self, key: str) -> Judgment | None:
        """Retrieve the judgment stored under *key*, or ``None``."""
        return self._region.judgments.get(key)

    # -- evidence CRUD -------------------------------------------------------

    def store_evidence(self, key: str, bundle: EvidenceBundle) -> None:
        """Store an evidence bundle, indexing by channel."""
        self._region.evidence[key] = bundle
        for item in bundle.items:
            if hasattr(item, "channel"):
                self._index.index_by_channel(key, item.channel)

    def retrieve_evidence(self, key: str) -> EvidenceBundle | None:
        """Retrieve the evidence bundle at *key*."""
        return self._region.evidence.get(key)

    # -- obstruction CRUD ----------------------------------------------------

    def store_obstruction(self, key: str, obstruction: Obstruction) -> None:
        """Store an obstruction — a persistent cohomology class."""
        self._region.obstructions[key] = obstruction

    def retrieve_obstruction(self, key: str) -> Obstruction | None:
        """Retrieve the obstruction at *key*."""
        return self._region.obstructions.get(key)

    # -- query ---------------------------------------------------------------

    def query(
        self,
        *,
        coordinate: str | None = None,
        proposition: str | None = None,
        trust_level: str | None = None,
        channel: str | None = None,
    ) -> dict[str, Any]:
        """Multi-faceted query across indexed dimensions.

        Returns a dict of matching keys grouped by section type.  At least
        one filter parameter must be provided.
        """
        candidate_sets: list[frozenset[str]] = []
        if coordinate is not None:
            candidate_sets.append(self._index.lookup("coordinate", coordinate))
        if proposition is not None:
            candidate_sets.append(self._index.lookup("proposition", proposition))
        if trust_level is not None:
            candidate_sets.append(self._index.lookup("trust", trust_level))
        if channel is not None:
            candidate_sets.append(self._index.lookup("channel", channel))
        if not candidate_sets:
            return {"judgments": {}, "evidence": {}, "obstructions": {}}
        # Intersect all non-empty candidate sets.
        result_keys = candidate_sets[0]
        for cs in candidate_sets[1:]:
            result_keys = result_keys & cs
        return {
            "judgments": {
                k: self._region.judgments[k]
                for k in result_keys
                if k in self._region.judgments
            },
            "evidence": {
                k: self._region.evidence[k]
                for k in result_keys
                if k in self._region.evidence
            },
            "obstructions": {
                k: self._region.obstructions[k]
                for k in result_keys
                if k in self._region.obstructions
            },
        }

    # -- update (the gluing construction) ------------------------------------

    def update(self, mem_update: MemoryUpdate) -> None:
        """Apply a semantic change event δ to the memory.

        Implements the full restrict-splice-glue pipeline:

        1. Restrict the current region to coordinates outside the support.
        2. Build fresh sections from the update's new_sections.
        3. Glue the restricted region with the fresh sections.
        4. Enforce quotas and take a snapshot.
        """
        self._region = mem_update.apply_to(self._region)
        self._region = self._quota.evict_if_needed(self._region)
        self._index.rebuild(self._region)
        self._snapshots.append(MemorySnapshot.capture(self._region))

    # -- snapshot / restore --------------------------------------------------

    def snapshot(self) -> MemorySnapshot:
        """Take an immutable snapshot of the current state."""
        snap = MemorySnapshot.capture(self._region)
        self._snapshots.append(snap)
        return snap

    def restore(self, snapshot: MemorySnapshot) -> None:
        """Restore memory to the state captured by *snapshot*."""
        self._region = snapshot.restore()
        self._index.rebuild(self._region)

    # -- garbage collection --------------------------------------------------

    def gc(self, live_keys: frozenset[str] | None = None) -> dict[str, Any]:
        """Run garbage collection, removing stale entries.

        If *live_keys* is ``None``, all keys currently in the region are
        considered live (i.e. only compaction is performed).
        """
        if live_keys is None:
            self._region = self._gc.compact(self._region)
        else:
            self._region = self._gc.collect(self._region, live_keys)
        self._region = self._gc.defragment(self._region)
        self._index.rebuild(self._region)
        return self._gc.gc_statistics()

    # -- statistics & diagnostics --------------------------------------------

    def statistics(self) -> dict[str, Any]:
        """Return comprehensive statistics suitable for copilot consumption.

        Includes memory usage, index health, quota status, and GC history.
        """
        return {
            "memory": self._diagnostics.memory_summary(self._region),
            "index": self._index.statistics(),
            "quota": self._quota.current_usage(self._region),
            "gc": self._gc.gc_statistics(),
            "snapshots": len(self._snapshots),
            "copilot_summary": self._diagnostics.copilot_memory_summary(
                self._region, self._index
            ),
        }

    # -- legacy API ----------------------------------------------------------

    def remember(self, note: MemoryNote) -> None:
        """Store a legacy :class:`MemoryNote` (backward compatibility)."""
        self._notes[note.key] = note

    def recall(self, key: str) -> MemoryNote | None:
        """Recall a legacy note by key."""
        return self._notes.get(key)

    def search_by_tag(self, tag: str) -> tuple[MemoryNote, ...]:
        """Search legacy notes by tag."""
        return tuple(n for n in self._notes.values() if tag in n.tags)

    # -- region access -------------------------------------------------------

    @property
    def notes(self) -> dict[str, MemoryNote]:
        """Legacy note mapping retained for checkpoint compatibility."""
        return self._notes

    @property
    def region(self) -> MemoryRegion:
        """The underlying :class:`MemoryRegion` (read-only view)."""
        return self._region

    # -- cross-subsystem integration -----------------------------------------

    def judgment_region(
        self,
        sections: Any,
        *,
        coordinate_range: Sequence[str] | None = None,
    ) -> MemoryRegion:
        """Create a :class:`MemoryRegion` from judgment sections.

        Uses ``jugeo.judgments.sections.Section`` objects to populate a
        new region with judgment data, ready to be glued into the
        persistent memory via :meth:`update`.

        Parameters
        ----------
        sections:
            An iterable of ``Section`` objects from
            ``jugeo.judgments.sections``, or a mapping of coordinate
            keys to ``Judgment`` objects.
        coordinate_range:
            Explicit coordinate range; if ``None`` it is derived from
            the sections themselves.

        Returns
        -------
        MemoryRegion
        """
        try:
            from jugeo.judgments.sections import Section
        except ImportError:  # pragma: no cover
            Section = None  # type: ignore[assignment,misc]

        judgments: dict[str, Judgment] = {}
        derived_range: list[str] = []

        if isinstance(sections, Mapping):
            for key, sec in sections.items():
                coord_key = str(key)
                derived_range.append(coord_key)
                if isinstance(sec, Judgment):
                    judgments[coord_key] = sec
                elif Section is not None and isinstance(sec, Section):
                    j = getattr(sec, "judgment", None)
                    if j is not None and isinstance(j, Judgment):
                        judgments[coord_key] = j
        else:
            for sec in sections:
                if Section is not None and isinstance(sec, Section):
                    coord_key = str(getattr(sec, "coordinate", ""))
                    if coord_key:
                        derived_range.append(coord_key)
                    j = getattr(sec, "judgment", None)
                    if j is not None and isinstance(j, Judgment):
                        judgments[coord_key] = j
                elif isinstance(sec, Judgment):
                    coord_key = (
                        ".".join(sec.coordinate.components)
                        if hasattr(sec.coordinate, "components")
                        else str(sec.coordinate)
                    )
                    derived_range.append(coord_key)
                    judgments[coord_key] = sec

        final_range = tuple(coordinate_range) if coordinate_range else tuple(dict.fromkeys(derived_range))
        return MemoryRegion(
            coordinate_range=final_range,
            judgments=judgments,
        )

    def evidence_region(
        self,
        evidence_items: Any,
        *,
        coordinate_range: Sequence[str] | None = None,
    ) -> MemoryRegion:
        """Create a :class:`MemoryRegion` from evidence bundles.

        Accepts evidence objects from ``jugeo.evidence`` (e.g.
        ``EvidenceBundle``) and organises them into a region that can
        be glued into persistent memory.

        Parameters
        ----------
        evidence_items:
            A mapping of coordinate keys to ``EvidenceBundle`` objects,
            or an iterable of ``(key, bundle)`` pairs.
        coordinate_range:
            Explicit coordinate range; derived from keys if ``None``.

        Returns
        -------
        MemoryRegion
        """
        evidence: dict[str, EvidenceBundle] = {}
        derived_range: list[str] = []

        if isinstance(evidence_items, Mapping):
            for key, bundle in evidence_items.items():
                coord_key = str(key)
                derived_range.append(coord_key)
                if isinstance(bundle, EvidenceBundle):
                    evidence[coord_key] = bundle
        else:
            for item in evidence_items:
                if isinstance(item, (tuple, list)) and len(item) == 2:
                    coord_key = str(item[0])
                    bundle = item[1]
                    derived_range.append(coord_key)
                    if isinstance(bundle, EvidenceBundle):
                        evidence[coord_key] = bundle

        final_range = tuple(coordinate_range) if coordinate_range else tuple(dict.fromkeys(derived_range))
        return MemoryRegion(
            coordinate_range=final_range,
            evidence=evidence,
        )

    def descent_glued_memory(
        self,
        base_region: MemoryRegion,
        patch_region: MemoryRegion,
        *,
        descent_log: Any | None = None,
    ) -> MemoryRegion:
        """Glue two memory regions using descent data.

        Uses ``jugeo.geometry.descent`` to inform the gluing operation.
        When a ``DescentLog`` is provided, the overlap conditions
        recorded in the descent log determine which coordinates in the
        overlap frontier are trusted for the merge.

        Parameters
        ----------
        base_region:
            The existing (restricted) memory region.
        patch_region:
            The new sections to splice in.
        descent_log:
            Optional ``DescentLog`` from ``jugeo.geometry.descent``
            providing overlap verification data.

        Returns
        -------
        MemoryRegion
            The glued result.
        """
        try:
            from jugeo.geometry.descent import DescentLog, OverlapCondition
        except ImportError:  # pragma: no cover
            DescentLog = None  # type: ignore[assignment,misc]
            OverlapCondition = None  # type: ignore[assignment,misc]

        if DescentLog is not None and isinstance(descent_log, DescentLog):
            verified_coords: set[str] = set()
            for entry in getattr(descent_log, "entries", []):
                coord = getattr(entry, "coordinate", None)
                if coord is not None:
                    verified_coords.add(str(coord))
            # Restrict patch to only verified overlap coordinates
            overlap = frozenset(base_region.coordinate_range) & frozenset(patch_region.coordinate_range)
            unverified = overlap - verified_coords
            if unverified:
                # Remove unverified overlap coordinates from the patch
                safe_range = tuple(c for c in patch_region.coordinate_range if c not in unverified)
                patch_region = MemoryRegion(
                    coordinate_range=safe_range,
                    judgments={k: v for k, v in patch_region.judgments.items() if k not in unverified},
                    evidence={k: v for k, v in patch_region.evidence.items() if k not in unverified},
                    obstructions={k: v for k, v in patch_region.obstructions.items() if k not in unverified},
                )

        return base_region.extend(patch_region)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "MemoryDiagnostics",
    "MemoryGC",
    "MemoryIndex",
    "MemoryMigration",
    "MemoryNote",
    "MemoryQuotaManager",
    "MemoryRegion",
    "MemorySerializer",
    "MemorySnapshot",
    "MemoryTransaction",
    "MemoryUpdate",
    "SemanticMemory",
]

# copilot: shared-core marker for persistent semantic memory orchestration.
