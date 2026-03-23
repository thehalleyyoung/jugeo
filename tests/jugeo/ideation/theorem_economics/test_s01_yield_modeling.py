from pathlib import Path
import sys

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import math

from jugeo.ideation.theorem_economics.s01_yield_modeling import (
    YieldCurve,
    SaturationEstimator,
    GrowthRateEstimator,
    YieldModeler,
    YieldModelValidator,
    YieldModelComparator,
    _r_squared,
    _mean_squared_error,
)
from jugeo.ideation.theorem_economics.models import TheoremYieldModel


# ---------------------------------------------------------------------------
# Test data helpers
# ---------------------------------------------------------------------------

def _saturating_data(
    y_inf: float = 10.0,
    lam: float = 0.3,
    budgets: list[float] | None = None,
    noise: float = 0.0,
) -> list[tuple[float, float]]:
    if budgets is None:
        budgets = [1.0, 2.0, 3.0, 5.0, 8.0, 10.0, 15.0, 20.0]
    return [(b, y_inf * (1 - math.exp(-lam * b)) + noise) for b in budgets]


def _make_yield_model(
    regime_id: str = "r1",
    saturation: float = 10.0,
    rate: float = 0.3,
) -> TheoremYieldModel:
    return TheoremYieldModel(
        model_id=f"model-{regime_id}",
        regime_id=regime_id,
        saturation_yield=saturation,
        growth_rate=rate,
        current_budget=0.0,
        empirical_data=[],
    )


def _make_yield_curve(
    regime_id: str = "r1",
    saturation: float = 10.0,
    rate: float = 0.3,
) -> YieldCurve:
    model = _make_yield_model(regime_id, saturation, rate)
    return YieldCurve(model=model, budget_range=(0.0, 20.0), resolution=10)


# ---------------------------------------------------------------------------
# _r_squared tests
# ---------------------------------------------------------------------------

def test_r_squared_perfect_predictions_returns_one() -> None:
    actual = [1.0, 2.0, 3.0, 4.0, 5.0]
    predicted = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert abs(_r_squared(actual, predicted) - 1.0) < 1e-9


def test_r_squared_constant_predictions_returns_zero_or_negative() -> None:
    actual = [1.0, 2.0, 3.0, 4.0, 5.0]
    mean_val = sum(actual) / len(actual)
    predicted = [mean_val] * len(actual)
    r2 = _r_squared(actual, predicted)
    assert r2 <= 0.0 + 1e-9


def test_r_squared_in_range_for_reasonable_fit() -> None:
    data = _saturating_data(y_inf=10.0, lam=0.3)
    actual = [y for _, y in data]
    model = _make_yield_model(saturation=10.0, rate=0.3)
    predicted = [model.yield_at(b) for b, _ in data]
    r2 = _r_squared(actual, predicted)
    assert 0.9 <= r2 <= 1.0


# ---------------------------------------------------------------------------
# _mean_squared_error tests
# ---------------------------------------------------------------------------

def test_mean_squared_error_perfect_returns_zero() -> None:
    actual = [1.0, 2.0, 3.0]
    predicted = [1.0, 2.0, 3.0]
    assert abs(_mean_squared_error(actual, predicted) - 0.0) < 1e-9


def test_mean_squared_error_non_negative() -> None:
    actual = [1.0, 2.0, 3.0]
    predicted = [2.0, 3.0, 4.0]
    assert _mean_squared_error(actual, predicted) >= 0.0


def test_mean_squared_error_correct_value() -> None:
    actual = [0.0, 0.0]
    predicted = [1.0, 1.0]
    assert abs(_mean_squared_error(actual, predicted) - 1.0) < 1e-9


def test_mean_squared_error_symmetric() -> None:
    a = [1.0, 3.0, 5.0]
    b = [2.0, 4.0, 6.0]
    assert abs(_mean_squared_error(a, b) - _mean_squared_error(b, a)) < 1e-9


# ---------------------------------------------------------------------------
# YieldCurve tests
# ---------------------------------------------------------------------------

def test_yield_curve_evaluate_returns_list_of_pairs() -> None:
    curve = _make_yield_curve()
    points = curve.evaluate()
    assert isinstance(points, list)
    assert all(len(p) == 2 for p in points)


def test_yield_curve_evaluate_length_matches_resolution() -> None:
    curve = YieldCurve(
        model=_make_yield_model(),
        budget_range=(0.0, 20.0),
        resolution=8,
    )
    points = curve.evaluate()
    assert len(points) == 8


def test_yield_curve_evaluate_budgets_are_increasing() -> None:
    curve = _make_yield_curve()
    points = curve.evaluate()
    budgets = [b for b, _ in points]
    assert budgets == sorted(budgets)


def test_yield_curve_evaluate_yields_are_non_negative() -> None:
    curve = _make_yield_curve()
    for _, y in curve.evaluate():
        assert y >= 0.0


def test_yield_curve_evaluate_yields_are_non_decreasing() -> None:
    curve = _make_yield_curve()
    points = curve.evaluate()
    yields = [y for _, y in points]
    for i in range(len(yields) - 1):
        assert yields[i] <= yields[i + 1] + 1e-9


def test_yield_curve_slope_at_positive_for_saturating() -> None:
    curve = _make_yield_curve(saturation=10.0, rate=0.3)
    slope = curve.slope_at(budget=5.0)
    assert slope > 0.0


def test_yield_curve_slope_at_decreasing_with_budget() -> None:
    curve = _make_yield_curve()
    slope_low = curve.slope_at(1.0)
    slope_high = curve.slope_at(15.0)
    assert slope_low > slope_high


def test_yield_curve_area_under_curve_positive() -> None:
    curve = _make_yield_curve()
    area = curve.area_under_curve()
    assert area > 0.0


def test_yield_curve_area_under_curve_increases_with_range() -> None:
    narrow = YieldCurve(model=_make_yield_model(), budget_range=(0.0, 5.0), resolution=10)
    wide = YieldCurve(model=_make_yield_model(), budget_range=(0.0, 20.0), resolution=10)
    assert wide.area_under_curve() > narrow.area_under_curve()


# ---------------------------------------------------------------------------
# SaturationEstimator tests
# ---------------------------------------------------------------------------

def test_saturation_estimator_estimate_at_least_max_y() -> None:
    data = _saturating_data(y_inf=10.0, lam=0.3)
    estimator = SaturationEstimator()
    est = estimator.estimate(data)
    max_y = max(y for _, y in data)
    assert est >= max_y - 0.01


def test_saturation_estimator_estimate_positive() -> None:
    data = _saturating_data(y_inf=8.0, lam=0.2)
    estimator = SaturationEstimator()
    assert estimator.estimate(data) > 0.0


def test_saturation_estimator_is_saturating_true_for_saturating_data() -> None:
    data = _saturating_data(y_inf=10.0, lam=0.4,
                            budgets=[1.0, 2.0, 5.0, 10.0, 20.0, 30.0, 50.0])
    estimator = SaturationEstimator()
    assert estimator.is_saturating(data) is True


def test_saturation_estimator_is_saturating_false_for_linear_data() -> None:
    data = [(float(b), float(b)) for b in range(1, 10)]
    estimator = SaturationEstimator()
    assert estimator.is_saturating(data) is False


def test_saturation_estimator_close_to_true_saturation() -> None:
    data = _saturating_data(y_inf=15.0, lam=0.5,
                            budgets=[1.0, 2.0, 5.0, 10.0, 20.0, 40.0, 80.0])
    estimator = SaturationEstimator()
    est = estimator.estimate(data)
    assert abs(est - 15.0) < 3.0


# ---------------------------------------------------------------------------
# GrowthRateEstimator tests
# ---------------------------------------------------------------------------

def test_growth_rate_estimator_returns_positive() -> None:
    data = _saturating_data(y_inf=10.0, lam=0.3)
    estimator = GrowthRateEstimator()
    rate = estimator.estimate(data, saturation_yield=10.0)
    assert rate > 0.0


def test_growth_rate_estimator_close_to_true_rate() -> None:
    true_rate = 0.4
    data = _saturating_data(y_inf=10.0, lam=true_rate,
                            budgets=[1.0, 2.0, 3.0, 5.0, 8.0, 12.0, 20.0])
    estimator = GrowthRateEstimator()
    est_rate = estimator.estimate(data, saturation_yield=10.0)
    assert abs(est_rate - true_rate) < 0.15


def test_growth_rate_estimator_larger_rate_for_faster_growth() -> None:
    slow_data = _saturating_data(y_inf=10.0, lam=0.1,
                                 budgets=[1.0, 3.0, 5.0, 10.0, 20.0])
    fast_data = _saturating_data(y_inf=10.0, lam=0.8,
                                 budgets=[1.0, 3.0, 5.0, 10.0, 20.0])
    estimator = GrowthRateEstimator()
    slow_rate = estimator.estimate(slow_data, saturation_yield=10.0)
    fast_rate = estimator.estimate(fast_data, saturation_yield=10.0)
    assert fast_rate > slow_rate


# ---------------------------------------------------------------------------
# YieldModeler tests
# ---------------------------------------------------------------------------

def test_yield_modeler_fit_returns_theorem_yield_model() -> None:
    data = _saturating_data()
    modeler = YieldModeler(regime_id="r-fit")
    model = modeler.fit(data)
    assert isinstance(model, TheoremYieldModel)


def test_yield_modeler_fit_returns_positive_saturation() -> None:
    data = _saturating_data(y_inf=10.0, lam=0.3)
    modeler = YieldModeler(regime_id="r-sat")
    model = modeler.fit(data)
    assert model.saturation_yield > 0.0


def test_yield_modeler_fit_returns_positive_rate() -> None:
    data = _saturating_data(y_inf=10.0, lam=0.3)
    modeler = YieldModeler(regime_id="r-rate")
    model = modeler.fit(data)
    assert model.growth_rate > 0.0


def test_yield_modeler_predict_matches_model_yield_at() -> None:
    data = _saturating_data()
    modeler = YieldModeler(regime_id="r-pred")
    model = modeler.fit(data)
    for b in [1.0, 5.0, 10.0]:
        predicted = modeler.predict(b)
        from_model = model.yield_at(b)
        assert abs(predicted - from_model) < 1e-9


def test_yield_modeler_compare_models_returns_dict() -> None:
    data = _saturating_data()
    modeler1 = YieldModeler(regime_id="r1")
    modeler2 = YieldModeler(regime_id="r2")
    m1 = modeler1.fit(data)
    m2 = modeler2.fit(data)
    result = modeler1.compare_models([m1, m2], data)
    assert isinstance(result, dict)


def test_yield_modeler_compare_models_keys_are_model_ids() -> None:
    data = _saturating_data()
    modeler = YieldModeler(regime_id="r1")
    m1 = modeler.fit(data)
    modeler2 = YieldModeler(regime_id="r2")
    m2 = modeler2.fit(data)
    result = modeler.compare_models([m1, m2], data)
    assert m1.model_id in result
    assert m2.model_id in result


# ---------------------------------------------------------------------------
# YieldModelValidator tests
# ---------------------------------------------------------------------------

def test_yield_model_validator_goodness_of_fit_in_zero_one() -> None:
    data = _saturating_data(y_inf=10.0, lam=0.3)
    modeler = YieldModeler(regime_id="rv")
    model = modeler.fit(data)
    validator = YieldModelValidator()
    gof = validator.goodness_of_fit(model, data)
    assert 0.0 <= gof <= 1.0


def test_yield_model_validator_goodness_of_fit_high_for_perfect_fit() -> None:
    y_inf, lam = 10.0, 0.3
    data = _saturating_data(y_inf=y_inf, lam=lam)
    model = TheoremYieldModel(
        model_id="perfect",
        regime_id="rp",
        saturation_yield=y_inf,
        growth_rate=lam,
        current_budget=0.0,
        empirical_data=[],
    )
    validator = YieldModelValidator()
    gof = validator.goodness_of_fit(model, data)
    assert gof > 0.9


def test_yield_model_validator_is_well_fitted_true_for_perfect_data() -> None:
    y_inf, lam = 10.0, 0.3
    data = _saturating_data(y_inf=y_inf, lam=lam)
    model = TheoremYieldModel(
        model_id="well-fit",
        regime_id="rwf",
        saturation_yield=y_inf,
        growth_rate=lam,
        current_budget=0.0,
        empirical_data=[],
    )
    validator = YieldModelValidator()
    assert validator.is_well_fitted(model, data) is True


def test_yield_model_validator_is_well_fitted_false_for_bad_model() -> None:
    data = _saturating_data(y_inf=10.0, lam=0.3)
    bad_model = TheoremYieldModel(
        model_id="bad",
        regime_id="rbad",
        saturation_yield=0.1,
        growth_rate=0.001,
        current_budget=0.0,
        empirical_data=[],
    )
    validator = YieldModelValidator()
    assert validator.is_well_fitted(bad_model, data) is False


# ---------------------------------------------------------------------------
# YieldModelComparator tests
# ---------------------------------------------------------------------------

def test_yield_model_comparator_best_fit_returns_theorem_yield_model() -> None:
    data = _saturating_data()
    m1 = _make_yield_model(regime_id="c1", saturation=10.0, rate=0.3)
    m2 = _make_yield_model(regime_id="c2", saturation=5.0, rate=0.1)
    comparator = YieldModelComparator()
    best = comparator.best_fit([m1, m2], data)
    assert isinstance(best, TheoremYieldModel)


def test_yield_model_comparator_best_fit_returns_closer_model() -> None:
    y_inf, lam = 10.0, 0.3
    data = _saturating_data(y_inf=y_inf, lam=lam)
    good = TheoremYieldModel(
        model_id="good-model",
        regime_id="rg",
        saturation_yield=y_inf,
        growth_rate=lam,
        current_budget=0.0,
        empirical_data=[],
    )
    bad = TheoremYieldModel(
        model_id="bad-model",
        regime_id="rb",
        saturation_yield=50.0,
        growth_rate=0.001,
        current_budget=0.0,
        empirical_data=[],
    )
    comparator = YieldModelComparator()
    best = comparator.best_fit([good, bad], data)
    assert best.model_id == "good-model"


def test_yield_model_comparator_best_fit_single_model() -> None:
    data = _saturating_data()
    m = _make_yield_model()
    comparator = YieldModelComparator()
    best = comparator.best_fit([m], data)
    assert best.model_id == m.model_id


def test_yield_modeler_fit_on_high_saturation_data() -> None:
    data = _saturating_data(y_inf=100.0, lam=0.05,
                            budgets=[10.0, 20.0, 40.0, 80.0, 120.0])
    modeler = YieldModeler(regime_id="r-large")
    model = modeler.fit(data)
    assert model.saturation_yield > 50.0


def test_yield_curve_slope_near_zero_at_large_budget() -> None:
    curve = _make_yield_curve(saturation=10.0, rate=0.5)
    slope = curve.slope_at(50.0)
    assert slope < 0.01


def test_growth_rate_estimator_handles_single_point_data() -> None:
    data = [(5.0, 8.0)]
    estimator = GrowthRateEstimator()
    rate = estimator.estimate(data, saturation_yield=10.0)
    assert rate > 0.0


def test_saturation_estimator_handles_flat_data() -> None:
    data = [(float(b), 9.9) for b in range(1, 10)]
    estimator = SaturationEstimator()
    est = estimator.estimate(data)
    assert est >= 9.9


def test_yield_modeler_regime_id_in_model() -> None:
    data = _saturating_data()
    modeler = YieldModeler(regime_id="special-regime")
    model = modeler.fit(data)
    assert model.regime_id == "special-regime"


def test_yield_curve_budget_range_respected() -> None:
    max_b = 30.0
    curve = YieldCurve(model=_make_yield_model(), budget_range=(0.0, max_b), resolution=10)
    points = curve.evaluate()
    assert all(b <= max_b + 1e-9 for b, _ in points)


def test_r_squared_penalizes_systematic_offset() -> None:
    actual = [1.0, 2.0, 3.0, 4.0, 5.0]
    predicted = [2.0, 3.0, 4.0, 5.0, 6.0]
    r2 = _r_squared(actual, predicted)
    assert r2 < 1.0


def test_mean_squared_error_larger_for_bigger_errors() -> None:
    actual = [0.0, 0.0, 0.0]
    small_errors = [0.1, 0.1, 0.1]
    large_errors = [1.0, 1.0, 1.0]
    assert _mean_squared_error(actual, large_errors) > _mean_squared_error(actual, small_errors)
