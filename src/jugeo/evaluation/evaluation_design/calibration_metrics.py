"""Calibration metrics for the evaluation_design package.

Theory reference: theory2.tex Ch63.
copilot: shared-core marker

This module implements calibration measurement and recalibration for the JuGeo
evaluation pipeline.  A well-calibrated system should output a predicted
probability of *p* when the true long-run frequency of positive outcomes among
those predictions is also *p*.

The three principal questions addressed here are:

1. **How well calibrated is the current system?**  Answered by
   ``CalibrationMeasurer`` and the free function ``measure_calibration()``.

2. **Can the calibration be improved post-hoc?**  Answered by
   ``CalibrationRecalibrator`` and the free function ``recalibrate()``.

3. **How should calibration data be visualised?**  Answered by
   ``ReliabilityDiagramBuilder``.

Key metrics implemented
-----------------------
* **ECE** – Expected Calibration Error:
  ``ECE = Σ_b (n_b / n) * |acc(b) - conf(b)|``
* **MCE** – Maximum Calibration Error:
  ``MCE = max_b |acc(b) - conf(b)|``
* **Brier Score**: ``(1/n) * Σ (p_i - y_i)^2``
* **Log Loss**: ``-(1/n) * Σ [y_i * ln(p_i) + (1-y_i) * ln(1-p_i)]``
"""

from __future__ import annotations

import json
import math
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .models import CalibrationMethod, CalibrationReport

# ---------------------------------------------------------------------------
__all__ = [
    "CalibrationMeasurer",
    "CalibrationRecalibrator",
    "ReliabilityDiagramBuilder",
    "CalibrationMetricsRunner",
    "measure_calibration",
    "recalibrate",
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


def _safe_log(x: float) -> float:
    """Return the natural logarithm of *x*, clamped away from 0.

    Prevents ``-inf`` when computing log loss for predictions very close
    to 0 or 1 by clamping *x* to [1e-15, 1 - 1e-15] first.

    Args:
        x: Input value.

    Returns:
        float: ``math.log(clamp(x, 1e-15, 1 - 1e-15))``.
    """
    return math.log(_clamp(x, 1e-15, 1.0 - 1e-15))


def _linspace(lo: float, hi: float, n: int) -> list[float]:
    """Return *n* evenly spaced values in [lo, hi].

    Args:
        lo: Start of interval.
        hi: End of interval.
        n:  Number of points (>= 2).

    Returns:
        list[float]: Evenly spaced float values.
    """
    if n < 2:
        return [lo]
    step = (hi - lo) / (n - 1)
    return [lo + i * step for i in range(n)]


def _sigmoid(x: float) -> float:
    """Compute the sigmoid (logistic) function of *x*.

    Uses a numerically stable implementation that avoids overflow for large
    negative *x*.

    Args:
        x: Input value.

    Returns:
        float: ``1 / (1 + exp(-x))`` clamped to (0, 1).
    """
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    else:
        z = math.exp(x)
        return z / (1.0 + z)


def _isotonic_pool_adjacent_violators(values: list[float]) -> list[float]:
    """Run the Pool Adjacent Violators (PAV) algorithm for isotonic regression.

    This enforces a non-decreasing ordering of *values* by merging adjacent
    blocks that violate monotonicity.

    Args:
        values: Input sequence of floats to isotonically regress.

    Returns:
        list[float]: Non-decreasing list of floats of the same length as
        *values*.
    """
    # Each block is (mean, size).
    blocks: list[list[float | int]] = [[v, 1] for v in values]
    # Merge backward until monotone.
    i = 0
    while i < len(blocks) - 1:
        if blocks[i][0] > blocks[i + 1][0]:
            # Merge blocks i and i+1.
            total = blocks[i][0] * blocks[i][1] + blocks[i + 1][0] * blocks[i + 1][1]
            size = blocks[i][1] + blocks[i + 1][1]
            merged = [total / size, size]
            blocks[i : i + 2] = [merged]
            # Step back to recheck the previous block.
            if i > 0:
                i -= 1
        else:
            i += 1

    # Expand blocks back into an array.
    result: list[float] = []
    for mean, size in blocks:
        result.extend([float(mean)] * int(size))
    return result


# ---------------------------------------------------------------------------
# CalibrationMeasurer
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CalibrationMeasurer:
    """Measure the calibration quality of probabilistic predictions.

    ``CalibrationMeasurer`` computes standard calibration metrics from a list
    of predicted probabilities and corresponding binary labels.  The primary
    output is a ``CalibrationReport``.

    Attributes:
        n_bins:        Number of equal-width bins used to partition predictions
                       into [0, 1].  Defaults to 10.
        adaptive_bins: If ``True``, use adaptive (equal-frequency) bins
                       instead of equal-width bins.  Not yet implemented;
                       reserved for future use.
        metadata:      Arbitrary JSON-serialisable key-value pairs.
    """

    n_bins: int = 10
    adaptive_bins: bool = False
    metadata: dict = field(default_factory=dict)

    # ---------------------------------------------------------------------------
    @classmethod
    def create(
        cls,
        n_bins: int = 10,
        adaptive_bins: bool = False,
        metadata: dict | None = None,
    ) -> CalibrationMeasurer:
        """Factory method: create a ``CalibrationMeasurer``.

        Args:
            n_bins:        Number of histogram bins.  Must be >= 2.
            adaptive_bins: Reserved for future adaptive-bin support.
            metadata:      Optional metadata dict.

        Returns:
            CalibrationMeasurer: A fully initialised measurer.
        """
        return cls(
            n_bins=max(2, n_bins),
            adaptive_bins=adaptive_bins,
            metadata=dict(metadata) if metadata else {},
        )

    # ---------------------------------------------------------------------------
    def measure(
        self, predictions: list[float], labels: list[int]
    ) -> CalibrationReport:
        """Compute all calibration metrics and return a ``CalibrationReport``.

        Args:
            predictions: List of predicted probabilities, each in [0, 1].
            labels:      Corresponding binary ground-truth labels (0 or 1).

        Returns:
            CalibrationReport: An immutable report capturing all metrics.

        Raises:
            ValueError: If ``len(predictions) != len(labels)`` or if the
                        lists are empty.
        """
        if len(predictions) != len(labels):
            raise ValueError(
                f"predictions and labels must have the same length; "
                f"got {len(predictions)} vs {len(labels)}"
            )
        if not predictions:
            raise ValueError("predictions and labels must not be empty")

        ece = self.compute_ece(predictions, labels)
        mce = self.compute_mce(predictions, labels)
        diagram = self.compute_reliability_diagram(predictions, labels)

        return CalibrationReport(
            report_id=_uid(),
            method=CalibrationMethod.PLATT_SCALING,  # "before" report uses no method.
            before_ece=ece,
            after_ece=ece,  # No recalibration applied; before == after.
            before_mce=mce,
            after_mce=mce,
            reliability_diagram_data=diagram,
            n_samples=len(predictions),
            metadata={
                "n_bins": self.n_bins,
                "measured_at": _utcnow(),
                "brier_score": self.compute_brier_score(predictions, labels),
                "log_loss": self.compute_log_loss(predictions, labels),
            },
        )

    # ---------------------------------------------------------------------------
    def compute_ece(
        self, predictions: list[float], labels: list[int]
    ) -> float:
        """Compute the Expected Calibration Error (ECE).

        ECE measures the average absolute difference between predicted
        confidence and actual accuracy, weighted by the fraction of samples
        in each bin:

        ``ECE = Σ_b (n_b / n) * |accuracy(b) - confidence(b)|``

        Args:
            predictions: List of predicted probabilities in [0, 1].
            labels:      Binary ground-truth labels (0 or 1).

        Returns:
            float: ECE in [0.0, 1.0].  Lower is better.
        """
        bins = self.bin_predictions(predictions, labels)
        n_total = len(predictions)
        if n_total == 0:
            return 0.0
        ece = 0.0
        for b in bins:
            n_b = b["count"]
            if n_b == 0:
                continue
            acc = b["accuracy"]
            conf = b["confidence"]
            ece += (n_b / n_total) * abs(acc - conf)
        return _clamp(ece, 0.0, 1.0)

    # ---------------------------------------------------------------------------
    def compute_mce(
        self, predictions: list[float], labels: list[int]
    ) -> float:
        """Compute the Maximum Calibration Error (MCE).

        MCE is the maximum over all bins of the absolute difference between
        predicted confidence and actual accuracy:

        ``MCE = max_b |accuracy(b) - confidence(b)|``

        Args:
            predictions: List of predicted probabilities in [0, 1].
            labels:      Binary ground-truth labels (0 or 1).

        Returns:
            float: MCE in [0.0, 1.0].  Lower is better.
        """
        bins = self.bin_predictions(predictions, labels)
        if not bins:
            return 0.0
        max_err = max(
            abs(b["accuracy"] - b["confidence"])
            for b in bins
            if b["count"] > 0
        )
        return _clamp(max_err, 0.0, 1.0)

    # ---------------------------------------------------------------------------
    def compute_reliability_diagram(
        self, predictions: list[float], labels: list[int]
    ) -> list[dict]:
        """Compute data for a reliability (calibration) diagram.

        A reliability diagram plots mean predicted confidence on the x-axis
        vs. observed accuracy on the y-axis, with one point per bin.

        Args:
            predictions: List of predicted probabilities in [0, 1].
            labels:      Binary ground-truth labels (0 or 1).

        Returns:
            list[dict]: One dict per bin with keys ``bin_lower``,
            ``bin_upper``, ``confidence``, ``accuracy``, ``count``.
        """
        return self.bin_predictions(predictions, labels)

    # ---------------------------------------------------------------------------
    def compute_brier_score(
        self, predictions: list[float], labels: list[int]
    ) -> float:
        """Compute the Brier Score.

        The Brier Score measures the mean squared error between predicted
        probabilities and binary outcomes:

        ``BS = (1/n) * Σ (p_i - y_i)^2``

        A perfect model scores 0.0; a model that always predicts 0.5 scores
        0.25.

        Args:
            predictions: List of predicted probabilities in [0, 1].
            labels:      Binary ground-truth labels (0 or 1).

        Returns:
            float: Brier Score in [0.0, 1.0].  Lower is better.
        """
        n = len(predictions)
        if n == 0:
            return 0.0
        total = sum(
            (p - y) ** 2 for p, y in zip(predictions, labels)
        )
        return _clamp(total / n, 0.0, 1.0)

    # ---------------------------------------------------------------------------
    def compute_log_loss(
        self, predictions: list[float], labels: list[int]
    ) -> float:
        """Compute the Log Loss (binary cross-entropy).

        Log Loss penalises confident wrong predictions more heavily than
        uncertain ones:

        ``LL = -(1/n) * Σ [y_i * ln(p_i) + (1-y_i) * ln(1-p_i)]``

        Args:
            predictions: List of predicted probabilities in [0, 1].
            labels:      Binary ground-truth labels (0 or 1).

        Returns:
            float: Non-negative log loss.  Lower is better.  A perfect
            model (with all predictions equal to the labels) achieves 0.0.
        """
        n = len(predictions)
        if n == 0:
            return 0.0
        total = 0.0
        for p, y in zip(predictions, labels):
            total += y * _safe_log(p) + (1 - y) * _safe_log(1.0 - p)
        return max(0.0, -total / n)

    # ---------------------------------------------------------------------------
    def bin_predictions(
        self, predictions: list[float], labels: list[int]
    ) -> list[dict]:
        """Partition predictions into equal-width bins and compute per-bin stats.

        Creates ``self.n_bins`` equal-width bins over [0, 1] and assigns each
        prediction to its bin.  For each bin, computes:

        * ``count``      – number of predictions in the bin.
        * ``confidence`` – mean predicted probability in the bin.
        * ``accuracy``   – fraction of positive labels in the bin.

        Args:
            predictions: List of predicted probabilities in [0, 1].
            labels:      Corresponding binary ground-truth labels.

        Returns:
            list[dict]: One dict per bin (including empty bins) with keys
            ``bin_lower``, ``bin_upper``, ``count``, ``confidence``,
            ``accuracy``.
        """
        n_bins = self.n_bins
        bin_width = 1.0 / n_bins
        # Initialise bins.
        bins: list[dict[str, Any]] = []
        for b in range(n_bins):
            bins.append(
                {
                    "bin_lower": b * bin_width,
                    "bin_upper": (b + 1) * bin_width,
                    "count": 0,
                    "confidence_sum": 0.0,
                    "positive_sum": 0,
                }
            )

        # Assign each prediction to a bin.
        for p, y in zip(predictions, labels):
            p_clamped = _clamp(p, 0.0, 1.0)
            # Bin index: floor(p * n_bins), clamped to [0, n_bins-1].
            b_idx = min(int(math.floor(p_clamped * n_bins)), n_bins - 1)
            bins[b_idx]["count"] += 1
            bins[b_idx]["confidence_sum"] += p_clamped
            bins[b_idx]["positive_sum"] += int(y)

        # Convert sums to means.
        result: list[dict] = []
        for b in bins:
            count = b["count"]
            if count > 0:
                confidence = b["confidence_sum"] / count
                accuracy = b["positive_sum"] / count
            else:
                # Use bin midpoint as the confidence for empty bins.
                confidence = (b["bin_lower"] + b["bin_upper"]) / 2.0
                accuracy = 0.0
            result.append(
                {
                    "bin_lower": b["bin_lower"],
                    "bin_upper": b["bin_upper"],
                    "count": count,
                    "confidence": confidence,
                    "accuracy": accuracy,
                }
            )
        return result


# ---------------------------------------------------------------------------
# CalibrationRecalibrator
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CalibrationRecalibrator:
    """Post-hoc recalibration of model predictions.

    ``CalibrationRecalibrator`` fits a calibration mapping from raw model
    scores to calibrated probabilities using one of four methods:

    * ``PLATT_SCALING``   – learns a sigmoid (logistic) mapping.
    * ``ISOTONIC``        – learns a non-decreasing step function (PAV).
    * ``TEMPERATURE``     – learns a single temperature parameter T such that
                           ``p_cal = sigmoid(logit(p) / T)``.
    * ``HISTOGRAM``       – bins predictions and replaces each bin's mean
                           confidence with the bin's empirical accuracy.

    Attributes:
        method:   ``CalibrationMethod`` enum member identifying the method.
        fitted:   ``True`` after ``fit()`` has been called successfully.
        metadata: Arbitrary JSON-serialisable key-value pairs.
    """

    method: CalibrationMethod = CalibrationMethod.PLATT_SCALING
    fitted: bool = False
    metadata: dict = field(default_factory=dict)

    # Internal parameters stored after fitting.  Not exposed as constructor
    # args because the slots=True dataclass requires init=False fields to be
    # declared with field(..., init=False).
    _params: dict = field(default_factory=dict, init=False)

    # ---------------------------------------------------------------------------
    @classmethod
    def create(
        cls,
        method: CalibrationMethod | str = CalibrationMethod.PLATT_SCALING,
        metadata: dict | None = None,
    ) -> CalibrationRecalibrator:
        """Factory method: create a ``CalibrationRecalibrator``.

        Args:
            method:   The recalibration method.  Accepts either a
                      ``CalibrationMethod`` enum member or its string value.
            metadata: Optional metadata dict.

        Returns:
            CalibrationRecalibrator: An unfitted recalibrator.
        """
        if isinstance(method, str):
            method = CalibrationMethod(method)
        return cls(
            method=method,
            fitted=False,
            metadata=dict(metadata) if metadata else {},
        )

    # ---------------------------------------------------------------------------
    def fit(
        self, predictions: list[float], labels: list[int]
    ) -> None:
        """Fit the recalibration model to *predictions* and *labels*.

        After calling ``fit()``, ``self.fitted`` is set to ``True`` and the
        learned parameters are stored in ``self._params``.

        Args:
            predictions: List of predicted probabilities in [0, 1].
            labels:      Corresponding binary ground-truth labels (0 or 1).

        Returns:
            None
        """
        if self.method == CalibrationMethod.PLATT_SCALING:
            self._fit_platt(predictions, labels)
        elif self.method == CalibrationMethod.ISOTONIC:
            self._fit_isotonic(predictions, labels)
        elif self.method == CalibrationMethod.TEMPERATURE:
            self._fit_temperature(predictions, labels)
        elif self.method == CalibrationMethod.HISTOGRAM:
            self._fit_histogram(predictions, labels)
        else:
            # Fallback: identity mapping.
            self._params = {"method": "identity"}
        self.fitted = True

    # ---------------------------------------------------------------------------
    def _fit_platt(
        self, predictions: list[float], labels: list[int]
    ) -> None:
        """Fit Platt scaling parameters A and B using gradient descent.

        Platt scaling learns A and B such that the calibrated probability is:
        ``p_cal = sigmoid(A * logit(p) + B)``

        Uses a simplified gradient descent over 200 iterations.

        Args:
            predictions: Raw predictions in (0, 1).
            labels:      Binary labels.

        Returns:
            None
        """
        # Convert to logits.
        def to_logit(p: float) -> float:
            p_safe = _clamp(p, 1e-7, 1.0 - 1e-7)
            return math.log(p_safe / (1.0 - p_safe))

        logits = [to_logit(p) for p in predictions]
        n = len(logits)
        # Initialise parameters.
        A = 1.0
        B = 0.0
        lr = 0.01  # Learning rate.
        for _ in range(200):
            grad_A = 0.0
            grad_B = 0.0
            for x, y in zip(logits, labels):
                p_hat = _sigmoid(A * x + B)
                err = p_hat - y
                grad_A += err * x
                grad_B += err
            grad_A /= n
            grad_B /= n
            A -= lr * grad_A
            B -= lr * grad_B
        self._params = {"A": A, "B": B, "method": "platt"}

    # ---------------------------------------------------------------------------
    def _fit_isotonic(
        self, predictions: list[float], labels: list[int]
    ) -> None:
        """Fit isotonic regression using the PAV algorithm.

        Stores the sorted (prediction, calibrated) pairs so that the
        transform can look up calibrated values by nearest-neighbour.

        Args:
            predictions: Raw predictions.
            labels:      Binary labels.

        Returns:
            None
        """
        paired = sorted(zip(predictions, labels), key=lambda x: x[0])
        sorted_preds = [p for p, _ in paired]
        sorted_labels = [float(y) for _, y in paired]
        calibrated = _isotonic_pool_adjacent_violators(sorted_labels)
        self._params = {
            "sorted_preds": sorted_preds,
            "calibrated": calibrated,
            "method": "isotonic",
        }

    # ---------------------------------------------------------------------------
    def _fit_temperature(
        self, predictions: list[float], labels: list[int]
    ) -> None:
        """Fit a single temperature parameter T via line search over log loss.

        Tries temperatures in [0.1, 10.0] at 200 steps and picks the one that
        minimises log loss.

        Args:
            predictions: Raw predictions.
            labels:      Binary labels.

        Returns:
            None
        """
        def to_logit(p: float) -> float:
            p_safe = _clamp(p, 1e-7, 1.0 - 1e-7)
            return math.log(p_safe / (1.0 - p_safe))

        logits = [to_logit(p) for p in predictions]
        best_T = 1.0
        best_loss = float("inf")
        for step in range(200):
            T = 0.1 + step * (10.0 - 0.1) / 199.0
            loss = 0.0
            for logit, y in zip(logits, labels):
                p_cal = _sigmoid(logit / T)
                loss += y * _safe_log(p_cal) + (1 - y) * _safe_log(1.0 - p_cal)
            loss = -loss / len(logits) if logits else 0.0
            if loss < best_loss:
                best_loss = loss
                best_T = T
        self._params = {"T": best_T, "method": "temperature"}

    # ---------------------------------------------------------------------------
    def _fit_histogram(
        self, predictions: list[float], labels: list[int]
    ) -> None:
        """Fit histogram binning calibration.

        Computes the empirical accuracy in each of 10 equal-width bins and
        stores them as the calibration mapping.

        Args:
            predictions: Raw predictions.
            labels:      Binary labels.

        Returns:
            None
        """
        n_bins = 10
        bin_width = 1.0 / n_bins
        bin_accuracy: list[float] = []
        for b in range(n_bins):
            lo = b * bin_width
            hi = (b + 1) * bin_width
            in_bin = [
                y for p, y in zip(predictions, labels) if lo <= p < hi
            ]
            # Edge case: include 1.0 in last bin.
            if b == n_bins - 1:
                in_bin = [
                    y for p, y in zip(predictions, labels) if lo <= p <= hi
                ]
            if in_bin:
                bin_accuracy.append(sum(in_bin) / len(in_bin))
            else:
                bin_accuracy.append((lo + hi) / 2.0)
        self._params = {
            "bin_accuracy": bin_accuracy,
            "n_bins": n_bins,
            "method": "histogram",
        }

    # ---------------------------------------------------------------------------
    def transform(self, predictions: list[float]) -> list[float]:
        """Apply the fitted calibration mapping to *predictions*.

        Args:
            predictions: List of raw predicted probabilities in [0, 1].

        Returns:
            list[float]: Calibrated probabilities, each in [0, 1].

        Raises:
            RuntimeError: If called before ``fit()``.
        """
        if not self.fitted:
            raise RuntimeError(
                "CalibrationRecalibrator must be fitted before calling transform()"
            )
        method = self._params.get("method", "identity")

        if method == "platt":
            A = self._params["A"]
            B = self._params["B"]

            def to_logit(p: float) -> float:
                p_s = _clamp(p, 1e-7, 1.0 - 1e-7)
                return math.log(p_s / (1.0 - p_s))

            return [_clamp(_sigmoid(A * to_logit(p) + B), 0.0, 1.0) for p in predictions]

        if method == "isotonic":
            sorted_preds = self._params["sorted_preds"]
            calibrated = self._params["calibrated"]
            result: list[float] = []
            for p in predictions:
                # Find nearest neighbour in sorted_preds.
                idx = min(
                    range(len(sorted_preds)),
                    key=lambda i: abs(sorted_preds[i] - p),
                )
                result.append(_clamp(calibrated[idx], 0.0, 1.0))
            return result

        if method == "temperature":
            T = self._params["T"]

            def to_logit(p: float) -> float:
                p_s = _clamp(p, 1e-7, 1.0 - 1e-7)
                return math.log(p_s / (1.0 - p_s))

            return [_clamp(_sigmoid(to_logit(p) / T), 0.0, 1.0) for p in predictions]

        if method == "histogram":
            bin_accuracy = self._params["bin_accuracy"]
            n_bins = self._params["n_bins"]
            bin_width = 1.0 / n_bins
            result2: list[float] = []
            for p in predictions:
                b_idx = min(
                    int(math.floor(_clamp(p, 0.0, 1.0) * n_bins)), n_bins - 1
                )
                result2.append(_clamp(bin_accuracy[b_idx], 0.0, 1.0))
            return result2

        # Identity (fallback).
        return [_clamp(p, 0.0, 1.0) for p in predictions]

    # ---------------------------------------------------------------------------
    def fit_transform(
        self, predictions: list[float], labels: list[int]
    ) -> list[float]:
        """Fit on *predictions* / *labels* and immediately return transformed scores.

        Args:
            predictions: Raw predicted probabilities in [0, 1].
            labels:      Binary ground-truth labels.

        Returns:
            list[float]: Calibrated probabilities.
        """
        self.fit(predictions, labels)
        return self.transform(predictions)

    # ---------------------------------------------------------------------------
    def platt_scaling(
        self, predictions: list[float], labels: list[int]
    ) -> list[float]:
        """Convenience method: fit-transform using Platt scaling.

        Temporarily overrides ``self.method``, fits, transforms, and restores
        the original method.

        Args:
            predictions: Raw predicted probabilities.
            labels:      Binary ground-truth labels.

        Returns:
            list[float]: Platt-scaled calibrated probabilities.
        """
        original_method = self.method
        self.method = CalibrationMethod.PLATT_SCALING
        self.fitted = False
        result = self.fit_transform(predictions, labels)
        self.method = original_method
        return result

    # ---------------------------------------------------------------------------
    def isotonic_regression(
        self, predictions: list[float], labels: list[int]
    ) -> list[float]:
        """Convenience method: fit-transform using isotonic regression.

        Args:
            predictions: Raw predicted probabilities.
            labels:      Binary ground-truth labels.

        Returns:
            list[float]: Isotonically calibrated probabilities.
        """
        original_method = self.method
        self.method = CalibrationMethod.ISOTONIC
        self.fitted = False
        result = self.fit_transform(predictions, labels)
        self.method = original_method
        return result

    # ---------------------------------------------------------------------------
    def temperature_scaling(
        self, predictions: list[float], labels: list[int]
    ) -> list[float]:
        """Convenience method: fit-transform using temperature scaling.

        Args:
            predictions: Raw predicted probabilities.
            labels:      Binary ground-truth labels.

        Returns:
            list[float]: Temperature-scaled calibrated probabilities.
        """
        original_method = self.method
        self.method = CalibrationMethod.TEMPERATURE
        self.fitted = False
        result = self.fit_transform(predictions, labels)
        self.method = original_method
        return result

    # ---------------------------------------------------------------------------
    def histogram_binning(
        self, predictions: list[float], labels: list[int]
    ) -> list[float]:
        """Convenience method: fit-transform using histogram binning.

        Args:
            predictions: Raw predicted probabilities.
            labels:      Binary ground-truth labels.

        Returns:
            list[float]: Histogram-binned calibrated probabilities.
        """
        original_method = self.method
        self.method = CalibrationMethod.HISTOGRAM
        self.fitted = False
        result = self.fit_transform(predictions, labels)
        self.method = original_method
        return result

    # ---------------------------------------------------------------------------
    def save_params(self, path: str) -> None:
        """Persist fitted parameters to a JSON file.

        Args:
            path: Destination file path.  Parent directories must exist.

        Returns:
            None

        Raises:
            RuntimeError: If called before ``fit()``.
        """
        if not self.fitted:
            raise RuntimeError("Cannot save params before fitting.")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(
                {"method": self.method.value, "params": self._params},
                fh,
                indent=2,
            )

    # ---------------------------------------------------------------------------
    def load_params(self, path: str) -> None:
        """Load fitted parameters from a JSON file.

        Args:
            path: Path to JSON file previously created by ``save_params()``.

        Returns:
            None
        """
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        self.method = CalibrationMethod(data["method"])
        self._params = data["params"]
        self.fitted = True


# ---------------------------------------------------------------------------
# ReliabilityDiagramBuilder
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ReliabilityDiagramBuilder:
    """Build reliability diagram data for visualisation.

    A reliability diagram shows how well a model's predicted probabilities
    match empirical frequencies.  This class computes the diagram data and
    provides helper methods for converting to plot-friendly formats.

    Attributes:
        n_bins:   Number of equal-width histogram bins.
        metadata: Arbitrary JSON-serialisable key-value pairs.
    """

    n_bins: int = 10
    metadata: dict = field(default_factory=dict)

    # ---------------------------------------------------------------------------
    @classmethod
    def create(
        cls,
        n_bins: int = 10,
        metadata: dict | None = None,
    ) -> ReliabilityDiagramBuilder:
        """Factory method: create a ``ReliabilityDiagramBuilder``.

        Args:
            n_bins:   Number of histogram bins (>= 2).
            metadata: Optional metadata dict.

        Returns:
            ReliabilityDiagramBuilder: A fully initialised builder.
        """
        return cls(
            n_bins=max(2, n_bins),
            metadata=dict(metadata) if metadata else {},
        )

    # ---------------------------------------------------------------------------
    def build(
        self, predictions: list[float], labels: list[int]
    ) -> list[dict]:
        """Build reliability diagram data from *predictions* and *labels*.

        Delegates to ``compute_bin_stats`` after generating equal-width bin
        edges from 0.0 to 1.0.

        Args:
            predictions: List of predicted probabilities in [0, 1].
            labels:      Binary ground-truth labels (0 or 1).

        Returns:
            list[dict]: One dict per bin with keys ``bin_lower``,
            ``bin_upper``, ``confidence``, ``accuracy``, ``count``.
        """
        bin_edges = _linspace(0.0, 1.0, self.n_bins + 1)
        return self.compute_bin_stats(predictions, labels, bin_edges)

    # ---------------------------------------------------------------------------
    def compute_bin_stats(
        self,
        predictions: list[float],
        labels: list[int],
        bin_edges: list[float],
    ) -> list[dict]:
        """Compute per-bin statistics given explicit *bin_edges*.

        Args:
            predictions: List of predicted probabilities.
            labels:      Binary ground-truth labels.
            bin_edges:   Monotonically increasing list of n+1 edge values
                         defining n bins.  The first edge is the lower bound
                         of the first bin; the last is the upper bound of the
                         last bin.

        Returns:
            list[dict]: One dict per bin with keys ``bin_lower``,
            ``bin_upper``, ``confidence``, ``accuracy``, ``count``.
        """
        n_bins = len(bin_edges) - 1
        if n_bins <= 0:
            return []

        bin_data: list[dict[str, Any]] = [
            {
                "bin_lower": bin_edges[i],
                "bin_upper": bin_edges[i + 1],
                "confidence_sum": 0.0,
                "positive_sum": 0,
                "count": 0,
            }
            for i in range(n_bins)
        ]

        for p, y in zip(predictions, labels):
            p_c = _clamp(p, 0.0, 1.0)
            # Binary search for the appropriate bin.
            lo, hi = 0, n_bins - 1
            while lo < hi:
                mid = (lo + hi) // 2
                if p_c < bin_edges[mid + 1]:
                    hi = mid
                else:
                    lo = mid + 1
            b = lo
            bin_data[b]["count"] += 1
            bin_data[b]["confidence_sum"] += p_c
            bin_data[b]["positive_sum"] += int(y)

        result: list[dict] = []
        for b in bin_data:
            count = b["count"]
            if count > 0:
                confidence = b["confidence_sum"] / count
                accuracy = b["positive_sum"] / count
            else:
                confidence = (b["bin_lower"] + b["bin_upper"]) / 2.0
                accuracy = 0.0
            result.append(
                {
                    "bin_lower": b["bin_lower"],
                    "bin_upper": b["bin_upper"],
                    "confidence": confidence,
                    "accuracy": accuracy,
                    "count": count,
                }
            )
        return result

    # ---------------------------------------------------------------------------
    def to_plot_data(self, diagram_data: list[dict]) -> dict:
        """Convert reliability diagram data to a simple x/y/counts format.

        Args:
            diagram_data: List of bin dicts as returned by ``build()``.

        Returns:
            dict: Dictionary with keys ``"x"`` (mean confidences), ``"y"``
            (empirical accuracies), and ``"counts"`` (bin sizes).
        """
        return {
            "x": [b["confidence"] for b in diagram_data],
            "y": [b["accuracy"] for b in diagram_data],
            "counts": [b["count"] for b in diagram_data],
            "bin_lower": [b["bin_lower"] for b in diagram_data],
            "bin_upper": [b["bin_upper"] for b in diagram_data],
        }

    # ---------------------------------------------------------------------------
    def compute_sharpness(self, predictions: list[float]) -> float:
        """Compute the sharpness of *predictions*.

        Sharpness is the variance of the predicted probabilities.  A perfectly
        sharp forecaster always predicts exactly 0 or 1.

        Args:
            predictions: List of predicted probabilities in [0, 1].

        Returns:
            float: Sample variance of *predictions*, or 0.0 if fewer than 2
            values are provided.
        """
        n = len(predictions)
        if n < 2:
            return 0.0
        mu = sum(predictions) / n
        return sum((p - mu) ** 2 for p in predictions) / (n - 1)

    # ---------------------------------------------------------------------------
    def compute_resolution(
        self, predictions: list[float], labels: list[int]
    ) -> float:
        """Compute the resolution of the forecast.

        Resolution measures how much the per-bin accuracies differ from the
        base rate (overall prevalence).  Higher resolution is better:

        ``resolution = (1/n) * Σ_b n_b * (accuracy(b) - base_rate)^2``

        Args:
            predictions: List of predicted probabilities in [0, 1].
            labels:      Binary ground-truth labels.

        Returns:
            float: Resolution score >= 0.  Higher is better.
        """
        n = len(labels)
        if n == 0:
            return 0.0
        base_rate = sum(labels) / n
        bins = self.build(predictions, labels)
        resolution = 0.0
        for b in bins:
            n_b = b["count"]
            if n_b == 0:
                continue
            resolution += n_b * (b["accuracy"] - base_rate) ** 2
        return resolution / n


# ---------------------------------------------------------------------------
# CalibrationMetricsRunner
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CalibrationMetricsRunner:
    """End-to-end orchestrator for calibration measurement and recalibration.

    ``CalibrationMetricsRunner`` wires together ``CalibrationMeasurer``,
    ``CalibrationRecalibrator``, and ``ReliabilityDiagramBuilder`` to provide
    a single-call interface for calibration analysis.

    Attributes:
        measurer:        ``CalibrationMeasurer`` for computing raw metrics.
        recalibrator:    ``CalibrationRecalibrator`` for post-hoc correction.
        diagram_builder: ``ReliabilityDiagramBuilder`` for diagram data.
        metadata:        Arbitrary JSON-serialisable key-value pairs.
    """

    measurer: CalibrationMeasurer
    recalibrator: CalibrationRecalibrator
    diagram_builder: ReliabilityDiagramBuilder
    metadata: dict = field(default_factory=dict)

    # ---------------------------------------------------------------------------
    @classmethod
    def create(
        cls,
        n_bins: int = 10,
        method: CalibrationMethod = CalibrationMethod.PLATT_SCALING,
        metadata: dict | None = None,
    ) -> CalibrationMetricsRunner:
        """Factory method: create a fully wired ``CalibrationMetricsRunner``.

        Args:
            n_bins:   Number of histogram bins for the measurer and builder.
            method:   Recalibration method.
            metadata: Optional metadata dict.

        Returns:
            CalibrationMetricsRunner: A ready-to-use runner.
        """
        return cls(
            measurer=CalibrationMeasurer.create(n_bins=n_bins),
            recalibrator=CalibrationRecalibrator.create(method=method),
            diagram_builder=ReliabilityDiagramBuilder.create(n_bins=n_bins),
            metadata=dict(metadata) if metadata else {},
        )

    # ---------------------------------------------------------------------------
    def run(
        self, predictions: list[float], labels: list[int]
    ) -> CalibrationReport:
        """Measure calibration of *predictions* and return a ``CalibrationReport``.

        Args:
            predictions: List of predicted probabilities in [0, 1].
            labels:      Binary ground-truth labels (0 or 1).

        Returns:
            CalibrationReport: Immutable report of all calibration metrics.
        """
        return self.measurer.measure(predictions, labels)

    # ---------------------------------------------------------------------------
    def run_with_recalibration(
        self, predictions: list[float], labels: list[int]
    ) -> tuple[CalibrationReport, list[float]]:
        """Measure calibration, recalibrate, re-measure, and return both.

        Runs the full calibration-then-recalibration pipeline.  Returns the
        combined ``CalibrationReport`` (with before/after metrics) and the
        recalibrated prediction list.

        Args:
            predictions: Raw predicted probabilities.
            labels:      Binary ground-truth labels.

        Returns:
            tuple[CalibrationReport, list[float]]: A pair of (report,
            recalibrated_predictions).  The report captures before and after
            ECE/MCE.
        """
        before_ece = self.measurer.compute_ece(predictions, labels)
        before_mce = self.measurer.compute_mce(predictions, labels)

        calibrated = self.recalibrator.fit_transform(predictions, labels)

        after_ece = self.measurer.compute_ece(calibrated, labels)
        after_mce = self.measurer.compute_mce(calibrated, labels)
        diagram = self.measurer.compute_reliability_diagram(calibrated, labels)

        report = CalibrationReport(
            report_id=_uid(),
            method=self.recalibrator.method,
            before_ece=before_ece,
            after_ece=after_ece,
            before_mce=before_mce,
            after_mce=after_mce,
            reliability_diagram_data=diagram,
            n_samples=len(predictions),
            metadata={
                "n_bins": self.measurer.n_bins,
                "run_at": _utcnow(),
                "brier_before": self.measurer.compute_brier_score(
                    predictions, labels
                ),
                "brier_after": self.measurer.compute_brier_score(
                    calibrated, labels
                ),
            },
        )
        return report, calibrated

    # ---------------------------------------------------------------------------
    def compare_methods(
        self, predictions: list[float], labels: list[int]
    ) -> dict[str, CalibrationReport]:
        """Compare all supported recalibration methods on the same data.

        Fits each method independently and measures calibration after applying
        it.  Returns a dictionary mapping method name to ``CalibrationReport``.

        Args:
            predictions: Raw predicted probabilities.
            labels:      Binary ground-truth labels.

        Returns:
            dict[str, CalibrationReport]: Method name → report.
        """
        results: dict[str, CalibrationReport] = {}
        before_ece = self.measurer.compute_ece(predictions, labels)
        before_mce = self.measurer.compute_mce(predictions, labels)

        for method in CalibrationMethod:
            recal = CalibrationRecalibrator.create(method=method)
            try:
                calibrated = recal.fit_transform(predictions, labels)
                after_ece = self.measurer.compute_ece(calibrated, labels)
                after_mce = self.measurer.compute_mce(calibrated, labels)
                diagram = self.measurer.compute_reliability_diagram(
                    calibrated, labels
                )
                report = CalibrationReport(
                    report_id=_uid(),
                    method=method,
                    before_ece=before_ece,
                    after_ece=after_ece,
                    before_mce=before_mce,
                    after_mce=after_mce,
                    reliability_diagram_data=diagram,
                    n_samples=len(predictions),
                    metadata={
                        "n_bins": self.measurer.n_bins,
                        "compared_at": _utcnow(),
                    },
                )
            except Exception as exc:
                # If a method fails, record a degenerate report.
                report = CalibrationReport(
                    report_id=_uid(),
                    method=method,
                    before_ece=before_ece,
                    after_ece=before_ece,
                    before_mce=before_mce,
                    after_mce=before_mce,
                    reliability_diagram_data=[],
                    n_samples=len(predictions),
                    metadata={"error": str(exc)},
                )
            results[method.value] = report
        return results

    # ---------------------------------------------------------------------------
    def generate_report(self, report: CalibrationReport) -> dict:
        """Convert *report* into a rich JSON-serialisable dictionary.

        Args:
            report: A ``CalibrationReport`` to serialise.

        Returns:
            dict: Dictionary including all metric values, improvement ratios,
            calibration status, and diagram data.
        """
        try:
            is_well = report.is_well_calibrated()
            improvement = report.improvement_ratio()
            summary = report.summary_line()
        except AttributeError:
            # Fallback for minimal CalibrationReport implementations.
            is_well = report.after_ece < 0.1
            improvement = (
                (report.before_ece - report.after_ece) / report.before_ece
                if report.before_ece > 0
                else 0.0
            )
            summary = (
                f"ECE: {report.before_ece:.4f} → {report.after_ece:.4f}"
            )

        return {
            "report_id": report.report_id,
            "method": report.method.value,
            "n_samples": report.n_samples,
            "before_ece": report.before_ece,
            "after_ece": report.after_ece,
            "before_mce": report.before_mce,
            "after_mce": report.after_mce,
            "ece_improvement_ratio": improvement,
            "is_well_calibrated": is_well,
            "summary": summary,
            "reliability_diagram": report.reliability_diagram_data,
            "metadata": report.metadata,
            "generated_at": _utcnow(),
        }


# ---------------------------------------------------------------------------
# Free functions
# ---------------------------------------------------------------------------


def measure_calibration(
    predictions: list[float],
    labels: list[int],
    n_bins: int = 10,
) -> CalibrationReport:
    """Measure calibration quality and return a ``CalibrationReport``.

    This is the primary functional entry point for calibration measurement.
    It constructs a ``CalibrationMeasurer`` and returns a complete report.

    Args:
        predictions: List of predicted probabilities in [0, 1].
        labels:      Corresponding binary ground-truth labels (0 or 1).
        n_bins:      Number of equal-width bins for ECE/MCE computation.
                     Defaults to 10.

    Returns:
        CalibrationReport: Immutable calibration report capturing ECE, MCE,
        Brier score, log loss, and reliability diagram data.
    """
    measurer = CalibrationMeasurer.create(n_bins=n_bins)
    return measurer.measure(predictions, labels)


def recalibrate(
    predictions: list[float],
    labels: list[int],
    method: CalibrationMethod | str = "platt_scaling",
) -> tuple[list[float], CalibrationReport]:
    """Recalibrate *predictions* and return recalibrated scores + report.

    This is the primary functional entry point for post-hoc recalibration.
    It fits the specified method on (*predictions*, *labels*), transforms the
    predictions, and returns a ``CalibrationReport`` comparing before/after.

    Args:
        predictions: Raw predicted probabilities in [0, 1].
        labels:      Binary ground-truth labels (0 or 1).
        method:      Recalibration method.  Accepts a ``CalibrationMethod``
                     enum member or a string value such as
                     ``"platt_scaling"``, ``"isotonic"``,
                     ``"temperature"``, or ``"histogram"``.

    Returns:
        tuple[list[float], CalibrationReport]: A pair of
        (recalibrated_predictions, report).  The report captures both
        before-calibration and after-calibration ECE/MCE values.
    """
    runner = CalibrationMetricsRunner.create(method=method if isinstance(method, CalibrationMethod) else CalibrationMethod(method))
    report, calibrated = runner.run_with_recalibration(predictions, labels)
    return calibrated, report
