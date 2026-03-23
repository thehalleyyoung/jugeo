"""Core domain models for jugeo.foundations.judgment_products.

Theory2.tex Chapter 5: Judgments, Sections, and Semantic Products.

This module defines the primary data models used throughout the
``judgment_products`` package.  The five central classes are:

* :class:`JudgmentProduct` — the semantic value produced by composing
  one or more judgments into a single algebraic object.
* :class:`SemanticProduct` — a typed wrapper coupling a
  ``JudgmentProduct`` to the ``Section`` that witnesses it.
* :class:`LocalJudgmentSection` — a thin, package-local view of a
  ``Section`` used for product assembly without pulling the full sheaf
  machinery into every callsite.
* :class:`ComparisonMap` — an explicit, structure-preserving morphism
  between two ``JudgmentProduct`` instances.
* :class:`ExplanationProjection` — a projection from a
  ``JudgmentProduct`` to a structured human-readable explanation.

All models are frozen dataclasses (immutable) except where a builder
pattern is explicitly required.  All mutation returns a new instance
via :func:`dataclasses.replace`.

References
----------
theory2.tex §5.1 (Semantic Products), §5.3 (Section Products),
§5.4 (Comparison Maps and Explanation Projections).

# copilot: core domain models — judgment_products package.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Mapping, Sequence

from jugeo.judgments.comparisons import ComparisonMode, ComparisonResult
from jugeo.judgments.judgment_terms import (
    EvidenceBundle,
    JudgmentStatus,
    Obstruction,
    Proposition,
    Provenance,
    ResidualObligation,
    TrustAnnotation,
    TrustLevel,
)
from jugeo.judgments.sections import GluingStatus, Section


# ---------------------------------------------------------------------------
# Supporting enumerations
# ---------------------------------------------------------------------------


class ProductStatus(str, Enum):
    """Lifecycle status of a :class:`JudgmentProduct`.

    Members
    -------
    INCOMPLETE
        Not all constituent judgments have been assembled.
    ASSEMBLED
        All judgments combined; residuals may still be open.
    DISCHARGED
        All residual obligations have been resolved.
    OBSTRUCTED
        At least one obstruction blocks full discharge.
    PROJECTED
        An explanation projection has been generated from this product.
    """

    INCOMPLETE = "incomplete"
    ASSEMBLED = "assembled"
    DISCHARGED = "discharged"
    OBSTRUCTED = "obstructed"
    PROJECTED = "projected"


class ProductKind(str, Enum):
    """Categorical kind of a :class:`JudgmentProduct`.

    Members
    -------
    ATOMIC
        A product wrapping exactly one base judgment.
    COMPOSED
        A product formed by composing two or more judgments.
    RESTRICTED
        A product obtained by restricting a larger product to a sub-site.
    TRANSPORTED
        A product obtained by transporting along a coordinate morphism.
    """

    ATOMIC = "atomic"
    COMPOSED = "composed"
    RESTRICTED = "restricted"
    TRANSPORTED = "transported"


class ProjectionMode(str, Enum):
    """How an :class:`ExplanationProjection` formats its output.

    Members
    -------
    BRIEF
        A single sentence summary.
    DETAILED
        Multi-paragraph prose with evidence citations.
    STRUCTURED
        A structured mapping suitable for JSON / UI rendering.
    """

    BRIEF = "brief"
    DETAILED = "detailed"
    STRUCTURED = "structured"


# ---------------------------------------------------------------------------
# JudgmentProduct
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


@dataclass(frozen=True, slots=True)
class JudgmentProduct:
    """The semantic value produced by composing one or more judgments.

    A ``JudgmentProduct`` is not a boolean.  It is a structured algebraic
    object carrying:

    * The composite proposition being asserted.
    * The combined evidence bundle.
    * Surviving residual obligations (things still to prove).
    * Surviving obstructions (cohomology witnesses blocking discharge).
    * A trust annotation derived from the weakest constituent.
    * Full provenance of the composition history.

    Theory reference: theory2.tex §5.1 Def 1.

    Parameters
    ----------
    product_id:
        Unique identifier (stable UUID-like string) for this product.
    kind:
        Categorical kind of the product (atomic, composed, …).
    status:
        Current lifecycle status.
    proposition_label:
        Human-readable label for the composite proposition.
    constituent_hashes:
        Content-hashes of the constituent judgments, in composition order.
    evidence:
        Combined evidence bundle from all constituents.
    residuals:
        Tuple of unresolved ``ResidualObligation`` objects.
    obstructions:
        Tuple of ``Obstruction`` objects blocking full discharge.
    trust:
        Trust annotation derived from the weakest constituent.
    provenance:
        Provenance record of how this product was assembled.
    coordinate_label:
        String label of the base coordinate, for display.
    metadata:
        Arbitrary extra data (tool names, timestamps, etc.).
    created_at:
        ISO-8601 timestamp of product creation.
    """

    product_id: str
    kind: ProductKind = ProductKind.ATOMIC
    status: ProductStatus = ProductStatus.INCOMPLETE
    proposition_label: str = ""
    constituent_hashes: tuple[str, ...] = ()
    evidence: EvidenceBundle = field(default_factory=EvidenceBundle)
    residuals: tuple[ResidualObligation, ...] = ()
    obstructions: tuple[Obstruction, ...] = ()
    trust: TrustAnnotation = field(default_factory=TrustAnnotation)
    provenance: Provenance = field(default_factory=Provenance)
    coordinate_label: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_now_iso)

    # ------------------------------------------------------------------
    # Derived properties
    # ------------------------------------------------------------------

    def is_discharged(self) -> bool:
        """Return ``True`` iff every residual obligation is resolved.

        Returns
        -------
        bool
            ``True`` when ``self.residuals`` is empty *and*
            ``self.status`` is ``DISCHARGED``.
        """
        return len(self.residuals) == 0 and self.status == ProductStatus.DISCHARGED

    def has_obstructions(self) -> bool:
        """Return ``True`` iff any obstruction is present.

        Returns
        -------
        bool
        """
        return len(self.obstructions) > 0

    def residual_count(self) -> int:
        """Return the number of open residual obligations.

        Returns
        -------
        int
        """
        return len(self.residuals)

    def constituent_count(self) -> int:
        """Return how many judgments were composed to form this product.

        Returns
        -------
        int
        """
        return len(self.constituent_hashes)

    def content_hash(self) -> str:
        """Compute a stable content hash of this product.

        The hash is computed over the ``product_id``, the
        ``constituent_hashes``, and the ``proposition_label``.

        Returns
        -------
        str
            A 16-character hex digest.
        """
        payload = json.dumps(
            {
                "pid": self.product_id,
                "ch": list(self.constituent_hashes),
                "prop": self.proposition_label,
            },
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    def trust_floor(self) -> TrustLevel:
        """Return the minimum trust level across the evidence bundle.

        Returns
        -------
        TrustLevel
            The weakest trust level present in ``self.evidence``, or
            ``TrustLevel.UNVERIFIED`` if the bundle is empty.
        """
        if self.evidence.is_empty():
            return TrustLevel.UNVERIFIED
        weakest = self.evidence.weakest()
        if weakest is None:
            return TrustLevel.UNVERIFIED
        return weakest.trust_level

    def with_status(self, new_status: ProductStatus) -> "JudgmentProduct":
        """Return a copy with an updated status.

        Parameters
        ----------
        new_status:
            The new ``ProductStatus`` to assign.

        Returns
        -------
        JudgmentProduct
        """
        return replace(self, status=new_status)

    def with_residuals(
        self, residuals: Sequence[ResidualObligation]
    ) -> "JudgmentProduct":
        """Return a copy with a new residual tuple.

        Parameters
        ----------
        residuals:
            The replacement residual sequence.

        Returns
        -------
        JudgmentProduct
        """
        return replace(self, residuals=tuple(residuals))

    def add_obstruction(self, obs: Obstruction) -> "JudgmentProduct":
        """Return a copy with *obs* appended to obstructions.

        Parameters
        ----------
        obs:
            The new ``Obstruction`` to record.

        Returns
        -------
        JudgmentProduct
        """
        return replace(
            self,
            obstructions=self.obstructions + (obs,),
            status=ProductStatus.OBSTRUCTED,
        )

    def to_mapping(self) -> dict[str, Any]:
        """Serialise to a plain dictionary.

        Returns
        -------
        dict[str, Any]
        """
        return {
            "product_id": self.product_id,
            "kind": self.kind.value,
            "status": self.status.value,
            "proposition_label": self.proposition_label,
            "constituent_hashes": list(self.constituent_hashes),
            "residual_count": self.residual_count(),
            "obstruction_count": len(self.obstructions),
            "trust_floor": self.trust_floor().name,
            "coordinate_label": self.coordinate_label,
            "created_at": self.created_at,
            "content_hash": self.content_hash(),
        }

    def __repr__(self) -> str:
        return (
            f"JudgmentProduct(id={self.product_id!r}, kind={self.kind.value}, "
            f"status={self.status.value}, residuals={self.residual_count()})"
        )


# ---------------------------------------------------------------------------
# SemanticProduct
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SemanticProduct:
    """A typed container coupling a :class:`JudgmentProduct` to its section witness.

    A ``SemanticProduct`` is the result of a *successful* gluing: it binds
    the algebraic product value to the geometric section that witnesses it.
    The section provides the proof-carrying data; the product provides the
    algebraic summary.

    Theory reference: theory2.tex §5.1 Def 2.

    Parameters
    ----------
    product:
        The underlying ``JudgmentProduct``.
    section:
        The ``Section`` that witnesses the product (may be ``None`` if
        section data is unavailable or gluing is incomplete).
    gluing_status:
        Status of the gluing operation that produced ``section``.
    type_label:
        Semantic type tag (e.g. ``"behavioral_contract"``,
        ``"resource_bound"``).
    is_global:
        ``True`` iff the witnessing section is a global section.
    notes:
        Free-text annotations from the producing algorithm.
    """

    product: JudgmentProduct
    section: Section | None = None
    gluing_status: GluingStatus = GluingStatus.MISSING_DATA
    type_label: str = ""
    is_global: bool = False
    notes: str = ""

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def is_complete(self) -> bool:
        """Return ``True`` iff both product and section are fully assembled.

        Returns
        -------
        bool
        """
        return (
            self.section is not None
            and self.gluing_status == GluingStatus.SUCCESS
            and self.product.is_discharged()
        )

    def is_witnessed(self) -> bool:
        """Return ``True`` iff a section witness is attached.

        Returns
        -------
        bool
        """
        return self.section is not None

    def proposition_label(self) -> str:
        """Convenience accessor for the product's proposition label.

        Returns
        -------
        str
        """
        return self.product.proposition_label

    def trust_floor(self) -> TrustLevel:
        """Delegate to the underlying product's trust floor.

        Returns
        -------
        TrustLevel
        """
        return self.product.trust_floor()

    def residual_count(self) -> int:
        """Delegate to the underlying product's residual count.

        Returns
        -------
        int
        """
        return self.product.residual_count()

    def with_section(self, section: Section) -> "SemanticProduct":
        """Return a copy with a new witnessing section.

        Parameters
        ----------
        section:
            The new section to attach.

        Returns
        -------
        SemanticProduct
        """
        return replace(
            self,
            section=section,
            gluing_status=GluingStatus.SUCCESS,
            is_global=section.is_global,
        )

    def with_gluing_status(self, status: GluingStatus) -> "SemanticProduct":
        """Return a copy with an updated gluing status.

        Parameters
        ----------
        status:
            New ``GluingStatus`` value.

        Returns
        -------
        SemanticProduct
        """
        return replace(self, gluing_status=status)

    def to_mapping(self) -> dict[str, Any]:
        """Serialise to a plain dictionary.

        Returns
        -------
        dict[str, Any]
        """
        d: dict[str, Any] = {
            "product": self.product.to_mapping(),
            "gluing_status": self.gluing_status.value,
            "type_label": self.type_label,
            "is_global": self.is_global,
            "is_complete": self.is_complete(),
            "notes": self.notes,
        }
        if self.section is not None:
            d["section_patch"] = self.section.patch
        return d

    def __repr__(self) -> str:
        witnessed = "witnessed" if self.is_witnessed() else "unwitnessed"
        return (
            f"SemanticProduct({witnessed}, type={self.type_label!r}, "
            f"complete={self.is_complete()})"
        )


# ---------------------------------------------------------------------------
# LocalJudgmentSection (package-local section view)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LocalJudgmentSection:
    """A lightweight, package-local view of a section for product assembly.

    This class provides a simplified interface to section data without
    requiring full ``Section`` objects in every callsite.  It is used
    internally by the algorithms module to assemble products from sections
    supplied by calling code.

    Parameters
    ----------
    patch:
        The coordinate patch identifier this section lives over.
    proposition_label:
        Proposition label for the judgment carried by this section.
    judgment_status:
        Status of the judgment at this coordinate.
    trust_level:
        Trust level of the judgment.
    residuals:
        Open residual obligation labels (strings, not full objects).
    obstruction_labels:
        Obstruction description strings.
    provenance_labels:
        Provenance source labels.
    data:
        Arbitrary section data payload.
    is_compatible_flag:
        Pre-computed compatibility flag with adjacent sections.
    """

    patch: str
    proposition_label: str = ""
    judgment_status: JudgmentStatus = JudgmentStatus.PROPOSED
    trust_level: TrustLevel = TrustLevel.UNVERIFIED
    residuals: tuple[str, ...] = ()
    obstruction_labels: tuple[str, ...] = ()
    provenance_labels: tuple[str, ...] = ()
    data: Mapping[str, Any] = field(default_factory=dict)
    is_compatible_flag: bool | None = None

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def is_settled(self) -> bool:
        """Return ``True`` iff the carried judgment is settled.

        Returns
        -------
        bool
        """
        return self.judgment_status == JudgmentStatus.SETTLED

    def has_residuals(self) -> bool:
        """Return ``True`` iff any residuals are present.

        Returns
        -------
        bool
        """
        return len(self.residuals) > 0

    def has_obstructions(self) -> bool:
        """Return ``True`` iff any obstructions are recorded.

        Returns
        -------
        bool
        """
        return len(self.obstruction_labels) > 0

    def compatible_with(self, other: "LocalJudgmentSection") -> bool:
        """Return ``True`` iff this section is compatible with *other*.

        Compatibility is declared when:

        * The pre-computed flag is set on both and both are ``True``.
        * Otherwise: neither section is obstructed and both carry the
          same proposition label (a conservative heuristic).

        Parameters
        ----------
        other:
            The other section to compare.

        Returns
        -------
        bool
        """
        if self.is_compatible_flag is not None and other.is_compatible_flag is not None:
            return self.is_compatible_flag and other.is_compatible_flag
        return (
            not self.has_obstructions()
            and not other.has_obstructions()
            and self.proposition_label == other.proposition_label
        )

    def content_hash(self) -> str:
        """Return a stable content hash of this section view.

        Returns
        -------
        str
            16-character hex digest.
        """
        payload = json.dumps(
            {
                "patch": self.patch,
                "prop": self.proposition_label,
                "status": self.judgment_status.value,
            },
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    def to_mapping(self) -> dict[str, Any]:
        """Serialise to a plain dictionary.

        Returns
        -------
        dict[str, Any]
        """
        return {
            "patch": self.patch,
            "proposition_label": self.proposition_label,
            "judgment_status": self.judgment_status.value,
            "trust_level": self.trust_level.name,
            "residuals": list(self.residuals),
            "obstructions": list(self.obstruction_labels),
            "provenance": list(self.provenance_labels),
            "content_hash": self.content_hash(),
        }

    def __repr__(self) -> str:
        return (
            f"LocalJudgmentSection(patch={self.patch!r}, "
            f"status={self.judgment_status.value}, "
            f"residuals={len(self.residuals)})"
        )


# ---------------------------------------------------------------------------
# ComparisonMap
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ComparisonMap:
    """An explicit, structure-preserving morphism between two judgment products.

    A ``ComparisonMap`` records *how* two ``JudgmentProduct`` instances
    relate to each other under a given ``ComparisonMode``.  It is not
    merely a boolean verdict — it carries:

    * The mode (equivalence, refinement, regression).
    * The directionality (which product is the source, which the target).
    * Compatible residual pairs (residuals that correspond across products).
    * Obstruction witnesses (when not compatible).
    * Metadata about how the map was computed.

    Theory reference: theory2.tex §5.4 Def 1.

    Parameters
    ----------
    map_id:
        Stable identifier for this comparison map.
    source_id:
        ``product_id`` of the source ``JudgmentProduct``.
    target_id:
        ``product_id`` of the target ``JudgmentProduct``.
    mode:
        The ``ComparisonMode`` used.
    is_morphism:
        ``True`` iff the comparison establishes a valid morphism.
    compatible_residual_pairs:
        Pairs ``(source_residual_label, target_residual_label)`` that
        correspond under this map.
    obstruction_witnesses:
        String descriptions of obstructions preventing a full morphism.
    comparison_result:
        The underlying ``ComparisonResult`` from the sections layer,
        if available.
    notes:
        Free-text notes about this comparison.
    computed_at:
        ISO-8601 timestamp.
    """

    map_id: str
    source_id: str
    target_id: str
    mode: ComparisonMode = ComparisonMode.EQUIVALENCE
    is_morphism: bool = False
    compatible_residual_pairs: tuple[tuple[str, str], ...] = ()
    obstruction_witnesses: tuple[str, ...] = ()
    comparison_result: ComparisonResult | None = None
    notes: str = ""
    computed_at: str = field(default_factory=_now_iso)

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def is_equivalence(self) -> bool:
        """Return ``True`` iff this is an equivalence-mode morphism.

        Returns
        -------
        bool
        """
        return self.mode == ComparisonMode.EQUIVALENCE and self.is_morphism

    def is_refinement(self) -> bool:
        """Return ``True`` iff this is a refinement-mode morphism.

        Returns
        -------
        bool
        """
        return self.mode == ComparisonMode.REFINEMENT and self.is_morphism

    def is_regression(self) -> bool:
        """Return ``True`` iff the target is a regression from the source.

        Returns
        -------
        bool
        """
        return self.mode == ComparisonMode.REGRESSION

    def has_obstructions(self) -> bool:
        """Return ``True`` iff any obstruction witnesses are recorded.

        Returns
        -------
        bool
        """
        return len(self.obstruction_witnesses) > 0

    def residual_correspondence_count(self) -> int:
        """Return how many residual pairs are recorded.

        Returns
        -------
        int
        """
        return len(self.compatible_residual_pairs)

    def invert(self) -> "ComparisonMap":
        """Return the inverse map (source ↔ target swapped).

        This is only semantically valid when ``self.mode`` is
        ``EQUIVALENCE``.  For other modes, the returned map is marked as
        ``is_morphism=False``.

        Returns
        -------
        ComparisonMap
        """
        inverted_mode = (
            ComparisonMode.EQUIVALENCE
            if self.mode == ComparisonMode.EQUIVALENCE
            else ComparisonMode.REGRESSION
        )
        inverted_pairs = tuple((t, s) for s, t in self.compatible_residual_pairs)
        return replace(
            self,
            map_id=f"{self.map_id}_inv",
            source_id=self.target_id,
            target_id=self.source_id,
            mode=inverted_mode,
            is_morphism=self.is_morphism and self.mode == ComparisonMode.EQUIVALENCE,
            compatible_residual_pairs=inverted_pairs,
        )

    def compose(self, other: "ComparisonMap") -> "ComparisonMap":
        """Compose this map with *other* (self: A→B, other: B→C → result: A→C).

        Parameters
        ----------
        other:
            The map to compose on the right.  Its ``source_id`` must
            equal this map's ``target_id``.

        Returns
        -------
        ComparisonMap
            A new map representing the composition.

        Raises
        ------
        ValueError
            If source/target IDs are incompatible.
        """
        if other.source_id != self.target_id:
            raise ValueError(
                f"Cannot compose maps: target_id={self.target_id!r} "
                f"!= other.source_id={other.source_id!r}"
            )
        composed_mode = (
            ComparisonMode.EQUIVALENCE
            if self.mode == other.mode == ComparisonMode.EQUIVALENCE
            else ComparisonMode.REFINEMENT
        )
        composed_morphism = self.is_morphism and other.is_morphism
        combined_obs = self.obstruction_witnesses + other.obstruction_witnesses
        return ComparisonMap(
            map_id=f"{self.map_id}·{other.map_id}",
            source_id=self.source_id,
            target_id=other.target_id,
            mode=composed_mode,
            is_morphism=composed_morphism,
            compatible_residual_pairs=self.compatible_residual_pairs,
            obstruction_witnesses=combined_obs,
            notes=f"Composed: {self.map_id} ∘ {other.map_id}",
            computed_at=_now_iso(),
        )

    def to_mapping(self) -> dict[str, Any]:
        """Serialise to a plain dictionary.

        Returns
        -------
        dict[str, Any]
        """
        return {
            "map_id": self.map_id,
            "source_id": self.source_id,
            "target_id": self.target_id,
            "mode": self.mode.value,
            "is_morphism": self.is_morphism,
            "residual_pairs": [list(p) for p in self.compatible_residual_pairs],
            "obstruction_witnesses": list(self.obstruction_witnesses),
            "notes": self.notes,
            "computed_at": self.computed_at,
        }

    def __repr__(self) -> str:
        return (
            f"ComparisonMap(id={self.map_id!r}, "
            f"{self.source_id!r}→{self.target_id!r}, "
            f"mode={self.mode.value}, morphism={self.is_morphism})"
        )


# ---------------------------------------------------------------------------
# ExplanationProjection
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ExplanationProjection:
    """A projection from a :class:`JudgmentProduct` to a structured explanation.

    An ``ExplanationProjection`` captures *what the product means* in
    human-readable terms.  It is generated by an algorithm
    (:func:`algorithms.JudgmentAlgorithms.generate_explanation`) and can
    be rendered in ``BRIEF``, ``DETAILED``, or ``STRUCTURED`` modes.

    Theory reference: theory2.tex §5.4 Def 2.

    Parameters
    ----------
    projection_id:
        Stable identifier for this projection.
    product_id:
        The ``product_id`` of the ``JudgmentProduct`` being explained.
    mode:
        Rendering mode (brief / detailed / structured).
    headline:
        One-sentence summary of the judgment result.
    body:
        Full explanation prose (used in ``DETAILED`` mode).
    evidence_citations:
        Labels of evidence items cited in the explanation.
    residual_summaries:
        Short descriptions of open residuals, in order.
    obstruction_summaries:
        Short descriptions of obstructions, in order.
    structured_data:
        Key-value data for ``STRUCTURED`` mode rendering.
    comparison_map_id:
        If this projection was derived from a comparison, the
        ``ComparisonMap.map_id`` that informed it.
    generated_at:
        ISO-8601 timestamp of projection generation.
    """

    projection_id: str
    product_id: str
    mode: ProjectionMode = ProjectionMode.BRIEF
    headline: str = ""
    body: str = ""
    evidence_citations: tuple[str, ...] = ()
    residual_summaries: tuple[str, ...] = ()
    obstruction_summaries: tuple[str, ...] = ()
    structured_data: Mapping[str, Any] = field(default_factory=dict)
    comparison_map_id: str = ""
    generated_at: str = field(default_factory=_now_iso)

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def has_residuals(self) -> bool:
        """Return ``True`` iff residual summaries are present.

        Returns
        -------
        bool
        """
        return len(self.residual_summaries) > 0

    def has_obstructions(self) -> bool:
        """Return ``True`` iff obstruction summaries are present.

        Returns
        -------
        bool
        """
        return len(self.obstruction_summaries) > 0

    def is_derived_from_comparison(self) -> bool:
        """Return ``True`` iff this projection was derived from a comparison map.

        Returns
        -------
        bool
        """
        return bool(self.comparison_map_id)

    def render(self) -> str:
        """Render the projection as a string according to ``self.mode``.

        Returns
        -------
        str
            A single string rendering of the explanation.
        """
        if self.mode == ProjectionMode.BRIEF:
            return self.headline
        if self.mode == ProjectionMode.DETAILED:
            parts = [self.headline]
            if self.body:
                parts.append(self.body)
            if self.residual_summaries:
                parts.append("Open obligations:")
                parts.extend(f"  • {r}" for r in self.residual_summaries)
            if self.obstruction_summaries:
                parts.append("Obstructions:")
                parts.extend(f"  ✗ {o}" for o in self.obstruction_summaries)
            return "\n".join(parts)
        # STRUCTURED: emit as JSON-like string
        return json.dumps(
            {
                "headline": self.headline,
                "body": self.body,
                "residuals": list(self.residual_summaries),
                "obstructions": list(self.obstruction_summaries),
                **dict(self.structured_data),
            },
            indent=2,
        )

    def with_mode(self, mode: ProjectionMode) -> "ExplanationProjection":
        """Return a copy re-rendered in *mode*.

        Parameters
        ----------
        mode:
            New ``ProjectionMode``.

        Returns
        -------
        ExplanationProjection
        """
        return replace(self, mode=mode)

    def append_residual(self, summary: str) -> "ExplanationProjection":
        """Return a copy with *summary* added to the residual list.

        Parameters
        ----------
        summary:
            Short description of the new residual.

        Returns
        -------
        ExplanationProjection
        """
        return replace(
            self, residual_summaries=self.residual_summaries + (summary,)
        )

    def append_obstruction(self, summary: str) -> "ExplanationProjection":
        """Return a copy with *summary* added to the obstruction list.

        Parameters
        ----------
        summary:
            Short description of the new obstruction.

        Returns
        -------
        ExplanationProjection
        """
        return replace(
            self, obstruction_summaries=self.obstruction_summaries + (summary,)
        )

    def to_mapping(self) -> dict[str, Any]:
        """Serialise to a plain dictionary.

        Returns
        -------
        dict[str, Any]
        """
        return {
            "projection_id": self.projection_id,
            "product_id": self.product_id,
            "mode": self.mode.value,
            "headline": self.headline,
            "body": self.body,
            "evidence_citations": list(self.evidence_citations),
            "residual_summaries": list(self.residual_summaries),
            "obstruction_summaries": list(self.obstruction_summaries),
            "structured_data": dict(self.structured_data),
            "comparison_map_id": self.comparison_map_id,
            "generated_at": self.generated_at,
        }

    def __repr__(self) -> str:
        return (
            f"ExplanationProjection(id={self.projection_id!r}, "
            f"mode={self.mode.value}, product={self.product_id!r})"
        )


# ---------------------------------------------------------------------------
# __all__
# ---------------------------------------------------------------------------

__all__: list[str] = [
    # Enumerations
    "ProductStatus",
    "ProductKind",
    "ProjectionMode",
    # Models
    "JudgmentProduct",
    "SemanticProduct",
    "LocalJudgmentSection",
    "ComparisonMap",
    "ExplanationProjection",
    # Cross-referencing helpers
    "model_site_bridge",
    "model_encoding_bridge",
]


# ---------------------------------------------------------------------------
# Cross-referencing helpers (Theory2.tex §5 — Judgments and Products)
# ---------------------------------------------------------------------------

import logging

_logger = logging.getLogger(__name__)


def model_site_bridge(model: Any) -> dict[str, Any]:
    """Map a judgment product model to site coordinates.

    Uses ``jugeo.geometry.site`` for coordinate construction and
    ``jugeo.geometry.covers`` for cover membership as described in
    Theory2.tex §5 (Judgments and Products).

    Parameters
    ----------
    model : Any
        A :class:`JudgmentProduct` or :class:`SemanticProduct` instance.

    Returns
    -------
    dict[str, Any]
        ``{"coordinate": ..., "kind": ..., "cover_member": ..., "model_id": str}``
    """
    try:
        from jugeo.geometry.site import Coordinate, CoordinateKind  # noqa: F811
        from jugeo.geometry.covers import CoverMember  # noqa: F811
    except ImportError as exc:
        _logger.debug("Optional cross-reference imports unavailable: %s", exc)
        return {"coordinate": None, "kind": None, "cover_member": None,
                "model_id": None, "error": str(exc)}

    model_id = getattr(model, "product_id", None) or str(model)
    kind = CoordinateKind.from_model(model) if hasattr(CoordinateKind, "from_model") else CoordinateKind.DEFAULT
    coordinate = Coordinate(source=model_id, kind=kind)
    cover_member = CoverMember(coordinate=coordinate)
    _logger.debug("model_site_bridge: model=%s kind=%s", model_id, kind)
    return {"coordinate": coordinate, "kind": kind, "cover_member": cover_member,
            "model_id": model_id}


def model_encoding_bridge(model: Any, *, format: str = "z3") -> dict[str, Any]:
    """Encode a judgment product model for solver consumption.

    Uses ``jugeo.encodings`` to translate a model into a solver-compatible
    representation as described in Theory2.tex §5 (Judgments and Products).

    Parameters
    ----------
    model : Any
        A :class:`JudgmentProduct` or :class:`SemanticProduct` instance.
    format : str
        Target encoding format (default ``"z3"``).

    Returns
    -------
    dict[str, Any]
        ``{"judgment_encoding": ..., "section_encoding": ..., "format": str, "model_id": str}``
    """
    try:
        from jugeo.encodings import encode_judgment, encode_section  # noqa: F811
    except ImportError as exc:
        _logger.debug("Optional cross-reference imports unavailable: %s", exc)
        return {"judgment_encoding": None, "section_encoding": None,
                "format": format, "model_id": None, "error": str(exc)}

    model_id = getattr(model, "product_id", None) or str(model)
    judgments = getattr(model, "judgments", [])
    section = getattr(model, "section", None)
    judgment_encoding = encode_judgment(judgments, format=format)
    section_encoding = encode_section(section, format=format) if section else None
    _logger.debug("model_encoding_bridge: model=%s format=%s", model_id, format)
    return {"judgment_encoding": judgment_encoding, "section_encoding": section_encoding,
            "format": format, "model_id": model_id}


# copilot: core domain models — judgment_products package.
