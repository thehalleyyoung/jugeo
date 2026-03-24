"""Stage 6 – Marginal-value ranking and equimarginal budget allocation.

Standalone module (no jugeo imports, Python stdlib only).

The core idea mirrors theorem economics from the JuGeo framework:
    marginal_value = value_delivered / (cost + ε)

*Value* is a weighted combination of three dimensions:
    1.  **User hours saved** – direct time savings per user per month.
    2.  **Error reduction** – fraction of human errors eliminated.
    3.  **Access democratisation** – making expensive expertise cheap.

A compounding factor rewards ideas that accumulate value over time
(network effects, durable artifacts, improving models).

The :class:`EquimarginalAllocator` distributes a fixed dev-hour budget
across ranked ideas so that the marginal return of the *last* hour
spent is equal across all funded projects – the classic equimarginal
principle from micro-economics.
"""

from __future__ import annotations

import math
from typing import Any

from .models import (
    ApplicationCoordinate,
    AppIdeationPurpose,
    GainProfile,
    IdeaProposal,
    RankedIdea,
    ValidationResult,
    ValidationStatus,
)

# Shorthand used throughout the module.
AC = ApplicationCoordinate

# ---------------------------------------------------------------------------
# Module-level lookup tables (all 22 coordinates)
# ---------------------------------------------------------------------------

# User hours saved per month estimates per coordinate.
COORD_HOURS_SAVED: dict[ApplicationCoordinate, float] = {
    AC.SCHEDULING: 3.0,
    AC.FORM_WORKFLOW: 2.5,
    AC.DATA_TRANSFORMATION: 4.0,
    AC.COMPUTATION_ON_DEMAND: 5.0,
    AC.BATCH_PROCESSING: 6.0,
    AC.AUDIT_TRAIL: 1.5,
    AC.INVENTORY: 2.0,
    AC.DATA_VISUALIZATION: 1.5,
    AC.INTERACTIVE_DASHBOARD: 2.0,
    AC.MATCHING: 3.5,
    AC.CONSTRAINT_SATISFACTION: 5.0,
    AC.SIMULATION: 4.0,
    AC.COMPARISON: 2.0,
    AC.AGGREGATION: 2.5,
    AC.DATA_INGESTION: 1.5,
    AC.DATA_EXPORT: 1.0,
    AC.FILE_PROCESSING: 3.0,
    AC.REAL_TIME_FEEDBACK: 1.0,
    AC.COLLABORATIVE_EDITING: 2.0,
    AC.NOTIFICATION: 1.5,
    AC.STATIC_REPORT: 2.0,
    AC.API_PROVISION: 3.0,
}

# Error reduction factors per coordinate (fraction of errors eliminated).
# Every coordinate has an entry; values reflect how strongly the coordinate
# prevents human mistakes in typical workflows.
COORD_ERROR_REDUCTION: dict[ApplicationCoordinate, float] = {
    AC.FORM_WORKFLOW: 0.60,
    AC.CONSTRAINT_SATISFACTION: 0.80,
    AC.AUDIT_TRAIL: 0.40,
    AC.COMPUTATION_ON_DEMAND: 0.70,
    AC.DATA_TRANSFORMATION: 0.55,
    AC.BATCH_PROCESSING: 0.50,
    AC.DATA_INGESTION: 0.35,
    AC.DATA_EXPORT: 0.25,
    AC.DATA_VISUALIZATION: 0.30,
    AC.INTERACTIVE_DASHBOARD: 0.25,
    AC.SCHEDULING: 0.45,
    AC.INVENTORY: 0.50,
    AC.MATCHING: 0.55,
    AC.SIMULATION: 0.40,
    AC.COMPARISON: 0.35,
    AC.AGGREGATION: 0.30,
    AC.FILE_PROCESSING: 0.35,
    AC.REAL_TIME_FEEDBACK: 0.20,
    AC.COLLABORATIVE_EDITING: 0.25,
    AC.NOTIFICATION: 0.15,
    AC.STATIC_REPORT: 0.20,
    AC.API_PROVISION: 0.10,
}

# Democratisation score per coordinate.
# High values mean the coordinate replaces expensive software or scarce
# human expertise; low values mean it is a basic utility.
COORD_DEMOCRATIZATION: dict[ApplicationCoordinate, float] = {
    AC.COMPUTATION_ON_DEMAND: 0.80,
    AC.CONSTRAINT_SATISFACTION: 0.90,
    AC.SIMULATION: 0.85,
    AC.DATA_VISUALIZATION: 0.70,
    AC.MATCHING: 0.75,
    AC.INTERACTIVE_DASHBOARD: 0.65,
    AC.DATA_TRANSFORMATION: 0.60,
    AC.BATCH_PROCESSING: 0.55,
    AC.SCHEDULING: 0.55,
    AC.INVENTORY: 0.50,
    AC.AGGREGATION: 0.50,
    AC.COMPARISON: 0.45,
    AC.FORM_WORKFLOW: 0.45,
    AC.FILE_PROCESSING: 0.40,
    AC.AUDIT_TRAIL: 0.40,
    AC.STATIC_REPORT: 0.40,
    AC.COLLABORATIVE_EDITING: 0.50,
    AC.REAL_TIME_FEEDBACK: 0.35,
    AC.DATA_INGESTION: 0.35,
    AC.DATA_EXPORT: 0.30,
    AC.NOTIFICATION: 0.30,
    AC.API_PROVISION: 0.20,
}

# Compounding multipliers – how much an idea's value grows over time.
# Only coordinates with notable compounding effects are listed; the
# default for unlisted coordinates is 1.0 (no compounding).
_COORD_COMPOUNDING: dict[ApplicationCoordinate, float] = {
    AC.AUDIT_TRAIL: 1.30,
    AC.COLLABORATIVE_EDITING: 1.30,
    AC.SIMULATION: 1.25,
    AC.INVENTORY: 1.20,
    AC.SCHEDULING: 1.15,
    AC.INTERACTIVE_DASHBOARD: 1.15,
    AC.DATA_VISUALIZATION: 1.10,
    AC.MATCHING: 1.10,
    AC.AGGREGATION: 1.05,
    AC.BATCH_PROCESSING: 1.05,
}

# Coordinates whose implementation complexity is notably higher than
# average and therefore deserve a cost penalty.
_COMPLEX_COORDINATES: frozenset[ApplicationCoordinate] = frozenset({
    AC.COLLABORATIVE_EDITING,
    AC.REAL_TIME_FEEDBACK,
    AC.SIMULATION,
    AC.CONSTRAINT_SATISFACTION,
})

# ── Tuning constants ──────────────────────────────────────────────────────

_SUB_LINEAR_DECAY = 0.70          # each extra coordinate adds 70 % value
_NOVELTY_CAP = 1.50               # max novelty premium multiplier
_NOVELTY_WEIGHT = 0.50            # how strongly novelty boosts value
_BENEFIT_MAX = 10.0               # upper clamp for raw benefit score
_COST_SCALE = 100.0               # normaliser for dev-hour denominator
_COST_EPSILON = 0.10              # avoids division by zero in MV formula
_BASE_HOURS_PER_COORD = 10.0      # fallback hours per coordinate
_COMPLEX_PENALTY = 1.50           # multiplier for complex coordinates
_DEFAULT_FEASIBILITY = 0.50       # when feasibility is unknown
_MIN_CONFIDENCE = 0.05            # floor for validation confidence
_MAX_USEFUL_HOURS_FACTOR = 3.0    # cap for equimarginal allocation
_EQUALIZE_ITERATIONS = 60         # binary-search iterations
_EQUALIZE_TOLERANCE = 0.5         # budget residual tolerance (hours)


# ═══════════════════════════════════════════════════════════════════════════
# AppMarginalAnalyzer
# ═══════════════════════════════════════════════════════════════════════════


class AppMarginalAnalyzer:
    """Stage 6: rank validated ideas by marginal value.

    Analogous to theorem economics in the JuGeo proof framework:
        marginal value = value_delivered / (cost + ε)

    Value is the weighted sum of user-hours saved, error reduction, and
    access democratisation, scaled by a compounding factor.  Cost is
    estimated development hours.
    """

    # ── public API ─────────────────────────────────────────────────────────

    def rank(
        self,
        validated: list[tuple[IdeaProposal, ValidationResult]],
        purpose: AppIdeationPurpose,
    ) -> list[RankedIdea]:
        """Rank all validated ideas by marginal value.

        Ideas whose validation status is ``ALREADY_EXISTS`` or ``INFEASIBLE``
        are silently dropped.  The remaining ideas are scored, receive a
        novelty premium, and are returned sorted best-first.
        """
        ranked: list[RankedIdea] = []

        for idea, vr in validated:
            if vr.status in (ValidationStatus.ALREADY_EXISTS,
                             ValidationStatus.INFEASIBLE):
                continue

            mv = self._marginal_value(idea, vr)
            final = self._apply_novelty_premium(mv, idea.novelty_score)

            # Weight by purpose weights (leverage ≈ user_hours,
            # tractability ≈ 1/cost, relevance ≈ error_reduction).
            hours_component = self._user_hours_saved(idea, vr)
            error_component = self._error_reduction(idea)
            access_component = self._access_democratization(idea)
            compound = self._compounding_factor(idea)
            cost = self._dev_hours_estimate(idea)

            purpose_weight = (
                purpose.leverage_weight * hours_component
                + purpose.tractability_weight * (1.0 / (cost / _COST_SCALE + _COST_EPSILON))
                + purpose.relevance_weight * error_component
            )
            # Blend purpose weighting into the final score.
            final = 0.6 * final + 0.4 * _clamp01(purpose_weight)

            components: dict[str, float] = {
                "user_hours_saved": round(hours_component, 4),
                "error_reduction": round(error_component, 4),
                "access_democratization": round(access_component, 4),
                "compounding_factor": round(compound, 4),
                "dev_hours_estimate": round(cost, 2),
                "raw_marginal_value": round(mv, 4),
                "novelty_premium_score": round(
                    self._apply_novelty_premium(mv, idea.novelty_score), 4
                ),
                "purpose_blend": round(purpose_weight, 4),
                "feasibility": round(idea.feasibility_score, 4),
                "confidence": round(vr.confidence, 4),
            }

            ranked.append(
                RankedIdea(
                    idea=idea,
                    marginal_value=round(mv, 6),
                    final_score=round(final, 6),
                    ranking_components=components,
                )
            )

        ranked.sort(key=lambda r: r.final_score, reverse=True)
        return ranked

    # ── benefit dimensions ─────────────────────────────────────────────────

    def _user_hours_saved(
        self, idea: IdeaProposal, validation: ValidationResult
    ) -> float:
        """Estimate hours/month saved per user.

        Sum ``COORD_HOURS_SAVED`` for each coordinate with sub-linear
        scaling: the *i*-th coordinate (sorted by descending value) adds
        ``value_i × 0.70^(i-1)``.  The total is multiplied by validation
        confidence so that uncertain ideas are discounted.
        """
        per_coord = sorted(
            (COORD_HOURS_SAVED.get(c, 1.0) for c in idea.coordinates),
            reverse=True,
        )
        total = 0.0
        for idx, val in enumerate(per_coord):
            total += val * (_SUB_LINEAR_DECAY ** idx)

        confidence = max(validation.confidence, _MIN_CONFIDENCE)
        return total * confidence

    def _error_reduction(self, idea: IdeaProposal) -> float:
        """Estimate fraction of errors reduced.

        Error reduction is dominated by the single strongest coordinate
        (not additive), so we take the maximum.
        """
        if not idea.coordinates:
            return 0.0
        reductions = [COORD_ERROR_REDUCTION.get(c, 0.05) for c in idea.coordinates]
        return max(reductions)

    def _access_democratization(self, idea: IdeaProposal) -> float:
        """Mean democratisation score across the idea's coordinates.

        Measures the degree to which the idea replaces expensive expert
        knowledge or costly proprietary software with an accessible tool.
        """
        if not idea.coordinates:
            return 0.0
        scores = [COORD_DEMOCRATIZATION.get(c, 0.3) for c in idea.coordinates]
        return sum(scores) / len(scores)

    def _compounding_factor(self, idea: IdeaProposal) -> float:
        """Return the compounding multiplier for *idea* (≥ 1.0).

        Uses the maximum compounding factor across the idea's coordinates.
        Ideas whose coordinates have no special compounding effect get 1.0.
        """
        if not idea.coordinates:
            return 1.0
        factors = [_COORD_COMPOUNDING.get(c, 1.0) for c in idea.coordinates]
        return max(factors)

    # ── cost estimation ────────────────────────────────────────────────────

    def _dev_hours_estimate(self, idea: IdeaProposal) -> float:
        """Estimate development hours.

        If the idea's :pyattr:`gain.cost` is positive it is used directly.
        Otherwise we fall back to ``n_coords × BASE_HOURS_PER_COORD`` with
        a 50 % penalty for each complex coordinate present.
        """
        if idea.gain.cost > 0:
            return float(idea.gain.cost)

        n_coords = max(len(idea.coordinates), 1)
        base = n_coords * _BASE_HOURS_PER_COORD
        complex_count = sum(
            1 for c in idea.coordinates if c in _COMPLEX_COORDINATES
        )
        penalty = 1.0 + (complex_count * (_COMPLEX_PENALTY - 1.0))
        return base * penalty

    # ── marginal value computation ─────────────────────────────────────────

    def _marginal_value(
        self, idea: IdeaProposal, validation: ValidationResult
    ) -> float:
        """Compute marginal value = benefits / cost.

        benefits = (hours × 0.4  +  error × 0.3  +  access × 0.3) × compound
        marginal  = benefits / (dev_hours / COST_SCALE + ε)

        The result is clamped to [0, 1].
        """
        hours = self._user_hours_saved(idea, validation)
        error = self._error_reduction(idea)
        access = self._access_democratization(idea)
        compound = self._compounding_factor(idea)

        # Normalise hours to roughly [0, 1] by dividing by a generous
        # upper bound (a 5-coordinate idea at full confidence can reach
        # ~15 h/month; we use 20 as the ceiling).
        norm_hours = min(hours / 20.0, 1.0)

        benefits = (
            norm_hours * 0.4
            + error * 0.3
            + access * 0.3
        ) * compound

        cost = self._dev_hours_estimate(idea)
        raw = benefits / (cost / _COST_SCALE + _COST_EPSILON)

        # Scale by feasibility so that hard-to-build ideas are discounted.
        raw *= max(idea.feasibility_score, _DEFAULT_FEASIBILITY)

        return _clamp01(raw)

    def _apply_novelty_premium(
        self, marginal_value: float, novelty_score: float
    ) -> float:
        """Boost marginal value for novel ideas (less competition).

        premium = mv × (1 + NOVELTY_WEIGHT × novelty_score)
        Capped at NOVELTY_CAP × original value.
        """
        premium = marginal_value * (1.0 + _NOVELTY_WEIGHT * novelty_score)
        cap = marginal_value * _NOVELTY_CAP
        return min(premium, cap)


# ═══════════════════════════════════════════════════════════════════════════
# EquimarginalAllocator
# ═══════════════════════════════════════════════════════════════════════════


class EquimarginalAllocator:
    """Allocate a fixed dev-hour budget across ranked ideas so that the
    marginal return of the last hour spent is equalised.

    Uses the equimarginal principle: at the optimum the derivative of the
    return curve is identical for every funded project.  We model each
    project's total return as a diminishing-returns curve::

        R(h) = max_return × (1 − exp(−k × h))

    with marginal return (derivative)::

        MR(h) = max_return × k × exp(−k × h)

    A binary search on the target marginal-return rate *R** finds the
    allocation where the sum of implied hours equals the budget.
    """

    # ── public API ─────────────────────────────────────────────────────────

    def allocate(
        self,
        ranked: list[RankedIdea],
        total_budget_hours: float,
    ) -> list[tuple[RankedIdea, float]]:
        """Return ``[(idea, allocated_hours)]`` sorted by hours descending.

        Ideas with zero marginal value or zero cost receive no allocation.
        If the budget exceeds what all ideas can usefully absorb, the
        surplus is reported as allocation to a sentinel ``None`` entry
        (which callers should interpret as unallocated slack).
        """
        if not ranked or total_budget_hours <= 0:
            return []

        # Filter to ideas that can actually absorb hours.
        usable = [r for r in ranked if r.marginal_value > 0 and
                  self._max_return(r) > 0]
        if not usable:
            return []

        allocation_map = self._equalize(usable, total_budget_hours)

        results: list[tuple[RankedIdea, float]] = []
        for r in usable:
            hours = allocation_map.get(r.idea.id, 0.0)
            if hours > 0:
                results.append((r, round(hours, 2)))

        results.sort(key=lambda pair: pair[1], reverse=True)
        return results

    # ── diminishing-returns model ──────────────────────────────────────────

    @staticmethod
    def _max_return(idea: RankedIdea) -> float:
        """Upper asymptote of the return curve for *idea*."""
        return idea.marginal_value * _COST_SCALE

    @staticmethod
    def _decay_rate(idea: RankedIdea) -> float:
        """Decay rate *k* governing how quickly returns diminish."""
        cost = idea.idea.gain.cost
        if cost > 0:
            return 1.0 / cost
        return 0.1

    def _marginal_return_curve(self, idea: RankedIdea, hours: float) -> float:
        """Marginal return at *hours* of investment.

        MR(h) = max_return × k × exp(−k × h)
        """
        mr = self._max_return(idea)
        k = self._decay_rate(idea)
        return mr * k * math.exp(-k * hours)

    def _total_return_curve(self, idea: RankedIdea, hours: float) -> float:
        """Total return accumulated after *hours* of investment.

        R(h) = max_return × (1 − exp(−k × h))
        """
        mr = self._max_return(idea)
        k = self._decay_rate(idea)
        return mr * (1.0 - math.exp(-k * hours))

    def _hours_for_return_rate(
        self, idea: RankedIdea, target_return: float
    ) -> float:
        """Inverse of :meth:`_marginal_return_curve`.

        Solve  max_return × k × exp(−k × h) = target_return  for *h*::

            h = −ln(target_return / (max_return × k)) / k

        Clamped to ``[0, max_useful_hours]``.
        """
        mr = self._max_return(idea)
        k = self._decay_rate(idea)
        peak = mr * k  # marginal return at h = 0
        if target_return <= 0 or peak <= 0:
            return self._max_useful_hours(idea)
        if target_return >= peak:
            return 0.0

        ratio = target_return / peak
        # Guard against log(0) – ratio is guaranteed > 0 and < 1 here.
        hours = -math.log(ratio) / k
        return _clamp(hours, 0.0, self._max_useful_hours(idea))

    @staticmethod
    def _max_useful_hours(idea: RankedIdea) -> float:
        """Cap on hours beyond which additional investment is wasteful."""
        cost = idea.idea.gain.cost
        if cost > 0:
            return cost * _MAX_USEFUL_HOURS_FACTOR
        n_coords = max(len(idea.idea.coordinates), 1)
        return n_coords * _BASE_HOURS_PER_COORD * _MAX_USEFUL_HOURS_FACTOR

    # ── binary search for equimarginal rate ────────────────────────────────

    def _equalize(
        self,
        ranked: list[RankedIdea],
        total_budget: float,
    ) -> dict[str, float]:
        """Binary-search for the equimarginal return rate *R**.

        Returns ``{idea_id: allocated_hours}`` such that the sum of hours
        is approximately ``total_budget`` and every funded idea has the
        same marginal return at its allocation point.
        """
        # Determine the search bounds for R*.
        # R* cannot exceed the highest peak marginal return (at h = 0).
        upper_rate = max(
            self._max_return(r) * self._decay_rate(r) for r in ranked
        )
        lower_rate = 1e-12

        # Edge case: even at the lowest return rate the budget cannot be
        # fully absorbed.
        total_at_lowest = sum(
            self._hours_for_return_rate(r, lower_rate) for r in ranked
        )
        if total_at_lowest <= total_budget:
            # Allocate every idea up to its max useful hours; budget has
            # slack.
            return {
                r.idea.id: self._hours_for_return_rate(r, lower_rate)
                for r in ranked
            }

        # Binary search.
        for _ in range(_EQUALIZE_ITERATIONS):
            mid_rate = (lower_rate + upper_rate) / 2.0
            total_hours = sum(
                self._hours_for_return_rate(r, mid_rate) for r in ranked
            )
            if abs(total_hours - total_budget) < _EQUALIZE_TOLERANCE:
                break
            if total_hours > total_budget:
                # We're allocating too many hours → raise the threshold
                # so each idea gets fewer hours.
                lower_rate = mid_rate
            else:
                upper_rate = mid_rate

        # Build the final allocation at the converged rate.
        mid_rate = (lower_rate + upper_rate) / 2.0
        allocation: dict[str, float] = {}
        for r in ranked:
            h = self._hours_for_return_rate(r, mid_rate)
            if h > 0:
                allocation[r.idea.id] = h
        return allocation

    # ── portfolio-level summaries ──────────────────────────────────────────

    def expected_total_return(
        self, allocation: list[tuple[RankedIdea, float]]
    ) -> float:
        """Sum of total-return curves evaluated at allocated hours."""
        return sum(
            self._total_return_curve(idea, hours)
            for idea, hours in allocation
        )

    def utilisation_ratio(
        self, allocation: list[tuple[RankedIdea, float]],
        total_budget: float,
    ) -> float:
        """Fraction of the budget that is actually allocated."""
        if total_budget <= 0:
            return 0.0
        used = sum(h for _, h in allocation)
        return min(used / total_budget, 1.0)

    def marginal_return_at_allocation(
        self, allocation: list[tuple[RankedIdea, float]]
    ) -> dict[str, float]:
        """Return the marginal return at each idea's allocation point.

        In a perfect equimarginal solution every value should be (nearly)
        identical.
        """
        return {
            idea.idea.id: round(self._marginal_return_curve(idea, hours), 6)
            for idea, hours in allocation
        }


# ═══════════════════════════════════════════════════════════════════════════
# Convenience helpers
# ═══════════════════════════════════════════════════════════════════════════


def rank_and_allocate(
    validated: list[tuple[IdeaProposal, ValidationResult]],
    purpose: AppIdeationPurpose,
    budget_hours: float = 200.0,
) -> dict[str, Any]:
    """One-shot convenience: rank ideas and allocate a budget.

    Returns a dict with keys:
        ``ranked``   – list of :class:`RankedIdea`
        ``allocation`` – list of (RankedIdea, hours) tuples
        ``total_return`` – expected total return
        ``utilisation``  – fraction of budget used
        ``marginal_rates`` – per-idea marginal return at allocation point
    """
    analyzer = AppMarginalAnalyzer()
    ranked = analyzer.rank(validated, purpose)

    allocator = EquimarginalAllocator()
    allocation = allocator.allocate(ranked, budget_hours)

    return {
        "ranked": ranked,
        "allocation": allocation,
        "total_return": allocator.expected_total_return(allocation),
        "utilisation": allocator.utilisation_ratio(allocation, budget_hours),
        "marginal_rates": allocator.marginal_return_at_allocation(allocation),
    }


def summarise_ranking(ranked: list[RankedIdea], top_n: int = 10) -> str:
    """Return a human-readable summary of the top *top_n* ranked ideas."""
    lines: list[str] = []
    for i, r in enumerate(ranked[:top_n], 1):
        coords = ", ".join(sorted(c.value for c in r.idea.coordinates))
        lines.append(
            f"{i:>3}. [{r.final_score:.3f}] {r.idea.title}\n"
            f"     MV={r.marginal_value:.3f}  "
            f"feasibility={r.idea.feasibility_score:.2f}  "
            f"novelty={r.idea.novelty_score:.2f}\n"
            f"     coords: {coords}"
        )
    header = f"Top {min(top_n, len(ranked))} ideas by marginal value"
    separator = "─" * len(header)
    return f"{header}\n{separator}\n" + "\n".join(lines)


def summarise_allocation(
    allocation: list[tuple[RankedIdea, float]],
    total_budget: float,
) -> str:
    """Return a human-readable summary of the budget allocation."""
    lines: list[str] = []
    used = 0.0
    for idea, hours in allocation:
        pct = (hours / total_budget * 100) if total_budget > 0 else 0.0
        lines.append(
            f"  {idea.idea.title:<40s}  {hours:>7.1f}h  ({pct:>5.1f}%)"
        )
        used += hours
    slack = total_budget - used
    header = f"Budget allocation  (total={total_budget:.0f}h)"
    separator = "─" * len(header)
    footer = f"  {'Unallocated':<40s}  {slack:>7.1f}h  ({slack/total_budget*100:>5.1f}%)" if slack > 0.5 else ""
    body = "\n".join(lines)
    if footer:
        body = body + "\n" + footer
    return f"{header}\n{separator}\n{body}"


# ── numeric utilities ──────────────────────────────────────────────────────


def _clamp01(value: float) -> float:
    """Clamp *value* to the interval [0, 1]."""
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp *value* to [lo, hi]."""
    if value < lo:
        return lo
    if value > hi:
        return hi
    return value
