"""Integration layer for jugeo.foundations.judgment_products.

Theory2.tex Chapter 5.

This module connects the ``judgment_products`` package to the upstream
``jugeo.judgments`` package.  It provides:

* :class:`JudgmentIntegration` — the primary façade for external callers.
  Given a ``Judgment`` (or a collection), it produces the full semantic
  product: algebraic composition, section gluing, residual tracking, and
  explanation generation.

* :class:`SectionBridge` — translates between the ``sections`` module's
  ``Section`` / ``SectionFamily`` objects and the ``judgment_products``
  ``SectionProduct`` / ``GlobalSection`` objects.

* :class:`ComparisonBridge` — translates between the ``comparisons``
  module's ``ComparisonResult`` and the ``judgment_products``
  ``ComparisonMap`` / ``EquivalenceCertificate``.

* :class:`LocalJudgmentAdapter` — adapts ``LocalJudgment`` (the legacy
  interface) to the new ``JudgmentProduct`` pipeline.

References
----------
theory2.tex §5 (all sections); jugeo.judgments package.

# copilot: integration layer — judgment_products ↔ jugeo.judgments.
"""

from __future__ import annotations

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
    EvidenceBundle,
    Judgment,
    JudgmentAlgebra,
    JudgmentBuilder,
    JudgmentStatus,
    LocalJudgment,
    Obstruction,
    Proposition,
    PropositionKind,
    Provenance,
    ProvenanceSource,
    ResidualObligation,
    TrustLevel,
)
from jugeo.judgments.sections import (
    GluingStatus,
    Section,
    SectionBuilder,
    SectionComparator,
    SectionFamily,
    SectionGluing,
)

from jugeo.foundations.judgment_products.algorithms import (
    JudgmentAlgorithms,
    ProductComputationOptions,
    ProductComputationResult,
)
from jugeo.foundations.judgment_products.models import (
    ComparisonMap as BaseComparisonMap,
    ExplanationProjection as BaseExplanationProjection,
    JudgmentProduct,
    LocalJudgmentSection,
    ProductKind,
    ProductStatus,
    ProjectionMode,
    SemanticProduct,
)
from jugeo.foundations.judgment_products.judgments_are_not_boolean_facts import (
    JudgmentAsObject,
    JudgmentComparison,
    JudgmentProductAlgebra,
    NonBooleanJudgment,
    StructuredJudgment,
    TruthDegree,
)
from jugeo.foundations.judgment_products.residual_obligations_are_the_livin import (
    DischargeStrategy,
    LiveResidualObligation,
    ObligationStatus,
    ObligationTracker,
    ResidualDischarger,
    ResidualSystem,
)
from jugeo.foundations.judgment_products.sections_are_the_real_products_of import (
    FunctorDirection,
    GlobalSection,
    SectionFunctor,
    SectionProduct,
    SectionProductStatus,
    SectionProducts,
)
from jugeo.foundations.judgment_products.comparison_maps_and_explanation_pr import (
    ComparisonMap,
    ComparisonMaps,
    EquivalenceCertificate,
    ExplanationProjection,
    ExplanationScope,
    RefinementWitness,
    WitnessKind,
)


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ---------------------------------------------------------------------------
# SectionBridge
# ---------------------------------------------------------------------------


class SectionBridge:
    """Translates between ``jugeo.judgments.sections`` and ``judgment_products``.

    All methods are static (pure functions, no side effects).
    """

    @staticmethod
    def section_to_local(section: Section) -> LocalJudgmentSection:
        """Convert a ``Section`` to a ``LocalJudgmentSection``.

        Parameters
        ----------
        section:
            The upstream ``Section`` to convert.

        Returns
        -------
        LocalJudgmentSection
        """
        patch = getattr(section, "patch", "")
        judgment = getattr(section, "judgment", None)

        status = JudgmentStatus.PROPOSED
        trust = TrustLevel.UNVERIFIED
        residuals: tuple[str, ...] = ()
        obstructions: tuple[str, ...] = ()
        provenance: tuple[str, ...] = ()
        prop_label = ""

        if judgment is not None:
            status = getattr(judgment, "status", JudgmentStatus.PROPOSED)
            trust_vec = getattr(judgment, "trust_vector", {})
            trust_val = trust_vec.get("level", TrustLevel.UNVERIFIED)
            if isinstance(trust_val, TrustLevel):
                trust = trust_val
            residuals = tuple(
                str(r) for r in getattr(judgment, "obligations", ())
            )
            obstructions = tuple(
                str(o) for o in getattr(judgment, "obstructions", ())
            )
            prop_label = str(getattr(judgment, "proposition", ""))
            provenance = getattr(judgment, "provenance", ())

        return LocalJudgmentSection(
            patch=patch,
            proposition_label=prop_label,
            judgment_status=status,
            trust_level=trust,
            residuals=residuals,
            obstruction_labels=obstructions,
            provenance_labels=tuple(str(p) for p in provenance),
        )

    @staticmethod
    def local_to_section_builder(
        local: LocalJudgmentSection,
    ) -> SectionBuilder:
        """Construct a ``SectionBuilder`` pre-populated from *local*.

        Parameters
        ----------
        local:
            The ``LocalJudgmentSection`` to use as a template.

        Returns
        -------
        SectionBuilder
        """
        from jugeo.geometry.site import CoordinateObject
        # We use SectionBuilder's fluent interface
        builder = SectionBuilder()
        # Set the patch / coordinate via the sections layer's own API
        builder.set_patch(local.patch)
        builder.set_provenance(local.provenance_labels)
        builder.set_residuals(list(local.residuals))
        return builder

    @staticmethod
    def section_family_to_section_products(
        family: SectionFamily,
        coordinate_label: str = "",
    ) -> tuple[SectionProduct, GlobalSection | None]:
        """Translate a ``SectionFamily`` to a ``SectionProduct`` via gluing.

        Parameters
        ----------
        family:
            The ``SectionFamily`` to translate.
        coordinate_label:
            Base coordinate label.

        Returns
        -------
        tuple[SectionProduct, GlobalSection | None]
        """
        return SectionProducts.attempt_global_gluing(family, coordinate_label)

    @staticmethod
    def product_to_section_family(
        product: JudgmentProduct,
        sections_map: Mapping[str, Section],
    ) -> SectionFamily:
        """Translate a ``JudgmentProduct`` back to a ``SectionFamily``.

        Each constituent hash is looked up in *sections_map* by the
        coordinate label prefix.

        Parameters
        ----------
        product:
            The product whose patches to assemble into a family.
        sections_map:
            A mapping from patch label to ``Section``.

        Returns
        -------
        SectionFamily
        """
        from jugeo.geometry.site import CoordinateObject

        family = SectionFamily(
            base_coordinate=CoordinateObject(name=product.coordinate_label or "base")
        )
        for patch, section in sections_map.items():
            family.sections[patch] = section
        return family


# ---------------------------------------------------------------------------
# ComparisonBridge
# ---------------------------------------------------------------------------


class ComparisonBridge:
    """Translates between ``jugeo.judgments.comparisons`` and ``judgment_products``.

    All methods are static (pure functions, no side effects).
    """

    @staticmethod
    def result_to_map(
        result: ComparisonResult,
        left_product_id: str,
        right_product_id: str,
    ) -> ComparisonMap:
        """Convert a ``ComparisonResult`` to a ``ComparisonMap``.

        Parameters
        ----------
        result:
            The ``ComparisonResult`` from the sections layer.
        left_product_id:
            The ``product_id`` of the left product.
        right_product_id:
            The ``product_id`` of the right product.

        Returns
        -------
        ComparisonMap
        """
        return ComparisonMap.from_comparison_result(
            result, left_product_id, right_product_id
        )

    @staticmethod
    def compare_product_sections(
        left_product: JudgmentProduct,
        right_product: JudgmentProduct,
        left_section: Section,
        right_section: Section,
        mode: ComparisonMode = ComparisonMode.EQUIVALENCE,
    ) -> ComparisonMap:
        """Compare two products using their underlying sections.

        Delegates the comparison to the sections layer
        (:func:`jugeo.judgments.comparisons.compare_sections`) and wraps
        the result as a ``ComparisonMap``.

        Parameters
        ----------
        left_product:
            The left product.
        right_product:
            The right product.
        left_section:
            The section witnessing the left product.
        right_section:
            The section witnessing the right product.
        mode:
            Comparison mode.

        Returns
        -------
        ComparisonMap
        """
        result = compare_sections(left_section, right_section, mode=mode)
        return ComparisonBridge.result_to_map(
            result, left_product.product_id, right_product.product_id
        )

    @staticmethod
    def try_certify_equivalence(
        map_: ComparisonMap,
        left: JudgmentProduct,
        right: JudgmentProduct,
    ) -> EquivalenceCertificate | None:
        """Try to build an ``EquivalenceCertificate`` from *map_*.

        Returns ``None`` if the map is not an equivalence morphism.

        Parameters
        ----------
        map_:
            The comparison map.
        left:
            The left product.
        right:
            The right product.

        Returns
        -------
        EquivalenceCertificate | None
        """
        if not map_.is_equivalence():
            return None
        try:
            return EquivalenceCertificate.from_comparison_map(map_, left, right)
        except ValueError:
            return None


# ---------------------------------------------------------------------------
# LocalJudgmentAdapter
# ---------------------------------------------------------------------------


class LocalJudgmentAdapter:
    """Adapts legacy ``LocalJudgment`` objects to the new pipeline.

    ``LocalJudgment`` is the backwards-compatible interface in
    ``jugeo.judgments.judgment_terms``.  This adapter converts them into
    ``JudgmentProduct`` objects suitable for use with
    :class:`JudgmentAlgorithms`.

    All methods are static.
    """

    @staticmethod
    def to_local_section(lj: LocalJudgment) -> LocalJudgmentSection:
        """Convert a ``LocalJudgment`` to a ``LocalJudgmentSection``.

        Parameters
        ----------
        lj:
            The ``LocalJudgment`` to convert.

        Returns
        -------
        LocalJudgmentSection
        """
        patch = str(lj.coordinate.name) if lj.coordinate else ""
        status = getattr(lj, "status", JudgmentStatus.PROPOSED)
        trust_vec = getattr(lj, "trust_vector", {})
        trust_level = trust_vec.get("level", TrustLevel.UNVERIFIED)
        if not isinstance(trust_level, TrustLevel):
            trust_level = TrustLevel.UNVERIFIED

        residuals = tuple(str(r) for r in lj.obligations)
        obstructions = tuple(str(o) for o in lj.obstructions)
        provenance = tuple(str(p) for p in lj.provenance)

        return LocalJudgmentSection(
            patch=patch,
            proposition_label=str(lj.proposition),
            judgment_status=status,
            trust_level=trust_level,
            residuals=residuals,
            obstruction_labels=obstructions,
            provenance_labels=provenance,
        )

    @staticmethod
    def to_product(lj: LocalJudgment) -> JudgmentProduct:
        """Convert a ``LocalJudgment`` to a ``JudgmentProduct``.

        Uses the ``upgrade_to_judgment`` method if available, then wraps
        via :class:`JudgmentAlgorithms`.

        Parameters
        ----------
        lj:
            The ``LocalJudgment`` to convert.

        Returns
        -------
        JudgmentProduct
        """
        try:
            upgraded: Judgment = lj.upgrade_to_judgment()
            opts = ProductComputationOptions(
                attempt_gluing=False,
                compute_degrees=False,
                propagate_residuals=False,
            )
            result = JudgmentAlgorithms.compute_product([upgraded], opts)
            return result.product
        except (AttributeError, TypeError):
            # Fallback: build from local section
            local = LocalJudgmentAdapter.to_local_section(lj)
            result_from_local = JudgmentAlgorithms.compute_product_from_locals(
                [local],
                proposition_label=str(lj.proposition),
            )
            return result_from_local.product

    @staticmethod
    def batch_to_product(
        locals_: Sequence[LocalJudgment],
        coordinate_label: str = "",
    ) -> ProductComputationResult:
        """Convert a batch of ``LocalJudgment`` objects to a single product.

        Parameters
        ----------
        locals_:
            The ``LocalJudgment`` objects to compose.
        coordinate_label:
            Coordinate label for the resulting product.

        Returns
        -------
        ProductComputationResult
        """
        local_sections = [
            LocalJudgmentAdapter.to_local_section(lj) for lj in locals_
        ]
        prop_labels = [str(lj.proposition) for lj in locals_]
        return JudgmentAlgorithms.compute_product_from_locals(
            local_sections,
            coordinate_label=coordinate_label,
            proposition_label=" ∧ ".join(prop_labels[:3])
            + ("…" if len(prop_labels) > 3 else ""),
        )


# ---------------------------------------------------------------------------
# JudgmentIntegration (main façade)
# ---------------------------------------------------------------------------


class JudgmentIntegration:
    """Primary façade connecting judgment_products to jugeo.judgments.

    Provides high-level methods that combine the full pipeline — from
    upstream ``Judgment`` / ``LocalJudgment`` input, through composition
    and gluing, to explanation output — into a single cohesive API.

    Parameters
    ----------
    default_options:
        Default ``ProductComputationOptions`` to use when none are supplied.
    default_strategies:
        Default discharge strategies to use.
    auto_explain:
        If ``True``, explanation projections are generated automatically
        whenever :meth:`run` is called.
    explanation_mode:
        Default rendering mode for auto-generated explanations.
    """

    def __init__(
        self,
        default_options: ProductComputationOptions | None = None,
        default_strategies: Sequence[DischargeStrategy] | None = None,
        auto_explain: bool = True,
        explanation_mode: ProjectionMode = ProjectionMode.BRIEF,
    ) -> None:
        self.default_options = default_options or ProductComputationOptions()
        self.default_strategies = list(
            default_strategies
            or [
                DischargeStrategy.EVIDENCE_MATCH,
                DischargeStrategy.STRUCTURAL_SIMPLIFICATION,
                DischargeStrategy.SUBSUMPTION,
            ]
        )
        self.auto_explain = auto_explain
        self.explanation_mode = explanation_mode
        self._history: list[ProductComputationResult] = []

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------

    def run(
        self,
        judgments: Sequence[Judgment],
        options: ProductComputationOptions | None = None,
    ) -> tuple[ProductComputationResult, ExplanationProjection | None]:
        """Run the full pipeline for a sequence of judgments.

        Parameters
        ----------
        judgments:
            The judgments to process.
        options:
            Computation options (defaults to ``self.default_options``).

        Returns
        -------
        tuple[ProductComputationResult, ExplanationProjection | None]
        """
        opts = options or self.default_options
        result, explanation = JudgmentAlgorithms.full_pipeline(
            judgments, opts, explain=self.auto_explain,
            explanation_mode=self.explanation_mode
        )
        self._history.append(result)
        return result, explanation

    def run_locals(
        self,
        locals_: Sequence[LocalJudgment],
        coordinate_label: str = "",
    ) -> ProductComputationResult:
        """Run the pipeline for a batch of ``LocalJudgment`` objects.

        Parameters
        ----------
        locals_:
            The ``LocalJudgment`` objects to process.
        coordinate_label:
            Coordinate label.

        Returns
        -------
        ProductComputationResult
        """
        result = LocalJudgmentAdapter.batch_to_product(locals_, coordinate_label)
        self._history.append(result)
        return result

    def compare(
        self,
        left: JudgmentProduct,
        right: JudgmentProduct,
        mode: ComparisonMode = ComparisonMode.EQUIVALENCE,
    ) -> tuple[ComparisonMap, ExplanationProjection]:
        """Compare two products and generate an explanation.

        Parameters
        ----------
        left:
            The source product.
        right:
            The target product.
        mode:
            Comparison mode.

        Returns
        -------
        tuple[ComparisonMap, ExplanationProjection]
        """
        map_ = JudgmentAlgorithms.compare_judgments(left, right, mode)
        explanation = JudgmentAlgorithms.generate_comparison_explanation(
            left, right, mode=self.explanation_mode
        )
        return map_, explanation

    def discharge(
        self,
        product: JudgmentProduct,
        evidence: EvidenceBundle,
    ) -> JudgmentProduct:
        """Attempt to discharge all obligations for *product*.

        Parameters
        ----------
        product:
            The product to discharge.
        evidence:
            Available evidence.

        Returns
        -------
        JudgmentProduct
            Updated product with discharged obligations where possible.
        """
        updated, _ = JudgmentAlgorithms.discharge_all_for_product(
            product, evidence, self.default_strategies
        )
        return updated

    def explain(
        self,
        product: JudgmentProduct,
        mode: ProjectionMode | None = None,
        scope: ExplanationScope = ExplanationScope.FULL,
    ) -> ExplanationProjection:
        """Generate an explanation for *product*.

        Parameters
        ----------
        product:
            The product to explain.
        mode:
            Rendering mode (defaults to ``self.explanation_mode``).
        scope:
            Explanation scope.

        Returns
        -------
        ExplanationProjection
        """
        return JudgmentAlgorithms.generate_explanation(
            product, mode=mode or self.explanation_mode, scope=scope
        )

    # ------------------------------------------------------------------
    # Section integration
    # ------------------------------------------------------------------

    def bridge_section(self, section: Section) -> LocalJudgmentSection:
        """Convert an upstream ``Section`` to a ``LocalJudgmentSection``.

        Parameters
        ----------
        section:
            The section to convert.

        Returns
        -------
        LocalJudgmentSection
        """
        return SectionBridge.section_to_local(section)

    def bridge_family(
        self,
        family: SectionFamily,
        coordinate_label: str = "",
    ) -> tuple[SectionProduct, GlobalSection | None]:
        """Translate a ``SectionFamily`` to a ``SectionProduct``.

        Parameters
        ----------
        family:
            The family to translate.
        coordinate_label:
            Base coordinate label.

        Returns
        -------
        tuple[SectionProduct, GlobalSection | None]
        """
        return SectionBridge.section_family_to_section_products(
            family, coordinate_label
        )

    # ------------------------------------------------------------------
    # History / diagnostics
    # ------------------------------------------------------------------

    def history(self) -> tuple[ProductComputationResult, ...]:
        """Return all computation results in chronological order.

        Returns
        -------
        tuple[ProductComputationResult, ...]
        """
        return tuple(self._history)

    def last_result(self) -> ProductComputationResult | None:
        """Return the most recent computation result.

        Returns
        -------
        ProductComputationResult | None
        """
        return self._history[-1] if self._history else None

    def summary(self) -> dict[str, Any]:
        """Return a summary of the integration instance.

        Returns
        -------
        dict[str, Any]
        """
        return {
            "auto_explain": self.auto_explain,
            "explanation_mode": self.explanation_mode.value,
            "default_strategies": [s.value for s in self.default_strategies],
            "history_count": len(self._history),
            "last_product_id": (
                self._history[-1].product.product_id
                if self._history
                else None
            ),
        }

    def __repr__(self) -> str:
        return (
            f"JudgmentIntegration(history={len(self._history)}, "
            f"auto_explain={self.auto_explain})"
        )


# ---------------------------------------------------------------------------
# __all__
# ---------------------------------------------------------------------------

__all__: list[str] = [
    # Bridges
    "SectionBridge",
    "ComparisonBridge",
    # Adapter
    "LocalJudgmentAdapter",
    # Main façade
    "JudgmentIntegration",
]

# copilot: integration layer — judgment_products ↔ jugeo.judgments.
