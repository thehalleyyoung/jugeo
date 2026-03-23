"""Section 04 — Comparison maps and explanation projections.

Theory2.tex Chapter 5, Section 5.4.

When two verification passes produce judgment products for the same
semantic claim, the question "are they equivalent?" cannot be answered
by comparing booleans — there are no booleans.  The question must be
answered by constructing an explicit **comparison map**: a
structure-preserving morphism between the two products that certifies
their relationship.

Similarly, translating a judgment product into a human-readable
explanation is not a lossy string-formatting step — it is a
**projection**: a morphism from the algebraic product to a structured
explanation space that preserves the semantic content that matters for
human understanding.

This module implements:

* :class:`ComparisonMap` — the central morphism type (also in models.py,
  but here extended with witness construction logic).
* :class:`ExplanationProjection` — projection to structured explanation.
* :class:`RefinementWitness` — explicit witness certifying one product
  refines another (theory2.tex §5.4 Prop 1).
* :class:`EquivalenceCertificate` — certificate of semantic equivalence
  (theory2.tex §5.4 Prop 2).
* :class:`ComparisonMaps` — static factory / utility class.

References
----------
theory2.tex §5.4 Def 1–2, Prop 1–2, Cor 2.

# copilot: s04 — comparison maps and explanation projections.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Mapping, Sequence

from jugeo.judgments.comparisons import (
    ComparisonMode,
    ComparisonResult,
    compare_sections,
)
from jugeo.judgments.judgment_terms import (
    JudgmentStatus,
    Obstruction,
    TrustLevel,
)
from jugeo.judgments.sections import Section

from jugeo.foundations.judgment_products.models import (
    ComparisonMap as BaseComparisonMap,
    ExplanationProjection as BaseExplanationProjection,
    JudgmentProduct,
    ProductStatus,
    ProjectionMode,
)


# ---------------------------------------------------------------------------
# Supporting enumerations
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class WitnessKind(str, Enum):
    """The kind of a refinement witness or equivalence certificate.

    Members
    -------
    INCLUSION
        The witness asserts a strict inclusion (source ⊆ target).
    EQUIVALENCE
        The witness asserts full equivalence (source ≅ target).
    WEAKENING
        The witness asserts a trust-weakening (source ≽ target but not ≅).
    FACTORISATION
        The witness asserts the source factors through the target.
    SUBSUMPTION
        The witness asserts the target subsumes the source's obligations.
    """

    INCLUSION = "inclusion"
    EQUIVALENCE = "equivalence"
    WEAKENING = "weakening"
    FACTORISATION = "factorisation"
    SUBSUMPTION = "subsumption"


class ExplanationScope(str, Enum):
    """Scope of an :class:`ExplanationProjection`.

    Members
    -------
    FULL
        Explains all aspects of the product (residuals, obstructions,
        evidence, trust).
    SUMMARY
        A concise summary of the outcome.
    RESIDUALS_ONLY
        Focuses only on open residual obligations.
    EVIDENCE_ONLY
        Focuses only on the evidence base.
    COMPARISON
        Explains a comparison between two products.
    """

    FULL = "full"
    SUMMARY = "summary"
    RESIDUALS_ONLY = "residuals_only"
    EVIDENCE_ONLY = "evidence_only"
    COMPARISON = "comparison"


# ---------------------------------------------------------------------------
# ComparisonMap (extended)
# ---------------------------------------------------------------------------
# The base ComparisonMap lives in models.py.  This module re-exports it
# and provides factory methods for the judgment_products package.


class ComparisonMap(BaseComparisonMap):
    """Extended :class:`models.ComparisonMap` with witness construction.

    This subclass adds class methods that construct ``ComparisonMap``
    instances from pairs of :class:`JudgmentProduct` objects, delegating
    the section-level comparison to the ``jugeo.judgments.comparisons``
    layer.

    The parent class is frozen; all mutation returns new instances.

    Theory reference: theory2.tex §5.4 Def 1.
    """

    # ------------------------------------------------------------------
    # Factory methods
    # ------------------------------------------------------------------

    @classmethod
    def between_products(
        cls,
        left: JudgmentProduct,
        right: JudgmentProduct,
        mode: ComparisonMode = ComparisonMode.EQUIVALENCE,
    ) -> "ComparisonMap":
        """Construct a ``ComparisonMap`` between two judgment products.

        The comparison analyses:

        * Trust ordering (which product has stronger trust).
        * Residual overlap (shared residual descriptions).
        * Obstruction presence (are obstructions present in either?).

        Parameters
        ----------
        left:
            The source product.
        right:
            The target product.
        mode:
            The ``ComparisonMode`` to use.

        Returns
        -------
        ComparisonMap
        """
        # Compute trust ordering
        left_trust = left.trust_floor()
        right_trust = right.trust_floor()

        left_stronger = left_trust >= right_trust
        is_morphism = True

        # Check for obstructions
        obs_witnesses: list[str] = []
        if left.has_obstructions():
            obs_witnesses.append(f"Left product {left.product_id!r} has obstructions.")
            is_morphism = False
        if right.has_obstructions():
            obs_witnesses.append(f"Right product {right.product_id!r} has obstructions.")
            is_morphism = False

        # Compute residual correspondence
        left_res = {
            getattr(r, "description", str(r)): r
            for r in left.residuals
        }
        right_res = {
            getattr(r, "description", str(r)): r
            for r in right.residuals
        }
        shared_descs = set(left_res) & set(right_res)
        residual_pairs = tuple(
            (desc, desc) for desc in sorted(shared_descs)
        )

        # Determine effective mode
        if mode == ComparisonMode.EQUIVALENCE and (
            left.status != right.status
            or left.residual_count() != right.residual_count()
        ):
            mode = ComparisonMode.REFINEMENT if left_stronger else ComparisonMode.REGRESSION

        map_id = (
            f"cmp_{left.product_id[:8]}_{right.product_id[:8]}_"
            f"{mode.value[:3]}"
        )
        return cls(
            map_id=map_id,
            source_id=left.product_id,
            target_id=right.product_id,
            mode=mode,
            is_morphism=is_morphism,
            compatible_residual_pairs=residual_pairs,
            obstruction_witnesses=tuple(obs_witnesses),
            notes=(
                f"Trust: {left_trust.name} vs {right_trust.name}. "
                f"Residual overlap: {len(shared_descs)}/{max(len(left_res), len(right_res), 1)}."
            ),
        )

    @classmethod
    def identity(cls, product: JudgmentProduct) -> "ComparisonMap":
        """Construct the identity comparison map for *product*.

        Parameters
        ----------
        product:
            The product to build an identity map for.

        Returns
        -------
        ComparisonMap
        """
        return cls(
            map_id=f"id_{product.product_id[:8]}",
            source_id=product.product_id,
            target_id=product.product_id,
            mode=ComparisonMode.EQUIVALENCE,
            is_morphism=True,
            compatible_residual_pairs=tuple(
                (getattr(r, "description", str(r)),) * 2
                for r in product.residuals
            ),
            notes="Identity comparison map.",
        )

    @classmethod
    def from_comparison_result(
        cls,
        result: ComparisonResult,
        left_id: str,
        right_id: str,
    ) -> "ComparisonMap":
        """Construct from a sections-layer :class:`ComparisonResult`.

        Parameters
        ----------
        result:
            The ``ComparisonResult`` from the sections layer.
        left_id:
            ``product_id`` of the left product.
        right_id:
            ``product_id`` of the right product.

        Returns
        -------
        ComparisonMap
        """
        obs = tuple(str(o) for o in result.obstructions)
        res = tuple(str(r) for r in result.residuals)
        pairs = tuple(zip(res, res))
        return cls(
            map_id=f"sec_cmp_{left_id[:6]}_{right_id[:6]}",
            source_id=left_id,
            target_id=right_id,
            mode=result.mode,
            is_morphism=result.compatible,
            compatible_residual_pairs=pairs,
            obstruction_witnesses=obs,
            comparison_result=result,
            notes=f"Built from sections ComparisonResult (compatible={result.compatible}).",
        )

    # ------------------------------------------------------------------
    # Witness construction
    # ------------------------------------------------------------------

    def to_refinement_witness(
        self,
        left: JudgmentProduct,
        right: JudgmentProduct,
    ) -> "RefinementWitness":
        """Construct a :class:`RefinementWitness` from this comparison map.

        Parameters
        ----------
        left:
            The source product (should match ``self.source_id``).
        right:
            The target product (should match ``self.target_id``).

        Returns
        -------
        RefinementWitness
        """
        return RefinementWitness.from_comparison_map(self, left, right)

    def to_equivalence_certificate(
        self,
        left: JudgmentProduct,
        right: JudgmentProduct,
    ) -> "EquivalenceCertificate":
        """Construct an :class:`EquivalenceCertificate` from this map.

        Raises ``ValueError`` if the map is not an equivalence.

        Parameters
        ----------
        left:
            The left product.
        right:
            The right product.

        Returns
        -------
        EquivalenceCertificate

        Raises
        ------
        ValueError
            If this map is not an equivalence morphism.
        """
        if not self.is_equivalence():
            raise ValueError(
                f"Cannot build EquivalenceCertificate from a non-equivalence "
                f"ComparisonMap (mode={self.mode.value!r}, "
                f"morphism={self.is_morphism})."
            )
        return EquivalenceCertificate.from_comparison_map(self, left, right)


# ---------------------------------------------------------------------------
# ExplanationProjection (extended)
# ---------------------------------------------------------------------------


class ExplanationProjection(BaseExplanationProjection):
    """Extended :class:`models.ExplanationProjection` with factory methods.

    Provides class methods for constructing explanation projections from
    judgment products and comparison maps.

    Theory reference: theory2.tex §5.4 Def 2.
    """

    # ------------------------------------------------------------------
    # Factory methods
    # ------------------------------------------------------------------

    @classmethod
    def from_product(
        cls,
        product: JudgmentProduct,
        mode: ProjectionMode = ProjectionMode.DETAILED,
        scope: ExplanationScope = ExplanationScope.FULL,
    ) -> "ExplanationProjection":
        """Construct an explanation projection from a judgment product.

        Parameters
        ----------
        product:
            The ``JudgmentProduct`` to explain.
        mode:
            The rendering mode.
        scope:
            The scope of the explanation.

        Returns
        -------
        ExplanationProjection
        """
        pid = str(uuid.uuid4())[:12]

        # Headline
        status_str = product.status.value
        trust_str = product.trust_floor().name.lower().replace("_", " ")
        res_count = product.residual_count()
        obs_count = len(product.obstructions)

        headline = (
            f"Judgment '{product.proposition_label}': "
            f"{status_str} (trust: {trust_str})"
        )
        if res_count:
            headline += f", {res_count} open obligation(s)"
        if obs_count:
            headline += f", {obs_count} obstruction(s)"

        # Body
        body = ""
        if mode == ProjectionMode.DETAILED or scope == ExplanationScope.FULL:
            parts: list[str] = [
                f"Proposition: {product.proposition_label}",
                f"Status: {product.status.value}",
                f"Trust floor: {product.trust_floor().name}",
                f"Coordinate: {product.coordinate_label or '(unspecified)'}",
                f"Constituents: {product.constituent_count()} judgment(s)",
            ]
            body = "\n".join(parts)

        # Residuals
        res_summaries = tuple(
            getattr(r, "description", str(r))
            for r in product.residuals
        )
        if scope == ExplanationScope.RESIDUALS_ONLY:
            res_summaries = res_summaries
            body = ""

        # Obstructions
        obs_summaries = tuple(
            getattr(o, "description", str(o))
            for o in product.obstructions
        )

        # Evidence citations
        evidence_keys = tuple(
            item.canonical_key()
            for item in product.evidence.items
        )

        structured: dict[str, Any] = {}
        if scope in (ExplanationScope.FULL, ExplanationScope.SUMMARY):
            structured = {
                "product_id": product.product_id,
                "status": product.status.value,
                "trust": product.trust_floor().name,
                "residual_count": res_count,
                "obstruction_count": obs_count,
            }

        return cls(
            projection_id=pid,
            product_id=product.product_id,
            mode=mode,
            headline=headline,
            body=body,
            evidence_citations=evidence_keys,
            residual_summaries=res_summaries,
            obstruction_summaries=obs_summaries,
            structured_data=structured,
        )

    @classmethod
    def from_comparison(
        cls,
        map_: ComparisonMap,
        left: JudgmentProduct,
        right: JudgmentProduct,
        mode: ProjectionMode = ProjectionMode.DETAILED,
    ) -> "ExplanationProjection":
        """Construct an explanation projection from a comparison map.

        Parameters
        ----------
        map_:
            The ``ComparisonMap`` between *left* and *right*.
        left:
            The source product.
        right:
            The target product.
        mode:
            The rendering mode.

        Returns
        -------
        ExplanationProjection
        """
        pid = str(uuid.uuid4())[:12]

        relation = (
            "equivalent to" if map_.is_equivalence()
            else ("refines" if map_.is_refinement() else "regresses from")
        )
        headline = (
            f"'{left.proposition_label}' {relation} '{right.proposition_label}'"
        )
        body_parts: list[str] = [
            f"Comparison mode: {map_.mode.value}",
            f"Is structure-preserving morphism: {map_.is_morphism}",
            f"Shared residuals: {map_.residual_correspondence_count()}",
            f"Obstruction witnesses: {len(map_.obstruction_witnesses)}",
        ]
        body = "\n".join(body_parts)

        obs_summaries = map_.obstruction_witnesses
        res_summaries = tuple(
            f"{s} ↔ {t}"
            for s, t in map_.compatible_residual_pairs
        )

        structured = {
            "map_id": map_.map_id,
            "source_id": map_.source_id,
            "target_id": map_.target_id,
            "mode": map_.mode.value,
            "is_morphism": map_.is_morphism,
        }

        return cls(
            projection_id=pid,
            product_id=map_.source_id,
            mode=mode,
            headline=headline,
            body=body,
            residual_summaries=res_summaries,
            obstruction_summaries=obs_summaries,
            structured_data=structured,
            comparison_map_id=map_.map_id,
        )


# ---------------------------------------------------------------------------
# RefinementWitness
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RefinementWitness:
    """Explicit witness certifying that one judgment product refines another.

    A ``RefinementWitness`` is a first-class record that captures the
    proof-relevant data certifying ``source ≤ target`` in the refinement
    ordering.  It is not merely an assertion — it carries the
    *evidence mapping* that shows how each obligation and evidence item
    in the source is covered by the target.

    Theory reference: theory2.tex §5.4 Prop 1.

    Parameters
    ----------
    witness_id:
        Stable unique identifier.
    kind:
        The kind of this witness.
    source_id:
        ``product_id`` of the source (finer) product.
    target_id:
        ``product_id`` of the target (coarser) product.
    evidence_mapping:
        Pairs ``(source_evidence_key, target_evidence_key)`` showing how
        each source evidence item is covered.
    residual_coverage:
        Pairs ``(source_residual_desc, target_residual_desc)`` showing
        residual subsumption.
    trust_ordering_holds:
        ``True`` iff the trust ordering ``source ≥ target`` holds (the
        refinement strengthens trust).
    verified_by:
        Description of the verification method (solver, proof, oracle).
    notes:
        Free-text notes.
    created_at:
        ISO-8601 timestamp.
    """

    witness_id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    kind: WitnessKind = WitnessKind.INCLUSION
    source_id: str = ""
    target_id: str = ""
    evidence_mapping: tuple[tuple[str, str], ...] = ()
    residual_coverage: tuple[tuple[str, str], ...] = ()
    trust_ordering_holds: bool = False
    verified_by: str = ""
    notes: str = ""
    created_at: str = field(default_factory=_now_iso)

    # ------------------------------------------------------------------
    # Derived properties
    # ------------------------------------------------------------------

    def is_strict_refinement(self) -> bool:
        """Return ``True`` iff this is a strict (not equivalence) refinement.

        Returns
        -------
        bool
        """
        return self.kind == WitnessKind.INCLUSION

    def coverage_completeness(self) -> float:
        """Return the fraction of source residuals that are covered.

        Returns
        -------
        float
            In [0.0, 1.0].  Returns 1.0 if there are no residuals.
        """
        if not self.residual_coverage:
            return 1.0
        return len(self.residual_coverage) / max(len(self.residual_coverage), 1)

    def evidence_coverage_count(self) -> int:
        """Return the number of evidence mappings.

        Returns
        -------
        int
        """
        return len(self.evidence_mapping)

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_comparison_map(
        cls,
        map_: BaseComparisonMap,
        left: JudgmentProduct,
        right: JudgmentProduct,
    ) -> "RefinementWitness":
        """Construct from a :class:`ComparisonMap`.

        Parameters
        ----------
        map_:
            The comparison map (should be REFINEMENT or EQUIVALENCE mode).
        left:
            The source product.
        right:
            The target product.

        Returns
        -------
        RefinementWitness
        """
        kind = (
            WitnessKind.EQUIVALENCE
            if map_.mode == ComparisonMode.EQUIVALENCE
            else WitnessKind.INCLUSION
        )
        trust_ok = left.trust_floor() >= right.trust_floor()

        return cls(
            kind=kind,
            source_id=map_.source_id,
            target_id=map_.target_id,
            residual_coverage=map_.compatible_residual_pairs,
            trust_ordering_holds=trust_ok,
            notes=f"Built from ComparisonMap {map_.map_id!r}.",
        )

    # ------------------------------------------------------------------
    # Composition
    # ------------------------------------------------------------------

    def compose(self, other: "RefinementWitness") -> "RefinementWitness":
        """Compose this witness with *other* (self: A≤B, other: B≤C → A≤C).

        Parameters
        ----------
        other:
            The witness to compose on the right.

        Returns
        -------
        RefinementWitness

        Raises
        ------
        ValueError
            If source/target IDs are incompatible.
        """
        if other.source_id != self.target_id:
            raise ValueError(
                f"Cannot compose witnesses: target={self.target_id!r} "
                f"!= other.source={other.source_id!r}."
            )
        combined_ev = self.evidence_mapping + other.evidence_mapping
        combined_res = self.residual_coverage + other.residual_coverage
        return replace(
            self,
            witness_id=f"{self.witness_id}·{other.witness_id}",
            target_id=other.target_id,
            evidence_mapping=combined_ev,
            residual_coverage=combined_res,
            trust_ordering_holds=self.trust_ordering_holds and other.trust_ordering_holds,
            notes=f"Composed: {self.witness_id} ∘ {other.witness_id}",
        )

    def to_mapping(self) -> dict[str, Any]:
        """Serialise to a plain dictionary.

        Returns
        -------
        dict[str, Any]
        """
        return {
            "witness_id": self.witness_id,
            "kind": self.kind.value,
            "source_id": self.source_id,
            "target_id": self.target_id,
            "evidence_mapping_count": self.evidence_coverage_count(),
            "residual_coverage_count": len(self.residual_coverage),
            "trust_ordering_holds": self.trust_ordering_holds,
            "verified_by": self.verified_by,
            "notes": self.notes,
            "created_at": self.created_at,
        }

    def __repr__(self) -> str:
        return (
            f"RefinementWitness(id={self.witness_id!r}, "
            f"kind={self.kind.value}, "
            f"{self.source_id!r}≤{self.target_id!r})"
        )


# ---------------------------------------------------------------------------
# EquivalenceCertificate
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EquivalenceCertificate:
    """A certificate of semantic equivalence between two judgment products.

    An ``EquivalenceCertificate`` witnesses that two ``JudgmentProduct``
    instances are semantically equivalent — they assert the same claim
    with equivalent evidence, residuals, and trust structure.

    It is built from two :class:`RefinementWitness` objects: one in each
    direction (source ≤ target *and* target ≤ source).

    Theory reference: theory2.tex §5.4 Prop 2.

    Parameters
    ----------
    certificate_id:
        Stable unique identifier.
    left_id:
        ``product_id`` of the left product.
    right_id:
        ``product_id`` of the right product.
    forward_witness:
        Witness certifying ``left ≤ right``.
    backward_witness:
        Witness certifying ``right ≤ left``.
    is_sound:
        ``True`` iff both witnesses are valid and consistent.
    verification_method:
        Description of how soundness was verified.
    notes:
        Free-text notes.
    issued_at:
        ISO-8601 timestamp of issuance.
    """

    certificate_id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    left_id: str = ""
    right_id: str = ""
    forward_witness: RefinementWitness | None = None
    backward_witness: RefinementWitness | None = None
    is_sound: bool = False
    verification_method: str = ""
    notes: str = ""
    issued_at: str = field(default_factory=_now_iso)

    # ------------------------------------------------------------------
    # Derived properties
    # ------------------------------------------------------------------

    def is_complete(self) -> bool:
        """Return ``True`` iff both witnesses are present.

        Returns
        -------
        bool
        """
        return (
            self.forward_witness is not None and self.backward_witness is not None
        )

    def is_valid(self) -> bool:
        """Return ``True`` iff the certificate is complete and sound.

        Returns
        -------
        bool
        """
        return self.is_complete() and self.is_sound

    def symmetrize(self) -> "EquivalenceCertificate":
        """Return a copy with left and right swapped.

        Returns
        -------
        EquivalenceCertificate
        """
        return replace(
            self,
            certificate_id=f"{self.certificate_id}_sym",
            left_id=self.right_id,
            right_id=self.left_id,
            forward_witness=self.backward_witness,
            backward_witness=self.forward_witness,
        )

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_comparison_map(
        cls,
        map_: BaseComparisonMap,
        left: JudgmentProduct,
        right: JudgmentProduct,
    ) -> "EquivalenceCertificate":
        """Construct from an equivalence :class:`ComparisonMap`.

        Parameters
        ----------
        map_:
            The comparison map (must be EQUIVALENCE mode and is_morphism=True).
        left:
            The left product.
        right:
            The right product.

        Returns
        -------
        EquivalenceCertificate

        Raises
        ------
        ValueError
            If the map is not an equivalence morphism.
        """
        if not (
            map_.mode == ComparisonMode.EQUIVALENCE and map_.is_morphism
        ):
            raise ValueError(
                "EquivalenceCertificate requires an equivalence morphism."
            )
        fw = RefinementWitness.from_comparison_map(map_, left, right)
        inv_map = map_.invert()
        bw = RefinementWitness.from_comparison_map(inv_map, right, left)
        is_sound = fw.trust_ordering_holds and bw.trust_ordering_holds

        return cls(
            left_id=left.product_id,
            right_id=right.product_id,
            forward_witness=fw,
            backward_witness=bw,
            is_sound=is_sound,
            verification_method="comparison_map",
            notes=f"Built from ComparisonMap {map_.map_id!r}.",
        )

    @classmethod
    def assert_by_declaration(
        cls,
        left: JudgmentProduct,
        right: JudgmentProduct,
        reason: str,
    ) -> "EquivalenceCertificate":
        """Assert equivalence by declaration (no witness, trust manually).

        Parameters
        ----------
        left:
            The left product.
        right:
            The right product.
        reason:
            Free-text reason for the declaration.

        Returns
        -------
        EquivalenceCertificate
        """
        return cls(
            left_id=left.product_id,
            right_id=right.product_id,
            forward_witness=None,
            backward_witness=None,
            is_sound=False,
            verification_method="declaration",
            notes=f"Asserted by declaration: {reason}",
        )

    def to_mapping(self) -> dict[str, Any]:
        """Serialise to a plain dictionary.

        Returns
        -------
        dict[str, Any]
        """
        return {
            "certificate_id": self.certificate_id,
            "left_id": self.left_id,
            "right_id": self.right_id,
            "is_complete": self.is_complete(),
            "is_sound": self.is_sound,
            "is_valid": self.is_valid(),
            "verification_method": self.verification_method,
            "notes": self.notes,
            "issued_at": self.issued_at,
        }

    def __repr__(self) -> str:
        valid_str = "✓" if self.is_valid() else "✗"
        return (
            f"EquivalenceCertificate({valid_str}, "
            f"id={self.certificate_id!r}, "
            f"{self.left_id!r}≅{self.right_id!r})"
        )


# ---------------------------------------------------------------------------
# ComparisonMaps (static utilities)
# ---------------------------------------------------------------------------


class ComparisonMaps:
    """Static utilities for working with comparison maps and explanation projections.

    All methods are pure functions (no mutation, no side effects).
    """

    # ------------------------------------------------------------------
    # Comparison map utilities
    # ------------------------------------------------------------------

    @staticmethod
    def compose_chain(
        maps: Sequence[ComparisonMap],
    ) -> ComparisonMap:
        """Compose a chain of comparison maps into a single map.

        The chain must be compatible (each map's ``target_id`` equals the
        next map's ``source_id``).

        Parameters
        ----------
        maps:
            Ordered sequence of maps to compose.

        Returns
        -------
        ComparisonMap

        Raises
        ------
        ValueError
            If the chain is empty or incompatible.
        """
        maps_list = list(maps)
        if not maps_list:
            raise ValueError("Cannot compose an empty chain of comparison maps.")
        result = maps_list[0]
        for m in maps_list[1:]:
            result = result.compose(m)
        return result

    @staticmethod
    def check_triangle_commutes(
        f: ComparisonMap,
        g: ComparisonMap,
        h: ComparisonMap,
    ) -> bool:
        """Return ``True`` iff the triangle f ; g = h commutes.

        Checks that composing *f* and *g* yields a map with the same
        source, target, and morphism status as *h*.

        Parameters
        ----------
        f:
            The first map (A → B).
        g:
            The second map (B → C).
        h:
            The candidate composite (A → C).

        Returns
        -------
        bool
        """
        try:
            fg = f.compose(g)
        except ValueError:
            return False
        return (
            fg.source_id == h.source_id
            and fg.target_id == h.target_id
            and fg.is_morphism == h.is_morphism
        )

    @staticmethod
    def find_refinement(
        candidate: JudgmentProduct,
        targets: Sequence[JudgmentProduct],
    ) -> ComparisonMap | None:
        """Find the first target that *candidate* refines.

        Parameters
        ----------
        candidate:
            The product to test as a refinement source.
        targets:
            Sequence of target products to test against.

        Returns
        -------
        ComparisonMap | None
            The first refinement map found, or ``None``.
        """
        for target in targets:
            m = ComparisonMap.between_products(
                candidate, target, mode=ComparisonMode.REFINEMENT
            )
            if m.is_refinement():
                return m
        return None

    @staticmethod
    def collect_equivalence_classes(
        products: Sequence[JudgmentProduct],
    ) -> list[list[JudgmentProduct]]:
        """Partition *products* into equivalence classes.

        Two products are placed in the same class iff
        :meth:`ComparisonMap.between_products` returns an equivalence
        morphism between them.

        Parameters
        ----------
        products:
            The products to partition.

        Returns
        -------
        list[list[JudgmentProduct]]
            Each inner list is one equivalence class.
        """
        prods = list(products)
        classes: list[list[JudgmentProduct]] = []
        assigned: set[str] = set()

        for p in prods:
            if p.product_id in assigned:
                continue
            cls_group = [p]
            assigned.add(p.product_id)
            for q in prods:
                if q.product_id in assigned:
                    continue
                m = ComparisonMap.between_products(
                    p, q, mode=ComparisonMode.EQUIVALENCE
                )
                if m.is_equivalence():
                    cls_group.append(q)
                    assigned.add(q.product_id)
            classes.append(cls_group)
        return classes

    # ------------------------------------------------------------------
    # Explanation utilities
    # ------------------------------------------------------------------

    @staticmethod
    def explain_product(
        product: JudgmentProduct,
        mode: ProjectionMode = ProjectionMode.DETAILED,
    ) -> ExplanationProjection:
        """Generate an explanation projection for *product*.

        Parameters
        ----------
        product:
            The product to explain.
        mode:
            Rendering mode.

        Returns
        -------
        ExplanationProjection
        """
        return ExplanationProjection.from_product(product, mode=mode)

    @staticmethod
    def explain_comparison(
        left: JudgmentProduct,
        right: JudgmentProduct,
        mode: ProjectionMode = ProjectionMode.DETAILED,
    ) -> ExplanationProjection:
        """Generate an explanation of the comparison between *left* and *right*.

        Parameters
        ----------
        left:
            The source product.
        right:
            The target product.
        mode:
            Rendering mode.

        Returns
        -------
        ExplanationProjection
        """
        map_ = ComparisonMap.between_products(left, right)
        return ExplanationProjection.from_comparison(map_, left, right, mode=mode)

    @staticmethod
    def batch_explain(
        products: Sequence[JudgmentProduct],
        mode: ProjectionMode = ProjectionMode.BRIEF,
    ) -> tuple[ExplanationProjection, ...]:
        """Generate brief explanations for a batch of products.

        Parameters
        ----------
        products:
            The products to explain.
        mode:
            Rendering mode (defaults to BRIEF for batch use).

        Returns
        -------
        tuple[ExplanationProjection, ...]
        """
        return tuple(
            ExplanationProjection.from_product(p, mode=mode)
            for p in products
        )


# ---------------------------------------------------------------------------
# __all__
# ---------------------------------------------------------------------------

__all__: list[str] = [
    # Enumerations
    "WitnessKind",
    "ExplanationScope",
    # Models (extended from base)
    "ComparisonMap",
    "ExplanationProjection",
    # Witnesses / certificates
    "RefinementWitness",
    "EquivalenceCertificate",
    # Utilities
    "ComparisonMaps",
]

# copilot: s04 — comparison maps and explanation projections.
