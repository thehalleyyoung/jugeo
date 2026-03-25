"""Architecture as Cover Design — sheaf-theoretic software architecture.

Re-exports key classes from models, algorithms, integration, and theorems.
"""
from __future__ import annotations

from jugeo.se_theory.architecture.algorithms import (
    ArchitectureEnforcer,
    ArchitectureTracker,
    CoverAnalyzer,
    CoverSuggester,
    TarjanSCC,
)
from jugeo.se_theory.architecture.integration import (
    ImportGraphArchitecture,
    SiteArchitectureAnalyzer,
)
from jugeo.se_theory.architecture.models import (
    ArchitecturalDecision,
    ArchitecturalDecisionKind,
    ArchitecturalDrift,
    ArchitecturalManifest,
    ArchitecturalMetric,
    ArchitecturalOverlap,
    ArchitecturalSnapshot,
    BoundaryViolation,
    CoverMember,
    CoverMemberKind,
    CoverQualityMetrics,
    DeclaredBoundary,
)
from jugeo.se_theory.architecture.theorems import (
    ALL_THEOREMS,
    THEOREM_BOUNDARY_ENFORCEMENT_PREVENTS_DRIFT,
    THEOREM_COHESION_IMPLIES_LOCAL_CORRECTNESS,
    THEOREM_COUPLING_BOUNDS_DESCENT_COST,
    THEOREM_INTERFACE_WIDTH_BOUNDS_TREATY_COST,
    THEOREM_SCC_COLLAPSE_PRESERVES_DESCENT,
    ArchitecturalTheorem,
    BoundaryEnforcementPreventsDriftTheorem,
    CohesionImpliesLocalCorrectnessTheorem,
    CouplingBoundsDescentCostTheorem,
    InterfaceWidthBoundsTreatyCostTheorem,
    SCCCollapsePreservesDescentTheorem,
)

__all__ = [
    # Models
    "ArchitecturalDecision",
    "ArchitecturalDecisionKind",
    "ArchitecturalDrift",
    "ArchitecturalManifest",
    "ArchitecturalMetric",
    "ArchitecturalOverlap",
    "ArchitecturalSnapshot",
    "BoundaryViolation",
    "CoverMember",
    "CoverMemberKind",
    "CoverQualityMetrics",
    "DeclaredBoundary",
    # Algorithms
    "ArchitectureEnforcer",
    "ArchitectureTracker",
    "CoverAnalyzer",
    "CoverSuggester",
    "TarjanSCC",
    # Integration
    "ImportGraphArchitecture",
    "SiteArchitectureAnalyzer",
    # Theorems
    "ALL_THEOREMS",
    "ArchitecturalTheorem",
    "BoundaryEnforcementPreventsDriftTheorem",
    "CohesionImpliesLocalCorrectnessTheorem",
    "CouplingBoundsDescentCostTheorem",
    "InterfaceWidthBoundsTreatyCostTheorem",
    "SCCCollapsePreservesDescentTheorem",
    "THEOREM_BOUNDARY_ENFORCEMENT_PREVENTS_DRIFT",
    "THEOREM_COHESION_IMPLIES_LOCAL_CORRECTNESS",
    "THEOREM_COUPLING_BOUNDS_DESCENT_COST",
    "THEOREM_INTERFACE_WIDTH_BOUNDS_TREATY_COST",
    "THEOREM_SCC_COLLAPSE_PRESERVES_DESCENT",
]
