"""jugeo.foundations.project_hypercovers — Theory2.tex Chapter 8.

Overview
--------
This package implements the full machinery of **Theory2.tex Chapter 8:
Projects, modules, hypercovers, and fleets**.  Chapter 8 develops the
categorical and sheaf-theoretic foundations required for structured
decomposition of software projects into verifiable sub-units, and for the
coordination of LLM agent fleets over those decompositions.

Mathematical setting
--------------------
The central objects of Chapter 8 are:

* A *project site* (C, J) — a small category C whose objects are semantic
  coordinates (modules, functions, interfaces, tests) equipped with a
  Grothendieck topology J that declares which families of modules are
  "admissible covers."

* A *module cover* — a finite family {U_i → X} of coordinate maps whose
  union spans every semantic point of the project site.  Covers are graded
  by a quality metric (CoverMetric) and checked against admissibility axioms.

* A *fleet* — an assignment of LLM agents to cover elements, together with
  a trust aggregation rule and a load-balance policy.  Fleet coverage
  (Thm 8.4) guarantees that every semantic point receives at least one
  agent assignment; trust monotonicity (Thm 8.5) ensures agent trust levels
  cannot self-promote.

* A *hypercover decomposition* — an iterated sequence of covers
  (U_0 → U_1 → …) whose colimit resolves all descent obstructions.  When
  the hypercover is contractible, the descent spectral sequence degenerates
  (Lem 8.4) and every local section glues uniquely (Thm 8.6).

Chapter 8 structure
-------------------
§8.1  Project Sites
    Defines a project site as a Grothendieck site (C, J) whose objects are
    the semantic coordinates of a project's modules and whose covering sieves
    are the admissible families of modules.

    Key constructions:

    * ``ProjectSite`` — the assembled category-with-topology for a project.
    * ``SemanticSiteBuilder`` — fluent API for constructing a ProjectSite
      from a list of module paths or a dependency graph.
    * ``CoordinateRegistry`` — fast hierarchical lookup of named coordinates.
    * ``TopologyGenerator`` — derives a Grothendieck topology from overlap
      data.
    * ``ProjectSiteInspector`` — diagnostic reports and axiom validation.

    Module: ``project_sites.py``  (theory2.tex §8.1)

§8.2  Module Covers
    Studies admissible covers of a project site — families of ModuleCover
    objects whose union spans the entire site.  Introduces the Čech nerve
    construction (Def 8.7) and proves the existence of admissible covers
    (Thm 8.2) and contractibility of the nerve (Thm 8.3).

    Key constructions:

    * ``ModuleCover`` — a single cover element pairing a set of coordinates
      with a trust-annotated evidence handle.
    * ``CoverBuilder`` — incremental cover construction with overlap tracking.
    * ``AdmissibilityChecker`` — verifies covering axioms (locality, descent,
      base-change stability).
    * ``CechNerveComputer`` — builds the simplicial complex of pairwise
      overlaps, enabling cohomological obstruction theory.
    * ``CoverRefiner`` — splits or merges cover elements to repair violations.

    Module: ``module_covers.py``  (theory2.tex §8.2)

§8.3  Fleet Structure
    Formalises a *fleet* of LLM agents assigned to the elements of a module
    cover, together with a trust aggregation rule and a load balancer.
    Chapter 8 §8.3 proves fleet coverage (Thm 8.4), trust monotonicity
    (Thm 8.5), and gives an optimal load-balance algorithm (Lem 8.3).

    Key constructions:

    * ``FleetMember`` — a single agent assigned to one cover element, with
      a trust level, task queue, and descent result record.
    * ``FleetCoordinator`` — manages the full fleet: routing tasks, collecting
      results, detecting stalls, and escalating failures.
    * ``TrustAggregator`` — combines per-member trust profiles into a
      project-level trust certificate using the trust ceiling rule.
    * ``LoadBalancer`` — re-assigns cover elements to balance agent workload
      subject to the load-balance optimality criterion (Lem 8.3).
    * ``FleetMonitor`` — real-time status tracking and alerting.
    * ``FleetPlanner`` — pre-computes the optimal fleet assignment given a
      cover and a set of agent profiles.

    Module: ``fleet_structure.py``  (theory2.tex §8.3)

§8.4  Hypercover Refinement and Descent
    When a fleet encounters descent obstructions — local sections that do not
    glue across overlaps — the cover must be refined into a *hypercover*:
    a sequence (U_0 → U_1 → …) whose colimit resolves all obstructions.
    This section proves the hypercover descent theorem (Thm 8.6) and gives
    the obstruction vanishing criterion (Thm 8.7).

    Key constructions:

    * ``HypercoverDecomposition`` — the full iterated cover record together
      with per-level descent results and obstruction annotations.
    * ``HypercoverBuilder`` — builds the initial hypercover from a raw cover.
    * ``RefinementEngine`` — drives the iterative refinement loop (Def 8.15),
      terminating when no further obstructions are detected.
    * ``ObstructionAnalyzer`` — identifies and classifies descent obstructions
      at each simplicial level using the CechNerve complex.
    * ``DescentCoordinator`` — wraps ``DescentEngine`` and orchestrates the
      gluing of local sections across overlap regions.

    Module: ``hypercover_refinement.py``  (theory2.tex §8.4)

Supporting modules
------------------
* ``manifest.py``    — Package metadata and integrity verification (§8.0).
* ``models.py``      — Domain model dataclasses spanning all four sections.
* ``algorithms.py``  — Cross-cutting algorithmic routines.
* ``integration.py`` — Glue to the judgment and evidence sub-systems.
* ``theorems.py``    — Formal theorem statements and proof-step records.

Usage examples
--------------
**Importing the full Ch8 API in one line:**

    >>> import jugeo.foundations.project_hypercovers as ph
    >>> ph.__version__
    '0.1.0'
    >>> ph.__theory_chapter__
    8

**Checking package health:**

    >>> from jugeo.foundations.project_hypercovers import verify_imports
    >>> status = verify_imports()
    >>> isinstance(status, dict)
    True

**Inspecting the chapter overview:**

    >>> from jugeo.foundations.project_hypercovers import get_chapter_overview
    >>> ov = get_chapter_overview()
    >>> len(ov["sections"])
    4
    >>> ov["sections"][0]["section_id"]
    '8.1'

**Reading the manifest:**

    >>> from jugeo.foundations.project_hypercovers import get_manifest
    >>> m = get_manifest()
    >>> m.count_exports() >= 50
    True

Package metadata
----------------
    __version__         = "0.1.0"
    __author__          = "JuGeo Theory Team"
    __theory_chapter__  = 8

copilot: shared-core marker — project_hypercovers exposes the full Ch8 machinery for LLM-assisted verification workflows.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Package-level metadata variables
# ---------------------------------------------------------------------------

__version__: str = "0.1.0"
__author__: str = "JuGeo Theory Team"
__theory_chapter__: int = 8

# ---------------------------------------------------------------------------
# §8.0 — Manifest
#
# The manifest module is the single source of truth for package metadata:
# exported symbols, theory section coverage, dependency records, and
# integrity hashing.  It does not depend on any other sub-module.
# ---------------------------------------------------------------------------

try:
    from jugeo.foundations.project_hypercovers.manifest import (
        PackageManifest,
        ModuleDescription,
        ModuleStatus,
        ExportKind,
        DependencyKind,
        SectionStatus,
        TheorySection,
        DependencyRecord,
        MODULE_REGISTRY,
        get_manifest,
        list_exports,
        validate_package_integrity,
        PACKAGE_NAME,
        VERSION,
        AUTHOR,
        CHAPTER,
        SECTION_START,
        SECTION_END,
    )
except ImportError:  # pragma: no cover
    PackageManifest = None  # type: ignore[assignment,misc]
    ModuleDescription = None  # type: ignore[assignment,misc]
    ModuleStatus = None  # type: ignore[assignment,misc]
    ExportKind = None  # type: ignore[assignment,misc]
    DependencyKind = None  # type: ignore[assignment,misc]
    SectionStatus = None  # type: ignore[assignment,misc]
    TheorySection = None  # type: ignore[assignment,misc]
    DependencyRecord = None  # type: ignore[assignment,misc]
    MODULE_REGISTRY = None  # type: ignore[assignment,misc]
    get_manifest = None  # type: ignore[assignment,misc]
    list_exports = None  # type: ignore[assignment,misc]
    validate_package_integrity = None  # type: ignore[assignment,misc]
    PACKAGE_NAME = "jugeo.foundations.project_hypercovers"  # type: ignore[misc]
    VERSION = "0.1.0"  # type: ignore[misc]
    AUTHOR = "JuGeo Theory Team"  # type: ignore[misc]
    CHAPTER = 8  # type: ignore[misc]
    SECTION_START = 1  # type: ignore[misc]
    SECTION_END = 4  # type: ignore[misc]

# ---------------------------------------------------------------------------
# §8.1–§8.4 — Core domain models
#
# models.py provides the foundational dataclasses used throughout all four
# sections of Chapter 8.  Every other sub-module in this package either
# constructs, consumes, or transforms these domain objects.
#
#   ProjectSite           — the assembled Grothendieck site for a project.
#   ModuleCover           — a single admissible cover element (theory2.tex §8.2).
#   FleetMember           — one LLM agent and its cover assignment (§8.3).
#   HypercoverDecomposition — iterated refinement record (§8.4).
#   ProjectKind           — enum: LIBRARY, APPLICATION, SERVICE, FRAMEWORK, …
#   CoverStrategy         — enum: GREEDY, OPTIMAL, HIERARCHICAL, …
#   FleetStatus           — enum: IDLE, ACTIVE, PAUSED, FAILED, COMPLETED, …
#   DecompositionStatus   — enum: PENDING, IN_PROGRESS, COMPLETE, FAILED, …
# ---------------------------------------------------------------------------

try:
    from jugeo.foundations.project_hypercovers.models import (
        ProjectSite,
        ModuleCover,
        FleetMember,
        HypercoverDecomposition,
        ProjectKind,
        CoverStrategy,
        FleetStatus,
        DecompositionStatus,
    )
except ImportError:  # pragma: no cover
    ProjectSite = None  # type: ignore[assignment,misc]
    ModuleCover = None  # type: ignore[assignment,misc]
    FleetMember = None  # type: ignore[assignment,misc]
    HypercoverDecomposition = None  # type: ignore[assignment,misc]
    ProjectKind = None  # type: ignore[assignment,misc]
    CoverStrategy = None  # type: ignore[assignment,misc]
    FleetStatus = None  # type: ignore[assignment,misc]
    DecompositionStatus = None  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# §8.1 — Project site construction
#
# project_sites.py provides the builders and inspectors for constructing
# a ProjectSite from raw module-path lists, dependency graphs, or
# pre-computed coordinate registries.
#
#   SemanticSiteBuilder    — fluent builder for ProjectSite instances.
#   CoordinateRegistry     — fast hierarchical coordinate lookup.
#   TopologyGenerator      — derives a Grothendieck topology from overlap data.
#   ProjectSiteInspector   — axiom validation and diagnostic reports.
#   build_project_site     — convenience function: paths → ProjectSite.
#   compute_site_morphisms — derives all site morphisms from a ProjectSite.
#   site_from_modules      — constructs a site from a list of module names.
# ---------------------------------------------------------------------------

try:
    from jugeo.foundations.project_hypercovers.project_sites import (
        SemanticSiteBuilder,
        CoordinateRegistry,
        TopologyGenerator,
        ProjectSiteInspector,
        build_project_site,
        compute_site_morphisms,
        site_from_modules,
    )
except ImportError:  # pragma: no cover
    SemanticSiteBuilder = None  # type: ignore[assignment,misc]
    CoordinateRegistry = None  # type: ignore[assignment,misc]
    TopologyGenerator = None  # type: ignore[assignment,misc]
    ProjectSiteInspector = None  # type: ignore[assignment,misc]
    build_project_site = None  # type: ignore[assignment,misc]
    compute_site_morphisms = None  # type: ignore[assignment,misc]
    site_from_modules = None  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# §8.2 — Module covers
#
# module_covers.py provides the machinery for constructing, validating,
# scoring, and refining admissible covers of a project site.  The module also
# implements the Čech nerve computation needed for obstruction theory.
#
#   CoverBuilder                — incremental cover construction.
#   OverlapComputer             — computes pairwise / triple overlaps.
#   AdmissibilityChecker        — verifies locality, descent, base-change axioms.
#   CoverRefiner                — splits/merges elements to repair violations.
#   CechNerveComputer           — builds the simplicial complex of overlaps.
#   build_module_cover          — convenience: site → ModuleCover.
#   refine_cover_until_admissible — iterates refinement until admissible.
#   score_cover_quality         — returns a CoverMetric for a given cover.
# ---------------------------------------------------------------------------

try:
    from jugeo.foundations.project_hypercovers.module_covers import (
        CoverBuilder,
        OverlapComputer,
        AdmissibilityChecker,
        CoverRefiner,
        CechNerveComputer,
        build_module_cover,
        refine_cover_until_admissible,
        score_cover_quality,
    )
except ImportError:  # pragma: no cover
    CoverBuilder = None  # type: ignore[assignment,misc]
    OverlapComputer = None  # type: ignore[assignment,misc]
    AdmissibilityChecker = None  # type: ignore[assignment,misc]
    CoverRefiner = None  # type: ignore[assignment,misc]
    CechNerveComputer = None  # type: ignore[assignment,misc]
    build_module_cover = None  # type: ignore[assignment,misc]
    refine_cover_until_admissible = None  # type: ignore[assignment,misc]
    score_cover_quality = None  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# §8.3 — Fleet structure
#
# fleet_structure.py provides all fleet-related constructions: assembly,
# assignment, load balancing, trust aggregation, monitoring, and planning.
#
#   FleetCoordinator      — manages the full fleet lifecycle.
#   LoadBalancer          — re-assigns cover elements to balance workload.
#   TrustAggregator       — combines per-member trust into a project cert.
#   FleetMonitor          — real-time status tracking and alerting.
#   FleetPlanner          — pre-computes optimal fleet assignment.
#   assemble_fleet        — convenience: cover + agent count → fleet.
#   assign_fleet_to_cover — assigns an existing fleet to a new cover.
#   compute_fleet_trust   — returns the aggregate trust level for a fleet.
# ---------------------------------------------------------------------------

try:
    from jugeo.foundations.project_hypercovers.fleet_structure import (
        FleetCoordinator,
        LoadBalancer,
        TrustAggregator,
        FleetMonitor,
        FleetPlanner,
        assemble_fleet,
        assign_fleet_to_cover,
        compute_fleet_trust,
    )
except ImportError:  # pragma: no cover
    FleetCoordinator = None  # type: ignore[assignment,misc]
    LoadBalancer = None  # type: ignore[assignment,misc]
    TrustAggregator = None  # type: ignore[assignment,misc]
    FleetMonitor = None  # type: ignore[assignment,misc]
    FleetPlanner = None  # type: ignore[assignment,misc]
    assemble_fleet = None  # type: ignore[assignment,misc]
    assign_fleet_to_cover = None  # type: ignore[assignment,misc]
    compute_fleet_trust = None  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# §8.4 — Hypercover refinement
#
# hypercover_refinement.py provides the iterative refinement engine that
# resolves descent obstructions by building a hypercover.  The key theorem
# (Thm 8.6) states that when the hypercover is contractible every local
# section glues uniquely.
#
#   HypercoverBuilder           — initial hypercover from a raw cover.
#   SimplicialStructureValidator — checks simplicial identities at each level.
#   RefinementEngine            — drives the iterative refinement loop.
#   ObstructionAnalyzer         — classifies descent obstructions per level.
#   DescentCoordinator          — orchestrates gluing of local sections.
#   build_hypercover            — convenience: cover → HypercoverDecomposition.
#   refine_hypercover           — refines an existing hypercover one step.
#   compute_descent_obstruction — returns the obstruction class at a level.
# ---------------------------------------------------------------------------

try:
    from jugeo.foundations.project_hypercovers.hypercover_refinement import (
        HypercoverBuilder,
        SimplicialStructureValidator,
        RefinementEngine,
        ObstructionAnalyzer,
        DescentCoordinator,
        build_hypercover,
        refine_hypercover,
        compute_descent_obstruction,
    )
except ImportError:  # pragma: no cover
    HypercoverBuilder = None  # type: ignore[assignment,misc]
    SimplicialStructureValidator = None  # type: ignore[assignment,misc]
    RefinementEngine = None  # type: ignore[assignment,misc]
    ObstructionAnalyzer = None  # type: ignore[assignment,misc]
    DescentCoordinator = None  # type: ignore[assignment,misc]
    build_hypercover = None  # type: ignore[assignment,misc]
    refine_hypercover = None  # type: ignore[assignment,misc]
    compute_descent_obstruction = None  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Cross-cutting algorithms
#
# algorithms.py provides standalone algorithmic routines that draw on the
# constructions from §8.1–§8.4 but do not belong to any single section.
#
#   greedy_cover_algorithm        — greedy maximal cover heuristic.
#   optimal_fleet_assignment      — ILP-based optimal fleet assignment.
#   hypercover_descent_algorithm  — full descent pipeline (cover → certificate).
#   cech_complex_computation      — standalone Čech complex builder.
#   obstruction_repair_algorithm  — automated obstruction repair routine.
#   iterative_refinement_loop     — generic refinement loop with callbacks.
#   trust_propagation_algorithm   — propagates trust through the cover graph.
# ---------------------------------------------------------------------------

try:
    from jugeo.foundations.project_hypercovers.algorithms import (
        greedy_cover_algorithm,
        optimal_fleet_assignment,
        hypercover_descent_algorithm,
        cech_complex_computation,
        obstruction_repair_algorithm,
        iterative_refinement_loop,
        trust_propagation_algorithm,
    )
except ImportError:  # pragma: no cover
    greedy_cover_algorithm = None  # type: ignore[assignment,misc]
    optimal_fleet_assignment = None  # type: ignore[assignment,misc]
    hypercover_descent_algorithm = None  # type: ignore[assignment,misc]
    cech_complex_computation = None  # type: ignore[assignment,misc]
    obstruction_repair_algorithm = None  # type: ignore[assignment,misc]
    iterative_refinement_loop = None  # type: ignore[assignment,misc]
    trust_propagation_algorithm = None  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Integration layer
#
# integration.py wires the Ch8 machinery to the rest of the jugeo system:
# it registers project sites in the global judgment context, connects fleets
# to judgment obligations, and provides import/export adapters so that
# external tools can consume and produce project hypercover records.
#
#   ProjectHypercoverIntegration    — top-level integration facade.
#   ProjectHypercoverExporter       — serialises a decomposition to JSON/YAML.
#   ProjectHypercoverImporter       — deserialises an external record.
#   register_project_site           — registers a site in the global context.
#   connect_fleet_to_judgment_system — links fleet tasks to JudgmentTerm objects.
# ---------------------------------------------------------------------------

try:
    from jugeo.foundations.project_hypercovers.integration import (
        ProjectHypercoverIntegration,
        ProjectHypercoverExporter,
        ProjectHypercoverImporter,
        register_project_site,
        connect_fleet_to_judgment_system,
    )
except ImportError:  # pragma: no cover
    ProjectHypercoverIntegration = None  # type: ignore[assignment,misc]
    ProjectHypercoverExporter = None  # type: ignore[assignment,misc]
    ProjectHypercoverImporter = None  # type: ignore[assignment,misc]
    register_project_site = None  # type: ignore[assignment,misc]
    connect_fleet_to_judgment_system = None  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Theorem records
#
# theorems.py provides the formal theorem statements for all Ch8 results as
# first-class Python objects, enabling programmatic verification workflows.
#
#   TheoremRecord                  — a single theorem statement + proof steps.
#   TheoremRegistry                — registry of all Ch8 theorem records.
#   ProofVerifier                  — checks proof steps for well-formedness.
#   theorem_hypercover_descent     — Thm 8.6 record (hypercover descent).
#   theorem_fleet_coverage         — Thm 8.4 record (fleet coverage).
#   theorem_module_decomposition   — Thm 8.8 record (module decomposition).
#   theorem_cech_nerve_contractible — Thm 8.3 record (Čech nerve contractible).
#   VerificationStatus             — enum: UNVERIFIED, PARTIAL, VERIFIED, …
#   ProofStep                      — a single step in a formal proof trace.
# ---------------------------------------------------------------------------

try:
    from jugeo.foundations.project_hypercovers.theorems import (
        TheoremRecord,
        TheoremRegistry,
        ProofVerifier,
        theorem_hypercover_descent,
        theorem_fleet_coverage,
        theorem_module_decomposition,
        theorem_cech_nerve_contractible,
        VerificationStatus,
        ProofStep,
    )
except ImportError:  # pragma: no cover
    TheoremRecord = None  # type: ignore[assignment,misc]
    TheoremRegistry = None  # type: ignore[assignment,misc]
    ProofVerifier = None  # type: ignore[assignment,misc]
    theorem_hypercover_descent = None  # type: ignore[assignment,misc]
    theorem_fleet_coverage = None  # type: ignore[assignment,misc]
    theorem_module_decomposition = None  # type: ignore[assignment,misc]
    theorem_cech_nerve_contractible = None  # type: ignore[assignment,misc]
    VerificationStatus = None  # type: ignore[assignment,misc]
    ProofStep = None  # type: ignore[assignment,misc]


# ---------------------------------------------------------------------------
# __all__
# ---------------------------------------------------------------------------

__all__: list[str] = [
    # -----------------------------------------------------------------------
    # Package metadata
    # -----------------------------------------------------------------------
    "__version__",
    "__author__",
    "__theory_chapter__",
    # -----------------------------------------------------------------------
    # Package-level helpers (defined in this file)
    # -----------------------------------------------------------------------
    "package_summary",
    "get_chapter_overview",
    "verify_imports",
    # -----------------------------------------------------------------------
    # §8.0  manifest — metadata, integrity, coverage
    # -----------------------------------------------------------------------
    "PackageManifest",       # Top-level manifest dataclass
    "ModuleDescription",     # Per-module metadata record
    "ModuleStatus",          # Enum: STABLE | EXPERIMENTAL | DEPRECATED | SCAFFOLD
    "ExportKind",            # Enum: CLASS | FUNCTION | CONSTANT | ENUM | …
    "DependencyKind",        # Enum: INTERNAL | EXTERNAL | OPTIONAL
    "SectionStatus",         # Enum: COMPLETE | PARTIAL | STUB
    "TheorySection",         # Per-section metadata record
    "DependencyRecord",      # Directed dependency record
    "MODULE_REGISTRY",       # dict[str, str] mapping module name → description
    "get_manifest",          # () → PackageManifest
    "list_exports",          # () → list[str]
    "validate_package_integrity",  # () → dict[str, bool]
    "PACKAGE_NAME",          # "jugeo.foundations.project_hypercovers"
    "VERSION",               # "0.1.0"
    "AUTHOR",                # "JuGeo Theory Team"
    "CHAPTER",               # 8
    "SECTION_START",         # 1
    "SECTION_END",           # 4
    # -----------------------------------------------------------------------
    # §8.1–§8.4  models — core domain objects
    # -----------------------------------------------------------------------
    "ProjectSite",            # Grothendieck site for a software project (§8.1)
    "ModuleCover",            # Single admissible cover element (§8.2)
    "FleetMember",            # One agent and its cover assignment (§8.3)
    "HypercoverDecomposition", # Iterated cover refinement record (§8.4)
    "ProjectKind",            # Enum: LIBRARY | APPLICATION | SERVICE | …
    "CoverStrategy",          # Enum: GREEDY | OPTIMAL | HIERARCHICAL | …
    "FleetStatus",            # Enum: IDLE | ACTIVE | PAUSED | FAILED | …
    "DecompositionStatus",    # Enum: PENDING | IN_PROGRESS | COMPLETE | …
    # -----------------------------------------------------------------------
    # §8.1  project_sites — site construction and inspection
    # -----------------------------------------------------------------------
    "SemanticSiteBuilder",    # Fluent ProjectSite builder (Def 8.1)
    "CoordinateRegistry",     # Hierarchical coordinate lookup (Def 8.2)
    "TopologyGenerator",      # Derives topology from overlap data (Def 8.3)
    "ProjectSiteInspector",   # Axiom validation and diagnostics
    "build_project_site",     # paths → ProjectSite
    "compute_site_morphisms", # ProjectSite → list[SiteMorphism]
    "site_from_modules",      # module names → ProjectSite
    # -----------------------------------------------------------------------
    # §8.2  module_covers — cover construction and Čech nerve
    # -----------------------------------------------------------------------
    "CoverBuilder",                  # Incremental cover construction
    "OverlapComputer",               # Pairwise/triple overlap computation
    "AdmissibilityChecker",          # Covering axiom verifier (Def 8.6)
    "CoverRefiner",                  # Element splitter/merger
    "CechNerveComputer",             # Simplicial complex of overlaps (Def 8.7)
    "build_module_cover",            # site → ModuleCover
    "refine_cover_until_admissible", # iterative admissibility repair
    "score_cover_quality",           # ModuleCover → CoverMetric
    # -----------------------------------------------------------------------
    # §8.3  fleet_structure — fleet coordination and trust
    # -----------------------------------------------------------------------
    "FleetCoordinator",      # Full fleet lifecycle manager (Def 8.10)
    "LoadBalancer",          # Cover element re-assignment (Lem 8.3)
    "TrustAggregator",       # Trust aggregation rule (Def 8.11, Thm 8.5)
    "FleetMonitor",          # Real-time status tracking
    "FleetPlanner",          # Pre-computes optimal assignment
    "assemble_fleet",        # cover + agent_count → fleet
    "assign_fleet_to_cover", # fleet + new_cover → updated fleet
    "compute_fleet_trust",   # fleet → aggregate TrustLevel
    # -----------------------------------------------------------------------
    # §8.4  hypercover_refinement — iterative refinement and descent
    # -----------------------------------------------------------------------
    "HypercoverBuilder",           # Initial hypercover from cover (Def 8.13)
    "SimplicialStructureValidator", # Checks simplicial identities
    "RefinementEngine",            # Iterative refinement loop (Def 8.15)
    "ObstructionAnalyzer",         # Per-level obstruction classification
    "DescentCoordinator",          # Gluing orchestration (Thm 8.6)
    "build_hypercover",            # cover → HypercoverDecomposition
    "refine_hypercover",           # HypercoverDecomposition → refined
    "compute_descent_obstruction", # decomposition + level → obstruction class
    # -----------------------------------------------------------------------
    # algorithms — cross-cutting routines
    # -----------------------------------------------------------------------
    "greedy_cover_algorithm",       # Greedy maximal cover heuristic
    "optimal_fleet_assignment",     # ILP optimal fleet assignment
    "hypercover_descent_algorithm", # Full descent pipeline
    "cech_complex_computation",     # Standalone Čech complex builder
    "obstruction_repair_algorithm", # Automated obstruction repair
    "iterative_refinement_loop",    # Generic refinement loop
    "trust_propagation_algorithm",  # Trust propagation through cover graph
    # -----------------------------------------------------------------------
    # integration — judgment and evidence system glue
    # -----------------------------------------------------------------------
    "ProjectHypercoverIntegration",   # Top-level integration facade
    "ProjectHypercoverExporter",      # Serialises decomposition to JSON/YAML
    "ProjectHypercoverImporter",      # Deserialises external record
    "register_project_site",          # Registers site in global context
    "connect_fleet_to_judgment_system",  # Links fleet → JudgmentTerm objects
    # -----------------------------------------------------------------------
    # theorems — formal Ch8 theorem records
    # -----------------------------------------------------------------------
    "TheoremRecord",                  # Single theorem + proof steps
    "TheoremRegistry",                # Registry of all Ch8 theorems
    "ProofVerifier",                  # Proof step well-formedness checker
    "theorem_hypercover_descent",     # Thm 8.6 record
    "theorem_fleet_coverage",         # Thm 8.4 record
    "theorem_module_decomposition",   # Thm 8.8 record
    "theorem_cech_nerve_contractible", # Thm 8.3 record
    "VerificationStatus",             # Enum: UNVERIFIED | PARTIAL | VERIFIED | …
    "ProofStep",                      # Single step in a formal proof trace
]


# ---------------------------------------------------------------------------
# Package-level utility functions
# ---------------------------------------------------------------------------


def package_summary() -> dict[str, object]:
    """Return a structured summary dictionary for this package.

    Returns
    -------
    dict[str, object]
        Dictionary with the following keys:

        name : str
            Fully-qualified package name.
        version : str
            Semantic version string.
        chapter : int
            Theory2.tex chapter number implemented by this package (8).
        chapter_title : str
            Human-readable chapter title from Theory2.tex.
        section_range : tuple[int, int]
            ``(first_section, last_section)`` within chapter 8.
        theory_file : str
            Name of the theory LaTeX source file.
        author : str
            Package author string.
        export_count : int
            Total number of symbols in ``__all__``.
        submodules : list[str]
            Bare names of the 9 sub-modules in this package.

    Notes
    -----
    Theory2.tex §8.0 — package-level summary for LLM orchestration.

    Examples
    --------
    >>> info = package_summary()
    >>> info["chapter"]
    8
    >>> info["name"]
    'jugeo.foundations.project_hypercovers'
    >>> len(info["submodules"]) == 9
    True
    """
    return {
        "name": "jugeo.foundations.project_hypercovers",
        "version": __version__,
        "chapter": __theory_chapter__,
        "chapter_title": "Projects, modules, hypercovers, and fleets",
        "section_range": (1, 4),
        "theory_file": "theory2.tex",
        "author": __author__,
        "export_count": len(__all__),
        "submodules": [
            "manifest",
            "models",
            "project_sites",
            "module_covers",
            "fleet_structure",
            "hypercover_refinement",
            "algorithms",
            "integration",
            "theorems",
        ],
    }


def get_chapter_overview() -> dict[str, object]:
    """Return a structured overview of Theory2.tex Chapter 8.

    Returns
    -------
    dict[str, object]
        Dictionary with the following keys:

        chapter : int
            Integer chapter number (8).
        title : str
            Chapter title string.
        theory_file : str
            ``"theory2.tex"``.
        mathematical_context : str
            Brief prose description of the mathematical setting.
        sections : list[dict]
            List of section overview dicts, one per §8.x section.  Each
            contains ``section_id``, ``title``, ``description``,
            ``key_classes``, ``key_functions``, and ``key_theorems``.
        key_constructions : list[str]
            Names of the most important classes and functions in the package.

    Notes
    -----
    Theory2.tex §8.0 — chapter-level overview for LLM orchestration.

    Examples
    --------
    >>> ov = get_chapter_overview()
    >>> ov["chapter"]
    8
    >>> len(ov["sections"])
    4
    >>> ov["sections"][3]["section_id"]
    '8.4'
    """
    return {
        "chapter": 8,
        "title": "Projects, modules, hypercovers, and fleets",
        "theory_file": "theory2.tex",
        "mathematical_context": (
            "Chapter 8 develops the topos-theoretic foundations for structured "
            "decomposition of software projects.  A project is modelled as a "
            "Grothendieck site (§8.1), whose admissible module covers (§8.2) "
            "are assigned to fleets of LLM agents (§8.3).  When local sections "
            "fail to glue across overlaps, the cover is iteratively refined "
            "into a hypercover (§8.4) whose descent spectral sequence "
            "degenerates (Lem 8.4), resolving all obstructions by Thm 8.6."
        ),
        "sections": [
            {
                "section_id": "8.1",
                "title": "Project Sites",
                "description": (
                    "Defines ProjectSite as a Grothendieck site (C, J) over "
                    "the category of semantic coordinates.  The covering "
                    "topology is derived from module overlap data.  Key "
                    "constructions: SemanticSiteBuilder, CoordinateRegistry, "
                    "TopologyGenerator, ProjectSiteInspector."
                ),
                "key_classes": [
                    "ProjectSite",
                    "SemanticSiteBuilder",
                    "CoordinateRegistry",
                    "TopologyGenerator",
                    "ProjectSiteInspector",
                ],
                "key_functions": [
                    "build_project_site",
                    "compute_site_morphisms",
                    "site_from_modules",
                ],
                "key_theorems": [
                    "Thm 8.1 (ProjectSiteExistence)",
                    "Lem 8.1 (CoordinateUniqueness)",
                    "Cor 8.1 (MorphismComposition)",
                ],
            },
            {
                "section_id": "8.2",
                "title": "Module Covers",
                "description": (
                    "Studies admissible covers of a project site.  Introduces "
                    "ModuleCover, CoverBuilder, AdmissibilityChecker, "
                    "CechNerveComputer, and the cover quality scoring function. "
                    "Proves existence (Thm 8.2) and nerve contractibility "
                    "(Thm 8.3)."
                ),
                "key_classes": [
                    "ModuleCover",
                    "CoverBuilder",
                    "OverlapComputer",
                    "AdmissibilityChecker",
                    "CoverRefiner",
                    "CechNerveComputer",
                ],
                "key_functions": [
                    "build_module_cover",
                    "refine_cover_until_admissible",
                    "score_cover_quality",
                ],
                "key_theorems": [
                    "Thm 8.2 (AdmissibleCoverExistence)",
                    "Thm 8.3 (CechNerveContractible)",
                    "Lem 8.2 (OverlapTransitivity)",
                ],
            },
            {
                "section_id": "8.3",
                "title": "Fleet Structure",
                "description": (
                    "Formalises LLM agent assignment to cover elements.  "
                    "Provides FleetCoordinator, LoadBalancer, TrustAggregator, "
                    "FleetMonitor, and FleetPlanner.  Proves fleet coverage "
                    "(Thm 8.4), trust monotonicity (Thm 8.5), and optimal "
                    "load-balance (Lem 8.3)."
                ),
                "key_classes": [
                    "FleetMember",
                    "FleetCoordinator",
                    "LoadBalancer",
                    "TrustAggregator",
                    "FleetMonitor",
                    "FleetPlanner",
                ],
                "key_functions": [
                    "assemble_fleet",
                    "assign_fleet_to_cover",
                    "compute_fleet_trust",
                ],
                "key_theorems": [
                    "Thm 8.4 (FleetCoverage)",
                    "Thm 8.5 (TrustMonotonicity)",
                    "Lem 8.3 (LoadBalanceOptimality)",
                ],
            },
            {
                "section_id": "8.4",
                "title": "Hypercover Refinement and Descent",
                "description": (
                    "Develops iterated cover refinement to resolve descent "
                    "obstructions.  Provides HypercoverBuilder, "
                    "SimplicialStructureValidator, RefinementEngine, "
                    "ObstructionAnalyzer, DescentCoordinator.  Proves the "
                    "hypercover descent theorem (Thm 8.6) and obstruction "
                    "vanishing criterion (Thm 8.7)."
                ),
                "key_classes": [
                    "HypercoverDecomposition",
                    "HypercoverBuilder",
                    "SimplicialStructureValidator",
                    "RefinementEngine",
                    "ObstructionAnalyzer",
                    "DescentCoordinator",
                ],
                "key_functions": [
                    "build_hypercover",
                    "refine_hypercover",
                    "compute_descent_obstruction",
                ],
                "key_theorems": [
                    "Thm 8.6 (HypercoverDescent)",
                    "Thm 8.7 (ObstructionVanishing)",
                    "Thm 8.8 (ModuleDecomposition)",
                    "Lem 8.4 (DescentSpectralSequence)",
                ],
            },
        ],
        "key_constructions": [
            "ProjectSite",
            "ModuleCover",
            "FleetMember",
            "HypercoverDecomposition",
            "SemanticSiteBuilder",
            "CoverBuilder",
            "FleetCoordinator",
            "HypercoverBuilder",
            "DescentCoordinator",
            "ObstructionAnalyzer",
            "TrustAggregator",
            "CechNerveComputer",
        ],
    }


def verify_imports() -> dict[str, bool]:
    """Check that all 9 sub-module imports in this package resolve successfully.

    Attempts to import each sub-module under
    ``jugeo.foundations.project_hypercovers`` and records whether the import
    succeeded.  This function never raises; failed imports are recorded as
    ``False``.

    This is useful for health-checking the package in environments where
    some optional C-extension dependencies may not be installed, or where
    the package is being used as a partial scaffold.

    Returns
    -------
    dict[str, bool]
        Mapping from bare sub-module name to ``True`` (import OK) or
        ``False`` (``ImportError`` encountered).

    Notes
    -----
    Theory2.tex §8.0 — package health check for LLM orchestration.

    Examples
    --------
    >>> result = verify_imports()
    >>> isinstance(result, dict)
    True
    >>> "manifest" in result
    True
    >>> "models" in result
    True
    >>> "hypercover_refinement" in result
    True
    """
    submodules = [
        "manifest",
        "models",
        "project_sites",
        "module_covers",
        "fleet_structure",
        "hypercover_refinement",
        "algorithms",
        "integration",
        "theorems",
    ]
    results: dict[str, bool] = {}
    base = "jugeo.foundations.project_hypercovers"
    for name in submodules:
        try:
            __import__(f"{base}.{name}")
            results[name] = True
        except ImportError:
            results[name] = False
    return results


# ---------------------------------------------------------------------------
# Cross-subsystem integration: connecting Ch8 hypercovers to the geometry
# implementation packages.
# ---------------------------------------------------------------------------

import logging as _logging

_ph_logger = _logging.getLogger(__name__)


def hypercover_from_covers(
    covers,
    *,
    depth: int = 2,
    base_coordinate=None,
):
    """Build a :class:`~jugeo.geometry.hypercovers.Hypercover` from a
    sequence of :class:`~jugeo.geometry.covers.Cover` objects.

    This bridges the foundational hypercover theory (Ch8 §8.4) with the
    implementation-level hypercover machinery in ``jugeo.geometry.hypercovers``
    and ``jugeo.geometry.covers``.

    The function takes the first cover as the base level, constructs a
    :class:`~jugeo.geometry.hypercovers.HypercoverBuilder`, and iteratively
    adds levels from subsequent covers.  When only a single cover is provided,
    ``jugeo.geometry.hypercovers.build_hypercover`` is used to generate the
    multi-level decomposition up to *depth*.

    Parameters
    ----------
    covers : Iterable[Cover]
        One or more :class:`~jugeo.geometry.covers.Cover` objects.  The
        first cover is used as the base level (level 0).
    depth : int
        Maximum hypercover depth (number of levels).  Only used when a
        single cover is provided and the builder auto-generates higher
        levels.  Defaults to ``2``.
    base_coordinate : Coordinate | None
        Optional base coordinate for the hypercover.  When ``None``,
        inferred from the first cover's target.

    Returns
    -------
    Hypercover
        A :class:`~jugeo.geometry.hypercovers.Hypercover` instance with
        levels populated from the supplied covers.

    Raises
    ------
    RuntimeError
        If ``jugeo.geometry.covers`` or ``jugeo.geometry.hypercovers``
        cannot be imported.
    ValueError
        If *covers* is empty.

    Notes
    -----
    Theory2.tex §8.4 — Hypercovers are iterated sequences of covers
    whose colimit resolves all descent obstructions.  When the hypercover
    is contractible, the descent spectral sequence degenerates (Lem 8.4)
    and every local section glues uniquely (Thm 8.6).

    Examples
    --------
    >>> from jugeo.geometry.covers import CoverBuilder  # doctest: +SKIP
    >>> c = CoverBuilder().build()
    >>> hc = hypercover_from_covers([c])
    >>> len(hc.levels) >= 1
    True
    """
    try:
        from jugeo.geometry.covers import Cover
    except ImportError as exc:
        raise RuntimeError(
            "jugeo.geometry.covers is required for hypercover_from_covers()"
        ) from exc

    try:
        from jugeo.geometry.hypercovers import (
            Hypercover,
            HypercoverBuilder,
            HypercoverLevel,
            build_hypercover,
        )
    except ImportError as exc:
        raise RuntimeError(
            "jugeo.geometry.hypercovers is required for hypercover_from_covers()"
        ) from exc

    cover_list = list(covers)
    if not cover_list:
        raise ValueError("hypercover_from_covers() requires at least one cover")

    base_cover = cover_list[0]
    resolved_base = base_coordinate
    if resolved_base is None:
        resolved_base = getattr(base_cover, "target", None)

    if len(cover_list) == 1:
        # Auto-generate the hypercover from a single base cover
        return build_hypercover(base_cover, depth=depth)

    # Build incrementally from multiple covers
    builder = HypercoverBuilder(base_coordinate=resolved_base)

    for i, cover in enumerate(cover_list):
        level = HypercoverLevel(
            level_number=i,
            cover=cover,
            face_maps=[],
            degeneracy_maps=[],
        )
        builder.add_level(level)

    return builder.build()


__all__.append("hypercover_from_covers")


# ---------------------------------------------------------------------------
# Cross-subsystem bridges (foundations ↔ geometry/evidence)
# ---------------------------------------------------------------------------

import logging as _ph_logging
_ph_logger = _ph_logging.getLogger(__name__)


def project_hypercover(
    site: "Any",
    *,
    max_depth: int = 3,
    cover_strategy: str = "greedy",
) -> dict:
    """Build a project-level hypercover from a site.

    Bridges Theory2.tex §8.4 (hypercover decomposition) to the concrete
    implementations in ``jugeo.geometry.hypercovers`` and ``jugeo.geometry.covers``.
    """
    try:
        from jugeo.geometry.hypercovers import Hypercover, HypercoverLevel, HypercoverKind
    except ImportError:
        _ph_logger.warning("jugeo.geometry.hypercovers unavailable")
        return {"hypercover": None, "available": False}
    try:
        from jugeo.geometry.covers import CoverMember, score_cover, refine_cover
    except ImportError:
        _ph_logger.warning("jugeo.geometry.covers unavailable")
        return {"hypercover": None, "available": False}

    raw_members = getattr(site, "members", None) or []
    covers = [CoverMember(name=getattr(m, "name", str(i)), data=m) for i, m in enumerate(raw_members)]
    scored = [(c, score_cover(c)) for c in covers]
    if cover_strategy == "greedy":
        scored.sort(key=lambda pair: pair[1], reverse=True)
    refined = [refine_cover(c) for c, _ in scored]

    levels: list = []
    for depth_idx in range(min(max_depth, len(refined))):
        kind = HypercoverKind.BASE if depth_idx == 0 else HypercoverKind.REFINEMENT
        level = HypercoverLevel(level_number=depth_idx, cover=refined[depth_idx], kind=kind)
        levels.append(level)

    hypercover = Hypercover(levels=levels, site=site)
    return {"hypercover": hypercover, "available": True, "depth": len(levels)}


def hypercover_descent(hypercover: dict, *, strategy: str = "iterative") -> dict:
    """Run descent on a hypercover to check section gluing.

    Bridges §8.6 (hypercover descent) to ``jugeo.geometry.descent``.
    """
    try:
        from jugeo.geometry.descent import LocalSection, OverlapCondition, DescentStrategy, DescentPhase
    except ImportError:
        _ph_logger.warning("jugeo.geometry.descent unavailable")
        return {"glued": False, "obstructions": [], "available": False}

    hc_obj = hypercover.get("hypercover") if isinstance(hypercover, dict) else hypercover
    if hc_obj is None:
        return {"glued": False, "obstructions": ["no hypercover provided"], "available": True}

    levels = getattr(hc_obj, "levels", [])
    strat = DescentStrategy.ITERATIVE if strategy == "iterative" else DescentStrategy.SPECTRAL
    obstructions: list = []
    phase = DescentPhase.INIT

    for i, level in enumerate(levels):
        section = LocalSection(level=level, index=i)
        if i > 0:
            prev_level = levels[i - 1]
            overlap = OverlapCondition(lower=prev_level, upper=level)
            if not overlap.is_compatible(section):
                obstructions.append(f"obstruction at level {i}: overlap incompatible")
                phase = DescentPhase.OBSTRUCTED
                continue
        phase = DescentPhase.GLUED

    glued = len(obstructions) == 0 and phase == DescentPhase.GLUED
    return {"glued": glued, "obstructions": obstructions, "available": True, "strategy": str(strat)}


def hypercover_evidence(hypercover: dict, *, channel: str = "default") -> dict:
    """Collect evidence manifests for a hypercover decomposition.

    Bridges §8 to ``jugeo.evidence`` subsystem.
    """
    try:
        from jugeo.evidence.manifests import build_evidence_manifest, EvidenceManifest
    except ImportError:
        _ph_logger.warning("jugeo.evidence.manifests unavailable")
        return {"manifests": [], "available": False}
    try:
        from jugeo.evidence.trust import TrustLevel
    except ImportError:
        _ph_logger.warning("jugeo.evidence.trust unavailable")
        return {"manifests": [], "available": False}

    hc_obj = hypercover.get("hypercover") if isinstance(hypercover, dict) else hypercover
    if hc_obj is None:
        return {"manifests": [], "available": True, "reason": "no hypercover"}

    levels = getattr(hc_obj, "levels", [])
    manifests: list = []
    overall_trust = TrustLevel.HIGH

    for level in levels:
        manifest: "EvidenceManifest" = build_evidence_manifest(
            source=level,
            channel=channel,
        )
        if manifest.trust < overall_trust:
            overall_trust = manifest.trust
        manifests.append({"level": level.level_number, "manifest": manifest, "trust": str(manifest.trust)})

    return {
        "manifests": manifests,
        "available": True,
        "overall_trust": str(overall_trust),
        "count": len(manifests),
    }


# copilot: shared-core marker — project_hypercovers exposes the full Ch8 machinery for LLM-assisted verification workflows.



# --- auto-registered submodules ---
try:
    from . import algorithms
except Exception:
    pass
try:
    from . import closure_and_resumability
except Exception:
    pass
try:
    from . import fleet_semantics_and_economic_choic
except Exception:
    pass
try:
    from . import fleet_structure
except Exception:
    pass
try:
    from . import from_single_artifact_reasoning_to
except Exception:
    pass
try:
    from . import hypercover_refinement
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
    from . import models
except Exception:
    pass
try:
    from . import module_covers
except Exception:
    pass
try:
    from . import project_sites
except Exception:
    pass
try:
    from . import theorems
except Exception:
    pass
