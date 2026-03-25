"""Data models for JuGeo's incremental computation infrastructure.

Covers file state tracking, change detection, delta records, cache entries,
invalidation events, and lazy-load status bookkeeping.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ChangeKind(str, Enum):
    """Kind of change detected for a file."""

    CREATED = "created"
    MODIFIED = "modified"
    DELETED = "deleted"
    RENAMED = "renamed"
    MOVED = "moved"


class LazyLoadStatus(str, Enum):
    """How much of a file has been loaded into memory."""

    UNLOADED = "unloaded"
    HEADER_ONLY = "header_only"
    IMPORTS_ONLY = "imports_only"
    FULL_AST = "full_ast"
    COORDINATES_EXTRACTED = "coordinates_extracted"


class InvalidationStrategy(str, Enum):
    """Strategy used when cascading an invalidation wave."""

    FULL = "FULL"
    CONTRACT_BOUNDED = "CONTRACT_BOUNDED"
    TIERED = "TIERED"
    PROBABILISTIC = "PROBABILISTIC"


# ---------------------------------------------------------------------------
# File tracking
# ---------------------------------------------------------------------------


@dataclass
class FileState:
    """Snapshot of a single source file at a point in time."""

    path: str
    content_hash: str
    size_bytes: int
    modified_at: float
    parsed: bool = False
    coordinate_ids: list[str] = field(default_factory=list)
    import_edges: list[tuple[str, str]] = field(default_factory=list)
    last_parsed_at: float | None = None

    # ------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "content_hash": self.content_hash,
            "size_bytes": self.size_bytes,
            "modified_at": self.modified_at,
            "parsed": self.parsed,
            "coordinate_ids": list(self.coordinate_ids),
            "import_edges": [list(e) for e in self.import_edges],
            "last_parsed_at": self.last_parsed_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> FileState:
        return cls(
            path=d["path"],
            content_hash=d["content_hash"],
            size_bytes=d["size_bytes"],
            modified_at=d["modified_at"],
            parsed=d.get("parsed", False),
            coordinate_ids=list(d.get("coordinate_ids", [])),
            import_edges=[tuple(e) for e in d.get("import_edges", [])],
            last_parsed_at=d.get("last_parsed_at"),
        )


# ---------------------------------------------------------------------------
# Change modelling
# ---------------------------------------------------------------------------


@dataclass
class FileChange:
    """Describes one detected change to a tracked file."""

    path: str
    kind: ChangeKind
    old_hash: str | None = None
    new_hash: str | None = None
    old_path: str | None = None  # populated for RENAMED / MOVED

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "kind": self.kind.value,
            "old_hash": self.old_hash,
            "new_hash": self.new_hash,
            "old_path": self.old_path,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> FileChange:
        return cls(
            path=d["path"],
            kind=ChangeKind(d["kind"]),
            old_hash=d.get("old_hash"),
            new_hash=d.get("new_hash"),
            old_path=d.get("old_path"),
        )


@dataclass
class ChangeSet:
    """A grouped batch of file changes with computed downstream impact."""

    id: str
    changes: list[FileChange]
    timestamp: float
    affected_coordinates: list[str] = field(default_factory=list)
    invalidated_morphisms: list[str] = field(default_factory=list)
    invalidated_overlaps: list[str] = field(default_factory=list)

    # Convenience constructor ---------------------------------------------------
    @classmethod
    def create(cls, changes: list[FileChange]) -> ChangeSet:
        return cls(
            id=str(uuid.uuid4()),
            changes=list(changes),
            timestamp=time.time(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "changes": [c.to_dict() for c in self.changes],
            "timestamp": self.timestamp,
            "affected_coordinates": list(self.affected_coordinates),
            "invalidated_morphisms": list(self.invalidated_morphisms),
            "invalidated_overlaps": list(self.invalidated_overlaps),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ChangeSet:
        return cls(
            id=d["id"],
            changes=[FileChange.from_dict(c) for c in d.get("changes", [])],
            timestamp=d["timestamp"],
            affected_coordinates=list(d.get("affected_coordinates", [])),
            invalidated_morphisms=list(d.get("invalidated_morphisms", [])),
            invalidated_overlaps=list(d.get("invalidated_overlaps", [])),
        )


# ---------------------------------------------------------------------------
# Delta records
# ---------------------------------------------------------------------------


@dataclass
class DeltaRecord:
    """Records what changed at the coordinate / morphism level across a ChangeSet."""

    change_set_id: str
    added_coordinates: list[str] = field(default_factory=list)
    removed_coordinates: list[str] = field(default_factory=list)
    modified_coordinates: list[str] = field(default_factory=list)
    added_morphisms: list[str] = field(default_factory=list)
    removed_morphisms: list[str] = field(default_factory=list)
    modified_morphisms: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not any([
            self.added_coordinates,
            self.removed_coordinates,
            self.modified_coordinates,
            self.added_morphisms,
            self.removed_morphisms,
            self.modified_morphisms,
        ])

    def all_affected_coordinates(self) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for cid in (
            self.added_coordinates
            + self.removed_coordinates
            + self.modified_coordinates
        ):
            if cid not in seen:
                seen.add(cid)
                result.append(cid)
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "change_set_id": self.change_set_id,
            "added_coordinates": list(self.added_coordinates),
            "removed_coordinates": list(self.removed_coordinates),
            "modified_coordinates": list(self.modified_coordinates),
            "added_morphisms": list(self.added_morphisms),
            "removed_morphisms": list(self.removed_morphisms),
            "modified_morphisms": list(self.modified_morphisms),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> DeltaRecord:
        return cls(
            change_set_id=d["change_set_id"],
            added_coordinates=list(d.get("added_coordinates", [])),
            removed_coordinates=list(d.get("removed_coordinates", [])),
            modified_coordinates=list(d.get("modified_coordinates", [])),
            added_morphisms=list(d.get("added_morphisms", [])),
            removed_morphisms=list(d.get("removed_morphisms", [])),
            modified_morphisms=list(d.get("modified_morphisms", [])),
        )


# ---------------------------------------------------------------------------
# Invalidation
# ---------------------------------------------------------------------------


@dataclass
class InvalidationEvent:
    """Records a single propagated invalidation wave."""

    source_coordinate: str
    invalidated_coordinates: list[str]
    invalidation_depth: int
    strategy: str
    timestamp: float

    @classmethod
    def create(
        cls,
        source: str,
        invalidated: list[str],
        depth: int,
        strategy: InvalidationStrategy,
    ) -> InvalidationEvent:
        return cls(
            source_coordinate=source,
            invalidated_coordinates=list(invalidated),
            invalidation_depth=depth,
            strategy=strategy.value,
            timestamp=time.time(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_coordinate": self.source_coordinate,
            "invalidated_coordinates": list(self.invalidated_coordinates),
            "invalidation_depth": self.invalidation_depth,
            "strategy": self.strategy,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> InvalidationEvent:
        return cls(
            source_coordinate=d["source_coordinate"],
            invalidated_coordinates=list(d.get("invalidated_coordinates", [])),
            invalidation_depth=d["invalidation_depth"],
            strategy=d["strategy"],
            timestamp=d["timestamp"],
        )


@dataclass
class InvalidationPolicy:
    """Governs how invalidation cascades are constrained."""

    max_cascade_depth: int = 10
    use_contract_boundaries: bool = True
    tiered_delays: dict[int, float] = field(
        default_factory=lambda: {1: 0.0, 2: 0.5, 3: 2.0}
    )
    probabilistic_threshold: int = 1000  # cascade count above which to sample

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_cascade_depth": self.max_cascade_depth,
            "use_contract_boundaries": self.use_contract_boundaries,
            "tiered_delays": {str(k): v for k, v in self.tiered_delays.items()},
            "probabilistic_threshold": self.probabilistic_threshold,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> InvalidationPolicy:
        raw_delays = d.get("tiered_delays", {})
        tiered: dict[int, float] = {int(k): float(v) for k, v in raw_delays.items()}
        return cls(
            max_cascade_depth=d.get("max_cascade_depth", 10),
            use_contract_boundaries=d.get("use_contract_boundaries", True),
            tiered_delays=tiered,
            probabilistic_threshold=d.get("probabilistic_threshold", 1000),
        )


# ---------------------------------------------------------------------------
# Cache entries
# ---------------------------------------------------------------------------


@dataclass
class CacheEntry:
    """A single entry in the incremental computation cache."""

    key: str
    value_hash: str
    created_at: float
    last_accessed_at: float
    access_count: int = 0
    is_valid: bool = True
    depends_on: list[str] = field(default_factory=list)

    @classmethod
    def create(cls, key: str, value_hash: str, depends_on: list[str] | None = None) -> CacheEntry:
        now = time.time()
        return cls(
            key=key,
            value_hash=value_hash,
            created_at=now,
            last_accessed_at=now,
            access_count=1,
            is_valid=True,
            depends_on=list(depends_on or []),
        )

    def touch(self) -> None:
        self.last_accessed_at = time.time()
        self.access_count += 1

    def invalidate(self) -> None:
        self.is_valid = False

    def age_seconds(self) -> float:
        return time.time() - self.created_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "value_hash": self.value_hash,
            "created_at": self.created_at,
            "last_accessed_at": self.last_accessed_at,
            "access_count": self.access_count,
            "is_valid": self.is_valid,
            "depends_on": list(self.depends_on),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CacheEntry:
        return cls(
            key=d["key"],
            value_hash=d["value_hash"],
            created_at=d["created_at"],
            last_accessed_at=d["last_accessed_at"],
            access_count=d.get("access_count", 0),
            is_valid=d.get("is_valid", True),
            depends_on=list(d.get("depends_on", [])),
        )


@dataclass
class CacheStatistics:
    """Aggregate statistics for the incremental cache."""

    total_entries: int
    valid_entries: int
    hits: int
    misses: int
    evictions: int
    invalidations: int
    hit_rate: float
    avg_age_s: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_entries": self.total_entries,
            "valid_entries": self.valid_entries,
            "hits": self.hits,
            "misses": self.misses,
            "evictions": self.evictions,
            "invalidations": self.invalidations,
            "hit_rate": self.hit_rate,
            "avg_age_s": self.avg_age_s,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CacheStatistics:
        return cls(
            total_entries=d["total_entries"],
            valid_entries=d["valid_entries"],
            hits=d["hits"],
            misses=d["misses"],
            evictions=d["evictions"],
            invalidations=d["invalidations"],
            hit_rate=d["hit_rate"],
            avg_age_s=d["avg_age_s"],
        )

    @classmethod
    def empty(cls) -> CacheStatistics:
        return cls(
            total_entries=0,
            valid_entries=0,
            hits=0,
            misses=0,
            evictions=0,
            invalidations=0,
            hit_rate=0.0,
            avg_age_s=0.0,
        )
