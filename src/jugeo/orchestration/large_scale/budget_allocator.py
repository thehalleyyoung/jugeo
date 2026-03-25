"""
Per-region budget management with obligation-aware rebalancing.
"""
from __future__ import annotations

import time
from collections import defaultdict
from typing import Any, Optional

from .models import BudgetAllocation, BudgetUsage, MoveCategory, ObligationPresheaf

__all__ = ["BudgetAllocator"]


class BudgetAllocator:
    """Allocate, track, and rebalance budget across regions and categories."""

    def __init__(self, total_budget: float) -> None:
        self._total_budget = total_budget
        self._allocation: BudgetAllocation | None = None
        self._spent_by_region: dict[str, float] = defaultdict(float)
        self._spent_by_category: dict[str, float] = defaultdict(float)
        self._total_spent: float = 0.0
        self._spend_timestamps: list[float] = []

    # ------------------------------------------------------------------
    # Allocation
    # ------------------------------------------------------------------

    def allocate(
        self,
        regions: list[str],
        priorities: dict[str, float],
        reserve_audit: float = 0.2,
        reserve_grounding: float = 0.1,
    ) -> BudgetAllocation:
        """Allocate budget proportionally to region priorities with reserves."""
        reserved = reserve_audit + reserve_grounding
        distributable = self._total_budget * (1.0 - reserved)

        total_priority = sum(priorities.get(r, 1.0) for r in regions) or 1.0

        by_region: dict[str, float] = {}
        for r in regions:
            share = priorities.get(r, 1.0) / total_priority
            by_region[r] = distributable * share

        self._allocation = BudgetAllocation(
            total=self._total_budget,
            by_region=by_region,
            by_category={},
            reserved_for_audit=self._total_budget * reserve_audit,
            reserved_for_grounding=self._total_budget * reserve_grounding,
        )
        return self._allocation

    def allocate_by_category(
        self, categories: list[str], weights: dict[str, float]
    ) -> dict[str, float]:
        """Distribute remaining budget across move categories."""
        remaining = self._total_budget - self._total_spent
        total_w = sum(weights.get(c, 1.0) for c in categories) or 1.0
        result: dict[str, float] = {}
        for c in categories:
            share = weights.get(c, 1.0) / total_w
            result[c] = remaining * share
        if self._allocation is not None:
            self._allocation.by_category = dict(result)
        return result

    # ------------------------------------------------------------------
    # Spending
    # ------------------------------------------------------------------

    def spend(self, region: str, category: str, amount: float) -> bool:
        """Record spending. Returns False if budget exceeded."""
        if self._total_spent + amount > self._total_budget:
            return False
        self._spent_by_region[region] += amount
        self._spent_by_category[category] += amount
        self._total_spent += amount
        self._spend_timestamps.append(time.time())
        return True

    def remaining(self, region: str | None = None) -> float:
        """Remaining budget overall or for a specific region."""
        if region is None:
            return self._total_budget - self._total_spent
        if self._allocation is None:
            return 0.0
        allocated = self._allocation.by_region.get(region, 0.0)
        spent = self._spent_by_region.get(region, 0.0)
        return max(0.0, allocated - spent)

    # ------------------------------------------------------------------
    # Usage snapshot
    # ------------------------------------------------------------------

    def usage(self) -> BudgetUsage:
        """Current budget usage snapshot."""
        return BudgetUsage(
            spent=self._total_spent,
            remaining=self._total_budget - self._total_spent,
            by_region=dict(self._spent_by_region),
            by_category=dict(self._spent_by_category),
            budget_exhaustion_eta=self.eta_to_exhaustion(),
        )

    # ------------------------------------------------------------------
    # Rebalancing
    # ------------------------------------------------------------------

    def rebalance(self, obligations: ObligationPresheaf) -> BudgetAllocation:
        """Rebalance budget allocation using obligation pressure."""
        if self._allocation is None:
            regions = list(obligations.by_coordinate.keys())[:10]
            priorities = {r: 1.0 for r in regions}
            return self.allocate(regions, priorities)

        regions = list(self._allocation.by_region.keys())
        if not regions:
            return self._allocation

        priorities: dict[str, float] = {}
        for r in regions:
            priorities[r] = self._priority_from_obligations(obligations, r)

        return self.allocate(regions, priorities)

    def _priority_from_obligations(
        self, obligations: ObligationPresheaf, region: str
    ) -> float:
        """Derive priority for a region from its obligation pressure."""
        # Sum pressure of all obligation kinds that mention coordinates
        # in this region (approximated by checking by_coordinate)
        region_obs = obligations.by_coordinate.get(region, [])
        if not region_obs:
            return 1.0
        # Each pending obligation id in the region contributes
        total = 0.0
        for ob_id in region_obs:
            ob_dict = obligations.obligations.get(ob_id, {})
            if ob_dict.get("status", "PENDING") == "PENDING":
                total += ob_dict.get("priority", 1.0)
        return max(1.0, total)

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    def is_exhausted(self, region: str | None = None) -> bool:
        """True if budget is exhausted."""
        return self.remaining(region) <= 0.0

    def extend_budget(self, additional: float) -> None:
        """Add more budget."""
        self._total_budget += additional
        if self._allocation is not None:
            self._allocation.total = self._total_budget

    def eta_to_exhaustion(self) -> Optional[float]:
        """Estimated seconds until budget is exhausted.

        Computed from recent spend rate.  Returns None if no spend history.
        """
        if len(self._spend_timestamps) < 2:
            return None
        elapsed = self._spend_timestamps[-1] - self._spend_timestamps[0]
        if elapsed <= 0:
            return None
        rate = self._total_spent / elapsed  # units per second
        if rate <= 0:
            return None
        remaining = self._total_budget - self._total_spent
        return remaining / rate
