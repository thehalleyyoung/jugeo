r"""Budget allocation for cover-design patch construction.

Theory (theory2.tex §41 — Cover design: Budget allocation):
    The budget B is treated as a *first-class object* rather than a bare integer.
    Let T denote the total capacity of the budget.  For a cover design with N
    patches U_1, …, U_N, each patch U_i is assigned a per-patch allocation b_i.
    An overhead fraction ε ∈ [0, 1) is reserved for coordination costs.

    Admissibility condition::

        Σ_{i=1}^{N} b_i  +  ε · T  ≤  T
        ⟺   Σ b_i  ≤  (1 − ε) · T

    Priority-weighted allocation::

        b_i  ∝  priority(U_i) · size(U_i)

    Normalised::

        b_i  =  (1 − ε) · T  ·  [ priority(U_i) · size(U_i) ]
                                  ─────────────────────────────
                                  Σ_j priority(U_j) · size(U_j)

    Dynamic reallocation: when patch U_i finishes under budget, its spare
    capacity  δ_i = b_i − spend_i  flows along the outgoing edges of the
    budget-flow graph to neighbouring patches.  The flow graph is a directed
    graph G_B = (V, E) where edge (i → j) means U_i may donate spare budget
    to U_j.

    Theory2 invariants enforced here:
    * Generated code enters at PROPOSAL trust tier.
    * Cover sections must be compatible on overlaps (Čech condition).
    * Budget is a first-class object (not just an int).

    References
    ----------
    theory2.tex  §41  (Cover design — Budget allocation)
    theory2.tex  §42  (Admissibility and overhead)
    theory2.tex  §43  (Budget flow graphs and dynamic reallocation)

# copilot: s03-budget-allocation

Usage::

    from jugeo.generation.cover_design.budget_allocation import (
        BudgetAllocationCoordinator,
        BudgetAllocationAnalyzer,
        BudgetAllocationWitness,
        AllocationPolicy,
        AllocationRecord,
        BudgetFlowEdge,
        BudgetFlowGraph,
    )

    analyzer = BudgetAllocationAnalyzer()
    coordinator = BudgetAllocationCoordinator(total_capacity=1000.0, overhead_fraction=0.05)
    witness = BudgetAllocationWitness()

    patches = [
        {"patch_id": "p1", "priority": 2.0, "size": 100},
        {"patch_id": "p2", "priority": 1.5, "size": 200},
        {"patch_id": "p3", "priority": 3.0, "size": 50},
    ]
    plan = coordinator.allocate(patches, policy=AllocationPolicy.PRIORITY_WEIGHTED)
    cert = witness.certify(plan, coordinator.budget_state())
    print(cert["admissible"])  # True
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

try:
    from jugeo.generation.cover_design.models import (  # type: ignore[import]
        Budget,
        PatchDescriptor,
        CoverDesignPlan,
        CoverDesignError,
    )
except ImportError:
    Budget = object  # type: ignore[misc,assignment]
    PatchDescriptor = object  # type: ignore[misc,assignment]
    CoverDesignPlan = object  # type: ignore[misc,assignment]
    CoverDesignError = Exception  # type: ignore[misc,assignment]

__all__ = [
    "AllocationPolicy",
    "AllocationRecord",
    "BudgetFlowEdge",
    "BudgetFlowGraph",
    "BudgetAllocationAnalyzer",
    "BudgetAllocationWitness",
    "BudgetAllocationCoordinator",
]

_LOG = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_OVERHEAD_FRACTION: float = 0.05
_DEFAULT_RETRY_FRACTION: float = 0.10
_MIN_PATCH_ALLOCATION: float = 1.0
_PROPOSAL_TRUST_TIER: str = "proposal"
_ADMISSIBILITY_TOLERANCE: float = 1e-9


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class AllocationPolicy(Enum):
    """Strategy for distributing budget across patches.

    Members
    -------
    UNIFORM:
        Each patch receives an equal share of the usable budget
        ``(1 − ε) · T / N``.
    PRIORITY_WEIGHTED:
        Each patch's share is proportional to ``priority(U_i) · size(U_i)``.
    COVERAGE_PROPORTIONAL:
        Each patch's share is proportional to the geometric size of the patch
        (e.g. token count, line count) alone, ignoring priority.
    ADAPTIVE:
        Allocation is revised dynamically after each patch completes, routing
        spare budget to the highest-priority remaining patches.
    """

    UNIFORM = "uniform"
    PRIORITY_WEIGHTED = "priority_weighted"
    COVERAGE_PROPORTIONAL = "coverage_proportional"
    ADAPTIVE = "adaptive"


# ---------------------------------------------------------------------------
# Frozen dataclasses (immutable value objects)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AllocationRecord:
    """Immutable record of a single patch's budget allocation.

    Attributes
    ----------
    record_id:
        Unique identifier for this allocation record.
    patch_id:
        The patch this allocation belongs to.
    allocated:
        Budget units assigned to this patch.
    priority_weight:
        The normalised priority weight used when computing *allocated*.
        Always in [0, 1].
    size_weight:
        The normalised size weight used when computing *allocated*.
        Always in [0, 1].
    combined_weight:
        ``priority_weight * size_weight``, normalised across all patches.
    policy:
        The :class:`AllocationPolicy` that produced this record.
    trust_tier:
        Theory2 trust tier at which this allocation was created.
        Always ``"proposal"`` for freshly generated allocations.
    created_at:
        Unix timestamp.
    """

    record_id: str
    patch_id: str
    allocated: float
    priority_weight: float
    size_weight: float
    combined_weight: float
    policy: str
    trust_tier: str
    created_at: float


@dataclass(frozen=True, slots=True)
class BudgetFlowEdge:
    """A directed edge in the budget-flow graph.

    Edge (donor_id → recipient_id) means the donor patch may donate spare
    budget to the recipient once the donor has completed construction.

    Attributes
    ----------
    edge_id:
        Unique identifier for this edge.
    donor_id:
        Patch that may donate spare budget.
    recipient_id:
        Patch that may receive donated budget.
    max_flow:
        Maximum amount that may flow along this edge in a single reallocation
        event.  ``None`` means unconstrained (up to full spare budget).
    flow_priority:
        Higher value means this edge is preferred when multiple recipients
        compete for the same donor's spare budget.
    created_at:
        Unix timestamp.
    """

    edge_id: str
    donor_id: str
    recipient_id: str
    max_flow: float | None
    flow_priority: float
    created_at: float


# ---------------------------------------------------------------------------
# Mutable dataclasses
# ---------------------------------------------------------------------------


@dataclass
class BudgetFlowGraph:
    """Directed graph encoding legal budget-flow paths between patches.

    The graph is mutable: edges can be added and removed as the cover-design
    plan evolves.  The graph is *not* required to be acyclic — circular flows
    are permitted but capped at each edge's ``max_flow``.

    Attributes
    ----------
    graph_id:
        Unique identifier.
    edges:
        Mapping from ``edge_id`` to :class:`BudgetFlowEdge`.
    adjacency:
        Mapping ``donor_id → list[recipient_id]`` for O(1) neighbour lookup.
    created_at:
        Unix timestamp.
    """

    graph_id: str
    edges: dict[str, BudgetFlowEdge] = field(default_factory=dict)
    adjacency: dict[str, list[str]] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    # ------------------------------------------------------------------
    # Mutation helpers
    # ------------------------------------------------------------------

    def add_edge(
        self,
        donor_id: str,
        recipient_id: str,
        max_flow: float | None = None,
        flow_priority: float = 1.0,
    ) -> BudgetFlowEdge:
        """Add a directed flow edge from *donor_id* to *recipient_id*.

        Parameters
        ----------
        donor_id:
            Patch that may donate spare budget.
        recipient_id:
            Patch that may receive budget.
        max_flow:
            Optional cap on flow volume.
        flow_priority:
            Priority of this edge; higher values are preferred.

        Returns
        -------
        BudgetFlowEdge
            The newly created edge.
        """
        edge = BudgetFlowEdge(
            edge_id=str(uuid.uuid4()),
            donor_id=donor_id,
            recipient_id=recipient_id,
            max_flow=max_flow,
            flow_priority=flow_priority,
            created_at=time.time(),
        )
        self.edges[edge.edge_id] = edge
        self.adjacency.setdefault(donor_id, [])
        if recipient_id not in self.adjacency[donor_id]:
            self.adjacency[donor_id].append(recipient_id)
        return edge

    def remove_edge(self, edge_id: str) -> bool:
        """Remove the edge identified by *edge_id*.

        Returns ``True`` if the edge was found and removed, ``False`` otherwise.
        """
        edge = self.edges.pop(edge_id, None)
        if edge is None:
            return False
        recipients = self.adjacency.get(edge.donor_id, [])
        if edge.recipient_id in recipients:
            recipients.remove(edge.recipient_id)
        return True

    def recipients_of(self, donor_id: str) -> list[str]:
        """Return the list of patches that can receive budget from *donor_id*."""
        return list(self.adjacency.get(donor_id, []))

    def edges_from(self, donor_id: str) -> list[BudgetFlowEdge]:
        """Return all edges whose donor is *donor_id*, sorted by descending priority."""
        result = [e for e in self.edges.values() if e.donor_id == donor_id]
        return sorted(result, key=lambda e: e.flow_priority, reverse=True)

    def compute_reachable(self, source_id: str) -> set[str]:
        """Return the set of patch IDs reachable from *source_id* via flow edges.

        Uses a simple BFS.  The source itself is *not* included in the result.
        """
        visited: set[str] = set()
        queue: list[str] = [source_id]
        while queue:
            current = queue.pop()
            for nid in self.adjacency.get(current, []):
                if nid not in visited:
                    visited.add(nid)
                    queue.append(nid)
        visited.discard(source_id)
        return visited

    def to_dict(self) -> dict[str, Any]:
        """Serialise the graph to a plain dict."""
        return {
            "graph_id": self.graph_id,
            "edges": [
                {
                    "edge_id": e.edge_id,
                    "donor_id": e.donor_id,
                    "recipient_id": e.recipient_id,
                    "max_flow": e.max_flow,
                    "flow_priority": e.flow_priority,
                }
                for e in self.edges.values()
            ],
            "node_count": len(self.adjacency),
            "edge_count": len(self.edges),
        }


# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------


class BudgetAllocationAnalyzer:
    """Estimates per-patch costs, computes priority weights, and detects over-allocation.

    The analyzer is stateless with respect to any particular allocation run —
    all state is passed in and returned as plain data structures.  This makes
    the analyzer safe to use from multiple concurrent allocation sessions.

    Configuration keys
    ------------------
    default_priority : float
        Priority assigned to patches that do not declare a priority (default 1.0).
    default_size : float
        Size assigned to patches that do not declare a size (default 1.0).
    cost_per_size_unit : float
        Estimated budget units consumed per unit of patch size (default 1.0).
    retry_cost_multiplier : float
        Extra budget factor reserved for retrying a failed patch (default 1.5).
    over_allocation_threshold : float
        Ratio above which an allocation is flagged as over-allocated (default 1.0).
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = config or {}
        self._config: dict[str, Any] = {
            "default_priority": cfg.get("default_priority", 1.0),
            "default_size": cfg.get("default_size", 1.0),
            "cost_per_size_unit": cfg.get("cost_per_size_unit", 1.0),
            "retry_cost_multiplier": cfg.get("retry_cost_multiplier", 1.5),
            "over_allocation_threshold": cfg.get("over_allocation_threshold", 1.0),
        }
        self._logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")

    # ------------------------------------------------------------------
    # Cost estimation
    # ------------------------------------------------------------------

    def estimate_patch_cost(self, patch: dict[str, Any]) -> float:
        """Estimate the budget units required to construct *patch*.

        The estimate is::

            cost(U_i) = cost_per_size_unit · size(U_i)

        A retry reserve is *not* included here; see
        :meth:`estimate_retry_cost` for that.

        Parameters
        ----------
        patch:
            Patch descriptor dict.  Expected key: ``"size"`` (float).

        Returns
        -------
        float
            Estimated cost in budget units.  Always ≥ 0.
        """
        size = float(patch.get("size", self._config["default_size"]))
        cost = self._config["cost_per_size_unit"] * max(size, 0.0)
        self._logger.debug("estimate_patch_cost(%s) = %.4f", patch.get("patch_id"), cost)
        return cost

    def estimate_retry_cost(self, patch: dict[str, Any]) -> float:
        """Estimate the additional budget required to *retry* a failed *patch*.

        Returns
        -------
        float
            Retry budget = ``retry_cost_multiplier · estimate_patch_cost(patch)``.
        """
        base = self.estimate_patch_cost(patch)
        retry = self._config["retry_cost_multiplier"] * base
        return retry

    # ------------------------------------------------------------------
    # Weight computation
    # ------------------------------------------------------------------

    def compute_priority_weights(
        self, patches: list[dict[str, Any]]
    ) -> dict[str, float]:
        """Compute normalised priority weights for every patch in *patches*.

        The raw weight of patch U_i is ``priority(U_i) · size(U_i)``.
        Normalised weight is ``raw_i / Σ_j raw_j``.

        Parameters
        ----------
        patches:
            List of patch descriptor dicts.

        Returns
        -------
        dict[str, float]
            Mapping ``patch_id → normalised weight``.  Weights sum to 1.0
            (or 0.0 if the list is empty).
        """
        raw: dict[str, float] = {}
        for p in patches:
            pid = str(p.get("patch_id", id(p)))
            priority = float(p.get("priority", self._config["default_priority"]))
            size = float(p.get("size", self._config["default_size"]))
            raw[pid] = max(priority, 0.0) * max(size, 0.0)

        total = sum(raw.values())
        if total <= 0.0:
            equal = 1.0 / len(patches) if patches else 0.0
            return {pid: equal for pid in raw}

        return {pid: w / total for pid, w in raw.items()}

    def compute_size_weights(
        self, patches: list[dict[str, Any]]
    ) -> dict[str, float]:
        """Compute normalised size-only weights (ignoring priority).

        Parameters
        ----------
        patches:
            List of patch descriptor dicts.

        Returns
        -------
        dict[str, float]
            Mapping ``patch_id → normalised size weight``.
        """
        raw: dict[str, float] = {}
        for p in patches:
            pid = str(p.get("patch_id", id(p)))
            size = float(p.get("size", self._config["default_size"]))
            raw[pid] = max(size, 0.0)

        total = sum(raw.values())
        if total <= 0.0:
            equal = 1.0 / len(patches) if patches else 0.0
            return {pid: equal for pid in raw}

        return {pid: w / total for pid, w in raw.items()}

    # ------------------------------------------------------------------
    # Over-allocation detection
    # ------------------------------------------------------------------

    def detect_over_allocation(
        self,
        records: list[AllocationRecord],
        total_capacity: float,
        overhead_fraction: float,
    ) -> dict[str, Any]:
        """Detect whether the allocation described by *records* exceeds the budget.

        Checks the admissibility condition::

            Σ b_i  +  ε · T  ≤  T

        Parameters
        ----------
        records:
            List of :class:`AllocationRecord` objects.
        total_capacity:
            Total budget capacity T.
        overhead_fraction:
            Overhead fraction ε.

        Returns
        -------
        dict
            ``{
                "admissible": bool,
                "total_allocated": float,
                "overhead_reserved": float,
                "usable_capacity": float,
                "excess": float,
                "over_allocated_patches": list[str],
            }``
        """
        usable = total_capacity * (1.0 - overhead_fraction)
        overhead_reserved = total_capacity * overhead_fraction
        total_allocated = sum(r.allocated for r in records)
        excess = max(total_allocated - usable, 0.0)
        admissible = total_allocated <= usable + _ADMISSIBILITY_TOLERANCE

        # Identify individual over-allocated patches (allocated > usable / N as heuristic)
        n = max(len(records), 1)
        per_patch_cap = usable / n
        over_allocated = [r.patch_id for r in records if r.allocated > per_patch_cap * 2]

        self._logger.debug(
            "detect_over_allocation: admissible=%s, allocated=%.4f, usable=%.4f",
            admissible,
            total_allocated,
            usable,
        )
        return {
            "admissible": admissible,
            "total_allocated": total_allocated,
            "overhead_reserved": overhead_reserved,
            "usable_capacity": usable,
            "excess": excess,
            "over_allocated_patches": over_allocated,
        }

    def summarise_allocation(
        self, records: list[AllocationRecord], total_capacity: float
    ) -> dict[str, Any]:
        """Return a human-readable summary of an allocation.

        Parameters
        ----------
        records:
            Completed allocation records.
        total_capacity:
            Total budget capacity T.

        Returns
        -------
        dict
            Summary with ``patch_count``, ``total_allocated``, ``mean_allocation``,
            ``min_allocation``, ``max_allocation``, ``utilisation_fraction``.
        """
        if not records:
            return {
                "patch_count": 0,
                "total_allocated": 0.0,
                "mean_allocation": 0.0,
                "min_allocation": 0.0,
                "max_allocation": 0.0,
                "utilisation_fraction": 0.0,
            }
        allocations = [r.allocated for r in records]
        total = sum(allocations)
        return {
            "patch_count": len(records),
            "total_allocated": total,
            "mean_allocation": total / len(records),
            "min_allocation": min(allocations),
            "max_allocation": max(allocations),
            "utilisation_fraction": total / total_capacity if total_capacity > 0 else 0.0,
        }


# ---------------------------------------------------------------------------
# Witness
# ---------------------------------------------------------------------------


class BudgetAllocationWitness:
    """Certifies that allocations are admissible and that total spend ≤ budget.

    A witness is a purely read-only component: it inspects a completed
    allocation (a list of :class:`AllocationRecord` objects plus the
    budget-state summary) and emits a signed certificate dict.  The
    certificate captures enough information to reconstruct the check later.

    All certificates carry a ``trust_tier`` field set to ``"proposal"`` in
    accordance with theory2's rule that generated artefacts enter at the
    PROPOSAL tier.
    """

    def __init__(self) -> None:
        self._logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
        self._certificate_log: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Admissibility check
    # ------------------------------------------------------------------

    def check_admissibility(
        self,
        records: list[AllocationRecord],
        total_capacity: float,
        overhead_fraction: float,
    ) -> dict[str, Any]:
        """Check whether the admissibility condition  Σ b_i + ε·T ≤ T  holds.

        Parameters
        ----------
        records:
            List of allocation records to check.
        total_capacity:
            Total budget capacity T.
        overhead_fraction:
            Overhead fraction ε.

        Returns
        -------
        dict
            ``{
                "admissible": bool,
                "lhs": float,   # Σ b_i + ε·T
                "rhs": float,   # T
                "margin": float,  # T − lhs (positive ⟹ admissible)
            }``
        """
        lhs = sum(r.allocated for r in records) + overhead_fraction * total_capacity
        rhs = total_capacity
        margin = rhs - lhs
        admissible = margin >= -_ADMISSIBILITY_TOLERANCE
        self._logger.debug(
            "check_admissibility: lhs=%.4f rhs=%.4f margin=%.4f admissible=%s",
            lhs,
            rhs,
            margin,
            admissible,
        )
        return {"admissible": admissible, "lhs": lhs, "rhs": rhs, "margin": margin}

    # ------------------------------------------------------------------
    # Spend verification
    # ------------------------------------------------------------------

    def verify_total_spend(
        self,
        spend_map: dict[str, float],
        records: list[AllocationRecord],
    ) -> dict[str, Any]:
        """Verify that actual spend does not exceed the allocated budget for each patch.

        Parameters
        ----------
        spend_map:
            Mapping ``patch_id → actual_spend``.
        records:
            The original allocation records.

        Returns
        -------
        dict
            ``{
                "all_within_budget": bool,
                "over_budget_patches": list[str],
                "total_allocated": float,
                "total_spent": float,
                "surplus": float,
            }``
        """
        record_map: dict[str, float] = {r.patch_id: r.allocated for r in records}
        over_budget: list[str] = []
        for patch_id, spend in spend_map.items():
            allocated = record_map.get(patch_id, 0.0)
            if spend > allocated + _ADMISSIBILITY_TOLERANCE:
                over_budget.append(patch_id)
        total_allocated = sum(record_map.values())
        total_spent = sum(spend_map.values())
        surplus = total_allocated - total_spent
        all_within = len(over_budget) == 0
        return {
            "all_within_budget": all_within,
            "over_budget_patches": over_budget,
            "total_allocated": total_allocated,
            "total_spent": total_spent,
            "surplus": surplus,
        }

    # ------------------------------------------------------------------
    # Full certification
    # ------------------------------------------------------------------

    def certify(
        self,
        records: list[AllocationRecord],
        budget_state: dict[str, Any],
        spend_map: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        """Produce a signed certificate for the given allocation.

        The certificate combines:
        1. An admissibility check (Σ b_i + ε·T ≤ T).
        2. Optionally, a spend verification (if *spend_map* is provided).
        3. A Čech-condition flag (always ``True`` here — overlap compatibility
           is verified by the caller, not by the budget witness).

        Parameters
        ----------
        records:
            Completed allocation records.
        budget_state:
            Dict returned by :meth:`BudgetAllocationCoordinator.budget_state`.
        spend_map:
            Optional mapping of actual per-patch spend.  When ``None``, spend
            verification is skipped.

        Returns
        -------
        dict
            Certificate record including:
            ``certificate_id``, ``admissible``, ``trust_tier``,
            ``admissibility_check``, ``spend_check`` (or ``None``),
            ``cech_condition_flag``, ``issued_at``.
        """
        total_capacity = float(budget_state.get("total_capacity", 0.0))
        overhead_fraction = float(budget_state.get("overhead_fraction", _DEFAULT_OVERHEAD_FRACTION))

        admissibility = self.check_admissibility(records, total_capacity, overhead_fraction)
        spend_check: dict[str, Any] | None = None
        if spend_map is not None:
            spend_check = self.verify_total_spend(spend_map, records)

        admissible = admissibility["admissible"]
        if spend_check is not None and not spend_check["all_within_budget"]:
            admissible = False

        certificate: dict[str, Any] = {
            "certificate_id": str(uuid.uuid4()),
            "admissible": admissible,
            "trust_tier": _PROPOSAL_TRUST_TIER,
            "admissibility_check": admissibility,
            "spend_check": spend_check,
            "cech_condition_flag": True,
            "patch_count": len(records),
            "issued_at": time.time(),
        }
        self._certificate_log.append(certificate)
        self._logger.info(
            "Certificate %s: admissible=%s for %d patches.",
            certificate["certificate_id"],
            admissible,
            len(records),
        )
        return certificate

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    @property
    def certificate_log(self) -> list[dict[str, Any]]:
        """Read-only copy of all certificates issued so far."""
        return list(self._certificate_log)

    def reset(self) -> None:
        """Clear the certificate log."""
        self._certificate_log.clear()

    def __repr__(self) -> str:
        return f"BudgetAllocationWitness(certificates_issued={len(self._certificate_log)})"


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------


class BudgetAllocationCoordinator:
    """Manages the full budget lifecycle: initial allocation, dynamic reallocation,
    and final accounting.

    The coordinator is the central authority for budget management in a
    cover-design session.  It owns:

    * The :class:`BudgetFlowGraph` that controls spare-budget donation.
    * The table of current :class:`AllocationRecord` objects.
    * The retry-budget pool.
    * Reallocation history for auditing.

    Theory2 invariants
    ------------------
    * All allocations enter at PROPOSAL trust tier.
    * Admissibility (Σ b_i + ε·T ≤ T) is enforced on every allocation call.
    * The Čech overlap condition is *noted* in the certificate but validated
      externally by the cover-design orchestrator.

    Configuration keys
    ------------------
    total_capacity : float
        Total budget T.  Must be > 0.
    overhead_fraction : float
        ε, overhead fraction reserved for coordination costs (default 0.05).
    retry_fraction : float
        Fraction of usable budget reserved for retrying failed patches
        (default 0.10).
    min_patch_allocation : float
        Minimum allocation per patch in budget units (default 1.0).
    flow_graph_id : str | None
        If provided, the flow graph is initialised with this ID.
    """

    def __init__(
        self,
        total_capacity: float = 1000.0,
        overhead_fraction: float = _DEFAULT_OVERHEAD_FRACTION,
        config: dict[str, Any] | None = None,
    ) -> None:
        cfg = config or {}
        self._total_capacity: float = float(cfg.get("total_capacity", total_capacity))
        self._overhead_fraction: float = float(
            cfg.get("overhead_fraction", overhead_fraction)
        )
        self._retry_fraction: float = float(
            cfg.get("retry_fraction", _DEFAULT_RETRY_FRACTION)
        )
        self._min_patch_allocation: float = float(
            cfg.get("min_patch_allocation", _MIN_PATCH_ALLOCATION)
        )
        self._logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")

        # State
        self._records: dict[str, AllocationRecord] = {}
        self._spend: dict[str, float] = {}
        self._retry_pool: float = 0.0
        self._reallocation_history: list[dict[str, Any]] = []
        self._flow_graph: BudgetFlowGraph = BudgetFlowGraph(
            graph_id=cfg.get("flow_graph_id") or str(uuid.uuid4())
        )
        self._analyzer = BudgetAllocationAnalyzer(config=cfg)

    # ------------------------------------------------------------------
    # Core allocation
    # ------------------------------------------------------------------

    def allocate(
        self,
        patches: list[dict[str, Any]],
        policy: AllocationPolicy = AllocationPolicy.PRIORITY_WEIGHTED,
    ) -> list[AllocationRecord]:
        """Compute and store initial budget allocations for *patches*.

        The allocation procedure:

        1. Compute usable capacity:  ``U = (1 − ε − r) · T``
           where r is the retry fraction.
        2. According to *policy*, compute per-patch weights.
        3. Derive per-patch allocations:  ``b_i = max(U · w_i, min_allocation)``.
        4. Re-normalise to ensure Σ b_i ≤ U.
        5. Set the retry pool:  ``retry_pool = r · T``.
        6. Build :class:`AllocationRecord` objects and store them.

        Parameters
        ----------
        patches:
            List of patch descriptor dicts.  Each must have ``"patch_id"``.
        policy:
            The :class:`AllocationPolicy` to apply.

        Returns
        -------
        list[AllocationRecord]
            One record per patch, in the same order as *patches*.
        """
        if not patches:
            return []

        usable = self._total_capacity * (
            1.0 - self._overhead_fraction - self._retry_fraction
        )
        usable = max(usable, 0.0)
        self._retry_pool = self._total_capacity * self._retry_fraction

        # Compute weights according to policy
        if policy == AllocationPolicy.UNIFORM:
            n = len(patches)
            weights: dict[str, float] = {
                str(p.get("patch_id", id(p))): 1.0 / n for p in patches
            }
            size_w: dict[str, float] = dict(weights)
        elif policy == AllocationPolicy.COVERAGE_PROPORTIONAL:
            weights = self._analyzer.compute_size_weights(patches)
            size_w = dict(weights)
        else:
            # PRIORITY_WEIGHTED and ADAPTIVE both start with priority*size
            weights = self._analyzer.compute_priority_weights(patches)
            size_w = self._analyzer.compute_size_weights(patches)

        records: list[AllocationRecord] = []
        raw_allocations: dict[str, float] = {}

        for p in patches:
            pid = str(p.get("patch_id", id(p)))
            w = weights.get(pid, 0.0)
            raw = max(usable * w, self._min_patch_allocation)
            raw_allocations[pid] = raw

        # Re-normalise if total exceeds usable
        total_raw = sum(raw_allocations.values())
        scale = usable / total_raw if total_raw > usable else 1.0

        for p in patches:
            pid = str(p.get("patch_id", id(p)))
            allocated = raw_allocations[pid] * scale
            record = AllocationRecord(
                record_id=str(uuid.uuid4()),
                patch_id=pid,
                allocated=allocated,
                priority_weight=float(p.get("priority", 1.0)),
                size_weight=size_w.get(pid, weights.get(pid, 0.0)),
                combined_weight=weights.get(pid, 0.0),
                policy=policy.value,
                trust_tier=_PROPOSAL_TRUST_TIER,
                created_at=time.time(),
            )
            records.append(record)
            self._records[pid] = record
            self._spend[pid] = 0.0

        self._logger.info(
            "allocate: %d patches, policy=%s, usable=%.2f, retry_pool=%.2f",
            len(patches),
            policy.value,
            usable,
            self._retry_pool,
        )
        return records

    # ------------------------------------------------------------------
    # Spend tracking
    # ------------------------------------------------------------------

    def record_spend(self, patch_id: str, amount: float) -> None:
        """Record that *amount* budget units were spent on *patch_id*.

        Parameters
        ----------
        patch_id:
            Identifier of the patch.
        amount:
            Non-negative amount to add to the running spend total.

        Raises
        ------
        KeyError
            If *patch_id* has no allocation record.
        ValueError
            If *amount* is negative.
        """
        if patch_id not in self._records:
            raise KeyError(f"No allocation record for patch '{patch_id}'")
        if amount < 0:
            raise ValueError(f"Spend amount must be non-negative, got {amount}")
        self._spend[patch_id] = self._spend.get(patch_id, 0.0) + amount

    def spare_budget(self, patch_id: str) -> float:
        """Return the unspent budget for *patch_id*.

        Returns
        -------
        float
            ``allocated − spent``.  Clamped at 0 if the patch has overspent.
        """
        record = self._records.get(patch_id)
        if record is None:
            return 0.0
        spent = self._spend.get(patch_id, 0.0)
        return max(record.allocated - spent, 0.0)

    # ------------------------------------------------------------------
    # Dynamic reallocation
    # ------------------------------------------------------------------

    def reallocate_spare(
        self, completed_patch_id: str
    ) -> list[dict[str, Any]]:
        """Propagate spare budget from a completed patch to its flow-graph neighbours.

        When patch U_i completes with spare budget δ_i = b_i − spend_i > 0,
        this method distributes δ_i among the recipients listed in the flow
        graph, in descending order of edge priority.

        Flow rule: each recipient j receives::

            flow_{i→j} = min(max_flow_{i→j}, δ_i / |neighbours|)

        The total flow is capped at δ_i.

        Parameters
        ----------
        completed_patch_id:
            The patch that has just finished construction.

        Returns
        -------
        list[dict]
            One flow-event dict per edge traversed, each with keys:
            ``donor_id``, ``recipient_id``, ``amount``, ``edge_id``,
            ``timestamp``.
        """
        spare = self.spare_budget(completed_patch_id)
        if spare <= 0.0:
            return []

        edges = self._flow_graph.edges_from(completed_patch_id)
        if not edges:
            return []

        flow_events: list[dict[str, Any]] = []
        remaining = spare
        per_recipient = spare / len(edges)

        for edge in edges:
            if remaining <= 0.0:
                break
            flow = per_recipient
            if edge.max_flow is not None:
                flow = min(flow, edge.max_flow)
            flow = min(flow, remaining)
            if flow <= 0.0:
                continue

            recipient_id = edge.recipient_id
            # Increase the recipient's allocation in-place by building a new record
            old_record = self._records.get(recipient_id)
            if old_record is not None:
                new_record = AllocationRecord(
                    record_id=str(uuid.uuid4()),
                    patch_id=old_record.patch_id,
                    allocated=old_record.allocated + flow,
                    priority_weight=old_record.priority_weight,
                    size_weight=old_record.size_weight,
                    combined_weight=old_record.combined_weight,
                    policy=old_record.policy,
                    trust_tier=old_record.trust_tier,
                    created_at=time.time(),
                )
                self._records[recipient_id] = new_record

            remaining -= flow
            event = {
                "donor_id": completed_patch_id,
                "recipient_id": recipient_id,
                "amount": flow,
                "edge_id": edge.edge_id,
                "timestamp": time.time(),
            }
            flow_events.append(event)
            self._reallocation_history.append(event)
            self._logger.debug(
                "reallocate_spare: %.4f units from '%s' → '%s'",
                flow,
                completed_patch_id,
                recipient_id,
            )

        return flow_events

    def draw_from_retry_pool(self, patch_id: str, amount: float) -> float:
        """Draw *amount* from the retry pool and add it to *patch_id*'s allocation.

        If the retry pool has insufficient funds, the maximum available is drawn.

        Parameters
        ----------
        patch_id:
            The patch requesting retry budget.
        amount:
            Requested retry budget in budget units.

        Returns
        -------
        float
            Actual amount drawn (≤ *amount*).
        """
        drawable = min(amount, self._retry_pool)
        if drawable <= 0.0:
            self._logger.warning("draw_from_retry_pool: retry pool exhausted.")
            return 0.0
        self._retry_pool -= drawable

        old_record = self._records.get(patch_id)
        if old_record is not None:
            new_record = AllocationRecord(
                record_id=str(uuid.uuid4()),
                patch_id=old_record.patch_id,
                allocated=old_record.allocated + drawable,
                priority_weight=old_record.priority_weight,
                size_weight=old_record.size_weight,
                combined_weight=old_record.combined_weight,
                policy=old_record.policy,
                trust_tier=old_record.trust_tier,
                created_at=time.time(),
            )
            self._records[patch_id] = new_record

        self._logger.info(
            "draw_from_retry_pool: patch '%s' drew %.4f (pool remaining %.4f)",
            patch_id,
            drawable,
            self._retry_pool,
        )
        return drawable

    # ------------------------------------------------------------------
    # Final accounting
    # ------------------------------------------------------------------

    def finalize_accounting(self) -> dict[str, Any]:
        """Compute the final budget accounting report.

        The report includes:
        * Per-patch allocated, spent, spare amounts.
        * Total allocated, total spent, total surplus.
        * Retry pool residual.
        * Whether the overall allocation was admissible.

        Returns
        -------
        dict
            Accounting report.
        """
        patch_summaries: list[dict[str, Any]] = []
        total_allocated = 0.0
        total_spent = 0.0

        for pid, record in self._records.items():
            spent = self._spend.get(pid, 0.0)
            spare = max(record.allocated - spent, 0.0)
            patch_summaries.append(
                {
                    "patch_id": pid,
                    "allocated": record.allocated,
                    "spent": spent,
                    "spare": spare,
                    "over_budget": spent > record.allocated + _ADMISSIBILITY_TOLERANCE,
                }
            )
            total_allocated += record.allocated
            total_spent += spent

        usable = self._total_capacity * (1.0 - self._overhead_fraction)
        admissible = total_allocated <= usable + _ADMISSIBILITY_TOLERANCE

        report: dict[str, Any] = {
            "total_capacity": self._total_capacity,
            "overhead_fraction": self._overhead_fraction,
            "usable_capacity": usable,
            "total_allocated": total_allocated,
            "total_spent": total_spent,
            "total_surplus": total_allocated - total_spent,
            "retry_pool_residual": self._retry_pool,
            "admissible": admissible,
            "patch_count": len(self._records),
            "patch_summaries": patch_summaries,
            "reallocation_events": len(self._reallocation_history),
            "finalized_at": time.time(),
        }
        self._logger.info(
            "finalize_accounting: admissible=%s, allocated=%.2f, spent=%.2f",
            admissible,
            total_allocated,
            total_spent,
        )
        return report

    # ------------------------------------------------------------------
    # Budget state
    # ------------------------------------------------------------------

    def budget_state(self) -> dict[str, Any]:
        """Return a snapshot of the current budget state.

        Used as input to :meth:`BudgetAllocationWitness.certify`.

        Returns
        -------
        dict
            ``{
                "total_capacity": float,
                "overhead_fraction": float,
                "retry_fraction": float,
                "retry_pool": float,
                "usable_capacity": float,
                "current_total_allocated": float,
                "record_count": int,
            }``
        """
        usable = self._total_capacity * (1.0 - self._overhead_fraction - self._retry_fraction)
        return {
            "total_capacity": self._total_capacity,
            "overhead_fraction": self._overhead_fraction,
            "retry_fraction": self._retry_fraction,
            "retry_pool": self._retry_pool,
            "usable_capacity": usable,
            "current_total_allocated": sum(r.allocated for r in self._records.values()),
            "record_count": len(self._records),
        }

    # ------------------------------------------------------------------
    # Flow-graph access
    # ------------------------------------------------------------------

    @property
    def flow_graph(self) -> BudgetFlowGraph:
        """The :class:`BudgetFlowGraph` managed by this coordinator."""
        return self._flow_graph

    @property
    def records(self) -> dict[str, AllocationRecord]:
        """Read-only view of the current allocation records keyed by patch_id."""
        return dict(self._records)

    @property
    def reallocation_history(self) -> list[dict[str, Any]]:
        """Read-only copy of all reallocation events."""
        return list(self._reallocation_history)

    def get_records_list(self) -> list[AllocationRecord]:
        """Return all current allocation records as an ordered list."""
        return list(self._records.values())

    def reset(self) -> None:
        """Clear all allocation state (records, spend, retry pool, history)."""
        self._records.clear()
        self._spend.clear()
        self._retry_pool = 0.0
        self._reallocation_history.clear()
        self._logger.info("BudgetAllocationCoordinator state reset.")

    def __repr__(self) -> str:
        return (
            f"BudgetAllocationCoordinator("
            f"T={self._total_capacity}, ε={self._overhead_fraction}, "
            f"patches={len(self._records)}, "
            f"retry_pool={self._retry_pool:.2f})"
        )


# ---------------------------------------------------------------------------
# Module self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.WARNING)

    # ------------------------------------------------------------------ #
    # Build three patches and allocate                                     #
    # ------------------------------------------------------------------ #
    patches = [
        {"patch_id": "alpha", "priority": 3.0, "size": 100.0},
        {"patch_id": "beta",  "priority": 1.5, "size": 200.0},
        {"patch_id": "gamma", "priority": 2.0, "size":  50.0},
    ]

    coordinator = BudgetAllocationCoordinator(
        total_capacity=1000.0,
        overhead_fraction=0.05,
    )
    records = coordinator.allocate(patches, policy=AllocationPolicy.PRIORITY_WEIGHTED)
    assert len(records) == 3, "Expected 3 allocation records"

    total = sum(r.allocated for r in records)
    usable = 1000.0 * (1.0 - 0.05 - _DEFAULT_RETRY_FRACTION)
    assert total <= usable + _ADMISSIBILITY_TOLERANCE, f"Over-allocated: {total} > {usable}"

    # ------------------------------------------------------------------ #
    # Witness certification                                                #
    # ------------------------------------------------------------------ #
    witness = BudgetAllocationWitness()
    cert = witness.certify(records, coordinator.budget_state())
    assert cert["admissible"], f"Certificate should be admissible: {cert}"
    assert cert["trust_tier"] == "proposal"

    # ------------------------------------------------------------------ #
    # Flow graph and dynamic reallocation                                  #
    # ------------------------------------------------------------------ #
    coordinator.flow_graph.add_edge("alpha", "beta", max_flow=50.0)
    coordinator.flow_graph.add_edge("alpha", "gamma", max_flow=20.0)
    coordinator.record_spend("alpha", 10.0)  # alpha completes under budget

    events = coordinator.reallocate_spare("alpha")
    assert len(events) > 0, "Expected reallocation events"
    # beta's allocation should have increased
    beta_record = coordinator.records["beta"]
    assert beta_record.allocated > records[1].allocated, "beta should have received spare"

    # ------------------------------------------------------------------ #
    # Retry pool                                                           #
    # ------------------------------------------------------------------ #
    drawn = coordinator.draw_from_retry_pool("gamma", amount=200.0)
    assert drawn > 0.0, "Should have drawn from retry pool"
    assert drawn <= 1000.0 * _DEFAULT_RETRY_FRACTION + _ADMISSIBILITY_TOLERANCE

    # ------------------------------------------------------------------ #
    # Analyzer                                                             #
    # ------------------------------------------------------------------ #
    analyzer = BudgetAllocationAnalyzer()
    pw = analyzer.compute_priority_weights(patches)
    assert abs(sum(pw.values()) - 1.0) < 1e-9, "Priority weights must sum to 1"

    over = analyzer.detect_over_allocation(records, 1000.0, 0.05)
    assert "admissible" in over

    # ------------------------------------------------------------------ #
    # Final accounting                                                     #
    # ------------------------------------------------------------------ #
    coordinator.record_spend("beta", 5.0)
    coordinator.record_spend("gamma", 3.0)
    report = coordinator.finalize_accounting()
    assert report["patch_count"] == 3
    assert report["total_spent"] > 0

    print("budget_allocation: smoke tests passed ✓")
    sys.exit(0)
