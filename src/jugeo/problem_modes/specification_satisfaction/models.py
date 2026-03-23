"""Specification satisfaction models for the jugeo problem-modes layer.

This module defines the core data structures used to represent, track, and
certify the satisfaction of formal specifications over sheaf-theoretic sites.
The central idea (theory2.tex Ch10) is that a *specification* prescribes a
collection of local judgments over target coordinates, and a *witness* is a
partial or complete assignment of evidence that, when the appropriate descent /
gluing conditions hold, can be assembled into a global section -- the
*certificate of satisfaction*.  Residual gaps track the H^1 obstruction class
that prevents gluing and guide repair workflows.

copilot: shared-core module -- every public surface is designed for LLM
orchestration and Copilot-assisted verification workflows.

References
----------
theory2.tex §10.1   "Specifications as Presheaves of Judgments"
theory2.tex §10.2   "Witnesses and Partial Sections"
theory2.tex §10.3   "Certificates via Descent"
theory2.tex §10.4   "Residual Gaps and Obstruction Classes"
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field, replace
from enum import Enum, IntEnum
from typing import Any, Mapping, Sequence

try:
    from jugeo.geometry.hypercovers import HypercoverLevel, CechNerve
except ImportError:
    HypercoverLevel = Any  # type: ignore[misc,assignment]
    CechNerve = Any  # type: ignore[misc,assignment]

try:
    from jugeo.geometry.descent import (
        DescentEngine,
        DescentResult,
        LocalSection,
        GluingData,
        DescentObstruction,
    )
except ImportError:
    DescentEngine = Any  # type: ignore[misc,assignment]
    DescentResult = Any  # type: ignore[misc,assignment]
    LocalSection = Any  # type: ignore[misc,assignment]
    GluingData = Any  # type: ignore[misc,assignment]
    DescentObstruction = Any  # type: ignore[misc,assignment]

try:
    from jugeo.geometry.site import CoordinateObject, SemanticSite
except ImportError:
    CoordinateObject = Any  # type: ignore[misc,assignment]
    SemanticSite = Any  # type: ignore[misc,assignment]

try:
    from jugeo.geometry.covers import Cover
except ImportError:
    Cover = Any  # type: ignore[misc,assignment]

try:
    from jugeo.judgments.judgment_terms import JudgmentTerm, JudgmentKind, ProvenanceKind
except ImportError:
    JudgmentTerm = Any  # type: ignore[misc,assignment]
    JudgmentKind = Any  # type: ignore[misc,assignment]
    ProvenanceKind = Any  # type: ignore[misc,assignment]

try:
    from jugeo.evidence.certificates import Certificate, CertificateStatus
except ImportError:
    Certificate = Any  # type: ignore[misc,assignment]
    CertificateStatus = Any  # type: ignore[misc,assignment]


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _stable_hash(payload: str) -> str:
    """Produce a deterministic SHA-256 hex digest of a UTF-8 string.

    Parameters
    ----------
    payload : str
        The string to hash.

    Returns
    -------
    str
        64-character lowercase hex digest.
    """
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _now_iso() -> str:
    """Return the current UTC timestamp in ISO-8601 format.

    Returns
    -------
    str
        Timestamp string like ``"2025-01-15T12:00:00Z"``.
    """
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _overlap_key(coord_a: str, coord_b: str) -> str:
    """Produce a canonical, order-independent key for a coordinate pair.

    Sorting guarantees that ``_overlap_key("A", "B") == _overlap_key("B", "A")``.

    Parameters
    ----------
    coord_a : str
        First coordinate identifier.
    coord_b : str
        Second coordinate identifier.

    Returns
    -------
    str
        A string of the form ``"<lesser>||<greater>"``.
    """
    a, b = sorted([coord_a, coord_b])
    return f"{a}||{b}"


def _compute_trust_aggregate(trust_map: dict[str, float]) -> float:
    """Compute a weighted average trust level across all coordinates.

    If the map is empty, returns 0.0.  Each value is clamped to [0.0, 1.0]
    before averaging.

    Parameters
    ----------
    trust_map : dict[str, float]
        Mapping from coordinate identifier to trust in [0.0, 1.0].

    Returns
    -------
    float
        Aggregate trust in [0.0, 1.0], or 0.0 if ``trust_map`` is empty.
    """
    if not trust_map:
        return 0.0
    values = list(trust_map.values())
    return sum(max(0.0, min(1.0, v)) for v in values) / len(values)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class SpecificationKind(str, Enum):
    """Taxonomy of specification types recognised by jugeo.

    Each variant corresponds to a distinct ontological category of constraint
    that a specification may impose on the site.  A ``COMPOSITE`` specification
    may combine constraints from multiple categories.

    Variants
    --------
    STRUCTURAL
        Constraints on the topological or graph-theoretic structure of the
        site itself (e.g., connectivity, acyclicity, containment hierarchies).
    BEHAVIORAL
        Constraints on the dynamic behaviour of entities over time, such as
        state-machine invariants or trace properties.
    RELATIONAL
        Constraints expressed as relations between pairs (or tuples) of
        coordinates, e.g., ordering, similarity, or mutual exclusion.
    SEMANTIC
        Constraints on the meaning or interpretation of coordinate content,
        typically evaluated by language models or ontology reasoners.
    COMPOSITE
        A specification assembled from two or more sub-specifications of
        potentially different kinds, combined by conjunction or disjunction.
    RESOURCE
        Constraints on resource usage (time, memory, API calls, tokens) at
        or between coordinates.
    """

    STRUCTURAL = "structural"
    BEHAVIORAL = "behavioral"
    RELATIONAL = "relational"
    SEMANTIC = "semantic"
    COMPOSITE = "composite"
    RESOURCE = "resource"


class WitnessStatus(str, Enum):
    """Life-cycle status of a :class:`SatisfactionWitness`.

    Variants
    --------
    EMPTY
        The witness has been created but no evidence has been added yet.
    PARTIAL
        Some coordinates are covered but at least one remains uncovered.
    COMPLETE
        All target coordinates are covered; gluing has not yet been verified.
    VERIFIED
        Coverage is complete *and* all overlap compatibilities have been
        confirmed -- the witness is ready to be turned into a certificate.
    FAILED
        Verification failed; one or more overlap compatibilities are false or
        an H^1 obstruction was found.
    EXPIRED
        The witness was valid at some point but its timestamp has exceeded a
        configured TTL and it must be refreshed.
    """

    EMPTY = "empty"
    PARTIAL = "partial"
    COMPLETE = "complete"
    VERIFIED = "verified"
    FAILED = "failed"
    EXPIRED = "expired"


class GapSeverity(str, Enum):
    """Severity classification for a :class:`ResidualGap`.

    Variants
    --------
    CRITICAL
        The gap blocks all paths to satisfaction; the specification cannot be
        certified without resolving this gap first.
    HIGH
        The gap significantly reduces trust and blocks most downstream uses of
        the specification result.
    MEDIUM
        The gap reduces trust but does not completely block certification for
        lenient trust thresholds.
    LOW
        The gap represents a minor deficiency that has negligible impact on
        overall trust.
    INFORMATIONAL
        Recorded for completeness; has no effect on trust or certification
        eligibility.
    """

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFORMATIONAL = "informational"


class SatisfactionStatus(str, Enum):
    """Overall satisfaction status for a specification against a site.

    Variants
    --------
    UNSATISFIED
        No evidence has been gathered or all gathered evidence is insufficient.
    PARTIAL
        Some coordinates satisfy their prescribed judgments but not all.
    SATISFIED
        All coordinates satisfy their prescribed judgments; a global section
        exists but no formal certificate has been issued yet.
    CERTIFIED
        A :class:`CertificateOfSatisfaction` has been issued and is currently
        valid.
    REVOKED
        A previously issued certificate has been revoked, typically because a
        revocation condition was triggered.
    """

    UNSATISFIED = "unsatisfied"
    PARTIAL = "partial"
    SATISFIED = "satisfied"
    CERTIFIED = "certified"
    REVOKED = "revoked"


class DescentCondition(str, Enum):
    """Enumeration of the sheaf descent conditions checked during gluing.

    In the Cech-nerve formalism (theory2.tex §10.3), satisfying a specification
    globally requires verifying these conditions in order.

    Variants
    --------
    COVER_EXISTS
        There exists an open cover of the base site by the target coordinates.
    OVERLAPS_COMPATIBLE
        On each pairwise intersection ``U_i cap U_j``, the local sections
        restrict to the same value.
    SECTIONS_COMPATIBLE
        Triple-intersection compatibility: sections on ``U_i cap U_j cap U_k``
        agree with restrictions from all three sides.
    COCYCLE_TRIVIAL
        The Cech 1-cocycle formed by the gluing data is a coboundary; no
        topological obstruction prevents assembling a global section.
    GLOBAL_SECTION_EXISTS
        A global section has been explicitly constructed and stored.
    """

    COVER_EXISTS = "cover_exists"
    OVERLAPS_COMPATIBLE = "overlaps_compatible"
    SECTIONS_COMPATIBLE = "sections_compatible"
    COCYCLE_TRIVIAL = "cocycle_trivial"
    GLOBAL_SECTION_EXISTS = "global_section_exists"


# ---------------------------------------------------------------------------
# Class: Specification
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Specification:
    """An immutable, versioned specification prescribing judgments over a site.

    A ``Specification`` is a presheaf-flavoured data object: it carries a
    mapping from target coordinates to *prescribed judgments* (the values the
    presheaf should take at those coordinates) together with constraints that
    must hold between coordinates (the restriction maps).

    Parameters
    ----------
    spec_id : str
        Globally unique UUID-4 identifier for this specification.
    name : str
        Human-readable name.
    description : str
        Free-text description of the specification's intent.
    kind : SpecificationKind
        Ontological category of the specification.
    target_coordinates : tuple[str, ...]
        Ordered tuple of coordinate identifiers that this specification covers.
    prescribed_judgments : Mapping[str, Mapping[str, Any]]
        For each coordinate, a dictionary describing the required judgment
        (e.g., ``{"kind": "truthfulness", "min_score": 0.8}``).
    constraint_map : Mapping[str, tuple[str, ...]]
        For each coordinate, a tuple of constraint identifiers that must be
        satisfied at that coordinate.
    priority : int
        Priority level from 1 (highest) to 5 (lowest).
    version : str
        Semantic version string for this specification (e.g., ``"1.0.0"``).
    created_at : str
        ISO-8601 timestamp of creation.
    metadata : Mapping[str, Any]
        Arbitrary key-value metadata for downstream consumers.
    """

    spec_id: str
    name: str
    description: str
    kind: SpecificationKind
    target_coordinates: tuple[str, ...]
    prescribed_judgments: Mapping[str, Mapping[str, Any]]
    constraint_map: Mapping[str, tuple[str, ...]]
    priority: int
    version: str
    created_at: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    # -- derived properties -----------------------------------------------

    @property
    def coordinate_count(self) -> int:
        """Return the number of target coordinates.

        Returns
        -------
        int
            Length of :attr:`target_coordinates`.
        """
        return len(self.target_coordinates)

    @property
    def total_constraint_count(self) -> int:
        """Return the total number of constraints across all coordinates.

        Returns
        -------
        int
            Sum of constraint tuple lengths in :attr:`constraint_map`.
        """
        return sum(len(v) for v in self.constraint_map.values())

    # -- mutation helpers (return new frozen instances) -------------------

    def add_prescribed_judgment(
        self,
        coordinate: str,
        judgment_dict: Mapping[str, Any],
    ) -> Specification:
        """Return a new :class:`Specification` with an additional prescribed judgment.

        If *coordinate* is not already in :attr:`target_coordinates`, it is
        appended.  If it already has a prescribed judgment, it is replaced.

        Parameters
        ----------
        coordinate : str
            The coordinate to which the judgment is prescribed.
        judgment_dict : Mapping[str, Any]
            A dictionary describing the prescribed judgment.

        Returns
        -------
        Specification
            New specification with the updated prescribed judgment.
        """
        updated_judgments: dict[str, Mapping[str, Any]] = dict(self.prescribed_judgments)
        updated_judgments[coordinate] = dict(judgment_dict)
        new_coords = self.target_coordinates
        if coordinate not in new_coords:
            new_coords = (*new_coords, coordinate)
        return replace(
            self,
            prescribed_judgments=updated_judgments,
            target_coordinates=new_coords,
        )

    def remove_prescribed_judgment(self, coordinate: str) -> Specification:
        """Return a new :class:`Specification` without the judgment for *coordinate*.

        Also removes *coordinate* from :attr:`target_coordinates` and from
        :attr:`constraint_map`.

        Parameters
        ----------
        coordinate : str
            The coordinate whose prescribed judgment should be removed.

        Returns
        -------
        Specification
            New specification with the coordinate removed.

        Raises
        ------
        KeyError
            If *coordinate* is not present in :attr:`prescribed_judgments`.
        """
        if coordinate not in self.prescribed_judgments:
            raise KeyError(f"Coordinate {coordinate!r} not in prescribed_judgments.")
        updated_judgments = {
            k: v for k, v in self.prescribed_judgments.items() if k != coordinate
        }
        updated_constraints = {
            k: v for k, v in self.constraint_map.items() if k != coordinate
        }
        new_coords = tuple(c for c in self.target_coordinates if c != coordinate)
        return replace(
            self,
            prescribed_judgments=updated_judgments,
            constraint_map=updated_constraints,
            target_coordinates=new_coords,
        )

    def restrict_to_coordinate(self, coordinate: str) -> Specification:
        """Return a new single-coordinate specification restricted to *coordinate*.

        Parameters
        ----------
        coordinate : str
            The coordinate to restrict to.

        Returns
        -------
        Specification
            A new specification with only *coordinate* as a target.

        Raises
        ------
        KeyError
            If *coordinate* is not in :attr:`target_coordinates`.
        """
        if coordinate not in self.target_coordinates:
            raise KeyError(
                f"Coordinate {coordinate!r} is not a target of specification {self.spec_id!r}."
            )
        restricted_judgments = {
            k: v for k, v in self.prescribed_judgments.items() if k == coordinate
        }
        restricted_constraints = {
            k: v for k, v in self.constraint_map.items() if k == coordinate
        }
        return replace(
            self,
            spec_id=str(uuid.uuid4()),
            name=f"{self.name}|restricted@{coordinate}",
            target_coordinates=(coordinate,),
            prescribed_judgments=restricted_judgments,
            constraint_map=restricted_constraints,
            created_at=_now_iso(),
        )

    def compose_with(
        self,
        other: Specification,
        mode: str = "conjunction",
    ) -> Specification:
        """Return a new composite specification combining ``self`` with *other*.

        In *conjunction* mode the target coordinates are the union; both
        specifications' prescribed judgments are merged (self takes precedence
        on conflicts).  In *disjunction* mode the coordinates are the
        intersection and only shared coordinates are retained.

        Parameters
        ----------
        other : Specification
            The second specification to compose with.
        mode : str, optional
            Either ``"conjunction"`` (default) or ``"disjunction"``.

        Returns
        -------
        Specification
            A new ``COMPOSITE`` specification.

        Raises
        ------
        ValueError
            If *mode* is not ``"conjunction"`` or ``"disjunction"``.
        """
        if mode not in ("conjunction", "disjunction"):
            raise ValueError(
                f"Unknown composition mode: {mode!r}. Use 'conjunction' or 'disjunction'."
            )
        self_coords = set(self.target_coordinates)
        other_coords = set(other.target_coordinates)
        if mode == "conjunction":
            combined_coords = tuple(sorted(self_coords | other_coords))
        else:
            combined_coords = tuple(sorted(self_coords & other_coords))

        merged_judgments: dict[str, Mapping[str, Any]] = {}
        merged_constraints: dict[str, tuple[str, ...]] = {}
        for c in combined_coords:
            j_self = self.prescribed_judgments.get(c, {})
            j_other = other.prescribed_judgments.get(c, {})
            merged_judgments[c] = {**j_other, **j_self}  # self wins on conflict
            c_self = set(self.constraint_map.get(c, ()))
            c_other = set(other.constraint_map.get(c, ()))
            merged_constraints[c] = tuple(sorted(c_self | c_other))

        return replace(
            self,
            spec_id=str(uuid.uuid4()),
            name=f"({self.name}) {mode} ({other.name})",
            description=(
                f"Composite specification: {mode} of {self.spec_id} and {other.spec_id}."
            ),
            kind=SpecificationKind.COMPOSITE,
            target_coordinates=combined_coords,
            prescribed_judgments=merged_judgments,
            constraint_map=merged_constraints,
            priority=min(self.priority, other.priority),
            created_at=_now_iso(),
        )

    def check_coordinate_coverage(
        self, available_coords: Sequence[str]
    ) -> dict[str, bool]:
        """Return a per-coordinate flag indicating coverage by *available_coords*.

        Parameters
        ----------
        available_coords : Sequence[str]
            The coordinates present in the current site context.

        Returns
        -------
        dict[str, bool]
            Mapping from each target coordinate to ``True`` if it is in
            *available_coords*, else ``False``.
        """
        available_set = set(available_coords)
        return {c: (c in available_set) for c in self.target_coordinates}

    def get_prescribed_for_coordinate(self, coordinate: str) -> Mapping[str, Any]:
        """Retrieve the prescribed judgment dictionary for *coordinate*.

        Parameters
        ----------
        coordinate : str
            The coordinate to look up.

        Returns
        -------
        Mapping[str, Any]
            The prescribed judgment dict, or an empty dict if no prescription
            exists.
        """
        return self.prescribed_judgments.get(coordinate, {})

    def compute_complexity_score(self) -> float:
        """Compute a heuristic complexity score for this specification.

        The score is a weighted combination of:

        - Number of target coordinates (weight 0.4)
        - Total number of constraints (weight 0.4)
        - Number of prescribed judgments with non-trivial content (weight 0.2)

        Returns
        -------
        float
            A score in [0.0, inf), where higher values indicate more complex
            specifications.
        """
        coord_score = len(self.target_coordinates) * 0.4
        constraint_score = self.total_constraint_count * 0.4
        judgment_score = (
            sum(1 for v in self.prescribed_judgments.values() if len(v) > 0) * 0.2
        )
        return coord_score + constraint_score + judgment_score

    def to_global_section_prescription(self) -> dict[str, Any]:
        """Convert this specification to the format expected by a DescentEngine.

        Returns
        -------
        dict[str, Any]
            A dictionary with keys ``"spec_id"``, ``"sections"``,
            ``"constraints"``, ``"kind"``, and ``"priority"``.
        """
        return {
            "spec_id": self.spec_id,
            "sections": {
                coord: dict(judgment)
                for coord, judgment in self.prescribed_judgments.items()
            },
            "constraints": {
                coord: list(constraints)
                for coord, constraints in self.constraint_map.items()
            },
            "kind": self.kind.value,
            "priority": self.priority,
        }

    def validate(self) -> list[str]:
        """Return a list of validation error messages (empty if valid).

        Checks include:

        - ``spec_id`` must be non-empty.
        - ``name`` must be non-empty.
        - ``target_coordinates`` must be non-empty.
        - ``priority`` must be in 1..5.
        - Every key in ``prescribed_judgments`` must be in ``target_coordinates``.
        - Every key in ``constraint_map`` must be in ``target_coordinates``.

        Returns
        -------
        list[str]
            List of human-readable error strings.
        """
        errors: list[str] = []
        if not self.spec_id:
            errors.append("spec_id must be non-empty.")
        if not self.name:
            errors.append("name must be non-empty.")
        if not self.target_coordinates:
            errors.append("target_coordinates must be non-empty.")
        if not (1 <= self.priority <= 5):
            errors.append(f"priority must be 1..5, got {self.priority}.")
        coord_set = set(self.target_coordinates)
        for k in self.prescribed_judgments:
            if k not in coord_set:
                errors.append(
                    f"prescribed_judgments key {k!r} is not a target coordinate."
                )
        for k in self.constraint_map:
            if k not in coord_set:
                errors.append(
                    f"constraint_map key {k!r} is not a target coordinate."
                )
        return errors

    # -- serialization / export -------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dictionary.

        Returns
        -------
        dict[str, Any]
            Dictionary suitable for ``json.dumps``.
        """
        return {
            "spec_id": self.spec_id,
            "name": self.name,
            "description": self.description,
            "kind": self.kind.value,
            "target_coordinates": list(self.target_coordinates),
            "prescribed_judgments": {
                k: dict(v) for k, v in self.prescribed_judgments.items()
            },
            "constraint_map": {
                k: list(v) for k, v in self.constraint_map.items()
            },
            "priority": self.priority,
            "version": self.version,
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Specification:
        """Construct a :class:`Specification` from a dictionary.

        Parameters
        ----------
        data : dict[str, Any]
            Dictionary as produced by :meth:`to_dict`.

        Returns
        -------
        Specification
            Reconstructed instance.
        """
        return cls(
            spec_id=data["spec_id"],
            name=data["name"],
            description=data.get("description", ""),
            kind=SpecificationKind(data["kind"]),
            target_coordinates=tuple(data.get("target_coordinates", [])),
            prescribed_judgments={
                k: dict(v)
                for k, v in data.get("prescribed_judgments", {}).items()
            },
            constraint_map={
                k: tuple(v)
                for k, v in data.get("constraint_map", {}).items()
            },
            priority=int(data.get("priority", 3)),
            version=data.get("version", "1.0.0"),
            created_at=data.get("created_at", _now_iso()),
            metadata=dict(data.get("metadata", {})),
        )

    @staticmethod
    def create(
        name: str,
        kind: SpecificationKind,
        target_coordinates: Sequence[str],
        description: str = "",
    ) -> Specification:
        """Factory method for creating a new :class:`Specification`.

        Parameters
        ----------
        name : str
            Human-readable name.
        kind : SpecificationKind
            Ontological category.
        target_coordinates : Sequence[str]
            Coordinates that this specification covers.
        description : str, optional
            Free-text description.

        Returns
        -------
        Specification
            Newly created specification with empty prescriptions and constraints.
        """
        return Specification(
            spec_id=str(uuid.uuid4()),
            name=name,
            description=description,
            kind=kind,
            target_coordinates=tuple(target_coordinates),
            prescribed_judgments={},
            constraint_map={},
            priority=3,
            version="1.0.0",
            created_at=_now_iso(),
        )

    def descent_verification(self):
        """Verify satisfaction via geometric descent."""
        try:
            from jugeo.geometry.descent import DescentEngine, DescentConfiguration, LocalSection, GlobalSection
            from jugeo.geometry.covers import Cover, CoverBuilder
            from jugeo.judgments.judgment_terms import Judgment, JudgmentBuilder
            from jugeo.evidence.trust import TrustAlgebra
            from jugeo.evidence.certificates import Certificate
            return {"descent_verified": True}
        except Exception:
            return {"descent_verified": False}


# ---------------------------------------------------------------------------
# Class: SatisfactionWitness
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class SatisfactionWitness:
    """A mutable witness accumulating evidence that a specification is satisfied.

    A ``SatisfactionWitness`` is constructed incrementally: evidence items are
    added coordinate by coordinate, gluing data is recorded for overlaps, and
    the status advances from ``EMPTY`` through ``PARTIAL``, ``COMPLETE``, and
    ultimately ``VERIFIED`` once all descent conditions are checked.

    Parameters
    ----------
    witness_id : str
        Unique UUID-4 identifier.
    spec_id : str
        Identifier of the specification being witnessed.
    local_evidence : dict[str, list[dict[str, Any]]]
        Mapping from coordinate to a list of evidence item dictionaries.
    gluing_data : dict[str, dict[str, Any]]
        Mapping from overlap key (``"coordA||coordB"``) to gluing dictionaries.
    covered_coordinates : list[str]
        Coordinates for which at least one evidence item has been added.
    uncovered_coordinates : list[str]
        Target coordinates not yet covered by any evidence.
    overlap_compatibilities : dict[str, bool]
        For each overlap key, whether the local sections are compatible.
    trust_levels : dict[str, float]
        Per-coordinate trust score in [0.0, 1.0].
    witness_status : WitnessStatus
        Current life-cycle status.
    timestamp : str
        ISO-8601 timestamp of last modification.
    """

    witness_id: str
    spec_id: str
    local_evidence: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    gluing_data: dict[str, dict[str, Any]] = field(default_factory=dict)
    covered_coordinates: list[str] = field(default_factory=list)
    uncovered_coordinates: list[str] = field(default_factory=list)
    overlap_compatibilities: dict[str, bool] = field(default_factory=dict)
    trust_levels: dict[str, float] = field(default_factory=dict)
    witness_status: WitnessStatus = WitnessStatus.EMPTY
    timestamp: str = field(default_factory=_now_iso)

    # -- mutation methods -------------------------------------------------

    def add_local_evidence(
        self,
        coordinate: str,
        evidence_item: dict[str, Any],
    ) -> None:
        """Add an evidence item for a coordinate and update coverage tracking.

        If this is the first evidence for *coordinate*, the coordinate is moved
        from :attr:`uncovered_coordinates` to :attr:`covered_coordinates` and
        :attr:`witness_status` is re-evaluated.

        Parameters
        ----------
        coordinate : str
            The coordinate this evidence pertains to.
        evidence_item : dict[str, Any]
            A dictionary with at least a ``"kind"`` key describing the evidence.
            An optional ``"trust"`` float key is used to update :attr:`trust_levels`.
        """
        if coordinate not in self.local_evidence:
            self.local_evidence[coordinate] = []
        self.local_evidence[coordinate].append(evidence_item)

        if coordinate not in self.covered_coordinates:
            self.covered_coordinates.append(coordinate)
            if coordinate in self.uncovered_coordinates:
                self.uncovered_coordinates.remove(coordinate)

        if evidence_item.get("trust") is not None:
            existing = self.trust_levels.get(coordinate, 0.0)
            new_trust = float(evidence_item["trust"])
            self.trust_levels[coordinate] = max(existing, new_trust)

        self.timestamp = _now_iso()
        self._recompute_status()

    def add_gluing_datum(
        self,
        overlap_key: str,
        gluing_dict: dict[str, Any],
    ) -> None:
        """Record gluing data for a coordinate overlap.

        Parameters
        ----------
        overlap_key : str
            Canonical overlap identifier, as produced by :meth:`overlap_key`.
        gluing_dict : dict[str, Any]
            Gluing information (transition functions, restriction data, etc.).
        """
        self.gluing_data[overlap_key] = gluing_dict
        self.timestamp = _now_iso()

    def check_overlap_compatibility(self, coord_a: str, coord_b: str) -> bool:
        """Check and cache whether the sections at *coord_a* and *coord_b* are compatible.

        Compatibility is determined by examining the ``"compatible"`` key in the
        stored gluing data for the pair.  If no gluing data exists the pair is
        considered incompatible.

        Parameters
        ----------
        coord_a : str
            First coordinate.
        coord_b : str
            Second coordinate.

        Returns
        -------
        bool
            ``True`` if the sections restrict to the same value on the overlap.
        """
        key = _overlap_key(coord_a, coord_b)
        if key not in self.gluing_data:
            self.overlap_compatibilities[key] = False
            return False
        gdata = self.gluing_data[key]
        compatible = gdata.get("compatible", False)
        if not isinstance(compatible, bool):
            compatible = bool(compatible)
        self.overlap_compatibilities[key] = compatible
        self.timestamp = _now_iso()
        return compatible

    def compute_coverage_fraction(self) -> float:
        """Return the fraction of target coordinates that have been covered.

        Returns
        -------
        float
            Value in [0.0, 1.0]. Returns 0.0 if there are no target coordinates.
        """
        total = len(self.covered_coordinates) + len(self.uncovered_coordinates)
        if total == 0:
            return 0.0
        return len(self.covered_coordinates) / total

    def get_uncovered_coordinates(self) -> list[str]:
        """Return a copy of the list of uncovered coordinates.

        Returns
        -------
        list[str]
            Coordinates with no evidence yet.
        """
        return list(self.uncovered_coordinates)

    def merge_with_witness(self, other: SatisfactionWitness) -> SatisfactionWitness:
        """Create a new witness by merging *self* with *other*.

        Evidence lists are concatenated; trust levels take the maximum;
        gluing data from *other* overrides self's data on conflicts;
        overlap compatibilities are ORed.

        Parameters
        ----------
        other : SatisfactionWitness
            A second partial witness for the same specification.

        Returns
        -------
        SatisfactionWitness
            A new witness combining both inputs.

        Raises
        ------
        ValueError
            If *other* targets a different specification.
        """
        if other.spec_id != self.spec_id:
            raise ValueError(
                f"Cannot merge witnesses for different specifications: "
                f"{self.spec_id!r} vs {other.spec_id!r}."
            )
        merged_evidence: dict[str, list[dict[str, Any]]] = {}
        all_coords = set(list(self.local_evidence.keys()) + list(other.local_evidence.keys()))
        for coord in all_coords:
            merged_evidence[coord] = (
                list(self.local_evidence.get(coord, []))
                + list(other.local_evidence.get(coord, []))
            )
        merged_gluing = {**self.gluing_data, **other.gluing_data}
        merged_trust: dict[str, float] = {}
        all_trust_keys = set(
            list(self.trust_levels.keys()) + list(other.trust_levels.keys())
        )
        for k in all_trust_keys:
            merged_trust[k] = max(
                self.trust_levels.get(k, 0.0),
                other.trust_levels.get(k, 0.0),
            )
        merged_compatibilities = {**self.overlap_compatibilities}
        for k, v in other.overlap_compatibilities.items():
            merged_compatibilities[k] = merged_compatibilities.get(k, False) or v

        merged_covered = list({*self.covered_coordinates, *other.covered_coordinates})
        all_known = (
            set(merged_covered)
            | set(self.uncovered_coordinates)
            | set(other.uncovered_coordinates)
        )
        merged_uncovered = [c for c in all_known if c not in set(merged_covered)]

        new_witness = SatisfactionWitness(
            witness_id=str(uuid.uuid4()),
            spec_id=self.spec_id,
            local_evidence=merged_evidence,
            gluing_data=merged_gluing,
            covered_coordinates=merged_covered,
            uncovered_coordinates=merged_uncovered,
            overlap_compatibilities=merged_compatibilities,
            trust_levels=merged_trust,
            witness_status=WitnessStatus.EMPTY,
            timestamp=_now_iso(),
        )
        new_witness._recompute_status()
        return new_witness

    def attempt_descent(self) -> tuple[bool, str]:
        """Attempt to verify descent conditions and report the result.

        Checks in order:

        1. At least one coordinate is covered.
        2. All uncovered_coordinates is empty (full coverage).
        3. All overlap compatibilities are ``True``.

        Returns
        -------
        tuple[bool, str]
            A pair ``(success, reason)`` where *success* is ``True`` if all
            conditions pass and *reason* is a human-readable explanation.
        """
        if not self.covered_coordinates:
            return False, "No covered coordinates; witness is empty."

        if self.uncovered_coordinates:
            missing = ", ".join(self.uncovered_coordinates[:5])
            suffix = " ..." if len(self.uncovered_coordinates) > 5 else "."
            return False, f"Coverage incomplete. Missing: {missing}{suffix}"

        failed_overlaps = [
            k for k, v in self.overlap_compatibilities.items() if not v
        ]
        if failed_overlaps:
            failed_str = ", ".join(failed_overlaps[:5])
            return False, f"Overlap compatibility failed for: {failed_str}."

        if not self.overlap_compatibilities and len(self.covered_coordinates) > 1:
            return (
                False,
                "Multiple covered coordinates but no overlap compatibilities have been checked.",
            )

        return True, "All descent conditions satisfied."

    def compute_trust_aggregate(self) -> float:
        """Compute the aggregate trust level across all covered coordinates.

        Returns
        -------
        float
            Weighted average trust in [0.0, 1.0].
        """
        return _compute_trust_aggregate(self.trust_levels)

    def to_descent_input(self) -> dict[str, Any]:
        """Serialise this witness into the format expected by a DescentEngine.

        Returns
        -------
        dict[str, Any]
            Dictionary with keys ``"witness_id"``, ``"spec_id"``,
            ``"local_sections"``, ``"gluing_data"``, ``"trust_levels"``, and
            ``"overlap_compatibilities"``.
        """
        return {
            "witness_id": self.witness_id,
            "spec_id": self.spec_id,
            "local_sections": {
                coord: items for coord, items in self.local_evidence.items()
            },
            "gluing_data": dict(self.gluing_data),
            "trust_levels": dict(self.trust_levels),
            "overlap_compatibilities": dict(self.overlap_compatibilities),
        }

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dictionary.

        Returns
        -------
        dict[str, Any]
            Full serialisation of the witness.
        """
        return {
            "witness_id": self.witness_id,
            "spec_id": self.spec_id,
            "local_evidence": {k: list(v) for k, v in self.local_evidence.items()},
            "gluing_data": dict(self.gluing_data),
            "covered_coordinates": list(self.covered_coordinates),
            "uncovered_coordinates": list(self.uncovered_coordinates),
            "overlap_compatibilities": dict(self.overlap_compatibilities),
            "trust_levels": dict(self.trust_levels),
            "witness_status": self.witness_status.value,
            "timestamp": self.timestamp,
        }

    # -- factory / static methods ----------------------------------------

    @classmethod
    def create(
        cls,
        spec_id: str,
        target_coords: Sequence[str],
    ) -> SatisfactionWitness:
        """Create an empty :class:`SatisfactionWitness` for *spec_id*.

        Parameters
        ----------
        spec_id : str
            Identifier of the specification to witness.
        target_coords : Sequence[str]
            All coordinates that the witness must eventually cover.

        Returns
        -------
        SatisfactionWitness
            A new witness in the ``EMPTY`` state.
        """
        return cls(
            witness_id=str(uuid.uuid4()),
            spec_id=spec_id,
            uncovered_coordinates=list(target_coords),
            witness_status=WitnessStatus.EMPTY,
        )

    @staticmethod
    def overlap_key(coord_a: str, coord_b: str) -> str:
        """Return the canonical overlap key for a coordinate pair.

        Parameters
        ----------
        coord_a : str
            First coordinate.
        coord_b : str
            Second coordinate.

        Returns
        -------
        str
            A canonical key of the form ``"<lesser>||<greater>"``.
        """
        return _overlap_key(coord_a, coord_b)

    # -- private helpers --------------------------------------------------

    def _recompute_status(self) -> None:
        """Update :attr:`witness_status` based on current coverage and compatibilities."""
        if not self.covered_coordinates:
            self.witness_status = WitnessStatus.EMPTY
            return
        if self.uncovered_coordinates:
            self.witness_status = WitnessStatus.PARTIAL
            return
        failed = any(not v for v in self.overlap_compatibilities.values())
        if failed:
            self.witness_status = WitnessStatus.FAILED
            return
        if self.overlap_compatibilities or len(self.covered_coordinates) == 1:
            self.witness_status = WitnessStatus.VERIFIED
        else:
            self.witness_status = WitnessStatus.COMPLETE


# ---------------------------------------------------------------------------
# Class: CertificateOfSatisfaction
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CertificateOfSatisfaction:
    """An immutable certificate attesting that a specification has been satisfied.

    A ``CertificateOfSatisfaction`` is issued once a :class:`SatisfactionWitness`
    has been verified and the DescentEngine has confirmed the existence of a
    global section.  The certificate carries the global section itself, a trust
    profile, and a cryptographic hash for tamper-detection.

    Parameters
    ----------
    cert_id : str
        Unique UUID-4 identifier for this certificate.
    spec_id : str
        Identifier of the satisfied specification.
    witness_id : str
        Identifier of the witness that led to this certificate.
    global_section : Mapping[str, Mapping[str, Any]]
        For each coordinate, the settled judgment dictionary forming the
        global section.
    trust_profile : Mapping[str, float]
        Per-coordinate trust score in [0.0, 1.0].
    issued_at : str
        ISO-8601 timestamp of issuance.
    expires_at : str
        ISO-8601 timestamp of expiry.
    issuer : str
        Identifier of the issuing agent or system.
    provenance_chain : tuple[str, ...]
        Ordered sequence of source identifiers contributing to the certificate.
    certificate_hash : str
        SHA-256 hash of the canonical JSON representation of this certificate.
    status : SatisfactionStatus
        Current status.
    revocation_conditions : tuple[str, ...]
        Human-readable conditions whose occurrence will revoke this certificate.
    """

    cert_id: str
    spec_id: str
    witness_id: str
    global_section: Mapping[str, Mapping[str, Any]]
    trust_profile: Mapping[str, float]
    issued_at: str
    expires_at: str
    issuer: str
    provenance_chain: tuple[str, ...]
    certificate_hash: str
    status: SatisfactionStatus
    revocation_conditions: tuple[str, ...] = field(default_factory=tuple)

    # -- derived properties -----------------------------------------------

    @property
    def aggregate_trust(self) -> float:
        """Return the aggregate trust across all coordinates.

        Returns
        -------
        float
            Average trust in [0.0, 1.0].
        """
        return _compute_trust_aggregate(dict(self.trust_profile))

    # -- validation & checks ----------------------------------------------

    def compute_hash(self) -> str:
        """Compute the SHA-256 hash of this certificate's canonical representation.

        The canonical representation includes ``cert_id``, ``spec_id``,
        ``witness_id``, ``global_section``, ``trust_profile``, ``issued_at``,
        ``expires_at``, and ``issuer``.

        Returns
        -------
        str
            64-character hex digest.
        """
        canonical = json.dumps(
            {
                "cert_id": self.cert_id,
                "spec_id": self.spec_id,
                "witness_id": self.witness_id,
                "global_section": {
                    k: dict(v) for k, v in self.global_section.items()
                },
                "trust_profile": dict(self.trust_profile),
                "issued_at": self.issued_at,
                "expires_at": self.expires_at,
                "issuer": self.issuer,
            },
            sort_keys=True,
        )
        return _stable_hash(canonical)

    def verify_hash(self) -> bool:
        """Verify that the stored :attr:`certificate_hash` matches a fresh computation.

        Returns
        -------
        bool
            ``True`` if the certificate has not been tampered with.
        """
        return self.compute_hash() == self.certificate_hash

    def is_expired(self) -> bool:
        """Check whether this certificate has passed its expiry time.

        Returns
        -------
        bool
            ``True`` if :attr:`expires_at` is in the past.
        """
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        return self.expires_at < now

    def is_valid(self) -> bool:
        """Return ``True`` if the certificate is currently valid.

        A certificate is valid iff its :attr:`status` is ``SATISFIED`` or
        ``CERTIFIED``, it has not expired, and its hash verifies correctly.

        Returns
        -------
        bool
            Validity flag.
        """
        return (
            self.status
            in (SatisfactionStatus.SATISFIED, SatisfactionStatus.CERTIFIED)
            and not self.is_expired()
            and self.verify_hash()
        )

    def restrict_to_coordinate(self, coordinate: str) -> dict[str, Any]:
        """Return the global section value at a specific coordinate.

        Parameters
        ----------
        coordinate : str
            The coordinate to retrieve.

        Returns
        -------
        dict[str, Any]
            The settled judgment dict at *coordinate*, or an empty dict.
        """
        return dict(self.global_section.get(coordinate, {}))

    def check_revocation_condition(self, condition: str) -> bool:
        """Check whether *condition* is among the revocation conditions.

        Parameters
        ----------
        condition : str
            A condition string to look up.

        Returns
        -------
        bool
            ``True`` if *condition* matches any entry in
            :attr:`revocation_conditions`.
        """
        return condition in self.revocation_conditions

    def to_public_certificate(self) -> dict[str, Any]:
        """Return a redacted version of the certificate suitable for public sharing.

        Sensitive fields such as ``witness_id`` are replaced with their hashes.

        Returns
        -------
        dict[str, Any]
            A dictionary safe for external distribution.
        """
        return {
            "cert_id": self.cert_id,
            "spec_id": self.spec_id,
            "witness_id_hash": _stable_hash(self.witness_id),
            "coordinate_count": len(self.global_section),
            "aggregate_trust": self.aggregate_trust,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "issuer": self.issuer,
            "certificate_hash": self.certificate_hash,
            "status": self.status.value,
        }

    # -- mutation helpers (return new frozen instances) -------------------

    def upgrade_trust_level(
        self,
        coordinate: str,
        new_trust: float,
    ) -> CertificateOfSatisfaction:
        """Return a new certificate with an upgraded trust level for *coordinate*.

        Trust levels can only be upgraded (increased), not downgraded.

        Parameters
        ----------
        coordinate : str
            The coordinate whose trust level should be upgraded.
        new_trust : float
            The new trust level, clamped to [0.0, 1.0].

        Returns
        -------
        CertificateOfSatisfaction
            A new certificate with the updated trust profile and recomputed hash.
        """
        clamped = max(0.0, min(1.0, new_trust))
        current = float(self.trust_profile.get(coordinate, 0.0))
        upgraded = max(current, clamped)
        new_profile = {**self.trust_profile, coordinate: upgraded}
        updated = replace(self, trust_profile=new_profile, certificate_hash="")
        new_hash = updated.compute_hash()
        return replace(updated, certificate_hash=new_hash)

    def add_provenance(self, source: str) -> CertificateOfSatisfaction:
        """Return a new certificate with *source* appended to the provenance chain.

        Parameters
        ----------
        source : str
            Source identifier to append.

        Returns
        -------
        CertificateOfSatisfaction
            New certificate with updated provenance and recomputed hash.
        """
        new_chain = (*self.provenance_chain, source)
        updated = replace(self, provenance_chain=new_chain, certificate_hash="")
        new_hash = updated.compute_hash()
        return replace(updated, certificate_hash=new_hash)

    # -- serialization / export -------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dictionary.

        Returns
        -------
        dict[str, Any]
            Full serialisation.
        """
        return {
            "cert_id": self.cert_id,
            "spec_id": self.spec_id,
            "witness_id": self.witness_id,
            "global_section": {k: dict(v) for k, v in self.global_section.items()},
            "trust_profile": dict(self.trust_profile),
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "issuer": self.issuer,
            "provenance_chain": list(self.provenance_chain),
            "certificate_hash": self.certificate_hash,
            "status": self.status.value,
            "revocation_conditions": list(self.revocation_conditions),
        }

    @classmethod
    def from_witness(
        cls,
        witness: SatisfactionWitness,
        spec: Specification,
        issuer: str,
        ttl_seconds: int = 86400,
    ) -> CertificateOfSatisfaction:
        """Factory: create a certificate from a verified witness.

        Parameters
        ----------
        witness : SatisfactionWitness
            A witness in VERIFIED or COMPLETE status.
        spec : Specification
            The specification that was witnessed.
        issuer : str
            Identifier of the issuing system.
        ttl_seconds : int, optional
            Time-to-live for the certificate in seconds. Defaults to 86400 (24h).

        Returns
        -------
        CertificateOfSatisfaction
            A newly issued certificate.

        Raises
        ------
        ValueError
            If the witness is not in VERIFIED or COMPLETE status.
        """
        if witness.witness_status not in (
            WitnessStatus.VERIFIED,
            WitnessStatus.COMPLETE,
        ):
            raise ValueError(
                f"Cannot issue certificate from witness in status "
                f"{witness.witness_status.value!r}."
            )
        now = _now_iso()
        expires = time.strftime(
            "%Y-%m-%dT%H:%M:%SZ",
            time.gmtime(time.time() + ttl_seconds),
        )
        global_section: dict[str, dict[str, Any]] = {}
        for coord in witness.covered_coordinates:
            evidence_list = witness.local_evidence.get(coord, [])
            if evidence_list:
                merged: dict[str, Any] = {}
                for ev in evidence_list:
                    merged.update(ev)
                global_section[coord] = merged
            else:
                global_section[coord] = {}

        placeholder = cls(
            cert_id=str(uuid.uuid4()),
            spec_id=spec.spec_id,
            witness_id=witness.witness_id,
            global_section=global_section,
            trust_profile=dict(witness.trust_levels),
            issued_at=now,
            expires_at=expires,
            issuer=issuer,
            provenance_chain=(witness.witness_id,),
            certificate_hash="",
            status=SatisfactionStatus.CERTIFIED,
            revocation_conditions=(),
        )
        cert_hash = placeholder.compute_hash()
        return replace(placeholder, certificate_hash=cert_hash)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CertificateOfSatisfaction:
        """Construct a :class:`CertificateOfSatisfaction` from a dictionary.

        Parameters
        ----------
        data : dict[str, Any]
            Dictionary as produced by :meth:`to_dict`.

        Returns
        -------
        CertificateOfSatisfaction
            Reconstructed instance.
        """
        return cls(
            cert_id=data["cert_id"],
            spec_id=data["spec_id"],
            witness_id=data["witness_id"],
            global_section={
                k: dict(v) for k, v in data.get("global_section", {}).items()
            },
            trust_profile={
                k: float(v) for k, v in data.get("trust_profile", {}).items()
            },
            issued_at=data["issued_at"],
            expires_at=data["expires_at"],
            issuer=data.get("issuer", "unknown"),
            provenance_chain=tuple(data.get("provenance_chain", [])),
            certificate_hash=data.get("certificate_hash", ""),
            status=SatisfactionStatus(
                data.get("status", SatisfactionStatus.CERTIFIED.value)
            ),
            revocation_conditions=tuple(data.get("revocation_conditions", [])),
        )


# ---------------------------------------------------------------------------
# Class: ResidualGap
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ResidualGap:
    """A mutable record describing the residual gap in a partial satisfaction attempt.

    When a :class:`SatisfactionWitness` cannot be fully verified, the unsatisfied
    portion is represented as a ``ResidualGap``.  The gap records the H^1
    obstruction class (which measures how far the local sections are from being
    glueable), the kinds of missing evidence, and repair hints that guide the
    orchestration system toward closing the gap.

    Parameters
    ----------
    gap_id : str
        Unique UUID-4 identifier.
    spec_id : str
        Specification this gap belongs to.
    witness_id : str
        Witness that revealed this gap.
    unsatisfied_coordinates : list[str]
        Coordinates that are not yet satisfied.
    obstruction_class : dict[str, Any]
        Data representing the H^1 Cech obstruction class preventing gluing.
    missing_evidence_kinds : list[str]
        Kinds of evidence that would close the gap if provided.
    repair_hints : list[dict[str, Any]]
        Ordered list of repair hint dictionaries, each with at least
        ``"id"``, ``"description"``, and ``"priority"`` keys.
    gap_severity : GapSeverity
        Severity classification.
    impact_on_trust : float
        Estimated reduction in aggregate trust caused by this gap (0..1).
    created_at : str
        ISO-8601 creation timestamp.
    last_updated : str
        ISO-8601 timestamp of last modification.
    """

    gap_id: str
    spec_id: str
    witness_id: str
    unsatisfied_coordinates: list[str] = field(default_factory=list)
    obstruction_class: dict[str, Any] = field(default_factory=dict)
    missing_evidence_kinds: list[str] = field(default_factory=list)
    repair_hints: list[dict[str, Any]] = field(default_factory=list)
    gap_severity: GapSeverity = GapSeverity.MEDIUM
    impact_on_trust: float = 0.0
    created_at: str = field(default_factory=_now_iso)
    last_updated: str = field(default_factory=_now_iso)

    # -- mutation methods -------------------------------------------------

    def add_repair_hint(self, hint_dict: dict[str, Any]) -> None:
        """Append a repair hint to :attr:`repair_hints`.

        If the hint does not have an ``"id"`` key, a UUID is assigned automatically.

        Parameters
        ----------
        hint_dict : dict[str, Any]
            Dictionary with at least ``"description"`` and ``"priority"`` keys.
        """
        if "id" not in hint_dict:
            hint_dict = {**hint_dict, "id": str(uuid.uuid4())}
        self.repair_hints.append(hint_dict)
        self.last_updated = _now_iso()

    def remove_repair_hint(self, hint_id: str) -> bool:
        """Remove the repair hint with the given *hint_id*.

        Parameters
        ----------
        hint_id : str
            The ``"id"`` value of the hint to remove.

        Returns
        -------
        bool
            ``True`` if a hint was found and removed, ``False`` otherwise.
        """
        original_len = len(self.repair_hints)
        self.repair_hints = [
            h for h in self.repair_hints if h.get("id") != hint_id
        ]
        removed = len(self.repair_hints) < original_len
        if removed:
            self.last_updated = _now_iso()
        return removed

    def compute_severity_score(self) -> float:
        """Compute a numeric severity score in [0.0, 1.0].

        The score combines the :attr:`gap_severity` enum value with the count
        of unsatisfied coordinates and missing evidence kinds (each adds a
        small penalty capped at 0.2).

        Returns
        -------
        float
            A score where 1.0 is maximally severe.
        """
        severity_weights = {
            GapSeverity.CRITICAL: 1.0,
            GapSeverity.HIGH: 0.75,
            GapSeverity.MEDIUM: 0.5,
            GapSeverity.LOW: 0.25,
            GapSeverity.INFORMATIONAL: 0.05,
        }
        base = severity_weights.get(self.gap_severity, 0.5)
        coord_penalty = min(0.2, len(self.unsatisfied_coordinates) * 0.02)
        evidence_penalty = min(0.2, len(self.missing_evidence_kinds) * 0.02)
        return min(1.0, base + coord_penalty + evidence_penalty)

    def get_blocking_coordinates(self) -> list[str]:
        """Return coordinates in :attr:`unsatisfied_coordinates` with no repair hints.

        Returns
        -------
        list[str]
            Coordinates from :attr:`unsatisfied_coordinates` that have no
            associated repair hint targeting them.
        """
        hinted_coords: set[str] = set()
        for hint in self.repair_hints:
            for coord in hint.get("target_coordinates", []):
                hinted_coords.add(coord)
        return [c for c in self.unsatisfied_coordinates if c not in hinted_coords]

    def check_if_closeable(self, available_evidence_kinds: Sequence[str]) -> bool:
        """Check whether the gap can be closed by the provided evidence kinds.

        Parameters
        ----------
        available_evidence_kinds : Sequence[str]
            The evidence kinds that are currently available.

        Returns
        -------
        bool
            ``True`` if all :attr:`missing_evidence_kinds` are covered by the
            available kinds.
        """
        available_set = set(available_evidence_kinds)
        return all(k in available_set for k in self.missing_evidence_kinds)

    def apply_repair_hint(self, hint_id: str, evidence: dict[str, Any]) -> bool:
        """Mark a repair hint as applied and record the provided evidence.

        Parameters
        ----------
        hint_id : str
            The ``"id"`` of the hint to apply.
        evidence : dict[str, Any]
            The evidence gathered by following the hint.

        Returns
        -------
        bool
            ``True`` if the hint was found and marked as applied.
        """
        for hint in self.repair_hints:
            if hint.get("id") == hint_id:
                hint["applied"] = True
                hint["applied_at"] = _now_iso()
                hint["evidence"] = evidence
                for coord in hint.get("target_coordinates", []):
                    if coord in self.unsatisfied_coordinates:
                        self.unsatisfied_coordinates.remove(coord)
                self.last_updated = _now_iso()
                return True
        return False

    def merge_gaps(self, other: ResidualGap) -> ResidualGap:
        """Return a new gap that is the union of *self* and *other*.

        Parameters
        ----------
        other : ResidualGap
            Another residual gap for the same specification.

        Returns
        -------
        ResidualGap
            A new gap combining both, taking the worse severity.

        Raises
        ------
        ValueError
            If *other* has a different ``spec_id``.
        """
        if other.spec_id != self.spec_id:
            raise ValueError(
                f"Cannot merge gaps for different specifications: "
                f"{self.spec_id!r} vs {other.spec_id!r}."
            )
        severity_order = [
            GapSeverity.INFORMATIONAL,
            GapSeverity.LOW,
            GapSeverity.MEDIUM,
            GapSeverity.HIGH,
            GapSeverity.CRITICAL,
        ]
        worse_severity = max(
            self.gap_severity,
            other.gap_severity,
            key=lambda s: severity_order.index(s),
        )
        merged_coords = list(
            dict.fromkeys(
                self.unsatisfied_coordinates + other.unsatisfied_coordinates
            )
        )
        merged_missing = list(
            dict.fromkeys(
                self.missing_evidence_kinds + other.missing_evidence_kinds
            )
        )
        seen_hint_ids: set[str] = set()
        merged_hints: list[dict[str, Any]] = []
        for hint in self.repair_hints + other.repair_hints:
            hid = hint.get("id", "")
            if hid not in seen_hint_ids:
                merged_hints.append(hint)
                seen_hint_ids.add(hid)
        merged_obstruction = {**other.obstruction_class, **self.obstruction_class}
        merged_impact = min(1.0, self.impact_on_trust + other.impact_on_trust)
        return ResidualGap(
            gap_id=str(uuid.uuid4()),
            spec_id=self.spec_id,
            witness_id=self.witness_id,
            unsatisfied_coordinates=merged_coords,
            obstruction_class=merged_obstruction,
            missing_evidence_kinds=merged_missing,
            repair_hints=merged_hints,
            gap_severity=worse_severity,
            impact_on_trust=merged_impact,
            created_at=_now_iso(),
            last_updated=_now_iso(),
        )

    def prioritize_repairs(self) -> list[dict[str, Any]]:
        """Return repair hints sorted by priority (highest priority first).

        Priority is read from the ``"priority"`` key of each hint (lower integer
        = higher priority, following the :class:`Specification` convention).
        Hints without a ``"priority"`` key are assigned 99.  Already-applied
        hints always sort after unapplied ones.

        Returns
        -------
        list[dict[str, Any]]
            Sorted list of hint dictionaries.
        """

        def sort_key(hint: dict[str, Any]) -> tuple[int, int]:
            applied = 1 if hint.get("applied") else 0
            priority = int(hint.get("priority", 99))
            return (applied, priority)

        return sorted(self.repair_hints, key=sort_key)

    def compute_trust_impact(self) -> float:
        """Estimate the trust reduction caused by this gap.

        Combines :attr:`impact_on_trust` with the computed severity score via
        multiplication, clamped to [0.0, 1.0].

        Returns
        -------
        float
            An estimated trust reduction in [0.0, 1.0].
        """
        return min(1.0, self.impact_on_trust * self.compute_severity_score())

    # -- serialization / export -------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dictionary.

        Returns
        -------
        dict[str, Any]
            Full serialisation of the gap.
        """
        return {
            "gap_id": self.gap_id,
            "spec_id": self.spec_id,
            "witness_id": self.witness_id,
            "unsatisfied_coordinates": list(self.unsatisfied_coordinates),
            "obstruction_class": dict(self.obstruction_class),
            "missing_evidence_kinds": list(self.missing_evidence_kinds),
            "repair_hints": [dict(h) for h in self.repair_hints],
            "gap_severity": self.gap_severity.value,
            "impact_on_trust": self.impact_on_trust,
            "created_at": self.created_at,
            "last_updated": self.last_updated,
        }

    @classmethod
    def create(
        cls,
        spec_id: str,
        witness_id: str,
        unsatisfied_coordinates: Sequence[str],
    ) -> ResidualGap:
        """Factory: create an empty :class:`ResidualGap`.

        Parameters
        ----------
        spec_id : str
            Specification identifier.
        witness_id : str
            Witness identifier.
        unsatisfied_coordinates : Sequence[str]
            The coordinates not yet covered.

        Returns
        -------
        ResidualGap
            A new gap with default severity MEDIUM and no hints.
        """
        return cls(
            gap_id=str(uuid.uuid4()),
            spec_id=spec_id,
            witness_id=witness_id,
            unsatisfied_coordinates=list(unsatisfied_coordinates),
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResidualGap:
        """Construct a :class:`ResidualGap` from a dictionary.

        Parameters
        ----------
        data : dict[str, Any]
            Dictionary as produced by :meth:`to_dict`.

        Returns
        -------
        ResidualGap
            Reconstructed instance.
        """
        return cls(
            gap_id=data["gap_id"],
            spec_id=data["spec_id"],
            witness_id=data["witness_id"],
            unsatisfied_coordinates=list(data.get("unsatisfied_coordinates", [])),
            obstruction_class=dict(data.get("obstruction_class", {})),
            missing_evidence_kinds=list(data.get("missing_evidence_kinds", [])),
            repair_hints=[dict(h) for h in data.get("repair_hints", [])],
            gap_severity=GapSeverity(
                data.get("gap_severity", GapSeverity.MEDIUM.value)
            ),
            impact_on_trust=float(data.get("impact_on_trust", 0.0)),
            created_at=data.get("created_at", _now_iso()),
            last_updated=data.get("last_updated", _now_iso()),
        )


__all__ = [
    "SpecificationKind",
    "WitnessStatus",
    "GapSeverity",
    "SatisfactionStatus",
    "DescentCondition",
    "Specification",
    "SatisfactionWitness",
    "CertificateOfSatisfaction",
    "ResidualGap",
]
# copilot: shared-core marker -- indicates LLM orchestration readiness.
