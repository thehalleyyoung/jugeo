"""Frontier management for JuGeo orchestration.

Implements the frontier search framework described in theory2.tex
("Frontier algorithms and phase transitions").  The frontier is a search
object over semantically admissible futures.  A *FrontierNode* represents a
successor state annotated with predicted closure gain, stability gain,
theorem yield, treaty impact, cost, uncertainty, and support scope.
Managing the frontier means balancing expected closure gain against
stability, diversity, and backpressure so that the orchestration controller
can drive judgement-geometry exploration efficiently.

The module is organised into ten cooperating classes:

* **FrontierNode** – rich dataclass for a single search node.
* **Frontier** – ordered collection with scoring, pruning, and merging.
* **FrontierSearch** – pluggable search strategies (beam, MCTS, …).
* **FrontierScorer** – composite scoring with Pareto support.
* **PhaseTransition** – detection and classification of phase changes.
* **BackpressureController** – adaptive rate-limiting across channels.
* **FrontierDiversity** – clustering and novelty enforcement.
* **FrontierBudget** – cost-aware pruning and budget allocation.
* **FrontierHistory** – temporal tracking of frontier evolution.
* **FrontierDiagnostics** – human- and copilot-readable reports.

Backward-compatible *FrontierItem* and *FrontierState* are preserved so
that existing callers (controller, fleet, negotiation, diagnostics) keep
working without modification.

copilot: shared-core marker for LLM-assisted frontier orchestration.
"""

from __future__ import annotations

import math
import random
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Sequence

from jugeo.generation.goals import ConstructionGoal

# ── Cross-subsystem imports (guarded) ─────────────────────────────────────
try:
    from jugeo.encodings.structural_frontier import StructuralFrontier, DecidabilityClass
except Exception:  # pragma: no cover
    StructuralFrontier = None  # type: ignore[assignment,misc]
    DecidabilityClass = None  # type: ignore[assignment,misc]

try:
    from jugeo.ideation.novelty import NoveltySearcher
except Exception:  # pragma: no cover
    NoveltySearcher = None  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Backward-compatible legacy types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FrontierItem:
    """Legacy frontier item retained for backward compatibility.

    Used by ``OrchestrationController.decide``, ``FleetState.assign``,
    ``NegotiationRound.resolve``, and associated tests.
    """

    goal: ConstructionGoal
    urgency: int = 0
    obstruction_rank: int = 0


@dataclass(slots=True)
class FrontierState:
    """Legacy mutable frontier queue retained for backward compatibility.

    Selection key: ``(urgency DESC, priority DESC, budget ASC)``.
    """

    items: list[FrontierItem] = field(default_factory=list)

    def add(self, item: FrontierItem) -> None:
        """Append *item* to the frontier."""
        self.items.append(item)

    def next_item(self) -> FrontierItem | None:
        """Pop and return the highest-priority item, or ``None``."""
        if not self.items:
            return None
        best = max(
            self.items,
            key=lambda item: (item.urgency, item.goal.priority, -item.goal.budget),
        )
        self.items.remove(best)
        return best


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class PhaseKind(Enum):
    """Classifies the current phase of frontier evolution."""

    EXPLORATION = auto()
    EXPLOITATION = auto()
    COLLAPSE = auto()
    RECOVERY = auto()
    SATURATION = auto()


class TransitionTrigger(Enum):
    """Possible triggers for a phase transition."""

    DIVERSITY_DROP = auto()
    CLOSURE_SPIKE = auto()
    BUDGET_EXHAUSTION = auto()
    STABILITY_LOSS = auto()
    EXTERNAL_SIGNAL = auto()
    BACKPRESSURE_OVERFLOW = auto()


# ---------------------------------------------------------------------------
# 1. FrontierNode
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class FrontierNode:
    """A single node in the semantic frontier search tree.

    Each node records the predicted impact of a candidate move on the
    judgment-geometry lattice together with cost, uncertainty, and the
    support scope (the set of coordinate patches affected).

    Attributes
    ----------
    node_id : str
        Unique identifier (UUID-based).
    semantic_state_hash : str
        Hash fingerprint of the semantic state this node leads to.
    predecessor_id : str | None
        Parent node in the search tree (``None`` for root nodes).
    move_that_produced : str
        Human-readable label of the move / action taken.
    predicted_closure_gain : float
        Expected increase in global closure metric ∈ [0, 1].
    predicted_stability_gain : float
        Expected increase in lattice stability ∈ [-1, 1].
    predicted_theorem_yield : float
        Likelihood that this path produces a verified theorem ∈ [0, 1].
    treaty_impact : float
        Net effect on inter-patch treaty satisfaction ∈ [-1, 1].
    estimated_cost : float
        Computational / resource cost to expand this node (≥ 0).
    uncertainty : float
        Epistemic uncertainty of the predictions ∈ [0, 1].
    support_scope : frozenset[str]
        Names of coordinate patches in the support region.
    depth : int
        Depth in the search tree (root = 0).
    is_terminal : bool
        ``True`` when no further expansion is possible.
    created_at : float
        Timestamp (``time.monotonic()``).
    expansion_count : int
        How many times this node has been expanded (for MCTS).
    cumulative_reward : float
        Sum of rewards collected through this node (for MCTS).
    """

    node_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    semantic_state_hash: str = ""
    predecessor_id: str | None = None
    move_that_produced: str = ""
    predicted_closure_gain: float = 0.0
    predicted_stability_gain: float = 0.0
    predicted_theorem_yield: float = 0.0
    treaty_impact: float = 0.0
    estimated_cost: float = 0.0
    uncertainty: float = 0.5
    support_scope: frozenset[str] = field(default_factory=frozenset)
    depth: int = 0
    is_terminal: bool = False
    created_at: float = field(default_factory=time.monotonic)
    expansion_count: int = 0
    cumulative_reward: float = 0.0

    # -- convenience helpers -------------------------------------------------

    def effective_closure(self) -> float:
        """Closure gain discounted by uncertainty."""
        return self.predicted_closure_gain * (1.0 - self.uncertainty)

    def reward_rate(self) -> float:
        """Average reward per expansion (guards against division by zero)."""
        if self.expansion_count == 0:
            return 0.0
        return self.cumulative_reward / self.expansion_count

    def cost_efficiency(self) -> float:
        """Closure gain per unit cost (returns ``inf`` when cost is zero)."""
        if self.estimated_cost <= 0.0:
            return float("inf")
        return self.predicted_closure_gain / self.estimated_cost

    def dominates(self, other: FrontierNode) -> bool:
        """Return ``True`` if *self* Pareto-dominates *other*.

        A node dominates another when it is at least as good on every
        objective and strictly better on at least one.
        """
        objectives_self = (
            self.predicted_closure_gain,
            self.predicted_stability_gain,
            self.predicted_theorem_yield,
            self.treaty_impact,
            -self.estimated_cost,
        )
        objectives_other = (
            other.predicted_closure_gain,
            other.predicted_stability_gain,
            other.predicted_theorem_yield,
            other.treaty_impact,
            -other.estimated_cost,
        )
        at_least = all(s >= o for s, o in zip(objectives_self, objectives_other))
        strictly_better = any(s > o for s, o in zip(objectives_self, objectives_other))
        return at_least and strictly_better

    def summary(self) -> dict[str, Any]:
        """Return a compact dictionary summary (copilot-friendly)."""
        return {
            "id": self.node_id,
            "depth": self.depth,
            "closure": round(self.predicted_closure_gain, 4),
            "stability": round(self.predicted_stability_gain, 4),
            "theorem_yield": round(self.predicted_theorem_yield, 4),
            "treaty": round(self.treaty_impact, 4),
            "cost": round(self.estimated_cost, 4),
            "uncertainty": round(self.uncertainty, 4),
            "terminal": self.is_terminal,
        }

    # ── cross-subsystem integration ─────────────────────────────────────

    def encoding_frontier(self) -> dict[str, Any]:
        """Classify this node's decidability via StructuralFrontier.

        Uses :mod:`jugeo.encodings.structural_frontier` to determine
        whether the specification encoded by this node falls on the
        decidable, semi-decidable, or undecidable side of the structural
        frontier, guiding the orchestrator's expansion strategy.

        Returns a dict with decidability classification and repair hints.

        Theory ref: theory2.tex §4 — Encoding Structural Frontier.
        """
        if StructuralFrontier is None:
            return {"status": "unavailable", "decidability": "unknown"}

        sf = StructuralFrontier()
        classification = sf.classify(
            state_hash=self.semantic_state_hash,
            support_scope=self.support_scope,
        )
        decidability = getattr(classification, "decidability", "unknown")
        repair_hints = getattr(classification, "repair_hints", [])
        return {
            "status": "ok",
            "decidability": str(decidability),
            "repair_hints": list(repair_hints),
            "node_id": self.node_id,
        }


# ---------------------------------------------------------------------------
# 2. Frontier
# ---------------------------------------------------------------------------


class Frontier:
    """Ordered frontier of *FrontierNode* objects.

    The frontier supports insertion, removal, top-k retrieval, filtering,
    pruning, merging, diversity measurement, and depth-based queries.
    """

    def __init__(self, scorer: FrontierScorer | None = None) -> None:
        self._nodes: dict[str, FrontierNode] = {}
        self._scorer: FrontierScorer = scorer or FrontierScorer()
        self._phase: PhaseKind = PhaseKind.EXPLORATION

    # -- core CRUD -----------------------------------------------------------

    def add_node(self, node: FrontierNode) -> None:
        """Insert *node* into the frontier (replaces if same id exists)."""
        self._nodes[node.node_id] = node

    def remove_node(self, node_id: str) -> FrontierNode | None:
        """Remove and return the node with *node_id*, or ``None``."""
        return self._nodes.pop(node_id, None)

    def get_node(self, node_id: str) -> FrontierNode | None:
        """Retrieve a node without removing it."""
        return self._nodes.get(node_id)

    def best_node(self) -> FrontierNode | None:
        """Return the highest-scoring non-terminal node without removal."""
        candidates = [n for n in self._nodes.values() if not n.is_terminal]
        if not candidates:
            return None
        return max(candidates, key=self._scorer.composite_score)

    def top_k(self, k: int = 5) -> list[FrontierNode]:
        """Return the *k* highest-scoring non-terminal nodes."""
        candidates = [n for n in self._nodes.values() if not n.is_terminal]
        candidates.sort(key=self._scorer.composite_score, reverse=True)
        return candidates[:k]

    def filter_by(
        self,
        predicate: Callable[[FrontierNode], bool],
    ) -> list[FrontierNode]:
        """Return all nodes satisfying *predicate*."""
        return [n for n in self._nodes.values() if predicate(n)]

    def prune(self, max_size: int) -> list[FrontierNode]:
        """Shrink the frontier to *max_size*, removing lowest-scoring nodes.

        Returns the pruned nodes so callers can log or archive them.
        """
        if len(self._nodes) <= max_size:
            return []
        ranked = sorted(
            self._nodes.values(),
            key=self._scorer.composite_score,
            reverse=True,
        )
        survivors = {n.node_id: n for n in ranked[:max_size]}
        pruned = [n for n in ranked[max_size:]]
        self._nodes = survivors
        return pruned

    def size(self) -> int:
        """Number of nodes currently in the frontier."""
        return len(self._nodes)

    def is_empty(self) -> bool:
        """``True`` when the frontier contains no nodes."""
        return len(self._nodes) == 0

    def merge_frontier(self, other: Frontier) -> int:
        """Merge *other* into this frontier, keeping higher-scored duplicates.

        Returns the number of nodes actually added or updated.
        """
        changed = 0
        for node in other._nodes.values():
            existing = self._nodes.get(node.node_id)
            if existing is None:
                self._nodes[node.node_id] = node
                changed += 1
            elif self._scorer.composite_score(node) > self._scorer.composite_score(existing):
                self._nodes[node.node_id] = node
                changed += 1
        return changed

    def diversity_score(self) -> float:
        """Jaccard-based diversity over support scopes.

        Returns a value in [0, 1] where 1 means every node covers a
        completely distinct set of patches.
        """
        nodes = list(self._nodes.values())
        if len(nodes) < 2:
            return 1.0
        total_jaccard = 0.0
        pairs = 0
        for i in range(len(nodes)):
            for j in range(i + 1, len(nodes)):
                a = nodes[i].support_scope
                b = nodes[j].support_scope
                union = a | b
                if not union:
                    total_jaccard += 1.0
                else:
                    total_jaccard += 1.0 - len(a & b) / len(union)
                pairs += 1
        return total_jaccard / pairs if pairs else 1.0

    def nodes_at_depth(self, depth: int) -> list[FrontierNode]:
        """Return all nodes at the given *depth*."""
        return [n for n in self._nodes.values() if n.depth == depth]

    def all_nodes(self) -> list[FrontierNode]:
        """Return a snapshot list of all current nodes."""
        return list(self._nodes.values())

    @property
    def nodes(self) -> list[FrontierNode]:
        return self.all_nodes()

    @property
    def phase(self) -> PhaseKind:
        return self._phase

    def trigger_transition(self, trigger: TransitionTrigger) -> PhaseKind:
        transitions = {
            TransitionTrigger.DIVERSITY_DROP: PhaseKind.EXPLOITATION,
            TransitionTrigger.CLOSURE_SPIKE: PhaseKind.EXPLOITATION,
            TransitionTrigger.BUDGET_EXHAUSTION: PhaseKind.COLLAPSE,
            TransitionTrigger.STABILITY_LOSS: PhaseKind.RECOVERY,
            TransitionTrigger.EXTERNAL_SIGNAL: PhaseKind.RECOVERY,
            TransitionTrigger.BACKPRESSURE_OVERFLOW: PhaseKind.SATURATION,
        }
        self._phase = transitions.get(trigger, self._phase)
        return self._phase

    # ── cross-subsystem integration ─────────────────────────────────────

    def ideation_expansion(self, *, top_k: int = 5) -> list[FrontierNode]:
        """Expand the frontier with novel directions from the ideation subsystem.

        Uses :class:`jugeo.ideation.novelty.NoveltySearcher` to score
        existing frontier nodes by novelty relative to the current
        portfolio, then synthesises new candidate nodes in under-explored
        directions.  This prevents the frontier from collapsing onto a
        narrow exploitation band.

        Parameters
        ----------
        top_k
            Maximum number of novel expansion nodes to produce.

        Returns the list of newly created :class:`FrontierNode` instances
        (already added to the frontier).

        Theory ref: theory2.tex §3.4 — Ideation-Guided Exploration.
        """
        if NoveltySearcher is None:
            return []

        searcher = NoveltySearcher()
        existing = self.all_nodes()
        portfolio = [
            {"node_id": n.node_id, "scope": list(n.support_scope), "depth": n.depth}
            for n in existing
        ]
        suggestions = searcher.suggest(portfolio=portfolio, top_k=top_k)
        new_nodes: list[FrontierNode] = []
        for suggestion in suggestions:
            node = FrontierNode(
                semantic_state_hash=getattr(suggestion, "state_hash", ""),
                predecessor_id=getattr(suggestion, "predecessor_id", None),
                move_that_produced=f"ideation:{getattr(suggestion, 'label', 'novel')}",
                predicted_closure_gain=getattr(suggestion, "predicted_gain", 0.1),
                predicted_stability_gain=0.0,
                predicted_theorem_yield=getattr(suggestion, "theorem_yield", 0.05),
                treaty_impact=0.0,
                estimated_cost=getattr(suggestion, "cost", 1.0),
                uncertainty=getattr(suggestion, "uncertainty", 0.7),
                support_scope=frozenset(getattr(suggestion, "scope", [])),
                depth=getattr(suggestion, "depth", 0) + 1,
            )
            self.add_node(node)
            new_nodes.append(node)
        return new_nodes


# ---------------------------------------------------------------------------
# 3. FrontierSearch
# ---------------------------------------------------------------------------


class FrontierSearch:
    """Pluggable search strategies over a *Frontier*.

    Each method expands the frontier according to a different policy and
    returns the sequence of nodes selected for expansion.
    """

    def __init__(
        self,
        frontier: Frontier,
        expand_fn: Callable[[FrontierNode], list[FrontierNode]] | None = None,
        scorer: FrontierScorer | None = None,
    ) -> None:
        self._frontier = frontier
        self._expand = expand_fn or (lambda _node: [])
        self._scorer = scorer or FrontierScorer()
        self._rng = random.Random(42)
        self.strategy = "greedy_best_first"

    def greedy_best_first(self, max_expansions: int = 100) -> list[FrontierNode]:
        """Greedily expand the single best node each iteration.

        Returns the ordered list of nodes that were expanded.
        """
        expanded: list[FrontierNode] = []
        for _ in range(max_expansions):
            best = self._frontier.best_node()
            if best is None:
                break
            self._frontier.remove_node(best.node_id)
            best.is_terminal = True
            children = self._expand(best)
            for child in children:
                self._frontier.add_node(child)
            expanded.append(best)
        return expanded

    def beam_search(
        self,
        beam_width: int = 5,
        max_depth: int = 10,
    ) -> list[FrontierNode]:
        """Beam search keeping *beam_width* candidates at each depth level.

        Returns the terminal or deepest-reached nodes.
        """
        current_level = self._frontier.top_k(beam_width)
        best_terminals: list[FrontierNode] = []

        for depth_step in range(max_depth):
            next_level: list[FrontierNode] = []
            for node in current_level:
                if node.is_terminal:
                    best_terminals.append(node)
                    continue
                self._frontier.remove_node(node.node_id)
                children = self._expand(node)
                for child in children:
                    self._frontier.add_node(child)
                next_level.extend(children)
            if not next_level:
                break
            next_level.sort(key=self._scorer.composite_score, reverse=True)
            current_level = next_level[:beam_width]

        best_terminals.extend(current_level)
        return best_terminals

    def monte_carlo_tree_search(
        self,
        iterations: int = 200,
        exploration_constant: float = 1.414,
    ) -> FrontierNode | None:
        """MCTS with UCB1 selection over frontier nodes.

        Runs *iterations* rounds of select → expand → simulate → backprop
        and returns the most-visited root child.
        """
        nodes = self._frontier.all_nodes()
        if not nodes:
            return None

        total_visits = sum(n.expansion_count for n in nodes) or 1

        for _ in range(iterations):
            # Selection via UCB1
            def ucb1(n: FrontierNode) -> float:
                if n.expansion_count == 0:
                    return float("inf")
                exploit = n.cumulative_reward / n.expansion_count
                explore = exploration_constant * math.sqrt(
                    math.log(total_visits) / n.expansion_count
                )
                return exploit + explore

            selected = max(nodes, key=ucb1)

            # Expansion
            if not selected.is_terminal:
                children = self._expand(selected)
                if children:
                    child = self._rng.choice(children)
                    self._frontier.add_node(child)
                    nodes.append(child)
                    selected = child

            # Simulation – use composite score as a cheap rollout proxy
            reward = self._scorer.composite_score(selected)

            # Backpropagation
            selected.expansion_count += 1
            selected.cumulative_reward += reward
            total_visits += 1

        # Return the most-visited node among the original root-level set
        root_nodes = [n for n in nodes if n.depth == 0]
        if not root_nodes:
            root_nodes = nodes
        return max(root_nodes, key=lambda n: n.expansion_count)

    def iterative_deepening(
        self,
        max_depth: int = 15,
        score_threshold: float = 0.0,
    ) -> list[FrontierNode]:
        """Iterative-deepening search with a score threshold cutoff.

        At each depth limit, nodes scoring below *score_threshold* are
        skipped.  Returns all expanded nodes across all depth rounds.
        """
        all_expanded: list[FrontierNode] = []

        for depth_limit in range(1, max_depth + 1):
            candidates = [
                n
                for n in self._frontier.all_nodes()
                if n.depth < depth_limit
                and not n.is_terminal
                and self._scorer.composite_score(n) >= score_threshold
            ]
            if not candidates:
                break
            for node in candidates:
                self._frontier.remove_node(node.node_id)
                children = self._expand(node)
                for child in children:
                    self._frontier.add_node(child)
                all_expanded.append(node)
        return all_expanded

    def adaptive_search(
        self,
        max_expansions: int = 100,
        diversity_floor: float = 0.3,
    ) -> list[FrontierNode]:
        """Switch between greedy and diversity-aware expansion adaptively.

        When diversity falls below *diversity_floor*, the search picks the
        most *novel* node (highest uncertainty × coverage) instead of the
        best-scoring one.  This prevents premature convergence.
        """
        expanded: list[FrontierNode] = []

        for _ in range(max_expansions):
            diversity = self._frontier.diversity_score()

            if diversity >= diversity_floor:
                node = self._frontier.best_node()
            else:
                # copilot: diversity-aware fallback – prefer novel nodes
                candidates = [
                    n for n in self._frontier.all_nodes() if not n.is_terminal
                ]
                if not candidates:
                    break
                node = max(
                    candidates,
                    key=lambda n: n.uncertainty * len(n.support_scope or {"_"}),
                )
            if node is None:
                break

            self._frontier.remove_node(node.node_id)
            children = self._expand(node)
            for child in children:
                self._frontier.add_node(child)
            expanded.append(node)
        return expanded

    def copilot_guided_search(
        self,
        hint_scores: dict[str, float] | None = None,
        max_expansions: int = 50,
    ) -> list[FrontierNode]:
        """Search guided by external copilot hint scores.

        *hint_scores* maps ``node_id`` → bonus score supplied by the LLM
        copilot.  Nodes with higher bonuses are expanded first, blending
        machine-learned intuition with the algebraic scorer.

        When no hints are provided, falls back to greedy best-first.
        """
        hints = hint_scores or {}
        expanded: list[FrontierNode] = []

        for _ in range(max_expansions):
            candidates = [
                n for n in self._frontier.all_nodes() if not n.is_terminal
            ]
            if not candidates:
                break

            def guided_score(n: FrontierNode) -> float:
                base = self._scorer.composite_score(n)
                bonus = hints.get(n.node_id, 0.0)
                return base + bonus

            best = max(candidates, key=guided_score)
            self._frontier.remove_node(best.node_id)
            children = self._expand(best)
            for child in children:
                self._frontier.add_node(child)
            expanded.append(best)
        return expanded


# ---------------------------------------------------------------------------
# 4. FrontierScorer
# ---------------------------------------------------------------------------


class FrontierScorer:
    """Multi-objective scorer for frontier nodes.

    The default weights can be overridden at construction time or per-call
    to explore different trade-off surfaces.
    """

    def __init__(
        self,
        closure_weight: float = 0.35,
        stability_weight: float = 0.25,
        theorem_weight: float = 0.15,
        treaty_weight: float = 0.10,
        cost_weight: float = 0.10,
        uncertainty_weight: float = 0.05,
    ) -> None:
        self.closure_weight = closure_weight
        self.stability_weight = stability_weight
        self.theorem_weight = theorem_weight
        self.treaty_weight = treaty_weight
        self.cost_weight = cost_weight
        self.uncertainty_weight = uncertainty_weight

    def composite_score(self, node: FrontierNode) -> float:
        """Weighted linear combination of all sub-scores.

        Higher is better.  The cost and uncertainty terms are *subtracted*.
        """
        return (
            self.closure_weight * self.closure_score(node)
            + self.stability_weight * self.stability_score(node)
            + self.theorem_weight * node.predicted_theorem_yield
            + self.treaty_weight * max(node.treaty_impact, 0.0)
            - self.cost_weight * self.cost_penalty(node)
            - self.uncertainty_weight * self.uncertainty_adjustment(node)
        )

    def closure_score(self, node: FrontierNode) -> float:
        """Closure gain discounted logarithmically by depth.

        Deeper nodes receive a mild penalty to favour near-term gains.
        """
        depth_discount = 1.0 / (1.0 + 0.1 * node.depth)
        return node.predicted_closure_gain * depth_discount

    def stability_score(self, node: FrontierNode) -> float:
        """Stability gain, boosted when treaty impact is also positive.

        Positive treaty synergy adds up to 20 % bonus.
        """
        synergy = 1.0 + 0.2 * max(node.treaty_impact, 0.0)
        return node.predicted_stability_gain * synergy

    def diversity_score(self, node: FrontierNode, frontier: Frontier | None = None) -> float:
        """Novelty of *node* relative to the rest of the frontier.

        If a *frontier* is provided the score reflects how different the
        node's support scope is from existing nodes; otherwise returns 0.5.
        """
        if frontier is None or frontier.is_empty():
            return 0.5
        other_scopes: list[frozenset[str]] = [
            n.support_scope for n in frontier.all_nodes() if n.node_id != node.node_id
        ]
        if not other_scopes:
            return 1.0
        avg_jaccard = 0.0
        for scope in other_scopes:
            union = node.support_scope | scope
            if not union:
                avg_jaccard += 1.0
            else:
                avg_jaccard += 1.0 - len(node.support_scope & scope) / len(union)
        return avg_jaccard / len(other_scopes)

    def cost_penalty(self, node: FrontierNode) -> float:
        """Normalised cost penalty using a soft saturating transform.

        Maps ``estimated_cost`` through ``tanh`` so very high costs don't
        dominate the composite score.
        """
        return math.tanh(node.estimated_cost)

    def uncertainty_adjustment(self, node: FrontierNode) -> float:
        """Penalty for epistemic uncertainty, scaled quadratically.

        Highly uncertain nodes are penalised more aggressively.
        """
        return node.uncertainty ** 2

    def pareto_score(
        self,
        node: FrontierNode,
        reference_set: Sequence[FrontierNode],
    ) -> float:
        """Fraction of *reference_set* that *node* Pareto-dominates.

        Returns a value in [0, 1]; higher means the node is closer to the
        Pareto front.
        """
        if not reference_set:
            return 1.0
        dominated = sum(1 for other in reference_set if node.dominates(other))
        return dominated / len(reference_set)


# ---------------------------------------------------------------------------
# 5. PhaseTransition
# ---------------------------------------------------------------------------


class PhaseTransition:
    """Detects and classifies phase transitions in frontier dynamics.

    A phase transition occurs when the statistical character of the
    frontier changes abruptly – e.g. diversity collapse after a closure
    spike, or recovery after budget rebalancing.
    """

    def __init__(
        self,
        diversity_threshold: float = 0.25,
        closure_spike_factor: float = 2.0,
        stability_drop_threshold: float = -0.3,
        window_size: int = 10,
    ) -> None:
        self._div_threshold = diversity_threshold
        self._spike_factor = closure_spike_factor
        self._stab_drop = stability_drop_threshold
        self._window = window_size
        self._history: list[dict[str, float]] = []

    def _record_snapshot(self, frontier: Frontier, scorer: FrontierScorer) -> dict[str, float]:
        """Capture a statistical snapshot of the frontier."""
        nodes = frontier.all_nodes()
        if not nodes:
            snap: dict[str, float] = {
                "diversity": 1.0,
                "mean_closure": 0.0,
                "mean_stability": 0.0,
                "size": 0.0,
            }
        else:
            snap = {
                "diversity": frontier.diversity_score(),
                "mean_closure": sum(n.predicted_closure_gain for n in nodes) / len(nodes),
                "mean_stability": sum(n.predicted_stability_gain for n in nodes) / len(nodes),
                "size": float(len(nodes)),
            }
        self._history.append(snap)
        return snap

    def detect(self, frontier: Frontier, scorer: FrontierScorer) -> TransitionTrigger | None:
        """Detect whether a phase transition has just occurred.

        Returns the trigger kind, or ``None`` if no transition is detected.
        """
        snap = self._record_snapshot(frontier, scorer)

        if len(self._history) < 2:
            return None

        prev = self._history[-2]

        # Diversity collapse
        if snap["diversity"] < self._div_threshold and prev["diversity"] >= self._div_threshold:
            return TransitionTrigger.DIVERSITY_DROP

        # Closure spike
        if prev["mean_closure"] > 0 and snap["mean_closure"] / prev["mean_closure"] > self._spike_factor:
            return TransitionTrigger.CLOSURE_SPIKE

        # Stability loss
        delta_stability = snap["mean_stability"] - prev["mean_stability"]
        if delta_stability < self._stab_drop:
            return TransitionTrigger.STABILITY_LOSS

        # Size drop may signal budget exhaustion
        if prev["size"] > 0 and snap["size"] / prev["size"] < 0.3:
            return TransitionTrigger.BUDGET_EXHAUSTION

        return None

    def classify_phase(self, frontier: Frontier) -> PhaseKind:
        """Classify the current frontier phase based on aggregate statistics."""
        nodes = frontier.all_nodes()
        if not nodes:
            return PhaseKind.COLLAPSE
        diversity = frontier.diversity_score()
        mean_closure = sum(n.predicted_closure_gain for n in nodes) / len(nodes)
        mean_uncertainty = sum(n.uncertainty for n in nodes) / len(nodes)

        if diversity > 0.6 and mean_uncertainty > 0.4:
            return PhaseKind.EXPLORATION
        if diversity < 0.25:
            return PhaseKind.COLLAPSE
        if mean_closure > 0.7 and mean_uncertainty < 0.3:
            return PhaseKind.EXPLOITATION
        if len(self._history) >= 2 and self._history[-1].get("diversity", 0) > self._history[-2].get("diversity", 0) + 0.1:
            return PhaseKind.RECOVERY
        return PhaseKind.SATURATION

    def transition_trigger(self) -> TransitionTrigger | None:
        """Return the most recent trigger, or ``None``."""
        if len(self._history) < 2:
            return None
        prev, curr = self._history[-2], self._history[-1]
        if curr["diversity"] < self._div_threshold <= prev["diversity"]:
            return TransitionTrigger.DIVERSITY_DROP
        return None

    def pre_transition_state(self) -> dict[str, float] | None:
        """Return the snapshot immediately before the last recorded transition."""
        if len(self._history) < 2:
            return None
        return dict(self._history[-2])

    def post_transition_state(self) -> dict[str, float] | None:
        """Return the most recent snapshot (post-transition)."""
        if not self._history:
            return None
        return dict(self._history[-1])

    def predict_transition(self, frontier: Frontier, scorer: FrontierScorer) -> float:
        """Heuristic probability that a phase transition is imminent.

        Uses a simple linear model over the recent trend in diversity and
        closure.  Returns a probability in [0, 1].
        """
        self._record_snapshot(frontier, scorer)
        if len(self._history) < self._window:
            return 0.0

        window = self._history[-self._window:]
        div_deltas = [
            window[i]["diversity"] - window[i - 1]["diversity"]
            for i in range(1, len(window))
        ]
        mean_div_delta = sum(div_deltas) / len(div_deltas) if div_deltas else 0.0
        # Rapid diversity decrease → higher transition probability
        prob = max(0.0, min(1.0, -mean_div_delta * 5.0))
        return prob


# ---------------------------------------------------------------------------
# 6. BackpressureController
# ---------------------------------------------------------------------------


class BackpressureController:
    """Adaptive backpressure management for frontier expansion.

    Backpressure prevents the frontier from growing unboundedly when
    downstream consumers (provers, checkers) cannot keep up.  Each
    *channel* can carry independent pressure.
    """

    def __init__(
        self,
        max_pressure: float = 1.0,
        release_rate: float = 0.05,
        channels: Sequence[str] | None = None,
    ) -> None:
        self._max_pressure = max_pressure
        self._release_rate = release_rate
        self._pressure: dict[str, float] = {}
        for ch in channels or ["default"]:
            self._pressure[ch] = 0.0

    def current_pressure(self, channel: str = "default") -> float:
        """Return the current pressure level for *channel* ∈ [0, max]."""
        return self._pressure.get(channel, 0.0)

    def apply_backpressure(self, amount: float, channel: str = "default") -> float:
        """Increase pressure on *channel* by *amount*.

        Clamps at ``max_pressure``.  Returns the new pressure level.
        """
        current = self._pressure.get(channel, 0.0)
        new = min(current + amount, self._max_pressure)
        self._pressure[channel] = new
        return new

    def release_backpressure(self, amount: float | None = None, channel: str = "default") -> float:
        """Decrease pressure on *channel*.

        If *amount* is ``None`` the default ``release_rate`` is used.
        Returns the new pressure level.
        """
        step = amount if amount is not None else self._release_rate
        current = self._pressure.get(channel, 0.0)
        new = max(current - step, 0.0)
        self._pressure[channel] = new
        return new

    def adaptive_rate(self, frontier: Frontier) -> float:
        """Compute an adaptive expansion rate in [0, 1].

        When total pressure is high the rate is low, throttling expansion.
        The copilot can read this rate to decide how aggressively to propose
        new frontier nodes.
        """
        if not self._pressure:
            return 1.0
        avg_pressure = sum(self._pressure.values()) / len(self._pressure)
        return max(0.0, 1.0 - avg_pressure / self._max_pressure)

    def pressure_by_channel(self) -> dict[str, float]:
        """Return a snapshot of pressure across all channels."""
        return dict(self._pressure)

    def copilot_pressure_advice(self, frontier: Frontier) -> str:
        """Return a plain-language advisory string for the copilot.

        The copilot uses this to calibrate how many nodes to propose in
        the next expansion round.
        """
        rate = self.adaptive_rate(frontier)
        total_p = sum(self._pressure.values())
        if rate > 0.8:
            return f"Backpressure low ({total_p:.2f}). Copilot may propose freely."
        if rate > 0.4:
            return (
                f"Moderate backpressure ({total_p:.2f}). "
                "Copilot should limit proposals to top-scoring candidates."
            )
        return (
            f"High backpressure ({total_p:.2f}). "
            "Copilot should pause new proposals and wait for downstream drain."
        )

    def tick(self) -> None:
        """Advance one time step, passively releasing pressure on all channels."""
        for ch in list(self._pressure):
            self.release_backpressure(channel=ch)

    def is_overloaded(self, channel: str = "default") -> bool:
        """``True`` when *channel* pressure has reached the maximum."""
        return self._pressure.get(channel, 0.0) >= self._max_pressure


# ---------------------------------------------------------------------------
# 7. FrontierDiversity
# ---------------------------------------------------------------------------


class FrontierDiversity:
    """Diversity analysis and enforcement for the frontier.

    Maintains semantic diversity by clustering nodes by their support
    scopes and ensuring a minimum number of distinct clusters survive
    pruning.
    """

    def __init__(self, min_diversity: float = 0.3) -> None:
        self._min_diversity = min_diversity

    def compute_diversity(self, nodes: Sequence[FrontierNode]) -> float:
        """Average pairwise Jaccard distance over support scopes.

        Returns 1.0 when all scopes are disjoint, 0.0 when identical.
        """
        if len(nodes) < 2:
            return 1.0
        total = 0.0
        pairs = 0
        for i in range(len(nodes)):
            for j in range(i + 1, len(nodes)):
                a, b = nodes[i].support_scope, nodes[j].support_scope
                union = a | b
                if not union:
                    total += 1.0
                else:
                    total += 1.0 - len(a & b) / len(union)
                pairs += 1
        return total / pairs if pairs else 1.0

    def enforce_minimum_diversity(
        self,
        frontier: Frontier,
        scorer: FrontierScorer,
    ) -> int:
        """Remove dominated nodes in over-represented clusters.

        Returns the number of nodes pruned.  The method repeatedly
        identifies the largest cluster and removes its lowest-scoring
        member until diversity rises above ``min_diversity``.
        """
        pruned_count = 0
        while frontier.size() > 1:
            if self.compute_diversity(frontier.all_nodes()) >= self._min_diversity:
                break
            clusters = self.cluster_nodes(frontier.all_nodes())
            if not clusters:
                break
            largest = max(clusters.values(), key=len)
            if len(largest) < 2:
                break
            worst = min(largest, key=scorer.composite_score)
            frontier.remove_node(worst.node_id)
            pruned_count += 1
        return pruned_count

    def cluster_nodes(
        self,
        nodes: Sequence[FrontierNode],
    ) -> dict[frozenset[str], list[FrontierNode]]:
        """Group nodes by their exact support scope.

        Nodes sharing the same ``support_scope`` are placed in the same
        cluster.  This is a fast, exact clustering; for approximate
        semantic clustering a locality-sensitive hash could be substituted.
        """
        clusters: dict[frozenset[str], list[FrontierNode]] = defaultdict(list)
        for node in nodes:
            clusters[node.support_scope].append(node)
        return dict(clusters)

    def representative_set(
        self,
        nodes: Sequence[FrontierNode],
        scorer: FrontierScorer,
        max_representatives: int = 10,
    ) -> list[FrontierNode]:
        """Select a maximally diverse representative subset.

        Picks the single best node from each cluster (by composite score)
        until *max_representatives* is reached.
        """
        clusters = self.cluster_nodes(nodes)
        reps: list[FrontierNode] = []
        for _scope, members in sorted(
            clusters.items(), key=lambda kv: -len(kv[1])
        ):
            best = max(members, key=scorer.composite_score)
            reps.append(best)
            if len(reps) >= max_representatives:
                break
        return reps

    def novelty_score(
        self,
        node: FrontierNode,
        existing: Sequence[FrontierNode],
    ) -> float:
        """How novel *node* is compared to *existing* nodes.

        Computes the minimum Jaccard distance to any existing node.
        Returns 1.0 if the node's support scope is entirely new.
        """
        if not existing:
            return 1.0
        min_dist = 1.0
        for other in existing:
            union = node.support_scope | other.support_scope
            if not union:
                dist = 0.0
            else:
                dist = 1.0 - len(node.support_scope & other.support_scope) / len(union)
            min_dist = min(min_dist, dist)
        return min_dist

    def diversity_gap(self, frontier: Frontier) -> float:
        """How far below the minimum diversity threshold the frontier is.

        Returns 0.0 when diversity is sufficient, positive otherwise.
        """
        current = self.compute_diversity(frontier.all_nodes())
        return max(0.0, self._min_diversity - current)


# ---------------------------------------------------------------------------
# 8. FrontierBudget
# ---------------------------------------------------------------------------


class FrontierBudget:
    """Budget-aware frontier management.

    Tracks total budget, cumulative spend, and provides cost-based pruning
    and allocation helpers.
    """

    def __init__(self, total_budget: float = 100.0) -> None:
        self._total_budget = total_budget
        self._spent: float = 0.0
        self._allocation: dict[str, float] = {}

    def budget_remaining(self) -> float:
        """How much budget is left."""
        return max(0.0, self._total_budget - self._spent)

    def cost_so_far(self) -> float:
        """Total cost incurred so far."""
        return self._spent

    def record_cost(self, amount: float) -> None:
        """Record that *amount* of budget has been consumed."""
        self._spent += amount

    def prune_expensive(
        self,
        frontier: Frontier,
        cost_ceiling: float | None = None,
    ) -> list[FrontierNode]:
        """Remove nodes whose ``estimated_cost`` exceeds the ceiling.

        If *cost_ceiling* is ``None``, defaults to the remaining budget.
        Returns the pruned nodes.
        """
        ceiling = cost_ceiling if cost_ceiling is not None else self.budget_remaining()
        pruned: list[FrontierNode] = []
        for node in list(frontier.all_nodes()):
            if node.estimated_cost > ceiling:
                frontier.remove_node(node.node_id)
                pruned.append(node)
        return pruned

    def allocate_budget(
        self,
        channels: Sequence[str],
        weights: Sequence[float] | None = None,
    ) -> dict[str, float]:
        """Distribute remaining budget across *channels* proportionally.

        If *weights* are given they are normalised; otherwise budget is
        split evenly.
        """
        remaining = self.budget_remaining()
        if weights is None:
            share = remaining / len(channels) if channels else 0.0
            self._allocation = {ch: share for ch in channels}
        else:
            total_w = sum(weights) or 1.0
            self._allocation = {
                ch: remaining * (w / total_w)
                for ch, w in zip(channels, weights)
            }
        return dict(self._allocation)

    def rebalance(self, frontier: Frontier, scorer: FrontierScorer) -> dict[str, float]:
        """Rebalance budget allocation based on current frontier quality.

        Channels with higher mean composite scores receive proportionally
        more of the remaining budget.  Copilot systems can call this
        periodically to keep budget aligned with promising search paths.
        """
        clusters = FrontierDiversity().cluster_nodes(frontier.all_nodes())
        channel_scores: dict[str, float] = {}
        for scope, members in clusters.items():
            label = ",".join(sorted(scope)) or "global"
            mean_score = sum(scorer.composite_score(m) for m in members) / len(members)
            channel_scores[label] = max(mean_score, 0.01)

        channels = list(channel_scores.keys())
        weights = [channel_scores[ch] for ch in channels]
        return self.allocate_budget(channels, weights)

    def is_exhausted(self) -> bool:
        """``True`` when no budget remains."""
        return self.budget_remaining() <= 0.0

    def utilization(self) -> float:
        """Fraction of total budget consumed so far ∈ [0, 1]."""
        if self._total_budget <= 0:
            return 1.0
        return min(1.0, self._spent / self._total_budget)


# ---------------------------------------------------------------------------
# 9. FrontierHistory
# ---------------------------------------------------------------------------


class FrontierHistory:
    """Records the temporal evolution of the frontier.

    Each call to ``record_state`` captures a time-stamped snapshot that
    downstream analytics (and copilot diagnostics) can query.
    """

    def __init__(self) -> None:
        self._snapshots: list[dict[str, Any]] = []
        self._pruning_events: list[dict[str, Any]] = []
        self._expansion_timestamps: list[float] = []

    def record_state(
        self,
        frontier: Frontier,
        scorer: FrontierScorer,
        *,
        label: str = "",
    ) -> dict[str, Any]:
        """Capture and store a snapshot of the frontier's current state.

        Returns the snapshot dictionary for immediate inspection.
        """
        nodes = frontier.all_nodes()
        scores = [scorer.composite_score(n) for n in nodes]
        snap: dict[str, Any] = {
            "timestamp": time.monotonic(),
            "label": label,
            "size": len(nodes),
            "best_score": max(scores) if scores else 0.0,
            "mean_score": (sum(scores) / len(scores)) if scores else 0.0,
            "diversity": frontier.diversity_score(),
            "terminal_count": sum(1 for n in nodes if n.is_terminal),
            "max_depth": max((n.depth for n in nodes), default=0),
        }
        self._snapshots.append(snap)
        self._expansion_timestamps.append(snap["timestamp"])
        return snap

    def frontier_size_over_time(self) -> list[tuple[float, int]]:
        """Return ``(timestamp, size)`` pairs for every recorded snapshot."""
        return [(s["timestamp"], s["size"]) for s in self._snapshots]

    def best_score_over_time(self) -> list[tuple[float, float]]:
        """Return ``(timestamp, best_score)`` pairs."""
        return [(s["timestamp"], s["best_score"]) for s in self._snapshots]

    def mean_score_over_time(self) -> list[tuple[float, float]]:
        """Return ``(timestamp, mean_score)`` pairs."""
        return [(s["timestamp"], s["mean_score"]) for s in self._snapshots]

    def record_pruning(
        self,
        count: int,
        reason: str = "",
    ) -> None:
        """Log a pruning event with the number of nodes removed."""
        self._pruning_events.append({
            "timestamp": time.monotonic(),
            "count": count,
            "reason": reason,
        })

    def pruning_history(self) -> list[dict[str, Any]]:
        """Return all recorded pruning events."""
        return list(self._pruning_events)

    def expansion_rate(self, window_seconds: float = 10.0) -> float:
        """Average expansions per second over the last *window_seconds*.

        Returns 0.0 if insufficient data.
        """
        if len(self._expansion_timestamps) < 2:
            return 0.0
        now = time.monotonic()
        cutoff = now - window_seconds
        recent = [t for t in self._expansion_timestamps if t >= cutoff]
        if len(recent) < 2:
            return 0.0
        span = recent[-1] - recent[0]
        if span <= 0:
            return 0.0
        return (len(recent) - 1) / span

    def total_snapshots(self) -> int:
        """Total number of snapshots recorded."""
        return len(self._snapshots)

    def latest_snapshot(self) -> dict[str, Any] | None:
        """Return the most recent snapshot, or ``None``."""
        return dict(self._snapshots[-1]) if self._snapshots else None


# ---------------------------------------------------------------------------
# 10. FrontierDiagnostics
# ---------------------------------------------------------------------------


class FrontierDiagnostics:
    """Human-readable and copilot-consumable diagnostic reports.

    Aggregates information from the frontier, scorer, phase detector,
    budget tracker, and history into concise summaries.
    """

    def __init__(
        self,
        frontier: Frontier,
        scorer: FrontierScorer,
        history: FrontierHistory | None = None,
        budget: FrontierBudget | None = None,
        phase_detector: PhaseTransition | None = None,
        diversity_manager: FrontierDiversity | None = None,
        backpressure: BackpressureController | None = None,
    ) -> None:
        self._frontier = frontier
        self._scorer = scorer
        self._history = history or FrontierHistory()
        self._budget = budget or FrontierBudget()
        self._phase = phase_detector or PhaseTransition()
        self._diversity = diversity_manager or FrontierDiversity()
        self._bp = backpressure or BackpressureController()

    def frontier_summary(self) -> dict[str, Any]:
        """High-level summary of the frontier state."""
        nodes = self._frontier.all_nodes()
        scores = [self._scorer.composite_score(n) for n in nodes]
        return {
            "size": len(nodes),
            "best_score": round(max(scores), 4) if scores else 0.0,
            "mean_score": round(sum(scores) / len(scores), 4) if scores else 0.0,
            "diversity": round(self._frontier.diversity_score(), 4),
            "terminal_count": sum(1 for n in nodes if n.is_terminal),
            "max_depth": max((n.depth for n in nodes), default=0),
            "total_cost": round(sum(n.estimated_cost for n in nodes), 4),
        }

    def phase_report(self) -> dict[str, Any]:
        """Report on the current phase and recent transitions."""
        phase = self._phase.classify_phase(self._frontier)
        trigger = self._phase.detect(self._frontier, self._scorer)
        pre = self._phase.pre_transition_state()
        post = self._phase.post_transition_state()
        prediction = self._phase.predict_transition(self._frontier, self._scorer)
        return {
            "current_phase": phase.name,
            "last_trigger": trigger.name if trigger else None,
            "transition_probability": round(prediction, 4),
            "pre_transition": pre,
            "post_transition": post,
        }

    def diversity_report(self) -> dict[str, Any]:
        """Report on frontier diversity and cluster structure."""
        nodes = self._frontier.all_nodes()
        clusters = self._diversity.cluster_nodes(nodes)
        return {
            "diversity": round(self._diversity.compute_diversity(nodes), 4),
            "cluster_count": len(clusters),
            "largest_cluster_size": max(
                (len(members) for members in clusters.values()), default=0
            ),
            "diversity_gap": round(self._diversity.diversity_gap(self._frontier), 4),
        }

    def budget_report(self) -> dict[str, Any]:
        """Report on budget utilization."""
        return {
            "budget_remaining": round(self._budget.budget_remaining(), 4),
            "cost_so_far": round(self._budget.cost_so_far(), 4),
            "utilization": round(self._budget.utilization(), 4),
            "is_exhausted": self._budget.is_exhausted(),
        }

    def copilot_frontier_summary(self) -> str:
        """Plain-language summary designed for copilot consumption.

        The copilot reads this to understand the frontier health at a
        glance and decide on next actions: expand, prune, rebalance, or
        wait.
        """
        fs = self.frontier_summary()
        phase = self._phase.classify_phase(self._frontier)
        bp_advice = self._bp.copilot_pressure_advice(self._frontier)
        budget_pct = self._budget.utilization() * 100

        lines = [
            f"Frontier: {fs['size']} nodes, best={fs['best_score']}, "
            f"mean={fs['mean_score']}, diversity={fs['diversity']}.",
            f"Phase: {phase.name}. Depth range: 0–{fs['max_depth']}.",
            f"Budget: {budget_pct:.1f}% used. {bp_advice}",
        ]

        gap = self._diversity.diversity_gap(self._frontier)
        if gap > 0:
            lines.append(
                f"⚠ Diversity below threshold by {gap:.3f}. "
                "Copilot should diversify proposals."
            )

        prediction = self._phase.predict_transition(self._frontier, self._scorer)
        if prediction > 0.5:
            lines.append(
                f"⚠ Phase transition likely (p={prediction:.2f}). "
                "Copilot should prepare adaptive strategy switch."
            )

        return "\n".join(lines)

    def full_diagnostic(self) -> dict[str, Any]:
        """Complete diagnostic bundle combining all sub-reports.

        Intended for logging, dashboards, or detailed copilot analysis.
        """
        return {
            "frontier": self.frontier_summary(),
            "phase": self.phase_report(),
            "diversity": self.diversity_report(),
            "budget": self.budget_report(),
            "backpressure": self._bp.pressure_by_channel(),
            "expansion_rate": round(self._history.expansion_rate(), 4),
            "total_snapshots": self._history.total_snapshots(),
            "latest_snapshot": self._history.latest_snapshot(),
            "copilot_summary": self.copilot_frontier_summary(),
        }

    def top_nodes_report(self, k: int = 5) -> list[dict[str, Any]]:
        """Summary of the top-*k* frontier nodes for quick inspection."""
        return [
            {
                **node.summary(),
                "composite_score": round(self._scorer.composite_score(node), 4),
            }
            for node in self._frontier.top_k(k)
        ]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    # Legacy (backward-compatible)
    "FrontierItem",
    "FrontierState",
    # New core types
    "FrontierNode",
    "Frontier",
    "FrontierSearch",
    "FrontierScorer",
    # Phase transitions
    "PhaseKind",
    "TransitionTrigger",
    "PhaseTransition",
    # Support systems
    "BackpressureController",
    "FrontierDiversity",
    "FrontierBudget",
    "FrontierHistory",
    "FrontierDiagnostics",
]
