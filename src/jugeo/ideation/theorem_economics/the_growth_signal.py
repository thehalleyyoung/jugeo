from __future__ import annotations

"""
The Growth Signal (Ch52 §3).

# copilot: This module measures and interprets the composite growth signal
# that indicates whether investment in new theorems is currently more
# valuable than investment in new code.

Layout
------
+-------------------------------+-------------------------------------------+
| Component                     | Responsibility                            |
+===============================+===========================================+
| GrowthSignalConfig            | Frozen config for signal computation      |
+-------------------------------+-------------------------------------------+
| TheoremROIRecord              | ROI for a single theorem investment       |
+-------------------------------+-------------------------------------------+
| CodeROIRecord                 | ROI for a single code change              |
+-------------------------------+-------------------------------------------+
| GrowthSignalReading           | Composite signal snapshot                 |
+-------------------------------+-------------------------------------------+
| TheGrowthSignalAnalyzer       | Computes and interprets the signal        |
+-------------------------------+-------------------------------------------+
| TheGrowthSignalWitness        | Ledger of historical readings             |
+-------------------------------+-------------------------------------------+
| TheGrowthSignalCoordinator    | Orchestrator façade                       |
+-------------------------------+-------------------------------------------+

Domain Background
-----------------
The growth signal is a weighted average of the marginal return on investment
(ROI) from two competing activities:

1. **Theorem investment**: discovering or proving new theorems that reduce
   the obstruction density structurally.
2. **Code investment**: writing or refactoring code to make progress within
   the existing obstruction landscape.

When the theorem ROI is substantially higher than the code ROI, the growth
signal is *positive* (favour theory).  When code ROI dominates, the signal
is *negative* (favour coding).

Mathematical formulation
~~~~~~~~~~~~~~~~~~~~~~~~
For a theorem *i* with cost ``c_i`` and value ``v_i``::

    roi_theorem_i = (v_i - c_i) / max(c_i, ε)

Aggregate theorem ROI is the mean across the investment window.

For a code change *j* that changes ``L`` lines and reduces ``R``
obstructions::

    roi_code_j = R / max(L, 1)

The composite signal is::

    net_signal = w_T * mean_theorem_roi - w_C * mean_code_roi + novelty_bonus

where ``w_T + w_C = 1`` and ``novelty_bonus`` rewards theorems that cover
genuinely new semantic territory.

A moving-average smoother with configurable window is applied before
interpretation to reduce noise from one-off investments.

Interpretation thresholds
~~~~~~~~~~~~~~~~~~~~~~~~~
- ``net_signal > 0.2``  → STRONGLY_FAVOR_THEORY
- ``0.05 < net_signal ≤ 0.2``  → FAVOR_THEORY
- ``-0.05 ≤ net_signal ≤ 0.05`` → NEUTRAL
- ``-0.2 ≤ net_signal < -0.05`` → FAVOR_CODE
- ``net_signal < -0.2`` → STRONGLY_FAVOR_CODE
"""

import datetime
import logging
import math
import statistics
import uuid
from dataclasses import dataclass, field
from typing import Any

try:
    from .models import (
        TheoremYieldModel,
        MarginalValue,
        InvestmentSchedule,
        BudgetAllocation,
        YieldForecast,
        RegimeEconomics,
        EconomicEquilibrium,
        TheoremPortfolioValue,
        CompoundingEffect,
    )
except ImportError:
    TheoremYieldModel = None  # type: ignore[assignment,misc]
    MarginalValue = None  # type: ignore[assignment,misc]
    InvestmentSchedule = None  # type: ignore[assignment,misc]
    BudgetAllocation = None  # type: ignore[assignment,misc]
    YieldForecast = None  # type: ignore[assignment,misc]
    RegimeEconomics = None  # type: ignore[assignment,misc]
    EconomicEquilibrium = None  # type: ignore[assignment,misc]
    TheoremPortfolioValue = None  # type: ignore[assignment,misc]
    CompoundingEffect = None  # type: ignore[assignment,misc]

_log = logging.getLogger(__name__)

# Interpretation thresholds
_STRONG_THEORY_THRESHOLD = 0.20
_THEORY_THRESHOLD = 0.05
_CODE_THRESHOLD = -0.05
_STRONG_CODE_THRESHOLD = -0.20

# Small epsilon to avoid division-by-zero
_EPS = 1e-9

__all__ = [
    "GrowthSignalConfig",
    "TheoremROIRecord",
    "CodeROIRecord",
    "GrowthSignalReading",
    "TheGrowthSignalAnalyzer",
    "TheGrowthSignalWitness",
    "TheGrowthSignalCoordinator",
    "_safe_roi",
    "_weighted_avg",
    "_moving_average",
    "_now_iso",
]

# ---------------------------------------------------------------------------
# Module-level pure helpers
# ---------------------------------------------------------------------------


def _safe_roi(value: float, cost: float) -> float:
    """Compute a safe return-on-investment ratio.

    Parameters
    ----------
    value:
        The value (benefit) produced by the investment.
    cost:
        The cost of the investment (must be non-negative).

    Returns
    -------
    float
        ``(value - cost) / max(cost, ε)`` clamped to [-10, 10] to prevent
        extreme outliers from dominating the aggregate signal.

    Examples
    --------
    >>> round(_safe_roi(10.0, 2.0), 4)
    4.0
    >>> round(_safe_roi(0.0, 0.0), 4)
    0.0
    >>> round(_safe_roi(1.0, 0.5), 4)
    1.0
    """
    denom = max(cost, _EPS)
    raw = (value - cost) / denom
    return max(-10.0, min(10.0, raw))


def _weighted_avg(a: float, wa: float, b: float, wb: float) -> float:
    """Compute a weighted average of two values.

    Parameters
    ----------
    a:
        First value.
    wa:
        Weight for the first value.
    b:
        Second value.
    wb:
        Weight for the second value.

    Returns
    -------
    float
        ``(a * wa + b * wb) / (wa + wb)`` or ``0.0`` if both weights are zero.

    Examples
    --------
    >>> _weighted_avg(1.0, 0.6, 0.0, 0.4)
    0.6
    >>> _weighted_avg(2.0, 0.5, 4.0, 0.5)
    3.0
    """
    total_weight = wa + wb
    if total_weight == 0.0:
        return 0.0
    return (a * wa + b * wb) / total_weight


def _moving_average(values: list[float], window: int) -> list[float]:
    """Compute a simple moving average.

    Parameters
    ----------
    values:
        Time-ordered sequence of floats.
    window:
        Number of observations to include in each average (≥ 1).

    Returns
    -------
    list[float]
        Moving averages of the same length as *values*.  For the first
        ``window - 1`` positions, the average is computed over all available
        data up to that point (expanding window).

    Examples
    --------
    >>> _moving_average([1.0, 2.0, 3.0, 4.0], 2)
    [1.0, 1.5, 2.5, 3.5]
    >>> _moving_average([], 3)
    []
    """
    if not values:
        return []
    w = max(1, window)
    result: list[float] = []
    for i, _ in enumerate(values):
        start = max(0, i - w + 1)
        chunk = values[start : i + 1]
        result.append(statistics.mean(chunk))
    return result


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string.

    Returns
    -------
    str
        e.g. ``"2024-01-15T12:34:56.789012"``

    Examples
    --------
    >>> "T" in _now_iso()
    True
    """
    return datetime.datetime.utcnow().isoformat()


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GrowthSignalConfig:
    """Configuration for the growth signal computation.

    Attributes
    ----------
    theorem_roi_weight:
        Weight applied to the aggregate theorem ROI in the composite signal.
        Default 0.6.  Should satisfy ``theorem_roi_weight + code_roi_weight ≈ 1``.
    code_roi_weight:
        Weight applied to the aggregate code ROI.  Default 0.4.
    smoothing_window:
        Number of readings to include in the moving-average smoother.
        Default 5.
    novelty_bonus:
        Additive bonus applied when the latest theorem batch contains at
        least one novel theorem (not yet seen in the portfolio).
        Default 0.1.
    decay_factor:
        Exponential decay applied to older readings when computing the
        aggregate.  At ``decay_factor=0.95`` a reading 10 steps old has
        weight ``0.95^10 ≈ 0.60``.  Default 0.95.

    Examples
    --------
    >>> cfg = GrowthSignalConfig()
    >>> cfg.theorem_roi_weight + cfg.code_roi_weight
    1.0
    """

    theorem_roi_weight: float = 0.6
    code_roi_weight: float = 0.4
    smoothing_window: int = 5
    novelty_bonus: float = 0.1
    decay_factor: float = 0.95


@dataclass(frozen=True, slots=True)
class TheoremROIRecord:
    """Return-on-investment record for a single theorem.

    Attributes
    ----------
    theorem_id:
        Unique identifier of the theorem.
    cost:
        Effort (in normalised budget units) required to discover/prove this theorem.
    value:
        Value produced (in the same units as cost).
    roi:
        Pre-computed ROI = ``(value - cost) / max(cost, ε)``.
    timestamp:
        ISO-8601 timestamp when the theorem was completed.

    Examples
    --------
    >>> r = TheoremROIRecord("th-001", 2.0, 8.0, 3.0, "2024-01-01T00:00:00")
    >>> r.roi
    3.0
    """

    theorem_id: str
    cost: float
    value: float
    roi: float
    timestamp: str


@dataclass(frozen=True, slots=True)
class CodeROIRecord:
    """Return-on-investment record for a single code change.

    Attributes
    ----------
    change_id:
        Unique identifier of the code change (e.g. a commit hash prefix).
    lines_changed:
        Number of source lines modified (added + deleted).
    obstructions_reduced:
        How many obstructions were removed as a direct result of this change.
    roi:
        Pre-computed ROI = ``obstructions_reduced / max(lines_changed, 1)``.
    timestamp:
        ISO-8601 timestamp when the change was applied.

    Notes
    -----
    ROI values close to zero indicate that code changes are producing very
    little obstruction reduction per unit of effort, which is a leading
    indicator that theory investment may be more productive.

    Examples
    --------
    >>> r = CodeROIRecord("abc123", 50, 5, 0.1, "2024-01-01T00:00:00")
    >>> r.roi
    0.1
    """

    change_id: str
    lines_changed: int
    obstructions_reduced: int
    roi: float
    timestamp: str


@dataclass(frozen=True, slots=True)
class GrowthSignalReading:
    """A single composite growth signal reading.

    Attributes
    ----------
    signal_id:
        Unique identifier for this reading.
    theorem_roi:
        Aggregate theorem ROI used in this reading.
    code_roi:
        Aggregate code ROI used in this reading.
    net_signal:
        The composite signal value.  Positive values indicate theory is
        more productive; negative values indicate coding is more productive.
    recommendation:
        Human-readable recommendation string (see module docstring for
        interpretation thresholds).
    timestamp:
        ISO-8601 timestamp.

    Examples
    --------
    >>> r = GrowthSignalReading(
    ...     signal_id="sig-001",
    ...     theorem_roi=2.5,
    ...     code_roi=0.3,
    ...     net_signal=0.42,
    ...     recommendation="STRONGLY_FAVOR_THEORY",
    ...     timestamp="2024-01-01T00:00:00",
    ... )
    >>> r.net_signal > 0
    True
    """

    signal_id: str
    theorem_roi: float
    code_roi: float
    net_signal: float
    recommendation: str
    timestamp: str


# ---------------------------------------------------------------------------
# Analyser
# ---------------------------------------------------------------------------


class TheGrowthSignalAnalyzer:
    """Core analyser for the growth signal.

    This class is stateless: all mutable context is passed as arguments.
    Instantiate once and reuse across many calls.

    Examples
    --------
    >>> analyzer = TheGrowthSignalAnalyzer()
    >>> theorems = [{"theorem_id": "t1", "cost": 1.0, "value": 4.0}]
    >>> records = analyzer.compute_theorem_roi(theorems)
    >>> records[0].roi
    3.0
    """

    # ------------------------------------------------------------------
    # Theorem ROI
    # ------------------------------------------------------------------

    def compute_theorem_roi(
        self, theorems: list[dict]
    ) -> list[TheoremROIRecord]:
        """Compute ROI records for a batch of theorems.

        Parameters
        ----------
        theorems:
            List of dicts, each with keys ``theorem_id`` (str, optional),
            ``cost`` (float), ``value`` (float).  Missing keys default to
            sensible zero values.

        Returns
        -------
        list[TheoremROIRecord]
            One record per input theorem.

        Examples
        --------
        >>> a = TheGrowthSignalAnalyzer()
        >>> recs = a.compute_theorem_roi([{"cost": 1.0, "value": 5.0}])
        >>> recs[0].roi
        4.0
        """
        records: list[TheoremROIRecord] = []
        for t in theorems:
            tid = str(t.get("theorem_id", uuid.uuid4().hex[:8]))
            cost = float(t.get("cost", 0.0))
            value = float(t.get("value", 0.0))
            roi = _safe_roi(value, cost)
            records.append(
                TheoremROIRecord(
                    theorem_id=tid,
                    cost=cost,
                    value=value,
                    roi=roi,
                    timestamp=_now_iso(),
                )
            )
        return records

    # ------------------------------------------------------------------
    # Code ROI
    # ------------------------------------------------------------------

    def compute_code_roi(self, changes: list[dict]) -> list[CodeROIRecord]:
        """Compute ROI records for a batch of code changes.

        Parameters
        ----------
        changes:
            List of dicts with keys ``change_id`` (str, optional),
            ``lines_changed`` (int), ``obstructions_reduced`` (int).

        Returns
        -------
        list[CodeROIRecord]
            One record per input change.

        Examples
        --------
        >>> a = TheGrowthSignalAnalyzer()
        >>> recs = a.compute_code_roi([{"lines_changed": 100, "obstructions_reduced": 10}])
        >>> recs[0].roi
        0.1
        """
        records: list[CodeROIRecord] = []
        for c in changes:
            cid = str(c.get("change_id", uuid.uuid4().hex[:8]))
            lines = max(1, int(c.get("lines_changed", 1)))
            obs_reduced = int(c.get("obstructions_reduced", 0))
            roi = obs_reduced / lines
            records.append(
                CodeROIRecord(
                    change_id=cid,
                    lines_changed=lines,
                    obstructions_reduced=obs_reduced,
                    roi=roi,
                    timestamp=_now_iso(),
                )
            )
        return records

    # ------------------------------------------------------------------
    # Composite signal
    # ------------------------------------------------------------------

    def compute_signal(
        self,
        theorem_records: list[TheoremROIRecord],
        code_records: list[CodeROIRecord],
        config: GrowthSignalConfig,
    ) -> GrowthSignalReading:
        """Compute the composite growth signal from ROI records.

        Parameters
        ----------
        theorem_records:
            Theorem ROI records from :meth:`compute_theorem_roi`.
        code_records:
            Code ROI records from :meth:`compute_code_roi`.
        config:
            :class:`GrowthSignalConfig` controlling weights and bonuses.

        Returns
        -------
        GrowthSignalReading
            Composite signal reading.

        Algorithm
        ---------
        1. Compute mean theorem ROI and mean code ROI.
        2. Apply novelty bonus if ``theorem_records`` is non-empty.
        3. Compute ``net_signal = w_T * t_roi - w_C * c_roi + novelty``.
        4. Determine recommendation from thresholds.

        Examples
        --------
        >>> a = TheGrowthSignalAnalyzer()
        >>> cfg = GrowthSignalConfig()
        >>> t_recs = a.compute_theorem_roi([{"cost": 1.0, "value": 5.0}])
        >>> c_recs = a.compute_code_roi([{"lines_changed": 100, "obstructions_reduced": 2}])
        >>> sig = a.compute_signal(t_recs, c_recs, cfg)
        >>> sig.net_signal > 0
        True
        """
        t_roi = statistics.mean([r.roi for r in theorem_records]) if theorem_records else 0.0
        c_roi = statistics.mean([r.roi for r in code_records]) if code_records else 0.0
        novelty = config.novelty_bonus if theorem_records else 0.0

        net = _weighted_avg(t_roi, config.theorem_roi_weight, 0.0, 0.0) \
              - _weighted_avg(c_roi, config.code_roi_weight, 0.0, 0.0) \
              + novelty

        # Simpler: net = w_T * t_roi - w_C * c_roi + novelty
        net = config.theorem_roi_weight * t_roi - config.code_roi_weight * c_roi + novelty
        recommendation = self._interpret_net(net)

        return GrowthSignalReading(
            signal_id=uuid.uuid4().hex[:12],
            theorem_roi=t_roi,
            code_roi=c_roi,
            net_signal=net,
            recommendation=recommendation,
            timestamp=_now_iso(),
        )

    # ------------------------------------------------------------------
    # Smoothing
    # ------------------------------------------------------------------

    def smooth_signal(
        self, readings: list[GrowthSignalReading], window: int
    ) -> list[float]:
        """Apply a moving average to a sequence of signal readings.

        Parameters
        ----------
        readings:
            Time-ordered list of :class:`GrowthSignalReading` instances.
        window:
            Moving-average window size.

        Returns
        -------
        list[float]
            Smoothed net-signal values (same length as *readings*).

        Examples
        --------
        >>> a = TheGrowthSignalAnalyzer()
        >>> r = GrowthSignalReading("s1", 1.0, 0.5, 0.3, "FAVOR_THEORY", "2024-01-01T00:00:00")
        >>> a.smooth_signal([r, r], 2)
        [0.3, 0.3]
        """
        values = [r.net_signal for r in readings]
        return _moving_average(values, window)

    # ------------------------------------------------------------------
    # Interpretation
    # ------------------------------------------------------------------

    def interpret(self, reading: GrowthSignalReading) -> str:
        """Produce a narrative interpretation of a growth signal reading.

        Parameters
        ----------
        reading:
            A :class:`GrowthSignalReading` instance.

        Returns
        -------
        str
            Multi-line human-readable interpretation.

        Examples
        --------
        >>> a = TheGrowthSignalAnalyzer()
        >>> r = GrowthSignalReading("s1", 2.0, 0.1, 0.35, "STRONGLY_FAVOR_THEORY", "2024-01-01T00:00:00")
        >>> "theorem" in a.interpret(r).lower()
        True
        """
        lines = [
            f"Growth Signal Reading [{reading.signal_id}]",
            f"  Theorem ROI    : {reading.theorem_roi:+.4f}",
            f"  Code ROI       : {reading.code_roi:+.4f}",
            f"  Net signal     : {reading.net_signal:+.4f}",
            f"  Recommendation : {reading.recommendation}",
            f"  Timestamp      : {reading.timestamp}",
            "",
        ]
        if reading.recommendation in ("STRONGLY_FAVOR_THEORY", "FAVOR_THEORY"):
            lines.append(
                "  Interpretation: Theorem ROI is outpacing code ROI.  "
                "Redirect a larger share of the budget toward theorem discovery "
                "and proof work."
            )
        elif reading.recommendation in ("STRONGLY_FAVOR_CODE", "FAVOR_CODE"):
            lines.append(
                "  Interpretation: Code ROI is outpacing theorem ROI.  "
                "The current obstruction landscape can still be reduced by "
                "targeted code changes."
            )
        else:
            lines.append(
                "  Interpretation: Signal is neutral.  "
                "Maintain the current allocation between theory and code effort."
            )
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _interpret_net(net: float) -> str:
        """Map a net signal value to a recommendation string."""
        if net > _STRONG_THEORY_THRESHOLD:
            return "STRONGLY_FAVOR_THEORY"
        if net > _THEORY_THRESHOLD:
            return "FAVOR_THEORY"
        if net >= _CODE_THRESHOLD:
            return "NEUTRAL"
        if net >= _STRONG_CODE_THRESHOLD:
            return "FAVOR_CODE"
        return "STRONGLY_FAVOR_CODE"


# ---------------------------------------------------------------------------
# Witness
# ---------------------------------------------------------------------------


class TheGrowthSignalWitness:
    """Ledger of historical :class:`GrowthSignalReading` instances.

    Maintains a time-ordered list of readings and provides trend analysis
    and export utilities.

    Examples
    --------
    >>> w = TheGrowthSignalWitness()
    >>> r = GrowthSignalReading("s1", 2.0, 0.1, 0.35, "STRONGLY_FAVOR_THEORY", "2024-01-01T00:00:00")
    >>> w.record(r)
    >>> w.peak() is not None
    True
    """

    def __init__(self) -> None:
        self._readings: list[GrowthSignalReading] = []

    def record(self, reading: GrowthSignalReading) -> None:
        """Append a reading to the ledger.

        Parameters
        ----------
        reading:
            The :class:`GrowthSignalReading` to record.
        """
        self._readings.append(reading)
        _log.debug(
            "GrowthSignalWitness: recorded signal_id=%s net=%.4f recommendation=%s",
            reading.signal_id,
            reading.net_signal,
            reading.recommendation,
        )

    def trend(self) -> str:
        """Describe the recent trend in the growth signal.

        Returns
        -------
        str
            One of ``"RISING"``, ``"FALLING"``, ``"FLAT"``, or ``"INSUFFICIENT_DATA"``.

        Examples
        --------
        >>> w = TheGrowthSignalWitness()
        >>> for val in [0.1, 0.2, 0.35]:
        ...     w.record(GrowthSignalReading("s", 1.0, 0.5, val, "NEUTRAL", "2024-01-01T00:00:00"))
        >>> w.trend()
        'RISING'
        """
        if len(self._readings) < 3:
            return "INSUFFICIENT_DATA"
        recent = [r.net_signal for r in self._readings[-5:]]
        first_half = statistics.mean(recent[: len(recent) // 2])
        second_half = statistics.mean(recent[len(recent) // 2 :])
        diff = second_half - first_half
        if diff > 0.02:
            return "RISING"
        if diff < -0.02:
            return "FALLING"
        return "FLAT"

    def export(self) -> list[dict[str, Any]]:
        """Export all readings as plain dicts.

        Returns
        -------
        list[dict]
            One dict per reading in insertion order.

        Examples
        --------
        >>> w = TheGrowthSignalWitness()
        >>> w.export()
        []
        """
        return [
            {
                "signal_id": r.signal_id,
                "theorem_roi": r.theorem_roi,
                "code_roi": r.code_roi,
                "net_signal": r.net_signal,
                "recommendation": r.recommendation,
                "timestamp": r.timestamp,
            }
            for r in self._readings
        ]

    def clear(self) -> None:
        """Remove all readings from the ledger.

        Examples
        --------
        >>> w = TheGrowthSignalWitness()
        >>> r = GrowthSignalReading("s1", 1.0, 0.5, 0.1, "NEUTRAL", "2024-01-01T00:00:00")
        >>> w.record(r)
        >>> w.clear()
        >>> len(w.export())
        0
        """
        self._readings.clear()

    def peak(self) -> GrowthSignalReading | None:
        """Return the reading with the highest net signal.

        Returns
        -------
        GrowthSignalReading or None
            The peak reading, or ``None`` if the ledger is empty.

        Examples
        --------
        >>> w = TheGrowthSignalWitness()
        >>> r1 = GrowthSignalReading("s1", 1.0, 0.5, 0.1, "NEUTRAL", "2024-01-01T00:00:00")
        >>> r2 = GrowthSignalReading("s2", 2.0, 0.5, 0.5, "FAVOR_THEORY", "2024-01-02T00:00:00")
        >>> w.record(r1); w.record(r2)
        >>> w.peak().signal_id
        's2'
        """
        if not self._readings:
            return None
        return max(self._readings, key=lambda r: r.net_signal)


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------


class TheGrowthSignalCoordinator:
    """High-level orchestrator for the growth signal pipeline.

    Examples
    --------
    >>> coord = TheGrowthSignalCoordinator()
    >>> theorems = [{"cost": 1.0, "value": 5.0}]
    >>> changes = [{"lines_changed": 100, "obstructions_reduced": 2}]
    >>> reading = coord.run(theorems, changes)
    >>> isinstance(reading, GrowthSignalReading)
    True
    """

    def __init__(self, config: GrowthSignalConfig | None = None) -> None:
        self.config: GrowthSignalConfig = config or GrowthSignalConfig()
        self.analyzer: TheGrowthSignalAnalyzer = TheGrowthSignalAnalyzer()
        self.witness: TheGrowthSignalWitness = TheGrowthSignalWitness()

    def run(
        self,
        theorems: list[dict],
        code_changes: list[dict],
    ) -> GrowthSignalReading:
        """Execute the full growth-signal pipeline.

        Parameters
        ----------
        theorems:
            List of theorem dicts (see :meth:`TheGrowthSignalAnalyzer.compute_theorem_roi`).
        code_changes:
            List of code-change dicts (see :meth:`TheGrowthSignalAnalyzer.compute_code_roi`).

        Returns
        -------
        GrowthSignalReading
            The computed reading (also recorded in the witness).

        Examples
        --------
        >>> coord = TheGrowthSignalCoordinator()
        >>> r = coord.run([], [])
        >>> r.recommendation
        'NEUTRAL'
        """
        t_records = self.analyzer.compute_theorem_roi(theorems)
        c_records = self.analyzer.compute_code_roi(code_changes)
        reading = self.analyzer.compute_signal(t_records, c_records, self.config)
        self.witness.record(reading)
        _log.info(
            "TheGrowthSignalCoordinator.run: net_signal=%.4f recommendation=%s",
            reading.net_signal,
            reading.recommendation,
        )
        return reading

    def report(self) -> dict[str, Any]:
        """Return a combined report from the witness ledger.

        Returns
        -------
        dict
            Contains trend, peak reading, total readings, and config values.

        Examples
        --------
        >>> coord = TheGrowthSignalCoordinator()
        >>> r = coord.report()
        >>> "trend" in r
        True
        """
        peak = self.witness.peak()
        return {
            "trend": self.witness.trend(),
            "total_readings": len(self.witness.export()),
            "peak_signal": peak.net_signal if peak else None,
            "peak_recommendation": peak.recommendation if peak else None,
            "config_theorem_weight": self.config.theorem_roi_weight,
            "config_code_weight": self.config.code_roi_weight,
            "config_smoothing_window": self.config.smoothing_window,
            "config_novelty_bonus": self.config.novelty_bonus,
            "config_decay_factor": self.config.decay_factor,
        }


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=== TheGrowthSignal smoke test ===")

    theorems_sample = [
        {"theorem_id": "th-001", "cost": 1.0, "value": 5.0},
        {"theorem_id": "th-002", "cost": 2.0, "value": 6.0},
        {"theorem_id": "th-003", "cost": 0.5, "value": 3.5},
    ]
    code_sample = [
        {"change_id": "ch-001", "lines_changed": 200, "obstructions_reduced": 4},
        {"change_id": "ch-002", "lines_changed": 50, "obstructions_reduced": 1},
    ]

    coord = TheGrowthSignalCoordinator()
    for i in range(5):
        reading = coord.run(theorems_sample, code_sample)
        print(f"Run {i+1}: net_signal={reading.net_signal:.4f} → {reading.recommendation}")

    print()
    report = coord.report()
    print("Report:")
    for k, v in report.items():
        print(f"  {k}: {v}")

    analyzer = TheGrowthSignalAnalyzer()
    print()
    print(analyzer.interpret(reading))

    print("\nSmoothed signals:", coord.analyzer.smooth_signal(
        list(coord.witness._readings), window=3
    ))
    print("Trend:", coord.witness.trend())
    print("Smoke test passed.")
