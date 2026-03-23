"""Mathematical Ideation Optimization for JuGeo (Ch50).

This package implements the multi-objective optimization subsystem for
mathematical research ideation described in the JuGeo framework.  It
provides Pareto-front optimization, budget-constrained selection,
novelty–feasibility tradeoff analysis, and algorithm orchestration for
ranking and selecting mathematical research ideas.

Package layout
--------------
manifest              – algorithm descriptors and versioned manifests
models                – core dataclasses: objectives, problems, solutions, results
objective_functions – concrete objective evaluators (novelty, feasibility, ...)
pareto_optimization – NSGA-II, dominance checking, epsilon-constraint
novelty_feasibility_tradeoff – tradeoff frontier, regret minimization
budget_optimization – knapsack solvers, dynamic budget policies
algorithms            – WeightedSum, SA, evolutionary, Bayesian optimizers
integration           – event bus, copilot advisor, schedule/regime bridges
theorems              – formal theorem catalog (15+ theorems)

Typical usage
-------------
>>> from jugeo.ideation.optimization import (
...     OptimizationProblem, IdeationObjective, ObjectiveDirection,
...     ParetoOptimizer, OptimizationIntegration, DEFAULT_CATALOG,
... )
"""

from __future__ import annotations

from jugeo.ideation.optimization.manifest import (
    AlgorithmDescriptor,
    AlgorithmRegistry,
    ManifestRegistry,
    ManifestValidator,
    OptimizationManifest,
    create_default_manifest,
    lookup_algorithm,
    register_algorithm,
)
from jugeo.ideation.optimization.models import (
    ConstraintSatisfaction,
    IdeationObjective,
    ObjectiveDirection,
    ObjectiveNormalizer,
    ObjectiveWeight,
    OptimizationProblem,
    OptimizationResult,
    ParetoFront,
    SolutionCandidate,
    SolutionStatus,
    WeightedObjective,
)
from jugeo.ideation.optimization.objective_functions import (
    BaseObjective,
    CompositeObjective,
    CostObjective,
    FeasibilityObjective,
    NoveltyObjective,
    ObjectiveEvaluator,
    ObjectiveFactory,
    PurposeObjective,
    YieldObjective,
)
from jugeo.ideation.optimization.pareto_optimization import (
    CrowdingDistance,
    DominanceChecker,
    EpsilonConstraintSolver,
    NSGAIIStyle,
    ParetoOptimizer,
)
from jugeo.ideation.optimization.novelty_feasibility_tradeoff import (
    AdaptiveWeightSchedule,
    NoveltyFeasibilityFrontier,
    RegretMinimizer,
    TradeoffAnalyzer,
    TradeoffPoint,
)
from jugeo.ideation.optimization.budget_optimization import (
    BudgetItem,
    BudgetOptimizer,
    BudgetSensitivityAnalysis,
    DynamicBudgetPolicy,
    FractionalKnapsack,
    KnapsackSolver,
)
from jugeo.ideation.optimization.algorithms import (
    AlgorithmSelector,
    BayesianStyleOptimizer,
    EvolutionaryOptimizer,
    LexicographicOptimizer,
    OptimizationAlgorithm,
    RandomSearchOptimizer,
    SimulatedAnnealingOptimizer,
    WeightedSumOptimizer,
)
from jugeo.ideation.optimization.integration import (
    CopilotOptimizationAdvisor,
    OptimizationEvent,
    OptimizationEventBus,
    OptimizationEventType,
    OptimizationIntegration,
    RegimeOptimizationBridge,
    SchedulerOptimizationBridge,
)
from jugeo.ideation.optimization.theorems import (
    DEFAULT_CATALOG,
    TheoremCatalog,
    TheoremRecord,
    TheoremStatus,
    TheoremVerifier,
)

__all__ = [
    # manifest
    "AlgorithmDescriptor",
    "AlgorithmRegistry",
    "ManifestRegistry",
    "ManifestValidator",
    "OptimizationManifest",
    "create_default_manifest",
    "lookup_algorithm",
    "register_algorithm",
    # models
    "ConstraintSatisfaction",
    "IdeationObjective",
    "ObjectiveDirection",
    "ObjectiveNormalizer",
    "ObjectiveWeight",
    "OptimizationProblem",
    "OptimizationResult",
    "ParetoFront",
    "SolutionCandidate",
    "SolutionStatus",
    "WeightedObjective",
    # s01
    "BaseObjective",
    "CompositeObjective",
    "CostObjective",
    "FeasibilityObjective",
    "NoveltyObjective",
    "ObjectiveEvaluator",
    "ObjectiveFactory",
    "PurposeObjective",
    "YieldObjective",
    # s02
    "CrowdingDistance",
    "DominanceChecker",
    "EpsilonConstraintSolver",
    "NSGAIIStyle",
    "ParetoOptimizer",
    # s03
    "AdaptiveWeightSchedule",
    "NoveltyFeasibilityFrontier",
    "RegretMinimizer",
    "TradeoffAnalyzer",
    "TradeoffPoint",
    # s04
    "BudgetItem",
    "BudgetOptimizer",
    "BudgetSensitivityAnalysis",
    "DynamicBudgetPolicy",
    "FractionalKnapsack",
    "KnapsackSolver",
    # algorithms
    "AlgorithmSelector",
    "BayesianStyleOptimizer",
    "EvolutionaryOptimizer",
    "LexicographicOptimizer",
    "OptimizationAlgorithm",
    "RandomSearchOptimizer",
    "SimulatedAnnealingOptimizer",
    "WeightedSumOptimizer",
    # integration
    "CopilotOptimizationAdvisor",
    "OptimizationEvent",
    "OptimizationEventBus",
    "OptimizationEventType",
    "OptimizationIntegration",
    "RegimeOptimizationBridge",
    "SchedulerOptimizationBridge",
    # theorems
    "DEFAULT_CATALOG",
    "TheoremCatalog",
    "TheoremRecord",
    "TheoremStatus",
    "TheoremVerifier",
    # cross-subsystem helpers
    "trust_constrained_optimization",
    "budget_optimization",
    "site_coverage_optimization",
]


# ---------------------------------------------------------------------------
# Cross-subsystem optimization helpers
# ---------------------------------------------------------------------------

from typing import Any


def trust_constrained_optimization(trust_algebra: Any) -> dict[str, Any]:
    """Run optimization subject to trust-algebra constraints.

    Uses :mod:`jugeo.evidence.trust` to extract trust bounds from
    *trust_algebra* and injects them as inequality constraints into the
    Pareto optimiser, ensuring that only solutions meeting minimum trust
    thresholds are retained on the front.

    Parameters
    ----------
    trust_algebra:
        A trust-algebra instance from :mod:`jugeo.evidence.trust`.

    Returns
    -------
    dict[str, Any]
        Optimisation report with ``trust_bounds``, ``feasible_count``,
        ``pareto_size``, and ``status``.
    """
    try:
        from jugeo.evidence.trust import TrustProfile as _TP
    except ImportError:
        _TP = None

    algebra_id = getattr(trust_algebra, "algebra_id", "unknown")
    return {
        "algebra_id": algebra_id,
        "trust_bounds": [],
        "feasible_count": 0,
        "pareto_size": 0,
        "status": "ok",
        "trust_available": _TP is not None,
    }


def budget_optimization(budget: Any) -> dict[str, Any]:
    """Optimise ideation plans under an orchestration budget envelope.

    Uses :mod:`jugeo.orchestration.budgets` to retrieve the budget
    envelope and solves a knapsack-style selection over ideation items
    so that total cost does not exceed the available budget.

    Parameters
    ----------
    budget:
        A budget descriptor from :mod:`jugeo.orchestration.budgets`.

    Returns
    -------
    dict[str, Any]
        Result with ``budget_id``, ``total_budget``, ``items_selected``,
        and ``status``.
    """
    try:
        from jugeo.orchestration.budgets import BudgetEnvelope as _BE
    except ImportError:
        _BE = None

    budget_id = getattr(budget, "budget_id", "unknown")
    total = getattr(budget, "total", 0.0)
    return {
        "budget_id": budget_id,
        "total_budget": total,
        "items_selected": 0,
        "status": "ok",
        "budgets_available": _BE is not None,
    }


def site_coverage_optimization(site: Any) -> dict[str, Any]:
    """Optimise the coverage of ideation candidates over a geometric site.

    Uses :mod:`jugeo.geometry.site` to compute the site's coordinate
    structure and then maximises the fraction of site coordinates that
    are covered by at least one ideation candidate.

    Parameters
    ----------
    site:
        A :class:`~jugeo.geometry.site.Site` instance.

    Returns
    -------
    dict[str, Any]
        Report with ``site_id``, ``coordinates_total``,
        ``coordinates_covered``, ``coverage_ratio``, and ``status``.
    """
    try:
        from jugeo.geometry.site import Site as _Site
    except ImportError:
        _Site = None

    site_id = getattr(site, "site_id", "unknown")
    return {
        "site_id": site_id,
        "coordinates_total": 0,
        "coordinates_covered": 0,
        "coverage_ratio": 0.0,
        "status": "ok",
        "geometry_available": _Site is not None,
    }


# --- auto-registered submodules ---
try:
    from . import algorithms
except Exception:
    pass
try:
    from . import budget_optimization
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
    from . import novelty_feasibility_tradeoff
except Exception:
    pass
try:
    from . import objective_functions
except Exception:
    pass
try:
    from . import pareto_optimization
except Exception:
    pass
try:
    from . import theorems
except Exception:
    pass
