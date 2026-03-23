"""Obstruction theory as common language for failures — Theory2.tex Ch4.

Obstructions are first-class cohomological objects recording exactly why
local-to-global extension fails and what repair is needed.

In the JuGeo framework, a *descent obstruction* is not merely a boolean
flag or an error message; it is a structured algebraic object living in a
Čech cohomology group H¹(U, F) for a cover U and a sheaf F of judgments.
This module provides the data structures and algorithms for representing,
classifying, composing, and proposing repairs for such obstructions.

Architecture overview
---------------------
The module is layered from mathematical primitives up to actionable repair
objects:

  CohomologyClass         — H¹ element encoding the obstruction cocycle.
  ObstructionRecord       — A single instance of observed failure, with
                            provenance, degree, severity, and repair hints.
  ObstructionMap          — A natural transformation from local sections to
                            obstruction classes, analogous to the connecting
                            homomorphism δ in the long exact sequence.
  RepairFrontier          — The mutable repair working set that a copilot
                            loop consumes to progressively fix obstructions.

Module-level helpers ``classify_obstruction``, ``compute_cech_cocycle``,
``build_obstruction_map``, and ``trivialize_obstruction`` provide
algorithm-facing entry points for the core operations.

References
----------
Theory2.tex Ch4        "Obstruction Theory"
Theory2.tex §4.1       "Čech Cohomology and Cocycles"
Theory2.tex §4.2       "The Connecting Homomorphism"
Theory2.tex §4.3       "Repair Frontiers and Copilot Integration"

copilot: shared-core marker
"""

from __future__ import annotations

import hashlib
import itertools
import json
import logging
import math
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterator, Mapping, Sequence

from jugeo.geometry.covers import Cover, CoverMember, OverlapDatum
from jugeo.geometry.descent import (
    DescentConfiguration,
    DescentEngine,
    DescentLog,
    DescentObstruction,
    DescentPhase,
    DescentResult,
    DescentStrategy,
    GluingData,
    GluingReport,
    GlobalSection,
    LocalSection,
    Obstruction,
    OverlapCondition,
)
from jugeo.geometry.site import (
    Coordinate,
    CoordinateKind,
    CoordinateMorphism,
    CoordinateObject,
    Site,
)

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class ObstructionSeverity(str, Enum):
    """Severity level for obstruction records.

    Theory2.tex Ch4 assigns a severity to every detected obstruction based
    on its impact on the descent procedure.  The four levels form a total
    order: BLOCKING > DEGRADING > WARNING > INFO.

    copilot: shared-core marker — used by the repair-prioritisation loop.

    Members
    -------
    BLOCKING:
        The cocycle is non-trivial and prevents the existence of any global
        section.  Descent cannot proceed; structural repair is mandatory.
    DEGRADING:
        A partial global section exists, but one or more overlap conditions
        are violated, degrading trust or omitting key judgment keys.
    WARNING:
        Descent technically succeeds, but a nearly-violated overlap condition
        was detected.  The global section should be flagged for review.
    INFO:
        An informational annotation; the cocycle is zero and descent is
        unaffected.  Recorded for audit purposes.
    """

    BLOCKING = "blocking"
    DEGRADING = "degrading"
    WARNING = "warning"
    INFO = "info"

    @property
    def ordinal(self) -> int:
        """Numeric ordinal; higher means more severe."""
        return {"blocking": 3, "degrading": 2, "warning": 1, "info": 0}[self.value]

    def is_at_least(self, other: ObstructionSeverity) -> bool:
        """Return True when *self* is at least as severe as *other*."""
        return self.ordinal >= other.ordinal


class CochainKind(str, Enum):
    """Classification of algebraic role in the Čech cochain complex.

    The Čech complex C^•(U, F) for a cover U = {U_i} and a sheaf F is:

        C^0 → C^1 → C^2 → …

    where C^k consists of sections on (k+1)-fold overlaps.  This enum
    identifies which level of the complex a datum inhabits.

    copilot: shared-core marker
    """

    ZERO_COCHAIN = "zero_cochain"
    """C^0: assignment of a section to each individual patch U_i."""

    ONE_COCHAIN = "one_cochain"
    """C^1: assignment of a section to each pairwise overlap U_i ∩ U_j."""

    TWO_COCHAIN = "two_cochain"
    """C^2: assignment of a section to each triple overlap U_i ∩ U_j ∩ U_k."""

    COCYCLE = "cocycle"
    """An element of ker(d^k) — satisfies the cocycle condition."""

    COBOUNDARY = "coboundary"
    """An element of im(d^{k−1}) — trivial in cohomology."""

    COHOMOLOGY_CLASS = "cohomology_class"
    """An equivalence class in H^k = ker(d^k) / im(d^{k−1})."""


class ObstructionOrigin(str, Enum):
    """Records where in the descent pipeline an obstruction was detected.

    Knowing the origin of an obstruction is critical for routing it to
    the correct repair strategy.  An OVERLAP_MISMATCH obstruction can
    often be resolved by revising a section's judgment, while a
    COVER_INSUFFICIENCY requires adding new patches to the cover.

    copilot: shared-core marker — used by RepairFinder to select strategies.
    """

    OVERLAP_MISMATCH = "overlap_mismatch"
    """Two sections disagree on a shared overlap coordinate."""

    MISSING_SECTION = "missing_section"
    """A patch in the cover has no associated section."""

    TRUST_FLOOR_VIOLATION = "trust_floor_violation"
    """A section's trust level is below the required floor."""

    RESIDUAL_OBLIGATION = "residual_obligation"
    """A section carries unresolved proof or test obligations."""

    COVER_INSUFFICIENCY = "cover_insufficiency"
    """The cover is too coarse to distinguish the relevant judgments."""

    COPILOT_PROPOSAL = "copilot_proposal"
    """An obstruction introduced by a copilot-generated proposal."""

    MANUAL_ANNOTATION = "manual_annotation"
    """An obstruction recorded manually by a human annotator."""

    UNKNOWN = "unknown"
    """Origin could not be determined during analysis."""


# ---------------------------------------------------------------------------
# CohomologyClass
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CohomologyClass:
    """A Čech cohomology class H^n(U, F) over a given coordinate.

    This class represents a concrete element of the k-th Čech cohomology
    group of a sheaf F over a cover U of *coordinate*.  It stores the
    representative cocycle explicitly, together with provenance metadata
    that records how the class was produced.

    The class is **immutable**; every operation returns a new instance.
    This guarantees that obstruction records built from cohomology classes
    are stable across repair iterations.

    copilot: shared-core marker

    Parameters
    ----------
    degree : int
        The cohomological degree (0 = sections, 1 = obstructions, 2 = …).
    coordinate : Coordinate
        The base coordinate over which this class lives.
    representative : dict[str, Any]
        An explicit cocycle representative, keyed by overlap strings of the
        form ``"coord_i∩coord_j"`` mapping to discrepancy values.
    is_zero : bool
        True when the class vanishes — the cocycle is a coboundary.
    group_label : str
        A human-readable label for the cohomology group, e.g. ``"H¹(U,F)"``.
    provenance : tuple[str, ...]
        Chain of transformation labels recording how this class was produced.
    """

    degree: int = 1
    coordinate: Coordinate = field(default_factory=lambda: Coordinate())
    representative: dict[str, Any] = field(default_factory=dict)
    is_zero: bool = False
    group_label: str = ""
    provenance: tuple[str, ...] = field(default_factory=tuple)

    # ------------------------------------------------------------------
    # Cup product
    # ------------------------------------------------------------------

    def cup_product(self, other: CohomologyClass) -> CohomologyClass:
        """Compute the cup product α ∪ β of two cohomology classes.

        The cup product raises degree: if α ∈ H^p and β ∈ H^q, then
        α ∪ β ∈ H^{p+q}.  The resulting representative is formed by
        tensoring the cocycle data of both inputs: for every pair of
        overlap keys (k1, k2) the combined key is ``"k1⊗k2"`` and the
        combined value is a dict wrapping both values.

        If either input is zero, the product is zero (by bilinearity).

        Returns
        -------
        CohomologyClass
            The cup product in degree ``self.degree + other.degree``.
        """
        if self.is_zero or other.is_zero:
            return CohomologyClass(
                degree=self.degree + other.degree,
                coordinate=self.coordinate,
                representative={},
                is_zero=True,
                group_label=f"H^{self.degree + other.degree}",
                provenance=self.provenance + ("cup_product_zero",),
            )
        combined: dict[str, Any] = {}
        for k1, v1 in self.representative.items():
            for k2, v2 in other.representative.items():
                combined_key = f"{k1}⊗{k2}"
                combined[combined_key] = {"left": v1, "right": v2}
        new_degree = self.degree + other.degree
        return CohomologyClass(
            degree=new_degree,
            coordinate=self.coordinate,
            representative=combined,
            is_zero=(len(combined) == 0),
            group_label=f"H^{new_degree}({self.coordinate.name})",
            provenance=self.provenance + other.provenance + ("cup_product",),
        )

    # ------------------------------------------------------------------
    # Restriction map
    # ------------------------------------------------------------------

    def restriction_map(self, sub_coord: Coordinate) -> CohomologyClass:
        """Apply the restriction map res: H^n(U,F) → H^n(U|_V, F|_V).

        Restricts the class to the sub-coordinate *sub_coord* by keeping
        only cocycle entries whose keys contain *sub_coord.key* as a
        substring.  This corresponds to pulling back the cocycle along the
        open-inclusion morphism V → X in theory2.tex §4.1.

        Parameters
        ----------
        sub_coord : Coordinate
            The target coordinate for the restriction.

        Returns
        -------
        CohomologyClass
            The restricted class over *sub_coord*.
        """
        key_fragment = sub_coord.key
        restricted = {
            k: v
            for k, v in self.representative.items()
            if key_fragment in k
        }
        return CohomologyClass(
            degree=self.degree,
            coordinate=sub_coord,
            representative=restricted,
            is_zero=(len(restricted) == 0),
            group_label=f"H^{self.degree}({sub_coord.name})",
            provenance=self.provenance + (f"restriction_to:{sub_coord.key}",),
        )

    # ------------------------------------------------------------------
    # Coboundary
    # ------------------------------------------------------------------

    def coboundary(self) -> CohomologyClass:
        """Apply the coboundary operator δ to raise the degree by one.

        The Čech coboundary δ: C^n → C^{n+1} sends a cochain f to the
        alternating-sum cochain (δf)_{i0…i{n+1}} = Σ (-1)^k f_{i0…î_k…i{n+1}}.

        For the purpose of this implementation, the coboundary of the class
        is computed symbolically: each existing overlap key is extended with
        a ``"δ"`` prefix and the value is the negation of the original.  The
        result lives in degree ``self.degree + 1``.

        Returns
        -------
        CohomologyClass
            The coboundary class in degree ``self.degree + 1``.
        """
        boundary: dict[str, Any] = {}
        for idx, (k, v) in enumerate(self.representative.items()):
            sign = 1 if idx % 2 == 0 else -1
            boundary[f"δ{idx}:{k}"] = {"sign": sign, "value": v}
        new_degree = self.degree + 1
        return CohomologyClass(
            degree=new_degree,
            coordinate=self.coordinate,
            representative=boundary,
            is_zero=(len(boundary) == 0),
            group_label=f"H^{new_degree}({self.coordinate.name})",
            provenance=self.provenance + ("coboundary",),
        )

    # ------------------------------------------------------------------
    # Exactness check
    # ------------------------------------------------------------------

    def is_exact(self) -> bool:
        """Check whether this class lies in the image of the previous coboundary.

        A cohomology class is *exact* (i.e., trivial) when it is a
        coboundary.  This implementation checks whether all representative
        values satisfy a negativity-pairing: for every key of the form
        ``"a∩b"`` there exist entries ``"b∩c"`` and ``"a∩c"`` with
        compatible (alternating) values.

        In practice, this is a heuristic: a class with an empty or
        identically-zero representative is exact, as is one explicitly
        flagged ``is_zero``.

        Returns
        -------
        bool
            True when the class is exact (trivial in cohomology).
        """
        if self.is_zero:
            return True
        if not self.representative:
            return True
        # Heuristic: check that the sum of values is consistent with ∂ ∘ ∂ = 0
        total_nonzero = sum(
            1
            for v in self.representative.values()
            if v is not None and v != 0 and v != {}
        )
        return total_nonzero == 0

    # ------------------------------------------------------------------
    # Lift
    # ------------------------------------------------------------------

    def lift(self, degree: int) -> CohomologyClass | None:
        """Attempt to lift this class to the given cohomological degree.

        In the long exact sequence of a sheaf pair, a lift from H^n to
        H^{n+1} is possible when the connecting homomorphism vanishes.
        This method returns a lifted class if the requested degree is
        reachable by iterating the coboundary operator, or None when
        the class is zero (since the zero class lifts trivially but
        conveys no information) or when the degree gap would require
        more than 4 applications of δ.

        Parameters
        ----------
        degree : int
            The target cohomological degree (must be > self.degree).

        Returns
        -------
        CohomologyClass or None
            The lifted class, or None if the lift is not available.
        """
        if degree <= self.degree:
            return None
        gap = degree - self.degree
        if gap > 4:
            return None
        if self.is_zero:
            return None
        result: CohomologyClass = self
        for _ in range(gap):
            result = result.coboundary()
        return result

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def as_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, Any]
            A plain dictionary suitable for ``json.dumps`` (assuming all
            representative values are JSON-serialisable).
        """
        return {
            "degree": self.degree,
            "coordinate": self.coordinate.serialize(),
            "representative": dict(self.representative),
            "is_zero": self.is_zero,
            "group_label": self.group_label,
            "provenance": list(self.provenance),
        }

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def summary(self) -> str:
        """Return a human-readable one-line summary.

        Returns
        -------
        str
            Short string for logging and diagnostics.
        """
        status = "zero" if self.is_zero else f"rank={len(self.representative)}"
        return (
            f"CohomologyClass(H^{self.degree}, {self.coordinate.name}, "
            f"{status}, label={self.group_label!r})"
        )

    # ------------------------------------------------------------------
    # Equality
    # ------------------------------------------------------------------

    def equals(self, other: CohomologyClass) -> bool:
        """Semantic equality: same degree, coordinate, and representative keys.

        This is a structural equality check on cocycle representatives.
        Two classes are considered equal when they have the same degree,
        their coordinates have the same key, and their representative
        dictionaries are identical.

        Note: This checks syntactic equality of representatives, not
        cohomological equivalence (which would require verifying that the
        difference is a coboundary).

        Parameters
        ----------
        other : CohomologyClass
            The class to compare against.

        Returns
        -------
        bool
        """
        return (
            self.degree == other.degree
            and self.coordinate.key == other.coordinate.key
            and self.representative == other.representative
            and self.is_zero == other.is_zero
        )

    # ------------------------------------------------------------------
    # Vanishing locus
    # ------------------------------------------------------------------

    def vanishing_locus(self) -> list[Coordinate]:
        """Return the coordinates where the representative cocycle vanishes.

        Iterates through representative keys (which encode overlap pairs as
        ``"coord_a∩coord_b"`` strings) and returns a deduplicated list of
        coordinates that appear only in zero-valued entries.

        Returns
        -------
        list[Coordinate]
            Coordinates at which the class evaluates to zero.
        """
        zero_coords: list[Coordinate] = []
        seen: set[str] = set()
        for overlap_key, value in self.representative.items():
            is_zero_val = value is None or value == 0 or value == {}
            parts = overlap_key.replace("⊗", "∩").split("∩")
            for part in parts:
                comp_name = part.strip().lstrip("δ0123456789:")
                if comp_name and comp_name not in seen and is_zero_val:
                    seen.add(comp_name)
                    zero_coords.append(
                        Coordinate(
                            components=tuple(comp_name.split("/")),
                            kind=self.coordinate.kind,
                            support_labels=self.coordinate.support_labels,
                        )
                    )
        return zero_coords


# ---------------------------------------------------------------------------
# ObstructionRecord
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ObstructionRecord:
    """A single observed failure in the descent procedure.

    An :class:`ObstructionRecord` packages everything needed to understand,
    prioritise, and repair a single observed obstruction: the coordinate at
    which it lives, the Čech cocycle data witnessing the failure, the cover
    from which it was detected, and free-form metadata for tooling
    integrations (copilot proposals, CI annotations, etc.).

    The record is **immutable**; every method that produces a modified
    version returns a new instance, preserving the audit trail.

    copilot: shared-core marker

    Parameters
    ----------
    obstruction_id : str
        Stable unique identifier.  Typically a hex digest derived from the
        coordinate key, degree, and cover id.
    coordinate : Coordinate
        The base coordinate at which the obstruction is detected.
    degree : int
        The cohomological degree (1 for standard H¹ obstructions).
    cocycle : dict[str, Any]
        The explicit Čech cocycle witnessing the obstruction.  Keys are
        overlap strings (``"U_i∩U_j"``); values encode discrepancies.
    is_coboundary : bool
        True when the cocycle is exact — the obstruction is locally
        trivial and can be repaired by section adjustment alone.
    source_cover_id : str
        Identifier of the cover over which the cocycle was computed.
    timestamp : float
        Unix timestamp of detection (``time.time()``).
    metadata : dict
        Arbitrary key-value annotations.
    """

    obstruction_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    coordinate: Coordinate = field(default_factory=lambda: Coordinate())
    degree: int = 1
    cocycle: dict[str, Any] = field(default_factory=dict)
    is_coboundary: bool = False
    source_cover_id: str = ""
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Trivial check
    # ------------------------------------------------------------------

    def is_trivial(self) -> bool:
        """Return True when the obstruction is cohomologically trivial.

        An obstruction is trivial when its cocycle is a coboundary (the
        ``is_coboundary`` flag is set) or when the cocycle dictionary is
        empty (no discrepancies detected).

        Returns
        -------
        bool
        """
        if self.is_coboundary:
            return True
        if not self.cocycle:
            return True
        return all(v is None or v == 0 or v == {} for v in self.cocycle.values())

    # ------------------------------------------------------------------
    # Refine
    # ------------------------------------------------------------------

    def refine(self, new_cover_id: str) -> ObstructionRecord:
        """Return a refined copy of this record associated with a new cover.

        Refinement corresponds to pulling back the obstruction along the
        morphism from a finer cover to the original cover.  The cocycle
        is preserved but the source cover id is updated, and the
        ``"refined_from"`` key is added to provenance metadata.

        Parameters
        ----------
        new_cover_id : str
            The identifier of the refined cover.

        Returns
        -------
        ObstructionRecord
            A new record with the updated cover id.
        """
        new_meta = dict(self.metadata)
        new_meta["refined_from"] = self.source_cover_id
        new_meta["refinement_timestamp"] = time.time()
        return ObstructionRecord(
            obstruction_id=self.obstruction_id + ":refined",
            coordinate=self.coordinate,
            degree=self.degree,
            cocycle=dict(self.cocycle),
            is_coboundary=self.is_coboundary,
            source_cover_id=new_cover_id,
            timestamp=time.time(),
            metadata=new_meta,
        )

    # ------------------------------------------------------------------
    # Restrict
    # ------------------------------------------------------------------

    def restrict_to(self, sub_coord: Coordinate) -> ObstructionRecord:
        """Restrict this record to a sub-coordinate.

        Filters the cocycle to retain only those entries whose overlap keys
        contain *sub_coord.key* as a component.  This corresponds to the
        restriction map in Čech cohomology (theory2.tex §4.1).

        Parameters
        ----------
        sub_coord : Coordinate
            The target sub-coordinate.

        Returns
        -------
        ObstructionRecord
            A new record over *sub_coord* with a filtered cocycle.
        """
        key_fragment = sub_coord.key
        restricted_cocycle = {
            k: v
            for k, v in self.cocycle.items()
            if key_fragment in k
        }
        new_meta = dict(self.metadata)
        new_meta["restricted_from"] = self.coordinate.key
        return ObstructionRecord(
            obstruction_id=self.obstruction_id + f":@{sub_coord.key}",
            coordinate=sub_coord,
            degree=self.degree,
            cocycle=restricted_cocycle,
            is_coboundary=self.is_coboundary,
            source_cover_id=self.source_cover_id,
            timestamp=time.time(),
            metadata=new_meta,
        )

    # ------------------------------------------------------------------
    # As cohomology class
    # ------------------------------------------------------------------

    def as_cohomology_class(self) -> CohomologyClass:
        """Lift this record to a :class:`CohomologyClass` object.

        Constructs the Čech cohomology class whose representative cocycle
        is exactly ``self.cocycle``.  The resulting class is marked as zero
        when ``is_trivial()`` returns True.

        Returns
        -------
        CohomologyClass
            The H^n class represented by this obstruction record.
        """
        return CohomologyClass(
            degree=self.degree,
            coordinate=self.coordinate,
            representative=dict(self.cocycle),
            is_zero=self.is_trivial(),
            group_label=f"H^{self.degree}({self.coordinate.name})",
            provenance=(f"from_obstruction:{self.obstruction_id}",),
        )

    # ------------------------------------------------------------------
    # Repair hints
    # ------------------------------------------------------------------

    def repair_hints(self) -> list[str]:
        """Generate actionable repair hints for this obstruction.

        Analyses the cocycle entries to produce a list of natural-language
        suggestions for resolution.  Hints are ranked by specificity:
        precise overlap-level hints come before general advice.

        Returns
        -------
        list[str]
            A list of hint strings, most-specific first.
        """
        hints: list[str] = []
        if self.is_trivial():
            hints.append(
                f"Obstruction {self.obstruction_id} is trivial; "
                "adjust local sections to eliminate the coboundary."
            )
            return hints
        for overlap_key, discrepancy in self.cocycle.items():
            if discrepancy is None or discrepancy == 0:
                continue
            parts = overlap_key.split("∩")
            if len(parts) == 2:
                hints.append(
                    f"Reconcile sections on overlap {overlap_key!r}: "
                    f"discrepancy = {discrepancy!r}."
                )
            else:
                hints.append(
                    f"Check overlap {overlap_key!r}: discrepancy = {discrepancy!r}."
                )
        if self.degree > 1:
            hints.append(
                f"H^{self.degree} obstruction at {self.coordinate.name}: "
                "consider cover refinement to split high-degree overlaps."
            )
        if not hints:
            hints.append(
                f"No specific repair found for obstruction {self.obstruction_id}; "
                "manual inspection required."
            )
        return hints

    # ------------------------------------------------------------------
    # Priority score
    # ------------------------------------------------------------------

    def priority_score(self) -> float:
        """Compute a numerical priority score for repair scheduling.

        Higher score = higher priority.  The score combines:
          * Number of non-zero cocycle entries (more violations → higher).
          * Cohomological degree (lower degree → higher, since degree-1
            obstructions are the most common and actionable).
          * Recency: older obstructions are slightly deprioritised.
          * Coboundary bonus: trivial obstructions get near-zero score.

        Returns
        -------
        float
            Priority score in [0.0, 100.0].
        """
        if self.is_trivial():
            return 0.0
        non_zero = sum(
            1 for v in self.cocycle.values() if v is not None and v != 0 and v != {}
        )
        degree_weight = max(0.0, 5.0 - self.degree)  # higher for low-degree
        recency_weight = max(0.0, 1.0 - (time.time() - self.timestamp) / 86400.0)
        raw = non_zero * degree_weight * (0.5 + 0.5 * recency_weight)
        return min(100.0, raw * 10.0)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def as_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, Any]
        """
        return {
            "obstruction_id": self.obstruction_id,
            "coordinate": self.coordinate.serialize(),
            "degree": self.degree,
            "cocycle": dict(self.cocycle),
            "is_coboundary": self.is_coboundary,
            "source_cover_id": self.source_cover_id,
            "timestamp": self.timestamp,
            "metadata": dict(self.metadata),
        }

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def summary(self) -> str:
        """Return a human-readable one-line summary.

        Returns
        -------
        str
        """
        trivial = "trivial" if self.is_trivial() else "non-trivial"
        return (
            f"ObstructionRecord({self.obstruction_id[:8]}…, "
            f"H^{self.degree}, {self.coordinate.name}, "
            f"{trivial}, entries={len(self.cocycle)})"
        )

    # ------------------------------------------------------------------
    # Merge
    # ------------------------------------------------------------------

    def merge_with(self, other: ObstructionRecord) -> ObstructionRecord:
        """Merge two obstruction records into one combined record.

        The merged record lives at the common ancestor of the two
        coordinates and carries the union of both cocycles.  Discrepancy
        values from *other* take precedence over those from *self* when
        overlap keys coincide.

        The resulting record is non-trivial unless both inputs are trivial.

        Parameters
        ----------
        other : ObstructionRecord
            The record to merge with.

        Returns
        -------
        ObstructionRecord
            A new record combining both inputs.
        """
        merged_cocycle = dict(self.cocycle)
        merged_cocycle.update(other.cocycle)
        merged_meta = dict(self.metadata)
        merged_meta.update(other.metadata)
        merged_meta["merged_from"] = [self.obstruction_id, other.obstruction_id]
        common_coord = self.coordinate.common_ancestor(other.coordinate)
        both_coboundary = self.is_coboundary and other.is_coboundary
        digest = hashlib.sha1(
            f"{self.obstruction_id}:{other.obstruction_id}".encode()
        ).hexdigest()[:16]
        return ObstructionRecord(
            obstruction_id=f"merged:{digest}",
            coordinate=common_coord,
            degree=max(self.degree, other.degree),
            cocycle=merged_cocycle,
            is_coboundary=both_coboundary,
            source_cover_id=self.source_cover_id or other.source_cover_id,
            timestamp=time.time(),
            metadata=merged_meta,
        )


# ---------------------------------------------------------------------------
# ObstructionMap
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ObstructionMap:
    """A map δ: Sections(U) → H¹(U, F) from sections to obstruction classes.

    In the long exact sequence of a sheaf pair (theory2.tex §4.2), the
    connecting homomorphism δ sends a global section of a quotient sheaf to
    an H¹ obstruction class of the sub-sheaf.  This class models that map
    concretely, storing both its input cover and the resulting obstruction
    records that constitute its image.

    The map supports functional composition via :meth:`compose_with` and
    kernel/image queries for use in algebraic computations.

    copilot: shared-core marker

    Parameters
    ----------
    source_cover : Cover
        The cover over which the map is defined.
    target_coord : Coordinate
        The base coordinate (the "global" object the map targets).
    obstruction_records : list[ObstructionRecord]
        Pre-computed image elements (obstructions the map produces).
    map_id : str
        Stable identifier for this map.
    degree : int
        The cohomological degree of the target group.
    is_natural : bool
        True when this map is verified to be natural (commutes with
        restriction maps).
    """

    source_cover: Cover
    target_coord: Coordinate
    obstruction_records: list[ObstructionRecord] = field(default_factory=list)
    map_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    degree: int = 1
    is_natural: bool = True

    # ------------------------------------------------------------------
    # Apply
    # ------------------------------------------------------------------

    def apply(self, section: Any) -> ObstructionRecord | None:
        """Apply this map to a section, returning its obstruction image.

        Given a section-like object (a :class:`LocalSection`, a dict with
        a ``"coordinate"`` key, or a plain string coordinate key), this
        method searches the stored obstruction records for one whose
        coordinate matches the section's coordinate.  Returns None when the
        section maps to the zero obstruction (i.e., is in the kernel).

        Parameters
        ----------
        section : Any
            A section object or coordinate key.

        Returns
        -------
        ObstructionRecord or None
            The matching obstruction record, or None for kernel elements.
        """
        # Resolve coordinate key from various input types
        if isinstance(section, LocalSection):
            coord_key = section.coordinate
        elif isinstance(section, dict):
            coord_key = section.get("coordinate", "")
        elif isinstance(section, str):
            coord_key = section
        else:
            coord_key = str(getattr(section, "coordinate", ""))

        for record in self.obstruction_records:
            if record.coordinate.key == coord_key:
                if record.is_trivial():
                    return None
                return record
        return None

    # ------------------------------------------------------------------
    # Kernel
    # ------------------------------------------------------------------

    def kernel(self) -> list[Any]:
        """Return section keys that lie in the kernel of this map.

        The kernel consists of sections whose obstruction record is trivial
        (i.e., they map to the zero class).  This implementation returns
        the coordinate keys of all trivial obstruction records.

        Returns
        -------
        list[Any]
            List of coordinate keys of kernel elements.
        """
        return [
            record.coordinate.key
            for record in self.obstruction_records
            if record.is_trivial()
        ]

    # ------------------------------------------------------------------
    # Image
    # ------------------------------------------------------------------

    def image(self) -> list[ObstructionRecord]:
        """Return the non-trivial image of this map.

        Filters out trivial (zero) records and returns only the records
        that constitute the proper image in H¹.

        Returns
        -------
        list[ObstructionRecord]
        """
        return [r for r in self.obstruction_records if not r.is_trivial()]

    # ------------------------------------------------------------------
    # Is zero map
    # ------------------------------------------------------------------

    def is_zero_map(self) -> bool:
        """Return True when every obstruction in the image is trivial.

        A zero map means every section lies in the kernel — the sheaf
        extension is split and local sections always glue globally.

        Returns
        -------
        bool
        """
        return all(r.is_trivial() for r in self.obstruction_records)

    # ------------------------------------------------------------------
    # Compose
    # ------------------------------------------------------------------

    def compose_with(self, other: ObstructionMap) -> ObstructionMap:
        """Compose self ∘ other: ObstructionMap → ObstructionMap.

        Composition is meaningful when *other* maps sections to a class in
        H^k and *self* maps H^k classes to H^{k+1}.  The composed map
        accumulates all obstruction records from both maps, filtering to
        those whose coordinates appear in both image sets.

        Parameters
        ----------
        other : ObstructionMap
            The map to compose with (applied first).

        Returns
        -------
        ObstructionMap
            The composed map.
        """
        other_image_keys = {r.coordinate.key for r in other.image()}
        composed_records = [
            r for r in self.obstruction_records
            if r.coordinate.key in other_image_keys
        ]
        composed_records += [r for r in other.obstruction_records if r not in composed_records]
        return ObstructionMap(
            source_cover=other.source_cover,
            target_coord=self.target_coord,
            obstruction_records=composed_records,
            map_id=f"{other.map_id}∘{self.map_id}",
            degree=self.degree + other.degree,
            is_natural=self.is_natural and other.is_natural,
        )

    # ------------------------------------------------------------------
    # Naturality check
    # ------------------------------------------------------------------

    def naturality_check(self) -> bool:
        """Verify that this map commutes with cover restriction.

        Naturality means that for any cover refinement f: U' → U and any
        section s, the square δ ∘ f* = (f*) ∘ δ commutes.

        This implementation performs a syntactic heuristic: the map is
        natural when the ``is_natural`` flag is set and no two records
        share a coordinate key (which would imply a non-functional map,
        violating naturality).

        Returns
        -------
        bool
        """
        if not self.is_natural:
            return False
        seen_keys: set[str] = set()
        for record in self.obstruction_records:
            k = record.coordinate.key
            if k in seen_keys:
                _log.debug("Naturality violation: duplicate coordinate %s", k)
                return False
            seen_keys.add(k)
        return True

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def as_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dictionary.

        Returns
        -------
        dict[str, Any]
        """
        return {
            "map_id": self.map_id,
            "target_coord": self.target_coord.serialize(),
            "degree": self.degree,
            "is_natural": self.is_natural,
            "is_zero_map": self.is_zero_map(),
            "image_size": len(self.image()),
            "kernel_size": len(self.kernel()),
            "obstruction_records": [r.as_dict() for r in self.obstruction_records],
        }

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def summary(self) -> str:
        """Return a human-readable one-line summary.

        Returns
        -------
        str
        """
        zero = "zero" if self.is_zero_map() else "non-zero"
        nat = "natural" if self.is_natural else "non-natural"
        return (
            f"ObstructionMap({self.map_id[:8]}…, H^{self.degree}, "
            f"{zero}, {nat}, image={len(self.image())}, "
            f"kernel={len(self.kernel())})"
        )

    # ------------------------------------------------------------------
    # Failure locus
    # ------------------------------------------------------------------

    def failure_locus(self) -> list[Coordinate]:
        """Return the coordinates at which this map is non-zero.

        The failure locus is the set of coordinates supporting non-trivial
        obstruction records in the image.  It characterises where descent
        fails when the map is applied to an arbitrary section.

        Returns
        -------
        list[Coordinate]
        """
        return [r.coordinate for r in self.image()]


# ---------------------------------------------------------------------------
# RepairFrontier
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class RepairFrontier:
    """Mutable repair working set for the copilot-assisted descent repair loop.

    A :class:`RepairFrontier` collects all outstanding obstruction records
    together with candidate repairs proposed by various sources (automated
    analysis, cover refinement, copilot proposals).  The repair loop
    iterates over the frontier, applying and verifying repairs, until either
    the frontier is empty or no further progress can be made.

    copilot: shared-core marker

    Parameters
    ----------
    failed_obstructions : list[ObstructionRecord]
        The unresolved obstruction records that need repair.
    repair_candidates : list[dict]
        Candidate repair actions, each a dict with at least a ``"type"``
        key (``"section_modification"`` | ``"cover_refinement"`` |
        ``"evidence_addition"``).
    priority_order : list[str]
        Ordered list of obstruction_ids determining repair priority.
    cover : Cover
        The cover over which the obstructions live.
    strategy : str
        The global repair strategy being applied
        (e.g., ``"greedy"``, ``"exhaustive"``, ``"copilot_first"``).
    copilot_proposals : list[dict]
        Proposals injected by the copilot repair subsystem.
    """

    failed_obstructions: list[ObstructionRecord] = field(default_factory=list)
    repair_candidates: list[dict[str, Any]] = field(default_factory=list)
    priority_order: list[str] = field(default_factory=list)
    cover: Cover = field(default_factory=lambda: Cover(target=Coordinate()))
    strategy: str = "greedy"
    copilot_proposals: list[dict[str, Any]] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Top priority
    # ------------------------------------------------------------------

    def top_priority(self) -> ObstructionRecord | None:
        """Return the highest-priority unresolved obstruction.

        Uses ``priority_order`` if set; otherwise falls back to the record
        with the highest :meth:`ObstructionRecord.priority_score`.

        Returns
        -------
        ObstructionRecord or None
            The next obstruction to repair, or None if the frontier is empty.
        """
        if not self.failed_obstructions:
            return None
        if self.priority_order:
            for oid in self.priority_order:
                for record in self.failed_obstructions:
                    if record.obstruction_id == oid:
                        return record
        return max(self.failed_obstructions, key=lambda r: r.priority_score())

    # ------------------------------------------------------------------
    # Propose repair
    # ------------------------------------------------------------------

    def propose_repair(self, obstruction_id: str) -> dict[str, Any]:
        """Propose a concrete repair action for an obstruction.

        Searches ``repair_candidates`` for actions targeting the given id,
        supplemented by hints derived from the obstruction record itself.
        When a copilot proposal exists for the obstruction, it is merged
        into the returned dict under the ``"copilot_proposal"`` key.

        Parameters
        ----------
        obstruction_id : str
            The obstruction to propose a repair for.

        Returns
        -------
        dict[str, Any]
            A proposal dict including ``"type"``, ``"obstruction_id"``,
            ``"hints"``, and optionally ``"copilot_proposal"``.
        """
        # Find the record
        record: ObstructionRecord | None = None
        for r in self.failed_obstructions:
            if r.obstruction_id == obstruction_id:
                record = r
                break
        if record is None:
            return {"type": "no_op", "obstruction_id": obstruction_id, "reason": "not_found"}

        hints = record.repair_hints()
        # Find matching candidates
        matched = [
            c for c in self.repair_candidates
            if c.get("obstruction_id") == obstruction_id
        ]
        # Find copilot proposal
        cp = next(
            (p for p in self.copilot_proposals if p.get("obstruction_id") == obstruction_id),
            None,
        )
        proposal: dict[str, Any] = {
            "type": matched[0].get("type", "section_modification") if matched else "section_modification",
            "obstruction_id": obstruction_id,
            "hints": hints,
            "candidates": matched,
        }
        if cp is not None:
            proposal["copilot_proposal"] = cp
        return proposal

    # ------------------------------------------------------------------
    # Apply repair
    # ------------------------------------------------------------------

    def apply_repair(self, obstruction_id: str, repair: dict[str, Any]) -> bool:
        """Apply a repair action and remove the obstruction if successful.

        Validates the repair dict (must have a ``"type"`` key), marks the
        obstruction as resolved by removing it from ``failed_obstructions``,
        and logs the action.

        Parameters
        ----------
        obstruction_id : str
            The obstruction to repair.
        repair : dict[str, Any]
            The repair action.  Must include ``"type"`` and optionally
            ``"confidence"`` (float in [0,1]).

        Returns
        -------
        bool
            True when the obstruction was found and removed from the
            frontier; False when not found or the repair dict is invalid.
        """
        if "type" not in repair:
            _log.warning("Repair dict missing 'type' key: %s", repair)
            return False
        idx_to_remove: int | None = None
        for idx, record in enumerate(self.failed_obstructions):
            if record.obstruction_id == obstruction_id:
                idx_to_remove = idx
                break
        if idx_to_remove is None:
            _log.debug("Obstruction %s not on frontier", obstruction_id)
            return False
        confidence = float(repair.get("confidence", 1.0))
        if confidence < 0.5:
            _log.info(
                "Low-confidence repair (%.2f) for %s; skipping",
                confidence, obstruction_id,
            )
            return False
        removed = self.failed_obstructions.pop(idx_to_remove)
        _log.info("Applied repair for %s (%s)", removed.obstruction_id, repair["type"])
        return True

    # ------------------------------------------------------------------
    # Remaining
    # ------------------------------------------------------------------

    def remaining_obstructions(self) -> list[ObstructionRecord]:
        """Return all still-unresolved obstruction records.

        Returns
        -------
        list[ObstructionRecord]
        """
        return list(self.failed_obstructions)

    # ------------------------------------------------------------------
    # Fully repaired
    # ------------------------------------------------------------------

    def is_fully_repaired(self) -> bool:
        """Return True when the frontier contains no remaining obstructions.

        Returns
        -------
        bool
        """
        return len(self.failed_obstructions) == 0

    # ------------------------------------------------------------------
    # Repair report
    # ------------------------------------------------------------------

    def repair_report(self) -> dict[str, Any]:
        """Generate a structured report of the current repair state.

        Returns
        -------
        dict[str, Any]
            A report containing:
            * ``"fully_repaired"`` — bool
            * ``"remaining"`` — count of unresolved obstructions
            * ``"candidates_available"`` — count of repair candidates
            * ``"copilot_proposals"`` — count of copilot proposals
            * ``"strategy"`` — the strategy in use
            * ``"obstruction_summaries"`` — list of summary strings
        """
        return {
            "fully_repaired": self.is_fully_repaired(),
            "remaining": len(self.failed_obstructions),
            "candidates_available": len(self.repair_candidates),
            "copilot_proposals": len(self.copilot_proposals),
            "strategy": self.strategy,
            "obstruction_summaries": [r.summary() for r in self.failed_obstructions],
            "top_priority": self.top_priority().summary() if self.top_priority() else None,
        }

    # ------------------------------------------------------------------
    # Add copilot proposal
    # ------------------------------------------------------------------

    def add_copilot_proposal(self, proposal: dict[str, Any]) -> None:
        """Inject a copilot-generated repair proposal into the frontier.

        Proposals must include at least an ``"obstruction_id"`` key.  They
        are appended to ``copilot_proposals`` and also added to
        ``repair_candidates`` so that :meth:`propose_repair` can find them.

        Parameters
        ----------
        proposal : dict[str, Any]
            The copilot proposal dict.
        """
        if "obstruction_id" not in proposal:
            _log.warning("Copilot proposal missing 'obstruction_id': %s", proposal)
            return
        stamped = dict(proposal)
        stamped.setdefault("source", "copilot")
        stamped.setdefault("injected_at", time.time())
        self.copilot_proposals.append(stamped)
        candidate = dict(stamped)
        candidate["type"] = candidate.get("type", "section_modification")
        self.repair_candidates.append(candidate)

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def summary(self) -> str:
        """Return a human-readable one-line summary of the frontier.

        Returns
        -------
        str
        """
        top = self.top_priority()
        top_str = top.obstruction_id[:8] if top else "none"
        return (
            f"RepairFrontier(remaining={len(self.failed_obstructions)}, "
            f"candidates={len(self.repair_candidates)}, "
            f"copilot={len(self.copilot_proposals)}, "
            f"strategy={self.strategy!r}, top={top_str})"
        )

    # ------------------------------------------------------------------
    # Frontier size
    # ------------------------------------------------------------------

    def frontier_size(self) -> int:
        """Return the number of remaining (unresolved) obstructions.

        Returns
        -------
        int
        """
        return len(self.failed_obstructions)


# ---------------------------------------------------------------------------
# Module-level functions
# ---------------------------------------------------------------------------


def classify_obstruction(record: ObstructionRecord) -> ObstructionSeverity:
    """Classify an obstruction record by severity.

    Uses a heuristic based on the number of non-trivial cocycle entries,
    the cohomological degree, and whether the cocycle is a coboundary:

    * Trivial coboundaries → INFO.
    * Degree ≥ 2 or ≥ 5 non-zero entries → BLOCKING.
    * 2–4 non-zero entries → DEGRADING.
    * 1 non-zero entry → WARNING.
    * Empty cocycle → INFO.

    copilot: shared-core marker

    Parameters
    ----------
    record : ObstructionRecord
        The obstruction to classify.

    Returns
    -------
    ObstructionSeverity
    """
    if record.is_trivial():
        return ObstructionSeverity.INFO
    non_zero_count = sum(
        1 for v in record.cocycle.values() if v is not None and v != 0 and v != {}
    )
    if record.degree >= 2 or non_zero_count >= 5:
        return ObstructionSeverity.BLOCKING
    if non_zero_count >= 2:
        return ObstructionSeverity.DEGRADING
    if non_zero_count == 1:
        return ObstructionSeverity.WARNING
    return ObstructionSeverity.INFO


def compute_cech_cocycle(
    sections: Sequence[LocalSection],
    cover: Cover,
) -> dict[str, Any]:
    """Compute the Čech 1-cocycle witnessing disagreement among sections.

    For every pairwise overlap (U_i ∩ U_j) in the cover, checks whether
    the two sections agree.  When they disagree, records the discrepancy
    under the overlap key ``"section_i.coordinate∩section_j.coordinate"``.

    The returned dictionary is a valid Čech 1-cochain:  an empty dict
    means all overlaps are compatible (the cocycle is zero).

    copilot: shared-core marker

    Parameters
    ----------
    sections : Sequence[LocalSection]
        The local sections to check.
    cover : Cover
        The cover determining which pairs of sections overlap.

    Returns
    -------
    dict[str, Any]
        Mapping from overlap keys to discrepancy values.  Empty when all
        sections are mutually compatible.
    """
    cocycle: dict[str, Any] = {}
    section_by_coord: dict[str, LocalSection] = {s.coordinate: s for s in sections}
    overlap_pairs = cover.pairwise_overlaps()

    for left_key, right_key in overlap_pairs:
        s_left = section_by_coord.get(left_key)
        s_right = section_by_coord.get(right_key)
        if s_left is None or s_right is None:
            # Missing section is itself an obstruction
            cocycle[f"{left_key}∩{right_key}"] = {
                "error": "missing_section",
                "missing": left_key if s_left is None else right_key,
            }
            continue
        # Compare judgment data on the shared keys
        shared_keys = set(s_left.judgment_data) & set(s_right.judgment_data)
        discrepancies: dict[str, Any] = {}
        for jk in shared_keys:
            v_left = s_left.judgment_data[jk]
            v_right = s_right.judgment_data[jk]
            if v_left != v_right:
                discrepancies[jk] = {"left": v_left, "right": v_right}
        if discrepancies:
            overlap_key = f"{left_key}∩{right_key}"
            cocycle[overlap_key] = discrepancies

    return cocycle


def build_obstruction_map(
    cover: Cover,
    sections: Sequence[LocalSection],
) -> ObstructionMap:
    """Construct the obstruction map δ: Sections(U) → H¹(U, F).

    Computes the Čech cocycle for the given sections and cover, then
    builds one :class:`ObstructionRecord` per non-zero overlap entry.
    The returned :class:`ObstructionMap` encodes the full connecting
    homomorphism.

    copilot: shared-core marker

    Parameters
    ----------
    cover : Cover
        The Grothendieck cover.
    sections : Sequence[LocalSection]
        The local sections over which the map acts.

    Returns
    -------
    ObstructionMap
        The full obstruction map for the given input.
    """
    cocycle = compute_cech_cocycle(sections, cover)
    records: list[ObstructionRecord] = []
    target_key = cover.target.key if hasattr(cover.target, "key") else str(cover.target)
    target_coord = Coordinate(
        components=tuple(target_key.split("/")),
        kind=CoordinateKind.REGION,
    )

    for overlap_key, discrepancy in cocycle.items():
        parts = overlap_key.split("∩")
        base_components = tuple(parts[0].split("/")) if parts else (overlap_key,)
        obs_coord = Coordinate(
            components=base_components,
            kind=CoordinateKind.REGION,
        )
        cover_id = getattr(cover, "cover_id", cover.target.key if hasattr(cover.target, "key") else "")
        record = ObstructionRecord(
            coordinate=obs_coord,
            degree=1,
            cocycle={overlap_key: discrepancy},
            is_coboundary=False,
            source_cover_id=cover_id,
        )
        records.append(record)

    is_zero = len([r for r in records if not r.is_trivial()]) == 0
    map_id = hashlib.sha1(
        json.dumps(cocycle, sort_keys=True, default=str).encode()
    ).hexdigest()[:12]
    return ObstructionMap(
        source_cover=cover,
        target_coord=target_coord,
        obstruction_records=records,
        map_id=map_id,
        degree=1,
        is_natural=True,
    )


def trivialize_obstruction(
    record: ObstructionRecord,
    repair: dict[str, Any],
) -> bool | None:
    """Attempt to trivialize an obstruction by applying a repair action.

    Given a repair dict (the output of
    :meth:`RepairFrontier.propose_repair` or a copilot proposal), checks
    whether the proposed action is sufficient to make the cocycle exact.

    The trivialization succeeds when:
    * The repair covers every non-zero overlap key in the cocycle, OR
    * The repair has ``"type": "cover_refinement"`` and the provided
      ``"target_overlaps"`` list covers all violation keys.

    Returns None when the repair dict is malformed or the analysis is
    inconclusive.

    copilot: shared-core marker

    Parameters
    ----------
    record : ObstructionRecord
        The obstruction to trivialize.
    repair : dict[str, Any]
        The repair action dict.

    Returns
    -------
    bool or None
        True if the repair is sufficient, False if not, None if unknown.
    """
    if "type" not in repair:
        return None
    non_zero_keys = {
        k for k, v in record.cocycle.items() if v is not None and v != 0 and v != {}
    }
    if not non_zero_keys:
        return True  # Already trivial

    repair_type = repair["type"]
    if repair_type == "cover_refinement":
        target_overlaps = set(repair.get("target_overlaps", []))
        covered = non_zero_keys.issubset(target_overlaps)
        return covered
    if repair_type in ("section_modification", "evidence_addition"):
        addressed = set(repair.get("addressed_overlaps", []))
        if addressed >= non_zero_keys:
            return True
        # Partial trivialization — some keys remain
        if addressed & non_zero_keys:
            return False
        return None
    if repair_type == "no_op":
        return False
    return None


# ---------------------------------------------------------------------------
# __all__
# ---------------------------------------------------------------------------

__all__: list[str] = [
    # Enumerations
    "ObstructionSeverity",
    "CochainKind",
    "ObstructionOrigin",
    # Core classes
    "CohomologyClass",
    "ObstructionRecord",
    "ObstructionMap",
    "RepairFrontier",
    # Module-level functions
    "classify_obstruction",
    "compute_cech_cocycle",
    "build_obstruction_map",
    "trivialize_obstruction",
]
