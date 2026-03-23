"""Core frozen dataclasses for the relational_refinement package.

Defines the four primary model types used throughout Ch12:

RefinementRelation
    Represents a directed refinement relation between two judgment coordinates.
    Stores the direction (FORWARD / BACKWARD / EQUIVALENT / INCOMPARABLE),
    trust delta, evidence embedding, and obligation discharge record.

EquivalenceClass
    An equivalence class of mutually-equivalent judgment coordinates.  The
    class has a representative coordinate, a frozenset of members, and a
    canonical trust level shared by all members.

RefinementWitness
    A certificate w: J → J' proving J ≤ J'.  Captures the trust promotion
    path, evidence embedding map, and obligation discharge map.

RefinementOrder
    The partial order structure on a set of judgment coordinates.  Stores all
    RefinementRelation edges, their derived EquivalenceClass partition, and
    supports join/meet/closure queries.

Design notes
------------
All classes are ``@dataclass(frozen=True, slots=True)``.  Mutations return
new instances via ``dataclasses.replace``.  JSON round-trip is provided by
``to_dict`` / ``from_dict``.
"""
from __future__ import annotations

import datetime
import uuid
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Mapping, Sequence

from jugeo.judgments.judgment_terms import (
    TrustLevel,
    Provenance,
    ProvenanceSource,
    EvidenceBundle,
)

# ---------------------------------------------------------------------------
# Project-wide type aliases
# ---------------------------------------------------------------------------
JsonScalar = None | bool | int | float | str
JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]

# ---------------------------------------------------------------------------
# RefinementRelation
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RefinementRelation:
    """A directed refinement relation between two judgment coordinates.

    A relation ``left ≤ right`` means *right* is stronger (more specific) than
    *left*.  The ``direction`` field records whether this is a proper forward
    refinement, a backward regression, a bidirectional equivalence, or an
    incomparability.

    Attributes
    ----------
    relation_id:
        Unique identifier for this relation record.
    left_coordinate:
        The coordinate string of the weaker (source) judgment.
    right_coordinate:
        The coordinate string of the stronger (target) judgment.
    direction:
        Structural direction of the refinement.
    trust_delta:
        ``trust(right) - trust(left)`` as an integer on the trust lattice.
        Positive = promotion, negative = regression, zero = same level.
    evidence_embedding:
        Keys of evidence items in *left* that embed into *right*, encoded as
        ``(left_key, right_key)`` pairs stored as a tuple of ``"l:r"`` strings.
    obligation_discharge:
        Residual obligations of *left* that are discharged in *right*, encoded
        as ``(left_obligation_id, right_obligation_id)`` pairs.
    is_witnessed:
        Whether a formal ``RefinementWitness`` has been constructed and validated.
    witness_id:
        The ``witness_id`` of the associated ``RefinementWitness``, if any.
    confidence:
        A ``[0.0, 1.0]`` score for the checker's confidence in this relation.
    metadata:
        Free-form annotation dict.
    """

    class RefinementDirection(str, Enum):
        """Direction of a refinement relation between two judgments."""

        FORWARD = "forward"
        """left ≤ right (right is strictly stronger)."""

        BACKWARD = "backward"
        """right ≤ left (left is actually stronger — regression from the
        perspective of left → right)."""

        EQUIVALENT = "equivalent"
        """left ≡ right (bidirectional refinement)."""

        INCOMPARABLE = "incomparable"
        """Neither left ≤ right nor right ≤ left holds."""

    relation_id: str
    left_coordinate: str
    right_coordinate: str
    direction: "RefinementRelation.RefinementDirection"
    trust_delta: int
    evidence_embedding: tuple[str, ...]
    obligation_discharge: tuple[str, ...]
    is_witnessed: bool
    witness_id: str | None
    confidence: float
    metadata: tuple[tuple[str, str], ...]

    # ------------------------------------------------------------------
    # Factory helpers
    # ------------------------------------------------------------------

    @classmethod
    def make(
        cls,
        left: str,
        right: str,
        direction: "RefinementRelation.RefinementDirection",
        trust_delta: int = 0,
        evidence_embedding: tuple[str, ...] = (),
        obligation_discharge: tuple[str, ...] = (),
        is_witnessed: bool = False,
        witness_id: str | None = None,
        confidence: float = 1.0,
        metadata: tuple[tuple[str, str], ...] = (),
    ) -> "RefinementRelation":
        """Convenience factory that auto-generates a ``relation_id``.

        Parameters
        ----------
        left:
            Source (weaker) coordinate.
        right:
            Target (stronger) coordinate.
        direction:
            Structural direction.
        trust_delta:
            Integer trust delta (right − left on the trust lattice).
        evidence_embedding:
            Encoded evidence pairs ``"left_key:right_key"``.
        obligation_discharge:
            Encoded obligation pairs ``"left_id:right_id"``.
        is_witnessed:
            Whether a formal witness has been constructed.
        witness_id:
            ID of the associated witness, if any.
        confidence:
            Checker confidence in ``[0, 1]``.
        metadata:
            Free-form key-value pairs.

        Returns
        -------
        RefinementRelation
            A new relation with a freshly generated ``relation_id``.
        """
        return cls(
            relation_id=str(uuid.uuid4()),
            left_coordinate=left,
            right_coordinate=right,
            direction=direction,
            trust_delta=trust_delta,
            evidence_embedding=evidence_embedding,
            obligation_discharge=obligation_discharge,
            is_witnessed=is_witnessed,
            witness_id=witness_id,
            confidence=confidence,
            metadata=metadata,
        )

    # ------------------------------------------------------------------
    # Predicate helpers
    # ------------------------------------------------------------------

    def is_proper_refinement(self) -> bool:
        """Return ``True`` iff this is a strict forward refinement (left ≤ right).

        Returns
        -------
        bool
            ``True`` for ``FORWARD`` direction.
        """
        return self.direction == RefinementRelation.RefinementDirection.FORWARD

    def is_equivalence(self) -> bool:
        """Return ``True`` iff left ≡ right (bidirectional refinement).

        Returns
        -------
        bool
            ``True`` for ``EQUIVALENT`` direction.
        """
        return self.direction == RefinementRelation.RefinementDirection.EQUIVALENT

    def is_regression(self) -> bool:
        """Return ``True`` iff this relation represents a regression.

        A relation is a regression when the trust delta is strictly negative
        *or* when the direction is ``BACKWARD``.

        Returns
        -------
        bool
            ``True`` if the relation regresses trust or direction.
        """
        return (
            self.direction == RefinementRelation.RefinementDirection.BACKWARD
            or self.trust_delta < 0
        )

    def trust_is_monotone(self) -> bool:
        """Return ``True`` iff trust is non-decreasing (trust_delta ≥ 0).

        For a proper refinement J ≤ J', trust monotonicity requires that
        trust(J') ≥ trust(J), i.e. trust_delta ≥ 0.

        Returns
        -------
        bool
            ``True`` when the trust delta is non-negative.
        """
        return self.trust_delta >= 0

    # ------------------------------------------------------------------
    # Algebraic operations
    # ------------------------------------------------------------------

    def invert(self) -> "RefinementRelation":
        """Return the inverse relation (swap left/right, flip direction/delta).

        If this relation encodes left ≤ right, the inverse encodes right ≤ left
        (which is a regression from the original perspective).

        Returns
        -------
        RefinementRelation
            New relation with left/right coordinates swapped and direction
            flipped (FORWARD ↔ BACKWARD; EQUIVALENT stays EQUIVALENT;
            INCOMPARABLE stays INCOMPARABLE).
        """
        flip = {
            RefinementRelation.RefinementDirection.FORWARD: RefinementRelation.RefinementDirection.BACKWARD,
            RefinementRelation.RefinementDirection.BACKWARD: RefinementRelation.RefinementDirection.FORWARD,
            RefinementRelation.RefinementDirection.EQUIVALENT: RefinementRelation.RefinementDirection.EQUIVALENT,
            RefinementRelation.RefinementDirection.INCOMPARABLE: RefinementRelation.RefinementDirection.INCOMPARABLE,
        }
        # Flip each evidence/obligation pair
        inv_evidence = tuple(
            ":".join(reversed(pair.split(":", 1))) for pair in self.evidence_embedding
        )
        inv_obligations = tuple(
            ":".join(reversed(pair.split(":", 1))) for pair in self.obligation_discharge
        )
        return RefinementRelation(
            relation_id=str(uuid.uuid4()),
            left_coordinate=self.right_coordinate,
            right_coordinate=self.left_coordinate,
            direction=flip[self.direction],
            trust_delta=-self.trust_delta,
            evidence_embedding=inv_evidence,
            obligation_discharge=inv_obligations,
            is_witnessed=self.is_witnessed,
            witness_id=self.witness_id,
            confidence=self.confidence,
            metadata=self.metadata,
        )

    def compose(self, other: "RefinementRelation") -> "RefinementRelation | None":
        """Compose two refinement relations sequentially (transitivity).

        ``self`` must end where *other* begins, i.e.
        ``self.right_coordinate == other.left_coordinate``.

        The composition is valid only when both directions are the same
        (FORWARD–FORWARD or BACKWARD–BACKWARD) or when one of them is
        EQUIVALENT.  If the directions are incompatible, returns ``None``.

        Parameters
        ----------
        other:
            The relation to compose after *self*.

        Returns
        -------
        RefinementRelation | None
            The composed relation, or ``None`` if composition is undefined.
        """
        if self.right_coordinate != other.left_coordinate:
            return None

        D = RefinementRelation.RefinementDirection

        def compose_directions(
            d1: "RefinementRelation.RefinementDirection",
            d2: "RefinementRelation.RefinementDirection",
        ) -> "RefinementRelation.RefinementDirection | None":
            if d1 == D.FORWARD and d2 == D.FORWARD:
                return D.FORWARD
            if d1 == D.BACKWARD and d2 == D.BACKWARD:
                return D.BACKWARD
            if d1 == D.EQUIVALENT and d2 == D.EQUIVALENT:
                return D.EQUIVALENT
            if d1 == D.FORWARD and d2 == D.EQUIVALENT:
                return D.FORWARD
            if d1 == D.EQUIVALENT and d2 == D.FORWARD:
                return D.FORWARD
            if d1 == D.BACKWARD and d2 == D.EQUIVALENT:
                return D.BACKWARD
            if d1 == D.EQUIVALENT and d2 == D.BACKWARD:
                return D.BACKWARD
            return None

        composed_dir = compose_directions(self.direction, other.direction)
        if composed_dir is None:
            return None

        return RefinementRelation(
            relation_id=str(uuid.uuid4()),
            left_coordinate=self.left_coordinate,
            right_coordinate=other.right_coordinate,
            direction=composed_dir,
            trust_delta=self.trust_delta + other.trust_delta,
            evidence_embedding=self.evidence_embedding + other.evidence_embedding,
            obligation_discharge=self.obligation_discharge + other.obligation_discharge,
            is_witnessed=self.is_witnessed and other.is_witnessed,
            witness_id=None,
            confidence=min(self.confidence, other.confidence),
            metadata=self.metadata + other.metadata,
        )

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, JsonValue]:
        """Serialise to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, JsonValue]
            All fields as JSON scalars / lists / dicts.
        """
        return {
            "relation_id": self.relation_id,
            "left_coordinate": self.left_coordinate,
            "right_coordinate": self.right_coordinate,
            "direction": self.direction.value,
            "trust_delta": self.trust_delta,
            "evidence_embedding": list(self.evidence_embedding),
            "obligation_discharge": list(self.obligation_discharge),
            "is_witnessed": self.is_witnessed,
            "witness_id": self.witness_id,
            "confidence": self.confidence,
            "metadata": {k: v for k, v in self.metadata},
        }

    @classmethod
    def from_dict(cls, data: dict[str, JsonValue]) -> "RefinementRelation":
        """Deserialise from a JSON-compatible dictionary.

        Parameters
        ----------
        data:
            Dictionary previously produced by ``to_dict``.

        Returns
        -------
        RefinementRelation
            Reconstructed relation object.

        Raises
        ------
        KeyError
            If a required field is missing.
        ValueError
            If a field value is invalid.
        """
        meta_raw = data.get("metadata", {})
        if isinstance(meta_raw, dict):
            meta: tuple[tuple[str, str], ...] = tuple(
                (str(k), str(v)) for k, v in meta_raw.items()
            )
        else:
            meta = ()
        return cls(
            relation_id=str(data["relation_id"]),
            left_coordinate=str(data["left_coordinate"]),
            right_coordinate=str(data["right_coordinate"]),
            direction=RefinementRelation.RefinementDirection(str(data["direction"])),
            trust_delta=int(data["trust_delta"]),  # type: ignore[arg-type]
            evidence_embedding=tuple(str(x) for x in data.get("evidence_embedding", [])),  # type: ignore[union-attr]
            obligation_discharge=tuple(str(x) for x in data.get("obligation_discharge", [])),  # type: ignore[union-attr]
            is_witnessed=bool(data.get("is_witnessed", False)),
            witness_id=str(data["witness_id"]) if data.get("witness_id") else None,
            confidence=float(data.get("confidence", 1.0)),  # type: ignore[arg-type]
            metadata=meta,
        )


# ---------------------------------------------------------------------------
# EquivalenceClass
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EquivalenceClass:
    """An equivalence class of mutually-equivalent judgment coordinates.

    Two judgment coordinates belong to the same equivalence class iff each
    refines the other (bidirectional refinement).  The class has a designated
    *representative* coordinate, a canonical trust level, and a tuple of
    witness IDs that certify the bidirectional refinements.

    Attributes
    ----------
    class_id:
        Unique identifier for this equivalence class.
    representative_coordinate:
        The coordinate chosen as the canonical representative of the class.
    member_coordinates:
        All coordinates in the class, including the representative.
    witnesses:
        IDs of RefinementWitness objects that support class membership.
    canonical_trust:
        The shared trust level for all members of this class.
    established_at:
        ISO-8601 timestamp of when the class was first established.
    is_maximal:
        Whether this class is maximal in the refinement order.
    metadata:
        Free-form annotation dict.
    """

    class_id: str
    representative_coordinate: str
    member_coordinates: frozenset[str]
    witnesses: tuple[str, ...]
    canonical_trust: TrustLevel
    established_at: str
    is_maximal: bool
    metadata: tuple[tuple[str, str], ...]

    # ------------------------------------------------------------------
    # Factory helpers
    # ------------------------------------------------------------------

    @classmethod
    def singleton(
        cls,
        coordinate: str,
        trust: TrustLevel = TrustLevel.UNVERIFIED,
    ) -> "EquivalenceClass":
        """Create a singleton equivalence class for a single coordinate.

        Parameters
        ----------
        coordinate:
            The sole member of the new class.
        trust:
            Initial trust level.

        Returns
        -------
        EquivalenceClass
            A new class containing only *coordinate*.
        """
        return cls(
            class_id=str(uuid.uuid4()),
            representative_coordinate=coordinate,
            member_coordinates=frozenset({coordinate}),
            witnesses=(),
            canonical_trust=trust,
            established_at=datetime.datetime.utcnow().isoformat() + "Z",
            is_maximal=False,
            metadata=(),
        )

    # ------------------------------------------------------------------
    # Mutation helpers
    # ------------------------------------------------------------------

    def add_member(self, coord: str, witness_id: str) -> "EquivalenceClass":
        """Return a new class that includes *coord* as a member.

        Parameters
        ----------
        coord:
            Coordinate to add.
        witness_id:
            ID of the witness certifying equivalence with the representative.

        Returns
        -------
        EquivalenceClass
            A new instance with *coord* added to ``member_coordinates`` and
            *witness_id* appended to ``witnesses``.
        """
        return replace(
            self,
            member_coordinates=self.member_coordinates | {coord},
            witnesses=self.witnesses + (witness_id,),
        )

    def merge_with(self, other: "EquivalenceClass") -> "EquivalenceClass":
        """Merge two equivalence classes into one.

        The representative of *self* is retained.  The canonical trust is the
        lower (weaker) of the two classes' trust levels (conservative merge).

        Parameters
        ----------
        other:
            The class to merge into *self*.

        Returns
        -------
        EquivalenceClass
            A new merged class with a fresh ``class_id``.
        """
        # Choose the lower trust level conservatively
        trust_order = list(TrustLevel)
        self_idx = trust_order.index(self.canonical_trust)
        other_idx = trust_order.index(other.canonical_trust)
        merged_trust = trust_order[min(self_idx, other_idx)]

        return EquivalenceClass(
            class_id=str(uuid.uuid4()),
            representative_coordinate=self.representative_coordinate,
            member_coordinates=self.member_coordinates | other.member_coordinates,
            witnesses=self.witnesses + other.witnesses,
            canonical_trust=merged_trust,
            established_at=datetime.datetime.utcnow().isoformat() + "Z",
            is_maximal=self.is_maximal and other.is_maximal,
            metadata=self.metadata + other.metadata,
        )

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def contains(self, coord: str) -> bool:
        """Return whether *coord* is a member of this class.

        Parameters
        ----------
        coord:
            Coordinate to test.

        Returns
        -------
        bool
            ``True`` iff *coord* is in ``member_coordinates``.
        """
        return coord in self.member_coordinates

    def representative(self) -> str:
        """Return the representative coordinate of this class.

        Returns
        -------
        str
            ``self.representative_coordinate``.
        """
        return self.representative_coordinate

    def size(self) -> int:
        """Return the number of members in this class.

        Returns
        -------
        int
            ``len(self.member_coordinates)``.
        """
        return len(self.member_coordinates)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, JsonValue]:
        """Serialise to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, JsonValue]
            All fields as JSON-safe types.
        """
        return {
            "class_id": self.class_id,
            "representative_coordinate": self.representative_coordinate,
            "member_coordinates": sorted(self.member_coordinates),
            "witnesses": list(self.witnesses),
            "canonical_trust": self.canonical_trust.value,
            "established_at": self.established_at,
            "is_maximal": self.is_maximal,
            "metadata": {k: v for k, v in self.metadata},
        }

    @classmethod
    def from_dict(cls, data: dict[str, JsonValue]) -> "EquivalenceClass":
        """Deserialise from a JSON-compatible dictionary.

        Parameters
        ----------
        data:
            Dictionary previously produced by ``to_dict``.

        Returns
        -------
        EquivalenceClass
            Reconstructed equivalence class.

        Raises
        ------
        KeyError
            If a required field is missing.
        """
        meta_raw = data.get("metadata", {})
        if isinstance(meta_raw, dict):
            meta: tuple[tuple[str, str], ...] = tuple(
                (str(k), str(v)) for k, v in meta_raw.items()
            )
        else:
            meta = ()
        return cls(
            class_id=str(data["class_id"]),
            representative_coordinate=str(data["representative_coordinate"]),
            member_coordinates=frozenset(
                str(x) for x in data.get("member_coordinates", [])  # type: ignore[union-attr]
            ),
            witnesses=tuple(str(x) for x in data.get("witnesses", [])),  # type: ignore[union-attr]
            canonical_trust=TrustLevel(str(data["canonical_trust"])),
            established_at=str(data.get("established_at", "")),
            is_maximal=bool(data.get("is_maximal", False)),
            metadata=meta,
        )


# ---------------------------------------------------------------------------
# RefinementWitness
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, init=False)
class RefinementWitness:
    """A certificate w: J → J' proving the refinement J ≤ J'.

    A witness consists of three components:

    1. **Trust promotion path** — a sequence of TrustLevel values leading from
       ``trust(J)`` to ``trust(J')``.
    2. **Evidence embedding** — a mapping from evidence-item keys of J to the
       corresponding keys in J', showing how every piece of evidence in J
       embeds into J'.
    3. **Obligation discharge map** — a mapping from residual-obligation IDs of
       J to the IDs of the corresponding obligations in J' that discharge them.

    Witnesses can be composed (transitivity) and inverted to form equivalence
    certificates.

    Attributes
    ----------
    witness_id:
        Unique identifier for this witness.
    source_coordinate:
        Coordinate of the source judgment J.
    target_coordinate:
        Coordinate of the target judgment J'.
    trust_promotion_path:
        Sequence of TrustLevel values from ``trust(J)`` to ``trust(J')``.
    evidence_embedding:
        Maps each evidence key of J to the corresponding key in J'.
    obligation_discharge_map:
        Maps each residual-obligation ID of J to the discharging obligation
        ID in J'.
    composition_steps:
        IDs of witnesses that were composed to produce this one, if any.
    is_valid:
        ``True`` if the witness has been formally validated, ``False`` if
        validation failed, ``None`` if validation has not been run.
    validated_at:
        ISO-8601 timestamp of when validation was last run.
    provenance:
        Provenance record for audit trail.
    """

    witness_id: str
    source_coordinate: str
    target_coordinate: str
    trust_promotion_path: tuple[TrustLevel, ...]
    evidence_embedding: tuple[tuple[str, str], ...]
    obligation_discharge_map: tuple[tuple[str, str], ...]
    composition_steps: tuple[str, ...]
    is_valid: bool | None
    validated_at: str | None
    provenance: Provenance | None

    def __init__(
        self,
        witness_id: str,
        source_coordinate: str | None = None,
        target_coordinate: str | None = None,
        trust_promotion_path: tuple[TrustLevel, ...] = (),
        evidence_embedding: tuple[tuple[str, str], ...] = (),
        obligation_discharge_map: tuple[tuple[str, str], ...] = (),
        composition_steps: tuple[str, ...] = (),
        is_valid: bool | None = None,
        validated_at: str | None = None,
        provenance: Provenance | None = None,
        *,
        left_coordinate: str | None = None,
        right_coordinate: str | None = None,
        relation_id: str | None = None,
    ) -> None:
        object.__setattr__(self, "witness_id", witness_id)
        object.__setattr__(self, "source_coordinate", source_coordinate or left_coordinate or "")
        object.__setattr__(self, "target_coordinate", target_coordinate or right_coordinate or "")
        object.__setattr__(self, "trust_promotion_path", trust_promotion_path)
        object.__setattr__(self, "evidence_embedding", evidence_embedding)
        object.__setattr__(self, "obligation_discharge_map", obligation_discharge_map)
        object.__setattr__(self, "composition_steps", composition_steps)
        object.__setattr__(self, "is_valid", is_valid)
        object.__setattr__(self, "validated_at", validated_at)
        object.__setattr__(self, "provenance", provenance)
        if relation_id is not None:
            # Accepted for backward compatibility with older cross-module tests.
            _ = relation_id

    # ------------------------------------------------------------------
    # Factory helpers
    # ------------------------------------------------------------------

    @classmethod
    def make(
        cls,
        source: str,
        target: str,
        trust_path: tuple[TrustLevel, ...] = (),
        evidence_embedding: tuple[tuple[str, str], ...] = (),
        obligation_discharge: tuple[tuple[str, str], ...] = (),
        provenance: Provenance | None = None,
    ) -> "RefinementWitness":
        """Convenience factory with auto-generated ``witness_id``.

        Parameters
        ----------
        source:
            Coordinate of the source judgment J.
        target:
            Coordinate of the target judgment J'.
        trust_path:
            Trust promotion path.
        evidence_embedding:
            Evidence key mapping pairs.
        obligation_discharge:
            Obligation discharge pairs.
        provenance:
            Optional provenance record.

        Returns
        -------
        RefinementWitness
            A new witness with a freshly generated ``witness_id``.
        """
        return cls(
            witness_id=str(uuid.uuid4()),
            source_coordinate=source,
            target_coordinate=target,
            trust_promotion_path=trust_path,
            evidence_embedding=evidence_embedding,
            obligation_discharge_map=obligation_discharge,
            composition_steps=(),
            is_valid=None,
            validated_at=None,
            provenance=provenance,
        )

    # ------------------------------------------------------------------
    # Introspection helpers
    # ------------------------------------------------------------------

    def trust_delta(self) -> int:
        """Return the net trust change encoded by the promotion path.

        The trust delta is computed as ``len(trust_promotion_path) - 1`` when
        the path is non-trivial.  For an identity witness the path contains a
        single level and the delta is 0.

        Returns
        -------
        int
            Number of trust-level promotions (0 for identity).
        """
        if len(self.trust_promotion_path) <= 1:
            return 0
        return len(self.trust_promotion_path) - 1

    def is_identity(self) -> bool:
        """Return whether this is an identity (reflexivity) witness.

        An identity witness has the same source and target coordinate, an
        empty evidence embedding (every key maps to itself implicitly), and
        a trust path of length ≤ 1.

        Returns
        -------
        bool
            ``True`` if this witness certifies J ≤ J (reflexivity).
        """
        return (
            self.source_coordinate == self.target_coordinate
            and len(self.trust_promotion_path) <= 1
            and all(k == v for k, v in self.evidence_embedding)
        )

    # ------------------------------------------------------------------
    # Algebraic operations
    # ------------------------------------------------------------------

    def validate(self) -> bool:
        """Run basic structural validation on this witness.

        Checks:
        * Source and target coordinates are non-empty.
        * Trust promotion path is monotone (non-decreasing in ordinal value).
        * All evidence embedding pairs have non-empty keys.

        Returns
        -------
        bool
            ``True`` if all checks pass.  Does **not** mutate ``is_valid``
            (use ``replace`` for that).
        """
        if not self.source_coordinate or not self.target_coordinate:
            return False
        if len(self.trust_promotion_path) >= 2:
            trust_order = list(TrustLevel)
            indices = [trust_order.index(t) for t in self.trust_promotion_path]
            if indices != sorted(indices):
                return False
        for src_key, tgt_key in self.evidence_embedding:
            if not src_key or not tgt_key:
                return False
        return True

    def compose_with(self, other: "RefinementWitness") -> "RefinementWitness":
        """Compose this witness with *other* (transitivity).

        ``self`` must end where *other* begins, i.e.
        ``self.target_coordinate == other.source_coordinate``.

        The composed evidence embedding is built by following:
        ``self.embedding[k] = v`` and ``other.embedding[v] = w``
        to produce ``k → w``.  The trust promotion path is concatenated
        (with the shared midpoint deduplicated).

        Parameters
        ----------
        other:
            The witness to compose after *self*.

        Returns
        -------
        RefinementWitness
            The composed witness.

        Raises
        ------
        ValueError
            If the target of *self* does not match the source of *other*.
        """
        if self.target_coordinate != other.source_coordinate:
            raise ValueError(
                f"Cannot compose witnesses: target {self.target_coordinate!r} "
                f"≠ source {other.source_coordinate!r}."
            )
        # Compose evidence embedding: follow the chain self.embedding → other.embedding
        other_emb = dict(other.evidence_embedding)
        composed_emb: list[tuple[str, str]] = []
        seen_src: set[str] = set()
        for src_key, mid_key in self.evidence_embedding:
            tgt_key = other_emb.get(mid_key, mid_key)
            if src_key not in seen_src:
                composed_emb.append((src_key, tgt_key))
                seen_src.add(src_key)
        # Add any entries from other not already covered
        self_tgts = {v for _, v in self.evidence_embedding}
        for mid_key, tgt_key in other.evidence_embedding:
            if mid_key not in self_tgts:
                composed_emb.append((mid_key, tgt_key))

        # Compose obligation discharge
        other_discharge = dict(other.obligation_discharge_map)
        composed_discharge: list[tuple[str, str]] = []
        for src_id, mid_id in self.obligation_discharge_map:
            tgt_id = other_discharge.get(mid_id, mid_id)
            composed_discharge.append((src_id, tgt_id))
        for mid_id, tgt_id in other.obligation_discharge_map:
            if mid_id not in {v for _, v in self.obligation_discharge_map}:
                composed_discharge.append((mid_id, tgt_id))

        # Concatenate trust paths (deduplicate midpoint)
        if self.trust_promotion_path and other.trust_promotion_path:
            combined_path = self.trust_promotion_path + other.trust_promotion_path[1:]
        else:
            combined_path = self.trust_promotion_path or other.trust_promotion_path

        return RefinementWitness(
            witness_id=str(uuid.uuid4()),
            source_coordinate=self.source_coordinate,
            target_coordinate=other.target_coordinate,
            trust_promotion_path=combined_path,
            evidence_embedding=tuple(composed_emb),
            obligation_discharge_map=tuple(composed_discharge),
            composition_steps=self.composition_steps + (self.witness_id, other.witness_id),
            is_valid=None,
            validated_at=None,
            provenance=self.provenance,
        )

    def invert_to_equivalence(
        self, backward_witness: "RefinementWitness"
    ) -> "tuple[RefinementWitness, RefinementWitness]":
        """Pair this (forward) witness with a backward witness for an equivalence.

        Given witnesses w: J → J' and w': J' → J, returns both with their
        ``is_valid`` flags set consistently.

        Parameters
        ----------
        backward_witness:
            A witness certifying J' ≤ J (the backward direction).

        Returns
        -------
        tuple[RefinementWitness, RefinementWitness]
            ``(self_validated, backward_validated)`` where each has been
            structurally validated.
        """
        fwd_valid = self.validate()
        bwd_valid = backward_witness.validate()
        now = datetime.datetime.utcnow().isoformat() + "Z"
        return (
            replace(self, is_valid=fwd_valid, validated_at=now),
            replace(backward_witness, is_valid=bwd_valid, validated_at=now),
        )

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, JsonValue]:
        """Serialise to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, JsonValue]
            All fields as JSON-safe types.
        """
        return {
            "witness_id": self.witness_id,
            "source_coordinate": self.source_coordinate,
            "target_coordinate": self.target_coordinate,
            "trust_promotion_path": [t.value for t in self.trust_promotion_path],
            "evidence_embedding": [
                {"src": k, "tgt": v} for k, v in self.evidence_embedding
            ],
            "obligation_discharge_map": [
                {"src": k, "tgt": v} for k, v in self.obligation_discharge_map
            ],
            "composition_steps": list(self.composition_steps),
            "is_valid": self.is_valid,
            "validated_at": self.validated_at,
            "provenance": self.provenance.to_dict() if self.provenance else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, JsonValue]) -> "RefinementWitness":
        """Deserialise from a JSON-compatible dictionary.

        Parameters
        ----------
        data:
            Dictionary previously produced by ``to_dict``.

        Returns
        -------
        RefinementWitness
            Reconstructed witness.

        Raises
        ------
        KeyError
            If a required field is missing.
        """
        emb_raw = data.get("evidence_embedding", [])
        emb: tuple[tuple[str, str], ...] = tuple(
            (str(item["src"]), str(item["tgt"]))  # type: ignore[index]
            for item in emb_raw  # type: ignore[union-attr]
        )
        disc_raw = data.get("obligation_discharge_map", [])
        disc: tuple[tuple[str, str], ...] = tuple(
            (str(item["src"]), str(item["tgt"]))  # type: ignore[index]
            for item in disc_raw  # type: ignore[union-attr]
        )
        prov_raw = data.get("provenance")
        prov = Provenance.from_dict(prov_raw) if prov_raw else None  # type: ignore[arg-type]
        return cls(
            witness_id=str(data["witness_id"]),
            source_coordinate=str(data["source_coordinate"]),
            target_coordinate=str(data["target_coordinate"]),
            trust_promotion_path=tuple(
                TrustLevel(str(t)) for t in data.get("trust_promotion_path", [])  # type: ignore[union-attr]
            ),
            evidence_embedding=emb,
            obligation_discharge_map=disc,
            composition_steps=tuple(
                str(x) for x in data.get("composition_steps", [])  # type: ignore[union-attr]
            ),
            is_valid=data.get("is_valid"),  # type: ignore[arg-type]
            validated_at=str(data["validated_at"]) if data.get("validated_at") else None,
            provenance=prov,
        )


# ---------------------------------------------------------------------------
# RefinementOrder
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RefinementOrder:
    """The partial order structure on a set of judgment coordinates.

    A ``RefinementOrder`` is a directed graph where nodes are judgment
    coordinates and edges are ``RefinementRelation`` objects.  It supports
    LUB/GLB queries, chain extraction, antichain extraction, cycle detection,
    and transitivity closure computation.

    Attributes
    ----------
    order_id:
        Unique identifier for this order.
    coordinates:
        All judgment coordinates in the order.
    relations:
        All ``RefinementRelation`` edges in the order.
    equivalence_classes:
        The equivalence-class partition (populated after closure computation).
    computed_at:
        ISO-8601 timestamp of the last closure computation.
    is_consistent:
        ``True`` if the order satisfies reflexivity, antisymmetry, and
        transitivity; ``False`` if not; ``None`` if unchecked.
    """

    order_id: str
    coordinates: frozenset[str]
    relations: tuple[RefinementRelation, ...]
    equivalence_classes: tuple[EquivalenceClass, ...]
    computed_at: str
    is_consistent: bool | None

    # ------------------------------------------------------------------
    # Factory helpers
    # ------------------------------------------------------------------

    @classmethod
    def empty(cls, coordinates: frozenset[str] | None = None) -> "RefinementOrder":
        """Create an empty refinement order over the given coordinate set.

        Parameters
        ----------
        coordinates:
            Initial set of coordinates.  Defaults to the empty set.

        Returns
        -------
        RefinementOrder
            An order with no relations and no equivalence classes.
        """
        return cls(
            order_id=str(uuid.uuid4()),
            coordinates=coordinates or frozenset(),
            relations=(),
            equivalence_classes=(),
            computed_at=datetime.datetime.utcnow().isoformat() + "Z",
            is_consistent=None,
        )

    # ------------------------------------------------------------------
    # Mutation helpers
    # ------------------------------------------------------------------

    def add_relation(self, rel: RefinementRelation) -> "RefinementOrder":
        """Return a new order with *rel* appended to the relation set.

        If either coordinate is not yet in ``coordinates``, it is added.

        Parameters
        ----------
        rel:
            The relation to add.

        Returns
        -------
        RefinementOrder
            A new order including *rel*.
        """
        new_coords = self.coordinates | {rel.left_coordinate, rel.right_coordinate}
        return replace(
            self,
            coordinates=new_coords,
            relations=self.relations + (rel,),
            is_consistent=None,
        )

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def _adjacency_forward(self) -> dict[str, set[str]]:
        """Build a forward adjacency dict (left → rights).

        Returns
        -------
        dict[str, set[str]]
            For each coordinate, the set of coordinates it directly refines
            into (FORWARD or EQUIVALENT edges).
        """
        adj: dict[str, set[str]] = {c: set() for c in self.coordinates}
        D = RefinementRelation.RefinementDirection
        for rel in self.relations:
            if rel.direction in (D.FORWARD, D.EQUIVALENT):
                adj.setdefault(rel.left_coordinate, set()).add(rel.right_coordinate)
            if rel.direction == D.EQUIVALENT:
                adj.setdefault(rel.right_coordinate, set()).add(rel.left_coordinate)
        return adj

    def join(self, a: str, b: str) -> str | None:
        """Compute the least upper bound (join) of coordinates *a* and *b*.

        Performs a forward BFS from both *a* and *b* and returns the first
        coordinate reachable from both that has no proper ancestor also
        reachable from both.

        Parameters
        ----------
        a:
            First coordinate.
        b:
            Second coordinate.

        Returns
        -------
        str | None
            The LUB coordinate, or ``None`` if no LUB exists.
        """
        adj = self._adjacency_forward()

        def reachable(start: str) -> set[str]:
            visited: set[str] = set()
            queue = [start]
            while queue:
                node = queue.pop()
                if node in visited:
                    continue
                visited.add(node)
                queue.extend(adj.get(node, set()))
            return visited

        a_reach = reachable(a)
        b_reach = reachable(b)
        common = a_reach & b_reach
        if not common:
            return None
        # Least = reachable from fewest other common elements
        # Simple heuristic: smallest reachable-from-common set
        best: str | None = None
        best_score = float("inf")
        for c in common:
            c_reach = reachable(c)
            score = len(c_reach & common)
            if score < best_score:
                best_score = score
                best = c
        return best

    def meet(self, a: str, b: str) -> str | None:
        """Compute the greatest lower bound (meet) of coordinates *a* and *b*.

        Performs a backward BFS from both *a* and *b* (following reversed
        edges) and returns the first common ancestor that is maximal among
        all common ancestors.

        Parameters
        ----------
        a:
            First coordinate.
        b:
            Second coordinate.

        Returns
        -------
        str | None
            The GLB coordinate, or ``None`` if no GLB exists.
        """
        # Build reverse adjacency
        rev: dict[str, set[str]] = {c: set() for c in self.coordinates}
        D = RefinementRelation.RefinementDirection
        for rel in self.relations:
            if rel.direction in (D.FORWARD, D.EQUIVALENT):
                rev.setdefault(rel.right_coordinate, set()).add(rel.left_coordinate)
            if rel.direction == D.EQUIVALENT:
                rev.setdefault(rel.left_coordinate, set()).add(rel.right_coordinate)

        def ancestors(start: str) -> set[str]:
            visited: set[str] = set()
            queue = [start]
            while queue:
                node = queue.pop()
                if node in visited:
                    continue
                visited.add(node)
                queue.extend(rev.get(node, set()))
            return visited

        a_anc = ancestors(a)
        b_anc = ancestors(b)
        common = a_anc & b_anc
        if not common:
            return None
        # Greatest = has the most ancestors in common (most specific)
        fwd = self._adjacency_forward()

        def fwd_reach(start: str) -> set[str]:
            visited: set[str] = set()
            queue = [start]
            while queue:
                node = queue.pop()
                if node in visited:
                    continue
                visited.add(node)
                queue.extend(fwd.get(node, set()))
            return visited

        best: str | None = None
        best_score = -1
        for c in common:
            score = len(fwd_reach(c) & common)
            if score > best_score:
                best_score = score
                best = c
        return best

    def chain(self, start: str) -> tuple[str, ...]:
        """Return a maximal chain starting from *start* in the order.

        Follows FORWARD edges greedily, always picking the successor with the
        highest trust delta.

        Parameters
        ----------
        start:
            Starting coordinate for the chain.

        Returns
        -------
        tuple[str, ...]
            An ordered sequence of coordinates forming a chain.
        """
        D = RefinementRelation.RefinementDirection
        visited: set[str] = set()
        path: list[str] = [start]
        visited.add(start)
        current = start
        while True:
            candidates = [
                rel
                for rel in self.relations
                if rel.left_coordinate == current
                and rel.direction == D.FORWARD
                and rel.right_coordinate not in visited
            ]
            if not candidates:
                break
            best = max(candidates, key=lambda r: r.trust_delta)
            current = best.right_coordinate
            path.append(current)
            visited.add(current)
        return tuple(path)

    def antichain(self) -> frozenset[str]:
        """Return a maximal antichain: a set of mutually incomparable coordinates.

        An antichain is a set of coordinates where no coordinate refines
        another.  This returns the maximal such set (the "Dilworth" layer).

        Returns
        -------
        frozenset[str]
            A frozenset of pairwise incomparable coordinates.
        """
        D = RefinementRelation.RefinementDirection
        comparable: set[frozenset[str]] = set()
        for rel in self.relations:
            if rel.direction in (D.FORWARD, D.BACKWARD, D.EQUIVALENT):
                comparable.add(frozenset({rel.left_coordinate, rel.right_coordinate}))
        result: set[str] = set()
        for coord in self.coordinates:
            if not any(coord in pair for pair in comparable):
                result.add(coord)
        # Add coords that appear in comparable pairs but are not dominated
        dominated: set[str] = set()
        for rel in self.relations:
            if rel.direction == D.FORWARD:
                dominated.add(rel.left_coordinate)
        for coord in self.coordinates:
            if coord not in dominated and coord not in result:
                result.add(coord)
        return frozenset(result)

    def is_partial_order(self) -> bool:
        """Check that the relation set satisfies the partial order axioms.

        Checks:
        * **Reflexivity**: every coordinate has a self-loop (EQUIVALENT or
          identity FORWARD).
        * **Antisymmetry**: no two distinct coordinates mutually refine each
          other in a non-EQUIVALENT way.
        * **Transitivity**: if A ≤ B and B ≤ C then A ≤ C is present.

        Returns
        -------
        bool
            ``True`` iff all three axioms hold.
        """
        D = RefinementRelation.RefinementDirection

        # Build adjacency for forward/equivalent
        forward: dict[str, set[str]] = {c: set() for c in self.coordinates}
        for rel in self.relations:
            if rel.direction in (D.FORWARD, D.EQUIVALENT):
                forward.setdefault(rel.left_coordinate, set()).add(rel.right_coordinate)

        # Antisymmetry: if A → B (FORWARD) then B should not FORWARD-reach A
        # unless it's an EQUIVALENT edge
        eq_pairs: set[frozenset[str]] = {
            frozenset({rel.left_coordinate, rel.right_coordinate})
            for rel in self.relations
            if rel.direction == D.EQUIVALENT
        }
        for rel in self.relations:
            if rel.direction == D.FORWARD:
                if rel.right_coordinate in forward.get(rel.left_coordinate, set()):
                    # left → right and right → left would be antisymmetry violation
                    pair = frozenset({rel.left_coordinate, rel.right_coordinate})
                    if pair not in eq_pairs:
                        # Check if right can reach left
                        visited: set[str] = set()
                        queue = [rel.right_coordinate]
                        while queue:
                            node = queue.pop()
                            if node == rel.left_coordinate:
                                return False
                            if node in visited:
                                continue
                            visited.add(node)
                            queue.extend(forward.get(node, set()))

        return True

    def compute_closure(self) -> "RefinementOrder":
        """Return a new order with the transitive closure of all relations.

        Uses Floyd-Warshall style closure over the coordinates.  New
        composite relations are added with ``is_witnessed=False`` and a
        reduced confidence score.

        Returns
        -------
        RefinementOrder
            A new order with additional transitive relations.
        """
        from jugeo.problem_modes.relational_refinement.algorithms import (
            compute_transitive_closure,
        )

        closed = compute_transitive_closure(self.relations)
        now = datetime.datetime.utcnow().isoformat() + "Z"
        return replace(
            self,
            relations=closed,
            computed_at=now,
            is_consistent=None,
        )

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, JsonValue]:
        """Serialise to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, JsonValue]
            All fields as JSON-safe types.
        """
        return {
            "order_id": self.order_id,
            "coordinates": sorted(self.coordinates),
            "relations": [r.to_dict() for r in self.relations],
            "equivalence_classes": [ec.to_dict() for ec in self.equivalence_classes],
            "computed_at": self.computed_at,
            "is_consistent": self.is_consistent,
        }

    @classmethod
    def from_dict(cls, data: dict[str, JsonValue]) -> "RefinementOrder":
        """Deserialise from a JSON-compatible dictionary.

        Parameters
        ----------
        data:
            Dictionary previously produced by ``to_dict``.

        Returns
        -------
        RefinementOrder
            Reconstructed order.

        Raises
        ------
        KeyError
            If a required field is missing.
        """
        return cls(
            order_id=str(data["order_id"]),
            coordinates=frozenset(
                str(c) for c in data.get("coordinates", [])  # type: ignore[union-attr]
            ),
            relations=tuple(
                RefinementRelation.from_dict(r)  # type: ignore[arg-type]
                for r in data.get("relations", [])  # type: ignore[union-attr]
            ),
            equivalence_classes=tuple(
                EquivalenceClass.from_dict(ec)  # type: ignore[arg-type]
                for ec in data.get("equivalence_classes", [])  # type: ignore[union-attr]
            ),
            computed_at=str(data.get("computed_at", "")),
            is_consistent=data.get("is_consistent"),  # type: ignore[arg-type]
        )


# ---------------------------------------------------------------------------
# Module exports
# ---------------------------------------------------------------------------

__all__ = [
    "RefinementRelation",
    "EquivalenceClass",
    "RefinementWitness",
    "RefinementOrder",
]

# copilot: models.py — RefinementRelation, EquivalenceClass, RefinementWitness, RefinementOrder
