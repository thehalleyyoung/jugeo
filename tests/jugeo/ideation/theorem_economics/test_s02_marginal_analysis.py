from pathlib import Path
import sys

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import math

from jugeo.ideation.theorem_economics.s02_marginal_analysis import (
    MarginalValueCurve,
    EquimarginalPrinciple,
    MarginalAnalyzer,
    MarginalReturnDiminishment,
    _bisect,
    _normalize_allocations,
)
from jugeo.ideation.theorem_economics.models import (
    TheoremYieldModel,
    MarginalValue,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_yield_model(
    regime_id: str = "r1",
    saturation: float = 10.0,
    rate: float = 0.5,
) -> TheoremYieldModel:
    return TheoremYieldModel(
        model_id=f"model-{regime_id}",
        regime_id=regime_id,
        saturation_yield=saturation,
        growth_rate=rate,
        current_budget=0.0,
        empirical_data=[],
    )


def _make_marginal_curve(
    regime_id: str = "r1",
    saturation: float = 10.0,
    rate: float = 0.5,
    resolution: int = 10,
) -> MarginalValueCurve:
    model = _make_yield_model(regime_id, saturation, rate)
    return MarginalValueCurve(model=model, budget_range=(0.01, 20.0), resolution=resolution)


def _make_models(n: int = 3) -> list[TheoremYieldModel]:
    return [_make_yield_model(f"r{i}", saturation=float(5 + i * 2), rate=0.3 + i * 0.1)
            for i in range(n)]


# ---------------------------------------------------------------------------
# _bisect tests
# ---------------------------------------------------------------------------

def test_bisect_finds_root_of_simple_function() -> None:
    root = _bisect(lambda x: x - 3.0, lo=0.0, hi=10.0)
    assert abs(root - 3.0) < 1e-6


def test_bisect_finds_root_of_quadratic() -> None:
    root = _bisect(lambda x: x * x - 4.0, lo=0.0, hi=5.0)
    assert abs(root - 2.0) < 1e-6


def test_bisect_returns_value_in_bracket() -> None:
    root = _bisect(lambda x: x - 7.5, lo=0.0, hi=20.0)
    assert 0.0 <= root <= 20.0


def test_bisect_handles_negative_bracket() -> None:
    root = _bisect(lambda x: x + 5.0, lo=-20.0, hi=0.0)
    assert abs(root - (-5.0)) < 1e-6


# ---------------------------------------------------------------------------
# _normalize_allocations tests
# ---------------------------------------------------------------------------

def test_normalize_allocations_sums_to_total() -> None:
    raw = {"r1": 2.0, "r2": 3.0, "r3": 5.0}
    normalized = _normalize_allocations(raw, total=15.0)
    assert abs(sum(normalized.values()) - 15.0) < 1e-9


def test_normalize_allocations_preserves_keys() -> None:
    raw = {"r1": 1.0, "r2": 2.0}
    normalized = _normalize_allocations(raw, total=10.0)
    assert set(normalized.keys()) == {"r1", "r2"}


def test_normalize_allocations_proportional() -> None:
    raw = {"r1": 1.0, "r2": 1.0}
    normalized = _normalize_allocations(raw, total=10.0)
    assert abs(normalized["r1"] - 5.0) < 1e-9
    assert abs(normalized["r2"] - 5.0) < 1e-9


def test_normalize_allocations_handles_single_entry() -> None:
    raw = {"r1": 42.0}
    normalized = _normalize_allocations(raw, total=7.0)
    assert abs(normalized["r1"] - 7.0) < 1e-9


# ---------------------------------------------------------------------------
# MarginalValueCurve tests
# ---------------------------------------------------------------------------

def test_marginal_curve_evaluate_returns_list_of_pairs() -> None:
    curve = _make_marginal_curve()
    points = curve.evaluate()
    assert isinstance(points, list)
    assert all(len(p) == 2 for p in points)


def test_marginal_curve_evaluate_length_matches_resolution() -> None:
    curve = MarginalValueCurve(
        model=_make_yield_model(),
        budget_range=(0.01, 20.0),
        resolution=7,
    )
    assert len(curve.evaluate()) == 7


def test_marginal_curve_evaluate_mv_values_positive() -> None:
    curve = _make_marginal_curve()
    for _, mv in curve.evaluate():
        assert mv >= 0.0


def test_marginal_curve_evaluate_mv_decreasing() -> None:
    curve = _make_marginal_curve(resolution=10)
    points = curve.evaluate()
    mv_values = [mv for _, mv in points]
    for i in range(len(mv_values) - 1):
        assert mv_values[i] >= mv_values[i + 1] - 1e-9


def test_marginal_curve_at_budget_small_is_maximum() -> None:
    curve = _make_marginal_curve()
    mv_small = curve.at_budget(0.01)
    mv_large = curve.at_budget(18.0)
    assert mv_small >= mv_large


def test_marginal_curve_at_budget_returns_positive_float() -> None:
    curve = _make_marginal_curve()
    mv = curve.at_budget(5.0)
    assert mv > 0.0


def test_marginal_curve_diminishing_returns_onset_positive() -> None:
    curve = _make_marginal_curve()
    onset = curve.diminishing_returns_onset()
    assert onset > 0.0


def test_marginal_curve_diminishing_returns_onset_within_range() -> None:
    curve = MarginalValueCurve(
        model=_make_yield_model(),
        budget_range=(0.01, 20.0),
        resolution=20,
    )
    onset = curve.diminishing_returns_onset()
    assert onset <= 20.0


# ---------------------------------------------------------------------------
# EquimarginalPrinciple tests
# ---------------------------------------------------------------------------

def test_equimarginal_optimal_allocation_sums_to_total_budget() -> None:
    models = _make_models(3)
    principle = EquimarginalPrinciple(models=models)
    total = 15.0
    allocs = principle.optimal_allocation(total_budget=total)
    assert abs(sum(allocs.values()) - total) < 1e-6


def test_equimarginal_optimal_allocation_non_negative() -> None:
    models = _make_models(3)
    principle = EquimarginalPrinciple(models=models)
    allocs = principle.optimal_allocation(total_budget=10.0)
    for v in allocs.values():
        assert v >= -1e-9


def test_equimarginal_optimal_allocation_keys_match_regime_ids() -> None:
    models = _make_models(3)
    regime_ids = {m.regime_id for m in models}
    principle = EquimarginalPrinciple(models=models)
    allocs = principle.optimal_allocation(total_budget=10.0)
    assert set(allocs.keys()) == regime_ids


def test_equimarginal_is_equimarginal_true_for_equimarginal() -> None:
    models = _make_models(2)
    principle = EquimarginalPrinciple(models=models)
    allocs = principle.optimal_allocation(total_budget=10.0)
    assert principle.is_equimarginal(allocs, tolerance=0.5) is True


def test_equimarginal_is_equimarginal_false_for_skewed_allocation() -> None:
    models = _make_models(2)
    principle = EquimarginalPrinciple(models=models)
    skewed_allocs = {models[0].regime_id: 9.9, models[1].regime_id: 0.1}
    assert principle.is_equimarginal(skewed_allocs, tolerance=0.01) is False


def test_equimarginal_two_models_equal_saturation() -> None:
    m1 = _make_yield_model("eq1", saturation=10.0, rate=0.5)
    m2 = _make_yield_model("eq2", saturation=10.0, rate=0.5)
    principle = EquimarginalPrinciple(models=[m1, m2])
    allocs = principle.optimal_allocation(total_budget=10.0)
    assert abs(allocs["eq1"] - allocs["eq2"]) < 1.0


# ---------------------------------------------------------------------------
# MarginalAnalyzer tests
# ---------------------------------------------------------------------------

def test_marginal_analyzer_compute_marginal_returns_marginal_value() -> None:
    models = _make_models(2)
    analyzer = MarginalAnalyzer(models=models)
    mv = analyzer.compute_marginal(regime_id=models[0].regime_id, at_budget=5.0)
    assert isinstance(mv, MarginalValue)


def test_marginal_analyzer_compute_marginal_value_correct() -> None:
    model = _make_yield_model("mv-test", saturation=10.0, rate=0.5)
    analyzer = MarginalAnalyzer(models=[model])
    mv = analyzer.compute_marginal(regime_id="mv-test", at_budget=5.0)
    expected_mv = model.marginal_yield(5.0)
    assert abs(mv.marginal_yields[0] - expected_mv) < 1e-6


def test_marginal_analyzer_marginal_schedule_returns_correct_length() -> None:
    models = _make_models(2)
    analyzer = MarginalAnalyzer(models=models)
    budgets = [1.0, 2.0, 5.0, 10.0]
    schedule = analyzer.marginal_schedule(regime_id=models[0].regime_id, budgets=budgets)
    assert len(schedule) == len(budgets)


def test_marginal_analyzer_equimarginal_allocation_sums_to_budget() -> None:
    models = _make_models(3)
    analyzer = MarginalAnalyzer(models=models)
    total = 20.0
    allocs = analyzer.equimarginal_allocation(total_budget=total)
    assert abs(sum(allocs.values()) - total) < 1e-6


def test_marginal_analyzer_rank_by_marginal_sorted_descending() -> None:
    models = _make_models(4)
    analyzer = MarginalAnalyzer(models=models)
    ranking = analyzer.rank_by_marginal(at_budget=3.0)
    mv_values = [mv for _, mv in ranking]
    assert mv_values == sorted(mv_values, reverse=True)


def test_marginal_analyzer_rank_by_marginal_has_all_regimes() -> None:
    models = _make_models(3)
    analyzer = MarginalAnalyzer(models=models)
    ranking = analyzer.rank_by_marginal(at_budget=5.0)
    assert len(ranking) == 3


# ---------------------------------------------------------------------------
# MarginalReturnDiminishment tests
# ---------------------------------------------------------------------------

def test_marginal_return_diminishment_is_diminishing_true_at_high_budget() -> None:
    model = _make_yield_model(saturation=10.0, rate=0.5)
    diminishment = MarginalReturnDiminishment(model=model)
    assert diminishment.is_diminishing(budget=10.0) is True


def test_marginal_return_diminishment_is_diminishing_false_at_zero() -> None:
    model = _make_yield_model(saturation=10.0, rate=0.5)
    diminishment = MarginalReturnDiminishment(model=model)
    assert diminishment.is_diminishing(budget=0.0) is False


def test_marginal_return_diminishment_saturation_index_in_zero_one() -> None:
    model = _make_yield_model()
    diminishment = MarginalReturnDiminishment(model=model)
    idx = diminishment.saturation_index(budget=5.0)
    assert 0.0 <= idx <= 1.0


def test_marginal_return_diminishment_saturation_index_increases_with_budget() -> None:
    model = _make_yield_model()
    diminishment = MarginalReturnDiminishment(model=model)
    idx_low = diminishment.saturation_index(budget=1.0)
    idx_high = diminishment.saturation_index(budget=20.0)
    assert idx_high >= idx_low


def test_marginal_return_diminishment_saturation_index_near_one_at_large_budget() -> None:
    model = _make_yield_model(saturation=10.0, rate=1.0)
    diminishment = MarginalReturnDiminishment(model=model)
    idx = diminishment.saturation_index(budget=100.0)
    assert idx > 0.95


def test_marginal_return_diminishment_saturation_index_near_zero_at_small_budget() -> None:
    model = _make_yield_model(saturation=10.0, rate=0.1)
    diminishment = MarginalReturnDiminishment(model=model)
    idx = diminishment.saturation_index(budget=0.01)
    assert idx < 0.1


# ---------------------------------------------------------------------------
# Additional integration-style tests
# ---------------------------------------------------------------------------

def test_marginal_analyzer_computes_all_marginals() -> None:
    models = _make_models(5)
    analyzer = MarginalAnalyzer(models=models)
    for model in models:
        mv = analyzer.compute_marginal(regime_id=model.regime_id, at_budget=3.0)
        assert mv.marginal_yields[0] > 0.0


def test_equimarginal_allocation_with_one_model() -> None:
    models = [_make_yield_model("solo")]
    principle = EquimarginalPrinciple(models=models)
    allocs = principle.optimal_allocation(total_budget=10.0)
    assert abs(allocs["solo"] - 10.0) < 1e-9


def test_normalize_allocations_with_large_total() -> None:
    raw = {"r1": 3.0, "r2": 7.0, "r3": 10.0}
    normalized = _normalize_allocations(raw, total=200.0)
    assert abs(sum(normalized.values()) - 200.0) < 1e-9


def test_marginal_curve_at_budget_is_derivative_of_yield() -> None:
    model = _make_yield_model(saturation=10.0, rate=0.5)
    curve = MarginalValueCurve(model=model, budget_range=(0.01, 20.0), resolution=20)
    b = 3.0
    mv_from_curve = curve.at_budget(b)
    mv_from_model = model.marginal_yield(b)
    assert abs(mv_from_curve - mv_from_model) < 0.1


def test_bisect_with_tight_tolerance() -> None:
    root = _bisect(lambda x: x ** 3 - 8.0, lo=0.0, hi=10.0, tol=1e-10)
    assert abs(root - 2.0) < 1e-8


def test_equimarginal_allocation_preserves_proportions() -> None:
    m1 = _make_yield_model("prop1", saturation=5.0, rate=0.5)
    m2 = _make_yield_model("prop2", saturation=15.0, rate=0.5)
    principle = EquimarginalPrinciple(models=[m1, m2])
    allocs = principle.optimal_allocation(total_budget=10.0)
    assert abs(sum(allocs.values()) - 10.0) < 1e-6


def test_marginal_analyzer_returns_mv_for_all_regimes() -> None:
    models = _make_models(4)
    analyzer = MarginalAnalyzer(models=models)
    for model in models:
        mv = analyzer.compute_marginal(regime_id=model.regime_id, at_budget=5.0)
        assert mv.regime_id == model.regime_id


def test_normalize_allocations_handles_equal_weights() -> None:
    raw = {"a": 5.0, "b": 5.0, "c": 5.0}
    normalized = _normalize_allocations(raw, total=30.0)
    for v in normalized.values():
        assert abs(v - 10.0) < 1e-9


def test_marginal_curve_high_rate_steeper_onset() -> None:
    slow = MarginalValueCurve(
        model=_make_yield_model("slow", saturation=10.0, rate=0.1),
        budget_range=(0.01, 20.0),
        resolution=10,
    )
    fast = MarginalValueCurve(
        model=_make_yield_model("fast", saturation=10.0, rate=1.0),
        budget_range=(0.01, 20.0),
        resolution=10,
    )
    assert slow.diminishing_returns_onset() > fast.diminishing_returns_onset()


def test_marginal_return_diminishment_is_non_diminishing_below_onset() -> None:
    model = _make_yield_model(saturation=10.0, rate=0.2)
    diminishment = MarginalReturnDiminishment(model=model)
    assert diminishment.is_diminishing(budget=0.0) is False


def test_equimarginal_many_models_all_allocated() -> None:
    models = _make_models(6)
    principle = EquimarginalPrinciple(models=models)
    allocs = principle.optimal_allocation(total_budget=30.0)
    assert len(allocs) == 6
    assert abs(sum(allocs.values()) - 30.0) < 1e-6


def test_marginal_analyzer_schedule_values_positive() -> None:
    models = _make_models(2)
    analyzer = MarginalAnalyzer(models=models)
    budgets = [1.0, 5.0, 10.0, 20.0]
    schedule = analyzer.marginal_schedule(regime_id=models[0].regime_id, budgets=budgets)
    mv_values = [mv for mv in schedule]
    for mv in mv_values:
        assert mv >= 0.0
