"""AI Fleets — Ch42 §2 fleet member and registry implementation.

This module implements the *AI fleet* layer: the collection of autonomous
agents (fleet members) that collaborate to propose inhabitants for semantic
patches.

Theory — Ch42 §2 Fleet Architecture
--------------------------------------
A fleet F = (members, coordinator, current_bids) is a group of agents
operating under a shared coordinator.  The fleet participates in a
*Vickrey-style sealed-bid auction* for each goal G:

    ∀ member m ∈ F.members:
        b_m = m.bid(G)          -- member produces a bid
    winner = argmax_m score(b_m)  -- coordinator picks winner
    current_bids ← { b_m | m ∈ F.members }

Fleet utilization is defined as:

    utilization(F) = (Σ_{m ∈ F} m.current_load) / (|F| × MAX_LOAD)

where MAX_LOAD = 10.0 is the maximum load per member.

Fleet Registry
---------------
The FleetRegistry stores multiple fleets and provides fleet discovery:

    find_fleet_for(goal) → fleet F such that F.can_handle(goal)

If no fleet can handle the goal, None is returned and the caller must
create a default fleet.

Bid Aggregation
----------------
The BidAggregator picks the winning bid from a list:

    pick_winner(bids) → argmax_b b.compute_total_score()

In case of a tie, the bid with the lower bid_id (lexicographic) wins
(deterministic tie-breaking).

Examples
---------
>>> from jugeo.generation.inhabitant_fleets.ai_fleets import (
...     create_default_fleet, FleetRegistry,
... )
>>> fleet = create_default_fleet("fleet_0", n_members=2)
>>> len(fleet.members)
2
>>> reg = FleetRegistry()
>>> reg.register_fleet(fleet)
>>> found = reg.find_fleet_for({"label": "test"})
>>> found is fleet
True
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
    MoveType,
    make_bid,
    make_proposal,
)

MAX_LOAD: float = 10.0
DEFAULT_BID_SCORE: float = 0.6


# ---------------------------------------------------------------------------
# FleetMember
# ---------------------------------------------------------------------------


@dataclass(init=False)
class FleetMember:
    """A single autonomous agent within a fleet.

    A FleetMember has a specialization that biases its bid scoring toward
    certain goal types.  Members accumulate load as they process goals.

    Theory — Ch42 §2.1
    --------------------
    A member m is characterized by:
        • specialization ∈ {generic, analytic, creative, critical, integrative}
        • current_load ∈ [0, MAX_LOAD]
        • bid_history : list of past bids

    The member's bid score is adjusted by specialization affinity:
        affinity(m, G) = 1.0 if m.specialization matches G.type else 0.7

    Attributes
    ----------
    member_id : str
        Unique identifier for this member.
    specialization : str
        Member's specialization label.
    current_load : float
        Current workload in [0, MAX_LOAD].
    fleet_id : str
        ID of the fleet this member belongs to.
    """

    member_id: str
    specialization: str = "generic"
    current_load: float = 0.0
    fleet_id: str = ""
    created_at: float = field(default_factory=time.time)
    bid_history: list[str] = field(default_factory=list, repr=False)

    def __init__(
        self,
        member_id: str,
        specialization: str = "generic",
        current_load: float = 0.0,
        fleet_id: str = "",
        created_at: float | None = None,
        bid_history: list[str] | None = None,
        *,
        trust_tier: Any | None = None,
        proposal_history: list[str] | None = None,
    ) -> None:
        self.member_id = member_id
        self.specialization = specialization
        self.current_load = current_load
        self.fleet_id = fleet_id
        self.created_at = time.time() if created_at is None else created_at
        self.bid_history = list(bid_history if bid_history is not None else (proposal_history or []))
        self.trust_tier = trust_tier if trust_tier is not None else TrustTier.PROPOSAL
        self.proposal_history = self.bid_history

    def bid(self, goal: Any) -> FleetBid:
        """Produce a FleetBid for the given goal.

        Parameters
        ----------
        goal : Any
            Goal with a label or proposition attribute.

        Returns
        -------
        FleetBid
        """
        label = self._extract_goal_label(goal)
        proposition = self._extract_proposition(goal)
        affinity = self._compute_affinity(goal)
        load_penalty = self.current_load / MAX_LOAD * 0.2
        score = max(0.1, min(1.0, DEFAULT_BID_SCORE * affinity - load_penalty))

        b = make_bid(self.member_id, label, proposition[:80])
        # Override with computed score
        b.bid_score = round(score, 4)
        self.bid_history.append(b.bid_id)
        return b

    def can_handle(self, goal: Any) -> bool:
        return self.current_load < MAX_LOAD

    def compute_bid(self, goal: Any) -> FleetBid:
        return self.bid(goal)

    def propose(self, goal: Any, context: Any | None = None) -> InhabitantProposal:
        proposal = make_proposal(
            patch_id=str(getattr(goal, "patch_id", "patch-001")),
            section_label=str(getattr(goal, "label", "default")),
            content=self._extract_proposition(goal),
            trust_tier=self.trust_tier,
            evidence_score=max(0.0, 1.0 - (self.current_load / 100.0)),
        )
        self.proposal_history.append(proposal.proposal_id)
        return proposal

    def update_load(self, delta: float) -> None:
        self.current_load = min(100.0, max(0.0, self.current_load + delta))

    def _extract_goal_label(self, goal: Any) -> str:
        for attr in ("label", "name", "goal_id"):
            val = getattr(goal, attr, None)
            if val and isinstance(val, str):
                return val[:60]
        return str(goal)[:60]

    def _extract_proposition(self, goal: Any) -> str:
        for attr in ("proposition", "required_proposition", "description", "label"):
            val = getattr(goal, attr, None)
            if val and isinstance(val, str):
                return val
        return str(goal)

    def _compute_affinity(self, goal: Any) -> float:
        goal_type = str(getattr(goal, "goal_type", "") or getattr(goal, "type", "")).lower()
        spec = self.specialization.lower()
        if not goal_type:
            return 1.0
        if spec == "generic":
            return 0.9
        if spec in goal_type or goal_type in spec:
            return 1.0
        return 0.7

    def increment_load(self, amount: float = 1.0) -> None:
        """Increase current_load by amount, clamped to MAX_LOAD."""
        self.current_load = min(MAX_LOAD, self.current_load + amount)

    def reset_load(self) -> None:
        """Reset current_load to zero."""
        self.current_load = 0.0

    def is_available(self) -> bool:
        """Return True if load is below maximum."""
        return self.current_load < MAX_LOAD


# ---------------------------------------------------------------------------
# FleetCoordinator
# ---------------------------------------------------------------------------


class FleetCoordinator:
    """Coordinates bids from fleet members and selects winners.

    The coordinator implements the auction mechanism described in Ch42 §2.2:

        1. Collect bids from all available members
        2. Pick winner via BidAggregator
        3. Notify winner and update loads

    Attributes
    ----------
    fleet_id : str
        ID of the fleet being coordinated.
    """

    def __init__(self, fleet_id: str = "") -> None:
        self.fleet_id = fleet_id
        self._round_count = 0

    def coordinate(self, members: list[FleetMember] | Any, goal: Any) -> list[FleetBid]:
        """Collect bids from all available members for a goal.

        Parameters
        ----------
        members : list[FleetMember]
            All fleet members.
        goal : Any
            The goal to bid on.

        Returns
        -------
        list[FleetBid]
            All bids collected from available members.
        """
        self._round_count += 1
        bids: list[FleetBid] = []
        if not isinstance(members, list):
            members = list(getattr(members, "members", []))
        for m in members:
            if m.is_available():
                b = m.bid(goal)
                bids.append(b)
        return bids

    def resolve_conflicts(self, bids: list[FleetBid]) -> list[FleetBid]:
        if not bids:
            return []
        winner = BidAggregator().pick_winner(bids)
        return [winner] if winner is not None else []

    def assign_tasks(self, bids: list[FleetBid]) -> dict[str, str]:
        return {
            bid.goal_label: bid.fleet_member_id
            for bid in self.resolve_conflicts(bids)
        }

    def balance_load(self, fleet: Any) -> None:
        members = list(getattr(fleet, "members", []))
        if not members:
            return
        avg = sum(member.current_load for member in members) / len(members)
        for member in members:
            member.current_load = max(0.0, min(100.0, (member.current_load + avg) / 2.0))

    def notify_winner(self, winner_bid: FleetBid, members: list[FleetMember]) -> None:
        """Increment load for the winning member.

        Parameters
        ----------
        winner_bid : FleetBid
            The winning bid.
        members : list[FleetMember]
            All members; the one matching winner_bid.fleet_member_id gets load.
        """
        for m in members:
            if m.member_id == winner_bid.fleet_member_id:
                m.increment_load(float(winner_bid.resource_estimate))
                break

    @property
    def round_count(self) -> int:
        """Number of coordination rounds completed."""
        return self._round_count


# ---------------------------------------------------------------------------
# InhabitantFleet
# ---------------------------------------------------------------------------


class InhabitantFleet:
    """A fleet of AI agents that bid for goals and synthesize inhabitants.

    Theory — Ch42 §2.3
    --------------------
    A fleet F = (id, members, coordinator, current_bids) participates in
    the inhabitant synthesis pipeline.  The fleet's utilization is:

        utilization(F) = (Σ m.current_load) / (|F| × MAX_LOAD)

    Attributes
    ----------
    fleet_id : str
        Unique identifier.
    members : list[FleetMember]
        Fleet members.
    coordinator : FleetCoordinator
        The bid coordinator.
    current_bids : list[FleetBid]
        Bids from the most recent auction round.
    """

    def __init__(
        self,
        fleet_id: str,
        members: list[FleetMember] | None = None,
        coordinator: FleetCoordinator | None = None,
        strategy: str = "greedy",
        current_bids: list[FleetBid] | None = None,
        completed_proposals: list[Any] | None = None,
    ) -> None:
        self.fleet_id = fleet_id
        self.members: list[FleetMember] = members or []
        self.coordinator = coordinator or FleetCoordinator(fleet_id)
        self.strategy = strategy
        self.current_bids: list[FleetBid] = list(current_bids or [])
        self.completed_proposals: list[Any] = list(completed_proposals or [])
        self._goal_history: list[str] = []

    def bid_for(self, goal: Any) -> FleetBid | None:
        """Run an auction round for the given goal.

        Parameters
        ----------
        goal : Any
            The goal to bid on.

        Returns
        -------
        list[FleetBid]
            All bids from the current round.
        """
        bids = self.coordinator.coordinate(self.members, goal)
        self.current_bids = bids
        label = str(getattr(goal, "label", "") or str(goal))[:50]
        self._goal_history.append(label)
        return BidAggregator().pick_winner(bids)

    def utilization(self) -> float:
        """Return fleet utilization in [0, 1].

        Returns
        -------
        float
        """
        if not self.members:
            return 0.0
        total = sum(m.current_load for m in self.members)
        return total / (len(self.members) * MAX_LOAD)

    def can_handle(self, goal: Any) -> bool:
        """Return True if the fleet has at least one available member.

        Parameters
        ----------
        goal : Any
            The goal to check compatibility for.

        Returns
        -------
        bool
        """
        return any(m.is_available() for m in self.members)

    def reset(self) -> None:
        """Reset all member loads and clear current bids."""
        for m in self.members:
            m.reset_load()
        self.current_bids = []

    def add_member(self, member: FleetMember) -> None:
        """Add a member to the fleet."""
        member.fleet_id = self.fleet_id
        self.members.append(member)

    def remove_member(self, member_id: str) -> None:
        self.members = [member for member in self.members if member.member_id != member_id]

    def coordinate(self) -> list[FleetBid]:
        return list(self.current_bids)

    def get_best_bid(self) -> FleetBid | None:
        return BidAggregator().pick_winner(self.current_bids)

    def __repr__(self) -> str:
        return f"InhabitantFleet(id={self.fleet_id!r}, members={len(self.members)})"


# ---------------------------------------------------------------------------
# FleetRegistry
# ---------------------------------------------------------------------------


class FleetRegistry:
    """Registry of all available InhabitantFleet instances.

    The registry provides fleet discovery via find_fleet_for(goal).

    Attributes
    ----------
    _fleets : dict[str, InhabitantFleet]
        Mapping from fleet_id to fleet.
    """

    def __init__(self) -> None:
        self._fleets: dict[str, InhabitantFleet] = {}

    def register_fleet(self, fleet: InhabitantFleet) -> None:
        """Register a fleet.

        Parameters
        ----------
        fleet : InhabitantFleet
            Fleet to register.
        """
        self._fleets[fleet.fleet_id] = fleet

    def find_fleet_for(self, goal: Any) -> InhabitantFleet | None:
        """Find the first fleet that can handle the goal.

        Parameters
        ----------
        goal : Any
            The goal to find a fleet for.

        Returns
        -------
        InhabitantFleet | None
        """
        for fleet in self._fleets.values():
            if fleet.can_handle(goal):
                return fleet
        return None

    def get_fleet(self, fleet_id: str) -> InhabitantFleet | None:
        """Return the fleet with the given ID, or None."""
        return self._fleets.get(fleet_id)

    def list_fleets(self) -> list[str]:
        """Return list of registered fleet IDs."""
        return list(self._fleets.keys())

    def fleet_count(self) -> int:
        """Return the number of registered fleets."""
        return len(self._fleets)

    def all_fleets(self) -> list[InhabitantFleet]:
        """Return all registered fleets."""
        return list(self._fleets.values())

    def get_all_fleets(self) -> list[InhabitantFleet]:
        """Legacy alias for :meth:`all_fleets`."""
        return self.all_fleets()

    def deregister(self, fleet_id: str) -> None:
        self._fleets.pop(fleet_id, None)


# ---------------------------------------------------------------------------
# BidAggregator
# ---------------------------------------------------------------------------


class BidAggregator:
    """Aggregates bids from multiple fleet members and selects a winner.

    Implements the Vickrey-style auction: winner is the bid with the
    highest compute_total_score().  Ties are broken by lexicographic
    bid_id order (deterministic).

    Theory — Ch42 §2.4
    --------------------
    The auction rule:

        winner = argmax_{b ∈ bids} score(b)
        score(b) = b.bid_score × b.overlap_compatibility_score × b.backpressure_tolerance

    In Vickrey auctions, the winner pays the second-highest price.
    Here, the "price" is the resource_estimate of the second-highest bid.
    """

    def __init__(self) -> None:
        self._auction_count = 0

    def pick_winner(self, bids: list[FleetBid] | FleetBid) -> FleetBid | None:
        """Select the winning bid from a list.

        Parameters
        ----------
        bids : list[FleetBid]
            All bids in the current auction round.

        Returns
        -------
        FleetBid | None
            The winning bid, or None if bids is empty.
        """
        if bids is None or bids == []:
            return None
        self._auction_count += 1
        flat_bids: list[FleetBid] = []
        if isinstance(bids, FleetBid):
            flat_bids.append(bids)
        else:
            for bid in bids:
                if isinstance(bid, list):
                    flat_bids.extend(item for item in bid if item is not None)
                elif bid is not None:
                    flat_bids.append(bid)
        if not flat_bids:
            return None
        # Sort by (score descending, bid_id ascending for tie-breaking)
        scored = [(b.compute_total_score(), b.bid_id, b) for b in flat_bids]
        scored.sort(key=lambda x: (-x[0], x[1]))
        return scored[0][2]

    def rank_bids(self, bids: list[FleetBid]) -> list[FleetBid]:
        """Return bids sorted from best to worst.

        Parameters
        ----------
        bids : list[FleetBid]
            All bids to rank.

        Returns
        -------
        list[FleetBid]
            Bids in descending score order.
        """
        return sorted(bids, key=lambda b: (-b.compute_total_score(), b.bid_id))

    def aggregate(self, bids: list[FleetBid]) -> list[FleetBid]:
        return self.rank_bids(bids)

    def compute_ensemble(self, bids: list[FleetBid]) -> FleetBid | None:
        if not bids:
            return None
        winner = self.pick_winner(bids)
        if winner is None:
            return None
        mean_score = sum(bid.bid_score for bid in bids) / len(bids)
        winner.bid_score = max(0.0, min(1.0, mean_score))
        return winner

    def auction_count(self) -> int:
        """Number of auctions run."""
        return self._auction_count


# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------


def create_fleet_member(
    member_id: str | None = None,
    specialization: str = "generic",
    fleet_id: str = "",
) -> FleetMember:
    """Create a FleetMember with sensible defaults.

    Parameters
    ----------
    member_id : str | None
        Member ID; auto-generated if None.
    specialization : str
        Member specialization.
    fleet_id : str
        Parent fleet ID.

    Returns
    -------
    FleetMember
    """
    mid = member_id or f"member_{uuid.uuid4().hex[:8]}"
    return FleetMember(member_id=mid, specialization=specialization, fleet_id=fleet_id)


def create_default_fleet(
    fleet_id: str,
    n_members: int = 3,
) -> InhabitantFleet:
    """Create a fleet with n_members generic members.

    Parameters
    ----------
    fleet_id : str
        ID for the new fleet.
    n_members : int
        Number of members to create; defaults to 3.

    Returns
    -------
    InhabitantFleet
    """
    specializations = ["generic", "analytic", "creative", "critical", "integrative"]
    members = [
        create_fleet_member(
            member_id=f"{fleet_id}_m{i}",
            specialization=specializations[i % len(specializations)],
            fleet_id=fleet_id,
        )
        for i in range(max(1, n_members))
    ]
    return InhabitantFleet(fleet_id=fleet_id, members=members)


__all__ = [
    "FleetMember",
    "FleetCoordinator",
    "InhabitantFleet",
    "FleetRegistry",
    "BidAggregator",
    "create_default_fleet",
    "create_fleet_member",
]
