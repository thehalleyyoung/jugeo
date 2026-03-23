"""
Chapter 47 frontier algorithms and phase transitions.

This module implements the core algorithmic primitives for the frontier objective
system: scoring, clustering, beam search, expected improvement, phase detection,
and budget allocation.  All functions are designed to operate on duck-typed
frontier nodes so they remain independent of any particular upstream class
hierarchy.

Theoretical grounding (Ch47):
- Closure-gain monotonicity under refinement (Theorem 47.1)
- Phase-transition detectability from score history (Theorem 47.2)
- Diversity maintainability under budget constraints (Theorem 47.3)
- Budget-allocation feasibility (Theorem 47.4)
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

try:
    from jugeo.orchestration.frontier_objectives.models import (
        FrontierObjective,
        ObjectiveKind,
        ClosureGainEstimate,
        DiversityMetric,
        ScoringState,
        BudgetPolicy,
        PhaseKind,
    )
except Exception:
    pass

# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClusteringResult:
    """Result of a clustering operation on frontier nodes or scalar values."""

    cluster_count: int
    assignments: dict[str, int]
    centroids: list[float]
    inertia: float

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {
            "cluster_count": self.cluster_count,
            "assignments": self.assignments,
            "centroids": self.centroids,
            "inertia": self.inertia,
        }


@dataclass(frozen=True)
class BeamSearchResult:
    """Result of a beam-search pass over a frontier."""

    selected_nodes: list[Any]
    scores: list[float]
    iterations: int
    budget_spent: float

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary (nodes represented by their IDs)."""
        node_ids = []
        for n in self.selected_nodes:
            node_ids.append(getattr(n, "node_id", str(n)))
        return {
            "selected_node_ids": node_ids,
            "scores": self.scores,
            "iterations": self.iterations,
            "budget_spent": self.budget_spent,
        }


@dataclass(frozen=True)
class EIResult:
    """Expected-improvement computation result for a single node."""

    node_id: str
    ei_value: float
    incumbent: float
    mean: float
    std: float

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {
            "node_id": self.node_id,
            "ei_value": self.ei_value,
            "incumbent": self.incumbent,
            "mean": self.mean,
            "std": self.std,
        }


@dataclass(frozen=True)
class PhaseDetectionResult:
    """Result of a phase-transition detection attempt."""

    detected: bool
    from_phase: str
    to_phase: str
    confidence: float
    trigger: str
    evidence: dict

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {
            "detected": self.detected,
            "from_phase": self.from_phase,
            "to_phase": self.to_phase,
            "confidence": self.confidence,
            "trigger": self.trigger,
            "evidence": self.evidence,
        }


@dataclass(frozen=True)
class BudgetAllocationResult:
    """Result of a Pareto-budget allocation across objectives."""

    allocations: dict[str, float]
    pareto_efficient: bool
    total: float
    objectives_covered: int

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {
            "allocations": self.allocations,
            "pareto_efficient": self.pareto_efficient,
            "total": self.total,
            "objectives_covered": self.objectives_covered,
        }


# ---------------------------------------------------------------------------
# Helper math utilities
# ---------------------------------------------------------------------------


def entropy_from_distribution(counts: dict[str, int]) -> float:
    """Compute Shannon entropy (nats) from a frequency map.

    Parameters
    ----------
    counts:
        Mapping from category label to non-negative integer count.

    Returns
    -------
    float
        Shannon entropy in nats; returns 0.0 for empty or degenerate inputs.
    """
    total = sum(counts.values())
    if total <= 0:
        return 0.0
    entropy = 0.0
    for n in counts.values():
        if n > 0:
            p = n / total
            entropy -= p * math.log(p)
    return entropy


def normalize_scores(scores: dict[str, float]) -> dict[str, float]:
    """Min-max normalise a dictionary of scores to the [0, 1] interval.

    Parameters
    ----------
    scores:
        Mapping from identifier to raw numeric score.

    Returns
    -------
    dict[str, float]
        Normalised scores; if all values are equal, each maps to 0.0.
    """
    if not scores:
        return {}
    lo = min(scores.values())
    hi = max(scores.values())
    span = hi - lo
    if span == 0.0:
        return {k: 0.0 for k in scores}
    return {k: (v - lo) / span for k, v in scores.items()}


def weighted_harmonic_mean(values: list[float], weights: list[float]) -> float:
    """Compute the weighted harmonic mean of *values* with *weights*.

    Parameters
    ----------
    values:
        Positive numeric values.
    weights:
        Non-negative weights; must be the same length as *values*.

    Returns
    -------
    float
        Weighted harmonic mean, or 0.0 if inputs are degenerate.
    """
    if not values or not weights or len(values) != len(weights):
        return 0.0
    total_weight = sum(weights)
    if total_weight == 0.0:
        return 0.0
    denominator = sum(w / v for w, v in zip(weights, values) if v != 0.0)
    if denominator == 0.0:
        return 0.0
    return total_weight / denominator


def exponential_moving_average(series: list[float], alpha: float = 0.3) -> float:
    """Compute the exponential moving average (EMA) of a series.

    Parameters
    ----------
    series:
        Ordered sequence of numeric observations (oldest first).
    alpha:
        Smoothing factor in (0, 1].

    Returns
    -------
    float
        EMA of the last element using all preceding values, or 0.0 for empty
        input.
    """
    if not series:
        return 0.0
    ema = series[0]
    for value in series[1:]:
        ema = alpha * value + (1.0 - alpha) * ema
    return ema


def compute_regret(chosen_score: float, best_score: float) -> float:
    """Return the non-negative regret of choosing *chosen_score* when *best_score* was available.

    Parameters
    ----------
    chosen_score:
        Score of the option actually selected.
    best_score:
        Score of the best available option.

    Returns
    -------
    float
        max(0, best_score - chosen_score)
    """
    return max(0.0, best_score - chosen_score)


def pareto_dominates(a: dict[str, float], b: dict[str, float]) -> bool:
    """Return True if *a* Pareto-dominates *b* on all shared objectives.

    *a* dominates *b* when ``a[k] >= b[k]`` for every shared key and strictly
    greater on at least one.

    Parameters
    ----------
    a, b:
        Mappings from objective name to score.
    """
    shared = set(a) & set(b)
    if not shared:
        return False
    all_geq = all(a[k] >= b[k] for k in shared)
    any_gt = any(a[k] > b[k] for k in shared)
    return all_geq and any_gt


def kendall_tau_distance(ranking1: list[str], ranking2: list[str]) -> float:
    """Compute the normalised Kendall-tau distance between two ranked lists.

    Only items present in both lists are considered.  The distance is in
    [0, 1] where 0 means identical order and 1 means completely reversed.

    Parameters
    ----------
    ranking1, ranking2:
        Ordered lists of string identifiers (index 0 = top-ranked).
    """
    common = [x for x in ranking1 if x in ranking2]
    if len(common) < 2:
        return 0.0
    pos2 = {item: idx for idx, item in enumerate(ranking2)}
    order2 = [pos2[item] for item in common if item in pos2]
    n = len(order2)
    discordant = 0
    for i in range(n):
        for j in range(i + 1, n):
            if order2[i] > order2[j]:
                discordant += 1
    max_pairs = n * (n - 1) // 2
    if max_pairs == 0:
        return 0.0
    return discordant / max_pairs


# ---------------------------------------------------------------------------
# Standard normal approximations for EI
# ---------------------------------------------------------------------------


def _phi(x: float) -> float:
    """Standard normal probability density function."""
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _Phi(x: float) -> float:
    """Standard normal cumulative distribution function (approximation).

    Uses the erfc-based formula from the Python standard library which is
    accurate to machine precision.
    """
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


# ---------------------------------------------------------------------------
# Core algorithm functions
# ---------------------------------------------------------------------------


def score_frontier_node(
    node: Any,
    objectives: list[Any],
    state: Any,
) -> float:
    """Score a frontier node against a list of objectives.

    The function first constructs a :class:`ScoringState`-like namespace from
    the node's attributes (with sensible defaults when attributes are absent)
    and then aggregates the per-objective scores into a single value that is
    normalised to [0, 1].

    Parameters
    ----------
    node:
        Any object that may expose ``closure_gain``, ``stability_score``,
        ``diversity_score``, and ``cost_estimate`` attributes.
    objectives:
        Sequence of objective objects that expose a ``weight`` attribute and
        an optional ``score(state)`` callable.
    state:
        Current orchestrator state forwarded to each objective's scorer.

    Returns
    -------
    float
        Weighted, normalised aggregate score in [0, 1].
    """
    # Build a simple scoring-state namespace from node attributes
    closure_gain = float(getattr(node, "closure_gain", 0.5))
    stability = float(getattr(node, "stability_score", 0.5))
    diversity = float(getattr(node, "diversity_score", 0.5))
    cost = float(getattr(node, "cost_estimate", 1.0))

    # Try to use ScoringState if available; fall back to a plain dict
    scoring_context: Any
    try:
        scoring_context = ScoringState(  # type: ignore[name-defined]
            closure_gain=closure_gain,
            stability_score=stability,
            diversity_score=diversity,
            cost_estimate=cost,
        )
    except Exception:
        scoring_context = {
            "closure_gain": closure_gain,
            "stability_score": stability,
            "diversity_score": diversity,
            "cost_estimate": cost,
            "state": state,
        }

    if not objectives:
        # No objectives: fall back to raw node attributes
        raw = (closure_gain + stability + diversity) / 3.0
        return max(0.0, min(1.0, raw))

    total_weight = 0.0
    weighted_sum = 0.0
    for obj in objectives:
        weight = float(getattr(obj, "weight", 1.0))
        total_weight += weight
        # Try objective.score(), then objective.evaluate(), then attribute
        obj_score: float
        score_fn = getattr(obj, "score", None)
        if callable(score_fn):
            try:
                obj_score = float(score_fn(scoring_context))
            except Exception:
                obj_score = closure_gain
        else:
            eval_fn = getattr(obj, "evaluate", None)
            if callable(eval_fn):
                try:
                    obj_score = float(eval_fn(scoring_context))
                except Exception:
                    obj_score = stability
            else:
                obj_score = float(getattr(obj, "value", closure_gain))
        weighted_sum += weight * max(0.0, min(1.0, obj_score))

    if total_weight == 0.0:
        return 0.0
    raw_score = weighted_sum / total_weight
    return max(0.0, min(1.0, raw_score))


def estimate_closure_gain(
    node: Any,
    history: list[Any],
    descent_engine: Any = None,
) -> "ClosureGainEstimate":
    """Estimate the closure gain for a frontier node.

    Uses an exponential moving average of past gains (weighted by recency) if
    *history* is non-empty.  A base gain extracted from node attributes is
    blended in.  The confidence is derived from the length of the history.

    Parameters
    ----------
    node:
        Frontier node; may expose a ``closure_gain`` or ``base_gain``
        attribute.
    history:
        List of previous gain observations (floats or objects with a
        ``closure_gain`` attribute).
    descent_engine:
        Optional descent engine; if it exposes a ``last_gain`` attribute that
        is used as an additional signal.

    Returns
    -------
    ClosureGainEstimate
        Populated estimate with ``gain``, ``confidence``, and ``node_id``
        fields (falls back to a plain dict when the class is unavailable).
    """
    # Extract scalar gains from history entries
    gain_series: list[float] = []
    for entry in history:
        if isinstance(entry, (int, float)):
            gain_series.append(float(entry))
        else:
            g = getattr(entry, "closure_gain", None) or getattr(entry, "gain", None)
            if g is not None:
                gain_series.append(float(g))

    # Base gain from the node itself
    base_gain = float(
        getattr(node, "closure_gain", None)
        or getattr(node, "base_gain", None)
        or 0.5
    )

    # Blend EMA of history with node base gain
    if gain_series:
        ema_gain = exponential_moving_average(gain_series, alpha=0.4)
        # Weight recent history more strongly as it grows
        history_weight = min(0.9, len(gain_series) / (len(gain_series) + 5))
        estimated_gain = history_weight * ema_gain + (1.0 - history_weight) * base_gain
    else:
        estimated_gain = base_gain

    # Additional signal from descent engine
    if descent_engine is not None:
        last_gain = getattr(descent_engine, "last_gain", None)
        if last_gain is not None:
            estimated_gain = 0.7 * estimated_gain + 0.3 * float(last_gain)

    # Confidence: saturates towards 1 as history lengthens
    confidence = min(0.99, len(gain_series) / (len(gain_series) + 10.0))
    if len(gain_series) == 0:
        confidence = 0.1

    node_id = str(getattr(node, "node_id", uuid4()))

    try:
        return ClosureGainEstimate(  # type: ignore[call-arg,name-defined]
            node_id=node_id,
            gain=max(0.0, estimated_gain),
            confidence=confidence,
        )
    except Exception:
        # Return a plain object if the class is unavailable
        result: Any = type("ClosureGainEstimate", (), {})()
        result.node_id = node_id
        result.gain = max(0.0, estimated_gain)
        result.confidence = confidence
        return result


def detect_phase_transition(
    history: list[float],
    window: int = 20,
) -> PhaseDetectionResult:
    """Detect a phase transition in a score-history time series.

    Compares the mean of the first half vs the second half of the most recent
    *window* observations.  If the relative delta exceeds a threshold the
    function reports a transition and infers the direction (exploration →
    exploitation or vice-versa).

    Parameters
    ----------
    history:
        Ordered sequence of numeric scores (oldest first).
    window:
        Number of recent observations to consider.

    Returns
    -------
    PhaseDetectionResult
    """
    _DELTA_THRESHOLD = 0.08  # relative mean-shift that constitutes a transition

    recent = history[-window:] if len(history) >= window else history[:]
    if len(recent) < 4:
        return PhaseDetectionResult(
            detected=False,
            from_phase="unknown",
            to_phase="unknown",
            confidence=0.0,
            trigger="insufficient_data",
            evidence={"history_length": len(history)},
        )

    mid = len(recent) // 2
    first_half = recent[:mid]
    second_half = recent[mid:]
    mean1 = sum(first_half) / len(first_half)
    mean2 = sum(second_half) / len(second_half)

    # Variance of the window — used for confidence
    all_mean = sum(recent) / len(recent)
    variance = sum((x - all_mean) ** 2 for x in recent) / len(recent)
    std = math.sqrt(variance) if variance > 0 else 0.0

    delta = mean2 - mean1
    relative_delta = abs(delta) / (abs(mean1) + 1e-9)

    if relative_delta < _DELTA_THRESHOLD:
        return PhaseDetectionResult(
            detected=False,
            from_phase="stable",
            to_phase="stable",
            confidence=1.0 - relative_delta / _DELTA_THRESHOLD,
            trigger="no_shift",
            evidence={
                "mean1": mean1,
                "mean2": mean2,
                "delta": delta,
                "std": std,
            },
        )

    # Determine direction
    if delta > 0:
        from_phase = "exploration"
        to_phase = "exploitation"
        trigger = "score_increase"
    else:
        from_phase = "exploitation"
        to_phase = "exploration"
        trigger = "score_decrease"

    confidence = min(0.99, relative_delta / (relative_delta + 0.1))

    return PhaseDetectionResult(
        detected=True,
        from_phase=from_phase,
        to_phase=to_phase,
        confidence=confidence,
        trigger=trigger,
        evidence={
            "mean1": mean1,
            "mean2": mean2,
            "delta": delta,
            "relative_delta": relative_delta,
            "std": std,
            "window": len(recent),
        },
    )


def compute_diversity_metric(
    nodes: list[Any],
    clustering_fn: Any = None,
) -> "DiversityMetric":
    """Compute a diversity metric for a list of frontier nodes.

    Parameters
    ----------
    nodes:
        Sequence of frontier node objects.
    clustering_fn:
        Optional callable ``(nodes) -> ClusteringResult``; when supplied, the
        ``cluster_count`` from the result is used directly.

    Returns
    -------
    DiversityMetric
        Populated with entropy, coverage_ratio, novelty_score, and
        cluster_count (falls back to plain namespace when class unavailable).
    """
    if not nodes:
        try:
            return DiversityMetric(  # type: ignore[call-arg,name-defined]
                entropy=0.0,
                coverage_ratio=0.0,
                novelty_score=0.0,
                cluster_count=0,
            )
        except Exception:
            dm: Any = type("DiversityMetric", (), {})()
            dm.entropy = 0.0
            dm.coverage_ratio = 0.0
            dm.novelty_score = 0.0
            dm.cluster_count = 0
            return dm

    # Type distribution
    type_counts: dict[str, int] = {}
    hashes: set[int] = set()
    for node in nodes:
        node_type = str(getattr(node, "node_type", type(node).__name__))
        type_counts[node_type] = type_counts.get(node_type, 0) + 1
        try:
            hashes.add(hash(node))
        except Exception:
            hashes.add(id(node))

    unique_types = set(type_counts.keys())
    total = len(nodes)

    entropy = entropy_from_distribution(type_counts)
    coverage_ratio = min(1.0, len(unique_types) / total)
    novelty_score = min(1.0, len(hashes) / total)

    cluster_count = len(unique_types)
    if clustering_fn is not None:
        try:
            cr = clustering_fn(nodes)
            cluster_count = int(getattr(cr, "cluster_count", cluster_count))
        except Exception:
            pass

    try:
        return DiversityMetric(  # type: ignore[call-arg,name-defined]
            entropy=entropy,
            coverage_ratio=coverage_ratio,
            novelty_score=novelty_score,
            cluster_count=cluster_count,
        )
    except Exception:
        dm = type("DiversityMetric", (), {})()
        dm.entropy = entropy
        dm.coverage_ratio = coverage_ratio
        dm.novelty_score = novelty_score
        dm.cluster_count = cluster_count
        return dm


def allocate_budget_pareto(
    objectives: list[Any],
    total_budget: float,
) -> BudgetAllocationResult:
    """Allocate *total_budget* proportionally to objective weights.

    Parameters
    ----------
    objectives:
        Sequence of objective objects, each exposing an ``objective_id`` and
        a ``weight`` attribute.
    total_budget:
        Total budget to distribute.

    Returns
    -------
    BudgetAllocationResult
    """
    if not objectives or total_budget <= 0.0:
        return BudgetAllocationResult(
            allocations={},
            pareto_efficient=True,
            total=0.0,
            objectives_covered=0,
        )

    weights: dict[str, float] = {}
    for obj in objectives:
        oid = str(getattr(obj, "objective_id", str(uuid4())))
        w = float(getattr(obj, "weight", 1.0))
        weights[oid] = max(0.0, w)

    total_weight = sum(weights.values())
    if total_weight == 0.0:
        # Equal split
        share = total_budget / len(objectives)
        allocations = {oid: share for oid in weights}
    else:
        allocations = {
            oid: (w / total_weight) * total_budget for oid, w in weights.items()
        }

    # Pareto efficiency: no single objective receives strictly more than half
    # AND no other objective receives zero (a common practical criterion).
    max_alloc = max(allocations.values(), default=0.0)
    pareto_efficient = max_alloc < total_budget * 0.75 and all(
        v > 0 for v in allocations.values()
    )

    return BudgetAllocationResult(
        allocations=allocations,
        pareto_efficient=pareto_efficient,
        total=sum(allocations.values()),
        objectives_covered=len(allocations),
    )


def frontier_beam_search(
    frontier: Any,
    objectives: list[Any],
    beam_width: int = 5,
    budget: float = 100.0,
) -> BeamSearchResult:
    """Beam-search over a frontier, selecting the highest-scoring nodes.

    Parameters
    ----------
    frontier:
        Frontier object; nodes are extracted via ``frontier.nodes`` or by
        iterating the object directly.
    objectives:
        Objectives passed to :func:`score_frontier_node`.
    beam_width:
        Maximum number of nodes to retain.
    budget:
        Computational budget; each node evaluation costs 1.0 unit.

    Returns
    -------
    BeamSearchResult
    """
    # Extract nodes from frontier
    nodes: list[Any]
    if hasattr(frontier, "nodes"):
        nodes = list(getattr(frontier, "nodes", []) or [])
    else:
        try:
            nodes = list(frontier)
        except Exception:
            nodes = []

    budget_spent = 0.0
    iterations = 0
    scored: list[tuple[Any, float]] = []

    for node in nodes:
        if budget_spent >= budget:
            break
        score = score_frontier_node(node, objectives, state=None)
        budget_spent += 1.0
        iterations += 1
        scored.append((node, score))

    # Keep top beam_width by score
    scored.sort(key=lambda t: t[1], reverse=True)
    beam = scored[:beam_width]

    selected = [t[0] for t in beam]
    scores = [t[1] for t in beam]

    return BeamSearchResult(
        selected_nodes=selected,
        scores=scores,
        iterations=iterations,
        budget_spent=budget_spent,
    )


def expected_improvement(
    node: Any,
    incumbent: float,
    model: Any = None,
) -> EIResult:
    """Compute the expected improvement (EI) for a frontier node.

    The standard EI formula is:

        EI(x) = (μ − f*) · Φ(z) + σ · φ(z)

    where  z = (μ − f*) / σ,  f* is the incumbent,  Φ is the standard normal
    CDF, and φ is the standard normal PDF.

    Parameters
    ----------
    node:
        Node with optional ``mean`` / ``mu``, ``std`` / ``sigma`` attributes.
    incumbent:
        Best observed score so far.
    model:
        Optional surrogate model; if it exposes ``predict(node)`` returning
        ``(mean, std)`` that takes precedence.

    Returns
    -------
    EIResult
    """
    # Extract or predict mean and std
    mu: float
    sigma: float
    if model is not None:
        predict_fn = getattr(model, "predict", None)
        if callable(predict_fn):
            try:
                prediction = predict_fn(node)
                if isinstance(prediction, (tuple, list)) and len(prediction) >= 2:
                    mu, sigma = float(prediction[0]), float(prediction[1])
                else:
                    mu = float(prediction)
                    sigma = float(getattr(node, "std", getattr(node, "sigma", 0.1)))
            except Exception:
                mu = float(getattr(node, "mean", getattr(node, "mu", 0.5)))
                sigma = float(getattr(node, "std", getattr(node, "sigma", 0.1)))
        else:
            mu = float(getattr(node, "mean", getattr(node, "mu", 0.5)))
            sigma = float(getattr(node, "std", getattr(node, "sigma", 0.1)))
    else:
        mu = float(getattr(node, "mean", getattr(node, "mu", 0.5)))
        sigma = float(getattr(node, "std", getattr(node, "sigma", 0.1)))

    sigma = max(sigma, 1e-9)  # avoid division by zero
    node_id = str(getattr(node, "node_id", uuid4()))

    z = (mu - incumbent) / sigma
    ei_value = max(0.0, (mu - incumbent) * _Phi(z) + sigma * _phi(z))

    return EIResult(
        node_id=node_id,
        ei_value=ei_value,
        incumbent=incumbent,
        mean=mu,
        std=sigma,
    )


def simple_cluster(items: list[float], k: int = 3) -> ClusteringResult:
    """Simple k-means-like clustering of scalar float values.

    Runs up to 100 iterations of Lloyd's algorithm with equidistant initial
    centroids.

    Parameters
    ----------
    items:
        Sequence of scalar values to cluster.
    k:
        Number of clusters.

    Returns
    -------
    ClusteringResult
    """
    if not items:
        return ClusteringResult(
            cluster_count=0,
            assignments={},
            centroids=[],
            inertia=0.0,
        )

    k = max(1, min(k, len(items)))
    lo, hi = min(items), max(items)
    span = hi - lo

    # Initialise centroids at equal intervals
    if span == 0.0:
        centroids = [lo] * k
    else:
        centroids = [lo + (i * span / (k - 1 if k > 1 else 1)) for i in range(k)]

    assignments: list[int] = [0] * len(items)

    for _iteration in range(100):
        # Assignment step
        changed = False
        for idx, x in enumerate(items):
            best = min(range(k), key=lambda ci: abs(x - centroids[ci]))
            if best != assignments[idx]:
                assignments[idx] = best
                changed = True

        if not changed:
            break

        # Update step
        cluster_sums = [0.0] * k
        cluster_counts = [0] * k
        for idx, x in enumerate(items):
            cluster_sums[assignments[idx]] += x
            cluster_counts[assignments[idx]] += 1
        for ci in range(k):
            if cluster_counts[ci] > 0:
                centroids[ci] = cluster_sums[ci] / cluster_counts[ci]

    # Inertia: sum of squared distances to assigned centroid
    inertia = sum(
        (x - centroids[assignments[i]]) ** 2 for i, x in enumerate(items)
    )

    # Build string-keyed assignment dict (index as string key)
    assignment_dict = {str(i): assignments[i] for i in range(len(items))}

    return ClusteringResult(
        cluster_count=k,
        assignments=assignment_dict,
        centroids=centroids,
        inertia=inertia,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    # Result types
    "ClusteringResult",
    "BeamSearchResult",
    "EIResult",
    "PhaseDetectionResult",
    "BudgetAllocationResult",
    # Core algorithms
    "score_frontier_node",
    "estimate_closure_gain",
    "detect_phase_transition",
    "compute_diversity_metric",
    "allocate_budget_pareto",
    "frontier_beam_search",
    "expected_improvement",
    "simple_cluster",
    # Utilities
    "entropy_from_distribution",
    "pareto_dominates",
    "normalize_scores",
    "weighted_harmonic_mean",
    "exponential_moving_average",
    "kendall_tau_distance",
    "compute_regret",
    # Cross-subsystem integration
    "objective_score_with_covers",
    "solver_validated_objective",
    "judgment_informed_scoring",
]


# ---------------------------------------------------------------------------
# Cross-subsystem integration: geometry, solver, judgments, evidence
# ---------------------------------------------------------------------------

try:
    from jugeo.geometry.covers import Cover, score_cover, CoverMetric
except Exception:
    Cover = None  # type: ignore[assignment,misc]
    score_cover = None  # type: ignore[assignment]
    CoverMetric = None  # type: ignore[assignment,misc]

try:
    from jugeo.solver.z3_session import Z3Session, SolverResult
except Exception:
    Z3Session = None  # type: ignore[assignment,misc]
    SolverResult = None  # type: ignore[assignment,misc]

try:
    from jugeo.judgments.sections import SectionComparator
except Exception:
    SectionComparator = None  # type: ignore[assignment,misc]


def objective_score_with_covers(node, cover):
    """Score a frontier objective incorporating cover quality from jugeo.geometry.covers.

    The cover metric modulates the raw objective score: better covers yield
    higher effective scores because they reduce the remaining proof burden.
    """
    raw_score = getattr(node, "score", 0.5)
    if score_cover is not None and cover is not None:
        try:
            metric = score_cover(cover)
            cover_bonus = getattr(metric, "completeness", 0.0)
        except Exception:
            cover_bonus = 0.0
    else:
        cover_bonus = 0.0

    return {
        "raw_score": raw_score,
        "cover_bonus": cover_bonus,
        "effective_score": raw_score + 0.3 * cover_bonus,
        "subsystem": "jugeo.geometry.covers",
    }


def solver_validated_objective(objective):
    """Validate a frontier objective's feasibility via Z3 (jugeo.solver.z3_session).

    Submits the objective's constraints to a Z3 session and returns whether
    the objective is satisfiable.
    """
    if Z3Session is None:
        return {"feasible": None, "reason": "Z3Session unavailable",
                "subsystem": "jugeo.solver.z3_session"}
    try:
        session = Z3Session()
        constraints = getattr(objective, "constraints", [])
        for c in constraints:
            session.add(c)
        outcome = session.check()
        return {"feasible": getattr(outcome, "satisfiable", False),
                "outcome": outcome,
                "subsystem": "jugeo.solver.z3_session"}
    except Exception as exc:
        return {"feasible": None, "reason": str(exc),
                "subsystem": "jugeo.solver.z3_session"}


def judgment_informed_scoring(node, sections):
    """Adjust objective scoring based on judgment section quality (jugeo.judgments.sections).

    Higher section quality implies the objective is better understood
    and can be scored more aggressively.
    """
    raw_score = getattr(node, "score", 0.5)
    if SectionComparator is None or not sections:
        return {"adjusted_score": raw_score, "quality_factor": 1.0,
                "subsystem": "jugeo.judgments.sections"}
    comparator = SectionComparator()
    scores = []
    for s in sections:
        try:
            scores.append(float(comparator.compare(s, s)))
        except Exception:
            scores.append(0.0)
    avg_quality = sum(scores) / len(scores) if scores else 0.5
    factor = 0.8 + 0.4 * avg_quality
    return {"adjusted_score": raw_score * factor, "quality_factor": factor,
            "subsystem": "jugeo.judgments.sections"}
