"""Evidence Routing — cost/trust-aware routing of verification requests.

This module implements the JuGeo routing layer: given a factual claim and a
required trust level, select the *cheapest* evidence channel whose trust
ceiling meets the requirement.  Routing policies, budget tracking, and
post-hoc analysis are all provided.
"""

from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any

from jugeo_agents.types import (
    CHANNEL_TRUST_CEILINGS,
    EvidenceChannel,
    FactualClaim,
    RoutingDecision,
    TrustLevel,
)

__all__ = [
    "ChannelCostModel",
    "TrustAwareRouter",
    "RoutingPolicy",
    "BudgetTracker",
    "RoutingAnalyzer",
]

# ---------------------------------------------------------------------------
# Default cost / reliability tables
# ---------------------------------------------------------------------------

_DEFAULT_BASE_COSTS: dict[EvidenceChannel, float] = {
    EvidenceChannel.CODE_EXECUTION: 0.5,
    EvidenceChannel.SQL_QUERY: 0.3,
    EvidenceChannel.API_CALL: 0.4,
    EvidenceChannel.WEB_SEARCH: 0.2,
    EvidenceChannel.RAG_RETRIEVAL: 0.1,
    EvidenceChannel.LLM_VERIFICATION: 0.05,
    EvidenceChannel.LLM_GENERATION: 0.02,
    EvidenceChannel.HUMAN_REVIEW: 10.0,
    EvidenceChannel.FORMAL_PROOF: 50.0,
}

_DEFAULT_RELIABILITY: dict[EvidenceChannel, float] = {
    EvidenceChannel.CODE_EXECUTION: 0.95,
    EvidenceChannel.SQL_QUERY: 0.93,
    EvidenceChannel.API_CALL: 0.85,
    EvidenceChannel.WEB_SEARCH: 0.75,
    EvidenceChannel.RAG_RETRIEVAL: 0.80,
    EvidenceChannel.LLM_VERIFICATION: 0.65,
    EvidenceChannel.LLM_GENERATION: 0.50,
    EvidenceChannel.HUMAN_REVIEW: 0.99,
    EvidenceChannel.FORMAL_PROOF: 0.999,
}

# Minimum reliability floor to avoid division-by-zero or extreme costs.
_RELIABILITY_FLOOR = 0.01


# ---------------------------------------------------------------------------
# 1. ChannelCostModel
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class ChannelCostModel:
    """Cost model for evidence channels.

    The effective cost of routing a claim to a channel is::

        base_cost × complexity_factor / reliability

    where *complexity_factor* is derived from the claim itself (length and
    metadata) and *reliability* is updated empirically via feedback.
    """

    base_costs: dict[EvidenceChannel, float] = field(default_factory=dict)
    reliability: dict[EvidenceChannel, float] = field(default_factory=dict)
    _successes: dict[EvidenceChannel, int] = field(
        default_factory=lambda: defaultdict(int),
    )
    _attempts: dict[EvidenceChannel, int] = field(
        default_factory=lambda: defaultdict(int),
    )

    def __post_init__(self) -> None:
        if not self.base_costs:
            self.base_costs = dict(_DEFAULT_BASE_COSTS)
        if not self.reliability:
            self.reliability = dict(_DEFAULT_RELIABILITY)

    # -- public API ---------------------------------------------------------

    def cost(self, channel: EvidenceChannel, claim: FactualClaim) -> float:
        """Effective cost of verifying *claim* via *channel*."""
        base = self.base_costs.get(channel, 1.0)
        rel = max(self.reliability.get(channel, 0.5), _RELIABILITY_FLOOR)
        return base * self._complexity_factor(claim) / rel

    def update_reliability(
        self, channel: EvidenceChannel, succeeded: bool,
    ) -> None:
        """Update the empirical reliability for *channel* after an attempt."""
        self._attempts[channel] += 1
        if succeeded:
            self._successes[channel] += 1
        total = self._attempts[channel]
        wins = self._successes[channel]
        # Blend prior (default) reliability with empirical observations using
        # a simple Bayesian-ish weighted update.  The prior has weight 10.
        prior_weight = 10
        prior = _DEFAULT_RELIABILITY.get(channel, 0.5)
        self.reliability[channel] = (
            (prior * prior_weight + wins) / (prior_weight + total)
        )

    # -- internals ----------------------------------------------------------

    @staticmethod
    def _complexity_factor(claim: FactualClaim) -> float:
        """Heuristic complexity multiplier derived from the claim.

        Short, simple claims get factor ~1.0; long or multi-part claims
        scale up to ~3.0.
        """
        length = len(claim.text)
        if length < 50:
            return 1.0
        if length < 200:
            return 1.0 + (length - 50) / 300  # up to ~1.5
        if length < 500:
            return 1.5 + (length - 200) / 600  # up to ~2.0
        return min(1.5 + length / 500, 3.0)


# ---------------------------------------------------------------------------
# 2. RoutingPolicy
# ---------------------------------------------------------------------------

class RoutingPolicy(Enum):
    """Configurable routing policies."""

    CHEAPEST_ELIGIBLE = auto()
    MOST_RELIABLE = auto()
    BALANCED = auto()
    TRUST_MAXIMIZING = auto()


# ---------------------------------------------------------------------------
# 3. TrustAwareRouter
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class TrustAwareRouter:
    """Main router: selects the best evidence channel for each claim.

    By default, the ``CHEAPEST_ELIGIBLE`` policy is used — pick the
    cheapest channel whose trust ceiling meets the requirement.  Other
    policies trade cost for reliability or trust headroom.
    """

    cost_model: ChannelCostModel = field(default_factory=ChannelCostModel)
    available_channels: set[EvidenceChannel] = field(
        default_factory=lambda: set(EvidenceChannel),
    )
    policy: RoutingPolicy = RoutingPolicy.CHEAPEST_ELIGIBLE
    _history: list[RoutingDecision] = field(default_factory=list)

    # -- core routing -------------------------------------------------------

    def route(
        self,
        claim: FactualClaim,
        required_trust: TrustLevel,
    ) -> RoutingDecision:
        """Select the best channel for *claim* under *required_trust*."""
        eligible = self.eligible_channels(required_trust)
        if not eligible:
            # Fall back to the highest-ceiling available channel.
            channel = self._highest_ceiling_channel()
            ceiling = CHANNEL_TRUST_CEILINGS.get(
                channel, TrustLevel.UNGROUNDED_CLAIM,
            )
            decision = RoutingDecision(
                claim=claim,
                channel=channel,
                required_trust=required_trust,
                channel_ceiling=ceiling,
                estimated_cost=self.cost_model.cost(channel, claim),
                rationale=(
                    "No channel meets the required trust level; "
                    "falling back to the highest-ceiling available channel."
                ),
                decision_id=uuid.uuid4().hex[:12],
            )
            self._history.append(decision)
            return decision

        channel = self._select_by_policy(eligible, claim, required_trust)
        ceiling = CHANNEL_TRUST_CEILINGS.get(
            channel, TrustLevel.UNGROUNDED_CLAIM,
        )
        decision = RoutingDecision(
            claim=claim,
            channel=channel,
            required_trust=required_trust,
            channel_ceiling=ceiling,
            estimated_cost=self.cost_model.cost(channel, claim),
            rationale=f"Selected via {self.policy.name} policy.",
            decision_id=uuid.uuid4().hex[:12],
        )
        self._history.append(decision)
        return decision

    def route_batch(
        self,
        claims: list[tuple[FactualClaim, TrustLevel]],
    ) -> list[RoutingDecision]:
        """Route a batch of ``(claim, required_trust)`` pairs."""
        return [self.route(claim, trust) for claim, trust in claims]

    # -- channel queries ----------------------------------------------------

    def eligible_channels(
        self, required_trust: TrustLevel,
    ) -> list[EvidenceChannel]:
        """Return available channels whose ceiling >= *required_trust*."""
        return [
            ch
            for ch in self.available_channels
            if CHANNEL_TRUST_CEILINGS.get(
                ch, TrustLevel.UNGROUNDED_CLAIM,
            )
            >= required_trust
        ]

    def cheapest_channel(
        self,
        required_trust: TrustLevel,
        claim: FactualClaim,
    ) -> EvidenceChannel | None:
        """Return the cheapest eligible channel, or ``None``."""
        eligible = self.eligible_channels(required_trust)
        if not eligible:
            return None
        return min(eligible, key=lambda ch: self.cost_model.cost(ch, claim))

    # -- history & summaries ------------------------------------------------

    def routing_history(self) -> list[RoutingDecision]:
        """Return a copy of all routing decisions made so far."""
        return list(self._history)

    def cost_summary(self) -> dict[str, Any]:
        """Total estimated cost by channel."""
        totals: dict[EvidenceChannel, float] = defaultdict(float)
        counts: dict[EvidenceChannel, int] = defaultdict(int)
        for d in self._history:
            totals[d.channel] += d.estimated_cost
            counts[d.channel] += 1
        return {
            "by_channel": {
                ch.value: {"total_cost": round(totals[ch], 4), "count": counts[ch]}
                for ch in totals
            },
            "grand_total": round(sum(totals.values()), 4),
            "decisions": len(self._history),
        }

    # -- internal helpers ---------------------------------------------------

    def _select_by_policy(
        self,
        eligible: list[EvidenceChannel],
        claim: FactualClaim,
        required_trust: TrustLevel,
    ) -> EvidenceChannel:
        """Choose one channel from *eligible* according to the active policy."""
        if self.policy is RoutingPolicy.CHEAPEST_ELIGIBLE:
            return min(
                eligible, key=lambda ch: self.cost_model.cost(ch, claim),
            )

        if self.policy is RoutingPolicy.MOST_RELIABLE:
            return max(
                eligible,
                key=lambda ch: self.cost_model.reliability.get(ch, 0.5),
            )

        if self.policy is RoutingPolicy.TRUST_MAXIMIZING:
            return max(
                eligible,
                key=lambda ch: CHANNEL_TRUST_CEILINGS.get(
                    ch, TrustLevel.UNGROUNDED_CLAIM,
                ),
            )

        # BALANCED: score = reliability / normalised_cost
        max_cost = max(
            self.cost_model.cost(ch, claim) for ch in eligible
        ) or 1.0
        return max(
            eligible,
            key=lambda ch: (
                self.cost_model.reliability.get(ch, 0.5)
                / (self.cost_model.cost(ch, claim) / max_cost + 0.01)
            ),
        )

    def _highest_ceiling_channel(self) -> EvidenceChannel:
        """Return the available channel with the highest trust ceiling."""
        return max(
            self.available_channels,
            key=lambda ch: CHANNEL_TRUST_CEILINGS.get(
                ch, TrustLevel.UNGROUNDED_CLAIM,
            ),
        )


# ---------------------------------------------------------------------------
# 4. BudgetTracker
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class BudgetTracker:
    """Track routing budget with optional per-channel limits."""

    total_budget: float = 100.0
    per_channel_limits: dict[EvidenceChannel, float] = field(
        default_factory=dict,
    )
    _spent_total: float = 0.0
    _spent_by_channel: dict[EvidenceChannel, float] = field(
        default_factory=lambda: defaultdict(float),
    )

    # -- public API ---------------------------------------------------------

    def can_afford(self, channel: EvidenceChannel, cost: float) -> bool:
        """Return ``True`` if *cost* fits within both global and channel budgets."""
        if self._spent_total + cost > self.total_budget:
            return False
        if channel in self.per_channel_limits:
            limit = self.per_channel_limits[channel]
            if self._spent_by_channel[channel] + cost > limit:
                return False
        return True

    def spend(self, channel: EvidenceChannel, cost: float) -> None:
        """Record a spend of *cost* on *channel*."""
        self._spent_total += cost
        self._spent_by_channel[channel] += cost

    def remaining(self) -> float:
        """Remaining global budget."""
        return max(self.total_budget - self._spent_total, 0.0)

    def remaining_for(self, channel: EvidenceChannel) -> float:
        """Remaining budget for a specific channel."""
        if channel not in self.per_channel_limits:
            return self.remaining()
        limit = self.per_channel_limits[channel]
        channel_remaining = max(limit - self._spent_by_channel[channel], 0.0)
        return min(channel_remaining, self.remaining())

    def summary(self) -> str:
        """Human-readable budget summary."""
        lines = [
            f"Budget: {self._spent_total:.2f} / {self.total_budget:.2f} "
            f"({self.remaining():.2f} remaining)",
        ]
        if self._spent_by_channel:
            lines.append("  Per-channel spend:")
            for ch in sorted(
                self._spent_by_channel, key=lambda c: c.value,
            ):
                spent = self._spent_by_channel[ch]
                limit_str = ""
                if ch in self.per_channel_limits:
                    limit_str = f" / {self.per_channel_limits[ch]:.2f}"
                lines.append(f"    {ch.value}: {spent:.2f}{limit_str}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 5. RoutingAnalyzer
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class RoutingAnalyzer:
    """Post-hoc analysis of routing decisions."""

    def trust_compliance(self, decisions: list[RoutingDecision]) -> float:
        """Fraction of decisions where channel ceiling >= required trust."""
        if not decisions:
            return 1.0
        compliant = sum(
            1
            for d in decisions
            if d.channel_ceiling >= d.required_trust
        )
        return compliant / len(decisions)

    def cost_efficiency(self, decisions: list[RoutingDecision]) -> float:
        """Average cost per trust-level achieved.

        Trust-level is the ceiling value normalised to [0, 1] (dividing by
        ``TrustLevel.FORMALLY_PROVEN``).
        """
        if not decisions:
            return 0.0
        max_trust = float(TrustLevel.FORMALLY_PROVEN)
        total_cost = 0.0
        total_trust = 0.0
        for d in decisions:
            total_cost += d.estimated_cost
            total_trust += d.channel_ceiling / max_trust
        if total_trust == 0.0:
            return float("inf")
        return total_cost / total_trust

    def channel_utilization(
        self, decisions: list[RoutingDecision],
    ) -> dict[EvidenceChannel, int]:
        """Count how many times each channel was selected."""
        counts: Counter[EvidenceChannel] = Counter()
        for d in decisions:
            counts[d.channel] += 1
        return dict(counts)

    def cost_breakdown(
        self, decisions: list[RoutingDecision],
    ) -> dict[EvidenceChannel, float]:
        """Total estimated cost by channel."""
        totals: dict[EvidenceChannel, float] = defaultdict(float)
        for d in decisions:
            totals[d.channel] += d.estimated_cost
        return dict(totals)

    def under_budget_ratio(
        self,
        decisions: list[RoutingDecision],
        budget: float,
    ) -> float:
        """Fraction of cumulative cost that fits within *budget*."""
        if not decisions:
            return 1.0
        cumulative = 0.0
        within = 0
        for d in decisions:
            cumulative += d.estimated_cost
            if cumulative <= budget:
                within += 1
        return within / len(decisions)

    def summary(self, decisions: list[RoutingDecision]) -> str:
        """Human-readable analysis summary."""
        if not decisions:
            return "No routing decisions to analyse."
        util = self.channel_utilization(decisions)
        total_cost = sum(d.estimated_cost for d in decisions)
        lines = [
            f"Routing Analysis ({len(decisions)} decisions)",
            f"  Trust compliance: {self.trust_compliance(decisions):.1%}",
            f"  Cost efficiency:  {self.cost_efficiency(decisions):.4f}",
            f"  Total est. cost:  {total_cost:.4f}",
            "  Channel utilisation:",
        ]
        for ch, count in sorted(util.items(), key=lambda kv: -kv[1]):
            lines.append(f"    {ch.value}: {count}")
        return "\n".join(lines)
