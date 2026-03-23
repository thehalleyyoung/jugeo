"""Budget allocation and management for frontier objectives (Ch47)."""
from __future__ import annotations

import heapq
import statistics
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

try:
    from jugeo.orchestration.frontier import (
        FrontierNode,
        Frontier,
        FrontierBudget,
        FrontierHistory,
        FrontierDiagnostics,
        PhaseKind,
        TransitionTrigger,
        FrontierScorer,
        FrontierDiversity,
        BackpressureController,
    )
except Exception:
    FrontierNode = None
    Frontier = None
    FrontierBudget = None
    FrontierHistory = None
    FrontierDiagnostics = None
    PhaseKind = None
    TransitionTrigger = None
    FrontierScorer = None
    FrontierDiversity = None
    BackpressureController = None

try:
    from jugeo.orchestration.controller import (
        OrchestratorState,
        ConvergenceMonitor,
        MoveHistory,
    )
except Exception:
    OrchestratorState = None
    ConvergenceMonitor = None
    MoveHistory = None

try:
    from jugeo.evidence.trust import TrustLevel, TrustAlgebra, TrustTier, TrustProfile
except Exception:
    TrustLevel = None
    TrustAlgebra = None
    TrustTier = None
    TrustProfile = None

try:
    from jugeo.orchestration.frontier_objectives.models import (
        FrontierObjective,
        ObjectiveKind,
        ClosureGainEstimate,
        DiversityMetric,
        ScoringState,
        ObjectiveSet,
        ObjectiveResult,
    )
except Exception:
    FrontierObjective = None
    ObjectiveKind = None
    ClosureGainEstimate = None
    DiversityMetric = None
    ScoringState = None
    ObjectiveSet = None
    ObjectiveResult = None


__all__ = [
    "BudgetChannel",
    "AllocationDecision",
    "BudgetAllocator",
    "AdaptiveBudgetPolicy",
    "BudgetLedger",
    "ChannelPriorityQueue",
    "BudgetRebalancer",
    "BudgetAuditLog",
    "BudgetReport",
    "make_default_allocator",
    "compute_roi",
    "recommend_rebalance",
    "generate_budget_report",
]


# ---------------------------------------------------------------------------
# BudgetChannel
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BudgetChannel:
    """Immutable value object describing a single named budget channel.

    A budget channel represents a logical partition of the total exploration
    budget.  Each channel tracks how much was allocated and how much has been
    consumed so that the allocator can make informed rebalancing decisions.

    Attributes:
        channel_id: Unique identifier for this channel.
        name: Human-readable channel label.
        priority: Relative scheduling priority (higher = more important).
        allocated: Total amount allocated to this channel.
        spent: Amount consumed so far.
    """

    channel_id: str
    name: str
    priority: float
    allocated: float
    spent: float

    def remaining(self) -> float:
        """Return the amount of budget not yet consumed.

        Returns:
            ``allocated - spent``, clamped to zero if over-spent.
        """
        return max(0.0, self.allocated - self.spent)

    def utilization(self) -> float:
        """Return the fraction of allocated budget that has been spent.

        Returns:
            Value in [0.0, 1.0]; 0.0 if nothing was allocated.
        """
        if self.allocated == 0:
            return 0.0
        return min(1.0, self.spent / self.allocated)

    def is_exhausted(self) -> bool:
        """Return ``True`` when all allocated budget has been consumed.

        Returns:
            ``True`` if :meth:`remaining` is zero (or below).
        """
        return self.remaining() <= 0.0

    def to_dict(self) -> dict:
        """Serialise channel state to a plain dictionary."""
        return {
            "channel_id": self.channel_id,
            "name": self.name,
            "priority": self.priority,
            "allocated": self.allocated,
            "spent": self.spent,
            "remaining": self.remaining(),
            "utilization": self.utilization(),
            "is_exhausted": self.is_exhausted(),
        }

    @classmethod
    def make(
        cls,
        name: str,
        allocated: float,
        priority: float = 1.0,
    ) -> BudgetChannel:
        """Factory that auto-generates a ``channel_id``.

        Args:
            name: Human-readable channel label.
            allocated: Initial budget allocation.
            priority: Scheduling priority (default 1.0).

        Returns:
            A new :class:`BudgetChannel` with zero spending.
        """
        return cls(
            channel_id=str(uuid.uuid4()),
            name=name,
            priority=priority,
            allocated=allocated,
            spent=0.0,
        )


# ---------------------------------------------------------------------------
# AllocationDecision
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AllocationDecision:
    """Immutable record of a single budget allocation or deallocation decision.

    Every call to :meth:`BudgetAllocator.allocate` or
    :meth:`BudgetAllocator.deallocate` that results in an approved action is
    represented as an :class:`AllocationDecision` for audit purposes.

    Attributes:
        decision_id: Unique identifier for this decision.
        channel: Name or ID of the affected budget channel.
        amount: Amount allocated (positive) or returned (negative).
        rationale: Free-text explanation of why this decision was made.
        timestamp: Unix timestamp at decision time.
        approved: Whether the request was approved.
    """

    decision_id: str
    channel: str
    amount: float
    rationale: str
    timestamp: float
    approved: bool

    def to_dict(self) -> dict:
        """Serialise decision to a plain dictionary."""
        return {
            "decision_id": self.decision_id,
            "channel": self.channel,
            "amount": self.amount,
            "rationale": self.rationale,
            "timestamp": self.timestamp,
            "approved": self.approved,
        }


# ---------------------------------------------------------------------------
# BudgetAllocator
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class BudgetAllocator:
    """Allocates and tracks budget across multiple named channels.

    Supports ``"adaptive"``, ``"proportional"``, and ``"fixed"`` allocation
    policies.  All channel mutations return :class:`AllocationDecision` records
    to provide a full audit trail.

    Attributes:
        channels: Mapping of channel name/id to :class:`BudgetChannel`.
        total_budget: The total budget envelope available for allocation.
        policy: Named allocation policy controlling rebalancing behaviour.
    """

    channels: dict[str, BudgetChannel]
    total_budget: float
    policy: str = "adaptive"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _decision(
        self,
        channel: str,
        amount: float,
        rationale: str,
        approved: bool,
    ) -> AllocationDecision:
        """Create an :class:`AllocationDecision` record."""
        return AllocationDecision(
            decision_id=str(uuid.uuid4()),
            channel=channel,
            amount=amount,
            rationale=rationale,
            timestamp=time.time(),
            approved=approved,
        )

    def _replace_channel(self, ch: BudgetChannel) -> None:
        """Update the channel registry with a modified channel."""
        self.channels[ch.name] = ch

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def allocate(
        self,
        channel: str,
        amount: float,
        rationale: str = "",
    ) -> AllocationDecision:
        """Allocate *amount* to the named *channel*.

        If the channel does not exist it is created on demand.  The allocation
        is approved only when sufficient unallocated budget remains.

        Args:
            channel: Channel name to allocate to.
            amount: Positive amount of budget to allocate.
            rationale: Optional human-readable reason for this allocation.

        Returns:
            An :class:`AllocationDecision` recording the outcome.
        """
        available = self.total_budget - self.total_allocated()
        if amount > available + 1e-9:
            return self._decision(channel, amount, rationale or "insufficient budget", False)

        if channel in self.channels:
            old_ch = self.channels[channel]
            new_ch = BudgetChannel(
                channel_id=old_ch.channel_id,
                name=old_ch.name,
                priority=old_ch.priority,
                allocated=old_ch.allocated + amount,
                spent=old_ch.spent,
            )
        else:
            new_ch = BudgetChannel.make(channel, amount)
        self._replace_channel(new_ch)
        return self._decision(channel, amount, rationale or "allocation approved", True)

    def deallocate(self, channel: str, amount: float) -> bool:
        """Return *amount* from *channel* back to the unallocated pool.

        Args:
            channel: Channel name to deallocate from.
            amount: Positive amount to return.

        Returns:
            ``True`` if the deallocation succeeded; ``False`` if the channel
            does not exist or has insufficient allocation.
        """
        if channel not in self.channels:
            return False
        ch = self.channels[channel]
        new_allocated = ch.allocated - amount
        if new_allocated < ch.spent:
            # Cannot reduce allocated below already-spent amount.
            return False
        new_ch = BudgetChannel(
            channel_id=ch.channel_id,
            name=ch.name,
            priority=ch.priority,
            allocated=max(0.0, new_allocated),
            spent=ch.spent,
        )
        self._replace_channel(new_ch)
        return True

    def rebalance(
        self, performance: dict[str, float]
    ) -> list[AllocationDecision]:
        """Rebalance channel allocations proportionally to their ROI scores.

        Channels with higher performance scores receive a larger share of the
        total budget.  The rebalance respects each channel's already-spent
        amount.

        Args:
            performance: Mapping ``{channel_name: roi_score}`` where higher
                values indicate better return on investment.

        Returns:
            List of :class:`AllocationDecision` records for each channel
            that received a new allocation.
        """
        if not self.channels:
            return []
        total_score = sum(performance.get(name, 0.0) for name in self.channels)
        decisions: list[AllocationDecision] = []
        for name, ch in list(self.channels.items()):
            score = performance.get(name, 0.0)
            share = (score / total_score) if total_score > 0 else 1.0 / len(self.channels)
            new_allocated = max(ch.spent, self.total_budget * share)
            new_ch = BudgetChannel(
                channel_id=ch.channel_id,
                name=ch.name,
                priority=ch.priority,
                allocated=new_allocated,
                spent=ch.spent,
            )
            self._replace_channel(new_ch)
            decisions.append(
                self._decision(
                    name,
                    new_allocated - ch.allocated,
                    f"rebalance: score={score:.3f}",
                    True,
                )
            )
        return decisions

    def total_allocated(self) -> float:
        """Return the sum of allocations across all channels.

        Returns:
            Total allocated budget (may exceed :attr:`total_budget` after
            manual edits; callers should treat this as authoritative).
        """
        return sum(ch.allocated for ch in self.channels.values())

    def total_remaining(self) -> float:
        """Return the sum of remaining (unspent) budget across all channels.

        Returns:
            Sum of :meth:`BudgetChannel.remaining` for all channels.
        """
        return sum(ch.remaining() for ch in self.channels.values())

    def channel_summary(self) -> dict[str, dict]:
        """Return a serialised summary of every channel.

        Returns:
            Mapping ``{channel_name: channel.to_dict()}``.
        """
        return {name: ch.to_dict() for name, ch in self.channels.items()}

    def top_channels(self, n: int = 3) -> list[str]:
        """Return the names of channels with the most remaining budget.

        Args:
            n: Number of top channels to return.

        Returns:
            List of channel names sorted by remaining budget descending.
        """
        ranked = sorted(
            self.channels.items(),
            key=lambda kv: kv[1].remaining(),
            reverse=True,
        )
        return [name for name, _ in ranked[:n]]

    def to_dict(self) -> dict:
        """Serialise allocator state to a plain dictionary."""
        return {
            "total_budget": self.total_budget,
            "total_allocated": self.total_allocated(),
            "total_remaining": self.total_remaining(),
            "policy": self.policy,
            "num_channels": len(self.channels),
            "channels": self.channel_summary(),
        }

    @classmethod
    def make(
        cls,
        total: float,
        channel_names: list[str],
        policy: str = "adaptive",
    ) -> BudgetAllocator:
        """Create a :class:`BudgetAllocator` with evenly-split initial allocations.

        Args:
            total: Total budget envelope.
            channel_names: List of channel names to pre-create.
            policy: Allocation policy name.

        Returns:
            A new :class:`BudgetAllocator` with equal allocations per channel.
        """
        if channel_names:
            per_channel = total / len(channel_names)
        else:
            per_channel = 0.0
        channels = {
            name: BudgetChannel.make(name, per_channel) for name in channel_names
        }
        return cls(channels=channels, total_budget=total, policy=policy)


# ---------------------------------------------------------------------------
# AdaptiveBudgetPolicy
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class AdaptiveBudgetPolicy:
    """Adapts channel allocations based on observed gain-to-cost ratios.

    Uses a momentum-based gradient update rule so that high-performing
    channels receive increasing shares of the budget over time.

    Attributes:
        learning_rate: Step size for weight updates.
        momentum: Fraction of previous gradient to carry forward.
        min_allocation: Minimum allocation weight for any channel (floor).
        history: Ordered list of ``{channel, gain, cost, weight}`` records.
    """

    learning_rate: float = 0.1
    momentum: float = 0.9
    min_allocation: float = 0.01
    history: list[dict] = field(default_factory=list)

    # Internal momentum state: channel -> velocity
    _velocities: dict[str, float] = field(default_factory=dict)

    def update(self, channel: str, gain: float, cost: float) -> float:
        """Update the allocation weight for *channel* given observed *gain* and *cost*.

        Args:
            channel: The channel to update.
            gain: Observed gain (higher is better).
            cost: Actual cost incurred (positive).

        Returns:
            New allocation weight for *channel* (not yet normalised).
        """
        roi = gain / max(cost, 1e-9)
        prev_velocity = self._velocities.get(channel, 0.0)
        velocity = self.momentum * prev_velocity + self.learning_rate * roi
        self._velocities[channel] = velocity
        # Determine previous weight from history.
        prev_weight = 1.0
        for record in reversed(self.history):
            if record.get("channel") == channel:
                prev_weight = record.get("weight", 1.0)
                break
        new_weight = max(self.min_allocation, prev_weight + velocity)
        self.history.append(
            {
                "channel": channel,
                "gain": gain,
                "cost": cost,
                "roi": roi,
                "weight": new_weight,
                "timestamp": time.time(),
            }
        )
        return new_weight

    def recommend(
        self,
        channels: list[str],
        total: float,
        performance: dict[str, float],
    ) -> dict[str, float]:
        """Recommend absolute budget allocations for *channels*.

        Each channel's share is proportional to its performance score,
        floored at :attr:`min_allocation` * total.

        Args:
            channels: List of channel names to allocate across.
            total: Total budget envelope to distribute.
            performance: Mapping ``{channel: roi}`` of observed performance.

        Returns:
            Mapping ``{channel: allocation}`` summing to *total*.
        """
        if not channels:
            return {}
        scores = {ch: max(self.min_allocation, performance.get(ch, self.min_allocation)) for ch in channels}
        total_score = sum(scores.values())
        return {ch: total * (s / total_score) for ch, s in scores.items()}

    def reset(self) -> None:
        """Clear history and momentum velocities.

        Returns:
            ``None``
        """
        self.history.clear()
        self._velocities.clear()

    def to_dict(self) -> dict:
        """Serialise policy state to a plain dictionary."""
        return {
            "learning_rate": self.learning_rate,
            "momentum": self.momentum,
            "min_allocation": self.min_allocation,
            "history_length": len(self.history),
            "num_channels_tracked": len(self._velocities),
        }


# ---------------------------------------------------------------------------
# BudgetLedger
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class BudgetLedger:
    """Double-entry ledger for tracking budget credits and debits.

    Maintains an ordered list of ledger entries and a running aggregate
    balance.  Debit calls will fail (returning ``False``) if the requested
    amount exceeds the available balance for the target channel.

    Attributes:
        ledger_id: Unique identifier for this ledger instance.
        entries: Ordered list of ledger entry dicts.
        balance: Running aggregate balance (credits minus debits).
    """

    ledger_id: str
    entries: list[dict] = field(default_factory=list)
    balance: float = 0.0

    def _entry(
        self,
        kind: str,
        channel: str,
        amount: float,
        note: str = "",
    ) -> dict:
        """Build a ledger entry dict."""
        return {
            "entry_id": str(uuid.uuid4()),
            "kind": kind,
            "channel": channel,
            "amount": amount,
            "note": note,
            "timestamp": time.time(),
        }

    def credit(self, channel: str, amount: float, note: str = "") -> None:
        """Add a credit entry for *channel*.

        Increases the aggregate :attr:`balance` by *amount*.

        Args:
            channel: Channel receiving the credit.
            amount: Positive amount to credit.
            note: Optional free-text note.
        """
        self.entries.append(self._entry("credit", channel, amount, note))
        self.balance += amount

    def debit(self, channel: str, amount: float, note: str = "") -> bool:
        """Attempt to debit *amount* from *channel*.

        The debit is rejected if the current balance for *channel* is
        insufficient.

        Args:
            channel: Channel to debit from.
            amount: Positive amount to consume.
            note: Optional free-text note.

        Returns:
            ``True`` if the debit was recorded; ``False`` otherwise.
        """
        if self.balance_for(channel) < amount:
            return False
        self.entries.append(self._entry("debit", channel, amount, note))
        self.balance -= amount
        return True

    def balance_for(self, channel: str) -> float:
        """Return the net balance (credits minus debits) for a specific channel.

        Args:
            channel: Channel identifier to query.

        Returns:
            Net float balance; may be negative if debits exceed credits.
        """
        total = 0.0
        for e in self.entries:
            if e["channel"] == channel:
                if e["kind"] == "credit":
                    total += e["amount"]
                elif e["kind"] == "debit":
                    total -= e["amount"]
        return total

    def total_credits(self) -> float:
        """Return the sum of all credit entries.

        Returns:
            Total credits across all channels.
        """
        return sum(e["amount"] for e in self.entries if e["kind"] == "credit")

    def total_debits(self) -> float:
        """Return the sum of all debit entries.

        Returns:
            Total debits across all channels.
        """
        return sum(e["amount"] for e in self.entries if e["kind"] == "debit")

    def recent_entries(self, n: int = 10) -> list[dict]:
        """Return the *n* most recent ledger entries.

        Args:
            n: Maximum number of entries to return.

        Returns:
            List of entry dicts, most recent last.
        """
        return self.entries[-n:]

    def to_dict(self) -> dict:
        """Serialise ledger metadata to a plain dictionary."""
        return {
            "ledger_id": self.ledger_id,
            "balance": self.balance,
            "total_credits": self.total_credits(),
            "total_debits": self.total_debits(),
            "num_entries": len(self.entries),
        }

    @classmethod
    def make(cls, initial_balance: float = 0.0) -> BudgetLedger:
        """Create a new :class:`BudgetLedger` with an optional opening credit.

        Args:
            initial_balance: If > 0, a synthetic opening credit is recorded.

        Returns:
            A new :class:`BudgetLedger` instance.
        """
        ledger = cls(ledger_id=str(uuid.uuid4()))
        if initial_balance > 0:
            ledger.credit("__opening__", initial_balance, "initial balance")
        return ledger


# ---------------------------------------------------------------------------
# ChannelPriorityQueue
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ChannelPriorityQueue:
    """Min-heap priority queue for budget channels keyed by expected return.

    Items are stored as ``(-priority, channel)`` internally so that the
    standard :mod:`heapq` min-heap behaves as a max-heap by priority.

    Attributes:
        items: Internal heap storage as ``(neg_priority, channel)`` pairs.
    """

    items: list[tuple[float, str]] = field(default_factory=list)

    def push(self, channel: str, priority: float) -> None:
        """Push *channel* onto the queue with the given *priority*.

        Higher priority values are returned first by :meth:`pop`.

        Args:
            channel: Channel identifier to enqueue.
            priority: Expected return or priority score (higher = sooner).
        """
        heapq.heappush(self.items, (-priority, channel))

    def pop(self) -> tuple[str, float] | None:
        """Remove and return the highest-priority channel.

        Returns:
            ``(channel, priority)`` tuple, or ``None`` if the queue is empty.
        """
        if not self.items:
            return None
        neg_priority, channel = heapq.heappop(self.items)
        return channel, -neg_priority

    def peek(self) -> tuple[str, float] | None:
        """Return the highest-priority channel without removing it.

        Returns:
            ``(channel, priority)`` tuple, or ``None`` if the queue is empty.
        """
        if not self.items:
            return None
        neg_priority, channel = self.items[0]
        return channel, -neg_priority

    def update_priority(self, channel: str, priority: float) -> None:
        """Update the priority of an existing *channel*.

        If the channel is not currently in the queue it is added.  Because
        heap updates require a full rebuild this method has O(n) cost.

        Args:
            channel: Channel identifier to update.
            priority: New priority value.
        """
        self.items = [
            (neg_p, ch) for neg_p, ch in self.items if ch != channel
        ]
        heapq.heapify(self.items)
        self.push(channel, priority)

    def size(self) -> int:
        """Return the number of items currently in the queue.

        Returns:
            Integer count of queued items.
        """
        return len(self.items)

    def to_dict(self) -> dict:
        """Serialise queue state to a plain dictionary."""
        top = self.peek()
        return {
            "size": self.size(),
            "top_channel": top[0] if top else None,
            "top_priority": top[1] if top else None,
        }


# ---------------------------------------------------------------------------
# BudgetRebalancer
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class BudgetRebalancer:
    """Triggers and applies budget rebalancing when channels underperform.

    Monitors performance metrics over a rolling window and fires when the
    performance spread between channels exceeds :attr:`rebalance_threshold`.

    Attributes:
        performance_window: Number of recent performance observations to use.
        rebalance_threshold: Minimum performance spread to trigger rebalancing.
        min_channel_budget: Floor allocation fraction for any single channel.
    """

    performance_window: int = 20
    rebalance_threshold: float = 0.2
    min_channel_budget: float = 0.05

    def should_rebalance(
        self,
        allocator: BudgetAllocator,
        performance: dict[str, float],
    ) -> bool:
        """Determine whether rebalancing is warranted.

        Rebalancing is triggered when:
        - At least two channels are tracked, *and*
        - The spread (max - min) of performance scores exceeds
          :attr:`rebalance_threshold`.

        Args:
            allocator: The current :class:`BudgetAllocator` state.
            performance: Mapping ``{channel: score}`` of recent ROI.

        Returns:
            ``True`` if rebalancing should be performed.
        """
        if len(allocator.channels) < 2:
            return False
        scores = [performance.get(name, 0.0) for name in allocator.channels]
        if not scores:
            return False
        spread = max(scores) - min(scores)
        return spread >= self.rebalance_threshold

    def compute_rebalance(
        self,
        allocator: BudgetAllocator,
        performance: dict[str, float],
    ) -> dict[str, float]:
        """Compute new absolute allocations for all channels.

        Allocations are proportional to performance scores, floored at
        :attr:`min_channel_budget` * total_budget.

        Args:
            allocator: The current :class:`BudgetAllocator` state.
            performance: Mapping ``{channel: score}`` of recent ROI.

        Returns:
            Mapping ``{channel: new_allocation}`` summing to total_budget.
        """
        names = list(allocator.channels.keys())
        if not names:
            return {}
        floor = self.min_channel_budget * allocator.total_budget
        raw_scores = {
            name: max(performance.get(name, 0.0), self.min_channel_budget)
            for name in names
        }
        total_score = sum(raw_scores.values())
        new_allocs: dict[str, float] = {}
        for name in names:
            share = raw_scores[name] / total_score if total_score > 0 else 1.0 / len(names)
            # Respect already-spent amounts: can't allocate below what's spent.
            spent = allocator.channels[name].spent
            new_allocs[name] = max(floor, max(spent, allocator.total_budget * share))
        return new_allocs

    def apply(
        self,
        allocator: BudgetAllocator,
        new_allocs: dict[str, float],
    ) -> list[AllocationDecision]:
        """Apply *new_allocs* to *allocator* and return the resulting decisions.

        For each channel the method adjusts the allocation in-place and
        records an :class:`AllocationDecision`.

        Args:
            allocator: The :class:`BudgetAllocator` to mutate.
            new_allocs: Mapping ``{channel: new_allocation}`` to apply.

        Returns:
            List of :class:`AllocationDecision` records.
        """
        decisions: list[AllocationDecision] = []
        for name, new_alloc in new_allocs.items():
            ch = allocator.channels.get(name)
            if ch is None:
                continue
            delta = new_alloc - ch.allocated
            new_ch = BudgetChannel(
                channel_id=ch.channel_id,
                name=ch.name,
                priority=ch.priority,
                allocated=new_alloc,
                spent=ch.spent,
            )
            allocator.channels[name] = new_ch
            decisions.append(
                AllocationDecision(
                    decision_id=str(uuid.uuid4()),
                    channel=name,
                    amount=delta,
                    rationale="rebalancer adjustment",
                    timestamp=time.time(),
                    approved=True,
                )
            )
        return decisions

    def to_dict(self) -> dict:
        """Serialise rebalancer configuration to a plain dictionary."""
        return {
            "performance_window": self.performance_window,
            "rebalance_threshold": self.rebalance_threshold,
            "min_channel_budget": self.min_channel_budget,
        }


# ---------------------------------------------------------------------------
# BudgetAuditLog
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class BudgetAuditLog:
    """Append-only audit log for all budget events.

    Retains a capped number of entries and supports filtering by channel or
    event type.  The log is intentionally append-only; entries cannot be
    removed or modified once written.

    Attributes:
        entries: Ordered list of audit event dicts.
        max_entries: Maximum number of entries to retain (FIFO eviction).
    """

    entries: list[dict] = field(default_factory=list)
    max_entries: int = 10000

    def append(
        self,
        event_type: str,
        channel: str,
        amount: float,
        metadata: dict | None = None,
    ) -> None:
        """Record a new audit event.

        Args:
            event_type: Short label for the event (e.g. ``"allocate"``,
                ``"debit"``, ``"rebalance"``).
            channel: Affected channel name or identifier.
            amount: Monetary amount involved (may be zero).
            metadata: Optional supplementary data to embed in the entry.
        """
        entry = {
            "entry_id": str(uuid.uuid4()),
            "event_type": event_type,
            "channel": channel,
            "amount": amount,
            "timestamp": time.time(),
            "metadata": metadata or {},
        }
        self.entries.append(entry)
        if len(self.entries) > self.max_entries:
            self.entries = self.entries[-self.max_entries :]

    def query(
        self,
        channel: str | None = None,
        event_type: str | None = None,
    ) -> list[dict]:
        """Filter audit entries by *channel* and/or *event_type*.

        Args:
            channel: Only include entries for this channel; ``None`` = any.
            event_type: Only include entries of this type; ``None`` = any.

        Returns:
            Filtered list of matching audit entries.
        """
        result = self.entries
        if channel is not None:
            result = [e for e in result if e["channel"] == channel]
        if event_type is not None:
            result = [e for e in result if e["event_type"] == event_type]
        return result

    def recent(self, n: int = 20) -> list[dict]:
        """Return the *n* most recently appended entries.

        Args:
            n: Maximum number of entries to return.

        Returns:
            List of entry dicts, most recent last.
        """
        return self.entries[-n:]

    def total_spent(self, channel: str | None = None) -> float:
        """Sum all debit/spend amounts in the log for an optional channel.

        Args:
            channel: If provided, restrict sum to entries for this channel.

        Returns:
            Sum of ``amount`` fields for all debit-like events.
        """
        debit_types = {"debit", "spend", "consume", "allocate"}
        entries = self.query(channel=channel)
        return sum(
            e["amount"]
            for e in entries
            if e["event_type"].lower() in debit_types
        )

    def to_dict(self) -> dict:
        """Serialise audit log metadata to a plain dictionary."""
        return {
            "total_entries": len(self.entries),
            "max_entries": self.max_entries,
        }


# ---------------------------------------------------------------------------
# BudgetReport
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BudgetReport:
    """Immutable summary report capturing a snapshot of allocator state.

    Generated by :func:`generate_budget_report` for reporting or logging
    purposes.  All monetary values are expressed in the same units as the
    parent :class:`BudgetAllocator`.

    Attributes:
        report_id: Unique identifier for this report.
        total_budget: Total budget envelope at report time.
        total_spent: Total amount consumed across all channels.
        utilization: Fraction of total budget consumed (``total_spent / total_budget``).
        channel_breakdown: Per-channel summary dicts.
        generated_at: Unix timestamp when the report was generated.
    """

    report_id: str
    total_budget: float
    total_spent: float
    utilization: float
    channel_breakdown: dict
    generated_at: float

    def to_dict(self) -> dict:
        """Serialise report to a plain dictionary."""
        return {
            "report_id": self.report_id,
            "total_budget": self.total_budget,
            "total_spent": self.total_spent,
            "utilization": self.utilization,
            "channel_breakdown": self.channel_breakdown,
            "generated_at": self.generated_at,
        }

    def summary(self) -> str:
        """Return a concise human-readable summary string.

        Returns:
            Multi-line text describing overall budget utilisation and the
            top three channels by spending.
        """
        lines = [
            f"BudgetReport [{self.report_id[:8]}]",
            f"  Total budget : {self.total_budget:.2f}",
            f"  Total spent  : {self.total_spent:.2f}",
            f"  Utilization  : {self.utilization * 100:.1f}%",
            "  Channels:",
        ]
        # Sort channels by spent descending.
        sorted_channels = sorted(
            self.channel_breakdown.items(),
            key=lambda kv: kv[1].get("spent", 0.0),
            reverse=True,
        )
        for name, info in sorted_channels[:5]:
            spent = info.get("spent", 0.0)
            alloc = info.get("allocated", 0.0)
            lines.append(f"    {name}: spent={spent:.2f} / allocated={alloc:.2f}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Module-level functions
# ---------------------------------------------------------------------------


def make_default_allocator(total: float = 100.0) -> BudgetAllocator:
    """Create a :class:`BudgetAllocator` with sensible default channels.

    The default channels are ``"explore"``, ``"exploit"``, and ``"transition"``
    with equal budget shares and adaptive policy.

    Args:
        total: Total budget envelope (default 100.0).

    Returns:
        A pre-configured :class:`BudgetAllocator`.
    """
    return BudgetAllocator.make(
        total=total,
        channel_names=["explore", "exploit", "transition"],
        policy="adaptive",
    )


def compute_roi(
    channel: str,
    ledger: BudgetLedger,
    gain_history: list[float],
) -> float:
    """Compute the return-on-investment for a budget channel.

    ROI is defined as ``mean_gain / total_spent`` where ``total_spent`` is
    derived from the ledger and ``mean_gain`` from *gain_history*.

    Args:
        channel: Channel identifier.
        ledger: :class:`BudgetLedger` containing spend records.
        gain_history: Ordered list of observed gain values for this channel.

    Returns:
        ROI float; returns 0.0 if no spending has occurred.
    """
    total_spent = abs(ledger.balance_for(channel))
    # balance_for returns credits - debits; negative means net spend.
    credits = sum(
        e["amount"]
        for e in ledger.entries
        if e["channel"] == channel and e["kind"] == "debit"
    )
    total_cost = credits if credits > 0 else max(0.0, -ledger.balance_for(channel))
    if total_cost == 0:
        return 0.0
    mean_gain = statistics.mean(gain_history) if gain_history else 0.0
    return mean_gain / total_cost


def recommend_rebalance(
    allocator: BudgetAllocator,
    performance: dict[str, float],
) -> dict[str, float]:
    """Compute recommended new allocations without mutating the allocator.

    Delegates to :class:`BudgetRebalancer` with default parameters.

    Args:
        allocator: The :class:`BudgetAllocator` to analyse.
        performance: Mapping ``{channel: roi_score}``.

    Returns:
        Mapping ``{channel: new_allocation}`` representing the recommendation.
    """
    rebalancer = BudgetRebalancer()
    return rebalancer.compute_rebalance(allocator, performance)


def generate_budget_report(
    allocator: BudgetAllocator,
    ledger: BudgetLedger,
) -> BudgetReport:
    """Generate a :class:`BudgetReport` snapshot from *allocator* and *ledger*.

    Args:
        allocator: The :class:`BudgetAllocator` to snapshot.
        ledger: The :class:`BudgetLedger` providing spend information.

    Returns:
        A new :class:`BudgetReport` capturing the current state.
    """
    total_spent = ledger.total_debits()
    utilization = total_spent / allocator.total_budget if allocator.total_budget > 0 else 0.0
    channel_breakdown: dict[str, dict] = {}
    for name, ch in allocator.channels.items():
        channel_breakdown[name] = {
            **ch.to_dict(),
            "ledger_balance": ledger.balance_for(name),
        }
    return BudgetReport(
        report_id=str(uuid.uuid4()),
        total_budget=allocator.total_budget,
        total_spent=total_spent,
        utilization=min(1.0, utilization),
        channel_breakdown=channel_breakdown,
        generated_at=time.time(),
    )
