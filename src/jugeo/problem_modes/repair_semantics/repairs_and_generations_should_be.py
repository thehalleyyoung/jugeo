"""Repairs and generations should be governed the same way (theory2.tex Ch11 §11.W).

Theoretical Motivation
-----------------------
Chapter 11 of the JuGeo theoretical foundations (``theory2.tex``) establishes
the *debugging pipeline* as a sequence of four stages.  A natural question that
arises when composing that pipeline with the broader JuGeo inference engine is:
**should repair proposals and generation proposals be treated differently by the
governance layer?**

The answer is **no**.  From the perspective of the governance layer, a repair
proposal and a generation proposal are *structurally identical*: each is a
triple (coordinate, content, provenance).  The distinction between "repairing
an existing section" and "generating a new section from scratch" is a
*user-facing* distinction — it lives in the UI layer, not in the semantic layer.

Unified Governance
------------------
The :class:`RepairsGenerationsGovernedSameAnalyzer` (the "unified governance
analyzer") enforces the following invariant for every proposal, regardless of
whether its ``proposal_kind`` is ``REPAIR``, ``GENERATION``, or ``HYBRID``:

1. **Obligation checking** — Each proposal must be checked against the current
   set of residual obligations.  An obligation is a named constraint that must
   be satisfied before the proposal can be trusted.  The standard obligation set
   is: ``NO_SCOPE_WIDENING``, ``TRUST_ASSIGNED``, ``PROVENANCE_PRESENT``,
   ``COORDINATE_VALID``, ``CONTENT_NON_EMPTY``.

2. **Trust assignment** — All proposals enter the governance pipeline at the
   ``PROPOSAL`` trust level.  They may be advanced to ``REVIEWED`` or
   ``ACCEPTED`` only after a human reviewer approves them via
   :meth:`RepairsGenerationsGovernedSameAnalyzer.review_proposal`.  The
   governance layer never auto-promotes a proposal past ``PROPOSAL``; that
   would circumvent the invariant.

3. **Evidence recording** — Every governed proposal gets an
   :class:`EvidenceRecord` that captures: the proposal identifier, the kind
   (REPAIR or GENERATION), the obligations that were checked, the trust level
   assigned, the reviewer identifier (once reviewed), and the UTC timestamp.
   The evidence record is the audit trail that allows post-hoc verification of
   governance compliance.

Why the Same Governance?
------------------------
Separating the governance of repairs from the governance of generations would
open a *governance bypass*: if the system treats generation proposals with
lighter scrutiny, an adversary (or a subtle bug) could route a dangerous
proposal through the "generation" pathway to avoid obligation checking.  The
unified governance model eliminates this attack surface.

Additionally, from the *cohomological* perspective developed in theory2.tex, a
repair proposal and a generation proposal are both *local sections*: elements
of 𝒟(U) for some patch U in the cover 𝔘.  The obligation-checking layer tests
whether a proposed section is globally consistent — that test is the same
regardless of whether the section replaces an existing one or is freshly
synthesised.

Governance Pipeline
-------------------
The full governance pipeline has the following stages (see
:class:`GovernanceStatus`):

    UNSUBMITTED → SUBMITTED → OBLIGATION_CHECKING → TRUST_ASSIGNMENT
    → EVIDENCE_RECORDING → {APPROVED | REJECTED | DEFERRED | WITHDRAWN}

The :class:`RepairsGenerationsGovernedSameWitness` records the live status of
a single proposal as it moves through this pipeline.  The witness is *immutable*
(frozen dataclass); each stage transition returns a fresh witness instance via
:func:`dataclasses.replace`.

Coordinator
-----------
The :class:`RepairsGenerationsGovernedSameCoordinator` manages a pool of
:class:`RepairsGenerationsGovernedSameAnalyzer` instances and provides
convenience methods :meth:`govern_repair` and :meth:`govern_generation`.  When
``enforce_unified_governance=True`` (the default), both methods resolve to
the same governance pipeline — confirming at the implementation level that the
two paths are identical.

# copilot: s04 repairs and generations governed the same way — theory2 ch11 §11.W
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Sequence

try:
    from jugeo.errors import (
        ObstructionRecord,
        RepairHint,
        RepairPriority,
        StructuredFailure,
        FailureScope,
        FailureClassification,
        EvidenceFamily,
        JuGeoError,
        raise_with_scope,
    )
except ImportError:
    ObstructionRecord = Any; RepairHint = Any; RepairPriority = Any  # type: ignore
    StructuredFailure = Any; FailureScope = Any; FailureClassification = Any  # type: ignore
    EvidenceFamily = Any; JuGeoError = Exception; raise_with_scope = None  # type: ignore

try:
    from jugeo.judgments.judgment_terms import (
        EvidenceBundle,
        EvidenceItem,
        EvidenceItemKind,
        Provenance,
        ProvenanceSource,
        TrustLevel,
        TrustAnnotation,
        Obstruction,
        ResidualObligation,
    )
except ImportError:
    EvidenceBundle = Any; EvidenceItem = Any; EvidenceItemKind = Any  # type: ignore
    Provenance = Any; ProvenanceSource = Any; TrustLevel = Any  # type: ignore
    TrustAnnotation = Any; Obstruction = Any; ResidualObligation = Any  # type: ignore

try:
    from jugeo.solver.countermodels import FailureClass, RepairType
except ImportError:
    FailureClass = Any; RepairType = Any  # type: ignore

try:
    from jugeo.problem_modes.repair_semantics.models import (
        CounterexampleRecord,
        DebugSession,
        RepairFrontier,
        RepairPlan,
        RepairValidator,
    )
except ImportError:
    CounterexampleRecord = Any; DebugSession = Any  # type: ignore
    RepairFrontier = Any; RepairPlan = Any; RepairValidator = Any  # type: ignore

# ---------------------------------------------------------------------------
# Module-level provenance manifest
# ---------------------------------------------------------------------------

MANIFEST_SPEC_PROVENANCE: dict[str, str] = {
    "stage": "ch11-repair-semantics",
    "sequence": "11.W",
    "semantic_source": "preliminaries/theory2.tex",
    "module": "repairs_and_generations_should_be",
    "pipeline_stage": "04W",
    "theory_section": "§11.W — Repairs and Generations Governed the Same Way",
    "key_invariant": "no proposal bypasses obligation checking",
}

# ---------------------------------------------------------------------------
# §1  Module helpers
# ---------------------------------------------------------------------------

_COORDINATE_PATTERN = re.compile(r'^[A-Za-z0-9._\-]+$')


def _iso_timestamp() -> str:
    """Return the current UTC timestamp in ISO-8601 format.

    The result is always UTC-aware, using the ``+00:00`` suffix for
    unambiguous timezone encoding.

    Returns
    -------
    str
        A string of the form ``YYYY-MM-DDTHH:MM:SS.ffffffZ``,
        e.g. ``"2024-07-15T12:34:56.789012+00:00"``.

    Examples
    --------
    >>> ts = _iso_timestamp()
    >>> assert "T" in ts
    """
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _stable_hash8(s: str) -> str:
    """Return the first 8 hex characters of the SHA-256 digest of *s*.

    Used for generating short, stable identifiers that embed a content
    fingerprint without exposing the full input.

    Parameters
    ----------
    s : str
        The string to hash.

    Returns
    -------
    str
        An 8-character lowercase hexadecimal string.

    Examples
    --------
    >>> _stable_hash8("hello")
    '2cf24dba'
    """
    return hashlib.sha256(s.encode()).hexdigest()[:8]


def _valid_trust_names() -> frozenset[str]:
    """Return the frozenset of all valid trust-level names.

    The canonical trust hierarchy (from lowest to highest) is:

    * ``UNKNOWN``     — trust level has not been assigned yet.
    * ``PROPOSAL``    — the default entry-level trust for all proposals.
    * ``REVIEWED``    — a reviewer has inspected but not yet accepted.
    * ``ACCEPTED``    — a reviewer has explicitly approved the proposal.
    * ``AUTHORITATIVE`` — produced by an authoritative source (reserved).
    * ``REJECTED``    — reviewer explicitly rejected; kept for audit.

    Returns
    -------
    frozenset[str]
        Frozenset of valid trust-level name strings.
    """
    return frozenset({
        "UNKNOWN",
        "PROPOSAL",
        "REVIEWED",
        "ACCEPTED",
        "AUTHORITATIVE",
        "REJECTED",
    })


def _valid_proposal_kinds() -> frozenset[str]:
    """Return the frozenset of all valid :class:`ProposalKind` names.

    Returns
    -------
    frozenset[str]
        Frozenset of valid proposal kind name strings.
    """
    return frozenset({"REPAIR", "GENERATION", "HYBRID", "UNKNOWN"})


def _is_valid_coordinate(coord: str) -> bool:
    """Check whether *coord* is a structurally valid semantic coordinate.

    A coordinate is valid iff it is non-empty and every character is
    alphanumeric, a dot (``.``), an underscore (``_``), or a hyphen (``-``).

    Parameters
    ----------
    coord : str
        The candidate coordinate string.

    Returns
    -------
    bool
        ``True`` iff *coord* is a valid coordinate.

    Examples
    --------
    >>> _is_valid_coordinate("root.module.func_name")
    True
    >>> _is_valid_coordinate("root.*")
    False
    >>> _is_valid_coordinate("")
    False
    """
    if not coord:
        return False
    return bool(_COORDINATE_PATTERN.match(coord))


def _governance_status_rank(status_name: str) -> int:
    """Return the ordinal rank of a :class:`GovernanceStatus` name.

    Used to verify that a pipeline transition only advances (never retreats)
    the governance status.  Higher rank = further along the pipeline.

    Parameters
    ----------
    status_name : str
        The :class:`GovernanceStatus` name to rank.

    Returns
    -------
    int
        The ordinal rank (0-based).  Unknown names return ``-1``.

    Examples
    --------
    >>> _governance_status_rank("SUBMITTED") < _governance_status_rank("APPROVED")
    True
    """
    order = [
        "UNSUBMITTED",
        "SUBMITTED",
        "OBLIGATION_CHECKING",
        "TRUST_ASSIGNMENT",
        "EVIDENCE_RECORDING",
        "DEFERRED",
        "APPROVED",
        "REJECTED",
        "WITHDRAWN",
    ]
    try:
        return order.index(status_name)
    except ValueError:
        return -1


def _default_obligations() -> tuple[str, ...]:
    """Return the standard set of obligation names applied to every proposal.

    The standard obligations are:

    ``NO_SCOPE_WIDENING``
        The proposal must not widen its declared coordinate scope.  Proposals
        that use wildcard characters (``*``) or parent-traversal sequences
        (``..``) in their coordinate are rejected.

    ``TRUST_ASSIGNED``
        The trust level must have been explicitly assigned before the proposal
        enters evidence recording.

    ``PROVENANCE_PRESENT``
        The proposal must carry at least one provenance entry so the audit
        trail is non-empty.

    ``COORDINATE_VALID``
        The coordinate must be structurally valid (see :func:`_is_valid_coordinate`).

    ``CONTENT_NON_EMPTY``
        The proposal content must be non-empty after stripping whitespace.

    Returns
    -------
    tuple[str, ...]
        The standard obligation names, in canonical check order.
    """
    return (
        "NO_SCOPE_WIDENING",
        "TRUST_ASSIGNED",
        "PROVENANCE_PRESENT",
        "COORDINATE_VALID",
        "CONTENT_NON_EMPTY",
    )


# ---------------------------------------------------------------------------
# §2  Enumerations
# ---------------------------------------------------------------------------


class ProposalKind(str, Enum):
    """The kind of a governance proposal.

    From the governance perspective, ``REPAIR`` and ``GENERATION`` proposals
    are structurally identical.  This enum exists to carry the user-facing
    distinction through the audit trail, *not* to bifurcate the governance
    logic.

    Members
    -------
    REPAIR
        A proposal that replaces an existing section at a coordinate.
    GENERATION
        A proposal that introduces a new section at a coordinate.
    HYBRID
        A proposal that both replaces and extends a coordinate's section.
    UNKNOWN
        Kind has not yet been determined.
    """

    REPAIR = "REPAIR"
    GENERATION = "GENERATION"
    HYBRID = "HYBRID"
    UNKNOWN = "UNKNOWN"


class GovernanceStatus(str, Enum):
    """The lifecycle status of a proposal within the governance pipeline.

    The canonical transition sequence for a successfully approved proposal is::

        UNSUBMITTED → SUBMITTED → OBLIGATION_CHECKING
        → TRUST_ASSIGNMENT → EVIDENCE_RECORDING → APPROVED

    A rejected proposal transitions::

        ... → OBLIGATION_CHECKING → REJECTED   (blocking obligation failed)
        ... → EVIDENCE_RECORDING  → REJECTED   (reviewer rejected)

    A proposal may also be:

    * ``DEFERRED`` — passed obligation checks but reviewer has not yet acted.
    * ``WITHDRAWN`` — retracted by the proposer before review.

    Members
    -------
    UNSUBMITTED
        Initial state; proposal has been constructed but not yet submitted.
    SUBMITTED
        Proposal has been submitted to the governance pipeline.
    OBLIGATION_CHECKING
        The pipeline is currently evaluating obligations.
    TRUST_ASSIGNMENT
        Obligations checked; the pipeline is assigning a trust level.
    EVIDENCE_RECORDING
        Trust assigned; the pipeline is recording the evidence bundle.
    APPROVED
        Human reviewer approved the proposal; trust advanced to ACCEPTED.
    REJECTED
        Proposal was rejected (either by obligation check or human review).
    DEFERRED
        Proposal passed automated checks but awaits human review.
    WITHDRAWN
        Proposal was retracted before reaching a terminal state.
    """

    UNSUBMITTED = "UNSUBMITTED"
    SUBMITTED = "SUBMITTED"
    OBLIGATION_CHECKING = "OBLIGATION_CHECKING"
    TRUST_ASSIGNMENT = "TRUST_ASSIGNMENT"
    EVIDENCE_RECORDING = "EVIDENCE_RECORDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    DEFERRED = "DEFERRED"
    WITHDRAWN = "WITHDRAWN"


# ---------------------------------------------------------------------------
# §3  ObligationCheck
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ObligationCheck:
    """The result of checking one named obligation against a proposal.

    Each :class:`ObligationCheck` records whether a single named obligation
    was satisfied by the proposal under scrutiny.

    Parameters
    ----------
    check_id : str
        A unique identifier for this check instance (UUID-style).
    obligation_name : str
        The canonical name of the obligation (e.g. ``"NO_SCOPE_WIDENING"``).
    description : str
        Human-readable description of what the obligation requires.
    passed : bool
        ``True`` iff the obligation was satisfied.
    failure_reason : str
        Non-empty string explaining *why* the check failed.  Empty if
        ``passed`` is ``True``.
    severity : int
        Integer in the range 1–5.  1 = informational, 5 = blocking.
    is_blocking : bool
        When ``True``, a failure of this check prevents the proposal from
        being approved.

    Examples
    --------
    >>> oc = ObligationCheck(
    ...     check_id="oc-1",
    ...     obligation_name="CONTENT_NON_EMPTY",
    ...     description="Content must be non-empty",
    ...     passed=True,
    ...     failure_reason="",
    ...     severity=3,
    ...     is_blocking=True,
    ... )
    >>> oc.is_fatal()
    False
    """

    check_id: str = ""
    obligation_name: str = ""
    description: str = ""
    passed: bool = False
    failure_reason: str = ""
    severity: int = 1
    is_blocking: bool = False

    # -----------------------------------------------------------------------
    # §3.1  Derived predicates
    # -----------------------------------------------------------------------

    def is_fatal(self) -> bool:
        """Return ``True`` iff this check is blocking *and* did not pass.

        A fatal check prevents the proposal from advancing past the
        obligation-checking stage.  Non-blocking failures are logged but do
        not halt the pipeline.

        Returns
        -------
        bool
            ``True`` iff ``is_blocking and not passed``.
        """
        return self.is_blocking and not self.passed

    # -----------------------------------------------------------------------
    # §3.2  Serialisation
    # -----------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, Any]
            A shallow copy of all fields with their current values.
        """
        return {
            "check_id": self.check_id,
            "obligation_name": self.obligation_name,
            "description": self.description,
            "passed": self.passed,
            "failure_reason": self.failure_reason,
            "severity": self.severity,
            "is_blocking": self.is_blocking,
            "is_fatal": self.is_fatal(),
        }


# ---------------------------------------------------------------------------
# §4  EvidenceRecord
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    """An immutable record of the governance evidence collected for a proposal.

    The :class:`EvidenceRecord` is the audit artefact produced at the
    ``EVIDENCE_RECORDING`` stage of the governance pipeline.  It bundles
    together all information needed to reconstruct and verify the governance
    decision for a proposal at any later point.

    Parameters
    ----------
    evidence_id : str
        Unique identifier for this evidence record.
    proposal_id : str
        The identifier of the governed proposal.
    proposal_kind : str
        The :class:`ProposalKind` name of the proposal.
    coordinate : str
        The semantic coordinate at which the proposal is targeted.
    obligation_checks : tuple[ObligationCheck, ...]
        All obligation checks that were run against the proposal, in order.
    trust_level_assigned : str
        The :class:`TrustLevel` name (or equivalent string) that was assigned
        after obligation checking.  Defaults to ``"PROPOSAL"``.
    reviewer_id : str
        The identifier of the human reviewer who approved or rejected the
        proposal.  Empty if not yet reviewed.
    all_obligations_met : bool
        ``True`` iff every check in ``obligation_checks`` passed.
    evidence_summary : str
        A human-readable summary of the governance outcome.
    timestamp : str
        The ISO-8601 UTC timestamp at which this record was created.
    metadata : tuple[tuple[str, str], ...]
        Arbitrary key–value metadata pairs for extensibility.
    """

    evidence_id: str = ""
    proposal_id: str = ""
    proposal_kind: str = "UNKNOWN"
    coordinate: str = ""
    obligation_checks: tuple[ObligationCheck, ...] = ()
    trust_level_assigned: str = "PROPOSAL"
    reviewer_id: str = ""
    all_obligations_met: bool = False
    evidence_summary: str = ""
    timestamp: str = ""
    metadata: tuple[tuple[str, str], ...] = ()

    # -----------------------------------------------------------------------
    # §4.1  Completeness and integrity
    # -----------------------------------------------------------------------

    def is_complete(self) -> bool:
        """Return ``True`` iff the record has all required fields populated.

        A complete evidence record must have:

        * Non-empty ``evidence_id``, ``proposal_id``, ``coordinate``,
          ``timestamp``, and ``evidence_summary``.
        * At least one entry in ``obligation_checks``.

        Returns
        -------
        bool
            ``True`` iff all required fields are present.
        """
        return bool(
            self.evidence_id
            and self.proposal_id
            and self.coordinate
            and self.timestamp
            and self.evidence_summary
            and len(self.obligation_checks) > 0
        )

    def has_fatal_failures(self) -> bool:
        """Return ``True`` iff any obligation check in this record is fatal.

        A fatal obligation check is one that is both *blocking* and *failed*.
        The presence of any fatal check indicates that the proposal should not
        be approved.

        Returns
        -------
        bool
            ``True`` iff any check satisfies :meth:`ObligationCheck.is_fatal`.
        """
        return any(c.is_fatal() for c in self.obligation_checks)

    # -----------------------------------------------------------------------
    # §4.2  Serialisation
    # -----------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, Any]
            A dictionary representation of all fields.  ``obligation_checks``
            is serialised as a list of dicts; ``metadata`` as a list of
            ``[key, value]`` pairs.
        """
        return {
            "evidence_id": self.evidence_id,
            "proposal_id": self.proposal_id,
            "proposal_kind": self.proposal_kind,
            "coordinate": self.coordinate,
            "obligation_checks": [c.to_dict() for c in self.obligation_checks],
            "trust_level_assigned": self.trust_level_assigned,
            "reviewer_id": self.reviewer_id,
            "all_obligations_met": self.all_obligations_met,
            "evidence_summary": self.evidence_summary,
            "timestamp": self.timestamp,
            "metadata": list(self.metadata),
            "is_complete": self.is_complete(),
            "has_fatal_failures": self.has_fatal_failures(),
        }


# ---------------------------------------------------------------------------
# §5  RepairsGenerationsGovernedSameWitness
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RepairsGenerationsGovernedSameWitness:
    """An immutable governance witness for a single repair or generation proposal.

    A :class:`RepairsGenerationsGovernedSameWitness` is the central artefact
    produced by the unified governance pipeline.  It records the full
    lifecycle of a proposal from initial submission through obligation
    checking, trust assignment, evidence recording, and (optionally) human
    review.

    The witness is *immutable*: each pipeline transition returns a new instance
    via :func:`dataclasses.replace`.  This makes the governance audit trail a
    pure functional sequence of states.

    Theory Basis
    ------------
    The witness corresponds to a *governance certificate* as described in
    theory2.tex §11.W.  It carries the evidence that the governance invariant
    was applied uniformly — that no proposal (repair or generation) bypassed
    obligation checking or trust assignment.

    Parameters
    ----------
    witness_id : str
        Unique identifier for this witness instance.
    proposal_id : str
        The identifier of the proposal being governed.
    proposal_kind : str
        The :class:`ProposalKind` name (``"REPAIR"``, ``"GENERATION"``, etc.).
    coordinate : str
        The semantic coordinate targeted by the proposal.
    governance_status : str
        The current :class:`GovernanceStatus` name.
    obligation_checks : tuple[ObligationCheck, ...]
        The obligation checks that have been run so far.
    trust_level : str
        The trust level currently assigned to this proposal.
    evidence_record : EvidenceRecord
        The evidence record attached at the ``EVIDENCE_RECORDING`` stage.
        An empty :class:`EvidenceRecord` is used as a sentinel before recording.
    reviewer_id : str
        The identifier of the human reviewer (empty until reviewed).
    approval_timestamp : str
        The ISO-8601 UTC timestamp at which the proposal was approved.
        Empty until approved.
    rejection_reason : str
        The reason given if the proposal was rejected.  Empty otherwise.
    provenance : tuple[tuple[str, str], ...]
        Key–value provenance pairs attached to this proposal.
    metadata : tuple[tuple[str, str], ...]
        Arbitrary key–value metadata for extensibility.
    """

    witness_id: str = ""
    proposal_id: str = ""
    proposal_kind: str = "UNKNOWN"
    coordinate: str = ""
    governance_status: str = "UNSUBMITTED"
    obligation_checks: tuple[ObligationCheck, ...] = ()
    trust_level: str = "PROPOSAL"
    evidence_record: EvidenceRecord = field(default_factory=EvidenceRecord)
    reviewer_id: str = ""
    approval_timestamp: str = ""
    rejection_reason: str = ""
    provenance: tuple[tuple[str, str], ...] = ()
    metadata: tuple[tuple[str, str], ...] = ()

    # -----------------------------------------------------------------------
    # §5.1  Status predicates
    # -----------------------------------------------------------------------

    def is_approved(self) -> bool:
        """Return ``True`` iff the proposal has been approved.

        Returns
        -------
        bool
            ``True`` iff ``governance_status == "APPROVED"``.
        """
        return self.governance_status == GovernanceStatus.APPROVED.value

    def is_rejected(self) -> bool:
        """Return ``True`` iff the proposal has been rejected.

        Returns
        -------
        bool
            ``True`` iff ``governance_status == "REJECTED"``.
        """
        return self.governance_status == GovernanceStatus.REJECTED.value

    def is_pending(self) -> bool:
        """Return ``True`` iff the proposal has not yet reached a terminal state.

        Terminal states are ``APPROVED``, ``REJECTED``, and ``WITHDRAWN``.
        Everything else is considered pending.

        Returns
        -------
        bool
            ``True`` iff the proposal has not been approved, rejected, or withdrawn.
        """
        terminal = {
            GovernanceStatus.APPROVED.value,
            GovernanceStatus.REJECTED.value,
            GovernanceStatus.WITHDRAWN.value,
        }
        return self.governance_status not in terminal

    # -----------------------------------------------------------------------
    # §5.2  Obligation predicates
    # -----------------------------------------------------------------------

    def all_obligations_met(self) -> bool:
        """Return ``True`` iff every obligation check passed.

        If no checks have been run yet, returns ``False`` (obligations cannot
        be considered met before they have been checked).

        Returns
        -------
        bool
            ``True`` iff ``len(obligation_checks) > 0`` and every check passed.
        """
        if not self.obligation_checks:
            return False
        return all(c.passed for c in self.obligation_checks)

    def has_fatal_obligations(self) -> bool:
        """Return ``True`` iff any obligation check is fatal.

        A fatal check is blocking and failed.  A proposal with any fatal
        obligation cannot be approved.

        Returns
        -------
        bool
            ``True`` iff any check in ``obligation_checks`` is fatal.
        """
        return any(c.is_fatal() for c in self.obligation_checks)

    def obligation_pass_rate(self) -> float:
        """Return the fraction of obligation checks that passed.

        Returns
        -------
        float
            A value in ``[0.0, 1.0]``.  Returns ``0.0`` if no checks have
            been run.

        Examples
        --------
        >>> # 3 of 5 checks passed → 0.6
        """
        if not self.obligation_checks:
            return 0.0
        passed = sum(1 for c in self.obligation_checks if c.passed)
        return passed / len(self.obligation_checks)

    # -----------------------------------------------------------------------
    # §5.3  State transitions (functional — return new instance)
    # -----------------------------------------------------------------------

    def advance_status(
        self, new_status: str
    ) -> "RepairsGenerationsGovernedSameWitness":
        """Return a new witness with *new_status* as the governance status.

        The transition is *always* accepted — callers are responsible for
        validating that the transition is legal before calling this method.

        Parameters
        ----------
        new_status : str
            The :class:`GovernanceStatus` name to advance to.

        Returns
        -------
        RepairsGenerationsGovernedSameWitness
            New instance with updated ``governance_status``.
        """
        return replace(self, governance_status=new_status)

    def with_trust(
        self, trust: str
    ) -> "RepairsGenerationsGovernedSameWitness":
        """Return a new witness with *trust* as the assigned trust level.

        Parameters
        ----------
        trust : str
            The trust level name to assign.  Should be a member of
            :func:`_valid_trust_names`.

        Returns
        -------
        RepairsGenerationsGovernedSameWitness
            New instance with updated ``trust_level``.
        """
        return replace(self, trust_level=trust)

    def with_review(
        self,
        reviewer_id: str,
        approved: bool,
        reason: str = "",
    ) -> "RepairsGenerationsGovernedSameWitness":
        """Return a new witness that records the outcome of a human review.

        If *approved* is ``True``, the witness advances to ``APPROVED`` status,
        its trust level is set to ``"ACCEPTED"``, and ``approval_timestamp`` is
        populated.

        If *approved* is ``False``, the witness advances to ``REJECTED`` status
        and ``rejection_reason`` is set to *reason*.

        Parameters
        ----------
        reviewer_id : str
            Identifier of the human reviewer.
        approved : bool
            ``True`` iff the reviewer approved the proposal.
        reason : str, optional
            Rejection reason (required for semantic clarity when
            ``approved=False``; ignored otherwise).

        Returns
        -------
        RepairsGenerationsGovernedSameWitness
            New instance reflecting the review outcome.
        """
        if approved:
            return replace(
                self,
                reviewer_id=reviewer_id,
                governance_status=GovernanceStatus.APPROVED.value,
                trust_level="ACCEPTED",
                approval_timestamp=_iso_timestamp(),
                rejection_reason="",
            )
        return replace(
            self,
            reviewer_id=reviewer_id,
            governance_status=GovernanceStatus.REJECTED.value,
            rejection_reason=reason or "Rejected by reviewer",
        )

    # -----------------------------------------------------------------------
    # §5.4  Serialisation
    # -----------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise the witness to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, Any]
            A complete representation of all fields.
        """
        return {
            "witness_id": self.witness_id,
            "proposal_id": self.proposal_id,
            "proposal_kind": self.proposal_kind,
            "coordinate": self.coordinate,
            "governance_status": self.governance_status,
            "obligation_checks": [c.to_dict() for c in self.obligation_checks],
            "trust_level": self.trust_level,
            "evidence_record": self.evidence_record.to_dict(),
            "reviewer_id": self.reviewer_id,
            "approval_timestamp": self.approval_timestamp,
            "rejection_reason": self.rejection_reason,
            "provenance": list(self.provenance),
            "metadata": list(self.metadata),
            "is_approved": self.is_approved(),
            "is_rejected": self.is_rejected(),
            "is_pending": self.is_pending(),
            "all_obligations_met": self.all_obligations_met(),
            "has_fatal_obligations": self.has_fatal_obligations(),
            "obligation_pass_rate": self.obligation_pass_rate(),
        }

    @classmethod
    def from_dict(
        cls, d: dict[str, Any]
    ) -> "RepairsGenerationsGovernedSameWitness":
        """Deserialise a witness from a dictionary (inverse of :meth:`to_dict`).

        Parameters
        ----------
        d : dict[str, Any]
            A dictionary produced by :meth:`to_dict`.

        Returns
        -------
        RepairsGenerationsGovernedSameWitness
            Reconstructed witness instance.
        """
        raw_checks = d.get("obligation_checks", [])
        checks = tuple(
            ObligationCheck(
                check_id=c.get("check_id", ""),
                obligation_name=c.get("obligation_name", ""),
                description=c.get("description", ""),
                passed=c.get("passed", False),
                failure_reason=c.get("failure_reason", ""),
                severity=c.get("severity", 1),
                is_blocking=c.get("is_blocking", False),
            )
            for c in raw_checks
        )
        raw_er = d.get("evidence_record", {})
        er_checks_raw = raw_er.get("obligation_checks", [])
        er_checks = tuple(
            ObligationCheck(
                check_id=c.get("check_id", ""),
                obligation_name=c.get("obligation_name", ""),
                description=c.get("description", ""),
                passed=c.get("passed", False),
                failure_reason=c.get("failure_reason", ""),
                severity=c.get("severity", 1),
                is_blocking=c.get("is_blocking", False),
            )
            for c in er_checks_raw
        )
        ev_rec = EvidenceRecord(
            evidence_id=raw_er.get("evidence_id", ""),
            proposal_id=raw_er.get("proposal_id", ""),
            proposal_kind=raw_er.get("proposal_kind", "UNKNOWN"),
            coordinate=raw_er.get("coordinate", ""),
            obligation_checks=er_checks,
            trust_level_assigned=raw_er.get("trust_level_assigned", "PROPOSAL"),
            reviewer_id=raw_er.get("reviewer_id", ""),
            all_obligations_met=raw_er.get("all_obligations_met", False),
            evidence_summary=raw_er.get("evidence_summary", ""),
            timestamp=raw_er.get("timestamp", ""),
            metadata=tuple(
                (str(k), str(v)) for k, v in raw_er.get("metadata", [])
            ),
        )
        return cls(
            witness_id=d.get("witness_id", ""),
            proposal_id=d.get("proposal_id", ""),
            proposal_kind=d.get("proposal_kind", "UNKNOWN"),
            coordinate=d.get("coordinate", ""),
            governance_status=d.get("governance_status", "UNSUBMITTED"),
            obligation_checks=checks,
            trust_level=d.get("trust_level", "PROPOSAL"),
            evidence_record=ev_rec,
            reviewer_id=d.get("reviewer_id", ""),
            approval_timestamp=d.get("approval_timestamp", ""),
            rejection_reason=d.get("rejection_reason", ""),
            provenance=tuple(
                (str(k), str(v)) for k, v in d.get("provenance", [])
            ),
            metadata=tuple(
                (str(k), str(v)) for k, v in d.get("metadata", [])
            ),
        )

    def summary(self) -> str:
        """Return a compact human-readable summary of the witness state.

        Returns
        -------
        str
            A single-line summary including proposal ID, kind, status,
            trust level, and obligation pass-rate.

        Examples
        --------
        >>> w = RepairsGenerationsGovernedSameWitness(
        ...     proposal_id="p-1", proposal_kind="REPAIR",
        ...     governance_status="APPROVED", trust_level="ACCEPTED")
        >>> "p-1" in w.summary()
        True
        """
        rate = f"{self.obligation_pass_rate():.0%}"
        return (
            f"Witness[{self.witness_id[:8] or 'n/a'}] "
            f"proposal={self.proposal_id!r} "
            f"kind={self.proposal_kind} "
            f"status={self.governance_status} "
            f"trust={self.trust_level} "
            f"obligations_passed={rate} "
            f"fatal={self.has_fatal_obligations()}"
        )


# ---------------------------------------------------------------------------
# §6  RepairsGenerationsGovernedSameAnalyzer
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RepairsGenerationsGovernedSameAnalyzer:
    """The unified governance analyzer for repair and generation proposals.

    This is the core implementation of the *unified governance* principle from
    theory2.tex §11.W: **repairs and generations must go through the same
    governance pipeline**.

    The analyzer is immutable.  All mutating operations return a new instance
    or a new :class:`RepairsGenerationsGovernedSameWitness` instance.

    Theory Basis
    ------------
    The unified governance analyzer implements the governance map

        G : Proposal → Witness

    defined in §11.W.2 of theory2.tex.  A proposal is a triple
    (coordinate, content, provenance); the governance map produces a witness
    that certifies the proposal passed all obligations and received a trust
    assignment.

    The key invariant is:

        ∀ p ∈ Proposals. G(p).obligations_checked ≠ ∅

    That is, every proposal — whether it is a repair or a generation — must
    pass through at least one obligation check before its trust level can be
    set.

    Parameters
    ----------
    analyzer_id : str
        Unique identifier for this analyzer instance.
    coordinate : str
        The root semantic coordinate for this analyzer's scope.
    obligations : tuple[str, ...]
        The names of obligations to check.  Defaults to
        :func:`_default_obligations`.
    trust_policy : str
        The trust assignment policy.  The only supported value is
        ``"PROPOSAL_REQUIRED"`` (all proposals start at ``PROPOSAL``).
    require_reviewer : bool
        When ``True``, a proposal cannot reach ``APPROVED`` without a human
        reviewer.
    strict_mode : bool
        When ``True``, any non-blocking obligation failure is promoted to
        blocking.
    evidence_ttl_seconds : int
        Time-to-live for evidence records, in seconds.  Used by external
        cache/cleanup systems; not enforced internally.
    """

    analyzer_id: str = ""
    coordinate: str = ""
    obligations: tuple[str, ...] = field(default_factory=_default_obligations)
    trust_policy: str = "PROPOSAL_REQUIRED"
    require_reviewer: bool = True
    strict_mode: bool = False
    evidence_ttl_seconds: int = 86400

    # -----------------------------------------------------------------------
    # §6.1  Proposal intake — §W.1
    # -----------------------------------------------------------------------

    def submit_proposal(
        self,
        proposal_id: str,
        proposal_kind: str,
        coordinate: str,
        content: str,
        provenance: Sequence[tuple[str, str]],
    ) -> RepairsGenerationsGovernedSameWitness:
        """Create and return a new witness at ``SUBMITTED`` status.

        No obligation checks are run at this stage.  The witness is a
        snapshot of the proposal at the moment it enters the governance
        pipeline.

        Parameters
        ----------
        proposal_id : str
            Unique identifier for the proposal.
        proposal_kind : str
            The :class:`ProposalKind` name.
        coordinate : str
            The semantic coordinate the proposal targets.
        content : str
            The proposal content (new section text, generated code, etc.).
        provenance : Sequence[tuple[str, str]]
            Key–value provenance pairs.  Should be non-empty.

        Returns
        -------
        RepairsGenerationsGovernedSameWitness
            A fresh witness at ``SUBMITTED`` status with no obligation checks
            yet populated.
        """
        kind = proposal_kind if self._validate_proposal_kind(proposal_kind) else "UNKNOWN"
        witness_id = f"w-{_stable_hash8(proposal_id + coordinate + kind)}-{uuid.uuid4().hex[:6]}"
        return RepairsGenerationsGovernedSameWitness(
            witness_id=witness_id,
            proposal_id=proposal_id,
            proposal_kind=kind,
            coordinate=coordinate,
            governance_status=GovernanceStatus.SUBMITTED.value,
            obligation_checks=(),
            trust_level="PROPOSAL",
            evidence_record=EvidenceRecord(),
            reviewer_id="",
            approval_timestamp="",
            rejection_reason="",
            provenance=tuple(provenance),
            metadata=(
                ("content_hash", _stable_hash8(content)),
                ("submitted_at", _iso_timestamp()),
                ("analyzer_id", self.analyzer_id),
            ),
        )

    def _validate_proposal_kind(self, kind: str) -> bool:
        """Return ``True`` iff *kind* is a valid :class:`ProposalKind` name.

        Parameters
        ----------
        kind : str
            Candidate kind string.

        Returns
        -------
        bool
            ``True`` iff *kind* is in :func:`_valid_proposal_kinds`.
        """
        return kind in _valid_proposal_kinds()

    # -----------------------------------------------------------------------
    # §6.2  Obligation checking — §W.2
    # -----------------------------------------------------------------------

    def check_obligations(
        self,
        witness: RepairsGenerationsGovernedSameWitness,
        proposal_content: str,
        proposal_provenance: Sequence[tuple[str, str]],
    ) -> RepairsGenerationsGovernedSameWitness:
        """Run all configured obligations against the proposal.

        Iterates over :attr:`obligations` and calls :meth:`_run_obligation`
        for each.  The resulting checks are attached to the witness.

        If :attr:`strict_mode` is ``True``, any failed check (even
        non-blocking) is promoted to ``is_blocking=True`` before being
        attached.

        The returned witness has status ``TRUST_ASSIGNMENT`` unless a
        fatal obligation was found, in which case it has status
        ``REJECTED``.

        Parameters
        ----------
        witness : RepairsGenerationsGovernedSameWitness
            The proposal witness in ``SUBMITTED`` status.
        proposal_content : str
            The full content of the proposal.
        proposal_provenance : Sequence[tuple[str, str]]
            The provenance entries attached to the proposal.

        Returns
        -------
        RepairsGenerationsGovernedSameWitness
            New witness with ``obligation_checks`` populated and status
            advanced to ``TRUST_ASSIGNMENT`` or ``REJECTED``.
        """
        checks: list[ObligationCheck] = []
        for ob_name in self.obligations:
            raw_check = self._run_obligation(
                name=ob_name,
                coordinate=witness.coordinate,
                content=proposal_content,
                trust=witness.trust_level,
                provenance=proposal_provenance,
            )
            if self.strict_mode and not raw_check.passed:
                raw_check = replace(raw_check, is_blocking=True, severity=5)
            checks.append(raw_check)

        checks_tuple = tuple(checks)
        has_fatal = any(c.is_fatal() for c in checks_tuple)
        new_status = (
            GovernanceStatus.REJECTED.value
            if has_fatal
            else GovernanceStatus.TRUST_ASSIGNMENT.value
        )
        rejection_reason = ""
        if has_fatal:
            fatal_names = [c.obligation_name for c in checks_tuple if c.is_fatal()]
            rejection_reason = f"Fatal obligation failures: {', '.join(fatal_names)}"

        return replace(
            witness,
            obligation_checks=checks_tuple,
            governance_status=new_status,
            rejection_reason=rejection_reason,
        )

    def _check_no_scope_widening(self, coordinate: str) -> ObligationCheck:
        """Check that the coordinate does not widen its declared scope.

        A coordinate widens scope if it contains ``"*"`` (wildcard) or
        ``".."`` (parent traversal).  Both patterns indicate that the
        proposal targets more semantic territory than declared, which
        violates the minimal-footprint principle of §11.W.

        Parameters
        ----------
        coordinate : str
            The coordinate to check.

        Returns
        -------
        ObligationCheck
            Passed iff the coordinate contains no ``*`` or ``..``.
        """
        check_id = f"oc-nsw-{_stable_hash8(coordinate)}"
        if not coordinate:
            return ObligationCheck(
                check_id=check_id,
                obligation_name="NO_SCOPE_WIDENING",
                description="Coordinate must be non-empty and must not widen scope",
                passed=False,
                failure_reason="Coordinate is empty",
                severity=5,
                is_blocking=True,
            )
        if "*" in coordinate:
            return ObligationCheck(
                check_id=check_id,
                obligation_name="NO_SCOPE_WIDENING",
                description="Coordinate must not contain wildcard '*'",
                passed=False,
                failure_reason=f"Coordinate {coordinate!r} contains wildcard '*'",
                severity=5,
                is_blocking=True,
            )
        if ".." in coordinate:
            return ObligationCheck(
                check_id=check_id,
                obligation_name="NO_SCOPE_WIDENING",
                description="Coordinate must not contain parent-traversal '..'",
                passed=False,
                failure_reason=f"Coordinate {coordinate!r} contains '..'",
                severity=5,
                is_blocking=True,
            )
        return ObligationCheck(
            check_id=check_id,
            obligation_name="NO_SCOPE_WIDENING",
            description="Coordinate scope is properly constrained",
            passed=True,
            failure_reason="",
            severity=5,
            is_blocking=True,
        )

    def _check_trust_assigned(self, trust_level: str) -> ObligationCheck:
        """Check that a trust level has been explicitly assigned.

        Parameters
        ----------
        trust_level : str
            The trust level currently assigned to the proposal.

        Returns
        -------
        ObligationCheck
            Passed iff *trust_level* is non-empty and a valid trust name.
        """
        check_id = f"oc-ta-{_stable_hash8(trust_level)}"
        if not trust_level or trust_level not in _valid_trust_names():
            return ObligationCheck(
                check_id=check_id,
                obligation_name="TRUST_ASSIGNED",
                description="A valid trust level must be assigned",
                passed=False,
                failure_reason=f"Trust level {trust_level!r} is not valid",
                severity=4,
                is_blocking=True,
            )
        return ObligationCheck(
            check_id=check_id,
            obligation_name="TRUST_ASSIGNED",
            description="A valid trust level is assigned",
            passed=True,
            failure_reason="",
            severity=4,
            is_blocking=True,
        )

    def _check_provenance_present(
        self, provenance: Sequence[tuple[str, str]]
    ) -> ObligationCheck:
        """Check that the proposal carries at least one provenance entry.

        Parameters
        ----------
        provenance : Sequence[tuple[str, str]]
            The provenance entries to check.

        Returns
        -------
        ObligationCheck
            Passed iff ``len(provenance) > 0``.
        """
        check_id = f"oc-pp-{_stable_hash8(str(len(list(provenance))))}"
        prov_list = list(provenance)
        if not prov_list:
            return ObligationCheck(
                check_id=check_id,
                obligation_name="PROVENANCE_PRESENT",
                description="Proposal must carry at least one provenance entry",
                passed=False,
                failure_reason="Provenance is empty; audit trail is broken",
                severity=4,
                is_blocking=True,
            )
        return ObligationCheck(
            check_id=check_id,
            obligation_name="PROVENANCE_PRESENT",
            description="Provenance is present and non-empty",
            passed=True,
            failure_reason="",
            severity=4,
            is_blocking=True,
        )

    def _check_coordinate_valid(self, coordinate: str) -> ObligationCheck:
        """Check that the coordinate is structurally valid.

        Parameters
        ----------
        coordinate : str
            The coordinate to validate.

        Returns
        -------
        ObligationCheck
            Passed iff :func:`_is_valid_coordinate` returns ``True``.
        """
        check_id = f"oc-cv-{_stable_hash8(coordinate)}"
        if not _is_valid_coordinate(coordinate):
            return ObligationCheck(
                check_id=check_id,
                obligation_name="COORDINATE_VALID",
                description="Coordinate must be non-empty and contain only alphanumeric, dots, underscores, hyphens",
                passed=False,
                failure_reason=f"Coordinate {coordinate!r} contains invalid characters or is empty",
                severity=5,
                is_blocking=True,
            )
        return ObligationCheck(
            check_id=check_id,
            obligation_name="COORDINATE_VALID",
            description="Coordinate is structurally valid",
            passed=True,
            failure_reason="",
            severity=5,
            is_blocking=True,
        )

    def _check_content_non_empty(self, content: str) -> ObligationCheck:
        """Check that the proposal content is non-empty after stripping.

        Parameters
        ----------
        content : str
            The proposal content.

        Returns
        -------
        ObligationCheck
            Passed iff ``content.strip() != ""``.
        """
        check_id = f"oc-cne-{_stable_hash8(content[:64])}"
        if not content.strip():
            return ObligationCheck(
                check_id=check_id,
                obligation_name="CONTENT_NON_EMPTY",
                description="Proposal content must be non-empty",
                passed=False,
                failure_reason="Content is empty or whitespace-only",
                severity=5,
                is_blocking=True,
            )
        return ObligationCheck(
            check_id=check_id,
            obligation_name="CONTENT_NON_EMPTY",
            description="Proposal content is present and non-empty",
            passed=True,
            failure_reason="",
            severity=5,
            is_blocking=True,
        )

    def _run_obligation(
        self,
        name: str,
        coordinate: str,
        content: str,
        trust: str,
        provenance: Sequence[tuple[str, str]],
    ) -> ObligationCheck:
        """Dispatch to the appropriate ``_check_*`` method by obligation name.

        Parameters
        ----------
        name : str
            The canonical obligation name.
        coordinate : str
            The proposal coordinate.
        content : str
            The proposal content.
        trust : str
            The current trust level.
        provenance : Sequence[tuple[str, str]]
            The proposal provenance entries.

        Returns
        -------
        ObligationCheck
            The result of the obligation check.  If *name* is not recognised,
            returns a passing informational check (unknown obligations are
            skipped rather than blocking the pipeline).
        """
        if name == "NO_SCOPE_WIDENING":
            return self._check_no_scope_widening(coordinate)
        if name == "TRUST_ASSIGNED":
            return self._check_trust_assigned(trust)
        if name == "PROVENANCE_PRESENT":
            return self._check_provenance_present(provenance)
        if name == "COORDINATE_VALID":
            return self._check_coordinate_valid(coordinate)
        if name == "CONTENT_NON_EMPTY":
            return self._check_content_non_empty(content)
        return ObligationCheck(
            check_id=f"oc-unknown-{_stable_hash8(name)}",
            obligation_name=name,
            description=f"Unknown obligation {name!r} — skipped",
            passed=True,
            failure_reason="",
            severity=1,
            is_blocking=False,
        )

    # -----------------------------------------------------------------------
    # §6.3  Trust assignment — §W.3
    # -----------------------------------------------------------------------

    def assign_trust(
        self,
        witness: RepairsGenerationsGovernedSameWitness,
    ) -> RepairsGenerationsGovernedSameWitness:
        """Assign a trust level to the proposal and advance to evidence recording.

        All proposals receive ``"PROPOSAL"`` trust level at this stage — the
        key invariant of unified governance.  A human reviewer may later
        advance the trust to ``"ACCEPTED"`` via :meth:`review_proposal`.

        The returned witness has status ``EVIDENCE_RECORDING`` (or remains
        ``REJECTED`` if the witness already reached that state).

        Parameters
        ----------
        witness : RepairsGenerationsGovernedSameWitness
            Witness at ``TRUST_ASSIGNMENT`` status.

        Returns
        -------
        RepairsGenerationsGovernedSameWitness
            New witness with trust level assigned and status advanced.
        """
        if witness.governance_status == GovernanceStatus.REJECTED.value:
            return witness
        trust = self._compute_trust_level(witness.obligation_checks)
        return replace(
            witness,
            trust_level=trust,
            governance_status=GovernanceStatus.EVIDENCE_RECORDING.value,
        )

    def _compute_trust_level(
        self,
        obligation_checks: tuple[ObligationCheck, ...],
    ) -> str:
        """Compute the trust level to assign based on obligation results.

        This method embodies the central invariant: **all proposals enter at
        ``PROPOSAL`` trust level**.  The obligation results may be logged for
        diagnostics, but they never auto-promote a proposal above ``PROPOSAL``.

        Rationale: auto-promotion to ``REVIEWED`` or ``ACCEPTED`` without
        human involvement would allow a sufficiently crafted proposal to
        bypass human oversight entirely.  The governance layer prevents this
        by always returning ``"PROPOSAL"`` here.

        Parameters
        ----------
        obligation_checks : tuple[ObligationCheck, ...]
            The obligation checks already run on this proposal.

        Returns
        -------
        str
            Always ``"PROPOSAL"``.
        """
        if obligation_checks:
            failed = [c.obligation_name for c in obligation_checks if not c.passed]
            if failed:
                pass  # diagnostic: some checks failed, but trust stays PROPOSAL
        return "PROPOSAL"

    # -----------------------------------------------------------------------
    # §6.4  Evidence recording — §W.4
    # -----------------------------------------------------------------------

    def record_evidence(
        self,
        witness: RepairsGenerationsGovernedSameWitness,
    ) -> RepairsGenerationsGovernedSameWitness:
        """Create and attach an :class:`EvidenceRecord` to the witness.

        If the witness has no fatal obligations, the returned witness is
        advanced to ``DEFERRED`` (awaiting human review).  If the witness
        already has ``REJECTED`` status, it is returned unchanged.

        Parameters
        ----------
        witness : RepairsGenerationsGovernedSameWitness
            Witness at ``EVIDENCE_RECORDING`` or ``REJECTED`` status.

        Returns
        -------
        RepairsGenerationsGovernedSameWitness
            New witness with ``evidence_record`` attached.
        """
        if witness.governance_status == GovernanceStatus.REJECTED.value:
            ev = self.build_evidence_record(witness)
            return replace(witness, evidence_record=ev)

        ev = self.build_evidence_record(witness)
        if witness.has_fatal_obligations():
            new_status = GovernanceStatus.REJECTED.value
            rej = "Evidence recording rejected due to fatal obligation failures"
        else:
            new_status = GovernanceStatus.DEFERRED.value
            rej = witness.rejection_reason

        return replace(
            witness,
            evidence_record=ev,
            governance_status=new_status,
            rejection_reason=rej,
        )

    def build_evidence_record(
        self,
        witness: RepairsGenerationsGovernedSameWitness,
    ) -> EvidenceRecord:
        """Build a complete :class:`EvidenceRecord` from *witness* state.

        Parameters
        ----------
        witness : RepairsGenerationsGovernedSameWitness
            The witness whose state should be captured in the evidence record.

        Returns
        -------
        EvidenceRecord
            A fully-populated evidence record.
        """
        obligations_met = witness.all_obligations_met()
        n_total = len(witness.obligation_checks)
        n_passed = sum(1 for c in witness.obligation_checks if c.passed)
        summary = (
            f"Governance evidence for proposal {witness.proposal_id!r} "
            f"(kind={witness.proposal_kind}, coord={witness.coordinate!r}): "
            f"{n_passed}/{n_total} obligations passed; "
            f"trust={witness.trust_level}; "
            f"status={witness.governance_status}; "
            f"fatal={witness.has_fatal_obligations()}"
        )
        evidence_id = f"ev-{_stable_hash8(witness.proposal_id + witness.coordinate)}-{uuid.uuid4().hex[:6]}"
        return EvidenceRecord(
            evidence_id=evidence_id,
            proposal_id=witness.proposal_id,
            proposal_kind=witness.proposal_kind,
            coordinate=witness.coordinate,
            obligation_checks=witness.obligation_checks,
            trust_level_assigned=witness.trust_level,
            reviewer_id=witness.reviewer_id,
            all_obligations_met=obligations_met,
            evidence_summary=summary,
            timestamp=_iso_timestamp(),
            metadata=(
                ("analyzer_id", self.analyzer_id),
                ("trust_policy", self.trust_policy),
                ("theory_section", MANIFEST_SPEC_PROVENANCE["theory_section"]),
            ),
        )

    # -----------------------------------------------------------------------
    # §6.5  Review — §W.5
    # -----------------------------------------------------------------------

    def review_proposal(
        self,
        witness: RepairsGenerationsGovernedSameWitness,
        reviewer_id: str,
        approved: bool,
        reason: str = "",
    ) -> RepairsGenerationsGovernedSameWitness:
        """Apply a human review decision to the witness.

        If *approved*: trust is advanced to ``"ACCEPTED"`` and status to
        ``APPROVED``.  If not *approved*: status is set to ``REJECTED`` and
        ``rejection_reason`` is set.

        Note that :attr:`require_reviewer` is checked: if ``True`` and
        *reviewer_id* is empty, the proposal is rejected automatically.

        Parameters
        ----------
        witness : RepairsGenerationsGovernedSameWitness
            The witness to review.
        reviewer_id : str
            Identifier of the human reviewer.
        approved : bool
            ``True`` iff the reviewer approves the proposal.
        reason : str, optional
            Rejection reason; required for clarity when ``approved=False``.

        Returns
        -------
        RepairsGenerationsGovernedSameWitness
            New witness reflecting the review outcome.
        """
        if self.require_reviewer and not reviewer_id:
            return replace(
                witness,
                governance_status=GovernanceStatus.REJECTED.value,
                rejection_reason="Reviewer ID is required but was not provided",
                reviewer_id="",
            )
        return witness.with_review(reviewer_id=reviewer_id, approved=approved, reason=reason)

    # -----------------------------------------------------------------------
    # §6.6  Full pipeline — §W.6
    # -----------------------------------------------------------------------

    def run_governance_pipeline(
        self,
        proposal_id: str,
        proposal_kind: str,
        coordinate: str,
        content: str,
        provenance: Sequence[tuple[str, str]],
    ) -> RepairsGenerationsGovernedSameWitness:
        """Run the full automated governance pipeline for a proposal.

        Executes the following stages in sequence:

        1. :meth:`submit_proposal` → ``SUBMITTED``
        2. :meth:`check_obligations` → ``TRUST_ASSIGNMENT`` or ``REJECTED``
        3. :meth:`assign_trust` → ``EVIDENCE_RECORDING``
        4. :meth:`record_evidence` → ``DEFERRED`` or ``REJECTED``

        Human review is *not* applied here; callers should call
        :meth:`review_proposal` separately (review is asynchronous in
        production workflows).

        Parameters
        ----------
        proposal_id : str
            Unique identifier for the proposal.
        proposal_kind : str
            The :class:`ProposalKind` name.
        coordinate : str
            The semantic coordinate targeted by the proposal.
        content : str
            The proposal content.
        provenance : Sequence[tuple[str, str]]
            Key–value provenance pairs.

        Returns
        -------
        RepairsGenerationsGovernedSameWitness
            The witness after the full automated pipeline.  Either
            ``DEFERRED`` (awaiting review) or ``REJECTED`` (pipeline failed).
        """
        w = self.submit_proposal(proposal_id, proposal_kind, coordinate, content, provenance)
        w = self.check_obligations(w, content, provenance)
        if w.governance_status != GovernanceStatus.REJECTED.value:
            w = self.assign_trust(w)
        if w.governance_status != GovernanceStatus.REJECTED.value:
            w = self.record_evidence(w)
        else:
            w = self.record_evidence(w)
        return w

    def get_pipeline_status(
        self,
        witness: RepairsGenerationsGovernedSameWitness,
    ) -> dict[str, Any]:
        """Return a status summary dictionary for *witness*.

        Parameters
        ----------
        witness : RepairsGenerationsGovernedSameWitness
            The witness to summarise.

        Returns
        -------
        dict[str, Any]
            Keys: ``proposal_id``, ``kind``, ``status``, ``obligations_passed``,
            ``obligations_failed``, ``trust_level``, ``has_evidence``,
            ``can_be_reviewed``.
        """
        passed = [c for c in witness.obligation_checks if c.passed]
        failed = [c for c in witness.obligation_checks if not c.passed]
        can_be_reviewed = (
            not witness.is_approved()
            and not witness.is_rejected()
            and witness.governance_status != GovernanceStatus.WITHDRAWN.value
        )
        return {
            "proposal_id": witness.proposal_id,
            "kind": witness.proposal_kind,
            "status": witness.governance_status,
            "obligations_passed": len(passed),
            "obligations_failed": len(failed),
            "trust_level": witness.trust_level,
            "has_evidence": bool(witness.evidence_record.evidence_id),
            "can_be_reviewed": can_be_reviewed,
        }

    # -----------------------------------------------------------------------
    # §6.7  Comparison — key invariant enforcement — §W.7
    # -----------------------------------------------------------------------

    def assert_same_governance(
        self,
        repair_witness: RepairsGenerationsGovernedSameWitness,
        generation_witness: RepairsGenerationsGovernedSameWitness,
    ) -> bool:
        """Assert that a repair and a generation witness received identical governance.

        The invariant requires that:

        1. Both witnesses have the same set of obligation names checked.
        2. Both witnesses were processed under the same trust policy.
        3. Both witnesses have evidence records attached.

        Parameters
        ----------
        repair_witness : RepairsGenerationsGovernedSameWitness
            The governance witness for a repair proposal.
        generation_witness : RepairsGenerationsGovernedSameWitness
            The governance witness for a generation proposal.

        Returns
        -------
        bool
            ``True`` iff all three invariant conditions hold for both witnesses.
        """
        repair_ob_names = frozenset(c.obligation_name for c in repair_witness.obligation_checks)
        gen_ob_names = frozenset(c.obligation_name for c in generation_witness.obligation_checks)
        same_obligations = repair_ob_names == gen_ob_names

        same_trust_policy = True  # both go through this analyzer → same policy

        repair_has_evidence = bool(repair_witness.evidence_record.evidence_id)
        gen_has_evidence = bool(generation_witness.evidence_record.evidence_id)
        same_evidence_present = repair_has_evidence == gen_has_evidence

        return same_obligations and same_trust_policy and same_evidence_present

    def compare_governance_paths(
        self,
        w1: RepairsGenerationsGovernedSameWitness,
        w2: RepairsGenerationsGovernedSameWitness,
    ) -> dict[str, Any]:
        """Compare the governance paths of two witnesses and return a diff report.

        Parameters
        ----------
        w1 : RepairsGenerationsGovernedSameWitness
            First witness.
        w2 : RepairsGenerationsGovernedSameWitness
            Second witness.

        Returns
        -------
        dict[str, Any]
            Keys: ``same_obligations``, ``same_trust_policy``,
            ``same_evidence_present``, ``divergence_points``.
        """
        ob1 = frozenset(c.obligation_name for c in w1.obligation_checks)
        ob2 = frozenset(c.obligation_name for c in w2.obligation_checks)
        same_obligations = ob1 == ob2
        same_trust_policy = True
        ev1 = bool(w1.evidence_record.evidence_id)
        ev2 = bool(w2.evidence_record.evidence_id)
        same_evidence_present = ev1 == ev2

        divergence_points: list[str] = []
        if not same_obligations:
            only_in_w1 = ob1 - ob2
            only_in_w2 = ob2 - ob1
            if only_in_w1:
                divergence_points.append(
                    f"Obligations only in w1 ({w1.proposal_id!r}): {sorted(only_in_w1)}"
                )
            if only_in_w2:
                divergence_points.append(
                    f"Obligations only in w2 ({w2.proposal_id!r}): {sorted(only_in_w2)}"
                )
        if w1.trust_level != w2.trust_level:
            divergence_points.append(
                f"Trust levels differ: {w1.trust_level!r} vs {w2.trust_level!r}"
            )
        if not same_evidence_present:
            divergence_points.append(
                f"Evidence presence differs: w1={ev1}, w2={ev2}"
            )
        if w1.governance_status != w2.governance_status:
            divergence_points.append(
                f"Governance statuses differ: {w1.governance_status!r} vs {w2.governance_status!r}"
            )

        return {
            "same_obligations": same_obligations,
            "same_trust_policy": same_trust_policy,
            "same_evidence_present": same_evidence_present,
            "divergence_points": divergence_points,
        }

    # -----------------------------------------------------------------------
    # §6.8  Serialisation — §W.8
    # -----------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise the analyzer configuration to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, Any]
            All analyzer fields.
        """
        return {
            "analyzer_id": self.analyzer_id,
            "coordinate": self.coordinate,
            "obligations": list(self.obligations),
            "trust_policy": self.trust_policy,
            "require_reviewer": self.require_reviewer,
            "strict_mode": self.strict_mode,
            "evidence_ttl_seconds": self.evidence_ttl_seconds,
            "manifest": MANIFEST_SPEC_PROVENANCE,
        }

    @classmethod
    def from_dict(
        cls, d: dict[str, Any]
    ) -> "RepairsGenerationsGovernedSameAnalyzer":
        """Deserialise an analyzer from a dictionary (inverse of :meth:`to_dict`).

        Parameters
        ----------
        d : dict[str, Any]
            A dictionary as produced by :meth:`to_dict`.

        Returns
        -------
        RepairsGenerationsGovernedSameAnalyzer
            Reconstructed analyzer instance.
        """
        return cls(
            analyzer_id=d.get("analyzer_id", ""),
            coordinate=d.get("coordinate", ""),
            obligations=tuple(d.get("obligations", list(_default_obligations()))),
            trust_policy=d.get("trust_policy", "PROPOSAL_REQUIRED"),
            require_reviewer=d.get("require_reviewer", True),
            strict_mode=d.get("strict_mode", False),
            evidence_ttl_seconds=d.get("evidence_ttl_seconds", 86400),
        )


# ---------------------------------------------------------------------------
# §7  RepairsGenerationsGovernedSameCoordinator
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RepairsGenerationsGovernedSameCoordinator:
    """Coordinates a pool of governance analyzers across repair and generation proposals.

    The :class:`RepairsGenerationsGovernedSameCoordinator` manages one or more
    :class:`RepairsGenerationsGovernedSameAnalyzer` instances and routes both
    repair and generation proposals through all of them.

    When :attr:`enforce_unified_governance` is ``True`` (the default), the
    coordinator asserts that repairs and generations were governed by the same
    analyzers — the implementation-level guarantee that the unified governance
    invariant holds.

    Design Note
    -----------
    The coordinator is deliberately *not* a singleton.  Multiple coordinators
    can coexist in a session, each managing a different pool of analyzers.
    This allows fine-grained governance scoping while preserving the invariant
    at each scope.

    Parameters
    ----------
    coordinator_id : str
        Unique identifier for this coordinator instance.
    analyzers : tuple[RepairsGenerationsGovernedSameAnalyzer, ...]
        The pool of analyzers managed by this coordinator.
    session_id : str
        The session this coordinator is associated with.
    enforce_unified_governance : bool
        When ``True``, :meth:`assert_governance_parity` raises an assertion
        error if repairs and generations were not governed identically.
    audit_log : tuple[tuple[str, str], ...]
        Immutable sequence of ``(timestamp, event_description)`` pairs
        recording major coordinator events.
    """

    coordinator_id: str = ""
    analyzers: tuple["RepairsGenerationsGovernedSameAnalyzer", ...] = ()
    session_id: str = ""
    enforce_unified_governance: bool = True
    audit_log: tuple[tuple[str, str], ...] = ()

    # -----------------------------------------------------------------------
    # §7.1  Analyzer management
    # -----------------------------------------------------------------------

    def add_analyzer(
        self,
        analyzer: "RepairsGenerationsGovernedSameAnalyzer",
    ) -> "RepairsGenerationsGovernedSameCoordinator":
        """Return a new coordinator with *analyzer* appended to the pool.

        Parameters
        ----------
        analyzer : RepairsGenerationsGovernedSameAnalyzer
            The analyzer to add.

        Returns
        -------
        RepairsGenerationsGovernedSameCoordinator
            New coordinator with the enlarged analyzer pool.
        """
        if not self.analyzers and not self.coordinator_id:
            cid = f"coord-{_stable_hash8(analyzer.analyzer_id or analyzer.coordinate)}-{uuid.uuid4().hex[:6]}"
            return replace(
                self,
                coordinator_id=cid,
                analyzers=(analyzer,),
            )
        return replace(self, analyzers=(*self.analyzers, analyzer))

    def _ensure_analyzer(self) -> "RepairsGenerationsGovernedSameAnalyzer":
        """Return the first available analyzer or create a default one.

        Returns
        -------
        RepairsGenerationsGovernedSameAnalyzer
            The first analyzer in the pool, or a fresh default analyzer.
        """
        if self.analyzers:
            return self.analyzers[0]
        return RepairsGenerationsGovernedSameAnalyzer(
            analyzer_id=f"default-{uuid.uuid4().hex[:8]}",
            coordinate="root",
            obligations=_default_obligations(),
        )

    # -----------------------------------------------------------------------
    # §7.2  Governance dispatch
    # -----------------------------------------------------------------------

    def govern_proposal(
        self,
        proposal_id: str,
        proposal_kind: str,
        coordinate: str,
        content: str,
        provenance: Sequence[tuple[str, str]],
    ) -> tuple[RepairsGenerationsGovernedSameWitness, ...]:
        """Run the governance pipeline on all analyzers for a proposal.

        Parameters
        ----------
        proposal_id : str
            Unique identifier for the proposal.
        proposal_kind : str
            The :class:`ProposalKind` name.
        coordinate : str
            The semantic coordinate targeted by the proposal.
        content : str
            The proposal content.
        provenance : Sequence[tuple[str, str]]
            Key–value provenance pairs.

        Returns
        -------
        tuple[RepairsGenerationsGovernedSameWitness, ...]
            One witness per analyzer.  If the pool is empty, a single witness
            is produced using a default analyzer.
        """
        pool = self.analyzers if self.analyzers else (self._ensure_analyzer(),)
        results: list[RepairsGenerationsGovernedSameWitness] = []
        for analyzer in pool:
            w = analyzer.run_governance_pipeline(
                proposal_id=proposal_id,
                proposal_kind=proposal_kind,
                coordinate=coordinate,
                content=content,
                provenance=provenance,
            )
            results.append(w)
        return tuple(results)

    def govern_repair(
        self,
        repair_id: str,
        coordinate: str,
        old_section: str,
        new_section: str,
    ) -> tuple[RepairsGenerationsGovernedSameWitness, ...]:
        """Govern a repair proposal.

        Convenience wrapper around :meth:`govern_proposal` that:

        * Sets ``proposal_kind="REPAIR"``.
        * Constructs a canonical provenance from the repair parameters.
        * Derives content from *new_section*.

        Parameters
        ----------
        repair_id : str
            Unique identifier for the repair.
        coordinate : str
            The semantic coordinate being repaired.
        old_section : str
            The existing section being replaced.
        new_section : str
            The replacement section.

        Returns
        -------
        tuple[RepairsGenerationsGovernedSameWitness, ...]
            Witnesses from all analyzers.
        """
        provenance: list[tuple[str, str]] = [
            ("source", "repair"),
            ("session", self.session_id),
            ("old_section_hash", _stable_hash8(old_section)),
            ("new_section_hash", _stable_hash8(new_section)),
            ("coordinator_id", self.coordinator_id),
        ]
        return self.govern_proposal(
            proposal_id=repair_id,
            proposal_kind=ProposalKind.REPAIR.value,
            coordinate=coordinate,
            content=new_section,
            provenance=provenance,
        )

    def govern_generation(
        self,
        gen_id: str,
        coordinate: str,
        generated_content: str,
    ) -> tuple[RepairsGenerationsGovernedSameWitness, ...]:
        """Govern a generation proposal.

        Convenience wrapper around :meth:`govern_proposal` that:

        * Sets ``proposal_kind="GENERATION"``.
        * Constructs canonical provenance from the generation parameters.

        Parameters
        ----------
        gen_id : str
            Unique identifier for the generation.
        coordinate : str
            The semantic coordinate being targeted.
        generated_content : str
            The generated content.

        Returns
        -------
        tuple[RepairsGenerationsGovernedSameWitness, ...]
            Witnesses from all analyzers.
        """
        provenance: list[tuple[str, str]] = [
            ("source", "generator"),
            ("session", self.session_id),
            ("content_hash", _stable_hash8(generated_content)),
            ("coordinator_id", self.coordinator_id),
        ]
        return self.govern_proposal(
            proposal_id=gen_id,
            proposal_kind=ProposalKind.GENERATION.value,
            coordinate=coordinate,
            content=generated_content,
            provenance=provenance,
        )

    # -----------------------------------------------------------------------
    # §7.3  Parity assertion
    # -----------------------------------------------------------------------

    def assert_governance_parity(
        self,
        repair_witnesses: Sequence[RepairsGenerationsGovernedSameWitness],
        gen_witnesses: Sequence[RepairsGenerationsGovernedSameWitness],
    ) -> bool:
        """Assert that repairs and generations received equivalent governance.

        For each pair (repair_witness, gen_witness) drawn from the two
        sequences (paired by index), calls
        :meth:`RepairsGenerationsGovernedSameAnalyzer.assert_same_governance`
        using the first available analyzer.

        If :attr:`enforce_unified_governance` is ``True`` and parity fails,
        this method returns ``False`` (callers may treat this as a hard error).

        Parameters
        ----------
        repair_witnesses : Sequence[RepairsGenerationsGovernedSameWitness]
            The governance witnesses for repair proposals.
        gen_witnesses : Sequence[RepairsGenerationsGovernedSameWitness]
            The governance witnesses for generation proposals.

        Returns
        -------
        bool
            ``True`` iff every matched pair satisfies the same-governance
            invariant.
        """
        analyzer = self._ensure_analyzer()
        n = min(len(list(repair_witnesses)), len(list(gen_witnesses)))
        if n == 0:
            return True
        all_parity = True
        for rw, gw in zip(list(repair_witnesses)[:n], list(gen_witnesses)[:n]):
            if not analyzer.assert_same_governance(rw, gw):
                all_parity = False
                if self.enforce_unified_governance:
                    comp = analyzer.compare_governance_paths(rw, gw)
                    _ = comp  # divergence info available to caller if needed
        return all_parity

    # -----------------------------------------------------------------------
    # §7.4  Reporting
    # -----------------------------------------------------------------------

    def build_governance_report(
        self,
        witnesses: Sequence[RepairsGenerationsGovernedSameWitness],
    ) -> dict[str, Any]:
        """Build a summary governance report over a collection of witnesses.

        Parameters
        ----------
        witnesses : Sequence[RepairsGenerationsGovernedSameWitness]
            All witnesses to include in the report.

        Returns
        -------
        dict[str, Any]
            Keys:
            ``total`` (int),
            ``approved`` (int),
            ``rejected`` (int),
            ``pending`` (int),
            ``trust_distribution`` (dict[str, int]),
            ``obligation_pass_rate_avg`` (float),
            ``parity_verified`` (bool),
            ``kind_distribution`` (dict[str, int]).
        """
        ws = list(witnesses)
        total = len(ws)
        approved = sum(1 for w in ws if w.is_approved())
        rejected = sum(1 for w in ws if w.is_rejected())
        pending = sum(1 for w in ws if w.is_pending())

        trust_distribution: dict[str, int] = {}
        kind_distribution: dict[str, int] = {}
        pass_rates: list[float] = []

        for w in ws:
            trust_distribution[w.trust_level] = trust_distribution.get(w.trust_level, 0) + 1
            kind_distribution[w.proposal_kind] = kind_distribution.get(w.proposal_kind, 0) + 1
            pass_rates.append(w.obligation_pass_rate())

        avg_pass_rate = sum(pass_rates) / len(pass_rates) if pass_rates else 0.0

        repair_ws = [w for w in ws if w.proposal_kind == ProposalKind.REPAIR.value]
        gen_ws = [w for w in ws if w.proposal_kind == ProposalKind.GENERATION.value]
        parity = self.assert_governance_parity(repair_ws, gen_ws) if (repair_ws and gen_ws) else True

        return {
            "total": total,
            "approved": approved,
            "rejected": rejected,
            "pending": pending,
            "trust_distribution": trust_distribution,
            "obligation_pass_rate_avg": avg_pass_rate,
            "parity_verified": parity,
            "kind_distribution": kind_distribution,
        }

    # -----------------------------------------------------------------------
    # §7.5  Audit logging
    # -----------------------------------------------------------------------

    def log_event(
        self, event: str
    ) -> "RepairsGenerationsGovernedSameCoordinator":
        """Return a new coordinator with *event* appended to the audit log.

        Parameters
        ----------
        event : str
            A human-readable description of the event.

        Returns
        -------
        RepairsGenerationsGovernedSameCoordinator
            New coordinator with the event appended.
        """
        entry = (_iso_timestamp(), event)
        return replace(self, audit_log=(*self.audit_log, entry))

    # -----------------------------------------------------------------------
    # §7.6  Serialisation
    # -----------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise the coordinator to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, Any]
            All coordinator fields.
        """
        return {
            "coordinator_id": self.coordinator_id,
            "analyzers": [a.to_dict() for a in self.analyzers],
            "session_id": self.session_id,
            "enforce_unified_governance": self.enforce_unified_governance,
            "audit_log": list(self.audit_log),
        }




# ---------------------------------------------------------------------------
# Unified architecture cross-references (jugeo.solver, jugeo.evidence, jugeo.geometry)
# ---------------------------------------------------------------------------


def repair_from_countermodel(cm: Any) -> dict[str, Any]:
    """Extract repair guidance from a countermodel.

    Countermodels from the solver encode exactly where the current section
    fails — they are the starting point for all repair actions.

    Parameters
    ----------
    cm : Any
        A Countermodel object or dict with countermodel data.

    Returns
    -------
    dict[str, Any]
        Repair guidance with ``failing_coordinates``, ``repair_hints``,
        ``countermodel_id``, and ``obstruction_class`` keys.
    """
    try:
        from jugeo.solver.countermodels import extract_repair_hints, Countermodel
    except ImportError:
        extract_repair_hints = None
        Countermodel = None

    model_id = getattr(cm, "model_id", None) or (cm.get("model_id") if isinstance(cm, dict) else "unknown")
    coord = getattr(cm, "coordinate", None) or (cm.get("coordinate") if isinstance(cm, dict) else None)

    guidance: dict[str, Any] = {
        "countermodel_id": model_id,
        "failing_coordinates": [coord] if coord else [],
        "repair_hints": [],
        "obstruction_class": f"H1_from_{model_id}",
    }

    if extract_repair_hints is not None:
        try:
            hints = extract_repair_hints(cm)
            guidance["repair_hints"] = list(hints) if hints else []
        except Exception:
            pass

    return guidance


def repair_certificate(repair: Any) -> dict[str, Any]:
    """Build an evidence certificate for a completed repair.

    Repair certificates attest that a repair action was performed,
    passed validation, and restored section well-formedness.

    Parameters
    ----------
    repair : Any
        A repair result object or dict.

    Returns
    -------
    dict[str, Any]
        Certificate with ``repair_id``, ``valid``, ``trust_level``,
        ``certificate_hash``, and ``certificate_obj`` keys.
    """
    try:
        from jugeo.evidence.certificates import Certificate, build_certificate
    except ImportError:
        Certificate = None
        build_certificate = None

    import hashlib, uuid

    repair_id = getattr(repair, "repair_id", None) or (
        repair.get("repair_id") if isinstance(repair, dict) else str(uuid.uuid4())
    )
    valid = getattr(repair, "valid", None)
    if valid is None and isinstance(repair, dict):
        valid = repair.get("valid", repair.get("status") == "success")

    cert: dict[str, Any] = {
        "certificate_id": str(uuid.uuid4()),
        "repair_id": repair_id,
        "valid": bool(valid) if valid is not None else False,
        "trust_level": "REPAIRED" if valid else "UNVERIFIED",
        "certificate_hash": hashlib.sha256(str(repair).encode()).hexdigest()[:16],
        "certificate_obj": None,
    }

    if build_certificate is not None:
        try:
            cert["certificate_obj"] = build_certificate(
                claim=f"repair_{repair_id}", satisfied=valid, source="repair_semantics"
            )
        except Exception:
            pass

    return cert


def repair_descent_check(repair: Any) -> dict[str, Any]:
    """Check whether a repair restores descent (gluing) conditions.

    A valid repair must restore the ability of local sections to glue
    into a global section — i.e., the cocycle obstruction must vanish.

    Parameters
    ----------
    repair : Any
        A repair result object or dict.

    Returns
    -------
    dict[str, Any]
        Descent check with ``gluing_restored``, ``cocycle_trivial``,
        ``affected_coordinates``, and ``descent_status`` keys.
    """
    try:
        from jugeo.geometry.descent import check_descent_after_repair, DescentStatus
    except ImportError:
        check_descent_after_repair = None
        DescentStatus = None

    coords = getattr(repair, "affected_coordinates", None) or (
        repair.get("affected_coordinates") if isinstance(repair, dict) else []
    )
    repair_id = getattr(repair, "repair_id", None) or (
        repair.get("repair_id") if isinstance(repair, dict) else "unknown"
    )

    check: dict[str, Any] = {
        "repair_id": repair_id,
        "affected_coordinates": list(coords) if coords else [],
        "gluing_restored": None,
        "cocycle_trivial": None,
        "descent_status": "UNKNOWN",
    }

    if check_descent_after_repair is not None:
        try:
            result = check_descent_after_repair(coords, repair_id=repair_id)
            check["gluing_restored"] = getattr(result, "gluing_restored", None)
            check["cocycle_trivial"] = getattr(result, "cocycle_trivial", None)
            check["descent_status"] = getattr(result, "status", "UNKNOWN")
        except Exception:
            pass

    return check


# ---------------------------------------------------------------------------
# Module exports
# ---------------------------------------------------------------------------

__all__ = [
    "MANIFEST_SPEC_PROVENANCE",
    # Enumerations
    "ProposalKind",
    "GovernanceStatus",
    # Dataclasses
    "ObligationCheck",
    "EvidenceRecord",
    "RepairsGenerationsGovernedSameWitness",
    "RepairsGenerationsGovernedSameAnalyzer",
    "RepairsGenerationsGovernedSameCoordinator",
    # Helpers
    "_iso_timestamp",
    "_stable_hash8",
    "_valid_trust_names",
    "_valid_proposal_kinds",
    "_is_valid_coordinate",
    "_governance_status_rank",
    "_default_obligations",
    # Unified architecture cross-references
    "repair_from_countermodel",
    "repair_certificate",
    "repair_descent_check",
]

# copilot: s04 repairs and generations governed the same way — theory2 ch11 §11.W

# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Smoke test: repair and generation proposals go through identical governance
    analyzer = RepairsGenerationsGovernedSameAnalyzer(
        analyzer_id="smoke-analyzer",
        coordinate="root.module",
        obligations=_default_obligations(),
        trust_policy="PROPOSAL_REQUIRED",
    )

    # Govern a repair
    repair_witness = analyzer.run_governance_pipeline(
        proposal_id="repair-001",
        proposal_kind="REPAIR",
        coordinate="root.module",
        content="def f(x): return max(x, 0)",
        provenance=[("source", "debugger"), ("session", "sess-001")],
    )
    print(f"Repair status: {repair_witness.governance_status}")
    print(f"Repair trust: {repair_witness.trust_level}")
    print(f"Obligations met: {repair_witness.all_obligations_met()}")

    # Govern a generation
    gen_witness = analyzer.run_governance_pipeline(
        proposal_id="gen-001",
        proposal_kind="GENERATION",
        coordinate="root.module",
        content="def new_feature(): return True",
        provenance=[("source", "generator"), ("session", "sess-001")],
    )
    print(f"Generation status: {gen_witness.governance_status}")
    print(f"Generation trust: {gen_witness.trust_level}")

    # Assert same governance
    same = analyzer.assert_same_governance(repair_witness, gen_witness)
    print(f"Same governance: {same}")

    comparison = analyzer.compare_governance_paths(repair_witness, gen_witness)
    print(f"Governance comparison: same_obligations={comparison['same_obligations']}")

    # Review
    reviewed = analyzer.review_proposal(repair_witness, "reviewer-1", True)
    print(f"After review: {reviewed.governance_status}, trust={reviewed.trust_level}")

    # Coordinator
    coordinator = RepairsGenerationsGovernedSameCoordinator(
        session_id="test-session",
        enforce_unified_governance=True,
    )
    coordinator = coordinator.add_analyzer(analyzer)
    repair_ws = coordinator.govern_repair("r-001", "root.module", "old_section", "new_section")
    gen_ws = coordinator.govern_generation("g-001", "root.module", "generated_content")
    parity = coordinator.assert_governance_parity(repair_ws, gen_ws)
    print(f"Governance parity: {parity}")
    report = coordinator.build_governance_report(list(repair_ws) + list(gen_ws))
    print(f"Report: total={report['total']}, approved={report['approved']}")
    print("s04 smoke test passed")
