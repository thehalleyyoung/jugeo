from __future__ import annotations

"""
theory2.tex Ch48 – "Treaty synthesis, negotiation memory, and archival semantics"
Chapter 48 §4 — Semantic archives versus raw history

# copilot: This module implements the semantic archival layer described in Ch48 §4.
# It contrasts two memory regimes:
#   (a) raw history  – an append-only log of every negotiation episode, and
#   (b) semantic archive – a compressed, tag-indexed representation that trades
#       completeness for rapid retrieval and low storage overhead.
#
# The central thesis (§4.2) is that *value-weighted* semantic compression allows
# treaty agents to reconstruct the essential meaning of past negotiations without
# replaying every raw event, provided the compression policy is monotone in value
# and the tag vocabulary is sufficiently expressive.

Design
------
The public surface is intentionally small:
  - SemanticArchivesCoordinator  – the primary facade
  - SemanticArchivesAnalyzer     – read-only analytics
  - ArchivalIndex                – mutable store
  - ArchiveEntry / SemanticTag   – immutable value objects

All state is held in plain Python structures so the module remains importable
without any external dependencies.  Optional jugeo imports are guarded.

References
----------
  theory2.tex §4.1   – "The raw-history baseline"
  theory2.tex §4.2   – "Semantic compression and value monotonicity"
  theory2.tex §4.3   – "Tag vocabularies and archive indices"
  theory2.tex §4.4   – "Retrieval and semantic distance"
  theory2.tex §4.5   – "Compaction policies"
"""

import hashlib
import logging
import math
import statistics
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

# ─── Optional jugeo imports ──────────────────────────────────────────────────

try:
    from jugeo.orchestration.treaty_memory.base import MemoryBase  # type: ignore
except ImportError:
    MemoryBase: Any = object  # type: ignore[misc,assignment]

try:
    from jugeo.core.episode import Episode  # type: ignore
except ImportError:
    Episode: Any = None  # type: ignore[misc,assignment]

try:
    from jugeo.core.value import ValueSignal  # type: ignore
except ImportError:
    ValueSignal: Any = None  # type: ignore[misc,assignment]

try:
    from jugeo.orchestration.treaty_memory.policy import CompactionPolicy  # type: ignore
except ImportError:
    CompactionPolicy: Any = None  # type: ignore[misc,assignment]

# ─── Module logger ───────────────────────────────────────────────────────────

log = logging.getLogger(__name__)

# ─── Public API ──────────────────────────────────────────────────────────────

__all__ = [
    # value objects
    "SemanticTag",
    "ArchiveEntry",
    # storage
    "ArchivalIndex",
    # analytics
    "ArchiveAnalysisReport",
    "SemanticArchivesAnalyzer",
    # facade
    "SemanticArchivesCoordinator",
    # helpers
    "make_archive_entry_from_episode",
    "tag_similarity",
    "compute_raw_hash",
    "semantic_distance",
    "build_tag_from_dict",
    "value_weighted_centroid",
    "entropy_of_tag_distribution",
    "normalise_weight",
    "score_entry_for_query",
    "compact_by_threshold",
    "compact_by_quota",
    "compact_by_recency",
]

# ─── Module-level constants ───────────────────────────────────────────────────

# Default weight assigned to a tag when none is explicitly supplied.
DEFAULT_TAG_WEIGHT: float = 1.0

# Tags with weight below this value are considered "weak" and may be ignored
# during coarse retrieval passes.
WEAK_TAG_THRESHOLD: float = 0.15

# The minimum value score an entry must have to survive a purge operation.
# Entries at or below this score are treated as low-information relics.
MIN_SURVIVAL_VALUE: float = 0.05

# Number of decimal places used when rounding compression ratios in reports.
COMPRESSION_RATIO_PRECISION: int = 4

# Sentinel string used when a semantic summary is unavailable.
EMPTY_SUMMARY_SENTINEL: str = "<no-summary>"

# Maximum number of entries returned by a single top-k query before
# explicit override.
DEFAULT_TOP_K: int = 10

# A tag label is considered "matching" when its normalised edit-distance to
# a query label is below this threshold (used in fuzzy search).
FUZZY_MATCH_THRESHOLD: float = 0.25

# When computing semantic distance between entries, weights of individual tag
# overlaps are discounted by this factor to avoid overconfidence on common tags.
TAG_OVERLAP_DISCOUNT: float = 0.9

# Hash algorithm used for raw episode digests.
RAW_HASH_ALGORITHM: str = "sha256"

# Compaction policy identifiers understood by SemanticArchivesCoordinator.
POLICY_THRESHOLD: str = "threshold"   # remove below min_survival_value
POLICY_QUOTA: str = "quota"           # keep only the top-N entries by value
POLICY_RECENCY: str = "recency"       # remove entries older than a time window

# Default quota when using the quota compaction policy.
DEFAULT_QUOTA: int = 500

# Default recency window in seconds (1 week).
DEFAULT_RECENCY_WINDOW: float = 7 * 24 * 3600.0

# Version string embedded in export payloads so importers can detect format
# mismatches.
ARCHIVE_EXPORT_VERSION: str = "1.0.0"

# ─── Value Objects ────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SemanticTag:
    """An immutable semantic label attached to an archive entry.

    Attributes
    ----------
    tag_id:
        A globally unique identifier for this tag instance.  Typically a UUID.
    label:
        Human-readable label string (e.g. ``"trade-concession"``,
        ``"sovereignty-clause"``).  Labels form the tag vocabulary.
    weight:
        A non-negative float in ``[0, 1]`` expressing the salience of this tag
        within the parent episode.  A weight of ``1.0`` means the tag is the
        dominant semantic signal; ``0.0`` means it is present but negligible.
    source_episode:
        The episode identifier from which this tag was derived.  Establishes
        provenance and enables reverse lookup.
    """

    tag_id: str
    label: str
    weight: float
    source_episode: str

    # ------------------------------------------------------------------
    def __post_init__(self) -> None:
        """Validate tag invariants on construction."""
        if not self.tag_id:
            raise ValueError("SemanticTag.tag_id must be non-empty")
        if not self.label:
            raise ValueError("SemanticTag.label must be non-empty")
        if not (0.0 <= self.weight <= 1.0):
            raise ValueError(
                f"SemanticTag.weight must be in [0, 1]; got {self.weight!r}"
            )

    # ------------------------------------------------------------------
    def is_weak(self) -> bool:
        """Return ``True`` if this tag's weight is below :data:`WEAK_TAG_THRESHOLD`."""
        return self.weight < WEAK_TAG_THRESHOLD

    # ------------------------------------------------------------------
    def rescaled(self, factor: float) -> SemanticTag:
        """Return a copy of this tag with weight multiplied by *factor*.

        The result is clamped to ``[0, 1]``.

        Parameters
        ----------
        factor:
            Positive scaling factor.
        """
        new_weight = min(1.0, max(0.0, self.weight * factor))
        return SemanticTag(
            tag_id=self.tag_id,
            label=self.label,
            weight=new_weight,
            source_episode=self.source_episode,
        )

    # ------------------------------------------------------------------
    def __repr__(self) -> str:
        return (
            f"SemanticTag(label={self.label!r}, weight={self.weight:.3f}, "
            f"src={self.source_episode!r})"
        )


# ─── Archive Entry ────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ArchiveEntry:
    """An immutable record representing one archived negotiation episode.

    An ``ArchiveEntry`` bundles the semantic tags extracted from an episode
    with a short natural-language summary and a hash of the raw episode data,
    enabling lightweight consistency checks against the original event store.

    Attributes
    ----------
    entry_id:
        Unique identifier for this archive entry (UUID).
    episode_id:
        Identifier of the source negotiation episode.
    tags:
        Tuple of :class:`SemanticTag` objects (immutable).
    semantic_summary:
        Short prose summary of the episode's semantic content.
    raw_hash:
        SHA-256 hex digest of the raw episode payload.  Used to detect
        whether the source record has changed since archival.
    archived_at:
        UNIX timestamp (float) of when this entry was created.
    value_score:
        Aggregated value score in ``[0, 1]`` representing the episode's
        importance.  Higher scores survive compaction longer.
    """

    entry_id: str
    episode_id: str
    tags: tuple[SemanticTag, ...]
    semantic_summary: str
    raw_hash: str
    archived_at: float
    value_score: float

    # ------------------------------------------------------------------
    def __post_init__(self) -> None:
        if not self.entry_id:
            raise ValueError("ArchiveEntry.entry_id must be non-empty")
        if not self.episode_id:
            raise ValueError("ArchiveEntry.episode_id must be non-empty")
        if not (0.0 <= self.value_score <= 1.0):
            raise ValueError(
                f"ArchiveEntry.value_score must be in [0, 1]; got {self.value_score!r}"
            )

    # ------------------------------------------------------------------
    def tag_labels(self) -> frozenset[str]:
        """Return the set of all tag labels attached to this entry."""
        return frozenset(t.label for t in self.tags)

    # ------------------------------------------------------------------
    def dominant_tag(self) -> SemanticTag | None:
        """Return the tag with the highest weight, or ``None`` if there are none."""
        if not self.tags:
            return None
        return max(self.tags, key=lambda t: t.weight)

    # ------------------------------------------------------------------
    def age(self) -> float:
        """Return the age of this entry in seconds relative to the current time."""
        return time.time() - self.archived_at

    # ------------------------------------------------------------------
    def is_stale(self, max_age_seconds: float) -> bool:
        """Return ``True`` if the entry is older than *max_age_seconds*."""
        return self.age() > max_age_seconds

    # ------------------------------------------------------------------
    def __repr__(self) -> str:
        return (
            f"ArchiveEntry(entry_id={self.entry_id!r}, episode={self.episode_id!r}, "
            f"tags={len(self.tags)}, value={self.value_score:.3f})"
        )


# ─── Archival Index ───────────────────────────────────────────────────────────


@dataclass(slots=True)
class ArchivalIndex:
    """A mutable, in-memory index of :class:`ArchiveEntry` objects.

    The index maintains two secondary structures that are rebuilt lazily or
    on demand:

    * ``_tag_index`` – maps each tag label to the list of entries that carry it.
    * ``_value_order`` – a list of ``(value_score, entry_id)`` pairs kept in
      descending order for fast ``top_k`` queries.

    Attributes
    ----------
    entries:
        Master dictionary mapping ``entry_id`` to :class:`ArchiveEntry`.
    _tag_index:
        Inverted index from tag label → list[ArchiveEntry].
    _value_order:
        Pre-sorted list of ``(value_score, entry_id)`` tuples, descending.
    _dirty:
        Flag indicating that the secondary structures may be stale and need
        to be rebuilt before the next read.
    """

    entries: dict[str, ArchiveEntry] = field(default_factory=dict)
    _tag_index: dict[str, list[ArchiveEntry]] = field(default_factory=dict)
    _value_order: list[tuple[float, str]] = field(default_factory=list)
    _dirty: bool = field(default=False)

    # ------------------------------------------------------------------
    def add(self, entry: ArchiveEntry) -> None:
        """Insert *entry* into the index.

        If an entry with the same ``entry_id`` already exists it is replaced
        (idempotent upsert semantics).

        Parameters
        ----------
        entry:
            The :class:`ArchiveEntry` to add.
        """
        self.entries[entry.entry_id] = entry
        self._dirty = True
        log.debug("ArchivalIndex: added entry %s (episode=%s)", entry.entry_id, entry.episode_id)

    # ------------------------------------------------------------------
    def search_by_tag(self, label: str) -> list[ArchiveEntry]:
        """Return all entries whose tag set contains a tag with *label*.

        The search is exact (case-sensitive).  For fuzzy matching use
        :func:`score_entry_for_query`.

        Parameters
        ----------
        label:
            Tag label to look up.

        Returns
        -------
        list[ArchiveEntry]
            Possibly empty list of matching entries, ordered by insertion time.
        """
        if self._dirty:
            self.rebuild_index()
        return list(self._tag_index.get(label, []))

    # ------------------------------------------------------------------
    def top_k(self, k: int = DEFAULT_TOP_K) -> list[ArchiveEntry]:
        """Return the *k* entries with the highest value scores.

        Parameters
        ----------
        k:
            Number of entries to return.  Clamped to the total number of
            entries if larger.

        Returns
        -------
        list[ArchiveEntry]
            Up to *k* entries, sorted by descending value score.
        """
        if self._dirty:
            self.rebuild_index()
        result: list[ArchiveEntry] = []
        for _, eid in self._value_order[:k]:
            entry = self.entries.get(eid)
            if entry is not None:
                result.append(entry)
        return result

    # ------------------------------------------------------------------
    def purge_low_value(self, threshold: float = MIN_SURVIVAL_VALUE) -> int:
        """Remove all entries with ``value_score <= threshold``.

        Parameters
        ----------
        threshold:
            Value score boundary.  Entries at or below this value are removed.

        Returns
        -------
        int
            Number of entries removed.
        """
        to_remove = [
            eid for eid, e in self.entries.items() if e.value_score <= threshold
        ]
        for eid in to_remove:
            del self.entries[eid]
        if to_remove:
            self._dirty = True
        log.info("ArchivalIndex: purged %d low-value entries (threshold=%.3f)", len(to_remove), threshold)
        return len(to_remove)

    # ------------------------------------------------------------------
    def rebuild_index(self) -> None:
        """Rebuild all secondary index structures from :attr:`entries`.

        Called automatically before any read operation when :attr:`_dirty` is
        ``True``.  May also be called explicitly after bulk mutations.
        """
        self._tag_index = {}
        for entry in self.entries.values():
            for tag in entry.tags:
                bucket = self._tag_index.setdefault(tag.label, [])
                if entry not in bucket:
                    bucket.append(entry)

        self._value_order = sorted(
            ((e.value_score, eid) for eid, e in self.entries.items()),
            reverse=True,
        )
        self._dirty = False
        log.debug("ArchivalIndex: rebuilt index (%d entries, %d labels)",
                  len(self.entries), len(self._tag_index))

    # ------------------------------------------------------------------
    def statistics(self) -> dict[str, Any]:
        """Return a summary statistics dictionary about the current index state.

        Returns
        -------
        dict
            Keys include ``count``, ``avg_value``, ``median_value``,
            ``min_value``, ``max_value``, ``unique_tag_labels``,
            ``total_tags``, ``dirty``.
        """
        if self._dirty:
            self.rebuild_index()

        values = [e.value_score for e in self.entries.values()]
        tag_counts = [len(e.tags) for e in self.entries.values()]

        return {
            "count": len(self.entries),
            "avg_value": statistics.mean(values) if values else 0.0,
            "median_value": statistics.median(values) if values else 0.0,
            "min_value": min(values, default=0.0),
            "max_value": max(values, default=0.0),
            "stdev_value": statistics.stdev(values) if len(values) > 1 else 0.0,
            "unique_tag_labels": len(self._tag_index),
            "total_tags": sum(tag_counts),
            "avg_tags_per_entry": statistics.mean(tag_counts) if tag_counts else 0.0,
            "dirty": self._dirty,
        }

    # ------------------------------------------------------------------
    def __len__(self) -> int:
        return len(self.entries)

    # ------------------------------------------------------------------
    def __contains__(self, entry_id: str) -> bool:
        return entry_id in self.entries

    # ------------------------------------------------------------------
    def __repr__(self) -> str:
        return f"ArchivalIndex(entries={len(self.entries)}, dirty={self._dirty})"


# ─── Analysis Report ──────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ArchiveAnalysisReport:
    """Immutable snapshot of an analysis run over an :class:`ArchivalIndex`.

    Attributes
    ----------
    report_id:
        UUID of this report instance.
    total_entries:
        Number of entries in the index at analysis time.
    compression_ratio:
        Ratio of semantic archive size to estimated raw history size.
        A value < 1.0 means the archive is smaller than the raw log.
    tag_counts:
        Mapping of tag label → occurrence count across all entries.
    avg_value:
        Mean value score across all entries.
    generated_at:
        UNIX timestamp when the report was generated.
    """

    report_id: str
    total_entries: int
    compression_ratio: float
    tag_counts: dict[str, int]
    avg_value: float
    generated_at: float

    # ------------------------------------------------------------------
    def dominant_tags(self, n: int = 5) -> list[tuple[str, int]]:
        """Return the *n* most frequent tag labels, descending by count.

        Parameters
        ----------
        n:
            How many top tags to return.
        """
        sorted_tags = sorted(self.tag_counts.items(), key=lambda kv: kv[1], reverse=True)
        return sorted_tags[:n]

    # ------------------------------------------------------------------
    def __repr__(self) -> str:
        return (
            f"ArchiveAnalysisReport(id={self.report_id!r}, "
            f"entries={self.total_entries}, "
            f"compression={self.compression_ratio:.4f}, "
            f"avg_value={self.avg_value:.3f})"
        )


# ─── Analyzer ─────────────────────────────────────────────────────────────────


class SemanticArchivesAnalyzer:
    """Read-only analytics facade for :class:`ArchivalIndex` objects.

    All methods are pure with respect to the index – none of them mutate state.

    This class corresponds to the analytical layer described in theory2.tex §4.3,
    where the *archive quality* is assessed independently of the compaction
    policy being applied.
    """

    # ------------------------------------------------------------------
    def analyze(self, index: ArchivalIndex) -> ArchiveAnalysisReport:
        """Run a full analysis pass and return an :class:`ArchiveAnalysisReport`.

        Parameters
        ----------
        index:
            The :class:`ArchivalIndex` to analyse.

        Returns
        -------
        ArchiveAnalysisReport
            A frozen snapshot of the current archive quality metrics.
        """
        values = self.value_distribution(index)
        avg_value = statistics.mean(values) if values else 0.0
        compression = round(
            self.semantic_compression_ratio(index), COMPRESSION_RATIO_PRECISION
        )
        tag_counts = self.tag_distribution(index)

        report = ArchiveAnalysisReport(
            report_id=str(uuid.uuid4()),
            total_entries=len(index),
            compression_ratio=compression,
            tag_counts=tag_counts,
            avg_value=avg_value,
            generated_at=time.time(),
        )
        log.info("SemanticArchivesAnalyzer: generated report %s", report.report_id)
        return report

    # ------------------------------------------------------------------
    def semantic_compression_ratio(self, index: ArchivalIndex) -> float:
        """Estimate the semantic compression ratio of *index*.

        The ratio is defined as::

            ratio = semantic_bits / raw_bits

        where ``semantic_bits`` is approximated as the total number of tags
        across all entries (each tag ≈ one semantic atom), and ``raw_bits`` is
        approximated as the number of entries × a nominal raw-episode size
        constant (chosen to reflect typical raw event sizes in practice).

        A ratio below 1.0 means the archive represents less information than
        the original raw history would have consumed.

        Parameters
        ----------
        index:
            The index to measure.

        Returns
        -------
        float
            Compression ratio in ``(0, ∞)``.  Returns ``1.0`` if the index is
            empty (no compression information available).
        """
        # Nominal raw size constant (arbitrary units – chosen empirically).
        NOMINAL_RAW_SIZE: int = 20  # atoms per raw episode

        n = len(index)
        if n == 0:
            return 1.0

        total_tags = sum(len(e.tags) for e in index.entries.values())
        semantic_size = total_tags if total_tags > 0 else n
        raw_size = n * NOMINAL_RAW_SIZE
        return semantic_size / raw_size

    # ------------------------------------------------------------------
    def tag_distribution(self, index: ArchivalIndex) -> dict[str, int]:
        """Count occurrences of each tag label across all entries.

        Parameters
        ----------
        index:
            Source index.

        Returns
        -------
        dict[str, int]
            Mapping from label to total occurrence count.
        """
        dist: dict[str, int] = {}
        for entry in index.entries.values():
            for tag in entry.tags:
                dist[tag.label] = dist.get(tag.label, 0) + 1
        return dist

    # ------------------------------------------------------------------
    def value_distribution(self, index: ArchivalIndex) -> list[float]:
        """Return a list of all ``value_score`` values in the index.

        Useful for plotting histograms or computing quantile statistics.

        Parameters
        ----------
        index:
            Source index.

        Returns
        -------
        list[float]
            Unordered list of value scores.
        """
        return [e.value_score for e in index.entries.values()]

    # ------------------------------------------------------------------
    def entropy_report(self, index: ArchivalIndex) -> dict[str, float]:
        """Compute Shannon entropy of the tag label distribution.

        Higher entropy indicates a more diverse tag vocabulary, which generally
        correlates with higher semantic richness.

        Parameters
        ----------
        index:
            Source index.

        Returns
        -------
        dict
            Keys: ``tag_entropy``, ``value_entropy``.
        """
        tag_dist = self.tag_distribution(index)
        tag_ent = entropy_of_tag_distribution(tag_dist)

        value_dist = self.value_distribution(index)
        # Discretise value scores into 10 buckets for entropy estimation.
        buckets: dict[int, int] = {}
        for v in value_dist:
            bucket = int(v * 10)
            buckets[bucket] = buckets.get(bucket, 0) + 1
        value_ent = entropy_of_tag_distribution(buckets)

        return {"tag_entropy": tag_ent, "value_entropy": value_ent}


# ─── Coordinator ──────────────────────────────────────────────────────────────


class SemanticArchivesCoordinator:
    """High-level facade for managing the semantic archive lifecycle.

    The coordinator owns an :class:`ArchivalIndex` and exposes the operations
    that treaty agents use to persist, retrieve, and compact memories.

    This is the primary entry-point described in theory2.tex §4.5 –
    "The coordinator pattern for semantic archival".

    Attributes
    ----------
    _index:
        The underlying :class:`ArchivalIndex`.
    _analyzer:
        Shared :class:`SemanticArchivesAnalyzer` instance.
    _created_at:
        UNIX timestamp of coordinator instantiation.
    _archive_count:
        Running total of archive operations performed.
    """

    def __init__(self) -> None:
        """Initialise a fresh coordinator with an empty archive."""
        self._index: ArchivalIndex = ArchivalIndex()
        self._analyzer: SemanticArchivesAnalyzer = SemanticArchivesAnalyzer()
        self._created_at: float = time.time()
        self._archive_count: int = 0
        log.debug("SemanticArchivesCoordinator initialised at %.3f", self._created_at)

    # ------------------------------------------------------------------
    def archive_episode(
        self, episode: dict[str, Any], tags: list[dict[str, Any]]
    ) -> ArchiveEntry:
        """Create an :class:`ArchiveEntry` from a raw episode dict and add it to
        the index.

        Parameters
        ----------
        episode:
            Raw episode payload.  Must contain at least ``"id"`` and
            optionally ``"summary"``, ``"value_score"``.
        tags:
            List of tag dicts.  Each dict should contain ``"label"`` and
            optionally ``"weight"``.

        Returns
        -------
        ArchiveEntry
            The newly created and indexed entry.
        """
        entry = make_archive_entry_from_episode(episode, tags)
        self._index.add(entry)
        self._archive_count += 1
        log.info(
            "SemanticArchivesCoordinator: archived episode %s → entry %s",
            entry.episode_id,
            entry.entry_id,
        )
        return entry

    # ------------------------------------------------------------------
    def retrieve(
        self,
        query_tags: list[str],
        top_k: int = DEFAULT_TOP_K,
    ) -> list[ArchiveEntry]:
        """Retrieve entries that best match *query_tags*.

        Scores each entry in the index using :func:`score_entry_for_query`
        and returns the *top_k* results by descending score.

        Parameters
        ----------
        query_tags:
            List of tag label strings to query against.
        top_k:
            Maximum number of results to return.

        Returns
        -------
        list[ArchiveEntry]
            Up to *top_k* entries, sorted by relevance score.
        """
        scored: list[tuple[float, ArchiveEntry]] = []
        for entry in self._index.entries.values():
            score = score_entry_for_query(entry, query_tags)
            if score > 0.0:
                scored.append((score, entry))

        scored.sort(key=lambda x: x[0], reverse=True)
        results = [e for _, e in scored[:top_k]]
        log.debug(
            "SemanticArchivesCoordinator.retrieve: query=%r → %d results",
            query_tags,
            len(results),
        )
        return results

    # ------------------------------------------------------------------
    def compact(self, policy: str = POLICY_THRESHOLD) -> int:
        """Run a compaction pass on the archive using *policy*.

        Parameters
        ----------
        policy:
            One of ``"threshold"``, ``"quota"``, ``"recency"``.

        Returns
        -------
        int
            Number of entries removed.
        """
        before = len(self._index)
        if policy == POLICY_THRESHOLD:
            removed = compact_by_threshold(self._index, MIN_SURVIVAL_VALUE)
        elif policy == POLICY_QUOTA:
            removed = compact_by_quota(self._index, DEFAULT_QUOTA)
        elif policy == POLICY_RECENCY:
            removed = compact_by_recency(self._index, DEFAULT_RECENCY_WINDOW)
        else:
            log.warning("SemanticArchivesCoordinator.compact: unknown policy %r", policy)
            removed = 0

        after = len(self._index)
        log.info(
            "SemanticArchivesCoordinator.compact [%s]: %d → %d entries (%d removed)",
            policy,
            before,
            after,
            removed,
        )
        return removed

    # ------------------------------------------------------------------
    def export(self) -> dict[str, Any]:
        """Serialise the entire archive to a plain dict suitable for JSON.

        Returns
        -------
        dict
            Keys: ``version``, ``exported_at``, ``entries``.
        """
        serialised_entries = []
        for entry in self._index.entries.values():
            serialised_tags = [
                {
                    "tag_id": t.tag_id,
                    "label": t.label,
                    "weight": t.weight,
                    "source_episode": t.source_episode,
                }
                for t in entry.tags
            ]
            serialised_entries.append(
                {
                    "entry_id": entry.entry_id,
                    "episode_id": entry.episode_id,
                    "tags": serialised_tags,
                    "semantic_summary": entry.semantic_summary,
                    "raw_hash": entry.raw_hash,
                    "archived_at": entry.archived_at,
                    "value_score": entry.value_score,
                }
            )

        payload = {
            "version": ARCHIVE_EXPORT_VERSION,
            "exported_at": time.time(),
            "entries": serialised_entries,
        }
        log.info("SemanticArchivesCoordinator.export: exported %d entries", len(serialised_entries))
        return payload

    # ------------------------------------------------------------------
    def import_archive(self, data: dict[str, Any]) -> int:
        """Load entries from a previously exported *data* dict.

        Existing entries with the same ``entry_id`` are overwritten.

        Parameters
        ----------
        data:
            Dict produced by :meth:`export`.

        Returns
        -------
        int
            Number of entries imported.
        """
        version = data.get("version", "unknown")
        if version != ARCHIVE_EXPORT_VERSION:
            log.warning(
                "SemanticArchivesCoordinator.import_archive: "
                "version mismatch (expected %s, got %s)",
                ARCHIVE_EXPORT_VERSION,
                version,
            )

        count = 0
        for raw in data.get("entries", []):
            tags = tuple(
                SemanticTag(
                    tag_id=t["tag_id"],
                    label=t["label"],
                    weight=float(t["weight"]),
                    source_episode=t["source_episode"],
                )
                for t in raw.get("tags", [])
            )
            entry = ArchiveEntry(
                entry_id=raw["entry_id"],
                episode_id=raw["episode_id"],
                tags=tags,
                semantic_summary=raw.get("semantic_summary", EMPTY_SUMMARY_SENTINEL),
                raw_hash=raw.get("raw_hash", ""),
                archived_at=float(raw.get("archived_at", 0.0)),
                value_score=float(raw.get("value_score", 0.0)),
            )
            self._index.add(entry)
            count += 1

        log.info("SemanticArchivesCoordinator.import_archive: imported %d entries", count)
        return count

    # ------------------------------------------------------------------
    @property
    def index(self) -> ArchivalIndex:
        """Read-only access to the underlying :class:`ArchivalIndex`."""
        return self._index

    # ------------------------------------------------------------------
    def report(self) -> ArchiveAnalysisReport:
        """Convenience shorthand for ``analyzer.analyze(index)``."""
        return self._analyzer.analyze(self._index)

    # ------------------------------------------------------------------
    def __repr__(self) -> str:
        return (
            f"SemanticArchivesCoordinator("
            f"entries={len(self._index)}, "
            f"archived={self._archive_count})"
        )


# ─── Helper Functions ─────────────────────────────────────────────────────────


def compute_raw_hash(data: dict[str, Any]) -> str:
    """Compute a deterministic SHA-256 hex digest of a dict *data*.

    The dict is serialised to a canonical byte string by sorting keys
    recursively before hashing, so insertion order does not affect the result.

    Parameters
    ----------
    data:
        Arbitrary dict (values must be JSON-serialisable).

    Returns
    -------
    str
        64-character lowercase hex string.
    """

    def _canonical(obj: Any) -> str:
        if isinstance(obj, dict):
            pairs = sorted((str(k), _canonical(v)) for k, v in obj.items())
            return "{" + ",".join(f"{k}:{v}" for k, v in pairs) + "}"
        if isinstance(obj, (list, tuple)):
            return "[" + ",".join(_canonical(x) for x in obj) + "]"
        return repr(obj)

    canonical_str = _canonical(data)
    return hashlib.new(RAW_HASH_ALGORITHM, canonical_str.encode()).hexdigest()


# ------------------------------------------------------------------
def build_tag_from_dict(
    tag_dict: dict[str, Any],
    source_episode: str,
) -> SemanticTag:
    """Construct a :class:`SemanticTag` from a raw dict representation.

    The dict must contain at least ``"label"``.  Optional keys: ``"weight"``,
    ``"tag_id"``.

    Parameters
    ----------
    tag_dict:
        Raw tag payload.
    source_episode:
        Episode identifier to embed as provenance.

    Returns
    -------
    SemanticTag
        Fully constructed tag.
    """
    label = str(tag_dict.get("label", "")).strip()
    if not label:
        raise ValueError(f"tag_dict must contain a non-empty 'label'; got {tag_dict!r}")

    raw_weight = tag_dict.get("weight", DEFAULT_TAG_WEIGHT)
    weight = normalise_weight(float(raw_weight))
    tag_id = str(tag_dict.get("tag_id", uuid.uuid4()))
    return SemanticTag(
        tag_id=tag_id,
        label=label,
        weight=weight,
        source_episode=source_episode,
    )


# ------------------------------------------------------------------
def normalise_weight(w: float) -> float:
    """Clamp *w* to ``[0.0, 1.0]`` and return the result.

    Parameters
    ----------
    w:
        Raw weight value (may be outside ``[0, 1]``).

    Returns
    -------
    float
        Clamped weight in ``[0.0, 1.0]``.
    """
    return max(0.0, min(1.0, w))


# ------------------------------------------------------------------
def make_archive_entry_from_episode(
    episode: dict[str, Any],
    tags: list[dict[str, Any]],
) -> ArchiveEntry:
    """Construct an :class:`ArchiveEntry` from a raw episode dict and tag list.

    Parameters
    ----------
    episode:
        Must contain ``"id"`` (str).  Optional: ``"summary"`` (str),
        ``"value_score"`` (float in ``[0,1]``).
    tags:
        List of tag dicts; each must have ``"label"``.

    Returns
    -------
    ArchiveEntry
        A fresh, immutable archive entry with a new UUID.
    """
    episode_id = str(episode.get("id", uuid.uuid4()))
    summary = str(episode.get("summary", EMPTY_SUMMARY_SENTINEL))
    value_score = normalise_weight(float(episode.get("value_score", 0.5)))
    raw_hash = compute_raw_hash(episode)

    semantic_tags = tuple(
        build_tag_from_dict(t, episode_id) for t in tags if t
    )

    return ArchiveEntry(
        entry_id=str(uuid.uuid4()),
        episode_id=episode_id,
        tags=semantic_tags,
        semantic_summary=summary,
        raw_hash=raw_hash,
        archived_at=time.time(),
        value_score=value_score,
    )


# ------------------------------------------------------------------
def tag_similarity(a: SemanticTag, b: SemanticTag) -> float:
    """Compute a scalar similarity between two :class:`SemanticTag` objects.

    The similarity is defined as::

        sim(a, b) = label_match * geometric_mean(a.weight, b.weight)

    where ``label_match`` is 1.0 if the labels are identical and
    ``1 - normalised_edit_distance(a.label, b.label)`` otherwise.

    The geometric mean of weights ensures that a tag with near-zero weight
    does not produce a high similarity even if the labels match.

    Parameters
    ----------
    a:
        First tag.
    b:
        Second tag.

    Returns
    -------
    float
        Similarity score in ``[0.0, 1.0]``.
    """
    if a.label == b.label:
        label_score = 1.0
    else:
        label_score = 1.0 - _normalised_edit_distance(a.label, b.label)

    weight_score = math.sqrt(a.weight * b.weight)
    return label_score * weight_score


# ------------------------------------------------------------------
def semantic_distance(a: ArchiveEntry, b: ArchiveEntry) -> float:
    """Compute the semantic distance between two :class:`ArchiveEntry` objects.

    Distance is defined as ``1 - max_tag_overlap_similarity``, where the
    overlap similarity is the maximum :func:`tag_similarity` over all pairs
    ``(tag_a, tag_b)`` drawn from *a* and *b* respectively, discounted by
    :data:`TAG_OVERLAP_DISCOUNT` for common tags (those with weight > 0.5 in
    both entries).

    Returns ``1.0`` (maximum distance) if either entry has no tags.

    Parameters
    ----------
    a:
        First entry.
    b:
        Second entry.

    Returns
    -------
    float
        Distance in ``[0.0, 1.0]``.  Lower means more similar.
    """
    if not a.tags or not b.tags:
        return 1.0

    best = 0.0
    for ta in a.tags:
        for tb in b.tags:
            sim = tag_similarity(ta, tb)
            # Discount highly common (high-weight) tag pairs to avoid
            # spurious similarity from domain-ubiquitous tags.
            if ta.weight > 0.5 and tb.weight > 0.5:
                sim *= TAG_OVERLAP_DISCOUNT
            if sim > best:
                best = sim

    return 1.0 - best


# ------------------------------------------------------------------
def score_entry_for_query(
    entry: ArchiveEntry, query_tags: list[str]
) -> float:
    """Score an :class:`ArchiveEntry` against a list of query tag labels.

    The score is the sum of weights of tags in *entry* whose labels appear
    in *query_tags*, multiplied by the entry's value score.

    Parameters
    ----------
    entry:
        The entry to score.
    query_tags:
        List of tag labels to match against.

    Returns
    -------
    float
        Non-negative relevance score.  Higher is more relevant.
    """
    if not query_tags:
        return 0.0

    query_set = set(query_tags)
    matched_weight = sum(
        t.weight for t in entry.tags if t.label in query_set
    )
    # Weight by value score so high-value entries rank higher on ties.
    return matched_weight * (1.0 + entry.value_score)


# ------------------------------------------------------------------
def value_weighted_centroid(entries: list[ArchiveEntry]) -> dict[str, float]:
    """Compute the value-weighted centroid of a collection of entries.

    The centroid is expressed as a mapping from tag label to aggregated
    weighted presence score, normalised by the total value mass.

    This is the "centroid" construction described in theory2.tex §4.4 –
    used to represent a cluster of episodes as a single semantic point.

    Parameters
    ----------
    entries:
        Non-empty list of :class:`ArchiveEntry` objects.

    Returns
    -------
    dict[str, float]
        Label → centroid weight in ``[0, 1]``.
    """
    if not entries:
        return {}

    total_value = sum(e.value_score for e in entries) or 1.0
    accumulator: dict[str, float] = {}

    for entry in entries:
        weight_factor = entry.value_score / total_value
        for tag in entry.tags:
            accumulator[tag.label] = (
                accumulator.get(tag.label, 0.0) + tag.weight * weight_factor
            )

    # Normalise to [0, 1].
    max_val = max(accumulator.values(), default=1.0) or 1.0
    return {k: v / max_val for k, v in accumulator.items()}


# ------------------------------------------------------------------
def entropy_of_tag_distribution(dist: dict[str, int]) -> float:
    """Compute the Shannon entropy (in bits) of a tag frequency distribution.

    Parameters
    ----------
    dist:
        Mapping from tag label (or bucket key) to non-negative count.

    Returns
    -------
    float
        Entropy in bits.  Returns ``0.0`` for an empty distribution.
    """
    total = sum(dist.values())
    if total == 0:
        return 0.0

    entropy = 0.0
    for count in dist.values():
        if count <= 0:
            continue
        p = count / total
        entropy -= p * math.log2(p)

    return entropy


# ------------------------------------------------------------------
def compact_by_threshold(
    index: ArchivalIndex,
    threshold: float = MIN_SURVIVAL_VALUE,
) -> int:
    """Remove all entries from *index* with ``value_score <= threshold``.

    Parameters
    ----------
    index:
        Mutable index to compact in place.
    threshold:
        Value score boundary.

    Returns
    -------
    int
        Number of entries removed.
    """
    return index.purge_low_value(threshold)


# ------------------------------------------------------------------
def compact_by_quota(
    index: ArchivalIndex,
    quota: int = DEFAULT_QUOTA,
) -> int:
    """Keep only the top *quota* entries by value score; remove the rest.

    Parameters
    ----------
    index:
        Mutable index to compact in place.
    quota:
        Maximum number of entries to retain.

    Returns
    -------
    int
        Number of entries removed.
    """
    if len(index) <= quota:
        return 0

    top_ids = {e.entry_id for e in index.top_k(quota)}
    to_remove = [eid for eid in list(index.entries) if eid not in top_ids]
    for eid in to_remove:
        del index.entries[eid]
    if to_remove:
        index._dirty = True  # noqa: SLF001
    log.info("compact_by_quota: removed %d entries (quota=%d)", len(to_remove), quota)
    return len(to_remove)


# ------------------------------------------------------------------
def compact_by_recency(
    index: ArchivalIndex,
    max_age_seconds: float = DEFAULT_RECENCY_WINDOW,
) -> int:
    """Remove entries older than *max_age_seconds*.

    Parameters
    ----------
    index:
        Mutable index to compact in place.
    max_age_seconds:
        Age threshold in seconds.

    Returns
    -------
    int
        Number of entries removed.
    """
    now = time.time()
    cutoff = now - max_age_seconds
    to_remove = [
        eid for eid, e in index.entries.items() if e.archived_at < cutoff
    ]
    for eid in to_remove:
        del index.entries[eid]
    if to_remove:
        index._dirty = True  # noqa: SLF001
    log.info("compact_by_recency: removed %d stale entries", len(to_remove))
    return len(to_remove)


# ─── Internal Helpers ─────────────────────────────────────────────────────────


def _normalised_edit_distance(s: str, t: str) -> float:
    """Return the normalised Levenshtein edit distance between *s* and *t*.

    The result is in ``[0.0, 1.0]``:
    * ``0.0`` means the strings are identical.
    * ``1.0`` means they share no characters (maximum edit distance).

    Uses the standard dynamic-programming recurrence.

    Parameters
    ----------
    s:
        Source string.
    t:
        Target string.

    Returns
    -------
    float
        Normalised edit distance.
    """
    if s == t:
        return 0.0
    if not s or not t:
        return 1.0

    m, n = len(s), len(t)
    # dp[j] holds the edit distance between s[:i] and t[:j].
    dp = list(range(n + 1))
    for i in range(1, m + 1):
        prev = dp[0]
        dp[0] = i
        for j in range(1, n + 1):
            temp = dp[j]
            if s[i - 1] == t[j - 1]:
                dp[j] = prev
            else:
                dp[j] = 1 + min(prev, dp[j], dp[j - 1])
            prev = temp

    max_dist = max(m, n)
    return dp[n] / max_dist


# ─── Smoke-test helpers ───────────────────────────────────────────────────────


def pytest_approx_like(expected: float, rel: float = 1e-2) -> float:
    """Tiny tolerance helper used only in the smoke test."""
    return expected  # direct comparison is sufficient for smoke


# ─── Smoke test ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.WARNING)
    print(f"[smoke] {__file__}")

    # ── 1. Build tags and entries ────────────────────────────────────────────
    tag_a = SemanticTag(
        tag_id=str(uuid.uuid4()),
        label="trade-concession",
        weight=0.9,
        source_episode="ep-001",
    )
    tag_b = SemanticTag(
        tag_id=str(uuid.uuid4()),
        label="sovereignty-clause",
        weight=0.6,
        source_episode="ep-001",
    )
    tag_c = SemanticTag(
        tag_id=str(uuid.uuid4()),
        label="trade-concession",
        weight=0.4,
        source_episode="ep-002",
    )

    assert not tag_a.is_weak(), "tag_a should not be weak"
    assert abs(tag_a.rescaled(0.1).weight - 0.09) < 1e-9, "rescaled weight mismatch"

    entry1 = ArchiveEntry(
        entry_id=str(uuid.uuid4()),
        episode_id="ep-001",
        tags=(tag_a, tag_b),
        semantic_summary="Trade concession with sovereignty carve-out.",
        raw_hash=compute_raw_hash({"id": "ep-001"}),
        archived_at=time.time(),
        value_score=0.85,
    )
    entry2 = ArchiveEntry(
        entry_id=str(uuid.uuid4()),
        episode_id="ep-002",
        tags=(tag_c,),
        semantic_summary="Minor trade concession.",
        raw_hash=compute_raw_hash({"id": "ep-002"}),
        archived_at=time.time() - 10,
        value_score=0.3,
    )

    assert entry1.dominant_tag().label == "trade-concession"
    assert "trade-concession" in entry1.tag_labels()

    # ── 2. ArchivalIndex operations ──────────────────────────────────────────
    idx = ArchivalIndex()
    idx.add(entry1)
    idx.add(entry2)
    assert len(idx) == 2

    results = idx.search_by_tag("trade-concession")
    assert len(results) == 2, f"Expected 2, got {len(results)}"

    top = idx.top_k(1)
    assert top[0].entry_id == entry1.entry_id, "Top entry should be entry1"

    stats = idx.statistics()
    assert stats["count"] == 2
    assert stats["unique_tag_labels"] == 2  # trade-concession, sovereignty-clause

    # ── 3. Helpers ───────────────────────────────────────────────────────────
    sim = tag_similarity(tag_a, tag_c)
    assert 0.0 < sim <= 1.0, f"tag_similarity out of range: {sim}"

    dist = semantic_distance(entry1, entry2)
    assert 0.0 <= dist <= 1.0, f"semantic_distance out of range: {dist}"

    h = compute_raw_hash({"id": "ep-001", "value": 42})
    assert len(h) == 64, "SHA-256 hex digest should be 64 chars"

    score = score_entry_for_query(entry1, ["trade-concession"])
    assert score > 0.0

    centroid = value_weighted_centroid([entry1, entry2])
    assert "trade-concession" in centroid

    ent = entropy_of_tag_distribution({"a": 5, "b": 5})
    assert abs(ent - 1.0) < 1e-9, f"Expected entropy=1.0, got {ent}"

    # ── 4. Coordinator round-trip ────────────────────────────────────────────
    coordinator = SemanticArchivesCoordinator()
    ep = {"id": "ep-smoke-1", "summary": "Smoke test episode", "value_score": 0.7}
    tgs = [{"label": "border-treaty", "weight": 0.8}, {"label": "tariff", "weight": 0.5}]
    archived = coordinator.archive_episode(ep, tgs)
    assert archived.episode_id == "ep-smoke-1"

    retrieved = coordinator.retrieve(["border-treaty"])
    assert len(retrieved) == 1
    assert retrieved[0].episode_id == "ep-smoke-1"

    exported = coordinator.export()
    assert exported["version"] == ARCHIVE_EXPORT_VERSION
    assert len(exported["entries"]) == 1

    c2 = SemanticArchivesCoordinator()
    n_imported = c2.import_archive(exported)
    assert n_imported == 1
    assert len(c2.index) == 1

    # ── 5. Analyzer ──────────────────────────────────────────────────────────
    analyzer = SemanticArchivesAnalyzer()
    report = analyzer.analyze(coordinator.index)
    assert report.total_entries == 1
    assert 0.0 < report.compression_ratio
    assert "border-treaty" in report.tag_counts
    ent_report = analyzer.entropy_report(coordinator.index)
    assert "tag_entropy" in ent_report

    # ── 6. Compaction ────────────────────────────────────────────────────────
    idx2 = ArchivalIndex()
    for i in range(20):
        e = ArchiveEntry(
            entry_id=str(uuid.uuid4()),
            episode_id=f"ep-bulk-{i}",
            tags=(SemanticTag(str(uuid.uuid4()), "bulk-tag", float(i) / 20, f"ep-bulk-{i}"),),
            semantic_summary=f"Bulk episode {i}",
            raw_hash=compute_raw_hash({"id": i}),
            archived_at=time.time() - i * 100,
            value_score=float(i) / 20,
        )
        idx2.add(e)

    removed_thresh = compact_by_threshold(idx2, threshold=0.25)
    assert removed_thresh > 0

    idx3 = ArchivalIndex()
    for i in range(20):
        e = ArchiveEntry(
            entry_id=str(uuid.uuid4()),
            episode_id=f"ep-quota-{i}",
            tags=(),
            semantic_summary="",
            raw_hash="",
            archived_at=time.time(),
            value_score=float(i) / 20,
        )
        idx3.add(e)
    removed_quota = compact_by_quota(idx3, quota=5)
    assert len(idx3) == 5, f"Expected 5 after quota, got {len(idx3)}"

    print("[smoke] PASS")
    sys.exit(0)
