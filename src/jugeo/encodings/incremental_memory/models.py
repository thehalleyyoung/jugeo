"""Core data models for the incremental_memory encoding package — theory2.tex Ch34.

This module defines the foundational data models used throughout the
incremental_memory encoding subsystem, developed with copilot assistance.
Models represent the mathematical objects from theory2.tex Chapter 34:
support sets, incremental updates, change events, invalidation cascades,
and persistent memory state.

The key construction is M' = Glue(M|_{X\\S}, new_sections, overlap_data),
where S is the support set of the change event.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any

logger = logging.getLogger(__name__)

try:
    from jugeo.geometry.supports import SupportSet, SupportRegion
except ImportError:
    SupportSet = Any  # type: ignore
    SupportRegion = Any  # type: ignore

try:
    from jugeo.geometry.site import Coordinate, CoordinateObject
except ImportError:
    Coordinate = Any  # type: ignore
    CoordinateObject = Any  # type: ignore

try:
    from jugeo.judgments.judgment_terms import Provenance, ProvenanceSource, JudgmentStatus
except ImportError:
    Provenance = Any  # type: ignore
    ProvenanceSource = Any  # type: ignore
    JudgmentStatus = Any  # type: ignore

try:
    from jugeo.runtime.invalidation import InvalidationEvent, TriggerKind
except ImportError:
    InvalidationEvent = Any  # type: ignore
    TriggerKind = Any  # type: ignore

try:
    from jugeo.runtime.memory import MemoryRegion, MemorySnapshot, SemanticMemory
except ImportError:
    MemoryRegion = Any  # type: ignore
    MemorySnapshot = Any  # type: ignore
    SemanticMemory = Any  # type: ignore


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class ChangeEventKind(Enum):
    """Classifies the semantic operation performed by a ChangeEvent.

    Each value corresponds to a distinct primitive operation in the
    incremental update algebra defined in theory2.tex section 34.3.  The
    distinction matters because different operations require different
    invalidation strategies: for example, a DELETE event must propagate
    an invalidation wave over the entire support set, while an ANNOTATE
    event may only affect metadata without triggering geometric recomputation.

    Attributes:
        INSERT: A new section or coordinate has been added to memory.
            The support set of the event describes the newly occupied region.
        UPDATE: An existing section's content has changed in place.
            The support set must overlap with the old section's support.
        DELETE: An existing section or coordinate has been removed.
            Downstream dependents of the deleted region must be invalidated.
        MERGE: Two or more sections have been merged into a single section.
            The resulting support is the union of the inputs' supports.
        SPLIT: A single section has been divided into two or more pieces.
            Each piece inherits a contiguous sub-region of the original support.
        RELOCATE: A section has moved to a new coordinate without changing
            its content.  The old coordinate is freed; the new one is occupied.
        ANNOTATE: Metadata attached to an existing section has changed
            without modifying the section's primary content or geometry.
    """

    INSERT = auto()
    UPDATE = auto()
    DELETE = auto()
    MERGE = auto()
    SPLIT = auto()
    RELOCATE = auto()
    ANNOTATE = auto()


class RegionType(Enum):
    """Classifies the geometric shape of an EncodingSupportSet.

    Region types allow the incremental_memory subsystem to choose efficient
    algorithms for overlap checking, diameter computation, and complement
    operations.  The classification follows the taxonomy in theory2.tex
    section 34.1.

    Attributes:
        STAR: A star-shaped region centred on one distinguished coordinate.
            Every coordinate in the set is directly adjacent to the centre.
        BALL: A metric ball of some radius around a central coordinate.
            All coordinates within the specified radius are included.
        CONE: A cone-shaped region extending from a base coordinate in one
            or more directions through the coordinate graph.
        ARBITRARY: An arbitrary finite set of coordinates with no guaranteed
            geometric structure.  Algorithms must treat it as a general set.
    """

    STAR = auto()
    BALL = auto()
    CONE = auto()
    ARBITRARY = auto()


# ---------------------------------------------------------------------------
# EncodingSupportSet
# ---------------------------------------------------------------------------

@dataclass
class EncodingSupportSet:
    """Wraps jugeo.geometry.supports.SupportSet with encoding-layer semantics.

    EncodingSupportSet is the primary representation of coordinate support
    within the incremental_memory encoding package, developed with copilot
    assistance to bridge the gap between the raw geometry layer and the
    higher-level encoding operations defined in theory2.tex Chapter 34.

    Unlike the underlying SupportSet, EncodingSupportSet stores coordinates
    as an immutable frozenset of string identifiers, carries an explicit
    RegionType annotation, and provides set-algebraic operations (union,
    intersect, complement) required by the Glue construction.  Instances
    are intentionally immutable with respect to their coords field to
    allow safe sharing across threads.

    Attributes:
        coords: The set of coordinate identifier strings belonging to this
            support set.
        region_type: The geometric shape classification of this support.
        metadata: Arbitrary key-value metadata for encoding-specific use.
    """

    coords: frozenset[str]
    region_type: RegionType = RegionType.ARBITRARY
    metadata: dict[str, Any] = field(default_factory=dict)

    def union(self, other: EncodingSupportSet) -> EncodingSupportSet:
        """Return the union of this support set and another.

        The resulting region type is ARBITRARY unless both sets share the
        same non-ARBITRARY type, in which case that type is preserved.

        Args:
            other: The other EncodingSupportSet to union with.

        Returns:
            A new EncodingSupportSet whose coords is the set union of the
            two inputs' coords.
        """
        merged_type = (
            self.region_type
            if self.region_type == other.region_type
            and self.region_type != RegionType.ARBITRARY
            else RegionType.ARBITRARY
        )
        combined_meta = {**self.metadata, **other.metadata}
        return EncodingSupportSet(
            coords=self.coords | other.coords,
            region_type=merged_type,
            metadata=combined_meta,
        )

    def intersect(self, other: EncodingSupportSet) -> EncodingSupportSet:
        """Return the intersection of this support set and another.

        The resulting region type is ARBITRARY, since the intersection of
        two geometrically-typed regions does not generally preserve either
        type without further analysis.

        Args:
            other: The other EncodingSupportSet to intersect with.

        Returns:
            A new EncodingSupportSet whose coords is the set intersection of
            the two inputs' coords.
        """
        return EncodingSupportSet(
            coords=self.coords & other.coords,
            region_type=RegionType.ARBITRARY,
            metadata={**self.metadata},
        )

    def complement_in(self, universe: EncodingSupportSet) -> EncodingSupportSet:
        """Return the complement of this set within a given universe.

        The complement is universe.coords minus self.coords.  The region
        type of the complement is set to ARBITRARY because the geometric
        structure of the complement is generally unknown.

        Args:
            universe: The EncodingSupportSet acting as the total universe.

        Returns:
            A new EncodingSupportSet containing all coords in the universe
            that are not in this set.
        """
        return EncodingSupportSet(
            coords=universe.coords - self.coords,
            region_type=RegionType.ARBITRARY,
            metadata={},
        )

    def is_compact(self) -> bool:
        """Return True if this support set is considered compact.

        Compactness in the encoding layer is a heuristic notion: a set is
        compact if it has at most 1000 coordinates and its region type is
        not ARBITRARY (i.e., it has a known geometric structure that bounds
        its extent).

        Returns:
            True if the set has a structured geometry and at most 1000 coords.
        """
        return len(self.coords) <= 1000 and self.region_type != RegionType.ARBITRARY

    def diameter(self) -> int:
        """Return the diameter of this support set, approximated as cardinality.

        The true geometric diameter depends on a metric that is not available
        at the encoding layer.  The cardinality of the coordinate set is used
        as a conservative upper bound on the diameter.

        Returns:
            The number of coordinates in this support set.
        """
        return len(self.coords)

    def contains(self, coord: str) -> bool:
        """Test whether a coordinate string belongs to this support set.

        Args:
            coord: The coordinate identifier to test.

        Returns:
            True if coord is in self.coords, False otherwise.
        """
        return coord in self.coords

    def expand_star(self, center: str, neighbors: list[str]) -> EncodingSupportSet:
        """Return a new star-shaped support set centred on the given coordinate.

        The new set is the union of this set with {center} union neighbors.
        The resulting region type is STAR to reflect the star geometry.

        Args:
            center: The central coordinate of the star.
            neighbors: A list of coordinates adjacent to the centre.

        Returns:
            A new EncodingSupportSet of type STAR containing all original
            coords plus the centre and all neighbours.
        """
        new_coords = self.coords | frozenset([center]) | frozenset(neighbors)
        return EncodingSupportSet(
            coords=new_coords,
            region_type=RegionType.STAR,
            metadata={**self.metadata, "star_center": center},
        )

    def to_geometry_support(self) -> Any:
        """Convert this encoding-layer support set to a geometry-layer SupportSet.

        Attempts to import and instantiate jugeo.geometry.supports.SupportSet
        with the coordinate set.  If the import fails, logs an error and
        returns None.

        Returns:
            A SupportSet instance, or None if the geometry layer is unavailable.
        """
        try:
            from jugeo.geometry.supports import SupportSet  # type: ignore[import]
            return SupportSet(coordinates=self.coords)
        except Exception as e:
            logger.error("to_geometry_support failed: %s", e)
            return None

    @classmethod
    def from_geometry_support(
        cls,
        ss: Any,
        region_type: RegionType = RegionType.ARBITRARY,
    ) -> EncodingSupportSet:
        """Construct an EncodingSupportSet from a geometry-layer SupportSet.

        The coordinates attribute of the given SupportSet is read and stored
        as a frozenset.  If the attribute is missing an empty frozenset is
        used and a warning is logged.

        Args:
            ss: A jugeo.geometry.supports.SupportSet instance (or Any when
                the geometry layer is unavailable).
            region_type: The RegionType annotation to assign to the new
                EncodingSupportSet.

        Returns:
            A new EncodingSupportSet wrapping the geometry-layer coordinates.
        """
        if hasattr(ss, "coordinates"):
            raw = ss.coordinates
            if isinstance(raw, frozenset):
                coords: frozenset[str] = raw
            else:
                try:
                    coords = frozenset(raw)
                except TypeError:
                    logger.warning(
                        "SupportSet.coordinates is not iterable; using empty set."
                    )
                    coords = frozenset()
        else:
            logger.warning(
                "Provided object has no 'coordinates' attribute; using empty set."
            )
            coords = frozenset()
        return cls(coords=coords, region_type=region_type)

    def to_json(self) -> str:
        """Serialize this support set to a JSON string.

        The frozenset of coordinates is converted to a sorted list to
        produce deterministic output.

        Returns:
            A JSON string representing this EncodingSupportSet.
        """
        return json.dumps({
            "coords": sorted(self.coords),
            "region_type": self.region_type.value,
            "metadata": self.metadata,
        })

    @classmethod
    def from_json(cls, data: str) -> EncodingSupportSet:
        """Deserialize an EncodingSupportSet from a JSON string.

        Args:
            data: A JSON string previously produced by to_json.

        Returns:
            A fully-populated EncodingSupportSet instance.
        """
        raw = json.loads(data)
        try:
            region_type = RegionType(raw.get("region_type", RegionType.ARBITRARY.value))
        except ValueError:
            region_type = RegionType.ARBITRARY
        return cls(
            coords=frozenset(raw.get("coords", [])),
            region_type=region_type,
            metadata=raw.get("metadata", {}),
        )

    def __len__(self) -> int:
        """Return the number of coordinates in this support set.

        Returns:
            Integer cardinality of self.coords.
        """
        return len(self.coords)

    def __contains__(self, item: str) -> bool:  # type: ignore[override]
        """Support the in operator for coordinate membership testing.

        Args:
            item: The coordinate identifier to test.

        Returns:
            True if item is in self.coords, False otherwise.
        """
        return item in self.coords


# ---------------------------------------------------------------------------
# IncrementalUpdate
# ---------------------------------------------------------------------------

@dataclass
class IncrementalUpdate:
    """Represents a single incremental update to semantic memory.

    An IncrementalUpdate captures all the data needed to apply the Glue
    construction M' = Glue(M|_{X minus S}, new_sections, overlap_data) from
    theory2.tex section 34.4.  It carries the support set S of the change,
    the new section data keyed by coordinate, the overlap information needed
    to stitch sections together at boundaries, and provenance metadata.

    IncrementalUpdate instances are immutable after construction in the sense
    that the Glue construction is defined only for the data present at creation
    time.  Applying multiple updates sequentially corresponds to composing Glue
    operations as described in theory2.tex Proposition 34.7.

    Attributes:
        support_set: The support set S identifying which coordinates are
            affected by this update.
        new_sections: Mapping from coordinate string to the new section data
            to be installed at that coordinate.
        overlap_data: Mapping describing boundary data needed to reconcile
            sections at the edges of the support set.
        author: Identifier of the agent that produced this update.
        epoch: Monotonically increasing epoch counter at the time of creation.
        update_id: Stable UUID for this update instance.
        timestamp: Unix timestamp of update creation.
    """

    support_set: EncodingSupportSet
    new_sections: dict[str, Any]
    overlap_data: dict[str, Any]
    author: str
    epoch: int
    update_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: float = field(default_factory=time.time)

    def apply_to(self, memory: Any) -> Any:
        """Apply this incremental update to a SemanticMemory object.

        Iterates over new_sections and writes each value into the memory
        object using its internal region metadata store.  If the memory
        object does not expose the expected interface the operation is logged
        and skipped gracefully.

        Args:
            memory: A jugeo.runtime.memory.SemanticMemory instance (or Any
                when the runtime layer is unavailable).

        Returns:
            The same memory object passed in, potentially mutated.
        """
        try:
            for key, value in self.new_sections.items():
                if hasattr(memory, "_region") and hasattr(memory._region, "_metadata"):
                    memory._region._metadata[key] = value
        except Exception as e:
            logger.error("apply_to failed: %s", e)
        return memory

    def validate(self) -> list[str]:
        """Check this update for structural validity.

        Validation rules applied:
        - new_sections must be non-empty.
        - epoch must be >= 0.
        - support_set must be non-empty (len > 0).

        Returns:
            A list of human-readable error strings.  Empty means valid.
        """
        errors: list[str] = []
        if not self.new_sections:
            errors.append("IncrementalUpdate.new_sections must not be empty.")
        if self.epoch < 0:
            errors.append(
                f"IncrementalUpdate.epoch must be >= 0, got {self.epoch}."
            )
        if len(self.support_set) == 0:
            errors.append("IncrementalUpdate.support_set must be non-empty.")
        return errors

    def diff(self, other: IncrementalUpdate) -> dict[str, Any]:
        """Compute the difference between this update and another.

        Compares the new_sections keys of the two updates and classifies
        each key as added (present in other but not self), removed (present
        in self but not other), or changed (present in both but with
        different values).

        Args:
            other: The IncrementalUpdate to compare against.

        Returns:
            A dict with keys added, removed, and changed, each mapping to a
            list of coordinate-key strings.
        """
        self_keys = set(self.new_sections.keys())
        other_keys = set(other.new_sections.keys())
        added = sorted(other_keys - self_keys)
        removed = sorted(self_keys - other_keys)
        changed = sorted(
            k for k in self_keys & other_keys
            if self.new_sections[k] != other.new_sections[k]
        )
        return {"added": added, "removed": removed, "changed": changed}

    def to_json(self) -> str:
        """Serialize this update to a JSON string.

        The support_set is serialised via its own to_json method.  Values in
        new_sections and overlap_data must be JSON-serialisable; non-
        serialisable values are converted to their string representation.

        Returns:
            A JSON string representing this IncrementalUpdate.
        """
        def safe(obj: Any) -> Any:
            try:
                json.dumps(obj)
                return obj
            except (TypeError, ValueError):
                return str(obj)

        return json.dumps({
            "support_set": json.loads(self.support_set.to_json()),
            "new_sections": {k: safe(v) for k, v in self.new_sections.items()},
            "overlap_data": {k: safe(v) for k, v in self.overlap_data.items()},
            "author": self.author,
            "epoch": self.epoch,
            "update_id": self.update_id,
            "timestamp": self.timestamp,
        })

    @classmethod
    def from_json(cls, data: str) -> IncrementalUpdate:
        """Deserialize an IncrementalUpdate from a JSON string.

        Args:
            data: A JSON string previously produced by to_json.

        Returns:
            A fully-populated IncrementalUpdate instance.
        """
        raw = json.loads(data)
        support_set = EncodingSupportSet.from_json(json.dumps(raw["support_set"]))
        return cls(
            support_set=support_set,
            new_sections=raw.get("new_sections", {}),
            overlap_data=raw.get("overlap_data", {}),
            author=raw.get("author", ""),
            epoch=raw.get("epoch", 0),
            update_id=raw.get("update_id", str(uuid.uuid4())),
            timestamp=raw.get("timestamp", time.time()),
        )

    def summarize(self) -> str:
        """Return a human-readable single-line summary of this update.

        The summary includes the update ID, author, epoch, section count,
        and support set diameter.

        Returns:
            A single-line string describing the update.
        """
        return (
            f"IncrementalUpdate(id={self.update_id[:8]}, author={self.author!r}, "
            f"epoch={self.epoch}, sections={len(self.new_sections)}, "
            f"support_diameter={self.support_set.diameter()})"
        )

    def get_support_coords(self) -> list[str]:
        """Return a sorted list of all coordinate strings in the support set.

        Returns:
            A sorted list of coordinate identifier strings.
        """
        return sorted(self.support_set.coords)

    def check_overlap_consistency(self) -> bool:
        """Check that every key in overlap_data corresponds to a known section.

        Overlap data is considered consistent when every key either appears in
        new_sections or can be interpreted as a boundary marker.  This
        lightweight check simply tests that the overlap keys form a subset of
        the new_sections keys (a strict check in the encoding layer).

        Returns:
            True if overlap_data keys are a subset of new_sections keys or
            overlap_data is empty.
        """
        if not self.overlap_data:
            return True
        return set(self.overlap_data.keys()).issubset(set(self.new_sections.keys()))

    def compute_glue_hash(self) -> str:
        """Compute a SHA-256 hash representing the Glue operation in this update.

        The hash covers the support set coordinates, the keys of new_sections,
        and the epoch.  It is intended as a cheap fingerprint for
        deduplication, not as a cryptographic commitment.

        Returns:
            A lowercase hex-encoded SHA-256 digest string.
        """
        hasher = hashlib.sha256()
        hasher.update(",".join(sorted(self.support_set.coords)).encode())
        hasher.update(",".join(sorted(self.new_sections.keys())).encode())
        hasher.update(str(self.epoch).encode())
        return hasher.hexdigest()


# ---------------------------------------------------------------------------
# ChangeEvent
# ---------------------------------------------------------------------------

@dataclass
class ChangeEvent:
    """Records a single semantic change at a coordinate in memory.

    A ChangeEvent is the primitive unit of change in the incremental_memory
    subsystem.  Each event identifies the affected coordinate, the kind of
    operation performed, the support set of the change (the region of memory
    that is logically impacted), and an arbitrary payload carrying the actual
    data difference.

    ChangeEvents are produced by the ingestion layer and consumed by the
    invalidation engine, which uses them to trigger InvalidationWave
    propagation over the memory graph.  The conversion method
    to_invalidation_event bridges the encoding layer and the runtime
    invalidation subsystem.

    Attributes:
        event_kind: The semantic operation this event represents.
        coordinate: The primary coordinate at which the change occurred.
        support: The EncodingSupportSet describing the full region affected
            by this change, including indirect dependencies.
        payload: Arbitrary data describing the content of the change.
        provenance_source: Provenance information identifying the origin of
            this change event.
        event_id: Stable UUID for this event.
        timestamp: Unix timestamp of event creation.
    """

    event_kind: ChangeEventKind
    coordinate: str
    support: EncodingSupportSet
    payload: dict[str, Any]
    provenance_source: Any  # ProvenanceSource
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: float = field(default_factory=time.time)

    def get_affected_coords(self) -> list[str]:
        """Return a deduplicated sorted list of all coordinates affected.

        The list includes the primary coordinate and all coordinates in the
        support set, with duplicates removed.

        Returns:
            A sorted list of coordinate identifier strings.
        """
        all_coords = {self.coordinate} | set(self.support.coords)
        return sorted(all_coords)

    def to_invalidation_event(self) -> Any:
        """Convert this ChangeEvent to a runtime-layer InvalidationEvent.

        Attempts to import jugeo.runtime.invalidation.InvalidationEvent and
        construct an event via its create factory method.  If the import or
        construction fails the error is logged and None is returned.

        Returns:
            An InvalidationEvent instance, or None if the runtime layer is
            unavailable.
        """
        try:
            from jugeo.runtime.invalidation import (  # type: ignore[import]
                InvalidationEvent,
                TriggerKind,
            )
            return InvalidationEvent.create(
                trigger_coordinate=self.coordinate,
                trigger_kind=TriggerKind.SECTION_CHANGED,
                affected_count=len(self.support),
                metadata={
                    "event_id": self.event_id,
                    "kind": self.event_kind.value,
                },
            )
        except Exception as e:
            logger.error("to_invalidation_event failed: %s", e)
            return None

    def compute_footprint(self) -> int:
        """Compute the total footprint of this event in terms of coordinate count.

        The footprint is the number of distinct coordinates affected: the
        primary coordinate plus the size of the support set.

        Returns:
            Integer count of distinct affected coordinates.
        """
        return len({self.coordinate} | set(self.support.coords))

    def is_local(self) -> bool:
        """Return True if this event is considered a local (small) change.

        An event is local if its support set has diameter <= 10 and the
        event kind is one of INSERT, UPDATE, or ANNOTATE.

        Returns:
            True if the event is local, False otherwise.
        """
        local_kinds = {
            ChangeEventKind.INSERT,
            ChangeEventKind.UPDATE,
            ChangeEventKind.ANNOTATE,
        }
        return self.support.diameter() <= 10 and self.event_kind in local_kinds

    def restrict_to(self, region: EncodingSupportSet) -> ChangeEvent:
        """Return a copy of this event restricted to a sub-region.

        The restricted event's support is the intersection of this event's
        support with the given region.  The payload and other fields are
        copied unchanged.

        Args:
            region: The EncodingSupportSet to restrict this event to.

        Returns:
            A new ChangeEvent with a restricted support set.
        """
        new_support = self.support.intersect(region)
        new_coord = self.coordinate if self.coordinate in region.coords else ""
        return ChangeEvent(
            event_kind=self.event_kind,
            coordinate=new_coord,
            support=new_support,
            payload=dict(self.payload),
            provenance_source=self.provenance_source,
            event_id=self.event_id,
            timestamp=self.timestamp,
        )

    def expand_support(self, radius: int) -> ChangeEvent:
        """Return a copy of this event with an expanded support set.

        The expanded support contains all strings formed by appending _r{i}
        to each existing coordinate for i in 1..radius, as a simple synthetic
        expansion.  In a real deployment the caller would provide a
        metric-aware expansion.

        Args:
            radius: Non-negative integer controlling expansion size.

        Returns:
            A new ChangeEvent with a larger support set.
        """
        if radius <= 0:
            return ChangeEvent(
                event_kind=self.event_kind,
                coordinate=self.coordinate,
                support=self.support,
                payload=dict(self.payload),
                provenance_source=self.provenance_source,
                event_id=self.event_id,
                timestamp=self.timestamp,
            )
        extra: set[str] = set()
        for coord in self.support.coords:
            for i in range(1, radius + 1):
                extra.add(f"{coord}_r{i}")
        new_coords = self.support.coords | frozenset(extra)
        new_support = EncodingSupportSet(
            coords=new_coords,
            region_type=RegionType.BALL,
            metadata={**self.support.metadata, "expansion_radius": radius},
        )
        return ChangeEvent(
            event_kind=self.event_kind,
            coordinate=self.coordinate,
            support=new_support,
            payload=dict(self.payload),
            provenance_source=self.provenance_source,
            event_id=self.event_id,
            timestamp=self.timestamp,
        )

    def to_json(self) -> str:
        """Serialize this ChangeEvent to a JSON string.

        Non-serialisable payload values are converted to their string
        representation.

        Returns:
            A JSON string representing this ChangeEvent.
        """
        def safe(obj: Any) -> Any:
            try:
                json.dumps(obj)
                return obj
            except (TypeError, ValueError):
                return str(obj)

        return json.dumps({
            "event_kind": self.event_kind.value,
            "coordinate": self.coordinate,
            "support": json.loads(self.support.to_json()),
            "payload": {k: safe(v) for k, v in self.payload.items()},
            "provenance_source": str(self.provenance_source),
            "event_id": self.event_id,
            "timestamp": self.timestamp,
        })

    @classmethod
    def from_json(cls, data: str) -> ChangeEvent:
        """Deserialize a ChangeEvent from a JSON string.

        Args:
            data: A JSON string previously produced by to_json.

        Returns:
            A fully-populated ChangeEvent instance.
        """
        raw = json.loads(data)
        try:
            event_kind = ChangeEventKind(
                raw.get("event_kind", ChangeEventKind.UPDATE.value)
            )
        except ValueError:
            event_kind = ChangeEventKind.UPDATE
        support = EncodingSupportSet.from_json(json.dumps(raw["support"]))
        return cls(
            event_kind=event_kind,
            coordinate=raw.get("coordinate", ""),
            support=support,
            payload=raw.get("payload", {}),
            provenance_source=raw.get("provenance_source", None),
            event_id=raw.get("event_id", str(uuid.uuid4())),
            timestamp=raw.get("timestamp", time.time()),
        )


# ---------------------------------------------------------------------------
# InvalidationWaveInfo
# ---------------------------------------------------------------------------

@dataclass
class InvalidationWaveInfo:
    """Records the metadata of a single wave in an invalidation cascade.

    An InvalidationWaveInfo is a lightweight snapshot of one breadth-first
    frontier in the propagation graph traversal performed by
    MemoryInvalidationCascade.  It stores the wave index (0-based depth),
    the list of node identifiers reached in that wave, and the timestamp at
    which the wave was emitted.

    Wave info objects are appended to MemoryInvalidationCascade.wave_front
    as the cascade progresses.  Consumers can replay the wave sequence to
    reconstruct the propagation order or to compute the critical path through
    the dependency graph.

    Attributes:
        wave_index: Zero-based index of this wave within its parent cascade.
        nodes: List of node (coordinate) identifiers reached in this wave.
        timestamp: Unix timestamp of when this wave was emitted.
    """

    wave_index: int
    nodes: list[str]
    timestamp: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# MemoryInvalidationCascade
# ---------------------------------------------------------------------------

@dataclass
class MemoryInvalidationCascade:
    """Encoding-layer representation of an invalidation cascade.

    Note: This class is an encoding-layer cascade, intentionally distinct
    from jugeo.runtime.invalidation.InvalidationCascade.  It operates on
    ChangeEvent objects and InvalidationWaveInfo metadata rather than on
    runtime-layer invalidation primitives.

    A MemoryInvalidationCascade models the full propagation of one or more
    root ChangeEvents through a memory graph.  Propagation proceeds in
    breadth-first waves; each wave is recorded as an InvalidationWaveInfo.
    When propagation terminates (either because the frontier is empty or
    because a policy halts it), end_time is set to the current timestamp
    and is_complete() returns True.

    Cascades can be merged via merge_with to support parallel cascade
    computations that must be reconciled into a single result.

    Attributes:
        root_events: The ChangeEvents that initiated this cascade.
        wave_front: Ordered list of invalidation waves emitted so far.
        terminated_nodes: Set of node identifiers whose invalidation is
            considered final (no further propagation required).
        depth: Current depth (number of waves emitted) of the cascade.
        start_time: Unix timestamp of cascade initialisation.
        end_time: Unix timestamp of cascade completion; 0.0 if not done yet.
        cascade_id: Stable UUID for this cascade instance.
    """

    root_events: list[ChangeEvent]
    wave_front: list[InvalidationWaveInfo] = field(default_factory=list)
    terminated_nodes: set[str] = field(default_factory=set)
    depth: int = 0
    start_time: float = field(default_factory=time.time)
    end_time: float = 0.0
    cascade_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def add_wave(self, wave: InvalidationWaveInfo) -> None:
        """Append an InvalidationWaveInfo to the wave_front and update depth.

        The depth counter is kept in sync with the wave index so that
        max_depth() returns the correct value without scanning the list.

        Args:
            wave: The InvalidationWaveInfo to append.
        """
        self.wave_front.append(wave)
        self.depth = max(self.depth, wave.wave_index + 1)

    def is_complete(self) -> bool:
        """Return True if this cascade has been marked as complete.

        Completeness is indicated by a non-zero end_time.  Callers should
        set end_time when the cascade frontier empties or when a cascade
        policy halts further propagation.

        Returns:
            True if self.end_time > 0, False otherwise.
        """
        return self.end_time > 0

    def total_affected(self) -> int:
        """Return the total number of distinct nodes invalidated across all waves.

        Nodes that appear in multiple waves are counted only once.

        Returns:
            Integer count of distinct affected node identifiers.
        """
        all_nodes: set[str] = set()
        for wave in self.wave_front:
            all_nodes.update(wave.nodes)
        return len(all_nodes)

    def max_depth(self) -> int:
        """Return the maximum wave depth reached by this cascade.

        Returns:
            The depth field (number of waves emitted so far).
        """
        return self.depth

    def to_graph(self) -> dict[str, list[str]]:
        """Represent the cascade as an adjacency dict mapping wave nodes.

        Each wave's nodes are connected to the nodes of the next wave,
        producing a layered DAG representation of the cascade.

        Returns:
            A dict mapping each node identifier to a list of node identifiers
            in the immediately following wave that are reachable from it.
        """
        graph: dict[str, list[str]] = {}
        for i, wave in enumerate(self.wave_front):
            next_nodes = (
                self.wave_front[i + 1].nodes
                if i + 1 < len(self.wave_front)
                else []
            )
            for node in wave.nodes:
                graph.setdefault(node, []).extend(next_nodes)
        return graph

    def compute_critical_path(self) -> list[str]:
        """Compute the critical path through the cascade waves.

        The critical path is approximated as one node from each wave, choosing
        the lexicographically first node in each wave for determinism.  In a
        production implementation this would be a weighted longest-path
        computation over the cascade DAG.

        Returns:
            A list of node identifiers, one per wave, forming the critical path.
        """
        path: list[str] = []
        for wave in self.wave_front:
            if wave.nodes:
                path.append(sorted(wave.nodes)[0])
        return path

    def cost_estimate(self) -> float:
        """Estimate the computational cost of this cascade.

        The cost is approximated as total_affected * depth * 0.001,
        reflecting the intuition that wider and deeper cascades are more
        expensive to process.

        Returns:
            A non-negative float representing the estimated cost.
        """
        return float(self.total_affected() * self.depth * 0.001)

    def to_json(self) -> str:
        """Serialize this cascade to a JSON string.

        Root events and wave_front entries are each serialised to nested
        JSON objects.  The terminated_nodes set is serialised as a sorted list.

        Returns:
            A JSON string representing this MemoryInvalidationCascade.
        """
        waves_raw = [
            {
                "wave_index": w.wave_index,
                "nodes": w.nodes,
                "timestamp": w.timestamp,
            }
            for w in self.wave_front
        ]
        root_raw = [json.loads(e.to_json()) for e in self.root_events]
        return json.dumps({
            "root_events": root_raw,
            "wave_front": waves_raw,
            "terminated_nodes": sorted(self.terminated_nodes),
            "depth": self.depth,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "cascade_id": self.cascade_id,
        })

    def merge_with(
        self, other: MemoryInvalidationCascade
    ) -> MemoryInvalidationCascade:
        """Merge another cascade into this one, producing a new combined cascade.

        The merged cascade's root_events is the union of the two inputs'
        root_events, deduplicated by event_id.  The wave_front entries are
        merged by wave_index: nodes for the same index are combined.
        terminated_nodes is the union of both sets.

        Args:
            other: The other MemoryInvalidationCascade to merge with.

        Returns:
            A new MemoryInvalidationCascade representing the merged result.
        """
        seen_event_ids: set[str] = set()
        merged_roots: list[ChangeEvent] = []
        for evt in self.root_events + other.root_events:
            if evt.event_id not in seen_event_ids:
                merged_roots.append(evt)
                seen_event_ids.add(evt.event_id)

        wave_map: dict[int, set[str]] = {}
        wave_times: dict[int, float] = {}
        for wave in self.wave_front + other.wave_front:
            wave_map.setdefault(wave.wave_index, set()).update(wave.nodes)
            wave_times[wave.wave_index] = min(
                wave_times.get(wave.wave_index, wave.timestamp),
                wave.timestamp,
            )
        merged_waves = [
            InvalidationWaveInfo(
                wave_index=idx,
                nodes=sorted(nodes),
                timestamp=wave_times[idx],
            )
            for idx, nodes in sorted(wave_map.items())
        ]

        return MemoryInvalidationCascade(
            root_events=merged_roots,
            wave_front=merged_waves,
            terminated_nodes=self.terminated_nodes | other.terminated_nodes,
            depth=max(self.depth, other.depth),
            start_time=min(self.start_time, other.start_time),
            end_time=max(self.end_time, other.end_time),
        )


# ---------------------------------------------------------------------------
# PersistentMemoryState
# ---------------------------------------------------------------------------

@dataclass
class PersistentMemoryState:
    """Encoding-layer record of durable memory state across epochs.

    PersistentMemoryState tracks the series of MemorySnapshots produced
    during the lifetime of the incremental_memory subsystem, the current
    epoch counter, a mapping from section keys to their most recent epoch,
    a section cache for fast lookups, and quota accounting for memory usage.

    The class provides checkpoint/restore operations to save and recover
    memory state, an epoch advance primitive, and merge/diff utilities for
    reconciling divergent memory histories.  It is designed to work whether
    or not the jugeo.runtime.memory module is available; when unavailable it
    falls back to storing lightweight dict markers as snapshot surrogates.

    Attributes:
        snapshots: Ordered list of MemorySnapshot objects (or fallback dicts).
        current_epoch: The current epoch number (incremented by advance_epoch).
        epoch_map: Mapping from section key to the epoch of its last update.
        section_cache: In-memory cache mapping section keys to their data.
        quota_used: Number of quota units currently consumed.
        quota_limit: Maximum number of quota units allowed.
        state_id: Stable UUID for this state instance.
    """

    snapshots: list[Any] = field(default_factory=list)
    current_epoch: int = 0
    epoch_map: dict[str, int] = field(default_factory=dict)
    section_cache: dict[str, Any] = field(default_factory=dict)
    quota_used: int = 0
    quota_limit: int = 100_000
    state_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def checkpoint(self) -> str:
        """Capture a snapshot of the current memory state and store it.

        Attempts to use jugeo.runtime.memory.MemoryRegion and
        MemorySnapshot.capture for a rich snapshot.  Falls back to a
        lightweight dict marker when the runtime layer is unavailable.

        Returns:
            The snapshot_id string identifying the newly created snapshot.
        """
        snapshot_id = str(uuid.uuid4())
        try:
            from jugeo.runtime.memory import MemoryRegion, MemorySnapshot  # type: ignore[import]
            region = MemoryRegion(coordinate_range=tuple(self.section_cache.keys()))
            snap = MemorySnapshot.capture(region)
            self.snapshots.append(snap)
            return snap.snapshot_id
        except Exception:
            # fallback: store a simple dict marker
            self.snapshots.append(  # type: ignore[arg-type]
                {
                    "snapshot_id": snapshot_id,
                    "timestamp": time.time(),
                    "data_hash": snapshot_id,
                }
            )
            return snapshot_id

    def restore(self, snapshot_id: str) -> bool:
        """Restore memory state from a snapshot identified by snapshot_id.

        Scans the snapshots list for an entry matching snapshot_id and
        restores the section cache from its data if found.  For dict-marker
        snapshots the section cache is cleared (no data stored).

        Args:
            snapshot_id: The identifier of the snapshot to restore.

        Returns:
            True if a matching snapshot was found and restored, False if no
            snapshot with that ID exists.
        """
        for snap in self.snapshots:
            sid: str | None = None
            if isinstance(snap, dict):
                sid = snap.get("snapshot_id")
            elif hasattr(snap, "snapshot_id"):
                sid = snap.snapshot_id
            if sid == snapshot_id:
                if isinstance(snap, dict):
                    logger.debug(
                        "Restoring from dict-marker snapshot %s; "
                        "section_cache cleared.",
                        snapshot_id,
                    )
                    self.section_cache.clear()
                else:
                    logger.debug(
                        "Restoring from MemorySnapshot %s.", snapshot_id
                    )
                    if hasattr(snap, "section_data") and isinstance(
                        snap.section_data, dict
                    ):
                        self.section_cache = dict(snap.section_data)
                return True
        logger.warning("Snapshot %s not found.", snapshot_id)
        return False

    def advance_epoch(self) -> int:
        """Increment the current epoch counter and return the new value.

        Returns:
            The new epoch value after incrementing.
        """
        self.current_epoch += 1
        logger.debug("Epoch advanced to %d.", self.current_epoch)
        return self.current_epoch

    def evict_stale(self, max_age: float) -> int:
        """Remove section_cache entries that have not been updated recently.

        An entry is considered stale if its epoch was recorded more than
        max_age seconds ago relative to time.time().  Because the epoch_map
        stores epoch numbers (not timestamps) this method uses the heuristic
        that an entry is stale if its epoch is more than int(max_age) epochs
        behind the current epoch.

        Args:
            max_age: Maximum allowable age expressed in epoch-units (used as
                an integer threshold against the epoch gap).

        Returns:
            The number of section_cache entries evicted.
        """
        threshold_epoch = self.current_epoch - int(max_age)
        stale_keys = [
            k
            for k, ep in self.epoch_map.items()
            if ep < threshold_epoch and k in self.section_cache
        ]
        for k in stale_keys:
            del self.section_cache[k]
        logger.debug("Evicted %d stale entries.", len(stale_keys))
        return len(stale_keys)

    def merge_states(
        self, other: PersistentMemoryState
    ) -> PersistentMemoryState:
        """Merge another PersistentMemoryState into this one, returning a new state.

        The merge strategy is last-writer-wins based on epoch: for keys
        present in both states the one with the higher epoch in epoch_map
        wins.  The resulting state has the max of the two current_epoch
        values and the union of both snapshots lists.

        Args:
            other: The PersistentMemoryState to merge with.

        Returns:
            A new PersistentMemoryState representing the merged result.
        """
        merged_cache: dict[str, Any] = dict(self.section_cache)
        merged_epoch_map: dict[str, int] = dict(self.epoch_map)
        for key, value in other.section_cache.items():
            self_epoch = self.epoch_map.get(key, -1)
            other_epoch = other.epoch_map.get(key, -1)
            if other_epoch >= self_epoch:
                merged_cache[key] = value
                merged_epoch_map[key] = other_epoch
        merged_snapshots = list(self.snapshots) + list(other.snapshots)
        merged_epoch = max(self.current_epoch, other.current_epoch)
        merged_quota = max(self.quota_used, other.quota_used)
        merged_limit = min(self.quota_limit, other.quota_limit)
        return PersistentMemoryState(
            snapshots=merged_snapshots,
            current_epoch=merged_epoch,
            epoch_map=merged_epoch_map,
            section_cache=merged_cache,
            quota_used=merged_quota,
            quota_limit=merged_limit,
        )

    def diff_states(self, other: PersistentMemoryState) -> dict[str, Any]:
        """Compute a diff between this state and another.

        Compares the section_cache keys of the two states and classifies each
        key as added (in other but not self), removed (in self but not other),
        or changed (in both but with different values or epochs).

        Args:
            other: The PersistentMemoryState to compare against.

        Returns:
            A dict with keys added, removed, changed, and epoch_delta
            (other.current_epoch - self.current_epoch).
        """
        self_keys = set(self.section_cache.keys())
        other_keys = set(other.section_cache.keys())
        added = sorted(other_keys - self_keys)
        removed = sorted(self_keys - other_keys)
        changed = sorted(
            k
            for k in self_keys & other_keys
            if self.section_cache.get(k) != other.section_cache.get(k)
            or self.epoch_map.get(k) != other.epoch_map.get(k)
        )
        return {
            "added": added,
            "removed": removed,
            "changed": changed,
            "epoch_delta": other.current_epoch - self.current_epoch,
        }

    def serialize(self) -> str:
        """Serialize this state to a JSON string.

        MemorySnapshot objects that are not natively JSON-serialisable are
        converted to their string representation.  Dict-marker snapshots are
        included as-is.

        Returns:
            A JSON string representing this PersistentMemoryState.
        """
        snaps_raw: list[Any] = []
        for snap in self.snapshots:
            if isinstance(snap, dict):
                snaps_raw.append(snap)
            else:
                snaps_raw.append(str(snap))
        return json.dumps({
            "snapshots": snaps_raw,
            "current_epoch": self.current_epoch,
            "epoch_map": self.epoch_map,
            "section_cache": {
                k: (
                    v
                    if isinstance(v, (str, int, float, bool, type(None)))
                    else str(v)
                )
                for k, v in self.section_cache.items()
            },
            "quota_used": self.quota_used,
            "quota_limit": self.quota_limit,
            "state_id": self.state_id,
        })

    @classmethod
    def deserialize(cls, data: str) -> PersistentMemoryState:
        """Deserialize a PersistentMemoryState from a JSON string.

        Args:
            data: A JSON string previously produced by serialize.

        Returns:
            A PersistentMemoryState instance restored from the JSON payload.
        """
        raw = json.loads(data)
        return cls(
            snapshots=raw.get("snapshots", []),
            current_epoch=raw.get("current_epoch", 0),
            epoch_map=raw.get("epoch_map", {}),
            section_cache=raw.get("section_cache", {}),
            quota_used=raw.get("quota_used", 0),
            quota_limit=raw.get("quota_limit", 100_000),
            state_id=raw.get("state_id", str(uuid.uuid4())),
        )

    def health_check(self) -> dict[str, Any]:
        """Return a health status dict summarising the state of this object.

        The health check reports snapshot count, current epoch, cache
        utilisation, quota utilisation, and whether the quota has been exceeded.

        Returns:
            A dict with keys snapshot_count, current_epoch, cache_size,
            quota_used, quota_limit, quota_pct, and quota_exceeded.
        """
        quota_pct = (
            round(100.0 * self.quota_used / self.quota_limit, 2)
            if self.quota_limit > 0
            else 0.0
        )
        return {
            "snapshot_count": len(self.snapshots),
            "current_epoch": self.current_epoch,
            "cache_size": len(self.section_cache),
            "quota_used": self.quota_used,
            "quota_limit": self.quota_limit,
            "quota_pct": quota_pct,
            "quota_exceeded": self.quota_used > self.quota_limit,
        }


# ---------------------------------------------------------------------------
# Module-level exports
# ---------------------------------------------------------------------------

__all__ = [
    # Enums
    "ChangeEventKind",
    "RegionType",
    # Dataclasses
    "EncodingSupportSet",
    "IncrementalUpdate",
    "ChangeEvent",
    "InvalidationWaveInfo",
    "MemoryInvalidationCascade",
    "PersistentMemoryState",
]