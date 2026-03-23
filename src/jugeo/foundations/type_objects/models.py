"""Core type-object model for JuGeo.

Theory2.tex Ch3 defines a **JuGeo type** as a coordinate-indexed semantic
object

    τ = (c, K, ρ, γ, supp, trust)

where:

- ``c`` is the coordinate (location in the semantic site)
- ``K`` is the type carrier (the underlying data structure / set of inhabitants)
- ``ρ: (f: c' → c) → Hom(K(c), K(c'))`` is the restriction/transport family
- ``γ: {(Kᵢ)|Uᵢ} → K`` is the gluing law (assembling global type from local pieces)
- ``supp ⊆ Obj(C)`` is the support (coordinates where the type is non-trivial)
- ``trust ∈ TrustLevel`` is the current confidence in this type assignment

This module provides six immutable value objects — ``TypeTrustAnnotation``,
``TypeCarrier``, ``TransportMap``, ``GluingLaw``, and ``JuGeoType`` — together
with the ``CarrierKind`` enumeration and a small set of private helper functions.

Each class satisfies:
- ``serialize() -> dict[str, Any]`` for JSON-friendly export.
- ``@classmethod parse(cls, data) -> T`` for reconstruction.
- All "mutations" return new instances via ``replace()`` from ``dataclasses``.

Unicode notation used in docstrings: ∧ ∨ ¬ → ≤ ⪯ τ φ ρ γ

Provenance
----------
MODULE_AUTHOR : str
    "copilot"
THEORY_REF : str
    "theory2.tex Ch3"
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping

from jugeo.errors import FailureScope, JuGeoError, StructuredFailure, raise_with_scope
from jugeo.geometry.site import (
    Coordinate,
    CoordinateKind,
    CoordinateObject,
    CoordinateMorphism,
    Morphism,
    MorphismKind,
    Site,
)
from jugeo.judgments.judgment_terms import Judgment, JudgmentStatus, Proposition, TrustLevel

__all__ = [
    "CarrierKind",
    "TypeTrustAnnotation",
    "TypeCarrier",
    "TransportMap",
    "GluingLaw",
    "JuGeoType",
]

# ---------------------------------------------------------------------------
# Module provenance constants
# ---------------------------------------------------------------------------

MODULE_AUTHOR: str = "copilot"
THEORY_REF: str = "theory2.tex Ch3"


# ---------------------------------------------------------------------------
# CarrierKind enumeration
# ---------------------------------------------------------------------------


class CarrierKind(str, Enum):
    """Classifies the structural nature of a type carrier K.

    Theory2.tex §3.2 distinguishes several carrier kinds that arise naturally
    when building the sheaf of semantic types over a JuGeo site.

    Attributes
    ----------
    PRIMITIVE
        K is an atomic/base type with no internal structure (e.g. ``int``,
        ``str``, a singleton set).  Primitive carriers have no dependencies and
        their constraints are purely syntactic.
    COMPOSITE
        K is formed by combining two or more other carriers (product, sum, or
        record type).  Composite carriers inherit constraints from their
        components.
    DEPENDENT
        K depends on a term — i.e. K(t) varies with a value t.  This is the
        Pi/Sigma type case.  Inhabitants must satisfy the dependency relation.
    INDEXED
        K is parametrised by an index set; each index selects a fibre carrier.
        Closely related to DEPENDENT but the index is a coordinate key rather
        than a term.
    QUOTIENT
        K is obtained from another carrier by modding out an equivalence
        relation.  Inhabitants are equivalence classes.
    EXTENSION
        K extends an existing carrier by adding new inhabitants and/or
        constraints (subtyping extension).
    """

    PRIMITIVE = "primitive"
    COMPOSITE = "composite"
    DEPENDENT = "dependent"
    INDEXED = "indexed"
    QUOTIENT = "quotient"
    EXTENSION = "extension"


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _assert_non_empty_str(value: str, field_name: str) -> None:
    """Raise ``ValueError`` if *value* is an empty or whitespace-only string.

    Parameters
    ----------
    value : str
        The string to check.
    field_name : str
        Name of the field, used in the error message.

    Raises
    ------
    ValueError
        If *value* is empty or contains only whitespace.
    """
    if not value or not value.strip():
        raise ValueError(f"Field '{field_name}' must be a non-empty string; got {value!r}.")


def _serialize_coordinate(coord: Coordinate) -> dict[str, Any]:
    """Serialise *coord* to a dictionary, delegating to ``coord.serialize()``.

    Parameters
    ----------
    coord : Coordinate
        The coordinate to serialise.

    Returns
    -------
    dict[str, Any]
        A JSON-friendly representation of *coord*.
    """
    return coord.serialize()


def _parse_coordinate(data: dict[str, Any]) -> Coordinate:
    """Reconstruct a ``Coordinate`` from a serialised dictionary.

    Parameters
    ----------
    data : dict[str, Any]
        A dictionary produced by ``Coordinate.serialize()``.

    Returns
    -------
    Coordinate
        The reconstructed coordinate.
    """
    return Coordinate.parse(data)


def _serialize_morphism(m: Morphism) -> dict[str, Any]:
    """Serialise *m* to a dictionary, delegating to ``m.serialize()``.

    Parameters
    ----------
    m : Morphism
        The morphism to serialise.

    Returns
    -------
    dict[str, Any]
        A JSON-friendly representation of *m*.
    """
    return m.serialize()


def _parse_morphism(data: dict[str, Any]) -> Morphism:
    """Reconstruct a ``Morphism`` from a serialised dictionary.

    Parameters
    ----------
    data : dict[str, Any]
        A dictionary produced by ``Morphism.serialize()``.

    Returns
    -------
    Morphism
        The reconstructed morphism.
    """
    return Morphism.parse(data)


def _trust_level_from_str(value: str | int) -> TrustLevel:
    """Coerce a string or integer to a ``TrustLevel``.

    Parameters
    ----------
    value : str | int
        Either the integer value of a ``TrustLevel`` member or its name
        (case-insensitive).

    Returns
    -------
    TrustLevel
        The matching ``TrustLevel`` member.

    Raises
    ------
    ValueError
        If *value* does not correspond to a valid ``TrustLevel``.
    """
    if isinstance(value, int):
        return TrustLevel(value)
    # Try name lookup (e.g. "VERIFIED_PROOF") and label lookup (e.g. "verified-proof")
    normalised = value.upper().replace("-", "_")
    try:
        return TrustLevel[normalised]
    except KeyError:
        # Fall back to integer parsing
        try:
            return TrustLevel(int(value))
        except (ValueError, KeyError):
            raise ValueError(f"Cannot coerce {value!r} to TrustLevel.")


# ---------------------------------------------------------------------------
# TypeTrustAnnotation
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TypeTrustAnnotation:
    """Trust metadata attached to a JuGeo type assignment.

    Records the current trust level for a type τ, the evidence keys that
    support it, when and by whom it was verified, and the rationale for the
    current trust level.

    In theory2.tex §3.5 trust is formalised as a monotone map from the
    verification chain lattice to ``TrustLevel``.  ``promote`` and ``demote``
    correspond to going up and down the lattice.

    Parameters
    ----------
    trust_level : TrustLevel
        The current confidence level for this type assignment.
    evidence_basis : tuple[str, ...]
        Keys or labels of evidence items that support the trust claim.
    verified_at : str | None
        ISO-8601 timestamp string recording when verification occurred, or
        ``None`` if not yet verified.
    verifier : str | None
        Identifier of the agent/system that performed verification, or ``None``.
    rationale : str
        Human-readable explanation for the current trust level.
    """

    trust_level: TrustLevel
    evidence_basis: tuple[str, ...]
    verified_at: str | None
    verifier: str | None
    rationale: str

    # ------------------------------------------------------------------
    # Trust-level queries
    # ------------------------------------------------------------------

    def is_verified(self) -> bool:
        """Return ``True`` if trust ≥ ``VERIFIED_PROOF``.

        A type is considered verified when it has been formally proved or
        discharged by a trusted solver with at least ``VERIFIED_PROOF`` trust.

        Returns
        -------
        bool
            ``True`` iff ``self.trust_level >= TrustLevel.VERIFIED_PROOF``.
        """
        return self.trust_level >= TrustLevel.VERIFIED_PROOF

    def is_proposed(self) -> bool:
        """Return ``True`` if trust is at ``ORACLE_PROPOSED`` level.

        A proposed type is one where an oracle or automated tool has suggested
        it but no independent verification has occurred.

        Returns
        -------
        bool
            ``True`` iff ``self.trust_level == TrustLevel.ORACLE_PROPOSED``.
        """
        return self.trust_level == TrustLevel.ORACLE_PROPOSED

    def is_contradicted(self) -> bool:
        """Return ``True`` if trust is ``CONTRADICTED``.

        A contradicted type assignment has been actively refuted.  It must not
        be used in downstream reasoning (¬ φ holds).

        Returns
        -------
        bool
            ``True`` iff ``self.trust_level == TrustLevel.CONTRADICTED``.
        """
        return self.trust_level == TrustLevel.CONTRADICTED

    def stronger_than(self, other: "TypeTrustAnnotation") -> bool:
        """Return ``True`` if this annotation's trust strictly exceeds *other*'s.

        Parameters
        ----------
        other : TypeTrustAnnotation
            The annotation to compare against.

        Returns
        -------
        bool
            ``True`` iff ``self.trust_level > other.trust_level``.
        """
        return self.trust_level > other.trust_level

    def weaker_than(self, other: "TypeTrustAnnotation") -> bool:
        """Return ``True`` if this annotation's trust is strictly below *other*'s.

        Parameters
        ----------
        other : TypeTrustAnnotation
            The annotation to compare against.

        Returns
        -------
        bool
            ``True`` iff ``self.trust_level < other.trust_level``.
        """
        return self.trust_level < other.trust_level

    # ------------------------------------------------------------------
    # Mutation helpers (return new instances)
    # ------------------------------------------------------------------

    def promote(self, new_level: TrustLevel, rationale: str) -> "TypeTrustAnnotation":
        """Return a new annotation at *new_level*, provided it is strictly higher.

        Trust monotonicity (theory2.tex §3.5): trust can only move upward
        through deliberate promotion by a verified agent.

        Parameters
        ----------
        new_level : TrustLevel
            The new trust level.  Must be ≥ current level.
        rationale : str
            Explanation for the promotion.

        Returns
        -------
        TypeTrustAnnotation
            A new annotation with ``trust_level=new_level`` and updated
            ``rationale``.

        Raises
        ------
        ValueError
            If *new_level* is strictly weaker than the current trust level
            (use ``demote`` for that direction).
        """
        if new_level < self.trust_level:
            raise ValueError(
                f"Cannot promote to a weaker level: "
                f"{new_level!r} < {self.trust_level!r}. "
                f"Use demote() for downward moves."
            )
        return replace(self, trust_level=new_level, rationale=rationale)

    def demote(self, rationale: str) -> "TypeTrustAnnotation":
        """Return a new annotation one trust step weaker.

        Demotion records a loss of confidence, e.g. when a counter-example
        is discovered (¬ φ) or a verifier is revoked.

        Parameters
        ----------
        rationale : str
            Explanation for the demotion.

        Returns
        -------
        TypeTrustAnnotation
            A new annotation at ``trust_level.step_weaker()``.
        """
        return replace(
            self,
            trust_level=self.trust_level.step_weaker(),
            rationale=rationale,
            verified_at=None,
            verifier=None,
        )

    def with_evidence(self, key: str) -> "TypeTrustAnnotation":
        """Return a new annotation with *key* added to the evidence basis.

        If *key* is already present the annotation is returned unchanged.

        Parameters
        ----------
        key : str
            The evidence key or label to add.

        Returns
        -------
        TypeTrustAnnotation
            Updated annotation.
        """
        if key in self.evidence_basis:
            return self
        return replace(self, evidence_basis=self.evidence_basis + (key,))

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def serialize(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, Any]
            Keys: ``trust_level`` (int), ``evidence_basis`` (list[str]),
            ``verified_at`` (str | None), ``verifier`` (str | None),
            ``rationale`` (str).
        """
        return {
            "trust_level": int(self.trust_level),
            "evidence_basis": list(self.evidence_basis),
            "verified_at": self.verified_at,
            "verifier": self.verifier,
            "rationale": self.rationale,
        }

    @classmethod
    def parse(cls, data: dict[str, Any]) -> "TypeTrustAnnotation":
        """Reconstruct a ``TypeTrustAnnotation`` from a serialised dictionary.

        Parameters
        ----------
        data : dict[str, Any]
            A dictionary previously produced by ``serialize()``.

        Returns
        -------
        TypeTrustAnnotation
            The reconstructed annotation.
        """
        return cls(
            trust_level=_trust_level_from_str(data["trust_level"]),
            evidence_basis=tuple(data.get("evidence_basis", ())),
            verified_at=data.get("verified_at"),
            verifier=data.get("verifier"),
            rationale=data.get("rationale", ""),
        )

    @classmethod
    def unverified(cls, rationale: str) -> "TypeTrustAnnotation":
        """Construct a minimal annotation at ``UNVERIFIED`` trust level.

        This is the appropriate starting point for a freshly constructed type
        before any verification has occurred.

        Parameters
        ----------
        rationale : str
            Brief explanation of why the type is proposed but not yet verified.

        Returns
        -------
        TypeTrustAnnotation
            A new annotation with ``trust_level=TrustLevel.UNVERIFIED`` and no
            evidence or verifier recorded.
        """
        return cls(
            trust_level=TrustLevel.UNVERIFIED,
            evidence_basis=(),
            verified_at=None,
            verifier=None,
            rationale=rationale,
        )


# ---------------------------------------------------------------------------
# TypeCarrier
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TypeCarrier:
    """The carrier K of a JuGeo type — the set of inhabitants and their constraints.

    In theory2.tex §3.2 the carrier K(c) is the underlying data structure at
    coordinate c.  A carrier is characterised by its kind (primitive, composite,
    dependent, etc.), the explicit set of known inhabitants, and a list of
    syntactic constraints that all inhabitants must satisfy.

    Parameters
    ----------
    carrier_id : str
        A unique identifier for this carrier within the subsystem.
    kind : CarrierKind
        Structural classification of the carrier.
    display_name : str
        Human-readable name for display in summaries and error messages.
    inhabitants : tuple[str, ...]
        Explicit enumeration of known inhabitant expressions (may be empty for
        open/infinite carriers).
    constraints : tuple[str, ...]
        Syntactic constraint strings that all inhabitants must satisfy.
    dependencies : tuple[str, ...]
        ``carrier_id`` values of carriers that this carrier depends on
        (relevant for ``DEPENDENT`` and ``COMPOSITE`` kinds).
    metadata : Mapping[str, Any]
        Arbitrary additional metadata (e.g. source location, notes).
    """

    carrier_id: str
    kind: CarrierKind
    display_name: str
    inhabitants: tuple[str, ...]
    constraints: tuple[str, ...]
    dependencies: tuple[str, ...]
    metadata: Mapping[str, Any]

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def is_primitive(self) -> bool:
        """Return ``True`` if this carrier is of kind ``PRIMITIVE``.

        A primitive carrier has no dependencies and its constraints are purely
        syntactic (no reference to other carriers).

        Returns
        -------
        bool
            ``True`` iff ``self.kind == CarrierKind.PRIMITIVE``.
        """
        return self.kind == CarrierKind.PRIMITIVE

    def is_dependent(self) -> bool:
        """Return ``True`` if this carrier is of kind ``DEPENDENT``.

        Dependent carriers require that ``self.dependencies`` is non-empty,
        as each dependency specifies the carrier of a free variable.

        Returns
        -------
        bool
            ``True`` iff ``self.kind == CarrierKind.DEPENDENT``.
        """
        return self.kind == CarrierKind.DEPENDENT

    def inhabitant_count(self) -> int:
        """Return the number of explicitly enumerated inhabitants.

        For open or infinite carriers this returns 0 even though the actual
        inhabitants are unbounded.

        Returns
        -------
        int
            ``len(self.inhabitants)``.
        """
        return len(self.inhabitants)

    def has_constraint(self, c: str) -> bool:
        """Return ``True`` if the constraint string *c* is registered on this carrier.

        Parameters
        ----------
        c : str
            The constraint string to test.

        Returns
        -------
        bool
            ``True`` iff *c* appears in ``self.constraints``.
        """
        return c in self.constraints

    def depends_on(self, cid: str) -> bool:
        """Return ``True`` if this carrier declares a dependency on carrier *cid*.

        Parameters
        ----------
        cid : str
            The ``carrier_id`` to test.

        Returns
        -------
        bool
            ``True`` iff *cid* appears in ``self.dependencies``.
        """
        return cid in self.dependencies

    def is_consistent(self) -> bool:
        """Return ``True`` if the carrier passes basic internal consistency checks.

        Consistency requires:

        1. ``carrier_id`` is non-empty.
        2. ``display_name`` is non-empty.
        3. PRIMITIVE carriers have no dependencies.
        4. No duplicate inhabitant strings.
        5. No duplicate constraint strings.

        Returns
        -------
        bool
            ``True`` iff all checks pass.
        """
        if not self.carrier_id.strip():
            return False
        if not self.display_name.strip():
            return False
        if self.kind == CarrierKind.PRIMITIVE and self.dependencies:
            return False
        if len(self.inhabitants) != len(set(self.inhabitants)):
            return False
        if len(self.constraints) != len(set(self.constraints)):
            return False
        return True

    # ------------------------------------------------------------------
    # Mutation helpers (return new instances)
    # ------------------------------------------------------------------

    def add_inhabitant(self, x: str) -> "TypeCarrier":
        """Return a new carrier with *x* appended to the inhabitant list.

        If *x* is already an inhabitant the carrier is returned unchanged.

        Parameters
        ----------
        x : str
            The inhabitant expression to add.

        Returns
        -------
        TypeCarrier
            Updated carrier.
        """
        if x in self.inhabitants:
            return self
        return replace(self, inhabitants=self.inhabitants + (x,))

    def add_constraint(self, c: str) -> "TypeCarrier":
        """Return a new carrier with constraint *c* appended.

        If *c* is already registered the carrier is returned unchanged.

        Parameters
        ----------
        c : str
            The constraint string to add.

        Returns
        -------
        TypeCarrier
            Updated carrier.
        """
        if c in self.constraints:
            return self
        return replace(self, constraints=self.constraints + (c,))

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def serialize(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, Any]
            All fields serialised to primitive types.
        """
        return {
            "carrier_id": self.carrier_id,
            "kind": self.kind.value,
            "display_name": self.display_name,
            "inhabitants": list(self.inhabitants),
            "constraints": list(self.constraints),
            "dependencies": list(self.dependencies),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def parse(cls, data: dict[str, Any]) -> "TypeCarrier":
        """Reconstruct a ``TypeCarrier`` from a serialised dictionary.

        Parameters
        ----------
        data : dict[str, Any]
            A dictionary previously produced by ``serialize()``.

        Returns
        -------
        TypeCarrier
            The reconstructed carrier.
        """
        return cls(
            carrier_id=data["carrier_id"],
            kind=CarrierKind(data.get("kind", CarrierKind.PRIMITIVE.value)),
            display_name=data.get("display_name", data["carrier_id"]),
            inhabitants=tuple(data.get("inhabitants", ())),
            constraints=tuple(data.get("constraints", ())),
            dependencies=tuple(data.get("dependencies", ())),
            metadata=MappingProxyType(data.get("metadata", {})),
        )

    @classmethod
    def primitive(cls, name: str, inhabitants: tuple[str, ...]) -> "TypeCarrier":
        """Construct a minimal ``PRIMITIVE`` carrier.

        This factory is the preferred way to create base-type carriers that
        have no dependencies and no constraints beyond inhabitant membership.

        Parameters
        ----------
        name : str
            Both the ``carrier_id`` and ``display_name`` for the new carrier.
        inhabitants : tuple[str, ...]
            The explicit inhabitant set.

        Returns
        -------
        TypeCarrier
            A new primitive carrier with the given name and inhabitants.
        """
        return cls(
            carrier_id=name,
            kind=CarrierKind.PRIMITIVE,
            display_name=name,
            inhabitants=inhabitants,
            constraints=(),
            dependencies=(),
            metadata=MappingProxyType({}),
        )


# ---------------------------------------------------------------------------
# TransportMap
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TransportMap:
    """An explicit transport map ρ(f): K(c) → K(c') along morphism f: c' → c.

    In theory2.tex §3.3 the transport family ρ is the contravariant action of
    the site morphisms on the type carrier.  For a morphism f: c' → c the map
    ρ(f): K(c) → K(c') restricts sections from c to c'.

    Parameters
    ----------
    map_id : str
        Unique identifier for this transport map.
    source_coordinate : Coordinate
        The coordinate c (the *larger* context whose sections are being
        restricted).
    target_coordinate : Coordinate
        The coordinate c' (the *smaller* context that receives the restricted
        section).
    morphism : Morphism
        The underlying morphism f: c' → c in the site.
    carrier_source : TypeCarrier
        The type carrier K(c) at the source coordinate.
    carrier_target : TypeCarrier
        The type carrier K(c') at the target coordinate.
    transport_rule : str
        A syntactic description of how ρ(f) acts on inhabitants.
    is_identity_transport : bool
        ``True`` iff this map is the identity transport (f = id_c, K(c) = K(c')).
    metadata : Mapping[str, Any]
        Arbitrary additional metadata.
    """

    map_id: str
    source_coordinate: Coordinate
    target_coordinate: Coordinate
    morphism: Morphism
    carrier_source: TypeCarrier
    carrier_target: TypeCarrier
    transport_rule: str
    is_identity_transport: bool
    metadata: Mapping[str, Any]

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def is_valid(self) -> bool:
        """Return ``True`` if the transport map passes basic coherence checks.

        Validity requires:

        1. ``map_id`` is non-empty.
        2. ``morphism.source`` equals ``target_coordinate`` (f: c' → c means
           morphism goes from c' to c, so source is c').
        3. ``morphism.target`` equals ``source_coordinate``.
        4. Both carriers pass their own consistency checks.

        Returns
        -------
        bool
            ``True`` iff all checks pass.
        """
        if not self.map_id.strip():
            return False
        if self.morphism.source.key != self.target_coordinate.key:
            return False
        if self.morphism.target.key != self.source_coordinate.key:
            return False
        if not self.carrier_source.is_consistent():
            return False
        if not self.carrier_target.is_consistent():
            return False
        return True

    def is_identity(self) -> bool:
        """Return ``True`` if this is an identity transport map.

        The identity transport ρ(id_c) satisfies:
        - ``source_coordinate == target_coordinate``
        - ``carrier_source == carrier_target`` (same object)
        - ``is_identity_transport`` flag is set

        Returns
        -------
        bool
            ``True`` iff all three identity conditions hold.
        """
        return (
            self.is_identity_transport
            and self.source_coordinate.key == self.target_coordinate.key
            and self.carrier_source.carrier_id == self.carrier_target.carrier_id
        )

    def is_restriction(self) -> bool:
        """Return ``True`` if the underlying morphism is an inclusion/restriction.

        Returns
        -------
        bool
            ``True`` iff ``self.morphism.kind == MorphismKind.RESTRICTION`` or
            ``MorphismKind.INCLUSION``.
        """
        return self.morphism.kind in (MorphismKind.RESTRICTION, MorphismKind.INCLUSION)

    def is_extension(self) -> bool:
        """Return ``True`` if the underlying morphism is an extension/transport.

        Returns
        -------
        bool
            ``True`` iff ``self.morphism.kind == MorphismKind.TRANSPORT``.
        """
        return self.morphism.kind == MorphismKind.TRANSPORT

    def transport_cost(self) -> int:
        """Return a heuristic cost estimate for this transport.

        The cost is defined as:

        - 0 for identity transports.
        - 1 for restriction/inclusion morphisms.
        - 2 for extension (transport) morphisms.
        - 3 for refinement morphisms.
        - ``len(carrier_source.inhabitants)`` added if the carrier is COMPOSITE or DEPENDENT.

        Returns
        -------
        int
            A non-negative integer cost estimate.
        """
        if self.is_identity_transport:
            return 0
        base: dict[MorphismKind, int] = {
            MorphismKind.RESTRICTION: 1,
            MorphismKind.INCLUSION: 1,
            MorphismKind.TRANSPORT: 2,
            MorphismKind.REFINEMENT: 3,
        }
        cost = base.get(self.morphism.kind, 2)
        if self.carrier_source.kind in (CarrierKind.COMPOSITE, CarrierKind.DEPENDENT):
            cost += self.carrier_source.inhabitant_count()
        return cost

    def is_coherent_with(self, other: "TransportMap") -> bool:
        """Return ``True`` if this map and *other* are coherent on their overlap.

        Two transport maps are coherent if, when composed (where composition
        is defined), the result is consistent with the transport rules.  As a
        lightweight approximation we check:

        1. Both maps originate from the same source carrier (by carrier_id).
        2. If their target coordinates are equal the target carriers must also
           match.

        Parameters
        ----------
        other : TransportMap
            The other transport map to compare against.

        Returns
        -------
        bool
            ``True`` iff the two maps are coherent on their overlap.
        """
        if self.carrier_source.carrier_id != other.carrier_source.carrier_id:
            return False
        if self.target_coordinate.key == other.target_coordinate.key:
            return self.carrier_target.carrier_id == other.carrier_target.carrier_id
        return True

    # ------------------------------------------------------------------
    # Composition / reversal (return new instances)
    # ------------------------------------------------------------------

    def compose(self, other: "TransportMap") -> "TransportMap":
        """Return the composite transport map ``self ∘ other``.

        Composition requires that ``other.target_coordinate == self.source_coordinate``
        (i.e. other goes f: c'' → c' and self goes g: c' → c, so the composite
        goes g∘f: c'' → c).

        Parameters
        ----------
        other : TransportMap
            The map to compose *after* this one (applied first).

        Returns
        -------
        TransportMap
            A new transport map representing the composition.

        Raises
        ------
        ValueError
            If the target of *other* does not match the source of *self*.
        """
        if other.target_coordinate.key != self.source_coordinate.key:
            raise ValueError(
                f"Cannot compose: other.target_coordinate "
                f"({other.target_coordinate.key!r}) ≠ self.source_coordinate "
                f"({self.source_coordinate.key!r})."
            )
        composed_morphism = replace(
            self.morphism,
            source=other.morphism.source,
            target=self.morphism.target,
            label=f"{other.morphism.label}∘{self.morphism.label}",
        )
        new_rule = f"({self.transport_rule}) ∘ ({other.transport_rule})"
        return replace(
            self,
            map_id=f"{self.map_id}∘{other.map_id}",
            source_coordinate=self.source_coordinate,
            target_coordinate=other.target_coordinate,
            morphism=composed_morphism,
            carrier_source=self.carrier_source,
            carrier_target=other.carrier_target,
            transport_rule=new_rule,
            is_identity_transport=(
                self.is_identity_transport and other.is_identity_transport
            ),
        )

    def reversed(self) -> "TransportMap":
        """Return a new transport map with source and target swapped.

        This constructs the formal reverse of the transport (not necessarily
        a mathematical inverse; it is a bookkeeping operation only).

        Returns
        -------
        TransportMap
            A new map with ``source_coordinate`` and ``target_coordinate``
            exchanged and ``morphism`` reversed.
        """
        reversed_morphism = replace(
            self.morphism,
            source=self.morphism.target,
            target=self.morphism.source,
            label=f"{self.morphism.label}⁻¹",
        )
        return replace(
            self,
            map_id=f"{self.map_id}⁻¹",
            source_coordinate=self.target_coordinate,
            target_coordinate=self.source_coordinate,
            morphism=reversed_morphism,
            carrier_source=self.carrier_target,
            carrier_target=self.carrier_source,
            transport_rule=f"({self.transport_rule})⁻¹",
        )

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def serialize(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible nested dictionary.

        Returns
        -------
        dict[str, Any]
            All fields serialised, with nested objects represented as
            sub-dictionaries.
        """
        return {
            "map_id": self.map_id,
            "source_coordinate": _serialize_coordinate(self.source_coordinate),
            "target_coordinate": _serialize_coordinate(self.target_coordinate),
            "morphism": _serialize_morphism(self.morphism),
            "carrier_source": self.carrier_source.serialize(),
            "carrier_target": self.carrier_target.serialize(),
            "transport_rule": self.transport_rule,
            "is_identity_transport": self.is_identity_transport,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def parse(cls, data: dict[str, Any]) -> "TransportMap":
        """Reconstruct a ``TransportMap`` from a serialised dictionary.

        Parameters
        ----------
        data : dict[str, Any]
            A dictionary previously produced by ``serialize()``.

        Returns
        -------
        TransportMap
            The reconstructed transport map.
        """
        return cls(
            map_id=data["map_id"],
            source_coordinate=_parse_coordinate(data["source_coordinate"]),
            target_coordinate=_parse_coordinate(data["target_coordinate"]),
            morphism=_parse_morphism(data["morphism"]),
            carrier_source=TypeCarrier.parse(data["carrier_source"]),
            carrier_target=TypeCarrier.parse(data["carrier_target"]),
            transport_rule=data.get("transport_rule", ""),
            is_identity_transport=bool(data.get("is_identity_transport", False)),
            metadata=MappingProxyType(data.get("metadata", {})),
        )

    @classmethod
    def identity(cls, coord: Coordinate, carrier: TypeCarrier) -> "TransportMap":
        """Construct the identity transport at *coord* for carrier *carrier*.

        The identity transport satisfies ρ(id_c) = id_{K(c)}, as required by
        the carrier-identity theorem (theory2.tex §3.3.1).

        Parameters
        ----------
        coord : Coordinate
            The coordinate at which the identity transport acts.
        carrier : TypeCarrier
            The carrier K(c) at that coordinate.

        Returns
        -------
        TransportMap
            A new identity transport map.
        """
        identity_morphism = Morphism(
            source=coord,
            target=coord,
            kind=MorphismKind.RESTRICTION,
            label=f"id_{coord.key}",
        )
        return cls(
            map_id=f"id_{coord.key}",
            source_coordinate=coord,
            target_coordinate=coord,
            morphism=identity_morphism,
            carrier_source=carrier,
            carrier_target=carrier,
            transport_rule=f"identity on {carrier.carrier_id}",
            is_identity_transport=True,
            metadata=MappingProxyType({}),
        )


# ---------------------------------------------------------------------------
# GluingLaw
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GluingLaw:
    """The gluing law γ assembling a global type from compatible local carriers.

    Theory2.tex §3.4 requires that a sheaf of types satisfies the *gluing
    condition*: given a covering family {Uᵢ → U} and compatible sections
    sᵢ ∈ K(Uᵢ) (agreeing on all pairwise overlaps), there exists a *unique*
    global section s ∈ K(U) restricting to each sᵢ.

    Parameters
    ----------
    law_id : str
        Unique identifier for this gluing law.
    base_coordinate : Coordinate
        The coordinate U that the law assembles a section over.
    covering_coordinates : tuple[Coordinate, ...]
        The covering family {Uᵢ} (open cover of U in the site topology).
    local_carriers : tuple[TypeCarrier, ...]
        The carrier K(Uᵢ) at each covering coordinate (parallel to
        ``covering_coordinates``).
    transport_maps : tuple[TransportMap, ...]
        Transport maps witnessing compatibility on pairwise overlaps.
    overlap_conditions : tuple[str, ...]
        Syntactic conditions expressing agreement on overlaps (ρ(fᵢⱼ)(sᵢ) = ρ(fⱼᵢ)(sⱼ)).
    uniqueness_condition : str
        A syntactic statement of the uniqueness clause.
    is_verified : bool
        ``True`` iff the gluing uniqueness has been formally verified.
    """

    law_id: str
    base_coordinate: Coordinate
    covering_coordinates: tuple[Coordinate, ...]
    local_carriers: tuple[TypeCarrier, ...]
    transport_maps: tuple[TransportMap, ...]
    overlap_conditions: tuple[str, ...]
    uniqueness_condition: str
    is_verified: bool

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def is_valid(self) -> bool:
        """Return ``True`` if the gluing law passes basic structural checks.

        Validity requires:

        1. ``law_id`` is non-empty.
        2. ``covering_coordinates`` is non-empty.
        3. ``local_carriers`` has the same length as ``covering_coordinates``.
        4. All local carriers pass their own consistency checks.

        Returns
        -------
        bool
            ``True`` iff all checks pass.
        """
        if not self.law_id.strip():
            return False
        if not self.covering_coordinates:
            return False
        if len(self.local_carriers) != len(self.covering_coordinates):
            return False
        for car in self.local_carriers:
            if not car.is_consistent():
                return False
        return True

    def covering_count(self) -> int:
        """Return the number of elements in the covering family.

        Returns
        -------
        int
            ``len(self.covering_coordinates)``.
        """
        return len(self.covering_coordinates)

    def overlap_count(self) -> int:
        """Return the number of pairwise overlap conditions registered.

        In a covering of size n there are at most n(n−1)/2 distinct pairwise
        overlaps.  The actual count may be smaller for sparse covers.

        Returns
        -------
        int
            ``len(self.overlap_conditions)``.
        """
        return len(self.overlap_conditions)

    def check_overlap_condition(self, i: int, j: int) -> bool:
        """Return ``True`` if an overlap condition for indices *i*, *j* is present.

        We look for any overlap condition whose string contains both the key
        of covering coordinate *i* and the key of covering coordinate *j*.
        This is a syntactic heuristic, not a semantic check.

        Parameters
        ----------
        i : int
            Index into ``covering_coordinates`` for the first patch.
        j : int
            Index into ``covering_coordinates`` for the second patch.

        Returns
        -------
        bool
            ``True`` iff at least one overlap condition references both
            coordinates *i* and *j*.

        Raises
        ------
        IndexError
            If *i* or *j* is out of range for ``covering_coordinates``.
        """
        ci = self.covering_coordinates[i]
        cj = self.covering_coordinates[j]
        for cond in self.overlap_conditions:
            if ci.key in cond and cj.key in cond:
                return True
        return False

    def admits_unique_gluing(self) -> bool:
        """Return ``True`` if the law records a non-empty uniqueness condition.

        This is a syntactic check — it does not evaluate the condition.  For
        semantic verification use ``verify()``.

        Returns
        -------
        bool
            ``True`` iff ``self.uniqueness_condition`` is non-empty.
        """
        return bool(self.uniqueness_condition.strip())

    def local_carrier_at(self, coord: Coordinate) -> TypeCarrier | None:
        """Return the local carrier at *coord*, or ``None`` if not in the covering.

        Parameters
        ----------
        coord : Coordinate
            The coordinate to look up.

        Returns
        -------
        TypeCarrier | None
            The carrier K(coord) if *coord* is in the covering family, else ``None``.
        """
        for c, car in zip(self.covering_coordinates, self.local_carriers):
            if c.key == coord.key:
                return car
        return None

    # ------------------------------------------------------------------
    # Mutation helpers (return new instances)
    # ------------------------------------------------------------------

    def verify(self) -> "GluingLaw":
        """Return a new gluing law with ``is_verified=True``.

        Corresponds to closing the gluing-uniqueness proof obligation
        (theory2.tex §3.4 Theorem "gluing uniqueness").

        Returns
        -------
        GluingLaw
            A copy of *self* with ``is_verified=True``.
        """
        return replace(self, is_verified=True)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def serialize(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible nested dictionary.

        Returns
        -------
        dict[str, Any]
            All fields serialised, with nested objects as sub-dictionaries.
        """
        return {
            "law_id": self.law_id,
            "base_coordinate": _serialize_coordinate(self.base_coordinate),
            "covering_coordinates": [
                _serialize_coordinate(c) for c in self.covering_coordinates
            ],
            "local_carriers": [car.serialize() for car in self.local_carriers],
            "transport_maps": [tm.serialize() for tm in self.transport_maps],
            "overlap_conditions": list(self.overlap_conditions),
            "uniqueness_condition": self.uniqueness_condition,
            "is_verified": self.is_verified,
        }

    @classmethod
    def parse(cls, data: dict[str, Any]) -> "GluingLaw":
        """Reconstruct a ``GluingLaw`` from a serialised dictionary.

        Parameters
        ----------
        data : dict[str, Any]
            A dictionary previously produced by ``serialize()``.

        Returns
        -------
        GluingLaw
            The reconstructed gluing law.
        """
        return cls(
            law_id=data["law_id"],
            base_coordinate=_parse_coordinate(data["base_coordinate"]),
            covering_coordinates=tuple(
                _parse_coordinate(c) for c in data.get("covering_coordinates", [])
            ),
            local_carriers=tuple(
                TypeCarrier.parse(c) for c in data.get("local_carriers", [])
            ),
            transport_maps=tuple(
                TransportMap.parse(t) for t in data.get("transport_maps", [])
            ),
            overlap_conditions=tuple(data.get("overlap_conditions", ())),
            uniqueness_condition=data.get("uniqueness_condition", ""),
            is_verified=bool(data.get("is_verified", False)),
        )

    @classmethod
    def trivial(cls, coord: Coordinate, carrier: TypeCarrier) -> "GluingLaw":
        """Construct the trivial (single-patch) gluing law at *coord*.

        The trivial gluing law is the degenerate case where the covering
        consists of a single open set equal to the base (a singleton cover).
        There are no overlaps to check and gluing is trivially unique.

        Parameters
        ----------
        coord : Coordinate
            The base coordinate.
        carrier : TypeCarrier
            The carrier at *coord*.

        Returns
        -------
        GluingLaw
            A verified trivial gluing law.
        """
        return cls(
            law_id=f"trivial_{coord.key}",
            base_coordinate=coord,
            covering_coordinates=(coord,),
            local_carriers=(carrier,),
            transport_maps=(TransportMap.identity(coord, carrier),),
            overlap_conditions=(),
            uniqueness_condition=f"Trivial: single patch {coord.key}",
            is_verified=True,
        )


# ---------------------------------------------------------------------------
# JuGeoType
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class JuGeoType:
    """A coordinate-indexed semantic type τ = (c, K, ρ, γ, supp, trust).

    This is the central value object of theory2.tex Ch3.  A ``JuGeoType``
    bundles together:

    - A coordinate *c* locating the type in the semantic site.
    - A type carrier *K* (the set/data structure of inhabitants).
    - A family of transport maps ρ (one per relevant morphism).
    - An optional gluing law γ (for sheaf-level assembly).
    - A support set supp ⊆ Obj(C) recording where τ is non-trivial.
    - A trust annotation recording confidence in the type assignment.
    - A logical formula φ expressing the semantic content of τ.

    All "mutations" return new ``JuGeoType`` instances via ``replace()``.

    Parameters
    ----------
    type_id : str
        Unique identifier for this type object within the subsystem.
    coordinate : Coordinate
        The coordinate c at which τ lives.
    carrier : TypeCarrier
        The type carrier K(c).
    transport_maps : tuple[TransportMap, ...]
        Transport maps ρ(f) for each relevant morphism f into c.
    gluing_law : GluingLaw | None
        The gluing law γ, or ``None`` if τ is purely local.
    support : frozenset[str]
        Coordinate key strings where τ is non-trivial.
    trust : TypeTrustAnnotation
        Trust metadata for this type assignment.
    formula : str
        A logical formula φ expressing the semantic content of τ.
    metadata : Mapping[str, Any]
        Arbitrary additional metadata.
    """

    type_id: str
    coordinate: Coordinate
    carrier: TypeCarrier
    transport_maps: tuple[TransportMap, ...]
    gluing_law: GluingLaw | None
    support: frozenset[str]
    trust: TypeTrustAnnotation
    formula: str
    metadata: Mapping[str, Any]

    # ------------------------------------------------------------------
    # Locality queries
    # ------------------------------------------------------------------

    def is_global(self) -> bool:
        """Return ``True`` if τ has a non-trivial gluing law (global/sheaf type).

        A global type participates in the sheaf gluing protocol:
        it can be assembled from local patches using the attached ``GluingLaw``.

        Returns
        -------
        bool
            ``True`` iff ``self.gluing_law`` is not ``None`` and is valid.
        """
        return self.gluing_law is not None and self.gluing_law.is_valid()

    def is_local(self) -> bool:
        """Return ``True`` if τ has no gluing law (purely local type).

        A local type is defined only at a single coordinate and does not
        participate in sheaf assembly.

        Returns
        -------
        bool
            ``True`` iff ``self.gluing_law`` is ``None``.
        """
        return self.gluing_law is None

    def is_supported_at(self, coord: Coordinate) -> bool:
        """Return ``True`` if τ is non-trivial at coordinate *coord*.

        Support monotonicity (theory2.tex §3.5): if c ⪯ c' and τ is supported
        at c then τ is also supported at c'.  This method checks raw support
        membership; the monotonicity enforcement is the caller's responsibility.

        Parameters
        ----------
        coord : Coordinate
            The coordinate to check.

        Returns
        -------
        bool
            ``True`` iff ``coord.key`` is in ``self.support``.
        """
        return coord.key in self.support

    # ------------------------------------------------------------------
    # Trust queries
    # ------------------------------------------------------------------

    def trust_level(self) -> TrustLevel:
        """Return the current ``TrustLevel`` of this type assignment.

        Returns
        -------
        TrustLevel
            ``self.trust.trust_level``.
        """
        return self.trust.trust_level

    def is_verified(self) -> bool:
        """Return ``True`` if the type assignment is verified (trust ≥ VERIFIED_PROOF).

        Returns
        -------
        bool
            ``self.trust.is_verified()``.
        """
        return self.trust.is_verified()

    # ------------------------------------------------------------------
    # Transport / restriction / extension
    # ------------------------------------------------------------------

    def restrict_to(self, target_coord: Coordinate, morphism: Morphism) -> "JuGeoType":
        """Return a new ``JuGeoType`` restricted to *target_coord* via *morphism*.

        Restriction is the contravariant action of the site topology on the
        type: given f: c' → c we obtain τ|_{c'} by applying ρ(f) to the
        carrier.

        Parameters
        ----------
        target_coord : Coordinate
            The target coordinate c'.
        morphism : Morphism
            The morphism f: c' → c.

        Returns
        -------
        JuGeoType
            A new type at ``target_coord`` with carrier ``carrier_target`` of
            the matching transport map (if found), or the current carrier
            decorated with the restriction morphism.

        Raises
        ------
        ValueError
            If *morphism* does not go from *target_coord* to ``self.coordinate``.
        """
        if morphism.target.key != self.coordinate.key:
            raise ValueError(
                f"Morphism target {morphism.target.key!r} ≠ "
                f"self.coordinate {self.coordinate.key!r}."
            )
        matching_tmap = next(
            (
                tm
                for tm in self.transport_maps
                if tm.target_coordinate.key == target_coord.key
            ),
            None,
        )
        new_carrier = (
            matching_tmap.carrier_target if matching_tmap is not None else self.carrier
        )
        new_tmap = (
            matching_tmap
            if matching_tmap is not None
            else TransportMap.identity(target_coord, new_carrier)
        )
        new_support = frozenset(
            k for k in self.support if k == target_coord.key
        )
        return replace(
            self,
            type_id=f"{self.type_id}|_{target_coord.key}",
            coordinate=target_coord,
            carrier=new_carrier,
            transport_maps=(new_tmap,),
            gluing_law=None,
            support=new_support,
            formula=f"({self.formula})|_{target_coord.key}",
        )

    def extend_along(self, morphism: Morphism) -> "JuGeoType":
        """Return a new ``JuGeoType`` extended along *morphism*.

        Extension moves the type from its current coordinate to the *target*
        of *morphism* (going in the forward direction, as opposed to restriction
        which goes backward).  This corresponds to the pushforward operation.

        Parameters
        ----------
        morphism : Morphism
            A morphism f: c → c' (``morphism.source == self.coordinate``).

        Returns
        -------
        JuGeoType
            A new type at ``morphism.target`` with an extension transport map.

        Raises
        ------
        ValueError
            If ``morphism.source.key != self.coordinate.key``.
        """
        if morphism.source.key != self.coordinate.key:
            raise ValueError(
                f"Morphism source {morphism.source.key!r} ≠ "
                f"self.coordinate {self.coordinate.key!r}."
            )
        target_coord = morphism.target
        extension_map = TransportMap(
            map_id=f"ext_{self.type_id}_{target_coord.key}",
            source_coordinate=target_coord,
            target_coordinate=self.coordinate,
            morphism=replace(morphism, source=self.coordinate, target=target_coord),
            carrier_source=self.carrier,
            carrier_target=self.carrier,
            transport_rule=f"extend {self.carrier.carrier_id} along {morphism.label}",
            is_identity_transport=False,
            metadata=MappingProxyType({}),
        )
        new_support = self.support | {target_coord.key}
        return replace(
            self,
            type_id=f"{self.type_id}↑{target_coord.key}",
            coordinate=target_coord,
            carrier=self.carrier,
            transport_maps=(extension_map,),
            gluing_law=None,
            support=new_support,
            formula=f"({self.formula})↑{target_coord.key}",
        )

    def transport_to(self, target_coord: Coordinate, tmap: TransportMap) -> "JuGeoType":
        """Return a new ``JuGeoType`` transported to *target_coord* using *tmap*.

        This is the explicit transport operation: given a pre-constructed
        ``TransportMap`` for ρ(f) we apply it to produce τ at c'.

        Parameters
        ----------
        target_coord : Coordinate
            The destination coordinate c'.
        tmap : TransportMap
            The transport map ρ(f): K(c) → K(c').

        Returns
        -------
        JuGeoType
            A new type at *target_coord* using the carrier in *tmap*.

        Raises
        ------
        ValueError
            If *tmap* does not transport from ``self.coordinate`` to
            *target_coord*.
        """
        if tmap.source_coordinate.key != self.coordinate.key:
            raise ValueError(
                f"tmap.source_coordinate {tmap.source_coordinate.key!r} ≠ "
                f"self.coordinate {self.coordinate.key!r}."
            )
        if tmap.target_coordinate.key != target_coord.key:
            raise ValueError(
                f"tmap.target_coordinate {tmap.target_coordinate.key!r} ≠ "
                f"target_coord {target_coord.key!r}."
            )
        new_support = self.support | {target_coord.key}
        return replace(
            self,
            type_id=f"{self.type_id}→{target_coord.key}",
            coordinate=target_coord,
            carrier=tmap.carrier_target,
            transport_maps=(tmap,),
            gluing_law=None,
            support=new_support,
            formula=f"ρ({tmap.morphism.label})({self.formula})",
        )

    def glue_with(
        self, others: tuple["JuGeoType", ...], law: GluingLaw
    ) -> "JuGeoType":
        """Return a new global ``JuGeoType`` assembled from *self* and *others* via *law*.

        This implements the sheaf gluing step γ: given a compatible family
        {τᵢ at Uᵢ} and a ``GluingLaw`` *law*, produce the global type τ at U.

        Parameters
        ----------
        others : tuple[JuGeoType, ...]
            The other local type objects in the compatible family.
        law : GluingLaw
            The gluing law governing the assembly.

        Returns
        -------
        JuGeoType
            A new type at ``law.base_coordinate`` with the gluing law attached.

        Raises
        ------
        ValueError
            If *law* is not valid.
        """
        if not law.is_valid():
            raise ValueError(
                f"GluingLaw {law.law_id!r} is not valid; cannot glue."
            )
        all_types = (self,) + others
        combined_support: frozenset[str] = frozenset()
        for t in all_types:
            combined_support = combined_support | t.support
        combined_support = combined_support | {law.base_coordinate.key}

        all_tmaps: tuple[TransportMap, ...] = ()
        for t in all_types:
            all_tmaps = all_tmaps + t.transport_maps

        glued_formula = "γ({" + ", ".join(t.formula for t in all_types) + "})"
        min_trust = min((t.trust_level() for t in all_types), default=TrustLevel.UNVERIFIED)
        glued_trust = replace(
            self.trust,
            trust_level=min_trust,
            rationale=f"Glued from {len(all_types)} local types; trust = min of components.",
        )
        return replace(
            self,
            type_id=f"glued_{law.law_id}",
            coordinate=law.base_coordinate,
            carrier=self.carrier,
            transport_maps=all_tmaps,
            gluing_law=law,
            support=combined_support,
            trust=glued_trust,
            formula=glued_formula,
        )

    # ------------------------------------------------------------------
    # Trust mutation (returns new instances)
    # ------------------------------------------------------------------

    def promote_trust(self, new_level: TrustLevel, rationale: str) -> "JuGeoType":
        """Return a new type with its trust promoted to *new_level*.

        Delegates to ``TypeTrustAnnotation.promote()`` and wraps the result
        in a new ``JuGeoType``.

        Parameters
        ----------
        new_level : TrustLevel
            The new trust level (must be ≥ current).
        rationale : str
            Explanation for the promotion.

        Returns
        -------
        JuGeoType
            A copy of *self* with updated trust annotation.

        Raises
        ------
        ValueError
            If *new_level* is weaker than the current trust level.
        """
        new_trust = self.trust.promote(new_level, rationale)
        return replace(self, trust=new_trust)

    # ------------------------------------------------------------------
    # Formula / compatibility queries
    # ------------------------------------------------------------------

    def matches_formula(self, formula: str) -> bool:
        """Return ``True`` if *formula* matches ``self.formula`` (exact string match).

        Parameters
        ----------
        formula : str
            The formula string to compare.

        Returns
        -------
        bool
            ``True`` iff ``self.formula == formula``.
        """
        return self.formula == formula

    def is_compatible_with(self, other: "JuGeoType") -> bool:
        """Return ``True`` if *self* and *other* are compatible for gluing.

        Two types are compatible iff:

        1. They share at least one coordinate in their support (∧ supp overlap ≠ ∅),
           OR one lives at the other's coordinate.
        2. Their carriers have the same ``kind``.
        3. Neither is contradicted (¬ trust = CONTRADICTED).

        Parameters
        ----------
        other : JuGeoType
            The type to compare against.

        Returns
        -------
        bool
            ``True`` iff all three compatibility conditions hold.
        """
        if self.trust.is_contradicted() or other.trust.is_contradicted():
            return False
        if self.carrier.kind != other.carrier.kind:
            return False
        support_overlap = self.support & other.support
        coord_overlap = (
            self.coordinate.key in other.support
            or other.coordinate.key in self.support
            or self.coordinate.key == other.coordinate.key
        )
        return bool(support_overlap) or coord_overlap

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def serialize(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible nested dictionary.

        Returns
        -------
        dict[str, Any]
            All fields serialised, with nested objects as sub-dictionaries.
        """
        return {
            "type_id": self.type_id,
            "coordinate": _serialize_coordinate(self.coordinate),
            "carrier": self.carrier.serialize(),
            "transport_maps": [tm.serialize() for tm in self.transport_maps],
            "gluing_law": self.gluing_law.serialize() if self.gluing_law else None,
            "support": sorted(self.support),
            "trust": self.trust.serialize(),
            "formula": self.formula,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def parse(cls, data: dict[str, Any]) -> "JuGeoType":
        """Reconstruct a ``JuGeoType`` from a serialised dictionary.

        Parameters
        ----------
        data : dict[str, Any]
            A dictionary previously produced by ``serialize()``.

        Returns
        -------
        JuGeoType
            The reconstructed type object.
        """
        gluing_raw = data.get("gluing_law")
        return cls(
            type_id=data["type_id"],
            coordinate=_parse_coordinate(data["coordinate"]),
            carrier=TypeCarrier.parse(data["carrier"]),
            transport_maps=tuple(
                TransportMap.parse(t) for t in data.get("transport_maps", [])
            ),
            gluing_law=GluingLaw.parse(gluing_raw) if gluing_raw is not None else None,
            support=frozenset(data.get("support", ())),
            trust=TypeTrustAnnotation.parse(data["trust"]),
            formula=data.get("formula", ""),
            metadata=MappingProxyType(data.get("metadata", {})),
        )

    @classmethod
    def from_annotation(
        cls,
        coord: Coordinate,
        annotation: TypeTrustAnnotation,
        carrier: TypeCarrier,
    ) -> "JuGeoType":
        """Construct a minimal ``JuGeoType`` from a coordinate, annotation, and carrier.

        This factory is the recommended entry point when creating a new type
        object from scratch without a pre-built transport map or gluing law.
        The resulting type is local (no gluing law) with the identity transport.

        Parameters
        ----------
        coord : Coordinate
            The coordinate c at which the type lives.
        annotation : TypeTrustAnnotation
            The initial trust annotation.
        carrier : TypeCarrier
            The type carrier K(c).

        Returns
        -------
        JuGeoType
            A new minimal ``JuGeoType`` with a single identity transport map,
            support equal to ``{coord.key}``, and a formula derived from the
            carrier name.
        """
        id_map = TransportMap.identity(coord, carrier)
        return cls(
            type_id=f"τ_{coord.key}_{carrier.carrier_id}",
            coordinate=coord,
            carrier=carrier,
            transport_maps=(id_map,),
            gluing_law=None,
            support=frozenset({coord.key}),
            trust=annotation,
            formula=f"{carrier.carrier_id}@{coord.key}",
            metadata=MappingProxyType({}),
        )


# ---------------------------------------------------------------------------
# Cross-referencing helpers (Theory2.tex §6 — Type Objects)
# ---------------------------------------------------------------------------

_models_logger = logging.getLogger(__name__)


def model_evidence_bridge(model):
    """Collect evidence for a type model and build an evidence manifest.

    Walks the model's carriers and transport maps, gathering supporting
    evidence via ``jugeo.evidence.manifests``.  Each piece of evidence is
    annotated with a ``TrustLevel`` drawn from the model's trust annotation.
    See Theory2.tex §6 (Type Objects) for the formal relationship between
    evidence and type models.

    Parameters
    ----------
    model : object
        A type model (e.g. ``JuGeoType``) carrying carrier and transport
        information.

    Returns
    -------
    dict
        ``{"manifest_id": str | None, "evidence_count": int,
        "trust_level": str, "complete": bool}``
    """
    try:
        from jugeo.evidence.manifests import build_evidence_manifest, EvidenceManifest
        from jugeo.evidence.trust import TrustLevel
    except ImportError as exc:
        _models_logger.warning("evidence bridge unavailable: %s", exc)
        return {"manifest_id": None, "evidence_count": 0,
                "trust_level": "unknown", "complete": False,
                "error": str(exc)}

    carriers = getattr(model, "transport_maps", ())
    trust = getattr(model, "trust", None)
    trust_level = trust if isinstance(trust, TrustLevel) else TrustLevel.UNVERIFIED
    items = []
    for tm in carriers:
        items.append({"source": str(getattr(tm, "source", "")),
                       "target": str(getattr(tm, "target", "")),
                       "trust": str(trust_level)})
    manifest = build_evidence_manifest(items) if items else EvidenceManifest.empty()
    manifest_id = getattr(manifest, "manifest_id", None)
    _models_logger.debug("evidence bridge: %d items, manifest=%s",
                         len(items), manifest_id)
    return {
        "manifest_id": str(manifest_id) if manifest_id else None,
        "evidence_count": len(items),
        "trust_level": str(trust_level),
        "complete": len(items) > 0 and manifest_id is not None,
    }


def model_cover_bridge(model):
    """Map a type model to a cover structure via geometry covers and descent.

    For every transport map in the model, constructs a ``CoverMember`` and
    a ``LocalSection``, then scores the resulting cover with
    ``score_cover``.  See Theory2.tex §6 (Type Objects) for the
    cover-model correspondence.

    Parameters
    ----------
    model : object
        A type model (e.g. ``JuGeoType``) with transport maps and support.

    Returns
    -------
    dict
        ``{"cover_score": float | None, "member_count": int,
        "section_count": int, "valid": bool}``
    """
    try:
        from jugeo.geometry.covers import CoverMember, score_cover
        from jugeo.geometry.descent import LocalSection
    except ImportError as exc:
        _models_logger.warning("cover bridge unavailable: %s", exc)
        return {"cover_score": None, "member_count": 0,
                "section_count": 0, "valid": False,
                "error": str(exc)}

    transport_maps = getattr(model, "transport_maps", ())
    members = []
    sections = []
    for tm in transport_maps:
        member = CoverMember(source=getattr(tm, "source", None),
                             target=getattr(tm, "target", None))
        members.append(member)
        section = LocalSection.from_transport(tm) if hasattr(LocalSection, "from_transport") else None
        if section is not None:
            sections.append(section)
    cover_score = score_cover(members) if members else None
    valid = cover_score is not None and len(sections) == len(members)
    _models_logger.debug("cover bridge: score=%.4f, %d members, %d sections",
                         cover_score or 0.0, len(members), len(sections))
    return {
        "cover_score": cover_score,
        "member_count": len(members),
        "section_count": len(sections),
        "valid": valid,
    }
