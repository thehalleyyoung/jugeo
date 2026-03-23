"""Specifications as target geometry: clauses, truth conditions, and target sections.

Section 10.X: Specifications as Target Geometry.  A *specification* is a target
section σ: X → J where X is the project site (a category with a Grothendieck
topology) and J is the judgment sheaf whose stalks are judgment tuples of the
form (c, φ, A, E, O, B, T, Π).  This module develops the clause-based
presentation of target geometry: a specification is assembled from *clauses*,
which are local sections over open patches of X prescribing what the judgment
sheaf must look like on that patch, together with *truth conditions*, which
associate each clause with a satisfaction criterion and evidence requirements.
The resulting *target section* is a coherent global prescription — or, when
global coherence fails, a partial section together with a record of incoherence
violations.

Theory context
--------------
Judgments are tuples (c, φ, A, E, O, B, T, Π) where:
  - c    = coordinate (object of the site X)
  - φ    = formula / type being judged
  - A    = author (who issued the judgment)
  - E    = evidence (proof terms, test results, attestations)
  - O    = obligations (follow-on requirements)
  - B    = blocking conditions (preconditions for validity)
  - T    = trust tier (VERIFIED | TRUSTED | PROPOSED | UNVERIFIED)
  - Π    = provenance (lineage record)

Trust is NOT a float — it is a discrete tier from the ordered set
  VERIFIED > TRUSTED > PROPOSED > UNVERIFIED.

A specification clause is a prescription: over the patch U ⊆ X indexed by
coordinate c, the judgment sheaf section must assign a tuple satisfying the
clause's prescribed_judgment_fields and the associated truth conditions.

References theory2.tex §10.X.

# copilot: generated scaffold for jugeo specification_satisfaction specifications_as_target_geometry
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Sequence

# ---------------------------------------------------------------------------
# Optional internal imports — all guarded with ImportError fallbacks
# ---------------------------------------------------------------------------

try:
    from jugeo.problem_modes.specification_satisfaction.models import (
        Specification,
        SatisfactionWitness,
        SpecificationKind,
        WitnessStatus,
        SatisfactionStatus,
        ResidualGap,
    )
except ImportError:
    Specification = Any  # type: ignore[assignment,misc]
    SatisfactionWitness = Any  # type: ignore[assignment,misc]
    SpecificationKind = Any  # type: ignore[assignment,misc]
    WitnessStatus = Any  # type: ignore[assignment,misc]
    SatisfactionStatus = Any  # type: ignore[assignment,misc]
    ResidualGap = Any  # type: ignore[assignment,misc]

try:
    from jugeo.judgments.judgment_terms import JudgmentTerm, JudgmentKind, ProvenanceKind
except ImportError:
    JudgmentTerm = Any  # type: ignore[assignment,misc]
    JudgmentKind = Any  # type: ignore[assignment,misc]
    ProvenanceKind = Any  # type: ignore[assignment,misc]

try:
    from jugeo.geometry.site import CoordinateObject, SemanticSite
except ImportError:
    CoordinateObject = Any  # type: ignore[assignment,misc]
    SemanticSite = Any  # type: ignore[assignment,misc]

try:
    from jugeo.geometry.covers import Cover
except ImportError:
    Cover = Any  # type: ignore[assignment,misc]

try:
    from jugeo.evidence.certificates import Certificate, CertificateStatus
except ImportError:
    Certificate = Any  # type: ignore[assignment,misc]
    CertificateStatus = Any  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------

DEFAULT_TRUST_TIER: str = "UNVERIFIED"
VERIFIED_TRUST_TIER: str = "VERIFIED"
PROPOSAL_TRUST_TIER: str = "PROPOSAL"
JUDGMENT_COMPONENTS: tuple[str, ...] = ("c", "phi", "A", "E", "O", "B", "T", "Pi")
MAX_CLAUSES_PER_SECTION: int = 512
CLAUSE_PARSE_VERSION: str = "1.0.0"

_TRUST_TIER_ORDER: dict[str, int] = {
    "UNVERIFIED": 0,
    "PROPOSAL": 1,
    "PROPOSED": 1,
    "TRUSTED": 2,
    "VERIFIED": 3,
}

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string.

    Returns
    -------
    str
        ISO-8601 UTC timestamp, e.g. ``"2024-01-15T12:34:56.789012+00:00"``.
    """
    return datetime.now(tz=timezone.utc).isoformat()


def _stable_hash(payload: str) -> str:
    """Compute a stable SHA-256 hex digest of *payload*.

    Parameters
    ----------
    payload : str
        Arbitrary string content to hash.

    Returns
    -------
    str
        64-character lower-case hex digest.
    """
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _make_clause_id(coordinate: str, formula: str) -> str:
    """Construct a deterministic clause identifier from coordinate and formula.

    Parameters
    ----------
    coordinate : str
        The site coordinate over which the clause is defined.
    formula : str
        The formula or type prescription carried by the clause.

    Returns
    -------
    str
        A ``clause-<hex8>`` identifier that is stable for the same inputs.
    """
    raw = f"clause|{coordinate}|{formula}"
    return f"clause-{_stable_hash(raw)[:8]}"


def _make_section_id(spec_id: str, clauses: list[Any]) -> str:
    """Construct a deterministic section identifier.

    Parameters
    ----------
    spec_id : str
        Specification identifier that the section belongs to.
    clauses : list
        Ordered list of :class:`TargetSectionClause` instances contributing to
        the section.

    Returns
    -------
    str
        A ``section-<hex8>`` identifier stable over the same inputs.
    """
    clause_ids = sorted(getattr(c, "clause_id", str(c)) for c in clauses)
    raw = f"section|{spec_id}|{'_'.join(clause_ids)}"
    return f"section-{_stable_hash(raw)[:8]}"


def _make_condition_id(clause_id: str, criterion: str) -> str:
    """Construct a deterministic truth-condition identifier.

    Parameters
    ----------
    clause_id : str
        The clause to which this condition is attached.
    criterion : str
        The satisfaction criterion text.

    Returns
    -------
    str
        A ``cond-<hex8>`` identifier.
    """
    raw = f"cond|{clause_id}|{criterion}"
    return f"cond-{_stable_hash(raw)[:8]}"


def _canonical_coordinate(coord: str) -> str:
    """Normalise a coordinate string to a canonical form.

    Strips leading/trailing whitespace, collapses internal runs of whitespace
    to single spaces, and lower-cases the result so that coordinate comparisons
    are case-insensitive.

    Parameters
    ----------
    coord : str
        Raw coordinate string from user input or serialised data.

    Returns
    -------
    str
        Normalised coordinate string.

    Examples
    --------
    >>> _canonical_coordinate("  Req.Auth  ")
    'req.auth'
    >>> _canonical_coordinate("REQ . AUTH")
    'req . auth'
    """
    import re
    return re.sub(r"\s+", " ", coord.strip()).lower()


def _clause_overlap(c1: TargetSectionClause, c2: TargetSectionClause) -> bool:
    """Determine whether two clauses have overlapping coordinates.

    Two clauses *overlap* when their canonical coordinates are equal or when
    one is a prefix of the other (e.g. ``req.auth`` overlaps ``req.auth.login``
    because patches are hierarchically organised).

    Parameters
    ----------
    c1 : TargetSectionClause
        First clause.
    c2 : TargetSectionClause
        Second clause.

    Returns
    -------
    bool
        ``True`` if the clauses share a coordinate or one subsumes the other.
    """
    coord1 = _canonical_coordinate(c1.coordinate)
    coord2 = _canonical_coordinate(c2.coordinate)
    if coord1 == coord2:
        return True
    # Hierarchical prefix overlap
    return coord1.startswith(coord2 + ".") or coord2.startswith(coord1 + ".")


def _judgment_fields_compatible(j1: dict[str, Any], j2: dict[str, Any]) -> bool:
    """Check whether two judgment-field dictionaries are mutually compatible.

    Two dictionaries are compatible when, for every key present in both, the
    associated values are equal.  Keys absent from one side do not cause
    incompatibility — they represent underspecification rather than conflict.

    Parameters
    ----------
    j1 : dict
        First judgment-fields mapping (a subset of JUDGMENT_COMPONENTS).
    j2 : dict
        Second judgment-fields mapping.

    Returns
    -------
    bool
        ``True`` when no key has conflicting values across j1 and j2.
    """
    shared_keys = set(j1.keys()) & set(j2.keys())
    return all(j1[k] == j2[k] for k in shared_keys)


def _compute_coverage_fraction(covered: list[Any], total: list[Any]) -> float:
    """Compute the fraction of *total* elements that appear in *covered*.

    Parameters
    ----------
    covered : list
        The subset of elements that have been covered / satisfied.
    total : list
        The complete collection of elements under consideration.

    Returns
    -------
    float
        A value in ``[0.0, 1.0]``.  Returns ``1.0`` when *total* is empty
        (vacuous coverage).
    """
    if not total:
        return 1.0
    covered_set = set(str(x) for x in covered)
    total_set = set(str(x) for x in total)
    if not total_set:
        return 1.0
    return len(covered_set & total_set) / len(total_set)


def _format_truth_condition_summary(tc: TruthCondition) -> str:
    """Produce a human-readable one-line summary of a truth condition.

    Parameters
    ----------
    tc : TruthCondition
        The truth condition to summarise.

    Returns
    -------
    str
        A formatted string like
        ``"[cond-abc12345] clause=clause-... | criterion='...' | trust≥TRUSTED"``.
    """
    neg = " (negation allowed)" if tc.negation_allowed else ""
    evidence = ", ".join(tc.required_evidence_kinds) if tc.required_evidence_kinds else "none"
    return (
        f"[{tc.condition_id}] clause={tc.clause_id}"
        f" | criterion='{tc.satisfaction_criterion}'"
        f" | trust≥{tc.trust_threshold}"
        f" | evidence=[{evidence}]"
        f" | kind={tc.condition_kind}{neg}"
    )


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class ClauseKind(str, Enum):
    """Enumeration of clause categories within target geometry.

    Attributes
    ----------
    STRUCTURAL : str
        Clauses that prescribe the structural form of a judgment — its type,
        shape, or syntactic organisation.
    BEHAVIORAL : str
        Clauses that prescribe observable behaviour at runtime or at
        verification time (e.g. «the component must respond within 200ms»).
    SEMANTIC : str
        Clauses that prescribe meaning — semantic constraints on formula
        interpretations in the model.
    RELATIONAL : str
        Clauses that prescribe relations between multiple coordinates (e.g.
        dependency ordering, implication chains).
    RESOURCE : str
        Clauses that prescribe resource bounds or allocation constraints
        (memory, CPU, budget).
    COMPOSITE : str
        Clauses that are composed from other clauses via logical connectives.
    """

    STRUCTURAL = "STRUCTURAL"
    BEHAVIORAL = "BEHAVIORAL"
    SEMANTIC = "SEMANTIC"
    RELATIONAL = "RELATIONAL"
    RESOURCE = "RESOURCE"
    COMPOSITE = "COMPOSITE"

    def __str__(self) -> str:  # noqa: D105
        return self.value


class WitnessStatusKind(str, Enum):
    """Status values for a :class:`SpecificationsTargetGeometryClausesWitness`.

    Attributes
    ----------
    SATISFIED : str
        All clauses satisfied; global section exists.
    PARTIALLY_SATISFIED : str
        Some clauses satisfied; global section does not exist.
    UNSATISFIED : str
        No clauses satisfied.
    INCOHERENT : str
        Clause prescriptions are mutually incoherent.
    PENDING : str
        Analysis not yet completed.
    ERROR : str
        An error occurred during witness construction.
    """

    SATISFIED = "SATISFIED"
    PARTIALLY_SATISFIED = "PARTIALLY_SATISFIED"
    UNSATISFIED = "UNSATISFIED"
    INCOHERENT = "INCOHERENT"
    PENDING = "PENDING"
    ERROR = "ERROR"

    def __str__(self) -> str:  # noqa: D105
        return self.value


# ---------------------------------------------------------------------------
# Supporting dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TargetSectionClause:
    """A local section prescription over a single patch of the site.

    A clause specifies what the judgment sheaf *must* look like over the open
    patch indexed by *coordinate*.  The ``prescribed_judgment_fields`` dict
    maps component names from :data:`JUDGMENT_COMPONENTS` to their required
    values; omitted components are left underspecified.

    Parameters
    ----------
    clause_id : str
        Unique identifier for this clause.
    coordinate : str
        The site coordinate (patch) over which this clause is defined.
    formula : str
        The formula or type prescription that the clause asserts.
    prescribed_judgment_fields : dict
        Mapping from judgment component names to prescribed values.  Keys
        must be drawn from :data:`JUDGMENT_COMPONENTS`.
    restriction_map : dict
        Maps target sub-coordinates to restricted clause variants.  Keys are
        canonical coordinate strings; values are ``clause_id`` strings of the
        restricted clauses.
    clause_kind : str
        Category of the clause; should be a :class:`ClauseKind` value.
    priority : int
        Ordering priority (higher = more important).  Used when resolving
        conflicts between overlapping clauses.
    created_at : str
        ISO-8601 timestamp of clause creation.

    Examples
    --------
    >>> c = TargetSectionClause(
    ...     clause_id="clause-abc12345",
    ...     coordinate="req.auth",
    ...     formula="AuthModule : Module",
    ...     prescribed_judgment_fields={"T": "VERIFIED", "phi": "AuthModule : Module"},
    ...     restriction_map={},
    ...     clause_kind=ClauseKind.STRUCTURAL,
    ...     priority=10,
    ...     created_at="2024-01-01T00:00:00+00:00",
    ... )
    """

    clause_id: str
    coordinate: str
    formula: str
    prescribed_judgment_fields: dict[str, Any]
    restriction_map: dict[str, str]
    clause_kind: str
    priority: int
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        """Serialise the clause to a plain dictionary.

        Returns
        -------
        dict
            JSON-serialisable representation of all fields.
        """
        return {
            "clause_id": self.clause_id,
            "coordinate": self.coordinate,
            "formula": self.formula,
            "prescribed_judgment_fields": dict(self.prescribed_judgment_fields),
            "restriction_map": dict(self.restriction_map),
            "clause_kind": str(self.clause_kind),
            "priority": self.priority,
            "created_at": self.created_at,
        }

    def restrict_to(self, sub_coordinate: str) -> TargetSectionClause:
        """Produce a restricted version of this clause over *sub_coordinate*.

        The restriction inherits all fields but narrows the coordinate and
        re-generates the clause identifier to reflect the new scope.

        Parameters
        ----------
        sub_coordinate : str
            The narrower coordinate (must be a sub-coordinate of
            ``self.coordinate``).

        Returns
        -------
        TargetSectionClause
            A new clause with ``coordinate=sub_coordinate`` and a freshly
            derived ``clause_id``.

        Raises
        ------
        ValueError
            If *sub_coordinate* is not hierarchically contained within
            ``self.coordinate``.
        """
        canon_self = _canonical_coordinate(self.coordinate)
        canon_sub = _canonical_coordinate(sub_coordinate)
        if not (canon_sub == canon_self or canon_sub.startswith(canon_self + ".")):
            raise ValueError(
                f"Cannot restrict clause at '{self.coordinate}' to "
                f"'{sub_coordinate}': not a sub-coordinate."
            )
        new_id = _make_clause_id(sub_coordinate, self.formula)
        new_restriction_map = dict(self.restriction_map)
        new_restriction_map[canon_self] = self.clause_id
        return TargetSectionClause(
            clause_id=new_id,
            coordinate=sub_coordinate,
            formula=self.formula,
            prescribed_judgment_fields=dict(self.prescribed_judgment_fields),
            restriction_map=new_restriction_map,
            clause_kind=self.clause_kind,
            priority=self.priority,
            created_at=_now_iso(),
        )


@dataclass(frozen=True, slots=True)
class TruthCondition:
    """A satisfaction criterion attached to a :class:`TargetSectionClause`.

    A truth condition specifies *how* a clause is to be satisfied: what
    evidence is required, what trust tier the evidence must achieve, whether
    the negation of the clause can be accepted, and what kind of criterion
    (e.g. unit-test, formal-proof, human-review) is relevant.

    Parameters
    ----------
    condition_id : str
        Unique identifier for this truth condition.
    clause_id : str
        The clause to which this condition is attached.
    satisfaction_criterion : str
        A human-readable description of what constitutes satisfaction (e.g.
        ``"All unit tests in the auth module pass"``).
    required_evidence_kinds : list of str
        The kinds of evidence that must be present in the judgment's E field
        (e.g. ``["unit_test", "code_review"]``).
    trust_threshold : str
        The minimum trust tier that evidence must achieve.  One of
        ``"UNVERIFIED"``, ``"PROPOSED"``, ``"TRUSTED"``, ``"VERIFIED"``.
    negation_allowed : bool
        If ``True``, the truth condition may be satisfied by evidence that the
        clause's formula is *not* required (i.e. the clause can be waived).
    condition_kind : str
        Category of the truth condition (e.g. ``"automated"``,
        ``"manual"``, ``"formal"``).

    Examples
    --------
    >>> tc = TruthCondition(
    ...     condition_id="cond-deadbeef",
    ...     clause_id="clause-abc12345",
    ...     satisfaction_criterion="Auth module unit tests pass at ≥95% coverage",
    ...     required_evidence_kinds=["unit_test"],
    ...     trust_threshold="TRUSTED",
    ...     negation_allowed=False,
    ...     condition_kind="automated",
    ... )
    """

    condition_id: str
    clause_id: str
    satisfaction_criterion: str
    required_evidence_kinds: list[str]
    trust_threshold: str
    negation_allowed: bool
    condition_kind: str

    def trust_tier_rank(self) -> int:
        """Return the integer rank of ``self.trust_threshold``.

        Higher rank means stronger trust requirement.

        Returns
        -------
        int
            Rank drawn from :data:`_TRUST_TIER_ORDER`.  Unknown tiers map
            to ``-1``.
        """
        return _TRUST_TIER_ORDER.get(self.trust_threshold.upper(), -1)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary.

        Returns
        -------
        dict
            JSON-serialisable representation.
        """
        return {
            "condition_id": self.condition_id,
            "clause_id": self.clause_id,
            "satisfaction_criterion": self.satisfaction_criterion,
            "required_evidence_kinds": list(self.required_evidence_kinds),
            "trust_threshold": self.trust_threshold,
            "negation_allowed": self.negation_allowed,
            "condition_kind": self.condition_kind,
        }

    def summary(self) -> str:
        """Return a one-line human-readable summary.

        Returns
        -------
        str
            Formatted summary string.
        """
        return _format_truth_condition_summary(self)


@dataclass(slots=True)
class ClauseParseResult:
    """Mutable record capturing the outcome of parsing raw clause input.

    Accumulates parsed clauses, errors, and warnings so that multiple parse
    passes can be merged via :func:`merge_clause_parse_results`.

    Parameters
    ----------
    parse_id : str
        Unique identifier for this parse run.
    raw_input : str
        The original raw string that was parsed.
    parsed_clauses : list of TargetSectionClause
        Successfully parsed clause objects.
    parse_errors : list of str
        Error messages for input that could not be parsed.
    parse_warnings : list of str
        Non-fatal warnings (e.g. deprecated syntax, implicit defaults used).
    parse_metadata : dict
        Freeform metadata about the parse run (version, strategy, etc.).
    parse_timestamp : str
        ISO-8601 timestamp of when the parse was performed.
    """

    parse_id: str
    raw_input: str
    parsed_clauses: list[TargetSectionClause]
    parse_errors: list[str]
    parse_warnings: list[str]
    parse_metadata: dict[str, Any]
    parse_timestamp: str

    @property
    def success(self) -> bool:
        """Whether the parse produced at least one clause without errors.

        Returns
        -------
        bool
            ``True`` if ``parsed_clauses`` is non-empty and ``parse_errors``
            is empty.
        """
        return bool(self.parsed_clauses) and not self.parse_errors

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary.

        Returns
        -------
        dict
            JSON-serialisable representation.
        """
        return {
            "parse_id": self.parse_id,
            "raw_input": self.raw_input,
            "parsed_clauses": [c.to_dict() for c in self.parsed_clauses],
            "parse_errors": list(self.parse_errors),
            "parse_warnings": list(self.parse_warnings),
            "parse_metadata": dict(self.parse_metadata),
            "parse_timestamp": self.parse_timestamp,
        }


@dataclass(frozen=True, slots=True)
class TargetSection:
    """An assembled target section of the judgment sheaf over the site.

    A target section is the global (or partial) prescription σ: X → J built
    from a collection of clauses.  It indexes judgments by coordinate, clauses
    by clause identifier, and truth conditions by condition identifier.

    Parameters
    ----------
    section_id : str
        Unique identifier for this target section.
    spec_id : str
        The specification identifier this section belongs to.
    coordinate_to_judgment : dict
        Maps canonical coordinate strings to judgment-field dicts.  Each
        judgment-field dict uses keys from :data:`JUDGMENT_COMPONENTS`.
    clause_index : dict
        Maps clause_id strings to :class:`TargetSectionClause` instances.
    truth_condition_index : dict
        Maps condition_id strings to :class:`TruthCondition` instances.
    is_complete : bool
        ``True`` if the section is defined over all coordinates present in
        ``clause_index`` with no incoherence violations.
    created_at : str
        ISO-8601 creation timestamp.

    Notes
    -----
    A section is *complete* in the sheaf-theoretic sense when every clause is
    represented, all truth conditions are consistent, and no incoherence
    violations have been detected.  Incompleteness corresponds to a partial
    section — a pre-sheaf section that fails the gluing axiom on some cover.
    """

    section_id: str
    spec_id: str
    coordinate_to_judgment: dict[str, dict[str, Any]]
    clause_index: dict[str, TargetSectionClause]
    truth_condition_index: dict[str, TruthCondition]
    is_complete: bool
    created_at: str

    @property
    def coordinates(self) -> list[str]:
        """All coordinates present in this section.

        Returns
        -------
        list of str
            Sorted list of canonical coordinate strings.
        """
        return sorted(self.coordinate_to_judgment.keys())

    @property
    def clause_count(self) -> int:
        """Number of clauses in the section.

        Returns
        -------
        int
        """
        return len(self.clause_index)

    def judgment_at(self, coordinate: str) -> dict[str, Any] | None:
        """Retrieve the prescribed judgment fields at *coordinate*.

        Parameters
        ----------
        coordinate : str
            The coordinate to look up (normalised automatically).

        Returns
        -------
        dict or None
            The judgment-field dict if the coordinate is present, otherwise
            ``None``.
        """
        return self.coordinate_to_judgment.get(_canonical_coordinate(coordinate))

    def clauses_for_coordinate(self, coordinate: str) -> list[TargetSectionClause]:
        """Return all clauses whose coordinate matches *coordinate*.

        Parameters
        ----------
        coordinate : str
            The coordinate to search for.

        Returns
        -------
        list of TargetSectionClause
            Possibly empty list of matching clauses.
        """
        canon = _canonical_coordinate(coordinate)
        return [
            c for c in self.clause_index.values()
            if _canonical_coordinate(c.coordinate) == canon
        ]

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary.

        Returns
        -------
        dict
            JSON-serialisable representation.
        """
        return {
            "section_id": self.section_id,
            "spec_id": self.spec_id,
            "coordinate_to_judgment": {
                k: dict(v) for k, v in self.coordinate_to_judgment.items()
            },
            "clause_index": {k: v.to_dict() for k, v in self.clause_index.items()},
            "truth_condition_index": {
                k: v.to_dict() for k, v in self.truth_condition_index.items()
            },
            "is_complete": self.is_complete,
            "created_at": self.created_at,
        }


@dataclass(frozen=True, slots=True)
class SpecificationsTargetGeometryClausesWitness:
    """Immutable witness record for a clause-based target geometry analysis.

    A witness records — at a point in time — which clauses of the
    specification were satisfied, which truth conditions were met, the
    coverage fraction, whether a global section could be assembled, and the
    trust tier under which the witness was issued.

    Parameters
    ----------
    witness_id : str
        Unique identifier for this witness.
    spec_id : str
        The specification identifier this witness pertains to.
    clauses_satisfied : list of str
        Clause IDs of clauses that were satisfied.
    clauses_unsatisfied : list of str
        Clause IDs of clauses that remain unsatisfied.
    truth_conditions_met : list of str
        Condition IDs of truth conditions that were met.
    truth_conditions_failed : list of str
        Condition IDs of truth conditions that failed.
    coverage_fraction : float
        Fraction of clauses satisfied, in ``[0.0, 1.0]``.
    global_section_exists : bool
        ``True`` if the satisfied clauses can be glued into a global section.
    witness_status : str
        One of the :class:`WitnessStatusKind` values.
    trust_tier : str
        Trust tier of this witness (e.g. ``"PROPOSAL"``, ``"VERIFIED"``).
    provenance : dict
        Provenance record describing how the witness was produced.
    timestamp : str
        ISO-8601 timestamp of witness creation.

    Notes
    -----
    Generation proposals enter at :data:`PROPOSAL_TRUST_TIER` and must be
    upgraded by a human reviewer to reach ``VERIFIED``.
    """

    witness_id: str
    spec_id: str
    clauses_satisfied: list[str]
    clauses_unsatisfied: list[str]
    truth_conditions_met: list[str]
    truth_conditions_failed: list[str]
    coverage_fraction: float
    global_section_exists: bool
    witness_status: str
    trust_tier: str
    provenance: dict[str, Any]
    timestamp: str

    def is_fully_satisfied(self) -> bool:
        """Whether the witness records full satisfaction of the specification.

        Returns
        -------
        bool
            ``True`` when ``coverage_fraction == 1.0`` and
            ``global_section_exists`` is ``True``.
        """
        return self.coverage_fraction >= 1.0 and self.global_section_exists

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary.

        Returns
        -------
        dict
            JSON-serialisable representation.
        """
        return {
            "witness_id": self.witness_id,
            "spec_id": self.spec_id,
            "clauses_satisfied": list(self.clauses_satisfied),
            "clauses_unsatisfied": list(self.clauses_unsatisfied),
            "truth_conditions_met": list(self.truth_conditions_met),
            "truth_conditions_failed": list(self.truth_conditions_failed),
            "coverage_fraction": self.coverage_fraction,
            "global_section_exists": self.global_section_exists,
            "witness_status": self.witness_status,
            "trust_tier": self.trust_tier,
            "provenance": dict(self.provenance),
            "timestamp": self.timestamp,
        }


# ---------------------------------------------------------------------------
# Main classes
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class SpecificationsTargetGeometryClausesCoordinator:
    """Orchestrator for clause-based target geometry operations.

    The coordinator maintains registries of clauses and truth conditions, builds
    and validates target sections, supports restriction to sub-patches, and
    records a full parse/error log.  It is the primary entry point for
    constructing the specification geometry from raw or pre-parsed data.

    Parameters
    ----------
    coordinator_id : str
        Unique identifier for this coordinator instance.
    site_coordinates : list of str
        The canonical coordinates of the project site over which sections are
        defined.
    target_section_map : dict
        Maps section_id strings to assembled :class:`TargetSection` objects.
    clause_registry : dict
        Maps clause_id strings to :class:`TargetSectionClause` objects.
    truth_condition_registry : dict
        Maps condition_id strings to :class:`TruthCondition` objects.
    parse_log : list of str
        Timestamped log of successful parse and build events.
    error_log : list of str
        Timestamped log of errors encountered during operation.

    Examples
    --------
    >>> coord = SpecificationsTargetGeometryClausesCoordinator(
    ...     coordinator_id="coord-001",
    ...     site_coordinates=["req.auth", "req.data", "req.api"],
    ...     target_section_map={},
    ...     clause_registry={},
    ...     truth_condition_registry={},
    ...     parse_log=[],
    ...     error_log=[],
    ... )
    >>> result = coord.register_clause(
    ...     coordinate="req.auth",
    ...     formula="AuthModule : Module",
    ...     prescribed_fields={"T": "TRUSTED"},
    ... )
    """

    coordinator_id: str
    site_coordinates: list[str]
    target_section_map: dict[str, TargetSection]
    clause_registry: dict[str, TargetSectionClause]
    truth_condition_registry: dict[str, TruthCondition]
    parse_log: list[str]
    error_log: list[str]

    def _log(self, msg: str) -> None:
        """Append a timestamped message to the parse log.

        Parameters
        ----------
        msg : str
            Message to record.
        """
        self.parse_log.append(f"[{_now_iso()}] {msg}")

    def _err(self, msg: str) -> None:
        """Append a timestamped message to the error log.

        Parameters
        ----------
        msg : str
            Error message to record.
        """
        self.error_log.append(f"[{_now_iso()}] ERROR: {msg}")

    def register_clause(
        self,
        coordinate: str,
        formula: str,
        prescribed_fields: dict[str, Any] | None = None,
        clause_kind: str = ClauseKind.STRUCTURAL,
        priority: int = 0,
        restriction_map: dict[str, str] | None = None,
    ) -> TargetSectionClause:
        """Register a new clause in the coordinator's registry.

        Constructs a :class:`TargetSectionClause`, validates that the coordinate
        is known to the site (or adds it), and stores the clause.

        Parameters
        ----------
        coordinate : str
            The site coordinate for the clause.
        formula : str
            The formula prescription.
        prescribed_fields : dict, optional
            Judgment field prescriptions; defaults to ``{"phi": formula}``.
        clause_kind : str
            Clause category; defaults to :attr:`ClauseKind.STRUCTURAL`.
        priority : int
            Ordering priority; defaults to ``0``.
        restriction_map : dict, optional
            Pre-existing restriction map; defaults to empty dict.

        Returns
        -------
        TargetSectionClause
            The newly registered clause.

        Raises
        ------
        ValueError
            If *formula* is empty or *coordinate* is empty.
        """
        if not coordinate.strip():
            self._err("register_clause called with empty coordinate")
            raise ValueError("coordinate must be non-empty")
        if not formula.strip():
            self._err("register_clause called with empty formula")
            raise ValueError("formula must be non-empty")

        canon = _canonical_coordinate(coordinate)
        if canon not in self.site_coordinates:
            self.site_coordinates.append(canon)
            self._log(f"Added new coordinate '{canon}' to site")

        fields = dict(prescribed_fields) if prescribed_fields else {"phi": formula}
        # Ensure phi is always set
        fields.setdefault("phi", formula)

        clause = TargetSectionClause(
            clause_id=_make_clause_id(canon, formula),
            coordinate=canon,
            formula=formula,
            prescribed_judgment_fields=fields,
            restriction_map=dict(restriction_map) if restriction_map else {},
            clause_kind=str(clause_kind),
            priority=priority,
            created_at=_now_iso(),
        )
        self.clause_registry[clause.clause_id] = clause
        self._log(f"Registered clause {clause.clause_id} at coordinate '{canon}'")
        return clause

    def register_truth_condition(
        self,
        clause_id: str,
        satisfaction_criterion: str,
        required_evidence_kinds: list[str] | None = None,
        trust_threshold: str = DEFAULT_TRUST_TIER,
        negation_allowed: bool = False,
        condition_kind: str = "automated",
    ) -> TruthCondition:
        """Register a truth condition for an existing clause.

        Parameters
        ----------
        clause_id : str
            The clause to attach this condition to.  Must already be in the
            registry.
        satisfaction_criterion : str
            Human-readable description of what satisfies the clause.
        required_evidence_kinds : list of str, optional
            Evidence kind tags required in the judgment's E field.
        trust_threshold : str
            Minimum trust tier for the evidence.  Defaults to
            :data:`DEFAULT_TRUST_TIER`.
        negation_allowed : bool
            Whether waiver by negation is permitted.
        condition_kind : str
            Category tag for the condition (e.g. ``"automated"``,
            ``"formal"``).

        Returns
        -------
        TruthCondition
            The newly registered truth condition.

        Raises
        ------
        KeyError
            If *clause_id* is not present in the registry.
        ValueError
            If *satisfaction_criterion* is empty.
        """
        if clause_id not in self.clause_registry:
            self._err(
                f"register_truth_condition: unknown clause_id '{clause_id}'"
            )
            raise KeyError(f"Clause '{clause_id}' not found in registry")
        if not satisfaction_criterion.strip():
            self._err("register_truth_condition: empty satisfaction_criterion")
            raise ValueError("satisfaction_criterion must be non-empty")

        cond = TruthCondition(
            condition_id=_make_condition_id(clause_id, satisfaction_criterion),
            clause_id=clause_id,
            satisfaction_criterion=satisfaction_criterion,
            required_evidence_kinds=list(required_evidence_kinds or []),
            trust_threshold=trust_threshold.upper(),
            negation_allowed=negation_allowed,
            condition_kind=condition_kind,
        )
        self.truth_condition_registry[cond.condition_id] = cond
        self._log(
            f"Registered truth condition {cond.condition_id} for clause {clause_id}"
        )
        return cond

    def build_target_section(
        self,
        spec_id: str | None = None,
        clause_ids: list[str] | None = None,
    ) -> TargetSection:
        """Assemble a :class:`TargetSection` from registered clauses.

        When *clause_ids* is provided, only those clauses are included.
        Otherwise all registered clauses are used (up to
        :data:`MAX_CLAUSES_PER_SECTION`).

        Parameters
        ----------
        spec_id : str, optional
            Specification identifier.  Auto-generated if omitted.
        clause_ids : list of str, optional
            Subset of clause IDs to include.

        Returns
        -------
        TargetSection
            The assembled target section.

        Raises
        ------
        ValueError
            If the resulting clause list would exceed
            :data:`MAX_CLAUSES_PER_SECTION`.
        """
        sid = spec_id or f"spec-{uuid.uuid4().hex[:8]}"
        if clause_ids is not None:
            clauses = [
                self.clause_registry[cid]
                for cid in clause_ids
                if cid in self.clause_registry
            ]
        else:
            clauses = list(self.clause_registry.values())

        if len(clauses) > MAX_CLAUSES_PER_SECTION:
            self._err(
                f"build_target_section: {len(clauses)} clauses exceeds "
                f"MAX_CLAUSES_PER_SECTION={MAX_CLAUSES_PER_SECTION}"
            )
            raise ValueError(
                f"Too many clauses: {len(clauses)} > {MAX_CLAUSES_PER_SECTION}"
            )

        section = build_target_section(clauses, spec_id=sid)

        # Include only truth conditions whose clause_id is in the section
        included_clause_ids = set(section.clause_index.keys())
        tc_index: dict[str, TruthCondition] = {
            cid: tc
            for cid, tc in self.truth_condition_registry.items()
            if tc.clause_id in included_clause_ids
        }

        # Rebuild with truth conditions merged in
        final_section = TargetSection(
            section_id=section.section_id,
            spec_id=section.spec_id,
            coordinate_to_judgment=section.coordinate_to_judgment,
            clause_index=section.clause_index,
            truth_condition_index=tc_index,
            is_complete=section.is_complete,
            created_at=section.created_at,
        )

        self.target_section_map[final_section.section_id] = final_section
        self._log(
            f"Built target section {final_section.section_id} "
            f"with {len(clauses)} clauses and {len(tc_index)} truth conditions"
        )
        return final_section

    def validate_target_section(self, section_id: str) -> tuple[bool, list[str]]:
        """Validate a previously built target section.

        Parameters
        ----------
        section_id : str
            The section to validate.

        Returns
        -------
        tuple of (bool, list of str)
            ``(valid, issues)`` where *valid* is ``True`` when no issues are
            found.

        Raises
        ------
        KeyError
            If *section_id* is not in ``target_section_map``.
        """
        if section_id not in self.target_section_map:
            raise KeyError(f"Section '{section_id}' not found")
        section = self.target_section_map[section_id]
        valid, issues = validate_target_section(section)
        if valid:
            self._log(f"Validated section {section_id}: OK")
        else:
            for issue in issues:
                self._err(f"Validation issue in {section_id}: {issue}")
        return valid, issues

    def restrict_to_patch(
        self, section_id: str, patch_coordinates: list[str]
    ) -> TargetSection:
        """Restrict a target section to a sub-patch of the site.

        Parameters
        ----------
        section_id : str
            The section to restrict.
        patch_coordinates : list of str
            The coordinates to retain in the restricted section.

        Returns
        -------
        TargetSection
            The restricted section (stored in ``target_section_map``).
        """
        if section_id not in self.target_section_map:
            raise KeyError(f"Section '{section_id}' not found")
        section = self.target_section_map[section_id]
        restricted = restrict_section_to_patch(section, patch_coordinates)
        self.target_section_map[restricted.section_id] = restricted
        self._log(
            f"Restricted section {section_id} → {restricted.section_id} "
            f"over {len(patch_coordinates)} coordinates"
        )
        return restricted

    def compose_with_coordinator(
        self, other: SpecificationsTargetGeometryClausesCoordinator
    ) -> SpecificationsTargetGeometryClausesCoordinator:
        """Merge *other*'s registries into this coordinator.

        Clauses, truth conditions, site coordinates, and sections from *other*
        are merged in.  Conflicts on clause_id are resolved by keeping the
        higher-priority clause.

        Parameters
        ----------
        other : SpecificationsTargetGeometryClausesCoordinator
            The coordinator to merge from.

        Returns
        -------
        SpecificationsTargetGeometryClausesCoordinator
            ``self`` after mutation (for chaining).
        """
        for coord in other.site_coordinates:
            if coord not in self.site_coordinates:
                self.site_coordinates.append(coord)

        for cid, clause in other.clause_registry.items():
            if cid in self.clause_registry:
                existing = self.clause_registry[cid]
                if clause.priority > existing.priority:
                    self.clause_registry[cid] = clause
                    self._log(f"Replaced clause {cid} with higher-priority version")
            else:
                self.clause_registry[cid] = clause

        for tcid, tc in other.truth_condition_registry.items():
            if tcid not in self.truth_condition_registry:
                self.truth_condition_registry[tcid] = tc

        for sid, section in other.target_section_map.items():
            if sid not in self.target_section_map:
                self.target_section_map[sid] = section

        self._log(
            f"Composed with coordinator {other.coordinator_id}: "
            f"+{len(other.clause_registry)} clauses, "
            f"+{len(other.truth_condition_registry)} conditions"
        )
        return self

    def get_parse_summary(self) -> dict[str, Any]:
        """Return a summary of the coordinator's current state.

        Returns
        -------
        dict
            Summary with counts, log excerpts, and registration statistics.
        """
        return {
            "coordinator_id": self.coordinator_id,
            "site_coordinate_count": len(self.site_coordinates),
            "clause_count": len(self.clause_registry),
            "truth_condition_count": len(self.truth_condition_registry),
            "section_count": len(self.target_section_map),
            "parse_log_lines": len(self.parse_log),
            "error_count": len(self.error_log),
            "recent_parse_log": self.parse_log[-5:],
            "recent_errors": self.error_log[-5:],
        }

    def reset(self) -> None:
        """Clear all registries and logs, retaining only coordinator_id.

        After calling ``reset``, ``site_coordinates``, ``clause_registry``,
        ``truth_condition_registry``, ``target_section_map``, ``parse_log``,
        and ``error_log`` are all empty.
        """
        self.site_coordinates.clear()
        self.clause_registry.clear()
        self.truth_condition_registry.clear()
        self.target_section_map.clear()
        self.parse_log.clear()
        self.error_log.clear()


@dataclass(slots=True)
class SpecificationsTargetGeometryClausesAnalyzer:
    """Analyzer for clause-based specification target geometry.

    Performs structural, coverage, and coherence analyses on clauses, truth
    conditions, and assembled target sections.  Results are accumulated in
    ``analysis_results`` and supporting maps for later report generation.

    Parameters
    ----------
    analyzer_id : str
        Unique identifier for this analyzer instance.
    analysis_results : dict
        Freeform mapping of analysis keys to result values.
    clause_coverage_map : dict
        Maps clause_id to coverage status (``"covered"`` or ``"uncovered"``).
    truth_gaps : list of str
        Condition IDs of truth conditions that could not be satisfied.
    coherence_violations : list of str
        Descriptions of incoherence violations detected during analysis.
    analysis_log : list of str
        Timestamped log of analysis events.

    Examples
    --------
    >>> analyzer = SpecificationsTargetGeometryClausesAnalyzer(
    ...     analyzer_id="ana-001",
    ...     analysis_results={},
    ...     clause_coverage_map={},
    ...     truth_gaps=[],
    ...     coherence_violations=[],
    ...     analysis_log=[],
    ... )
    """

    analyzer_id: str
    analysis_results: dict[str, Any]
    clause_coverage_map: dict[str, str]
    truth_gaps: list[str]
    coherence_violations: list[str]
    analysis_log: list[str]

    def _log(self, msg: str) -> None:
        """Append a timestamped log entry.

        Parameters
        ----------
        msg : str
            Message to record.
        """
        self.analysis_log.append(f"[{_now_iso()}] {msg}")

    def analyze_clause(self, clause: TargetSectionClause) -> dict[str, Any]:
        """Analyse a single clause for structural correctness.

        Checks that all prescribed judgment fields use valid component names,
        that the clause kind is a known :class:`ClauseKind`, and that the
        coordinate is non-empty.

        Parameters
        ----------
        clause : TargetSectionClause
            The clause to analyse.

        Returns
        -------
        dict
            Analysis record with keys ``"clause_id"``, ``"issues"``,
            ``"valid"``, ``"kind_known"``, ``"field_names_valid"``.
        """
        issues: list[str] = []
        valid_components = set(JUDGMENT_COMPONENTS)
        invalid_fields = [
            k for k in clause.prescribed_judgment_fields
            if k not in valid_components
        ]
        if invalid_fields:
            issues.append(
                f"Unknown judgment field names: {invalid_fields}. "
                f"Valid: {list(JUDGMENT_COMPONENTS)}"
            )

        kind_known = clause.clause_kind in {ck.value for ck in ClauseKind}
        if not kind_known:
            issues.append(
                f"Unknown clause_kind '{clause.clause_kind}'. "
                f"Valid: {[ck.value for ck in ClauseKind]}"
            )

        if not clause.coordinate.strip():
            issues.append("Clause has empty coordinate")

        if not clause.formula.strip():
            issues.append("Clause has empty formula")

        result = {
            "clause_id": clause.clause_id,
            "issues": issues,
            "valid": len(issues) == 0,
            "kind_known": kind_known,
            "field_names_valid": len(invalid_fields) == 0,
        }
        status = "covered" if result["valid"] else "uncovered"
        self.clause_coverage_map[clause.clause_id] = status
        self.analysis_results[f"clause:{clause.clause_id}"] = result
        self._log(
            f"Analyzed clause {clause.clause_id}: valid={result['valid']}, "
            f"issues={len(issues)}"
        )
        return result

    def analyze_truth_condition(self, tc: TruthCondition) -> dict[str, Any]:
        """Analyse a truth condition for completeness and consistency.

        Checks that the trust threshold is a known tier, that the clause
        reference is resolvable, and that the condition kind is non-empty.

        Parameters
        ----------
        tc : TruthCondition
            The truth condition to analyse.

        Returns
        -------
        dict
            Analysis record with keys ``"condition_id"``, ``"issues"``,
            ``"valid"``, ``"tier_known"``, ``"has_evidence_kinds"``.
        """
        issues: list[str] = []
        tier_known = tc.trust_threshold.upper() in _TRUST_TIER_ORDER
        if not tier_known:
            issues.append(
                f"Unknown trust_threshold '{tc.trust_threshold}'. "
                f"Valid: {list(_TRUST_TIER_ORDER.keys())}"
            )

        if not tc.satisfaction_criterion.strip():
            issues.append("Truth condition has empty satisfaction_criterion")

        if not tc.condition_kind.strip():
            issues.append("Truth condition has empty condition_kind")

        has_evidence = bool(tc.required_evidence_kinds)
        if not has_evidence:
            # Not an error, but worth noting
            pass

        result = {
            "condition_id": tc.condition_id,
            "issues": issues,
            "valid": len(issues) == 0,
            "tier_known": tier_known,
            "has_evidence_kinds": has_evidence,
        }
        if not result["valid"]:
            self.truth_gaps.append(tc.condition_id)
        self.analysis_results[f"tc:{tc.condition_id}"] = result
        self._log(
            f"Analyzed truth condition {tc.condition_id}: valid={result['valid']}"
        )
        return result

    def analyze_target_section(self, section: TargetSection) -> dict[str, Any]:
        """Analyse a target section for structural integrity.

        Runs :meth:`analyze_clause` on each clause, checks that every clause
        has at least one truth condition, and checks for incoherence.

        Parameters
        ----------
        section : TargetSection
            The target section to analyse.

        Returns
        -------
        dict
            Aggregate analysis record including per-clause and per-condition
            results, coherence status, and coverage fraction.
        """
        clause_results = {}
        for cid, clause in section.clause_index.items():
            clause_results[cid] = self.analyze_clause(clause)

        condition_results = {}
        for tcid, tc in section.truth_condition_index.items():
            condition_results[tcid] = self.analyze_truth_condition(tc)

        # Check each clause has at least one truth condition
        clauses_without_conditions: list[str] = []
        for cid in section.clause_index:
            has_cond = any(
                tc.clause_id == cid
                for tc in section.truth_condition_index.values()
            )
            if not has_cond:
                clauses_without_conditions.append(cid)

        coherence_ok, violations = self.check_coherence(section)

        covered = [
            cid for cid, r in clause_results.items() if r.get("valid", False)
        ]
        all_clause_ids = list(section.clause_index.keys())
        coverage = _compute_coverage_fraction(covered, all_clause_ids)

        result: dict[str, Any] = {
            "section_id": section.section_id,
            "clause_count": section.clause_count,
            "clause_results": clause_results,
            "condition_results": condition_results,
            "clauses_without_conditions": clauses_without_conditions,
            "coherent": coherence_ok,
            "coherence_violations": violations,
            "coverage_fraction": coverage,
            "is_complete": section.is_complete,
        }
        self.analysis_results[f"section:{section.section_id}"] = result
        self._log(
            f"Analyzed section {section.section_id}: "
            f"coverage={coverage:.2%}, coherent={coherence_ok}"
        )
        return result

    def check_coherence(self, section: TargetSection) -> tuple[bool, list[str]]:
        """Check a target section for incoherence violations.

        Two clauses are *incoherent* when their coordinates overlap and their
        prescribed judgment fields conflict.  This corresponds to a failure of
        the sheaf gluing axiom over the intersection of the patches.

        Parameters
        ----------
        section : TargetSection
            Section to check.

        Returns
        -------
        tuple of (bool, list of str)
            ``(coherent, violations)`` where *violations* is empty when
            *coherent* is ``True``.
        """
        violations: list[str] = []
        clauses = list(section.clause_index.values())
        for i in range(len(clauses)):
            for j in range(i + 1, len(clauses)):
                c1, c2 = clauses[i], clauses[j]
                if _clause_overlap(c1, c2):
                    j1 = c1.prescribed_judgment_fields
                    j2 = c2.prescribed_judgment_fields
                    if not _judgment_fields_compatible(j1, j2):
                        msg = (
                            f"Incoherence: clauses {c1.clause_id} and "
                            f"{c2.clause_id} overlap at coordinates "
                            f"'{c1.coordinate}'/'{c2.coordinate}' with "
                            f"incompatible judgment fields"
                        )
                        violations.append(msg)
                        self.coherence_violations.append(msg)
        coherent = len(violations) == 0
        self._log(f"Coherence check: {len(violations)} violations found")
        return coherent, violations

    def compute_coverage(
        self,
        section: TargetSection,
        covered_clause_ids: list[str],
    ) -> float:
        """Compute the coverage fraction for a section given a set of covered clauses.

        Parameters
        ----------
        section : TargetSection
            The section whose clause inventory defines the denominator.
        covered_clause_ids : list of str
            Clause IDs that have been covered / satisfied.

        Returns
        -------
        float
            Coverage fraction in ``[0.0, 1.0]``.
        """
        total = list(section.clause_index.keys())
        fraction = _compute_coverage_fraction(covered_clause_ids, total)
        self.analysis_results["coverage_fraction"] = fraction
        self._log(
            f"Coverage: {len(covered_clause_ids)}/{len(total)} "
            f"= {fraction:.2%}"
        )
        return fraction

    def find_truth_gaps(
        self,
        section: TargetSection,
        satisfied_condition_ids: list[str] | None = None,
    ) -> list[str]:
        """Identify truth conditions that have no evidence of satisfaction.

        A truth *gap* is a condition whose condition_id does not appear in
        *satisfied_condition_ids*.

        Parameters
        ----------
        section : TargetSection
            The section whose truth condition index is interrogated.
        satisfied_condition_ids : list of str, optional
            IDs of conditions already satisfied.  Defaults to empty list.

        Returns
        -------
        list of str
            Condition IDs that are unsatisfied (the truth gaps).
        """
        satisfied = set(satisfied_condition_ids or [])
        gaps = [
            tcid
            for tcid in section.truth_condition_index
            if tcid not in satisfied
        ]
        for gap in gaps:
            if gap not in self.truth_gaps:
                self.truth_gaps.append(gap)
        self._log(
            f"Truth gaps: {len(gaps)} unsatisfied conditions out of "
            f"{len(section.truth_condition_index)}"
        )
        return gaps

    def produce_analysis_report(self) -> dict[str, Any]:
        """Compile a full analysis report from accumulated results.

        Returns
        -------
        dict
            Report with keys:
            - ``"analyzer_id"``
            - ``"total_analyses"``
            - ``"clause_coverage_summary"``
            - ``"truth_gap_count"``
            - ``"coherence_violation_count"``
            - ``"analysis_log_tail"``
            - ``"results_index"``
        """
        covered_clauses = [
            cid for cid, status in self.clause_coverage_map.items()
            if status == "covered"
        ]
        report: dict[str, Any] = {
            "analyzer_id": self.analyzer_id,
            "total_analyses": len(self.analysis_results),
            "clause_coverage_summary": {
                "total": len(self.clause_coverage_map),
                "covered": len(covered_clauses),
                "uncovered": len(self.clause_coverage_map) - len(covered_clauses),
                "fraction": _compute_coverage_fraction(
                    covered_clauses, list(self.clause_coverage_map.keys())
                ),
            },
            "truth_gap_count": len(self.truth_gaps),
            "truth_gaps": list(self.truth_gaps),
            "coherence_violation_count": len(self.coherence_violations),
            "coherence_violations": list(self.coherence_violations),
            "analysis_log_tail": self.analysis_log[-10:],
            "results_index": list(self.analysis_results.keys()),
        }
        self._log("Produced analysis report")
        return report


# ---------------------------------------------------------------------------
# Module-level functions
# ---------------------------------------------------------------------------


def parse_clause_from_string(
    raw: str,
    coordinate: str | None = None,
) -> ClauseParseResult:
    """Parse one or more clauses from a raw string representation.

    The parser supports a simple line-based format::

        [coordinate] :: formula [:: kind] [:: priority=N]

    Lines starting with ``#`` are treated as comments.  If *coordinate* is
    provided, it overrides the coordinate parsed from the string.

    Parameters
    ----------
    raw : str
        Raw input string containing clause definitions.
    coordinate : str, optional
        Override coordinate; applied to all parsed clauses when provided.

    Returns
    -------
    ClauseParseResult
        Result accumulating all successfully parsed clauses and any errors.

    Examples
    --------
    >>> result = parse_clause_from_string(
    ...     "req.auth :: AuthModule : Module :: STRUCTURAL :: priority=10"
    ... )
    >>> result.success
    True
    >>> result.parsed_clauses[0].coordinate
    'req.auth'
    """
    parse_id = f"parse-{uuid.uuid4().hex[:8]}"
    parsed: list[TargetSectionClause] = []
    errors: list[str] = []
    warnings: list[str] = []

    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    for lineno, line in enumerate(lines, start=1):
        if line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("::")]
        # When a coordinate override is provided, a bare formula line is valid
        if len(parts) < 2:
            if coordinate:
                parts = [coordinate, parts[0]]
            else:
                errors.append(
                    f"Line {lineno}: expected 'coordinate :: formula', got: {line!r}"
                )
                continue

        # Determine coordinate
        coord_raw = coordinate if coordinate else parts[0]
        formula_raw = parts[1]
        kind_raw = ClauseKind.STRUCTURAL
        priority_val = 0

        if len(parts) >= 3:
            kind_candidate = parts[2].upper()
            if kind_candidate in {ck.value for ck in ClauseKind}:
                kind_raw = kind_candidate
            else:
                warnings.append(
                    f"Line {lineno}: unknown clause kind '{parts[2]}', "
                    f"defaulting to STRUCTURAL"
                )

        if len(parts) >= 4:
            priority_part = parts[3]
            if priority_part.lower().startswith("priority="):
                try:
                    priority_val = int(priority_part.split("=", 1)[1])
                except ValueError:
                    warnings.append(
                        f"Line {lineno}: cannot parse priority from '{priority_part}'"
                    )

        canon_coord = _canonical_coordinate(coord_raw)
        clause = TargetSectionClause(
            clause_id=_make_clause_id(canon_coord, formula_raw),
            coordinate=canon_coord,
            formula=formula_raw,
            prescribed_judgment_fields={"phi": formula_raw},
            restriction_map={},
            clause_kind=str(kind_raw),
            priority=priority_val,
            created_at=_now_iso(),
        )
        parsed.append(clause)

    return ClauseParseResult(
        parse_id=parse_id,
        raw_input=raw,
        parsed_clauses=parsed,
        parse_errors=errors,
        parse_warnings=warnings,
        parse_metadata={
            "version": CLAUSE_PARSE_VERSION,
            "line_count": len(lines),
            "comment_count": sum(1 for ln in lines if ln.startswith("#")),
        },
        parse_timestamp=_now_iso(),
    )


def build_target_section(
    clauses: list[TargetSectionClause],
    spec_id: str | None = None,
) -> TargetSection:
    """Assemble a :class:`TargetSection` from a list of clauses.

    Merges judgment fields for clauses that share the same coordinate (using
    :func:`_judgment_fields_compatible` to detect conflicts).  Sets
    ``is_complete`` to ``True`` only when no conflicts are detected.

    Parameters
    ----------
    clauses : list of TargetSectionClause
        Clauses contributing to the section.
    spec_id : str, optional
        Specification identifier; auto-generated when omitted.

    Returns
    -------
    TargetSection
        The assembled (possibly partial) target section.

    Raises
    ------
    ValueError
        If *clauses* exceeds :data:`MAX_CLAUSES_PER_SECTION`.

    Examples
    --------
    >>> c = TargetSectionClause(
    ...     clause_id="clause-00000000",
    ...     coordinate="req.auth",
    ...     formula="X : T",
    ...     prescribed_judgment_fields={"phi": "X : T"},
    ...     restriction_map={},
    ...     clause_kind="STRUCTURAL",
    ...     priority=0,
    ...     created_at="2024-01-01T00:00:00+00:00",
    ... )
    >>> section = build_target_section([c], spec_id="spec-test")
    >>> section.is_complete
    True
    """
    if len(clauses) > MAX_CLAUSES_PER_SECTION:
        raise ValueError(
            f"Too many clauses: {len(clauses)} > {MAX_CLAUSES_PER_SECTION}"
        )

    sid = spec_id or f"spec-{uuid.uuid4().hex[:8]}"
    section_id = _make_section_id(sid, clauses)

    # Build coordinate→merged judgment fields
    coord_to_judgment: dict[str, dict[str, Any]] = {}
    is_complete = True

    for clause in clauses:
        canon = _canonical_coordinate(clause.coordinate)
        if canon not in coord_to_judgment:
            coord_to_judgment[canon] = dict(clause.prescribed_judgment_fields)
        else:
            existing = coord_to_judgment[canon]
            incoming = clause.prescribed_judgment_fields
            if not _judgment_fields_compatible(existing, incoming):
                is_complete = False
                # Merge with priority: keep existing on conflict, note gap
            # Merge non-conflicting fields from incoming
            for k, v in incoming.items():
                if k not in existing:
                    existing[k] = v

    clause_index = {c.clause_id: c for c in clauses}

    return TargetSection(
        section_id=section_id,
        spec_id=sid,
        coordinate_to_judgment=coord_to_judgment,
        clause_index=clause_index,
        truth_condition_index={},
        is_complete=is_complete,
        created_at=_now_iso(),
    )


def restrict_section_to_patch(
    section: TargetSection,
    patch_coordinates: list[str],
) -> TargetSection:
    """Restrict a target section to a set of patch coordinates.

    Only those coordinates (and their clauses) that are present in
    *patch_coordinates* are retained.  The resulting section has a new
    ``section_id`` and its ``is_complete`` flag reflects the restricted scope.

    Parameters
    ----------
    section : TargetSection
        The section to restrict.
    patch_coordinates : list of str
        The coordinates to retain.  Each entry is normalised via
        :func:`_canonical_coordinate`.

    Returns
    -------
    TargetSection
        A new section covering only the specified patch.

    Examples
    --------
    >>> restricted = restrict_section_to_patch(section, ["req.auth"])
    >>> "req.auth" in restricted.coordinates
    True
    """
    canon_patch = {_canonical_coordinate(c) for c in patch_coordinates}

    # Filter coordinate map
    restricted_coords: dict[str, dict[str, Any]] = {
        coord: fields
        for coord, fields in section.coordinate_to_judgment.items()
        if coord in canon_patch
    }

    # Filter clauses
    restricted_clauses: dict[str, TargetSectionClause] = {
        cid: clause
        for cid, clause in section.clause_index.items()
        if _canonical_coordinate(clause.coordinate) in canon_patch
    }

    # Filter truth conditions
    restricted_tcs: dict[str, TruthCondition] = {
        tcid: tc
        for tcid, tc in section.truth_condition_index.items()
        if tc.clause_id in restricted_clauses
    }

    new_section_id = _make_section_id(
        section.spec_id, list(restricted_clauses.values())
    )

    return TargetSection(
        section_id=new_section_id,
        spec_id=section.spec_id,
        coordinate_to_judgment=restricted_coords,
        clause_index=restricted_clauses,
        truth_condition_index=restricted_tcs,
        is_complete=section.is_complete and len(restricted_coords) > 0,
        created_at=_now_iso(),
    )


def compose_target_sections(
    left: TargetSection,
    right: TargetSection,
) -> TargetSection:
    """Compose two target sections by taking their union.

    Merges the coordinate-to-judgment maps, clause indices, and truth
    condition indices.  Conflicts between judgment fields at shared coordinates
    are resolved by preferring the *left* section's values; ``is_complete``
    is set to ``False`` when any conflict is detected.

    Parameters
    ----------
    left : TargetSection
        The primary (preferred) section.
    right : TargetSection
        The secondary section.

    Returns
    -------
    TargetSection
        A new section spanning all coordinates of both inputs.

    Notes
    -----
    The composed section's ``spec_id`` is taken from *left*.
    """
    is_complete = left.is_complete and right.is_complete

    merged_coords: dict[str, dict[str, Any]] = dict(left.coordinate_to_judgment)
    for coord, fields in right.coordinate_to_judgment.items():
        if coord not in merged_coords:
            merged_coords[coord] = dict(fields)
        else:
            existing = merged_coords[coord]
            if not _judgment_fields_compatible(existing, fields):
                is_complete = False
            for k, v in fields.items():
                if k not in existing:
                    existing[k] = v

    merged_clauses: dict[str, TargetSectionClause] = {
        **right.clause_index,
        **left.clause_index,  # left wins on conflict
    }

    merged_tcs: dict[str, TruthCondition] = {
        **right.truth_condition_index,
        **left.truth_condition_index,
    }

    new_section_id = _make_section_id(
        left.spec_id, list(merged_clauses.values())
    )

    return TargetSection(
        section_id=new_section_id,
        spec_id=left.spec_id,
        coordinate_to_judgment=merged_coords,
        clause_index=merged_clauses,
        truth_condition_index=merged_tcs,
        is_complete=is_complete,
        created_at=_now_iso(),
    )


def validate_target_section(
    section: TargetSection,
) -> tuple[bool, list[str]]:
    """Validate the structural integrity of a target section.

    Checks performed:

    1. All clause coordinates appear in ``coordinate_to_judgment``.
    2. All truth conditions reference a clause present in ``clause_index``.
    3. All prescribed judgment field names are valid component names.
    4. The section does not exceed :data:`MAX_CLAUSES_PER_SECTION`.
    5. No two clauses have conflicting prescribed fields at overlapping coords.

    Parameters
    ----------
    section : TargetSection
        The section to validate.

    Returns
    -------
    tuple of (bool, list of str)
        ``(valid, issues)`` — *valid* is ``True`` when *issues* is empty.
    """
    issues: list[str] = []
    valid_components = set(JUDGMENT_COMPONENTS)

    if section.clause_count > MAX_CLAUSES_PER_SECTION:
        issues.append(
            f"Section has {section.clause_count} clauses; "
            f"exceeds MAX_CLAUSES_PER_SECTION={MAX_CLAUSES_PER_SECTION}"
        )

    for cid, clause in section.clause_index.items():
        canon = _canonical_coordinate(clause.coordinate)
        if canon not in section.coordinate_to_judgment:
            issues.append(
                f"Clause {cid}: coordinate '{canon}' missing from "
                f"coordinate_to_judgment"
            )
        bad_fields = [
            k for k in clause.prescribed_judgment_fields
            if k not in valid_components
        ]
        if bad_fields:
            issues.append(
                f"Clause {cid}: invalid judgment field names {bad_fields}"
            )

    for tcid, tc in section.truth_condition_index.items():
        if tc.clause_id not in section.clause_index:
            issues.append(
                f"TruthCondition {tcid}: references unknown clause "
                f"'{tc.clause_id}'"
            )

    # Pairwise conflict check
    clauses = list(section.clause_index.values())
    for i in range(len(clauses)):
        for j in range(i + 1, len(clauses)):
            c1, c2 = clauses[i], clauses[j]
            if _clause_overlap(c1, c2):
                if not _judgment_fields_compatible(
                    c1.prescribed_judgment_fields,
                    c2.prescribed_judgment_fields,
                ):
                    issues.append(
                        f"Conflict between clauses {c1.clause_id} and "
                        f"{c2.clause_id} at overlapping coordinates "
                        f"'{c1.coordinate}'/'{c2.coordinate}'"
                    )

    return len(issues) == 0, issues


def clause_from_dict(data: dict[str, Any]) -> TargetSectionClause:
    """Deserialise a :class:`TargetSectionClause` from a plain dictionary.

    Parameters
    ----------
    data : dict
        Dictionary as produced by :meth:`TargetSectionClause.to_dict`.

    Returns
    -------
    TargetSectionClause
        The reconstructed clause.

    Raises
    ------
    KeyError
        If any required field is absent from *data*.
    """
    return TargetSectionClause(
        clause_id=data["clause_id"],
        coordinate=data["coordinate"],
        formula=data["formula"],
        prescribed_judgment_fields=dict(data.get("prescribed_judgment_fields", {})),
        restriction_map=dict(data.get("restriction_map", {})),
        clause_kind=str(data.get("clause_kind", ClauseKind.STRUCTURAL)),
        priority=int(data.get("priority", 0)),
        created_at=str(data.get("created_at", _now_iso())),
    )


def truth_condition_from_dict(data: dict[str, Any]) -> TruthCondition:
    """Deserialise a :class:`TruthCondition` from a plain dictionary.

    Parameters
    ----------
    data : dict
        Dictionary as produced by :meth:`TruthCondition.to_dict`.

    Returns
    -------
    TruthCondition
        The reconstructed truth condition.

    Raises
    ------
    KeyError
        If any required field is absent from *data*.
    """
    return TruthCondition(
        condition_id=data["condition_id"],
        clause_id=data["clause_id"],
        satisfaction_criterion=data["satisfaction_criterion"],
        required_evidence_kinds=list(data.get("required_evidence_kinds", [])),
        trust_threshold=str(data.get("trust_threshold", DEFAULT_TRUST_TIER)),
        negation_allowed=bool(data.get("negation_allowed", False)),
        condition_kind=str(data.get("condition_kind", "automated")),
    )


def merge_clause_parse_results(
    results: list[ClauseParseResult],
) -> ClauseParseResult:
    """Merge multiple :class:`ClauseParseResult` objects into one.

    De-duplicates clauses by ``clause_id`` (first occurrence wins), combines
    all errors and warnings, and builds a merged metadata dict.

    Parameters
    ----------
    results : list of ClauseParseResult
        Parse results to merge.

    Returns
    -------
    ClauseParseResult
        A single merged parse result.

    Examples
    --------
    >>> r1 = parse_clause_from_string("req.auth :: X : T")
    >>> r2 = parse_clause_from_string("req.data :: Y : T")
    >>> merged = merge_clause_parse_results([r1, r2])
    >>> len(merged.parsed_clauses)
    2
    """
    if not results:
        return ClauseParseResult(
            parse_id=f"parse-{uuid.uuid4().hex[:8]}",
            raw_input="",
            parsed_clauses=[],
            parse_errors=[],
            parse_warnings=["merge_clause_parse_results called with empty list"],
            parse_metadata={"version": CLAUSE_PARSE_VERSION},
            parse_timestamp=_now_iso(),
        )

    seen_ids: set[str] = set()
    merged_clauses: list[TargetSectionClause] = []
    merged_errors: list[str] = []
    merged_warnings: list[str] = []
    raw_inputs: list[str] = []

    for result in results:
        raw_inputs.append(result.raw_input)
        merged_errors.extend(result.parse_errors)
        merged_warnings.extend(result.parse_warnings)
        for clause in result.parsed_clauses:
            if clause.clause_id not in seen_ids:
                seen_ids.add(clause.clause_id)
                merged_clauses.append(clause)

    total_lines = sum(
        r.parse_metadata.get("line_count", 0) for r in results
    )
    return ClauseParseResult(
        parse_id=f"parse-{uuid.uuid4().hex[:8]}",
        raw_input="\n---\n".join(raw_inputs),
        parsed_clauses=merged_clauses,
        parse_errors=merged_errors,
        parse_warnings=merged_warnings,
        parse_metadata={
            "version": CLAUSE_PARSE_VERSION,
            "merged_from": len(results),
            "line_count": total_lines,
        },
        parse_timestamp=_now_iso(),
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
    "DEFAULT_TRUST_TIER",
    "VERIFIED_TRUST_TIER",
    "PROPOSAL_TRUST_TIER",
    "JUDGMENT_COMPONENTS",
    "MAX_CLAUSES_PER_SECTION",
    "CLAUSE_PARSE_VERSION",
    # Enumerations
    "ClauseKind",
    "WitnessStatusKind",
    # Dataclasses
    "TargetSectionClause",
    "TruthCondition",
    "ClauseParseResult",
    "TargetSection",
    "SpecificationsTargetGeometryClausesWitness",
    # Main classes
    "SpecificationsTargetGeometryClausesCoordinator",
    "SpecificationsTargetGeometryClausesAnalyzer",
    # Module-level functions
    "parse_clause_from_string",
    "build_target_section",
    "restrict_section_to_patch",
    "compose_target_sections",
    "validate_target_section",
    "clause_from_dict",
    "truth_condition_from_dict",
    "merge_clause_parse_results",
    # Internal helpers (exported for testing)
    "_now_iso",
    "_stable_hash",
    "_make_clause_id",
    "_make_section_id",
    "_make_condition_id",
    "_canonical_coordinate",
    "_clause_overlap",
    "_judgment_fields_compatible",
    "_compute_coverage_fraction",
    "_format_truth_condition_summary",
    # Unified architecture cross-references
    "spec_descent",
    "spec_certificate",
    "spec_encoding",
]

# copilot: specifications_as_target_geometry — target geometry and clauses for theory2.tex §10.X

if __name__ == "__main__":
    import sys

    print("─" * 60)
    print("specifications_as_target_geometry smoke test")
    print("─" * 60)

    # ------------------------------------------------------------------
    # 1. Internal helpers
    # ------------------------------------------------------------------
    print("\n[1] Internal helpers")

    ts = _now_iso()
    assert "T" in ts or "+" in ts, f"_now_iso format unexpected: {ts}"
    print(f"  _now_iso()           → {ts}")

    h = _stable_hash("hello world")
    assert len(h) == 64, f"Expected 64-char hex, got {len(h)}"
    assert h == _stable_hash("hello world"), "_stable_hash not stable"
    print(f"  _stable_hash(...)    → {h[:16]}...")

    cid = _make_clause_id("req.auth", "AuthModule : Module")
    assert cid.startswith("clause-"), f"Bad clause id: {cid}"
    print(f"  _make_clause_id(...) → {cid}")

    tc_id = _make_condition_id(cid, "all tests pass")
    assert tc_id.startswith("cond-"), f"Bad cond id: {tc_id}"
    print(f"  _make_condition_id   → {tc_id}")

    canon = _canonical_coordinate("  REQ.Auth  ")
    assert canon == "req.auth", f"Expected 'req.auth', got {canon!r}"
    print(f"  _canonical_coord     → {canon!r}")

    assert _judgment_fields_compatible({"phi": "X"}, {"T": "VERIFIED"})
    assert not _judgment_fields_compatible({"phi": "X"}, {"phi": "Y"})
    print("  _judgment_fields_compatible: OK")

    assert _compute_coverage_fraction([], []) == 1.0
    assert abs(_compute_coverage_fraction(["a", "b"], ["a", "b", "c"]) - 2 / 3) < 1e-9
    print("  _compute_coverage_fraction: OK")

    # ------------------------------------------------------------------
    # 2. ClauseKind enum
    # ------------------------------------------------------------------
    print("\n[2] ClauseKind enum")
    for kind in ClauseKind:
        assert isinstance(kind.value, str), f"Bad kind value: {kind}"
    print(f"  Kinds: {[k.value for k in ClauseKind]}")

    # ------------------------------------------------------------------
    # 3. TargetSectionClause
    # ------------------------------------------------------------------
    print("\n[3] TargetSectionClause")
    clause_auth = TargetSectionClause(
        clause_id=_make_clause_id("req.auth", "AuthModule : Module"),
        coordinate="req.auth",
        formula="AuthModule : Module",
        prescribed_judgment_fields={"phi": "AuthModule : Module", "T": "TRUSTED"},
        restriction_map={},
        clause_kind=ClauseKind.STRUCTURAL,
        priority=10,
        created_at=_now_iso(),
    )
    assert clause_auth.to_dict()["coordinate"] == "req.auth"
    print(f"  Created clause: {clause_auth.clause_id}")

    clause_data = TargetSectionClause(
        clause_id=_make_clause_id("req.data", "DataModule : Module"),
        coordinate="req.data",
        formula="DataModule : Module",
        prescribed_judgment_fields={"phi": "DataModule : Module", "T": "UNVERIFIED"},
        restriction_map={},
        clause_kind=ClauseKind.BEHAVIORAL,
        priority=5,
        created_at=_now_iso(),
    )

    # Test restrict_to
    restricted_clause = clause_auth.restrict_to("req.auth.login")
    assert restricted_clause.coordinate == "req.auth.login"
    assert "req.auth" in restricted_clause.restriction_map  # key = parent coord
    print(f"  Restricted clause: {restricted_clause.clause_id}")

    # Test restrict_to raises on bad sub-coordinate
    try:
        clause_auth.restrict_to("req.other")
        assert False, "Should have raised ValueError"
    except ValueError:
        print("  restrict_to raises ValueError for non-sub-coordinate: OK")

    # ------------------------------------------------------------------
    # 4. TruthCondition
    # ------------------------------------------------------------------
    print("\n[4] TruthCondition")
    tc_auth = TruthCondition(
        condition_id=_make_condition_id(clause_auth.clause_id, "auth tests pass"),
        clause_id=clause_auth.clause_id,
        satisfaction_criterion="All auth module unit tests pass at ≥95% coverage",
        required_evidence_kinds=["unit_test", "code_review"],
        trust_threshold="TRUSTED",
        negation_allowed=False,
        condition_kind="automated",
    )
    assert tc_auth.trust_tier_rank() == 2, f"Expected rank 2, got {tc_auth.trust_tier_rank()}"
    summary = tc_auth.summary()
    assert "TRUSTED" in summary
    print(f"  TruthCondition summary: {summary[:60]}...")

    # ------------------------------------------------------------------
    # 5. parse_clause_from_string
    # ------------------------------------------------------------------
    print("\n[5] parse_clause_from_string")
    raw_input = """
# Authentication requirements
req.auth :: AuthModule : Module :: STRUCTURAL :: priority=10
req.data :: DataModule : Module :: BEHAVIORAL :: priority=5
# API requirements
req.api :: ApiModule : Module :: SEMANTIC :: priority=7
"""
    parse_result = parse_clause_from_string(raw_input)
    assert parse_result.success, f"Parse failed: {parse_result.parse_errors}"
    assert len(parse_result.parsed_clauses) == 3
    assert parse_result.parse_metadata["comment_count"] == 2
    print(
        f"  Parsed {len(parse_result.parsed_clauses)} clauses, "
        f"{parse_result.parse_metadata['comment_count']} comments skipped"
    )

    # Test with coordinate override
    result_with_coord = parse_clause_from_string(
        "SomeFormula : Type", coordinate="req.override"
    )
    assert result_with_coord.parsed_clauses[0].coordinate == "req.override"
    print(f"  Coordinate override: OK")

    # Test error case
    bad_result = parse_clause_from_string("no separator here")
    assert len(bad_result.parse_errors) == 1
    print(f"  Parse error detection: OK")

    # ------------------------------------------------------------------
    # 6. build_target_section
    # ------------------------------------------------------------------
    print("\n[6] build_target_section")
    all_clauses = parse_result.parsed_clauses + [clause_auth, clause_data]
    # clause_auth has same coord as first parsed clause, different formula → conflict
    section = build_target_section(
        [clause_auth, clause_data], spec_id="spec-smoke-001"
    )
    assert section.spec_id == "spec-smoke-001"
    assert section.clause_count == 2
    assert "req.auth" in section.coordinates
    assert "req.data" in section.coordinates
    assert section.is_complete, "Non-conflicting clauses should yield complete section"
    print(f"  Built section {section.section_id}: {section.clause_count} clauses")

    # Check judgment_at
    jf = section.judgment_at("req.auth")
    assert jf is not None and jf.get("phi") == "AuthModule : Module"
    print(f"  judgment_at('req.auth'): {jf}")

    # ------------------------------------------------------------------
    # 7. validate_target_section
    # ------------------------------------------------------------------
    print("\n[7] validate_target_section")
    valid, issues = validate_target_section(section)
    assert valid, f"Valid section reported issues: {issues}"
    print(f"  Validation: valid={valid}, issues={issues}")

    # Manufacture a conflicting section
    clause_auth_conflict = TargetSectionClause(
        clause_id=_make_clause_id("req.auth", "OtherModule : Module"),
        coordinate="req.auth",
        formula="OtherModule : Module",
        prescribed_judgment_fields={"phi": "OtherModule : Module", "T": "UNVERIFIED"},
        restriction_map={},
        clause_kind=ClauseKind.STRUCTURAL,
        priority=1,
        created_at=_now_iso(),
    )
    conflict_section = build_target_section(
        [clause_auth, clause_auth_conflict], spec_id="spec-conflict"
    )
    valid2, issues2 = validate_target_section(conflict_section)
    assert not valid2, "Conflicting section should be invalid"
    assert len(issues2) > 0
    print(f"  Conflict detection: {issues2[0][:60]}...")

    # ------------------------------------------------------------------
    # 8. restrict_section_to_patch
    # ------------------------------------------------------------------
    print("\n[8] restrict_section_to_patch")
    big_section = build_target_section(
        parse_result.parsed_clauses, spec_id="spec-big"
    )
    restricted = restrict_section_to_patch(big_section, ["req.auth"])
    assert "req.auth" in restricted.coordinates
    assert "req.data" not in restricted.coordinates
    assert "req.api" not in restricted.coordinates
    print(
        f"  Restricted to 'req.auth': {len(restricted.coordinates)} coordinate(s)"
    )

    # ------------------------------------------------------------------
    # 9. compose_target_sections
    # ------------------------------------------------------------------
    print("\n[9] compose_target_sections")
    sec_auth = build_target_section([clause_auth], spec_id="spec-auth")
    sec_data = build_target_section([clause_data], spec_id="spec-auth")
    composed = compose_target_sections(sec_auth, sec_data)
    assert "req.auth" in composed.coordinates
    assert "req.data" in composed.coordinates
    assert composed.spec_id == "spec-auth"
    print(f"  Composed: {len(composed.coordinates)} coordinates, "
          f"is_complete={composed.is_complete}")

    # ------------------------------------------------------------------
    # 10. clause_from_dict / truth_condition_from_dict / to_dict round-trips
    # ------------------------------------------------------------------
    print("\n[10] Serialisation round-trips")
    clause_dict = clause_auth.to_dict()
    clause_rt = clause_from_dict(clause_dict)
    assert clause_rt.clause_id == clause_auth.clause_id
    assert clause_rt.formula == clause_auth.formula
    print(f"  clause_from_dict round-trip: OK")

    tc_dict = tc_auth.to_dict()
    tc_rt = truth_condition_from_dict(tc_dict)
    assert tc_rt.condition_id == tc_auth.condition_id
    assert tc_rt.trust_threshold == tc_auth.trust_threshold
    print(f"  truth_condition_from_dict round-trip: OK")

    section_dict = section.to_dict()
    assert "section_id" in section_dict
    assert "clause_index" in section_dict
    print(f"  TargetSection.to_dict: {list(section_dict.keys())}")

    # ------------------------------------------------------------------
    # 11. merge_clause_parse_results
    # ------------------------------------------------------------------
    print("\n[11] merge_clause_parse_results")
    r1 = parse_clause_from_string("req.auth :: X : T")
    r2 = parse_clause_from_string("req.data :: Y : T\nreq.api :: Z : T")
    merged = merge_clause_parse_results([r1, r2])
    assert len(merged.parsed_clauses) == 3, \
        f"Expected 3 merged clauses, got {len(merged.parsed_clauses)}"
    assert merged.parse_metadata["merged_from"] == 2
    print(f"  Merged {len(merged.parsed_clauses)} clauses from 2 results")

    # Empty merge
    empty_merge = merge_clause_parse_results([])
    assert empty_merge.parse_warnings, "Empty merge should have a warning"
    print(f"  Empty merge warning: {empty_merge.parse_warnings[0][:60]}...")

    # ------------------------------------------------------------------
    # 12. SpecificationsTargetGeometryClausesCoordinator
    # ------------------------------------------------------------------
    print("\n[12] SpecificationsTargetGeometryClausesCoordinator")
    coordinator = SpecificationsTargetGeometryClausesCoordinator(
        coordinator_id="coord-smoke-001",
        site_coordinates=[],
        target_section_map={},
        clause_registry={},
        truth_condition_registry={},
        parse_log=[],
        error_log=[],
    )

    c1 = coordinator.register_clause(
        coordinate="req.auth",
        formula="AuthModule : Module",
        prescribed_fields={"phi": "AuthModule : Module", "T": "TRUSTED"},
        clause_kind=ClauseKind.STRUCTURAL,
        priority=10,
    )
    c2 = coordinator.register_clause(
        coordinate="req.data",
        formula="DataModule : Module",
        prescribed_fields={"phi": "DataModule : Module"},
        clause_kind=ClauseKind.BEHAVIORAL,
        priority=5,
    )
    c3 = coordinator.register_clause(
        coordinate="req.api",
        formula="ApiModule : Module",
        prescribed_fields={"phi": "ApiModule : Module"},
        clause_kind=ClauseKind.SEMANTIC,
        priority=7,
    )

    tc1 = coordinator.register_truth_condition(
        clause_id=c1.clause_id,
        satisfaction_criterion="Auth tests pass at 95% coverage",
        required_evidence_kinds=["unit_test"],
        trust_threshold="TRUSTED",
    )
    tc2 = coordinator.register_truth_condition(
        clause_id=c2.clause_id,
        satisfaction_criterion="Data module integration tests green",
        required_evidence_kinds=["integration_test"],
        trust_threshold="PROPOSED",
    )

    # Errors for invalid operations
    try:
        coordinator.register_clause("", "X : T")
        assert False
    except ValueError:
        print("  Empty coordinate raises ValueError: OK")

    try:
        coordinator.register_truth_condition("nonexistent-id", "criterion")
        assert False
    except KeyError:
        print("  Unknown clause_id raises KeyError: OK")

    built_section = coordinator.build_target_section(spec_id="spec-coordinator")
    assert built_section.clause_count == 3
    assert len(built_section.truth_condition_index) == 2
    print(
        f"  Built section: {built_section.section_id}, "
        f"{built_section.clause_count} clauses, "
        f"{len(built_section.truth_condition_index)} truth conditions"
    )

    valid3, issues3 = coordinator.validate_target_section(built_section.section_id)
    assert valid3, f"Built section invalid: {issues3}"
    print(f"  Validate built section: valid={valid3}")

    restricted2 = coordinator.restrict_to_patch(
        built_section.section_id, ["req.auth", "req.data"]
    )
    assert len(restricted2.coordinates) == 2
    print(f"  restrict_to_patch: {restricted2.coordinates}")

    # Compose with another coordinator
    coord2 = SpecificationsTargetGeometryClausesCoordinator(
        coordinator_id="coord-smoke-002",
        site_coordinates=[],
        target_section_map={},
        clause_registry={},
        truth_condition_registry={},
        parse_log=[],
        error_log=[],
    )
    coord2.register_clause(
        coordinate="req.security",
        formula="SecurityModule : Module",
        prescribed_fields={"phi": "SecurityModule : Module"},
        priority=8,
    )
    coordinator.compose_with_coordinator(coord2)
    assert "req.security" in coordinator.site_coordinates
    print(f"  compose_with_coordinator: site now has "
          f"{len(coordinator.site_coordinates)} coordinates")

    summary = coordinator.get_parse_summary()
    assert summary["clause_count"] == 4  # 3 original + 1 from coord2
    print(f"  parse_summary: {summary['clause_count']} clauses, "
          f"{summary['error_count']} errors")

    # Test reset
    coordinator.reset()
    assert len(coordinator.clause_registry) == 0
    assert len(coordinator.parse_log) == 0
    print("  reset(): OK")

    # ------------------------------------------------------------------
    # 13. SpecificationsTargetGeometryClausesAnalyzer
    # ------------------------------------------------------------------
    print("\n[13] SpecificationsTargetGeometryClausesAnalyzer")
    analyzer = SpecificationsTargetGeometryClausesAnalyzer(
        analyzer_id="ana-smoke-001",
        analysis_results={},
        clause_coverage_map={},
        truth_gaps=[],
        coherence_violations=[],
        analysis_log=[],
    )

    # Re-build a clean coordinator for analysis
    coord3 = SpecificationsTargetGeometryClausesCoordinator(
        coordinator_id="coord-smoke-003",
        site_coordinates=[],
        target_section_map={},
        clause_registry={},
        truth_condition_registry={},
        parse_log=[],
        error_log=[],
    )
    ca = coord3.register_clause(
        "req.auth", "AuthModule : Module",
        {"phi": "AuthModule : Module", "T": "TRUSTED"},
        priority=10,
    )
    cd = coord3.register_clause(
        "req.data", "DataModule : Module",
        {"phi": "DataModule : Module"},
        priority=5,
    )
    coord3.register_truth_condition(
        ca.clause_id, "Auth tests pass", ["unit_test"], "TRUSTED"
    )
    coord3.register_truth_condition(
        cd.clause_id, "Data tests pass", ["integration_test"], "PROPOSED"
    )
    analysis_section = coord3.build_target_section(spec_id="spec-analysis")

    # analyze_clause
    clause_result = analyzer.analyze_clause(ca)
    assert clause_result["valid"], f"Clause analysis should be valid: {clause_result}"
    print(f"  analyze_clause: valid={clause_result['valid']}")

    # analyze with bad fields
    bad_clause = TargetSectionClause(
        clause_id="clause-bad00000",
        coordinate="req.bad",
        formula="Bad : Type",
        prescribed_judgment_fields={"NOTAFIELD": "value"},
        restriction_map={},
        clause_kind="UNKNOWN_KIND",
        priority=0,
        created_at=_now_iso(),
    )
    bad_result = analyzer.analyze_clause(bad_clause)
    assert not bad_result["valid"]
    assert len(bad_result["issues"]) >= 2
    print(f"  Bad clause issues: {bad_result['issues']}")

    # analyze_truth_condition
    tc_result = analyzer.analyze_truth_condition(tc_auth)
    assert tc_result["valid"]
    print(f"  analyze_truth_condition: valid={tc_result['valid']}")

    # analyze_target_section
    section_result = analyzer.analyze_target_section(analysis_section)
    assert section_result["clause_count"] == 2
    assert section_result["coherent"]
    assert section_result["coverage_fraction"] == 1.0
    print(
        f"  analyze_target_section: coverage={section_result['coverage_fraction']:.0%}, "
        f"coherent={section_result['coherent']}"
    )

    # find_truth_gaps
    all_tc_ids = list(analysis_section.truth_condition_index.keys())
    # Satisfy only the first condition
    if all_tc_ids:
        gaps = analyzer.find_truth_gaps(analysis_section, [all_tc_ids[0]])
        assert len(gaps) == len(all_tc_ids) - 1
        print(
            f"  find_truth_gaps: {len(gaps)} gap(s) "
            f"when 1/{len(all_tc_ids)} satisfied"
        )
    else:
        print("  find_truth_gaps: no truth conditions to gap (section has none)")

    # compute_coverage
    coverage_val = analyzer.compute_coverage(
        analysis_section, [ca.clause_id]
    )
    expected = 1 / 2
    assert abs(coverage_val - expected) < 1e-9, \
        f"Expected {expected}, got {coverage_val}"
    print(f"  compute_coverage: {coverage_val:.1%}")

    # produce_analysis_report
    report = analyzer.produce_analysis_report()
    assert report["analyzer_id"] == "ana-smoke-001"
    assert "clause_coverage_summary" in report
    print(
        f"  analysis_report: {report['total_analyses']} analyses, "
        f"coverage={report['clause_coverage_summary']['fraction']:.1%}"
    )

    # ------------------------------------------------------------------
    # 14. SpecificationsTargetGeometryClausesWitness
    # ------------------------------------------------------------------
    print("\n[14] SpecificationsTargetGeometryClausesWitness")
    witness = SpecificationsTargetGeometryClausesWitness(
        witness_id=f"witness-{uuid.uuid4().hex[:8]}",
        spec_id="spec-analysis",
        clauses_satisfied=[ca.clause_id],
        clauses_unsatisfied=[cd.clause_id],
        truth_conditions_met=[tc_auth.condition_id],
        truth_conditions_failed=[],
        coverage_fraction=0.5,
        global_section_exists=False,
        witness_status=WitnessStatusKind.PARTIALLY_SATISFIED,
        trust_tier=PROPOSAL_TRUST_TIER,
        provenance={
            "generator": "smoke_test",
            "method": "direct_construction",
            "timestamp": _now_iso(),
        },
        timestamp=_now_iso(),
    )
    assert not witness.is_fully_satisfied()
    assert witness.trust_tier == PROPOSAL_TRUST_TIER

    # Full satisfaction
    full_witness = SpecificationsTargetGeometryClausesWitness(
        witness_id=f"witness-{uuid.uuid4().hex[:8]}",
        spec_id="spec-analysis",
        clauses_satisfied=[ca.clause_id, cd.clause_id],
        clauses_unsatisfied=[],
        truth_conditions_met=[tc_auth.condition_id],
        truth_conditions_failed=[],
        coverage_fraction=1.0,
        global_section_exists=True,
        witness_status=WitnessStatusKind.SATISFIED,
        trust_tier=VERIFIED_TRUST_TIER,
        provenance={"generator": "smoke_test"},
        timestamp=_now_iso(),
    )
    assert full_witness.is_fully_satisfied()
    witness_dict = full_witness.to_dict()
    assert witness_dict["global_section_exists"] is True
    print(
        f"  Witness (partial): fully_satisfied={witness.is_fully_satisfied()}, "
        f"tier={witness.trust_tier}"
    )
    print(
        f"  Witness (full): fully_satisfied={full_witness.is_fully_satisfied()}, "
        f"tier={full_witness.trust_tier}"
    )

    # ------------------------------------------------------------------
    # 15. _clause_overlap edge cases
    # ------------------------------------------------------------------
    print("\n[15] _clause_overlap edge cases")
    c_parent = TargetSectionClause(
        _make_clause_id("req", "X"), "req", "X", {}, {}, "STRUCTURAL", 0, _now_iso()
    )
    c_child = TargetSectionClause(
        _make_clause_id("req.auth", "Y"), "req.auth", "Y", {}, {}, "STRUCTURAL", 0,
        _now_iso()
    )
    c_sibling = TargetSectionClause(
        _make_clause_id("req.data", "Z"), "req.data", "Z", {}, {}, "STRUCTURAL", 0,
        _now_iso()
    )
    assert _clause_overlap(c_parent, c_child), "Parent should overlap child"
    assert not _clause_overlap(c_child, c_sibling), "Siblings should not overlap"
    print("  Parent-child overlap: OK")
    print("  Sibling non-overlap: OK")

    # ------------------------------------------------------------------
    # 16. _format_truth_condition_summary
    # ------------------------------------------------------------------
    print("\n[16] _format_truth_condition_summary")
    formatted = _format_truth_condition_summary(tc_auth)
    assert "TRUSTED" in formatted
    assert tc_auth.condition_id in formatted
    print(f"  Summary: {formatted[:70]}...")

    # ------------------------------------------------------------------
    # 17. JSON serialisation of full section
    # ------------------------------------------------------------------
    print("\n[17] JSON serialisation of TargetSection")
    analysis_section_dict = analysis_section.to_dict()
    json_str = json.dumps(analysis_section_dict, indent=2)
    roundtrip = json.loads(json_str)
    assert roundtrip["section_id"] == analysis_section.section_id
    assert len(roundtrip["clause_index"]) == analysis_section.clause_count
    print(f"  JSON round-trip: {len(json_str)} bytes, OK")

    print("\n" + "─" * 60)
    print("All smoke tests passed.")
    print("─" * 60)
    sys.exit(0)


# ---------------------------------------------------------------------------
# Private helper used only in smoke test
# ---------------------------------------------------------------------------

def pytest_approx_equiv(x: float) -> float:
    """Return *x* unchanged — allows approximate comparison in smoke test.

    Parameters
    ----------
    x : float
        The expected value.

    Returns
    -------
    float
        The value unchanged (comparison is exact in the smoke test).
    """
    return x
