"""Core algorithms for jugeo.foundations.judgment_products.

Theory2.tex Chapter 5 — All Sections.

This module collects the four principal algorithms of Chapter 5 into a
single :class:`JudgmentAlgorithms` class:

1. :meth:`JudgmentAlgorithms.compute_product` — assemble a
   :class:`~jugeo.foundations.judgment_products.models.JudgmentProduct`
   from a sequence of judgments or local sections.

2. :meth:`JudgmentAlgorithms.compare_judgments` — compute a
   :class:`~jugeo.foundations.judgment_products.comparison_maps_and_explanation_pr.ComparisonMap`
   between two products.

3. :meth:`JudgmentAlgorithms.discharge_residual` — attempt to discharge
   a single residual obligation, updating an
   :class:`~jugeo.foundations.judgment_products.residual_obligations_are_the_livin.ObligationTracker`.

4. :meth:`JudgmentAlgorithms.generate_explanation` — project a product
   to a structured :class:`~jugeo.foundations.judgment_products.comparison_maps_and_explanation_pr.ExplanationProjection`.

Each algorithm is a stateless static method; the class acts as a
namespace.  All state is carried in the input/output data models.

References
----------
theory2.tex §5.1 (compute_product), §5.2 (discharge_residual),
§5.3 (section assembly), §5.4 (compare, generate_explanation).

# copilot: core algorithms — judgment_products package.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Callable, Mapping, Sequence

from jugeo.judgments.comparisons import ComparisonMode, compare_sections
from jugeo.judgments.judgment_terms import (
    EvidenceBundle,
    Judgment,
    JudgmentAlgebra,
    JudgmentStatus,
    LocalJudgment,
    Obstruction,
    ProvenanceSource,
    ResidualObligation,
    TrustLevel,
)
from jugeo.judgments.sections import (
    GluingStatus,
    Section,
    SectionBuilder,
    SectionFamily,
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
    JudgmentProductAlgebra,
    NonBooleanJudgment,
    TruthDegree,
)
from jugeo.foundations.judgment_products.residual_obligations_are_the_livin import (
    DischargeResult,
    DischargeStrategy,
    LiveResidualObligation,
    ObligationStatus,
    ObligationTracker,
    ResidualDischarger,
    ResidualPropagator,
    ResidualSystem,
)
from jugeo.foundations.judgment_products.sections_are_the_real_products_of import (
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


# ---------------------------------------------------------------------------
# Supporting types
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


@dataclass(frozen=True, slots=True)
class ProductComputationOptions:
    """Options controlling :meth:`JudgmentAlgorithms.compute_product`.

    Parameters
    ----------
    attempt_gluing:
        Whether to attempt section gluing after assembling constituents.
    compute_degrees:
        Whether to assign ``TruthDegree`` values to each constituent.
    propagate_residuals:
        Whether to propagate residuals from constituents to the product.
    coordinate_label:
        Coordinate label to attach to the product.
    proposition_label:
        Proposition label override (defaults to the first judgment's formula).
    metadata:
        Arbitrary extra data to attach to the product.
    """

    attempt_gluing: bool = True
    compute_degrees: bool = True
    propagate_residuals: bool = True
    coordinate_label: str = ""
    proposition_label: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProductComputationResult:
    """The full result of a :meth:`JudgmentAlgorithms.compute_product` call.

    Parameters
    ----------
    product:
        The assembled ``JudgmentProduct``.
    semantic_product:
        The ``SemanticProduct`` wrapping the product and its section.
    section_product:
        The ``SectionProduct`` from the gluing attempt, or ``None``.
    global_section:
        The ``GlobalSection`` if gluing succeeded, or ``None``.
    non_boolean_judgments:
        Degree-annotated wrappers for each constituent judgment.
    residual_system:
        The ``ResidualSystem`` tracking obligations for this product.
    warnings:
        Tuple of warning strings emitted during computation.
    """

    product: JudgmentProduct
    semantic_product: SemanticProduct
    section_product: SectionProduct | None = None
    global_section: GlobalSection | None = None
    non_boolean_judgments: tuple[NonBooleanJudgment, ...] = ()
    residual_system: ResidualSystem | None = None
    warnings: tuple[str, ...] = ()

    def is_fully_verified(self) -> bool:
        """Return ``True`` iff the product is discharged and globally witnessed.

        Returns
        -------
        bool
        """
        return (
            self.product.is_discharged()
            and self.global_section is not None
            and self.global_section.is_verified()
        )

    def to_mapping(self) -> dict[str, Any]:
        """Serialise to a plain dictionary.

        Returns
        -------
        dict[str, Any]
        """
        d: dict[str, Any] = {
            "product": self.product.to_mapping(),
            "semantic_product": self.semantic_product.to_mapping(),
            "is_fully_verified": self.is_fully_verified(),
            "non_boolean_count": len(self.non_boolean_judgments),
            "warnings": list(self.warnings),
        }
        if self.section_product is not None:
            d["section_product"] = self.section_product.to_mapping()
        if self.global_section is not None:
            d["global_section"] = self.global_section.to_mapping()
        return d


@dataclass(frozen=True, slots=True)
class DischargeAttemptResult:
    """The result of a :meth:`JudgmentAlgorithms.discharge_residual` call.

    Parameters
    ----------
    obligation_id:
        The ID of the obligation that was attempted.
    discharge_result:
        The underlying ``DischargeResult``.
    updated_product:
        The product with updated status (if discharge changed it).
    updated_tracker:
        The tracker snapshot after the attempt.
    """

    obligation_id: str
    discharge_result: DischargeResult
    updated_product: JudgmentProduct
    updated_tracker_summary: dict[str, int] = field(default_factory=dict)

    def succeeded(self) -> bool:
        """Return ``True`` iff the discharge succeeded.

        Returns
        -------
        bool
        """
        return self.discharge_result.succeeded


# ---------------------------------------------------------------------------
# JudgmentAlgorithms
# ---------------------------------------------------------------------------


class JudgmentAlgorithms:
    """Core algorithms for judgment product computation.

    All methods are static (pure functions).  Each algorithm operates on
    the data models defined in the ``judgment_products`` package and
    returns new immutable result objects.

    Theory reference: theory2.tex §5 (all sections).
    """

    # ==================================================================
    # 1. compute_product
    # ==================================================================

    @staticmethod
    def compute_product(
        judgments: Sequence[Judgment],
        options: ProductComputationOptions | None = None,
    ) -> ProductComputationResult:
        """Assemble a :class:`JudgmentProduct` from a sequence of judgments.

        This is the primary entry point for judgment product computation.
        It:

        1. Wraps each judgment as a :class:`JudgmentAsObject`.
        2. Assigns :class:`TruthDegree` values if ``compute_degrees=True``.
        3. Composes judgments via :class:`JudgmentAlgebra`.
        4. Builds a :class:`JudgmentProduct` from the composed result.
        5. Initialises a :class:`ResidualSystem` for obligation tracking.
        6. Optionally attempts section gluing.

        Parameters
        ----------
        judgments:
            Ordered sequence of ``Judgment`` instances to compose.
        options:
            Options controlling the computation.  Defaults to
            ``ProductComputationOptions()``.

        Returns
        -------
        ProductComputationResult
        """
        opts = options or ProductComputationOptions()
        warnings: list[str] = []

        if not judgments:
            empty_product = JudgmentProduct(
                product_id=str(uuid.uuid4())[:12],
                status=ProductStatus.INCOMPLETE,
                proposition_label=opts.proposition_label or "(empty)",
                coordinate_label=opts.coordinate_label,
            )
            return ProductComputationResult(
                product=empty_product,
                semantic_product=SemanticProduct(product=empty_product),
                warnings=("No judgments provided; product is empty.",),
            )

        # Step 1: Wrap as objects
        objects: list[JudgmentAsObject] = [
            JudgmentProductAlgebra.wrap(j) for j in judgments
        ]

        # Step 2: Assign degrees (optional)
        nb_judgments: tuple[NonBooleanJudgment, ...] = ()
        if opts.compute_degrees:
            nb_judgments = JudgmentProductAlgebra.collect_degrees(objects)

        # Step 3: Compose judgments
        if len(judgments) == 1:
            composed = judgments[0]
        else:
            composed = judgments[0]
            for j in judgments[1:]:
                try:
                    composed = JudgmentAlgebra.compose(composed, j)
                except Exception as exc:  # pragma: no cover
                    warnings.append(f"Composition warning: {exc}")

        # Step 4: Build JudgmentProduct
        status = (
            ProductStatus.DISCHARGED
            if composed.status == JudgmentStatus.SETTLED
            and not composed.obligations
            and not composed.obstructions
            else (
                ProductStatus.OBSTRUCTED
                if composed.obstructions
                else ProductStatus.ASSEMBLED
            )
        )
        prop_label = (
            opts.proposition_label
            or composed.proposition.formula[:80]
        )
        constituent_hashes = tuple(obj.content_hash() for obj in objects)

        product = JudgmentProduct(
            product_id=str(uuid.uuid4())[:12],
            kind=(
                ProductKind.ATOMIC if len(judgments) == 1 else ProductKind.COMPOSED
            ),
            status=status,
            proposition_label=prop_label,
            constituent_hashes=constituent_hashes,
            evidence=composed.evidence,
            residuals=composed.obligations,
            obstructions=composed.obstructions,
            trust=composed.trust,
            provenance=composed.provenance,
            coordinate_label=opts.coordinate_label,
            metadata=opts.metadata,
        )

        # Step 5: Residual system
        res_system: ResidualSystem | None = None
        if opts.propagate_residuals and product.residuals:
            res_system = ResidualSystem()
            res_system.ingest_product(product)

        # Step 6: Attempt section gluing (optional)
        sp: SectionProduct | None = None
        gs: GlobalSection | None = None

        if opts.attempt_gluing and len(judgments) > 1:
            locals_ = [
                LocalJudgmentSection(
                    patch=opts.coordinate_label or f"patch_{i}",
                    proposition_label=j.proposition.formula[:60],
                    judgment_status=j.status,
                    trust_level=(
                        j.evidence.weakest().trust_level
                        if not j.evidence.is_empty() and j.evidence.weakest()
                        else TrustLevel.UNVERIFIED
                    ),
                    residuals=tuple(
                        getattr(r, "description", str(r)) for r in j.obligations
                    ),
                )
                for i, j in enumerate(judgments)
            ]
            sp = SectionProducts.from_local_sections(
                locals_, coordinate_label=opts.coordinate_label
            )
            if sp.is_global():
                gs = GlobalSection.from_section_product(sp, verify_cocycle=True)
        elif opts.attempt_gluing and len(judgments) == 1:
            j0 = judgments[0]
            sp = SectionProduct(
                status=(
                    SectionProductStatus.GLOBAL
                    if j0.status == JudgmentStatus.SETTLED
                    else SectionProductStatus.PARTIAL
                ),
                input_patches=(opts.coordinate_label or "patch_0",),
                coordinate_label=opts.coordinate_label,
            )

        semantic_product = SemanticProduct(
            product=product,
            gluing_status=GluingStatus.SUCCESS if sp and sp.is_global() else GluingStatus.MISSING_DATA,
            type_label=prop_label[:40],
            is_global=gs is not None,
        )

        return ProductComputationResult(
            product=product,
            semantic_product=semantic_product,
            section_product=sp,
            global_section=gs,
            non_boolean_judgments=nb_judgments,
            residual_system=res_system,
            warnings=tuple(warnings),
        )

    @staticmethod
    def compute_product_from_locals(
        locals_: Sequence[LocalJudgmentSection],
        coordinate_label: str = "",
        proposition_label: str = "",
    ) -> ProductComputationResult:
        """Assemble a product directly from :class:`LocalJudgmentSection` objects.

        This variant bypasses full ``Judgment`` composition and works
        purely with local section views — useful when only local data is
        available.

        Parameters
        ----------
        locals_:
            Sequence of ``LocalJudgmentSection`` objects.
        coordinate_label:
            Coordinate label for the resulting product.
        proposition_label:
            Proposition label override.

        Returns
        -------
        ProductComputationResult
        """
        warnings: list[str] = []
        patches = tuple(ls.patch for ls in locals_)

        if not locals_:
            empty_product = JudgmentProduct(
                product_id=str(uuid.uuid4())[:12],
                status=ProductStatus.INCOMPLETE,
                proposition_label=proposition_label or "(empty)",
            )
            return ProductComputationResult(
                product=empty_product,
                semantic_product=SemanticProduct(product=empty_product),
                warnings=("No local sections provided.",),
            )

        # Aggregate trust and residuals
        min_trust = min(
            (ls.trust_level for ls in locals_), default=TrustLevel.UNVERIFIED
        )
        all_residuals_labels = tuple(
            r for ls in locals_ for r in ls.residuals
        )
        all_obstructions = tuple(
            o for ls in locals_ for o in ls.obstruction_labels
        )
        all_settled = all(ls.is_settled() for ls in locals_)

        if all_obstructions:
            warnings.append(
                f"Local sections carry {len(all_obstructions)} obstruction(s)."
            )

        status = (
            ProductStatus.DISCHARGED
            if all_settled and not all_residuals_labels and not all_obstructions
            else (
                ProductStatus.OBSTRUCTED
                if all_obstructions
                else ProductStatus.ASSEMBLED
            )
        )

        prop = proposition_label or (locals_[0].proposition_label if locals_ else "(unknown)")
        product = JudgmentProduct(
            product_id=str(uuid.uuid4())[:12],
            kind=ProductKind.COMPOSED if len(locals_) > 1 else ProductKind.ATOMIC,
            status=status,
            proposition_label=prop,
            constituent_hashes=tuple(ls.content_hash() for ls in locals_),
            coordinate_label=coordinate_label,
        )

        sp = SectionProducts.from_local_sections(locals_, coordinate_label)
        gs: GlobalSection | None = None
        if sp.is_global():
            gs = GlobalSection.from_section_product(sp, verify_cocycle=True)

        res_system: ResidualSystem | None = None
        if all_residuals_labels:
            res_system = ResidualSystem()
            res_system.ingest_product(product)

        semantic = SemanticProduct(
            product=product,
            gluing_status=GluingStatus.SUCCESS if sp.is_global() else GluingStatus.MISSING_DATA,
            type_label=prop[:40],
            is_global=gs is not None,
        )

        return ProductComputationResult(
            product=product,
            semantic_product=semantic,
            section_product=sp,
            global_section=gs,
            residual_system=res_system,
            warnings=tuple(warnings),
        )

    # ==================================================================
    # 2. compare_judgments
    # ==================================================================

    @staticmethod
    def compare_judgments(
        left: JudgmentProduct,
        right: JudgmentProduct,
        mode: ComparisonMode = ComparisonMode.EQUIVALENCE,
    ) -> ComparisonMap:
        """Compute a :class:`ComparisonMap` between two judgment products.

        Delegates to :meth:`ComparisonMap.between_products`.

        Parameters
        ----------
        left:
            The source product.
        right:
            The target product.
        mode:
            The ``ComparisonMode`` to apply.

        Returns
        -------
        ComparisonMap
        """
        return ComparisonMap.between_products(left, right, mode=mode)

    @staticmethod
    def compare_and_certify(
        left: JudgmentProduct,
        right: JudgmentProduct,
    ) -> EquivalenceCertificate | RefinementWitness:
        """Compare two products and return the strongest available certificate.

        If the products are equivalent, returns an
        :class:`EquivalenceCertificate`.  If one refines the other,
        returns a :class:`RefinementWitness`.  Otherwise returns a
        ``RefinementWitness`` with ``trust_ordering_holds=False``.

        Parameters
        ----------
        left:
            The source product.
        right:
            The target product.

        Returns
        -------
        EquivalenceCertificate | RefinementWitness
        """
        map_ = JudgmentAlgorithms.compare_judgments(left, right)
        if map_.is_equivalence():
            try:
                return EquivalenceCertificate.from_comparison_map(map_, left, right)
            except ValueError:
                pass
        return RefinementWitness.from_comparison_map(map_, left, right)

    @staticmethod
    def batch_compare(
        products: Sequence[JudgmentProduct],
        mode: ComparisonMode = ComparisonMode.EQUIVALENCE,
    ) -> list[ComparisonMap]:
        """Compare every pair of products in *products*.

        Returns maps for all pairs (i, j) with i < j.

        Parameters
        ----------
        products:
            Products to compare pairwise.
        mode:
            Comparison mode.

        Returns
        -------
        list[ComparisonMap]
        """
        prods = list(products)
        results: list[ComparisonMap] = []
        for i, p in enumerate(prods):
            for q in prods[i + 1:]:
                results.append(
                    JudgmentAlgorithms.compare_judgments(p, q, mode=mode)
                )
        return results

    # ==================================================================
    # 3. discharge_residual
    # ==================================================================

    @staticmethod
    def discharge_residual(
        obligation: LiveResidualObligation,
        evidence: EvidenceBundle,
        tracker: ObligationTracker,
        strategies: Sequence[DischargeStrategy] | None = None,
    ) -> DischargeAttemptResult:
        """Attempt to discharge a single residual obligation.

        Parameters
        ----------
        obligation:
            The ``LiveResidualObligation`` to discharge.
        evidence:
            The ``EvidenceBundle`` to use for discharge.
        tracker:
            The ``ObligationTracker`` to update.
        strategies:
            Discharge strategies to try, in order.

        Returns
        -------
        DischargeAttemptResult
        """
        discharger = ResidualDischarger(tracker, strategies)
        result = discharger.attempt_discharge(obligation, evidence)

        # Update the tracker
        if result.succeeded:
            tracker.mark_discharged(obligation.obligation_id, result.notes)
        elif result.partial:
            tracker.transition(
                obligation.obligation_id,
                ObligationStatus.PARTIALLY_DISCHARGED,
                result.notes,
            )

        # Build a dummy product for status update
        dummy_product = JudgmentProduct(
            product_id=obligation.product_id or str(uuid.uuid4())[:12],
            status=ProductStatus.ASSEMBLED,
            proposition_label="",
        )
        updated_product = discharger.update_product_status(dummy_product)

        return DischargeAttemptResult(
            obligation_id=obligation.obligation_id,
            discharge_result=result,
            updated_product=updated_product,
            updated_tracker_summary=tracker.summary(),
        )

    @staticmethod
    def discharge_all_for_product(
        product: JudgmentProduct,
        evidence: EvidenceBundle,
        strategies: Sequence[DischargeStrategy] | None = None,
    ) -> tuple[JudgmentProduct, ObligationTracker]:
        """Attempt to discharge all obligations for *product*.

        Parameters
        ----------
        product:
            The product whose residuals to discharge.
        evidence:
            Available evidence bundle.
        strategies:
            Discharge strategies to try.

        Returns
        -------
        tuple[JudgmentProduct, ObligationTracker]
            The updated product and the final obligation tracker state.
        """
        system = ResidualSystem(strategies)
        system.ingest_product(product)
        updated = system.discharge_product(product, evidence)
        return updated, system.tracker

    # ==================================================================
    # 4. generate_explanation
    # ==================================================================

    @staticmethod
    def generate_explanation(
        product: JudgmentProduct,
        mode: ProjectionMode = ProjectionMode.DETAILED,
        scope: ExplanationScope = ExplanationScope.FULL,
    ) -> ExplanationProjection:
        """Project *product* to a structured explanation.

        Parameters
        ----------
        product:
            The product to explain.
        mode:
            Rendering mode.
        scope:
            Explanation scope.

        Returns
        -------
        ExplanationProjection
        """
        return ExplanationProjection.from_product(product, mode=mode, scope=scope)

    @staticmethod
    def generate_comparison_explanation(
        left: JudgmentProduct,
        right: JudgmentProduct,
        mode: ProjectionMode = ProjectionMode.DETAILED,
    ) -> ExplanationProjection:
        """Explain the comparison between *left* and *right*.

        Parameters
        ----------
        left:
            Source product.
        right:
            Target product.
        mode:
            Rendering mode.

        Returns
        -------
        ExplanationProjection
        """
        return ComparisonMaps.explain_comparison(left, right, mode=mode)

    @staticmethod
    def generate_residual_explanation(
        product: JudgmentProduct,
    ) -> ExplanationProjection:
        """Generate an explanation focused on open residuals.

        Parameters
        ----------
        product:
            The product to explain.

        Returns
        -------
        ExplanationProjection
        """
        return ExplanationProjection.from_product(
            product,
            mode=ProjectionMode.DETAILED,
            scope=ExplanationScope.RESIDUALS_ONLY,
        )

    # ==================================================================
    # Convenience: full pipeline
    # ==================================================================

    @staticmethod
    def full_pipeline(
        judgments: Sequence[Judgment],
        options: ProductComputationOptions | None = None,
        explain: bool = True,
        explanation_mode: ProjectionMode = ProjectionMode.DETAILED,
    ) -> tuple[ProductComputationResult, ExplanationProjection | None]:
        """Run the full judgment-product pipeline.

        Computes the product, tracks residuals, and optionally generates
        an explanation projection.

        Parameters
        ----------
        judgments:
            Input judgments.
        options:
            Computation options.
        explain:
            Whether to generate an explanation.
        explanation_mode:
            Mode for the explanation projection.

        Returns
        -------
        tuple[ProductComputationResult, ExplanationProjection | None]
        """
        result = JudgmentAlgorithms.compute_product(judgments, options)
        explanation: ExplanationProjection | None = None
        if explain:
            explanation = JudgmentAlgorithms.generate_explanation(
                result.product, mode=explanation_mode
            )
        return result, explanation


# ---------------------------------------------------------------------------
# __all__
# ---------------------------------------------------------------------------

__all__: list[str] = [
    # Options / results
    "ProductComputationOptions",
    "ProductComputationResult",
    "DischargeAttemptResult",
    # Main algorithm class
    "JudgmentAlgorithms",
    # Cross-referencing helpers
    "product_solver_verification",
    "product_evidence_collection",
]


# ---------------------------------------------------------------------------
# Cross-referencing helpers (Theory2.tex §5 — Judgments and Products)
# ---------------------------------------------------------------------------

import logging

_logger = logging.getLogger(__name__)


def product_solver_verification(product: Any, *, backend: str = "z3") -> dict[str, Any]:
    """Verify a judgment product through solver-backed descent checking.

    Uses ``jugeo.solver.z3_session`` for constraint solving and
    ``jugeo.geometry.descent`` for local-section descent strategies as
    described in Theory2.tex §5 (Judgments and Products).

    Parameters
    ----------
    product : Any
        A :class:`JudgmentProduct` (or compatible mapping) to verify.
    backend : str
        Solver backend identifier (default ``"z3"``).

    Returns
    -------
    dict[str, Any]
        ``{"verified": bool, "outcome": ..., "descent_strategy": ..., "backend": str}``
    """
    try:
        from jugeo.solver.z3_session import SolverResult, SolveOutcome  # noqa: F811
        from jugeo.geometry.descent import LocalSection, DescentStrategy  # noqa: F811
    except ImportError as exc:
        _logger.debug("Optional cross-reference imports unavailable: %s", exc)
        return {"verified": False, "outcome": None, "descent_strategy": None,
                "backend": backend, "error": str(exc)}

    product_id = getattr(product, "product_id", None) or str(product)
    strategy = DescentStrategy()
    section = LocalSection(source=product_id)
    result: SolverResult = SolverResult(backend=backend, query=product_id)
    outcome = result.check(section, strategy=strategy)
    verified = outcome == SolveOutcome.SAT
    _logger.debug("product_solver_verification: product=%s verified=%s", product_id, verified)
    return {"verified": verified, "outcome": outcome, "descent_strategy": strategy,
            "backend": backend}


def product_evidence_collection(product: Any) -> dict[str, Any]:
    """Collect an evidence manifest for a judgment product.

    Uses ``jugeo.evidence.manifests`` for manifest construction and
    ``jugeo.evidence.trust`` for trust-level algebra as described in
    Theory2.tex §5 (Judgments and Products).

    Parameters
    ----------
    product : Any
        A :class:`JudgmentProduct` (or compatible mapping) to collect evidence for.

    Returns
    -------
    dict[str, Any]
        ``{"manifest": ..., "trust_level": ..., "product_id": str}``
    """
    try:
        from jugeo.evidence.manifests import build_evidence_manifest  # noqa: F811
        from jugeo.evidence.trust import TrustLevel, TrustAlgebra  # noqa: F811
    except ImportError as exc:
        _logger.debug("Optional cross-reference imports unavailable: %s", exc)
        return {"manifest": None, "trust_level": None,
                "product_id": None, "error": str(exc)}

    product_id = getattr(product, "product_id", None) or str(product)
    manifest = build_evidence_manifest(product)
    algebra = TrustAlgebra()
    trust_level = algebra.compute(manifest) if manifest else TrustLevel.UNKNOWN
    _logger.debug("product_evidence_collection: product=%s trust=%s", product_id, trust_level)
    return {"manifest": manifest, "trust_level": trust_level, "product_id": product_id}


# copilot: core algorithms — judgment_products package.
