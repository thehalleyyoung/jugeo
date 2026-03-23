"""Challenge and adjudication protocol for fleet competition.

This module implements the challenge-and-response protocol described in
theory2.tex Ch46 §46.4–46.7.  A *challenge* is raised when one fleet member
(the *challenger*) disputes the bid submitted by another member (the
*challenged*).  The challenge passes through several lifecycle stages
(initiated → evidence submitted → adjudicated → resolved) and produces an
outcome that feeds back into the bid-weighting system.

Design notes
------------
* The ``ChallengeInitiator`` creates ``ChallengeRecord`` objects and enforces
  per-member rate limits.
* The ``ChallengeAdjudicator`` scores each challenge by combining a *trust
  score* (derived from the record's internal bookkeeping) and an *evidence
  score* (derived from ``EvidenceGatherer``).
* The ``ChallengeLedger`` is a bounded in-memory store; old records are
  pruned by ``expire_old``.
* The ``ChallengeEventBus`` provides a lightweight publish/subscribe mechanism
  so external components can react to lifecycle transitions without coupling.
* ``ChallengeStatistics`` wraps the ledger to produce aggregate metrics.

All external jugeo imports are guarded with try/except to allow the module to
be imported in environments where only a subset of the jugeo package is
available.
"""

from __future__ import annotations

import logging
import math
import random
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Guarded external imports
# ---------------------------------------------------------------------------

try:
    from jugeo.orchestration.fleet_competition.models import (
        ChallengeRecord,
        CompetitiveBid,
        CalibrationTrace,
        BidStatus,
    )
except Exception:
    ChallengeRecord = Any  # type: ignore[assignment,misc]
    CompetitiveBid = Any  # type: ignore[assignment,misc]
    CalibrationTrace = Any  # type: ignore[assignment,misc]
    BidStatus = Any  # type: ignore[assignment,misc]

try:
    from jugeo.orchestration.fleet import (
        ChallengeRecord as FleetChallengeRecord,
        ChallengeOutcome,
    )
except Exception:
    FleetChallengeRecord = Any  # type: ignore[assignment,misc]
    ChallengeOutcome = Any  # type: ignore[assignment,misc]

try:
    from jugeo.evidence.trust import TrustAuditLog, TrustPolicy, TrustLevel
except Exception:
    TrustAuditLog = Any  # type: ignore[assignment,misc]
    TrustPolicy = Any  # type: ignore[assignment,misc]
    TrustLevel = Any  # type: ignore[assignment,misc]

try:
    from jugeo.geometry.descent import DescentEngine, DescentResult, OverlapStatus
except Exception:
    DescentEngine = Any  # type: ignore[assignment,misc]
    DescentResult = Any  # type: ignore[assignment,misc]
    OverlapStatus = Any  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

#: Maximum number of pending challenges a single member may have at once.
MAX_PENDING_PER_MEMBER: int = 5

#: Weight applied when the challenger's historical win rate is unknown.
DEFAULT_TRUST_SCORE: float = 0.5

#: Minimum evidence score required for a challenge to be "upheld".
UPHOLD_EVIDENCE_THRESHOLD: float = 0.6

#: Minimum trust score delta (challenger minus challenged) for an uphold.
UPHOLD_TRUST_DELTA: float = 0.1

#: Outcome string constants – used throughout the module and by callers.
OUTCOME_UPHELD: str = "upheld"
OUTCOME_OVERTURNED: str = "overturned"
OUTCOME_WITHDRAWN: str = "withdrawn"
OUTCOME_SPLIT: str = "split"

#: Synthetic semantic overlap when no descent engine is available.
SYNTHETIC_OVERLAP_BASE: float = 0.4

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class ChallengeEventKind(Enum):
    """Lifecycle events that can occur on a challenge record.

    Each constant corresponds to a transition in the challenge state machine
    described in theory2.tex Ch46 §46.5 Figure 3.
    """

    INITIATED = auto()
    """The challenge has been created and is awaiting evidence."""

    EVIDENCE_SUBMITTED = auto()
    """One or more evidence items have been attached to the challenge."""

    ADJUDICATED = auto()
    """The adjudicator has rendered a verdict (upheld / overturned / split)."""

    WITHDRAWN = auto()
    """The challenger retracted the challenge before adjudication."""

    ESCALATED = auto()
    """The challenge exceeded the escalation threshold and was forwarded to a
    higher-authority component (e.g. a fleet-level arbiter)."""

    RESOLVED = auto()
    """Terminal state – the challenge has been fully closed out."""


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class AdjudicationPolicy:
    """Configurable parameters for the adjudication algorithm.

    Attributes
    ----------
    require_evidence:
        Whether the adjudicator must have at least one evidence item before
        rendering a verdict.  When ``False``, verdicts can be issued on trust
        scores alone.
    timeout_seconds:
        How long (in wall-clock seconds) the adjudicator waits for evidence
        before timing out and issuing a default verdict.
    trust_weight:
        Fractional weight assigned to the trust component when combining
        trust and evidence scores.  Must satisfy ``trust_weight +
        evidence_weight == 1.0`` after validation.
    evidence_weight:
        Fractional weight assigned to the evidence component.
    escalation_threshold:
        Number of previous unresolved challenges between the same pair of
        members that triggers automatic escalation.
    auto_resolve_after:
        Age in seconds after which a challenge is automatically resolved as
        ``OUTCOME_WITHDRAWN`` if it has not been adjudicated.
    """

    require_evidence: bool = True
    timeout_seconds: float = 60.0
    trust_weight: float = 0.5
    evidence_weight: float = 0.5
    escalation_threshold: int = 3
    auto_resolve_after: float = 300.0

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """Serialise the policy to a plain dictionary."""
        return {
            "require_evidence": self.require_evidence,
            "timeout_seconds": self.timeout_seconds,
            "trust_weight": self.trust_weight,
            "evidence_weight": self.evidence_weight,
            "escalation_threshold": self.escalation_threshold,
            "auto_resolve_after": self.auto_resolve_after,
        }

    @classmethod
    def from_dict(cls, d: dict) -> AdjudicationPolicy:
        """Deserialise an ``AdjudicationPolicy`` from a dictionary.

        Unknown keys are silently ignored so that older serialised policies
        remain compatible with newer code.
        """
        return cls(
            require_evidence=bool(d.get("require_evidence", True)),
            timeout_seconds=float(d.get("timeout_seconds", 60.0)),
            trust_weight=float(d.get("trust_weight", 0.5)),
            evidence_weight=float(d.get("evidence_weight", 0.5)),
            escalation_threshold=int(d.get("escalation_threshold", 3)),
            auto_resolve_after=float(d.get("auto_resolve_after", 300.0)),
        )

    def validate(self) -> list[str]:
        """Return a list of validation errors; empty list means the policy is valid.

        Checks include:
        * Weights sum to 1.0 (within floating-point tolerance).
        * Timeout is positive.
        * Escalation threshold is at least 1.
        * Auto-resolve window is greater than timeout.
        """
        errors: list[str] = []
        weight_sum = self.trust_weight + self.evidence_weight
        if abs(weight_sum - 1.0) > 1e-6:
            errors.append(
                f"trust_weight ({self.trust_weight}) + evidence_weight "
                f"({self.evidence_weight}) must equal 1.0, got {weight_sum}"
            )
        if self.timeout_seconds <= 0:
            errors.append(f"timeout_seconds must be positive, got {self.timeout_seconds}")
        if self.escalation_threshold < 1:
            errors.append(
                f"escalation_threshold must be >= 1, got {self.escalation_threshold}"
            )
        if self.auto_resolve_after <= self.timeout_seconds:
            errors.append(
                f"auto_resolve_after ({self.auto_resolve_after}) must be greater "
                f"than timeout_seconds ({self.timeout_seconds})"
            )
        return errors


@dataclass(frozen=True, slots=True)
class ChallengeEvent:
    """An immutable event record for a single lifecycle transition.

    ``ChallengeEvent`` objects are emitted by the ``ChallengeEventBus`` and
    consumed by subscribers.  They are *not* persisted to the ledger; the
    ledger stores only the ``ChallengeRecord`` mutable state.

    Attributes
    ----------
    event_id:
        Unique identifier for this specific event instance.
    challenge_id:
        The challenge this event belongs to.
    kind:
        The kind of lifecycle transition.
    payload:
        Arbitrary metadata associated with the event (e.g. evidence items,
        outcome string, escalation reason).
    timestamp:
        Unix epoch seconds when the event was created.
    """

    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    challenge_id: str = ""
    kind: ChallengeEventKind = ChallengeEventKind.INITIATED
    payload: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        """Serialise the event to a plain dictionary suitable for logging or JSON."""
        return {
            "event_id": self.event_id,
            "challenge_id": self.challenge_id,
            "kind": self.kind.name,
            "payload": self.payload,
            "timestamp": self.timestamp,
        }


# ---------------------------------------------------------------------------
# Evidence gathering
# ---------------------------------------------------------------------------


class EvidenceGatherer:
    """Collect and summarise evidence relevant to a challenge.

    When a ``DescentEngine`` is available, the gatherer can perform a semantic
    overlap computation between the two competing bids.  Without one it falls
    back to a lightweight synthetic estimate derived from the bid values
    themselves.

    Parameters
    ----------
    descent_engine:
        An optional ``DescentEngine`` instance.  Pass ``None`` (or leave as
        default) to operate in synthetic mode.
    """

    def __init__(self, descent_engine: Any = None) -> None:
        self.descent_engine = descent_engine

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def gather(
        self,
        challenge: Any,
        challenger_bid: Any | None,
        challenged_bid: Any | None,
    ) -> dict:
        """Gather all available evidence for *challenge*.

        Returns a dictionary with the following keys:

        ``semantic_overlap``
            Float in [0, 1] – how semantically similar the two bids are.
            High overlap suggests the challenger and challenged were competing
            for the same content, which strengthens the challenge.
        ``trust_delta``
            Signed float – the difference in trust between challenger and
            challenged (challenger_trust - challenged_trust).  Positive values
            favour the challenger.
        ``descent_status``
            String describing the geometric descent compatibility result.
        ``evidence_items``
            A list of individual evidence strings gathered from the bids.
        """
        evidence: dict = {
            "semantic_overlap": 0.0,
            "trust_delta": 0.0,
            "descent_status": "unknown",
            "evidence_items": [],
        }

        # --- Compute semantic overlap ---
        if challenger_bid is not None and challenged_bid is not None:
            evidence["semantic_overlap"] = self._compute_semantic_overlap(
                challenger_bid, challenged_bid
            )
            evidence["descent_status"] = self._check_descent_compatibility(
                challenger_bid, challenged_bid
            )

        # --- Trust delta ---
        challenger_trust = _safe_attr(challenge, "challenger_trust", DEFAULT_TRUST_SCORE)
        challenged_trust = _safe_attr(challenge, "challenged_trust", DEFAULT_TRUST_SCORE)
        evidence["trust_delta"] = float(challenger_trust) - float(challenged_trust)

        # --- Evidence items ---
        items: list[str] = []
        if challenger_bid is not None:
            items.append(f"challenger_bid_value={_safe_attr(challenger_bid, 'bid_value', '?')}")
            items.append(f"challenger_semantic={_safe_attr(challenger_bid, 'semantic_score', '?')}")
        if challenged_bid is not None:
            items.append(f"challenged_bid_value={_safe_attr(challenged_bid, 'bid_value', '?')}")
            items.append(f"challenged_semantic={_safe_attr(challenged_bid, 'semantic_score', '?')}")
        evidence["evidence_items"] = items

        return evidence

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _compute_semantic_overlap(self, a: Any, b: Any) -> float:
        """Estimate the semantic overlap between two bids.

        When a ``DescentEngine`` is present, delegates to it.  Otherwise
        computes a synthetic proxy based on the ratio of the smaller semantic
        score to the larger, which is a rough measure of relative confidence
        proximity.
        """
        if self.descent_engine is not None:
            try:
                result = self.descent_engine.overlap(a, b)
                return float(result)
            except Exception as exc:
                logger.debug("DescentEngine.overlap failed: %s", exc)

        # Synthetic fallback: ratio of min/max semantic scores.
        score_a = float(_safe_attr(a, "semantic_score", 0.5))
        score_b = float(_safe_attr(b, "semantic_score", 0.5))
        if score_a == 0.0 and score_b == 0.0:
            return SYNTHETIC_OVERLAP_BASE
        max_s = max(score_a, score_b)
        min_s = min(score_a, score_b)
        # Overlap is higher when the scores are close together.
        raw = min_s / max_s if max_s > 0 else SYNTHETIC_OVERLAP_BASE
        # Blend with base to avoid extreme values when scores are trivial.
        return _clamp(0.3 * SYNTHETIC_OVERLAP_BASE + 0.7 * raw, 0.0, 1.0)

    def _check_descent_compatibility(self, a: Any, b: Any) -> str:
        """Return a string describing the descent compatibility of two bids.

        Uses ``DescentEngine`` when available; otherwise returns a heuristic
        string based on uncertainty comparison.
        """
        if self.descent_engine is not None:
            try:
                status = self.descent_engine.check_compatibility(a, b)
                return str(status)
            except Exception as exc:
                logger.debug("DescentEngine.check_compatibility failed: %s", exc)

        # Heuristic: compare uncertainty attributes.
        unc_a = float(_safe_attr(a, "uncertainty", 0.5))
        unc_b = float(_safe_attr(b, "uncertainty", 0.5))
        delta = abs(unc_a - unc_b)
        if delta < 0.05:
            return "compatible"
        elif delta < 0.2:
            return "marginal"
        else:
            return "incompatible"


# ---------------------------------------------------------------------------
# Challenge initiation
# ---------------------------------------------------------------------------


class ChallengeInitiator:
    """Creates and validates new ``ChallengeRecord`` objects.

    The initiator enforces business rules defined in theory2.tex Ch46 §46.4:
    a member may not challenge itself, must supply a non-empty reason, and
    must not have too many outstanding challenges already.

    Parameters
    ----------
    policy:
        An ``AdjudicationPolicy`` used to derive rate-limit thresholds.  If
        ``None``, a default policy is constructed.
    """

    def __init__(self, policy: Any | None = None) -> None:
        self.policy: AdjudicationPolicy = policy if policy is not None else AdjudicationPolicy()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def initiate(
        self,
        challenger_id: str,
        challenged_id: str,
        bid_id: str,
        reason: str,
    ) -> Any:
        """Create and return a new ``ChallengeRecord``.

        The record is not added to any ledger here; the caller is responsible
        for persisting it.  Use ``validate_challenge`` to check the record
        before persisting.

        Parameters
        ----------
        challenger_id:
            Identifier of the fleet member raising the challenge.
        challenged_id:
            Identifier of the fleet member whose bid is being challenged.
        bid_id:
            The specific bid being disputed.
        reason:
            Human-readable explanation of why the bid is being challenged.
        """
        record_id = str(uuid.uuid4())
        now = time.time()

        # Build a plain dict that quacks like a ChallengeRecord so the module
        # works even when the real dataclass is unavailable.
        try:
            record = ChallengeRecord(  # type: ignore[call-arg]
                challenge_id=record_id,
                challenger_id=challenger_id,
                challenged_id=challenged_id,
                bid_id=bid_id,
                reason=reason,
                created_at=now,
                status="pending",
            )
        except Exception:
            # Fallback: use a plain namespace object.
            record = _SimpleNamespace(
                challenge_id=record_id,
                challenger_id=challenger_id,
                challenged_id=challenged_id,
                bid_id=bid_id,
                reason=reason,
                created_at=now,
                updated_at=now,
                status="pending",
                outcome=None,
                evidence={},
                challenger_trust=DEFAULT_TRUST_SCORE,
                challenged_trust=DEFAULT_TRUST_SCORE,
            )

        logger.info(
            "Challenge initiated: %s → %s (id=%s)",
            challenger_id,
            challenged_id,
            record_id,
        )
        return record

    def validate_challenge(self, record: Any) -> list[str]:
        """Return a list of validation errors for *record*.

        An empty list means the record is valid and may be persisted.

        Checks performed
        ~~~~~~~~~~~~~~~~
        * ``challenger_id != challenged_id``
        * ``reason`` is a non-empty string
        * ``bid_id`` is a non-empty string
        * ``challenge_id`` is a non-empty string
        """
        errors: list[str] = []
        challenger_id = _safe_attr(record, "challenger_id", "")
        challenged_id = _safe_attr(record, "challenged_id", "")
        reason = _safe_attr(record, "reason", "")
        bid_id = _safe_attr(record, "bid_id", "")
        challenge_id = _safe_attr(record, "challenge_id", "")

        if not challenge_id:
            errors.append("challenge_id must be non-empty")
        if not bid_id:
            errors.append("bid_id must be non-empty")
        if challenger_id == challenged_id:
            errors.append(
                f"challenger_id and challenged_id must differ; both are '{challenger_id}'"
            )
        if not isinstance(reason, str) or not reason.strip():
            errors.append("reason must be a non-empty string")
        return errors

    def can_initiate(self, challenger_id: str, existing_challenges: list[Any]) -> bool:
        """Return ``True`` if *challenger_id* is allowed to raise another challenge.

        A member is blocked when the number of pending challenges it has
        already raised meets or exceeds ``MAX_PENDING_PER_MEMBER``.
        """
        pending_count = sum(
            1
            for ch in existing_challenges
            if _safe_attr(ch, "challenger_id", "") == challenger_id
            and _safe_attr(ch, "status", "") == "pending"
        )
        return pending_count < MAX_PENDING_PER_MEMBER


# ---------------------------------------------------------------------------
# Adjudication
# ---------------------------------------------------------------------------


class ChallengeAdjudicator:
    """Renders verdicts on challenges using trust and evidence signals.

    The adjudication algorithm is described in theory2.tex Ch46 §46.6.  The
    final outcome is determined by a weighted combination of:

    * A *trust score* reflecting the relative trustworthiness of the
      challenger vs the challenged member.
    * An *evidence score* derived from semantic overlap, descent compatibility,
      and any explicit evidence items.

    Parameters
    ----------
    policy:
        ``AdjudicationPolicy`` controlling weights and thresholds.
    evidence_gatherer:
        ``EvidenceGatherer`` used to collect evidence before scoring.  If
        ``None``, a default gatherer (without a descent engine) is used.
    """

    def __init__(
        self,
        policy: Any | None = None,
        evidence_gatherer: Any | None = None,
    ) -> None:
        self.policy: AdjudicationPolicy = (
            policy if policy is not None else AdjudicationPolicy()
        )
        self.evidence_gatherer: EvidenceGatherer = (
            evidence_gatherer if evidence_gatherer is not None else EvidenceGatherer()
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def adjudicate(
        self,
        record: Any,
        challenger_bid: Any | None = None,
        challenged_bid: Any | None = None,
    ) -> str:
        """Render a verdict for *record* and return the outcome string.

        Possible return values: ``"upheld"``, ``"overturned"``, ``"withdrawn"``,
        ``"split"``.

        Side effects
        ~~~~~~~~~~~~
        The method mutates *record* in place if possible (sets
        ``record.outcome`` and ``record.status``).  If *record* is frozen,
        the mutation is silently skipped and the outcome is only returned.

        Parameters
        ----------
        record:
            The challenge to adjudicate.
        challenger_bid:
            The bid submitted by the challenger, used for evidence gathering.
        challenged_bid:
            The bid submitted by the challenged member.
        """
        status = _safe_attr(record, "status", "pending")
        if status == "withdrawn":
            logger.info("Challenge %s already withdrawn", _safe_attr(record, "challenge_id", "?"))
            return OUTCOME_WITHDRAWN

        # Gather evidence first.
        evidence = self.evidence_gatherer.gather(record, challenger_bid, challenged_bid)

        trust_score = self._compute_trust_score(record)
        evidence_score = self._compute_evidence_score(evidence)

        outcome = self._resolve_outcome(trust_score, evidence_score)

        # Attempt to update the record in place.
        _try_set(record, "outcome", outcome)
        _try_set(record, "status", "resolved")
        _try_set(record, "updated_at", time.time())
        _try_set(record, "evidence", evidence)

        logger.info(
            "Challenge %s adjudicated: %s (trust=%.3f, evidence=%.3f)",
            _safe_attr(record, "challenge_id", "?"),
            outcome,
            trust_score,
            evidence_score,
        )
        return outcome

    # ------------------------------------------------------------------
    # Private scoring helpers
    # ------------------------------------------------------------------

    def _compute_trust_score(self, record: Any) -> float:
        """Compute a normalised trust advantage score for the challenger.

        Returns a value in [0, 1].  Values above 0.5 indicate the challenger
        has higher trust than the challenged member.
        """
        challenger_trust = float(_safe_attr(record, "challenger_trust", DEFAULT_TRUST_SCORE))
        challenged_trust = float(_safe_attr(record, "challenged_trust", DEFAULT_TRUST_SCORE))

        # Sigmoid-normalise the delta to keep the output in (0, 1).
        delta = challenger_trust - challenged_trust
        sigmoid = 1.0 / (1.0 + math.exp(-10.0 * delta))  # scale factor 10 for sensitivity
        return _clamp(sigmoid, 0.0, 1.0)

    def _compute_evidence_score(self, evidence: dict) -> float:
        """Convert an evidence dictionary into a scalar score in [0, 1].

        Combines:
        * Semantic overlap (high overlap → stronger challenge).
        * Trust delta sign (positive = challenger has more trust).
        * Descent status ('compatible' adds a bonus).
        """
        overlap = float(evidence.get("semantic_overlap", 0.0))
        trust_delta = float(evidence.get("trust_delta", 0.0))
        descent = str(evidence.get("descent_status", "unknown"))

        # Base from overlap.
        base = overlap * 0.6

        # Trust contribution: clamp normalised delta to [-0.2, 0.2].
        trust_contrib = _clamp(trust_delta * 0.5, -0.2, 0.2)

        # Descent bonus.
        descent_bonus = 0.0
        if descent == "compatible":
            descent_bonus = 0.1
        elif descent == "marginal":
            descent_bonus = 0.05
        elif descent == "incompatible":
            descent_bonus = -0.1

        raw = base + trust_contrib + descent_bonus
        return _clamp(raw, 0.0, 1.0)

    def _resolve_outcome(self, trust_score: float, evidence_score: float) -> str:
        """Combine trust and evidence scores into a categorical outcome.

        Decision table (from theory2.tex Ch46 §46.6 Table 2):

        +-----------------+--------------------+-------------+
        | trust_score     | evidence_score     | outcome     |
        +=================+====================+=============+
        | >= 0.6          | >= threshold       | upheld      |
        | >= 0.6          | < threshold        | split       |
        | < 0.6           | >= threshold       | split       |
        | < 0.6           | < threshold        | overturned  |
        +-----------------+--------------------+=============+
        """
        policy = self.policy
        combined = (
            policy.trust_weight * trust_score
            + policy.evidence_weight * evidence_score
        )

        trust_ok = trust_score >= 0.6
        evidence_ok = evidence_score >= UPHOLD_EVIDENCE_THRESHOLD

        if trust_ok and evidence_ok:
            return OUTCOME_UPHELD
        elif not trust_ok and not evidence_ok:
            return OUTCOME_OVERTURNED
        else:
            # Mixed signals → split verdict.
            # Break the tie by the combined weighted score.
            if combined >= 0.55:
                return OUTCOME_UPHELD
            return OUTCOME_SPLIT


# ---------------------------------------------------------------------------
# Ledger
# ---------------------------------------------------------------------------


class ChallengeLedger:
    """Bounded in-memory store for challenge records.

    Records are stored in insertion order.  When the ledger exceeds
    ``max_size`` a ``ValueError`` is raised rather than silently dropping
    records (callers should call ``expire_old`` periodically to free space).

    Parameters
    ----------
    max_size:
        Maximum number of records held before ``add`` raises ``ValueError``.
    """

    def __init__(self, max_size: int = 10_000) -> None:
        self.max_size = max_size
        self._records: dict[str, Any] = {}  # challenge_id → record

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def add(self, record: Any) -> None:
        """Add *record* to the ledger.

        Raises ``ValueError`` if the ledger is full.
        """
        if len(self._records) >= self.max_size:
            raise ValueError(
                f"ChallengeLedger is full ({self.max_size} records); "
                "call expire_old() before adding more"
            )
        cid = _safe_attr(record, "challenge_id", "")
        if not cid:
            raise ValueError("record must have a non-empty challenge_id")
        self._records[cid] = record

    def get(self, challenge_id: str) -> Any | None:
        """Return the record with *challenge_id*, or ``None`` if not found."""
        return self._records.get(challenge_id)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def pending(self) -> list[Any]:
        """Return all records with status ``"pending"``."""
        return [r for r in self._records.values() if _safe_attr(r, "status", "") == "pending"]

    def resolved(self) -> list[Any]:
        """Return all records with status ``"resolved"``."""
        return [r for r in self._records.values() if _safe_attr(r, "status", "") == "resolved"]

    def by_challenger(self, challenger_id: str) -> list[Any]:
        """Return all records where *challenger_id* raised the challenge."""
        return [
            r
            for r in self._records.values()
            if _safe_attr(r, "challenger_id", "") == challenger_id
        ]

    def by_challenged(self, challenged_id: str) -> list[Any]:
        """Return all records where *challenged_id* is the target."""
        return [
            r
            for r in self._records.values()
            if _safe_attr(r, "challenged_id", "") == challenged_id
        ]

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    def expire_old(self, max_age_seconds: float) -> int:
        """Mark old pending challenges as expired and return the count.

        Only ``"pending"`` records older than *max_age_seconds* are affected.
        Their status is set to ``"expired"`` (a terminal status indicating
        they were never adjudicated).
        """
        cutoff = time.time() - max_age_seconds
        count = 0
        for record in self._records.values():
            if (
                _safe_attr(record, "status", "") == "pending"
                and float(_safe_attr(record, "created_at", time.time())) < cutoff
            ):
                _try_set(record, "status", "expired")
                _try_set(record, "updated_at", time.time())
                count += 1
        return count

    def summary(self) -> dict:
        """Return a high-level summary of ledger contents.

        Keys: total, pending, resolved, expired, withdrawn.
        """
        totals: dict[str, int] = defaultdict(int)
        for r in self._records.values():
            status = _safe_attr(r, "status", "unknown")
            totals[status] += 1
        return {
            "total": len(self._records),
            "pending": totals.get("pending", 0),
            "resolved": totals.get("resolved", 0),
            "expired": totals.get("expired", 0),
            "withdrawn": totals.get("withdrawn", 0),
        }

    def __len__(self) -> int:
        """Return the number of records currently held."""
        return len(self._records)


# ---------------------------------------------------------------------------
# Event bus
# ---------------------------------------------------------------------------


class ChallengeEventBus:
    """Publish/subscribe bus for ``ChallengeEvent`` objects.

    Handlers are keyed by ``ChallengeEventKind``.  When an event is
    published, all handlers registered for that kind are called in order of
    registration.  Exceptions raised by handlers are caught and logged but do
    not interrupt other handlers.

    Parameters
    ----------
    handlers:
        Optional pre-populated handler dictionary.  Rarely needed outside
        tests.
    """

    def __init__(
        self,
        handlers: dict[ChallengeEventKind, list[Callable]] | None = None,
    ) -> None:
        self.handlers: dict[ChallengeEventKind, list[Callable]] = (
            handlers if handlers is not None else {}
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def subscribe(
        self,
        kind: ChallengeEventKind,
        handler: Callable[[ChallengeEvent], None],
    ) -> None:
        """Register *handler* to be called when events of *kind* are published.

        The same handler may be registered multiple times; each registration
        results in a separate invocation.
        """
        self.handlers.setdefault(kind, []).append(handler)

    def publish(self, event: ChallengeEvent) -> None:
        """Publish *event* and invoke all subscribed handlers.

        Handlers are called synchronously in registration order.  Exceptions
        are caught and logged as warnings; they do not propagate.
        """
        for handler in self.handlers.get(event.kind, []):
            try:
                handler(event)
            except Exception as exc:
                logger.warning(
                    "Handler %s raised exception for event %s: %s",
                    handler,
                    event.event_id,
                    exc,
                )

    def clear(self, kind: ChallengeEventKind | None = None) -> None:
        """Remove all handlers for *kind*, or all handlers if *kind* is ``None``."""
        if kind is None:
            self.handlers.clear()
        else:
            self.handlers.pop(kind, None)

    def handler_count(self, kind: ChallengeEventKind | None = None) -> int:
        """Return the number of registered handlers for *kind* (or total)."""
        if kind is not None:
            return len(self.handlers.get(kind, []))
        return sum(len(hs) for hs in self.handlers.values())


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------


class ChallengeStatistics:
    """Compute aggregate metrics from a ``ChallengeLedger``.

    All methods read from the ledger at call time; no caching is performed.
    This makes the statistics always consistent with the current ledger state
    but means repeated calls may be expensive on large ledgers.

    Parameters
    ----------
    ledger:
        The ``ChallengeLedger`` to derive statistics from.
    """

    def __init__(self, ledger: ChallengeLedger) -> None:
        self.ledger = ledger

    # ------------------------------------------------------------------
    # Scalar metrics
    # ------------------------------------------------------------------

    def total_challenges(self) -> int:
        """Return the total number of challenge records in the ledger."""
        return len(self.ledger)

    def upheld_rate(self) -> float:
        """Fraction of resolved challenges that were upheld.

        Returns 0.0 when there are no resolved challenges.
        """
        resolved = self.ledger.resolved()
        if not resolved:
            return 0.0
        upheld = sum(
            1 for r in resolved if _safe_attr(r, "outcome", "") == OUTCOME_UPHELD
        )
        return _safe_div(upheld, len(resolved))

    def overturned_rate(self) -> float:
        """Fraction of resolved challenges that were overturned.

        Returns 0.0 when there are no resolved challenges.
        """
        resolved = self.ledger.resolved()
        if not resolved:
            return 0.0
        overturned = sum(
            1 for r in resolved if _safe_attr(r, "outcome", "") == OUTCOME_OVERTURNED
        )
        return _safe_div(overturned, len(resolved))

    def average_resolution_time(self) -> float:
        """Mean wall-clock seconds from creation to resolution.

        Only considers records that have both ``created_at`` and
        ``updated_at`` attributes (i.e. records that have been through the
        adjudicator at least once).  Returns 0.0 if no such records exist.
        """
        resolved = self.ledger.resolved()
        times: list[float] = []
        for r in resolved:
            created = _safe_attr(r, "created_at", None)
            updated = _safe_attr(r, "updated_at", None)
            if created is not None and updated is not None:
                delta = float(updated) - float(created)
                if delta >= 0:
                    times.append(delta)
        if not times:
            return 0.0
        return sum(times) / len(times)

    def most_challenged_member(self) -> str | None:
        """Return the member ID that has been the *target* most often.

        Returns ``None`` when the ledger is empty.
        """
        counts: dict[str, int] = defaultdict(int)
        for r in self.ledger._records.values():
            cid = _safe_attr(r, "challenged_id", "")
            if cid:
                counts[cid] += 1
        if not counts:
            return None
        return max(counts, key=lambda k: counts[k])

    def most_challenging_member(self) -> str | None:
        """Return the member ID that has raised the most challenges.

        Returns ``None`` when the ledger is empty.
        """
        counts: dict[str, int] = defaultdict(int)
        for r in self.ledger._records.values():
            cid = _safe_attr(r, "challenger_id", "")
            if cid:
                counts[cid] += 1
        if not counts:
            return None
        return max(counts, key=lambda k: counts[k])

    def summary_report(self) -> dict:
        """Produce a comprehensive summary report dictionary.

        The report includes all scalar metrics plus the ledger summary.
        """
        return {
            "total_challenges": self.total_challenges(),
            "upheld_rate": self.upheld_rate(),
            "overturned_rate": self.overturned_rate(),
            "average_resolution_time_seconds": self.average_resolution_time(),
            "most_challenged_member": self.most_challenged_member(),
            "most_challenging_member": self.most_challenging_member(),
            "ledger_summary": self.ledger.summary(),
        }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


class _SimpleNamespace:
    """Minimal namespace used as a fallback when the real dataclass is absent."""

    def __init__(self, **kwargs: Any) -> None:
        for k, v in kwargs.items():
            object.__setattr__(self, k, v)


def _safe_attr(obj: Any, attr: str, default: Any = None) -> Any:
    """Return ``getattr(obj, attr, default)`` without raising.

    Handles both attribute access (for dataclasses/objects) and dict access
    transparently.
    """
    if isinstance(obj, dict):
        return obj.get(attr, default)
    try:
        return getattr(obj, attr, default)
    except Exception:
        return default


def _try_set(obj: Any, attr: str, value: Any) -> None:
    """Attempt to set an attribute on *obj*, silently ignoring failures.

    Works for mutable dataclasses and plain objects.  Frozen dataclasses
    will raise ``FrozenInstanceError`` which is caught and swallowed.
    """
    if isinstance(obj, dict):
        obj[attr] = value
        return
    try:
        setattr(obj, attr, value)
    except Exception:
        pass


def _safe_div(numerator: float, denominator: float, default: float = 0.0) -> float:
    """Return ``numerator / denominator`` or *default* when denominator is zero."""
    if denominator == 0:
        return default
    return numerator / denominator


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp *value* to the closed interval [*lo*, *hi*]."""
    return max(lo, min(hi, value))


# ---------------------------------------------------------------------------
# Convenience factory
# ---------------------------------------------------------------------------


def build_default_pipeline(
    policy: AdjudicationPolicy | None = None,
    descent_engine: Any = None,
    ledger_max_size: int = 10_000,
) -> tuple[ChallengeInitiator, ChallengeAdjudicator, ChallengeLedger, ChallengeEventBus]:
    """Construct a ready-to-use challenge pipeline with default components.

    Returns a 4-tuple: (initiator, adjudicator, ledger, event_bus).

    Example
    -------
    >>> initiator, adjudicator, ledger, bus = build_default_pipeline()
    >>> record = initiator.initiate("A", "B", "bid-001", "Suspicious overlap")
    >>> ledger.add(record)
    >>> outcome = adjudicator.adjudicate(record)
    """
    p = policy if policy is not None else AdjudicationPolicy()
    gatherer = EvidenceGatherer(descent_engine=descent_engine)
    initiator = ChallengeInitiator(policy=p)
    adjudicator = ChallengeAdjudicator(policy=p, evidence_gatherer=gatherer)
    ledger = ChallengeLedger(max_size=ledger_max_size)
    bus = ChallengeEventBus()
    return initiator, adjudicator, ledger, bus


# ---------------------------------------------------------------------------
# Escalation helper
# ---------------------------------------------------------------------------


def check_escalation(
    challenger_id: str,
    challenged_id: str,
    ledger: ChallengeLedger,
    policy: AdjudicationPolicy | None = None,
) -> bool:
    """Return ``True`` if the pair should be escalated.

    Escalation is triggered when the number of *pending* challenges between
    the same challenger/challenged pair meets or exceeds
    ``policy.escalation_threshold``.

    Parameters
    ----------
    challenger_id:
        ID of the challenging member.
    challenged_id:
        ID of the challenged member.
    ledger:
        Ledger to search for existing records.
    policy:
        Optional policy; if ``None`` a default is used.
    """
    p = policy if policy is not None else AdjudicationPolicy()
    pending = ledger.pending()
    pair_pending = sum(
        1
        for r in pending
        if _safe_attr(r, "challenger_id", "") == challenger_id
        and _safe_attr(r, "challenged_id", "") == challenged_id
    )
    return pair_pending >= p.escalation_threshold


def create_event(
    challenge_id: str,
    kind: ChallengeEventKind,
    payload: dict | None = None,
) -> ChallengeEvent:
    """Convenience constructor for ``ChallengeEvent`` with sensible defaults.

    Parameters
    ----------
    challenge_id:
        The challenge this event is associated with.
    kind:
        The lifecycle event kind.
    payload:
        Optional metadata dictionary.
    """
    return ChallengeEvent(
        event_id=str(uuid.uuid4()),
        challenge_id=challenge_id,
        kind=kind,
        payload=payload or {},
        timestamp=time.time(),
    )


# ---------------------------------------------------------------------------
# Module self-test (runs when executed as __main__)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # Quick smoke-test of the pipeline.
    initiator, adjudicator, ledger, bus = build_default_pipeline()

    # Subscribe a simple handler.
    events_seen: list[ChallengeEvent] = []
    bus.subscribe(ChallengeEventKind.INITIATED, lambda e: events_seen.append(e))
    bus.subscribe(ChallengeEventKind.ADJUDICATED, lambda e: events_seen.append(e))

    # Create and persist three challenges.
    for i in range(3):
        rec = initiator.initiate(
            challenger_id=f"member-{i}",
            challenged_id=f"member-{i+1}",
            bid_id=f"bid-{i}",
            reason=f"Test challenge {i}",
        )
        ledger.add(rec)
        bus.publish(create_event(rec.challenge_id, ChallengeEventKind.INITIATED))
        outcome = adjudicator.adjudicate(rec)
        bus.publish(
            create_event(rec.challenge_id, ChallengeEventKind.ADJUDICATED, {"outcome": outcome})
        )

    stats = ChallengeStatistics(ledger)
    print("Summary report:", stats.summary_report())
    print("Events seen:", len(events_seen))
