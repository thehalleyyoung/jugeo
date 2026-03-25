"""Debugging as Obstruction Localization (B4).

In JG, a bug is an obstruction at a coordinate where the local section fails.
Debugging is localizing descent failures, extracting countermodels, and
computing repair frontiers.
"""
from __future__ import annotations

from jugeo.se_theory.debugging.models import (
    CohomologyClass,
    CountermodelReport,
    DescentTrace,
    LocalSection,
    Morphism,
    Obstruction,
    ObstructionCluster,
    ObstructionSeverity,
    Overlap,
    RepairFrontier,
    RepairPlan,
    RepairStrategy,
    RootCauseAnalysis,
    TriageReport,
)
from jugeo.se_theory.debugging.algorithms import (
    CountermodelAnalyzer,
    ObstructionLocalizer,
    ObstructionTriager,
    RepairFrontierComputer,
    RootCauseTracer,
)
from jugeo.se_theory.debugging.integration import (
    ObstructionDatabase,
    SiteDebugger,
)
from jugeo.se_theory.debugging.theorems import (
    CANONICAL_THEOREM_OBLIGATIONS,
    ProofStrategy,
    TheoremObligation,
    TheoremStatus,
    check_theorem_blast_radius_bounds_cascade,
    check_theorem_clustering_reduces_human_load,
    check_theorem_obstruction_localization_is_sound,
    check_theorem_repair_frontier_is_minimal,
    check_theorem_root_cause_precedes_symptoms,
    get_theorem,
    list_open_theorems,
    list_verified_theorems,
    theorem_summary,
)

__all__ = [
    # Models
    "CohomologyClass",
    "CountermodelReport",
    "DescentTrace",
    "LocalSection",
    "Morphism",
    "Obstruction",
    "ObstructionCluster",
    "ObstructionSeverity",
    "Overlap",
    "RepairFrontier",
    "RepairPlan",
    "RepairStrategy",
    "RootCauseAnalysis",
    "TriageReport",
    # Algorithms
    "CountermodelAnalyzer",
    "ObstructionLocalizer",
    "ObstructionTriager",
    "RepairFrontierComputer",
    "RootCauseTracer",
    # Integration
    "ObstructionDatabase",
    "SiteDebugger",
    # Theorems
    "CANONICAL_THEOREM_OBLIGATIONS",
    "ProofStrategy",
    "TheoremObligation",
    "TheoremStatus",
    "check_theorem_blast_radius_bounds_cascade",
    "check_theorem_clustering_reduces_human_load",
    "check_theorem_obstruction_localization_is_sound",
    "check_theorem_repair_frontier_is_minimal",
    "check_theorem_root_cause_precedes_symptoms",
    "get_theorem",
    "list_open_theorems",
    "list_verified_theorems",
    "theorem_summary",
]
