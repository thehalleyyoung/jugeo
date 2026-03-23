from pathlib import Path
import sys

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import math

from jugeo.ideation.theorem_economics.models import (
    TheoremYieldModel,
    MarginalValue,
    InvestmentSchedule,
    CompoundingEffect,
    TheoremPortfolioValue,
    RegimeEconomics,
    BudgetAllocation,
    YieldForecast,
    EconomicEquilibrium,
    _saturating_yield,
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


def _make_investment_schedule(
    regime_ids: list[str] | None = None,
    allocations: dict[str, float] | None = None,
    total_budget: float = 10.0,
) -> InvestmentSchedule:
    if regime_ids is None:
        regime_ids = ["r1", "r2"]
    if allocations is None:
        allocations = {rid: total_budget / len(regime_ids) for rid in regime_ids}
    return InvestmentSchedule(
        schedule_id="sched-1",
        regime_ids=regime_ids,
        allocations=allocations,
        total_budget=total_budget,
        expected_yield=5.0,
    )


def _make_compounding_effect(
    base_yield: float = 5.0,
    derived_theorems: int = 3,
    chain_depth: int = 2,
) -> CompoundingEffect:
    return CompoundingEffect(
        base_theorem_id="t0",
        base_yield=base_yield,
        derived_theorems=derived_theorems,
        chain_depth=chain_depth,
        compounding_factor=1.2,
    )


def _make_portfolio_value(num_theorems: int = 3) -> TheoremPortfolioValue:
    return TheoremPortfolioValue(
        portfolio_id="portfolio-1",
        theorem_ids=[f"t{i}" for i in range(num_theorems)],
        yields={f"t{i}": float(i + 1) for i in range(num_theorems)},
        discount_rate=0.05,
        time_horizon=10.0,
    )


def _make_regime_economics(regime_id: str = "r1") -> RegimeEconomics:
    model = _make_yield_model(regime_id=regime_id)
    return RegimeEconomics(
        regime_id=regime_id,
        yield_model=model,
        novelty_premium=0.2,
        exploration_cost=1.0,
        regime_weight=1.0,
    )


def _make_budget_allocation(regime_ids: list[str] | None = None) -> BudgetAllocation:
    if regime_ids is None:
        regime_ids = ["r1", "r2", "r3"]
    total = 10.0
    per = total / len(regime_ids)
    return BudgetAllocation(
        allocation_id="alloc-1",
        regime_ids=regime_ids,
        amounts={rid: per for rid in regime_ids},
        total_budget=total,
    )


def _make_yield_forecast(model: TheoremYieldModel | None = None) -> YieldForecast:
    if model is None:
        model = _make_yield_model()
    return YieldForecast(
        model_id=model.model_id,
        budget_points=[1.0, 2.0, 5.0, 10.0],
        yield_points=[model.yield_at(b) for b in [1.0, 2.0, 5.0, 10.0]],
        confidence=0.85,
        forecast_horizon=20.0,
    )


def _make_equilibrium(num_regimes: int = 3) -> EconomicEquilibrium:
    regimes = [f"r{i}" for i in range(num_regimes)]
    mvs = {rid: 1.0 / (i + 1) for i, rid in enumerate(regimes)}
    return EconomicEquilibrium(
        equilibrium_id="eq-1",
        regime_ids=regimes,
        marginal_values=mvs,
        allocations={rid: 3.0 for rid in regimes},
        total_budget=9.0,
    )


# ---------------------------------------------------------------------------
# _saturating_yield helper tests
# ---------------------------------------------------------------------------

def test_saturating_yield_at_zero_is_zero() -> None:
    y = _saturating_yield(budget=0.0, saturation_yield=10.0, growth_rate=0.5)
    assert y == 0.0


def test_saturating_yield_increases_with_budget() -> None:
    y1 = _saturating_yield(budget=1.0, saturation_yield=10.0, growth_rate=0.5)
    y5 = _saturating_yield(budget=5.0, saturation_yield=10.0, growth_rate=0.5)
    assert y5 > y1


def test_saturating_yield_approaches_saturation() -> None:
    y_large = _saturating_yield(budget=100.0, saturation_yield=10.0, growth_rate=0.5)
    assert y_large > 9.9


def test_saturating_yield_bounded_by_saturation() -> None:
    y = _saturating_yield(budget=1000.0, saturation_yield=10.0, growth_rate=0.5)
    assert y <= 10.0


def test_saturating_yield_formula_correct() -> None:
    s, r, b = 10.0, 0.3, 5.0
    expected = s * (1 - math.exp(-r * b))
    actual = _saturating_yield(budget=b, saturation_yield=s, growth_rate=r)
    assert abs(actual - expected) < 1e-9


# ---------------------------------------------------------------------------
# TheoremYieldModel tests
# ---------------------------------------------------------------------------

def test_yield_model_yield_at_zero_is_zero() -> None:
    model = _make_yield_model()
    assert model.yield_at(0.0) == 0.0


def test_yield_model_yield_increases_with_budget() -> None:
    model = _make_yield_model()
    assert model.yield_at(5.0) > model.yield_at(1.0)


def test_yield_model_yield_approaches_saturation() -> None:
    model = _make_yield_model(saturation=10.0, rate=0.5)
    y = model.yield_at(100.0)
    assert y > 9.9


def test_yield_model_yield_bounded_by_saturation() -> None:
    model = _make_yield_model(saturation=10.0)
    for b in [0.0, 1.0, 10.0, 100.0, 1000.0]:
        assert model.yield_at(b) <= 10.0 + 1e-9


def test_yield_model_marginal_yield_is_positive() -> None:
    model = _make_yield_model()
    for b in [1.0, 2.0, 5.0]:
        mv = model.marginal_yield(b)
        assert mv > 0.0


def test_yield_model_marginal_yield_is_decreasing() -> None:
    model = _make_yield_model()
    mv1 = model.marginal_yield(1.0)
    mv5 = model.marginal_yield(5.0)
    mv10 = model.marginal_yield(10.0)
    assert mv1 > mv5 > mv10


def test_yield_model_calibrate_updates_in_place() -> None:
    model = _make_yield_model()
    data = [(float(b), 10.0 * (1 - math.exp(-0.3 * b))) for b in range(1, 15)]
    model.calibrate(data)
    assert model.saturation_yield > 0.0
    assert model.growth_rate > 0.0


def test_yield_model_calibrate_improves_fit() -> None:
    model = _make_yield_model(saturation=10.0, rate=0.5)
    data = [(float(b), 20.0 * (1 - math.exp(-0.2 * b))) for b in range(1, 15)]
    model.calibrate(data)
    assert model.saturation_yield > 5.0


def test_yield_model_optimal_budget_is_positive() -> None:
    model = _make_yield_model()
    budget = model.optimal_budget()
    assert budget > 0.0


def test_yield_model_forecast_correct_length() -> None:
    model = _make_yield_model()
    forecast = model.forecast(max_budget=10.0, steps=5)
    assert len(forecast) == 5


def test_yield_model_forecast_values_increasing() -> None:
    model = _make_yield_model()
    forecast = model.forecast(max_budget=10.0, steps=6)
    yields = [y for _, y in forecast]
    for i in range(len(yields) - 1):
        assert yields[i] <= yields[i + 1]


def test_yield_model_regime_id_stored() -> None:
    model = _make_yield_model(regime_id="my-regime")
    assert model.regime_id == "my-regime"


def test_yield_model_model_id_stored() -> None:
    model = _make_yield_model(regime_id="r99")
    assert model.model_id == "model-r99"


# ---------------------------------------------------------------------------
# MarginalValue tests
# ---------------------------------------------------------------------------

def test_marginal_value_is_decreasing_true_for_saturating() -> None:
    mvs = MarginalValue(
        regime_id="r1",
        budget_points=[1.0, 2.0, 5.0, 10.0],
        marginal_yields=[4.0, 2.5, 1.2, 0.6],
    )
    assert mvs.is_decreasing() is True


def test_marginal_value_is_decreasing_false_for_increasing() -> None:
    mvs = MarginalValue(
        regime_id="r1",
        budget_points=[1.0, 2.0, 5.0],
        marginal_yields=[0.5, 1.0, 2.0],
    )
    assert mvs.is_decreasing() is False


def test_marginal_value_regime_id_stored() -> None:
    mvs = MarginalValue(
        regime_id="regime-x",
        budget_points=[1.0, 2.0],
        marginal_yields=[2.0, 1.0],
    )
    assert mvs.regime_id == "regime-x"


# ---------------------------------------------------------------------------
# InvestmentSchedule tests
# ---------------------------------------------------------------------------

def test_investment_schedule_efficiency_ratio_correct() -> None:
    sched = InvestmentSchedule(
        schedule_id="s1",
        regime_ids=["r1"],
        allocations={"r1": 10.0},
        total_budget=10.0,
        expected_yield=5.0,
    )
    assert sched.efficiency_ratio() == 0.5


def test_investment_schedule_efficiency_ratio_not_negative() -> None:
    sched = _make_investment_schedule()
    assert sched.efficiency_ratio() >= 0.0


def test_investment_schedule_reallocate_updates_allocations() -> None:
    sched = _make_investment_schedule()
    new_allocs = {"r1": 7.0, "r2": 3.0}
    sched.reallocate(new_allocs)
    assert sched.allocations["r1"] == 7.0
    assert sched.allocations["r2"] == 3.0


def test_investment_schedule_allocation_fractions_sum_to_one() -> None:
    sched = _make_investment_schedule(
        regime_ids=["r1", "r2", "r3"],
        allocations={"r1": 3.0, "r2": 4.0, "r3": 3.0},
        total_budget=10.0,
    )
    fractions = sched.allocation_fractions()
    total = sum(fractions.values())
    assert abs(total - 1.0) < 1e-9


def test_investment_schedule_allocation_fractions_non_negative() -> None:
    sched = _make_investment_schedule()
    fractions = sched.allocation_fractions()
    for f in fractions.values():
        assert f >= 0.0


# ---------------------------------------------------------------------------
# CompoundingEffect tests
# ---------------------------------------------------------------------------

def test_compounding_effect_total_yield_is_positive() -> None:
    ce = _make_compounding_effect(base_yield=5.0, derived_theorems=3)
    assert ce.total_yield() > 0.0


def test_compounding_effect_total_yield_exceeds_base() -> None:
    ce = _make_compounding_effect(base_yield=5.0, derived_theorems=3)
    assert ce.total_yield() >= ce.base_yield


def test_compounding_effect_add_derived_increases_count() -> None:
    ce = _make_compounding_effect(derived_theorems=3)
    original_count = ce.derived_theorems
    ce.add_derived("new-theorem")
    assert ce.derived_theorems > original_count


def test_compounding_effect_base_theorem_id_stored() -> None:
    ce = _make_compounding_effect()
    assert ce.base_theorem_id == "t0"


def test_compounding_effect_chain_depth_stored() -> None:
    ce = _make_compounding_effect(chain_depth=4)
    assert ce.chain_depth == 4


# ---------------------------------------------------------------------------
# TheoremPortfolioValue tests
# ---------------------------------------------------------------------------

def test_portfolio_value_present_value_is_positive() -> None:
    pv = _make_portfolio_value()
    assert pv.present_value() > 0.0


def test_portfolio_value_add_theorem_increases_count() -> None:
    pv = _make_portfolio_value(num_theorems=2)
    original_count = len(pv.theorem_ids)
    pv.add_theorem("t-new", yield_value=3.0)
    assert len(pv.theorem_ids) > original_count


def test_portfolio_value_theorem_ids_stored() -> None:
    pv = _make_portfolio_value(num_theorems=3)
    assert len(pv.theorem_ids) == 3


def test_portfolio_value_higher_discount_lower_pv() -> None:
    pv_low = TheoremPortfolioValue(
        portfolio_id="p1",
        theorem_ids=["t0"],
        yields={"t0": 10.0},
        discount_rate=0.01,
        time_horizon=10.0,
    )
    pv_high = TheoremPortfolioValue(
        portfolio_id="p2",
        theorem_ids=["t0"],
        yields={"t0": 10.0},
        discount_rate=0.5,
        time_horizon=10.0,
    )
    assert pv_low.present_value() >= pv_high.present_value()


# ---------------------------------------------------------------------------
# RegimeEconomics tests
# ---------------------------------------------------------------------------

def test_regime_economics_adjusted_yield_with_premium() -> None:
    re = _make_regime_economics()
    base_yield = re.yield_model.yield_at(5.0)
    adjusted = re.adjusted_yield(budget=5.0, novelty_score=0.8)
    assert adjusted >= base_yield


def test_regime_economics_adjusted_yield_accounts_for_novelty() -> None:
    re_low = _make_regime_economics()
    re_high = _make_regime_economics()
    re_low.novelty_premium = 0.0
    re_high.novelty_premium = 1.0
    y_low = re_low.adjusted_yield(budget=5.0, novelty_score=0.8)
    y_high = re_high.adjusted_yield(budget=5.0, novelty_score=0.8)
    assert y_high >= y_low


def test_regime_economics_regime_id_stored() -> None:
    re = _make_regime_economics(regime_id="test-regime")
    assert re.regime_id == "test-regime"


# ---------------------------------------------------------------------------
# BudgetAllocation tests
# ---------------------------------------------------------------------------

def test_budget_allocation_fractions_sum_to_one() -> None:
    alloc = _make_budget_allocation()
    fractions = alloc.fractions()
    total = sum(fractions.values())
    assert abs(total - 1.0) < 1e-9


def test_budget_allocation_fractions_non_negative() -> None:
    alloc = _make_budget_allocation()
    for f in alloc.fractions().values():
        assert f >= 0.0


def test_budget_allocation_total_budget_stored() -> None:
    alloc = _make_budget_allocation()
    assert alloc.total_budget == 10.0


def test_budget_allocation_amounts_sum_to_total() -> None:
    alloc = _make_budget_allocation()
    assert abs(sum(alloc.amounts.values()) - alloc.total_budget) < 1e-9


# ---------------------------------------------------------------------------
# YieldForecast tests
# ---------------------------------------------------------------------------

def test_yield_forecast_is_reliable_true_for_high_confidence() -> None:
    model = _make_yield_model()
    forecast = YieldForecast(
        model_id=model.model_id,
        budget_points=[1.0, 5.0, 10.0],
        yield_points=[model.yield_at(b) for b in [1.0, 5.0, 10.0]],
        confidence=0.9,
        forecast_horizon=20.0,
    )
    assert forecast.is_reliable() is True


def test_yield_forecast_is_reliable_false_for_low_confidence() -> None:
    model = _make_yield_model()
    forecast = YieldForecast(
        model_id=model.model_id,
        budget_points=[1.0, 5.0],
        yield_points=[1.0, 3.0],
        confidence=0.2,
        forecast_horizon=20.0,
    )
    assert forecast.is_reliable() is False


def test_yield_forecast_budget_and_yield_same_length() -> None:
    forecast = _make_yield_forecast()
    assert len(forecast.budget_points) == len(forecast.yield_points)


# ---------------------------------------------------------------------------
# EconomicEquilibrium tests
# ---------------------------------------------------------------------------

def test_equilibrium_max_marginal_deviation_correct() -> None:
    eq = EconomicEquilibrium(
        equilibrium_id="eq-test",
        regime_ids=["r1", "r2"],
        marginal_values={"r1": 1.0, "r2": 3.0},
        allocations={"r1": 5.0, "r2": 5.0},
        total_budget=10.0,
    )
    dev = eq.max_marginal_deviation()
    assert abs(dev - 2.0) < 1e-9


def test_equilibrium_max_marginal_deviation_zero_when_equal() -> None:
    eq = EconomicEquilibrium(
        equilibrium_id="eq-equal",
        regime_ids=["r1", "r2"],
        marginal_values={"r1": 2.0, "r2": 2.0},
        allocations={"r1": 5.0, "r2": 5.0},
        total_budget=10.0,
    )
    assert eq.max_marginal_deviation() == 0.0


def test_equilibrium_regime_ids_stored() -> None:
    eq = _make_equilibrium(num_regimes=4)
    assert len(eq.regime_ids) == 4


def test_equilibrium_total_budget_stored() -> None:
    eq = _make_equilibrium()
    assert eq.total_budget == 9.0
