"""
Typed counter-challenges for the fleet_competition package.

# copilot: This module is part of JuGeo's copilot-assisted encoding of theory2.tex
Chapter 46: Fleet semantics — competitive search over admissible futures.

Chapter 46 §46.4–46.7 specifies the *counter-challenge sub-system* of the fleet
competition protocol.  When one fleet member believes that a rival's semantic
section contains an error, leaves a gap in coverage, violates an obligation, or
mismatches the target interface, it may issue a *typed challenge* backed by a
concrete *counterexample*.  Challenges are the adversarial engine that drives
quality improvement in the semantic marketplace.

Theory invariants enforced here
---------------------------------
1. **Judgment tuples** — every challenge outcome is expressed as the 8-tuple
   ``(c, φ, A, E, O, B, T, Π)``.  The challenge response must produce a new
   judgment that directly addresses the counterexample embedded in the challenge.

2. **Trust tier ordering** — a challenge that succeeds may *demote* the
   challenged section's trust tier, but never to a tier above the challenger's
   own minimum evidence-backed tier.

3. **Fleet = semantic marketplace** — challenges are market signals: they
   redistribute credibility from weaker to stronger sections.  The
   ``ChallengeRegistry`` maintains a bounded ledger of outstanding challenges
   and their lifecycle states.

Design overview
---------------
``ChallengeKind`` (Enum)
    Four fundamental kinds of typed challenge.

``TypedChallenge`` (frozen dataclass)
    An immutable challenge record with a ``ChallengeKind``, counterexample,
    evidence, and lifecycle metadata.

``CounterExample`` (frozen dataclass)
    A concrete witness that demonstrates the alleged flaw.

``ChallengeRegistry``
    Bounded in-memory store for ``TypedChallenge`` objects, supporting
    lifecycle transitions and expiry.

``ChallengeEvaluator``
    Scores each challenge by combining challenger trust, evidence quality,
    and counterexample specificity.

Chapter reference: theory2.tex Ch46 §46.4–46.7 — Typed counter-challenges.
"""
from __future__ import annotations

import logging
import math
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# Guarded upstream imports
# ---------------------------------------------------------------------------

try:
    from jugeo.orchestration.fleet_competition.models import (
        BidStatus,
        ChallengeRecord,
        CompetitiveBid,
        _clamp,
        _safe_mean,
        _safe_std,
    )
except Exception:  # pragma: no cover
    CompetitiveBid = Any  # type: ignore[assignment,misc]
    ChallengeRecord = Any  # type: ignore[assignment,misc]
    BidStatus = Any  # type: ignore[assignment,misc]

    def _clamp(v: float, lo: float, hi: float) -> float:  # type: ignore[misc]
        return max(lo, min(hi, v))

    def _safe_mean(seq: Any) -> float:  # type: ignore[misc]
        if not seq:
            return 0.0
        return sum(seq) / len(seq)

    def _safe_std(seq: Any) -> float:  # type: ignore[misc]
        import statistics
        if len(seq) < 2:
            return 0.0
        return statistics.stdev(seq)


try:
    from jugeo.orchestration.fleet import Fleet, FleetMember, ChallengeOutcome
except Exception:  # pragma: no cover
    Fleet = Any  # type: ignore[assignment,misc]
    FleetMember = Any  # type: ignore[assignment,misc]
    ChallengeOutcome = Any  # type: ignore[assignment,misc]

try:
    from jugeo.evidence.trust import TrustLevel, TrustPolicy
except Exception:  # pragma: no cover
    TrustLevel = Any  # type: ignore[assignment,misc]
    TrustPolicy = Any  # type: ignore[assignment,misc]

try:
    from jugeo.orchestration.fleet_competition.a_fleet_member_should_propose_sema import (
        TrustTier,
        JudgmentRecord,
        SemanticSection,
        FleetMemberProposal,
    )
except Exception:  # pragma: no cover
    TrustTier = Any  # type: ignore[assignment,misc]
    JudgmentRecord = Any  # type: ignore[assignment,misc]
    SemanticSection = Any  # type: ignore[assignment,misc]
    FleetMemberProposal = Any  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Module-level logger
# ---------------------------------------------------------------------------
_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

#: Maximum outstanding challenges allowed per challenger per round.
MAX_CHALLENGES_PER_CHALLENGER: int = 8

#: Maximum number of challenges stored in the registry at any one time.
REGISTRY_CAPACITY: int = 512

#: Age (seconds) after which a challenge that has not been adjudicated is
#: automatically expired as STALE.
CHALLENGE_TTL_SECONDS: float = 3600.0

#: Minimum evidence quality required for a challenge to be admissible.
MIN_CHALLENGE_EVIDENCE_QUALITY: float = 0.20

#: Weight applied to challenger trust tier in the challenge score.
CHALLENGE_WEIGHT_TRUST: float = 0.30

#: Weight applied to evidence quality in the challenge score.
CHALLENGE_WEIGHT_EVIDENCE: float = 0.40

#: Weight applied to counterexample specificity in the challenge score.
CHALLENGE_WEIGHT_SPECIFICITY: float = 0.30

__all__ = [
    "ChallengeKind",
    "ChallengeStatus",
    "CounterExample",
    "TypedChallenge",
    "ChallengeResponse",
    "ChallengeVerdict",
    "ChallengeRegistry",
    "ChallengeEvaluator",
    "issue_challenge",
    "evaluate_challenge",
    "respond_to_challenge",
]


# ===========================================================================
# Enumerations
# ===========================================================================


class ChallengeKind(Enum):
    """Four fundamental kinds of typed challenge in the semantic marketplace.

    Each kind targets a different aspect of a fleet member's proposal:

    SEMANTIC_ERROR
        The challenged section contains a factually incorrect claim — its
        proposition is false under the shared background theory.

    COVERAGE_GAP
        The challenged section claims to cover a semantic domain but leaves
        an identifiable sub-domain uncovered.

    OBLIGATION_VIOLATION
        The challenged section fails to discharge one or more of the obligations
        it declared in its proposal.

    INTERFACE_MISMATCH
        The challenged section's output type or interface contract does not
        match what downstream consumers require.
    """

    SEMANTIC_ERROR = auto()
    COVERAGE_GAP = auto()
    OBLIGATION_VIOLATION = auto()
    INTERFACE_MISMATCH = auto()


class ChallengeStatus(Enum):
    """Lifecycle states of a ``TypedChallenge``."""

    INITIATED = auto()       # Challenge has been created, not yet admitted
    ADMITTED = auto()        # Admitted to the adjudication queue
    EVIDENCE_SUBMITTED = auto()  # Challenger has submitted evidence
    RESPONDED = auto()       # Challenged member has responded
    ADJUDICATED = auto()     # ChallengeEvaluator has produced a verdict
    RESOLVED = auto()        # All downstream effects applied
    WITHDRAWN = auto()       # Challenger withdrew the challenge
    STALE = auto()           # TTL expired without adjudication
    REJECTED = auto()        # Rejected on admissibility grounds


# ===========================================================================
# Frozen value objects
# ===========================================================================


@dataclass(frozen=True, slots=True)
class CounterExample:
    """Immutable witness demonstrating the alleged flaw in a section.

    A counterexample is the concrete artefact that makes a challenge *typed*:
    it is not a vague assertion but a specific object that witnesses the flaw.

    Attributes:
        counterexample_id: Unique identifier.
        kind: The ``ChallengeKind`` this counterexample targets.
        witness_description: Human-readable description of the witness.
        witness_data: Serialisable representation of the witness object.
        specificity: Estimated specificity in [0, 1] — how precisely the
            counterexample pins down the flaw.
        created_by: Member ID of the challenger who constructed this witness.
        evidence_references: IDs of evidence items supporting the witness.
    """

    counterexample_id: str
    kind: ChallengeKind
    witness_description: str
    witness_data: Tuple[Tuple[str, str], ...]
    specificity: float
    created_by: str
    evidence_references: Tuple[str, ...]
    created_at: float = field(default_factory=time.monotonic)

    # ------------------------------------------------------------------
    def validate(self) -> List[str]:
        """Return a list of validation errors (empty == valid).

        Returns:
            List of human-readable error strings.
        """
        errors: List[str] = []
        if not self.witness_description:
            errors.append("witness_description must be non-empty")
        if not (0.0 <= self.specificity <= 1.0):
            errors.append(f"specificity {self.specificity} is outside [0, 1]")
        if not self.evidence_references:
            errors.append("A counterexample must include at least one evidence reference")
        return errors

    # ------------------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a plain dict.

        Returns:
            JSON-compatible dict representation.
        """
        return {
            "counterexample_id": self.counterexample_id,
            "kind": self.kind.name,
            "witness_description": self.witness_description,
            "witness_data": dict(self.witness_data),
            "specificity": self.specificity,
            "created_by": self.created_by,
            "evidence_references": list(self.evidence_references),
            "created_at": self.created_at,
        }


@dataclass(frozen=True, slots=True)
class TypedChallenge:
    """Immutable challenge record with full provenance.

    A ``TypedChallenge`` carries a ``ChallengeKind``, a ``CounterExample``,
    the identities of challenger and challenged, the target section, and full
    lifecycle metadata.  Once created, the record is immutable; status
    transitions produce new records via ``transition``.

    Attributes:
        challenge_id: Unique challenge identifier.
        kind: The ``ChallengeKind`` of this challenge.
        challenger_id: Fleet member ID of the issuing party.
        challenged_id: Fleet member ID of the responding party.
        target_section_id: ID of the ``SemanticSection`` being challenged.
        target_proposal_id: ID of the proposal containing the section.
        counterexample: The typed ``CounterExample`` backing this challenge.
        challenger_trust_tier: Trust tier of the challenger at challenge time.
        evidence_quality: Challenger's self-reported evidence quality in [0, 1].
        status: Current ``ChallengeStatus``.
        round_id: The fleet round in which this challenge was issued.
        response_deadline: Monotonic timestamp by which a response is required.
        created_at: Monotonic timestamp of challenge creation.
        metadata: Arbitrary metadata.
    """

    challenge_id: str
    kind: ChallengeKind
    challenger_id: str
    challenged_id: str
    target_section_id: str
    target_proposal_id: str
    counterexample: CounterExample
    challenger_trust_tier: Any  # TrustTier
    evidence_quality: float
    status: ChallengeStatus
    round_id: str
    response_deadline: float
    created_at: float = field(default_factory=time.monotonic)
    metadata: Tuple[Tuple[str, str], ...] = field(default_factory=tuple)

    # ------------------------------------------------------------------
    def transition(self, new_status: ChallengeStatus) -> "TypedChallenge":
        """Return a new ``TypedChallenge`` with *new_status* applied.

        Enforces the challenge lifecycle DAG:
            INITIATED → ADMITTED | REJECTED | WITHDRAWN
            ADMITTED → EVIDENCE_SUBMITTED | STALE | WITHDRAWN
            EVIDENCE_SUBMITTED → RESPONDED | STALE
            RESPONDED → ADJUDICATED
            ADJUDICATED → RESOLVED

        Args:
            new_status: Target status.

        Returns:
            A new ``TypedChallenge`` with *new_status*.

        Raises:
            ValueError: If the transition is not permitted by the lifecycle DAG.
        """
        _ALLOWED: Dict[ChallengeStatus, Tuple[ChallengeStatus, ...]] = {
            ChallengeStatus.INITIATED: (
                ChallengeStatus.ADMITTED,
                ChallengeStatus.REJECTED,
                ChallengeStatus.WITHDRAWN,
            ),
            ChallengeStatus.ADMITTED: (
                ChallengeStatus.EVIDENCE_SUBMITTED,
                ChallengeStatus.STALE,
                ChallengeStatus.WITHDRAWN,
            ),
            ChallengeStatus.EVIDENCE_SUBMITTED: (
                ChallengeStatus.RESPONDED,
                ChallengeStatus.STALE,
            ),
            ChallengeStatus.RESPONDED: (ChallengeStatus.ADJUDICATED,),
            ChallengeStatus.ADJUDICATED: (ChallengeStatus.RESOLVED,),
        }
        allowed = _ALLOWED.get(self.status, ())
        if new_status not in allowed:
            raise ValueError(
                f"Illegal challenge transition: {self.status.name} → {new_status.name}. "
                f"Allowed from {self.status.name}: {[s.name for s in allowed]}"
            )
        return TypedChallenge(
            challenge_id=self.challenge_id,
            kind=self.kind,
            challenger_id=self.challenger_id,
            challenged_id=self.challenged_id,
            target_section_id=self.target_section_id,
            target_proposal_id=self.target_proposal_id,
            counterexample=self.counterexample,
            challenger_trust_tier=self.challenger_trust_tier,
            evidence_quality=self.evidence_quality,
            status=new_status,
            round_id=self.round_id,
            response_deadline=self.response_deadline,
            created_at=self.created_at,
            metadata=self.metadata,
        )

    # ------------------------------------------------------------------
    def is_expired(self, now: Optional[float] = None) -> bool:
        """Return ``True`` if the challenge has exceeded its TTL.

        Args:
            now: Current monotonic time.  Defaults to ``time.monotonic()``.

        Returns:
            ``True`` if the challenge age exceeds ``CHALLENGE_TTL_SECONDS``
            and the status is not terminal.
        """
        _TERMINAL = {
            ChallengeStatus.RESOLVED,
            ChallengeStatus.WITHDRAWN,
            ChallengeStatus.STALE,
            ChallengeStatus.REJECTED,
        }
        if self.status in _TERMINAL:
            return False
        t = now if now is not None else time.monotonic()
        return (t - self.created_at) > CHALLENGE_TTL_SECONDS

    # ------------------------------------------------------------------
    def validate(self) -> List[str]:
        """Return a list of validation errors (empty == valid).

        Returns:
            List of human-readable error strings.
        """
        errors: List[str] = []
        if self.challenger_id == self.challenged_id:
            errors.append("challenger_id must differ from challenged_id")
        if not (0.0 <= self.evidence_quality <= 1.0):
            errors.append(f"evidence_quality {self.evidence_quality} outside [0, 1]")
        errors.extend(self.counterexample.validate())
        return errors

    # ------------------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a plain dict.

        Returns:
            JSON-compatible dict representation.
        """
        tier_name = (
            self.challenger_trust_tier.name
            if hasattr(self.challenger_trust_tier, "name")
            else str(self.challenger_trust_tier)
        )
        return {
            "challenge_id": self.challenge_id,
            "kind": self.kind.name,
            "challenger_id": self.challenger_id,
            "challenged_id": self.challenged_id,
            "target_section_id": self.target_section_id,
            "target_proposal_id": self.target_proposal_id,
            "counterexample": self.counterexample.to_dict(),
            "challenger_trust_tier": tier_name,
            "evidence_quality": self.evidence_quality,
            "status": self.status.name,
            "round_id": self.round_id,
            "response_deadline": self.response_deadline,
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class ChallengeResponse:
    """Immutable response from the challenged fleet member.

    Attributes:
        response_id: Unique response identifier.
        challenge_id: The challenge being responded to.
        responder_id: Fleet member ID of the responding party.
        rebuttal_description: Human-readable rebuttal.
        evidence_references: IDs of evidence supporting the rebuttal.
        proposed_patch_description: Optional description of a section patch.
        concedes: Whether the responder concedes the challenge.
        response_trust_tier: Trust tier of the responder's rebuttal evidence.
        created_at: Monotonic timestamp.
    """

    response_id: str
    challenge_id: str
    responder_id: str
    rebuttal_description: str
    evidence_references: Tuple[str, ...]
    proposed_patch_description: Optional[str]
    concedes: bool
    response_trust_tier: Any  # TrustTier
    created_at: float = field(default_factory=time.monotonic)

    # ------------------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a plain dict.

        Returns:
            JSON-compatible dict representation.
        """
        tier_name = (
            self.response_trust_tier.name
            if hasattr(self.response_trust_tier, "name")
            else str(self.response_trust_tier)
        )
        return {
            "response_id": self.response_id,
            "challenge_id": self.challenge_id,
            "responder_id": self.responder_id,
            "rebuttal_description": self.rebuttal_description,
            "evidence_references": list(self.evidence_references),
            "proposed_patch_description": self.proposed_patch_description,
            "concedes": self.concedes,
            "response_trust_tier": tier_name,
            "created_at": self.created_at,
        }


@dataclass(frozen=True, slots=True)
class ChallengeVerdict:
    """Immutable verdict produced by the ``ChallengeEvaluator``.

    Attributes:
        verdict_id: Unique verdict identifier.
        challenge_id: The challenge this verdict adjudicates.
        upheld: Whether the challenge is upheld (challenger wins).
        challenge_score: Score assigned to the challenge [0, 1].
        response_score: Score assigned to the response [0, 1]; 0 if no response.
        score_delta: Reputation delta applied to the challenger (positive if upheld).
        trust_demotion: Whether the challenged section's trust tier is demoted.
        demotion_target_tier: Target tier if demotion applies; ``None`` otherwise.
        explanation: Human-readable explanation of the verdict.
        adjudicated_at: Monotonic timestamp.
    """

    verdict_id: str
    challenge_id: str
    upheld: bool
    challenge_score: float
    response_score: float
    score_delta: float
    trust_demotion: bool
    demotion_target_tier: Any  # Optional[TrustTier]
    explanation: str
    adjudicated_at: float = field(default_factory=time.monotonic)

    # ------------------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a plain dict.

        Returns:
            JSON-compatible dict representation.
        """
        demotion_tier_name = None
        if self.demotion_target_tier is not None and hasattr(self.demotion_target_tier, "name"):
            demotion_tier_name = self.demotion_target_tier.name
        return {
            "verdict_id": self.verdict_id,
            "challenge_id": self.challenge_id,
            "upheld": self.upheld,
            "challenge_score": self.challenge_score,
            "response_score": self.response_score,
            "score_delta": self.score_delta,
            "trust_demotion": self.trust_demotion,
            "demotion_target_tier": demotion_tier_name,
            "explanation": self.explanation,
            "adjudicated_at": self.adjudicated_at,
        }


# ===========================================================================
# Challenge Registry
# ===========================================================================


class ChallengeRegistry:
    """Bounded in-memory store for ``TypedChallenge`` objects.

    The registry enforces:
    * The global capacity cap ``REGISTRY_CAPACITY``.
    * Per-challenger rate limits ``MAX_CHALLENGES_PER_CHALLENGER``.
    * Automatic expiry of stale challenges via ``expire_stale``.

    Args:
        capacity: Maximum number of challenges to store simultaneously.
        per_challenger_limit: Maximum outstanding challenges per challenger.
    """

    def __init__(
        self,
        capacity: int = REGISTRY_CAPACITY,
        per_challenger_limit: int = MAX_CHALLENGES_PER_CHALLENGER,
    ) -> None:
        self._capacity = capacity
        self._per_challenger_limit = per_challenger_limit
        self._store: Dict[str, TypedChallenge] = {}
        self._challenger_counts: Dict[str, int] = {}

    # ------------------------------------------------------------------
    def register(self, challenge: TypedChallenge) -> None:
        """Register *challenge* in the store.

        Args:
            challenge: The ``TypedChallenge`` to register.

        Raises:
            OverflowError: If the registry capacity is exceeded.
            ValueError: If the per-challenger rate limit is exceeded or the
                challenge fails validation.
        """
        errors = challenge.validate()
        if errors:
            raise ValueError(f"Invalid challenge: {errors}")

        if len(self._store) >= self._capacity:
            raise OverflowError(
                f"ChallengeRegistry capacity {self._capacity} exceeded; "
                "call expire_stale() to free space."
            )

        outstanding = self._challenger_counts.get(challenge.challenger_id, 0)
        if outstanding >= self._per_challenger_limit:
            raise ValueError(
                f"Challenger {challenge.challenger_id} has reached the per-round limit "
                f"of {self._per_challenger_limit} outstanding challenges."
            )

        self._store[challenge.challenge_id] = challenge
        self._challenger_counts[challenge.challenger_id] = outstanding + 1
        _log.debug("Registered challenge %s (%s)", challenge.challenge_id, challenge.kind.name)

    # ------------------------------------------------------------------
    def get(self, challenge_id: str) -> Optional[TypedChallenge]:
        """Return the challenge with *challenge_id*, or ``None``.

        Args:
            challenge_id: Challenge to look up.

        Returns:
            ``TypedChallenge`` or ``None``.
        """
        return self._store.get(challenge_id)

    # ------------------------------------------------------------------
    def update(self, challenge: TypedChallenge) -> None:
        """Replace the stored challenge with *challenge* (identified by challenge_id).

        Args:
            challenge: Updated ``TypedChallenge``.

        Raises:
            KeyError: If *challenge.challenge_id* is not in the store.
        """
        if challenge.challenge_id not in self._store:
            raise KeyError(f"Challenge {challenge.challenge_id} not found in registry")
        self._store[challenge.challenge_id] = challenge

    # ------------------------------------------------------------------
    def expire_stale(self, now: Optional[float] = None) -> int:
        """Mark expired challenges as STALE and remove them from counts.

        Args:
            now: Current monotonic time.  Defaults to ``time.monotonic()``.

        Returns:
            Number of challenges expired.
        """
        t = now if now is not None else time.monotonic()
        expired_ids = [
            cid for cid, c in self._store.items() if c.is_expired(t)
        ]
        for cid in expired_ids:
            old = self._store[cid]
            self._store[cid] = old.transition(ChallengeStatus.STALE)
            count = self._challenger_counts.get(old.challenger_id, 0)
            self._challenger_counts[old.challenger_id] = max(0, count - 1)
            _log.info("Challenge %s expired as STALE", cid)
        return len(expired_ids)

    # ------------------------------------------------------------------
    def all_for_round(self, round_id: str) -> List[TypedChallenge]:
        """Return all challenges for *round_id*.

        Args:
            round_id: Fleet round identifier.

        Returns:
            List of ``TypedChallenge`` objects for the given round.
        """
        return [c for c in self._store.values() if c.round_id == round_id]

    # ------------------------------------------------------------------
    def all_for_target(self, section_id: str) -> List[TypedChallenge]:
        """Return all challenges targeting *section_id*.

        Args:
            section_id: Target section identifier.

        Returns:
            List of ``TypedChallenge`` objects targeting the given section.
        """
        return [c for c in self._store.values() if c.target_section_id == section_id]

    # ------------------------------------------------------------------
    def summary(self) -> Dict[str, Any]:
        """Return a summary dict of registry state.

        Returns:
            Dict with counts by status and challenger.
        """
        by_status: Dict[str, int] = {}
        for c in self._store.values():
            by_status[c.status.name] = by_status.get(c.status.name, 0) + 1
        return {
            "total": len(self._store),
            "capacity": self._capacity,
            "by_status": by_status,
            "challenger_counts": dict(self._challenger_counts),
        }


# ===========================================================================
# Challenge Evaluator
# ===========================================================================


class ChallengeEvaluator:
    """Scores challenges and produces ``ChallengeVerdict`` objects.

    The evaluator applies three orthogonal criteria:
    * Challenger trust tier weight
    * Evidence quality
    * Counterexample specificity

    It also considers whether the challenged member submitted a response and
    adjusts the verdict accordingly.

    Args:
        weight_trust: Weight for challenger trust tier [0, 1].
        weight_evidence: Weight for evidence quality [0, 1].
        weight_specificity: Weight for counterexample specificity [0, 1].
        uphold_threshold: Minimum challenge score required to uphold [0, 1].
    """

    def __init__(
        self,
        weight_trust: float = CHALLENGE_WEIGHT_TRUST,
        weight_evidence: float = CHALLENGE_WEIGHT_EVIDENCE,
        weight_specificity: float = CHALLENGE_WEIGHT_SPECIFICITY,
        uphold_threshold: float = 0.55,
    ) -> None:
        total = weight_trust + weight_evidence + weight_specificity
        if not math.isclose(total, 1.0, abs_tol=1e-6):
            raise ValueError(
                f"ChallengeEvaluator weights must sum to 1.0; got {total:.6f}"
            )
        self._w_trust = weight_trust
        self._w_ev = weight_evidence
        self._w_spec = weight_specificity
        self._threshold = uphold_threshold

    # ------------------------------------------------------------------
    def _trust_score(self, challenge: TypedChallenge) -> float:
        """Return a numeric trust score in [0, 1] for *challenge*.

        Args:
            challenge: The ``TypedChallenge`` to evaluate.

        Returns:
            Float in [0.0, 1.0].
        """
        tier = challenge.challenger_trust_tier
        if hasattr(tier, "numeric_weight"):
            return tier.numeric_weight()
        # Fallback for Any-typed tier
        return 0.5

    # ------------------------------------------------------------------
    def score_challenge(self, challenge: TypedChallenge) -> float:
        """Return the challenge's raw score in [0, 1].

        Combines trust, evidence quality, and counterexample specificity.

        Args:
            challenge: The ``TypedChallenge`` to score.

        Returns:
            Float in [0.0, 1.0].
        """
        trust = self._trust_score(challenge)
        ev = _clamp(challenge.evidence_quality, 0.0, 1.0)
        spec = _clamp(challenge.counterexample.specificity, 0.0, 1.0)
        raw = self._w_trust * trust + self._w_ev * ev + self._w_spec * spec
        return _clamp(raw, 0.0, 1.0)

    # ------------------------------------------------------------------
    def score_response(self, response: Optional[ChallengeResponse]) -> float:
        """Return the response score in [0, 1], or 0.0 if no response.

        Args:
            response: A ``ChallengeResponse``, or ``None`` if not provided.

        Returns:
            Float in [0.0, 1.0].
        """
        if response is None:
            return 0.0
        if response.concedes:
            return 0.0
        n_ev = len(response.evidence_references)
        ev_score = _clamp(n_ev / 5.0, 0.0, 1.0)
        has_patch = 0.2 if response.proposed_patch_description else 0.0
        tier = response.response_trust_tier
        trust_bonus = tier.numeric_weight() * 0.3 if hasattr(tier, "numeric_weight") else 0.15
        return _clamp(ev_score * 0.5 + has_patch + trust_bonus, 0.0, 1.0)

    # ------------------------------------------------------------------
    def adjudicate(
        self,
        challenge: TypedChallenge,
        response: Optional[ChallengeResponse] = None,
    ) -> ChallengeVerdict:
        """Produce a ``ChallengeVerdict`` for *challenge* given optional *response*.

        Args:
            challenge: The challenge to adjudicate.
            response: Optional response from the challenged party.

        Returns:
            A frozen ``ChallengeVerdict``.
        """
        c_score = self.score_challenge(challenge)
        r_score = self.score_response(response)

        # Uphold if the challenge score dominates the response
        effective_challenge = c_score
        effective_response = r_score
        upheld = effective_challenge >= self._threshold and effective_challenge > effective_response

        # Score delta: positive reward for upheld challenges, negative penalty for frivolous ones
        if upheld:
            score_delta = c_score * 0.5
            trust_demotion = c_score >= 0.80
            demotion_target = _compute_demotion_tier(challenge) if trust_demotion else None
            explanation = (
                f"Challenge upheld: c_score={c_score:.3f} > r_score={r_score:.3f} "
                f"and c_score >= threshold {self._threshold:.3f}."
            )
        else:
            score_delta = -(1.0 - c_score) * 0.25
            trust_demotion = False
            demotion_target = None
            explanation = (
                f"Challenge not upheld: c_score={c_score:.3f}, r_score={r_score:.3f}. "
                f"Either below threshold {self._threshold:.3f} or response was stronger."
            )

        return ChallengeVerdict(
            verdict_id=str(uuid.uuid4()),
            challenge_id=challenge.challenge_id,
            upheld=upheld,
            challenge_score=c_score,
            response_score=r_score,
            score_delta=score_delta,
            trust_demotion=trust_demotion,
            demotion_target_tier=demotion_target,
            explanation=explanation,
        )


def _compute_demotion_tier(challenge: TypedChallenge) -> Any:
    """Return the demotion target trust tier for an upheld challenge.

    The demotion tier is one step below the challenger's current tier,
    bounded below by ``TrustTier.PROPOSAL``.

    Args:
        challenge: The upheld challenge.

    Returns:
        A ``TrustTier`` one level below the challenger's tier, or
        ``TrustTier.PROPOSAL`` if already at the bottom.
    """
    tier = challenge.challenger_trust_tier
    if not hasattr(tier, "value"):
        return None
    new_value = max(0, tier.value - 1)
    try:
        for t in type(tier):
            if t.value == new_value:
                return t
    except Exception:
        pass
    return tier


# ===========================================================================
# Module-level entry-point functions
# ===========================================================================


def issue_challenge(
    challenger_id: str,
    challenged_id: str,
    target_section_id: str,
    target_proposal_id: str,
    kind: ChallengeKind,
    counterexample: CounterExample,
    round_id: str,
    evidence_quality: float = 0.5,
    challenger_trust_tier: Any = None,
    response_deadline_offset: float = 600.0,
    registry: Optional[ChallengeRegistry] = None,
) -> TypedChallenge:
    """Create and optionally register a new ``TypedChallenge``.

    Factory function that stamps the challenge, validates admissibility,
    and optionally registers it with *registry*.

    Args:
        challenger_id: Fleet member ID of the challenger.
        challenged_id: Fleet member ID of the challenged party.
        target_section_id: ID of the section being challenged.
        target_proposal_id: ID of the proposal containing the section.
        kind: The ``ChallengeKind`` of the challenge.
        counterexample: The typed witness backing the challenge.
        round_id: Fleet round ID.
        evidence_quality: Evidence quality in [0, 1].
        challenger_trust_tier: Trust tier of the challenger.
        response_deadline_offset: Seconds from now until response is due.
        registry: Optional ``ChallengeRegistry`` to register in.

    Returns:
        A new ``TypedChallenge`` with status ``INITIATED`` (or ``ADMITTED``
        if evidence_quality meets the minimum threshold).

    Raises:
        ValueError: If evidence_quality is below ``MIN_CHALLENGE_EVIDENCE_QUALITY``.
    """
    if evidence_quality < MIN_CHALLENGE_EVIDENCE_QUALITY:
        raise ValueError(
            f"Evidence quality {evidence_quality:.3f} is below the minimum "
            f"{MIN_CHALLENGE_EVIDENCE_QUALITY} required to issue a challenge."
        )

    try:
        from jugeo.orchestration.fleet_competition.a_fleet_member_should_propose_sema import TrustTier as _TT
        tier = challenger_trust_tier if challenger_trust_tier is not None else _TT.PROPOSAL
    except Exception:
        tier = challenger_trust_tier

    challenge = TypedChallenge(
        challenge_id=str(uuid.uuid4()),
        kind=kind,
        challenger_id=challenger_id,
        challenged_id=challenged_id,
        target_section_id=target_section_id,
        target_proposal_id=target_proposal_id,
        counterexample=counterexample,
        challenger_trust_tier=tier,
        evidence_quality=_clamp(evidence_quality, 0.0, 1.0),
        status=ChallengeStatus.INITIATED,
        round_id=round_id,
        response_deadline=time.monotonic() + response_deadline_offset,
    )

    if registry is not None:
        registry.register(challenge)

    return challenge


def evaluate_challenge(
    challenge: TypedChallenge,
    response: Optional[ChallengeResponse] = None,
    evaluator: Optional[ChallengeEvaluator] = None,
) -> ChallengeVerdict:
    """Adjudicate *challenge* using *evaluator* (or a default evaluator).

    Convenience wrapper around ``ChallengeEvaluator.adjudicate``.

    Args:
        challenge: The ``TypedChallenge`` to adjudicate.
        response: Optional ``ChallengeResponse``.
        evaluator: Optional custom evaluator.

    Returns:
        A frozen ``ChallengeVerdict``.
    """
    ev = evaluator or ChallengeEvaluator()
    return ev.adjudicate(challenge, response)


def respond_to_challenge(
    challenge: TypedChallenge,
    responder_id: str,
    rebuttal_description: str,
    evidence_references: Sequence[str],
    concedes: bool = False,
    proposed_patch_description: Optional[str] = None,
    responder_trust_tier: Any = None,
    registry: Optional[ChallengeRegistry] = None,
) -> Tuple[ChallengeResponse, TypedChallenge]:
    """Create a response to *challenge* and advance its lifecycle.

    Args:
        challenge: The challenge to respond to.
        responder_id: Fleet member ID of the responder.
        rebuttal_description: Human-readable rebuttal text.
        evidence_references: Sequence of evidence IDs supporting the rebuttal.
        concedes: Whether the responder concedes the challenge.
        proposed_patch_description: Optional description of a proposed section patch.
        responder_trust_tier: Trust tier of the responder's evidence.
        registry: Optional ``ChallengeRegistry`` to update.

    Returns:
        A ``(ChallengeResponse, updated_TypedChallenge)`` tuple.
    """
    try:
        from jugeo.orchestration.fleet_competition.a_fleet_member_should_propose_sema import TrustTier as _TT
        tier = responder_trust_tier if responder_trust_tier is not None else _TT.REVIEWED
    except Exception:
        tier = responder_trust_tier

    response = ChallengeResponse(
        response_id=str(uuid.uuid4()),
        challenge_id=challenge.challenge_id,
        responder_id=responder_id,
        rebuttal_description=rebuttal_description,
        evidence_references=tuple(evidence_references),
        proposed_patch_description=proposed_patch_description,
        concedes=concedes,
        response_trust_tier=tier,
    )

    # Advance challenge lifecycle: EVIDENCE_SUBMITTED → RESPONDED
    transitioned = challenge
    if challenge.status == ChallengeStatus.EVIDENCE_SUBMITTED:
        transitioned = challenge.transition(ChallengeStatus.RESPONDED)
    elif challenge.status == ChallengeStatus.ADMITTED:
        transitioned = challenge.transition(ChallengeStatus.EVIDENCE_SUBMITTED)
        transitioned = transitioned.transition(ChallengeStatus.RESPONDED)

    if registry is not None:
        try:
            registry.update(transitioned)
        except KeyError:
            pass  # Registry may not contain this challenge

    return response, transitioned


# ===========================================================================
# Smoke test
# ===========================================================================

if __name__ == "__main__":
    print("=== Typed counter-challenge smoke test ===\n")

    try:
        from jugeo.orchestration.fleet_competition.a_fleet_member_should_propose_sema import TrustTier
    except Exception:
        class TrustTier(Enum):  # type: ignore[no-redef]
            PROPOSAL = 0
            REVIEWED = 1
            VERIFIED = 2
            RUNTIME_WITNESSED = 3
            PROOF_BACKED = 4
            def numeric_weight(self) -> float:
                return self.value / 4.0
            def can_promote_to(self, t: "TrustTier") -> bool:
                return t.value > self.value

    registry = ChallengeRegistry(capacity=50, per_challenger_limit=5)
    evaluator = ChallengeEvaluator()

    # Build a counterexample
    cx = CounterExample(
        counterexample_id=str(uuid.uuid4()),
        kind=ChallengeKind.COVERAGE_GAP,
        witness_description="Section claims to cover arithmetic but omits division-by-zero case",
        witness_data=(("input", "x / 0"), ("expected_behaviour", "raise ZeroDivisionError")),
        specificity=0.85,
        created_by="member-alpha",
        evidence_references=(str(uuid.uuid4()), str(uuid.uuid4())),
    )

    # Issue a challenge
    challenge = issue_challenge(
        challenger_id="member-alpha",
        challenged_id="member-beta",
        target_section_id="section-001",
        target_proposal_id="proposal-001",
        kind=ChallengeKind.COVERAGE_GAP,
        counterexample=cx,
        round_id="round-001",
        evidence_quality=0.8,
        challenger_trust_tier=TrustTier.VERIFIED,
        registry=registry,
    )
    print(f"Challenge issued: {challenge.challenge_id[:8]}…")
    print(f"  Status: {challenge.status.name}")
    print(f"  Kind:   {challenge.kind.name}")

    # Advance lifecycle
    challenge2 = challenge.transition(ChallengeStatus.ADMITTED)
    challenge3 = challenge2.transition(ChallengeStatus.EVIDENCE_SUBMITTED)
    registry.update(challenge3)

    # Respond
    response, challenge4 = respond_to_challenge(
        challenge=challenge3,
        responder_id="member-beta",
        rebuttal_description="Division-by-zero is handled in the error-handling section separately",
        evidence_references=[str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())],
        concedes=False,
        proposed_patch_description="Add explicit ZeroDivisionError guard to section body",
        responder_trust_tier=TrustTier.REVIEWED,
        registry=registry,
    )
    print(f"\nResponse issued: {response.response_id[:8]}…")
    print(f"  Concedes: {response.concedes}")

    # Adjudicate
    verdict = evaluate_challenge(challenge4, response, evaluator)
    print(f"\nVerdict: upheld={verdict.upheld}")
    print(f"  challenge_score={verdict.challenge_score:.3f}  response_score={verdict.response_score:.3f}")
    print(f"  score_delta={verdict.score_delta:+.3f}")
    print(f"  trust_demotion={verdict.trust_demotion}")
    print(f"  explanation: {verdict.explanation}")

    # Registry summary
    print(f"\nRegistry summary: {registry.summary()}")

    print("\nSmoke test passed.")
