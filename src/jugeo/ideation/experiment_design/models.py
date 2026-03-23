"""Core domain models for the jugeo.ideation.experiment_design package.

This module defines the immutable and mutable data structures that flow
through the entire experiment-design pipeline.  The lifecycle of an
experiment moves from specification through execution to result:

- :class:`ExperimentDesign`       — immutable parametric specification of a
  planned study (factors, run count, hypothesis).
- :class:`AblationStudy`          — mutable record of a component-removal
  study consistent with Theorem 53.1 (additivity of yield components):
  ΔY_i = Y(full) − Y(full ∖ {C_i}).
- :class:`CalibrationExperiment`  — mutable record tracking parameter
  convergence: E[θ̂] → θ as data accumulates.
- :class:`ExperimentResult`       — mutable record of the observed
  statistical outcome, including effect size d, p-value, and CI.
- :class:`ExperimentBatch`        — container grouping related experiments
  that share a common hypothesis or design phase.
- :class:`ExperimentComparison`   — pairwise comparison of two experiments
  or system configurations.
- :class:`FalsificationTest`      — Popperian falsification attempt storing
  the refuting condition and its severity.
- :class:`PowerAnalysis`          — statistical power planning; tracks
  required sample size, effect size, and α/β parameters.
- :class:`StatisticalTest`        — records a formal hypothesis test with
  test statistic, degrees of freedom, and power estimate.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

_log = logging.getLogger(__name__)

__all__ = [
    "ExperimentDesign",
    "AblationStudy",
    "CalibrationExperiment",
    "ExperimentBatch",
    "ExperimentComparison",
    "ExperimentResult",
    "FalsificationTest",
    "PowerAnalysis",
    "StatisticalTest",
]


@dataclass(frozen=True)
class ExperimentDesign:
    """Immutable specification of a planned experiment.

    The design object is the single source of truth for what an experiment
    *should* do.  Execution state and results live in :class:`ExperimentResult`.

    Attributes:
        design_id: Unique identifier for this design (UUID-4 string).
        name: Human-readable name for the experiment.
        experiment_type: String tag matching an :class:`~manifest.ExperimentType`
            value (e.g. ``"factorial"``, ``"rct"``).
        factors: Ordered tuple of factor/variable names under study.
        n_runs: Number of experimental runs specified by the design.
        hypothesis: Formal or informal hypothesis statement.
        metadata: Arbitrary key-value pairs for downstream consumers.
    """

    design_id: str
    name: str
    experiment_type: str
    factors: tuple[str, ...]
    n_runs: int
    hypothesis: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def factor_count(self) -> int:
        """Return the number of experimental factors."""
        return len(self.factors)

    def summary(self) -> str:
        """Return a one-line summary of the design suitable for logging."""
        return (
            f"ExperimentDesign({self.name!r}, type={self.experiment_type!r}, "
            f"factors={self.factor_count}, runs={self.n_runs})"
        )

    def with_hypothesis(self, hypothesis: str) -> ExperimentDesign:
        """Return a copy of this design with the given hypothesis string."""
        return ExperimentDesign(
            design_id=self.design_id,
            name=self.name,
            experiment_type=self.experiment_type,
            factors=self.factors,
            n_runs=self.n_runs,
            hypothesis=hypothesis,
            metadata=dict(self.metadata),
        )


@dataclass
class AblationStudy:
    """Mutable record of a component-ablation study.

    An ablation study systematically removes components from a system and
    measures the effect on a target yield metric.  Under the additivity
    assumption of Theorem 53.1, ΔY = Σ_i ΔY_i, this identifies which
    components are necessary (ΔY_i > ε) vs. redundant.

    The ``ablation_results`` dict accumulates per-component degradation
    values as :meth:`~ablation.AblationDesigner.run_ablation` is
    called.  It maps component name → observed ΔY_i.

    Attributes:
        study_id: Unique identifier (UUID-4 string).
        name: Human-readable study name.
        components: Ordered tuple of component names to ablate.
        baseline: Description of the full-component baseline configuration.
        baseline_yield: Numeric yield of the full-component system.
        ablation_results: Accumulated per-component degradation values
            (component → ΔY_i).  Populated during execution.
        ablation_order: If non-empty, enforces a specific sequential order
            in which components are removed.
        metadata: Arbitrary key-value pairs.
    """

    study_id: str
    name: str
    components: tuple[str, ...]
    baseline: str
    baseline_yield: float = 1.0
    ablation_results: dict[str, float] = field(default_factory=dict)
    ablation_order: tuple[str, ...] = field(default_factory=tuple)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def n_components(self) -> int:
        """Return the number of components eligible for ablation."""
        return len(self.components)

    @property
    def ordered_ablation(self) -> tuple[str, ...]:
        """Return the ablation order, defaulting to the component declaration order."""
        return self.ablation_order if self.ablation_order else self.components

    def record_result(self, component: str, degradation: float) -> None:
        """Record the yield degradation for a single ablated component."""
        self.ablation_results[component] = degradation

    def summary(self) -> str:
        """Return a one-line summary of the ablation study."""
        n_done = len(self.ablation_results)
        return (
            f"AblationStudy({self.name!r}, n_components={self.n_components}, "
            f"completed={n_done}/{self.n_components}, "
            f"baseline_yield={self.baseline_yield:.4f})"
        )


@dataclass
class ExperimentResult:
    """Mutable record of the outcome of a completed experiment.

    Statistics are stored in SI-compatible units.  The *significant* flag
    is derived from the p-value compared to the conventional α=0.05
    threshold unless explicitly overridden.

    Attributes:
        result_id: Unique identifier (UUID-4 string).
        design_id: Back-reference to the originating :class:`ExperimentDesign`.
        effect_size: Measured effect size; Cohen's d by convention.
        p_value: Observed p-value from hypothesis test in [0, 1].
        significant: Whether the result clears the significance threshold.
        sample_size: Total number of observations contributing to this result.
        confidence_interval: Optional 95% CI as ``(lower, upper)`` tuple.
        summary: Plain-English summary of the result for copilot display.
        completed_at: POSIX timestamp when the result was recorded.
        metadata: Arbitrary key-value pairs for downstream consumers.
    """

    result_id: str
    design_id: str
    effect_size: float
    p_value: float
    significant: bool
    sample_size: int = 0
    confidence_interval: tuple[float, float] | None = None
    summary: str = ""
    completed_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def effect_label(self) -> str:
        """Return a conventional label for the effect size magnitude.

        Uses Cohen's d thresholds: |d| < 0.2 → negligible, < 0.5 → small,
        < 0.8 → medium, ≥ 0.8 → large.
        """
        abs_d = abs(self.effect_size)
        if abs_d < 0.2:
            return "negligible"
        if abs_d < 0.5:
            return "small"
        if abs_d < 0.8:
            return "medium"
        return "large"

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable representation of this result."""
        return {
            "result_id": self.result_id,
            "design_id": self.design_id,
            "effect_size": self.effect_size,
            "p_value": self.p_value,
            "significant": self.significant,
            "sample_size": self.sample_size,
            "confidence_interval": (
                list(self.confidence_interval) if self.confidence_interval else None
            ),
            "summary": self.summary,
            "completed_at": self.completed_at,
            "metadata": self.metadata,
        }

    def __repr__(self) -> str:
        sig_str = "✓" if self.significant else "✗"
        return (
            f"ExperimentResult(id={self.result_id[:8]}…, "
            f"d={self.effect_size:.3f}, p={self.p_value:.4f}, sig={sig_str})"
        )
