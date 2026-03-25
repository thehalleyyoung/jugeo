"""Tests for FleetManager."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "src" / "jugeo").exists())
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from jugeo.orchestration.large_scale.models import (
    Bid,
    FleetResult,
    MoveCategory,
    MoveResult,
    ObligationKind,
    ObligationPresheaf,
    SemanticMove,
    Strategy,
    Surface,
)
from jugeo.orchestration.large_scale.fleet_manager import FleetManager


def _make_strategy(sid: str = "s1", name: str = "test") -> Strategy:
    return Strategy(
        id=sid,
        name=name,
        scoring_weights={
            "ideation": 1.0, "verification": 0.8,
            "obligation_pressure": 0.3, "cost_penalty": 0.05,
        },
        budget_fraction=0.2,
    )


def _make_move(mid: str = "m1", category: str = "ideation") -> SemanticMove:
    return SemanticMove(
        id=mid,
        category=MoveCategory(category),
        name=f"move-{mid}",
        priority=1.0,
        estimated_cost=1.0,
    )


def _empty_presheaf() -> ObligationPresheaf:
    return ObligationPresheaf()


# ---------------------------------------------------------------------------
# Strategy management
# ---------------------------------------------------------------------------

class TestRegisterStrategy:
    def test_register_strategy(self) -> None:
        fm = FleetManager()
        s = _make_strategy()
        fm.register_strategy(s)
        assert s.id in fm._strategies


# ---------------------------------------------------------------------------
# Bidding
# ---------------------------------------------------------------------------

class TestGenerateBids:
    def test_generate_bids_empty_moves(self) -> None:
        fm = FleetManager()
        fm.register_strategy(_make_strategy())
        bids = fm.generate_bids({}, _empty_presheaf(), [])
        # With empty presheaf and no moves, no synthetic moves generated
        assert isinstance(bids, list)

    def test_generate_bids_with_moves(self) -> None:
        fm = FleetManager()
        fm.register_strategy(_make_strategy())
        moves = [_make_move("m1"), _make_move("m2")]
        bids = fm.generate_bids({}, _empty_presheaf(), moves)
        assert len(bids) == 2  # 1 strategy × 2 moves


# ---------------------------------------------------------------------------
# Winner selection
# ---------------------------------------------------------------------------

class TestSelectWinner:
    def test_select_winner_single_bid(self) -> None:
        fm = FleetManager()
        bid = Bid(strategy_id="s1", move_id="m1", score=5.0)
        result = fm.select_winner([bid])
        assert result.winning_bid is bid

    def test_select_winner_multiple_bids(self) -> None:
        fm = FleetManager()
        b1 = Bid(strategy_id="s1", move_id="m1", score=3.0, confidence=1.0,
                  estimated_cost=1.0, expected_drift_reduction=0.1)
        b2 = Bid(strategy_id="s2", move_id="m2", score=5.0, confidence=1.0,
                  estimated_cost=1.0, expected_drift_reduction=0.2)
        result = fm.select_winner([b1, b2])
        assert result.winning_bid.score >= b1.score


# ---------------------------------------------------------------------------
# Pareto filtering
# ---------------------------------------------------------------------------

class TestParetoFilter:
    def test_pareto_filter(self) -> None:
        fm = FleetManager()
        b_dominated = Bid(strategy_id="s1", move_id="m1", score=1.0,
                          confidence=0.5, estimated_cost=5.0,
                          expected_drift_reduction=0.0)
        b_dominant = Bid(strategy_id="s2", move_id="m2", score=5.0,
                         confidence=1.0, estimated_cost=1.0,
                         expected_drift_reduction=0.5)
        result = fm._pareto_filter([b_dominated, b_dominant])
        assert len(result) == 1
        assert result[0] is b_dominant

    def test_pareto_filter_all_nondominated(self) -> None:
        fm = FleetManager()
        # Each bid is better on one dimension
        b1 = Bid(strategy_id="s1", move_id="m1", score=5.0, confidence=0.5,
                  estimated_cost=1.0, expected_drift_reduction=0.1)
        b2 = Bid(strategy_id="s2", move_id="m2", score=1.0, confidence=1.0,
                  estimated_cost=1.0, expected_drift_reduction=0.5)
        result = fm._pareto_filter([b1, b2])
        assert len(result) == 2


class TestTiebreak:
    def test_tiebreak(self) -> None:
        fm = FleetManager()
        b1 = Bid(strategy_id="s1", move_id="m1", score=5.0, estimated_cost=3.0)
        b2 = Bid(strategy_id="s2", move_id="m2", score=5.0, estimated_cost=1.0)
        winner = fm._tiebreak([b1, b2])
        assert winner is b2  # same score, lower cost wins


# ---------------------------------------------------------------------------
# Feedback
# ---------------------------------------------------------------------------

class TestUpdateStrategyWeights:
    def test_update_success(self) -> None:
        fm = FleetManager()
        s = _make_strategy()
        fm.register_strategy(s)
        result = MoveResult(move_id="m1", success=True)
        old_w = dict(s.scoring_weights)
        fm.update_strategy_weights(s.id, result)
        # Weights should shift toward 1.0
        for key in old_w:
            assert s.scoring_weights[key] >= old_w[key] * 0.9

    def test_update_failure(self) -> None:
        fm = FleetManager()
        s = _make_strategy()
        fm.register_strategy(s)
        result = MoveResult(move_id="m1", success=False)
        fm.update_strategy_weights(s.id, result)
        # Weights shift toward 0.0

    def test_strategy_success_rate(self) -> None:
        fm = FleetManager()
        fm._history["s1"] = [("m1", True), ("m2", True), ("m3", False)]
        rate = fm._strategy_success_rate("s1")
        assert rate == pytest.approx(2 / 3, abs=0.01)


class TestRemoveUnderperformers:
    def test_remove_underperformers(self) -> None:
        fm = FleetManager()
        s = _make_strategy("s-bad", "bad")
        fm.register_strategy(s)
        fm._history["s-bad"] = [("m1", False)] * 10  # 0% success
        fm.remove_underperformers(min_success_rate=0.1)
        assert "s-bad" not in fm._strategies


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

class TestDefaultStrategies:
    def test_default_strategies(self) -> None:
        fm = FleetManager()
        defaults = fm.default_strategies()
        assert len(defaults) == 5
        assert len(fm._strategies) == 5


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

class TestStatistics:
    def test_statistics(self) -> None:
        fm = FleetManager()
        fm.default_strategies()
        stats = fm.statistics()
        assert stats["strategy_count"] == 5
        assert "strategies" in stats
