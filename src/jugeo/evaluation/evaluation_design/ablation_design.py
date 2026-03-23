"""Ablation design for the evaluation_design package.

Theory reference: theory2.tex Ch63.
copilot: shared-core marker

This module implements the ablation study pipeline used by JuGeo to isolate
the contribution of individual system components.  Each ablation experiment
removes one or more components from a baseline configuration, runs the system,
and measures the resulting change in performance.

The main classes are:

* ``AblationPlanner``  – generates ``AblationDesign`` objects.
* ``AblationExecutor`` – executes designs against a callable system.
* ``AblationAnalyzer`` – ranks and interprets ``AblationResult`` objects.
* ``AblationDesignRunner`` – end-to-end orchestrator.
"""

from __future__ import annotations

import json
import math
import random
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from .models import (
    AblationDesign,
    AblationKind,
    AblationResult,
    EvaluationResult,
)

# ---------------------------------------------------------------------------
__all__ = [
    "AblationPlanner",
    "AblationExecutor",
    "AblationAnalyzer",
    "AblationDesignRunner",
    "design_ablation_study",
    "run_ablation",
]

# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _utcnow() -> float:
    """Return the current UTC time as a POSIX timestamp (float seconds).

    Returns:
        float: Current UTC epoch time in seconds.
    """
    return time.time()


def _uid() -> str:
    """Generate a fresh, collision-resistant UUID4 string.

    Returns:
        str: A 36-character hyphenated UUID4 string.
    """
    return str(uuid.uuid4())


def _clamp(v: float, lo: float, hi: float) -> float:
    """Clamp *v* to the closed interval [lo, hi].

    Args:
        v:  The value to clamp.
        lo: Lower bound (inclusive).
        hi: Upper bound (inclusive).

    Returns:
        float: The clamped value such that lo <= result <= hi.
    """
    return max(lo, min(hi, v))


# ---------------------------------------------------------------------------
# Internal statistical helpers
# ---------------------------------------------------------------------------


def _mean(values: list[float]) -> float:
    """Compute the arithmetic mean of *values*.

    Args:
        values: Non-empty list of floats.

    Returns:
        float: Arithmetic mean, or 0.0 for an empty list.
    """
    if not values:
        return 0.0
    return sum(values) / len(values)


def _variance(values: list[float]) -> float:
    """Compute the sample variance of *values* (Bessel-corrected).

    Args:
        values: List of floats with at least two elements.

    Returns:
        float: Sample variance, or 0.0 if fewer than 2 values.
    """
    n = len(values)
    if n < 2:
        return 0.0
    mu = _mean(values)
    return sum((x - mu) ** 2 for x in values) / (n - 1)


def _welch_t_statistic(
    group_a: list[float], group_b: list[float]
) -> float:
    """Compute the Welch *t*-statistic for two independent groups.

    Uses Welch's unequal-variance formula:
    ``t = (mean_a - mean_b) / sqrt(var_a/n_a + var_b/n_b)``

    Args:
        group_a: Scores for the first group (e.g. baseline).
        group_b: Scores for the second group (e.g. ablated).

    Returns:
        float: The t-statistic, or 0.0 if the denominator is zero.
    """
    n_a, n_b = len(group_a), len(group_b)
    if n_a == 0 or n_b == 0:
        return 0.0
    var_a = _variance(group_a)
    var_b = _variance(group_b)
    denom = math.sqrt(var_a / n_a + var_b / n_b)
    if denom == 0.0:
        return 0.0
    return (_mean(group_a) - _mean(group_b)) / denom


def _approx_p_value(t: float, df: float) -> float:
    """Approximate the two-tailed *p*-value from a *t*-statistic and *df*.

    Uses a polynomial approximation of the incomplete beta function
    suitable for moderate degrees of freedom (df >= 2).  This is an
    approximation only; use ``scipy.stats.ttest_ind`` in production.

    Args:
        t:  The t-statistic (sign does not matter for two-tailed test).
        df: Degrees of freedom (Welch approximation).

    Returns:
        float: Approximate two-tailed p-value in (0, 1].
    """
    if df <= 0:
        return 1.0
    abs_t = abs(t)
    # Use an exponential approximation: p ≈ 2 * exp(-(abs_t^2) / (2 + df/50))
    # This is deliberately conservative (over-estimates p) for safety.
    scale = 2.0 + df / 50.0
    p_approx = 2.0 * math.exp(-(abs_t ** 2) / scale)
    return _clamp(p_approx, 1e-6, 1.0)


# ---------------------------------------------------------------------------
# AblationPlanner
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class AblationPlanner:
    """Plan an ablation study over a set of system components.

    ``AblationPlanner`` generates a list of ``AblationDesign`` objects each
    describing an experiment where one or more components are removed from the
    baseline configuration.  Three planning strategies are supported:

    * ``"one_at_a_time"`` – remove each component individually.
    * ``"pairwise"``      – remove all pairs of components simultaneously.
    * ``"random_subset"`` – sample *n_samples* random subsets of size *k*.

    Attributes:
        components:      Ordered list of component names to ablate.
        baseline_config: Dict of baseline configuration values passed to the
                         system function unchanged.
        strategy:        One of ``"one_at_a_time"``, ``"pairwise"``,
                         ``"random_subset"``.
        n_samples:       Number of random subsets to draw (used only by the
                         ``"random_subset"`` strategy).
        random_seed:     Seed for reproducible random sampling.
        metadata:        Arbitrary JSON-serialisable key-value pairs.
    """

    components: list[str]
    baseline_config: dict = field(default_factory=dict)
    strategy: str = "one_at_a_time"
    n_samples: int = 10
    random_seed: int = 42
    metadata: dict = field(default_factory=dict)

    # ---------------------------------------------------------------------------
    @classmethod
    def create(
        cls,
        components: list[str],
        baseline_config: dict | None = None,
        strategy: str = "one_at_a_time",
        n_samples: int = 10,
        random_seed: int = 42,
        metadata: dict | None = None,
    ) -> AblationPlanner:
        """Factory method: create an ``AblationPlanner`` with sane defaults.

        Args:
            components:      Names of the components to ablate.
            baseline_config: Baseline configuration dict.  Defaults to ``{}``.
            strategy:        Ablation planning strategy.
            n_samples:       Number of random-subset samples.
            random_seed:     Random seed for reproducibility.
            metadata:        Optional metadata dict.

        Returns:
            AblationPlanner: A fully initialised planner.
        """
        return cls(
            components=list(components),
            baseline_config=dict(baseline_config) if baseline_config else {},
            strategy=strategy,
            n_samples=max(1, n_samples),
            random_seed=random_seed,
            metadata=dict(metadata) if metadata else {},
        )

    # ---------------------------------------------------------------------------
    def plan(self) -> list[AblationDesign]:
        """Dispatch to the configured strategy and return ablation designs.

        Returns:
            list[AblationDesign]: One ``AblationDesign`` per ablation
            experiment.

        Raises:
            ValueError: If ``self.strategy`` is not a recognised value.
        """
        if self.strategy == "one_at_a_time":
            return self.plan_one_at_a_time()
        if self.strategy == "pairwise":
            return self.plan_pairwise()
        if self.strategy == "random_subset":
            return self.plan_random_subset()
        raise ValueError(
            f"Unknown ablation strategy '{self.strategy}'. "
            "Expected one of: 'one_at_a_time', 'pairwise', 'random_subset'."
        )

    # ---------------------------------------------------------------------------
    def plan_one_at_a_time(self) -> list[AblationDesign]:
        """Create one ``AblationDesign`` per component.

        Each design specifies the removal of exactly one component from the
        baseline configuration, enabling isolated measurement of each
        component's contribution.

        Returns:
            list[AblationDesign]: One design per component in
            ``self.components``.
        """
        designs: list[AblationDesign] = []
        for component in self.components:
            design = AblationDesign(
                design_id=_uid(),
                components_to_ablate=[component],
                baseline_config=dict(self.baseline_config),
                metrics=list(self.metadata.get("metrics", [])),
                n_repeats=int(self.metadata.get("n_repeats", 1)),
                random_seed=self.random_seed,
                metadata={
                    "strategy": "one_at_a_time",
                    "ablated_component": component,
                    "planner_id": self.metadata.get("planner_id", _uid()),
                },
            )
            designs.append(design)
        return designs

    # ---------------------------------------------------------------------------
    def plan_pairwise(self) -> list[AblationDesign]:
        """Create one ``AblationDesign`` for every unique pair of components.

        Generates C(n, 2) designs where *n* is the number of components.  Each
        design removes exactly two components simultaneously.

        Returns:
            list[AblationDesign]: One design per component pair.
        """
        designs: list[AblationDesign] = []
        n = len(self.components)
        for i in range(n):
            for j in range(i + 1, n):
                pair = [self.components[i], self.components[j]]
                design = AblationDesign(
                    design_id=_uid(),
                    components_to_ablate=pair,
                    baseline_config=dict(self.baseline_config),
                    metrics=list(self.metadata.get("metrics", [])),
                    n_repeats=int(self.metadata.get("n_repeats", 1)),
                    random_seed=self.random_seed,
                    metadata={
                        "strategy": "pairwise",
                        "ablated_pair": pair,
                        "planner_id": self.metadata.get("planner_id", _uid()),
                    },
                )
                designs.append(design)
        return designs

    # ---------------------------------------------------------------------------
    def plan_random_subset(self, k: int = 2) -> list[AblationDesign]:
        """Create ``n_samples`` ablation designs with random *k*-subsets.

        Each design removes a randomly selected subset of *k* components.
        The same subset may appear more than once if *n_samples* exceeds the
        number of unique *k*-subsets.

        Args:
            k: Number of components to remove per design.  Clamped to
               ``[1, len(self.components)]``.

        Returns:
            list[AblationDesign]: *n_samples* randomly generated designs.
        """
        rng = random.Random(self.random_seed)
        k = _clamp(k, 1, max(1, len(self.components)))
        designs: list[AblationDesign] = []
        for sample_idx in range(self.n_samples):
            subset = rng.sample(self.components, int(k))
            design = AblationDesign(
                design_id=_uid(),
                components_to_ablate=subset,
                baseline_config=dict(self.baseline_config),
                metrics=list(self.metadata.get("metrics", [])),
                n_repeats=int(self.metadata.get("n_repeats", 1)),
                random_seed=self.random_seed + sample_idx,
                metadata={
                    "strategy": "random_subset",
                    "k": k,
                    "sample_index": sample_idx,
                    "planner_id": self.metadata.get("planner_id", _uid()),
                },
            )
            designs.append(design)
        return designs

    # ---------------------------------------------------------------------------
    def estimate_cost(self) -> float:
        """Estimate the relative cost (number of system calls) for the plan.

        The cost is defined as the total number of experiments that would be
        run under the current strategy, multiplied by ``n_repeats`` (taken
        from metadata if present, defaulting to 1).

        Returns:
            float: Estimated number of system function invocations.
        """
        n_repeats = float(self.metadata.get("n_repeats", 1))
        n = len(self.components)
        if self.strategy == "one_at_a_time":
            n_experiments = float(n)
        elif self.strategy == "pairwise":
            # C(n, 2) pairs.
            n_experiments = float(n * (n - 1) // 2) if n >= 2 else 0.0
        elif self.strategy == "random_subset":
            n_experiments = float(self.n_samples)
        else:
            n_experiments = 0.0
        # Add 1 for the baseline run itself.
        return (n_experiments + 1.0) * n_repeats

    # ---------------------------------------------------------------------------
    def validate_plan(self, designs: list[AblationDesign]) -> list[str]:
        """Validate *designs* and return a list of error strings.

        Args:
            designs: List of ``AblationDesign`` objects to validate.

        Returns:
            list[str]: A (possibly empty) list of human-readable error
            descriptions.
        """
        errors: list[str] = []
        seen_ids: set[str] = set()
        for i, design in enumerate(designs):
            if design.design_id in seen_ids:
                errors.append(
                    f"designs[{i}]: duplicate design_id '{design.design_id}'"
                )
            seen_ids.add(design.design_id)

            # Every ablated component should be in the known components list.
            for comp in design.components_to_ablate:
                if comp not in self.components:
                    errors.append(
                        f"designs[{i}]: unknown component '{comp}' "
                        f"(not in self.components)"
                    )

            # Validate the AblationDesign itself.
            design_errors = design.validate()
            for de in design_errors:
                errors.append(f"designs[{i}] ({design.design_id}): {de}")

        return errors


# ---------------------------------------------------------------------------
# AblationExecutor
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class AblationExecutor:
    """Execute ablation designs by calling a system function.

    ``AblationExecutor`` calls a user-supplied *system_fn* with modified
    configurations, computes delta scores relative to a baseline, and
    constructs ``AblationResult`` objects.

    The *system_fn* is a callable with signature
    ``system_fn(config: dict) -> dict`` where the returned dict is expected
    to contain a ``"score"`` key (float) and optionally per-metric scores
    under a ``"metrics"`` sub-dict.

    Attributes:
        baseline_score: Pre-computed baseline score.  Set by
                        ``compute_baseline()`` or provided directly.
        metric_names:   Names of metric keys to extract from system output.
        metadata:       Arbitrary JSON-serialisable key-value pairs.
    """

    baseline_score: float = 0.0
    metric_names: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    # ---------------------------------------------------------------------------
    @classmethod
    def create(
        cls,
        baseline_score: float = 0.0,
        metric_names: list[str] | None = None,
        metadata: dict | None = None,
    ) -> AblationExecutor:
        """Factory method: create an ``AblationExecutor``.

        Args:
            baseline_score: Pre-computed baseline score.
            metric_names:   Names of metrics to track.
            metadata:       Optional metadata dict.

        Returns:
            AblationExecutor: A fully initialised executor.
        """
        return cls(
            baseline_score=baseline_score,
            metric_names=list(metric_names) if metric_names else [],
            metadata=dict(metadata) if metadata else {},
        )

    # ---------------------------------------------------------------------------
    def execute(
        self, design: AblationDesign, system_fn: object
    ) -> AblationResult:
        """Execute a single ablation design and return the result.

        Builds a modified configuration by removing all components listed in
        ``design.components_to_ablate`` from ``design.baseline_config``, calls
        *system_fn*, extracts the score, and computes the delta vs. the stored
        baseline.

        Args:
            design:    The ``AblationDesign`` specifying what to ablate.
            system_fn: Callable with signature ``(config: dict) -> dict``.
                       The returned dict must contain a ``"score"`` key.

        Returns:
            AblationResult: Immutable result record for this ablation
            experiment.
        """
        fn = system_fn  # type: ignore[assignment]
        ablated_score = self.run_with_ablation(
            fn,
            design.baseline_config,
            design.components_to_ablate[0]
            if len(design.components_to_ablate) == 1
            else ",".join(design.components_to_ablate),
        )
        delta = ablated_score - self.baseline_score
        # Determine AblationKind heuristically from component name.
        kind = AblationKind.COMPONENT
        comp_name = (
            design.components_to_ablate[0]
            if design.components_to_ablate
            else "unknown"
        )
        if "feature" in comp_name.lower():
            kind = AblationKind.FEATURE
        elif "subsystem" in comp_name.lower():
            kind = AblationKind.SUBSYSTEM
        elif "pathway" in comp_name.lower():
            kind = AblationKind.PATHWAY

        significant = abs(delta) > 0.05  # Simple significance heuristic.
        result = AblationResult(
            ablation_id=_uid(),
            ablation_kind=kind,
            removed_component=",".join(design.components_to_ablate),
            baseline_score=self.baseline_score,
            ablated_score=ablated_score,
            delta_score=delta,
            p_value=1.0,  # Single-run p-value is undefined; set to 1.
            significant=significant,
            metadata={
                "design_id": design.design_id,
                "strategy": design.metadata.get("strategy", "unknown"),
                "executed_at": _utcnow(),
            },
        )
        return result

    # ---------------------------------------------------------------------------
    def execute_all(
        self, designs: list[AblationDesign], system_fn: object
    ) -> list[AblationResult]:
        """Execute every design in *designs* and return results.

        Args:
            designs:   List of ``AblationDesign`` objects to execute.
            system_fn: Callable as described in ``execute()``.

        Returns:
            list[AblationResult]: One result per design, in order.
        """
        return [self.execute(d, system_fn) for d in designs]

    # ---------------------------------------------------------------------------
    def compute_baseline(
        self, system_fn: object, config: dict
    ) -> float:
        """Invoke *system_fn* with *config* and extract the baseline score.

        Also stores the result in ``self.baseline_score`` for subsequent use.

        Args:
            system_fn: Callable with signature ``(config: dict) -> dict``.
            config:    The unmodified baseline configuration dict.

        Returns:
            float: The baseline score extracted from the system output.
        """
        fn = system_fn  # type: ignore[assignment]
        output = fn(config)  # type: ignore[operator]
        score = float(output.get("score", 0.0))
        self.baseline_score = _clamp(score, 0.0, 1.0)
        return self.baseline_score

    # ---------------------------------------------------------------------------
    def run_with_ablation(
        self, system_fn: object, config: dict, removed: str
    ) -> float:
        """Run *system_fn* with *removed* components deleted from *config*.

        Constructs an ablated copy of *config* by deleting all keys matching
        any of the comma-separated component names in *removed*, then invokes
        *system_fn* and returns the resulting score.

        Args:
            system_fn: Callable with signature ``(config: dict) -> dict``.
            config:    The original configuration dict (not mutated).
            removed:   Comma-separated component name(s) to remove.

        Returns:
            float: The score returned by *system_fn* on the ablated config.
        """
        ablated_config = dict(config)
        for component in removed.split(","):
            component = component.strip()
            ablated_config.pop(component, None)
            # Also remove any config keys that are prefixed with the component.
            ablated_config = {
                k: v
                for k, v in ablated_config.items()
                if not k.startswith(component + "_")
            }

        fn = system_fn  # type: ignore[assignment]
        output = fn(ablated_config)  # type: ignore[operator]
        score = float(output.get("score", 0.0))
        return _clamp(score, 0.0, 1.0)

    # ---------------------------------------------------------------------------
    def compute_p_value(
        self,
        baseline_scores: list[float],
        ablated_scores: list[float],
    ) -> float:
        """Compute an approximate two-tailed *p*-value for the score difference.

        Uses Welch's *t*-test approximation.  This is intentionally simplified
        for embedded use; for rigorous statistics use ``scipy.stats.ttest_ind``.

        Args:
            baseline_scores: Repeated baseline scores from multiple runs.
            ablated_scores:  Repeated ablated scores from multiple runs.

        Returns:
            float: Approximate two-tailed p-value in (0, 1].  Returns 1.0 if
            either input list has fewer than 2 elements.
        """
        if len(baseline_scores) < 2 or len(ablated_scores) < 2:
            return 1.0
        t = _welch_t_statistic(baseline_scores, ablated_scores)
        n_a, n_b = len(baseline_scores), len(ablated_scores)
        var_a = _variance(baseline_scores)
        var_b = _variance(ablated_scores)
        # Welch–Satterthwaite degrees of freedom.
        num = (var_a / n_a + var_b / n_b) ** 2
        denom = (
            (var_a / n_a) ** 2 / (n_a - 1)
            + (var_b / n_b) ** 2 / (n_b - 1)
        )
        df = num / denom if denom > 0 else 1.0
        return _approx_p_value(t, df)


# ---------------------------------------------------------------------------
# AblationAnalyzer
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class AblationAnalyzer:
    """Analyse and interpret a collection of ``AblationResult`` objects.

    ``AblationAnalyzer`` provides methods to rank components by their
    importance (absolute delta score), identify critical components (those
    whose removal causes a statistically significant performance drop), and
    generate human-readable reports.

    Attributes:
        results:                List of ``AblationResult`` objects to analyse.
        significance_threshold: Maximum *p*-value for a result to be
                                considered statistically significant.
        metadata:               Arbitrary JSON-serialisable key-value pairs.
    """

    results: list[AblationResult] = field(default_factory=list)
    significance_threshold: float = 0.05
    metadata: dict = field(default_factory=dict)

    # ---------------------------------------------------------------------------
    @classmethod
    def create(
        cls,
        results: list[AblationResult] | None = None,
        significance_threshold: float = 0.05,
        metadata: dict | None = None,
    ) -> AblationAnalyzer:
        """Factory method: create an ``AblationAnalyzer``.

        Args:
            results:                Initial list of results to analyse.
            significance_threshold: P-value cutoff for significance.
            metadata:               Optional metadata dict.

        Returns:
            AblationAnalyzer: A fully initialised analyser.
        """
        return cls(
            results=list(results) if results else [],
            significance_threshold=_clamp(significance_threshold, 0.0, 1.0),
            metadata=dict(metadata) if metadata else {},
        )

    # ---------------------------------------------------------------------------
    def rank_by_importance(self) -> list[AblationResult]:
        """Sort ``self.results`` by absolute delta score (descending).

        Higher absolute delta means the removed component had more impact on
        performance.

        Returns:
            list[AblationResult]: A new list sorted by ``abs(delta_score)``
            descending.
        """
        return sorted(
            self.results,
            key=lambda r: abs(r.delta_score),
            reverse=True,
        )

    # ---------------------------------------------------------------------------
    def critical_components(self) -> list[str]:
        """Return names of components whose removal is statistically significant.

        A component is considered *critical* if ``result.is_critical()``
        returns ``True`` (i.e. the p-value is below ``significance_threshold``
        and the delta is negative – removal hurt performance).

        Returns:
            list[str]: Sorted list of critical component names.
        """
        critical: list[str] = []
        for result in self.results:
            # Use the result's own is_critical() method from the model.
            try:
                is_crit = result.is_critical()
            except AttributeError:
                # Fallback if the model doesn't implement is_critical().
                is_crit = (
                    result.significant
                    and result.delta_score < 0.0
                    and result.p_value < self.significance_threshold
                )
            if is_crit:
                critical.append(result.removed_component)
        return sorted(set(critical))

    # ---------------------------------------------------------------------------
    def feature_importance_scores(self) -> dict[str, float]:
        """Compute a feature importance score for each component.

        The importance score is the mean absolute delta across all results that
        mention the component.  Components with higher importance scores
        contribute more to overall performance.

        Returns:
            dict[str, float]: Mapping from component name to mean absolute
            delta score.
        """
        component_deltas: dict[str, list[float]] = {}
        for result in self.results:
            comp = result.removed_component
            if comp not in component_deltas:
                component_deltas[comp] = []
            component_deltas[comp].append(abs(result.delta_score))
        return {
            comp: _mean(deltas)
            for comp, deltas in component_deltas.items()
        }

    # ---------------------------------------------------------------------------
    def generate_report(self) -> dict:
        """Generate a full analysis report as a JSON-serialisable dictionary.

        The report includes:
        * A ranked list of components by importance.
        * The set of critical components.
        * Aggregate statistics (mean delta, max delta, p-value distribution).
        * Raw result summaries.

        Returns:
            dict: Report dictionary with keys ``generated_at``,
            ``n_results``, ``critical_components``, ``importance_scores``,
            ``ranked_results``, ``aggregate_stats``.
        """
        ranked = self.rank_by_importance()
        importance = self.feature_importance_scores()
        critical = self.critical_components()
        deltas = [r.delta_score for r in self.results]
        p_values = [r.p_value for r in self.results]
        return {
            "generated_at": _utcnow(),
            "n_results": len(self.results),
            "critical_components": critical,
            "importance_scores": importance,
            "ranked_results": [
                {
                    "ablation_id": r.ablation_id,
                    "removed_component": r.removed_component,
                    "baseline_score": r.baseline_score,
                    "ablated_score": r.ablated_score,
                    "delta_score": r.delta_score,
                    "p_value": r.p_value,
                    "significant": r.significant,
                }
                for r in ranked
            ],
            "aggregate_stats": {
                "mean_delta": _mean(deltas) if deltas else 0.0,
                "max_abs_delta": max((abs(d) for d in deltas), default=0.0),
                "min_delta": min(deltas, default=0.0),
                "max_delta": max(deltas, default=0.0),
                "mean_p_value": _mean(p_values) if p_values else 1.0,
                "n_significant": sum(1 for r in self.results if r.significant),
            },
        }

    # ---------------------------------------------------------------------------
    def plot_importance_data(self) -> list[dict]:
        """Return data suitable for plotting a component importance bar chart.

        Each entry in the returned list represents one ablated component and
        contains the component name, its importance score, and whether the
        effect was statistically significant.

        Returns:
            list[dict]: Sorted (desc importance) list of dicts with keys
            ``component``, ``importance``, ``significant``, ``delta``.
        """
        importance = self.feature_importance_scores()
        significance_map: dict[str, bool] = {}
        delta_map: dict[str, float] = {}
        for result in self.results:
            comp = result.removed_component
            significance_map[comp] = significance_map.get(
                comp, False
            ) or result.significant
            # Use the result with the largest abs delta as representative.
            if abs(result.delta_score) > abs(delta_map.get(comp, 0.0)):
                delta_map[comp] = result.delta_score

        plot_data = [
            {
                "component": comp,
                "importance": imp,
                "significant": significance_map.get(comp, False),
                "delta": delta_map.get(comp, 0.0),
            }
            for comp, imp in importance.items()
        ]
        return sorted(plot_data, key=lambda x: x["importance"], reverse=True)

    # ---------------------------------------------------------------------------
    def summarize(self) -> str:
        """Return a compact multi-line summary of the ablation analysis.

        Returns:
            str: Human-readable summary covering n_results, critical
            components, and the top-3 most important components.
        """
        n = len(self.results)
        critical = self.critical_components()
        ranked = self.rank_by_importance()[:3]
        lines = [
            f"AblationAnalysis: {n} result(s)",
            f"  Critical components ({len(critical)}): "
            + (", ".join(critical) if critical else "none"),
            "  Top components by importance:",
        ]
        for r in ranked:
            lines.append(
                f"    {r.removed_component}: Δ={r.delta_score:+.4f}, "
                f"p={r.p_value:.4f}"
            )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# AblationDesignRunner
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class AblationDesignRunner:
    """End-to-end orchestrator for ablation studies.

    ``AblationDesignRunner`` wires together ``AblationPlanner``,
    ``AblationExecutor``, and ``AblationAnalyzer`` to provide a single-call
    interface for running complete ablation studies.

    Attributes:
        planner:  ``AblationPlanner`` for generating experiment designs.
        executor: ``AblationExecutor`` for running experiments.
        analyzer: ``AblationAnalyzer`` for interpreting results.
        metadata: Arbitrary JSON-serialisable key-value pairs.
    """

    planner: AblationPlanner
    executor: AblationExecutor
    analyzer: AblationAnalyzer
    metadata: dict = field(default_factory=dict)

    # ---------------------------------------------------------------------------
    @classmethod
    def create(
        cls,
        components: list[str],
        baseline_config: dict | None = None,
        strategy: str = "one_at_a_time",
        significance_threshold: float = 0.05,
        metadata: dict | None = None,
    ) -> AblationDesignRunner:
        """Factory method: create a fully wired ``AblationDesignRunner``.

        Args:
            components:             Names of the components to ablate.
            baseline_config:        Baseline configuration dict.
            strategy:               Ablation planning strategy.
            significance_threshold: P-value cutoff for significance.
            metadata:               Optional metadata dict.

        Returns:
            AblationDesignRunner: A ready-to-use runner.
        """
        planner = AblationPlanner.create(
            components=components,
            baseline_config=baseline_config,
            strategy=strategy,
            metadata=dict(metadata) if metadata else {},
        )
        executor = AblationExecutor.create()
        analyzer = AblationAnalyzer.create(
            significance_threshold=significance_threshold
        )
        return cls(
            planner=planner,
            executor=executor,
            analyzer=analyzer,
            metadata=dict(metadata) if metadata else {},
        )

    # ---------------------------------------------------------------------------
    def run(self, system_fn: object) -> list[AblationResult]:
        """Plan, execute, and collect all ablation results.

        Computes the baseline score first, then runs all planned ablation
        experiments.  The analyzer is populated with the results.

        Args:
            system_fn: Callable ``(config: dict) -> dict`` representing the
                       system under evaluation.

        Returns:
            list[AblationResult]: All ablation results in design order.
        """
        # Step 1: compute baseline.
        self.executor.compute_baseline(
            system_fn, self.planner.baseline_config
        )
        # Step 2: generate designs.
        designs = self.planner.plan()
        # Step 3: execute all designs.
        results = self.executor.execute_all(designs, system_fn)
        # Step 4: populate analyzer.
        self.analyzer.results = results
        return results

    # ---------------------------------------------------------------------------
    def run_with_analysis(
        self, system_fn: object
    ) -> tuple[list[AblationResult], dict]:
        """Run the ablation study and immediately generate an analysis report.

        Args:
            system_fn: Callable as described in ``run()``.

        Returns:
            tuple[list[AblationResult], dict]: A pair of (results, report).
        """
        results = self.run(system_fn)
        report = self.analyzer.generate_report()
        return results, report

    # ---------------------------------------------------------------------------
    def generate_full_report(self, system_fn: object) -> dict:
        """Run the study and return a complete report including plot data.

        Args:
            system_fn: Callable as described in ``run()``.

        Returns:
            dict: Full report including ``"analysis"``, ``"plot_data"``,
            ``"summary"``, ``"baseline_score"``, and ``"planner_config"``.
        """
        results, analysis_report = self.run_with_analysis(system_fn)
        return {
            "baseline_score": self.executor.baseline_score,
            "analysis": analysis_report,
            "plot_data": self.analyzer.plot_importance_data(),
            "summary": self.analyzer.summarize(),
            "planner_config": {
                "components": self.planner.components,
                "strategy": self.planner.strategy,
                "n_samples": self.planner.n_samples,
                "random_seed": self.planner.random_seed,
            },
            "generated_at": _utcnow(),
        }

    # ---------------------------------------------------------------------------
    def save_report(self, report: dict, path: str) -> None:
        """Persist *report* to a JSON file at *path*.

        Args:
            report: Report dictionary (as returned by ``generate_full_report``).
            path:   Destination file path.  Parent directories must exist.

        Returns:
            None
        """
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2)


# ---------------------------------------------------------------------------
# Free functions
# ---------------------------------------------------------------------------


def design_ablation_study(
    components: list[str],
    baseline_config: dict,
    strategy: str = "one_at_a_time",
) -> list[AblationDesign]:
    """Design an ablation study returning a list of ``AblationDesign`` objects.

    This is the primary functional entry point for ablation study design.  It
    constructs an ``AblationPlanner`` with the supplied arguments and returns
    the generated list of designs without executing any experiments.

    Args:
        components:      Names of the components to ablate.
        baseline_config: The unmodified baseline configuration dict.
        strategy:        One of ``"one_at_a_time"``, ``"pairwise"``, or
                         ``"random_subset"``.  Defaults to
                         ``"one_at_a_time"``.

    Returns:
        list[AblationDesign]: One ``AblationDesign`` per experiment, as
        determined by the strategy.
    """
    planner = AblationPlanner.create(
        components=components,
        baseline_config=baseline_config,
        strategy=strategy,
    )
    return planner.plan()


def run_ablation(
    designs: list[AblationDesign],
    system_fn: object,
    baseline_score: float | None = None,
) -> list[AblationResult]:
    """Execute ablation designs and return results.

    This is the primary functional entry point for ablation execution.  It
    constructs an ``AblationExecutor``, optionally sets the baseline score,
    runs every design, and returns the collected results.

    If *baseline_score* is ``None`` and the designs share a common
    ``baseline_config`` (taken from the first design), the baseline is
    computed automatically by calling *system_fn* with the unmodified config.

    Args:
        designs:         List of ``AblationDesign`` objects to execute.
        system_fn:       Callable with signature ``(config: dict) -> dict``.
                         The returned dict must contain a ``"score"`` key.
        baseline_score:  Pre-computed baseline score.  If ``None``, the
                         executor will call *system_fn* with the first
                         design's ``baseline_config`` to compute it.

    Returns:
        list[AblationResult]: One result per design, in the same order.
    """
    executor = AblationExecutor.create()
    if baseline_score is not None:
        executor.baseline_score = _clamp(baseline_score, 0.0, 1.0)
    elif designs:
        # Compute baseline from the first design's config.
        executor.compute_baseline(system_fn, designs[0].baseline_config)
    return executor.execute_all(designs, system_fn)
