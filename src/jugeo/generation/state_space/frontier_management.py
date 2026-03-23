r"""Chapter 40, Section 2 — Frontier management.

Theory (theory2.tex §40.2):
    The frontier F ⊆ Σ is the set of discovered-but-unexplored states.
    Different data structures support different traversal orderings:
      FIFO (queue)   → breadth-first traversal (complete, finds shortest path)
      LIFO (stack)   → depth-first traversal (memory-efficient, may miss optimal)
      Priority queue → best-first traversal (heuristic-guided, most efficient)
      Beam           → keeps only top-k states (approximation, bounded memory)
    The frontier evolves as: F_{t+1} = (F_t \ {σ}) ∪ successors(σ)
    where σ is the selected state. Frontier diversity measures how spread the
    current search is across Σ; low diversity signals premature convergence.

# copilot: s02-frontier-management
"""

from __future__ import annotations

import heapq
import logging
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

try:
    from jugeo.generation.state_space.models import (
        SemanticState,
        GenerationStateSpace,
        make_initial_state,
    )
    _MODELS_AVAILABLE = True
except Exception:
    _MODELS_AVAILABLE = False
    SemanticState = Any  # type: ignore[misc,assignment]
    GenerationStateSpace = Any

    def make_initial_state(patches):
        return None


__all__ = [
    "FrontierType",
    "FrontierStats",
    "FrontierWitness",
    "FrontierCoordinator",
    "FrontierAnalyzer",
    "BoundedPriorityFrontier",
    "BeamFrontier",
]


# ---------------------------------------------------------------------------
# FrontierType — enumeration of supported traversal orderings
# ---------------------------------------------------------------------------

class FrontierType(Enum):
    """Enumeration of frontier traversal strategies.

    Each strategy offers a different trade-off between completeness,
    memory usage, and search efficiency:

    - ``FIFO``: Breadth-first; level-by-level expansion guarantees the
      shortest path is found first (in unweighted spaces).
    - ``LIFO``: Depth-first; recurses as deeply as possible before
      backtracking; memory-efficient but may loop or miss optimal paths.
    - ``PRIORITY``: Best-first using a numeric priority (lower = better);
      subsumes both Dijkstra (path cost) and greedy best-first (heuristic).
    - ``BEAM``: Keeps only the top-k states at each step; approximation
      method that bounds memory at the cost of completeness.
    """

    FIFO = auto()
    LIFO = auto()
    PRIORITY = auto()
    BEAM = auto()


# ---------------------------------------------------------------------------
# FrontierStats — lightweight snapshot of frontier health
# ---------------------------------------------------------------------------

@dataclass
class FrontierStats:
    """Snapshot statistics for a :class:`FrontierCoordinator`.

    This dataclass is produced by :meth:`FrontierCoordinator.get_stats` and
    is intended for logging, debugging, and convergence monitoring.  All
    counters accumulate from the moment the coordinator was created; they are
    never reset unless :meth:`FrontierCoordinator.clear` is called.

    Attributes:
        frontier_type: The :class:`FrontierType` in use.
        current_size: Number of states currently on the frontier.
        total_pushed: Cumulative count of :meth:`~FrontierCoordinator.push`
            calls that successfully added a state (duplicates excluded).
        total_popped: Cumulative count of successful
            :meth:`~FrontierCoordinator.pop` calls.
        max_size_seen: High-water mark of ``current_size`` across all rounds.
        duplicate_pushes: Number of push attempts that were rejected because
            the state was already on the frontier.
        empty_pops: Number of pop attempts made while the frontier was empty.
        avg_priority: Mean priority of all states currently on the frontier.
            For FIFO/LIFO frontiers, priorities are not tracked and this will
            always be ``0.0``.
        stagnation_rounds: Number of consecutive rounds in which
            ``current_size`` did not grow.  A high value may indicate search
            convergence or a stalled heuristic.
        timestamp: Wall-clock time (seconds since epoch) when this snapshot
            was captured.
    """

    frontier_type: FrontierType
    current_size: int
    total_pushed: int
    total_popped: int
    max_size_seen: int
    duplicate_pushes: int
    empty_pops: int
    avg_priority: float
    stagnation_rounds: int
    timestamp: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# FrontierWitness — immutable audit record of a coordinator snapshot
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class FrontierWitness:
    """Immutable audit record capturing the state of a frontier at a point in time.

    Witnesses are used to build a history that can later be analysed for
    stagnation, diversity collapse, or anomalous growth.  Because they are
    frozen and use ``__slots__``, they are cheap to store in large lists.

    Attributes:
        witness_id: Globally unique identifier for this witness record (UUID4).
        frontier_type: String name of the :class:`FrontierType` in use at
            capture time.
        size_at_witness: Number of states on the frontier when captured.
        pop_count: Total pops performed on the coordinator up to this moment.
        push_count: Total (non-duplicate) pushes performed up to this moment.
        timestamp: Wall-clock time of capture.
    """

    witness_id: str
    frontier_type: str
    size_at_witness: int
    pop_count: int
    push_count: int
    timestamp: float

    @classmethod
    def capture(cls, coordinator: "FrontierCoordinator") -> "FrontierWitness":
        """Capture a :class:`FrontierWitness` from a live coordinator.

        Reads the coordinator's internal statistics atomically (no locking is
        performed; this is not thread-safe) and packages them into a new
        immutable witness object.

        Args:
            coordinator: The :class:`FrontierCoordinator` to snapshot.

        Returns:
            A freshly constructed :class:`FrontierWitness` reflecting the
            current state of *coordinator*.

        Example::

            w = FrontierWitness.capture(coordinator)
            history.append(w)
        """
        stats = coordinator.get_stats()
        return cls(
            witness_id=str(uuid.uuid4()),
            frontier_type=coordinator.frontier_type.name,
            size_at_witness=stats.current_size,
            pop_count=stats.total_popped,
            push_count=stats.total_pushed,
            timestamp=stats.timestamp,
        )


# ---------------------------------------------------------------------------
# BoundedPriorityFrontier — heap-based priority queue with size cap
# ---------------------------------------------------------------------------

class BoundedPriorityFrontier:
    """A heap-backed priority queue that enforces a maximum capacity.

    When the heap is full and a new state is pushed, the *worst* state
    (highest priority value when minimising, or lowest when maximising) is
    evicted to make room.  This ensures that the frontier never consumes more
    than ``max_size`` entries while always retaining the most promising states.

    The heap stores tuples of ``(effective_priority, push_counter, state)``
    where ``push_counter`` is a monotonically increasing integer used to break
    ties deterministically without comparing states directly.

    Attributes:
        max_size: Maximum number of states the frontier may hold.
        minimize: If ``True`` (default), lower priority values are popped
            first (min-heap semantics).  If ``False``, higher values come
            first (achieved by negating priorities).
    """

    def __init__(self, max_size: int = 1000, minimize: bool = True) -> None:
        """Initialise a :class:`BoundedPriorityFrontier`.

        Args:
            max_size: Hard upper bound on frontier size.  Must be ≥ 1.
            minimize: When ``True``, the state with the *lowest* priority is
                popped first (standard min-heap).  When ``False``, the state
                with the *highest* priority is popped first.

        Raises:
            ValueError: If *max_size* is less than 1.
        """
        if max_size < 1:
            raise ValueError(f"max_size must be ≥ 1, got {max_size}")
        self.max_size = max_size
        self.minimize = minimize
        # Heap of (effective_priority, push_counter, state).
        self._heap: list = []
        # Set of state_ids currently in the heap (for O(1) membership tests).
        self._in_frontier: Set[str] = set()
        # Monotonically increasing counter used to break priority ties.
        self._push_counter: int = 0

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _effective_priority(self, priority: float) -> float:
        """Return the priority value as stored in the heap.

        For a min-heap (``minimize=True``) this is just *priority*.  For a
        max-heap (``minimize=False``) we negate the value so that Python's
        ``heapq`` module (which is always a min-heap) pops the highest
        priority first.

        Args:
            priority: Raw caller-supplied priority.

        Returns:
            The priority value to store in the heap tuple.
        """
        return priority if self.minimize else -priority

    def _state_id(self, state: Any) -> str:
        """Extract a string identifier from *state*.

        Tries ``state.state_id`` (the attribute used by :class:`SemanticState`),
        then ``state.id``, then falls back to ``str(state)``.

        Args:
            state: Any state object.

        Returns:
            A string that uniquely identifies the state.
        """
        if hasattr(state, "state_id"):
            return str(state.state_id)
        if hasattr(state, "id"):
            return str(state.id)
        return str(state)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def push(self, state: Any, priority: float) -> bool:
        """Push *state* onto the frontier with the given *priority*.

        If the state is already present (checked via ``state_id``), the push
        is silently ignored and ``False`` is returned.  If the frontier is at
        capacity after the push, the *worst* state is evicted.

        Args:
            state: The state to add.  Must expose a ``state_id`` attribute or
                fall back to ``str(state)`` for identity comparison.
            priority: Numeric priority.  Lower = better when ``minimize=True``.

        Returns:
            ``True`` if the state was added, ``False`` if it was a duplicate.
        """
        sid = self._state_id(state)
        if sid in self._in_frontier:
            logger.debug("BoundedPriorityFrontier: duplicate push for state %s", sid)
            return False

        eff = self._effective_priority(priority)
        entry = (eff, self._push_counter, state)
        self._push_counter += 1
        heapq.heappush(self._heap, entry)
        self._in_frontier.add(sid)

        # Evict the worst entry if we are now over capacity.
        if len(self._heap) > self.max_size:
            # For a min-heap the *worst* element (highest effective priority)
            # is the last element after heapify, but Python's heapq does not
            # expose the maximum efficiently.  We use a linear scan to find
            # and remove it; this is acceptable because eviction is rare (only
            # when the heap is full) and max_size is typically large.
            worst_idx = max(range(len(self._heap)), key=lambda i: self._heap[i][0])
            worst_entry = self._heap[worst_idx]
            # Swap with the last element and pop, then restore heap invariant.
            self._heap[worst_idx] = self._heap[-1]
            self._heap.pop()
            heapq.heapify(self._heap)
            worst_sid = self._state_id(worst_entry[2])
            self._in_frontier.discard(worst_sid)
            logger.debug(
                "BoundedPriorityFrontier: evicted state %s to enforce max_size=%d",
                worst_sid,
                self.max_size,
            )
            # If we evicted the state we just pushed, report failure.
            if worst_sid == sid:
                return False

        return True

    def pop(self) -> Optional[Any]:
        """Remove and return the best-priority state.

        Args:
            (none)

        Returns:
            The state with the lowest priority value (or highest, if
            ``minimize=False``), or ``None`` if the frontier is empty.
        """
        while self._heap:
            _eff, _counter, state = heapq.heappop(self._heap)
            sid = self._state_id(state)
            # The state might have been removed via a concurrent eviction path;
            # skip stale entries that are no longer in ``_in_frontier``.
            if sid in self._in_frontier:
                self._in_frontier.discard(sid)
                return state
        return None

    def peek(self) -> Optional[Any]:
        """Return the best-priority state *without* removing it.

        Args:
            (none)

        Returns:
            The state that would be returned by the next :meth:`pop`, or
            ``None`` if the frontier is empty.
        """
        # The heap root is always the minimum effective priority entry.
        if not self._heap:
            return None
        return self._heap[0][2]

    def __len__(self) -> int:
        """Return the number of states currently on the frontier.

        Returns:
            Non-negative integer count.
        """
        return len(self._in_frontier)

    def __contains__(self, state_id: str) -> bool:
        """Test whether a state with the given id is on the frontier.

        Args:
            state_id: The string identifier to look up.

        Returns:
            ``True`` if *state_id* is present, ``False`` otherwise.
        """
        return state_id in self._in_frontier

    def clear(self) -> None:
        """Remove all states from the frontier.

        After this call, :meth:`is_empty` returns ``True`` and all counters
        (except ``_push_counter``) are reset.

        Args:
            (none)

        Returns:
            ``None``
        """
        self._heap.clear()
        self._in_frontier.clear()


# ---------------------------------------------------------------------------
# BeamFrontier — bounded beam search frontier
# ---------------------------------------------------------------------------

class BeamFrontier:
    """A frontier that retains only the top-k (beam) states at each step.

    After every :meth:`push`, the frontier is automatically trimmed to
    ``beam_width`` by calling :meth:`trim`.  States are scored by *scorer*
    (lower = better); by default, lower negative coverage fraction is used
    so states with more coverage are preferred.

    This approximation trades completeness for bounded memory and is most
    effective when a good scorer is available.

    Attributes:
        beam_width: Maximum number of states to retain.
        scorer: Callable mapping a state to a float (lower = better).
    """

    def __init__(
        self,
        beam_width: int = 10,
        scorer: Optional[Callable] = None,
    ) -> None:
        """Initialise a :class:`BeamFrontier`.

        Args:
            beam_width: Maximum number of states kept after each trim
                operation.  Must be ≥ 1.
            scorer: Optional callable ``scorer(state) -> float``.  Lower
                values are considered better.  Defaults to
                ``-state.compute_coverage_fraction()`` when the state
                exposes that method, otherwise ``0.0``.

        Raises:
            ValueError: If *beam_width* is less than 1.
        """
        if beam_width < 1:
            raise ValueError(f"beam_width must be ≥ 1, got {beam_width}")
        self.beam_width = beam_width
        # Use the supplied scorer or fall back to negative coverage fraction.
        if scorer is not None:
            self.scorer: Callable = scorer
        else:
            def _default_scorer(state: Any) -> float:
                if hasattr(state, "compute_coverage_fraction"):
                    # Negate so that higher coverage → lower score → preferred.
                    return -state.compute_coverage_fraction()
                return 0.0
            self.scorer = _default_scorer

        # Internal beam stored as a plain list; trimmed after every push.
        self._beam: List[Any] = []

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _state_id(self, state: Any) -> str:
        """Extract a stable string identifier from *state*.

        Args:
            state: Any state object.

        Returns:
            String id.
        """
        if hasattr(state, "state_id"):
            return str(state.state_id)
        if hasattr(state, "id"):
            return str(state.id)
        return str(state)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def push(self, state: Any) -> None:
        """Add *state* to the beam.

        The state is appended unconditionally (no deduplication here; the
        coordinator layer handles that).  :meth:`trim` is called immediately
        so the beam never exceeds ``beam_width + 1`` entries momentarily.

        Args:
            state: State to add to the beam.

        Returns:
            ``None``
        """
        self._beam.append(state)
        self.trim()

    def pop(self) -> Optional[Any]:
        """Remove and return the best-scored state.

        The *best* state is the one with the lowest scorer value.

        Args:
            (none)

        Returns:
            The best-scored state, or ``None`` if the beam is empty.
        """
        if not self._beam:
            return None
        # Identify the best state by minimising the scorer.
        best_idx = min(range(len(self._beam)), key=lambda i: self.scorer(self._beam[i]))
        best = self._beam[best_idx]
        # Remove it by swapping with the last element (O(1)).
        self._beam[best_idx] = self._beam[-1]
        self._beam.pop()
        return best

    def peek(self) -> Optional[Any]:
        """Return the best-scored state without removing it.

        Args:
            (none)

        Returns:
            The state with the lowest scorer value, or ``None`` if empty.
        """
        if not self._beam:
            return None
        return min(self._beam, key=self.scorer)

    def trim(self) -> None:
        """Prune the beam to at most ``beam_width`` states.

        States are sorted ascending by ``scorer``; the first ``beam_width``
        are kept.  This ensures the beam always contains the most promising
        states according to the heuristic.

        Args:
            (none)

        Returns:
            ``None``
        """
        if len(self._beam) > self.beam_width:
            # Sort by score (ascending = best first) and keep top entries.
            self._beam.sort(key=self.scorer)
            dropped = self._beam[self.beam_width:]
            self._beam = self._beam[: self.beam_width]
            logger.debug(
                "BeamFrontier: trimmed %d state(s) to enforce beam_width=%d",
                len(dropped),
                self.beam_width,
            )

    def __len__(self) -> int:
        """Return the current beam size.

        Returns:
            Non-negative integer.
        """
        return len(self._beam)

    def get_beam(self) -> List[Any]:
        """Return a *copy* of the current beam (sorted best-first).

        Callers should not mutate the returned list; use the push/pop API
        to modify the frontier.

        Args:
            (none)

        Returns:
            List of states sorted by scorer (ascending), best first.
        """
        return sorted(self._beam, key=self.scorer)


# ---------------------------------------------------------------------------
# FrontierCoordinator — unified high-level frontier manager
# ---------------------------------------------------------------------------

class FrontierCoordinator:
    """High-level manager that wraps any of the four frontier strategies.

    :class:`FrontierCoordinator` provides a single, consistent push/pop
    interface regardless of the underlying data structure (FIFO queue, LIFO
    stack, priority heap, or beam).  It maintains cumulative statistics and
    deduplicates states by ``state_id``.

    Design notes:

    * Deduplication is done at this layer for FIFO and LIFO frontiers (using
      a ``_seen`` set) because :class:`deque` / list do not track membership.
    * For PRIORITY and BEAM, deduplication is delegated to the inner class
      where possible; :class:`FrontierCoordinator` still maintains the
      ``_seen`` set for consistency.
    * ``stagnation_rounds`` counts consecutive rounds in which no new state
      was successfully pushed; it is reset whenever a push succeeds.

    Attributes:
        frontier_type: The :class:`FrontierType` of this coordinator.
        beam_width: Beam width (only meaningful for ``BEAM`` type).
        max_priority_size: Max-size cap for ``PRIORITY`` type.
        scorer: Optional scorer for ``BEAM`` type.
    """

    def __init__(
        self,
        frontier_type: FrontierType = FrontierType.FIFO,
        beam_width: int = 10,
        max_priority_size: int = 10_000,
        scorer: Optional[Callable] = None,
    ) -> None:
        """Initialise a :class:`FrontierCoordinator`.

        Args:
            frontier_type: Which traversal strategy to use.
            beam_width: Beam width; only used when *frontier_type* is
                ``FrontierType.BEAM``.
            max_priority_size: Maximum heap size; only used when
                *frontier_type* is ``FrontierType.PRIORITY``.
            scorer: Optional scorer callable passed through to
                :class:`BeamFrontier` when *frontier_type* is ``BEAM``.
        """
        self.frontier_type = frontier_type
        self.beam_width = beam_width
        self.max_priority_size = max_priority_size
        self.scorer = scorer

        # Instantiate the backing data structure.
        if frontier_type == FrontierType.FIFO:
            self._fifo: deque = deque()
        elif frontier_type == FrontierType.LIFO:
            self._lifo: list = []
        elif frontier_type == FrontierType.PRIORITY:
            self._priority = BoundedPriorityFrontier(
                max_size=max_priority_size, minimize=True
            )
        elif frontier_type == FrontierType.BEAM:
            self._beam_frontier = BeamFrontier(
                beam_width=beam_width, scorer=scorer
            )
        else:  # pragma: no cover
            raise ValueError(f"Unknown FrontierType: {frontier_type!r}")

        # Membership tracking for FIFO and LIFO (and redundant coverage for
        # PRIORITY/BEAM to avoid cross-type inconsistency).
        self._seen: Set[str] = set()

        # Cumulative statistics.
        self._total_pushed: int = 0
        self._total_popped: int = 0
        self._max_size_seen: int = 0
        self._duplicate_pushes: int = 0
        self._empty_pops: int = 0
        self._stagnation_rounds: int = 0
        self._priority_sum: float = 0.0  # running sum for avg_priority
        self._priority_count: int = 0    # number of states with tracked priority

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _state_id(self, state: Any) -> str:
        """Extract a string identifier from *state*.

        Args:
            state: Any state object.

        Returns:
            String identifier.
        """
        if hasattr(state, "state_id"):
            return str(state.state_id)
        if hasattr(state, "id"):
            return str(state.id)
        return str(state)

    def _current_raw_size(self) -> int:
        """Return the actual size of the backing data structure.

        Args:
            (none)

        Returns:
            Non-negative integer.
        """
        if self.frontier_type == FrontierType.FIFO:
            return len(self._fifo)
        if self.frontier_type == FrontierType.LIFO:
            return len(self._lifo)
        if self.frontier_type == FrontierType.PRIORITY:
            return len(self._priority)
        if self.frontier_type == FrontierType.BEAM:
            return len(self._beam_frontier)
        return 0  # unreachable

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def push(self, state: Any, priority: float = 0.0) -> None:
        """Push *state* onto the frontier.

        Duplicate states (same ``state_id``) are silently discarded;
        ``duplicate_pushes`` is incremented for each such attempt.  On a
        successful push, statistics are updated and the max-size high-water
        mark is refreshed.

        Args:
            state: The state to push.  Should expose a ``state_id`` attribute.
            priority: Numeric priority used by PRIORITY and (indirectly) BEAM
                frontiers.  Ignored for FIFO and LIFO.

        Returns:
            ``None``
        """
        sid = self._state_id(state)

        # Deduplicate at coordinator level.
        if sid in self._seen:
            self._duplicate_pushes += 1
            self._stagnation_rounds += 1
            logger.debug("FrontierCoordinator: duplicate state %s rejected", sid)
            return

        # Route to backing structure.
        if self.frontier_type == FrontierType.FIFO:
            self._fifo.append(state)
            self._seen.add(sid)
        elif self.frontier_type == FrontierType.LIFO:
            self._lifo.append(state)
            self._seen.add(sid)
        elif self.frontier_type == FrontierType.PRIORITY:
            pushed = self._priority.push(state, priority)
            if not pushed:
                # The bounded heap evicted this state immediately.
                self._duplicate_pushes += 1
                self._stagnation_rounds += 1
                return
            self._seen.add(sid)
            self._priority_sum += priority
            self._priority_count += 1
        elif self.frontier_type == FrontierType.BEAM:
            self._beam_frontier.push(state)
            self._seen.add(sid)

        # Update counters on a successful push.
        self._total_pushed += 1
        self._stagnation_rounds = 0  # reset stagnation on new state
        current_size = self._current_raw_size()
        if current_size > self._max_size_seen:
            self._max_size_seen = current_size
        logger.debug(
            "FrontierCoordinator: pushed state %s (size now %d)", sid, current_size
        )

    def pop(self) -> Optional[Any]:
        """Remove and return the next state according to the traversal strategy.

        The semantics depend on the frontier type:
        - FIFO: oldest pushed state
        - LIFO: most recently pushed state
        - PRIORITY: state with lowest priority value
        - BEAM: best-scored state

        Args:
            (none)

        Returns:
            The next state, or ``None`` if the frontier is empty.
        """
        state: Optional[Any] = None

        if self.frontier_type == FrontierType.FIFO:
            if self._fifo:
                state = self._fifo.popleft()
            else:
                self._empty_pops += 1
                return None
        elif self.frontier_type == FrontierType.LIFO:
            if self._lifo:
                state = self._lifo.pop()
            else:
                self._empty_pops += 1
                return None
        elif self.frontier_type == FrontierType.PRIORITY:
            state = self._priority.pop()
            if state is None:
                self._empty_pops += 1
                return None
        elif self.frontier_type == FrontierType.BEAM:
            state = self._beam_frontier.pop()
            if state is None:
                self._empty_pops += 1
                return None

        if state is not None:
            sid = self._state_id(state)
            self._seen.discard(sid)
            self._total_popped += 1
            logger.debug(
                "FrontierCoordinator: popped state %s (size now %d)",
                sid,
                self._current_raw_size(),
            )

        return state

    def peek(self) -> Optional[Any]:
        """Return the next state without removing it from the frontier.

        Args:
            (none)

        Returns:
            The state that would be returned by the next :meth:`pop`, or
            ``None`` if the frontier is empty.
        """
        if self.frontier_type == FrontierType.FIFO:
            return self._fifo[0] if self._fifo else None
        if self.frontier_type == FrontierType.LIFO:
            return self._lifo[-1] if self._lifo else None
        if self.frontier_type == FrontierType.PRIORITY:
            return self._priority.peek()
        if self.frontier_type == FrontierType.BEAM:
            return self._beam_frontier.peek()
        return None  # unreachable

    def is_empty(self) -> bool:
        """Return ``True`` if the frontier contains no states.

        Args:
            (none)

        Returns:
            Boolean.
        """
        return self._current_raw_size() == 0

    def size(self) -> int:
        """Return the number of states currently on the frontier.

        Args:
            (none)

        Returns:
            Non-negative integer.
        """
        return self._current_raw_size()

    def contains(self, state_id: str) -> bool:
        """Test frontier membership by state_id.

        Args:
            state_id: The identifier to look up.

        Returns:
            ``True`` if a state with that id is currently on the frontier.
        """
        return state_id in self._seen

    def clear(self) -> None:
        """Remove all states and reset all statistics.

        This is useful when restarting a search without constructing a new
        coordinator (e.g., in iterative deepening).

        Args:
            (none)

        Returns:
            ``None``
        """
        if self.frontier_type == FrontierType.FIFO:
            self._fifo.clear()
        elif self.frontier_type == FrontierType.LIFO:
            self._lifo.clear()
        elif self.frontier_type == FrontierType.PRIORITY:
            self._priority.clear()
        elif self.frontier_type == FrontierType.BEAM:
            self._beam_frontier._beam.clear()

        self._seen.clear()
        self._total_pushed = 0
        self._total_popped = 0
        self._max_size_seen = 0
        self._duplicate_pushes = 0
        self._empty_pops = 0
        self._stagnation_rounds = 0
        self._priority_sum = 0.0
        self._priority_count = 0
        logger.debug("FrontierCoordinator: cleared all state and counters")

    def get_stats(self) -> FrontierStats:
        """Return a :class:`FrontierStats` snapshot of the current state.

        The snapshot is a point-in-time copy; modifying the coordinator
        afterwards does not affect previously returned snapshots.

        Args:
            (none)

        Returns:
            A :class:`FrontierStats` instance reflecting the coordinator's
            current statistics.
        """
        avg_priority = (
            self._priority_sum / self._priority_count
            if self._priority_count > 0
            else 0.0
        )
        return FrontierStats(
            frontier_type=self.frontier_type,
            current_size=self._current_raw_size(),
            total_pushed=self._total_pushed,
            total_popped=self._total_popped,
            max_size_seen=self._max_size_seen,
            duplicate_pushes=self._duplicate_pushes,
            empty_pops=self._empty_pops,
            avg_priority=avg_priority,
            stagnation_rounds=self._stagnation_rounds,
        )


# ---------------------------------------------------------------------------
# FrontierAnalyzer — analytics over collections of frontier states
# ---------------------------------------------------------------------------

class FrontierAnalyzer:
    """Analytical utilities for evaluating frontier quality.

    :class:`FrontierAnalyzer` operates on *snapshots* (plain Python lists of
    states) rather than live frontiers; this keeps it decoupled from any
    specific coordinator and makes it easy to test in isolation.

    All methods are stateless and may be called in any order.  No internal
    mutable state is maintained between calls.
    """

    # ------------------------------------------------------------------
    # Diversity
    # ------------------------------------------------------------------

    def compute_diversity(self, states: List[Any]) -> float:
        """Compute the mean pairwise Jaccard diversity of patch assignments.

        For each pair of states (i, j), the Jaccard *distance* between their
        ``patch_assignments`` dicts is::

            J_dist(i, j) = 1 - |A_i ∩ A_j| / |A_i ∪ A_j|

        where the "intersection" counts keys that appear in *both* dicts with
        the *same* value, and the "union" counts all keys that appear in
        either dict.  The final diversity score is the mean of all pairwise
        distances.

        A score of ``1.0`` means every pair of states disagrees completely on
        all patch assignments; ``0.0`` means all states are identical.

        Args:
            states: List of states.  Each state should expose a
                ``patch_assignments`` attribute (dict of patch_id → label).
                States without the attribute are treated as having empty
                assignments.

        Returns:
            Float in ``[0.0, 1.0]``.  Returns ``0.0`` if fewer than two
            states are provided (no pairs to compare).

        Example::

            analyzer = FrontierAnalyzer()
            score = analyzer.compute_diversity(coordinator_snapshot)
        """
        if len(states) < 2:
            return 0.0

        def _assignments(state: Any) -> Dict[str, str]:
            if hasattr(state, "patch_assignments"):
                return dict(state.patch_assignments)
            return {}

        total_distance = 0.0
        pair_count = 0

        for i in range(len(states)):
            a = _assignments(states[i])
            for j in range(i + 1, len(states)):
                b = _assignments(states[j])

                # Build union of keys.
                all_keys = set(a.keys()) | set(b.keys())
                if not all_keys:
                    # Both assignments empty → identical → distance = 0.
                    pair_count += 1
                    continue

                # Count keys where both dicts agree on the same value.
                intersection_size = sum(
                    1
                    for k in all_keys
                    if k in a and k in b and a[k] == b[k]
                )
                union_size = len(all_keys)
                jaccard_similarity = intersection_size / union_size
                jaccard_distance = 1.0 - jaccard_similarity
                total_distance += jaccard_distance
                pair_count += 1

        if pair_count == 0:
            return 0.0
        return total_distance / pair_count

    # ------------------------------------------------------------------
    # Best candidate selection
    # ------------------------------------------------------------------

    def find_best_candidate(
        self,
        states: List[Any],
        scorer: Callable,
    ) -> Optional[Any]:
        """Return the state minimising *scorer*.

        Args:
            states: Candidate states.  May be empty.
            scorer: Callable ``scorer(state) -> float``.  The state with the
                *lowest* score is returned.

        Returns:
            The best-scoring state, or ``None`` if *states* is empty.

        Raises:
            TypeError: If *scorer* raises a ``TypeError`` on any state; this
                propagates to the caller unchanged.

        Example::

            best = analyzer.find_best_candidate(frontier_snapshot, lambda s: -s.compute_coverage_fraction())
        """
        if not states:
            return None
        return min(states, key=scorer)

    # ------------------------------------------------------------------
    # Remaining-work estimate
    # ------------------------------------------------------------------

    def estimate_remaining_work(self, states: List[Any]) -> float:
        """Estimate how much work remains, averaged over the frontier.

        The heuristic is simply the mean of ``(1 - coverage_fraction)``
        across all states.  A value near ``0.0`` means most frontier states
        are almost fully covered; a value near ``1.0`` means most states have
        barely started.

        States without a ``compute_coverage_fraction`` method contribute
        ``1.0`` (i.e., assumed fully uncovered).

        Args:
            states: Current frontier snapshot.

        Returns:
            Float in ``[0.0, 1.0]``.  Returns ``1.0`` if *states* is empty
            (no evidence of progress).

        Example::

            remaining = analyzer.estimate_remaining_work(frontier_states)
            if remaining < 0.1:
                print("Search is close to termination")
        """
        if not states:
            return 1.0

        total_remaining = 0.0
        for state in states:
            if hasattr(state, "compute_coverage_fraction"):
                coverage = state.compute_coverage_fraction()
                # Clamp to [0, 1] in case of unusual float values.
                coverage = max(0.0, min(1.0, float(coverage)))
                total_remaining += 1.0 - coverage
            else:
                # No coverage data → assume fully uncovered.
                total_remaining += 1.0

        return total_remaining / len(states)

    # ------------------------------------------------------------------
    # Stagnation detection
    # ------------------------------------------------------------------

    def detect_stagnation(self, history: List[int]) -> bool:
        """Detect whether the frontier has stopped growing.

        Stagnation is defined as the last 5 or more entries of *history*
        all being equal (no growth or shrinkage in frontier size).

        Args:
            history: Time-ordered list of frontier size measurements.  Each
                element is an integer representing the frontier size at one
                generation round.

        Returns:
            ``True`` if the frontier is stagnant (last ≥ 5 entries all
            identical), ``False`` otherwise.  Also returns ``False`` if
            ``len(history) < 5``.

        Example::

            history = [3, 4, 5, 5, 5, 5, 5]
            assert analyzer.detect_stagnation(history) is True

            history = [1, 2, 3, 4, 5]
            assert analyzer.detect_stagnation(history) is False
        """
        if len(history) < 5:
            return False

        # Check whether the last 5 entries are all the same value.
        tail = history[-5:]
        reference = tail[0]
        return all(v == reference for v in tail)


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

def _run_smoke_test() -> None:
    """Exercise all major classes with a sequence of push/pop operations.

    This function is intentionally self-contained: it constructs lightweight
    mock state objects so it can run without the real models module.  It is
    called at module import time when the module is executed directly
    (``python -m jugeo.generation.state_space.frontier_management``).

    The test covers:

    1. :class:`BoundedPriorityFrontier` — push/pop/peek with eviction.
    2. :class:`BeamFrontier` — push/pop/trim with a custom scorer.
    3. :class:`FrontierCoordinator` in all four modes — FIFO, LIFO, PRIORITY,
       BEAM — with duplicate detection, stats, and witness capture.
    4. :class:`FrontierAnalyzer` — diversity, best-candidate, remaining-work,
       and stagnation detection.
    """
    import sys

    # ------------------------------------------------------------------
    # Mock state
    # ------------------------------------------------------------------

    @dataclass
    class MockState:
        """Minimal stand-in for SemanticState used in the smoke test."""

        state_id: str
        patch_assignments: Dict[str, str] = field(default_factory=dict)
        coverage: float = 0.0

        def compute_coverage_fraction(self) -> float:
            return self.coverage

    def make_states(n: int) -> List[MockState]:
        """Return *n* distinct MockState objects with varied assignments."""
        states = []
        for i in range(n):
            assignments = {f"patch_{j}": ("A" if (i + j) % 2 == 0 else "B") for j in range(4)}
            states.append(
                MockState(
                    state_id=f"s{i:03d}",
                    patch_assignments=assignments,
                    coverage=round(i / max(n - 1, 1), 3),
                )
            )
        return states

    states = make_states(12)
    print("=== BoundedPriorityFrontier smoke test ===")
    bpf = BoundedPriorityFrontier(max_size=5, minimize=True)
    for i, s in enumerate(states[:8]):
        pushed = bpf.push(s, priority=float(i))
        print(f"  push({s.state_id}, priority={i}) → {pushed}, size={len(bpf)}")
    # Push a duplicate — should be rejected.
    dup_result = bpf.push(states[0], priority=99.0)
    print(f"  duplicate push(s000) → {dup_result}")
    print(f"  peek → {bpf.peek().state_id if bpf.peek() else None}")
    while len(bpf) > 0:
        popped = bpf.pop()
        print(f"  pop → {popped.state_id if popped else None}")
    empty_pop = bpf.pop()
    print(f"  pop (empty) → {empty_pop}")

    print()
    print("=== BeamFrontier smoke test ===")
    bf = BeamFrontier(beam_width=3, scorer=lambda s: -s.coverage)
    for s in states[:6]:
        bf.push(s)
        print(f"  push({s.state_id}, coverage={s.coverage}), beam={[x.state_id for x in bf.get_beam()]}")
    print(f"  peek → {bf.peek().state_id if bf.peek() else None}")
    popped = bf.pop()
    print(f"  pop (best) → {popped.state_id if popped else None}")

    print()
    print("=== FrontierCoordinator (all modes) smoke test ===")
    for ftype in FrontierType:
        coordinator = FrontierCoordinator(
            frontier_type=ftype,
            beam_width=4,
            max_priority_size=20,
        )
        batch = states[:5]
        for i, s in enumerate(batch):
            coordinator.push(s, priority=float(i))
        # Duplicate push.
        coordinator.push(batch[0], priority=0.0)
        stats = coordinator.get_stats()
        witness = FrontierWitness.capture(coordinator)
        print(
            f"  [{ftype.name}] size={coordinator.size()}, "
            f"pushed={stats.total_pushed}, dups={stats.duplicate_pushes}, "
            f"witness_id={witness.witness_id[:8]}…"
        )
        # Drain all states.
        popped_ids = []
        while not coordinator.is_empty():
            s = coordinator.pop()
            if s is not None:
                popped_ids.append(s.state_id)
        empty = coordinator.pop()
        final_stats = coordinator.get_stats()
        print(
            f"    drained={popped_ids}, empty_pop={final_stats.empty_pops}"
        )

    print()
    print("=== FrontierAnalyzer smoke test ===")
    analyzer = FrontierAnalyzer()

    diversity = analyzer.compute_diversity(states)
    print(f"  diversity({len(states)} states) = {diversity:.4f}")

    best = analyzer.find_best_candidate(states, scorer=lambda s: -s.coverage)
    print(f"  best_candidate = {best.state_id if best else None} (coverage={best.coverage if best else None})")

    remaining = analyzer.estimate_remaining_work(states[:6])
    print(f"  remaining_work(first 6) = {remaining:.4f}")

    stagnant_history = [5, 5, 5, 5, 5]
    growing_history = [1, 2, 3, 4, 5]
    short_history = [5, 5]
    print(f"  detect_stagnation({stagnant_history}) = {analyzer.detect_stagnation(stagnant_history)}")
    print(f"  detect_stagnation({growing_history}) = {analyzer.detect_stagnation(growing_history)}")
    print(f"  detect_stagnation({short_history}) = {analyzer.detect_stagnation(short_history)}")

    print()
    print("Smoke test passed.")
    sys.stdout.flush()


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    _run_smoke_test()
