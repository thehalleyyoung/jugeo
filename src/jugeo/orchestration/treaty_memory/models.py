"""Treaty-memory models for the JuGeo theorem-proving orchestration system.

This module defines the persistent data structures that the treaty_memory
package uses to index, recall, and analyse past negotiation sessions, friction
patterns, archived treaty entries, and raw negotiation outcomes.

Theory reference
----------------
theory2.tex Ch48 – "Treaty memory, archival semantics, and negotiation recall"

Every class in this module is a first-class value in the treaty-memory algebra.
TreatyMemoryRecord is the primary index that maps pattern keys to session
histories.  FrictionPattern captures the structural signature of a conflict so
that future rounds can recognise it early.  TreatyArchiveEntry stores the full
evolution history of a stabilised treaty.  NegotiationResult records the outcome
of a single session including all agreed clauses and observed friction.
MemoryQuery provides a structured query interface over a collection of results.
MemoryStatistics aggregates analytics for diagnostics and reporting.
TreatyClause represents a single binding obligation within a treaty.

Design principles
-----------------
* Immutable value objects use ``@dataclass(frozen=True)`` so they can be
  safely shared across threads and cached without defensive copies.
* Mutable accumulators use ``@dataclass(slots=True)`` for memory efficiency
  while still allowing in-place updates as new evidence arrives.
* All upstream dependencies are guarded with ``try/except ImportError`` so
  the module loads and passes tests even in environments where optional
  packages are absent.
* Every identifier is a 16-character hex string produced by
  ``uuid.uuid4().hex[:16]`` to keep keys compact yet collision-resistant.
* All timestamps are POSIX floats from ``time.time()``.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# ---------------------------------------------------------------------------
# Guarded upstream imports
# ---------------------------------------------------------------------------

try:
    from jugeo.orchestration.negotiation import (
        NegotiationSession,
        NegotiationMemory,
        FrictionPattern as _NegFrictionPattern,
        TreatyProposal,
        SessionState,
    )
    _NEG_AVAILABLE = True
except ImportError:
    _NEG_AVAILABLE = False
    NegotiationSession = object  # type: ignore[assignment,misc]
    NegotiationMemory = object   # type: ignore[assignment,misc]
    TreatyProposal = object      # type: ignore[assignment,misc]

    class SessionState(Enum):    # type: ignore[no-redef]
        """Fallback stub for SessionState when negotiation module is absent."""
        OPEN = "open"
        AGREED = "agreed"
        DEADLOCKED = "deadlocked"
        ABANDONED = "abandoned"

try:
    from jugeo.orchestration.controller import OrchestratorState, SemanticMove, MoveKind
    _CTRL_AVAILABLE = True
except ImportError:
    _CTRL_AVAILABLE = False
    OrchestratorState = object  # type: ignore[assignment,misc]
    SemanticMove = object       # type: ignore[assignment,misc]

try:
    from jugeo.evidence.trust import TrustLevel, TrustAlgebra
    _TRUST_AVAILABLE = True
except ImportError:
    _TRUST_AVAILABLE = False

    class TrustLevel(Enum):     # type: ignore[no-redef]
        """Fallback stub for TrustLevel when evidence.trust is absent."""
        LOW = "low"
        MEDIUM = "medium"
        HIGH = "high"

    TrustAlgebra = object       # type: ignore[assignment,misc]

try:
    from jugeo.geometry.descent import DescentEngine, GluingData
    _DESCENT_AVAILABLE = True
except ImportError:
    _DESCENT_AVAILABLE = False
    DescentEngine = object  # type: ignore[assignment,misc]
    GluingData = object     # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class NegotiationOutcome(Enum):
    """Possible final outcomes of a negotiation session (Ch48 §2).

    The outcome determines how the session's evidence is weighted when
    updating the memory index and whether the resulting clauses are
    eligible for archival.
    """

    AGREED = "agreed"
    DEADLOCKED = "deadlocked"
    ESCALATED = "escalated"
    ABANDONED = "abandoned"
    PARTIALLY_AGREED = "partially_agreed"


class MemoryIndexKind(Enum):
    """Kind of index entry in TreatyMemoryRecord.

    Used to partition the memory record's internal dictionaries so that
    friction evidence and success evidence can be queried independently.
    """

    FRICTION = "friction"
    SUCCESS = "success"
    FAILURE = "failure"
    ESCALATION = "escalation"


class ArchivePolicy(Enum):
    """Policy for managing the treaty archive (Ch48 §5).

    The archive policy governs which entries are retained when the treaty
    store is compacted, allowing operators to trade storage against recall
    completeness.
    """

    KEEP_ALL = "keep_all"
    KEEP_RECENT = "keep_recent"
    KEEP_SUCCESSFUL = "keep_successful"
    COMPACT = "compact"


# ---------------------------------------------------------------------------
# TreatyClause
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TreatyClause:
    """A single binding obligation within a treaty (Ch48 §3).

    A TreatyClause is an immutable value object that records the text of an
    obligation, the parties bound by it, the guard condition under which the
    obligation activates, and the trigger that would invalidate it.  Clauses
    are added to a treaty during a negotiation session and may be retired
    when superseded by later agreements.

    Parameters
    ----------
    clause_id:
        A 16-character hex identifier unique to this clause.
    text:
        Human-readable statement of the obligation.
    binding_parties:
        List of party identifiers that are bound by this clause.
    guard_condition:
        A condition string (may be a logical formula) that must hold for
        the clause to be in effect.
    invalidation_trigger:
        A condition string whose truth invalidates this clause.
    confidence:
        A float in [0, 1] representing how certain the issuing agent is
        that this clause correctly captures the negotiated intent.
    added_at:
        POSIX timestamp at which this clause was added to the treaty.
    removed_at:
        POSIX timestamp at which this clause was removed, or ``None`` if
        it is still active.
    """

    clause_id: str
    text: str
    binding_parties: list[str]
    guard_condition: str
    invalidation_trigger: str
    confidence: float
    added_at: float
    removed_at: float | None = None

    def is_active(self) -> bool:
        """Return ``True`` iff this clause has not been removed.

        A clause whose ``removed_at`` field is ``None`` is considered to be
        currently in force; any non-``None`` value indicates retirement.

        Returns
        -------
        bool
            ``True`` if the clause is currently active.
        """
        return self.removed_at is None

    def matches_party(self, party: str) -> bool:
        """Return ``True`` iff *party* is bound by this clause.

        Parameters
        ----------
        party:
            The identifier of the party to test.

        Returns
        -------
        bool
            ``True`` if *party* appears in ``binding_parties``.
        """
        return party in self.binding_parties

    def age(self) -> float:
        """Return the age of this clause in seconds since it was added.

        Returns
        -------
        float
            ``time.time() - self.added_at``
        """
        return time.time() - self.added_at

    def to_dict(self) -> dict[str, Any]:
        """Serialise the clause to a plain dictionary.

        Returns
        -------
        dict
            A JSON-serialisable dictionary representation of all fields.
        """
        return {
            "clause_id": self.clause_id,
            "text": self.text,
            "binding_parties": list(self.binding_parties),
            "guard_condition": self.guard_condition,
            "invalidation_trigger": self.invalidation_trigger,
            "confidence": self.confidence,
            "added_at": self.added_at,
            "removed_at": self.removed_at,
            "is_active": self.is_active(),
            "age_seconds": self.age(),
        }


# ---------------------------------------------------------------------------
# FrictionPattern
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class FrictionPattern:
    """Structural signature of a recurring negotiation conflict (Ch48 §4).

    A FrictionPattern accumulates evidence across sessions about a specific
    type of conflict between two patches.  Because the pattern must be
    updated in-place as new sessions complete, it is mutable (``slots=True``
    rather than ``frozen=True``).

    The *fingerprint* method provides a deterministic hash that can be used
    as a dictionary key so that patterns can be looked up without relying on
    the ``pattern_id``.

    Parameters
    ----------
    pattern_id:
        Unique 16-character hex identifier.
    source_patch_id:
        The identifier of the patch that initiates the conflicting move.
    target_patch_id:
        The identifier of the patch that resists or rejects the move.
    conflict_kind:
        A short string label describing the category of conflict, e.g.
        ``"axiom_clash"`` or ``"scope_overlap"``.
    severity:
        A float in [0, 1] representing how disruptive this pattern is.
    occurrences:
        How many times this pattern has been observed.
    first_seen:
        POSIX timestamp of first observation.
    last_seen:
        POSIX timestamp of most recent observation.
    resolved_by:
        List of session IDs in which this pattern was resolved.
    """

    pattern_id: str
    source_patch_id: str
    target_patch_id: str
    conflict_kind: str
    severity: float
    occurrences: int
    first_seen: float
    last_seen: float
    resolved_by: list[str]

    def matches(self, session_dict: dict[str, Any]) -> bool:
        """Return ``True`` iff this pattern is recognisable in *session_dict*.

        A session is considered to match when its recorded patch identifiers
        or conflict kind overlap with this pattern's structural signature.
        The check is intentionally loose so that partial matches still
        trigger recall.

        Parameters
        ----------
        session_dict:
            A dictionary describing a negotiation session.  Relevant keys
            are ``"source_patch_id"``, ``"target_patch_id"``, and
            ``"conflict_kind"``.

        Returns
        -------
        bool
            ``True`` if the session contains evidence of this pattern.
        """
        src_match = session_dict.get("source_patch_id") == self.source_patch_id
        tgt_match = session_dict.get("target_patch_id") == self.target_patch_id
        kind_match = session_dict.get("conflict_kind") == self.conflict_kind
        # Direct identity match on all three fields.
        if src_match and tgt_match and kind_match:
            return True
        # Partial match: either the patch pair or the conflict kind is shared.
        if (src_match or tgt_match) and kind_match:
            return True
        # Looser match: both patches appear anywhere in the session values.
        values = set(str(v) for v in session_dict.values())
        patch_hit = self.source_patch_id in values or self.target_patch_id in values
        return patch_hit and kind_match

    def fingerprint(self) -> str:
        """Return a deterministic 16-character hex fingerprint for this pattern.

        The fingerprint is computed as the first 16 hex digits of the
        SHA-256 hash of the concatenation
        ``source_patch_id + ":" + target_patch_id + ":" + conflict_kind``.
        This allows patterns to be indexed by content rather than by the
        opaque ``pattern_id``.

        Returns
        -------
        str
            A 16-character lowercase hex string.
        """
        raw = f"{self.source_patch_id}:{self.target_patch_id}:{self.conflict_kind}"
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        return digest[:16]

    def merge(self, other: FrictionPattern) -> FrictionPattern:
        """Return a new FrictionPattern that combines *self* and *other*.

        The merged pattern takes the minimum ``first_seen``, the maximum
        ``last_seen``, the sum of ``occurrences``, and the maximum
        ``severity``.  The ``resolved_by`` list is the union of both lists
        deduplicated while preserving order.

        Parameters
        ----------
        other:
            Another FrictionPattern with compatible source/target/kind.

        Returns
        -------
        FrictionPattern
            A new pattern object reflecting the merged evidence.
        """
        merged_resolved: list[str] = list(self.resolved_by)
        for sid in other.resolved_by:
            if sid not in merged_resolved:
                merged_resolved.append(sid)
        return FrictionPattern(
            pattern_id=uuid.uuid4().hex[:16],
            source_patch_id=self.source_patch_id,
            target_patch_id=self.target_patch_id,
            conflict_kind=self.conflict_kind,
            severity=max(self.severity, other.severity),
            occurrences=self.occurrences + other.occurrences,
            first_seen=min(self.first_seen, other.first_seen),
            last_seen=max(self.last_seen, other.last_seen),
            resolved_by=merged_resolved,
        )

    def age(self) -> float:
        """Return seconds since this pattern was first observed.

        Returns
        -------
        float
            ``time.time() - self.first_seen``
        """
        return time.time() - self.first_seen

    def is_recent(self, max_age: float = 86400.0) -> bool:
        """Return ``True`` iff the pattern was last seen within *max_age* seconds.

        Parameters
        ----------
        max_age:
            Maximum age in seconds.  Defaults to 24 hours (86400 s).

        Returns
        -------
        bool
            ``True`` if ``time.time() - self.last_seen < max_age``.
        """
        return time.time() - self.last_seen < max_age

    def update_seen(self) -> None:
        """Update ``last_seen`` to the current POSIX timestamp.

        This method mutates the object in place and is intended to be
        called each time a session confirms that this pattern is still
        active.
        """
        self.last_seen = time.time()

    def increment(self) -> None:
        """Increment ``occurrences`` by one and refresh ``last_seen``.

        Convenience method that combines ``update_seen`` with an occurrence
        count increment so callers do not have to do both separately.
        """
        self.occurrences += 1
        self.last_seen = time.time()

    def to_dict(self) -> dict[str, Any]:
        """Serialise the pattern to a plain dictionary.

        Returns
        -------
        dict
            A JSON-serialisable dictionary of all fields plus derived values
            ``fingerprint`` and ``age_seconds``.
        """
        return {
            "pattern_id": self.pattern_id,
            "source_patch_id": self.source_patch_id,
            "target_patch_id": self.target_patch_id,
            "conflict_kind": self.conflict_kind,
            "severity": self.severity,
            "occurrences": self.occurrences,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "resolved_by": list(self.resolved_by),
            "fingerprint": self.fingerprint(),
            "age_seconds": self.age(),
        }


# ---------------------------------------------------------------------------
# TreatyMemoryRecord
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class TreatyMemoryRecord:
    """Primary index mapping pattern keys to session histories (Ch48 §6).

    The TreatyMemoryRecord is the heart of the treaty-memory system.  It
    maintains an ordered list of raw session records alongside two secondary
    indices: ``friction_index`` maps pattern keys to lists of session
    dictionaries where that pattern appeared, and ``success_index`` maps
    pattern keys to empirical success rates.

    Because records accumulate continuously, the class is mutable
    (``slots=True``).

    Parameters
    ----------
    memory_id:
        Unique 16-character hex identifier for this memory record.
    session_id:
        The ID of the session context that owns this record (may be a
        top-level orchestrator session ID).
    session_records:
        Ordered list of raw session dictionaries added via
        :meth:`record_session`.
    friction_index:
        Mapping from pattern keys to lists of session dicts where that
        pattern appeared.
    success_index:
        Mapping from pattern keys to empirical success rates in [0, 1].
    created_at:
        POSIX timestamp when this record was created.
    last_updated:
        POSIX timestamp of the most recent call to :meth:`record_session`.
    """

    memory_id: str
    session_id: str
    session_records: list[dict[str, Any]]
    friction_index: dict[str, list[dict[str, Any]]]
    success_index: dict[str, float]
    created_at: float
    last_updated: float

    def record_session(self, session_dict: dict[str, Any]) -> None:
        """Append *session_dict* to the record and update both indices.

        The method extracts friction keys from the session's
        ``"friction_patterns"`` list (if present) and updates the friction
        index.  It also updates the success index for each friction key
        based on whether the session's ``"outcome"`` indicates success.

        Parameters
        ----------
        session_dict:
            A dictionary describing a completed negotiation session.
        """
        self.session_records.append(session_dict)
        self.last_updated = time.time()

        successful_outcomes = {
            NegotiationOutcome.AGREED.value,
            NegotiationOutcome.PARTIALLY_AGREED.value,
        }
        outcome_val = session_dict.get("outcome", "")
        is_success = outcome_val in successful_outcomes

        # Extract friction patterns and update friction index.
        friction_list = session_dict.get("friction_patterns", [])
        for fp in friction_list:
            if isinstance(fp, dict):
                key = fp.get("fingerprint") or fp.get("conflict_kind", "unknown")
            else:
                key = str(fp)
            if key not in self.friction_index:
                self.friction_index[key] = []
            self.friction_index[key].append(session_dict)

            # Update running success rate for this friction key using an
            # exponential moving average with alpha = 0.2 so older evidence
            # decays gracefully as new sessions arrive.
            alpha = 0.2
            current_rate = self.success_index.get(key, 0.0)
            new_value = 1.0 if is_success else 0.0
            self.success_index[key] = (1.0 - alpha) * current_rate + alpha * new_value

    def lookup_friction(self, pattern_key: str) -> list[dict[str, Any]]:
        """Return all session records associated with *pattern_key*.

        Parameters
        ----------
        pattern_key:
            A friction fingerprint or conflict-kind string used as the
            index key.

        Returns
        -------
        list[dict]
            The list of session dictionaries stored under *pattern_key*, or
            an empty list if the key is not in the index.
        """
        return self.friction_index.get(pattern_key, [])

    def similar_patterns(
        self, pattern_key: str, threshold: float = 0.5
    ) -> list[dict[str, Any]]:
        """Return session records for keys similar to *pattern_key*.

        Similarity is measured with a Jaccard-like coefficient on the
        whitespace-tokenised token sets of the two keys.  Keys whose
        similarity exceeds *threshold* are included in the result.

        Parameters
        ----------
        pattern_key:
            The query key.
        threshold:
            Minimum Jaccard similarity in [0, 1].  Defaults to 0.5.

        Returns
        -------
        list[dict]
            Concatenated list of session dicts from all matching keys,
            deduplicated by session_id where that field is present.
        """
        query_tokens = set(pattern_key.split())
        seen_ids: set[str] = set()
        results: list[dict[str, Any]] = []

        for key, records in self.friction_index.items():
            key_tokens = set(key.split())
            union = query_tokens | key_tokens
            if not union:
                continue
            intersection = query_tokens & key_tokens
            jaccard = len(intersection) / len(union)
            if jaccard >= threshold:
                for rec in records:
                    sid = rec.get("session_id", id(rec))
                    if sid not in seen_ids:
                        seen_ids.add(str(sid))
                        results.append(rec)
        return results

    def success_rate(self, pattern_key: str) -> float:
        """Return the empirical success rate for *pattern_key*.

        Parameters
        ----------
        pattern_key:
            The friction index key to query.

        Returns
        -------
        float
            A float in [0, 1] representing the estimated probability of
            success given this pattern, or 0.0 if the key is unknown.
        """
        return self.success_index.get(pattern_key, 0.0)

    def purge_old(self, max_age: float) -> None:
        """Remove session records older than *max_age* seconds.

        Records that lack a ``"timestamp"`` field are treated as age zero
        (i.e. never purged) to avoid inadvertently discarding important
        undated entries.

        Parameters
        ----------
        max_age:
            Maximum age in seconds.  Records older than this threshold
            are removed from ``session_records``.
        """
        cutoff = time.time() - max_age
        self.session_records = [
            rec for rec in self.session_records
            if rec.get("timestamp", time.time()) >= cutoff
        ]
        self.last_updated = time.time()

    def export(self) -> dict[str, Any]:
        """Serialise this record to a plain dictionary.

        Returns
        -------
        dict
            A JSON-serialisable representation of all fields.
        """
        return {
            "memory_id": self.memory_id,
            "session_id": self.session_id,
            "session_records": list(self.session_records),
            "friction_index": {k: list(v) for k, v in self.friction_index.items()},
            "success_index": dict(self.success_index),
            "created_at": self.created_at,
            "last_updated": self.last_updated,
        }

    def import_from(self, data: dict[str, Any]) -> None:
        """Populate this record's fields from a previously exported dictionary.

        Parameters
        ----------
        data:
            A dictionary previously produced by :meth:`export`.
        """
        self.memory_id = data.get("memory_id", self.memory_id)
        self.session_id = data.get("session_id", self.session_id)
        self.session_records = list(data.get("session_records", []))
        raw_fi = data.get("friction_index", {})
        self.friction_index = {k: list(v) for k, v in raw_fi.items()}
        self.success_index = dict(data.get("success_index", {}))
        self.created_at = data.get("created_at", self.created_at)
        self.last_updated = data.get("last_updated", self.last_updated)


# ---------------------------------------------------------------------------
# TreatyArchiveEntry
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class TreatyArchiveEntry:
    """Full evolution history of a stabilised treaty (Ch48 §7).

    A TreatyArchiveEntry tracks every version of a treaty from its initial
    ratification through subsequent amendments.  Each entry is linked to its
    predecessor (if any) and may point to a successor when it is retired.
    The ``history`` list records a chronological audit trail of all events.

    Parameters
    ----------
    entry_id:
        Unique 16-character hex identifier for this specific version entry.
    treaty_id:
        Identifier that is shared across all versions of the same treaty.
    law_clauses:
        List of clause text strings that constitute this version of the
        treaty.
    effective_from:
        POSIX timestamp at which this version became effective.
    retired_at:
        POSIX timestamp at which this version was retired, or ``None`` if
        it is the current active version.
    reason_retired:
        Human-readable explanation of why this version was retired.
    successor_id:
        The ``entry_id`` of the version that superseded this one.
    version:
        Monotonically increasing integer version number starting at 1.
    provenance:
        Arbitrary key-value metadata describing how this version was
        produced (e.g. session IDs, agent names).
    tags:
        List of string tags for filtering and categorisation.
    history:
        Ordered list of event dictionaries recording mutations.
    """

    entry_id: str
    treaty_id: str
    law_clauses: list[str]
    effective_from: float
    retired_at: float | None
    reason_retired: str | None
    successor_id: str | None
    version: int
    provenance: dict[str, Any]
    tags: list[str]
    history: list[dict[str, Any]]

    def is_active(self) -> bool:
        """Return ``True`` iff this entry has not been retired.

        Returns
        -------
        bool
            ``True`` when ``retired_at`` is ``None``.
        """
        return self.retired_at is None

    def retire(self, reason: str, successor_id: str | None = None) -> None:
        """Mark this entry as retired and record the event in history.

        Parameters
        ----------
        reason:
            Human-readable explanation for the retirement.
        successor_id:
            Optional identifier of the entry that supersedes this one.
        """
        self.retired_at = time.time()
        self.reason_retired = reason
        self.successor_id = successor_id
        self.history.append({
            "event": "retired",
            "timestamp": self.retired_at,
            "reason": reason,
            "successor_id": successor_id,
        })

    def evolve(self, new_clauses: list[str]) -> TreatyArchiveEntry:
        """Return a new TreatyArchiveEntry that supersedes this one.

        The new entry inherits the same ``treaty_id`` but receives a fresh
        ``entry_id``, an incremented ``version``, and the current timestamp
        as its ``effective_from``.  A corresponding event is appended to
        *this* entry's history so the audit trail is complete.

        Parameters
        ----------
        new_clauses:
            The list of clause strings for the new version.

        Returns
        -------
        TreatyArchiveEntry
            A new entry with ``version = self.version + 1``.
        """
        new_id = uuid.uuid4().hex[:16]
        now = time.time()
        # Record the evolution event in the current entry's history.
        self.history.append({
            "event": "evolved",
            "timestamp": now,
            "new_entry_id": new_id,
            "new_version": self.version + 1,
            "clause_count": len(new_clauses),
        })
        new_history: list[dict[str, Any]] = [
            {
                "event": "created_via_evolution",
                "timestamp": now,
                "predecessor_id": self.entry_id,
                "predecessor_version": self.version,
            }
        ]
        return TreatyArchiveEntry(
            entry_id=new_id,
            treaty_id=self.treaty_id,
            law_clauses=list(new_clauses),
            effective_from=now,
            retired_at=None,
            reason_retired=None,
            successor_id=None,
            version=self.version + 1,
            provenance=dict(self.provenance),
            tags=list(self.tags),
            history=new_history,
        )

    def clause_count(self) -> int:
        """Return the number of law clauses in this entry.

        Returns
        -------
        int
            ``len(self.law_clauses)``
        """
        return len(self.law_clauses)

    def age_seconds(self) -> float:
        """Return the age of this entry in seconds since it became effective.

        Returns
        -------
        float
            ``time.time() - self.effective_from``
        """
        return time.time() - self.effective_from

    def summary(self) -> str:
        """Return a one-line human-readable summary of this entry.

        The summary includes the treaty ID, version number, clause count,
        and active/retired status.

        Returns
        -------
        str
            A concise summary string.
        """
        status = "active" if self.is_active() else "retired"
        return (
            f"Treaty {self.treaty_id} v{self.version} "
            f"[{self.clause_count()} clause(s), {status}]"
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise this entry to a plain dictionary.

        Returns
        -------
        dict
            A JSON-serialisable representation of all fields plus derived
            values.
        """
        return {
            "entry_id": self.entry_id,
            "treaty_id": self.treaty_id,
            "law_clauses": list(self.law_clauses),
            "effective_from": self.effective_from,
            "retired_at": self.retired_at,
            "reason_retired": self.reason_retired,
            "successor_id": self.successor_id,
            "version": self.version,
            "provenance": dict(self.provenance),
            "tags": list(self.tags),
            "history": list(self.history),
            "is_active": self.is_active(),
            "clause_count": self.clause_count(),
            "age_seconds": self.age_seconds(),
            "summary": self.summary(),
        }


# ---------------------------------------------------------------------------
# NegotiationResult
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class NegotiationResult:
    """Complete record of a single negotiation session's outcome (Ch48 §8).

    A NegotiationResult is created once a session terminates and captures
    everything needed to reconstruct what happened: the parties involved,
    the clauses that were agreed, the friction patterns that arose, timing
    and cost information, and the final outcome classification.

    Parameters
    ----------
    result_id:
        Unique 16-character hex identifier.
    session_id:
        Identifier of the session that produced this result.
    outcome:
        The final :class:`NegotiationOutcome` value.
    agreed_clauses:
        List of clause text strings that were mutually accepted.
    friction_patterns:
        List of :class:`FrictionPattern` objects observed during this
        session.
    duration_seconds:
        Wall-clock duration of the session in seconds.
    cost:
        Computational or resource cost (unitless float).
    trust_gain:
        Net change in trust between parties over this session (may be
        negative).
    timestamp:
        POSIX timestamp when the session concluded.
    parties:
        List of party identifiers that participated.
    rounds_completed:
        Number of negotiation rounds that took place.
    notes:
        Free-text annotations added by the orchestrator.
    """

    result_id: str
    session_id: str
    outcome: NegotiationOutcome
    agreed_clauses: list[str]
    friction_patterns: list[FrictionPattern]
    duration_seconds: float
    cost: float
    trust_gain: float
    timestamp: float
    parties: list[str]
    rounds_completed: int
    notes: str

    def was_successful(self) -> bool:
        """Return ``True`` iff the negotiation reached agreement.

        Both ``AGREED`` and ``PARTIALLY_AGREED`` outcomes count as
        successful because they produce at least some binding clauses.

        Returns
        -------
        bool
            ``True`` if the outcome indicates at least partial agreement.
        """
        return self.outcome in (
            NegotiationOutcome.AGREED,
            NegotiationOutcome.PARTIALLY_AGREED,
        )

    def friction_count(self) -> int:
        """Return the number of friction patterns observed in this session.

        Returns
        -------
        int
            ``len(self.friction_patterns)``
        """
        return len(self.friction_patterns)

    def extract_lessons(self) -> list[str]:
        """Derive a list of lesson strings from this session's evidence.

        Lessons are short, actionable statements derived by combining the
        session's outcome with the observed friction patterns.  They are
        intended for consumption by future planning phases.

        Returns
        -------
        list[str]
            A list of natural-language lesson strings.
        """
        lessons: list[str] = []

        if self.was_successful():
            lessons.append(
                f"Session {self.session_id} achieved {self.outcome.value} "
                f"with {len(self.agreed_clauses)} agreed clause(s) in "
                f"{self.rounds_completed} round(s)."
            )
        else:
            lessons.append(
                f"Session {self.session_id} ended as {self.outcome.value}; "
                f"no durable agreement was reached."
            )

        for fp in self.friction_patterns:
            if fp.severity >= 0.7:
                lessons.append(
                    f"High-severity friction ({fp.severity:.2f}) of kind "
                    f"'{fp.conflict_kind}' between patches "
                    f"{fp.source_patch_id} and {fp.target_patch_id}. "
                    f"Consider pre-negotiating this boundary in future sessions."
                )
            elif fp.occurrences > 3:
                lessons.append(
                    f"Recurring friction '{fp.conflict_kind}' has appeared "
                    f"{fp.occurrences} time(s); pattern is likely structural."
                )

        if self.trust_gain < 0:
            lessons.append(
                f"Trust decreased by {abs(self.trust_gain):.3f} units; "
                f"investigate breakdown in party cooperation."
            )
        elif self.trust_gain > 0.5:
            lessons.append(
                f"Significant trust gain ({self.trust_gain:.3f}); relationship "
                f"strengthened during this session."
            )

        if self.duration_seconds > 3600:
            lessons.append(
                f"Session ran for {self.duration_seconds / 3600:.1f} hours; "
                f"consider decomposing into shorter negotiation units."
            )

        return lessons

    def summary(self) -> str:
        """Return a one-line summary of this result.

        Returns
        -------
        str
            A concise description of outcome, clause count, and duration.
        """
        return (
            f"Result {self.result_id}: {self.outcome.value}, "
            f"{len(self.agreed_clauses)} clause(s), "
            f"{self.rounds_completed} round(s), "
            f"{self.duration_seconds:.1f}s, "
            f"trust_gain={self.trust_gain:+.3f}"
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise this result to a plain dictionary.

        Returns
        -------
        dict
            A JSON-serialisable dictionary including serialised friction
            patterns and derived values.
        """
        return {
            "result_id": self.result_id,
            "session_id": self.session_id,
            "outcome": self.outcome.value,
            "agreed_clauses": list(self.agreed_clauses),
            "friction_patterns": [fp.to_dict() for fp in self.friction_patterns],
            "duration_seconds": self.duration_seconds,
            "cost": self.cost,
            "trust_gain": self.trust_gain,
            "timestamp": self.timestamp,
            "parties": list(self.parties),
            "rounds_completed": self.rounds_completed,
            "notes": self.notes,
            "was_successful": self.was_successful(),
            "friction_count": self.friction_count(),
            "lessons": self.extract_lessons(),
            "summary": self.summary(),
        }


# ---------------------------------------------------------------------------
# MemoryQuery
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MemoryQuery:
    """Structured query interface over a collection of NegotiationResults (Ch48 §9).

    MemoryQuery is an immutable value object that encapsulates filter
    criteria.  Because it is frozen, queries can be safely cached, hashed,
    and passed across threads.  The ``with_outcome`` and ``with_age_limit``
    methods return new instances rather than modifying the existing one.

    Parameters
    ----------
    query_id:
        Unique 16-character hex identifier.
    pattern_filter:
        Dictionary of key-value pairs that must be present in a result's
        serialisation for it to match.
    outcome_filter:
        If not ``None``, only results with this outcome value are returned.
    max_age_hours:
        If not ``None``, only results newer than this many hours are
        returned.
    min_success_rate:
        Reserved for future use with indexed queries; ignored in
        :meth:`matches`.
    limit:
        Maximum number of results to return from :meth:`execute`.
    tags_filter:
        List of tags; a result must satisfy all tags (currently unused
        at the result level but available for extension).
    """

    query_id: str
    pattern_filter: dict[str, Any]
    outcome_filter: str | None
    max_age_hours: float | None
    min_success_rate: float | None
    limit: int
    tags_filter: list[str]

    def matches(self, result: NegotiationResult) -> bool:
        """Return ``True`` iff *result* satisfies all filter criteria.

        Checks are applied in order from cheapest to most expensive:
        1. outcome_filter - exact string match on result.outcome.value.
        2. max_age_hours - result.timestamp must be within the window.
        3. pattern_filter - all key-value pairs must appear in the
           result's ``to_dict()`` serialisation.

        Parameters
        ----------
        result:
            The NegotiationResult to test.

        Returns
        -------
        bool
            ``True`` if *result* passes all active filters.
        """
        if self.outcome_filter is not None:
            if result.outcome.value != self.outcome_filter:
                return False

        if self.max_age_hours is not None:
            cutoff = time.time() - self.max_age_hours * 3600.0
            if result.timestamp < cutoff:
                return False

        if self.pattern_filter:
            result_dict = result.to_dict()
            for key, value in self.pattern_filter.items():
                if result_dict.get(key) != value:
                    return False

        return True

    def execute(self, results: list[NegotiationResult]) -> list[NegotiationResult]:
        """Apply this query to *results* and return matching entries.

        Iterates through *results* in order and collects those for which
        :meth:`matches` returns ``True``, stopping once ``limit`` items
        have been accumulated.

        Parameters
        ----------
        results:
            The full list of NegotiationResult objects to filter.

        Returns
        -------
        list[NegotiationResult]
            A (possibly empty) list of at most ``self.limit`` matching
            results.
        """
        matched: list[NegotiationResult] = []
        for result in results:
            if len(matched) >= self.limit:
                break
            if self.matches(result):
                matched.append(result)
        return matched

    def with_outcome(self, outcome: NegotiationOutcome) -> MemoryQuery:
        """Return a new MemoryQuery with ``outcome_filter`` set to *outcome*.

        Parameters
        ----------
        outcome:
            The desired outcome to filter on.

        Returns
        -------
        MemoryQuery
            A new query identical to this one except for ``outcome_filter``.
        """
        return MemoryQuery(
            query_id=uuid.uuid4().hex[:16],
            pattern_filter=self.pattern_filter,
            outcome_filter=outcome.value,
            max_age_hours=self.max_age_hours,
            min_success_rate=self.min_success_rate,
            limit=self.limit,
            tags_filter=self.tags_filter,
        )

    def with_age_limit(self, hours: float) -> MemoryQuery:
        """Return a new MemoryQuery with ``max_age_hours`` set to *hours*.

        Parameters
        ----------
        hours:
            Maximum age in hours for results to be included.

        Returns
        -------
        MemoryQuery
            A new query identical to this one except for ``max_age_hours``.
        """
        return MemoryQuery(
            query_id=uuid.uuid4().hex[:16],
            pattern_filter=self.pattern_filter,
            outcome_filter=self.outcome_filter,
            max_age_hours=hours,
            min_success_rate=self.min_success_rate,
            limit=self.limit,
            tags_filter=self.tags_filter,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise this query to a plain dictionary.

        Returns
        -------
        dict
            A JSON-serialisable representation of all fields.
        """
        return {
            "query_id": self.query_id,
            "pattern_filter": dict(self.pattern_filter),
            "outcome_filter": self.outcome_filter,
            "max_age_hours": self.max_age_hours,
            "min_success_rate": self.min_success_rate,
            "limit": self.limit,
            "tags_filter": list(self.tags_filter),
        }


# ---------------------------------------------------------------------------
# MemoryStatistics
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MemoryStatistics:
    """Aggregate analytics over a collection of NegotiationResults (Ch48 §10).

    MemoryStatistics is a snapshot value object computed at a specific
    moment in time.  It provides high-level diagnostics useful for
    monitoring treaty-memory health and for generating reports.

    Parameters
    ----------
    total_sessions:
        Total number of sessions included in the computation.
    success_rate:
        Fraction of sessions that were successful (in [0, 1]).
    avg_duration:
        Mean session duration in seconds.
    avg_cost:
        Mean session computational cost.
    avg_trust_gain:
        Mean trust gain per session (may be negative).
    top_friction_patterns:
        List of conflict_kind strings most frequently observed, ordered
        by descending frequency.
    common_outcomes:
        Mapping from outcome value strings to counts.
    timestamp:
        POSIX timestamp when these statistics were computed.
    """

    total_sessions: int
    success_rate: float
    avg_duration: float
    avg_cost: float
    avg_trust_gain: float
    top_friction_patterns: list[str]
    common_outcomes: dict[str, int]
    timestamp: float

    def to_dict(self) -> dict[str, Any]:
        """Serialise these statistics to a plain dictionary.

        Returns
        -------
        dict
            A JSON-serialisable representation of all fields.
        """
        return {
            "total_sessions": self.total_sessions,
            "success_rate": self.success_rate,
            "avg_duration": self.avg_duration,
            "avg_cost": self.avg_cost,
            "avg_trust_gain": self.avg_trust_gain,
            "top_friction_patterns": list(self.top_friction_patterns),
            "common_outcomes": dict(self.common_outcomes),
            "timestamp": self.timestamp,
        }

    def summary(self) -> str:
        """Return a multi-line human-readable statistics summary.

        Returns
        -------
        str
            A formatted string suitable for logging or display.
        """
        lines = [
            "=== Treaty Memory Statistics ===",
            f"  Total sessions    : {self.total_sessions}",
            f"  Success rate      : {self.success_rate * 100:.1f}%",
            f"  Avg duration      : {self.avg_duration:.1f}s",
            f"  Avg cost          : {self.avg_cost:.4f}",
            f"  Avg trust gain    : {self.avg_trust_gain:+.4f}",
            "  Outcome breakdown :",
        ]
        for outcome_val, count in sorted(
            self.common_outcomes.items(), key=lambda kv: -kv[1]
        ):
            lines.append(f"    {outcome_val:<20} {count}")
        if self.top_friction_patterns:
            lines.append("  Top friction kinds:")
            for kind in self.top_friction_patterns[:5]:
                lines.append(f"    - {kind}")
        lines.append(f"  Computed at       : {self.timestamp:.3f}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Factory functions
# ---------------------------------------------------------------------------


def make_friction_pattern(
    source: str,
    target: str,
    kind: str,
    severity: float,
) -> FrictionPattern:
    """Create a fresh :class:`FrictionPattern` with sensible defaults.

    Parameters
    ----------
    source:
        Identifier of the source patch.
    target:
        Identifier of the target patch.
    kind:
        Short label describing the conflict category.
    severity:
        Float in [0, 1] representing conflict severity.

    Returns
    -------
    FrictionPattern
        A new pattern with ``occurrences = 1`` and ``first_seen`` /
        ``last_seen`` both set to the current timestamp.
    """
    now = time.time()
    return FrictionPattern(
        pattern_id=uuid.uuid4().hex[:16],
        source_patch_id=source,
        target_patch_id=target,
        conflict_kind=kind,
        severity=float(max(0.0, min(1.0, severity))),
        occurrences=1,
        first_seen=now,
        last_seen=now,
        resolved_by=[],
    )


def make_archive_entry(
    treaty_id: str,
    clauses: list[str],
) -> TreatyArchiveEntry:
    """Create a fresh version-1 :class:`TreatyArchiveEntry`.

    Parameters
    ----------
    treaty_id:
        Shared identifier for the treaty lineage.
    clauses:
        Initial list of law-clause strings.

    Returns
    -------
    TreatyArchiveEntry
        A new entry at version 1 with an empty history except for a
        creation event.
    """
    now = time.time()
    entry_id = uuid.uuid4().hex[:16]
    history: list[dict[str, Any]] = [
        {"event": "created", "timestamp": now, "version": 1}
    ]
    return TreatyArchiveEntry(
        entry_id=entry_id,
        treaty_id=treaty_id,
        law_clauses=list(clauses),
        effective_from=now,
        retired_at=None,
        reason_retired=None,
        successor_id=None,
        version=1,
        provenance={},
        tags=[],
        history=history,
    )


def make_negotiation_result(
    session_id: str,
    outcome: NegotiationOutcome | str,
    clauses: list[str],
) -> NegotiationResult:
    """Create a :class:`NegotiationResult` with default numeric values.

    Parameters
    ----------
    session_id:
        Identifier of the originating session.
    outcome:
        Either a :class:`NegotiationOutcome` enum member or its string
        value.
    clauses:
        List of agreed clause text strings.

    Returns
    -------
    NegotiationResult
        A new result with zero friction patterns, zero cost, zero trust
        gain, and zero duration.  Callers should update these fields
        before storing the result.
    """
    if isinstance(outcome, str):
        outcome = NegotiationOutcome(outcome)
    return NegotiationResult(
        result_id=uuid.uuid4().hex[:16],
        session_id=session_id,
        outcome=outcome,
        agreed_clauses=list(clauses),
        friction_patterns=[],
        duration_seconds=0.0,
        cost=0.0,
        trust_gain=0.0,
        timestamp=time.time(),
        parties=[],
        rounds_completed=0,
        notes="",
    )


def make_memory_query(**kwargs: Any) -> MemoryQuery:
    """Create a :class:`MemoryQuery` from keyword arguments.

    All fields have defaults, so callers can specify only what they need.

    Parameters
    ----------
    **kwargs:
        Any subset of MemoryQuery field names.  Unknown keys are silently
        ignored.

    Returns
    -------
    MemoryQuery
        A new query with the provided values and sensible defaults for
        the rest.
    """
    return MemoryQuery(
        query_id=kwargs.get("query_id", uuid.uuid4().hex[:16]),
        pattern_filter=kwargs.get("pattern_filter", {}),
        outcome_filter=kwargs.get("outcome_filter", None),
        max_age_hours=kwargs.get("max_age_hours", None),
        min_success_rate=kwargs.get("min_success_rate", None),
        limit=int(kwargs.get("limit", 100)),
        tags_filter=list(kwargs.get("tags_filter", [])),
    )


# ---------------------------------------------------------------------------
# Module-level analytics
# ---------------------------------------------------------------------------


def compute_memory_statistics(results: list[NegotiationResult]) -> MemoryStatistics:
    """Compute aggregate statistics over a list of NegotiationResult objects.

    The function is designed to be called after each batch of sessions
    completes.  It returns an immutable :class:`MemoryStatistics` snapshot
    that can be logged or displayed without risk of mutation.

    Parameters
    ----------
    results:
        The list of :class:`NegotiationResult` objects to analyse.  An
        empty list returns a zero-filled statistics object.

    Returns
    -------
    MemoryStatistics
        Aggregate statistics computed over *results*.
    """
    total = len(results)
    if total == 0:
        return MemoryStatistics(
            total_sessions=0,
            success_rate=0.0,
            avg_duration=0.0,
            avg_cost=0.0,
            avg_trust_gain=0.0,
            top_friction_patterns=[],
            common_outcomes={},
            timestamp=time.time(),
        )

    successes = sum(1 for r in results if r.was_successful())
    success_rate = successes / total

    total_duration = sum(r.duration_seconds for r in results)
    avg_duration = total_duration / total

    total_cost = sum(r.cost for r in results)
    avg_cost = total_cost / total

    total_trust = sum(r.trust_gain for r in results)
    avg_trust_gain = total_trust / total

    # Count outcomes.
    outcome_counter: Counter[str] = Counter()
    for r in results:
        outcome_counter[r.outcome.value] += 1
    common_outcomes = dict(outcome_counter)

    # Collect friction pattern kinds and rank by frequency.
    friction_counter: Counter[str] = Counter()
    for r in results:
        for fp in r.friction_patterns:
            friction_counter[fp.conflict_kind] += fp.occurrences
    top_friction = [kind for kind, _ in friction_counter.most_common(10)]

    return MemoryStatistics(
        total_sessions=total,
        success_rate=success_rate,
        avg_duration=avg_duration,
        avg_cost=avg_cost,
        avg_trust_gain=avg_trust_gain,
        top_friction_patterns=top_friction,
        common_outcomes=common_outcomes,
        timestamp=time.time(),
    )
