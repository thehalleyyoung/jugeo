from __future__ import annotations

"""
Scheduling Principle — Allocating Effort Between Theory and Implementation (Ch52 §4).

# copilot: This module implements the scheduling principle that determines
# how research budget should be split between theorem-proving work and
# implementation work based on composite semantic signals.

Layout
------
+-----------------------------------+--------------------------------------+
| Component                         | Responsibility                       |
+===================================+======================================+
| SchedulingConfig                  | Frozen config for scheduler          |
+-----------------------------------+--------------------------------------+
| EffortAllocation                  | A single budget allocation snapshot  |
+-----------------------------------+--------------------------------------+
| SchedulingSignal                  | Composite signal driving allocation  |
+-----------------------------------+--------------------------------------+
| AllocationHistory                 | Mutable ledger of allocations        |
+-----------------------------------+--------------------------------------+
| SchedulingPrincipleAnalyzer       | Core scheduling logic                |
+-----------------------------------+--------------------------------------+
| SchedulingPrincipleWitness        | Records allocations, detects drift   |
+-----------------------------------+--------------------------------------+
| SchedulingPrincipleCoordinator    | Orchestrator façade                  |
+-----------------------------------+--------------------------------------+

Domain Background
-----------------
The scheduling principle governs how a finite research budget is partitioned
between two modes of work:

1. **Theory work** — discovering theorems, producing proofs, building the
   semantic scaffolding that allows the obstruction density to fall.
2. **Implementation work** — writing and refactoring code, applying known
   theorems to concrete artefacts.

The allocation is driven by a composite signal that blends three inputs:

- ``growth_signal``: the signed ROI differential from ``the_growth_signal``
- ``obstruction_density``: raw density ratio from the coordinate system
- ``backlog_pressure``: normalised measure of outstanding implementation tasks

The combined signal is a weighted sum::

    combined = α * growth_signal + β * obstruction_density - γ * backlog_pressure

where ``α + β + γ = 1`` (the weights are derived from ``signal_sensitivity``).

The theory fraction is then::

    theory_fraction = clamp(0.5 + combined * signal_sensitivity,
                            theory_budget_min,
                            theory_budget_max)

and ``implementation_fraction = 1 - theory_fraction``.

Equilibrium tolerance
~~~~~~~~~~~~~~~~~~~~~
If successive allocations change by less than ``equilibrium_tolerance``, the
scheduler is said to be *at equilibrium* and the witness records a stable epoch.

Rebalancing
~~~~~~~~~~~
The ``rebalance_interval`` controls how many steps between forced re-evaluations.
Even when the signal appears stable, the coordinator will recompute the allocation
every ``rebalance_interval`` steps to guard against signal staleness.

Budget conservation invariant
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The invariant ``theory_fraction + implementation_fraction == 1.0`` must hold
at all times.  :meth:`SchedulingPrincipleAnalyzer.validate_allocation` enforces
this.

Examples of allocation profiles
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
+------------------+----------+--------+--------------+-----------------+
| Growth signal    | Density  | Backlog | Theory %     | Mode            |
+==================+==========+========+==============+=================+
| +0.40 (theory)   | 0.80     | 0.20   | ~75-85 %     | Theory-heavy    |
+------------------+----------+--------+--------------+-----------------+
|  0.00 (neutral)  | 0.50     | 0.50   | ~50 %        | Balanced        |
+------------------+----------+--------+--------------+-----------------+
| -0.30 (code)     | 0.30     | 0.80   | ~20-30 %     | Impl-heavy      |
+------------------+----------+--------+--------------+-----------------+
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
    "SchedulingConfig",
    "EffortAllocation",
    "SchedulingSignal",
    "AllocationHistory",
    "SchedulingPrincipleAnalyzer",
    "SchedulingPrincipleWitness",
    "SchedulingPrincipleCoordinator",
    "_clamp",
    "_now_iso",
    "_signal_id",
    "_normalize_fractions",
]

# ---------------------------------------------------------------------------
# Module-level pure helpers
# ---------------------------------------------------------------------------


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
        Clamped value.

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
    >>> "T" in _now_iso()
    True
    """
    return datetime.datetime.utcnow().isoformat()


def _signal_id() -> str:
    """Generate a short unique identifier for a scheduling signal.

    Returns
    -------
    str
        12-character hex string prefixed with ``"sig-"``.

    Examples
    --------
    >>> s = _signal_id()
    >>> s.startswith("sig-")
    True
    """
    return f"sig-{uuid.uuid4().hex[:12]}"


def _normalize_fractions(theory: float, impl: float) -> tuple[float, float]:
    """Normalise theory and implementation fractions so they sum to 1.

    Parameters
    ----------
    theory:
        Raw theory fraction (before normalisation).
    impl:
        Raw implementation fraction (before normalisation).

    Returns
    -------
    tuple[float, float]
        ``(theory_normalised, impl_normalised)`` both in [0, 1] and summing to 1.

    Notes
    -----
    If both inputs are zero the fractions default to a 50/50 split.

    Examples
    --------
    >>> _normalize_fractions(0.6, 0.6)
    (0.5, 0.5)
    >>> _normalize_fractions(0.7, 0.3)
    (0.7, 0.3)
    >>> _normalize_fractions(0.0, 0.0)
    (0.5, 0.5)
    """
    total = theory + impl
    if total == 0.0:
        return 0.5, 0.5
    return theory / total, impl / total


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SchedulingConfig:
    """Configuration for the scheduling principle.

    Attributes
    ----------
    theory_budget_min:
        Minimum fraction of budget allocated to theory (floor).  Default 0.1.
    theory_budget_max:
        Maximum fraction of budget allocated to theory (ceiling).  Default 0.9.
    implementation_budget_min:
        Minimum fraction of budget allocated to implementation (floor).
        Default 0.1.
    rebalance_interval:
        Number of scheduling steps between forced re-evaluations even when
        the signal appears stable.  Default 10.
    signal_sensitivity:
        Scaling factor that converts the combined signal to a fraction offset.
        Higher values make the scheduler more reactive to signal changes.
        Default 0.5.
    equilibrium_tolerance:
        If successive theory fractions differ by less than this value the
        allocation is considered stable.  Default 0.05.

    Notes
    -----
    The constraints ``theory_budget_min + implementation_budget_min ≤ 1`` and
    ``theory_budget_max + implementation_budget_min ≤ 1`` must hold.

    Examples
    --------
    >>> cfg = SchedulingConfig()
    >>> cfg.theory_budget_min
    0.1
    >>> cfg.equilibrium_tolerance
    0.05
    """

    theory_budget_min: float = 0.1
    theory_budget_max: float = 0.9
    implementation_budget_min: float = 0.1
    rebalance_interval: int = 10
    signal_sensitivity: float = 0.5
    equilibrium_tolerance: float = 0.05


@dataclass(frozen=True, slots=True)
class EffortAllocation:
    """A single point-in-time budget allocation.

    Attributes
    ----------
    allocation_id:
        Unique identifier for this allocation snapshot.
    theory_fraction:
        Fraction of the total budget allocated to theory work, in [0, 1].
    implementation_fraction:
        Fraction allocated to implementation work.  Should equal
        ``1 - theory_fraction`` up to floating-point precision.
    total_budget:
        The total budget being partitioned (in normalised units).
    rationale:
        Human-readable string explaining the allocation.
    timestamp:
        ISO-8601 timestamp.

    Invariant
    ---------
    ``abs(theory_fraction + implementation_fraction - 1.0) < 1e-6``

    Examples
    --------
    >>> a = EffortAllocation(
    ...     allocation_id="alloc-001",
    ...     theory_fraction=0.7,
    ...     implementation_fraction=0.3,
    ...     total_budget=100.0,
    ...     rationale="High obstruction density",
    ...     timestamp="2024-01-01T00:00:00",
    ... )
    >>> a.theory_fraction + a.implementation_fraction
    1.0
    """

    allocation_id: str
    theory_fraction: float
    implementation_fraction: float
    total_budget: float
    rationale: str
    timestamp: str


@dataclass(frozen=True, slots=True)
class SchedulingSignal:
    """Composite signal driving the scheduling decision.

    Attributes
    ----------
    signal_id:
        Unique identifier.
    growth_signal:
        The net growth signal from ``s02`` (positive → favour theory).
    obstruction_density:
        The current obstruction density from the coordinate system.
    backlog_pressure:
        Normalised measure of outstanding implementation tasks (higher →
        favour implementation).
    combined:
        The weighted combination of the three inputs.

    Examples
    --------
    >>> s = SchedulingSignal("sig-abc", 0.3, 0.6, 0.2, 0.28)
    >>> s.combined > 0
    True
    """

    signal_id: str
    growth_signal: float
    obstruction_density: float
    backlog_pressure: float
    combined: float


# ---------------------------------------------------------------------------
# AllocationHistory (mutable)
# ---------------------------------------------------------------------------


class AllocationHistory:
    """Mutable container for a time-series of :class:`EffortAllocation` objects.

    This class is intentionally **not** a frozen dataclass because it
    accumulates state over the lifetime of a scheduling session.

    Examples
    --------
    >>> h = AllocationHistory()
    >>> a = EffortAllocation("a1", 0.6, 0.4, 100.0, "high density", "2024-01-01T00:00:00")
    >>> h.append(a)
    >>> len(h._items)
    1
    """

    def __init__(self) -> None:
        self._items: list[EffortAllocation] = []

    def append(self, alloc: EffortAllocation) -> None:
        """Append an allocation to the history.

        Parameters
        ----------
        alloc:
            The :class:`EffortAllocation` to store.
        """
        self._items.append(alloc)

    def recent(self, n: int) -> list[EffortAllocation]:
        """Return the *n* most recent allocations.

        Parameters
        ----------
        n:
            Number of recent entries to return.

        Returns
        -------
        list[EffortAllocation]
            Up to *n* allocations, newest last.

        Examples
        --------
        >>> h = AllocationHistory()
        >>> for i in range(5):
        ...     h.append(EffortAllocation(f"a{i}", 0.5, 0.5, 100.0, "", "2024-01-01T00:00:00"))
        >>> len(h.recent(3))
        3
        """
        return self._items[-n:]

    def to_dict_list(self) -> list[dict[str, Any]]:
        """Serialise all allocations to a list of dicts.

        Returns
        -------
        list[dict]
            Plain-Python representation suitable for JSON serialisation.

        Examples
        --------
        >>> h = AllocationHistory()
        >>> h.to_dict_list()
        []
        """
        return [
            {
                "allocation_id": a.allocation_id,
                "theory_fraction": a.theory_fraction,
                "implementation_fraction": a.implementation_fraction,
                "total_budget": a.total_budget,
                "rationale": a.rationale,
                "timestamp": a.timestamp,
            }
            for a in self._items
        ]

    def average_theory_fraction(self) -> float:
        """Compute the mean theory fraction across all stored allocations.

        Returns
        -------
        float
            Mean ``theory_fraction``, or ``0.5`` when history is empty.

        Examples
        --------
        >>> h = AllocationHistory()
        >>> h.average_theory_fraction()
        0.5
        """
        if not self._items:
            return 0.5
        return statistics.mean([a.theory_fraction for a in self._items])


# ---------------------------------------------------------------------------
# Analyser
# ---------------------------------------------------------------------------


class SchedulingPrincipleAnalyzer:
    """Core scheduling logic.

    This class is stateless: all mutable context is passed as arguments.

    Examples
    --------
    >>> a = SchedulingPrincipleAnalyzer()
    >>> cfg = SchedulingConfig()
    >>> sig = a.compute_signal(0.3, 0.6, 0.2, cfg)
    >>> sig.combined > 0
    True
    """

    def compute_signal(
        self,
        growth: float,
        density: float,
        backlog: float,
        config: SchedulingConfig,
    ) -> SchedulingSignal:
        """Compute the combined scheduling signal.

        Parameters
        ----------
        growth:
            Net growth signal (from ``s02``), typically in [-1, 1].
        density:
            Obstruction density ratio, in [0, 1].
        backlog:
            Backlog pressure, in [0, 1].  Higher values pull toward
            implementation.
        config:
            The :class:`SchedulingConfig` in use.

        Returns
        -------
        SchedulingSignal
            Immutable signal snapshot.

        Algorithm
        ---------
        The combined signal blends the three inputs with fixed weights
        derived from ``signal_sensitivity``::

            α = signal_sensitivity
            β = (1 - signal_sensitivity) * 0.6   # density contributes positively
            γ = (1 - signal_sensitivity) * 0.4   # backlog contributes negatively

            combined = α * growth + β * density - γ * backlog

        Examples
        --------
        >>> a = SchedulingPrincipleAnalyzer()
        >>> s = a.compute_signal(0.0, 0.5, 0.5, SchedulingConfig())
        >>> abs(s.combined) < 0.2
        True
        """
        α = config.signal_sensitivity
        remainder = 1.0 - α
        β = remainder * 0.6
        γ = remainder * 0.4
        combined = α * growth + β * density - γ * backlog
        return SchedulingSignal(
            signal_id=_signal_id(),
            growth_signal=growth,
            obstruction_density=density,
            backlog_pressure=backlog,
            combined=_clamp(combined, -1.0, 1.0),
        )

    def allocate(
        self,
        signal: SchedulingSignal,
        total_budget: float,
        config: SchedulingConfig,
    ) -> EffortAllocation:
        """Translate a scheduling signal into a concrete budget allocation.

        Parameters
        ----------
        signal:
            The :class:`SchedulingSignal` to act on.
        total_budget:
            The total budget to partition (in normalised units).
        config:
            The :class:`SchedulingConfig` in use.

        Returns
        -------
        EffortAllocation
            Immutable allocation snapshot.

        Algorithm
        ---------
        ::

            raw_theory = 0.5 + signal.combined * signal_sensitivity
            theory = clamp(raw_theory, theory_budget_min, theory_budget_max)
            impl   = 1.0 - theory
            # ensure impl also respects implementation_budget_min
            if impl < implementation_budget_min:
                impl   = implementation_budget_min
                theory = 1.0 - impl

        Examples
        --------
        >>> a = SchedulingPrincipleAnalyzer()
        >>> sig = SchedulingSignal("s", 0.4, 0.7, 0.1, 0.4)
        >>> alloc = a.allocate(sig, 100.0, SchedulingConfig())
        >>> alloc.theory_fraction + alloc.implementation_fraction
        1.0
        """
        raw_theory = 0.5 + signal.combined * config.signal_sensitivity
        theory = _clamp(raw_theory, config.theory_budget_min, config.theory_budget_max)
        impl = 1.0 - theory
        if impl < config.implementation_budget_min:
            impl = config.implementation_budget_min
            theory = 1.0 - impl

        theory, impl = _normalize_fractions(theory, impl)

        if signal.combined > 0.2:
            rationale = (
                f"Strong theory signal ({signal.combined:+.3f}): "
                f"obstruction density={signal.obstruction_density:.2f}, "
                f"growth={signal.growth_signal:+.3f}"
            )
        elif signal.combined < -0.2:
            rationale = (
                f"Strong implementation signal ({signal.combined:+.3f}): "
                f"backlog pressure={signal.backlog_pressure:.2f}, "
                f"growth={signal.growth_signal:+.3f}"
            )
        else:
            rationale = (
                f"Balanced signal ({signal.combined:+.3f}): "
                f"maintaining approximate 50/50 split"
            )

        return EffortAllocation(
            allocation_id=f"alloc-{uuid.uuid4().hex[:10]}",
            theory_fraction=theory,
            implementation_fraction=impl,
            total_budget=total_budget,
            rationale=rationale,
            timestamp=_now_iso(),
        )

    def validate_allocation(
        self,
        alloc: EffortAllocation,
        config: SchedulingConfig,
    ) -> list[str]:
        """Validate that an allocation satisfies all config constraints.

        Parameters
        ----------
        alloc:
            The allocation to validate.
        config:
            The config to validate against.

        Returns
        -------
        list[str]
            A list of error messages.  Empty list means the allocation is valid.

        Examples
        --------
        >>> a = SchedulingPrincipleAnalyzer()
        >>> alloc = EffortAllocation("x", 0.7, 0.3, 100.0, "ok", "2024-01-01T00:00:00")
        >>> a.validate_allocation(alloc, SchedulingConfig())
        []
        """
        errors: list[str] = []
        total = alloc.theory_fraction + alloc.implementation_fraction
        if abs(total - 1.0) > 1e-6:
            errors.append(
                f"Budget conservation violated: "
                f"theory={alloc.theory_fraction:.6f} + impl={alloc.implementation_fraction:.6f} "
                f"= {total:.6f} ≠ 1.0"
            )
        if alloc.theory_fraction < config.theory_budget_min - 1e-9:
            errors.append(
                f"Theory fraction {alloc.theory_fraction:.4f} < min {config.theory_budget_min}"
            )
        if alloc.theory_fraction > config.theory_budget_max + 1e-9:
            errors.append(
                f"Theory fraction {alloc.theory_fraction:.4f} > max {config.theory_budget_max}"
            )
        if alloc.implementation_fraction < config.implementation_budget_min - 1e-9:
            errors.append(
                f"Impl fraction {alloc.implementation_fraction:.4f} < min "
                f"{config.implementation_budget_min}"
            )
        if alloc.total_budget < 0:
            errors.append(f"Total budget {alloc.total_budget} is negative")
        return errors

    def explain(
        self,
        alloc: EffortAllocation,
        signal: SchedulingSignal,
    ) -> str:
        """Produce a narrative explanation of an allocation given the signal.

        Parameters
        ----------
        alloc:
            The allocation to explain.
        signal:
            The signal that produced the allocation.

        Returns
        -------
        str
            Multi-line prose explanation.

        Examples
        --------
        >>> a = SchedulingPrincipleAnalyzer()
        >>> sig = SchedulingSignal("s", 0.3, 0.6, 0.1, 0.3)
        >>> alloc = EffortAllocation("a", 0.65, 0.35, 100.0, "high signal", "2024-01-01T00:00:00")
        >>> "theory" in a.explain(alloc, sig).lower()
        True
        """
        theory_units = alloc.theory_fraction * alloc.total_budget
        impl_units = alloc.implementation_fraction * alloc.total_budget
        lines = [
            f"Scheduling Allocation [{alloc.allocation_id}]",
            f"  Theory fraction      : {alloc.theory_fraction:.2%}  ({theory_units:.2f} units)",
            f"  Implementation frac  : {alloc.implementation_fraction:.2%}  ({impl_units:.2f} units)",
            f"  Total budget         : {alloc.total_budget:.2f} units",
            f"  Combined signal      : {signal.combined:+.4f}",
            f"    growth_signal      : {signal.growth_signal:+.4f}",
            f"    obstruction_density: {signal.obstruction_density:.4f}",
            f"    backlog_pressure   : {signal.backlog_pressure:.4f}",
            f"  Rationale: {alloc.rationale}",
        ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Witness
# ---------------------------------------------------------------------------


class SchedulingPrincipleWitness:
    """Audit ledger for effort allocations.

    Records each allocation and detects drift (large shift in theory fraction
    relative to recent history).

    Examples
    --------
    >>> w = SchedulingPrincipleWitness()
    >>> a = EffortAllocation("a1", 0.6, 0.4, 100.0, "test", "2024-01-01T00:00:00")
    >>> w.record(a)
    >>> w.drift_detected()
    False
    """

    _DRIFT_THRESHOLD = 0.20  # 20% shift in theory fraction

    def __init__(self) -> None:
        self._history: AllocationHistory = AllocationHistory()

    def record(self, alloc: EffortAllocation) -> None:
        """Append an allocation to the internal history.

        Parameters
        ----------
        alloc:
            The :class:`EffortAllocation` to record.
        """
        self._history.append(alloc)
        _log.debug(
            "SchedulingPrincipleWitness: theory=%.2f impl=%.2f signal=%s",
            alloc.theory_fraction,
            alloc.implementation_fraction,
            alloc.rationale[:60],
        )

    def drift_detected(self) -> bool:
        """Detect if recent allocations exhibit significant drift.

        Returns
        -------
        bool
            ``True`` if the last allocation differs from the median of the
            preceding five by more than ``_DRIFT_THRESHOLD``.

        Examples
        --------
        >>> w = SchedulingPrincipleWitness()
        >>> for t in [0.5, 0.5, 0.5, 0.5, 0.5]:
        ...     w.record(EffortAllocation("x", t, 1-t, 100.0, "", "2024-01-01T00:00:00"))
        >>> w.record(EffortAllocation("x", 0.9, 0.1, 100.0, "", "2024-01-01T00:00:00"))
        >>> w.drift_detected()
        True
        """
        items = self._history.recent(6)
        if len(items) < 3:
            return False
        baseline = statistics.median([a.theory_fraction for a in items[:-1]])
        latest = items[-1].theory_fraction
        return abs(latest - baseline) > self._DRIFT_THRESHOLD

    def summary(self) -> dict[str, Any]:
        """Return summary statistics over all recorded allocations.

        Returns
        -------
        dict
            Keys: ``total``, ``avg_theory_fraction``, ``drift_detected``,
            ``history_length``.

        Examples
        --------
        >>> w = SchedulingPrincipleWitness()
        >>> w.summary()["total"]
        0
        """
        items = self._history._items
        total = len(items)
        avg_t = self._history.average_theory_fraction()
        return {
            "total": total,
            "avg_theory_fraction": avg_t,
            "avg_impl_fraction": 1.0 - avg_t,
            "drift_detected": self.drift_detected(),
            "history_length": total,
        }


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------


class SchedulingPrincipleCoordinator:
    """High-level orchestrator for the scheduling pipeline.

    Examples
    --------
    >>> coord = SchedulingPrincipleCoordinator()
    >>> alloc = coord.run(0.3, 0.6, 0.2, 100.0)
    >>> isinstance(alloc, EffortAllocation)
    True
    """

    def __init__(self, config: SchedulingConfig | None = None) -> None:
        self.config: SchedulingConfig = config or SchedulingConfig()
        self.analyzer: SchedulingPrincipleAnalyzer = SchedulingPrincipleAnalyzer()
        self.witness: SchedulingPrincipleWitness = SchedulingPrincipleWitness()
        self._step: int = 0

    def run(
        self,
        growth_signal: float,
        density: float,
        backlog: float,
        budget: float,
    ) -> EffortAllocation:
        """Execute the full scheduling pipeline for one step.

        Parameters
        ----------
        growth_signal:
            Net growth signal from ``s02`` (positive → favour theory).
        density:
            Current obstruction density from the coordinate system.
        backlog:
            Normalised backlog pressure (higher → favour implementation).
        budget:
            Total budget to partition.

        Returns
        -------
        EffortAllocation
            The computed allocation (also recorded in the witness).

        Examples
        --------
        >>> coord = SchedulingPrincipleCoordinator()
        >>> a = coord.run(0.0, 0.5, 0.5, 100.0)
        >>> abs(a.theory_fraction + a.implementation_fraction - 1.0) < 1e-9
        True
        """
        self._step += 1
        sig = self.analyzer.compute_signal(growth_signal, density, backlog, self.config)
        alloc = self.analyzer.allocate(sig, budget, self.config)
        errors = self.analyzer.validate_allocation(alloc, self.config)
        if errors:
            _log.warning("Allocation validation errors: %s", errors)
        self.witness.record(alloc)
        _log.info(
            "SchedulingPrincipleCoordinator step=%d theory=%.2f impl=%.2f",
            self._step,
            alloc.theory_fraction,
            alloc.implementation_fraction,
        )
        return alloc

    def report(self) -> dict[str, Any]:
        """Return a combined report from the witness.

        Returns
        -------
        dict
            Witness summary augmented with config and step count.

        Examples
        --------
        >>> coord = SchedulingPrincipleCoordinator()
        >>> "total" in coord.report()
        True
        """
        summary = self.witness.summary()
        summary.update(
            {
                "step_count": self._step,
                "config_theory_min": self.config.theory_budget_min,
                "config_theory_max": self.config.theory_budget_max,
                "config_impl_min": self.config.implementation_budget_min,
                "config_rebalance_interval": self.config.rebalance_interval,
                "config_signal_sensitivity": self.config.signal_sensitivity,
                "config_equilibrium_tolerance": self.config.equilibrium_tolerance,
            }
        )
        return summary


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=== SchedulingPrinciple smoke test ===")

    cfg = SchedulingConfig(signal_sensitivity=0.6)
    coord = SchedulingPrincipleCoordinator(config=cfg)

    # Simulate a typical scheduling run over several steps
    scenarios = [
        (0.4, 0.8, 0.1, 100.0),   # Strong theory signal
        (0.2, 0.6, 0.3, 100.0),   # Moderate theory
        (0.0, 0.5, 0.5, 100.0),   # Balanced
        (-0.2, 0.3, 0.7, 100.0),  # Moderate implementation
        (-0.4, 0.2, 0.9, 100.0),  # Strong implementation
    ]

    for i, (growth, density, backlog, budget) in enumerate(scenarios):
        alloc = coord.run(growth, density, backlog, budget)
        print(
            f"Step {i+1}: growth={growth:+.1f} density={density:.1f} backlog={backlog:.1f}"
            f" → theory={alloc.theory_fraction:.2%} impl={alloc.implementation_fraction:.2%}"
        )

    print()
    analyzer = SchedulingPrincipleAnalyzer()
    sig = analyzer.compute_signal(0.3, 0.6, 0.2, cfg)
    alloc = analyzer.allocate(sig, 100.0, cfg)
    print(analyzer.explain(alloc, sig))
    errors = analyzer.validate_allocation(alloc, cfg)
    print(f"\nValidation errors: {errors}")

    print()
    report = coord.report()
    print("Report:")
    for k, v in report.items():
        print(f"  {k}: {v}")

    print("\nDrift detected:", coord.witness.drift_detected())
    print("Avg theory fraction:", coord.witness._history.average_theory_fraction())
    print("Smoke test passed.")
