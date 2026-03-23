"""
Ablation Philosophy — How to Remove Components Meaningfully.

This module implements the ablation study design framework for the JuGeo
Evaluation Design subsystem (theory2.tex Ch72 §2). Ablation studies
measure the contribution of each system component by systematically
removing it and measuring the resulting metric drop.

Core philosophy modelled here:
  SURGICAL_ABLATION   — Remove exactly one component, hold all others fixed.
  CAUSAL_ABLATION     — Remove a component and all causally downstream effects.
  ADDITIVE_ABLATION   — Start from the null system and add components one by one.
  COUNTERFACTUAL      — Replace a component with a null baseline rather than
                        removing it, to avoid confounds from architecture change.

The AblationPhilosophyCoordinator designs the ablation schedule.
The AblationPhilosophyAnalyzer interprets ablation deltas.
The AblationPhilosophyWitness records the ablation study audit trail.

copilot: ablation-philosophy marker
theory2.tex Ch72 §2 — Ablation Philosophy
"""

from __future__ import annotations

import math
import uuid
import statistics
import itertools
import functools
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, auto
from typing import Callable, Optional, Sequence

try:
    from jugeo.evaluation.evaluation_design.project_scale_metrics import (
        ProjectScorecard,
    )
except ImportError:
    ProjectScorecard = None  # type: ignore

try:
    from jugeo.config import JugeoConfig  # type: ignore
except ImportError:
    JugeoConfig = None  # type: ignore


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

SIGNIFICANCE_THRESHOLD_P: float = 0.05
"""P-value below which an ablation delta is declared statistically significant.

Follows standard academic convention.  For highly exploratory ablation studies
one may relax this to 0.10, but 0.05 is the module default.
"""

MIN_RELATIVE_DELTA: float = 0.02
"""Minimum relative metric delta (2 %) considered practically meaningful.

Even if an ablation reaches statistical significance, a relative drop
smaller than MIN_RELATIVE_DELTA is treated as negligible in all ranking
and reporting logic.
"""

MAX_ABLATION_TARGETS: int = 32
"""Hard upper bound on the number of ablation targets in a single schedule.

Ablation studies are expensive to run.  Schedules exceeding this limit
are rejected by the coordinator to prevent runaway compute usage.
"""

DEFAULT_NULL_BASELINE: str = "zero-output-baseline"
"""Name of the default null-baseline component used in COUNTERFACTUAL ablations.

A null baseline produces empty / zero outputs without crashing, ensuring
that the rest of the system can still be evaluated after the replacement.
"""

COUNTERFACTUAL_SUFFIX: str = "__NULL"
"""Suffix appended to a component name when it is replaced by the null baseline."""

ADDITIVE_ORDERING_HEURISTIC: str = "greedy-marginal-gain"
"""Default ordering heuristic for ADDITIVE_ABLATION schedules.

In greedy-marginal-gain ordering, components are added in the order that
maximises the marginal metric gain at each step, computed from a pilot
run over the full system.
"""

REPORT_VERSION: str = "1.0.0"
"""Version tag stamped into every AblationPhilosophyWitness study report."""

_MISSING_FLOAT: float = float("nan")
"""Sentinel float value used when a metric measurement is unavailable."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    """Return the current UTC time as a timezone-aware datetime object.

    This wrapper centralises the call so that tests can monkeypatch it
    without touching the standard library directly.

    Returns:
        A timezone-aware ``datetime`` in UTC.

    Example:
        >>> ts = _utcnow()
        >>> ts.tzinfo is not None
        True
    """
    return datetime.now(tz=timezone.utc)


def _uid() -> str:
    """Generate a compact unique identifier string.

    The identifier is derived from a UUID4 and shortened to 12 hex
    characters, trading theoretical uniqueness for legibility.

    Returns:
        A 12-character lowercase hex string.

    Example:
        >>> uid = _uid()
        >>> len(uid)
        12
    """
    return uuid.uuid4().hex[:12]


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp *value* to the closed interval [lo, hi].

    Args:
        value: The floating-point value to clamp.
        lo: Lower bound (inclusive).
        hi: Upper bound (inclusive).

    Returns:
        The clamped value, always satisfying ``lo <= result <= hi``.

    Raises:
        ValueError: If ``lo > hi``.

    Example:
        >>> _clamp(1.5, 0.0, 1.0)
        1.0
        >>> _clamp(-0.3, 0.0, 1.0)
        0.0
    """
    if lo > hi:
        raise ValueError(f"_clamp: lower bound {lo} exceeds upper bound {hi}")
    return max(lo, min(hi, value))


def _relative_delta(before: float, after: float) -> float:
    """Compute the relative metric change from *before* to *after*.

    The relative delta is defined as ``(before - after) / |before|``, which
    is positive when the metric drops (component was helpful) and negative
    when the metric rises (component was harmful or redundant).

    Args:
        before: Metric value with the component present.
        after:  Metric value with the component absent or replaced.

    Returns:
        A float representing the fractional change, in ``(-∞, +∞)``.
        Returns 0.0 when *before* is exactly zero to avoid division by zero.

    Example:
        >>> round(_relative_delta(0.80, 0.72), 4)
        0.1
    """
    if before == 0.0:
        return 0.0
    return (before - after) / abs(before)


def _approximate_p_value(delta: float, std_err: float) -> float:
    """Approximate a two-tailed p-value for an observed metric delta.

    Uses a normal-distribution approximation.  This is not a rigorous
    statistical test — it is intended for fast, within-module significance
    screening.  Full studies should use a proper bootstrap or permutation
    test.

    Args:
        delta:   Observed metric delta (before − after).
        std_err: Standard error of the delta estimate.

    Returns:
        An approximate p-value in [0, 1].  Returns 1.0 when std_err is zero
        or non-positive to indicate complete uncertainty.

    Example:
        >>> p = _approximate_p_value(0.1, 0.03)
        >>> p < 0.05
        True
    """
    if std_err <= 0.0:
        return 1.0
    z = abs(delta) / std_err
    # Rational approximation to the standard-normal CDF tail
    p_one_tail = 0.5 * math.erfc(z / math.sqrt(2))
    return _clamp(2.0 * p_one_tail, 0.0, 1.0)


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class AblationMode(str, Enum):
    """Enumeration of the four canonical ablation philosophies.

    Each member encodes a different assumption about what it means to
    "remove" a component from a system.  Choosing the wrong mode can
    invalidate an entire study, so the coordinator enforces explicit mode
    selection at schedule creation time.

    The four modes correspond to distinct causal assumptions:

    * SURGICAL isolates a single variable under ceteris-paribus conditions.
    * CAUSAL propagates the removal through the causal graph.
    * ADDITIVE builds understanding bottom-up.
    * COUNTERFACTUAL avoids architecture-change confounds.
    """

    SURGICAL = "surgical"
    """Remove exactly one component while all other components remain unchanged.

    This is the standard ablation design in ML papers.  It answers the
    question: "How much does this one component contribute, given that
    everything else is present?"  The assumption is that components are
    roughly independent; when strong interactions exist, CAUSAL mode is
    preferable.
    """

    CAUSAL = "causal"
    """Remove a component together with every component causally downstream of it.

    Used when the system has a directed acyclic dependency graph and
    removing an upstream component would leave downstream components in an
    undefined or meaningless state.  The resulting delta reflects the
    entire sub-tree contribution, not just the removed node.
    """

    ADDITIVE = "additive"
    """Start from the null (empty) system and add components one at a time.

    Each measurement records the marginal gain of the newly added
    component on top of all previously added components.  The ordering of
    addition matters and should be motivated by the research question.
    ADDITIVE mode is useful when baseline zero-output behaviour is
    well-defined and the researcher wants to build an understanding of
    synergies.
    """

    COUNTERFACTUAL = "counterfactual"
    """Replace the target component with an inert null-baseline component.

    Unlike SURGICAL, which leaves a structural hole in the pipeline,
    COUNTERFACTUAL substitutes a null placeholder so that the downstream
    components receive valid (but trivial) inputs.  This avoids confounds
    caused by broken data flows and is the recommended mode when
    components have mandatory predecessors.
    """


DEFAULT_ABLATION_MODE: AblationMode = AblationMode.SURGICAL
"""Module-level default ablation mode used by the coordinator unless overridden."""


class AblationStatus(str, Enum):
    """Lifecycle states for an ablation study or individual ablation target.

    Transitions follow the directed path::

        DESIGNED → RUNNING → COMPLETED
                           ↘ INCONCLUSIVE
                     ↘ INVALID

    INVALID may be reached from any state if a configuration error is
    discovered.
    """

    DESIGNED = "designed"
    """The ablation has been scheduled but execution has not begun."""

    RUNNING = "running"
    """Execution is in progress; partial results may be available."""

    COMPLETED = "completed"
    """Execution finished and a valid result was recorded."""

    INCONCLUSIVE = "inconclusive"
    """Execution finished but the result could not be interpreted reliably.

    Typical causes: high variance in the metric function, degenerate
    ablation targets, or numerically unstable delta estimates.
    """

    INVALID = "invalid"
    """The ablation target or schedule was found to be malformed.

    Examples: missing component, circular causal dependency, or a null
    baseline that crashes the downstream pipeline.
    """


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AblationTarget:
    """A single component to be removed or replaced during an ablation study.

    An AblationTarget packages everything the execution engine needs to
    carry out one ablation experiment: which component to touch
    (``component_name``), how to touch it (``removal_strategy``), what to
    substitute when using COUNTERFACTUAL mode (``null_baseline``), an
    a-priori estimate of the expected metric drop
    (``estimated_delta``), and the list of components whose outputs
    depend on this one (``causal_dependents``).

    The ``estimated_delta`` is populated from pilot experiments or
    domain knowledge before the study runs and is used to prioritise
    the ablation schedule; it does not affect the analysis of actual
    results.

    The ``causal_dependents`` tuple lists component IDs (not names) that
    must also be removed when operating in CAUSAL mode.  For SURGICAL
    and COUNTERFACTUAL modes this field is ignored but still recorded
    for documentation purposes.

    Attributes:
        target_id:          Unique identifier for this target within its schedule.
        component_name:     Human-readable name of the component being ablated.
        removal_strategy:   The AblationMode to apply for this specific target.
        null_baseline:      Name of the null-baseline component for COUNTERFACTUAL.
        estimated_delta:    Prior estimate of metric drop (positive = helpful component).
        causal_dependents:  IDs of components downstream in the causal graph.
    """

    target_id: str
    component_name: str
    removal_strategy: AblationMode
    null_baseline: str
    estimated_delta: float
    causal_dependents: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AblationResult:
    """The outcome of executing one ablation target.

    An AblationResult is produced after the metric function has been
    evaluated both with and without the target component.  It stores the
    raw before/after measurements, the derived delta and relative delta,
    the ablation mode that was applied, significance flags, and a
    timestamp for audit purposes.

    The ``is_significant`` flag combines statistical significance
    (``p_value < SIGNIFICANCE_THRESHOLD_P``) with practical significance
    (``abs(relative_delta) >= MIN_RELATIVE_DELTA``).  Both conditions
    must hold for the flag to be True.

    Attributes:
        target_id:      Matches the corresponding AblationTarget.target_id.
        metric_before:  Metric value with the component present.
        metric_after:   Metric value with the component removed / replaced.
        delta:          Raw numeric difference (before - after).
        relative_delta: Fractional change, see :func:`_relative_delta`.
        mode:           The AblationMode used for this result.
        is_significant: True iff both statistical and practical thresholds pass.
        p_value:        Approximate p-value from :func:`_approximate_p_value`.
        timestamp:      UTC datetime when the result was recorded.
    """

    target_id: str
    metric_before: float
    metric_after: float
    delta: float
    relative_delta: float
    mode: AblationMode
    is_significant: bool
    p_value: float
    timestamp: datetime


@dataclass(frozen=True, slots=True)
class AblationSchedule:
    """An ordered collection of ablation targets to be executed sequentially.

    A schedule is designed by the AblationPhilosophyAnalyzer and executed
    by the AblationPhilosophyCoordinator.  The ``ordering`` tuple contains
    the target_ids in the intended execution order, which may differ from
    the insertion order of ``targets`` (e.g., for ADDITIVE mode where
    ordering is set by marginal-gain heuristics).

    A schedule is immutable once created; revisions require creating a new
    schedule with an updated schedule_id.

    Attributes:
        schedule_id:  Unique identifier for this schedule.
        targets:      All AblationTargets in this study, in arbitrary order.
        ordering:     Execution order as a tuple of target_id strings.
        mode:         The dominant AblationMode for this schedule.
        created_at:   UTC datetime when the schedule was created.
    """

    schedule_id: str
    targets: tuple[AblationTarget, ...]
    ordering: tuple[str, ...]
    mode: AblationMode
    created_at: datetime


# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------


class AblationPhilosophyAnalyzer:
    """Designs ablation schedules and interprets their results.

    The analyzer is the intellectual core of the ablation framework.  It
    takes a list of component descriptions, applies the chosen ablation
    philosophy, and produces an executable AblationSchedule.  After
    execution it interprets individual results (``interpret_delta``),
    ranks components by contribution (``rank_components``), and flags
    potential confounds (``detect_confounds``).

    The analyzer is stateless — it does not store any results internally.
    All outputs are pure functions of the inputs.  State is managed by
    the Coordinator and Witness classes.

    Typical usage::

        analyzer = AblationPhilosophyAnalyzer()
        schedule = analyzer.design_schedule(["embedder", "ranker", "reranker"],
                                            AblationMode.SURGICAL)
        # ... run study ...
        ranking = analyzer.rank_components(results)
    """

    # ------------------------------------------------------------------
    def design_schedule(
        self,
        components: Sequence[str],
        mode: AblationMode = DEFAULT_ABLATION_MODE,
    ) -> AblationSchedule:
        """Create an AblationSchedule for the given component names.

        For SURGICAL, CAUSAL, and COUNTERFACTUAL modes each component
        yields exactly one target.  For ADDITIVE mode the single target
        list is identical but the ordering reflects cumulative addition
        rather than subtraction.

        The estimated_delta for each target is initialised to 0.0 and
        should be updated from pilot data before execution.

        Args:
            components: Ordered sequence of component name strings.
            mode:       AblationMode governing how targets are created.

        Returns:
            A fully populated AblationSchedule ready for execution.

        Raises:
            ValueError: If ``components`` is empty or contains duplicates.
            ValueError: If ``len(components) > MAX_ABLATION_TARGETS``.

        Example:
            >>> analyzer = AblationPhilosophyAnalyzer()
            >>> sched = analyzer.design_schedule(["a", "b", "c"], AblationMode.SURGICAL)
            >>> len(sched.targets)
            3
        """
        # copilot: validate component list before building targets
        if not components:
            raise ValueError("design_schedule: component list is empty")
        if len(set(components)) != len(components):
            raise ValueError("design_schedule: duplicate component names detected")
        if len(components) > MAX_ABLATION_TARGETS:
            raise ValueError(
                f"design_schedule: {len(components)} components exceeds "
                f"MAX_ABLATION_TARGETS={MAX_ABLATION_TARGETS}"
            )

        targets: list[AblationTarget] = []
        for comp in components:
            null_bl = comp + COUNTERFACTUAL_SUFFIX if mode == AblationMode.COUNTERFACTUAL else DEFAULT_NULL_BASELINE
            targets.append(
                AblationTarget(
                    target_id=_uid(),
                    component_name=comp,
                    removal_strategy=mode,
                    null_baseline=null_bl,
                    estimated_delta=0.0,
                    causal_dependents=(),
                )
            )

        # copilot: for ADDITIVE mode reverse ordering to represent bottom-up addition
        if mode == AblationMode.ADDITIVE:
            ordering = tuple(t.target_id for t in reversed(targets))
        else:
            ordering = tuple(t.target_id for t in targets)

        return AblationSchedule(
            schedule_id=_uid(),
            targets=tuple(targets),
            ordering=ordering,
            mode=mode,
            created_at=_utcnow(),
        )

    # ------------------------------------------------------------------
    def interpret_delta(self, result: AblationResult) -> str:
        """Produce a human-readable interpretation of an ablation result.

        The interpretation classifies the component as critical, helpful,
        neutral, harmful, or redundant based on the sign and magnitude of
        the relative delta, and states whether the result is statistically
        significant.

        Args:
            result: A completed AblationResult with valid metric values.

        Returns:
            A multi-sentence string interpretation suitable for a report.

        Raises:
            ValueError: If ``result.metric_before`` is NaN (no baseline).

        Example:
            >>> r = AblationResult("t1", 0.8, 0.6, 0.2, 0.25, AblationMode.SURGICAL,
            ...                    True, 0.01, _utcnow())
            >>> "critical" in AblationPhilosophyAnalyzer().interpret_delta(r)
            True
        """
        # copilot: guard against missing baseline values
        if math.isnan(result.metric_before):
            raise ValueError("interpret_delta: metric_before is NaN — no baseline available")

        rel = result.relative_delta
        sig_tag = "statistically significant" if result.is_significant else "not statistically significant"
        p_tag = f"p≈{result.p_value:.4f}"

        if rel >= 0.20:
            role = "a critical contributor"
            advice = "Removing this component causes a major performance collapse."
        elif rel >= 0.10:
            role = "a strongly helpful component"
            advice = "This component provides substantial metric gains and should be retained."
        elif rel >= MIN_RELATIVE_DELTA:
            role = "a moderately helpful component"
            advice = "This component provides a modest but real improvement."
        elif rel > -MIN_RELATIVE_DELTA:
            role = "a neutral component"
            advice = "Removing this component has negligible effect; consider simplifying the system."
        elif rel > -0.10:
            role = "a mildly harmful component"
            advice = "This component slightly degrades performance and warrants review."
        else:
            role = "a harmful component"
            advice = "Removing this component actually improves performance; investigate its design."

        return (
            f"Component '{result.target_id}' is {role} "
            f"(Δ={result.delta:+.4f}, rel={rel:+.2%}, {sig_tag}, {p_tag}).  "
            f"{advice}"
        )

    # ------------------------------------------------------------------
    def rank_components(self, results: list[AblationResult]) -> list[AblationResult]:
        """Rank ablation results by descending absolute relative delta.

        Components that cause the largest relative metric drop when removed
        are ranked first, signalling highest contribution to system
        performance.  Only significant results participate in the ranking
        by default; non-significant results are appended at the end in
        arbitrary order.

        Args:
            results: List of AblationResult objects from a completed study.

        Returns:
            A new list sorted by ``abs(relative_delta)`` descending,
            significant results first.

        Raises:
            ValueError: If *results* is empty.

        Example:
            >>> ranked = analyzer.rank_components(results)
            >>> ranked[0].relative_delta >= ranked[-1].relative_delta
            True
        """
        if not results:
            raise ValueError("rank_components: results list is empty")

        # copilot: split into significant and non-significant pools before sorting
        significant = [r for r in results if r.is_significant]
        non_significant = [r for r in results if not r.is_significant]

        # Sort each pool by absolute relative delta descending
        significant_sorted = sorted(
            significant, key=lambda r: abs(r.relative_delta), reverse=True
        )
        non_significant_sorted = sorted(
            non_significant, key=lambda r: abs(r.relative_delta), reverse=True
        )

        return significant_sorted + non_significant_sorted

    # ------------------------------------------------------------------
    def detect_confounds(self, results: list[AblationResult]) -> list[str]:
        """Identify potential confounding factors in an ablation study.

        Confounds are detected via three heuristic checks:

        1. **Sign inconsistency**: Two ablations in the same study have
           opposite-signed deltas while using the same mode — suggests
           interaction effects that SURGICAL mode cannot capture.
        2. **Variance spike**: One ablation result has a relative delta
           more than 3 standard deviations above the mean, which can
           indicate measurement noise rather than a real contribution.
        3. **Near-zero baseline**: Any result where ``metric_before < 1e-6``
           invalidates the relative-delta calculation.

        Args:
            results: List of AblationResult objects to inspect.

        Returns:
            A list of human-readable confound warning strings.
            An empty list indicates no confounds were detected.

        Raises:
            ValueError: If *results* is empty.

        Example:
            >>> warnings = analyzer.detect_confounds(results)
            >>> isinstance(warnings, list)
            True
        """
        if not results:
            raise ValueError("detect_confounds: results list is empty")

        warnings: list[str] = []

        # copilot: check sign inconsistency within same ablation mode
        for mode in AblationMode:
            mode_results = [r for r in results if r.mode == mode]
            if len(mode_results) < 2:
                continue
            signs = {("pos" if r.delta >= 0 else "neg") for r in mode_results}
            if len(signs) > 1:
                warnings.append(
                    f"Sign inconsistency detected among {mode.value} ablations: "
                    "some components appear helpful and others harmful — "
                    "interaction effects may be present."
                )

        # copilot: detect variance spikes using z-score on relative deltas
        rel_deltas = [r.relative_delta for r in results]
        if len(rel_deltas) >= 3:
            mean_rd = statistics.mean(rel_deltas)
            stdev_rd = statistics.stdev(rel_deltas)
            if stdev_rd > 0:
                for r in results:
                    z = (r.relative_delta - mean_rd) / stdev_rd
                    if abs(z) > 3.0:
                        warnings.append(
                            f"Variance spike: target '{r.target_id}' has z-score "
                            f"{z:.2f} on relative delta — possible measurement noise."
                        )

        # copilot: warn on near-zero baseline values
        for r in results:
            if abs(r.metric_before) < 1e-6:
                warnings.append(
                    f"Near-zero baseline for target '{r.target_id}' "
                    f"(metric_before={r.metric_before}): relative delta is unreliable."
                )

        return warnings


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------


class AblationPhilosophyCoordinator:
    """Orchestrates the full lifecycle of an ablation study.

    The coordinator holds references to an analyzer (for schedule design
    and result interpretation) and a witness (for audit recording).  It
    exposes a high-level API that covers the entire study lifecycle: plan
    → simulate → run → summarize.

    The ``simulate_result`` method enables dry-run testing: given an
    AblationTarget and a callable metric function, it evaluates the metric
    before and after the ablation and wraps the outcome in an
    AblationResult.  The metric function accepts a dict of active
    components and returns a float score.

    Attributes:
        _analyzer:    The AblationPhilosophyAnalyzer used by this coordinator.
        _witness:     The AblationPhilosophyWitness receiving result observations.
        _schedules:   Cache of all schedules produced in this session.
        _results:     Cache of all results produced in this session.
    """

    def __init__(
        self,
        analyzer: Optional[AblationPhilosophyAnalyzer] = None,
        witness: Optional["AblationPhilosophyWitness"] = None,
    ) -> None:
        """Initialise the coordinator with an optional analyzer and witness.

        If *analyzer* or *witness* are None, default instances are created
        automatically.

        Args:
            analyzer: Pre-constructed AblationPhilosophyAnalyzer, or None.
            witness:  Pre-constructed AblationPhilosophyWitness, or None.

        Example:
            >>> coord = AblationPhilosophyCoordinator()
        """
        # copilot: default-construct collaborators if not provided
        self._analyzer: AblationPhilosophyAnalyzer = analyzer or AblationPhilosophyAnalyzer()
        self._witness: AblationPhilosophyWitness = witness or AblationPhilosophyWitness()
        self._schedules: list[AblationSchedule] = []
        self._results: list[AblationResult] = []

    # ------------------------------------------------------------------
    def plan(
        self,
        components: Sequence[str],
        mode: AblationMode = DEFAULT_ABLATION_MODE,
    ) -> AblationSchedule:
        """Design and cache an ablation schedule.

        Delegates to the analyzer and caches the resulting schedule for
        later retrieval.

        Args:
            components: List of component names to ablate.
            mode:       Ablation philosophy to apply.

        Returns:
            A new AblationSchedule.

        Raises:
            ValueError: Propagated from :meth:`AblationPhilosophyAnalyzer.design_schedule`.

        Example:
            >>> coord = AblationPhilosophyCoordinator()
            >>> sched = coord.plan(["alpha", "beta"], AblationMode.COUNTERFACTUAL)
            >>> sched.mode == AblationMode.COUNTERFACTUAL
            True
        """
        schedule = self._analyzer.design_schedule(components, mode)
        self._schedules.append(schedule)
        return schedule

    # ------------------------------------------------------------------
    def simulate_result(
        self,
        target: AblationTarget,
        metric_fn: Callable[[dict[str, bool]], float],
    ) -> AblationResult:
        """Simulate a single ablation by invoking the metric function twice.

        The metric function receives a dict mapping component names to
        booleans indicating whether each component is active.  For the
        *before* evaluation all components are active.  For the *after*
        evaluation the target component (and its causal dependents, if
        any) is disabled.

        A standard error of 5 % of the absolute delta is assumed for the
        approximate p-value computation.

        Args:
            target:    The AblationTarget describing what to remove.
            metric_fn: A callable ``(active_map) → float`` returning the
                       system metric.

        Returns:
            A populated AblationResult.

        Raises:
            RuntimeError: If metric_fn raises an exception.

        Example:
            >>> def fake_metric(m): return 1.0 if m.get("x") else 0.5
            >>> coord = AblationPhilosophyCoordinator()
            >>> t = AblationTarget("t1","x",AblationMode.SURGICAL,"x__NULL",0.5,())
            >>> r = coord.simulate_result(t, fake_metric)
            >>> r.delta > 0
            True
        """
        # copilot: build active maps for before and after states
        # "before": every component is active
        active_before: dict[str, bool] = {}
        active_before[target.component_name] = True
        for dep in target.causal_dependents:
            active_before[dep] = True

        active_after = dict(active_before)
        active_after[target.component_name] = False
        if target.removal_strategy == AblationMode.CAUSAL:
            for dep in target.causal_dependents:
                active_after[dep] = False

        try:
            before = float(metric_fn(active_before))
            after = float(metric_fn(active_after))
        except Exception as exc:
            raise RuntimeError(
                f"simulate_result: metric_fn raised an exception for "
                f"target '{target.target_id}': {exc}"
            ) from exc

        delta = before - after
        rel = _relative_delta(before, after)
        std_err = abs(delta) * 0.05 if delta != 0 else 1.0
        p_val = _approximate_p_value(delta, std_err)
        is_sig = p_val < SIGNIFICANCE_THRESHOLD_P and abs(rel) >= MIN_RELATIVE_DELTA

        return AblationResult(
            target_id=target.target_id,
            metric_before=before,
            metric_after=after,
            delta=delta,
            relative_delta=rel,
            mode=target.removal_strategy,
            is_significant=is_sig,
            p_value=p_val,
            timestamp=_utcnow(),
        )

    # ------------------------------------------------------------------
    def run_study(
        self,
        schedule: AblationSchedule,
        metric_fn: Callable[[dict[str, bool]], float],
    ) -> list[AblationResult]:
        """Execute all targets in a schedule and return results.

        Targets are executed in the order specified by ``schedule.ordering``.
        Each result is passed to the witness before being appended to the
        return list.

        Args:
            schedule:  An AblationSchedule produced by :meth:`plan`.
            metric_fn: A callable ``(active_map) → float``.

        Returns:
            A list of AblationResult objects in execution order.

        Raises:
            KeyError: If a target_id in ``ordering`` has no matching target.

        Example:
            >>> results = coord.run_study(schedule, metric_fn)
            >>> len(results) == len(schedule.targets)
            True
        """
        # copilot: build a lookup map from target_id to AblationTarget
        target_map: dict[str, AblationTarget] = {
            t.target_id: t for t in schedule.targets
        }

        results: list[AblationResult] = []
        for tid in schedule.ordering:
            if tid not in target_map:
                raise KeyError(
                    f"run_study: ordering references unknown target_id '{tid}'"
                )
            result = self.simulate_result(target_map[tid], metric_fn)
            self._witness.observe(result)
            self._results.append(result)
            results.append(result)

        return results

    # ------------------------------------------------------------------
    def summarize(self, results: list[AblationResult]) -> dict:
        """Produce a summary dictionary from a list of ablation results.

        The summary includes aggregate statistics, component rankings, and
        confound warnings produced by the analyzer.

        Args:
            results: List of AblationResult objects to summarize.

        Returns:
            A dict with keys: ``n_targets``, ``n_significant``,
            ``mean_relative_delta``, ``top_contributor``,
            ``confound_warnings``, ``ranking`` (list of target_ids).

        Raises:
            ValueError: If *results* is empty.

        Example:
            >>> summary = coord.summarize(results)
            >>> "n_targets" in summary
            True
        """
        if not results:
            raise ValueError("summarize: results list is empty")

        ranked = self._analyzer.rank_components(results)
        confounds = self._analyzer.detect_confounds(results)
        rel_deltas = [r.relative_delta for r in results]
        mean_rd = statistics.mean(rel_deltas)

        return {
            "n_targets": len(results),
            "n_significant": sum(1 for r in results if r.is_significant),
            "mean_relative_delta": round(mean_rd, 6),
            "top_contributor": ranked[0].target_id if ranked else None,
            "confound_warnings": confounds,
            "ranking": [r.target_id for r in ranked],
            "report_version": REPORT_VERSION,
        }


# ---------------------------------------------------------------------------
# Witness
# ---------------------------------------------------------------------------


class AblationPhilosophyWitness:
    """Observes and records all ablation results for audit and reporting.

    The witness pattern separates result storage and querying from
    execution logic.  Every AblationResult produced by the coordinator is
    forwarded to the witness, which accumulates a complete log for the
    session.  Post-hoc queries (insignificant ablations, top contributors,
    study report) all operate on this log.

    The witness is intentionally lightweight and performs no analysis
    beyond simple filtering and sorting.

    Attributes:
        _log: Internal ordered list of observed AblationResult objects.
    """

    def __init__(self) -> None:
        """Initialise an empty witness log.

        Example:
            >>> w = AblationPhilosophyWitness()
            >>> w.insignificant_ablations()
            []
        """
        self._log: list[AblationResult] = []

    # ------------------------------------------------------------------
    def observe(self, result: AblationResult) -> None:
        """Append an AblationResult to the witness log.

        Args:
            result: The AblationResult to record.

        Returns:
            None.

        Raises:
            TypeError: If *result* is not an AblationResult.

        Example:
            >>> w = AblationPhilosophyWitness()
            >>> w.observe(some_result)
            >>> len(w._log)
            1
        """
        if not isinstance(result, AblationResult):
            raise TypeError(
                f"observe: expected AblationResult, got {type(result).__name__}"
            )
        # copilot: append to append-only log
        self._log.append(result)

    # ------------------------------------------------------------------
    def insignificant_ablations(self) -> list[AblationResult]:
        """Return all observed results that did not reach significance.

        An ablation is insignificant when either its p-value is above the
        threshold or its absolute relative delta is below MIN_RELATIVE_DELTA.
        These results are candidates for pruning from future studies.

        Returns:
            A list of AblationResult objects with ``is_significant == False``.

        Example:
            >>> insig = w.insignificant_ablations()
            >>> all(not r.is_significant for r in insig)
            True
        """
        return [r for r in self._log if not r.is_significant]

    # ------------------------------------------------------------------
    def top_contributors(self, n: int = 5) -> list[AblationResult]:
        """Return the top-n most significant contributors by relative delta.

        Only significant results are considered.  If fewer than *n*
        significant results exist, all significant results are returned.

        Args:
            n: Maximum number of results to return.  Must be positive.

        Returns:
            A list of up to *n* AblationResult objects sorted by
            ``abs(relative_delta)`` descending.

        Raises:
            ValueError: If *n* is not a positive integer.

        Example:
            >>> top = w.top_contributors(3)
            >>> len(top) <= 3
            True
        """
        if n <= 0:
            raise ValueError(f"top_contributors: n must be positive, got {n}")
        significant = [r for r in self._log if r.is_significant]
        return sorted(significant, key=lambda r: abs(r.relative_delta), reverse=True)[:n]

    # ------------------------------------------------------------------
    def study_report(self) -> dict:
        """Generate a comprehensive study report from the witness log.

        The report summarises all observed results, split by ablation mode,
        and includes top contributors and counts of significant versus
        insignificant ablations.

        Returns:
            A dict with keys: ``total_observed``, ``significant``,
            ``insignificant``, ``by_mode``, ``top_3_contributors``,
            ``report_version``, ``generated_at``.

        Example:
            >>> report = w.study_report()
            >>> "total_observed" in report
            True
        """
        by_mode: dict[str, list[str]] = {}
        for r in self._log:
            by_mode.setdefault(r.mode.value, []).append(r.target_id)

        return {
            "total_observed": len(self._log),
            "significant": sum(1 for r in self._log if r.is_significant),
            "insignificant": sum(1 for r in self._log if not r.is_significant),
            "by_mode": by_mode,
            "top_3_contributors": [r.target_id for r in self.top_contributors(3)],
            "report_version": REPORT_VERSION,
            "generated_at": _utcnow().isoformat(),
        }


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import math as _math

    print("=== AblationPhilosophy smoke test ===")

    # copilot: define a toy metric that rewards having certain components active
    _COMPONENT_WEIGHTS = {
        "theorem_selector": 0.35,
        "proof_embedder": 0.25,
        "federation_router": 0.20,
        "authority_scorer": 0.12,
        "obstruction_filter": 0.08,
    }

    def _toy_metric(active: dict[str, bool]) -> float:
        # copilot: sum weighted contributions of active components
        return sum(w for k, w in _COMPONENT_WEIGHTS.items() if active.get(k, True))

    _components = list(_COMPONENT_WEIGHTS.keys())

    # --- Analyzer ---
    _analyzer = AblationPhilosophyAnalyzer()
    for _mode in AblationMode:
        _sched = _analyzer.design_schedule(_components, _mode)
        print(f"  Designed {_mode.value} schedule: {_sched.schedule_id}, "
              f"{len(_sched.targets)} targets")

    # --- Coordinator ---
    _witness = AblationPhilosophyWitness()
    _coord = AblationPhilosophyCoordinator(analyzer=_analyzer, witness=_witness)
    _schedule = _coord.plan(_components, AblationMode.SURGICAL)
    _results = _coord.run_study(_schedule, _toy_metric)
    _summary = _coord.summarize(_results)
    print(f"  Study summary: {_summary}")

    # --- Witness ---
    _report = _witness.study_report()
    print(f"  Witness report: {_report}")
    _top = _witness.top_contributors(3)
    print(f"  Top contributors: {[r.target_id for r in _top]}")

    for _r in _results:
        _interp = _analyzer.interpret_delta(_r)
        print(f"  {_interp}")

    print("=== Smoke test PASSED ===")
