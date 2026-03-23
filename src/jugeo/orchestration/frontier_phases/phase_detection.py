"""
Phase detection and classification for the frontier_phases orchestration sub-package.

This module is part of JuGeo's copilot-assisted encoding of theory2.tex Chapter 47:
Phase dynamics — classifying and managing search phases over admissible frontiers.

Overview
--------
Robust phase detection requires aggregating *signals* from the live frontier state,
applying *heuristic rules* to map those signals to a coarse :class:`PhaseKind`,
and then stabilising the classification over a *sliding window* of recent
observations to filter out transient noise.  A confidence estimator quantifies
uncertainty in the current classification, and a transition detector tracks
when to commit to a phase change.

This module provides:

* :class:`PhaseSignalExtractor` — pull raw numeric signals out of a frontier
  (or a mock/dict-like proxy).
* :class:`PhaseHeuristics` — pure rule-based mapping from signals to
  :class:`PhaseKind`.
* :class:`PhaseConfidenceEstimator` — soft scoring across all phase candidates.
* :class:`PhaseWindowAnalyzer` — sliding-window stability analysis.
* :class:`PhaseClassifier` — high-level classifier combining all of the above.
* :class:`TransitionDetector` — detects and records phase transition events.
* :class:`PhaseChangeNotifier` — pub/sub bus for downstream consumers.

Design notes
~~~~~~~~~~~~
* All signal values are normalised to ``[0.0, 1.0]`` before being passed to
  heuristic rules so that rules can use uniform threshold constants.
* The :class:`PhaseWindowAnalyzer` deliberately uses a :class:`collections.deque`
  so that ``add_snapshot`` is O(1) amortised regardless of window size.
* Import of the heavier ``jugeo.orchestration.frontier`` module is guarded by a
  ``try/except ImportError`` block so that this module can be imported in
  isolation (e.g. for unit tests) without requiring the full orchestration stack.

Chapter reference: theory2.tex Ch47 — Phase dynamics.

copilot
"""
from __future__ import annotations

import math
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable

try:
    from jugeo.orchestration.frontier import (  # noqa: F401
        Frontier,
        FrontierNode,
        FrontierHistory,
        PhaseTransition,
        BackpressureController,
    )
except ImportError:
    pass

# Always use models.py enums: frontier.py's PhaseKind uses a different
# value set (COLLAPSE/SATURATION) vs the frontier_phases sub-package
# (STALLED/CONVERGED/DIVERGED/TRANSITION).
from jugeo.orchestration.frontier_phases.models import (
    PhaseKind,
    TransitionTrigger,
    PhaseDescriptor,
    PhaseTransitionRecord,
    PhaseHistory,
    StallDetector,
    ConvergenceCertificate,
    PhaseHealthStatus,
)

__all__ = [
    "PhaseSignalExtractor",
    "PhaseHeuristics",
    "PhaseConfidenceEstimator",
    "PhaseWindowAnalyzer",
    "PhaseClassifier",
    "TransitionDetector",
    "PhaseChangeNotifier",
]

# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Return *value* clamped to the range [*lo*, *hi*]."""
    return max(lo, min(hi, value))


def _safe_mean(values: list[float]) -> float:
    """Return the arithmetic mean of *values*, or ``0.0`` for an empty list."""
    return sum(values) / len(values) if values else 0.0


def _safe_std(values: list[float]) -> float:
    """Return the population standard deviation, or ``0.0`` for < 2 elements."""
    if len(values) < 2:
        return 0.0
    mean = _safe_mean(values)
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    return math.sqrt(variance)


def _linear_slope(pairs: list[tuple[float, float]]) -> float:
    """Compute the least-squares slope of ``(x, y)`` pairs.

    Returns ``0.0`` if there are fewer than 2 pairs or the denominator is
    degenerate.
    """
    n = len(pairs)
    if n < 2:
        return 0.0
    sum_x = sum(x for x, _ in pairs)
    sum_y = sum(y for _, y in pairs)
    sum_xx = sum(x * x for x, _ in pairs)
    sum_xy = sum(x * y for x, y in pairs)
    denom = n * sum_xx - sum_x * sum_x
    if abs(denom) < 1e-12:
        return 0.0
    return (n * sum_xy - sum_x * sum_y) / denom


# ---------------------------------------------------------------------------
# 1. PhaseSignalExtractor
# ---------------------------------------------------------------------------


class PhaseSignalExtractor:
    """Extracts numeric classification signals from a live frontier state.

    This class bridges the gap between the raw :class:`Frontier` API and the
    normalised signal dictionary consumed by :class:`PhaseHeuristics`.  Each
    signal is a float in ``[0.0, 1.0]`` unless otherwise documented.

    When a frontier object is not available (e.g. during testing), the
    extractor accepts a plain ``dict`` proxy that may already contain
    pre-computed signal values; missing keys fall back to sensible defaults.

    Parameters
    ----------
    frontier:
        A :class:`Frontier` instance or any object exposing compatible
        attributes/methods (duck-typed).  May also be a plain ``dict`` whose
        keys match the standard signal names.
    history:
        Optional :class:`PhaseHistory` providing temporal context for
        computing rate-based signals.
    """

    def __init__(self, frontier: Any, history: Any | None = None) -> None:
        self._frontier = frontier
        self._history = history
        self._is_dict = isinstance(frontier, dict)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract_signals(self) -> dict[str, float]:
        """Extract all classification signals and return them as a dictionary.

        Keys returned
        ~~~~~~~~~~~~~
        ``coverage_rate``
            Proportion of the admissible space estimated to be covered by the
            current frontier (0 = none, 1 = complete).
        ``diversity_score``
            Structural diversity of nodes in the frontier (0 = homogeneous,
            1 = maximally diverse).
        ``stall_indicator``
            Normalised stall signal (0 = no stall, 1 = complete stall).
        ``exploitation_ratio``
            Fraction of frontier nodes that are in exploitation-favourable
            positions (high closure, low cost).
        ``divergence_score``
            Signal indicating incoherent spread across disjoint sub-spaces.
        ``closure_variance``
            Normalised variance of closure estimates across frontier nodes.
        ``cost_growth_rate``
            Rate at which frontier cost is increasing, normalised to [0, 1].
        """
        return {
            "coverage_rate": _clamp(self.coverage_rate()),
            "diversity_score": _clamp(self.diversity_metric()),
            "stall_indicator": _clamp(self.stall_indicator()),
            "exploitation_ratio": _clamp(self.exploitation_ratio()),
            "divergence_score": _clamp(self._divergence_score()),
            "closure_variance": _clamp(self._closure_variance()),
            "cost_growth_rate": _clamp(self._cost_growth_rate()),
        }

    def coverage_rate(self) -> float:
        """Estimate the coverage rate of the current frontier.

        Coverage is approximated as the mean effective closure across all
        frontier nodes.  For dict-proxy frontiers the value is read directly
        from the ``'coverage_rate'`` key.

        Returns
        -------
        float
            A value in ``[0.0, 1.0]``.
        """
        if self._is_dict:
            return float(self._frontier.get("coverage_rate", 0.3))
        nodes = self._get_all_nodes()
        if not nodes:
            return 0.0
        closures = [self._node_closure(n) for n in nodes]
        return _clamp(_safe_mean(closures))

    def diversity_metric(self) -> float:
        """Compute the structural diversity of the current frontier.

        Diversity is estimated via the normalised mean pairwise label-distance
        between frontier nodes.  For dict-proxy frontiers the value is read
        directly from ``'diversity_score'``.

        Returns
        -------
        float
            A value in ``[0.0, 1.0]``.
        """
        if self._is_dict:
            return float(self._frontier.get("diversity_score", 0.5))
        try:
            score = self._frontier.diversity_score()
            return _clamp(float(score))
        except (AttributeError, TypeError):
            nodes = self._get_all_nodes()
            if len(nodes) < 2:
                return 0.0
            depths = [self._node_depth(n) for n in nodes]
            depth_std = _safe_std(depths)
            return _clamp(depth_std / (max(depths) + 1e-9))

    def stall_indicator(self) -> float:
        """Return a normalised stall signal.

        Combines information from :class:`StallDetector` (if available via
        the history) and the inverse coverage rate.  A value near 1.0
        indicates a near-total stall.

        Returns
        -------
        float
            A value in ``[0.0, 1.0]``.
        """
        if self._is_dict:
            return float(self._frontier.get("stall_indicator", 0.0))
        if self._history is not None:
            # Try to read stall information from a StallDetector stored in history
            stall_det = getattr(self._history, "_stall_detector", None)
            if stall_det is not None and hasattr(stall_det, "progress_rate"):
                rate = stall_det.progress_rate()
                # Low progress rate → high stall indicator
                return _clamp(1.0 - min(rate / 0.1, 1.0))
        # Fallback: zero-diversity + low coverage → stall
        diversity = self.diversity_metric()
        coverage = self.coverage_rate()
        stall_proxy = (1.0 - diversity) * (1.0 - coverage)
        return _clamp(stall_proxy)

    def exploitation_ratio(self) -> float:
        """Return the fraction of frontier nodes that are exploitation-ready.

        A node is exploitation-ready when its closure estimate exceeds 0.5
        and its cost is below the median cost of the frontier.

        Returns
        -------
        float
            A value in ``[0.0, 1.0]``.
        """
        if self._is_dict:
            return float(self._frontier.get("exploitation_ratio", 0.4))
        nodes = self._get_all_nodes()
        if not nodes:
            return 0.0
        costs = [self._node_cost(n) for n in nodes]
        median_cost = sorted(costs)[len(costs) // 2] if costs else 1.0
        ready = sum(
            1
            for n in nodes
            if self._node_closure(n) > 0.5 and self._node_cost(n) <= median_cost
        )
        return _clamp(ready / len(nodes))

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _divergence_score(self) -> float:
        """Compute incoherence / divergence signal."""
        if self._is_dict:
            return float(self._frontier.get("divergence_score", 0.0))
        nodes = self._get_all_nodes()
        if len(nodes) < 3:
            return 0.0
        closures = [self._node_closure(n) for n in nodes]
        # High variance in closure combined with high mean depth signals divergence
        closure_std = _safe_std(closures)
        mean_closure = _safe_mean(closures)
        # If mean closure is low and variance is high → diverged
        divergence = closure_std * (1.0 - mean_closure)
        return _clamp(divergence * 2.0)

    def _closure_variance(self) -> float:
        """Return normalised variance of closure estimates."""
        if self._is_dict:
            return float(self._frontier.get("closure_variance", 0.1))
        nodes = self._get_all_nodes()
        if len(nodes) < 2:
            return 0.0
        closures = [self._node_closure(n) for n in nodes]
        return _clamp(_safe_std(closures) * 2.0)

    def _cost_growth_rate(self) -> float:
        """Return normalised cost growth rate."""
        if self._is_dict:
            return float(self._frontier.get("cost_growth_rate", 0.1))
        nodes = self._get_all_nodes()
        if not nodes:
            return 0.0
        costs = [self._node_cost(n) for n in nodes]
        mean_cost = _safe_mean(costs)
        # Normalise: assume costs beyond 5.0 are 'very high'
        return _clamp(mean_cost / 5.0)

    def _get_all_nodes(self) -> list[Any]:
        """Return all nodes from the frontier, handling multiple API shapes."""
        try:
            return list(self._frontier.all_nodes())
        except AttributeError:
            pass
        try:
            return list(self._frontier.nodes.values())
        except AttributeError:
            return []

    @staticmethod
    def _node_closure(node: Any) -> float:
        """Extract closure estimate from a node, defaulting to 0.0."""
        for attr in ("effective_closure", "closure_estimate"):
            val = getattr(node, attr, None)
            if val is not None:
                return float(val() if callable(val) else val)
        return 0.0

    @staticmethod
    def _node_cost(node: Any) -> float:
        """Extract cost from a node, defaulting to 1.0."""
        return float(getattr(node, "cost", 1.0))

    @staticmethod
    def _node_depth(node: Any) -> float:
        """Extract depth from a node, defaulting to 0."""
        return float(getattr(node, "depth", 0))


# ---------------------------------------------------------------------------
# 2. PhaseHeuristics
# ---------------------------------------------------------------------------


class PhaseHeuristics:
    """Pure heuristic rules for mapping signal dictionaries to :class:`PhaseKind`.

    All rules operate on the normalised signal dictionary produced by
    :class:`PhaseSignalExtractor`.  Each rule is an independent predicate;
    ``classify_signals`` resolves conflicts by applying rules in priority order.

    Parameters
    ----------
    thresholds:
        Optional override for the default threshold constants.  Provide a
        mapping of ``threshold_name → value`` to customise behaviour.

    Default threshold names and values
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    ``stall_score_min`` (0.65)
        Minimum ``stall_indicator`` for the stall rule to fire.
    ``diversity_min`` (0.25)
        Minimum diversity required to avoid triggering the divergence rule.
    ``coverage_rate_min`` (0.75)
        Minimum coverage rate for the convergence rule to fire.
    ``exploitation_ratio_min`` (0.55)
        Minimum exploitation ratio for the exploitation rule.
    ``divergence_score_max`` (0.70)
        Maximum divergence score before the divergence rule fires.
    ``exploration_diversity_min`` (0.45)
        Minimum diversity for the exploration rule to fire.
    """

    _DEFAULTS: dict[str, float] = {
        "stall_score_min": 0.65,
        "diversity_min": 0.25,
        "coverage_rate_min": 0.75,
        "exploitation_ratio_min": 0.55,
        "divergence_score_max": 0.70,
        "exploration_diversity_min": 0.45,
    }

    def __init__(self, thresholds: dict[str, float] | None = None) -> None:
        self._thresholds: dict[str, float] = {**self._DEFAULTS}
        if thresholds:
            self._thresholds.update(thresholds)

    # ------------------------------------------------------------------
    # Classification entry point
    # ------------------------------------------------------------------

    def classify_signals(self, signals: dict[str, float]) -> PhaseKind:
        """Map a signal dictionary to the most appropriate :class:`PhaseKind`.

        Rules are evaluated in descending priority.  The first matching rule
        wins.  If no rule fires, the phase defaults to :attr:`PhaseKind.EXPLORATION`.

        Priority order
        ~~~~~~~~~~~~~~
        1. Stall (overrides everything else)
        2. Divergence
        3. Convergence
        4. Exploitation
        5. Exploration
        6. Default → :attr:`PhaseKind.EXPLORATION`

        Parameters
        ----------
        signals:
            Normalised signal dictionary from :class:`PhaseSignalExtractor`.

        Returns
        -------
        PhaseKind
        """
        if self.apply_stall_rule(signals):
            return PhaseKind.STALLED
        if self.apply_divergence_rule(signals):
            return PhaseKind.DIVERGED
        if self.apply_convergence_rule(signals):
            return PhaseKind.CONVERGED
        if self.apply_exploitation_rule(signals):
            return PhaseKind.EXPLOITATION
        if self.apply_exploration_rule(signals):
            return PhaseKind.EXPLORATION
        return PhaseKind.EXPLORATION

    # ------------------------------------------------------------------
    # Individual rules
    # ------------------------------------------------------------------

    def apply_exploration_rule(self, signals: dict[str, float]) -> bool:
        """Return ``True`` if signals indicate an exploration phase.

        The exploration rule fires when:
        - ``diversity_score`` exceeds the configured exploration diversity minimum, and
        - ``coverage_rate`` is below 0.5 (not yet nearing completion), and
        - ``stall_indicator`` is below the stall threshold.

        Parameters
        ----------
        signals:
            Normalised signal dictionary.
        """
        diversity = signals.get("diversity_score", 0.0)
        coverage = signals.get("coverage_rate", 0.0)
        stall = signals.get("stall_indicator", 0.0)
        div_min = self._thresholds["exploration_diversity_min"]
        stall_min = self._thresholds["stall_score_min"]
        return diversity >= div_min and coverage < 0.5 and stall < stall_min

    def apply_exploitation_rule(self, signals: dict[str, float]) -> bool:
        """Return ``True`` if signals indicate an exploitation phase.

        The exploitation rule fires when:
        - ``exploitation_ratio`` exceeds the configured minimum, and
        - ``diversity_score`` is below the exploration diversity threshold (focused),
        - ``stall_indicator`` is below the stall threshold.

        Parameters
        ----------
        signals:
            Normalised signal dictionary.
        """
        exploit = signals.get("exploitation_ratio", 0.0)
        diversity = signals.get("diversity_score", 0.5)
        stall = signals.get("stall_indicator", 0.0)
        exploit_min = self._thresholds["exploitation_ratio_min"]
        div_threshold = self._thresholds["exploration_diversity_min"]
        stall_min = self._thresholds["stall_score_min"]
        return exploit >= exploit_min and diversity < div_threshold and stall < stall_min

    def apply_stall_rule(self, signals: dict[str, float]) -> bool:
        """Return ``True`` if signals indicate a stalled phase.

        The stall rule fires when ``stall_indicator`` meets or exceeds the
        configured ``stall_score_min`` threshold.

        Parameters
        ----------
        signals:
            Normalised signal dictionary.
        """
        stall = signals.get("stall_indicator", 0.0)
        return stall >= self._thresholds["stall_score_min"]

    def apply_convergence_rule(self, signals: dict[str, float]) -> bool:
        """Return ``True`` if signals indicate a converged phase.

        The convergence rule fires when:
        - ``coverage_rate`` meets or exceeds the configured minimum, and
        - ``stall_indicator`` is *not* firing (coverage, not stagnation), and
        - ``diversity_score`` is relatively low (no unexplored clusters remain).

        Parameters
        ----------
        signals:
            Normalised signal dictionary.
        """
        coverage = signals.get("coverage_rate", 0.0)
        stall = signals.get("stall_indicator", 0.0)
        diversity = signals.get("diversity_score", 0.5)
        cov_min = self._thresholds["coverage_rate_min"]
        stall_min = self._thresholds["stall_score_min"]
        return coverage >= cov_min and stall < stall_min and diversity < 0.4

    def apply_divergence_rule(self, signals: dict[str, float]) -> bool:
        """Return ``True`` if signals indicate a diverged phase.

        The divergence rule fires when:
        - ``divergence_score`` meets or exceeds the configured maximum threshold, and
        - ``diversity_score`` is below the minimum (incoherent, not just broad).

        Parameters
        ----------
        signals:
            Normalised signal dictionary.
        """
        divergence = signals.get("divergence_score", 0.0)
        diversity = signals.get("diversity_score", 0.5)
        div_max = self._thresholds["divergence_score_max"]
        div_min = self._thresholds["diversity_min"]
        return divergence >= div_max and diversity < div_min

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def thresholds(self) -> dict[str, float]:
        """Return a copy of the current threshold configuration."""
        return dict(self._thresholds)


# ---------------------------------------------------------------------------
# 3. PhaseConfidenceEstimator
# ---------------------------------------------------------------------------


class PhaseConfidenceEstimator:
    """Estimates confidence in a phase classification.

    Given a signal dictionary and a candidate :class:`PhaseKind`, this class
    computes a normalised confidence score in ``[0.0, 1.0]``.  It also
    exposes methods for computing signal ambiguity and for ranking all phase
    candidates.

    The confidence model is a weighted combination of:
    - How strongly the signals align with the canonical profile of the given phase.
    - How weakly the signals align with competing phases (contrast).

    No external dependencies or learned parameters are required; the model
    is fully deterministic.
    """

    #: Canonical signal profiles for each phase kind.  Each profile lists
    #: ``(signal_name, ideal_value, weight)`` triples.
    _PROFILES: dict[PhaseKind, list[tuple[str, float, float]]] = {
        PhaseKind.EXPLORATION: [
            ("diversity_score", 0.7, 2.0),
            ("coverage_rate", 0.2, 1.5),
            ("stall_indicator", 0.0, 2.0),
            ("exploitation_ratio", 0.2, 1.0),
        ],
        PhaseKind.EXPLOITATION: [
            ("exploitation_ratio", 0.8, 2.0),
            ("diversity_score", 0.2, 1.5),
            ("stall_indicator", 0.0, 2.0),
            ("coverage_rate", 0.5, 1.0),
        ],
        PhaseKind.STALLED: [
            ("stall_indicator", 1.0, 3.0),
            ("coverage_rate", 0.3, 0.5),
            ("diversity_score", 0.3, 0.5),
        ],
        PhaseKind.CONVERGED: [
            ("coverage_rate", 1.0, 3.0),
            ("stall_indicator", 0.1, 1.0),
            ("diversity_score", 0.1, 1.0),
        ],
        PhaseKind.DIVERGED: [
            ("divergence_score", 1.0, 3.0),
            ("diversity_score", 0.1, 1.5),
            ("closure_variance", 0.8, 1.5),
        ],
        PhaseKind.TRANSITION: [
            ("closure_variance", 0.5, 2.0),
            ("cost_growth_rate", 0.5, 1.0),
            ("stall_indicator", 0.3, 1.0),
        ],
        PhaseKind.RECOVERY: [
            ("stall_indicator", 0.4, 2.0),
            ("diversity_score", 0.5, 1.5),
            ("coverage_rate", 0.3, 1.0),
        ],
    }

    def __init__(self) -> None:
        pass

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def estimate(self, signals: dict[str, float], phase: PhaseKind) -> float:
        """Estimate confidence that *signals* correspond to *phase*.

        The score is computed as the weighted cosine similarity between the
        signal vector and the canonical profile for *phase*, normalised by
        the total profile weight.

        Parameters
        ----------
        signals:
            Normalised signal dictionary.
        phase:
            The :class:`PhaseKind` being evaluated.

        Returns
        -------
        float
            Confidence in ``[0.0, 1.0]``.
        """
        profile = self._PROFILES.get(phase, [])
        if not profile:
            return 0.0
        total_weight = sum(w for _, _, w in profile)
        if total_weight == 0.0:
            return 0.0
        weighted_match = sum(
            w * (1.0 - abs(signals.get(sig, 0.0) - ideal))
            for sig, ideal, w in profile
        )
        raw = weighted_match / total_weight
        return _clamp(raw)

    def ambiguity_score(self, signals: dict[str, float]) -> float:
        """Compute the ambiguity of the signal dictionary.

        Ambiguity is high when two or more phases have similar confidence
        scores, making classification uncertain.  It is computed as the
        normalised standard deviation of confidence scores across all phases.

        Parameters
        ----------
        signals:
            Normalised signal dictionary.

        Returns
        -------
        float
            Ambiguity in ``[0.0, 1.0]`` where 0 = unambiguous, 1 = maximally
            ambiguous.
        """
        scores = [self.estimate(signals, phase) for phase in PhaseKind]
        if not scores:
            return 1.0
        std = _safe_std(scores)
        # Max possible std for values in [0,1] is 0.5
        return _clamp(1.0 - std / 0.5)

    def top_candidates(
        self, signals: dict[str, float], n: int = 3
    ) -> list[tuple[PhaseKind, float]]:
        """Return the *n* most-confident phase candidates in descending order.

        Parameters
        ----------
        signals:
            Normalised signal dictionary.
        n:
            Number of top candidates to return.  Clamped to the total number
            of :class:`PhaseKind` members.

        Returns
        -------
        list[tuple[PhaseKind, float]]
            List of ``(PhaseKind, confidence)`` pairs, sorted by descending
            confidence.
        """
        all_scores = [(phase, self.estimate(signals, phase)) for phase in PhaseKind]
        all_scores.sort(key=lambda x: x[1], reverse=True)
        return all_scores[: max(1, min(n, len(all_scores)))]


# ---------------------------------------------------------------------------
# 4. PhaseWindowAnalyzer
# ---------------------------------------------------------------------------


@dataclass
class _Snapshot:
    """Internal record for a single window entry."""

    signals: dict[str, float]
    timestamp: float
    phase: PhaseKind | None = None


class PhaseWindowAnalyzer:
    """Analyses a sliding window of recent frontier state snapshots.

    Rather than classifying a phase based on a single point-in-time measurement,
    :class:`PhaseWindowAnalyzer` accumulates observations over a configurable
    window and derives *stable* estimates of the current phase, its stability,
    and the direction of the trend.

    Parameters
    ----------
    window_size:
        Maximum number of snapshots retained.  Older snapshots are dropped
        when the window is full.
    """

    def __init__(self, window_size: int = 10) -> None:
        self._window_size = max(2, window_size)
        self._window: deque[_Snapshot] = deque(maxlen=self._window_size)

    # ------------------------------------------------------------------
    # Ingestion
    # ------------------------------------------------------------------

    def add_snapshot(
        self, signals: dict[str, float], timestamp: float | None = None
    ) -> None:
        """Append a new snapshot to the sliding window.

        Parameters
        ----------
        signals:
            Normalised signal dictionary from :class:`PhaseSignalExtractor`.
        timestamp:
            Unix timestamp; defaults to ``time.time()``.
        """
        ts = timestamp if timestamp is not None else time.time()
        self._window.append(_Snapshot(signals=dict(signals), timestamp=ts))

    # ------------------------------------------------------------------
    # Analysis
    # ------------------------------------------------------------------

    def dominant_phase(self, heuristics: PhaseHeuristics) -> PhaseKind:
        """Return the most frequently occurring phase in the current window.

        Each snapshot is independently classified using *heuristics*; the
        result is the majority-vote winner.

        Parameters
        ----------
        heuristics:
            :class:`PhaseHeuristics` instance used to classify each snapshot.

        Returns
        -------
        PhaseKind
            The plurality phase.  If the window is empty, defaults to
            :attr:`PhaseKind.EXPLORATION`.
        """
        if not self._window:
            return PhaseKind.EXPLORATION
        counts: dict[PhaseKind, int] = {}
        for snap in self._window:
            phase = heuristics.classify_signals(snap.signals)
            snap.phase = phase
            counts[phase] = counts.get(phase, 0) + 1
        return max(counts, key=lambda p: counts[p])

    def phase_stability(self) -> float:
        """Return a stability score in ``[0.0, 1.0]`` for the current window.

        Stability is defined as the fraction of snapshots that share the same
        (most common) phase classification.  A score of 1.0 means all snapshots
        agree; 0.0 means maximum disagreement.

        Returns
        -------
        float
        """
        if not self._window:
            return 0.0
        # Count phases that have been cached on snapshots, or default EXPLORATION
        phase_list = [snap.phase or PhaseKind.EXPLORATION for snap in self._window]
        if not phase_list:
            return 0.0
        most_common_count = max(phase_list.count(p) for p in set(phase_list))
        return _clamp(most_common_count / len(phase_list))

    def trend_direction(self) -> str:
        """Infer the trend direction from the coverage_rate signal over the window.

        The trend is determined by the sign of the linear regression slope of
        ``coverage_rate`` values in the window.

        Returns
        -------
        str
            One of ``'improving'``, ``'degrading'``, or ``'stable'``.
        """
        if len(self._window) < 2:
            return "stable"
        pairs = [
            (snap.timestamp, snap.signals.get("coverage_rate", 0.0))
            for snap in self._window
        ]
        slope = _linear_slope(pairs)
        if slope > 0.005:
            return "improving"
        if slope < -0.005:
            return "degrading"
        return "stable"

    def window_summary(self) -> dict[str, Any]:
        """Return a human-readable summary of the current window state.

        Returns
        -------
        dict[str, Any]
            Keys: ``window_size``, ``snapshot_count``, ``trend``,
            ``stability``, ``oldest_ts``, ``newest_ts``, ``mean_signals``.
        """
        if not self._window:
            return {
                "window_size": self._window_size,
                "snapshot_count": 0,
                "trend": "stable",
                "stability": 0.0,
                "oldest_ts": None,
                "newest_ts": None,
                "mean_signals": {},
            }
        signal_keys = set().union(*(s.signals.keys() for s in self._window))
        mean_signals: dict[str, float] = {}
        for key in signal_keys:
            vals = [s.signals.get(key, 0.0) for s in self._window]
            mean_signals[key] = _safe_mean(vals)
        return {
            "window_size": self._window_size,
            "snapshot_count": len(self._window),
            "trend": self.trend_direction(),
            "stability": self.phase_stability(),
            "oldest_ts": self._window[0].timestamp,
            "newest_ts": self._window[-1].timestamp,
            "mean_signals": mean_signals,
        }

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def snapshot_count(self) -> int:
        """Number of snapshots currently in the window."""
        return len(self._window)


# ---------------------------------------------------------------------------
# 5. PhaseClassifier
# ---------------------------------------------------------------------------


class PhaseClassifier:
    """High-level classifier that combines signal extraction, heuristics,
    window analysis, and confidence estimation.

    :class:`PhaseClassifier` is the primary entry point for classifying the
    phase of a live frontier.  It integrates:

    - :class:`PhaseSignalExtractor` for raw signal extraction.
    - :class:`PhaseHeuristics` for deterministic rule application.
    - :class:`PhaseWindowAnalyzer` for temporal smoothing.
    - :class:`PhaseConfidenceEstimator` for uncertainty quantification.

    Parameters
    ----------
    heuristics:
        Optional pre-configured :class:`PhaseHeuristics` instance.  If
        ``None``, a default instance is constructed.
    window_size:
        Window size passed to the internal :class:`PhaseWindowAnalyzer`.
    """

    def __init__(
        self,
        heuristics: PhaseHeuristics | None = None,
        window_size: int = 10,
    ) -> None:
        self._heuristics = heuristics or PhaseHeuristics()
        self._analyzer = PhaseWindowAnalyzer(window_size=window_size)
        self._confidence_estimator = PhaseConfidenceEstimator()
        self._last_phase: PhaseKind = PhaseKind.EXPLORATION
        self._observation_count: int = 0

    # ------------------------------------------------------------------
    # Classification
    # ------------------------------------------------------------------

    def classify(self, frontier: Any, history: Any | None = None) -> PhaseKind:
        """Classify the current phase of *frontier*.

        Extracts signals, records an observation in the window, and returns
        the dominant phase from the window's majority vote.

        Parameters
        ----------
        frontier:
            A :class:`Frontier` instance or dict-proxy.
        history:
            Optional :class:`PhaseHistory` or any compatible object.

        Returns
        -------
        PhaseKind
        """
        extractor = PhaseSignalExtractor(frontier, history)
        signals = extractor.extract_signals()
        self._analyzer.add_snapshot(signals)
        self._observation_count += 1
        phase = self._analyzer.dominant_phase(self._heuristics)
        self._last_phase = phase
        return phase

    def classify_with_confidence(self, frontier: Any) -> tuple[PhaseKind, float]:
        """Classify *frontier* and return a ``(phase, confidence)`` pair.

        Parameters
        ----------
        frontier:
            A :class:`Frontier` instance or dict-proxy.

        Returns
        -------
        tuple[PhaseKind, float]
            The classified phase and the confidence score in ``[0.0, 1.0]``.
        """
        extractor = PhaseSignalExtractor(frontier)
        signals = extractor.extract_signals()
        self._analyzer.add_snapshot(signals)
        self._observation_count += 1
        phase = self._analyzer.dominant_phase(self._heuristics)
        self._last_phase = phase
        confidence = self._confidence_estimator.estimate(signals, phase)
        return phase, confidence

    def record_observation(self, frontier: Any) -> None:
        """Record an observation without returning a classification result.

        Useful for feeding data into the window without triggering a full
        classification pass.

        Parameters
        ----------
        frontier:
            A :class:`Frontier` instance or dict-proxy.
        """
        extractor = PhaseSignalExtractor(frontier)
        signals = extractor.extract_signals()
        self._analyzer.add_snapshot(signals)
        self._observation_count += 1

    def phase_report(self) -> dict[str, Any]:
        """Return a comprehensive report of the classifier's current state.

        Returns
        -------
        dict[str, Any]
            Keys: ``last_phase``, ``observation_count``, ``window_summary``,
            ``top_candidates`` (from the window's mean signals).
        """
        summary = self._analyzer.window_summary()
        mean_signals = summary.get("mean_signals", {})
        top = self._confidence_estimator.top_candidates(mean_signals)
        return {
            "last_phase": self._last_phase.name,
            "observation_count": self._observation_count,
            "window_summary": summary,
            "top_candidates": [(p.name, round(s, 4)) for p, s in top],
        }


# ---------------------------------------------------------------------------
# 6. TransitionDetector
# ---------------------------------------------------------------------------


class TransitionDetector:
    """Detects when a phase transition is warranted.

    Computes a composite *transition score* from the current signal dictionary
    and compares it against a configurable *sensitivity* threshold.  When the
    score exceeds the threshold, a transition trigger is identified.

    Parameters
    ----------
    sensitivity:
        Threshold in ``[0.0, 1.0]``.  Higher values make the detector more
        conservative (fewer false positives); lower values make it more
        aggressive (catches transitions earlier).
    """

    def __init__(self, sensitivity: float = 0.7) -> None:
        self._sensitivity = _clamp(sensitivity)
        self._transition_log: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Detection
    # ------------------------------------------------------------------

    def check_transition(
        self,
        current_phase: PhaseKind,
        signals: dict[str, float],
        history: Any | None = None,
    ) -> tuple[bool, TransitionTrigger | None]:
        """Check whether signals justify a transition away from *current_phase*.

        Parameters
        ----------
        current_phase:
            The phase the system is currently in.
        signals:
            Normalised signal dictionary.
        history:
            Optional :class:`PhaseHistory` for contextual disambiguation.

        Returns
        -------
        tuple[bool, TransitionTrigger | None]
            ``(should_transition, trigger_or_None)``.
        """
        score = self.compute_transition_score(signals)
        if score < self._sensitivity:
            return False, None
        trigger = self.get_likely_trigger(signals)
        # Avoid trivial self-transitions: if heuristics suggest we're already
        # in the correct phase, don't trigger.
        heuristics = PhaseHeuristics()
        suggested = heuristics.classify_signals(signals)
        if suggested == current_phase:
            return False, None
        return True, trigger

    def compute_transition_score(self, signals: dict[str, float]) -> float:
        """Compute a composite transition score from *signals*.

        The score aggregates:
        - ``stall_indicator`` (weight 2)
        - ``divergence_score`` (weight 2)
        - ``closure_variance`` (weight 1)
        - ``cost_growth_rate`` (weight 1)

        A high score means the current state is far from its ideal profile,
        suggesting a transition is needed.

        Parameters
        ----------
        signals:
            Normalised signal dictionary.

        Returns
        -------
        float
            Score in ``[0.0, 1.0]``.
        """
        stall = signals.get("stall_indicator", 0.0)
        diverge = signals.get("divergence_score", 0.0)
        var = signals.get("closure_variance", 0.0)
        cost_growth = signals.get("cost_growth_rate", 0.0)
        weighted = 2 * stall + 2 * diverge + 1 * var + 1 * cost_growth
        max_possible = 6.0
        return _clamp(weighted / max_possible)

    def get_likely_trigger(self, signals: dict[str, float]) -> TransitionTrigger:
        """Identify the most likely :class:`TransitionTrigger` given *signals*.

        The trigger is selected by inspecting which signal component
        contributes most to the transition score.

        Parameters
        ----------
        signals:
            Normalised signal dictionary.

        Returns
        -------
        TransitionTrigger
        """
        stall = signals.get("stall_indicator", 0.0)
        diverge = signals.get("divergence_score", 0.0)
        coverage = signals.get("coverage_rate", 0.0)
        diversity = signals.get("diversity_score", 1.0)

        candidates: list[tuple[float, TransitionTrigger]] = [
            (stall, TransitionTrigger.STALL_DETECTED),
            (diverge, TransitionTrigger.STALL_DETECTED),
            (coverage, TransitionTrigger.COVERAGE_THRESHOLD),
            (1.0 - diversity, TransitionTrigger.DIVERSITY_DROP),
        ]
        # Override with DIVERSITY_DROP if diversity is critically low
        if diversity < 0.25:
            return TransitionTrigger.DIVERSITY_DROP
        best = max(candidates, key=lambda x: x[0])
        return best[1]

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------

    def transition_history(self) -> list[dict[str, Any]]:
        """Return the full log of recorded transitions.

        Returns
        -------
        list[dict[str, Any]]
            Each entry has keys: ``from_phase``, ``to_phase``, ``trigger``,
            ``timestamp``.
        """
        return list(self._transition_log)

    def record_transition(
        self,
        from_phase: PhaseKind,
        to_phase: PhaseKind,
        trigger: TransitionTrigger,
    ) -> None:
        """Append a transition event to the internal log.

        Parameters
        ----------
        from_phase:
            The phase being exited.
        to_phase:
            The phase being entered.
        trigger:
            The trigger that caused the transition.
        """
        self._transition_log.append(
            {
                "from_phase": from_phase.name,
                "to_phase": to_phase.name,
                "trigger": trigger.name,
                "timestamp": time.time(),
            }
        )

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def sensitivity(self) -> float:
        """The currently configured sensitivity threshold."""
        return self._sensitivity

    @sensitivity.setter
    def sensitivity(self, value: float) -> None:
        """Update the sensitivity threshold (clamped to ``[0.0, 1.0]``)."""
        self._sensitivity = _clamp(value)


# ---------------------------------------------------------------------------
# 7. PhaseChangeNotifier
# ---------------------------------------------------------------------------


class PhaseChangeNotifier:
    """Publish-subscribe bus for phase change notifications.

    Downstream components (loggers, dashboards, recovery schedulers) can
    subscribe to phase change events and be notified whenever a phase
    transition is committed.

    Each subscriber is a callable with the signature::

        callback(old_phase: PhaseKind, new_phase: PhaseKind,
                 trigger: TransitionTrigger) -> None

    Subscriptions are identified by opaque UUID strings returned from
    :meth:`subscribe`.  They can be cancelled with :meth:`unsubscribe`.
    """

    def __init__(self) -> None:
        self._subscribers: dict[
            str, Callable[[PhaseKind, PhaseKind, TransitionTrigger], None]
        ] = {}
        self._notification_log: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Subscription management
    # ------------------------------------------------------------------

    def subscribe(
        self,
        callback: Callable[[PhaseKind, PhaseKind, TransitionTrigger], None],
    ) -> str:
        """Register a callback for phase change events.

        Parameters
        ----------
        callback:
            Callable invoked whenever a phase transition is published.  Must
            accept ``(old_phase, new_phase, trigger)`` positional arguments.

        Returns
        -------
        str
            An opaque subscription ID.  Pass this to :meth:`unsubscribe` to
            cancel the subscription.
        """
        subscription_id = str(uuid.uuid4())
        self._subscribers[subscription_id] = callback
        return subscription_id

    def unsubscribe(self, subscription_id: str) -> bool:
        """Cancel a subscription by its ID.

        Parameters
        ----------
        subscription_id:
            The ID returned by a previous call to :meth:`subscribe`.

        Returns
        -------
        bool
            ``True`` if the subscription was found and removed, ``False``
            otherwise.
        """
        if subscription_id in self._subscribers:
            del self._subscribers[subscription_id]
            return True
        return False

    # ------------------------------------------------------------------
    # Publishing
    # ------------------------------------------------------------------

    def notify(
        self,
        old_phase: PhaseKind,
        new_phase: PhaseKind,
        trigger: TransitionTrigger,
    ) -> None:
        """Publish a phase change event to all registered subscribers.

        Each subscriber is called synchronously.  Exceptions raised by
        individual callbacks are caught and stored in the notification log
        without aborting delivery to remaining subscribers.

        Parameters
        ----------
        old_phase:
            The phase that was exited.
        new_phase:
            The phase that was entered.
        trigger:
            The :class:`TransitionTrigger` that caused the transition.
        """
        event: dict[str, Any] = {
            "old_phase": old_phase.name,
            "new_phase": new_phase.name,
            "trigger": trigger.name,
            "timestamp": time.time(),
            "errors": [],
        }
        for sub_id, callback in list(self._subscribers.items()):
            try:
                callback(old_phase, new_phase, trigger)
            except Exception as exc:  # noqa: BLE001
                event["errors"].append({"subscription_id": sub_id, "error": str(exc)})
        self._notification_log.append(event)

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def subscriber_count(self) -> int:
        """Return the current number of active subscribers."""
        return len(self._subscribers)

    def notification_history(self) -> list[dict[str, Any]]:
        """Return a copy of the notification event log.

        Returns
        -------
        list[dict[str, Any]]
            Each entry has keys: ``old_phase``, ``new_phase``, ``trigger``,
            ``timestamp``, ``errors``.
        """
        return list(self._notification_log)
