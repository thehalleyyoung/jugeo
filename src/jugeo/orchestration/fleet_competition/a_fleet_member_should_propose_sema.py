"""
Fleet member semantic section proposals for the fleet_competition package.

# copilot: This module is part of JuGeo's copilot-assisted encoding of theory2.tex
Chapter 46: Fleet semantics — competitive search over admissible futures.

Chapter 46 §46.1 describes the *proposal sub-system* of the fleet competition
protocol.  Each fleet member does not merely emit a surface action: it proposes
a *semantic section* — a structured object that specifies what semantic content
is being contributed, which obligations are being assumed, and how confident the
member is about each claim.  Proposals are the fundamental unit of the semantic
marketplace.

Theory invariants enforced here
---------------------------------
1. **Judgment tuples** — every proposal outcome is expressed as the 8-tuple
   ``(c, φ, A, E, O, B, T, Π)`` where *c* is the context, *φ* is the
   proposition, *A* is the agent set, *E* is evidence, *O* is obligations,
   *B* is background assumptions, *T* is the trust tier, and *Π* is proof
   obligations.  Bare booleans are never used for judgments.

2. **Trust tier ordering** — trust is an ordered algebra:
   PROPOSAL → REVIEWED → VERIFIED → RUNTIME_WITNESSED → PROOF_BACKED.
   A section's trust tier must be monotone non-decreasing throughout its
   lifecycle.

3. **Fleet = semantic marketplace** — sections compete on semantic content, not
   just syntactic form.  The ``ProposalEvaluator`` scores proposals on semantic
   coverage, obligation completeness, evidence strength, and trust alignment.

Design overview
---------------
``SemanticSection``
    Frozen dataclass representing a single semantic unit proposed by a fleet
    member.  Carries context, proposition, obligations, evidence references,
    and a trust tier.

``ProposalObligation``
    Frozen dataclass representing a single obligation that a fleet member
    agrees to discharge if the proposal is accepted.

``FleetMemberProposal``
    Mutable dataclass (frozen=False, slots=False) that aggregates one or more
    ``SemanticSection`` objects into a complete fleet member proposal.

``ProposalEvaluator``
    Stateless evaluator that scores a ``FleetMemberProposal`` and returns a
    rich ``ProposalScore``.

Chapter reference: theory2.tex Ch46 §46.1 — Fleet semantics.
"""
from __future__ import annotations

import logging
import math
import statistics
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
        CalibrationStatus,
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
    CalibrationStatus = Any  # type: ignore[assignment,misc]

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


try:
    from jugeo.orchestration.fleet import Fleet, FleetMember
except Exception:  # pragma: no cover
    Fleet = Any  # type: ignore[assignment,misc]
    FleetMember = Any  # type: ignore[assignment,misc]

try:
    from jugeo.evidence.trust import TrustLevel
except Exception:  # pragma: no cover
    TrustLevel = Any  # type: ignore[assignment,misc]

try:
    from jugeo.judgments.core import JudgmentTuple
except Exception:  # pragma: no cover
    JudgmentTuple = Any  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Module-level logger
# ---------------------------------------------------------------------------
_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

#: Minimum semantic coverage score required for a proposal to be admitted to
#: the competition.  Proposals scoring below this are rejected outright.
MIN_ADMISSIBLE_COVERAGE: float = 0.10

#: Weight applied to semantic coverage in the overall proposal score.
WEIGHT_COVERAGE: float = 0.35

#: Weight applied to obligation completeness in the overall proposal score.
WEIGHT_OBLIGATIONS: float = 0.25

#: Weight applied to evidence strength in the overall proposal score.
WEIGHT_EVIDENCE: float = 0.25

#: Weight applied to trust alignment in the overall proposal score.
WEIGHT_TRUST: float = 0.15

#: Maximum number of semantic sections permitted in a single proposal.
MAX_SECTIONS_PER_PROPOSAL: int = 64

#: Maximum number of obligations permitted per semantic section.
MAX_OBLIGATIONS_PER_SECTION: int = 32

__all__ = [
    "TrustTier",
    "ObligationKind",
    "JudgmentRecord",
    "ProposalObligation",
    "SemanticSection",
    "FleetMemberProposal",
    "ProposalScore",
    "ProposalEvaluator",
    "create_fleet_proposal",
    "evaluate_proposal",
    "score_proposal_quality",
]


# ===========================================================================
# Enumerations
# ===========================================================================


class TrustTier(Enum):
    """Ordered trust tiers forming the trust algebra.

    Trust is an ordered algebra; the ordering is:
        PROPOSAL < REVIEWED < VERIFIED < RUNTIME_WITNESSED < PROOF_BACKED.

    Every semantic section carries a trust tier, and the tier is monotone
    non-decreasing throughout the section's lifecycle.  Promotions must go
    through the ``promote_trust`` helper which enforces the ordering.
    """

    PROPOSAL = 0
    REVIEWED = 1
    VERIFIED = 2
    RUNTIME_WITNESSED = 3
    PROOF_BACKED = 4

    # ------------------------------------------------------------------
    def can_promote_to(self, target: "TrustTier") -> bool:
        """Return ``True`` iff *target* is a valid promotion from *self*.

        Args:
            target: The candidate target trust tier.

        Returns:
            ``True`` if target.value > self.value (strict improvement).
        """
        return target.value > self.value

    # ------------------------------------------------------------------
    def numeric_weight(self) -> float:
        """Return a normalised weight in [0, 1] for scoring purposes.

        The weight is proportional to the tier's ordinal position so that
        higher-trust proposals receive a higher trust alignment score.

        Returns:
            Float in [0.0, 1.0].
        """
        return self.value / (len(TrustTier) - 1)


class ObligationKind(Enum):
    """Kinds of obligations a fleet member can assume in a proposal."""

    COVERAGE = auto()       # Must cover a specified semantic domain
    CONSISTENCY = auto()    # Must not contradict existing verified sections
    EVIDENCE = auto()       # Must supply supporting evidence of specified quality
    LIVENESS = auto()       # Must produce a valid output within a time budget
    SAFETY = auto()         # Must not violate safety constraints
    TERMINATION = auto()    # Must terminate under specified resource limits
    COMPLETENESS = auto()   # Must cover all required sub-cases
    MONOTONICITY = auto()   # Must produce refinements that are monotone


# ===========================================================================
# Value objects — frozen dataclasses
# ===========================================================================


@dataclass(frozen=True, slots=True)
class JudgmentRecord:
    """Immutable 8-tuple judgment following the theory2 judgment schema.

    The full judgment tuple is ``(c, φ, A, E, O, B, T, Π)`` where:

    * *c* — context identifier (e.g. section_id or proposal_id)
    * *φ* — proposition being judged (human-readable string)
    * *A* — agent set (frozenset of member IDs that co-sign this judgment)
    * *E* — evidence references (tuple of evidence IDs)
    * *O* — obligations assumed (tuple of ``ObligationKind`` names)
    * *B* — background assumptions (tuple of assumption strings)
    * *T* — trust tier (``TrustTier`` value)
    * *Π* — proof obligations (tuple of proof obligation descriptions)

    This object is immutable.  Any transition (e.g. trust promotion) must
    produce a *new* ``JudgmentRecord`` via ``promote_trust``.
    """

    context: str
    proposition: str
    agent_set: Tuple[str, ...]
    evidence: Tuple[str, ...]
    obligations: Tuple[str, ...]
    background: Tuple[str, ...]
    trust_tier: TrustTier
    proof_obligations: Tuple[str, ...]
    record_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: float = field(default_factory=time.monotonic)

    # ------------------------------------------------------------------
    def as_tuple(
        self,
    ) -> Tuple[str, str, Tuple[str, ...], Tuple[str, ...], Tuple[str, ...], Tuple[str, ...], TrustTier, Tuple[str, ...]]:
        """Return the canonical 8-tuple ``(c, φ, A, E, O, B, T, Π)``.

        Returns:
            An 8-tuple of the judgment components in canonical order.
        """
        return (
            self.context,
            self.proposition,
            self.agent_set,
            self.evidence,
            self.obligations,
            self.background,
            self.trust_tier,
            self.proof_obligations,
        )

    # ------------------------------------------------------------------
    def promote_trust(self, new_tier: TrustTier) -> "JudgmentRecord":
        """Return a new ``JudgmentRecord`` with *new_tier* applied.

        Enforces the monotone trust ordering: raises ``ValueError`` if
        *new_tier* is not strictly above the current tier.

        Args:
            new_tier: The target trust tier.

        Returns:
            A new ``JudgmentRecord`` with *new_tier* and a fresh ``record_id``
            (the ``created_at`` is also refreshed to reflect the promotion
            timestamp).

        Raises:
            ValueError: If *new_tier* does not strictly dominate the current
                trust tier.
        """
        if not self.trust_tier.can_promote_to(new_tier):
            raise ValueError(
                f"Cannot promote trust from {self.trust_tier.name} to {new_tier.name}: "
                f"target must be strictly higher in the trust algebra."
            )
        return JudgmentRecord(
            context=self.context,
            proposition=self.proposition,
            agent_set=self.agent_set,
            evidence=self.evidence,
            obligations=self.obligations,
            background=self.background,
            trust_tier=new_tier,
            proof_obligations=self.proof_obligations,
        )

    # ------------------------------------------------------------------
    def validate(self) -> List[str]:
        """Return a list of validation error strings (empty == valid).

        Returns:
            A list of human-readable error strings.  Empty list means the
            record is structurally valid.
        """
        errors: List[str] = []
        if not self.context:
            errors.append("context must be a non-empty string")
        if not self.proposition:
            errors.append("proposition must be a non-empty string")
        if not self.agent_set:
            errors.append("agent_set must contain at least one agent ID")
        return errors

    # ------------------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a plain dict suitable for JSON encoding.

        Returns:
            A dict with all fields serialised to JSON-compatible types.
        """
        return {
            "record_id": self.record_id,
            "context": self.context,
            "proposition": self.proposition,
            "agent_set": list(self.agent_set),
            "evidence": list(self.evidence),
            "obligations": list(self.obligations),
            "background": list(self.background),
            "trust_tier": self.trust_tier.name,
            "proof_obligations": list(self.proof_obligations),
            "created_at": self.created_at,
        }


@dataclass(frozen=True, slots=True)
class ProposalObligation:
    """Immutable obligation that a fleet member assumes if its proposal is accepted.

    Obligations are the binding commitments that give the semantic marketplace
    its structure.  A fleet member that wins a section agrees to discharge all
    obligations it declared in its proposal.  Undischarged obligations are
    tracked by the calibration sub-system and penalise future bids.

    Attributes:
        obligation_id: Unique identifier for this obligation instance.
        kind: The ``ObligationKind`` classifying this obligation.
        description: Human-readable description of what must be satisfied.
        deadline_budget: Relative time budget (seconds) for discharging.
        evidence_threshold: Minimum evidence quality score required [0, 1].
        is_discharged: Whether this obligation has already been discharged.
            Frozen at construction time; use ``discharge()`` to get a new
            instance with ``is_discharged=True``.
        discharged_at: Monotonic timestamp of discharge, or ``None``.
    """

    obligation_id: str
    kind: ObligationKind
    description: str
    deadline_budget: float
    evidence_threshold: float
    is_discharged: bool = False
    discharged_at: Optional[float] = None

    # ------------------------------------------------------------------
    def discharge(self) -> "ProposalObligation":
        """Return a new ``ProposalObligation`` marked as discharged.

        Returns:
            A new frozen instance with ``is_discharged=True`` and
            ``discharged_at`` set to the current monotonic time.
        """
        return ProposalObligation(
            obligation_id=self.obligation_id,
            kind=self.kind,
            description=self.description,
            deadline_budget=self.deadline_budget,
            evidence_threshold=self.evidence_threshold,
            is_discharged=True,
            discharged_at=time.monotonic(),
        )

    # ------------------------------------------------------------------
    def urgency(self) -> float:
        """Return an urgency score in [0, 1] based on the deadline budget.

        Short deadlines produce high urgency; the relationship is inverse
        exponential.  A budget of 0 produces urgency 1.0; a budget of 3600
        seconds produces urgency close to 0.

        Returns:
            Float in [0.0, 1.0].
        """
        if self.deadline_budget <= 0.0:
            return 1.0
        return math.exp(-self.deadline_budget / 300.0)

    # ------------------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a plain dict.

        Returns:
            JSON-compatible dict representation.
        """
        return {
            "obligation_id": self.obligation_id,
            "kind": self.kind.name,
            "description": self.description,
            "deadline_budget": self.deadline_budget,
            "evidence_threshold": self.evidence_threshold,
            "is_discharged": self.is_discharged,
            "discharged_at": self.discharged_at,
        }


@dataclass(frozen=True, slots=True)
class SemanticSection:
    """Immutable semantic section proposed by a fleet member.

    A *semantic section* is the atomic unit of content in the fleet competition
    semantic marketplace.  It carries a full judgment record (the 8-tuple), a
    list of obligations, an estimated semantic coverage score, and evidence
    references.  Multiple sections may be bundled into a single
    ``FleetMemberProposal``.

    Attributes:
        section_id: Unique identifier for this section.
        judgment: The ``JudgmentRecord`` (8-tuple) for this section.
        obligations: Tuple of ``ProposalObligation`` objects.
        estimated_coverage: Float in [0, 1] — fraction of the target semantic
            domain covered by this section.
        evidence_ids: Tuple of evidence reference strings.
        parent_section_id: ID of the section this refines, or ``None``.
        depth: Refinement depth (0 = root proposal).
        metadata: Arbitrary key-value metadata.
    """

    section_id: str
    judgment: JudgmentRecord
    obligations: Tuple[ProposalObligation, ...]
    estimated_coverage: float
    evidence_ids: Tuple[str, ...]
    parent_section_id: Optional[str] = None
    depth: int = 0
    metadata: Tuple[Tuple[str, str], ...] = field(default_factory=tuple)

    # ------------------------------------------------------------------
    def trust_tier(self) -> TrustTier:
        """Return the trust tier from the embedded judgment record.

        Returns:
            The ``TrustTier`` of the underlying judgment.
        """
        return self.judgment.trust_tier

    # ------------------------------------------------------------------
    def obligation_completeness(self) -> float:
        """Return the fraction of obligations that are discharged.

        Returns:
            Float in [0.0, 1.0].  Returns 1.0 if there are no obligations.
        """
        if not self.obligations:
            return 1.0
        discharged = sum(1 for o in self.obligations if o.is_discharged)
        return discharged / len(self.obligations)

    # ------------------------------------------------------------------
    def mean_urgency(self) -> float:
        """Return the mean urgency across all undischarged obligations.

        Returns:
            Float in [0.0, 1.0].  Returns 0.0 if all are discharged.
        """
        active = [o for o in self.obligations if not o.is_discharged]
        if not active:
            return 0.0
        return _safe_mean([o.urgency() for o in active])

    # ------------------------------------------------------------------
    def validate(self) -> List[str]:
        """Return a list of validation errors (empty == valid).

        Returns:
            List of human-readable error strings.
        """
        errors: List[str] = []
        errors.extend(self.judgment.validate())
        if not (0.0 <= self.estimated_coverage <= 1.0):
            errors.append(
                f"estimated_coverage {self.estimated_coverage} is outside [0, 1]"
            )
        if len(self.obligations) > MAX_OBLIGATIONS_PER_SECTION:
            errors.append(
                f"Too many obligations: {len(self.obligations)} > {MAX_OBLIGATIONS_PER_SECTION}"
            )
        if self.depth < 0:
            errors.append(f"depth must be non-negative, got {self.depth}")
        return errors

    # ------------------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a plain dict.

        Returns:
            JSON-compatible dict representation.
        """
        return {
            "section_id": self.section_id,
            "judgment": self.judgment.to_dict(),
            "obligations": [o.to_dict() for o in self.obligations],
            "estimated_coverage": self.estimated_coverage,
            "evidence_ids": list(self.evidence_ids),
            "parent_section_id": self.parent_section_id,
            "depth": self.depth,
            "metadata": dict(self.metadata),
        }


# ===========================================================================
# Proposal score — frozen output object
# ===========================================================================


@dataclass(frozen=True, slots=True)
class ProposalScore:
    """Immutable scoring result for a fleet member proposal.

    Produced by ``ProposalEvaluator.score``.  All component scores are in
    [0, 1]; the ``total`` is a weighted combination.

    Attributes:
        proposal_id: The proposal being scored.
        coverage_score: Semantic coverage component [0, 1].
        obligation_score: Obligation completeness component [0, 1].
        evidence_score: Evidence strength component [0, 1].
        trust_score: Trust alignment component [0, 1].
        total: Weighted total score [0, 1].
        is_admissible: Whether the proposal meets minimum admissibility criteria.
        rejection_reasons: Tuple of reasons if not admissible.
        scored_at: Monotonic timestamp of scoring.
    """

    proposal_id: str
    coverage_score: float
    obligation_score: float
    evidence_score: float
    trust_score: float
    total: float
    is_admissible: bool
    rejection_reasons: Tuple[str, ...]
    scored_at: float = field(default_factory=time.monotonic)

    # ------------------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a plain dict.

        Returns:
            JSON-compatible dict representation.
        """
        return {
            "proposal_id": self.proposal_id,
            "coverage_score": self.coverage_score,
            "obligation_score": self.obligation_score,
            "evidence_score": self.evidence_score,
            "trust_score": self.trust_score,
            "total": self.total,
            "is_admissible": self.is_admissible,
            "rejection_reasons": list(self.rejection_reasons),
            "scored_at": self.scored_at,
        }


# ===========================================================================
# Fleet member proposal — mutable aggregate
# ===========================================================================


@dataclass
class FleetMemberProposal:
    """Aggregated proposal from a single fleet member.

    A ``FleetMemberProposal`` bundles one or more ``SemanticSection`` objects
    into a complete proposal that the member submits to the fleet competition.
    The class is *mutable* at the aggregate level (sections can be added) but
    individual sections are frozen.

    Attributes:
        proposal_id: Unique proposal identifier.
        member_id: ID of the fleet member submitting this proposal.
        sections: List of ``SemanticSection`` objects (ordered by depth).
        submitted_at: Monotonic timestamp of submission.
        round_id: The fleet round this proposal belongs to.
        is_withdrawn: Whether the member has withdrawn this proposal.
        metadata: Arbitrary metadata dict.
    """

    proposal_id: str
    member_id: str
    sections: List[SemanticSection]
    submitted_at: float
    round_id: str
    is_withdrawn: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    def add_section(self, section: SemanticSection) -> None:
        """Append *section* to the proposal's section list.

        Enforces the ``MAX_SECTIONS_PER_PROPOSAL`` limit.

        Args:
            section: The ``SemanticSection`` to append.

        Raises:
            ValueError: If the proposal is withdrawn.
            OverflowError: If the section limit is exceeded.
        """
        if self.is_withdrawn:
            raise ValueError(
                f"Proposal {self.proposal_id} has been withdrawn; cannot add sections."
            )
        if len(self.sections) >= MAX_SECTIONS_PER_PROPOSAL:
            raise OverflowError(
                f"Proposal {self.proposal_id} already has {len(self.sections)} sections "
                f"(limit {MAX_SECTIONS_PER_PROPOSAL})."
            )
        self.sections.append(section)

    # ------------------------------------------------------------------
    def withdraw(self) -> None:
        """Mark this proposal as withdrawn.

        A withdrawn proposal is ignored by the evaluator and archived.
        """
        self.is_withdrawn = True
        _log.info("Proposal %s withdrawn by member %s", self.proposal_id, self.member_id)

    # ------------------------------------------------------------------
    def total_coverage(self) -> float:
        """Return the union coverage estimate across all sections.

        Uses a simple inclusion–exclusion approximation:
        ``1 - product(1 - s.estimated_coverage for s in sections)``.
        Returns 0.0 for empty proposals.

        Returns:
            Float in [0.0, 1.0].
        """
        if not self.sections:
            return 0.0
        complement = 1.0
        for sec in self.sections:
            complement *= 1.0 - _clamp(sec.estimated_coverage, 0.0, 1.0)
        return _clamp(1.0 - complement, 0.0, 1.0)

    # ------------------------------------------------------------------
    def min_trust_tier(self) -> TrustTier:
        """Return the minimum trust tier across all sections.

        The weakest trust tier limits the overall proposal trust.

        Returns:
            The minimum ``TrustTier`` in the section list.  If no sections
            exist, returns ``TrustTier.PROPOSAL``.
        """
        if not self.sections:
            return TrustTier.PROPOSAL
        return min(
            (sec.trust_tier() for sec in self.sections),
            key=lambda t: t.value,
        )

    # ------------------------------------------------------------------
    def validate(self) -> List[str]:
        """Return a list of validation errors (empty == valid).

        Returns:
            List of human-readable error strings.
        """
        errors: List[str] = []
        if not self.proposal_id:
            errors.append("proposal_id must be non-empty")
        if not self.member_id:
            errors.append("member_id must be non-empty")
        if not self.sections:
            errors.append("A proposal must contain at least one SemanticSection")
        for idx, sec in enumerate(self.sections):
            sub = sec.validate()
            for e in sub:
                errors.append(f"Section[{idx}] {sec.section_id}: {e}")
        return errors

    # ------------------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a plain dict.

        Returns:
            JSON-compatible dict representation.
        """
        return {
            "proposal_id": self.proposal_id,
            "member_id": self.member_id,
            "sections": [s.to_dict() for s in self.sections],
            "submitted_at": self.submitted_at,
            "round_id": self.round_id,
            "is_withdrawn": self.is_withdrawn,
            "metadata": dict(self.metadata),
        }


# ===========================================================================
# Proposal evaluator
# ===========================================================================


class ProposalEvaluator:
    """Stateless evaluator that scores ``FleetMemberProposal`` objects.

    The evaluator applies four orthogonal criteria (coverage, obligation
    completeness, evidence strength, trust alignment) and combines them into
    a weighted total score.  It also checks admissibility: proposals that fall
    below ``MIN_ADMISSIBLE_COVERAGE`` or contain validation errors are rejected.

    All weights are configurable at construction time with sensible defaults
    drawn from the module-level constants.

    Args:
        weight_coverage: Weight for semantic coverage [0, 1].
        weight_obligations: Weight for obligation completeness [0, 1].
        weight_evidence: Weight for evidence strength [0, 1].
        weight_trust: Weight for trust alignment [0, 1].
    """

    def __init__(
        self,
        weight_coverage: float = WEIGHT_COVERAGE,
        weight_obligations: float = WEIGHT_OBLIGATIONS,
        weight_evidence: float = WEIGHT_EVIDENCE,
        weight_trust: float = WEIGHT_TRUST,
    ) -> None:
        total = weight_coverage + weight_obligations + weight_evidence + weight_trust
        if not math.isclose(total, 1.0, abs_tol=1e-6):
            raise ValueError(
                f"Weights must sum to 1.0; got {total:.6f}. "
                f"Adjust weight_coverage, weight_obligations, weight_evidence, or weight_trust."
            )
        self._w_cov = weight_coverage
        self._w_obl = weight_obligations
        self._w_ev = weight_evidence
        self._w_trust = weight_trust

    # ------------------------------------------------------------------
    def score(self, proposal: FleetMemberProposal) -> ProposalScore:
        """Score *proposal* and return a ``ProposalScore``.

        Performs validation first; invalid proposals receive a 0.0 total and
        are marked inadmissible.

        Args:
            proposal: The ``FleetMemberProposal`` to evaluate.

        Returns:
            A frozen ``ProposalScore`` with per-criterion and total scores.
        """
        rejection_reasons: List[str] = []

        # Structural validation
        errors = proposal.validate()
        if errors:
            rejection_reasons.extend(errors)
            return ProposalScore(
                proposal_id=proposal.proposal_id,
                coverage_score=0.0,
                obligation_score=0.0,
                evidence_score=0.0,
                trust_score=0.0,
                total=0.0,
                is_admissible=False,
                rejection_reasons=tuple(rejection_reasons),
            )

        # --- Coverage score -------------------------------------------
        cov = proposal.total_coverage()
        cov_score = _clamp(cov, 0.0, 1.0)
        if cov_score < MIN_ADMISSIBLE_COVERAGE:
            rejection_reasons.append(
                f"Coverage {cov_score:.3f} below minimum {MIN_ADMISSIBLE_COVERAGE}"
            )

        # --- Obligation completeness score ----------------------------
        obl_scores = [s.obligation_completeness() for s in proposal.sections]
        obl_score = _safe_mean(obl_scores) if obl_scores else 0.0

        # --- Evidence strength score ----------------------------------
        ev_counts = [len(s.evidence_ids) for s in proposal.sections]
        raw_ev = _safe_mean(ev_counts) if ev_counts else 0.0
        # Normalise: 5+ evidence items → score 1.0; 0 → 0.0
        ev_score = _clamp(raw_ev / 5.0, 0.0, 1.0)

        # --- Trust alignment score ------------------------------------
        trust_score = proposal.min_trust_tier().numeric_weight()

        # --- Weighted total ------------------------------------------
        total = (
            self._w_cov * cov_score
            + self._w_obl * obl_score
            + self._w_ev * ev_score
            + self._w_trust * trust_score
        )
        total = _clamp(total, 0.0, 1.0)

        return ProposalScore(
            proposal_id=proposal.proposal_id,
            coverage_score=cov_score,
            obligation_score=obl_score,
            evidence_score=ev_score,
            trust_score=trust_score,
            total=total,
            is_admissible=len(rejection_reasons) == 0,
            rejection_reasons=tuple(rejection_reasons),
        )

    # ------------------------------------------------------------------
    def rank(self, proposals: Sequence[FleetMemberProposal]) -> List[Tuple[FleetMemberProposal, ProposalScore]]:
        """Score and rank *proposals* in descending order of total score.

        Withdrawn and inadmissible proposals are placed at the bottom of the
        ranking (in that order) but are still included for audit purposes.

        Args:
            proposals: Sequence of ``FleetMemberProposal`` objects.

        Returns:
            List of ``(proposal, score)`` pairs sorted by descending total
            score; ties broken by proposal_id lexicographically.
        """
        scored = [(p, self.score(p)) for p in proposals if not p.is_withdrawn]
        withdrawn = [(p, ProposalScore(
            proposal_id=p.proposal_id,
            coverage_score=0.0,
            obligation_score=0.0,
            evidence_score=0.0,
            trust_score=0.0,
            total=0.0,
            is_admissible=False,
            rejection_reasons=("withdrawn",),
        )) for p in proposals if p.is_withdrawn]

        scored.sort(key=lambda x: (-x[1].total, x[0].proposal_id))
        withdrawn.sort(key=lambda x: x[0].proposal_id)
        return scored + withdrawn


# ===========================================================================
# Module-level factory / entry-point functions
# ===========================================================================


def create_fleet_proposal(
    member_id: str,
    round_id: str,
    sections: Optional[List[SemanticSection]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> FleetMemberProposal:
    """Create a new ``FleetMemberProposal`` with a generated proposal_id.

    Factory function that sets sensible defaults and stamps the submission
    time.

    Args:
        member_id: ID of the fleet member submitting the proposal.
        round_id: ID of the fleet competition round.
        sections: Optional initial list of ``SemanticSection`` objects.
            Defaults to an empty list; sections may be added later via
            ``FleetMemberProposal.add_section``.
        metadata: Optional metadata dict.

    Returns:
        A new ``FleetMemberProposal`` ready for section population and
        submission.
    """
    return FleetMemberProposal(
        proposal_id=str(uuid.uuid4()),
        member_id=member_id,
        sections=list(sections or []),
        submitted_at=time.monotonic(),
        round_id=round_id,
        metadata=dict(metadata or {}),
    )


def evaluate_proposal(
    proposal: FleetMemberProposal,
    evaluator: Optional[ProposalEvaluator] = None,
) -> ProposalScore:
    """Evaluate *proposal* using *evaluator* (or a default evaluator).

    Convenience wrapper around ``ProposalEvaluator.score``.

    Args:
        proposal: The ``FleetMemberProposal`` to evaluate.
        evaluator: Optional custom evaluator.  If ``None``, a default
            ``ProposalEvaluator`` with module-level weights is used.

    Returns:
        A ``ProposalScore`` for the given proposal.
    """
    ev = evaluator or ProposalEvaluator()
    return ev.score(proposal)


def score_proposal_quality(
    proposals: Sequence[FleetMemberProposal],
    evaluator: Optional[ProposalEvaluator] = None,
) -> Dict[str, float]:
    """Score a sequence of proposals and return a ``{proposal_id: total}`` map.

    Useful for quickly comparing all proposals in a round without needing the
    full ``ProposalScore`` objects.

    Args:
        proposals: Sequence of ``FleetMemberProposal`` objects to score.
        evaluator: Optional custom evaluator.  Defaults to the standard
            ``ProposalEvaluator``.

    Returns:
        Dict mapping ``proposal_id`` → total score in [0, 1].
    """
    ev = evaluator or ProposalEvaluator()
    return {p.proposal_id: ev.score(p).total for p in proposals}


# ===========================================================================
# Internal helpers
# ===========================================================================


def _make_judgment(
    context: str,
    proposition: str,
    member_id: str,
    trust_tier: TrustTier = TrustTier.PROPOSAL,
    evidence_ids: Optional[Tuple[str, ...]] = None,
) -> JudgmentRecord:
    """Create a minimal ``JudgmentRecord`` suitable for unit testing.

    Args:
        context: Context identifier.
        proposition: Proposition string.
        member_id: ID of the single agent in the agent_set.
        trust_tier: Initial trust tier.
        evidence_ids: Optional evidence tuple.

    Returns:
        A new ``JudgmentRecord``.
    """
    return JudgmentRecord(
        context=context,
        proposition=proposition,
        agent_set=(member_id,),
        evidence=evidence_ids or (),
        obligations=(),
        background=("theory2.tex Ch46",),
        trust_tier=trust_tier,
        proof_obligations=(),
    )


def _make_obligation(
    kind: ObligationKind = ObligationKind.COVERAGE,
    description: str = "cover target domain",
    deadline_budget: float = 60.0,
    evidence_threshold: float = 0.7,
) -> ProposalObligation:
    """Create a minimal ``ProposalObligation`` suitable for unit testing.

    Args:
        kind: Obligation kind.
        description: Human-readable description.
        deadline_budget: Seconds allowed for discharge.
        evidence_threshold: Required evidence quality.

    Returns:
        A new frozen ``ProposalObligation``.
    """
    return ProposalObligation(
        obligation_id=str(uuid.uuid4()),
        kind=kind,
        description=description,
        deadline_budget=deadline_budget,
        evidence_threshold=evidence_threshold,
    )


def _make_section(
    member_id: str,
    coverage: float = 0.5,
    trust_tier: TrustTier = TrustTier.PROPOSAL,
    n_obligations: int = 2,
    n_evidence: int = 3,
) -> SemanticSection:
    """Create a minimal ``SemanticSection`` suitable for unit testing.

    Args:
        member_id: Agent ID for the embedded judgment.
        coverage: Estimated semantic coverage in [0, 1].
        trust_tier: Initial trust tier.
        n_obligations: Number of obligations to generate.
        n_evidence: Number of evidence IDs to generate.

    Returns:
        A new frozen ``SemanticSection``.
    """
    section_id = str(uuid.uuid4())
    judgment = _make_judgment(
        context=section_id,
        proposition=f"Section proposed by {member_id}",
        member_id=member_id,
        trust_tier=trust_tier,
        evidence_ids=tuple(str(uuid.uuid4()) for _ in range(n_evidence)),
    )
    obligations = tuple(_make_obligation() for _ in range(n_obligations))
    return SemanticSection(
        section_id=section_id,
        judgment=judgment,
        obligations=obligations,
        estimated_coverage=_clamp(coverage, 0.0, 1.0),
        evidence_ids=tuple(str(uuid.uuid4()) for _ in range(n_evidence)),
    )


# ===========================================================================
# Smoke test
# ===========================================================================

if __name__ == "__main__":
    import json

    print("=== Fleet member proposal smoke test ===\n")

    evaluator = ProposalEvaluator()

    # Build three proposals from different fleet members
    proposals: List[FleetMemberProposal] = []
    for i, (member, cov, tier, n_obl, n_ev) in enumerate([
        ("member-alpha", 0.75, TrustTier.REVIEWED, 3, 5),
        ("member-beta", 0.45, TrustTier.PROOF_BACKED, 1, 8),
        ("member-gamma", 0.05, TrustTier.PROPOSAL, 0, 0),  # Should fail admissibility
    ]):
        proposal = create_fleet_proposal(member_id=member, round_id="round-001")
        section = _make_section(
            member_id=member,
            coverage=cov,
            trust_tier=tier,
            n_obligations=n_obl,
            n_evidence=n_ev,
        )
        proposal.add_section(section)
        proposals.append(proposal)

    # Evaluate
    ranked = evaluator.rank(proposals)
    for rank_pos, (prop, score) in enumerate(ranked, 1):
        print(f"Rank {rank_pos}: member={prop.member_id}")
        print(f"  admissible={score.is_admissible}  total={score.total:.4f}")
        print(f"  coverage={score.coverage_score:.3f}  obligations={score.obligation_score:.3f}")
        print(f"  evidence={score.evidence_score:.3f}  trust={score.trust_score:.3f}")
        if score.rejection_reasons:
            print(f"  REJECTED: {score.rejection_reasons}")
        print()

    # Test judgment tuple round-trip
    j = _make_judgment("ctx-01", "phi_1 is valid", "member-alpha", TrustTier.VERIFIED)
    tup = j.as_tuple()
    assert len(tup) == 8, "Judgment tuple must be 8-tuple (c, φ, A, E, O, B, T, Π)"
    print(f"Judgment tuple: {tup}\n")

    # Test trust promotion
    j2 = j.promote_trust(TrustTier.PROOF_BACKED)
    assert j2.trust_tier == TrustTier.PROOF_BACKED
    print(f"Trust promoted: {j.trust_tier.name} → {j2.trust_tier.name}\n")

    # score_proposal_quality convenience function
    quality_map = score_proposal_quality(proposals)
    print("Quality map (proposal_id → score):")
    for pid, score in quality_map.items():
        print(f"  {pid[:8]}…  {score:.4f}")

    print("\nSmoke test passed.")
