r"""Cover design algorithms for JuGeo — theory2.tex §cover_design.

Theory (theory2.tex §cover_design — Cover Design):
    A *cover design* for a judgment site :math:`S` is a finite family of
    open patches :math:`\mathcal{U} = \{U_i\}_{i \in I}` such that

    .. math::

        S \;\subseteq\; \bigcup_{i \in I} U_i

    together with a budget allocation :math:`\{b_i\}_{i \in I}` satisfying

    .. math::

        \sum_{i \in I} b_i \;\leq\; B - \beta \cdot B

    where :math:`B` is the total budget and :math:`\beta \in [0,1)` is the
    overhead fraction.

    The *Čech condition* (§cover_design.2) requires that sections
    :math:`s_i : U_i \to \mathcal{F}(U_i)` and
    :math:`s_j : U_j \to \mathcal{F}(U_j)` satisfy

    .. math::

        s_i\big|_{U_i \cap U_j} \;=\; s_j\big|_{U_i \cap U_j}

    for all :math:`i, j \in I`.  This ensures that the locally defined
    sections glue into a globally consistent section over all of :math:`S`.

    The *dependency DAG* :math:`D = (I, E)` encodes ordering constraints
    between patches: :math:`(i, j) \in E` means patch :math:`j` must be
    applied after patch :math:`i`.  Kahn's algorithm produces a valid
    topological order; the critical path gives the minimum makespan under
    the assumption that patches on the critical path cannot be parallelised.

    Dilworth's theorem guarantees that the minimum number of chains needed
    to cover the partial order equals the maximum antichain width.  The
    antichain decomposition partitions patches into *generation waves* that
    can be executed in parallel.

    Generated code enters at the **PROPOSAL** trust tier (§cover_design.8):
    no section produced by this module is automatically trusted — it must
    pass the theorem checks in :mod:`theorems` before being admitted.

    copilot: algorithms-marker

Public API
----------
``greedy_cover_algorithm``
    Select patches to minimise uncovered area while respecting budget.
``topological_sort_patches``
    Kahn's algorithm for topological sorting of the patch DAG.
``compute_critical_path``
    Longest-path algorithm for critical-path analysis.
``compute_antichain_decomposition``
    Decompose partial order into antichains (parallel generation waves).
``check_cech_condition``
    Verify that two sections agree on their overlap region.
``compute_overlap_graph``
    Build the graph of which patches overlap one another.
``priority_weighted_allocation``
    Compute per-patch budget allocations respecting priorities and overhead.
``compute_coverage_completeness``
    Fraction of site covered by the current patch family.
``compute_coffman_graham_order``
    Coffman–Graham scheduling algorithm for bounded-width schedules.
``estimate_patch_cost``
    Cost model for a single patch descriptor.
``DependencyGraph``
    Lightweight adjacency-list DAG for patch dependencies.
``OverlapGraph``
    Graph recording which patches have non-empty pairwise intersections.
``ScheduleResult``
    Immutable result of a scheduling computation.
"""

from __future__ import annotations

import logging
import math
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    pass

try:
    from jugeo.generation.cover_design.models import (  # type: ignore[import]
        PatchDescriptor,
        Budget,
        CoverSection,
    )
except ImportError:
    PatchDescriptor = Any  # type: ignore[assignment, misc]
    Budget = Any  # type: ignore[assignment, misc]
    CoverSection = Any  # type: ignore[assignment, misc]

__all__ = [
    # Helper classes
    "DependencyGraph",
    "OverlapGraph",
    "ScheduleResult",
    # Core algorithm functions
    "greedy_cover_algorithm",
    "topological_sort_patches",
    "compute_critical_path",
    "compute_antichain_decomposition",
    "check_cech_condition",
    "compute_overlap_graph",
    "priority_weighted_allocation",
    "compute_coverage_completeness",
    "compute_coffman_graham_order",
    "estimate_patch_cost",
    # Internal helpers (exported for testing)
    "_normalize_dag",
    "_compute_in_degrees",
    "_transitive_reduction",
    "_patch_area",
    "_patch_intersection_area",
    "_validate_budget",
]

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_OVERHEAD_FRACTION: float = 0.10
_MIN_PATCH_SIZE: float = 1e-9          # patches smaller than this are ignored
_COST_PER_UNIT_AREA: float = 1.0       # base cost coefficient
_COST_COMPLEXITY_SCALE: float = 0.5    # complexity multiplier
_COST_PRIORITY_DISCOUNT: float = 0.15  # high-priority patches get a discount
_COVERAGE_TOLERANCE: float = 1e-9      # floating-point tolerance for area checks
_MAX_DAG_NODES: int = 10_000           # guard against pathological inputs

# Budget fields expected on patch descriptors
_BUDGET_KEY: str = "budget"
_PRIORITY_KEY: str = "priority"
_AREA_KEY: str = "area"
_COMPLEXITY_KEY: str = "complexity"
_COST_KEY: str = "cost"
_ID_KEY: str = "patch_id"
_COORDS_KEY: str = "coords"


# ---------------------------------------------------------------------------
# Helper classes
# ---------------------------------------------------------------------------

@dataclass
class DependencyGraph:
    """Lightweight adjacency-list directed acyclic graph for patch dependencies.

    Each node is identified by a string patch ID.  Edges run from
    *predecessor* to *successor*: an edge ``(u, v)`` means patch ``v``
    depends on patch ``u`` (i.e., ``u`` must be completed before ``v``).

    Theory reference: theory2.tex §cover_design.5 (Dependency ordering).

    Attributes
    ----------
    nodes:
        Set of all patch IDs registered in this graph.
    edges:
        Dict mapping each node to the set of its direct successors.
    reverse_edges:
        Dict mapping each node to the set of its direct predecessors.
    """

    nodes: set[str] = field(default_factory=set)
    edges: dict[str, set[str]] = field(default_factory=dict)
    reverse_edges: dict[str, set[str]] = field(default_factory=dict)

    def add_node(self, node: str) -> None:
        """Register *node* in the graph without adding any edges.

        Parameters
        ----------
        node:
            Patch ID string to register.
        """
        self.nodes.add(node)
        self.edges.setdefault(node, set())
        self.reverse_edges.setdefault(node, set())

    def add_edge(self, predecessor: str, successor: str) -> None:
        """Add a directed edge from *predecessor* to *successor*.

        Both nodes are auto-registered if not already present.

        Parameters
        ----------
        predecessor:
            The patch that must be completed first.
        successor:
            The patch that depends on *predecessor*.
        """
        self.add_node(predecessor)
        self.add_node(successor)
        self.edges[predecessor].add(successor)
        self.reverse_edges[successor].add(predecessor)

    def successors(self, node: str) -> set[str]:
        """Return the direct successors of *node*.

        Parameters
        ----------
        node:
            Patch ID.

        Returns
        -------
        set[str]
            Possibly empty set of successor IDs.
        """
        return set(self.edges.get(node, set()))

    def predecessors(self, node: str) -> set[str]:
        """Return the direct predecessors of *node*.

        Parameters
        ----------
        node:
            Patch ID.

        Returns
        -------
        set[str]
            Possibly empty set of predecessor IDs.
        """
        return set(self.reverse_edges.get(node, set()))

    def has_cycle(self) -> bool:
        """Return ``True`` if the graph contains a directed cycle.

        Uses iterative DFS with three-colour marking.

        Returns
        -------
        bool
        """
        WHITE, GREY, BLACK = 0, 1, 2
        colour: dict[str, int] = {n: WHITE for n in self.nodes}

        def dfs(start: str) -> bool:
            stack: list[tuple[str, bool]] = [(start, False)]
            while stack:
                node, leaving = stack.pop()
                if leaving:
                    colour[node] = BLACK
                    continue
                if colour[node] == GREY:
                    return True  # back edge → cycle
                if colour[node] == BLACK:
                    continue
                colour[node] = GREY
                stack.append((node, True))
                for succ in self.edges.get(node, set()):
                    if colour[succ] != BLACK:
                        stack.append((succ, False))
            return False

        for n in self.nodes:
            if colour[n] == WHITE:
                if dfs(n):
                    return True
        return False

    def as_dict(self) -> dict[str, list[str]]:
        """Serialise edges as a plain dict of lists.

        Returns
        -------
        dict[str, list[str]]
            Maps each node ID to a sorted list of its successor IDs.
        """
        return {n: sorted(self.edges.get(n, set())) for n in sorted(self.nodes)}


@dataclass
class OverlapGraph:
    """Graph recording which patches have non-empty pairwise intersections.

    An edge ``{u, v}`` exists iff patches ``u`` and ``v`` share a region of
    positive area.  The overlap area for each pair is stored in
    ``overlap_areas``.

    Theory reference: theory2.tex §cover_design.2 (Čech condition, overlaps).

    Attributes
    ----------
    patch_ids:
        Ordered list of patch IDs.
    adjacency:
        Dict mapping each patch ID to the set of overlapping patch IDs.
    overlap_areas:
        Dict mapping ``(min_id, max_id)`` tuples to float overlap areas.
    """

    patch_ids: list[str] = field(default_factory=list)
    adjacency: dict[str, set[str]] = field(default_factory=dict)
    overlap_areas: dict[tuple[str, str], float] = field(default_factory=dict)

    def add_overlap(self, id_a: str, id_b: str, area: float) -> None:
        """Record that patches *id_a* and *id_b* overlap with the given *area*.

        Parameters
        ----------
        id_a, id_b:
            Patch IDs of the two overlapping patches.
        area:
            Non-negative overlap area.
        """
        if area <= _COVERAGE_TOLERANCE:
            return
        self.adjacency.setdefault(id_a, set()).add(id_b)
        self.adjacency.setdefault(id_b, set()).add(id_a)
        key = (min(id_a, id_b), max(id_a, id_b))
        self.overlap_areas[key] = area

    def get_overlap_area(self, id_a: str, id_b: str) -> float:
        """Return the overlap area between *id_a* and *id_b* (0.0 if none).

        Parameters
        ----------
        id_a, id_b:
            Patch IDs.

        Returns
        -------
        float
        """
        key = (min(id_a, id_b), max(id_a, id_b))
        return self.overlap_areas.get(key, 0.0)

    def neighbours(self, patch_id: str) -> set[str]:
        """Return all patches that overlap *patch_id*.

        Parameters
        ----------
        patch_id:
            Patch ID.

        Returns
        -------
        set[str]
        """
        return set(self.adjacency.get(patch_id, set()))

    def edge_count(self) -> int:
        """Return the number of undirected overlap edges.

        Returns
        -------
        int
        """
        return len(self.overlap_areas)


@dataclass(frozen=True, slots=True)
class ScheduleResult:
    """Immutable result of a scheduling computation.

    Records the generation waves produced by antichain decomposition or
    Coffman–Graham scheduling, together with the critical-path length and
    the assignment of patches to waves.

    Theory reference: theory2.tex §cover_design.6 (Parallelism, wave scheduling).

    Attributes
    ----------
    waves:
        Tuple of tuples, one per generation wave.  Each inner tuple holds
        the patch IDs assigned to that wave.
    critical_path_length:
        Total cost along the longest path through the dependency DAG.
    critical_path_nodes:
        Tuple of patch IDs forming the critical path (in topological order).
    makespan:
        Total elapsed cost when all waves execute in sequence.
    schedule_id:
        Unique identifier for this scheduling result.
    """

    waves: tuple[tuple[str, ...], ...]
    critical_path_length: float
    critical_path_nodes: tuple[str, ...]
    makespan: float
    schedule_id: str


# ---------------------------------------------------------------------------
# 1.  Greedy cover algorithm
# ---------------------------------------------------------------------------

def greedy_cover_algorithm(
    site_size: float,
    patch_candidates: list[dict[str, Any]],
    budget: float,
) -> list[dict[str, Any]]:
    """Select patches to minimise uncovered area while respecting budget.

    This is a greedy approximation of the weighted set-cover problem.  At
    each step the patch with the best ratio of *new coverage gained* to
    *cost incurred* is selected, provided its cost does not exceed the
    remaining budget.  The algorithm terminates when the site is fully
    covered, the budget is exhausted, or no improving patch remains.

    Approximation guarantee: the greedy algorithm achieves a
    :math:`(1 - 1/e) \approx 63\\%` coverage fraction guarantee when the
    site decomposes into unit cells.  See Hochbaum (1997) for the weighted
    variant.

    Theory reference: theory2.tex §cover_design.1 (Cover completeness),
    §cover_design.3 (Budget admissibility).

    Parameters
    ----------
    site_size:
        Total area of the judgment site.  Must be positive.
    patch_candidates:
        List of patch descriptor dicts.  Each dict must contain:

        * ``"patch_id"`` — unique string identifier.
        * ``"area"`` — float area of the patch (≥ 0).
        * ``"cost"`` — float cost to apply this patch (≥ 0).
        * ``"covers"`` — set or list of atomic *region IDs* covered by
          this patch.

    budget:
        Maximum total cost that may be incurred.

    Returns
    -------
    list[dict[str, Any]]
        Ordered list of selected patches (dicts from *patch_candidates*),
        in the order they were greedily selected.

    Raises
    ------
    ValueError
        If *site_size* ≤ 0 or *budget* < 0.
    """
    if site_size <= 0:
        raise ValueError(f"site_size must be positive; got {site_size!r}")
    if budget < 0:
        raise ValueError(f"budget must be non-negative; got {budget!r}")

    log.debug(
        "greedy_cover_algorithm: site_size=%.4f candidates=%d budget=%.4f",
        site_size, len(patch_candidates), budget,
    )

    remaining_budget: float = budget
    covered_regions: set[Any] = set()
    selected: list[dict[str, Any]] = []
    remaining_candidates: list[dict[str, Any]] = list(patch_candidates)

    while remaining_candidates and remaining_budget > _COVERAGE_TOLERANCE:
        best_ratio: float = -1.0
        best_idx: int = -1

        for idx, patch in enumerate(remaining_candidates):
            cost: float = float(patch.get(_COST_KEY, 0.0))
            if cost > remaining_budget:
                continue  # cannot afford this patch

            covers: set[Any] = set(patch.get("covers", []))
            new_coverage: set[Any] = covers - covered_regions
            new_area_gained: float = len(new_coverage)

            if new_area_gained <= 0:
                continue  # no marginal gain

            ratio: float = new_area_gained / max(cost, _COVERAGE_TOLERANCE)
            if ratio > best_ratio:
                best_ratio = ratio
                best_idx = idx

        if best_idx == -1:
            log.debug("greedy_cover: no affordable improving patch found — stopping")
            break

        chosen = remaining_candidates.pop(best_idx)
        covers_chosen: set[Any] = set(chosen.get("covers", []))
        covered_regions.update(covers_chosen)
        remaining_budget -= float(chosen.get(_COST_KEY, 0.0))
        selected.append(chosen)

        log.debug(
            "greedy_cover: selected %s cost=%.4f remaining_budget=%.4f covered=%d",
            chosen.get(_ID_KEY, "?"),
            float(chosen.get(_COST_KEY, 0.0)),
            remaining_budget,
            len(covered_regions),
        )

    log.debug(
        "greedy_cover: finished — selected %d patches, remaining_budget=%.4f",
        len(selected), remaining_budget,
    )
    return selected


# ---------------------------------------------------------------------------
# 2.  Topological sort (Kahn's algorithm)
# ---------------------------------------------------------------------------

def topological_sort_patches(
    dependency_dag: DependencyGraph,
) -> list[str]:
    """Produce a topological ordering of the patch DAG using Kahn's algorithm.

    Kahn's algorithm works by repeatedly removing source nodes (nodes with
    in-degree 0) from the graph and appending them to the output.  If the
    resulting order has fewer nodes than the graph, a cycle was detected.

    Complexity: :math:`O(V + E)` where :math:`V` is the number of patches
    and :math:`E` is the number of dependency edges.

    Reference: Kahn (1962), "Topological sorting of large networks".

    Theory reference: theory2.tex §cover_design.5 (Dependency ordering).

    Parameters
    ----------
    dependency_dag:
        A :class:`DependencyGraph` instance.  The graph must be a DAG;
        if it contains a cycle a ``ValueError`` is raised.

    Returns
    -------
    list[str]
        Patch IDs in a valid topological order (i.e., every predecessor
        appears before its successors).

    Raises
    ------
    ValueError
        If the graph contains a directed cycle.
    """
    in_degree: dict[str, int] = _compute_in_degrees(dependency_dag)
    queue: deque[str] = deque(
        sorted(n for n, d in in_degree.items() if d == 0)
    )
    order: list[str] = []

    while queue:
        node = queue.popleft()
        order.append(node)
        for succ in sorted(dependency_dag.successors(node)):
            in_degree[succ] -= 1
            if in_degree[succ] == 0:
                queue.append(succ)

    if len(order) != len(dependency_dag.nodes):
        cycle_nodes = dependency_dag.nodes - set(order)
        raise ValueError(
            f"Dependency DAG contains a cycle involving nodes: "
            f"{sorted(cycle_nodes)}"
        )

    log.debug("topological_sort_patches: order has %d nodes", len(order))
    return order


# ---------------------------------------------------------------------------
# 3.  Critical path computation
# ---------------------------------------------------------------------------

def compute_critical_path(
    dependency_dag: DependencyGraph,
    cost_map: dict[str, float],
) -> tuple[list[str], float]:
    r"""Compute the critical path through the dependency DAG.

    The critical path is the longest path from any source node to any sink
    node, where path length is measured by the sum of node costs along the
    path.  It gives a lower bound on the makespan of any schedule.

    Algorithm: dynamic programming in topological order.  For each node
    :math:`v` in topological order:

    .. math::

        d(v) = c(v) + \max\bigl(\{0\} \cup \{d(u) : u \to v \in E\}\bigr)

    where :math:`c(v)` is the cost of node :math:`v`.

    Complexity: :math:`O(V + E)`.

    Reference: Cormen et al., *Introduction to Algorithms* §24.2.

    Theory reference: theory2.tex §cover_design.5 (Critical path, makespan).

    Parameters
    ----------
    dependency_dag:
        A :class:`DependencyGraph` instance (must be a DAG).
    cost_map:
        Dict mapping patch IDs to their individual costs.  Missing nodes
        default to cost 1.0.

    Returns
    -------
    (path, length)
        ``path`` — list of patch IDs forming the critical path.
        ``length`` — total cost of the critical path.

    Raises
    ------
    ValueError
        If the DAG contains a cycle.
    """
    topo_order = topological_sort_patches(dependency_dag)
    dist: dict[str, float] = {}
    prev: dict[str, str | None] = {}

    for node in topo_order:
        node_cost = float(cost_map.get(node, 1.0))
        best_predecessor: str | None = None
        best_predecessor_dist: float = 0.0

        for pred in dependency_dag.predecessors(node):
            if dist.get(pred, 0.0) > best_predecessor_dist:
                best_predecessor_dist = dist[pred]
                best_predecessor = pred

        dist[node] = node_cost + best_predecessor_dist
        prev[node] = best_predecessor

    # Find the sink with the maximum distance
    if not dist:
        return [], 0.0

    sink = max(dist, key=lambda n: dist[n])
    critical_length = dist[sink]

    # Reconstruct the path by following prev pointers
    path: list[str] = []
    current: str | None = sink
    while current is not None:
        path.append(current)
        current = prev[current]
    path.reverse()

    log.debug(
        "compute_critical_path: length=%.4f path_length=%d",
        critical_length, len(path),
    )
    return path, critical_length


# ---------------------------------------------------------------------------
# 4.  Antichain decomposition
# ---------------------------------------------------------------------------

def compute_antichain_decomposition(
    partial_order: DependencyGraph,
) -> list[list[str]]:
    """Decompose a partial order into antichains (parallel generation waves).

    An *antichain* is a set of elements that are pairwise incomparable
    under the partial order.  By Dilworth's theorem, the minimum number of
    chains needed to partition the poset equals the maximum antichain size.
    This function instead greedily builds a *level decomposition*: the
    :math:`k`-th antichain consists of all elements whose longest chain
    from any minimal element has length :math:`k`.

    This level decomposition has the property that all elements in wave
    :math:`k` depend only on elements in waves :math:`0, \\ldots, k-1`,
    enabling safe parallel execution of all patches in the same wave.

    Complexity: :math:`O(V + E)`.

    Reference: Dilworth (1950), "A decomposition theorem for partially
    ordered sets".

    Theory reference: theory2.tex §cover_design.6 (Parallelism safety).

    Parameters
    ----------
    partial_order:
        A :class:`DependencyGraph` representing a partial order (must be
        a DAG).

    Returns
    -------
    list[list[str]]
        A list of antichains (generation waves) in order.  Each inner
        list contains patch IDs assigned to that wave.  The union of all
        inner lists equals the set of all nodes.

    Raises
    ------
    ValueError
        If the partial order contains a cycle.
    """
    topo_order = topological_sort_patches(partial_order)
    level: dict[str, int] = {}

    for node in topo_order:
        preds = partial_order.predecessors(node)
        if not preds:
            level[node] = 0
        else:
            level[node] = 1 + max(level.get(p, 0) for p in preds)

    if not level:
        return []

    max_level = max(level.values())
    waves: list[list[str]] = [[] for _ in range(max_level + 1)]
    for node, lv in level.items():
        waves[lv].append(node)

    # Sort within each wave for determinism
    for wave in waves:
        wave.sort()

    log.debug(
        "compute_antichain_decomposition: %d waves, max_width=%d",
        len(waves), max(len(w) for w in waves) if waves else 0,
    )
    return waves


# ---------------------------------------------------------------------------
# 5.  Čech condition check
# ---------------------------------------------------------------------------

def check_cech_condition(
    section_a: dict[str, Any],
    section_b: dict[str, Any],
    overlap_region: Any,
) -> tuple[bool, dict[str, Any]]:
    r"""Check that two sections agree on their overlap region.

    The Čech condition (also called the *gluing condition* or *cocycle
    condition*) for a cover :math:`\mathcal{U}` requires that for any two
    patches :math:`U_i, U_j \in \mathcal{U}` and any point
    :math:`x \in U_i \cap U_j`:

    .. math::

        s_i(x) = s_j(x)

    This function checks the condition at the level of data dictionaries.
    Two sections *agree on the overlap* iff, for every key that appears in
    both sections' ``"values"`` sub-dicts, the values are equal when
    restricted to keys that appear in *overlap_region*.

    Trust tier: both *section_a* and *section_b* must carry a
    ``"trust_tier"`` field.  Generated sections enter at tier
    ``"PROPOSAL"``; the check is **advisory** for ``"PROPOSAL"`` sections
    but **mandatory** for ``"VERIFIED"`` and above.

    Theory reference: theory2.tex §cover_design.2 (Čech condition soundness).

    Parameters
    ----------
    section_a:
        Dict for the first section.  Expected keys: ``"section_id"``,
        ``"patch_id"``, ``"trust_tier"``, ``"values"`` (dict of
        region-key → value).
    section_b:
        Dict for the second section.  Same structure as *section_a*.
    overlap_region:
        Either a set/list of region keys on which the sections must
        agree, or a dict whose keys define the overlap.

    Returns
    -------
    (satisfied, evidence)
        ``satisfied`` — ``True`` iff the Čech condition holds on the
        given overlap region.
        ``evidence`` — Dict with keys ``"agreement"``, ``"disagreements"``,
        ``"checked_keys"``, ``"trust_tiers"``.

    """
    vals_a: dict[str, Any] = section_a.get("values", {})
    vals_b: dict[str, Any] = section_b.get("values", {})

    if isinstance(overlap_region, dict):
        overlap_keys: set[str] = set(overlap_region.keys())
    else:
        overlap_keys = {str(k) for k in overlap_region}

    disagreements: list[dict[str, Any]] = []
    checked: list[str] = []

    for key in sorted(overlap_keys):
        if key in vals_a and key in vals_b:
            checked.append(key)
            if vals_a[key] != vals_b[key]:
                disagreements.append(
                    {
                        "key": key,
                        "value_a": vals_a[key],
                        "value_b": vals_b[key],
                    }
                )

    tier_a: str = str(section_a.get("trust_tier", "PROPOSAL"))
    tier_b: str = str(section_b.get("trust_tier", "PROPOSAL"))
    mandatory = tier_a in ("VERIFIED", "CERTIFIED") or tier_b in (
        "VERIFIED",
        "CERTIFIED",
    )

    satisfied = len(disagreements) == 0
    if not satisfied and mandatory:
        log.warning(
            "check_cech_condition: MANDATORY violation between %s and %s — "
            "%d disagreement(s)",
            section_a.get("section_id", "?"),
            section_b.get("section_id", "?"),
            len(disagreements),
        )

    evidence: dict[str, Any] = {
        "section_a_id": section_a.get("section_id", "?"),
        "section_b_id": section_b.get("section_id", "?"),
        "checked_keys": checked,
        "disagreements": disagreements,
        "agreement": satisfied,
        "trust_tiers": [tier_a, tier_b],
        "mandatory": mandatory,
    }
    return satisfied, evidence


# ---------------------------------------------------------------------------
# 6.  Overlap graph construction
# ---------------------------------------------------------------------------

def compute_overlap_graph(
    patches: list[dict[str, Any]],
) -> OverlapGraph:
    """Build the graph recording which patches overlap.

    Two patches *overlap* iff their coordinate sets (the ``"coords"``
    field, treated as a set of region IDs) have a non-empty intersection.
    The overlap area is taken as the cardinality of that intersection times
    a unit-area constant (or from ``"overlap_area"`` if pre-computed in the
    patch descriptor).

    Complexity: :math:`O(P^2 \\cdot C)` where :math:`P` is the number of
    patches and :math:`C` is the average coordinate set size.

    Theory reference: theory2.tex §cover_design.2 (Čech condition,
    overlap structure).

    Parameters
    ----------
    patches:
        List of patch descriptor dicts.  Each dict must contain:

        * ``"patch_id"`` — unique string identifier.
        * ``"coords"`` — set, list, or frozenset of region IDs covered.

    Returns
    -------
    OverlapGraph
        Populated overlap graph.
    """
    graph = OverlapGraph()
    patch_coords: list[tuple[str, frozenset[Any]]] = []

    for patch in patches:
        pid = str(patch.get(_ID_KEY, str(uuid.uuid4())))
        raw_coords = patch.get(_COORDS_KEY, [])
        coords = frozenset(raw_coords) if not isinstance(raw_coords, frozenset) else raw_coords
        if pid not in graph.patch_ids:
            graph.patch_ids.append(pid)
        patch_coords.append((pid, coords))

    for i in range(len(patch_coords)):
        pid_i, coords_i = patch_coords[i]
        for j in range(i + 1, len(patch_coords)):
            pid_j, coords_j = patch_coords[j]
            intersection = coords_i & coords_j
            if intersection:
                area = float(len(intersection))
                graph.add_overlap(pid_i, pid_j, area)

    log.debug(
        "compute_overlap_graph: %d patches, %d overlap edges",
        len(patch_coords), graph.edge_count(),
    )
    return graph


# ---------------------------------------------------------------------------
# 7.  Priority-weighted budget allocation
# ---------------------------------------------------------------------------

def priority_weighted_allocation(
    patches: list[dict[str, Any]],
    total_budget: float,
    overhead_fraction: float = _DEFAULT_OVERHEAD_FRACTION,
) -> dict[str, float]:
    """Compute per-patch budget allocations respecting priorities and overhead.

    The allocation algorithm is:

    1. Deduct overhead: :math:`B_\\text{net} = B \\cdot (1 - \\beta)`.
    2. For each patch compute a *weight*:

       .. math::

           w_i = \\frac{p_i \\cdot a_i}{\\sum_j p_j \\cdot a_j}

       where :math:`p_i` is the patch priority (default 1) and :math:`a_i`
       is the patch area (default 1).

    3. Allocate :math:`b_i = w_i \\cdot B_\\text{net}`.

    This ensures that (a) :math:`\\sum_i b_i = B_\\text{net} \\leq B` and
    (b) a patch with higher priority but equal area receives a
    proportionally larger allocation — satisfying Theorem T_CD_8.

    Theory reference: theory2.tex §cover_design.3 (Budget admissibility),
    §cover_design.7 (Quality metrics).

    Parameters
    ----------
    patches:
        List of patch descriptor dicts with optional ``"priority"`` (float,
        default 1.0) and ``"area"`` (float, default 1.0) fields.
    total_budget:
        Gross total budget :math:`B`.
    overhead_fraction:
        Fraction :math:`\\beta \\in [0, 1)` reserved for overhead.
        Default: :data:`_DEFAULT_OVERHEAD_FRACTION`.

    Returns
    -------
    dict[str, float]
        Mapping from patch ID to allocated budget amount.

    Raises
    ------
    ValueError
        If *total_budget* < 0 or *overhead_fraction* not in [0, 1).
    """
    _validate_budget(total_budget, overhead_fraction)

    net_budget = total_budget * (1.0 - overhead_fraction)
    allocations: dict[str, float] = {}

    if not patches:
        return allocations

    weights: dict[str, float] = {}
    for patch in patches:
        pid = str(patch.get(_ID_KEY, str(uuid.uuid4())))
        priority = float(patch.get(_PRIORITY_KEY, 1.0))
        area = float(patch.get(_AREA_KEY, 1.0))
        weights[pid] = max(0.0, priority) * max(0.0, area)

    total_weight = sum(weights.values())
    if total_weight <= _COVERAGE_TOLERANCE:
        # Uniform fallback
        per_patch = net_budget / len(patches)
        for patch in patches:
            pid = str(patch.get(_ID_KEY, str(uuid.uuid4())))
            allocations[pid] = per_patch
        return allocations

    for pid, w in weights.items():
        allocations[pid] = (w / total_weight) * net_budget

    log.debug(
        "priority_weighted_allocation: net_budget=%.4f, %d patches allocated",
        net_budget, len(allocations),
    )
    return allocations


# ---------------------------------------------------------------------------
# 8.  Coverage completeness
# ---------------------------------------------------------------------------

def compute_coverage_completeness(
    patches: list[dict[str, Any]],
    site_boundary: Any,
) -> float:
    """Compute the fraction of the site covered by the given patches.

    *Coverage completeness* is defined as:

    .. math::

        \\kappa = \\frac{|\\bigcup_i C_i \\cap S|}{|S|}

    where :math:`C_i` is the coordinate set of patch :math:`i` and
    :math:`S` is the site boundary.

    If *site_boundary* is a set or list of region IDs, the function
    counts covered region IDs.  If it is a positive float, it is
    interpreted as a total site area and the patches are expected to
    carry a pre-computed ``"covered_area"`` field summed over unique
    regions.

    By Theorem T_CD_7, adding more patches to a valid cover cannot
    decrease :math:`\\kappa`.

    Theory reference: theory2.tex §cover_design.1 (Cover completeness),
    §cover_design.7 (Quality metrics).

    Parameters
    ----------
    patches:
        List of patch descriptor dicts, each with a ``"coords"`` field.
    site_boundary:
        Either a set/list of all region IDs in the site, or a float
        representing the total site area.

    Returns
    -------
    float
        Coverage fraction in :math:`[0, 1]`.

    Raises
    ------
    ValueError
        If *site_boundary* is a float ≤ 0.
    """
    if isinstance(site_boundary, (int, float)):
        site_area = float(site_boundary)
        if site_area <= 0:
            raise ValueError(
                f"site_boundary area must be positive; got {site_area!r}"
            )
        covered_area = sum(
            float(p.get("covered_area", _patch_area(p))) for p in patches
        )
        return min(1.0, covered_area / site_area)

    # Set-of-region-IDs path
    site_regions: frozenset[Any] = frozenset(site_boundary)
    if not site_regions:
        return 1.0  # Empty site is trivially covered

    covered: set[Any] = set()
    for patch in patches:
        raw_coords = patch.get(_COORDS_KEY, [])
        covered.update(raw_coords)

    covered_in_site = covered & site_regions
    completeness = len(covered_in_site) / len(site_regions)
    log.debug(
        "compute_coverage_completeness: %.4f (%d/%d regions)",
        completeness, len(covered_in_site), len(site_regions),
    )
    return completeness


# ---------------------------------------------------------------------------
# 9.  Coffman–Graham scheduling
# ---------------------------------------------------------------------------

def compute_coffman_graham_order(
    dependency_dag: DependencyGraph,
    width_limit: int,
) -> ScheduleResult:
    """Compute a bounded-width schedule using the Coffman–Graham algorithm.

    The Coffman–Graham algorithm produces an optimal schedule on two
    identical processors (width 2) and a good heuristic schedule for
    larger widths.  It proceeds in two phases:

    **Phase 1 — Label assignment** (reverse topological order): assign
    integer labels to nodes using the rule that the label of node :math:`v`
    is the smallest positive integer not in the label set of any successor
    of :math:`v`, prioritising successors by their previously assigned
    labels in decreasing order.

    **Phase 2 — List scheduling**: process nodes in decreasing label order,
    assigning each node to the earliest time slot on a free processor such
    that all predecessors are complete.  With *width_limit* processors this
    produces a schedule of minimum makespan under the Coffman–Graham
    criterion.

    Reference: Coffman & Graham (1972), "Optimal scheduling for two-processor
    systems".

    Theory reference: theory2.tex §cover_design.6 (Parallelism, wave
    scheduling).

    Parameters
    ----------
    dependency_dag:
        A :class:`DependencyGraph` (must be a DAG).
    width_limit:
        Maximum number of patches per generation wave (≥ 1).

    Returns
    -------
    ScheduleResult
        Contains the wave assignment, critical-path length, and makespan.

    Raises
    ------
    ValueError
        If *width_limit* < 1 or the DAG contains a cycle.
    """
    if width_limit < 1:
        raise ValueError(f"width_limit must be ≥ 1; got {width_limit!r}")

    nodes = sorted(dependency_dag.nodes)
    if not nodes:
        return ScheduleResult(
            waves=(),
            critical_path_length=0.0,
            critical_path_nodes=(),
            makespan=0.0,
            schedule_id=str(uuid.uuid4()),
        )

    # Phase 1: label assignment in reverse topological order
    topo = topological_sort_patches(dependency_dag)
    label: dict[str, int] = {}
    label_counter: int = 1

    for node in reversed(topo):
        succ_labels = sorted(
            (label[s] for s in dependency_dag.successors(node) if s in label),
            reverse=True,
        )
        # Assign the smallest label not yet taken, using succ label ordering
        # as tie-breaker (CG heuristic: prefer nodes whose successors have
        # been labelled with large values → they are likely on the critical path)
        used_labels = set(label.values())
        candidate = label_counter
        while candidate in used_labels:
            candidate += 1
        label[node] = candidate
        label_counter = candidate + 1

    # Phase 2: list scheduling
    sorted_by_label = sorted(nodes, key=lambda n: label.get(n, 0), reverse=True)
    waves: list[list[str]] = []
    completed: set[str] = set()
    remaining: list[str] = list(sorted_by_label)

    while remaining:
        wave: list[str] = []
        still_remaining: list[str] = []
        for node in remaining:
            preds = dependency_dag.predecessors(node)
            if preds <= completed and len(wave) < width_limit:
                wave.append(node)
            else:
                still_remaining.append(node)
        if not wave:
            # Force progress: take the first node whose preds are done
            forced = [n for n in remaining if dependency_dag.predecessors(n) <= completed]
            if not forced:
                # Remaining nodes have unsatisfied predecessors — should not
                # happen in a valid DAG; break to avoid infinite loop.
                log.warning(
                    "compute_coffman_graham_order: cannot schedule %d remaining nodes",
                    len(remaining),
                )
                break
            wave = forced[:width_limit]
            still_remaining = [n for n in remaining if n not in wave]
        completed.update(wave)
        waves.append(wave)
        remaining = still_remaining

    # Critical path (unit costs)
    unit_costs = {n: 1.0 for n in dependency_dag.nodes}
    cp_nodes, cp_length = compute_critical_path(dependency_dag, unit_costs)
    makespan = float(len(waves))

    result = ScheduleResult(
        waves=tuple(tuple(w) for w in waves),
        critical_path_length=cp_length,
        critical_path_nodes=tuple(cp_nodes),
        makespan=makespan,
        schedule_id=str(uuid.uuid4()),
    )
    log.debug(
        "compute_coffman_graham_order: %d waves, makespan=%.1f, cp_length=%.1f",
        len(waves), makespan, cp_length,
    )
    return result


# ---------------------------------------------------------------------------
# 10. Patch cost estimation
# ---------------------------------------------------------------------------

def estimate_patch_cost(
    patch_descriptor: dict[str, Any],
) -> float:
    """Estimate the cost to apply a single patch.

    The cost model is:

    .. math::

        c(p) = \\alpha \\cdot A(p) \\cdot (1 + \\gamma \\cdot \\kappa(p))
               \\cdot \\delta(p)

    where:

    * :math:`A(p)` is the patch area (number of covered regions or
      ``"area"`` field).
    * :math:`\\kappa(p)` is the complexity score (``"complexity"`` field,
      default 0).
    * :math:`\\delta(p)` is a priority discount factor: patches with
      priority ≥ 2 receive a 15 % discount (priority work is
      pre-planned and thus cheaper to execute).
    * :math:`\\alpha = 1.0` is the base cost coefficient.
    * :math:`\\gamma = 0.5` is the complexity scale.

    The formula is empirically calibrated to the JuGeo cost model
    described in theory2.tex §cover_design.3.

    Theory reference: theory2.tex §cover_design.3 (Budget admissibility,
    cost model).

    Parameters
    ----------
    patch_descriptor:
        Dict with optional fields ``"area"``, ``"complexity"``,
        ``"priority"``, ``"coords"``.

    Returns
    -------
    float
        Estimated cost (≥ 0).
    """
    area = float(patch_descriptor.get(_AREA_KEY, 0.0))
    if area <= _COVERAGE_TOLERANCE:
        # Fall back to coordinate set cardinality
        coords = patch_descriptor.get(_COORDS_KEY, [])
        area = float(len(coords)) if coords else 1.0

    complexity = float(patch_descriptor.get(_COMPLEXITY_KEY, 0.0))
    priority = float(patch_descriptor.get(_PRIORITY_KEY, 1.0))

    discount = (1.0 - _COST_PRIORITY_DISCOUNT) if priority >= 2.0 else 1.0
    raw_cost = (
        _COST_PER_UNIT_AREA
        * area
        * (1.0 + _COST_COMPLEXITY_SCALE * max(0.0, complexity))
        * discount
    )
    cost = max(0.0, raw_cost)
    log.debug(
        "estimate_patch_cost: area=%.4f complexity=%.4f priority=%.1f -> cost=%.4f",
        area, complexity, priority, cost,
    )
    return cost


# ---------------------------------------------------------------------------
# Internal helper functions
# ---------------------------------------------------------------------------

def _normalize_dag(
    raw: dict[str, list[str]],
) -> DependencyGraph:
    """Convert a plain adjacency-list dict to a :class:`DependencyGraph`.

    Parameters
    ----------
    raw:
        Dict mapping node ID to list of successor IDs.

    Returns
    -------
    DependencyGraph
    """
    dag = DependencyGraph()
    for node, succs in raw.items():
        dag.add_node(node)
        for s in succs:
            dag.add_edge(node, s)
    return dag


def _compute_in_degrees(dag: DependencyGraph) -> dict[str, int]:
    """Return a dict of in-degrees for every node in *dag*.

    Parameters
    ----------
    dag:
        A :class:`DependencyGraph`.

    Returns
    -------
    dict[str, int]
        Maps each node ID to its in-degree (number of direct predecessors).
    """
    in_deg: dict[str, int] = {n: 0 for n in dag.nodes}
    for node in dag.nodes:
        for succ in dag.successors(node):
            in_deg[succ] = in_deg.get(succ, 0) + 1
    return in_deg


def _transitive_reduction(dag: DependencyGraph) -> DependencyGraph:
    """Compute the transitive reduction of *dag*.

    Removes edges that are implied by transitivity, leaving only the
    minimal DAG with the same reachability relation.

    Algorithm: for each edge ``(u, v)`` check whether there is a path of
    length ≥ 2 from ``u`` to ``v`` through another node.  If so, the edge
    is redundant and is removed.

    Parameters
    ----------
    dag:
        A :class:`DependencyGraph` (must be a DAG).

    Returns
    -------
    DependencyGraph
        A new :class:`DependencyGraph` with redundant edges removed.
    """
    reduced = DependencyGraph()
    for n in dag.nodes:
        reduced.add_node(n)

    for u in dag.nodes:
        for v in dag.successors(u):
            # Check for a path u → w → ... → v (length ≥ 2)
            reachable_via_other = False
            for w in dag.successors(u):
                if w == v:
                    continue
                # BFS/DFS from w to see if v is reachable
                visited: set[str] = set()
                stack: list[str] = [w]
                while stack:
                    curr = stack.pop()
                    if curr == v:
                        reachable_via_other = True
                        break
                    if curr in visited:
                        continue
                    visited.add(curr)
                    stack.extend(dag.successors(curr))
                if reachable_via_other:
                    break

            if not reachable_via_other:
                reduced.add_edge(u, v)

    return reduced


def _patch_area(patch: dict[str, Any]) -> float:
    """Return the area of a patch descriptor.

    Falls back to the cardinality of ``"coords"`` if ``"area"`` is absent.

    Parameters
    ----------
    patch:
        Patch descriptor dict.

    Returns
    -------
    float
    """
    area = patch.get(_AREA_KEY)
    if area is not None:
        return float(area)
    coords = patch.get(_COORDS_KEY, [])
    return float(len(coords))


def _patch_intersection_area(
    patch_a: dict[str, Any],
    patch_b: dict[str, Any],
) -> float:
    """Return the area of the intersection of two patches.

    Uses coordinate set intersection.

    Parameters
    ----------
    patch_a, patch_b:
        Patch descriptor dicts with ``"coords"`` fields.

    Returns
    -------
    float
    """
    coords_a = frozenset(patch_a.get(_COORDS_KEY, []))
    coords_b = frozenset(patch_b.get(_COORDS_KEY, []))
    return float(len(coords_a & coords_b))


def _validate_budget(total_budget: float, overhead_fraction: float) -> None:
    """Raise ``ValueError`` if budget parameters are out of range.

    Parameters
    ----------
    total_budget:
        Must be ≥ 0.
    overhead_fraction:
        Must be in [0, 1).

    Raises
    ------
    ValueError
    """
    if total_budget < 0:
        raise ValueError(
            f"total_budget must be non-negative; got {total_budget!r}"
        )
    if not (0.0 <= overhead_fraction < 1.0):
        raise ValueError(
            f"overhead_fraction must be in [0, 1); got {overhead_fraction!r}"
        )


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.DEBUG, stream=sys.stdout)

    # ── DependencyGraph ────────────────────────────────────────────────────
    dag = DependencyGraph()
    for edge in [("p0", "p1"), ("p0", "p2"), ("p1", "p3"), ("p2", "p3")]:
        dag.add_edge(*edge)
    dag.add_node("p4")  # isolated node

    print("=== DependencyGraph ===")
    print("Nodes:", sorted(dag.nodes))
    print("Has cycle:", dag.has_cycle())
    print()

    # ── Topological sort ──────────────────────────────────────────────────
    order = topological_sort_patches(dag)
    print("=== Topological sort ===")
    print("Order:", order)
    print()

    # ── Critical path ─────────────────────────────────────────────────────
    costs = {"p0": 1.0, "p1": 3.0, "p2": 2.0, "p3": 1.0, "p4": 0.5}
    cp, cp_len = compute_critical_path(dag, costs)
    print("=== Critical path ===")
    print("Path:", cp, "Length:", cp_len)
    print()

    # ── Antichain decomposition ───────────────────────────────────────────
    waves = compute_antichain_decomposition(dag)
    print("=== Antichain decomposition ===")
    for i, wave in enumerate(waves):
        print(f"  Wave {i}: {wave}")
    print()

    # ── Coffman–Graham ────────────────────────────────────────────────────
    sched = compute_coffman_graham_order(dag, width_limit=2)
    print("=== Coffman–Graham schedule (width=2) ===")
    for i, wave in enumerate(sched.waves):
        print(f"  Wave {i}: {wave}")
    print(f"  Makespan: {sched.makespan}")
    print()

    # ── Overlap graph ─────────────────────────────────────────────────────
    patches = [
        {"patch_id": "a", "coords": [1, 2, 3]},
        {"patch_id": "b", "coords": [3, 4, 5]},
        {"patch_id": "c", "coords": [6, 7]},
    ]
    og = compute_overlap_graph(patches)
    print("=== Overlap graph ===")
    print("Edges:", og.edge_count())
    print("a–b overlap:", og.get_overlap_area("a", "b"))
    print("a–c overlap:", og.get_overlap_area("a", "c"))
    print()

    # ── Čech condition ────────────────────────────────────────────────────
    sec_a = {"section_id": "s0", "patch_id": "a", "trust_tier": "PROPOSAL",
             "values": {"r3": "x", "r1": "y"}}
    sec_b = {"section_id": "s1", "patch_id": "b", "trust_tier": "PROPOSAL",
             "values": {"r3": "x", "r4": "z"}}
    ok, ev = check_cech_condition(sec_a, sec_b, {"r3"})
    print("=== Čech condition ===")
    print("Satisfied:", ok, "Evidence:", ev)
    print()

    # ── Budget allocation ─────────────────────────────────────────────────
    test_patches = [
        {"patch_id": "p0", "priority": 3.0, "area": 5.0},
        {"patch_id": "p1", "priority": 1.0, "area": 5.0},
        {"patch_id": "p2", "priority": 2.0, "area": 5.0},
    ]
    alloc = priority_weighted_allocation(test_patches, total_budget=100.0)
    print("=== Budget allocation ===")
    for pid, amt in sorted(alloc.items()):
        print(f"  {pid}: {amt:.4f}")
    total_alloc = sum(alloc.values())
    net = 100.0 * (1 - _DEFAULT_OVERHEAD_FRACTION)
    print(f"  Sum: {total_alloc:.4f}  Net budget: {net:.4f}  OK: {abs(total_alloc - net) < 1e-6}")
    print()

    # ── Coverage completeness ─────────────────────────────────────────────
    site = {1, 2, 3, 4, 5, 6, 7}
    completeness = compute_coverage_completeness(patches, site)
    print(f"=== Coverage completeness ===\n  κ = {completeness:.4f}")
    print()

    # ── Greedy cover ──────────────────────────────────────────────────────
    candidates = [
        {"patch_id": "c0", "area": 3.0, "cost": 10.0, "covers": {1, 2, 3}},
        {"patch_id": "c1", "area": 3.0, "cost": 10.0, "covers": {4, 5, 6}},
        {"patch_id": "c2", "area": 2.0, "cost": 5.0,  "covers": {3, 7}},
        {"patch_id": "c3", "area": 1.0, "cost": 20.0, "covers": {7}},
    ]
    selected = greedy_cover_algorithm(7.0, candidates, budget=25.0)
    print("=== Greedy cover ===")
    for p in selected:
        print(f"  {p['patch_id']}: cost={p['cost']}, covers={p['covers']}")
    print()

    # ── Patch cost estimate ───────────────────────────────────────────────
    desc = {"patch_id": "t0", "area": 4.0, "complexity": 1.5, "priority": 3.0}
    c = estimate_patch_cost(desc)
    print(f"=== Estimate patch cost ===\n  cost = {c:.4f}")
    print()

    print("Smoke test PASSED.")
