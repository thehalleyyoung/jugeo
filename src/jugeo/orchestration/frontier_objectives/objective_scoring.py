"""Objective scoring engine for frontier objectives.

This module implements the scoring infrastructure described in theory2.tex
Ch47 -- Frontier algorithms and phase transitions.

All mutable classes use ``@dataclass(slots=True)``; value objects use
``@dataclass(frozen=True)``.  Upstream imports are guarded with try/except
so the module degrades gracefully in isolated testing environments.
"""

from __future__ import annotations

import math
import statistics
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Guarded upstream imports
# ---------------------------------------------------------------------------

try:
    from jugeo.orchestration.frontier import (
        FrontierNode, Frontier, FrontierBudget, FrontierHistory,
        FrontierDiagnostics, PhaseKind, TransitionTrigger,
        FrontierScorer, FrontierDiversity, BackpressureController,
    )
except Exception:  # pragma: no cover
    FrontierNode = Frontier = FrontierBudget = FrontierHistory = Any  # type: ignore[assignment,misc]
    FrontierDiagnostics = PhaseKind = TransitionTrigger = FrontierScorer = Any  # type: ignore[assignment,misc]
    FrontierDiversity = BackpressureController = Any  # type: ignore[assignment,misc]

try:
    from jugeo.orchestration.controller import (
        OrchestratorState, ConvergenceMonitor, MoveHistory,
    )
except Exception:  # pragma: no cover
    OrchestratorState = ConvergenceMonitor = MoveHistory = Any  # type: ignore[assignment,misc]

try:
    from jugeo.evidence.trust import (
        TrustLevel, TrustAlgebra, TrustTier, TrustProfile,
    )
except Exception:  # pragma: no cover
    TrustLevel = TrustAlgebra = TrustTier = TrustProfile = Any  # type: ignore[assignment,misc]

try:
    from jugeo.orchestration.frontier_objectives.models import (
        FrontierObjective, ObjectiveKind, ClosureGainEstimate, DiversityMetric,
        ScoringState, ObjectiveSet, ObjectiveResult,
    )
except Exception:  # pragma: no cover
    FrontierObjective = ObjectiveKind = ClosureGainEstimate = DiversityMetric = Any  # type: ignore[assignment,misc]
    ScoringState = ObjectiveSet = ObjectiveResult = Any  # type: ignore[assignment,misc]

__all__ = [
    "ScoringContext", "ObjectiveScorer", "ClosureGainPredictor",
    "StabilityAnalyzer", "DiversityEnforcer", "CostEstimator",
    "CompositeObjectiveFunction", "ScoringHistory",
    "score_objectives", "rank_nodes_by_objective",
    "compute_pareto_front", "aggregate_scores",
]


def _safe_float(obj: Any, attr: str, default: float = 0.0) -> float:
    """Return getattr(obj, attr, default) as float, swallowing errors."""
    try:
        return float(getattr(obj, attr, default))
    except (TypeError, ValueError):
        return default


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp value to [lo, hi]."""
    return max(lo, min(hi, value))


def _ema(series: list[float], alpha: float = 0.3) -> float:
    """Exponential moving average over series."""
    if not series:
        return 0.0
    ema = series[0]
    for v in series[1:]:
        ema = alpha * v + (1.0 - alpha) * ema
    return ema


@dataclass(frozen=True)
class ScoringContext:
    """Immutable context for a single scoring run.

    Carries identifying information about the frontier node being scored,
    the current orchestration phase, and the iteration count so that scoring
    functions can adapt their behaviour without mutating shared state.

    Attributes
    ----------
    context_id:
        Unique identifier for this scoring invocation.
    node_id:
        The frontier node being evaluated.
    phase:
        Current orchestration phase label (e.g. "exploration").
    iteration:
        Zero-based loop counter within the current phase.
    metadata:
        Free-form key/value annotations from the caller.
    """

    context_id: str
    node_id: str
    phase: str
    iteration: int
    metadata: dict[str, Any]

    @classmethod
    def make(
        cls,
        node_id: str,
        phase: str = "exploration",
        iteration: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> ScoringContext:
        """Create a fresh ScoringContext with a generated UUID.

        Args:
            node_id: Identifier of the frontier node being scored.
            phase: Current orchestration phase name.
            iteration: Loop counter in the current phase (default 0).
            metadata: Optional free-form dict; defaults to empty.

        Returns:
            A new ScoringContext.
        """
        return cls(
            context_id=str(uuid.uuid4()),
            node_id=node_id,
            phase=phase,
            iteration=iteration,
            metadata=metadata or {},
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {
            "context_id": self.context_id,
            "node_id": self.node_id,
            "phase": self.phase,
            "iteration": self.iteration,
            "metadata": self.metadata,
        }


@dataclass(slots=True)
class ObjectiveScorer:
    """Scores a frontier state against a registered set of objectives.

    The scorer evaluates each FrontierObjective in objective_set
    independently, then aggregates them into a weighted total.  Historical
    scores are maintained so trend analysis is possible.

    Attributes
    ----------
    objective_set:
        The ObjectiveSet containing all registered objectives.
    history:
        Append-only list of {objective_id: score} dicts from past calls.
    """

    objective_set: Any
    history: list[dict[str, float]] = field(default_factory=list)

    def score(self, state: Any, context: ScoringContext | None = None) -> dict[str, float]:
        """Score state against every registered objective.

        Args:
            state: Any object from which objective metrics can be read.
            context: Optional context for logging purposes.

        Returns:
            Mapping {objective_id: score} where each score is in [0, 1].
        """
        scores: dict[str, float] = {}
        if self.objective_set is None:
            return scores
        objectives = getattr(self.objective_set, "objectives", [])
        for obj in objectives:
            try:
                raw = obj.score(state)
                scores[obj.objective_id] = _clamp(float(raw))
            except Exception:
                scores[getattr(obj, "objective_id", str(id(obj)))] = 0.0
        self.update_history(scores)
        return scores

    def weighted_total(self, state: Any) -> float:
        """Return the weighted sum of all objective scores.

        Args:
            state: State object to score.

        Returns:
            A float in [0, 1] representing the composite objective value.
        """
        scores = self.score(state)
        if not scores:
            return 0.0
        objectives = getattr(self.objective_set, "objectives", [])
        weight_map = {obj.objective_id: getattr(obj, "weight", 1.0) for obj in objectives}
        total_weight = sum(weight_map.values()) or 1.0
        weighted_sum = sum(scores.get(oid, 0.0) * w for oid, w in weight_map.items())
        return _clamp(weighted_sum / total_weight)

    def rank_objectives(self, state: Any) -> list[tuple[str, float]]:
        """Return objectives sorted by score descending.

        Args:
            state: State object to score.

        Returns:
            List of (objective_id, score) pairs, highest score first.
        """
        scores = self.score(state)
        return sorted(scores.items(), key=lambda kv: kv[1], reverse=True)

    def score_with_context(self, state: Any, context: ScoringContext) -> list[Any]:
        """Score state and wrap results in ObjectiveResult objects.

        Args:
            state: State object to score.
            context: The scoring context.

        Returns:
            List of ObjectiveResult instances.
        """
        scores = self.score(state, context)
        objectives = getattr(self.objective_set, "objectives", [])
        results: list[Any] = []
        for obj in objectives:
            oid = getattr(obj, "objective_id", str(id(obj)))
            score_val = scores.get(oid, 0.0)
            threshold = getattr(obj, "threshold", 0.5)
            direction = getattr(obj, "direction", "maximize")
            satisfied = (score_val >= threshold if direction == "maximize" else score_val <= threshold)
            try:
                result = ObjectiveResult(  # type: ignore[call-arg]
                    objective_id=oid,
                    score=score_val,
                    satisfied=satisfied,
                    rationale=f"phase={context.phase} iter={context.iteration}",
                    timestamp=time.time(),
                )
                results.append(result)
            except Exception:
                results.append({"objective_id": oid, "score": score_val, "satisfied": satisfied})
        return results

    def update_history(self, scores: dict[str, float]) -> None:
        """Append scores to the internal history.

        Args:
            scores: Mapping {objective_id: score}.
        """
        entry = dict(scores)
        entry["_timestamp"] = time.time()
        self.history.append(entry)

    def trend(self, objective_id: str, window: int = 10) -> float:
        """Compute the average score for objective_id over the last window records.

        Args:
            objective_id: The objective whose trend to compute.
            window: Number of recent records to average.

        Returns:
            Average score, or 0.0 if no history available.
        """
        relevant = [h[objective_id] for h in self.history[-window:] if objective_id in h]
        return statistics.mean(relevant) if relevant else 0.0

    @classmethod
    def from_objectives(cls, objectives: list[Any]) -> ObjectiveScorer:
        """Create an ObjectiveScorer from a flat list of objectives.

        Args:
            objectives: List of FrontierObjective instances.

        Returns:
            A new ObjectiveScorer.
        """
        try:
            obj_set = ObjectiveSet(objectives=list(objectives), name="scorer_set")  # type: ignore[call-arg]
        except Exception:
            class _FallbackSet:
                def __init__(self, objs: list) -> None:
                    self.objectives = objs
            obj_set = _FallbackSet(objectives)  # type: ignore[assignment]
        return cls(objective_set=obj_set)


@dataclass(slots=True)
class ClosureGainPredictor:
    """Predicts expected closure gain for a frontier node.

    Maintains a rolling history of ClosureGainEstimate objects and uses
    exponential decay weighting to favour recent observations.

    Attributes
    ----------
    model_id:
        Unique identifier for this predictor instance.
    history:
        Ordered list of past ClosureGainEstimate records.
    decay_factor:
        Geometric decay applied to older estimates (0 < decay <= 1).
    """

    model_id: str
    history: list[Any] = field(default_factory=list)
    decay_factor: float = 0.95

    def predict(self, node_id: str, features: dict[str, float]) -> Any:
        """Predict closure gain for node_id given features.

        When history is available the prediction is a decay-weighted average
        of past gains.  When history is empty, falls back to base_gain from features.

        Args:
            node_id: The node to predict for.
            features: Feature dict.  Keys: base_gain, complexity, depth, novelty.

        Returns:
            A ClosureGainEstimate with the predicted gain.
        """
        base_gain = features.get("base_gain", 0.1)
        complexity = features.get("complexity", 1.0)

        if not self.history:
            expected = _clamp(base_gain / max(complexity, 1e-9), 0.0, 1.0)
            confidence = 0.3
        else:
            gains = [getattr(e, "expected_gain", 0.0) for e in self.history]
            confidences = [getattr(e, "confidence", 0.5) for e in self.history]
            weights = [self.decay_factor ** (len(gains) - 1 - i) for i in range(len(gains))]
            total_w = sum(weights) or 1.0
            expected = _clamp(sum(g * w for g, w in zip(gains, weights)) / total_w, 0.0, 1.0)
            confidence = _clamp(sum(c * w for c, w in zip(confidences, weights)) / total_w, 0.0, 1.0)

        try:
            return ClosureGainEstimate.make(  # type: ignore[union-attr]
                node_id=node_id,
                gain=expected,
                confidence=confidence,
                cost=features.get("cost", 1.0),
                method="ema_predictor",
            )
        except Exception:
            return {"node_id": node_id, "expected_gain": expected, "confidence": confidence}

    def update(self, estimate: Any) -> None:
        """Append estimate to the predictor history.

        Args:
            estimate: A ClosureGainEstimate (or dict) to record.
        """
        self.history.append(estimate)

    def best_estimate(self) -> Any:
        """Return the estimate with the highest risk-adjusted gain.

        Returns:
            The best ClosureGainEstimate, or None if empty.
        """
        if not self.history:
            return None
        return max(
            self.history,
            key=lambda e: (
                e.risk_adjusted_gain() if callable(getattr(e, "risk_adjusted_gain", None))
                else getattr(e, "expected_gain", 0.0) * getattr(e, "confidence", 1.0)
            ),
        )

    def confidence_interval(self, node_id: str) -> tuple[float, float]:
        """Compute a 90% confidence interval for node_id gain.

        Args:
            node_id: Node identifier (uses all history).

        Returns:
            (low, high) tuple, both in [0, 1].
        """
        gains = [getattr(e, "expected_gain", 0.0) for e in self.history]
        if len(gains) < 2:
            mean = gains[0] if gains else 0.1
            return (_clamp(mean - 0.1), _clamp(mean + 0.1))
        mean = statistics.mean(gains)
        std = statistics.stdev(gains)
        z = 1.645  # 90% CI
        return (_clamp(mean - z * std), _clamp(mean + z * std))

    def calibration_error(self) -> float:
        """Estimate the mean absolute error of past predictions.

        Returns:
            Mean absolute difference between successive expected gains, or 0.0.
        """
        gains = [getattr(e, "expected_gain", 0.0) for e in self.history]
        if len(gains) < 2:
            return 0.0
        diffs = [abs(gains[i] - gains[i - 1]) for i in range(1, len(gains))]
        return statistics.mean(diffs)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {
            "model_id": self.model_id,
            "history_length": len(self.history),
            "decay_factor": self.decay_factor,
            "calibration_error": self.calibration_error(),
        }

    @classmethod
    def make(cls, model_id: str | None = None) -> ClosureGainPredictor:
        """Create a fresh ClosureGainPredictor.

        Args:
            model_id: Optional identifier; generated via UUID if omitted.

        Returns:
            A new empty ClosureGainPredictor.
        """
        return cls(model_id=model_id or str(uuid.uuid4()))


@dataclass(slots=True)
class StabilityAnalyzer:
    """Analyzes the stability of the current frontier over time.

    Tracks a sliding window of objective scores and computes summary
    statistics used by the orchestration controller to decide whether
    to switch phases.

    Attributes
    ----------
    window:
        Maximum number of scores retained in the sliding window.
    threshold:
        CV threshold below which the frontier is considered stable (default 0.1).
    score_history:
        The sliding window of observed scores.
    """

    window: int = 20
    threshold: float = 0.1
    score_history: list[float] = field(default_factory=list)

    def analyze(self, scores: list[float]) -> dict[str, float]:
        """Compute stability statistics for scores.

        Args:
            scores: A sequence of recent objective scores.

        Returns:
            Dict with keys: mean, std, trend, stability_score, cv.
        """
        if not scores:
            return {"mean": 0.0, "std": 0.0, "trend": 0.0, "stability_score": 1.0, "cv": 0.0}
        mean = statistics.mean(scores)
        std = statistics.stdev(scores) if len(scores) > 1 else 0.0
        cv = std / max(abs(mean), 1e-9)
        stability_score = _clamp(1.0 - cv)
        if len(scores) >= 4:
            half = len(scores) // 2
            trend = statistics.mean(scores[half:]) - statistics.mean(scores[:half])
        else:
            trend = 0.0
        return {"mean": mean, "std": std, "trend": trend, "stability_score": stability_score, "cv": cv}

    def update(self, score: float) -> None:
        """Append score to the history, pruning if necessary.

        Args:
            score: A single objective score in [0, 1].
        """
        self.score_history.append(score)
        if len(self.score_history) > self.window:
            self.score_history = self.score_history[-self.window:]

    def is_stable(self) -> bool:
        """Return True when the coefficient of variation is below threshold.

        Returns:
            True if the frontier is currently stable.
        """
        if len(self.score_history) < 3:
            return True
        result = self.analyze(self.score_history)
        return result["cv"] < self.threshold

    def instability_signal(self) -> float:
        """Return a normalised instability signal in [0, 1].

        0.0 = stable; 1.0 = maximally unstable.

        Returns:
            Instability metric.
        """
        if len(self.score_history) < 3:
            return 0.0
        result = self.analyze(self.score_history)
        return _clamp(result["cv"])

    def phase_recommendation(self) -> str:
        """Suggest the next orchestration phase based on stability.

        Returns:
            One of "explore", "exploit", or "transition".
        """
        if len(self.score_history) < 3:
            return "explore"
        result = self.analyze(self.score_history)
        cv = result["cv"]
        trend = result["trend"]
        if cv > 0.3:
            return "explore"
        if trend > 0.05:
            return "exploit"
        if cv < 0.05:
            return "exploit"
        return "transition"

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary."""
        stats = self.analyze(self.score_history) if self.score_history else {}
        return {
            "window": self.window,
            "threshold": self.threshold,
            "history_length": len(self.score_history),
            "is_stable": self.is_stable(),
            "instability_signal": self.instability_signal(),
            "recommendation": self.phase_recommendation(),
            **stats,
        }


@dataclass(slots=True)
class DiversityEnforcer:
    """Enforces diversity constraints on frontier expansion.

    Checks that the current frontier satisfies minimum diversity requirements
    and provides filtering and penalty mechanisms when it does not.

    Attributes
    ----------
    min_clusters:
        Minimum number of distinct clusters required.
    min_entropy:
        Minimum Shannon entropy of node types required.
    min_coverage:
        Minimum coverage ratio required.
    violations:
        Log of violation messages from past calls.
    """

    min_clusters: int = 3
    min_entropy: float = 0.5
    min_coverage: float = 0.3
    violations: list[str] = field(default_factory=list)

    def check(self, metric: Any) -> list[str]:
        """Identify diversity violations in metric.

        Args:
            metric: A DiversityMetric or duck-typed object.

        Returns:
            List of violation strings. Empty = no violations.
        """
        msgs: list[str] = []
        cluster_count = getattr(metric, "cluster_count", 0)
        entropy = getattr(metric, "entropy", 0.0)
        coverage_ratio = getattr(metric, "coverage_ratio", 0.0)
        if cluster_count < self.min_clusters:
            msgs.append(f"cluster_count {cluster_count} < minimum {self.min_clusters}")
        if entropy < self.min_entropy:
            msgs.append(f"entropy {entropy:.3f} < minimum {self.min_entropy:.3f}")
        if coverage_ratio < self.min_coverage:
            msgs.append(f"coverage_ratio {coverage_ratio:.3f} < minimum {self.min_coverage:.3f}")
        self.violations.extend(msgs)
        return msgs

    def enforce(self, nodes: list[Any], metric: Any) -> list[Any]:
        """Filter nodes to improve diversity when violations exist.

        Args:
            nodes: List of frontier nodes.
            metric: Current DiversityMetric.

        Returns:
            Filtered list with improved diversity.
        """
        violations = self.check(metric)
        if not violations:
            return list(nodes)
        groups: dict[str, list[Any]] = {}
        for node in nodes:
            key = str(getattr(node, "node_type", getattr(node, "kind", id(node))))
            groups.setdefault(key, []).append(node)
        if not groups:
            return list(nodes)
        max_per_group = max(1, math.ceil(len(nodes) / max(len(groups), 1) / 2))
        filtered: list[Any] = []
        for group_nodes in groups.values():
            filtered.extend(group_nodes[:max_per_group])
        return filtered

    def penalty(self, metric: Any) -> float:
        """Compute a diversity penalty in [0, 1].

        Args:
            metric: A DiversityMetric.

        Returns:
            Penalty value (0 = none, 1 = maximum).
        """
        violations = self.check(metric)
        return _clamp(len(violations) / 3.0)

    def suggestion(self, metric: Any) -> str:
        """Produce a human-readable suggestion for improving diversity.

        Args:
            metric: A DiversityMetric.

        Returns:
            A suggestion string.
        """
        violations = self.check(metric)
        if not violations:
            return "Diversity is sufficient; no action required."
        parts = []
        cluster_count = getattr(metric, "cluster_count", 0)
        entropy = getattr(metric, "entropy", 0.0)
        coverage_ratio = getattr(metric, "coverage_ratio", 0.0)
        if cluster_count < self.min_clusters:
            parts.append(
                f"Add nodes from at least {self.min_clusters - cluster_count} additional semantic clusters."
            )
        if entropy < self.min_entropy:
            parts.append("Introduce more heterogeneous node types to raise entropy.")
        if coverage_ratio < self.min_coverage:
            parts.append("Expand search to cover a larger fraction of the objective space.")
        return " ".join(parts)

    def to_dict(self) -> dict[str, Any]:
        """Serialise enforcer configuration."""
        return {
            "min_clusters": self.min_clusters,
            "min_entropy": self.min_entropy,
            "min_coverage": self.min_coverage,
            "violation_count": len(self.violations),
        }


@dataclass(slots=True)
class CostEstimator:
    """Estimates the computational cost of exploring a frontier node.

    Maintains a running history of actual costs to adaptively adjust estimates.

    Attributes
    ----------
    base_cost:
        The fallback cost when no node-specific information is available.
    cost_multipliers:
        Per-attribute multipliers applied when estimating node costs.
    history:
        Log of actual costs observed after exploration.
    """

    base_cost: float = 1.0
    cost_multipliers: dict[str, float] = field(default_factory=dict)
    history: list[float] = field(default_factory=list)

    def estimate(self, node: Any) -> float:
        """Estimate the cost of exploring node.

        Args:
            node: A frontier node or any object with optional cost attrs.

        Returns:
            Estimated cost as a positive float.
        """
        if hasattr(node, "cost_estimate"):
            raw = float(getattr(node, "cost_estimate", self.base_cost))
            if raw > 0:
                return raw
        cost = self.base_cost
        for attr, mult in self.cost_multipliers.items():
            if hasattr(node, attr):
                try:
                    cost *= max(0.0, float(getattr(node, attr))) * mult + 1.0
                except (TypeError, ValueError):
                    pass
        return max(1e-6, cost)

    def update(self, actual_cost: float) -> None:
        """Record an actual post-exploration cost.

        Args:
            actual_cost: The observed cost after exploring a node.
        """
        self.history.append(max(0.0, actual_cost))

    def expected_cost(self) -> float:
        """Return the mean of observed costs, falling back to base_cost.

        Returns:
            Expected cost as a positive float.
        """
        if not self.history:
            return self.base_cost
        return statistics.mean(self.history)

    def overhead_ratio(self) -> float:
        """Return the ratio of mean actual cost to base_cost.

        Returns:
            A float >= 0. 1.0 means perfectly calibrated.
        """
        return self.expected_cost() / max(self.base_cost, 1e-9)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {
            "base_cost": self.base_cost,
            "cost_multipliers": dict(self.cost_multipliers),
            "history_length": len(self.history),
            "expected_cost": self.expected_cost(),
            "overhead_ratio": self.overhead_ratio(),
        }

    @classmethod
    def make(cls, base_cost: float = 1.0) -> CostEstimator:
        """Create a fresh CostEstimator with given base cost.

        Args:
            base_cost: The default cost per node.

        Returns:
            A new CostEstimator.
        """
        return cls(base_cost=base_cost)


@dataclass(slots=True)
class CompositeObjectiveFunction:
    """Combines multiple objectives with per-component weights.

    Supports weighted-sum aggregation, numerical gradient approximation,
    and Pareto-front identification.

    Attributes
    ----------
    components:
        List of (FrontierObjective, weight) pairs.
    aggregation:
        Aggregation method (currently "weighted_sum" only).
    """

    components: list[tuple[Any, float]]
    aggregation: str = "weighted_sum"

    def evaluate(self, state: Any) -> float:
        """Evaluate the composite objective on state.

        Args:
            state: A state object exposing metric attributes.

        Returns:
            Composite score in [0, 1].
        """
        if not self.components:
            return 0.0
        total_weight = sum(w for _, w in self.components) or 1.0
        weighted_sum = 0.0
        for obj, weight in self.components:
            try:
                raw = obj.score(state)
                weighted_sum += _clamp(float(raw)) * weight
            except Exception:
                pass
        return _clamp(weighted_sum / total_weight)

    def gradient(self, state: Any, epsilon: float = 0.01) -> dict[str, float]:
        """Approximate numerical gradient of the objective w.r.t. state attributes.

        For each float attribute of state, perturbs by +/- epsilon and computes
        central-difference gradient.

        Args:
            state: A ScoringState or mutable object.
            epsilon: Step size for finite differences.

        Returns:
            Mapping {attribute_name: partial_derivative}.
        """
        gradient: dict[str, float] = {}
        float_attrs = [
            attr for attr in vars(state)
            if isinstance(getattr(state, attr), (int, float))
        ] if hasattr(state, "__dict__") else []
        for attr in float_attrs:
            original = getattr(state, attr)
            try:
                setattr(state, attr, original + epsilon)
                score_plus = self.evaluate(state)
                setattr(state, attr, original - epsilon)
                score_minus = self.evaluate(state)
                setattr(state, attr, original)
                gradient[attr] = (score_plus - score_minus) / (2 * epsilon)
            except Exception:
                try:
                    setattr(state, attr, original)
                except Exception:
                    pass
        return gradient

    def add_component(self, obj: Any, weight: float) -> None:
        """Add a new objective component.

        Args:
            obj: A FrontierObjective to add.
            weight: Relative weight for this objective.
        """
        self.components.append((obj, max(0.0, weight)))

    def normalize_weights(self) -> None:
        """Normalise component weights to sum to 1.0 in place."""
        total = sum(w for _, w in self.components) or 1.0
        self.components = [(obj, w / total) for obj, w in self.components]

    def pareto_front(self, states: list[Any]) -> list[Any]:
        """Return the Pareto-non-dominated states.

        Args:
            states: List of state objects to filter.

        Returns:
            Pareto front (non-dominated subset).
        """
        if not states:
            return []
        scores_per_state = [
            [_clamp(obj.score(s)) for obj, _ in self.components]
            for s in states
        ]
        front: list[Any] = []
        for i, (state, s_scores) in enumerate(zip(states, scores_per_state)):
            dominated = False
            for j, o_scores in enumerate(scores_per_state):
                if i == j:
                    continue
                if all(o >= s for o, s in zip(o_scores, s_scores)) and any(
                    o > s for o, s in zip(o_scores, s_scores)
                ):
                    dominated = True
                    break
            if not dominated:
                front.append(state)
        return front

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {
            "aggregation": self.aggregation,
            "component_count": len(self.components),
            "weights": [w for _, w in self.components],
        }

    @classmethod
    def from_objectives(cls, objectives: list[Any]) -> CompositeObjectiveFunction:
        """Build a CompositeObjectiveFunction from a list of objectives.

        Args:
            objectives: List of FrontierObjective instances.

        Returns:
            A new CompositeObjectiveFunction.
        """
        components = [(obj, float(getattr(obj, "weight", 1.0))) for obj in objectives]
        return cls(components=components)


@dataclass(slots=True)
class ScoringHistory:
    """Append-only record of past scoring runs.

    Provides trend analysis, best-score lookup, and automatic pruning.

    Attributes
    ----------
    records:
        Ordered list of scoring records (dicts).
    max_records:
        Upper bound on the number of retained records.
    """

    records: list[dict[str, Any]] = field(default_factory=list)
    max_records: int = 1000

    def record(self, scores: dict[str, float], context: ScoringContext | None = None) -> None:
        """Append a scoring event to the history.

        Args:
            scores: Mapping {objective_id: score}.
            context: Optional context for provenance.
        """
        entry: dict[str, Any] = {"scores": dict(scores), "timestamp": time.time()}
        if context is not None:
            entry["context_id"] = context.context_id
            entry["node_id"] = context.node_id
            entry["phase"] = context.phase
        self.records.append(entry)
        self.prune()

    def recent(self, n: int = 10) -> list[dict[str, Any]]:
        """Return the n most recent records.

        Args:
            n: Number of records to return.

        Returns:
            List of record dicts (newest last).
        """
        return self.records[-n:]

    def average(self, objective_id: str) -> float:
        """Return the mean score for objective_id across all records.

        Args:
            objective_id: Target objective.

        Returns:
            Mean score, or 0.0 if not found.
        """
        vals = [r["scores"][objective_id] for r in self.records if objective_id in r.get("scores", {})]
        return statistics.mean(vals) if vals else 0.0

    def trend(self, objective_id: str, window: int = 20) -> float:
        """Estimate the trend over the last window records.

        Args:
            objective_id: Target objective.
            window: Number of recent records to analyse.

        Returns:
            Trend value. Positive = improving.
        """
        vals = [
            r["scores"][objective_id]
            for r in self.records[-window:]
            if objective_id in r.get("scores", {})
        ]
        if len(vals) < 2:
            return 0.0
        half = len(vals) // 2
        return statistics.mean(vals[half:]) - statistics.mean(vals[:half])

    def best(self, objective_id: str) -> float:
        """Return the highest score ever recorded for objective_id.

        Args:
            objective_id: Target objective.

        Returns:
            Maximum score, or 0.0 if not found.
        """
        vals = [r["scores"][objective_id] for r in self.records if objective_id in r.get("scores", {})]
        return max(vals) if vals else 0.0

    def to_dict(self) -> dict[str, Any]:
        """Serialise summary statistics."""
        all_ids: set[str] = set()
        for r in self.records:
            all_ids.update(r.get("scores", {}).keys())
        return {
            "record_count": len(self.records),
            "max_records": self.max_records,
            "objective_ids": sorted(all_ids),
        }

    def prune(self) -> None:
        """Remove oldest records when the history exceeds max_records."""
        if len(self.records) > self.max_records:
            self.records = self.records[-self.max_records:]


# ---------------------------------------------------------------------------
# Module-level functions
# ---------------------------------------------------------------------------

def score_objectives(state: Any, objectives: list[Any]) -> dict[str, float]:
    """Score state against each objective in objectives.

    Args:
        state: State object to score.
        objectives: List of FrontierObjective instances.

    Returns:
        Mapping {objective_id: score} with all scores in [0, 1].
    """
    scores: dict[str, float] = {}
    for obj in objectives:
        try:
            raw = obj.score(state)
            scores[obj.objective_id] = _clamp(float(raw))
        except Exception:
            scores[getattr(obj, "objective_id", str(id(obj)))] = 0.0
    return scores


def rank_nodes_by_objective(nodes: list[Any], objective: Any) -> list[tuple[Any, float]]:
    """Rank nodes by how well each satisfies a single objective.

    Args:
        nodes: List of frontier nodes.
        objective: A single FrontierObjective.

    Returns:
        List of (node, score) pairs sorted by score descending.
    """
    scored: list[tuple[Any, float]] = []
    for node in nodes:
        try:
            raw = objective.score(node)
            scored.append((node, _clamp(float(raw))))
        except Exception:
            scored.append((node, 0.0))
    return sorted(scored, key=lambda x: x[1], reverse=True)


def compute_pareto_front(states: list[Any], objectives: list[Any]) -> list[Any]:
    """Return the Pareto-non-dominated subset of states.

    Args:
        states: List of state objects.
        objectives: List of FrontierObjective instances.

    Returns:
        Non-dominated states.
    """
    if not states or not objectives:
        return list(states)
    score_matrix: list[list[float]] = []
    for s in states:
        row: list[float] = []
        for obj in objectives:
            try:
                row.append(_clamp(float(obj.score(s))))
            except Exception:
                row.append(0.0)
        score_matrix.append(row)
    front: list[Any] = []
    for i, (state, s_scores) in enumerate(zip(states, score_matrix)):
        dominated = False
        for j, o_scores in enumerate(score_matrix):
            if i == j:
                continue
            if all(o >= s for o, s in zip(o_scores, s_scores)) and any(
                o > s for o, s in zip(o_scores, s_scores)
            ):
                dominated = True
                break
        if not dominated:
            front.append(state)
    return front


def aggregate_scores(scores: dict[str, float], weights: dict[str, float]) -> float:
    """Compute a weighted average of scores using weights.

    Missing weights default to 1.0.  Missing scores default to 0.0.

    Args:
        scores: Mapping {objective_id: score}.
        weights: Mapping {objective_id: weight}.

    Returns:
        Weighted average in [0, 1].
    """
    if not scores:
        return 0.0
    total_weight = sum(weights.get(k, 1.0) for k in scores) or 1.0
    weighted_sum = sum(v * weights.get(k, 1.0) for k, v in scores.items())
    return _clamp(weighted_sum / total_weight)
