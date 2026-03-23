from pathlib import Path
import sys

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "src" / "jugeo").exists())
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from jugeo.ideation.theorem_economics.integration import (
    TheoremEconomicsIntegration,
    SchedulerEconomicsBridge,
    CopilotEconomicsAdvisor,
    EconomicEventBus,
    PortfolioReporter,
)
from jugeo.ideation.theorem_economics.models import TheoremYieldModel, InvestmentSchedule
from jugeo.ideation.ideas import (
    IdeaProposal,
    Idea,
    GainProfile,
    TrustStatus,
    ValidationPath,
    IdeaPortfolio,
)
from jugeo.ideation.novelty import NoveltyScore
from jugeo.ideation.scheduling import IdeationSchedule


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_yield_model(
    regime_id: str = "r1",
    saturation: float = 10.0,
    rate: float = 0.4,
) -> TheoremYieldModel:
    return TheoremYieldModel(
        model_id=f"model-{regime_id}",
        regime_id=regime_id,
        saturation_yield=saturation,
        growth_rate=rate,
        current_budget=0.0,
        empirical_data=[],
    )


def _make_models(n: int = 3) -> list[TheoremYieldModel]:
    return [
        _make_yield_model(f"r{i}", saturation=10.0 + i * 2.0, rate=0.3 + i * 0.1)
        for i in range(n)
    ]


def _make_investment_schedule(
    regime_ids: list[str] | None = None,
    total_budget: float = 10.0,
) -> InvestmentSchedule:
    if regime_ids is None:
        regime_ids = ["r0", "r1"]
    per = total_budget / len(regime_ids)
    return InvestmentSchedule(
        schedule_id="bridge-sched",
        regime_ids=regime_ids,
        allocations={rid: per for rid in regime_ids},
        total_budget=total_budget,
        expected_yield=4.0,
    )


def _make_idea(idea_id: str = "idea-1") -> Idea:
    return Idea(
        idea_id=idea_id,
        title="Test theorem idea",
        purpose="improve yield",
        target_area="semantic futures",
        hypothesis="A focused bridge should unlock reusable lemmas.",
        predicted_gain=GainProfile(
            theorem_yield=4.0,
            bridge_impact=2.0,
            cost=1.0,
            uncertainty=0.2,
        ),
        novelty_score=0.7,
        validation_plan=ValidationPath(
            steps=("state the bridge", "prove witness"),
            required_evidence=("witness proof",),
            success_criteria=("reusable theorem",),
        ),
        trust_status=TrustStatus.PROVISIONAL,
    )


def _make_integration(num_regimes: int = 3) -> TheoremEconomicsIntegration:
    models = _make_models(num_regimes)
    return TheoremEconomicsIntegration(yield_models=models)


# ---------------------------------------------------------------------------
# TheoremEconomicsIntegration tests
# ---------------------------------------------------------------------------

def test_integration_evaluate_idea_returns_dict() -> None:
    integration = _make_integration()
    idea = _make_idea()
    result = integration.evaluate_idea(idea)
    assert isinstance(result, dict)


def test_integration_evaluate_idea_has_economic_value_key() -> None:
    integration = _make_integration()
    idea = _make_idea()
    result = integration.evaluate_idea(idea)
    assert "economic_value" in result


def test_integration_evaluate_idea_economic_value_is_float() -> None:
    integration = _make_integration()
    idea = _make_idea()
    result = integration.evaluate_idea(idea)
    assert isinstance(result["economic_value"], float)


def test_integration_evaluate_idea_economic_value_non_negative() -> None:
    integration = _make_integration()
    idea = _make_idea()
    result = integration.evaluate_idea(idea)
    assert result["economic_value"] >= 0.0


def test_integration_recommend_schedule_returns_investment_schedule() -> None:
    integration = _make_integration()
    sched = integration.recommend_schedule(total_budget=15.0)
    assert isinstance(sched, InvestmentSchedule)


def test_integration_recommend_schedule_correct_total_budget() -> None:
    integration = _make_integration()
    total = 15.0
    sched = integration.recommend_schedule(total_budget=total)
    assert abs(sum(sched.allocations.values()) - total) < 1e-6


def test_integration_recommend_schedule_affected_by_novelty() -> None:
    models = _make_models(3)
    integration_standard = TheoremEconomicsIntegration(yield_models=models)
    integration_novel = TheoremEconomicsIntegration(
        yield_models=models,
        novelty_scores={"r0": NoveltyScore(score=0.9, regime_id="r0"),
                        "r1": NoveltyScore(score=0.9, regime_id="r1"),
                        "r2": NoveltyScore(score=0.9, regime_id="r2")},
    )
    sched_std = integration_standard.recommend_schedule(total_budget=10.0)
    sched_novel = integration_novel.recommend_schedule(total_budget=10.0)
    assert isinstance(sched_std, InvestmentSchedule)
    assert isinstance(sched_novel, InvestmentSchedule)


def test_integration_evaluate_idea_proposal() -> None:
    integration = _make_integration()
    proposal = IdeaProposal(
        title="Bridge theorem",
        hypothesis="bridges unlock lemmas",
        predicted_yield=3.0,
    )
    result = integration.evaluate_idea(proposal)
    assert "economic_value" in result


# ---------------------------------------------------------------------------
# SchedulerEconomicsBridge tests
# ---------------------------------------------------------------------------

def test_scheduler_bridge_returns_ideation_schedule() -> None:
    sched = _make_investment_schedule()
    bridge = SchedulerEconomicsBridge()
    ideation = bridge.bridge(sched)
    assert isinstance(ideation, IdeationSchedule)


def test_scheduler_bridge_economic_value_is_positive() -> None:
    sched = _make_investment_schedule()
    bridge = SchedulerEconomicsBridge()
    value = bridge.economic_value_of_ideation(sched)
    assert value > 0.0


def test_scheduler_bridge_ideation_schedule_has_entries() -> None:
    sched = _make_investment_schedule(regime_ids=["r0", "r1", "r2"], total_budget=15.0)
    bridge = SchedulerEconomicsBridge()
    ideation = bridge.bridge(sched)
    assert ideation is not None


def test_scheduler_bridge_economic_value_of_empty_schedule() -> None:
    sched = InvestmentSchedule(
        schedule_id="empty",
        regime_ids=[],
        allocations={},
        total_budget=0.0,
        expected_yield=0.0,
    )
    bridge = SchedulerEconomicsBridge()
    value = bridge.economic_value_of_ideation(sched)
    assert value >= 0.0


def test_scheduler_bridge_economic_value_increases_with_budget() -> None:
    sched_small = _make_investment_schedule(total_budget=5.0)
    sched_large = _make_investment_schedule(total_budget=20.0)
    bridge = SchedulerEconomicsBridge()
    val_small = bridge.economic_value_of_ideation(sched_small)
    val_large = bridge.economic_value_of_ideation(sched_large)
    assert val_large >= val_small


# ---------------------------------------------------------------------------
# CopilotEconomicsAdvisor tests
# ---------------------------------------------------------------------------

def test_advisor_advise_allocation_returns_non_empty_string() -> None:
    models = _make_models(3)
    advisor = CopilotEconomicsAdvisor(yield_models=models)
    sched = _make_investment_schedule(regime_ids=["r0", "r1", "r2"])
    advice = advisor.advise_allocation(sched)
    assert isinstance(advice, str)
    assert len(advice) > 0


def test_advisor_interpret_marginal_values_returns_string() -> None:
    models = _make_models(3)
    advisor = CopilotEconomicsAdvisor(yield_models=models)
    marginal_values = {m.regime_id: m.marginal_yield(5.0) for m in models}
    interpretation = advisor.interpret_marginal_values(marginal_values)
    assert isinstance(interpretation, str)
    assert len(interpretation) > 0


def test_advisor_investment_report_returns_non_empty_string() -> None:
    models = _make_models(3)
    advisor = CopilotEconomicsAdvisor(yield_models=models)
    sched = _make_investment_schedule(regime_ids=["r0", "r1", "r2"])
    report = advisor.investment_report(sched)
    assert isinstance(report, str)
    assert len(report) > 0


def test_advisor_advice_contains_regime_info() -> None:
    models = _make_models(2)
    advisor = CopilotEconomicsAdvisor(yield_models=models)
    sched = _make_investment_schedule(regime_ids=["r0", "r1"])
    advice = advisor.advise_allocation(sched)
    assert isinstance(advice, str)


def test_advisor_marginal_interpretation_mentions_diminishing() -> None:
    models = _make_models(2)
    advisor = CopilotEconomicsAdvisor(yield_models=models)
    high_mv = {m.regime_id: 0.01 for m in models}
    interpretation = advisor.interpret_marginal_values(high_mv)
    assert isinstance(interpretation, str)


# ---------------------------------------------------------------------------
# EconomicEventBus tests
# ---------------------------------------------------------------------------

def test_event_bus_subscribe_and_publish() -> None:
    bus = EconomicEventBus()
    received = []
    bus.subscribe("test_event", lambda e: received.append(e))
    bus.publish("test_event", {"data": 42})
    assert len(received) == 1
    assert received[0]["data"] == 42


def test_event_bus_handler_count_increments() -> None:
    bus = EconomicEventBus()
    initial = bus.handler_count("budget_updated")
    bus.subscribe("budget_updated", lambda e: None)
    bus.subscribe("budget_updated", lambda e: None)
    assert bus.handler_count("budget_updated") == initial + 2


def test_event_bus_multiple_subscribers_all_receive() -> None:
    bus = EconomicEventBus()
    results = []
    bus.subscribe("ev", lambda e: results.append("handler1"))
    bus.subscribe("ev", lambda e: results.append("handler2"))
    bus.publish("ev", {})
    assert "handler1" in results
    assert "handler2" in results


def test_event_bus_different_events_isolated() -> None:
    bus = EconomicEventBus()
    received_a = []
    received_b = []
    bus.subscribe("event_a", lambda e: received_a.append(e))
    bus.subscribe("event_b", lambda e: received_b.append(e))
    bus.publish("event_a", {"x": 1})
    assert len(received_a) == 1
    assert len(received_b) == 0


def test_event_bus_publish_without_subscribers_does_not_raise() -> None:
    bus = EconomicEventBus()
    bus.publish("no_subscribers", {"data": "test"})


def test_event_bus_handler_count_zero_initially() -> None:
    bus = EconomicEventBus()
    assert bus.handler_count("fresh_event") == 0


# ---------------------------------------------------------------------------
# PortfolioReporter tests
# ---------------------------------------------------------------------------

def test_portfolio_reporter_allocation_report_returns_string() -> None:
    models = _make_models(3)
    reporter = PortfolioReporter(yield_models=models)
    sched = _make_investment_schedule(regime_ids=["r0", "r1", "r2"])
    report = reporter.allocation_report(sched)
    assert isinstance(report, str)
    assert len(report) > 0


def test_portfolio_reporter_report_contains_budget_info() -> None:
    models = _make_models(2)
    reporter = PortfolioReporter(yield_models=models)
    sched = _make_investment_schedule(regime_ids=["r0", "r1"], total_budget=10.0)
    report = reporter.allocation_report(sched)
    assert isinstance(report, str)


def test_portfolio_reporter_report_for_empty_schedule() -> None:
    models = _make_models(1)
    reporter = PortfolioReporter(yield_models=models)
    empty_sched = InvestmentSchedule(
        schedule_id="empty",
        regime_ids=[],
        allocations={},
        total_budget=0.0,
        expected_yield=0.0,
    )
    report = reporter.allocation_report(empty_sched)
    assert isinstance(report, str)


# ---------------------------------------------------------------------------
# Cross-module tests
# ---------------------------------------------------------------------------

def test_cross_module_novelty_score_affects_recommendation() -> None:
    models = _make_models(3)
    low_novelty = {m.regime_id: NoveltyScore(score=0.1, regime_id=m.regime_id) for m in models}
    high_novelty = {m.regime_id: NoveltyScore(score=0.9, regime_id=m.regime_id) for m in models}
    integration_low = TheoremEconomicsIntegration(
        yield_models=models, novelty_scores=low_novelty
    )
    integration_high = TheoremEconomicsIntegration(
        yield_models=models, novelty_scores=high_novelty
    )
    sched_low = integration_low.recommend_schedule(total_budget=10.0)
    sched_high = integration_high.recommend_schedule(total_budget=10.0)
    assert isinstance(sched_low, InvestmentSchedule)
    assert isinstance(sched_high, InvestmentSchedule)


def test_cross_module_idea_proposal_can_be_evaluated() -> None:
    integration = _make_integration()
    idea = _make_idea("cross-idea")
    result = integration.evaluate_idea(idea)
    assert "economic_value" in result
    assert result["economic_value"] >= 0.0


def test_cross_module_ideation_schedule_bridges_correctly() -> None:
    inv_sched = _make_investment_schedule(regime_ids=["r0", "r1", "r2"])
    bridge = SchedulerEconomicsBridge()
    ideation = bridge.bridge(inv_sched)
    assert isinstance(ideation, IdeationSchedule)
    value = bridge.economic_value_of_ideation(inv_sched)
    assert value >= 0.0


def test_cross_module_full_pipeline() -> None:
    models = _make_models(4)
    novelty = {m.regime_id: NoveltyScore(score=0.6, regime_id=m.regime_id) for m in models}
    integration = TheoremEconomicsIntegration(yield_models=models, novelty_scores=novelty)

    sched = integration.recommend_schedule(total_budget=20.0)
    assert isinstance(sched, InvestmentSchedule)

    bridge = SchedulerEconomicsBridge()
    ideation = bridge.bridge(sched)
    assert isinstance(ideation, IdeationSchedule)

    advisor = CopilotEconomicsAdvisor(yield_models=models)
    report = advisor.investment_report(sched)
    assert len(report) > 0


def test_cross_module_event_bus_with_integration() -> None:
    bus = EconomicEventBus()
    events = []
    bus.subscribe("recommendation_made", lambda e: events.append(e))

    integration = _make_integration()
    sched = integration.recommend_schedule(total_budget=10.0)
    bus.publish("recommendation_made", {"schedule_id": sched.schedule_id})
    assert len(events) == 1
    assert events[0]["schedule_id"] == sched.schedule_id
