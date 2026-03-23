from __future__ import annotations

"""
When Coding Should Stop and Theory Should Begin (Ch52 §2).

# copilot: This module implements the obstruction-density threshold analysis
# that determines when continued coding yields diminishing returns and the
# system should switch to theorem-proving mode.

Layout
------
+--------------------------------------+-----------------------------------+
| Component                            | Responsibility                    |
+======================================+===================================+
| WhenCodingShouldStopConfig           | Frozen config for thresholds      |
+--------------------------------------+-----------------------------------+
| ObstructionDensityMeasure            | Snapshot of obstruction density   |
+--------------------------------------+-----------------------------------+
| RepairAttemptRecord                  | One code-repair attempt result    |
+--------------------------------------+-----------------------------------+
| SwitchingDecision                    | Output: should we switch?         |
+--------------------------------------+-----------------------------------+
| WhenCodingShouldStopAnalyzer         | Core decision logic               |
+--------------------------------------+-----------------------------------+
| WhenCodingShouldStopWitness          | Decision ledger / audit trail     |
+--------------------------------------+-----------------------------------+
| WhenCodingShouldStopCoordinator      | Orchestrator façade               |
+--------------------------------------+-----------------------------------+

Domain Background
-----------------
In the theorem-growth economics model, the codebase is navigated via a
coordinate system over an obstruction landscape.  When obstruction density
exceeds the configured threshold *and* repeated repair attempts have all
failed to reduce that density, continuing to write code produces negative
marginal yield.  The correct economic decision is to switch effort toward
producing new theorems that structurally lower the obstruction density.

The hysteresis factor prevents rapid oscillation: once the system switches
to theory mode it requires the density to fall below
``threshold - hysteresis_factor`` before recommending a switch back.

Mathematical formulation
~~~~~~~~~~~~~~~~~~~~~~~~
Let ``ρ = obstruction_count / coordinate_count`` be the density ratio.
Let ``f`` be the fraction of repair attempts that *reduced* density.

The switching score is::

    score = ρ / threshold  +  (1 - f)

When ``score ≥ 1.5`` and consecutive failures ≥ ``min_consecutive_failures``,
``SwitchingDecision.should_switch`` is ``True``.

Confidence is calibrated as ``min(1.0, score / 2.0)``.
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

__all__ = [
    "WhenCodingShouldStopConfig",
    "ObstructionDensityMeasure",
    "RepairAttemptRecord",
    "SwitchingDecision",
    "WhenCodingShouldStopAnalyzer",
    "WhenCodingShouldStopWitness",
    "WhenCodingShouldStopCoordinator",
    "_default_regime_id",
    "_clamp",
    "_now_iso",
    "_count_obstructions",
    "_density_ratio",
]

# ---------------------------------------------------------------------------
# Module-level pure helpers
# ---------------------------------------------------------------------------


def _default_regime_id() -> str:
    """Return a default regime identifier derived from the current UTC time.

    The format is ``regime-<YYYYmmddHHMM>`` so that regimes created in the
    same minute share an identifier, which is useful in tests.

    Returns
    -------
    str
        A stable, human-readable regime identifier.

    Examples
    --------
    >>> r = _default_regime_id()
    >>> r.startswith("regime-")
    True
    """
    ts = datetime.datetime.utcnow().strftime("%Y%m%d%H%M")
    return f"regime-{ts}"


def _clamp(v: float, lo: float, hi: float) -> float:
    """Clamp *v* to the closed interval [lo, hi].

    Parameters
    ----------
    v:
        The value to clamp.
    lo:
        Lower bound (inclusive).
    hi:
        Upper bound (inclusive).

    Returns
    -------
    float
        ``lo`` if ``v < lo``, ``hi`` if ``v > hi``, else ``v``.

    Examples
    --------
    >>> _clamp(1.5, 0.0, 1.0)
    1.0
    >>> _clamp(-0.1, 0.0, 1.0)
    0.0
    >>> _clamp(0.5, 0.0, 1.0)
    0.5
    """
    return max(lo, min(hi, v))


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string.

    Returns
    -------
    str
        e.g. ``"2024-01-15T12:34:56.789012"``

    Examples
    --------
    >>> ts = _now_iso()
    >>> "T" in ts
    True
    """
    return datetime.datetime.utcnow().isoformat()


def _count_obstructions(coords: list[dict]) -> int:
    """Count the number of coordinates that are marked as obstructed.

    A coordinate is considered obstructed when its dict contains the key
    ``"obstructed"`` with a truthy value, **or** when it contains the key
    ``"type"`` equal to ``"obstruction"``.

    Parameters
    ----------
    coords:
        A list of coordinate dicts from the codebase navigation layer.

    Returns
    -------
    int
        The number of obstructed coordinates found.

    Examples
    --------
    >>> coords = [
    ...     {"id": "c1", "obstructed": True},
    ...     {"id": "c2", "obstructed": False},
    ...     {"id": "c3", "type": "obstruction"},
    ... ]
    >>> _count_obstructions(coords)
    2
    """
    count = 0
    for c in coords:
        if c.get("obstructed") or c.get("type") == "obstruction":
            count += 1
    return count


def _density_ratio(obstructions: int, total: int) -> float:
    """Compute the obstruction density ratio.

    Parameters
    ----------
    obstructions:
        Number of obstructed coordinates.
    total:
        Total number of coordinates.

    Returns
    -------
    float
        ``obstructions / total``, or ``0.0`` when *total* is zero.

    Examples
    --------
    >>> _density_ratio(3, 10)
    0.3
    >>> _density_ratio(0, 0)
    0.0
    """
    if total == 0:
        return 0.0
    return obstructions / total


# ---------------------------------------------------------------------------
# Value objects (frozen dataclasses)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WhenCodingShouldStopConfig:
    """Configuration for the switching-decision analyser.

    Attributes
    ----------
    obstruction_density_threshold:
        The density ratio above which coding effort becomes unproductive.
        Default: 0.7 (70% of coordinates obstructed).
    repair_attempt_limit:
        Maximum number of repair attempts to consider.  Older records beyond
        this window are ignored in the consecutive-failure count.
    min_consecutive_failures:
        Minimum number of consecutive failed repairs before switching is
        recommended.
    switching_cost:
        Economic cost (in normalised budget units) of switching to theory
        mode.  Used to dampen switching when the density is only marginally
        above the threshold.
    hysteresis_factor:
        Hysteresis band around the threshold.  Once in theory mode the
        density must fall to ``threshold - hysteresis_factor`` before
        switching back.  This prevents rapid oscillation between modes.

    Examples
    --------
    >>> cfg = WhenCodingShouldStopConfig()
    >>> cfg.obstruction_density_threshold
    0.7
    >>> cfg.hysteresis_factor
    0.15
    """

    obstruction_density_threshold: float = 0.7
    repair_attempt_limit: int = 5
    min_consecutive_failures: int = 3
    switching_cost: float = 0.1
    hysteresis_factor: float = 0.15


@dataclass(frozen=True, slots=True)
class ObstructionDensityMeasure:
    """A point-in-time measurement of obstruction density.

    Attributes
    ----------
    density:
        The computed density ratio in [0, 1].
    coordinate_count:
        Total number of coordinates in the current scope.
    obstruction_count:
        Number of those coordinates that are currently obstructed.
    timestamp:
        ISO-8601 string recording when the measurement was taken.
    regime_id:
        Identifier of the economic regime in which the measurement was made.

    Examples
    --------
    >>> m = ObstructionDensityMeasure(
    ...     density=0.75,
    ...     coordinate_count=100,
    ...     obstruction_count=75,
    ...     timestamp="2024-01-01T00:00:00",
    ...     regime_id="regime-202401010000",
    ... )
    >>> m.density
    0.75
    """

    density: float
    coordinate_count: int
    obstruction_count: int
    timestamp: str
    regime_id: str


@dataclass(frozen=True, slots=True)
class RepairAttemptRecord:
    """Record of a single code-repair attempt.

    Attributes
    ----------
    attempt_id:
        Unique identifier for this repair attempt.
    success:
        Whether the repair produced a passing test / lint suite.
    obstruction_reduced:
        Whether the repair actually lowered the obstruction density.
    delta_density:
        Change in density (negative = density fell = good).
    strategy:
        Human-readable label for the strategy employed (e.g. ``"refactor"``,
        ``"delete_dead_code"``, ``"add_abstraction"``).

    Notes
    -----
    A repair can ``success=True`` (tests pass) but ``obstruction_reduced=False``
    (the density stayed the same).  The switching logic uses
    ``obstruction_reduced`` rather than ``success`` as its primary signal.

    Examples
    --------
    >>> r = RepairAttemptRecord(
    ...     attempt_id="a-001",
    ...     success=True,
    ...     obstruction_reduced=False,
    ...     delta_density=0.0,
    ...     strategy="refactor",
    ... )
    >>> r.obstruction_reduced
    False
    """

    attempt_id: str
    success: bool
    obstruction_reduced: bool
    delta_density: float
    strategy: str


@dataclass(frozen=True, slots=True)
class SwitchingDecision:
    """The outcome of a switching-decision analysis.

    Attributes
    ----------
    should_switch:
        ``True`` if the system should switch from coding to theorem-proving.
    reason:
        Human-readable explanation of the decision.
    density:
        The obstruction density at decision time.
    consecutive_failures:
        Number of consecutive repair attempts that failed to reduce density.
    confidence:
        Calibrated confidence in [0, 1] that the decision is correct.

    Examples
    --------
    >>> d = SwitchingDecision(
    ...     should_switch=True,
    ...     reason="density 0.82 > threshold 0.70; 4 consecutive failures",
    ...     density=0.82,
    ...     consecutive_failures=4,
    ...     confidence=0.91,
    ... )
    >>> d.should_switch
    True
    """

    should_switch: bool
    reason: str
    density: float
    consecutive_failures: int
    confidence: float


# ---------------------------------------------------------------------------
# Analyser
# ---------------------------------------------------------------------------


class WhenCodingShouldStopAnalyzer:
    """Core analyser for the coding-vs-theory switching decision.

    This class is stateless: all mutable context is passed as arguments to
    each method.  Instantiate it once and reuse across many calls.

    Examples
    --------
    >>> analyzer = WhenCodingShouldStopAnalyzer()
    >>> coords = [{"obstructed": True}] * 8 + [{"obstructed": False}] * 2
    >>> m = analyzer.measure_density(coords)
    >>> m.density
    0.8
    """

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def measure_density(self, coords: list[dict]) -> ObstructionDensityMeasure:
        """Measure the obstruction density from a list of coordinate dicts.

        Parameters
        ----------
        coords:
            Coordinate dicts, each optionally containing ``"obstructed": True``
            or ``"type": "obstruction"``.

        Returns
        -------
        ObstructionDensityMeasure
            Immutable snapshot of the current density.

        Examples
        --------
        >>> a = WhenCodingShouldStopAnalyzer()
        >>> coords = [{"obstructed": True}, {"obstructed": False}]
        >>> m = a.measure_density(coords)
        >>> m.density
        0.5
        """
        total = len(coords)
        obs = _count_obstructions(coords)
        density = _density_ratio(obs, total)
        return ObstructionDensityMeasure(
            density=_clamp(density, 0.0, 1.0),
            coordinate_count=total,
            obstruction_count=obs,
            timestamp=_now_iso(),
            regime_id=_default_regime_id(),
        )

    def evaluate_repair_attempts(
        self, records: list[RepairAttemptRecord]
    ) -> dict[str, Any]:
        """Summarise a list of repair-attempt records.

        Parameters
        ----------
        records:
            A list of :class:`RepairAttemptRecord` instances.

        Returns
        -------
        dict
            A summary dict with keys:
            - ``total``: total attempts
            - ``succeeded``: attempts where ``success=True``
            - ``reduced``: attempts where ``obstruction_reduced=True``
            - ``consecutive_failures``: trailing run of non-reducing attempts
            - ``avg_delta``: mean ``delta_density`` across all records
            - ``reduction_rate``: fraction of attempts that reduced density

        Examples
        --------
        >>> a = WhenCodingShouldStopAnalyzer()
        >>> records = [
        ...     RepairAttemptRecord("r1", True, False, 0.0, "inline"),
        ...     RepairAttemptRecord("r2", False, False, 0.0, "delete"),
        ...     RepairAttemptRecord("r3", False, False, 0.02, "abstract"),
        ... ]
        >>> ev = a.evaluate_repair_attempts(records)
        >>> ev["consecutive_failures"]
        3
        """
        total = len(records)
        succeeded = sum(1 for r in records if r.success)
        reduced = sum(1 for r in records if r.obstruction_reduced)
        avg_delta = statistics.mean([r.delta_density for r in records]) if records else 0.0
        reduction_rate = reduced / total if total else 0.0

        # Count trailing consecutive non-reducing attempts
        consecutive_failures = 0
        for rec in reversed(records):
            if not rec.obstruction_reduced:
                consecutive_failures += 1
            else:
                break

        return {
            "total": total,
            "succeeded": succeeded,
            "reduced": reduced,
            "consecutive_failures": consecutive_failures,
            "avg_delta": avg_delta,
            "reduction_rate": reduction_rate,
        }

    def compute_switching_decision(
        self,
        density: ObstructionDensityMeasure,
        repairs: list[RepairAttemptRecord],
        config: WhenCodingShouldStopConfig,
    ) -> SwitchingDecision:
        """Compute whether the system should switch to theorem-proving mode.

        Parameters
        ----------
        density:
            Current obstruction density measurement.
        repairs:
            Recent repair-attempt records (older → newer order).
        config:
            Configuration object with thresholds.

        Returns
        -------
        SwitchingDecision
            The decision with supporting metadata.

        Algorithm
        ---------
        1. Compute ``score = density.density / threshold + (1 - reduction_rate)``.
        2. Count consecutive failures from the tail of *repairs*.
        3. Switch when ``score ≥ 1.5`` **and** consecutive failures ≥ threshold.
        4. Confidence = ``min(1.0, score / 2.0)``.

        Examples
        --------
        >>> a = WhenCodingShouldStopAnalyzer()
        >>> m = ObstructionDensityMeasure(0.82, 100, 82, "2024-01-01T00:00:00", "r1")
        >>> repairs = [RepairAttemptRecord(f"r{i}", False, False, 0.0, "try") for i in range(4)]
        >>> cfg = WhenCodingShouldStopConfig()
        >>> d = a.compute_switching_decision(m, repairs, cfg)
        >>> d.should_switch
        True
        """
        limited_repairs = repairs[-config.repair_attempt_limit :]
        ev = self.evaluate_repair_attempts(limited_repairs)
        reduction_rate = ev["reduction_rate"]
        consecutive = ev["consecutive_failures"]
        threshold = config.obstruction_density_threshold

        score = (density.density / threshold) + (1.0 - reduction_rate)
        should_switch = (
            score >= 1.5 and consecutive >= config.min_consecutive_failures
        )
        confidence = _clamp(score / 2.0, 0.0, 1.0)

        if should_switch:
            reason = (
                f"density {density.density:.3f} > threshold {threshold:.2f}; "
                f"{consecutive} consecutive failures; score={score:.3f}"
            )
        else:
            reason = (
                f"density {density.density:.3f} (threshold {threshold:.2f}); "
                f"{consecutive} consecutive failures; score={score:.3f}; "
                f"below switching threshold"
            )

        return SwitchingDecision(
            should_switch=should_switch,
            reason=reason,
            density=density.density,
            consecutive_failures=consecutive,
            confidence=confidence,
        )

    def explain_decision(self, decision: SwitchingDecision) -> str:
        """Produce a narrative explanation of a switching decision.

        Parameters
        ----------
        decision:
            The :class:`SwitchingDecision` to explain.

        Returns
        -------
        str
            A multi-sentence prose explanation suitable for logging or display.

        Examples
        --------
        >>> a = WhenCodingShouldStopAnalyzer()
        >>> d = SwitchingDecision(True, "score too high", 0.85, 4, 0.9)
        >>> "theorem" in a.explain_decision(d).lower()
        True
        """
        mode = "THEORY" if decision.should_switch else "CODING"
        lines = [
            f"Switching Decision: {mode}",
            f"  Obstruction density : {decision.density:.4f}",
            f"  Consecutive failures: {decision.consecutive_failures}",
            f"  Confidence          : {decision.confidence:.4f}",
            f"  Reason              : {decision.reason}",
        ]
        if decision.should_switch:
            lines.append(
                "  Action: Redirect budget toward theorem-proving.  "
                "Code-level repairs are no longer producing density reductions."
            )
        else:
            lines.append(
                "  Action: Continue coding.  "
                "There is still marginal value in repair attempts."
            )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Witness (audit ledger)
# ---------------------------------------------------------------------------


class WhenCodingShouldStopWitness:
    """Audit ledger that records and replays switching decisions.

    Decisions are appended in chronological order.  The witness never
    discards records—use :meth:`summary` for aggregate statistics and
    :meth:`replay` to re-evaluate a sequence of decisions.

    Examples
    --------
    >>> w = WhenCodingShouldStopWitness()
    >>> d = SwitchingDecision(True, "density too high", 0.9, 5, 0.95)
    >>> w.record(d)
    >>> w.summary()["total"]
    1
    """

    def __init__(self) -> None:
        self._history: list[SwitchingDecision] = []

    # ------------------------------------------------------------------

    def record(self, decision: SwitchingDecision) -> None:
        """Append a decision to the ledger.

        Parameters
        ----------
        decision:
            The :class:`SwitchingDecision` to record.
        """
        self._history.append(decision)
        _log.debug(
            "WhenCodingShouldStopWitness: recorded decision should_switch=%s "
            "density=%.4f confidence=%.4f",
            decision.should_switch,
            decision.density,
            decision.confidence,
        )

    def summary(self) -> dict[str, Any]:
        """Return aggregate statistics over all recorded decisions.

        Returns
        -------
        dict
            Keys: ``total``, ``switch_count``, ``stay_count``,
            ``avg_density``, ``avg_confidence``, ``switch_rate``.

        Examples
        --------
        >>> w = WhenCodingShouldStopWitness()
        >>> for switch in [True, False, True]:
        ...     w.record(SwitchingDecision(switch, "r", 0.8, 3, 0.8))
        >>> w.summary()["switch_rate"]
        0.6666666666666666
        """
        total = len(self._history)
        if total == 0:
            return {"total": 0, "switch_count": 0, "stay_count": 0,
                    "avg_density": 0.0, "avg_confidence": 0.0, "switch_rate": 0.0}
        switch_count = sum(1 for d in self._history if d.should_switch)
        avg_density = statistics.mean([d.density for d in self._history])
        avg_confidence = statistics.mean([d.confidence for d in self._history])
        return {
            "total": total,
            "switch_count": switch_count,
            "stay_count": total - switch_count,
            "avg_density": avg_density,
            "avg_confidence": avg_confidence,
            "switch_rate": switch_count / total,
        }

    def history(self) -> list[SwitchingDecision]:
        """Return a copy of all recorded decisions.

        Returns
        -------
        list[SwitchingDecision]
            All decisions in insertion order.
        """
        return list(self._history)

    def replay(self, decisions: list[SwitchingDecision]) -> list[str]:
        """Replay a sequence of decisions and produce explanation strings.

        This does *not* add the decisions to the internal ledger; it is a
        pure projection.

        Parameters
        ----------
        decisions:
            Sequence of :class:`SwitchingDecision` instances to replay.

        Returns
        -------
        list[str]
            One explanation string per decision.

        Examples
        --------
        >>> w = WhenCodingShouldStopWitness()
        >>> d = SwitchingDecision(False, "ok", 0.5, 1, 0.4)
        >>> lines = w.replay([d])
        >>> len(lines)
        1
        """
        analyzer = WhenCodingShouldStopAnalyzer()
        return [analyzer.explain_decision(d) for d in decisions]


# ---------------------------------------------------------------------------
# Coordinator (orchestrator façade)
# ---------------------------------------------------------------------------


class WhenCodingShouldStopCoordinator:
    """High-level orchestrator for the coding-stop decision pipeline.

    This façade ties together config, analyser and witness into a single
    object that callers use for the full decision cycle.

    Attributes
    ----------
    config:
        The :class:`WhenCodingShouldStopConfig` in use.
    analyzer:
        The underlying :class:`WhenCodingShouldStopAnalyzer`.
    witness:
        The :class:`WhenCodingShouldStopWitness` ledger.

    Examples
    --------
    >>> coord = WhenCodingShouldStopCoordinator()
    >>> coords = [{"obstructed": True}] * 8 + [{"obstructed": False}] * 2
    >>> repairs = [{"success": False, "obstruction_reduced": False,
    ...             "delta_density": 0.0, "strategy": "noop"} for _ in range(4)]
    >>> decision = coord.run(coords, repairs)
    >>> isinstance(decision, SwitchingDecision)
    True
    """

    def __init__(self, config: WhenCodingShouldStopConfig | None = None) -> None:
        self.config: WhenCodingShouldStopConfig = config or WhenCodingShouldStopConfig()
        self.analyzer: WhenCodingShouldStopAnalyzer = WhenCodingShouldStopAnalyzer()
        self.witness: WhenCodingShouldStopWitness = WhenCodingShouldStopWitness()

    def run(
        self,
        coords: list[dict],
        repairs: list[dict],
    ) -> SwitchingDecision:
        """Execute the full switching-decision pipeline.

        Parameters
        ----------
        coords:
            Coordinate dicts from the codebase navigation layer.
        repairs:
            Raw repair-attempt dicts.  Each must contain at minimum:
            ``success`` (bool), ``obstruction_reduced`` (bool),
            ``delta_density`` (float), ``strategy`` (str).

        Returns
        -------
        SwitchingDecision
            The computed decision (also recorded in :attr:`witness`).

        Examples
        --------
        >>> coord = WhenCodingShouldStopCoordinator()
        >>> d = coord.run([], [])
        >>> d.should_switch
        False
        """
        density_measure = self.analyzer.measure_density(coords)

        repair_records = [
            RepairAttemptRecord(
                attempt_id=r.get("attempt_id", str(uuid.uuid4())[:8]),
                success=bool(r.get("success", False)),
                obstruction_reduced=bool(r.get("obstruction_reduced", False)),
                delta_density=float(r.get("delta_density", 0.0)),
                strategy=str(r.get("strategy", "unknown")),
            )
            for r in repairs
        ]

        decision = self.analyzer.compute_switching_decision(
            density_measure, repair_records, self.config
        )
        self.witness.record(decision)
        _log.info(
            "WhenCodingShouldStopCoordinator.run: should_switch=%s density=%.4f",
            decision.should_switch,
            decision.density,
        )
        return decision

    def report(self) -> dict[str, Any]:
        """Return a combined report from the witness ledger.

        Returns
        -------
        dict
            The witness :meth:`~WhenCodingShouldStopWitness.summary` dict,
            augmented with the current config values.

        Examples
        --------
        >>> coord = WhenCodingShouldStopCoordinator()
        >>> r = coord.report()
        >>> "total" in r
        True
        """
        summary = self.witness.summary()
        summary.update(
            {
                "config_threshold": self.config.obstruction_density_threshold,
                "config_repair_limit": self.config.repair_attempt_limit,
                "config_min_failures": self.config.min_consecutive_failures,
                "config_switching_cost": self.config.switching_cost,
                "config_hysteresis": self.config.hysteresis_factor,
            }
        )
        return summary


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=== WhenCodingShouldStop smoke test ===")

    # Build a scenario: 80% obstruction, 4 failed repairs
    coords_sample = [{"obstructed": True}] * 80 + [{"obstructed": False}] * 20
    repairs_sample = [
        {"success": False, "obstruction_reduced": False, "delta_density": 0.0, "strategy": "refactor"},
        {"success": False, "obstruction_reduced": False, "delta_density": 0.0, "strategy": "delete"},
        {"success": True,  "obstruction_reduced": False, "delta_density": 0.0, "strategy": "inline"},
        {"success": False, "obstruction_reduced": False, "delta_density": 0.0, "strategy": "extract"},
    ]

    coordinator = WhenCodingShouldStopCoordinator()
    decision = coordinator.run(coords_sample, repairs_sample)
    print(f"Decision: should_switch={decision.should_switch}")
    print(f"Reason  : {decision.reason}")
    print(f"Confidence: {decision.confidence:.4f}")
    print()

    analyzer = WhenCodingShouldStopAnalyzer()
    print(analyzer.explain_decision(decision))
    print()

    report = coordinator.report()
    print("Report:")
    for k, v in report.items():
        print(f"  {k}: {v}")

    # Low-density scenario (should not switch)
    low_coords = [{"obstructed": True}] * 30 + [{"obstructed": False}] * 70
    low_repairs = [
        {"success": True, "obstruction_reduced": True, "delta_density": -0.05, "strategy": "refactor"},
    ]
    decision2 = coordinator.run(low_coords, low_repairs)
    print(f"\nLow-density decision: should_switch={decision2.should_switch}")
    print("Smoke test passed.")
