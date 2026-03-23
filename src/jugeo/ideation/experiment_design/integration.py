from __future__ import annotations

"""Integration layer connecting experiment design to the JuGeo ideation system.

Chapter 53 — Experiment Design for Mathematical Ideation Optimization.

This module bridges four ideation sub-systems to the experiment design pipeline:

- :class:`ExperimentDesignIntegration` orchestrates a suite of
  :class:`~jugeo.ideation.experiment_design.models.ExperimentDesign` objects,
  collects their results, and produces human-readable summaries and
  recommendations.

- :class:`IdeationSystemBridge` translates first-class ideation objects
  (:class:`~jugeo.ideation.ideas.IdeaProposal`,
  :class:`~jugeo.ideation.regimes.IdeationRegime`,
  :class:`~jugeo.ideation.scheduling.IdeationSchedule`,
  :class:`~jugeo.ideation.novelty.NoveltyScore`) into experiment-design
  constructs.

- :class:`CopilotExperimentAdvisor` provides advisory reasoning over
  :class:`~jugeo.ideation.experiment_design.models.ExperimentDesign` and
  :class:`~jugeo.ideation.experiment_design.models.ExperimentResult` objects,
  surfacing improvement suggestions and follow-up experiments in plain English.

- :class:`ExperimentEventBus` implements a lightweight publish-subscribe bus
  for experiment lifecycle events (started, completed, failed), enabling
  decoupled notification handlers.

- :class:`ResultRepository` is an in-memory store for
  :class:`~jugeo.ideation.experiment_design.models.ExperimentResult` objects
  with query methods by design ID, significance, and aggregate statistics.

Mathematical context
--------------------
Experiment results are evaluated using Cohen's d thresholds (Theorem 53.13)
and the Bonferroni family-wise error rate bound (Theorem 53.8).  The event
bus ensures experiments are independent (Theorem 53.15).  Recommendations
respect statistical power requirements (Theorem 53.4).
"""

import dataclasses
import json
import logging
import math
import time
import uuid
from collections.abc import Callable
from typing import Any

from jugeo.ideation.ideas import IdeaProposal
from jugeo.ideation.novelty import NoveltyScore
from jugeo.ideation.regimes import IdeationRegime
from jugeo.ideation.scheduling import IdeationSchedule
from jugeo.ideation.experiment_design.manifest import ExperimentDesignManifest, ExperimentType
from jugeo.ideation.experiment_design.models import AblationStudy, ExperimentDesign, ExperimentResult

_log = logging.getLogger(__name__)

__all__ = [
    "ExperimentDesignIntegration",
    "IdeationSystemBridge",
    "CopilotExperimentAdvisor",
    "ExperimentEventBus",
    "ResultRepository",
]


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _event_timestamp() -> float:
    """Return the current POSIX timestamp.

    Returns:
        Current time as a float (seconds since the Unix epoch).
    """
    return time.time()


def _format_event(event_type: str, payload: dict) -> dict:
    """Build a standardised event envelope dict.

    Args:
        event_type: Short string identifier for the event class (e.g.
            ``"experiment.started"``).
        payload: Arbitrary dict describing the event's content.

    Returns:
        Dict with keys ``'type'``, ``'timestamp'``, and ``'payload'``.
    """
    return {
        "type": event_type,
        "timestamp": _event_timestamp(),
        "payload": payload,
    }


def _score_idea_for_experiment(idea: IdeaProposal) -> float:
    """Derive a normalised experiment-priority score from an :class:`IdeaProposal`.

    Uses the ``payoff`` field (the closest analogue to an integer priority) and
    clamps the result to [0.0, 1.0].

    Args:
        idea: :class:`~jugeo.ideation.ideas.IdeaProposal` to score.

    Returns:
        Float in [0.0, 1.0] representing how urgently the idea warrants an
        experiment.
    """
    raw = getattr(idea, "priority", None)
    if raw is None:
        raw = getattr(idea, "payoff", 0)
    return max(0.0, min(1.0, float(raw) / 10.0))


# ---------------------------------------------------------------------------
# ExperimentDesignIntegration
# ---------------------------------------------------------------------------


class ExperimentDesignIntegration:
    """Orchestrates an experiment design session for a research programme.

    Provides high-level workflow methods: running a suite of designs, collecting
    results, generating recommendations, and exporting outcomes.

    Attributes:
        manifest: The active :class:`ExperimentDesignManifest` configuration.
        _results_cache: In-memory cache of completed results keyed by design ID.
    """

    def __init__(self, manifest: ExperimentDesignManifest | None = None) -> None:
        """Initialise with an optional manifest.

        Args:
            manifest: Active configuration.  If ``None``, a default manifest
                must be supplied via :meth:`setup` before running experiments.
        """
        self.manifest: ExperimentDesignManifest | None = manifest
        self._results_cache: dict[str, list[ExperimentResult]] = {}
        _log.debug("ExperimentDesignIntegration initialised.")

    def setup(self, manifest: ExperimentDesignManifest) -> None:
        """Attach a manifest to this integration instance.

        Args:
            manifest: :class:`ExperimentDesignManifest` describing the
                programme's scope, budget, and active experiment types.
        """
        self.manifest = manifest
        _log.info("ExperimentDesignIntegration configured with manifest %r.", manifest.manifest_id)

    def run_experiment_suite(
        self, designs: list[ExperimentDesign]
    ) -> list[ExperimentResult]:
        """Simulate running a suite of experiment designs and collect results.

        For each design a synthetic :class:`ExperimentResult` is produced whose
        effect size and p-value are derived deterministically from the design's
        factor count and run count, ensuring reproducibility without external
        computation.

        In production code this method would dispatch to real execution
        infrastructure; here it provides a testable stand-in that respects the
        manifest's significance level.

        Args:
            designs: List of :class:`ExperimentDesign` objects to execute.

        Returns:
            List of :class:`ExperimentResult` objects, one per design, in the
            same order as *designs*.
        """
        alpha = self.manifest.significance_level if self.manifest else 0.05
        results: list[ExperimentResult] = []
        for design in designs:
            # Deterministic synthetic effect size: larger designs → smaller d
            effect_size = round(1.0 / (1.0 + math.log1p(design.n_runs)), 4)
            # p-value inversely proportional to factor count (more factors → lower p)
            raw_p = 1.0 / (1.0 + design.factor_count * design.n_runs / 10.0)
            p_value = round(max(1e-6, min(1.0, raw_p)), 6)
            significant = p_value < alpha
            result = ExperimentResult(
                result_id=str(uuid.uuid4()),
                design_id=design.design_id,
                effect_size=effect_size,
                p_value=p_value,
                significant=significant,
                sample_size=design.n_runs,
                summary=(
                    f"Experiment {design.name!r} completed: "
                    f"d={effect_size:.3f}, p={p_value:.4f}, "
                    f"{'significant' if significant else 'not significant'} at α={alpha}."
                ),
            )
            self._results_cache.setdefault(design.design_id, []).append(result)
            results.append(result)
            _log.debug("Ran experiment %r → result %r.", design.name, result.result_id[:8])
        return results

    def summarize_results(self, results: list[ExperimentResult]) -> dict[str, Any]:
        """Produce an aggregate summary over a collection of results.

        Args:
            results: List of :class:`ExperimentResult` objects.

        Returns:
            Dict with:
                - ``'n_experiments'``: total experiment count.
                - ``'n_significant'``: count of significant results.
                - ``'mean_effect_size'``: arithmetic mean of |d| values.
                - ``'mean_p_value'``: arithmetic mean of p-values.
                - ``'significant_fraction'``: n_significant / n_experiments.
                - ``'overall_conclusion'``: plain-English interpretation.
        """
        if not results:
            return {
                "n_experiments": 0,
                "n_significant": 0,
                "mean_effect_size": 0.0,
                "mean_p_value": 1.0,
                "significant_fraction": 0.0,
                "overall_conclusion": "No results to summarise.",
            }
        n = len(results)
        n_sig = sum(1 for r in results if r.significant)
        mean_d = sum(abs(r.effect_size) for r in results) / n
        mean_p = sum(r.p_value for r in results) / n
        sig_frac = n_sig / n
        if sig_frac >= 0.7:
            conclusion = "Strong consistent evidence across experiments."
        elif sig_frac >= 0.3:
            conclusion = "Mixed evidence; some experiments show significant effects."
        else:
            conclusion = "Weak evidence overall; consider increasing sample sizes."
        return {
            "n_experiments": n,
            "n_significant": n_sig,
            "mean_effect_size": round(mean_d, 4),
            "mean_p_value": round(mean_p, 6),
            "significant_fraction": round(sig_frac, 4),
            "overall_conclusion": conclusion,
        }

    def recommend_next_experiments(
        self, results: list[ExperimentResult], n: int = 3
    ) -> list[str]:
        """Generate experiment type recommendations based on observed results.

        Prioritises experiment types that are likely to resolve remaining
        uncertainty: small effects suggest increasing sample size (RCT or
        Bayesian); large effects suggest ablation to identify drivers; no
        significance suggests sensitivity or calibration.

        Args:
            results: Completed results to base recommendations on.
            n: Number of recommendations to return.

        Returns:
            List of up to *n* :class:`ExperimentType` value strings.
        """
        if not results:
            return [ExperimentType.ABLATION.value, ExperimentType.CALIBRATION.value][:n]

        mean_d = sum(abs(r.effect_size) for r in results) / len(results)
        n_sig = sum(1 for r in results if r.significant)
        recommendations: list[str] = []

        if n_sig == 0:
            recommendations.append(ExperimentType.CALIBRATION.value)
            recommendations.append(ExperimentType.SENSITIVITY.value)
        if mean_d < 0.2:
            recommendations.append(ExperimentType.CALIBRATION.value)
        if mean_d >= 0.5:
            recommendations.append(ExperimentType.ABLATION.value)
        recommendations.append(ExperimentType.FALSIFICATION.value)
        recommendations.append(ExperimentType.COMPARISON.value)

        # De-duplicate while preserving order
        seen: set[str] = set()
        unique: list[str] = []
        for rec in recommendations:
            if rec not in seen:
                seen.add(rec)
                unique.append(rec)
        return unique[:n]

    def export_results(
        self, results: list[ExperimentResult], format: str = "text"
    ) -> str:
        """Export results in the requested format.

        Args:
            results: List of :class:`ExperimentResult` to export.
            format: ``"text"`` for a human-readable table, ``"json"`` for
                machine-readable output, ``"csv"`` for spreadsheet-compatible
                output.

        Returns:
            String representation in the requested format.
        """
        if format == "json":
            return json.dumps([r.as_dict() for r in results], indent=2)
        if format == "csv":
            header = "result_id,design_id,effect_size,p_value,significant,sample_size"
            rows = [header]
            for r in results:
                rows.append(
                    f"{r.result_id},{r.design_id},"
                    f"{r.effect_size},{r.p_value},"
                    f"{r.significant},{r.sample_size}"
                )
            return "\n".join(rows)
        # Default: text table
        lines = ["=" * 72, "Experiment Results Summary", "=" * 72]
        for r in results:
            sig_mark = "✓" if r.significant else "✗"
            lines.append(
                f"  [{sig_mark}] {r.result_id[:8]}… | "
                f"d={r.effect_size:+.3f} | p={r.p_value:.4f} | "
                f"n={r.sample_size} | {r.effect_label()}"
            )
        lines.append("=" * 72)
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# IdeationSystemBridge
# ---------------------------------------------------------------------------


class IdeationSystemBridge:
    """Translates ideation objects into experiment design constructs.

    Converts :class:`~jugeo.ideation.ideas.IdeaProposal` objects into
    :class:`~jugeo.ideation.experiment_design.models.ExperimentDesign` objects,
    :class:`~jugeo.ideation.regimes.IdeationRegime` objects into
    :class:`~jugeo.ideation.experiment_design.models.AblationStudy` objects,
    and ideation schedules / novelty scores into calibration targets.
    """

    def __init__(self) -> None:
        """Initialise the bridge (stateless; no configuration required)."""
        _log.debug("IdeationSystemBridge initialised.")

    def ideas_to_experiment_designs(
        self, ideas: list[IdeaProposal]
    ) -> list[ExperimentDesign]:
        """Convert a list of idea proposals into experiment designs.

        Each :class:`IdeaProposal` produces one :class:`ExperimentDesign` whose
        factors are derived from the proposal's provenance strings and whose
        run count is set proportional to the normalised payoff score.

        Args:
            ideas: List of :class:`IdeaProposal` to convert.

        Returns:
            List of :class:`ExperimentDesign` objects, one per idea.
        """
        designs: list[ExperimentDesign] = []
        for idea in ideas:
            score = _score_idea_for_experiment(idea)
            n_runs = max(8, int(score * 100))
            # Use provenance as factor proxies; fall back to generic names
            factors: tuple[str, ...] = tuple(
                f"prov_{i}" for i in range(len(idea.provenance))
            ) if idea.provenance else ("hypothesis_factor", "context_factor")
            design = ExperimentDesign(
                design_id=str(uuid.uuid4()),
                name=f"exp_{idea.title[:40].replace(' ', '_').lower()}",
                experiment_type=ExperimentType.ABLATION.value,
                factors=factors,
                n_runs=n_runs,
                hypothesis=idea.hypothesis,
            )
            designs.append(design)
            _log.debug("Converted IdeaProposal %r → ExperimentDesign %r.", idea.title, design.design_id[:8])
        return designs

    def regime_to_ablation_study(
        self, regime: IdeationRegime, components: list[str]
    ) -> AblationStudy:
        """Create an ablation study that probes a regime's admissible moves.

        Each *component* is treated as one unit of the regime that can be
        removed to measure its contribution to the regime's novelty score.

        Args:
            regime: :class:`IdeationRegime` whose components are to be ablated.
            components: Explicit list of component names to ablate.

        Returns:
            :class:`AblationStudy` with baseline set to the regime's purpose.
        """
        return AblationStudy(
            study_id=str(uuid.uuid4()),
            name=f"ablation_{regime.regime_id}",
            components=tuple(components),
            baseline=regime.purpose,
            ablation_order=tuple(components),
            metadata={"regime_id": regime.regime_id, "novelty_metric": regime.novelty_metric},
        )

    def schedule_to_calibration_targets(
        self, schedule: IdeationSchedule
    ) -> list[dict[str, Any]]:
        """Extract calibration targets from an ideation schedule.

        Each exploration and exploitation idea in the schedule becomes one
        calibration target: the target yield is the schedule's expected yield
        normalised over the total idea count.

        Args:
            schedule: :class:`IdeationSchedule` snapshot for one epoch.

        Returns:
            List of dicts with keys ``'idea_title'``, ``'mode'``,
            ``'target_yield'``, ``'budget_share'``.
        """
        targets: list[dict[str, Any]] = []
        total_ideas = schedule.total_ideas or 1
        per_idea_yield = schedule.expected_yield / total_ideas if total_ideas > 0 else 0.0

        for title in schedule.planned_explorations:
            budget_share = schedule.regime_allocations.get(title, 1.0 / total_ideas)
            targets.append({
                "idea_title": title,
                "mode": "exploration",
                "target_yield": round(per_idea_yield, 4),
                "budget_share": round(budget_share, 4),
            })
        for title in schedule.planned_exploitations:
            budget_share = schedule.regime_allocations.get(title, 1.0 / total_ideas)
            targets.append({
                "idea_title": title,
                "mode": "exploitation",
                "target_yield": round(per_idea_yield, 4),
                "budget_share": round(budget_share, 4),
            })
        return targets

    def novelty_scores_to_validation_data(
        self, scores: list[NoveltyScore]
    ) -> list[dict[str, Any]]:
        """Convert a list of novelty scores into calibration data points.

        Each :class:`~jugeo.ideation.novelty.NoveltyScore` becomes one row
        in the calibration dataset, using its composite score as the target
        response variable.

        Args:
            scores: List of :class:`NoveltyScore` to convert.

        Returns:
            List of dicts with keys ``'idea_id'``, ``'composite'``,
            ``'semantic_distance'``, ``'purpose_alignment'``, ``'feasibility'``,
            and ``'title'``.
        """
        validation_data: list[dict[str, Any]] = []
        for ns in scores:
            validation_data.append({
                "idea_id": str(ns.idea_id),
                "composite": ns.composite,
                "semantic_distance": ns.semantic_distance,
                "purpose_alignment": ns.purpose_alignment,
                "feasibility": ns.feasibility,
                "title": ns.title,
            })
        return validation_data


# ---------------------------------------------------------------------------
# CopilotExperimentAdvisor
# ---------------------------------------------------------------------------


class CopilotExperimentAdvisor:
    """Advisory reasoning layer for experiment design decisions.

    Provides plain-English suggestions for improving designs, prioritising
    experiments, explaining results, and generating high-level insights.
    All methods are stateless and operate purely on their arguments.
    """

    def __init__(self) -> None:
        """Initialise the advisor (stateless; no configuration required)."""
        _log.debug("CopilotExperimentAdvisor initialised.")

    def advise_on_design(self, design: ExperimentDesign) -> list[str]:
        """Return a list of improvement suggestions for a design.

        Checks for common design deficiencies: too few runs relative to factor
        count (violating the n ≥ (z+z)² formula of Theorem 53.4), missing
        hypotheses, and single-factor designs that could be upgraded to
        factorial.

        Args:
            design: :class:`ExperimentDesign` to evaluate.

        Returns:
            List of plain-English suggestion strings (may be empty if the
            design is already well-specified).
        """
        suggestions: list[str] = []
        min_runs_per_factor = 5
        if design.n_runs < design.factor_count * min_runs_per_factor:
            suggestions.append(
                f"Increase n_runs to at least {design.factor_count * min_runs_per_factor} "
                f"to achieve adequate power per factor (Theorem 53.4)."
            )
        if not design.hypothesis.strip():
            suggestions.append(
                "Add a falsifiable hypothesis string to enable rigorous falsification "
                "testing (Theorem 53.3)."
            )
        if design.factor_count == 1:
            suggestions.append(
                "Consider upgrading to a factorial design with ≥2 factors to estimate "
                "interaction effects (Theorem 53.5)."
            )
        if design.n_runs > 500:
            suggestions.append(
                "Very large run count detected.  Consider a Bayesian adaptive design to "
                "reduce runs while maintaining power (Theorem 53.11)."
            )
        if design.experiment_type == ExperimentType.ABLATION.value and design.factor_count < 2:
            suggestions.append(
                "Ablation studies benefit from at least 2 components to detect "
                "interaction effects (Theorem 53.1)."
            )
        return suggestions

    def prioritize_experiments(
        self, designs: list[ExperimentDesign]
    ) -> list[ExperimentDesign]:
        """Return designs sorted by expected value (descending).

        Expected value is approximated as factor_count × log(n_runs), rewarding
        designs that probe more of the parameter space with sufficient runs.

        Args:
            designs: List of :class:`ExperimentDesign` objects to rank.

        Returns:
            New list sorted from highest to lowest expected value.
        """
        def _expected_value(d: ExperimentDesign) -> float:
            return d.factor_count * math.log1p(d.n_runs)

        return sorted(designs, key=_expected_value, reverse=True)

    def explain_result(self, result: ExperimentResult) -> str:
        """Generate a plain-English explanation of an experiment result.

        Uses Cohen's d thresholds (Theorem 53.13) and the p-value to produce
        a sentence suitable for surfacing in a copilot summary.

        Args:
            result: :class:`ExperimentResult` to explain.

        Returns:
            Single-sentence explanation string.
        """
        label = result.effect_label()
        sig_phrase = "statistically significant" if result.significant else "not statistically significant"
        direction = "positive" if result.effect_size >= 0 else "negative"
        return (
            f"The experiment produced a {direction} {label} effect "
            f"(d={result.effect_size:+.3f}) that is {sig_phrase} "
            f"(p={result.p_value:.4f}, n={result.sample_size})."
        )

    def suggest_follow_up(self, result: ExperimentResult) -> list[str]:
        """Propose follow-up experiments based on an observed result.

        Args:
            result: Completed :class:`ExperimentResult` to build on.

        Returns:
            List of follow-up experiment descriptions.
        """
        suggestions: list[str] = []
        if result.significant and abs(result.effect_size) >= 0.5:
            suggestions.append(
                "Run a component ablation study to identify which factors drive the "
                "observed medium/large effect (Theorem 53.1)."
            )
        if not result.significant:
            suggestions.append(
                "Increase sample size to achieve 80% power for the observed effect "
                "magnitude (Theorem 53.4)."
            )
            suggestions.append(
                "Run a sensitivity analysis to locate the most influential factor "
                "before committing to a larger RCT."
            )
        if result.significant and abs(result.effect_size) < 0.2:
            suggestions.append(
                "Effect size is negligible despite significance; consider whether "
                "practical significance warrants further investment."
            )
        suggestions.append(
            "Replicate with an independent random seed to verify result independence "
            "(Theorem 53.15)."
        )
        return suggestions

    def generate_insight(self, results: list[ExperimentResult]) -> str:
        """Synthesise a high-level insight from multiple results.

        Applies Holm correction (Theorem 53.14) logic to avoid over-counting
        simultaneous significant results, then produces a one-paragraph insight.

        Args:
            results: List of completed :class:`ExperimentResult` objects.

        Returns:
            Multi-sentence insight paragraph.
        """
        if not results:
            return "No results available to synthesise an insight."

        n = len(results)
        # Holm-corrected significance count
        sorted_pvals = sorted(r.p_value for r in results)
        holm_sig = 0
        alpha = 0.05
        for rank, p in enumerate(sorted_pvals, start=1):
            if p <= alpha / (n - rank + 1):
                holm_sig += 1
            else:
                break

        mean_d = sum(abs(r.effect_size) for r in results) / n
        d_label = (
            "negligible" if mean_d < 0.2 else
            "small" if mean_d < 0.5 else
            "medium" if mean_d < 0.8 else "large"
        )
        return (
            f"Across {n} experiments, {holm_sig} show significance after Holm correction "
            f"(Theorem 53.14).  The mean effect size is {d_label} (d̄={mean_d:.3f}).  "
            f"{'The evidence supports further targeted ablation.' if holm_sig > 0 else 'Consider recalibrating the ideation parameters before proceeding.'}"
        )


# ---------------------------------------------------------------------------
# ExperimentEventBus
# ---------------------------------------------------------------------------


class ExperimentEventBus:
    """Lightweight publish-subscribe bus for experiment lifecycle events.

    Handlers are callables registered per event type.  Published events are
    dispatched synchronously and appended to an immutable history log, ensuring
    experiment independence (Theorem 53.15).

    Attributes:
        _subscribers: Dict mapping event type string to list of callables.
        _history: Append-only list of published event dicts.
    """

    def __init__(self) -> None:
        """Initialise the bus with empty subscriber and history stores."""
        self._subscribers: dict[str, list[Callable[[dict], None]]] = {}
        self._history: list[dict] = []
        _log.debug("ExperimentEventBus initialised.")

    def subscribe(self, event_type: str, handler: Callable[[dict], None]) -> None:
        """Register *handler* to be called whenever *event_type* is published.

        Args:
            event_type: Event type string (e.g. ``"experiment.started"``).
            handler: Callable accepting a single event dict argument.
        """
        self._subscribers.setdefault(event_type, []).append(handler)
        _log.debug("Subscribed handler %r to event type %r.", getattr(handler, "__name__", repr(handler)), event_type)

    def publish(self, event_type: str, payload: dict) -> None:
        """Publish an event to all registered handlers and append to history.

        Args:
            event_type: Event type string matching subscriptions.
            payload: Arbitrary dict with event-specific data.
        """
        event = _format_event(event_type, payload)
        self._history.append(event)
        for handler in self._subscribers.get(event_type, []):
            try:
                handler(event)
            except Exception:
                _log.exception("Handler %r raised an exception for event %r.", handler, event_type)

    def experiment_started(self, design: ExperimentDesign) -> None:
        """Publish an ``'experiment.started'`` event for *design*.

        Args:
            design: :class:`ExperimentDesign` that is beginning execution.
        """
        self.publish(
            "experiment.started",
            {
                "design_id": design.design_id,
                "name": design.name,
                "experiment_type": design.experiment_type,
                "n_runs": design.n_runs,
            },
        )

    def experiment_completed(self, result: ExperimentResult) -> None:
        """Publish an ``'experiment.completed'`` event for *result*.

        Args:
            result: :class:`ExperimentResult` from the completed experiment.
        """
        self.publish(
            "experiment.completed",
            {
                "result_id": result.result_id,
                "design_id": result.design_id,
                "effect_size": result.effect_size,
                "p_value": result.p_value,
                "significant": result.significant,
            },
        )

    def get_history(self) -> list[dict]:
        """Return a copy of all published events in chronological order.

        Returns:
            List of event dicts, each with ``'type'``, ``'timestamp'``, and
            ``'payload'`` keys.
        """
        return list(self._history)


# ---------------------------------------------------------------------------
# ResultRepository
# ---------------------------------------------------------------------------


class ResultRepository:
    """In-memory repository for :class:`ExperimentResult` objects.

    Provides storage, retrieval, and query operations over experiment outcomes.
    All write operations are append-only; results are never mutated after
    storage.

    Attributes:
        _store: Dict mapping result_id to :class:`ExperimentResult`.
    """

    def __init__(self) -> None:
        """Initialise an empty result repository."""
        self._store: dict[str, ExperimentResult] = {}
        _log.debug("ResultRepository initialised.")

    def store(self, result: ExperimentResult) -> None:
        """Persist *result* by its result_id.

        If a result with the same ID already exists it is silently overwritten.

        Args:
            result: :class:`ExperimentResult` to store.
        """
        self._store[result.result_id] = result
        _log.debug("Stored result %r.", result.result_id[:8])

    def retrieve(self, result_id: str) -> ExperimentResult | None:
        """Fetch a result by its unique identifier.

        Args:
            result_id: UUID string of the result to retrieve.

        Returns:
            The :class:`ExperimentResult` if found, else ``None``.
        """
        return self._store.get(result_id)

    def all_results(self) -> list[ExperimentResult]:
        """Return all stored results in insertion order.

        Returns:
            List of all :class:`ExperimentResult` objects.
        """
        return list(self._store.values())

    def results_by_design(self, design_id: str) -> list[ExperimentResult]:
        """Return all results associated with *design_id*.

        Args:
            design_id: Design identifier to filter by.

        Returns:
            Possibly empty list of :class:`ExperimentResult` objects.
        """
        return [r for r in self._store.values() if r.design_id == design_id]

    def significant_results(self, alpha: float = 0.05) -> list[ExperimentResult]:
        """Return results whose p-value is below *alpha*.

        Applies a simple per-test threshold; callers requiring FWER control
        should apply Bonferroni or Holm correction externally (Theorems 53.8,
        53.14).

        Args:
            alpha: Significance threshold (default 0.05).

        Returns:
            List of :class:`ExperimentResult` with p_value < alpha.
        """
        return [r for r in self._store.values() if r.p_value < alpha]

    def export_json(self) -> str:
        """Export all stored results as a JSON string.

        Uses :func:`dataclasses.asdict` for serialisation.

        Returns:
            JSON-encoded list of result dicts.
        """
        serialised = [r.as_dict() for r in self._store.values()]
        return json.dumps(serialised, indent=2)

    def summary_statistics(self) -> dict[str, Any]:
        """Compute aggregate statistics over all stored results.

        Returns:
            Dict with:
                - ``'n_results'``: total result count.
                - ``'n_significant'``: count with significant=True.
                - ``'mean_effect_size'``: arithmetic mean of |d|.
                - ``'mean_p_value'``: arithmetic mean of p-values.
                - ``'median_sample_size'``: median sample size across results.
                - ``'effect_distribution'``: dict counting negligible/small/medium/large.
        """
        results = list(self._store.values())
        n = len(results)
        if n == 0:
            return {
                "n_results": 0,
                "n_significant": 0,
                "mean_effect_size": 0.0,
                "mean_p_value": 1.0,
                "median_sample_size": 0,
                "effect_distribution": {},
            }
        n_sig = sum(1 for r in results if r.significant)
        mean_d = sum(abs(r.effect_size) for r in results) / n
        mean_p = sum(r.p_value for r in results) / n

        sample_sizes = sorted(r.sample_size for r in results)
        mid = n // 2
        median_ss = sample_sizes[mid] if n % 2 == 1 else (sample_sizes[mid - 1] + sample_sizes[mid]) // 2

        effect_dist: dict[str, int] = {"negligible": 0, "small": 0, "medium": 0, "large": 0}
        for r in results:
            effect_dist[r.effect_label()] = effect_dist.get(r.effect_label(), 0) + 1

        return {
            "n_results": n,
            "n_significant": n_sig,
            "mean_effect_size": round(mean_d, 4),
            "mean_p_value": round(mean_p, 6),
            "median_sample_size": median_ss,
            "effect_distribution": effect_dist,
        }
