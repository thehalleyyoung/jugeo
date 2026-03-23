from __future__ import annotations

"""
Yield modeling for Theorem-Growth Economics (Ch52 §1).

Models the functional form of theorem output as a function of research
budget.  The canonical model is the saturating exponential:

.. math::

   Y(B) = Y_\\infty \\bigl(1 - e^{-\\lambda B}\\bigr)

Additional diagnostics include R², MSE, and MAE goodness-of-fit metrics,
and a grid-search calibration procedure.
"""

import logging
import math
from dataclasses import dataclass, field
from typing import Any

from .models import MarginalValue, TheoremYieldModel, YieldForecast

_log = logging.getLogger(__name__)

__all__ = [
    "YieldCurve",
    "SaturationEstimator",
    "GrowthRateEstimator",
    "YieldModeler",
    "YieldModelValidator",
    "YieldModelComparator",
    "_r_squared",
    "_mean_squared_error",
    "_mean_absolute_error",
    "_log_transform",
]


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _r_squared(actual: list[float], predicted: list[float]) -> float:
    """Coefficient of determination (R²) between actual and predicted values.

    Returns 0.0 for empty lists and 1.0 when the total sum-of-squares is zero
    (i.e., all actuals are identical).
    """
    if not actual or not predicted:
        return 0.0
    n = min(len(actual), len(predicted))
    mean_actual = sum(actual[:n]) / n
    ss_tot = sum((a - mean_actual) ** 2 for a in actual[:n])
    if ss_tot == 0.0:
        return 1.0
    ss_res = sum((a - p) ** 2 for a, p in zip(actual[:n], predicted[:n]))
    return 1.0 - ss_res / ss_tot


def _mean_squared_error(actual: list[float], predicted: list[float]) -> float:
    """Mean of squared differences between actual and predicted values."""
    if not actual or not predicted:
        return 0.0
    n = min(len(actual), len(predicted))
    return sum((a - p) ** 2 for a, p in zip(actual[:n], predicted[:n])) / n


def _mean_absolute_error(actual: list[float], predicted: list[float]) -> float:
    """Mean of absolute differences between actual and predicted values."""
    if not actual or not predicted:
        return 0.0
    n = min(len(actual), len(predicted))
    return sum(abs(a - p) for a, p in zip(actual[:n], predicted[:n])) / n


def _log_transform(values: list[float]) -> list[float]:
    """Apply log(v + 1) to every element, keeping non-negative inputs safe."""
    return [math.log(v + 1) for v in values]


# ---------------------------------------------------------------------------
# YieldCurve
# ---------------------------------------------------------------------------


class YieldCurve:
    """Evaluates a saturating-exponential yield curve and caches evaluations.

    The curve is: Y(B) = saturation * (1 - exp(-rate * B))

    Parameters
    ----------
    saturation:
        Asymptotic yield Y_∞.
    rate:
        Growth-rate parameter λ.
    regime_id:
        Optional label identifying the research regime.
    """

    def __init__(
        self,
        saturation: float | None = None,
        rate: float | None = None,
        regime_id: str = "",
        *,
        model: TheoremYieldModel | None = None,
        budget_range: tuple[float, float] = (0.0, 10.0),
        resolution: int = 10,
    ) -> None:
        if model is not None:
            saturation = model.saturation_yield
            rate = model.growth_rate
            regime_id = model.regime_id
        self.saturation = float(0.0 if saturation is None else saturation)
        self.rate = float(0.0 if rate is None else rate)
        self.regime_id = regime_id
        self.budget_range = budget_range
        self.resolution = resolution
        self._cache: dict[float, float] = {}

    # ------------------------------------------------------------------
    # Core mathematics
    # ------------------------------------------------------------------

    def evaluate(self, budget: float | None = None) -> float | list[tuple[float, float]]:
        """Return Y(budget) = saturation * (1 - exp(-rate * budget)).

        Returns 0.0 for non-positive rate or negative budget.
        """
        if budget is None:
            lo, hi = self.budget_range
            count = max(int(self.resolution), 0)
            if count <= 0:
                return []
            if count == 1:
                return [(lo, self._evaluate_budget(lo))]
            step = (hi - lo) / (count - 1)
            return [(lo + i * step, self._evaluate_budget(lo + i * step)) for i in range(count)]
        return self._evaluate_budget(budget)

    def _evaluate_budget(self, budget: float) -> float:
        if self.rate <= 0.0 or budget < 0.0:
            return 0.0
        if budget in self._cache:
            return self._cache[budget]
        value = self.saturation * (1.0 - math.exp(-self.rate * budget))
        self._cache[budget] = value
        return value

    def marginal(self, budget: float) -> float:
        """Return dY/dB = saturation * rate * exp(-rate * budget).

        Returns 0.0 for non-positive rate or negative budget.
        """
        if self.rate <= 0.0 or budget < 0.0:
            return 0.0
        return self.saturation * self.rate * math.exp(-self.rate * budget)

    def slope_at(self, budget: float) -> float:
        return self.marginal(budget)

    def inverse(self, target_yield: float) -> float:
        """Return the budget B such that Y(B) == target_yield.

        Uses B = -log(1 - y / Y_inf) / lambda.
        Returns 0.0 when the target is unreachable or parameters are invalid.
        """
        if self.saturation <= 0.0 or self.rate <= 0.0:
            _log.debug("inverse: invalid saturation or rate – returning 0.0")
            return 0.0
        if target_yield >= self.saturation:
            _log.debug("inverse: target_yield %.4g >= saturation %.4g – clamping", target_yield, self.saturation)
            target_yield = 0.9999 * self.saturation
        if target_yield < 0.0:
            return 0.0
        ratio = target_yield / self.saturation
        # ratio is in (0, 1) at this point
        return -math.log(1.0 - ratio) / self.rate

    # ------------------------------------------------------------------
    # Conversion / serialisation helpers
    # ------------------------------------------------------------------

    def to_model(self) -> TheoremYieldModel:
        """Create a :class:`TheoremYieldModel` mirroring this curve."""
        return TheoremYieldModel(
            regime_id=self.regime_id,
            saturation_yield=self.saturation,
            growth_rate=self.rate,
        )

    def points(self, n: int, max_budget: float) -> list[tuple[float, float]]:
        """Return *n* evenly-spaced (budget, yield) pairs over [0, max_budget]."""
        if n <= 0:
            return []
        if max_budget <= 0.0:
            return [(0.0, self._evaluate_budget(0.0))]
        step = max_budget / max(n - 1, 1)
        return [(i * step, self._evaluate_budget(i * step)) for i in range(n)]

    def area_under_curve(self) -> float:
        points = self.evaluate()
        if not isinstance(points, list) or len(points) < 2:
            return 0.0
        area = 0.0
        for (x0, y0), (x1, y1) in zip(points, points[1:]):
            area += (x1 - x0) * (y0 + y1) / 2.0
        return area

    def summary(self) -> str:
        """Return a formatted one-line description of this curve."""
        return (
            f"YieldCurve(regime_id={self.regime_id!r}, "
            f"saturation={self.saturation:.4g}, rate={self.rate:.4g}, "
            f"cache_size={len(self._cache)})"
        )


# ---------------------------------------------------------------------------
# SaturationEstimator
# ---------------------------------------------------------------------------


class SaturationEstimator:
    """Estimates Y_inf from observed (budget, yield) data.

    The heuristic is: Y_inf ≈ multiplier * max(observed yields).

    Parameters
    ----------
    multiplier:
        Scaling factor applied to the observed maximum (default 1.2).
    """

    def __init__(self, multiplier: float = 1.1) -> None:
        self.multiplier = multiplier

    def estimate(self, data: list[tuple[float, float]]) -> float:
        """Return the saturation estimate for *data*.

        Falls back to 10.0 for empty data and 1.0 when max yield is zero.
        """
        if not data:
            return 10.0
        max_yield = max(y for _, y in data)
        if max_yield == 0.0:
            return 1.0
        return max_yield * self.multiplier

    def estimate_with_ci(
        self, data: list[tuple[float, float]]
    ) -> tuple[float, float, float]:
        """Return (estimate, lower_bound, upper_bound) for the saturation.

        Lower is 90 % of estimate; upper is 130 % of estimate.
        """
        est = self.estimate(data)
        return est, est * 0.9, est * 1.3

    def summary(self, data: list[tuple[float, float]]) -> str:
        """Return a formatted summary string including the saturation estimate."""
        est, lo, hi = self.estimate_with_ci(data)
        return (
            f"SaturationEstimator(multiplier={self.multiplier}, "
            f"estimate={est:.4g}, 90%-CI=[{lo:.4g}, {hi:.4g}], "
            f"n_points={len(data)})"
        )

    def is_saturating(self, data: list[tuple[float, float]]) -> bool:
        if len(data) < 3:
            return False
        ordered = sorted(data)
        increments = [ordered[i + 1][1] - ordered[i][1] for i in range(len(ordered) - 1)]
        return increments[-1] <= max(increments[0] * 0.5, 1e-9)


# ---------------------------------------------------------------------------
# GrowthRateEstimator
# ---------------------------------------------------------------------------


class GrowthRateEstimator:
    """Estimates λ (growth rate) from observed data given Y_inf."""

    def __init__(self) -> None:
        pass

    def estimate(
        self, data: list[tuple[float, float]], y_inf: float | None = None, *, saturation_yield: float | None = None
    ) -> float:
        """Return a moment-based estimate of λ.

        For each observation (b, y) computes λ_i = -log(1 - y/y_inf) / b,
        then returns the mean.  Points with b ≤ 0 or y ≥ y_inf are skipped.
        Falls back to 0.1 when no valid points exist.
        """
        y_inf = y_inf if y_inf is not None else saturation_yield
        if not data or y_inf is None or y_inf <= 0.0:
            return 0.1
        lambdas: list[float] = []
        for b, y in data:
            if b <= 0.0:
                continue
            effective_y = min(y, 0.9999 * y_inf)
            ratio = effective_y / y_inf
            if ratio <= 0.0:
                continue
            lam = -math.log(1.0 - ratio) / b
            if lam > 0.0:
                lambdas.append(lam)
        if not lambdas:
            return 0.1
        return sum(lambdas) / len(lambdas)

    def grid_search(
        self,
        data: list[tuple[float, float]],
        y_inf: float,
        n_points: int = 50,
    ) -> float:
        """Find λ by minimising MSE over a grid of candidate values.

        Evaluates *n_points* values of λ in [0.001, 2.0] against the
        saturating-exponential model and returns the best one.
        Falls back to 0.1 for empty data.
        """
        if not data:
            return 0.1
        best_lam = 0.1
        best_mse = float("inf")
        lo, hi = 0.001, 2.0
        for i in range(n_points):
            lam = lo + (hi - lo) * i / max(n_points - 1, 1)
            predicted = [y_inf * (1.0 - math.exp(-lam * b)) for b, _ in data]
            actuals = [y for _, y in data]
            mse = _mean_squared_error(actuals, predicted)
            if mse < best_mse:
                best_mse = mse
                best_lam = lam
        _log.debug("grid_search best λ=%.4g (MSE=%.4g)", best_lam, best_mse)
        return best_lam

    def summary(self, data: list[tuple[float, float]], y_inf: float) -> str:
        """Return a formatted summary of the growth-rate estimation."""
        est = self.estimate(data, y_inf)
        gs = self.grid_search(data, y_inf)
        return (
            f"GrowthRateEstimator(moment_estimate={est:.4g}, "
            f"grid_search_estimate={gs:.4g}, y_inf={y_inf:.4g}, "
            f"n_points={len(data)})"
        )


# ---------------------------------------------------------------------------
# YieldModeler
# ---------------------------------------------------------------------------


class YieldModeler:
    """High-level modeler that wraps :class:`SaturationEstimator` and
    :class:`GrowthRateEstimator` to produce fitted :class:`TheoremYieldModel`
    instances.
    """

    def __init__(self, regime_id: str = "") -> None:
        self.regime_id = regime_id
        self.sat_estimator = SaturationEstimator()
        self.rate_estimator = GrowthRateEstimator()
        self.fitted_models: dict[str, TheoremYieldModel] = {}
        self._latest_model: TheoremYieldModel | None = None

    # ------------------------------------------------------------------
    # Fitting
    # ------------------------------------------------------------------

    def fit(
        self, regime_id: str | list[tuple[float, float]], data: list[tuple[float, float]] | None = None
    ) -> TheoremYieldModel:
        """Fit a :class:`TheoremYieldModel` to *data* for *regime_id*.

        Uses grid-search for the growth rate (more robust than moment
        estimator) and stores the model in :attr:`fitted_models`.
        """
        if data is None:
            data = regime_id  # type: ignore[assignment]
            regime_id = self.regime_id or "default-regime"
        y_inf = self.sat_estimator.estimate(data)
        rate = self.rate_estimator.grid_search(data, y_inf)
        model = TheoremYieldModel(
            model_id=f"model-{regime_id}",
            regime_id=str(regime_id),
            saturation_yield=y_inf,
            growth_rate=rate,
            current_budget=0.0,
            empirical_data=list(data),
        )
        self.fitted_models[str(regime_id)] = model
        self._latest_model = model
        _log.info("fit: regime=%s  Y_inf=%.4g  λ=%.4g", regime_id, y_inf, rate)
        return model

    def update(
        self,
        model: TheoremYieldModel,
        new_data: list[tuple[float, float]],
    ) -> TheoremYieldModel:
        """Incorporate *new_data* into *model* by re-calibrating in place.

        Returns the updated model (the same object, now modified).
        """
        model.calibrate(new_data)
        self.fitted_models[model.regime_id] = model
        _log.debug("update: regime=%s re-calibrated with %d new points", model.regime_id, len(new_data))
        return model

    # ------------------------------------------------------------------
    # Forecasting
    # ------------------------------------------------------------------

    def forecast(
        self,
        model: TheoremYieldModel,
        budgets: list[float],
    ) -> list[YieldForecast]:
        """Return a :class:`YieldForecast` for each element of *budgets*."""
        import time

        forecasts: list[YieldForecast] = []
        for b in budgets:
            pred = model.yield_at(b)
            low = pred * 0.85
            high = pred * 1.15
            # Confidence shrinks with budget distance; simple heuristic
            confidence = max(0.5, 0.95 - 0.001 * b)
            forecasts.append(
                YieldForecast(
                    regime_id=model.regime_id,
                    budget=b,
                    predicted_yield=pred,
                    low=low,
                    high=high,
                    confidence=confidence,
                    created_at=time.time(),
                )
            )
        return forecasts

    # ------------------------------------------------------------------
    # Comparison
    # ------------------------------------------------------------------

    def compare(
        self,
        model_a: TheoremYieldModel,
        model_b: TheoremYieldModel,
        data: list[tuple[float, float]],
    ) -> str:
        """Return ``"A"`` if *model_a* has higher R² on *data*, else ``"B"``."""
        if not data:
            return "A"
        actuals = [y for _, y in data]
        pred_a = [model_a.yield_at(b) for b, _ in data]
        pred_b = [model_b.yield_at(b) for b, _ in data]
        r2_a = _r_squared(actuals, pred_a)
        r2_b = _r_squared(actuals, pred_b)
        _log.debug("compare: R²_A=%.4g  R²_B=%.4g", r2_a, r2_b)
        return "A" if r2_a >= r2_b else "B"

    def predict(self, budget: float) -> float:
        if self._latest_model is None:
            return 0.0
        return self._latest_model.yield_at(budget)

    def compare_models(
        self,
        models: list[TheoremYieldModel],
        data: list[tuple[float, float]],
    ) -> dict[str, float]:
        actuals = [y for _, y in data]
        return {
            model.model_id: _r_squared(actuals, [model.yield_at(b) for b, _ in data])
            for model in models
        }

    def summary(self) -> str:
        """Return a formatted summary of all fitted models."""
        lines = [f"YieldModeler(fitted_models={len(self.fitted_models)})"]
        for rid, m in self.fitted_models.items():
            lines.append(
                f"  {rid}: Y_inf={m.saturation_yield:.4g}, λ={m.growth_rate:.4g}, "
                f"n_data={len(m.empirical_data)}"
            )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# YieldModelValidator
# ---------------------------------------------------------------------------


class YieldModelValidator:
    """Validates a :class:`TheoremYieldModel` on held-out test data."""

    def validate(
        self,
        model: TheoremYieldModel,
        test_data: list[tuple[float, float]],
    ) -> dict[str, float]:
        """Compute R², MSE, and MAE for *model* on *test_data*.

        Returns a dict with keys ``"r2"``, ``"mse"``, and ``"mae"``.
        """
        if not test_data:
            return {"r2": 1.0, "mse": 0.0, "mae": 0.0}
        actuals = [y for _, y in test_data]
        predicted = [model.yield_at(b) for b, _ in test_data]
        return {
            "r2": _r_squared(actuals, predicted),
            "mse": _mean_squared_error(actuals, predicted),
            "mae": _mean_absolute_error(actuals, predicted),
        }

    def is_acceptable(
        self,
        model: TheoremYieldModel,
        test_data: list[tuple[float, float]],
        r2_threshold: float = 0.7,
    ) -> bool:
        """Return ``True`` when the model's R² meets *r2_threshold*.

        Returns ``True`` for empty *test_data* (no evidence of failure).
        """
        if not test_data:
            return True
        metrics = self.validate(model, test_data)
        return metrics["r2"] >= r2_threshold

    def explain(
        self,
        model: TheoremYieldModel,
        test_data: list[tuple[float, float]],
    ) -> str:
        """Return a human-readable explanation of model quality metrics."""
        metrics = self.validate(model, test_data)
        r2 = metrics["r2"]
        verdict = "acceptable" if r2 >= 0.7 else "poor"
        return (
            f"YieldModelValidator: regime={model.regime_id!r}\n"
            f"  R²   = {r2:.4f}  ({verdict})\n"
            f"  MSE  = {metrics['mse']:.6g}\n"
            f"  MAE  = {metrics['mae']:.6g}\n"
            f"  n    = {len(test_data)}"
        )

    def goodness_of_fit(self, model: TheoremYieldModel, test_data: list[tuple[float, float]]) -> float:
        return max(0.0, min(1.0, self.validate(model, test_data)["r2"]))

    def is_well_fitted(
        self, model: TheoremYieldModel, test_data: list[tuple[float, float]], threshold: float = 0.8
    ) -> bool:
        return self.goodness_of_fit(model, test_data) >= threshold


# ---------------------------------------------------------------------------
# YieldModelComparator
# ---------------------------------------------------------------------------


class YieldModelComparator:
    """Ranks multiple :class:`TheoremYieldModel` instances by goodness-of-fit."""

    def compare(
        self,
        models: list[TheoremYieldModel],
        data: list[tuple[float, float]],
    ) -> list[TheoremYieldModel]:
        """Return *models* sorted by R² (best first) on *data*.

        Models with identical R² preserve their original order.
        """
        if not models:
            return []
        if not data:
            return list(models)
        actuals = [y for _, y in data]

        def r2_for(m: TheoremYieldModel) -> float:
            predicted = [m.yield_at(b) for b, _ in data]
            return _r_squared(actuals, predicted)

        return sorted(models, key=r2_for, reverse=True)

    def best(
        self,
        models: list[TheoremYieldModel],
        data: list[tuple[float, float]],
    ) -> TheoremYieldModel | None:
        """Return the model with the highest R², or ``None`` for an empty list."""
        ranked = self.compare(models, data)
        return ranked[0] if ranked else None

    def best_fit(
        self,
        models: list[TheoremYieldModel],
        data: list[tuple[float, float]],
    ) -> TheoremYieldModel | None:
        return self.best(models, data)

    def ranking_table(
        self,
        models: list[TheoremYieldModel],
        data: list[tuple[float, float]],
    ) -> str:
        """Return a formatted table of rank, regime_id, R², and MSE."""
        ranked = self.compare(models, data)
        if not ranked:
            return "YieldModelComparator: no models to rank."
        actuals = [y for _, y in data] if data else []
        header = f"{'Rank':>4}  {'Regime':<24}  {'R²':>8}  {'MSE':>12}"
        separator = "-" * len(header)
        rows = [header, separator]
        for rank, m in enumerate(ranked, start=1):
            if data:
                predicted = [m.yield_at(b) for b, _ in data]
                r2 = _r_squared(actuals, predicted)
                mse = _mean_squared_error(actuals, predicted)
            else:
                r2 = float("nan")
                mse = float("nan")
            rows.append(f"{rank:>4}  {m.regime_id:<24}  {r2:>8.4f}  {mse:>12.6g}")
        return "\n".join(rows)
