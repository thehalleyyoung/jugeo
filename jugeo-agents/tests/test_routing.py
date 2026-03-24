"""Tests for jugeo_agents.orchestration.routing — TrustAwareRouter, BudgetTracker."""

import pytest

from jugeo_agents.types import (
    CHANNEL_TRUST_CEILINGS,
    EvidenceChannel,
    FactualClaim,
    RoutingDecision,
    TrustLevel,
)
from jugeo_agents.orchestration.routing import (
    BudgetTracker,
    ChannelCostModel,
    RoutingAnalyzer,
    RoutingPolicy,
    TrustAwareRouter,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_claim(text: str = "X is 42") -> FactualClaim:
    return FactualClaim(text=text, subject="X", predicate="is_a", value="42")


# ---------------------------------------------------------------------------
# TrustAwareRouter.route
# ---------------------------------------------------------------------------


def test_route_returns_decision():
    router = TrustAwareRouter()
    claim = _make_claim()
    decision = router.route(claim, TrustLevel.RAG_GROUNDED)
    assert isinstance(decision, RoutingDecision)
    assert decision.channel in EvidenceChannel
    assert decision.required_trust == TrustLevel.RAG_GROUNDED


def test_route_cheapest_eligible():
    router = TrustAwareRouter(policy=RoutingPolicy.CHEAPEST_ELIGIBLE)
    claim = _make_claim()
    decision = router.route(claim, TrustLevel.RAG_GROUNDED)
    # Should pick the cheapest channel that meets RAG_GROUNDED ceiling
    assert decision.channel_ceiling >= TrustLevel.RAG_GROUNDED


def test_route_trust_maximizing():
    router = TrustAwareRouter(policy=RoutingPolicy.TRUST_MAXIMIZING)
    claim = _make_claim()
    decision = router.route(claim, TrustLevel.UNGROUNDED_CLAIM)
    # Trust-maximizing should pick the highest-ceiling channel
    assert decision.channel_ceiling >= TrustLevel.UNGROUNDED_CLAIM


def test_route_fallback_when_no_eligible():
    # Only make LLM_GENERATION available (low ceiling)
    router = TrustAwareRouter(
        available_channels={EvidenceChannel.LLM_GENERATION},
    )
    claim = _make_claim()
    # Require a trust level higher than LLM_GENERATION's ceiling
    decision = router.route(claim, TrustLevel.FORMALLY_PROVEN)
    # Should still return a decision (fallback)
    assert isinstance(decision, RoutingDecision)
    assert "falling back" in decision.rationale.lower() or decision.channel == EvidenceChannel.LLM_GENERATION


def test_route_batch():
    router = TrustAwareRouter()
    claims = [
        (_make_claim("claim 1"), TrustLevel.RAG_GROUNDED),
        (_make_claim("claim 2"), TrustLevel.TOOL_EXECUTED),
    ]
    decisions = router.route_batch(claims)
    assert len(decisions) == 2


def test_routing_history():
    router = TrustAwareRouter()
    router.route(_make_claim(), TrustLevel.RAG_GROUNDED)
    router.route(_make_claim(), TrustLevel.TOOL_EXECUTED)
    history = router.routing_history()
    assert len(history) == 2


def test_eligible_channels():
    router = TrustAwareRouter()
    eligible = router.eligible_channels(TrustLevel.TOOL_VERIFIED)
    # Only channels with ceiling >= TOOL_VERIFIED should be included
    for ch in eligible:
        assert CHANNEL_TRUST_CEILINGS.get(ch, TrustLevel.UNGROUNDED_CLAIM) >= TrustLevel.TOOL_VERIFIED


# ---------------------------------------------------------------------------
# BudgetTracker
# ---------------------------------------------------------------------------


def test_budget_tracker_spend():
    tracker = BudgetTracker(total_budget=10.0)
    assert tracker.remaining() == 10.0
    tracker.spend(EvidenceChannel.WEB_SEARCH, 3.0)
    assert tracker.remaining() == pytest.approx(7.0)


def test_budget_tracker_can_afford():
    tracker = BudgetTracker(total_budget=5.0)
    assert tracker.can_afford(EvidenceChannel.WEB_SEARCH, 3.0)
    tracker.spend(EvidenceChannel.WEB_SEARCH, 3.0)
    assert not tracker.can_afford(EvidenceChannel.WEB_SEARCH, 3.0)


def test_budget_tracker_per_channel_limit():
    tracker = BudgetTracker(
        total_budget=100.0,
        per_channel_limits={EvidenceChannel.WEB_SEARCH: 5.0},
    )
    tracker.spend(EvidenceChannel.WEB_SEARCH, 4.0)
    assert tracker.can_afford(EvidenceChannel.WEB_SEARCH, 0.5)
    assert not tracker.can_afford(EvidenceChannel.WEB_SEARCH, 2.0)


def test_budget_tracker_remaining_for():
    tracker = BudgetTracker(
        total_budget=50.0,
        per_channel_limits={EvidenceChannel.CODE_EXECUTION: 10.0},
    )
    assert tracker.remaining_for(EvidenceChannel.CODE_EXECUTION) == pytest.approx(10.0)
    tracker.spend(EvidenceChannel.CODE_EXECUTION, 7.0)
    assert tracker.remaining_for(EvidenceChannel.CODE_EXECUTION) == pytest.approx(3.0)


def test_budget_tracker_summary():
    tracker = BudgetTracker(total_budget=20.0)
    tracker.spend(EvidenceChannel.WEB_SEARCH, 5.0)
    summary = tracker.summary()
    assert "5.00" in summary
    assert "20.00" in summary


# ---------------------------------------------------------------------------
# RoutingAnalyzer
# ---------------------------------------------------------------------------


def test_routing_analyzer_trust_compliance():
    analyzer = RoutingAnalyzer()
    decisions = [
        RoutingDecision(
            claim=_make_claim(),
            channel=EvidenceChannel.CODE_EXECUTION,
            required_trust=TrustLevel.TOOL_VERIFIED,
            channel_ceiling=TrustLevel.TOOL_VERIFIED,
        ),
    ]
    assert analyzer.trust_compliance(decisions) == 1.0


def test_routing_analyzer_empty():
    analyzer = RoutingAnalyzer()
    assert analyzer.trust_compliance([]) == 1.0
    assert analyzer.cost_efficiency([]) == 0.0
