"""JuGeo geometry package — site, cover, descent, and support structures.

This package implements the sheaf-theoretic geometry that underpins JuGeo.
Coordinates name points in a semantic site, covers decompose those points
into local views, descent glues local judgments into global sections, and
supports track where evidence honestly lives.

Cross-subsystem integration
~~~~~~~~~~~~~~~~~~~~~~~~~~~
The geometry classes expose integration hooks into other JuGeo subsystems.
All imports are lazy (try/except) so that geometry remains usable without
the optional subsystems installed.

* **judgments** — ``Coordinate.judgment_section()`` materialises the
  judgment presheaf at a coordinate via ``jugeo.judgments.sections``.
* **evidence** — ``Coordinate.trust_annotation()`` and
  ``Cover.evidence_cover()`` query ``jugeo.evidence.trust`` for
  trust-tier information.
  ``DescentEngine.certificate_of_descent()`` produces certificates
  via ``jugeo.evidence.certificates``.
* **foundations** — ``Site.formal_core_site()`` builds a site from
  ``jugeo.foundations.formal_core``.
  ``Cover.oracle_assisted_refinement()`` uses
  ``jugeo.foundations.oracle_federation`` for oracle-guided cover
  refinement.
* **solver** — ``DescentEngine.solver_assisted_descent()`` encodes
  gluing conditions as SMT formulae via ``jugeo.solver.z3_session``.
  ``DescentEngine.obstruction_to_countermodel()`` converts descent
  obstructions into countermodels via ``jugeo.solver.countermodels``.
* **encodings** — ``SupportSet.encoding_support()`` maps coordinates
  to encoding families via ``jugeo.encodings``.
* **python_runtime** — ``SupportSet.runtime_witness_support()`` collects
  runtime witnesses via ``jugeo.python_runtime``.
"""

from jugeo.geometry.site import (  # noqa: F401
    Coordinate,
    CoordinateIndex,
    CoordinateKind,
    CoordinateMorphism,
    CoordinateObject,
    CoveringFamily,
    GrothendieckTopology,
    Morphism,
    OverlapData,
    SemanticSite,
    Site,
    SiteBuilder,
    SiteDiagnostics,
    SiteSerializer,
    build_site,
    restrict_coordinate,
)
from jugeo.geometry.covers import (  # noqa: F401
    Cover,
    CoverBuilder,
    CoverCategory,
    CoverDiagnostics,
    CoverGenerator,
    CoverMember,
    CoverMerger,
    CoverRefinement,
    CoverSerializer,
    CoverStatistics,
    MergeConflictPolicy,
    OverlapDatum,
    Sieve,
)
from jugeo.geometry.descent import (  # noqa: F401
    CohomologyClass,
    DescentConfiguration,
    DescentEngine,
    DescentLog,
    DescentObstruction,
    DescentResult,
    DescentStrategy,
    GlobalSection,
    GluingData,
    GluingReport,
    LocalSection,
    Obstruction,
    OverlapCondition,
    RepairFrontier,
    glue_sections,
    run_descent,
)
from jugeo.geometry.supports import (  # noqa: F401
    EvidenceSupportScope,
    SupportMap,
    SupportMerger,
    SupportPolicy,
    SupportPropagation,
    SupportRegion,
    SupportSerializer,
    SupportSet,
    SupportStatistics,
    SupportStatus,
    SupportTracker,
    SupportVerifier,
    SupportVisualization,
    SupportedSection,
    VerificationResult,
)
from jugeo.geometry.bundle import (  # noqa: F401
    BundleCurvature,
    BundleHolonomy,
    CharacteristicClass,
    EvidenceChannel,
    Judgment,
    JudgmentFiber,
    TransportObservation,
    TrustConnection,
    TrustStratum,
    VerificationBundle,
)

__all__ = [
    # site
    "Coordinate",
    "CoordinateIndex",
    "CoordinateKind",
    "CoordinateMorphism",
    "CoordinateObject",
    "CoveringFamily",
    "GrothendieckTopology",
    "Morphism",
    "OverlapData",
    "SemanticSite",
    "Site",
    "SiteBuilder",
    "SiteDiagnostics",
    "SiteSerializer",
    "build_site",
    "restrict_coordinate",
    # covers
    "Cover",
    "CoverBuilder",
    "CoverCategory",
    "CoverDiagnostics",
    "CoverGenerator",
    "CoverMember",
    "CoverMerger",
    "CoverRefinement",
    "CoverSerializer",
    "CoverStatistics",
    "MergeConflictPolicy",
    "OverlapDatum",
    "Sieve",
    # descent
    "CohomologyClass",
    "DescentConfiguration",
    "DescentEngine",
    "DescentLog",
    "DescentObstruction",
    "DescentResult",
    "DescentStrategy",
    "GlobalSection",
    "GluingData",
    "GluingReport",
    "LocalSection",
    "Obstruction",
    "OverlapCondition",
    "RepairFrontier",
    "glue_sections",
    "run_descent",
    # supports
    "EvidenceSupportScope",
    "SupportMap",
    "SupportMerger",
    "SupportPolicy",
    "SupportPropagation",
    "SupportRegion",
    "SupportSerializer",
    "SupportSet",
    "SupportStatistics",
    "SupportStatus",
    "SupportTracker",
    "SupportVerifier",
    "SupportVisualization",
    "SupportedSection",
    "VerificationResult",
    # bundle
    "BundleCurvature",
    "BundleHolonomy",
    "CharacteristicClass",
    "EvidenceChannel",
    "Judgment",
    "JudgmentFiber",
    "TransportObservation",
    "TrustConnection",
    "TrustStratum",
    "VerificationBundle",
]


# --- auto-registered submodules ---
try:
    from . import covers
except Exception:
    pass
try:
    from . import descent
except Exception:
    pass
try:
    from . import hypercovers
except Exception:
    pass
try:
    from . import site
except Exception:
    pass
try:
    from . import supports
except Exception:
    pass
try:
    from . import bundle
except Exception:
    pass
