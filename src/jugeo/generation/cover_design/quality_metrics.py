r"""Quality metrics for cover design evaluation.

Theory (theory2.tex §58 — Quality metrics):
    The quality of a completed cover design is assessed by a *quality vector*

        q(U) = (q_1, q_2, q_3, q_4, q_5, q_6) ∈ [0,1]^6

    whose components are:

    1. **Coverage completeness** ``q_1``:
            q_1 = |⋃ U_i| / |J|
       Must equal ``1.0`` for a valid cover; any value < 1 indicates
       uncovered regions of the base manifold J.

    2. **Overlap efficiency** ``q_2``:
            q_2 = (useful overlap area) / (total overlap area)
       "Useful" overlap is overlap that participates in at least one Čech
       compatibility check.  Redundant overlap wastes budget.

    3. **Budget utilisation** ``q_3``:
            q_3 = budget_used / budget_allocated
       A budget is a first-class object (theory2.tex §21), not merely an
       integer.  Values near 1.0 are ideal; underspend and overspend are
       both penalised.

    4. **Čech compliance rate** ``q_4``:
            q_4 = |{(i,j) : U_i ∩ U_j ≠ ∅ ∧ Čech(U_i, U_j)}| / |overlapping pairs|
       The Čech condition requires that sections agree on their overlap:
            s_i|_{U_i ∩ U_j} ≅ s_j|_{U_i ∩ U_j}
       Failure here is a hard incompatibility.

    5. **Section coherence** ``q_5``:
            q_5 = (1 / |P|)  Σ_{(i,j)∈P} compat(s_i, s_j)
       where P is the set of overlapping pairs and ``compat`` returns a
       score in [0,1] reflecting how smoothly the sections agree on the
       overlap.

    6. **Priority satisfaction** ``q_6``:
            q_6 = (Σ_{i : complete} w_i) / (Σ_i w_i)
       where w_i is the priority weight of patch i.

    A cover design is *acceptable* iff

        ∀ k ∈ {1,...,6} :  q_k ≥ τ_k

    for threshold vector τ = (τ_1,...,τ_6).  The default thresholds are:

        τ = (1.0, 0.5, 0.7, 0.9, 0.75, 0.8)

    Generated code enters at the ``PROPOSAL`` trust tier (theory2.tex §12)
    and must pass all metric checks before promotion.

    References
    ----------
    theory2.tex  §58  (Quality metrics)
    theory2.tex  §59  (Čech condition and section coherence)
    theory2.tex  §21  (Budget as a first-class object)
    theory2.tex  §12  (Trust tiers — PROPOSAL level)

copilot: s06-quality-metrics
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

try:
    from jugeo.generation.cover_design.models import (  # type: ignore[import]
        CoverDesignError,
        PatchDescriptor,
        Budget,
        CechCondition,
    )
except Exception:  # noqa: BLE001 — optional dependency; stubs used when absent
    class CoverDesignError(Exception):  # type: ignore[no-redef]
        """Stub for CoverDesignError when models are unavailable."""

    @dataclass
    class PatchDescriptor:  # type: ignore[no-redef]
        """Minimal stub for PatchDescriptor."""
        patch_id: str
        priority_weight: float = 1.0
        is_complete: bool = False
        metadata: dict[str, Any] = field(default_factory=dict)

    @dataclass
    class Budget:  # type: ignore[no-redef]
        """Minimal stub for Budget (first-class object per theory2.tex §21)."""
        allocated: float
        used: float = 0.0
        unit: str = "abstract"

        @property
        def utilisation(self) -> float:
            """Fraction of budget consumed."""
            if self.allocated <= 0:
                return 0.0
            return self.used / self.allocated

    @dataclass
    class CechCondition:  # type: ignore[no-redef]
        """Minimal stub for CechCondition."""
        patch_i_id: str
        patch_j_id: str
        passes: bool = True
        compatibility_score: float = 1.0


__all__ = [
    "QualityLevel",
    "MetricDefinition",
    "MetricResult",
    "MetricThreshold",
    "QualityReport",
    "QualityMetricsCoordinator",
    "QualityMetricsAnalyzer",
    "QualityMetricsWitness",
]

_LOG = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TRUST_TIER: str = "PROPOSAL"

# Default threshold values for each metric (τ vector)
_DEFAULT_THRESHOLDS: dict[str, float] = {
    "coverage_completeness": 1.0,
    "overlap_efficiency": 0.5,
    "budget_utilisation": 0.7,
    "cech_compliance_rate": 0.9,
    "section_coherence": 0.75,
    "priority_satisfaction": 0.8,
}

# Human-readable metric labels
_METRIC_LABELS: dict[str, str] = {
    "coverage_completeness": "Coverage completeness (q₁)",
    "overlap_efficiency": "Overlap efficiency (q₂)",
    "budget_utilisation": "Budget utilisation (q₃)",
    "cech_compliance_rate": "Čech compliance rate (q₄)",
    "section_coherence": "Section coherence (q₅)",
    "priority_satisfaction": "Priority satisfaction (q₆)",
}


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class QualityLevel(Enum):
    """Qualitative grade assigned to a completed quality report.

    Levels are ordered from worst to best:

    ``POOR``
        At least one metric is critically below threshold.  The cover
        design is rejected.
    ``ACCEPTABLE``
        All metrics meet their minimum thresholds but none is
        significantly above.  The design is minimally valid.
    ``GOOD``
        All metrics exceed thresholds by at least a comfortable margin
        (≥ 0.05 above threshold for each metric).
    ``EXCELLENT``
        All metrics score ≥ 0.95 across the board.  The cover is
        considered high quality.
    """

    POOR = "POOR"
    ACCEPTABLE = "ACCEPTABLE"
    GOOD = "GOOD"
    EXCELLENT = "EXCELLENT"

    def __lt__(self, other: QualityLevel) -> bool:  # noqa: D105
        _order = [QualityLevel.POOR, QualityLevel.ACCEPTABLE,
                  QualityLevel.GOOD, QualityLevel.EXCELLENT]
        return _order.index(self) < _order.index(other)

    def __le__(self, other: QualityLevel) -> bool:  # noqa: D105
        return self == other or self < other

    def is_acceptable(self) -> bool:
        """Return ``True`` iff this level represents an acceptable design."""
        return self != QualityLevel.POOR


# ---------------------------------------------------------------------------
# Immutable data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MetricDefinition:
    """Static definition of a single quality metric.

    Attributes
    ----------
    metric_id:
        Machine-readable identifier (e.g. ``"coverage_completeness"``).
    label:
        Human-readable label for display.
    description:
        Extended description of what the metric measures.
    unit:
        Unit of measurement (e.g. ``"fraction"``, ``"ratio"``).
    higher_is_better:
        Whether higher values indicate better quality.  All six standard
        metrics satisfy this with value ``True``.
    """

    metric_id: str
    label: str
    description: str
    unit: str = "fraction"
    higher_is_better: bool = True

    def __str__(self) -> str:  # noqa: D105
        return f"MetricDefinition({self.metric_id!r}: {self.label})"


@dataclass(frozen=True, slots=True)
class MetricThreshold:
    """Threshold configuration for a single quality metric.

    Attributes
    ----------
    metric_id:
        Must match a ``MetricDefinition.metric_id``.
    minimum:
        The minimum acceptable value.  Scores below this mark the design
        as ``POOR``.
    good:
        Scores at or above this value earn a ``GOOD`` rating contribution.
        Defaults to ``minimum + 0.05``.
    excellent:
        Scores at or above this value earn an ``EXCELLENT`` rating
        contribution.  Defaults to ``0.95``.
    """

    metric_id: str
    minimum: float
    good: float = 0.0
    excellent: float = 0.95

    def __post_init__(self) -> None:  # noqa: D105
        # Use object.__setattr__ because the dataclass is frozen
        if self.good == 0.0:
            object.__setattr__(self, "good", self.minimum + 0.05)

    def classify(self, score: float) -> QualityLevel:
        """Map *score* to a ``QualityLevel`` relative to this threshold.

        Parameters
        ----------
        score:
            The measured metric value in [0, 1].

        Returns
        -------
        QualityLevel
            The qualitative grade for this metric in isolation.
        """
        if score < self.minimum:
            return QualityLevel.POOR
        if score >= self.excellent:
            return QualityLevel.EXCELLENT
        if score >= self.good:
            return QualityLevel.GOOD
        return QualityLevel.ACCEPTABLE

    def passes(self, score: float) -> bool:
        """Return ``True`` iff *score* meets the minimum threshold."""
        return score >= self.minimum

    def __str__(self) -> str:  # noqa: D105
        return (
            f"MetricThreshold({self.metric_id!r}: "
            f"min={self.minimum:.3f}, good={self.good:.3f}, "
            f"excellent={self.excellent:.3f})"
        )


@dataclass(frozen=True, slots=True)
class MetricResult:
    """The measured value and grade for a single quality metric.

    Attributes
    ----------
    metric_id:
        Identifies which metric was measured.
    score:
        Measured value in [0, 1].
    level:
        Qualitative grade derived from the threshold.
    passes_threshold:
        Whether the score meets the minimum threshold.
    contributing_factors:
        Optional dict describing sub-components of the score (for
        debugging and reporting).
    computed_at:
        Unix timestamp of computation.
    """

    metric_id: str
    score: float
    level: QualityLevel
    passes_threshold: bool
    contributing_factors: dict[str, Any] = field(default_factory=dict)
    computed_at: float = field(default_factory=time.time)

    def summary(self) -> str:
        """Return a single-line summary string."""
        status = "✓" if self.passes_threshold else "✗"
        return (
            f"{status} {_METRIC_LABELS.get(self.metric_id, self.metric_id)}: "
            f"{self.score:.4f} [{self.level.value}]"
        )

    def __str__(self) -> str:  # noqa: D105
        return self.summary()


# ---------------------------------------------------------------------------
# Mutable data types
# ---------------------------------------------------------------------------


@dataclass
class QualityReport:
    """A complete quality assessment for a cover design.

    Attributes
    ----------
    report_id:
        Unique identifier for this report instance.
    design_id:
        Identifier of the cover design that was assessed.
    metric_results:
        Mapping from metric ID to its ``MetricResult``.
    overall_level:
        Aggregated ``QualityLevel`` across all metrics.
    is_acceptable:
        ``True`` iff all metrics pass their thresholds.
    trust_tier:
        Always ``"PROPOSAL"`` for generated designs.
    created_at:
        Unix timestamp.
    notes:
        Free-form annotations added during analysis.
    """

    report_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    design_id: str = ""
    metric_results: dict[str, MetricResult] = field(default_factory=dict)
    overall_level: QualityLevel = QualityLevel.POOR
    is_acceptable: bool = False
    trust_tier: str = _TRUST_TIER
    created_at: float = field(default_factory=time.time)
    notes: list[str] = field(default_factory=list)

    def add_result(self, result: MetricResult) -> None:
        """Add or replace the result for ``result.metric_id``."""
        self.metric_results[result.metric_id] = result

    def add_note(self, note: str) -> None:
        """Append a free-form note to the report."""
        self.notes.append(note)

    def quality_vector(self) -> tuple[float, ...]:
        """Return the quality vector (q₁, …, q₆) in canonical metric order.

        The order follows the theory2.tex §58 specification:
        coverage_completeness, overlap_efficiency, budget_utilisation,
        cech_compliance_rate, section_coherence, priority_satisfaction.
        """
        metric_order = [
            "coverage_completeness",
            "overlap_efficiency",
            "budget_utilisation",
            "cech_compliance_rate",
            "section_coherence",
            "priority_satisfaction",
        ]
        return tuple(
            self.metric_results[m].score if m in self.metric_results else 0.0
            for m in metric_order
        )

    def failing_metrics(self) -> list[str]:
        """Return metric IDs of all metrics that do not pass their threshold."""
        return [mid for mid, r in self.metric_results.items() if not r.passes_threshold]

    def passing_metrics(self) -> list[str]:
        """Return metric IDs of all metrics that pass their threshold."""
        return [mid for mid, r in self.metric_results.items() if r.passes_threshold]

    def format_report(self) -> str:
        """Render the report as a human-readable multi-line string."""
        lines: list[str] = [
            f"Quality Report  [{self.report_id}]",
            f"  Design: {self.design_id}",
            f"  Overall: {self.overall_level.value}  (acceptable={self.is_acceptable})",
            f"  Trust tier: {self.trust_tier}",
            "  Metrics:",
        ]
        for mid in sorted(self.metric_results):
            result = self.metric_results[mid]
            lines.append(f"    {result.summary()}")
        if self.notes:
            lines.append("  Notes:")
            for note in self.notes:
                lines.append(f"    • {note}")
        return "\n".join(lines)

    def __repr__(self) -> str:  # noqa: D105
        return (
            f"QualityReport(id={self.report_id!r}, "
            f"design={self.design_id!r}, "
            f"level={self.overall_level.value}, "
            f"acceptable={self.is_acceptable})"
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _safe_ratio(numerator: float, denominator: float, default: float = 0.0) -> float:
    """Return ``numerator / denominator`` or *default* when denominator is zero."""
    return numerator / denominator if denominator != 0.0 else default


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp *value* to [lo, hi]."""
    return max(lo, min(hi, value))


def _budget_utilisation_score(used: float, allocated: float) -> float:
    """Compute a utilisation score that penalises both overspend and underspend.

    The ideal utilisation is 1.0 (all budget used).  The score is:

        score = 1.0 - |u - 1.0|    where u = used / allocated

    clamped to [0, 1].  This means spending exactly the budget scores 1.0,
    while spending zero or double the budget scores 0.0.
    """
    if allocated <= 0:
        return 0.0
    u = used / allocated
    return _clamp(1.0 - abs(u - 1.0))


def _aggregate_quality_level(
    results: dict[str, MetricResult],
    thresholds: dict[str, MetricThreshold],
) -> tuple[QualityLevel, bool]:
    """Aggregate individual metric levels into a single overall quality level.

    Returns
    -------
    overall_level : QualityLevel
    is_acceptable : bool
    """
    if not results:
        return QualityLevel.POOR, False

    # If any metric fails its threshold the design is POOR
    if any(not r.passes_threshold for r in results.values()):
        return QualityLevel.POOR, False

    # All pass — compute the minimum individual level
    min_level = min(
        (thresholds[mid].classify(r.score) if mid in thresholds else r.level)
        for mid, r in results.items()
    )
    return min_level, True


def _build_default_thresholds() -> dict[str, MetricThreshold]:
    """Construct the default threshold set from the τ vector in theory2.tex §58."""
    return {
        mid: MetricThreshold(
            metric_id=mid,
            minimum=_DEFAULT_THRESHOLDS[mid],
        )
        for mid in _DEFAULT_THRESHOLDS
    }


def _build_standard_definitions() -> dict[str, MetricDefinition]:
    """Return the six standard metric definitions from theory2.tex §58."""
    return {
        "coverage_completeness": MetricDefinition(
            metric_id="coverage_completeness",
            label=_METRIC_LABELS["coverage_completeness"],
            description=(
                "Fraction of the base manifold J covered by the union of all patches. "
                "Must equal 1.0 for a valid cover; any value < 1 indicates uncovered "
                "regions and renders the design invalid."
            ),
        ),
        "overlap_efficiency": MetricDefinition(
            metric_id="overlap_efficiency",
            label=_METRIC_LABELS["overlap_efficiency"],
            description=(
                "Ratio of useful overlap area (area participating in at least one "
                "Čech compatibility check) to total overlap area.  High values "
                "indicate that overlaps are purposeful rather than redundant."
            ),
        ),
        "budget_utilisation": MetricDefinition(
            metric_id="budget_utilisation",
            label=_METRIC_LABELS["budget_utilisation"],
            description=(
                "Closeness of actual budget consumption to the allocated budget. "
                "Both underspend and overspend are penalised.  The budget is a "
                "first-class object (theory2.tex §21), not merely a scalar."
            ),
        ),
        "cech_compliance_rate": MetricDefinition(
            metric_id="cech_compliance_rate",
            label=_METRIC_LABELS["cech_compliance_rate"],
            description=(
                "Fraction of overlapping patch pairs that satisfy the Čech "
                "compatibility condition: s_i|_{U_i∩U_j} ≅ s_j|_{U_i∩U_j}. "
                "Failures here represent hard incompatibilities."
            ),
        ),
        "section_coherence": MetricDefinition(
            metric_id="section_coherence",
            label=_METRIC_LABELS["section_coherence"],
            description=(
                "Mean pairwise compatibility score on overlapping patch pairs.  "
                "Measures the smoothness of section agreement on overlaps, "
                "complementing the binary Čech compliance rate with a graded "
                "assessment."
            ),
        ),
        "priority_satisfaction": MetricDefinition(
            metric_id="priority_satisfaction",
            label=_METRIC_LABELS["priority_satisfaction"],
            description=(
                "Weighted fraction of high-priority patches that have been "
                "successfully completed: (Σ w_i for complete patches) / (Σ w_i). "
                "Reflects how well the design serves its stated priorities."
            ),
        ),
    }


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------


class QualityMetricsCoordinator:
    """Computes all quality metrics for a completed cover design.

    The coordinator is the primary entry point.  It accepts a description
    of the cover design in the form of patch descriptors, overlap records,
    a budget object, and Čech condition results, then delegates to
    ``QualityMetricsAnalyzer`` for individual metric computation and to
    ``QualityMetricsWitness`` for certification.

    All generated designs enter at the ``PROPOSAL`` trust tier
    (theory2.tex §12) until certified by the witness.
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        """Initialise the coordinator.

        Parameters
        ----------
        config:
            Optional configuration overrides.  Recognised keys:

            ``thresholds``
                Dict mapping metric ID to minimum acceptable value.
                Overrides individual entries from the default τ vector.
            ``trust_tier``
                Trust tier for generated reports.  Default: ``"PROPOSAL"``.
            ``warn_on_poor_metrics``
                Log a warning for each metric that fails its threshold.
                Default: ``True``.
        """
        defaults: dict[str, Any] = {
            "thresholds": {},
            "trust_tier": _TRUST_TIER,
            "warn_on_poor_metrics": True,
        }
        cfg = dict(defaults)
        if config:
            cfg.update(config)
        self._config = cfg
        self._logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")

        # Build threshold objects, allowing per-metric overrides
        base_thresholds = _build_default_thresholds()
        override_map: dict[str, float] = cfg.get("thresholds", {})
        for mid, override_min in override_map.items():
            if mid in base_thresholds:
                base_thresholds[mid] = MetricThreshold(
                    metric_id=mid, minimum=override_min
                )
        self._thresholds: dict[str, MetricThreshold] = base_thresholds
        self._definitions: dict[str, MetricDefinition] = _build_standard_definitions()
        self._analyzer = QualityMetricsAnalyzer(thresholds=self._thresholds)
        self._witness = QualityMetricsWitness(thresholds=self._thresholds)
        self._report_history: list[QualityReport] = []

    # ------------------------------------------------------------------
    # Core computation
    # ------------------------------------------------------------------

    def compute_report(
        self,
        design_id: str,
        patches: list[PatchDescriptor],
        overlapping_pairs: list[tuple[str, str]],
        cech_results: list[CechCondition],
        budget: Budget,
        covered_area: float,
        total_area: float,
        total_overlap_area: float,
        useful_overlap_area: float,
        compatibility_scores: dict[tuple[str, str], float] | None = None,
    ) -> QualityReport:
        """Compute the full quality report for *design_id*.

        This is the principal method: it calls the analyzer for each of the
        six standard metrics, aggregates results, and asks the witness to
        certify the report.

        Parameters
        ----------
        design_id:
            Identifier of the cover design being assessed.
        patches:
            All patches in the cover.
        overlapping_pairs:
            Pairs ``(i_id, j_id)`` of patch IDs whose corresponding patches
            have non-empty intersection.
        cech_results:
            One ``CechCondition`` per overlapping pair; ``passes`` indicates
            whether the Čech condition is satisfied.
        budget:
            The budget object for the design (first-class object per §21).
        covered_area:
            Total area covered by the union ⋃ U_i (after de-duplication).
        total_area:
            Total area of the base manifold J.
        total_overlap_area:
            Total area of pairwise intersections (may count multiply-covered
            regions more than once).
        useful_overlap_area:
            Area of overlaps that participate in at least one Čech check.
        compatibility_scores:
            Optional mapping ``(i_id, j_id) → score ∈ [0,1]`` for each
            overlapping pair.  When absent, Čech ``compatibility_score``
            attributes are used as a fallback.

        Returns
        -------
        QualityReport
            The complete, certified quality report.
        """
        self._logger.info(
            "Computing quality report for design '%s' (%d patches, %d pairs)",
            design_id, len(patches), len(overlapping_pairs),
        )

        report = QualityReport(
            design_id=design_id,
            trust_tier=self._config["trust_tier"],
        )

        # Metric 1: coverage completeness
        q1 = self._analyzer.compute_coverage_completeness(covered_area, total_area)
        report.add_result(q1)

        # Metric 2: overlap efficiency
        q2 = self._analyzer.compute_overlap_efficiency(
            useful_overlap_area, total_overlap_area
        )
        report.add_result(q2)

        # Metric 3: budget utilisation
        q3 = self._analyzer.compute_budget_utilisation(budget)
        report.add_result(q3)

        # Metric 4: Čech compliance rate
        q4 = self._analyzer.compute_cech_compliance_rate(
            cech_results, len(overlapping_pairs)
        )
        report.add_result(q4)

        # Metric 5: section coherence
        compat: dict[tuple[str, str], float] = compatibility_scores or {}
        q5 = self._analyzer.compute_section_coherence(
            overlapping_pairs, cech_results, compat
        )
        report.add_result(q5)

        # Metric 6: priority satisfaction
        q6 = self._analyzer.compute_priority_satisfaction(patches)
        report.add_result(q6)

        # Aggregate
        overall_level, is_acceptable = _aggregate_quality_level(
            report.metric_results, self._thresholds
        )
        report.overall_level = overall_level
        report.is_acceptable = is_acceptable

        # Warn on failures
        if self._config["warn_on_poor_metrics"]:
            for mid in report.failing_metrics():
                r = report.metric_results[mid]
                self._logger.warning(
                    "Design '%s': metric '%s' FAILED threshold "
                    "(score=%.4f, minimum=%.4f)",
                    design_id,
                    mid,
                    r.score,
                    self._thresholds[mid].minimum,
                )

        # Certify
        cert = self._witness.certify_report(report)
        if cert["certified"]:
            report.add_note(f"Certified by witness [{cert['certificate_id']}]")
        else:
            report.add_note(
                f"Certification FAILED: {cert['reason']} [{cert['certificate_id']}]"
            )

        self._report_history.append(report)
        self._logger.info(
            "Quality report for '%s': level=%s, acceptable=%s",
            design_id, overall_level.value, is_acceptable,
        )
        return report

    # ------------------------------------------------------------------
    # Convenience methods
    # ------------------------------------------------------------------

    def is_acceptable(self, report: QualityReport) -> bool:
        """Return whether *report* represents an acceptable design."""
        return report.is_acceptable

    def get_threshold(self, metric_id: str) -> MetricThreshold | None:
        """Return the threshold object for *metric_id*, or ``None``."""
        return self._thresholds.get(metric_id)

    def set_threshold(self, metric_id: str, minimum: float) -> None:
        """Override the minimum threshold for *metric_id* at runtime.

        Parameters
        ----------
        metric_id:
            One of the six standard metric IDs.
        minimum:
            New minimum value in [0, 1].
        """
        if metric_id not in self._thresholds:
            raise KeyError(f"Unknown metric ID: {metric_id!r}")
        self._thresholds[metric_id] = MetricThreshold(
            metric_id=metric_id,
            minimum=_clamp(minimum),
        )
        self._logger.info(
            "Threshold for '%s' updated to %.4f", metric_id, minimum
        )

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def report_history(self) -> list[QualityReport]:
        """All reports produced by this coordinator (read-only copy)."""
        return list(self._report_history)

    @property
    def thresholds(self) -> dict[str, MetricThreshold]:
        """Current threshold configuration (read-only copy)."""
        return dict(self._thresholds)

    @property
    def definitions(self) -> dict[str, MetricDefinition]:
        """Static metric definitions (read-only copy)."""
        return dict(self._definitions)

    def __repr__(self) -> str:  # noqa: D105
        return (
            f"QualityMetricsCoordinator("
            f"reports={len(self._report_history)}, "
            f"trust_tier={self._config['trust_tier']!r})"
        )


# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------


class QualityMetricsAnalyzer:
    """Analyses individual metric components and identifies weaknesses.

    This class is responsible for the arithmetic of each quality metric.
    It is a pure computational class: it does not mutate any state it
    receives and produces only immutable ``MetricResult`` objects.
    """

    def __init__(self, thresholds: dict[str, MetricThreshold] | None = None) -> None:
        """Initialise the analyzer with threshold configuration.

        Parameters
        ----------
        thresholds:
            Mapping from metric ID to ``MetricThreshold``.  Falls back to
            the default τ vector from theory2.tex §58 when absent.
        """
        self._thresholds: dict[str, MetricThreshold] = (
            thresholds if thresholds is not None else _build_default_thresholds()
        )
        self._logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")

    # ------------------------------------------------------------------
    # Metric 1: Coverage completeness
    # ------------------------------------------------------------------

    def compute_coverage_completeness(
        self,
        covered_area: float,
        total_area: float,
    ) -> MetricResult:
        """Compute the coverage completeness metric (q₁).

        Parameters
        ----------
        covered_area:
            Area of the union of all patches (double-counting removed).
        total_area:
            Area of the base manifold J.

        Returns
        -------
        MetricResult
            The computed result with contributing factors.
        """
        mid = "coverage_completeness"
        score = _clamp(_safe_ratio(covered_area, total_area, default=0.0))
        threshold = self._thresholds[mid]
        level = threshold.classify(score)

        factors: dict[str, Any] = {
            "covered_area": covered_area,
            "total_area": total_area,
            "uncovered_area": max(0.0, total_area - covered_area),
        }

        self._logger.debug(
            "Coverage completeness: covered=%.4f / total=%.4f → score=%.4f [%s]",
            covered_area, total_area, score, level.value,
        )
        return MetricResult(
            metric_id=mid,
            score=score,
            level=level,
            passes_threshold=threshold.passes(score),
            contributing_factors=factors,
        )

    # ------------------------------------------------------------------
    # Metric 2: Overlap efficiency
    # ------------------------------------------------------------------

    def compute_overlap_efficiency(
        self,
        useful_overlap_area: float,
        total_overlap_area: float,
    ) -> MetricResult:
        """Compute the overlap efficiency metric (q₂).

        Parameters
        ----------
        useful_overlap_area:
            Area of overlaps that participate in at least one Čech check.
        total_overlap_area:
            Total overlap area (sum of areas of all pairwise intersections).

        Returns
        -------
        MetricResult
        """
        mid = "overlap_efficiency"
        score = _clamp(_safe_ratio(useful_overlap_area, total_overlap_area, default=1.0))
        threshold = self._thresholds[mid]
        level = threshold.classify(score)

        factors: dict[str, Any] = {
            "useful_overlap_area": useful_overlap_area,
            "total_overlap_area": total_overlap_area,
            "wasted_overlap_area": max(0.0, total_overlap_area - useful_overlap_area),
        }

        self._logger.debug(
            "Overlap efficiency: useful=%.4f / total=%.4f → score=%.4f [%s]",
            useful_overlap_area, total_overlap_area, score, level.value,
        )
        return MetricResult(
            metric_id=mid,
            score=score,
            level=level,
            passes_threshold=threshold.passes(score),
            contributing_factors=factors,
        )

    # ------------------------------------------------------------------
    # Metric 3: Budget utilisation
    # ------------------------------------------------------------------

    def compute_budget_utilisation(self, budget: Budget) -> MetricResult:
        """Compute the budget utilisation metric (q₃).

        The budget is a *first-class object* (theory2.tex §21).  Both
        overspend and underspend reduce the score.

        Parameters
        ----------
        budget:
            The budget object for the design.

        Returns
        -------
        MetricResult
        """
        mid = "budget_utilisation"
        allocated = float(getattr(budget, "allocated", 0.0))
        used = float(getattr(budget, "used", 0.0))
        score = _clamp(_budget_utilisation_score(used, allocated))
        threshold = self._thresholds[mid]
        level = threshold.classify(score)

        raw_util = _safe_ratio(used, allocated, default=0.0)
        factors: dict[str, Any] = {
            "allocated": allocated,
            "used": used,
            "raw_utilisation": raw_util,
            "unit": getattr(budget, "unit", "abstract"),
            "overspend": max(0.0, used - allocated),
            "underspend": max(0.0, allocated - used),
        }

        self._logger.debug(
            "Budget utilisation: used=%.4f / allocated=%.4f → "
            "raw_util=%.4f, score=%.4f [%s]",
            used, allocated, raw_util, score, level.value,
        )
        return MetricResult(
            metric_id=mid,
            score=score,
            level=level,
            passes_threshold=threshold.passes(score),
            contributing_factors=factors,
        )

    # ------------------------------------------------------------------
    # Metric 4: Čech compliance rate
    # ------------------------------------------------------------------

    def compute_cech_compliance_rate(
        self,
        cech_results: list[CechCondition],
        total_overlapping_pairs: int,
    ) -> MetricResult:
        """Compute the Čech compliance rate metric (q₄).

        Parameters
        ----------
        cech_results:
            One ``CechCondition`` per overlapping pair.
        total_overlapping_pairs:
            Total number of overlapping pairs (denominator).

        Returns
        -------
        MetricResult
        """
        mid = "cech_compliance_rate"
        if total_overlapping_pairs == 0:
            # No overlaps to check: vacuously compliant
            score = 1.0
        else:
            passing = sum(
                1 for cc in cech_results if getattr(cc, "passes", False)
            )
            score = _clamp(_safe_ratio(passing, total_overlapping_pairs))

        threshold = self._thresholds[mid]
        level = threshold.classify(score)
        passing_count = sum(1 for cc in cech_results if getattr(cc, "passes", False))
        failing_count = len(cech_results) - passing_count

        factors: dict[str, Any] = {
            "checked_pairs": len(cech_results),
            "passing_pairs": passing_count,
            "failing_pairs": failing_count,
            "total_overlapping_pairs": total_overlapping_pairs,
        }

        self._logger.debug(
            "Čech compliance: %d/%d pairs pass → score=%.4f [%s]",
            passing_count, total_overlapping_pairs, score, level.value,
        )
        return MetricResult(
            metric_id=mid,
            score=score,
            level=level,
            passes_threshold=threshold.passes(score),
            contributing_factors=factors,
        )

    # ------------------------------------------------------------------
    # Metric 5: Section coherence
    # ------------------------------------------------------------------

    def compute_section_coherence(
        self,
        overlapping_pairs: list[tuple[str, str]],
        cech_results: list[CechCondition],
        compatibility_scores: dict[tuple[str, str], float],
    ) -> MetricResult:
        """Compute the section coherence metric (q₅).

        The section coherence is the mean pairwise compatibility score on
        all overlapping pairs.

        Parameters
        ----------
        overlapping_pairs:
            All pairs of patches with non-empty intersection.
        cech_results:
            Čech condition results used as a fallback source of
            compatibility scores when ``compatibility_scores`` does not
            contain an entry.
        compatibility_scores:
            Explicit compatibility scores for pairs.  Takes priority over
            ``cech_results``.

        Returns
        -------
        MetricResult
        """
        mid = "section_coherence"

        if not overlapping_pairs:
            score = 1.0
            factors: dict[str, Any] = {
                "pair_count": 0,
                "mean_score": 1.0,
                "min_score": 1.0,
                "max_score": 1.0,
            }
        else:
            # Build a lookup from Čech results
            cech_compat: dict[tuple[str, str], float] = {}
            for cc in cech_results:
                key = (cc.patch_i_id, cc.patch_j_id)
                cech_compat[key] = getattr(cc, "compatibility_score", 1.0 if cc.passes else 0.0)
                cech_compat[(cc.patch_j_id, cc.patch_i_id)] = cech_compat[key]

            scores_list: list[float] = []
            for pair in overlapping_pairs:
                if pair in compatibility_scores:
                    s = compatibility_scores[pair]
                elif (pair[1], pair[0]) in compatibility_scores:
                    s = compatibility_scores[(pair[1], pair[0])]
                elif pair in cech_compat:
                    s = cech_compat[pair]
                else:
                    # No data available; assume neutral compatibility
                    s = 0.5
                scores_list.append(_clamp(s))

            mean_score = sum(scores_list) / len(scores_list)
            score = _clamp(mean_score)
            factors = {
                "pair_count": len(overlapping_pairs),
                "mean_score": score,
                "min_score": min(scores_list),
                "max_score": max(scores_list),
                "low_coherence_pairs": [
                    overlapping_pairs[i]
                    for i, s in enumerate(scores_list)
                    if s < 0.5
                ],
            }

        threshold = self._thresholds[mid]
        level = threshold.classify(score)

        self._logger.debug(
            "Section coherence: %d pairs → mean=%.4f [%s]",
            len(overlapping_pairs), score, level.value,
        )
        return MetricResult(
            metric_id=mid,
            score=score,
            level=level,
            passes_threshold=threshold.passes(score),
            contributing_factors=factors,
        )

    # ------------------------------------------------------------------
    # Metric 6: Priority satisfaction
    # ------------------------------------------------------------------

    def compute_priority_satisfaction(
        self,
        patches: list[PatchDescriptor],
    ) -> MetricResult:
        """Compute the priority satisfaction metric (q₆).

        Parameters
        ----------
        patches:
            All patches in the cover design.  Each must have a
            ``priority_weight`` attribute (float) and an ``is_complete``
            attribute (bool).

        Returns
        -------
        MetricResult
        """
        mid = "priority_satisfaction"

        if not patches:
            score = 1.0
            factors: dict[str, Any] = {
                "total_weight": 0.0,
                "completed_weight": 0.0,
                "incomplete_patches": [],
            }
        else:
            total_weight = sum(
                float(getattr(p, "priority_weight", 1.0)) for p in patches
            )
            completed_weight = sum(
                float(getattr(p, "priority_weight", 1.0))
                for p in patches
                if getattr(p, "is_complete", False)
            )
            score = _clamp(_safe_ratio(completed_weight, total_weight, default=0.0))
            incomplete = [
                getattr(p, "patch_id", "?")
                for p in patches
                if not getattr(p, "is_complete", False)
            ]
            factors = {
                "total_weight": total_weight,
                "completed_weight": completed_weight,
                "incomplete_patches": incomplete,
                "patch_count": len(patches),
                "complete_count": len(patches) - len(incomplete),
            }

        threshold = self._thresholds[mid]
        level = threshold.classify(score)

        self._logger.debug(
            "Priority satisfaction: score=%.4f [%s]", score, level.value
        )
        return MetricResult(
            metric_id=mid,
            score=score,
            level=level,
            passes_threshold=threshold.passes(score),
            contributing_factors=factors,
        )

    # ------------------------------------------------------------------
    # Weakness analysis
    # ------------------------------------------------------------------

    def identify_weaknesses(self, report: QualityReport) -> list[dict[str, Any]]:
        """Identify the weakest metrics in *report* and suggest improvements.

        A weakness is any metric that either fails its threshold or scores
        in the bottom 20% relative to its threshold's excellent band.

        Parameters
        ----------
        report:
            A completed quality report.

        Returns
        -------
        list[dict]
            Each entry has keys:
            ``metric_id``, ``score``, ``threshold``, ``gap``,
            ``severity``, ``suggestion``.
        """
        weaknesses: list[dict[str, Any]] = []

        _suggestions: dict[str, str] = {
            "coverage_completeness": (
                "Identify and fill uncovered regions by adding patches to J."
            ),
            "overlap_efficiency": (
                "Reduce redundant overlaps; merge patches that overlap without "
                "contributing to Čech checks."
            ),
            "budget_utilisation": (
                "Review spend allocation; reallocate budget from idle patches "
                "to those near completion."
            ),
            "cech_compliance_rate": (
                "Inspect failing overlapping pairs for interface inconsistencies "
                "and apply re-gluing operations."
            ),
            "section_coherence": (
                "Identify low-coherence patch pairs and improve their boundary "
                "compatibility through interface negotiation."
            ),
            "priority_satisfaction": (
                "Review incomplete high-priority patches and either complete them "
                "or downgrade their priority with justification."
            ),
        }

        for mid, result in report.metric_results.items():
            threshold = self._thresholds.get(mid)
            if threshold is None:
                continue
            gap = result.score - threshold.minimum
            is_weak = not result.passes_threshold or result.score < threshold.good

            if is_weak:
                severity = "critical" if not result.passes_threshold else "minor"
                weaknesses.append({
                    "metric_id": mid,
                    "score": result.score,
                    "threshold": threshold.minimum,
                    "gap": gap,
                    "severity": severity,
                    "suggestion": _suggestions.get(mid, "Review this metric."),
                })

        # Sort: critical first, then by gap ascending (biggest deficits first)
        weaknesses.sort(key=lambda w: (0 if w["severity"] == "critical" else 1, w["gap"]))
        return weaknesses

    def __repr__(self) -> str:  # noqa: D105
        return "QualityMetricsAnalyzer()"


# ---------------------------------------------------------------------------
# Witness
# ---------------------------------------------------------------------------


class QualityMetricsWitness:
    """Certifies that a cover design meets quality thresholds.

    A witness issues a *certificate of quality*: a serialisable dict
    recording which metrics were checked, whether they passed, and the
    resulting overall verdict.  The certificate can be stored for auditing
    and used to gate promotion from the ``PROPOSAL`` trust tier.
    """

    def __init__(self, thresholds: dict[str, MetricThreshold] | None = None) -> None:
        """Initialise the witness.

        Parameters
        ----------
        thresholds:
            Threshold configuration.  Falls back to the default τ vector.
        """
        self._thresholds: dict[str, MetricThreshold] = (
            thresholds if thresholds is not None else _build_default_thresholds()
        )
        self._logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
        self._certificates: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Certification
    # ------------------------------------------------------------------

    def certify_report(self, report: QualityReport) -> dict[str, Any]:
        """Certify that *report* represents an acceptable cover design.

        The certification checks:

        1. Every standard metric is present in the report.
        2. Every metric score lies in [0, 1].
        3. Coverage completeness equals 1.0 (hard requirement from §58).
        4. All metrics pass their minimum thresholds.

        Parameters
        ----------
        report:
            The quality report to certify.

        Returns
        -------
        dict
            ``{
                "certificate_id": str,
                "report_id": str,
                "design_id": str,
                "certified": bool,
                "reason": str,
                "metric_verdicts": dict[str, bool],
                "certified_at": float,
            }``
        """
        cert_id = str(uuid.uuid4())
        issues: list[str] = []
        metric_verdicts: dict[str, bool] = {}

        required_metrics = set(_DEFAULT_THRESHOLDS.keys())
        present_metrics = set(report.metric_results.keys())

        # 1. Completeness of metrics
        missing_metrics = required_metrics - present_metrics
        if missing_metrics:
            issues.append(f"Missing metrics: {sorted(missing_metrics)}")

        for mid, result in report.metric_results.items():
            # 2. Score range check
            if not (0.0 <= result.score <= 1.0):
                issues.append(
                    f"Metric '{mid}' score {result.score:.6f} is out of [0,1]"
                )
                metric_verdicts[mid] = False
                continue

            # 3. Threshold check
            threshold = self._thresholds.get(mid)
            if threshold is not None and not threshold.passes(result.score):
                issues.append(
                    f"Metric '{mid}' score {result.score:.4f} < threshold "
                    f"{threshold.minimum:.4f}"
                )
                metric_verdicts[mid] = False
            else:
                metric_verdicts[mid] = True

        # 4. Hard requirement: coverage completeness must be exactly 1.0
        cov_result = report.metric_results.get("coverage_completeness")
        if cov_result is not None and cov_result.score < 1.0:
            issues.append(
                f"Coverage completeness is {cov_result.score:.6f} < 1.0 — "
                "design does not fully cover J."
            )
            metric_verdicts["coverage_completeness"] = False

        certified = len(issues) == 0
        reason = "All checks passed." if certified else "; ".join(issues)

        cert: dict[str, Any] = {
            "certificate_id": cert_id,
            "report_id": report.report_id,
            "design_id": report.design_id,
            "certified": certified,
            "reason": reason,
            "metric_verdicts": metric_verdicts,
            "overall_level": report.overall_level.value,
            "certified_at": time.time(),
        }
        self._certificates.append(cert)

        if certified:
            self._logger.info(
                "Quality certificate issued for design '%s' [cert=%s]",
                report.design_id, cert_id,
            )
        else:
            self._logger.warning(
                "Certification FAILED for design '%s': %s",
                report.design_id, reason,
            )

        return cert

    def certify_metric_individually(
        self,
        result: MetricResult,
    ) -> dict[str, Any]:
        """Issue a targeted certificate for a single ``MetricResult``.

        Useful for spot-checking one metric without building a full report.

        Parameters
        ----------
        result:
            The metric result to certify.

        Returns
        -------
        dict
            ``{
                "certificate_id": str,
                "metric_id": str,
                "score": float,
                "certified": bool,
                "reason": str,
                "certified_at": float,
            }``
        """
        cert_id = str(uuid.uuid4())
        threshold = self._thresholds.get(result.metric_id)
        if threshold is None:
            certified = True
            reason = f"No threshold defined for metric '{result.metric_id}'."
        elif not (0.0 <= result.score <= 1.0):
            certified = False
            reason = f"Score {result.score:.6f} outside [0, 1]."
        elif not threshold.passes(result.score):
            certified = False
            reason = (
                f"Score {result.score:.4f} below minimum threshold "
                f"{threshold.minimum:.4f}."
            )
        else:
            certified = True
            reason = "Passes threshold."

        cert: dict[str, Any] = {
            "certificate_id": cert_id,
            "metric_id": result.metric_id,
            "score": result.score,
            "certified": certified,
            "reason": reason,
            "certified_at": time.time(),
        }
        self._certificates.append(cert)
        return cert

    # ------------------------------------------------------------------
    # Certificate retrieval
    # ------------------------------------------------------------------

    @property
    def certificates(self) -> list[dict[str, Any]]:
        """All certificates issued so far (read-only copy)."""
        return list(self._certificates)

    def latest_certificate(self) -> dict[str, Any] | None:
        """Return the most recently issued certificate, or ``None``."""
        return self._certificates[-1] if self._certificates else None

    def summary(self) -> dict[str, Any]:
        """Return aggregate statistics over all issued certificates.

        Returns
        -------
        dict
            ``{
                "total": int,
                "certified": int,
                "failed": int,
                "pass_rate": float,
            }``
        """
        total = len(self._certificates)
        certified = sum(1 for c in self._certificates if c.get("certified"))
        return {
            "total": total,
            "certified": certified,
            "failed": total - certified,
            "pass_rate": _safe_ratio(certified, total, default=1.0),
        }

    def __repr__(self) -> str:  # noqa: D105
        return f"QualityMetricsWitness(certificates={len(self._certificates)})"


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    print("=== quality_metrics smoke test ===\n")

    # Construct a simple cover design scenario
    patches = [
        PatchDescriptor(patch_id="P1", priority_weight=3.0, is_complete=True),
        PatchDescriptor(patch_id="P2", priority_weight=2.0, is_complete=True),
        PatchDescriptor(patch_id="P3", priority_weight=1.0, is_complete=False),
    ]

    overlapping_pairs = [("P1", "P2"), ("P2", "P3")]

    cech_results = [
        CechCondition(patch_i_id="P1", patch_j_id="P2", passes=True, compatibility_score=0.92),
        CechCondition(patch_i_id="P2", patch_j_id="P3", passes=True, compatibility_score=0.80),
    ]

    budget = Budget(allocated=100.0, used=88.0)

    coord = QualityMetricsCoordinator()

    report = coord.compute_report(
        design_id="cover-design-001",
        patches=patches,
        overlapping_pairs=overlapping_pairs,
        cech_results=cech_results,
        budget=budget,
        covered_area=95.0,
        total_area=100.0,
        total_overlap_area=15.0,
        useful_overlap_area=15.0,
    )

    print(report.format_report())
    print()
    print("Quality vector:", report.quality_vector())
    print("Failing metrics:", report.failing_metrics())

    analyzer = QualityMetricsAnalyzer()
    weaknesses = analyzer.identify_weaknesses(report)
    if weaknesses:
        print("\nWeaknesses identified:")
        for w in weaknesses:
            print(f"  [{w['severity'].upper()}] {w['metric_id']}: "
                  f"score={w['score']:.4f}, gap={w['gap']:.4f}")
            print(f"    → {w['suggestion']}")
    else:
        print("\nNo weaknesses identified.")

    # Test individual metric certification
    witness = QualityMetricsWitness()
    cov_result = report.metric_results.get("coverage_completeness")
    if cov_result:
        cert = witness.certify_metric_individually(cov_result)
        print(f"\nIndividual cert for coverage_completeness: "
              f"certified={cert['certified']}, reason={cert['reason']!r}")

    print("\nWitness summary:", witness.summary())
    print("\nSmoke test passed.")
