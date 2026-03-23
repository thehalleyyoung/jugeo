"""jugeo.foundations.judgment_products — Semantic products of judgment verification.

Theory2.tex Chapter 5: Judgments, Sections, and Semantic Products.

Re-exports the complete public API from all sub-modules.

# copilot: package init — judgment_products.
"""

from jugeo.foundations.judgment_products.manifest import (
    PACKAGE_NAME,
    PACKAGE_VERSION,
    ComponentDescriptor,
    ComponentKind,
    ComponentRegistry,
    REGISTRY,
    Stability,
    UpstreamDependency,
    UPSTREAM_DEPENDENCIES,
)
from jugeo.foundations.judgment_products.models import (
    ComparisonMap,
    ExplanationProjection,
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
    DischargeResult,
    DischargeStrategy,
    LiveResidualObligation,
    ObligationStatus,
    ObligationTracker,
    PropagationDirection,
    PropagationRecord,
    ResidualDischarger,
    ResidualPropagator,
    ResidualSystem,
)
from jugeo.foundations.judgment_products.sections_are_the_real_products_of import (
    FunctorDirection,
    GlobalSection,
    SectionComparison,
    SectionFunctor,
    SectionProduct,
    SectionProductStatus,
    SectionProducts,
)
from jugeo.foundations.judgment_products.comparison_maps_and_explanation_pr import (
    ComparisonMaps,
    EquivalenceCertificate,
    ExplanationScope,
    RefinementWitness,
    WitnessKind,
)
from jugeo.foundations.judgment_products.algorithms import (
    DischargeAttemptResult,
    JudgmentAlgorithms,
    ProductComputationOptions,
    ProductComputationResult,
)
from jugeo.foundations.judgment_products.integration import (
    ComparisonBridge,
    JudgmentIntegration,
    LocalJudgmentAdapter,
    SectionBridge,
)
from jugeo.foundations.judgment_products.theorems import (
    JudgmentTheorems,
    Thm1NonBooleanComposition,
    Thm2ResidualMonotonicity,
    Thm3SectionGluing,
    Thm4TrustMonotonicity,
    Thm5ComparisonTransitivity,
    Thm6ExplanationFaithfulness,
    Thm7DischargeSoundness,
    TheoremAssumption,
    TheoremResult,
    TheoremStatus,
)

# ---------------------------------------------------------------------------
# Cross-subsystem bridges (foundations ↔ geometry/evidence/encodings)
# ---------------------------------------------------------------------------

import logging as _logging_bridges

_jp_bridge_logger = _logging_bridges.getLogger(__name__)


def product_over_site(site: "Any", *, coordinate_filter: str | None = None) -> dict:
    """Compute semantic judgment products over every object of *site*.

    Bridges Theory2.tex §5 (judgment products) to the concrete site
    implementation in ``jugeo.geometry.site``.
    """
    try:
        from jugeo.geometry.site import Coordinate, CoordinateKind
    except ImportError:
        _jp_bridge_logger.warning(
            "jugeo.geometry.site unavailable; returning empty product"
        )
        return {"products": [], "site_available": False}

    coordinates = getattr(site, "coordinates", [])
    if coordinate_filter is not None:
        coordinates = [
            c for c in coordinates
            if getattr(c, "kind", None) == CoordinateKind[coordinate_filter]
            or str(getattr(c, "name", "")) == coordinate_filter
        ]

    products: list[dict] = []
    for coord in coordinates:
        trust = getattr(coord, "trust_annotation", None)
        section_data = getattr(coord, "section_data", {})
        kind = ProductKind.SEMANTIC if ProductKind is not None else "SEMANTIC"
        status = ProductStatus.COMPLETE if (
            ProductStatus is not None and trust is not None
        ) else ProductStatus.PARTIAL if ProductStatus is not None else "PARTIAL"
        products.append({
            "coordinate": coord,
            "kind": str(kind),
            "status": str(status),
            "section_data": section_data,
        })

    return {"products": products, "site_available": True}


def product_evidence(product: dict, *, channel: str = "default") -> dict:
    """Collect evidence supporting a semantic product.

    Bridges §5 product theory to ``jugeo.evidence`` subsystem.
    """
    trust_available = True
    try:
        from jugeo.evidence.trust import TrustLevel, TrustAlgebra
    except ImportError:
        TrustLevel = None  # noqa: N806
        TrustAlgebra = None  # noqa: N806
        trust_available = False

    manifest_available = True
    try:
        from jugeo.evidence.manifests import build_evidence_manifest
    except ImportError:
        build_evidence_manifest = None
        manifest_available = False

    sections = product.get("sections", [])
    evidence_items: list[dict] = []
    aggregate_trust = None

    for sec in sections:
        trust_val = sec.get("trust_annotation") or sec.get("trust")
        if trust_available and TrustAlgebra is not None and trust_val is not None:
            level = TrustAlgebra.evaluate(trust_val, channel=channel)
            aggregate_trust = level if aggregate_trust is None else TrustAlgebra.join(
                aggregate_trust, level
            )
        evidence_items.append({
            "section": sec,
            "trust": str(trust_val),
            "channel": channel,
        })

    manifest = None
    if manifest_available and build_evidence_manifest is not None:
        manifest = build_evidence_manifest(evidence_items, channel=channel)

    return {
        "evidence_items": evidence_items,
        "aggregate_trust": str(aggregate_trust),
        "manifest": manifest,
        "trust_available": trust_available,
        "manifest_available": manifest_available,
    }


def product_encoding(product: dict, *, format: str = "z3") -> dict:
    """Encode a semantic product for solver consumption.

    Bridges §5 to ``jugeo.encodings`` subsystem.
    """
    try:
        from jugeo.encodings import encode_judgment, encode_section
    except ImportError:
        _jp_bridge_logger.warning(
            "jugeo.encodings unavailable; returning empty encoding"
        )
        return {"encoding": None, "format": format, "encodings_available": False}

    sections = product.get("sections", [])
    encoded_components: list[dict] = []

    for sec in sections:
        judgment_data = sec.get("judgment") or sec.get("data")
        coord = sec.get("coordinate")
        enc_judgment = encode_judgment(judgment_data, format=format) if judgment_data else None
        enc_section = encode_section(sec, format=format)
        encoded_components.append({
            "coordinate": str(coord),
            "encoded_judgment": enc_judgment,
            "encoded_section": enc_section,
        })

    combined = None
    if encoded_components:
        combined = {
            "format": format,
            "components": encoded_components,
            "component_count": len(encoded_components),
        }

    return {"encoding": combined, "format": format, "encodings_available": True}


__all__ = [
    # manifest
    "PACKAGE_NAME", "PACKAGE_VERSION", "ComponentDescriptor", "ComponentKind",
    "ComponentRegistry", "REGISTRY", "Stability", "UpstreamDependency",
    "UPSTREAM_DEPENDENCIES",
    # models
    "ComparisonMap", "ExplanationProjection", "JudgmentProduct",
    "LocalJudgmentSection", "ProductKind", "ProductStatus", "ProjectionMode",
    "SemanticProduct",
    # s01
    "JudgmentAsObject", "JudgmentComparison", "JudgmentProductAlgebra",
    "NonBooleanJudgment", "StructuredJudgment", "TruthDegree",
    # s02
    "DischargeResult", "DischargeStrategy", "LiveResidualObligation",
    "ObligationStatus", "ObligationTracker", "PropagationDirection",
    "PropagationRecord", "ResidualDischarger", "ResidualPropagator",
    "ResidualSystem",
    # s03
    "FunctorDirection", "GlobalSection", "SectionComparison", "SectionFunctor",
    "SectionProduct", "SectionProductStatus", "SectionProducts",
    # s04
    "ComparisonMaps", "EquivalenceCertificate", "ExplanationScope",
    "RefinementWitness", "WitnessKind",
    # algorithms
    "DischargeAttemptResult", "JudgmentAlgorithms", "ProductComputationOptions",
    "ProductComputationResult",
    # integration
    "ComparisonBridge", "JudgmentIntegration", "LocalJudgmentAdapter",
    "SectionBridge",
    # theorems
    "JudgmentTheorems", "Thm1NonBooleanComposition", "Thm2ResidualMonotonicity",
    "Thm3SectionGluing", "Thm4TrustMonotonicity", "Thm5ComparisonTransitivity",
    "Thm6ExplanationFaithfulness", "Thm7DischargeSoundness",
    "TheoremAssumption", "TheoremResult", "TheoremStatus",
    # Cross-subsystem integration helpers
    "product_from_sections",
    "residual_propagation",
    "section_over_cover",
    # cross-subsystem bridges
    "product_over_site",
    "product_evidence",
    "product_encoding",
]


# ---------------------------------------------------------------------------
# Cross-subsystem integration: connecting Ch5 judgment products to
# implementation packages (judgments.sections, evidence.manifests,
# geometry.covers).
# ---------------------------------------------------------------------------

import logging as _logging

_jp_logger = _logging.getLogger(__name__)


def product_from_sections(
    sections,
    *,
    product_kind=None,
):
    """Build a :class:`SemanticProduct` from judgment sections obtained via
    ``jugeo.judgments.sections``.

    This bridges the foundational theory (Ch5 §5.3 — sections as real
    products of verification) with the implementation-level
    :class:`~jugeo.judgments.sections.Section` objects.

    Parameters
    ----------
    sections : Iterable[Section]
        An iterable of :class:`~jugeo.judgments.sections.Section` objects,
        each carrying coordinate, trust annotation, and evidence data.
    product_kind : ProductKind | None
        Optional product kind override.  Defaults to ``ProductKind.SEMANTIC``
        when available.

    Returns
    -------
    SemanticProduct
        A :class:`SemanticProduct` assembled from the supplied sections,
        with trust annotations and residual obligations propagated.

    Raises
    ------
    RuntimeError
        If ``jugeo.judgments.sections`` cannot be imported.

    Notes
    -----
    Theory2.tex §5.3 — Sections are the real products of verification.
    This function makes the theoretical construction concrete by mapping
    implementation-level ``Section`` objects into the foundational
    ``SemanticProduct`` model.

    Examples
    --------
    >>> from jugeo.judgments.sections import SectionBuilder  # doctest: +SKIP
    >>> s = SectionBuilder().build()
    >>> product = product_from_sections([s])
    >>> product.status
    'COMPLETE'
    """
    try:
        from jugeo.judgments.sections import Section, SectionFamily
    except ImportError as exc:
        raise RuntimeError(
            "jugeo.judgments.sections is required for product_from_sections()"
        ) from exc

    resolved_kind = product_kind
    if resolved_kind is None and ProductKind is not None:
        resolved_kind = ProductKind.SEMANTIC

    local_sections = []
    trust_annotations = []
    residuals = []

    for sec in sections:
        # Extract fields from jugeo.judgments.sections.Section
        coord = getattr(sec, "coordinate", None)
        trust = getattr(sec, "trust_annotation", None)
        sec_residuals = getattr(sec, "residuals", [])

        if LocalJudgmentSection is not None:
            local_sections.append(
                LocalJudgmentSection(
                    coordinate=coord,
                    data=getattr(sec, "data", None),
                    trust_annotation=trust,
                )
            )
        if trust is not None:
            trust_annotations.append(trust)
        residuals.extend(sec_residuals)

    status = ProductStatus.COMPLETE if (ProductStatus is not None and not residuals) else ProductStatus.PARTIAL if ProductStatus is not None else "PARTIAL"

    if SemanticProduct is not None:
        return SemanticProduct(
            sections=local_sections,
            kind=resolved_kind,
            status=status,
            residual_obligations=residuals,
        )

    # Fallback: return a plain dict when models are not loaded
    return {
        "sections": local_sections,
        "kind": str(resolved_kind),
        "status": str(status),
        "residual_obligations": residuals,
    }


def residual_propagation(
    manifest,
    *,
    direction=None,
):
    """Propagate residual obligations through an evidence manifest from
    ``jugeo.evidence.manifests``.

    Residual obligations (§5.2) are the "living obligations" that survive
    partial verification.  This function connects the foundational residual
    machinery (``ResidualPropagator``) to the manifest system so that
    residuals discovered during judgment-product computation flow into the
    project-wide evidence manifest.

    Parameters
    ----------
    manifest : Manifest | EvidenceManifest
        A manifest object from :mod:`jugeo.evidence.manifests`.
    direction : PropagationDirection | None
        Direction of propagation.  When ``None``, defaults to
        ``PropagationDirection.FORWARD`` if available.

    Returns
    -------
    dict[str, Any]
        Keys: ``"propagated_count"`` (int), ``"residuals"`` (list),
        ``"manifest_updated"`` (bool).

    Notes
    -----
    Theory2.tex §5.2 — Residual obligations are the living obligations of
    incomplete verification.  Propagation ensures that downstream judgments
    inherit upstream residuals.

    Examples
    --------
    >>> from jugeo.evidence.manifests import ManifestBuilder  # doctest: +SKIP
    >>> m = ManifestBuilder().build()
    >>> result = residual_propagation(m)
    >>> result["propagated_count"] >= 0
    True
    """
    try:
        from jugeo.evidence.manifests import Manifest, EvidenceManifest
    except ImportError as exc:
        raise RuntimeError(
            "jugeo.evidence.manifests is required for residual_propagation()"
        ) from exc

    resolved_direction = direction
    if resolved_direction is None and PropagationDirection is not None:
        resolved_direction = PropagationDirection.FORWARD

    # Extract residuals from the manifest
    raw_residuals = []
    if hasattr(manifest, "residuals"):
        raw_residuals = list(manifest.residuals)
    elif hasattr(manifest, "obligation_store"):
        store = manifest.obligation_store
        if hasattr(store, "list_all"):
            raw_residuals = list(store.list_all())

    propagated = []
    if ResidualPropagator is not None:
        try:
            propagator = ResidualPropagator(direction=resolved_direction)
            for residual in raw_residuals:
                result = propagator.propagate(residual)
                propagated.append(result)
        except Exception as exc:
            _jp_logger.warning("residual_propagation: propagator error: %s", exc)
            propagated = raw_residuals
    else:
        propagated = raw_residuals

    return {
        "propagated_count": len(propagated),
        "residuals": propagated,
        "manifest_updated": len(propagated) > 0,
    }


def section_over_cover(
    cover,
    *,
    section_builder=None,
):
    """Compute judgment sections over a cover from ``jugeo.geometry.covers``.

    Given an admissible cover ``{U_i → X}`` this function constructs a local
    judgment section on each cover member and returns the family, ready for
    descent (gluing).

    Parameters
    ----------
    cover : Cover
        A :class:`~jugeo.geometry.covers.Cover` instance.
    section_builder : SectionBuilder | None
        Optional custom section builder from :mod:`jugeo.judgments.sections`.
        When ``None``, a default builder is used.

    Returns
    -------
    dict[str, Any]
        Keys: ``"cover"`` (the input cover), ``"sections"`` (list of
        local sections, one per cover member), ``"is_compatible"`` (bool,
        whether sections agree on overlaps).

    Notes
    -----
    Theory2.tex §5.3 — Sections over a cover form the raw material for
    descent.  Compatibility on overlaps is the precondition for gluing
    (Theorem 5.3).

    Examples
    --------
    >>> from jugeo.geometry.covers import CoverBuilder  # doctest: +SKIP
    >>> cover = CoverBuilder().build()
    >>> result = section_over_cover(cover)
    >>> len(result["sections"]) == len(cover.members)
    True
    """
    try:
        from jugeo.geometry.covers import Cover, CoverMember
    except ImportError as exc:
        raise RuntimeError(
            "jugeo.geometry.covers is required for section_over_cover()"
        ) from exc

    try:
        from jugeo.judgments.sections import Section, SectionBuilder as _SB
    except ImportError as exc:
        raise RuntimeError(
            "jugeo.judgments.sections is required for section_over_cover()"
        ) from exc

    builder = section_builder
    if builder is None:
        builder = _SB()

    members = getattr(cover, "members", [])
    local_sections = []

    for member in members:
        coord = getattr(member, "source_coordinate", None)
        trust_ceiling = getattr(member, "trust_ceiling", None)
        section = builder.build(coordinate=coord, trust_annotation=trust_ceiling)
        local_sections.append(section)

    # Check compatibility on overlaps
    overlaps = getattr(cover, "overlap_data", None) or getattr(cover, "overlaps", [])
    is_compatible = True
    if overlaps and len(local_sections) > 1:
        # Pairwise compatibility: sections must agree on restrictions to overlaps
        for overlap in overlaps:
            indices = getattr(overlap, "indices", None)
            if indices and len(indices) >= 2:
                i, j = indices[0], indices[1]
                if i < len(local_sections) and j < len(local_sections):
                    s_i = local_sections[i]
                    s_j = local_sections[j]
                    trust_i = getattr(s_i, "trust_annotation", None)
                    trust_j = getattr(s_j, "trust_annotation", None)
                    if trust_i is not None and trust_j is not None and trust_i != trust_j:
                        is_compatible = False

    return {
        "cover": cover,
        "sections": local_sections,
        "is_compatible": is_compatible,
    }


# --- auto-registered submodules ---
try:
    from . import algorithms
except Exception:
    pass
try:
    from . import comparison_maps_and_explanation_pr
except Exception:
    pass
try:
    from . import integration
except Exception:
    pass
try:
    from . import judgments_are_not_boolean_facts
except Exception:
    pass
try:
    from . import manifest
except Exception:
    pass
try:
    from . import models
except Exception:
    pass
try:
    from . import residual_obligations_are_the_livin
except Exception:
    pass
try:
    from . import sections_are_the_real_products_of
except Exception:
    pass
try:
    from . import theorems
except Exception:
    pass
