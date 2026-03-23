"""
generation_as_extension_partial_se.py
==========================================

Generation as Extension of Partial Sections (s04)
---------------------------------------------------

This module implements the theory and machinery for **generation-as-extension** of partial
sections within the Jugeo specification-satisfaction framework.  A *partial section* is a
section that covers some—but not all—of the coordinates required by a specification.  The
coordinates that are not yet covered are called *holes*.  One principled strategy for filling
holes is to *generate* candidate judgments (via an LLM, a template engine, rule-based systems,
or manual edit) and then *extend* the partial section with those generated judgments.

Key theoretical commitments
~~~~~~~~~~~~~~~~~~~~~~~~~~~
* Judgments are 8-tuples ``(c, φ, A, E, O, B, T, Π)`` where:

  - ``c``   — coordinate (where in the specification the judgment applies)
  - ``φ``   — formula / type being judged
  - ``A``   — author (who produced the judgment)
  - ``E``   — evidence bundle
  - ``O``   — local obligations that the judgment must satisfy
  - ``B``   — blocking conditions (conditions that prevent acceptance)
  - ``T``   — trust tier (one of VERIFIED / TRUSTED / PROPOSED / UNVERIFIED)
  - ``Π``   — provenance (how the judgment was derived)

* **Trust tiers are categorical strings**, never floats.  Generated proposals always enter at
  the ``PROPOSED`` tier and must be explicitly promoted by a reviewer before reaching
  ``TRUSTED`` or ``VERIFIED``.

* **Generation is an extension attempt**: a proposal at hole coordinate ``c`` must be
  *compatible* with every authored judgment already present in the partial section, and must
  satisfy the local obligations ``O(c)`` declared for that hole.

* Specifications are *target sections* (mappings from coordinates to judgment shapes), not
  boolean predicates.

# copilot: s04 generation-as-extension of partial sections — do not remove this tag.

References
~~~~~~~~~~
* Jugeo Theory Document §4 — Partial Sections and Hole Semantics
* Jugeo Theory Document §7 — Trust Tiers and Promotion Paths
* Jugeo Theory Document §9 — Generation Strategies
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Standard-library imports
# ---------------------------------------------------------------------------
import enum
import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# ---------------------------------------------------------------------------
# Jugeo internal imports — all guarded so the module is importable standalone
# ---------------------------------------------------------------------------
try:
    from jugeo.core.coordinates import Coordinate  # type: ignore[import]
except ImportError:
    Coordinate: Any = Any  # type: ignore[assignment,misc]

try:
    from jugeo.core.judgment import Judgment  # type: ignore[import]
except ImportError:
    Judgment: Any = Any  # type: ignore[assignment,misc]

try:
    from jugeo.core.section import Section  # type: ignore[import]
except ImportError:
    Section: Any = Any  # type: ignore[assignment,misc]

try:
    from jugeo.core.trust import TrustTier  # type: ignore[import]
except ImportError:
    TrustTier: Any = Any  # type: ignore[assignment,misc]

try:
    from jugeo.core.obligation import Obligation  # type: ignore[import]
except ImportError:
    Obligation: Any = Any  # type: ignore[assignment,misc]

try:
    from jugeo.core.evidence import Evidence  # type: ignore[import]
except ImportError:
    Evidence: Any = Any  # type: ignore[assignment,misc]

try:
    from jugeo.core.provenance import Provenance  # type: ignore[import]
except ImportError:
    Provenance: Any = Any  # type: ignore[assignment,misc]

try:
    from jugeo.problem_modes.specification_satisfaction.base import (  # type: ignore[import]
        SpecificationSatisfactionBase,
    )
except ImportError:
    SpecificationSatisfactionBase: Any = Any  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

PROPOSAL_TRUST_TIER: str = "PROPOSED"
VERIFIED_TRUST_TIER: str = "VERIFIED"
TRUSTED_TRUST_TIER: str = "TRUSTED"
UNVERIFIED_TRUST_TIER: str = "UNVERIFIED"

GENERATION_SOURCE_LLM: str = "LLM"
GENERATION_SOURCE_TEMPLATE: str = "TEMPLATE"
GENERATION_SOURCE_MANUAL: str = "MANUAL"
GENERATION_SOURCE_RULE: str = "RULE"

REVIEW_STATUS_PENDING: str = "PENDING"
REVIEW_STATUS_ACCEPTED: str = "ACCEPTED"
REVIEW_STATUS_REJECTED: str = "REJECTED"

JUDGMENT_COMPONENTS: tuple[str, ...] = ("c", "phi", "A", "E", "O", "B", "T", "Pi")

MAX_PROPOSALS_PER_HOLE: int = 10
DEFAULT_GENERATION_SOURCE: str = "LLM"

_VALID_GENERATION_SOURCES: frozenset[str] = frozenset(
    {GENERATION_SOURCE_LLM, GENERATION_SOURCE_TEMPLATE, GENERATION_SOURCE_MANUAL, GENERATION_SOURCE_RULE}
)
_VALID_TRUST_TIERS: frozenset[str] = frozenset(
    {PROPOSAL_TRUST_TIER, VERIFIED_TRUST_TIER, TRUSTED_TRUST_TIER, UNVERIFIED_TRUST_TIER}
)
_VALID_REVIEW_STATUSES: frozenset[str] = frozenset(
    {REVIEW_STATUS_PENDING, REVIEW_STATUS_ACCEPTED, REVIEW_STATUS_REJECTED}
)

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class GenerationKind(enum.Enum):
    """Enumeration of generation strategies available for filling holes.

    Attributes
    ----------
    LLM_COMPLETION : str
        Completion via a large language model (e.g. GPT-class models).
    TEMPLATE_INSTANTIATION : str
        Filling holes by instantiating a pre-authored template.
    RULE_BASED : str
        Deterministic rule engine that derives judgments from context.
    MANUAL_EDIT : str
        A human author directly provides the judgment text.
    HYBRID : str
        Combination of two or more generation strategies.
    """

    LLM_COMPLETION = "LLM_COMPLETION"
    TEMPLATE_INSTANTIATION = "TEMPLATE_INSTANTIATION"
    RULE_BASED = "RULE_BASED"
    MANUAL_EDIT = "MANUAL_EDIT"
    HYBRID = "HYBRID"


class ExtensionStatus(enum.Enum):
    """Status of a generation-as-extension attempt on a partial section.

    Attributes
    ----------
    PENDING : str
        Extension process has been initialised but no proposals submitted yet.
    PARTIALLY_EXTENDED : str
        Some holes have been filled; others remain open.
    FULLY_EXTENDED : str
        Every hole in the partial section has been filled with an accepted proposal.
    FAILED : str
        Extension process encountered a terminal error or all proposals were rejected.
    REVERTED : str
        A previously accepted extension was rolled back due to obligation violations.
    """

    PENDING = "PENDING"
    PARTIALLY_EXTENDED = "PARTIALLY_EXTENDED"
    FULLY_EXTENDED = "FULLY_EXTENDED"
    FAILED = "FAILED"
    REVERTED = "REVERTED"


# ---------------------------------------------------------------------------
# Helper functions (private)
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string.

    Returns
    -------
    str
        Current UTC timestamp in the form ``'YYYY-MM-DDTHH:MM:SS.ffffffZ'``.

    Examples
    --------
    >>> ts = _now_iso()
    >>> ts.endswith('Z') or '+' in ts or True
    True
    """
    return datetime.now(tz=timezone.utc).isoformat()


def _stable_hash(payload: str) -> str:
    """Return a short deterministic hex digest for *payload*.

    Uses SHA-256 truncated to 16 hex characters so IDs remain human-readable.

    Parameters
    ----------
    payload : str
        The string to hash.

    Returns
    -------
    str
        16-character lowercase hex string.

    Examples
    --------
    >>> len(_stable_hash("hello")) == 16
    True
    """
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _make_constraint_id(hole_id: str, kind: str) -> str:
    """Construct a deterministic constraint identifier.

    Parameters
    ----------
    hole_id : str
        Identifier of the hole to which the constraint applies.
    kind : str
        The constraint kind (e.g. ``'obligation'``, ``'compatibility'``).

    Returns
    -------
    str
        A string of the form ``'cst-<hash>'``.
    """
    raw = f"constraint::{hole_id}::{kind}::{uuid.uuid4().hex[:8]}"
    return f"cst-{_stable_hash(raw)}"


def _make_proposal_id(hole_id: str, source: str) -> str:
    """Construct a unique proposal identifier.

    Parameters
    ----------
    hole_id : str
        Identifier of the hole targeted by this proposal.
    source : str
        Generation source label (``'LLM'``, ``'TEMPLATE'``, etc.).

    Returns
    -------
    str
        A string of the form ``'prp-<hash>'``.
    """
    raw = f"proposal::{hole_id}::{source}::{uuid.uuid4().hex}"
    return f"prp-{_stable_hash(raw)}"


def _make_candidate_id(hole_id: str) -> str:
    """Construct a unique candidate-bundle identifier.

    Parameters
    ----------
    hole_id : str
        Identifier of the hole for which candidates are being gathered.

    Returns
    -------
    str
        A string of the form ``'cnd-<hash>'``.
    """
    raw = f"candidate::{hole_id}::{uuid.uuid4().hex}"
    return f"cnd-{_stable_hash(raw)}"


def _make_witness_id(spec_id: str) -> str:
    """Construct a unique witness identifier.

    Parameters
    ----------
    spec_id : str
        Identifier of the specification being witnessed.

    Returns
    -------
    str
        A string of the form ``'wit-<hash>'``.
    """
    raw = f"witness::{spec_id}::{uuid.uuid4().hex}"
    return f"wit-{_stable_hash(raw)}"


def _score_proposal(proposal: "GenerationProposal", constraints: list[Any]) -> float:
    """Compute a numeric fitness score for *proposal* against *constraints*.

    The score is a value in ``[0.0, 1.0]``.  Scoring heuristics:

    * Start at ``1.0``.
    * Subtract ``0.15`` for each constraint that the proposal explicitly lists as violated.
    * Add ``0.05`` for each constraint that is addressed and not violated (capped at 1.0).
    * Subtract a small penalty if the proposed text is very short (< 20 chars).

    Parameters
    ----------
    proposal : GenerationProposal
        The proposal to score.
    constraints : list
        List of ``GenerationConstraint`` objects to evaluate against.

    Returns
    -------
    float
        Fitness score in the range ``[0.0, 1.0]``.
    """
    score = 1.0
    violated_ids = set(proposal.constraints_violated)
    addressed_ids = set(proposal.constraints_addressed)

    for c in constraints:
        cid = getattr(c, "constraint_id", None)
        if cid in violated_ids:
            score -= 0.15
        elif cid in addressed_ids:
            score = min(1.0, score + 0.05)

    # Penalise extremely short proposal text
    if len(proposal.proposed_text.strip()) < 20:
        score -= 0.10

    return max(0.0, round(score, 4))


def _check_judgment_compatibility(j1: dict[str, Any], j2: dict[str, Any]) -> bool:
    """Determine whether two judgment dicts are mutually compatible.

    Two judgments are *compatible* if:

    * They occupy different coordinates (``c`` fields differ), OR
    * If they share a coordinate, their ``phi`` fields are identical (no contradictory
      formulas at the same position).

    Parameters
    ----------
    j1 : dict
        First judgment represented as a plain dict with JUDGMENT_COMPONENTS keys.
    j2 : dict
        Second judgment represented as a plain dict with JUDGMENT_COMPONENTS keys.

    Returns
    -------
    bool
        ``True`` if the two judgments are compatible; ``False`` otherwise.
    """
    c1 = j1.get("c")
    c2 = j2.get("c")
    if c1 != c2:
        return True
    # Same coordinate — check formula agreement
    return j1.get("phi") == j2.get("phi")


def _extract_obligation_kinds(constraints: list[Any]) -> list[str]:
    """Extract the unique obligation kinds referenced by a list of constraints.

    Parameters
    ----------
    constraints : list
        List of ``GenerationConstraint`` instances.

    Returns
    -------
    list[str]
        Sorted list of unique ``constraint_kind`` strings from the constraints.
    """
    kinds: list[str] = []
    seen: set[str] = set()
    for c in constraints:
        k = getattr(c, "constraint_kind", None)
        if k and k not in seen:
            seen.add(k)
            kinds.append(k)
    return sorted(kinds)


def _format_constraint_for_prompt(constraint: "GenerationConstraint") -> str:
    """Render a single constraint as a human/LLM-readable prompt fragment.

    Parameters
    ----------
    constraint : GenerationConstraint
        The constraint to render.

    Returns
    -------
    str
        A multi-line string describing the constraint.
    """
    lines = [
        f"[Constraint {constraint.constraint_id}]",
        f"  Kind      : {constraint.constraint_kind}",
        f"  Hole      : {constraint.hole_id}",
        f"  Coordinate: {constraint.coordinate}",
        f"  Formula   : {constraint.formula}",
        f"  Priority  : {constraint.priority}",
        f"  Trust req : {constraint.trust_tier_required}",
    ]
    if constraint.must_match_pattern:
        lines.append(f"  Pattern   : {constraint.must_match_pattern}")
    if constraint.required_evidence_kinds:
        lines.append(f"  Evidence  : {', '.join(constraint.required_evidence_kinds)}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Supporting dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GenerationConstraint:
    """An immutable constraint that a generated proposal must satisfy.

    A ``GenerationConstraint`` encodes a single local obligation or compatibility
    requirement for a hole.  When an LLM or template engine generates a proposal, it
    receives all constraints for the hole as a prompt fragment so it can attempt to
    satisfy them.

    Parameters
    ----------
    constraint_id : str
        Globally unique identifier for this constraint.
    hole_id : str
        The hole to which this constraint applies.
    coordinate : str
        The coordinate string of the hole.
    constraint_kind : str
        Semantic category, e.g. ``'type_obligation'``, ``'evidence_obligation'``,
        ``'compatibility'``, ``'pattern_match'``.
    formula : str
        The logical formula or textual description of what must be satisfied.
    required_evidence_kinds : list[str]
        Evidence categories that must appear in the proposal's evidence field.
    must_match_pattern : str | None
        Optional regex / glob pattern the proposed text must match.
    trust_tier_required : str
        Minimum trust tier the proposal must claim (always ``'PROPOSED'`` for generated
        content; higher values require non-generation pathways).
    priority : int
        Relative priority (higher = more important; used for prompt ordering).

    Methods
    -------
    to_prompt_fragment() -> str
        Format this constraint as LLM-readable text for prompt injection.

    Examples
    --------
    >>> gc = GenerationConstraint(
    ...     constraint_id="cst-abc123",
    ...     hole_id="hole-1",
    ...     coordinate="§3.2",
    ...     constraint_kind="type_obligation",
    ...     formula="phi must be a well-formed type",
    ...     required_evidence_kinds=["type_derivation"],
    ...     must_match_pattern=None,
    ...     trust_tier_required="PROPOSED",
    ...     priority=10,
    ... )
    >>> gc.to_prompt_fragment().startswith("[Constraint")
    True
    """

    constraint_id: str
    hole_id: str
    coordinate: str
    constraint_kind: str
    formula: str
    required_evidence_kinds: list[str]
    must_match_pattern: str | None
    trust_tier_required: str
    priority: int

    def to_prompt_fragment(self) -> str:
        """Format this constraint as an LLM-readable prompt fragment.

        Returns
        -------
        str
            Multi-line human/LLM-readable description of the constraint.
        """
        return _format_constraint_for_prompt(self)


@dataclass(slots=True)
class GenerationProposal:
    """A mutable record representing a single generated judgment proposal.

    A ``GenerationProposal`` is the fundamental unit produced by a generation
    strategy.  It targets exactly one hole (identified by ``hole_id`` and
    ``coordinate``) and proposes a concrete judgment value for that hole.

    **Trust tier invariant**: ``trust_tier`` MUST be ``"PROPOSED"`` at all times.
    Generation never directly produces ``"VERIFIED"`` or ``"TRUSTED"`` judgments;
    those trust levels must be earned through the review/promotion pipeline.

    Parameters
    ----------
    proposal_id : str
        Globally unique identifier for this proposal.
    hole_id : str
        Identifier of the hole being targeted.
    coordinate : str
        Coordinate string of the hole.
    proposed_text : str
        Human-readable / LLM-generated text for the judgment body.
    proposed_judgment_fields : dict
        A dict with keys from ``JUDGMENT_COMPONENTS`` containing the proposed
        values for each judgment component.
    constraints_addressed : list[str]
        IDs of constraints that this proposal believes it satisfies.
    constraints_violated : list[str]
        IDs of constraints that this proposal acknowledges it cannot satisfy.
    generation_source : str
        Source of generation: ``'LLM'``, ``'TEMPLATE'``, ``'MANUAL'``, ``'RULE'``.
    trust_tier : str
        Must always be ``"PROPOSED"``.
    submission_timestamp : str
        ISO-8601 timestamp when the proposal was submitted.
    review_status : str
        Current review status: ``'PENDING'``, ``'ACCEPTED'``, or ``'REJECTED'``.
    rejection_reason : str | None
        If rejected, the reason string; otherwise ``None``.
    acceptance_notes : str | None
        If accepted, optional reviewer notes; otherwise ``None``.

    Methods
    -------
    mark_accepted(notes: str | None = None) -> None
        Transition this proposal to ACCEPTED status.
    mark_rejected(reason: str) -> None
        Transition this proposal to REJECTED status with a reason.
    check_trust_tier_invariant() -> None
        Raise ``ValueError`` if ``trust_tier != "PROPOSED"``.
    """

    proposal_id: str
    hole_id: str
    coordinate: str
    proposed_text: str
    proposed_judgment_fields: dict[str, Any]
    constraints_addressed: list[str]
    constraints_violated: list[str]
    generation_source: str
    trust_tier: str
    submission_timestamp: str
    review_status: str
    rejection_reason: str | None
    acceptance_notes: str | None

    def mark_accepted(self, notes: str | None = None) -> None:
        """Transition this proposal to ACCEPTED review status.

        Parameters
        ----------
        notes : str | None, optional
            Optional reviewer notes to attach.

        Raises
        ------
        ValueError
            If the proposal is not currently in ``PENDING`` status.
        """
        if self.review_status != REVIEW_STATUS_PENDING:
            raise ValueError(
                f"Cannot accept proposal {self.proposal_id!r}: "
                f"current status is {self.review_status!r}, expected 'PENDING'."
            )
        self.check_trust_tier_invariant()
        self.review_status = REVIEW_STATUS_ACCEPTED
        self.acceptance_notes = notes

    def mark_rejected(self, reason: str) -> None:
        """Transition this proposal to REJECTED review status.

        Parameters
        ----------
        reason : str
            Human-readable explanation of why the proposal was rejected.

        Raises
        ------
        ValueError
            If the proposal is not currently in ``PENDING`` status.
        """
        if self.review_status != REVIEW_STATUS_PENDING:
            raise ValueError(
                f"Cannot reject proposal {self.proposal_id!r}: "
                f"current status is {self.review_status!r}, expected 'PENDING'."
            )
        self.review_status = REVIEW_STATUS_REJECTED
        self.rejection_reason = reason

    def check_trust_tier_invariant(self) -> None:
        """Assert that the trust tier is ``"PROPOSED"``.

        This invariant is core to the generation-as-extension theory: generated
        proposals must never claim a trust tier higher than ``PROPOSED`` at submission
        time.

        Raises
        ------
        ValueError
            If ``trust_tier != "PROPOSED"``.
        """
        if self.trust_tier != PROPOSAL_TRUST_TIER:
            raise ValueError(
                f"Trust tier invariant violated for proposal {self.proposal_id!r}: "
                f"expected {PROPOSAL_TRUST_TIER!r}, got {self.trust_tier!r}. "
                "Generated proposals must always enter at PROPOSED tier."
            )


@dataclass(slots=True)
class ExtensionCandidate:
    """A bundle of proposals competing to fill a single hole.

    When multiple proposals are generated for the same hole, they are collected
    into an ``ExtensionCandidate`` so they can be scored and ranked.  The
    ``best_proposal_id`` field is set after ``score_and_rank()`` is called.

    Parameters
    ----------
    candidate_id : str
        Globally unique identifier for this candidate bundle.
    hole_id : str
        The hole that all proposals in this bundle are targeting.
    proposals : list[GenerationProposal]
        All proposals in the bundle.
    scored_proposals : list[tuple[str, float]]
        After scoring: list of ``(proposal_id, score)`` tuples, sorted descending.
    best_proposal_id : str | None
        ID of the highest-scoring proposal, or ``None`` before ranking.
    candidate_status : str
        One of ``'PENDING'``, ``'RANKED'``, ``'SELECTED'``, ``'EXHAUSTED'``.
    created_at : str
        ISO-8601 creation timestamp.

    Methods
    -------
    add_proposal(proposal: GenerationProposal) -> None
        Add a proposal to the bundle.
    score_and_rank(constraints: list[GenerationConstraint]) -> None
        Score all proposals against the given constraints and sort them.
    select_best() -> GenerationProposal | None
        Return the best-scoring proposal, or ``None`` if the bundle is empty.
    to_dict() -> dict
        Serialise this candidate bundle to a plain dict.
    """

    candidate_id: str
    hole_id: str
    proposals: list[GenerationProposal]
    scored_proposals: list[tuple[str, float]]
    best_proposal_id: str | None
    candidate_status: str
    created_at: str

    def add_proposal(self, proposal: "GenerationProposal") -> None:
        """Add *proposal* to this candidate bundle.

        Parameters
        ----------
        proposal : GenerationProposal
            The proposal to add.  Its ``hole_id`` must match ``self.hole_id``.

        Raises
        ------
        ValueError
            If ``proposal.hole_id`` does not match ``self.hole_id``.
        """
        if proposal.hole_id != self.hole_id:
            raise ValueError(
                f"Proposal {proposal.proposal_id!r} targets hole {proposal.hole_id!r}, "
                f"but this candidate is for hole {self.hole_id!r}."
            )
        self.proposals.append(proposal)

    def score_and_rank(self, constraints: list["GenerationConstraint"]) -> None:
        """Score all proposals in this bundle and sort them by descending score.

        After calling this method ``scored_proposals`` is populated and
        ``best_proposal_id`` is set.

        Parameters
        ----------
        constraints : list[GenerationConstraint]
            Constraints to evaluate each proposal against.
        """
        scored = [
            (p.proposal_id, _score_proposal(p, constraints))
            for p in self.proposals
        ]
        scored.sort(key=lambda t: t[1], reverse=True)
        self.scored_proposals = scored
        self.best_proposal_id = scored[0][0] if scored else None
        self.candidate_status = "RANKED"

    def select_best(self) -> "GenerationProposal | None":
        """Return the highest-scoring proposal in this bundle.

        Returns
        -------
        GenerationProposal | None
            The best proposal, or ``None`` if the bundle is empty or unranked.
        """
        if not self.best_proposal_id:
            return None
        for p in self.proposals:
            if p.proposal_id == self.best_proposal_id:
                self.candidate_status = "SELECTED"
                return p
        return None

    def to_dict(self) -> dict[str, Any]:
        """Serialise this candidate bundle to a plain dict.

        Returns
        -------
        dict
            JSON-serialisable representation of the bundle.
        """
        return {
            "candidate_id": self.candidate_id,
            "hole_id": self.hole_id,
            "candidate_status": self.candidate_status,
            "created_at": self.created_at,
            "best_proposal_id": self.best_proposal_id,
            "num_proposals": len(self.proposals),
            "scored_proposals": self.scored_proposals,
        }


@dataclass(frozen=True, slots=True)
class GenerationConfig:
    """Immutable configuration governing how generation proposals are created.

    Parameters
    ----------
    config_id : str
        Unique identifier for this configuration snapshot.
    max_proposals_per_hole : int
        Maximum number of proposals that may be submitted for a single hole.
    generation_source : str
        Default generation source (``'LLM'``, ``'TEMPLATE'``, etc.).
    trust_tier_for_proposals : str
        Trust tier assigned to all proposals — must always be ``'PROPOSED'``.
    require_all_obligations_met : bool
        If ``True``, proposals must address *every* constraint; otherwise partial
        satisfaction is permitted.
    allow_partial_fills : bool
        If ``True``, a section can be accepted even if some holes remain unfilled.
    prompt_template : str | None
        Optional Jinja2-compatible prompt template string.
    extra_constraints : dict
        Arbitrary extra configuration passed through to the generation backend.

    Raises
    ------
    ValueError
        On construction if ``trust_tier_for_proposals != 'PROPOSED'``.
    """

    config_id: str
    max_proposals_per_hole: int
    generation_source: str
    trust_tier_for_proposals: str
    require_all_obligations_met: bool
    allow_partial_fills: bool
    prompt_template: str | None
    extra_constraints: dict[str, Any]

    def __post_init__(self) -> None:
        """Validate invariants after construction."""
        if self.trust_tier_for_proposals != PROPOSAL_TRUST_TIER:
            raise ValueError(
                f"GenerationConfig.trust_tier_for_proposals must be {PROPOSAL_TRUST_TIER!r}; "
                f"got {self.trust_tier_for_proposals!r}."
            )
        if self.max_proposals_per_hole < 1:
            raise ValueError("max_proposals_per_hole must be >= 1.")


@dataclass(frozen=True, slots=True)
class GenerationExtensionPartialSectionsWitness:
    """Immutable frozen record capturing the outcome of a generation-as-extension run.

    A witness is created after a full extension attempt completes (whether successfully
    or not).  It summarises what happened: how many holes were attempted, how many
    proposals were submitted/accepted/rejected, and whether all obligations were met.

    The ``trust_tier`` field is always ``"PROPOSED"`` because the witness records
    the state of generated (not yet promoted) content.

    Parameters
    ----------
    witness_id : str
        Globally unique witness identifier.
    spec_id : str
        Identifier of the specification being targeted.
    holes_attempted : int
        Number of holes for which generation was attempted.
    holes_filled : int
        Number of holes that received at least one accepted proposal.
    holes_remaining : int
        Number of holes with no accepted proposal (``holes_attempted - holes_filled``).
    proposals_submitted : int
        Total number of proposals submitted across all holes.
    proposals_accepted : int
        Total number of proposals accepted.
    proposals_rejected : int
        Total number of proposals rejected.
    extension_completeness_fraction : float
        Fraction of holes filled: ``holes_filled / holes_attempted`` (0.0 if 0 attempted).
    all_obligations_met : bool
        Whether every constraint for every filled hole was satisfied.
    witness_status : str
        One of ``'FULLY_EXTENDED'``, ``'PARTIALLY_EXTENDED'``, ``'FAILED'``, ``'EMPTY'``.
    accepted_proposal_ids : list[str]
        IDs of all accepted proposals.
    trust_tier : str
        Always ``"PROPOSED"`` for generation witnesses.
    witness_timestamp : str
        ISO-8601 timestamp of witness creation.
    provenance : str
        Human-readable provenance string.
    """

    witness_id: str
    spec_id: str
    holes_attempted: int
    holes_filled: int
    holes_remaining: int
    proposals_submitted: int
    proposals_accepted: int
    proposals_rejected: int
    extension_completeness_fraction: float
    all_obligations_met: bool
    witness_status: str
    accepted_proposal_ids: list[str]
    trust_tier: str
    witness_timestamp: str
    provenance: str


# ---------------------------------------------------------------------------
# Main orchestrator dataclass
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class GenerationExtensionPartialSectionsCoordinator:
    """Main orchestrator for the generation-as-extension workflow.

    The coordinator owns the full lifecycle of a generation-as-extension run:

    1. A *partial section snapshot* is loaded (mapping coordinates → authored judgments).
    2. Holes are *registered* — each hole gets an entry in ``hole_registry``.
    3. Generation proposals are *submitted* — they enter at ``PROPOSED`` trust tier.
    4. Proposals are *accepted* or *rejected* after compatibility checking.
    5. Accepted proposals form an *extended section* that covers more coordinates.

    All state mutations are logged to ``coordination_log``.

    Parameters
    ----------
    coordinator_id : str
        Globally unique identifier for this coordinator instance.
    partial_section_snapshot : dict
        Mapping of ``coordinate → judgment_dict`` for authored (non-hole) judgments.
    hole_registry : dict
        Mapping of ``hole_id → hole_descriptor_dict``.  Each hole descriptor must
        contain at least ``'coordinate'``, ``'formula'``, and ``'obligations'`` keys.
    pending_proposals : list[GenerationProposal]
        Proposals awaiting review.
    accepted_proposals : list[GenerationProposal]
        Proposals that have been accepted.
    rejected_proposals : list[GenerationProposal]
        Proposals that have been rejected.
    coordination_log : list[dict]
        Ordered log of all coordinator events.
    generation_config : dict
        Serialised form of the active ``GenerationConfig``.
    max_proposals_per_hole : int
        Maximum number of proposals allowed per hole (mirrors config).

    Methods
    -------
    register_hole_for_generation(hole_id, coordinate, formula, obligations) -> None
        Register a hole so it can receive generation proposals.
    submit_proposal(proposal) -> None
        Submit a generation proposal for a registered hole.
    accept_proposal(proposal_id, notes) -> None
        Accept a pending proposal.
    reject_proposal(proposal_id, reason) -> None
        Reject a pending proposal.
    check_proposal_compatibility(proposal) -> tuple[bool, list[str]]
        Check whether a proposal is compatible with the partial section.
    compute_extension_candidates(constraints) -> dict[str, ExtensionCandidate]
        Group pending proposals into scored candidates per hole.
    get_pending_proposals() -> list[GenerationProposal]
        Return all pending proposals.
    get_accepted_proposals() -> list[GenerationProposal]
        Return all accepted proposals.
    summarize_generation_state() -> dict
        Return a summary dict of the current coordinator state.
    """

    coordinator_id: str
    partial_section_snapshot: dict[str, Any]
    hole_registry: dict[str, Any]
    pending_proposals: list[GenerationProposal]
    accepted_proposals: list[GenerationProposal]
    rejected_proposals: list[GenerationProposal]
    coordination_log: list[dict[str, Any]]
    generation_config: dict[str, Any]
    max_proposals_per_hole: int

    def _log(self, event_kind: str, payload: dict[str, Any]) -> None:
        """Append an event to the coordination log.

        Parameters
        ----------
        event_kind : str
            Short label for the event type.
        payload : dict
            Event-specific data.
        """
        self.coordination_log.append(
            {"event_kind": event_kind, "timestamp": _now_iso(), **payload}
        )

    def register_hole_for_generation(
        self,
        hole_id: str,
        coordinate: str,
        formula: str,
        obligations: list[str],
    ) -> None:
        """Register a hole so the coordinator can accept proposals for it.

        Parameters
        ----------
        hole_id : str
            Unique identifier for the hole.
        coordinate : str
            The coordinate in the specification where the hole exists.
        formula : str
            The formula or type shape that must be filled.
        obligations : list[str]
            Local obligation identifiers that the fill must satisfy.

        Raises
        ------
        ValueError
            If ``hole_id`` is already registered.
        """
        if hole_id in self.hole_registry:
            raise ValueError(f"Hole {hole_id!r} is already registered.")
        descriptor: dict[str, Any] = {
            "hole_id": hole_id,
            "coordinate": coordinate,
            "formula": formula,
            "obligations": obligations,
            "registered_at": _now_iso(),
            "proposal_count": 0,
        }
        self.hole_registry[hole_id] = descriptor
        self._log("HOLE_REGISTERED", {"hole_id": hole_id, "coordinate": coordinate})

    def submit_proposal(self, proposal: "GenerationProposal") -> None:
        """Submit a generation proposal for a registered hole.

        The proposal must:
        * target a registered hole (``hole_id`` in ``hole_registry``),
        * have ``trust_tier == "PROPOSED"``,
        * not exceed ``max_proposals_per_hole`` for its hole.

        Parameters
        ----------
        proposal : GenerationProposal
            The proposal to submit.

        Raises
        ------
        ValueError
            If the hole is not registered, the trust tier invariant is violated,
            or the hole's proposal quota is exceeded.
        """
        proposal.check_trust_tier_invariant()
        hole_id = proposal.hole_id
        if hole_id not in self.hole_registry:
            raise ValueError(
                f"Cannot submit proposal for unregistered hole {hole_id!r}."
            )
        descriptor = self.hole_registry[hole_id]
        current_count: int = descriptor.get("proposal_count", 0)
        if current_count >= self.max_proposals_per_hole:
            raise ValueError(
                f"Hole {hole_id!r} has reached the maximum of "
                f"{self.max_proposals_per_hole} proposals."
            )
        descriptor["proposal_count"] = current_count + 1
        self.pending_proposals.append(proposal)
        self._log(
            "PROPOSAL_SUBMITTED",
            {"proposal_id": proposal.proposal_id, "hole_id": hole_id},
        )

    def accept_proposal(
        self, proposal_id: str, notes: str | None = None
    ) -> None:
        """Accept a pending proposal by ID.

        After acceptance the proposal is moved from ``pending_proposals`` to
        ``accepted_proposals``.

        Parameters
        ----------
        proposal_id : str
            The ID of the proposal to accept.
        notes : str | None, optional
            Optional reviewer notes.

        Raises
        ------
        KeyError
            If no pending proposal with ``proposal_id`` is found.
        """
        for i, p in enumerate(self.pending_proposals):
            if p.proposal_id == proposal_id:
                p.mark_accepted(notes=notes)
                self.accepted_proposals.append(p)
                del self.pending_proposals[i]
                self._log("PROPOSAL_ACCEPTED", {"proposal_id": proposal_id})
                return
        raise KeyError(f"No pending proposal with ID {proposal_id!r}.")

    def reject_proposal(self, proposal_id: str, reason: str) -> None:
        """Reject a pending proposal by ID.

        After rejection the proposal is moved from ``pending_proposals`` to
        ``rejected_proposals``.

        Parameters
        ----------
        proposal_id : str
            The ID of the proposal to reject.
        reason : str
            Human-readable rejection reason.

        Raises
        ------
        KeyError
            If no pending proposal with ``proposal_id`` is found.
        """
        for i, p in enumerate(self.pending_proposals):
            if p.proposal_id == proposal_id:
                p.mark_rejected(reason=reason)
                self.rejected_proposals.append(p)
                del self.pending_proposals[i]
                self._log(
                    "PROPOSAL_REJECTED",
                    {"proposal_id": proposal_id, "reason": reason},
                )
                return
        raise KeyError(f"No pending proposal with ID {proposal_id!r}.")

    def check_proposal_compatibility(
        self, proposal: "GenerationProposal"
    ) -> tuple[bool, list[str]]:
        """Check whether *proposal* is compatible with the existing partial section.

        Compatibility means: the proposed judgment does not contradict any authored
        judgment already present in ``partial_section_snapshot``.

        Parameters
        ----------
        proposal : GenerationProposal
            The proposal to check.

        Returns
        -------
        tuple[bool, list[str]]
            ``(compatible, issues)`` where *compatible* is ``True`` if no issues
            were found and *issues* is a (possibly empty) list of problem descriptions.
        """
        issues: list[str] = []
        pj = proposal.proposed_judgment_fields
        for coord, authored_j in self.partial_section_snapshot.items():
            if not _check_judgment_compatibility(pj, authored_j):
                issues.append(
                    f"Proposal {proposal.proposal_id!r} conflicts with authored judgment "
                    f"at coordinate {coord!r}: formula mismatch."
                )
        return (len(issues) == 0, issues)

    def compute_extension_candidates(
        self, constraints_by_hole: dict[str, list["GenerationConstraint"]]
    ) -> dict[str, "ExtensionCandidate"]:
        """Group pending proposals into scored ``ExtensionCandidate`` bundles.

        One ``ExtensionCandidate`` is produced per hole that has at least one
        pending proposal.  Each bundle is scored and ranked using the supplied
        constraints.

        Parameters
        ----------
        constraints_by_hole : dict[str, list[GenerationConstraint]]
            Mapping from ``hole_id`` to the list of constraints for that hole.

        Returns
        -------
        dict[str, ExtensionCandidate]
            Mapping from ``hole_id`` to the ranked ``ExtensionCandidate``.
        """
        bundles: dict[str, ExtensionCandidate] = {}
        for proposal in self.pending_proposals:
            hid = proposal.hole_id
            if hid not in bundles:
                bundles[hid] = ExtensionCandidate(
                    candidate_id=_make_candidate_id(hid),
                    hole_id=hid,
                    proposals=[],
                    scored_proposals=[],
                    best_proposal_id=None,
                    candidate_status="PENDING",
                    created_at=_now_iso(),
                )
            bundles[hid].add_proposal(proposal)

        for hid, bundle in bundles.items():
            constraints = constraints_by_hole.get(hid, [])
            bundle.score_and_rank(constraints)

        self._log("CANDIDATES_COMPUTED", {"num_bundles": len(bundles)})
        return bundles

    def get_pending_proposals(self) -> list["GenerationProposal"]:
        """Return a copy of the list of pending proposals.

        Returns
        -------
        list[GenerationProposal]
            All proposals currently in ``PENDING`` status.
        """
        return list(self.pending_proposals)

    def get_accepted_proposals(self) -> list["GenerationProposal"]:
        """Return a copy of the list of accepted proposals.

        Returns
        -------
        list[GenerationProposal]
            All proposals currently in ``ACCEPTED`` status.
        """
        return list(self.accepted_proposals)

    def summarize_generation_state(self) -> dict[str, Any]:
        """Produce a summary of the current coordinator state.

        Returns
        -------
        dict
            A JSON-serialisable summary including counts of holes, proposals
            in each state, and the log length.
        """
        holes_with_accepted = {p.hole_id for p in self.accepted_proposals}
        return {
            "coordinator_id": self.coordinator_id,
            "total_holes_registered": len(self.hole_registry),
            "holes_with_accepted_proposals": len(holes_with_accepted),
            "holes_remaining": len(self.hole_registry) - len(holes_with_accepted),
            "pending_proposals": len(self.pending_proposals),
            "accepted_proposals": len(self.accepted_proposals),
            "rejected_proposals": len(self.rejected_proposals),
            "log_entries": len(self.coordination_log),
            "extension_completeness": (
                len(holes_with_accepted) / len(self.hole_registry)
                if self.hole_registry
                else 0.0
            ),
        }


# ---------------------------------------------------------------------------
# Analyzer dataclass
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class GenerationExtensionPartialSectionsAnalyzer:
    """Analyzes generation proposals and their extension fitness.

    The analyzer operates independently of the coordinator.  It accepts proposals
    and constraints, computes per-proposal scores and compatibility assessments,
    and produces structured analysis reports.

    Parameters
    ----------
    analyzer_id : str
        Globally unique identifier for this analyzer instance.
    analysis_log : list[dict]
        Ordered log of analysis events.
    proposal_scores : dict[str, float]
        Mapping of ``proposal_id → score``.
    compatibility_matrix : dict[str, dict[str, bool]]
        Nested mapping: ``proposal_id → {hole_id: compatible}``.
    obligation_coverage_map : dict[str, dict[str, bool]]
        Nested mapping: ``proposal_id → {constraint_id: covered}``.
    rejected_reasons : dict[str, str]
        Mapping of ``proposal_id → rejection_reason`` for proposals the analyzer
        recommends rejecting.
    analysis_results : list[dict]
        Accumulated structured analysis results.

    Methods
    -------
    analyze_proposal(proposal, constraints, partial_section) -> dict
        Full analysis of a single proposal.
    score_proposal(proposal, constraints) -> float
        Score a proposal against a constraint list.
    check_extension_compatibility(proposal, partial_section) -> tuple[bool, list[str]]
        Check whether a proposal can extend the given partial section.
    check_obligation_coverage(proposal, constraints) -> dict[str, bool]
        For each constraint, determine whether the proposal covers it.
    rank_proposals(proposals, constraints) -> list[tuple[str, float]]
        Rank proposals by score descending.
    produce_analysis_report() -> dict
        Compile all analysis results into a single report dict.
    """

    analyzer_id: str
    analysis_log: list[dict[str, Any]]
    proposal_scores: dict[str, float]
    compatibility_matrix: dict[str, dict[str, bool]]
    obligation_coverage_map: dict[str, dict[str, bool]]
    rejected_reasons: dict[str, str]
    analysis_results: list[dict[str, Any]]

    def _alog(self, event: str, payload: dict[str, Any]) -> None:
        """Append to the analysis log.

        Parameters
        ----------
        event : str
            Event label.
        payload : dict
            Additional data.
        """
        self.analysis_log.append({"event": event, "ts": _now_iso(), **payload})

    def analyze_proposal(
        self,
        proposal: "GenerationProposal",
        constraints: list["GenerationConstraint"],
        partial_section: dict[str, Any],
    ) -> dict[str, Any]:
        """Perform a full analysis of *proposal*.

        This combines scoring, extension compatibility checking, and obligation
        coverage into a single structured result dict.

        Parameters
        ----------
        proposal : GenerationProposal
            The proposal to analyse.
        constraints : list[GenerationConstraint]
            The constraints for the proposal's target hole.
        partial_section : dict
            The existing partial section (authored judgments).

        Returns
        -------
        dict
            Analysis result with keys: ``proposal_id``, ``score``,
            ``compatible``, ``compatibility_issues``, ``obligation_coverage``,
            ``all_obligations_covered``, ``recommended_action``.
        """
        score = self.score_proposal(proposal, constraints)
        compatible, issues = self.check_extension_compatibility(proposal, partial_section)
        coverage = self.check_obligation_coverage(proposal, constraints)
        all_covered = all(coverage.values()) if coverage else True

        recommended = "ACCEPT" if (compatible and all_covered and score >= 0.5) else "REJECT"
        if not compatible:
            self.rejected_reasons[proposal.proposal_id] = (
                "Incompatible with partial section: " + "; ".join(issues)
            )
        elif not all_covered:
            uncovered = [cid for cid, cov in coverage.items() if not cov]
            self.rejected_reasons[proposal.proposal_id] = (
                "Obligations not covered: " + ", ".join(uncovered)
            )

        result = {
            "proposal_id": proposal.proposal_id,
            "hole_id": proposal.hole_id,
            "score": score,
            "compatible": compatible,
            "compatibility_issues": issues,
            "obligation_coverage": coverage,
            "all_obligations_covered": all_covered,
            "recommended_action": recommended,
            "analyzed_at": _now_iso(),
        }
        self.analysis_results.append(result)
        self._alog("PROPOSAL_ANALYZED", {"proposal_id": proposal.proposal_id, "score": score})
        return result

    def score_proposal(
        self,
        proposal: "GenerationProposal",
        constraints: list["GenerationConstraint"],
    ) -> float:
        """Score *proposal* against *constraints* and cache the result.

        Parameters
        ----------
        proposal : GenerationProposal
            The proposal to score.
        constraints : list[GenerationConstraint]
            Constraints to evaluate against.

        Returns
        -------
        float
            Score in ``[0.0, 1.0]``.
        """
        score = _score_proposal(proposal, constraints)
        self.proposal_scores[proposal.proposal_id] = score
        return score

    def check_extension_compatibility(
        self,
        proposal: "GenerationProposal",
        partial_section: dict[str, Any],
    ) -> tuple[bool, list[str]]:
        """Check that *proposal* can extend *partial_section* without contradiction.

        Parameters
        ----------
        proposal : GenerationProposal
            The proposal being checked.
        partial_section : dict
            Authored judgments keyed by coordinate.

        Returns
        -------
        tuple[bool, list[str]]
            ``(compatible, issues)``
        """
        issues: list[str] = []
        pj = proposal.proposed_judgment_fields
        compat_entry: dict[str, bool] = {}
        for coord, aj in partial_section.items():
            ok = _check_judgment_compatibility(pj, aj)
            compat_entry[coord] = ok
            if not ok:
                issues.append(
                    f"Coordinate {coord!r}: proposed phi={pj.get('phi')!r} "
                    f"conflicts with authored phi={aj.get('phi')!r}."
                )
        self.compatibility_matrix[proposal.proposal_id] = compat_entry
        return (len(issues) == 0, issues)

    def check_obligation_coverage(
        self,
        proposal: "GenerationProposal",
        constraints: list["GenerationConstraint"],
    ) -> dict[str, bool]:
        """For each constraint, determine whether *proposal* covers it.

        A constraint is considered *covered* if its ``constraint_id`` appears in
        ``proposal.constraints_addressed`` and NOT in ``proposal.constraints_violated``.

        Parameters
        ----------
        proposal : GenerationProposal
            The proposal being checked.
        constraints : list[GenerationConstraint]
            The constraints to check.

        Returns
        -------
        dict[str, bool]
            Mapping of ``constraint_id → covered``.
        """
        addressed = set(proposal.constraints_addressed)
        violated = set(proposal.constraints_violated)
        coverage: dict[str, bool] = {}
        for c in constraints:
            cid = c.constraint_id
            coverage[cid] = (cid in addressed) and (cid not in violated)
        self.obligation_coverage_map[proposal.proposal_id] = coverage
        return coverage

    def rank_proposals(
        self,
        proposals: list["GenerationProposal"],
        constraints: list["GenerationConstraint"],
    ) -> list[tuple[str, float]]:
        """Rank *proposals* by score descending.

        Parameters
        ----------
        proposals : list[GenerationProposal]
            Proposals to rank.
        constraints : list[GenerationConstraint]
            Constraints to score against.

        Returns
        -------
        list[tuple[str, float]]
            ``[(proposal_id, score), ...]`` sorted by score descending.
        """
        scored = [(p.proposal_id, self.score_proposal(p, constraints)) for p in proposals]
        scored.sort(key=lambda t: t[1], reverse=True)
        self._alog("PROPOSALS_RANKED", {"num_proposals": len(proposals)})
        return scored

    def produce_analysis_report(self) -> dict[str, Any]:
        """Compile all accumulated analysis results into a single report.

        Returns
        -------
        dict
            Report with summary statistics and the full list of per-proposal
            analysis results.
        """
        total = len(self.analysis_results)
        accept_count = sum(
            1 for r in self.analysis_results if r.get("recommended_action") == "ACCEPT"
        )
        reject_count = total - accept_count
        avg_score = (
            sum(r["score"] for r in self.analysis_results) / total if total else 0.0
        )
        report = {
            "analyzer_id": self.analyzer_id,
            "report_generated_at": _now_iso(),
            "total_proposals_analyzed": total,
            "recommended_accept": accept_count,
            "recommended_reject": reject_count,
            "average_score": round(avg_score, 4),
            "rejection_reasons": dict(self.rejected_reasons),
            "results": self.analysis_results,
        }
        self._alog("REPORT_PRODUCED", {"total": total})
        return report


# ---------------------------------------------------------------------------
# Module-level factory functions
# ---------------------------------------------------------------------------


def create_generation_constraint(
    hole_id: str,
    coordinate: str,
    kind: str,
    formula: str,
    *,
    required_evidence_kinds: list[str] | None = None,
    must_match_pattern: str | None = None,
    trust_tier_required: str = PROPOSAL_TRUST_TIER,
    priority: int = 5,
) -> GenerationConstraint:
    """Create a ``GenerationConstraint`` with a generated ID.

    Parameters
    ----------
    hole_id : str
        The hole this constraint applies to.
    coordinate : str
        The coordinate of the hole.
    kind : str
        Constraint kind label (e.g. ``'type_obligation'``).
    formula : str
        The logical formula or description to satisfy.
    required_evidence_kinds : list[str] | None, optional
        Evidence categories required; defaults to empty list.
    must_match_pattern : str | None, optional
        Optional regex pattern the proposal text must match.
    trust_tier_required : str, optional
        Minimum trust tier required; defaults to ``'PROPOSED'``.
    priority : int, optional
        Constraint priority; defaults to ``5``.

    Returns
    -------
    GenerationConstraint
        A freshly created constraint with a stable generated ID.
    """
    return GenerationConstraint(
        constraint_id=_make_constraint_id(hole_id, kind),
        hole_id=hole_id,
        coordinate=coordinate,
        constraint_kind=kind,
        formula=formula,
        required_evidence_kinds=required_evidence_kinds or [],
        must_match_pattern=must_match_pattern,
        trust_tier_required=trust_tier_required,
        priority=priority,
    )


def create_generation_proposal(
    hole_id: str,
    coordinate: str,
    proposed_text: str,
    proposed_judgment: dict[str, Any],
    source: str = "LLM",
    *,
    constraints_addressed: list[str] | None = None,
    constraints_violated: list[str] | None = None,
) -> GenerationProposal:
    """Create a ``GenerationProposal`` with guaranteed ``PROPOSED`` trust tier.

    The trust tier invariant is enforced here: the returned proposal always has
    ``trust_tier == "PROPOSED"`` regardless of what *source* is.

    Parameters
    ----------
    hole_id : str
        The hole this proposal targets.
    coordinate : str
        The coordinate of the hole.
    proposed_text : str
        The generated judgment text.
    proposed_judgment : dict
        Dict with ``JUDGMENT_COMPONENTS`` keys describing the proposed judgment.
    source : str, optional
        Generation source; defaults to ``'LLM'``.
    constraints_addressed : list[str] | None, optional
        IDs of constraints this proposal believes it addresses.
    constraints_violated : list[str] | None, optional
        IDs of constraints this proposal cannot satisfy.

    Returns
    -------
    GenerationProposal
        A new proposal at ``PROPOSED`` trust tier.

    Raises
    ------
    ValueError
        If *source* is not one of the valid generation sources.
    """
    if source not in _VALID_GENERATION_SOURCES:
        raise ValueError(
            f"Unknown generation source {source!r}. "
            f"Valid sources: {sorted(_VALID_GENERATION_SOURCES)}"
        )
    # Inject trust tier into proposed_judgment_fields for completeness
    judgment_fields: dict[str, Any] = dict(proposed_judgment)
    judgment_fields["T"] = PROPOSAL_TRUST_TIER
    judgment_fields.setdefault("c", coordinate)

    proposal = GenerationProposal(
        proposal_id=_make_proposal_id(hole_id, source),
        hole_id=hole_id,
        coordinate=coordinate,
        proposed_text=proposed_text,
        proposed_judgment_fields=judgment_fields,
        constraints_addressed=list(constraints_addressed or []),
        constraints_violated=list(constraints_violated or []),
        generation_source=source,
        trust_tier=PROPOSAL_TRUST_TIER,
        submission_timestamp=_now_iso(),
        review_status=REVIEW_STATUS_PENDING,
        rejection_reason=None,
        acceptance_notes=None,
    )
    proposal.check_trust_tier_invariant()
    return proposal


def build_generation_config(
    source: str = "LLM",
    max_proposals: int = 3,
    *,
    require_all_obligations_met: bool = True,
    allow_partial_fills: bool = False,
    prompt_template: str | None = None,
    extra_constraints: dict[str, Any] | None = None,
) -> GenerationConfig:
    """Build a ``GenerationConfig`` with sensible defaults.

    Parameters
    ----------
    source : str, optional
        Default generation source; defaults to ``'LLM'``.
    max_proposals : int, optional
        Maximum proposals per hole; defaults to ``3``.
    require_all_obligations_met : bool, optional
        Whether all obligations must be met; defaults to ``True``.
    allow_partial_fills : bool, optional
        Whether partial fills are acceptable; defaults to ``False``.
    prompt_template : str | None, optional
        Optional prompt template string.
    extra_constraints : dict | None, optional
        Extra configuration for the generation backend.

    Returns
    -------
    GenerationConfig
        A new immutable configuration object.
    """
    return GenerationConfig(
        config_id=f"cfg-{_stable_hash(f'{source}:{max_proposals}:{uuid.uuid4().hex}')}",
        max_proposals_per_hole=max_proposals,
        generation_source=source,
        trust_tier_for_proposals=PROPOSAL_TRUST_TIER,
        require_all_obligations_met=require_all_obligations_met,
        allow_partial_fills=allow_partial_fills,
        prompt_template=prompt_template,
        extra_constraints=extra_constraints or {},
    )


def check_proposal_satisfies_constraints(
    proposal: GenerationProposal,
    constraints: list[GenerationConstraint],
) -> tuple[bool, list[str]]:
    """Check whether *proposal* satisfies all given *constraints*.

    A constraint is satisfied when:
    * Its ID is in ``proposal.constraints_addressed``, AND
    * Its ID is NOT in ``proposal.constraints_violated``.

    Parameters
    ----------
    proposal : GenerationProposal
        The proposal to evaluate.
    constraints : list[GenerationConstraint]
        The constraints to check against.

    Returns
    -------
    tuple[bool, list[str]]
        ``(all_satisfied, unsatisfied_descriptions)`` where the second element
        lists human-readable descriptions of unsatisfied constraints.
    """
    addressed = set(proposal.constraints_addressed)
    violated = set(proposal.constraints_violated)
    unsatisfied: list[str] = []
    for c in constraints:
        cid = c.constraint_id
        if cid in violated:
            unsatisfied.append(
                f"Constraint {cid!r} ({c.constraint_kind}) is explicitly violated."
            )
        elif cid not in addressed:
            unsatisfied.append(
                f"Constraint {cid!r} ({c.constraint_kind}) is not addressed by proposal."
            )
    return (len(unsatisfied) == 0, unsatisfied)


def check_proposal_extends_partial_section(
    proposal: GenerationProposal,
    partial_section: dict[str, Any],
    authored_judgments: dict[str, Any],
) -> tuple[bool, list[str]]:
    """Verify that *proposal* can validly extend *partial_section*.

    This function checks three things:

    1. The proposal targets a coordinate NOT already covered by *partial_section*.
    2. The proposed judgment is compatible with every authored judgment in
       *authored_judgments*.
    3. The proposal's trust tier is ``"PROPOSED"``.

    Parameters
    ----------
    proposal : GenerationProposal
        The proposal to check.
    partial_section : dict
        Currently covered coordinates → authored judgment dicts.
    authored_judgments : dict
        All authored judgments in the section (may overlap with partial_section).

    Returns
    -------
    tuple[bool, list[str]]
        ``(can_extend, issues)``
    """
    issues: list[str] = []

    # Check trust tier
    if proposal.trust_tier != PROPOSAL_TRUST_TIER:
        issues.append(
            f"Trust tier invariant: expected {PROPOSAL_TRUST_TIER!r}, "
            f"got {proposal.trust_tier!r}."
        )

    # Check not already covered
    coord = proposal.coordinate
    if coord in partial_section:
        issues.append(
            f"Coordinate {coord!r} is already covered by the partial section. "
            "Proposals should target holes, not existing judgments."
        )

    # Compatibility with all authored judgments
    pj = proposal.proposed_judgment_fields
    for ac, aj in authored_judgments.items():
        if not _check_judgment_compatibility(pj, aj):
            issues.append(
                f"Proposal conflicts with authored judgment at {ac!r}: "
                f"phi mismatch ({pj.get('phi')!r} vs {aj.get('phi')!r})."
            )

    return (len(issues) == 0, issues)


def select_best_proposal(
    candidates: list[GenerationProposal],
    constraints: list[GenerationConstraint],
) -> "GenerationProposal | None":
    """Select the highest-scoring proposal from *candidates*.

    Parameters
    ----------
    candidates : list[GenerationProposal]
        List of proposals to choose from.
    constraints : list[GenerationConstraint]
        Constraints to score against.

    Returns
    -------
    GenerationProposal | None
        The highest-scoring proposal, or ``None`` if the list is empty.
    """
    if not candidates:
        return None
    scored = [(_score_proposal(p, constraints), p) for p in candidates]
    scored.sort(key=lambda t: t[0], reverse=True)
    return scored[0][1]


def build_extension_from_proposals(
    partial_section: dict[str, Any],
    accepted_proposals: list[GenerationProposal],
) -> dict[str, Any]:
    """Build an extended section by merging accepted proposals into *partial_section*.

    The result is a new dict containing all authored judgments from
    *partial_section* plus a new entry for each accepted proposal.  Each
    proposal entry records the full proposed judgment plus metadata.

    Parameters
    ----------
    partial_section : dict
        Existing authored judgments keyed by coordinate.
    accepted_proposals : list[GenerationProposal]
        Proposals to incorporate.  Their coordinates must not overlap with
        *partial_section* (caller responsibility).

    Returns
    -------
    dict
        Extended section mapping coordinate → judgment-or-proposal-record.
    """
    extended: dict[str, Any] = dict(partial_section)
    for p in accepted_proposals:
        coord = p.coordinate
        extended[coord] = {
            **p.proposed_judgment_fields,
            "_proposal_id": p.proposal_id,
            "_generation_source": p.generation_source,
            "_trust_tier": p.trust_tier,
            "_accepted_at": p.acceptance_notes or _now_iso(),
        }
    return extended


def generation_witness_from_coordinator(
    coordinator: GenerationExtensionPartialSectionsCoordinator,
    spec_id: str,
) -> GenerationExtensionPartialSectionsWitness:
    """Construct a ``GenerationExtensionPartialSectionsWitness`` from *coordinator* state.

    Parameters
    ----------
    coordinator : GenerationExtensionPartialSectionsCoordinator
        The coordinator whose state is being witnessed.
    spec_id : str
        Identifier of the specification.

    Returns
    -------
    GenerationExtensionPartialSectionsWitness
        An immutable record summarising the generation run.
    """
    holes_attempted = len(coordinator.hole_registry)
    holes_with_accepted = {p.hole_id for p in coordinator.accepted_proposals}
    holes_filled = len(holes_with_accepted)
    holes_remaining = holes_attempted - holes_filled
    proposals_submitted = (
        len(coordinator.pending_proposals)
        + len(coordinator.accepted_proposals)
        + len(coordinator.rejected_proposals)
    )
    proposals_accepted = len(coordinator.accepted_proposals)
    proposals_rejected = len(coordinator.rejected_proposals)
    completeness = holes_filled / holes_attempted if holes_attempted else 0.0

    # All obligations met heuristic: every accepted proposal addresses all constraints
    # (We cannot fully verify here without the constraints, so we use a proxy: any rejected
    # proposals indicate some obligation failures.)
    all_obligations_met = (proposals_rejected == 0) and (holes_remaining == 0)

    if holes_attempted == 0:
        status = "EMPTY"
    elif completeness >= 1.0:
        status = "FULLY_EXTENDED"
    elif completeness > 0.0:
        status = "PARTIALLY_EXTENDED"
    else:
        status = "FAILED"

    return GenerationExtensionPartialSectionsWitness(
        witness_id=_make_witness_id(spec_id),
        spec_id=spec_id,
        holes_attempted=holes_attempted,
        holes_filled=holes_filled,
        holes_remaining=holes_remaining,
        proposals_submitted=proposals_submitted,
        proposals_accepted=proposals_accepted,
        proposals_rejected=proposals_rejected,
        extension_completeness_fraction=round(completeness, 4),
        all_obligations_met=all_obligations_met,
        witness_status=status,
        accepted_proposal_ids=[p.proposal_id for p in coordinator.accepted_proposals],
        trust_tier=PROPOSAL_TRUST_TIER,
        witness_timestamp=_now_iso(),
        provenance=(
            f"GenerationExtensionPartialSectionsCoordinator/{coordinator.coordinator_id}"
        ),
    )


def constraints_to_prompt(
    constraints: list[GenerationConstraint],
    hole: dict[str, Any],
) -> str:
    """Render a list of constraints and a hole descriptor as an LLM prompt string.

    This is the main entry-point for prompt construction.  It sorts constraints
    by descending priority, then formats each one using its ``to_prompt_fragment()``
    method.

    Parameters
    ----------
    constraints : list[GenerationConstraint]
        Constraints to include in the prompt.
    hole : dict
        Hole descriptor dict (from ``coordinator.hole_registry``).

    Returns
    -------
    str
        A multi-line string ready for injection into an LLM prompt.
    """
    sorted_constraints = sorted(
        constraints, key=lambda c: c.priority, reverse=True
    )
    lines = [
        "=== Generation Task ===",
        f"Hole ID   : {hole.get('hole_id', 'UNKNOWN')}",
        f"Coordinate: {hole.get('coordinate', 'UNKNOWN')}",
        f"Formula   : {hole.get('formula', 'UNKNOWN')}",
        "",
        "--- Constraints (sorted by priority) ---",
    ]
    for c in sorted_constraints:
        lines.append(c.to_prompt_fragment())
        lines.append("")
    lines.append(
        "Your proposed judgment must satisfy ALL constraints above. "
        f"The trust tier of your output will be set to '{PROPOSAL_TRUST_TIER}' automatically."
    )
    return "\n".join(lines)


def validate_trust_tier_invariant(
    proposals: list[GenerationProposal],
) -> tuple[bool, list[str]]:
    """Validate that every proposal in *proposals* has trust tier ``"PROPOSED"``.

    This is a module-level invariant check useful for bulk validation (e.g. after
    loading proposals from a database or file).

    Parameters
    ----------
    proposals : list[GenerationProposal]
        Proposals to validate.

    Returns
    -------
    tuple[bool, list[str]]
        ``(all_valid, violations)`` where *violations* is a list of human-readable
        descriptions of any proposals that violate the invariant.
    """
    violations: list[str] = []
    for p in proposals:
        if p.trust_tier != PROPOSAL_TRUST_TIER:
            violations.append(
                f"Proposal {p.proposal_id!r} has trust_tier={p.trust_tier!r}; "
                f"expected {PROPOSAL_TRUST_TIER!r}."
            )
    return (len(violations) == 0, violations)


# ---------------------------------------------------------------------------
# Constructor helpers (for convenient object creation in tests / scripts)
# ---------------------------------------------------------------------------


def _make_coordinator(
    partial_section: dict[str, Any] | None = None,
    config: GenerationConfig | None = None,
) -> GenerationExtensionPartialSectionsCoordinator:
    """Construct a fresh coordinator with optional partial section and config.

    Parameters
    ----------
    partial_section : dict | None
        Initial partial section snapshot.  Defaults to empty dict.
    config : GenerationConfig | None
        Generation config.  Defaults to ``build_generation_config()``.

    Returns
    -------
    GenerationExtensionPartialSectionsCoordinator
    """
    cfg = config or build_generation_config()
    return GenerationExtensionPartialSectionsCoordinator(
        coordinator_id=f"coord-{_stable_hash(uuid.uuid4().hex)}",
        partial_section_snapshot=dict(partial_section or {}),
        hole_registry={},
        pending_proposals=[],
        accepted_proposals=[],
        rejected_proposals=[],
        coordination_log=[],
        generation_config={
            "config_id": cfg.config_id,
            "max_proposals_per_hole": cfg.max_proposals_per_hole,
            "generation_source": cfg.generation_source,
            "trust_tier_for_proposals": cfg.trust_tier_for_proposals,
        },
        max_proposals_per_hole=cfg.max_proposals_per_hole,
    )


def _make_analyzer() -> GenerationExtensionPartialSectionsAnalyzer:
    """Construct a fresh analyzer.

    Returns
    -------
    GenerationExtensionPartialSectionsAnalyzer
    """
    return GenerationExtensionPartialSectionsAnalyzer(
        analyzer_id=f"anlz-{_stable_hash(uuid.uuid4().hex)}",
        analysis_log=[],
        proposal_scores={},
        compatibility_matrix={},
        obligation_coverage_map={},
        rejected_reasons={},
        analysis_results=[],
    )


# ---------------------------------------------------------------------------
# Unified architecture cross-references (jugeo.geometry, jugeo.evidence, jugeo.encodings)
# ---------------------------------------------------------------------------

def spec_descent(spec: Any) -> dict[str, Any]:
    """Compute descent data for specification satisfaction.
    
    Specification satisfaction IS descent — satisfying a spec means finding
    a global section that restricts correctly to each local patch.
    
    Parameters
    ----------
    spec : Any
        A Specification object or dict with specification data.
    
    Returns
    -------
    dict[str, Any]
        Descent record with ``cover``, ``local_sections``, ``cocycle_trivial``,
        and ``global_section_exists`` keys.
    """
    try:
        from jugeo.geometry.descent import run_descent, DescentDatum
    except ImportError:
        run_descent = None
        DescentDatum = None

    name = getattr(spec, "name", None) or (spec.get("name") if isinstance(spec, dict) else "unknown")
    coords = getattr(spec, "target_coordinates", None) or (
        spec.get("target_coordinates") if isinstance(spec, dict) else []
    )

    descent: dict[str, Any] = {
        "spec_name": name,
        "cover": list(coords) if coords else [],
        "local_sections": {},
        "cocycle_trivial": None,
        "global_section_exists": None,
    }

    if run_descent is not None:
        try:
            result = run_descent(coords)
            descent["cocycle_trivial"] = getattr(result, "cocycle_trivial", None)
            descent["global_section_exists"] = getattr(result, "global_section_exists", None)
            descent["local_sections"] = getattr(result, "local_sections", {})
        except Exception:
            pass

    return descent


def spec_certificate(result: Any) -> dict[str, Any]:
    """Build an evidence certificate for a satisfaction result.
    
    A satisfaction certificate records that a specification was checked,
    the outcome, and the trust level of the evidence.
    
    Parameters
    ----------
    result : Any
        A satisfaction result object or dict.
    
    Returns
    -------
    dict[str, Any]
        Certificate with ``satisfied``, ``trust_level``, ``witness_hash``,
        ``spec_name``, and ``certificate_id`` keys.
    """
    try:
        from jugeo.evidence.certificates import Certificate, build_certificate
    except ImportError:
        Certificate = None
        build_certificate = None

    import hashlib, uuid

    satisfied = getattr(result, "satisfied", None)
    if satisfied is None and isinstance(result, dict):
        satisfied = result.get("satisfied", result.get("status") == "satisfied")

    spec_name = getattr(result, "spec_name", None) or (
        result.get("spec_name") if isinstance(result, dict) else "unknown"
    )

    cert: dict[str, Any] = {
        "certificate_id": str(uuid.uuid4()),
        "spec_name": spec_name,
        "satisfied": bool(satisfied),
        "trust_level": "VERIFIED" if satisfied else "UNVERIFIED",
        "witness_hash": hashlib.sha256(str(result).encode()).hexdigest()[:16],
        "certificate_obj": None,
    }

    if build_certificate is not None:
        try:
            cert["certificate_obj"] = build_certificate(
                claim=spec_name, satisfied=satisfied, source="specification_satisfaction"
            )
        except Exception:
            pass

    return cert


def spec_encoding(spec: Any) -> dict[str, Any]:
    """Encode a specification as scalar constraints for SMT solving.
    
    Specifications translate to scalar encodings where each clause becomes
    a conjunction of SMT predicates over the target coordinates.
    
    Parameters
    ----------
    spec : Any
        A Specification object or dict.
    
    Returns
    -------
    dict[str, Any]
        Encoding with ``formulas``, ``variables``, ``coordinate_map``,
        and ``encoding_kind`` keys.
    """
    try:
        from jugeo.encodings.scalar_encodings import ScalarEncoder, encode_constraint
    except ImportError:
        ScalarEncoder = None
        encode_constraint = None

    name = getattr(spec, "name", None) or (spec.get("name") if isinstance(spec, dict) else "unknown")
    coords = getattr(spec, "target_coordinates", None) or (
        spec.get("target_coordinates") if isinstance(spec, dict) else []
    )

    encoding: dict[str, Any] = {
        "spec_name": name,
        "encoding_kind": "scalar_conjunction",
        "formulas": [f"(sat {c})" for c in (coords or [])],
        "variables": [f"sat_{c}" for c in (coords or [])],
        "coordinate_map": {c: f"sat_{c}" for c in (coords or [])},
        "encoder": None,
    }

    if encode_constraint is not None:
        try:
            for c in (coords or []):
                enc = encode_constraint(c, name)
                if hasattr(enc, "formula"):
                    encoding["formulas"].append(enc.formula)
        except Exception:
            pass

    if ScalarEncoder is not None:
        try:
            encoding["encoder"] = ScalarEncoder(coordinates=list(coords or []))
        except Exception:
            pass

    return encoding


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    # Constants
    "PROPOSAL_TRUST_TIER",
    "VERIFIED_TRUST_TIER",
    "TRUSTED_TRUST_TIER",
    "UNVERIFIED_TRUST_TIER",
    "GENERATION_SOURCE_LLM",
    "GENERATION_SOURCE_TEMPLATE",
    "GENERATION_SOURCE_MANUAL",
    "GENERATION_SOURCE_RULE",
    "REVIEW_STATUS_PENDING",
    "REVIEW_STATUS_ACCEPTED",
    "REVIEW_STATUS_REJECTED",
    "JUDGMENT_COMPONENTS",
    "MAX_PROPOSALS_PER_HOLE",
    "DEFAULT_GENERATION_SOURCE",
    # Enumerations
    "GenerationKind",
    "ExtensionStatus",
    # Dataclasses
    "GenerationConstraint",
    "GenerationProposal",
    "ExtensionCandidate",
    "GenerationConfig",
    "GenerationExtensionPartialSectionsWitness",
    # Main classes
    "GenerationExtensionPartialSectionsCoordinator",
    "GenerationExtensionPartialSectionsAnalyzer",
    # Module-level functions
    "create_generation_constraint",
    "create_generation_proposal",
    "build_generation_config",
    "check_proposal_satisfies_constraints",
    "check_proposal_extends_partial_section",
    "select_best_proposal",
    "build_extension_from_proposals",
    "generation_witness_from_coordinator",
    "constraints_to_prompt",
    "validate_trust_tier_invariant",
    # Unified architecture cross-references
    "spec_descent",
    "spec_certificate",
    "spec_encoding",
]


# ---------------------------------------------------------------------------
# Smoke test / __main__ entry point
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    print("=" * 70)
    print("s04 — Generation as Extension of Partial Sections — Smoke Test")
    print("=" * 70)

    # ------------------------------------------------------------------
    # Step 1: Create GenerationConfig
    # ------------------------------------------------------------------
    print("\n[1] Building GenerationConfig...")
    cfg = build_generation_config(
        source=GENERATION_SOURCE_LLM,
        max_proposals=5,
        require_all_obligations_met=True,
        allow_partial_fills=True,
    )
    print(f"    config_id                : {cfg.config_id}")
    print(f"    max_proposals_per_hole   : {cfg.max_proposals_per_hole}")
    print(f"    trust_tier_for_proposals : {cfg.trust_tier_for_proposals}")
    assert cfg.trust_tier_for_proposals == PROPOSAL_TRUST_TIER, "Config trust tier invariant!"

    # ------------------------------------------------------------------
    # Step 2: Define a partial section and HoleRecord-like dicts
    # ------------------------------------------------------------------
    print("\n[2] Defining partial section (authored judgments)...")
    authored_j_at_coord_1: dict[str, Any] = {
        "c": "§1.1",
        "phi": "TypeA",
        "A": "alice",
        "E": ["evidence:derivation:001"],
        "O": [],
        "B": [],
        "T": VERIFIED_TRUST_TIER,
        "Pi": "manual_authoring",
    }
    authored_j_at_coord_2: dict[str, Any] = {
        "c": "§1.2",
        "phi": "TypeB",
        "A": "alice",
        "E": ["evidence:derivation:002"],
        "O": ["ob:well_typed"],
        "B": [],
        "T": TRUSTED_TRUST_TIER,
        "Pi": "manual_authoring",
    }
    partial_section: dict[str, Any] = {
        "§1.1": authored_j_at_coord_1,
        "§1.2": authored_j_at_coord_2,
    }
    print(f"    Authored coordinates: {list(partial_section.keys())}")

    # Hole descriptors
    hole_1 = {
        "hole_id": "hole-001",
        "coordinate": "§1.3",
        "formula": "TypeC",
        "obligations": ["ob:well_typed", "ob:evidence_present"],
    }
    hole_2 = {
        "hole_id": "hole-002",
        "coordinate": "§2.1",
        "formula": "FunctionType(TypeA, TypeB)",
        "obligations": ["ob:well_formed_function"],
    }
    print(f"    Holes to fill: {[h['hole_id'] for h in [hole_1, hole_2]]}")

    # ------------------------------------------------------------------
    # Step 3: Create GenerationConstraints
    # ------------------------------------------------------------------
    print("\n[3] Creating GenerationConstraints...")
    c1 = create_generation_constraint(
        hole_id="hole-001",
        coordinate="§1.3",
        kind="type_obligation",
        formula="phi must be a well-formed type extending TypeB",
        required_evidence_kinds=["type_derivation"],
        priority=10,
    )
    c2 = create_generation_constraint(
        hole_id="hole-001",
        coordinate="§1.3",
        kind="evidence_obligation",
        formula="Evidence bundle must include at least one derivation",
        required_evidence_kinds=["derivation"],
        priority=8,
    )
    c3 = create_generation_constraint(
        hole_id="hole-002",
        coordinate="§2.1",
        kind="type_obligation",
        formula="phi must be a function type from TypeA to TypeB",
        required_evidence_kinds=["function_type_derivation"],
        priority=9,
    )
    print(f"    Constraint 1: {c1.constraint_id} [{c1.constraint_kind}]")
    print(f"    Constraint 2: {c2.constraint_id} [{c2.constraint_kind}]")
    print(f"    Constraint 3: {c3.constraint_id} [{c3.constraint_kind}]")

    constraints_by_hole: dict[str, list[GenerationConstraint]] = {
        "hole-001": [c1, c2],
        "hole-002": [c3],
    }

    # ------------------------------------------------------------------
    # Step 4: Create GenerationProposals — verify PROPOSAL trust tier
    # ------------------------------------------------------------------
    print("\n[4] Creating GenerationProposals (trust tier must be PROPOSED)...")
    p1 = create_generation_proposal(
        hole_id="hole-001",
        coordinate="§1.3",
        proposed_text="TypeC is defined as a well-formed type extending TypeB, with derivation evidence.",
        proposed_judgment={
            "c": "§1.3",
            "phi": "TypeC",
            "A": "llm-agent-v1",
            "E": ["evidence:type_derivation:003"],
            "O": ["ob:well_typed", "ob:evidence_present"],
            "B": [],
            "T": PROPOSAL_TRUST_TIER,
            "Pi": "llm_generation",
        },
        source=GENERATION_SOURCE_LLM,
        constraints_addressed=[c1.constraint_id, c2.constraint_id],
        constraints_violated=[],
    )
    p2 = create_generation_proposal(
        hole_id="hole-001",
        coordinate="§1.3",
        proposed_text="TypeC — short form, minimal derivation.",
        proposed_judgment={
            "c": "§1.3",
            "phi": "TypeC",
            "A": "template-engine",
            "E": [],
            "O": ["ob:well_typed"],
            "B": [],
            "T": PROPOSAL_TRUST_TIER,
            "Pi": "template_instantiation",
        },
        source=GENERATION_SOURCE_TEMPLATE,
        constraints_addressed=[c1.constraint_id],
        constraints_violated=[c2.constraint_id],
    )
    p3 = create_generation_proposal(
        hole_id="hole-002",
        coordinate="§2.1",
        proposed_text="FunctionType(TypeA, TypeB) — arrow type, fully derived.",
        proposed_judgment={
            "c": "§2.1",
            "phi": "FunctionType(TypeA, TypeB)",
            "A": "llm-agent-v1",
            "E": ["evidence:function_type_derivation:007"],
            "O": ["ob:well_formed_function"],
            "B": [],
            "T": PROPOSAL_TRUST_TIER,
            "Pi": "llm_generation",
        },
        source=GENERATION_SOURCE_LLM,
        constraints_addressed=[c3.constraint_id],
        constraints_violated=[],
    )

    for p in [p1, p2, p3]:
        assert p.trust_tier == PROPOSAL_TRUST_TIER, f"Trust tier violated: {p.proposal_id}"
        p.check_trust_tier_invariant()
        print(f"    Proposal {p.proposal_id[:14]}... trust_tier={p.trust_tier!r} ✓")

    # Bulk invariant check
    all_valid, violations = validate_trust_tier_invariant([p1, p2, p3])
    assert all_valid, f"Bulk invariant check failed: {violations}"
    print("    Bulk trust-tier invariant: ALL PASS ✓")

    # ------------------------------------------------------------------
    # Step 5: Check proposals satisfy constraints
    # ------------------------------------------------------------------
    print("\n[5] Checking proposal/constraint satisfaction...")
    ok1, issues1 = check_proposal_satisfies_constraints(p1, [c1, c2])
    ok2, issues2 = check_proposal_satisfies_constraints(p2, [c1, c2])
    print(f"    p1 satisfies all hole-001 constraints: {ok1} (issues: {issues1})")
    print(f"    p2 satisfies all hole-001 constraints: {ok2} (issues: {issues2[:1]}...)")
    assert ok1, "p1 should satisfy all constraints"
    assert not ok2, "p2 violates c2, should fail"

    # ------------------------------------------------------------------
    # Step 6: Use ExtensionCandidate to rank proposals
    # ------------------------------------------------------------------
    print("\n[6] Ranking proposals via ExtensionCandidate...")
    candidate = ExtensionCandidate(
        candidate_id=_make_candidate_id("hole-001"),
        hole_id="hole-001",
        proposals=[],
        scored_proposals=[],
        best_proposal_id=None,
        candidate_status="PENDING",
        created_at=_now_iso(),
    )
    candidate.add_proposal(p1)
    candidate.add_proposal(p2)
    candidate.score_and_rank([c1, c2])
    best = candidate.select_best()
    print(f"    Best proposal for hole-001: {best.proposal_id[:14]}...")
    print(f"    Scored ranking: {[(pid[:14]+'...', sc) for pid, sc in candidate.scored_proposals]}")
    assert best is not None
    assert best.proposal_id == p1.proposal_id, "p1 should rank higher than p2"
    print("    Ranking assertion: p1 > p2 ✓")

    # ------------------------------------------------------------------
    # Step 7: Exercise Coordinator — submit / accept / reject
    # ------------------------------------------------------------------
    print("\n[7] Exercising Coordinator...")
    coordinator = _make_coordinator(partial_section=partial_section, config=cfg)

    coordinator.register_hole_for_generation(
        hole_id="hole-001",
        coordinate="§1.3",
        formula="TypeC",
        obligations=["ob:well_typed", "ob:evidence_present"],
    )
    coordinator.register_hole_for_generation(
        hole_id="hole-002",
        coordinate="§2.1",
        formula="FunctionType(TypeA, TypeB)",
        obligations=["ob:well_formed_function"],
    )
    print(f"    Registered holes: {list(coordinator.hole_registry.keys())}")

    # Submit proposals
    coordinator.submit_proposal(p1)
    coordinator.submit_proposal(p2)
    coordinator.submit_proposal(p3)
    print(f"    Pending after submission: {len(coordinator.get_pending_proposals())}")
    assert len(coordinator.get_pending_proposals()) == 3

    # Check compatibility
    compat_ok, compat_issues = coordinator.check_proposal_compatibility(p1)
    print(f"    p1 compatibility with partial section: {compat_ok} (issues={compat_issues})")
    assert compat_ok, "p1 should be compatible with existing partial section"

    # Compute extension candidates
    extension_candidates = coordinator.compute_extension_candidates(constraints_by_hole)
    print(f"    Extension candidates computed: {list(extension_candidates.keys())}")

    # Reject p2 (it violates c2), accept p1 and p3
    coordinator.reject_proposal(p2.proposal_id, reason="Violates evidence obligation c2")
    coordinator.accept_proposal(p1.proposal_id, notes="LLM output meets all constraints")
    coordinator.accept_proposal(p3.proposal_id, notes="Function type correctly derived")

    state = coordinator.summarize_generation_state()
    print(f"    Coordinator state summary:")
    for k, v in state.items():
        print(f"      {k}: {v}")
    assert state["accepted_proposals"] == 2
    assert state["rejected_proposals"] == 1
    assert state["pending_proposals"] == 0

    # ------------------------------------------------------------------
    # Step 8: Exercise Analyzer
    # ------------------------------------------------------------------
    print("\n[8] Exercising Analyzer...")
    analyzer = _make_analyzer()

    # Create fresh proposals for analysis (using same fields but new IDs)
    ap1 = create_generation_proposal(
        hole_id="hole-001",
        coordinate="§1.3",
        proposed_text="TypeC well-formed type with full derivation evidence.",
        proposed_judgment={"c": "§1.3", "phi": "TypeC", "A": "llm", "E": ["ev:003"], "O": [], "B": [], "T": PROPOSAL_TRUST_TIER, "Pi": "llm"},
        source=GENERATION_SOURCE_LLM,
        constraints_addressed=[c1.constraint_id, c2.constraint_id],
    )
    ap2 = create_generation_proposal(
        hole_id="hole-001",
        coordinate="§1.3",
        proposed_text="TC",  # Very short — will score lower
        proposed_judgment={"c": "§1.3", "phi": "TypeC", "A": "template", "E": [], "O": [], "B": [], "T": PROPOSAL_TRUST_TIER, "Pi": "tmpl"},
        source=GENERATION_SOURCE_TEMPLATE,
        constraints_addressed=[c1.constraint_id],
        constraints_violated=[c2.constraint_id],
    )

    r1 = analyzer.analyze_proposal(ap1, [c1, c2], partial_section)
    r2 = analyzer.analyze_proposal(ap2, [c1, c2], partial_section)
    print(f"    ap1 → score={r1['score']}, recommended={r1['recommended_action']}")
    print(f"    ap2 → score={r2['score']}, recommended={r2['recommended_action']}")
    assert r1["recommended_action"] == "ACCEPT", "ap1 should be recommended ACCEPT"
    assert r2["recommended_action"] == "REJECT", "ap2 should be recommended REJECT"

    ranking = analyzer.rank_proposals([ap1, ap2], [c1, c2])
    print(f"    Ranking: {[(pid[:14]+'...', sc) for pid, sc in ranking]}")
    assert ranking[0][1] >= ranking[1][1], "ap1 should outrank ap2"

    report = analyzer.produce_analysis_report()
    print(f"    Report: total={report['total_proposals_analyzed']}, avg_score={report['average_score']}")
    assert report["total_proposals_analyzed"] == 2

    # ------------------------------------------------------------------
    # Step 9: Build extension from accepted proposals
    # ------------------------------------------------------------------
    print("\n[9] Building extended section from accepted proposals...")
    extended = build_extension_from_proposals(
        partial_section=partial_section,
        accepted_proposals=coordinator.get_accepted_proposals(),
    )
    print(f"    Coordinates in extended section: {sorted(extended.keys())}")
    assert "§1.1" in extended, "Authored judgments must be preserved"
    assert "§1.2" in extended, "Authored judgments must be preserved"
    assert "§1.3" in extended, "Hole-001 must be filled"
    assert "§2.1" in extended, "Hole-002 must be filled"
    assert extended["§1.3"]["_trust_tier"] == PROPOSAL_TRUST_TIER
    assert extended["§2.1"]["_trust_tier"] == PROPOSAL_TRUST_TIER
    print("    Extension correctness assertions: PASS ✓")

    # ------------------------------------------------------------------
    # Step 10: Create witness
    # ------------------------------------------------------------------
    print("\n[10] Creating GenerationExtensionPartialSectionsWitness...")
    witness = generation_witness_from_coordinator(
        coordinator=coordinator,
        spec_id="spec-smoke-test-001",
    )
    print(f"    witness_id                      : {witness.witness_id}")
    print(f"    spec_id                         : {witness.spec_id}")
    print(f"    holes_attempted                 : {witness.holes_attempted}")
    print(f"    holes_filled                    : {witness.holes_filled}")
    print(f"    holes_remaining                 : {witness.holes_remaining}")
    print(f"    proposals_submitted             : {witness.proposals_submitted}")
    print(f"    proposals_accepted              : {witness.proposals_accepted}")
    print(f"    proposals_rejected              : {witness.proposals_rejected}")
    print(f"    extension_completeness_fraction : {witness.extension_completeness_fraction}")
    print(f"    all_obligations_met             : {witness.all_obligations_met}")
    print(f"    witness_status                  : {witness.witness_status}")
    print(f"    trust_tier                      : {witness.trust_tier}")
    assert witness.trust_tier == PROPOSAL_TRUST_TIER, "Witness trust tier invariant!"
    assert witness.witness_status == "FULLY_EXTENDED", "All holes should be filled!"
    assert witness.holes_filled == 2
    assert witness.holes_remaining == 0

    # ------------------------------------------------------------------
    # Constraints-to-prompt demo
    # ------------------------------------------------------------------
    print("\n[11] Demo: constraints_to_prompt for LLM injection...")
    prompt_text = constraints_to_prompt([c1, c2], hole_1)
    print("    --- Prompt excerpt (first 400 chars) ---")
    print("   ", prompt_text[:400].replace("\n", "\n    "))

    # ------------------------------------------------------------------
    # Demonstrate check_proposal_extends_partial_section
    # ------------------------------------------------------------------
    print("\n[12] check_proposal_extends_partial_section demo...")
    ext_ok, ext_issues = check_proposal_extends_partial_section(
        proposal=p1,
        partial_section=partial_section,
        authored_judgments=partial_section,
    )
    print(f"    p1 can extend partial section: {ext_ok} (issues: {ext_issues})")
    # p1 was already accepted so might show 'already covered' if coordinate is in partial_section
    # In our test it targets §1.3 which is NOT in partial_section, so should be True.
    assert ext_ok, f"p1 should be able to extend partial section: {ext_issues}"

    # Test with conflicting phi
    conflict_proposal = create_generation_proposal(
        hole_id="hole-001",
        coordinate="§1.1",  # Already covered!
        proposed_text="Conflicting proposal targeting existing coordinate.",
        proposed_judgment={"c": "§1.1", "phi": "TypeZ", "A": "llm", "E": [], "O": [], "B": [], "T": PROPOSAL_TRUST_TIER, "Pi": "llm"},
        source=GENERATION_SOURCE_LLM,
    )
    conf_ok, conf_issues = check_proposal_extends_partial_section(
        proposal=conflict_proposal,
        partial_section=partial_section,
        authored_judgments=partial_section,
    )
    print(f"    Conflicting proposal can extend: {conf_ok} (issues: {conf_issues[:1]}...)")
    assert not conf_ok, "Conflicting proposal should fail extension check"

    # ------------------------------------------------------------------
    # select_best_proposal demo
    # ------------------------------------------------------------------
    print("\n[13] select_best_proposal demo...")
    fresh_proposals = [
        create_generation_proposal(
            hole_id="hole-001", coordinate="§1.3",
            proposed_text="Full derivation with all evidence categories present.",
            proposed_judgment={"c": "§1.3", "phi": "TypeC", "A": "llm", "E": ["ev:001", "ev:002"], "O": [], "B": [], "T": PROPOSAL_TRUST_TIER, "Pi": "llm"},
            source=GENERATION_SOURCE_LLM,
            constraints_addressed=[c1.constraint_id, c2.constraint_id],
        ),
        create_generation_proposal(
            hole_id="hole-001", coordinate="§1.3",
            proposed_text="Partial.",
            proposed_judgment={"c": "§1.3", "phi": "TypeC", "A": "rule", "E": [], "O": [], "B": [], "T": PROPOSAL_TRUST_TIER, "Pi": "rule"},
            source=GENERATION_SOURCE_RULE,
            constraints_addressed=[c1.constraint_id],
            constraints_violated=[c2.constraint_id],
        ),
    ]
    best_fresh = select_best_proposal(fresh_proposals, [c1, c2])
    assert best_fresh is not None
    print(f"    Best proposal selected: {best_fresh.proposal_id[:14]}... source={best_fresh.generation_source}")
    assert best_fresh.generation_source == GENERATION_SOURCE_LLM

    # ------------------------------------------------------------------
    # GenerationKind and ExtensionStatus enum demo
    # ------------------------------------------------------------------
    print("\n[14] Enum demo...")
    print(f"    GenerationKind members: {[e.value for e in GenerationKind]}")
    print(f"    ExtensionStatus members: {[e.value for e in ExtensionStatus]}")
    assert GenerationKind.LLM_COMPLETION.value == "LLM_COMPLETION"
    assert ExtensionStatus.FULLY_EXTENDED.value == "FULLY_EXTENDED"

    # ------------------------------------------------------------------
    # Final summary
    # ------------------------------------------------------------------
    print("\n" + "=" * 70)
    print("ALL SMOKE TEST ASSERTIONS PASSED")
    print("=" * 70)
    print(f"\nModule constants:")
    print(f"  PROPOSAL_TRUST_TIER    = {PROPOSAL_TRUST_TIER!r}")
    print(f"  VERIFIED_TRUST_TIER    = {VERIFIED_TRUST_TIER!r}")
    print(f"  TRUSTED_TRUST_TIER     = {TRUSTED_TRUST_TIER!r}")
    print(f"  UNVERIFIED_TRUST_TIER  = {UNVERIFIED_TRUST_TIER!r}")
    print(f"  JUDGMENT_COMPONENTS    = {JUDGMENT_COMPONENTS}")
    print(f"  MAX_PROPOSALS_PER_HOLE = {MAX_PROPOSALS_PER_HOLE}")
    print(f"  DEFAULT_GENERATION_SOURCE = {DEFAULT_GENERATION_SOURCE!r}")
    print("\nDone.")
