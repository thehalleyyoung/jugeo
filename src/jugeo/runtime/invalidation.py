"""Invalidation engine for the JuGeo runtime — component **σ** of the manifest.

From *theory2.tex* §Incrementality, invalidation, and persistent semantic memory:

    A useful manifest object is
        M = (J, O, E, X, K, η, σ),
    where σ is the support-indexed invalidation graph.  Invalidation tracks
    which judgments depend on which evidence so that when evidence is revoked
    or a section changes, all downstream judgments that depended on it are
    invalidated.  The theorem burden is concrete: *serialization determinism*,
    *dependency-trace integrity*, and *stale-manifest conservativity*.

This module provides the full runtime invalidation subsystem:

* :class:`InvalidationGraph` — directed acyclic dependency graph (the σ DAG).
* :class:`InvalidationEvent` — immutable record of a single triggering event.
* :class:`InvalidationEngine` — main entry-point for running invalidation cascades.
* :class:`InvalidationPolicy` — configurable cascade strategy and limits.
* :class:`InvalidationCascade` — structured representation of a cascade wave.
* :class:`InvalidationTracker` — mutable validity bitmap over coordinates.
* :class:`RepairScheduler` — dependency-aware repair ordering after invalidation.
* :class:`InvalidationNotifier` — publish/subscribe notification delivery.
* :class:`InvalidationHistory` — append-only audit log of all invalidation events.
* :class:`InvalidationDiagnostics` — analytics, hotspot detection, copilot summaries.
* :class:`InvalidationSerializer` — deterministic JSON (de)serialization.

The backward-compatible helpers :func:`plan_invalidation`, :class:`InvalidationPlan`,
and :class:`InvalidationReason` are retained at module level.

copilot: shared-core marker for LLM-assisted repair orchestration.
"""

from __future__ import annotations

import json
import time
import uuid
from collections import defaultdict, deque
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Callable, Mapping, Sequence

from jugeo.geometry.covers import Cover
from jugeo.geometry.supports import StarNeighborhood, SupportRegion, star_of_support
from jugeo.runtime.cache import SemanticCache


# ---------------------------------------------------------------------------
# Backward-compatible enums kept from original module
# ---------------------------------------------------------------------------

class InvalidationReason(str, Enum):
    """Reason for an invalidation event (runtime layer)."""

    SUPPORT_CHANGE = 'support-change'
    TRUST_CHANGE = 'trust-change'
    REPLAY_CONFLICT = 'replay-conflict'


# ---------------------------------------------------------------------------
# 0. InvalidationGraph — the σ DAG
# ---------------------------------------------------------------------------

class InvalidationGraph:
    """Directed acyclic dependency graph — the **σ** component of M.

    An edge ``(a, b)`` means *"if a changes then b must be re-evaluated"*.
    Both forward (source → dependents) and reverse (target → dependencies)
    adjacency lists are maintained so that look-ups in either direction are
    O(1) amortised.

    The graph is expected to remain acyclic; :meth:`is_acyclic` can be called
    as an audit invariant.
    """

    def __init__(self) -> None:
        self._forward: dict[str, set[str]] = defaultdict(set)
        self._reverse: dict[str, set[str]] = defaultdict(set)
        self._nodes: set[str] = set()

    # -- mutation ----------------------------------------------------------

    def add_node(self, node: str) -> None:
        """Register *node* in the graph even if it has no edges yet."""
        self._nodes.add(node)

    def add_dependency(self, source: str, target: str) -> None:
        """Declare that *target* depends on *source*.

        After this call an invalidation of *source* will propagate to
        *target*.  Both nodes are implicitly registered.

        Raises :class:`ValueError` if the edge would create a cycle.
        """
        if source == target:
            raise ValueError(f"Self-loop not allowed: {source}")
        # Cheap pre-check: would adding this edge form a cycle?
        if source in self._transitive_closure_forward(target):
            raise ValueError(
                f"Adding edge ({source} → {target}) would create a cycle"
            )
        self._nodes.add(source)
        self._nodes.add(target)
        self._forward[source].add(target)
        self._reverse[target].add(source)

    def remove_dependency(self, source: str, target: str) -> bool:
        """Remove the edge from *source* to *target*.

        Returns ``True`` if the edge existed and was removed, ``False``
        otherwise.
        """
        if target in self._forward.get(source, set()):
            self._forward[source].discard(target)
            self._reverse[target].discard(source)
            return True
        return False

    def remove_node(self, node: str) -> bool:
        """Remove *node* and all incident edges.

        Returns ``True`` if the node existed.
        """
        if node not in self._nodes:
            return False
        for tgt in list(self._forward.get(node, [])):
            self._reverse[tgt].discard(node)
        for src in list(self._reverse.get(node, [])):
            self._forward[src].discard(node)
        self._forward.pop(node, None)
        self._reverse.pop(node, None)
        self._nodes.discard(node)
        return True

    # -- queries -----------------------------------------------------------

    def dependents_of(self, node: str) -> frozenset[str]:
        """Return direct dependents (one hop forward) of *node*."""
        return frozenset(self._forward.get(node, set()))

    def dependencies_of(self, node: str) -> frozenset[str]:
        """Return direct dependencies (one hop reverse) of *node*."""
        return frozenset(self._reverse.get(node, set()))

    def transitive_dependents(self, node: str) -> frozenset[str]:
        """BFS forward closure — all nodes transitively depending on *node*."""
        return self._transitive_closure_forward(node)

    def transitive_dependencies(self, node: str) -> frozenset[str]:
        """BFS reverse closure — all nodes *node* transitively depends on."""
        return self._transitive_closure_reverse(node)

    def is_acyclic(self) -> bool:
        """Return ``True`` when the graph contains no directed cycles.

        Uses Kahn's algorithm (iterative in-degree reduction).
        """
        in_degree: dict[str, int] = {n: 0 for n in self._nodes}
        for src, targets in self._forward.items():
            for tgt in targets:
                in_degree[tgt] = in_degree.get(tgt, 0) + 1
        queue = deque(n for n, d in in_degree.items() if d == 0)
        visited = 0
        while queue:
            n = queue.popleft()
            visited += 1
            for tgt in self._forward.get(n, []):
                in_degree[tgt] -= 1
                if in_degree[tgt] == 0:
                    queue.append(tgt)
        return visited == len(self._nodes)

    def topological_sort(self) -> list[str]:
        """Return nodes in a valid topological order.

        Raises :class:`ValueError` if the graph contains a cycle.
        """
        in_degree: dict[str, int] = {n: 0 for n in self._nodes}
        for src, targets in self._forward.items():
            for tgt in targets:
                in_degree[tgt] = in_degree.get(tgt, 0) + 1
        queue = deque(sorted(n for n, d in in_degree.items() if d == 0))
        result: list[str] = []
        while queue:
            n = queue.popleft()
            result.append(n)
            for tgt in sorted(self._forward.get(n, [])):
                in_degree[tgt] -= 1
                if in_degree[tgt] == 0:
                    queue.append(tgt)
        if len(result) != len(self._nodes):
            raise ValueError("Graph contains a cycle; topological sort impossible")
        return result

    def roots(self) -> frozenset[str]:
        """Return nodes with no incoming edges (in-degree 0)."""
        return frozenset(
            n for n in self._nodes if not self._reverse.get(n)
        )

    def leaves(self) -> frozenset[str]:
        """Return nodes with no outgoing edges (out-degree 0)."""
        return frozenset(
            n for n in self._nodes if not self._forward.get(n)
        )

    @property
    def node_count(self) -> int:
        """Total number of registered nodes."""
        return len(self._nodes)

    @property
    def edge_count(self) -> int:
        """Total number of directed edges."""
        return sum(len(ts) for ts in self._forward.values())

    def nodes(self) -> frozenset[str]:
        """Return all registered nodes."""
        return frozenset(self._nodes)

    # -- internal helpers --------------------------------------------------

    def _transitive_closure_forward(self, start: str) -> frozenset[str]:
        visited: set[str] = set()
        queue = deque(self._forward.get(start, []))
        while queue:
            n = queue.popleft()
            if n in visited:
                continue
            visited.add(n)
            queue.extend(self._forward.get(n, []))
        return frozenset(visited)

    def _transitive_closure_reverse(self, start: str) -> frozenset[str]:
        visited: set[str] = set()
        queue = deque(self._reverse.get(start, []))
        while queue:
            n = queue.popleft()
            if n in visited:
                continue
            visited.add(n)
            queue.extend(self._reverse.get(n, []))
        return frozenset(visited)


# ---------------------------------------------------------------------------
# 1. InvalidationEvent
# ---------------------------------------------------------------------------

class TriggerKind(str, Enum):
    """Classification of the root cause of an invalidation event."""

    EVIDENCE_REVOKED = 'evidence-revoked'
    SECTION_CHANGED = 'section-changed'
    TRUST_DEMOTED = 'trust-demoted'
    TREATY_BROKEN = 'treaty-broken'
    PACK_UNLOADED = 'pack-unloaded'


@dataclass(frozen=True, slots=True)
class InvalidationEvent:
    """Immutable record of a single invalidation trigger.

    Each event is assigned a unique *event_id* at creation time and captures
    the coordinate that was the proximate cause, the kind of trigger, a
    monotonic timestamp, and the total number of coordinates affected by the
    resulting cascade.
    """

    event_id: str
    trigger_coordinate: str
    trigger_kind: TriggerKind
    timestamp: float
    affected_count: int = 0
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @staticmethod
    def create(
        trigger_coordinate: str,
        trigger_kind: TriggerKind,
        *,
        affected_count: int = 0,
        metadata: Mapping[str, Any] | None = None,
    ) -> InvalidationEvent:
        """Factory with automatic id / timestamp."""
        return InvalidationEvent(
            event_id=uuid.uuid4().hex[:16],
            trigger_coordinate=trigger_coordinate,
            trigger_kind=trigger_kind,
            timestamp=time.monotonic(),
            affected_count=affected_count,
            metadata=metadata or {},
        )

    def with_affected_count(self, count: int) -> InvalidationEvent:
        """Return a copy of this event with an updated *affected_count*."""
        return InvalidationEvent(
            event_id=self.event_id,
            trigger_coordinate=self.trigger_coordinate,
            trigger_kind=self.trigger_kind,
            timestamp=self.timestamp,
            affected_count=count,
            metadata=self.metadata,
        )


# ---------------------------------------------------------------------------
# 2. InvalidationPolicy
# ---------------------------------------------------------------------------

class CascadeStrategy(str, Enum):
    """How cascades are executed."""

    EAGER = 'eager'
    LAZY = 'lazy'
    BATCHED = 'batched'


class NotificationPolicy(str, Enum):
    """When subscribers are notified."""

    IMMEDIATE = 'immediate'
    AFTER_CASCADE = 'after-cascade'
    MANUAL = 'manual'


@dataclass(slots=True)
class InvalidationPolicy:
    """Configurable knobs that govern cascade behaviour.

    The *copilot_assist_repair* flag, when ``True``, signals the repair
    scheduler to generate LLM-friendly summaries that a copilot agent can
    use to propose automated fixes.
    """

    cascade_depth_limit: int = 64
    cascade_strategy: CascadeStrategy = CascadeStrategy.EAGER
    notification_policy: NotificationPolicy = NotificationPolicy.AFTER_CASCADE
    copilot_assist_repair: bool = True
    batch_window_seconds: float = 0.5
    max_batch_size: int = 256

    def allows_cascade(self) -> bool:
        """Return ``True`` unless the strategy is :attr:`CascadeStrategy.LAZY`."""
        return self.cascade_strategy != CascadeStrategy.LAZY

    def effective_depth_limit(self) -> int:
        """Depth limit clamped to the range [1, 1024]."""
        return max(1, min(self.cascade_depth_limit, 1024))

    def should_notify_immediately(self) -> bool:
        """Check if subscribers should be notified on every wave."""
        return self.notification_policy == NotificationPolicy.IMMEDIATE

    def should_batch(self) -> bool:
        """Return ``True`` when cascade strategy is BATCHED."""
        return self.cascade_strategy == CascadeStrategy.BATCHED

    def copilot_enabled(self) -> bool:
        """Whether copilot-assisted repair is active."""
        return self.copilot_assist_repair


# ---------------------------------------------------------------------------
# 3. InvalidationCascade
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class InvalidationCascade:
    """Represents a complete cascade originating from one event.

    A cascade proceeds in *waves*: wave 0 contains the direct dependents of
    the trigger coordinate, wave 1 the dependents of wave 0 not already
    visited, and so on until no new nodes are reached or the depth limit is
    hit.
    """

    root_event: InvalidationEvent
    waves: list[list[str]] = field(default_factory=list)
    total_affected: int = 0
    cascade_depth: int = 0
    _complete: bool = False

    def is_complete(self) -> bool:
        """Return ``True`` when all reachable nodes have been visited."""
        return self._complete

    def mark_complete(self) -> None:
        """Seal the cascade after the engine finishes propagation."""
        self._complete = True
        self.total_affected = sum(len(w) for w in self.waves)
        self.cascade_depth = len(self.waves)

    def all_affected(self) -> list[str]:
        """Flat list of every coordinate touched, in wave order."""
        return [node for wave in self.waves for node in wave]

    def wave_at(self, depth: int) -> list[str]:
        """Return the list of nodes invalidated at a given *depth*.

        Returns an empty list if *depth* is out of range.
        """
        if 0 <= depth < len(self.waves):
            return list(self.waves[depth])
        return []

    def summary(self) -> str:
        """One-line human-readable summary."""
        status = "complete" if self._complete else "in-progress"
        return (
            f"Cascade({self.root_event.event_id}): "
            f"{self.total_affected} affected across "
            f"{self.cascade_depth} wave(s) [{status}]"
        )


# ---------------------------------------------------------------------------
# 4. InvalidationTracker
# ---------------------------------------------------------------------------

class InvalidationTracker:
    """Mutable validity bitmap over coordinate keys.

    The tracker is the authoritative source of truth for whether a given
    coordinate is currently valid or has been invalidated and awaits repair.
    """

    def __init__(self) -> None:
        self._invalid: set[str] = set()
        self._invalidation_times: dict[str, float] = {}

    def mark_invalid(self, key: str) -> None:
        """Mark *key* as invalid, recording the current timestamp."""
        self._invalid.add(key)
        self._invalidation_times[key] = time.monotonic()

    def mark_valid(self, key: str) -> None:
        """Restore *key* to valid status."""
        self._invalid.discard(key)
        self._invalidation_times.pop(key, None)

    def is_valid(self, key: str) -> bool:
        """Return ``True`` when *key* has **not** been invalidated."""
        return key not in self._invalid

    def invalid_set(self) -> frozenset[str]:
        """Snapshot of all currently invalid keys."""
        return frozenset(self._invalid)

    def valid_set(self, universe: frozenset[str]) -> frozenset[str]:
        """Return the complement: keys in *universe* that are still valid."""
        return universe - self._invalid

    def staleness_report(self) -> list[tuple[str, float]]:
        """Return ``(key, seconds_stale)`` pairs sorted by staleness desc.

        Useful for prioritising repairs: the longest-stale coordinates should
        be repaired first if no dependency ordering overrides.
        """
        now = time.monotonic()
        report = [
            (k, now - t) for k, t in self._invalidation_times.items()
        ]
        report.sort(key=lambda pair: pair[1], reverse=True)
        return report

    def invalidation_count(self) -> int:
        """Number of currently invalid keys."""
        return len(self._invalid)

    def reset(self) -> None:
        """Clear all invalidation marks (e.g. after a full re-check)."""
        self._invalid.clear()
        self._invalidation_times.clear()


# ---------------------------------------------------------------------------
# 5. InvalidationEngine — main entry-point
# ---------------------------------------------------------------------------

class InvalidationEngine:
    """Orchestrates invalidation cascades over an :class:`InvalidationGraph`.

    The engine reads the dependency structure from σ, respects the policy
    limits, records events in the history, and drives notifications.
    """

    def __init__(
        self,
        graph: InvalidationGraph,
        tracker: InvalidationTracker | None = None,
        policy: InvalidationPolicy | None = None,
        history: InvalidationHistory | None = None,
        notifier: InvalidationNotifier | None = None,
    ) -> None:
        self._graph = graph
        self._tracker = tracker or InvalidationTracker()
        self._policy = policy or InvalidationPolicy()
        self._history: InvalidationHistory = history or InvalidationHistory()
        self._notifier: InvalidationNotifier = notifier or InvalidationNotifier()
        self._undo_stack: list[tuple[InvalidationEvent, frozenset[str]]] = []

    # -- primary API -------------------------------------------------------

    def invalidate(
        self,
        coordinate: str,
        kind: TriggerKind,
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> InvalidationCascade:
        """Invalidate *coordinate* and cascade through σ.

        Returns the completed :class:`InvalidationCascade`.
        """
        event = InvalidationEvent.create(
            coordinate, kind, metadata=metadata,
        )
        cascade = self.cascade(event)
        # Record the set of newly-invalidated keys so undo is possible.
        all_affected = frozenset(cascade.all_affected()) | {coordinate}
        self._undo_stack.append((event, all_affected))
        return cascade

    def cascade(self, event: InvalidationEvent) -> InvalidationCascade:
        """Run a full cascade from *event* respecting the active policy.

        The cascade proceeds in breadth-first waves up to the configured
        depth limit.  Each touched coordinate is marked invalid in the
        tracker and recorded in the history.
        """
        cas = InvalidationCascade(root_event=event)
        visited: set[str] = set()
        depth_limit = self._policy.effective_depth_limit()

        # Mark the trigger coordinate itself invalid.
        self._tracker.mark_invalid(event.trigger_coordinate)
        visited.add(event.trigger_coordinate)

        frontier = list(self._graph.dependents_of(event.trigger_coordinate))
        depth = 0

        while frontier and depth < depth_limit:
            wave: list[str] = []
            next_frontier: list[str] = []
            for node in frontier:
                if node in visited:
                    continue
                visited.add(node)
                wave.append(node)
                self._tracker.mark_invalid(node)
                next_frontier.extend(self._graph.dependents_of(node))
            if wave:
                cas.waves.append(wave)
                if self._policy.should_notify_immediately():
                    self._notifier.notify(event, wave)
            frontier = next_frontier
            depth += 1

        cas.mark_complete()
        updated_event = event.with_affected_count(cas.total_affected)
        self._history.record(updated_event, cas)

        if not self._policy.should_notify_immediately():
            self._notifier.notify(updated_event, cas.all_affected())

        return cas

    def compute_affected(self, coordinate: str) -> frozenset[str]:
        """Return all coordinates that *would* be invalidated (dry-run)."""
        return self._graph.transitive_dependents(coordinate) | {coordinate}

    def compute_repair_order(self, invalid_keys: frozenset[str] | None = None) -> list[str]:
        """Topological repair order over the currently invalid coordinates.

        Only nodes present in *invalid_keys* (defaulting to the tracker's
        invalid set) are included; the ordering respects the dependency edges
        in σ so that a node is repaired only after all its dependencies have
        been repaired.
        """
        keys = invalid_keys or self._tracker.invalid_set()
        if not keys:
            return []
        try:
            full_order = self._graph.topological_sort()
        except ValueError:
            # Fallback: return sorted keys if cycle detected.
            return sorted(keys)
        return [n for n in full_order if n in keys]

    def incremental_invalidate(
        self,
        coordinate: str,
        kind: TriggerKind,
    ) -> InvalidationCascade:
        """Like :meth:`invalidate` but skips nodes already invalid.

        This is more efficient when several overlapping cascades happen in
        quick succession — already-invalid nodes are not re-enqueued.
        """
        event = InvalidationEvent.create(coordinate, kind)
        cas = InvalidationCascade(root_event=event)
        visited: set[str] = set()
        depth_limit = self._policy.effective_depth_limit()

        if not self._tracker.is_valid(coordinate):
            cas.mark_complete()
            return cas
        self._tracker.mark_invalid(coordinate)
        visited.add(coordinate)

        frontier = [
            n for n in self._graph.dependents_of(coordinate)
            if self._tracker.is_valid(n)
        ]
        depth = 0

        while frontier and depth < depth_limit:
            wave: list[str] = []
            next_frontier: list[str] = []
            for node in frontier:
                if node in visited or not self._tracker.is_valid(node):
                    continue
                visited.add(node)
                wave.append(node)
                self._tracker.mark_invalid(node)
                next_frontier.extend(
                    n for n in self._graph.dependents_of(node)
                    if self._tracker.is_valid(n)
                )
            if wave:
                cas.waves.append(wave)
            frontier = next_frontier
            depth += 1

        cas.mark_complete()
        updated_event = event.with_affected_count(cas.total_affected)
        self._history.record(updated_event, cas)
        self._undo_stack.append(
            (updated_event, frozenset(cas.all_affected()) | {coordinate})
        )
        return cas

    def batch_invalidate(
        self,
        coordinates: Sequence[str],
        kind: TriggerKind,
    ) -> list[InvalidationCascade]:
        """Invalidate several root coordinates, de-duplicating visited nodes.

        Returns one cascade per coordinate but each cascade skips nodes
        already invalidated by a prior cascade in the same batch.
        """
        results: list[InvalidationCascade] = []
        for coord in coordinates:
            cas = self.incremental_invalidate(coord, kind)
            results.append(cas)
        return results

    def undo_invalidation(self) -> frozenset[str] | None:
        """Pop the most recent invalidation and restore affected keys.

        Returns the set of keys that were restored, or ``None`` if the undo
        stack is empty.  Note: undo is a *best-effort* operation — if
        another cascade has since invalidated overlapping keys those keys
        remain invalid.
        """
        if not self._undo_stack:
            return None
        _event, keys = self._undo_stack.pop()
        for k in keys:
            self._tracker.mark_valid(k)
        return keys

    # -- accessors ---------------------------------------------------------

    @property
    def graph(self) -> InvalidationGraph:
        """The underlying σ graph."""
        return self._graph

    @property
    def tracker(self) -> InvalidationTracker:
        return self._tracker

    @property
    def policy(self) -> InvalidationPolicy:
        return self._policy

    @property
    def history(self) -> InvalidationHistory:
        return self._history


# ---------------------------------------------------------------------------
# 6. RepairScheduler
# ---------------------------------------------------------------------------

class RepairScheduler:
    """Schedules repair work after invalidation cascades.

    The scheduler consults the invalidation graph to determine the correct
    dependency-driven order and can estimate repair cost using a configurable
    cost model.
    """

    def __init__(
        self,
        engine: InvalidationEngine,
        *,
        cost_model: Callable[[str], float] | None = None,
    ) -> None:
        self._engine = engine
        self._cost_model = cost_model or (lambda _key: 1.0)
        self._scheduled: list[str] = []

    def schedule(self, keys: frozenset[str] | None = None) -> list[str]:
        """Compute a repair schedule for the given (or all invalid) keys.

        The returned list is in dependency order — each item appears only
        after all of its dependencies.
        """
        self._scheduled = self._engine.compute_repair_order(keys)
        return list(self._scheduled)

    def priority_order(self, keys: frozenset[str] | None = None) -> list[str]:
        """Order by estimated cost descending (most expensive first).

        Useful when resources are limited and the highest-value repairs
        should be attempted first.
        """
        target = list(keys or self._engine.tracker.invalid_set())
        target.sort(key=lambda k: self._cost_model(k), reverse=True)
        return target

    def dependency_order(self, keys: frozenset[str] | None = None) -> list[str]:
        """Alias for :meth:`schedule` — explicit dependency ordering."""
        return self.schedule(keys)

    def estimate_cost(self, keys: frozenset[str] | None = None) -> float:
        """Sum of per-key costs for the given (or all invalid) keys."""
        target = keys or self._engine.tracker.invalid_set()
        return sum(self._cost_model(k) for k in target)

    def copilot_suggest_repair_order(self, keys: frozenset[str] | None = None) -> str:
        """Return a copilot-friendly Markdown summary of the suggested order.

        When :attr:`InvalidationPolicy.copilot_assist_repair` is ``True``
        this summary is designed for an LLM agent to parse and act upon.
        """
        order = self.schedule(keys)
        if not order:
            return "✅ No repairs needed — all coordinates are valid."
        lines = ["## copilot: Suggested Repair Order", ""]
        for idx, key in enumerate(order, 1):
            cost = self._cost_model(key)
            deps = self._engine.graph.dependencies_of(key)
            dep_note = f" (after {', '.join(sorted(deps))})" if deps else ""
            lines.append(f"{idx}. `{key}` — est. cost {cost:.1f}{dep_note}")
        lines.append("")
        lines.append(f"**Total estimated cost:** {self.estimate_cost(frozenset(order)):.1f}")
        return "\n".join(lines)

    def pending(self) -> list[str]:
        """Return the last computed schedule (may be stale)."""
        return list(self._scheduled)


# ---------------------------------------------------------------------------
# 7. InvalidationNotifier
# ---------------------------------------------------------------------------

class InvalidationNotifier:
    """Publish / subscribe notification delivery for invalidation events.

    Subscribers register a callable that receives an :class:`InvalidationEvent`
    and the list of affected coordinate keys.
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, Callable[[InvalidationEvent, list[str]], None]] = {}
        self._history: list[tuple[InvalidationEvent, list[str]]] = []
        self._pending: list[tuple[InvalidationEvent, list[str]]] = []

    def subscribe(
        self,
        subscriber_id: str,
        callback: Callable[[InvalidationEvent, list[str]], None],
    ) -> None:
        """Register *callback* under *subscriber_id*.

        If a subscriber with the same id already exists it is replaced.
        """
        self._subscribers[subscriber_id] = callback

    def unsubscribe(self, subscriber_id: str) -> bool:
        """Remove the subscriber.  Returns ``True`` if it existed."""
        return self._subscribers.pop(subscriber_id, None) is not None

    def notify(self, event: InvalidationEvent, affected: Sequence[str]) -> int:
        """Deliver *event* and *affected* list to all subscribers.

        Returns the number of subscribers successfully notified.
        """
        affected_list = list(affected)
        self._history.append((event, affected_list))
        delivered = 0
        for _sid, cb in self._subscribers.items():
            try:
                cb(event, affected_list)
                delivered += 1
            except Exception:
                # Subscriber errors must not break the cascade.
                self._pending.append((event, affected_list))
        return delivered

    def notification_history(self) -> list[tuple[InvalidationEvent, list[str]]]:
        """Full ordered history of notifications delivered."""
        return list(self._history)

    def pending_notifications(self) -> list[tuple[InvalidationEvent, list[str]]]:
        """Notifications that failed to deliver and are awaiting retry."""
        return list(self._pending)

    def clear_pending(self) -> int:
        """Drop all pending notifications, returning the count dropped."""
        count = len(self._pending)
        self._pending.clear()
        return count

    def subscriber_ids(self) -> frozenset[str]:
        """Currently registered subscriber ids."""
        return frozenset(self._subscribers)


# ---------------------------------------------------------------------------
# 8. InvalidationHistory
# ---------------------------------------------------------------------------

class InvalidationHistory:
    """Append-only audit log of every invalidation event and its cascade."""

    def __init__(self) -> None:
        self._events: list[InvalidationEvent] = []
        self._cascades: list[InvalidationCascade] = []
        self._by_coordinate: dict[str, list[int]] = defaultdict(list)

    def record(self, event: InvalidationEvent, cascade: InvalidationCascade) -> None:
        """Append an event and its associated cascade."""
        idx = len(self._events)
        self._events.append(event)
        self._cascades.append(cascade)
        self._by_coordinate[event.trigger_coordinate].append(idx)

    def events_for_coordinate(self, coordinate: str) -> list[InvalidationEvent]:
        """Return all events triggered by *coordinate*."""
        return [self._events[i] for i in self._by_coordinate.get(coordinate, [])]

    def cascade_history(self) -> list[InvalidationCascade]:
        """Full cascade history in chronological order."""
        return list(self._cascades)

    def all_events(self) -> list[InvalidationEvent]:
        """All recorded events in insertion order."""
        return list(self._events)

    def frequency_analysis(self) -> list[tuple[str, int]]:
        """Return ``(coordinate, event_count)`` pairs sorted descending.

        Coordinates that trigger many invalidations are good refactoring
        candidates.
        """
        freq = [(coord, len(idxs)) for coord, idxs in self._by_coordinate.items()]
        freq.sort(key=lambda p: p[1], reverse=True)
        return freq

    def hotspot_detection(self, *, threshold: int = 3) -> list[str]:
        """Return coordinates that have triggered ≥ *threshold* events.

        Hotspots indicate unstable evidence or volatile sections that may
        benefit from architectural attention or copilot-assisted refactoring.
        """
        return [
            coord for coord, idxs in self._by_coordinate.items()
            if len(idxs) >= threshold
        ]

    def event_count(self) -> int:
        """Total number of recorded events."""
        return len(self._events)

    def latest_event(self) -> InvalidationEvent | None:
        """Most recently recorded event, or ``None``."""
        return self._events[-1] if self._events else None


# ---------------------------------------------------------------------------
# 9. InvalidationDiagnostics
# ---------------------------------------------------------------------------

class InvalidationDiagnostics:
    """Analytics and diagnostic reports over the invalidation subsystem.

    Methods prefixed with ``copilot_`` produce Markdown-formatted output
    suitable for consumption by an LLM copilot agent during assisted repair
    sessions.
    """

    def __init__(self, engine: InvalidationEngine) -> None:
        self._engine = engine

    def invalidation_summary(self) -> dict[str, Any]:
        """Dictionary summarising the current invalidation state."""
        tracker = self._engine.tracker
        graph = self._engine.graph
        history = self._engine.history
        return {
            "total_nodes": graph.node_count,
            "total_edges": graph.edge_count,
            "invalid_count": tracker.invalidation_count(),
            "event_count": history.event_count(),
            "is_acyclic": graph.is_acyclic(),
            "root_count": len(graph.roots()),
            "leaf_count": len(graph.leaves()),
        }

    def cascade_analysis(self) -> list[dict[str, Any]]:
        """Per-cascade statistics for every recorded cascade."""
        results: list[dict[str, Any]] = []
        for cas in self._engine.history.cascade_history():
            results.append({
                "event_id": cas.root_event.event_id,
                "trigger": cas.root_event.trigger_coordinate,
                "kind": cas.root_event.trigger_kind.value,
                "total_affected": cas.total_affected,
                "depth": cas.cascade_depth,
                "complete": cas.is_complete(),
            })
        return results

    def hotspot_report(self, *, threshold: int = 3) -> dict[str, Any]:
        """Identify frequently-invalidated coordinates.

        Returns a mapping with the hotspot list and summary statistics.
        """
        hotspots = self._engine.history.hotspot_detection(threshold=threshold)
        freq = self._engine.history.frequency_analysis()
        return {
            "threshold": threshold,
            "hotspots": hotspots,
            "hotspot_count": len(hotspots),
            "frequency_top10": freq[:10],
        }

    def repair_backlog(self) -> dict[str, Any]:
        """Summary of outstanding repair work."""
        invalid = self._engine.tracker.invalid_set()
        staleness = self._engine.tracker.staleness_report()
        return {
            "pending_repairs": len(invalid),
            "keys": sorted(invalid),
            "staleness_top10": staleness[:10],
        }

    def graph_stats(self) -> dict[str, Any]:
        """Structural statistics about the σ graph."""
        graph = self._engine.graph
        roots = graph.roots()
        leaves = graph.leaves()
        return {
            "nodes": graph.node_count,
            "edges": graph.edge_count,
            "roots": sorted(roots),
            "leaves": sorted(leaves),
            "acyclic": graph.is_acyclic(),
        }

    def copilot_invalidation_summary(self) -> str:
        """Markdown summary designed for a copilot repair agent.

        Combines graph stats, current backlog, and hotspot data into a
        single document that an LLM can use to reason about the next
        repair steps.
        """
        summary = self.invalidation_summary()
        backlog = self.repair_backlog()
        hotspot = self.hotspot_report()

        lines: list[str] = [
            "# copilot: Invalidation Diagnostics",
            "",
            "## Graph Overview",
            f"- **Nodes:** {summary['total_nodes']}",
            f"- **Edges:** {summary['total_edges']}",
            f"- **Acyclic:** {'yes' if summary['is_acyclic'] else '⚠️ NO — cycle detected'}",
            f"- **Roots / Leaves:** {summary['root_count']} / {summary['leaf_count']}",
            "",
            "## Current Backlog",
            f"- **Invalid coordinates:** {backlog['pending_repairs']}",
        ]
        if backlog["staleness_top10"]:
            lines.append("- **Stalest (top 10):**")
            for key, sec in backlog["staleness_top10"]:
                lines.append(f"  - `{key}` — {sec:.1f}s stale")
        lines.append("")
        lines.append("## Hotspots")
        if hotspot["hotspots"]:
            for hs in hotspot["hotspots"]:
                lines.append(f"- `{hs}`")
        else:
            lines.append("- _No hotspots detected._")
        lines.append("")
        lines.append(f"## Event History ({summary['event_count']} total events)")
        cascade_analysis = self.cascade_analysis()
        for ca in cascade_analysis[-5:]:
            lines.append(
                f"- `{ca['event_id']}`: {ca['trigger']} ({ca['kind']}) "
                f"→ {ca['total_affected']} affected, depth {ca['depth']}"
            )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 10. InvalidationSerializer
# ---------------------------------------------------------------------------

class InvalidationSerializer:
    """Deterministic JSON (de)serialization for invalidation artefacts.

    Serialization determinism is a theorem burden from theory2.tex: equal
    semantic states must serialize identically up to declared ordering.
    All collection outputs are therefore sorted.
    """

    # -- graph serialization -----------------------------------------------

    @staticmethod
    def graph_to_dict(graph: InvalidationGraph) -> dict[str, Any]:
        """Serialize the σ graph to a JSON-ready dictionary.

        The encoding is ``{"nodes": [...], "edges": [{"source": ..., "target": ...}, ...]}``.
        """
        edges: list[dict[str, str]] = []
        for src in sorted(graph.nodes()):
            for tgt in sorted(graph.dependents_of(src)):
                edges.append({"source": src, "target": tgt})
        return {
            "nodes": sorted(graph.nodes()),
            "edges": edges,
        }

    @staticmethod
    def dict_to_graph(data: dict[str, Any]) -> InvalidationGraph:
        """Deserialize a dictionary into an :class:`InvalidationGraph`."""
        g = InvalidationGraph()
        for node in data.get("nodes", []):
            g.add_node(node)
        for edge in data.get("edges", []):
            g.add_dependency(edge["source"], edge["target"])
        return g

    # -- event serialization -----------------------------------------------

    @staticmethod
    def event_to_dict(event: InvalidationEvent) -> dict[str, Any]:
        """Serialize an :class:`InvalidationEvent`."""
        return {
            "event_id": event.event_id,
            "trigger_coordinate": event.trigger_coordinate,
            "trigger_kind": event.trigger_kind.value,
            "timestamp": event.timestamp,
            "affected_count": event.affected_count,
            "metadata": dict(event.metadata),
        }

    @staticmethod
    def dict_to_event(data: dict[str, Any]) -> InvalidationEvent:
        """Deserialize an :class:`InvalidationEvent`."""
        return InvalidationEvent(
            event_id=data["event_id"],
            trigger_coordinate=data["trigger_coordinate"],
            trigger_kind=TriggerKind(data["trigger_kind"]),
            timestamp=data["timestamp"],
            affected_count=data.get("affected_count", 0),
            metadata=data.get("metadata", {}),
        )

    # -- cascade serialization ---------------------------------------------

    @staticmethod
    def cascade_to_dict(cascade: InvalidationCascade) -> dict[str, Any]:
        """Serialize an :class:`InvalidationCascade`."""
        return {
            "root_event": InvalidationSerializer.event_to_dict(cascade.root_event),
            "waves": [sorted(w) for w in cascade.waves],
            "total_affected": cascade.total_affected,
            "cascade_depth": cascade.cascade_depth,
            "is_complete": cascade.is_complete(),
        }

    @staticmethod
    def dict_to_cascade(data: dict[str, Any]) -> InvalidationCascade:
        """Deserialize an :class:`InvalidationCascade`."""
        event = InvalidationSerializer.dict_to_event(data["root_event"])
        cas = InvalidationCascade(
            root_event=event,
            waves=data.get("waves", []),
            total_affected=data.get("total_affected", 0),
            cascade_depth=data.get("cascade_depth", 0),
            _complete=data.get("is_complete", False),
        )
        return cas

    # -- policy serialization ----------------------------------------------

    @staticmethod
    def policy_to_dict(policy: InvalidationPolicy) -> dict[str, Any]:
        """Serialize :class:`InvalidationPolicy`."""
        return {
            "cascade_depth_limit": policy.cascade_depth_limit,
            "cascade_strategy": policy.cascade_strategy.value,
            "notification_policy": policy.notification_policy.value,
            "copilot_assist_repair": policy.copilot_assist_repair,
            "batch_window_seconds": policy.batch_window_seconds,
            "max_batch_size": policy.max_batch_size,
        }

    @staticmethod
    def dict_to_policy(data: dict[str, Any]) -> InvalidationPolicy:
        """Deserialize :class:`InvalidationPolicy`."""
        return InvalidationPolicy(
            cascade_depth_limit=data.get("cascade_depth_limit", 64),
            cascade_strategy=CascadeStrategy(data.get("cascade_strategy", "eager")),
            notification_policy=NotificationPolicy(data.get("notification_policy", "after-cascade")),
            copilot_assist_repair=data.get("copilot_assist_repair", True),
            batch_window_seconds=data.get("batch_window_seconds", 0.5),
            max_batch_size=data.get("max_batch_size", 256),
        )

    # -- full round-trip helpers -------------------------------------------

    @staticmethod
    def to_json(obj: InvalidationGraph | InvalidationEvent | InvalidationCascade | InvalidationPolicy) -> str:
        """Serialize any supported object to a JSON string."""
        if isinstance(obj, InvalidationGraph):
            d = InvalidationSerializer.graph_to_dict(obj)
        elif isinstance(obj, InvalidationEvent):
            d = InvalidationSerializer.event_to_dict(obj)
        elif isinstance(obj, InvalidationCascade):
            d = InvalidationSerializer.cascade_to_dict(obj)
        elif isinstance(obj, InvalidationPolicy):
            d = InvalidationSerializer.policy_to_dict(obj)
        else:
            raise TypeError(f"Unsupported type for serialization: {type(obj)}")
        return json.dumps(d, sort_keys=True, indent=2)

    @staticmethod
    def from_json(text: str, cls: type) -> Any:
        """Deserialize a JSON string to the given class."""
        data = json.loads(text)
        dispatch: dict[type, Callable[[dict[str, Any]], Any]] = {
            InvalidationGraph: InvalidationSerializer.dict_to_graph,
            InvalidationEvent: InvalidationSerializer.dict_to_event,
            InvalidationCascade: InvalidationSerializer.dict_to_cascade,
            InvalidationPolicy: InvalidationSerializer.dict_to_policy,
        }
        factory = dispatch.get(cls)
        if factory is None:
            raise TypeError(f"Unsupported deserialization target: {cls}")
        return factory(data)


# ---------------------------------------------------------------------------
# Backward-compatible helper kept from original module
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class InvalidationPlan:
    """Frozen plan produced by :func:`plan_invalidation`."""

    reason: InvalidationReason
    invalidated_keys: tuple[str, ...]
    reopened_patches: tuple[str, ...]


def plan_invalidation(
    cache: SemanticCache,
    support: SupportRegion,
    cover: Cover,
    *,
    reason: InvalidationReason = InvalidationReason.SUPPORT_CHANGE,
) -> InvalidationPlan:
    """Create a localised invalidation plan (original API surface).

    Invalidates cache entries whose support intersects *support*, then
    computes the star neighbourhood in *cover* to identify reopened patches.
    """
    invalidated = cache.invalidate_by_support(support)
    star = star_of_support(support, cover)
    reopened = tuple(dict.fromkeys((support.coordinate.key, *star.adjacent_patches)))
    return InvalidationPlan(reason, invalidated, reopened)


# ---------------------------------------------------------------------------
# __all__
# ---------------------------------------------------------------------------

__all__ = [
    # Graph & DAG
    'InvalidationGraph',
    # Events & triggers
    'TriggerKind',
    'InvalidationEvent',
    # Policy
    'CascadeStrategy',
    'NotificationPolicy',
    'InvalidationPolicy',
    # Cascade
    'InvalidationCascade',
    # Tracker
    'InvalidationTracker',
    # Engine
    'InvalidationEngine',
    # Repair
    'RepairScheduler',
    # Notifications
    'InvalidationNotifier',
    # History & diagnostics
    'InvalidationHistory',
    'InvalidationDiagnostics',
    # Serialization
    'InvalidationSerializer',
    # Backward-compatible
    'InvalidationReason',
    'InvalidationPlan',
    'plan_invalidation',
]

# copilot: shared-core marker for LLM-assisted repair orchestration.
