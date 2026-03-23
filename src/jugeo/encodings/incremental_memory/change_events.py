"""Change event infrastructure for incremental memory — theory2.tex Ch34.

This module provides event streaming, batching, filtering, and aggregation
for change events in the incremental_memory encoding subsystem, developed
with copilot assistance.  Change events represent atomic mutations to the
semantic memory M with associated support sets.

Theory alignment: each ChangeEvent corresponds to a semantic change δ with
support S ⊆ X in theory2.tex §34.2, triggering M' = Glue(M|_{X\\S}, δ).
The streaming and batching infrastructure allows multiple events to be
collected and replayed in order, while the aggregation layer coarsens
fine-grained events into larger updates that can be processed more
efficiently by the runtime memory pipeline.

The serialisation helpers (ChangeEventSerializer) provide JSON and newline-
delimited JSON formats suitable for persistence and network transport.  The
filter builder (ChangeEventFilter) follows a fluent builder pattern so that
complex filter predicates can be composed incrementally without mutation of
the underlying event data.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Iterator

logger = logging.getLogger(__name__)

try:
    from jugeo.geometry.site import Coordinate
except ImportError:
    Coordinate = Any  # type: ignore

try:
    from jugeo.judgments.judgment_terms import ProvenanceSource
except ImportError:
    class ProvenanceSource(str, Enum):  # type: ignore
        RUNTIME = "runtime"
        SOLVER = "solver"

from jugeo.encodings.incremental_memory.models import (
    ChangeEvent,
    ChangeEventKind,
    EncodingSupportSet,
    RegionType,
)

__all__ = [
    "ChangeEventStream",
    "ChangeEventBatch",
    "SupportTracker",
    "EventAggregator",
    "ChangeEventSerializer",
    "ChangeEventFilter",
    "emit_change_event",
]


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _sha256(text: str) -> str:
    """Return the SHA-256 hex digest of *text*.

    Args:
        text: The input string to hash.

    Returns:
        A 64-character lowercase hex string.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# ChangeEventStream
# ---------------------------------------------------------------------------


class ChangeEventStream:
    """An ordered, appendable stream of ChangeEvent objects.

    A ChangeEventStream is the primary transport container for sequences of
    change events produced during incremental memory updates.  It supports
    iteration, slicing, merging with other streams, and filtering by event
    kind or coordinate.  Streams are identified by a UUID stream_id that is
    generated automatically if not provided at construction time.

    The since method returns a new stream containing only events with a
    timestamp greater than or equal to the given value, which is useful for
    implementing incremental replay from a checkpoint.  The deduplicate method
    removes events with duplicate event_ids, keeping the first occurrence, so
    that streams produced by merging can be safely processed exactly once.

    The to_json / from_json round-trip preserves all event fields via the
    ChangeEvent.to_json() / ChangeEvent.from_json() methods, making streams
    fully serialisable for persistence or network transport.

    The merge method combines two streams into a new stream whose events are
    sorted by timestamp in ascending order; the merged stream inherits a new
    stream_id.

    Args:
        stream_id: Optional UUID string for this stream.  A fresh UUID is
            generated automatically if not supplied.
    """

    def __init__(self, stream_id: str | None = None) -> None:
        """Initialise an empty stream with the given or a generated stream_id.

        Args:
            stream_id: Optional identifier for this stream.
        """
        self._events: list = []
        self._stream_id: str = stream_id or str(uuid.uuid4())
        self._created_at: float = time.time()

    # ------------------------------------------------------------------
    # Core API
    # ------------------------------------------------------------------

    def append(self, event: ChangeEvent) -> None:
        """Append a ChangeEvent to the end of this stream.

        Args:
            event: The ChangeEvent to append.
        """
        self._events.append(event)

    def __iter__(self) -> Iterator:
        """Iterate over all ChangeEvent objects in insertion order.

        Yields:
            Each ChangeEvent in the stream.
        """
        return iter(list(self._events))

    def __len__(self) -> int:
        """Return the number of events in the stream.

        Returns:
            Integer count of events.
        """
        return len(self._events)

    def get_by_kind(self, kind: ChangeEventKind) -> list:
        """Return a list of events matching the given ChangeEventKind.

        Args:
            kind: The ChangeEventKind to filter by.

        Returns:
            A list of ChangeEvent objects whose event_kind equals kind.
        """
        return [e for e in self._events if e.event_kind == kind]

    def get_by_coordinate(self, coord: str) -> list:
        """Return a list of events whose coordinate matches coord.

        Args:
            coord: The coordinate string to match.

        Returns:
            A list of ChangeEvent objects with the given coordinate.
        """
        return [e for e in self._events if e.coordinate == coord]

    def slice(self, start: int, end: int) -> ChangeEventStream:
        """Return a new stream containing events in the index range [start, end).

        Args:
            start: Inclusive start index.
            end: Exclusive end index.

        Returns:
            A new ChangeEventStream containing the sliced events.
        """
        new_stream = ChangeEventStream()
        for event in self._events[start:end]:
            new_stream.append(event)
        return new_stream

    def merge(self, other: ChangeEventStream) -> ChangeEventStream:
        """Return a new stream containing all events from self and other, sorted by timestamp.

        The merged stream is a new object with a fresh stream_id.  Events are
        sorted in ascending order by their timestamp field.

        Args:
            other: Another ChangeEventStream to merge with.

        Returns:
            A new ChangeEventStream with events from both streams sorted by
            timestamp.
        """
        combined = list(self._events) + list(other._events)
        combined.sort(key=lambda e: e.timestamp)
        merged = ChangeEventStream()
        for event in combined:
            merged.append(event)
        return merged

    def since(self, timestamp: float) -> ChangeEventStream:
        """Return a new stream containing only events at or after timestamp.

        Args:
            timestamp: A Unix timestamp; events strictly before this value are
                excluded.

        Returns:
            A new ChangeEventStream with qualifying events in original order.
        """
        new_stream = ChangeEventStream()
        for event in self._events:
            if event.timestamp >= timestamp:
                new_stream.append(event)
        return new_stream

    def to_json(self) -> str:
        """Serialise this stream to a JSON string.

        Returns:
            A JSON string containing stream_id, created_at, and a list of
            serialised events.
        """
        return json.dumps({
            "stream_id": self._stream_id,
            "created_at": self._created_at,
            "events": [json.loads(e.to_json()) for e in self._events],
        })

    @classmethod
    def from_json(cls, data: str) -> ChangeEventStream:
        """Reconstruct a ChangeEventStream from a JSON string.

        Args:
            data: A JSON string produced by ``to_json``.

        Returns:
            A new ChangeEventStream with all events restored.
        """
        obj = json.loads(data)
        stream = cls(stream_id=obj.get("stream_id"))
        stream._created_at = obj.get("created_at", time.time())
        for raw_event in obj.get("events", []):
            event = ChangeEvent.from_json(json.dumps(raw_event))
            stream.append(event)
        return stream

    def summary(self) -> str:
        """Return a human-readable summary of this stream.

        Returns:
            A single-line string with stream_id prefix, event count, and time range.
        """
        if not self._events:
            return f"ChangeEventStream[{self._stream_id[:8]}]: empty"
        earliest = min(e.timestamp for e in self._events)
        latest = max(e.timestamp for e in self._events)
        kinds = {e.event_kind.value for e in self._events}
        return (
            f"ChangeEventStream[{self._stream_id[:8]}]: "
            f"count={len(self._events)} "
            f"kinds={sorted(kinds)} "
            f"t=[{earliest:.3f}, {latest:.3f}]"
        )

    def deduplicate(self) -> ChangeEventStream:
        """Return a new stream with duplicate event_ids removed (first wins).

        Iterates over events in insertion order and keeps the first occurrence
        of each event_id, discarding subsequent duplicates.

        Returns:
            A new ChangeEventStream without duplicate event_ids.
        """
        seen: set = set()
        deduped = ChangeEventStream(stream_id=self._stream_id)
        for event in self._events:
            if event.event_id not in seen:
                seen.add(event.event_id)
                deduped.append(event)
        return deduped


# ---------------------------------------------------------------------------
# ChangeEventBatch
# ---------------------------------------------------------------------------


@dataclass
class ChangeEventBatch:
    """An atomic, ordered batch of ChangeEvent objects with authorship metadata.

    A ChangeEventBatch groups multiple ChangeEvent objects that should be
    processed together as an atomic unit.  The atomic flag indicates whether
    the batch must be applied all-or-nothing; if True, failure of any single
    event should cause the entire batch to be rolled back.

    The author and description fields carry human-readable provenance for the
    batch, useful for audit logs and debugging.  The batch_id is a UUID
    generated at creation time that uniquely identifies the batch.

    The validate method checks that all events within the batch are self-
    consistent: non-empty coordinate strings, valid event_kind values, and
    support sets with at least one coordinate.  Errors are returned as a list
    of strings rather than raised as exceptions, allowing partial validation
    results to be inspected.

    The split method partitions the batch into two sub-batches using a
    predicate function, which is useful for separating events that can be
    processed immediately from those that must be deferred.

    Args:
        events: The list of ChangeEvent objects in this batch.
        atomic: Whether the batch must be applied atomically.
        author: Human-readable author identifier.
        description: Human-readable description of the batch.
        batch_id: UUID string for this batch.
        timestamp: Unix timestamp of batch creation.
    """

    events: list = field(default_factory=list)
    atomic: bool = True
    author: str = ""
    description: str = ""
    batch_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: float = field(default_factory=time.time)

    def add(self, event: ChangeEvent) -> None:
        """Append a ChangeEvent to this batch.

        Args:
            event: The ChangeEvent to add.
        """
        self.events.append(event)

    def remove(self, event_id: str) -> bool:
        """Remove the event with the given event_id from this batch.

        Args:
            event_id: The event_id string to remove.

        Returns:
            True if an event was found and removed, False otherwise.
        """
        original_count = len(self.events)
        self.events = [e for e in self.events if e.event_id != event_id]
        return len(self.events) < original_count

    def validate(self) -> list:
        """Validate all events in the batch and return a list of error strings.

        Checks each event for:
        - Non-empty coordinate string.
        - Non-empty support.coords.
        - Valid event_kind membership.

        Returns:
            A list of error strings; an empty list means the batch is valid.
        """
        errors: list = []
        for i, event in enumerate(self.events):
            prefix = f"event[{i}] id={event.event_id[:8]}"
            if not event.coordinate:
                errors.append(f"{prefix}: coordinate is empty")
            if not event.support.coords:
                errors.append(f"{prefix}: support.coords is empty")
            if not isinstance(event.event_kind, ChangeEventKind):
                errors.append(f"{prefix}: event_kind is not a valid ChangeEventKind")
        return errors

    def total_footprint(self) -> int:
        """Return the total number of distinct coordinates touched by this batch.

        Returns:
            Count of unique coordinate strings across all events.
        """
        return len(self.affected_coordinates())

    def affected_coordinates(self) -> set:
        """Return the set of all coordinates touched by events in this batch.

        Returns:
            A set of coordinate strings.
        """
        coords: set = set()
        for event in self.events:
            coords.add(event.coordinate)
            coords.update(event.support.coords)
        return coords

    def to_stream(self) -> ChangeEventStream:
        """Convert this batch to a ChangeEventStream in the same order.

        Returns:
            A new ChangeEventStream containing all events from this batch.
        """
        stream = ChangeEventStream()
        for event in self.events:
            stream.append(event)
        return stream

    def split(self, predicate: Callable) -> tuple:
        """Split the batch into two sub-batches based on a predicate.

        Events for which predicate(event) returns True go into the first
        sub-batch; all others go into the second.  Both sub-batches inherit
        the author and description of the original batch.

        Args:
            predicate: A callable (ChangeEvent) -> bool.

        Returns:
            A tuple (matching_batch, non_matching_batch) of ChangeEventBatch.
        """
        matching = ChangeEventBatch(
            author=self.author,
            description=f"{self.description} [split:match]",
        )
        non_matching = ChangeEventBatch(
            author=self.author,
            description=f"{self.description} [split:rest]",
        )
        for event in self.events:
            if predicate(event):
                matching.add(event)
            else:
                non_matching.add(event)
        return matching, non_matching

    def to_json(self) -> str:
        """Serialise this batch to a JSON string.

        Events are serialised as a list of JSON strings (one per event).

        Returns:
            A JSON-encoded string containing all batch fields.
        """
        return json.dumps({
            "events": [e.to_json() for e in self.events],
            "atomic": self.atomic,
            "author": self.author,
            "description": self.description,
            "batch_id": self.batch_id,
            "timestamp": self.timestamp,
        })

    @classmethod
    def from_json(cls, data: str) -> ChangeEventBatch:
        """Reconstruct a ChangeEventBatch from a JSON string.

        Events are deserialised from the list of JSON strings stored under
        the 'events' key.

        Args:
            data: A JSON string produced by ``to_json``.

        Returns:
            A new ChangeEventBatch with all fields restored.
        """
        obj = json.loads(data)
        batch = cls(
            atomic=obj.get("atomic", True),
            author=obj.get("author", ""),
            description=obj.get("description", ""),
            batch_id=obj.get("batch_id", str(uuid.uuid4())),
            timestamp=obj.get("timestamp", time.time()),
        )
        for raw in obj.get("events", []):
            event = ChangeEvent.from_json(raw)
            batch.add(event)
        return batch

    def summary(self) -> str:
        """Return a human-readable one-line summary of this batch.

        Returns:
            A string with batch_id prefix, count, atomic flag, and author.
        """
        return (
            f"ChangeEventBatch[{self.batch_id[:8]}]: "
            f"count={len(self.events)} "
            f"atomic={self.atomic} "
            f"author={self.author!r} "
            f"footprint={self.total_footprint()}"
        )


# ---------------------------------------------------------------------------
# SupportTracker
# ---------------------------------------------------------------------------


class SupportTracker:
    """Tracks the evolution of EncodingSupportSet per coordinate over time.

    The SupportTracker maintains a current support mapping from coordinate
    strings to their most recent EncodingSupportSet, as well as a history log
    that records each change with its associated timestamp.  This allows
    callers to audit how the support of any given coordinate has evolved and
    to detect whether a support has changed since the last observation.

    The merged_support method computes the union of the supports for a list of
    coordinates by unioning their coords frozensets.  This is used when the
    invalidation cascade must be computed for a group of coordinates at once.

    The snapshot / restore methods provide a simple checkpointing mechanism:
    snapshot returns a plain dict of the current state, and restore replaces
    the current state with a previously captured snapshot (history is not
    affected by restore).

    The has_changed method returns True if a coordinate's support has been
    updated at least once since it was first tracked (i.e., it has more than
    one entry in its history).

    Args:
        None.
    """

    def __init__(self) -> None:
        """Initialise an empty support tracker."""
        self._current: dict = {}
        self._history: dict = {}

    def track(self, coord: str, support: EncodingSupportSet) -> None:
        """Record the initial or updated support for coord.

        If coord is not yet tracked, this sets the initial support.  Otherwise
        it updates the current support and appends to the history.

        Args:
            coord: The coordinate string to track.
            support: The EncodingSupportSet to associate with coord.
        """
        now = time.time()
        self._current[coord] = support
        if coord not in self._history:
            self._history[coord] = []
        self._history[coord].append((support, now))

    def get_support(self, coord: str) -> EncodingSupportSet | None:
        """Return the current support for coord, or None if not tracked.

        Args:
            coord: The coordinate string to look up.

        Returns:
            The current EncodingSupportSet or None.
        """
        return self._current.get(coord)

    def get_history(self, coord: str) -> list:
        """Return the full history of (EncodingSupportSet, timestamp) for coord.

        Args:
            coord: The coordinate string to look up.

        Returns:
            A list of (EncodingSupportSet, float) tuples in chronological order.
        """
        return list(self._history.get(coord, []))

    def update_support(self, coord: str, new_support: EncodingSupportSet) -> None:
        """Update the support for coord to new_support.

        Equivalent to track(coord, new_support) but semantically signals an
        update rather than initial tracking.

        Args:
            coord: The coordinate string to update.
            new_support: The new EncodingSupportSet.
        """
        self.track(coord, new_support)

    def merged_support(self, coords: list) -> EncodingSupportSet:
        """Return the union of the supports for all given coordinates.

        Unions the coords frozensets of all tracked supports for the given
        coordinate list.  Coordinates that are not tracked are ignored.
        The resulting EncodingSupportSet uses RegionType.ARBITRARY.

        Args:
            coords: A list of coordinate strings.

        Returns:
            An EncodingSupportSet whose coords is the union of all tracked
            support coords for the given coordinates.
        """
        union_coords: set = set()
        for coord in coords:
            support = self._current.get(coord)
            if support is not None:
                union_coords.update(support.coords)
        return EncodingSupportSet(
            coords=frozenset(union_coords),
            region_type=RegionType.ARBITRARY,
            metadata={},
        )

    def has_changed(self, coord: str) -> bool:
        """Return True if coord's support has been updated at least once.

        A support is considered changed if there are two or more history
        entries for the given coordinate.

        Args:
            coord: The coordinate string to check.

        Returns:
            True if the coordinate's support has been updated.
        """
        return len(self._history.get(coord, [])) > 1

    def snapshot(self) -> dict:
        """Return a shallow copy of the current support mapping.

        Returns:
            A dict mapping coordinate strings to their current
            EncodingSupportSet objects.
        """
        return dict(self._current)

    def restore(self, snapshot: dict) -> None:
        """Replace the current support mapping with the given snapshot.

        The history is not modified by this operation; only the current
        mapping is replaced.

        Args:
            snapshot: A dict mapping coordinate strings to EncodingSupportSet
                objects, as produced by ``snapshot()``.
        """
        self._current = dict(snapshot)

    def summary(self) -> str:
        """Return a human-readable summary of the tracker's state.

        Returns:
            A single-line string with the count of tracked coordinates and
            the total number of history entries.
        """
        total_history = sum(len(v) for v in self._history.values())
        return (
            f"SupportTracker: tracked={len(self._current)} "
            f"history_entries={total_history}"
        )


# ---------------------------------------------------------------------------
# EventAggregator
# ---------------------------------------------------------------------------


class EventAggregator:
    """Buffers and aggregates ChangeEvents within a sliding time window.

    The EventAggregator accumulates incoming ChangeEvent objects in an internal
    buffer.  When the elapsed time since the first buffered event exceeds
    window_seconds, the aggregator is considered ready to flush.  Flushing
    returns a ChangeEventBatch containing all buffered events and resets the
    internal state.

    The aggregate_by_coord method groups buffered events by their primary
    coordinate, providing a view that is useful for the coarsening step.
    The coarsen method reduces multiple events at the same coordinate to a
    single UPDATE event whose payload merges all individual payloads (later
    payloads override earlier ones), reducing the number of events the
    downstream pipeline must process.

    The is_ready method checks whether the flush condition is met: the buffer
    is non-empty and the window has elapsed.  The size method returns the
    current buffer length without triggering a flush.

    Args:
        window_seconds: Duration in seconds of the aggregation window.
    """

    def __init__(self, window_seconds: float = 5.0) -> None:
        """Initialise with the given window duration.

        Args:
            window_seconds: Duration of the aggregation window in seconds.
        """
        self._window_seconds = window_seconds
        self._buffer: list = []
        self._window_start: float | None = None

    def ingest(self, event: ChangeEvent) -> None:
        """Add a ChangeEvent to the aggregation buffer.

        If this is the first event in a new window, records the window start
        time.

        Args:
            event: The ChangeEvent to buffer.
        """
        if not self._buffer:
            self._window_start = time.time()
        self._buffer.append(event)

    def flush(self) -> ChangeEventBatch:
        """Return a ChangeEventBatch of all buffered events and reset the buffer.

        Creates a new ChangeEventBatch containing all currently buffered
        events, then clears the buffer and window start time.

        Returns:
            A ChangeEventBatch with all buffered events.
        """
        batch = ChangeEventBatch(description="aggregator flush")
        for event in self._buffer:
            batch.add(event)
        self._buffer = []
        self._window_start = None
        return batch

    def aggregate_by_coord(self) -> dict:
        """Return a dict grouping buffered events by their primary coordinate.

        Returns:
            A dict mapping coordinate strings to lists of ChangeEvent objects.
        """
        grouped: dict = {}
        for event in self._buffer:
            coord = event.coordinate
            if coord not in grouped:
                grouped[coord] = []
            grouped[coord].append(event)
        return grouped

    def coarsen(self) -> list:
        """Merge multiple events at the same coordinate into a single UPDATE event.

        For each coordinate that appears more than once in the buffer, merges
        all events' payloads (later events override earlier ones) and creates
        a single ChangeEventKind.UPDATE event with the merged payload.
        Coordinates with only one event are passed through unchanged.

        Returns:
            A list of coarsened ChangeEvent objects, one per unique coordinate.
        """
        grouped = self.aggregate_by_coord()
        coarsened: list = []
        for coord, events in grouped.items():
            if len(events) == 1:
                coarsened.append(events[0])
            else:
                merged_payload: dict = {}
                latest_support = events[-1].support
                for event in events:
                    merged_payload.update(event.payload)
                merged_event = emit_change_event(
                    coord=coord,
                    kind=ChangeEventKind.UPDATE,
                    payload=merged_payload,
                    support_coords=list(latest_support.coords),
                    provenance=events[-1].provenance_source,
                )
                coarsened.append(merged_event)
        return coarsened

    def is_ready(self) -> bool:
        """Return True if the buffer is non-empty and the window has elapsed.

        Returns:
            True when there are buffered events and window_seconds have passed
            since the first event was ingested.
        """
        if not self._buffer:
            return False
        if self._window_start is None:
            return False
        return (time.time() - self._window_start) >= self._window_seconds

    def size(self) -> int:
        """Return the number of events currently in the buffer.

        Returns:
            Integer count of buffered events.
        """
        return len(self._buffer)

    def clear(self) -> None:
        """Clear the buffer and reset the window start time."""
        self._buffer = []
        self._window_start = None

    def summary(self) -> str:
        """Return a human-readable summary of the aggregator's state.

        Returns:
            A single-line string with buffer size, window duration, and
            whether the aggregator is ready to flush.
        """
        elapsed = (
            f"{time.time() - self._window_start:.2f}s"
            if self._window_start is not None
            else "n/a"
        )
        return (
            f"EventAggregator: size={self.size()} "
            f"window={self._window_seconds}s "
            f"elapsed={elapsed} "
            f"ready={self.is_ready()}"
        )


# ---------------------------------------------------------------------------
# ChangeEventSerializer
# ---------------------------------------------------------------------------


class ChangeEventSerializer:
    """Serialises and deserialises ChangeEvent objects and their containers.

    ChangeEventSerializer provides a unified interface for converting
    ChangeEvent, ChangeEventBatch, and ChangeEventStream objects to and from
    JSON strings.  It also supports newline-delimited JSON (NDJSON) format
    for efficient streaming serialisation of large event lists.

    All serialize methods delegate to the to_json() methods on the individual
    objects.  All deserialize methods use the from_json() classmethods.  The
    serialiser itself is stateless and can be shared across threads.

    Args:
        None.
    """

    def __init__(self) -> None:
        """Initialise the serialiser (no state)."""
        pass

    def serialize(self, event: ChangeEvent) -> str:
        """Serialise a single ChangeEvent to a JSON string.

        Args:
            event: The ChangeEvent to serialise.

        Returns:
            A JSON string.
        """
        return event.to_json()

    def deserialize(self, data: str) -> ChangeEvent:
        """Deserialise a ChangeEvent from a JSON string.

        Args:
            data: A JSON string produced by serialize or ChangeEvent.to_json.

        Returns:
            A new ChangeEvent instance.
        """
        return ChangeEvent.from_json(data)

    def serialize_batch(self, batch: ChangeEventBatch) -> str:
        """Serialise a ChangeEventBatch to a JSON string.

        Args:
            batch: The ChangeEventBatch to serialise.

        Returns:
            A JSON string.
        """
        return batch.to_json()

    def deserialize_batch(self, data: str) -> ChangeEventBatch:
        """Deserialise a ChangeEventBatch from a JSON string.

        Args:
            data: A JSON string produced by serialize_batch.

        Returns:
            A new ChangeEventBatch instance.
        """
        return ChangeEventBatch.from_json(data)

    def serialize_stream(self, stream: ChangeEventStream) -> str:
        """Serialise a ChangeEventStream to a JSON string.

        Args:
            stream: The ChangeEventStream to serialise.

        Returns:
            A JSON string.
        """
        return stream.to_json()

    def deserialize_stream(self, data: str) -> ChangeEventStream:
        """Deserialise a ChangeEventStream from a JSON string.

        Args:
            data: A JSON string produced by serialize_stream.

        Returns:
            A new ChangeEventStream instance.
        """
        return ChangeEventStream.from_json(data)

    def to_ndjson(self, events: list) -> str:
        """Serialise a list of ChangeEvent objects to NDJSON format.

        Each event is serialised to a single JSON line; lines are joined with
        newlines.

        Args:
            events: A list of ChangeEvent objects.

        Returns:
            A newline-delimited string of JSON objects.
        """
        return "\n".join(e.to_json() for e in events)

    def from_ndjson(self, data: str) -> list:
        """Deserialise a list of ChangeEvent objects from an NDJSON string.

        Each non-empty line is deserialised as a ChangeEvent.

        Args:
            data: A newline-delimited JSON string produced by to_ndjson.

        Returns:
            A list of ChangeEvent objects.
        """
        events: list = []
        for line in data.splitlines():
            line = line.strip()
            if line:
                events.append(ChangeEvent.from_json(line))
        return events


# ---------------------------------------------------------------------------
# ChangeEventFilter
# ---------------------------------------------------------------------------


class ChangeEventFilter:
    """A fluent builder for composing predicates that filter ChangeEvent lists.

    ChangeEventFilter collects a sequence of predicate functions, each added
    by a builder method that returns self so that calls can be chained.  The
    apply method runs all collected predicates against a list of events and
    returns only the events that pass every predicate (logical AND).

    Predicates are added with by_kind (filter by one or more ChangeEventKind
    values), by_coordinate (filter by a specific coordinate string),
    by_epoch (filter by a timestamp range), and by_support_size (filter by
    the size of the event's support.coords frozenset).

    The apply_to_stream method wraps apply and returns a new ChangeEventStream
    containing only the passing events.  The reset method clears all predicates
    and returns self so that the filter can be reused.

    The filter is purely functional: it does not modify the input list or
    stream; it always returns new objects.

    Args:
        None.
    """

    def __init__(self) -> None:
        """Initialise with an empty predicate list."""
        self._filters: list = []

    def by_kind(self, *kinds: ChangeEventKind) -> ChangeEventFilter:
        """Add a predicate that accepts events whose event_kind is in kinds.

        Args:
            *kinds: One or more ChangeEventKind values to accept.

        Returns:
            self (for chaining).
        """
        kind_set = set(kinds)
        self._filters.append(lambda e: e.event_kind in kind_set)
        return self

    def by_coordinate(self, coord: str) -> ChangeEventFilter:
        """Add a predicate that accepts events whose coordinate equals coord.

        Args:
            coord: The coordinate string to match.

        Returns:
            self (for chaining).
        """
        self._filters.append(lambda e, c=coord: e.coordinate == c)
        return self

    def by_epoch(self, min_epoch: float, max_epoch: float) -> ChangeEventFilter:
        """Add a predicate that accepts events within [min_epoch, max_epoch].

        Args:
            min_epoch: Minimum Unix timestamp (inclusive).
            max_epoch: Maximum Unix timestamp (inclusive).

        Returns:
            self (for chaining).
        """
        self._filters.append(
            lambda e, lo=min_epoch, hi=max_epoch: lo <= e.timestamp <= hi
        )
        return self

    def by_support_size(self, min_size: int, max_size: int) -> ChangeEventFilter:
        """Add a predicate accepting events whose support.coords size is in [min_size, max_size].

        Args:
            min_size: Minimum number of coordinates in the support (inclusive).
            max_size: Maximum number of coordinates in the support (inclusive).

        Returns:
            self (for chaining).
        """
        self._filters.append(
            lambda e, lo=min_size, hi=max_size: lo <= len(e.support.coords) <= hi
        )
        return self

    def apply(self, events: list) -> list:
        """Apply all collected predicates to the events list.

        Returns a new list containing only events that satisfy every predicate.

        Args:
            events: A list of ChangeEvent objects to filter.

        Returns:
            A filtered list of ChangeEvent objects.
        """
        result = list(events)
        for predicate in self._filters:
            result = [e for e in result if predicate(e)]
        return result

    def apply_to_stream(self, stream: ChangeEventStream) -> ChangeEventStream:
        """Apply all predicates to a ChangeEventStream and return a new stream.

        Args:
            stream: The ChangeEventStream to filter.

        Returns:
            A new ChangeEventStream containing only events that pass all
            predicates.
        """
        filtered_events = self.apply(list(stream))
        new_stream = ChangeEventStream()
        for event in filtered_events:
            new_stream.append(event)
        return new_stream

    def reset(self) -> ChangeEventFilter:
        """Clear all predicates and return self for reuse.

        Returns:
            self (for chaining).
        """
        self._filters.clear()
        return self

    def summary(self) -> str:
        """Return a human-readable summary of the filter's state.

        Returns:
            A single-line string showing the number of active predicates.
        """
        return f"ChangeEventFilter: predicates={len(self._filters)}"


# ---------------------------------------------------------------------------
# Module-level function
# ---------------------------------------------------------------------------


def emit_change_event(
    coord: str,
    kind: ChangeEventKind,
    payload: dict,
    *,
    support_coords: list | None = None,
    provenance: Any = None,
) -> ChangeEvent:
    """Create and return a ChangeEvent with a computed support set.

    This is the primary factory function for ChangeEvent objects.  It
    constructs an EncodingSupportSet from the given support_coords (defaulting
    to [coord] if not provided) and resolves the provenance_source to
    ProvenanceSource.RUNTIME if none is given.  The returned ChangeEvent has
    a freshly generated event_id and the current Unix timestamp.

    Theory alignment: this function corresponds to the emission of a semantic
    change δ at coordinate coord with support S = frozenset(support_coords),
    as described in theory2.tex §34.2.

    Args:
        coord: The primary coordinate of the change.
        kind: The ChangeEventKind classifying the change.
        payload: A dict containing the new section data at coord.
        support_coords: Optional list of coordinate strings forming the
            support set S.  Defaults to [coord] if not provided.
        provenance: Optional provenance object.  Defaults to
            ProvenanceSource.RUNTIME if not provided.

    Returns:
        A new ChangeEvent with the given parameters and computed support.
    """
    try:
        from jugeo.judgments.judgment_terms import ProvenanceSource as PS
        prov = provenance if provenance is not None else PS.RUNTIME
    except ImportError:
        prov = provenance
    support = EncodingSupportSet(
        coords=frozenset(support_coords or [coord]),
        region_type=RegionType.ARBITRARY,
        metadata={},
    )
    return ChangeEvent(
        event_kind=kind,
        coordinate=coord,
        support=support,
        payload=payload,
        provenance_source=prov,
    )
