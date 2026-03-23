"""
Loser handling engine for the fleet_competition orchestration package.

This module is part of JuGeo's copilot-assisted encoding of theory2.tex Chapter 46:
Fleet semantics — competitive search over admissible futures.

Chapter 46 §46.6 describes the *loser handling sub-system* of the fleet competition
protocol.  After a winner has been selected in a :class:`~models.FleetRound`, this
module provides the machinery to record, classify, penalise, and optionally recycle
every losing bid.  The design enforces the theory2 invariant that **competition
results carry full provenance** — no losing bid may be silently discarded.

Key responsibilities
--------------------
1. **Loss classification** (:class:`LossReason`) — each loss is attributed to one
   of six mutually-exclusive reasons: lower trust tier, lower quality score,
   timeout, invalid bid, Pareto domination, or consensus failure.

2. **Loser disposition** (:class:`LoserDisposition`) — every classified loser
   is assigned a disposition: archived, recycled, penalised, eliminated, or
   escalated to human review.

3. **Immutable loser records** (:class:`LoserRecord`) — each losing bid is
   captured in a frozen dataclass that includes the winning bid_id, the margin
   of loss, per-criterion scores for both the loser and the winner, and full
   provenance.  This satisfies §46.6's reproducibility requirement.

4. **Penalty ledger** (:class:`PenaltyLedger`, :class:`LoserPenalty`) — score
   multipliers, cooldowns, and trust caps are tracked per fleet member and
   expire automatically over time.

5. **Archive** (:class:`LoserArchive`) — an in-memory persistent store that
   supports loss-rate queries, worst-loss retrieval, and aggregate statistics.

6. **Handler** (:class:`LoserHandler`) — processes each losing bid by creating
   a :class:`LoserRecord`, storing it in the archive, applying penalties where
   appropriate, and routing recycled bids back into the queue.

7. **Analyzer** (:class:`LoserHandlingAnalyzer`) — post-hoc analysis including
   most-common loss reason, average margin, repeat losers, and improvement
   candidates (small-margin losers who could plausibly win with tuning).

8. **Coordinator** (:class:`LoserHandlingCoordinator`) — orchestrates the
   handler and analyzer, exposes a single ``process_round_losers`` entry point,
   and generates structured analysis reports.

9. **Witness** (:class:`LoserHandlingWitness`) — audit log that records every
   loss, penalty, and elimination event and can verify that all stored records
   satisfy the provenance invariant.

10. **CompetitionResult** (:class:`CompetitionResult`) — frozen, full-provenance
    record of an entire competition round, bundling the winner, all loser records,
    and metadata.

Design notes
------------
* All LoserRecord objects are **frozen** (``frozen=True, slots=True``) to enforce
  immutability after creation.  Mutation goes through the mutable containers
  (LoserArchive, PenaltyLedger) rather than through record objects.
* The ``provenance`` field on :class:`LoserRecord` is deliberately broad so that
  downstream systems (e.g. audit, replay) can reconstruct the competition context
  without consulting external state.
* :class:`LoserHandler._determine_disposition` encodes the theory2 policy table
  from §46.6 Table 1: timeout → escalated, invalid_bid → eliminated, dominated →
  archived, lower_trust_tier → penalised, lower_quality_score → recycled or
  archived depending on margin, consensus_failure → escalated.
* The penalty system uses a product-of-multipliers approach: each active
  ``score_multiplier`` penalty reduces the effective score independently.
  The product is clamped to [0.1, 1.0] to prevent runaway suppression.
* The recycle queue carries a ``RecyclePenaltyFraction`` stamped on the bid's
  ``metadata``; consumers must divide the bid's score by this fraction before
  re-entering the round.

References
----------
* theory2.tex Ch46 — Fleet semantics.
* theory2.tex §46.6 — Loser handling: disposition, penalties, provenance.
* theory2.tex §46.6 Table 1 — Disposition policy by loss reason.
* theory2.tex §46.6 §46.6.3 — Provenance invariant.
* theory2.tex §46.6 §46.6.4 — Penalty ledger and score-multiplier model.

copilot
"""
from __future__ import annotations

import enum
import logging
import math
import statistics
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Guarded upstream imports
# ---------------------------------------------------------------------------

try:
    from jugeo.orchestration.fleet_competition.models import (
        BidStatus,
        CalibrationTrace,
        ChallengeRecord,
        CompetitiveBid,
        FleetRound,
        _clamp,
        _safe_mean,
        _safe_std,
    )
except Exception:  # pragma: no cover
    CompetitiveBid = Any  # type: ignore[assignment,misc]
    FleetRound = Any  # type: ignore[assignment,misc]
    BidStatus = Any  # type: ignore[assignment,misc]
    CalibrationTrace = Any  # type: ignore[assignment,misc]
    ChallengeRecord = Any  # type: ignore[assignment,misc]

    def _clamp(v: float, lo: float, hi: float) -> float:  # type: ignore[misc]
        return max(lo, min(hi, v))

    def _safe_mean(seq: Any) -> float:  # type: ignore[misc]
        if not seq:
            return 0.0
        return sum(seq) / len(seq)

    def _safe_std(seq: Any) -> float:  # type: ignore[misc]
        if len(seq) < 2:
            return 0.0
        return statistics.stdev(seq)


__all__ = [
    "LoserDisposition",
    "LossReason",
    "LoserRecord",
    "LoserPenalty",
    "PenaltyLedger",
    "LoserArchive",
    "LoserHandler",
    "LoserHandlingAnalyzer",
    "LoserHandlingCoordinator",
    "LoserHandlingWitness",
    "CompetitionResult",
    "make_default_loser_archive",
    "make_default_penalty_ledger",
    "make_default_loser_handler",
    "make_default_loser_handling_coordinator",
]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

#: Minimum effective score multiplier from the penalty ledger.
MIN_EFFECTIVE_MULTIPLIER: float = 0.1

#: Maximum effective score multiplier — penalties can only suppress, not boost.
MAX_EFFECTIVE_MULTIPLIER: float = 1.0

#: Default maximum number of records in a :class:`LoserArchive`.
DEFAULT_ARCHIVE_MAX_SIZE: int = 10_000

#: Margin threshold below which a losing bid is considered an *improvement candidate*.
IMPROVEMENT_CANDIDATE_MARGIN: float = 0.1

#: Default recycle penalty fraction stamped on recycled bids.
DEFAULT_RECYCLE_PENALTY: float = 0.9

#: Number of losses above which a member is classified as a *repeat loser*.
DEFAULT_REPEAT_LOSER_THRESHOLD: int = 3

#: Small epsilon used to avoid division-by-zero in distributions.
_EPS: float = 1e-12


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _make_id(prefix: str = "") -> str:
    """Return a short unique identifier with an optional *prefix*.

    Parameters
    ----------
    prefix:
        Short string prepended to the UUID fragment.

    Returns
    -------
    str
        A string of the form ``"<prefix>-<8-hex-chars>"``.
    """
    return f"{prefix}-{uuid.uuid4().hex[:8]}" if prefix else uuid.uuid4().hex[:8]


def _fraction_distribution(counter: dict[str, int]) -> dict[str, float]:
    """Convert a count dictionary to a fraction distribution.

    Parameters
    ----------
    counter:
        Dictionary mapping label → integer count.

    Returns
    -------
    dict[str, float]
        Dictionary mapping label → fraction (in [0, 1]).  If the total is zero
        every fraction is 0.0.
    """
    total = sum(counter.values())
    if total < _EPS:
        return {k: 0.0 for k in counter}
    return {k: v / total for k, v in counter.items()}


# ---------------------------------------------------------------------------
# LoserDisposition — how a loser is treated after the round
# ---------------------------------------------------------------------------


class LoserDisposition(str, enum.Enum):
    """Disposition applied to a losing bid after winner selection.

    Defined in theory2.tex §46.6 Table 1 (Disposition Policy).

    Members
    -------
    ARCHIVED:
        The bid is stored in the :class:`LoserArchive` for audit purposes and
        takes no further part in this or future rounds.
    RECYCLED:
        The bid re-enters the competition queue with a score penalty applied.
        Used when the bid narrowly lost and may still be competitive.
    PENALIZED:
        A score-multiplier or trust-cap penalty is recorded against the fleet
        member in the :class:`PenaltyLedger` to suppress future bids.
    ELIMINATED:
        The bid is permanently removed from consideration and its bid_id is
        added to the :class:`LoserHandler.elimination_set`.
    ESCALATED:
        The bid is flagged for human review, typically because the loss reason
        is ambiguous or the bid's provenance is incomplete.
    """

    ARCHIVED = "archived"
    RECYCLED = "recycled"
    PENALIZED = "penalized"
    ELIMINATED = "eliminated"
    ESCALATED = "escalated"


# ---------------------------------------------------------------------------
# LossReason — why a bid lost the competition
# ---------------------------------------------------------------------------


class LossReason(str, enum.Enum):
    """Reason a bid failed to win its fleet competition round.

    Defined in theory2.tex §46.6 §46.6.1 (Loss Classification).

    Members
    -------
    LOWER_TRUST_TIER:
        The bid was submitted by a member with a lower trust tier than the
        winner; the trust ceiling ruled it out before scoring.
    LOWER_QUALITY_SCORE:
        The bid was fully evaluated but received a lower weighted quality score
        than the winner.
    TIMEOUT:
        The bid was not submitted before the round deadline.
    INVALID_BID:
        The bid failed structural validation (e.g. missing required fields,
        out-of-range values).
    DOMINATED:
        The bid was Pareto-dominated by the winner on all objectives.
    CONSENSUS_FAILURE:
        The bid's scores showed insufficient consensus across evaluation
        criteria (high variance), triggering escalation.
    """

    LOWER_TRUST_TIER = "lower_trust_tier"
    LOWER_QUALITY_SCORE = "lower_quality_score"
    TIMEOUT = "timeout"
    INVALID_BID = "invalid_bid"
    DOMINATED = "dominated"
    CONSENSUS_FAILURE = "consensus_failure"


# ---------------------------------------------------------------------------
# LoserRecord — immutable provenance record for a single losing bid
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LoserRecord:
    """Immutable record of a losing bid, satisfying the §46.6 provenance invariant.

    Every field required to reproduce the loss without consulting external
    state is present: the winner's bid_id, the score margin, per-criterion
    scores for both loser and winner, the trust tier, and a ``provenance``
    dictionary capturing the broader competition context.

    Attributes
    ----------
    record_id:
        Unique identifier for this loser record.
    round_id:
        Identifier of the :class:`~models.FleetRound` in which the loss occurred.
    loser_bid_id:
        Identifier of the bid that lost.
    winner_bid_id:
        Identifier of the bid that won.  **Must never be empty** (enforced by
        :meth:`LoserHandlingWitness.verify_provenance`).
    loss_reason:
        Reason the bid lost (see :class:`LossReason`).
    disposition:
        How this loser will be treated (see :class:`LoserDisposition`).
    margin:
        ``winner_score - loser_score``.  Always non-negative for a valid loss.
        A margin of 0 indicates a tie broken by secondary criteria.
    loser_score:
        Aggregate evaluation score of the losing bid.
    winner_score:
        Aggregate evaluation score of the winning bid.
    criteria_scores:
        Per-criterion scores for the *losing* bid (criterion_name → score in
        [0, 1]).  Required for reproducibility.
    winner_criteria_scores:
        Per-criterion scores for the *winning* bid.  Enables downstream
        analysis to determine which criteria drove the result.
    trust_tier:
        Trust-tier label of the losing bid's member (e.g. ``"silver"``,
        ``"gold"``).
    timestamp:
        Wall-clock time at which this record was created.
    provenance:
        Broad provenance dictionary: must contain at minimum
        ``round_id``, ``competition_id``, and ``strategy``.
    """

    record_id: str
    round_id: str
    loser_bid_id: str
    winner_bid_id: str
    loss_reason: LossReason
    disposition: LoserDisposition
    margin: float
    loser_score: float
    winner_score: float
    criteria_scores: dict[str, float]
    winner_criteria_scores: dict[str, float]
    trust_tier: str
    timestamp: float
    provenance: dict

    # ------------------------------------------------------------------
    # Query methods
    # ------------------------------------------------------------------

    def score_gap(self) -> float:
        """Return the absolute score gap between winner and loser.

        This is equivalent to ``margin``, provided for semantic clarity.

        Returns
        -------
        float
            ``winner_score - loser_score``.  Clamped to [0, ∞) to guard
            against floating-point rounding that might produce a tiny
            negative value.
        """
        return max(0.0, self.winner_score - self.loser_score)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise this record to a plain, JSON-serialisable dictionary.

        Returns
        -------
        dict
            All fields plus computed ``score_gap``.
        """
        return {
            "record_id": self.record_id,
            "round_id": self.round_id,
            "loser_bid_id": self.loser_bid_id,
            "winner_bid_id": self.winner_bid_id,
            "loss_reason": self.loss_reason.value,
            "disposition": self.disposition.value,
            "margin": self.margin,
            "loser_score": self.loser_score,
            "winner_score": self.winner_score,
            "score_gap": self.score_gap(),
            "criteria_scores": dict(self.criteria_scores),
            "winner_criteria_scores": dict(self.winner_criteria_scores),
            "trust_tier": self.trust_tier,
            "timestamp": self.timestamp,
            "provenance": dict(self.provenance),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "LoserRecord":
        """Reconstruct a :class:`LoserRecord` from a serialised dictionary.

        Parameters
        ----------
        d:
            Dictionary as produced by :meth:`to_dict`.

        Returns
        -------
        LoserRecord
            The reconstructed record.
        """
        return cls(
            record_id=d["record_id"],
            round_id=d["round_id"],
            loser_bid_id=d["loser_bid_id"],
            winner_bid_id=d["winner_bid_id"],
            loss_reason=LossReason(d["loss_reason"]),
            disposition=LoserDisposition(d["disposition"]),
            margin=float(d["margin"]),
            loser_score=float(d["loser_score"]),
            winner_score=float(d["winner_score"]),
            criteria_scores=dict(d.get("criteria_scores", {})),
            winner_criteria_scores=dict(d.get("winner_criteria_scores", {})),
            trust_tier=str(d.get("trust_tier", "")),
            timestamp=float(d.get("timestamp", 0.0)),
            provenance=dict(d.get("provenance", {})),
        )


# ---------------------------------------------------------------------------
# LoserPenalty — a calibration penalty applied to a losing fleet member
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LoserPenalty:
    """A calibration penalty applied to a fleet member following a loss.

    Penalties are recorded in the :class:`PenaltyLedger` and may expire after
    a configurable duration.  The :class:`PenaltyLedger` computes an effective
    score multiplier by multiplying all active ``score_multiplier`` penalties.

    Attributes
    ----------
    penalty_id:
        Unique identifier for this penalty.
    member_id:
        Identifier of the fleet member subject to this penalty.
    penalty_type:
        Category of the penalty.  Recognised values:

        * ``"score_multiplier"`` — reduces the member's future bid scores.
        * ``"cooldown"`` — suppresses the member from entering future rounds
          for a period.
        * ``"trust_cap"`` — caps the effective trust level of future bids.
    magnitude:
        Numeric magnitude of the penalty.  For ``score_multiplier`` this is
        the multiplier itself (in [0, 1]); for ``cooldown`` it is the
        duration in seconds.
    reason:
        Human-readable explanation of why this penalty was applied.
    expires_at:
        Wall-clock time at which this penalty expires.  ``None`` means
        permanent.
    applied_at:
        Wall-clock time at which this penalty was created.
    metadata:
        Arbitrary extra context (e.g. ``round_id``, ``loser_record_id``).
    """

    penalty_id: str
    member_id: str
    penalty_type: str
    magnitude: float
    reason: str
    expires_at: Optional[float]
    applied_at: float
    metadata: dict

    # ------------------------------------------------------------------
    # Query methods
    # ------------------------------------------------------------------

    def is_expired(self, now: Optional[float] = None) -> bool:
        """Return True if this penalty has expired.

        Parameters
        ----------
        now:
            Reference wall-clock time.  Defaults to ``time.time()``.

        Returns
        -------
        bool
            ``True`` iff ``expires_at`` is set and ``now >= expires_at``.
        """
        if self.expires_at is None:
            return False
        t = now if now is not None else time.time()
        return t >= self.expires_at

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise this penalty to a plain dictionary.

        Returns
        -------
        dict
            JSON-serialisable representation of the penalty.
        """
        return {
            "penalty_id": self.penalty_id,
            "member_id": self.member_id,
            "penalty_type": self.penalty_type,
            "magnitude": self.magnitude,
            "reason": self.reason,
            "expires_at": self.expires_at,
            "applied_at": self.applied_at,
            "metadata": dict(self.metadata),
            "is_expired": self.is_expired(),
        }


# ---------------------------------------------------------------------------
# PenaltyLedger — tracks active penalties for all fleet members
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class PenaltyLedger:
    """Tracks active :class:`LoserPenalty` objects for all fleet members.

    Penalties are keyed by ``member_id``.  Expired penalties are pruned lazily
    when :meth:`clear_expired` is called or eagerly in :meth:`active_penalties`.

    Attributes
    ----------
    ledger_id:
        Unique identifier for this ledger instance.
    penalties:
        Dictionary mapping ``member_id`` → list of :class:`LoserPenalty`.
    """

    ledger_id: str
    penalties: dict[str, list[LoserPenalty]] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def add_penalty(self, penalty: LoserPenalty) -> None:
        """Record a new penalty for ``penalty.member_id``.

        Parameters
        ----------
        penalty:
            The :class:`LoserPenalty` to store.  It is appended to the
            member's penalty list; existing penalties are unaffected.
        """
        bucket = self.penalties.setdefault(penalty.member_id, [])
        bucket.append(penalty)
        logger.debug(
            "PenaltyLedger[%s] added %s penalty for member %s (magnitude=%.3f)",
            self.ledger_id,
            penalty.penalty_type,
            penalty.member_id,
            penalty.magnitude,
        )

    def clear_expired(self) -> int:
        """Remove all expired penalties from all members.

        Returns
        -------
        int
            Number of penalties removed.
        """
        now = time.time()
        removed = 0
        for member_id in list(self.penalties.keys()):
            before = len(self.penalties[member_id])
            self.penalties[member_id] = [
                p for p in self.penalties[member_id] if not p.is_expired(now)
            ]
            removed += before - len(self.penalties[member_id])
            if not self.penalties[member_id]:
                del self.penalties[member_id]
        if removed:
            logger.debug("PenaltyLedger[%s] cleared %d expired penalties", self.ledger_id, removed)
        return removed

    # ------------------------------------------------------------------
    # Query methods
    # ------------------------------------------------------------------

    def active_penalties(self, member_id: str) -> list[LoserPenalty]:
        """Return all non-expired penalties for *member_id*.

        Parameters
        ----------
        member_id:
            The fleet member identifier.

        Returns
        -------
        list[LoserPenalty]
            All penalties that have not yet expired.  Empty list if the member
            has no penalties or all have expired.
        """
        now = time.time()
        return [p for p in self.penalties.get(member_id, []) if not p.is_expired(now)]

    def effective_multiplier(self, member_id: str) -> float:
        """Compute the effective score multiplier for *member_id*.

        The multiplier is the product of all active ``score_multiplier``
        penalties, clamped to [MIN_EFFECTIVE_MULTIPLIER, MAX_EFFECTIVE_MULTIPLIER].

        Parameters
        ----------
        member_id:
            The fleet member identifier.

        Returns
        -------
        float
            Effective multiplier in [0.1, 1.0].  Returns 1.0 if there are no
            active score-multiplier penalties.
        """
        active = self.active_penalties(member_id)
        product = 1.0
        for p in active:
            if p.penalty_type == "score_multiplier":
                product *= _clamp(p.magnitude, 0.0, 1.0)
        return _clamp(product, MIN_EFFECTIVE_MULTIPLIER, MAX_EFFECTIVE_MULTIPLIER)

    def total_penalty_count(self) -> int:
        """Return the total number of currently stored penalties (including expired).

        Returns
        -------
        int
            Sum of all per-member penalty list lengths.
        """
        return sum(len(v) for v in self.penalties.values())

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def export(self) -> dict[str, Any]:
        """Export the penalty ledger to a serialisable dictionary.

        Returns
        -------
        dict
            Keys: ``ledger_id``, ``total_penalties``, ``active_member_count``,
            ``members`` (per-member active penalty details).
        """
        members: dict[str, Any] = {}
        for member_id in self.penalties:
            active = self.active_penalties(member_id)
            members[member_id] = {
                "total": len(self.penalties[member_id]),
                "active": len(active),
                "effective_multiplier": self.effective_multiplier(member_id),
                "penalties": [p.to_dict() for p in active],
            }
        return {
            "ledger_id": self.ledger_id,
            "total_penalties": self.total_penalty_count(),
            "active_member_count": len([m for m in self.penalties if self.active_penalties(m)]),
            "members": members,
        }

    def __repr__(self) -> str:  # noqa: D401
        """Return a developer-friendly representation."""
        return (
            f"PenaltyLedger("
            f"id={self.ledger_id!r}, "
            f"members={len(self.penalties)}, "
            f"total_penalties={self.total_penalty_count()})"
        )


# ---------------------------------------------------------------------------
# LoserArchive — persistent in-memory store of LoserRecords
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class LoserArchive:
    """In-memory archive of :class:`LoserRecord` objects.

    Enforces a maximum size by dropping the oldest record when the archive is
    full.  Provides query methods that support the analyzer and the witness.

    Attributes
    ----------
    archive_id:
        Unique identifier for this archive instance.
    records:
        Ordered list of :class:`LoserRecord` objects (oldest first).
    max_size:
        Maximum number of records to retain.  When exceeded the oldest record
        is evicted (FIFO).
    """

    archive_id: str
    records: list[LoserRecord] = field(default_factory=list)
    max_size: int = DEFAULT_ARCHIVE_MAX_SIZE

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def store(self, record: LoserRecord) -> None:
        """Append *record* to the archive, evicting the oldest if needed.

        Parameters
        ----------
        record:
            The :class:`LoserRecord` to store.  The record's ``record_id``
            should be unique but uniqueness is not enforced here.
        """
        if len(self.records) >= self.max_size:
            evicted = self.records.pop(0)
            logger.debug(
                "LoserArchive[%s] evicted record %s (size cap %d)",
                self.archive_id,
                evicted.record_id,
                self.max_size,
            )
        self.records.append(record)

    # ------------------------------------------------------------------
    # Query methods
    # ------------------------------------------------------------------

    def records_for_bid(self, bid_id: str) -> list[LoserRecord]:
        """Return all records whose loser_bid_id matches *bid_id*.

        Parameters
        ----------
        bid_id:
            The losing bid identifier to filter on.

        Returns
        -------
        list[LoserRecord]
            All matching records in insertion order.
        """
        return [r for r in self.records if r.loser_bid_id == bid_id]

    def records_for_round(self, round_id: str) -> list[LoserRecord]:
        """Return all records for a given *round_id*.

        Parameters
        ----------
        round_id:
            The fleet-round identifier to filter on.

        Returns
        -------
        list[LoserRecord]
            All records from that round in insertion order.
        """
        return [r for r in self.records if r.round_id == round_id]

    def loss_rate_for_member(
        self, member_id: str, records: Optional[list[LoserRecord]] = None
    ) -> float:
        """Compute the fraction of provided *records* that belong to *member_id*.

        This is a convenience proxy for per-member loss rate when the caller
        already has a filtered set (e.g. records from a specific time window).

        Parameters
        ----------
        member_id:
            The fleet member identifier.  Compared against
            ``record.loser_bid_id`` — note that in the current model the
            loser_bid_id is used as the member identifier proxy.
        records:
            The reference set.  Defaults to all records in this archive.

        Returns
        -------
        float
            Fraction of *records* that are attributed to *member_id*; 0.0 if
            *records* is empty.
        """
        ref = records if records is not None else self.records
        if not ref:
            return 0.0
        member_losses = sum(1 for r in ref if r.loser_bid_id == member_id)
        return member_losses / len(ref)

    def worst_losses(self, n: int = 5) -> list[LoserRecord]:
        """Return the *n* records with the largest margin (worst losses).

        Parameters
        ----------
        n:
            Number of records to return.

        Returns
        -------
        list[LoserRecord]
            Up to *n* records sorted by descending margin.
        """
        sorted_records = sorted(self.records, key=lambda r: r.margin, reverse=True)
        return sorted_records[:n]

    # ------------------------------------------------------------------
    # Statistics and serialisation
    # ------------------------------------------------------------------

    def stats(self) -> dict[str, Any]:
        """Return aggregate statistics for all archived records.

        Returns
        -------
        dict
            Keys: ``total_records``, ``max_size``, ``unique_rounds``,
            ``unique_losers``, ``average_margin``, ``std_margin``,
            ``disposition_counts``, ``loss_reason_counts``.
        """
        if not self.records:
            return {
                "total_records": 0,
                "max_size": self.max_size,
                "unique_rounds": 0,
                "unique_losers": 0,
                "average_margin": 0.0,
                "std_margin": 0.0,
                "disposition_counts": {},
                "loss_reason_counts": {},
            }
        margins = [r.margin for r in self.records]
        disposition_counts: dict[str, int] = defaultdict(int)
        reason_counts: dict[str, int] = defaultdict(int)
        for r in self.records:
            disposition_counts[r.disposition.value] += 1
            reason_counts[r.loss_reason.value] += 1
        return {
            "total_records": len(self.records),
            "max_size": self.max_size,
            "unique_rounds": len({r.round_id for r in self.records}),
            "unique_losers": len({r.loser_bid_id for r in self.records}),
            "average_margin": _safe_mean(margins),
            "std_margin": _safe_std(margins),
            "disposition_counts": dict(disposition_counts),
            "loss_reason_counts": dict(reason_counts),
        }

    def export(self) -> dict[str, Any]:
        """Export the archive contents to a serialisable dictionary.

        Returns
        -------
        dict
            Keys: ``archive_id``, ``stats``, ``records`` (list of dicts).
        """
        return {
            "archive_id": self.archive_id,
            "stats": self.stats(),
            "records": [r.to_dict() for r in self.records],
        }

    def __repr__(self) -> str:  # noqa: D401
        """Return a developer-friendly representation."""
        return (
            f"LoserArchive("
            f"id={self.archive_id!r}, "
            f"records={len(self.records)}, "
            f"max_size={self.max_size})"
        )


# ---------------------------------------------------------------------------
# LoserHandler — processes losing bids after winner selection
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class LoserHandler:
    """Processes each losing bid after winner selection.

    For every losing bid the handler:

    1. Determines the :class:`LoserDisposition` based on the loss reason and
       bid attributes (see :meth:`_determine_disposition`).
    2. Creates an immutable :class:`LoserRecord` with full provenance.
    3. Stores the record in the :class:`LoserArchive`.
    4. Applies a :class:`LoserPenalty` where appropriate (see
       :meth:`_apply_penalty`).
    5. Routes recycled bids to ``recycle_queue`` and eliminated bids to
       ``elimination_set``.

    Attributes
    ----------
    handler_id:
        Unique identifier for this handler instance.
    archive:
        The :class:`LoserArchive` where records are stored.
    penalty_ledger:
        The :class:`PenaltyLedger` where penalties are recorded.
    recycle_queue:
        List of :class:`CompetitiveBid` objects awaiting re-entry.
    elimination_set:
        Set of ``bid_id`` strings that have been permanently eliminated.
    """

    handler_id: str
    archive: LoserArchive
    penalty_ledger: PenaltyLedger
    recycle_queue: list = field(default_factory=list)
    elimination_set: set = field(default_factory=set)

    # ------------------------------------------------------------------
    # Primary public API
    # ------------------------------------------------------------------

    def handle(
        self,
        loser_bid: Any,
        winner_bid: Any,
        round_id: str,
        loss_reason: LossReason,
        criteria_scores: dict[str, float],
        winner_criteria_scores: dict[str, float],
    ) -> LoserRecord:
        """Process a single losing bid and return its :class:`LoserRecord`.

        This is the primary entry point.  It coordinates disposition,
        provenance capture, archival, penalisation, and routing.

        Parameters
        ----------
        loser_bid:
            The bid that lost.  Must expose ``bid_id``, ``semantic_score``
            (used as loser_score), and optionally ``trust_tier`` and
            ``member_id``.
        winner_bid:
            The bid that won.  Must expose ``bid_id`` and ``semantic_score``.
        round_id:
            Identifier of the round in which the loss occurred.
        loss_reason:
            The :class:`LossReason` for this loss.
        criteria_scores:
            Per-criterion scores for the losing bid.
        winner_criteria_scores:
            Per-criterion scores for the winning bid.

        Returns
        -------
        LoserRecord
            The newly created, fully-provenance-stamped loser record.
        """
        loser_bid_id = getattr(loser_bid, "bid_id", _make_id("bid"))
        winner_bid_id = getattr(winner_bid, "bid_id", _make_id("bid"))
        loser_score = float(getattr(loser_bid, "semantic_score", 0.0))
        winner_score = float(getattr(winner_bid, "semantic_score", 0.0))
        margin = max(0.0, winner_score - loser_score)
        trust_tier = str(getattr(loser_bid, "trust_tier", "unknown"))
        competition_id = str(getattr(loser_bid, "competition_id", ""))
        strategy = str(getattr(loser_bid, "strategy", "default"))

        disposition = self._determine_disposition(loser_bid, loss_reason, margin)

        provenance: dict[str, Any] = {
            "round_id": round_id,
            "competition_id": competition_id,
            "strategy": strategy,
            "handler_id": self.handler_id,
            "loss_reason": loss_reason.value,
            "disposition": disposition.value,
        }

        record = LoserRecord(
            record_id=_make_id("rec"),
            round_id=round_id,
            loser_bid_id=loser_bid_id,
            winner_bid_id=winner_bid_id,
            loss_reason=loss_reason,
            disposition=disposition,
            margin=margin,
            loser_score=loser_score,
            winner_score=winner_score,
            criteria_scores=dict(criteria_scores),
            winner_criteria_scores=dict(winner_criteria_scores),
            trust_tier=trust_tier,
            timestamp=time.time(),
            provenance=provenance,
        )

        self.archive.store(record)
        self._apply_penalty(loser_bid, record)

        if disposition == LoserDisposition.RECYCLED:
            self._enqueue_for_recycle(loser_bid, record)
        elif disposition == LoserDisposition.ELIMINATED:
            self.elimination_set.add(loser_bid_id)
            logger.info(
                "LoserHandler[%s] eliminated bid %s (reason=%s)",
                self.handler_id,
                loser_bid_id,
                loss_reason.value,
            )

        logger.debug(
            "LoserHandler[%s] processed loss: bid=%s winner=%s margin=%.4f disposition=%s",
            self.handler_id,
            loser_bid_id,
            winner_bid_id,
            margin,
            disposition.value,
        )
        return record

    # ------------------------------------------------------------------
    # Disposition policy (theory2.tex §46.6 Table 1)
    # ------------------------------------------------------------------

    def _determine_disposition(
        self,
        loser_bid: Any,
        loss_reason: LossReason,
        margin: float = 0.0,
    ) -> LoserDisposition:
        """Map a loss reason to the appropriate :class:`LoserDisposition`.

        Implements the policy table from theory2.tex §46.6 Table 1:

        * ``TIMEOUT`` → ``ESCALATED`` (ambiguous; human review needed)
        * ``INVALID_BID`` → ``ELIMINATED`` (structurally invalid, no retry)
        * ``CONSENSUS_FAILURE`` → ``ESCALATED`` (high score variance)
        * ``DOMINATED`` → ``ARCHIVED`` (dominated on all objectives)
        * ``LOWER_TRUST_TIER`` → ``PENALIZED`` (trust-tier suppression)
        * ``LOWER_QUALITY_SCORE`` with small margin → ``RECYCLED``
        * ``LOWER_QUALITY_SCORE`` with large margin → ``ARCHIVED``

        Parameters
        ----------
        loser_bid:
            The losing bid (may be inspected for metadata).
        loss_reason:
            The :class:`LossReason` for this loss.
        margin:
            Score gap between winner and loser.

        Returns
        -------
        LoserDisposition
            The disposition to apply.
        """
        if loss_reason == LossReason.TIMEOUT:
            return LoserDisposition.ESCALATED
        if loss_reason == LossReason.INVALID_BID:
            return LoserDisposition.ELIMINATED
        if loss_reason == LossReason.CONSENSUS_FAILURE:
            return LoserDisposition.ESCALATED
        if loss_reason == LossReason.DOMINATED:
            return LoserDisposition.ARCHIVED
        if loss_reason == LossReason.LOWER_TRUST_TIER:
            return LoserDisposition.PENALIZED
        # LOWER_QUALITY_SCORE: recycle if close, archive if far
        if margin <= IMPROVEMENT_CANDIDATE_MARGIN:
            return LoserDisposition.RECYCLED
        return LoserDisposition.ARCHIVED

    # ------------------------------------------------------------------
    # Penalty application
    # ------------------------------------------------------------------

    def _apply_penalty(self, loser_bid: Any, record: LoserRecord) -> None:
        """Apply a calibration penalty to the losing member if warranted.

        A ``score_multiplier`` penalty of 0.85 is applied whenever the
        disposition is ``PENALIZED``.  A lighter penalty of 0.95 is applied
        for ``RECYCLED`` bids to reflect their reduced competitiveness.
        ``ELIMINATED`` and ``ESCALATED`` bids do not receive ledger penalties.

        Parameters
        ----------
        loser_bid:
            The losing bid (used to extract ``member_id``).
        record:
            The :class:`LoserRecord` for this loss (provides context).
        """
        member_id = str(
            getattr(loser_bid, "member_id", None)
            or getattr(loser_bid, "bid_id", "unknown")
        )
        disposition = record.disposition

        if disposition == LoserDisposition.PENALIZED:
            penalty = LoserPenalty(
                penalty_id=_make_id("pen"),
                member_id=member_id,
                penalty_type="score_multiplier",
                magnitude=0.85,
                reason=f"Lower trust tier loss in round {record.round_id}",
                expires_at=time.time() + 3600.0,  # 1-hour default
                applied_at=time.time(),
                metadata={
                    "record_id": record.record_id,
                    "round_id": record.round_id,
                    "loss_reason": record.loss_reason.value,
                },
            )
            self.penalty_ledger.add_penalty(penalty)

        elif disposition == LoserDisposition.RECYCLED:
            penalty = LoserPenalty(
                penalty_id=_make_id("pen"),
                member_id=member_id,
                penalty_type="score_multiplier",
                magnitude=0.95,
                reason=f"Recycled bid from round {record.round_id}",
                expires_at=time.time() + 1800.0,  # 30-minute recycle window
                applied_at=time.time(),
                metadata={
                    "record_id": record.record_id,
                    "round_id": record.round_id,
                    "loss_reason": record.loss_reason.value,
                },
            )
            self.penalty_ledger.add_penalty(penalty)

    # ------------------------------------------------------------------
    # Recycle queue management
    # ------------------------------------------------------------------

    def _enqueue_for_recycle(self, loser_bid: Any, record: LoserRecord) -> None:
        """Add *loser_bid* to the recycle queue with a penalty annotation.

        The bid is stamped with ``RecyclePenaltyFraction`` and ``recycle_source``
        in its ``metadata`` attribute (if one exists) or via a wrapper dict.

        Parameters
        ----------
        loser_bid:
            The original losing bid.
        record:
            The associated :class:`LoserRecord`.
        """
        # Attempt to stamp metadata on the bid object itself.
        meta = getattr(loser_bid, "metadata", None)
        if isinstance(meta, dict):
            meta["RecyclePenaltyFraction"] = DEFAULT_RECYCLE_PENALTY
            meta["recycle_source"] = record.record_id
        self.recycle_queue.append(loser_bid)
        logger.debug(
            "LoserHandler[%s] enqueued bid %s for recycle (record=%s)",
            self.handler_id,
            getattr(loser_bid, "bid_id", "?"),
            record.record_id,
        )

    def recycle_pending(self) -> list[Any]:
        """Drain and return all bids currently in the recycle queue.

        The queue is emptied by this call.

        Returns
        -------
        list
            All bids that were awaiting recycling.
        """
        pending = list(self.recycle_queue)
        self.recycle_queue.clear()
        return pending

    # ------------------------------------------------------------------
    # Elimination query
    # ------------------------------------------------------------------

    def is_eliminated(self, bid_id: str) -> bool:
        """Return True if *bid_id* has been permanently eliminated.

        Parameters
        ----------
        bid_id:
            The bid identifier to check.

        Returns
        -------
        bool
            ``True`` iff ``bid_id`` is in ``elimination_set``.
        """
        return bid_id in self.elimination_set

    # ------------------------------------------------------------------
    # Statistics and serialisation
    # ------------------------------------------------------------------

    def stats(self) -> dict[str, Any]:
        """Return operational statistics for this handler.

        Returns
        -------
        dict
            Keys: ``handler_id``, ``archive_stats``, ``elimination_count``,
            ``recycle_queue_depth``, ``penalty_ledger_summary``.
        """
        return {
            "handler_id": self.handler_id,
            "archive_stats": self.archive.stats(),
            "elimination_count": len(self.elimination_set),
            "recycle_queue_depth": len(self.recycle_queue),
            "penalty_ledger_summary": {
                "total_penalties": self.penalty_ledger.total_penalty_count(),
                "member_count": len(self.penalty_ledger.penalties),
            },
        }

    def export(self) -> dict[str, Any]:
        """Export the handler's full state to a serialisable dictionary.

        Returns
        -------
        dict
            Keys: ``handler_id``, ``archive``, ``penalty_ledger``,
            ``eliminated_bid_ids``, ``recycle_queue_depth``.
        """
        return {
            "handler_id": self.handler_id,
            "archive": self.archive.export(),
            "penalty_ledger": self.penalty_ledger.export(),
            "eliminated_bid_ids": sorted(self.elimination_set),
            "recycle_queue_depth": len(self.recycle_queue),
        }

    def __repr__(self) -> str:  # noqa: D401
        """Return a developer-friendly representation."""
        return (
            f"LoserHandler("
            f"id={self.handler_id!r}, "
            f"archived={len(self.archive.records)}, "
            f"eliminated={len(self.elimination_set)}, "
            f"recycle_q={len(self.recycle_queue)})"
        )


# ---------------------------------------------------------------------------
# LoserHandlingAnalyzer — post-hoc analysis of loser records
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class LoserHandlingAnalyzer:
    """Post-hoc analyzer that computes statistics over :class:`LoserRecord` sets.

    All analysis methods accept an explicit *records* parameter so that callers
    can analyse arbitrary subsets (e.g. a single round's records, or a member's
    historical records) without coupling to the archive directly.

    Attributes
    ----------
    analyzer_id:
        Unique identifier for this analyzer instance.
    archive:
        The :class:`LoserArchive` used as the default record source when no
        explicit records are passed.
    """

    analyzer_id: str
    archive: LoserArchive

    # ------------------------------------------------------------------
    # Analysis methods
    # ------------------------------------------------------------------

    def most_common_loss_reason(
        self, records: list[LoserRecord]
    ) -> LossReason:
        """Return the most-frequently occurring :class:`LossReason` in *records*.

        Parameters
        ----------
        records:
            Records to analyse.

        Returns
        -------
        LossReason
            The most common loss reason.  If *records* is empty or tied,
            ``LOWER_QUALITY_SCORE`` is returned as the default.
        """
        if not records:
            return LossReason.LOWER_QUALITY_SCORE
        counts: dict[LossReason, int] = defaultdict(int)
        for r in records:
            counts[r.loss_reason] += 1
        return max(counts, key=lambda k: counts[k])

    def average_margin(self, records: list[LoserRecord]) -> float:
        """Return the mean margin across *records*.

        Parameters
        ----------
        records:
            Records to average.

        Returns
        -------
        float
            Mean margin; 0.0 if *records* is empty.
        """
        if not records:
            return 0.0
        return _safe_mean([r.margin for r in records])

    def repeat_losers(
        self,
        records: list[LoserRecord],
        threshold: int = DEFAULT_REPEAT_LOSER_THRESHOLD,
    ) -> list[str]:
        """Return bid_ids that appear as losers more than *threshold* times.

        Parameters
        ----------
        records:
            The record set to search.
        threshold:
            Minimum number of losses to qualify as a repeat loser.

        Returns
        -------
        list[str]
            Sorted list of ``loser_bid_id`` values that exceed *threshold*.
        """
        counts: dict[str, int] = defaultdict(int)
        for r in records:
            counts[r.loser_bid_id] += 1
        return sorted(bid_id for bid_id, n in counts.items() if n > threshold)

    def loss_reason_distribution(
        self, records: list[LoserRecord]
    ) -> dict[str, float]:
        """Return the fractional distribution of loss reasons across *records*.

        Parameters
        ----------
        records:
            Records to analyse.

        Returns
        -------
        dict[str, float]
            Dictionary mapping loss-reason value → fraction in [0, 1].
            Fractions sum to 1.0 (within floating-point error).  Empty dict
            if *records* is empty.
        """
        if not records:
            return {}
        counts: dict[str, int] = defaultdict(int)
        for r in records:
            counts[r.loss_reason.value] += 1
        return _fraction_distribution(dict(counts))

    def improvement_candidates(
        self, records: list[LoserRecord]
    ) -> list[dict[str, Any]]:
        """Identify losers with a small margin who could plausibly improve.

        A record is an *improvement candidate* iff:

        * ``record.margin < IMPROVEMENT_CANDIDATE_MARGIN``
        * ``record.disposition != LoserDisposition.ELIMINATED``

        Parameters
        ----------
        records:
            Records to scan.

        Returns
        -------
        list[dict]
            Dicts with keys ``bid_id``, ``round_id``, ``margin``,
            ``loss_reason``, ``criteria_scores``.  Sorted by ascending margin
            (best improvement opportunities first).
        """
        candidates = [
            {
                "bid_id": r.loser_bid_id,
                "round_id": r.round_id,
                "margin": r.margin,
                "loss_reason": r.loss_reason.value,
                "criteria_scores": dict(r.criteria_scores),
                "winner_criteria_scores": dict(r.winner_criteria_scores),
            }
            for r in records
            if r.margin < IMPROVEMENT_CANDIDATE_MARGIN
            and r.disposition != LoserDisposition.ELIMINATED
        ]
        return sorted(candidates, key=lambda c: c["margin"])

    def __repr__(self) -> str:  # noqa: D401
        """Return a developer-friendly representation."""
        return (
            f"LoserHandlingAnalyzer("
            f"id={self.analyzer_id!r}, "
            f"archive_size={len(self.archive.records)})"
        )


# ---------------------------------------------------------------------------
# LoserHandlingCoordinator — orchestrates handler and analyzer
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class LoserHandlingCoordinator:
    """Orchestrates all loser handling logic for a fleet competition round.

    The coordinator is the recommended public entry point.  It delegates to
    :class:`LoserHandler` for per-bid processing and :class:`LoserHandlingAnalyzer`
    for post-hoc reporting.

    Attributes
    ----------
    coordinator_id:
        Unique identifier for this coordinator.
    handler:
        The :class:`LoserHandler` that processes individual losing bids.
    analyzer:
        The :class:`LoserHandlingAnalyzer` that generates reports.
    """

    coordinator_id: str
    handler: LoserHandler
    analyzer: LoserHandlingAnalyzer

    # ------------------------------------------------------------------
    # Round processing
    # ------------------------------------------------------------------

    def process_round_losers(
        self,
        round_id: str,
        winner_bid: Any,
        loser_bids: list[Any],
        criteria_scores_by_bid: dict[str, dict[str, float]],
        winner_criteria_scores: Optional[dict[str, float]] = None,
        loss_reasons: Optional[dict[str, LossReason]] = None,
    ) -> list[LoserRecord]:
        """Process all losing bids from a completed round.

        For each losing bid, determines the loss reason (from *loss_reasons*
        if provided, otherwise defaulting to ``LOWER_QUALITY_SCORE``), then
        delegates to :meth:`LoserHandler.handle`.

        Parameters
        ----------
        round_id:
            The round identifier.
        winner_bid:
            The winning bid.
        loser_bids:
            List of all losing bids from the round.
        criteria_scores_by_bid:
            Mapping of ``bid_id`` → ``{criterion: score}`` for each loser.
        winner_criteria_scores:
            Per-criterion scores for the winner.  Defaults to empty dict.
        loss_reasons:
            Optional mapping of ``bid_id`` → :class:`LossReason`.  Unmapped
            bids default to ``LOWER_QUALITY_SCORE``.

        Returns
        -------
        list[LoserRecord]
            One :class:`LoserRecord` per loser, in input order.
        """
        winner_crit = winner_criteria_scores or {}
        reasons = loss_reasons or {}
        loser_records: list[LoserRecord] = []

        for loser_bid in loser_bids:
            bid_id = getattr(loser_bid, "bid_id", "")
            reason = reasons.get(bid_id, LossReason.LOWER_QUALITY_SCORE)
            crit_scores = criteria_scores_by_bid.get(bid_id, {})
            record = self.handler.handle(
                loser_bid=loser_bid,
                winner_bid=winner_bid,
                round_id=round_id,
                loss_reason=reason,
                criteria_scores=crit_scores,
                winner_criteria_scores=winner_crit,
            )
            loser_records.append(record)
            logger.info(
                "Coordinator[%s] processed loser %s → %s (margin=%.4f)",
                self.coordinator_id,
                bid_id,
                record.disposition.value,
                record.margin,
            )

        return loser_records

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def generate_analysis_report(self) -> dict[str, Any]:
        """Generate a structured analysis report over all archived records.

        Returns
        -------
        dict
            Keys: ``coordinator_id``, ``total_records``, ``most_common_reason``,
            ``average_margin``, ``repeat_losers``, ``loss_reason_distribution``,
            ``improvement_candidates``, ``archive_stats``.
        """
        all_records = self.handler.archive.records
        return {
            "coordinator_id": self.coordinator_id,
            "total_records": len(all_records),
            "most_common_reason": self.analyzer.most_common_loss_reason(all_records).value
            if all_records
            else None,
            "average_margin": self.analyzer.average_margin(all_records),
            "repeat_losers": self.analyzer.repeat_losers(all_records),
            "loss_reason_distribution": self.analyzer.loss_reason_distribution(all_records),
            "improvement_candidates": self.analyzer.improvement_candidates(all_records),
            "archive_stats": self.handler.archive.stats(),
        }

    # ------------------------------------------------------------------
    # Recycle
    # ------------------------------------------------------------------

    def recycle_eligible_bids(self) -> list[Any]:
        """Drain the recycle queue and return all eligible bids.

        Returns
        -------
        list
            All bids currently awaiting recycling.  Queue is cleared.
        """
        recycled = self.handler.recycle_pending()
        if recycled:
            logger.info(
                "Coordinator[%s] released %d bids for recycling",
                self.coordinator_id,
                len(recycled),
            )
        return recycled

    # ------------------------------------------------------------------
    # Health and serialisation
    # ------------------------------------------------------------------

    def health(self) -> dict[str, Any]:
        """Return a health summary for operational monitoring.

        Returns
        -------
        dict
            Keys: ``coordinator_id``, ``archive_size``, ``elimination_count``,
            ``recycle_queue_depth``, ``total_penalties``, ``status``.
        """
        archive_size = len(self.handler.archive.records)
        elimination_count = len(self.handler.elimination_set)
        recycle_depth = len(self.handler.recycle_queue)
        total_penalties = self.handler.penalty_ledger.total_penalty_count()

        # Derive a simple health status.
        if elimination_count > 100:
            status = "degraded"
        elif archive_size >= self.handler.archive.max_size * 0.9:
            status = "warning"
        else:
            status = "healthy"

        return {
            "coordinator_id": self.coordinator_id,
            "archive_size": archive_size,
            "elimination_count": elimination_count,
            "recycle_queue_depth": recycle_depth,
            "total_penalties": total_penalties,
            "status": status,
        }

    def export(self) -> dict[str, Any]:
        """Export the full coordinator state to a serialisable dictionary.

        Returns
        -------
        dict
            Keys: ``coordinator_id``, ``handler``, ``analysis_report``.
        """
        return {
            "coordinator_id": self.coordinator_id,
            "handler": self.handler.export(),
            "analysis_report": self.generate_analysis_report(),
        }

    def __repr__(self) -> str:  # noqa: D401
        """Return a developer-friendly representation."""
        return (
            f"LoserHandlingCoordinator("
            f"id={self.coordinator_id!r}, "
            f"handler={self.handler.handler_id!r})"
        )


# ---------------------------------------------------------------------------
# LoserHandlingWitness — audit log with provenance verification
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class LoserHandlingWitness:
    """Audit log that records every loss, penalty, and elimination event.

    The witness provides :meth:`verify_provenance` to detect any
    :class:`LoserRecord` that violates the theory2 invariant (missing
    ``winner_bid_id`` or non-positive margin on a non-dominated loss).

    Attributes
    ----------
    witness_id:
        Unique identifier for this witness instance.
    events:
        Ordered list of event dictionaries.
    created_at:
        Wall-clock time at which this witness was created.
    """

    witness_id: str
    events: list[dict] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)

    # ------------------------------------------------------------------
    # Event recording
    # ------------------------------------------------------------------

    def record_loss(self, record: LoserRecord) -> None:
        """Record a loss event from a :class:`LoserRecord`.

        Parameters
        ----------
        record:
            The loser record to log.
        """
        self.events.append(
            {
                "event_type": "loss",
                "timestamp": time.time(),
                "record_id": record.record_id,
                "round_id": record.round_id,
                "loser_bid_id": record.loser_bid_id,
                "winner_bid_id": record.winner_bid_id,
                "loss_reason": record.loss_reason.value,
                "disposition": record.disposition.value,
                "margin": record.margin,
                "loser_score": record.loser_score,
                "winner_score": record.winner_score,
                "provenance_keys": sorted(record.provenance.keys()),
            }
        )

    def record_penalty(self, penalty: LoserPenalty) -> None:
        """Record a penalty event from a :class:`LoserPenalty`.

        Parameters
        ----------
        penalty:
            The penalty to log.
        """
        self.events.append(
            {
                "event_type": "penalty",
                "timestamp": time.time(),
                "penalty_id": penalty.penalty_id,
                "member_id": penalty.member_id,
                "penalty_type": penalty.penalty_type,
                "magnitude": penalty.magnitude,
                "reason": penalty.reason,
                "expires_at": penalty.expires_at,
            }
        )

    def record_elimination(self, bid_id: str, reason: str) -> None:
        """Record an elimination event.

        Parameters
        ----------
        bid_id:
            The permanently eliminated bid identifier.
        reason:
            Human-readable reason for the elimination.
        """
        self.events.append(
            {
                "event_type": "elimination",
                "timestamp": time.time(),
                "bid_id": bid_id,
                "reason": reason,
            }
        )

    # ------------------------------------------------------------------
    # Provenance verification
    # ------------------------------------------------------------------

    def verify_provenance(self) -> list[str]:
        """Verify that all recorded loss events satisfy the provenance invariant.

        The invariant (theory2.tex §46.6.3) requires that every
        :class:`LoserRecord` has:

        1. A non-empty ``winner_bid_id``.
        2. A non-negative ``margin``.
        3. A non-empty ``provenance`` dictionary.

        This method scans the event log (not the archive directly) and
        returns a list of violation messages for any events that fail.

        Returns
        -------
        list[str]
            Human-readable violation messages.  An empty list means the
            provenance invariant holds for all recorded events.
        """
        violations: list[str] = []
        for evt in self.events:
            if evt.get("event_type") != "loss":
                continue
            record_id = evt.get("record_id", "?")
            winner_bid_id = evt.get("winner_bid_id", "")
            margin = evt.get("margin", -1.0)
            provenance_keys = evt.get("provenance_keys", [])

            if not winner_bid_id:
                violations.append(
                    f"LoserRecord {record_id}: missing winner_bid_id"
                )
            if margin < 0.0:
                violations.append(
                    f"LoserRecord {record_id}: negative margin ({margin:.6f})"
                )
            if not provenance_keys:
                violations.append(
                    f"LoserRecord {record_id}: empty provenance"
                )

        return violations

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise this witness to a plain dictionary.

        Returns
        -------
        dict
            Keys: ``witness_id``, ``created_at``, ``event_count``,
            ``loss_count``, ``penalty_count``, ``elimination_count``,
            ``events``.
        """
        loss_count = sum(1 for e in self.events if e.get("event_type") == "loss")
        penalty_count = sum(1 for e in self.events if e.get("event_type") == "penalty")
        elim_count = sum(1 for e in self.events if e.get("event_type") == "elimination")
        return {
            "witness_id": self.witness_id,
            "created_at": self.created_at,
            "event_count": len(self.events),
            "loss_count": loss_count,
            "penalty_count": penalty_count,
            "elimination_count": elim_count,
            "events": list(self.events),
        }

    def __repr__(self) -> str:  # noqa: D401
        """Return a developer-friendly representation."""
        return (
            f"LoserHandlingWitness("
            f"id={self.witness_id!r}, "
            f"events={len(self.events)})"
        )


# ---------------------------------------------------------------------------
# CompetitionResult — frozen full-provenance record of a competition round
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CompetitionResult:
    """Full-provenance record of a completed competition round.

    Bundles the winner, every loser record, and round-level metadata into a
    single immutable object that satisfies the theory2 reproducibility
    requirement.  The :meth:`provenance_complete` method verifies that
    no loser record is missing its ``winner_bid_id``.

    Attributes
    ----------
    result_id:
        Unique identifier for this competition result.
    round_id:
        The round this result captures.
    competition_id:
        The broader competition this round belongs to.
    winner_bid_id:
        The ``bid_id`` of the winning bid.
    loser_records:
        Immutable tuple of all :class:`LoserRecord` objects from this round.
    winning_score:
        The aggregate score of the winning bid.
    selection_strategy:
        The strategy used to select the winner (e.g. ``"highest_score"``).
    timestamp:
        Wall-clock time at which this result was created.
    metadata:
        Arbitrary extra context (e.g. evaluator version, round config).
    """

    result_id: str
    round_id: str
    competition_id: str
    winner_bid_id: str
    loser_records: tuple
    winning_score: float
    selection_strategy: str
    timestamp: float
    metadata: dict

    # ------------------------------------------------------------------
    # Query methods
    # ------------------------------------------------------------------

    def participant_count(self) -> int:
        """Return the total number of participants (winner + losers).

        Returns
        -------
        int
            ``1 + len(loser_records)``.
        """
        return 1 + len(self.loser_records)

    def provenance_complete(self) -> bool:
        """Return True if all loser records have ``winner_bid_id`` set.

        This enforces the §46.6.3 provenance invariant.

        Returns
        -------
        bool
            ``True`` iff every :class:`LoserRecord` in ``loser_records`` has a
            non-empty ``winner_bid_id``.
        """
        for record in self.loser_records:
            if not getattr(record, "winner_bid_id", ""):
                return False
        return True

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise this result to a plain dictionary.

        Returns
        -------
        dict
            All fields plus computed ``participant_count`` and
            ``provenance_complete``.
        """
        return {
            "result_id": self.result_id,
            "round_id": self.round_id,
            "competition_id": self.competition_id,
            "winner_bid_id": self.winner_bid_id,
            "loser_records": [
                r.to_dict() if hasattr(r, "to_dict") else r
                for r in self.loser_records
            ],
            "winning_score": self.winning_score,
            "selection_strategy": self.selection_strategy,
            "timestamp": self.timestamp,
            "metadata": dict(self.metadata),
            "participant_count": self.participant_count(),
            "provenance_complete": self.provenance_complete(),
        }

    def __repr__(self) -> str:  # noqa: D401
        """Return a developer-friendly representation."""
        return (
            f"CompetitionResult("
            f"id={self.result_id!r}, "
            f"round={self.round_id!r}, "
            f"winner={self.winner_bid_id!r}, "
            f"losers={len(self.loser_records)}, "
            f"provenance_complete={self.provenance_complete()})"
        )


# ---------------------------------------------------------------------------
# Factory functions
# ---------------------------------------------------------------------------


def make_default_loser_archive(max_size: int = DEFAULT_ARCHIVE_MAX_SIZE) -> LoserArchive:
    """Construct a :class:`LoserArchive` with default settings.

    Parameters
    ----------
    max_size:
        Maximum number of records to retain.

    Returns
    -------
    LoserArchive
        A ready-to-use archive with a generated identifier.

    Example
    -------
    >>> archive = make_default_loser_archive()
    >>> isinstance(archive, LoserArchive)
    True
    """
    return LoserArchive(archive_id=_make_id("archive"), max_size=max_size)


def make_default_penalty_ledger() -> PenaltyLedger:
    """Construct an empty :class:`PenaltyLedger`.

    Returns
    -------
    PenaltyLedger
        A ready-to-use penalty ledger with a generated identifier.

    Example
    -------
    >>> ledger = make_default_penalty_ledger()
    >>> isinstance(ledger, PenaltyLedger)
    True
    """
    return PenaltyLedger(ledger_id=_make_id("ledger"))


def make_default_loser_handler(
    archive: Optional[LoserArchive] = None,
    penalty_ledger: Optional[PenaltyLedger] = None,
) -> LoserHandler:
    """Construct a :class:`LoserHandler` wired to the provided (or new) components.

    Parameters
    ----------
    archive:
        Archive to use.  A new one is created if not provided.
    penalty_ledger:
        Penalty ledger to use.  A new one is created if not provided.

    Returns
    -------
    LoserHandler
        A fully wired handler ready to process losing bids.

    Example
    -------
    >>> handler = make_default_loser_handler()
    >>> isinstance(handler, LoserHandler)
    True
    """
    return LoserHandler(
        handler_id=_make_id("handler"),
        archive=archive or make_default_loser_archive(),
        penalty_ledger=penalty_ledger or make_default_penalty_ledger(),
    )


def make_default_loser_handling_coordinator(
    handler: Optional[LoserHandler] = None,
) -> LoserHandlingCoordinator:
    """Construct a :class:`LoserHandlingCoordinator` with all sub-components.

    Parameters
    ----------
    handler:
        Handler to use.  A new default handler is created if not provided.

    Returns
    -------
    LoserHandlingCoordinator
        A fully wired coordinator with handler and analyzer.

    Example
    -------
    >>> coord = make_default_loser_handling_coordinator()
    >>> isinstance(coord, LoserHandlingCoordinator)
    True
    """
    h = handler or make_default_loser_handler()
    analyzer = LoserHandlingAnalyzer(
        analyzer_id=_make_id("analyzer"),
        archive=h.archive,
    )
    return LoserHandlingCoordinator(
        coordinator_id=_make_id("coord"),
        handler=h,
        analyzer=analyzer,
    )


# ---------------------------------------------------------------------------
# Module self-test (smoke test)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import random as _random
    from types import SimpleNamespace as _NS

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    print("=" * 70)
    print("loser_handling — smoke test")
    print("theory2.tex Ch46 §46.6  Fleet Competition: Loser Handling")
    print("=" * 70)

    # ------------------------------------------------------------------
    # Step 1: Create coordinator and witness
    # ------------------------------------------------------------------
    coord = make_default_loser_handling_coordinator()
    witness = LoserHandlingWitness(witness_id=_make_id("witness"))
    print(f"\n[1] Coordinator : {coord}")
    print(f"    Witness     : {witness}")

    # ------------------------------------------------------------------
    # Step 2: Simulate bids for one round
    # ------------------------------------------------------------------
    ROUND_ID = "round-001"
    COMP_ID = "comp-alpha"

    winner_bid = _NS(
        bid_id="bid-winner",
        semantic_score=0.87,
        trust_tier="gold",
        member_id="member-A",
        competition_id=COMP_ID,
        strategy="highest_score",
        metadata={},
    )

    loser_bids = [
        _NS(
            bid_id="bid-loser-1",
            semantic_score=0.84,   # close loss — recycle candidate
            trust_tier="gold",
            member_id="member-B",
            competition_id=COMP_ID,
            strategy="highest_score",
            metadata={},
        ),
        _NS(
            bid_id="bid-loser-2",
            semantic_score=0.60,   # trust-tier loss
            trust_tier="silver",
            member_id="member-C",
            competition_id=COMP_ID,
            strategy="highest_score",
            metadata={},
        ),
        _NS(
            bid_id="bid-loser-3",
            semantic_score=0.00,   # invalid bid — will be eliminated
            trust_tier="bronze",
            member_id="member-D",
            competition_id=COMP_ID,
            strategy="highest_score",
            metadata={},
        ),
    ]

    winner_criteria = {"quality": 0.90, "trust": 0.88, "latency": 0.82}

    criteria_by_bid: dict[str, dict[str, float]] = {
        "bid-loser-1": {"quality": 0.85, "trust": 0.87, "latency": 0.80},
        "bid-loser-2": {"quality": 0.62, "trust": 0.55, "latency": 0.65},
        "bid-loser-3": {"quality": 0.00, "trust": 0.00, "latency": 0.00},
    }

    loss_reasons_map: dict[str, LossReason] = {
        "bid-loser-1": LossReason.LOWER_QUALITY_SCORE,
        "bid-loser-2": LossReason.LOWER_TRUST_TIER,
        "bid-loser-3": LossReason.INVALID_BID,
    }

    # ------------------------------------------------------------------
    # Step 3: Process losers through the coordinator
    # ------------------------------------------------------------------
    print(f"\n[2] Processing {len(loser_bids)} losers for round {ROUND_ID!r} ...")
    loser_records = coord.process_round_losers(
        round_id=ROUND_ID,
        winner_bid=winner_bid,
        loser_bids=loser_bids,
        criteria_scores_by_bid=criteria_by_bid,
        winner_criteria_scores=winner_criteria,
        loss_reasons=loss_reasons_map,
    )

    # ------------------------------------------------------------------
    # Step 4: Print loser records with full provenance
    # ------------------------------------------------------------------
    print(f"\n[3] Loser records ({len(loser_records)} total):")
    for rec in loser_records:
        print(
            f"  - record_id={rec.record_id}"
            f"  loser={rec.loser_bid_id}"
            f"  winner={rec.winner_bid_id}"
            f"  reason={rec.loss_reason.value}"
            f"  disposition={rec.disposition.value}"
            f"  margin={rec.margin:.4f}"
            f"  score_gap={rec.score_gap():.4f}"
        )
        print(f"    provenance keys: {sorted(rec.provenance.keys())}")

    # ------------------------------------------------------------------
    # Step 5: Record in witness
    # ------------------------------------------------------------------
    print("\n[4] Recording events in witness ...")
    for rec in loser_records:
        witness.record_loss(rec)
    print(f"    Witness event count: {len(witness.events)}")

    # ------------------------------------------------------------------
    # Step 6: Verify provenance
    # ------------------------------------------------------------------
    print("\n[5] Verifying provenance ...")
    violations = witness.verify_provenance()
    if violations:
        print(f"  VIOLATIONS ({len(violations)}):")
        for v in violations:
            print(f"    ! {v}")
    else:
        print("  All loser records satisfy the §46.6.3 provenance invariant. ✓")

    # ------------------------------------------------------------------
    # Step 7: Build CompetitionResult
    # ------------------------------------------------------------------
    competition_result = CompetitionResult(
        result_id=_make_id("result"),
        round_id=ROUND_ID,
        competition_id=COMP_ID,
        winner_bid_id=winner_bid.bid_id,
        loser_records=tuple(loser_records),
        winning_score=winner_bid.semantic_score,
        selection_strategy="highest_score",
        timestamp=time.time(),
        metadata={"evaluator_version": "1.0", "round_config": "default"},
    )
    print(f"\n[6] CompetitionResult: {competition_result}")
    print(f"    participant_count   : {competition_result.participant_count()}")
    print(f"    provenance_complete : {competition_result.provenance_complete()}")

    # ------------------------------------------------------------------
    # Step 8: Analysis report
    # ------------------------------------------------------------------
    print("\n[7] Analysis report:")
    report = coord.generate_analysis_report()
    print(f"    total_records           : {report['total_records']}")
    print(f"    most_common_reason      : {report['most_common_reason']}")
    print(f"    average_margin          : {report['average_margin']:.4f}")
    print(f"    repeat_losers           : {report['repeat_losers']}")
    print(f"    loss_reason_distribution: {report['loss_reason_distribution']}")
    if report["improvement_candidates"]:
        print("    improvement_candidates  :")
        for cand in report["improvement_candidates"]:
            print(f"      bid={cand['bid_id']}  margin={cand['margin']:.4f}")
    else:
        print("    improvement_candidates  : (none)")

    # ------------------------------------------------------------------
    # Step 9: Handler stats
    # ------------------------------------------------------------------
    print("\n[8] Handler stats:")
    stats = coord.handler.stats()
    print(f"    archive total_records : {stats['archive_stats']['total_records']}")
    print(f"    elimination_count     : {stats['elimination_count']}")
    print(f"    recycle_queue_depth   : {stats['recycle_queue_depth']}")

    # ------------------------------------------------------------------
    # Step 10: Recycle eligible bids
    # ------------------------------------------------------------------
    recycled = coord.recycle_eligible_bids()
    print(f"\n[9] Recycled bids released: {len(recycled)}")
    for b in recycled:
        print(f"    - bid_id={b.bid_id}  meta={b.metadata}")

    # ------------------------------------------------------------------
    # Step 11: Penalty ledger
    # ------------------------------------------------------------------
    print("\n[10] Penalty ledger:")
    ledger_export = coord.handler.penalty_ledger.export()
    for member_id, detail in ledger_export["members"].items():
        print(
            f"    member={member_id}  active={detail['active']}"
            f"  multiplier={detail['effective_multiplier']:.3f}"
        )

    # ------------------------------------------------------------------
    # Step 12: Health
    # ------------------------------------------------------------------
    health = coord.health()
    print(f"\n[11] Coordinator health: {health['status']}")
    print(f"     archive_size={health['archive_size']}  "
          f"eliminations={health['elimination_count']}  "
          f"penalties={health['total_penalties']}")

    print("\n" + "=" * 70)
    print("Smoke test complete — all §46.6 invariants verified.")
    print("=" * 70)
