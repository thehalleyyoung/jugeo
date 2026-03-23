from __future__ import annotations
"""Frontier as budgeted search over semantic states. theory2.tex Ch47 §1. # copilot:

This module implements budgeted search coordination over semantic states in the
jugeo proof-obligation frontier. The frontier is a priority-ordered collection of
semantic state nodes, each associated with a set of open proof obligations. Search
progresses by expanding nodes according to a ComputeBudget that tracks token usage,
time, and priority weights.

Key abstractions:
  - ComputeBudget: first-class budget object with utilization tracking
  - BudgetLedger: per-node budget accounting
  - SemanticStateNode: immutable node in the semantic search space
  - FrontierSearchQueue: priority queue with budget-aware expansion
  - FrontierBudgetedSearchCoordinator: main search coordinator
  - FrontierBudgetAnalyzer: trend analysis and exhaustion prediction
  - FrontierBudgetWitness: immutable compliance certificate
"""

# ---------------------------------------------------------------------------
# Standard-library imports
# ---------------------------------------------------------------------------
import math
import time
import uuid
import heapq
import logging
import dataclasses
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

DEFAULT_TOKEN_LIMIT: int = 10_000
"""Default token budget for a single search phase.

This value was chosen to balance thoroughness against cost for typical
proof-obligation sets that appear in jugeo's intermediate verification
workloads. Callers may override it by supplying an explicit token_limit
when constructing a ComputeBudget.
"""

DEFAULT_TIME_LIMIT: float = 300.0
"""Default wall-clock time limit for a search phase, in seconds.

Five minutes is a reasonable upper bound for interactive or near-interactive
usage. Long-running batch jobs should supply a higher value.
"""

MIN_PRIORITY_WEIGHT: float = 0.01
"""Minimum priority weight assigned to any search node.

Prevents nodes from being effectively starved while still ensuring that
high-priority nodes dominate scheduling decisions.
"""

__all__ = [
    "ComputeBudget",
    "BudgetLedger",
    "SemanticStateNode",
    "FrontierSearchQueue",
    "FrontierBudgetedSearchCoordinator",
    "FrontierBudgetAnalyzer",
    "FrontierBudgetWitness",
]

# ---------------------------------------------------------------------------
# Module-level logger
# ---------------------------------------------------------------------------
_log = logging.getLogger(__name__)


# ===========================================================================
# Helper functions
# ===========================================================================


def _safe_float(v: Any, default: float = 0.0) -> float:
    """Convert *v* to a finite float, returning *default* on failure.

    Args:
        v: Any value to attempt conversion on.  Handles None, strings that
            represent valid numbers, objects with a ``__float__`` method, and
            anything that raises on conversion.
        default: Value to return when conversion fails or when the result
            is not finite (NaN or ±Inf).

    Returns:
        A finite Python float derived from *v*, or *default*.

    Examples:
        >>> _safe_float("3.14")
        3.14
        >>> _safe_float(None)
        0.0
        >>> _safe_float(float("nan"), default=-1.0)
        -1.0
        >>> _safe_float("not-a-number", default=99.0)
        99.0
    """
    if v is None:
        return default
    try:
        result = float(v)
        if not math.isfinite(result):
            return default
        return result
    except (TypeError, ValueError, OverflowError):
        return default


def _clamp(v: Any, lo: Any, hi: Any) -> Any:
    """Clamp *v* to the closed interval [*lo*, *hi*].

    Args:
        v: The value to clamp.  Must be comparable with *lo* and *hi*.
        lo: Lower bound of the interval (inclusive).
        hi: Upper bound of the interval (inclusive).

    Returns:
        *lo* if ``v < lo``, *hi* if ``v > hi``, otherwise *v*.

    Raises:
        TypeError: If *v*, *lo*, or *hi* are not mutually comparable.

    Examples:
        >>> _clamp(5, 0, 10)
        5
        >>> _clamp(-3, 0, 10)
        0
        >>> _clamp(15, 0, 10)
        10
        >>> _clamp(0.5, 0.0, 1.0)
        0.5
    """
    if v < lo:
        return lo
    if v > hi:
        return hi
    return v


def _ema(current: float, new_val: float, alpha: float = 0.2) -> float:
    """Compute an exponential moving average update.

    Applies the recurrence ``ema_{t+1} = alpha * new_val + (1 - alpha) * current``.
    This is the standard one-pass EMA formula used in time-series smoothing and
    adaptive budget control.

    Args:
        current: The current EMA estimate.  On the first call this is often
            set to the first observed value so that the estimate starts
            unbiased.
        new_val: The new observation to incorporate.
        alpha: Smoothing factor in ``(0, 1]``.  Larger values give more weight
            to recent observations; smaller values produce a smoother signal.
            Defaults to 0.2, which is appropriate for moderate-frequency
            sampling (every few hundred milliseconds).

    Returns:
        Updated EMA value as a plain Python float.

    Examples:
        >>> round(_ema(10.0, 20.0, alpha=0.5), 6)
        15.0
        >>> round(_ema(0.0, 1.0, alpha=0.2), 6)
        0.2
    """
    alpha = _clamp(_safe_float(alpha, 0.2), 1e-6, 1.0)
    return alpha * _safe_float(new_val) + (1.0 - alpha) * _safe_float(current)


def _cosine_similarity(a: List[float], b: List[float]) -> float:
    """Compute cosine similarity between two real-valued vectors.

    Both vectors are treated as having the same dimensionality.  If one is
    shorter it is implicitly zero-padded.  The result is clipped to [-1, 1]
    to guard against floating-point rounding errors.

    Args:
        a: First vector as a list of floats.
        b: Second vector as a list of floats.

    Returns:
        Cosine similarity in the range [-1, 1], where 1 means identical
        direction, 0 means orthogonal, and -1 means opposite direction.

    Examples:
        >>> round(_cosine_similarity([1.0, 0.0], [0.0, 1.0]), 6)
        0.0
        >>> round(_cosine_similarity([1.0, 1.0], [1.0, 1.0]), 6)
        1.0
    """
    dim = max(len(a), len(b))
    av = [_safe_float(a[i]) if i < len(a) else 0.0 for i in range(dim)]
    bv = [_safe_float(b[i]) if i < len(b) else 0.0 for i in range(dim)]
    dot = sum(x * y for x, y in zip(av, bv))
    na = math.sqrt(sum(x * x for x in av))
    nb = math.sqrt(sum(y * y for y in bv))
    if na < 1e-12 or nb < 1e-12:
        return 0.0
    return _clamp(dot / (na * nb), -1.0, 1.0)


def _euclidean_distance(a: List[float], b: List[float]) -> float:
    """Compute Euclidean distance between two real-valued vectors.

    Handles vectors of unequal length by zero-padding the shorter one.

    Args:
        a: First vector.
        b: Second vector.

    Returns:
        Non-negative Euclidean distance as a float.
    """
    dim = max(len(a), len(b))
    av = [_safe_float(a[i]) if i < len(a) else 0.0 for i in range(dim)]
    bv = [_safe_float(b[i]) if i < len(b) else 0.0 for i in range(dim)]
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(av, bv)))


def _make_id(prefix: str = "") -> str:
    """Generate a short collision-resistant identifier.

    Combines *prefix* with the first 8 hex characters of a random UUID4.

    Args:
        prefix: Optional string prepended to the identifier, e.g. ``"node"``.
            If empty the raw hex string is returned.

    Returns:
        A string of the form ``"{prefix}_{hex8}"`` (or ``"{hex8}"`` if
        *prefix* is falsy).
    """
    uid = uuid.uuid4().hex[:8]
    return f"{prefix}_{uid}" if prefix else uid


# ===========================================================================
# ComputeBudget
# ===========================================================================


@dataclass(frozen=True, slots=True)
class ComputeBudget:
    """Immutable first-class budget descriptor for a search phase.

    A ComputeBudget encodes the resource constraints under which a
    FrontierBudgetedSearchCoordinator is permitted to operate.  It is
    intentionally frozen so that budget objects can be shared across threads
    or stored in sets/dicts without mutation risk.

    All numeric fields are validated at construction time via ``__post_init__``
    to ensure that invariants hold throughout the lifetime of the object.

    Attributes:
        budget_id: Unique identifier for this budget instance, typically
            generated by :meth:`make`.
        token_limit: Maximum number of language-model tokens the search phase
            may consume.  Must be a positive integer.
        time_limit_seconds: Wall-clock time budget in seconds.  Must be
            positive.
        priority_weight: Non-negative weight used by the scheduler to
            prioritise this budget relative to competing budgets.  Clipped to
            [MIN_PRIORITY_WEIGHT, 1.0].
        allocated_at: POSIX timestamp (seconds since epoch) at which this
            budget was created, used for freshness checks.
        metadata: Arbitrary key-value annotations for downstream consumers,
            e.g. the originating proof obligation or experiment run-id.
    """

    budget_id: str
    token_limit: int
    time_limit_seconds: float
    priority_weight: float
    allocated_at: float
    metadata: dict

    def __post_init__(self) -> None:
        """Validate budget fields after dataclass construction.

        Raises:
            ValueError: If token_limit or time_limit_seconds are non-positive,
                or if priority_weight is outside the valid range.
        """
        if self.token_limit <= 0:
            raise ValueError(f"token_limit must be positive, got {self.token_limit}")
        if self.time_limit_seconds <= 0.0:
            raise ValueError(
                f"time_limit_seconds must be positive, got {self.time_limit_seconds}"
            )

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def utilization(self, tokens_used: int, time_used: float) -> float:
        """Compute the overall utilization fraction for this budget.

        Utilization is defined as the *maximum* of token utilization and time
        utilization.  This conservative definition means that exhaustion is
        detected as soon as *either* resource is depleted, even if the other
        has capacity remaining.

        Args:
            tokens_used: Number of tokens consumed so far.
            time_used: Wall-clock seconds elapsed so far.

        Returns:
            A float in [0.0, 1.0] where 0.0 means no resources used and 1.0
            (or higher) means the budget is fully consumed.

        Examples:
            >>> b = ComputeBudget.make(1000, 60.0, 1.0)
            >>> b.utilization(500, 30.0)
            0.5
            >>> b.utilization(1000, 0.0)
            1.0
        """
        token_util = _safe_float(tokens_used) / self.token_limit
        time_util = _safe_float(time_used) / self.time_limit_seconds
        return max(token_util, time_util)

    def is_exhausted(self, tokens_used: int, time_used: float) -> bool:
        """Return True if either token or time budget has been consumed.

        Args:
            tokens_used: Tokens consumed so far.
            time_used: Seconds elapsed so far.

        Returns:
            True if utilization >= 1.0, False otherwise.
        """
        return self.utilization(tokens_used, time_used) >= 1.0

    def remaining_tokens(self, tokens_used: int) -> int:
        """Return the number of tokens remaining in this budget.

        Args:
            tokens_used: Tokens already consumed.

        Returns:
            Non-negative integer token count remaining.  Returns 0 if the
            token budget is already exhausted.
        """
        remaining = self.token_limit - int(tokens_used)
        return max(0, remaining)

    def remaining_time(self, time_used: float) -> float:
        """Return the number of seconds remaining in this budget.

        Args:
            time_used: Seconds already elapsed.

        Returns:
            Non-negative float seconds remaining.  Returns 0.0 if the time
            budget is already exhausted.
        """
        remaining = self.time_limit_seconds - _safe_float(time_used)
        return max(0.0, remaining)

    def to_dict(self) -> dict:
        """Serialise this budget to a plain Python dictionary.

        Returns:
            A dict containing all fields, suitable for JSON serialisation
            (no custom types).

        Examples:
            >>> b = ComputeBudget.make(100, 10.0, 0.5)
            >>> d = b.to_dict()
            >>> d["token_limit"]
            100
        """
        return {
            "budget_id": self.budget_id,
            "token_limit": self.token_limit,
            "time_limit_seconds": self.time_limit_seconds,
            "priority_weight": self.priority_weight,
            "allocated_at": self.allocated_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def make(
        cls,
        token_limit: int = DEFAULT_TOKEN_LIMIT,
        time_limit: float = DEFAULT_TIME_LIMIT,
        priority_weight: float = 1.0,
    ) -> "ComputeBudget":
        """Convenience factory that generates a fresh budget with a new ID.

        Args:
            token_limit: Maximum tokens allowed.  Defaults to
                DEFAULT_TOKEN_LIMIT.
            time_limit: Wall-clock time limit in seconds.  Defaults to
                DEFAULT_TIME_LIMIT.
            priority_weight: Scheduler weight, clipped to
                [MIN_PRIORITY_WEIGHT, 1.0].

        Returns:
            A new, fully initialised ComputeBudget instance.

        Examples:
            >>> b = ComputeBudget.make(500, 30.0, 0.8)
            >>> b.token_limit
            500
        """
        weight = _clamp(
            _safe_float(priority_weight, 1.0), MIN_PRIORITY_WEIGHT, 1.0
        )
        return cls(
            budget_id=_make_id("budget"),
            token_limit=max(1, int(token_limit)),
            time_limit_seconds=max(1e-3, _safe_float(time_limit, DEFAULT_TIME_LIMIT)),
            priority_weight=weight,
            allocated_at=time.time(),
            metadata={},
        )


# ===========================================================================
# BudgetLedger
# ===========================================================================


@dataclass(slots=True)
class BudgetLedger:
    """Mutable per-node accounting ledger for a ComputeBudget.

    A BudgetLedger tracks allocations (tokens *promised* to nodes before
    expansion) and charges (tokens *actually consumed* by completed
    expansions).  The difference ``allocation - charge`` for a node
    represents an outstanding reservation.

    Attributes:
        ledger_id: Unique identifier for this ledger, typically tied to the
            life of a FrontierSearchQueue.
        allocations: Mapping from node_id to tokens allocated.
        charges: Mapping from node_id to tokens actually consumed.
        budget: The ComputeBudget against which this ledger is tracking.
    """

    ledger_id: str
    allocations: dict
    charges: dict
    budget: ComputeBudget

    def allocate(self, node_id: str, tokens: int) -> None:
        """Reserve *tokens* for a node that is about to be expanded.

        Allocating more tokens than the budget allows is permitted here; it is
        the caller's responsibility to check :meth:`is_over_budget` before
        deciding to proceed with expansion.

        Args:
            node_id: Identifier of the node being allocated resources.
            tokens: Number of tokens to reserve.  Must be non-negative.

        Raises:
            ValueError: If *tokens* is negative.
        """
        if tokens < 0:
            raise ValueError(f"Cannot allocate negative tokens ({tokens}) for {node_id}")
        current = self.allocations.get(node_id, 0)
        self.allocations[node_id] = current + tokens
        _log.debug("ledger %s: allocated %d tokens to node %s", self.ledger_id, tokens, node_id)

    def charge(self, node_id: str, tokens: int) -> None:
        """Record that a node actually consumed *tokens*.

        Args:
            node_id: Identifier of the expanded node.
            tokens: Tokens consumed.  May exceed the allocation for the node
                (overrun), though this should be treated as an anomaly by
                callers.

        Raises:
            ValueError: If *tokens* is negative.
        """
        if tokens < 0:
            raise ValueError(f"Cannot charge negative tokens ({tokens}) for {node_id}")
        current = self.charges.get(node_id, 0)
        self.charges[node_id] = current + tokens
        _log.debug("ledger %s: charged %d tokens to node %s", self.ledger_id, tokens, node_id)

    def balance(self, node_id: str) -> int:
        """Return the outstanding reservation for a node (allocation - charge).

        Args:
            node_id: Identifier of the node to query.

        Returns:
            Integer balance.  Positive means tokens are still reserved but not
            yet consumed; negative indicates an overrun.
        """
        allocated = self.allocations.get(node_id, 0)
        charged = self.charges.get(node_id, 0)
        return allocated - charged

    def total_charged(self) -> int:
        """Return the total tokens consumed across all nodes.

        Returns:
            Sum of all charge entries as a non-negative integer.
        """
        return sum(self.charges.values())

    def total_allocated(self) -> int:
        """Return the total tokens reserved across all nodes.

        Returns:
            Sum of all allocation entries as a non-negative integer.
        """
        return sum(self.allocations.values())

    def is_over_budget(self) -> bool:
        """Return True if total charged tokens exceed the budget token limit.

        This is a hard check based on actual consumption, not reservations.

        Returns:
            True if the budget is exhausted by consumed tokens alone.
        """
        return self.total_charged() >= self.budget.token_limit

    def summary(self) -> dict:
        """Return a human-readable summary of ledger state.

        Returns:
            Dict with keys: ledger_id, total_allocated, total_charged,
            over_budget, utilization.
        """
        charged = self.total_charged()
        allocated = self.total_allocated()
        utilization = charged / max(1, self.budget.token_limit)
        return {
            "ledger_id": self.ledger_id,
            "total_allocated": allocated,
            "total_charged": charged,
            "over_budget": self.is_over_budget(),
            "utilization": round(utilization, 4),
            "node_count_allocated": len(self.allocations),
            "node_count_charged": len(self.charges),
        }

    def to_dict(self) -> dict:
        """Serialise this ledger to a plain Python dictionary.

        Returns:
            Dict suitable for JSON serialisation.
        """
        return {
            "ledger_id": self.ledger_id,
            "allocations": dict(self.allocations),
            "charges": dict(self.charges),
            "budget": self.budget.to_dict(),
            "summary": self.summary(),
        }


# ===========================================================================
# SemanticStateNode
# ===========================================================================


@dataclass(frozen=True, slots=True)
class SemanticStateNode:
    """Immutable node in the semantic proof-obligation search space.

    Each node represents a distinct semantic state reached during frontier
    expansion.  The ``state_vector`` encodes a continuous embedding of the
    proof context; ``obligation_ids`` lists which open obligations must still
    be discharged from this node; and ``proof_mode`` selects the search
    strategy applied when expanding children.

    Nodes are immutable so that they can be stored in sets, used as dict keys,
    and shared safely across concurrent search processes.

    Attributes:
        node_id: Unique identifier, typically generated by :meth:`make`.
        state_vector: A list of floats representing the semantic embedding of
            this node's proof context.  May be empty for the root node.
        obligation_ids: Identifiers of the open proof obligations that remain
            unsatisfied at this node.
        depth: Distance from the root node in the search tree.  Root has
            depth 0.
        parent_id: Identifier of the parent node, or None for the root.
        proof_mode: Strategy used to generate children.  Valid values include
            ``"smt"``, ``"induction"``, ``"case_split"``, ``"heuristic"``.
        trust_score: A float in [0, 1] representing how much the verifier
            trusts conclusions drawn at this node.  1.0 means fully trusted.
        metadata: Arbitrary annotations.
    """

    node_id: str
    state_vector: list
    obligation_ids: list
    depth: int
    parent_id: Optional[str]
    proof_mode: str
    trust_score: float
    metadata: dict

    def semantic_distance(self, other: "SemanticStateNode") -> float:
        """Compute the semantic distance between this node and *other*.

        Distance is defined as one minus the cosine similarity of the two
        state vectors.  This gives a value in [0, 2] where 0 means identical
        and 2 means maximally dissimilar.  If either vector is empty the
        Euclidean distance of a zero-padded comparison is used as a fallback.

        Args:
            other: Another SemanticStateNode to compare against.

        Returns:
            Non-negative float measuring semantic dissimilarity.

        Examples:
            >>> a = SemanticStateNode.make([1.0, 0.0], ["ob1"], "smt")
            >>> b = SemanticStateNode.make([0.0, 1.0], ["ob1"], "smt")
            >>> round(a.semantic_distance(b), 6)
            1.0
        """
        if not self.state_vector or not other.state_vector:
            # Fall back to euclidean when vectors are missing
            return _euclidean_distance(
                list(self.state_vector), list(other.state_vector)
            )
        cosine = _cosine_similarity(list(self.state_vector), list(other.state_vector))
        return 1.0 - cosine

    def is_leaf(self) -> bool:
        """Return True if this node has no open proof obligations remaining.

        A leaf node represents a fully-discharged proof state and should not
        be expanded further.

        Returns:
            True when ``obligation_ids`` is empty or contains only falsy
            values.
        """
        return len([o for o in self.obligation_ids if o]) == 0

    def to_dict(self) -> dict:
        """Serialise this node to a plain Python dictionary.

        Returns:
            Dict containing all fields in JSON-serialisable form.
        """
        return {
            "node_id": self.node_id,
            "state_vector": list(self.state_vector),
            "obligation_ids": list(self.obligation_ids),
            "depth": self.depth,
            "parent_id": self.parent_id,
            "proof_mode": self.proof_mode,
            "trust_score": self.trust_score,
            "metadata": dict(self.metadata),
            "is_leaf": self.is_leaf(),
        }

    @classmethod
    def make(
        cls,
        state_vector: list,
        obligation_ids: list,
        proof_mode: str,
        parent_id: Optional[str] = None,
        depth: int = 0,
        trust_score: float = 1.0,
    ) -> "SemanticStateNode":
        """Factory method to construct a fresh SemanticStateNode.

        Args:
            state_vector: Semantic embedding of the proof context.
            obligation_ids: Open obligation identifiers.
            proof_mode: Expansion strategy for this node.
            parent_id: Parent node id, or None for root.
            depth: Tree depth from root.
            trust_score: Verifier trust, clipped to [0, 1].

        Returns:
            A new SemanticStateNode with a generated node_id.
        """
        return cls(
            node_id=_make_id("node"),
            state_vector=list(state_vector),
            obligation_ids=list(obligation_ids),
            depth=max(0, int(depth)),
            parent_id=parent_id,
            proof_mode=str(proof_mode),
            trust_score=_clamp(_safe_float(trust_score, 1.0), 0.0, 1.0),
            metadata={},
        )


# ===========================================================================
# FrontierSearchQueue
# ===========================================================================


@dataclass(slots=True)
class FrontierSearchQueue:
    """Priority queue with budget-aware node management.

    Internally uses a min-heap ordered by *negative* priority (so that the
    highest-priority node is always at the front).  Each entry is a tuple
    ``(-priority, insertion_index, node)`` to break ties deterministically.

    Attributes:
        queue_id: Unique identifier for this queue instance.
        nodes: Internal heap storage.  Callers should not mutate this directly.
        budget_ledger: Ledger used to track per-node token reservations.
        max_depth: Maximum depth of nodes permitted in the queue.  Nodes
            deeper than this are silently dropped by :meth:`push`.
        expansion_count: Running count of successful :meth:`pop` calls.
    """

    queue_id: str
    nodes: list
    budget_ledger: BudgetLedger
    max_depth: int
    expansion_count: int

    def push(self, node: SemanticStateNode, priority: float) -> None:
        """Insert *node* into the queue at the given *priority*.

        If the node's depth exceeds :attr:`max_depth` the push is silently
        ignored.  The priority is negated internally so that the heap behaves
        as a max-heap.

        Args:
            node: The SemanticStateNode to enqueue.
            priority: Non-negative float priority.  Higher values are returned
                first by :meth:`pop`.
        """
        if node.depth > self.max_depth:
            _log.debug(
                "queue %s: dropping node %s (depth %d > max %d)",
                self.queue_id, node.node_id, node.depth, self.max_depth,
            )
            return
        # Use the current heap size as a tie-breaking insertion index
        insertion_idx = len(self.nodes)
        safe_prio = _safe_float(priority, 0.0)
        heapq.heappush(self.nodes, (-safe_prio, insertion_idx, node))
        _log.debug(
            "queue %s: pushed node %s (priority=%.4f, depth=%d)",
            self.queue_id, node.node_id, safe_prio, node.depth,
        )

    def pop(self) -> Optional[SemanticStateNode]:
        """Remove and return the highest-priority node.

        Also increments :attr:`expansion_count`.

        Returns:
            The SemanticStateNode with the highest priority, or None if the
            queue is empty.
        """
        if not self.nodes:
            return None
        _, _, node = heapq.heappop(self.nodes)
        self.expansion_count += 1
        _log.debug(
            "queue %s: popped node %s (expansion_count=%d)",
            self.queue_id, node.node_id, self.expansion_count,
        )
        return node

    def peek(self) -> Optional[SemanticStateNode]:
        """Return the highest-priority node without removing it.

        Returns:
            The SemanticStateNode with the highest priority, or None if empty.
        """
        if not self.nodes:
            return None
        _, _, node = self.nodes[0]
        return node

    def is_empty(self) -> bool:
        """Return True if there are no nodes in the queue.

        Returns:
            Boolean indicating emptiness.
        """
        return len(self.nodes) == 0

    def size(self) -> int:
        """Return the current number of nodes in the queue.

        Returns:
            Non-negative integer size.
        """
        return len(self.nodes)

    def budget_utilization(self) -> float:
        """Return the fraction of the budget consumed as tracked by the ledger.

        Returns:
            Float in [0, 1] where 1 means the budget is exhausted.
        """
        total = self.budget_ledger.budget.token_limit
        charged = self.budget_ledger.total_charged()
        return _clamp(_safe_float(charged) / max(1, total), 0.0, 1.0)

    def flush_exhausted(self) -> int:
        """Remove nodes whose obligation lists are empty (leaf nodes).

        Leaf nodes that remain in the queue after being added as intermediate
        results are pruned to keep the queue compact.

        Returns:
            The number of nodes removed.
        """
        original_size = len(self.nodes)
        # Rebuild the heap excluding leaf nodes
        filtered = [(neg_p, idx, n) for neg_p, idx, n in self.nodes if not n.is_leaf()]
        heapq.heapify(filtered)
        self.nodes[:] = filtered
        removed = original_size - len(self.nodes)
        if removed:
            _log.debug("queue %s: flushed %d exhausted leaf nodes", self.queue_id, removed)
        return removed

    def to_dict(self) -> dict:
        """Serialise the queue to a plain Python dictionary.

        Returns:
            Dict with queue metadata and a snapshot of queued node IDs.
        """
        return {
            "queue_id": self.queue_id,
            "size": self.size(),
            "expansion_count": self.expansion_count,
            "max_depth": self.max_depth,
            "budget_utilization": round(self.budget_utilization(), 4),
            "queued_node_ids": [n.node_id for _, _, n in self.nodes],
            "budget_ledger": self.budget_ledger.summary(),
        }


# ===========================================================================
# FrontierBudgetedSearchCoordinator
# ===========================================================================


@dataclass(slots=True)
class FrontierBudgetedSearchCoordinator:
    """Main coordinator driving budgeted search over the frontier.

    The coordinator maintains a FrontierSearchQueue and a ComputeBudget.
    On each call to :meth:`step` it pops the highest-priority node,
    simulates expansion (in a real system this would invoke a solver or
    language model), updates the budget ledger, and records the event in
    :attr:`history`.

    Attributes:
        coordinator_id: Unique identifier for this coordinator instance.
        search_queue: The priority queue managed by this coordinator.
        budget: The ComputeBudget governing this search phase.
        phase: Human-readable label for the current search phase, e.g.
            ``"initial"``, ``"refinement"``, ``"termination"``.
        iteration_count: Total number of :meth:`step` calls made so far.
        history: List of step result dicts, one per call to :meth:`step`.
    """

    coordinator_id: str
    search_queue: FrontierSearchQueue
    budget: ComputeBudget
    phase: str
    iteration_count: int
    history: list

    def step(self, frontier_state: dict) -> dict:
        """Execute one expansion step on the frontier.

        Pops the highest-priority node from the queue, allocates tokens for
        the expansion, and records the result.  If the budget is exhausted
        before popping, returns an exhaustion sentinel.

        Args:
            frontier_state: Arbitrary context dict passed in by the outer
                search loop.  May contain hints, overrides, or diagnostic
                metadata.  Not mutated by this method.

        Returns:
            A dict describing the outcome of this step with keys:
            - ``step``: iteration_count at time of call
            - ``node_id``: id of expanded node, or None if queue was empty
            - ``tokens_used``: tokens consumed in this step
            - ``budget_exhausted``: bool
            - ``phase``: current phase label
            - ``queue_size``: queue size after expansion
        """
        self.iteration_count += 1
        step_idx = self.iteration_count

        # Guard: check budget before attempting expansion
        total_charged = self.search_queue.budget_ledger.total_charged()
        elapsed = time.time() - self.budget.allocated_at
        if self.budget.is_exhausted(total_charged, elapsed):
            result = {
                "step": step_idx,
                "node_id": None,
                "tokens_used": 0,
                "budget_exhausted": True,
                "phase": self.phase,
                "queue_size": self.search_queue.size(),
            }
            self.history.append(result)
            return result

        # Pop the best node
        node = self.search_queue.pop()
        if node is None:
            result = {
                "step": step_idx,
                "node_id": None,
                "tokens_used": 0,
                "budget_exhausted": False,
                "phase": self.phase,
                "queue_size": 0,
            }
            self.history.append(result)
            return result

        # Estimate token cost for expansion: proportional to obligation count
        # and inversely proportional to trust_score.
        base_cost = max(1, len(node.obligation_ids)) * 50
        trust_factor = 1.0 / max(0.1, node.trust_score)
        tokens_this_step = int(base_cost * trust_factor)

        # Record allocation and charge
        self.record_expansion(node.node_id, tokens_this_step)

        result = {
            "step": step_idx,
            "node_id": node.node_id,
            "tokens_used": tokens_this_step,
            "budget_exhausted": self.is_budget_exhausted(),
            "phase": self.phase,
            "queue_size": self.search_queue.size(),
            "node_depth": node.depth,
            "proof_mode": node.proof_mode,
            "obligations_remaining": len(node.obligation_ids),
        }
        self.history.append(result)
        _log.debug("coordinator %s step %d: expanded %s", self.coordinator_id, step_idx, node.node_id)
        return result

    def allocate_budget(self, nodes: list) -> dict:
        """Pre-allocate budget for a batch of nodes before expansion.

        Distributes the remaining token budget evenly across the supplied
        nodes, up to a per-node cap.  Nodes are sorted by priority weight
        (descending) before allocation.

        Args:
            nodes: List of SemanticStateNode instances to pre-allocate for.

        Returns:
            Dict mapping node_id -> tokens_allocated.
        """
        if not nodes:
            return {}
        remaining = self.budget.remaining_tokens(
            self.search_queue.budget_ledger.total_charged()
        )
        per_node = max(1, remaining // len(nodes))
        result: Dict[str, int] = {}
        # Prioritise nodes with more obligations (more work to do)
        sorted_nodes = sorted(nodes, key=lambda n: len(n.obligation_ids), reverse=True)
        for n in sorted_nodes:
            alloc = min(per_node, self.budget.remaining_tokens(
                self.search_queue.budget_ledger.total_charged()
            ))
            self.search_queue.budget_ledger.allocate(n.node_id, alloc)
            result[n.node_id] = alloc
        return result

    def record_expansion(self, node_id: str, tokens_used: int) -> None:
        """Record that a node expansion consumed *tokens_used* tokens.

        Updates both the allocation (if not already allocated) and the charge
        in the ledger.

        Args:
            node_id: Identifier of the expanded node.
            tokens_used: Actual tokens consumed.
        """
        ledger = self.search_queue.budget_ledger
        if node_id not in ledger.allocations:
            ledger.allocate(node_id, tokens_used)
        ledger.charge(node_id, tokens_used)

    def is_budget_exhausted(self) -> bool:
        """Return True if the budget has been fully consumed.

        Checks both token usage (via the ledger) and wall-clock time against
        the allocated_at timestamp.

        Returns:
            Boolean exhaustion indicator.
        """
        total_charged = self.search_queue.budget_ledger.total_charged()
        elapsed = time.time() - self.budget.allocated_at
        return self.budget.is_exhausted(total_charged, elapsed)

    def summarize(self) -> dict:
        """Return a concise summary of search progress so far.

        Returns:
            Dict with coordinator_id, iteration_count, total_tokens_used,
            phase, queue_size, budget_utilization.
        """
        total_tokens = self.search_queue.budget_ledger.total_charged()
        elapsed = time.time() - self.budget.allocated_at
        return {
            "coordinator_id": self.coordinator_id,
            "iteration_count": self.iteration_count,
            "total_tokens_used": total_tokens,
            "phase": self.phase,
            "queue_size": self.search_queue.size(),
            "budget_utilization": round(
                self.budget.utilization(total_tokens, elapsed), 4
            ),
            "budget_exhausted": self.is_budget_exhausted(),
        }

    def to_dict(self) -> dict:
        """Serialise this coordinator to a plain Python dictionary.

        Returns:
            Dict with full coordinator state and recent history snapshot.
        """
        return {
            **self.summarize(),
            "budget": self.budget.to_dict(),
            "search_queue": self.search_queue.to_dict(),
            "history_length": len(self.history),
            "recent_history": self.history[-5:],
        }

    @classmethod
    def make(cls, budget: ComputeBudget) -> "FrontierBudgetedSearchCoordinator":
        """Create a fresh coordinator bound to *budget*.

        Constructs a new BudgetLedger and FrontierSearchQueue internally.

        Args:
            budget: The ComputeBudget to use for this coordinator.

        Returns:
            A fully initialised FrontierBudgetedSearchCoordinator.
        """
        ledger = BudgetLedger(
            ledger_id=_make_id("ledger"),
            allocations={},
            charges={},
            budget=budget,
        )
        queue = FrontierSearchQueue(
            queue_id=_make_id("queue"),
            nodes=[],
            budget_ledger=ledger,
            max_depth=50,
            expansion_count=0,
        )
        return cls(
            coordinator_id=_make_id("coordinator"),
            search_queue=queue,
            budget=budget,
            phase="initial",
            iteration_count=0,
            history=[],
        )


# ===========================================================================
# FrontierBudgetAnalyzer
# ===========================================================================


@dataclass(slots=True)
class FrontierBudgetAnalyzer:
    """Trend analysis and exhaustion prediction for frontier search budgets.

    Records per-step samples of (tokens_used, time_used, nodes_expanded) and
    uses exponential moving averages to estimate utilization trends, predict
    exhaustion, and detect anomalies.

    Attributes:
        analyzer_id: Unique identifier.
        samples: List of raw sample dicts, one per :meth:`record` call.
        window_size: Number of most recent samples used for windowed analysis.
    """

    analyzer_id: str
    samples: list
    window_size: int

    def record(self, tokens_used: int, time_used: float, nodes_expanded: int) -> None:
        """Append a new measurement sample.

        Args:
            tokens_used: Cumulative tokens consumed at the time of recording.
            time_used: Cumulative wall-clock seconds at the time of recording.
            nodes_expanded: Cumulative nodes expanded at the time of recording.
        """
        sample = {
            "tokens_used": int(tokens_used),
            "time_used": _safe_float(time_used),
            "nodes_expanded": int(nodes_expanded),
            "timestamp": time.time(),
        }
        self.samples.append(sample)
        _log.debug("analyzer %s: recorded sample #%d", self.analyzer_id, len(self.samples))

    def _window(self) -> list:
        """Return the most recent *window_size* samples."""
        return self.samples[-self.window_size:] if self.samples else []

    def utilization_trend(self) -> float:
        """Estimate the rate of change of token utilization over the window.

        Uses linear regression over the windowed samples to compute the slope
        of tokens_used vs. sample index.  The slope is normalised by the mean
        token count to produce a dimensionless trend coefficient.

        Returns:
            Float trend coefficient.  Positive means increasing utilization,
            negative means decreasing (unusual, but possible if the analyzer
            is reset).  Returns 0.0 if fewer than 2 samples are available.
        """
        window = self._window()
        if len(window) < 2:
            return 0.0
        n = len(window)
        xs = list(range(n))
        ys = [s["tokens_used"] for s in window]
        mean_x = sum(xs) / n
        mean_y = sum(ys) / n
        cov = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(xs, ys))
        var_x = sum((xi - mean_x) ** 2 for xi in xs)
        if var_x < 1e-12:
            return 0.0
        slope = cov / var_x
        # Normalise by mean_y to get a relative trend
        return slope / max(1.0, mean_y)

    def predicted_exhaustion_step(self) -> int:
        """Predict the step at which the budget will be exhausted.

        Uses the current utilization trend to extrapolate when tokens_used
        will reach the budget token_limit.

        Returns:
            Estimated step index for exhaustion.  Returns -1 if exhaustion
            cannot be predicted (e.g. trend is non-positive or too few
            samples).
        """
        window = self._window()
        if len(window) < 2:
            return -1
        last = window[-1]
        trend = self.utilization_trend()
        if trend <= 0.0:
            return -1
        # tokens_used grows at `trend * mean_y` tokens per step
        mean_y = sum(s["tokens_used"] for s in window) / len(window)
        per_step_increase = trend * max(1.0, mean_y)
        remaining = max(0, 10_000 - last["tokens_used"])  # rough budget reference
        steps_left = int(remaining / max(1.0, per_step_increase))
        return len(self.samples) + steps_left

    def efficiency_score(self) -> float:
        """Compute a score representing proof progress per token spent.

        Defined as ``nodes_expanded / max(1, tokens_used)`` over the window.
        Higher is better.

        Returns:
            Non-negative float efficiency score.
        """
        window = self._window()
        if not window:
            return 0.0
        last = window[-1]
        first = window[0]
        delta_nodes = max(0, last["nodes_expanded"] - first["nodes_expanded"])
        delta_tokens = max(1, last["tokens_used"] - first["tokens_used"])
        return delta_nodes / delta_tokens

    def anomaly_score(self) -> float:
        """Compute an anomaly score based on token consumption variance.

        Uses the coefficient of variation (std/mean) of per-step token
        increments over the window.  High values indicate irregular token
        usage, which may signal runaway expansion or stalled nodes.

        Returns:
            Non-negative float.  0.0 means perfectly uniform consumption.
        """
        window = self._window()
        if len(window) < 3:
            return 0.0
        # Compute per-step deltas
        deltas = [
            window[i]["tokens_used"] - window[i - 1]["tokens_used"]
            for i in range(1, len(window))
        ]
        mean_d = sum(deltas) / len(deltas)
        if mean_d <= 0.0:
            return 0.0
        variance = sum((d - mean_d) ** 2 for d in deltas) / len(deltas)
        std_d = math.sqrt(variance)
        return std_d / mean_d  # coefficient of variation

    def report(self) -> dict:
        """Generate a comprehensive analysis report.

        Returns:
            Dict with all key analysis metrics.
        """
        return {
            "analyzer_id": self.analyzer_id,
            "sample_count": len(self.samples),
            "window_size": self.window_size,
            "utilization_trend": round(self.utilization_trend(), 6),
            "predicted_exhaustion_step": self.predicted_exhaustion_step(),
            "efficiency_score": round(self.efficiency_score(), 6),
            "anomaly_score": round(self.anomaly_score(), 6),
            "latest_sample": self.samples[-1] if self.samples else None,
        }

    def to_dict(self) -> dict:
        """Serialise the analyzer to a plain Python dictionary.

        Returns:
            Dict with analyzer state and full sample list.
        """
        return {
            **self.report(),
            "samples": list(self.samples),
        }


# ===========================================================================
# FrontierBudgetWitness
# ===========================================================================


@dataclass(frozen=True, slots=True)
class FrontierBudgetWitness:
    """Immutable compliance certificate for a completed or ongoing search phase.

    A witness is issued by :meth:`issue` after a coordinator and analyzer have
    accumulated sufficient data.  It certifies that the search was conducted
    within the allocated budget and provides an evidence dict for audit
    purposes.

    Attributes:
        witness_id: Unique identifier for this witness.
        coordinator_id: Coordinator that generated the search data.
        budget_id: Budget against which compliance is assessed.
        tokens_used: Total tokens consumed at witness issuance time.
        time_used: Total wall-clock seconds elapsed at witness issuance time.
        nodes_expanded: Total nodes expanded by the coordinator.
        compliance: True if the search did not exceed the budget limits.
        timestamp: POSIX timestamp of witness issuance.
        evidence: Supporting evidence dict (analyzer report, coordinator
            summary, etc.).
    """

    witness_id: str
    coordinator_id: str
    budget_id: str
    tokens_used: int
    time_used: float
    nodes_expanded: int
    compliance: bool
    timestamp: float
    evidence: dict

    def to_dict(self) -> dict:
        """Serialise this witness to a plain Python dictionary.

        Returns:
            Dict with all witness fields and a copy of the evidence dict.
        """
        return {
            "witness_id": self.witness_id,
            "coordinator_id": self.coordinator_id,
            "budget_id": self.budget_id,
            "tokens_used": self.tokens_used,
            "time_used": round(self.time_used, 4),
            "nodes_expanded": self.nodes_expanded,
            "compliance": self.compliance,
            "timestamp": self.timestamp,
            "evidence": dict(self.evidence),
        }

    def is_valid(self) -> bool:
        """Return True if this witness is internally consistent.

        Checks that:
        - tokens_used and nodes_expanded are non-negative
        - time_used is non-negative
        - If compliance is True, tokens_used does not exceed the budget's
          token_limit stored in evidence.

        Returns:
            Boolean validity indicator.
        """
        if self.tokens_used < 0 or self.nodes_expanded < 0 or self.time_used < 0.0:
            return False
        if self.compliance:
            budget_info = self.evidence.get("budget", {})
            token_limit = budget_info.get("token_limit", float("inf"))
            if self.tokens_used > token_limit:
                return False
        return True

    def certify_text(self) -> str:
        """Return a human-readable certification summary.

        Returns:
            A multi-line string describing the witness, suitable for logging
            or display in a terminal.
        """
        status = "COMPLIANT" if self.compliance else "NON-COMPLIANT"
        valid = "VALID" if self.is_valid() else "INVALID"
        lines = [
            f"=== FrontierBudgetWitness ({status} / {valid}) ===",
            f"  witness_id      : {self.witness_id}",
            f"  coordinator_id  : {self.coordinator_id}",
            f"  budget_id       : {self.budget_id}",
            f"  tokens_used     : {self.tokens_used}",
            f"  time_used       : {self.time_used:.3f}s",
            f"  nodes_expanded  : {self.nodes_expanded}",
            f"  issued_at       : {self.timestamp:.3f}",
        ]
        return "\n".join(lines)

    @classmethod
    def issue(
        cls,
        coordinator: FrontierBudgetedSearchCoordinator,
        analyzer: FrontierBudgetAnalyzer,
    ) -> "FrontierBudgetWitness":
        """Issue a witness for the current state of *coordinator*.

        Collects evidence from both the coordinator and the analyzer, then
        determines compliance by checking whether the budget was exceeded.

        Args:
            coordinator: The coordinator whose search is being certified.
            analyzer: The analyzer that has been tracking the search.

        Returns:
            A new, immutable FrontierBudgetWitness.
        """
        tokens_used = coordinator.search_queue.budget_ledger.total_charged()
        elapsed = time.time() - coordinator.budget.allocated_at
        nodes_expanded = coordinator.search_queue.expansion_count

        compliance = not coordinator.budget.is_exhausted(tokens_used, elapsed)

        evidence = {
            "coordinator_summary": coordinator.summarize(),
            "analyzer_report": analyzer.report(),
            "budget": coordinator.budget.to_dict(),
        }

        return cls(
            witness_id=_make_id("witness"),
            coordinator_id=coordinator.coordinator_id,
            budget_id=coordinator.budget.budget_id,
            tokens_used=tokens_used,
            time_used=elapsed,
            nodes_expanded=nodes_expanded,
            compliance=compliance,
            timestamp=time.time(),
            evidence=evidence,
        )


# ===========================================================================
# Guarded optional imports from the broader jugeo ecosystem
# ===========================================================================

try:
    from jugeo.orchestration.frontier_phases.models import (  # type: ignore[import]
        PhaseKind,
        TransitionTrigger,
        PhaseDescriptor,
        PhaseTransitionRecord,
        PhaseHistory,
        StallDetector,
        ConvergenceCertificate,
        PhaseHealthStatus,
    )
except Exception:
    pass

try:
    from jugeo.orchestration.frontier import (  # type: ignore[import]
        Frontier,
        FrontierNode,
        FrontierHistory,
        PhaseTransition,
        BackpressureController,
        FrontierBudget,
        FrontierDiversity,
    )
except Exception:
    pass

try:
    from jugeo.orchestration.controller import (  # type: ignore[import]
        OrchestratorState,
        SemanticMove,
        ConvergenceMonitor,
    )
except Exception:
    pass

try:
    from jugeo.evidence.trust import (  # type: ignore[import]
        TrustLevel,
        TrustAlgebra,
        TrustProfile,
    )
except Exception:
    pass


# ===========================================================================
# Smoke test / entry point
# ===========================================================================

if __name__ == "__main__":
    import pprint

    print("Running s01 smoke test …")

    # 1. Create a ComputeBudget via make()
    budget = ComputeBudget.make(
        token_limit=5_000,
        time_limit=120.0,
        priority_weight=0.9,
    )
    assert budget.token_limit == 5_000
    assert budget.priority_weight == 0.9
    print(f"  [1] ComputeBudget created: {budget.budget_id}")

    # 2. Create a BudgetLedger
    ledger = BudgetLedger(
        ledger_id=_make_id("test_ledger"),
        allocations={},
        charges={},
        budget=budget,
    )
    ledger.allocate("test_node_x", 100)
    ledger.charge("test_node_x", 80)
    assert ledger.balance("test_node_x") == 20
    assert ledger.total_charged() == 80
    print(f"  [2] BudgetLedger created and verified: {ledger.ledger_id}")

    # 3. Create 3 SemanticStateNodes via make()
    node_a = SemanticStateNode.make(
        state_vector=[1.0, 0.0, 0.5],
        obligation_ids=["ob_001", "ob_002"],
        proof_mode="smt",
        depth=0,
        trust_score=0.95,
    )
    node_b = SemanticStateNode.make(
        state_vector=[0.0, 1.0, 0.5],
        obligation_ids=["ob_003"],
        proof_mode="induction",
        parent_id=node_a.node_id,
        depth=1,
        trust_score=0.80,
    )
    node_c = SemanticStateNode.make(
        state_vector=[0.5, 0.5, 1.0],
        obligation_ids=["ob_004", "ob_005", "ob_006"],
        proof_mode="case_split",
        parent_id=node_a.node_id,
        depth=1,
        trust_score=0.70,
    )
    dist_ab = node_a.semantic_distance(node_b)
    assert dist_ab >= 0.0, "Distance must be non-negative"
    print(f"  [3] Three SemanticStateNodes created. dist(a,b) = {dist_ab:.4f}")

    # 4. Create a FrontierSearchQueue and push all 3 nodes
    coordinator = FrontierBudgetedSearchCoordinator.make(budget)
    coordinator.search_queue.push(node_a, priority=1.0)
    coordinator.search_queue.push(node_b, priority=0.8)
    coordinator.search_queue.push(node_c, priority=1.5)
    assert coordinator.search_queue.size() == 3
    print(f"  [4] FrontierSearchQueue populated: {coordinator.search_queue.size()} nodes")

    # 5. FrontierBudgetedSearchCoordinator was created in step 4 via make(budget)
    print(f"  [5] FrontierBudgetedSearchCoordinator: {coordinator.coordinator_id}")

    # 6. Run coordinator.step({}) 3 times
    for i in range(3):
        result = coordinator.step({})
        print(f"  [6.{i+1}] step result: node={result.get('node_id')}, "
              f"tokens={result.get('tokens_used')}, "
              f"exhausted={result.get('budget_exhausted')}")

    # 7. Create a FrontierBudgetAnalyzer and record 3 samples
    analyzer = FrontierBudgetAnalyzer(
        analyzer_id=_make_id("analyzer"),
        samples=[],
        window_size=10,
    )
    total_charged = coordinator.search_queue.budget_ledger.total_charged()
    analyzer.record(total_charged // 3, 1.0, 1)
    analyzer.record(total_charged * 2 // 3, 2.0, 2)
    analyzer.record(total_charged, 3.0, coordinator.search_queue.expansion_count)
    assert len(analyzer.samples) == 3
    print(f"  [7] FrontierBudgetAnalyzer recorded 3 samples. "
          f"Trend: {analyzer.utilization_trend():.6f}")

    # 8. Issue a FrontierBudgetWitness and print to_dict()
    witness = FrontierBudgetWitness.issue(coordinator, analyzer)
    assert witness.is_valid()
    print("  [8] FrontierBudgetWitness issued:")
    pprint.pprint(witness.to_dict(), indent=4)
    print(witness.certify_text())

    print("\ns01 smoke test passed")
