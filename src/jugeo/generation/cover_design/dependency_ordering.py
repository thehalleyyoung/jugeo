r"""Dependency ordering for cover design patches.

Theory (theory2.tex §52 — Dependency ordering):
    A *dependency ordering* is a total order on patches consistent with the
    partial order induced by interface dependencies.  Formally, let
    ``U_1, U_2, ..., U_n`` be the patches in a cover design.  A directed
    edge ``(U_i, U_j) ∈ E`` exists in the *dependency DAG* ``D = (V, E)``
    whenever patch ``U_j`` imports interface data produced by patch ``U_i``.

    A valid execution order ``π = (U_{π(1)}, ..., U_{π(n)})`` must satisfy:

        ∀ (U_i, U_j) ∈ E :  π^{-1}(U_i) < π^{-1}(U_j)

    i.e. producers always precede consumers.  Such an order is a *linear
    extension* of the partial order ``≤_D`` induced by ``D``.

    Among all valid linear extensions the algorithm selects the one that
    maximises *parallelism* following the Coffman–Graham heuristic: at each
    level of the schedule, the maximum number of independent patches is
    grouped together so that they can be executed concurrently.

    A *cycle* in ``D`` — i.e. a strongly-connected component of size > 1 —
    represents a circular interface dependency and is a hard error.  The
    invariant

        trust_tier(generated_patch) = PROPOSAL

    means that no generated patch may skip the ordering check.

    The *critical path* through ``D`` is the longest directed path
    (weighted by estimated execution cost) and gives a lower bound on the
    minimum wall time regardless of the degree of parallelism available:

        wall_time_lower_bound = max_{paths P in D}  Σ_{U_i ∈ P} cost(U_i)

    References
    ----------
    theory2.tex  §52  (Dependency ordering)
    theory2.tex  §53  (Coffman–Graham level scheduling)
    theory2.tex  §54  (Critical path analysis)
    theory2.tex  §12  (Trust tiers — PROPOSAL level)

copilot: s05-dependency-ordering
"""
from __future__ import annotations

import logging
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

try:
    from jugeo.generation.cover_design.models import (  # type: ignore[import]
        CoverDesignError,
        PatchDescriptor,
        InterfaceDependency,
    )
except Exception:  # noqa: BLE001 — optional dependency; stubs used when absent
    class CoverDesignError(Exception):  # type: ignore[no-redef]
        """Stub for CoverDesignError when models are unavailable."""

    @dataclass
    class PatchDescriptor:  # type: ignore[no-redef]
        """Minimal stub for PatchDescriptor."""
        patch_id: str
        cost: float = 1.0
        metadata: dict[str, Any] = field(default_factory=dict)

    @dataclass
    class InterfaceDependency:  # type: ignore[no-redef]
        """Minimal stub for InterfaceDependency."""
        producer_id: str
        consumer_id: str
        interface_key: str = ""


__all__ = [
    "CyclicDependencyError",
    "DependencyEdge",
    "DependencyDAG",
    "TopologicalOrder",
    "CriticalPath",
    "DependencyOrderingCoordinator",
    "DependencyOrderingAnalyzer",
    "DependencyOrderingWitness",
]

_LOG = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_PATCH_COST: float = 1.0
_INFINITY_COST: float = float("inf")
_TRUST_TIER: str = "PROPOSAL"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class CyclicDependencyError(CoverDesignError):
    """Raised when a circular interface dependency is detected in the DAG.

    Attributes
    ----------
    cycle:
        The sequence of patch identifiers that form the cycle.
    dag_id:
        Identifier of the DAG in which the cycle was found.
    """

    def __init__(self, cycle: list[str], dag_id: str = "") -> None:
        self.cycle = cycle
        self.dag_id = dag_id
        cycle_repr = " → ".join(cycle)
        super().__init__(
            f"Circular interface dependency detected in DAG '{dag_id}': "
            f"{cycle_repr}"
        )


# ---------------------------------------------------------------------------
# Immutable data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DependencyEdge:
    """A directed edge in the dependency DAG.

    Represents the assertion that ``consumer_id`` imports interface data
    produced by ``producer_id`` and therefore must be executed after it.

    Attributes
    ----------
    producer_id:
        Patch that must execute first.
    consumer_id:
        Patch that depends on the producer's output.
    interface_key:
        The specific interface export/import key that creates the
        dependency.  May be empty when the dependency is implicit.
    weight:
        An optional numeric weight used during critical path analysis.
        Defaults to ``1.0``.
    """

    producer_id: str
    consumer_id: str
    interface_key: str = ""
    weight: float = 1.0

    def reversed(self) -> DependencyEdge:
        """Return a new edge with producer and consumer swapped.

        Useful when building the transpose graph for reverse-reachability
        or Tarjan's algorithm.
        """
        return DependencyEdge(
            producer_id=self.consumer_id,
            consumer_id=self.producer_id,
            interface_key=self.interface_key,
            weight=self.weight,
        )

    def __str__(self) -> str:  # noqa: D105
        key_part = f" [{self.interface_key}]" if self.interface_key else ""
        return f"{self.producer_id} →{key_part} {self.consumer_id}"


@dataclass(frozen=True, slots=True)
class TopologicalOrder:
    """A certified linear extension of the dependency partial order.

    Attributes
    ----------
    order_id:
        Unique identifier for this ordering.
    patch_sequence:
        Patches in execution order (producers before consumers).
    levels:
        Coffman–Graham levels: ``levels[k]`` is the set of patches that can
        be started concurrently after all patches in earlier levels complete.
    dag_id:
        The DAG this ordering was derived from.
    computed_at:
        Unix timestamp when the ordering was produced.
    """

    order_id: str
    patch_sequence: tuple[str, ...]
    levels: tuple[frozenset[str], ...]
    dag_id: str
    computed_at: float

    @property
    def depth(self) -> int:
        """Number of sequential levels (minimum number of serial rounds)."""
        return len(self.levels)

    @property
    def width(self) -> int:
        """Maximum number of patches executable in parallel at any level."""
        return max((len(lvl) for lvl in self.levels), default=0)

    def position_of(self, patch_id: str) -> int:
        """Return the 0-based position of *patch_id* in the sequence.

        Raises
        ------
        KeyError
            If *patch_id* is not in the sequence.
        """
        try:
            return self.patch_sequence.index(patch_id)
        except ValueError:
            raise KeyError(patch_id) from None

    def __len__(self) -> int:  # noqa: D105
        return len(self.patch_sequence)

    def __str__(self) -> str:  # noqa: D105
        return (
            f"TopologicalOrder(id={self.order_id!r}, "
            f"patches={len(self.patch_sequence)}, "
            f"depth={self.depth}, width={self.width})"
        )


@dataclass(frozen=True, slots=True)
class CriticalPath:
    """The longest (most costly) directed path through the dependency DAG.

    Attributes
    ----------
    path_id:
        Unique identifier.
    nodes:
        Sequence of patch identifiers along the critical path.
    total_cost:
        Sum of patch costs along the path — a lower bound on wall time.
    dag_id:
        The DAG this path was extracted from.
    computed_at:
        Unix timestamp.
    """

    path_id: str
    nodes: tuple[str, ...]
    total_cost: float
    dag_id: str
    computed_at: float

    @property
    def length(self) -> int:
        """Number of patches on the critical path."""
        return len(self.nodes)

    def __str__(self) -> str:  # noqa: D105
        path_repr = " → ".join(self.nodes)
        return (
            f"CriticalPath(cost={self.total_cost:.3f}, "
            f"length={self.length}, path={path_repr})"
        )


# ---------------------------------------------------------------------------
# Mutable data types
# ---------------------------------------------------------------------------


@dataclass
class DependencyDAG:
    """Mutable representation of the patch dependency directed acyclic graph.

    Attributes
    ----------
    dag_id:
        Unique identifier for this graph instance.
    vertices:
        Mapping from patch ID to its descriptor metadata.
    edges:
        List of all directed dependency edges.
    created_at:
        Unix timestamp of construction.
    trust_tier:
        Trust tier applied to all nodes; always ``"PROPOSAL"`` for
        generated patches per theory2.tex §12.
    """

    dag_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    vertices: dict[str, dict[str, Any]] = field(default_factory=dict)
    edges: list[DependencyEdge] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    trust_tier: str = _TRUST_TIER

    # ------------------------------------------------------------------
    # Vertex management
    # ------------------------------------------------------------------

    def add_vertex(self, patch_id: str, cost: float = _DEFAULT_PATCH_COST,
                   metadata: dict[str, Any] | None = None) -> None:
        """Add a patch vertex to the DAG.

        If the vertex already exists its cost and metadata are updated.

        Parameters
        ----------
        patch_id:
            Unique identifier of the patch.
        cost:
            Estimated execution cost used in critical path analysis.
        metadata:
            Optional extra attributes stored verbatim.
        """
        self.vertices[patch_id] = {
            "patch_id": patch_id,
            "cost": cost,
            "metadata": metadata or {},
            "trust_tier": self.trust_tier,
        }
        _LOG.debug("DAG %s: added vertex %s (cost=%.2f)", self.dag_id, patch_id, cost)

    def remove_vertex(self, patch_id: str) -> None:
        """Remove *patch_id* and all incident edges from the DAG.

        Parameters
        ----------
        patch_id:
            Identifier of the patch to remove.

        Raises
        ------
        KeyError
            If the patch is not present.
        """
        if patch_id not in self.vertices:
            raise KeyError(f"Patch '{patch_id}' not in DAG '{self.dag_id}'")
        del self.vertices[patch_id]
        self.edges = [
            e for e in self.edges
            if e.producer_id != patch_id and e.consumer_id != patch_id
        ]
        _LOG.debug("DAG %s: removed vertex %s and its incident edges", self.dag_id, patch_id)

    # ------------------------------------------------------------------
    # Edge management
    # ------------------------------------------------------------------

    def add_edge(self, edge: DependencyEdge) -> None:
        """Add a dependency edge.

        Both endpoints must already be registered as vertices.  Duplicate
        edges (same producer, consumer, and interface_key) are silently
        ignored.

        Parameters
        ----------
        edge:
            The directed dependency edge to add.

        Raises
        ------
        KeyError
            If either endpoint is not a vertex in the DAG.
        """
        if edge.producer_id not in self.vertices:
            raise KeyError(
                f"Producer '{edge.producer_id}' not in DAG '{self.dag_id}'. "
                "Add the vertex before adding edges."
            )
        if edge.consumer_id not in self.vertices:
            raise KeyError(
                f"Consumer '{edge.consumer_id}' not in DAG '{self.dag_id}'. "
                "Add the vertex before adding edges."
            )
        # Deduplicate
        for existing in self.edges:
            if (existing.producer_id == edge.producer_id
                    and existing.consumer_id == edge.consumer_id
                    and existing.interface_key == edge.interface_key):
                _LOG.debug(
                    "DAG %s: duplicate edge %s ignored", self.dag_id, edge
                )
                return
        self.edges.append(edge)
        _LOG.debug("DAG %s: added edge %s", self.dag_id, edge)

    # ------------------------------------------------------------------
    # Adjacency accessors
    # ------------------------------------------------------------------

    def successors(self, patch_id: str) -> list[str]:
        """Return patches that depend on *patch_id* (its direct consumers)."""
        return [e.consumer_id for e in self.edges if e.producer_id == patch_id]

    def predecessors(self, patch_id: str) -> list[str]:
        """Return patches that *patch_id* depends on (its direct producers)."""
        return [e.producer_id for e in self.edges if e.consumer_id == patch_id]

    def in_degree(self, patch_id: str) -> int:
        """Number of incoming edges for *patch_id*."""
        return sum(1 for e in self.edges if e.consumer_id == patch_id)

    def out_degree(self, patch_id: str) -> int:
        """Number of outgoing edges from *patch_id*."""
        return sum(1 for e in self.edges if e.producer_id == patch_id)

    def __repr__(self) -> str:  # noqa: D105
        return (
            f"DependencyDAG(id={self.dag_id!r}, "
            f"vertices={len(self.vertices)}, edges={len(self.edges)}, "
            f"trust_tier={self.trust_tier!r})"
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_adjacency(dag: DependencyDAG) -> dict[str, list[str]]:
    """Return a successor-list adjacency map for *dag*."""
    adj: dict[str, list[str]] = {v: [] for v in dag.vertices}
    for edge in dag.edges:
        adj[edge.producer_id].append(edge.consumer_id)
    return adj


def _build_in_degree_map(dag: DependencyDAG) -> dict[str, int]:
    """Return a mapping from each vertex to its in-degree."""
    in_deg: dict[str, int] = {v: 0 for v in dag.vertices}
    for edge in dag.edges:
        in_deg[edge.consumer_id] += 1
    return in_deg


def _kahn_topological_sort(dag: DependencyDAG) -> tuple[list[str], list[list[str]]]:
    """Kahn's algorithm for topological sort with level grouping.

    Returns
    -------
    order : list[str]
        A linear extension of the dependency partial order.
    levels : list[list[str]]
        Each sub-list contains the patches at the same Coffman–Graham level
        (all can execute concurrently).

    Raises
    ------
    CyclicDependencyError
        If the graph contains a cycle.
    """
    adj = _build_adjacency(dag)
    in_deg = _build_in_degree_map(dag)

    queue: deque[str] = deque(
        sorted(v for v, d in in_deg.items() if d == 0)
    )
    order: list[str] = []
    levels: list[list[str]] = []

    while queue:
        # Drain entire current level in one pass
        level_size = len(queue)
        level: list[str] = []
        for _ in range(level_size):
            node = queue.popleft()
            order.append(node)
            level.append(node)
            for succ in adj[node]:
                in_deg[succ] -= 1
                if in_deg[succ] == 0:
                    queue.append(succ)
        levels.append(level)

    if len(order) != len(dag.vertices):
        # Some vertices were not visited — a cycle exists
        visited = set(order)
        remaining = [v for v in dag.vertices if v not in visited]
        raise CyclicDependencyError(remaining, dag_id=dag.dag_id)

    return order, levels


def _find_cycle_dfs(dag: DependencyDAG) -> list[str]:
    """Depth-first search to find one cycle in *dag*.

    Returns an empty list if no cycle exists; otherwise returns a
    representative cycle as a list of patch IDs.
    """
    adj = _build_adjacency(dag)
    WHITE, GRAY, BLACK = 0, 1, 2
    colour: dict[str, int] = {v: WHITE for v in dag.vertices}
    parent: dict[str, str | None] = {v: None for v in dag.vertices}

    def dfs(node: str) -> list[str]:
        colour[node] = GRAY
        for nbr in adj[node]:
            if colour[nbr] == GRAY:
                # Back edge — reconstruct cycle
                cycle = [nbr]
                curr = node
                while curr != nbr:
                    cycle.append(curr)
                    p = parent[curr]
                    if p is None:
                        break
                    curr = p
                cycle.append(nbr)
                cycle.reverse()
                return cycle
            if colour[nbr] == WHITE:
                parent[nbr] = node
                result = dfs(nbr)
                if result:
                    return result
        colour[node] = BLACK
        return []

    for v in dag.vertices:
        if colour[v] == WHITE:
            cycle = dfs(v)
            if cycle:
                return cycle
    return []


def _longest_path_dp(dag: DependencyDAG) -> tuple[dict[str, float], dict[str, str | None]]:
    """Compute longest-path distances from all sources using DP on the DAG.

    Assumes the graph is acyclic; call after verifying no cycles.

    Returns
    -------
    dist : dict[str, float]
        ``dist[v]`` is the maximum total cost of any path ending at ``v``.
    pred : dict[str, str | None]
        Predecessor mapping for path reconstruction.
    """
    # Topological order is required for correct DP
    order, _ = _kahn_topological_sort(dag)
    cost_of: dict[str, float] = {
        v: dag.vertices[v].get("cost", _DEFAULT_PATCH_COST)
        for v in dag.vertices
    }
    dist: dict[str, float] = {v: cost_of[v] for v in dag.vertices}
    pred: dict[str, str | None] = {v: None for v in dag.vertices}
    adj = _build_adjacency(dag)

    for node in order:
        node_dist = dist[node]
        for succ in adj[node]:
            candidate = node_dist + cost_of[succ]
            if candidate > dist[succ]:
                dist[succ] = candidate
                pred[succ] = node

    return dist, pred


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------


class DependencyOrderingCoordinator:
    """Builds the dependency DAG and produces an ordered execution plan.

    The coordinator is the entry point for consumers of this module.  Its
    responsibilities are:

    1. Accepting patch descriptors and interface-dependency declarations.
    2. Constructing the ``DependencyDAG`` incrementally.
    3. Delegating analysis to ``DependencyOrderingAnalyzer``.
    4. Delegating certification to ``DependencyOrderingWitness``.
    5. Returning a ``TopologicalOrder`` that can be used directly as an
       execution schedule.

    All generated patches enter at the ``PROPOSAL`` trust tier as required
    by theory2.tex §12.
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        """Initialise the coordinator.

        Parameters
        ----------
        config:
            Optional configuration.  Recognised keys:

            ``auto_detect_cycles``
                Run cycle detection before attempting topological sort.
                Provides a better error message at the cost of an extra
                DFS pass.  Default: ``True``.
            ``prefer_parallelism``
                When ``True``, apply Coffman–Graham tie-breaking to
                maximise concurrency.  Default: ``True``.
            ``default_patch_cost``
                Cost assigned to patches without an explicit cost.
                Default: ``1.0``.
        """
        defaults: dict[str, Any] = {
            "auto_detect_cycles": True,
            "prefer_parallelism": True,
            "default_patch_cost": _DEFAULT_PATCH_COST,
        }
        cfg = dict(defaults)
        if config:
            cfg.update(config)
        self._config = cfg
        self._logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
        self._analyzer = DependencyOrderingAnalyzer(config=cfg)
        self._witness = DependencyOrderingWitness()
        self._dag: DependencyDAG = DependencyDAG()
        self._ordering_history: list[TopologicalOrder] = []

    # ------------------------------------------------------------------
    # DAG construction
    # ------------------------------------------------------------------

    def register_patch(
        self,
        patch_id: str,
        cost: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Register a patch as a vertex in the dependency DAG.

        Parameters
        ----------
        patch_id:
            Unique identifier of the patch.
        cost:
            Estimated execution cost.  Falls back to
            ``config["default_patch_cost"]`` when not supplied.
        metadata:
            Arbitrary key-value pairs attached to the vertex.
        """
        effective_cost = cost if cost is not None else self._config["default_patch_cost"]
        self._dag.add_vertex(patch_id, cost=effective_cost, metadata=metadata or {})
        self._logger.debug(
            "Registered patch '%s' (cost=%.2f) in DAG %s",
            patch_id, effective_cost, self._dag.dag_id,
        )

    def register_patches_from_descriptors(
        self,
        descriptors: list[PatchDescriptor],
    ) -> None:
        """Bulk-register patches from a list of ``PatchDescriptor`` objects.

        Parameters
        ----------
        descriptors:
            Each descriptor must have a ``patch_id`` attribute and
            optionally a ``cost`` attribute.
        """
        for desc in descriptors:
            cost = getattr(desc, "cost", self._config["default_patch_cost"])
            meta = getattr(desc, "metadata", {})
            self.register_patch(desc.patch_id, cost=cost, metadata=meta)

    def declare_dependency(
        self,
        producer_id: str,
        consumer_id: str,
        interface_key: str = "",
        weight: float = 1.0,
    ) -> None:
        """Declare that *consumer_id* depends on *producer_id*.

        Parameters
        ----------
        producer_id:
            The patch that must execute first.
        consumer_id:
            The patch that requires the producer's output.
        interface_key:
            Optional label identifying which interface export/import
            creates this dependency.
        weight:
            Numeric weight for critical path weighting.
        """
        edge = DependencyEdge(
            producer_id=producer_id,
            consumer_id=consumer_id,
            interface_key=interface_key,
            weight=weight,
        )
        self._dag.add_edge(edge)
        self._logger.debug(
            "Declared dependency: %s → %s (key=%r)", producer_id, consumer_id, interface_key
        )

    def declare_dependencies_from_interface_deps(
        self,
        deps: list[InterfaceDependency],
    ) -> None:
        """Bulk-declare dependencies from ``InterfaceDependency`` objects.

        Parameters
        ----------
        deps:
            Each object must have ``producer_id``, ``consumer_id``, and
            optionally ``interface_key`` attributes.
        """
        for dep in deps:
            key = getattr(dep, "interface_key", "")
            self.declare_dependency(dep.producer_id, dep.consumer_id, interface_key=key)

    # ------------------------------------------------------------------
    # Plan production
    # ------------------------------------------------------------------

    def build_execution_plan(self) -> TopologicalOrder:
        """Build and certify an ordered execution plan from the current DAG.

        Returns
        -------
        TopologicalOrder
            A certified linear extension maximising parallelism.

        Raises
        ------
        CyclicDependencyError
            If a circular dependency is detected.
        """
        dag = self._dag
        self._logger.info(
            "Building execution plan for DAG %s (%d patches, %d edges)",
            dag.dag_id, len(dag.vertices), len(dag.edges),
        )

        if self._config["auto_detect_cycles"]:
            cycle = self._analyzer.detect_cycle(dag)
            if cycle:
                raise CyclicDependencyError(cycle, dag_id=dag.dag_id)

        ordering = self._analyzer.topological_sort(dag)
        certificate = self._witness.certify_ordering(ordering, dag)

        if not certificate["valid"]:
            raise CoverDesignError(
                f"Ordering certification failed: {certificate['reason']}"
            )

        self._ordering_history.append(ordering)
        self._logger.info("Execution plan ready: %s", ordering)
        return ordering

    def compute_critical_path(self) -> CriticalPath:
        """Compute the critical path through the current dependency DAG.

        The critical path is the longest (by total patch cost) directed path
        and represents the minimum possible wall time.

        Returns
        -------
        CriticalPath
            The identified critical path with total cost.

        Raises
        ------
        CyclicDependencyError
            If the DAG contains a cycle (critical path is only defined for
            acyclic graphs).
        """
        cycle = self._analyzer.detect_cycle(self._dag)
        if cycle:
            raise CyclicDependencyError(cycle, dag_id=self._dag.dag_id)

        critical = self._analyzer.critical_path_analysis(self._dag)
        self._logger.info("Critical path: %s", critical)
        return critical

    def reset(self) -> None:
        """Discard the current DAG and start fresh.

        The ordering history is preserved for auditing.
        """
        old_id = self._dag.dag_id
        self._dag = DependencyDAG()
        self._logger.info(
            "DAG reset: replaced %s with %s", old_id, self._dag.dag_id
        )

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def dag(self) -> DependencyDAG:
        """The current dependency DAG (read access)."""
        return self._dag

    @property
    def ordering_history(self) -> list[TopologicalOrder]:
        """All topological orders produced so far."""
        return list(self._ordering_history)

    def __repr__(self) -> str:  # noqa: D105
        return (
            f"DependencyOrderingCoordinator("
            f"dag={self._dag.dag_id!r}, "
            f"patches={len(self._dag.vertices)}, "
            f"edges={len(self._dag.edges)})"
        )


# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------


class DependencyOrderingAnalyzer:
    """Performs topological analysis, cycle detection, and critical path analysis.

    This class encapsulates all graph-algorithm concerns and is designed to
    be pure (no side effects on the DAG it analyses).  It is used internally
    by ``DependencyOrderingCoordinator`` but can also be used directly for
    ad-hoc analysis.
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        """Initialise the analyzer.

        Parameters
        ----------
        config:
            Optional configuration overrides (shares structure with
            ``DependencyOrderingCoordinator``'s config).
        """
        self._config: dict[str, Any] = config or {}
        self._logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")

    # ------------------------------------------------------------------
    # Cycle detection
    # ------------------------------------------------------------------

    def detect_cycle(self, dag: DependencyDAG) -> list[str]:
        """Attempt to find a cycle in *dag*.

        Uses a DFS-based colouring algorithm.  Returns an empty list when
        the graph is acyclic.

        Parameters
        ----------
        dag:
            The dependency DAG to inspect.

        Returns
        -------
        list[str]
            A representative cycle (sequence of patch IDs) if one exists,
            or an empty list if the graph is acyclic.
        """
        cycle = _find_cycle_dfs(dag)
        if cycle:
            self._logger.warning(
                "Cycle detected in DAG %s: %s",
                dag.dag_id, " → ".join(cycle),
            )
        else:
            self._logger.debug("DAG %s is acyclic", dag.dag_id)
        return cycle

    def has_cycle(self, dag: DependencyDAG) -> bool:
        """Return ``True`` iff *dag* contains a directed cycle."""
        return bool(self.detect_cycle(dag))

    # ------------------------------------------------------------------
    # Topological sort
    # ------------------------------------------------------------------

    def topological_sort(self, dag: DependencyDAG) -> TopologicalOrder:
        """Perform a topological sort of *dag*, maximising parallelism.

        Uses Kahn's algorithm with level-grouping to obtain a Coffman–Graham
        style schedule: at each level, all patches with satisfied dependencies
        (in-degree zero after removing earlier levels) are grouped together.

        Parameters
        ----------
        dag:
            The dependency DAG.  Must be acyclic; callers should invoke
            ``detect_cycle`` beforehand.

        Returns
        -------
        TopologicalOrder
            The computed ordering.

        Raises
        ------
        CyclicDependencyError
            Re-raised from Kahn's algorithm if a cycle is present.
        """
        order, levels = _kahn_topological_sort(dag)
        frozen_levels = tuple(frozenset(lvl) for lvl in levels)
        result = TopologicalOrder(
            order_id=str(uuid.uuid4()),
            patch_sequence=tuple(order),
            levels=frozen_levels,
            dag_id=dag.dag_id,
            computed_at=time.time(),
        )
        self._logger.info(
            "Topological sort of DAG %s: %d patches across %d level(s), "
            "max parallelism=%d",
            dag.dag_id, len(order), result.depth, result.width,
        )
        return result

    # ------------------------------------------------------------------
    # Critical path
    # ------------------------------------------------------------------

    def critical_path_analysis(self, dag: DependencyDAG) -> CriticalPath:
        """Identify the critical path through the dependency DAG.

        The critical path is the path with maximum total vertex cost,
        representing the minimum achievable wall time regardless of
        parallelism.

        Parameters
        ----------
        dag:
            An acyclic dependency DAG.

        Returns
        -------
        CriticalPath
            The critical path with nodes and total cost.
        """
        if not dag.vertices:
            return CriticalPath(
                path_id=str(uuid.uuid4()),
                nodes=(),
                total_cost=0.0,
                dag_id=dag.dag_id,
                computed_at=time.time(),
            )

        dist, pred = _longest_path_dp(dag)

        # Find the endpoint with maximum distance
        sink = max(dist, key=lambda v: dist[v])
        total_cost = dist[sink]

        # Reconstruct path by following predecessor chain
        path: list[str] = []
        curr: str | None = sink
        while curr is not None:
            path.append(curr)
            curr = pred[curr]
        path.reverse()

        critical = CriticalPath(
            path_id=str(uuid.uuid4()),
            nodes=tuple(path),
            total_cost=total_cost,
            dag_id=dag.dag_id,
            computed_at=time.time(),
        )
        self._logger.info(
            "Critical path for DAG %s: cost=%.3f, length=%d",
            dag.dag_id, total_cost, critical.length,
        )
        return critical

    # ------------------------------------------------------------------
    # Summary statistics
    # ------------------------------------------------------------------

    def summarise(self, dag: DependencyDAG) -> dict[str, Any]:
        """Produce a summary of the DAG's structural properties.

        Returns
        -------
        dict
            Keys include:
            ``vertex_count``, ``edge_count``, ``source_count``
            (vertices with in-degree zero), ``sink_count``
            (vertices with out-degree zero), ``is_acyclic``,
            ``max_in_degree``, ``max_out_degree``.
        """
        in_deg = _build_in_degree_map(dag)
        adj = _build_adjacency(dag)
        out_deg = {v: len(adj[v]) for v in dag.vertices}

        sources = [v for v, d in in_deg.items() if d == 0]
        sinks = [v for v, d in out_deg.items() if d == 0]
        is_acyclic = not self.has_cycle(dag)

        return {
            "dag_id": dag.dag_id,
            "vertex_count": len(dag.vertices),
            "edge_count": len(dag.edges),
            "source_count": len(sources),
            "sink_count": len(sinks),
            "sources": sources,
            "sinks": sinks,
            "is_acyclic": is_acyclic,
            "max_in_degree": max(in_deg.values(), default=0),
            "max_out_degree": max(out_deg.values(), default=0),
            "trust_tier": dag.trust_tier,
        }

    def __repr__(self) -> str:  # noqa: D105
        return "DependencyOrderingAnalyzer()"


# ---------------------------------------------------------------------------
# Witness
# ---------------------------------------------------------------------------


class DependencyOrderingWitness:
    """Certifies that a topological ordering is valid and cycle-free.

    A *witness* in the theory2.tex sense is an object that holds a
    certificate of validity: if the witness was constructed without error,
    the ordering it certifies is a correct linear extension of the
    dependency partial order, and no cycles exist in the source DAG.

    The certificate is a plain dict for easy serialisation and auditing.
    """

    def __init__(self) -> None:  # noqa: D107
        self._logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
        self._certificates: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Certification
    # ------------------------------------------------------------------

    def certify_ordering(
        self,
        ordering: TopologicalOrder,
        dag: DependencyDAG,
    ) -> dict[str, Any]:
        """Verify that *ordering* is a valid topological sort of *dag*.

        The check walks every edge ``(u, v)`` in *dag* and confirms that
        ``u`` appears before ``v`` in the ordering's ``patch_sequence``.
        Additionally verifies that:

        * Every vertex in the DAG is present in the ordering.
        * No vertex appears more than once in the ordering.
        * The ordering's ``dag_id`` matches ``dag.dag_id``.

        Parameters
        ----------
        ordering:
            The topological order to certify.
        dag:
            The dependency DAG against which to certify.

        Returns
        -------
        dict
            ``{
                "certificate_id": str,
                "valid": bool,
                "reason": str,
                "checked_edges": int,
                "violations": list[dict],
                "certified_at": float,
            }``
        """
        violations: list[dict[str, Any]] = []
        cert_id = str(uuid.uuid4())

        # 1. ID consistency
        if ordering.dag_id != dag.dag_id:
            violations.append({
                "type": "dag_id_mismatch",
                "ordering_dag_id": ordering.dag_id,
                "dag_dag_id": dag.dag_id,
            })

        # 2. Coverage check
        ordering_vertices = set(ordering.patch_sequence)
        dag_vertices = set(dag.vertices.keys())
        missing_in_ordering = dag_vertices - ordering_vertices
        extra_in_ordering = ordering_vertices - dag_vertices
        if missing_in_ordering:
            violations.append({"type": "missing_vertices", "ids": sorted(missing_in_ordering)})
        if extra_in_ordering:
            violations.append({"type": "extra_vertices", "ids": sorted(extra_in_ordering)})

        # 3. Uniqueness check
        seen: dict[str, int] = {}
        for idx, patch_id in enumerate(ordering.patch_sequence):
            if patch_id in seen:
                violations.append({
                    "type": "duplicate_vertex",
                    "patch_id": patch_id,
                    "first_at": seen[patch_id],
                    "duplicate_at": idx,
                })
            seen[patch_id] = idx

        # 4. Edge ordering check (producers before consumers)
        position: dict[str, int] = {
            p: i for i, p in enumerate(ordering.patch_sequence)
        }
        checked_edges = 0
        for edge in dag.edges:
            checked_edges += 1
            pos_prod = position.get(edge.producer_id)
            pos_cons = position.get(edge.consumer_id)
            if pos_prod is None or pos_cons is None:
                continue  # already caught by coverage check
            if pos_prod >= pos_cons:
                violations.append({
                    "type": "ordering_violation",
                    "edge": str(edge),
                    "producer_position": pos_prod,
                    "consumer_position": pos_cons,
                })

        valid = len(violations) == 0
        reason = "OK" if valid else f"{len(violations)} violation(s) found"

        cert: dict[str, Any] = {
            "certificate_id": cert_id,
            "ordering_id": ordering.order_id,
            "dag_id": dag.dag_id,
            "valid": valid,
            "reason": reason,
            "checked_edges": checked_edges,
            "violations": violations,
            "certified_at": time.time(),
        }
        self._certificates.append(cert)

        if valid:
            self._logger.info(
                "Ordering %s certified OK (%d edges checked)",
                ordering.order_id, checked_edges,
            )
        else:
            self._logger.error(
                "Ordering %s FAILED certification: %s",
                ordering.order_id, reason,
            )

        return cert

    def certify_acyclicity(self, dag: DependencyDAG) -> dict[str, Any]:
        """Certify that *dag* contains no directed cycles.

        Parameters
        ----------
        dag:
            The DAG to inspect.

        Returns
        -------
        dict
            ``{
                "certificate_id": str,
                "dag_id": str,
                "acyclic": bool,
                "cycle_found": list[str],
                "certified_at": float,
            }``
        """
        cert_id = str(uuid.uuid4())
        cycle = _find_cycle_dfs(dag)
        acyclic = len(cycle) == 0

        cert: dict[str, Any] = {
            "certificate_id": cert_id,
            "dag_id": dag.dag_id,
            "acyclic": acyclic,
            "cycle_found": cycle,
            "certified_at": time.time(),
        }
        self._certificates.append(cert)

        if acyclic:
            self._logger.info("Acyclicity certificate issued for DAG %s", dag.dag_id)
        else:
            self._logger.warning(
                "DAG %s has a cycle: %s", dag.dag_id, " → ".join(cycle)
            )

        return cert

    # ------------------------------------------------------------------
    # Certificate retrieval
    # ------------------------------------------------------------------

    @property
    def certificates(self) -> list[dict[str, Any]]:
        """All certificates issued so far (read-only copy)."""
        return list(self._certificates)

    def latest_certificate(self) -> dict[str, Any] | None:
        """Return the most recently issued certificate, or ``None``."""
        return self._certificates[-1] if self._certificates else None

    def __repr__(self) -> str:  # noqa: D105
        return f"DependencyOrderingWitness(certificates={len(self._certificates)})"


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    print("=== dependency_ordering smoke test ===\n")

    # Build a simple diamond DAG:  A → B, A → C, B → D, C → D
    coord = DependencyOrderingCoordinator()
    for pid, cost in [("A", 1.0), ("B", 2.0), ("C", 3.0), ("D", 1.0)]:
        coord.register_patch(pid, cost=cost)

    coord.declare_dependency("A", "B", interface_key="export_alpha")
    coord.declare_dependency("A", "C", interface_key="export_beta")
    coord.declare_dependency("B", "D", interface_key="export_gamma")
    coord.declare_dependency("C", "D", interface_key="export_delta")

    print("DAG:", coord.dag)

    plan = coord.build_execution_plan()
    print("Execution plan:", plan)
    print("  sequence:", list(plan.patch_sequence))
    print("  levels:")
    for i, lvl in enumerate(plan.levels):
        print(f"    level {i}: {sorted(lvl)}")

    critical = coord.compute_critical_path()
    print("Critical path:", critical)
    print()

    # Verify that a cyclic graph raises correctly
    coord2 = DependencyOrderingCoordinator()
    for pid in ["X", "Y", "Z"]:
        coord2.register_patch(pid)
    coord2.declare_dependency("X", "Y")
    coord2.declare_dependency("Y", "Z")
    coord2.declare_dependency("Z", "X")  # cycle!

    try:
        coord2.build_execution_plan()
        print("ERROR: should have raised CyclicDependencyError")
    except CyclicDependencyError as exc:
        print(f"Cycle correctly detected: {exc}")

    # Analyzer summary
    analyzer = DependencyOrderingAnalyzer()
    summary = analyzer.summarise(coord.dag)
    print("\nDAG summary:", summary)

    print("\nSmoke test passed.")
