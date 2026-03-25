"""
Large-Scale Co-Evolution Orchestration Engine for JuGeo.

This package implements the domain-agnostic co-evolution orchestration system
that handles ANY software ideation/generation at scale.  It strictly generalises
Comet-H's augmented Mealy machine while being domain-agnostic (not just
research software).

Key concepts:

- obligation vector → obligation presheaf (typed, coordinate-aware)
- grounding trigger → descent obligation (normal sheaf condition)
- adjacency rules → cover refinement
- mode graph → automatic phase detection
- single scorer → multi-strategy fleet competition

Activated when site size exceeds a threshold or ``--desired-kloc`` is set.
"""
from __future__ import annotations

try:
    from .models import (  # noqa: F401
        Surface,
        SurfaceState,
        DriftEdge,
        CoEvolutionState,
        ObligationKind,
        TypedObligation,
        ObligationPresheaf,
        SupportAwareDecay,
        ControllerLevel,
        ControllerState,
        LocalController,
        RegionalController,
        GlobalController,
        Phase,
        PhaseSignal,
        PhaseTransition,
        Strategy,
        Bid,
        FleetResult,
        ConvergenceCriterion,
        ConvergenceCertificate,
        MoveCategory,
        SemanticMove,
        MoveResult,
        MoveHistory,
        BudgetAllocation,
        BudgetUsage,
    )
except Exception:  # pragma: no cover
    pass

try:
    from .co_evolution import CoEvolutionEngine  # noqa: F401
except Exception:  # pragma: no cover
    pass

try:
    from .obligation_presheaf import ObligationManager  # noqa: F401
except Exception:  # pragma: no cover
    pass

try:
    from .phase_detector import PhaseDetector  # noqa: F401
except Exception:  # pragma: no cover
    pass

try:
    from .fleet_manager import FleetManager  # noqa: F401
except Exception:  # pragma: no cover
    pass

try:
    from .convergence import ConvergenceMonitor  # noqa: F401
except Exception:  # pragma: no cover
    pass

try:
    from .budget_allocator import BudgetAllocator  # noqa: F401
except Exception:  # pragma: no cover
    pass

try:
    from .controller import LargeScaleController  # noqa: F401
except Exception:  # pragma: no cover
    pass

try:
    from .large_repo import LargeRepoOptimizer  # noqa: F401
except Exception:  # pragma: no cover
    pass

__all__ = [
    # Models
    "Surface", "SurfaceState", "DriftEdge", "CoEvolutionState",
    "ObligationKind", "TypedObligation", "ObligationPresheaf", "SupportAwareDecay",
    "ControllerLevel", "ControllerState", "LocalController", "RegionalController",
    "GlobalController",
    "Phase", "PhaseSignal", "PhaseTransition",
    "Strategy", "Bid", "FleetResult",
    "ConvergenceCriterion", "ConvergenceCertificate",
    "MoveCategory", "SemanticMove", "MoveResult", "MoveHistory",
    "BudgetAllocation", "BudgetUsage",
    # Engines
    "CoEvolutionEngine", "ObligationManager", "PhaseDetector", "FleetManager",
    "ConvergenceMonitor", "BudgetAllocator", "LargeScaleController", "LargeRepoOptimizer",
]
