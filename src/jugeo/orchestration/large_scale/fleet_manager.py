"""
Multi-strategy fleet competition manager.

Generalises Comet-H's single linear scorer to a fleet of competing strategies
that bid on moves.  Winner selection uses Pareto dominance across multiple
objectives: score, confidence, cost, and expected drift reduction.
"""
from __future__ import annotations

import time
import uuid
from collections import defaultdict
from typing import Any, Optional

from .models import (
    Bid,
    FleetResult,
    MoveCategory,
    ObligationKind,
    ObligationPresheaf,
    MoveResult,
    SemanticMove,
    Strategy,
    Surface,
)

__all__ = ["FleetManager"]


class FleetManager:
    """Fleet of competing strategies that bid on semantic moves."""

    def __init__(self) -> None:
        self._strategies: dict[str, Strategy] = {}
        self._history: dict[str, list[tuple[str, bool]]] = defaultdict(list)

    # ------------------------------------------------------------------
    # Strategy management
    # ------------------------------------------------------------------

    def register_strategy(self, strategy: Strategy) -> None:
        """Register a new strategy (or overwrite an existing one)."""
        self._strategies[strategy.id] = strategy

    # ------------------------------------------------------------------
    # Bidding
    # ------------------------------------------------------------------

    def generate_bids(
        self,
        state: dict[str, Any],
        obligations: ObligationPresheaf,
        available_moves: list[SemanticMove],
    ) -> list[Bid]:
        """Generate bids from all strategies over all available moves.

        If *available_moves* is empty, generate synthetic moves from obligation
        pressure so the fleet always has something to bid on.
        """
        moves = list(available_moves)
        if not moves:
            moves = self._synthetic_moves_from_obligations(obligations)
        if not moves:
            return []

        bids: list[Bid] = []
        for strategy in self._strategies.values():
            for move in moves:
                score = self._score_move(strategy, move, obligations, state)
                confidence = self._confidence(strategy)
                bids.append(
                    Bid(
                        strategy_id=strategy.id,
                        move_id=move.id,
                        score=score,
                        confidence=confidence,
                        estimated_cost=move.estimated_cost,
                        surface_targets=list(move.target_surfaces),
                        expected_drift_reduction=score * 0.1,
                    )
                )
        return bids

    def select_winner(self, bids: list[Bid]) -> FleetResult:
        """Select the winning bid using Pareto filtering + tie-breaking."""
        if not bids:
            fallback = Bid(strategy_id="none", move_id="none", score=0.0)
            return FleetResult(winning_bid=fallback, reason="no bids")

        if len(bids) == 1:
            return FleetResult(winning_bid=bids[0], reason="single bid")

        pareto = self._pareto_filter(bids)
        winner = self._tiebreak(pareto)
        runner_ups = [b for b in pareto if b is not winner]
        return FleetResult(
            winning_bid=winner,
            runner_up_bids=runner_ups,
            reason=f"pareto front size={len(pareto)}",
        )

    # ------------------------------------------------------------------
    # Pareto filtering
    # ------------------------------------------------------------------

    def _pareto_filter(self, bids: list[Bid]) -> list[Bid]:
        """Return the Pareto-non-dominated subset.

        A bid is dominated if another bid is strictly better on ALL four
        objectives: score↑, confidence↑, -estimated_cost↑ (lower cost is
        better), expected_drift_reduction↑.
        """
        non_dominated: list[Bid] = []
        for candidate in bids:
            dominated = False
            for other in bids:
                if other is candidate:
                    continue
                if (
                    other.score >= candidate.score
                    and other.confidence >= candidate.confidence
                    and other.estimated_cost <= candidate.estimated_cost
                    and other.expected_drift_reduction >= candidate.expected_drift_reduction
                    and (
                        other.score > candidate.score
                        or other.confidence > candidate.confidence
                        or other.estimated_cost < candidate.estimated_cost
                        or other.expected_drift_reduction > candidate.expected_drift_reduction
                    )
                ):
                    dominated = True
                    break
            if not dominated:
                non_dominated.append(candidate)
        return non_dominated if non_dominated else list(bids)

    def _tiebreak(self, pareto_bids: list[Bid]) -> Bid:
        """Among Pareto-optimal bids pick highest score; then lowest cost."""
        return max(
            pareto_bids,
            key=lambda b: (b.score, -b.estimated_cost),
        )

    # ------------------------------------------------------------------
    # Evaluation helpers
    # ------------------------------------------------------------------

    def _evaluate_bid(self, bid: Bid, state: dict[str, Any]) -> float:
        """Score a single bid against the current state (for external use)."""
        return bid.score * bid.confidence - bid.estimated_cost * 0.01

    def _score_move(
        self,
        strategy: Strategy,
        move: SemanticMove,
        obligations: ObligationPresheaf,
        state: dict[str, Any],
    ) -> float:
        """Score a move under a strategy's weights and the obligation landscape."""
        weights = strategy.scoring_weights
        score = 0.0

        # Category weight
        cat_weight = weights.get(move.category.value, 0.5)
        score += cat_weight * move.priority

        # Obligation pressure bonus
        for kind in move.generates_obligations:
            kind_str = kind.value
            pressure = obligations.pressure_by_kind.get(kind_str, 0.0)
            score += pressure * weights.get("obligation_pressure", 0.3)

        # Cost penalty
        score -= move.estimated_cost * weights.get("cost_penalty", 0.05)

        return max(0.0, score)

    def _confidence(self, strategy: Strategy) -> float:
        """Strategy confidence based on historical success rate."""
        rate = self._strategy_success_rate(strategy.id)
        # Blend: 50% base + 50% empirical (handles cold-start)
        return 0.5 + 0.5 * rate

    # ------------------------------------------------------------------
    # Feedback
    # ------------------------------------------------------------------

    def update_strategy_weights(
        self, strategy_id: str, result: MoveResult
    ) -> None:
        """Update a strategy's scoring weights via exponential moving average."""
        strategy = self._strategies.get(strategy_id)
        if strategy is None:
            return

        success = result.success
        self._history[strategy_id].append((result.move_id, success))

        signal = 1.0 if success else 0.0
        alpha = 0.1
        for key in list(strategy.scoring_weights):
            old = strategy.scoring_weights[key]
            strategy.scoring_weights[key] = (1 - alpha) * old + alpha * signal

    def _strategy_success_rate(self, strategy_id: str) -> float:
        """Fraction of past moves that succeeded for this strategy."""
        hist = self._history.get(strategy_id)
        if not hist:
            return 0.5  # prior
        return sum(1 for _, ok in hist if ok) / len(hist)

    def remove_underperformers(self, min_success_rate: float = 0.1) -> None:
        """Remove strategies whose success rate is below *min_success_rate*."""
        to_remove: list[str] = []
        for sid in list(self._strategies):
            hist = self._history.get(sid)
            if hist and len(hist) >= 5:
                rate = self._strategy_success_rate(sid)
                if rate < min_success_rate:
                    to_remove.append(sid)
        for sid in to_remove:
            del self._strategies[sid]

    # ------------------------------------------------------------------
    # Defaults
    # ------------------------------------------------------------------

    def default_strategies(self) -> list[Strategy]:
        """Create and register five default domain-agnostic strategies."""
        defaults = [
            Strategy(
                id="strat-ideation",
                name="ideation",
                move_generator="ideation",
                scoring_weights={
                    "ideation": 1.0, "construction": 0.8, "verification": 0.2,
                    "obligation_pressure": 0.3, "cost_penalty": 0.05,
                },
                budget_fraction=0.25,
            ),
            Strategy(
                id="strat-verification",
                name="verification",
                move_generator="verification",
                scoring_weights={
                    "verification": 1.0, "grounding": 0.7, "testing": 0.6,
                    "obligation_pressure": 0.5, "cost_penalty": 0.1,
                },
                budget_fraction=0.25,
            ),
            Strategy(
                id="strat-grounding",
                name="grounding",
                move_generator="grounding",
                scoring_weights={
                    "grounding": 1.0, "verification": 0.5, "audit": 0.4,
                    "obligation_pressure": 0.6, "cost_penalty": 0.08,
                },
                budget_fraction=0.2,
            ),
            Strategy(
                id="strat-audit",
                name="audit",
                move_generator="audit",
                scoring_weights={
                    "audit": 1.0, "review": 0.7, "documentation": 0.3,
                    "obligation_pressure": 0.4, "cost_penalty": 0.06,
                },
                budget_fraction=0.15,
            ),
            Strategy(
                id="strat-repair",
                name="repair",
                move_generator="repair",
                scoring_weights={
                    "repair": 1.0, "refinement": 0.8, "construction": 0.5,
                    "obligation_pressure": 0.7, "cost_penalty": 0.12,
                },
                budget_fraction=0.15,
            ),
        ]
        for s in defaults:
            self.register_strategy(s)
        return defaults

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    def statistics(self) -> dict[str, Any]:
        """Summary statistics about the fleet."""
        return {
            "strategy_count": len(self._strategies),
            "strategies": {
                sid: {
                    "name": s.name,
                    "success_rate": self._strategy_success_rate(sid),
                    "moves_evaluated": len(self._history.get(sid, [])),
                }
                for sid, s in self._strategies.items()
            },
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _synthetic_moves_from_obligations(
        self, obligations: ObligationPresheaf
    ) -> list[SemanticMove]:
        """Create placeholder moves when no explicit moves are available."""
        moves: list[SemanticMove] = []
        kind_to_cat = {
            ObligationKind.VERIFICATION.value: MoveCategory.VERIFICATION,
            ObligationKind.GROUNDING.value: MoveCategory.GROUNDING,
            ObligationKind.AUDIT.value: MoveCategory.AUDIT,
            ObligationKind.DOCUMENTATION.value: MoveCategory.DOCUMENTATION,
            ObligationKind.BENCHMARK.value: MoveCategory.BENCHMARKING,
            ObligationKind.TESTING.value: MoveCategory.TESTING,
            ObligationKind.REVIEW.value: MoveCategory.REVIEW,
            ObligationKind.DEPLOYMENT.value: MoveCategory.DEPLOYMENT,
            ObligationKind.CLEANUP.value: MoveCategory.REFINEMENT,
        }
        for kind_str, pressure in obligations.pressure_by_kind.items():
            if pressure <= 0:
                continue
            cat = kind_to_cat.get(kind_str, MoveCategory.CONSTRUCTION)
            moves.append(
                SemanticMove(
                    id=str(uuid.uuid4()),
                    category=cat,
                    name=f"synthetic-{kind_str}",
                    description=f"Synthetic move for {kind_str} pressure",
                    generates_obligations=[],
                    estimated_cost=1.0,
                    priority=pressure,
                )
            )
        return moves
