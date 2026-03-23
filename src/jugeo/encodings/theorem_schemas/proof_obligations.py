"""Proof obligation management for the theorem schemas subsystem.

Tracks, queues, dispatches, and audits all proof obligations arising from
schema instantiation. This module implements the obligation lifecycle from
creation (via schema instantiation) through assignment, discharge, and audit.
Proof obligations are the concrete work items that agents (solvers, humans,
copilot, oracles) must discharge to satisfy the theorem burdens of Chapter 36.

The primary classes are:

- ``ObligationTracker`` — central registry mapping obligation IDs to statuses
  and discharge records.  All mutations go through this object so that the
  rest of the system always has a single authoritative view of the obligation
  lifecycle.

- ``ObligationQueue`` — a priority-ordered heap of pending obligations.
  Consumers (dispatchers, schedulers) pull from this queue to find the
  highest-priority work item without scanning the full tracker.

- ``ObligationDispatcher`` — rule-based router that selects the most
  appropriate ``ProofAgent`` for each obligation based on subsystem, proof
  style, and current agent load.

- ``ObligationAuditor`` — post-discharge verifier that confirms discharge
  records meet minimum correctness criteria before they are accepted as
  evidence of proof completion.

Design principles
-----------------
1. Every mutation that changes an obligation's status is logged with a
   timestamp so that the full lifecycle can be reconstructed from the
   tracker's ``to_json()`` snapshot.
2. The queue and tracker are decoupled: the queue tells you *which* obligations
   are ready; the tracker tells you *what state* they are in.  Keep them
   synchronised by calling ``tracker.update_status`` whenever you push or pop
   from the queue.
3. Dispatching is intentionally rule-based rather than learned, because proof
   obligations have hard semantic requirements (e.g. SMT obligations must go
   to an SMT solver).

copilot: proof obligation lifecycle management for theorem schemas.
"""
from __future__ import annotations

import heapq
import json
import logging
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Iterator

from jugeo.encodings.theorem_schemas.models import (
    InstanceStatus,
    ProofAgent,
    ProofObligation,
    ProofStyle,
    SchemaInstance,
    SubsystemKind,
    SubsystemSchema,
    TheoremSchema,
)

__all__ = [
    "ObligationStatus",
    "DischargeRecord",
    "ObligationTracker",
    "ObligationQueue",
    "ObligationDispatcher",
    "ObligationAuditor",
    "build_obligations_from_schema",
    "dispatch_obligations",
]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Sentinel used by ObligationQueue to mark removed entries without resizing
# the underlying heap.
# ---------------------------------------------------------------------------
_REMOVED = "<removed>"


class ObligationStatus(str, Enum):
    """Lifecycle status of a single proof obligation.

    The status progresses along the following state machine::

        PENDING → ASSIGNED → IN_PROGRESS → DISCHARGED
                                         ↘ FAILED
                                         ↘ EXPIRED

    ``PENDING``      — Created but not yet assigned to any agent.
    ``ASSIGNED``     — Assigned to an agent; waiting for the agent to pick it up.
    ``IN_PROGRESS``  — Agent has acknowledged the obligation and is working.
    ``DISCHARGED``   — Proof evidence has been submitted and accepted.
    ``FAILED``       — The agent could not discharge the obligation.
    ``EXPIRED``      — The obligation's deadline elapsed before discharge.
    """

    PENDING = "pending"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    DISCHARGED = "discharged"
    FAILED = "failed"
    EXPIRED = "expired"


@dataclass
class DischargeRecord:
    """An immutable record of a successful (or attempted) proof discharge.

    Produced by ``ObligationTracker.discharge`` and consumed by
    ``ObligationAuditor``.  Each record captures *who* discharged the
    obligation, *when*, *what* proof evidence was submitted, and whether that
    evidence has been independently verified.

    Attributes:
        obligation_id: The ID of the ProofObligation that was discharged.
        agent: The ProofAgent that produced the discharge.
        proof_data: Arbitrary proof artefacts (e.g. proof term, witness,
            tactic script) keyed by format or tool name.
        timestamp: Unix timestamp at which the discharge was recorded.
        verified: True once an auditor has confirmed the proof evidence.
        verification_notes: Human- or machine-readable notes left by the
            auditor, explaining the outcome of verification.
    """

    obligation_id: str
    agent: ProofAgent
    proof_data: dict[str, Any]
    timestamp: float = field(default_factory=time.time)
    verified: bool = False
    verification_notes: str = ""

    # ------------------------------------------------------------------
    # Serialisation helpers
    # ------------------------------------------------------------------

    def to_json(self) -> dict[str, Any]:
        """Serialise this record to a JSON-compatible dictionary.

        The result can be round-tripped through ``from_json`` without loss.

        Returns:
            Dictionary with all fields; agent is stored as its string value.
        """
        return {
            "obligation_id": self.obligation_id,
            "agent": self.agent.value,
            "proof_data": self.proof_data,
            "timestamp": self.timestamp,
            "verified": self.verified,
            "verification_notes": self.verification_notes,
        }

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> DischargeRecord:
        """Reconstruct a DischargeRecord from a serialised dictionary.

        Args:
            d: Dictionary as produced by ``to_json()``.

        Returns:
            A new ``DischargeRecord`` instance with all fields populated.

        Raises:
            ValueError: If the ``agent`` field is not a valid ``ProofAgent``
                value.
        """
        return cls(
            obligation_id=d.get("obligation_id") or str(uuid.uuid4()),
            agent=ProofAgent(d["agent"]),
            proof_data=d.get("proof_data", {}),
            timestamp=d.get("timestamp", time.time()),
            verified=d.get("verified", False),
            verification_notes=d.get("verification_notes", ""),
        )

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def is_recent(self, threshold_seconds: float = 3600.0) -> bool:
        """Return True if this record was created within *threshold_seconds*.

        Useful for auditors and dashboards that want to highlight freshly
        submitted discharge evidence.

        Args:
            threshold_seconds: Age threshold in seconds (default: one hour).

        Returns:
            True when ``time.time() - self.timestamp <= threshold_seconds``.
        """
        return (time.time() - self.timestamp) <= threshold_seconds

    def summarize(self) -> str:
        """Return a compact, human-readable summary of this record.

        Includes the truncated obligation ID, agent name, verification status,
        and how many proof artefacts were submitted.

        Returns:
            A single-line summary string.
        """
        age = time.time() - self.timestamp
        verified_label = "✓ verified" if self.verified else "✗ unverified"
        return (
            f"Discharge[{self.obligation_id[:8]}] "
            f"by={self.agent.value} "
            f"artefacts={len(self.proof_data)} "
            f"{verified_label} "
            f"age={age:.1f}s"
        )


# ---------------------------------------------------------------------------
# ObligationTracker
# ---------------------------------------------------------------------------


class ObligationTracker:
    """Central registry for the lifecycle of all proof obligations.

    The tracker stores every ``ProofObligation`` that has been registered with
    it, along with its current ``ObligationStatus`` and, once discharged, the
    associated ``DischargeRecord``.  It is the single source of truth for
    obligation state and provides query methods for consumers such as
    dispatchers, auditors, and reporting dashboards.

    Thread safety
    -------------
    This implementation is *not* thread-safe.  If you need concurrent access
    wrap it in a lock or use it from a single async event loop.

    Example usage::

        tracker = ObligationTracker()
        tracker.register(obligation)
        tracker.update_status(obligation.obligation_id, ObligationStatus.ASSIGNED)
        record = tracker.discharge(obligation.obligation_id, ProofAgent.SOLVER, {"term": "..."})
    """

    def __init__(self) -> None:
        """Initialise an empty tracker.

        Internal structures:
        - ``_obligations``: maps obligation_id → ProofObligation
        - ``_statuses``: maps obligation_id → ObligationStatus
        - ``_discharge_records``: maps obligation_id → DischargeRecord
        - ``_history``: maps obligation_id → list of (timestamp, status) transitions
        - ``_created_at``: tracker creation timestamp
        """
        self._obligations: dict[str, ProofObligation] = {}
        self._statuses: dict[str, ObligationStatus] = {}
        self._discharge_records: dict[str, DischargeRecord] = {}
        self._history: dict[str, list[tuple[float, str]]] = defaultdict(list)
        self._created_at: float = time.time()
        logger.debug("ObligationTracker initialised at t=%.3f", self._created_at)

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, obligation: ProofObligation) -> None:
        """Register a new obligation with PENDING status.

        If an obligation with the same ID has already been registered this
        call is a no-op (idempotent), so it is safe to call multiple times.

        Args:
            obligation: The ``ProofObligation`` to register.
        """
        oid = obligation.obligation_id
        if oid in self._obligations:
            logger.debug("Obligation %s already registered; skipping.", oid[:8])
            return
        self._obligations[oid] = obligation
        self._statuses[oid] = ObligationStatus.PENDING
        self._history[oid].append((time.time(), ObligationStatus.PENDING.value))
        logger.debug("Registered obligation %s (%s).", oid[:8], obligation.statement[:40])

    # ------------------------------------------------------------------
    # Status management
    # ------------------------------------------------------------------

    def update_status(self, obligation_id: str, status: ObligationStatus) -> None:
        """Transition an obligation to a new status, recording the change.

        Args:
            obligation_id: ID of the obligation to update.
            status: The new ``ObligationStatus`` value.

        Raises:
            KeyError: If the obligation_id is not registered.
        """
        if obligation_id not in self._obligations:
            raise KeyError(f"Obligation {obligation_id!r} is not registered.")
        self._statuses[obligation_id] = status
        self._history[obligation_id].append((time.time(), status.value))
        logger.debug("Obligation %s → %s.", obligation_id[:8], status.value)

    def get_status(self, obligation_id: str) -> ObligationStatus | None:
        """Return the current status of an obligation, or None if unknown.

        Args:
            obligation_id: ID of the obligation to query.

        Returns:
            Current ``ObligationStatus`` or ``None`` if not registered.
        """
        return self._statuses.get(obligation_id)

    def get_by_status(self, status: ObligationStatus) -> list[ProofObligation]:
        """Return all obligations currently in the given status.

        The returned list is sorted by obligation priority (descending) so
        that callers receive the most urgent work first.

        Args:
            status: The ``ObligationStatus`` to filter by.

        Returns:
            List of matching ``ProofObligation`` objects.
        """
        matches = [
            self._obligations[oid]
            for oid, s in self._statuses.items()
            if s == status
        ]
        return sorted(matches, key=lambda o: o.priority, reverse=True)

    # ------------------------------------------------------------------
    # Discharge and failure
    # ------------------------------------------------------------------

    def discharge(
        self,
        obligation_id: str,
        agent: ProofAgent,
        proof_data: dict[str, Any],
    ) -> DischargeRecord:
        """Record a successful discharge of an obligation.

        Creates a ``DischargeRecord``, stores it, and transitions the
        obligation to ``DISCHARGED`` status.

        Args:
            obligation_id: ID of the obligation being discharged.
            agent: The agent that produced the proof.
            proof_data: Proof artefacts keyed by format or tool name.

        Returns:
            The newly created ``DischargeRecord``.

        Raises:
            KeyError: If the obligation is not registered.
            ValueError: If the obligation is already discharged or failed.
        """
        if obligation_id not in self._obligations:
            raise KeyError(f"Obligation {obligation_id!r} is not registered.")
        current = self._statuses[obligation_id]
        if current in (ObligationStatus.DISCHARGED, ObligationStatus.FAILED):
            raise ValueError(
                f"Obligation {obligation_id} is already in terminal status {current.value}."
            )
        record = DischargeRecord(
            obligation_id=obligation_id,
            agent=agent,
            proof_data=dict(proof_data),
            timestamp=time.time(),
        )
        self._discharge_records[obligation_id] = record
        self.update_status(obligation_id, ObligationStatus.DISCHARGED)
        logger.info("Obligation %s discharged by %s.", obligation_id[:8], agent.value)
        return record

    def fail(self, obligation_id: str, reason: str) -> None:
        """Mark an obligation as failed with an explanatory reason.

        Stores the failure reason in the obligation's metadata and transitions
        to ``FAILED`` status.

        Args:
            obligation_id: ID of the obligation that failed.
            reason: Human-readable description of the failure.

        Raises:
            KeyError: If the obligation is not registered.
        """
        if obligation_id not in self._obligations:
            raise KeyError(f"Obligation {obligation_id!r} is not registered.")
        self._obligations[obligation_id].metadata["failure_reason"] = reason
        self._obligations[obligation_id].metadata["failed_at"] = time.time()
        self.update_status(obligation_id, ObligationStatus.FAILED)
        logger.warning("Obligation %s failed: %s", obligation_id[:8], reason)

    # ------------------------------------------------------------------
    # Counting and querying
    # ------------------------------------------------------------------

    def count_by_status(self) -> dict[str, int]:
        """Return a mapping of status name → obligation count.

        Covers all six ``ObligationStatus`` values; missing statuses are
        reported as 0.

        Returns:
            Dictionary with one key per ``ObligationStatus`` value.
        """
        counts: dict[str, int] = {s.value: 0 for s in ObligationStatus}
        for s in self._statuses.values():
            counts[s.value] += 1
        return counts

    def pending_count(self) -> int:
        """Return the number of obligations in PENDING status.

        Returns:
            Integer count of pending obligations.
        """
        return sum(1 for s in self._statuses.values() if s == ObligationStatus.PENDING)

    def discharged_count(self) -> int:
        """Return the number of successfully discharged obligations.

        Returns:
            Integer count of discharged obligations.
        """
        return sum(
            1 for s in self._statuses.values() if s == ObligationStatus.DISCHARGED
        )

    def get_discharge_record(self, obligation_id: str) -> DischargeRecord | None:
        """Return the discharge record for an obligation, or None.

        Args:
            obligation_id: ID of the obligation whose record to retrieve.

        Returns:
            The ``DischargeRecord`` if one exists, otherwise ``None``.
        """
        return self._discharge_records.get(obligation_id)

    # ------------------------------------------------------------------
    # Serialisation and reporting
    # ------------------------------------------------------------------

    def to_json(self) -> dict[str, Any]:
        """Serialise the full tracker state to a JSON-compatible dictionary.

        The snapshot captures obligations, statuses, discharge records, and
        history so that the tracker can be reconstructed or audited offline.

        Returns:
            Nested dictionary suitable for ``json.dumps``.
        """
        return {
            "created_at": self._created_at,
            "snapshot_at": time.time(),
            "obligations": {
                oid: ob.to_json() for oid, ob in self._obligations.items()
            },
            "statuses": {
                oid: s.value for oid, s in self._statuses.items()
            },
            "discharge_records": {
                oid: rec.to_json()
                for oid, rec in self._discharge_records.items()
            },
            "history": dict(self._history),
        }

    def summary_report(self) -> str:
        """Generate a human-readable summary of tracker state.

        Includes counts per status, average obligation priority, and age of
        the oldest pending obligation.

        Returns:
            Multi-line report string.
        """
        counts = self.count_by_status()
        total = len(self._obligations)
        lines: list[str] = [
            "=== ObligationTracker Summary ===",
            f"  Total obligations : {total}",
            f"  Created at        : {self._created_at:.3f}",
        ]
        for status_name, count in sorted(counts.items()):
            lines.append(f"  {status_name:<15}: {count}")
        pending = self.get_by_status(ObligationStatus.PENDING)
        if pending:
            oldest_age = max(o.age_seconds() for o in pending)
            avg_priority = sum(o.priority for o in pending) / len(pending)
            lines.append(f"  Oldest pending    : {oldest_age:.1f}s")
            lines.append(f"  Avg pending prio  : {avg_priority:.2f}")
        lines.append("=================================")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# ObligationQueue
# ---------------------------------------------------------------------------


class ObligationQueue:
    """A priority-ordered queue of proof obligations backed by a min-heap.

    Higher priority obligations are returned first.  Internally we negate
    the priority value so that Python's ``heapq`` min-heap returns the
    highest-priority item first.  Ties are broken by a monotonic insertion
    counter so that ``heapq`` never needs to compare ``ProofObligation``
    objects directly.

    Lazy deletion is used: when an obligation is removed from the queue its
    entry in the heap is replaced with a sentinel value.  The sentinel is
    skipped when popping.

    Example usage::

        queue = ObligationQueue()
        queue.push(high_priority_obligation)
        queue.push(low_priority_obligation)
        next_ob = queue.pop()  # returns high_priority_obligation
    """

    def __init__(self) -> None:
        """Initialise an empty priority queue.

        Internal structures:
        - ``_heap``: list of ``[-priority, counter, obligation_id]`` entries.
        - ``_entry_finder``: maps obligation_id → heap entry (for removal).
        - ``_obligations``: maps obligation_id → ProofObligation (for retrieval).
        - ``_counter``: monotonically increasing tie-breaker.
        """
        self._heap: list[list[Any]] = []
        self._entry_finder: dict[str, list[Any]] = {}
        self._obligations: dict[str, ProofObligation] = {}
        self._counter: int = 0

    def push(self, obligation: ProofObligation) -> None:
        """Add an obligation to the queue.

        If the obligation is already present (same ID) it is silently ignored.
        Use ``requeue`` if you want to update the priority of an existing item.

        Args:
            obligation: The ``ProofObligation`` to enqueue.
        """
        oid = obligation.obligation_id
        if oid in self._entry_finder:
            return
        entry = [-obligation.priority, self._counter, oid]
        self._counter += 1
        self._entry_finder[oid] = entry
        self._obligations[oid] = obligation
        heapq.heappush(self._heap, entry)

    def pop(self) -> ProofObligation | None:
        """Remove and return the highest-priority obligation.

        Skips lazily-deleted sentinel entries.

        Returns:
            The highest-priority ``ProofObligation``, or ``None`` if the
            queue is empty.
        """
        while self._heap:
            entry = heapq.heappop(self._heap)
            oid = entry[2]
            if oid is _REMOVED:
                continue
            if oid not in self._entry_finder:
                continue
            del self._entry_finder[oid]
            obligation = self._obligations.pop(oid)
            return obligation
        return None

    def peek(self) -> ProofObligation | None:
        """Return the highest-priority obligation without removing it.

        Skips lazily-deleted sentinels but leaves the heap intact.

        Returns:
            The highest-priority ``ProofObligation``, or ``None``.
        """
        for entry in self._heap:
            oid = entry[2]
            if oid is _REMOVED:
                continue
            if oid in self._entry_finder:
                return self._obligations.get(oid)
        return None

    def remove(self, obligation_id: str) -> bool:
        """Lazily remove an obligation by ID.

        Marks its heap entry as removed without rebuilding the heap.

        Args:
            obligation_id: ID of the obligation to remove.

        Returns:
            True if the obligation was found and marked for removal,
            False if it was not in the queue.
        """
        entry = self._entry_finder.pop(obligation_id, None)
        if entry is None:
            return False
        entry[2] = _REMOVED
        self._obligations.pop(obligation_id, None)
        return True

    def is_empty(self) -> bool:
        """Return True when the queue contains no active obligations.

        Returns:
            True if there are no live entries in the queue.
        """
        return len(self._entry_finder) == 0

    def size(self) -> int:
        """Return the number of active obligations in the queue.

        Returns:
            Count of non-removed obligations.
        """
        return len(self._entry_finder)

    def requeue(self, obligation: ProofObligation) -> None:
        """Remove an obligation (if present) and re-add it with current priority.

        Use this when an obligation's priority has been updated and you want
        the queue to reflect the new value.

        Args:
            obligation: The ``ProofObligation`` to requeue.
        """
        self.remove(obligation.obligation_id)
        self.push(obligation)

    def drain(self) -> list[ProofObligation]:
        """Remove and return all obligations in priority order.

        After this call the queue will be empty.

        Returns:
            List of all obligations, highest priority first.
        """
        results: list[ProofObligation] = []
        while not self.is_empty():
            item = self.pop()
            if item is not None:
                results.append(item)
        return results

    def to_list(self) -> list[ProofObligation]:
        """Return all obligations sorted by priority (non-destructive).

        The queue is not modified; this is a read-only snapshot.

        Returns:
            List of obligations sorted by priority descending.
        """
        items = list(self._obligations.values())
        return sorted(items, key=lambda o: o.priority, reverse=True)


# ---------------------------------------------------------------------------
# ObligationDispatcher
# ---------------------------------------------------------------------------


class ObligationDispatcher:
    """Rule-based dispatcher that assigns proof obligations to agents.

    Rules are dictionaries with optional keys ``subsystem``, ``proof_style``,
    and ``preferred_agent``.  When dispatching an obligation the dispatcher
    evaluates all rules in insertion order and selects the first matching
    rule's ``preferred_agent``.  If no rule matches, ``default_agent_for_style``
    is used as a fallback.

    Load balancing is intentionally simple: the dispatcher tracks how many
    obligations are currently assigned to each agent and uses that count to
    break ties when multiple rules could apply.

    Example usage::

        dispatcher = ObligationDispatcher()
        dispatcher.add_rule({"proof_style": "smt", "preferred_agent": "smt"})
        agent = dispatcher.dispatch(obligation)
    """

    def __init__(self) -> None:
        """Initialise a dispatcher with no rules and no tracker.

        Internal structures:
        - ``_agent_queues``: maps ProofAgent → list of assigned obligation IDs.
        - ``_rules``: ordered list of rule dictionaries.
        - ``_tracker``: optional reference to an ObligationTracker.
        - ``_rule_counter``: monotonic counter used to assign rule IDs.
        """
        self._agent_queues: dict[ProofAgent, list[str]] = defaultdict(list)
        self._rules: list[dict[str, Any]] = []
        self._tracker: ObligationTracker | None = None
        self._rule_counter: int = 0

    def set_tracker(self, tracker: ObligationTracker) -> None:
        """Attach an ObligationTracker so status updates happen automatically.

        When a tracker is attached the dispatcher will call
        ``tracker.update_status(oid, ASSIGNED)`` immediately after routing.

        Args:
            tracker: The ``ObligationTracker`` to attach.
        """
        self._tracker = tracker

    def dispatch(self, obligation: ProofObligation) -> ProofAgent:
        """Select and return the best agent for this obligation.

        Rules are evaluated in insertion order.  The first rule whose
        ``subsystem`` (if present) and ``proof_style`` (if present) match the
        obligation is selected.  On a match the rule's ``preferred_agent`` is
        used, falling back to ``default_agent_for_style`` if the key is
        missing.

        The chosen agent is recorded in ``_agent_queues`` and, if a tracker
        is attached, the obligation's status is advanced to ``ASSIGNED``.

        Args:
            obligation: The ``ProofObligation`` to dispatch.

        Returns:
            The ``ProofAgent`` selected to handle this obligation.
        """
        selected: ProofAgent | None = None
        for rule in self._rules:
            subsystem_ok = (
                "subsystem" not in rule
                or rule["subsystem"] == obligation.subsystem.value
            )
            style_ok = (
                "proof_style" not in rule
                or rule["proof_style"] == obligation.proof_style.value
            )
            if subsystem_ok and style_ok:
                raw = rule.get("preferred_agent")
                if raw is not None:
                    try:
                        selected = ProofAgent(raw)
                    except ValueError:
                        selected = None
                break

        if selected is None:
            selected = self.default_agent_for_style(obligation.proof_style)

        self._agent_queues[selected].append(obligation.obligation_id)
        if self._tracker is not None:
            try:
                self._tracker.update_status(
                    obligation.obligation_id, ObligationStatus.ASSIGNED
                )
                self._tracker._obligations[obligation.obligation_id].assigned_agent = selected
            except KeyError:
                pass
        logger.debug(
            "Obligation %s dispatched to %s.", obligation.obligation_id[:8], selected.value
        )
        return selected

    def dispatch_batch(
        self, obligations: list[ProofObligation]
    ) -> dict[str, ProofAgent]:
        """Dispatch a list of obligations, returning the full routing map.

        Args:
            obligations: List of ``ProofObligation`` objects to dispatch.

        Returns:
            Dictionary mapping obligation_id → assigned ``ProofAgent``.
        """
        return {ob.obligation_id: self.dispatch(ob) for ob in obligations}

    def add_rule(self, rule: dict[str, Any]) -> None:
        """Append a routing rule to the rule list.

        The rule dictionary may contain any subset of:
        - ``subsystem`` (str): matches ``ProofObligation.subsystem.value``
        - ``proof_style`` (str): matches ``ProofObligation.proof_style.value``
        - ``preferred_agent`` (str): the ``ProofAgent.value`` to select
        - ``rule_id`` (str): optional explicit ID; auto-assigned if absent

        Args:
            rule: Routing rule dictionary.
        """
        if "rule_id" not in rule:
            rule = dict(rule)
            rule["rule_id"] = f"rule-{self._rule_counter}"
        self._rule_counter += 1
        self._rules.append(rule)
        logger.debug("Added dispatch rule %s.", rule.get("rule_id"))

    def remove_rule(self, rule_id: str) -> bool:
        """Remove the rule with the given rule_id.

        Args:
            rule_id: The ``rule_id`` of the rule to remove.

        Returns:
            True if a rule was removed, False if no rule had that ID.
        """
        before = len(self._rules)
        self._rules = [r for r in self._rules if r.get("rule_id") != rule_id]
        removed = len(self._rules) < before
        if removed:
            logger.debug("Removed dispatch rule %s.", rule_id)
        return removed

    def get_agent_load(self, agent: ProofAgent) -> int:
        """Return the number of obligations currently assigned to an agent.

        Args:
            agent: The ``ProofAgent`` to query.

        Returns:
            Count of obligation IDs in the agent's queue.
        """
        return len(self._agent_queues.get(agent, []))

    def rebalance(self) -> int:
        """Move excess obligations from over-loaded agents to idle ones.

        Identifies the agent with the highest load and the agent with the
        lowest load.  Moves obligations from the busiest to the least busy
        until the difference is at most 1.

        Returns:
            The total number of obligations moved.
        """
        agents = list(ProofAgent)
        moved = 0
        if not any(self._agent_queues.values()):
            return 0
        max_iter = 100
        for _ in range(max_iter):
            loads = {a: len(self._agent_queues.get(a, [])) for a in agents}
            busiest = max(loads, key=lambda a: loads[a])
            idlest = min(loads, key=lambda a: loads[a])
            if loads[busiest] - loads[idlest] <= 1:
                break
            oid = self._agent_queues[busiest].pop()
            self._agent_queues[idlest].append(oid)
            moved += 1
        logger.debug("Rebalanced %d obligations.", moved)
        return moved

    def default_agent_for_style(self, style: ProofStyle) -> ProofAgent:
        """Return the default agent for a given proof style.

        This is the fallback used when no explicit rule matches.

        Args:
            style: The ``ProofStyle`` of the obligation.

        Returns:
            The ``ProofAgent`` best suited to handle that style by default.
        """
        mapping: dict[ProofStyle, ProofAgent] = {
            ProofStyle.AUTOMATED: ProofAgent.SOLVER,
            ProofStyle.INTERACTIVE: ProofAgent.HUMAN,
            ProofStyle.SEMI_AUTOMATED: ProofAgent.TACTIC_ENGINE,
            ProofStyle.ORACLE: ProofAgent.ORACLE,
            ProofStyle.HUMAN: ProofAgent.HUMAN,
            ProofStyle.COPILOT: ProofAgent.COPILOT,
        }
        return mapping.get(style, ProofAgent.SOLVER)

    def to_json(self) -> dict[str, Any]:
        """Serialise the dispatcher's current state.

        Returns:
            Dictionary capturing rules and current agent load counts.
        """
        return {
            "rules": list(self._rules),
            "agent_loads": {
                agent.value: len(oids)
                for agent, oids in self._agent_queues.items()
            },
        }


# ---------------------------------------------------------------------------
# ObligationAuditor
# ---------------------------------------------------------------------------


class ObligationAuditor:
    """Post-discharge auditor that verifies discharge records.

    The auditor applies a (optionally user-supplied) verification function to
    each ``DischargeRecord`` and logs the result.  If no custom function is
    provided, the built-in heuristic checks that:

    1. ``record.verified`` is True.
    2. ``record.proof_data`` is non-empty.
    3. The record is not older than 30 days.

    All audit outcomes are appended to an in-memory audit log that can be
    exported as a structured report.

    Example usage::

        auditor = ObligationAuditor()
        passed = auditor.audit(discharge_record)
        print(auditor.generate_report())
    """

    def __init__(self) -> None:
        """Initialise an auditor with an empty log and default verifier.

        Internal structures:
        - ``_audit_log``: list of audit result dicts (obligation_id, passed, ts).
        - ``_verification_fn``: optional callable override for verification.
        """
        self._audit_log: list[dict[str, Any]] = []
        self._verification_fn: Callable[[DischargeRecord], bool] | None = None

    def _default_verify(self, record: DischargeRecord) -> bool:
        """Built-in verification heuristic applied when no custom fn is set.

        Checks: record is marked verified, proof_data is non-empty, and the
        record is not older than 30 days.

        Args:
            record: The ``DischargeRecord`` to verify.

        Returns:
            True if all three conditions hold.
        """
        thirty_days = 30 * 24 * 3600
        age_ok = (time.time() - record.timestamp) <= thirty_days
        return record.verified and bool(record.proof_data) and age_ok

    def audit(self, record: DischargeRecord) -> bool:
        """Audit a single discharge record and log the outcome.

        Uses ``_verification_fn`` if set, otherwise falls back to
        ``_default_verify``.

        Args:
            record: The ``DischargeRecord`` to audit.

        Returns:
            True if the record passes verification.
        """
        fn = self._verification_fn if self._verification_fn is not None else self._default_verify
        passed = fn(record)
        entry: dict[str, Any] = {
            "obligation_id": record.obligation_id,
            "agent": record.agent.value,
            "passed": passed,
            "timestamp": time.time(),
            "notes": record.verification_notes,
        }
        self._audit_log.append(entry)
        level = logging.INFO if passed else logging.WARNING
        logger.log(
            level,
            "Audit obligation %s: %s.",
            record.obligation_id[:8],
            "PASSED" if passed else "FAILED",
        )
        return passed

    def audit_batch(
        self, records: list[DischargeRecord]
    ) -> dict[str, bool]:
        """Audit a list of discharge records.

        Args:
            records: List of ``DischargeRecord`` objects to audit.

        Returns:
            Dictionary mapping obligation_id → audit result (True/False).
        """
        return {r.obligation_id: self.audit(r) for r in records}

    def set_verification_function(
        self, fn: Callable[[DischargeRecord], bool]
    ) -> None:
        """Replace the default verification heuristic with a custom function.

        The function must accept a single ``DischargeRecord`` argument and
        return a boolean.

        Args:
            fn: Custom verification callable.
        """
        self._verification_fn = fn
        logger.debug("Custom verification function registered.")

    def get_audit_log(self) -> list[dict[str, Any]]:
        """Return the full audit log as a list of entry dictionaries.

        Each entry contains: ``obligation_id``, ``agent``, ``passed``,
        ``timestamp``, and ``notes``.

        Returns:
            Shallow copy of the internal audit log list.
        """
        return list(self._audit_log)

    def failed_audits(self) -> list[str]:
        """Return the obligation IDs of all failed audit entries.

        Returns:
            List of obligation_id strings where ``passed`` is False.
        """
        return [e["obligation_id"] for e in self._audit_log if not e["passed"]]

    def passed_audits(self) -> list[str]:
        """Return the obligation IDs of all passed audit entries.

        Returns:
            List of obligation_id strings where ``passed`` is True.
        """
        return [e["obligation_id"] for e in self._audit_log if e["passed"]]

    def generate_report(self) -> str:
        """Generate a structured text report of all audit outcomes.

        Returns:
            Multi-line report string with per-obligation results and summary
            statistics.
        """
        total = len(self._audit_log)
        passed = len(self.passed_audits())
        failed = total - passed
        lines: list[str] = [
            "=== ObligationAuditor Report ===",
            f"  Total audited : {total}",
            f"  Passed        : {passed}",
            f"  Failed        : {failed}",
            "",
        ]
        for entry in self._audit_log:
            mark = "✓" if entry["passed"] else "✗"
            lines.append(
                f"  {mark} [{entry['obligation_id'][:8]}] "
                f"agent={entry['agent']} "
                f"ts={entry['timestamp']:.1f}"
            )
        lines.append("================================")
        return "\n".join(lines)

    def clear_log(self) -> int:
        """Clear the audit log and return the count of entries removed.

        Returns:
            The number of audit entries that were present before clearing.
        """
        count = len(self._audit_log)
        self._audit_log.clear()
        logger.debug("Audit log cleared (%d entries removed).", count)
        return count


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def build_obligations_from_schema(
    schema: SubsystemSchema,
    bindings: dict[str, dict[str, str]],
) -> list[ProofObligation]:
    """Build a list of proof obligations from a ``SubsystemSchema``.

    Iterates over all required theorems in *schema*.  For each theorem it
    looks up the bindings to use in *bindings* (keyed by ``schema_id``).  If
    no specific bindings are provided for a theorem, an empty binding
    dictionary is used, leaving any free variables as unresolved placeholders.

    The function then calls ``instance.to_proof_obligation()`` to produce a
    dispatchable ``ProofObligation`` for each theorem.

    Args:
        schema: The ``SubsystemSchema`` whose required theorems should be
            instantiated.
        bindings: Mapping of theorem ``schema_id`` → variable bindings
            dictionary.  May be partial; missing keys default to ``{}``.

    Returns:
        List of ``ProofObligation`` objects, one per required theorem, in
        definition order.

    Example::

        bindings = {"theorem-uuid": {"X": "ℤ", "f": "succ"}}
        obligations = build_obligations_from_schema(sub_schema, bindings)
    """
    obligations: list[ProofObligation] = []
    for theorem in schema.required_theorems:
        theorem_bindings = bindings.get(theorem.schema_id, {})
        instance: SchemaInstance = theorem.instantiate(theorem_bindings)
        obligation = instance.to_proof_obligation()
        obligations.append(obligation)
        logger.debug(
            "Built obligation %s from theorem %s.",
            obligation.obligation_id[:8],
            theorem.schema_id[:8],
        )
    return obligations


def dispatch_obligations(
    tracker: ObligationTracker,
    dispatcher: ObligationDispatcher,
) -> dict[str, ProofAgent]:
    """Dispatch all PENDING obligations in *tracker* via *dispatcher*.

    Retrieves every obligation in ``PENDING`` status from the tracker, sends
    each through the dispatcher, and returns the full routing map.  The
    tracker's statuses are updated to ``ASSIGNED`` as a side-effect (assuming
    the dispatcher has a tracker attached; if not, caller is responsible for
    updating statuses).

    Args:
        tracker: The ``ObligationTracker`` holding the pending obligations.
        dispatcher: The ``ObligationDispatcher`` used to route each obligation.

    Returns:
        Dictionary mapping ``obligation_id`` → assigned ``ProofAgent`` for
        every obligation that was pending at the time of the call.
    """
    pending = tracker.get_by_status(ObligationStatus.PENDING)
    logger.info("Dispatching %d pending obligations.", len(pending))
    routing: dict[str, ProofAgent] = {}
    for obligation in pending:
        agent = dispatcher.dispatch(obligation)
        routing[obligation.obligation_id] = agent
    return routing
