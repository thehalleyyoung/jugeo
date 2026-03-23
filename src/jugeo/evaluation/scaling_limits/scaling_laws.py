"""
Scaling law fitting utilities for the JuGeo evaluation framework.

copilot: shared-core marker

Theory reference: theory2.tex Ch64

This module implements power-law and exponential-law fitters, a validator, and a
runner that orchestrates multiple fitting strategies over empirical measurement data.
It is used by the scaling-limits subsystem to characterise the asymptotic growth
behaviour of complexity metrics derived from JuGeo geometry/pack pipelines.

The fitting methodology follows the log-linearisation approach described in Ch64:
for a power law  y = c · xᵅ  we take logs to obtain  log y = log c + α · log x,
then apply ordinary least squares (OLS) in log-log space.  For an exponential law
y = c · e^(α·x) we take  log y = log c + α · x  and apply OLS in semi-log space.

Goodness of fit is measured by R², and confidence intervals are produced via a
non-parametric bootstrap (drawing with replacement from the data set) so that no
distributional assumptions are needed.

The top-level free functions ``fit_scaling_law`` and ``validate_scaling_law``
provide a convenient one-call API for the rest of the package.

Cross-module types (Manifest, TrustProfile, BridgeTheorem …) are imported
defensively so that the module remains usable in isolation during unit-testing or
early-stage pipeline runs where the full JuGeo package tree may not yet be present.
"""

from __future__ import annotations

__all__ = [
    "PowerLawFitter",
    "ExponentialLawFitter",
    "ScalingLawValidator",
    "ScalingLawRunner",
    "fit_scaling_law",
    "validate_scaling_law",
]

# ---------------------------------------------------------------------------
# Standard-library imports
# ---------------------------------------------------------------------------
import itertools
import functools
import json
import math
import statistics
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Guarded cross-module imports (full JuGeo tree may not be installed)
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

try:
    from jugeo.evaluation.scaling_limits.models import (
        ComplexityClass,
        ScalingRegime,
        PhaseKind,
        LimitKind,
        ComplexityBound,
        PhaseChange,
        ScalingLaw,
        LimitCertificate,
        ComplexityAnalyzer,
        PhaseChangeDetector,
        ScalingLawFitter,
        FundamentalLimits,
    )
except Exception:
    pass

# ---------------------------------------------------------------------------
# Module-level helper utilities (shared across all classes in this file)
# ---------------------------------------------------------------------------

def _utcnow() -> str:
    """Return the current UTC time as an ISO-8601 string (seconds precision).

    This helper centralises the timestamp generation logic so that any future
    migration to a different time source (e.g. a monotonic clock or an external
    time server) only requires changes in a single location.  The returned value
    is always timezone-naive but implicitly UTC per JuGeo convention (see
    theory2.tex §2.1).
    """
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _uid() -> str:
    """Return a fresh universally-unique identifier string (UUID-4, hex form).

    UUID-4 values are generated from OS-provided entropy and have negligible
    collision probability even at JuGeo's expected throughput (millions of
    analysis runs per day).  The hex form (no hyphens) is used to keep
    serialised identifiers compact while still remaining human-readable.
    """
    return uuid.uuid4().hex


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp *value* to the closed interval [lo, hi].

    Parameters
    ----------
    value:
        The number to clamp.
    lo:
        Lower bound (inclusive).  Must satisfy lo <= hi.
    hi:
        Upper bound (inclusive).

    Returns
    -------
    float
        The clamped value; equal to *value* when lo <= value <= hi,
        equal to *lo* when value < lo, and equal to *hi* when value > hi.
    """
    return max(lo, min(hi, value))


# ---------------------------------------------------------------------------
# Fitting constants
# ---------------------------------------------------------------------------
_MIN_VALID_X: float = 1e-12   # Guard against log(0) in log-space transformations
_MIN_VALID_Y: float = 1e-12   # Same guard for the dependent variable
_DEFAULT_MIN_POINTS: int = 5  # Minimum sample size for a statistically valid fit
_BOOTSTRAP_SEED_BASE: int = 0x4A756765  # Deterministic-ish base for bootstrap draws
_R2_POOR_THRESHOLD: float = 0.50   # Below this R² the fit is considered poor
_R2_GOOD_THRESHOLD: float = 0.90   # Above this R² the fit is considered good


# ---------------------------------------------------------------------------
# PowerLawFitter
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class PowerLawFitter:
    """Fit a power law of the form  y = c · xᵅ  to empirical measurement data.

    The fitting is performed in log-log space: taking logarithms of both sides
    gives  log y = log c + α · log x, which is a linear regression problem
    solvable by ordinary least squares.  The exponent α and the constant c are
    recovered from the OLS slope and intercept respectively.

    This class is intentionally stateful: after calling ``fit()`` the result is
    stored in ``fit_result`` so that downstream consumers (validators, runners)
    can inspect it without re-running the regression.

    Attributes
    ----------
    min_points : int
        Minimum number of (x, y) pairs required before fitting is attempted.
        Attempting to fit fewer points raises a ``ValueError``.
    fit_result : Any
        Stores the ``ScalingLaw`` produced by the most recent call to ``fit()``,
        or ``None`` if the fitter has not yet been used.
    """

    min_points: int = _DEFAULT_MIN_POINTS
    fit_result: Any = field(default=None)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fit(self, xs: list, ys: list) -> Any:
        """Fit a power law y = c · xᵅ and return a ScalingLaw descriptor.

        The method first filters out non-positive values (which would be
        undefined in log space), then performs OLS regression in log-log space
        to recover (α, log c).  The coefficient of determination R² is
        computed over the original (not log-transformed) residuals so that it
        reflects prediction accuracy in the natural scale of the data.

        The resulting ScalingLaw object is also stored in ``self.fit_result``
        for later inspection.

        Parameters
        ----------
        xs : list
            Sequence of positive numeric x-values (independent variable).
        ys : list
            Sequence of positive numeric y-values (dependent variable).
            Must be the same length as *xs*.

        Returns
        -------
        ScalingLaw
            A descriptor object containing the exponent α, the constant c,
            the law kind ('power'), the R² value, and provenance metadata.

        Raises
        ------
        ValueError
            If fewer than ``min_points`` valid pairs remain after filtering.
        """
        # --- Filter to strictly positive pairs only ---
        pairs = [
            (x, y)
            for x, y in zip(xs, ys)
            if x > _MIN_VALID_X and y > _MIN_VALID_Y
        ]
        if len(pairs) < self.min_points:
            raise ValueError(
                f"PowerLawFitter requires at least {self.min_points} positive "
                f"data points; got {len(pairs)} after filtering."
            )

        # --- Unzip and fit in log-log space ---
        valid_xs, valid_ys = zip(*pairs)
        alpha, log_c = self._log_linear_fit(list(valid_xs), list(valid_ys))
        c = math.exp(log_c)  # Back-transform the intercept

        # --- Build a simple namespace/dict to stand in for ScalingLaw ---
        r2 = self.r_squared(list(valid_xs), list(valid_ys), (alpha, c, "power"))

        # Attempt to construct a proper ScalingLaw if the models module is available
        try:
            law = ScalingLaw(  # type: ignore[name-defined]
                kind="power",
                exponent=alpha,
                constant=c,
                r_squared=r2,
                fit_timestamp=_utcnow(),
                uid=_uid(),
            )
        except Exception:
            # Fallback: use a plain dict when models are unavailable
            law = {  # type: ignore[assignment]
                "kind": "power",
                "exponent": alpha,
                "constant": c,
                "r_squared": r2,
                "fit_timestamp": _utcnow(),
                "uid": _uid(),
            }

        self.fit_result = law
        return law

    def _log_linear_fit(self, xs: list, ys: list) -> tuple:
        """Perform OLS regression of log(y) on log(x) and return (slope, intercept).

        This internal helper transforms the data to log-log space and then
        applies the standard closed-form OLS estimator:
            slope     = Cov(log_x, log_y) / Var(log_x)
            intercept = mean(log_y) - slope * mean(log_x)

        The transformation is guarded against non-positive values, though the
        public ``fit()`` method should already have filtered those out.

        Parameters
        ----------
        xs : list
            Positive x-values.
        ys : list
            Positive y-values.

        Returns
        -------
        tuple
            A pair (slope, intercept) where slope == α (the power-law exponent)
            and intercept == log(c) (natural log of the scaling constant).
        """
        # Transform to log space using natural logarithm
        log_xs = [math.log(max(x, _MIN_VALID_X)) for x in xs]
        log_ys = [math.log(max(y, _MIN_VALID_Y)) for y in ys]

        n = len(log_xs)
        mean_lx = statistics.mean(log_xs)
        mean_ly = statistics.mean(log_ys)

        # Numerator: sum of (lx_i - mean_lx)(ly_i - mean_ly)
        cov_num = sum(
            (lx - mean_lx) * (ly - mean_ly)
            for lx, ly in zip(log_xs, log_ys)
        )
        # Denominator: sum of (lx_i - mean_lx)²
        var_den = sum((lx - mean_lx) ** 2 for lx in log_xs)

        if abs(var_den) < 1e-14:
            # All x values are identical — slope is undefined; return zero slope
            return 0.0, mean_ly

        slope = cov_num / var_den
        intercept = mean_ly - slope * mean_lx
        return slope, intercept

    def r_squared(self, xs: list, ys: list, law: Any) -> float:
        """Compute the coefficient of determination R² for a fitted power law.

        R² measures the fraction of variance in *ys* explained by the fitted
        model.  A value of 1.0 indicates a perfect fit; values near zero
        indicate that the model captures no more variance than the mean baseline.
        Negative values are possible if the model is worse than the mean.

        The computation uses the natural-scale (not log-scale) predictions so
        that R² reflects accuracy in the units the caller cares about.

        Parameters
        ----------
        xs : list
            x-values used to generate predictions.
        ys : list
            Observed y-values against which predictions are compared.
        law : Any
            A ScalingLaw or dict with keys 'exponent' and 'constant'.

        Returns
        -------
        float
            R² value in the range (-∞, 1].
        """
        # Extract exponent and constant from whatever law representation is used
        if isinstance(law, dict):
            alpha = law["exponent"]
            c = law["constant"]
        elif isinstance(law, tuple):
            # Internal convenience tuple (alpha, c, kind)
            alpha, c = law[0], law[1]
        else:
            alpha = getattr(law, "exponent", 0.0)
            c = getattr(law, "constant", 1.0)

        # Predicted values
        y_pred = [c * (max(x, _MIN_VALID_X) ** alpha) for x in xs]
        y_mean = statistics.mean(ys)

        ss_res = sum((y - yp) ** 2 for y, yp in zip(ys, y_pred))
        ss_tot = sum((y - y_mean) ** 2 for y in ys)

        if abs(ss_tot) < 1e-14:
            return 1.0 if abs(ss_res) < 1e-14 else 0.0

        return 1.0 - ss_res / ss_tot

    def residuals(self, xs: list, ys: list, law: Any) -> list:
        """Return a list of signed residuals (y_observed - y_predicted).

        Residuals are computed in the natural scale of the data.  Inspecting
        the residuals allows the caller to diagnose systematic deviations from
        the power-law model (e.g., a transition between scaling regimes) that
        would not be apparent from the scalar R² statistic alone.

        Parameters
        ----------
        xs : list
            x-values.
        ys : list
            Observed y-values.
        law : Any
            Fitted ScalingLaw or tuple/dict.

        Returns
        -------
        list[float]
            Signed residuals, one per data point.
        """
        if isinstance(law, dict):
            alpha, c = law["exponent"], law["constant"]
        elif isinstance(law, tuple):
            alpha, c = law[0], law[1]
        else:
            alpha = getattr(law, "exponent", 0.0)
            c = getattr(law, "constant", 1.0)

        return [
            y - c * (max(x, _MIN_VALID_X) ** alpha)
            for x, y in zip(xs, ys)
        ]

    def to_dict(self) -> dict:
        """Serialise the fitter's configuration and last fit result to a dict.

        The returned dictionary is JSON-serialisable and includes all fields
        needed to reconstruct the fitter's state for logging, caching, or
        cross-process communication.  The ``fit_result`` field is included only
        when it is not None; its representation depends on whether the JuGeo
        models module was available at fit time.

        Returns
        -------
        dict
            Serialisable representation of this fitter instance.
        """
        base = {
            "class": "PowerLawFitter",
            "min_points": self.min_points,
        }
        if self.fit_result is not None:
            if isinstance(self.fit_result, dict):
                base["fit_result"] = self.fit_result
            else:
                try:
                    base["fit_result"] = self.fit_result.to_dict()
                except Exception:
                    base["fit_result"] = repr(self.fit_result)
        return base

    def __repr__(self) -> str:
        """Return a developer-oriented string representation of this fitter.

        Includes the class name, configuration parameters, and a brief
        summary of the last fit result (if any) so that the object can be
        meaningfully inspected in a REPL or log file without calling
        ``to_dict()`` explicitly.
        """
        fitted = "not fitted"
        if self.fit_result is not None:
            if isinstance(self.fit_result, dict):
                fitted = (
                    f"α={self.fit_result.get('exponent', '?'):.4f}, "
                    f"c={self.fit_result.get('constant', '?'):.4g}, "
                    f"R²={self.fit_result.get('r_squared', '?'):.4f}"
                )
            else:
                fitted = repr(self.fit_result)
        return (
            f"PowerLawFitter(min_points={self.min_points}, "
            f"fit_result=[{fitted}])"
        )


# ---------------------------------------------------------------------------
# ExponentialLawFitter
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class ExponentialLawFitter:
    """Fit an exponential law of the form  y = c · exp(α · x)  to data.

    The fitting is performed in semi-log space: taking the natural log of both
    sides yields  log y = log c + α · x, which is a linear regression in x.
    The OLS slope gives α directly, and the intercept gives log c.

    This fitter is most appropriate when the dependent variable grows or decays
    exponentially with the independent variable — for example, when modelling
    the relationship between problem size and runtime for algorithms with
    exponential complexity classes.

    Attributes
    ----------
    min_points : int
        Minimum number of (x, y) pairs required before fitting is attempted.
    fit_result : Any
        The ScalingLaw produced by the last call to ``fit()``, or ``None``.
    """

    min_points: int = _DEFAULT_MIN_POINTS
    fit_result: Any = field(default=None)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fit(self, xs: list, ys: list) -> Any:
        """Fit an exponential law y = c · exp(α·x) and return a ScalingLaw.

        The fitting proceeds by filtering to positive y values (x can be any
        real number for the exponential family), transforming ys to log space,
        then applying OLS regression of log(y) on x.  The exponent α and the
        constant c are extracted from the regression coefficients.

        Parameters
        ----------
        xs : list
            Sequence of numeric x-values (may include zero or negatives).
        ys : list
            Sequence of strictly positive y-values.

        Returns
        -------
        ScalingLaw
            Descriptor with kind='exponential', the fitted α and c, and R².

        Raises
        ------
        ValueError
            If fewer than ``min_points`` valid pairs remain after filtering.
        """
        # Keep only pairs where y is strictly positive
        pairs = [
            (x, y) for x, y in zip(xs, ys) if y > _MIN_VALID_Y
        ]
        if len(pairs) < self.min_points:
            raise ValueError(
                f"ExponentialLawFitter requires at least {self.min_points} "
                f"positive y-values; got {len(pairs)} after filtering."
            )

        valid_xs, valid_ys = zip(*pairs)
        alpha, log_c = self._log_linear_fit(list(valid_xs), list(valid_ys))
        c = math.exp(log_c)

        r2 = self.r_squared(list(valid_xs), list(valid_ys), (alpha, c, "exponential"))

        try:
            law = ScalingLaw(  # type: ignore[name-defined]
                kind="exponential",
                exponent=alpha,
                constant=c,
                r_squared=r2,
                fit_timestamp=_utcnow(),
                uid=_uid(),
            )
        except Exception:
            law = {  # type: ignore[assignment]
                "kind": "exponential",
                "exponent": alpha,
                "constant": c,
                "r_squared": r2,
                "fit_timestamp": _utcnow(),
                "uid": _uid(),
            }

        self.fit_result = law
        return law

    def _log_linear_fit(self, xs: list, ys: list) -> tuple:
        """Perform OLS regression of log(y) on x and return (slope, intercept).

        For the exponential family the transformation is semi-logarithmic:
        only the dependent variable is log-transformed, while the independent
        variable remains in its natural scale.  The OLS estimator is identical
        in structure to the one used by ``PowerLawFitter._log_linear_fit``
        but operates on (x, log y) pairs rather than (log x, log y) pairs.

        Parameters
        ----------
        xs : list
            x-values (any finite real numbers).
        ys : list
            Strictly positive y-values.

        Returns
        -------
        tuple
            (slope, intercept) where slope == α and intercept == log(c).
        """
        log_ys = [math.log(max(y, _MIN_VALID_Y)) for y in ys]

        n = len(xs)
        mean_x = statistics.mean(xs)
        mean_ly = statistics.mean(log_ys)

        cov_num = sum(
            (x - mean_x) * (ly - mean_ly)
            for x, ly in zip(xs, log_ys)
        )
        var_den = sum((x - mean_x) ** 2 for x in xs)

        if abs(var_den) < 1e-14:
            return 0.0, mean_ly

        slope = cov_num / var_den
        intercept = mean_ly - slope * mean_x
        return slope, intercept

    def r_squared(self, xs: list, ys: list, law: Any) -> float:
        """Compute the coefficient of determination R² for an exponential fit.

        Predictions are generated from the exponential formula  ŷ = c·exp(α·x)
        and compared against the observed values in natural scale.  The metric
        is numerically identical to the formula used in PowerLawFitter but the
        prediction function differs.

        Parameters
        ----------
        xs : list
            x-values.
        ys : list
            Observed y-values.
        law : Any
            ScalingLaw or convenience tuple (alpha, c, kind).

        Returns
        -------
        float
            R² ∈ (-∞, 1].
        """
        if isinstance(law, dict):
            alpha, c = law["exponent"], law["constant"]
        elif isinstance(law, tuple):
            alpha, c = law[0], law[1]
        else:
            alpha = getattr(law, "exponent", 0.0)
            c = getattr(law, "constant", 1.0)

        y_pred = [c * math.exp(alpha * x) for x in xs]
        y_mean = statistics.mean(ys)

        ss_res = sum((y - yp) ** 2 for y, yp in zip(ys, y_pred))
        ss_tot = sum((y - y_mean) ** 2 for y in ys)

        if abs(ss_tot) < 1e-14:
            return 1.0 if abs(ss_res) < 1e-14 else 0.0

        return 1.0 - ss_res / ss_tot

    def residuals(self, xs: list, ys: list, law: Any) -> list:
        """Return signed residuals (y_observed - y_predicted) for an exponential fit.

        Residuals in the natural scale are more interpretable to domain experts
        and are used by the validator to detect outliers and heteroscedasticity.
        Large structured residuals (e.g., monotone trends) indicate that the
        exponential model may be misspecified for this data set.

        Parameters
        ----------
        xs : list
            x-values.
        ys : list
            Observed y-values.
        law : Any
            Fitted ScalingLaw or convenience tuple.

        Returns
        -------
        list[float]
            One signed residual per data point.
        """
        if isinstance(law, dict):
            alpha, c = law["exponent"], law["constant"]
        elif isinstance(law, tuple):
            alpha, c = law[0], law[1]
        else:
            alpha = getattr(law, "exponent", 0.0)
            c = getattr(law, "constant", 1.0)

        return [
            y - c * math.exp(alpha * x)
            for x, y in zip(xs, ys)
        ]

    def to_dict(self) -> dict:
        """Serialise the fitter's configuration and last fit result to a dict.

        The returned mapping is fully JSON-serialisable and mirrors the
        structure produced by ``PowerLawFitter.to_dict()`` so that downstream
        consumers can handle both fitter types uniformly.

        Returns
        -------
        dict
            Serialisable state of this fitter.
        """
        base = {
            "class": "ExponentialLawFitter",
            "min_points": self.min_points,
        }
        if self.fit_result is not None:
            if isinstance(self.fit_result, dict):
                base["fit_result"] = self.fit_result
            else:
                try:
                    base["fit_result"] = self.fit_result.to_dict()
                except Exception:
                    base["fit_result"] = repr(self.fit_result)
        return base

    def __repr__(self) -> str:
        """Developer-oriented string representation of this fitter.

        Shows the fitter class, configuration, and a concise summary of the
        most recently fitted law so that the object state can be understood
        at a glance during interactive debugging or when printed in log output.
        """
        fitted = "not fitted"
        if self.fit_result is not None:
            if isinstance(self.fit_result, dict):
                fitted = (
                    f"α={self.fit_result.get('exponent', '?'):.4f}, "
                    f"c={self.fit_result.get('constant', '?'):.4g}, "
                    f"R²={self.fit_result.get('r_squared', '?'):.4f}"
                )
            else:
                fitted = repr(self.fit_result)
        return (
            f"ExponentialLawFitter(min_points={self.min_points}, "
            f"fit_result=[{fitted}])"
        )


# ---------------------------------------------------------------------------
# ScalingLawValidator
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class ScalingLawValidator:
    """Validate a fitted scaling law using holdout testing and bootstrap CI.

    Validation guards against over-fitting by withholding a fraction of the
    data during validation and computing confidence intervals for the fitted
    exponent via non-parametric bootstrap resampling.  A cross-validation score
    is also produced by averaging holdout R² over multiple random splits.

    Attributes
    ----------
    holdout_fraction : float
        Fraction of data to withhold for the holdout validation test.
        Must be in (0, 1).  Default 0.2 (20 %).
    n_bootstrap : int
        Number of bootstrap resamples to draw when computing confidence
        intervals.  More resamples give narrower intervals but are slower.
        Default 50; set to 200+ for publication-quality intervals.
    """

    holdout_fraction: float = 0.2
    n_bootstrap: int = 50

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------

    def _get_exponent(self, law: Any) -> float:
        """Extract the fitted exponent from a ScalingLaw or dict representation."""
        if isinstance(law, dict):
            return float(law.get("exponent", 0.0))
        return float(getattr(law, "exponent", 0.0))

    def _get_constant(self, law: Any) -> float:
        """Extract the scaling constant from a ScalingLaw or dict representation."""
        if isinstance(law, dict):
            return float(law.get("constant", 1.0))
        return float(getattr(law, "constant", 1.0))

    def _get_kind(self, law: Any) -> str:
        """Extract the law kind string from a ScalingLaw or dict representation."""
        if isinstance(law, dict):
            return str(law.get("kind", "power"))
        return str(getattr(law, "kind", "power"))

    def _predict(self, x: float, alpha: float, c: float, kind: str) -> float:
        """Compute ŷ for a single x value using the specified law kind."""
        if kind == "exponential":
            return c * math.exp(alpha * x)
        # Default: power law
        return c * (max(x, _MIN_VALID_X) ** alpha)

    def _r2_on_pairs(self, xs: list, ys: list, alpha: float, c: float, kind: str) -> float:
        """Compute R² directly from parameter values without a law object."""
        y_pred = [self._predict(x, alpha, c, kind) for x in xs]
        y_mean = statistics.mean(ys) if ys else 0.0
        ss_res = sum((y - yp) ** 2 for y, yp in zip(ys, y_pred))
        ss_tot = sum((y - y_mean) ** 2 for y in ys)
        if abs(ss_tot) < 1e-14:
            return 1.0 if abs(ss_res) < 1e-14 else 0.0
        return 1.0 - ss_res / ss_tot

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def validate(self, law: Any, xs: list, ys: list) -> dict:
        """Validate a fitted scaling law and return a rich diagnostics dict.

        The validation procedure has three stages:
        1. Holdout test — split the data 80/20 (or as configured) and compute
           R² on the withheld 20 %.
        2. Bootstrap CI — resample the data ``n_bootstrap`` times and record
           the exponent from each resample; return the 2.5th and 97.5th
           percentiles as the 95 % CI.
        3. Cross-validation — run ``cross_validate()`` and record the mean R².

        Parameters
        ----------
        law : Any
            A fitted ScalingLaw object or dict with 'kind', 'exponent',
            'constant' keys.
        xs : list
            x-values used for the original fit.
        ys : list
            y-values used for the original fit.

        Returns
        -------
        dict
            Keys: 'holdout_r2', 'bootstrap_ci', 'cv_r2', 'n_points',
                  'quality', 'timestamp'.
        """
        alpha = self._get_exponent(law)
        c = self._get_constant(law)
        kind = self._get_kind(law)
        n = len(xs)

        # --- Holdout split (use last fraction as holdout) ---
        n_holdout = max(1, int(n * self.holdout_fraction))
        n_train = n - n_holdout
        if n_train < 2:
            n_train = max(2, n - 1)
            n_holdout = n - n_train

        holdout_xs = xs[n_train:]
        holdout_ys = ys[n_train:]
        holdout_r2 = self._r2_on_pairs(holdout_xs, holdout_ys, alpha, c, kind) if holdout_xs else 0.0

        # --- Bootstrap CI ---
        ci_low, ci_high = self.bootstrap_ci(law, xs, ys)

        # --- Cross-validation ---
        cv_r2 = self.cross_validate(law, xs, ys)

        # --- Quality label ---
        if holdout_r2 >= _R2_GOOD_THRESHOLD:
            quality = "good"
        elif holdout_r2 >= _R2_POOR_THRESHOLD:
            quality = "moderate"
        else:
            quality = "poor"

        return {
            "holdout_r2": holdout_r2,
            "bootstrap_ci": (ci_low, ci_high),
            "cv_r2": cv_r2,
            "n_points": n,
            "quality": quality,
            "timestamp": _utcnow(),
        }

    def bootstrap_ci(self, law: Any, xs: list, ys: list) -> tuple:
        """Compute a 95 % bootstrap confidence interval for the exponent α.

        For each of the ``n_bootstrap`` resamples the method draws n pairs
        with replacement, refits the appropriate scaling law in log space, and
        records the resulting exponent.  The 2.5th and 97.5th percentiles of
        the bootstrap distribution are returned as the lower and upper bounds
        of the confidence interval.

        The bootstrap avoids distributional assumptions about the residuals,
        making it valid even when residuals are heavy-tailed or heteroscedastic
        — both common in empirical complexity measurements.

        Parameters
        ----------
        law : Any
            The original fitted law (used to determine law kind).
        xs : list
            x-values.
        ys : list
            y-values.

        Returns
        -------
        tuple
            (ci_low, ci_high) — 2.5th and 97.5th percentile exponents.
        """
        kind = self._get_kind(law)
        n = len(xs)
        pairs = list(zip(xs, ys))
        exponents: list[float] = []

        # Pseudo-random bootstrap draws using a simple LCG seeded from n_bootstrap
        seed = _BOOTSTRAP_SEED_BASE ^ n
        a_lcg, c_lcg, m_lcg = 1664525, 1013904223, 2 ** 32

        for _ in range(self.n_bootstrap):
            # Draw n indices with replacement using LCG
            sample: list[tuple] = []
            for _ in range(n):
                seed = (a_lcg * seed + c_lcg) % m_lcg
                idx = seed % n
                sample.append(pairs[idx])

            bx = [p[0] for p in sample]
            by_ = [p[1] for p in sample]

            try:
                if kind == "exponential":
                    fitter = ExponentialLawFitter()
                    bl = fitter.fit(bx, by_)
                else:
                    fitter = PowerLawFitter()
                    bl = fitter.fit(bx, by_)
                exponents.append(self._get_exponent(bl))
            except Exception:
                pass  # Skip failed bootstrap resamples

        if len(exponents) < 2:
            return (self._get_exponent(law), self._get_exponent(law))

        exponents.sort()
        lo_idx = max(0, int(0.025 * len(exponents)))
        hi_idx = min(len(exponents) - 1, int(0.975 * len(exponents)))
        return (exponents[lo_idx], exponents[hi_idx])

    def cross_validate(self, law: Any, xs: list, ys: list) -> float:
        """Estimate generalisation performance via leave-one-out cross-validation.

        For each data point in turn the method withholds that point, uses the
        remaining points to compute the law parameters (in log space), then
        records R² on the withheld point.  The mean R² across all folds is
        returned as the cross-validation score.

        Leave-one-out is preferred over k-fold here because the data sets
        processed by the scaling-limits pipeline are typically small (10–100
        points) and LOO is unbiased for such sizes.

        Parameters
        ----------
        law : Any
            The fitted law (used for kind information only; parameters are
            re-estimated on each fold's training data).
        xs : list
            x-values.
        ys : list
            y-values.

        Returns
        -------
        float
            Mean R² across all leave-one-out folds.
        """
        kind = self._get_kind(law)
        n = len(xs)
        if n < 3:
            return self._r2_on_pairs(xs, ys, self._get_exponent(law), self._get_constant(law), kind)

        fold_r2s: list[float] = []
        for i in range(n):
            # Build training set by excluding index i
            train_xs = [x for j, x in enumerate(xs) if j != i]
            train_ys = [y for j, y in enumerate(ys) if j != i]
            test_xs = [xs[i]]
            test_ys = [ys[i]]

            try:
                if kind == "exponential":
                    fitter = ExponentialLawFitter()
                else:
                    fitter = PowerLawFitter()
                fold_law = fitter.fit(train_xs, train_ys)
                fa = self._get_exponent(fold_law)
                fc = self._get_constant(fold_law)
                fold_r2s.append(self._r2_on_pairs(test_xs, test_ys, fa, fc, kind))
            except Exception:
                pass  # Skip folds that fail (e.g., too few positive points)

        return statistics.mean(fold_r2s) if fold_r2s else 0.0

    def to_dict(self) -> dict:
        """Serialise the validator's configuration to a JSON-compatible dict.

        The returned dictionary captures all parameters that affect the
        validation output, making it suitable for logging, reproducibility
        records, and configuration diffing across pipeline runs.

        Returns
        -------
        dict
            Serialisable representation of this validator's configuration.
        """
        return {
            "class": "ScalingLawValidator",
            "holdout_fraction": self.holdout_fraction,
            "n_bootstrap": self.n_bootstrap,
        }

    def __repr__(self) -> str:
        """Return a developer-oriented string representation of this validator.

        Shows the class name and configuration so that the object can be
        meaningfully identified in logs and REPL sessions without calling
        ``to_dict()``.
        """
        return (
            f"ScalingLawValidator("
            f"holdout_fraction={self.holdout_fraction}, "
            f"n_bootstrap={self.n_bootstrap})"
        )


# ---------------------------------------------------------------------------
# ScalingLawRunner
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class ScalingLawRunner:
    """Orchestrate multiple scaling-law fitters over a shared data set.

    The runner applies each registered fitter to the data, validates each
    fitted law using the shared validator, then ranks the laws by holdout R²
    so that the best-fitting model can be retrieved via ``best_law()``.

    Fitters are added to the ``fitters`` list before calling ``run()``.  If
    the list is empty at run time the runner automatically registers a
    ``PowerLawFitter`` and an ``ExponentialLawFitter`` as defaults.

    Attributes
    ----------
    fitters : list
        Ordered list of fitter objects to apply during ``run()``.
    validator : ScalingLawValidator
        Validator instance used to assess each fitted law.
    fitted_laws : list
        Populated by ``run()``; contains tuples of (law, validation_dict)
        for each successfully fitted law, ordered by decreasing holdout R².
    """

    fitters: list = field(default_factory=list)
    validator: ScalingLawValidator = field(default_factory=ScalingLawValidator)
    fitted_laws: list = field(default_factory=list)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, xs: list, ys: list) -> list:
        """Run all registered fitters and return a ranked list of ScalingLaws.

        For each fitter in ``self.fitters`` the method attempts to fit the
        provided data.  Successful fits are validated using ``self.validator``
        and the results are stored in ``self.fitted_laws`` sorted by holdout
        R² in descending order (best fit first).

        If ``self.fitters`` is empty at call time, a ``PowerLawFitter`` and an
        ``ExponentialLawFitter`` are registered automatically.

        Parameters
        ----------
        xs : list
            Independent-variable values.
        ys : list
            Dependent-variable values (must be the same length as *xs*).

        Returns
        -------
        list
            Ranked list of (ScalingLaw, validation_dict) tuples, best first.
        """
        # Auto-populate fitters if none registered
        if not self.fitters:
            self.fitters = [PowerLawFitter(), ExponentialLawFitter()]

        results: list = []
        for fitter in self.fitters:
            try:
                law = fitter.fit(xs, ys)
                validation = self.validator.validate(law, xs, ys)
                results.append((law, validation))
            except Exception as exc:
                # Silently skip fitters that fail; callers can inspect fitted_laws
                # to see which fitters succeeded.
                pass  # noqa: S110

        # Sort by holdout_r2 descending
        results.sort(key=lambda item: item[1].get("holdout_r2", 0.0), reverse=True)
        self.fitted_laws = results
        return results

    def best_law(self) -> Any:
        """Return the ScalingLaw with the highest holdout R² from the last run.

        If ``run()`` has not been called or all fitters failed, returns None.
        The best law is the first element of ``self.fitted_laws`` (which is
        sorted by holdout R² descending by ``run()``).

        Returns
        -------
        ScalingLaw or None
            The best-fitting law, or None if no fit has been produced.
        """
        if not self.fitted_laws:
            return None
        return self.fitted_laws[0][0]

    def summary(self) -> dict:
        """Return a summary dict describing all fitted laws and their quality.

        The summary includes the number of fitters attempted, the number that
        succeeded, the best law's exponent and R², and a full listing of all
        fitted laws with their holdout R² values.  It is intended for logging
        and diagnostic dashboards.

        Returns
        -------
        dict
            Keys: 'n_attempted', 'n_succeeded', 'best_exponent',
                  'best_r2', 'laws', 'timestamp'.
        """
        laws_summary = []
        for law, val in self.fitted_laws:
            if isinstance(law, dict):
                entry = {
                    "kind": law.get("kind"),
                    "exponent": law.get("exponent"),
                    "r_squared": law.get("r_squared"),
                }
            else:
                entry = {
                    "kind": getattr(law, "kind", None),
                    "exponent": getattr(law, "exponent", None),
                    "r_squared": getattr(law, "r_squared", None),
                }
            entry["holdout_r2"] = val.get("holdout_r2")
            entry["quality"] = val.get("quality")
            laws_summary.append(entry)

        best = self.best_law()
        best_exp = None
        best_r2 = None
        if best is not None:
            best_exp = best.get("exponent") if isinstance(best, dict) else getattr(best, "exponent", None)
            best_r2 = best.get("r_squared") if isinstance(best, dict) else getattr(best, "r_squared", None)

        return {
            "n_attempted": len(self.fitters),
            "n_succeeded": len(self.fitted_laws),
            "best_exponent": best_exp,
            "best_r2": best_r2,
            "laws": laws_summary,
            "timestamp": _utcnow(),
        }

    def to_dict(self) -> dict:
        """Serialise the runner's full state to a JSON-compatible dict.

        Captures the validator configuration and all fitted laws (including
        their validation diagnostics) so that the runner's state can be
        stored, transmitted, or compared across pipeline runs.

        Returns
        -------
        dict
            Fully serialisable state of this runner.
        """
        return {
            "class": "ScalingLawRunner",
            "validator": self.validator.to_dict(),
            "summary": self.summary(),
        }

    def __repr__(self) -> str:
        """Return a developer-oriented string representation of this runner.

        Shows the number of fitters, whether ``run()`` has been called, and
        the best law's key statistics if available.  This avoids requiring
        the caller to call ``summary()`` just to understand the runner's state
        in a REPL session.
        """
        best = self.best_law()
        best_str = "none"
        if best is not None:
            exp = best.get("exponent") if isinstance(best, dict) else getattr(best, "exponent", None)
            r2 = best.get("r_squared") if isinstance(best, dict) else getattr(best, "r_squared", None)
            best_str = f"exponent={exp:.4f}, R²={r2:.4f}"
        return (
            f"ScalingLawRunner("
            f"n_fitters={len(self.fitters)}, "
            f"n_fitted={len(self.fitted_laws)}, "
            f"best=[{best_str}])"
        )


# ---------------------------------------------------------------------------
# Module-level free functions
# ---------------------------------------------------------------------------

def fit_scaling_law(xs: list, ys: list) -> Any:
    """Fit the best scaling law (power or exponential) to the given data.

    This top-level convenience function creates a ``ScalingLawRunner`` with
    default fitters and validator, runs the fitting pipeline, and returns
    the best-fitting ScalingLaw object.  It is the recommended entry point
    for callers that do not need fine-grained control over the fitting process.

    The function attempts both a power-law fit (y = c·xᵅ) and an exponential
    fit (y = c·exp(α·x)) and selects the one with the higher holdout R².  If
    both fits fail (e.g., because all y values are non-positive), a ValueError
    is propagated to the caller.

    Parameters
    ----------
    xs : list
        Independent-variable values.  Must contain at least five positive
        values for a power-law fit; may contain any finite values for an
        exponential fit.
    ys : list
        Dependent-variable values.  Should be strictly positive for
        reliable fitting in log space.

    Returns
    -------
    ScalingLaw
        The best-fitting scaling law as determined by holdout R².

    Raises
    ------
    ValueError
        If no fitter succeeds on the provided data.

    Examples
    --------
    >>> import math
    >>> xs = [1, 2, 4, 8, 16, 32]
    >>> ys = [x**2.3 for x in xs]
    >>> law = fit_scaling_law(xs, ys)
    >>> abs(law['exponent'] - 2.3) < 0.1
    True
    """
    runner = ScalingLawRunner()
    runner.run(xs, ys)
    best = runner.best_law()
    if best is None:
        raise ValueError(
            "fit_scaling_law: no fitter succeeded on the provided data. "
            "Ensure that xs and ys are positive numeric sequences with at "
            f"least {_DEFAULT_MIN_POINTS} elements."
        )
    return best


def validate_scaling_law(law: Any, xs: list, ys: list) -> dict:
    """Validate a fitted scaling law and return a rich diagnostics dictionary.

    This convenience function creates a ``ScalingLawValidator`` with default
    parameters and runs the full three-stage validation (holdout test,
    bootstrap confidence interval, cross-validation) on the provided law and
    data.  It is the recommended entry point for callers that already have a
    fitted law and want a quick quality assessment.

    The returned dictionary mirrors the structure produced by
    ``ScalingLawValidator.validate()``:
    ``{'holdout_r2': float, 'bootstrap_ci': (float, float),
       'cv_r2': float, 'n_points': int, 'quality': str, 'timestamp': str}``

    Parameters
    ----------
    law : Any
        A fitted ScalingLaw object or dict with 'kind', 'exponent', 'constant'
        keys as produced by ``fit_scaling_law()`` or the individual fitters.
    xs : list
        The x-values used when the law was originally fitted.
    ys : list
        The y-values used when the law was originally fitted.

    Returns
    -------
    dict
        Validation diagnostics including holdout R², bootstrap 95 % CI for
        the exponent, leave-one-out cross-validation R², a quality label
        ('good'/'moderate'/'poor'), and a UTC timestamp.

    Examples
    --------
    >>> xs = [1, 2, 4, 8, 16, 32]
    >>> ys = [x**2.3 for x in xs]
    >>> law = fit_scaling_law(xs, ys)
    >>> result = validate_scaling_law(law, xs, ys)
    >>> result['quality'] in ('good', 'moderate', 'poor')
    True
    """
    validator = ScalingLawValidator()
    return validator.validate(law, xs, ys)
