"""Chapter 43, Section 3 — Convergence Verification.

This module implements the convergence verification subsystem for the JuGeo
generation pipeline.  After an incremental replay has finished one or more
rounds of patching, the question arises: has the geometric construction
stabilised, or are sections still drifting between iterations?

The primary entry point is :class:`ConvergenceVerifier`, which accepts the
*gluing history* — a chronological list of gluing state dicts, one per replay
round — and produces a :class:`ConvergenceRecord` that summarises:

  * The per-round scalar metric values (computed from patch-section hashes).
  * Whether the sequence has converged to a fixed point.
  * The rate at which convergence is occurring.
  * An optional :class:`ConvergenceCertificate` attesting to successful
    convergence.

Additionally the module provides:

  * :class:`ConvergenceMetric` — a running tracker for a single convergence
    quantity, with rate-of-change and threshold logic.
  * :class:`FixedPointChecker` — computes a normalised distance between two
    consecutive gluing dicts and decides whether convergence has occurred.
  * :class:`ConvergenceStatus` — an enum classifying the convergence state.
  * :class:`ConvergenceReport` — a summary dataclass bundling status, history,
    and certificate.
  * Several helper functions for oscillation detection, exponential rate
    estimation, and report formatting.

All computations are performed on plain Python dicts so the module is fully
functional even when jugeo.geometry and jugeo.generation are not installed
(HAS_JUGEO_DEPS = False).

Usage example
-------------
>>> verifier = ConvergenceVerifier(threshold=1e-6, max_rounds=50)
>>> history = [round_0_gluing, round_1_gluing, ...]
>>> record = verifier.verify(history)
>>> if record.is_converged():
...     cert = verifier.certify_convergence(record)
"""

from __future__ import annotations

import enum
import hashlib
import logging
import math
import statistics
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional jugeo dependencies
# ---------------------------------------------------------------------------

try:
    from jugeo.generation.replay_gluing.models import ConvergenceRecord, GluingUnderReplay, ReplayPhase
    HAS_MODELS = True
except ImportError:
    HAS_MODELS = False

try:
    from jugeo.geometry.descent import DescentEngine, DescentResult, GluingData
    from jugeo.generation.treaties import OverlapTreaty, TreatyStatus
    HAS_JUGEO_DEPS = True
except ImportError:
    HAS_JUGEO_DEPS = False

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

CONVERGENCE_TOLERANCE: float = 1e-6
MAX_CONVERGENCE_ROUNDS: int = 100
_SECTION_KEY = "sections"
_PATCH_HASH_NORMALISER: float = 1e12  # prevents division by 0
_MIN_HISTORY_FOR_RATE: int = 3
_OSCILLATION_SIGN_CHANGES: int = 3


# ---------------------------------------------------------------------------
# ConvergenceStatus enum
# ---------------------------------------------------------------------------


class ConvergenceStatus(enum.Enum):
    """Classification of the convergence state of a gluing sequence.

    CONVERGED     – the metric has dropped below the threshold.
    DIVERGED      – the metric is increasing monotonically.
    OSCILLATING   – the metric alternates above and below a plateau.
    IN_PROGRESS   – the metric is decreasing but has not reached the threshold.
    UNKNOWN       – not enough data to determine status.
    """

    CONVERGED = "converged"
    DIVERGED = "diverged"
    OSCILLATING = "oscillating"
    IN_PROGRESS = "in_progress"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# ConvergenceMetric
# ---------------------------------------------------------------------------


@dataclass
class ConvergenceMetric:
    """Running tracker for a single named convergence quantity.

    Attributes
    ----------
    name : str
        Human-readable name of the metric (e.g. ``"patch_distance"``).
    value : float
        Most recently observed value.
    previous_value : float | None
        The value observed in the preceding update, or ``None`` if this is
        the first observation.
    delta : float
        Absolute change between the last two observations.
    threshold : float
        The value below which the metric is considered converged.
    """

    name: str
    value: float = 0.0
    previous_value: float | None = None
    delta: float = 0.0
    threshold: float = CONVERGENCE_TOLERANCE

    # ------------------------------------------------------------------
    # Mutation helpers
    # ------------------------------------------------------------------

    def update(self, new_value: float) -> None:
        """Advance the metric to *new_value*, recording the previous value."""
        self.previous_value = self.value
        self.delta = abs(new_value - self.previous_value) if self.previous_value is not None else abs(new_value)
        self.value = new_value

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def is_below_threshold(self) -> bool:
        """Return ``True`` if :attr:`value` is at or below :attr:`threshold`."""
        return self.value <= self.threshold

    def get_rate_of_change(self) -> float:
        """Return the relative change since the previous observation.

        Returns ``float('inf')`` if no previous observation exists.
        """
        if self.previous_value is None:
            return float("inf")
        denom = max(abs(self.previous_value), 1e-12)
        return self.delta / denom

    def is_improving(self) -> bool:
        """Return ``True`` if the metric decreased in the last update."""
        return self.previous_value is not None and self.value < self.previous_value

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": self.value,
            "previous_value": self.previous_value,
            "delta": self.delta,
            "threshold": self.threshold,
            "is_converged": self.is_below_threshold(),
            "rate_of_change": (
                self.get_rate_of_change()
                if not math.isinf(self.get_rate_of_change())
                else None
            ),
        }


# ---------------------------------------------------------------------------
# FixedPointChecker
# ---------------------------------------------------------------------------


class FixedPointChecker:
    """Determines whether two consecutive gluings are at a fixed point.

    Parameters
    ----------
    tolerance : float
        Maximum distance that still counts as a fixed point.
    """

    def __init__(self, tolerance: float = CONVERGENCE_TOLERANCE) -> None:
        self.tolerance = tolerance

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def check(self, current: dict[str, Any], previous: dict[str, Any]) -> bool:
        """Return ``True`` if *current* and *previous* are within tolerance."""
        dist = self.compute_distance(current, previous)
        return self.is_fixed_point(dist)

    def compute_distance(self, g1: dict[str, Any], g2: dict[str, Any]) -> float:
        """Compute a normalised distance in [0, 1] between two gluing dicts.

        The distance is computed as the mean per-patch normalised distance,
        where each patch contributes a value in [0, 1].  Patches present in
        only one dict count as fully different (distance = 1.0).
        """
        s1 = g1.get(_SECTION_KEY, g1)
        s2 = g2.get(_SECTION_KEY, g2)

        if not isinstance(s1, dict):
            s1 = {}
        if not isinstance(s2, dict):
            s2 = {}

        all_patches = set(s1) | set(s2)
        if not all_patches:
            return 0.0

        total = 0.0
        for patch in all_patches:
            d = self._compute_patch_distance(s1.get(patch), s2.get(patch))
            total += d

        return total / len(all_patches)

    def is_fixed_point(self, distance: float, tolerance: float | None = None) -> bool:
        """Return ``True`` if *distance* is within *tolerance* of zero."""
        tol = tolerance if tolerance is not None else self.tolerance
        return distance <= tol

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _patch_hash(self, section_data: Any) -> int:
        """Return an integer hash of *section_data*."""
        return hash(repr(section_data))

    def _compute_patch_distance(self, s1: Any, s2: Any) -> float:
        """Return a normalised distance in [0, 1] between two section values.

        Rules:
        * Both None → 0.0
        * One None  → 1.0
        * Both identical by equality → 0.0
        * Both dicts: fraction of keys that differ
        * Otherwise: 1.0 if reprs differ, 0.0 if same
        """
        if s1 is None and s2 is None:
            return 0.0
        if s1 is None or s2 is None:
            return 1.0
        if s1 == s2:
            return 0.0
        if isinstance(s1, dict) and isinstance(s2, dict):
            all_keys = set(s1) | set(s2)
            if not all_keys:
                return 0.0
            diff = sum(1 for k in all_keys if s1.get(k) != s2.get(k))
            frac = diff / len(all_keys)
            # Clamp to (0, 1] so partially-same dicts return 0.5 when exactly
            # half the keys differ, consistent with the docstring contract.
            return frac
        return 0.0 if repr(s1) == repr(s2) else 1.0


# ---------------------------------------------------------------------------
# ConvergenceCertificate
# ---------------------------------------------------------------------------


@dataclass
class ConvergenceCertificate:
    """Attestation that a gluing has successfully converged.

    Attributes
    ----------
    cert_id : str
        Unique certificate identifier.
    gluing_id : str
        Identifier of the gluing that converged.
    rounds_to_converge : int
        Number of replay rounds required.
    final_metric : float
        The convergence metric value at the last round.
    certifier : str
        Name of the component that issued the certificate.
    timestamp : float
        Unix timestamp at issuance.
    expiry : float | None
        Unix timestamp after which the certificate is no longer valid,
        or ``None`` for a perpetual certificate.
    metadata : dict[str, Any]
        Arbitrary additional information.
    """

    cert_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    gluing_id: str = ""
    rounds_to_converge: int = 0
    final_metric: float = 0.0
    certifier: str = "ConvergenceVerifier"
    timestamp: float = field(default_factory=time.time)
    expiry: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self) -> bool:
        """Return ``True`` if the certificate fields are internally consistent."""
        return (
            bool(self.cert_id)
            and bool(self.gluing_id)
            and self.rounds_to_converge > 0
            and self.final_metric >= 0.0
        )

    # ------------------------------------------------------------------
    # Expiry helpers
    # ------------------------------------------------------------------

    def is_expired(self) -> bool:
        """Return ``True`` if the certificate has passed its expiry time."""
        if self.expiry is None:
            return False
        return time.time() > self.expiry

    def get_age_seconds(self) -> float:
        """Return the number of seconds since this certificate was issued."""
        return time.time() - self.timestamp

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "cert_id": self.cert_id,
            "gluing_id": self.gluing_id,
            "rounds_to_converge": self.rounds_to_converge,
            "final_metric": self.final_metric,
            "certifier": self.certifier,
            "timestamp": self.timestamp,
            "expiry": self.expiry,
            "metadata": self.metadata,
            "is_valid": self.validate(),
            "is_expired": self.is_expired(),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ConvergenceCertificate:
        """Reconstruct a certificate from a dict produced by :meth:`to_dict`."""
        return cls(
            cert_id=d.get("cert_id", str(uuid.uuid4())),
            gluing_id=d.get("gluing_id", ""),
            rounds_to_converge=int(d.get("rounds_to_converge", 0)),
            final_metric=float(d.get("final_metric", 0.0)),
            certifier=d.get("certifier", "ConvergenceVerifier"),
            timestamp=float(d.get("timestamp", time.time())),
            expiry=d.get("expiry"),
            metadata=dict(d.get("metadata", {})),
        )


# ---------------------------------------------------------------------------
# ConvergenceReport
# ---------------------------------------------------------------------------


@dataclass
class ConvergenceReport:
    """High-level report summarising the convergence analysis.

    Attributes
    ----------
    gluing_id : str
        Identifier of the gluing being analysed.
    status : ConvergenceStatus
        Classified convergence state.
    metric_history : list[float]
        Sequence of per-round scalar metric values.
    rounds : int
        Number of rounds included in the analysis.
    certificate : ConvergenceCertificate | None
        Certificate, if convergence was certified; ``None`` otherwise.
    """

    gluing_id: str = ""
    status: ConvergenceStatus = ConvergenceStatus.UNKNOWN
    metric_history: list[float] = field(default_factory=list)
    rounds: int = 0
    certificate: ConvergenceCertificate | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "gluing_id": self.gluing_id,
            "status": self.status.value,
            "metric_history": self.metric_history,
            "rounds": self.rounds,
            "certificate": self.certificate.to_dict() if self.certificate else None,
        }


# ---------------------------------------------------------------------------
# ConvergenceVerifier
# ---------------------------------------------------------------------------


class ConvergenceVerifier:
    """Orchestrates multi-round convergence verification.

    Parameters
    ----------
    threshold : float
        The fixed-point tolerance below which convergence is declared.
    max_rounds : int
        Maximum number of rounds to consider.
    checker : FixedPointChecker | None
        Pre-constructed checker; a new one is created if ``None``.
    """

    def __init__(
        self,
        threshold: float = CONVERGENCE_TOLERANCE,
        max_rounds: int = MAX_CONVERGENCE_ROUNDS,
        checker: FixedPointChecker | None = None,
    ) -> None:
        self.threshold = threshold
        self.max_rounds = max_rounds
        self.checker: FixedPointChecker = (
            checker if checker is not None else FixedPointChecker(tolerance=threshold)
        )
        self._metric_history: list[float] = []
        self._round_count: int = 0

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def verify(self, gluing_history: list[dict[str, Any]]) -> Any:
        """Run the full convergence analysis over *gluing_history*.

        Parameters
        ----------
        gluing_history : list[dict[str, Any]]
            Chronological list of gluing state dicts (one per replay round).

        Returns
        -------
        ConvergenceRecord
            Populated record describing whether and when convergence occurred.
        """
        if not gluing_history:
            logger.warning("ConvergenceVerifier.verify: empty history")
            return self._build_record("empty")

        gluing_id = _extract_gluing_id(gluing_history[-1])
        record = self._build_record(gluing_id)
        self._metric_history = []
        self._round_count = 0

        limited_history = gluing_history[: self.max_rounds + 1]

        # Compute metric for first gluing.
        m0 = self.compute_convergence_metric(limited_history[0])
        self._metric_history.append(m0)

        converged_at: int | None = None

        for i in range(1, len(limited_history)):
            prev = limited_history[i - 1]
            curr = limited_history[i]
            self._round_count = i

            metric = self._compute_metric_between(prev, curr)
            self._metric_history.append(metric)

            if self.checker.is_fixed_point(metric):
                converged_at = i
                logger.info(
                    "ConvergenceVerifier: fixed point at round %d (metric=%.3e)",
                    i,
                    metric,
                )
                break

        # Populate record.
        _set_record_metric_history(record, self._metric_history)
        _set_record_rounds(record, self._round_count)
        _set_record_converged(record, converged_at is not None)
        if converged_at is not None:
            _set_record_converged_at(record, converged_at)
            _set_record_final_metric(record, self._metric_history[-1])

        return record

    # ------------------------------------------------------------------
    # Metric computation
    # ------------------------------------------------------------------

    def compute_convergence_metric(self, gluing: dict[str, Any]) -> float:
        """Compute a scalar convergence metric for a single gluing dict.

        The metric is computed as the mean of per-patch hash values,
        normalised to lie in [0, 1].  An empty gluing yields 0.0.
        """
        sections = gluing.get(_SECTION_KEY, gluing)
        if not isinstance(sections, dict) or not sections:
            return 0.0

        total = 0.0
        for patch_data in sections.values():
            h = abs(hash(repr(patch_data)))
            total += h

        normalised = (total / len(sections)) / _PATCH_HASH_NORMALISER
        # Clamp to [0, 1] — the hash values can exceed _PATCH_HASH_NORMALISER.
        return min(normalised, 1.0)

    # ------------------------------------------------------------------
    # Fixed-point checks
    # ------------------------------------------------------------------

    def check_fixed_point(self, history: list[dict[str, Any]]) -> bool:
        """Return ``True`` if the last two entries in *history* are a fixed point."""
        if len(history) < 2:
            return False
        return self.checker.check(history[-1], history[-2])

    # ------------------------------------------------------------------
    # Certification
    # ------------------------------------------------------------------

    def certify_convergence(self, record: Any) -> ConvergenceCertificate | None:
        """Issue a :class:`ConvergenceCertificate` if *record* reports convergence.

        Returns ``None`` if convergence has not been reached.
        """
        if not _record_is_converged(record):
            return None

        gluing_id = _get_record_gluing_id(record)
        rounds = _get_record_rounds(record)
        final_metric = _get_record_final_metric(record)

        cert = ConvergenceCertificate(
            gluing_id=gluing_id,
            rounds_to_converge=max(rounds, 1),
            final_metric=final_metric,
            metadata={
                "threshold": self.threshold,
                "max_rounds": self.max_rounds,
                "metric_history_len": len(self._metric_history),
            },
        )
        logger.info("ConvergenceVerifier: issued certificate %s", cert.cert_id)
        return cert

    # ------------------------------------------------------------------
    # Rate computation
    # ------------------------------------------------------------------

    def compute_convergence_rate(self, record: Any) -> float:
        """Estimate the convergence rate via linear regression on the metric history.

        A negative slope indicates convergence (metric decreasing).
        Returns ``float('nan')`` if there are fewer than 2 data points.
        """
        history = _get_record_metric_history(record)
        if len(history) < _MIN_HISTORY_FOR_RATE:
            return float("nan")
        return _linear_regression_slope(list(range(len(history))), history)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _compute_metric_between(self, g1: dict[str, Any], g2: dict[str, Any]) -> float:
        """Wrapper around :meth:`FixedPointChecker.compute_distance`."""
        return self.checker.compute_distance(g1, g2)

    def _build_record(self, gluing_id: str) -> Any:
        """Construct an empty ConvergenceRecord (or fallback dict)."""
        if not gluing_id:
            gluing_id = str(uuid.uuid4())
        if HAS_MODELS:
            try:
                record = ConvergenceRecord(  # type: ignore[call-arg]
                    gluing_id=gluing_id,
                    converged=False,
                    rounds=0,
                    score=0.0,
                )
                record.metric_history = []
                record.final_metric = float("inf")
                record.converged_at_round = None
                return record
            except Exception:
                pass
        return {
            "gluing_id": gluing_id,
            "converged": False,
            "rounds_completed": 0,
            "metric_history": [],
            "final_metric": float("inf"),
        }


# ---------------------------------------------------------------------------
# Module-level helper functions
# ---------------------------------------------------------------------------


def compute_convergence_status(record: Any) -> ConvergenceStatus:
    """Classify the convergence state of *record*.

    Rules (applied in order):
    1. ``CONVERGED``  – record reports convergence.
    2. ``DIVERGED``   – metric history is monotonically increasing.
    3. ``OSCILLATING``– oscillation is detected in the metric history.
    4. ``IN_PROGRESS``– history is non-empty and metric is decreasing.
    5. ``UNKNOWN``    – none of the above apply.
    """
    history = _get_record_metric_history(record)

    if _record_is_converged(record):
        return ConvergenceStatus.CONVERGED

    if not history:
        return ConvergenceStatus.UNKNOWN

    if len(history) >= 2:
        if _is_metric_monotone_increasing(history):
            return ConvergenceStatus.DIVERGED

        if detect_oscillation(history):
            return ConvergenceStatus.OSCILLATING

        if is_metric_monotone_decreasing(history):
            return ConvergenceStatus.IN_PROGRESS

    return ConvergenceStatus.UNKNOWN


def format_convergence_report(report: ConvergenceReport) -> str:
    """Format *report* as a human-readable multi-line string."""
    lines: list[str] = [
        f"Convergence Report — {report.gluing_id or '(no id)'}",
        f"  Status  : {report.status.value.upper()}",
        f"  Rounds  : {report.rounds}",
    ]
    if report.metric_history:
        first = report.metric_history[0]
        last = report.metric_history[-1]
        lines.append(f"  Metric  : {first:.3e} → {last:.3e}")
        rate = compute_exponential_convergence_rate(report.metric_history)
        if not math.isnan(rate):
            lines.append(f"  Exp. rate : {rate:.4f} per round")

    if report.certificate is not None:
        cert = report.certificate
        lines.append(f"  Certificate : {cert.cert_id[:8]}…  valid={cert.validate()}")
    else:
        lines.append("  Certificate : (none)")

    return "\n".join(lines)


def is_metric_monotone_decreasing(history: list[float]) -> bool:
    """Return ``True`` if every consecutive pair in *history* is strictly decreasing."""
    for i in range(1, len(history)):
        if history[i] >= history[i - 1]:
            return False
    return len(history) >= 2


def _is_metric_monotone_increasing(history: list[float]) -> bool:
    """Return ``True`` if every consecutive pair in *history* is non-decreasing."""
    for i in range(1, len(history)):
        if history[i] < history[i - 1]:
            return False
    return len(history) >= 2


def compute_exponential_convergence_rate(history: list[float]) -> float:
    """Estimate the exponential convergence rate constant.

    Fits a log-linear model: log(metric[i]) ≈ a + b·i.  Returns *b*.
    A negative value indicates exponential convergence.
    Returns ``float('nan')`` if fewer than 2 positive values exist.
    """
    log_vals = []
    indices = []
    for i, v in enumerate(history):
        if v > 0:
            log_vals.append(math.log(v))
            indices.append(float(i))

    if len(log_vals) < 2:
        return float("nan")

    return _linear_regression_slope(indices, log_vals)


def detect_oscillation(history: list[float], window: int = 5) -> bool:
    """Return ``True`` if the metric history exhibits oscillatory behaviour.

    Oscillation is detected by counting sign changes in the first-differences
    within the last *window* values.  A sequence is considered oscillating if
    it has at least :data:`_OSCILLATION_SIGN_CHANGES` sign changes.
    """
    if len(history) < 3:
        return False

    tail = history[-window:]
    diffs = [tail[i + 1] - tail[i] for i in range(len(tail) - 1)]
    sign_changes = sum(
        1 for i in range(1, len(diffs))
        if (diffs[i] > 0) != (diffs[i - 1] > 0)
    )
    return sign_changes >= _OSCILLATION_SIGN_CHANGES


def _linear_regression_slope(xs: list[float], ys: list[float]) -> float:
    """Return the OLS slope of the line fitting *(xs, ys)*.

    Returns ``float('nan')`` if fewer than 2 points, or the denominator
    is zero (all *xs* identical).
    """
    n = len(xs)
    if n < 2:
        return float("nan")

    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    den = sum((x - mean_x) ** 2 for x in xs)
    if den == 0.0:
        return float("nan")
    return num / den


def compute_half_life(history: list[float]) -> float:
    """Compute the convergence half-life in rounds.

    Returns the number of rounds for the metric to halve, based on the
    exponential fit.  Returns ``float('inf')`` for non-converging sequences
    and ``float('nan')`` if the rate cannot be estimated.
    """
    rate = compute_exponential_convergence_rate(history)
    if math.isnan(rate):
        return float("nan")
    if rate >= 0.0:
        return float("inf")  # diverging or flat
    return -math.log(2.0) / rate


def estimate_rounds_to_converge(
    history: list[float],
    target: float = CONVERGENCE_TOLERANCE,
) -> int:
    """Estimate how many additional rounds are needed to reach *target*.

    Uses the exponential convergence rate.  Returns ``-1`` if the estimate
    cannot be computed or the sequence appears to be diverging.
    """
    if not history or history[-1] <= target:
        return 0

    rate = compute_exponential_convergence_rate(history)
    if math.isnan(rate) or rate >= 0.0:
        return -1

    current = history[-1]
    if current <= 0:
        return 0

    try:
        rounds = math.log(target / current) / rate
        return max(0, math.ceil(rounds))
    except (ValueError, ZeroDivisionError):
        return -1


def compute_metric_variance(history: list[float]) -> float:
    """Return the sample variance of *history*, or 0.0 for short sequences."""
    if len(history) < 2:
        return 0.0
    return statistics.variance(history)


def compute_metric_mean(history: list[float]) -> float:
    """Return the arithmetic mean of *history*, or 0.0 if empty."""
    if not history:
        return 0.0
    return statistics.mean(history)


def normalise_metric_history(history: list[float]) -> list[float]:
    """Scale *history* so its maximum value is 1.0 (or return unchanged if all zeros)."""
    if not history:
        return []
    max_val = max(abs(v) for v in history)
    if max_val == 0.0:
        return list(history)
    return [v / max_val for v in history]


def smooth_metric_history(history: list[float], window: int = 3) -> list[float]:
    """Apply a simple moving average with *window* to *history*.

    Boundary values use a smaller window (available samples only).
    """
    if len(history) <= window:
        return list(history)
    smoothed = []
    for i in range(len(history)):
        lo = max(0, i - window // 2)
        hi = min(len(history), i + window // 2 + 1)
        smoothed.append(sum(history[lo:hi]) / (hi - lo))
    return smoothed


def compare_convergence_records(r1: Any, r2: Any) -> dict[str, Any]:
    """Produce a comparison dict between two convergence records.

    Returns keys: ``faster`` (which converged sooner), ``better_metric``
    (which ended with a smaller final metric), ``both_converged`` (bool).
    """
    c1 = _record_is_converged(r1)
    c2 = _record_is_converged(r2)
    rounds1 = _get_record_rounds(r1)
    rounds2 = _get_record_rounds(r2)
    m1 = _get_record_final_metric(r1)
    m2 = _get_record_final_metric(r2)

    if c1 and c2:
        faster = "r1" if rounds1 <= rounds2 else "r2"
    elif c1:
        faster = "r1"
    elif c2:
        faster = "r2"
    else:
        faster = "neither"

    better_metric = "r1" if m1 <= m2 else "r2"

    return {
        "faster": faster,
        "better_metric": better_metric,
        "both_converged": c1 and c2,
        "r1_rounds": rounds1,
        "r2_rounds": rounds2,
        "r1_final_metric": m1,
        "r2_final_metric": m2,
    }


def build_convergence_report(
    record: Any,
    verifier: ConvergenceVerifier,
) -> ConvergenceReport:
    """Produce a :class:`ConvergenceReport` from *record* and *verifier*."""
    status = compute_convergence_status(record)
    cert = verifier.certify_convergence(record) if _record_is_converged(record) else None

    return ConvergenceReport(
        gluing_id=_get_record_gluing_id(record),
        status=status,
        metric_history=list(_get_record_metric_history(record)),
        rounds=_get_record_rounds(record),
        certificate=cert,
    )


def assert_converged(record: Any, tolerance: float = CONVERGENCE_TOLERANCE) -> None:
    """Raise ``AssertionError`` if *record* does not report convergence."""
    if not _record_is_converged(record):
        rounds = _get_record_rounds(record)
        final = _get_record_final_metric(record)
        raise AssertionError(
            f"Gluing did not converge after {rounds} round(s); "
            f"final metric = {final:.3e} (tolerance = {tolerance:.3e})"
        )


def verify_single_round(
    gluing: dict[str, Any],
    checker: FixedPointChecker,
) -> bool:
    """Convenience wrapper: check if a single gluing dict is at a zero-distance
    fixed point with itself (trivially true, useful as a smoke test)."""
    return checker.check(gluing, gluing)


def patch_section_hash(section_data: Any) -> str:
    """Return a stable hex hash of *section_data*."""
    raw = repr(section_data)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def gluing_fingerprint(gluing: dict[str, Any]) -> str:
    """Produce a short fingerprint string for an entire gluing dict."""
    sections = gluing.get(_SECTION_KEY, gluing)
    if not isinstance(sections, dict):
        return hashlib.sha256(repr(gluing).encode()).hexdigest()[:12]

    ordered = sorted(sections.items())
    combined = "|".join(f"{k}:{repr(v)}" for k, v in ordered)
    return hashlib.sha256(combined.encode()).hexdigest()[:12]


def history_fingerprints(history: list[dict[str, Any]]) -> list[str]:
    """Return a list of fingerprints, one per entry in *history*."""
    return [gluing_fingerprint(g) for g in history]


# ---------------------------------------------------------------------------
# Private record accessors (duck-typed to handle both ORM objects and dicts)
# ---------------------------------------------------------------------------


def _extract_gluing_id(gluing: Any) -> str:
    """Best-effort extraction of a gluing identifier."""
    if isinstance(gluing, dict):
        return str(gluing.get("gluing_id", gluing.get("plan_id", "")))
    return str(getattr(gluing, "gluing_id", getattr(gluing, "plan_id", "")))


def _set_record_metric_history(record: Any, history: list[float]) -> None:
    if isinstance(record, dict):
        record["metric_history"] = list(history)
    elif hasattr(record, "metric_history"):
        try:
            record.metric_history = list(history)
        except AttributeError:
            pass


def _set_record_rounds(record: Any, rounds: int) -> None:
    if isinstance(record, dict):
        record["rounds_completed"] = rounds
        record["rounds"] = rounds
    elif hasattr(record, "rounds"):
        try:
            record.rounds = rounds
        except AttributeError:
            pass
    elif hasattr(record, "rounds_completed"):
        try:
            record.rounds_completed = rounds
        except AttributeError:
            pass


def _set_record_converged(record: Any, converged: bool) -> None:
    if isinstance(record, dict):
        record["converged"] = converged
    elif hasattr(record, "converged"):
        try:
            record.converged = converged
        except AttributeError:
            pass


def _set_record_converged_at(record: Any, round_idx: int) -> None:
    if isinstance(record, dict):
        record["converged_at_round"] = round_idx
    elif hasattr(record, "converged_at_round"):
        try:
            record.converged_at_round = round_idx
        except AttributeError:
            pass


def _set_record_final_metric(record: Any, metric: float) -> None:
    if isinstance(record, dict):
        record["final_metric"] = metric
        record["score"] = metric
    elif hasattr(record, "score"):
        try:
            record.score = metric
            record.final_metric = metric
        except AttributeError:
            pass
    elif hasattr(record, "final_metric"):
        try:
            record.final_metric = metric
        except AttributeError:
            pass


def _record_is_converged(record: Any) -> bool:
    if isinstance(record, dict):
        return bool(record.get("converged", False))
    return bool(getattr(record, "converged", False))


def _get_record_gluing_id(record: Any) -> str:
    if isinstance(record, dict):
        return str(record.get("gluing_id", ""))
    return str(getattr(record, "gluing_id", ""))


def _get_record_rounds(record: Any) -> int:
    if isinstance(record, dict):
        return int(record.get("rounds_completed", record.get("rounds", 0)))
    return int(getattr(record, "rounds_completed", getattr(record, "rounds", 0)))


def _get_record_final_metric(record: Any) -> float:
    if isinstance(record, dict):
        return float(record.get("final_metric", record.get("score", float("inf"))))
    return float(getattr(record, "final_metric", getattr(record, "score", float("inf"))))


def _get_record_metric_history(record: Any) -> list[float]:
    if isinstance(record, dict):
        return list(record.get("metric_history", []))
    h = getattr(record, "metric_history", [])
    return list(h) if h else []


# ---------------------------------------------------------------------------
# Bulk verification helpers
# ---------------------------------------------------------------------------


def verify_multiple_gluings(
    histories: list[list[dict[str, Any]]],
    threshold: float = CONVERGENCE_TOLERANCE,
    max_rounds: int = MAX_CONVERGENCE_ROUNDS,
) -> list[ConvergenceReport]:
    """Verify convergence for multiple independent gluing histories.

    Returns a list of :class:`ConvergenceReport` objects in the same order
    as *histories*.
    """
    reports: list[ConvergenceReport] = []
    for history in histories:
        verifier = ConvergenceVerifier(threshold=threshold, max_rounds=max_rounds)
        record = verifier.verify(history)
        reports.append(build_convergence_report(record, verifier))
    return reports


def convergence_summary_table(reports: list[ConvergenceReport]) -> str:
    """Format a tabular summary of *reports*."""
    header = f"{'Gluing':>20}  {'Status':>14}  {'Rounds':>6}  {'Final metric':>14}"
    separator = "-" * len(header)
    rows = [header, separator]
    for rep in reports:
        gid = (rep.gluing_id or "(none)")[:20]
        status = rep.status.value[:14]
        final = rep.metric_history[-1] if rep.metric_history else float("nan")
        rows.append(f"{gid:>20}  {status:>14}  {rep.rounds:>6}  {final:>14.3e}")
    return "\n".join(rows)


# ---------------------------------------------------------------------------
# Module self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)

    # ---- ConvergenceMetric smoke test ----
    m = ConvergenceMetric(name="test_metric", threshold=1e-4)
    m.update(0.5)
    m.update(0.1)
    m.update(0.01)
    assert m.is_improving(), "metric should be improving"
    assert not m.is_below_threshold(), "metric not yet below threshold"
    m.update(1e-5)
    assert m.is_below_threshold(), "metric should be below threshold"

    # ---- FixedPointChecker smoke test ----
    checker = FixedPointChecker(tolerance=1e-6)
    g1 = {_SECTION_KEY: {"p1": {"value": 1.0}, "p2": {"value": 2.0}}}
    g2 = {_SECTION_KEY: {"p1": {"value": 1.0}, "p2": {"value": 2.0}}}
    assert checker.check(g1, g2), "identical gluings should be fixed point"

    g3 = {_SECTION_KEY: {"p1": {"value": 99.0}, "p2": {"value": 2.0}}}
    assert not checker.check(g1, g3), "different gluings should not be fixed point"

    # ---- ConvergenceCertificate round-trip ----
    cert = ConvergenceCertificate(gluing_id="test-gluing", rounds_to_converge=5, final_metric=1e-8)
    assert cert.validate()
    restored = ConvergenceCertificate.from_dict(cert.to_dict())
    assert restored.gluing_id == cert.gluing_id

    # ---- ConvergenceVerifier end-to-end ----
    # Build a converging history: sections drift to a fixed value.
    history: list[dict[str, Any]] = []
    for i in range(10):
        val = 1.0 / (i + 1)
        history.append({_SECTION_KEY: {"p1": {"value": val}, "p2": {"value": val}}})
    # Add a truly fixed round at the end.
    history.append(history[-1])

    verifier = ConvergenceVerifier(threshold=0.6, max_rounds=20)
    record = verifier.verify(history)
    assert _record_is_converged(record), "sequence should converge"

    cert2 = verifier.certify_convergence(record)
    assert cert2 is not None
    assert cert2.validate()

    # ---- Helper functions ----
    assert is_metric_monotone_decreasing([1.0, 0.5, 0.25, 0.1])
    assert not is_metric_monotone_decreasing([1.0, 0.5, 0.6])
    assert not detect_oscillation([1.0, 0.9, 0.8, 0.7, 0.6])  # monotone, no oscillation

    rate = compute_exponential_convergence_rate([1.0, 0.5, 0.25, 0.125])
    assert rate < 0, "rate should be negative for converging sequence"

    report = build_convergence_report(record, verifier)
    text = format_convergence_report(report)
    assert "CONVERGED" in text.upper() or "converged" in text

    print("convergence_verification: smoke tests passed ✓")
