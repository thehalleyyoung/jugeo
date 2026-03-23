"""
Implementation consequences of mathematical regime bootstrapping — theory2.tex Ch59.

Overview
--------
When a new regime is bootstrapped into the JuGeo system — meaning a new mathematical
domain has been formed, type constructors have been introduced, carriers have been
established, and the governing laws have been accepted — there are concrete and
potentially wide-ranging implementation consequences for the running system.

Concretely, the following categories of system artefacts may be affected:

  1. **Pack updates** — existing packs may expose types or operations whose
     semantics are now superseded or extended by the newly introduced regime.
     Indices, metadata, and cross-references within those packs must be
     refreshed to reflect the new regime's type constructors and carriers.

  2. **Bridge re-indexing** — bridges connect packs across domain boundaries.
     When a new regime is bootstrapped, the bridge registry may need to be
     re-scanned: new edges may become valid, old edges may be invalidated, and
     the trust weights assigned to bridge theorems may need to be recalculated
     against the updated domain topology.

  3. **Theorem dependency graph recomputation** — the JuGeo dependency graph
     tracks which theorems rely on which others.  A regime change can make
     previously independent theorems mutually dependent (e.g. via a new shared
     carrier type), or can dissolve dependencies that were only necessary under
     the old domain topology.  The affected subgraph must be recomputed to
     keep proof-search and incremental-checking correct.

Theory Reference
----------------
theory2.tex Ch59 §6 — "Propagating Bootstrapping Consequences Through the
Implementation Layer".  The section formalises a *consequence functor* C whose
domain is the category of regime-change events and whose codomain is the
category of implementation tasks, ordered by priority.  The key result
(Proposition 59.6.3) states that the consequence functor is *finitely
presentable*: for any finite regime change, the induced task set is finite and
admits a canonical cost bound.

Impact Scope
------------
The module distinguishes four impact scopes, ordered by increasing breadth:

  - LOCAL      — a single theorem or type constructor is affected.
  - MODULE     — all definitions within a single pack module are affected.
  - PACKAGE    — all modules within a pack (and transitively dependent packs)
                 are affected.
  - SYSTEM_WIDE — the entire JuGeo system requires updating; this occurs when
                  a carrier promotion is accepted that alters a foundational
                  type shared across all packs.

The scope is determined heuristically from the change type and the count of
affected IDs; see `_scope_from_change_type` for the exact decision procedure.

Cost Estimation
---------------
Costs are measured in abstract *work units* (WU).  The estimation algorithm
decomposes the total cost into three independent components:

  total = pack_update_cost + bridge_reindex_cost + dependency_recompute_cost

Each component scales sub-linearly with the number of affected artefacts,
reflecting the amortisation of fixed per-session overhead.  Specifically:

  pack_update_cost          = BASE_PACK_UPDATE_COST   * n_packs^0.8
  bridge_reindex_cost       = BASE_BRIDGE_REINDEX_COST * n_bridges^0.75
  dependency_recompute_cost = BASE_DEPENDENCY_RECOMPUTE_COST * n_theorems * log2(depth + 2)

Design Notes
------------
The coordinator follows a *plan-then-execute* pattern:

  1. `compute_impact_set`  — pure analysis; no side effects.
  2. `schedule_*`          — task generation; no side effects.
  3. `execute_implementation_plan` — side-effecting; honours `dry_run`.
  4. `run_consequence_cycle` — full pipeline with wall-clock timing.

The witness class produces lightweight, immutable audit records that can be
forwarded to an external evidence channel without coupling this module to
the full evidence subsystem.

Typical Usage
-------------
::

    from jugeo.ideation.regime_bootstrapping.implementation_consequences import (
        RegimeChange,
        ImplementationConfig,
        run_consequence_cycle,
    )

    change = RegimeChange(
        change_id="rc-001",
        regime_name="TopologicalGroups",
        change_type="domain_formation",
        affected_domain_ids=("dom-tg",),
        affected_constructor_ids=("ctor-grp", "ctor-top"),
        affected_carrier_ids=("carr-set",),
        timestamp="2025-01-01T00:00:00+00:00",
    )
    result = run_consequence_cycle(change)
    print(result)

# copilot: shared-core marker
"""

from __future__ import annotations

import hashlib
import itertools
import logging
import math
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional, Sequence, Tuple

__all__ = [
    "TaskStatus",
    "ImpactScope",
    "ImplementationConfig",
    "RegimeChange",
    "ImpactSet",
    "PackUpdateTask",
    "BridgeReindexTask",
    "DependencyRecomputeTask",
    "ImplementationPlan",
    "ImplementationResult",
    "ConsequenceCycleResult",
    "ImpactScopeReport",
    "FeasibilityReport",
    "GraphChangeReport",
    "CostEstimate",
    "ImpactWitnessReport",
    "PlanWitnessReport",
    "ResultWitnessReport",
    "RegimeImplementationCoordinator",
    "RegimeImplementationAnalyzer",
    "RegimeImplementationWitness",
    "run_consequence_cycle",
    "compute_impact_set",
    "build_implementation_plan",
]

try:
    from jugeo.evidence.manifests import Manifest, build_evidence_manifest
    from jugeo.evidence.trust import TrustProfile, TrustTier, join_trust_profiles
    from jugeo.evidence.channels import EvidenceRecord, EvidenceKind, build_channel
    from jugeo.evidence.provenance import ProvenanceTrace
    from jugeo.packs.bridges import BridgeTheorem, BridgeRegistry, BridgeComposer
    from jugeo.packs.authority import PackAuthority, PackAuthorityRegistry
    from jugeo.packs.catalog import PackDescriptor
    from jugeo.orchestration.controller import Orchestrator, OrchestratorState
    from jugeo.ideation.ideas import IdeaProposal, TrustStatus
    from jugeo.ideation.regimes import Regime, RegimeCatalog
    from jugeo.ideation.novelty import NoveltyScore
    from jugeo.geometry.site import Site, Coordinate
    from jugeo.geometry.descent import DescentResult, GlobalSection
except Exception:
    pass

try:
    from jugeo.ideation.regime_bootstrapping.models import (
        BootstrappedRegime,
        RegimeBootstrappingConfig,
        RegimeBootstrappingResult,
    )
except Exception:
    pass

# ---------------------------------------------------------------------------
# Module-level logger
# ---------------------------------------------------------------------------

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

BASE_PACK_UPDATE_COST: float = 12.0
"""Base work-unit cost for updating a single pack."""

BASE_BRIDGE_REINDEX_COST: float = 8.5
"""Base work-unit cost for re-indexing a single bridge."""

BASE_DEPENDENCY_RECOMPUTE_COST: float = 5.0
"""Base work-unit cost per theorem in the dependency recomputation pass."""

MAX_RECOMPUTE_DEPTH: int = 16
"""Maximum recursion depth when traversing the dependency graph."""

DOMAIN_FORMATION_SCOPE_THRESHOLD: int = 3
"""If ≥ this many domains are affected, domain_formation triggers PACKAGE scope."""

CARRIER_PROMOTION_SCOPE_THRESHOLD: int = 1
"""Carrier promotion always triggers at least SYSTEM_WIDE when threshold is met."""

LAW_ACCEPTED_MODULE_THRESHOLD: int = 5
"""Theorems above this count during law_accepted trigger MODULE scope."""

TASK_PRIORITY_BASE: float = 100.0
"""Baseline task priority; higher values are scheduled first."""

FEASIBILITY_WARNING_RATIO: float = 0.80
"""Emit a budget warning when estimated cost exceeds this fraction of the budget."""

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class TaskStatus(Enum):
    """Lifecycle status of an implementation task."""

    PENDING = auto()
    IN_PROGRESS = auto()
    COMPLETED = auto()
    FAILED = auto()


class ImpactScope(Enum):
    """
    Breadth of a regime change's implementation impact.

    Ordered from narrowest (LOCAL) to broadest (SYSTEM_WIDE).  The ordering is
    reflected in the integer values assigned by ``auto()``; callers may compare
    scopes with ``scope.value >= ImpactScope.PACKAGE.value``.
    """

    LOCAL = auto()        # affects a single theorem or type
    MODULE = auto()       # affects a single pack module
    PACKAGE = auto()      # affects all modules in a pack
    SYSTEM_WIDE = auto()  # affects the entire JuGeo system

# ---------------------------------------------------------------------------
# Value-object dataclasses (frozen + slots)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ImplementationConfig:
    """
    Immutable configuration governing the consequence-cycle pipeline.

    Attributes
    ----------
    max_pack_update_tasks:
        Hard cap on the number of pack-update tasks generated per cycle.
    max_bridge_reindex_tasks:
        Hard cap on the number of bridge re-index tasks generated per cycle.
    max_dependency_recompute_tasks:
        Hard cap on the number of dependency-recomputation tasks per cycle.
    dry_run:
        When *True*, no work is actually performed; tasks are logged only.
    parallel_execution:
        When *True*, the coordinator may execute tasks concurrently (reserved
        for future async implementation; currently honoured as a hint only).
    cost_budget:
        Maximum permissible total cost in work units for a single cycle.
    impact_scope_threshold:
        Tasks whose computed scope is strictly below this value are silently
        dropped from the plan (they are still included in the impact set).
    """

    max_pack_update_tasks: int = 100
    max_bridge_reindex_tasks: int = 200
    max_dependency_recompute_tasks: int = 50
    dry_run: bool = False
    parallel_execution: bool = False
    cost_budget: float = 1000.0
    impact_scope_threshold: ImpactScope = ImpactScope.MODULE


@dataclass(frozen=True, slots=True)
class RegimeChange:
    """
    An immutable record describing a single bootstrapping event in a regime.

    Attributes
    ----------
    change_id:
        A globally unique identifier for this change event.
    regime_name:
        The human-readable name of the regime being bootstrapped.
    change_type:
        One of ``"domain_formation"``, ``"type_constructor"``,
        ``"carrier_promotion"``, or ``"law_accepted"``.
    affected_domain_ids:
        Tuple of domain IDs whose structures are directly affected.
    affected_constructor_ids:
        Tuple of type-constructor IDs introduced or modified by this change.
    affected_carrier_ids:
        Tuple of carrier IDs that have been promoted or altered.
    timestamp:
        ISO-8601 timestamp at which the change was recorded.
    metadata:
        Arbitrary key/value annotations (e.g. author, ticket reference).
    """

    change_id: str
    regime_name: str
    change_type: str
    affected_domain_ids: tuple[str, ...]
    affected_constructor_ids: tuple[str, ...]
    affected_carrier_ids: tuple[str, ...]
    timestamp: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ImpactSet:
    """
    A mutable record accumulating all artefacts affected by a regime change.

    This object is built incrementally by ``RegimeImplementationCoordinator
    .compute_impact_set`` and is subsequently consumed by the scheduling
    methods.

    Attributes
    ----------
    impact_id:
        Unique identifier for this impact-set computation.
    regime_change_id:
        The change event that triggered this impact analysis.
    scope:
        The computed impact scope (see ``ImpactScope``).
    affected_pack_ids:
        List of pack IDs whose contents require updating.
    affected_bridge_ids:
        List of bridge IDs that must be re-indexed.
    affected_theorem_ids:
        List of theorem IDs whose dependency edges must be recomputed.
    estimated_cost:
        Total estimated cost in work units, populated after all lists are
        complete.
    computed_at:
        ISO-8601 timestamp at which this impact set was finalised.
    """

    impact_id: str
    regime_change_id: str
    scope: ImpactScope
    affected_pack_ids: list[str] = field(default_factory=list)
    affected_bridge_ids: list[str] = field(default_factory=list)
    affected_theorem_ids: list[str] = field(default_factory=list)
    estimated_cost: float = 0.0
    computed_at: str = ""


@dataclass(frozen=True, slots=True)
class PackUpdateTask:
    """An immutable task record requesting a pack-metadata refresh."""

    task_id: str
    pack_id: str
    update_reason: str
    priority: float
    status: TaskStatus
    estimated_cost: float
    created_at: str


@dataclass(frozen=True, slots=True)
class BridgeReindexTask:
    """An immutable task record requesting bridge re-indexing."""

    task_id: str
    bridge_id: str
    reindex_reason: str
    priority: float
    status: TaskStatus
    estimated_cost: float
    created_at: str


@dataclass(frozen=True, slots=True)
class DependencyRecomputeTask:
    """An immutable task record requesting dependency-graph recomputation."""

    task_id: str
    theorem_id: str
    recompute_depth: int
    priority: float
    status: TaskStatus
    estimated_cost: float
    created_at: str


@dataclass(slots=True)
class ImplementationPlan:
    """
    A mutable plan aggregating all tasks scheduled for a single regime change.

    The plan is created by ``RegimeImplementationCoordinator`` and passed to
    ``execute_implementation_plan``.  After execution the plan object should be
    treated as read-only.

    Attributes
    ----------
    plan_id:
        Unique identifier for this plan.
    regime_change_id:
        The triggering change event.
    pack_update_tasks:
        Ordered list of pack-update tasks (highest priority first).
    bridge_reindex_tasks:
        Ordered list of bridge re-index tasks.
    dependency_recompute_tasks:
        Ordered list of dependency-recomputation tasks.
    total_estimated_cost:
        Sum of estimated costs across all tasks.
    created_at:
        ISO-8601 creation timestamp.
    """

    plan_id: str
    regime_change_id: str
    pack_update_tasks: list[PackUpdateTask] = field(default_factory=list)
    bridge_reindex_tasks: list[BridgeReindexTask] = field(default_factory=list)
    dependency_recompute_tasks: list[DependencyRecomputeTask] = field(default_factory=list)
    total_estimated_cost: float = 0.0
    created_at: str = ""


@dataclass(frozen=True, slots=True)
class ImplementationResult:
    """Immutable summary of a completed (or aborted) plan execution."""

    plan_id: str
    pack_tasks_completed: int
    pack_tasks_failed: int
    bridge_tasks_completed: int
    bridge_tasks_failed: int
    dependency_tasks_completed: int
    dependency_tasks_failed: int
    total_cost_incurred: float
    duration_seconds: float


@dataclass(frozen=True, slots=True)
class ConsequenceCycleResult:
    """Top-level result record for a complete consequence cycle."""

    cycle_id: str
    regime_change_id: str
    impact_scope: ImpactScope
    total_tasks: int
    completed_tasks: int
    failed_tasks: int
    total_cost: float
    duration_seconds: float
    success: bool


@dataclass(frozen=True, slots=True)
class ImpactScopeReport:
    """Analytical report on the scope of an impact set."""

    impact_id: str
    scope: ImpactScope
    pack_count: int
    bridge_count: int
    theorem_count: int
    scope_justification: str


@dataclass(frozen=True, slots=True)
class FeasibilityReport:
    """Feasibility assessment for a set of pack-update tasks."""

    plan_id: str
    is_feasible: bool
    cost_within_budget: bool
    estimated_cost: float
    budget: float
    blocking_issues: tuple[str, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GraphChangeReport:
    """Summary of changes to the theorem dependency graph."""

    affected_theorem_count: int
    new_edges: int
    removed_edges: int
    recompute_depth_max: int
    estimated_recompute_cost: float
    summary: str


@dataclass(frozen=True, slots=True)
class CostEstimate:
    """Detailed cost breakdown for an implementation plan."""

    plan_id: str
    pack_update_cost: float
    bridge_reindex_cost: float
    dependency_recompute_cost: float
    total_cost: float
    is_within_budget: bool
    breakdown: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ImpactWitnessReport:
    """Lightweight audit record witnessing an impact-set computation."""

    witness_id: str
    change_id: str
    scope_name: str
    affected_count: int
    timestamp: str


@dataclass(frozen=True, slots=True)
class PlanWitnessReport:
    """Lightweight audit record witnessing a plan creation."""

    witness_id: str
    plan_id: str
    total_tasks: int
    estimated_cost: float
    timestamp: str


@dataclass(frozen=True, slots=True)
class ResultWitnessReport:
    """Lightweight audit record witnessing a plan execution result."""

    witness_id: str
    plan_id: str
    completed_tasks: int
    failed_tasks: int
    total_cost: float
    success: bool
    timestamp: str


# ---------------------------------------------------------------------------
# Private helper functions
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _stable_hash(value: str) -> int:
    """Return a stable, positive integer hash of *value* using SHA-256."""
    digest = hashlib.sha256(value.encode()).hexdigest()
    return int(digest[:16], 16)


def _build_impact_id(change_id: str) -> str:
    """Derive a deterministic impact-set ID from a change ID."""
    h = _stable_hash(f"impact:{change_id}")
    return f"impact-{h:016x}"


def _build_task_id(prefix: str, resource_id: str) -> str:
    """Derive a deterministic task ID from a prefix and resource ID."""
    h = _stable_hash(f"{prefix}:{resource_id}")
    return f"{prefix}-{h:016x}"


def _build_plan_id(change_id: str) -> str:
    """Derive a deterministic plan ID from a change ID."""
    h = _stable_hash(f"plan:{change_id}")
    return f"plan-{h:016x}"


def _build_cycle_id(change_id: str) -> str:
    """Derive a deterministic cycle ID from a change ID."""
    h = _stable_hash(f"cycle:{change_id}")
    return f"cycle-{h:016x}"


def _scope_from_change_type(change_type: str, affected_count: int) -> ImpactScope:
    """
    Heuristically determine the impact scope from the change type and the total
    number of affected artefacts.

    Decision table
    --------------
    domain_formation   + count >= DOMAIN_FORMATION_SCOPE_THRESHOLD  -> PACKAGE
    domain_formation   + count <  threshold                          -> MODULE
    type_constructor   + count >= 4                                  -> MODULE
    type_constructor   + count <  4                                  -> LOCAL
    carrier_promotion  (any count)                                   -> SYSTEM_WIDE
    law_accepted       + count >= LAW_ACCEPTED_MODULE_THRESHOLD      -> MODULE
    law_accepted       + count <  threshold                          -> LOCAL
    (unknown)                                                        -> LOCAL
    """
    if change_type == "carrier_promotion":
        return ImpactScope.SYSTEM_WIDE
    if change_type == "domain_formation":
        if affected_count >= DOMAIN_FORMATION_SCOPE_THRESHOLD:
            return ImpactScope.PACKAGE
        return ImpactScope.MODULE
    if change_type == "type_constructor":
        if affected_count >= 4:
            return ImpactScope.MODULE
        return ImpactScope.LOCAL
    if change_type == "law_accepted":
        if affected_count >= LAW_ACCEPTED_MODULE_THRESHOLD:
            return ImpactScope.MODULE
        return ImpactScope.LOCAL
    return ImpactScope.LOCAL


def _estimate_pack_update_cost(pack_count: int) -> float:
    """
    Estimate the total cost (in work units) of updating *pack_count* packs.

    Uses a sub-linear model: ``BASE_PACK_UPDATE_COST * n^0.8``.
    Returns 0.0 when *pack_count* is zero.
    """
    if pack_count <= 0:
        return 0.0
    return BASE_PACK_UPDATE_COST * (pack_count ** 0.8)


def _estimate_bridge_reindex_cost(bridge_count: int) -> float:
    """
    Estimate the total cost (in work units) of re-indexing *bridge_count* bridges.

    Uses a sub-linear model: ``BASE_BRIDGE_REINDEX_COST * n^0.75``.
    Returns 0.0 when *bridge_count* is zero.
    """
    if bridge_count <= 0:
        return 0.0
    return BASE_BRIDGE_REINDEX_COST * (bridge_count ** 0.75)


def _estimate_dependency_recompute_cost(theorem_count: int, depth: int) -> float:
    """
    Estimate the total cost (in work units) of recomputing *theorem_count*
    theorem dependencies to a maximum recursion depth of *depth*.

    Model: ``BASE_DEPENDENCY_RECOMPUTE_COST * theorem_count * log2(depth + 2)``
    """
    if theorem_count <= 0:
        return 0.0
    depth_factor = math.log2(max(depth, 0) + 2)
    return BASE_DEPENDENCY_RECOMPUTE_COST * theorem_count * depth_factor


def _simulate_task_execution(task_id: str, estimated_cost: float) -> tuple[bool, float]:
    """
    Simulate the execution of a single task with deterministic success/failure.

    Determinism is achieved by hashing the *task_id*: tasks whose hash value
    is divisible by 17 are treated as failures (roughly ~6% failure rate),
    while all others succeed.  The actual cost incurred is sampled as
    ``estimated_cost * (0.9 + 0.2 * frac)`` where *frac* is the fractional
    part of the hash in [0, 1), giving a ±10% variation around the estimate.

    Returns
    -------
    (success, actual_cost)
        A boolean success flag and the actual cost incurred (0.0 on failure).
    """
    h = _stable_hash(task_id)
    success = (h % 17) != 0
    frac = (h % 1000) / 1000.0
    actual_cost = estimated_cost * (0.9 + 0.2 * frac) if success else 0.0
    return success, actual_cost


# ---------------------------------------------------------------------------
# RegimeImplementationCoordinator
# ---------------------------------------------------------------------------


class RegimeImplementationCoordinator:
    """
    Orchestrates the full consequence-cycle pipeline for a regime change.

    This is the primary entry point for triggering implementation updates after
    a regime-bootstrapping event.  It combines impact analysis, task scheduling,
    and (simulated or real) plan execution into a single ``run_consequence_cycle``
    call.

    Parameters
    ----------
    config:
        Configuration governing task caps, cost budgets, and execution mode.
    """

    def __init__(self, config: ImplementationConfig) -> None:
        self._config = config
        log.debug("RegimeImplementationCoordinator initialised with config=%r", config)

    # ------------------------------------------------------------------
    # Impact analysis
    # ------------------------------------------------------------------

    def compute_impact_set(self, regime_change: RegimeChange) -> ImpactSet:
        """
        Determine which system artefacts are affected by *regime_change*.

        The method applies the heuristic in ``_scope_from_change_type`` to
        establish the broad scope, then synthesises placeholder IDs for the
        affected packs, bridges, and theorems from the IDs embedded in the
        regime-change record.  In a production deployment these IDs would be
        resolved against the live pack and bridge registries.

        Parameters
        ----------
        regime_change:
            The bootstrapping event to analyse.

        Returns
        -------
        ImpactSet
            A fully populated (but mutable) impact-set record with an estimated
            cost assigned.
        """
        log.info(
            "Computing impact set for change %s (type=%s)",
            regime_change.change_id,
            regime_change.change_type,
        )

        total_affected = (
            len(regime_change.affected_domain_ids)
            + len(regime_change.affected_constructor_ids)
            + len(regime_change.affected_carrier_ids)
        )
        scope = _scope_from_change_type(regime_change.change_type, total_affected)
        log.debug("Resolved impact scope: %s", scope.name)

        impact_id = _build_impact_id(regime_change.change_id)
        impact = ImpactSet(
            impact_id=impact_id,
            regime_change_id=regime_change.change_id,
            scope=scope,
        )

        # Derive affected pack IDs — one pack per domain, constructor, and carrier.
        seen_packs: set[str] = set()
        for dom_id in regime_change.affected_domain_ids:
            pack_id = f"pack-dom-{dom_id}"
            if pack_id not in seen_packs:
                impact.affected_pack_ids.append(pack_id)
                seen_packs.add(pack_id)
        for ctor_id in regime_change.affected_constructor_ids:
            pack_id = f"pack-ctor-{ctor_id}"
            if pack_id not in seen_packs:
                impact.affected_pack_ids.append(pack_id)
                seen_packs.add(pack_id)
        if scope in (ImpactScope.PACKAGE, ImpactScope.SYSTEM_WIDE):
            for carr_id in regime_change.affected_carrier_ids:
                pack_id = f"pack-carr-{carr_id}"
                if pack_id not in seen_packs:
                    impact.affected_pack_ids.append(pack_id)
                    seen_packs.add(pack_id)

        # Derive affected bridge IDs — bridges connecting each pair of affected domains.
        domain_pairs = list(
            itertools.combinations(regime_change.affected_domain_ids, 2)
        )
        for d1, d2 in domain_pairs:
            bridge_id = f"bridge-{d1}-{d2}"
            impact.affected_bridge_ids.append(bridge_id)
        # Always include bridges from constructors when scope is broad.
        if scope.value >= ImpactScope.MODULE.value:
            for ctor_id in regime_change.affected_constructor_ids:
                impact.affected_bridge_ids.append(f"bridge-ctor-{ctor_id}")

        # Derive affected theorem IDs.
        for dom_id in regime_change.affected_domain_ids:
            impact.affected_theorem_ids.append(f"thm-{dom_id}-main")
            impact.affected_theorem_ids.append(f"thm-{dom_id}-coherence")
        for ctor_id in regime_change.affected_constructor_ids:
            impact.affected_theorem_ids.append(f"thm-{ctor_id}-typing")
        if scope.value >= ImpactScope.MODULE.value:
            for carr_id in regime_change.affected_carrier_ids:
                impact.affected_theorem_ids.append(f"thm-{carr_id}-promotion")

        # Cost estimation.
        max_depth = MAX_RECOMPUTE_DEPTH
        pack_cost = _estimate_pack_update_cost(len(impact.affected_pack_ids))
        bridge_cost = _estimate_bridge_reindex_cost(len(impact.affected_bridge_ids))
        dep_cost = _estimate_dependency_recompute_cost(
            len(impact.affected_theorem_ids), max_depth
        )
        impact.estimated_cost = pack_cost + bridge_cost + dep_cost
        impact.computed_at = _now_iso()

        log.info(
            "Impact set %s computed: packs=%d bridges=%d theorems=%d cost=%.2f",
            impact_id,
            len(impact.affected_pack_ids),
            len(impact.affected_bridge_ids),
            len(impact.affected_theorem_ids),
            impact.estimated_cost,
        )
        return impact

    # ------------------------------------------------------------------
    # Task scheduling
    # ------------------------------------------------------------------

    def schedule_pack_updates(self, impact: ImpactSet) -> list[PackUpdateTask]:
        """
        Generate a sorted list of ``PackUpdateTask`` objects from *impact*.

        Tasks are ordered by descending priority.  Priority is derived from the
        stable hash of the pack ID (mod TASK_PRIORITY_BASE), ensuring
        reproducible ordering without an external sort key.  The total number
        of tasks is capped by ``config.max_pack_update_tasks``.

        Parameters
        ----------
        impact:
            The impact set produced by ``compute_impact_set``.

        Returns
        -------
        list[PackUpdateTask]
            Tasks ready for scheduling, highest priority first.
        """
        tasks: list[PackUpdateTask] = []
        cap = self._config.max_pack_update_tasks
        now = _now_iso()

        for pack_id in impact.affected_pack_ids[:cap]:
            task_id = _build_task_id("pack-upd", pack_id)
            h = _stable_hash(task_id)
            priority = TASK_PRIORITY_BASE * (1.0 + (h % 100) / 100.0)
            cost = BASE_PACK_UPDATE_COST * (1.0 + (h % 50) / 100.0)
            reason = (
                f"Regime change {impact.regime_change_id} affected pack {pack_id} "
                f"(scope={impact.scope.name})"
            )
            tasks.append(
                PackUpdateTask(
                    task_id=task_id,
                    pack_id=pack_id,
                    update_reason=reason,
                    priority=priority,
                    status=TaskStatus.PENDING,
                    estimated_cost=cost,
                    created_at=now,
                )
            )

        tasks.sort(key=lambda t: t.priority, reverse=True)
        log.debug(
            "Scheduled %d pack-update tasks for impact %s",
            len(tasks),
            impact.impact_id,
        )
        return tasks

    def schedule_bridge_reindexing(self, impact: ImpactSet) -> list[BridgeReindexTask]:
        """
        Generate a sorted list of ``BridgeReindexTask`` objects from *impact*.

        Bridge re-index tasks receive a higher base priority than pack-update
        tasks because incorrect bridge information can corrupt proof search
        immediately, while stale pack metadata degrades performance more
        gradually.

        Parameters
        ----------
        impact:
            The impact set produced by ``compute_impact_set``.

        Returns
        -------
        list[BridgeReindexTask]
            Tasks ready for scheduling, highest priority first.
        """
        tasks: list[BridgeReindexTask] = []
        cap = self._config.max_bridge_reindex_tasks
        now = _now_iso()

        for bridge_id in impact.affected_bridge_ids[:cap]:
            task_id = _build_task_id("bridge-ridx", bridge_id)
            h = _stable_hash(task_id)
            priority = TASK_PRIORITY_BASE * 1.2 * (1.0 + (h % 100) / 100.0)
            cost = BASE_BRIDGE_REINDEX_COST * (1.0 + (h % 50) / 100.0)
            reason = (
                f"Regime change {impact.regime_change_id} invalidated bridge {bridge_id} "
                f"(scope={impact.scope.name})"
            )
            tasks.append(
                BridgeReindexTask(
                    task_id=task_id,
                    bridge_id=bridge_id,
                    reindex_reason=reason,
                    priority=priority,
                    status=TaskStatus.PENDING,
                    estimated_cost=cost,
                    created_at=now,
                )
            )

        tasks.sort(key=lambda t: t.priority, reverse=True)
        log.debug(
            "Scheduled %d bridge-reindex tasks for impact %s",
            len(tasks),
            impact.impact_id,
        )
        return tasks

    def schedule_dependency_recomputation(
        self, impact: ImpactSet
    ) -> list[DependencyRecomputeTask]:
        """
        Generate a sorted list of ``DependencyRecomputeTask`` objects from *impact*.

        The recomputation depth for each theorem is set to ``MAX_RECOMPUTE_DEPTH``
        for system-wide changes, and scales down linearly for narrower scopes:
        ``PACKAGE → 12``, ``MODULE → 8``, ``LOCAL → 4``.

        Parameters
        ----------
        impact:
            The impact set produced by ``compute_impact_set``.

        Returns
        -------
        list[DependencyRecomputeTask]
            Tasks ready for scheduling, highest priority first.
        """
        depth_map: dict[ImpactScope, int] = {
            ImpactScope.LOCAL: 4,
            ImpactScope.MODULE: 8,
            ImpactScope.PACKAGE: 12,
            ImpactScope.SYSTEM_WIDE: MAX_RECOMPUTE_DEPTH,
        }
        recompute_depth = depth_map.get(impact.scope, 8)

        tasks: list[DependencyRecomputeTask] = []
        cap = self._config.max_dependency_recompute_tasks
        now = _now_iso()

        for thm_id in impact.affected_theorem_ids[:cap]:
            task_id = _build_task_id("dep-rcmp", thm_id)
            h = _stable_hash(task_id)
            priority = TASK_PRIORITY_BASE * 1.5 * (1.0 + (h % 100) / 100.0)
            cost = _estimate_dependency_recompute_cost(1, recompute_depth)
            tasks.append(
                DependencyRecomputeTask(
                    task_id=task_id,
                    theorem_id=thm_id,
                    recompute_depth=recompute_depth,
                    priority=priority,
                    status=TaskStatus.PENDING,
                    estimated_cost=cost,
                    created_at=now,
                )
            )

        tasks.sort(key=lambda t: t.priority, reverse=True)
        log.debug(
            "Scheduled %d dependency-recompute tasks for impact %s",
            len(tasks),
            impact.impact_id,
        )
        return tasks

    # ------------------------------------------------------------------
    # Plan construction
    # ------------------------------------------------------------------

    def _build_plan(
        self,
        regime_change: RegimeChange,
        impact: ImpactSet,
        pack_tasks: list[PackUpdateTask],
        bridge_tasks: list[BridgeReindexTask],
        dep_tasks: list[DependencyRecomputeTask],
    ) -> ImplementationPlan:
        """Assemble a complete ``ImplementationPlan`` from its constituent tasks."""
        plan_id = _build_plan_id(regime_change.change_id)
        total_cost = (
            sum(t.estimated_cost for t in pack_tasks)
            + sum(t.estimated_cost for t in bridge_tasks)
            + sum(t.estimated_cost for t in dep_tasks)
        )
        plan = ImplementationPlan(
            plan_id=plan_id,
            regime_change_id=regime_change.change_id,
            pack_update_tasks=list(pack_tasks),
            bridge_reindex_tasks=list(bridge_tasks),
            dependency_recompute_tasks=list(dep_tasks),
            total_estimated_cost=total_cost,
            created_at=_now_iso(),
        )
        log.info(
            "Implementation plan %s assembled: %d pack / %d bridge / %d dep tasks, cost=%.2f",
            plan_id,
            len(pack_tasks),
            len(bridge_tasks),
            len(dep_tasks),
            total_cost,
        )
        return plan

    # ------------------------------------------------------------------
    # Plan execution
    # ------------------------------------------------------------------

    def execute_implementation_plan(
        self, plan: ImplementationPlan
    ) -> ImplementationResult:
        """
        Execute all tasks in *plan*, returning a summary result.

        In ``dry_run`` mode the coordinator logs each task without simulating
        execution, and marks all tasks as COMPLETED with zero cost incurred.
        Otherwise, ``_simulate_task_execution`` is called for each task.

        Parameters
        ----------
        plan:
            The plan to execute.

        Returns
        -------
        ImplementationResult
            Counts of completed/failed tasks and the total cost incurred.
        """
        import time

        start = time.monotonic()
        log.info("Executing implementation plan %s (dry_run=%s)", plan.plan_id, self._config.dry_run)

        p_completed = p_failed = 0
        b_completed = b_failed = 0
        d_completed = d_failed = 0
        total_cost = 0.0

        # Pack-update tasks
        for task in plan.pack_update_tasks:
            if self._config.dry_run:
                log.debug("[DRY-RUN] Would execute pack-update task %s", task.task_id)
                p_completed += 1
            else:
                success, cost = _simulate_task_execution(task.task_id, task.estimated_cost)
                if success:
                    log.debug("Pack-update task %s completed (cost=%.2f)", task.task_id, cost)
                    p_completed += 1
                    total_cost += cost
                else:
                    log.warning("Pack-update task %s FAILED", task.task_id)
                    p_failed += 1

        # Bridge re-index tasks
        for task in plan.bridge_reindex_tasks:
            if self._config.dry_run:
                log.debug("[DRY-RUN] Would execute bridge-reindex task %s", task.task_id)
                b_completed += 1
            else:
                success, cost = _simulate_task_execution(task.task_id, task.estimated_cost)
                if success:
                    log.debug("Bridge-reindex task %s completed (cost=%.2f)", task.task_id, cost)
                    b_completed += 1
                    total_cost += cost
                else:
                    log.warning("Bridge-reindex task %s FAILED", task.task_id)
                    b_failed += 1

        # Dependency-recomputation tasks
        for task in plan.dependency_recompute_tasks:
            if self._config.dry_run:
                log.debug("[DRY-RUN] Would execute dep-recompute task %s", task.task_id)
                d_completed += 1
            else:
                success, cost = _simulate_task_execution(task.task_id, task.estimated_cost)
                if success:
                    log.debug("Dep-recompute task %s completed (cost=%.2f)", task.task_id, cost)
                    d_completed += 1
                    total_cost += cost
                else:
                    log.warning("Dep-recompute task %s FAILED", task.task_id)
                    d_failed += 1

        duration = time.monotonic() - start
        result = ImplementationResult(
            plan_id=plan.plan_id,
            pack_tasks_completed=p_completed,
            pack_tasks_failed=p_failed,
            bridge_tasks_completed=b_completed,
            bridge_tasks_failed=b_failed,
            dependency_tasks_completed=d_completed,
            dependency_tasks_failed=d_failed,
            total_cost_incurred=total_cost,
            duration_seconds=duration,
        )
        log.info(
            "Plan %s execution finished in %.3fs (cost=%.2f, failures=%d)",
            plan.plan_id,
            duration,
            total_cost,
            p_failed + b_failed + d_failed,
        )
        return result

    # ------------------------------------------------------------------
    # Full pipeline
    # ------------------------------------------------------------------

    def run_consequence_cycle(self, regime_change: RegimeChange) -> ConsequenceCycleResult:
        """
        Execute the complete consequence-cycle pipeline for *regime_change*.

        Steps:

        1. Compute the impact set.
        2. Schedule pack-update, bridge-reindex, and dependency-recompute tasks.
        3. Assemble the implementation plan.
        4. Execute the plan.
        5. Return a ``ConsequenceCycleResult`` with wall-clock timing.

        Parameters
        ----------
        regime_change:
            The bootstrapping event driving this cycle.

        Returns
        -------
        ConsequenceCycleResult
        """
        import time

        cycle_id = _build_cycle_id(regime_change.change_id)
        start = time.monotonic()
        log.info(
            "Starting consequence cycle %s for regime change %s",
            cycle_id,
            regime_change.change_id,
        )

        impact = self.compute_impact_set(regime_change)
        pack_tasks = self.schedule_pack_updates(impact)
        bridge_tasks = self.schedule_bridge_reindexing(impact)
        dep_tasks = self.schedule_dependency_recomputation(impact)
        plan = self._build_plan(regime_change, impact, pack_tasks, bridge_tasks, dep_tasks)
        result = self.execute_implementation_plan(plan)

        total_tasks = (
            len(pack_tasks) + len(bridge_tasks) + len(dep_tasks)
        )
        completed_tasks = (
            result.pack_tasks_completed
            + result.bridge_tasks_completed
            + result.dependency_tasks_completed
        )
        failed_tasks = (
            result.pack_tasks_failed
            + result.bridge_tasks_failed
            + result.dependency_tasks_failed
        )
        duration = time.monotonic() - start
        success = failed_tasks == 0

        cycle_result = ConsequenceCycleResult(
            cycle_id=cycle_id,
            regime_change_id=regime_change.change_id,
            impact_scope=impact.scope,
            total_tasks=total_tasks,
            completed_tasks=completed_tasks,
            failed_tasks=failed_tasks,
            total_cost=result.total_cost_incurred,
            duration_seconds=duration,
            success=success,
        )
        log.info(
            "Consequence cycle %s finished: success=%s total_tasks=%d cost=%.2f",
            cycle_id,
            success,
            total_tasks,
            result.total_cost_incurred,
        )
        return cycle_result


# ---------------------------------------------------------------------------
# RegimeImplementationAnalyzer
# ---------------------------------------------------------------------------


class RegimeImplementationAnalyzer:
    """
    Provides analytical reports on impact sets, tasks, and plans.

    Unlike the coordinator, the analyzer is purely functional — it never
    modifies any state or executes any tasks.
    """

    def analyze_impact_scope(self, impact: ImpactSet) -> ImpactScopeReport:
        """
        Produce a human-readable justification for the computed impact scope.

        Parameters
        ----------
        impact:
            The impact set to analyse.

        Returns
        -------
        ImpactScopeReport
        """
        scope = impact.scope
        pack_count = len(impact.affected_pack_ids)
        bridge_count = len(impact.affected_bridge_ids)
        theorem_count = len(impact.affected_theorem_ids)

        if scope == ImpactScope.SYSTEM_WIDE:
            justification = (
                "A carrier promotion was detected.  Because carriers form the "
                "foundational type layer shared across all packs, the entire "
                "JuGeo system requires updating."
            )
        elif scope == ImpactScope.PACKAGE:
            justification = (
                f"Domain formation affected {pack_count} packs across multiple "
                f"modules, exceeding the package-scope threshold "
                f"({DOMAIN_FORMATION_SCOPE_THRESHOLD} domains)."
            )
        elif scope == ImpactScope.MODULE:
            justification = (
                f"The change affected {theorem_count} theorems within a single "
                "pack module, triggering module-level re-indexing."
            )
        else:
            justification = (
                f"Only {theorem_count} theorem(s) and {pack_count} pack(s) are "
                "affected; LOCAL scope is sufficient."
            )

        return ImpactScopeReport(
            impact_id=impact.impact_id,
            scope=scope,
            pack_count=pack_count,
            bridge_count=bridge_count,
            theorem_count=theorem_count,
            scope_justification=justification,
        )

    def analyze_update_feasibility(
        self, tasks: list[PackUpdateTask], budget: float = 1000.0, plan_id: str = ""
    ) -> FeasibilityReport:
        """
        Assess whether the given pack-update tasks can be executed within *budget*.

        Parameters
        ----------
        tasks:
            Pack-update tasks to assess.
        budget:
            Maximum permissible cost.
        plan_id:
            Optional plan ID to embed in the report.

        Returns
        -------
        FeasibilityReport
        """
        estimated = sum(t.estimated_cost for t in tasks)
        within_budget = estimated <= budget
        blocking: list[str] = []
        warnings: list[str] = []

        if not within_budget:
            blocking.append(
                f"Estimated cost {estimated:.2f} WU exceeds budget {budget:.2f} WU."
            )
        elif estimated >= budget * FEASIBILITY_WARNING_RATIO:
            warnings.append(
                f"Estimated cost {estimated:.2f} WU is {100*estimated/budget:.1f}% of "
                f"budget {budget:.2f} WU — little headroom remaining."
            )

        if not tasks:
            warnings.append("Task list is empty; nothing to execute.")

        return FeasibilityReport(
            plan_id=plan_id or "unknown",
            is_feasible=len(blocking) == 0,
            cost_within_budget=within_budget,
            estimated_cost=estimated,
            budget=budget,
            blocking_issues=tuple(blocking),
            warnings=tuple(warnings),
        )

    def analyze_dependency_graph_changes(
        self, recompute_tasks: list[DependencyRecomputeTask]
    ) -> GraphChangeReport:
        """
        Summarise the expected changes to the theorem dependency graph.

        Uses a simple heuristic: the number of new and removed edges is
        estimated as a fraction of the affected theorem count, weighted by
        the maximum recompute depth.

        Parameters
        ----------
        recompute_tasks:
            Dependency-recomputation tasks to analyse.

        Returns
        -------
        GraphChangeReport
        """
        if not recompute_tasks:
            return GraphChangeReport(
                affected_theorem_count=0,
                new_edges=0,
                removed_edges=0,
                recompute_depth_max=0,
                estimated_recompute_cost=0.0,
                summary="No dependency-recomputation tasks scheduled.",
            )

        n = len(recompute_tasks)
        depth_max = max(t.recompute_depth for t in recompute_tasks)
        total_cost = sum(t.estimated_cost for t in recompute_tasks)

        # Heuristic: on average each theorem gains ~1.5 new edges and loses ~0.8.
        new_edges = int(n * 1.5 * math.log2(depth_max + 2))
        removed_edges = int(n * 0.8)

        summary = (
            f"Recomputing {n} theorem(s) to depth {depth_max}: "
            f"~{new_edges} new edge(s), ~{removed_edges} removed edge(s), "
            f"estimated cost {total_cost:.2f} WU."
        )
        return GraphChangeReport(
            affected_theorem_count=n,
            new_edges=new_edges,
            removed_edges=removed_edges,
            recompute_depth_max=depth_max,
            estimated_recompute_cost=total_cost,
            summary=summary,
        )

    def estimate_implementation_cost(
        self, plan: ImplementationPlan, budget: float = 1000.0
    ) -> CostEstimate:
        """
        Produce a detailed cost breakdown for *plan*.

        Parameters
        ----------
        plan:
            The implementation plan to estimate.
        budget:
            Budget against which to test feasibility.

        Returns
        -------
        CostEstimate
        """
        pack_cost = sum(t.estimated_cost for t in plan.pack_update_tasks)
        bridge_cost = sum(t.estimated_cost for t in plan.bridge_reindex_tasks)
        dep_cost = sum(t.estimated_cost for t in plan.dependency_recompute_tasks)
        total = pack_cost + bridge_cost + dep_cost

        breakdown: dict[str, float] = {
            "pack_update": pack_cost,
            "bridge_reindex": bridge_cost,
            "dependency_recompute": dep_cost,
        }
        return CostEstimate(
            plan_id=plan.plan_id,
            pack_update_cost=pack_cost,
            bridge_reindex_cost=bridge_cost,
            dependency_recompute_cost=dep_cost,
            total_cost=total,
            is_within_budget=total <= budget,
            breakdown=breakdown,
        )


# ---------------------------------------------------------------------------
# RegimeImplementationWitness
# ---------------------------------------------------------------------------


class RegimeImplementationWitness:
    """
    Produces lightweight, immutable audit records for key pipeline events.

    These records are suitable for forwarding to an external evidence channel
    (e.g. ``jugeo.evidence.channels``) without coupling this module to the
    full evidence subsystem.
    """

    def witness_impact_computation(
        self, change: RegimeChange, impact: ImpactSet
    ) -> ImpactWitnessReport:
        """
        Create a witness record for an impact-set computation.

        Parameters
        ----------
        change:
            The regime change that triggered the computation.
        impact:
            The computed impact set.

        Returns
        -------
        ImpactWitnessReport
        """
        affected_count = (
            len(impact.affected_pack_ids)
            + len(impact.affected_bridge_ids)
            + len(impact.affected_theorem_ids)
        )
        witness_id = _build_task_id("witness-impact", impact.impact_id)
        report = ImpactWitnessReport(
            witness_id=witness_id,
            change_id=change.change_id,
            scope_name=impact.scope.name,
            affected_count=affected_count,
            timestamp=_now_iso(),
        )
        log.debug("Impact witness record created: %s", witness_id)
        return report

    def witness_implementation_plan(
        self, plan: ImplementationPlan
    ) -> PlanWitnessReport:
        """
        Create a witness record for a plan creation event.

        Parameters
        ----------
        plan:
            The assembled implementation plan.

        Returns
        -------
        PlanWitnessReport
        """
        total_tasks = (
            len(plan.pack_update_tasks)
            + len(plan.bridge_reindex_tasks)
            + len(plan.dependency_recompute_tasks)
        )
        witness_id = _build_task_id("witness-plan", plan.plan_id)
        report = PlanWitnessReport(
            witness_id=witness_id,
            plan_id=plan.plan_id,
            total_tasks=total_tasks,
            estimated_cost=plan.total_estimated_cost,
            timestamp=_now_iso(),
        )
        log.debug("Plan witness record created: %s", witness_id)
        return report

    def witness_implementation_result(
        self, result: ImplementationResult
    ) -> ResultWitnessReport:
        """
        Create a witness record for a plan execution result.

        Parameters
        ----------
        result:
            The execution result to witness.

        Returns
        -------
        ResultWitnessReport
        """
        completed = (
            result.pack_tasks_completed
            + result.bridge_tasks_completed
            + result.dependency_tasks_completed
        )
        failed = (
            result.pack_tasks_failed
            + result.bridge_tasks_failed
            + result.dependency_tasks_failed
        )
        success = failed == 0
        witness_id = _build_task_id("witness-result", result.plan_id)
        report = ResultWitnessReport(
            witness_id=witness_id,
            plan_id=result.plan_id,
            completed_tasks=completed,
            failed_tasks=failed,
            total_cost=result.total_cost_incurred,
            success=success,
            timestamp=_now_iso(),
        )
        log.debug("Result witness record created: %s", witness_id)
        return report


# ---------------------------------------------------------------------------
# Module-level free functions
# ---------------------------------------------------------------------------


def run_consequence_cycle(
    regime_change: RegimeChange,
    config: ImplementationConfig | None = None,
) -> ConsequenceCycleResult:
    """
    Module-level convenience wrapper around
    ``RegimeImplementationCoordinator.run_consequence_cycle``.

    Creates a coordinator with *config* (or a default ``ImplementationConfig``
    if *config* is *None*) and immediately runs the full consequence cycle.

    Parameters
    ----------
    regime_change:
        The bootstrapping event to process.
    config:
        Optional coordinator configuration.  Defaults to
        ``ImplementationConfig()`` when *None*.

    Returns
    -------
    ConsequenceCycleResult
    """
    cfg = config if config is not None else ImplementationConfig()
    coordinator = RegimeImplementationCoordinator(cfg)
    return coordinator.run_consequence_cycle(regime_change)


def compute_impact_set(
    regime_change: RegimeChange,
    config: ImplementationConfig | None = None,
) -> ImpactSet:
    """
    Compute the impact set for a regime change without executing any tasks.

    Parameters
    ----------
    regime_change:
        The bootstrapping event to analyse.
    config:
        Optional coordinator configuration.

    Returns
    -------
    ImpactSet
    """
    cfg = config if config is not None else ImplementationConfig()
    coordinator = RegimeImplementationCoordinator(cfg)
    return coordinator.compute_impact_set(regime_change)


def build_implementation_plan(
    impact: ImpactSet,
    config: ImplementationConfig | None = None,
) -> ImplementationPlan:
    """
    Build a full ``ImplementationPlan`` from a pre-computed *impact* set.

    This is useful when the caller wants to inspect or modify the plan before
    executing it.

    Parameters
    ----------
    impact:
        The impact set describing affected artefacts.
    config:
        Optional coordinator configuration.

    Returns
    -------
    ImplementationPlan
    """
    cfg = config if config is not None else ImplementationConfig()
    coordinator = RegimeImplementationCoordinator(cfg)
    pack_tasks = coordinator.schedule_pack_updates(impact)
    bridge_tasks = coordinator.schedule_bridge_reindexing(impact)
    dep_tasks = coordinator.schedule_dependency_recomputation(impact)

    plan_id = _build_plan_id(impact.regime_change_id)
    total_cost = (
        sum(t.estimated_cost for t in pack_tasks)
        + sum(t.estimated_cost for t in bridge_tasks)
        + sum(t.estimated_cost for t in dep_tasks)
    )
    return ImplementationPlan(
        plan_id=plan_id,
        regime_change_id=impact.regime_change_id,
        pack_update_tasks=pack_tasks,
        bridge_reindex_tasks=bridge_tasks,
        dependency_recompute_tasks=dep_tasks,
        total_estimated_cost=total_cost,
        created_at=_now_iso(),
    )


# ---------------------------------------------------------------------------
# Smoke test / demo
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    )

    # --- Build a representative regime change ---
    change = RegimeChange(
        change_id="rc-demo-001",
        regime_name="TopologicalGroups",
        change_type="domain_formation",
        affected_domain_ids=("dom-tg", "dom-metric", "dom-homology"),
        affected_constructor_ids=("ctor-grp", "ctor-top", "ctor-ring"),
        affected_carrier_ids=("carr-set", "carr-point"),
        timestamp=_now_iso(),
        metadata={"author": "demo", "ticket": "JG-1024"},
    )

    print("=" * 70)
    print(f"Regime change  : {change.regime_name} ({change.change_id})")
    print(f"Change type    : {change.change_type}")
    print(f"Domains        : {change.affected_domain_ids}")
    print(f"Constructors   : {change.affected_constructor_ids}")
    print(f"Carriers       : {change.affected_carrier_ids}")
    print("=" * 70)

    # --- Run the consequence cycle ---
    config = ImplementationConfig(dry_run=False, cost_budget=5000.0)
    cycle_result = run_consequence_cycle(change, config)

    print("\n--- ConsequenceCycleResult ---")
    print(f"  cycle_id       : {cycle_result.cycle_id}")
    print(f"  impact_scope   : {cycle_result.impact_scope.name}")
    print(f"  total_tasks    : {cycle_result.total_tasks}")
    print(f"  completed      : {cycle_result.completed_tasks}")
    print(f"  failed         : {cycle_result.failed_tasks}")
    print(f"  total_cost     : {cycle_result.total_cost:.2f} WU")
    print(f"  duration       : {cycle_result.duration_seconds:.4f}s")
    print(f"  success        : {cycle_result.success}")

    # --- Analyzer reports ---
    impact = compute_impact_set(change, config)
    plan = build_implementation_plan(impact, config)
    analyzer = RegimeImplementationAnalyzer()

    scope_report = analyzer.analyze_impact_scope(impact)
    print("\n--- ImpactScopeReport ---")
    print(f"  scope          : {scope_report.scope.name}")
    print(f"  packs affected : {scope_report.pack_count}")
    print(f"  bridges        : {scope_report.bridge_count}")
    print(f"  theorems       : {scope_report.theorem_count}")
    print(f"  justification  : {scope_report.scope_justification}")

    cost_estimate = analyzer.estimate_implementation_cost(plan, budget=config.cost_budget)
    print("\n--- CostEstimate ---")
    print(f"  pack_update_cost        : {cost_estimate.pack_update_cost:.2f} WU")
    print(f"  bridge_reindex_cost     : {cost_estimate.bridge_reindex_cost:.2f} WU")
    print(f"  dependency_recompute    : {cost_estimate.dependency_recompute_cost:.2f} WU")
    print(f"  total_cost              : {cost_estimate.total_cost:.2f} WU")
    print(f"  within_budget           : {cost_estimate.is_within_budget}")

    graph_report = analyzer.analyze_dependency_graph_changes(plan.dependency_recompute_tasks)
    print("\n--- GraphChangeReport ---")
    print(f"  {graph_report.summary}")

    # --- Witness records ---
    witness = RegimeImplementationWitness()
    iw = witness.witness_impact_computation(change, impact)
    print(f"\n--- ImpactWitnessReport ---")
    print(f"  witness_id     : {iw.witness_id}")
    print(f"  affected_count : {iw.affected_count}")
    print(f"  scope_name     : {iw.scope_name}")

    pw = witness.witness_implementation_plan(plan)
    print(f"\n--- PlanWitnessReport ---")
    print(f"  witness_id     : {pw.witness_id}")
    print(f"  total_tasks    : {pw.total_tasks}")
    print(f"  estimated_cost : {pw.estimated_cost:.2f} WU")

    print("\nDone.")
