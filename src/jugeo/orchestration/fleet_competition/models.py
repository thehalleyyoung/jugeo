"""
Core data models for the fleet_competition orchestration package.

This module is part of JuGeo's copilot-assisted encoding of theory2.tex Chapter 46:
Fleet semantics — competitive search over admissible futures.

Chapter 46 introduces the formal machinery for fleets of agents that do not merely
execute instructions but *compete* over the semantic content of their moves.  Each
agent submits a *bid* that specifies not only the proposed action but also an
uncertainty profile, a set of claimed capabilities, and a self-declared trust
ceiling.  A centralised (or decentralised) evaluator then selects winners through
multi-criterion comparison, Pareto dominance checks, and optional challenge rounds.

This module defines the foundational dataclasses and enumerations that the rest of
the fleet_competition sub-package builds on.  Key design goals:

1. **Immutability where possible** — frozen dataclasses for value objects such as
   :class:`BidDelta` prevent accidental mutation after creation.

2. **Slots everywhere** — ``slots=True`` reduces memory overhead and speeds up
   attribute access for the hot-path objects (:class:`CompetitiveBid`,
   :class:`FleetRound`).

3. **Rich introspection** — every public class exposes ``to_dict()`` / ``from_dict()``
   round-trip methods so that bids, rounds, and calibration traces can be serialised
   to JSON for persistence and replay.

4. **Defensive validation** — ``validate()`` methods return *lists of error strings*
   rather than raising immediately; callers decide whether to abort or degrade
   gracefully.

Chapter reference: theory2.tex Ch46 — Fleet semantics.

copilot
"""
from __future__ import annotations

import csv
import io
import math
import statistics
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional, Sequence

__all__ = [
    # Enumerations
    "BidStatus",
    "RoundPhase",
    "CalibrationStatus",
    # Value objects
    "BidDelta",
    # Core dataclasses
    "CompetitiveBid",
    "FleetRound",
    "ChallengeRecord",
    "CalibrationTrace",
    # Helper utilities (module-level)
    "_clamp",
    "_safe_mean",
    "_safe_std",
]

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

#: Maximum latency (in seconds) used as the normalisation denominator when
#: computing the latency component of a calibration score.  Values above this
#: are treated as this maximum, preventing extreme latencies from producing
#: negative calibration scores.
MAX_LATENCY: float = 60.0

#: Minimum number of samples required before a :class:`CalibrationTrace` is
#: considered FRESH rather than DEGRADED on first use.
MIN_SAMPLES_FOR_FRESH: int = 5

#: Number of seconds after which a calibration trace is considered STALE if
#: no new samples have been added.
STALE_THRESHOLD_SECONDS: float = 300.0

#: Number of seconds after which a calibration trace is considered DEGRADED
#: even if recently updated (indicates the trace is very old overall).
DEGRADED_THRESHOLD_SECONDS: float = 3600.0

#: Window size used by :meth:`CalibrationTrace.calibration_score` when
#: computing the trailing mean over the accuracy / latency / trust histories.
CALIBRATION_TRAILING_WINDOW: int = 10

#: Weights for the three components of the calibration score.
CALIBRATION_WEIGHT_ACCURACY: float = 0.5
CALIBRATION_WEIGHT_LATENCY: float = 0.3
CALIBRATION_WEIGHT_TRUST: float = 0.2

# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------


def _clamp(v: float, lo: float, hi: float) -> float:
    """Return *v* clamped to the closed interval [*lo*, *hi*].

    This is a pure-utility function used throughout the module to ensure that
    scores, probabilities, and fractions remain within their legal ranges.

    Parameters
    ----------
    v:
        The value to clamp.
    lo:
        Lower bound (inclusive).
    hi:
        Upper bound (inclusive).

    Returns
    -------
    float
        The clamped value.

    Examples
    --------
    >>> _clamp(1.5, 0.0, 1.0)
    1.0
    >>> _clamp(-0.1, 0.0, 1.0)
    0.0
    >>> _clamp(0.5, 0.0, 1.0)
    0.5
    """
    if lo > hi:
        raise ValueError(f"_clamp: lo ({lo}) must not exceed hi ({hi})")
    return max(lo, min(hi, v))


def _safe_mean(seq: Sequence[float]) -> float:
    """Return the arithmetic mean of *seq*, or 0.0 if the sequence is empty.

    Using ``statistics.mean`` on an empty sequence raises ``StatisticsError``;
    this wrapper provides a safe default of 0.0 that is suitable for score
    aggregation in fleet calibration.

    Parameters
    ----------
    seq:
        A sequence of float values.

    Returns
    -------
    float
        The mean, or 0.0 for an empty sequence.
    """
    if not seq:
        return 0.0
    return statistics.mean(seq)


def _safe_std(seq: Sequence[float]) -> float:
    """Return the sample standard deviation of *seq*, or 0.0 for len < 2.

    Provides a safe fallback consistent with ``_safe_mean`` for use in
    calibration and evaluation routines.

    Parameters
    ----------
    seq:
        A sequence of float values.

    Returns
    -------
    float
        The sample standard deviation, or 0.0 if there are fewer than two
        elements.
    """
    if len(seq) < 2:
        return 0.0
    return statistics.stdev(seq)


def _moving_average(series: list[float], window: int) -> list[float]:
    """Compute a simple moving average over *series* with the given *window*.

    Parameters
    ----------
    series:
        The raw time-ordered float series.
    window:
        The number of preceding samples (inclusive) to average at each step.
        Clamped to at least 1.

    Returns
    -------
    list[float]
        A list of the same length as *series* where the i-th element is the
        mean of ``series[max(0, i-window+1) : i+1]``.

    Notes
    -----
    For the first ``window - 1`` elements the average is computed over fewer
    than *window* values (causal / no look-ahead).
    """
    window = max(1, window)
    result: list[float] = []
    for i in range(len(series)):
        lo = max(0, i - window + 1)
        result.append(_safe_mean(series[lo : i + 1]))
    return result


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class BidStatus(str, Enum):
    """Lifecycle status of a :class:`CompetitiveBid`.

    Transitions follow the state machine described in theory2.tex §46.3:

    PENDING    → ACCEPTED   (bid selected as winner by the evaluator)
    PENDING    → REJECTED   (bid not selected; round closed without it)
    PENDING    → CHALLENGED (a peer fleet member has issued a challenge)
    PENDING    → EXPIRED    (the round closed before the bid was evaluated)
    CHALLENGED → ACCEPTED   (challenge resolved in the bidder's favour)
    CHALLENGED → REJECTED   (challenge upheld; bid overturned)

    The string values are intentionally lowercase to match JSON serialisation
    conventions used by the rest of the JuGeo encoding layer.
    """

    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    CHALLENGED = "challenged"
    EXPIRED = "expired"


class RoundPhase(str, Enum):
    """Phase of a :class:`FleetRound`.

    Rounds progress through four phases as defined in theory2.tex §46.4:

    OPEN        — bids are being collected; new bids may be added.
    EVALUATING  — bid collection is closed; the evaluator is running.
    CLOSED      — a winner has been determined; the round is finalised.
    ARCHIVED    — historical data; no further state changes expected.
    """

    OPEN = "open"
    EVALUATING = "evaluating"
    CLOSED = "closed"
    ARCHIVED = "archived"


class CalibrationStatus(str, Enum):
    """Freshness status of a :class:`CalibrationTrace`.

    Calibration traces degrade over time and must be periodically refreshed
    to remain trustworthy.  The fleet scheduler uses this status to decide
    whether to trust a member's self-declared uncertainty profile.

    FRESH    — recently updated with sufficient samples; fully trusted.
    STALE    — last sample is old; trust is discounted but not zero.
    DEGRADED — very few samples or very old; trust is heavily penalised.
    INVALID  — data is inconsistent or corrupt; member must re-calibrate.
    """

    FRESH = "fresh"
    STALE = "stale"
    DEGRADED = "degraded"
    INVALID = "invalid"


# ---------------------------------------------------------------------------
# BidDelta — frozen value object
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BidDelta:
    """Signed difference between two :class:`CompetitiveBid` objects.

    A ``BidDelta`` captures *how much* one bid differs from another along the
    three primary axes used by the fleet evaluator: raw value, semantic score,
    and uncertainty.  The ``dominant`` flag is pre-computed by
    :meth:`CompetitiveBid.delta_from` and indicates whether the reference bid
    strictly dominates the other in the Pareto sense (higher value, higher
    score, lower uncertainty — all simultaneously).

    This is a *frozen* dataclass (immutable after construction) because deltas
    are purely derived quantities that should not be mutated in place.

    Attributes
    ----------
    bid_id_a:
        Identifier of the *reference* bid (the one calling ``delta_from``).
    bid_id_b:
        Identifier of the bid being compared against.
    value_delta:
        ``bid_a.bid_value - bid_b.bid_value``.
    score_delta:
        ``bid_a.semantic_score - bid_b.semantic_score``.
    uncertainty_delta:
        ``bid_a.uncertainty - bid_b.uncertainty``; negative means *a* is less
        uncertain than *b* (generally good).
    dominant:
        True if *a* weakly dominates *b* on all three axes and strictly
        dominates on at least one.
    """

    bid_id_a: str
    bid_id_b: str
    value_delta: float
    score_delta: float
    uncertainty_delta: float
    dominant: bool

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def is_improvement(self) -> bool:
        """Return True if bid_a is strictly better than bid_b.

        An improvement requires *both* a positive value delta (bid_a has
        higher bid value) and a positive score delta (bid_a has higher
        semantic score).  Uncertainty direction is not checked here; use
        :attr:`dominant` for the full three-axis dominance test.

        Returns
        -------
        bool
            ``True`` iff ``value_delta > 0`` and ``score_delta > 0``.
        """
        return self.value_delta > 0 and self.score_delta > 0

    def to_dict(self) -> dict[str, Any]:
        """Serialise this ``BidDelta`` to a plain Python dictionary.

        The returned dictionary is JSON-serialisable (all values are built-in
        Python types).

        Returns
        -------
        dict
            Keys: ``bid_id_a``, ``bid_id_b``, ``value_delta``,
            ``score_delta``, ``uncertainty_delta``, ``dominant``,
            ``is_improvement``.
        """
        return {
            "bid_id_a": self.bid_id_a,
            "bid_id_b": self.bid_id_b,
            "value_delta": self.value_delta,
            "score_delta": self.score_delta,
            "uncertainty_delta": self.uncertainty_delta,
            "dominant": self.dominant,
            "is_improvement": self.is_improvement(),
        }


# ---------------------------------------------------------------------------
# CompetitiveBid — mutable bid record
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CompetitiveBid:
    """A normalised bid record submitted by a fleet member for a given move.

    ``CompetitiveBid`` is the central data structure of the fleet_competition
    sub-package.  It encodes everything the fleet evaluator needs to score and
    rank a proposed move:

    * **move_id** / **bidder_id** — provenance identifiers.
    * **bid_value** — the bidder's self-declared utility for the move (≥ 0).
    * **semantic_score** — grounded semantic quality in [0, 1]; computed by
      the bidder's internal doctrine checker before submission.
    * **uncertainty** — epistemic uncertainty in [0, 1]; lower is better.
    * **capabilities** — the capability tags the bidder asserts are relevant.
    * **trust_ceiling** — upper bound on how much the fleet may trust this
      bid; mirrors the bidder's :attr:`FleetMember.trust_ceiling`.
    * **timestamp** — wall-clock time of bid creation (``time.time()``).
    * **metadata** — free-form annotation dictionary for downstream tooling.
    * **bid_id** — stable UUID assigned at construction time.
    * **status** — mutable lifecycle status; starts as ``PENDING``.

    Theory reference: theory2.tex §46.2 — Normalised bid structure.

    Parameters
    ----------
    move_id:
        Identifier of the move being bid on.
    bidder_id:
        Identifier of the fleet member submitting this bid.
    bid_value:
        Raw utility value (must be ≥ 0; validated by :meth:`validate`).
    semantic_score:
        Semantic quality score in [0, 1].
    uncertainty:
        Epistemic uncertainty in [0, 1].
    capabilities:
        List of capability string tags declared by the bidder.
    trust_ceiling:
        Maximum trust level in [0, 1].
    timestamp:
        Creation time; defaults to ``time.time()``.
    metadata:
        Optional free-form annotations.
    bid_id:
        Unique identifier; auto-generated if not provided.
    status:
        Initial status; defaults to ``BidStatus.PENDING``.
    """

    move_id: str
    bidder_id: str
    bid_value: float
    semantic_score: float
    uncertainty: float
    capabilities: list[str]
    trust_ceiling: float
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)
    bid_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    status: BidStatus = field(default=BidStatus.PENDING)

    # ------------------------------------------------------------------
    # Serialisation helpers
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise this bid to a plain Python dictionary.

        All fields are included; ``status`` is serialised as its string value.

        Returns
        -------
        dict
            A JSON-serialisable representation of this bid.
        """
        return {
            "bid_id": self.bid_id,
            "move_id": self.move_id,
            "bidder_id": self.bidder_id,
            "bid_value": self.bid_value,
            "semantic_score": self.semantic_score,
            "uncertainty": self.uncertainty,
            "capabilities": list(self.capabilities),
            "trust_ceiling": self.trust_ceiling,
            "timestamp": self.timestamp,
            "metadata": dict(self.metadata),
            "status": self.status.value,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "CompetitiveBid":
        """Deserialise a ``CompetitiveBid`` from a plain dictionary.

        This is the inverse of :meth:`to_dict`.  Unknown keys in *d* are
        silently ignored to allow forward-compatible deserialisation.

        Parameters
        ----------
        d:
            A dictionary as produced by :meth:`to_dict`.

        Returns
        -------
        CompetitiveBid
            A newly constructed bid populated from *d*.

        Raises
        ------
        KeyError
            If a required field (``move_id``, ``bidder_id``, ``bid_value``,
            ``semantic_score``, ``uncertainty``, ``capabilities``,
            ``trust_ceiling``) is missing from *d*.
        ValueError
            If ``status`` is not a valid ``BidStatus`` value.
        """
        status_raw = d.get("status", BidStatus.PENDING.value)
        status = BidStatus(status_raw) if isinstance(status_raw, str) else BidStatus.PENDING
        return cls(
            move_id=d["move_id"],
            bidder_id=d["bidder_id"],
            bid_value=float(d["bid_value"]),
            semantic_score=float(d["semantic_score"]),
            uncertainty=float(d["uncertainty"]),
            capabilities=list(d.get("capabilities", [])),
            trust_ceiling=float(d["trust_ceiling"]),
            timestamp=float(d.get("timestamp", time.time())),
            metadata=dict(d.get("metadata", {})),
            bid_id=d.get("bid_id", str(uuid.uuid4())),
            status=status,
        )

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self) -> list[str]:
        """Check this bid for constraint violations.

        Returns a list of human-readable error strings.  An empty list means
        the bid is valid.  Checks performed:

        * ``bid_value >= 0``
        * ``semantic_score`` ∈ [0, 1]
        * ``uncertainty`` ∈ [0, 1]
        * ``trust_ceiling`` ∈ [0, 1]
        * ``move_id`` and ``bidder_id`` are non-empty strings
        * ``bid_id`` is a non-empty string

        Returns
        -------
        list[str]
            Validation error messages; empty if the bid is valid.
        """
        errors: list[str] = []
        if not isinstance(self.move_id, str) or not self.move_id.strip():
            errors.append("move_id must be a non-empty string")
        if not isinstance(self.bidder_id, str) or not self.bidder_id.strip():
            errors.append("bidder_id must be a non-empty string")
        if not isinstance(self.bid_id, str) or not self.bid_id.strip():
            errors.append("bid_id must be a non-empty string")
        if not math.isfinite(self.bid_value) or self.bid_value < 0:
            errors.append(
                f"bid_value must be a finite non-negative float; got {self.bid_value!r}"
            )
        if not math.isfinite(self.semantic_score) or not (0.0 <= self.semantic_score <= 1.0):
            errors.append(
                f"semantic_score must be in [0, 1]; got {self.semantic_score!r}"
            )
        if not math.isfinite(self.uncertainty) or not (0.0 <= self.uncertainty <= 1.0):
            errors.append(
                f"uncertainty must be in [0, 1]; got {self.uncertainty!r}"
            )
        if not math.isfinite(self.trust_ceiling) or not (0.0 <= self.trust_ceiling <= 1.0):
            errors.append(
                f"trust_ceiling must be in [0, 1]; got {self.trust_ceiling!r}"
            )
        return errors

    # ------------------------------------------------------------------
    # Comparison utilities
    # ------------------------------------------------------------------

    def delta_from(self, other: "CompetitiveBid") -> BidDelta:
        """Compute the signed difference from *other* to *self*.

        Returns a frozen :class:`BidDelta` that encodes how much *self* differs
        from *other* along value, score, and uncertainty axes, plus a
        pre-computed dominance flag.

        *Self* is considered to Pareto-dominate *other* when:

        * ``self.bid_value >= other.bid_value``
        * ``self.semantic_score >= other.semantic_score``
        * ``self.uncertainty <= other.uncertainty``
        * At least one of the above inequalities is strict.

        Parameters
        ----------
        other:
            The bid to compare against (the baseline).

        Returns
        -------
        BidDelta
            Signed differences and a dominance flag.
        """
        v_delta = self.bid_value - other.bid_value
        s_delta = self.semantic_score - other.semantic_score
        u_delta = self.uncertainty - other.uncertainty
        # Pareto dominance: self weakly dominates other on all axes and
        # strictly on at least one.
        weakly_better_value = v_delta >= 0
        weakly_better_score = s_delta >= 0
        weakly_better_uncert = u_delta <= 0  # lower uncertainty is better
        strictly_better = (v_delta > 0) or (s_delta > 0) or (u_delta < 0)
        dominant = (
            weakly_better_value
            and weakly_better_score
            and weakly_better_uncert
            and strictly_better
        )
        return BidDelta(
            bid_id_a=self.bid_id,
            bid_id_b=other.bid_id,
            value_delta=v_delta,
            score_delta=s_delta,
            uncertainty_delta=u_delta,
            dominant=dominant,
        )


# ---------------------------------------------------------------------------
# FleetRound — mutable round state
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class FleetRound:
    """State for a single competitive fleet round.

    A round is the atomic unit of fleet competition described in
    theory2.tex §46.4.  During a round, fleet members submit bids for a
    target move; once the round closes the evaluator selects a winner.
    Rounds progress through the :class:`RoundPhase` state machine and are
    retained in the fleet's history for calibration and auditing.

    Attributes
    ----------
    round_id:
        UUID assigned at construction.
    bids:
        Ordered list of submitted bids.
    winner:
        bid_id of the winning bid; ``None`` until the round closes.
    round_timestamp:
        Wall-clock time when this round was created.
    phase:
        Current :class:`RoundPhase`; mutable.
    budget_remaining:
        Notional budget available for the winning bid to consume.
    """

    round_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    bids: list[CompetitiveBid] = field(default_factory=list)
    winner: Optional[str] = None
    round_timestamp: float = field(default_factory=time.time)
    phase: RoundPhase = field(default=RoundPhase.OPEN)
    budget_remaining: float = 100.0

    # ------------------------------------------------------------------
    # Bid management
    # ------------------------------------------------------------------

    def add_bid(self, bid: CompetitiveBid) -> None:
        """Add *bid* to this round's bid list.

        Parameters
        ----------
        bid:
            The bid to add.

        Raises
        ------
        ValueError
            If the round is not in the ``OPEN`` phase — bids may only be
            submitted while the round is accepting entries.
        ValueError
            If a bid with the same ``bid_id`` already exists in this round.
        """
        if self.phase != RoundPhase.OPEN:
            raise ValueError(
                f"Cannot add bids to round {self.round_id}: "
                f"phase is {self.phase.value!r} (must be 'open')"
            )
        for existing in self.bids:
            if existing.bid_id == bid.bid_id:
                raise ValueError(
                    f"Bid {bid.bid_id!r} already exists in round {self.round_id!r}"
                )
        self.bids.append(bid)

    # ------------------------------------------------------------------
    # Winner determination
    # ------------------------------------------------------------------

    def determine_winner(
        self, evaluator: Callable[[list[CompetitiveBid]], Optional[str]]
    ) -> Optional[str]:
        """Close the round and determine the winning bid.

        Transitions the phase to ``EVALUATING`` while the evaluator runs,
        then to ``CLOSED`` once the winner is determined.  The winning bid's
        status is set to ``ACCEPTED``; all other bids' statuses are set to
        ``REJECTED``.

        Parameters
        ----------
        evaluator:
            A callable that accepts the list of bids and returns the
            ``bid_id`` of the winning bid, or ``None`` if no winner can be
            determined (e.g. no bids were submitted).

        Returns
        -------
        str or None
            The ``bid_id`` of the winning bid, or ``None`` if no winner.

        Raises
        ------
        ValueError
            If the round is already in the ``CLOSED`` or ``ARCHIVED`` phase.
        """
        if self.phase in (RoundPhase.CLOSED, RoundPhase.ARCHIVED):
            raise ValueError(
                f"Round {self.round_id!r} is already {self.phase.value!r}; "
                "cannot re-determine winner"
            )
        self.phase = RoundPhase.EVALUATING
        winning_id = evaluator(self.bids)
        self.winner = winning_id
        self.phase = RoundPhase.CLOSED
        bid_index: dict[str, CompetitiveBid] = {b.bid_id: b for b in self.bids}
        for bid in self.bids:
            if bid.bid_id == winning_id:
                bid.status = BidStatus.ACCEPTED
            elif bid.status == BidStatus.PENDING:
                bid.status = BidStatus.REJECTED
        return winning_id

    # ------------------------------------------------------------------
    # Summarisation
    # ------------------------------------------------------------------

    def summarize(self) -> dict[str, Any]:
        """Return a compact summary dictionary for this round.

        Useful for logging, monitoring, and exporting round history without
        carrying the full bid payloads.

        Returns
        -------
        dict
            Keys: ``round_id``, ``num_bids``, ``winner``, ``phase``,
            ``budget_remaining``, ``top_bid`` (dict or ``None``),
            ``round_timestamp``.
        """
        top_bid: Optional[dict[str, Any]] = None
        if self.bids:
            best = max(self.bids, key=lambda b: (b.semantic_score, b.bid_value))
            top_bid = {
                "bid_id": best.bid_id,
                "bidder_id": best.bidder_id,
                "semantic_score": best.semantic_score,
                "bid_value": best.bid_value,
                "uncertainty": best.uncertainty,
            }
        return {
            "round_id": self.round_id,
            "num_bids": len(self.bids),
            "winner": self.winner,
            "phase": self.phase.value,
            "budget_remaining": self.budget_remaining,
            "top_bid": top_bid,
            "round_timestamp": self.round_timestamp,
        }


# ---------------------------------------------------------------------------
# ChallengeRecord — mutable challenge state
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ChallengeRecord:
    """A structured record of a bid challenge in the fleet.

    When one fleet member believes another's bid is erroneous — due to
    incorrect capability declarations, inflated semantic scores, or conflicts
    with existing treaties — it may issue a *challenge*.  The challenge system
    is described in theory2.tex §46.5.

    A challenge record captures:

    * **who** challenged whom (``challenger_id``, ``challenged_id``)
    * **which bid** was challenged (``bid_id``)
    * **why** the challenge was raised (``challenge_reason``, free text)
    * **outcome** of resolution (initially ``"pending"``)
    * **evidence** submitted during resolution
    * **timing** (``created_at``, ``resolved_at``)

    Attributes
    ----------
    challenge_id:
        UUID assigned at construction.
    challenger_id:
        Identifier of the fleet member raising the challenge.
    challenged_id:
        Identifier of the fleet member whose bid is being challenged.
    bid_id:
        Identifier of the bid under challenge.
    challenge_reason:
        Human-readable description of why the challenge was raised.
    outcome:
        Resolution outcome; starts as ``"pending"``.
    evidence:
        Supporting evidence dictionary accumulated during resolution.
    resolved_at:
        Wall-clock time of resolution; ``None`` until resolved.
    created_at:
        Wall-clock time of challenge creation.
    """

    challenge_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    challenger_id: str = ""
    challenged_id: str = ""
    bid_id: str = ""
    challenge_reason: str = ""
    outcome: str = "pending"
    evidence: dict[str, Any] = field(default_factory=dict)
    resolved_at: Optional[float] = None
    created_at: float = field(default_factory=time.time)

    # ------------------------------------------------------------------
    # State transitions
    # ------------------------------------------------------------------

    def resolve(self, outcome: str, evidence: dict[str, Any]) -> None:
        """Resolve this challenge with the given outcome and supporting evidence.

        Parameters
        ----------
        outcome:
            A string describing the resolution (e.g. ``"upheld"``,
            ``"overturned"``, ``"withdrawn"``).  Not restricted to an enum
            to allow downstream layers to extend the vocabulary.
        evidence:
            Dictionary of evidence that informed the resolution.  Merged into
            the existing :attr:`evidence` dict.

        Raises
        ------
        ValueError
            If the challenge has already been resolved (i.e. ``resolved_at``
            is not ``None``).
        """
        if self.resolved_at is not None:
            raise ValueError(
                f"Challenge {self.challenge_id!r} has already been resolved "
                f"at {self.resolved_at}"
            )
        if not outcome or not isinstance(outcome, str):
            raise ValueError("outcome must be a non-empty string")
        self.outcome = outcome
        self.evidence.update(evidence)
        self.resolved_at = time.time()

    def is_pending(self) -> bool:
        """Return True if this challenge has not yet been resolved.

        Returns
        -------
        bool
            ``True`` iff ``resolved_at is None`` and ``outcome == "pending"``.
        """
        return self.resolved_at is None and self.outcome == "pending"

    def age_seconds(self) -> float:
        """Return the number of seconds since this challenge was created.

        Returns
        -------
        float
            Wall-clock age in seconds (``time.time() - created_at``).
        """
        return time.time() - self.created_at

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise this challenge record to a plain dictionary.

        Returns
        -------
        dict
            JSON-serialisable representation with all fields.
        """
        return {
            "challenge_id": self.challenge_id,
            "challenger_id": self.challenger_id,
            "challenged_id": self.challenged_id,
            "bid_id": self.bid_id,
            "challenge_reason": self.challenge_reason,
            "outcome": self.outcome,
            "evidence": dict(self.evidence),
            "resolved_at": self.resolved_at,
            "created_at": self.created_at,
            "is_pending": self.is_pending(),
            "age_seconds": self.age_seconds(),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ChallengeRecord":
        """Deserialise a ``ChallengeRecord`` from a plain dictionary.

        Parameters
        ----------
        d:
            Dictionary as produced by :meth:`to_dict`.

        Returns
        -------
        ChallengeRecord
            Reconstructed challenge record.
        """
        record = cls(
            challenge_id=d.get("challenge_id", str(uuid.uuid4())),
            challenger_id=d.get("challenger_id", ""),
            challenged_id=d.get("challenged_id", ""),
            bid_id=d.get("bid_id", ""),
            challenge_reason=d.get("challenge_reason", ""),
            outcome=d.get("outcome", "pending"),
            evidence=dict(d.get("evidence", {})),
            created_at=float(d.get("created_at", time.time())),
        )
        raw_resolved = d.get("resolved_at")
        record.resolved_at = float(raw_resolved) if raw_resolved is not None else None
        return record


# ---------------------------------------------------------------------------
# CalibrationTrace — mutable calibration history
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CalibrationTrace:
    """Longitudinal calibration history for a single fleet member.

    The fleet calibration system (theory2.tex §46.6) continuously observes
    member performance along three axes:

    * **accuracy** — fraction of past bids whose declared semantic scores
      were validated as correct by the evaluator (0 = wrong, 1 = perfect).
    * **latency** — response time in seconds for bid submission.
    * **trust** — composite trust score assigned by the fleet to this member
      (derived from accuracy, latency, and challenge outcomes).

    This class stores sliding histories for each axis and computes a summary
    *calibration score* that the scheduler uses to weight members' bids and
    to decide whether to trigger re-calibration.

    Attributes
    ----------
    member_id:
        The fleet member this trace belongs to.
    accuracy_history:
        Ordered list of observed accuracy values.
    latency_history:
        Ordered list of observed latency values (seconds).
    trust_history:
        Ordered list of trust scores at each observation.
    timestamps:
        Wall-clock timestamps corresponding to each sample.
    status:
        Current :class:`CalibrationStatus`.
    """

    member_id: str
    accuracy_history: list[float] = field(default_factory=list)
    latency_history: list[float] = field(default_factory=list)
    trust_history: list[float] = field(default_factory=list)
    timestamps: list[float] = field(default_factory=list)
    status: CalibrationStatus = field(default=CalibrationStatus.FRESH)

    # ------------------------------------------------------------------
    # Sample ingestion
    # ------------------------------------------------------------------

    def add_sample(
        self, accuracy: float, latency: float, trust: float
    ) -> None:
        """Record a new calibration observation.

        Values are clamped to valid ranges before appending:

        * accuracy → [0, 1]
        * latency  → [0, ∞) (clamped to [0, MAX_LATENCY])
        * trust    → [0, 1]

        After adding the sample, :attr:`status` is updated based on:

        1. Number of samples — fewer than :data:`MIN_SAMPLES_FOR_FRESH`
           triggers ``DEGRADED``.
        2. Time since last sample — exceeding :data:`STALE_THRESHOLD_SECONDS`
           triggers ``STALE``.
        3. Total age — exceeding :data:`DEGRADED_THRESHOLD_SECONDS` since the
           first sample without a recent update triggers ``DEGRADED``.

        Parameters
        ----------
        accuracy:
            Observed accuracy; clamped to [0, 1].
        latency:
            Observed latency in seconds; clamped to [0, MAX_LATENCY].
        trust:
            Observed trust score; clamped to [0, 1].
        """
        self.accuracy_history.append(_clamp(accuracy, 0.0, 1.0))
        self.latency_history.append(_clamp(latency, 0.0, MAX_LATENCY))
        self.trust_history.append(_clamp(trust, 0.0, 1.0))
        self.timestamps.append(time.time())
        self._update_status()

    def _update_status(self) -> None:
        """Recompute :attr:`status` based on the current history state.

        Called automatically by :meth:`add_sample`.  May also be called
        externally to refresh the status without adding a new observation
        (e.g. after a time-based staleness check).
        """
        n = len(self.timestamps)
        if n == 0:
            self.status = CalibrationStatus.INVALID
            return
        now = time.time()
        last_ts = self.timestamps[-1]
        first_ts = self.timestamps[0]
        age_since_last = now - last_ts
        total_age = now - first_ts
        if n < MIN_SAMPLES_FOR_FRESH:
            self.status = CalibrationStatus.DEGRADED
        elif age_since_last > STALE_THRESHOLD_SECONDS:
            self.status = CalibrationStatus.STALE
        elif total_age > DEGRADED_THRESHOLD_SECONDS and n < 20:
            # Old trace with few samples — not fresh enough to trust
            self.status = CalibrationStatus.DEGRADED
        else:
            self.status = CalibrationStatus.FRESH

    # ------------------------------------------------------------------
    # Analytical methods
    # ------------------------------------------------------------------

    def moving_average(self, window: int, series: str = "accuracy") -> list[float]:
        """Return a moving average over the named history series.

        Parameters
        ----------
        window:
            Number of trailing samples to average at each point; clamped
            to at least 1.
        series:
            Which history to operate on: ``"accuracy"``, ``"latency"``, or
            ``"trust"``.

        Returns
        -------
        list[float]
            Moving average values; same length as the source series.

        Raises
        ------
        ValueError
            If *series* is not one of the three recognised names.
        """
        if series == "accuracy":
            src = self.accuracy_history
        elif series == "latency":
            src = self.latency_history
        elif series == "trust":
            src = self.trust_history
        else:
            raise ValueError(
                f"Unknown series {series!r}; must be 'accuracy', 'latency', or 'trust'"
            )
        return _moving_average(src, window)

    def calibration_score(self) -> float:
        """Compute a single summary calibration score in [0, 1].

        The score is a weighted combination of trailing means over the last
        :data:`CALIBRATION_TRAILING_WINDOW` samples:

        .. math::

            \\text{score} = w_a \\cdot \\bar{a} + w_l \\cdot \\left(1 - \\frac{\\bar{l}}{L_{\\max}}\\right) + w_t \\cdot \\bar{t}

        where :math:`\\bar{a}` is the mean accuracy, :math:`\\bar{l}` is the
        mean latency normalised by :data:`MAX_LATENCY`, and :math:`\\bar{t}`
        is the mean trust.  Weights
        (:data:`CALIBRATION_WEIGHT_ACCURACY`, :data:`CALIBRATION_WEIGHT_LATENCY`,
        :data:`CALIBRATION_WEIGHT_TRUST`) sum to 1.0.

        Returns
        -------
        float
            Calibration score in [0, 1]; 0.0 if no samples are available.
        """
        w = CALIBRATION_TRAILING_WINDOW
        recent_acc = self.accuracy_history[-w:]
        recent_lat = self.latency_history[-w:]
        recent_trust = self.trust_history[-w:]
        if not recent_acc:
            return 0.0
        acc_component = _safe_mean(recent_acc)
        lat_mean = _safe_mean(recent_lat)
        lat_component = 1.0 - (lat_mean / MAX_LATENCY if MAX_LATENCY > 0 else 0.0)
        trust_component = _safe_mean(recent_trust)
        raw = (
            CALIBRATION_WEIGHT_ACCURACY * acc_component
            + CALIBRATION_WEIGHT_LATENCY * lat_component
            + CALIBRATION_WEIGHT_TRUST * trust_component
        )
        return _clamp(raw, 0.0, 1.0)

    def export_csv(self) -> str:
        """Export the calibration history as a CSV string.

        The CSV contains a header row followed by one row per sample.  The
        columns are: ``timestamp``, ``accuracy``, ``latency``, ``trust``.

        Returns
        -------
        str
            A UTF-8 CSV string with header.  Empty histories produce only the
            header row.

        Example
        -------
        ::

            timestamp,accuracy,latency,trust
            1700000000.0,0.95,0.12,0.88
            1700000010.0,0.97,0.10,0.90
        """
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["timestamp", "accuracy", "latency", "trust"])
        for ts, acc, lat, trust in zip(
            self.timestamps,
            self.accuracy_history,
            self.latency_history,
            self.trust_history,
        ):
            writer.writerow([ts, acc, lat, trust])
        return buf.getvalue()

    # ------------------------------------------------------------------
    # Summarisation
    # ------------------------------------------------------------------

    def summary(self) -> dict[str, Any]:
        """Return a high-level summary of this calibration trace.

        Returns
        -------
        dict
            Keys: ``member_id``, ``n_samples``, ``status``,
            ``calibration_score``, ``mean_accuracy``, ``mean_latency``,
            ``mean_trust``, ``std_accuracy``, ``std_latency``, ``std_trust``,
            ``latest_timestamp``.
        """
        n = len(self.timestamps)
        return {
            "member_id": self.member_id,
            "n_samples": n,
            "status": self.status.value,
            "calibration_score": self.calibration_score(),
            "mean_accuracy": _safe_mean(self.accuracy_history),
            "mean_latency": _safe_mean(self.latency_history),
            "mean_trust": _safe_mean(self.trust_history),
            "std_accuracy": _safe_std(self.accuracy_history),
            "std_latency": _safe_std(self.latency_history),
            "std_trust": _safe_std(self.trust_history),
            "latest_timestamp": self.timestamps[-1] if n > 0 else None,
        }

    def to_dict(self) -> dict[str, Any]:
        """Serialise the full calibration trace to a plain dictionary.

        Returns
        -------
        dict
            JSON-serialisable representation with all fields and the summary.
        """
        return {
            "member_id": self.member_id,
            "accuracy_history": list(self.accuracy_history),
            "latency_history": list(self.latency_history),
            "trust_history": list(self.trust_history),
            "timestamps": list(self.timestamps),
            "status": self.status.value,
            "summary": self.summary(),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "CalibrationTrace":
        """Deserialise a ``CalibrationTrace`` from a plain dictionary.

        Parameters
        ----------
        d:
            Dictionary as produced by :meth:`to_dict`.

        Returns
        -------
        CalibrationTrace
            Reconstructed calibration trace.
        """
        status_raw = d.get("status", CalibrationStatus.FRESH.value)
        try:
            status = CalibrationStatus(status_raw)
        except ValueError:
            status = CalibrationStatus.INVALID
        trace = cls(member_id=d.get("member_id", ""))
        trace.accuracy_history = [float(x) for x in d.get("accuracy_history", [])]
        trace.latency_history = [float(x) for x in d.get("latency_history", [])]
        trace.trust_history = [float(x) for x in d.get("trust_history", [])]
        trace.timestamps = [float(x) for x in d.get("timestamps", [])]
        trace.status = status
        return trace
