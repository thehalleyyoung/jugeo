"""
Web descent module for JuGeo webapp.

Implements the descent checking engine — detecting Čech cohomology obstructions
H¹ (pairwise overlaps) and H² (triple overlaps) across language layers.
"""
from __future__ import annotations

from jugeo.webapp.descent.models import (
    DescentStrategy,
    CohomologyClass,
    WebObstruction,
    DescentResult,
    DescentConfiguration,
)
from jugeo.webapp.descent.web_descent import WebDescentEngine, IncrementalDescentEngine
from jugeo.webapp.descent.cohomology import CechCohomology, ObstructionClassifier
from jugeo.webapp.descent.obstruction_catalog import (
    ObstructionPattern,
    KNOWN_PATTERNS,
    PatternMatcher,
)
from jugeo.webapp.descent.theorems import (
    ContextCompletenessTheorem,
    ContractConsistencyTheorem,
    DOMIntegrityTheorem,
    TrustMonotonicityTheorem,
    CohomologicalCompletenessTheorem,
)

__all__ = [
    "DescentStrategy", "CohomologyClass", "WebObstruction", "DescentResult", "DescentConfiguration",
    "WebDescentEngine", "IncrementalDescentEngine",
    "CechCohomology", "ObstructionClassifier",
    "ObstructionPattern", "KNOWN_PATTERNS", "PatternMatcher",
    "ContextCompletenessTheorem", "ContractConsistencyTheorem", "DOMIntegrityTheorem",
    "TrustMonotonicityTheorem", "CohomologicalCompletenessTheorem",
]
