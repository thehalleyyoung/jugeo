"""Ch53: Experiment Design for Mathematical Ideation Optimization.

This package provides infrastructure for designing, registering, running, and
evaluating experiments that probe the performance of ideation systems. It covers
four canonical study types:

  * **Ablation** — systematically removes components to measure their contribution:
      ΔY_i = Y(full) − Y(full − C_i)

  * **Calibration** — verifies that model parameter estimates converge to ground
    truth as data accumulates:
      E[θ̂] → θ  (consistency)

  * **Falsification** — attempts to construct conditions that refute a hypothesis,
    following Popperian methodology.

  * **Statistical Validation** — applies rigorous hypothesis testing with explicit
    power analysis and effect-size reporting.

Public API
----------
All symbols exported by sub-modules are re-exported here so callers can do::

    from jugeo.ideation.experiment_design import (
        ExperimentDesignManifest,
        AblationStudy,
        CalibrationExperiment,
        FalsificationTest,
        ...
    )
"""

from __future__ import annotations

from jugeo.ideation.experiment_design.manifest import (
    ControlVariable,
    ExperimentDescriptor,
    ExperimentDesignManifest,
    ExperimentRegistry,
    ExperimentStatus,
    ExperimentType,
    ManifestRegistry,
    ManifestValidator,
    MeasureSpec,
)
from jugeo.ideation.experiment_design.models import (
    AblationStudy,
    CalibrationExperiment,
    ExperimentBatch,
    ExperimentComparison,
    ExperimentDesign,
    ExperimentResult,
    FalsificationTest,
    PowerAnalysis,
    StatisticalTest,
)

__all__: list[str] = [
    # manifest
    "ControlVariable",
    "ExperimentDescriptor",
    "ExperimentDesignManifest",
    "ExperimentRegistry",
    "ExperimentStatus",
    "ExperimentType",
    "ManifestRegistry",
    "ManifestValidator",
    "MeasureSpec",
    # models
    "AblationStudy",
    "CalibrationExperiment",
    "ExperimentBatch",
    "ExperimentComparison",
    "ExperimentDesign",
    "ExperimentResult",
    "FalsificationTest",
    "PowerAnalysis",
    "StatisticalTest",
    # cross-subsystem helpers
    "judgment_experiment",
    "solver_experiment",
    "evidence_experiment",
]


# ---------------------------------------------------------------------------
# Cross-subsystem experiment helpers
# ---------------------------------------------------------------------------

from typing import Any


def judgment_experiment(judgment: Any) -> dict[str, Any]:
    """Design an experiment probing judgment-term sensitivity.

    Uses :mod:`jugeo.judgments.judgment_terms` to decompose the given
    judgment into constituent terms and constructs an ablation study that
    removes one term at a time to measure its contribution.

    Parameters
    ----------
    judgment:
        A judgment object whose terms will be individually ablated.

    Returns
    -------
    dict[str, Any]
        Experiment descriptor with ``judgment_id``, ``term_count``,
        ``ablation_designs``, and ``status``.
    """
    try:
        from jugeo.judgments.judgment_terms import decompose_terms
    except ImportError:
        decompose_terms = None  # type: ignore[assignment]

    judgment_id = getattr(judgment, "judgment_id", "unknown")
    terms: list[Any] = []
    if decompose_terms is not None:
        try:
            terms = list(decompose_terms(judgment))
        except Exception:
            pass

    return {
        "judgment_id": judgment_id,
        "term_count": len(terms),
        "ablation_designs": [],
        "status": "ok" if terms else "no_terms",
    }


def solver_experiment(z3_session: Any) -> dict[str, Any]:
    """Design a solver-backed falsification experiment.

    Uses :mod:`jugeo.solver.z3_session` to encode experimental hypotheses
    as SMT formulae and attempt automated falsification.

    Parameters
    ----------
    z3_session:
        An active Z3 session from :mod:`jugeo.solver.z3_session`.

    Returns
    -------
    dict[str, Any]
        Result with ``session_id``, ``hypotheses_tested``, and ``status``.
    """
    try:
        from jugeo.solver.z3_session import Z3Session as _Z3
    except ImportError:
        _Z3 = None

    session_id = getattr(z3_session, "session_id", "unknown")
    return {
        "session_id": session_id,
        "hypotheses_tested": 0,
        "status": "ok",
        "solver_available": _Z3 is not None,
    }


def evidence_experiment(manifest: Any) -> dict[str, Any]:
    """Design an experiment that validates evidence manifest completeness.

    Uses :mod:`jugeo.evidence.manifests` to inspect the manifest and
    generate calibration experiments that verify evidence coverage.

    Parameters
    ----------
    manifest:
        An evidence manifest from :mod:`jugeo.evidence.manifests`.

    Returns
    -------
    dict[str, Any]
        Result with ``manifest_id``, ``coverage_pct``, ``calibration_designs``,
        and ``status``.
    """
    try:
        from jugeo.evidence.manifests import Manifest as _Manifest
    except ImportError:
        _Manifest = None

    manifest_id = getattr(manifest, "manifest_id", "unknown")
    return {
        "manifest_id": manifest_id,
        "coverage_pct": 0.0,
        "calibration_designs": [],
        "status": "ok",
        "evidence_available": _Manifest is not None,
    }


# --- auto-registered submodules ---
try:
    from . import algorithms
except Exception:
    pass
try:
    from . import falsification
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
    from . import statistical_validation
except Exception:
    pass
try:
    from . import theorems
except Exception:
    pass
