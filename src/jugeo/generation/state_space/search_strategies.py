r"""Chapter 40, Section 3 — Search strategies.

Theory (theory2.tex §40.3):
    A search strategy is a policy for ordering the expansion of frontier states.
    Formally: strategy π: (F, V) → σ selects a state σ ∈ F given the frontier
    F and visited set V. Different strategies trade off completeness, optimality,
    and memory usage:

      Breadth-first (BFS):  complete, optimal for uniform costs, O(b^d) memory
      Depth-first (DFS):    not complete (infinite spaces), memory O(b·d)
      Best-first:           uses heuristic h(σ) to guide towards goal; not complete
      Beam search:          best-first with bounded frontier width k; fast, not complete

    In the jugeo state space, BFS finds the minimum-round GlobalSection;
    DFS finds *some* GlobalSection quickly; best-first uses coverage fraction
    as a heuristic; beam search is practical for large patch covers.

# copilot: s03-search-strategies
"""

from __future__ import annotations

import heapq
import logging
import time
import uuid
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

try:
    from jugeo.generation.state_space.models import (
        SemanticState,
        StateTransition,
        GenerationStateSpace,
        make_initial_state,
        make_goal_state,
        make_propose_transition,
    )
    _MODELS_AVAILABLE = True
except Exception:
    _MODELS_AVAILABLE = False
    SemanticState = Any  # type: ignore[misc,assignment]
    StateTransition = Any
    GenerationStateSpace = Any

    def make_initial_state(patches): return None  # type: ignore[misc]
    def make_goal_state(patches, assignments): return None  # type: ignore[misc]
    def make_propose_transition(src, patch, sec): return None  # type: ignore[misc]

__all__ = [
    "SearchStrategy",
    "BreadthFirstStrategy",
    "DepthFirstStrategy",
    "BestFirstStrategy",
    "BeamSearchStrategy",
    "SearchResult",
    "SearchTree",
    "SearchStepResult",
    "SearchStrategyCoordinator",
    "SearchStrategyAnalyzer",
    "SearchStrategyWitness",
]


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------


@dataclass
class SearchResult:
    """The outcome of a complete search run.

    Encapsulates all statistics and artifacts produced by running a
    ``SearchStrategyCoordinator`` to completion (success or exhaustion).

    Attributes:
        success: Whether the search found a goal state.
        goal_state: The goal state discovered, or ``None`` if not found.
        path: Ordered list of ``state_id`` strings from root to goal.
        states_explored: Number of states that were *expanded* (popped from
            frontier and had their successors generated).
        states_generated: Total number of successor states produced during the
            search (includes states that may have been pruned as already-visited).
        max_frontier_size: The high-water mark of the frontier's size across
            the entire run — useful for analysing memory pressure.
        elapsed_seconds: Wall-clock duration of the search in seconds.
        strategy_name: Human-readable name of the strategy used, e.g.
            ``"BreadthFirst"``.
        rounds: The ``generation_round`` field of the goal state (or the
            maximum round seen if no goal was found).
        message: An optional free-text message — used to convey termination
            reasons such as ``"max_states exceeded"`` or ``"frontier empty"``.
    """

    success: bool
    goal_state: Optional[Any]
    path: List[str]
    states_explored: int
    states_generated: int
    max_frontier_size: int
    elapsed_seconds: float
    strategy_name: str
    rounds: int
    message: str = ""


@dataclass
class SearchTree:
    """A lightweight tree structure recording the search graph explored so far.

    The tree is rooted at the initial state and grows as the coordinator
    expands states.  It supports path reconstruction (for building the
    ``SearchResult.path`` list) and depth queries.

    Attributes:
        nodes: Maps ``state_id`` → state object for every discovered state.
        edges: Maps parent ``state_id`` → list of child ``state_id``s.
        root_id: The ``state_id`` of the root (initial) state.
        goal_ids: Set of ``state_id``s that satisfy the goal predicate.
    """

    nodes: Dict[str, Any] = field(default_factory=dict)
    edges: Dict[str, List[str]] = field(default_factory=dict)
    root_id: str = ""
    goal_ids: Set[str] = field(default_factory=set)

    # ------------------------------------------------------------------
    # Mutation helpers
    # ------------------------------------------------------------------

    def add_node(self, state: Any) -> None:
        """Register *state* in the tree.

        If the state has a ``state_id`` attribute it is used as the key;
        otherwise ``str(id(state))`` is used as a fallback so that the tree
        never silently drops a node.

        Args:
            state: Any state object (preferably a ``SemanticState``).
        """
        sid = getattr(state, "state_id", str(id(state)))
        self.nodes[sid] = state
        # Ensure an edge-list slot exists even if no children have been added yet
        if sid not in self.edges:
            self.edges[sid] = []

    def add_edge(self, parent_id: str, child_id: str) -> None:
        """Record that *child_id* was generated by expanding *parent_id*.

        Args:
            parent_id: ``state_id`` of the parent node.
            child_id: ``state_id`` of the child node.
        """
        if parent_id not in self.edges:
            self.edges[parent_id] = []
        if child_id not in self.edges[parent_id]:
            self.edges[parent_id].append(child_id)

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def get_path_to(self, state_id: str) -> List[str]:
        """Return the path from the root to *state_id* as a list of state_ids.

        The path is found via BFS over the ``edges`` adjacency structure so
        that it is always the *shortest* root-to-node path (in terms of tree
        edges, which correspond to search steps).

        Args:
            state_id: Target ``state_id`` whose path we want.

        Returns:
            A list ``[root_id, ..., state_id]`` if a path exists, or an empty
            list if *state_id* is unreachable from the root.
        """
        if not self.root_id:
            return []
        if state_id == self.root_id:
            return [self.root_id]

        # BFS: predecessor map lets us reconstruct the path once found
        predecessor: Dict[str, str] = {}
        queue: deque[str] = deque([self.root_id])
        visited_bfs: Set[str] = {self.root_id}

        while queue:
            current = queue.popleft()
            for child in self.edges.get(current, []):
                if child in visited_bfs:
                    continue
                visited_bfs.add(child)
                predecessor[child] = current
                if child == state_id:
                    # Reconstruct path by walking predecessor links backwards
                    path: List[str] = []
                    node = state_id
                    while node in predecessor:
                        path.append(node)
                        node = predecessor[node]
                    path.append(self.root_id)
                    path.reverse()
                    return path
                queue.append(child)

        # state_id is not reachable from the root
        return []

    def depth_of(self, state_id: str) -> int:
        """Return the depth of *state_id* in the search tree.

        Depth is defined as the length of the path from the root minus one
        (so the root itself is at depth 0).  If *state_id* is not reachable,
        returns -1.

        Args:
            state_id: The node whose depth we want.

        Returns:
            Non-negative integer depth, or -1 if unreachable.
        """
        path = self.get_path_to(state_id)
        if not path:
            return -1
        return len(path) - 1


@dataclass
class SearchStepResult:
    """The outcome of a *single* expansion step in the search loop.

    A step consists of: selecting a state σ from the frontier, generating
    all successors of σ, filtering already-visited successors, and pushing
    new states back onto the frontier.

    Attributes:
        expanded_state_id: The ``state_id`` of the state that was expanded.
        new_states: List of successor states generated in this step (only those
            not previously visited — already-seen states are excluded).
        frontier_size: Size of the frontier *after* this step.
        visited_count: Number of states in the visited set *after* this step.
        step_number: 1-based index of this step within the current search run.
        goal_found: Whether any successor in *new_states* satisfies the goal
            predicate (or the expanded state itself was a goal).
    """

    expanded_state_id: str
    new_states: List[Any]
    frontier_size: int
    visited_count: int
    step_number: int
    goal_found: bool


# ---------------------------------------------------------------------------
# Abstract base strategy
# ---------------------------------------------------------------------------


class SearchStrategy(ABC):
    """Abstract base class for all search strategies.

    A ``SearchStrategy`` encapsulates the *selection* policy: given a
    (possibly unordered) frontier of candidate states and the set of visited
    state-ids, it decides which state to expand next.

    Concrete subclasses must implement :meth:`select` and :meth:`name`.
    Optionally, they can override :meth:`on_push` (called every time a new
    state is about to be added to the frontier) and :meth:`priority` (used
    by strategies that rank states numerically).
    """

    @abstractmethod
    def select(self, frontier: List[Any], visited: Set[str]) -> Optional[Any]:
        """Select and *remove* the next state to expand from *frontier*.

        Implementations are responsible for mutating *frontier* in place
        (removing the selected state) **and** returning the state.  The
        coordinator passes the raw list and expects the strategy to manage
        its internal ordering data structure.

        Args:
            frontier: Mutable list of states currently on the frontier.
                The strategy removes the chosen state from this list.
            visited: Set of ``state_id`` strings already explored.  The
                strategy may consult this to skip stale entries.

        Returns:
            The selected state, or ``None`` if the frontier is empty or no
            suitable state exists.
        """

    @abstractmethod
    def name(self) -> str:
        """Return a short human-readable strategy name.

        Returns:
            A string such as ``"BreadthFirst"``, ``"DepthFirst"``, etc.
        """

    def reset(self) -> None:
        """Reset any internal mutable state so the strategy can be reused.

        Called by ``SearchStrategyCoordinator.reset()`` before a new search.
        The default implementation is a no-op; subclasses with internal
        data structures (deques, heaps, beam lists) must override this.
        """
        # Base implementation intentionally does nothing; subclasses override.
        logger.debug("SearchStrategy.reset() called on %s", self.name())

    def on_push(self, state: Any) -> None:
        """Notification hook: called *before* a state is pushed to the frontier.

        The coordinator calls this hook for every new (unvisited) successor
        state.  Strategies such as ``BeamSearchStrategy`` use this hook to
        maintain a separate bounded internal data structure and return states
        from that structure in :meth:`select`.

        Args:
            state: The state about to be added to the external frontier list.
        """
        # Default: no action; beam search overrides.
        _ = state

    def priority(self, state: Any) -> float:
        """Return a numeric priority score for *state* (lower is better).

        Used internally by priority-queue–based strategies.  The default
        returns 0.0 so that all states are treated as equal.

        Args:
            state: The state to score.

        Returns:
            A float where a *lower* value means *higher* priority.
        """
        _ = state
        return 0.0


# ---------------------------------------------------------------------------
# Concrete strategies
# ---------------------------------------------------------------------------


class BreadthFirstStrategy(SearchStrategy):
    """Breadth-first search (BFS).

    BFS expands states in FIFO order (first-in, first-out), which guarantees
    that all states at depth *d* are explored before any state at depth
    *d + 1*.

    **Completeness:** Yes — BFS is complete for finite branching factors.
    **Optimality:** Yes — BFS finds the shallowest goal (fewest transitions).
    **Memory:** O(b^d) where *b* is the branching factor and *d* is solution
        depth.  For large branching factors this can be prohibitive.

    The strategy maintains an internal ``deque`` that mirrors the frontier
    in FIFO order.  The coordinator's frontier list is used as an
    authoritative membership set; the deque drives selection order.
    """

    def __init__(self) -> None:
        # Internal FIFO queue that shadows the frontier list
        self._queue: deque[Any] = deque()
        self._pushed_ids: Set[str] = set()

    def reset(self) -> None:
        """Clear the internal queue so the strategy can be reused."""
        self._queue.clear()
        self._pushed_ids.clear()

    def on_push(self, state: Any) -> None:
        """Enqueue *state* in the FIFO deque.

        Args:
            state: The state being added to the frontier.
        """
        sid = getattr(state, "state_id", str(id(state)))
        if sid not in self._pushed_ids:
            self._queue.append(state)
            self._pushed_ids.add(sid)

    def select(self, frontier: List[Any], visited: Set[str]) -> Optional[Any]:
        """Return the oldest unvisited state in the queue (FIFO).

        Skips any state that has since been moved to the visited set (can
        happen when the same state is reachable via multiple paths and was
        already expanded via another route).

        Args:
            frontier: Mutable frontier list; the selected state is removed.
            visited: Set of already-explored state_ids.

        Returns:
            The next state to expand, or ``None`` if the frontier is empty.
        """
        # Drain stale entries from the front of the queue
        while self._queue:
            candidate = self._queue.popleft()
            sid = getattr(candidate, "state_id", str(id(candidate)))
            if sid in visited:
                # Already processed via a different path — skip
                continue
            # Remove from the external frontier list
            try:
                frontier.remove(candidate)
            except ValueError:
                # State was removed externally; continue draining
                continue
            return candidate

        # Queue empty — fall back to the raw frontier list (shouldn't happen
        # in normal usage but makes the strategy robust to external mutations)
        if frontier:
            return frontier.pop(0)
        return None

    def name(self) -> str:
        """Return ``"BreadthFirst"``."""
        return "BreadthFirst"

    def priority(self, state: Any) -> float:
        """BFS priority is the insertion order (depth proxy).

        Returns the current queue length as a proxy for depth; not used
        directly by BFS (which uses FIFO), but provided for API completeness.

        Args:
            state: The state to score.

        Returns:
            0.0 — BFS does not use numeric priorities.
        """
        _ = state
        return 0.0


class DepthFirstStrategy(SearchStrategy):
    """Depth-first search (DFS).

    DFS expands states in LIFO order (last-in, first-out), diving deep into
    the search tree before backtracking.  It is memory-efficient but not
    complete in infinite (or very deep) state spaces.

    **Completeness:** No — can loop forever in cyclic or infinite spaces
        unless a depth bound or cycle-detection (visited set) is used.
        The coordinator uses a visited set, so cycles are avoided, but very
        deep finite spaces may still require exponential time.
    **Optimality:** No — the first goal found may not be the shallowest.
    **Memory:** O(b·d) — only a single path from root to the current node
        plus its siblings needs to be retained.

    The strategy uses a list as a stack (append / pop).
    """

    def __init__(self) -> None:
        # Internal LIFO stack that shadows the frontier
        self._stack: List[Any] = []
        self._pushed_ids: Set[str] = set()

    def reset(self) -> None:
        """Clear the internal stack."""
        self._stack.clear()
        self._pushed_ids.clear()

    def on_push(self, state: Any) -> None:
        """Push *state* onto the LIFO stack.

        Args:
            state: The state being added to the frontier.
        """
        sid = getattr(state, "state_id", str(id(state)))
        if sid not in self._pushed_ids:
            self._stack.append(state)
            self._pushed_ids.add(sid)

    def select(self, frontier: List[Any], visited: Set[str]) -> Optional[Any]:
        """Return the most recently added unvisited state (LIFO).

        Args:
            frontier: Mutable frontier list; the selected state is removed.
            visited: Set of already-explored state_ids.

        Returns:
            The next state to expand, or ``None`` if the frontier is empty.
        """
        while self._stack:
            candidate = self._stack.pop()
            sid = getattr(candidate, "state_id", str(id(candidate)))
            if sid in visited:
                continue
            try:
                frontier.remove(candidate)
            except ValueError:
                continue
            return candidate

        # Fallback to raw frontier (LIFO from end of list)
        if frontier:
            return frontier.pop()
        return None

    def name(self) -> str:
        """Return ``"DepthFirst"``."""
        return "DepthFirst"

    def priority(self, state: Any) -> float:
        """DFS priority — not used numerically, returns 0.0.

        Args:
            state: Unused.

        Returns:
            0.0
        """
        _ = state
        return 0.0


class BestFirstStrategy(SearchStrategy):
    """Greedy best-first search guided by a heuristic function.

    Best-first search uses a min-heap ordered by ``heuristic(state)`` to
    always expand the state that looks most promising according to the
    heuristic.  The default heuristic is the *negative* coverage fraction
    (so higher coverage ↔ lower heap key ↔ higher priority).

    **Completeness:** Not guaranteed — may get trapped in regions of the
        space with locally good but globally misleading heuristic values.
    **Optimality:** No — the heuristic is not required to be admissible.
    **Memory:** O(b^d) in the worst case, like BFS, though in practice
        better heuristics lead to much smaller frontiers.

    The heap contains tuples ``(priority, tie_breaker, state)`` where
    *tie_breaker* is a monotonically increasing integer used to break ties
    deterministically (without comparing state objects).
    """

    def __init__(self, heuristic: Optional[Callable[[Any], float]] = None) -> None:
        """Initialise the strategy with an optional heuristic.

        Args:
            heuristic: A callable ``(state) -> float`` where lower values
                indicate more promising states.  If ``None``, the default
                heuristic is used: ``-compute_coverage_fraction(state)``,
                so states with *higher* coverage fraction are expanded first.
        """
        self._heuristic: Callable[[Any], float] = heuristic or self._default_heuristic
        # Min-heap: list of (priority, tie_breaker, state)
        self._heap: List[Tuple[float, int, Any]] = []
        self._counter: int = 0  # monotonic tie-breaker
        self._pushed_ids: Set[str] = set()

    @staticmethod
    def _default_heuristic(state: Any) -> float:
        """Default heuristic: negative coverage fraction.

        States with higher coverage (more patches assigned) receive a lower
        (more negative) priority value, making them preferred.

        Args:
            state: A ``SemanticState`` or any object with a
                ``compute_coverage_fraction()`` method.

        Returns:
            ``-coverage_fraction`` in [−1.0, 0.0].
        """
        if hasattr(state, "compute_coverage_fraction"):
            return -state.compute_coverage_fraction()
        # Fallback for non-SemanticState objects
        return 0.0

    def reset(self) -> None:
        """Clear the heap and reset the tie-breaker counter."""
        self._heap.clear()
        self._counter = 0
        self._pushed_ids.clear()

    def on_push(self, state: Any) -> None:
        """Push *state* onto the priority heap.

        Args:
            state: The state being added to the frontier.
        """
        sid = getattr(state, "state_id", str(id(state)))
        if sid not in self._pushed_ids:
            p = self._heuristic(state)
            heapq.heappush(self._heap, (p, self._counter, state))
            self._counter += 1
            self._pushed_ids.add(sid)

    def select(self, frontier: List[Any], visited: Set[str]) -> Optional[Any]:
        """Return the state with the lowest heuristic value (best estimate).

        Stale entries (already visited) are skipped lazily.

        Args:
            frontier: Mutable frontier list; the selected state is removed.
            visited: Set of already-explored state_ids.

        Returns:
            The highest-priority unvisited state, or ``None``.
        """
        while self._heap:
            _priority, _tb, candidate = heapq.heappop(self._heap)
            sid = getattr(candidate, "state_id", str(id(candidate)))
            if sid in visited:
                continue
            try:
                frontier.remove(candidate)
            except ValueError:
                continue
            return candidate

        # Heap exhausted — fallback: pick lowest-heuristic from raw frontier
        if frontier:
            best = min(frontier, key=self._heuristic)
            frontier.remove(best)
            return best
        return None

    def name(self) -> str:
        """Return ``"BestFirst"``."""
        return "BestFirst"

    def priority(self, state: Any) -> float:
        """Return the heuristic value for *state*.

        Args:
            state: The state to score.

        Returns:
            ``heuristic(state)`` — lower means higher priority.
        """
        return self._heuristic(state)


class BeamSearchStrategy(SearchStrategy):
    """Beam search: best-first with a bounded frontier width.

    Beam search keeps only the top-*k* states (by heuristic) in the
    "beam" at any time, aggressively pruning lower-quality alternatives.
    This makes it memory-efficient and fast, but sacrifices completeness:
    the optimal path may be outside the beam.

    **Completeness:** No — states outside the beam are permanently discarded.
    **Optimality:** No — but in practice often finds good solutions fast.
    **Memory:** O(k) where *k* is the beam width — the key advantage over
        BFS/best-first.

    The beam is maintained as a sorted list of ``(priority, tie_breaker,
    state)`` triples.  When the beam overflows, the worst-scored state
    (highest priority value) is dropped.
    """

    def __init__(
        self,
        beam_width: int = 5,
        heuristic: Optional[Callable[[Any], float]] = None,
    ) -> None:
        """Initialise beam search.

        Args:
            beam_width: Maximum number of states retained in the beam.
                Must be ≥ 1.  Defaults to 5.
            heuristic: Heuristic function ``(state) -> float`` (lower =
                better).  Defaults to negative coverage fraction.

        Raises:
            ValueError: If *beam_width* < 1.
        """
        if beam_width < 1:
            raise ValueError(f"beam_width must be >= 1, got {beam_width}")
        self.beam_width = beam_width
        self._heuristic: Callable[[Any], float] = (
            heuristic or BestFirstStrategy._default_heuristic
        )
        # Sorted list (ascending priority = best first): [(priority, tb, state)]
        self._beam: List[Tuple[float, int, Any]] = []
        self._counter: int = 0
        self._pushed_ids: Set[str] = set()

    def reset(self) -> None:
        """Clear the beam and reset internal counters."""
        self._beam.clear()
        self._counter = 0
        self._pushed_ids.clear()

    def on_push(self, state: Any) -> None:
        """Add *state* to the beam, trimming to ``beam_width`` if needed.

        The beam is kept sorted by priority (ascending).  When a new state
        is added and the beam overflows, the state with the *highest*
        priority value (worst score) is removed — effectively pruning the
        least-promising candidate.

        Args:
            state: The state being added to the frontier.
        """
        sid = getattr(state, "state_id", str(id(state)))
        if sid in self._pushed_ids:
            return
        p = self._heuristic(state)
        entry = (p, self._counter, state)
        self._counter += 1
        self._pushed_ids.add(sid)

        # Insert in sorted order (ascending priority)
        inserted = False
        for i, (ep, _etb, _es) in enumerate(self._beam):
            if p < ep:
                self._beam.insert(i, entry)
                inserted = True
                break
        if not inserted:
            self._beam.append(entry)

        # Trim the beam: remove the last (worst) entry if over capacity
        if len(self._beam) > self.beam_width:
            dropped_entry = self._beam.pop()  # worst score is at the end
            dropped_state = dropped_entry[2]
            dropped_sid = getattr(dropped_state, "state_id", str(id(dropped_state)))
            # Allow the dropped state to be re-pushed if needed
            self._pushed_ids.discard(dropped_sid)

    def select(self, frontier: List[Any], visited: Set[str]) -> Optional[Any]:
        """Return the best-scored state from the beam.

        Args:
            frontier: Mutable frontier list; the selected state is removed.
            visited: Set of already-explored state_ids.

        Returns:
            The best unvisited state in the beam, or ``None``.
        """
        while self._beam:
            _priority, _tb, candidate = self._beam.pop(0)  # best is first
            sid = getattr(candidate, "state_id", str(id(candidate)))
            if sid in visited:
                continue
            try:
                frontier.remove(candidate)
            except ValueError:
                continue
            return candidate

        # Beam empty — fallback to raw frontier sorted by heuristic
        if frontier:
            best = min(frontier, key=self._heuristic)
            frontier.remove(best)
            return best
        return None

    def name(self) -> str:
        """Return ``"BeamSearch(k=<width>)"``."""
        return f"BeamSearch(k={self.beam_width})"

    def priority(self, state: Any) -> float:
        """Return the heuristic value for *state*.

        Args:
            state: The state to score.

        Returns:
            ``heuristic(state)`` — lower means higher priority.
        """
        return self._heuristic(state)


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------


class SearchStrategyCoordinator:
    """Orchestrates a search run using a pluggable ``SearchStrategy``.

    The coordinator owns the frontier, visited set, and search tree.  The
    strategy is responsible only for *selecting* the next state; the
    coordinator handles successor generation, goal testing, cycle detection,
    and statistics collection.

    Typical usage::

        successor_fn = lambda s: generate_successors(s)
        coord = SearchStrategyCoordinator(successor_fn=successor_fn)
        result = coord.run(initial_state, goal_pred, BreadthFirstStrategy())

    Attributes:
        max_states: Abort after exploring this many states (safety valve).
        max_rounds: Abort once any state's ``generation_round`` exceeds this.
    """

    def __init__(
        self,
        successor_fn: Optional[Callable[[Any], List[Any]]] = None,
        max_states: int = 10000,
        max_rounds: int = 1000,
    ) -> None:
        """Initialise the coordinator.

        Args:
            successor_fn: A callable ``(state) -> List[state]`` that generates
                the successor states of *state*.  If ``None``, a default no-op
                function is used (producing no successors), which means the
                search will terminate immediately after the initial state.
            max_states: Maximum number of states to explore before giving up.
            max_rounds: Maximum ``generation_round`` value before aborting.
        """
        self._successor_fn: Callable[[Any], List[Any]] = (
            successor_fn if successor_fn is not None else lambda _s: []
        )
        self.max_states = max_states
        self.max_rounds = max_rounds

        # Mutable search state — reset between runs
        self._frontier: List[Any] = []
        self._visited: Set[str] = set()
        self._tree: SearchTree = SearchTree()
        self._step_count: int = 0
        self._states_generated: int = 0
        self._max_frontier: int = 0
        self._goal_predicate: Optional[Callable[[Any], bool]] = None
        self._current_strategy: Optional[SearchStrategy] = None
        self._goal_state: Optional[Any] = None

    def set_successor_fn(self, fn: Callable[[Any], List[Any]]) -> None:
        """Replace the successor function.

        Args:
            fn: New successor function ``(state) -> List[state]``.
        """
        self._successor_fn = fn

    def reset(self) -> None:
        """Reset all search state so the coordinator can be reused.

        Clears frontier, visited set, search tree, and statistics.  Also
        calls ``strategy.reset()`` if a current strategy is set.
        """
        self._frontier = []
        self._visited = set()
        self._tree = SearchTree()
        self._step_count = 0
        self._states_generated = 0
        self._max_frontier = 0
        self._goal_state = None
        if self._current_strategy is not None:
            self._current_strategy.reset()
        logger.debug("SearchStrategyCoordinator.reset() completed")

    def get_search_tree(self) -> SearchTree:
        """Return the search tree built by the most recent (or ongoing) search.

        Returns:
            The ``SearchTree`` instance currently held by the coordinator.
        """
        return self._tree

    def run(
        self,
        initial_state: Any,
        goal_predicate: Callable[[Any], bool],
        strategy: SearchStrategy,
    ) -> SearchResult:
        """Execute a complete search from *initial_state* using *strategy*.

        The search loop:
        1. Push *initial_state* onto the frontier.
        2. While the frontier is non-empty and limits not exceeded:
           a. Let strategy select a state σ from the frontier.
           b. If σ satisfies *goal_predicate*, return success.
           c. Mark σ as visited.
           d. Generate successors; push unvisited ones to the frontier.
        3. If the frontier empties without finding a goal, return failure.

        Args:
            initial_state: The root of the search tree.
            goal_predicate: ``(state) -> bool`` — returns ``True`` iff the
                state is a goal.
            strategy: The search strategy to use for state selection.

        Returns:
            A ``SearchResult`` summarising the outcome.
        """
        self.reset()
        strategy.reset()
        self._current_strategy = strategy
        self._goal_predicate = goal_predicate

        start_time = time.monotonic()

        # Bootstrap: register root in the tree and push to frontier
        root_id = getattr(initial_state, "state_id", str(id(initial_state)))
        self._tree.root_id = root_id
        self._tree.add_node(initial_state)
        self._frontier.append(initial_state)
        strategy.on_push(initial_state)
        self._states_generated += 1

        max_round_seen: int = getattr(initial_state, "generation_round", 0)

        while self._frontier:
            # --- Safety limits ---
            if len(self._visited) >= self.max_states:
                elapsed = time.monotonic() - start_time
                return SearchResult(
                    success=False,
                    goal_state=None,
                    path=[],
                    states_explored=len(self._visited),
                    states_generated=self._states_generated,
                    max_frontier_size=self._max_frontier,
                    elapsed_seconds=elapsed,
                    strategy_name=strategy.name(),
                    rounds=max_round_seen,
                    message=f"max_states={self.max_states} exceeded",
                )

            # --- Select next state ---
            state = strategy.select(self._frontier, self._visited)
            if state is None:
                break

            sid = getattr(state, "state_id", str(id(state)))

            # Guard against race if select returned already-visited state
            if sid in self._visited:
                continue

            self._visited.add(sid)
            self._step_count += 1

            round_val = getattr(state, "generation_round", 0)
            if round_val > max_round_seen:
                max_round_seen = round_val

            if max_round_seen > self.max_rounds:
                elapsed = time.monotonic() - start_time
                return SearchResult(
                    success=False,
                    goal_state=None,
                    path=[],
                    states_explored=len(self._visited),
                    states_generated=self._states_generated,
                    max_frontier_size=self._max_frontier,
                    elapsed_seconds=elapsed,
                    strategy_name=strategy.name(),
                    rounds=max_round_seen,
                    message=f"max_rounds={self.max_rounds} exceeded",
                )

            # --- Goal check ---
            if goal_predicate(state):
                self._goal_state = state
                self._tree.goal_ids.add(sid)
                path = self._tree.get_path_to(sid)
                elapsed = time.monotonic() - start_time
                return SearchResult(
                    success=True,
                    goal_state=state,
                    path=path,
                    states_explored=len(self._visited),
                    states_generated=self._states_generated,
                    max_frontier_size=self._max_frontier,
                    elapsed_seconds=elapsed,
                    strategy_name=strategy.name(),
                    rounds=max_round_seen,
                    message="goal found",
                )

            # --- Expand: generate successors ---
            successors = self._successor_fn(state)
            for succ in successors:
                succ_id = getattr(succ, "state_id", str(id(succ)))
                self._states_generated += 1
                self._tree.add_node(succ)
                self._tree.add_edge(sid, succ_id)
                if succ_id not in self._visited:
                    self._frontier.append(succ)
                    strategy.on_push(succ)

            # Track frontier high-water mark
            if len(self._frontier) > self._max_frontier:
                self._max_frontier = len(self._frontier)

        elapsed = time.monotonic() - start_time
        return SearchResult(
            success=False,
            goal_state=None,
            path=[],
            states_explored=len(self._visited),
            states_generated=self._states_generated,
            max_frontier_size=self._max_frontier,
            elapsed_seconds=elapsed,
            strategy_name=strategy.name(),
            rounds=max_round_seen,
            message="frontier exhausted without finding goal",
        )

    def step(self, strategy: SearchStrategy) -> SearchStepResult:
        """Perform a single expansion step using *strategy*.

        This method is intended for interactive / step-by-step debugging.
        The coordinator must have been primed (``run()`` should *not* be
        called; instead call ``reset()`` and manually push the initial state
        before calling ``step()`` repeatedly).

        Args:
            strategy: The strategy to use for selection.

        Returns:
            A ``SearchStepResult`` describing what happened in this step.

        Raises:
            RuntimeError: If the frontier is empty when ``step()`` is called.
        """
        if not self._frontier:
            raise RuntimeError("step() called on empty frontier")

        self._step_count += 1
        goal_pred = self._goal_predicate or (lambda _s: False)

        state = strategy.select(self._frontier, self._visited)
        if state is None:
            return SearchStepResult(
                expanded_state_id="<none>",
                new_states=[],
                frontier_size=len(self._frontier),
                visited_count=len(self._visited),
                step_number=self._step_count,
                goal_found=False,
            )

        sid = getattr(state, "state_id", str(id(state)))
        self._visited.add(sid)

        goal_found = goal_pred(state)
        if goal_found:
            self._goal_state = state

        new_states: List[Any] = []
        if not goal_found:
            successors = self._successor_fn(state)
            for succ in successors:
                succ_id = getattr(succ, "state_id", str(id(succ)))
                self._states_generated += 1
                self._tree.add_node(succ)
                self._tree.add_edge(sid, succ_id)
                if succ_id not in self._visited:
                    self._frontier.append(succ)
                    strategy.on_push(succ)
                    new_states.append(succ)

        if len(self._frontier) > self._max_frontier:
            self._max_frontier = len(self._frontier)

        return SearchStepResult(
            expanded_state_id=sid,
            new_states=new_states,
            frontier_size=len(self._frontier),
            visited_count=len(self._visited),
            step_number=self._step_count,
            goal_found=goal_found,
        )


# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------


class SearchStrategyAnalyzer:
    """Compares, estimates, and recommends search strategies.

    The analyzer works post-hoc (consuming ``SearchResult`` objects) and
    pre-hoc (estimating theoretical complexity from problem parameters).  It
    is stateless — all methods are pure functions of their arguments.
    """

    def compare_strategies(self, results: List[SearchResult]) -> Dict[str, Any]:
        """Compare a list of search results across several dimensions.

        Produces a dictionary suitable for logging or display.  For each
        strategy, it records: success, states_explored, elapsed_seconds,
        and max_frontier_size.  It also identifies the "winner" on each
        dimension.

        Args:
            results: List of ``SearchResult`` objects from different strategies
                run on the *same* problem (or comparable problems).

        Returns:
            A dict with keys:
            - ``"per_strategy"``: ``{name: {stats…}}`` for every result.
            - ``"fastest"``      : name of the strategy with lowest elapsed_seconds.
            - ``"most_efficient"``: name with fewest states_explored (among successes).
            - ``"min_memory"``   : name with smallest max_frontier_size.
            - ``"all_successful"``: bool — whether all strategies succeeded.
        """
        if not results:
            return {"per_strategy": {}, "fastest": None, "most_efficient": None,
                    "min_memory": None, "all_successful": False}

        per_strategy: Dict[str, Dict[str, Any]] = {}
        for r in results:
            per_strategy[r.strategy_name] = {
                "success": r.success,
                "states_explored": r.states_explored,
                "states_generated": r.states_generated,
                "elapsed_seconds": r.elapsed_seconds,
                "max_frontier_size": r.max_frontier_size,
                "rounds": r.rounds,
                "message": r.message,
            }

        # Fastest overall (regardless of success)
        fastest = min(results, key=lambda r: r.elapsed_seconds).strategy_name

        # Most efficient: fewest states_explored among successful runs
        successful = [r for r in results if r.success]
        most_efficient: Optional[str] = None
        if successful:
            most_efficient = min(successful, key=lambda r: r.states_explored).strategy_name

        # Minimum memory pressure: smallest max_frontier_size
        min_memory = min(results, key=lambda r: r.max_frontier_size).strategy_name

        return {
            "per_strategy": per_strategy,
            "fastest": fastest,
            "most_efficient": most_efficient,
            "min_memory": min_memory,
            "all_successful": all(r.success for r in results),
        }

    def estimate_complexity(
        self, strategy: SearchStrategy, state_space_size: int
    ) -> Dict[str, Any]:
        """Estimate theoretical time and space complexity for a strategy.

        Uses textbook complexity formulas parameterised by a rough estimate
        of the state-space size *n*.  The branching factor *b* is assumed to
        be ``max(2, int(n**0.5))``.  Solution depth *d* is assumed to be
        ``max(1, int(log2(n)))``.

        Args:
            strategy: The strategy whose complexity to estimate.
            state_space_size: Approximate total number of states in the
                problem.  Must be ≥ 1.

        Returns:
            A dict with keys:
            - ``"strategy"``         : strategy name.
            - ``"time_complexity"``  : textbook big-O string.
            - ``"space_complexity"`` : textbook big-O string.
            - ``"estimated_states_explored"``: int estimate.
            - ``"estimated_max_frontier"``   : int estimate.
            - ``"complete"``         : bool.
            - ``"optimal"``          : bool.
            - ``"notes"``            : str.
        """
        import math

        n = max(1, state_space_size)
        b = max(2, int(n ** 0.5))
        d = max(1, int(math.log2(n)))

        name = strategy.name()

        if "BreadthFirst" in name:
            return {
                "strategy": name,
                "time_complexity": "O(b^d)",
                "space_complexity": "O(b^d)",
                "estimated_states_explored": min(n, b ** d),
                "estimated_max_frontier": min(n, b ** (d - 1) * b),
                "complete": True,
                "optimal": True,
                "notes": "Optimal for uniform-cost edges; very memory-hungry for large b.",
            }
        if "DepthFirst" in name:
            return {
                "strategy": name,
                "time_complexity": "O(b^m)",
                "space_complexity": "O(b*d)",
                "estimated_states_explored": min(n, b ** d),
                "estimated_max_frontier": b * d,
                "complete": False,
                "optimal": False,
                "notes": "Memory-efficient but may not find optimal or any solution.",
            }
        if "BestFirst" in name:
            return {
                "strategy": name,
                "time_complexity": "O(b^d)",
                "space_complexity": "O(b^d)",
                "estimated_states_explored": max(1, n // b),
                "estimated_max_frontier": max(1, n // b),
                "complete": False,
                "optimal": False,
                "notes": "With perfect heuristic approaches O(d); not complete in general.",
            }
        if "Beam" in name:
            # Parse beam width k from name if present
            k = getattr(strategy, "beam_width", 5)
            return {
                "strategy": name,
                "time_complexity": "O(k * b * d)",
                "space_complexity": "O(k)",
                "estimated_states_explored": k * b * d,
                "estimated_max_frontier": k,
                "complete": False,
                "optimal": False,
                "notes": f"Beam width k={k} caps memory; may prune optimal paths.",
            }

        # Generic fallback
        return {
            "strategy": name,
            "time_complexity": "O(n)",
            "space_complexity": "O(n)",
            "estimated_states_explored": n,
            "estimated_max_frontier": n,
            "complete": True,
            "optimal": False,
            "notes": "Unknown strategy — using linear worst-case estimate.",
        }

    def recommend_strategy(self, problem_profile: Dict[str, Any]) -> str:
        """Recommend the best strategy for a given problem profile.

        Decision logic (in priority order):

        1. If ``memory_limited`` is ``True`` and space is large → BeamSearch.
        2. If ``has_heuristic`` is ``True`` and space is not tiny → BestFirst.
        3. If ``state_space_size`` is small (≤ 500) → BreadthFirst (guaranteed optimal).
        4. If ``branching_factor`` is very high (> 50) → BeamSearch.
        5. Default → BreadthFirst.

        Args:
            problem_profile: A dict that may contain:
                - ``"state_space_size"``  (int, default 1000)
                - ``"branching_factor"``  (int, default 10)
                - ``"has_heuristic"``     (bool, default False)
                - ``"memory_limited"``    (bool, default False)
                - ``"need_optimal"``      (bool, default False)

        Returns:
            A strategy name string: one of ``"BreadthFirst"``, ``"DepthFirst"``,
            ``"BestFirst"``, or ``"BeamSearch"``.
        """
        size = int(problem_profile.get("state_space_size", 1000))
        branching = int(problem_profile.get("branching_factor", 10))
        has_heuristic = bool(problem_profile.get("has_heuristic", False))
        memory_limited = bool(problem_profile.get("memory_limited", False))
        need_optimal = bool(problem_profile.get("need_optimal", False))

        if need_optimal:
            # Only BFS guarantees optimality (for uniform costs)
            return "BreadthFirst"

        if memory_limited and size > 1000:
            return "BeamSearch"

        if has_heuristic and size > 500:
            return "BestFirst"

        if size <= 500:
            return "BreadthFirst"

        if branching > 50:
            return "BeamSearch"

        return "BreadthFirst"


# ---------------------------------------------------------------------------
# Witness (immutable audit record)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SearchStrategyWitness:
    """An immutable, hashable audit record for a single search run.

    A ``SearchStrategyWitness`` is produced after a ``SearchResult`` is
    obtained.  It captures the key statistics in a frozen dataclass that can
    be stored in sets, used as dict keys, or serialised for logging.

    Attributes:
        witness_id: A unique UUID4 string identifying this witness record.
        strategy_name: Name of the strategy (from ``SearchResult.strategy_name``).
        states_explored: Number of states expanded during the search.
        states_generated: Total states generated (including those pruned).
        max_frontier_size: High-water mark of the frontier.
        goal_found: Whether the search succeeded.
        elapsed_seconds: Wall-clock duration in seconds.
        timestamp: ``time.time()`` value captured at witness creation time.
    """

    witness_id: str
    strategy_name: str
    states_explored: int
    states_generated: int
    max_frontier_size: int
    goal_found: bool
    elapsed_seconds: float
    timestamp: float

    @classmethod
    def from_result(cls, result: SearchResult) -> "SearchStrategyWitness":
        """Construct a ``SearchStrategyWitness`` from a ``SearchResult``.

        Args:
            result: The ``SearchResult`` to record.

        Returns:
            A new, frozen ``SearchStrategyWitness`` with a freshly generated
            ``witness_id`` and the current system time as ``timestamp``.
        """
        return cls(
            witness_id=str(uuid.uuid4()),
            strategy_name=result.strategy_name,
            states_explored=result.states_explored,
            states_generated=result.states_generated,
            max_frontier_size=result.max_frontier_size,
            goal_found=result.success,
            elapsed_seconds=result.elapsed_seconds,
            timestamp=time.time(),
        )


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------


def _smoke_test() -> None:
    """Run a quick sanity check of all four strategies.

    Builds a small linear state space (using ``make_linear_space`` when the
    models module is available, or a hand-crafted chain of simple objects
    otherwise), then runs BFS, DFS, BestFirst, and BeamSearch over it,
    comparing results via ``SearchStrategyAnalyzer``.

    This function is intentionally kept self-contained and side-effect-free
    (it only writes to the logger) so it can be called safely from unit tests.
    """
    logger.info("=== s03 smoke test starting ===")

    if _MODELS_AVAILABLE:
        try:
            from jugeo.generation.state_space.models import (  # type: ignore[import]
                make_linear_space,
            )
            space = make_linear_space(6)
            _states_map: Dict[str, Any] = (
                space.states
                if hasattr(space.states, "get")
                else {st.state_id: st for st in space.states}
            )
            state_list: List[Any] = list(_states_map.values())

            def _succ(s: Any, _sp: Any = space) -> List[Any]:
                results_inner: List[Any] = []
                transitions_iter = (
                    _sp.transitions.values()
                    if hasattr(_sp.transitions, "values")
                    else _sp.transitions
                )
                sm: Dict[str, Any] = (
                    _sp.states
                    if hasattr(_sp.states, "get")
                    else {st.state_id: st for st in _sp.states}
                )
                for t in transitions_iter:
                    if t.source_state_id == s.state_id:
                        tgt = sm.get(t.target_state_id)
                        if tgt is not None:
                            results_inner.append(tgt)
                return results_inner

            initial = state_list[0]
            goal_pred: Callable[[Any], bool] = lambda s: bool(
                getattr(s, "is_goal_state", False)
            )
            successor_fn = _succ
        except Exception as exc:
            logger.warning("make_linear_space unavailable (%s); using fallback", exc)
            state_list = []
            initial = None
            goal_pred = lambda _s: False
            successor_fn = lambda _s: []
    else:
        state_list = []
        initial = None
        goal_pred = lambda _s: False
        successor_fn = lambda _s: []

    if initial is None:
        # Fallback: build a tiny chain of plain objects
        class _S:
            def __init__(self, i: int) -> None:
                self.state_id = f"s{i}"
                self.generation_round = i
                self.is_goal_state = False
                self.is_terminal = False

            def compute_coverage_fraction(self) -> float:
                return 0.0

        chain = [_S(i) for i in range(6)]
        chain[-1].is_goal_state = True
        chain[-1].is_terminal = True
        chain_map = {s.state_id: s for s in chain}
        initial = chain[0]
        goal_pred = lambda s: getattr(s, "is_goal_state", False)

        def successor_fn(s: Any, _cm: Any = chain_map, _ch: Any = chain) -> List[Any]:
            idx = next((i for i, x in enumerate(_ch) if x.state_id == s.state_id), None)
            if idx is None or idx + 1 >= len(_ch):
                return []
            return [_ch[idx + 1]]

    strategies: List[SearchStrategy] = [
        BreadthFirstStrategy(),
        DepthFirstStrategy(),
        BestFirstStrategy(),
        BeamSearchStrategy(beam_width=3),
    ]

    results: List[SearchResult] = []
    analyzer = SearchStrategyAnalyzer()
    coord = SearchStrategyCoordinator(successor_fn=successor_fn, max_states=500)

    for strat in strategies:
        res = coord.run(initial, goal_pred, strat)
        results.append(res)
        w = SearchStrategyWitness.from_result(res)
        logger.info(
            "[%s] success=%s explored=%d path_len=%d witness=%s",
            strat.name(), res.success, res.states_explored,
            len(res.path), w.witness_id[:8],
        )

    comparison = analyzer.compare_strategies(results)
    logger.info("Fastest strategy: %s", comparison["fastest"])
    logger.info("Most efficient:   %s", comparison["most_efficient"])
    logger.info("Min memory:       %s", comparison["min_memory"])

    # Complexity estimates
    for strat in strategies:
        est = analyzer.estimate_complexity(strat, state_space_size=100)
        logger.info(
            "[%s] time=%s space=%s complete=%s",
            est["strategy"], est["time_complexity"],
            est["space_complexity"], est["complete"],
        )

    # Recommendation examples
    profiles = [
        {"state_space_size": 50, "has_heuristic": False},
        {"state_space_size": 5000, "has_heuristic": True},
        {"state_space_size": 100000, "memory_limited": True},
        {"need_optimal": True},
    ]
    for profile in profiles:
        rec = analyzer.recommend_strategy(profile)
        logger.info("Profile %s → %s", profile, rec)

    logger.info("=== s03 smoke test complete ===")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    _smoke_test()
