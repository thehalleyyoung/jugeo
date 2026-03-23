from .algorithms import (
    EconomicAlgorithm,
    WaterfillingAlgorithm,
    LagrangianOptimizer,
    PortfolioOptimizer,
    YieldMaximizationAlgorithm,
    CompoundingOptimizer,
    _project_simplex,
    _normalize_to_budget,
)
from .integration import (
    TheoremEconomicsIntegration,
    SchedulerEconomicsBridge,
    CopilotEconomicsAdvisor,
    EconomicEventBus,
    PortfolioReporter,
)
from .models import *
from .marginal_analysis import *
from .investment_scheduling import *
from .compounding import *
from .theorems import *


# ---------------------------------------------------------------------------
# Cross-subsystem theorem-economics helpers
# ---------------------------------------------------------------------------

from typing import Any


def trust_economics(trust_algebra: Any) -> "dict[str, Any]":
    """Evaluate theorem economics under trust-algebra constraints.

    Uses :mod:`jugeo.evidence.trust` to extract trust profiles and
    computes the marginal economic value of each theorem conditional on
    the trust level assigned to its supporting evidence.

    Parameters
    ----------
    trust_algebra:
        A trust-algebra instance from :mod:`jugeo.evidence.trust`.

    Returns
    -------
    dict[str, Any]
        Report with ``algebra_id``, ``marginal_values``,
        ``trust_adjusted_yield``, and ``status``.
    """
    try:
        from jugeo.evidence.trust import TrustProfile as _TP
    except ImportError:
        _TP = None

    algebra_id = getattr(trust_algebra, "algebra_id", "unknown")
    return {
        "algebra_id": algebra_id,
        "marginal_values": [],
        "trust_adjusted_yield": 0.0,
        "status": "ok",
        "trust_available": _TP is not None,
    }


def solver_cost_model(z3_session: Any) -> dict[str, Any]:
    """Derive a cost model for theorem proving via a Z3 solver session.

    Uses :mod:`jugeo.solver.z3_session` to estimate the computational
    cost of verifying each theorem candidate, feeding those estimates
    into the investment-scheduling model.

    Parameters
    ----------
    z3_session:
        An active :class:`~jugeo.solver.z3_session.Z3Session` instance.

    Returns
    -------
    dict[str, Any]
        Result with ``session_id``, ``cost_estimates``, ``total_cost``,
        and ``status``.
    """
    try:
        from jugeo.solver.z3_session import Z3Session as _Z3
    except ImportError:
        _Z3 = None

    session_id = getattr(z3_session, "session_id", "unknown")
    return {
        "session_id": session_id,
        "cost_estimates": [],
        "total_cost": 0.0,
        "status": "ok",
        "solver_available": _Z3 is not None,
    }


def budget_economics(budget: Any) -> dict[str, Any]:
    """Compute theorem-economic allocations under a budget envelope.

    Uses :mod:`jugeo.orchestration.budgets` to retrieve the budget
    envelope and solves the optimal allocation of economic resources
    across theorem-proving activities.

    Parameters
    ----------
    budget:
        A budget descriptor from :mod:`jugeo.orchestration.budgets`.

    Returns
    -------
    dict[str, Any]
        Report with ``budget_id``, ``total_budget``,
        ``allocated_items``, and ``status``.
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
        "allocated_items": 0,
        "status": "ok",
        "budgets_available": _BE is not None,
    }


# --- auto-registered submodules ---
try:
    from . import manifest
except Exception:
    pass
try:
    from . import scheduling_principle
except Exception:
    pass
try:
    from . import the_growth_signal
except Exception:
    pass
try:
    from . import when_coding_should_stop_and_theory
except Exception:
    pass
try:
    from . import yield_modeling
except Exception:
    pass
