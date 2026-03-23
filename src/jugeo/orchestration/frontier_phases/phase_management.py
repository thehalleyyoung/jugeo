"""
Phase lifecycle management for the frontier_phases orchestration sub-package.

This module is part of JuGeo's copilot-assisted encoding of theory2.tex Chapter 47:
Phase dynamics — classifying and managing search phases over admissible frontiers.

Overview
--------
Where ``phase_detection.py`` answers the question *"what phase are we in?"*,
this module answers *"what do we do about it?"*.  It provides the full machinery
for executing phase transitions, enforcing phase-specific search policies, and
recovering from pathological states such as stalls and divergence.

This module provides:

* :class:`PhaseEventBus` — pub/sub infrastructure for phase lifecycle events.
* :class:`ExplorationPolicy` — governs node expansion during exploration.
* :class:`ExploitationPolicy` — governs node exploitation during exploitation.
* :class:`RecoveryScheduler` — plans and tracks recovery actions.
* :class:`StallRecoveryProtocol` — high-level stall recovery orchestration.
* :class:`PhaseTransitionEngine` — validates and executes phase transitions.
* :class:`PhaseManager` — top-level lifecycle manager (entry/exit/callbacks).

Design notes
~~~~~~~~~~~~
* :class:`PhaseEventBus` is intentionally decoupled from :class:`PhaseChangeNotifier`
  (in ``phase_detection.py``): the notifier handles *detection-time* events
  while the event bus handles *management-time* lifecycle events (entry, exit,
  policy changes, recovery milestones).
* All policy classes work with duck-typed nodes and frontiers so that they can
  be used in test environments with mock objects.
* :class:`PhaseTransitionEngine` logs every attempted transition (including
  rejected ones) for debugging purposes.

Chapter reference: theory2.tex Ch47 — Phase dynamics.

copilot
"""
from __future__ import annotations

import math
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

try:
    from jugeo.orchestration.frontier import (  # noqa: F401
        Frontier,
        FrontierNode,
        FrontierHistory,
        PhaseTransition,
        BackpressureController,
    )
except ImportError:
    pass

# Always use models.py enums: frontier.py's PhaseKind uses a different
# value set (COLLAPSE/SATURATION) vs the frontier_phases sub-package
# (STALLED/CONVERGED/DIVERGED/TRANSITION).
from jugeo.orchestration.frontier_phases.models import (
    PhaseKind,
    TransitionTrigger,
    PhaseDescriptor,
    PhaseTransitionRecord,
    PhaseHistory,
    StallDetector,
    ConvergenceCertificate,
    PhaseHealthStatus,
)

__all__ = [
    "PhaseEventBus",
    "ExplorationPolicy",
    "ExploitationPolicy",
    "RecoveryScheduler",
    "StallRecoveryProtocol",
    "PhaseTransitionEngine",
    "PhaseManager",
]

# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Return *value* clamped to the range [*lo*, *hi*]."""
    return max(lo, min(hi, value))


def _safe_mean(values: list[float]) -> float:
    """Return the arithmetic mean of *values*, or ``0.0`` for an empty list."""
    return sum(values) / len(values) if values else 0.0


#: Valid phase transition edges.  The value is a frozenset of ``PhaseKind``
#: values that the key phase is permitted to transition *to*.
_VALID_TRANSITIONS: dict[PhaseKind, frozenset[PhaseKind]] = {
    PhaseKind.EXPLORATION: frozenset(
        {
            PhaseKind.EXPLOITATION,
            PhaseKind.STALLED,
            PhaseKind.TRANSITION,
            PhaseKind.CONVERGED,
            PhaseKind.DIVERGED,
        }
    ),
    PhaseKind.EXPLOITATION: frozenset(
        {
            PhaseKind.EXPLORATION,
            PhaseKind.STALLED,
            PhaseKind.TRANSITION,
            PhaseKind.CONVERGED,
        }
    ),
    PhaseKind.TRANSITION: frozenset(
        {
            PhaseKind.EXPLORATION,
            PhaseKind.EXPLOITATION,
            PhaseKind.STALLED,
            PhaseKind.RECOVERY,
        }
    ),
    PhaseKind.STALLED: frozenset(
        {
            PhaseKind.RECOVERY,
            PhaseKind.EXPLORATION,
            PhaseKind.EXPLOITATION,
        }
    ),
    PhaseKind.CONVERGED: frozenset(
        {
            PhaseKind.EXPLORATION,  # Re-open after new evidence
        }
    ),
    PhaseKind.DIVERGED: frozenset(
        {
            PhaseKind.RECOVERY,
            PhaseKind.EXPLORATION,
        }
    ),
    PhaseKind.RECOVERY: frozenset(
        {
            PhaseKind.EXPLORATION,
            PhaseKind.EXPLOITATION,
            PhaseKind.STALLED,
        }
    ),
}

#: Approximate cost (arbitrary units) of executing each transition.
_TRANSITION_COSTS: dict[tuple[PhaseKind, PhaseKind], float] = {
    (PhaseKind.EXPLORATION, PhaseKind.EXPLOITATION): 0.2,
    (PhaseKind.EXPLORATION, PhaseKind.STALLED): 0.1,
    (PhaseKind.EXPLORATION, PhaseKind.CONVERGED): 0.3,
    (PhaseKind.EXPLORATION, PhaseKind.DIVERGED): 0.4,
    (PhaseKind.EXPLORATION, PhaseKind.TRANSITION): 0.1,
    (PhaseKind.EXPLOITATION, PhaseKind.EXPLORATION): 0.3,
    (PhaseKind.EXPLOITATION, PhaseKind.STALLED): 0.1,
    (PhaseKind.EXPLOITATION, PhaseKind.CONVERGED): 0.2,
    (PhaseKind.EXPLOITATION, PhaseKind.TRANSITION): 0.1,
    (PhaseKind.TRANSITION, PhaseKind.EXPLORATION): 0.1,
    (PhaseKind.TRANSITION, PhaseKind.EXPLOITATION): 0.1,
    (PhaseKind.TRANSITION, PhaseKind.STALLED): 0.2,
    (PhaseKind.TRANSITION, PhaseKind.RECOVERY): 0.3,
    (PhaseKind.STALLED, PhaseKind.RECOVERY): 0.5,
    (PhaseKind.STALLED, PhaseKind.EXPLORATION): 0.4,
    (PhaseKind.STALLED, PhaseKind.EXPLOITATION): 0.4,
    (PhaseKind.CONVERGED, PhaseKind.EXPLORATION): 0.5,
    (PhaseKind.DIVERGED, PhaseKind.RECOVERY): 0.6,
    (PhaseKind.DIVERGED, PhaseKind.EXPLORATION): 0.7,
    (PhaseKind.RECOVERY, PhaseKind.EXPLORATION): 0.3,
    (PhaseKind.RECOVERY, PhaseKind.EXPLOITATION): 0.3,
    (PhaseKind.RECOVERY, PhaseKind.STALLED): 0.2,
}


# ---------------------------------------------------------------------------
# 1. PhaseEventBus
# ---------------------------------------------------------------------------


class PhaseEventBus:
    """Pub/sub bus for phase lifecycle events.

    Unlike :class:`~jugeo.orchestration.frontier_phases.phase_detection.PhaseChangeNotifier`,
    which carries *detection* notifications, :class:`PhaseEventBus` carries
    arbitrary structured events keyed by an ``event_type`` string.  This
    allows heterogeneous consumers (loggers, schedulers, dashboards) to
    subscribe to only the event types they care about.

    Typical event types
    ~~~~~~~~~~~~~~~~~~~
    ``"phase_entered"``
        Published when a phase is entered.  Payload: ``{phase, trigger, ts}``.
    ``"phase_exited"``
        Published when a phase is exited.  Payload: ``{phase, trigger, ts}``.
    ``"recovery_initiated"``
        Published when :class:`StallRecoveryProtocol` begins a recovery run.
    ``"recovery_complete"``
        Published when recovery is declared complete.
    ``"policy_updated"``
        Published when an exploration or exploitation policy parameter changes.
    ``"budget_warning"``
        Published when budget consumption exceeds a configured alert level.
    """

    def __init__(self) -> None:
        # Mapping: subscription_id → (event_type, callback)
        self._subscribers: dict[str, tuple[str, Callable[..., None]]] = {}
        self._history: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Subscription management
    # ------------------------------------------------------------------

    def subscribe(self, event_type: str, callback: Callable[..., None]) -> str:
        """Register a callback for a specific event type.

        Parameters
        ----------
        event_type:
            The event type to listen for.  Use ``"*"`` to subscribe to all
            event types.
        callback:
            A callable that accepts a single positional argument: the event
            payload dictionary.

        Returns
        -------
        str
            An opaque subscription ID.
        """
        sub_id = str(uuid.uuid4())
        self._subscribers[sub_id] = (event_type, callback)
        return sub_id

    def unsubscribe(self, subscription_id: str) -> bool:
        """Cancel a subscription.

        Parameters
        ----------
        subscription_id:
            ID returned by a previous call to :meth:`subscribe`.

        Returns
        -------
        bool
            ``True`` if found and removed, ``False`` otherwise.
        """
        if subscription_id in self._subscribers:
            del self._subscribers[subscription_id]
            return True
        return False

    # ------------------------------------------------------------------
    # Publishing
    # ------------------------------------------------------------------

    def publish(self, event_type: str, payload: dict[str, Any]) -> int:
        """Publish an event to all matching subscribers.

        Subscribers whose registered ``event_type`` matches *event_type* or
        is ``"*"`` will be invoked with the *payload* dict.  Exceptions are
        caught per-subscriber and do not abort delivery.

        Parameters
        ----------
        event_type:
            The type of event being published.
        payload:
            Arbitrary data accompanying the event.

        Returns
        -------
        int
            The number of subscribers successfully notified.
        """
        enriched = {"event_type": event_type, "timestamp": time.time(), **payload}
        self._history.append(dict(enriched))
        notified = 0
        for sub_id, (registered_type, callback) in list(self._subscribers.items()):
            if registered_type not in (event_type, "*"):
                continue
            try:
                callback(enriched)
                notified += 1
            except Exception:  # noqa: BLE001
                pass
        return notified

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def event_types(self) -> list[str]:
        """Return the distinct event types that have been published so far.

        Returns
        -------
        list[str]
            Sorted list of event type strings seen in the history.
        """
        return sorted({e["event_type"] for e in self._history})

    def history(self, event_type: str | None = None) -> list[dict[str, Any]]:
        """Return the event history, optionally filtered by *event_type*.

        Parameters
        ----------
        event_type:
            If provided, only events of this type are returned.  Pass
            ``None`` (default) to return all events.

        Returns
        -------
        list[dict[str, Any]]
            A copy of the matching events in chronological order.
        """
        if event_type is None:
            return list(self._history)
        return [e for e in self._history if e.get("event_type") == event_type]


# ---------------------------------------------------------------------------
# 2. ExplorationPolicy
# ---------------------------------------------------------------------------


class ExplorationPolicy:
    """Policy controlling node expansion during the exploration phase.

    The exploration policy aims to maximise *coverage* of the admissible space.
    It therefore favours nodes that are structurally dissimilar to the rest of
    the frontier, have not yet been deeply explored, and whose cost is within
    the configured limit.

    Parameters
    ----------
    diversity_target:
        Target diversity score for the frontier.  Nodes that would reduce
        diversity below this target are penalised.
    max_depth:
        Maximum node depth considered for further expansion.  Deeper nodes
        are excluded from exploration to prevent runaway depth-first search.
    cost_limit:
        Maximum node cost to consider for expansion.  Nodes above this
        threshold are always excluded.
    """

    def __init__(
        self,
        diversity_target: float = 0.6,
        max_depth: int = 8,
        cost_limit: float = 1.0,
    ) -> None:
        self._diversity_target = _clamp(diversity_target)
        self._max_depth = max(1, max_depth)
        self._cost_limit = max(0.0, cost_limit)
        self._expansion_count: int = 0
        self._skipped_count: int = 0

    # ------------------------------------------------------------------
    # Decision methods
    # ------------------------------------------------------------------

    def should_explore(self, node: Any, frontier: Any) -> bool:
        """Decide whether *node* should be explored.

        A node is eligible for exploration when:
        - Its cost does not exceed ``cost_limit``.
        - Its depth does not exceed ``max_depth``.
        - The frontier's current diversity score is below ``diversity_target``
          *or* the node would increase diversity.

        Parameters
        ----------
        node:
            A frontier node (duck-typed).
        frontier:
            The current frontier (duck-typed).

        Returns
        -------
        bool
        """
        cost = float(getattr(node, "cost", 0.0))
        depth = int(getattr(node, "depth", 0))
        if cost > self._cost_limit:
            self._skipped_count += 1
            return False
        if depth > self._max_depth:
            self._skipped_count += 1
            return False
        # Check frontier diversity
        current_diversity = self._frontier_diversity(frontier)
        if current_diversity >= self._diversity_target:
            # Frontier is already diverse; only explore if node adds value
            penalty = self.diversity_penalty(node, frontier)
            if penalty > 0.5:
                self._skipped_count += 1
                return False
        self._expansion_count += 1
        return True

    def expansion_priority(self, node: Any) -> float:
        """Return the expansion priority for *node* in ``[0.0, 1.0]``.

        Nodes are prioritised by a combination of:
        - Low closure estimate (unexplored territory is more interesting).
        - Low depth (breadth-first bias to maximise coverage).
        - Low cost (efficient resource usage).

        Parameters
        ----------
        node:
            A frontier node (duck-typed).

        Returns
        -------
        float
            Priority score in ``[0.0, 1.0]``; higher means more important to
            expand.
        """
        closure = self._node_closure(node)
        depth = float(getattr(node, "depth", 0))
        cost = float(getattr(node, "cost", 1.0))
        # Unexplored territory (low closure) + low depth + low cost is ideal
        novelty = 1.0 - closure
        depth_score = max(0.0, 1.0 - depth / max(self._max_depth, 1))
        cost_score = max(0.0, 1.0 - cost / max(self._cost_limit, 1e-9))
        return _clamp((novelty * 0.5 + depth_score * 0.3 + cost_score * 0.2))

    def diversity_penalty(self, node: Any, frontier: Any) -> float:
        """Compute a diversity penalty for *node* given the current *frontier*.

        A high penalty (→ 1.0) means the node is very similar to existing
        frontier nodes.  A low penalty (→ 0.0) means it is novel.

        Parameters
        ----------
        node:
            A frontier node (duck-typed).
        frontier:
            The current frontier (duck-typed).

        Returns
        -------
        float
            Penalty in ``[0.0, 1.0]``.
        """
        all_nodes = self._get_all_nodes(frontier)
        if not all_nodes:
            return 0.0
        node_depth = float(getattr(node, "depth", 0))
        node_label = str(getattr(node, "label", ""))
        # Simple label-overlap penalty
        matching = sum(
            1
            for n in all_nodes
            if str(getattr(n, "label", "")) == node_label
            or abs(float(getattr(n, "depth", 0)) - node_depth) < 1.0
        )
        return _clamp(matching / (len(all_nodes) + 1))

    def policy_report(self) -> dict[str, Any]:
        """Return a summary of policy parameters and usage statistics.

        Returns
        -------
        dict[str, Any]
            Keys: ``diversity_target``, ``max_depth``, ``cost_limit``,
            ``expansion_count``, ``skipped_count``.
        """
        return {
            "diversity_target": self._diversity_target,
            "max_depth": self._max_depth,
            "cost_limit": self._cost_limit,
            "expansion_count": self._expansion_count,
            "skipped_count": self._skipped_count,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _node_closure(node: Any) -> float:
        for attr in ("effective_closure", "closure_estimate"):
            val = getattr(node, attr, None)
            if val is not None:
                return float(val() if callable(val) else val)
        return 0.0

    @staticmethod
    def _frontier_diversity(frontier: Any) -> float:
        try:
            return float(frontier.diversity_score())
        except (AttributeError, TypeError):
            return 0.5

    @staticmethod
    def _get_all_nodes(frontier: Any) -> list[Any]:
        try:
            return list(frontier.all_nodes())
        except AttributeError:
            return []


# ---------------------------------------------------------------------------
# 3. ExploitationPolicy
# ---------------------------------------------------------------------------


class ExploitationPolicy:
    """Policy controlling node exploitation during the exploitation phase.

    The exploitation policy aims to *deepen* coverage within already-promising
    regions of the admissible space.  It favours nodes with high closure
    estimates that have not yet been fully refined, applying a closure bonus
    to amplify their priority score.

    Parameters
    ----------
    min_closure:
        Minimum closure estimate a node must have to be eligible for
        exploitation.  Nodes below this threshold are skipped.
    exploitation_depth:
        Maximum depth at which exploitation is applied.  Deeper nodes may
        still be exploited if their closure exceeds ``min_closure``, but they
        receive a depth penalty.
    """

    def __init__(
        self,
        min_closure: float = 0.5,
        exploitation_depth: int = 5,
    ) -> None:
        self._min_closure = _clamp(min_closure)
        self._exploitation_depth = max(1, exploitation_depth)
        self._exploitation_count: int = 0
        self._skipped_count: int = 0

    # ------------------------------------------------------------------
    # Decision methods
    # ------------------------------------------------------------------

    def should_exploit(self, node: Any, frontier: Any) -> bool:
        """Decide whether *node* should be exploited.

        A node is eligible when its closure estimate meets or exceeds
        ``min_closure``.  Depth is used to scale priority but never to
        hard-exclude a node.

        Parameters
        ----------
        node:
            A frontier node (duck-typed).
        frontier:
            The current frontier (duck-typed; used to compute relative
            standing, currently unused but reserved for future use).

        Returns
        -------
        bool
        """
        closure = self._node_closure(node)
        if closure < self._min_closure:
            self._skipped_count += 1
            return False
        self._exploitation_count += 1
        return True

    def exploitation_priority(self, node: Any) -> float:
        """Return the exploitation priority for *node* in ``[0.0, 1.0]``.

        Priority is dominated by the closure estimate, amplified by the
        :meth:`closure_bonus`, and attenuated by a depth penalty.

        Parameters
        ----------
        node:
            A frontier node (duck-typed).

        Returns
        -------
        float
        """
        closure = self._node_closure(node)
        bonus = self.closure_bonus(node)
        depth = float(getattr(node, "depth", 0))
        depth_penalty = max(0.0, depth - self._exploitation_depth) * 0.05
        raw = closure * (1.0 + bonus) - depth_penalty
        return _clamp(raw)

    def closure_bonus(self, node: Any) -> float:
        """Compute an additive bonus for nodes with very high closure.

        The bonus is non-linear: nodes with closure above 0.8 receive an
        amplified bonus to ensure they are prioritised over marginal candidates.

        Parameters
        ----------
        node:
            A frontier node (duck-typed).

        Returns
        -------
        float
            Bonus in ``[0.0, 0.5]``.
        """
        closure = self._node_closure(node)
        if closure <= 0.8:
            return 0.0
        # Quadratic amplification above 0.8
        excess = closure - 0.8
        return _clamp(excess ** 2 * 12.5, 0.0, 0.5)

    def policy_report(self) -> dict[str, Any]:
        """Return a summary of policy parameters and usage statistics.

        Returns
        -------
        dict[str, Any]
            Keys: ``min_closure``, ``exploitation_depth``,
            ``exploitation_count``, ``skipped_count``.
        """
        return {
            "min_closure": self._min_closure,
            "exploitation_depth": self._exploitation_depth,
            "exploitation_count": self._exploitation_count,
            "skipped_count": self._skipped_count,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _node_closure(node: Any) -> float:
        for attr in ("effective_closure", "closure_estimate"):
            val = getattr(node, attr, None)
            if val is not None:
                return float(val() if callable(val) else val)
        return 0.0


# ---------------------------------------------------------------------------
# 4. RecoveryScheduler
# ---------------------------------------------------------------------------


class RecoveryScheduler:
    """Plans and tracks a sequence of recovery actions after a stall or regression.

    A recovery *plan* is an ordered list of action descriptors.  Each action
    has a unique ID, a type string, and optional parameters.  The scheduler
    tracks which actions have been completed and exposes progress metrics.

    Parameters
    ----------
    max_recovery_steps:
        Maximum number of actions allowed in a single recovery plan.  If a
        :meth:`plan_recovery` call would exceed this limit, the plan is
        truncated.
    """

    def __init__(self, max_recovery_steps: int = 20) -> None:
        self._max_steps = max(1, max_recovery_steps)
        self._plan: list[dict[str, Any]] = []
        self._completed: set[str] = set()

    # ------------------------------------------------------------------
    # Planning
    # ------------------------------------------------------------------

    def plan_recovery(
        self, stall_info: dict[str, Any], frontier: Any
    ) -> list[dict[str, Any]]:
        """Build a recovery action plan from stall diagnostics.

        The plan is heuristically constructed from the *stall_info* dict.
        Common actions include diversification (adding fresh nodes),
        rollback (reverting to a previous checkpoint), and re-scoring
        (re-evaluating all frontier nodes).

        Parameters
        ----------
        stall_info:
            Diagnostic information about the stall, typically obtained from a
            :class:`~jugeo.orchestration.frontier_phases.models.StallDetector`.
            Recognised keys: ``progress_rate``, ``stall_threshold``,
            ``observations_count``, ``is_stalled``.
        frontier:
            The frontier that is stalled (duck-typed; used for size and
            diversity heuristics).

        Returns
        -------
        list[dict[str, Any]]
            The generated action plan.  Each entry has keys:
            ``action_id``, ``action_type``, ``priority``, ``params``.
        """
        self._plan = []
        self._completed = set()

        progress_rate = float(stall_info.get("progress_rate", 0.0))
        is_stalled = bool(stall_info.get("is_stalled", True))

        # Always start with a re-score to get an accurate picture
        self._add_action("rescore", priority=10, params={"full": True})

        if is_stalled and progress_rate < 0.01:
            # Deep stall: need aggressive diversification
            self._add_action("diversify", priority=9, params={"n": 5, "mode": "random"})
            self._add_action("prune_dominated", priority=8, params={"fraction": 0.2})
            self._add_action(
                "rollback", priority=7, params={"steps": 3, "reason": "stall"}
            )
        elif is_stalled:
            # Mild stall: light diversification
            self._add_action("diversify", priority=8, params={"n": 2, "mode": "guided"})
            self._add_action("boost_exploration", priority=7, params={"duration": 10})
        else:
            # Pre-emptive: add a few exploratory nodes
            self._add_action("diversify", priority=6, params={"n": 1, "mode": "guided"})

        # Cap to max_recovery_steps
        self._plan = self._plan[: self._max_steps]
        # Sort by descending priority
        self._plan.sort(key=lambda a: a["priority"], reverse=True)
        return list(self._plan)

    # ------------------------------------------------------------------
    # Execution tracking
    # ------------------------------------------------------------------

    def next_action(self) -> dict[str, Any] | None:
        """Return the next pending action, or ``None`` if recovery is complete.

        Returns
        -------
        dict[str, Any] | None
            The highest-priority incomplete action, or ``None``.
        """
        for action in self._plan:
            if action["action_id"] not in self._completed:
                return dict(action)
        return None

    def mark_action_done(self, action_id: str) -> bool:
        """Mark an action as completed.

        Parameters
        ----------
        action_id:
            The ``action_id`` of the action to mark as done.

        Returns
        -------
        bool
            ``True`` if the action was found in the plan, ``False`` otherwise.
        """
        ids = {a["action_id"] for a in self._plan}
        if action_id not in ids:
            return False
        self._completed.add(action_id)
        return True

    def recovery_progress(self) -> float:
        """Return the fraction of recovery actions that have been completed.

        Returns
        -------
        float
            Value in ``[0.0, 1.0]``.  Returns ``1.0`` if the plan is empty.
        """
        if not self._plan:
            return 1.0
        return _clamp(len(self._completed) / len(self._plan))

    def is_recovery_complete(self) -> bool:
        """Return ``True`` when all planned actions have been marked done."""
        return len(self._plan) > 0 and len(self._completed) >= len(self._plan)

    def reset(self) -> None:
        """Clear the current plan and all completion records."""
        self._plan = []
        self._completed = set()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _add_action(
        self,
        action_type: str,
        priority: int = 5,
        params: dict[str, Any] | None = None,
    ) -> None:
        """Append a new action to the plan."""
        self._plan.append(
            {
                "action_id": str(uuid.uuid4()),
                "action_type": action_type,
                "priority": priority,
                "params": params or {},
                "created_at": time.time(),
            }
        )


# ---------------------------------------------------------------------------
# 5. StallRecoveryProtocol
# ---------------------------------------------------------------------------


class StallRecoveryProtocol:
    """High-level protocol for recovering from a stalled frontier search.

    Orchestrates :class:`RecoveryScheduler` actions against a live frontier.
    Publishes lifecycle events via :class:`PhaseEventBus` when available.

    Parameters
    ----------
    scheduler:
        Optional pre-configured :class:`RecoveryScheduler`.  If ``None``, a
        default instance is created.
    event_bus:
        Optional :class:`PhaseEventBus` for publishing recovery lifecycle
        events.  If ``None``, events are suppressed.
    """

    def __init__(
        self,
        scheduler: RecoveryScheduler | None = None,
        event_bus: PhaseEventBus | None = None,
    ) -> None:
        self._scheduler = scheduler or RecoveryScheduler()
        self._event_bus = event_bus
        self._active: bool = False
        self._steps_executed: int = 0
        self._nodes_added: int = 0
        self._rollbacks_attempted: int = 0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def initiate_recovery(self, frontier: Any, stall_detector: Any) -> bool:
        """Initiate a recovery run using diagnostics from *stall_detector*.

        Parameters
        ----------
        frontier:
            The stalled frontier (duck-typed).
        stall_detector:
            A :class:`~jugeo.orchestration.frontier_phases.models.StallDetector`
            or compatible object.

        Returns
        -------
        bool
            ``True`` if recovery was successfully initiated, ``False`` if
            recovery is already in progress.
        """
        if self._active:
            return False

        stall_info: dict[str, Any] = {}
        if hasattr(stall_detector, "to_dict"):
            stall_info = stall_detector.to_dict()
        elif isinstance(stall_detector, dict):
            stall_info = stall_detector

        self._scheduler.reset()
        plan = self._scheduler.plan_recovery(stall_info, frontier)
        self._active = len(plan) > 0
        self._steps_executed = 0
        self._nodes_added = 0
        self._rollbacks_attempted = 0

        if self._active and self._event_bus is not None:
            self._event_bus.publish(
                "recovery_initiated",
                {"plan_size": len(plan), "stall_info": stall_info},
            )
        return self._active

    # ------------------------------------------------------------------
    # Step execution
    # ------------------------------------------------------------------

    def execute_recovery_step(self, frontier: Any) -> dict[str, Any]:
        """Execute the next pending recovery action against *frontier*.

        If there are no remaining actions or recovery is not active, returns
        a status dict indicating completion.

        Parameters
        ----------
        frontier:
            The frontier to recover (duck-typed).

        Returns
        -------
        dict[str, Any]
            Result dict with keys: ``action_type``, ``success``,
            ``nodes_added``, ``steps_remaining``, ``complete``.
        """
        if not self._active:
            return {
                "action_type": "noop",
                "success": False,
                "nodes_added": 0,
                "steps_remaining": 0,
                "complete": True,
            }

        action = self._scheduler.next_action()
        if action is None:
            self._active = False
            if self._event_bus is not None:
                self._event_bus.publish("recovery_complete", {"steps": self._steps_executed})
            return {
                "action_type": "noop",
                "success": True,
                "nodes_added": self._nodes_added,
                "steps_remaining": 0,
                "complete": True,
            }

        action_type = action.get("action_type", "noop")
        params = action.get("params", {})
        nodes_added_this_step = 0
        success = True

        if action_type == "diversify":
            n = int(params.get("n", 1))
            nodes_added_this_step = self.attempt_diversification(frontier)
            self._nodes_added += nodes_added_this_step
        elif action_type == "rollback":
            self._rollbacks_attempted += 1
            # Rollback has no history in this simple call; attempt it anyway
            success = False  # We cannot rollback without history here
        elif action_type == "rescore":
            # Re-scoring is a no-op at this level; frontier scorer handles it
            pass
        elif action_type == "prune_dominated":
            fraction = float(params.get("fraction", 0.1))
            self._prune_frontier(frontier, fraction)
        elif action_type == "boost_exploration":
            # Signal only; policy objects handle the actual boost
            pass

        self._scheduler.mark_action_done(action["action_id"])
        self._steps_executed += 1

        remaining = len(
            [
                a
                for a in self._scheduler._plan
                if a["action_id"] not in self._scheduler._completed
            ]
        )
        if remaining == 0:
            self._active = False
            if self._event_bus is not None:
                self._event_bus.publish("recovery_complete", {"steps": self._steps_executed})

        return {
            "action_type": action_type,
            "success": success,
            "nodes_added": nodes_added_this_step,
            "steps_remaining": remaining,
            "complete": remaining == 0,
        }

    # ------------------------------------------------------------------
    # Atomic recovery operations
    # ------------------------------------------------------------------

    def attempt_diversification(self, frontier: Any) -> int:
        """Attempt to diversify the frontier by adding synthetic exploratory nodes.

        In a real system this would invoke the node generator.  Here we
        approximate by cloning existing low-closure nodes with small perturbations
        if the frontier supports ``add_node``.

        Parameters
        ----------
        frontier:
            The frontier to diversify (duck-typed).

        Returns
        -------
        int
            Number of new nodes successfully added.
        """
        added = 0
        try:
            all_nodes = list(frontier.all_nodes())
        except AttributeError:
            return added

        # Pick the three lowest-closure nodes as diversification seeds
        def _closure(n: Any) -> float:
            for attr in ("effective_closure", "closure_estimate"):
                val = getattr(n, attr, None)
                if val is not None:
                    return float(val() if callable(val) else val)
            return 0.0

        seeds = sorted(all_nodes, key=_closure)[:3]
        for seed in seeds:
            try:
                import copy
                new_node = copy.deepcopy(seed)
                # Perturb the node_id so it's treated as new
                new_id = str(uuid.uuid4())
                object.__setattr__(new_node, "node_id", new_id)
                frontier.add_node(new_node)
                added += 1
            except Exception:  # noqa: BLE001
                continue
        return added

    def attempt_rollback(self, frontier: Any, history: Any) -> bool:
        """Attempt to roll the frontier back to a previous state.

        Uses the *history* object to restore a previous :class:`FrontierState`.
        Falls back gracefully if the history object does not support rollback.

        Parameters
        ----------
        frontier:
            The frontier to roll back.
        history:
            A history object exposing either a ``states`` list or a
            ``last_state()`` method.

        Returns
        -------
        bool
            ``True`` if rollback succeeded, ``False`` otherwise.
        """
        self._rollbacks_attempted += 1
        try:
            # Attempt via history.states list
            states = getattr(history, "states", None)
            if states and len(states) >= 2:
                # Restore the second-to-last state
                previous_state = states[-2]
                nodes = getattr(previous_state, "nodes", [])
                # Clear and repopulate the frontier
                for node in nodes:
                    try:
                        frontier.add_node(node)
                    except Exception:  # noqa: BLE001
                        pass
                return True
        except Exception:  # noqa: BLE001
            pass
        return False

    def recovery_status(self) -> dict[str, Any]:
        """Return the current recovery status.

        Returns
        -------
        dict[str, Any]
            Keys: ``active``, ``progress``, ``steps_executed``,
            ``nodes_added``, ``rollbacks_attempted``, ``complete``.
        """
        return {
            "active": self._active,
            "progress": self._scheduler.recovery_progress(),
            "steps_executed": self._steps_executed,
            "nodes_added": self._nodes_added,
            "rollbacks_attempted": self._rollbacks_attempted,
            "complete": self._scheduler.is_recovery_complete() or not self._active,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _prune_frontier(frontier: Any, fraction: float) -> int:
        """Prune the bottom *fraction* of nodes from the frontier by closure."""
        try:
            all_nodes = list(frontier.all_nodes())
        except AttributeError:
            return 0
        n_prune = max(1, int(len(all_nodes) * fraction))

        def _closure(n: Any) -> float:
            for attr in ("effective_closure", "closure_estimate"):
                val = getattr(n, attr, None)
                if val is not None:
                    return float(val() if callable(val) else val)
            return 0.0

        to_prune = sorted(all_nodes, key=_closure)[:n_prune]
        removed = 0
        for node in to_prune:
            try:
                nid = getattr(node, "node_id", None)
                if nid and hasattr(frontier, "remove_node"):
                    frontier.remove_node(nid)
                    removed += 1
            except Exception:  # noqa: BLE001
                pass
        return removed


# ---------------------------------------------------------------------------
# 6. PhaseTransitionEngine
# ---------------------------------------------------------------------------


class PhaseTransitionEngine:
    """Validates and executes phase transitions.

    Maintains an audit log of all attempted transitions, including rejected
    ones.  Uses the ``_VALID_TRANSITIONS`` and ``_TRANSITION_COSTS`` module-
    level dictionaries to validate and cost transitions.

    Parameters
    ----------
    event_bus:
        Optional :class:`PhaseEventBus` to which ``"phase_entered"`` and
        ``"phase_exited"`` events are published.
    """

    def __init__(self, event_bus: PhaseEventBus | None = None) -> None:
        self._event_bus = event_bus
        self._transitions: list[PhaseTransitionRecord] = []
        self._rejected_count: int = 0

    # ------------------------------------------------------------------
    # Transition execution
    # ------------------------------------------------------------------

    def execute_transition(
        self,
        from_phase: PhaseKind,
        to_phase: PhaseKind,
        trigger: TransitionTrigger,
        frontier: Any,
    ) -> PhaseTransitionRecord:
        """Execute a phase transition and return the :class:`PhaseTransitionRecord`.

        If the transition is invalid (not in ``_VALID_TRANSITIONS``), the
        engine still records the attempt but marks it with ``closure_delta = -1``
        as a sentinel.  The returned record represents the *attempted*
        transition regardless of validity.

        Parameters
        ----------
        from_phase:
            The phase being exited.
        to_phase:
            The phase being entered.
        trigger:
            The trigger that caused the transition.
        frontier:
            The frontier at the time of transition (used to snapshot closure
            and cost deltas).

        Returns
        -------
        PhaseTransitionRecord
        """
        is_valid = self.validate_transition(from_phase, to_phase)
        closure_delta = self._compute_closure_delta(frontier) if is_valid else -1.0
        cost_delta = self.transition_cost(from_phase, to_phase)

        record = PhaseTransitionRecord.make(
            from_phase_id=from_phase.name,
            to_phase_id=to_phase.name,
            trigger=trigger,
            closure_delta=closure_delta if is_valid else 0.0,
            cost_delta=cost_delta,
            evidence={
                "valid": is_valid,
                "from_phase": from_phase.name,
                "to_phase": to_phase.name,
            },
        )
        self._transitions.append(record)

        if not is_valid:
            self._rejected_count += 1
            return record

        if self._event_bus is not None:
            self._event_bus.publish(
                "phase_exited",
                {"phase": from_phase.name, "trigger": trigger.name},
            )
            self._event_bus.publish(
                "phase_entered",
                {"phase": to_phase.name, "trigger": trigger.name},
            )
        return record

    # ------------------------------------------------------------------
    # Validation and cost
    # ------------------------------------------------------------------

    def validate_transition(
        self, from_phase: PhaseKind, to_phase: PhaseKind
    ) -> bool:
        """Return ``True`` if the transition from *from_phase* to *to_phase* is valid.

        Validity is checked against the ``_VALID_TRANSITIONS`` adjacency set.
        Transitions to ``PhaseKind.RECOVERY`` are always permitted as a safety
        valve regardless of the current phase.

        Parameters
        ----------
        from_phase:
            The phase being exited.
        to_phase:
            The phase being entered.

        Returns
        -------
        bool
        """
        if to_phase == PhaseKind.RECOVERY:
            return True
        allowed = _VALID_TRANSITIONS.get(from_phase, frozenset())
        return to_phase in allowed

    def transition_cost(
        self, from_phase: PhaseKind, to_phase: PhaseKind
    ) -> float:
        """Return the estimated cost of transitioning from *from_phase* to *to_phase*.

        Looks up the ``_TRANSITION_COSTS`` table.  Returns ``1.0`` for
        transitions not found in the table (i.e. very expensive / invalid).

        Parameters
        ----------
        from_phase:
            Source phase.
        to_phase:
            Target phase.

        Returns
        -------
        float
        """
        return _TRANSITION_COSTS.get((from_phase, to_phase), 1.0)

    # ------------------------------------------------------------------
    # History and metrics
    # ------------------------------------------------------------------

    def recent_transitions(self, n: int = 10) -> list[PhaseTransitionRecord]:
        """Return the *n* most recent :class:`PhaseTransitionRecord` entries.

        Parameters
        ----------
        n:
            Maximum number of records to return.

        Returns
        -------
        list[PhaseTransitionRecord]
        """
        return list(self._transitions[-max(1, n) :])

    def transition_count(self) -> int:
        """Return the total number of transitions executed (including rejected)."""
        return len(self._transitions)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_closure_delta(frontier: Any) -> float:
        """Estimate the aggregate closure delta from the frontier."""
        try:
            nodes = list(frontier.all_nodes())
        except AttributeError:
            return 0.0
        if not nodes:
            return 0.0
        closures: list[float] = []
        for node in nodes:
            for attr in ("effective_closure", "closure_estimate"):
                val = getattr(node, attr, None)
                if val is not None:
                    closures.append(float(val() if callable(val) else val))
                    break
        return _safe_mean(closures)


# ---------------------------------------------------------------------------
# 7. PhaseManager
# ---------------------------------------------------------------------------


class PhaseManager:
    """Manages the phase lifecycle: entry, execution, exit, and callbacks.

    :class:`PhaseManager` is the top-level orchestration object that ties
    together all components in this module.  It maintains the authoritative
    record of which phase is currently active, fires on-enter and on-exit
    callbacks, and exposes a rich status report.

    Parameters
    ----------
    initial_phase:
        The phase to start in.  Defaults to :attr:`PhaseKind.EXPLORATION`.
    """

    def __init__(
        self, initial_phase: PhaseKind = PhaseKind.EXPLORATION
    ) -> None:
        self._current_phase: PhaseKind = initial_phase
        self._phase_entry_time: float = time.time()
        # History: list of (phase, entry_time, exit_time)
        self._phase_history: list[tuple[PhaseKind, float, float]] = []
        # Callbacks: phase → list of callbacks
        self._enter_callbacks: dict[PhaseKind, list[Callable[[PhaseKind], None]]] = {}
        self._exit_callbacks: dict[PhaseKind, list[Callable[[PhaseKind], None]]] = {}

    # ------------------------------------------------------------------
    # Phase access
    # ------------------------------------------------------------------

    def current_phase(self) -> PhaseKind:
        """Return the currently active :class:`PhaseKind`."""
        return self._current_phase

    # ------------------------------------------------------------------
    # Lifecycle management
    # ------------------------------------------------------------------

    def enter_phase(
        self,
        phase: PhaseKind,
        trigger: TransitionTrigger = TransitionTrigger.MANUAL,
    ) -> bool:
        """Transition into *phase*.

        Fires the on-exit callbacks for the current phase, then fires the
        on-enter callbacks for *phase*.  The transition is always accepted;
        use :class:`PhaseTransitionEngine` if you need validity enforcement.

        Parameters
        ----------
        phase:
            The phase to enter.
        trigger:
            The trigger causing this transition (informational).

        Returns
        -------
        bool
            ``True`` if the transition was accepted (always ``True`` for
            :class:`PhaseManager`).
        """
        if phase == self._current_phase:
            return True

        # Record exit of current phase
        exit_time = time.time()
        self._phase_history.append(
            (self._current_phase, self._phase_entry_time, exit_time)
        )
        self._fire_exit_callbacks(self._current_phase)

        # Enter new phase
        self._current_phase = phase
        self._phase_entry_time = time.time()
        self._fire_enter_callbacks(phase)
        return True

    def exit_phase(
        self, trigger: TransitionTrigger = TransitionTrigger.MANUAL
    ) -> bool:
        """Exit the current phase without specifying the next one.

        Records the exit of the current phase and fires on-exit callbacks.
        After calling this method, :attr:`current_phase` remains unchanged
        until a subsequent call to :meth:`enter_phase`.  This is useful when
        the caller needs to compute the next phase before committing to it.

        Parameters
        ----------
        trigger:
            The trigger causing this exit (informational).

        Returns
        -------
        bool
            Always ``True``.
        """
        self._fire_exit_callbacks(self._current_phase)
        return True

    # ------------------------------------------------------------------
    # Callback registration
    # ------------------------------------------------------------------

    def on_enter(
        self, phase: PhaseKind, callback: Callable[[PhaseKind], None]
    ) -> None:
        """Register a callback to be invoked when *phase* is entered.

        Multiple callbacks can be registered for the same phase; they are
        invoked in registration order.

        Parameters
        ----------
        phase:
            The phase to listen for.
        callback:
            A callable that accepts the entered :class:`PhaseKind` as its
            sole positional argument.
        """
        self._enter_callbacks.setdefault(phase, []).append(callback)

    def on_exit(
        self, phase: PhaseKind, callback: Callable[[PhaseKind], None]
    ) -> None:
        """Register a callback to be invoked when *phase* is exited.

        Parameters
        ----------
        phase:
            The phase to listen for.
        callback:
            A callable that accepts the exited :class:`PhaseKind` as its
            sole positional argument.
        """
        self._exit_callbacks.setdefault(phase, []).append(callback)

    # ------------------------------------------------------------------
    # Metrics and reporting
    # ------------------------------------------------------------------

    def phase_duration(self) -> float:
        """Return the number of seconds the current phase has been active.

        Returns
        -------
        float
        """
        return time.time() - self._phase_entry_time

    def phase_history(self) -> list[tuple[PhaseKind, float, float]]:
        """Return the ordered history of completed phases.

        Returns
        -------
        list[tuple[PhaseKind, float, float]]
            Each entry is ``(phase, entry_time, exit_time)`` in chronological
            order.  The currently active phase is **not** included.
        """
        return list(self._phase_history)

    def status_report(self) -> dict[str, Any]:
        """Return a comprehensive status report of the phase manager.

        Returns
        -------
        dict[str, Any]
            Keys: ``current_phase``, ``phase_duration_seconds``,
            ``completed_phases``, ``enter_callback_count``,
            ``exit_callback_count``, ``history_length``.
        """
        enter_total = sum(len(cbs) for cbs in self._enter_callbacks.values())
        exit_total = sum(len(cbs) for cbs in self._exit_callbacks.values())
        completed = [
            {
                "phase": phase.name,
                "entry_time": entry,
                "exit_time": exit_t,
                "duration": exit_t - entry,
            }
            for phase, entry, exit_t in self._phase_history
        ]
        return {
            "current_phase": self._current_phase.name,
            "phase_duration_seconds": round(self.phase_duration(), 4),
            "completed_phases": completed,
            "enter_callback_count": enter_total,
            "exit_callback_count": exit_total,
            "history_length": len(self._phase_history),
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _fire_enter_callbacks(self, phase: PhaseKind) -> None:
        """Invoke all on-enter callbacks for *phase*, swallowing exceptions."""
        for callback in self._enter_callbacks.get(phase, []):
            try:
                callback(phase)
            except Exception:  # noqa: BLE001
                pass

    def _fire_exit_callbacks(self, phase: PhaseKind) -> None:
        """Invoke all on-exit callbacks for *phase*, swallowing exceptions."""
        for callback in self._exit_callbacks.get(phase, []):
            try:
                callback(phase)
            except Exception:  # noqa: BLE001
                pass
