"""
Evaluation algorithms for the evaluation_design package.
Theory reference: theory2.tex Ch63. copilot: shared-core marker
"""
from __future__ import annotations

import math
import random
from typing import Any

__all__ = [
    "compute_ece",
    "compute_precision_recall",
    "compute_f1",
    "compute_auc_roc",
    "compute_brier_score",
    "compute_consistency_score",
    "compute_soundness_score",
    "EvaluationAlgorithms",
    # cross-reference evaluation functions
    "judgment_evaluation",
    "descent_quality_score",
    "evidence_completeness_score",
    "encoding_evaluation",
    "solver_performance_eval",
]


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _clamp(value: float, lo: float, hi: float) -> float:
    """Clamp *value* to the closed interval [lo, hi].

    Args:
        value: Numeric value to clamp.
        lo:    Lower bound (inclusive).
        hi:    Upper bound (inclusive).

    Returns:
        A float in ``[lo, hi]``.
    """
    return max(lo, min(hi, value))


def _safe_mean(values: list[float]) -> float:
    """Return the arithmetic mean of *values*, or 0.0 for an empty list.

    Args:
        values: List of floats to average.

    Returns:
        Mean of the list, or 0.0 if the list is empty.
    """
    if not values:
        return 0.0
    return sum(values) / len(values)


# ---------------------------------------------------------------------------
# Free functions
# ---------------------------------------------------------------------------


def compute_ece(predictions: list[float], labels: list[int], n_bins: int = 10) -> float:
    """Expected Calibration Error.

    Partitions predictions into *n_bins* equal-width bins over [0, 1] and
    computes the fraction-weighted absolute difference between mean confidence
    and empirical accuracy in each bin.

    Args:
        predictions: List of predicted probabilities in [0, 1].
        labels:      List of binary ground-truth labels (0 or 1).
        n_bins:      Number of equal-width bins; default 10.

    Returns:
        ECE as a float in [0, 1].  Returns 0.0 when *predictions* is empty
        or *n_bins* is less than 1.
    """
    if not predictions or n_bins < 1:
        return 0.0

    n = len(predictions)
    bin_width = 1.0 / n_bins

    bin_conf: list[float] = [0.0] * n_bins
    bin_acc: list[float] = [0.0] * n_bins
    bin_count: list[int] = [0] * n_bins

    for p, y in zip(predictions, labels):
        idx = min(int(p / bin_width), n_bins - 1)
        bin_conf[idx] += p
        bin_acc[idx] += y
        bin_count[idx] += 1

    ece = 0.0
    for b in range(n_bins):
        cnt = bin_count[b]
        if cnt == 0:
            continue
        avg_conf = bin_conf[b] / cnt
        avg_acc = bin_acc[b] / cnt
        ece += (cnt / n) * abs(avg_acc - avg_conf)

    return _clamp(ece, 0.0, 1.0)


def compute_precision_recall(
    predictions: list[float],
    labels: list[int],
    threshold: float = 0.5,
) -> tuple[float, float]:
    """Compute precision and recall at a fixed decision threshold.

    Args:
        predictions: List of predicted probabilities in [0, 1].
        labels:      List of binary ground-truth labels (0 or 1).
        threshold:   Decision boundary; predictions >= threshold are positive.

    Returns:
        A ``(precision, recall)`` tuple of floats in [0, 1].  Returns
        ``(0.0, 0.0)`` when there are no positive predictions or when
        *predictions* is empty.
    """
    if not predictions:
        return (0.0, 0.0)

    tp = fp = fn = 0
    for p, y in zip(predictions, labels):
        predicted_pos = p >= threshold
        if predicted_pos and y == 1:
            tp += 1
        elif predicted_pos and y == 0:
            fp += 1
        elif not predicted_pos and y == 1:
            fn += 1

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    return (precision, recall)


def compute_f1(precision: float, recall: float) -> float:
    """Compute the F1 score as the harmonic mean of precision and recall.

    Args:
        precision: Precision value in [0, 1].
        recall:    Recall value in [0, 1].

    Returns:
        F1 score in [0, 1].  Returns 0.0 when both precision and recall
        are zero to avoid division by zero.
    """
    denom = precision + recall
    if denom == 0.0:
        return 0.0
    return 2.0 * precision * recall / denom


def compute_auc_roc(predictions: list[float], labels: list[int]) -> float:
    """Compute the Area Under the ROC Curve via the trapezoidal rule.

    Sorts predictions in descending order and sweeps decision thresholds
    to build the ROC curve, then integrates with the trapezoid rule.

    Args:
        predictions: List of predicted probabilities in [0, 1].
        labels:      List of binary ground-truth labels (0 or 1).

    Returns:
        AUC-ROC as a float in [0, 1].  Returns 0.5 for degenerate inputs
        (empty lists, or all labels identical).
    """
    if not predictions or len(predictions) != len(labels):
        return 0.5

    n_pos = sum(labels)
    n_neg = len(labels) - n_pos
    if n_pos == 0 or n_neg == 0:
        return 0.5

    paired = sorted(zip(predictions, labels), key=lambda x: -x[0])

    tpr_points: list[float] = [0.0]
    fpr_points: list[float] = [0.0]
    tp = fp = 0

    for _, y in paired:
        if y == 1:
            tp += 1
        else:
            fp += 1
        tpr_points.append(tp / n_pos)
        fpr_points.append(fp / n_neg)

    auc = 0.0
    for i in range(1, len(fpr_points)):
        d_fpr = fpr_points[i] - fpr_points[i - 1]
        avg_tpr = (tpr_points[i] + tpr_points[i - 1]) / 2.0
        auc += d_fpr * avg_tpr

    return _clamp(auc, 0.0, 1.0)


def compute_brier_score(predictions: list[float], labels: list[int]) -> float:
    """Compute the Brier score: mean squared error of predicted probabilities.

    Defined as ``mean((p - y)^2)``.  A score of 0 means perfect predictions;
    a score of 1 is the worst possible.

    Args:
        predictions: List of predicted probabilities in [0, 1].
        labels:      List of binary ground-truth labels (0 or 1).

    Returns:
        Brier score as a float in [0, 1].  Returns 0.0 for empty input.
    """
    if not predictions:
        return 0.0

    total = sum((p - y) ** 2 for p, y in zip(predictions, labels))
    return _clamp(total / len(predictions), 0.0, 1.0)


def compute_consistency_score(outputs: list[dict]) -> float:
    """Measure self-consistency of repeated system outputs.

    Compares each output to the first element; counts the fraction whose
    ``"score"`` value is within an absolute tolerance of 0.1 of the first
    output's ``"score"``.

    Args:
        outputs: List of output dicts, each containing a ``"score"`` key.

    Returns:
        Fraction of outputs (including the first) that match the first
        output's score within 0.1.  Returns 1.0 for an empty list or a
        single-element list.
    """
    if len(outputs) <= 1:
        return 1.0

    reference_score = outputs[0].get("score", 0.0)
    matching = sum(
        1
        for out in outputs
        if abs(out.get("score", 0.0) - reference_score) <= 0.1
    )
    return matching / len(outputs)


def compute_soundness_score(claims: list[str], verified: list[str]) -> float:
    """Compute the fraction of claims that appear in the verified set.

    Args:
        claims:   List of claim strings asserted by the system.
        verified: List of claim strings that have been independently verified.

    Returns:
        ``len(set(claims) & set(verified)) / len(claims)`` as a float in
        [0, 1].  Returns 0.0 when *claims* is empty.
    """
    if not claims:
        return 0.0
    return len(set(claims) & set(verified)) / len(claims)


# ---------------------------------------------------------------------------
# EvaluationAlgorithms
# ---------------------------------------------------------------------------


class EvaluationAlgorithms:
    """Collection of evaluation algorithm methods bound to an EvaluationDesign.

    Wraps the free-function algorithms with per-design configuration and
    provides higher-level composite operations such as full evaluations,
    system comparisons, bootstrap confidence intervals, and cross-validation.

    Attributes:
        design:           The EvaluationDesign that governs this instance.
        n_bins:           Number of calibration bins for ECE computation.
        n_bootstrap:      Number of bootstrap resamples for CI estimation.
        confidence_level: Confidence level for bootstrap CIs (default 0.95).
        random_seed:      Seed for the internal random generator.
    """

    def __init__(
        self,
        design: Any,
        n_bins: int = 10,
        n_bootstrap: int = 500,
        confidence_level: float = 0.95,
        random_seed: int = 42,
    ) -> None:
        """Initialise an EvaluationAlgorithms instance.

        Args:
            design:           EvaluationDesign instance defining the plan.
            n_bins:           Number of bins for ECE/calibration; default 10.
            n_bootstrap:      Bootstrap resamples for CIs; default 500.
            confidence_level: Width of bootstrap CI as a fraction; default 0.95.
            random_seed:      Integer seed for reproducible sampling; default 42.
        """
        self.design = design
        self.n_bins = n_bins
        self.n_bootstrap = n_bootstrap
        self.confidence_level = confidence_level
        self.random_seed = random_seed
        self._rng = random.Random(random_seed)

    def clausewise_score(self, clause_results: list) -> list[float]:
        """Extract numeric scores from a list of ClauseResult objects.

        Args:
            clause_results: List of ClauseResult instances, each with a
                            ``score`` attribute.

        Returns:
            A list of float scores in the same order as *clause_results*.
            Returns an empty list when *clause_results* is empty.
        """
        return [cr.score for cr in clause_results]

    def ablation_run(self, system_fn: callable, components: list[str]) -> list[dict]:
        """Run an ablation experiment for each listed component.

        Calls *system_fn* once with no arguments to get the baseline score,
        then once per component with ``excluded_component`` set.

        Args:
            system_fn:  Callable accepting optional ``excluded_component``
                        keyword argument; returns a numeric score.
            components: List of component name strings to ablate.

        Returns:
            A list of dicts with keys
            ``{"component": str, "score": float, "delta": float}``.
        """
        baseline = float(system_fn())
        results: list[dict] = []
        for component in components:
            ablated = float(system_fn(excluded_component=component))
            results.append(
                {
                    "component": component,
                    "score": ablated,
                    "delta": ablated - baseline,
                }
            )
        return results

    def calibration_measure(
        self,
        predictions: list[float],
        labels: list[int],
    ) -> dict:
        """Compute calibration metrics for a set of predictions and labels.

        Args:
            predictions: List of predicted probabilities in [0, 1].
            labels:      Corresponding binary ground-truth labels (0 or 1).

        Returns:
            A dict with keys:
            ``{"ece": float, "brier_score": float, "n_bins": int}``.
        """
        return {
            "ece": compute_ece(predictions, labels, n_bins=self.n_bins),
            "brier_score": compute_brier_score(predictions, labels),
            "n_bins": self.n_bins,
        }

    def overall_evaluation_score(self, clause_scores: list[float]) -> float:
        """Compute the overall evaluation score as the mean of clause scores.

        Args:
            clause_scores: List of per-clause float scores in [0, 1].

        Returns:
            Mean clause score, clamped to [0, 1].  Returns 0.0 for an
            empty list.
        """
        if not clause_scores:
            return 0.0
        return _clamp(_safe_mean(clause_scores), 0.0, 1.0)

    def run_full_evaluation(
        self,
        system_fn: callable,
        predictions: list[float],
        labels: list[int],
    ) -> dict:
        """Run a complete evaluation combining clause scoring, calibration,
        and ablation.

        *system_fn* is called without arguments to produce clause-level
        results (a list of objects with a ``score`` attribute), and also for
        ablation over components from ``self.design.ablation_plan``.

        Args:
            system_fn:   Callable returning clause-result-like objects with
                         a ``score`` attribute.
            predictions: Predicted probabilities for calibration metrics.
            labels:      Ground-truth labels for calibration metrics.

        Returns:
            A dict with keys:
            ``{"overall_score": float, "ece": float, "brier_score": float,
            "clause_scores": list[float], "ablation_results": list[dict]}``.
        """
        raw_results = system_fn()
        clause_scores = self.clausewise_score(raw_results)
        overall = self.overall_evaluation_score(clause_scores)
        cal = self.calibration_measure(predictions, labels)

        components = list(getattr(self.design, "ablation_plan", {}).keys())
        ablation_results = self.ablation_run(system_fn, components) if components else []

        return {
            "overall_score": overall,
            "ece": cal["ece"],
            "brier_score": cal["brier_score"],
            "clause_scores": clause_scores,
            "ablation_results": ablation_results,
        }

    def compare_systems(
        self,
        system_fns: dict[str, callable],
        predictions: list[float],
        labels: list[int],
    ) -> dict:
        """Evaluate multiple systems and return their results keyed by name.

        Args:
            system_fns:  Dict mapping system name (str) to a callable
                         matching the contract of :meth:`run_full_evaluation`.
            predictions: Predicted probabilities for calibration comparison.
            labels:      Ground-truth labels for calibration comparison.

        Returns:
            A dict mapping each system name to its result dict.
        """
        return {
            name: self.run_full_evaluation(fn, predictions, labels)
            for name, fn in system_fns.items()
        }

    def compute_statistical_significance(
        self,
        scores_a: list[float],
        scores_b: list[float],
    ) -> dict:
        """Test whether two score distributions differ significantly.

        Uses a permutation test: the observed mean difference is compared
        against the null distribution from randomly reassigning scores.
        Returns ``p_value = 1.0`` when both lists are identical.

        Args:
            scores_a: List of float scores for system A.
            scores_b: List of float scores for system B.

        Returns:
            A dict with keys
            ``{"p_value": float, "statistic": float, "significant": bool}``.
            ``significant`` is ``True`` when ``p_value < 0.05``.
        """
        if not scores_a or not scores_b:
            return {"p_value": 1.0, "statistic": 0.0, "significant": False}

        observed_stat = abs(_safe_mean(scores_a) - _safe_mean(scores_b))

        if observed_stat == 0.0:
            return {"p_value": 1.0, "statistic": 0.0, "significant": False}

        combined = list(scores_a) + list(scores_b)
        n_a = len(scores_a)
        n_permutations = 1000
        extreme_count = 0

        rng = random.Random(self.random_seed)
        for _ in range(n_permutations):
            rng.shuffle(combined)
            perm_stat = abs(_safe_mean(combined[:n_a]) - _safe_mean(combined[n_a:]))
            if perm_stat >= observed_stat:
                extreme_count += 1

        p_value = extreme_count / n_permutations
        return {
            "p_value": p_value,
            "statistic": observed_stat,
            "significant": p_value < 0.05,
        }

    def bootstrap_confidence_interval(
        self,
        scores: list[float],
        n_bootstrap: int | None = None,
    ) -> tuple[float, float]:
        """Estimate a bootstrap confidence interval for the mean score.

        Draws *n_bootstrap* samples with replacement, computes the mean of
        each, then returns the percentile-based CI at ``self.confidence_level``.

        Args:
            scores:      List of float scores to resample.
            n_bootstrap: Number of bootstrap resamples.  Defaults to
                         ``self.n_bootstrap`` when ``None``.

        Returns:
            A ``(lower, upper)`` tuple where ``lower <= upper``.  Returns
            ``(0.0, 0.0)`` for an empty list.
        """
        if not scores:
            return (0.0, 0.0)

        n_boot = n_bootstrap if n_bootstrap is not None else self.n_bootstrap
        n = len(scores)
        rng = random.Random(self.random_seed)
        boot_means: list[float] = []

        for _ in range(n_boot):
            sample = [rng.choice(scores) for _ in range(n)]
            boot_means.append(_safe_mean(sample))

        boot_means.sort()
        alpha = 1.0 - self.confidence_level
        lo_idx = int(math.floor(alpha / 2.0 * n_boot))
        hi_idx = int(math.ceil((1.0 - alpha / 2.0) * n_boot)) - 1
        lo_idx = int(_clamp(lo_idx, 0, n_boot - 1))
        hi_idx = int(_clamp(hi_idx, 0, n_boot - 1))

        lower = boot_means[lo_idx]
        upper = boot_means[hi_idx]
        if lower > upper:
            lower, upper = upper, lower
        return (lower, upper)

    def cross_validate(
        self,
        system_fn: callable,
        data: list,
        labels: list[int],
        n_folds: int = 5,
    ) -> list[float]:
        """Estimate generalisation performance via k-fold cross-validation.

        Splits *data* and *labels* into *n_folds* equal-ish partitions,
        calls *system_fn* with each fold's held-out subset, and collects
        the returned score.

        Args:
            system_fn: Callable accepting ``data`` and ``labels`` keyword
                       arguments; returns a numeric score.
            data:      List of input samples.
            labels:    Corresponding integer labels (same length as *data*).
            n_folds:   Number of CV folds; default 5.

        Returns:
            A list of float scores of length exactly *n_folds*.
        """
        n = len(data)
        fold_size = max(1, n // n_folds)
        fold_scores: list[float] = []

        for fold_idx in range(n_folds):
            start = fold_idx * fold_size
            end = start + fold_size if fold_idx < n_folds - 1 else n
            fold_data = data[start:end]
            fold_labels = labels[start:end]
            score = float(system_fn(data=fold_data, labels=fold_labels))
            fold_scores.append(score)

        return fold_scores


# ---------------------------------------------------------------------------
# Cross-reference evaluation functions
# ---------------------------------------------------------------------------


def judgment_evaluation(judgments: list | None = None) -> dict:
    """Evaluate judgments using clausewise scoring from the judgments subsystem.

    Imports ``jugeo.judgments.judgment_terms`` at call time and scores each
    judgment's clauses against their propositions and evidence bundles.  The
    overall score is the mean of per-clause scores, clamped to [0, 1].

    Args:
        judgments: Optional list of ``Judgment`` objects from
            ``jugeo.judgments.judgment_terms``.  When *None* an empty list
            is assumed.

    Returns:
        A dict with keys ``"per_judgment"``, ``"clause_scores"``, and
        ``"overall_score"``.
    """
    try:
        from jugeo.judgments.judgment_terms import Judgment, JudgmentClause
    except ImportError:
        Judgment = None  # type: ignore[assignment,misc]
        JudgmentClause = None  # type: ignore[assignment,misc]

    judgments = judgments or []
    clause_scores: list[float] = []
    per_judgment: list[dict] = []

    for j in judgments:
        j_clauses = getattr(j, "clauses", []) or []
        scores_for_j: list[float] = []
        for clause in j_clauses:
            evidence = getattr(clause, "evidence", None)
            proposition = getattr(clause, "proposition", None)
            has_evidence = evidence is not None
            has_proposition = proposition is not None
            score = 1.0 if (has_evidence and has_proposition) else (0.5 if has_proposition else 0.0)
            scores_for_j.append(score)
        clause_scores.extend(scores_for_j)
        per_judgment.append({
            "judgment_id": getattr(j, "judgment_id", str(id(j))),
            "num_clauses": len(j_clauses),
            "clause_scores": scores_for_j,
            "mean_score": _safe_mean(scores_for_j),
        })

    return {
        "per_judgment": per_judgment,
        "clause_scores": clause_scores,
        "overall_score": _clamp(_safe_mean(clause_scores), 0.0, 1.0),
    }


def descent_quality_score(descent_result: Any = None) -> dict:
    """Evaluate descent quality using the geometry descent subsystem.

    Inspects a ``DescentResult`` from ``jugeo.geometry.descent`` and scores
    the quality of the gluing process based on overlap satisfaction, obstruction
    count, and the completeness of the resulting global section.

    Args:
        descent_result: A ``DescentResult`` object from
            ``jugeo.geometry.descent``.  If *None*, returns a zero-score stub.

    Returns:
        A dict with keys ``"overlap_score"``, ``"obstruction_penalty"``,
        ``"completeness"``, and ``"overall_score"``.
    """
    try:
        from jugeo.geometry.descent import DescentResult, OverlapStatus
    except ImportError:
        DescentResult = None  # type: ignore[assignment,misc]
        OverlapStatus = None  # type: ignore[assignment,misc]

    if descent_result is None:
        return {
            "overlap_score": 0.0,
            "obstruction_penalty": 0.0,
            "completeness": 0.0,
            "overall_score": 0.0,
        }

    overlaps = getattr(descent_result, "overlap_conditions", []) or []
    satisfied = sum(
        1 for o in overlaps
        if getattr(o, "status", None) is not None
        and getattr(o.status, "value", str(o.status)) == "SATISFIED"
    )
    overlap_score = satisfied / max(len(overlaps), 1)

    obstructions = getattr(descent_result, "obstructions", []) or []
    obstruction_penalty = _clamp(len(obstructions) * 0.1, 0.0, 1.0)

    global_section = getattr(descent_result, "global_section", None)
    completeness = 1.0 if global_section is not None else 0.0

    overall = _clamp((overlap_score + completeness - obstruction_penalty) / 2.0, 0.0, 1.0)

    return {
        "overlap_score": overlap_score,
        "obstruction_penalty": obstruction_penalty,
        "completeness": completeness,
        "overall_score": overall,
    }


def evidence_completeness_score(manifest: Any = None) -> dict:
    """Evaluate evidence coverage using the evidence manifests subsystem.

    Inspects a ``Manifest`` from ``jugeo.evidence.manifests`` and computes
    coverage ratios for the judgment store, obligation store, evidence archive,
    and certificate store.

    Args:
        manifest: A ``Manifest`` object from ``jugeo.evidence.manifests``.
            If *None*, returns a zero-score stub.

    Returns:
        A dict with keys ``"judgment_coverage"``, ``"obligation_ratio"``,
        ``"evidence_archive_size"``, ``"certificate_coverage"``, and
        ``"overall_score"``.
    """
    try:
        from jugeo.evidence.manifests import Manifest
    except ImportError:
        Manifest = None  # type: ignore[assignment,misc]

    if manifest is None:
        return {
            "judgment_coverage": 0.0,
            "obligation_ratio": 0.0,
            "evidence_archive_size": 0,
            "certificate_coverage": 0.0,
            "overall_score": 0.0,
        }

    j_store = getattr(manifest, "judgment_store", None)
    j_count = len(getattr(j_store, "entries", [])) if j_store else 0

    o_store = getattr(manifest, "obligation_store", None)
    o_count = len(getattr(o_store, "entries", [])) if o_store else 0

    archive = getattr(manifest, "evidence_archive", None)
    a_count = len(getattr(archive, "entries", [])) if archive else 0

    cert_store = getattr(manifest, "certificate_store", None)
    c_count = len(getattr(cert_store, "entries", [])) if cert_store else 0

    judgment_coverage = 1.0 if j_count > 0 else 0.0
    obligation_ratio = _clamp(1.0 - (o_count / max(j_count, 1)), 0.0, 1.0)
    certificate_coverage = _clamp(c_count / max(j_count, 1), 0.0, 1.0)

    scores = [judgment_coverage, obligation_ratio, certificate_coverage]
    if a_count > 0:
        scores.append(1.0)

    return {
        "judgment_coverage": judgment_coverage,
        "obligation_ratio": obligation_ratio,
        "evidence_archive_size": a_count,
        "certificate_coverage": certificate_coverage,
        "overall_score": _clamp(_safe_mean(scores), 0.0, 1.0),
    }


def encoding_evaluation(judgment: Any = None) -> dict:
    """Evaluate encoding quality from the encodings subsystem.

    Calls ``jugeo.encodings.encode_judgment`` on the supplied judgment and
    checks whether the encoded representation is non-empty and structurally
    sound (i.e. contains expected keys).

    Args:
        judgment: A judgment object accepted by
            ``jugeo.encodings.encode_judgment``.  If *None*, returns a
            zero-score stub.

    Returns:
        A dict with keys ``"encoded"``, ``"has_required_keys"``,
        ``"key_count"``, and ``"quality_score"``.
    """
    try:
        from jugeo.encodings import encode_judgment
    except ImportError:
        encode_judgment = None  # type: ignore[assignment]

    if judgment is None or encode_judgment is None:
        return {
            "encoded": None,
            "has_required_keys": False,
            "key_count": 0,
            "quality_score": 0.0,
        }

    try:
        encoded = encode_judgment(judgment)
    except Exception:
        encoded = {}

    key_count = len(encoded) if isinstance(encoded, dict) else 0
    has_required = key_count > 0
    quality = _clamp(min(key_count / 5.0, 1.0), 0.0, 1.0) if has_required else 0.0

    return {
        "encoded": encoded,
        "has_required_keys": has_required,
        "key_count": key_count,
        "quality_score": quality,
    }


def solver_performance_eval(
    formulas: list | None = None,
    timeout: float = 5.0,
) -> dict:
    """Evaluate solver performance from the solver subsystem.

    Creates a ``Z3Session`` from ``jugeo.solver.z3_session`` and benchmarks
    solve time and outcome for each supplied formula.

    Args:
        formulas: Optional list of ``Z3Formula`` objects (or plain dicts with
            a ``"formula"`` key) to submit.  If *None* or empty, returns a
            zero-score stub.
        timeout: Maximum seconds per formula; default 5.0.

    Returns:
        A dict with keys ``"results"``, ``"sat_rate"``, ``"mean_time"``, and
        ``"performance_score"``.
    """
    try:
        from jugeo.solver.z3_session import Z3Session, SolveOutcome
    except ImportError:
        Z3Session = None  # type: ignore[assignment,misc]
        SolveOutcome = None  # type: ignore[assignment,misc]

    formulas = formulas or []
    if not formulas or Z3Session is None:
        return {
            "results": [],
            "sat_rate": 0.0,
            "mean_time": 0.0,
            "performance_score": 0.0,
        }

    import time as _time

    results: list[dict] = []
    sat_count = 0
    total_time = 0.0

    try:
        session = Z3Session()
    except Exception:
        return {
            "results": [],
            "sat_rate": 0.0,
            "mean_time": 0.0,
            "performance_score": 0.0,
        }

    for formula in formulas:
        t0 = _time.monotonic()
        try:
            result = session.solve(formula) if hasattr(session, "solve") else None
            elapsed = _time.monotonic() - t0
            outcome = getattr(result, "outcome", None)
            outcome_str = getattr(outcome, "value", str(outcome)) if outcome else "UNKNOWN"
            is_sat = outcome_str in ("SAT", "UNSAT")
        except Exception:
            elapsed = _time.monotonic() - t0
            outcome_str = "ERROR"
            is_sat = False

        if is_sat:
            sat_count += 1
        total_time += elapsed
        results.append({"outcome": outcome_str, "time": elapsed})

    n = len(formulas)
    sat_rate = sat_count / n
    mean_time = total_time / n
    speed_score = _clamp(1.0 - (mean_time / max(timeout, 0.01)), 0.0, 1.0)
    performance_score = _clamp((sat_rate + speed_score) / 2.0, 0.0, 1.0)

    return {
        "results": results,
        "sat_rate": sat_rate,
        "mean_time": mean_time,
        "performance_score": performance_score,
    }
