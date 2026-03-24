"""JuGeo web application site module — Grothendieck site model for web apps."""
from .coordinate_kinds import WebCoordinateKind
from .morphism_kinds import CrossLanguageMorphismKind, _MORPHISM_LAYERS
from .models import (
    WebCoordinate, WebMorphism, WebCoveringFamily,
    RequestLifecycle, DescentCondition, DescentViolation, LanguageLayer,
    LANGUAGE_LAYERS,
)
from .web_site import WebApplicationSite
from .request_lifecycle import (
    LifecycleStage, DataFlowEdge, RequestLifecycleBuilder,
    STANDARD_LIFECYCLE_STAGES,
    lifecycle_overlap_conditions, trace_data_flow,
)
from .topology import WebTopology, is_covering, refinement, generate_standard_covers, sieve_from_morphisms
from .integration import WebSiteToSite, SiteToWebSite, WebSiteAnalyzer, generate_report

__all__ = [
    "WebCoordinateKind",
    "CrossLanguageMorphismKind",
    "_MORPHISM_LAYERS",
    "WebCoordinate",
    "WebMorphism",
    "WebCoveringFamily",
    "RequestLifecycle",
    "DescentCondition",
    "DescentViolation",
    "LanguageLayer",
    "LANGUAGE_LAYERS",
    "WebApplicationSite",
    "LifecycleStage",
    "DataFlowEdge",
    "RequestLifecycleBuilder",
    "STANDARD_LIFECYCLE_STAGES",
    "lifecycle_overlap_conditions",
    "trace_data_flow",
    "WebTopology",
    "is_covering",
    "refinement",
    "generate_standard_covers",
    "sieve_from_morphisms",
    "WebSiteToSite",
    "SiteToWebSite",
    "WebSiteAnalyzer",
    "generate_report",
]
