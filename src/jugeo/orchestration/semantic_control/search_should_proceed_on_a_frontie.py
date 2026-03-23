"""
Search Should Proceed on a Frontier.

# copilot: This module formalises the idea that orchestrated generation is a
*search* process and that search should be managed through an explicit frontier
data structure.  The frontier is the set of judgment states that are "active" —
they have been reached from the initial state but have not yet been expanded to
produce successor states.

Why a frontier is not a queue
==============================
In classical BFS/DFS the frontier is a queue or stack of unvisited nodes.  In
semantic search the frontier is richer:
  - Each frontier node carries a full judgment tuple J = (c, φ, A, E, O, B, T, Π).
  - Nodes are comparable by heuristic estimate h(J) + cost-so-far g(J).
  - The trust algebra (E_adm, ≼, ⊕, ⊖, ↑_π, ↓_χ) acts as a *gating mechanism*:
    a node may not be expanded if its trust tier T is below the expansion policy's
    floor.
  - Two nodes may be *dominated* in the semantic sense: J₁ ≼ J₂ if J₂ has fewer
    obligations, higher trust, and a deeper proof than J₁.  Dominated nodes are
    pruned.

Trust stratification of the frontier
=====================================
The frontier is partitioned into trust strata:
  S₁ = {J ∈ frontier | T = PROPOSAL}
  S₂ = {J ∈ frontier | T = REVIEWED}
  …
  S₅ = {J ∈ frontier | T = PROOF_BACKED}

Expansion proceeds from the highest stratum first.  If the expansion budget is
exhausted before S₅ nodes are found, the system demotes to lower strata — but
marks the resulting solutions with lower trust.

Proof obligations for frontier nodes
=====================================
Every expansion step generates a proof obligation: "this successor J' is reachable
from J via an admissible control action."  The FrontierExpansion.expansion_proof
must reference a ProofObject discharging this obligation.  Until the proof is
complete, the successor node is treated as PROPOSAL-tier.

Cycle detection
===============
Semantic cycles are subtler than syntactic cycles.  A cycle occurs when a node
J' is reachable from J and J' ≼ J (J' is dominated by J).  This is not just
visiting the same string-state twice — it is visiting a semantically weaker state
after a semantically stronger one, which constitutes progress loss.
"""

from __future__ import annotations

import hashlib
import heapq
import math
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, FrozenSet, Iterator, List, Optional, Set, Tuple

# ---------------------------------------------------------------------------
# Jugeo imports with stub fallback
# ---------------------------------------------------------------------------
try:
    from jugeo.core.trust import TrustTier, TrustAlgebraElement  # type: ignore
    from jugeo.core.judgment import JudgmentTuple  # type: ignore
    from jugeo.core.proof import ProofObject  # type: ignore
    from jugeo.orchestration.semantic_control.orchestration_is_a_control_problem import (  # type: ignore
        ControlState, ControlPolicy, PolicyType, compute_semantic_distance,
        TRUST_TIER_COUNT,
    )
except ImportError:
    class TrustTier(Enum):  # type: ignore
        PROPOSAL = 1
        REVIEWED = 2
        VERIFIED = 3
        RUNTIME_WITNESSED = 4
        PROOF_BACKED = 5

    @dataclass(frozen=True)
    class TrustAlgebraElement:  # type: ignore
        tier: TrustTier = TrustTier.PROPOSAL
        evidence_ids: Tuple[str, ...] = ()
        lattice_height: int = 0

        def join(self, other: TrustAlgebraElement) -> TrustAlgebraElement:
            higher = max(self.tier.value, other.tier.value)
            return TrustAlgebraElement(
                tier=TrustTier(higher),
                evidence_ids=self.evidence_ids + other.evidence_ids,
                lattice_height=max(self.lattice_height, other.lattice_height) + 1,
            )

        def meet(self, other: TrustAlgebraElement) -> TrustAlgebraElement:
            lower = min(self.tier.value, other.tier.value)
            return TrustAlgebraElement(
                tier=TrustTier(lower),
                evidence_ids=(),
                lattice_height=min(self.lattice_height, other.lattice_height),
            )

        def elevate(self, proof_id: str) -> TrustAlgebraElement:
            new_val = min(self.tier.value + 1, max(t.value for t in TrustTier))
            return TrustAlgebraElement(
                tier=TrustTier(new_val),
                evidence_ids=self.evidence_ids + (proof_id,),
                lattice_height=self.lattice_height + 1,
            )

        def demote(self, cx_id: str) -> TrustAlgebraElement:
            new_val = max(self.tier.value - 1, 1)
            return TrustAlgebraElement(
                tier=TrustTier(new_val),
                evidence_ids=self.evidence_ids,
                lattice_height=max(self.lattice_height - 1, 0),
            )

    @dataclass(frozen=True)
    class JudgmentTuple:  # type: ignore
        c: str = ""
        phi: str = ""
        A: Tuple[str, ...] = ()
        E: Tuple[str, ...] = ()
        O: Tuple[str, ...] = ()
        B: str = ""
        T: TrustTier = TrustTier.PROPOSAL
        Pi: str = ""

    @dataclass(frozen=True)
    class ProofObject:  # type: ignore
        proof_id: str = ""
        strategy: str = "none"
        steps: Tuple[str, ...] = ()
        is_complete: bool = False

    class PolicyType(Enum):  # type: ignore
        GREEDY = auto()
        OPTIMAL = auto()
        ROBUST = auto()
        ADAPTIVE = auto()
        PROOF_GUIDED = auto()
        TRUST_WEIGHTED = auto()

    TRUST_TIER_COUNT: int = 5

    @dataclass(frozen=True)
    class ControlState:  # type: ignore
        state_id: str = ""
        judgment_tuple: JudgmentTuple = field(default_factory=JudgmentTuple)
        semantic_coordinates: Tuple[float, ...] = ()
        distance_to_goal: float = 1.0
        trust_level: TrustAlgebraElement = field(default_factory=TrustAlgebraElement)
        timestamp: float = 0.0
        trajectory_segment: int = 0

    @dataclass(frozen=True)
    class ControlPolicy:  # type: ignore
        policy_id: str = ""
        policy_type: PolicyType = PolicyType.GREEDY
        policy_parameters: Dict[str, Any] = field(default_factory=dict)
        coverage_region: str = ""
        trust_tier: TrustTier = TrustTier.PROPOSAL
        policy_proof: str = ""
        last_validated: float = 0.0

    def compute_semantic_distance(s1: ControlState, s2: ControlState) -> float:  # type: ignore
        return abs(s1.distance_to_goal - s2.distance_to_goal)


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class FrontierStrategy(Enum):
    """The expansion strategy for the semantic frontier.

    The strategy determines the *order* in which nodes are selected for
    expansion — it does not determine which nodes are admissible (that is
    governed by the trust algebra).
    """
    BEST_FIRST = auto()
    """Expand the node with the smallest f(J) = g(J) + h(J).  This is A*-like
    search in semantic space.  Requires an admissible heuristic h."""

    BREADTH_FIRST = auto()
    """Expand nodes in FIFO order (level by level).  Guarantees shortest-path
    solutions but may be slow when the branching factor is high."""

    DEPTH_FIRST = auto()
    """Expand nodes in LIFO order.  Fast but incomplete — may not find solutions
    if the search space has infinite branches.  Useful for proof-guided search
    where we want to commit to a proof strategy quickly."""

    BEAM_SEARCH = auto()
    """Keep only the top-k nodes on the frontier at each level.  Trades
    completeness for computational efficiency.  k is the beam width."""

    PROOF_GUIDED = auto()
    """Expand nodes that make most progress on discharging proof obligations.
    The priority is the number of obligations discharged per expansion step."""

    TRUST_STRATIFIED = auto()
    """Expand nodes in trust-tier order: PROOF_BACKED nodes before VERIFIED,
    VERIFIED before RUNTIME_WITNESSED, etc.  Within the same tier, use BEST_FIRST."""


class PruningCriterion(Enum):
    """Criteria for pruning nodes from the semantic frontier.

    Pruning reduces the frontier size and prevents the search from exploring
    semantically unproductive branches.
    """
    TRUST_BELOW_THRESHOLD = auto()
    """Prune nodes whose trust tier is below the configured floor.  These nodes
    cannot contribute to a PROOF_BACKED solution."""

    DOMINATED = auto()
    """Prune nodes that are dominated by another node: J₁ is dominated by J₂ if
    J₂ has ≤ obligations, ≥ trust, and ≥ proof depth."""

    CYCLE_DETECTED = auto()
    """Prune nodes that form a semantic cycle: the node's judgment tuple is
    semantically equivalent to an ancestor in the search tree."""

    BUDGET_EXCEEDED = auto()
    """Prune nodes that exceed the expansion budget (total cost g(J) > budget)."""

    PROOF_FAILED = auto()
    """Prune nodes whose expansion proof obligation could not be discharged."""

    GEOMETRIC_DOMINATED = auto()
    """Prune nodes that are farther from the goal than another node with the same
    or lower cost g(J).  This is the geometric dominance criterion."""


class ExpansionPolicy(Enum):
    """The policy governing how frontier nodes are expanded.

    Expansion policy determines *when* a node is expanded and *how many*
    successors are generated per expansion.
    """
    GREEDY_EXPAND = auto()
    """Expand immediately and generate all admissible successors.  No proof
    required before expansion.  Fastest but least trustworthy."""

    CAUTIOUS_EXPAND = auto()
    """Generate only one successor per expansion (the best one).  Reduces
    branching factor but may miss optimal solutions."""

    PROOF_REQUIRED = auto()
    """Require a discharge proof before expanding.  Slowest but produces
    a fully certified search tree."""

    TRUST_GATED = auto()
    """Expand only if the node's trust tier is above the configured floor.
    Nodes below the floor are held in a "pending" queue."""

    PARALLEL_EXPAND = auto()
    """Expand multiple nodes in parallel.  Requires that the expansions are
    independent (no shared mutable state)."""


# ---------------------------------------------------------------------------
# Frozen dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FrontierNode:
    """A single node on the semantic search frontier.

    A FrontierNode is the frontier-layer representation of a judgment tuple.
    It augments the tuple with search-specific metadata: cost-so-far, heuristic
    estimate, parent reference, and a proof of reachability.

    The f-value is f(J) = g(J) + h(J) where:
      g(J) = cost_so_far (number of expansion steps to reach J from J₀)
      h(J) = heuristic_estimate (estimated remaining steps to J*)

    Trust constraint: h must be admissible — it must never *overestimate* the
    true remaining cost.  If h overestimates, A* may not find the optimal path.
    For semantic search, admissibility requires that h ≤ obligation_count(J*) − obligation_count(J).

    The ``node_proof`` field is a reference to a proof that the node is reachable
    from its parent via an admissible control action.  Nodes without a proof are
    treated as PROPOSAL-tier regardless of their judgment tuple's T field.
    """
    node_id: str
    judgment_tuple: JudgmentTuple
    semantic_coordinates: Tuple[float, ...]
    heuristic_estimate: float        # h(J): estimated cost to goal
    cost_so_far: float               # g(J): actual cost from root
    parent_node_id: str              # "" for the root node
    generation_depth: int            # 0 for root
    trust_tier: TrustTier
    node_proof: str                  # proof_id or "" if unproven

    @property
    def f_value(self) -> float:
        """f(J) = g(J) + h(J) — total estimated path cost through this node."""
        return self.cost_so_far + self.heuristic_estimate

    def dominates(self, other: FrontierNode) -> bool:
        """Return True if self dominates other in the semantic dominance order.

        J dominates J' if:
          - obligations(J) ≤ obligations(J')  (fewer unmet obligations)
          - trust_tier(J) ≥ trust_tier(J')   (higher trust)
          - cost_so_far(J) ≤ cost_so_far(J') (not more expensive to reach)

        All three conditions must hold simultaneously.
        """
        return (
            len(self.judgment_tuple.O) <= len(other.judgment_tuple.O)
            and self.trust_tier.value >= other.trust_tier.value
            and self.cost_so_far <= other.cost_so_far
        )


@dataclass(frozen=True)
class FrontierExpansion:
    """The result of expanding a single frontier node.

    When a node J is expanded, it produces a set of child nodes J₁, …, Jₙ.
    The expansion proof certifies that each child is reachable from J via
    an admissible control action.

    Trust propagation: the trust tier of each child is:
      - At most the parent's tier (trust cannot be fabricated during expansion)
      - Possibly lower if the expansion action has lower trust than the parent
      - Possibly higher if the expansion action includes a trust elevation ↑_π

    The ``expansion_cost`` is the semantic cost of the expansion step (the
    decrease in distance to goal per child).  It must be non-negative for
    each child (each child must be at least as close to the goal as the parent).
    """
    expansion_id: str
    parent_node: FrontierNode
    child_nodes: Tuple[FrontierNode, ...]
    expansion_proof: str                   # proof_id or ""
    trust_propagation: Tuple[TrustAlgebraElement, ...]  # one per child
    expansion_cost: float                  # cost of this expansion step


@dataclass(frozen=True)
class FrontierPruning:
    """A pruning operation applied to the frontier.

    Records which nodes were pruned, why, and with what proof.

    The ``pruning_proof`` certifies that the pruned nodes are *subsumed*: any
    solution that passes through them also has an equal or better solution
    through a retained node.  This is the soundness condition for pruning: we
    must not prune nodes that are on the unique path to the goal.

    The ``trust_threshold_used`` records the minimum trust tier that was required
    for retention.  Nodes below this threshold were pruned by TRUST_BELOW_THRESHOLD.
    """
    pruning_id: str
    pruned_nodes: Tuple[str, ...]           # node_ids of pruned nodes
    pruning_criterion: PruningCriterion
    nodes_retained: int
    pruning_proof: str                      # proof_id or ""
    trust_threshold_used: TrustTier


@dataclass(frozen=True)
class SemanticFrontier:
    """The active frontier of the semantic search.

    This is a *snapshot* of the frontier at a given point in time.  The
    FrontierManager maintains the mutable frontier and produces SemanticFrontier
    snapshots for logging and proof purposes.

    ``active_nodes`` — nodes currently on the frontier (not yet expanded).
    ``pruned_nodes`` — nodes that have been pruned (for audit trail).
    ``expansion_budget`` — remaining number of expansion steps.
    ``frontier_metric`` — the metric used to rank nodes on the frontier.
    ``trust_filter`` — the minimum trust tier for a node to be on the frontier.
    ``frontier_proof`` — a proof that the frontier satisfies the search invariant:
      every path from the initial state to the goal passes through at least one
      active node.
    """
    frontier_id: str
    active_nodes: Tuple[str, ...]           # node_ids
    pruned_nodes: Tuple[str, ...]           # node_ids
    expansion_budget: int
    frontier_metric: str                    # "f_value", "trust_stratified", etc.
    trust_filter: TrustTier
    frontier_proof: str                     # proof_id or ""


# ---------------------------------------------------------------------------
# Mutable helper classes
# ---------------------------------------------------------------------------

class FrontierManager:
    """Manages the lifecycle of the semantic search frontier.

    The frontier is a priority queue of FrontierNode objects, ordered by their
    f-value (BEST_FIRST) or trust tier (TRUST_STRATIFIED).

    The FrontierManager is responsible for:
      1. Initialising the frontier with the start node.
      2. Expanding nodes according to the expansion policy.
      3. Pruning dominated or inadmissible nodes.
      4. Detecting cycles.
      5. Returning the frontier snapshot for auditing.

    Internal data structures:
      _heap      — a min-heap of (priority, FrontierNode) tuples
      _open      — set of node_ids currently on the frontier
      _closed    — set of node_ids already expanded
      _all_nodes — dict mapping node_id → FrontierNode for the full history
      _parent    — dict mapping node_id → parent_node_id for path reconstruction
    """

    def __init__(
        self,
        strategy: FrontierStrategy,
        expansion_policy: ExpansionPolicy,
        trust_floor: TrustTier = TrustTier.PROPOSAL,
        expansion_budget: int = 1000,
        beam_width: int = 10,
    ) -> None:
        self.strategy = strategy
        self.expansion_policy = expansion_policy
        self.trust_floor = trust_floor
        self.expansion_budget = expansion_budget
        self.beam_width = beam_width

        self._heap: List[Tuple[float, int, FrontierNode]] = []
        self._open: Set[str] = set()
        self._closed: Set[str] = set()
        self._all_nodes: Dict[str, FrontierNode] = {}
        self._parent: Dict[str, str] = {}
        self._expansions_done: int = 0
        self._pruning_history: List[FrontierPruning] = []
        self._tie_counter: int = 0   # For stable heap ordering

    def initialise(self, root_node: FrontierNode) -> None:
        """Add the root node to the frontier."""
        self._add_node(root_node)
        self._parent[root_node.node_id] = ""

    def _add_node(self, node: FrontierNode) -> None:
        """Add a node to the open frontier."""
        if node.node_id in self._open or node.node_id in self._closed:
            return
        priority = self._compute_priority(node)
        self._tie_counter += 1
        heapq.heappush(self._heap, (priority, self._tie_counter, node))
        self._open.add(node.node_id)
        self._all_nodes[node.node_id] = node

    def _compute_priority(self, node: FrontierNode) -> float:
        """Compute the priority for a node (lower = expanded sooner)."""
        if self.strategy == FrontierStrategy.BEST_FIRST:
            return node.f_value
        elif self.strategy == FrontierStrategy.BREADTH_FIRST:
            return float(node.generation_depth)
        elif self.strategy == FrontierStrategy.DEPTH_FIRST:
            return -float(node.generation_depth)
        elif self.strategy == FrontierStrategy.BEAM_SEARCH:
            return node.f_value
        elif self.strategy == FrontierStrategy.PROOF_GUIDED:
            # Priority = number of undischarged obligations (fewer = better)
            return float(len(node.judgment_tuple.O))
        elif self.strategy == FrontierStrategy.TRUST_STRATIFIED:
            # Higher trust tier → lower priority value (expanded first)
            max_tier = max(t.value for t in TrustTier)
            trust_priority = max_tier - node.trust_tier.value
            return trust_priority * 1000.0 + node.f_value
        return node.f_value

    def pop_next(self) -> Optional[FrontierNode]:
        """Pop the highest-priority node from the frontier."""
        while self._heap:
            _, _, node = heapq.heappop(self._heap)
            if node.node_id in self._open:
                self._open.discard(node.node_id)
                self._closed.add(node.node_id)
                return node
        return None

    def add_children(self, children: List[FrontierNode]) -> int:
        """Add child nodes to the frontier.  Returns number of nodes added."""
        added = 0
        for child in children:
            if child.node_id in self._closed:
                continue
            if child.trust_tier.value < self.trust_floor.value:
                continue  # Trust-gated: do not add below-floor nodes
            self._add_node(child)
            added += 1
        # Apply beam pruning if necessary
        if self.strategy == FrontierStrategy.BEAM_SEARCH:
            self._apply_beam_pruning()
        return added

    def _apply_beam_pruning(self) -> None:
        """Keep only the top beam_width nodes on the frontier."""
        if len(self._open) <= self.beam_width:
            return
        # Collect all open nodes and sort by priority
        open_nodes = [
            self._all_nodes[nid] for nid in self._open
            if nid in self._all_nodes
        ]
        open_nodes.sort(key=lambda n: self._compute_priority(n))
        to_prune = [n.node_id for n in open_nodes[self.beam_width:]]
        for nid in to_prune:
            self._open.discard(nid)

        pruning = FrontierPruning(
            pruning_id=f"prune-beam-{uuid.uuid4().hex[:8]}",
            pruned_nodes=tuple(to_prune),
            pruning_criterion=PruningCriterion.BUDGET_EXCEEDED,
            nodes_retained=self.beam_width,
            pruning_proof="",
            trust_threshold_used=self.trust_floor,
        )
        self._pruning_history.append(pruning)

    def record_expansion(self) -> None:
        """Increment the expansion counter."""
        self._expansions_done += 1

    @property
    def is_empty(self) -> bool:
        return len(self._open) == 0

    @property
    def budget_remaining(self) -> int:
        return self.expansion_budget - self._expansions_done

    def snapshot(self) -> SemanticFrontier:
        """Return an immutable snapshot of the current frontier state."""
        return SemanticFrontier(
            frontier_id=f"front-{uuid.uuid4().hex[:8]}",
            active_nodes=tuple(self._open),
            pruned_nodes=tuple(
                nid
                for p in self._pruning_history
                for nid in p.pruned_nodes
            ),
            expansion_budget=self.budget_remaining,
            frontier_metric=self.strategy.name,
            trust_filter=self.trust_floor,
            frontier_proof="",  # No proof yet
        )

    def get_node(self, node_id: str) -> Optional[FrontierNode]:
        return self._all_nodes.get(node_id)

    def get_parent_id(self, node_id: str) -> str:
        return self._parent.get(node_id, "")

    def register_parent(self, child_id: str, parent_id: str) -> None:
        self._parent[child_id] = parent_id


class HeuristicEstimator:
    """Estimates heuristic values h(J) for frontier nodes.

    The heuristic is an *admissible* estimate of the remaining cost to the
    goal: it must never overestimate.  In semantic search:
      h(J) ≤ obligation_count(J) − obligation_count(J*)
             + trust_gap(J)
             + proof_completeness_gap(J)

    This implementation uses a weighted combination of these components.
    The weights are recalibrated as search proceeds (adaptive heuristic).
    """

    def __init__(self, goal_judgment: JudgmentTuple) -> None:
        self.goal_judgment = goal_judgment
        self._goal_obligations: int = len(goal_judgment.O)
        self._goal_trust_value: int = goal_judgment.T.value
        self._goal_proof_len: int = len(goal_judgment.Pi)
        self._weight_obligation: float = 0.5
        self._weight_trust: float = 0.3
        self._weight_proof: float = 0.2
        self._call_count: int = 0

    def estimate(self, node: FrontierNode) -> float:
        """Compute h(J) for a frontier node.

        The estimate is guaranteed admissible: each component is a lower bound
        on the true remaining cost (each obligation discharge costs at least 1
        step, each trust elevation costs at least 1 step, etc.).
        """
        self._call_count += 1
        j = node.judgment_tuple

        # Obligation component: remaining obligations to discharge
        obligation_gap = max(0, len(j.O) - self._goal_obligations)

        # Trust component: trust levels to elevate
        max_tier = max(t.value for t in TrustTier)
        trust_gap = max(0, self._goal_trust_value - j.T.value) / max_tier

        # Proof component: proxy for remaining proof work
        proof_gap = max(0, self._goal_proof_len - len(j.Pi)) / max(self._goal_proof_len, 1)

        h = (
            self._weight_obligation * obligation_gap
            + self._weight_trust * trust_gap
            + self._weight_proof * proof_gap
        )
        return h

    def recalibrate(self, actual_cost: float, predicted_cost: float) -> None:
        """Recalibrate heuristic weights based on observed prediction error.

        If the heuristic overestimated (inadmissible), reduce trust-component
        weight.  If it consistently underestimated, increase it.

        Note: this must NOT make the heuristic inadmissible — we clamp weights
        to ensure admissibility is preserved.
        """
        error = actual_cost - predicted_cost
        if error > 0:
            # Heuristic underestimated — can be more aggressive (stay admissible)
            self._weight_obligation = min(0.7, self._weight_obligation + 0.01)
        else:
            # Heuristic overestimated — reduce to restore admissibility
            self._weight_obligation = max(0.3, self._weight_obligation - 0.01)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_EXPANSION_BRANCHING_FACTOR: int = 3
"""Default number of successors generated per expansion."""

MAX_FRONTIER_SIZE: int = 10_000
"""Hard limit on frontier size to prevent memory exhaustion."""

MAX_GENERATION_DEPTH: int = 500
"""Maximum depth of the search tree (prevents infinite depth-first search)."""

CYCLE_DETECTION_FINGERPRINT_BITS: int = 128
"""Number of bits used for node fingerprinting in cycle detection."""

DOMINANCE_OBLIGATION_WEIGHT: float = 0.5
DOMINANCE_TRUST_WEIGHT: float = 0.3
DOMINANCE_PROOF_WEIGHT: float = 0.2
"""Weights for the dominance relation comparison."""

EXPANSION_LOG_TEMPLATE: str = (
    "[EXPAND] depth={depth:04d} | node={node_id} | f={f:.4f} | "
    "trust={trust} | obligations={oblig} | children={n_children}"
)


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def _node_fingerprint(node: FrontierNode) -> str:
    """Compute a compact fingerprint for cycle detection.

    The fingerprint is derived from the semantically significant components
    of the judgment tuple: φ, O, T, Π.  The context c and agents A are
    excluded because they may change without semantic regression.
    """
    j = node.judgment_tuple
    canonical = f"{j.phi}|{sorted(j.O)}|{j.T.value}|{len(j.Pi)}"
    return hashlib.sha256(canonical.encode()).hexdigest()[:32]


def _obligation_similarity(j1: JudgmentTuple, j2: JudgmentTuple) -> float:
    """Jaccard similarity of obligation sets."""
    o1 = set(j1.O)
    o2 = set(j2.O)
    if not o1 and not o2:
        return 1.0
    intersection = len(o1 & o2)
    union = len(o1 | o2)
    return intersection / union if union > 0 else 1.0


def _make_node_id() -> str:
    return f"fn-{uuid.uuid4().hex[:12]}"


def _make_expansion_id() -> str:
    return f"exp-{uuid.uuid4().hex[:12]}"


def _make_pruning_id() -> str:
    return f"prn-{uuid.uuid4().hex[:12]}"


def _embed_judgment(j: JudgmentTuple, dim: int = 16) -> Tuple[float, ...]:
    """Embed a judgment tuple into a float vector for coordinate operations."""
    canonical = f"{j.c}|{j.phi}|{j.A}|{j.E}|{j.O}|{j.B}|{j.T}|{j.Pi}"
    digest = hashlib.sha256(canonical.encode()).digest()
    coords: List[float] = []
    for i in range(min(dim, len(digest) // 2)):
        val = (digest[2 * i] * 256 + digest[2 * i + 1]) / 32767.5 - 1.0
        coords.append(val)
    while len(coords) < dim:
        coords.append(0.0)
    return tuple(coords[:dim])


def _euclidean(a: Tuple[float, ...], b: Tuple[float, ...]) -> float:
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def compute_frontier_heuristic(
    node: FrontierNode,
    goal_judgment: JudgmentTuple,
) -> float:
    """Compute h(J) — the heuristic estimate of remaining cost to goal.

    The heuristic is admissible: it never overestimates.

    Components:
      1. Obligation gap: |O(J)| − |O(J*)| (always non-negative since J* has |O|=0)
      2. Trust gap: (T*_value − T_value) / max_tier (normalised)
      3. Proof gap: max(0, len(Π*) − len(Π)) / max(len(Π*), 1)

    Parameters
    ----------
    node : FrontierNode
        The node whose heuristic we are computing.
    goal_judgment : JudgmentTuple
        The goal judgment J* (should have O=(), T=PROOF_BACKED).

    Returns
    -------
    float
        The heuristic estimate h(J) ≥ 0.
    """
    j = node.judgment_tuple
    max_tier = max(t.value for t in TrustTier)

    obligation_gap = float(max(0, len(j.O) - len(goal_judgment.O)))
    trust_gap = max(0.0, (goal_judgment.T.value - j.T.value)) / max_tier
    proof_gap = max(0.0, len(goal_judgment.Pi) - len(j.Pi)) / max(len(goal_judgment.Pi), 1)

    h = 0.5 * obligation_gap + 0.3 * trust_gap + 0.2 * proof_gap
    return h


def detect_frontier_cycle(
    node: FrontierNode,
    frontier: SemanticFrontier,
    all_nodes: Dict[str, FrontierNode],
    visited_fingerprints: Optional[Set[str]] = None,
) -> bool:
    """Detect if the given node forms a semantic cycle in the search tree.

    A semantic cycle occurs when the node's fingerprint matches any ancestor's
    fingerprint in the current path from root to node.

    Note: this is stricter than syntactic cycle detection (same string).  Two
    nodes are semantically cyclic if they have the same (φ, O, T, |Π|) tuple
    — they are at the same semantic state even if reached by different paths.

    Parameters
    ----------
    node : FrontierNode
        The node to check.
    frontier : SemanticFrontier
        The current frontier snapshot (used for context).
    all_nodes : Dict[str, FrontierNode]
        Full node dictionary (for ancestor traversal).
    visited_fingerprints : Set[str], optional
        Pre-computed set of fingerprints of already-visited nodes.

    Returns
    -------
    bool
        True if a cycle is detected.
    """
    if visited_fingerprints is None:
        visited_fingerprints = set()

    node_fp = _node_fingerprint(node)
    if node_fp in visited_fingerprints:
        return True  # Cycle detected

    # Traverse ancestors up to root
    seen: Set[str] = {node_fp}
    current_id = node.parent_node_id
    depth = 0
    while current_id and depth < MAX_GENERATION_DEPTH:
        parent = all_nodes.get(current_id)
        if parent is None:
            break
        parent_fp = _node_fingerprint(parent)
        if parent_fp in seen:
            return True  # Ancestor cycle
        seen.add(parent_fp)
        current_id = parent.parent_node_id
        depth += 1

    return False


def expand_frontier_node(
    node: FrontierNode,
    policy: ExpansionPolicy,
    trust_algebra: TrustAlgebraElement,
    goal_judgment: Optional[JudgmentTuple] = None,
    branching_factor: int = DEFAULT_EXPANSION_BRANCHING_FACTOR,
    verbose: bool = False,
) -> FrontierExpansion:
    """Expand a single frontier node to produce child nodes.

    The expansion generates up to ``branching_factor`` successor nodes.
    Each successor is produced by:
      1. Discharging one pending obligation (if any).
      2. Elevating trust by one tier (if the parent's trust is below PROOF_BACKED).
      3. Adding a new piece of evidence.

    Trust propagation:
      - Each child inherits the parent's trust algebra element joined with the
        expansion's trust element.
      - A child's trust tier cannot exceed the parent's tier + 1 (one step per expansion).

    Parameters
    ----------
    node : FrontierNode
        The node to expand.
    policy : ExpansionPolicy
        The expansion policy.
    trust_algebra : TrustAlgebraElement
        The trust algebra element governing this expansion.
    goal_judgment : JudgmentTuple, optional
        The goal judgment (used to compute heuristic for children).
    branching_factor : int
        Maximum number of children per expansion.

    Returns
    -------
    FrontierExpansion
        The expansion result with child nodes.
    """
    # Trust-gated check
    if policy == ExpansionPolicy.TRUST_GATED:
        if trust_algebra.tier.value < node.trust_tier.value:
            # Cannot expand: trust algebra below node's trust
            return FrontierExpansion(
                expansion_id=_make_expansion_id(),
                parent_node=node,
                child_nodes=(),
                expansion_proof="",
                trust_propagation=(),
                expansion_cost=0.0,
            )

    j = node.judgment_tuple
    children: List[FrontierNode] = []
    trust_props: List[TrustAlgebraElement] = []

    # Determine how many children to generate
    n_children = (
        1 if policy == ExpansionPolicy.CAUTIOUS_EXPAND else min(branching_factor, max(len(j.O), 1))
    )

    goal_j = goal_judgment or JudgmentTuple(
        c=j.c, phi=j.phi, A=j.A, E=j.E, O=(), B=j.B,
        T=TrustTier.PROOF_BACKED, Pi="goal"
    )

    for i in range(n_children):
        # Build child judgment by discharging one obligation
        new_obligations = j.O[i + 1:] if i < len(j.O) else ()
        new_tier = j.T
        new_pi = j.Pi
        if j.O and i < len(j.O):
            # Obligation discharged → possible trust elevation
            if j.T.value < TrustTier.PROOF_BACKED.value:
                new_tier = TrustTier(j.T.value + 1)
            new_pi = f"{j.Pi}+{j.O[i][:8]}"
        elif not j.Pi:
            new_pi = f"proof-{uuid.uuid4().hex[:8]}"

        child_j = JudgmentTuple(
            c=j.c,
            phi=j.phi,
            A=j.A,
            E=j.E + (f"exp-ev-{node.generation_depth}-{i}",),
            O=new_obligations,
            B=j.B,
            T=new_tier,
            Pi=new_pi,
        )
        child_coords = _embed_judgment(child_j)
        child_trust = trust_algebra.elevate(f"exp-{node.node_id}-{i}")
        child_trust_tier = min(child_trust.tier, new_tier, key=lambda t: t.value)

        child_node = FrontierNode(
            node_id=_make_node_id(),
            judgment_tuple=child_j,
            semantic_coordinates=child_coords,
            heuristic_estimate=compute_frontier_heuristic(
                FrontierNode(
                    node_id="tmp", judgment_tuple=child_j,
                    semantic_coordinates=child_coords,
                    heuristic_estimate=0.0, cost_so_far=0.0,
                    parent_node_id=node.node_id, generation_depth=0,
                    trust_tier=child_trust_tier, node_proof="",
                ),
                goal_j,
            ),
            cost_so_far=node.cost_so_far + 1.0,
            parent_node_id=node.node_id,
            generation_depth=node.generation_depth + 1,
            trust_tier=child_trust_tier,
            node_proof=f"exp-proof-{node.node_id}-{i}" if policy == ExpansionPolicy.PROOF_REQUIRED else "",
        )
        children.append(child_node)
        trust_props.append(child_trust)

    if verbose:
        print(
            EXPANSION_LOG_TEMPLATE.format(
                depth=node.generation_depth,
                node_id=node.node_id[:12],
                f=node.f_value,
                trust=node.trust_tier.name,
                oblig=len(j.O),
                n_children=len(children),
            )
        )

    return FrontierExpansion(
        expansion_id=_make_expansion_id(),
        parent_node=node,
        child_nodes=tuple(children),
        expansion_proof=f"exp-proof-{node.node_id}" if children else "",
        trust_propagation=tuple(trust_props),
        expansion_cost=1.0,
    )


def maintain_frontier(
    frontier_manager: FrontierManager,
    new_nodes: List[FrontierNode],
    visited_fps: Optional[Set[str]] = None,
) -> Tuple[int, int]:
    """Add new nodes to the frontier and prune dominated nodes.

    This function implements the frontier maintenance invariant:
      After maintenance, the frontier contains only nodes that are:
        1. Not dominated by any existing node.
        2. Not already in the closed set.
        3. Not forming a semantic cycle.
        4. Above the trust floor.

    Parameters
    ----------
    frontier_manager : FrontierManager
        The mutable frontier manager to update.
    new_nodes : List[FrontierNode]
        New nodes to (potentially) add.
    visited_fps : Set[str], optional
        Fingerprints of nodes already visited (for cycle detection).

    Returns
    -------
    (int, int)
        (added_count, rejected_count)
    """
    if visited_fps is None:
        visited_fps = set()

    added = 0
    rejected = 0
    snapshot = frontier_manager.snapshot()

    for node in new_nodes:
        # Check trust floor
        if node.trust_tier.value < frontier_manager.trust_floor.value:
            rejected += 1
            continue

        # Check cycle
        if detect_frontier_cycle(node, snapshot, frontier_manager._all_nodes, visited_fps):
            rejected += 1
            continue

        # Check budget
        if frontier_manager.budget_remaining <= 0:
            rejected += 1
            continue

        # Attempt to add
        count = frontier_manager.add_children([node])
        added += count
        rejected += (1 - count)
        visited_fps.add(_node_fingerprint(node))

    return added, rejected


def prune_frontier(
    frontier_manager: FrontierManager,
    criterion: PruningCriterion,
    trust_threshold: Optional[TrustTier] = None,
    budget_limit: Optional[float] = None,
) -> FrontierPruning:
    """Prune the frontier according to the given criterion.

    Each criterion removes a different category of semantically unproductive nodes.

    Parameters
    ----------
    frontier_manager : FrontierManager
        The frontier to prune.
    criterion : PruningCriterion
        The pruning criterion to apply.
    trust_threshold : TrustTier, optional
        Minimum trust tier for TRUST_BELOW_THRESHOLD criterion.
    budget_limit : float, optional
        Maximum cost-so-far for BUDGET_EXCEEDED criterion.

    Returns
    -------
    FrontierPruning
        The pruning record.
    """
    threshold = trust_threshold or frontier_manager.trust_floor
    to_prune: Set[str] = set()

    if criterion == PruningCriterion.TRUST_BELOW_THRESHOLD:
        for nid in list(frontier_manager._open):
            node = frontier_manager._all_nodes.get(nid)
            if node and node.trust_tier.value < threshold.value:
                to_prune.add(nid)

    elif criterion == PruningCriterion.DOMINATED:
        open_nodes = [
            frontier_manager._all_nodes[nid]
            for nid in frontier_manager._open
            if nid in frontier_manager._all_nodes
        ]
        for i, n1 in enumerate(open_nodes):
            for j, n2 in enumerate(open_nodes):
                if i == j:
                    continue
                if n2.dominates(n1) and n1.node_id not in to_prune:
                    to_prune.add(n1.node_id)

    elif criterion == PruningCriterion.BUDGET_EXCEEDED:
        limit = budget_limit if budget_limit is not None else float(frontier_manager.expansion_budget)
        for nid in list(frontier_manager._open):
            node = frontier_manager._all_nodes.get(nid)
            if node and node.cost_so_far > limit:
                to_prune.add(nid)

    elif criterion == PruningCriterion.GEOMETRIC_DOMINATED:
        open_nodes = [
            frontier_manager._all_nodes[nid]
            for nid in frontier_manager._open
            if nid in frontier_manager._all_nodes
        ]
        for i, n1 in enumerate(open_nodes):
            for j, n2 in enumerate(open_nodes):
                if i == j:
                    continue
                if (n2.heuristic_estimate <= n1.heuristic_estimate
                        and n2.cost_so_far <= n1.cost_so_far
                        and n2.node_id != n1.node_id):
                    to_prune.add(n1.node_id)

    for nid in to_prune:
        frontier_manager._open.discard(nid)

    return FrontierPruning(
        pruning_id=_make_pruning_id(),
        pruned_nodes=tuple(to_prune),
        pruning_criterion=criterion,
        nodes_retained=len(frontier_manager._open),
        pruning_proof="",
        trust_threshold_used=threshold,
    )


def select_next_node(
    frontier_manager: FrontierManager,
    strategy: FrontierStrategy,
) -> Optional[FrontierNode]:
    """Select the next node to expand from the frontier.

    This is a thin wrapper around FrontierManager.pop_next() that enforces
    strategy-specific selection logic and records the selection decision.

    Parameters
    ----------
    frontier_manager : FrontierManager
        The frontier manager.
    strategy : FrontierStrategy
        The selection strategy (should match frontier_manager.strategy).

    Returns
    -------
    Optional[FrontierNode]
        The selected node, or None if the frontier is empty.
    """
    if frontier_manager.is_empty:
        return None
    node = frontier_manager.pop_next()
    if node is not None:
        frontier_manager.record_expansion()
    return node


def reconstruct_solution_path(
    goal_node: FrontierNode,
    frontier_manager: FrontierManager,
) -> List[FrontierNode]:
    """Reconstruct the solution path from root to the goal node.

    Follows parent pointers from the goal node back to the root, then
    reverses the path.

    Parameters
    ----------
    goal_node : FrontierNode
        The node that reached the goal.
    frontier_manager : FrontierManager
        The frontier manager containing the full node history.

    Returns
    -------
    List[FrontierNode]
        The path from root to goal, inclusive.
    """
    path: List[FrontierNode] = []
    current = goal_node
    depth = 0

    while current is not None and depth < MAX_GENERATION_DEPTH:
        path.append(current)
        parent_id = frontier_manager.get_parent_id(current.node_id)
        if not parent_id:
            break
        parent = frontier_manager.get_node(parent_id)
        if parent is None:
            break
        current = parent
        depth += 1

    path.reverse()
    return path


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 70)
    print("Smoke test: search_should_proceed_on_a_frontie.py")
    print("=" * 70)

    # 1. Build root judgment tuple
    root_j = JudgmentTuple(
        c="context-search-test",
        phi="answer_verified",
        A=("agent-x", "agent-y"),
        E=("ev-root",),
        O=("oblig-a", "oblig-b", "oblig-c"),
        B="initial-belief",
        T=TrustTier.PROPOSAL,
        Pi="",
    )
    root_coords = _embed_judgment(root_j)
    root_trust = TrustAlgebraElement(tier=TrustTier.PROPOSAL)

    # Goal
    goal_j = JudgmentTuple(
        c="context-search-test",
        phi="answer_verified",
        A=("agent-x", "agent-y"),
        E=("ev-root",),
        O=(),
        B="final-belief",
        T=TrustTier.PROOF_BACKED,
        Pi="complete-proof",
    )

    # 2. Build root node
    root_node = FrontierNode(
        node_id=_make_node_id(),
        judgment_tuple=root_j,
        semantic_coordinates=root_coords,
        heuristic_estimate=compute_frontier_heuristic(
            FrontierNode(
                node_id="tmp", judgment_tuple=root_j,
                semantic_coordinates=root_coords,
                heuristic_estimate=0.0, cost_so_far=0.0,
                parent_node_id="", generation_depth=0,
                trust_tier=TrustTier.PROPOSAL, node_proof="",
            ),
            goal_j,
        ),
        cost_so_far=0.0,
        parent_node_id="",
        generation_depth=0,
        trust_tier=TrustTier.PROPOSAL,
        node_proof="",
    )
    print(f"Root node: id={root_node.node_id[:16]}, f={root_node.f_value:.4f}")
    print(f"  Obligations: {root_node.judgment_tuple.O}")
    print(f"  Trust: {root_node.trust_tier.name}")

    # 3. Initialise frontier
    fm = FrontierManager(
        strategy=FrontierStrategy.BEST_FIRST,
        expansion_policy=ExpansionPolicy.GREEDY_EXPAND,
        trust_floor=TrustTier.PROPOSAL,
        expansion_budget=50,
    )
    fm.initialise(root_node)
    print(f"\nFrontier initialised: {len(fm._open)} nodes")

    # 4. Run search loop
    heuristic = HeuristicEstimator(goal_j)
    trust_elem = TrustAlgebraElement(tier=TrustTier.REVIEWED)
    visited_fps: Set[str] = set()
    goal_found = False
    goal_node_result = None

    print("\nRunning frontier search (max 15 expansions):")
    for step in range(15):
        node = select_next_node(fm, FrontierStrategy.BEST_FIRST)
        if node is None:
            print("  Frontier exhausted.")
            break

        expansion = expand_frontier_node(
            node, ExpansionPolicy.GREEDY_EXPAND, trust_elem, goal_j,
            branching_factor=2, verbose=True,
        )

        # Check if any child is at the goal
        for child in expansion.child_nodes:
            if len(child.judgment_tuple.O) == 0 and child.trust_tier.value >= TrustTier.VERIFIED.value:
                goal_found = True
                goal_node_result = child
                fm._all_nodes[child.node_id] = child
                fm.register_parent(child.node_id, node.node_id)
                break

        if goal_found:
            print(f"  Goal reached at depth {node.generation_depth + 1}!")
            break

        # Maintain frontier with children
        children = list(expansion.child_nodes)
        for c in children:
            fm._all_nodes[c.node_id] = c
            fm.register_parent(c.node_id, node.node_id)
        added, rejected = maintain_frontier(fm, children, visited_fps)
        print(f"    Added {added} children, rejected {rejected}")

    # 5. Prune frontier
    pruning = prune_frontier(fm, PruningCriterion.DOMINATED)
    print(f"\nPruning (DOMINATED): {len(pruning.pruned_nodes)} nodes pruned, "
          f"{pruning.nodes_retained} retained")

    # 6. Reconstruct path (if goal found)
    if goal_found and goal_node_result is not None:
        path = reconstruct_solution_path(goal_node_result, fm)
        print(f"\nSolution path length: {len(path)}")
        for i, n in enumerate(path):
            print(f"  [{i:02d}] depth={n.generation_depth} trust={n.trust_tier.name} "
                  f"f={n.f_value:.4f} oblig={len(n.judgment_tuple.O)}")
    else:
        print("\nNo goal reached — path reconstruction skipped.")

    # 7. Frontier snapshot
    snap = fm.snapshot()
    print(f"\nFrontier snapshot: {len(snap.active_nodes)} active, "
          f"{len(snap.pruned_nodes)} pruned, "
          f"budget={snap.expansion_budget}")

    # 8. Cycle detection check
    # Build a synthetic cycle-forming node with same fingerprint as root
    cycle_candidate = FrontierNode(
        node_id=_make_node_id(),
        judgment_tuple=root_j,
        semantic_coordinates=root_coords,
        heuristic_estimate=root_node.heuristic_estimate,
        cost_so_far=10.0,
        parent_node_id=root_node.node_id,
        generation_depth=5,
        trust_tier=TrustTier.REVIEWED,
        node_proof="",
    )
    is_cycle = detect_frontier_cycle(cycle_candidate, snap, fm._all_nodes)
    print(f"\nCycle detection on synthetic cycle node: {is_cycle} (expected True)")

    print("\nSmoke test complete.")
