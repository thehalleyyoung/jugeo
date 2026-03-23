"""Algorithms — Ch42 fleet allocation and ranking algorithms.

This module implements the core algorithms used in the inhabitant_fleets
pipeline:

  FleetAllocationAlgorithm  – abstract base for allocation strategies
  GreedyFleetAllocation     – O(n) greedy assignment
  OptimalFleetAllocation    – O(n²) optimal assignment via scoring
  HeuristicFleetAllocation  – O(n log n) heuristic with load balancing
  BackpressurePropagation   – propagates backpressure through the signal graph
  InhabitantRanking         – multi-criteria ranking of proposals
  SemanticDistanceComputer  – computes semantic distance between proposals
  FleetConvergenceChecker   – checks fleet convergence conditions

Theory — Ch42 Algorithms
--------------------------
The allocation problem is: given a set of goals G and a set of fleet
members M, assign each g ∈ G to a member m ∈ M such that:

    ∀ g ∈ G: ∃ m ∈ M assigned to g  AND  load(m) ≤ MAX_LOAD

The ranking problem is: given a set of proposals P and a list of criteria
C = [c₁, …, cₙ], produce a total order ≤_C on P such that:

    p₁ ≤_C p₂  iff  Σᵢ wᵢ × cᵢ(p₁) ≤ Σᵢ wᵢ × cᵢ(p₂)

where wᵢ are criterion weights.

Convergence (Ch42 §2 Theorem 42.1):
    A fleet F converges in at most |F.members|² rounds when backpressure
    is bounded: ∀s ∈ signals: s.instability_score ≤ 1.0.

Examples
---------
>>> from jugeo.generation.inhabitant_fleets.algorithms import (
...     InhabitantRanking, FleetConvergenceChecker,
... )
>>> ranking = InhabitantRanking()
>>> checker = FleetConvergenceChecker()
"""
from __future__ import annotations

import math
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from jugeo.generation.inhabitant_fleets.models import (
    InhabitantProposal,
    FleetBid,
    BackpressureSignal,
    ProposalStatus,
    TrustTier,
)

MAX_LOAD: float = 10.0
CONVERGENCE_THRESHOLD: float = 0.95


# ---------------------------------------------------------------------------
# FleetAllocationAlgorithm (Abstract Base)
# ---------------------------------------------------------------------------


class FleetAllocationAlgorithm:
    """Abstract base class for fleet allocation algorithms.

    Subclasses implement allocate(goals, members) which assigns each goal
    to a fleet member.

    Theory — Ch42 Algorithms §1
    ----------------------------
    The allocation maps:

        allocate : Goals × Members → Assignments

    where Assignments[g] = m means goal g is assigned to member m.

    Subclasses
    ----------
    - GreedyFleetAllocation   : O(n) greedy by availability
    - OptimalFleetAllocation  : O(n²) optimal by combined score
    - HeuristicFleetAllocation: O(n log n) load-balanced heuristic
    """

    algorithm_name: str = "abstract"

    def allocate(
        self,
        goals: list[Any],
        members: list[Any],
    ) -> dict[str, str]:
        """Assign goals to members.

        Parameters
        ----------
        goals : list[Any]
            Goals to allocate.
        members : list[Any]
            Available fleet members.

        Returns
        -------
        dict[str, str]
            Mapping from goal label to member_id.
        """
        raise NotImplementedError("Subclasses must implement allocate()")

    def _extract_goal_label(self, goal: Any) -> str:
        for attr in ("label", "name", "goal_id", "proposition"):
            val = getattr(goal, attr, None)
            if val and isinstance(val, str):
                return val[:60]
        return str(goal)[:60]

    def _member_load(self, member: Any) -> float:
        return float(getattr(member, "current_load", 0.0))

    def _member_id(self, member: Any) -> str:
        return str(getattr(member, "member_id", id(member)))

    def _legacy_allocate(self, fleets: list[Any], goal: Any) -> Any:
        if not fleets:
            return None
        viable = [fleet for fleet in fleets if getattr(fleet, "members", None)]
        if not viable:
            return fleets[0]
        return max(
            viable,
            key=lambda fleet: (
                len(getattr(fleet, "members", [])),
                -sum(float(getattr(member, "current_load", 0.0)) for member in getattr(fleet, "members", [])),
            ),
        )


# ---------------------------------------------------------------------------
# GreedyFleetAllocation
# ---------------------------------------------------------------------------


class GreedyFleetAllocation(FleetAllocationAlgorithm):
    """Greedy allocation: assign each goal to the first available member.

    Complexity: O(n × |members|) worst case, O(n) average.

    Theory — Ch42 Algorithms §1.1
    --------------------------------
    The greedy rule is:

        for g in goals:
            m* = first m ∈ members with load(m) < MAX_LOAD
            assign(g, m*)

    Attributes
    ----------
    algorithm_name : str
        "greedy"
    """

    algorithm_name = "greedy"

    def allocate(
        self,
        goals: list[Any],
        members: list[Any] | Any,
    ) -> dict[str, str] | Any:
        """Greedily assign goals to members.

        Parameters
        ----------
        goals : list[Any]
        members : list[Any]

        Returns
        -------
        dict[str, str]
        """
        if not isinstance(members, list):
            return self._legacy_allocate(goals, members)
        assignments: dict[str, str] = {}
        available = list(members)
        idx = 0
        for goal in goals:
            if not available:
                break
            label = self._extract_goal_label(goal)
            # Find first member with room
            assigned = False
            for member in available:
                if self._member_load(member) < MAX_LOAD:
                    assignments[label] = self._member_id(member)
                    # Increment load
                    if hasattr(member, "increment_load"):
                        member.increment_load(1.0)
                    elif hasattr(member, "current_load"):
                        member.current_load = min(MAX_LOAD, member.current_load + 1.0)
                    assigned = True
                    break
            if not assigned:
                # All full: assign to least loaded
                lm = min(available, key=self._member_load)
                assignments[label] = self._member_id(lm)
        return assignments


# ---------------------------------------------------------------------------
# OptimalFleetAllocation
# ---------------------------------------------------------------------------


class OptimalFleetAllocation(FleetAllocationAlgorithm):
    """Optimal allocation: maximize total assignment score.

    Complexity: O(n × |members|) with greedy score maximization.

    Theory — Ch42 Algorithms §1.2
    --------------------------------
    For each goal g, compute assignment_score(g, m) for all m ∈ members
    and assign to the m* = argmax_m assignment_score(g, m).

        assignment_score(g, m) = (MAX_LOAD - load(m)) / MAX_LOAD
                                  × affinity(g.type, m.specialization)

    Attributes
    ----------
    algorithm_name : str
        "optimal"
    """

    algorithm_name = "optimal"

    def allocate(
        self,
        goals: list[Any],
        members: list[Any] | Any,
    ) -> dict[str, str] | Any:
        """Optimally assign goals to members by score.

        Parameters
        ----------
        goals : list[Any]
        members : list[Any]

        Returns
        -------
        dict[str, str]
        """
        if not isinstance(members, list):
            return self._legacy_allocate(goals, members)
        assignments: dict[str, str] = {}
        if not members:
            return assignments
        for goal in goals:
            label = self._extract_goal_label(goal)
            best_member = max(
                members,
                key=lambda m: self._assignment_score(goal, m),
            )
            assignments[label] = self._member_id(best_member)
            if hasattr(best_member, "increment_load"):
                best_member.increment_load(1.0)
            elif hasattr(best_member, "current_load"):
                best_member.current_load = min(MAX_LOAD, best_member.current_load + 1.0)
        return assignments

    def _assignment_score(self, goal: Any, member: Any) -> float:
        """Compute assignment score for (goal, member) pair."""
        load = self._member_load(member)
        load_factor = (MAX_LOAD - load) / MAX_LOAD
        affinity = self._compute_affinity(goal, member)
        return load_factor * affinity

    def _compute_affinity(self, goal: Any, member: Any) -> float:
        """Compute affinity between goal type and member specialization."""
        goal_type = str(getattr(goal, "goal_type", "") or "").lower()
        spec = str(getattr(member, "specialization", "generic")).lower()
        if not goal_type or spec == "generic":
            return 0.9
        return 1.0 if spec in goal_type else 0.7


# ---------------------------------------------------------------------------
# HeuristicFleetAllocation
# ---------------------------------------------------------------------------


class HeuristicFleetAllocation(FleetAllocationAlgorithm):
    """Heuristic allocation: load-balanced round-robin with score tiebreak.

    Complexity: O(n log |members|).

    Theory — Ch42 Algorithms §1.3
    --------------------------------
    Goals are processed in priority order.  Members are ranked by
    (load ascending, affinity descending).  Each goal is assigned to
    the top-ranked member.

    Attributes
    ----------
    algorithm_name : str
        "heuristic"
    """

    algorithm_name = "heuristic"

    def allocate(
        self,
        goals: list[Any],
        members: list[Any] | Any,
    ) -> dict[str, str] | Any:
        """Heuristically assign goals to members with load balancing.

        Parameters
        ----------
        goals : list[Any]
        members : list[Any]

        Returns
        -------
        dict[str, str]
        """
        if not isinstance(members, list):
            return self._legacy_allocate(goals, members)
        assignments: dict[str, str] = {}
        if not members:
            return assignments
        # Sort goals by priority descending
        sorted_goals = sorted(
            goals,
            key=lambda g: -int(getattr(g, "priority", 2)),
        )
        for goal in sorted_goals:
            label = self._extract_goal_label(goal)
            # Pick member with minimum load
            chosen = min(members, key=lambda m: (self._member_load(m), self._member_id(m)))
            assignments[label] = self._member_id(chosen)
            if hasattr(chosen, "increment_load"):
                chosen.increment_load(1.0)
            elif hasattr(chosen, "current_load"):
                chosen.current_load = min(MAX_LOAD, chosen.current_load + 1.0)
        return assignments


# ---------------------------------------------------------------------------
# BackpressurePropagation
# ---------------------------------------------------------------------------


class BackpressurePropagation:
    """Propagates backpressure signals through the patch graph.

    Theory — Ch42 Algorithms §2
    ----------------------------
    Given a set of signals S and a propagation factor α ∈ (0, 1), the
    propagated instability for a target patch Q is:

        σ'(Q) = max_{s ∈ S: Q ∈ s.target_patches} α × s.instability_score

    Propagation is applied iteratively until convergence:

        σ_{k+1}(Q) = max(σ_k(Q), α × max_s σ_k(s.source_patch))

    Attributes
    ----------
    propagation_factor : float
        Damping factor α ∈ (0, 1].
    max_iterations : int
        Maximum propagation iterations.
    """

    def __init__(
        self,
        propagation_factor: float = 0.8,
        max_iterations: int = 10,
    ) -> None:
        self.propagation_factor = propagation_factor
        self.max_iterations = max_iterations

    def propagate(
        self,
        signals: list[BackpressureSignal] | BackpressureSignal,
        graph: dict[str, list[str]] | None = None,
    ) -> dict[str, float] | list[BackpressureSignal]:
        """Compute propagated instability scores for all patches.

        Parameters
        ----------
        signals : list[BackpressureSignal]
            All active backpressure signals.

        Returns
        -------
        dict[str, float]
            Mapping from patch_id to propagated instability score.
        """
        if isinstance(signals, BackpressureSignal):
            frontier = [signals]
            if graph is None:
                return frontier
            seen = {signals.source_patch}
            propagated_signals: list[BackpressureSignal] = []
            while frontier:
                sig = frontier.pop(0)
                propagated_signals.append(sig)
                for target in graph.get(sig.source_patch, []):
                    if target in seen:
                        continue
                    seen.add(target)
                    frontier.append(self.dampen(
                        BackpressureSignal(
                            signal_id=f"{sig.signal_id}:{target}",
                            source_patch=target,
                            target_patches=tuple(graph.get(target, [])),
                            instability_score=sig.instability_score,
                            threshold=sig.threshold,
                            severity=sig.severity,
                            timestamp=sig.timestamp,
                            remediation_hints=list(getattr(sig, "remediation_hints", ())),
                        ),
                        self.propagation_factor,
                    ))
            return propagated_signals

        scores: dict[str, float] = {}
        for sig in signals:
            scores[sig.source_patch] = max(
                scores.get(sig.source_patch, 0.0),
                sig.instability_score,
            )
        # Propagation iterations
        for _ in range(self.max_iterations):
            updated = False
            new_scores = dict(scores)
            for sig in signals:
                src_score = scores.get(sig.source_patch, 0.0)
                propagated = self.propagation_factor * src_score
                for target in sig.target_patches:
                    old = new_scores.get(target, 0.0)
                    new_val = max(old, propagated)
                    if new_val > old + 1e-9:
                        new_scores[target] = new_val
                        updated = True
            scores = new_scores
            if not updated:
                break
        return scores

    def dampen(self, signal: BackpressureSignal, factor: float) -> BackpressureSignal:
        return BackpressureSignal(
            signal_id=signal.signal_id,
            source_patch=signal.source_patch,
            target_patches=signal.target_patches,
            instability_score=max(0.0, signal.instability_score * factor),
            threshold=signal.threshold,
            severity=signal.severity,
            timestamp=signal.timestamp,
            remediation_hints=list(getattr(signal, "remediation_hints", ())),
        )

    def accumulate(self, signals: list[BackpressureSignal]) -> BackpressureSignal | None:
        if not signals:
            return None
        if len(signals) == 1:
            return signals[0]
        peak = max(sig.instability_score for sig in signals)
        base = signals[0]
        targets: list[str] = []
        for sig in signals:
            for target in sig.target_patches:
                if target not in targets:
                    targets.append(target)
        return BackpressureSignal(
            signal_id=base.signal_id,
            source_patch=base.source_patch,
            target_patches=tuple(targets),
            instability_score=peak,
            threshold=base.threshold,
            severity=base.severity,
            timestamp=base.timestamp,
            remediation_hints=list(getattr(base, "remediation_hints", ())),
        )

    def critical_patches(
        self,
        signals: list[BackpressureSignal],
        critical_threshold: float = 0.9,
    ) -> list[str]:
        """Return patches with propagated instability above critical_threshold.

        Parameters
        ----------
        signals : list[BackpressureSignal]
        critical_threshold : float

        Returns
        -------
        list[str]
        """
        scores = self.propagate(signals)
        return [p for p, s in scores.items() if s >= critical_threshold]


# ---------------------------------------------------------------------------
# InhabitantRanking
# ---------------------------------------------------------------------------


class InhabitantRanking:
    """Multi-criteria ranking of InhabitantProposal objects.

    Supported criteria names:
      "score"    – p.score()
      "trust"    – int(p.trust_tier)
      "evidence" – p.evidence_score
      "length"   – len(p.semantic_content)
      "age"      – time since created_at (lower is better, so negated)

    Weights are uniform by default; pass weight_map to override.

    Theory — Ch42 Algorithms §3
    ----------------------------
    The combined ranking score for criteria C = [c₁, …, cₙ] is:

        R(p) = Σᵢ wᵢ × normalize(cᵢ(p))

    where normalize maps each criterion value to [0, 1].

    Attributes
    ----------
    weight_map : dict[str, float]
        Optional per-criterion weights.
    """

    DEFAULT_WEIGHTS: dict[str, float] = {
        "score": 0.5,
        "trust": 0.3,
        "evidence": 0.2,
        "length": 0.0,
        "age": 0.0,
    }

    def __init__(self, weight_map: dict[str, float] | None = None) -> None:
        self.weight_map = weight_map or dict(self.DEFAULT_WEIGHTS)

    def rank(
        self,
        proposals: list[InhabitantProposal],
        criteria: list[str] | dict[str, float] | None = None,
    ) -> list[InhabitantProposal]:
        """Rank proposals by weighted multi-criteria score.

        Parameters
        ----------
        proposals : list[InhabitantProposal]
            Proposals to rank.
        criteria : list[str] | None
            Criteria to use; defaults to ["score", "trust", "evidence"].

        Returns
        -------
        list[InhabitantProposal]
            Proposals sorted by combined score descending.
        """
        if not proposals:
            return []
        used_criteria = criteria or ["score", "trust", "evidence"]
        if isinstance(used_criteria, dict):
            self.weight_map = dict(used_criteria)
            used_criteria = list(used_criteria)
        scored = [(self._combined_score(p, used_criteria), p.proposal_id, p) for p in proposals]
        scored.sort(key=lambda x: (-x[0], x[1]))
        return [p for _, _, p in scored]

    def _combined_score(
        self,
        proposal: InhabitantProposal,
        criteria: list[str],
    ) -> float:
        """Compute weighted combined score for a proposal."""
        total = 0.0
        for crit in criteria:
            w = self.weight_map.get(crit, 1.0 / max(len(criteria), 1))
            val = self._criterion_value(proposal, crit)
            total += w * val
        return total

    def _criterion_value(self, proposal: InhabitantProposal, criterion: str) -> float:
        """Extract and normalize criterion value for a proposal."""
        if criterion == "score":
            return min(1.0, max(0.0, proposal.score() / 3.0))
        if criterion == "trust":
            return min(1.0, int(proposal.trust_tier) / 3.0)
        if criterion == "evidence":
            return min(1.0, max(0.0, proposal.evidence_score))
        if criterion == "length":
            return min(1.0, len(proposal.semantic_content) / 200.0)
        if criterion == "age":
            age = time.time() - getattr(proposal, "created_at", time.time())
            return max(0.0, 1.0 - age / 3600.0)
        if criterion == "evidence_score":
            return min(1.0, max(0.0, getattr(proposal, "evidence_score", 0.0)))
        if criterion == "trust_tier":
            return min(1.0, int(getattr(proposal, "trust_tier", 0)) / 3.0)
        return 0.0

    def top_k(
        self,
        proposals: list[InhabitantProposal],
        k: int,
        criteria: list[str] | None = None,
    ) -> list[InhabitantProposal]:
        """Return the top-k ranked proposals.

        Parameters
        ----------
        proposals : list[InhabitantProposal]
        k : int
        criteria : list[str] | None

        Returns
        -------
        list[InhabitantProposal]
        """
        return self.rank(proposals, criteria)[:k]

    def pareto_rank(self, proposals: list[InhabitantProposal]) -> list[InhabitantProposal]:
        if not proposals:
            return []
        return self.rank(proposals, {"evidence_score": 0.7, "trust_tier": 0.3})

    def weighted_rank(
        self,
        proposals: list[InhabitantProposal],
        weights: dict[str, float],
    ) -> list[InhabitantProposal]:
        return self.rank(proposals, weights)


# ---------------------------------------------------------------------------
# SemanticDistanceComputer
# ---------------------------------------------------------------------------


class SemanticDistanceComputer:
    """Computes semantic distance between InhabitantProposal pairs.

    Theory — Ch42 Algorithms §4
    ----------------------------
    The semantic distance between two proposals p₁, p₂ is:

        δ(p₁, p₂) = 1 - Jaccard(tokens(p₁), tokens(p₂))

    where tokens(p) = set of whitespace-split tokens from p.semantic_content.

    For proposals with identical content: δ = 0.
    For proposals with disjoint content: δ = 1.

    Attributes
    ----------
    tokenize_fn : callable
        Function mapping content str to a set of tokens.
    """

    def __init__(self) -> None:
        self._cache: dict[tuple[str, str], float] = {}

    def compute(
        self,
        p1: InhabitantProposal,
        p2: InhabitantProposal,
    ) -> float:
        return self.distance(p1, p2)

    def distance(
        self,
        p1: InhabitantProposal,
        p2: InhabitantProposal,
    ) -> float:
        """Compute semantic distance between two proposals.

        Parameters
        ----------
        p1, p2 : InhabitantProposal
            Proposals to compare.

        Returns
        -------
        float
            Distance ∈ [0, 1].
        """
        key = (p1.proposal_id, p2.proposal_id)
        if key in self._cache:
            return self._cache[key]
        t1 = self._tokenize(p1.semantic_content)
        t2 = self._tokenize(p2.semantic_content)
        d = 1.0 - self._jaccard(t1, t2)
        self._cache[key] = d
        return d

    def _tokenize(self, content: str) -> frozenset[str]:
        """Tokenize content into a frozenset of lower-cased words."""
        return frozenset(w.lower() for w in content.split() if w)

    def _jaccard(self, s1: frozenset[str], s2: frozenset[str]) -> float:
        """Compute Jaccard similarity between two sets."""
        if not s1 and not s2:
            return 1.0
        union = s1 | s2
        if not union:
            return 1.0
        return len(s1 & s2) / len(union)

    def distance_matrix(
        self,
        proposals: list[InhabitantProposal],
    ) -> list[list[float]]:
        """Compute the pairwise distance matrix.

        Parameters
        ----------
        proposals : list[InhabitantProposal]

        Returns
        -------
        list[list[float]]
            n × n matrix where entry [i][j] = distance(proposals[i], proposals[j]).
        """
        n = len(proposals)
        matrix = [[0.0] * n for _ in range(n)]
        for i in range(n):
            for j in range(i + 1, n):
                d = self.distance(proposals[i], proposals[j])
                matrix[i][j] = d
                matrix[j][i] = d
        return matrix

    def compute_matrix(self, proposals: list[InhabitantProposal]) -> list[list[float]]:
        return self.distance_matrix(proposals)

    def most_similar_pair(
        self,
        proposals: list[InhabitantProposal],
    ) -> tuple[InhabitantProposal, InhabitantProposal, float] | None:
        """Find the most similar pair of proposals (lowest distance).

        Parameters
        ----------
        proposals : list[InhabitantProposal]

        Returns
        -------
        tuple[InhabitantProposal, InhabitantProposal, float] | None
            (p1, p2, distance) or None if fewer than 2 proposals.
        """
        if len(proposals) < 2:
            return None
        best = (proposals[0], proposals[1], self.distance(proposals[0], proposals[1]))
        for i in range(len(proposals)):
            for j in range(i + 1, len(proposals)):
                d = self.distance(proposals[i], proposals[j])
                if d < best[2]:
                    best = (proposals[i], proposals[j], d)
        return best

    def find_nearest(
        self,
        query: InhabitantProposal,
        candidates: list[InhabitantProposal],
    ) -> InhabitantProposal | None:
        if not candidates:
            return None
        return min(
            candidates,
            key=lambda candidate: (self.distance(query, candidate), candidate.proposal_id),
        )


# ---------------------------------------------------------------------------
# FleetConvergenceChecker
# ---------------------------------------------------------------------------


class FleetConvergenceChecker:
    """Checks whether a fleet has converged to a stable bid assignment.

    Theory — Ch42 §2 Theorem 42.1 (Fleet Convergence)
    ---------------------------------------------------
    Under bounded backpressure (∀s ∈ signals: s.instability_score ≤ 1.0),
    a fleet F with |F.members| ≥ 1 converges to a stable bid assignment
    in at most O(|F.members|²) rounds.

    Convergence is detected when:
      1. Fleet utilization is below convergence_threshold
      2. No critical backpressure signals exist
      3. At least one bid exists in current_bids

    Attributes
    ----------
    convergence_threshold : float
        Utilization threshold below which the fleet is considered converged.
    """

    def __init__(self, convergence_threshold: float = CONVERGENCE_THRESHOLD) -> None:
        self.convergence_threshold = convergence_threshold

    def is_converged(
        self,
        fleet: Any,
        signals: list[BackpressureSignal] | None = None,
    ) -> bool:
        """Check if the fleet has converged.

        Parameters
        ----------
        fleet : Any
            InhabitantFleet or duck-typed equivalent.
        signals : list[BackpressureSignal] | None
            Active backpressure signals; empty if None.

        Returns
        -------
        bool
        """
        sigs = signals or []
        members = getattr(fleet, "members", [])
        current_bids = getattr(fleet, "current_bids", [])
        if not members:
            return False
        utilization_fn = getattr(fleet, "utilization", None)
        utilization = utilization_fn() if callable(utilization_fn) else 0.0
        has_critical = any(s.is_critical() for s in sigs)
        has_bids = len(current_bids) > 0
        return (
            utilization <= self.convergence_threshold
            and not has_critical
            and has_bids
        )

    def check(
        self,
        fleet: Any,
        signals: list[BackpressureSignal] | None = None,
    ) -> bool:
        return self.is_converged(fleet, signals)

    def compute_agreement(self, bids: list[Any]) -> float:
        if not bids:
            return 0.0
        scores = [float(getattr(bid, "bid_score", 0.0)) for bid in bids]
        mean = sum(scores) / len(scores)
        variance = sum((score - mean) ** 2 for score in scores) / len(scores)
        overlap = sum(float(getattr(bid, "overlap_compatibility", getattr(bid, "overlap", 1.0))) for bid in bids) / len(bids)
        return max(0.0, min(1.0, (1.0 - min(1.0, variance)) * max(0.0, min(1.0, overlap))))

    def is_stable(self, fleet: Any, rounds: int = 1) -> bool:
        if rounds <= 0:
            return False
        bids = getattr(fleet, "current_bids", []) or []
        if not bids:
            return False
        return self.compute_agreement(bids) >= 0.5 or rounds >= 3

    def estimate_rounds(self, fleet: Any) -> int:
        """Estimate number of rounds to convergence.

        Parameters
        ----------
        fleet : Any

        Returns
        -------
        int
            Upper bound on convergence rounds.
        """
        members = getattr(fleet, "members", [])
        n = len(members)
        if n == 0:
            return 0
        mean_load = sum(
            float(getattr(m, "current_load", 0)) for m in members
        ) / n
        return max(1, int(n * (1.0 + mean_load / MAX_LOAD)))

    def convergence_report(
        self,
        fleet: Any,
        signals: list[BackpressureSignal] | None = None,
    ) -> dict[str, Any]:
        """Return a detailed convergence report.

        Parameters
        ----------
        fleet : Any
        signals : list[BackpressureSignal] | None

        Returns
        -------
        dict[str, Any]
        """
        sigs = signals or []
        members = getattr(fleet, "members", [])
        current_bids = getattr(fleet, "current_bids", [])
        utilization_fn = getattr(fleet, "utilization", None)
        utilization = utilization_fn() if callable(utilization_fn) else 0.0
        converged = self.is_converged(fleet, sigs)
        return {
            "converged": converged,
            "fleet_size": len(members),
            "bid_count": len(current_bids),
            "utilization": utilization,
            "critical_signals": sum(1 for s in sigs if s.is_critical()),
            "estimated_rounds": self.estimate_rounds(fleet),
            "threshold": self.convergence_threshold,
        }


__all__ = [
    "FleetAllocationAlgorithm",
    "GreedyFleetAllocation",
    "OptimalFleetAllocation",
    "HeuristicFleetAllocation",
    "BackpressurePropagation",
    "InhabitantRanking",
    "SemanticDistanceComputer",
    "FleetConvergenceChecker",
]
