"""
Cross-language analysis module for JuGeo webapp.

Detects bugs at language layer boundaries: Python/Jinja2/JS/CSS/HTML/SQL.
These are cohomology obstructions — Čech H¹ cocycles from §2.5.
"""
from __future__ import annotations

from jugeo.webapp.cross_language.models import (
    OverlapKind,
    OverlapCondition,
    OverlapViolation,
    CrossReference,
    MorphismEvidence,
    DescentReport,
)
from jugeo.webapp.cross_language.reference_resolver import (
    CrossReferenceResolver,
    URLPatternMatcher,
)
from jugeo.webapp.cross_language.overlap_checker import OverlapChecker
from jugeo.webapp.cross_language.morphism_builder import (
    CrossLanguageMorphismBuilder,
    MorphismGraph,
)
from jugeo.webapp.cross_language.contract_checker import (
    APIContractChecker,
    SchemaComparer,
)
from jugeo.webapp.cross_language.trust_topology import (
    WebTrustLevel,
    TrustBoundary,
    WebTrustChecker,
    TrustTransportChecker,
)
from jugeo.webapp.cross_language.integration import (
    CrossLanguageAnalyzer,
    QuickCheck,
)

__all__ = [
    "OverlapKind", "OverlapCondition", "OverlapViolation", "CrossReference",
    "MorphismEvidence", "DescentReport",
    "CrossReferenceResolver", "URLPatternMatcher",
    "OverlapChecker",
    "CrossLanguageMorphismBuilder", "MorphismGraph",
    "APIContractChecker", "SchemaComparer",
    "WebTrustLevel", "TrustBoundary", "WebTrustChecker", "TrustTransportChecker",
    "CrossLanguageAnalyzer", "QuickCheck",
]
