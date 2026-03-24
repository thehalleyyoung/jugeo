"""jugeo.webapp.ideation — Application ideation pipeline."""
from .models import (
    ApplicationCoordinate, GapType, IdeaSource, ValidationStatus,
    AppIdeationPurpose, ExistingApp, IdeaPortfolio, Gap,
    CoverageReport, GainProfile, IdeaProposal, ValidationResult,
    RankedIdea, IdeationResult,
)
from .app_coordinates import ApplicationCoordinateSpace, COORD_SPACE
from .portfolio_builder import ApplicationPortfolioBuilder, BuiltinPortfolios
from .coverage_estimator import AppCoverageEstimator, GapDetector
from .gap_filler import GapFillerGenerator
from .analogy_transporter import (
    AppAnalogyTransporter, DomainTool, AnalogyMap, BuiltinSourceTools, SOURCE_DOMAINS,
)
from .intersection_detector import IntersectionDetector
from .novelty_functional import (
    PurposeConditionedNoveltyFunctional, FeasibilityFilter, NoveltyMetric,
)
from .validator import AppIdeaValidator, DemandSignalAnalyzer
from .marginal_analyzer import AppMarginalAnalyzer, EquimarginalAllocator
from .pipeline import IdeationPipeline, IdeationConfig, WORKED_EXAMPLES

__all__ = [
    "ApplicationCoordinate", "GapType", "IdeaSource", "ValidationStatus",
    "AppIdeationPurpose", "ExistingApp", "IdeaPortfolio", "Gap",
    "CoverageReport", "GainProfile", "IdeaProposal", "ValidationResult",
    "RankedIdea", "IdeationResult",
    "ApplicationCoordinateSpace", "COORD_SPACE",
    "ApplicationPortfolioBuilder", "BuiltinPortfolios",
    "AppCoverageEstimator", "GapDetector",
    "GapFillerGenerator",
    "AppAnalogyTransporter", "DomainTool", "AnalogyMap", "BuiltinSourceTools", "SOURCE_DOMAINS",
    "IntersectionDetector",
    "PurposeConditionedNoveltyFunctional", "FeasibilityFilter", "NoveltyMetric",
    "AppIdeaValidator", "DemandSignalAnalyzer",
    "AppMarginalAnalyzer", "EquimarginalAllocator",
    "IdeationPipeline", "IdeationConfig", "WORKED_EXAMPLES",
]
