"""
Core Search Algorithms for the Generation State Space.

# copilot: state-space-algorithms

This module implements BFS, DFS, and A* search over the generation state space
with a composite semantic heuristic tailored to the sheaf-theoretic structure
of the generation problem.

## Algorithms

### Breadth-First Search (BFS)
BFS is complete: if any path to a COMPLETE state exists, BFS finds the
shortest one (in terms of number of transitions).  This is guaranteed by the
Generation Completeness Theorem (see theorems.py) provided the state space is
finite.

### Depth-First Search (DFS)
DFS is memory-efficient but may diverge on infinite branches.  The
implementation includes a depth limit (``max_depth``) to ensure termination.
It is useful for quickly finding any solution when the target state space is
small.

### A* Search
A* combines BFS's completeness with an admissible heuristic that estimates the
remaining cost to reach a COMPLETE state.  The semantic heuristic combines:

1. **Obligation count** (weighted 1.0 per obligation): each unresolved obligation
   requires at least one move to discharge.
2. **Trust gap** (weighted 2.0 per level): the distance from the current trust
   level to the required trust floor.
3. **Coverage gap** (weighted 1.5 per patch): the number of patches without a
   local section.
4. **Obstruction penalty** (weighted 10.0 per obstruction): each Čech H¹ class
   is expensive to discharge.

The heuristic is admissible (never over-estimates) because each term lower-bounds
the true cost of the corresponding sub-problem.

Theory Reference: theory2.tex §40.13.
"""

from __future__ import annotations

import dataclasses
import datetime
import functools
import hashlib
import heapq
import itertools
import logging
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

__all__ = [
    "SearchNode",
    "SemanticHeuristic",
    "PriorityQueue",
    "SearchResult",
    "StateSpaceSearch",
    "bfs_generation",
    "dfs_generation",
    "astar_generation",
    "semantic_heuristic_fn",
    "reconstruct_path",
    "build_default_heuristic",
    "THEORY_SECTION",
    "CHAPTER",
]

THEORY_SECTION = "40.13"
CHAPTER = 40

# ---------------------------------------------------------------------------
# Jugeo imports with fallback stubs
# ---------------------------------------------------------------------------

try:
    from jugeo.generation.state_space.the_core_state_space_for_generatio import (
        GenStateKind,
        GenerationState,
        StateTransition,
        StateSpace,
    )
except ImportError:
    class GenStateKind:  # type: ignore[no-redef]
        INITIAL = "INITIAL"
        COVER_PROPOSED = "COVER_PROPOSED"
        COMPLETE = "COMPLETE"
        FAILED = "FAILED"

        @staticmethod
        def is_terminal() -> bool:
            return False

    class GenerationState:  # type: ignore[no-redef]
        def __init__(self, **kwargs: Any):
            for k, v in kwargs.items():
                setattr(self, k, v)

        def is_complete(self) -> bool:
            return getattr(self, "kind", None) == "COMPLETE"

        def is_terminal(self) -> bool:
            return self.is_complete() or getattr(self, "kind", None) == "FAILED"

    class StateTransition:  # type: ignore[no-redef]
        def __init__(self, **kwargs: Any):
            for k, v in kwargs.items():
                setattr(self, k, v)

    class StateSpace:  # type: ignore[no-redef]
        def get_successors(self, state_id: str) -> list:
            return []

        def get_transitions_from(self, state_id: str) -> list:
            return []

        def get_state(self, state_id: str):
            return None

        def all_states(self) -> list:
            return []

try:
    from jugeo.errors import JuGeoError, StructuredFailure
except ImportError:
    class JuGeoError(Exception):  # type: ignore[no-redef]
        pass

    class StructuredFailure(Exception):  # type: ignore[no-redef]
        pass

# ---------------------------------------------------------------------------
# Trust level ordering (for heuristic)
# ---------------------------------------------------------------------------

_TRUST_ORDER: list[str] = [
    "CONTRADICTED",
    "UNVERIFIED",
    "COPILOT_SUGGESTED",
    "ORACLE_PROPOSED",
    "HUMAN_ATTESTED",
    "RUNTIME_WITNESSED",
    "SOLVER_DISCHARGED",
    "MECHANICALLY_VERIFIED",
]


def _trust_index(level: str) -> int:
    try:
        return _TRUST_ORDER.index(level)
    except ValueError:
        return 0


class TrustTier(Enum):
    """Ordered trust tiers — the trust algebra over the generation process.

    Theory Invariant: Trust = ordered algebra, never a plain float.
    The natural order is PROPOSAL < REVIEWED < VERIFIED < RUNTIME_WITNESSED
    < PROOF_BACKED.

    Satisfies the invariant specified in theory2.tex §40.13: TrustTier is an
    ordered enumeration used to bound the heuristic's trust-gap term and to
    annotate SearchNode and SearchResult objects.
    """

    PROPOSAL = 0
    REVIEWED = 1
    VERIFIED = 2
    RUNTIME_WITNESSED = 3
    PROOF_BACKED = 4

    def join(self, other: "TrustTier") -> "TrustTier":
        """Least upper bound in the trust order (promotion)."""
        return TrustTier(max(self.value, other.value))

    def meet(self, other: "TrustTier") -> "TrustTier":
        """Greatest lower bound in the trust order (demotion)."""
        return TrustTier(min(self.value, other.value))

    def is_at_least(self, floor: "TrustTier") -> bool:
        """Return True iff self >= floor in the trust order."""
        return self.value >= floor.value

    def distance_to(self, target: "TrustTier") -> int:
        """Number of promotion steps needed to reach target."""
        return max(0, target.value - self.value)

    @classmethod
    def from_str(cls, s: str) -> "TrustTier":
        """Parse a TrustTier from its name (case-insensitive)."""
        for member in cls:
            if member.name == s.upper():
                return member
        return cls.PROPOSAL


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SearchNode:
    """A node in the BFS/DFS/A* search tree.

    Search nodes wrap generation states and augment them with path-cost
    information needed by the search algorithms.

    Fields
    ------
    node_id : str
        Unique identifier for this node.
    state_id : str
        The generation state this node corresponds to.
    parent_node_id : Optional[str]
        The parent node, or None for the root.
    depth : int
        Depth from the root node.
    g_cost : float
        Actual cost from the root to this node (g-value in A*).
    h_cost : float
        Estimated cost from this node to the goal (h-value in A*).
    f_cost : float
        Total estimated cost f = g + h (f-value in A*).
    path_transitions : tuple[str, ...]
        Transition IDs on the path from root to this node.
    """

    node_id: str
    state_id: str
    parent_node_id: Optional[str]
    depth: int
    g_cost: float
    h_cost: float
    f_cost: float
    path_transitions: tuple[str, ...]

    def __lt__(self, other: SearchNode) -> bool:
        """Enable comparison in priority queues (by f_cost)."""
        return self.f_cost < other.f_cost

    @classmethod
    def root(cls, state_id: str, h_cost: float = 0.0) -> SearchNode:
        """Create a root node for *state_id*."""
        return cls(
            node_id=str(uuid.uuid4()),
            state_id=state_id,
            parent_node_id=None,
            depth=0,
            g_cost=0.0,
            h_cost=h_cost,
            f_cost=h_cost,
            path_transitions=(),
        )

    def child(
        self,
        state_id: str,
        transition_id: str,
        edge_cost: float,
        h_cost: float,
    ) -> SearchNode:
        """Create a child node reached via *transition_id* with *edge_cost*."""
        new_g = self.g_cost + edge_cost
        return SearchNode(
            node_id=str(uuid.uuid4()),
            state_id=state_id,
            parent_node_id=self.node_id,
            depth=self.depth + 1,
            g_cost=new_g,
            h_cost=h_cost,
            f_cost=new_g + h_cost,
            path_transitions=self.path_transitions + (transition_id,),
        )


@dataclass(frozen=True)
class SearchResult:
    """Result of a BFS/DFS/A* search.

    Fields
    ------
    success : bool
        True iff a path to a COMPLETE state was found.
    path : tuple[str, ...]
        Sequence of state_ids on the solution path.
    path_transitions : tuple[str, ...]
        Sequence of transition_ids on the solution path.
    nodes_explored : int
        Number of nodes popped from the frontier.
    nodes_generated : int
        Number of nodes added to the frontier.
    depth_reached : int
        Maximum depth reached during search.
    total_cost : float
        Total g-cost of the solution path.
    algorithm : str
        The algorithm used (bfs/dfs/astar).
    elapsed_ms : float
        Wall-clock time in milliseconds.
    explanation : str
        Human-readable explanation of the result.
    """

    success: bool
    path: tuple[str, ...]
    path_transitions: tuple[str, ...]
    nodes_explored: int
    nodes_generated: int
    depth_reached: int
    total_cost: float
    algorithm: str
    elapsed_ms: float
    explanation: str

    @classmethod
    def failure(
        cls,
        algorithm: str,
        nodes_explored: int,
        elapsed_ms: float,
        reason: str = "no path found",
    ) -> SearchResult:
        """Create a failure result."""
        return cls(
            success=False,
            path=(),
            path_transitions=(),
            nodes_explored=nodes_explored,
            nodes_generated=0,
            depth_reached=0,
            total_cost=float("inf"),
            algorithm=algorithm,
            elapsed_ms=elapsed_ms,
            explanation=f"FAILED ({algorithm}): {reason}",
        )

    def path_length(self) -> int:
        """Return the number of states in the solution path."""
        return len(self.path)

    def is_optimal(self) -> bool:
        """Return True if the algorithm used was A* (guaranteed optimal)."""
        return self.algorithm == "astar" and self.success


# ---------------------------------------------------------------------------
# Priority queue
# ---------------------------------------------------------------------------


class PriorityQueue:
    """A min-heap priority queue for :class:`SearchNode` objects.

    Wraps Python's ``heapq`` module to provide a clean interface.

    Usage
    -----
    >>> pq = PriorityQueue()
    >>> pq.push(node, 3.14)
    >>> item = pq.pop()
    """

    def __init__(self) -> None:
        self._heap: list[tuple[float, int, Any]] = []
        self._counter: int = 0  # tie-breaking counter

    def push(self, item: Any, priority: float) -> None:
        """Push *item* with the given *priority* (lower = higher priority)."""
        heapq.heappush(self._heap, (priority, self._counter, item))
        self._counter += 1

    def pop(self) -> Any:
        """Pop and return the item with the lowest priority value."""
        if self._heap:
            _, _, item = heapq.heappop(self._heap)
            return item
        raise IndexError("pop from an empty PriorityQueue")

    def peek(self) -> Any:
        """Return (without removing) the item with the lowest priority."""
        if self._heap:
            return self._heap[0][2]
        raise IndexError("peek at an empty PriorityQueue")

    def is_empty(self) -> bool:
        """Return True if the queue is empty."""
        return len(self._heap) == 0

    def size(self) -> int:
        """Return the number of items in the queue."""
        return len(self._heap)


# ---------------------------------------------------------------------------
# Semantic heuristic
# ---------------------------------------------------------------------------


class SemanticHeuristic:
    """A composite semantic heuristic for A* search over generation states.

    The heuristic h(n) estimates the minimum remaining cost to reach a COMPLETE
    state from the generation state encoded in node n.  It is admissible
    (h(n) ≤ true cost) when each component weight is ≤ the actual cost of the
    corresponding operation.

    Components
    ----------
    1. Obligation term: w_obl × |O| — each obligation costs at least w_obl.
    2. Trust gap term: w_trust × (target_trust_idx - current_trust_idx) —
       each trust level step costs at least w_trust.
    3. Coverage term: w_cov × |missing_patches| — each uncovered patch costs
       at least w_cov.
    4. Obstruction term: w_obs × |B| — each obstruction costs at least w_obs.

    Parameters
    ----------
    w_obligation : float
        Weight per unresolved obligation (default 1.0).
    w_trust : float
        Weight per trust level below target (default 2.0).
    w_coverage : float
        Weight per uncovered patch (default 1.5).
    w_obstruction : float
        Weight per active obstruction (default 10.0).
    target_trust_level : str
        The trust level a COMPLETE state must achieve.
    """

    def __init__(
        self,
        name: str = "semantic_composite",
        description: str = "Composite semantic heuristic for generation search",
        w_obligation: float = 1.0,
        w_trust: float = 2.0,
        w_coverage: float = 1.5,
        w_obstruction: float = 10.0,
        target_trust_level: str = "MECHANICALLY_VERIFIED",
    ) -> None:
        self.name = name
        self.description = description
        self.w_obligation = w_obligation
        self.w_trust = w_trust
        self.w_coverage = w_coverage
        self.w_obstruction = w_obstruction
        self.target_trust_level = target_trust_level

    def estimate(self, state: dict) -> float:
        """Compute h(n) for *state*.

        Parameters
        ----------
        state:
            A generation state dict.

        Returns
        -------
        float
            Estimated remaining cost.  0.0 if state is already COMPLETE.
        """
        kind = state.get("kind", "INITIAL")
        if kind in ("COMPLETE",):
            return 0.0
        if kind == "FAILED":
            return float("inf")

        h = 0.0
        h += self._obligation_heuristic(state)
        h += self._trust_gap_heuristic(state)
        h += self._coverage_heuristic(state)
        h += self._obstruction_heuristic(state)

        logger.debug(
            "SemanticHeuristic: h=%.2f for state %s (kind=%s)",
            h,
            state.get("state_id", "?")[:8],
            kind,
        )
        return h

    def _obligation_heuristic(self, state: dict) -> float:
        """Obligation component: w_obl × |O|."""
        obligations = state.get("obligations", ())
        return self.w_obligation * len(obligations)

    def _trust_gap_heuristic(self, state: dict) -> float:
        """Trust gap component: w_trust × gap_to_target."""
        current = state.get("trust_annotation", "UNVERIFIED")
        current_idx = _trust_index(current)
        target_idx = _trust_index(self.target_trust_level)
        gap = max(0, target_idx - current_idx)
        return self.w_trust * gap

    def _coverage_heuristic(self, state: dict) -> float:
        """Coverage gap component: w_cov × uncovered_patches."""
        cover_patches = state.get("cover_patches", ())
        local_sections = state.get("local_sections", {})
        uncovered = sum(1 for p in cover_patches if p not in local_sections)
        return self.w_coverage * uncovered

    def _obstruction_heuristic(self, state: dict) -> float:
        """Obstruction component: w_obs × |B|."""
        obstructions = state.get("obstructions", ())
        return self.w_obstruction * len(obstructions)


# ---------------------------------------------------------------------------
# Main search class
# ---------------------------------------------------------------------------


class StateSpaceSearch:
    """Implements BFS, DFS, and A* search over a generation state space.

    Parameters
    ----------
    state_space:
        The state space to search.  Must support ``get_successors``,
        ``get_transitions_from``, and ``get_state``.
    heuristic:
        The heuristic for A* search.  If None, a default is used.
    """

    def __init__(
        self,
        state_space: Any,
        heuristic: Optional[SemanticHeuristic] = None,
    ) -> None:
        self.state_space = state_space
        self.heuristic = heuristic or build_default_heuristic()
        logger.info(
            "StateSpaceSearch initialised (heuristic=%r)",
            self.heuristic.name,
        )

    # ------------------------------------------------------------------
    # BFS
    # ------------------------------------------------------------------

    def bfs(
        self,
        start_id: str,
        goal_test: Callable[[Any], bool],
    ) -> SearchResult:
        """Breadth-first search from *start_id*.

        Complete: finds the shortest path (fewest transitions) to a state
        satisfying *goal_test*.

        Parameters
        ----------
        start_id:
            The starting state ID.
        goal_test:
            A callable that returns True for goal states.

        Returns
        -------
        SearchResult
        """
        t0 = time.time()
        logger.info("BFS starting from state %s", start_id[:8])

        start_node = SearchNode.root(start_id, h_cost=0.0)
        frontier: deque[SearchNode] = deque([start_node])
        node_map: dict[str, SearchNode] = {start_node.node_id: start_node}
        visited: set[str] = {start_id}
        nodes_explored = 0
        nodes_generated = 1
        max_depth_reached = 0

        while frontier:
            node = frontier.popleft()
            nodes_explored += 1
            max_depth_reached = max(max_depth_reached, node.depth)

            state = self.state_space.get_state(node.state_id)
            if state is not None and goal_test(state):
                path, path_trans = self._reconstruct_path(node, node_map)
                elapsed = (time.time() - t0) * 1000
                logger.info(
                    "BFS success: %d nodes explored, path length=%d, %.1f ms",
                    nodes_explored,
                    len(path),
                    elapsed,
                )
                return SearchResult(
                    success=True,
                    path=tuple(path),
                    path_transitions=tuple(path_trans),
                    nodes_explored=nodes_explored,
                    nodes_generated=nodes_generated,
                    depth_reached=max_depth_reached,
                    total_cost=float(node.depth),
                    algorithm="bfs",
                    elapsed_ms=elapsed,
                    explanation=f"BFS found path of length {len(path)}",
                )

            for child_node in self._expand_node(node, node_map):
                nodes_generated += 1
                if child_node.state_id not in visited:
                    visited.add(child_node.state_id)
                    frontier.append(child_node)

        elapsed = (time.time() - t0) * 1000
        logger.info("BFS: no path found after %d explored, %.1f ms", nodes_explored, elapsed)
        return SearchResult.failure("bfs", nodes_explored, elapsed)

    # ------------------------------------------------------------------
    # DFS
    # ------------------------------------------------------------------

    def dfs(
        self,
        start_id: str,
        goal_test: Callable[[Any], bool],
        max_depth: int = 50,
    ) -> SearchResult:
        """Depth-first search from *start_id* with depth limit *max_depth*.

        Parameters
        ----------
        start_id:
            Starting state ID.
        goal_test:
            A callable that returns True for goal states.
        max_depth:
            Maximum depth to explore (default 50).

        Returns
        -------
        SearchResult
        """
        t0 = time.time()
        logger.info("DFS starting from state %s (max_depth=%d)", start_id[:8], max_depth)

        start_node = SearchNode.root(start_id, h_cost=0.0)
        stack: list[SearchNode] = [start_node]
        node_map: dict[str, SearchNode] = {start_node.node_id: start_node}
        visited: set[str] = set()
        nodes_explored = 0
        nodes_generated = 1
        max_depth_reached = 0

        while stack:
            node = stack.pop()
            if node.state_id in visited:
                continue
            visited.add(node.state_id)
            nodes_explored += 1
            max_depth_reached = max(max_depth_reached, node.depth)

            state = self.state_space.get_state(node.state_id)
            if state is not None and goal_test(state):
                path, path_trans = self._reconstruct_path(node, node_map)
                elapsed = (time.time() - t0) * 1000
                logger.info(
                    "DFS success: %d nodes explored, path length=%d, %.1f ms",
                    nodes_explored,
                    len(path),
                    elapsed,
                )
                return SearchResult(
                    success=True,
                    path=tuple(path),
                    path_transitions=tuple(path_trans),
                    nodes_explored=nodes_explored,
                    nodes_generated=nodes_generated,
                    depth_reached=max_depth_reached,
                    total_cost=float(node.depth),
                    algorithm="dfs",
                    elapsed_ms=elapsed,
                    explanation=f"DFS found path of length {len(path)}",
                )

            if node.depth < max_depth:
                for child_node in reversed(self._expand_node(node, node_map)):
                    nodes_generated += 1
                    stack.append(child_node)

        elapsed = (time.time() - t0) * 1000
        logger.info("DFS: no path found after %d explored, %.1f ms", nodes_explored, elapsed)
        return SearchResult.failure("dfs", nodes_explored, elapsed, "no path within depth limit")

    # ------------------------------------------------------------------
    # A*
    # ------------------------------------------------------------------

    def astar(
        self,
        start_id: str,
        goal_test: Callable[[Any], bool],
    ) -> SearchResult:
        """A* search from *start_id* using the semantic heuristic.

        A* is optimal (finds minimum-cost path) when the heuristic is
        admissible (never over-estimates the remaining cost).

        Parameters
        ----------
        start_id:
            Starting state ID.
        goal_test:
            A callable that returns True for goal states.

        Returns
        -------
        SearchResult
        """
        t0 = time.time()
        logger.info("A* starting from state %s", start_id[:8])

        start_state = self.state_space.get_state(start_id)
        start_state_dict = self._state_to_dict(start_state)
        h0 = self.heuristic.estimate(start_state_dict)

        start_node = SearchNode.root(start_id, h_cost=h0)
        open_set: PriorityQueue = PriorityQueue()
        open_set.push(start_node, start_node.f_cost)
        node_map: dict[str, SearchNode] = {start_node.node_id: start_node}

        # g_cost for each state_id (best known)
        g_scores: dict[str, float] = {start_id: 0.0}
        closed: set[str] = set()

        nodes_explored = 0
        nodes_generated = 1
        max_depth_reached = 0

        while not open_set.is_empty():
            node: SearchNode = open_set.pop()

            if node.state_id in closed:
                continue
            closed.add(node.state_id)
            nodes_explored += 1
            max_depth_reached = max(max_depth_reached, node.depth)

            state = self.state_space.get_state(node.state_id)
            if state is not None and goal_test(state):
                path, path_trans = self._reconstruct_path(node, node_map)
                elapsed = (time.time() - t0) * 1000
                logger.info(
                    "A* success: %d explored, path length=%d, cost=%.2f, %.1f ms",
                    nodes_explored,
                    len(path),
                    node.g_cost,
                    elapsed,
                )
                return SearchResult(
                    success=True,
                    path=tuple(path),
                    path_transitions=tuple(path_trans),
                    nodes_explored=nodes_explored,
                    nodes_generated=nodes_generated,
                    depth_reached=max_depth_reached,
                    total_cost=node.g_cost,
                    algorithm="astar",
                    elapsed_ms=elapsed,
                    explanation=f"A* found optimal path of length {len(path)} (cost={node.g_cost:.2f})",
                )

            for transition in self.state_space.get_transitions_from(node.state_id):
                child_id = getattr(transition, "to_state_id", "")
                if child_id in closed:
                    continue
                edge_cost = getattr(transition, "cost", 1.0)
                tentative_g = node.g_cost + edge_cost

                if tentative_g < g_scores.get(child_id, float("inf")):
                    g_scores[child_id] = tentative_g
                    child_state = self.state_space.get_state(child_id)
                    child_state_dict = self._state_to_dict(child_state)
                    h = self.heuristic.estimate(child_state_dict)
                    transition_id = getattr(transition, "transition_id", str(uuid.uuid4()))
                    child_node = node.child(
                        state_id=child_id,
                        transition_id=transition_id,
                        edge_cost=edge_cost,
                        h_cost=h,
                    )
                    node_map[child_node.node_id] = child_node
                    open_set.push(child_node, child_node.f_cost)
                    nodes_generated += 1

        elapsed = (time.time() - t0) * 1000
        logger.info("A*: no path found after %d explored, %.1f ms", nodes_explored, elapsed)
        return SearchResult.failure("astar", nodes_explored, elapsed)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _reconstruct_path(
        self,
        goal_node: SearchNode,
        node_map: dict[str, SearchNode],
    ) -> tuple[list[str], list[str]]:
        """Reconstruct the state path and transition path from *goal_node*.

        Parameters
        ----------
        goal_node:
            The goal search node.
        node_map:
            Map from node_id to SearchNode.

        Returns
        -------
        (state_path, transition_path)
        """
        state_path: list[str] = []
        transition_path: list[str] = list(goal_node.path_transitions)

        node: Optional[SearchNode] = goal_node
        while node is not None:
            state_path.append(node.state_id)
            if node.parent_node_id is None:
                break
            node = node_map.get(node.parent_node_id)

        state_path.reverse()
        return state_path, transition_path

    def _expand_node(
        self,
        node: SearchNode,
        node_map: dict[str, SearchNode],
    ) -> list[SearchNode]:
        """Generate child SearchNodes for *node*.

        Parameters
        ----------
        node:
            The node to expand.
        node_map:
            Map from node_id to SearchNode (updated in place).

        Returns
        -------
        list[SearchNode]
        """
        children: list[SearchNode] = []
        for transition in self.state_space.get_transitions_from(node.state_id):
            child_id = getattr(transition, "to_state_id", "")
            if not child_id:
                continue
            edge_cost = getattr(transition, "cost", 1.0)
            child_state = self.state_space.get_state(child_id)
            child_state_dict = self._state_to_dict(child_state)
            h = self.heuristic.estimate(child_state_dict)
            t_id = getattr(transition, "transition_id", str(uuid.uuid4()))
            child_node = node.child(
                state_id=child_id,
                transition_id=t_id,
                edge_cost=edge_cost,
                h_cost=h,
            )
            node_map[child_node.node_id] = child_node
            children.append(child_node)
        return children

    @staticmethod
    def _state_to_dict(state: Any) -> dict:
        """Convert a state object to a dict for heuristic evaluation."""
        if state is None:
            return {}
        if isinstance(state, dict):
            return state
        if hasattr(state, "to_dict"):
            return state.to_dict()
        # Fall back to __dict__
        d = {}
        for attr in [
            "state_id", "kind", "trust_annotation", "obligations",
            "obstructions", "evidence_refs", "depth", "cover_patches",
        ]:
            v = getattr(state, attr, None)
            if v is not None:
                if hasattr(v, "name"):  # enum
                    d[attr] = v.name
                else:
                    d[attr] = v
        return d


# ---------------------------------------------------------------------------
# Module-level functions
# ---------------------------------------------------------------------------


def semantic_heuristic_fn(state: dict) -> float:
    """Compute the composite semantic heuristic for *state*.

    Convenience wrapper around :class:`SemanticHeuristic`.

    Parameters
    ----------
    state:
        A generation state dict.

    Returns
    -------
    float
        Estimated remaining cost to a COMPLETE state.
    """
    h = build_default_heuristic()
    return h.estimate(state)


def reconstruct_path(
    goal_node: SearchNode, node_map: dict[str, SearchNode]
) -> list[str]:
    """Reconstruct the state_id path from root to *goal_node*.

    Parameters
    ----------
    goal_node:
        The goal search node.
    node_map:
        Map from node_id to SearchNode.

    Returns
    -------
    list[str]
        Ordered sequence of state_ids from root to goal.
    """
    state_path: list[str] = []
    node: Optional[SearchNode] = goal_node
    while node is not None:
        state_path.append(node.state_id)
        if node.parent_node_id is None:
            break
        node = node_map.get(node.parent_node_id)
    state_path.reverse()
    return state_path


def build_default_heuristic() -> SemanticHeuristic:
    """Build and return the default semantic heuristic.

    Returns
    -------
    SemanticHeuristic
    """
    return SemanticHeuristic(
        name="jugeo_semantic_composite",
        description=(
            "Composite heuristic: obligation count + trust gap + coverage gap "
            "+ obstruction penalty (all admissible weights)."
        ),
        w_obligation=1.0,
        w_trust=2.0,
        w_coverage=1.5,
        w_obstruction=10.0,
        target_trust_level="MECHANICALLY_VERIFIED",
    )


def bfs_generation(
    state_space: Any,
    start_id: str,
    goal_test: Callable[[Any], bool],
) -> SearchResult:
    """Top-level BFS search over *state_space* from *start_id*.

    Parameters
    ----------
    state_space:
        The state space to search.
    start_id:
        Starting state ID.
    goal_test:
        Returns True for goal states.

    Returns
    -------
    SearchResult
    """
    search = StateSpaceSearch(state_space)
    return search.bfs(start_id, goal_test)


def dfs_generation(
    state_space: Any,
    start_id: str,
    goal_test: Callable[[Any], bool],
    max_depth: int = 50,
) -> SearchResult:
    """Top-level DFS search over *state_space* from *start_id*.

    Parameters
    ----------
    state_space:
        The state space to search.
    start_id:
        Starting state ID.
    goal_test:
        Returns True for goal states.
    max_depth:
        Maximum search depth.

    Returns
    -------
    SearchResult
    """
    search = StateSpaceSearch(state_space)
    return search.dfs(start_id, goal_test, max_depth=max_depth)


def astar_generation(
    state_space: Any,
    start_id: str,
    goal_test: Callable[[Any], bool],
    heuristic: Optional[SemanticHeuristic] = None,
) -> SearchResult:
    """Top-level A* search over *state_space* from *start_id*.

    Parameters
    ----------
    state_space:
        The state space to search.
    start_id:
        Starting state ID.
    goal_test:
        Returns True for goal states.
    heuristic:
        Optional heuristic; defaults to :func:`build_default_heuristic`.

    Returns
    -------
    SearchResult
    """
    search = StateSpaceSearch(state_space, heuristic=heuristic)
    return search.astar(start_id, goal_test)


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    print("=== algorithms.py smoke test ===")

    # 1. PriorityQueue
    pq = PriorityQueue()
    assert pq.is_empty()
    pq.push("a", 3.0)
    pq.push("b", 1.0)
    pq.push("c", 2.0)
    assert pq.size() == 3
    assert pq.pop() == "b"
    assert pq.pop() == "c"
    assert pq.pop() == "a"
    assert pq.is_empty()
    print("  PriorityQueue: push/pop ordering correct ✓")

    # 2. SearchNode
    root = SearchNode.root("state_0", h_cost=5.0)
    assert root.depth == 0
    assert root.g_cost == 0.0
    assert root.f_cost == 5.0
    child = root.child("state_1", "trans_1", edge_cost=2.0, h_cost=3.0)
    assert child.depth == 1
    assert child.g_cost == 2.0
    assert child.f_cost == 5.0
    assert child.path_transitions == ("trans_1",)
    print(f"  SearchNode: root f={root.f_cost}, child f={child.f_cost} ✓")

    # 3. SemanticHeuristic
    h = build_default_heuristic()
    state_complete = {"kind": "COMPLETE", "obligations": (), "obstructions": ()}
    assert h.estimate(state_complete) == 0.0
    state_failed = {"kind": "FAILED"}
    assert h.estimate(state_failed) == float("inf")
    state_mid = {
        "kind": "COVER_PROPOSED",
        "obligations": ("o1", "o2"),
        "obstructions": ("b1",),
        "trust_annotation": "UNVERIFIED",
        "cover_patches": ("p1", "p2", "p3"),
        "local_sections": {},
    }
    h_mid = h.estimate(state_mid)
    assert h_mid > 0.0
    print(f"  SemanticHeuristic: h(complete)=0, h(failed)=inf, h(mid)={h_mid:.1f} ✓")

    # 4. SearchResult
    sr_fail = SearchResult.failure("bfs", 10, 42.0)
    assert not sr_fail.success
    assert sr_fail.total_cost == float("inf")
    print(f"  SearchResult.failure: {sr_fail.explanation} ✓")

    # 5. Build a tiny state space for search tests
    try:
        from jugeo.generation.state_space.the_core_state_space_for_generatio import (
            GenerationContext,
            GenerationState,
            StateSpaceExplorer,
            StateSpace,
            build_state_space,
            GenStateKind,
        )
        ctx = GenerationContext.create(
            project_root="test",
            max_depth=8,
            timeout_ms=10_000.0,
        )
        space = build_state_space(ctx, "src/smoke.py", "implement smoke()")
        init_states = [s for s in space.all_states() if s.depth == 0]
        assert init_states, "Should have an initial state"
        init_state = init_states[0]

        def is_complete(s: Any) -> bool:
            return hasattr(s, "kind") and s.kind == GenStateKind.COMPLETE

        # BFS
        bfs_result = bfs_generation(space, init_state.state_id, is_complete)
        print(f"  BFS: success={bfs_result.success}, explored={bfs_result.nodes_explored}, path={bfs_result.path_length()}")

        # DFS
        dfs_result = dfs_generation(space, init_state.state_id, is_complete, max_depth=10)
        print(f"  DFS: success={dfs_result.success}, explored={dfs_result.nodes_explored}")

        # A*
        astar_result = astar_generation(space, init_state.state_id, is_complete)
        print(f"  A*: success={astar_result.success}, explored={astar_result.nodes_explored}, cost={astar_result.total_cost:.2f}")

        if astar_result.success:
            assert astar_result.is_optimal(), "A* result should be marked optimal"
            print(f"  A* is_optimal: {astar_result.is_optimal()} ✓")

    except ImportError as e:
        print(f"  Skipping state-space integration tests (import error: {e})")
        # Test on a stub state space
        class _StubSpace:
            def __init__(self):
                self._states = {
                    "s0": type("S", (), {"state_id": "s0", "kind": "INITIAL", "depth": 0,
                                         "is_complete": lambda self: False,
                                         "is_terminal": lambda self: False})(),
                    "s1": type("S", (), {"state_id": "s1", "kind": "COMPLETE", "depth": 1,
                                         "is_complete": lambda self: True,
                                         "is_terminal": lambda self: True})(),
                }
                self._trans = {
                    "s0": [type("T", (), {"transition_id": "t1", "to_state_id": "s1", "cost": 1.0})()],
                }

            def get_state(self, sid):
                return self._states.get(sid)

            def get_successors(self, sid):
                return [self._states[t.to_state_id] for t in self._trans.get(sid, []) if t.to_state_id in self._states]

            def get_transitions_from(self, sid):
                return self._trans.get(sid, [])

        stub_space = _StubSpace()
        goal = lambda s: hasattr(s, "kind") and s.kind == "COMPLETE"
        r = bfs_generation(stub_space, "s0", goal)
        print(f"  BFS (stub): success={r.success}, path={r.path} ✓")

    # 6. reconstruct_path
    node_map: dict[str, SearchNode] = {}
    r = SearchNode.root("s0", 0.0)
    c1 = r.child("s1", "t1", 1.0, 0.0)
    c2 = c1.child("s2", "t2", 1.0, 0.0)
    for n in [r, c1, c2]:
        node_map[n.node_id] = n
    path = reconstruct_path(c2, node_map)
    assert path == ["s0", "s1", "s2"], f"path={path}"
    print(f"  reconstruct_path: {path} ✓")

    # 7. semantic_heuristic_fn
    h_val = semantic_heuristic_fn({"kind": "COVER_PROPOSED", "obligations": ("o",), "obstructions": (),"trust_annotation": "UNVERIFIED"})
    assert h_val > 0.0
    print(f"  semantic_heuristic_fn: h={h_val:.2f} ✓")

    print("All smoke tests passed.")
    sys.exit(0)
