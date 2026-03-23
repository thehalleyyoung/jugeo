"""Path-finding algorithms for theory-space navigation.

This module implements search algorithms for navigating ``TheorySpace``
graphs as described in ``theory2.tex`` §4 (path-finding in semantic space).
Every algorithm operates over ``TheoryNode`` vertices connected by typed
edges; edge traversal costs are modulated by purpose alignment so that the
system naturally prefers routes through conceptually coherent territory.

Module layout
─────────────
┌─────────────────────────────┬───────────────────────────────────────────────┐
│ Symbol                      │ Role                                          │
├─────────────────────────────┼───────────────────────────────────────────────┤
│ _uuid4_hex                  │ Generate compact random identifiers           │
│ _path_id                    │ Build deterministic path IDs from endpoints   │
│ _jaccard                    │ Jaccard similarity between two sets           │
│ _node_ids_from_search_node  │ Trace parent chain back to root               │
│ SearchNode                  │ Single node in A* search tree (frozen dc)     │
│ PathFinder                  │ A*, BFS, DFS, strategy-dispatch search        │
│ DiversePathFinder           │ Yen-style k-shortest diverse paths            │
│ PurposeGuidedSearch         │ Beam search with purpose-alignment scoring    │
│ PathEvaluator               │ Multi-metric path quality evaluation          │
│ PathCache                   │ LRU-bounded path result cache                 │
└─────────────────────────────┴───────────────────────────────────────────────┘
"""

from __future__ import annotations

import heapq
import math
import random
import time
import uuid
from collections import deque
from dataclasses import dataclass, field, replace
from typing import Any

from jugeo.ideation.theory_navigation.models import (
    NavigationPath,
    NavigationState,
    NavigationStrategy,
    NodeMaturity,
    PurposeCondition,
    TheoryNode,
    TheorySpace,
)
from jugeo.ideation.theory_navigation.purpose_conditioning import (
    HeuristicComputer,
    PurposeAligner,
    PurposeConditioner,
    PurposeWeightMap,
)

# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _uuid4_hex() -> str:
    """Return a compact 12-character hex identifier."""
    return uuid.uuid4().hex[:12]


def _path_id(start_id: str, goal_id: str) -> str:
    """Build a deterministic path identifier from two endpoint node IDs.

    Parameters
    ----------
    start_id : str
        Source node identifier.
    goal_id : str
        Target node identifier.

    Returns
    -------
    str
        Path identifier of the form ``path-<start[:6]>-<goal[:6]>-<rand>``.
    """
    rand_suffix = _uuid4_hex()[:6]
    return f"path-{start_id[:6]}-{goal_id[:6]}-{rand_suffix}"


def _jaccard(set_a: set[str], set_b: set[str]) -> float:
    """Compute the Jaccard similarity coefficient between two string sets.

    Parameters
    ----------
    set_a : set[str]
        First set.
    set_b : set[str]
        Second set.

    Returns
    -------
    float
        Similarity in [0, 1].  Returns 0.0 when both sets are empty.
    """
    if not set_a and not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union > 0 else 0.0


def _node_ids_from_search_node(
    goal: "SearchNode",
    nodes_by_id: dict[str, "SearchNode"],
) -> tuple[str, ...]:
    """Trace parent pointers from *goal* back to the root.

    Parameters
    ----------
    goal : SearchNode
        Terminal search node whose ancestors form the path.
    nodes_by_id : dict[str, SearchNode]
        Lookup table mapping node_id → SearchNode so parent chains can be
        followed without embedding full objects in each node.

    Returns
    -------
    tuple[str, ...]
        Ordered sequence of node IDs from start to goal (inclusive).
    """
    path: list[str] = []
    current: SearchNode | None = goal
    while current is not None:
        path.append(current.node_id)
        if current.parent_id is None:
            break
        current = nodes_by_id.get(current.parent_id)
    path.reverse()
    return tuple(path)


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp *value* to the closed interval [*lo*, *hi*]."""
    return max(lo, min(hi, value))


# ---------------------------------------------------------------------------
# SearchNode – A* search-tree node
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SearchNode:
    """One node in an A* (or greedy) search tree.

    Parameters
    ----------
    node_id : str
        ID of the theory-space node this search node represents.
    parent_id : str | None
        ID of the parent search node, ``None`` for the root.
    g_cost : float
        Accumulated path cost from the start to this node.
    h_cost : float
        Heuristic estimate of the remaining cost to the goal.
    depth : int
        Number of edges from the root.
    path_so_far : tuple[str, ...]
        Ordered IDs of nodes visited so far (for cycle detection without a
        separate closed set when needed).
    """

    node_id: str
    parent_id: str | None
    g_cost: float
    h_cost: float
    depth: int
    path_so_far: tuple[str, ...]

    # ------------------------------------------------------------------
    # Derived properties
    # ------------------------------------------------------------------

    def f_cost(self) -> float:
        """Return the total estimated path cost f = g + h.

        Returns
        -------
        float
            Combined cost used by A* to order the open set.
        """
        return self.g_cost + self.h_cost

    def __lt__(self, other: "SearchNode") -> bool:
        """Compare two search nodes by f-cost for heap ordering.

        Parameters
        ----------
        other : SearchNode
            Node to compare against.

        Returns
        -------
        bool
            ``True`` when this node has strictly lower f-cost.
        """
        if not isinstance(other, SearchNode):
            return NotImplemented
        my_f = self.f_cost()
        other_f = other.f_cost()
        if my_f != other_f:
            return my_f < other_f
        # Tie-break on h_cost (prefer nodes closer to goal)
        return self.h_cost < other.h_cost

    def extend(
        self,
        child_id: str,
        edge_cost: float,
        new_h: float,
    ) -> "SearchNode":
        """Produce a child search node for moving to *child_id*.

        Parameters
        ----------
        child_id : str
            The theory-space node being entered.
        edge_cost : float
            Cost of traversing the edge from this node to *child_id*.
        new_h : float
            Heuristic estimate from *child_id* to the goal.

        Returns
        -------
        SearchNode
            New node with updated costs, depth, and path history.
        """
        return SearchNode(
            node_id=child_id,
            parent_id=self.node_id,
            g_cost=self.g_cost + edge_cost,
            h_cost=new_h,
            depth=self.depth + 1,
            path_so_far=self.path_so_far + (child_id,),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise this search node to a plain dictionary.

        Returns
        -------
        dict[str, Any]
            JSON-safe representation.
        """
        return {
            "node_id": self.node_id,
            "parent_id": self.parent_id,
            "g_cost": self.g_cost,
            "h_cost": self.h_cost,
            "f_cost": self.f_cost(),
            "depth": self.depth,
            "path_so_far": list(self.path_so_far),
        }


# ---------------------------------------------------------------------------
# PathFinder – core search algorithms
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG: dict[str, Any] = {
    "max_depth": 50,
    "beam_width": 10,
    "max_nodes_expanded": 1000,
}


class PathFinder:
    """Core collection of graph search algorithms for ``TheorySpace``.

    Supports A*, BFS, DFS, and a strategy dispatcher that selects the
    correct algorithm automatically from a ``NavigationStrategy`` enum
    value.

    Parameters
    ----------
    config : dict | None, optional
        Override default search parameters.  Recognised keys:

        * ``max_depth`` (int, default 50) – hard depth limit for all searches.
        * ``beam_width`` (int, default 10) – beam width for beam search.
        * ``max_nodes_expanded`` (int, default 1000) – expansion budget for
          A* and related exhaustive algorithms.
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self._config: dict[str, Any] = {**_DEFAULT_CONFIG, **(config or {})}
        self._default_heuristic = HeuristicComputer()

    # ------------------------------------------------------------------
    # Public search methods
    # ------------------------------------------------------------------

    def find_path_astar(
        self,
        start_id: str,
        goal_id: str,
        space: TheorySpace,
        heuristic: HeuristicComputer | None = None,
    ) -> NavigationPath:
        """Find a cost-optimal path using A* search.

        The open set is a min-heap ordered by f = g + h.  Edge costs are
        provided by *heuristic* (default: ``HeuristicComputer``).  The
        closed set prevents revisiting nodes.

        Parameters
        ----------
        start_id : str
            Source node ID.
        goal_id : str
            Target node ID.
        space : TheorySpace
            Graph to search within.
        heuristic : HeuristicComputer | None, optional
            Custom heuristic and cost provider.

        Returns
        -------
        NavigationPath
            Discovered path, or an empty path if no route exists.
        """
        if not space.has_node(start_id) or not space.has_node(goal_id):
            return self._make_empty_path(start_id, goal_id, NavigationStrategy.PURPOSE_GUIDED)

        if start_id == goal_id:
            return self._single_node_path(start_id, NavigationStrategy.PURPOSE_GUIDED)

        h_computer = heuristic or self._default_heuristic
        max_depth = self._config["max_depth"]
        max_expanded = self._config["max_nodes_expanded"]

        root = SearchNode(
            node_id=start_id,
            parent_id=None,
            g_cost=0.0,
            h_cost=h_computer.heuristic(start_id, goal_id, space),
            depth=0,
            path_so_far=(start_id,),
        )

        open_heap: list[tuple[float, SearchNode]] = [(root.f_cost(), root)]
        # closed maps node_id -> best g_cost seen
        closed: dict[str, float] = {}
        # all_nodes maps node_id -> SearchNode for parent-chain reconstruction
        all_nodes: dict[str, SearchNode] = {start_id: root}
        nodes_expanded = 0

        while open_heap:
            if nodes_expanded >= max_expanded:
                break

            _, current = heapq.heappop(open_heap)

            if current.node_id in closed:
                continue
            closed[current.node_id] = current.g_cost
            nodes_expanded += 1

            if current.node_id == goal_id:
                return self._reconstruct_path(
                    current, all_nodes, NavigationStrategy.PURPOSE_GUIDED
                )

            if current.depth >= max_depth:
                continue

            for neighbor in space.get_neighbors(current.node_id):
                nid = neighbor.node_id
                if nid in closed:
                    continue

                edge_cost = h_computer.edge_cost(current.node_id, nid, space)
                new_g = current.g_cost + edge_cost

                # Skip if we already have a better route to this neighbour
                existing_node = all_nodes.get(nid)
                if existing_node is not None and existing_node.g_cost <= new_g:
                    continue

                new_h = h_computer.heuristic(nid, goal_id, space)
                child = current.extend(nid, edge_cost, new_h)
                all_nodes[nid] = child
                heapq.heappush(open_heap, (child.f_cost(), child))

        return self._make_empty_path(start_id, goal_id, NavigationStrategy.PURPOSE_GUIDED)

    def find_path_bfs(
        self,
        start_id: str,
        goal_id: str,
        space: TheorySpace,
    ) -> NavigationPath:
        """Find a path using breadth-first search.

        BFS is complete (finds a path if one exists) and returns a path
        with the minimum number of edges.

        Parameters
        ----------
        start_id : str
            Source node ID.
        goal_id : str
            Target node ID.
        space : TheorySpace
            Graph to search.

        Returns
        -------
        NavigationPath
            Shortest-hop path, or empty path if unreachable.
        """
        if not space.has_node(start_id) or not space.has_node(goal_id):
            return self._make_empty_path(start_id, goal_id, NavigationStrategy.BREADTH_FIRST)

        if start_id == goal_id:
            return self._single_node_path(start_id, NavigationStrategy.BREADTH_FIRST)

        max_depth = self._config["max_depth"]
        max_expanded = self._config["max_nodes_expanded"]

        # Queue of (node_id, path_so_far)
        queue: deque[tuple[str, list[str]]] = deque([(start_id, [start_id])])
        visited: set[str] = {start_id}
        expanded = 0

        while queue:
            if expanded >= max_expanded:
                break

            current_id, path = queue.popleft()
            expanded += 1

            if len(path) > max_depth + 1:
                continue

            for neighbor in space.get_neighbors(current_id):
                nid = neighbor.node_id
                if nid in visited:
                    continue
                visited.add(nid)
                new_path = path + [nid]

                if nid == goal_id:
                    node_ids = tuple(new_path)
                    return NavigationPath(
                        path_id=_path_id(start_id, goal_id),
                        node_ids=node_ids,
                        start_id=start_id,
                        goal_id=goal_id,
                        purpose="",
                        total_cost=float(len(node_ids) - 1),
                        purpose_alignment=0.5,
                        strategy=NavigationStrategy.BREADTH_FIRST,
                        created_at=time.time(),
                    )

                queue.append((nid, new_path))

        return self._make_empty_path(start_id, goal_id, NavigationStrategy.BREADTH_FIRST)

    def find_path_dfs(
        self,
        start_id: str,
        goal_id: str,
        space: TheorySpace,
        max_depth: int = 20,
    ) -> NavigationPath:
        """Find a path using depth-first search with a depth limit.

        DFS uses an explicit stack to avoid Python recursion limits.  The
        depth limit prevents infinite loops in cyclic graphs.

        Parameters
        ----------
        start_id : str
            Source node ID.
        goal_id : str
            Target node ID.
        space : TheorySpace
            Graph to search.
        max_depth : int, optional
            Maximum traversal depth before backtracking.

        Returns
        -------
        NavigationPath
            A path from start to goal, or an empty path if none found.
        """
        if not space.has_node(start_id) or not space.has_node(goal_id):
            return self._make_empty_path(start_id, goal_id, NavigationStrategy.DEPTH_FIRST)

        if start_id == goal_id:
            return self._single_node_path(start_id, NavigationStrategy.DEPTH_FIRST)

        effective_max = min(max_depth, self._config["max_depth"])

        # Stack holds (node_id, path_to_here)
        stack: list[tuple[str, list[str]]] = [(start_id, [start_id])]
        visited_globally: set[str] = set()

        while stack:
            current_id, path = stack.pop()

            if current_id in visited_globally:
                continue
            visited_globally.add(current_id)

            if current_id == goal_id:
                node_ids = tuple(path)
                return NavigationPath(
                    path_id=_path_id(start_id, goal_id),
                    node_ids=node_ids,
                    start_id=start_id,
                    goal_id=goal_id,
                    purpose="",
                    total_cost=float(len(node_ids) - 1),
                    purpose_alignment=0.5,
                    strategy=NavigationStrategy.DEPTH_FIRST,
                    created_at=time.time(),
                )

            if len(path) >= effective_max + 1:
                continue

            neighbors = space.get_neighbors(current_id)
            # Push in reverse order so first neighbor is processed first
            for neighbor in reversed(neighbors):
                nid = neighbor.node_id
                if nid not in visited_globally:
                    stack.append((nid, path + [nid]))

        return self._make_empty_path(start_id, goal_id, NavigationStrategy.DEPTH_FIRST)

    def find_path_by_strategy(
        self,
        start_id: str,
        goal_id: str,
        space: TheorySpace,
        strategy: NavigationStrategy,
        heuristic: HeuristicComputer | None = None,
    ) -> NavigationPath:
        """Dispatch to the appropriate algorithm for *strategy*.

        Parameters
        ----------
        start_id : str
            Source node ID.
        goal_id : str
            Target node ID.
        space : TheorySpace
            Theory graph.
        strategy : NavigationStrategy
            Selects which algorithm to run.
        heuristic : HeuristicComputer | None, optional
            Passed through to A* / purpose-guided algorithms.

        Returns
        -------
        NavigationPath
            Path discovered by the selected algorithm.
        """
        if strategy == NavigationStrategy.BREADTH_FIRST:
            return self.find_path_bfs(start_id, goal_id, space)
        if strategy == NavigationStrategy.DEPTH_FIRST:
            return self.find_path_dfs(start_id, goal_id, space)
        if strategy == NavigationStrategy.RANDOM_WALK:
            return self._random_walk(start_id, goal_id, space)
        # Default: A* (covers PURPOSE_GUIDED, BEAM_SEARCH, bare A_STAR)
        return self.find_path_astar(start_id, goal_id, space, heuristic)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _reconstruct_path(
        self,
        goal_node: SearchNode,
        all_nodes: dict[str, SearchNode],
        strategy: NavigationStrategy,
        purpose: str = "",
    ) -> NavigationPath:
        """Trace parent pointers to build a ``NavigationPath``.

        Parameters
        ----------
        goal_node : SearchNode
            Terminal node of the search tree.
        all_nodes : dict[str, SearchNode]
            Full registry of discovered search nodes keyed by node_id.
        strategy : NavigationStrategy
            Navigation strategy tag to embed in the result.
        purpose : str, optional
            Purpose string to annotate the returned path.

        Returns
        -------
        NavigationPath
            Completed path from start to goal.
        """
        node_ids = _node_ids_from_search_node(goal_node, all_nodes)
        if not node_ids:
            return self._make_empty_path(
                goal_node.node_id, goal_node.node_id, strategy
            )
        start_id = node_ids[0]
        goal_id = node_ids[-1]
        return NavigationPath(
            path_id=_path_id(start_id, goal_id),
            node_ids=node_ids,
            start_id=start_id,
            goal_id=goal_id,
            purpose=purpose,
            total_cost=goal_node.g_cost,
            purpose_alignment=0.5,
            strategy=strategy,
            created_at=time.time(),
        )

    def _make_empty_path(
        self,
        start_id: str,
        goal_id: str,
        strategy: NavigationStrategy,
    ) -> NavigationPath:
        """Return a ``NavigationPath`` with an empty node tuple.

        Parameters
        ----------
        start_id : str
            Intended start node.
        goal_id : str
            Intended goal node.
        strategy : NavigationStrategy
            Strategy tag.

        Returns
        -------
        NavigationPath
            Path object with ``node_ids == ()``.
        """
        return NavigationPath(
            path_id=_path_id(start_id, goal_id),
            node_ids=(),
            start_id=start_id,
            goal_id=goal_id,
            purpose="",
            total_cost=math.inf,
            purpose_alignment=0.0,
            strategy=strategy,
            created_at=time.time(),
        )

    def _single_node_path(
        self,
        node_id: str,
        strategy: NavigationStrategy,
    ) -> NavigationPath:
        """Return a trivial path of length 0 containing just *node_id*."""
        return NavigationPath(
            path_id=_path_id(node_id, node_id),
            node_ids=(node_id,),
            start_id=node_id,
            goal_id=node_id,
            purpose="",
            total_cost=0.0,
            purpose_alignment=1.0,
            strategy=strategy,
            created_at=time.time(),
        )

    def _random_walk(
        self,
        start_id: str,
        goal_id: str,
        space: TheorySpace,
    ) -> NavigationPath:
        """Attempt to reach *goal_id* via random walk.

        Makes up to ``max_nodes_expanded`` random steps.  If the goal is
        reached, returns the walk path; otherwise returns empty.
        """
        if not space.has_node(start_id) or not space.has_node(goal_id):
            return self._make_empty_path(start_id, goal_id, NavigationStrategy.RANDOM_WALK)

        max_steps = self._config["max_nodes_expanded"]
        current = start_id
        path: list[str] = [current]
        visited: set[str] = {current}

        for _ in range(max_steps):
            if current == goal_id:
                node_ids = tuple(path)
                return NavigationPath(
                    path_id=_path_id(start_id, goal_id),
                    node_ids=node_ids,
                    start_id=start_id,
                    goal_id=goal_id,
                    purpose="",
                    total_cost=float(len(node_ids) - 1),
                    purpose_alignment=0.3,
                    strategy=NavigationStrategy.RANDOM_WALK,
                    created_at=time.time(),
                )
            neighbors = space.get_neighbors(current)
            unvisited = [n for n in neighbors if n.node_id not in visited]
            candidates = unvisited if unvisited else neighbors
            if not candidates:
                break
            chosen = random.choice(candidates)
            current = chosen.node_id
            path.append(current)
            visited.add(current)

        return self._make_empty_path(start_id, goal_id, NavigationStrategy.RANDOM_WALK)


# ---------------------------------------------------------------------------
# DiversePathFinder – Yen-style k diverse paths
# ---------------------------------------------------------------------------


class DiversePathFinder:
    """Discover *k* structurally diverse paths between two nodes.

    Uses an approximation of Yen's k-shortest-paths algorithm: after finding
    each path, temporarily remove its edges from the space and search again.
    A diversity filter ensures that the final result set spans different
    regions of the theory space.

    Parameters
    ----------
    base_finder : PathFinder | None, optional
        ``PathFinder`` instance to delegate single-path searches to.  A
        fresh default instance is created when ``None`` is supplied.
    """

    def __init__(self, base_finder: PathFinder | None = None) -> None:
        self._finder = base_finder or PathFinder()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def find_k_paths(
        self,
        start_id: str,
        goal_id: str,
        space: TheorySpace,
        k: int = 5,
        min_diversity: float = 0.3,
    ) -> list[NavigationPath]:
        """Return up to *k* diverse paths from *start_id* to *goal_id*.

        Uses Yen-style edge removal: find the first path, strip its edges,
        search again, repeat.  Then filter for diversity.

        Parameters
        ----------
        start_id : str
            Source node.
        goal_id : str
            Target node.
        space : TheorySpace
            Search graph.
        k : int, optional
            Maximum number of paths to return.
        min_diversity : float, optional
            Minimum Jaccard dissimilarity between any two selected paths.

        Returns
        -------
        list[NavigationPath]
            Up to *k* diverse paths, sorted by quality (best first).
        """
        candidates: list[NavigationPath] = []
        working_space = space

        # Collect up to 2*k candidate paths via iterative edge removal
        for _ in range(k * 2):
            path = self._finder.find_path_astar(start_id, goal_id, working_space)
            if path.is_empty():
                # Try BFS as fallback
                path = self._finder.find_path_bfs(start_id, goal_id, working_space)
            if path.is_empty():
                break
            # Avoid exact duplicates
            if not any(p.node_ids == path.node_ids for p in candidates):
                candidates.append(path)
            # Remove this path's edges for next iteration
            working_space = self._remove_path_edges(working_space, path)

        if not candidates:
            return []

        diverse = self.filter_diverse(candidates, min_diversity)
        return self.rank_by_quality(diverse)[:k]

    def path_diversity(self, a: NavigationPath, b: NavigationPath) -> float:
        """Compute the Jaccard dissimilarity between two path node sets.

        Parameters
        ----------
        a : NavigationPath
            First path.
        b : NavigationPath
            Second path.

        Returns
        -------
        float
            Value in [0, 1]; 1.0 means completely disjoint, 0.0 identical.
        """
        set_a = set(a.node_ids)
        set_b = set(b.node_ids)
        return 1.0 - _jaccard(set_a, set_b)

    def filter_diverse(
        self,
        paths: list[NavigationPath],
        min_diversity: float = 0.3,
    ) -> list[NavigationPath]:
        """Greedily select paths that are mutually diverse.

        Starts with the highest-quality path and adds subsequent paths only
        when they are at least *min_diversity* dissimilar from every already-
        selected path.

        Parameters
        ----------
        paths : list[NavigationPath]
            Candidate path pool (will not be mutated).
        min_diversity : float, optional
            Minimum pairwise Jaccard dissimilarity.

        Returns
        -------
        list[NavigationPath]
            Filtered list preserving original ordering among selected items.
        """
        if not paths:
            return []

        ranked = self.rank_by_quality(paths)
        selected: list[NavigationPath] = [ranked[0]]

        for candidate in ranked[1:]:
            diverse_enough = all(
                self.path_diversity(candidate, chosen) >= min_diversity
                for chosen in selected
            )
            if diverse_enough:
                selected.append(candidate)

        return selected

    def rank_by_quality(
        self,
        paths: list[NavigationPath],
    ) -> list[NavigationPath]:
        """Sort *paths* by their ``quality_score()`` in descending order.

        Parameters
        ----------
        paths : list[NavigationPath]
            Paths to sort.

        Returns
        -------
        list[NavigationPath]
            New list sorted best-first.
        """
        return sorted(paths, key=lambda p: p.quality_score(), reverse=True)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _remove_path_edges(
        self,
        space: TheorySpace,
        path: NavigationPath,
    ) -> TheorySpace:
        """Return a copy of *space* with the edges used by *path* removed.

        Only edges that are in *path* (consecutive node pairs) are removed.
        The node set is untouched.

        Parameters
        ----------
        space : TheorySpace
            Original space.
        path : NavigationPath
            Path whose edges should be stripped.

        Returns
        -------
        TheorySpace
            Mutated copy (``TheorySpace`` is not frozen, so we operate on
            a lightweight clone built by ``from_dict / to_dict``).
        """
        clone = TheorySpace.from_dict(space.to_dict())
        ids = path.node_ids
        for i in range(len(ids) - 1):
            try:
                clone.remove_edge(ids[i], ids[i + 1])
            except (KeyError, ValueError):
                pass
        return clone


# ---------------------------------------------------------------------------
# PurposeGuidedSearch – beam search with purpose alignment
# ---------------------------------------------------------------------------


class PurposeGuidedSearch:
    """Beam search over ``TheorySpace`` guided by ``PurposeCondition`` scoring.

    At each iteration the beam holds the *beam_width* most promising node
    IDs based on a combined score of purpose alignment and distance to goal.
    The search terminates when the goal enters the beam or the beam empties.

    Parameters
    ----------
    condition : PurposeCondition | None, optional
        Scoring condition.  If ``None`` a neutral scorer is used.
    """

    def __init__(self, condition: PurposeCondition | None = None) -> None:
        self._condition: PurposeCondition | None = condition
        self._conditioner = PurposeConditioner(condition) if condition else None
        self._heuristic = HeuristicComputer()

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def set_condition(self, condition: PurposeCondition) -> None:
        """Replace the active purpose condition.

        Parameters
        ----------
        condition : PurposeCondition
            New scoring condition.
        """
        self._condition = condition
        self._conditioner = PurposeConditioner(condition)

    # ------------------------------------------------------------------
    # Core beam search
    # ------------------------------------------------------------------

    def guided_search(
        self,
        start_id: str,
        goal_id: str,
        space: TheorySpace,
        beam_width: int = 5,
    ) -> NavigationPath:
        """Beam search from *start_id* to *goal_id* guided by purpose scores.

        The beam is a sorted list of (score, node_id, path_so_far) triples.
        At each step every beam element is expanded; the resulting successors
        are merged, de-duplicated, and trimmed to *beam_width*.

        Parameters
        ----------
        start_id : str
            Source node.
        goal_id : str
            Target node.
        space : TheorySpace
            Theory graph.
        beam_width : int, optional
            Number of candidates to keep in the beam at each step.

        Returns
        -------
        NavigationPath
            Best path found, or empty path if search fails.
        """
        if not space.has_node(start_id) or not space.has_node(goal_id):
            return self._empty(start_id, goal_id)

        if start_id == goal_id:
            return NavigationPath(
                path_id=_path_id(start_id, goal_id),
                node_ids=(start_id,),
                start_id=start_id,
                goal_id=goal_id,
                purpose="",
                total_cost=0.0,
                purpose_alignment=1.0,
                strategy=NavigationStrategy.BEAM_SEARCH,
                created_at=time.time(),
            )

        max_depth = 60
        # Beam entry: (score, node_id, path_so_far_as_list)
        initial_score = self._score_state(start_id, goal_id, space, 0)
        beam: list[tuple[float, str, list[str]]] = [
            (initial_score, start_id, [start_id])
        ]
        visited_global: set[str] = {start_id}
        best_path: NavigationPath | None = None
        best_score = math.inf

        for depth in range(max_depth):
            if not beam:
                break

            next_candidates: list[tuple[float, str, list[str]]] = []

            for score, node_id, path in beam:
                neighbors = space.get_neighbors(node_id)
                for neighbor in neighbors:
                    nid = neighbor.node_id
                    if nid in visited_global:
                        continue
                    new_path = path + [nid]
                    ns = self._score_state(nid, goal_id, space, depth + 1)

                    if nid == goal_id:
                        candidate = self._make_path(
                            tuple(new_path), start_id, goal_id, ns
                        )
                        if ns < best_score:
                            best_score = ns
                            best_path = candidate

                    next_candidates.append((ns, nid, new_path))

            if best_path is not None and depth >= 1:
                # Return as soon as goal found at any depth
                break

            # Trim and sort beam
            next_candidates.sort(key=lambda x: x[0])
            beam = next_candidates[:beam_width]
            for _, nid, _ in beam:
                visited_global.add(nid)

        if best_path is not None:
            return best_path

        # Fallback: return best partial path from beam
        if beam:
            _, nid, path = beam[0]
            node_ids = tuple(path)
            return NavigationPath(
                path_id=_path_id(start_id, goal_id),
                node_ids=node_ids,
                start_id=start_id,
                goal_id=goal_id,
                purpose="",
                total_cost=float(len(node_ids)),
                purpose_alignment=0.2,
                strategy=NavigationStrategy.BEAM_SEARCH,
                created_at=time.time(),
            )

        return self._empty(start_id, goal_id)

    def search_with_restarts(
        self,
        start_id: str,
        goal_id: str,
        space: TheorySpace,
        max_restarts: int = 3,
    ) -> NavigationPath:
        """Run ``guided_search`` with random beam-width perturbations.

        Each restart uses a slightly different beam width.  The best path
        across all runs (by quality_score) is returned.

        Parameters
        ----------
        start_id : str
            Source node.
        goal_id : str
            Target node.
        space : TheorySpace
            Theory graph.
        max_restarts : int, optional
            Number of times to repeat the search.

        Returns
        -------
        NavigationPath
            Best path found across all restarts.
        """
        base_bw = 5
        results: list[NavigationPath] = []
        for i in range(max(1, max_restarts)):
            perturb = random.randint(-2, 3)
            bw = max(2, base_bw + perturb + i)
            path = self.guided_search(start_id, goal_id, space, beam_width=bw)
            if not path.is_empty():
                results.append(path)

        if not results:
            return self._empty(start_id, goal_id)

        return max(results, key=lambda p: p.quality_score())

    def explore_from(
        self,
        start_id: str,
        space: TheorySpace,
        max_depth: int = 5,
    ) -> list[TheoryNode]:
        """BFS/purpose-guided exploration from *start_id*.

        Visits up to ``max_depth`` hops from the start, collects all
        encountered nodes, and returns them sorted by descending purpose
        alignment score.

        Parameters
        ----------
        start_id : str
            Origin node.
        space : TheorySpace
            Theory graph.
        max_depth : int, optional
            Depth limit.

        Returns
        -------
        list[TheoryNode]
            Nodes encountered, sorted by purpose alignment descending.
        """
        if not space.has_node(start_id):
            return []

        visited: dict[str, TheoryNode] = {}
        queue: deque[tuple[str, int]] = deque([(start_id, 0)])
        seen: set[str] = {start_id}

        while queue:
            node_id, depth = queue.popleft()
            node = space.get_node(node_id)
            if node is not None:
                visited[node_id] = node

            if depth >= max_depth:
                continue

            for neighbor in space.get_neighbors(node_id):
                nid = neighbor.node_id
                if nid not in seen:
                    seen.add(nid)
                    # Prioritise higher-purpose nodes in BFS by skipping
                    # very low-alignment neighbours early
                    if self._condition is not None:
                        alignment = neighbor.purpose_alignment
                        if alignment < 0.1 and depth >= max_depth - 1:
                            continue
                    queue.append((nid, depth + 1))

        nodes = list(visited.values())
        nodes.sort(key=lambda n: n.purpose_alignment, reverse=True)
        return nodes

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _score_state(
        self,
        node_id: str,
        goal_id: str,
        space: TheorySpace,
        depth: int,
    ) -> float:
        """Lower is better.  Combines purpose alignment with distance estimate.

        Parameters
        ----------
        node_id : str
            Current node being scored.
        goal_id : str
            Target node.
        space : TheorySpace
            Theory graph.
        depth : int
            Current search depth.

        Returns
        -------
        float
            Combined score (lower → higher priority in beam).
        """
        node = space.get_node(node_id)
        purpose_score: float = 0.5
        if node is not None:
            purpose_score = node.purpose_alignment
            if self._condition is not None:
                text = f"{node.name} {node.description}"
                condition_score = self._condition.score_text(text)
                purpose_score = (purpose_score + condition_score) / 2.0

        # Negate alignment so higher alignment → lower score
        alignment_penalty = 1.0 - purpose_score
        # Heuristic distance estimate
        distance_estimate = self._heuristic.heuristic(node_id, goal_id, space)
        # Depth penalty (slight encouragement to stay shallow)
        depth_penalty = depth * 0.02

        return alignment_penalty + distance_estimate * 0.5 + depth_penalty

    def _make_path(
        self,
        node_ids: tuple[str, ...],
        start_id: str,
        goal_id: str,
        score: float,
    ) -> NavigationPath:
        alignment = _clamp(1.0 - score)
        return NavigationPath(
            path_id=_path_id(start_id, goal_id),
            node_ids=node_ids,
            start_id=start_id,
            goal_id=goal_id,
            purpose="",
            total_cost=float(len(node_ids) - 1),
            purpose_alignment=alignment,
            strategy=NavigationStrategy.BEAM_SEARCH,
            created_at=time.time(),
        )

    def _empty(self, start_id: str, goal_id: str) -> NavigationPath:
        return NavigationPath(
            path_id=_path_id(start_id, goal_id),
            node_ids=(),
            start_id=start_id,
            goal_id=goal_id,
            purpose="",
            total_cost=math.inf,
            purpose_alignment=0.0,
            strategy=NavigationStrategy.BEAM_SEARCH,
            created_at=time.time(),
        )


# ---------------------------------------------------------------------------
# PathEvaluator – multi-metric path quality assessment
# ---------------------------------------------------------------------------


class PathEvaluator:
    """Compute multi-metric quality scores for navigation paths.

    Metrics
    -------
    quality
        Internal ``quality_score()`` from the path model.
    coherence
        Average purpose alignment of nodes along the path.
    length_penalty
        Penalty for overly long paths (longer → higher penalty).
    alignment
        Mean alignment with the active ``PurposeCondition``.
    coverage
        Fraction of path nodes that are MATURE or ESTABLISHED.
    final_score
        Weighted combination of the above metrics.

    Parameters
    ----------
    condition : PurposeCondition | None, optional
        Scoring condition used for alignment metric.
    """

    def __init__(self, condition: PurposeCondition | None = None) -> None:
        self._condition = condition

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def evaluate(
        self,
        path: NavigationPath,
        space: TheorySpace,
    ) -> dict[str, float]:
        """Evaluate *path* on all metrics.

        Parameters
        ----------
        path : NavigationPath
            Path to evaluate.
        space : TheorySpace
            Graph containing the path's nodes.

        Returns
        -------
        dict[str, float]
            Keys: quality, coherence, length_penalty, alignment, coverage,
            final_score.
        """
        if path.is_empty():
            return {
                "quality": 0.0,
                "coherence": 0.0,
                "length_penalty": 1.0,
                "alignment": 0.0,
                "coverage": 0.0,
                "final_score": 0.0,
            }

        quality = _clamp(path.quality_score())
        coherence = self._compute_coherence(path, space)
        length_penalty = self._length_penalty(path)
        alignment = self._compute_alignment(path, space)
        coverage = self.score_coverage(path, space)

        # Weighted combination
        final = (
            0.30 * quality
            + 0.25 * coherence
            + 0.15 * alignment
            + 0.20 * coverage
            + 0.10 * (1.0 - length_penalty)
        )

        return {
            "quality": quality,
            "coherence": coherence,
            "length_penalty": length_penalty,
            "alignment": alignment,
            "coverage": coverage,
            "final_score": _clamp(final),
        }

    def score_coverage(
        self,
        path: NavigationPath,
        space: TheorySpace,
    ) -> float:
        """Compute the fraction of path nodes that are MATURE or ESTABLISHED.

        Parameters
        ----------
        path : NavigationPath
            Path to assess.
        space : TheorySpace
            Provides node maturity data.

        Returns
        -------
        float
            Fraction in [0, 1].
        """
        if not path.node_ids:
            return 0.0

        mature_count = 0
        total = 0
        for nid in path.node_ids:
            node = space.get_node(nid)
            if node is None:
                continue
            total += 1
            if node.maturity in (NodeMaturity.MATURE, NodeMaturity.ESTABLISHED):
                mature_count += 1

        return mature_count / total if total > 0 else 0.0

    def compare_paths(
        self,
        paths: list[NavigationPath],
        space: TheorySpace,
    ) -> list[tuple[NavigationPath, dict[str, float]]]:
        """Evaluate all paths and return them sorted by final_score.

        Parameters
        ----------
        paths : list[NavigationPath]
            Paths to compare.
        space : TheorySpace
            Theory graph.

        Returns
        -------
        list[tuple[NavigationPath, dict[str, float]]]
            Pairs of (path, metrics_dict) sorted by final_score descending.
        """
        evaluated = [(p, self.evaluate(p, space)) for p in paths]
        evaluated.sort(key=lambda x: x[1]["final_score"], reverse=True)
        return evaluated

    def best_path(
        self,
        paths: list[NavigationPath],
        space: TheorySpace,
    ) -> NavigationPath | None:
        """Return the path with the highest ``final_score``.

        Parameters
        ----------
        paths : list[NavigationPath]
            Candidate paths.
        space : TheorySpace
            Theory graph.

        Returns
        -------
        NavigationPath | None
            Best path or ``None`` if *paths* is empty.
        """
        if not paths:
            return None
        ranked = self.compare_paths(paths, space)
        return ranked[0][0]

    def evaluation_report(
        self,
        path: NavigationPath,
        space: TheorySpace,
    ) -> str:
        """Produce a multi-line human-readable evaluation report.

        Parameters
        ----------
        path : NavigationPath
            Path to report on.
        space : TheorySpace
            Theory graph.

        Returns
        -------
        str
            Formatted multi-line report.
        """
        metrics = self.evaluate(path, space)
        lines: list[str] = [
            "═" * 60,
            f"  Path Evaluation Report",
            "═" * 60,
            f"  Path ID      : {path.path_id}",
            f"  Start → Goal : {path.start_id} → {path.goal_id}",
            f"  Length       : {path.length()} nodes",
            f"  Strategy     : {path.strategy.value}",
            f"  Total Cost   : {path.total_cost:.4f}",
            "─" * 60,
            f"  Metrics:",
            f"    Quality Score   : {metrics['quality']:.4f}",
            f"    Coherence       : {metrics['coherence']:.4f}",
            f"    Length Penalty  : {metrics['length_penalty']:.4f}",
            f"    Alignment       : {metrics['alignment']:.4f}",
            f"    Coverage        : {metrics['coverage']:.4f}",
            "─" * 60,
            f"  ► Final Score     : {metrics['final_score']:.4f}",
            "═" * 60,
        ]
        node_section: list[str] = ["  Node trace:"]
        for i, nid in enumerate(path.node_ids):
            node = space.get_node(nid)
            if node is not None:
                label = f"    [{i:>2}] {nid[:10]:10s} | {node.name[:30]:30s} | {node.maturity.value:12s} | align={node.purpose_alignment:.3f}"
            else:
                label = f"    [{i:>2}] {nid[:10]:10s} | <not in space>"
            node_section.append(label)
        lines.extend(node_section)
        lines.append("═" * 60)
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _compute_coherence(
        self,
        path: NavigationPath,
        space: TheorySpace,
    ) -> float:
        """Mean purpose_alignment across all nodes in path."""
        total = 0.0
        count = 0
        for nid in path.node_ids:
            node = space.get_node(nid)
            if node is not None:
                total += node.purpose_alignment
                count += 1
        return total / count if count > 0 else 0.0

    def _length_penalty(self, path: NavigationPath) -> float:
        """Penalty that increases with path length.  Normalised to [0, 1]."""
        length = path.length()
        if length <= 1:
            return 0.0
        # Sigmoid-like penalty: ~0.1 at 5 nodes, ~0.5 at 15, ~0.9 at 30
        return _clamp(1.0 - math.exp(-length / 20.0))

    def _compute_alignment(
        self,
        path: NavigationPath,
        space: TheorySpace,
    ) -> float:
        """Compute mean condition alignment across path nodes."""
        if self._condition is None:
            return path.purpose_alignment

        total = 0.0
        count = 0
        for nid in path.node_ids:
            node = space.get_node(nid)
            if node is not None:
                text = f"{node.name} {node.description}"
                total += self._condition.score_text(text)
                count += 1

        return total / count if count > 0 else 0.0


# ---------------------------------------------------------------------------
# PathCache – bounded path result cache
# ---------------------------------------------------------------------------


class PathCache:
    """LRU-bounded cache for navigation path results.

    Avoids redundant searches for recently computed (start, goal, purpose)
    triples.  Tracks hit/miss statistics for monitoring.

    Parameters
    ----------
    max_size : int, optional
        Maximum number of cached entries before eviction.
    """

    def __init__(self, max_size: int = 200) -> None:
        self._max_size = max(1, max_size)
        self._store: dict[str, NavigationPath] = {}
        # Access-order tracking: most recent at end
        self._order: list[str] = []
        self._hits: int = 0
        self._misses: int = 0

    # ------------------------------------------------------------------
    # Key generation
    # ------------------------------------------------------------------

    def _key(
        self,
        start_id: str,
        goal_id: str,
        purpose: str = "",
    ) -> str:
        """Build a deterministic cache key from the query parameters.

        Parameters
        ----------
        start_id : str
            Source node ID.
        goal_id : str
            Target node ID.
        purpose : str, optional
            Purpose context string (empty = any purpose).

        Returns
        -------
        str
            Cache key string.
        """
        norm_purpose = purpose.strip().lower()
        return f"{start_id}|{goal_id}|{norm_purpose}"

    # ------------------------------------------------------------------
    # Cache operations
    # ------------------------------------------------------------------

    def get(
        self,
        start_id: str,
        goal_id: str,
        purpose: str = "",
    ) -> NavigationPath | None:
        """Retrieve a cached path for the given query.

        Parameters
        ----------
        start_id : str
            Source node ID.
        goal_id : str
            Target node ID.
        purpose : str, optional
            Purpose context.

        Returns
        -------
        NavigationPath | None
            Cached path or ``None`` on cache miss.
        """
        key = self._key(start_id, goal_id, purpose)
        if key in self._store:
            self._hits += 1
            # Move to end (most recently used)
            if key in self._order:
                self._order.remove(key)
            self._order.append(key)
            return self._store[key]
        self._misses += 1
        return None

    def put(self, path: NavigationPath) -> None:
        """Store a path in the cache, evicting least-recently-used if full.

        Parameters
        ----------
        path : NavigationPath
            Path to cache (key is derived from start_id, goal_id, purpose).
        """
        key = self._key(path.start_id, path.goal_id, path.purpose)

        if key in self._store:
            # Update existing entry
            self._order.remove(key)
        elif len(self._store) >= self._max_size:
            # Evict LRU entry
            lru_key = self._order.pop(0)
            del self._store[lru_key]

        self._store[key] = path
        self._order.append(key)

    def invalidate(self, node_id: str) -> None:
        """Remove all cached paths that involve *node_id*.

        Parameters
        ----------
        node_id : str
            Node whose paths should be invalidated.
        """
        keys_to_remove = [
            k for k in list(self._store.keys())
            if node_id in k
        ]
        for k in keys_to_remove:
            del self._store[k]
            if k in self._order:
                self._order.remove(k)

    def clear(self) -> None:
        """Remove all cached entries and reset statistics."""
        self._store.clear()
        self._order.clear()
        self._hits = 0
        self._misses = 0

    def size(self) -> int:
        """Return the current number of cached entries.

        Returns
        -------
        int
            Cache population count.
        """
        return len(self._store)

    def hit_rate(self) -> float:
        """Return the cache hit rate as a fraction in [0, 1].

        Returns
        -------
        float
            ``hits / (hits + misses)``.  Returns 0.0 when no lookups have
            been performed.
        """
        total = self._hits + self._misses
        return self._hits / total if total > 0 else 0.0

    def stats(self) -> dict[str, Any]:
        """Return a summary of cache statistics.

        Returns
        -------
        dict[str, Any]
            Keys: size, max_size, hits, misses, hit_rate.
        """
        return {
            "size": self.size(),
            "max_size": self._max_size,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": self.hit_rate(),
        }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "SearchNode",
    "PathFinder",
    "DiversePathFinder",
    "PurposeGuidedSearch",
    "PathEvaluator",
    "PathCache",
]
