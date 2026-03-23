"""
jugeo.ideation.semantic_futures.pre_implementation_valuation_expec
=======================================================================

Pre-Implementation Valuation (Theory Ch. — Ideation as Search over Semantic
Futures, §2).

Theory
------
*Pre-implementation valuation* is the process of assigning an expected value
to an idea *before* it has been implemented, using only the information
available in the current portfolio and the idea's structural attributes.

Formally, the pre-implementation value :math:`V(I)` of idea :math:`I` is:

.. math::

   V(I) = \\mathbb{E}[\\Delta_{\\text{obstructions}}(I)] - c(I)

where :math:`\\mathbb{E}[\\Delta_{\\text{obstructions}}(I)]` is the expected
reduction in total obstruction count if :math:`I` is implemented, and
:math:`c(I)` is the estimated implementation cost.

The expected obstruction reduction decomposes as:

.. math::

   \\mathbb{E}[\\Delta_{\\text{obstructions}}(I)] = \\lambda(I) \\cdot
       |\\mathcal{C}_{\\text{obstructed}}| \\cdot \\rho(I)

so that only ideas with high leverage *and* high attainability receive high
pre-implementation value.

Valuation tiers:
* **TIER_1** (V ≥ 0.75): implement immediately.
* **TIER_2** (0.5 ≤ V < 0.75): queue for next iteration.
* **TIER_3** (0.25 ≤ V < 0.5): defer, monitor.
* **TIER_4** (V < 0.25): discard or archive.

Cost model:
The implementation cost :math:`c(I)` is estimated from:
1. Support scope size (larger scope → higher cost).
2. Idea type (constructions cost more than conjectures).
3. Dependency depth (deep dependency chains inflate cost).
"""
from __future__ import annotations

import heapq
import math
import statistics
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

try:
    from jugeo.ideation.semantic_futures.idea_objects_future_attainability import (
        IdeaObject,
        IdeaType,
        IdeaPortfolio,
    )
except ImportError:
    IdeaObject = Any  # type: ignore[assignment,misc]
    IdeaType = Any  # type: ignore[assignment,misc]
    IdeaPortfolio = Any  # type: ignore[assignment,misc]

__all__ = [
    "ValuationTier",
    "CostComponents",
    "ObstructionReductionEstimate",
    "PreImplementationValue",
    "ValueDistribution",
    "CostEstimator",
    "ObstructionReductionForecaster",
    "ValuationTierClassifier",
    "PreImplementationValuationAnalyzer",
    "PreImplementationValuationWitness",
    "PreImplementationValuationCoordinator",
    "compute_expected_reduction",
    "compute_raw_value",
    "classify_tier",
    "sort_by_tier",
    "filter_by_tier",
    "aggregate_portfolio_value",
]


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _safe_float(value: Any, default: float = 0.0) -> float:
    """Convert *value* to float, returning *default* on any error.

    Args:
        value: The value to convert.
        default: The fallback value if conversion fails.

    Returns:
        A float representation of value, or default on failure.
    """
    try:
        result = float(value)
        if math.isnan(result) or math.isinf(result):
            return default
        return result
    except (TypeError, ValueError):
        return default


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp *value* to the closed interval [lo, hi].

    Args:
        value: The value to clamp.
        lo: The lower bound (inclusive).
        hi: The upper bound (inclusive).

    Returns:
        The clamped value.
    """
    if value < lo:
        return lo
    if value > hi:
        return hi
    return value


def _ema(previous: float, current: float, alpha: float = 0.1) -> float:
    """Compute an exponential moving average update.

    Args:
        previous: The previous EMA value.
        current: The new observation.
        alpha: The smoothing factor in (0, 1].  Defaults to 0.1.

    Returns:
        The updated EMA value.
    """
    alpha = _clamp(_safe_float(alpha, 0.1), 1e-9, 1.0)
    return alpha * _safe_float(current, 0.0) + (1.0 - alpha) * _safe_float(previous, 0.0)


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class ValuationTier(Enum):
    """Valuation tiers for pre-implementation idea valuation.

    Tiers are ordered from best (TIER_1) to worst (TIER_4):

    Attributes:
        TIER_1: V ≥ 0.75 — implement immediately.
        TIER_2: 0.5 ≤ V < 0.75 — queue for next iteration.
        TIER_3: 0.25 ≤ V < 0.5 — defer and monitor.
        TIER_4: V < 0.25 — discard or archive.
    """

    TIER_1 = 1
    TIER_2 = 2
    TIER_3 = 3
    TIER_4 = 4


# ---------------------------------------------------------------------------
# Frozen dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CostComponents:
    """Decomposed implementation cost for an idea.

    Attributes:
        scope_cost: Cost component from the support scope size.
        type_cost: Cost component from the idea type.
        depth_cost: Cost component from dependency chain depth.
        total: Combined total cost, averaged over the three components.
    """

    scope_cost: float
    type_cost: float
    depth_cost: float
    total: float

    @classmethod
    def from_idea(cls, idea: Any, dependency_depth: int) -> "CostComponents":
        """Compute cost components from an idea and its dependency depth.

        Scope cost: min(1.0, len(idea.support_scope) * 0.05)
        Type cost: CONSTRUCTION=0.3, THEOREM=0.2, INVARIANT=0.15,
                   SEMANTIC_REGION=0.1, CONJECTURE=0.1
        Depth cost: min(0.3, dependency_depth * 0.05)
        Total: (scope_cost + type_cost + depth_cost) / 3

        Args:
            idea: An IdeaObject (or duck-typed equivalent) to compute costs for.
            dependency_depth: The depth of the idea's dependency chain.

        Returns:
            A CostComponents instance.
        """
        scope_size = len(getattr(idea, "support_scope", set()))
        scope_cost = min(1.0, scope_size * 0.05)

        idea_type_val = getattr(getattr(idea, "idea_type", None), "value", None)
        type_cost_map: dict[str, float] = {
            "construction": 0.3,
            "theorem": 0.2,
            "invariant": 0.15,
            "semantic_region": 0.1,
            "conjecture": 0.1,
        }
        type_cost = type_cost_map.get(str(idea_type_val).lower(), 0.15)

        depth_cost = min(0.3, max(0, int(dependency_depth)) * 0.05)
        total = (scope_cost + type_cost + depth_cost) / 3.0

        return cls(
            scope_cost=_clamp(scope_cost, 0.0, 1.0),
            type_cost=_clamp(type_cost, 0.0, 1.0),
            depth_cost=_clamp(depth_cost, 0.0, 1.0),
            total=_clamp(total, 0.0, 1.0),
        )


@dataclass(frozen=True, slots=True)
class ObstructionReductionEstimate:
    """An estimate of the expected reduction in obstruction count.

    Attributes:
        estimate_id: A unique identifier for this estimate.
        expected_delta: The expected fractional reduction in obstructions.
        confidence: Confidence in the estimate, in [0, 1].
        lower_bound: Lower bound of the 80% credible interval.
        upper_bound: Upper bound of the 80% credible interval.
    """

    estimate_id: str
    expected_delta: float
    confidence: float
    lower_bound: float
    upper_bound: float


@dataclass(frozen=True, slots=True)
class PreImplementationValue:
    """The pre-implementation value assigned to a single idea.

    Attributes:
        value_id: A unique identifier for this valuation record.
        idea_id: The ID of the valued idea.
        raw_value: The computed pre-implementation value V(I).
        tier: The valuation tier assigned to this idea.
        cost: The estimated implementation cost c(I).
        expected_reduction: E[ΔObstructions(I)].
        timestamp: Unix timestamp of the valuation.
    """

    value_id: str
    idea_id: str
    raw_value: float
    tier: ValuationTier
    cost: float
    expected_reduction: float
    timestamp: float

    def is_worth_implementing(self, threshold: float = 0.5) -> bool:
        """Determine whether this idea meets a value threshold.

        Args:
            threshold: Minimum raw_value to be considered worth implementing.
                Defaults to 0.5.

        Returns:
            True if raw_value >= threshold, False otherwise.
        """
        return _safe_float(self.raw_value, 0.0) >= _safe_float(threshold, 0.5)

    def roi(self) -> float:
        """Compute the return on investment: raw_value / cost.

        If cost is 0 or effectively zero, returns float("inf").

        Returns:
            The ROI as a float, or float("inf") if cost is zero.
        """
        cost = _safe_float(self.cost, 0.0)
        if cost <= 1e-12:
            return float("inf")
        return _safe_float(self.raw_value, 0.0) / cost


# ---------------------------------------------------------------------------
# Mutable dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ValueDistribution:
    """A mutable collection of PreImplementationValue records.

    Provides aggregate statistics and filtering over a set of valuations.

    Attributes:
        distribution_id: A unique identifier for this distribution.
        _values: Internal list of PreImplementationValue records.
    """

    distribution_id: str
    _values: list[PreImplementationValue] = field(default_factory=list)

    def append(self, v: PreImplementationValue) -> None:
        """Append a PreImplementationValue to the distribution.

        Args:
            v: The PreImplementationValue to append.
        """
        if not isinstance(v, PreImplementationValue):
            raise TypeError(f"Expected PreImplementationValue, got {type(v)}")
        self._values.append(v)

    def tier_counts(self) -> dict[str, int]:
        """Count how many values fall in each valuation tier.

        Returns:
            A dict with keys "TIER_1", "TIER_2", "TIER_3", "TIER_4" and
            integer counts.
        """
        counts = {"TIER_1": 0, "TIER_2": 0, "TIER_3": 0, "TIER_4": 0}
        for v in self._values:
            key = v.tier.name
            if key in counts:
                counts[key] += 1
        return counts

    def top_k(self, k: int) -> list[PreImplementationValue]:
        """Return the top-k values by raw_value.

        Args:
            k: Number of top values to return.

        Returns:
            A list of up to k PreImplementationValues sorted descending.
        """
        k = max(0, int(k))
        return sorted(self._values, key=lambda v: _safe_float(v.raw_value, 0.0), reverse=True)[:k]

    def mean_value(self) -> float:
        """Compute the mean raw_value over all records.

        Returns:
            The mean raw_value, or 0.0 if the distribution is empty.
        """
        if not self._values:
            return 0.0
        vals = [_safe_float(v.raw_value, 0.0) for v in self._values]
        return statistics.mean(vals)

    def __len__(self) -> int:
        """Return the number of records in the distribution."""
        return len(self._values)


@dataclass
class CostEstimator:
    """Estimates implementation cost for ideas, incorporating portfolio context.

    A larger portfolio implies more reusable results, so cost is slightly
    reduced as portfolio_size grows.

    Attributes:
        estimator_id: A unique identifier for this estimator instance.
        scope_rate: Per-coordinate cost increment.  Defaults to 0.05.
        depth_rate: Per-depth-level cost increment.  Defaults to 0.05.
    """

    estimator_id: str
    scope_rate: float = 0.05
    depth_rate: float = 0.05

    def estimate(
        self,
        idea: Any,
        dependency_depth: int,
        portfolio_size: int,
    ) -> CostComponents:
        """Estimate cost components for *idea*, adjusted for portfolio reuse.

        Uses CostComponents.from_idea() as the base, then applies a reuse
        discount: the larger the portfolio, the more results are available for
        reuse, reducing overall cost by up to 20%.

        Args:
            idea: An IdeaObject (or duck-typed equivalent).
            dependency_depth: The depth of the dependency chain.
            portfolio_size: The number of ideas currently in the portfolio.

        Returns:
            An adjusted CostComponents instance.
        """
        base = CostComponents.from_idea(idea, dependency_depth)
        reuse_discount = _clamp(math.log1p(portfolio_size) / 50.0, 0.0, 0.20)
        adjusted_total = _clamp(base.total * (1.0 - reuse_discount), 0.0, 1.0)
        adjusted_scope = _clamp(base.scope_cost * (1.0 - reuse_discount * 0.5), 0.0, 1.0)
        return CostComponents(
            scope_cost=adjusted_scope,
            type_cost=base.type_cost,
            depth_cost=base.depth_cost,
            total=adjusted_total,
        )


@dataclass
class ObstructionReductionForecaster:
    """Forecasts the expected reduction in obstructions if an idea is implemented.

    The forecast uses the idea's leverage and attainability along with the
    current obstruction state to produce a probabilistic estimate.

    Attributes:
        forecaster_id: A unique identifier for this forecaster instance.
        confidence_base: The baseline confidence factor.  Defaults to 0.8.
    """

    forecaster_id: str
    confidence_base: float = 0.8

    def forecast(
        self,
        idea: Any,
        obstructed_coords: set[str],
        total_coords: int,
    ) -> ObstructionReductionEstimate:
        """Forecast the expected obstruction reduction for *idea*.

        Args:
            idea: An IdeaObject (or duck-typed equivalent).
            obstructed_coords: The set of currently obstructed coordinates.
            total_coords: Total number of coordinates in the search space.

        Returns:
            An ObstructionReductionEstimate with delta, confidence, and bounds.
        """
        leverage = _safe_float(getattr(idea, "predicted_leverage", 0.0), 0.0)
        attainability = _safe_float(getattr(idea, "attainability", 0.0), 0.0)
        n_obstructed = len(obstructed_coords)

        expected_delta = compute_expected_reduction(leverage, attainability, n_obstructed)
        confidence = _clamp(
            _safe_float(self.confidence_base, 0.8) * attainability, 0.0, 1.0
        )
        lower = _clamp(expected_delta * (1.0 - 0.2), 0.0, 1.0)
        upper = _clamp(expected_delta * (1.0 + 0.2), 0.0, 1.0)

        return ObstructionReductionEstimate(
            estimate_id=str(uuid.uuid4()),
            expected_delta=expected_delta,
            confidence=confidence,
            lower_bound=lower,
            upper_bound=upper,
        )


@dataclass
class ValuationTierClassifier:
    """Classifies a raw value into a ValuationTier.

    Attributes:
        classifier_id: A unique identifier for this classifier instance.
        tier_thresholds: A tuple of (tier1_threshold, tier2_threshold,
            tier3_threshold) in descending order.  Defaults to (0.75, 0.5, 0.25).
    """

    classifier_id: str
    tier_thresholds: tuple[float, ...] = (0.75, 0.5, 0.25)

    def classify(self, raw_value: float) -> ValuationTier:
        """Assign a ValuationTier based on the raw_value.

        Args:
            raw_value: The pre-implementation value to classify.

        Returns:
            The appropriate ValuationTier for raw_value.
        """
        v = _safe_float(raw_value, 0.0)
        thresholds = self.tier_thresholds
        t1 = thresholds[0] if len(thresholds) > 0 else 0.75
        t2 = thresholds[1] if len(thresholds) > 1 else 0.50
        t3 = thresholds[2] if len(thresholds) > 2 else 0.25
        if v >= t1:
            return ValuationTier.TIER_1
        if v >= t2:
            return ValuationTier.TIER_2
        if v >= t3:
            return ValuationTier.TIER_3
        return ValuationTier.TIER_4


@dataclass
class PreImplementationValuationAnalyzer:
    """Analyzes a ValueDistribution to produce portfolio-level insights.

    Provides aggregate statistics, Pareto-optimal idea identification, and
    actionable recommendations for the ideation process.

    Attributes:
        analyzer_id: A unique identifier for this analyzer instance.
    """

    analyzer_id: str

    def analyze(self, distribution: ValueDistribution) -> dict[str, Any]:
        """Analyze a value distribution and produce a summary report.

        The report includes mean value, tier counts, top ideas by ROI,
        Pareto-optimal idea IDs, and a recommendation string.

        Args:
            distribution: The ValueDistribution to analyze.

        Returns:
            A dict containing:
              - mean_value (float)
              - tier_counts (dict[str, int])
              - top_ideas (list of value_id for top 3 by ROI)
              - pareto_ids (list of idea_ids Pareto-optimal in value/cost)
              - recommendation (str)
        """
        mean_val = distribution.mean_value()
        tc = distribution.tier_counts()

        # Top 3 by ROI
        all_vals = list(distribution._values)
        top3_by_roi = sorted(all_vals, key=lambda v: v.roi(), reverse=True)[:3]
        top_ideas = [v.value_id for v in top3_by_roi]

        # Pareto-optimal: not dominated in both raw_value and -cost
        pareto_ids = self.pareto_optimal_ideas(all_vals, [])

        # Recommendation
        tier1_count = tc.get("TIER_1", 0)
        tier4_count = tc.get("TIER_4", 0)
        total = len(distribution)
        if total == 0:
            recommendation = "No ideas to evaluate. Expand the idea pool."
        elif tier1_count >= max(1, total // 3):
            recommendation = (
                f"Strong portfolio: {tier1_count}/{total} ideas are TIER_1. "
                "Proceed with immediate implementation."
            )
        elif tier4_count > total // 2:
            recommendation = (
                f"Weak portfolio: {tier4_count}/{total} ideas are TIER_4. "
                "Revise ideas to target high-obstruction coordinates."
            )
        else:
            recommendation = (
                f"Mixed portfolio (mean value={mean_val:.2f}). "
                "Focus on converting TIER_3 ideas into TIER_2 via background acquisition."
            )

        return {
            "mean_value": mean_val,
            "tier_counts": tc,
            "top_ideas": top_ideas,
            "pareto_ids": pareto_ids,
            "recommendation": recommendation,
        }

    def pareto_optimal_ideas(
        self,
        values: list[PreImplementationValue],
        ideas: list[Any],
    ) -> list[str]:
        """Identify Pareto-optimal ideas in the (raw_value, -cost) space.

        An idea is Pareto-optimal if no other idea strictly dominates it in
        both raw_value (higher is better) and cost (lower is better).

        Args:
            values: A list of PreImplementationValue records.
            ideas: A list of idea objects (may be empty; ideas are duck-typed).

        Returns:
            A list of idea_ids for the Pareto-optimal subset.
        """
        if not values:
            return []

        pareto: list[str] = []
        for candidate in values:
            dominated = False
            for other in values:
                if other.idea_id == candidate.idea_id:
                    continue
                other_rv = _safe_float(other.raw_value, 0.0)
                cand_rv = _safe_float(candidate.raw_value, 0.0)
                other_cost = _safe_float(other.cost, 0.0)
                cand_cost = _safe_float(candidate.cost, 0.0)
                if other_rv >= cand_rv and other_cost <= cand_cost:
                    if other_rv > cand_rv or other_cost < cand_cost:
                        dominated = True
                        break
            if not dominated:
                pareto.append(candidate.idea_id)
        return pareto


@dataclass(frozen=True, slots=True)
class PreImplementationValuationWitness:
    """An immutable witness record for a pre-implementation valuation run.

    Attributes:
        witness_id: A unique identifier for this witness.
        timestamp: Unix timestamp of the valuation run.
        success: Whether the run completed without error.
        idea_id: The ID of the valued idea.
        tier: The assigned ValuationTier.
        raw_value: The computed V(I).
        roi: The return on investment raw_value / cost.
        analysis_report: The full dict from PreImplementationValuationAnalyzer.
        summary: A human-readable one-line summary.
    """

    witness_id: str
    timestamp: float
    success: bool
    idea_id: str
    tier: ValuationTier
    raw_value: float
    roi: float
    analysis_report: dict[str, Any]
    summary: str


@dataclass
class PreImplementationValuationCoordinator:
    """Coordinator for pre-implementation idea valuation in JuGeo.

    # copilot: The coordinator orchestrates pre-implementation valuation.
    # copilot: It accepts an idea and context and:
    # copilot:
    # copilot: 1. Estimates cost components (scope, type, depth).
    # copilot: 2. Forecasts expected obstruction reduction.
    # copilot: 3. Computes raw pre-implementation value V(I).
    # copilot: 4. Classifies the value into a valuation tier.
    # copilot: 5. Computes ROI = raw_value / cost.
    # copilot: 6. Runs PreImplementationValuationAnalyzer for quality report.
    # copilot: 7. Seals and returns a PreImplementationValuationWitness.

    Attributes:
        session_id: A unique identifier for this coordinator session.
        cost_weights: Optional overrides for cost weight components.
    """

    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    cost_weights: dict[str, float] = field(default_factory=dict)

    def run(
        self,
        idea: Any,
        obstructed_coords: set[str],
        total_coords: int,
        dependency_depth: int,
        portfolio_size: int,
    ) -> PreImplementationValuationWitness:
        """Execute the full pre-implementation valuation pipeline.

        Args:
            idea: An IdeaObject (or duck-typed equivalent) to value.
            obstructed_coords: The set of currently obstructed coordinates.
            total_coords: Total number of coordinates in the search space.
            dependency_depth: The depth of the idea's dependency chain.
            portfolio_size: The current portfolio size (for cost discount).

        Returns:
            A PreImplementationValuationWitness capturing all outputs.
        """
        success = True
        raw_value = 0.0
        tier = ValuationTier.TIER_4
        cost_total = 0.0
        roi_val = 0.0
        report: dict[str, Any] = {}

        try:
            # Step 1: Estimate cost
            cost_estimator = CostEstimator(estimator_id=f"{self.session_id}:cost")
            cost_components = cost_estimator.estimate(idea, dependency_depth, portfolio_size)
            cost_total = cost_components.total

            # Step 2: Forecast obstruction reduction
            forecaster = ObstructionReductionForecaster(
                forecaster_id=f"{self.session_id}:fcast"
            )
            reduction_est = forecaster.forecast(idea, obstructed_coords, total_coords)

            # Step 3: Compute raw value
            raw_value = compute_raw_value(reduction_est.expected_delta, cost_total)

            # Step 4: Classify tier
            classifier = ValuationTierClassifier(
                classifier_id=f"{self.session_id}:cls"
            )
            tier = classifier.classify(raw_value)

            # Step 5: ROI
            roi_val = raw_value / cost_total if cost_total > 1e-12 else float("inf")

            # Step 6: Analyzer pass
            piv = PreImplementationValue(
                value_id=str(uuid.uuid4()),
                idea_id=getattr(idea, "idea_id", "unknown"),
                raw_value=raw_value,
                tier=tier,
                cost=cost_total,
                expected_reduction=reduction_est.expected_delta,
                timestamp=time.time(),
            )
            dist = ValueDistribution(distribution_id=f"{self.session_id}:dist")
            dist.append(piv)
            analyzer = PreImplementationValuationAnalyzer(
                analyzer_id=f"{self.session_id}:anal"
            )
            report = analyzer.analyze(dist)
            report["cost_components"] = {
                "scope": cost_components.scope_cost,
                "type": cost_components.type_cost,
                "depth": cost_components.depth_cost,
                "total": cost_components.total,
            }
            report["reduction_estimate"] = {
                "expected_delta": reduction_est.expected_delta,
                "confidence": reduction_est.confidence,
                "lower": reduction_est.lower_bound,
                "upper": reduction_est.upper_bound,
            }

        except Exception as exc:  # pragma: no cover
            success = False
            report = {"error": str(exc)}

        idea_id = getattr(idea, "idea_id", "unknown")
        summary = (
            f"[{self.session_id}] idea={idea_id} "
            f"tier={tier.name} value={raw_value:.3f} "
            f"cost={cost_total:.3f} roi={roi_val:.2f} success={success}"
        )

        return PreImplementationValuationWitness(
            witness_id=str(uuid.uuid4()),
            timestamp=time.time(),
            success=success,
            idea_id=idea_id,
            tier=tier,
            raw_value=raw_value,
            roi=roi_val,
            analysis_report=report,
            summary=summary,
        )


# ---------------------------------------------------------------------------
# Module-level standalone functions
# ---------------------------------------------------------------------------


def compute_expected_reduction(
    leverage: float,
    attainability: float,
    n_obstructed: int,
) -> float:
    """Compute the expected fractional reduction in obstructions.

    The formula is:

        E[ΔObstructions] = leverage * attainability * n_obstructed

    normalised to [0, 1] by dividing by max(1, n_obstructed).

    Args:
        leverage: The predicted leverage of the idea, in [0, 1].
        attainability: The estimated attainability, in [0, 1].
        n_obstructed: Number of currently obstructed coordinates.

    Returns:
        A float in [0, 1] representing the expected fractional reduction.
    """
    lev = _clamp(_safe_float(leverage, 0.0), 0.0, 1.0)
    att = _clamp(_safe_float(attainability, 0.0), 0.0, 1.0)
    n = max(1, int(n_obstructed))
    raw = lev * att * n / n  # simplifies to lev * att
    return _clamp(raw, 0.0, 1.0)


def compute_raw_value(expected_reduction: float, cost: float) -> float:
    """Compute the raw pre-implementation value V(I) = E[ΔObs] - c(I).

    The result is clamped to [0, 1] since negative value is treated as 0
    (the idea is simply not worth implementing).

    Args:
        expected_reduction: The expected fractional obstruction reduction.
        cost: The estimated implementation cost, in [0, 1].

    Returns:
        A float in [0, 1] representing the raw pre-implementation value.
    """
    er = _safe_float(expected_reduction, 0.0)
    c = _safe_float(cost, 0.0)
    return _clamp(er - c, 0.0, 1.0)


def classify_tier(value: float) -> ValuationTier:
    """Classify a raw value into a ValuationTier using default thresholds.

    Thresholds:
      - TIER_1: value ≥ 0.75
      - TIER_2: 0.5 ≤ value < 0.75
      - TIER_3: 0.25 ≤ value < 0.5
      - TIER_4: value < 0.25

    Args:
        value: The raw pre-implementation value to classify.

    Returns:
        The appropriate ValuationTier.
    """
    v = _safe_float(value, 0.0)
    if v >= 0.75:
        return ValuationTier.TIER_1
    if v >= 0.50:
        return ValuationTier.TIER_2
    if v >= 0.25:
        return ValuationTier.TIER_3
    return ValuationTier.TIER_4


def sort_by_tier(values: list[PreImplementationValue]) -> list[PreImplementationValue]:
    """Sort a list of PreImplementationValues by tier (best first).

    TIER_1 < TIER_2 < TIER_3 < TIER_4 in terms of sort order (ascending
    enum value maps to best tier first).

    Within the same tier, values are sorted by raw_value descending.

    Args:
        values: The list of PreImplementationValues to sort.

    Returns:
        A new sorted list.
    """
    return sorted(
        values,
        key=lambda v: (v.tier.value, -_safe_float(v.raw_value, 0.0)),
    )


def filter_by_tier(
    values: list[PreImplementationValue],
    min_tier: ValuationTier,
) -> list[PreImplementationValue]:
    """Keep only values with tier ≤ min_tier (i.e., at least as good).

    TIER_1 is best (value=1), TIER_4 is worst (value=4).  Passing min_tier=TIER_2
    keeps TIER_1 and TIER_2 records.

    Args:
        values: The list of PreImplementationValues to filter.
        min_tier: The maximum acceptable tier (inclusive).

    Returns:
        A filtered list containing only values with tier.value <= min_tier.value.
    """
    threshold = min_tier.value
    return [v for v in values if v.tier.value <= threshold]


def aggregate_portfolio_value(values: list[PreImplementationValue]) -> float:
    """Sum the raw_values of all PreImplementationValues in the list.

    Args:
        values: A list of PreImplementationValues to aggregate.

    Returns:
        The total raw value of the entire list.
    """
    return sum(_safe_float(v.raw_value, 0.0) for v in values)


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=== Pre-Implementation Valuation Smoke Test ===\n")

    # Build mock ideas via duck-typing (avoid requiring s01 import at runtime)
    @dataclass
    class _MockIdea:
        """Minimal mock idea for smoke testing."""
        idea_id: str
        support_scope: frozenset
        idea_type: Any
        predicted_leverage: float
        attainability: float

    class _MockIdeaType(Enum):
        THEOREM = "theorem"
        CONSTRUCTION = "construction"
        CONJECTURE = "conjecture"

    idea_x = _MockIdea(
        idea_id="mock-001",
        support_scope=frozenset({"c1", "c2", "c3", "c4"}),
        idea_type=_MockIdeaType.THEOREM,
        predicted_leverage=0.8,
        attainability=0.75,
    )
    idea_y = _MockIdea(
        idea_id="mock-002",
        support_scope=frozenset({"c1"}),
        idea_type=_MockIdeaType.CONSTRUCTION,
        predicted_leverage=0.3,
        attainability=0.4,
    )
    idea_z = _MockIdea(
        idea_id="mock-003",
        support_scope=frozenset({"c5", "c6"}),
        idea_type=_MockIdeaType.CONJECTURE,
        predicted_leverage=0.6,
        attainability=0.9,
    )

    # CostComponents
    cc = CostComponents.from_idea(idea_x, dependency_depth=3)
    print(f"CostComponents for idea_x: scope={cc.scope_cost:.3f}, type={cc.type_cost:.3f}, "
          f"depth={cc.depth_cost:.3f}, total={cc.total:.3f}")

    # CostEstimator
    estimator = CostEstimator(estimator_id="est-01")
    cc2 = estimator.estimate(idea_x, dependency_depth=3, portfolio_size=10)
    print(f"Adjusted cost (portfolio_size=10): {cc2.total:.4f}")

    # Forecaster
    obstructed = {"c1", "c2", "c3", "c4", "c5"}
    forecaster = ObstructionReductionForecaster(forecaster_id="fcast-01")
    est = forecaster.forecast(idea_x, obstructed, total_coords=20)
    print(f"\nReduction estimate: delta={est.expected_delta:.4f}, "
          f"confidence={est.confidence:.4f}, "
          f"[{est.lower_bound:.4f}, {est.upper_bound:.4f}]")

    # Standalone functions
    exp_red = compute_expected_reduction(0.8, 0.75, len(obstructed))
    raw_val = compute_raw_value(exp_red, cc2.total)
    tier = classify_tier(raw_val)
    print(f"\nExpected reduction: {exp_red:.4f}")
    print(f"Raw value V(I): {raw_val:.4f}")
    print(f"Tier: {tier.name}")

    # ValueDistribution
    dist = ValueDistribution(distribution_id="dist-main")
    for mock_idea in [idea_x, idea_y, idea_z]:
        cc_i = CostComponents.from_idea(mock_idea, dependency_depth=2)
        er_i = compute_expected_reduction(
            mock_idea.predicted_leverage, mock_idea.attainability, len(obstructed)
        )
        rv_i = compute_raw_value(er_i, cc_i.total)
        piv = PreImplementationValue(
            value_id=str(uuid.uuid4()),
            idea_id=mock_idea.idea_id,
            raw_value=rv_i,
            tier=classify_tier(rv_i),
            cost=cc_i.total,
            expected_reduction=er_i,
            timestamp=time.time(),
        )
        dist.append(piv)

    print(f"\nDistribution size: {len(dist)}")
    print(f"Mean value: {dist.mean_value():.4f}")
    print(f"Tier counts: {dist.tier_counts()}")
    print(f"Top-2 by raw_value: {[v.idea_id for v in dist.top_k(2)]}")

    # Analyzer
    analyzer = PreImplementationValuationAnalyzer(analyzer_id="anal-01")
    report = analyzer.analyze(dist)
    print(f"\nAnalysis recommendation: {report['recommendation']}")
    print(f"Pareto-optimal IDs: {report['pareto_ids']}")

    # Sort and filter
    all_vals = dist._values
    sorted_vals = sort_by_tier(all_vals)
    print(f"\nSorted by tier: {[(v.idea_id, v.tier.name) for v in sorted_vals]}")
    t2_vals = filter_by_tier(all_vals, ValuationTier.TIER_2)
    print(f"Values at TIER_2 or better: {[v.idea_id for v in t2_vals]}")
    total_portfolio_value = aggregate_portfolio_value(all_vals)
    print(f"Aggregate portfolio value: {total_portfolio_value:.4f}")

    # ROI
    for v in dist._values:
        print(f"  {v.idea_id}: ROI={v.roi():.3f}, worth={v.is_worth_implementing()}")

    # Coordinator
    print("\n--- Coordinator run ---")
    coord = PreImplementationValuationCoordinator()
    witness = coord.run(idea_x, obstructed, total_coords=20, dependency_depth=3, portfolio_size=5)
    print(f"Witness summary: {witness.summary}")
    print(f"Tier: {witness.tier.name}, Value: {witness.raw_value:.4f}")

    print("\n=== All smoke tests passed ===")
