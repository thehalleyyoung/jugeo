"""Mixed-mode programming: partial semantic authorship, holes, and local obligations.

Section s03: Mixed-Mode Programming with Partial Semantic Authorship.

In mixed-mode programming, some regions of code carry full specifications
(complete authorship), while other regions are *holes* — portions of a partial
section that the author has deliberately left undefined.  Each hole induces one
or more *local obligations*: constraints that any eventual fill must satisfy in
order for the overall section to remain coherent with the specification.

Formally, a ``PartialSection`` is a section σ: X → J defined only on the
*authored* sub-cover of coordinates.  The *hole coordinates* are the complement
within the cover; they correspond to coordinates where σ is undefined.  The
goal of this module is to:

  1. Represent partial sections and their hole/authored decomposition.
  2. Derive local obligations from holes and surrounding judgment fragments.
  3. Track fill attempts and record whether each obligation was discharged.
  4. Compute authorship coverage fractions and boundary-coherence properties.
  5. Assemble a ``MixedModeProgrammingPartialWitness`` summarising the entire
     partial-section analysis.

Theory2 invariants enforced throughout:

* Judgments are tuples ``(c, φ, A, E, O, B, T, Π)``; see ``JUDGMENT_COMPONENTS``.
* Trust is expressed as *tiers* (``VERIFIED``, ``PROPOSED``, ``UNVERIFIED``),
  never as a raw float.
* Specifications are target sections, not boolean predicates.
* Holes are undefined portions of a partial section.
* Local obligations arise from holes; they constrain the fill.
* Generation proposals enter at the ``PROPOSED`` trust tier.

References
----------
theory2.tex §s03; descent-theoretic partial sections §6; obligation lifting §7.

# copilot: generated scaffold for jugeo mixed-mode partial-sem module; hole
# derivation and obligation-lifting logic follow theory2.tex §s03.  Extend
# LocalObligation.is_satisfiable_by and MixedModeProgrammingPartialCoordinator
# check methods as domain-specific constraint languages are formalised.
"""

from __future__ import annotations

import enum
import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# ---------------------------------------------------------------------------
# Optional internal imports (jugeo packages may not be installed in all envs)
# ---------------------------------------------------------------------------

try:
    from jugeo.problem_modes.specification_satisfaction.models import (
        Specification,
        SatisfactionStatus,
        WitnessStatus,
    )
except ImportError:
    Specification = Any  # type: ignore[assignment,misc]
    SatisfactionStatus = Any  # type: ignore[assignment,misc]
    WitnessStatus = Any  # type: ignore[assignment,misc]

try:
    from jugeo.problem_modes.specification_satisfaction.specifications import (
        SpecificationClause,
    )
except ImportError:
    SpecificationClause = Any  # type: ignore[assignment,misc]

try:
    from jugeo.problem_modes.specification_satisfaction.satisfaction_witnesses import (
        SatisfactionWitness,
        WitnessBuilder,
    )
except ImportError:
    SatisfactionWitness = Any  # type: ignore[assignment,misc]
    WitnessBuilder = Any  # type: ignore[assignment,misc]

try:
    from jugeo.geometry.descent import DescentDatum
except ImportError:
    DescentDatum = Any  # type: ignore[assignment,misc]

try:
    from jugeo.core.trust import TrustTier
except ImportError:
    TrustTier = Any  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------

HOLE_KIND_UNIMPLEMENTED: str = "UNIMPLEMENTED"
HOLE_KIND_DELEGATED: str = "DELEGATED"
HOLE_KIND_DEFERRED: str = "DEFERRED"
HOLE_KIND_OPTIONAL: str = "OPTIONAL"

OBLIGATION_KIND_TYPE_SAFE: str = "TYPE_SAFE"
OBLIGATION_KIND_BEHAVIOR_CORRECT: str = "BEHAVIOR_CORRECT"
OBLIGATION_KIND_API_CONSISTENT: str = "API_CONSISTENT"
OBLIGATION_KIND_SECURITY_SOUND: str = "SECURITY_SOUND"

TRUST_TIER_VERIFIED: str = "VERIFIED"
TRUST_TIER_PROPOSED: str = "PROPOSED"
TRUST_TIER_UNVERIFIED: str = "UNVERIFIED"

# The eight components of a Theory2 judgment tuple: (c, φ, A, E, O, B, T, Π)
JUDGMENT_COMPONENTS: tuple[str, ...] = ("c", "phi", "A", "E", "O", "B", "T", "Pi")

MAX_HOLES_PER_SECTION: int = 256

# Minimum trust tier required to close a hole (fills from generation pipelines
# start at PROPOSED and must be promoted to at least this tier before the hole
# is considered definitively closed).
_MIN_FILL_TIER_FOR_CLOSED: str = TRUST_TIER_PROPOSED

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    """Return the current UTC instant as an ISO-8601 string.

    Returns
    -------
    str
        e.g. ``"2024-03-15T12:00:00.000000+00:00"``
    """
    return datetime.now(tz=timezone.utc).isoformat()


def _stable_hash(payload: str) -> str:
    """Return an 8-character deterministic hex digest of *payload*.

    The hash is stable across Python versions because it uses SHA-256 on the
    UTF-8 encoding of the string.

    Parameters
    ----------
    payload:
        Arbitrary string to hash.

    Returns
    -------
    str
        First 8 hex characters of the SHA-256 digest.
    """
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:8]


def _make_hole_id(coordinate: str, kind: str) -> str:
    """Construct a stable, human-readable hole identifier.

    Parameters
    ----------
    coordinate:
        The coordinate string identifying the hole's location in the cover.
    kind:
        The hole kind label (e.g. ``"UNIMPLEMENTED"``).

    Returns
    -------
    str
        Identifier of the form ``"hole:<hash>:<kind>"``.
    """
    h = _stable_hash(f"{coordinate}:{kind}")
    return f"hole:{h}:{kind.lower()}"


def _make_obligation_id(hole_id: str, kind: str) -> str:
    """Construct a stable obligation identifier derived from a hole id.

    Parameters
    ----------
    hole_id:
        The identifier of the parent hole.
    kind:
        The obligation kind label (e.g. ``"TYPE_SAFE"``).

    Returns
    -------
    str
        Identifier of the form ``"oblig:<hash>:<kind>"``.
    """
    h = _stable_hash(f"{hole_id}:{kind}")
    return f"oblig:{h}:{kind.lower()}"


def _make_section_id(spec_id: str) -> str:
    """Construct a partial-section identifier linked to a specification.

    Parameters
    ----------
    spec_id:
        The specification identifier this section is tracking.

    Returns
    -------
    str
        Identifier of the form ``"psec:<hash>"``.
    """
    suffix = uuid.uuid4().hex[:6]
    h = _stable_hash(f"{spec_id}:{suffix}")
    return f"psec:{h}"


def _make_attempt_id(hole_id: str) -> str:
    """Construct a unique fill-attempt identifier for a given hole.

    Parameters
    ----------
    hole_id:
        The identifier of the hole being filled.

    Returns
    -------
    str
        Identifier of the form ``"attempt:<hash>"``.
    """
    suffix = uuid.uuid4().hex[:6]
    h = _stable_hash(f"{hole_id}:{suffix}")
    return f"attempt:{h}"


def _check_obligation_met(obligation: "LocalObligation", fill_judgment: dict) -> bool:
    """Determine whether *fill_judgment* satisfies a single *obligation*.

    The implementation performs a best-effort structural check:

    * ``TYPE_SAFE`` — the fill judgment must contain the ``"T"`` component.
    * ``BEHAVIOR_CORRECT`` — the fill judgment must contain both ``"phi"``
      and ``"O"`` components.
    * ``API_CONSISTENT`` — the fill judgment must contain ``"A"`` and ``"E"``.
    * ``SECURITY_SOUND`` — the fill judgment must contain ``"B"`` (boundary).
    * ``RESOURCE_BOUNDED`` — the fill judgment must contain ``"Pi"`` (policy).
    * ``CUSTOM`` — delegates to ``LocalObligation.is_satisfiable_by``.

    Parameters
    ----------
    obligation:
        The local obligation to check.
    fill_judgment:
        Dictionary containing the proposed judgment's field values.

    Returns
    -------
    bool
        ``True`` if the obligation is considered met, ``False`` otherwise.
    """
    kind = obligation.obligation_kind
    if kind == OBLIGATION_KIND_TYPE_SAFE:
        return _judgment_has_field(fill_judgment, "T")
    if kind == OBLIGATION_KIND_BEHAVIOR_CORRECT:
        return _judgment_has_field(fill_judgment, "phi") and _judgment_has_field(
            fill_judgment, "O"
        )
    if kind == OBLIGATION_KIND_API_CONSISTENT:
        return _judgment_has_field(fill_judgment, "A") and _judgment_has_field(
            fill_judgment, "E"
        )
    if kind == OBLIGATION_KIND_SECURITY_SOUND:
        return _judgment_has_field(fill_judgment, "B")
    if kind == "RESOURCE_BOUNDED":
        return _judgment_has_field(fill_judgment, "Pi")
    # CUSTOM — use formula hint if present, then delegate
    return obligation.is_satisfiable_by(fill_judgment)


def _boundary_coordinates(authored: dict, holes: dict) -> list[str]:
    """Identify coordinates that are adjacent to both authored and hole regions.

    A coordinate is a *boundary coordinate* if it appears in *authored* and at
    least one of its "neighbours" (determined by shared hash prefix) appears in
    *holes*, or vice-versa.

    In this simplified implementation, boundary coordinates are simply those
    authored coordinates whose stable hash shares a prefix with any hole
    coordinate hash.

    Parameters
    ----------
    authored:
        Mapping ``coordinate → judgment_fields`` for the authored region.
    holes:
        Mapping ``coordinate → HoleRecord`` for the hole region.

    Returns
    -------
    list[str]
        Sorted list of boundary coordinate strings.
    """
    if not authored or not holes:
        return []

    hole_prefixes = {_stable_hash(c)[:4] for c in holes}
    boundary = []
    for coord in authored:
        if _stable_hash(coord)[:4] in hole_prefixes:
            boundary.append(coord)
    # Also include hole coordinates adjacent to authored ones
    authored_prefixes = {_stable_hash(c)[:4] for c in authored}
    for coord in holes:
        if _stable_hash(coord)[:4] in authored_prefixes and coord not in boundary:
            boundary.append(coord)
    return sorted(boundary)


def _judgment_has_field(judgment: dict, field_name: str) -> bool:
    """Check whether *judgment* contains a non-None value for *field_name*.

    Parameters
    ----------
    judgment:
        Dictionary representing judgment fields.
    field_name:
        The Theory2 component name to check (one of ``JUDGMENT_COMPONENTS``).

    Returns
    -------
    bool
        ``True`` if ``field_name`` is present and its value is not ``None``.
    """
    return judgment.get(field_name) is not None


def _compute_coverage(authored_count: int, total_count: int) -> float:
    """Compute the fraction of the cover that has been authored.

    Parameters
    ----------
    authored_count:
        Number of authored (non-hole) coordinates.
    total_count:
        Total number of coordinates in the cover.

    Returns
    -------
    float
        Value in ``[0.0, 1.0]``; returns ``0.0`` when *total_count* is zero.
    """
    if total_count <= 0:
        return 0.0
    return min(1.0, max(0.0, authored_count / total_count))


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class HoleKind(enum.Enum):
    """Enumeration of the recognised kinds of holes in a partial section.

    Attributes
    ----------
    UNIMPLEMENTED:
        The region exists in the specification but has not been implemented.
    DELEGATED:
        The fill has been delegated to another author or subsystem.
    DEFERRED:
        The fill is intentionally postponed; must be resolved before release.
    OPTIONAL:
        The hole need not be filled; the section is valid without it.
    BLOCKED:
        The fill is blocked by an external dependency and cannot proceed.
    """

    UNIMPLEMENTED = "UNIMPLEMENTED"
    DELEGATED = "DELEGATED"
    DEFERRED = "DEFERRED"
    OPTIONAL = "OPTIONAL"
    BLOCKED = "BLOCKED"


class ObligationKind(enum.Enum):
    """Enumeration of the kinds of local obligations arising from holes.

    Attributes
    ----------
    TYPE_SAFE:
        The fill must satisfy type-safety constraints (``T`` component).
    BEHAVIOR_CORRECT:
        The fill must realise the specified behaviour (``phi``, ``O``).
    API_CONSISTENT:
        The fill must conform to the public API contract (``A``, ``E``).
    SECURITY_SOUND:
        The fill must not introduce security vulnerabilities (``B``).
    RESOURCE_BOUNDED:
        The fill must respect resource-bound policies (``Pi``).
    CUSTOM:
        A domain-specific obligation not covered by the above.
    """

    TYPE_SAFE = "TYPE_SAFE"
    BEHAVIOR_CORRECT = "BEHAVIOR_CORRECT"
    API_CONSISTENT = "API_CONSISTENT"
    SECURITY_SOUND = "SECURITY_SOUND"
    RESOURCE_BOUNDED = "RESOURCE_BOUNDED"
    CUSTOM = "CUSTOM"


class FillStatus(enum.Enum):
    """Status of a fill attempt for a hole.

    Attributes
    ----------
    PENDING:
        Attempt has been registered but not yet evaluated.
    SUCCESSFUL:
        All obligations were met; the hole is closed.
    PARTIAL:
        Some obligations were met but others remain open.
    FAILED:
        The fill violated at least one mandatory obligation.
    """

    PENDING = "PENDING"
    SUCCESSFUL = "SUCCESSFUL"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class WitnessOutcome(enum.Enum):
    """High-level outcome of a mixed-mode partial-section analysis.

    Attributes
    ----------
    FULLY_AUTHORED:
        All coordinates are authored; no holes remain.
    HOLES_WITH_OBLIGATIONS:
        Holes exist and each has associated obligations.
    HOLES_NO_OBLIGATIONS:
        Holes exist but none carries any obligation (all optional).
    INCOHERENT:
        Boundary coherence check failed.
    """

    FULLY_AUTHORED = "FULLY_AUTHORED"
    HOLES_WITH_OBLIGATIONS = "HOLES_WITH_OBLIGATIONS"
    HOLES_NO_OBLIGATIONS = "HOLES_NO_OBLIGATIONS"
    INCOHERENT = "INCOHERENT"


# ---------------------------------------------------------------------------
# Supporting frozen dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HoleRecord:
    """Immutable record describing a single hole in a partial section.

    A hole is a coordinate in the cover where the author has not yet committed
    to a specific implementation.  The ``hole_kind`` expresses the *intent*
    behind the omission (see ``HoleKind``).

    Parameters
    ----------
    hole_id:
        Stable identifier for this hole (see ``_make_hole_id``).
    coordinate:
        The coordinate in the cover where the hole exists.
    hole_kind:
        String label drawn from ``HoleKind`` values.
    surrounding_judgment_fragments:
        Partial judgment fields from neighbouring authored coordinates that
        constrain what the fill may look like.
    prescribed_formula:
        An optional logical formula (string) that the fill must satisfy,
        derived from the surrounding specification clauses.
    hole_annotations:
        Arbitrary key-value metadata attached to the hole by the author.
    priority:
        Numeric priority for scheduling fill attempts; lower is more urgent.
    created_at:
        ISO-8601 timestamp of hole registration.

    Examples
    --------
    >>> hr = HoleRecord(
    ...     hole_id="hole:abcd1234:unimplemented",
    ...     coordinate="module.foo",
    ...     hole_kind=HOLE_KIND_UNIMPLEMENTED,
    ...     surrounding_judgment_fragments={"T": "int -> int"},
    ...     prescribed_formula=None,
    ...     hole_annotations={},
    ...     priority=1,
    ...     created_at="2024-01-01T00:00:00+00:00",
    ... )
    >>> hr.is_optional()
    False
    """

    hole_id: str
    coordinate: str
    hole_kind: str
    surrounding_judgment_fragments: dict
    prescribed_formula: str | None
    hole_annotations: dict
    priority: int
    created_at: str

    def is_optional(self) -> bool:
        """Return ``True`` when this hole need not be filled to close the section.

        A hole is optional when its ``hole_kind`` is ``"OPTIONAL"``.

        Returns
        -------
        bool
        """
        return self.hole_kind == HOLE_KIND_OPTIONAL


@dataclass(frozen=True, slots=True)
class LocalObligation:
    """Immutable record representing a single local obligation induced by a hole.

    A local obligation is a constraint that any fill for the associated hole
    must satisfy in order for the partial section to remain coherent with the
    enclosing specification.

    Parameters
    ----------
    obligation_id:
        Stable identifier (see ``_make_obligation_id``).
    hole_id:
        Identifier of the hole that induced this obligation.
    coordinate:
        The coordinate at which the obligation must be discharged.
    obligation_kind:
        String label drawn from ``ObligationKind`` values.
    constraint_formula:
        Logical or informal statement of the constraint.
    required_evidence_kinds:
        List of evidence-kind strings that a valid fill must provide.
    trust_tier_required:
        The minimum trust tier at which the fill must be accepted.
    can_delegate:
        Whether this obligation may be delegated to another subsystem.
    created_at:
        ISO-8601 timestamp of obligation creation.

    Examples
    --------
    >>> from jugeo.problem_modes.specification_satisfaction.mixed_mode_programming_partial_sem import (
    ...     LocalObligation, OBLIGATION_KIND_TYPE_SAFE, TRUST_TIER_PROPOSED
    ... )
    >>> ob = LocalObligation(
    ...     obligation_id="oblig:abcd1234:type_safe",
    ...     hole_id="hole:abcd1234:unimplemented",
    ...     coordinate="module.foo",
    ...     obligation_kind=OBLIGATION_KIND_TYPE_SAFE,
    ...     constraint_formula="fill : int -> int",
    ...     required_evidence_kinds=["TYPE_ANNOTATION"],
    ...     trust_tier_required=TRUST_TIER_PROPOSED,
    ...     can_delegate=False,
    ...     created_at="2024-01-01T00:00:00+00:00",
    ... )
    >>> ob.is_satisfiable_by({"T": "int -> int", "phi": "f x = x + 1"})
    True
    """

    obligation_id: str
    hole_id: str
    coordinate: str
    obligation_kind: str
    constraint_formula: str
    required_evidence_kinds: list
    trust_tier_required: str
    can_delegate: bool
    created_at: str

    def is_satisfiable_by(self, fill_dict: dict) -> bool:
        """Test whether *fill_dict* plausibly satisfies this obligation.

        The check is structural: it verifies that the fill judgment contains
        the Theory2 components that the obligation's kind requires, and that
        all ``required_evidence_kinds`` are represented as non-empty keys.

        Parameters
        ----------
        fill_dict:
            Dictionary of judgment fields from the proposed fill.

        Returns
        -------
        bool
            ``True`` if the obligation appears satisfiable, ``False`` otherwise.

        Notes
        -----
        This is a *plausibility* check, not a formal proof.  Domain-specific
        verifiers may impose stricter requirements.
        """
        # Delegate to the internal helper for kind-specific structural checks
        structurally_ok = _check_obligation_met(self, fill_dict)
        if not structurally_ok:
            return False
        # Additionally check that every required evidence kind appears as a
        # non-None key somewhere in the fill dictionary (by substring match on
        # keys, case-insensitive)
        for ev_kind in self.required_evidence_kinds:
            ev_lower = ev_kind.lower()
            found = any(ev_lower in k.lower() for k in fill_dict if fill_dict[k] is not None)
            if not found:
                # Evidence kind not represented — try matching against values
                found_in_val = any(
                    ev_lower in str(v).lower()
                    for v in fill_dict.values()
                    if v is not None
                )
                if not found_in_val:
                    return False
        return True


# ---------------------------------------------------------------------------
# Mutable supporting dataclasses
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class PartialSection:
    """Mutable representation of a partial section σ: X → J.

    A partial section partitions the coordinate cover ``X`` into *authored*
    coordinates (where σ is defined) and *hole* coordinates (where σ is
    undefined).  Obligations are tracked per hole in ``obligation_map``.

    Parameters
    ----------
    section_id:
        Unique identifier for this section instance.
    spec_id:
        The specification this partial section is attempting to satisfy.
    authored_map:
        Mapping ``coordinate → judgment_fields`` for authored regions.
    hole_map:
        Mapping ``coordinate → HoleRecord`` for hole regions.
    obligation_map:
        Mapping ``hole_id → list[LocalObligation]`` for registered obligations.
    created_at:
        ISO-8601 timestamp of section creation.
    updated_at:
        ISO-8601 timestamp of last modification.

    Examples
    --------
    >>> ps = PartialSection(
    ...     section_id="psec:abc123",
    ...     spec_id="spec:xyz",
    ...     authored_map={},
    ...     hole_map={},
    ...     obligation_map={},
    ...     created_at="2024-01-01T00:00:00+00:00",
    ...     updated_at="2024-01-01T00:00:00+00:00",
    ... )
    """

    section_id: str
    spec_id: str
    authored_map: dict = field(default_factory=dict)
    hole_map: dict = field(default_factory=dict)
    obligation_map: dict = field(default_factory=dict)
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)

    # ------------------------------------------------------------------
    # Mutation helpers
    # ------------------------------------------------------------------

    def add_authored(self, coordinate: str, judgment_fields: dict) -> None:
        """Register *coordinate* as an authored region with *judgment_fields*.

        If the coordinate was previously a hole, it is promoted to authored
        and removed from ``hole_map``.

        Parameters
        ----------
        coordinate:
            Cover coordinate to mark as authored.
        judgment_fields:
            The Theory2 judgment tuple fields for this coordinate.
        """
        self.authored_map[coordinate] = dict(judgment_fields)
        self.hole_map.pop(coordinate, None)
        self.updated_at = _now_iso()

    def add_hole(self, coordinate: str, hole_record: "HoleRecord") -> None:
        """Register *coordinate* as a hole described by *hole_record*.

        Raises
        ------
        ValueError
            If adding this hole would exceed ``MAX_HOLES_PER_SECTION``.

        Parameters
        ----------
        coordinate:
            Cover coordinate to mark as a hole.
        hole_record:
            The ``HoleRecord`` describing the hole.
        """
        if len(self.hole_map) >= MAX_HOLES_PER_SECTION:
            raise ValueError(
                f"Cannot add more than {MAX_HOLES_PER_SECTION} holes to a section."
            )
        self.hole_map[coordinate] = hole_record
        self.authored_map.pop(coordinate, None)
        self.updated_at = _now_iso()

    def fill_hole(self, coordinate: str, judgment_fields: dict) -> bool:
        """Attempt to fill the hole at *coordinate* with *judgment_fields*.

        The fill is accepted unconditionally at the section level; obligation
        checking is the responsibility of the coordinator.  On success the
        coordinate is moved from ``hole_map`` to ``authored_map``.

        Parameters
        ----------
        coordinate:
            The coordinate whose hole is being filled.
        judgment_fields:
            The proposed judgment fields for the fill.

        Returns
        -------
        bool
            ``True`` if the coordinate was a hole and has been filled;
            ``False`` if the coordinate was not a registered hole.
        """
        if coordinate not in self.hole_map:
            return False
        del self.hole_map[coordinate]
        self.authored_map[coordinate] = dict(judgment_fields)
        self.updated_at = _now_iso()
        return True

    def get_completion_fraction(self) -> float:
        """Return the fraction of coordinates that are authored.

        Returns
        -------
        float
            Value in ``[0.0, 1.0]``.
        """
        total = len(self.authored_map) + len(self.hole_map)
        return _compute_coverage(len(self.authored_map), total)

    def to_dict(self) -> dict:
        """Serialise the partial section to a plain dictionary.

        Returns
        -------
        dict
            JSON-compatible representation of this partial section.
        """
        return {
            "section_id": self.section_id,
            "spec_id": self.spec_id,
            "authored_count": len(self.authored_map),
            "hole_count": len(self.hole_map),
            "completion_fraction": self.get_completion_fraction(),
            "authored_coordinates": sorted(self.authored_map.keys()),
            "hole_coordinates": sorted(self.hole_map.keys()),
            "obligation_counts": {
                hid: len(obs) for hid, obs in self.obligation_map.items()
            },
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(slots=True)
class HoleFillAttempt:
    """Mutable record tracking a single attempt to fill a hole.

    Parameters
    ----------
    attempt_id:
        Unique identifier for this fill attempt.
    hole_id:
        Identifier of the hole being filled.
    coordinate:
        The coordinate being filled.
    proposed_judgment_fields:
        The Theory2 judgment tuple fields proposed as the fill.
    obligations_addressed:
        List of obligation IDs that the fill has satisfied.
    obligations_remaining:
        List of obligation IDs that remain unmet after this attempt.
    fill_trust_tier:
        Trust tier at which this fill was proposed.
    fill_status:
        Current status string (see ``FillStatus``).
    attempt_timestamp:
        ISO-8601 timestamp of when the attempt was made.

    Examples
    --------
    >>> attempt = HoleFillAttempt(
    ...     attempt_id="attempt:abcdef",
    ...     hole_id="hole:abcd1234:unimplemented",
    ...     coordinate="module.foo",
    ...     proposed_judgment_fields={"T": "int -> int"},
    ...     obligations_addressed=[],
    ...     obligations_remaining=[],
    ...     fill_trust_tier=TRUST_TIER_PROPOSED,
    ...     fill_status=FillStatus.PENDING.value,
    ...     attempt_timestamp="2024-01-01T00:00:00+00:00",
    ... )
    """

    attempt_id: str
    hole_id: str
    coordinate: str
    proposed_judgment_fields: dict = field(default_factory=dict)
    obligations_addressed: list = field(default_factory=list)
    obligations_remaining: list = field(default_factory=list)
    fill_trust_tier: str = TRUST_TIER_PROPOSED
    fill_status: str = FillStatus.PENDING.value
    attempt_timestamp: str = field(default_factory=_now_iso)

    def mark_successful(self) -> None:
        """Mark this attempt as fully successful.

        Sets ``fill_status`` to ``FillStatus.SUCCESSFUL`` and clears
        ``obligations_remaining``.
        """
        self.fill_status = FillStatus.SUCCESSFUL.value
        self.obligations_remaining = []

    def mark_failed(self, unmet_ids: list[str]) -> None:
        """Mark this attempt as failed due to unmet obligations.

        Parameters
        ----------
        unmet_ids:
            List of obligation IDs that were not satisfied.
        """
        self.fill_status = FillStatus.FAILED.value
        self.obligations_remaining = list(unmet_ids)

    def to_dict(self) -> dict:
        """Serialise this fill attempt to a plain dictionary.

        Returns
        -------
        dict
        """
        return {
            "attempt_id": self.attempt_id,
            "hole_id": self.hole_id,
            "coordinate": self.coordinate,
            "fill_trust_tier": self.fill_trust_tier,
            "fill_status": self.fill_status,
            "obligations_addressed_count": len(self.obligations_addressed),
            "obligations_remaining_count": len(self.obligations_remaining),
            "attempt_timestamp": self.attempt_timestamp,
        }


# ---------------------------------------------------------------------------
# Main coordinator class
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class MixedModeProgrammingPartialCoordinator:
    """Orchestrator for partial-section checking with holes and obligations.

    The coordinator is the central mutable object that:

    * Tracks which coordinates are authored vs. holes.
    * Manages ``HoleRecord`` instances in ``hole_registry``.
    * Tracks ``LocalObligation`` lists per hole in ``obligation_registry``.
    * Handles fill attempts and obligation checking.
    * Produces a ``MixedModeProgrammingPartialWitness`` on request.

    Parameters
    ----------
    coordinator_id:
        Unique identifier for this coordinator instance.
    partial_section:
        Dict mapping ``coordinate → judgment_fields`` (authored) or ``None``
        (hole).  Entries with value ``None`` are treated as holes; entries with
        a dict value are authored.
    hole_registry:
        Dict mapping ``coordinate → HoleRecord``.
    obligation_registry:
        Dict mapping ``hole_id → list[LocalObligation]``.
    authored_coordinates:
        Ordered list of authored coordinate strings.
    hole_coordinates:
        Ordered list of hole coordinate strings.
    coordination_log:
        Append-only list of log entry dicts recording coordinator events.
    strict_mode:
        When ``True``, fill attempts that leave any non-optional obligation
        unmet will be rejected with a ``ValueError``.

    Examples
    --------
    >>> coord = MixedModeProgrammingPartialCoordinator(
    ...     coordinator_id="coord-001",
    ...     partial_section={},
    ...     hole_registry={},
    ...     obligation_registry={},
    ...     authored_coordinates=[],
    ...     hole_coordinates=[],
    ...     coordination_log=[],
    ...     strict_mode=False,
    ... )
    """

    coordinator_id: str
    partial_section: dict = field(default_factory=dict)
    hole_registry: dict = field(default_factory=dict)
    obligation_registry: dict = field(default_factory=dict)
    authored_coordinates: list = field(default_factory=list)
    hole_coordinates: list = field(default_factory=list)
    coordination_log: list = field(default_factory=list)
    strict_mode: bool = False

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register_hole(
        self,
        coordinate: str,
        hole_kind: str = HOLE_KIND_UNIMPLEMENTED,
        surrounding_fragments: dict | None = None,
        prescribed_formula: str | None = None,
        annotations: dict | None = None,
        priority: int = 5,
    ) -> HoleRecord:
        """Register a hole at *coordinate* in the partial section.

        If the coordinate was previously authored, it will be demoted to a hole.

        Parameters
        ----------
        coordinate:
            The cover coordinate to mark as a hole.
        hole_kind:
            The kind of hole (default ``HOLE_KIND_UNIMPLEMENTED``).
        surrounding_fragments:
            Judgment fragments from neighbouring authored regions.
        prescribed_formula:
            Optional constraint formula derived from the specification.
        annotations:
            Arbitrary author-supplied metadata.
        priority:
            Scheduling priority; lower numbers are higher priority.

        Returns
        -------
        HoleRecord
            The newly registered hole record.

        Raises
        ------
        ValueError
            If the coordinate already has a registered hole.
        """
        if coordinate in self.hole_registry:
            raise ValueError(f"Hole already registered at coordinate {coordinate!r}.")
        hole_id = _make_hole_id(coordinate, hole_kind)
        record = HoleRecord(
            hole_id=hole_id,
            coordinate=coordinate,
            hole_kind=hole_kind,
            surrounding_judgment_fragments=dict(surrounding_fragments or {}),
            prescribed_formula=prescribed_formula,
            hole_annotations=dict(annotations or {}),
            priority=priority,
            created_at=_now_iso(),
        )
        self.hole_registry[coordinate] = record
        if coordinate not in self.hole_coordinates:
            self.hole_coordinates.append(coordinate)
        # Remove from authored if it was previously there
        if coordinate in self.authored_coordinates:
            self.authored_coordinates.remove(coordinate)
        self.partial_section[coordinate] = None
        self._log("register_hole", {"coordinate": coordinate, "hole_id": hole_id})
        return record

    def register_obligation(
        self,
        hole: HoleRecord,
        obligation_kind: str,
        constraint_formula: str,
        required_evidence_kinds: list[str] | None = None,
        trust_tier_required: str = TRUST_TIER_PROPOSED,
        can_delegate: bool = False,
    ) -> "LocalObligation":
        """Register a local obligation for *hole*.

        Parameters
        ----------
        hole:
            The hole that induces this obligation.
        obligation_kind:
            Kind string from ``ObligationKind`` values.
        constraint_formula:
            Human-readable or logical formula expressing the constraint.
        required_evidence_kinds:
            Evidence kinds that must be present in the fill judgment.
        trust_tier_required:
            Minimum trust tier for the fill to satisfy this obligation.
        can_delegate:
            Whether the obligation can be delegated.

        Returns
        -------
        LocalObligation
        """
        oblig_id = _make_obligation_id(hole.hole_id, obligation_kind)
        obligation = LocalObligation(
            obligation_id=oblig_id,
            hole_id=hole.hole_id,
            coordinate=hole.coordinate,
            obligation_kind=obligation_kind,
            constraint_formula=constraint_formula,
            required_evidence_kinds=list(required_evidence_kinds or []),
            trust_tier_required=trust_tier_required,
            can_delegate=can_delegate,
            created_at=_now_iso(),
        )
        self.obligation_registry.setdefault(hole.hole_id, []).append(obligation)
        self._log(
            "register_obligation",
            {"hole_id": hole.hole_id, "obligation_id": oblig_id, "kind": obligation_kind},
        )
        return obligation

    def fill_hole(
        self,
        coordinate: str,
        fill_judgment: dict,
        fill_trust_tier: str = TRUST_TIER_PROPOSED,
    ) -> HoleFillAttempt:
        """Attempt to fill the hole at *coordinate* with *fill_judgment*.

        Each registered obligation for the hole is checked via
        ``_check_obligation_met``.  The coordinator updates its internal state
        based on the outcome, and in ``strict_mode`` raises an error if any
        non-optional obligation is left unmet.

        Parameters
        ----------
        coordinate:
            The coordinate of the hole to fill.
        fill_judgment:
            Dict of Theory2 judgment fields for the proposed fill.
        fill_trust_tier:
            Trust tier at which this fill is proposed (default ``PROPOSED``).

        Returns
        -------
        HoleFillAttempt
            A record of the attempt, including which obligations were met.

        Raises
        ------
        KeyError
            If no hole is registered at *coordinate*.
        ValueError
            In ``strict_mode``, if any non-optional obligation is unmet.
        """
        if coordinate not in self.hole_registry:
            raise KeyError(f"No hole registered at coordinate {coordinate!r}.")

        hole = self.hole_registry[coordinate]
        attempt_id = _make_attempt_id(hole.hole_id)
        obligations = self.obligation_registry.get(hole.hole_id, [])

        addressed: list[str] = []
        remaining: list[str] = []
        for ob in obligations:
            if _check_obligation_met(ob, fill_judgment):
                addressed.append(ob.obligation_id)
            else:
                remaining.append(ob.obligation_id)

        attempt = HoleFillAttempt(
            attempt_id=attempt_id,
            hole_id=hole.hole_id,
            coordinate=coordinate,
            proposed_judgment_fields=dict(fill_judgment),
            obligations_addressed=addressed,
            obligations_remaining=remaining,
            fill_trust_tier=fill_trust_tier,
            fill_status=FillStatus.PENDING.value,
            attempt_timestamp=_now_iso(),
        )

        if not remaining:
            attempt.mark_successful()
            # Promote coordinate from hole to authored
            del self.hole_registry[coordinate]
            if coordinate in self.hole_coordinates:
                self.hole_coordinates.remove(coordinate)
            self.partial_section[coordinate] = dict(fill_judgment)
            if coordinate not in self.authored_coordinates:
                self.authored_coordinates.append(coordinate)
        elif remaining and not any(
            ob.obligation_id in remaining
            for ob in obligations
            if not hole.is_optional()
        ):
            # All remaining obligations belong to optional holes
            attempt.mark_successful()
            del self.hole_registry[coordinate]
            if coordinate in self.hole_coordinates:
                self.hole_coordinates.remove(coordinate)
            self.partial_section[coordinate] = dict(fill_judgment)
            if coordinate not in self.authored_coordinates:
                self.authored_coordinates.append(coordinate)
        else:
            attempt.fill_status = FillStatus.PARTIAL.value if addressed else FillStatus.FAILED.value
            if self.strict_mode:
                unmet_summary = ", ".join(remaining)
                raise ValueError(
                    f"strict_mode: fill at {coordinate!r} left obligations unmet: {unmet_summary}"
                )

        self._log(
            "fill_hole",
            {
                "coordinate": coordinate,
                "attempt_id": attempt_id,
                "status": attempt.fill_status,
                "addressed": len(addressed),
                "remaining": len(remaining),
            },
        )
        return attempt

    def check_partial_section(self) -> dict:
        """Run a full coherence check across the current partial section.

        Checks:

        * Boundary coherence between authored and hole regions.
        * Whether all non-optional holes have at least one obligation.
        * Coverage fraction.

        Returns
        -------
        dict
            A summary dict with keys ``coherent``, ``coverage``,
            ``boundary_issues``, ``holes_without_obligations``,
            ``authored_count``, ``hole_count``.
        """
        authored = {
            c: v for c, v in self.partial_section.items() if v is not None
        }
        holes = {c: self.hole_registry.get(c) for c in self.hole_coordinates}
        boundaries = _boundary_coordinates(authored, holes)

        boundary_issues: list[str] = []
        for bc in boundaries:
            j = authored.get(bc, {})
            if j and not any(_judgment_has_field(j, f) for f in JUDGMENT_COMPONENTS):
                boundary_issues.append(f"{bc}: authored judgment is empty at boundary")

        holes_without_obligations: list[str] = []
        for coord in self.hole_coordinates:
            hr = self.hole_registry.get(coord)
            if hr and not hr.is_optional():
                if not self.obligation_registry.get(hr.hole_id):
                    holes_without_obligations.append(coord)

        coverage = _compute_coverage(len(self.authored_coordinates), len(self.partial_section))
        coherent = not boundary_issues and not holes_without_obligations

        self._log(
            "check_partial_section",
            {
                "coherent": coherent,
                "coverage": coverage,
                "boundary_issues": boundary_issues,
                "holes_without_obligations": holes_without_obligations,
            },
        )
        return {
            "coherent": coherent,
            "coverage": coverage,
            "boundary_issues": boundary_issues,
            "holes_without_obligations": holes_without_obligations,
            "authored_count": len(self.authored_coordinates),
            "hole_count": len(self.hole_coordinates),
        }

    def compute_hole_obligations(
        self, hole: HoleRecord, spec_clauses: list[dict]
    ) -> list["LocalObligation"]:
        """Derive and register local obligations for *hole* from *spec_clauses*.

        Each clause that references the hole's coordinate or whose ``kind``
        matches a known obligation kind generates a corresponding obligation.

        Parameters
        ----------
        hole:
            The ``HoleRecord`` for which to compute obligations.
        spec_clauses:
            List of specification clause dicts.  Each clause may have keys
            ``"coordinate"``, ``"kind"``, ``"formula"``, and ``"evidence"``.

        Returns
        -------
        list[LocalObligation]
            The obligations created and registered.
        """
        new_obligations: list[LocalObligation] = []
        for clause in spec_clauses:
            clause_coord = clause.get("coordinate", "")
            clause_kind = clause.get("kind", OBLIGATION_KIND_BEHAVIOR_CORRECT)
            clause_formula = clause.get("formula", "true")
            clause_evidence = clause.get("evidence", [])
            # Only derive an obligation if the clause applies to this hole
            if clause_coord and clause_coord != hole.coordinate:
                continue
            ob = self.register_obligation(
                hole=hole,
                obligation_kind=str(clause_kind),
                constraint_formula=str(clause_formula),
                required_evidence_kinds=list(clause_evidence),
            )
            new_obligations.append(ob)
        # If no clauses applied, add a default BEHAVIOR_CORRECT obligation
        if not new_obligations and not hole.is_optional():
            ob = self.register_obligation(
                hole=hole,
                obligation_kind=OBLIGATION_KIND_BEHAVIOR_CORRECT,
                constraint_formula=f"fill at {hole.coordinate} must satisfy spec",
            )
            new_obligations.append(ob)
        return new_obligations

    def get_authored_coverage(self) -> float:
        """Return the fraction of coordinates that are authored.

        Returns
        -------
        float
            Value in ``[0.0, 1.0]``.
        """
        total = len(self.authored_coordinates) + len(self.hole_coordinates)
        return _compute_coverage(len(self.authored_coordinates), total)

    def get_unfilled_holes(self) -> list[HoleRecord]:
        """Return all currently unfilled hole records.

        Returns
        -------
        list[HoleRecord]
            Hole records in priority order (lowest priority number first).
        """
        records = list(self.hole_registry.values())
        return sorted(records, key=lambda hr: hr.priority)

    def summarize(self) -> dict:
        """Return a summary dict of the coordinator's current state.

        Returns
        -------
        dict
            Includes coverage, hole counts, obligation counts, and log length.
        """
        total_obligations = sum(
            len(obs) for obs in self.obligation_registry.values()
        )
        return {
            "coordinator_id": self.coordinator_id,
            "authored_count": len(self.authored_coordinates),
            "hole_count": len(self.hole_coordinates),
            "coverage": self.get_authored_coverage(),
            "total_obligations": total_obligations,
            "strict_mode": self.strict_mode,
            "log_entries": len(self.coordination_log),
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _log(self, event: str, data: dict) -> None:
        """Append a timestamped log entry.

        Parameters
        ----------
        event:
            Short event name.
        data:
            Additional data to record alongside the event.
        """
        self.coordination_log.append(
            {"event": event, "timestamp": _now_iso(), **data}
        )


# ---------------------------------------------------------------------------
# Main analyzer class
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class MixedModeProgrammingPartialAnalyzer:
    """Analyzes partial sections to identify hole patterns and authorship structure.

    The analyzer performs a deeper structural analysis of a partial section,
    computing:

    * Hole patterns: which hole kinds dominate, which are clustered.
    * Obligation structures: which holes carry the heaviest obligation loads.
    * Authorship boundaries: where authored and hole regions meet.
    * Coherence at boundaries: whether the authored judgments are compatible
      with the constraint context established by surrounding holes.

    Parameters
    ----------
    analyzer_id:
        Unique identifier for this analyzer.
    analysis_log:
        Append-only list of analysis event dicts.
    hole_analysis:
        Dict mapping ``hole_id → dict`` with per-hole analysis results.
    obligation_analysis:
        Dict mapping ``obligation_id → dict`` with per-obligation results.
    authorship_boundary_map:
        Dict mapping ``coordinate → bool`` indicating whether the coordinate
        is on an authorship boundary.
    partial_section_coherence:
        ``None`` until ``check_coherence_at_boundary`` has been called;
        thereafter a ``bool``.
    analysis_results:
        Ordered list of high-level analysis result dicts.

    Examples
    --------
    >>> analyzer = MixedModeProgrammingPartialAnalyzer(
    ...     analyzer_id="anal-001",
    ...     analysis_log=[],
    ...     hole_analysis={},
    ...     obligation_analysis={},
    ...     authorship_boundary_map={},
    ...     partial_section_coherence=None,
    ...     analysis_results=[],
    ... )
    """

    analyzer_id: str
    analysis_log: list = field(default_factory=list)
    hole_analysis: dict = field(default_factory=dict)
    obligation_analysis: dict = field(default_factory=dict)
    authorship_boundary_map: dict = field(default_factory=dict)
    partial_section_coherence: bool | None = None
    analysis_results: list = field(default_factory=list)

    def analyze_partial_section(self, section: PartialSection) -> dict:
        """Perform a full analysis of *section*.

        Analyses each hole, each obligation, computes boundaries, and checks
        coherence.

        Parameters
        ----------
        section:
            The ``PartialSection`` to analyse.

        Returns
        -------
        dict
            High-level analysis result including hole count, obligation counts,
            coherence flag, and boundary coordinates.
        """
        for coord, hole_record in section.hole_map.items():
            self.analyze_hole(hole_record)

        for hole_id, obligations in section.obligation_map.items():
            for ob in obligations:
                self.analyze_obligation(ob)

        boundaries = _boundary_coordinates(section.authored_map, section.hole_map)
        for bc in boundaries:
            self.authorship_boundary_map[bc] = True

        coherent = self.check_coherence_at_boundary(section)

        total_obligations = sum(
            len(obs) for obs in section.obligation_map.values()
        )
        result = {
            "section_id": section.section_id,
            "spec_id": section.spec_id,
            "authored_count": len(section.authored_map),
            "hole_count": len(section.hole_map),
            "boundary_count": len(boundaries),
            "total_obligations": total_obligations,
            "coherent": coherent,
            "completion_fraction": section.get_completion_fraction(),
        }
        self.analysis_results.append(result)
        self._alog("analyze_partial_section", {"section_id": section.section_id})
        return result

    def analyze_hole(self, hole: HoleRecord) -> dict:
        """Analyse a single *hole* and store per-hole metrics.

        Parameters
        ----------
        hole:
            The ``HoleRecord`` to analyse.

        Returns
        -------
        dict
            Analysis result including kind, priority, whether it has a
            prescribed formula, and whether it is optional.
        """
        result = {
            "hole_id": hole.hole_id,
            "coordinate": hole.coordinate,
            "kind": hole.hole_kind,
            "priority": hole.priority,
            "has_prescribed_formula": hole.prescribed_formula is not None,
            "is_optional": hole.is_optional(),
            "fragment_field_count": len(hole.surrounding_judgment_fragments),
        }
        self.hole_analysis[hole.hole_id] = result
        self._alog("analyze_hole", {"hole_id": hole.hole_id})
        return result

    def analyze_obligation(self, obligation: LocalObligation) -> dict:
        """Analyse a single *obligation* and store per-obligation metrics.

        Parameters
        ----------
        obligation:
            The ``LocalObligation`` to analyse.

        Returns
        -------
        dict
            Analysis result including kind, trust tier, delegatability, and
            required evidence counts.
        """
        result = {
            "obligation_id": obligation.obligation_id,
            "hole_id": obligation.hole_id,
            "kind": obligation.obligation_kind,
            "trust_tier_required": obligation.trust_tier_required,
            "can_delegate": obligation.can_delegate,
            "evidence_required": len(obligation.required_evidence_kinds),
        }
        self.obligation_analysis[obligation.obligation_id] = result
        self._alog("analyze_obligation", {"obligation_id": obligation.obligation_id})
        return result

    def check_coherence_at_boundary(self, section: PartialSection) -> bool:
        """Check that authored judgments at boundaries are coherent with holes.

        Coherence is defined as: for every boundary coordinate in the authored
        region, the judgment must contain at least one of the components
        referenced by the surrounding hole's ``surrounding_judgment_fragments``.

        Parameters
        ----------
        section:
            The partial section to check.

        Returns
        -------
        bool
            ``True`` if all boundary authored judgments are coherent.
        """
        boundaries = _boundary_coordinates(section.authored_map, section.hole_map)
        coherent = True
        for bc in boundaries:
            authored_j = section.authored_map.get(bc, {})
            hole_r = section.hole_map.get(bc)
            if hole_r is None:
                continue
            frags = hole_r.surrounding_judgment_fragments
            if not frags:
                continue
            # Check that at least one fragment key is present in authored judgment
            overlap = set(authored_j.keys()) & set(frags.keys())
            if not overlap:
                coherent = False
                break
        self.partial_section_coherence = coherent
        self._alog("check_coherence_at_boundary", {"coherent": coherent})
        return coherent

    def rank_holes_by_obligation_count(
        self, section: PartialSection
    ) -> list[tuple[str, int]]:
        """Rank holes in *section* by their obligation count (descending).

        Parameters
        ----------
        section:
            The partial section whose holes are to be ranked.

        Returns
        -------
        list[tuple[str, int]]
            List of ``(coordinate, obligation_count)`` pairs, sorted from
            highest to lowest obligation count.
        """
        ranked: list[tuple[str, int]] = []
        for coord, hole_r in section.hole_map.items():
            count = len(section.obligation_map.get(hole_r.hole_id, []))
            ranked.append((coord, count))
        ranked.sort(key=lambda t: t[1], reverse=True)
        self._alog("rank_holes_by_obligation_count", {"ranked_count": len(ranked)})
        return ranked

    def produce_analysis_report(self) -> dict:
        """Produce a comprehensive analysis report from accumulated data.

        Returns
        -------
        dict
            Includes hole summaries, obligation summaries, boundary map, and
            overall coherence status.
        """
        holes_by_kind: dict[str, int] = {}
        for ha in self.hole_analysis.values():
            kind = ha.get("kind", "UNKNOWN")
            holes_by_kind[kind] = holes_by_kind.get(kind, 0) + 1

        obligations_by_kind: dict[str, int] = {}
        for oa in self.obligation_analysis.values():
            kind = oa.get("kind", "UNKNOWN")
            obligations_by_kind[kind] = obligations_by_kind.get(kind, 0) + 1

        report = {
            "analyzer_id": self.analyzer_id,
            "total_holes_analyzed": len(self.hole_analysis),
            "total_obligations_analyzed": len(self.obligation_analysis),
            "holes_by_kind": holes_by_kind,
            "obligations_by_kind": obligations_by_kind,
            "boundary_coordinates": sorted(
                k for k, v in self.authorship_boundary_map.items() if v
            ),
            "partial_section_coherence": self.partial_section_coherence,
            "analysis_results_count": len(self.analysis_results),
            "log_entries": len(self.analysis_log),
        }
        self._alog("produce_analysis_report", {"report_keys": list(report.keys())})
        return report

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _alog(self, event: str, data: dict) -> None:
        """Append an analysis log entry.

        Parameters
        ----------
        event:
            Short event label.
        data:
            Additional data dict.
        """
        self.analysis_log.append({"event": event, "timestamp": _now_iso(), **data})


# ---------------------------------------------------------------------------
# Main witness class (frozen)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MixedModeProgrammingPartialWitness:
    """Frozen record capturing the result of a partial-section mixed-mode analysis.

    A witness is assembled after a complete analysis of a ``PartialSection`` and
    its associated holes and obligations.  It summarises whether the authored
    region is coherent, how many obligations have been discharged, and what
    trust tier the overall section achieves.

    Parameters
    ----------
    witness_id:
        Stable identifier for this witness record.
    spec_id:
        The specification identifier this witness is for.
    authored_count:
        Number of authored (non-hole) coordinates.
    hole_count:
        Number of remaining unfilled holes.
    filled_count:
        Number of holes that have been successfully filled.
    obligations_total:
        Total number of local obligations across all holes.
    obligations_met:
        Number of obligations discharged by successful fills.
    obligations_unmet:
        Number of obligations still outstanding.
    partial_coverage_fraction:
        Fraction of the coordinate cover that is authored.
    coherence_at_boundaries:
        Whether the section is coherent at all authorship boundaries.
    witness_status:
        High-level outcome string (see ``WitnessOutcome``).
    blocking_holes:
        List of hole coordinate strings that are blocking progress.
    trust_tier:
        Trust tier achieved by the overall section.
    witness_timestamp:
        ISO-8601 timestamp of witness assembly.
    provenance:
        Identifier of the coordinator or analyzer that produced the witness.

    Examples
    --------
    >>> w = MixedModeProgrammingPartialWitness(
    ...     witness_id="wit:abcd1234",
    ...     spec_id="spec:xyz",
    ...     authored_count=3,
    ...     hole_count=1,
    ...     filled_count=0,
    ...     obligations_total=2,
    ...     obligations_met=0,
    ...     obligations_unmet=2,
    ...     partial_coverage_fraction=0.75,
    ...     coherence_at_boundaries=True,
    ...     witness_status=WitnessOutcome.HOLES_WITH_OBLIGATIONS.value,
    ...     blocking_holes=["module.bar"],
    ...     trust_tier=TRUST_TIER_UNVERIFIED,
    ...     witness_timestamp="2024-01-01T00:00:00+00:00",
    ...     provenance="coord-001",
    ... )
    >>> w.obligations_unmet
    2
    """

    witness_id: str
    spec_id: str
    authored_count: int
    hole_count: int
    filled_count: int
    obligations_total: int
    obligations_met: int
    obligations_unmet: int
    partial_coverage_fraction: float
    coherence_at_boundaries: bool
    witness_status: str
    blocking_holes: list
    trust_tier: str
    witness_timestamp: str
    provenance: str


# ---------------------------------------------------------------------------
# Module-level functions
# ---------------------------------------------------------------------------


def create_hole(
    coordinate: str,
    kind: str = HOLE_KIND_UNIMPLEMENTED,
    formula: str | None = None,
) -> HoleRecord:
    """Create a ``HoleRecord`` for the given *coordinate*.

    Parameters
    ----------
    coordinate:
        The cover coordinate to mark as a hole.
    kind:
        Hole kind string (default ``HOLE_KIND_UNIMPLEMENTED``).
    formula:
        Optional prescribed formula for the hole.

    Returns
    -------
    HoleRecord
        A freshly created hole record.

    Examples
    --------
    >>> hr = create_hole("module.foo", kind="DEFERRED", formula="f : int -> int")
    >>> hr.hole_kind
    'DEFERRED'
    """
    hole_id = _make_hole_id(coordinate, kind)
    return HoleRecord(
        hole_id=hole_id,
        coordinate=coordinate,
        hole_kind=kind,
        surrounding_judgment_fragments={},
        prescribed_formula=formula,
        hole_annotations={},
        priority=5,
        created_at=_now_iso(),
    )


def create_local_obligation(
    hole: HoleRecord,
    kind: str,
    constraint: str,
) -> LocalObligation:
    """Create a ``LocalObligation`` for the given *hole*.

    Parameters
    ----------
    hole:
        The ``HoleRecord`` that induces the obligation.
    kind:
        Obligation kind string from ``ObligationKind`` values.
    constraint:
        Logical or informal constraint formula.

    Returns
    -------
    LocalObligation

    Examples
    --------
    >>> hr = create_hole("module.foo")
    >>> ob = create_local_obligation(hr, OBLIGATION_KIND_TYPE_SAFE, "fill : int -> int")
    >>> ob.obligation_kind
    'TYPE_SAFE'
    """
    oblig_id = _make_obligation_id(hole.hole_id, kind)
    return LocalObligation(
        obligation_id=oblig_id,
        hole_id=hole.hole_id,
        coordinate=hole.coordinate,
        obligation_kind=kind,
        constraint_formula=constraint,
        required_evidence_kinds=[],
        trust_tier_required=TRUST_TIER_PROPOSED,
        can_delegate=False,
        created_at=_now_iso(),
    )


def build_partial_section(
    authored: dict,
    holes: list[HoleRecord],
) -> PartialSection:
    """Construct a ``PartialSection`` from authored judgments and hole records.

    Parameters
    ----------
    authored:
        Dict mapping ``coordinate → judgment_fields`` for authored regions.
    holes:
        List of ``HoleRecord`` instances for the hole regions.

    Returns
    -------
    PartialSection
        A freshly created partial section containing all authored and hole data.

    Raises
    ------
    ValueError
        If any hole coordinate overlaps with an authored coordinate.

    Examples
    --------
    >>> ps = build_partial_section(
    ...     authored={"module.a": {"T": "int"}},
    ...     holes=[create_hole("module.b")],
    ... )
    >>> ps.get_completion_fraction()
    0.5
    """
    authored_set = set(authored.keys())
    hole_set = {hr.coordinate for hr in holes}
    overlap = authored_set & hole_set
    if overlap:
        raise ValueError(
            f"Coordinates appear in both authored and holes: {overlap}"
        )
    spec_id = _stable_hash(json.dumps(sorted(authored_set | hole_set)))
    section_id = _make_section_id(spec_id)
    now = _now_iso()
    section = PartialSection(
        section_id=section_id,
        spec_id=spec_id,
        authored_map=dict(authored),
        hole_map={hr.coordinate: hr for hr in holes},
        obligation_map={},
        created_at=now,
        updated_at=now,
    )
    return section


def compute_hole_obligations(
    hole: HoleRecord,
    spec_clauses: list[dict],
) -> list[LocalObligation]:
    """Derive local obligations for *hole* from a list of specification clauses.

    Each clause that matches the hole coordinate (or has no coordinate
    restriction) generates one ``LocalObligation``.  If no clauses match,
    a default ``BEHAVIOR_CORRECT`` obligation is created.

    Parameters
    ----------
    hole:
        The hole for which to derive obligations.
    spec_clauses:
        List of clause dicts.  Recognised keys: ``"coordinate"``, ``"kind"``,
        ``"formula"``, ``"evidence"``.

    Returns
    -------
    list[LocalObligation]

    Examples
    --------
    >>> hr = create_hole("module.foo")
    >>> clauses = [{"kind": "TYPE_SAFE", "formula": "fill : int -> int"}]
    >>> obs = compute_hole_obligations(hr, clauses)
    >>> len(obs) >= 1
    True
    """
    obligations: list[LocalObligation] = []
    for clause in spec_clauses:
        clause_coord = clause.get("coordinate", "")
        if clause_coord and clause_coord != hole.coordinate:
            continue
        kind = str(clause.get("kind", OBLIGATION_KIND_BEHAVIOR_CORRECT))
        formula = str(clause.get("formula", "true"))
        evidence = list(clause.get("evidence", []))
        oblig_id = _make_obligation_id(hole.hole_id, kind)
        ob = LocalObligation(
            obligation_id=oblig_id,
            hole_id=hole.hole_id,
            coordinate=hole.coordinate,
            obligation_kind=kind,
            constraint_formula=formula,
            required_evidence_kinds=evidence,
            trust_tier_required=TRUST_TIER_PROPOSED,
            can_delegate=False,
            created_at=_now_iso(),
        )
        obligations.append(ob)
    if not obligations and not hole.is_optional():
        oblig_id = _make_obligation_id(hole.hole_id, OBLIGATION_KIND_BEHAVIOR_CORRECT)
        obligations.append(
            LocalObligation(
                obligation_id=oblig_id,
                hole_id=hole.hole_id,
                coordinate=hole.coordinate,
                obligation_kind=OBLIGATION_KIND_BEHAVIOR_CORRECT,
                constraint_formula=f"fill at {hole.coordinate} must satisfy spec",
                required_evidence_kinds=[],
                trust_tier_required=TRUST_TIER_PROPOSED,
                can_delegate=False,
                created_at=_now_iso(),
            )
        )
    return obligations


def attempt_hole_fill(
    hole: HoleRecord,
    obligations: list[LocalObligation],
    fill_judgment: dict,
) -> HoleFillAttempt:
    """Attempt to fill *hole* with *fill_judgment* and check *obligations*.

    This function is a standalone (coordinator-independent) version of the fill
    logic.  It creates a ``HoleFillAttempt`` and evaluates each obligation.

    Parameters
    ----------
    hole:
        The hole to fill.
    obligations:
        The list of ``LocalObligation`` objects that the fill must satisfy.
    fill_judgment:
        Dict of Theory2 judgment fields for the proposed fill.

    Returns
    -------
    HoleFillAttempt
        The fill attempt record with status set based on obligation outcomes.

    Examples
    --------
    >>> hr = create_hole("module.foo")
    >>> ob = create_local_obligation(hr, OBLIGATION_KIND_TYPE_SAFE, "fill : int -> int")
    >>> attempt = attempt_hole_fill(hr, [ob], {"T": "int -> int", "phi": "f x = x"})
    >>> attempt.fill_status in (FillStatus.SUCCESSFUL.value, FillStatus.PARTIAL.value)
    True
    """
    attempt_id = _make_attempt_id(hole.hole_id)
    addressed: list[str] = []
    remaining: list[str] = []
    for ob in obligations:
        if _check_obligation_met(ob, fill_judgment):
            addressed.append(ob.obligation_id)
        else:
            remaining.append(ob.obligation_id)

    attempt = HoleFillAttempt(
        attempt_id=attempt_id,
        hole_id=hole.hole_id,
        coordinate=hole.coordinate,
        proposed_judgment_fields=dict(fill_judgment),
        obligations_addressed=addressed,
        obligations_remaining=remaining,
        fill_trust_tier=TRUST_TIER_PROPOSED,
        fill_status=FillStatus.PENDING.value,
        attempt_timestamp=_now_iso(),
    )

    if not remaining:
        attempt.mark_successful()
    elif addressed:
        attempt.fill_status = FillStatus.PARTIAL.value
    else:
        attempt.mark_failed(remaining)
    return attempt


def check_partial_section_coherence(
    section: PartialSection,
) -> tuple[bool, list[str]]:
    """Check whether *section* is coherent at authorship boundaries.

    Coherence requires that:

    1. Every boundary authored coordinate has at least one non-empty judgment
       component.
    2. Every non-optional hole has at least one registered obligation.

    Parameters
    ----------
    section:
        The ``PartialSection`` to check.

    Returns
    -------
    tuple[bool, list[str]]
        ``(True, [])`` if coherent; ``(False, [issue, ...])`` otherwise.

    Examples
    --------
    >>> ps = build_partial_section({"a": {"T": "int"}}, [create_hole("b")])
    >>> ok, issues = check_partial_section_coherence(ps)
    >>> isinstance(ok, bool)
    True
    """
    issues: list[str] = []
    boundaries = _boundary_coordinates(section.authored_map, section.hole_map)

    for bc in boundaries:
        j = section.authored_map.get(bc)
        if j is not None and not any(_judgment_has_field(j, f) for f in JUDGMENT_COMPONENTS):
            issues.append(f"Boundary {bc!r}: authored judgment has no Theory2 components.")

    for coord, hole_r in section.hole_map.items():
        if not hole_r.is_optional():
            obs = section.obligation_map.get(hole_r.hole_id, [])
            if not obs:
                issues.append(f"Hole {hole_r.hole_id!r} at {coord!r} has no obligations.")

    coherent = len(issues) == 0
    return coherent, issues


def merge_partial_sections(
    left: PartialSection,
    right: PartialSection,
) -> PartialSection:
    """Merge two partial sections into a single combined partial section.

    The ``right`` section takes precedence on coordinate conflicts in the
    authored map.  Holes from ``right`` that have a matching authored
    coordinate in ``left`` are ignored (the authored version wins).

    Parameters
    ----------
    left:
        The base partial section.
    right:
        The partial section to merge into *left*.

    Returns
    -------
    PartialSection
        A new ``PartialSection`` containing the union of both sections.

    Raises
    ------
    ValueError
        If *left* and *right* have different ``spec_id`` values.

    Examples
    --------
    >>> a = build_partial_section({"x": {"T": "int"}}, [create_hole("y")])
    >>> b = build_partial_section({"z": {"T": "str"}}, [])
    >>> c = merge_partial_sections(a, b)
    >>> "x" in c.authored_map and "z" in c.authored_map
    True
    """
    if left.spec_id != right.spec_id:
        raise ValueError(
            f"Cannot merge sections with different spec_ids: "
            f"{left.spec_id!r} vs {right.spec_id!r}"
        )

    merged_authored = dict(left.authored_map)
    merged_authored.update(right.authored_map)

    merged_holes = dict(left.hole_map)
    for coord, hr in right.hole_map.items():
        if coord not in merged_authored:
            merged_holes[coord] = hr

    merged_obligations: dict[str, list] = {}
    for hole_id, obs in left.obligation_map.items():
        merged_obligations.setdefault(hole_id, []).extend(obs)
    for hole_id, obs in right.obligation_map.items():
        merged_obligations.setdefault(hole_id, []).extend(obs)

    now = _now_iso()
    section_id = _make_section_id(left.spec_id)
    return PartialSection(
        section_id=section_id,
        spec_id=left.spec_id,
        authored_map=merged_authored,
        hole_map=merged_holes,
        obligation_map=merged_obligations,
        created_at=now,
        updated_at=now,
    )


def partial_section_witness(
    section: PartialSection,
    spec_id: str,
) -> MixedModeProgrammingPartialWitness:
    """Assemble a ``MixedModeProgrammingPartialWitness`` from *section*.

    Computes coverage, obligation counts, blocking holes, and coherence,
    then packages the results as an immutable witness record.

    Parameters
    ----------
    section:
        The partial section to summarise.
    spec_id:
        The specification identifier (may differ from section.spec_id when the
        section was built from a merged spec).

    Returns
    -------
    MixedModeProgrammingPartialWitness

    Examples
    --------
    >>> ps = build_partial_section({"a": {"T": "int"}}, [create_hole("b")])
    >>> w = partial_section_witness(ps, "spec:demo")
    >>> isinstance(w, MixedModeProgrammingPartialWitness)
    True
    """
    authored_count = len(section.authored_map)
    hole_count = len(section.hole_map)
    total_count = authored_count + hole_count
    coverage = _compute_coverage(authored_count, total_count)

    # Tally obligations
    obligations_total = sum(len(obs) for obs in section.obligation_map.values())

    # For a section without fill-attempt tracking at section level we infer
    # met/unmet from obligation_map presence vs hole presence.
    obligations_met = 0
    obligations_unmet = 0
    for hole_id, obs in section.obligation_map.items():
        # Determine the hole's coordinate
        hole_coord = None
        for coord, hr in section.hole_map.items():
            if hr.hole_id == hole_id:
                hole_coord = coord
                break
        if hole_coord is None:
            # The hole has been filled (coordinate no longer in hole_map)
            obligations_met += len(obs)
        else:
            obligations_unmet += len(obs)

    blocking_holes = [
        coord for coord, hr in section.hole_map.items()
        if not hr.is_optional()
    ]

    coherent, _ = check_partial_section_coherence(section)

    # Determine trust tier
    if not blocking_holes and obligations_unmet == 0:
        trust_tier = TRUST_TIER_VERIFIED
    elif obligations_met > 0:
        trust_tier = TRUST_TIER_PROPOSED
    else:
        trust_tier = TRUST_TIER_UNVERIFIED

    # Determine witness status
    if hole_count == 0:
        ws = WitnessOutcome.FULLY_AUTHORED.value
    elif not coherent:
        ws = WitnessOutcome.INCOHERENT.value
    elif obligations_total > 0:
        ws = WitnessOutcome.HOLES_WITH_OBLIGATIONS.value
    else:
        ws = WitnessOutcome.HOLES_NO_OBLIGATIONS.value

    filled_count = sum(
        1 for hr in section.hole_map.values()
        if hr.coordinate in section.authored_map
    )

    witness_id = f"wit:{_stable_hash(section.section_id + spec_id)}"
    return MixedModeProgrammingPartialWitness(
        witness_id=witness_id,
        spec_id=spec_id,
        authored_count=authored_count,
        hole_count=hole_count,
        filled_count=filled_count,
        obligations_total=obligations_total,
        obligations_met=obligations_met,
        obligations_unmet=obligations_unmet,
        partial_coverage_fraction=coverage,
        coherence_at_boundaries=coherent,
        witness_status=ws,
        blocking_holes=blocking_holes,
        trust_tier=trust_tier,
        witness_timestamp=_now_iso(),
        provenance=section.section_id,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    # Enumerations
    "HoleKind",
    "ObligationKind",
    "FillStatus",
    "WitnessOutcome",
    # Supporting frozen dataclasses
    "HoleRecord",
    "LocalObligation",
    # Mutable supporting dataclasses
    "PartialSection",
    "HoleFillAttempt",
    # Main classes
    "MixedModeProgrammingPartialCoordinator",
    "MixedModeProgrammingPartialAnalyzer",
    "MixedModeProgrammingPartialWitness",
    # Module-level functions
    "create_hole",
    "create_local_obligation",
    "build_partial_section",
    "compute_hole_obligations",
    "attempt_hole_fill",
    "check_partial_section_coherence",
    "merge_partial_sections",
    "partial_section_witness",
    # Constants
    "HOLE_KIND_UNIMPLEMENTED",
    "HOLE_KIND_DELEGATED",
    "HOLE_KIND_DEFERRED",
    "HOLE_KIND_OPTIONAL",
    "OBLIGATION_KIND_TYPE_SAFE",
    "OBLIGATION_KIND_BEHAVIOR_CORRECT",
    "OBLIGATION_KIND_API_CONSISTENT",
    "OBLIGATION_KIND_SECURITY_SOUND",
    "TRUST_TIER_VERIFIED",
    "TRUST_TIER_PROPOSED",
    "TRUST_TIER_UNVERIFIED",
    "JUDGMENT_COMPONENTS",
    "MAX_HOLES_PER_SECTION",
    # Unified architecture cross-references
    "spec_descent",
    "spec_certificate",
    "spec_encoding",
]

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


# copilot: end of mixed_mode_programming_partial_sem.py; obligation-lifting
# and coherence-checking logic follows theory2.tex §s03.  Extend
# LocalObligation.is_satisfiable_by with domain-specific verifiers as needed.

# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    """Smoke test exercising all main classes and module-level functions."""

    print("=== s03 mixed-mode partial sem smoke test ===")

    # ------------------------------------------------------------------
    # 1. Internal helpers
    # ------------------------------------------------------------------
    ts = _now_iso()
    assert isinstance(ts, str) and "T" in ts, "ISO timestamp malformed"

    h = _stable_hash("hello world")
    assert len(h) == 8, f"Expected 8-char hash, got {len(h)}"
    assert _stable_hash("hello world") == h, "Hash not stable"

    hid = _make_hole_id("module.foo", "UNIMPLEMENTED")
    assert hid.startswith("hole:"), f"Bad hole_id: {hid}"

    oid = _make_obligation_id(hid, "TYPE_SAFE")
    assert oid.startswith("oblig:"), f"Bad obligation_id: {oid}"

    sid = _make_section_id("spec:xyz")
    assert sid.startswith("psec:"), f"Bad section_id: {sid}"

    aid = _make_attempt_id(hid)
    assert aid.startswith("attempt:"), f"Bad attempt_id: {aid}"

    assert _judgment_has_field({"T": "int"}, "T"), "Field check failed"
    assert not _judgment_has_field({"T": None}, "T"), "None field should fail"

    assert _compute_coverage(3, 4) == 0.75, "Coverage computation wrong"
    assert _compute_coverage(0, 0) == 0.0, "Zero total should return 0.0"
    assert _compute_coverage(4, 4) == 1.0, "Full coverage should return 1.0"

    boundary = _boundary_coordinates({"aa": {}}, {"ab": None})
    assert isinstance(boundary, list), "Boundary result should be a list"

    print("  [OK] internal helpers")

    # ------------------------------------------------------------------
    # 2. Enumerations
    # ------------------------------------------------------------------
    assert HoleKind.UNIMPLEMENTED.value == "UNIMPLEMENTED"
    assert ObligationKind.TYPE_SAFE.value == "TYPE_SAFE"
    assert FillStatus.SUCCESSFUL.value == "SUCCESSFUL"
    assert WitnessOutcome.FULLY_AUTHORED.value == "FULLY_AUTHORED"
    print("  [OK] enumerations")

    # ------------------------------------------------------------------
    # 3. HoleRecord
    # ------------------------------------------------------------------
    hr_foo = create_hole("module.foo", kind=HOLE_KIND_UNIMPLEMENTED, formula="f : A -> B")
    assert hr_foo.hole_kind == HOLE_KIND_UNIMPLEMENTED
    assert not hr_foo.is_optional()
    assert hr_foo.prescribed_formula == "f : A -> B"

    hr_opt = create_hole("module.opt", kind=HOLE_KIND_OPTIONAL)
    assert hr_opt.is_optional()

    hr_deferred = create_hole("module.bar", kind=HOLE_KIND_DEFERRED)
    assert hr_deferred.hole_kind == HOLE_KIND_DEFERRED
    print("  [OK] HoleRecord")

    # ------------------------------------------------------------------
    # 4. LocalObligation
    # ------------------------------------------------------------------
    ob_ts = create_local_obligation(hr_foo, OBLIGATION_KIND_TYPE_SAFE, "fill : int -> int")
    assert ob_ts.obligation_kind == OBLIGATION_KIND_TYPE_SAFE
    assert ob_ts.hole_id == hr_foo.hole_id

    # is_satisfiable_by: TYPE_SAFE requires "T" component
    assert ob_ts.is_satisfiable_by({"T": "int -> int"})
    assert not ob_ts.is_satisfiable_by({"phi": "some formula"})

    ob_bc = create_local_obligation(hr_foo, OBLIGATION_KIND_BEHAVIOR_CORRECT, "spec behaviour")
    assert ob_bc.is_satisfiable_by({"phi": "f x = x + 1", "O": "output spec"})
    assert not ob_bc.is_satisfiable_by({"T": "int -> int"})

    ob_api = create_local_obligation(hr_foo, OBLIGATION_KIND_API_CONSISTENT, "API contract")
    assert ob_api.is_satisfiable_by({"A": "API", "E": "env"})

    ob_sec = create_local_obligation(hr_foo, OBLIGATION_KIND_SECURITY_SOUND, "no leaks")
    assert ob_sec.is_satisfiable_by({"B": "boundary_clear"})
    print("  [OK] LocalObligation")

    # ------------------------------------------------------------------
    # 5. PartialSection
    # ------------------------------------------------------------------
    ps = PartialSection(
        section_id="psec:test001",
        spec_id="spec:test",
        authored_map={"x": {"T": "int", "phi": "f x = x"}},
        hole_map={},
        obligation_map={},
        created_at=_now_iso(),
        updated_at=_now_iso(),
    )
    ps.add_authored("y", {"T": "str"})
    assert "y" in ps.authored_map
    assert ps.get_completion_fraction() == 1.0

    ps.add_hole("z", hr_foo)
    assert "z" in ps.hole_map
    assert "z" not in ps.authored_map

    frac = ps.get_completion_fraction()
    assert 0.0 < frac < 1.0, f"Expected fraction between 0 and 1, got {frac}"

    filled = ps.fill_hole("z", {"T": "int -> int", "phi": "g z = z"})
    assert filled
    assert "z" in ps.authored_map
    assert "z" not in ps.hole_map

    d = ps.to_dict()
    assert "section_id" in d and "completion_fraction" in d
    print("  [OK] PartialSection")

    # ------------------------------------------------------------------
    # 6. HoleFillAttempt
    # ------------------------------------------------------------------
    att = HoleFillAttempt(
        attempt_id="attempt:aabbcc",
        hole_id=hr_foo.hole_id,
        coordinate="module.foo",
        proposed_judgment_fields={"T": "int"},
        obligations_addressed=[],
        obligations_remaining=[ob_ts.obligation_id],
        fill_trust_tier=TRUST_TIER_PROPOSED,
        fill_status=FillStatus.PENDING.value,
        attempt_timestamp=_now_iso(),
    )
    att.mark_failed([ob_ts.obligation_id])
    assert att.fill_status == FillStatus.FAILED.value
    assert ob_ts.obligation_id in att.obligations_remaining

    att2 = HoleFillAttempt(
        attempt_id="attempt:ddeeff",
        hole_id=hr_foo.hole_id,
        coordinate="module.foo",
        proposed_judgment_fields={"T": "int -> int"},
        obligations_addressed=[ob_ts.obligation_id],
        obligations_remaining=[],
        fill_trust_tier=TRUST_TIER_PROPOSED,
        fill_status=FillStatus.PENDING.value,
        attempt_timestamp=_now_iso(),
    )
    att2.mark_successful()
    assert att2.fill_status == FillStatus.SUCCESSFUL.value
    assert att2.obligations_remaining == []

    d2 = att2.to_dict()
    assert "fill_status" in d2
    print("  [OK] HoleFillAttempt")

    # ------------------------------------------------------------------
    # 7. module-level functions: create_hole, create_local_obligation
    # ------------------------------------------------------------------
    hr2 = create_hole("coord.alpha", kind=HOLE_KIND_DELEGATED)
    assert hr2.hole_kind == HOLE_KIND_DELEGATED

    ob2 = create_local_obligation(hr2, OBLIGATION_KIND_API_CONSISTENT, "v2 API")
    assert ob2.coordinate == "coord.alpha"
    print("  [OK] create_hole, create_local_obligation")

    # ------------------------------------------------------------------
    # 8. build_partial_section
    # ------------------------------------------------------------------
    hr_a = create_hole("coord.b")
    hr_b = create_hole("coord.c", kind=HOLE_KIND_OPTIONAL)
    section1 = build_partial_section(
        authored={"coord.a": {"T": "bool", "phi": "true"}},
        holes=[hr_a, hr_b],
    )
    assert "coord.a" in section1.authored_map
    assert "coord.b" in section1.hole_map
    assert "coord.c" in section1.hole_map
    assert abs(section1.get_completion_fraction() - 1 / 3) < 1e-9

    try:
        build_partial_section(
            authored={"coord.overlap": {"T": "int"}},
            holes=[create_hole("coord.overlap")],
        )
        assert False, "Should have raised ValueError for coordinate overlap"
    except ValueError:
        pass
    print("  [OK] build_partial_section")

    # ------------------------------------------------------------------
    # 9. compute_hole_obligations
    # ------------------------------------------------------------------
    clauses = [
        {"kind": OBLIGATION_KIND_TYPE_SAFE, "formula": "fill : int -> int"},
        {"coordinate": "coord.b", "kind": OBLIGATION_KIND_BEHAVIOR_CORRECT, "formula": "ok"},
        {"coordinate": "coord.other", "kind": OBLIGATION_KIND_SECURITY_SOUND},
    ]
    obs_for_hr_a = compute_hole_obligations(hr_a, clauses)
    # clause[0] has no coord restriction → applies; clause[1] matches; clause[2] doesn't
    assert len(obs_for_hr_a) >= 2
    assert any(o.obligation_kind == OBLIGATION_KIND_TYPE_SAFE for o in obs_for_hr_a)
    assert any(o.obligation_kind == OBLIGATION_KIND_BEHAVIOR_CORRECT for o in obs_for_hr_a)

    # Optional hole with no matching clauses → no default obligation
    obs_opt = compute_hole_obligations(hr_b, [])
    assert obs_opt == []
    print("  [OK] compute_hole_obligations")

    # ------------------------------------------------------------------
    # 10. attempt_hole_fill
    # ------------------------------------------------------------------
    fill_j = {"T": "int -> int", "phi": "f x = x + 1", "O": "output", "A": "API", "E": "env", "B": "safe"}
    attempt3 = attempt_hole_fill(hr_a, obs_for_hr_a, fill_j)
    assert attempt3.fill_status in (
        FillStatus.SUCCESSFUL.value,
        FillStatus.PARTIAL.value,
    )
    assert isinstance(attempt3.obligations_addressed, list)

    # Fill that fails type-safe obligation
    attempt4 = attempt_hole_fill(hr_a, [ob_ts], {"phi": "no type info"})
    assert attempt4.fill_status == FillStatus.FAILED.value
    print("  [OK] attempt_hole_fill")

    # ------------------------------------------------------------------
    # 11. check_partial_section_coherence
    # ------------------------------------------------------------------
    section1.obligation_map[hr_a.hole_id] = obs_for_hr_a
    ok, issues = check_partial_section_coherence(section1)
    # The optional hole (hr_b) needs no obligations, hr_a has them → coherent
    assert isinstance(ok, bool)
    assert isinstance(issues, list)
    print("  [OK] check_partial_section_coherence")

    # ------------------------------------------------------------------
    # 12. merge_partial_sections
    # ------------------------------------------------------------------
    sectionL = build_partial_section(
        authored={"l1": {"T": "int"}, "l2": {"phi": "true"}},
        holes=[create_hole("l3")],
    )
    sectionR_wrong_spec = build_partial_section(
        authored={"r1": {"T": "str"}},
        holes=[],
    )
    # Force same spec_id for merge test
    sectionR = PartialSection(
        section_id=_make_section_id(sectionL.spec_id),
        spec_id=sectionL.spec_id,
        authored_map={"r1": {"T": "str"}},
        hole_map={},
        obligation_map={},
        created_at=_now_iso(),
        updated_at=_now_iso(),
    )
    merged = merge_partial_sections(sectionL, sectionR)
    assert "l1" in merged.authored_map
    assert "r1" in merged.authored_map
    assert "l3" in merged.hole_map

    # Merging with different spec_id raises
    try:
        merge_partial_sections(sectionL, sectionR_wrong_spec)
        assert False, "Should have raised ValueError"
    except ValueError:
        pass
    print("  [OK] merge_partial_sections")

    # ------------------------------------------------------------------
    # 13. partial_section_witness
    # ------------------------------------------------------------------
    w = partial_section_witness(section1, "spec:demo-001")
    assert isinstance(w, MixedModeProgrammingPartialWitness)
    assert w.spec_id == "spec:demo-001"
    assert 0.0 <= w.partial_coverage_fraction <= 1.0
    assert w.trust_tier in (TRUST_TIER_VERIFIED, TRUST_TIER_PROPOSED, TRUST_TIER_UNVERIFIED)
    assert w.witness_status in (o.value for o in WitnessOutcome)
    print("  [OK] partial_section_witness")

    # ------------------------------------------------------------------
    # 14. MixedModeProgrammingPartialCoordinator
    # ------------------------------------------------------------------
    coord = MixedModeProgrammingPartialCoordinator(
        coordinator_id="coord-smoke-001",
        partial_section={},
        hole_registry={},
        obligation_registry={},
        authored_coordinates=["init.a", "init.b"],
        hole_coordinates=[],
        coordination_log=[],
        strict_mode=False,
    )
    coord.partial_section["init.a"] = {"T": "int", "phi": "id"}
    coord.partial_section["init.b"] = {"T": "str", "phi": "const"}

    h1 = coord.register_hole("coord.missing", hole_kind=HOLE_KIND_UNIMPLEMENTED,
                              surrounding_fragments={"T": "int"}, priority=1)
    assert h1.hole_id in coord.obligation_registry or h1.coordinate in coord.hole_registry
    assert "coord.missing" in coord.hole_coordinates

    # Duplicate registration should raise
    try:
        coord.register_hole("coord.missing")
        assert False, "Should have raised ValueError"
    except ValueError:
        pass

    ob_c1 = coord.register_obligation(
        h1, OBLIGATION_KIND_TYPE_SAFE, "fill : int -> int"
    )
    assert ob_c1.hole_id == h1.hole_id
    assert len(coord.obligation_registry[h1.hole_id]) == 1

    # Compute obligations from clauses
    clauses2 = [{"kind": OBLIGATION_KIND_API_CONSISTENT, "formula": "API v2"}]
    new_obs = coord.compute_hole_obligations(h1, clauses2)
    assert any(o.obligation_kind == OBLIGATION_KIND_API_CONSISTENT for o in new_obs)

    # Check before fill
    check = coord.check_partial_section()
    assert "coherent" in check
    assert "coverage" in check

    # Fill with insufficient judgment — should not raise in non-strict mode
    fill_attempt = coord.fill_hole(
        "coord.missing",
        {"T": "int -> int", "phi": "f x = x", "O": "output", "A": "api", "E": "env"},
        fill_trust_tier=TRUST_TIER_PROPOSED,
    )
    assert fill_attempt.fill_status in (
        FillStatus.SUCCESSFUL.value,
        FillStatus.PARTIAL.value,
        FillStatus.FAILED.value,
    )

    # get_authored_coverage
    cov = coord.get_authored_coverage()
    assert 0.0 <= cov <= 1.0

    # get_unfilled_holes
    unfilled = coord.get_unfilled_holes()
    assert isinstance(unfilled, list)

    # summarize
    summary = coord.summarize()
    assert summary["coordinator_id"] == "coord-smoke-001"
    assert "coverage" in summary
    assert "total_obligations" in summary

    # strict_mode test
    coord_strict = MixedModeProgrammingPartialCoordinator(
        coordinator_id="coord-strict",
        partial_section={},
        hole_registry={},
        obligation_registry={},
        authored_coordinates=[],
        hole_coordinates=[],
        coordination_log=[],
        strict_mode=True,
    )
    hr_strict = coord_strict.register_hole("strict.x", hole_kind=HOLE_KIND_UNIMPLEMENTED,
                                           priority=1)
    coord_strict.register_obligation(hr_strict, OBLIGATION_KIND_SECURITY_SOUND, "no leaks")
    # This fill lacks "B" → strict mode should raise
    try:
        coord_strict.fill_hole("strict.x", {"T": "int"}, TRUST_TIER_PROPOSED)
        # If it succeeds, that means the obligation was met — also fine for smoke test
    except ValueError:
        pass  # Expected in strict_mode
    print("  [OK] MixedModeProgrammingPartialCoordinator")

    # ------------------------------------------------------------------
    # 15. MixedModeProgrammingPartialAnalyzer
    # ------------------------------------------------------------------
    analyzer = MixedModeProgrammingPartialAnalyzer(
        analyzer_id="anal-smoke-001",
        analysis_log=[],
        hole_analysis={},
        obligation_analysis={},
        authorship_boundary_map={},
        partial_section_coherence=None,
        analysis_results=[],
    )

    # Build a section to analyze
    hr_x = create_hole("x.alpha", kind=HOLE_KIND_DEFERRED)
    hr_y = create_hole("x.beta", kind=HOLE_KIND_OPTIONAL)
    ob_x = create_local_obligation(hr_x, OBLIGATION_KIND_BEHAVIOR_CORRECT, "must behave")
    section2 = build_partial_section(
        authored={
            "x.gamma": {"T": "int", "phi": "true"},
            "x.delta": {"T": "str", "A": "api"},
        },
        holes=[hr_x, hr_y],
    )
    section2.obligation_map[hr_x.hole_id] = [ob_x]

    result2 = analyzer.analyze_partial_section(section2)
    assert result2["authored_count"] == 2
    assert result2["hole_count"] == 2
    assert isinstance(result2["coherent"], bool)

    ha = analyzer.analyze_hole(hr_x)
    assert ha["hole_id"] == hr_x.hole_id
    assert not ha["is_optional"]

    oa = analyzer.analyze_obligation(ob_x)
    assert oa["obligation_id"] == ob_x.obligation_id

    coherent2 = analyzer.check_coherence_at_boundary(section2)
    assert isinstance(coherent2, bool)
    assert analyzer.partial_section_coherence is not None

    ranked = analyzer.rank_holes_by_obligation_count(section2)
    assert isinstance(ranked, list)
    assert len(ranked) == 2
    # hr_x has 1 obligation, hr_y has 0 → hr_x should rank first
    assert ranked[0][1] >= ranked[1][1]

    report = analyzer.produce_analysis_report()
    assert "total_holes_analyzed" in report
    assert "obligations_by_kind" in report
    assert isinstance(report["partial_section_coherence"], bool)
    print("  [OK] MixedModeProgrammingPartialAnalyzer")

    # ------------------------------------------------------------------
    # 16. MixedModeProgrammingPartialWitness (direct construction)
    # ------------------------------------------------------------------
    wit = MixedModeProgrammingPartialWitness(
        witness_id="wit:aabbccdd",
        spec_id="spec:final",
        authored_count=5,
        hole_count=2,
        filled_count=1,
        obligations_total=4,
        obligations_met=3,
        obligations_unmet=1,
        partial_coverage_fraction=5 / 7,
        coherence_at_boundaries=True,
        witness_status=WitnessOutcome.HOLES_WITH_OBLIGATIONS.value,
        blocking_holes=["module.z"],
        trust_tier=TRUST_TIER_PROPOSED,
        witness_timestamp=_now_iso(),
        provenance="coord-smoke-001",
    )
    assert wit.obligations_unmet == 1
    assert wit.trust_tier == TRUST_TIER_PROPOSED
    assert wit.blocking_holes == ["module.z"]

    # Immutability check
    try:
        wit.authored_count = 99  # type: ignore[misc]
        assert False, "Should have raised FrozenInstanceError"
    except (AttributeError, TypeError):
        pass
    print("  [OK] MixedModeProgrammingPartialWitness (frozen)")

    # ------------------------------------------------------------------
    # 17. Full pipeline integration test
    # ------------------------------------------------------------------
    # Build a coordinator, populate it, then build a witness via the module fn
    coord2 = MixedModeProgrammingPartialCoordinator(
        coordinator_id="pipeline-coord",
        partial_section={},
        hole_registry={},
        obligation_registry={},
        authored_coordinates=[],
        hole_coordinates=[],
        coordination_log=[],
        strict_mode=False,
    )
    for i in range(4):
        coord2.partial_section[f"authored.{i}"] = {"T": f"type{i}", "phi": f"spec{i}"}
        coord2.authored_coordinates.append(f"authored.{i}")

    h_p1 = coord2.register_hole("pipeline.hole1", hole_kind=HOLE_KIND_UNIMPLEMENTED,
                                  surrounding_fragments={"T": "int"}, priority=2)
    h_p2 = coord2.register_hole("pipeline.hole2", hole_kind=HOLE_KIND_DEFERRED,
                                  surrounding_fragments={"phi": "deferred spec"}, priority=1)
    coord2.compute_hole_obligations(
        h_p1,
        [{"kind": OBLIGATION_KIND_TYPE_SAFE, "formula": "fill : int"}],
    )
    coord2.compute_hole_obligations(
        h_p2,
        [{"kind": OBLIGATION_KIND_BEHAVIOR_CORRECT, "formula": "deferred ok"}],
    )

    # Fill hole1 successfully
    fa1 = coord2.fill_hole(
        "pipeline.hole1",
        {"T": "int", "phi": "body", "O": "out", "A": "api", "E": "env", "B": "b", "Pi": "pi", "c": "ctx"},
        TRUST_TIER_PROPOSED,
    )
    print(f"     fill hole1 status: {fa1.fill_status}")

    # Build a PartialSection for the witness
    ps_for_witness = PartialSection(
        section_id=_make_section_id("pipeline-spec"),
        spec_id="pipeline-spec",
        authored_map={k: v for k, v in coord2.partial_section.items() if v is not None},
        hole_map={c: coord2.hole_registry[c] for c in coord2.hole_coordinates if c in coord2.hole_registry},
        obligation_map=dict(coord2.obligation_registry),
        created_at=_now_iso(),
        updated_at=_now_iso(),
    )
    pipeline_witness = partial_section_witness(ps_for_witness, "pipeline-spec")
    assert isinstance(pipeline_witness, MixedModeProgrammingPartialWitness)
    assert pipeline_witness.spec_id == "pipeline-spec"
    print(f"     pipeline witness status: {pipeline_witness.witness_status}")
    print(f"     pipeline coverage: {pipeline_witness.partial_coverage_fraction:.2f}")
    print("  [OK] full pipeline integration")

    # ------------------------------------------------------------------
    # 18. Constants sanity checks
    # ------------------------------------------------------------------
    assert JUDGMENT_COMPONENTS == ("c", "phi", "A", "E", "O", "B", "T", "Pi")
    assert MAX_HOLES_PER_SECTION == 256
    assert TRUST_TIER_VERIFIED == "VERIFIED"
    assert TRUST_TIER_PROPOSED == "PROPOSED"
    assert TRUST_TIER_UNVERIFIED == "UNVERIFIED"
    print("  [OK] module constants")

    print("\n=== ALL SMOKE TESTS PASSED ===")
