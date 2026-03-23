"""
Integration layer connecting the scaling_limits package with the broader
JuGeo evaluation pipeline: evaluation_design and methodology_loops modules.

copilot: shared-core marker
Theory reference: theory2.tex Ch64

This module provides the glue code that allows the scaling_limits analysis
pipeline to interoperate with the top-level evaluation infrastructure used
by JuGeo.  Concretely it defines three integration points:

1.  ``ScalingLimitsIntegration`` – the main facade that orchestrates a
    complete scaling-limits analysis run and exposes its results to the
    rest of the evaluation pipeline.  It holds a reference to the
    ``Orchestrator`` instance (if available) and exposes methods for
    attaching to that orchestrator, running integrated analyses, collecting
    evidence, and building manifests.

2.  ``EvaluationDesignBridge`` – a lightweight adapter that translates
    between the result schema used internally by scaling_limits and the
    schema expected by the ``EvaluationDesign`` abstraction.  It also
    supports a round-trip: pushing results into a design object and pulling
    existing design configuration back out.

3.  ``MethodologyLoopsBridge`` – a companion adapter for the
    ``MethodologyLoop`` abstraction that exposes detected phase changes and
    fitted scaling laws as structured loop-state updates.

The two free functions at the bottom (``integrate_with_evaluation_design``
and ``create_evidence_record``) provide simple one-call entry points for
the most common integration scenarios.

Theory note: The integration architecture described here is formalised in
theory2.tex Ch64 §64.7 "Pipeline Integration Contracts".  That section
defines the interface contracts that every evaluation sub-package must
satisfy in order to plug into the shared evidence chain.

Usage example::

    from jugeo.evaluation.scaling_limits.integration import (
        integrate_with_evaluation_design,
    )

    integration = integrate_with_evaluation_design(config={"sensitivity": 2.5})
    result = integration.run_integrated_analysis(
        design_id="exp-001",
        xs=[1, 2, 4, 8, 16],
        ys=[0.1, 0.4, 1.6, 6.4, 25.6],
    )
    print(result["summary"])

Compatibility: Python 3.11+.
No external dependencies — stdlib only.
"""

from __future__ import annotations

__all__ = [
    "ScalingLimitsIntegration",
    "EvaluationDesignBridge",
    "MethodologyLoopsBridge",
    "integrate_with_evaluation_design",
    "create_evidence_record",
]

import json
import math
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Cross-module guarded imports (evidence, packs, orchestration, geometry)
# ---------------------------------------------------------------------------
try:
    from jugeo.evidence.manifests import Manifest, build_evidence_manifest
    from jugeo.evidence.trust import TrustProfile, TrustTier, join_trust_profiles
    from jugeo.evidence.channels import EvidenceRecord, EvidenceKind, build_channel
    from jugeo.evidence.provenance import ProvenanceTrace
    from jugeo.packs.bridges import BridgeTheorem, BridgeRegistry, BridgeComposer
    from jugeo.packs.authority import PackAuthority, PackAuthorityRegistry
    from jugeo.packs.catalog import PackDescriptor
    from jugeo.orchestration.controller import Orchestrator, OrchestratorState
    from jugeo.ideation.ideas import IdeaProposal, TrustStatus
    from jugeo.ideation.regimes import Regime, RegimeCatalog
    from jugeo.ideation.novelty import NoveltyScore
    from jugeo.geometry.site import Site, Coordinate
    from jugeo.geometry.descent import DescentResult, GlobalSection
except Exception:
    pass

# ---------------------------------------------------------------------------
# Guarded imports from scaling_limits submodules
# ---------------------------------------------------------------------------
try:
    from jugeo.evaluation.scaling_limits.models import (
        ComplexityClass, ScalingRegime, PhaseKind, LimitKind,
        ComplexityBound, PhaseChange, ScalingLaw, LimitCertificate,
        ComplexityAnalyzer, PhaseChangeDetector, ScalingLawFitter, FundamentalLimits,
    )
    from jugeo.evaluation.scaling_limits.manifest import (
        ScalingLimitsManifest, ScalingManifestBuilder, build_scaling_manifest,
    )
    from jugeo.evaluation.scaling_limits.algorithms import ScalingAlgorithms
    from jugeo.evaluation.scaling_limits.complexity_analysis import (
        ComplexityMeasurer, AsymptoticAnalyzer, BoundDeriver, ComplexityAnalysisRunner,
        run_complexity_analysis, derive_bounds,
    )
    from jugeo.evaluation.scaling_limits.phase_changes import (
        PhaseChangeScanner, TransitionPointFinder, PhaseCharacterizer, PhaseChangeRunner,
        detect_phase_changes, characterize_phases,
    )
    from jugeo.evaluation.scaling_limits.scaling_laws import (
        PowerLawFitter, ExponentialLawFitter, ScalingLawValidator, ScalingLawRunner,
        fit_scaling_law, validate_scaling_law,
    )
    from jugeo.evaluation.scaling_limits.theorems import (
        ComplexityBoundTheoremClass, PhaseChangeDetectionSoundnessTheorem,
        ScalingLawValidityTheorem, FundamentalLimitSharpnessTheorem, NoFreeScalingTheorem,
        ScalingTheoremRegistry, DEFAULT_THEOREM_REGISTRY,
    )
except Exception:
    pass

# ---------------------------------------------------------------------------
# Guarded imports from evaluation_design and methodology_loops
# ---------------------------------------------------------------------------
try:
    from jugeo.evaluation.evaluation_design import EvaluationDesign
except Exception:
    pass

try:
    from jugeo.evaluation.methodology_loops import MethodologyLoop
except Exception:
    pass


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

#: Default sensitivity parameter used when none is provided in config.
DEFAULT_SENSITIVITY: float = 2.0

#: Minimum number of data points required for a valid integrated analysis.
MIN_ANALYSIS_POINTS: int = 3

#: Version of the integration contract implemented by this module.
#: Bump when the schema of result dicts or manifest fields changes.
INTEGRATION_CONTRACT_VERSION: str = "1.0.0"

#: Maximum number of results retained per ``ScalingLimitsIntegration`` instance
#: before older results are dropped (FIFO eviction).
MAX_RESULTS_HISTORY: int = 1000


# ---------------------------------------------------------------------------
# Module-level helpers (as required by the project style guide)
# ---------------------------------------------------------------------------

def _utcnow() -> str:
    """Return the current UTC timestamp as an ISO-8601 string.

    This helper ensures that all timestamp fields created within the
    integration module use a consistent format that is sortable and
    directly comparable with timestamps produced by other JuGeo packages.

    Returns
    -------
    str
        Current UTC time formatted as ``YYYY-MM-DDTHH:MM:SS``.
    """
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())


def _uid() -> str:
    """Generate a new random UUID-4 hex string.

    Provides unique identifiers for analysis runs, evidence records, and
    bridge transactions without depending on a central counter or
    wall-clock uniqueness.

    Returns
    -------
    str
        A UUID-4 value formatted as a lowercase 32-character hex string.
    """
    return uuid.uuid4().hex


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp *value* to the closed interval [lo, hi].

    A pure arithmetic helper used throughout this module to keep numeric
    scores (confidence, quality, coverage) within expected ranges and
    prevent downstream consumers from receiving out-of-range values.

    Parameters
    ----------
    value:
        The raw numeric value to clamp.
    lo:
        Lower bound of the allowed interval (inclusive).
    hi:
        Upper bound of the allowed interval (inclusive).

    Returns
    -------
    float
        The clamped value satisfying ``lo <= result <= hi``.
    """
    return max(lo, min(hi, value))


def _safe_log(x: float) -> float:
    """Return log(x) safely, returning 0.0 for non-positive inputs.

    Used in metric computations where the input might be zero or negative
    due to noisy data, avoiding ValueError from math.log on bad inputs.

    Parameters
    ----------
    x:
        Input value.

    Returns
    -------
    float
        Natural logarithm of x, or 0.0 if x <= 0.
    """
    if x <= 0:
        return 0.0
    return math.log(x)


def _describe_xs_ys(xs: list, ys: list) -> dict[str, Any]:
    """Compute basic descriptive statistics for xs and ys lists.

    Calculates min, max, mean, and range for both the independent variable
    xs and the dependent variable ys.  Used internally by the integration
    facade to populate result metadata and log entries.

    Parameters
    ----------
    xs:
        List of x (independent variable) values.
    ys:
        List of y (dependent variable) values.

    Returns
    -------
    dict
        A dictionary with keys ``n``, ``x_min``, ``x_max``, ``x_mean``,
        ``y_min``, ``y_max``, ``y_mean``, ``x_range``, ``y_range``.
    """
    n = len(xs)
    if n == 0:
        return {"n": 0}

    float_xs = [float(v) for v in xs]
    float_ys = [float(v) for v in ys]

    return {
        "n": n,
        "x_min": min(float_xs),
        "x_max": max(float_xs),
        "x_mean": sum(float_xs) / n,
        "y_min": min(float_ys),
        "y_max": max(float_ys),
        "y_mean": sum(float_ys) / n,
        "x_range": max(float_xs) - min(float_xs),
        "y_range": max(float_ys) - min(float_ys),
    }


# ---------------------------------------------------------------------------
# Main integration facade
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class ScalingLimitsIntegration:
    """Facade that orchestrates a complete scaling-limits analysis run.

    Theory reference: theory2.tex Ch64, §64.7.

    This class is the primary entry point for integrating the scaling_limits
    sub-package into a larger evaluation pipeline.  It holds configuration
    for the analysis, an optional reference to an ``Orchestrator`` instance,
    and an accumulated list of analysis results from previous runs.

    The typical lifecycle is:

    1. Construct (or use :meth:`create`) with a config dict.
    2. Optionally call :meth:`attach_to_orchestrator` with the running
       orchestrator so that results are routed through the evidence chain.
    3. Call :meth:`run_integrated_analysis` one or more times with
       different ``design_id`` / data combinations.
    4. Call :meth:`collect_evidence` and :meth:`build_manifest` to package
       the results for downstream consumers.
    5. Call :meth:`summarize` to get a human-readable summary dict.

    Attributes
    ----------
    config : dict
        Analysis configuration dict.  Recognised keys include:
        ``sensitivity`` (float, default 2.0), ``min_r2`` (float, default 0.8),
        ``law_form`` (str, default ``"power"``), ``max_phase_changes`` (int).
    orchestrator : object or None
        Optional reference to a JuGeo ``Orchestrator`` instance.
    _results : list
        Accumulated list of result dicts from previous analysis runs.
    """

    config: dict = field(default_factory=dict)
    orchestrator: object = field(default=None)
    _results: list = field(default_factory=list)

    # ------------------------------------------------------------------
    def attach_to_orchestrator(self, orch: Any) -> None:
        """Attach this integration to a running ``Orchestrator`` instance.

        Storing a reference to the orchestrator allows :meth:`run_integrated_analysis`
        to route results through the evidence chain and register artefacts
        with the orchestrator's state store.  If *orch* is ``None`` the
        method is a no-op and subsequent analysis runs will simply
        accumulate results locally without orchestrator routing.

        Calling this method more than once replaces the previous orchestrator
        reference without raising an error, allowing callers to re-attach to
        a new orchestrator when the analysis context changes.

        Parameters
        ----------
        orch:
            An ``Orchestrator``-compatible object (or ``None`` to detach).
        """
        # Store the orchestrator reference for later use
        object.__setattr__(self, "orchestrator", orch)

    # ------------------------------------------------------------------
    def run_integrated_analysis(
        self,
        design_id: str,
        xs: list,
        ys: list,
    ) -> dict[str, Any]:
        """Run a full scaling-limits analysis for the given data and design ID.

        This method is the primary workhorse of the integration facade.  It
        performs the following steps in order:

        1. Validates that ``xs`` and ``ys`` are non-empty lists of the same
           length with at least ``MIN_ANALYSIS_POINTS`` elements.
        2. Attempts to call :func:`detect_phase_changes` with the configured
           sensitivity parameter; falls back to a simple heuristic if the
           function is not available.
        3. Attempts to call :func:`fit_scaling_law` with the configured law
           form; falls back to a linear regression in log-log space.
        4. Attempts to call :func:`run_complexity_analysis` on the ys series;
           falls back to a trivial bound estimate.
        5. Assembles all sub-results into a unified result dict and appends
           it to ``self._results``.
        6. If an orchestrator is attached, pushes a lightweight notification
           to it (no-op if the orchestrator API is unavailable).

        Parameters
        ----------
        design_id:
            A string identifier for the evaluation design that requested
            this analysis (used for logging and manifest generation).
        xs:
            Ordered list of independent-variable values (e.g. input sizes).
        ys:
            Ordered list of dependent-variable values (e.g. runtimes).

        Returns
        -------
        dict
            A result dict with keys: ``run_id``, ``design_id``, ``timestamp``,
            ``stats``, ``phase_changes``, ``scaling_law``, ``complexity_bounds``,
            ``quality_score``.
        """
        # --- Input validation ---
        if len(xs) != len(ys):
            raise ValueError(
                f"xs and ys must have the same length; got {len(xs)} and {len(ys)}."
            )
        if len(xs) < MIN_ANALYSIS_POINTS:
            raise ValueError(
                f"At least {MIN_ANALYSIS_POINTS} data points are required; got {len(xs)}."
            )

        run_id = _uid()
        sensitivity = float(self.config.get("sensitivity", DEFAULT_SENSITIVITY))
        law_form = str(self.config.get("law_form", "power"))

        # --- Descriptive statistics ---
        stats = _describe_xs_ys(xs, ys)

        # --- Phase change detection ---
        phase_changes: list = []
        try:
            phase_changes = detect_phase_changes(  # type: ignore[name-defined]
                xs, ys, sensitivity=sensitivity
            )
        except Exception:
            # Fallback: naïve first-difference heuristic
            phase_changes = self._fallback_phase_detect(xs, ys, sensitivity)

        # --- Scaling law fitting ---
        scaling_law: Any = None
        try:
            scaling_law = fit_scaling_law(xs, ys, form=law_form)  # type: ignore[name-defined]
        except Exception:
            scaling_law = self._fallback_fit_law(xs, ys)

        # --- Complexity bound estimation ---
        complexity_bounds: list = []
        try:
            bounds_result = run_complexity_analysis(  # type: ignore[name-defined]
                components={"main": ys}, sizes=list(xs)
            )
            complexity_bounds = bounds_result if isinstance(bounds_result, list) else [bounds_result]
        except Exception:
            complexity_bounds = self._fallback_complexity_bounds(xs, ys)

        # --- Quality score (composite heuristic) ---
        quality_score = self._compute_quality(phase_changes, scaling_law, stats)

        # --- Assemble result ---
        result: dict[str, Any] = {
            "run_id": run_id,
            "design_id": design_id,
            "timestamp": _utcnow(),
            "stats": stats,
            "phase_changes": phase_changes,
            "scaling_law": (
                scaling_law.to_dict()
                if hasattr(scaling_law, "to_dict")
                else scaling_law
            ),
            "complexity_bounds": [
                (b.to_dict() if hasattr(b, "to_dict") else b)
                for b in complexity_bounds
            ],
            "quality_score": quality_score,
        }

        # --- Accumulate (with eviction) ---
        self._results.append(result)
        if len(self._results) > MAX_RESULTS_HISTORY:
            self._results.pop(0)

        # --- Notify orchestrator (best-effort) ---
        self._notify_orchestrator(run_id, design_id)

        return result

    # ------------------------------------------------------------------
    def _fallback_phase_detect(
        self, xs: list, ys: list, sensitivity: float
    ) -> list[dict[str, Any]]:
        """Naïve first-difference phase detector used as fallback.

        Computes the first differences of the ys series, normalises them by
        the standard deviation, and flags indices where the normalised
        difference exceeds the sensitivity threshold.  Used when the
        full :func:`detect_phase_changes` implementation is not importable.

        Parameters
        ----------
        xs:
            Independent-variable values.
        ys:
            Dependent-variable values.
        sensitivity:
            Threshold multiplier applied to the standard deviation.

        Returns
        -------
        list[dict]
            List of minimal phase-change dicts (keys: index, x, magnitude).
        """
        float_ys = [float(v) for v in ys]
        n = len(float_ys)
        if n < 2:
            return []

        diffs = [abs(float_ys[i + 1] - float_ys[i]) for i in range(n - 1)]
        mean_d = sum(diffs) / len(diffs)
        # Compute std dev manually (avoid importing statistics here)
        var_d = sum((d - mean_d) ** 2 for d in diffs) / len(diffs)
        std_d = math.sqrt(var_d) if var_d > 0 else 1.0
        threshold = mean_d + sensitivity * std_d

        results = []
        for i, d in enumerate(diffs):
            if d > threshold:
                results.append({
                    "transition_index": i + 1,
                    "x": float(xs[i + 1]),
                    "magnitude": d,
                    "confidence": _clamp(d / (threshold + 1e-12), 0.0, 1.0),
                    "kind": "discontinuity",
                })
        return results

    # ------------------------------------------------------------------
    def _fallback_fit_law(self, xs: list, ys: list) -> dict[str, Any]:
        """Fit a simple power law in log-log space as a fallback.

        When the full :func:`fit_scaling_law` implementation is not
        available this method performs an OLS regression on the log-log
        transformed data to estimate the power-law exponent b in y = a*x^b.
        The result is returned as a plain dict compatible with the
        ``ScalingLaw`` schema.

        Parameters
        ----------
        xs:
            Independent-variable values (must be positive).
        ys:
            Dependent-variable values (must be positive).

        Returns
        -------
        dict
            A minimal scaling-law dict with keys: ``form``, ``exponent``,
            ``coefficient``, ``r_squared``.
        """
        # Filter to strictly positive pairs
        pairs = [
            (math.log(float(x)), math.log(float(y)))
            for x, y in zip(xs, ys)
            if float(x) > 0 and float(y) > 0
        ]
        if len(pairs) < 2:
            return {"form": "power", "exponent": 1.0, "coefficient": 1.0, "r_squared": 0.0}

        n = len(pairs)
        lx = [p[0] for p in pairs]
        ly = [p[1] for p in pairs]
        mean_lx = sum(lx) / n
        mean_ly = sum(ly) / n

        num = sum((lx[i] - mean_lx) * (ly[i] - mean_ly) for i in range(n))
        den = sum((lx[i] - mean_lx) ** 2 for i in range(n))

        b = num / den if den != 0 else 1.0
        a = math.exp(mean_ly - b * mean_lx)

        # Compute R^2
        ss_tot = sum((ly[i] - mean_ly) ** 2 for i in range(n))
        ss_res = sum((ly[i] - (math.log(a) + b * lx[i])) ** 2 for i in range(n))
        r2 = 1.0 - (ss_res / ss_tot) if ss_tot > 0 else 0.0

        return {
            "form": "power",
            "exponent": b,
            "coefficient": a,
            "r_squared": _clamp(r2, 0.0, 1.0),
        }

    # ------------------------------------------------------------------
    def _fallback_complexity_bounds(
        self, xs: list, ys: list
    ) -> list[dict[str, Any]]:
        """Derive a trivial upper complexity bound as fallback.

        Estimates an upper bound on the growth rate by computing the ratio
        of the last observed y-value to the first, then normalising by the
        growth in x.  This gives a crude but correct O(n^exponent) bound.

        Parameters
        ----------
        xs:
            Independent-variable values.
        ys:
            Dependent-variable values.

        Returns
        -------
        list[dict]
            A single-element list containing a minimal complexity bound dict.
        """
        try:
            x0, x1 = float(xs[0]), float(xs[-1])
            y0, y1 = float(ys[0]), float(ys[-1])
            if x0 <= 0 or y0 <= 0 or x1 <= x0:
                return [{"label": "O(n)", "upper_coeff": 1.0, "exponent": 1.0}]
            exponent = _safe_log(y1 / y0) / _safe_log(x1 / x0)
            upper_coeff = y1 / (x1 ** exponent) if x1 > 0 else 1.0
            return [{"label": f"O(n^{exponent:.2f})", "upper_coeff": upper_coeff, "exponent": exponent}]
        except Exception:
            return [{"label": "O(n)", "upper_coeff": 1.0, "exponent": 1.0}]

    # ------------------------------------------------------------------
    def _compute_quality(
        self, phase_changes: list, scaling_law: Any, stats: dict
    ) -> float:
        """Compute a composite quality score for an analysis run.

        Combines three sub-scores:

        - **Coverage score**: proportion of the xs range that is covered by
          the analysis (always 1.0 when the full dataset is used).
        - **Law quality**: the R^2 of the fitted scaling law (or 0.5 if not
          available).
        - **Consistency score**: 1.0 if fewer than 3 phase changes are detected
          per 10 data points (suggesting a stable regime), else reduced.

        The composite is the geometric mean of the three sub-scores, clamped
        to [0, 1].

        Parameters
        ----------
        phase_changes:
            Detected phase changes from the analysis run.
        scaling_law:
            Fitted scaling law (dict or object with ``r_squared`` attribute).
        stats:
            Descriptive statistics dict produced by :func:`_describe_xs_ys`.

        Returns
        -------
        float
            Composite quality score in [0, 1].
        """
        n = int(stats.get("n", 1))

        # Coverage: trivially 1.0 since we analyse the whole dataset
        coverage = 1.0

        # Law quality
        if isinstance(scaling_law, dict):
            r2 = float(scaling_law.get("r_squared", 0.5))
        else:
            r2 = float(getattr(scaling_law, "r_squared", 0.5))
        law_quality = _clamp(r2, 0.0, 1.0)

        # Consistency
        n_changes = len(phase_changes)
        changes_per_10 = (n_changes / max(n, 1)) * 10
        consistency = _clamp(1.0 - changes_per_10 / 10.0, 0.0, 1.0)

        # Geometric mean
        product = coverage * law_quality * consistency
        quality = product ** (1.0 / 3.0) if product > 0 else 0.0
        return _clamp(quality, 0.0, 1.0)

    # ------------------------------------------------------------------
    def _notify_orchestrator(self, run_id: str, design_id: str) -> None:
        """Send a best-effort notification to the attached orchestrator.

        If an orchestrator is attached and has a ``notify`` or ``record``
        method, this method calls it with a minimal event payload.  Any
        exception is silently swallowed so that orchestrator failures never
        interrupt the analysis pipeline.

        Parameters
        ----------
        run_id:
            The unique identifier of the analysis run just completed.
        design_id:
            The design identifier associated with the run.
        """
        orch = self.orchestrator
        if orch is None:
            return
        payload = {
            "event": "scaling_limits_run_completed",
            "run_id": run_id,
            "design_id": design_id,
            "timestamp": _utcnow(),
        }
        try:
            if hasattr(orch, "notify"):
                orch.notify(payload)
            elif hasattr(orch, "record"):
                orch.record(payload)
        except Exception:
            pass

    # ------------------------------------------------------------------
    def collect_evidence(self) -> list[dict[str, Any]]:
        """Collect and return all accumulated analysis results as evidence records.

        Wraps each accumulated result dict in a minimal evidence-record
        envelope that is compatible with the JuGeo evidence subsystem.
        If the full ``EvidenceRecord`` / ``build_channel`` API is available
        it is used; otherwise a plain dict envelope is returned.

        Each evidence record includes the original result plus metadata:
        ``kind`` (set to ``"scaling_limits_run"``), ``timestamp``, and a
        unique ``record_id``.

        Returns
        -------
        list[dict]
            A list of evidence-record dicts, one per accumulated result.
        """
        records = []
        for result in self._results:
            record = {
                "record_id": _uid(),
                "kind": "scaling_limits_run",
                "timestamp": _utcnow(),
                "payload": result,
            }
            # Attempt to wrap in a proper EvidenceRecord if available
            try:
                full_record = build_channel(  # type: ignore[name-defined]
                    kind="scaling_limits_run",
                    payload=result,
                )
                records.append(full_record)
            except Exception:
                records.append(record)
        return records

    # ------------------------------------------------------------------
    def build_manifest(self) -> Any:
        """Build and return a ``ScalingLimitsManifest`` from accumulated results.

        Attempts to call :func:`build_scaling_manifest` with the current
        configuration and evidence records.  If that function is not
        available a plain dict representation of the manifest is returned
        instead, preserving all relevant metadata.

        Returns
        -------
        ScalingLimitsManifest or dict
            A manifest object (or plain dict) summarising all analysis runs
            performed by this integration instance.
        """
        evidence = self.collect_evidence()
        manifest_data: dict[str, Any] = {
            "manifest_id": _uid(),
            "generated_at": _utcnow(),
            "contract_version": INTEGRATION_CONTRACT_VERSION,
            "config": dict(self.config),
            "n_runs": len(self._results),
            "evidence_records": evidence,
        }
        try:
            return build_scaling_manifest(  # type: ignore[name-defined]
                config=self.config,
                evidence=evidence,
            )
        except Exception:
            return manifest_data

    # ------------------------------------------------------------------
    def summarize(self) -> dict[str, Any]:
        """Return a human-readable summary of all accumulated analysis runs.

        Aggregates the key metrics across all stored results into a single
        compact dict.  The summary includes the total number of runs, the
        average quality score, the total number of phase changes detected,
        the most common law form, and the time range of the runs.

        This method is designed to be called at the end of a session to
        give operators a quick health-check view of the analysis results
        without requiring them to inspect every individual result dict.

        Returns
        -------
        dict
            A summary dict with aggregated statistics across all runs.
        """
        if not self._results:
            return {"n_runs": 0, "message": "No analysis runs recorded yet."}

        quality_scores = [float(r.get("quality_score", 0.0)) for r in self._results]
        avg_quality = sum(quality_scores) / len(quality_scores)

        total_phase_changes = sum(
            len(r.get("phase_changes", [])) for r in self._results
        )

        # Tally law forms
        law_forms: dict[str, int] = {}
        for r in self._results:
            sl = r.get("scaling_law") or {}
            form = sl.get("form", "unknown") if isinstance(sl, dict) else "unknown"
            law_forms[form] = law_forms.get(form, 0) + 1

        most_common_form = max(law_forms, key=lambda k: law_forms[k]) if law_forms else "unknown"

        timestamps = [r.get("timestamp", "") for r in self._results if r.get("timestamp")]

        return {
            "n_runs": len(self._results),
            "avg_quality_score": round(avg_quality, 4),
            "total_phase_changes_detected": total_phase_changes,
            "most_common_law_form": most_common_form,
            "law_form_counts": law_forms,
            "earliest_run": min(timestamps) if timestamps else None,
            "latest_run": max(timestamps) if timestamps else None,
            "contract_version": INTEGRATION_CONTRACT_VERSION,
        }

    # ------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        """Serialise this integration instance to a JSON-compatible dictionary.

        Includes the configuration, the number of accumulated results (but
        not the full result list, which can be large), and the contract
        version.  Use :meth:`collect_evidence` to obtain the full result list.

        Returns
        -------
        dict
            A serialisable snapshot of this integration instance's state.
        """
        return {
            "__class__": "ScalingLimitsIntegration",
            "config": dict(self.config),
            "has_orchestrator": self.orchestrator is not None,
            "n_results": len(self._results),
            "contract_version": INTEGRATION_CONTRACT_VERSION,
            "snapshot_at": _utcnow(),
        }

    # ------------------------------------------------------------------
    @classmethod
    def create(cls, config: dict) -> "ScalingLimitsIntegration":
        """Factory classmethod that constructs a ``ScalingLimitsIntegration``.

        Provides a convenient one-call construction pattern that validates
        the config dict and fills in defaults for missing keys before
        constructing the instance.  Recognised config keys and their
        defaults are:

        - ``sensitivity`` (float, default 2.0): detection sensitivity.
        - ``law_form`` (str, default ``"power"``): default law form.
        - ``min_r2`` (float, default 0.8): minimum acceptable R^2.
        - ``max_phase_changes`` (int, default 20): cap on detected changes.

        Parameters
        ----------
        config:
            Configuration dict (may be empty; defaults are applied).

        Returns
        -------
        ScalingLimitsIntegration
            A new, fully configured integration instance.
        """
        defaults = {
            "sensitivity": DEFAULT_SENSITIVITY,
            "law_form": "power",
            "min_r2": 0.8,
            "max_phase_changes": 20,
        }
        merged = {**defaults, **config}
        return cls(config=merged)

    # ------------------------------------------------------------------
    def __repr__(self) -> str:
        return (
            f"ScalingLimitsIntegration("
            f"n_results={len(self._results)}, "
            f"has_orchestrator={self.orchestrator is not None}, "
            f"config_keys={sorted(self.config.keys())})"
        )

    # ------------------------------------------------------------------
    def __str__(self) -> str:
        return (
            f"ScalingLimitsIntegration with {len(self._results)} run(s), "
            f"quality summary: {self.summarize().get('avg_quality_score', 'n/a')}"
        )


# ---------------------------------------------------------------------------
# EvaluationDesign bridge
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class EvaluationDesignBridge:
    """Adapter translating between scaling_limits results and EvaluationDesign schema.

    Theory reference: theory2.tex Ch64, §64.7 "Pipeline Integration Contracts".

    The ``EvaluationDesign`` abstraction used at the top level of the JuGeo
    evaluation pipeline has its own schema for experiment results.  This bridge
    converts between that schema and the internal result dicts produced by
    ``ScalingLimitsIntegration``.  It supports both push (write results into a
    design object) and pull (read configuration from a design object) operations.

    Attributes
    ----------
    scaling_results : list
        Accumulated scaling-limits result dicts to be pushed to the design.
    design_config : dict
        Configuration pulled from the ``EvaluationDesign`` object (if any).
    """

    scaling_results: list = field(default_factory=list)
    design_config: dict = field(default_factory=dict)

    # ------------------------------------------------------------------
    @property
    def has_results(self) -> bool:
        """Return ``True`` if there are results ready to push to the design.

        A convenience property that lets callers check whether the bridge
        has any pending results before calling :meth:`push_to_design`,
        avoiding a no-op call when there is nothing to push.

        Returns
        -------
        bool
        """
        return len(self.scaling_results) > 0

    # ------------------------------------------------------------------
    def adapt_results(self) -> list[dict[str, Any]]:
        """Translate accumulated scaling-limits results to the EvaluationDesign schema.

        Each scaling-limits result dict is transformed into an
        ``EvaluationDesign``-compatible record by renaming and restructuring
        fields to match the design schema.  The transformation applies the
        following mappings:

        - ``run_id`` → ``experiment_id``
        - ``quality_score`` → ``score``
        - ``phase_changes`` → ``events`` (list of event dicts)
        - ``scaling_law`` → ``fitted_model``
        - ``stats`` → ``data_summary``

        Any keys not in the mapping are preserved in an ``extras`` sub-dict
        so that no information is lost during translation.

        Returns
        -------
        list[dict]
            List of EvaluationDesign-compatible result records.
        """
        adapted = []
        for r in self.scaling_results:
            extras = {
                k: v for k, v in r.items()
                if k not in {"run_id", "quality_score", "phase_changes", "scaling_law", "stats"}
            }
            record = {
                "experiment_id": r.get("run_id", _uid()),
                "score": r.get("quality_score", 0.0),
                "events": r.get("phase_changes", []),
                "fitted_model": r.get("scaling_law"),
                "data_summary": r.get("stats", {}),
                "extras": extras,
                "adapted_at": _utcnow(),
            }
            adapted.append(record)
        return adapted

    # ------------------------------------------------------------------
    def push_to_design(self, design: Any) -> bool:
        """Push adapted results into the given EvaluationDesign object.

        Calls :meth:`adapt_results` and then attempts to call the design's
        ``add_results`` or ``record`` method with the translated records.
        Returns ``True`` if the push succeeded, ``False`` otherwise.

        Parameters
        ----------
        design:
            An ``EvaluationDesign``-compatible object to receive the results.

        Returns
        -------
        bool
            ``True`` if results were successfully pushed.
        """
        if not self.has_results:
            return False
        adapted = self.adapt_results()
        try:
            if hasattr(design, "add_results"):
                design.add_results(adapted)
                return True
            if hasattr(design, "record"):
                for record in adapted:
                    design.record(record)
                return True
        except Exception:
            pass
        return False

    # ------------------------------------------------------------------
    def pull_from_design(self, design: Any) -> list[dict[str, Any]]:
        """Pull configuration and existing results from an EvaluationDesign object.

        Attempts to read configuration from the design object using
        ``get_config``, ``config``, or ``configuration`` attributes/methods.
        Updates ``self.design_config`` with whatever is found, then returns
        a list of any existing results already stored in the design.

        Parameters
        ----------
        design:
            An ``EvaluationDesign``-compatible object to pull from.

        Returns
        -------
        list[dict]
            Existing result records found in the design, or an empty list.
        """
        config_data: dict = {}
        try:
            if hasattr(design, "get_config"):
                config_data = design.get_config() or {}
            elif hasattr(design, "config"):
                config_data = dict(design.config or {})
            elif hasattr(design, "configuration"):
                config_data = dict(design.configuration or {})
        except Exception:
            pass

        object.__setattr__(self, "design_config", config_data)

        existing: list = []
        try:
            if hasattr(design, "get_results"):
                existing = list(design.get_results() or [])
            elif hasattr(design, "results"):
                existing = list(design.results or [])
        except Exception:
            pass

        return existing

    # ------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        """Serialise this bridge to a JSON-compatible dictionary."""
        return {
            "__class__": "EvaluationDesignBridge",
            "n_scaling_results": len(self.scaling_results),
            "design_config": dict(self.design_config),
            "has_results": self.has_results,
            "snapshot_at": _utcnow(),
        }

    # ------------------------------------------------------------------
    def __repr__(self) -> str:
        return (
            f"EvaluationDesignBridge("
            f"n_results={len(self.scaling_results)}, "
            f"has_results={self.has_results})"
        )

    # ------------------------------------------------------------------
    def __str__(self) -> str:
        return (
            f"EvaluationDesignBridge with {len(self.scaling_results)} result(s) "
            f"and design config keys: {sorted(self.design_config.keys())}"
        )


# ---------------------------------------------------------------------------
# MethodologyLoops bridge
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class MethodologyLoopsBridge:
    """Adapter that exposes scaling-limits artefacts as MethodologyLoop state.

    Theory reference: theory2.tex Ch64, §64.7.

    The ``MethodologyLoop`` abstraction models a feedback loop between
    evaluation steps.  This bridge builds a loop-state update dict from
    the detected phase changes and fitted scaling laws accumulated by the
    scaling_limits pipeline, and pushes that update to an active loop.

    Attributes
    ----------
    loop_id : str
        Identifier of the target methodology loop (may be empty if not
        yet associated with a loop).
    phase_changes : list
        Detected phase changes to include in loop updates.
    scaling_laws : list
        Fitted scaling laws to include in loop updates.
    """

    loop_id: str = ""
    phase_changes: list = field(default_factory=list)
    scaling_laws: list = field(default_factory=list)

    # ------------------------------------------------------------------
    @property
    def is_active(self) -> bool:
        """Return ``True`` if this bridge is associated with a loop.

        A bridge is considered active if its ``loop_id`` is non-empty,
        indicating that it has been associated with a specific
        ``MethodologyLoop`` instance.  An inactive bridge can still
        accumulate phase changes and scaling laws; it just cannot push
        updates until a loop_id is set.

        Returns
        -------
        bool
        """
        return bool(self.loop_id)

    # ------------------------------------------------------------------
    def build_loop_update(self) -> dict[str, Any]:
        """Construct a loop-state update dict from accumulated artefacts.

        Packages the currently stored phase changes and scaling laws into
        a structured update dict that conforms to the ``MethodologyLoop``
        state schema.  The dict includes:

        - ``loop_id``: the target loop identifier.
        - ``phase_changes``: serialised list of phase changes.
        - ``scaling_laws``: serialised list of scaling laws.
        - ``n_phase_changes``: count of phase changes.
        - ``n_scaling_laws``: count of scaling laws.
        - ``generated_at``: ISO-8601 timestamp.

        Returns
        -------
        dict
            A loop-state update dict ready to be pushed via :meth:`push_phase_changes`.
        """
        def _serialise(item: Any) -> Any:
            if hasattr(item, "to_dict"):
                return item.to_dict()
            if isinstance(item, dict):
                return item
            return str(item)

        return {
            "loop_id": self.loop_id,
            "phase_changes": [_serialise(pc) for pc in self.phase_changes],
            "scaling_laws": [_serialise(sl) for sl in self.scaling_laws],
            "n_phase_changes": len(self.phase_changes),
            "n_scaling_laws": len(self.scaling_laws),
            "generated_at": _utcnow(),
        }

    # ------------------------------------------------------------------
    def push_phase_changes(self, loop: Any) -> bool:
        """Push accumulated phase changes to the given MethodologyLoop.

        Builds a loop update via :meth:`build_loop_update` and attempts to
        push it to the provided loop object using ``update_state``,
        ``push_update``, or ``record_phase_changes`` methods (tried in that
        order).  Returns ``True`` if the push succeeded, ``False`` otherwise.

        Parameters
        ----------
        loop:
            A ``MethodologyLoop``-compatible object to receive the update.

        Returns
        -------
        bool
            ``True`` if the push succeeded.
        """
        update = self.build_loop_update()
        try:
            if hasattr(loop, "update_state"):
                loop.update_state(update)
                return True
            if hasattr(loop, "push_update"):
                loop.push_update(update)
                return True
            if hasattr(loop, "record_phase_changes"):
                loop.record_phase_changes(self.phase_changes)
                return True
        except Exception:
            pass
        return False

    # ------------------------------------------------------------------
    def pull_loop_state(self, loop: Any) -> dict[str, Any]:
        """Pull the current state from the given MethodologyLoop.

        Attempts to retrieve the loop's current state using ``get_state``,
        ``state``, or ``current_state`` attributes/methods and returns it
        as a plain dict.  If none of these are available an empty dict is
        returned.

        Parameters
        ----------
        loop:
            A ``MethodologyLoop``-compatible object to pull from.

        Returns
        -------
        dict
            The current loop state, or an empty dict if unavailable.
        """
        try:
            if hasattr(loop, "get_state"):
                return dict(loop.get_state() or {})
            if hasattr(loop, "state"):
                return dict(loop.state or {})
            if hasattr(loop, "current_state"):
                return dict(loop.current_state or {})
        except Exception:
            pass
        return {}

    # ------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        """Serialise this bridge to a JSON-compatible dictionary."""
        return {
            "__class__": "MethodologyLoopsBridge",
            "loop_id": self.loop_id,
            "n_phase_changes": len(self.phase_changes),
            "n_scaling_laws": len(self.scaling_laws),
            "is_active": self.is_active,
            "snapshot_at": _utcnow(),
        }

    # ------------------------------------------------------------------
    def __repr__(self) -> str:
        return (
            f"MethodologyLoopsBridge("
            f"loop_id={self.loop_id!r}, "
            f"n_phase_changes={len(self.phase_changes)}, "
            f"n_scaling_laws={len(self.scaling_laws)}, "
            f"is_active={self.is_active})"
        )

    # ------------------------------------------------------------------
    def __str__(self) -> str:
        status = "active" if self.is_active else "inactive"
        return (
            f"MethodologyLoopsBridge [{status}] loop={self.loop_id!r} "
            f"| {len(self.phase_changes)} phase changes, "
            f"{len(self.scaling_laws)} scaling laws"
        )


# ---------------------------------------------------------------------------
# Free functions
# ---------------------------------------------------------------------------

def integrate_with_evaluation_design(config: dict) -> ScalingLimitsIntegration:
    """Create a ``ScalingLimitsIntegration`` configured for use with EvaluationDesign.

    This factory function is the recommended way to obtain an integration
    instance when the caller intends to use it together with an
    ``EvaluationDesign`` object from the top-level evaluation pipeline.
    It applies EvaluationDesign-specific defaults on top of the base
    defaults from :meth:`ScalingLimitsIntegration.create` and returns a
    ready-to-use instance.

    The EvaluationDesign-specific defaults are:

    - ``min_r2``: 0.85 (slightly stricter than the base default of 0.8,
      reflecting the higher quality bar expected in design-linked analyses).
    - ``law_form``: ``"power"`` (power laws are the default form for
      evaluation-design-linked regressions per §64.7).
    - ``sensitivity``: 2.5 (slightly higher to reduce false positives in
      the typically noisier evaluation-design context).

    The config dict passed by the caller overrides any of these defaults,
    allowing fine-grained customisation while still providing sensible
    out-of-the-box behaviour.

    Parameters
    ----------
    config:
        Caller-supplied configuration dict.  May be empty; defaults
        are applied for any missing keys.

    Returns
    -------
    ScalingLimitsIntegration
        A new, fully configured integration instance suitable for use with
        ``EvaluationDesign``.

    Examples
    --------
    >>> integration = integrate_with_evaluation_design({"sensitivity": 3.0})
    >>> result = integration.run_integrated_analysis("exp-001", [1,2,4], [1,4,16])
    """
    # EvaluationDesign-specific defaults
    ed_defaults: dict[str, Any] = {
        "sensitivity": 2.5,
        "law_form": "power",
        "min_r2": 0.85,
        "max_phase_changes": 20,
        "context": "evaluation_design",
    }
    merged = {**ed_defaults, **config}
    return ScalingLimitsIntegration.create(config=merged)


def create_evidence_record(result: dict) -> dict[str, Any]:
    """Wrap a raw analysis result in a minimal evidence-record envelope.

    Converts a plain result dict (as produced by
    :meth:`ScalingLimitsIntegration.run_integrated_analysis`) into an
    evidence-record dict that is compatible with the JuGeo evidence
    subsystem schema.  If the full ``EvidenceRecord`` / ``build_channel``
    API is available the envelope is further enriched with provenance
    metadata; otherwise a plain dict envelope is returned.

    The envelope adds the following fields to the result:

    - ``record_id``: a new UUID-4 hex string.
    - ``kind``: always ``"scaling_limits_run"``.
    - ``schema_version``: the current ``INTEGRATION_CONTRACT_VERSION``.
    - ``wrapped_at``: UTC timestamp of when the envelope was created.
    - ``source_module``: always ``"jugeo.evaluation.scaling_limits.integration"``.

    This function is intentionally stateless and idempotent: calling it
    multiple times on the same result dict produces independent evidence
    records (each with a distinct ``record_id`` and ``wrapped_at``).

    Parameters
    ----------
    result:
        A result dict as produced by
        :meth:`ScalingLimitsIntegration.run_integrated_analysis` or any
        compatible dict with at minimum a ``run_id`` key.

    Returns
    -------
    dict
        An evidence-record dict wrapping the supplied result.

    Examples
    --------
    >>> record = create_evidence_record({"run_id": "abc123", "quality_score": 0.95})
    >>> assert record["kind"] == "scaling_limits_run"
    >>> assert len(record["record_id"]) == 32
    """
    envelope: dict[str, Any] = {
        "record_id": _uid(),
        "kind": "scaling_limits_run",
        "schema_version": INTEGRATION_CONTRACT_VERSION,
        "wrapped_at": _utcnow(),
        "source_module": "jugeo.evaluation.scaling_limits.integration",
        "payload": result,
    }

    # Attempt to enrich with provenance if available
    try:
        trace = ProvenanceTrace(  # type: ignore[name-defined]
            source="scaling_limits.integration",
            created_at=_utcnow(),
            run_id=str(result.get("run_id", "")),
        )
        envelope["provenance"] = trace.to_dict() if hasattr(trace, "to_dict") else str(trace)
    except Exception:
        pass

    return envelope
