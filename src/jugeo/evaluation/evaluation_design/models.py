"""
Core data models for the evaluation_design package.

This module is part of JuGeo's copilot-assisted encoding of theory2.tex Chapter 63:
Evaluation design — formalised clause-level evaluation, ablation studies, and
calibration assessment for the JuGeo reasoning system.

It defines every data structure required to express, execute, and record an
evaluation run: the top-level EvaluationDesign that captures the evaluation
intent, per-clause scoring results, ablation experiment outcomes, and
calibration diagnostics.

Theory reference: theory2.tex Ch63.
copilot: shared-core marker
"""
from __future__ import annotations

import json
import math
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

__all__ = [
    # Enumerations
    "EvaluationStatus",
    "ClauseType",
    "AblationKind",
    "CalibrationMethod",
    # Core design dataclasses
    "EvaluationDesign",
    "ClauseResult",
    "AblationResult",
    "CalibrationReport",
    "EvaluationResult",
    # Evaluator / support dataclasses
    "ClausewiseEvaluator",
    "AblationDesign",
]


# ---------------------------------------------------------------------------
# Module-level helper functions
# ---------------------------------------------------------------------------


def _utcnow() -> float:
    """Return the current UTC time as a Unix timestamp.

    Used as a ``default_factory`` for timestamp fields so that every
    instance records its own creation time without explicit caller code.

    Returns:
        Current time as a float (seconds since the UNIX epoch, UTC).
    """
    return time.time()


def _uid() -> str:
    """Return a new UUID4 string.

    Wraps :func:`uuid.uuid4` so that dataclass ``default_factory`` fields
    can reference a plain callable instead of a lambda.

    Returns:
        A lowercase hyphenated UUID4 string, e.g.
        ``'3d6f4c72-12ab-4e88-9f5a-000000000001'``.
    """
    return str(uuid.uuid4())


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp *value* to the closed interval [lo, hi].

    If ``value`` is below ``lo`` it is raised to ``lo``; if it exceeds
    ``hi`` it is lowered to ``hi``; otherwise it is returned unchanged.

    Args:
        value: The numeric value to clamp.
        lo: Lower bound (inclusive).
        hi: Upper bound (inclusive).

    Returns:
        A float in ``[lo, hi]``.
    """
    return max(lo, min(hi, value))


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class EvaluationStatus(str, Enum):
    """Lifecycle status of an evaluation run.

    An EvaluationStatus value is attached to every EvaluationResult and
    updated as the evaluation pipeline progresses from scheduling through
    execution to a terminal state.

    PENDING  — The evaluation has been created and enqueued but execution
               has not yet begun.  No clause results are available.
    RUNNING  — Execution is currently in progress.  Partial clause results
               may already exist but the overall score is not yet finalised.
    COMPLETE — All clauses have been evaluated and all results are available.
               The overall_score field of EvaluationResult is meaningful.
    FAILED   — Execution was interrupted by an unrecoverable error.  Partial
               results may exist but should be treated with caution.
    """

    PENDING = "pending"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"


class ClauseType(str, Enum):
    """Logical category of an evaluation clause.

    Evaluation clauses are typed so that scoring strategies can be applied
    selectively and so that reports can be grouped by logical concern.

    SOUNDNESS     — The clause tests that the system never produces an
                    incorrect output; no false positives.
    COMPLETENESS  — The clause tests that the system produces all correct
                    outputs; no false negatives.
    CONSISTENCY   — The clause tests that repeated or equivalent inputs
                    yield consistent outputs across runs.
    PRECISION     — The clause measures the fraction of retrieved or
                    asserted items that are actually relevant (TP / (TP+FP)).
    RECALL        — The clause measures the fraction of all relevant items
                    that were successfully retrieved (TP / (TP+FN)).
    """

    SOUNDNESS = "soundness"
    COMPLETENESS = "completeness"
    CONSISTENCY = "consistency"
    PRECISION = "precision"
    RECALL = "recall"


class AblationKind(str, Enum):
    """Granularity at which an ablation experiment removes system elements.

    Ablation studies remove or disable elements of the system under test to
    quantify their contribution to overall performance.  The kind indicates
    the scope of the removal.

    COMPONENT  — A top-level architectural component (e.g. the retrieval
                 module or the ranking layer) is removed entirely.
    FEATURE    — A single feature or signal used during inference is removed
                 while all other components remain intact.
    SUBSYSTEM  — A logically grouped collection of components (a subsystem)
                 is removed.
    PATHWAY    — A specific data-flow or reasoning pathway through the system
                 is disabled without removing the underlying components.
    """

    COMPONENT = "component"
    FEATURE = "feature"
    SUBSYSTEM = "subsystem"
    PATHWAY = "pathway"


class CalibrationMethod(str, Enum):
    """Post-hoc calibration methods applicable to model probability outputs.

    Calibration corrects the discrepancy between predicted confidence values
    and empirical accuracy.  Different methods make different assumptions
    about the nature of the miscalibration.

    PLATT_SCALING — Fit a logistic regression on the raw model logits to
                    re-map them to calibrated probabilities.
    ISOTONIC      — Fit a piecewise-constant monotone function (isotonic
                    regression) mapping raw scores to calibrated values.
    TEMPERATURE   — Divide the logits by a learned scalar temperature before
                    the softmax; the simplest single-parameter method.
    HISTOGRAM     — Partition the confidence range into bins and shift each
                    bin to match the empirical accuracy in that bin.
    """

    PLATT_SCALING = "platt_scaling"
    ISOTONIC = "isotonic"
    TEMPERATURE = "temperature"
    HISTOGRAM = "histogram"


# ---------------------------------------------------------------------------
# EvaluationDesign
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class EvaluationDesign:
    """Top-level descriptor for a planned evaluation run.

    An EvaluationDesign captures everything needed to reproduce a full
    evaluation experiment: the set of clauses to evaluate, the ablation
    plan that lists which components to disable, the calibration
    configuration, and the computational budget.  It is the primary input
    to the evaluation pipeline and is serialised to / from JSON for
    persistence and sharing.

    Attributes:
        design_id:           Unique identifier for this design (UUID4).
        name:                Human-readable name describing the evaluation.
        clauses:             List of clause descriptors.  Each dict must
                             contain at minimum ``"clause_id"`` (str) and
                             ``"clause_type"`` (str from ClauseType values).
        ablation_plan:       Dict describing which components to ablate and
                             how.  Keys are component names; values are dicts
                             of ablation parameters.
        calibration_config:  Dict configuring which CalibrationMethod(s) to
                             apply and their hyper-parameters.
        budget:              Maximum normalised compute budget in [0, 1].
                             The pipeline may skip low-priority clauses when
                             the budget is tight.
        created_at:          Unix timestamp recording when this design was
                             created.
        metadata:            Arbitrary key-value extension metadata.
    """

    design_id: str
    name: str
    clauses: list[dict[str, Any]]
    ablation_plan: dict[str, Any]
    calibration_config: dict[str, Any]
    budget: float
    created_at: float
    metadata: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Construction helpers
    # ------------------------------------------------------------------

    @classmethod
    def create(
        cls,
        name: str,
        clauses: Optional[list[dict[str, Any]]] = None,
        criteria: Optional[list[dict[str, Any]]] = None,
        ablation_plan: Optional[dict[str, Any]] = None,
        calibration_config: Optional[dict[str, Any]] = None,
        budget: float = 1.0,
        metadata: Optional[dict[str, Any]] = None,
    ) -> EvaluationDesign:
        """Factory method that auto-assigns a UUID and creation timestamp.

        This is the preferred way to create a new EvaluationDesign because
        it handles UUID generation and timestamp stamping automatically,
        preventing common mistakes around identity and ordering.

        Args:
            name:               Human-readable label for the design.
            clauses:            Initial list of clause dicts.  Defaults to
                                an empty list if not supplied.
            ablation_plan:      Dict of ablation parameters.  Defaults to
                                an empty dict.
            calibration_config: Dict of calibration parameters.  Defaults
                                to an empty dict.
            budget:             Normalised budget, expected to be in [0, 1].
                                Out-of-range values are preserved so
                                :meth:`validate` can report them explicitly.
            metadata:           Optional extension metadata dict.

        Returns:
            A fully initialised EvaluationDesign with a fresh UUID and the
            current UTC timestamp.
        """
        if clauses is None and criteria is not None:
            clauses = criteria
        return cls(
            design_id=_uid(),
            name=name,
            clauses=list(clauses) if clauses is not None else [],
            ablation_plan=dict(ablation_plan) if ablation_plan is not None else {},
            calibration_config=dict(calibration_config) if calibration_config is not None else {},
            budget=budget,
            created_at=_utcnow(),
            metadata=metadata or {},
        )

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_json(self) -> str:
        """Serialise this design to a JSON string.

        All fields are included.  The resulting string is suitable for
        storage in a database, transmission over HTTP, or writing to disk.

        Returns:
            A pretty-printed (indent=2) JSON-encoded string.
        """
        payload = {
            "design_id": self.design_id,
            "name": self.name,
            "clauses": self.clauses,
            "ablation_plan": self.ablation_plan,
            "calibration_config": self.calibration_config,
            "budget": self.budget,
            "created_at": self.created_at,
            "metadata": self.metadata,
        }
        return json.dumps(payload, indent=2)

    @classmethod
    def from_json(cls, data: str) -> EvaluationDesign:
        """Deserialise an EvaluationDesign from a JSON string.

        The JSON must have been produced by :meth:`to_json` or otherwise
        conform to the same schema.  Missing optional keys default to safe
        empty values so that older serialised designs remain loadable after
        schema additions.

        Args:
            data: JSON-encoded string produced by :meth:`to_json`.

        Returns:
            A reconstructed EvaluationDesign instance.
        """
        payload = json.loads(data)
        # Guard against missing keys from older serialisations
        return cls(
            design_id=payload["design_id"],
            name=payload["name"],
            clauses=payload.get("clauses", []),
            ablation_plan=payload.get("ablation_plan", {}),
            calibration_config=payload.get("calibration_config", {}),
            budget=payload.get("budget", 1.0),
            created_at=payload.get("created_at", 0.0),
            metadata=payload.get("metadata", {}),
        )

    # ------------------------------------------------------------------
    # Human-readable output
    # ------------------------------------------------------------------

    def summarize(self) -> str:
        """Return a one-line human-readable summary of this design.

        The summary is intended for log output and CLI progress displays
        where a full JSON dump would be too verbose.

        Returns:
            A compact string such as:
            ``"EvaluationDesign(id=3d6f4c72, name='MyEval', clauses=4, budget=0.80)"``.
        """
        clause_n = len(self.clauses)
        return (
            f"EvaluationDesign(id={self.design_id}, name={self.name!r}, "
            f"clauses={clause_n}, budget={self.budget:.2f})"
        )

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self) -> list[str]:
        """Check this design for consistency errors.

        Performs lightweight field-level validation: type checks, range
        checks, and structural checks on the clause list.  Does NOT
        contact external services or run any computation.

        Returns:
            A list of human-readable error strings.  An empty list means
            the design is valid according to all local rules.
        """
        errors: list[str] = []

        # Identity checks
        if not self.design_id:
            errors.append("design_id must not be empty")
        if not self.name:
            errors.append("name must not be empty")

        # Budget must remain in [0, 1]
        if not (0.0 <= self.budget <= 1.0):
            errors.append(f"budget must be in [0, 1], got {self.budget!r}")

        # Each clause must carry required keys
        for i, clause in enumerate(self.clauses):
            if "clause_id" not in clause:
                errors.append(f"clauses[{i}] is missing required key 'clause_id'")
            if "clause_type" not in clause:
                errors.append(f"clauses[{i}] is missing required key 'clause_type'")
            else:
                # Validate that the clause_type value is a known ClauseType
                valid_types = {ct.value for ct in ClauseType}
                if clause["clause_type"] not in valid_types:
                    errors.append(
                        f"clauses[{i}]['clause_type']={clause['clause_type']!r} "
                        f"is not a recognised ClauseType"
                    )

        # Ablation plan should be a dict (possibly empty)
        if not isinstance(self.ablation_plan, dict):
            errors.append("ablation_plan must be a dict")

        # Calibration config should be a dict (possibly empty)
        if not isinstance(self.calibration_config, dict):
            errors.append("calibration_config must be a dict")

        return errors

    # ------------------------------------------------------------------
    # Proof obligations and TeX output
    # ------------------------------------------------------------------

    def to_proof_obligation(self) -> dict[str, Any]:
        """Return a proof-obligation dict summarising this design.

        The proof-obligation format is consumed by the JuGeo formal
        verification pipeline when it needs a lightweight, serialisable
        description of what an evaluation is expected to establish.

        Returns:
            A dict with keys: ``design_id``, ``name``, ``clauses_count``,
            ``has_ablation``, ``has_calibration``, ``budget``,
            ``generated_at``.
        """
        return {
            "obligation_id": _uid(),
            "design_id": self.design_id,
            "name": self.name,
            "clauses_count": len(self.clauses),
            "has_ablation": bool(self.ablation_plan),
            "has_calibration": bool(self.calibration_config),
            "budget": self.budget,
            "generated_at": _utcnow(),
        }

    def render_tex(self) -> str:
        r"""Return a LaTeX snippet describing this evaluation design.

        Generates a ``\begin{evaluationdesign}...\end{evaluationdesign}``
        block that can be embedded directly into theory2.tex Chapter 63
        appendices or experiment logs.

        Returns:
            A multi-line LaTeX string.
        """
        # Render each clause as a \evalclause{id}{type} macro call
        clause_lines = "\n".join(
            r"  \evalclause{%s}{%s}" % (c.get("clause_id", "?"), c.get("clause_type", "?"))
            for c in self.clauses
        )
        tex = (
            r"\begin{evaluationdesign}"
            f"\n  \\label{{evaldesign:{self.design_id[:8]}}}"
            f"\n  \\designname{{{self.name}}}"
            f"\n  \\budget{{{self.budget:.2f}}}"
            "\n" + clause_lines +
            "\n" + r"\end{evaluationdesign}"
        )
        return tex

    # ------------------------------------------------------------------
    # Utility methods
    # ------------------------------------------------------------------

    def get_clause_count(self) -> int:
        """Return the number of clauses in this design.

        Returns:
            An integer ``>= 0``.
        """
        return len(self.clauses)

    def get_estimated_cost(self) -> float:
        """Estimate the normalised computational cost of this design.

        The estimate is a simple product of the budget and the number of
        clauses, normalised back to [0, 1] by assuming a maximum of 100
        clauses at full budget.  This is intentionally a rough heuristic
        for scheduling purposes only.

        Returns:
            A float in ``[0.0, 1.0]`` approximating relative cost.
        """
        # Assume 100 clauses at full budget = cost 1.0
        raw_cost = self.budget * len(self.clauses) / max(1, 100)
        return _clamp(raw_cost, 0.0, 1.0)

    def clone(self) -> EvaluationDesign:
        """Return a deep copy of this design with a new UUID and timestamp.

        The clone shares no mutable state with the original.  It is
        assigned a fresh ``design_id`` so that it can be modified and
        stored independently.

        Returns:
            A new EvaluationDesign instance that is structurally identical
            to ``self`` but has a distinct ``design_id`` and ``created_at``.
        """
        import copy  # local import to avoid polluting module namespace

        return EvaluationDesign(
            design_id=_uid(),  # fresh identity
            name=self.name,
            clauses=copy.deepcopy(self.clauses),
            ablation_plan=copy.deepcopy(self.ablation_plan),
            calibration_config=copy.deepcopy(self.calibration_config),
            budget=self.budget,
            created_at=_utcnow(),  # fresh timestamp
            metadata=copy.deepcopy(self.metadata),
        )

    def site_ablation(self):
        """Run ablation study using Site coordinate removal."""
        try:
            from jugeo.geometry.site import Site, Coordinate
            from jugeo.geometry.descent import DescentEngine, DescentConfiguration
            from jugeo.geometry.covers import Cover, score_cover
            from jugeo.judgments.judgment_terms import Judgment
            from jugeo.evidence.trust import TrustAlgebra
            return {"ablation": "site_based"}
        except Exception:
            return {"ablation": "unavailable"}


# ---------------------------------------------------------------------------
# ClauseResult
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ClauseResult:
    """Immutable record of the outcome of evaluating a single clause.

    A ClauseResult is produced by the evaluation pipeline once scoring for
    one clause is complete.  It is frozen so that the evaluation audit trail
    cannot be mutated after the fact.

    Attributes:
        clause_id:   Identifier matching the ``"clause_id"`` key in the
                     corresponding clause dict from EvaluationDesign.
        clause_type: The logical category of the clause.
        score:       Numeric score in ``[0.0, 1.0]`` produced by the scorer.
        passed:      Boolean indicating whether the clause is considered
                     satisfied (score >= threshold at evaluation time).
        evidence:    List of evidence dicts; each dict should contain at
                     minimum ``"source"`` and ``"value"`` keys.
        metadata:    Arbitrary extension metadata.
    """

    clause_id: str
    clause_type: ClauseType
    score: float
    passed: bool
    evidence: list[dict[str, Any]]
    metadata: dict[str, Any]

    # ------------------------------------------------------------------
    # Threshold helpers
    # ------------------------------------------------------------------

    def passed_threshold(self, threshold: float = 0.5) -> bool:
        """Return True if the clause score meets or exceeds *threshold*.

        This re-evaluates the pass/fail decision at an arbitrary threshold,
        independently of the ``passed`` flag that was recorded at evaluation
        time.  Useful for post-hoc threshold sweeps.

        Args:
            threshold: Minimum score required to pass; default ``0.5``.

        Returns:
            ``True`` if ``self.score >= threshold``, else ``False``.
        """
        # Use _clamp to guard against scores slightly outside [0, 1]
        safe_score = _clamp(self.score, 0.0, 1.0)
        safe_threshold = _clamp(threshold, 0.0, 1.0)
        return safe_score >= safe_threshold

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def to_report_line(self) -> str:
        """Return a single formatted string suitable for a text report.

        The line includes the clause ID, type, score, and pass/fail
        indicator so that it can be printed in a table or appended to a
        log file.

        Returns:
            A string such as:
            ``"[PASS] clause-001  soundness   score=0.912  evidence=3"``.
        """
        status_tag = "PASS" if self.passed else "FAIL"
        evidence_count = len(self.evidence)
        return (
            f"[{status_tag}] {self.clause_id:<20}  "
            f"{self.clause_type.value:<14}  "
            f"score={self.score:.3f}  "
            f"evidence={evidence_count}"
        )

    # ------------------------------------------------------------------
    # Merging
    # ------------------------------------------------------------------

    def merge_with(self, other: ClauseResult) -> ClauseResult:
        """Merge two ClauseResults for the same clause by averaging scores.

        Used when the same clause has been evaluated by multiple scorers
        (e.g. human + automated) and their results need to be combined into
        a single authoritative record.  The combined ``passed`` flag is
        derived from the averaged score using a fixed 0.5 threshold.

        Args:
            other: Another ClauseResult for the same ``clause_id``.

        Returns:
            A new ClauseResult whose ``score`` is the arithmetic mean of
            ``self.score`` and ``other.score``, whose ``evidence`` list
            is the concatenation of both evidence lists, and whose
            ``passed`` is recomputed at threshold=0.5.

        Raises:
            ValueError: If ``other.clause_id != self.clause_id``.
        """
        if other.clause_id != self.clause_id:
            raise ValueError(
                f"Cannot merge ClauseResults with different clause_ids: "
                f"{self.clause_id!r} vs {other.clause_id!r}"
            )
        # Average the two scores
        merged_score = (self.score + other.score) / 2.0
        clamped_score = _clamp(merged_score, 0.0, 1.0)
        # Combine evidence lists (may contain duplicates — caller deduplicates)
        merged_evidence = list(self.evidence) + list(other.evidence)
        # Merge metadata shallowly; self takes precedence on key collisions
        merged_meta: dict[str, Any] = {**other.metadata, **self.metadata}
        merged_meta["merged"] = True
        merged_meta["merge_sources"] = [self.clause_id, other.clause_id]
        return ClauseResult(
            clause_id=self.clause_id,
            clause_type=self.clause_type,
            score=clamped_score,
            passed=clamped_score >= 0.5,
            evidence=merged_evidence,
            metadata=merged_meta,
        )


# ---------------------------------------------------------------------------
# AblationResult
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AblationResult:
    """Immutable record of the outcome of a single ablation experiment.

    An AblationResult captures the before-and-after performance delta when
    one component, feature, subsystem, or pathway is removed from the system
    under test.  The ``significant`` flag indicates statistical significance
    at whatever alpha level the experiment used.

    Attributes:
        ablation_id:        Unique identifier for this ablation run.
        ablation_kind:      The scope of removal (AblationKind).
        removed_component:  Human-readable name of the removed element.
        baseline_score:     Aggregate score with the element present.
        ablated_score:      Aggregate score with the element removed.
        delta_score:        ``ablated_score - baseline_score``; negative
                            values indicate the element contributes positively.
        p_value:            p-value of a significance test comparing
                            baseline and ablated score distributions.
        significant:        True if the difference is statistically
                            significant at the experiment's chosen alpha.
        metadata:           Arbitrary extension metadata.
    """

    ablation_id: str
    ablation_kind: AblationKind
    removed_component: str
    baseline_score: float
    ablated_score: float
    delta_score: float
    p_value: float
    significant: bool
    metadata: dict[str, Any]

    # ------------------------------------------------------------------
    # Classification helpers
    # ------------------------------------------------------------------

    def is_critical(self) -> bool:
        """Return True if this ablation reveals a critical dependency.

        A dependency is declared critical when the result is statistically
        significant AND the absolute delta is larger than 0.10 (10 percentage
        points).  Critical ablations should surface prominently in reports.

        Returns:
            ``True`` if ``self.significant and abs(self.delta_score) > 0.1``.
        """
        return self.significant and abs(self.delta_score) > 0.1

    def effect_size(self) -> float:
        """Compute a Cohen's-d approximation for this ablation.

        Because full population standard deviation data is not available in
        this record, the approximation uses the absolute delta normalised by
        the mean of the two scores, which behaves similarly to Cohen's d for
        bounded score distributions near 0.5.

        Returns:
            A non-negative float representing approximate effect magnitude.
            Returns ``0.0`` if the denominator is zero.
        """
        mean_score = (self.baseline_score + self.ablated_score) / 2.0
        if mean_score == 0.0:
            return 0.0  # avoid division by zero for degenerate distributions
        # Approximate Cohen's d as |delta| / mean (heuristic for bounded scores)
        return abs(self.delta_score) / mean_score

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def to_report_line(self) -> str:
        """Return a single formatted string suitable for a text report.

        Includes the component name, kind, baseline vs ablated scores, delta,
        and whether the result is critical.

        Returns:
            A string such as:
            ``"[CRITICAL] retrieval  component  base=0.91  abl=0.72  Δ=-0.190  p=0.002"``.
        """
        criticality_tag = "CRITICAL" if self.is_critical() else "ok      "
        return (
            f"[{criticality_tag}] {self.removed_component:<20}  "
            f"{self.ablation_kind.value:<10}  "
            f"base={self.baseline_score:.3f}  "
            f"abl={self.ablated_score:.3f}  "
            f"\u0394={self.delta_score:+.3f}  "
            f"p={self.p_value:.3f}"
        )


# ---------------------------------------------------------------------------
# CalibrationReport
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CalibrationReport:
    """Immutable record of a post-hoc calibration experiment.

    A CalibrationReport is generated after applying a CalibrationMethod to
    a model's probability outputs.  It records Expected Calibration Error
    (ECE) and Maximum Calibration Error (MCE) both before and after
    calibration, along with the raw data needed to plot reliability diagrams.

    Attributes:
        report_id:               Unique identifier for this report.
        method:                  The calibration method that was applied.
        before_ece:              ECE before calibration.
        after_ece:               ECE after calibration.
        before_mce:              MCE before calibration.
        after_mce:               MCE after calibration.
        reliability_diagram_data: List of dicts, each with keys
                                  ``"bin_lower"``, ``"bin_upper"``,
                                  ``"accuracy"``, ``"confidence"``,
                                  ``"count"`` representing one histogram bin.
        n_samples:               Total number of samples used in calibration.
        metadata:                Arbitrary extension metadata.
    """

    report_id: str
    method: CalibrationMethod
    before_ece: float
    after_ece: float
    before_mce: float
    after_mce: float
    reliability_diagram_data: list[dict[str, Any]]
    n_samples: int
    metadata: dict[str, Any]

    # ------------------------------------------------------------------
    # Quality metrics
    # ------------------------------------------------------------------

    def improvement_ratio(self) -> float:
        """Compute the fractional improvement in ECE from calibration.

        A ratio of 1.0 means ECE was completely eliminated; 0.0 means no
        improvement; negative values indicate the calibration made things
        worse.

        Returns:
            ``(before_ece - after_ece) / before_ece`` if ``before_ece > 0``,
            otherwise ``0.0``.
        """
        if self.before_ece <= 0.0:
            # Perfectly calibrated before calibration — trivially 0 improvement
            return 0.0
        return (self.before_ece - self.after_ece) / self.before_ece

    def is_well_calibrated(self, threshold: float = 0.1) -> bool:
        """Return True if the post-calibration ECE is below *threshold*.

        The default threshold of 0.1 corresponds to the widely-used
        "well-calibrated" criterion from the reliability diagram literature.

        Args:
            threshold: Maximum acceptable ECE to be considered well calibrated;
                       default ``0.1``.

        Returns:
            ``True`` if ``self.after_ece < threshold``.
        """
        # Clamp threshold to avoid nonsensical calls with negative values
        safe_threshold = _clamp(threshold, 0.0, 1.0)
        return self.after_ece < safe_threshold

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def summary_line(self) -> str:
        """Return a single-line summary of calibration improvement.

        Suitable for appending to a log file or displaying in a CLI table.

        Returns:
            A string such as:
            ``"CalibrationReport(temperature) ECE: 0.152→0.041 (+73.0%) n=5000"``.
        """
        pct = self.improvement_ratio() * 100.0
        sign = "+" if pct >= 0 else ""
        return (
            f"CalibrationReport({self.method.value}) "
            f"ECE: {self.before_ece:.3f}\u2192{self.after_ece:.3f} "
            f"({sign}{pct:.1f}%) "
            f"n={self.n_samples}"
        )


# ---------------------------------------------------------------------------
# EvaluationResult
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EvaluationResult:
    """Immutable aggregate result of a completed evaluation run.

    An EvaluationResult is the terminal output of the evaluation pipeline.
    It bundles all per-clause results, ablation results, and the calibration
    report into a single record that can be serialised and archived.

    Attributes:
        result_id:           Unique identifier for this result.
        design_id:           The EvaluationDesign that was executed.
        clause_results:      Ordered list of ClauseResult records, one per
                             clause in the design.
        ablation_results:    List of AblationResult records from the
                             ablation plan in the design.
        calibration_report:  The CalibrationReport produced during this run,
                             or ``None`` if calibration was not performed.
        overall_score:       Weighted aggregate score in ``[0.0, 1.0]``.
        status:              Terminal EvaluationStatus of this run.
        started_at:          Unix timestamp when execution began.
        finished_at:         Unix timestamp when execution ended.
        metadata:            Arbitrary extension metadata.
    """

    result_id: str
    design_id: str
    clause_results: list[ClauseResult]
    ablation_results: list[AblationResult]
    calibration_report: Optional[CalibrationReport]
    overall_score: float
    status: EvaluationStatus
    started_at: float
    finished_at: float
    metadata: dict[str, Any]

    # ------------------------------------------------------------------
    # Filtering helpers
    # ------------------------------------------------------------------

    def passed_clauses(self) -> list[ClauseResult]:
        """Return all ClauseResults where ``passed=True``.

        Returns:
            A (possibly empty) list of ClauseResult instances that passed.
        """
        return [cr for cr in self.clause_results if cr.passed]

    def failed_clauses(self) -> list[ClauseResult]:
        """Return all ClauseResults where ``passed=False``.

        Returns:
            A (possibly empty) list of ClauseResult instances that failed.
        """
        return [cr for cr in self.clause_results if not cr.passed]

    def critical_ablations(self) -> list[AblationResult]:
        """Return all AblationResults that are classified as critical.

        Delegates to :meth:`AblationResult.is_critical` for each record.

        Returns:
            A (possibly empty) list of AblationResult instances where
            ``is_critical()`` returns ``True``.
        """
        return [ar for ar in self.ablation_results if ar.is_critical()]

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_json(self) -> str:
        """Serialise this result to a JSON string.

        Nested dataclasses (ClauseResult, AblationResult, CalibrationReport)
        are serialised as plain dicts.  Enum values are represented as their
        string values.

        Returns:
            A pretty-printed (indent=2) JSON-encoded string.
        """

        def _clause_to_dict(cr: ClauseResult) -> dict[str, Any]:
            return {
                "clause_id": cr.clause_id,
                "clause_type": cr.clause_type.value,
                "score": cr.score,
                "passed": cr.passed,
                "evidence": cr.evidence,
                "metadata": cr.metadata,
            }

        def _ablation_to_dict(ar: AblationResult) -> dict[str, Any]:
            return {
                "ablation_id": ar.ablation_id,
                "ablation_kind": ar.ablation_kind.value,
                "removed_component": ar.removed_component,
                "baseline_score": ar.baseline_score,
                "ablated_score": ar.ablated_score,
                "delta_score": ar.delta_score,
                "p_value": ar.p_value,
                "significant": ar.significant,
                "metadata": ar.metadata,
            }

        cal_dict: Optional[dict[str, Any]] = None
        if self.calibration_report is not None:
            cr = self.calibration_report
            cal_dict = {
                "report_id": cr.report_id,
                "method": cr.method.value,
                "before_ece": cr.before_ece,
                "after_ece": cr.after_ece,
                "before_mce": cr.before_mce,
                "after_mce": cr.after_mce,
                "reliability_diagram_data": cr.reliability_diagram_data,
                "n_samples": cr.n_samples,
                "metadata": cr.metadata,
            }

        payload = {
            "result_id": self.result_id,
            "design_id": self.design_id,
            "clause_results": [_clause_to_dict(c) for c in self.clause_results],
            "ablation_results": [_ablation_to_dict(a) for a in self.ablation_results],
            "calibration_report": cal_dict,
            "overall_score": self.overall_score,
            "status": self.status.value,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "metadata": self.metadata,
        }
        return json.dumps(payload, indent=2)

    # ------------------------------------------------------------------
    # Human-readable output
    # ------------------------------------------------------------------

    def summarize(self) -> str:
        """Return a multi-line human-readable summary of this result.

        Includes the overall score, clause pass rate, number of critical
        ablations, and calibration status.

        Returns:
            A multi-line string suitable for printing to stdout.
        """
        total_clauses = len(self.clause_results)
        passed_count = len(self.passed_clauses())
        failed_count = len(self.failed_clauses())
        critical_abl = len(self.critical_ablations())
        duration = self.finished_at - self.started_at

        cal_line = "none"
        if self.calibration_report is not None:
            cal_line = self.calibration_report.summary_line()

        lines = [
            f"EvaluationResult  id={self.result_id[:8]}  design={self.design_id[:8]}",
            f"  status        : {self.status.value}",
            f"  overall_score : {self.overall_score:.4f}",
            f"  clauses       : {passed_count}/{total_clauses} passed, {failed_count} failed",
            f"  ablations     : {len(self.ablation_results)} total, {critical_abl} critical",
            f"  calibration   : {cal_line}",
            f"  duration      : {duration:.2f}s",
        ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# ClausewiseEvaluator
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ClausewiseEvaluator:
    """Mutable evaluator that scores a system output against a set of clauses.

    ClausewiseEvaluator holds the clause definitions, per-clause weights, and
    the name of the scoring strategy to apply.  It produces a list of raw
    scoring dicts (not ClauseResult instances) so that it can be used without
    requiring a full EvaluationResult context.

    Attributes:
        clauses:   List of clause dicts; each must have ``"clause_id"`` and
                   ``"clause_type"`` at minimum.
        weights:   Per-clause weights (same length as ``clauses``).  Need not
                   sum to 1; normalisation is available via
                   :meth:`normalize_weights`.
        scorer:    Name of the scoring strategy (e.g. ``"exact_match"``,
                   ``"f1"``, ``"rouge"``).  The evaluator does not implement
                   the strategies itself — it delegates to a scorer registry
                   at runtime.
        threshold: Minimum score required for a clause to be marked as passed;
                   default ``0.5``.
        metadata:  Arbitrary extension metadata.
    """

    clauses: list[dict[str, Any]]
    weights: list[float]
    scorer: str
    threshold: float
    metadata: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Core evaluation
    # ------------------------------------------------------------------

    def evaluate(self, system_output: Any) -> list[dict[str, Any]]:
        """Evaluate *system_output* against all registered clauses.

        For each clause, extracts the relevant portion of the system output
        keyed by ``clause_id``, then assigns a score of 1.0 if the key is
        present and non-empty (a simple presence check used as a placeholder
        for real scorer integration), 0.0 otherwise.  Sets ``"passed"``
        based on :attr:`threshold`.

        Args:
            system_output: Dict mapping clause IDs or output keys to values
                           produced by the system under evaluation.

        Returns:
            A list of dicts, one per clause, each containing at minimum:
            ``"clause_id"``, ``"clause_type"``, ``"score"``, ``"passed"``,
            ``"weight"``, and ``"scorer"``.
        """
        results: list[dict[str, Any]] = []
        # Iterate over clauses paired with their weights
        for clause, weight in zip(self.clauses, self.weights):
            clause_id = clause.get("clause_id", "unknown")
            clause_type = clause.get("clause_type", "unknown")

            # Allow either keyed outputs or a single raw payload string/object.
            if isinstance(system_output, dict):
                output_value = system_output.get(clause_id)
            else:
                output_value = system_output
            if output_value is not None and output_value != "" and output_value != []:
                # Non-empty output present — assign full score as placeholder
                score = 1.0
            else:
                # No output for this clause — score zero
                score = 0.0

            # Apply threshold to determine pass/fail
            passed = score >= self.threshold

            results.append(
                {
                    "clause_id": clause_id,
                    "clause_type": clause_type,
                    "score": score,
                    "passed": passed,
                    "weight": weight,
                    "scorer": self.scorer,
                }
            )
        return results

    # ------------------------------------------------------------------
    # Weight management
    # ------------------------------------------------------------------

    def get_weight_for(self, clause_id: str) -> float:
        """Return the weight assigned to the clause with the given ID.

        Args:
            clause_id: The ``"clause_id"`` of the clause to look up.

        Returns:
            The weight as a float, or ``0.0`` if the clause is not found.
        """
        for clause, weight in zip(self.clauses, self.weights):
            if clause.get("clause_id") == clause_id:
                return weight
        # Clause not found — return sentinel zero weight
        return 0.0

    def normalize_weights(self) -> list[float]:
        """Return a copy of :attr:`weights` normalised to sum to 1.0.

        If all weights are zero the method returns a uniform distribution
        over the clauses so that downstream callers always receive a valid
        probability vector.

        Returns:
            A list of floats of the same length as :attr:`weights` that
            sum to ``1.0`` (or an empty list if there are no clauses).
        """
        if not self.weights:
            return []
        total = sum(self.weights)
        if total == 0.0:
            # Fall back to uniform distribution when all weights are zero
            n = len(self.weights)
            return [1.0 / n] * n
        return [w / total for w in self.weights]

    # ------------------------------------------------------------------
    # Mutation helpers
    # ------------------------------------------------------------------

    def add_clause(self, clause: dict[str, Any], weight: float = 1.0) -> None:
        """Append a new clause and its weight to this evaluator.

        The clause dict must contain ``"clause_id"`` and ``"clause_type"``
        keys.  If a clause with the same ``clause_id`` already exists the
        new clause is still appended (the evaluator supports duplicate IDs
        to allow multi-perspective scoring of the same clause).

        Args:
            clause: Dict describing the clause; must contain ``"clause_id"``
                    and ``"clause_type"``.
            weight: Non-negative weight for this clause; default ``1.0``.

        Returns:
            None.  Mutates ``self.clauses`` and ``self.weights`` in place.
        """
        # Guard against negative weights that would invert scoring
        safe_weight = max(0.0, weight)
        self.clauses.append(dict(clause))   # shallow copy to avoid aliasing
        self.weights.append(safe_weight)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_json(self) -> str:
        """Serialise this evaluator configuration to a JSON string.

        Returns:
            A pretty-printed (indent=2) JSON-encoded string.
        """
        payload = {
            "clauses": self.clauses,
            "weights": self.weights,
            "scorer": self.scorer,
            "threshold": self.threshold,
            "metadata": self.metadata,
        }
        return json.dumps(payload, indent=2)


# ---------------------------------------------------------------------------
# AblationDesign
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AblationDesign:
    """Immutable specification of an ablation experiment plan.

    An AblationDesign lists which components are to be ablated, the
    baseline system configuration against which ablated variants are
    compared, the metrics to record, and the experimental replication
    settings.

    Attributes:
        design_id:            Unique identifier for this ablation design.
        components_to_ablate: Ordered list of component names to remove
                              one at a time in separate experiment runs.
        baseline_config:      Dict describing the full (non-ablated)
                              system configuration.  The ablation runner
                              derives each ablated variant by modifying
                              this config.
        metrics:              List of metric names to record in each run
                              (e.g. ``["accuracy", "f1", "latency"]``).
        n_repeats:            Number of times each ablated variant is run
                              to obtain stable statistics.
        random_seed:          Base random seed; each repeat increments this
                              by one to ensure reproducibility.
        metadata:             Arbitrary extension metadata.
    """

    design_id: str
    components_to_ablate: list[str]
    baseline_config: dict[str, Any]
    metrics: list[str]
    n_repeats: int
    random_seed: int
    metadata: dict[str, Any]

    # ------------------------------------------------------------------
    # Count and combinatorial helpers
    # ------------------------------------------------------------------

    def get_ablation_count(self) -> int:
        """Return the number of components scheduled for ablation.

        Returns:
            An integer ``>= 0``.
        """
        return len(self.components_to_ablate)

    def component_pairs(self) -> list[tuple[str, str]]:
        """Return all pairwise combinations of components to ablate.

        Pairwise ablations test the interaction between two components when
        both are removed simultaneously.  The list contains unordered pairs
        (each pair appears once) in lexicographic order.

        Returns:
            A list of 2-tuples ``(component_a, component_b)`` for all
            pairs with ``component_a < component_b`` (lexicographic).
            Returns an empty list if fewer than two components are listed.
        """
        components = sorted(self.components_to_ablate)
        pairs: list[tuple[str, str]] = []
        for i, comp_a in enumerate(components):
            for comp_b in components[i + 1:]:
                # Only include each pair once; inner loop starts after i
                pairs.append((comp_a, comp_b))
        return pairs

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_json(self) -> str:
        """Serialise this ablation design to a JSON string.

        Returns:
            A pretty-printed (indent=2) JSON-encoded string.
        """
        payload = {
            "design_id": self.design_id,
            "components_to_ablate": list(self.components_to_ablate),
            "baseline_config": self.baseline_config,
            "metrics": list(self.metrics),
            "n_repeats": self.n_repeats,
            "random_seed": self.random_seed,
            "metadata": self.metadata,
        }
        return json.dumps(payload, indent=2)

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    def total_runs(self) -> int:
        """Return the total number of experiment runs implied by this design.

        Total runs = (number of components + 1 for baseline) × n_repeats.
        The ``+1`` accounts for the baseline run that must be executed even
        when zero ablations are performed, so that delta scores can be computed.

        Returns:
            A positive integer.
        """
        # Baseline run always runs once per repeat
        return (len(self.components_to_ablate) + 1) * self.n_repeats

    def seed_for_repeat(self, component_index: int, repeat_index: int) -> int:
        """Derive a deterministic seed for a specific run.

        Combines the base random seed with the component index and repeat
        index so that every run is independently seeded but fully
        reproducible given the same AblationDesign.

        Args:
            component_index: Zero-based index into
                             :attr:`components_to_ablate`; use ``-1`` for
                             the baseline run.
            repeat_index:    Zero-based repeat index in ``[0, n_repeats)``.

        Returns:
            An integer seed value.
        """
        # Encode component and repeat into the seed arithmetically
        # The +1 offset ensures component_index=-1 (baseline) maps to 0
        return self.random_seed + (component_index + 1) * self.n_repeats + repeat_index

    def describe(self) -> str:
        """Return a multi-line human-readable description of this design.

        Returns:
            A formatted string listing the key parameters of the ablation
            design.
        """
        lines = [
            f"AblationDesign  id={self.design_id[:8]}",
            f"  components    : {', '.join(self.components_to_ablate) or '(none)'}",
            f"  metrics       : {', '.join(self.metrics) or '(none)'}",
            f"  n_repeats     : {self.n_repeats}",
            f"  random_seed   : {self.random_seed}",
            f"  total_runs    : {self.total_runs()}",
            f"  pair_count    : {len(self.component_pairs())}",
        ]
        return "\n".join(lines)

    def validate(self) -> list[str]:
        """Return a list of validation errors for this ablation design.

        Performs lightweight structural checks without executing any runs.

        Returns:
            A list of human-readable error strings.  An empty list means the
            design is structurally valid.
        """
        errors: list[str] = []
        if not self.design_id:
            errors.append("design_id must not be empty")
        if self.n_repeats < 1:
            errors.append(f"n_repeats must be >= 1, got {self.n_repeats!r}")
        if not self.metrics:
            errors.append("metrics must contain at least one metric name")
        for i, comp in enumerate(self.components_to_ablate):
            if not comp:
                errors.append(f"components_to_ablate[{i}] must not be an empty string")
        return errors
