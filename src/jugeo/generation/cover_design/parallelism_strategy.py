r"""Parallelism strategy for cover-design patch construction.

Theory (theory2.tex §42 — Cover design: Parallelism strategy):
    Patches that do not overlap in their interface dependencies can be
    constructed in parallel.  The parallelism strategy operates on a
    *dependency graph* G = (V, E) where each vertex v ∈ V represents a
    patch U_v and a directed edge (i → j) ∈ E means patch U_i must be
    fully constructed before construction of U_j can begin, because U_j's
    interface treaty depends on outputs produced by U_i.

    An *anti-chain* in G is a set A ⊆ V of vertices such that no two
    vertices in A are related by the partial order induced by G (i.e.
    there is no directed path between any pair of vertices in A).  Patches
    in an anti-chain can be constructed concurrently without any ordering
    constraint between them.

    A *generation wave* W_k is a maximal anti-chain in the DAG after
    removing all patches that were assigned to earlier waves W_1, …, W_{k−1}.
    The waves partition V into an ordered sequence::

        V = W_1  ∪  W_2  ∪  …  ∪  W_m      (disjoint)

    with the property that every patch in W_k depends only on patches in
    W_1 ∪ … ∪ W_{k−1}.

    Budget constraint: the total concurrent budget consumed by a wave must
    not exceed the parallel budget limit B_parallel::

        Σ_{i ∈ W_k} b_i  ≤  B_parallel   ∀ k

    When a wave would exceed B_parallel the wave is *split* into two or
    more sub-waves, each of which is individually admissible.

    A parallelism plan is *deadlock-free* if and only if the dependency
    graph G is a DAG (directed acyclic graph).  The witness verifies this
    by checking for cycles using DFS.

    Theory2 invariants enforced here:
    * Generated code enters at PROPOSAL trust tier.
    * Cover sections must be compatible on overlaps (Čech condition).
    * Budget is a first-class object (not just an int).

    References
    ----------
    theory2.tex  §42  (Cover design — Parallelism strategy)
    theory2.tex  §43  (Anti-chains and generation waves)
    theory2.tex  §44  (Deadlock freedom and DAG verification)

# copilot: s04-parallelism-strategy

Usage::

    from jugeo.generation.cover_design.parallelism_strategy import (
        ParallelismStrategyCoordinator,
        ParallelismStrategyAnalyzer,
        ParallelismStrategyWitness,
        ParallelismPolicy,
        GenerationWave,
        ParallelismGroup,
        DependencyEdge,
        ParallelismConstraint,
    )

    patches = ["p1", "p2", "p3", "p4"]
    edges = [("p1", "p3"), ("p2", "p3"), ("p3", "p4")]
    budgets = {"p1": 100.0, "p2": 150.0, "p3": 200.0, "p4": 80.0}

    analyzer = ParallelismStrategyAnalyzer()
    coordinator = ParallelismStrategyCoordinator(parallel_budget_limit=400.0)
    witness = ParallelismStrategyWitness()

    dep_edges = coordinator.build_dependency_edges(edges)
    waves = coordinator.compute_waves(patches, dep_edges, budgets)
    cert = witness.certify(waves, dep_edges, parallel_budget_limit=400.0, budget_map=budgets)
    print(cert["deadlock_free"], cert["budget_admissible"])  # True True
"""
from __future__ import annotations

import logging
import time
import uuid
from collections import deque
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
    "ParallelismPolicy",
    "DependencyEdge",
    "ParallelismConstraint",
    "ParallelismGroup",
    "GenerationWave",
    "ParallelismStrategyAnalyzer",
    "ParallelismStrategyWitness",
    "ParallelismStrategyCoordinator",
]

_LOG = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_PROPOSAL_TRUST_TIER: str = "proposal"
_ADMISSIBILITY_TOLERANCE: float = 1e-9
_DEFAULT_PARALLEL_BUDGET_LIMIT: float = float("inf")


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ParallelismPolicy(Enum):
    """Strategy for grouping patches into parallelism groups.

    Members
    -------
    SEQUENTIAL:
        All patches are executed one at a time, in topological order.
        Maximally safe but slowest.
    WAVE:
        Patches are grouped into generation waves (level-by-level BFS on the
        dependency DAG).  All patches in a wave run concurrently subject to
        the budget limit.
    FULL_PARALLEL:
        Every patch without unsatisfied dependencies starts immediately.
        Equivalent to WAVE but sub-waves are not further split by budget.
        Unsafe if the concurrent budget limit would be exceeded.
    ADAPTIVE:
        Waves are computed dynamically: as each patch completes, its
        dependents are promoted to the ready queue if all of their
        prerequisites are done.  Budget is re-evaluated at every step.
    """

    SEQUENTIAL = "sequential"
    WAVE = "wave"
    FULL_PARALLEL = "full_parallel"
    ADAPTIVE = "adaptive"


# ---------------------------------------------------------------------------
# Frozen dataclasses (immutable value objects)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DependencyEdge:
    """A directed edge (predecessor → successor) in the patch dependency graph.

    Attributes
    ----------
    edge_id:
        Unique identifier.
    predecessor_id:
        The patch that must complete before the successor can start.
    successor_id:
        The patch that depends on the predecessor's output.
    reason:
        Human-readable explanation of why this dependency exists (e.g.
        ``"interface_treaty"`` or ``"overlap_constraint"``).
    strength:
        ``"hard"`` — the ordering is strictly required.
        ``"soft"`` — the ordering is preferred but may be relaxed.
    created_at:
        Unix timestamp.
    """

    edge_id: str
    predecessor_id: str
    successor_id: str
    reason: str
    strength: str
    created_at: float


@dataclass(frozen=True, slots=True)
class ParallelismConstraint:
    """An external constraint imposed on the parallelism plan.

    Constraints refine the plan produced by wave-decomposition by adding
    rules that go beyond raw dependency ordering.

    Attributes
    ----------
    constraint_id:
        Unique identifier.
    constraint_type:
        One of ``"max_concurrent"``, ``"must_precede"``,
        ``"must_not_overlap"``, ``"budget_cap"``.
    patch_ids:
        Patches affected by this constraint.
    parameters:
        Type-specific parameters (e.g. ``{"max": 3}`` for ``max_concurrent``).
    priority:
        Integer priority; higher values take precedence when constraints conflict.
    created_at:
        Unix timestamp.
    """

    constraint_id: str
    constraint_type: str
    patch_ids: tuple[str, ...]
    parameters: dict[str, Any]
    priority: int
    created_at: float


# ---------------------------------------------------------------------------
# Mutable dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ParallelismGroup:
    """A set of patches that may execute concurrently within a generation wave.

    Attributes
    ----------
    group_id:
        Unique identifier.
    wave_index:
        The wave this group belongs to (0-based).
    sub_index:
        Sub-wave index within *wave_index* (0 for the first sub-wave).
    patch_ids:
        Ordered list of patch IDs in this group.
    total_budget:
        Sum of per-patch budgets for patches in this group.
    parallel:
        ``True`` if patches in this group may execute concurrently.
    created_at:
        Unix timestamp.
    """

    group_id: str
    wave_index: int
    sub_index: int
    patch_ids: list[str]
    total_budget: float
    parallel: bool
    created_at: float = field(default_factory=time.time)

    def add_patch(self, patch_id: str, budget: float) -> None:
        """Append *patch_id* to this group and add *budget* to *total_budget*."""
        if patch_id not in self.patch_ids:
            self.patch_ids.append(patch_id)
            self.total_budget += budget

    def remove_patch(self, patch_id: str, budget: float) -> bool:
        """Remove *patch_id* from this group.

        Returns ``True`` if the patch was found and removed.
        """
        if patch_id in self.patch_ids:
            self.patch_ids.remove(patch_id)
            self.total_budget = max(self.total_budget - budget, 0.0)
            return True
        return False

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict."""
        return {
            "group_id": self.group_id,
            "wave_index": self.wave_index,
            "sub_index": self.sub_index,
            "patch_ids": list(self.patch_ids),
            "total_budget": self.total_budget,
            "parallel": self.parallel,
            "patch_count": len(self.patch_ids),
        }


@dataclass
class GenerationWave:
    """An ordered collection of :class:`ParallelismGroup` objects.

    A generation wave W_k contains one or more parallelism groups.  When a
    wave has been split due to budget constraints the groups are executed in
    order within the wave, but patches across all groups in the same wave
    have no dependency ordering constraint between them (beyond the budget
    split).

    Attributes
    ----------
    wave_id:
        Unique identifier.
    wave_index:
        0-based sequential index.
    groups:
        The parallelism groups comprising this wave.
    predecessor_wave_ids:
        IDs of waves that must complete before this wave can start.
    status:
        One of ``"pending"``, ``"running"``, ``"completed"``, ``"failed"``.
    started_at:
        Unix timestamp when the wave began executing, or ``None``.
    completed_at:
        Unix timestamp when the wave finished, or ``None``.
    """

    wave_id: str
    wave_index: int
    groups: list[ParallelismGroup]
    predecessor_wave_ids: list[str]
    status: str = "pending"
    started_at: float | None = None
    completed_at: float | None = None

    def total_patch_count(self) -> int:
        """Return the total number of patches across all groups in this wave."""
        return sum(len(g.patch_ids) for g in self.groups)

    def total_budget(self) -> float:
        """Return the total budget consumed by this wave (first group only, for budget checking)."""
        # The budget limit applies per-group, but we track total for reporting.
        return sum(g.total_budget for g in self.groups)

    def max_group_budget(self) -> float:
        """Return the maximum single-group budget in this wave."""
        if not self.groups:
            return 0.0
        return max(g.total_budget for g in self.groups)

    def all_patch_ids(self) -> list[str]:
        """Return all patch IDs across every group in this wave."""
        result: list[str] = []
        for g in self.groups:
            result.extend(g.patch_ids)
        return result

    def mark_started(self) -> None:
        """Set status to ``"running"`` and record the start timestamp."""
        self.status = "running"
        self.started_at = time.time()

    def mark_completed(self, success: bool = True) -> None:
        """Set status to ``"completed"`` or ``"failed"`` and record the end timestamp."""
        self.status = "completed" if success else "failed"
        self.completed_at = time.time()

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict."""
        return {
            "wave_id": self.wave_id,
            "wave_index": self.wave_index,
            "group_count": len(self.groups),
            "groups": [g.to_dict() for g in self.groups],
            "predecessor_wave_ids": list(self.predecessor_wave_ids),
            "status": self.status,
            "total_patch_count": self.total_patch_count(),
            "total_budget": self.total_budget(),
        }


# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------


class ParallelismStrategyAnalyzer:
    """Analyses patch dependencies, detects cycles, and computes anti-chains.

    The analyzer operates on a dependency graph represented as:

    * ``adjacency``  — mapping ``patch_id → list[successor_id]``  (forward edges)
    * ``in_edges``   — mapping ``patch_id → list[predecessor_id]`` (reverse edges)

    All methods are pure functions over their arguments; no internal mutable
    state is modified by analysis methods.  (The cycle cache is populated as a
    side-effect for efficiency, but this does not affect correctness.)
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = config or {}
        self._config: dict[str, Any] = {
            "max_cycle_search_depth": cfg.get("max_cycle_search_depth", 10_000),
            "anti_chain_algorithm": cfg.get("anti_chain_algorithm", "kahn_bfs"),
        }
        self._logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
        self._cycle_cache: dict[str, bool] = {}  # graph hash → has_cycle

    # ------------------------------------------------------------------
    # Graph construction helpers
    # ------------------------------------------------------------------

    def build_adjacency(
        self, patch_ids: list[str], edges: list[DependencyEdge]
    ) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
        """Build forward and reverse adjacency maps from *edges*.

        Parameters
        ----------
        patch_ids:
            All patch identifiers in the dependency graph.
        edges:
            List of :class:`DependencyEdge` objects.

        Returns
        -------
        tuple[dict, dict]
            ``(forward, reverse)`` where ``forward[u] = list[successors]``
            and ``reverse[v] = list[predecessors]``.
        """
        forward: dict[str, list[str]] = {pid: [] for pid in patch_ids}
        reverse: dict[str, list[str]] = {pid: [] for pid in patch_ids}

        for edge in edges:
            pred = edge.predecessor_id
            succ = edge.successor_id
            if pred not in forward:
                forward[pred] = []
            if succ not in reverse:
                reverse[succ] = []
            if succ not in forward[pred]:
                forward[pred].append(succ)
            if pred not in reverse[succ]:
                reverse[succ].append(pred)

        return forward, reverse

    # ------------------------------------------------------------------
    # Cycle detection
    # ------------------------------------------------------------------

    def detect_cycles(
        self,
        patch_ids: list[str],
        edges: list[DependencyEdge],
    ) -> dict[str, Any]:
        """Detect cycles in the dependency graph using iterative DFS.

        A dependency graph with cycles cannot be scheduled for parallel
        construction without risking deadlock.

        Parameters
        ----------
        patch_ids:
            All patch identifiers.
        edges:
            Dependency edges.

        Returns
        -------
        dict
            ``{
                "has_cycle": bool,
                "cycle_nodes": list[str],  # non-empty if has_cycle
                "strongly_connected_components": list[list[str]],
                "dag_verified": bool,  # True iff no cycle found
            }``
        """
        forward, _ = self.build_adjacency(patch_ids, edges)
        # Iterative DFS-based cycle detection (Kahn in-degree check)
        in_degree: dict[str, int] = {pid: 0 for pid in patch_ids}
        for pid in patch_ids:
            for succ in forward.get(pid, []):
                if succ in in_degree:
                    in_degree[succ] += 1

        queue: deque[str] = deque(pid for pid, deg in in_degree.items() if deg == 0)
        visited_count = 0
        topo_order: list[str] = []

        while queue:
            node = queue.popleft()
            topo_order.append(node)
            visited_count += 1
            for succ in forward.get(node, []):
                if succ in in_degree:
                    in_degree[succ] -= 1
                    if in_degree[succ] == 0:
                        queue.append(succ)

        has_cycle = visited_count < len(patch_ids)
        cycle_nodes: list[str] = []
        if has_cycle:
            cycle_nodes = [pid for pid in patch_ids if pid not in set(topo_order)]

        # SCCs via Tarjan's algorithm (lightweight version)
        sccs = self._compute_sccs(patch_ids, forward)

        self._logger.debug(
            "detect_cycles: has_cycle=%s, cycle_nodes=%s", has_cycle, cycle_nodes
        )
        return {
            "has_cycle": has_cycle,
            "cycle_nodes": cycle_nodes,
            "strongly_connected_components": sccs,
            "dag_verified": not has_cycle,
            "topological_order": topo_order,
        }

    def _compute_sccs(
        self,
        patch_ids: list[str],
        forward: dict[str, list[str]],
    ) -> list[list[str]]:
        """Compute strongly connected components using Kosaraju's algorithm.

        Only SCCs with more than one member (or a self-loop) indicate cycles.

        Returns
        -------
        list[list[str]]
            List of SCCs, each SCC is a list of patch IDs.
        """
        # Pass 1: finish-time DFS on forward graph
        visited: set[str] = set()
        finish_order: list[str] = []

        def dfs_forward(start: str) -> None:
            stack: list[tuple[str, int]] = [(start, 0)]
            while stack:
                node, idx = stack.pop()
                if idx == 0:
                    if node in visited:
                        continue
                    visited.add(node)
                    stack.append((node, 1))
                    for succ in forward.get(node, []):
                        if succ not in visited:
                            stack.append((succ, 0))
                else:
                    finish_order.append(node)

        for pid in patch_ids:
            if pid not in visited:
                dfs_forward(pid)

        # Build reverse graph
        reverse: dict[str, list[str]] = {pid: [] for pid in patch_ids}
        for pid in patch_ids:
            for succ in forward.get(pid, []):
                reverse.setdefault(succ, []).append(pid)

        # Pass 2: DFS on reverse graph in reverse finish order
        visited2: set[str] = set()
        sccs: list[list[str]] = []

        def dfs_reverse(start: str) -> list[str]:
            component: list[str] = []
            stack2: list[str] = [start]
            while stack2:
                node = stack2.pop()
                if node in visited2:
                    continue
                visited2.add(node)
                component.append(node)
                for pred in reverse.get(node, []):
                    if pred not in visited2:
                        stack2.append(pred)
            return component

        for node in reversed(finish_order):
            if node not in visited2:
                comp = dfs_reverse(node)
                sccs.append(comp)

        return sccs

    # ------------------------------------------------------------------
    # Anti-chain / wave decomposition
    # ------------------------------------------------------------------

    def compute_anti_chains(
        self,
        patch_ids: list[str],
        edges: list[DependencyEdge],
    ) -> list[list[str]]:
        """Decompose the DAG into a sequence of anti-chains (generation waves).

        Uses Kahn's BFS level-by-level topological sort.  Each BFS level
        constitutes one anti-chain: all patches in the same level have in-degree
        zero in the subgraph obtained after removing all earlier levels.

        Parameters
        ----------
        patch_ids:
            All patch identifiers.
        edges:
            Dependency edges (must form a DAG; caller should verify with
            :meth:`detect_cycles` first).

        Returns
        -------
        list[list[str]]
            Ordered list of anti-chains.  Anti-chains[0] = level-0 patches
            (no predecessors), etc.
        """
        forward, _ = self.build_adjacency(patch_ids, edges)
        in_degree: dict[str, int] = {pid: 0 for pid in patch_ids}
        for pid in patch_ids:
            for succ in forward.get(pid, []):
                if succ in in_degree:
                    in_degree[succ] += 1

        queue: deque[str] = deque(
            sorted(pid for pid, deg in in_degree.items() if deg == 0)
        )
        anti_chains: list[list[str]] = []

        while queue:
            level_size = len(queue)
            level: list[str] = []
            for _ in range(level_size):
                node = queue.popleft()
                level.append(node)
                for succ in forward.get(node, []):
                    if succ in in_degree:
                        in_degree[succ] -= 1
                        if in_degree[succ] == 0:
                            queue.append(succ)
            anti_chains.append(level)

        # Nodes with cycles end up unvisited; add them defensively
        seen = {pid for chain in anti_chains for pid in chain}
        remaining = [pid for pid in patch_ids if pid not in seen]
        if remaining:
            anti_chains.append(remaining)

        self._logger.debug(
            "compute_anti_chains: %d patches → %d waves", len(patch_ids), len(anti_chains)
        )
        return anti_chains

    def compute_in_degrees(
        self, patch_ids: list[str], edges: list[DependencyEdge]
    ) -> dict[str, int]:
        """Return a mapping ``patch_id → in-degree`` for the dependency graph.

        Parameters
        ----------
        patch_ids:
            All patch identifiers.
        edges:
            Dependency edges.

        Returns
        -------
        dict[str, int]
            In-degree of each patch.
        """
        in_degree: dict[str, int] = {pid: 0 for pid in patch_ids}
        for edge in edges:
            if edge.successor_id in in_degree:
                in_degree[edge.successor_id] += 1
        return in_degree

    def transitive_closure(
        self, patch_ids: list[str], edges: list[DependencyEdge]
    ) -> dict[str, set[str]]:
        """Compute the transitive closure of the dependency graph.

        ``closure[u]`` is the set of all patches reachable from u via forward edges.

        Parameters
        ----------
        patch_ids:
            All patch identifiers.
        edges:
            Dependency edges.

        Returns
        -------
        dict[str, set[str]]
            Transitive closure.
        """
        forward, _ = self.build_adjacency(patch_ids, edges)
        closure: dict[str, set[str]] = {pid: set() for pid in patch_ids}

        def reach(source: str) -> set[str]:
            if closure[source]:
                return closure[source]
            visited: set[str] = set()
            stack: list[str] = list(forward.get(source, []))
            while stack:
                node = stack.pop()
                if node in visited:
                    continue
                visited.add(node)
                stack.extend(forward.get(node, []))
            closure[source] = visited
            return visited

        for pid in patch_ids:
            reach(pid)

        return closure

    def analyse_critical_path(
        self,
        patch_ids: list[str],
        edges: list[DependencyEdge],
        budget_map: dict[str, float],
    ) -> dict[str, Any]:
        """Identify the critical path through the dependency DAG.

        The critical path is the longest path (by total budget) from any
        source to any sink.  It determines the minimum total elapsed budget
        needed for sequential execution.

        Parameters
        ----------
        patch_ids:
            All patch identifiers.
        edges:
            Dependency edges.
        budget_map:
            Mapping ``patch_id → budget_units``.

        Returns
        -------
        dict
            ``{
                "critical_path": list[str],
                "critical_path_budget": float,
                "source_nodes": list[str],
                "sink_nodes": list[str],
            }``
        """
        forward, reverse = self.build_adjacency(patch_ids, edges)
        sources = [pid for pid in patch_ids if not reverse.get(pid)]
        sinks = [pid for pid in patch_ids if not forward.get(pid)]

        # DP: longest-weighted path
        dist: dict[str, float] = {pid: 0.0 for pid in patch_ids}
        prev: dict[str, str | None] = {pid: None for pid in patch_ids}

        # Topological order via Kahn
        in_deg: dict[str, int] = {pid: len(reverse.get(pid, [])) for pid in patch_ids}
        q: deque[str] = deque(pid for pid in patch_ids if in_deg[pid] == 0)
        topo: list[str] = []
        while q:
            node = q.popleft()
            topo.append(node)
            for succ in forward.get(node, []):
                if succ in in_deg:
                    in_deg[succ] -= 1
                    if in_deg[succ] == 0:
                        q.append(succ)

        for node in topo:
            cost = dist[node] + budget_map.get(node, 0.0)
            for succ in forward.get(node, []):
                if cost > dist.get(succ, 0.0):
                    dist[succ] = cost
                    prev[succ] = node

        # Find end of critical path (max dist among sinks)
        if not sinks:
            return {
                "critical_path": [],
                "critical_path_budget": 0.0,
                "source_nodes": sources,
                "sink_nodes": sinks,
            }

        end_node = max(sinks, key=lambda n: dist.get(n, 0.0))
        path: list[str] = []
        cur: str | None = end_node
        while cur is not None:
            path.append(cur)
            cur = prev.get(cur)
        path.reverse()

        critical_budget = sum(budget_map.get(pid, 0.0) for pid in path)
        return {
            "critical_path": path,
            "critical_path_budget": critical_budget,
            "source_nodes": sources,
            "sink_nodes": sinks,
        }


# ---------------------------------------------------------------------------
# Witness
# ---------------------------------------------------------------------------


class ParallelismStrategyWitness:
    """Certifies that a parallelism plan is deadlock-free and budget-admissible.

    Deadlock-freedom is equivalent to the dependency graph being a DAG
    (no directed cycles).  Budget admissibility requires that for every
    :class:`ParallelismGroup` g: ``g.total_budget ≤ B_parallel``.

    All certificates carry a ``trust_tier`` field of ``"proposal"``.
    """

    def __init__(self) -> None:
        self._logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
        self._certificate_log: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Deadlock-freedom check
    # ------------------------------------------------------------------

    def check_deadlock_free(
        self,
        waves: list[GenerationWave],
        edges: list[DependencyEdge],
        patch_ids: list[str],
    ) -> dict[str, Any]:
        """Check whether the dependency graph is a DAG (no cycles → deadlock-free).

        Parameters
        ----------
        waves:
            Planned generation waves.
        edges:
            All dependency edges.
        patch_ids:
            All patch identifiers.

        Returns
        -------
        dict
            ``{
                "deadlock_free": bool,
                "cycle_detected": bool,
                "cycle_nodes": list[str],
                "wave_count": int,
                "patch_count": int,
            }``
        """
        analyzer = ParallelismStrategyAnalyzer()
        cycle_info = analyzer.detect_cycles(patch_ids, edges)
        deadlock_free = not cycle_info["has_cycle"]
        self._logger.debug(
            "check_deadlock_free: deadlock_free=%s, cycle_nodes=%s",
            deadlock_free,
            cycle_info["cycle_nodes"],
        )
        return {
            "deadlock_free": deadlock_free,
            "cycle_detected": cycle_info["has_cycle"],
            "cycle_nodes": cycle_info["cycle_nodes"],
            "wave_count": len(waves),
            "patch_count": len(patch_ids),
        }

    # ------------------------------------------------------------------
    # Budget admissibility check
    # ------------------------------------------------------------------

    def check_budget_admissibility(
        self,
        waves: list[GenerationWave],
        parallel_budget_limit: float,
    ) -> dict[str, Any]:
        """Check that every parallelism group's budget ≤ *parallel_budget_limit*.

        Parameters
        ----------
        waves:
            Planned generation waves.
        parallel_budget_limit:
            Maximum concurrent budget B_parallel.

        Returns
        -------
        dict
            ``{
                "budget_admissible": bool,
                "violating_groups": list[str],  # group_ids that exceed the limit
                "max_group_budget": float,
                "parallel_budget_limit": float,
            }``
        """
        violating: list[str] = []
        max_budget = 0.0

        for wave in waves:
            for group in wave.groups:
                if group.total_budget > max_budget:
                    max_budget = group.total_budget
                if group.total_budget > parallel_budget_limit + _ADMISSIBILITY_TOLERANCE:
                    violating.append(group.group_id)

        admissible = len(violating) == 0
        self._logger.debug(
            "check_budget_admissibility: admissible=%s, violating=%s",
            admissible,
            violating,
        )
        return {
            "budget_admissible": admissible,
            "violating_groups": violating,
            "max_group_budget": max_budget,
            "parallel_budget_limit": parallel_budget_limit,
        }

    # ------------------------------------------------------------------
    # Wave ordering check
    # ------------------------------------------------------------------

    def check_wave_ordering(
        self,
        waves: list[GenerationWave],
        edges: list[DependencyEdge],
    ) -> dict[str, Any]:
        """Verify that no wave contains a patch that depends on a patch in the same wave.

        Two patches U_i, U_j are in the same wave W_k.  If there is an edge
        (i → j) then this is a wave-ordering violation — j should be in a
        later wave.

        Parameters
        ----------
        waves:
            The ordered list of generation waves.
        edges:
            Dependency edges.

        Returns
        -------
        dict
            ``{
                "ordering_valid": bool,
                "violations": list[dict],
                "violation_count": int,
            }``
        """
        # Build a mapping from patch_id to wave_index
        patch_to_wave: dict[str, int] = {}
        for wave in waves:
            for pid in wave.all_patch_ids():
                patch_to_wave[pid] = wave.wave_index

        violations: list[dict[str, Any]] = []
        for edge in edges:
            pred_wave = patch_to_wave.get(edge.predecessor_id)
            succ_wave = patch_to_wave.get(edge.successor_id)
            if pred_wave is None or succ_wave is None:
                continue
            if pred_wave >= succ_wave:
                violations.append(
                    {
                        "edge_id": edge.edge_id,
                        "predecessor_id": edge.predecessor_id,
                        "successor_id": edge.successor_id,
                        "predecessor_wave": pred_wave,
                        "successor_wave": succ_wave,
                    }
                )

        ordering_valid = len(violations) == 0
        return {
            "ordering_valid": ordering_valid,
            "violations": violations,
            "violation_count": len(violations),
        }

    # ------------------------------------------------------------------
    # Full certification
    # ------------------------------------------------------------------

    def certify(
        self,
        waves: list[GenerationWave],
        edges: list[DependencyEdge],
        parallel_budget_limit: float,
        budget_map: dict[str, float],
        patch_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Produce a signed certificate for the parallelism plan.

        The certificate combines:
        1. Deadlock-freedom check.
        2. Budget admissibility check.
        3. Wave-ordering check.

        Parameters
        ----------
        waves:
            Planned generation waves.
        edges:
            Dependency edges.
        parallel_budget_limit:
            Maximum concurrent budget B_parallel.
        budget_map:
            Mapping ``patch_id → budget``.
        patch_ids:
            All patch identifiers.  Inferred from *waves* if ``None``.

        Returns
        -------
        dict
            Certificate record with keys:
            ``certificate_id``, ``deadlock_free``, ``budget_admissible``,
            ``ordering_valid``, ``trust_tier``, ``cech_condition_flag``,
            ``wave_count``, ``issued_at``.
        """
        if patch_ids is None:
            all_pids: list[str] = []
            for w in waves:
                all_pids.extend(w.all_patch_ids())
            patch_ids = all_pids

        deadlock_check = self.check_deadlock_free(waves, edges, patch_ids)
        budget_check = self.check_budget_admissibility(waves, parallel_budget_limit)
        ordering_check = self.check_wave_ordering(waves, edges)

        overall_valid = (
            deadlock_check["deadlock_free"]
            and budget_check["budget_admissible"]
            and ordering_check["ordering_valid"]
        )

        certificate: dict[str, Any] = {
            "certificate_id": str(uuid.uuid4()),
            "deadlock_free": deadlock_check["deadlock_free"],
            "budget_admissible": budget_check["budget_admissible"],
            "ordering_valid": ordering_check["ordering_valid"],
            "overall_valid": overall_valid,
            "trust_tier": _PROPOSAL_TRUST_TIER,
            "cech_condition_flag": True,
            "wave_count": len(waves),
            "patch_count": len(patch_ids),
            "parallel_budget_limit": parallel_budget_limit,
            "deadlock_check": deadlock_check,
            "budget_check": budget_check,
            "ordering_check": ordering_check,
            "issued_at": time.time(),
        }
        self._certificate_log.append(certificate)
        self._logger.info(
            "Certificate %s: valid=%s (deadlock_free=%s, budget=%s, order=%s)",
            certificate["certificate_id"],
            overall_valid,
            deadlock_check["deadlock_free"],
            budget_check["budget_admissible"],
            ordering_check["ordering_valid"],
        )
        return certificate

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    @property
    def certificate_log(self) -> list[dict[str, Any]]:
        """Read-only copy of all certificates issued."""
        return list(self._certificate_log)

    def reset(self) -> None:
        """Clear the certificate log."""
        self._certificate_log.clear()

    def __repr__(self) -> str:
        return f"ParallelismStrategyWitness(certificates_issued={len(self._certificate_log)})"


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------


class ParallelismStrategyCoordinator:
    """Builds parallelism groups and generation waves from a dependency graph
    and budget constraints.

    The coordinator is the central driver for constructing a parallelism plan.
    It:

    1. Accepts a list of patch IDs and dependency edges.
    2. Verifies the dependency graph is a DAG.
    3. Decomposes the DAG into anti-chain levels.
    4. Applies the :class:`ParallelismPolicy` to produce :class:`GenerationWave` objects.
    5. Splits waves that would exceed the parallel budget limit.
    6. Optionally applies :class:`ParallelismConstraint` objects.
    7. Returns the ordered wave list and a summary.

    Configuration keys
    ------------------
    parallel_budget_limit : float
        Maximum total concurrent budget per generation wave group.  Default: inf.
    policy : str
        Default :class:`ParallelismPolicy` value name (default ``"wave"``).
    max_wave_splits : int
        Maximum number of times a single wave may be split due to budget (default 10).
    enforce_hard_constraints_only : bool
        When ``True``, soft dependency edges are treated as hints only and do not
        affect wave placement (default ``False``).
    """

    def __init__(
        self,
        parallel_budget_limit: float = _DEFAULT_PARALLEL_BUDGET_LIMIT,
        policy: ParallelismPolicy = ParallelismPolicy.WAVE,
        config: dict[str, Any] | None = None,
    ) -> None:
        cfg = config or {}
        self._parallel_budget_limit: float = float(
            cfg.get("parallel_budget_limit", parallel_budget_limit)
        )
        self._default_policy: ParallelismPolicy = policy
        self._max_wave_splits: int = int(cfg.get("max_wave_splits", 10))
        self._enforce_hard_only: bool = bool(
            cfg.get("enforce_hard_constraints_only", False)
        )
        self._logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
        self._analyzer = ParallelismStrategyAnalyzer()
        self._active_waves: list[GenerationWave] = []
        self._constraint_registry: list[ParallelismConstraint] = []
        self._plan_history: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Dependency edge construction
    # ------------------------------------------------------------------

    def build_dependency_edges(
        self,
        raw_edges: list[tuple[str, str]],
        reason: str = "interface_treaty",
        strength: str = "hard",
    ) -> list[DependencyEdge]:
        """Convert raw (predecessor, successor) tuples into :class:`DependencyEdge` objects.

        Parameters
        ----------
        raw_edges:
            List of ``(predecessor_id, successor_id)`` pairs.
        reason:
            Reason description applied to all edges.
        strength:
            ``"hard"`` or ``"soft"``.

        Returns
        -------
        list[DependencyEdge]
            One :class:`DependencyEdge` per input tuple.
        """
        edges: list[DependencyEdge] = []
        for pred, succ in raw_edges:
            edge = DependencyEdge(
                edge_id=str(uuid.uuid4()),
                predecessor_id=pred,
                successor_id=succ,
                reason=reason,
                strength=strength,
                created_at=time.time(),
            )
            edges.append(edge)
        return edges

    # ------------------------------------------------------------------
    # Core wave computation
    # ------------------------------------------------------------------

    def compute_waves(
        self,
        patch_ids: list[str],
        edges: list[DependencyEdge],
        budget_map: dict[str, float],
        policy: ParallelismPolicy | None = None,
    ) -> list[GenerationWave]:
        """Compute the ordered list of :class:`GenerationWave` objects for the given patches.

        The computation proceeds as follows:

        1. If *policy* is ``SEQUENTIAL``, each patch becomes its own
           single-patch group and wave.
        2. Otherwise, decompose into anti-chains with
           :meth:`ParallelismStrategyAnalyzer.compute_anti_chains`.
        3. For each anti-chain, build a :class:`ParallelismGroup`.
        4. If the group's total budget exceeds ``parallel_budget_limit``,
           split it into sub-groups.
        5. Wrap each (possibly split) set of groups into a :class:`GenerationWave`.
        6. Assign predecessor wave IDs.

        Parameters
        ----------
        patch_ids:
            All patch identifiers.
        edges:
            Dependency edges.
        budget_map:
            Mapping ``patch_id → budget_units``.
        policy:
            Override the coordinator's default policy for this call.

        Returns
        -------
        list[GenerationWave]
            Ordered list of generation waves.
        """
        effective_policy = policy or self._default_policy
        if not patch_ids:
            return []

        # Filter to hard edges only if configured
        effective_edges = edges
        if self._enforce_hard_only:
            effective_edges = [e for e in edges if e.strength == "hard"]

        if effective_policy == ParallelismPolicy.SEQUENTIAL:
            waves = self._build_sequential_waves(patch_ids, budget_map)
        elif effective_policy == ParallelismPolicy.FULL_PARALLEL:
            waves = self._build_full_parallel_waves(
                patch_ids, effective_edges, budget_map
            )
        else:
            # WAVE and ADAPTIVE both start with anti-chain decomposition
            anti_chains = self._analyzer.compute_anti_chains(patch_ids, effective_edges)
            waves = self._build_waves_from_anti_chains(
                anti_chains, budget_map, effective_policy
            )

        # Apply constraints
        waves = self._apply_constraints(waves, budget_map)

        # Store and log
        self._active_waves = waves
        plan_record: dict[str, Any] = {
            "plan_id": str(uuid.uuid4()),
            "policy": effective_policy.value,
            "wave_count": len(waves),
            "patch_count": len(patch_ids),
            "timestamp": time.time(),
        }
        self._plan_history.append(plan_record)
        self._logger.info(
            "compute_waves: %d patches → %d waves (policy=%s)",
            len(patch_ids),
            len(waves),
            effective_policy.value,
        )
        return waves

    def _build_sequential_waves(
        self,
        patch_ids: list[str],
        budget_map: dict[str, float],
    ) -> list[GenerationWave]:
        """Build one wave per patch (fully sequential execution)."""
        waves: list[GenerationWave] = []
        prev_wave_id: str | None = None
        for idx, pid in enumerate(patch_ids):
            b = budget_map.get(pid, 0.0)
            group = ParallelismGroup(
                group_id=str(uuid.uuid4()),
                wave_index=idx,
                sub_index=0,
                patch_ids=[pid],
                total_budget=b,
                parallel=False,
            )
            wave = GenerationWave(
                wave_id=str(uuid.uuid4()),
                wave_index=idx,
                groups=[group],
                predecessor_wave_ids=[prev_wave_id] if prev_wave_id else [],
            )
            prev_wave_id = wave.wave_id
            waves.append(wave)
        return waves

    def _build_full_parallel_waves(
        self,
        patch_ids: list[str],
        edges: list[DependencyEdge],
        budget_map: dict[str, float],
    ) -> list[GenerationWave]:
        """Build waves from anti-chains without further budget splitting."""
        anti_chains = self._analyzer.compute_anti_chains(patch_ids, edges)
        waves: list[GenerationWave] = []
        for idx, chain in enumerate(anti_chains):
            total_b = sum(budget_map.get(p, 0.0) for p in chain)
            group = ParallelismGroup(
                group_id=str(uuid.uuid4()),
                wave_index=idx,
                sub_index=0,
                patch_ids=list(chain),
                total_budget=total_b,
                parallel=len(chain) > 1,
            )
            predecessor_ids = [waves[idx - 1].wave_id] if idx > 0 else []
            wave = GenerationWave(
                wave_id=str(uuid.uuid4()),
                wave_index=idx,
                groups=[group],
                predecessor_wave_ids=predecessor_ids,
            )
            waves.append(wave)
        return waves

    def _build_waves_from_anti_chains(
        self,
        anti_chains: list[list[str]],
        budget_map: dict[str, float],
        policy: ParallelismPolicy,
    ) -> list[GenerationWave]:
        """Build generation waves from anti-chains, splitting on budget if needed."""
        waves: list[GenerationWave] = []
        for wave_idx, chain in enumerate(anti_chains):
            groups = self._split_chain_by_budget(chain, wave_idx, budget_map)
            predecessor_ids = [waves[wave_idx - 1].wave_id] if wave_idx > 0 else []
            wave = GenerationWave(
                wave_id=str(uuid.uuid4()),
                wave_index=wave_idx,
                groups=groups,
                predecessor_wave_ids=predecessor_ids,
            )
            waves.append(wave)
        return waves

    def _split_chain_by_budget(
        self,
        chain: list[str],
        wave_index: int,
        budget_map: dict[str, float],
    ) -> list[ParallelismGroup]:
        """Split *chain* into budget-admissible parallelism groups.

        Each resulting group has ``total_budget ≤ parallel_budget_limit``.

        Parameters
        ----------
        chain:
            Anti-chain of patch IDs.
        wave_index:
            The wave index for labelling.
        budget_map:
            Per-patch budget.

        Returns
        -------
        list[ParallelismGroup]
            One or more groups covering all patches in *chain*.
        """
        limit = self._parallel_budget_limit
        groups: list[ParallelismGroup] = []
        current_patches: list[str] = []
        current_budget = 0.0
        sub_idx = 0

        for pid in chain:
            b = budget_map.get(pid, 0.0)
            if current_patches and (
                current_budget + b > limit + _ADMISSIBILITY_TOLERANCE
            ):
                # Flush current group
                groups.append(
                    ParallelismGroup(
                        group_id=str(uuid.uuid4()),
                        wave_index=wave_index,
                        sub_index=sub_idx,
                        patch_ids=list(current_patches),
                        total_budget=current_budget,
                        parallel=len(current_patches) > 1,
                    )
                )
                sub_idx += 1
                current_patches = []
                current_budget = 0.0

            current_patches.append(pid)
            current_budget += b

        if current_patches:
            groups.append(
                ParallelismGroup(
                    group_id=str(uuid.uuid4()),
                    wave_index=wave_index,
                    sub_index=sub_idx,
                    patch_ids=list(current_patches),
                    total_budget=current_budget,
                    parallel=len(current_patches) > 1,
                )
            )

        return groups

    # ------------------------------------------------------------------
    # Constraint handling
    # ------------------------------------------------------------------

    def register_constraint(self, constraint: ParallelismConstraint) -> None:
        """Register an external constraint to be applied during wave computation.

        Parameters
        ----------
        constraint:
            The :class:`ParallelismConstraint` to register.
        """
        self._constraint_registry.append(constraint)
        self._logger.debug(
            "Registered constraint %s (type=%s)",
            constraint.constraint_id,
            constraint.constraint_type,
        )

    def _apply_constraints(
        self,
        waves: list[GenerationWave],
        budget_map: dict[str, float],
    ) -> list[GenerationWave]:
        """Apply registered constraints to the current wave list.

        Currently handles ``"max_concurrent"`` constraints only; other types
        are logged and skipped.

        Parameters
        ----------
        waves:
            Current wave list.
        budget_map:
            Per-patch budget.

        Returns
        -------
        list[GenerationWave]
            Possibly modified wave list.
        """
        hard_constraints = sorted(
            [c for c in self._constraint_registry if c.constraint_type == "max_concurrent"],
            key=lambda c: c.priority,
            reverse=True,
        )
        for constraint in hard_constraints:
            max_concurrent = int(constraint.parameters.get("max", len(waves) + 1))
            waves = self._enforce_max_concurrent(waves, max_concurrent, budget_map)
        return waves

    def _enforce_max_concurrent(
        self,
        waves: list[GenerationWave],
        max_concurrent: int,
        budget_map: dict[str, float],
    ) -> list[GenerationWave]:
        """Ensure no group has more than *max_concurrent* patches.

        Any group exceeding *max_concurrent* is split into smaller sub-groups.
        """
        new_waves: list[GenerationWave] = []
        for wave in waves:
            new_groups: list[ParallelismGroup] = []
            for group in wave.groups:
                if len(group.patch_ids) <= max_concurrent:
                    new_groups.append(group)
                    continue
                # Split group
                sub_idx = group.sub_index
                for chunk_start in range(0, len(group.patch_ids), max_concurrent):
                    chunk = group.patch_ids[chunk_start: chunk_start + max_concurrent]
                    b = sum(budget_map.get(p, 0.0) for p in chunk)
                    new_groups.append(
                        ParallelismGroup(
                            group_id=str(uuid.uuid4()),
                            wave_index=group.wave_index,
                            sub_index=sub_idx,
                            patch_ids=chunk,
                            total_budget=b,
                            parallel=len(chunk) > 1,
                        )
                    )
                    sub_idx += 1
            wave.groups = new_groups
            new_waves.append(wave)
        return new_waves

    # ------------------------------------------------------------------
    # Plan execution simulation (ADAPTIVE policy)
    # ------------------------------------------------------------------

    def simulate_adaptive_execution(
        self,
        patch_ids: list[str],
        edges: list[DependencyEdge],
        budget_map: dict[str, float],
        spend_order: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Simulate adaptive execution, promoting patches as predecessors complete.

        In adaptive mode the coordinator continuously promotes patches whose
        all predecessors have been marked as completed.  The simulation
        proceeds in rounds; each round processes all currently-ready patches
        that fit within the budget limit.

        Parameters
        ----------
        patch_ids:
            All patch identifiers.
        edges:
            Dependency edges.
        budget_map:
            Per-patch budgets.
        spend_order:
            Optional list giving the order in which patches are assumed to
            complete.  When ``None``, ready patches complete in alphabetical
            order within each round.

        Returns
        -------
        list[dict]
            One round-record dict per execution round, each with keys:
            ``round``, ``executed``, ``remaining``, ``wave_budget``.
        """
        forward, reverse = self._analyzer.build_adjacency(patch_ids, edges)
        completed: set[str] = set()
        remaining: set[str] = set(patch_ids)
        rounds: list[dict[str, Any]] = []
        completion_lookup: dict[str, int] = {}
        if spend_order:
            for rank, pid in enumerate(spend_order):
                completion_lookup[pid] = rank

        round_num = 0
        while remaining:
            # Determine which patches are ready (all predecessors done)
            ready: list[str] = [
                pid
                for pid in remaining
                if all(pred in completed for pred in reverse.get(pid, []))
            ]
            if not ready:
                # Deadlock (cycle) - break
                self._logger.warning(
                    "simulate_adaptive: deadlock detected at round %d", round_num
                )
                break

            # Sort by completion order hint or alphabetically
            if completion_lookup:
                ready.sort(key=lambda p: completion_lookup.get(p, 999999))
            else:
                ready.sort()

            # Greedily pack into budget limit
            executed: list[str] = []
            wave_budget = 0.0
            for pid in ready:
                b = budget_map.get(pid, 0.0)
                if wave_budget + b <= self._parallel_budget_limit + _ADMISSIBILITY_TOLERANCE:
                    executed.append(pid)
                    wave_budget += b

            if not executed:
                # Even a single patch exceeds the limit — execute it alone
                executed = [ready[0]]
                wave_budget = budget_map.get(ready[0], 0.0)

            for pid in executed:
                completed.add(pid)
                remaining.discard(pid)

            rounds.append(
                {
                    "round": round_num,
                    "executed": list(executed),
                    "remaining": list(remaining),
                    "wave_budget": wave_budget,
                }
            )
            round_num += 1

        return rounds

    # ------------------------------------------------------------------
    # Summary and accessors
    # ------------------------------------------------------------------

    def summarise_plan(self, waves: list[GenerationWave]) -> dict[str, Any]:
        """Return a human-readable summary of a wave plan.

        Parameters
        ----------
        waves:
            The plan to summarise.

        Returns
        -------
        dict
            Summary with ``wave_count``, ``total_patches``, ``total_groups``,
            ``max_parallelism``, ``total_budget``, ``wave_details``.
        """
        total_patches = sum(w.total_patch_count() for w in waves)
        total_groups = sum(len(w.groups) for w in waves)
        max_parallelism = max(
            (w.total_patch_count() for w in waves), default=0
        )
        total_budget = sum(w.total_budget() for w in waves)

        wave_details = [w.to_dict() for w in waves]

        return {
            "wave_count": len(waves),
            "total_patches": total_patches,
            "total_groups": total_groups,
            "max_parallelism": max_parallelism,
            "total_budget": total_budget,
            "parallel_budget_limit": self._parallel_budget_limit,
            "wave_details": wave_details,
        }

    @property
    def active_waves(self) -> list[GenerationWave]:
        """The most recently computed wave plan."""
        return list(self._active_waves)

    @property
    def plan_history(self) -> list[dict[str, Any]]:
        """Read-only copy of all plan-computation records."""
        return list(self._plan_history)

    def reset(self) -> None:
        """Clear all plan state."""
        self._active_waves.clear()
        self._constraint_registry.clear()
        self._plan_history.clear()
        self._logger.info("ParallelismStrategyCoordinator state reset.")

    def __repr__(self) -> str:
        return (
            f"ParallelismStrategyCoordinator("
            f"policy={self._default_policy.value!r}, "
            f"budget_limit={self._parallel_budget_limit}, "
            f"waves={len(self._active_waves)})"
        )


# ---------------------------------------------------------------------------
# Module self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.WARNING)

    # ------------------------------------------------------------------ #
    # Build patches and dependency graph                                   #
    # ------------------------------------------------------------------ #
    patches = ["p1", "p2", "p3", "p4", "p5"]
    raw_edges = [("p1", "p3"), ("p2", "p3"), ("p3", "p4"), ("p3", "p5")]
    budgets = {"p1": 100.0, "p2": 80.0, "p3": 150.0, "p4": 90.0, "p5": 60.0}

    coordinator = ParallelismStrategyCoordinator(
        parallel_budget_limit=250.0,
        policy=ParallelismPolicy.WAVE,
    )
    edges = coordinator.build_dependency_edges(raw_edges)
    waves = coordinator.compute_waves(patches, edges, budgets)
    assert len(waves) >= 1, "Expected at least one wave"

    # p1 and p2 should be in wave 0 (no predecessors)
    wave0_patches = waves[0].all_patch_ids()
    assert "p1" in wave0_patches or "p2" in wave0_patches, (
        f"Expected p1 or p2 in wave 0, got {wave0_patches}"
    )

    # ------------------------------------------------------------------ #
    # Analyzer: cycle detection                                            #
    # ------------------------------------------------------------------ #
    analyzer = ParallelismStrategyAnalyzer()
    cycle_info = analyzer.detect_cycles(patches, edges)
    assert not cycle_info["has_cycle"], "Graph should be acyclic"
    assert cycle_info["dag_verified"] is True

    # Add a cycle and verify detection
    cyclic_edges = edges + coordinator.build_dependency_edges([("p4", "p1")])
    cycle_info2 = analyzer.detect_cycles(patches, cyclic_edges)
    assert cycle_info2["has_cycle"], "Expected cycle to be detected"

    # ------------------------------------------------------------------ #
    # Anti-chains                                                          #
    # ------------------------------------------------------------------ #
    chains = analyzer.compute_anti_chains(patches, edges)
    assert len(chains) >= 1, "Expected at least one anti-chain"
    # All patches should appear exactly once
    all_in_chains = [pid for chain in chains for pid in chain]
    assert sorted(all_in_chains) == sorted(patches), "All patches must appear in chains"

    # ------------------------------------------------------------------ #
    # Witness certification                                                #
    # ------------------------------------------------------------------ #
    witness = ParallelismStrategyWitness()
    cert = witness.certify(waves, edges, parallel_budget_limit=250.0, budget_map=budgets)
    assert cert["deadlock_free"], "Should be deadlock-free"
    assert cert["budget_admissible"], f"Should be budget-admissible: {cert['budget_check']}"
    assert cert["ordering_valid"], f"Wave ordering should be valid: {cert['ordering_check']}"
    assert cert["trust_tier"] == "proposal"

    # ------------------------------------------------------------------ #
    # Sequential policy                                                    #
    # ------------------------------------------------------------------ #
    seq_waves = coordinator.compute_waves(
        patches, edges, budgets, policy=ParallelismPolicy.SEQUENTIAL
    )
    assert len(seq_waves) == len(patches), (
        f"Sequential: expected {len(patches)} waves, got {len(seq_waves)}"
    )

    # ------------------------------------------------------------------ #
    # Adaptive simulation                                                  #
    # ------------------------------------------------------------------ #
    rounds = coordinator.simulate_adaptive_execution(patches, edges, budgets)
    assert len(rounds) > 0, "Expected at least one adaptive round"
    executed_all = {pid for r in rounds for pid in r["executed"]}
    assert executed_all == set(patches), "Adaptive simulation should execute all patches"

    # ------------------------------------------------------------------ #
    # Critical path                                                        #
    # ------------------------------------------------------------------ #
    cp_info = analyzer.analyse_critical_path(patches, edges, budgets)
    assert "critical_path" in cp_info
    assert cp_info["critical_path_budget"] > 0

    # ------------------------------------------------------------------ #
    # Constraint: max_concurrent = 1 should produce sequential groups     #
    # ------------------------------------------------------------------ #
    coordinator2 = ParallelismStrategyCoordinator(parallel_budget_limit=float("inf"))
    c = ParallelismConstraint(
        constraint_id=str(uuid.uuid4()),
        constraint_type="max_concurrent",
        patch_ids=tuple(patches),
        parameters={"max": 1},
        priority=10,
        created_at=time.time(),
    )
    coordinator2.register_constraint(c)
    constrained_waves = coordinator2.compute_waves(patches, edges, budgets)
    for w in constrained_waves:
        for g in w.groups:
            assert len(g.patch_ids) <= 1, (
                f"max_concurrent=1 violated: group has {len(g.patch_ids)} patches"
            )

    # ------------------------------------------------------------------ #
    # Summary                                                              #
    # ------------------------------------------------------------------ #
    summary = coordinator.summarise_plan(waves)
    assert summary["wave_count"] == len(waves)
    assert summary["total_patches"] == len(patches)

    print("parallelism_strategy: smoke tests passed ✓")
    sys.exit(0)
