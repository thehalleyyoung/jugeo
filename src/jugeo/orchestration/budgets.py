r"""Resource budget algebra for JuGeo orchestration.

This module implements the budget sub-system described in §4.3 of
``preliminaries/theory2.tex``.  Resource budgets control how much
computation, wall-clock time, and oracle queries each subsystem may
consume.  Budgets prevent runaway costs and ensure fair allocation across
evidence channels:

.. math::

    \mathcal{B} = \bigl\{(d, T_d, S_d, R_d)
                   \;\big|\; d \in \mathcal{D},\;
                   S_d + R_d \le T_d\bigr\}

where:

* :math:`\mathcal{D}` — the finite set of budget dimensions
  (time, solver queries, oracle queries, copilot tokens, …);
* :math:`T_d` — total allocation for dimension *d*;
* :math:`S_d` — amount already spent;
* :math:`R_d` — amount currently reserved but not yet spent.

The constraint :math:`S_d + R_d \le T_d` is the **budget invariant**
and is enforced at every spend / reserve operation by
:class:`BudgetEnforcer`.

Key design points
-----------------

1. Every subsystem—including the copilot/LLM-backed channel—receives an
   explicit :class:`Budget` before it begins work.
2. :class:`BudgetPolicy` captures organisational rules such as
   replenishment cadences, priority overrides, and copilot token
   ceilings.
3. :class:`BudgetAllocator` distributes a global pool across subsystems
   using proportional, priority-based, or adaptive strategies.
4. :class:`BudgetTracker` records every spend event so that
   :class:`BudgetOptimizer` and :class:`BudgetDiagnostics` can reason
   about efficiency after the fact.

Backward compatibility
~~~~~~~~~~~~~~~~~~~~~~

The former :class:`BudgetLedger` API is preserved as an alias at the
bottom of this module.
"""

from __future__ import annotations

import json
import logging
import math
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import (
    Any,
    Callable,
    Final,
    Mapping,
    Sequence,
)

_log = logging.getLogger(__name__)

# ── Cross-subsystem imports (guarded) ─────────────────────────────────────
try:
    from jugeo.evidence.manifests import EvidenceManifest
except Exception:  # pragma: no cover
    EvidenceManifest = None  # type: ignore[assignment,misc]

try:
    from jugeo.solver.z3_session import Z3SessionPool
except Exception:  # pragma: no cover
    Z3SessionPool = None  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_CRITICAL_THRESHOLD: Final[float] = 0.90
_DEFAULT_WARNING_THRESHOLD: Final[float] = 0.75
_DEFAULT_COPILOT_TOKEN_CEILING: Final[int] = 500_000
_HISTORY_WINDOW: Final[int] = 2_000


# ===================================================================== #
# 1. BudgetDimension                                                     #
# ===================================================================== #

class BudgetDimension(str, Enum):
    """Enumeration of tracked resource dimensions.

    Each member corresponds to a measurable axis of resource consumption
    inside the JuGeo runtime.  The copilot token dimension is first-class
    because LLM-backed evidence channels have hard cost ceilings.
    """

    TIME_MS = 'time_ms'
    SOLVER_QUERIES = 'solver_queries'
    ORACLE_QUERIES = 'oracle_queries'
    COPILOT_TOKENS = 'copilot_tokens'
    MEMORY_BYTES = 'memory_bytes'
    NETWORK_CALLS = 'network_calls'
    DESCENT_DEPTH = 'descent_depth'
    FLEET_ROUNDS = 'fleet_rounds'
    HUMAN_REVIEWS = 'human_reviews'

    # -- helpers ----------------------------------------------------------

    @property
    def is_monetary(self) -> bool:
        """Return ``True`` when spending in this dimension has direct cost."""
        return self in {
            BudgetDimension.COPILOT_TOKENS,
            BudgetDimension.ORACLE_QUERIES,
            BudgetDimension.NETWORK_CALLS,
        }

    @property
    def unit_label(self) -> str:
        """Human-readable label for one unit of this dimension."""
        _labels: dict[BudgetDimension, str] = {
            BudgetDimension.TIME_MS: 'ms',
            BudgetDimension.SOLVER_QUERIES: 'queries',
            BudgetDimension.ORACLE_QUERIES: 'queries',
            BudgetDimension.COPILOT_TOKENS: 'tokens',
            BudgetDimension.MEMORY_BYTES: 'bytes',
            BudgetDimension.NETWORK_CALLS: 'calls',
            BudgetDimension.DESCENT_DEPTH: 'levels',
            BudgetDimension.FLEET_ROUNDS: 'rounds',
            BudgetDimension.HUMAN_REVIEWS: 'reviews',
        }
        return _labels.get(self, 'units')

    @classmethod
    def monetary_dimensions(cls) -> tuple[BudgetDimension, ...]:
        """Return the subset of dimensions that carry monetary cost."""
        return tuple(d for d in cls if d.is_monetary)

    @classmethod
    def all_dimensions(cls) -> tuple[BudgetDimension, ...]:
        """Return every dimension in declaration order."""
        return tuple(cls)

    def default_total(self) -> int:
        """Return a sensible default total for this dimension."""
        _defaults: dict[BudgetDimension, int] = {
            BudgetDimension.TIME_MS: 300_000,
            BudgetDimension.SOLVER_QUERIES: 10_000,
            BudgetDimension.ORACLE_QUERIES: 500,
            BudgetDimension.COPILOT_TOKENS: _DEFAULT_COPILOT_TOKEN_CEILING,
            BudgetDimension.MEMORY_BYTES: 1_073_741_824,
            BudgetDimension.NETWORK_CALLS: 1_000,
            BudgetDimension.DESCENT_DEPTH: 64,
            BudgetDimension.FLEET_ROUNDS: 200,
            BudgetDimension.HUMAN_REVIEWS: 10,
        }
        return _defaults.get(self, 1_000)


# ===================================================================== #
# 2. BudgetAllocation                                                    #
# ===================================================================== #

@dataclass(slots=True)
class BudgetAllocation:
    """Allocation state for a single budget dimension.

    The budget invariant ``spent + reserved <= total`` is maintained by
    every mutating operation.  If an operation would violate it the
    method returns ``False`` or raises.
    """

    dimension: BudgetDimension
    total: int
    spent: int = 0
    reserved: int = 0
    _created_at: float = field(default_factory=time.monotonic, repr=False)

    # -- queries ----------------------------------------------------------

    def remaining(self) -> int:
        """Return the amount still available (not spent and not reserved)."""
        return max(0, self.total - self.spent - self.reserved)

    def utilization(self) -> float:
        """Return the fraction of the total that has been spent.

        Returns
        -------
        float
            Value in ``[0.0, 1.0]``.  Does **not** include reserved
            amounts—only realised spending counts.
        """
        if self.total <= 0:
            return 1.0
        return min(1.0, self.spent / self.total)

    def is_exhausted(self) -> bool:
        """Return ``True`` when no further spending is possible."""
        return self.remaining() <= 0

    def is_critical(self, threshold: float = _DEFAULT_CRITICAL_THRESHOLD) -> bool:
        """Return ``True`` when utilization exceeds *threshold*.

        Parameters
        ----------
        threshold:
            Fraction in ``[0.0, 1.0]`` above which the allocation is
            deemed critical.
        """
        return self.utilization() >= threshold

    def project_exhaustion_time(self, rate_per_second: float) -> float | None:
        """Estimate seconds until exhaustion at a constant spending rate.

        Parameters
        ----------
        rate_per_second:
            Current rate of spending in the dimension's native unit.

        Returns
        -------
        float | None
            Estimated seconds until exhaustion, or ``None`` if the
            rate is zero or negative (i.e. never exhausted at this
            rate).
        """
        if rate_per_second <= 0.0:
            return None
        rem = self.remaining()
        if rem <= 0:
            return 0.0
        return rem / rate_per_second

    def effective_total(self) -> int:
        """Return total minus any hard-reserved emergency buffer."""
        return self.total

    def age_seconds(self) -> float:
        """Seconds since this allocation was created."""
        return time.monotonic() - self._created_at

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {
            'dimension': self.dimension.value,
            'total': self.total,
            'spent': self.spent,
            'reserved': self.reserved,
            'remaining': self.remaining(),
            'utilization': round(self.utilization(), 6),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> BudgetAllocation:
        """Deserialise from a dictionary."""
        return cls(
            dimension=BudgetDimension(data['dimension']),
            total=int(data['total']),
            spent=int(data.get('spent', 0)),
            reserved=int(data.get('reserved', 0)),
        )


# ===================================================================== #
# 3. Budget                                                              #
# ===================================================================== #

class Budget:
    """Composite budget spanning multiple :class:`BudgetDimension` axes.

    A *Budget* groups one :class:`BudgetAllocation` per active dimension
    and exposes aggregate queries (``is_any_exhausted``,
    ``most_critical``) and lifecycle operations (``spend``, ``reserve``,
    ``release``).
    """

    def __init__(
        self,
        allocations: Mapping[BudgetDimension, BudgetAllocation] | None = None,
        *,
        label: str = '',
    ) -> None:
        self._allocations: dict[BudgetDimension, BudgetAllocation] = dict(
            allocations or {}
        )
        self.label: str = label

    # -- mutators ---------------------------------------------------------

    def spend(self, dimension: BudgetDimension, amount: int) -> bool:
        """Record spending of *amount* units in *dimension*.

        Returns
        -------
        bool
            ``True`` if the spend was accepted, ``False`` if it would
            violate the budget invariant.
        """
        alloc = self._allocations.get(dimension)
        if alloc is None:
            _log.warning('Spend on untracked dimension %s', dimension.value)
            return False
        if alloc.remaining() < amount:
            _log.debug(
                'Budget %s: spend of %d %s denied (remaining=%d)',
                self.label, amount, dimension.unit_label, alloc.remaining(),
            )
            return False
        alloc.spent += amount
        return True

    def reserve(self, dimension: BudgetDimension, amount: int) -> bool:
        """Reserve *amount* units for future spending.

        Reserved units are not available for other consumers but are not
        yet spent.

        Returns
        -------
        bool
            ``True`` if the reservation was accepted.
        """
        alloc = self._allocations.get(dimension)
        if alloc is None:
            return False
        if alloc.remaining() < amount:
            return False
        alloc.reserved += amount
        return True

    def release(self, dimension: BudgetDimension, amount: int) -> None:
        """Release a previously held reservation."""
        alloc = self._allocations.get(dimension)
        if alloc is None:
            return
        alloc.reserved = max(0, alloc.reserved - amount)

    # -- queries ----------------------------------------------------------

    def remaining_for(self, dimension: BudgetDimension) -> int:
        """Return remaining capacity for *dimension*, or 0 if untracked."""
        alloc = self._allocations.get(dimension)
        return alloc.remaining() if alloc else 0

    def is_any_exhausted(self) -> bool:
        """Return ``True`` if **any** tracked dimension is exhausted."""
        return any(a.is_exhausted() for a in self._allocations.values())

    def most_critical(self) -> BudgetAllocation | None:
        """Return the allocation closest to exhaustion, or ``None``."""
        if not self._allocations:
            return None
        return max(self._allocations.values(), key=lambda a: a.utilization())

    def dimensions(self) -> tuple[BudgetDimension, ...]:
        """Return all tracked dimensions."""
        return tuple(self._allocations)

    # -- snapshot / restore -----------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """Capture the current state as a serialisable dictionary."""
        return {
            'label': self.label,
            'allocations': {
                d.value: a.to_dict() for d, a in self._allocations.items()
            },
        }

    def restore(self, data: Mapping[str, Any]) -> None:
        """Restore state from a snapshot dictionary.

        Parameters
        ----------
        data:
            A dictionary previously produced by :meth:`snapshot`.
        """
        self.label = str(data.get('label', ''))
        raw = data.get('allocations', {})
        self._allocations.clear()
        for _key, alloc_data in raw.items():
            alloc = BudgetAllocation.from_dict(alloc_data)
            self._allocations[alloc.dimension] = alloc

    def serialize(self) -> str:
        """Serialise to a JSON string."""
        return json.dumps(self.snapshot(), indent=2)

    @classmethod
    def deserialize(cls, payload: str) -> Budget:
        """Create a :class:`Budget` from a JSON string."""
        data = json.loads(payload)
        budget = cls(label=data.get('label', ''))
        budget.restore(data)
        return budget

    # ── cross-subsystem integration ─────────────────────────────────────

    def evidence_budget(
        self, manifest: Any | None = None
    ) -> dict[str, Any]:
        """Budget evidence collection based on an EvidenceManifest.

        Queries the evidence subsystem's :class:`EvidenceManifest` to
        determine how much resource each evidence-collection task
        requires, then reserves the corresponding amounts in the
        ``ORACLE_QUERIES`` and ``COPILOT_TOKENS`` dimensions.

        Parameters
        ----------
        manifest
            An :class:`EvidenceManifest` instance.  If ``None`` and the
            evidence subsystem is available, a default manifest is built.

        Returns a summary of reservations made.

        Theory ref: theory2.tex §252 — Evidence Algebra.
        """
        if EvidenceManifest is None:
            return {"status": "unavailable", "reservations": {}}

        manifest = manifest or EvidenceManifest()
        tasks = manifest.required_tasks() if hasattr(manifest, "required_tasks") else []
        reservations: dict[str, int] = {}
        for task in tasks:
            dim_name = getattr(task, "dimension", "oracle_queries")
            amount = getattr(task, "estimated_cost", 1)
            try:
                dim = BudgetDimension(dim_name)
            except ValueError:
                dim = BudgetDimension.ORACLE_QUERIES
            ok = self.reserve(dim, amount)
            if ok:
                reservations[dim_name] = reservations.get(dim_name, 0) + amount

        return {"status": "ok", "reservations": reservations}

    def solver_time_budget(
        self, timeout_ms: int = 30_000
    ) -> dict[str, Any]:
        """Reserve solver time and manage Z3 session pool timeouts.

        Uses :class:`jugeo.solver.z3_session.Z3SessionPool` to query
        the current pool utilisation and then reserves the requested
        ``timeout_ms`` in the ``TIME_MS`` and ``SOLVER_QUERIES``
        budget dimensions.

        Parameters
        ----------
        timeout_ms
            Maximum solver wall-clock time to budget for a single query.

        Returns a summary with pool status and reservation result.

        Theory ref: theory2.tex §4.3 — Resource Budget Algebra.
        """
        pool_info: dict[str, Any] = {}
        if Z3SessionPool is not None:
            pool = Z3SessionPool()
            pool_info = {
                "active_sessions": getattr(pool, "active_count", 0),
                "pool_capacity": getattr(pool, "capacity", 0),
            }

        time_ok = self.reserve(BudgetDimension.TIME_MS, timeout_ms)
        query_ok = self.reserve(BudgetDimension.SOLVER_QUERIES, 1)
        return {
            "status": "ok" if (time_ok and query_ok) else "budget_exceeded",
            "reserved_time_ms": timeout_ms if time_ok else 0,
            "reserved_queries": 1 if query_ok else 0,
            "pool": pool_info,
        }


# ===================================================================== #
# 4. BudgetPolicy                                                       #
# ===================================================================== #

@dataclass(slots=True)
class BudgetPolicy:
    """Configurable policy governing budget lifecycle.

    A policy is created once per orchestration session and consulted by
    the :class:`BudgetAllocator` and :class:`BudgetEnforcer` whenever
    allocation or enforcement decisions are made.

    The ``copilot_token_ceiling`` field is special-cased because
    copilot/LLM-backed channels have strict vendor cost limits that
    must not be exceeded under any circumstances.
    """

    initial_allocations: dict[BudgetDimension, int] = field(default_factory=dict)
    replenishment_rules: dict[BudgetDimension, float] = field(default_factory=dict)
    priority_overrides: dict[str, dict[BudgetDimension, int]] = field(
        default_factory=dict,
    )
    emergency_reserves: dict[BudgetDimension, int] = field(default_factory=dict)
    copilot_token_ceiling: int = _DEFAULT_COPILOT_TOKEN_CEILING
    warning_threshold: float = _DEFAULT_WARNING_THRESHOLD
    critical_threshold: float = _DEFAULT_CRITICAL_THRESHOLD

    # -- builders ---------------------------------------------------------

    def with_defaults(self) -> BudgetPolicy:
        """Return a copy with any unset dimension filled to defaults."""
        filled = dict(self.initial_allocations)
        for dim in BudgetDimension:
            if dim not in filled:
                filled[dim] = dim.default_total()
        # Ensure copilot ceiling is respected
        filled[BudgetDimension.COPILOT_TOKENS] = min(
            filled.get(BudgetDimension.COPILOT_TOKENS, self.copilot_token_ceiling),
            self.copilot_token_ceiling,
        )
        return BudgetPolicy(
            initial_allocations=filled,
            replenishment_rules=dict(self.replenishment_rules),
            priority_overrides=dict(self.priority_overrides),
            emergency_reserves=dict(self.emergency_reserves),
            copilot_token_ceiling=self.copilot_token_ceiling,
            warning_threshold=self.warning_threshold,
            critical_threshold=self.critical_threshold,
        )

    def allocation_for(self, dimension: BudgetDimension) -> int:
        """Return the configured initial allocation for *dimension*."""
        return self.initial_allocations.get(dimension, dimension.default_total())

    def replenishment_rate(self, dimension: BudgetDimension) -> float:
        """Return per-second replenishment rate for *dimension* (0 if none)."""
        return self.replenishment_rules.get(dimension, 0.0)

    def emergency_reserve_for(self, dimension: BudgetDimension) -> int:
        """Return the emergency reserve held back for *dimension*."""
        return self.emergency_reserves.get(dimension, 0)

    def effective_total(self, dimension: BudgetDimension) -> int:
        """Return allocation minus emergency reserve."""
        base = self.allocation_for(dimension)
        reserve = self.emergency_reserve_for(dimension)
        return max(0, base - reserve)

    def override_for_subsystem(
        self,
        subsystem: str,
        dimension: BudgetDimension,
    ) -> int | None:
        """Return a priority override for *subsystem*, or ``None``."""
        overrides = self.priority_overrides.get(subsystem, {})
        return overrides.get(dimension)

    def to_dict(self) -> dict[str, Any]:
        """Serialise the policy to a plain dictionary."""
        return {
            'initial_allocations': {
                d.value: v for d, v in self.initial_allocations.items()
            },
            'replenishment_rules': {
                d.value: v for d, v in self.replenishment_rules.items()
            },
            'priority_overrides': {
                k: {d.value: v for d, v in inner.items()}
                for k, inner in self.priority_overrides.items()
            },
            'emergency_reserves': {
                d.value: v for d, v in self.emergency_reserves.items()
            },
            'copilot_token_ceiling': self.copilot_token_ceiling,
            'warning_threshold': self.warning_threshold,
            'critical_threshold': self.critical_threshold,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> BudgetPolicy:
        """Deserialise from a dictionary."""
        def _parse_dim_dict(raw: Mapping[str, Any]) -> dict[BudgetDimension, Any]:
            return {BudgetDimension(k): v for k, v in raw.items()}

        overrides: dict[str, dict[BudgetDimension, int]] = {}
        for sub, inner in data.get('priority_overrides', {}).items():
            overrides[sub] = _parse_dim_dict(inner)

        return cls(
            initial_allocations=_parse_dim_dict(data.get('initial_allocations', {})),
            replenishment_rules=_parse_dim_dict(data.get('replenishment_rules', {})),
            priority_overrides=overrides,
            emergency_reserves=_parse_dim_dict(data.get('emergency_reserves', {})),
            copilot_token_ceiling=int(
                data.get('copilot_token_ceiling', _DEFAULT_COPILOT_TOKEN_CEILING),
            ),
            warning_threshold=float(data.get('warning_threshold', _DEFAULT_WARNING_THRESHOLD)),
            critical_threshold=float(data.get('critical_threshold', _DEFAULT_CRITICAL_THRESHOLD)),
        )


# ===================================================================== #
# 5. BudgetAllocator                                                     #
# ===================================================================== #

class BudgetAllocator:
    """Distributes a global budget pool across named subsystems.

    Three allocation strategies are provided:

    * **proportional** — divide evenly (or by weight) across subsystems;
    * **priority** — honour :class:`BudgetPolicy` overrides first, then
      split the remainder;
    * **adaptive** — adjust shares based on historical spending rates
      reported by :class:`BudgetTracker`.
    """

    def __init__(self, policy: BudgetPolicy) -> None:
        self._policy = policy.with_defaults()
        self._budgets: dict[str, Budget] = {}

    # -- core API ---------------------------------------------------------

    def allocate(self, subsystem: str) -> Budget:
        """Create and return a fresh :class:`Budget` for *subsystem*.

        If *subsystem* already has a budget, returns the existing one.
        """
        if subsystem in self._budgets:
            return self._budgets[subsystem]
        allocs: dict[BudgetDimension, BudgetAllocation] = {}
        for dim in BudgetDimension:
            override = self._policy.override_for_subsystem(subsystem, dim)
            total = override if override is not None else self._policy.effective_total(dim)
            allocs[dim] = BudgetAllocation(dimension=dim, total=total)
        budget = Budget(allocs, label=subsystem)
        self._budgets[subsystem] = budget
        _log.info('Allocated budget for subsystem %r', subsystem)
        return budget

    def reallocate(
        self,
        source: str,
        target: str,
        dimension: BudgetDimension,
        amount: int,
    ) -> bool:
        """Transfer *amount* from *source* to *target* in *dimension*.

        Returns ``True`` if the transfer succeeded.
        """
        src = self._budgets.get(source)
        tgt = self._budgets.get(target)
        if src is None or tgt is None:
            _log.warning('Reallocate: unknown subsystem')
            return False
        src_alloc = src._allocations.get(dimension)
        tgt_alloc = tgt._allocations.get(dimension)
        if src_alloc is None or tgt_alloc is None:
            return False
        if src_alloc.remaining() < amount:
            return False
        src_alloc.total -= amount
        tgt_alloc.total += amount
        _log.info(
            'Reallocated %d %s from %s → %s',
            amount, dimension.unit_label, source, target,
        )
        return True

    def rebalance(self, subsystems: Sequence[str] | None = None) -> None:
        """Re-distribute unused capacity evenly across *subsystems*.

        If *subsystems* is ``None``, all registered subsystems are
        considered.
        """
        names = list(subsystems or self._budgets.keys())
        if len(names) < 2:
            return
        for dim in BudgetDimension:
            total_remaining = sum(
                self._budgets[n]._allocations[dim].remaining()
                for n in names
                if n in self._budgets and dim in self._budgets[n]._allocations
            )
            share = total_remaining // len(names)
            for name in names:
                b = self._budgets.get(name)
                if b is None:
                    continue
                alloc = b._allocations.get(dim)
                if alloc is None:
                    continue
                alloc.total = alloc.spent + alloc.reserved + share

    def proportional_allocation(
        self,
        subsystems: Sequence[str],
        dimension: BudgetDimension,
        weights: Sequence[float] | None = None,
    ) -> dict[str, int]:
        """Compute a proportional split of *dimension*'s total.

        Parameters
        ----------
        subsystems:
            Names of subsystems to receive shares.
        dimension:
            The dimension to split.
        weights:
            Optional relative weights; defaults to equal.

        Returns
        -------
        dict[str, int]
            Mapping from subsystem name to allocated amount.
        """
        total = self._policy.effective_total(dimension)
        if weights is None:
            weights = [1.0] * len(subsystems)
        weight_sum = sum(weights)
        if weight_sum <= 0:
            weight_sum = 1.0
        result: dict[str, int] = {}
        for name, w in zip(subsystems, weights):
            result[name] = int(total * (w / weight_sum))
        return result

    def priority_allocation(
        self,
        subsystems: Sequence[str],
        dimension: BudgetDimension,
    ) -> dict[str, int]:
        """Allocate *dimension* budget honouring policy overrides first.

        Subsystems with explicit overrides receive their full amount;
        the remainder is split evenly among the rest.
        """
        total = self._policy.effective_total(dimension)
        result: dict[str, int] = {}
        remaining = total
        unoverridden: list[str] = []
        for name in subsystems:
            override = self._policy.override_for_subsystem(name, dimension)
            if override is not None:
                share = min(override, remaining)
                result[name] = share
                remaining -= share
            else:
                unoverridden.append(name)
        if unoverridden:
            share = remaining // len(unoverridden)
            for name in unoverridden:
                result[name] = share
        return result

    def adaptive_allocation(
        self,
        subsystems: Sequence[str],
        dimension: BudgetDimension,
        spending_rates: Mapping[str, float],
    ) -> dict[str, int]:
        """Allocate *dimension* proportionally to spending rates.

        Subsystems that consume faster receive a larger share so that
        all subsystems are projected to run out at roughly the same
        time.
        """
        total = self._policy.effective_total(dimension)
        rate_sum = sum(spending_rates.get(n, 0.0) for n in subsystems) or 1.0
        result: dict[str, int] = {}
        for name in subsystems:
            rate = spending_rates.get(name, 0.0)
            result[name] = int(total * (rate / rate_sum)) if rate_sum > 0 else 0
        return result

    def registered_subsystems(self) -> tuple[str, ...]:
        """Return the names of all subsystems with allocated budgets."""
        return tuple(self._budgets)

    def budget_for(self, subsystem: str) -> Budget | None:
        """Return the budget for *subsystem*, or ``None``."""
        return self._budgets.get(subsystem)

    # ── cross-subsystem integration ─────────────────────────────────────

    def evidence_budget(
        self, subsystem: str, manifest: Any | None = None
    ) -> dict[str, Any]:
        """Budget evidence collection for *subsystem* via EvidenceManifest.

        Delegates to :meth:`Budget.evidence_budget` on the appropriate
        subsystem budget, creating the budget if it does not yet exist.

        Theory ref: theory2.tex §252 — Evidence Algebra.
        """
        budget = self._budgets.get(subsystem)
        if budget is None:
            budget = Budget(label=subsystem)
            self._budgets[subsystem] = budget
        return budget.evidence_budget(manifest)

    def solver_time_budget(
        self, subsystem: str, timeout_ms: int = 30_000
    ) -> dict[str, Any]:
        """Reserve solver time for *subsystem* via Z3SessionPool.

        Delegates to :meth:`Budget.solver_time_budget` on the appropriate
        subsystem budget, creating the budget if it does not yet exist.

        Theory ref: theory2.tex §4.3 — Resource Budget Algebra.
        """
        budget = self._budgets.get(subsystem)
        if budget is None:
            budget = Budget(label=subsystem)
            self._budgets[subsystem] = budget
        return budget.solver_time_budget(timeout_ms)


# ===================================================================== #
# 6. BudgetTracker                                                       #
# ===================================================================== #

@dataclass(slots=True)
class _SpendEvent:
    """Internal record of a single spend event."""
    timestamp: float
    dimension: BudgetDimension
    amount: int
    channel: str


class BudgetTracker:
    """Records every spend, reservation, and release event for analysis.

    The tracker maintains a bounded ring-buffer of recent events (capped
    at ``_HISTORY_WINDOW``) so memory usage stays constant even in
    long-running sessions.
    """

    def __init__(self, window: int = _HISTORY_WINDOW) -> None:
        self._events: deque[_SpendEvent] = deque(maxlen=window)
        self._totals: dict[BudgetDimension, int] = defaultdict(int)
        self._by_channel: dict[str, dict[BudgetDimension, int]] = defaultdict(
            lambda: defaultdict(int),
        )
        self._reservation_total: dict[BudgetDimension, int] = defaultdict(int)
        self._release_total: dict[BudgetDimension, int] = defaultdict(int)
        self._start_time: float = time.monotonic()

    # -- recording --------------------------------------------------------

    def record_spend(
        self,
        dimension: BudgetDimension,
        amount: int,
        *,
        channel: str = 'unknown',
    ) -> None:
        """Record that *amount* units of *dimension* were spent.

        Parameters
        ----------
        dimension:
            The budget dimension being consumed.
        amount:
            Number of units consumed.
        channel:
            Evidence channel or subsystem responsible (e.g. ``'copilot'``,
            ``'solver'``).
        """
        evt = _SpendEvent(
            timestamp=time.monotonic(),
            dimension=dimension,
            amount=amount,
            channel=channel,
        )
        self._events.append(evt)
        self._totals[dimension] += amount
        self._by_channel[channel][dimension] += amount

    def record_reservation(
        self,
        dimension: BudgetDimension,
        amount: int,
    ) -> None:
        """Record a reservation of *amount* in *dimension*."""
        self._reservation_total[dimension] += amount

    def record_release(
        self,
        dimension: BudgetDimension,
        amount: int,
    ) -> None:
        """Record the release of a prior reservation."""
        self._release_total[dimension] += amount

    # -- analytics --------------------------------------------------------

    def spending_rate(
        self,
        dimension: BudgetDimension,
        window_seconds: float = 60.0,
    ) -> float:
        """Return the per-second spending rate over the last *window_seconds*.

        Only events within the time window are considered so the rate
        reflects recent behaviour, not lifetime averages.
        """
        cutoff = time.monotonic() - window_seconds
        total = sum(
            e.amount for e in self._events
            if e.dimension is dimension and e.timestamp >= cutoff
        )
        return total / window_seconds if window_seconds > 0 else 0.0

    def projected_depletion(
        self,
        dimension: BudgetDimension,
        remaining: int,
        window_seconds: float = 60.0,
    ) -> float | None:
        """Estimate seconds until *remaining* units are consumed.

        Returns ``None`` when the current rate is zero.
        """
        rate = self.spending_rate(dimension, window_seconds)
        if rate <= 0:
            return None
        return remaining / rate

    def spending_by_channel(
        self,
        dimension: BudgetDimension | None = None,
    ) -> dict[str, int]:
        """Return aggregate spending grouped by channel.

        Parameters
        ----------
        dimension:
            If given, only spending in this dimension is returned.
            Otherwise all dimensions are summed.
        """
        result: dict[str, int] = {}
        for ch, dims in self._by_channel.items():
            if dimension is not None:
                result[ch] = dims.get(dimension, 0)
            else:
                result[ch] = sum(dims.values())
        return result

    def total_spent(self, dimension: BudgetDimension) -> int:
        """Return the total amount spent in *dimension* since tracking began."""
        return self._totals.get(dimension, 0)

    def elapsed_seconds(self) -> float:
        """Wall-clock seconds since the tracker was created."""
        return time.monotonic() - self._start_time

    def event_count(self) -> int:
        """Total number of spend events recorded (may be capped by window)."""
        return len(self._events)


# ===================================================================== #
# 7. BudgetEnforcer                                                      #
# ===================================================================== #

@dataclass(slots=True)
class _EnforcementResult:
    """Outcome of a budget enforcement check."""
    allowed: bool
    reason: str = ''
    warning: str = ''


class BudgetEnforcer:
    """Enforces budget limits before spending occurs.

    Every call site that consumes resources should call
    :meth:`check_before_spend` (or the convenience wrapper
    :meth:`enforce`) **before** committing a spend.  The enforcer
    consults the :class:`BudgetPolicy` to decide whether to allow,
    warn, or deny the request.
    """

    def __init__(
        self,
        policy: BudgetPolicy,
        tracker: BudgetTracker | None = None,
    ) -> None:
        self._policy = policy
        self._tracker = tracker
        self._override_active: bool = False
        self._denial_log: list[dict[str, Any]] = []

    # -- checks -----------------------------------------------------------

    def check_before_spend(
        self,
        budget: Budget,
        dimension: BudgetDimension,
        amount: int,
    ) -> _EnforcementResult:
        """Determine whether a spend of *amount* in *dimension* is allowed.

        Returns
        -------
        _EnforcementResult
            The enforcement decision including any warning text.
        """
        if self._override_active:
            return _EnforcementResult(allowed=True, reason='emergency-override')

        alloc = budget._allocations.get(dimension)
        if alloc is None:
            return _EnforcementResult(
                allowed=False,
                reason=f'dimension {dimension.value} not tracked',
            )

        # Hard denial
        if alloc.remaining() < amount:
            result = _EnforcementResult(
                allowed=False,
                reason=(
                    f'{dimension.value}: requested {amount} but only '
                    f'{alloc.remaining()} remaining'
                ),
            )
            self._denial_log.append({
                'ts': time.monotonic(),
                'dimension': dimension.value,
                'requested': amount,
                'remaining': alloc.remaining(),
            })
            return result

        # Soft warning
        future_util = (alloc.spent + amount) / alloc.total if alloc.total > 0 else 1.0
        warning = ''
        if future_util >= self._policy.critical_threshold:
            warning = (
                f'{dimension.value}: spending {amount} will push utilization '
                f'to {future_util:.1%} (critical threshold '
                f'{self._policy.critical_threshold:.0%})'
            )
        elif future_util >= self._policy.warning_threshold:
            warning = (
                f'{dimension.value}: utilization will reach {future_util:.1%}'
            )

        return _EnforcementResult(allowed=True, warning=warning)

    def enforce(
        self,
        budget: Budget,
        dimension: BudgetDimension,
        amount: int,
    ) -> bool:
        """Check and, if allowed, perform the spend.

        A convenience wrapper that combines :meth:`check_before_spend`
        with :meth:`Budget.spend`.  Logs a warning when the soft
        threshold is reached.

        Returns
        -------
        bool
            ``True`` if the spend was committed.
        """
        result = self.check_before_spend(budget, dimension, amount)
        if not result.allowed:
            _log.warning('Budget enforcement denied: %s', result.reason)
            return False
        if result.warning:
            _log.warning('Budget warning: %s', result.warning)
        ok = budget.spend(dimension, amount)
        if ok and self._tracker is not None:
            self._tracker.record_spend(dimension, amount)
        return ok

    def deny_if_exhausted(
        self,
        budget: Budget,
        dimension: BudgetDimension,
    ) -> bool:
        """Return ``True`` (deny) if *dimension* is exhausted.

        This is a quick guard intended for hot paths where the full
        :meth:`check_before_spend` protocol is too verbose.
        """
        alloc = budget._allocations.get(dimension)
        if alloc is None:
            return True
        return alloc.is_exhausted()

    def allow_with_warning(
        self,
        budget: Budget,
        dimension: BudgetDimension,
        amount: int,
    ) -> tuple[bool, str]:
        """Return ``(allowed, warning_message)`` without actually spending.

        Useful for UI surfaces that want to present a warning before the
        user commits to a costly operation.
        """
        result = self.check_before_spend(budget, dimension, amount)
        return result.allowed, result.warning or result.reason

    def emergency_override(self, *, activate: bool) -> None:
        """Activate or deactivate the emergency override.

        While the override is active **all** enforcement checks pass
        regardless of budget state.  This is intended for disaster
        recovery scenarios only.

        Parameters
        ----------
        activate:
            ``True`` to bypass enforcement, ``False`` to restore normal
            operation.
        """
        self._override_active = activate
        _log.warning(
            'Emergency override %s',
            'ACTIVATED' if activate else 'deactivated',
        )

    def denial_count(self) -> int:
        """Return the number of denials recorded since creation."""
        return len(self._denial_log)

    def recent_denials(self, n: int = 10) -> list[dict[str, Any]]:
        """Return the most recent *n* denial records."""
        return list(self._denial_log[-n:])


# ===================================================================== #
# 8. BudgetOptimizer                                                     #
# ===================================================================== #

class BudgetOptimizer:
    """Suggests allocation improvements based on historical spending.

    The optimizer is a *read-only* advisory component: it never mutates
    budgets directly but returns recommendations that the orchestrator
    may choose to apply.
    """

    def __init__(
        self,
        tracker: BudgetTracker,
        policy: BudgetPolicy,
    ) -> None:
        self._tracker = tracker
        self._policy = policy

    def optimize(
        self,
        budgets: Mapping[str, Budget],
    ) -> list[dict[str, Any]]:
        """Return a list of optimisation recommendations.

        Each recommendation is a dict with keys ``subsystem``,
        ``dimension``, ``action`` (``'increase'`` | ``'decrease'``),
        and ``suggested_delta``.
        """
        recs: list[dict[str, Any]] = []
        for name, budget in budgets.items():
            for dim, alloc in budget._allocations.items():
                util = alloc.utilization()
                if util >= 0.95 and alloc.remaining() < alloc.total * 0.02:
                    recs.append({
                        'subsystem': name,
                        'dimension': dim.value,
                        'action': 'increase',
                        'suggested_delta': int(alloc.total * 0.25),
                        'reason': f'utilization at {util:.0%}, near exhaustion',
                    })
                elif util < 0.10 and self._tracker.elapsed_seconds() > 60:
                    recs.append({
                        'subsystem': name,
                        'dimension': dim.value,
                        'action': 'decrease',
                        'suggested_delta': int(alloc.total * 0.50),
                        'reason': f'utilization only {util:.0%} after '
                                  f'{self._tracker.elapsed_seconds():.0f}s',
                    })
        return recs

    def analyze_roi(
        self,
        budgets: Mapping[str, Budget],
    ) -> dict[str, dict[str, float]]:
        """Compute return-on-investment proxies per subsystem.

        ROI is approximated as the ratio of spending to the total
        allocation—a higher ROI means the subsystem is making good use
        of its budget.  For dimensions with monetary cost (copilot
        tokens, oracle queries, network calls) the ROI is weighted
        more heavily.
        """
        roi: dict[str, dict[str, float]] = {}
        for name, budget in budgets.items():
            subsys_roi: dict[str, float] = {}
            for dim, alloc in budget._allocations.items():
                weight = 2.0 if dim.is_monetary else 1.0
                subsys_roi[dim.value] = alloc.utilization() * weight
            roi[name] = subsys_roi
        return roi

    def suggest_rebalance(
        self,
        budgets: Mapping[str, Budget],
    ) -> list[dict[str, Any]]:
        """Suggest transfers from under-utilised to over-utilised subsystems.

        Returns a list of transfer suggestions, each with ``source``,
        ``target``, ``dimension``, and ``amount``.
        """
        suggestions: list[dict[str, Any]] = []
        for dim in BudgetDimension:
            over: list[tuple[str, BudgetAllocation]] = []
            under: list[tuple[str, BudgetAllocation]] = []
            for name, budget in budgets.items():
                alloc = budget._allocations.get(dim)
                if alloc is None:
                    continue
                if alloc.utilization() >= self._policy.critical_threshold:
                    over.append((name, alloc))
                elif alloc.utilization() < 0.25:
                    under.append((name, alloc))
            for u_name, u_alloc in under:
                for o_name, _o_alloc in over:
                    transferable = u_alloc.remaining() // 2
                    if transferable > 0:
                        suggestions.append({
                            'source': u_name,
                            'target': o_name,
                            'dimension': dim.value,
                            'amount': transferable,
                        })
        return suggestions

    def copilot_budget_advice(
        self,
        budget: Budget,
    ) -> dict[str, Any]:
        """Return advisory information specific to the copilot token budget.

        This method analyses the copilot-tokens dimension in isolation
        and produces targeted advice for controlling LLM spend.
        """
        alloc = budget._allocations.get(BudgetDimension.COPILOT_TOKENS)
        if alloc is None:
            return {'status': 'no_copilot_allocation'}
        rate = self._tracker.spending_rate(BudgetDimension.COPILOT_TOKENS)
        projected = self._tracker.projected_depletion(
            BudgetDimension.COPILOT_TOKENS,
            alloc.remaining(),
        )
        return {
            'status': 'critical' if alloc.is_critical() else 'ok',
            'tokens_remaining': alloc.remaining(),
            'tokens_spent': alloc.spent,
            'utilization': round(alloc.utilization(), 4),
            'rate_per_second': round(rate, 2),
            'projected_depletion_seconds': (
                round(projected, 1) if projected is not None else None
            ),
            'ceiling': self._policy.copilot_token_ceiling,
        }

    def historical_analysis(
        self,
        window_seconds: float = 300.0,
    ) -> dict[str, Any]:
        """Summarise spending trends over the last *window_seconds*.

        Useful for the diagnostics UI and for feeding back into
        :meth:`adaptive_allocation` in :class:`BudgetAllocator`.
        """
        analysis: dict[str, Any] = {'window_seconds': window_seconds}
        for dim in BudgetDimension:
            rate = self._tracker.spending_rate(dim, window_seconds)
            total = self._tracker.total_spent(dim)
            analysis[dim.value] = {
                'total_spent': total,
                'rate_per_second': round(rate, 4),
            }
        return analysis

    def efficiency_score(self, budget: Budget) -> float:
        """Return a scalar efficiency score in ``[0.0, 1.0]``.

        The score combines utilisation across all dimensions, weighting
        monetary dimensions double.
        """
        scores: list[float] = []
        weights: list[float] = []
        for dim, alloc in budget._allocations.items():
            w = 2.0 if dim.is_monetary else 1.0
            scores.append(alloc.utilization())
            weights.append(w)
        if not scores:
            return 0.0
        weighted = sum(s * w for s, w in zip(scores, weights))
        return weighted / sum(weights)


# ===================================================================== #
# 9. BudgetHistory                                                       #
# ===================================================================== #

@dataclass(slots=True)
class _HistorySnapshot:
    """A point-in-time capture of all allocation states."""
    timestamp: float
    allocations: dict[str, dict[str, Any]]
    alerts: list[str]


class BudgetHistory:
    """Maintains a time-series of budget snapshots for post-hoc analysis.

    Snapshots are recorded at caller-controlled intervals (typically
    once per orchestration round) and stored in a bounded deque.
    """

    def __init__(self, max_snapshots: int = 1_000) -> None:
        self._snapshots: deque[_HistorySnapshot] = deque(maxlen=max_snapshots)

    def record(
        self,
        budgets: Mapping[str, Budget],
        alerts: Sequence[str] | None = None,
    ) -> None:
        """Capture the current state of all *budgets*.

        Parameters
        ----------
        budgets:
            Subsystem-name → :class:`Budget` mapping.
        alerts:
            Optional list of alert messages to attach to this snapshot.
        """
        allocs: dict[str, dict[str, Any]] = {}
        for name, budget in budgets.items():
            allocs[name] = budget.snapshot()
        snap = _HistorySnapshot(
            timestamp=time.monotonic(),
            allocations=allocs,
            alerts=list(alerts or []),
        )
        self._snapshots.append(snap)

    def spending_timeline(
        self,
        subsystem: str,
        dimension: BudgetDimension,
    ) -> list[tuple[float, int]]:
        """Return ``(timestamp, spent)`` pairs for a single dimension."""
        result: list[tuple[float, int]] = []
        for snap in self._snapshots:
            sub_data = snap.allocations.get(subsystem, {})
            allocs_raw = sub_data.get('allocations', {})
            dim_data = allocs_raw.get(dimension.value, {})
            if dim_data:
                result.append((snap.timestamp, dim_data.get('spent', 0)))
        return result

    def utilization_timeline(
        self,
        subsystem: str,
        dimension: BudgetDimension,
    ) -> list[tuple[float, float]]:
        """Return ``(timestamp, utilization)`` pairs."""
        result: list[tuple[float, float]] = []
        for snap in self._snapshots:
            sub_data = snap.allocations.get(subsystem, {})
            allocs_raw = sub_data.get('allocations', {})
            dim_data = allocs_raw.get(dimension.value, {})
            if dim_data:
                result.append((
                    snap.timestamp,
                    dim_data.get('utilization', 0.0),
                ))
        return result

    def alerts_timeline(self) -> list[tuple[float, list[str]]]:
        """Return ``(timestamp, alerts)`` for every snapshot with alerts."""
        return [
            (s.timestamp, s.alerts)
            for s in self._snapshots
            if s.alerts
        ]

    def trend_analysis(
        self,
        subsystem: str,
        dimension: BudgetDimension,
    ) -> dict[str, Any]:
        """Compute a simple linear trend over the recorded timeline.

        Returns
        -------
        dict[str, Any]
            Keys: ``slope`` (units/second), ``intercept``, ``r_squared``,
            ``data_points``.
        """
        timeline = self.spending_timeline(subsystem, dimension)
        n = len(timeline)
        if n < 2:
            return {'slope': 0.0, 'intercept': 0.0, 'r_squared': 0.0, 'data_points': n}

        t0 = timeline[0][0]
        xs = [t - t0 for t, _ in timeline]
        ys = [float(v) for _, v in timeline]
        mean_x = sum(xs) / n
        mean_y = sum(ys) / n
        ss_xx = sum((x - mean_x) ** 2 for x in xs) or 1e-12
        ss_xy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
        slope = ss_xy / ss_xx
        intercept = mean_y - slope * mean_x
        ss_yy = sum((y - mean_y) ** 2 for y in ys) or 1e-12
        r_squared = (ss_xy ** 2) / (ss_xx * ss_yy)
        return {
            'slope': round(slope, 6),
            'intercept': round(intercept, 4),
            'r_squared': round(r_squared, 6),
            'data_points': n,
        }

    def snapshot_count(self) -> int:
        """Number of snapshots currently retained."""
        return len(self._snapshots)

    def latest(self) -> _HistorySnapshot | None:
        """Return the most recent snapshot, or ``None``."""
        return self._snapshots[-1] if self._snapshots else None


# ===================================================================== #
# 10. BudgetAlert                                                        #
# ===================================================================== #

@dataclass(frozen=True, slots=True)
class AlertRecord:
    """Immutable record of a single budget alert."""
    alert_id: str
    severity: str
    dimension: BudgetDimension
    subsystem: str
    message: str
    timestamp: float


class BudgetAlert:
    """Generates alerts when budget thresholds are breached.

    The alert system is pull-based: the orchestrator calls
    :meth:`check_thresholds` periodically and receives any new alerts.
    """

    def __init__(
        self,
        policy: BudgetPolicy,
        tracker: BudgetTracker | None = None,
    ) -> None:
        self._policy = policy
        self._tracker = tracker
        self._fired: list[AlertRecord] = []
        self._suppressed: set[tuple[str, str]] = set()

    def check_thresholds(
        self,
        budgets: Mapping[str, Budget],
    ) -> list[AlertRecord]:
        """Check all budgets against policy thresholds.

        Returns a list of **new** alerts generated during this call.
        """
        new_alerts: list[AlertRecord] = []
        for name, budget in budgets.items():
            for dim, alloc in budget._allocations.items():
                key = (name, dim.value)
                if key in self._suppressed:
                    continue
                if alloc.is_exhausted():
                    alert = self._make_alert(
                        'critical', dim, name,
                        f'{name}/{dim.value}: EXHAUSTED — no capacity remains',
                    )
                    new_alerts.append(alert)
                    self._suppressed.add(key)
                elif alloc.is_critical(self._policy.critical_threshold):
                    alert = self._make_alert(
                        'warning', dim, name,
                        f'{name}/{dim.value}: utilization {alloc.utilization():.0%} '
                        f'exceeds critical threshold',
                    )
                    new_alerts.append(alert)
                    self._suppressed.add(key)
        self._fired.extend(new_alerts)
        return new_alerts

    def alert_on_critical(
        self,
        budget: Budget,
        subsystem: str,
    ) -> list[AlertRecord]:
        """Return alerts only for dimensions currently in critical state."""
        alerts: list[AlertRecord] = []
        for dim, alloc in budget._allocations.items():
            if alloc.is_critical(self._policy.critical_threshold):
                alerts.append(self._make_alert(
                    'critical', dim, subsystem,
                    f'{subsystem}/{dim.value}: critical at {alloc.utilization():.0%}',
                ))
        return alerts

    def alert_on_anomaly(
        self,
        budget: Budget,
        subsystem: str,
        expected_rates: Mapping[BudgetDimension, float] | None = None,
    ) -> list[AlertRecord]:
        """Detect anomalous spending relative to *expected_rates*.

        If the actual rate exceeds 3× the expected rate an anomaly
        alert is generated.
        """
        if self._tracker is None or expected_rates is None:
            return []
        alerts: list[AlertRecord] = []
        for dim, expected in expected_rates.items():
            actual = self._tracker.spending_rate(dim)
            if expected > 0 and actual > expected * 3.0:
                alerts.append(self._make_alert(
                    'anomaly', dim, subsystem,
                    f'{subsystem}/{dim.value}: spending rate {actual:.2f}/s '
                    f'is {actual / expected:.1f}× expected ({expected:.2f}/s)',
                ))
        return alerts

    def alert_on_projected_exhaustion(
        self,
        budget: Budget,
        subsystem: str,
        horizon_seconds: float = 300.0,
    ) -> list[AlertRecord]:
        """Alert if any dimension is projected to exhaust within *horizon_seconds*.

        Uses the :class:`BudgetTracker` spending rate to project forward.
        """
        if self._tracker is None:
            return []
        alerts: list[AlertRecord] = []
        for dim, alloc in budget._allocations.items():
            proj = self._tracker.projected_depletion(dim, alloc.remaining())
            if proj is not None and proj < horizon_seconds:
                alerts.append(self._make_alert(
                    'warning', dim, subsystem,
                    f'{subsystem}/{dim.value}: projected exhaustion in '
                    f'{proj:.0f}s (horizon={horizon_seconds:.0f}s)',
                ))
        return alerts

    def all_alerts(self) -> list[AlertRecord]:
        """Return every alert fired since creation."""
        return list(self._fired)

    def clear_suppressions(self) -> None:
        """Reset suppression state so alerts may fire again."""
        self._suppressed.clear()

    # -- internals --------------------------------------------------------

    def _make_alert(
        self,
        severity: str,
        dimension: BudgetDimension,
        subsystem: str,
        message: str,
    ) -> AlertRecord:
        return AlertRecord(
            alert_id=uuid.uuid4().hex[:12],
            severity=severity,
            dimension=dimension,
            subsystem=subsystem,
            message=message,
            timestamp=time.monotonic(),
        )


# ===================================================================== #
# 11. BudgetSerializer                                                   #
# ===================================================================== #

class BudgetSerializer:
    """JSON serialisation for all budget types.

    This is the canonical serialisation layer: every persistent or
    network-transmitted budget payload goes through this class.
    """

    @staticmethod
    def serialize_allocation(alloc: BudgetAllocation) -> dict[str, Any]:
        """Serialise a single :class:`BudgetAllocation`."""
        return alloc.to_dict()

    @staticmethod
    def deserialize_allocation(data: Mapping[str, Any]) -> BudgetAllocation:
        """Deserialise a single :class:`BudgetAllocation`."""
        return BudgetAllocation.from_dict(data)

    @staticmethod
    def serialize_budget(budget: Budget) -> dict[str, Any]:
        """Serialise a :class:`Budget` to a plain dict."""
        return budget.snapshot()

    @staticmethod
    def deserialize_budget(data: Mapping[str, Any]) -> Budget:
        """Deserialise a :class:`Budget` from a plain dict."""
        b = Budget(label=data.get('label', ''))
        b.restore(data)
        return b

    @staticmethod
    def serialize_policy(policy: BudgetPolicy) -> dict[str, Any]:
        """Serialise a :class:`BudgetPolicy`."""
        return policy.to_dict()

    @staticmethod
    def deserialize_policy(data: Mapping[str, Any]) -> BudgetPolicy:
        """Deserialise a :class:`BudgetPolicy`."""
        return BudgetPolicy.from_dict(data)

    @classmethod
    def to_json(cls, obj: Budget | BudgetPolicy | BudgetAllocation) -> str:
        """Serialise any supported budget type to a JSON string.

        Parameters
        ----------
        obj:
            A :class:`Budget`, :class:`BudgetPolicy`, or
            :class:`BudgetAllocation`.

        Returns
        -------
        str
            JSON-encoded string.

        Raises
        ------
        TypeError
            If *obj* is not a supported type.
        """
        if isinstance(obj, Budget):
            payload = cls.serialize_budget(obj)
        elif isinstance(obj, BudgetPolicy):
            payload = cls.serialize_policy(obj)
        elif isinstance(obj, BudgetAllocation):
            payload = cls.serialize_allocation(obj)
        else:
            raise TypeError(f'Unsupported type: {type(obj).__name__}')
        return json.dumps(payload, indent=2)

    @classmethod
    def budget_from_json(cls, payload: str) -> Budget:
        """Deserialise a :class:`Budget` from a JSON string."""
        return cls.deserialize_budget(json.loads(payload))

    @classmethod
    def policy_from_json(cls, payload: str) -> BudgetPolicy:
        """Deserialise a :class:`BudgetPolicy` from a JSON string."""
        return cls.deserialize_policy(json.loads(payload))

    @staticmethod
    def serialize_alert(alert: AlertRecord) -> dict[str, Any]:
        """Serialise an :class:`AlertRecord`."""
        return {
            'alert_id': alert.alert_id,
            'severity': alert.severity,
            'dimension': alert.dimension.value,
            'subsystem': alert.subsystem,
            'message': alert.message,
            'timestamp': alert.timestamp,
        }

    @staticmethod
    def deserialize_alert(data: Mapping[str, Any]) -> AlertRecord:
        """Deserialise an :class:`AlertRecord`."""
        return AlertRecord(
            alert_id=str(data['alert_id']),
            severity=str(data['severity']),
            dimension=BudgetDimension(data['dimension']),
            subsystem=str(data['subsystem']),
            message=str(data['message']),
            timestamp=float(data['timestamp']),
        )


# ===================================================================== #
# 12. BudgetDiagnostics                                                  #
# ===================================================================== #

class BudgetDiagnostics:
    """Produces human-readable diagnostic reports on budget health.

    Reports can be rendered to dictionaries (for JSON APIs) or to
    formatted strings (for CLI / log output).
    """

    def __init__(
        self,
        tracker: BudgetTracker,
        policy: BudgetPolicy,
        history: BudgetHistory | None = None,
    ) -> None:
        self._tracker = tracker
        self._policy = policy
        self._history = history

    def budget_summary(
        self,
        budgets: Mapping[str, Budget],
    ) -> dict[str, Any]:
        """One-line summary per subsystem.

        Returns
        -------
        dict[str, Any]
            ``{ subsystem: { dimension: { total, spent, remaining, utilization } } }``
        """
        summary: dict[str, Any] = {}
        for name, budget in budgets.items():
            dims: dict[str, Any] = {}
            for dim, alloc in budget._allocations.items():
                dims[dim.value] = {
                    'total': alloc.total,
                    'spent': alloc.spent,
                    'remaining': alloc.remaining(),
                    'utilization': round(alloc.utilization(), 4),
                }
            summary[name] = dims
        return summary

    def spending_report(
        self,
        budgets: Mapping[str, Budget],
    ) -> dict[str, Any]:
        """Break down spending by channel and dimension.

        Returns
        -------
        dict[str, Any]
            Keys: ``by_channel``, ``by_dimension``, ``elapsed_seconds``.
        """
        by_dim: dict[str, int] = {}
        for dim in BudgetDimension:
            by_dim[dim.value] = self._tracker.total_spent(dim)
        return {
            'by_channel': self._tracker.spending_by_channel(),
            'by_dimension': by_dim,
            'elapsed_seconds': round(self._tracker.elapsed_seconds(), 2),
            'event_count': self._tracker.event_count(),
        }

    def efficiency_report(
        self,
        budgets: Mapping[str, Budget],
    ) -> dict[str, Any]:
        """Rate each subsystem's budget efficiency.

        Efficiency is defined as the ratio of productive spending to
        total allocation, weighted toward monetary dimensions.
        """
        optimizer = BudgetOptimizer(self._tracker, self._policy)
        report: dict[str, Any] = {}
        for name, budget in budgets.items():
            score = optimizer.efficiency_score(budget)
            roi = optimizer.analyze_roi({name: budget}).get(name, {})
            report[name] = {
                'efficiency_score': round(score, 4),
                'roi_by_dimension': roi,
                'recommendation': (
                    'well-utilised' if score >= 0.5
                    else 'under-utilised — consider reducing allocation'
                ),
            }
        return report

    def copilot_budget_summary(
        self,
        budgets: Mapping[str, Budget],
    ) -> dict[str, Any]:
        """Focused summary of copilot/LLM token spending across subsystems.

        This is the report that platform administrators consult to
        understand aggregate copilot cost.
        """
        total_spent = 0
        total_remaining = 0
        per_subsystem: dict[str, dict[str, Any]] = {}
        dim = BudgetDimension.COPILOT_TOKENS
        for name, budget in budgets.items():
            alloc = budget._allocations.get(dim)
            if alloc is None:
                continue
            total_spent += alloc.spent
            total_remaining += alloc.remaining()
            per_subsystem[name] = {
                'spent': alloc.spent,
                'remaining': alloc.remaining(),
                'utilization': round(alloc.utilization(), 4),
            }
        rate = self._tracker.spending_rate(dim)
        return {
            'ceiling': self._policy.copilot_token_ceiling,
            'total_spent': total_spent,
            'total_remaining': total_remaining,
            'aggregate_rate_per_second': round(rate, 2),
            'per_subsystem': per_subsystem,
        }

    def full_diagnostics(
        self,
        budgets: Mapping[str, Budget],
    ) -> dict[str, Any]:
        """Combine all diagnostic reports into a single payload.

        Intended for export to monitoring systems or the diagnostics
        endpoint.
        """
        diag: dict[str, Any] = {
            'budget_summary': self.budget_summary(budgets),
            'spending_report': self.spending_report(budgets),
            'efficiency_report': self.efficiency_report(budgets),
            'copilot_budget_summary': self.copilot_budget_summary(budgets),
        }
        if self._history is not None:
            diag['history_snapshot_count'] = self._history.snapshot_count()
            latest = self._history.latest()
            if latest is not None:
                diag['latest_snapshot_ts'] = latest.timestamp
                diag['latest_alerts'] = latest.alerts
        return diag

    def format_text_report(
        self,
        budgets: Mapping[str, Budget],
        *,
        width: int = 72,
    ) -> str:
        """Return a human-readable text report suitable for logging.

        Parameters
        ----------
        budgets:
            Subsystem → budget mapping.
        width:
            Maximum line width for the report.
        """
        lines: list[str] = []
        sep = '=' * width
        lines.append(sep)
        lines.append('JuGeo Budget Diagnostics Report')
        lines.append(sep)
        for name, budget in budgets.items():
            lines.append(f'\n--- {name} ---')
            for dim, alloc in budget._allocations.items():
                bar_len = 30
                filled = int(bar_len * alloc.utilization())
                bar = '█' * filled + '░' * (bar_len - filled)
                lines.append(
                    f'  {dim.value:<20s} [{bar}] '
                    f'{alloc.spent}/{alloc.total} {dim.unit_label} '
                    f'({alloc.utilization():.0%})'
                )
        lines.append(f'\nElapsed: {self._tracker.elapsed_seconds():.1f}s')
        lines.append(f'Events recorded: {self._tracker.event_count()}')
        lines.append(sep)
        return '\n'.join(lines)


# ===================================================================== #
# Backward-compatibility alias                                           #
# ===================================================================== #

class BudgetLedger:
    """Legacy budget interface preserved for backward compatibility.

    New code should use :class:`Budget` directly.  This wrapper
    delegates to a :class:`Budget` instance internally.

    .. deprecated::
        Use :class:`Budget` instead.
    """

    def __init__(
        self,
        limits: dict[str, int] | None = None,
        spent: dict[str, int] | None = None,
    ) -> None:
        self.limits: dict[str, int] = dict(limits or {})
        self.spent: dict[str, int] = dict(spent or {})

    def remaining(self, key: str) -> int:
        """Return remaining capacity for *key*."""
        return self.limits.get(key, 0) - self.spent.get(key, 0)

    def consume(self, key: str, amount: int) -> bool:
        """Consume *amount* from *key*.  Returns ``False`` on overspend."""
        if self.remaining(key) < amount:
            return False
        self.spent[key] = self.spent.get(key, 0) + amount
        return True

    def release(self, key: str, amount: int) -> None:
        """Release *amount* previously consumed under *key*."""
        self.spent[key] = max(0, self.spent.get(key, 0) - amount)


# ===================================================================== #
# Module exports                                                         #
# ===================================================================== #

__all__ = [
    'AlertRecord',
    'Budget',
    'BudgetAlert',
    'BudgetAllocation',
    'BudgetAllocator',
    'BudgetDiagnostics',
    'BudgetDimension',
    'BudgetEnforcer',
    'BudgetHistory',
    'BudgetLedger',
    'BudgetOptimizer',
    'BudgetPolicy',
    'BudgetSerializer',
    'BudgetTracker',
]

# copilot: shared-core marker for LLM orchestration budget enforcement.
