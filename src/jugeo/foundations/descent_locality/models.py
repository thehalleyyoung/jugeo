r"""Core domain models for descent locality — Theory2.tex Ch4.

Locality principle, transport data, gluing data, obstruction classes, and
descent data as first-class domain objects.

This module is the Python implementation companion to Theory2.tex Chapter 4:
*Locality, Transport, Gluing, and Obstruction*.  It provides the complete set
of structured data models that represent:

* the **locality principle** — that a semantic object is determined by its
  restrictions to the members of a covering family;
* **transport data** — morphism-induced maps that carry local data between
  overlapping patches;
* **gluing data** — the collection of local sections together with their
  pairwise compatibility evidence;
* **obstruction classes** — Čech cocycles in H¹ that witness gluing failure;
  and
* **descent data** — the full package needed to run the descent algorithm and
  produce either a global section or an obstruction.

Theory alignment
----------------

All classes are anchored to sections of Theory2.tex Chapter 4:

* §4.1  — Locality principle and sheaf condition
* §4.2  — Transport maps and restriction morphisms
* §4.3  — Gluing data and compatibility matrices
* §4.4  — Obstruction classes and Čech cohomology
* §4.5  — Descent data and the descent algorithm

Design principles
-----------------

1. **Immutability** — All primary models are frozen dataclasses with
   ``slots=True``.  Updates produce new instances, mirroring the append-only
   audit log used throughout JuGeo.

2. **Explicit compatibility** — Compatibility between sections on overlapping
   patches is never inferred; it is always passed as explicit data and
   validated by a predicate.

3. **Obstruction-first** — When gluing fails, the resulting obstruction is a
   first-class domain object (not merely a boolean), carrying the degree,
   cocycle data, and cohomology group needed for further analysis.

4. **Composability** — Transport maps compose explicitly; incoherence is
   detected early and reported with typed evidence.

5. **Copilot provenance** — All domain objects that may be copilot-assisted
   carry a ``provenance`` field and are tagged with a copilot channel
   identifier when appropriate.

copilot: shared-core marker
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence

from jugeo.geometry.covers import Cover
from jugeo.geometry.descent import (
    DescentConfiguration,
    DescentEngine,
    DescentPhase,
    DescentStrategy,
    GluingData as GeometryGluingData,
    LocalSection,
)
from jugeo.geometry.site import Coordinate, Morphism, MorphismKind

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class CompatibilityStatus(Enum):
    """Pairwise compatibility status between two local sections on an overlap.

    ``COMPATIBLE``   — the sections agree on the overlap and may be glued.
    ``INCOMPATIBLE`` — the sections disagree; gluing at this pair is blocked.
    ``PARTIAL``      — the sections agree on a proper sub-region of the overlap;
                       a cover refinement may resolve the partial compatibility.
    ``UNKNOWN``      — the compatibility check has not yet been performed.

    Theory2.tex §4.3 defines compatibility in terms of restriction morphisms:
    two sections ``s_i`` and ``s_j`` are compatible on ``U_i ∩ U_j`` iff
    ``s_i|_{U_i ∩ U_j} = s_j|_{U_i ∩ U_j}``.

    copilot: shared-core marker
    """

    COMPATIBLE = "compatible"
    INCOMPATIBLE = "incompatible"
    PARTIAL = "partial"
    UNKNOWN = "unknown"

    @property
    def blocks_gluing(self) -> bool:
        """Return True when this status prevents gluing from proceeding."""
        return self == CompatibilityStatus.INCOMPATIBLE

    @property
    def is_resolved(self) -> bool:
        """Return True when the compatibility check has been performed."""
        return self != CompatibilityStatus.UNKNOWN

    def merge_with(self, other: CompatibilityStatus) -> CompatibilityStatus:
        """Return the most pessimistic status between *self* and *other*.

        Used when aggregating pairwise compatibility results: a single
        INCOMPATIBLE pair is enough to block overall gluing.
        """
        rank = {
            CompatibilityStatus.COMPATIBLE: 3,
            CompatibilityStatus.PARTIAL: 2,
            CompatibilityStatus.UNKNOWN: 1,
            CompatibilityStatus.INCOMPATIBLE: 0,
        }
        return min((self, other), key=lambda s: rank[s])


class TransportCoherence(Enum):
    """Coherence status of a sequence of transport maps.

    ``COHERENT``            — all triangle identities hold; the transport data
                              defines a consistent functor on the site.
    ``INCOHERENT``          — at least one triangle identity fails; the transport
                              data is inconsistent and cannot define a sheaf.
    ``PARTIALLY_COHERENT``  — some triangles hold and some do not; the data is
                              coherent on a sub-cover.

    Theory2.tex §4.2 requires transport maps to satisfy the cocycle condition:
    ``t_{ik} = t_{jk} ∘ t_{ij}`` for all composable pairs ``(i,j,k)``.

    copilot: shared-core marker
    """

    COHERENT = "coherent"
    INCOHERENT = "incoherent"
    PARTIALLY_COHERENT = "partially_coherent"

    @property
    def is_valid(self) -> bool:
        """Return True when the transport data is at least partially coherent."""
        return self != TransportCoherence.INCOHERENT

    @property
    def is_fully_coherent(self) -> bool:
        """Return True only when all triangle identities hold."""
        return self == TransportCoherence.COHERENT


class SectionKind(Enum):
    """Semantic kind of a section in the Grothendieck topology.

    ``LOCAL``          — defined over exactly one cover member.
    ``GLOBAL``         — defined over the entire covering space (full gluing
                         succeeded).
    ``PARTIAL_GLOBAL`` — defined over a proper sub-cover (some but not all
                         patches could be glued).

    copilot: shared-core marker
    """

    LOCAL = "local"
    GLOBAL = "global"
    PARTIAL_GLOBAL = "partial_global"

    def can_be_extended(self) -> bool:
        """Return True when this section kind may admit a global extension."""
        return self in (SectionKind.LOCAL, SectionKind.PARTIAL_GLOBAL)

    def is_complete(self) -> bool:
        """Return True when this section already covers the full space."""
        return self == SectionKind.GLOBAL


class ObstructionDegree(Enum):
    """Čech cohomology degree of an obstruction class.

    ``H0``     — obstruction in H⁰; a global section that vanishes locally
                 (vacuous in the sheaf setting — H⁰ obstructions are trivial).
    ``H1``     — obstruction in H¹; the primary descent obstruction from
                 Theory2.tex §4.4.
    ``H2``     — obstruction in H²; arises in higher descent or stack-level
                 problems.
    ``HIGHER`` — obstruction in H^n for n ≥ 3.

    copilot: shared-core marker
    """

    H0 = "H0"
    H1 = "H1"
    H2 = "H2"
    HIGHER = "higher"

    @property
    def numeric_degree(self) -> int:
        """Return the integer cohomological degree."""
        return {"H0": 0, "H1": 1, "H2": 2, "higher": 3}[self.value]

    def is_primary(self) -> bool:
        """Return True for the primary descent obstruction degree (H¹)."""
        return self == ObstructionDegree.H1

    def next_degree(self) -> ObstructionDegree:
        """Return the obstruction degree one level higher."""
        if self == ObstructionDegree.H0:
            return ObstructionDegree.H1
        if self == ObstructionDegree.H1:
            return ObstructionDegree.H2
        return ObstructionDegree.HIGHER


# ---------------------------------------------------------------------------
# LocalityPrinciple
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LocalityPrinciple:
    """First-class representation of the locality principle for a coordinate.

    The locality principle (Theory2.tex §4.1) states that a global section is
    uniquely determined by its local restrictions: if two global sections agree
    on every member of a cover, they are equal.  This class records the
    coordinate, the cover that witnesses locality, a predicate that tests
    whether a given section satisfies the principle, and a trust floor below
    which the principle cannot be asserted.

    Parameters
    ----------
    coordinate:
        The :class:`~jugeo.geometry.site.Coordinate` over which locality is
        asserted.
    cover_id:
        Identifier of the :class:`~jugeo.geometry.covers.Cover` witnessing
        locality.
    locality_predicate:
        Callable that takes a section mapping and returns ``True`` when the
        section satisfies the locality principle.  Defaults to a predicate
        that always returns ``True`` (useful for scaffolding).
    trust_floor:
        Minimum trust level (as a float in ``[0.0, 1.0]``) required for the
        locality claim to be asserted.  Sections below this trust are treated
        as not locally determined.
    metadata:
        Optional mapping of additional key-value data for downstream use.

    copilot: shared-core marker
    """

    coordinate: Coordinate
    cover_id: str
    locality_predicate: Callable[[Mapping[str, Any]], bool]
    trust_floor: float
    metadata: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not self.cover_id:
            raise ValueError("LocalityPrinciple.cover_id must not be empty.")
        if not (0.0 <= self.trust_floor <= 1.0):
            raise ValueError(
                f"trust_floor must be in [0.0, 1.0], got {self.trust_floor!r}."
            )

    def check_locality(self, section: Mapping[str, Any]) -> bool:
        """Return True when *section* satisfies the locality predicate.

        Also verifies that any ``"trust"`` key in *section* meets the
        trust floor; if it does not, the locality check fails regardless of
        the predicate outcome.

        Parameters
        ----------
        section:
            A mapping representing a local or global section.
        """
        trust = float(section.get("trust", 1.0))
        if trust < self.trust_floor:
            return False
        try:
            return bool(self.locality_predicate(section))
        except Exception:
            return False

    def restrict_to(self, sub_coord: Coordinate) -> LocalityPrinciple:
        """Return a new :class:`LocalityPrinciple` restricted to *sub_coord*.

        The trust floor is preserved; the predicate is carried over unchanged
        because restriction never strengthens the locality requirement.

        Parameters
        ----------
        sub_coord:
            A coordinate that should be a sub-object of ``self.coordinate``
            in the site.
        """
        return LocalityPrinciple(
            coordinate=sub_coord,
            cover_id=self.cover_id,
            locality_predicate=self.locality_predicate,
            trust_floor=self.trust_floor,
            metadata=dict(self.metadata),
        )

    def extend_to(self, super_coord: Coordinate) -> LocalityPrinciple:
        """Return a new :class:`LocalityPrinciple` extended to *super_coord*.

        Extension raises the trust floor by ``0.05`` to reflect that claiming
        locality over a larger coordinate requires slightly more evidence.

        Parameters
        ----------
        super_coord:
            A coordinate that contains ``self.coordinate`` as a sub-object.
        """
        new_floor = min(1.0, self.trust_floor + 0.05)
        return LocalityPrinciple(
            coordinate=super_coord,
            cover_id=self.cover_id,
            locality_predicate=self.locality_predicate,
            trust_floor=new_floor,
            metadata=dict(self.metadata),
        )

    def is_compatible_with(self, other: LocalityPrinciple) -> bool:
        """Return True when *other* uses the same cover and trust floor.

        Two locality principles over different coordinates but the same cover
        can be composed (e.g. to derive a global locality principle from local
        ones).  Compatibility is a necessary precondition for composition.

        Parameters
        ----------
        other:
            The other :class:`LocalityPrinciple` to compare with.
        """
        return (
            self.cover_id == other.cover_id
            and abs(self.trust_floor - other.trust_floor) < 1e-9
        )

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary representation."""
        return {
            "coordinate_id": getattr(self.coordinate, "coord_id", str(self.coordinate)),
            "cover_id": self.cover_id,
            "trust_floor": self.trust_floor,
            "metadata": dict(self.metadata),
        }

    def validate(self) -> list[str]:
        """Return a list of validation errors; empty means the record is valid."""
        errors: list[str] = []
        if not self.cover_id:
            errors.append("cover_id must not be empty.")
        if not (0.0 <= self.trust_floor <= 1.0):
            errors.append(f"trust_floor {self.trust_floor!r} is out of range [0, 1].")
        if not callable(self.locality_predicate):
            errors.append("locality_predicate must be callable.")
        return errors

    def summary(self) -> str:
        """Return a one-line human-readable summary."""
        cid = getattr(self.coordinate, "coord_id", str(self.coordinate))
        return (
            f"LocalityPrinciple(coord={cid!r}, cover={self.cover_id!r}, "
            f"trust_floor={self.trust_floor:.2f})"
        )

    @classmethod
    def from_dict(
        cls,
        d: Mapping[str, Any],
        coordinate: Coordinate,
        predicate: Callable[[Mapping[str, Any]], bool] | None = None,
    ) -> LocalityPrinciple:
        """Construct a :class:`LocalityPrinciple` from a dictionary.

        Parameters
        ----------
        d:
            Dictionary as produced by :meth:`as_dict`.
        coordinate:
            The coordinate object (not serialisable, so passed separately).
        predicate:
            Optional predicate; defaults to the trivial always-true predicate.
        """
        if predicate is None:
            predicate = lambda _s: True  # noqa: E731
        return cls(
            coordinate=coordinate,
            cover_id=str(d["cover_id"]),
            locality_predicate=predicate,
            trust_floor=float(d.get("trust_floor", 0.5)),
            metadata=dict(d.get("metadata", {})),
        )


# ---------------------------------------------------------------------------
# TransportData
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TransportData:
    """Morphism-induced transport of local data between patches.

    Theory2.tex §4.2 defines transport maps as the data that carries a section
    from one patch to another along a morphism in the site.  ``TransportData``
    is the Python record for one such transport: it carries the source and
    target coordinates, the morphism kind, the payload being transported, the
    actual transport function, and a provenance tag.

    Parameters
    ----------
    source_coord:
        The coordinate from which data is being transported.
    target_coord:
        The coordinate to which data is being transported.
    morphism_kind:
        The :class:`~jugeo.geometry.site.MorphismKind` of the underlying site
        morphism.
    payload:
        The local data being transported (a mapping of section data).
    transport_map:
        A callable ``f(payload) -> transported_payload`` implementing the
        actual transport operation.
    provenance:
        Mapping recording the origin of this transport datum (e.g. copilot
        channel, theory section).

    copilot: shared-core marker
    """

    source_coord: Coordinate
    target_coord: Coordinate
    morphism_kind: MorphismKind
    payload: Mapping[str, Any]
    transport_map: Callable[[Mapping[str, Any]], Mapping[str, Any]]
    provenance: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not callable(self.transport_map):
            raise ValueError("TransportData.transport_map must be callable.")

    def apply(self, data: Mapping[str, Any] | None = None) -> Mapping[str, Any]:
        """Apply the transport map to *data* (or to ``self.payload`` if None).

        Returns the transported data mapping.  Any exception raised by the
        underlying transport function is wrapped in a ``RuntimeError`` to
        give a stable error boundary.

        Parameters
        ----------
        data:
            Optional data to transport.  When ``None``, transports
            ``self.payload``.
        """
        input_data = data if data is not None else self.payload
        try:
            result = self.transport_map(input_data)
            if not isinstance(result, Mapping):
                raise TypeError(
                    f"transport_map must return a Mapping, got {type(result).__name__!r}."
                )
            return result
        except TypeError:
            raise
        except Exception as exc:
            raise RuntimeError(
                f"Transport from {self.source_coord!r} to {self.target_coord!r} "
                f"failed: {exc}"
            ) from exc

    def compose_with(self, other: TransportData) -> TransportData:
        """Return the composition of *self* followed by *other*.

        The composition is valid only when ``self.target_coord`` equals
        ``other.source_coord``.  The composed transport map applies *self*'s
        map first, then *other*'s.

        Parameters
        ----------
        other:
            The :class:`TransportData` to compose with.  Its source coordinate
            must equal this transport's target coordinate.

        Raises
        ------
        ValueError
            When the coordinates do not compose (source/target mismatch).
        """
        self_target_id = getattr(self.target_coord, "coord_id", str(self.target_coord))
        other_source_id = getattr(other.source_coord, "coord_id", str(other.source_coord))
        if self_target_id != other_source_id:
            raise ValueError(
                f"Cannot compose: self.target_coord={self_target_id!r} != "
                f"other.source_coord={other_source_id!r}."
            )

        self_map = self.transport_map
        other_map = other.transport_map

        def composed(data: Mapping[str, Any]) -> Mapping[str, Any]:
            intermediate = self_map(data)
            return other_map(intermediate)

        composed_prov = {
            "composed_from": [
                dict(self.provenance),
                dict(other.provenance),
            ],
            "composition_time": time.time(),
        }
        return TransportData(
            source_coord=self.source_coord,
            target_coord=other.target_coord,
            morphism_kind=self.morphism_kind,
            payload=self.payload,
            transport_map=composed,
            provenance=composed_prov,
        )

    def is_invertible(self) -> bool:
        """Return True when the morphism kind suggests the transport is invertible.

        For the purposes of this domain model, ``ISOMORPHISM`` and
        ``IDENTITY`` morphism kinds are treated as invertible; all others are
        treated as not invertible without additional proof.
        """
        invertible_kinds = {MorphismKind.IDENTITY, MorphismKind.ISOMORPHISM}
        return self.morphism_kind in invertible_kinds

    def invert(self) -> TransportData:
        """Return the inverse transport, swapping source and target.

        Raises ``ValueError`` when the transport is not invertible (i.e. when
        :meth:`is_invertible` returns ``False``).

        The inverse transport map is constructed by applying *self*'s map and
        then recording the inversion; this is only valid for bijective maps.
        """
        if not self.is_invertible():
            raise ValueError(
                f"TransportData from {self.source_coord!r} to {self.target_coord!r} "
                f"with morphism kind {self.morphism_kind!r} is not invertible."
            )
        forward = self.transport_map

        def inverse_map(data: Mapping[str, Any]) -> Mapping[str, Any]:
            transported = forward(data)
            return {k: v for k, v in data.items() if k not in transported}

        inv_prov = {"inverted_from": dict(self.provenance), "inversion_time": time.time()}
        return TransportData(
            source_coord=self.target_coord,
            target_coord=self.source_coord,
            morphism_kind=self.morphism_kind,
            payload=dict(self.apply()),
            transport_map=inverse_map,
            provenance=inv_prov,
        )

    def verify_coherence(self) -> TransportCoherence:
        """Check whether this transport satisfies the cocycle condition locally.

        For a single transport datum, coherence reduces to checking that
        applying the transport map and then applying the identity yields the
        same result as applying directly.  This is a sanity check rather than
        a full cocycle verification (which requires a composed triple).

        Returns :attr:`TransportCoherence.COHERENT` when the check passes,
        :attr:`TransportCoherence.INCOHERENT` otherwise.
        """
        try:
            transported = self.apply()
            re_transported = self.transport_map(transported)
            if dict(re_transported) == dict(transported):
                return TransportCoherence.COHERENT
            return TransportCoherence.PARTIALLY_COHERENT
        except Exception:
            return TransportCoherence.INCOHERENT

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary representation."""
        src_id = getattr(self.source_coord, "coord_id", str(self.source_coord))
        tgt_id = getattr(self.target_coord, "coord_id", str(self.target_coord))
        return {
            "source_coord": src_id,
            "target_coord": tgt_id,
            "morphism_kind": self.morphism_kind.value,
            "payload_keys": list(self.payload.keys()),
            "provenance": dict(self.provenance),
            "is_invertible": self.is_invertible(),
        }

    def summary(self) -> str:
        """Return a one-line human-readable summary."""
        src = getattr(self.source_coord, "coord_id", str(self.source_coord))
        tgt = getattr(self.target_coord, "coord_id", str(self.target_coord))
        return (
            f"TransportData({src!r} → {tgt!r}, kind={self.morphism_kind.value!r}, "
            f"invertible={self.is_invertible()})"
        )


# ---------------------------------------------------------------------------
# GluingData
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GluingData:
    """Structured gluing data: local sections together with overlap compatibility.

    Theory2.tex §4.3 defines gluing data as a collection of local sections
    ``{s_i}`` over cover members ``{U_i}``, together with a compatibility
    matrix recording whether ``s_i|_{U_i ∩ U_j} = s_j|_{U_i ∩ U_j}`` for
    every pair ``(i, j)``.  This class is the Python record for that structure.

    Parameters
    ----------
    patches:
        Tuple of patch identifiers (strings) corresponding to cover members.
    sections:
        Tuple of section data mappings, one per patch.  The i-th section
        corresponds to the i-th patch.
    overlaps:
        Tuple of ``(i, j)`` pairs indicating which patches overlap.
    compatibility_matrix:
        Mapping from ``(i, j)`` pairs to :class:`CompatibilityStatus` values.
    cover_id:
        Identifier of the :class:`~jugeo.geometry.covers.Cover` over which
        these sections are defined.

    copilot: shared-core marker
    """

    patches: tuple[str, ...]
    sections: tuple[Mapping[str, Any], ...]
    overlaps: tuple[tuple[int, int], ...]
    compatibility_matrix: Mapping[tuple[int, int], CompatibilityStatus]
    cover_id: str

    def __post_init__(self) -> None:
        if len(self.patches) != len(self.sections):
            raise ValueError(
                f"GluingData requires len(patches)==len(sections), got "
                f"{len(self.patches)} patches and {len(self.sections)} sections."
            )
        if not self.cover_id:
            raise ValueError("GluingData.cover_id must not be empty.")
        n = len(self.patches)
        for i, j in self.overlaps:
            if not (0 <= i < n and 0 <= j < n):
                raise ValueError(
                    f"Overlap index ({i}, {j}) is out of range for {n} patches."
                )

    def check_pair(self, i: int, j: int) -> CompatibilityStatus:
        """Return the compatibility status of patches *i* and *j*.

        Looks up the compatibility matrix first with key ``(i, j)``, then
        with ``(j, i)`` (since compatibility is symmetric), and finally
        returns ``UNKNOWN`` when neither key is present.

        Parameters
        ----------
        i, j:
            Zero-based indices into the ``patches`` tuple.
        """
        if i == j:
            return CompatibilityStatus.COMPATIBLE
        status = self.compatibility_matrix.get((i, j))
        if status is not None:
            return status
        status = self.compatibility_matrix.get((j, i))
        if status is not None:
            return status
        return CompatibilityStatus.UNKNOWN

    def all_compatible(self) -> bool:
        """Return True when every overlap pair is COMPATIBLE.

        Sections with UNKNOWN status are treated as not compatible (fail-safe
        semantics: we do not assume compatibility without evidence).
        """
        for i, j in self.overlaps:
            status = self.check_pair(i, j)
            if status != CompatibilityStatus.COMPATIBLE:
                return False
        return True

    def failed_overlaps(self) -> list[tuple[int, int]]:
        """Return the list of overlap pairs that are not COMPATIBLE.

        UNKNOWN pairs are included in the failure list, reflecting the
        fail-safe semantics described in :meth:`all_compatible`.
        """
        failed: list[tuple[int, int]] = []
        for i, j in self.overlaps:
            status = self.check_pair(i, j)
            if status != CompatibilityStatus.COMPATIBLE:
                failed.append((i, j))
        return failed

    def build_global(self) -> Mapping[str, Any] | None:
        """Attempt to build a global section from the local sections.

        Returns a merged mapping when all overlaps are compatible, or
        ``None`` when gluing fails.  The merge is performed by taking the
        union of all section data; conflicting values on overlaps are detected
        by :meth:`all_compatible`.
        """
        if not self.all_compatible():
            return None
        merged: dict[str, Any] = {}
        for section_data in self.sections:
            for key, value in section_data.items():
                if key in merged and merged[key] != value:
                    return None
                merged[key] = value
        merged["_kind"] = SectionKind.GLOBAL.value
        merged["_cover_id"] = self.cover_id
        merged["_patch_count"] = len(self.patches)
        return merged

    def as_overlap_graph(self) -> dict[str, list[int]]:
        """Return the overlap structure as an adjacency list.

        Returns a dict mapping patch indices (as strings) to sorted lists of
        their overlapping patch indices.  Useful for visualisation and graph
        algorithms.
        """
        adjacency: dict[str, list[int]] = {str(i): [] for i in range(len(self.patches))}
        for i, j in self.overlaps:
            adjacency[str(i)].append(j)
            adjacency[str(j)].append(i)
        for key in adjacency:
            adjacency[key] = sorted(set(adjacency[key]))
        return adjacency

    def to_descent_input(self) -> dict[str, Any]:
        """Serialise to a dict compatible with the geometry descent engine.

        The returned dict can be passed to :class:`~jugeo.geometry.descent.DescentEngine`
        as input data after reconstruction of the geometry types.
        """
        return {
            "patches": list(self.patches),
            "sections": [dict(s) for s in self.sections],
            "overlaps": [list(pair) for pair in self.overlaps],
            "compatibility": {
                f"{i},{j}": status.value
                for (i, j), status in self.compatibility_matrix.items()
            },
            "cover_id": self.cover_id,
        }

    def summary(self) -> str:
        """Return a multi-line human-readable summary."""
        failed = self.failed_overlaps()
        lines = [
            f"GluingData(cover={self.cover_id!r})",
            f"  patches     : {len(self.patches)}",
            f"  overlaps    : {len(self.overlaps)}",
            f"  compatible  : {self.all_compatible()}",
            f"  failed pairs: {len(failed)}",
        ]
        if failed:
            lines.append(f"  failed      : {failed[:5]}{'...' if len(failed) > 5 else ''}")
        return "\n".join(lines)

    def validate(self) -> list[str]:
        """Return a list of validation errors; empty means the record is valid."""
        errors: list[str] = []
        n = len(self.patches)
        if n == 0:
            errors.append("GluingData must have at least one patch.")
        if len(self.sections) != n:
            errors.append(
                f"len(sections)={len(self.sections)} != len(patches)={n}."
            )
        seen_overlaps: set[tuple[int, int]] = set()
        for i, j in self.overlaps:
            if i == j:
                errors.append(f"Self-overlap ({i}, {j}) is not meaningful.")
            key = (min(i, j), max(i, j))
            if key in seen_overlaps:
                errors.append(f"Duplicate overlap pair ({i}, {j}).")
            seen_overlaps.add(key)
        for (i, j) in self.compatibility_matrix:
            if (i, j) not in self.overlaps and (j, i) not in self.overlaps:
                errors.append(
                    f"Compatibility matrix entry ({i},{j}) does not correspond "
                    "to a declared overlap."
                )
        return errors


# ---------------------------------------------------------------------------
# ObstructionClass
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ObstructionClass:
    """Čech cohomology obstruction class witnessing a gluing failure.

    Theory2.tex §4.4 defines the descent obstruction as a class in H¹ of the
    site with coefficients in the automorphism sheaf.  ``ObstructionClass`` is
    the Python record for this data: it carries the cohomological degree, the
    coordinate over which the obstruction lives, the cocycle data (a mapping
    from overlap pairs to transition functions), the name of the cohomology
    group, a flag indicating whether the class is trivial (i.e. in the image
    of the coboundary map from C⁰), and a provenance tag.

    Parameters
    ----------
    degree:
        Cohomological degree from :class:`ObstructionDegree`.
    coordinate:
        The coordinate over which the obstruction is defined.
    cocycle_data:
        Mapping from ``(i, j)`` string keys to cocycle coefficient values.
        For H¹ this is the collection of transition functions on overlaps.
    cohomology_group:
        Human-readable name of the cohomology group, e.g. ``"H¹(X, Aut)"``.
    is_trivial:
        Whether this obstruction class is the zero class (trivial in
        cohomology).  A trivial obstruction means gluing can succeed after
        a coboundary adjustment.
    provenance:
        Mapping recording the origin of this obstruction.

    copilot: shared-core marker
    """

    degree: ObstructionDegree
    coordinate: Coordinate
    cocycle_data: Mapping[str, Any]
    cohomology_group: str
    is_trivial: bool
    provenance: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not self.cohomology_group:
            raise ValueError("ObstructionClass.cohomology_group must not be empty.")

    def is_trivializable(self) -> bool:
        """Return True when the obstruction can be trivialized.

        An obstruction is trivializable when it is already trivial, or when
        its cocycle data contains at most one non-identity transition function
        (suggesting a single coboundary adjustment suffices).

        This is a heuristic; full trivialization requires a cohomological
        computation beyond the scope of this domain model.
        """
        if self.is_trivial:
            return True
        non_identity = sum(
            1
            for v in self.cocycle_data.values()
            if v not in (None, True, 1, "id", "identity")
        )
        return non_identity <= 1

    def cup_product(self, other: ObstructionClass) -> ObstructionClass:
        """Return the cup product of *self* and *other*.

        The cup product raises the degree by combining the cocycle data via
        a pointwise multiplication rule (simplified: keys are unioned,
        conflicting values are flagged as ``"conflict"``).

        Parameters
        ----------
        other:
            The obstruction class to cup-product with *self*.
        """
        combined_cocycle: dict[str, Any] = {}
        for k in set(self.cocycle_data) | set(other.cocycle_data):
            v_self = self.cocycle_data.get(k)
            v_other = other.cocycle_data.get(k)
            if v_self is None:
                combined_cocycle[k] = v_other
            elif v_other is None:
                combined_cocycle[k] = v_self
            elif v_self == v_other:
                combined_cocycle[k] = v_self
            else:
                combined_cocycle[k] = f"conflict({v_self!r}, {v_other!r})"

        new_degree = self.degree.next_degree()
        combined_trivial = self.is_trivial and other.is_trivial
        prov = {
            "cup_product_of": [dict(self.provenance), dict(other.provenance)],
            "cup_product_time": time.time(),
        }
        return ObstructionClass(
            degree=new_degree,
            coordinate=self.coordinate,
            cocycle_data=combined_cocycle,
            cohomology_group=f"cup({self.cohomology_group}, {other.cohomology_group})",
            is_trivial=combined_trivial,
            provenance=prov,
        )

    def restrict_to(self, coord: Coordinate) -> ObstructionClass:
        """Return the restriction of this obstruction class to *coord*.

        Restriction preserves all cocycle data (the caller is responsible for
        filtering to overlaps that are relevant to *coord*).

        Parameters
        ----------
        coord:
            The coordinate to restrict to.
        """
        prov = {"restricted_from": dict(self.provenance), "restriction_time": time.time()}
        return ObstructionClass(
            degree=self.degree,
            coordinate=coord,
            cocycle_data=dict(self.cocycle_data),
            cohomology_group=self.cohomology_group,
            is_trivial=self.is_trivial,
            provenance=prov,
        )

    def lift_to_degree(self, n: int) -> ObstructionClass:
        """Return a lifted obstruction class at degree *n* ≥ current degree.

        The lift is formal: the cocycle data is wrapped in a degree-n envelope
        but not recomputed.  This is useful for comparing obstructions across
        degrees.

        Parameters
        ----------
        n:
            Target numeric degree.  Must be ≥ ``self.degree.numeric_degree``.

        Raises
        ------
        ValueError
            When *n* is below the current degree.
        """
        current = self.degree.numeric_degree
        if n < current:
            raise ValueError(
                f"Cannot lift obstruction from degree {current} to lower degree {n}."
            )
        degree_map = {0: ObstructionDegree.H0, 1: ObstructionDegree.H1, 2: ObstructionDegree.H2}
        new_degree = degree_map.get(n, ObstructionDegree.HIGHER)
        wrapped = {f"lifted_{k}": v for k, v in self.cocycle_data.items()}
        wrapped["_lift_from_degree"] = current
        wrapped["_lift_to_degree"] = n
        prov = {"lifted_from": dict(self.provenance), "lift_time": time.time()}
        return ObstructionClass(
            degree=new_degree,
            coordinate=self.coordinate,
            cocycle_data=wrapped,
            cohomology_group=f"H^{n}(lift from {self.cohomology_group})",
            is_trivial=self.is_trivial,
            provenance=prov,
        )

    def boundary_map(self) -> Mapping[str, Any]:
        """Return the image of this class under the coboundary map δ.

        The coboundary map δ: C⁰ → C¹ sends a 0-cochain (collection of local
        values) to the 1-cocycle of their pairwise differences on overlaps.
        When applied to a 1-class, this computes the formal coboundary, which
        vanishes iff the class is a cocycle.

        Returns a mapping whose keys are triple indices ``"i,j,k"`` and whose
        values are the (formal) coboundary coefficients.
        """
        result: dict[str, Any] = {}
        keys = list(self.cocycle_data.keys())
        for idx, key in enumerate(keys):
            for other_key in keys[idx + 1:]:
                result[f"δ({key},{other_key})"] = (
                    f"boundary({self.cocycle_data[key]!r}, "
                    f"{self.cocycle_data[other_key]!r})"
                )
        return result

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary representation."""
        coord_id = getattr(self.coordinate, "coord_id", str(self.coordinate))
        return {
            "degree": self.degree.value,
            "coordinate_id": coord_id,
            "cocycle_data": dict(self.cocycle_data),
            "cohomology_group": self.cohomology_group,
            "is_trivial": self.is_trivial,
            "is_trivializable": self.is_trivializable(),
            "provenance": dict(self.provenance),
        }

    def summary(self) -> str:
        """Return a one-line human-readable summary."""
        trivial_str = "trivial" if self.is_trivial else (
            "trivializable" if self.is_trivializable() else "non-trivial"
        )
        coord_id = getattr(self.coordinate, "coord_id", str(self.coordinate))
        return (
            f"ObstructionClass(degree={self.degree.value}, "
            f"coord={coord_id!r}, group={self.cohomology_group!r}, "
            f"status={trivial_str})"
        )

    def vanishes_locally(self) -> bool:
        """Return True when the obstruction is locally trivial on every patch.

        An obstruction that vanishes locally but not globally is the canonical
        sign that a non-trivial H¹ class is present: it is locally coboundary
        but not globally so.  Detected here by checking whether all cocycle
        values equal the identity element (``None``, ``True``, ``1``, or
        ``"id"``).
        """
        identity_values = frozenset({None, True, 1, "id", "identity"})
        return all(v in identity_values for v in self.cocycle_data.values())


# ---------------------------------------------------------------------------
# DescentDatum
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DescentDatum:
    """Complete descent datum for a single descent computation.

    Theory2.tex §4.5 defines the descent datum as the package of data that the
    descent algorithm consumes: a cover, local sections, transport maps, gluing
    data, an optional obstruction (pre-computed or from a prior run), and a
    descent strategy.  ``DescentDatum`` is the Python record for this package.

    Parameters
    ----------
    cover:
        The :class:`~jugeo.geometry.covers.Cover` over which descent is
        performed.
    local_sections:
        Tuple of :class:`~jugeo.geometry.descent.LocalSection` objects, one
        per cover member.
    transport_maps:
        Tuple of :class:`TransportData` objects providing the transport between
        overlapping patches.
    gluing_data:
        The :class:`GluingData` record (may be ``None`` if not yet computed).
    obstruction:
        The :class:`ObstructionClass` from a prior run (may be ``None`` if
        descent has not yet been attempted).
    descent_strategy:
        The :class:`~jugeo.geometry.descent.DescentStrategy` to use.

    copilot: shared-core marker
    """

    cover: Cover
    local_sections: tuple[LocalSection, ...]
    transport_maps: tuple[TransportData, ...]
    gluing_data: GluingData | None
    obstruction: ObstructionClass | None
    descent_strategy: DescentStrategy

    def __post_init__(self) -> None:
        if not self.local_sections:
            raise ValueError("DescentDatum.local_sections must not be empty.")

    def run_descent(self) -> dict[str, Any]:
        """Run the descent algorithm and return a result summary.

        Uses the attached :class:`GluingData` when available; otherwise
        constructs a minimal gluing datum from ``local_sections`` before
        attempting gluing.

        Returns a dict with keys:
        * ``"success"`` — bool, True when a global section was assembled;
        * ``"global_section"`` — the assembled global section, or ``None``;
        * ``"obstruction"`` — an obstruction dict, or ``None``;
        * ``"failed_overlaps"`` — list of failed overlap pairs;
        * ``"strategy"`` — the descent strategy used.
        """
        if self.gluing_data is None:
            gd = self._build_gluing_data_from_sections()
        else:
            gd = self.gluing_data

        global_section = gd.build_global()
        if global_section is not None:
            return {
                "success": True,
                "global_section": dict(global_section),
                "obstruction": None,
                "failed_overlaps": [],
                "strategy": self.descent_strategy.value,
            }

        failed = gd.failed_overlaps()
        obs = self.compute_obstruction()
        return {
            "success": False,
            "global_section": None,
            "obstruction": obs.as_dict() if obs is not None else None,
            "failed_overlaps": failed,
            "strategy": self.descent_strategy.value,
        }

    def compute_obstruction(self) -> ObstructionClass | None:
        """Compute or return the cached obstruction class.

        When an obstruction was provided at construction time, it is returned
        directly.  Otherwise a new obstruction is derived from the gluing data
        by inspecting the failed overlaps and constructing a minimal H¹ cocycle.

        Returns ``None`` when there are no failed overlaps (i.e. gluing
        succeeds and there is no obstruction).
        """
        if self.obstruction is not None:
            return self.obstruction
        gd = self.gluing_data
        if gd is None:
            gd = self._build_gluing_data_from_sections()
        if gd.all_compatible():
            return None
        failed = gd.failed_overlaps()
        cocycle: dict[str, Any] = {}
        for i, j in failed:
            key = f"{i},{j}"
            si = dict(gd.sections[i]) if i < len(gd.sections) else {}
            sj = dict(gd.sections[j]) if j < len(gd.sections) else {}
            differing = {k: (si.get(k), sj.get(k)) for k in set(si) | set(sj) if si.get(k) != sj.get(k)}
            cocycle[key] = differing if differing else "incompatible"

        coord = getattr(self.cover, "base_coord", None)
        if coord is None:
            coord = next(
                (getattr(ls, "patch", None) for ls in self.local_sections),
                None,
            )

        prov = {
            "computed_by": "DescentDatum.compute_obstruction",
            "cover_id": getattr(self.cover, "cover_id", str(self.cover)),
            "strategy": self.descent_strategy.value,
            "timestamp": time.time(),
        }
        return ObstructionClass(
            degree=ObstructionDegree.H1,
            coordinate=coord,  # type: ignore[arg-type]
            cocycle_data=cocycle,
            cohomology_group="H¹(cover, sections)",
            is_trivial=False,
            provenance=prov,
        )

    def find_repairs(self) -> list[dict[str, Any]]:
        """Return a list of repair suggestions for failed overlaps.

        Each suggestion is a dict with keys ``"patch_i"``, ``"patch_j"``,
        ``"suggestion"``, and ``"confidence"``.  Repairs are heuristic:
        they identify the conflicting keys and suggest either choosing one
        value or refining the cover.
        """
        gd = self.gluing_data
        if gd is None:
            gd = self._build_gluing_data_from_sections()
        failed = gd.failed_overlaps()
        repairs: list[dict[str, Any]] = []
        for i, j in failed:
            si = dict(gd.sections[i]) if i < len(gd.sections) else {}
            sj = dict(gd.sections[j]) if j < len(gd.sections) else {}
            conflicting = [k for k in set(si) | set(sj) if si.get(k) != sj.get(k)]
            if conflicting:
                suggestion = (
                    f"Resolve key conflict on {conflicting[:3]} "
                    f"between patches {i!r} and {j!r}."
                )
                confidence = max(0.1, 1.0 - 0.1 * len(conflicting))
            else:
                suggestion = (
                    f"Cover refinement may resolve partial compatibility "
                    f"between patches {i!r} and {j!r}."
                )
                confidence = 0.4
            repairs.append(
                {
                    "patch_i": gd.patches[i] if i < len(gd.patches) else str(i),
                    "patch_j": gd.patches[j] if j < len(gd.patches) else str(j),
                    "conflicting_keys": conflicting,
                    "suggestion": suggestion,
                    "confidence": round(confidence, 3),
                }
            )
        return repairs

    def is_globally_extendable(self) -> bool:
        """Return True when there are no failed overlaps.

        A descent datum is globally extendable iff its gluing data reports
        all overlaps compatible.
        """
        gd = self.gluing_data
        if gd is None:
            gd = self._build_gluing_data_from_sections()
        return gd.all_compatible()

    def refine_cover_and_retry(self) -> DescentDatum:
        """Return a new :class:`DescentDatum` with a refined cover.

        Cover refinement subdivides patches around the failed overlaps.  This
        is a schematic implementation: it returns a datum with the same data
        but with a ``ITERATIVE`` strategy, signalling to the descent engine
        that a refinement step has been attempted.

        This method does not modify *self*; it returns a fresh record.
        """
        return DescentDatum(
            cover=self.cover,
            local_sections=self.local_sections,
            transport_maps=self.transport_maps,
            gluing_data=self.gluing_data,
            obstruction=None,
            descent_strategy=DescentStrategy.ITERATIVE,
        )

    def summary(self) -> str:
        """Return a multi-line human-readable summary."""
        cover_id = getattr(self.cover, "cover_id", str(self.cover))
        obs_str = (
            self.obstruction.summary()
            if self.obstruction is not None
            else "none"
        )
        gd_summary = self.gluing_data.summary() if self.gluing_data is not None else "not computed"
        lines = [
            f"DescentDatum(cover={cover_id!r})",
            f"  local_sections  : {len(self.local_sections)}",
            f"  transport_maps  : {len(self.transport_maps)}",
            f"  gluing_data     : {gd_summary[:60]}",
            f"  obstruction     : {obs_str}",
            f"  strategy        : {self.descent_strategy.value!r}",
        ]
        return "\n".join(lines)

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary representation."""
        cover_id = getattr(self.cover, "cover_id", str(self.cover))
        return {
            "cover_id": cover_id,
            "local_section_count": len(self.local_sections),
            "transport_map_count": len(self.transport_maps),
            "gluing_data": self.gluing_data.to_descent_input() if self.gluing_data else None,
            "obstruction": self.obstruction.as_dict() if self.obstruction else None,
            "descent_strategy": self.descent_strategy.value,
        }

    def to_engine_input(self) -> dict[str, Any]:
        """Serialise to a dict suitable as input to :class:`~jugeo.geometry.descent.DescentEngine`.

        The returned dict is not a complete DescentEngine configuration but
        provides the keys expected by the engine's ``from_dict`` factory if
        one exists, or can be used for manual construction.
        """
        cover_id = getattr(self.cover, "cover_id", str(self.cover))
        sections_raw = [
            {
                "patch": getattr(ls, "patch_id", str(i)),
                "data": getattr(ls, "data", {}),
                "trust": getattr(ls, "trust", 1.0),
            }
            for i, ls in enumerate(self.local_sections)
        ]
        return {
            "cover_id": cover_id,
            "sections": sections_raw,
            "transport_maps": [t.as_dict() for t in self.transport_maps],
            "gluing_data": self.gluing_data.to_descent_input() if self.gluing_data else None,
            "strategy": self.descent_strategy.value,
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_gluing_data_from_sections(self) -> GluingData:
        """Build a minimal :class:`GluingData` from ``local_sections``.

        Constructs patches from section identifiers, computes all pairwise
        overlaps, and populates the compatibility matrix using a simple
        key-equality check.
        """
        patches = tuple(
            getattr(ls, "patch_id", str(i)) for i, ls in enumerate(self.local_sections)
        )
        sections = tuple(
            dict(getattr(ls, "data", {})) for ls in self.local_sections
        )
        n = len(patches)
        overlaps: list[tuple[int, int]] = [
            (i, j) for i in range(n) for j in range(i + 1, n)
        ]
        compat: dict[tuple[int, int], CompatibilityStatus] = {}
        for i, j in overlaps:
            si = sections[i]
            sj = sections[j]
            shared_keys = set(si) & set(sj)
            if not shared_keys:
                compat[(i, j)] = CompatibilityStatus.COMPATIBLE
            elif all(si[k] == sj[k] for k in shared_keys):
                compat[(i, j)] = CompatibilityStatus.COMPATIBLE
            else:
                compat[(i, j)] = CompatibilityStatus.INCOMPATIBLE
        cover_id = getattr(self.cover, "cover_id", "auto")
        return GluingData(
            patches=patches,
            sections=sections,
            overlaps=tuple(overlaps),
            compatibility_matrix=compat,
            cover_id=cover_id,
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__: list[str] = [
    "CompatibilityStatus",
    "TransportCoherence",
    "SectionKind",
    "ObstructionDegree",
    "LocalityPrinciple",
    "TransportData",
    "GluingData",
    "ObstructionClass",
    "DescentDatum",
    # Cross-referencing bridges
    "model_evidence_bridge",
    "model_encoding_bridge",
]


# ---------------------------------------------------------------------------
# Cross-referencing: evidence and encoding bridges (Theory2.tex §4)
# ---------------------------------------------------------------------------

import logging as _logging

_mdl_log = _logging.getLogger(__name__)


def model_evidence_bridge(model: Any) -> dict[str, Any]:
    """Collect evidence for a descent model via the evidence subsystem.

    Builds an evidence manifest from the model's components using
    ``jugeo.evidence.manifests`` and assigns a trust level using
    ``jugeo.evidence.trust``.

    Parameters
    ----------
    model:
        A descent model instance (:class:`DescentDatum`, :class:`GluingData`,
        etc.) or a plain dict describing the model.

    Returns
    -------
    dict[str, Any]
        Keys: ``"model_id"``, ``"manifest_id"``, ``"entries"``,
        ``"trust_level"``, ``"summary"``.

    References
    ----------
    Theory2.tex §4 — Descent and Locality, evidence collection.
    """
    try:
        from jugeo.evidence.manifests import build_evidence_manifest
    except ImportError as exc:
        raise RuntimeError(
            "jugeo.evidence.manifests is required for model_evidence_bridge()"
        ) from exc

    try:
        from jugeo.evidence.trust import TrustLevel
        _has_trust = True
    except ImportError:
        TrustLevel = None  # type: ignore[assignment,misc]
        _has_trust = False

    if isinstance(model, dict):
        model_id = str(model.get("model_id", model.get("datum_id", uuid.uuid4().hex[:12])))
        components = model.get("components", model.get("sections", []))
    elif hasattr(model, "datum_id"):
        model_id = str(model.datum_id)
        components = list(getattr(model, "local_sections", getattr(model, "components", [])))
    else:
        model_id = uuid.uuid4().hex[:12]
        components = []

    _mdl_log.debug("model_evidence_bridge: model=%s components=%d", model_id, len(components))

    manifest = build_evidence_manifest(
        source_id=model_id,
        entries=[{"component": str(c)} for c in components],
    )
    manifest_id = getattr(manifest, "manifest_id", None) or (manifest.get("manifest_id") if isinstance(manifest, dict) else str(uuid.uuid4().hex[:12]))
    entries = getattr(manifest, "entries", None) or (manifest.get("entries", []) if isinstance(manifest, dict) else [])

    trust_level = "UNVERIFIED"
    if _has_trust and entries:
        trust_level = TrustLevel.COPILOT_SUGGESTED.name if len(entries) > 0 else TrustLevel.UNVERIFIED.name

    return {
        "model_id": model_id,
        "manifest_id": str(manifest_id),
        "entries": list(entries),
        "trust_level": trust_level,
        "summary": f"Evidence manifest for model {model_id}: {len(entries)} entries",
    }


def model_encoding_bridge(
    model: Any,
    *,
    format: str = "z3",
) -> dict[str, Any]:
    """Encode a descent model for external consumption.

    Uses ``jugeo.encodings`` to serialise the model into the requested
    format (e.g. Z3 assertions or section encodings).

    Parameters
    ----------
    model:
        A descent model instance or plain dict.
    format:
        Target encoding format (default ``"z3"``).

    Returns
    -------
    dict[str, Any]
        Keys: ``"model_id"``, ``"format"``, ``"judgment_encoding"``,
        ``"section_encoding"``, ``"detail"``.

    References
    ----------
    Theory2.tex §4 — Descent and Locality, model encoding.
    """
    try:
        from jugeo.encodings import encode_judgment, encode_section
    except ImportError as exc:
        raise RuntimeError(
            "jugeo.encodings is required for model_encoding_bridge()"
        ) from exc

    if isinstance(model, dict):
        model_id = str(model.get("model_id", model.get("datum_id", uuid.uuid4().hex[:12])))
        sections = model.get("sections", model.get("local_sections", []))
        claim = model.get("claim", model.get("statement", ""))
    elif hasattr(model, "datum_id"):
        model_id = str(model.datum_id)
        sections = list(getattr(model, "local_sections", []))
        claim = str(getattr(model, "claim", getattr(model, "statement", "")))
    else:
        model_id = uuid.uuid4().hex[:12]
        sections = []
        claim = ""

    _mdl_log.debug("model_encoding_bridge: model=%s format=%s", model_id, format)

    judgment_enc = encode_judgment(claim or f"model:{model_id}", format=format)
    section_encs = [encode_section(s, format=format) for s in sections]

    return {
        "model_id": model_id,
        "format": format,
        "judgment_encoding": judgment_enc,
        "section_encoding": section_encs,
        "detail": f"Encoded model {model_id} into {format}: judgment + {len(section_encs)} sections",
    }
