"""Invalidation cascade infrastructure for incremental memory — theory2.tex Ch34.

This module implements the encoding-layer invalidation cascade infrastructure,
developed with copilot assistance. It provides policies, tracers, cascade
computers, schedulers, and repair planning for the incremental_memory subsystem.

This module wraps and extends jugeo.runtime.invalidation with encoding-specific
semantics, maintaining a clear separation between the runtime invalidation layer
and the encoding layer. The CascadePolicy enum governs how cascades propagate
through the dependency graph.

Theory reference: theory2.tex §34.4 — Cascade semantics and termination.
"""
from __future__ import annotations

import uuid
import time
import json
import logging
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any

logger = logging.getLogger(__name__)

try:
    from jugeo.runtime.invalidation import (
        InvalidationGraph,
        InvalidationEngine,
        InvalidationPolicy,
        InvalidationCascade,
        InvalidationReason,
        InvalidationEvent,
        TriggerKind,
    )
except ImportError:
    InvalidationGraph = Any  # type: ignore
    InvalidationEngine = Any  # type: ignore
    InvalidationPolicy = Any  # type: ignore
    InvalidationCascade = Any  # type: ignore
    InvalidationReason = Any  # type: ignore
    InvalidationEvent = Any  # type: ignore
    TriggerKind = Any  # type: ignore

try:
    from jugeo.runtime.memory import SemanticMemory, MemoryRegion
except ImportError:
    SemanticMemory = Any  # type: ignore
    MemoryRegion = Any  # type: ignore

try:
    from jugeo.encodings.incremental_memory.models import (
        ChangeEvent,
        ChangeEventKind,
        EncodingSupportSet,
        MemoryInvalidationCascade,
        InvalidationWaveInfo,
    )
except ImportError:
    ChangeEvent = Any  # type: ignore
    ChangeEventKind = Any  # type: ignore
    EncodingSupportSet = Any  # type: ignore
    MemoryInvalidationCascade = Any  # type: ignore
    InvalidationWaveInfo = Any  # type: ignore


# ---------------------------------------------------------------------------
# CascadePolicy
# ---------------------------------------------------------------------------


class CascadePolicy(Enum):
    """Governs how invalidation cascades propagate through the dependency graph.

    Each member represents a distinct propagation strategy that trades off
    completeness against computational cost.  The policy is consulted by
    ``CascadeComputer.apply_policy`` to post-process a fully computed cascade
    before it is handed back to the caller.

    Theory reference: theory2.tex §34.4.1.
    """

    EAGER = auto()
    """Propagate invalidation to all transitively reachable dependents.

    This is the most aggressive policy.  Every node reachable from the trigger
    coordinate through the forward dependency edges is marked invalid, regardless
    of depth or estimated cost.  Use EAGER when correctness is paramount and the
    graph is small enough that the full traversal is affordable.
    """

    LAZY = auto()
    """Record the cascade but defer actual invalidation until nodes are accessed.

    Under LAZY semantics the cascade object is returned with a non-None
    ``end_time`` set to the current wall-clock time, signalling to downstream
    consumers that the cascade has been *scheduled* but not yet applied.  Nodes
    are only truly invalidated when they are next read.  This reduces wasted work
    when many cascades overlap or when nodes are rarely accessed.
    """

    BOUNDED_DEPTH = auto()
    """Limit cascade propagation to the first five dependency waves.

    This policy prevents unbounded cascade growth in deep graphs by truncating
    the wave list at depth five.  Nodes beyond the depth limit are left in a
    potentially stale state; the caller is responsible for deciding whether a
    follow-up full invalidation is required.  Useful for incremental previews
    and editor-speed responsiveness.
    """

    CONSERVATIVE = auto()
    """Invalidate only the direct dependents of the trigger (first wave only).

    CONSERVATIVE is the safest and cheapest policy in terms of over-invalidation.
    It keeps only the first wave of the cascade, invalidating only nodes that
    directly depend on the trigger coordinate.  Transitive dependents are left
    untouched, which may introduce staleness but preserves the most existing
    cached state.
    """


# ---------------------------------------------------------------------------
# RepairAction
# ---------------------------------------------------------------------------


@dataclass
class RepairAction:
    """A single concrete action to be taken during post-cascade memory repair.

    A RepairAction encapsulates one atomic step in the repair process that
    follows an invalidation cascade.  Each action targets a specific memory
    coordinate and specifies the operation to perform, such as clearing a cache
    entry, re-running a computation, or emitting a diagnostic event.  The
    ``priority`` field controls scheduling order: higher values are processed
    first by the ``CascadeScheduler``.  The ``estimated_cost`` field is used by
    the scheduler to group expensive repairs and budget available resources.
    Each action is uniquely identified by a UUID so that duplicate suppression
    and completion tracking are straightforward.
    """

    target_coord: str
    """The memory coordinate this action operates on."""

    action_type: str
    """A short descriptor such as ``"clear_cache"``, ``"recompute"``, or ``"log"``."""

    priority: int = 5
    """Scheduling priority; higher values are processed first."""

    estimated_cost: float = 0.0
    """Estimated CPU/wall-clock cost in arbitrary units."""

    dependencies: list[str] = field(default_factory=list)
    """Coordinates that must be repaired before this action can execute."""

    action_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    """Unique identifier for deduplication and completion tracking."""

    def to_json(self) -> str:
        """Serialise this action to a JSON string.

        Returns:
            A compact JSON representation of all action fields, suitable for
            logging, network transport, or persistent storage.
        """
        return json.dumps(
            {
                "action_id": self.action_id,
                "target_coord": self.target_coord,
                "action_type": self.action_type,
                "priority": self.priority,
                "estimated_cost": self.estimated_cost,
                "dependencies": self.dependencies,
            }
        )

    def is_high_priority(self) -> bool:
        """Return True if this action has above-average priority.

        Returns:
            ``True`` when ``priority`` is strictly greater than 5, indicating
            the action should be scheduled ahead of baseline-priority actions.
        """
        return self.priority > 5


# ---------------------------------------------------------------------------
# RepairPlan
# ---------------------------------------------------------------------------


@dataclass
class RepairPlan:
    """An ordered collection of RepairActions that together restore memory consistency.

    A RepairPlan is produced by ``repair_after_cascade`` and consumed by the
    ``CascadeScheduler``.  It groups all actions needed to repair the state
    invalidated by a single cascade, associating them with the cascade by ID.
    The ``estimated_total_cost`` field gives the scheduler a quick estimate
    without iterating through the action list.  Plans are immutable once
    submitted to the scheduler; any modification should produce a new plan
    with a fresh ``plan_id``.  The ``created_at`` timestamp enables staleness
    checks when a plan sits in the queue longer than expected.
    """

    actions: list[RepairAction] = field(default_factory=list)
    """Ordered list of repair actions to execute."""

    cascade_id: str = ""
    """The ID of the cascade that triggered this repair plan."""

    estimated_total_cost: float = 0.0
    """Pre-computed cost estimate; may differ from ``total_cost()`` after edits."""

    created_at: float = field(default_factory=time.time)
    """Wall-clock creation time (seconds since epoch)."""

    plan_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    """Unique identifier for this plan."""

    def total_cost(self) -> float:
        """Compute the exact total cost by summing all action costs.

        Returns:
            Sum of ``estimated_cost`` for every action in this plan.
        """
        return sum(a.estimated_cost for a in self.actions)

    def by_priority(self) -> list[RepairAction]:
        """Return actions sorted by descending priority.

        Returns:
            A new list with the highest-priority actions first.
        """
        return sorted(self.actions, key=lambda a: a.priority, reverse=True)

    def to_json(self) -> str:
        """Serialise the entire plan to a JSON string.

        Returns:
            A JSON object containing plan metadata and a list of serialised
            actions.
        """
        return json.dumps(
            {
                "plan_id": self.plan_id,
                "cascade_id": self.cascade_id,
                "estimated_total_cost": self.estimated_total_cost,
                "created_at": self.created_at,
                "actions": [json.loads(a.to_json()) for a in self.actions],
            }
        )

    def summary(self) -> str:
        """Return a human-readable one-line summary of this plan.

        Returns:
            A string describing the plan ID, action count, and total cost.
        """
        return (
            f"RepairPlan(id={self.plan_id[:8]}, cascade={self.cascade_id[:8] if self.cascade_id else 'none'}, "
            f"actions={len(self.actions)}, total_cost={self.total_cost():.2f})"
        )


# ---------------------------------------------------------------------------
# InvalidationWave
# ---------------------------------------------------------------------------


@dataclass
class InvalidationWave:
    """A single wave (frontier) within a breadth-first invalidation cascade.

    Invalidation cascades are decomposed into discrete waves, where wave *k*
    contains all nodes at graph-distance *k* from the trigger coordinate.
    Each wave is processed atomically before the next begins, which ensures
    that when a node in wave *k+1* is invalidated all its transitive ancestors
    in wave *k* have already been invalidated.  The ``policy`` attached to a
    wave controls whether the wave itself should be truncated or deferred.
    The ``trigger`` field preserves the originating ``ChangeEvent`` for audit
    purposes.
    """

    nodes: list[str] = field(default_factory=list)
    """Coordinates invalidated in this wave."""

    policy: CascadePolicy = CascadePolicy.EAGER
    """The policy under which this wave was computed."""

    trigger: Any = None
    """The ChangeEvent that initiated the cascade, if available."""

    wave_index: int = 0
    """Zero-based position of this wave within the overall cascade."""

    timestamp: float = field(default_factory=time.time)
    """Wall-clock time when this wave was computed."""

    wave_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    """Unique identifier for this wave."""

    def size(self) -> int:
        """Return the number of nodes in this wave.

        Returns:
            Length of the ``nodes`` list.
        """
        return len(self.nodes)

    def to_json(self) -> str:
        """Serialise this wave to a compact JSON string.

        Returns:
            A JSON object with wave metadata and node list.
        """
        return json.dumps(
            {
                "wave_id": self.wave_id,
                "wave_index": self.wave_index,
                "nodes": self.nodes,
                "policy": self.policy.name,
                "timestamp": self.timestamp,
            }
        )

    def to_wave_info(self) -> Any:
        """Convert to an ``InvalidationWaveInfo`` value object.

        Returns:
            An ``InvalidationWaveInfo`` with ``wave_index``, ``nodes``, and
            ``timestamp`` populated from this wave's fields.
        """
        try:
            return InvalidationWaveInfo(
                wave_index=self.wave_index,
                nodes=list(self.nodes),
                timestamp=self.timestamp,
            )
        except Exception:
            return {
                "wave_index": self.wave_index,
                "nodes": list(self.nodes),
                "timestamp": self.timestamp,
            }


# ---------------------------------------------------------------------------
# DependencyTracer
# ---------------------------------------------------------------------------


class DependencyTracer:
    """Traces dependency paths through an ``InvalidationGraph`` for cascade planning.

    The ``DependencyTracer`` wraps a dependency graph (either an
    ``InvalidationGraph`` instance or any object exposing ``_forward``,
    ``_reverse``, and ``dependents_of``) and provides higher-level traversal
    operations needed by the cascade infrastructure.  It supports forward
    tracing (finding all downstream dependents), backward tracing (finding
    upstream providers), critical-node identification (nodes with the most
    dependents), and topological ordering for safe sequential repair.  All
    graph access is wrapped in try/except blocks so that the tracer degrades
    gracefully when the underlying graph implementation changes.  The tracer
    is stateless with respect to the graph — it does not cache traversal
    results, so it always reflects the current graph state.
    """

    def __init__(self, graph: Any) -> None:
        """Initialise the tracer with a dependency graph.

        Args:
            graph: An ``InvalidationGraph`` or compatible object that exposes
                ``_forward``, ``_reverse`` dicts and/or a ``dependents_of``
                method.
        """
        self._graph = graph

    def trace_forward(self, coord: str, depth: int = 10) -> list[list[str]]:
        """Compute BFS waves of forward (downstream) dependents.

        Performs a breadth-first traversal of the forward dependency graph
        starting from ``coord``.  Each returned list represents one wave of
        the traversal; wave 0 contains the immediate dependents of ``coord``,
        wave 1 contains their dependents, and so on.

        Args:
            coord: The starting coordinate.
            depth: Maximum number of waves to compute (default 10).

        Returns:
            A list of waves, where each wave is a list of coordinate strings.
            Returns an empty list if ``coord`` has no dependents.
        """
        waves = []
        frontier = {coord}
        visited = {coord}
        for d in range(depth):
            next_frontier: set[str] = set()
            for node in frontier:
                try:
                    children = self._graph._forward.get(node, set())
                    next_frontier.update(children - visited)
                except AttributeError:
                    try:
                        children = set(self._graph.dependents_of(node))
                        next_frontier.update(children - visited)
                    except Exception:
                        children = set()
            if not next_frontier:
                break
            waves.append(list(next_frontier))
            visited.update(next_frontier)
            frontier = next_frontier
        return waves

    def trace_backward(self, coord: str) -> list[str]:
        """Return the immediate upstream providers of a coordinate.

        Args:
            coord: The coordinate whose backward dependencies are requested.

        Returns:
            A list of coordinates that ``coord`` directly depends on.
            Returns an empty list if ``coord`` has no backward edges.
        """
        try:
            return list(self._graph._reverse.get(coord, set()))
        except AttributeError:
            try:
                return list(self._graph.providers_of(coord))
            except Exception:
                return []

    def find_critical_nodes(self, coords: list[str]) -> list[str]:
        """Identify the nodes with the most forward dependents in the given set.

        A critical node is one whose invalidation triggers the largest number
        of downstream cascades.  This method ranks each coordinate by the total
        number of nodes reachable from it via forward edges and returns the
        coordinates sorted from most to least connected.

        Args:
            coords: The candidate coordinate set to analyse.

        Returns:
            The input coordinates sorted by descending forward-dependent count.
        """
        def forward_count(c: str) -> int:
            waves = self.trace_forward(c)
            return sum(len(w) for w in waves)

        return sorted(coords, key=forward_count, reverse=True)

    def compute_dependency_depth(self, coord: str) -> int:
        """Compute the maximum forward depth reachable from a coordinate.

        Args:
            coord: The root coordinate.

        Returns:
            The number of BFS waves reachable from ``coord``, equivalent to
            the longest dependency chain starting at ``coord``.
        """
        return len(self.trace_forward(coord))

    def is_isolated(self, coord: str) -> bool:
        """Check whether a coordinate has no dependency edges in either direction.

        Args:
            coord: The coordinate to inspect.

        Returns:
            ``True`` if ``coord`` has neither forward nor backward edges,
            meaning invalidating it will not cascade further.
        """
        has_forward: bool
        has_backward: bool
        try:
            has_forward = bool(self._graph._forward.get(coord, set()))
        except AttributeError:
            has_forward = False
        try:
            has_backward = bool(self._graph._reverse.get(coord, set()))
        except AttributeError:
            has_backward = False
        return not has_forward and not has_backward

    def subgraph(self, coords: list[str]) -> dict[str, list[str]]:
        """Return the adjacency map of the dependency graph restricted to ``coords``.

        Only edges whose both endpoints are members of ``coords`` are included.

        Args:
            coords: The coordinate subset to restrict to.

        Returns:
            A dict mapping each coordinate in ``coords`` to the list of its
            forward neighbours that are also in ``coords``.
        """
        coord_set = set(coords)
        result: dict[str, list[str]] = {}
        for c in coords:
            try:
                neighbours = self._graph._forward.get(c, set())
            except AttributeError:
                try:
                    neighbours = set(self._graph.dependents_of(c))
                except Exception:
                    neighbours = set()
            result[c] = [n for n in neighbours if n in coord_set]
        return result

    def topological_order(self, coords: list[str]) -> list[str]:
        """Return a topological ordering of ``coords`` using Kahn's algorithm.

        Produces a linear ordering where each coordinate appears before all
        coordinates that depend on it.  This is the safe order for applying
        repairs: repairing a node before its dependents ensures that each
        repaired value is propagated correctly.

        Args:
            coords: Coordinates to order topologically.

        Returns:
            A topologically sorted list; if a cycle exists the remaining
            nodes are appended in arbitrary order after the acyclic prefix.
        """
        adj = self.subgraph(coords)
        in_degree: dict[str, int] = {c: 0 for c in coords}
        for c, neighbours in adj.items():
            for n in neighbours:
                in_degree[n] = in_degree.get(n, 0) + 1

        queue = [c for c in coords if in_degree[c] == 0]
        result: list[str] = []
        while queue:
            node = queue.pop(0)
            result.append(node)
            for neighbour in adj.get(node, []):
                in_degree[neighbour] -= 1
                if in_degree[neighbour] == 0:
                    queue.append(neighbour)

        # Append any remaining nodes (cycle members) in stable order.
        remaining = [c for c in coords if c not in result]
        result.extend(remaining)
        return result

    def summary(self) -> str:
        """Return a one-line description of this tracer and its graph.

        Returns:
            A string indicating the tracer type and the repr of the graph.
        """
        try:
            fwd_size = len(self._graph._forward)
            rev_size = len(self._graph._reverse)
            return f"DependencyTracer(fwd_nodes={fwd_size}, rev_nodes={rev_size})"
        except AttributeError:
            return f"DependencyTracer(graph={type(self._graph).__name__})"


# ---------------------------------------------------------------------------
# CascadeComputer
# ---------------------------------------------------------------------------


class CascadeComputer:
    """Computes ``MemoryInvalidationCascade`` objects from ``ChangeEvent`` inputs.

    The ``CascadeComputer`` orchestrates the full pipeline from a raw change
    event to a structured cascade value: it delegates graph traversal to the
    ``DependencyTracer``, converts BFS waves into ``InvalidationWaveInfo``
    records, applies the configured ``CascadePolicy`` to prune or defer waves,
    estimates computational cost, and decides whether the cascade should be
    aborted.  Multiple events can be merged into a single cascade by
    ``compute_from_events``, which unions all affected nodes wave by wave.
    The policy can be overridden at construction time and is applied uniformly
    to every cascade produced by this computer.
    """

    def __init__(
        self,
        tracer: DependencyTracer,
        policy: CascadePolicy = CascadePolicy.EAGER,
    ) -> None:
        """Initialise the computer with a tracer and a propagation policy.

        Args:
            tracer: A ``DependencyTracer`` that provides graph traversal.
            policy: The ``CascadePolicy`` applied to every computed cascade.
        """
        self._tracer = tracer
        self._policy = policy

    def compute_from_event(self, event: Any) -> Any:
        """Compute a ``MemoryInvalidationCascade`` from a single ``ChangeEvent``.

        Retrieves the target coordinate from the event, uses the tracer to
        enumerate all downstream waves, converts each wave to an
        ``InvalidationWaveInfo``, and assembles the final cascade.  The
        configured policy is applied before returning.

        Args:
            event: A ``ChangeEvent`` with a ``coordinate`` attribute identifying
                the invalidation root.

        Returns:
            A ``MemoryInvalidationCascade`` representing the full (policy-trimmed)
            propagation.
        """
        try:
            coord = event.coordinate
        except AttributeError:
            coord = str(event)

        raw_waves = self._tracer.trace_forward(coord)
        wave_infos: list[Any] = []
        for idx, nodes in enumerate(raw_waves):
            wave = InvalidationWave(
                nodes=nodes,
                policy=self._policy,
                trigger=event,
                wave_index=idx,
            )
            wave_infos.append(wave.to_wave_info())

        try:
            cascade = MemoryInvalidationCascade(
                root_coord=coord,
                waves=wave_infos,
                trigger_event=event,
                policy=self._policy.name,
            )
        except Exception:
            cascade = {
                "root_coord": coord,
                "waves": wave_infos,
                "policy": self._policy.name,
            }

        return self.apply_policy(cascade)

    def compute_from_events(self, events: list[Any]) -> Any:
        """Compute a merged cascade from a list of ``ChangeEvent`` objects.

        Each event is individually cascaded and the results are merged: waves
        at the same depth index have their node sets unioned together.

        Args:
            events: A list of ``ChangeEvent`` instances.

        Returns:
            A merged ``MemoryInvalidationCascade`` covering all events.
        """
        if not events:
            try:
                return MemoryInvalidationCascade(root_coord="", waves=[], trigger_event=None, policy=self._policy.name)
            except Exception:
                return {"root_coord": "", "waves": [], "policy": self._policy.name}

        cascades = [self.compute_from_event(e) for e in events]
        base = cascades[0]
        for other in cascades[1:]:
            try:
                base_waves = base.waves
                other_waves = other.waves
            except AttributeError:
                base_waves = base.get("waves", [])
                other_waves = other.get("waves", [])

            max_len = max(len(base_waves), len(other_waves))
            merged_waves = []
            for i in range(max_len):
                if i < len(base_waves) and i < len(other_waves):
                    try:
                        nodes = list(set(base_waves[i].nodes) | set(other_waves[i].nodes))
                        merged = InvalidationWave(
                            nodes=nodes,
                            policy=self._policy,
                            wave_index=i,
                        )
                        merged_waves.append(merged.to_wave_info())
                    except Exception:
                        merged_waves.append(base_waves[i])
                elif i < len(base_waves):
                    merged_waves.append(base_waves[i])
                else:
                    merged_waves.append(other_waves[i])

            try:
                base.waves = merged_waves
            except AttributeError:
                base["waves"] = merged_waves

        return base

    def apply_policy(self, cascade: Any) -> Any:
        """Trim or annotate a cascade according to the configured policy.

        - ``EAGER``: returns the cascade unchanged.
        - ``LAZY``: sets ``end_time`` on the cascade to the current timestamp.
        - ``BOUNDED_DEPTH``: truncates waves to the first 5.
        - ``CONSERVATIVE``: retains only the first wave.

        Args:
            cascade: A ``MemoryInvalidationCascade`` to post-process.

        Returns:
            The (possibly modified) cascade.
        """
        try:
            waves = cascade.waves
        except AttributeError:
            waves = cascade.get("waves", [])

        if self._policy == CascadePolicy.BOUNDED_DEPTH:
            trimmed = waves[:5]
            try:
                cascade.waves = trimmed
            except AttributeError:
                cascade["waves"] = trimmed

        elif self._policy == CascadePolicy.CONSERVATIVE:
            trimmed = waves[:1]
            try:
                cascade.waves = trimmed
            except AttributeError:
                cascade["waves"] = trimmed

        elif self._policy == CascadePolicy.LAZY:
            try:
                cascade.end_time = time.time()
            except AttributeError:
                try:
                    cascade["end_time"] = time.time()
                except Exception:
                    pass

        # EAGER: no modification needed.
        return cascade

    def estimate_cost(self, cascade: Any) -> float:
        """Estimate the computational cost of applying a cascade.

        Cost is approximated as the total number of affected nodes multiplied
        by a per-node base cost of 1.0, with an additional 10.0 per wave to
        account for synchronisation overhead.

        Args:
            cascade: A ``MemoryInvalidationCascade`` to estimate cost for.

        Returns:
            A non-negative float representing the estimated cost.
        """
        try:
            waves = cascade.waves
        except AttributeError:
            waves = cascade.get("waves", [])

        node_count = 0
        for w in waves:
            try:
                node_count += len(w.nodes)
            except AttributeError:
                node_count += len(w.get("nodes", []))

        wave_overhead = len(waves) * 10.0
        return float(node_count) + wave_overhead

    def should_abort(self, cascade: Any) -> bool:
        """Decide whether the cascade is too expensive to apply.

        Args:
            cascade: The cascade to evaluate.

        Returns:
            ``True`` if the estimated cost exceeds 1000.0, signalling that
            the caller should abort or defer the cascade.
        """
        return self.estimate_cost(cascade) > 1000.0

    def summary(self) -> str:
        """Return a one-line description of this computer's configuration.

        Returns:
            A string naming the policy and tracer summary.
        """
        return f"CascadeComputer(policy={self._policy.name}, tracer={self._tracer.summary()})"


# ---------------------------------------------------------------------------
# CascadeScheduler
# ---------------------------------------------------------------------------


class CascadeScheduler:
    """Schedules and executes ``RepairPlan`` objects in priority order.

    The ``CascadeScheduler`` maintains a queue of pending repair plans and a
    log of completed plans.  Plans are inserted in descending order of
    ``estimated_total_cost`` so that the most expensive repairs are tackled
    first, preventing starvation of cheap repairs by a flood of expensive
    ones.  ``execute_plan`` walks each action and applies the appropriate
    repair operation to the target memory object, logging any failures without
    aborting the remaining actions.  Completed plans are moved to the
    ``_completed`` list so that audit queries remain possible throughout the
    lifetime of the scheduler.
    """

    def __init__(self) -> None:
        """Initialise the scheduler with empty pending and completed queues."""
        self._queue: list[RepairPlan] = []
        self._completed: list[RepairPlan] = []

    def schedule(self, plan: RepairPlan) -> None:
        """Add a repair plan to the queue in sorted order.

        Plans are kept sorted by ``estimated_total_cost`` in descending order.
        The sort is performed after insertion so that the queue remains ordered
        across multiple ``schedule`` calls.

        Args:
            plan: The ``RepairPlan`` to enqueue.
        """
        self._queue.append(plan)
        self._queue.sort(key=lambda p: p.estimated_total_cost, reverse=True)
        logger.debug("Scheduled plan %s (cost=%.2f)", plan.plan_id[:8], plan.estimated_total_cost)

    def next_plan(self) -> RepairPlan | None:
        """Pop and return the highest-cost plan from the queue.

        Returns:
            The next ``RepairPlan`` to execute, or ``None`` if the queue is
            empty.
        """
        if not self._queue:
            return None
        return self._queue.pop(0)

    def execute_plan(self, plan: RepairPlan, memory: Any) -> bool:
        """Execute all actions in a plan against the provided memory object.

        For each action the method attempts to apply the appropriate repair
        operation.  Supported ``action_type`` values are ``"clear_cache"``
        (deletes the coordinate from any cache attribute on ``memory``) and
        anything else (falls back to logging the action).  Failures are caught
        and logged rather than re-raised so that one failed action does not
        prevent the remaining actions from running.

        Args:
            plan: The ``RepairPlan`` whose actions are to be executed.
            memory: The memory object to repair (any object with optional cache
                attributes).

        Returns:
            ``True`` after all actions have been attempted (regardless of
            individual failures).
        """
        for action in plan.by_priority():
            try:
                if action.action_type == "clear_cache":
                    for attr in ("_cache", "cache", "_section_cache"):
                        cache = getattr(memory, attr, None)
                        if isinstance(cache, dict):
                            cache.pop(action.target_coord, None)
                            logger.debug(
                                "Cleared cache entry %s from %s",
                                action.target_coord,
                                attr,
                            )
                            break
                else:
                    logger.info(
                        "RepairAction[%s] type=%s coord=%s (no-op)",
                        action.action_id[:8],
                        action.action_type,
                        action.target_coord,
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Failed to execute action %s: %s",
                    action.action_id[:8],
                    exc,
                )
        return True

    def complete_plan(self, plan_id: str) -> None:
        """Mark a plan as complete and move it from pending to completed.

        Args:
            plan_id: The ``plan_id`` of the plan to complete.  If not found
                in the queue the call is silently ignored.
        """
        remaining = []
        for plan in self._queue:
            if plan.plan_id == plan_id:
                self._completed.append(plan)
                logger.debug("Completed plan %s", plan_id[:8])
            else:
                remaining.append(plan)
        self._queue = remaining

    def pending_count(self) -> int:
        """Return the number of plans waiting to be executed.

        Returns:
            Length of the pending queue.
        """
        return len(self._queue)

    def completed_count(self) -> int:
        """Return the number of plans that have been completed.

        Returns:
            Length of the completed list.
        """
        return len(self._completed)

    def summary(self) -> str:
        """Return a one-line status summary of this scheduler.

        Returns:
            A string showing pending and completed plan counts.
        """
        return (
            f"CascadeScheduler(pending={self.pending_count()}, "
            f"completed={self.completed_count()})"
        )


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def compute_cascade(
    events: list[Any],
    memory: Any,
    policy: CascadePolicy = CascadePolicy.EAGER,
) -> Any:
    """Compute a merged invalidation cascade for a list of change events.

    This convenience function constructs a ``DependencyTracer`` from the
    dependency graph attached to ``memory`` (accessed via ``memory._region``
    or ``memory.graph``), instantiates a ``CascadeComputer`` with the given
    policy, and delegates to ``CascadeComputer.compute_from_events``.

    Args:
        events: A list of ``ChangeEvent`` objects describing what changed.
        memory: A memory object that exposes a dependency graph via
            ``._region``, ``.graph``, or ``.dependency_graph``.
        policy: The ``CascadePolicy`` to apply to the resulting cascade.

    Returns:
        A ``MemoryInvalidationCascade`` (or equivalent dict) describing all
        affected coordinates.
    """
    graph: Any = None
    for attr in ("graph", "dependency_graph", "_graph"):
        graph = getattr(memory, attr, None)
        if graph is not None:
            break
    if graph is None:
        try:
            graph = memory._region
        except AttributeError:
            pass

    # Fall back to a minimal stub graph so the rest of the pipeline works.
    if graph is None:
        class _StubGraph:
            _forward: dict = {}
            _reverse: dict = {}
        graph = _StubGraph()

    tracer = DependencyTracer(graph)
    computer = CascadeComputer(tracer, policy)
    return computer.compute_from_events(events)


def repair_after_cascade(cascade: Any, memory: Any) -> RepairPlan:
    """Build a ``RepairPlan`` for all coordinates affected by ``cascade``.

    For each affected coordinate a ``RepairAction`` of type ``"clear_cache"``
    is created with a base priority of 5 and estimated cost of 1.0.  The
    plan's ``estimated_total_cost`` is set to the sum of all action costs.

    Args:
        cascade: A ``MemoryInvalidationCascade`` produced by ``compute_cascade``
            or equivalent.
        memory: Unused here but accepted for API symmetry with callers that
            may pass it.

    Returns:
        A ``RepairPlan`` ready to be submitted to a ``CascadeScheduler``.
    """
    try:
        waves = cascade.waves
    except AttributeError:
        waves = cascade.get("waves", [])

    affected_coords: list[str] = []
    for wave in waves:
        try:
            affected_coords.extend(wave.nodes)
        except AttributeError:
            affected_coords.extend(wave.get("nodes", []))

    # Deduplicate while preserving order.
    seen: set[str] = set()
    unique_coords: list[str] = []
    for c in affected_coords:
        if c not in seen:
            seen.add(c)
            unique_coords.append(c)

    actions = [
        RepairAction(
            target_coord=coord,
            action_type="clear_cache",
            priority=5,
            estimated_cost=1.0,
        )
        for coord in unique_coords
    ]

    try:
        cascade_id = cascade.cascade_id
    except AttributeError:
        cascade_id = cascade.get("cascade_id", str(uuid.uuid4()))

    total = sum(a.estimated_cost for a in actions)
    plan = RepairPlan(
        actions=actions,
        cascade_id=cascade_id,
        estimated_total_cost=total,
    )
    logger.debug(
        "Built repair plan %s with %d actions for cascade %s",
        plan.plan_id[:8],
        len(actions),
        str(cascade_id)[:8],
    )
    return plan


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "CascadePolicy",
    "RepairAction",
    "RepairPlan",
    "InvalidationWave",
    "DependencyTracer",
    "CascadeComputer",
    "CascadeScheduler",
    "compute_cascade",
    "repair_after_cascade",
]
