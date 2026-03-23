"""
Federated Knowledge — Step 2 of the Discovery Federation Pipeline.

This module implements the knowledge propagation and merging subsystem
for the JuGeo Discovery Federation (theory2.tex Ch61). After a discovery
has been promoted to authority status (see discovery_as_authority.py),
its associated knowledge must be propagated to all federation nodes and
any conflicting knowledge claims must be resolved through a merge strategy.

The knowledge propagation pipeline:
  1. A KnowledgeEntry is created from an authoritative discovery.
  2. The KnowledgePropagator distributes the entry to all registered nodes.
  3. At each target node the KnowledgeRepository stores the entry.
  4. If a conflicting entry exists, the KnowledgeMerger resolves the
     conflict using the configured MergeStrategy.
  5. The FederatedKnowledgeRunner orchestrates the entire pipeline.

Merge Strategies
----------------
UNION          — Combine all fields from both entries; prefer newer values.
INTERSECTION   — Retain only fields present in all entries.
LATEST         — Keep only the most recently timestamped entry.
TRUST_WEIGHTED — Compute a weighted blend of numeric fields using
                 each entry's trust_weight.
CONSENSUS      — Require a majority of entries to agree on each field.

copilot: shared-core marker
theory2.tex Ch61 — Federated Discovery Authority
"""

from __future__ import annotations

import math
import time
import uuid
import logging
import hashlib
import itertools
import statistics
import collections
from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from dataclasses import dataclass, field

try:
    from jugeo.core.logging import get_logger as _get_logger  # type: ignore
    _log: logging.Logger = _get_logger(__name__)
except ImportError:  # pragma: no cover — optional dependency
    _log = logging.getLogger(__name__)

__all__ = [
    "PropagationStatus",
    "MergeStrategy",
    "KnowledgeEntry",
    "MergeResult",
    "KnowledgePropagator",
    "KnowledgeMerger",
    "KnowledgeRepository",
    "FederatedKnowledgeRunner",
    "propagate_knowledge",
    "merge_knowledge",
]

# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _utcnow() -> float:
    """Return the current UTC time as a POSIX timestamp (float seconds).

    This helper centralises all timestamp generation so that it can be
    monkeypatched in tests without touching the standard library directly.
    The value returned is always a finite, positive float representing the
    number of seconds elapsed since the Unix epoch (1970-01-01 00:00:00 UTC).

    Returns:
        float: Current UTC POSIX timestamp, e.g. 1_700_000_000.123456.

    Notes:
        - Uses ``time.time()`` which is subject to NTP adjustments.
        - For monotonic timing, use ``time.monotonic()`` instead.
        - All timestamps stored in dataclasses in this module are produced
          by this function to ensure consistency.
    """
    return time.time()


def _uid() -> str:
    """Generate a compact, URL-safe unique identifier string.

    Produces a 32-character lowercase hexadecimal string derived from a
    version-4 (random) UUID.  The hyphens from the standard UUID string
    representation are stripped so that the result can be used safely as
    a dictionary key, filename component, or database identifier without
    further escaping.

    Returns:
        str: A 32-character hex string such as ``'a1b2c3d4e5f6...'``.

    Notes:
        - Uniqueness relies on ``uuid.uuid4()`` which uses OS-level
          entropy sources.  Collision probability across 2^61 IDs is
          roughly 50 % (birthday paradox), which is sufficient for all
          federation use-cases in JuGeo.
        - Do **not** use this function for cryptographic purposes.
    """
    return uuid.uuid4().hex


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp *value* to the closed interval [*lo*, *hi*].

    Guarantees that the returned float satisfies ``lo <= result <= hi``.
    This helper is used throughout the module to bound confidence scores,
    trust weights, and other bounded numeric fields before they are stored
    in immutable dataclasses.

    Args:
        value (float): The input value to be clamped.
        lo (float): The inclusive lower bound of the acceptable range.
        hi (float): The inclusive upper bound of the acceptable range.

    Returns:
        float: ``lo`` if *value* < *lo*, ``hi`` if *value* > *hi*,
               otherwise *value* unchanged.

    Raises:
        ValueError: If *lo* > *hi*, which would define an empty interval.

    Examples:
        >>> _clamp(1.5, 0.0, 1.0)
        1.0
        >>> _clamp(-0.3, 0.0, 1.0)
        0.0
        >>> _clamp(0.7, 0.0, 1.0)
        0.7
    """
    if lo > hi:
        raise ValueError(f"_clamp: lo ({lo!r}) must be <= hi ({hi!r})")
    return max(lo, min(hi, value))


def _format_timestamp(timestamp: float) -> str:
    """Format a POSIX timestamp as an ISO-8601 string."""
    return datetime.fromtimestamp(float(timestamp), tz=timezone.utc).replace(
        tzinfo=None
    ).isoformat(timespec="seconds")


def _parse_timestamp(value: object) -> float:
    """Parse legacy timestamp representations into a POSIX timestamp."""
    if value is None:
        return _utcnow()
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        candidate = value.strip()
        if not candidate:
            return _utcnow()
        try:
            return float(candidate)
        except ValueError:
            pass
        try:
            parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.timestamp()
        except ValueError:
            return _utcnow()
    return _utcnow()


def _coerce_merge_strategy(strategy: "MergeStrategy | str") -> "MergeStrategy":
    """Accept both enum values and legacy case-insensitive strings."""
    if isinstance(strategy, MergeStrategy):
        return strategy
    normalized = str(strategy).strip()
    if not normalized:
        return MergeStrategy.UNION
    try:
        return MergeStrategy[normalized.upper()]
    except KeyError:
        return MergeStrategy(normalized.lower())


def _coerce_knowledge_entry(entry: "KnowledgeEntry | dict") -> "KnowledgeEntry":
    """Accept both KnowledgeEntry instances and discovery-like dicts."""
    if isinstance(entry, KnowledgeEntry):
        return entry
    discovery_id = entry.get(
        "discovery_id",
        entry.get("knowledge_id", entry.get("entry_id", _uid())),
    )
    source_node = entry.get("source_node", entry.get("node_id", "unknown"))
    content = entry.get("content")
    if not isinstance(content, dict):
        content = {
            key: value
            for key, value in entry.items()
            if key
            not in {
                "entry_id",
                "discovery_id",
                "knowledge_id",
                "source_node",
                "node_id",
                "trust_weight",
                "trust_score",
                "timestamp",
                "created_at",
                "tags",
            }
        }
    return KnowledgeEntry(
        entry_id=entry.get("entry_id", discovery_id),
        knowledge_id=entry.get("knowledge_id", discovery_id),
        source_node=source_node,
        content=dict(content),
        trust_weight=_clamp(
            float(entry.get("trust_score", entry.get("trust_weight", 1.0))),
            0.0,
            1.0,
        ),
        timestamp=_parse_timestamp(entry.get("timestamp", entry.get("created_at"))),
        tags=list(entry.get("tags", []) or []),
    )


def _entry_to_dict(entry: "KnowledgeEntry | dict") -> dict:
    """Return a legacy-compatible plain-dict representation of an entry."""
    if isinstance(entry, KnowledgeEntry):
        return entry.to_dict()
    normalized = _coerce_knowledge_entry(entry)
    payload = dict(entry)
    payload["entry_id"] = normalized.entry_id
    payload.setdefault("knowledge_id", normalized.knowledge_id)
    payload.setdefault("source_node", normalized.source_node)
    payload["node_id"] = normalized.source_node
    payload.setdefault("content", dict(normalized.content))
    payload["trust_weight"] = _clamp(float(payload.get("trust_weight", normalized.trust_weight)), 0.0, 1.0)
    payload["trust_score"] = _clamp(float(payload.get("trust_score", payload["trust_weight"])), 0.0, 1.0)
    payload["timestamp"] = _parse_timestamp(payload.get("timestamp", payload.get("created_at")))
    payload["created_at"] = payload.get("created_at") or _format_timestamp(payload["timestamp"])
    payload["tags"] = list(payload.get("tags", normalized.tags) or [])
    return payload


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class PropagationStatus(str, Enum):
    """Lifecycle states for a single knowledge-propagation task.

    Each member is also a plain ``str`` so that instances can be used
    directly in JSON serialisation, log messages, and dictionary keys
    without an explicit ``.value`` call.
    """

    QUEUED = "queued"             # task created, not yet started
    PROPAGATING = "propagating"   # actively distributing to nodes
    COMPLETE = "complete"         # all target nodes received the entry
    FAILED = "failed"             # all target nodes rejected the entry
    PARTIAL = "partial"           # some nodes succeeded, some failed


class MergeStrategy(str, Enum):
    """Algorithm used to reconcile conflicting KnowledgeEntry objects.

    Each strategy operates on a list of KnowledgeEntry instances that
    share the same ``knowledge_id`` and produces a single merged content
    dictionary.  See each strategy's corresponding ``KnowledgeMerger``
    method for the detailed algorithm.
    """

    UNION = "union"                       # combine all fields, newer wins
    INTERSECTION = "intersection"         # keep only universally-present keys
    LATEST = "latest"                     # use the most-recent entry verbatim
    TRUST_WEIGHTED = "trust_weighted"     # weighted blend of numeric fields
    CONSENSUS = "consensus"               # majority vote per field value


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class KnowledgeEntry:
    """An immutable snapshot of a knowledge claim from one federation node.

    Each instance captures a single piece of knowledge (identified by
    ``knowledge_id``) as asserted by a particular ``source_node`` at a
    specific ``timestamp``.  The ``trust_weight`` scalar (0–1) encodes
    how reliable the source node is considered to be by the federation.

    Because the dataclass is *frozen* and uses *slots*, instances are
    hashable and memory-efficient, making them safe to store in sets and
    as dictionary keys.

    Attributes:
        entry_id (str): Unique identifier for this specific entry snapshot.
        knowledge_id (str): Stable identifier for the knowledge concept,
            shared across all entries that describe the same fact.
        source_node (str): Identifier of the federation node that produced
            this entry.
        content (dict): Arbitrary key-value payload holding the actual
            knowledge fields (e.g. coordinates, classifications, scores).
        trust_weight (float): Reliability score in [0.0, 1.0] assigned to
            the source node by the federation authority.
        timestamp (float): POSIX UTC timestamp of when the entry was created.
    """

    entry_id: str
    knowledge_id: str
    source_node: str
    content: dict = field(default_factory=dict)
    trust_weight: float = 1.0
    timestamp: float = field(default_factory=_utcnow)
    tags: list[str] = field(default_factory=list)

    @classmethod
    def create(
        cls,
        knowledge_id: str | None = None,
        source_node: str | None = None,
        content: dict | None = None,
        trust_weight: float | None = None,
        *,
        node_id: str | None = None,
        trust_score: float | None = None,
        tags: list[str] | None = None,
    ) -> "KnowledgeEntry":
        """Construct a new KnowledgeEntry with auto-generated id and timestamp.

        Convenience factory that calls ``_uid()`` and ``_utcnow()``
        automatically so callers do not have to manage those fields.

        Args:
            knowledge_id (str): Stable concept identifier.
            source_node (str): Origin node identifier.
            content (dict): Knowledge payload dictionary.
            trust_weight (float): Node reliability score; clamped to [0, 1].

        Returns:
            KnowledgeEntry: A fully-initialised, frozen entry instance.
        """
        return cls(
            entry_id=_uid(),
            knowledge_id=knowledge_id or _uid(),
            source_node=source_node or node_id or "",
            content=dict(content or {}),
            trust_weight=_clamp(float(trust_weight if trust_weight is not None else (trust_score if trust_score is not None else 1.0)), 0.0, 1.0),
            timestamp=_utcnow(),
            tags=list(tags or []),
        )

    @property
    def node_id(self) -> str:
        return self.source_node

    @property
    def trust_score(self) -> float:
        return self.trust_weight

    @property
    def created_at(self) -> str:
        return _format_timestamp(self.timestamp)

    def to_dict(self) -> dict:
        """Serialise this entry to a plain dictionary.

        Produces a JSON-serialisable representation of all fields.  The
        ``content`` sub-dict is shallow-copied so that mutation of the
        returned dict does not affect the frozen instance.

        Returns:
            dict: A flat dictionary with keys matching the field names.
        """
        return {
            "entry_id": self.entry_id,
            "knowledge_id": self.knowledge_id,
            "source_node": self.source_node,
            "node_id": self.source_node,
            "content": dict(self.content),
            "trust_weight": self.trust_weight,
            "trust_score": self.trust_weight,
            "timestamp": self.timestamp,
            "created_at": self.created_at,
            "tags": list(self.tags),
        }

    def age(self) -> float:
        """Return the elapsed time in seconds since this entry was created.

        Computed as the difference between the current UTC time and the
        entry's stored timestamp.  A freshly-created entry will return a
        value close to zero; stale entries will return large positive numbers.

        Returns:
            float: Age in seconds (always >= 0 in normal operation).
        """
        return max(0.0, _utcnow() - self.timestamp)

    def weighted_repr(self) -> dict:
        """Return a copy of content with each numeric value scaled by trust_weight.

        Non-numeric values are passed through unchanged.  This representation
        is used by the TRUST_WEIGHTED merge strategy to blend contributions
        from multiple source nodes proportionally to their reliability scores.

        Returns:
            dict: Content copy where ``int`` and ``float`` values are
                  multiplied by ``self.trust_weight``.
        """
        result: dict = {}
        for key, val in self.content.items():
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                result[key] = val * self.trust_weight
            else:
                result[key] = val
        result.setdefault("entry_id", self.entry_id)
        result.setdefault("trust_score", self.trust_weight)
        return result


@dataclass(frozen=True, slots=True)
class MergeResult:
    """The output produced by a KnowledgeMerger after reconciling entries.

    Captures the merged content dictionary, the identifiers of all source
    entries that contributed to the merge, the strategy that was applied,
    and a confidence score reflecting how well the entries agreed.

    Attributes:
        result_id (str): Unique identifier for this merge result.
        merged_ids (tuple[str, ...]): Ordered entry_ids of contributing entries.
        strategy (MergeStrategy): The merge algorithm that was applied.
        merged_content (dict): The reconciled knowledge payload.
        confidence (float): Agreement score in [0.0, 1.0]; 1.0 means all
            entries agreed perfectly.
        timestamp (float): POSIX UTC timestamp of when the merge was performed.
    """

    result_id: str
    merged_ids: tuple
    strategy: MergeStrategy
    merged_content: dict = field(default_factory=dict)
    input_count: int = 0
    output_entries: list[dict] = field(default_factory=list)
    confidence: float = 1.0
    timestamp: float = field(default_factory=_utcnow)

    @classmethod
    def create(
        cls,
        merged_ids: tuple | None = None,
        strategy: MergeStrategy | str = MergeStrategy.UNION,
        merged_content: dict | None = None,
        confidence: float = 1.0,
        *,
        input_count: int | None = None,
        output_entries: list[KnowledgeEntry | dict] | None = None,
    ) -> "MergeResult":
        """Construct a MergeResult with auto-generated id and timestamp.

        Factory method that supplies ``result_id`` and ``timestamp``
        automatically.  Confidence is clamped to [0.0, 1.0].

        Args:
            merged_ids (tuple[str, ...]): entry_ids of the merged entries.
            strategy (MergeStrategy): The algorithm that produced the result.
            merged_content (dict): Reconciled content dictionary.
            confidence (float): Agreement score; clamped to [0.0, 1.0].

        Returns:
            MergeResult: A fully-initialised, frozen result instance.
        """
        normalized_output = [_entry_to_dict(entry) for entry in (output_entries or [])]
        normalized_strategy = _coerce_merge_strategy(strategy)
        normalized_ids = (
            tuple(merged_ids)
            if merged_ids is not None
            else tuple(entry["entry_id"] for entry in normalized_output)
        )
        resolved_input_count = (
            int(input_count)
            if input_count is not None
            else len(normalized_ids)
        )
        resolved_merged_content = dict(merged_content or {})
        if not resolved_merged_content and normalized_output:
            resolved_merged_content = dict(normalized_output[-1].get("content", {}))
        return cls(
            result_id=_uid(),
            merged_ids=normalized_ids,
            strategy=normalized_strategy,
            merged_content=resolved_merged_content,
            input_count=max(0, resolved_input_count),
            output_entries=normalized_output,
            confidence=_clamp(float(confidence), 0.0, 1.0),
            timestamp=_utcnow(),
        )

    @property
    def merged_at(self) -> str:
        return _format_timestamp(self.timestamp)

    def to_dict(self) -> dict:
        """Serialise the MergeResult to a plain dictionary.

        Returns a JSON-serialisable mapping of all fields.  The strategy
        enum member is converted to its string value automatically.

        Returns:
            dict: Flat serialisable dictionary representation.
        """
        return {
            "result_id": self.result_id,
            "merged_ids": list(self.merged_ids),
            "strategy": self.strategy.value,
            "merged_content": dict(self.merged_content),
            "input_count": self.input_count,
            "output_entries": [dict(entry) for entry in self.output_entries],
            "confidence": self.confidence,
            "timestamp": self.timestamp,
            "merged_at": self.merged_at,
        }

    def summary(self) -> str:
        """Return a human-readable one-line summary of this merge result.

        Formats the result_id, number of contributing entries, strategy name,
        and confidence score into a single descriptive string suitable for
        log output or progress reporting.

        Returns:
            str: A concise summary string, e.g.
                 ``"MergeResult[a1b2...] 3 entries via UNION (conf=0.87)"``.
        """
        short_id = self.result_id[:8]
        n = len(self.merged_ids)
        strat = self.strategy.value.upper()
        conf = f"{self.confidence:.3f}"
        return f"MergeResult[{short_id}] {n} entries via {strat} (conf={conf})"


# ---------------------------------------------------------------------------
# KnowledgePropagator
# ---------------------------------------------------------------------------


class KnowledgePropagator:
    """Distributes KnowledgeEntry objects to registered federation nodes.

    The propagator maintains a registry of known federation nodes and
    provides methods to push a KnowledgeEntry to individual nodes or to
    all registered nodes simultaneously.  Each propagation attempt is
    recorded in an internal log that can be inspected to determine the
    overall PropagationStatus of a knowledge concept.

    In a real federated system the ``propagate_to`` method would perform
    a network call (e.g. gRPC, HTTP, or message-queue publish) to the
    target node.  In this reference implementation the method simulates
    propagation by recording a success record in the internal log, with
    failure injected when the node's ``"simulate_failure"`` flag is set.

    The log-driven ``get_propagation_status`` method aggregates per-node
    outcomes to produce a single PropagationStatus for the knowledge_id:

    - All successes → COMPLETE
    - All failures  → FAILED
    - Mixed         → PARTIAL
    - None recorded → QUEUED
    - At least one  in-progress record → PROPAGATING

    Typical usage::

        propagator = KnowledgePropagator()
        propagator.register_node("node-alpha", {"region": "eu-west"})
        propagator.register_node("node-beta",  {"region": "us-east"})
        entry = KnowledgeEntry.create("geo:42", "node-origin", {"lat": 1.0})
        results = propagator.propagate_all(entry)
    """

    def __init__(self) -> None:
        """Initialise an empty propagator with no registered nodes.

        Creates the internal node registry (a dict mapping node_id to its
        metadata dict) and an empty propagation log list.  Both structures
        are instance-private and should only be accessed through the public
        API methods.
        """
        self._nodes: dict[str, dict] = {}
        self._propagation_log: list[dict] = []

    def register_node(
        self,
        node_id: str,
        node_info: Optional[dict] = None,
        **metadata: object,
    ) -> None:
        """Add a federation node to the propagation registry.

        If the node_id is already registered its metadata is updated with
        the supplied node_info.  This allows callers to update node metadata
        (e.g. updated endpoint URLs) without deregistering and re-registering.

        Args:
            node_id (str): Stable unique identifier for the federation node.
            node_info (Optional[dict]): Arbitrary metadata for the node such
                as ``{"region": "eu-west", "endpoint": "https://..."}``; if
                ``None`` an empty dict is stored.

        Returns:
            None
        """
        info = dict(node_info) if node_info else {}
        info.update(metadata)
        if "trust_score" in info and "trust_weight" not in info:
            info["trust_weight"] = info["trust_score"]
        info.setdefault("registered_at", _utcnow())
        self._nodes[node_id] = info
        _log.debug("Registered federation node %r", node_id)

    def propagate_to(
        self,
        knowledge_entry: KnowledgeEntry | dict,
        target_node_id: str | None = None,
        *,
        target_node: str | None = None,
    ) -> bool | dict:
        """Push a single KnowledgeEntry to one target node.

        Simulates a network push to the target node and records the outcome
        in the internal propagation log.  The simulation uses the node's
        ``"simulate_failure"`` boolean flag to decide the outcome; real
        implementations would replace this with actual I/O.

        Args:
            knowledge_entry (KnowledgeEntry): The entry to propagate.
            target_node_id (str): The destination node's identifier.

        Returns:
            bool: ``True`` if the propagation was recorded as successful,
                  ``False`` if the node was not found or flagged for failure.
        """
        entry = _coerce_knowledge_entry(knowledge_entry)
        target = target_node_id or target_node or ""
        legacy_shape = (
            isinstance(knowledge_entry, dict)
            or target_node is not None
            or target_node_id is None
        )

        if target not in self._nodes:
            _log.warning("propagate_to: unknown node %r", target)
            record = {
                "knowledge_id": entry.knowledge_id,
                "entry_id": entry.entry_id,
                "target_node": target,
                "status": PropagationStatus.FAILED,
                "reason": "unknown_node",
                "timestamp": _utcnow(),
            }
            self._propagation_log.append(record)
            return dict(record) if legacy_shape else False

        node_info = self._nodes[target]
        simulated_failure = node_info.get("simulate_failure", False)

        record: dict = {
            "knowledge_id": entry.knowledge_id,
            "entry_id": entry.entry_id,
            "target_node": target,
            "timestamp": _utcnow(),
        }

        if simulated_failure:
            record["status"] = PropagationStatus.FAILED
            record["reason"] = "simulated_failure"
            self._propagation_log.append(record)
            _log.info(
                "Propagation FAILED (simulated) for %r -> %r",
                entry.knowledge_id,
                target,
            )
            return dict(record) if legacy_shape else False

        record["status"] = PropagationStatus.COMPLETE
        record["reason"] = "ok"
        self._propagation_log.append(record)
        _log.info(
            "Propagated %r -> %r successfully",
            entry.knowledge_id,
            target,
        )
        return dict(record) if legacy_shape else True

    def propagate_all(
        self,
        knowledge_entry: KnowledgeEntry | dict | list[KnowledgeEntry | dict],
        nodes: list[str] | None = None,
    ) -> dict[str, bool] | list[dict]:
        """Propagate a KnowledgeEntry to every registered node.

        Iterates over all registered nodes in insertion order and calls
        ``propagate_to`` for each.  Returns a mapping from node_id to the
        boolean success result so callers can identify which nodes failed.

        Args:
            knowledge_entry (KnowledgeEntry): The entry to broadcast.

        Returns:
            dict[str, bool]: Mapping of node_id → True (success) or
                             False (failure) for every registered node.
        """
        target_nodes = list(nodes) if nodes is not None else list(self._nodes)
        if isinstance(knowledge_entry, list):
            results: list[dict] = []
            for entry in knowledge_entry:
                for node_id in target_nodes:
                    propagated = self.propagate_to(entry, target_node=node_id)
                    if isinstance(propagated, dict):
                        results.append(propagated)
            return results

        entry = _coerce_knowledge_entry(knowledge_entry)
        if nodes is not None:
            results_list: list[dict] = []
            for node_id in target_nodes:
                propagated = self.propagate_to(entry, target_node=node_id)
                if isinstance(propagated, dict):
                    results_list.append(propagated)
            return results_list

        results: dict[str, bool] = {}
        for node_id in target_nodes:
            propagated = self.propagate_to(entry, node_id)
            results[node_id] = bool(propagated)
        return results

    def propagate(self, knowledge_entry: KnowledgeEntry | dict) -> dict:
        """Compatibility wrapper for simple dict-based propagation."""
        entry = _coerce_knowledge_entry(knowledge_entry)
        if not self._nodes:
            self.register_node(entry.source_node)
        propagation = self.propagate_all(entry)
        return {
            "entry_id": entry.entry_id,
            "knowledge_id": entry.knowledge_id,
            "propagation": propagation,
            "status": self.get_propagation_status(entry.knowledge_id),
            "timestamp": _utcnow(),
        }

    def get_log(self) -> list[dict]:
        """Return a copy of the full propagation log.

        Each log record is a dictionary containing at minimum:
        ``knowledge_id``, ``entry_id``, ``target_node``, ``status``,
        ``reason``, and ``timestamp`` keys.

        Returns:
            list[dict]: Shallow-copied list of all propagation log records.
        """
        return list(self._propagation_log)

    def clear_log(self) -> None:
        """Remove all records from the propagation log.

        After calling this method ``get_log()`` will return an empty list
        and ``get_propagation_status()`` will return QUEUED for all
        knowledge_ids until new propagations are performed.

        Returns:
            None
        """
        self._propagation_log.clear()
        _log.debug("Propagation log cleared")

    def node_count(self) -> int:
        """Return the number of currently registered federation nodes.

        Provides a quick check of the registry size without exposing the
        internal ``_nodes`` dictionary directly.

        Returns:
            int: Number of registered nodes (>= 0).
        """
        return len(self._nodes)

    def get_propagation_status(self, knowledge_id: str) -> PropagationStatus:
        """Compute the overall PropagationStatus for a knowledge concept.

        Scans the propagation log for all records matching the given
        ``knowledge_id`` and aggregates their per-node statuses:

        - No records → QUEUED
        - Any PROPAGATING record → PROPAGATING
        - All COMPLETE → COMPLETE
        - All FAILED → FAILED
        - Mixed → PARTIAL

        Args:
            knowledge_id (str): The knowledge concept identifier to query.

        Returns:
            PropagationStatus: Aggregated status enum member.
        """
        records = [
            r for r in self._propagation_log
            if r.get("knowledge_id") == knowledge_id
        ]
        if not records:
            return PropagationStatus.QUEUED

        statuses = {r["status"] for r in records}
        if PropagationStatus.PROPAGATING in statuses:
            return PropagationStatus.PROPAGATING

        all_complete = all(s == PropagationStatus.COMPLETE for s in statuses)
        if all_complete:
            return PropagationStatus.COMPLETE

        all_failed = all(s == PropagationStatus.FAILED for s in statuses)
        if all_failed:
            return PropagationStatus.FAILED

        return PropagationStatus.PARTIAL

    def summary(self) -> str:
        """Return a human-readable summary of the propagator's current state.

        Includes the number of registered nodes and total log entries in a
        single string suitable for logging or CLI output.

        Returns:
            str: Summary string, e.g. ``"KnowledgePropagator: 3 nodes, 7 log entries"``.
        """
        return (
            f"KnowledgePropagator: {self.node_count()} nodes, "
            f"{len(self._propagation_log)} log entries"
        )


# ---------------------------------------------------------------------------
# KnowledgeMerger
# ---------------------------------------------------------------------------


class KnowledgeMerger:
    """Reconciles conflicting KnowledgeEntry objects using a configurable strategy.

    When a federation node receives a KnowledgeEntry whose ``knowledge_id``
    already exists in its repository, the KnowledgeMerger resolves the
    conflict by applying one of the five MergeStrategy algorithms.  The
    result is a new MergeResult whose ``merged_content`` can then be stored
    in the repository as the authoritative version.

    The five strategies and their trade-offs:

    UNION
        Takes the superset of all keys.  When two entries have the same key
        the value from the more-recently-timestamped entry wins.  Best when
        nodes are additive and no knowledge is ever retracted.

    INTERSECTION
        Keeps only keys that appear in *every* entry.  Eliminates unconfirmed
        claims; best when conservative certainty is more important than recall.

    LATEST
        Discards all but the entry with the highest timestamp.  Simplest
        strategy; correct when newer information supersedes older.

    TRUST_WEIGHTED
        For numeric fields, computes a weighted average using each entry's
        trust_weight.  Non-numeric fields fall back to the highest-trust
        entry's value.  Best for sensor or estimation data.

    CONSENSUS
        Each field value must appear in a strict majority (>50 %) of entries
        to be retained.  Fields without consensus are dropped.  Best when
        the federation contains many nodes and Byzantine behaviour is possible.

    Merge history is recorded internally; callers can inspect ``get_history()``
    to audit all past merges.
    """

    def __init__(self, strategy: MergeStrategy | str = MergeStrategy.UNION) -> None:
        """Initialise the merger with an initial strategy and empty history.

        Args:
            strategy (MergeStrategy): Default merge algorithm to apply.
                Can be changed at any time with ``set_strategy()``.
        """
        self._strategy: MergeStrategy = _coerce_merge_strategy(strategy)
        self._history: list[MergeResult] = []

    def _normalize_entries(
        self, entries: list[KnowledgeEntry | dict]
    ) -> list[KnowledgeEntry]:
        return [_coerce_knowledge_entry(entry) for entry in entries]

    def _build_result(
        self,
        entries: list[KnowledgeEntry | dict],
        strategy: MergeStrategy | str,
        *,
        output_entries: list[KnowledgeEntry | dict] | None = None,
        merged_content: dict | None = None,
        confidence: float = 1.0,
        record_history: bool = True,
    ) -> MergeResult:
        normalized_entries = self._normalize_entries(entries)
        result = MergeResult.create(
            merged_ids=tuple(entry.entry_id for entry in normalized_entries),
            strategy=_coerce_merge_strategy(strategy),
            merged_content=merged_content or {},
            input_count=len(entries),
            output_entries=output_entries or [],
            confidence=confidence,
        )
        if record_history:
            self._history.append(result)
        return result

    def merge_entries(self, entries: list[KnowledgeEntry | dict]) -> MergeResult:
        """Merge a list of KnowledgeEntry objects using the active strategy.

        Dispatches to the appropriate strategy method based on
        ``self._strategy``, wraps the result in a MergeResult, appends it
        to the history, and returns it.

        Args:
            entries (list[KnowledgeEntry]): Two or more entries to reconcile.
                All entries should share the same ``knowledge_id``.

        Returns:
            MergeResult: The reconciled result with merged_content and
                         confidence score.

        Raises:
            ValueError: If ``entries`` is empty.
        """
        return self.merge(entries, self._strategy)

    def merge(
        self,
        entries: list[KnowledgeEntry | dict],
        strategy: MergeStrategy | str | None = None,
    ) -> MergeResult:
        normalized_strategy = _coerce_merge_strategy(strategy or self._strategy)
        dispatch = {
            MergeStrategy.UNION: self.merge_union,
            MergeStrategy.INTERSECTION: self.merge_intersection,
            MergeStrategy.LATEST: self.merge_latest,
            MergeStrategy.TRUST_WEIGHTED: self.merge_trust_weighted,
            MergeStrategy.CONSENSUS: self.merge_consensus,
        }
        return dispatch[normalized_strategy](entries)

    def merge_union(self, entries: list[KnowledgeEntry | dict]) -> MergeResult:
        """Merge entries by taking the union of all keys; newest value wins.

        Iterates entries in ascending timestamp order so that newer entries
        overwrite older values for duplicate keys.  All keys from all entries
        are present in the result.

        Args:
            entries (list[KnowledgeEntry]): Source entries to merge.

        Returns:
            dict: Merged content dictionary containing all keys.
        """
        normalized = self._normalize_entries(entries)
        if not normalized:
            return self._build_result([], MergeStrategy.UNION, output_entries=[], merged_content={})
        sorted_entries = sorted(normalized, key=lambda e: e.timestamp)
        merged: dict = {}
        for entry in sorted_entries:
            merged.update(entry.content)
        return self._build_result(
            entries,
            MergeStrategy.UNION,
            output_entries=sorted_entries,
            merged_content=merged,
        )

    def merge_intersection(self, entries: list[KnowledgeEntry | dict]) -> MergeResult:
        """Merge entries by retaining only keys present in every entry.

        Computes the intersection of all content key-sets.  For each common
        key, the value from the most-recently-timestamped entry is used.

        Args:
            entries (list[KnowledgeEntry]): Source entries to merge.

        Returns:
            dict: Merged content dictionary with only universally-present keys.
        """
        normalized = self._normalize_entries(entries)
        if not normalized:
            return self._build_result([], MergeStrategy.INTERSECTION, output_entries=[], merged_content={})
        common_keys = set(normalized[0].content.keys())
        for entry in normalized[1:]:
            common_keys &= set(entry.content.keys())

        latest = max(normalized, key=lambda e: e.timestamp)
        merged_content = {k: latest.content[k] for k in common_keys}
        output_entries = [
            entry for entry in normalized
            if all(key in entry.content for key in common_keys)
        ] if common_keys else []
        return self._build_result(
            entries,
            MergeStrategy.INTERSECTION,
            output_entries=output_entries,
            merged_content=merged_content,
        )

    def merge_latest(self, entries: list[KnowledgeEntry | dict]) -> MergeResult:
        """Return the content of the most-recently-timestamped entry verbatim.

        Selects the entry with the maximum timestamp and returns a shallow
        copy of its content without any further modification.

        Args:
            entries (list[KnowledgeEntry]): Source entries to consider.

        Returns:
            dict: Content dictionary of the newest entry.
        """
        normalized = self._normalize_entries(entries)
        if not normalized:
            return self._build_result([], MergeStrategy.LATEST, output_entries=[], merged_content={})
        latest = max(normalized, key=lambda e: e.timestamp)
        return self._build_result(
            entries,
            MergeStrategy.LATEST,
            output_entries=[latest],
            merged_content=dict(latest.content),
        )

    def merge_trust_weighted(self, entries: list[KnowledgeEntry | dict]) -> MergeResult:
        """Merge numeric fields using a trust-weight-proportional blend.

        For each key that appears in at least one entry:
        - If the value is numeric (int or float), compute the weighted
          average across all entries that carry that key, using
          ``trust_weight`` as the weight.
        - If the value is non-numeric, use the value from the highest-trust
          entry that carries the key.

        Args:
            entries (list[KnowledgeEntry]): Source entries to merge.

        Returns:
            dict: Merged content with blended numeric fields.
        """
        normalized = self._normalize_entries(entries)
        if not normalized:
            return self._build_result([], MergeStrategy.TRUST_WEIGHTED, output_entries=[], merged_content={})
        all_keys: set[str] = set()
        for entry in normalized:
            all_keys.update(entry.content.keys())

        merged: dict = {}
        for key in all_keys:
            carriers = [(e.trust_weight, e.content[key])
                        for e in normalized if key in e.content]
            if not carriers:
                continue

            numeric = all(isinstance(v, (int, float)) and not isinstance(v, bool)
                          for _, v in carriers)
            if numeric:
                total_weight = sum(w for w, _ in carriers)
                if total_weight == 0.0:
                    merged[key] = carriers[-1][1]
                else:
                    merged[key] = sum(w * v for w, v in carriers) / total_weight
            else:
                best = max(carriers, key=lambda wv: wv[0])
                merged[key] = best[1]
        best_entry = max(normalized, key=lambda e: (e.trust_weight, e.timestamp))
        return self._build_result(
            entries,
            MergeStrategy.TRUST_WEIGHTED,
            output_entries=[best_entry],
            merged_content=merged,
        )

    def merge_consensus(
        self,
        entries: list[KnowledgeEntry | dict],
        threshold: float = 0.5,
    ) -> MergeResult:
        """Retain only fields where a strict majority of entries agree.

        For each key, collects all values from entries that carry it, then
        checks whether any single value appears in more than half the
        entries.  Keys that reach majority are included; others are dropped.

        Args:
            entries (list[KnowledgeEntry]): Source entries to merge.

        Returns:
            dict: Merged content containing only majority-agreed fields.
        """
        normalized = self._normalize_entries(entries)
        if not normalized:
            return self._build_result([], MergeStrategy.CONSENSUS, output_entries=[], merged_content={})
        all_keys: set[str] = set()
        for entry in normalized:
            all_keys.update(entry.content.keys())

        n = len(normalized)
        required = max(0.0, min(1.0, float(threshold)))
        merged: dict = {}

        for key in all_keys:
            values = [e.content[key] for e in normalized if key in e.content]
            counter: dict = collections.Counter(
                (v if not isinstance(v, list) else tuple(v)) for v in values
            )
            best_val, best_count = counter.most_common(1)[0]
            if (best_count / n) >= required:
                # Convert tuples back to lists for JSON-compatibility
                merged[key] = list(best_val) if isinstance(best_val, tuple) else best_val

        output_entries = []
        for entry in normalized:
            if all(entry.content.get(key) == value for key, value in merged.items()):
                output_entries.append(entry)
        return self._build_result(
            entries,
            MergeStrategy.CONSENSUS,
            output_entries=output_entries,
            merged_content=merged,
        )

    def set_strategy(self, strategy: MergeStrategy | str) -> None:
        """Change the active merge strategy for future merge operations.

        Previously recorded history is retained regardless of strategy
        changes.  The new strategy takes effect for the next call to
        ``merge_entries()``.

        Args:
            strategy (MergeStrategy): The new strategy to apply.

        Returns:
            None
        """
        self._strategy = _coerce_merge_strategy(strategy)
        _log.debug("KnowledgeMerger strategy set to %r", self._strategy.value)

    def get_history(self) -> list[MergeResult]:
        """Return a copy of the list of all past MergeResult objects.

        Each element is an immutable MergeResult dataclass.  The list is
        in the order that merges were performed.

        Returns:
            list[MergeResult]: Shallow-copied merge history list.
        """
        return list(self._history)

    def summary(self) -> str:
        """Return a one-line summary of the merger's current state.

        Includes the active strategy name and total number of merges
        recorded in the history.

        Returns:
            str: Summary string, e.g. ``"KnowledgeMerger: UNION, 4 merges performed"``.
        """
        strat = self._strategy.value.upper()
        n = len(self._history)
        return f"KnowledgeMerger: {strat}, {n} merges performed"


# ---------------------------------------------------------------------------
# KnowledgeRepository
# ---------------------------------------------------------------------------


class KnowledgeRepository:
    """In-memory store for KnowledgeEntry objects with inverted index support.

    Provides fast retrieval of entries by entry_id, by originating node, or
    by knowledge concept identifier.  An inverted index maps knowledge_id to
    the list of entry_ids that describe that concept, enabling efficient
    conflict detection without full table scans.

    The repository does not enforce uniqueness on knowledge_id — multiple
    entries for the same concept can coexist, which is the normal pre-merge
    state.  The caller is responsible for invoking a KnowledgeMerger when
    conflicts must be resolved.

    Key design decisions:

    - Entries are stored in a flat dict keyed by ``entry_id`` for O(1)
      point retrieval.
    - The ``_index`` dict maps ``knowledge_id`` → ``list[entry_id]`` for
      O(1) concept-based lookup.
    - ``rebuild_index()`` can reconstruct ``_index`` from ``_entries`` at any
      time, which is useful after bulk imports or deserialisations.
    - Deletion removes the entry from both structures atomically.
    - ``to_dict()`` exports the full repository as a JSON-serialisable dict.

    Thread-safety: this implementation is **not** thread-safe.  Callers
    that require concurrent access should add their own locking.
    """

    def __init__(self) -> None:
        """Initialise an empty repository with empty store and index.

        Creates ``_entries`` (a flat dict keyed by entry_id) and ``_index``
        (an inverted index mapping knowledge_id to a list of entry_ids).
        """
        self._entries: dict[str, dict] = {}
        self._index: dict[str, list[str]] = {}

    def store(self, entry: KnowledgeEntry | dict) -> str:
        """Store a KnowledgeEntry and update the inverted index.

        If an entry with the same entry_id already exists it will be
        silently overwritten.  The inverted index is updated atomically
        so that ``search()`` returns consistent results immediately.

        Args:
            entry (KnowledgeEntry): The entry to persist.

        Returns:
            None
        """
        stored = _entry_to_dict(entry)
        entry_id = stored["entry_id"]
        knowledge_id = stored["knowledge_id"]
        self._entries[entry_id] = stored  # type: ignore[assignment]
        bucket = self._index.setdefault(knowledge_id, [])
        if entry_id not in bucket:
            bucket.append(entry_id)
        _log.debug("Stored entry %r for knowledge_id %r", entry_id, knowledge_id)
        return entry_id

    def retrieve(self, entry_id: str) -> Optional[dict]:
        """Retrieve a single KnowledgeEntry by its entry_id.

        Args:
            entry_id (str): The unique identifier of the entry to retrieve.

        Returns:
            Optional[KnowledgeEntry]: The matching entry, or ``None`` if
                no entry with that id exists in the repository.
        """
        result = self._entries.get(entry_id)
        if result is None:
            for entry in self._entries.values():
                if entry.get("discovery_id") == entry_id:
                    return dict(entry)
        return dict(result) if result is not None else None

    def retrieve_by_node(self, source_node: str) -> list[dict]:
        """Return all entries whose source_node matches the given identifier.

        Performs a linear scan over all stored entries.  This is acceptable
        for the expected repository sizes in JuGeo federation nodes (< 10 000
        entries); a secondary index could be added for larger deployments.

        Args:
            source_node (str): The node identifier to filter by.

        Returns:
            list[KnowledgeEntry]: Potentially-empty list of matching entries,
                sorted by ascending timestamp.
        """
        matches = [
            e for e in self._entries.values()
            if e.get("source_node") == source_node or e.get("node_id") == source_node
        ]
        return sorted((dict(match) for match in matches), key=lambda e: e.get("timestamp", 0.0))

    def search(self, knowledge_id: str) -> list[dict]:
        """Return all entries for a given knowledge concept identifier.

        Uses the inverted index for O(1) key lookup, then resolves the
        entry_ids to full entry objects.

        Args:
            knowledge_id (str): The knowledge concept identifier to search.

        Returns:
            list[KnowledgeEntry]: All entries whose knowledge_id matches,
                sorted by ascending timestamp.
        """
        if not knowledge_id:
            return [dict(entry) for entry in sorted(self._entries.values(), key=lambda e: e.get("timestamp", 0.0))]

        entry_ids = self._index.get(knowledge_id, [])
        entries = [self._entries[eid] for eid in entry_ids if eid in self._entries]
        if entries:
            return [dict(entry) for entry in sorted(entries, key=lambda e: e.get("timestamp", 0.0))]

        query = str(knowledge_id).lower()
        matches = []
        for entry in self._entries.values():
            tags = [str(tag).lower() for tag in entry.get("tags", [])]
            content = entry.get("content", {})
            haystack = " ".join(
                [
                    str(entry.get("knowledge_id", "")),
                    str(entry.get("entry_id", "")),
                    str(entry.get("source_node", "")),
                    str(entry.get("node_id", "")),
                    " ".join(tags),
                    str(content),
                ]
            ).lower()
            if query in haystack:
                matches.append(dict(entry))
        return sorted(matches, key=lambda e: e.get("timestamp", 0.0))

    def delete(self, entry_id: str) -> bool:
        """Remove an entry from the repository and update the index.

        Args:
            entry_id (str): The unique identifier of the entry to delete.

        Returns:
            bool: ``True`` if the entry was found and deleted, ``False``
                  if no entry with that id existed.
        """
        entry = self._entries.pop(entry_id, None)
        if entry is None:
            return False
        bucket = self._index.get(entry.get("knowledge_id", ""), [])
        if entry_id in bucket:
            bucket.remove(entry_id)
        if not bucket:
            self._index.pop(entry.get("knowledge_id", ""), None)
        _log.debug("Deleted entry %r", entry_id)
        return True

    def count(self) -> int:
        """Return the total number of entries currently stored.

        Returns:
            int: Number of stored KnowledgeEntry objects (>= 0).
        """
        return len(self._entries)

    def all_ids(self) -> list[str]:
        """Return a sorted list of all stored entry_ids.

        Sorting ensures deterministic output regardless of insertion order,
        which is useful for tests and checksumming.

        Returns:
            list[str]: Sorted list of all entry_id strings.
        """
        return sorted(self._entries.keys())

    def rebuild_index(self) -> None:
        """Reconstruct the inverted index from the current ``_entries`` dict.

        This method is idempotent and can be called at any time to repair
        an inconsistent index (e.g. after a bulk import bypasses ``store()``).
        It clears the existing index and rebuilds it by iterating over all
        stored entries.

        Returns:
            None
        """
        self._index.clear()
        for entry in self._entries.values():
            knowledge_id = entry.get("knowledge_id")
            entry_id = entry.get("entry_id")
            if not knowledge_id or not entry_id:
                continue
            bucket = self._index.setdefault(knowledge_id, [])
            if entry_id not in bucket:
                bucket.append(entry_id)
        _log.debug("Index rebuilt: %d knowledge_ids indexed", len(self._index))

    def to_dict(self) -> dict:
        """Export the entire repository as a JSON-serialisable dictionary.

        The outer dict has a single ``"entries"`` key whose value is a list
        of per-entry dicts produced by ``KnowledgeEntry.to_dict()``.

        Returns:
            dict: ``{"entries": [...]}`` serialisation of the repository.
        """
        return {"entries": [dict(e) for e in self._entries.values()]}

    def summary(self) -> str:
        """Return a one-line summary of the repository's current state.

        Includes the total entry count and the number of distinct
        knowledge_ids currently indexed.

        Returns:
            str: Summary string, e.g.
                 ``"KnowledgeRepository: 12 entries, 5 knowledge_ids"``.
        """
        n_entries = self.count()
        n_concepts = len(self._index)
        return (
            f"KnowledgeRepository: {n_entries} entries, "
            f"{n_concepts} knowledge_ids"
        )


# ---------------------------------------------------------------------------
# FederatedKnowledgeRunner
# ---------------------------------------------------------------------------


class FederatedKnowledgeRunner:
    """High-level orchestrator for the full federated-knowledge pipeline.

    Wires together a KnowledgePropagator, a KnowledgeMerger, and a
    KnowledgeRepository into a single object that can execute the complete
    pipeline from a raw discovery dictionary to a stored, merged result.

    The two primary entry points are:

    ``run(discovery_dict, node_ids)``
        Creates a KnowledgeEntry from a discovery payload, registers the
        given node_ids, propagates the entry to all of them, stores it in
        the repository, and returns a result dictionary.

    ``run_from_nodes(entries)``
        Accepts a pre-built list of KnowledgeEntry objects (e.g. collected
        from multiple nodes), merges them using the configured strategy,
        stores the best entry, and returns a result dictionary.

    The runner accumulates a list of result dicts from each ``run`` or
    ``run_from_nodes`` call which can be retrieved with ``get_results()``.
    Calling ``reset()`` clears the propagator log, merge history, repository,
    and results list so that the runner can be reused for a new pipeline run.

    Typical usage::

        runner = FederatedKnowledgeRunner(strategy=MergeStrategy.LATEST)
        runner.propagator.register_node("alpha")
        result = runner.run({"knowledge_id": "geo:7", "content": {...}}, ["alpha"])
    """

    def __init__(
        self,
        strategy: MergeStrategy | str = MergeStrategy.UNION,
        *,
        propagator: KnowledgePropagator | None = None,
        merger: KnowledgeMerger | None = None,
        repository: KnowledgeRepository | None = None,
    ) -> None:
        """Initialise the runner with fresh sub-components.

        Creates a new KnowledgePropagator, KnowledgeMerger (with the given
        strategy), and KnowledgeRepository.  Exposes the propagator as
        ``self.propagator`` so callers can register nodes directly.

        Args:
            strategy (MergeStrategy): Merge algorithm to use for conflict
                resolution throughout the lifetime of this runner.
        """
        normalized_strategy = _coerce_merge_strategy(strategy)
        self.propagator = propagator or KnowledgePropagator()
        self._merger = merger or KnowledgeMerger(strategy=normalized_strategy)
        self._repository = repository or KnowledgeRepository()
        self._results: list[dict] = []

    def run(
        self,
        discovery_dict: dict | list[KnowledgeEntry | dict],
        node_ids: list[str] | None = None,
        *,
        nodes: list[str] | None = None,
        strategy: MergeStrategy | str | None = None,
    ) -> dict:
        """Execute the full propagation pipeline for a single discovery.

        Registers any new node_ids, creates a KnowledgeEntry from the
        discovery payload, propagates it to all specified nodes, and stores
        it in the local repository.  Any existing entries for the same
        knowledge_id are merged before storage.

        Args:
            discovery_dict (dict): Must contain at least ``"knowledge_id"``
                and ``"content"`` keys; optionally ``"source_node"`` and
                ``"trust_weight"``.
            node_ids (list[str]): Node identifiers to register (if not already
                present) and propagate to.

        Returns:
            dict: Result dictionary with keys ``"entry_id"``,
                  ``"knowledge_id"``, ``"propagation"``, ``"status"``,
                  ``"merge_result"`` (if a merge was performed), and
                  ``"timestamp"``.
        """
        selected_nodes = list(nodes if nodes is not None else (node_ids or []))
        if strategy is not None:
            self._merger.set_strategy(strategy)

        for nid in selected_nodes:
            if nid not in self.propagator._nodes:
                self.propagator.register_node(nid)

        if isinstance(discovery_dict, list):
            entries = [_coerce_knowledge_entry(entry) for entry in discovery_dict]
            if not entries:
                result = {
                    "status": PropagationStatus.QUEUED,
                    "merge_result": None,
                    "propagation": [],
                    "timestamp": _utcnow(),
                }
                self._results.append(result)
                return result
            propagation = self.propagator.propagate_all(discovery_dict, nodes=selected_nodes)
            merge_result = self._merger.merge(entries, strategy or self._merger._strategy)
            for output_entry in merge_result.output_entries:
                self._repository.store(output_entry)
            result = {
                "status": PropagationStatus.COMPLETE if propagation else PropagationStatus.QUEUED,
                "merge_result": merge_result.to_dict(),
                "propagation": propagation,
                "timestamp": _utcnow(),
            }
            self._results.append(result)
            return result

        knowledge_id = discovery_dict.get("knowledge_id", _uid())
        source_node = discovery_dict.get("source_node", "runner-default")
        content = discovery_dict.get("content", {})
        trust_weight = float(
            discovery_dict.get(
                "trust_weight",
                discovery_dict.get("trust_score", 1.0),
            )
        )

        entry = KnowledgeEntry.create(
            knowledge_id=knowledge_id,
            source_node=source_node,
            content=content,
            trust_weight=trust_weight,
        )

        propagation = self.propagator.propagate_all(entry)
        status = self.propagator.get_propagation_status(knowledge_id)

        existing = self._repository.search(knowledge_id)
        merge_result_dict: Optional[dict] = None
        if existing:
            merge_result = self._merger.merge(existing + [entry], strategy or self._merger._strategy)
            merge_result_dict = merge_result.to_dict()
            # Store a synthetic entry representing the merged state
            merged_entry = KnowledgeEntry.create(
                knowledge_id=knowledge_id,
                source_node="merger",
                content=merge_result.merged_content,
                trust_weight=_clamp(
                    sum(_coerce_knowledge_entry(e).trust_weight for e in existing) / max(len(existing), 1),
                    0.0, 1.0,
                ),
            )
            self._repository.store(merged_entry)
        else:
            self._repository.store(entry)

        result: dict = {
            "entry_id": entry.entry_id,
            "knowledge_id": knowledge_id,
            "propagation": propagation,
            "status": status,
            "merge_result": merge_result_dict,
            "timestamp": _utcnow(),
        }
        self._results.append(result)
        return result

    def run_from_nodes(
        self,
        entries: list[KnowledgeEntry | dict] | None = None,
        *,
        node_ids: list[str] | None = None,
        strategy: MergeStrategy | str | None = None,
    ) -> dict:
        """Merge a pre-collected list of KnowledgeEntry objects and store the result.

        Intended for use when entries have already been gathered from remote
        nodes and only the merge-and-store step is needed.  All entries are
        merged using the configured strategy; the MergeResult is stored as a
        new synthetic entry in the repository.

        Args:
            entries (list[KnowledgeEntry]): Two or more entries to reconcile.
                Typically all share the same knowledge_id, though this is not
                enforced.

        Returns:
            dict: Result dictionary with ``"merge_result"``, ``"repository_count"``,
                  and ``"timestamp"`` keys.
        """
        if strategy is not None:
            self._merger.set_strategy(strategy)

        selected_entries = list(entries or [])
        if entries is None and node_ids is not None:
            selected_entries = []
            for node_id in node_ids:
                selected_entries.extend(self._repository.retrieve_by_node(node_id))

        if not selected_entries:
            result: dict = {
                "merge_result": None,
                "repository_count": self._repository.count(),
                "timestamp": _utcnow(),
            }
            self._results.append(result)
            return result

        normalized_entries = [_coerce_knowledge_entry(entry) for entry in selected_entries]
        merge_result = self._merger.merge(normalized_entries, strategy or self._merger._strategy)

        avg_trust = sum(e.trust_weight for e in normalized_entries) / len(normalized_entries)
        merged_entry = KnowledgeEntry.create(
            knowledge_id=normalized_entries[0].knowledge_id,
            source_node="runner-merge",
            content=merge_result.merged_content,
            trust_weight=_clamp(avg_trust, 0.0, 1.0),
        )
        self._repository.store(merged_entry)

        result = {
            "merge_result": merge_result.to_dict(),
            "repository_count": self._repository.count(),
            "timestamp": _utcnow(),
        }
        self._results.append(result)
        return result

    def get_results(self) -> list[dict]:
        """Return a copy of all result dictionaries accumulated so far.

        Each element is the dict returned by a prior ``run()`` or
        ``run_from_nodes()`` call.  Useful for post-run auditing.

        Returns:
            list[dict]: Shallow-copied list of accumulated result dicts.
        """
        return list(self._results)

    def reset(self) -> None:
        """Clear all state and return the runner to its initial condition.

        Resets the propagation log, merge history, repository, and results
        list.  Registered nodes are also cleared.  After calling ``reset()``,
        the runner behaves as if it were freshly constructed.

        Returns:
            None
        """
        self.propagator.clear_log()
        self.propagator._nodes.clear()
        self._merger._history.clear()
        self._repository._entries.clear()
        self._repository._index.clear()
        self._results.clear()
        _log.debug("FederatedKnowledgeRunner reset")

    def summary(self) -> str:
        """Return a multi-line summary of the runner's sub-component states.

        Concatenates the summary strings from the propagator, merger, and
        repository for a complete picture of the runner's current state.

        Returns:
            str: Multi-line summary string.
        """
        lines = [
            "FederatedKnowledgeRunner",
            f"  {self.propagator.summary()}",
            f"  {self._merger.summary()}",
            f"  {self._repository.summary()}",
            f"  Results recorded: {len(self._results)}",
        ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Free functions
# ---------------------------------------------------------------------------


def propagate_knowledge(
    entry: KnowledgeEntry | dict | list[KnowledgeEntry | dict],
    node_ids: list[str] | None = None,
    target_nodes: list[str] | None = None,
    *,
    nodes: list[str] | None = None,
) -> dict | list[dict]:
    """Propagate a KnowledgeEntry to a list of node identifiers.

    A convenience wrapper that instantiates a KnowledgePropagator,
    registers all provided node_ids, runs the propagation, and returns a
    result dictionary.  This function is stateless from the caller's
    perspective — no propagator or log state persists after the call.

    Args:
        entry (KnowledgeEntry): The knowledge entry to propagate.
        node_ids (list[str]): Identifiers of the target federation nodes.

    Returns:
        dict: A result dictionary with keys:
            ``"entry_id"`` — the entry's identifier,
            ``"knowledge_id"`` — the knowledge concept identifier,
            ``"propagation"`` — mapping of node_id → bool success,
            ``"status"`` — the PropagationStatus for this knowledge_id,
            ``"timestamp"`` — POSIX UTC float of the propagation time.
    """
    propagator = KnowledgePropagator()
    selected_nodes = node_ids or target_nodes or nodes or []
    for nid in selected_nodes:
        propagator.register_node(nid)
    if isinstance(entry, list):
        if not entry:
            return []
        return propagator.propagate_all(entry, nodes=selected_nodes)
    entry_obj = _coerce_knowledge_entry(entry)
    propagation = propagator.propagate_all(entry_obj)
    status = propagator.get_propagation_status(entry_obj.knowledge_id)
    return {
        "entry_id": entry_obj.entry_id,
        "knowledge_id": entry_obj.knowledge_id,
        "propagation": propagation,
        "status": status,
        "timestamp": _utcnow(),
    }


def merge_knowledge(
    entries: list[KnowledgeEntry | dict],
    strategy: MergeStrategy | str = MergeStrategy.UNION,
) -> MergeResult | dict:
    """Merge a list of KnowledgeEntry objects into a single MergeResult.

    A convenience wrapper that instantiates a KnowledgeMerger with the
    given strategy and calls ``merge_entries``.  This function is stateless
    from the caller's perspective — no merger state persists after the call.

    Args:
        entries (list[KnowledgeEntry]): Two or more entries to reconcile.
            All entries should share the same ``knowledge_id`` for the
            result to be semantically meaningful.
        strategy (MergeStrategy): The reconciliation algorithm to apply.
            Defaults to ``MergeStrategy.UNION``.

    Returns:
        MergeResult: The reconciled result with ``merged_content``,
                     ``confidence``, and ``strategy`` populated.

    Raises:
        ValueError: If ``entries`` is empty (propagated from
                    ``KnowledgeMerger.merge_entries``).
    """
    merger = KnowledgeMerger(strategy=_coerce_merge_strategy(strategy))
    result = merger.merge(entries, strategy)
    if any(isinstance(entry, dict) for entry in entries) or isinstance(strategy, str) or not entries:
        return result.to_dict()
    return result
