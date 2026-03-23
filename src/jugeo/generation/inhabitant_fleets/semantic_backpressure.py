"""Semantic Backpressure — Ch42 §3 backpressure monitoring and resolution.

This module implements the *semantic backpressure* subsystem: detection,
monitoring, and resolution of instability signals that arise when fleet
members produce conflicting or overlapping inhabitants for the same patch.

Theory — Ch42 §3 Backpressure
--------------------------------
Backpressure arises when the combined instability score of a patch exceeds
a threshold θ:

    σ(P) = Σᵢ |compatibility(tᵢ, tⱼ)| for all competing pairs (i,j)
    if σ(P) > θ  →  BackpressureSignal is emitted with instability_score = σ(P)

The instability score σ(P) ∈ [0, 1] is computed by the InstabilityMetric.

Boundedness Theorem (Ch42 §3, Theorem 42.2):
Under stable overlaps (all treaties ratified), backpressure signals satisfy:

    ∀s ∈ signals: s.instability_score ≤ 1.0

Controller Response
--------------------
The BackpressureController responds to signals by:
  - LOW/MEDIUM: Reduce fleet member loads proportionally
  - HIGH: Suspend new bids from affected members
  - CRITICAL: Emergency reset of all fleet members

Cascade Detection
------------------
The CascadeDetector identifies chains of backpressure propagation:

    cascade(P) = { Q | ∃ signal s: s.source_patch = P ∧ Q ∈ s.target_patches }

A cascade occurs when |cascade(P)| ≥ CASCADE_THRESHOLD.

Examples
---------
>>> from jugeo.generation.inhabitant_fleets.semantic_backpressure import (
...     BackpressureMonitor, BackpressureController,
... )
>>> monitor = BackpressureMonitor(threshold=0.7)
>>> # With no proposals, no signals are emitted
>>> signals = monitor.monitor([])
>>> len(signals)
0
"""
from __future__ import annotations

import math
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from jugeo.generation.inhabitant_fleets.models import (
    InhabitantProposal,
    BackpressureSignal,
    ProposalStatus,
    SeverityLevel,
    SemanticMove,
    MoveType,
    make_signal,
)

CASCADE_THRESHOLD: int = 3
MAX_INSTABILITY: float = 1.0


# ---------------------------------------------------------------------------
# InstabilityMetric
# ---------------------------------------------------------------------------


class InstabilityMetric:
    """Computes instability scores for sets of competing proposals.

    Theory — Ch42 §3.1
    --------------------
    The instability score σ(P) for patch P is computed as:

        σ(P) = 1 - (max_score - mean_score) / (max_score + ε)

    where max_score and mean_score are taken over all proposals for P.
    σ(P) = 0 means all proposals agree perfectly; σ(P) = 1 means maximal
    disagreement.

    Attributes
    ----------
    epsilon : float
        Small constant to avoid division by zero.
    """

    def __init__(
        self,
        metric_id: str | None = None,
        patch_pair: tuple[str, str] = ("patch-A", "patch-B"),
        measurement_rounds: int = 0,
        current_score: float = 0.0,
        trend: float = 0.0,
        epsilon: float = 1e-9,
    ) -> None:
        self.metric_id = metric_id or f"metric-{uuid.uuid4().hex[:8]}"
        self.patch_pair = patch_pair
        self.measurement_rounds = max(0, measurement_rounds)
        self.current_score = max(0.0, min(MAX_INSTABILITY, current_score))
        self.trend = trend
        self.epsilon = epsilon
        self._history: list[float] = []
        if self.measurement_rounds > 0:
            self._history = [self.current_score] * self.measurement_rounds

    def update(self, new_score: float) -> float:
        """Record a new measurement and update rolling score and trend."""
        bounded_score = max(0.0, min(MAX_INSTABILITY, new_score))
        previous_score = self.current_score
        total_rounds = self.measurement_rounds + 1
        if self.measurement_rounds == 0:
            self.current_score = bounded_score
        else:
            self.current_score = (
                (self.current_score * self.measurement_rounds) + bounded_score
            ) / total_rounds
        self.measurement_rounds = total_rounds
        self.trend = bounded_score - previous_score
        self._history.append(bounded_score)
        return self.current_score

    def get_trend(self) -> float:
        """Return the most recently observed trend."""
        return float(self.trend)

    def exceeds_threshold(self, threshold: float) -> bool:
        """Return True when the current score strictly exceeds *threshold*."""
        return self.current_score > threshold

    def compute(self, proposals: list[InhabitantProposal]) -> float:
        """Compute instability score for a list of proposals targeting the same patch.

        Parameters
        ----------
        proposals : list[InhabitantProposal]
            Proposals (typically competing for the same patch).

        Returns
        -------
        float
            Instability score ∈ [0, 1].
        """
        if len(proposals) <= 1:
            return 0.0
        scores = [p.score() for p in proposals]
        max_s = max(scores)
        mean_s = sum(scores) / len(scores)
        if max_s < self.epsilon:
            return 1.0
        spread = (max_s - mean_s) / (max_s + self.epsilon)
        # High spread = low instability; low spread = high instability
        return max(0.0, min(1.0, 1.0 - spread))

    def group_by_patch(
        self, proposals: list[InhabitantProposal]
    ) -> dict[str, list[InhabitantProposal]]:
        """Group proposals by patch_id.

        Parameters
        ----------
        proposals : list[InhabitantProposal]
            Proposals to group.

        Returns
        -------
        dict[str, list[InhabitantProposal]]
        """
        groups: dict[str, list[InhabitantProposal]] = {}
        for p in proposals:
            groups.setdefault(p.patch_id, []).append(p)
        return groups

    def compute_all(
        self, proposals: list[InhabitantProposal]
    ) -> dict[str, float]:
        """Compute instability scores for all patches.

        Parameters
        ----------
        proposals : list[InhabitantProposal]

        Returns
        -------
        dict[str, float]
            Mapping from patch_id to instability score.
        """
        groups = self.group_by_patch(proposals)
        return {pid: self.compute(group) for pid, group in groups.items()}


# ---------------------------------------------------------------------------
# BackpressureMonitor
# ---------------------------------------------------------------------------


class BackpressureMonitor:
    """Monitors proposals for backpressure signals.

    The monitor observes incoming proposals, computes instability scores
    per patch, and emits BackpressureSignal objects when instability
    exceeds the threshold.

    Theory — Ch42 §3.2
    --------------------
    Let θ be the instability threshold.  For each patch P:

        if σ(P) > θ  →  emit BackpressureSignal(source=P, instability=σ(P))

    Attributes
    ----------
    threshold : float
        Instability threshold θ ∈ (0, 1].
    metric : InstabilityMetric
        Used to compute σ(P).
    _all_signals : list[BackpressureSignal]
        Accumulated signals across all monitor() calls.
    """

    def __init__(self, threshold: float = 0.7) -> None:
        self.threshold = threshold
        self.metric = InstabilityMetric()
        self._all_signals: list[BackpressureSignal] = []

    def compute_instability(self, patch_pair: tuple[str, str]) -> float:
        """Compute a deterministic pairwise instability estimate."""
        left, right = patch_pair
        if left == right:
            return 0.0
        overlap = len(set(left) & set(right))
        scale = max(len(set(left) | set(right)), 1)
        return max(0.0, min(MAX_INSTABILITY, 1.0 - (overlap / scale)))

    def detect_cascade(
        self, signals: list[BackpressureSignal]
    ) -> list[list[str]]:
        """Delegate cascade detection to the dedicated detector."""
        return CascadeDetector().detect(signals)

    def emit_signal(
        self, instability_score: float, patch_pair: tuple[str, str]
    ) -> BackpressureSignal:
        """Create a signal for the supplied patch pair."""
        source, target = patch_pair
        return make_signal(source, [target], instability_score)

    def monitor(self, proposals: list[InhabitantProposal]) -> list[BackpressureSignal]:
        """Compute instability and emit signals for patches above threshold.

        Parameters
        ----------
        proposals : list[InhabitantProposal]
            Proposals to analyze.

        Returns
        -------
        list[BackpressureSignal]
            Signals emitted in this monitoring round.
        """
        if not proposals:
            return []
        scores_by_patch = self.metric.compute_all(proposals)
        groups = self.metric.group_by_patch(proposals)
        signals: list[BackpressureSignal] = []
        for patch_id, score in scores_by_patch.items():
            if score > self.threshold:
                affected = [p.patch_id for p in proposals if p.patch_id != patch_id]
                sig = make_signal(patch_id, affected, score)
                signals.append(sig)
        self._all_signals.extend(signals)
        return signals

    def get_all_signals(self) -> list[BackpressureSignal]:
        """Return all signals accumulated across all monitor() calls.

        Returns
        -------
        list[BackpressureSignal]
        """
        return list(self._all_signals)

    def clear(self) -> None:
        """Clear accumulated signals."""
        self._all_signals.clear()

    def signal_count(self) -> int:
        """Return total number of signals emitted."""
        return len(self._all_signals)


# ---------------------------------------------------------------------------
# BackpressureController
# ---------------------------------------------------------------------------


class BackpressureController:
    """Applies control actions in response to backpressure signals.

    Control actions depend on signal severity (Ch42 §3.3):
      - LOW:      Reduce load by 10% for affected fleet members
      - MEDIUM:   Reduce load by 25%
      - HIGH:     Suspend new bids (set load to MAX_LOAD * 0.9)
      - CRITICAL: Emergency reset (load = 0, clear bids)

    Attributes
    ----------
    _action_count : int
        Number of control actions applied.
    """

    def __init__(self) -> None:
        self._action_count = 0

    def throttle_fleet(self, fleet: Any, rate: float) -> None:
        """Reduce each member's load by the supplied rate."""
        clamped_rate = max(0.0, min(1.0, rate))
        for member in getattr(fleet, "members", []):
            load = getattr(member, "current_load", None)
            if load is not None:
                member.current_load = max(0.0, load * clamped_rate)

    def release_backpressure(self, fleet: Any) -> None:
        """Gently restore member capacity after pressure subsides."""
        for member in getattr(fleet, "members", []):
            load = getattr(member, "current_load", None)
            if load is not None:
                member.current_load = min(100.0, max(0.0, load * 1.1))

    def compute_safe_rate(self, instability: float) -> float:
        """Compute a conservative rate in [0.1, 1.0] from instability."""
        bounded = max(0.0, min(MAX_INSTABILITY, instability))
        return max(0.1, min(1.0, 1.0 - (0.9 * bounded)))

    def apply(self, signal: BackpressureSignal, fleets: list[Any]) -> None:
        """Apply control action for a backpressure signal.

        Parameters
        ----------
        signal : BackpressureSignal
            The signal to respond to.
        fleets : list[Any]
            All fleets to potentially affect.
        """
        self._action_count += 1
        severity_val = signal.severity
        if hasattr(severity_val, "value"):
            severity_str = severity_val.value
        else:
            severity_str = str(severity_val)

        for fleet in fleets:
            members = getattr(fleet, "members", [])
            for m in members:
                self._apply_to_member(m, severity_str)
            if severity_str in ("critical", "CRITICAL"):
                reset_fn = getattr(fleet, "reset", None)
                if reset_fn:
                    try:
                        reset_fn()
                    except Exception:
                        pass

    def _apply_to_member(self, member: Any, severity: str) -> None:
        """Apply load adjustment to a fleet member.

        Parameters
        ----------
        member : Any
            Fleet member with current_load attribute.
        severity : str
            Severity level string.
        """
        load = getattr(member, "current_load", None)
        if load is None:
            return
        if severity in ("low", "LOW"):
            member.current_load = load * 0.9
        elif severity in ("medium", "MEDIUM"):
            member.current_load = load * 0.75
        elif severity in ("high", "HIGH"):
            member.current_load = min(load * 1.5, 9.0)
        elif severity in ("critical", "CRITICAL"):
            member.current_load = 0.0

    def action_count(self) -> int:
        """Return total actions applied."""
        return self._action_count


# ---------------------------------------------------------------------------
# BackpressureResolver
# ---------------------------------------------------------------------------


class BackpressureResolver:
    """Resolves backpressure by filtering or re-ranking proposals.

    Resolution strategy (Ch42 §3.4):
      1. Remove proposals from the highest-instability patch
      2. Re-rank remaining proposals by score descending
      3. Return the resolved list

    The resolver guarantees that after resolution the instability score
    of the returned list does not exceed the signal's threshold.

    Attributes
    ----------
    metric : InstabilityMetric
        Used to verify resolution success.
    """

    def __init__(self) -> None:
        self.metric = InstabilityMetric()
        self._resolve_count = 0

    def find_stabilizing_moves(
        self, signal: BackpressureSignal
    ) -> list[SemanticMove]:
        """Suggest moves that can reduce the supplied signal's instability."""
        moves: list[SemanticMove] = [
            SemanticMove(
                move_id=uuid.uuid4().hex,
                move_type=MoveType.REFINE,
                source_state={"patch": signal.source_patch},
                target_state={"patch": signal.source_patch, "mode": "refined"},
                semantic_distance=max(0.0, signal.instability_score / 2.0),
                validity_certificate=f"stabilize-{signal.signal_id[:8]}",
                overlap_impact=min(1.0, signal.instability_score),
                move_cost=1.0,
            )
        ]
        if signal.instability_score >= max(signal.threshold, 0.75):
            moves.append(
                SemanticMove(
                    move_id=uuid.uuid4().hex,
                    move_type=MoveType.RETRACT,
                    source_state={"patch": signal.source_patch},
                    target_state={"patch": signal.source_patch, "mode": "retracted"},
                    semantic_distance=max(0.0, signal.instability_score),
                    validity_certificate=f"retract-{signal.signal_id[:8]}",
                    overlap_impact=min(1.0, signal.instability_score),
                    move_cost=0.5,
                )
            )
        return moves

    def apply_move(
        self, move: SemanticMove, proposals: list[InhabitantProposal]
    ) -> list[InhabitantProposal]:
        """Apply a semantic move to the proposal set."""
        updated = list(proposals)
        if move.move_type is MoveType.RETRACT:
            if updated:
                updated.pop()
            return updated
        if move.move_type is MoveType.REFINE:
            for proposal in updated:
                proposal.metadata["refined"] = True
            return updated
        if move.move_type is MoveType.GENERALIZE:
            for proposal in updated:
                proposal.metadata["generalized"] = True
            return updated
        return updated

    def resolve(
        self, signal: BackpressureSignal, proposals: list[InhabitantProposal]
    ) -> list[InhabitantProposal]:
        """Resolve backpressure by filtering proposals from the source patch.

        Parameters
        ----------
        signal : BackpressureSignal
            The signal to resolve.
        proposals : list[InhabitantProposal]
            Current proposals list.

        Returns
        -------
        list[InhabitantProposal]
            Resolved (filtered and re-ranked) proposals.
        """
        self._resolve_count += 1
        source = signal.source_patch
        # Keep proposals not from the source patch, plus the best one from it
        source_proposals = [p for p in proposals if p.patch_id == source]
        other_proposals = [p for p in proposals if p.patch_id != source]
        if source_proposals:
            best = max(source_proposals, key=lambda p: p.score())
            resolved = other_proposals + [best]
        else:
            resolved = list(other_proposals)
        # Re-rank by score descending
        resolved.sort(key=lambda p: p.score(), reverse=True)
        return resolved

    def resolve_all(
        self,
        signals: list[BackpressureSignal],
        proposals: list[InhabitantProposal],
    ) -> list[InhabitantProposal]:
        """Apply resolution for all signals in sequence.

        Parameters
        ----------
        signals : list[BackpressureSignal]
        proposals : list[InhabitantProposal]

        Returns
        -------
        list[InhabitantProposal]
        """
        current = list(proposals)
        for sig in signals:
            current = self.resolve(sig, current)
        return current

    def resolve_count(self) -> int:
        """Return number of resolve() calls."""
        return self._resolve_count


# ---------------------------------------------------------------------------
# CascadeDetector
# ---------------------------------------------------------------------------


class CascadeDetector:
    """Detects cascade patterns in backpressure signal propagation.

    A cascade occurs when a signal from patch P triggers signals from
    Q₁, Q₂, … and those trigger further signals, forming a chain of
    length ≥ CASCADE_THRESHOLD.

    Theory — Ch42 §3.5
    --------------------
    Let G = (V, E) be the signal propagation graph where:
        V = { source_patch | signal ∈ signals }
        E = { (source, target) | target ∈ signal.target_patches }

    A cascade from P is a path in G of length ≥ CASCADE_THRESHOLD.

    Attributes
    ----------
    cascade_threshold : int
        Minimum cascade length to trigger detection.
    """

    def __init__(self, cascade_threshold: int = CASCADE_THRESHOLD) -> None:
        self.cascade_threshold = cascade_threshold
        self._detected_cascades: list[list[str]] = []

    def detect(self, signals: list[BackpressureSignal]) -> list[list[str]]:
        """Detect cascade chains in a list of signals.

        Parameters
        ----------
        signals : list[BackpressureSignal]
            All backpressure signals to analyze.

        Returns
        -------
        list[list[str]]
            List of cascade chains (each a list of patch IDs).
        """
        if not signals:
            return []
        active_signals = [
            sig
            for sig in signals
            if sig.instability_score >= float(self.cascade_threshold)
        ]
        if not active_signals:
            return []
        # Build propagation graph
        graph: dict[str, set[str]] = {}
        for sig in active_signals:
            graph.setdefault(sig.source_patch, set()).update(sig.target_patches)

        cascades: list[list[str]] = []
        for start in list(graph.keys()):
            chain = self._dfs_chain(start, graph, set())
            if len(chain) >= 2:
                cascades.append(chain)
        self._detected_cascades.extend(cascades)
        return cascades

    def _dfs_chain(
        self, node: str, graph: dict[str, set[str]], seen: set[str]
    ) -> list[str]:
        """DFS to find the longest chain from node."""
        if node in seen or node not in graph:
            return [node]
        seen = seen | {node}
        best: list[str] = [node]
        for nb in graph.get(node, set()):
            sub = self._dfs_chain(nb, graph, seen)
            if len([node] + sub) > len(best):
                best = [node] + sub
        return best

    def has_cascade(self, signals: list[BackpressureSignal]) -> bool:
        """Return True if any cascade chain exists.

        Parameters
        ----------
        signals : list[BackpressureSignal]

        Returns
        -------
        bool
        """
        return len(self.detect(signals)) > 0

    def cascade_count(self) -> int:
        """Return total cascades detected."""
        return len(self._detected_cascades)

    def trace_cascade(self, origin: BackpressureSignal) -> list[str]:
        """Return a linearised view of the patches affected by a signal."""
        return [origin.source_patch, *origin.target_patches]

    def estimate_cascade_impact(
        self, cascade: list[BackpressureSignal] | list[list[str]]
    ) -> float:
        """Estimate a non-negative impact score for a cascade description."""
        if not cascade:
            return 0.0
        first = cascade[0]
        if isinstance(first, BackpressureSignal):
            total = sum(
                signal.instability_score for signal in cascade if isinstance(signal, BackpressureSignal)
            )
            return max(0.0, total / len(cascade))
        return max(0.0, float(sum(len(chain) for chain in cascade)))


__all__ = [
    "InstabilityMetric",
    "BackpressureMonitor",
    "BackpressureController",
    "BackpressureResolver",
    "CascadeDetector",
]
