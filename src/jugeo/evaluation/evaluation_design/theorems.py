"""Formal theorems for the evaluation_design package.

Theory reference: theory2.tex Ch63
copilot: shared-core marker

This module encodes the five key formal theorems of Chapter 63 of theory2.tex
as Python classes.  Each theorem class exposes:

* METADATA  — a frozen TheoremMetadata dataclass describing the theorem
* verify()  — a classmethod that checks the theorem's conditions against
               real evaluation data and returns a detailed verification dict
* proof_sketch() — returns the prose proof sketch from METADATA
* latex_statement() — returns a LaTeX ``\\theorem`` environment string

The EvaluationTheoremRegistry provides a central registry for all theorem
classes and supports batch verification and LaTeX document generation.

Theorems defined:
  1. EvaluationSoundnessTheorem    (Ch63 §1) — scores reflect true performance
  2. AblationIsolationTheorem      (Ch63 §2) — ablation isolates contributions
  3. CalibrationConsistencyTheorem (Ch63 §3) — calibration matches frequencies
  4. ClauseCompletenessTheorem     (Ch63 §4) — clause set covers the domain
  5. ScoreMonotonicityTheorem      (Ch63 §5) — better systems score higher
"""
from __future__ import annotations

import json
import math
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, ClassVar, Dict, List, Optional, Sequence

try:
    from .models import EvaluationResult, AblationResult, CalibrationReport, ClauseResult
    from .clausewise_evaluation import ClauseSpecification
except Exception:
    pass

# ---------------------------------------------------------------------------
# Public exports
# ---------------------------------------------------------------------------

__all__ = [
    "TheoremMetadata",
    "EvaluationSoundnessTheorem",
    "AblationIsolationTheorem",
    "CalibrationConsistencyTheorem",
    "ClauseCompletenessTheorem",
    "ScoreMonotonicityTheorem",
    "EvaluationTheoremRegistry",
    "THEOREM_CATALOG",
]

# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _utcnow() -> float:
    """Return the current UTC timestamp as a float.

    Returns:
        Seconds since the Unix epoch (UTC).
    """
    return time.time()


def _uid() -> str:
    """Return a fresh UUID4 string.

    Returns:
        A random UUID4 string suitable for use as a unique identifier.
    """
    return str(uuid.uuid4())


def _clamp(v: float, lo: float, hi: float) -> float:
    """Clamp *v* to the closed interval [lo, hi].

    Args:
        v: Value to clamp.
        lo: Lower bound (inclusive).
        hi: Upper bound (inclusive).

    Returns:
        The clamped value: lo if v < lo, hi if v > hi, else v.
    """
    return max(lo, min(hi, v))


# ---------------------------------------------------------------------------
# TheoremMetadata
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class TheoremMetadata:
    """Immutable metadata record for a formal theorem.

    This dataclass stores all of the bibliographic and logical metadata for
    a single theorem as defined in theory2.tex Chapter 63.  It is intentionally
    immutable so that theorem classes can declare METADATA as a class variable
    without risking accidental mutation at runtime.

    Attributes:
        theorem_id: Unique identifier for this theorem (UUID4 string or slug).
        name: Human-readable name of the theorem.
        chapter_ref: Reference to the theory chapter (e.g. 'Ch63 §1').
        statement: Full formal statement of the theorem in plain English.
        assumptions: List of assumption strings required by the theorem.
        conclusion: The conclusion drawn when all assumptions hold.
        proof_sketch: Informal prose sketch of the proof strategy.
        tags: List of category tags (e.g. ['calibration', 'soundness']).
    """

    theorem_id: str
    name: str
    chapter_ref: str
    statement: str
    assumptions: list[str]
    conclusion: str
    proof_sketch: str
    tags: list[str]

    def to_dict(self) -> dict:
        """Serialise this metadata record to a plain dictionary.

        All values are JSON-serialisable.  Lists are copied to avoid sharing
        references with the frozen dataclass internals.

        Returns:
            Dict with all fields as JSON-serialisable values.
        """
        return {
            "theorem_id": self.theorem_id,
            "name": self.name,
            "chapter_ref": self.chapter_ref,
            "statement": self.statement,
            "assumptions": list(self.assumptions),
            "conclusion": self.conclusion,
            "proof_sketch": self.proof_sketch,
            "tags": list(self.tags),
        }

    def to_latex(self) -> str:
        """Render this theorem as a LaTeX theorem environment.

        Produces a ``\\begin{theorem}[name]...\\end{theorem}`` block that
        includes the statement, assumptions listed as an enumerate environment,
        and the conclusion.  A ``\\begin{proof}[Proof sketch]`` block follows
        immediately containing the informal proof sketch.

        Returns:
            Multi-line LaTeX string suitable for inclusion in a .tex file.
        """
        assumptions_latex = "\n".join(
            f"  \\item {a}" for a in self.assumptions
        )
        lines = [
            f"\\begin{{theorem}}[{self.name}]",
            f"\\label{{thm:{self.theorem_id}}}",
            f"% {self.chapter_ref}",
            "",
            self.statement,
            "",
            "\\textbf{Assumptions:}",
            "\\begin{enumerate}",
            assumptions_latex,
            "\\end{enumerate}",
            "",
            f"\\textbf{{Conclusion:}} {self.conclusion}",
            "\\end{theorem}",
            "",
            f"\\begin{{proof}}[Proof sketch]",
            self.proof_sketch,
            "\\end{proof}",
        ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# EvaluationSoundnessTheorem
# ---------------------------------------------------------------------------

class EvaluationSoundnessTheorem:
    """Theorem: evaluation scores reflect true system performance (Ch63 §1).

    The soundness theorem guarantees that the evaluation procedure assigns
    higher scores to better-performing systems.  Its verify() method checks
    that the provided EvaluationResult satisfies structural soundness conditions:
    non-empty results, scores in [0,1], and consistency between clause-level
    and overall scores.

    The class carries a class-level METADATA attribute of type TheoremMetadata
    that encodes the full formal statement from theory2.tex Chapter 63 §1.

    Attributes:
        METADATA: Frozen TheoremMetadata instance with all formal details.
    """

    METADATA: ClassVar[TheoremMetadata] = TheoremMetadata(
        theorem_id="soundness-ch63-s1",
        name="EvaluationSoundness",
        chapter_ref="Ch63 §1",
        statement=(
            "For any evaluation design D and system S, if S is evaluated "
            "under D using the clausewise scoring procedure, then the resulting "
            "score \\sigma(S, D) \\in [0,1] is a monotone function of the "
            "system's true performance on each clause criterion."
        ),
        assumptions=[
            "The evaluation design D is well-formed (non-empty clause set).",
            "Each clause criterion is independently measurable.",
            "The scoring function for each clause type is bounded in [0,1].",
            "Clause weights are non-negative and sum to 1.",
        ],
        conclusion=(
            "The overall score \\sigma(S, D) faithfully reflects system "
            "performance: higher-performing systems receive strictly higher scores."
        ),
        proof_sketch=(
            "By construction each clause scorer returns a value in [0,1].  "
            "The weighted average of bounded values is itself bounded in [0,1] "
            "because the weights are non-negative and sum to 1.  "
            "Monotonicity follows from the fact that improving performance on "
            "any clause strictly increases its sub-score, which propagates to "
            "the weighted average provided the clause weight is strictly positive.  "
            "Because all clause weights are non-negative by assumption 4, and at "
            "least one is positive (the design is non-empty by assumption 1), the "
            "overall score is a strictly increasing function of each non-zero-weight "
            "clause score.  The conclusion follows by induction over the clause set."
        ),
        tags=["soundness", "clausewise", "monotonicity", "ch63"],
    )

    @classmethod
    def verify(cls, result: object, ground_truth: dict) -> dict:
        """Verify the soundness theorem against an EvaluationResult.

        Checks that:
        - The result has a numeric score in [0, 1].
        - The result contains at least one clause-level result.
        - The weighted average of clause scores equals the overall score
          (within floating-point tolerance of 0.05).
        - The overall score is consistent with the ground_truth dict if
          a 'expected_min_score' or 'expected_max_score' key is provided.
        - Each individual clause score is bounded within [0, 1].

        Args:
            result: EvaluationResult object or dict with keys 'score' and
                'clause_results' (list of dicts with 'score' and 'weight').
            ground_truth: Dict optionally containing 'expected_min_score'
                (float) and 'expected_max_score' (float) for bounds checking.

        Returns:
            Verification dict with keys: theorem (str), passed (bool),
            checks (list of check dicts), score (float), violations (list
            of str), verified_at (float UTC timestamp).
            'passed' is True iff all checks pass and violations is empty.
        """
        if isinstance(result, dict):
            score = float(result.get("score", -1.0))
            clause_results = result.get("clause_results", [])
        else:
            score = float(getattr(result, "score", -1.0))
            clause_results = getattr(result, "clause_results", [])

        checks: list[dict] = []
        violations: list[str] = []

        # Check 1: overall score in [0, 1]
        in_range = 0.0 <= score <= 1.0
        checks.append({"check": "score_in_unit_interval", "passed": in_range, "value": score})
        if not in_range:
            violations.append(f"Score {score:.4f} is outside [0, 1].")

        # Check 2: non-empty clause results
        non_empty = len(clause_results) > 0
        checks.append({"check": "non_empty_clause_results", "passed": non_empty, "value": len(clause_results)})
        if not non_empty:
            violations.append("Clause results list is empty.")

        # Check 3: each clause score in [0, 1]
        all_clause_bounded = True
        for i, cr in enumerate(clause_results):
            cs = float(cr.get("score", 0.0) if isinstance(cr, dict) else getattr(cr, "score", 0.0))
            if not (0.0 <= cs <= 1.0):
                all_clause_bounded = False
                violations.append(f"Clause result {i} score {cs:.4f} outside [0,1].")
        checks.append({"check": "all_clause_scores_bounded", "passed": all_clause_bounded})

        # Check 4: weighted average consistency
        if clause_results:
            total_weight = 0.0
            weighted_sum = 0.0
            for cr in clause_results:
                if isinstance(cr, dict):
                    w = float(cr.get("weight", 1.0))
                    s = float(cr.get("score", 0.0))
                else:
                    w = float(getattr(cr, "weight", 1.0))
                    s = float(getattr(cr, "score", 0.0))
                weighted_sum += w * s
                total_weight += w
            if total_weight > 0.0:
                expected_score = weighted_sum / total_weight
                consistent = abs(expected_score - score) < 0.05
                checks.append({
                    "check": "weighted_average_consistency",
                    "passed": consistent,
                    "expected": round(expected_score, 6),
                    "actual": round(score, 6),
                })
                if not consistent:
                    violations.append(
                        f"Overall score {score:.4f} deviates from weighted avg {expected_score:.4f}."
                    )

        # Check 5: ground truth lower bound
        if ground_truth and "expected_min_score" in ground_truth:
            min_ok = score >= float(ground_truth["expected_min_score"])
            checks.append({"check": "min_score_bound", "passed": min_ok,
                           "bound": ground_truth["expected_min_score"]})
            if not min_ok:
                violations.append(
                    f"Score {score:.4f} below expected minimum {ground_truth['expected_min_score']}."
                )

        # Check 6: ground truth upper bound
        if ground_truth and "expected_max_score" in ground_truth:
            max_ok = score <= float(ground_truth["expected_max_score"])
            checks.append({"check": "max_score_bound", "passed": max_ok,
                           "bound": ground_truth["expected_max_score"]})
            if not max_ok:
                violations.append(
                    f"Score {score:.4f} above expected maximum {ground_truth['expected_max_score']}."
                )

        passed = len(violations) == 0
        return {
            "theorem": cls.METADATA.name,
            "passed": passed,
            "checks": checks,
            "score": score,
            "violations": violations,
            "verified_at": _utcnow(),
        }

    @classmethod
    def check_preconditions(cls, result: object) -> list[str]:
        """List any unmet preconditions for the soundness theorem.

        Args:
            result: EvaluationResult object or dict.

        Returns:
            List of unmet precondition description strings.  An empty list
            indicates that all preconditions are satisfied and verify() can
            be expected to run without structural errors.
        """
        unmet: list[str] = []
        if isinstance(result, dict):
            if "score" not in result:
                unmet.append("Result missing 'score' field.")
            if "clause_results" not in result or not result["clause_results"]:
                unmet.append("Result missing non-empty 'clause_results' field.")
        else:
            if not hasattr(result, "score"):
                unmet.append("Result object missing 'score' attribute.")
            cr = getattr(result, "clause_results", None)
            if cr is None or len(cr) == 0:
                unmet.append("Result object missing non-empty 'clause_results'.")
        return unmet

    @classmethod
    def proof_sketch(cls) -> str:
        """Return the informal proof sketch for this theorem.

        Returns:
            Prose proof sketch string from METADATA.proof_sketch.
        """
        return cls.METADATA.proof_sketch

    @classmethod
    def latex_statement(cls) -> str:
        """Return the LaTeX theorem environment for this theorem.

        Returns:
            LaTeX string with begin/end theorem and proof sketch environments.
        """
        return cls.METADATA.to_latex()


# ---------------------------------------------------------------------------
# AblationIsolationTheorem
# ---------------------------------------------------------------------------

class AblationIsolationTheorem:
    """Theorem: ablation results isolate individual component contributions (Ch63 §2).

    The ablation isolation theorem asserts that a properly designed ablation
    study can attribute performance differences to individual system components.
    Its verify() method checks structural conditions on a list of ablation
    results: unique component names, bounded delta scores, and approximate
    additivity of deltas with respect to the overall performance gap.

    Attributes:
        METADATA: Frozen TheoremMetadata instance with all formal details.
    """

    METADATA: ClassVar[TheoremMetadata] = TheoremMetadata(
        theorem_id="ablation-isolation-ch63-s2",
        name="AblationIsolation",
        chapter_ref="Ch63 §2",
        statement=(
            "For a system S decomposed into components C_1, ..., C_k, if each "
            "component C_i can be independently removed to produce a degraded "
            "system S_{-i}, then the ablation delta \\delta_i = \\sigma(S) - "
            "\\sigma(S_{-i}) isolates the marginal contribution of C_i to the "
            "overall score \\sigma(S)."
        ),
        assumptions=[
            "Components C_1, ..., C_k are independently removable without "
            "altering the remaining components.",
            "The baseline system S_{-i} is identical to S except for the "
            "absence of component C_i.",
            "Each ablation study is run under identical evaluation conditions.",
            "Component contributions are approximately additive: "
            "\\sum_i \\delta_i \\approx \\sigma(S) - \\sigma(S_{baseline}).",
        ],
        conclusion=(
            "The ablation deltas \\delta_1, ..., \\delta_k provide an "
            "attribution of the overall performance gap to individual components, "
            "up to interaction effects bounded by the non-additivity residual."
        ),
        proof_sketch=(
            "Each ablation study removes exactly one component while holding "
            "all others fixed.  Under the independence assumption the removed "
            "component cannot influence the outputs of remaining components, so "
            "the delta \\delta_i = \\sigma(S) - \\sigma(S_{-i}) captures exactly "
            "the marginal contribution of C_i.  Approximate additivity follows "
            "from a first-order Taylor expansion of \\sigma around the all-components "
            "baseline: higher-order interaction terms are bounded by the product "
            "of pairwise component covariances, which are small under assumption 1.  "
            "The total residual |\\sum_i \\delta_i - (\\sigma(S) - \\sigma(S_0))| "
            "is therefore O(k \\cdot \\epsilon^2) where \\epsilon is the maximum "
            "pairwise interaction magnitude."
        ),
        tags=["ablation", "attribution", "isolation", "ch63"],
    )

    @classmethod
    def verify(cls, ablation_results: list, ground_truth: dict | None = None) -> dict:
        """Verify the ablation isolation theorem against a list of ablation results.

        Checks that:
        - Each ablation result has a 'component' name and a 'delta' score.
        - All component names are unique (no duplicate ablations).
        - Each delta score is a finite float.
        - If ground_truth provides 'overall_gap' (float), the sum of deltas
          approximates it within a tolerance of 0.1.

        Args:
            ablation_results: List of dicts or objects with 'component' (str)
                and 'delta' (float) attributes/keys.
            ground_truth: Optional dict optionally containing 'overall_gap'
                (float, the full score difference from baseline to full system)
                and 'tolerance' (float, default 0.1) for additivity checking.

        Returns:
            Verification dict with keys: theorem (str), passed (bool),
            checks (list), violations (list), num_components (int),
            delta_sum (float), verified_at (float).
        """
        if ground_truth is None:
            ground_truth = {}

        checks: list[dict] = []
        violations: list[str] = []

        # Check 1: all results have required fields
        has_required = True
        for i, ar in enumerate(ablation_results):
            if isinstance(ar, dict):
                has_comp = "component" in ar
                has_delta = "delta" in ar
            else:
                has_comp = hasattr(ar, "component")
                has_delta = hasattr(ar, "delta")
            if not (has_comp and has_delta):
                has_required = False
                violations.append(f"Ablation result {i} missing 'component' or 'delta' field.")
        checks.append({"check": "all_results_have_required_fields", "passed": has_required})

        # Check 2: unique component names
        names: list[str] = []
        for ar in ablation_results:
            name = ar.get("component") if isinstance(ar, dict) else getattr(ar, "component", None)
            if name is not None:
                names.append(str(name))
        unique_names = len(names) == len(set(names))
        checks.append({"check": "unique_component_names", "passed": unique_names,
                       "num_components": len(names), "num_unique": len(set(names))})
        if not unique_names:
            duplicates = [n for n in names if names.count(n) > 1]
            violations.append(f"Duplicate component names found: {list(set(duplicates))}.")

        # Check 3: all deltas are finite floats
        all_finite = True
        delta_values: list[float] = []
        for i, ar in enumerate(ablation_results):
            raw = ar.get("delta") if isinstance(ar, dict) else getattr(ar, "delta", None)
            try:
                d = float(raw)
                if not math.isfinite(d):
                    all_finite = False
                    violations.append(f"Ablation result {i} delta is not finite: {raw}.")
                delta_values.append(d)
            except (TypeError, ValueError):
                all_finite = False
                violations.append(f"Ablation result {i} delta is not numeric: {raw!r}.")
        checks.append({"check": "all_deltas_finite", "passed": all_finite})

        delta_sum = sum(delta_values)

        # Check 4: approximate additivity with overall_gap
        if "overall_gap" in ground_truth and delta_values:
            tolerance = float(ground_truth.get("tolerance", 0.1))
            overall_gap = float(ground_truth["overall_gap"])
            additive = abs(delta_sum - overall_gap) <= tolerance
            checks.append({
                "check": "approximate_additivity",
                "passed": additive,
                "delta_sum": round(delta_sum, 6),
                "overall_gap": overall_gap,
                "residual": round(abs(delta_sum - overall_gap), 6),
                "tolerance": tolerance,
            })
            if not additive:
                violations.append(
                    f"Delta sum {delta_sum:.4f} deviates from overall gap "
                    f"{overall_gap:.4f} by {abs(delta_sum - overall_gap):.4f} "
                    f"(tolerance {tolerance})."
                )

        passed = len(violations) == 0
        return {
            "theorem": cls.METADATA.name,
            "passed": passed,
            "checks": checks,
            "violations": violations,
            "num_components": len(names),
            "delta_sum": round(delta_sum, 6),
            "verified_at": _utcnow(),
        }

    @classmethod
    def check_independence(cls, results: list) -> bool:
        """Check that component names in *results* are all unique.

        Uniqueness of component names is a necessary (though not sufficient)
        condition for the independence assumption of the ablation theorem.

        Args:
            results: List of ablation result dicts or objects with a
                'component' key/attribute.

        Returns:
            True if all component names are unique, False otherwise.
        """
        names: list[str] = []
        for ar in results:
            name = ar.get("component") if isinstance(ar, dict) else getattr(ar, "component", None)
            if name is not None:
                names.append(str(name))
        return len(names) == len(set(names))

    @classmethod
    def proof_sketch(cls) -> str:
        """Return the informal proof sketch for this theorem.

        Returns:
            Prose proof sketch string from METADATA.proof_sketch.
        """
        return cls.METADATA.proof_sketch

    @classmethod
    def latex_statement(cls) -> str:
        """Return the LaTeX theorem environment for this theorem.

        Returns:
            LaTeX string with begin/end theorem and proof sketch environments.
        """
        return cls.METADATA.to_latex()


# ---------------------------------------------------------------------------
# CalibrationConsistencyTheorem
# ---------------------------------------------------------------------------

class CalibrationConsistencyTheorem:
    """Theorem: calibrated scores are consistent with empirical frequencies (Ch63 §3).

    The calibration consistency theorem guarantees that after calibration the
    evaluation scores assigned by the model agree with the true empirical
    pass/fail frequencies across the evaluation corpus.  The key metric is the
    Expected Calibration Error (ECE); the theorem asserts ECE ≤ 0.05 after
    the calibration procedure is applied.

    Attributes:
        METADATA: Frozen TheoremMetadata instance with all formal details.
    """

    METADATA: ClassVar[TheoremMetadata] = TheoremMetadata(
        theorem_id="calibration-consistency-ch63-s3",
        name="CalibrationConsistency",
        chapter_ref="Ch63 §3",
        statement=(
            "Let R be a calibration report produced by the evaluation calibration "
            "procedure.  If the sample size n \\geq 100 and observations are "
            "independently drawn, then the Expected Calibration Error "
            "ECE(R) = \\sum_b |\\hat{p}_b - \\bar{y}_b| \\cdot n_b / n "
            "satisfies ECE(R) \\leq 0.05 after calibration."
        ),
        assumptions=[
            "The evaluation corpus contains at least 100 independently drawn examples.",
            "Examples are drawn i.i.d. from the target distribution.",
            "The ECE bound of 0.05 is achievable by the calibration procedure.",
            "Score monotonicity is preserved: higher raw scores map to higher "
            "calibrated scores.",
        ],
        conclusion=(
            "The calibrated scores are consistent with empirical frequencies in "
            "the sense that ECE \\leq 0.05, meaning the model's confidence scores "
            "deviate from true frequencies by at most 5 percentage points on average."
        ),
        proof_sketch=(
            "The calibration procedure partitions the score range into M equal-width "
            "bins and fits a temperature parameter T that minimises the negative "
            "log-likelihood on a held-out calibration set.  By the law of large "
            "numbers each bin's empirical frequency \\bar{y}_b converges to the true "
            "conditional probability E[Y | score \\in bin b] as the calibration set "
            "size grows.  The fitted temperature T rescales raw logits so that the "
            "bin-average confidence \\hat{p}_b matches \\bar{y}_b.  The resulting ECE "
            "is bounded by O(1/\\sqrt{n}) by a standard concentration inequality, "
            "which is below 0.05 for n \\geq 100 under mild regularity conditions.  "
            "Monotonicity is preserved because temperature scaling is a strictly "
            "monotone transformation of the logit."
        ),
        tags=["calibration", "ece", "consistency", "ch63"],
    )

    @classmethod
    def verify(cls, report: object, tolerance: float = 0.05) -> dict:
        """Verify the calibration consistency theorem against a CalibrationReport.

        Checks that:
        - The report has an 'ece' attribute or key that is a finite float.
        - ECE <= tolerance (default 0.05).
        - If 'before_ece' is also present, calibration strictly improved ECE
          (before_ece > after_ece).

        Args:
            report: CalibrationReport object or dict with keys 'ece' (float),
                optionally 'before_ece' (float for pre-calibration ECE).
            tolerance: Maximum acceptable ECE value.  Defaults to 0.05.

        Returns:
            Verification dict with keys: theorem (str), passed (bool),
            checks (list), ece (float), violations (list), verified_at (float).
        """
        if isinstance(report, dict):
            ece = report.get("ece", None)
            before_ece = report.get("before_ece", None)
        else:
            ece = getattr(report, "ece", None)
            before_ece = getattr(report, "before_ece", None)

        checks: list[dict] = []
        violations: list[str] = []

        # Check 1: ECE field present and numeric
        ece_present = ece is not None
        checks.append({"check": "ece_field_present", "passed": ece_present})
        if not ece_present:
            violations.append("Calibration report missing 'ece' field.")
            return {
                "theorem": cls.METADATA.name,
                "passed": False,
                "checks": checks,
                "ece": None,
                "violations": violations,
                "verified_at": _utcnow(),
            }

        try:
            ece_val = float(ece)
        except (TypeError, ValueError):
            violations.append(f"ECE value {ece!r} is not numeric.")
            return {
                "theorem": cls.METADATA.name,
                "passed": False,
                "checks": checks,
                "ece": None,
                "violations": violations,
                "verified_at": _utcnow(),
            }

        # Check 2: ECE is finite
        ece_finite = math.isfinite(ece_val)
        checks.append({"check": "ece_is_finite", "passed": ece_finite, "value": ece_val})
        if not ece_finite:
            violations.append(f"ECE value {ece_val} is not finite.")

        # Check 3: ECE <= tolerance
        ece_bounded = ece_val <= tolerance
        checks.append({
            "check": "ece_within_tolerance",
            "passed": ece_bounded,
            "ece": round(ece_val, 6),
            "tolerance": tolerance,
        })
        if not ece_bounded:
            violations.append(f"ECE {ece_val:.4f} exceeds tolerance {tolerance}.")

        # Check 4: calibration improved ECE (if before_ece provided)
        if before_ece is not None:
            try:
                before_ece_val = float(before_ece)
                improved = before_ece_val > ece_val
                checks.append({
                    "check": "calibration_improved_ece",
                    "passed": improved,
                    "before_ece": round(before_ece_val, 6),
                    "after_ece": round(ece_val, 6),
                    "improvement": round(before_ece_val - ece_val, 6),
                })
                if not improved:
                    violations.append(
                        f"Calibration did not improve ECE: before={before_ece_val:.4f}, "
                        f"after={ece_val:.4f}."
                    )
            except (TypeError, ValueError):
                violations.append(f"'before_ece' value {before_ece!r} is not numeric.")

        passed = len(violations) == 0
        return {
            "theorem": cls.METADATA.name,
            "passed": passed,
            "checks": checks,
            "ece": round(ece_val, 6),
            "violations": violations,
            "verified_at": _utcnow(),
        }

    @classmethod
    def check_calibration_bound(cls, report: object) -> bool:
        """Check whether the report's ECE satisfies the 0.05 bound.

        Args:
            report: CalibrationReport object or dict with an 'ece' key/attribute.

        Returns:
            True if ECE is present, numeric, finite, and <= 0.05; False otherwise.
        """
        ece = report.get("ece") if isinstance(report, dict) else getattr(report, "ece", None)
        if ece is None:
            return False
        try:
            val = float(ece)
            return math.isfinite(val) and val <= 0.05
        except (TypeError, ValueError):
            return False

    @classmethod
    def proof_sketch(cls) -> str:
        """Return the informal proof sketch for this theorem.

        Returns:
            Prose proof sketch string from METADATA.proof_sketch.
        """
        return cls.METADATA.proof_sketch

    @classmethod
    def latex_statement(cls) -> str:
        """Return the LaTeX theorem environment for this theorem.

        Returns:
            LaTeX string with begin/end theorem and proof sketch environments.
        """
        return cls.METADATA.to_latex()


# ---------------------------------------------------------------------------
# ClauseCompletenessTheorem
# ---------------------------------------------------------------------------

# The five canonical clause types that every complete evaluation design must cover.
_CANONICAL_CLAUSE_TYPES: list[str] = [
    "SOUNDNESS",
    "PRECISION",
    "RECALL",
    "CONSISTENCY",
    "COMPLETENESS",
]


class ClauseCompletenessTheorem:
    """Theorem: the clause set is complete for the evaluation domain (Ch63 §4).

    The clause completeness theorem asserts that the set of clause specifications
    in an evaluation design covers the entire evaluation domain: every aspect of
    system behaviour that the domain requires to be tested is addressed by at
    least one clause.  Its verify() method checks coverage against a domain dict
    and checks for internal non-redundancy.

    Attributes:
        METADATA: Frozen TheoremMetadata instance with all formal details.
    """

    METADATA: ClassVar[TheoremMetadata] = TheoremMetadata(
        theorem_id="clause-completeness-ch63-s4",
        name="ClauseCompleteness",
        chapter_ref="Ch63 §4",
        statement=(
            "Let \\mathcal{C} = \\{c_1, ..., c_m\\} be the clause set of an "
            "evaluation design D, and let \\Omega be the finite evaluation domain.  "
            "If every aspect \\omega \\in \\Omega is addressed by at least one "
            "clause c_i \\in \\mathcal{C} via its criterion, then \\mathcal{C} "
            "is complete for \\Omega."
        ),
        assumptions=[
            "The evaluation domain \\Omega is finite and explicitly enumerated.",
            "Each clause type in {SOUNDNESS, PRECISION, RECALL, CONSISTENCY, "
            "COMPLETENESS} corresponds to a disjoint partition of \\Omega.",
            "No two distinct clauses in \\mathcal{C} have identical criteria "
            "(non-redundancy condition).",
            "Clause weights are distributed such that each domain aspect has "
            "positive total weight.",
        ],
        conclusion=(
            "The clause set \\mathcal{C} provides complete coverage of \\Omega: "
            "every domain aspect is measurable and no aspect is evaluated with "
            "zero total weight."
        ),
        proof_sketch=(
            "Completeness is proved by exhaustive coverage checking: for each "
            "aspect \\omega \\in \\Omega we exhibit a clause c_i whose criterion "
            "mentions \\omega.  Non-redundancy follows from the distinctness "
            "condition: if two clauses had identical criteria they would be "
            "indistinguishable under any evaluation, contradicting the "
            "independent measurability assumption.  Weight positivity holds "
            "because by assumption 4 each aspect receives positive total weight; "
            "since the weight distribution is over a finite set, the minimum "
            "weight is strictly positive."
        ),
        tags=["completeness", "coverage", "clause-set", "ch63"],
    )

    @classmethod
    def verify(cls, specifications: list, domain: dict) -> dict:
        """Verify the clause completeness theorem against a set of specifications.

        Checks that:
        - Every key in *domain* is mentioned in at least one specification's
          'criteria' or 'clause_type' field.
        - Coverage fraction (covered domain keys / total domain keys) is computed.
        - No two specifications have identical 'criteria' strings (non-redundancy).

        Args:
            specifications: List of ClauseSpecification objects or dicts, each
                with at minimum a 'clause_type' (str) and optionally 'criteria'
                (str or list of str).
            domain: Dict whose keys are domain aspect names that must be covered.
                Values are not used; only keys are checked for coverage.

        Returns:
            Verification dict with keys: theorem (str), passed (bool),
            checks (list), coverage_fraction (float), uncovered_keys (list),
            violations (list), verified_at (float).
        """
        checks: list[dict] = []
        violations: list[str] = []

        domain_keys = list(domain.keys())
        total_keys = len(domain_keys)

        # Collect all criteria text and clause types from specs
        all_criteria: list[str] = []
        clause_types_present: set[str] = set()
        for spec in specifications:
            if isinstance(spec, dict):
                ct = str(spec.get("clause_type", ""))
                criteria = spec.get("criteria", "")
            else:
                ct = str(getattr(spec, "clause_type", ""))
                criteria = getattr(spec, "criteria", "")
            clause_types_present.add(ct.upper())
            if isinstance(criteria, list):
                all_criteria.extend(str(c) for c in criteria)
            else:
                all_criteria.append(str(criteria))

        # Check 1: coverage of domain keys
        covered_keys: list[str] = []
        uncovered_keys: list[str] = []
        combined_text = " ".join(all_criteria + list(clause_types_present)).lower()
        for key in domain_keys:
            if key.lower() in combined_text:
                covered_keys.append(key)
            else:
                uncovered_keys.append(key)

        coverage_fraction = len(covered_keys) / total_keys if total_keys > 0 else 1.0
        full_coverage = len(uncovered_keys) == 0
        checks.append({
            "check": "domain_coverage",
            "passed": full_coverage,
            "covered": len(covered_keys),
            "total": total_keys,
            "fraction": round(coverage_fraction, 4),
        })
        if not full_coverage:
            violations.append(f"Uncovered domain keys: {uncovered_keys}.")

        # Check 2: canonical clause types present
        canonical_coverage = [ct for ct in _CANONICAL_CLAUSE_TYPES if ct in clause_types_present]
        canonical_fraction = len(canonical_coverage) / len(_CANONICAL_CLAUSE_TYPES)
        checks.append({
            "check": "canonical_clause_types_present",
            "passed": canonical_fraction >= 0.8,
            "present": sorted(canonical_coverage),
            "fraction": round(canonical_fraction, 4),
        })
        if canonical_fraction < 0.8:
            missing = [ct for ct in _CANONICAL_CLAUSE_TYPES if ct not in clause_types_present]
            violations.append(f"Missing canonical clause types: {missing}.")

        # Check 3: non-redundancy (no duplicate criteria)
        non_empty_criteria = [c for c in all_criteria if c.strip()]
        unique_criteria = len(non_empty_criteria) == len(set(non_empty_criteria))
        checks.append({"check": "non_redundant_criteria", "passed": unique_criteria,
                       "num_criteria": len(non_empty_criteria),
                       "num_unique": len(set(non_empty_criteria))})
        if not unique_criteria:
            duplicates = [c for c in non_empty_criteria if non_empty_criteria.count(c) > 1]
            violations.append(f"Duplicate criteria found: {list(set(duplicates))[:3]}.")

        passed = len(violations) == 0
        return {
            "theorem": cls.METADATA.name,
            "passed": passed,
            "checks": checks,
            "coverage_fraction": round(coverage_fraction, 4),
            "uncovered_keys": uncovered_keys,
            "violations": violations,
            "verified_at": _utcnow(),
        }

    @classmethod
    def check_coverage(cls, specifications: list) -> float:
        """Compute the fraction of canonical clause types covered by *specifications*.

        Args:
            specifications: List of ClauseSpecification objects or dicts, each
                with a 'clause_type' key/attribute.

        Returns:
            Float in [0, 1] representing the fraction of
            ['SOUNDNESS', 'PRECISION', 'RECALL', 'CONSISTENCY', 'COMPLETENESS']
            that appear in the specifications' clause_type fields.
        """
        present: set[str] = set()
        for spec in specifications:
            ct = spec.get("clause_type", "") if isinstance(spec, dict) else getattr(spec, "clause_type", "")
            present.add(str(ct).upper())
        covered = sum(1 for ct in _CANONICAL_CLAUSE_TYPES if ct in present)
        return covered / len(_CANONICAL_CLAUSE_TYPES)

    @classmethod
    def proof_sketch(cls) -> str:
        """Return the informal proof sketch for this theorem.

        Returns:
            Prose proof sketch string from METADATA.proof_sketch.
        """
        return cls.METADATA.proof_sketch

    @classmethod
    def latex_statement(cls) -> str:
        """Return the LaTeX theorem environment for this theorem.

        Returns:
            LaTeX string with begin/end theorem and proof sketch environments.
        """
        return cls.METADATA.to_latex()


# ---------------------------------------------------------------------------
# ScoreMonotonicityTheorem
# ---------------------------------------------------------------------------

class ScoreMonotonicityTheorem:
    """Theorem: better systems score higher under consistent evaluation (Ch63 §5).

    The score monotonicity theorem asserts that when two systems A and B are
    evaluated under the same evaluation design D, the system with objectively
    better performance receives a strictly higher score.  Its verify() method
    checks that a provided ranking of system names is consistent with their
    observed scores.

    Attributes:
        METADATA: Frozen TheoremMetadata instance with all formal details.
    """

    METADATA: ClassVar[TheoremMetadata] = TheoremMetadata(
        theorem_id="score-monotonicity-ch63-s5",
        name="ScoreMonotonicity",
        chapter_ref="Ch63 §5",
        statement=(
            "Let S_1, ..., S_n be systems evaluated under a common evaluation "
            "design D, and let \\succ be a ground-truth performance ordering.  "
            "If S_i \\succ S_j (S_i is objectively better than S_j), then the "
            "evaluation procedure assigns \\sigma(S_i, D) > \\sigma(S_j, D)."
        ),
        assumptions=[
            "All systems are evaluated under identical evaluation conditions "
            "(same D, same evaluation corpus).",
            "The evaluation procedure is deterministic given the same inputs.",
            "Systems are evaluated independently; the score of one system does "
            "not influence the score of another.",
            "The ground-truth performance ordering \\succ is a valid strict "
            "partial order (irreflexive, transitive, asymmetric).",
        ],
        conclusion=(
            "The score ordering \\sigma(S_1) \\geq \\sigma(S_2) \\geq ... \\geq "
            "\\sigma(S_n) is consistent with the ground-truth ranking whenever "
            "the evaluation design is sound and the assumptions hold."
        ),
        proof_sketch=(
            "Monotonicity follows directly from the soundness theorem (Ch63 §1) "
            "applied to each pair of systems.  If S_i \\succ S_j then by definition "
            "S_i performs at least as well as S_j on every clause criterion.  "
            "By soundness each clause scorer is a monotone function of performance, "
            "so \\sigma_c(S_i) \\geq \\sigma_c(S_j) for every clause c.  "
            "Taking the weighted average preserves the inequality, giving "
            "\\sigma(S_i) \\geq \\sigma(S_j).  Strict inequality holds whenever "
            "S_i strictly dominates S_j on at least one positively-weighted clause.  "
            "The full ranking follows by transitivity of \\geq."
        ),
        tags=["monotonicity", "ranking", "ordering", "ch63"],
    )

    @classmethod
    def verify(cls, results: list, rankings: list[str]) -> dict:
        """Verify the score monotonicity theorem against a list of system results.

        Checks that the score ordering of systems in *results* is consistent
        with the ordering implied by *rankings* (first entry = best system).

        Steps:
        1. Build a score dict mapping system name -> score from *results*.
        2. Filter *rankings* to those system names that appear in the score dict.
        3. For each consecutive pair (rankings[i], rankings[i+1]) check that
           score[rankings[i]] >= score[rankings[i+1]].
        4. Count the number of inversions (pairs where the better-ranked system
           has a strictly lower score).

        Args:
            results: List of dicts or objects with 'system_name' (str) and
                'score' (float) keys/attributes.
            rankings: List of system name strings ordered from best (index 0)
                to worst (last index) according to ground truth.

        Returns:
            Verification dict with keys: theorem (str), passed (bool),
            checks (list), num_inversions (int), monotone (bool),
            violations (list), verified_at (float).
        """
        checks: list[dict] = []
        violations: list[str] = []

        # Build score lookup
        score_map: dict[str, float] = {}
        for r in results:
            if isinstance(r, dict):
                name = r.get("system_name", r.get("name", None))
                score = r.get("score", None)
            else:
                name = getattr(r, "system_name", getattr(r, "name", None))
                score = getattr(r, "score", None)
            if name is not None and score is not None:
                try:
                    score_map[str(name)] = float(score)
                except (TypeError, ValueError):
                    violations.append(f"Score for system '{name}' is not numeric: {score!r}.")

        # Check 1: all ranked systems have scores
        ranked_with_scores = [s for s in rankings if s in score_map]
        missing_scores = [s for s in rankings if s not in score_map]
        all_have_scores = len(missing_scores) == 0
        checks.append({
            "check": "all_ranked_systems_have_scores",
            "passed": all_have_scores,
            "ranked_count": len(rankings),
            "scored_count": len(ranked_with_scores),
            "missing": missing_scores,
        })
        if not all_have_scores:
            violations.append(f"No scores found for ranked systems: {missing_scores}.")

        # Check 2: monotone ordering — count inversions
        num_inversions = 0
        inversion_pairs: list[dict] = []
        for i in range(len(ranked_with_scores) - 1):
            s_better = ranked_with_scores[i]
            s_worse = ranked_with_scores[i + 1]
            score_better = score_map[s_better]
            score_worse = score_map[s_worse]
            if score_better < score_worse:
                num_inversions += 1
                inversion_pairs.append({
                    "better_system": s_better,
                    "worse_system": s_worse,
                    "better_score": round(score_better, 6),
                    "worse_score": round(score_worse, 6),
                })
                violations.append(
                    f"Inversion: '{s_better}' (score={score_better:.4f}) ranked above "
                    f"'{s_worse}' (score={score_worse:.4f}) but has lower score."
                )
        monotone = num_inversions == 0
        checks.append({
            "check": "monotone_score_ordering",
            "passed": monotone,
            "num_inversions": num_inversions,
            "inversions": inversion_pairs,
        })

        passed = len(violations) == 0
        return {
            "theorem": cls.METADATA.name,
            "passed": passed,
            "checks": checks,
            "num_inversions": num_inversions,
            "monotone": monotone,
            "violations": violations,
            "verified_at": _utcnow(),
        }

    @classmethod
    def check_monotonicity(cls, scores_a: list[float], scores_b: list[float]) -> bool:
        """Check that the mean of *scores_a* is >= the mean of *scores_b*.

        This is a lightweight pairwise monotonicity check: system A is
        considered to dominate system B if its average score across a sample
        of evaluations is at least as high.

        Args:
            scores_a: List of float scores for system A.
            scores_b: List of float scores for system B.

        Returns:
            True if mean(scores_a) >= mean(scores_b), or if either list is
            empty (vacuously true).
        """
        if not scores_a or not scores_b:
            return True
        mean_a = sum(scores_a) / len(scores_a)
        mean_b = sum(scores_b) / len(scores_b)
        return mean_a >= mean_b

    @classmethod
    def proof_sketch(cls) -> str:
        """Return the informal proof sketch for this theorem.

        Returns:
            Prose proof sketch string from METADATA.proof_sketch.
        """
        return cls.METADATA.proof_sketch

    @classmethod
    def latex_statement(cls) -> str:
        """Return the LaTeX theorem environment for this theorem.

        Returns:
            LaTeX string with begin/end theorem and proof sketch environments.
        """
        return cls.METADATA.to_latex()


# ---------------------------------------------------------------------------
# EvaluationTheoremRegistry
# ---------------------------------------------------------------------------

class EvaluationTheoremRegistry:
    """Central registry for all EvaluationDesign theorem classes.

    Theorem classes are registered at module load time via auto-registration
    calls at the bottom of this file.  The registry provides methods for batch
    verification, LaTeX document generation, and plain-text summaries.

    Class Attributes:
        _registry: Dict mapping theorem class name (str) to theorem class (type).
    """

    _registry: ClassVar[dict] = {}

    @classmethod
    def register(cls, theorem_class: type) -> None:
        """Register a theorem class under its class name.

        After registration the theorem class is available via get() and will
        be included in verify_all(), to_latex_document(), and summary() output.

        Args:
            theorem_class: A theorem class with a METADATA class attribute of
                type TheoremMetadata and a verify() classmethod.

        Returns:
            None.
        """
        name = theorem_class.__name__
        cls._registry[name] = theorem_class

    @classmethod
    def get(cls, name: str) -> type | None:
        """Retrieve a registered theorem class by name.

        Args:
            name: The class name of the theorem (e.g. 'EvaluationSoundnessTheorem').

        Returns:
            The theorem class, or None if not registered.
        """
        return cls._registry.get(name)

    @classmethod
    def list_all(cls) -> list[str]:
        """List all registered theorem class names.

        Returns:
            Sorted list of registered theorem class name strings.
        """
        return sorted(cls._registry.keys())

    @classmethod
    def verify_all(cls, context: dict) -> dict:
        """Run verify() on all registered theorems using *context*.

        Each theorem class's verify() is called with positional arguments
        extracted from *context*.  The calling convention varies by theorem:
        - EvaluationSoundnessTheorem.verify(result, ground_truth)
        - AblationIsolationTheorem.verify(ablation_results, ground_truth)
        - CalibrationConsistencyTheorem.verify(report, tolerance)
        - ClauseCompletenessTheorem.verify(specifications, domain)
        - ScoreMonotonicityTheorem.verify(results, rankings)

        If a theorem's verify() raises an exception the error is captured
        and the theorem is marked as failed with 'passed': False.

        Args:
            context: Dict containing any subset of: result, ground_truth,
                ablation_results, report, tolerance, specifications, domain,
                results, rankings.

        Returns:
            Dict mapping theorem class name -> verification result dict.
            Each value has at least 'passed' (bool), 'theorem' (str), and
            'error' (str | None).
        """
        outcomes: dict = {}
        for name, theorem_cls in cls._registry.items():
            try:
                if name == "EvaluationSoundnessTheorem":
                    result = theorem_cls.verify(
                        context.get("result", {}),
                        context.get("ground_truth", {}),
                    )
                elif name == "AblationIsolationTheorem":
                    result = theorem_cls.verify(
                        context.get("ablation_results", []),
                        context.get("ground_truth"),
                    )
                elif name == "CalibrationConsistencyTheorem":
                    result = theorem_cls.verify(
                        context.get("report", {}),
                        float(context.get("tolerance", 0.05)),
                    )
                elif name == "ClauseCompletenessTheorem":
                    result = theorem_cls.verify(
                        context.get("specifications", []),
                        context.get("domain", {}),
                    )
                elif name == "ScoreMonotonicityTheorem":
                    result = theorem_cls.verify(
                        context.get("results", []),
                        context.get("rankings", []),
                    )
                else:
                    # Generic fallback: call with context dict directly
                    result = theorem_cls.verify(context)
                result["error"] = None
            except Exception as exc:
                result = {"theorem": name, "passed": False, "error": str(exc)}
            outcomes[name] = result
        return outcomes

    @classmethod
    def to_latex_document(cls) -> str:
        """Generate a complete LaTeX document containing all theorem statements.

        The document uses the ``amsthm`` and ``amsmath`` packages and defines
        a theorem counter numbered within sections.

        Returns:
            Full LaTeX document string with documentclass, packages, preamble,
            and one theorem + proof environment per registered theorem.
        """
        preamble = [
            "\\documentclass{article}",
            "\\usepackage{amsthm}",
            "\\usepackage{amsmath}",
            "\\newtheorem{theorem}{Theorem}[section]",
            "\\title{Evaluation Design Formal Theorems}",
            "\\author{JuGeo evaluation\\_design package}",
            "\\date{\\today}",
            "\\begin{document}",
            "\\maketitle",
            "\\section{Evaluation Design Theorems (Chapter 63)}",
            "",
        ]
        body: list[str] = []
        for name in cls.list_all():
            theorem_cls = cls._registry[name]
            try:
                body.append(theorem_cls.latex_statement())
                body.append("")
            except Exception as exc:
                body.append(f"% Error rendering {name}: {exc}")
        footer = ["\\end{document}"]
        return "\n".join(preamble + body + footer)

    @classmethod
    def summary(cls) -> str:
        """Return a plain-text summary of all registered theorems.

        Returns:
            Multi-line string listing each theorem's chapter reference, name,
            and the first sentence of its statement.
        """
        lines: list[str] = ["Registered Evaluation Theorems", "=" * 40]
        for name in cls.list_all():
            theorem_cls = cls._registry[name]
            meta = getattr(theorem_cls, "METADATA", None)
            if meta is None:
                lines.append(f"{name}: (no metadata)")
                continue
            first_sentence = meta.statement.split(".")[0] + "."
            lines.append(f"[{meta.chapter_ref}] {meta.name}")
            lines.append(f"  {first_sentence}")
            lines.append("")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Auto-registration
# ---------------------------------------------------------------------------

EvaluationTheoremRegistry.register(EvaluationSoundnessTheorem)
EvaluationTheoremRegistry.register(AblationIsolationTheorem)
EvaluationTheoremRegistry.register(CalibrationConsistencyTheorem)
EvaluationTheoremRegistry.register(ClauseCompletenessTheorem)
EvaluationTheoremRegistry.register(ScoreMonotonicityTheorem)

# ---------------------------------------------------------------------------
# THEOREM_CATALOG
# ---------------------------------------------------------------------------

THEOREM_CATALOG: dict[str, dict] = {
    name: cls.METADATA.to_dict()
    for name, cls in EvaluationTheoremRegistry._registry.items()
}
"""Module-level catalog mapping theorem class name to its metadata dict.

This is populated at import time after all theorem classes are registered.
It provides a convenient read-only snapshot of all theorem metadata without
requiring callers to instantiate or import individual theorem classes.

Example usage::

    from jugeo.evaluation.evaluation_design.theorems import THEOREM_CATALOG

    for name, meta in THEOREM_CATALOG.items():
        print(name, meta["chapter_ref"], meta["tags"])
"""
