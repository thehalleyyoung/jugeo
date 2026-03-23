"""Core data models for the inhabitant_fleets package.

Overview
--------
This module defines the foundational data structures used throughout the
``inhabitant_fleets`` sub-system of the JuGeo generation pipeline.  The
classes here implement the *Ch42 Semantic-Fleet Model*: a framework in which
multiple AI agents ("inhabitants") compete to *inhabit* overlapping semantic
patches of a construction space.

Theory Background – Ch42 Fleet Semantics
-----------------------------------------
In Ch42 theory a *semantic patch* P ⊆ S (where S is the full semantic space)
is a region whose meaning can be *inhabited* by an inhabitant t such that the
*inhabitation judgement*  Γ ⊢ t : P  holds under a given evidence context Γ.

Fleet operation proceeds through five *semantic move* types:

  PROPOSE    – assert a candidate inhabitant for a patch
  RETRACT    – withdraw a prior assertion
  REFINE     – strengthen an existing assertion (semantic narrowing)
  GENERALIZE – weaken an existing assertion (semantic broadening)
  SPECIALIZE – introduce a sub-case inhabitant (type refinement)

These moves are *semantic*, not syntactic: they operate on the *meaning* of
patches rather than on surface syntax.  Each move carries a *semantic distance*
δ(s, t) ∈ ℝ≥0 measuring how far the move travels in semantic space.

Backpressure arises when multiple inhabitants overlap in the same patch and
their combined instability score σ(P) exceeds a threshold θ:

    σ(P) = Σᵢ |compatibility(tᵢ, tⱼ)| for all competing pairs (i,j)
    if σ(P) > θ  →  BackpressureSignal is emitted

Fleet convergence is guaranteed (Theorem 4.2 in Ch42) when:

    ∀ patches P: ∃ a winning inhabitant t* such that
        score(t*) ≥ score(t)  ∀ competing inhabitants t
    AND
        σ(P) ≤ θ  (backpressure resolved)

Auction Mechanism (FleetBid)
-----------------------------
Each fleet member participates in a *Vickrey-style* sealed-bid auction for
the right to inhabit a patch.  A bid B encodes:

    total_score(B) = bid_score(B) × compat(B) × bp_tolerance(B)

where:
  • bid_score(B) ∈ [0, 1] is the member's self-assessed quality
  • compat(B) ∈ [0, 1] is the overlap compatibility with existing inhabitants
  • bp_tolerance(B) ∈ [0, 1] is the member's tolerance for backpressure

The winning bid is the one maximising total_score subject to compatibility
constraints across all concurrent bids.

NormalizedProposal & Canonical Forms
--------------------------------------
Before proposals are compared for overlap, they are *normalised* into a
canonical form to ensure that semantically equivalent proposals written in
different surface forms are recognised as equal.  Normalisation steps include:

    1. Case-fold and Unicode NFC normalisation
    2. Stop-word removal
    3. Stemming / lemmatisation (domain-specific)
    4. Sorted-token canonicalisation

The resulting *normal_form_hash* h(P) is a SHA-256 digest of the canonical
form.  Two proposals P₁, P₂ are *equivalent* iff:

    h(P₁) = h(P₂)  OR  Jaccard(tokens(P₁), tokens(P₂)) > 0.95

Data Relationships
-------------------
  InhabitantProposal  ←──contains──→  competing_proposals : list[str]
  FleetBid            ←──references─→  InhabitantProposal (via proposed_inhabitant)
  BackpressureSignal  ←──triggered by─→ overlap instability
  SemanticMove        ←──transforms──→  InhabitantProposal (source_state → target_state)
  NormalizedProposal  ←──wraps──────→  InhabitantProposal (canonical_form)

Thread Safety
--------------
All dataclasses are intended to be used within a single async event loop or
under explicit locking.  InhabitantProposal is *mutable* (status field changes)
while the other dataclasses are effectively immutable after construction.

Examples
---------
>>> from jugeo.generation.inhabitant_fleets.models import make_proposal, make_bid
>>> p = make_proposal("patch-1", "intro", "The system initialises.")
>>> p.status.value
'pending'
>>> p.accept()
>>> p.status.value
'accepted'

>>> b = make_bid("fleet-agent-7", "goal:coherence", "∀x.P(x)")
>>> b.compute_total_score()
0.36...

"""
from __future__ import annotations

import hashlib
import math
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, ClassVar

# ---------------------------------------------------------------------------
# Optional integration with the broader JuGeo trust model.
# If the trust module is available we use its TrustTier enum; otherwise we
# fall back to an IntEnum shim so this module remains standalone.
# ---------------------------------------------------------------------------
try:
    from jugeo.evidence.trust import TrustTier  # type: ignore[import]
except ImportError:
    from enum import IntEnum  # noqa: F811

    class TrustTier(IntEnum):  # type: ignore[no-redef]
        """Minimal shim for TrustTier when jugeo.evidence is unavailable.

        Tiers:
            PROPOSAL (1) – unreviewed, machine-generated candidate
            REVIEWED (2) – manually inspected by a human reviewer
            VERIFIED (3) – formally checked / consensus-accepted
        """

        PROPOSAL = 1
        REVIEWED = 2
        VERIFIED = 3


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class ProposalStatus(str, Enum):
    """Lifecycle status of an :class:`InhabitantProposal`.

    The status advances through a simple state machine::

        PENDING  →  ACCEPTED
                 ↘  REJECTED

    A proposal starts life as PENDING and is either ACCEPTED (it wins the
    fleet auction) or REJECTED (it loses to a competing proposal or is
    explicitly withdrawn).

    Using ``str`` as a mixin ensures that serialisation to JSON is trivial::

        >>> ProposalStatus.PENDING.value
        'pending'
        >>> import json; json.dumps(ProposalStatus.ACCEPTED)
        '"accepted"'
    """

    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class SeverityLevel(str, Enum):
    """Severity of a :class:`BackpressureSignal`.

    Severity determines how urgently the fleet coordinator must respond:

    LOW      – informational; no immediate action required
    MEDIUM   – advisory; consider throttling proposal rate
    HIGH     – warning; suspend new proposals until resolved
    CRITICAL – halt; freeze all fleet activity in affected patches

    The escalation ladder is strictly ordered::

        LOW < MEDIUM < HIGH < CRITICAL

    This ordering is used by :meth:`BackpressureSignal.escalate`.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    # Convenience class-level ordering map used by escalate()
    _ignore_ = ["_ORDER"]
    _ORDER: ClassVar[list[str]] = []  # populated after class body


# Populate the ordering after the class is defined
SeverityLevel._ORDER = [
    SeverityLevel.LOW,
    SeverityLevel.MEDIUM,
    SeverityLevel.HIGH,
    SeverityLevel.CRITICAL,
]


class MoveType(str, Enum):
    """The five semantic move types in Ch42 fleet semantics.

    Each move type describes *how* a fleet member wishes to change the
    inhabited state of a semantic patch:

    PROPOSE    – introduce a new candidate inhabitant (creates a new proposal)
    RETRACT    – withdraw a previously proposed inhabitant (marks as rejected)
    REFINE     – narrow the semantic content of an existing proposal
                 (subtype relationship: refined ⊆ original)
    GENERALIZE – broaden the semantic content of an existing proposal
                 (supertype relationship: generalised ⊇ original)
    SPECIALIZE – introduce a specialised sub-case of an existing proposal
                 (case-split: specialised handles a strict subset of inputs)

    Irreversibility note
    ~~~~~~~~~~~~~~~~~~~~
    REFINE and SPECIALIZE are *not* reversible in general because they lose
    information (they commit to a more specific interpretation).  PROPOSE,
    RETRACT and GENERALIZE are reversible.

    Usage::

        >>> MoveType.PROPOSE.value
        'propose'
        >>> MoveType.REFINE in {MoveType.REFINE, MoveType.SPECIALIZE}
        True
    """

    PROPOSE = "propose"
    RETRACT = "retract"
    REFINE = "refine"
    GENERALIZE = "generalize"
    SPECIALIZE = "specialize"


# ---------------------------------------------------------------------------
# InhabitantProposal
# ---------------------------------------------------------------------------


@dataclass
class InhabitantProposal:
    """A proposal from a fleet member to inhabit a semantic patch.

    An InhabitantProposal encodes the claim ``Γ ⊢ t : P`` where:
        • Γ  – the evidence context (captured by ``trust_tier`` and
               ``evidence_score``)
        • t  – the proposed inhabitant (``semantic_content``)
        • P  – the target patch (``patch_id``, ``section_label``)

    The proposal is *mutable* because its ``status`` changes as the fleet
    auction proceeds.  All other fields are effectively set at construction
    time and should not be modified directly.

    Scoring
    -------
    The proposal's score is::

        score = clamp(int(trust_tier) × evidence_score × (1 − 0.05 × |competing|),
                      0.0, 3.0)

    The penalty term ``0.05 × |competing|`` reflects the fact that a proposal
    facing many competitors is less likely to be unambiguously correct.

    Attributes
    ----------
    proposal_id : str
        Unique hex UUID for this proposal.
    patch_id : str
        Identifier of the semantic patch this proposal targets.
    section_label : str
        Human-readable label for the section / goal within the patch.
    semantic_content : str
        The actual semantic content being proposed as the inhabitant.
    proposer_id : str
        Identifier of the fleet member making the proposal.
    trust_tier : TrustTier
        Trust level of the proposing agent.
    evidence_score : float
        Self-assessed evidence quality ∈ [0, 1].
    competing_proposals : list[str]
        IDs of other proposals competing for the same patch.
    status : ProposalStatus
        Current lifecycle status (PENDING → ACCEPTED | REJECTED).
    created_at : float
        Unix timestamp of creation.
    metadata : dict[str, Any]
        Arbitrary extension metadata (e.g. accepted_at, rejected_at, reason).

    Examples
    --------
    >>> p = make_proposal("patch-A", "section-1", "All elements are typed.")
    >>> p.score()  # trust=1, evidence=0.5, competitors=0
    0.5
    >>> p2 = make_proposal("patch-A", "section-1", "Some elements are typed.")
    >>> p.compete_with(p2)
    >>> p.score()  # penalty for 1 competitor
    0.475
    """

    # --- Required fields (no defaults) ---
    proposal_id: str
    patch_id: str
    section_label: str
    semantic_content: str
    proposer_id: str
    trust_tier: TrustTier

    # --- Optional fields with defaults ---
    evidence_score: float = 0.5
    competing_proposals: list[str] = field(default_factory=list)
    status: ProposalStatus = field(default=ProposalStatus.PENDING)
    created_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def accept(self) -> "InhabitantProposal":
        """Accept this proposal, marking it as the winning inhabitant.

        Sets ``status`` to ACCEPTED and records ``accepted_at`` in
        ``metadata``.  Calling this on an already-accepted or rejected
        proposal is a no-op (idempotent).

        Examples
        --------
        >>> p = make_proposal("patch-1", "s1", "content")
        >>> p.accept()
        >>> p.status
        <ProposalStatus.ACCEPTED: 'accepted'>
        >>> "accepted_at" in p.metadata
        True
        """
        if self.status is ProposalStatus.ACCEPTED:
            return self
        self.status = ProposalStatus.ACCEPTED
        self.metadata["accepted_at"] = time.time()
        return self

    def reject(self, reason: str = "") -> "InhabitantProposal":
        """Reject this proposal.

        Sets ``status`` to REJECTED and records ``rejected_at`` plus an
        optional ``reason`` string in ``metadata``.

        Parameters
        ----------
        reason : str
            Human-readable rejection reason (optional).

        Examples
        --------
        >>> p = make_proposal("patch-1", "s1", "content")
        >>> p.reject("Beaten by higher-score proposal")
        >>> p.status
        <ProposalStatus.REJECTED: 'rejected'>
        >>> p.metadata["reason"]
        'Beaten by higher-score proposal'
        """
        self.status = ProposalStatus.REJECTED
        self.metadata["rejected_at"] = time.time()
        if reason:
            self.metadata["reason"] = reason
        return self

    # ------------------------------------------------------------------
    # Competition
    # ------------------------------------------------------------------

    def compete_with(self, other: InhabitantProposal) -> None:
        """Register a mutual competition relationship with *other*.

        After this call, ``self.competing_proposals`` contains
        ``other.proposal_id`` and vice-versa.  Duplicate registrations are
        silently ignored.

        Parameters
        ----------
        other : InhabitantProposal
            The competing proposal.

        Note
        ----
        This mutates both proposals.  In multi-threaded contexts, callers
        are responsible for appropriate locking.

        Examples
        --------
        >>> p1 = make_proposal("patch-1", "s1", "A")
        >>> p2 = make_proposal("patch-1", "s1", "B")
        >>> p1.compete_with(p2)
        >>> p2.proposal_id in p1.competing_proposals
        True
        >>> p1.proposal_id in p2.competing_proposals
        True
        """
        if other.proposal_id not in self.competing_proposals:
            self.competing_proposals.append(other.proposal_id)
        if self.proposal_id not in other.competing_proposals:
            other.competing_proposals.append(self.proposal_id)

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    def score(self) -> float:
        """Compute the proposal's auction score.

        The score is::

            s = int(trust_tier) × evidence_score × (1.0 − 0.05 × n_competitors)

        clamped to [0.0, 3.0].

        Returns
        -------
        float
            Score in [0.0, 3.0].

        Examples
        --------
        >>> p = make_proposal("p1", "s1", "x", evidence_score=1.0)
        >>> p.score()  # TrustTier.PROPOSAL=1, no competitors
        1.0
        """
        raw = (
            int(self.trust_tier)
            * self.evidence_score
            * max(0.0, 1.0 - 0.05 * len(self.competing_proposals))
        )
        return max(0.0, min(3.0, raw))

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise this proposal to a plain dictionary.

        All enum values are converted to their string ``.value``; the
        ``trust_tier`` is stored as its integer value for portability.

        Returns
        -------
        dict[str, Any]
            JSON-serialisable dictionary.
        """
        return {
            "proposal_id": self.proposal_id,
            "patch_id": self.patch_id,
            "section_label": self.section_label,
            "semantic_content": self.semantic_content,
            "proposer_id": self.proposer_id,
            "trust_tier": int(self.trust_tier),
            "evidence_score": self.evidence_score,
            "competing_proposals": list(self.competing_proposals),
            "status": self.status.value,
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> InhabitantProposal:
        """Deserialise an InhabitantProposal from a plain dictionary.

        Handles enum reconstruction for ``status`` and ``trust_tier``.

        Parameters
        ----------
        d : dict[str, Any]
            Dictionary previously produced by :meth:`to_dict`.

        Returns
        -------
        InhabitantProposal
        """
        return cls(
            proposal_id=d["proposal_id"],
            patch_id=d["patch_id"],
            section_label=d["section_label"],
            semantic_content=d["semantic_content"],
            proposer_id=d["proposer_id"],
            trust_tier=TrustTier(int(d["trust_tier"])),
            evidence_score=float(d["evidence_score"]),
            competing_proposals=list(d.get("competing_proposals", [])),
            status=ProposalStatus(d.get("status", "pending")),
            created_at=float(d.get("created_at", 0.0)),
            metadata=dict(d.get("metadata", {})),
        )

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validation_errors(self) -> list[str]:
        """Validate this proposal and return a list of error messages.

        An empty list means the proposal is valid.

        Checks
        ------
        • ``semantic_content`` is non-empty
        • ``evidence_score`` ∈ [0, 1]
        • ``trust_tier`` is a valid TrustTier member
        • ``patch_id`` is non-empty
        • ``proposal_id`` is non-empty

        Returns
        -------
        list[str]
            List of human-readable error strings (empty ⇒ valid).
        """
        errors: list[str] = []
        if not self.semantic_content.strip():
            errors.append("semantic_content must not be empty")
        if not (0.0 <= self.evidence_score <= 1.0):
            errors.append(
                f"evidence_score {self.evidence_score!r} is not in [0, 1]"
            )
        if not isinstance(self.trust_tier, TrustTier):
            errors.append(f"trust_tier {self.trust_tier!r} is not a TrustTier")
        if not self.patch_id.strip():
            errors.append("patch_id must not be empty")
        if not self.proposal_id.strip():
            errors.append("proposal_id must not be empty")
        return errors

    def validate(self) -> bool:
        """Legacy boolean validation API."""
        return len(self.validation_errors()) == 0

    # ------------------------------------------------------------------
    # Human-readable helpers
    # ------------------------------------------------------------------

    def summary(self) -> str:
        """Return a one-line human-readable summary of this proposal.

        Format::

            Proposal {id[:8]} [{status}] on {patch_id}: {content[:40]}...

        Returns
        -------
        str
        """
        snippet = self.semantic_content[:40]
        if len(self.semantic_content) > 40:
            snippet += "…"
        return (
            f"Proposal {self.proposal_id[:8]} [{self.status.value}]"
            f" on {self.patch_id}: {snippet}"
        )

    def __repr__(self) -> str:
        return (
            f"InhabitantProposal("
            f"proposal_id={self.proposal_id[:8]!r}, "
            f"patch_id={self.patch_id!r}, "
            f"status={self.status.value!r}, "
            f"score={self.score():.3f})"
        )

    def __eq__(self, other: object) -> bool:
        """Equality is determined solely by ``proposal_id``."""
        if isinstance(other, InhabitantProposal):
            return self.proposal_id == other.proposal_id
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self.proposal_id)


# ---------------------------------------------------------------------------
# FleetBid
# ---------------------------------------------------------------------------


@dataclass
class FleetBid:
    """A sealed bid in the fleet auction mechanism.

    Each fleet member submits a FleetBid to compete for the right to inhabit
    a semantic patch.  The auction selects the bid maximising::

        total_score = bid_score × overlap_compatibility_score × backpressure_tolerance

    subject to cross-bid compatibility constraints.

    Bid Fields
    ----------
    bid_id : str
        Unique hex UUID for this bid.
    fleet_member_id : str
        ID of the fleet member placing this bid.
    goal_label : str
        The goal or objective this bid is trying to satisfy.
    proposed_inhabitant : str
        The semantic content being proposed as the inhabitant.
    bid_score : float
        Self-assessed bid quality ∈ [0, ∞).  Typically normalised to [0, 1]
        before use, but the auction may accept raw scores.
    resource_estimate : int
        Estimated resource cost (arbitrary units ≥ 0).
    overlap_compatibility_score : float
        How well this bid co-exists with other inhabitants in the patch ∈ [0, 1].
    backpressure_tolerance : float
        How well the fleet member tolerates backpressure signals ∈ [0, 1].
        A value of 0 means the member halts on any backpressure.
    metadata : dict[str, Any]
        Extension metadata (timestamps, provenance, etc.).

    Examples
    --------
    >>> b = make_bid("agent-3", "goal:soundness", "∀x. typed(x)")
    >>> 0.0 <= b.compute_total_score() <= 1.0
    True
    """

    # --- Required (no-default) fields ---
    bid_id: str
    fleet_member_id: str
    goal_label: str
    proposed_inhabitant: str
    bid_score: float
    resource_estimate: int
    overlap_compatibility_score: float
    backpressure_tolerance: float

    # --- Optional field ---
    metadata: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    def compute_total_score(self) -> float:
        """Compute the composite auction score for this bid.

        Formula::

            total = bid_score × overlap_compatibility_score × backpressure_tolerance

        Returns
        -------
        float
            Total score (unbounded above, but typically ∈ [0, 1]).

        Examples
        --------
        >>> b = make_bid("m1", "g1", "t1")
        >>> b.bid_score = 1.0; b.overlap_compatibility_score = 1.0
        >>> b.backpressure_tolerance = 1.0
        >>> b.compute_total_score()
        1.0
        """
        return (
            self.bid_score
            * self.overlap_compatibility_score
            * self.backpressure_tolerance
        )

    # ------------------------------------------------------------------
    # Compatibility
    # ------------------------------------------------------------------

    def is_compatible_with(self, other: FleetBid) -> bool:
        """Determine whether two bids are mutually compatible.

        Two bids are compatible if:
          • They come from different fleet members (a member cannot win both
            sides of its own auction)
          • Their combined overlap_compatibility_score exceeds 1.0, meaning
            together they provide more than full patch coverage

        Parameters
        ----------
        other : FleetBid
            The other bid to check against.

        Returns
        -------
        bool

        Examples
        --------
        >>> b1 = make_bid("m1", "g1", "t1")
        >>> b2 = make_bid("m2", "g1", "t2")
        >>> # Default compat scores are 0.8; 0.8+0.8 > 1.0 → True
        >>> b1.is_compatible_with(b2)
        True
        """
        return (
            self.fleet_member_id != other.fleet_member_id
            and (self.overlap_compatibility_score + other.overlap_compatibility_score)
            > 1.0
        )

    # ------------------------------------------------------------------
    # Serialisation & validation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise this bid to a plain dictionary."""
        return {
            "bid_id": self.bid_id,
            "fleet_member_id": self.fleet_member_id,
            "goal_label": self.goal_label,
            "proposed_inhabitant": self.proposed_inhabitant,
            "bid_score": self.bid_score,
            "resource_estimate": self.resource_estimate,
            "overlap_compatibility_score": self.overlap_compatibility_score,
            "backpressure_tolerance": self.backpressure_tolerance,
            "metadata": dict(self.metadata),
        }

    def validation_errors(self) -> list[str]:
        """Validate this bid and return a list of error messages.

        Checks
        ------
        • bid_score ≥ 0
        • resource_estimate ≥ 0
        • overlap_compatibility_score ∈ [0, 1]
        • backpressure_tolerance ∈ [0, 1]
        • fleet_member_id non-empty
        • goal_label non-empty

        Returns
        -------
        list[str]
            Empty list ⇒ bid is valid.
        """
        errors: list[str] = []
        if self.bid_score < 0:
            errors.append(f"bid_score {self.bid_score!r} must be ≥ 0")
        if self.resource_estimate < 0:
            errors.append(
                f"resource_estimate {self.resource_estimate!r} must be ≥ 0"
            )
        if not (0.0 <= self.overlap_compatibility_score <= 1.0):
            errors.append(
                f"overlap_compatibility_score {self.overlap_compatibility_score!r}"
                " is not in [0, 1]"
            )
        if not (0.0 <= self.backpressure_tolerance <= 1.0):
            errors.append(
                f"backpressure_tolerance {self.backpressure_tolerance!r}"
                " is not in [0, 1]"
            )
        if not self.fleet_member_id.strip():
            errors.append("fleet_member_id must not be empty")
        if not self.goal_label.strip():
            errors.append("goal_label must not be empty")
        return errors

    def validate(self) -> bool:
        """Legacy boolean validation API."""
        return len(self.validation_errors()) == 0

    def summary(self) -> str:
        """Return a one-line human-readable summary of this bid.

        Returns
        -------
        str
        """
        snippet = self.proposed_inhabitant[:40]
        if len(self.proposed_inhabitant) > 40:
            snippet += "…"
        return (
            f"Bid {self.bid_id[:8]} by {self.fleet_member_id}"
            f" for {self.goal_label!r}: {snippet}"
            f" (score={self.compute_total_score():.3f})"
        )

    def __repr__(self) -> str:
        return (
            f"FleetBid(bid_id={self.bid_id[:8]!r}, "
            f"member={self.fleet_member_id!r}, "
            f"total_score={self.compute_total_score():.3f})"
        )


# ---------------------------------------------------------------------------
# BackpressureSignal
# ---------------------------------------------------------------------------


@dataclass
class BackpressureSignal:
    """A signal emitted when overlap instability exceeds a threshold.

    Backpressure is the fleet's mechanism for detecting and resolving
    *over-competition*: situations where too many inhabitants are competing
    for the same patch, causing semantic instability.

    When the instability score σ(P) of a patch P exceeds the threshold θ,
    a BackpressureSignal is emitted and broadcast to all affected patches.
    Fleet members receiving this signal must:

      • Reduce their proposal rate (throttle)
      • Withdraw low-confidence proposals (RETRACT)
      • Merge overlapping proposals (GENERALIZE)

    Cascade Propagation
    -------------------
    Backpressure can cascade: a signal from patch A may cause patches B and C
    to also exceed their thresholds, triggering further signals.  The
    ``target_patches`` field records all patches affected by the cascade.

    Attributes
    ----------
    signal_id : str
        Unique identifier for this signal.
    source_patch : str
        The patch that originally triggered the instability.
    target_patches : list[str]
        Patches to which this signal is broadcast.
    instability_score : float
        Measured instability ∈ [0, 1] (where 1 is maximum instability).
    threshold : float
        The threshold that was exceeded to trigger this signal.
    severity : SeverityLevel
        How severe the instability is (LOW / MEDIUM / HIGH / CRITICAL).
    timestamp : float
        Unix timestamp of signal creation.
    remediation_hints : list[str]
        Suggested remediation actions for fleet members.

    Examples
    --------
    >>> s = make_signal("patch-X", ["patch-Y", "patch-Z"], instability_score=0.95)
    >>> s.is_critical()
    True
    >>> s.affects_patch("patch-Y")
    True
    >>> s.affects_patch("patch-W")
    False
    """

    # --- Required fields ---
    signal_id: str
    source_patch: str
    target_patches: list[str]
    instability_score: float
    threshold: float
    severity: SeverityLevel

    # --- Optional fields ---
    timestamp: float = field(default_factory=time.time)
    remediation_hints: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def is_critical(self) -> bool:
        """Return True if this signal is CRITICAL severity.

        A CRITICAL signal requires an immediate fleet-wide halt in the
        affected patches.

        Returns
        -------
        bool

        Examples
        --------
        >>> s = make_signal("p1", [], 0.95)
        >>> s.is_critical()
        True
        """
        return self.severity is SeverityLevel.CRITICAL

    def affects_patch(self, patch: str) -> bool:
        """Return True if *patch* is the source or a broadcast target.

        Parameters
        ----------
        patch : str
            Patch identifier to check.

        Returns
        -------
        bool
        """
        return patch == self.source_patch or patch in self.target_patches

    # ------------------------------------------------------------------
    # Broadcast & escalation
    # ------------------------------------------------------------------

    def broadcast_to(self, patches: list[str]) -> BackpressureSignal:
        """Extend this signal to cover additional *patches* and return ``self``.

        Legacy callers expect this operation to mutate in place.

        Parameters
        ----------
        patches : list[str]
            Additional patch IDs to include in the broadcast.

        Returns
        -------
        BackpressureSignal
            This signal after mutation.

        Examples
        --------
        >>> s = make_signal("p1", ["p2"])
        >>> s2 = s.broadcast_to(["p3", "p4"])
        >>> "p3" in s2.target_patches and "p2" in s2.target_patches
        True
        >>> s.signal_id == s2.signal_id  # new ID each time
        False
        """
        self.target_patches = list(dict.fromkeys(self.target_patches + list(patches)))
        return self

    def escalate(self) -> BackpressureSignal:
        """Return a new signal with severity one level higher.

        If already CRITICAL, the severity stays CRITICAL.  The
        instability_score is bumped by 0.1 (capped at 1.0).

        Returns
        -------
        BackpressureSignal
            Escalated signal (original is not mutated).

        Examples
        --------
        >>> s = make_signal("p1", [], 0.5)
        >>> s.severity
        <SeverityLevel.MEDIUM: 'medium'>
        >>> s2 = s.escalate()
        >>> s2.severity
        <SeverityLevel.HIGH: 'high'>
        """
        order = SeverityLevel._ORDER
        current_idx = order.index(self.severity)
        next_severity = order[min(current_idx + 1, len(order) - 1)]
        return BackpressureSignal(
            signal_id=uuid.uuid4().hex,
            source_patch=self.source_patch,
            target_patches=list(self.target_patches),
            instability_score=min(1.0, self.instability_score + 0.1),
            threshold=self.threshold,
            severity=next_severity,
            timestamp=time.time(),
            remediation_hints=list(self.remediation_hints),
        )

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise this signal to a plain dictionary."""
        return {
            "signal_id": self.signal_id,
            "source_patch": self.source_patch,
            "target_patches": list(self.target_patches),
            "instability_score": self.instability_score,
            "threshold": self.threshold,
            "severity": self.severity.value,
            "timestamp": self.timestamp,
            "remediation_hints": list(self.remediation_hints),
        }

    def __repr__(self) -> str:
        return (
            f"BackpressureSignal("
            f"signal_id={self.signal_id[:8]!r}, "
            f"source={self.source_patch!r}, "
            f"severity={self.severity.value!r}, "
            f"instability={self.instability_score:.3f})"
        )


# ---------------------------------------------------------------------------
# SemanticMove
# ---------------------------------------------------------------------------


@dataclass
class SemanticMove:
    """A move in the fleet's semantic state space.

    A SemanticMove represents a *transition* in the semantic space from a
    ``source_state`` to a ``target_state``, labelled with a ``move_type``
    that determines the nature of the transformation.

    Moves are composable: if move M₁ takes state s → t and move M₂ takes
    t → u, then M₁ ∘ M₂ is a valid composite move taking s → u.

    Semantic Distance
    -----------------
    The semantic distance δ of a move measures how far the move travels in
    semantic space.  For a composition M₁ ∘ M₂::

        δ(M₁ ∘ M₂) = δ(M₁) + δ(M₂)

    This satisfies the triangle inequality (Ch42 Lemma 3.1), ensuring that
    the semantic metric is consistent.

    Validity Certificates
    ---------------------
    Each move carries a ``validity_certificate``: a short token (or hash)
    attesting that the move was validated by the fleet's type-checker.  For
    composed moves the certificate is the composition of the two constituent
    certificates: ``cert₁ ∘ cert₂``.

    Reversibility
    -------------
    A move is *reversible* if there exists an inverse move M⁻¹ such that
    M ∘ M⁻¹ = id.  REFINE and SPECIALIZE are NOT reversible because they
    discard information; the other move types are reversible.

    Attributes
    ----------
    move_id : str
        Unique identifier.
    move_type : MoveType
        The semantic move type.
    source_state : str
        State identifier before the move.
    target_state : str
        State identifier after the move.
    semantic_distance : float
        Non-negative distance between source and target in semantic space.
    validity_certificate : str
        Short token certifying the move has been type-checked.
    overlap_impact : float
        How much this move affects patch-overlap instability ∈ [0, 1].
    move_cost : float
        Computational / resource cost of applying this move.

    Examples
    --------
    >>> m = make_move(MoveType.PROPOSE, "∅", "∀x.P(x)")
    >>> m.is_reversible()
    True
    >>> m2 = make_move(MoveType.REFINE, "∀x.P(x)", "∀x.Q(x)")
    >>> m2.is_reversible()
    False
    """

    move_id: str
    move_type: MoveType
    source_state: str
    target_state: str
    semantic_distance: float
    validity_certificate: str
    overlap_impact: float
    move_cost: float

    # ------------------------------------------------------------------
    # Application
    # ------------------------------------------------------------------

    def apply_to(self, state: Any) -> Any:
        """Apply this move to *state* and return the resulting state.

        If *state* matches ``source_state`` exactly, the move transitions
        to ``target_state``.  Otherwise, the move is applied *speculatively*
        by appending an annotation to *state*.

        Parameters
        ----------
        state : str
            Current semantic state string.

        Returns
        -------
        str
            New semantic state after applying the move.

        Examples
        --------
        >>> m = make_move(MoveType.PROPOSE, "A", "B")
        >>> m.apply_to("A")
        'B'
        >>> m.apply_to("C")
        'C[propose:B]'
        """
        if isinstance(state, dict):
            new_state = dict(state)
            new_state["move_type"] = self.move_type.value
            new_state["target_state"] = self.target_state
            return new_state
        if state == self.source_state:
            return self.target_state
        return f"{state}[{self.move_type.value}:{self.target_state}]"

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    def is_reversible(self) -> bool:
        """Return True if this move type is semantically reversible.

        REFINE and SPECIALIZE are irreversible (they lose information).
        All other move types (PROPOSE, RETRACT, GENERALIZE) are reversible.

        Returns
        -------
        bool

        Examples
        --------
        >>> make_move(MoveType.REFINE, "A", "B").is_reversible()
        False
        >>> make_move(MoveType.GENERALIZE, "A", "B").is_reversible()
        True
        """
        return self.move_type not in {MoveType.REFINE, MoveType.SPECIALIZE}

    # ------------------------------------------------------------------
    # Composition
    # ------------------------------------------------------------------

    def compose_with(self, other: SemanticMove) -> SemanticMove:
        """Return the composition of *self* followed by *other*.

        Composition semantics::

            (M₁ ∘ M₂).source_state  = M₁.source_state
            (M₁ ∘ M₂).target_state  = M₂.target_state
            (M₁ ∘ M₂).δ             = M₁.δ + M₂.δ
            (M₁ ∘ M₂).cert          = M₁.cert ∘ M₂.cert
            (M₁ ∘ M₂).overlap       = max(M₁.overlap, M₂.overlap)
            (M₁ ∘ M₂).cost          = M₁.cost + M₂.cost

        The ``move_type`` of the composition is that of *other* (the
        outermost move).

        Parameters
        ----------
        other : SemanticMove
            The move to compose with (applied after *self*).

        Returns
        -------
        SemanticMove
            New composite move (neither input is mutated).

        Examples
        --------
        >>> m1 = make_move(MoveType.PROPOSE, "A", "B")
        >>> m2 = make_move(MoveType.REFINE, "B", "C")
        >>> comp = m1.compose_with(m2)
        >>> comp.source_state, comp.target_state
        ('A', 'C')
        >>> comp.semantic_distance == m1.semantic_distance + m2.semantic_distance
        True
        """
        return SemanticMove(
            move_id=uuid.uuid4().hex,
            move_type=other.move_type,
            source_state=self.source_state,
            target_state=other.target_state,
            semantic_distance=self.semantic_distance + other.semantic_distance,
            validity_certificate=(
                f"{self.validity_certificate}∘{other.validity_certificate}"
            ),
            overlap_impact=max(self.overlap_impact, other.overlap_impact),
            move_cost=self.move_cost + other.move_cost,
        )

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise this move to a plain dictionary."""
        return {
            "move_id": self.move_id,
            "move_type": self.move_type.value,
            "source_state": self.source_state,
            "target_state": self.target_state,
            "semantic_distance": self.semantic_distance,
            "validity_certificate": self.validity_certificate,
            "overlap_impact": self.overlap_impact,
            "move_cost": self.move_cost,
        }

    def __repr__(self) -> str:
        return (
            f"SemanticMove("
            f"move_id={self.move_id[:8]!r}, "
            f"type={self.move_type.value!r}, "
            f"{self.source_state!r}→{self.target_state!r}, "
            f"δ={self.semantic_distance:.3f})"
        )


# ---------------------------------------------------------------------------
# NormalizedProposal
# ---------------------------------------------------------------------------


@dataclass
class NormalizedProposal:
    """Canonical form of an InhabitantProposal for semantic comparison.

    Before the fleet coordinator can determine whether two proposals are
    equivalent, redundant, or genuinely competing, it must normalise them
    into a canonical form.  This class wraps an :class:`InhabitantProposal`
    along with the result of the normalisation process.

    Normalisation Pipeline
    ----------------------
    The normalisation steps captured in ``normalization_steps`` typically
    include:

      1. Unicode NFC normalisation
      2. Lower-casing
      3. Punctuation stripping
      4. Stop-word removal (domain-specific)
      5. Stemming or lemmatisation
      6. Token sorting (to ensure canonical order)
      7. Whitespace collapsing

    The ``normal_form_hash`` is the SHA-256 hex digest of the resulting
    canonical string.  This hash is the primary key for equivalence checks.

    Jaccard Similarity Fallback
    ----------------------------
    When two proposals have different hashes (which may happen after
    surface-level variation that the normaliser did not collapse), the
    coordinator falls back to computing the Jaccard similarity of their
    token sets::

        J(A, B) = |A ∩ B| / |A ∪ B|

    If J > 0.95, the proposals are considered equivalent.

    Attributes
    ----------
    normalized_id : str
        Unique ID for this normalised wrapper.
    original_proposal : InhabitantProposal
        The original proposal before normalisation.
    canonical_form : str
        The normalised canonical string.
    normalization_steps : list[str]
        Ordered list of normalisation step descriptions.
    comparability_score : float
        Confidence in the normalisation ∈ [0, 1].
    normal_form_hash : str
        SHA-256 hex digest of ``canonical_form``.

    Examples
    --------
    >>> p = make_proposal("patch-1", "s1", "All elements are typed.")
    >>> nf = NormalizedProposal(
    ...     normalized_id=uuid.uuid4().hex,
    ...     original_proposal=p,
    ...     canonical_form="element type",
    ...     normalization_steps=["lower", "stop-words", "sort"],
    ...     comparability_score=0.9,
    ...     normal_form_hash=hashlib.sha256(b"element type").hexdigest(),
    ... )
    >>> nf.is_equivalent_to(nf)
    True
    """

    normalized_id: str
    original_proposal: InhabitantProposal
    canonical_form: str
    normalization_steps: list[str]
    comparability_score: float
    normal_form_hash: str

    # ------------------------------------------------------------------
    # Comparison
    # ------------------------------------------------------------------

    def _canonical_text(self) -> str:
        if isinstance(self.canonical_form, str):
            return self.canonical_form
        if isinstance(self.canonical_form, dict):
            return " ".join(str(value) for key, value in sorted(self.canonical_form.items()))
        return str(self.canonical_form)

    def compare_with(self, other: NormalizedProposal) -> float:
        """Compute semantic similarity with *other* in [0, 1].

        First checks for exact hash equality (returns 1.0).  Otherwise
        falls back to Jaccard similarity on word tokens.

        Parameters
        ----------
        other : NormalizedProposal
            The proposal to compare against.

        Returns
        -------
        float
            Similarity score in [0.0, 1.0].

        Examples
        --------
        >>> # Two identical proposals should have similarity 1.0
        >>> # (assuming same canonical form)
        """
        if self.normal_form_hash == other.normal_form_hash:
            return 1.0
        tokens_self = set(self._canonical_text().lower().split())
        tokens_other = set(other._canonical_text().lower().split())
        if not tokens_self and not tokens_other:
            return 1.0
        union = tokens_self | tokens_other
        intersection = tokens_self & tokens_other
        if not union:
            return 0.0
        return float(len(intersection)) / float(len(union))

    def is_equivalent_to(self, other: NormalizedProposal) -> bool:
        """Return True if *self* and *other* represent the same semantic content.

        Equivalence holds when:
          • The normal_form_hashes are identical, OR
          • The Jaccard similarity exceeds 0.95

        Parameters
        ----------
        other : NormalizedProposal

        Returns
        -------
        bool

        Examples
        --------
        >>> p = make_proposal("p1", "s1", "x")
        >>> # Two normalizations of the same content should be equivalent
        """
        return self.normal_form_hash == other.normal_form_hash

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise this normalised proposal to a plain dictionary."""
        return {
            "normalized_id": self.normalized_id,
            "original_proposal": self.original_proposal.to_dict(),
            "canonical_form": self.canonical_form,
            "normalization_steps": list(self.normalization_steps),
            "comparability_score": self.comparability_score,
            "normal_form_hash": self.normal_form_hash,
        }

    def __repr__(self) -> str:
        return (
            f"NormalizedProposal("
            f"normalized_id={self.normalized_id[:8]!r}, "
            f"hash={self.normal_form_hash[:12]!r}, "
            f"comparability={self.comparability_score:.3f})"
        )

    def __eq__(self, other: object) -> bool:
        if isinstance(other, NormalizedProposal):
            return self.normalized_id == other.normalized_id
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self.normalized_id)


# ---------------------------------------------------------------------------
# Factory functions
# ---------------------------------------------------------------------------


def make_proposal(
    patch_id: str,
    section_label: str,
    content: str,
    trust_tier: TrustTier = TrustTier.PROPOSAL,
    evidence_score: float = 0.5,
) -> InhabitantProposal:
    """Convenience factory for creating an :class:`InhabitantProposal`.

    All fields not specified here are given sensible defaults:
      • ``proposal_id`` – fresh UUID4 hex
      • ``proposer_id`` – "auto" (to be overwritten by the fleet member)
      • ``competing_proposals`` – empty list
      • ``status`` – PENDING
      • ``created_at`` – current time

    Parameters
    ----------
    patch_id : str
        Target patch identifier.
    section_label : str
        Section / goal label within the patch.
    content : str
        Semantic content of the proposal.
    trust_tier : TrustTier
        Trust level; defaults to PROPOSAL (lowest).
    evidence_score : float
        Evidence quality ∈ [0, 1]; defaults to 0.5.

    Returns
    -------
    InhabitantProposal

    Examples
    --------
    >>> p = make_proposal("patch-42", "intro", "The universe exists.")
    >>> p.status
    <ProposalStatus.PENDING: 'pending'>
    >>> p.proposer_id
    'auto'
    """
    return InhabitantProposal(
        proposal_id=uuid.uuid4().hex,
        patch_id=patch_id,
        section_label=section_label,
        semantic_content=content,
        proposer_id="auto",
        trust_tier=trust_tier,
        evidence_score=evidence_score,
    )


def make_bid(
    fleet_member_id: str,
    goal_label: str,
    inhabitant: str,
) -> FleetBid:
    """Convenience factory for creating a :class:`FleetBid`.

    Default scores are moderate (0.5 bid, 0.8 compat, 0.9 bp_tolerance)
    representing a baseline fleet member with reasonable confidence.

    Parameters
    ----------
    fleet_member_id : str
        ID of the fleet member placing the bid.
    goal_label : str
        Goal or objective the bid satisfies.
    inhabitant : str
        The semantic content proposed as the inhabitant.

    Returns
    -------
    FleetBid

    Examples
    --------
    >>> b = make_bid("agent-7", "goal:completeness", "∃x.P(x)")
    >>> b.bid_score
    0.5
    >>> b.compute_total_score()  # 0.5 * 0.8 * 0.9
    0.36...
    """
    return FleetBid(
        bid_id=uuid.uuid4().hex,
        fleet_member_id=fleet_member_id,
        goal_label=goal_label,
        proposed_inhabitant=inhabitant,
        bid_score=0.5,
        resource_estimate=1,
        overlap_compatibility_score=0.8,
        backpressure_tolerance=0.9,
    )


def make_signal(
    source_patch: str,
    target_patches: list[str],
    instability_score: float = 0.8,
) -> BackpressureSignal:
    """Convenience factory for creating a :class:`BackpressureSignal`.

    Automatically determines severity from instability_score::

        > 0.9  → CRITICAL
        > 0.7  → HIGH
        > 0.4  → MEDIUM
        else   → LOW

    Parameters
    ----------
    source_patch : str
        Patch that triggered the instability.
    target_patches : list[str]
        Patches affected by the signal.
    instability_score : float
        Measured instability ∈ [0, 1]; defaults to 0.8.

    Returns
    -------
    BackpressureSignal

    Examples
    --------
    >>> s = make_signal("patch-1", ["patch-2"], 0.95)
    >>> s.is_critical()
    True
    >>> s = make_signal("patch-1", [], 0.3)
    >>> s.severity
    <SeverityLevel.LOW: 'low'>
    """
    if instability_score > 0.9:
        severity = SeverityLevel.CRITICAL
    elif instability_score > 0.7:
        severity = SeverityLevel.HIGH
    elif instability_score > 0.4:
        severity = SeverityLevel.MEDIUM
    else:
        severity = SeverityLevel.LOW

    return BackpressureSignal(
        signal_id=uuid.uuid4().hex,
        source_patch=source_patch,
        target_patches=target_patches,
        instability_score=instability_score,
        threshold=0.7,
        severity=severity,
    )


def make_move(
    move_type: MoveType,
    source_state: str,
    target_state: str,
) -> SemanticMove:
    """Convenience factory for creating a :class:`SemanticMove`.

    Default parameters represent a short, low-impact move of unit cost.

    Parameters
    ----------
    move_type : MoveType
        The type of semantic move.
    source_state : str
        Starting semantic state.
    target_state : str
        Resulting semantic state.

    Returns
    -------
    SemanticMove

    Examples
    --------
    >>> m = make_move(MoveType.PROPOSE, "∅", "∀x.typed(x)")
    >>> m.apply_to("∅")
    '∀x.typed(x)'
    >>> m.move_cost
    1.0
    """
    return SemanticMove(
        move_id=uuid.uuid4().hex,
        move_type=move_type,
        source_state=source_state,
        target_state=target_state,
        semantic_distance=0.3,
        validity_certificate=uuid.uuid4().hex[:8],
        overlap_impact=0.1,
        move_cost=1.0,
    )


# ---------------------------------------------------------------------------
# Module-level exports
# ---------------------------------------------------------------------------

__all__ = [
    # Enumerations
    "ProposalStatus",
    "SeverityLevel",
    "MoveType",
    "TrustTier",
    # Dataclasses
    "InhabitantProposal",
    "FleetBid",
    "BackpressureSignal",
    "SemanticMove",
    "NormalizedProposal",
    # Factories
    "make_proposal",
    "make_bid",
    "make_signal",
    "make_move",
]
