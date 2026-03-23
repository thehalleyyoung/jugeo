r"""Unified Problem Atlas for JuGeo — Theory2.tex Ch14.

copilot: problem atlas orchestration layer for the unified problem classification
and evidence routing system.

The ``problem_atlas`` package implements Chapter 14 of *Theory2.tex*: a comprehensive
catalog of computational and verification problem classes, their semantic signatures,
evidence requirements, and trust profiles.  Every problem encountered in jugeo is
mapped to a class in this atlas, enabling:

  §14.0  Package Manifest         — ModuleRecord registry and integrity checks
  §14.1  Problem Classes          — ProblemClass lattice with inheritance and subsumption
  §14.2  Semantic Signatures      — Input/output type schemas and contracts
  §14.3  Evidence Channels        — Channel registry and routing logic
  §14.4  Trust Requirements       — Trust algebra and sufficiency checks
  §14.5  Algorithms               — Atlas-aware classification and search algorithms
  §14.6  Integration              — Bridges to jugeo.evidence and jugeo.judgments
  §14.7  Theorems                 — Formal properties proved over the atlas

The atlas serves as the central registry connecting jugeo's judgment system,
evidence infrastructure, and solver orchestration.  It provides:

  - Canonical classification of all problem kinds across five categories:
    COMPUTATIONAL, VERIFICATION, CONSTRUCTIVE, ANALYTICAL, RELATIONAL
  - Semantic compatibility checking between problem specifications
  - Evidence channel routing based on problem class requirements
  - Trust budget management and gap analysis
  - Dependency resolution for multi-step verification pipelines

Architecture overview::

    AtlasCatalog (root)
      ├─ ProblemClass      — nodes in the classification lattice
      │     ├─ SemanticSignature  — typed I/O contract
      │     └─ EvidenceRequirement — channel + trust spec
      └─ TrustRequirement  — per-class trust budget

Usage::

    from jugeo.problem_modes.problem_atlas import AtlasCatalog, ProblemClass
    from jugeo.problem_modes.problem_atlas import ProblemCategory, EvidenceRequirement

    catalog = AtlasCatalog.default()
    pc = catalog.lookup_by_name("VERIFICATION")
    req = catalog.get_evidence_requirements(pc.class_id)
    gaps = catalog.compute_trust_gap(pc.class_id, available_trust=0.6)

    # Manifest inspection
    from jugeo.problem_modes.problem_atlas import get_manifest, validate_package_integrity
    manifest = get_manifest()
    report = validate_package_integrity()

See Also:
    theory2.tex §14.1–§14.7 for the mathematical foundations.
    jugeo.evidence.certificates for certificate integration.
    jugeo.judgments.judgment_terms for the 8-tuple judgment model.
    jugeo.problem_modes.problem_atlas.manifest for package integrity tools.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Version
# ---------------------------------------------------------------------------

__version__ = "0.1.0"
__author__ = "JuGeo Research"
__theory_ref__ = "theory2.tex Ch14: Unified Problem Atlas"

# ---------------------------------------------------------------------------
# §14.0  Manifest — package registry and integrity
# ---------------------------------------------------------------------------

try:
    from jugeo.problem_modes.problem_atlas.manifest import (
        CHAPTER,
        CHAPTER_NUM,
        MODULE_REGISTRY,
        PACKAGE_NAME,
        PROBLEM_CLASS_CATALOG,
        THEORY_REF,
        VERSION,
        ModuleKind,
        ModuleRecord,
        PackageManifest,
        get_manifest,
        get_problem_categories,
        get_problems_in_category,
        list_exports,
        resolve_module_dependencies,
        validate_package_integrity,
    )
except ImportError:  # pragma: no cover
    ModuleKind = None  # type: ignore[assignment,misc]
    ModuleRecord = None  # type: ignore[assignment,misc]
    PackageManifest = None  # type: ignore[assignment,misc]
    MODULE_REGISTRY = {}  # type: ignore[assignment]
    PROBLEM_CLASS_CATALOG = {}  # type: ignore[assignment]
    PACKAGE_NAME = "jugeo.problem_modes.problem_atlas"
    VERSION = "0.1.0"
    AUTHOR = "JuGeo Research"
    CHAPTER = "Theory2.tex Ch14: Unified Problem Atlas"
    THEORY_REF = "theory2.tex"
    CHAPTER_NUM = 14

    def get_manifest():  # type: ignore[misc]
        return None

    def list_exports():  # type: ignore[misc]
        return {}

    def validate_package_integrity():  # type: ignore[misc]
        return {}

    def get_problem_categories():  # type: ignore[misc]
        return []

    def get_problems_in_category(category):  # type: ignore[misc]
        return []

    def resolve_module_dependencies(module_name):  # type: ignore[misc]
        return []

# ---------------------------------------------------------------------------
# §14.1  Problem Classes — ProblemClass lattice
# ---------------------------------------------------------------------------

try:
    from jugeo.problem_modes.problem_atlas.problem_classes import (
        ProblemCategory,
        ProblemClass,
        ProblemClassLattice,
        ProblemKind,
        SubsumptionRelation,
        build_default_lattice,
        get_all_problem_kinds,
        lookup_problem_class,
    )
except ImportError:  # pragma: no cover
    ProblemCategory = None  # type: ignore[assignment,misc]
    ProblemClass = None  # type: ignore[assignment,misc]
    ProblemClassLattice = None  # type: ignore[assignment,misc]
    ProblemKind = None  # type: ignore[assignment,misc]
    SubsumptionRelation = None  # type: ignore[assignment,misc]

    def build_default_lattice():  # type: ignore[misc]
        return None

    def get_all_problem_kinds():  # type: ignore[misc]
        return []

    def lookup_problem_class(name):  # type: ignore[misc]
        return None

# ---------------------------------------------------------------------------
# §14.2  Semantic Signatures — typed I/O contracts
# ---------------------------------------------------------------------------

try:
    from jugeo.problem_modes.problem_atlas.semantic_signatures import (
        IOSchema,
        SemanticCompatibility,
        SemanticContract,
        SemanticSignature,
        SignatureKind,
        check_signature_compatibility,
        infer_signature,
    )
except ImportError:  # pragma: no cover
    IOSchema = None  # type: ignore[assignment,misc]
    SemanticCompatibility = None  # type: ignore[assignment,misc]
    SemanticContract = None  # type: ignore[assignment,misc]
    SemanticSignature = None  # type: ignore[assignment,misc]
    SignatureKind = None  # type: ignore[assignment,misc]

    def check_signature_compatibility(a, b):  # type: ignore[misc]
        return False

    def infer_signature(problem_class):  # type: ignore[misc]
        return None

# ---------------------------------------------------------------------------
# §14.3  Evidence Channels — channel registry and routing
# ---------------------------------------------------------------------------

try:
    from jugeo.problem_modes.problem_atlas.evidence_channels import (
        ChannelBinding,
        ChannelRoute,
        EvidenceRequirement,
        RequirementStrength,
        RoutePolicy,
        build_channel_route,
        get_required_channels,
        route_evidence,
    )
except ImportError:  # pragma: no cover
    ChannelBinding = None  # type: ignore[assignment,misc]
    ChannelRoute = None  # type: ignore[assignment,misc]
    EvidenceRequirement = None  # type: ignore[assignment,misc]
    RequirementStrength = None  # type: ignore[assignment,misc]
    RoutePolicy = None  # type: ignore[assignment,misc]

    def build_channel_route(problem_class):  # type: ignore[misc]
        return None

    def get_required_channels(problem_class):  # type: ignore[misc]
        return []

    def route_evidence(evidence, problem_class):  # type: ignore[misc]
        return None

# ---------------------------------------------------------------------------
# §14.4  Trust Requirements — trust algebra and sufficiency
# ---------------------------------------------------------------------------

try:
    from jugeo.problem_modes.problem_atlas.trust_requirements import (
        TrustBudget,
        TrustGap,
        TrustRequirement,
        TrustSufficiencyResult,
        TrustThreshold,
        check_trust_sufficiency,
        compute_trust_gap,
        get_trust_requirement,
    )
except ImportError:  # pragma: no cover
    TrustBudget = None  # type: ignore[assignment,misc]
    TrustGap = None  # type: ignore[assignment,misc]
    TrustRequirement = None  # type: ignore[assignment,misc]
    TrustSufficiencyResult = None  # type: ignore[assignment,misc]
    TrustThreshold = None  # type: ignore[assignment,misc]

    def check_trust_sufficiency(req, available):  # type: ignore[misc]
        return None

    def compute_trust_gap(req, available):  # type: ignore[misc]
        return None

    def get_trust_requirement(problem_class):  # type: ignore[misc]
        return None

# ---------------------------------------------------------------------------
# §14.5–§14.7  Algorithms, Integration, Theorems
# ---------------------------------------------------------------------------

try:
    from jugeo.problem_modes.problem_atlas.algorithms import (
        AtlasSearchResult,
        ClassificationAlgorithm,
        classify_problem,
        find_covering_class,
        rank_problem_classes,
    )
except ImportError:  # pragma: no cover
    AtlasSearchResult = None  # type: ignore[assignment,misc]
    ClassificationAlgorithm = None  # type: ignore[assignment,misc]

    def classify_problem(spec):  # type: ignore[misc]
        return None

    def find_covering_class(spec):  # type: ignore[misc]
        return None

    def rank_problem_classes(spec):  # type: ignore[misc]
        return []

try:
    from jugeo.problem_modes.problem_atlas.integration import (
        AtlasIntegrationBridge,
        CertificateBinding,
        JudgmentBinding,
        bind_certificate_to_class,
        bind_judgment_to_class,
        create_integration_bridge,
    )
except ImportError:  # pragma: no cover
    AtlasIntegrationBridge = None  # type: ignore[assignment,misc]
    CertificateBinding = None  # type: ignore[assignment,misc]
    JudgmentBinding = None  # type: ignore[assignment,misc]

    def bind_certificate_to_class(cert, pc):  # type: ignore[misc]
        return None

    def bind_judgment_to_class(jt, pc):  # type: ignore[misc]
        return None

    def create_integration_bridge():  # type: ignore[misc]
        return None

try:
    from jugeo.problem_modes.problem_atlas.theorems import (
        AtlasTheorem,
        TheoremRegistry,
        TheoremStatus,
        get_theorem,
        list_theorems,
    )
except ImportError:  # pragma: no cover
    AtlasTheorem = None  # type: ignore[assignment,misc]
    TheoremRegistry = None  # type: ignore[assignment,misc]
    TheoremStatus = None  # type: ignore[assignment,misc]

    def get_theorem(name):  # type: ignore[misc]
        return None

    def list_theorems():  # type: ignore[misc]
        return []

# ---------------------------------------------------------------------------
# §14.0  Top-level AtlasCatalog convenience class
# ---------------------------------------------------------------------------

try:
    from jugeo.problem_modes.problem_atlas.models import AtlasCatalog
except ImportError:  # pragma: no cover
    AtlasCatalog = None  # type: ignore[assignment,misc]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    # manifest
    "CHAPTER",
    "CHAPTER_NUM",
    "MODULE_REGISTRY",
    "PACKAGE_NAME",
    "PROBLEM_CLASS_CATALOG",
    "THEORY_REF",
    "VERSION",
    "ModuleKind",
    "ModuleRecord",
    "PackageManifest",
    "get_manifest",
    "get_problem_categories",
    "get_problems_in_category",
    "list_exports",
    "resolve_module_dependencies",
    "validate_package_integrity",
    # problem_classes
    "ProblemCategory",
    "ProblemClass",
    "ProblemClassLattice",
    "ProblemKind",
    "SubsumptionRelation",
    "build_default_lattice",
    "get_all_problem_kinds",
    "lookup_problem_class",
    # semantic_signatures
    "IOSchema",
    "SemanticCompatibility",
    "SemanticContract",
    "SemanticSignature",
    "SignatureKind",
    "check_signature_compatibility",
    "infer_signature",
    # evidence_channels
    "ChannelBinding",
    "ChannelRoute",
    "EvidenceRequirement",
    "RequirementStrength",
    "RoutePolicy",
    "build_channel_route",
    "get_required_channels",
    "route_evidence",
    # trust_requirements
    "TrustBudget",
    "TrustGap",
    "TrustRequirement",
    "TrustSufficiencyResult",
    "TrustThreshold",
    "check_trust_sufficiency",
    "compute_trust_gap",
    "get_trust_requirement",
    # algorithms
    "AtlasSearchResult",
    "ClassificationAlgorithm",
    "classify_problem",
    "find_covering_class",
    "rank_problem_classes",
    # integration
    "AtlasIntegrationBridge",
    "CertificateBinding",
    "JudgmentBinding",
    "bind_certificate_to_class",
    "bind_judgment_to_class",
    "create_integration_bridge",
    # theorems
    "AtlasTheorem",
    "TheoremRegistry",
    "TheoremStatus",
    "get_theorem",
    "list_theorems",
    # models
    "AtlasCatalog",
]

# ---------------------------------------------------------------------------
# Package-level convenience helpers
# ---------------------------------------------------------------------------

def get_default_catalog() -> "AtlasCatalog":  # type: ignore[return]
    """Return the default AtlasCatalog populated with all standard problem classes.

    This is the primary entry point for consumers of the atlas.  The returned
    catalog contains all ten standard problem classes (SEARCH, OPTIMIZATION,
    DECISION, COUNTING, CONSTRUCTION, VERIFICATION, INFERENCE, SYNTHESIS,
    REPAIR, CLASSIFICATION) with their semantic signatures and evidence
    requirements pre-populated.

    Returns:
        AtlasCatalog: The default catalog instance.

    Raises:
        ImportError: If the models submodule is unavailable.

    Example::

        catalog = get_default_catalog()
        pc = catalog.lookup_by_name("VERIFICATION")
        print(pc.complexity_notes)
    """
    try:
        from jugeo.problem_modes.problem_atlas.models import AtlasCatalog as _AC
        return _AC.default()
    except (ImportError, AttributeError):
        raise ImportError(  # noqa: B904
            "AtlasCatalog is not available; ensure jugeo.problem_modes.problem_atlas.models is installed."
        )


def quick_lookup(problem_description: str) -> "dict[str, object]":
    """Quickly look up a problem class by natural-language description.

    Tokenises *problem_description*, scores each class in the default catalog
    by keyword overlap, and returns a summary dict for the best match.

    Args:
        problem_description: Free-text description of the problem to classify
            (e.g. ``"find the shortest path in a graph"``).

    Returns:
        dict with keys ``class_id``, ``name``, ``category``, ``confidence``,
        ``required_evidence_kinds``.  Returns an empty dict when no catalog
        entry can be loaded.

    Example::

        result = quick_lookup("verify that a function satisfies its contract")
        print(result["name"])   # => "VERIFICATION"
    """
    try:
        from jugeo.problem_modes.problem_atlas.algorithms import (
            atlas_lookup_algorithm,
            LookupStrategy,
        )
        from jugeo.problem_modes.problem_atlas.models import AtlasCatalog
        catalog = AtlasCatalog.default()
        result = atlas_lookup_algorithm(
            problem_description, catalog, strategy=LookupStrategy.FUZZY
        )
        pc = catalog.lookup_by_name(result.matched_class or "") if result.matched_class else None
        if pc is None:
            return {}
        return {
            "class_id": pc.class_id,
            "name": pc.name,
            "category": pc.category.value,
            "confidence": result.confidence,
            "required_evidence_kinds": list(pc.required_evidence_kinds),
        }
    except Exception:  # noqa: BLE001
        return {}


def check_evidence_sufficiency(
    problem_name: str,
    evidence_map: "dict[str, float]",
) -> "dict[str, object]":
    """Check whether the supplied evidence satisfies the requirements for a problem class.

    Args:
        problem_name: Canonical class name, e.g. ``"VERIFICATION"``.
        evidence_map: Mapping from channel identifier to trust score (0.0–1.0),
            e.g. ``{"FORMAL_PROOF": 0.95, "TYPE_CHECKING": 0.80}``.

    Returns:
        dict with keys ``satisfied`` (bool), ``aggregate_trust`` (float),
        ``missing_channels`` (list[str]), ``gaps`` (list[dict]).
        Returns ``{"error": "..."}`` if the class cannot be resolved.

    Raises:
        Nothing — all exceptions are caught and surfaced as ``{"error": ...}``.

    Example::

        result = check_evidence_sufficiency(
            "VERIFICATION", {"FORMAL_PROOF": 0.95, "TYPE_CHECKING": 0.80}
        )
        print(result["satisfied"])   # => True
    """
    try:
        from jugeo.problem_modes.problem_atlas.models import AtlasCatalog
        from jugeo.problem_modes.problem_atlas.trust_requirements import (
            RequirementChecker,
        )
        catalog = AtlasCatalog.default()
        pc = catalog.lookup_by_name(problem_name)
        if pc is None:
            return {"error": f"Unknown problem class: {problem_name!r}"}
        req = catalog.get_evidence_requirements(pc.class_id)
        if req is None:
            return {"error": f"No requirements registered for {problem_name!r}"}
        checker = RequirementChecker()
        result = checker.check(req, evidence_map)
        return {
            "satisfied": result.status.is_acceptable(),
            "aggregate_trust": result.aggregate_trust,
            "missing_channels": list(result.missing_channels),
            "gaps": [g.to_dict() for g in result.gaps],
        }
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


def atlas_summary() -> "dict[str, object]":
    """Return a high-level summary of the loaded atlas.

    Queries the default catalog and theorem registry to build a snapshot of
    the currently loaded atlas state.

    Returns:
        dict with keys:
          ``catalog_size``    — number of registered problem classes,
          ``categories``      — list of category names,
          ``theorem_count``   — number of registered theorems,
          ``proved_theorems`` — names of theorems with PROVED/VERIFIED status,
          ``version``         — atlas package version string.

    Example::

        summary = atlas_summary()
        print(summary["catalog_size"])   # => 10
    """
    result: dict[str, object] = {"version": __version__}
    try:
        from jugeo.problem_modes.problem_atlas.models import AtlasCatalog
        catalog = AtlasCatalog.default()
        result["catalog_size"] = len(catalog.entries)
        result["categories"] = list(catalog.category_index.keys())
    except Exception:  # noqa: BLE001
        result["catalog_size"] = 0
        result["categories"] = []
    try:
        from jugeo.problem_modes.problem_atlas.theorems import (
            get_default_registry,
            list_proved_theorems,
        )
        reg = get_default_registry()
        result["theorem_count"] = reg.count()
        result["proved_theorems"] = [t.name for t in list_proved_theorems()]
    except Exception:  # noqa: BLE001
        result["theorem_count"] = 0
        result["proved_theorems"] = []
    return result


# ---------------------------------------------------------------------------
# Expose convenience helpers in __all__
# ---------------------------------------------------------------------------

__all__ += [
    "get_default_catalog",
    "quick_lookup",
    "check_evidence_sufficiency",
    "atlas_summary",
    # cross-subsystem integration
    "atlas_over_site",
    "evidence_sufficiency_check",
    "orchestration_routing",
]


# ---------------------------------------------------------------------------
# Cross-subsystem integration helpers
# ---------------------------------------------------------------------------


def atlas_over_site(
    *,
    site_name: str = "default",
) -> "dict[str, object]":
    """Organize problem classes over the judgment site.

    For each coordinate in the :class:`~jugeo.geometry.site.Site`, determines
    which :class:`ProblemClass` entries from the atlas catalog apply, creating
    a coordinate → problem-class mapping that mirrors the site topology.

    Parameters
    ----------
    site_name : str, optional
        Identifier for the site to load (default: ``"default"``).

    Returns
    -------
    dict[str, object]
        Keys: ``site`` (the :class:`~jugeo.geometry.site.Site` instance or
        ``None``), ``mapping`` (dict from coordinate id → list of
        :class:`ProblemClass`), ``catalog`` (the :class:`AtlasCatalog` used),
        ``coverage`` (float — fraction of coordinates with at least one class).

    Raises
    ------
    NotImplementedError
        If ``jugeo.geometry.site`` is not available.

    See Also
    --------
    jugeo.geometry.site.Site : The semantic site structure.
    jugeo.geometry.site.Coordinate : Site coordinate type.
    """
    try:
        from jugeo.geometry.site import Site, SiteBuilder
    except ImportError:
        raise NotImplementedError(
            "atlas_over_site requires jugeo.geometry.site to be installed."
        )

    site = None
    mapping: dict[str, list] = {}
    catalog = None
    coverage = 0.0

    try:
        builder = SiteBuilder()
        site = builder.build(name=site_name)
        catalog = get_default_catalog()
        coordinates = getattr(site, "coordinates", [])
        covered = 0
        for coord in coordinates:
            coord_id = str(getattr(coord, "id", coord))
            coord_kind = str(getattr(coord, "kind", ""))
            matched: list = []
            try:
                for entry in catalog.entries.values() if hasattr(catalog.entries, "values") else catalog.entries:
                    pc = entry if not hasattr(entry, "problem_class") else entry.problem_class
                    pc_name = str(getattr(pc, "name", "")).lower()
                    if coord_kind.lower() in pc_name or pc_name in coord_kind.lower():
                        matched.append(pc)
            except Exception:  # noqa: BLE001
                pass
            mapping[coord_id] = matched
            if matched:
                covered += 1
        if coordinates:
            coverage = covered / len(coordinates)
    except Exception:  # noqa: BLE001
        pass

    return {
        "site": site,
        "mapping": mapping,
        "catalog": catalog,
        "coverage": coverage,
    }


def evidence_sufficiency_check(
    problem_name: str,
    evidence_items: "list[dict[str, object]]",
) -> "dict[str, object]":
    """Check evidence sufficiency per problem class using the evidence subsystem.

    Bridges the atlas to :mod:`jugeo.evidence` by evaluating whether the
    supplied evidence items meet the requirements for the given problem class.

    Parameters
    ----------
    problem_name : str
        Canonical class name (e.g. ``"VERIFICATION"``).
    evidence_items : list[dict[str, object]]
        List of evidence dictionaries, each containing at least ``"kind"``
        and ``"trust"`` keys.

    Returns
    -------
    dict[str, object]
        Keys: ``sufficient`` (bool), ``trust_scores`` (dict of channel →
        trust score), ``missing`` (list of missing evidence kinds),
        ``manifest_builder`` (a :class:`~jugeo.evidence.manifests.ManifestBuilder`
        or ``None``).

    Raises
    ------
    NotImplementedError
        If ``jugeo.evidence`` sub-modules are not available.

    See Also
    --------
    jugeo.evidence.manifests.ManifestBuilder : Evidence manifest builder.
    jugeo.evidence.trust.TrustAlgebra : Trust computation.
    """
    try:
        from jugeo.evidence.manifests import ManifestBuilder
    except ImportError:
        raise NotImplementedError(
            "evidence_sufficiency_check requires jugeo.evidence.manifests to be installed."
        )
    try:
        from jugeo.evidence.trust import TrustAlgebra
    except ImportError:
        raise NotImplementedError(
            "evidence_sufficiency_check requires jugeo.evidence.trust to be installed."
        )

    trust_scores: dict[str, float] = {}
    missing: list[str] = []
    sufficient = False
    manifest_builder = None

    try:
        manifest_builder = ManifestBuilder()
        algebra = TrustAlgebra()

        for item in evidence_items:
            kind = str(item.get("kind", "unknown"))
            trust = float(item.get("trust", 0.0))
            trust_scores[kind] = max(trust_scores.get(kind, 0.0), trust)

        atlas_result = check_evidence_sufficiency(problem_name, trust_scores)
        sufficient = bool(atlas_result.get("satisfied", False))
        missing = list(atlas_result.get("missing_channels", []))
    except Exception:  # noqa: BLE001
        pass

    return {
        "sufficient": sufficient,
        "trust_scores": trust_scores,
        "missing": missing,
        "manifest_builder": manifest_builder,
    }


def orchestration_routing(
    problem_name: str,
    *,
    context: "dict[str, object] | None" = None,
) -> "dict[str, object]":
    """Route a problem class to the appropriate orchestration strategy.

    Bridges the atlas to :mod:`jugeo.orchestration`, selecting the right
    orchestration controller or strategy based on the problem's classification.

    Parameters
    ----------
    problem_name : str
        Canonical class name (e.g. ``"VERIFICATION"``).
    context : dict[str, object] | None, optional
        Additional routing context (budget, constraints, preferences).

    Returns
    -------
    dict[str, object]
        Keys: ``strategy`` (the selected strategy object or ``None``),
        ``controller`` (the :mod:`jugeo.orchestration.controller` instance
        or ``None``), ``problem_class`` (the resolved :class:`ProblemClass`
        or ``None``), ``route_info`` (str describing the routing decision).

    Raises
    ------
    NotImplementedError
        If ``jugeo.orchestration`` is not available.

    See Also
    --------
    jugeo.orchestration : Orchestration infrastructure package.
    """
    try:
        import jugeo.orchestration as orch_pkg
    except ImportError:
        raise NotImplementedError(
            "orchestration_routing requires jugeo.orchestration to be installed."
        )

    strategy = None
    controller = None
    problem_class = None
    route_info = "routing not performed"

    try:
        pc = lookup_problem_class(problem_name) if callable(lookup_problem_class) else None  # type: ignore[truthy-function]
        problem_class = pc

        controller_mod = getattr(orch_pkg, "controller", None)
        if controller_mod is not None:
            controller_cls = getattr(controller_mod, "OrchestrationController", None)
            if controller_cls is not None:
                controller = controller_cls()

        route_fn = getattr(orch_pkg, "route", None) or getattr(orch_pkg, "select_strategy", None)
        if route_fn is not None:
            strategy = route_fn(problem_name, context=context or {})
            route_info = f"routed {problem_name!r} via {route_fn.__name__}"
        elif controller is not None:
            select = getattr(controller, "select_strategy", None)
            if select is not None:
                strategy = select(problem_name, **(context or {}))
                route_info = f"routed {problem_name!r} via controller.select_strategy"
            else:
                route_info = f"controller available but no select_strategy method for {problem_name!r}"
        else:
            route_info = f"orchestration package available; no routing function found for {problem_name!r}"
    except Exception as exc:  # noqa: BLE001
        route_info = f"routing failed: {exc}"

    return {
        "strategy": strategy,
        "controller": controller,
        "problem_class": problem_class,
        "route_info": route_info,
    }


# copilot: shared-core marker for future LLM orchestration.


# --- auto-registered submodules ---
try:
    from . import algorithms
except Exception:
    pass
try:
    from . import evidence_channels
except Exception:
    pass
try:
    from . import generated_code_governance
except Exception:
    pass
try:
    from . import integration
except Exception:
    pass
try:
    from . import manifest
except Exception:
    pass
try:
    from . import migration_and_donor_inheritance
except Exception:
    pass
try:
    from . import models
except Exception:
    pass
try:
    from . import performance_obligations
except Exception:
    pass
try:
    from . import problem_classes
except Exception:
    pass
try:
    from . import repair_and_program_transformation
except Exception:
    pass
try:
    from . import semantic_signatures
except Exception:
    pass
try:
    from . import specification_satisfaction
except Exception:
    pass
try:
    from . import theorems
except Exception:
    pass
try:
    from . import trust_requirements
except Exception:
    pass
