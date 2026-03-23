"""Section 03 — Sections are the real products of verification.

Theory2.tex Chapter 5, Section 5.3.

The central claim of this section is that the canonical output of a
successful verification pass is not a boolean flag, a confidence score,
or even a ``Judgment`` record in isolation — it is a **section of the
judgment sheaf**.

A section captures the *global coherence* of a family of local
judgments.  It is the unique element (when it exists) that restricts
consistently to every local judgment in the family.  Its existence
witnesses that the local evidence *fits together* over the entire
semantic site.

This module implements:

* :class:`SectionProduct` — the output of a successful gluing,
  coupling the geometric section to the algebraic product.
* :class:`GlobalSection` — a section that extends over the full base,
  the "gold standard" verification output.
* :class:`SectionFunctor` — functorial transport of section products
  along coordinate morphisms.
* :class:`SectionComparison` — comparison morphisms between section
  products, tracking compatibility and obstructions.
* :class:`SectionProducts` — top-level coordinator.

References
----------
theory2.tex §5.3 Def 1–2, §2.4 (Functoriality), §3 (Sheaf Axioms).

# copilot: s03 — sections are the real products of verification.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Callable, Mapping, Sequence

from jugeo.judgments.comparisons import (
    ComparisonMode,
    ComparisonResult,
    compare_sections,
)
from jugeo.judgments.judgment_terms import (
    JudgmentStatus,
    Obstruction,
    Provenance,
    ProvenanceSource,
    ResidualObligation,
    TrustLevel,
)
from jugeo.judgments.sections import (
    GluingStatus,
    Section,
    SectionComparator,
    SectionFamily,
    SectionGluing,
    SectionBuilder,
)

from jugeo.foundations.judgment_products.models import (
    JudgmentProduct,
    LocalJudgmentSection,
    ProductKind,
    ProductStatus,
)


# ---------------------------------------------------------------------------
# Supporting enumerations
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class SectionProductStatus(str, Enum):
    """Status of a :class:`SectionProduct`.

    Members
    -------
    PENDING
        The section product has been created but gluing has not been attempted.
    GLUING_FAILED
        The gluing operation failed (incompatible local data).
    PARTIAL
        Gluing succeeded over a strict sub-cover; some patches are missing.
    GLOBAL
        The section extends consistently to the full base site.
    OBSTRUCTED
        Gluing failed due to a non-trivial cohomological obstruction.
    TRANSPORTED
        The section was obtained by transport along a morphism.
    """

    PENDING = "pending"
    GLUING_FAILED = "gluing_failed"
    PARTIAL = "partial"
    GLOBAL = "global"
    OBSTRUCTED = "obstructed"
    TRANSPORTED = "transported"

    def is_usable(self) -> bool:
        """Return ``True`` iff the section product can be used downstream.

        Returns
        -------
        bool
            ``True`` for PARTIAL, GLOBAL, and TRANSPORTED.
        """
        return self in (
            SectionProductStatus.PARTIAL,
            SectionProductStatus.GLOBAL,
            SectionProductStatus.TRANSPORTED,
        )


class FunctorDirection(str, Enum):
    """Direction of functorial application in :class:`SectionFunctor`.

    Members
    -------
    PULLBACK
        Pulling sections back along a coordinate morphism (contravariant).
    PUSHFORWARD
        Pushing sections forward (covariant, requires adjoint structure).
    RESTRICTION
        Restricting to a sub-site (special case of pullback along inclusion).
    """

    PULLBACK = "pullback"
    PUSHFORWARD = "pushforward"
    RESTRICTION = "restriction"


# ---------------------------------------------------------------------------
# SectionProduct
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SectionProduct:
    """The output of a successful (or attempted) section gluing.

    A ``SectionProduct`` records the full context of a gluing attempt:
    the input family of local sections, the resulting section (if any),
    and the algebraic product that summarises the outcome.

    Theory reference: theory2.tex §5.3 Def 1.

    Parameters
    ----------
    section_product_id:
        Stable unique identifier.
    status:
        Current status of the section product.
    input_patches:
        Tuple of patch labels that were input to the gluing.
    result_section:
        The section produced by gluing, or ``None`` if gluing failed.
    gluing_status:
        Underlying ``GluingStatus`` from the sections layer.
    product:
        The algebraic ``JudgmentProduct`` associated with this section.
    obstruction_descriptions:
        Descriptions of obstructions that prevented full gluing.
    missing_patches:
        Patch labels where data was missing.
    coordinate_label:
        String label of the base coordinate.
    provenance_notes:
        Free-text provenance notes about how this product was assembled.
    created_at:
        ISO-8601 creation timestamp.
    """

    section_product_id: str = field(
        default_factory=lambda: str(uuid.uuid4())[:12]
    )
    status: SectionProductStatus = SectionProductStatus.PENDING
    input_patches: tuple[str, ...] = ()
    result_section: Section | None = None
    gluing_status: GluingStatus = GluingStatus.MISSING_DATA
    product: JudgmentProduct | None = None
    obstruction_descriptions: tuple[str, ...] = ()
    missing_patches: tuple[str, ...] = ()
    coordinate_label: str = ""
    provenance_notes: str = ""
    created_at: str = field(default_factory=_now_iso)

    # ------------------------------------------------------------------
    # Derived properties
    # ------------------------------------------------------------------

    def is_global(self) -> bool:
        """Return ``True`` iff the section product is a global section.

        Returns
        -------
        bool
        """
        return self.status == SectionProductStatus.GLOBAL

    def is_usable(self) -> bool:
        """Return ``True`` iff this product can be used in downstream composition.

        Returns
        -------
        bool
        """
        return self.status.is_usable()

    def has_obstructions(self) -> bool:
        """Return ``True`` iff any obstructions were recorded.

        Returns
        -------
        bool
        """
        return len(self.obstruction_descriptions) > 0

    def patch_count(self) -> int:
        """Return the number of input patches.

        Returns
        -------
        int
        """
        return len(self.input_patches)

    def coverage_fraction(self) -> float:
        """Return the fraction of patches that were successfully covered.

        Returns
        -------
        float
            A value in [0.0, 1.0].  Returns 0.0 if no patches were input.
        """
        if not self.input_patches:
            return 0.0
        covered = len(self.input_patches) - len(self.missing_patches)
        return covered / len(self.input_patches)

    def content_hash(self) -> str:
        """Compute a stable content hash.

        Returns
        -------
        str
            16-character hex digest.
        """
        payload = json.dumps(
            {
                "id": self.section_product_id,
                "patches": list(self.input_patches),
                "status": self.status.value,
            },
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    def with_status(self, status: SectionProductStatus) -> "SectionProduct":
        """Return a copy with an updated status.

        Parameters
        ----------
        status:
            New ``SectionProductStatus``.

        Returns
        -------
        SectionProduct
        """
        return replace(self, status=status)

    def with_result_section(self, section: Section) -> "SectionProduct":
        """Return a copy with the result section attached.

        If the section is global, the status is updated to GLOBAL;
        otherwise to PARTIAL.

        Parameters
        ----------
        section:
            The result section from gluing.

        Returns
        -------
        SectionProduct
        """
        new_status = (
            SectionProductStatus.GLOBAL
            if section.is_global
            else SectionProductStatus.PARTIAL
        )
        return replace(
            self,
            result_section=section,
            gluing_status=GluingStatus.SUCCESS,
            status=new_status,
        )

    def add_obstruction(self, description: str) -> "SectionProduct":
        """Return a copy with *description* added to obstructions.

        Parameters
        ----------
        description:
            Description of the obstruction.

        Returns
        -------
        SectionProduct
        """
        return replace(
            self,
            obstruction_descriptions=self.obstruction_descriptions + (description,),
            status=SectionProductStatus.OBSTRUCTED,
        )

    def to_mapping(self) -> dict[str, Any]:
        """Serialise to a plain dictionary.

        Returns
        -------
        dict[str, Any]
        """
        d: dict[str, Any] = {
            "section_product_id": self.section_product_id,
            "status": self.status.value,
            "input_patches": list(self.input_patches),
            "gluing_status": self.gluing_status.value,
            "obstruction_descriptions": list(self.obstruction_descriptions),
            "missing_patches": list(self.missing_patches),
            "coordinate_label": self.coordinate_label,
            "coverage_fraction": self.coverage_fraction(),
            "is_global": self.is_global(),
            "content_hash": self.content_hash(),
            "created_at": self.created_at,
        }
        if self.product is not None:
            d["product"] = self.product.to_mapping()
        if self.result_section is not None:
            d["result_section_patch"] = self.result_section.patch
        return d

    def __repr__(self) -> str:
        return (
            f"SectionProduct(id={self.section_product_id!r}, "
            f"status={self.status.value}, "
            f"patches={self.patch_count()}, "
            f"coverage={self.coverage_fraction():.0%})"
        )


# ---------------------------------------------------------------------------
# GlobalSection
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GlobalSection:
    """A :class:`SectionProduct` that extends over the full base site.

    A ``GlobalSection`` is the "gold standard" output of verification: it
    witnesses that local judgments over all patches agree on their
    overlaps, forming a coherent global picture.

    Construction
    ------------
    Build via :meth:`from_section_product` after gluing succeeds, or use
    :meth:`SectionProducts.attempt_global_gluing`.

    Theory reference: theory2.tex §5.3 Def 2.

    Parameters
    ----------
    base:
        The ``SectionProduct`` underlying this global section.
    base_coordinate_label:
        Label of the base coordinate over which this section is global.
    patches_covered:
        Exhaustive tuple of all patches covered.
    cocycle_checked:
        Whether the cocycle condition has been explicitly verified.
    uniqueness_checked:
        Whether uniqueness of the global section has been verified.
    gluing_witnesses:
        Patch-overlap labels that served as witnesses to compatibility.
    """

    base: SectionProduct
    base_coordinate_label: str = ""
    patches_covered: tuple[str, ...] = ()
    cocycle_checked: bool = False
    uniqueness_checked: bool = False
    gluing_witnesses: tuple[str, ...] = ()

    # ------------------------------------------------------------------
    # Derived properties
    # ------------------------------------------------------------------

    def is_verified(self) -> bool:
        """Return ``True`` iff all sheaf axioms have been verified.

        The cocycle condition and uniqueness are both checked.

        Returns
        -------
        bool
        """
        return self.cocycle_checked and self.uniqueness_checked

    def covers_patch(self, patch: str) -> bool:
        """Return ``True`` iff *patch* is among the covered patches.

        Parameters
        ----------
        patch:
            Patch label to check.

        Returns
        -------
        bool
        """
        return patch in self.patches_covered

    def patch_count(self) -> int:
        """Return the number of covered patches.

        Returns
        -------
        int
        """
        return len(self.patches_covered)

    def is_usable(self) -> bool:
        """Return ``True`` iff the base section product is usable.

        Returns
        -------
        bool
        """
        return self.base.is_usable()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @classmethod
    def from_section_product(
        cls,
        sp: SectionProduct,
        base_coordinate_label: str = "",
        verify_cocycle: bool = False,
    ) -> "GlobalSection":
        """Construct a ``GlobalSection`` from a GLOBAL ``SectionProduct``.

        Parameters
        ----------
        sp:
            A ``SectionProduct`` with ``status == GLOBAL``.
        base_coordinate_label:
            Label for the base coordinate.
        verify_cocycle:
            If ``True``, mark ``cocycle_checked=True`` (caller is
            asserting the cocycle condition has been verified externally).

        Returns
        -------
        GlobalSection

        Raises
        ------
        ValueError
            If *sp* is not in GLOBAL status.
        """
        if sp.status != SectionProductStatus.GLOBAL:
            raise ValueError(
                f"Cannot build GlobalSection from a non-global "
                f"SectionProduct (status={sp.status.value!r})."
            )
        return cls(
            base=sp,
            base_coordinate_label=base_coordinate_label or sp.coordinate_label,
            patches_covered=sp.input_patches,
            cocycle_checked=verify_cocycle,
            uniqueness_checked=False,
        )

    def assert_uniqueness(self) -> "GlobalSection":
        """Return a copy with ``uniqueness_checked`` set to ``True``.

        This is a declaration by the caller that uniqueness has been
        externally verified (e.g. by a solver or formal proof).

        Returns
        -------
        GlobalSection
        """
        return replace(self, uniqueness_checked=True)

    def assert_cocycle(self) -> "GlobalSection":
        """Return a copy with ``cocycle_checked`` set to ``True``.

        Returns
        -------
        GlobalSection
        """
        return replace(self, cocycle_checked=True)

    def to_mapping(self) -> dict[str, Any]:
        """Serialise to a plain dictionary.

        Returns
        -------
        dict[str, Any]
        """
        return {
            "base": self.base.to_mapping(),
            "base_coordinate_label": self.base_coordinate_label,
            "patches_covered": list(self.patches_covered),
            "cocycle_checked": self.cocycle_checked,
            "uniqueness_checked": self.uniqueness_checked,
            "is_verified": self.is_verified(),
            "gluing_witnesses": list(self.gluing_witnesses),
        }

    def __repr__(self) -> str:
        verified = "✓" if self.is_verified() else "?"
        return (
            f"GlobalSection({verified}, "
            f"patches={self.patch_count()}, "
            f"coord={self.base_coordinate_label!r})"
        )


# ---------------------------------------------------------------------------
# SectionFunctor
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SectionFunctor:
    """Functorial transport of section products along coordinate morphisms.

    Implements the functor ``Γ: Coord^{op} → SectionProduct`` that
    assigns to each coordinate its section product, and to each morphism
    the corresponding restriction/transport map.

    Theory reference: theory2.tex §5.3 §2.4.

    Parameters
    ----------
    functor_id:
        Stable identifier for this functor instance.
    direction:
        The direction of functoriality (pullback / pushforward / restriction).
    morphism_label:
        Label of the coordinate morphism being applied.
    source_coordinate:
        Label of the source coordinate.
    target_coordinate:
        Label of the target coordinate.
    notes:
        Free-text notes about this functor instance.
    """

    functor_id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    direction: FunctorDirection = FunctorDirection.PULLBACK
    morphism_label: str = ""
    source_coordinate: str = ""
    target_coordinate: str = ""
    notes: str = ""

    # ------------------------------------------------------------------
    # Application
    # ------------------------------------------------------------------

    def apply(self, sp: SectionProduct) -> SectionProduct:
        """Apply this functor to *sp*, producing a transported section product.

        For PULLBACK / RESTRICTION: the result is a restricted section.
        For PUSHFORWARD: the result records the pushforward attempt.

        Parameters
        ----------
        sp:
            The ``SectionProduct`` to transform.

        Returns
        -------
        SectionProduct
            A new ``SectionProduct`` with updated coordinate label and
            provenance.
        """
        new_status = (
            SectionProductStatus.TRANSPORTED
            if sp.is_usable()
            else sp.status
        )
        new_coord = (
            self.target_coordinate
            if self.direction in (FunctorDirection.PULLBACK, FunctorDirection.RESTRICTION)
            else self.source_coordinate
        )
        provenance = (
            f"{sp.provenance_notes}\n"
            f"[{self.direction.value} along {self.morphism_label!r}: "
            f"{self.source_coordinate!r} → {self.target_coordinate!r}]"
        ).strip()
        return replace(
            sp,
            section_product_id=f"{sp.section_product_id}_{self.direction.value[:3]}",
            status=new_status,
            coordinate_label=new_coord,
            provenance_notes=provenance,
        )

    def compose(self, other: "SectionFunctor") -> "SectionFunctor":
        """Compose this functor with *other* (self then other).

        Both must have the same direction.  The resulting morphism
        label is the concatenation.

        Parameters
        ----------
        other:
            The functor to compose on the right.

        Returns
        -------
        SectionFunctor

        Raises
        ------
        ValueError
            If source/target coordinates are incompatible.
        """
        if self.target_coordinate != other.source_coordinate:
            raise ValueError(
                f"Cannot compose functors: target {self.target_coordinate!r} "
                f"!= source {other.source_coordinate!r}."
            )
        return replace(
            self,
            functor_id=f"{self.functor_id}∘{other.functor_id}",
            morphism_label=f"{self.morphism_label}∘{other.morphism_label}",
            target_coordinate=other.target_coordinate,
            notes=f"Composed: {self.functor_id} ∘ {other.functor_id}",
        )

    def identity(self, coordinate: str) -> "SectionFunctor":
        """Return the identity functor at *coordinate*.

        Parameters
        ----------
        coordinate:
            The coordinate label.

        Returns
        -------
        SectionFunctor
        """
        return replace(
            self,
            functor_id=f"id_{coordinate}",
            direction=FunctorDirection.RESTRICTION,
            morphism_label=f"id_{coordinate}",
            source_coordinate=coordinate,
            target_coordinate=coordinate,
            notes=f"Identity functor at {coordinate!r}.",
        )

    def to_mapping(self) -> dict[str, Any]:
        """Serialise to a plain dictionary.

        Returns
        -------
        dict[str, Any]
        """
        return {
            "functor_id": self.functor_id,
            "direction": self.direction.value,
            "morphism_label": self.morphism_label,
            "source_coordinate": self.source_coordinate,
            "target_coordinate": self.target_coordinate,
            "notes": self.notes,
        }

    def __repr__(self) -> str:
        return (
            f"SectionFunctor({self.direction.value}, "
            f"{self.source_coordinate!r}→{self.target_coordinate!r})"
        )


# ---------------------------------------------------------------------------
# SectionComparison
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SectionComparison:
    """A comparison morphism between two :class:`SectionProduct` instances.

    Records the compatibility, obstruction structure, and mode of
    comparison between two section products.

    Theory reference: theory2.tex §5.3 §5.4.

    Parameters
    ----------
    comparison_id:
        Stable unique identifier.
    left_id:
        ``section_product_id`` of the left section product.
    right_id:
        ``section_product_id`` of the right section product.
    mode:
        The ``ComparisonMode`` used.
    compatible:
        ``True`` iff the two section products are compatible (can be glued).
    shared_patches:
        Patches present in both section products.
    left_only_patches:
        Patches only in the left section product.
    right_only_patches:
        Patches only in the right section product.
    obstruction_descriptions:
        Descriptions of compatibility obstructions.
    comparison_result:
        Underlying ``ComparisonResult`` from the sections layer, if available.
    notes:
        Free-text notes.
    computed_at:
        ISO-8601 timestamp.
    """

    comparison_id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    left_id: str = ""
    right_id: str = ""
    mode: ComparisonMode = ComparisonMode.EQUIVALENCE
    compatible: bool = False
    shared_patches: tuple[str, ...] = ()
    left_only_patches: tuple[str, ...] = ()
    right_only_patches: tuple[str, ...] = ()
    obstruction_descriptions: tuple[str, ...] = ()
    comparison_result: ComparisonResult | None = None
    notes: str = ""
    computed_at: str = field(default_factory=_now_iso)

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def is_compatible(self) -> bool:
        """Return ``True`` iff the two section products are compatible.

        Returns
        -------
        bool
        """
        return self.compatible

    def patch_overlap_count(self) -> int:
        """Return the number of shared patches.

        Returns
        -------
        int
        """
        return len(self.shared_patches)

    def has_obstructions(self) -> bool:
        """Return ``True`` iff any obstruction descriptions are present.

        Returns
        -------
        bool
        """
        return len(self.obstruction_descriptions) > 0

    def can_glue(self) -> bool:
        """Return ``True`` iff gluing these two section products is possible.

        Gluing is possible when they are compatible and have no obstructions.

        Returns
        -------
        bool
        """
        return self.compatible and not self.has_obstructions()

    def to_mapping(self) -> dict[str, Any]:
        """Serialise to a plain dictionary.

        Returns
        -------
        dict[str, Any]
        """
        return {
            "comparison_id": self.comparison_id,
            "left_id": self.left_id,
            "right_id": self.right_id,
            "mode": self.mode.value,
            "compatible": self.compatible,
            "shared_patches": list(self.shared_patches),
            "left_only_patches": list(self.left_only_patches),
            "right_only_patches": list(self.right_only_patches),
            "obstruction_descriptions": list(self.obstruction_descriptions),
            "notes": self.notes,
            "computed_at": self.computed_at,
        }

    def __repr__(self) -> str:
        return (
            f"SectionComparison(id={self.comparison_id!r}, "
            f"compatible={self.compatible}, "
            f"shared_patches={self.patch_overlap_count()})"
        )


# ---------------------------------------------------------------------------
# SectionProducts (top-level coordinator)
# ---------------------------------------------------------------------------


class SectionProducts:
    """Top-level coordinator for section product operations.

    Provides high-level methods for assembling, comparing, and transporting
    section products.

    Theory reference: theory2.tex §5.3.
    """

    # ------------------------------------------------------------------
    # Assembly
    # ------------------------------------------------------------------

    @staticmethod
    def from_local_sections(
        locals_: Sequence[LocalJudgmentSection],
        coordinate_label: str = "",
    ) -> SectionProduct:
        """Assemble a ``SectionProduct`` from local judgment sections.

        Checks mutual compatibility of all pairs of local sections and
        attempts a gluing.  Returns a PARTIAL or GLOBAL section product
        based on the result.

        Parameters
        ----------
        locals_:
            Sequence of ``LocalJudgmentSection`` objects to glue.
        coordinate_label:
            Label of the base coordinate.

        Returns
        -------
        SectionProduct
        """
        patches = tuple(ls.patch for ls in locals_)
        sp = SectionProduct(
            input_patches=patches,
            coordinate_label=coordinate_label,
            status=SectionProductStatus.PENDING,
        )
        if not locals_:
            return replace(sp, status=SectionProductStatus.GLUING_FAILED)

        # Check pairwise compatibility
        obstructions: list[str] = []
        for i, ls_i in enumerate(locals_):
            for j, ls_j in enumerate(locals_):
                if j <= i:
                    continue
                if not ls_i.compatible_with(ls_j):
                    obstructions.append(
                        f"Incompatible patches: {ls_i.patch!r} ↔ {ls_j.patch!r}"
                    )

        if obstructions:
            sp_fail = sp
            for obs in obstructions:
                sp_fail = sp_fail.add_obstruction(obs)
            return sp_fail

        # All patches compatible: mark as global if all sections are settled
        all_settled = all(ls.is_settled() for ls in locals_)
        new_status = (
            SectionProductStatus.GLOBAL
            if all_settled
            else SectionProductStatus.PARTIAL
        )
        return replace(sp, status=new_status, gluing_status=GluingStatus.SUCCESS)

    @staticmethod
    def attempt_global_gluing(
        family: SectionFamily,
        coordinate_label: str = "",
    ) -> tuple[SectionProduct, GlobalSection | None]:
        """Attempt to glue a ``SectionFamily`` into a global section.

        Parameters
        ----------
        family:
            The ``SectionFamily`` to glue.
        coordinate_label:
            Label of the base coordinate.

        Returns
        -------
        tuple[SectionProduct, GlobalSection | None]
            The ``SectionProduct`` and, if gluing succeeded, a
            ``GlobalSection`` wrapping it.
        """
        glueing = SectionGluing(input_family=family)
        glueing.verify_cocycle_condition()
        glueing.glue()

        patches = tuple(family.patch_keys())
        sp = SectionProduct(
            input_patches=patches,
            coordinate_label=coordinate_label,
        )

        if glueing.status == GluingStatus.SUCCESS and glueing.result is not None:
            section = glueing.result
            if isinstance(section, Section):
                sp = sp.with_result_section(section)
                gs = GlobalSection.from_section_product(
                    sp, base_coordinate_label=coordinate_label, verify_cocycle=True
                )
                return sp, gs

        # Gluing failed
        obs_desc = (
            glueing.result.description
            if glueing.result is not None and hasattr(glueing.result, "description")
            else "Gluing failed"
        )
        sp_fail = sp.add_obstruction(str(obs_desc))
        return sp_fail, None

    # ------------------------------------------------------------------
    # Comparison
    # ------------------------------------------------------------------

    @staticmethod
    def compare(
        left: SectionProduct,
        right: SectionProduct,
        mode: ComparisonMode = ComparisonMode.EQUIVALENCE,
    ) -> SectionComparison:
        """Compare two ``SectionProduct`` instances.

        Parameters
        ----------
        left:
            The left section product.
        right:
            The right section product.
        mode:
            The comparison mode.

        Returns
        -------
        SectionComparison
        """
        left_patches = set(left.input_patches)
        right_patches = set(right.input_patches)
        shared = tuple(left_patches & right_patches)
        left_only = tuple(left_patches - right_patches)
        right_only = tuple(right_patches - left_patches)

        # If both have result sections, delegate to the sections layer
        cr: ComparisonResult | None = None
        compatible = False
        obs: list[str] = []

        if left.result_section is not None and right.result_section is not None:
            cr = compare_sections(left.result_section, right.result_section, mode=mode)
            compatible = cr.compatible
            obs = list(cr.obstructions)
        else:
            # Heuristic: compatible iff no obstructions and same status family
            compatible = (
                not left.has_obstructions()
                and not right.has_obstructions()
                and left.status.is_usable()
                and right.status.is_usable()
            )
            if not compatible:
                obs = ["Section data unavailable for full comparison."]

        return SectionComparison(
            left_id=left.section_product_id,
            right_id=right.section_product_id,
            mode=mode,
            compatible=compatible,
            shared_patches=shared,
            left_only_patches=left_only,
            right_only_patches=right_only,
            obstruction_descriptions=tuple(obs),
            comparison_result=cr,
        )

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------

    @staticmethod
    def transport(
        sp: SectionProduct,
        functor: SectionFunctor,
    ) -> SectionProduct:
        """Transport *sp* along *functor*.

        Parameters
        ----------
        sp:
            The section product to transport.
        functor:
            The ``SectionFunctor`` defining the transport.

        Returns
        -------
        SectionProduct
        """
        return functor.apply(sp)


# ---------------------------------------------------------------------------
# __all__
# ---------------------------------------------------------------------------

__all__: list[str] = [
    # Enumerations
    "SectionProductStatus",
    "FunctorDirection",
    # Models
    "SectionProduct",
    "GlobalSection",
    "SectionFunctor",
    "SectionComparison",
    # Coordinator
    "SectionProducts",
]

# copilot: s03 — sections are the real products of verification.
