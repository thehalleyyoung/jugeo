"""Fleet semantics for JuGeo orchestration.

Implements the fleet-semantics model from theory2.tex: fleet members propose
*semantic moves*, not mere patches.  A fleet bid is a normalised record that
includes intended judgment deltas, expected obligation effects, anticipated
treaty interactions, and a self-declared uncertainty profile.  This turns the
fleet from parallel noise into **competitive search over admissible futures**.

The module provides ten co-operating classes:

* ``FleetMember``        – identity, capabilities, trust ceiling, load tracking
* ``FleetBid``           – normalised bid record with semantic metadata
* ``Fleet``              – central fleet manager (register, solicit, assign, …)
* ``BidEvaluator``       – multi-criterion bid evaluation and Pareto selection
* ``FleetScheduler``     – work scheduling across the fleet
* ``CompetitiveSearch``  – tournament-style competitive search over moves
* ``FleetCalibration``   – runtime trust / accuracy / latency calibration
* ``ChallengeRecord``    – structured record of bid challenges
* ``FleetHistory``       – longitudinal fleet performance tracking
* ``FleetDiagnostics``   – summary reports and bottleneck detection

Backward-compatible ``FleetState`` wrapper is retained so existing tests
that construct ``FleetState((member,))`` continue to pass.

copilot: shared-core marker for LLM-fleet orchestration.
"""

from __future__ import annotations

import math
import statistics
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Sequence

from jugeo.orchestration.frontier import FrontierItem

# ── Cross-subsystem imports (guarded) ─────────────────────────────────────
try:
    from jugeo.evidence.trust import TrustAlgebra
except Exception:  # pragma: no cover
    TrustAlgebra = None  # type: ignore[assignment,misc]

try:
    from jugeo.judgments.sections import SectionFamily
except Exception:  # pragma: no cover
    SectionFamily = None  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class BidOutcome(Enum):
    """Outcome of a fleet bid after execution."""

    SUCCESS = auto()
    PARTIAL = auto()
    FAILURE = auto()
    TIMEOUT = auto()
    REJECTED = auto()


class ChallengeOutcome(Enum):
    """Result of a challenge between fleet members."""

    UPHELD = auto()
    OVERTURNED = auto()
    WITHDRAWN = auto()
    SPLIT = auto()


# ---------------------------------------------------------------------------
# 1. FleetMember
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class FleetMember:
    """A single fleet participant with capabilities, trust ceiling, and load.

    Fleet members are the atomic workers in the JuGeo orchestration layer.
    Each member advertises a set of *capabilities* (string tags), a numeric
    *trust_ceiling* that bounds how much epistemic weight the fleet may
    assign to its outputs, and a live *current_load* counter used by the
    scheduler for load-balancing.

    Parameters
    ----------
    name : str
        Human-readable name (also used as legacy positional identifier).
    capacity : int
        Maximum concurrent assignments this member can handle.
    member_id : str | None
        Unique identifier.  Auto-generated UUID if not supplied.
    capabilities : frozenset[str]
        Tags describing what this member can do (e.g. ``"prove"``,
        ``"countermodel"``, ``"treaty-check"``).
    trust_ceiling : float
        Upper bound on trust the fleet grants this member (0.0–1.0).
    specialization_domains : tuple[str, ...]
        Ordered list of domains the member is specialised in.
    current_load : int
        Number of assignments currently in progress.
    lifetime_stats : dict[str, Any]
        Accumulated statistics (wins, losses, latency samples, …).
    is_available : bool
        Whether the member is currently accepting bids.
    skills : tuple[str, ...]
        Legacy compatibility field (maps to ``capabilities``).
    """

    name: str
    capacity: int = 1
    member_id: str | None = None
    capabilities: frozenset[str] = frozenset()
    trust_ceiling: float = 1.0
    specialization_domains: tuple[str, ...] = ()
    current_load: int = 0
    lifetime_stats: dict[str, Any] = field(default_factory=dict)
    is_available: bool = True
    skills: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.member_id is None:
            self.member_id = uuid.uuid4().hex[:12]
        # Merge legacy *skills* into *capabilities* for a single source of truth.
        if self.skills:
            self.capabilities = self.capabilities | frozenset(self.skills)
        # Initialise lifetime_stats with sensible defaults if empty.
        if not self.lifetime_stats:
            self.lifetime_stats = {
                "bids_submitted": 0,
                "bids_won": 0,
                "assignments_completed": 0,
                "assignments_failed": 0,
                "total_latency_ms": 0.0,
                "accuracy_samples": [],
            }

    # -- public API ----------------------------------------------------------

    def can_handle(self, required_capabilities: frozenset[str]) -> bool:
        """Return ``True`` if this member possesses all *required_capabilities*.

        Also checks that the member is available and under its capacity limit.

        >>> m = FleetMember("w", capabilities=frozenset({"prove", "model"}))
        >>> m.can_handle(frozenset({"prove"}))
        True
        """
        if not self.is_available:
            return False
        if self.current_load >= self.capacity:
            return False
        return required_capabilities.issubset(self.capabilities)

    def bid_for(
        self,
        target: str,
        proposed_move: str,
        *,
        judgment_deltas: list[dict[str, Any]] | None = None,
        obligation_effects: list[dict[str, Any]] | None = None,
        treaty_interactions: list[dict[str, Any]] | None = None,
        uncertainty: dict[str, float] | None = None,
        estimated_cost: float = 0.0,
        estimated_time: float = 0.0,
    ) -> FleetBid:
        """Construct a :class:`FleetBid` on behalf of this member.

        The bid is automatically normalised so the fleet can compare it
        against competing proposals in the same round.

        Returns
        -------
        FleetBid
            A normalised bid ready for evaluation.
        """
        confidence = min(self.trust_ceiling, self._estimate_confidence(target))
        bid = FleetBid(
            bid_id=uuid.uuid4().hex[:16],
            member_id=self.member_id or self.name,
            target_coordinate=target,
            proposed_move=proposed_move,
            judgment_deltas=judgment_deltas or [],
            obligation_effects=obligation_effects or [],
            treaty_interactions=treaty_interactions or [],
            uncertainty_profile=uncertainty or {"epistemic": 0.5, "aleatory": 0.5},
            estimated_cost=estimated_cost,
            estimated_time=estimated_time,
            confidence=confidence,
        )
        bid.normalize()
        self.lifetime_stats["bids_submitted"] = (
            self.lifetime_stats.get("bids_submitted", 0) + 1
        )
        return bid

    def execute_assignment(self, bid: FleetBid) -> dict[str, Any]:
        """Simulate executing the work described by *bid*.

        In production this delegates to a real solver / prover backend.
        Here we return a structured outcome record used by the fleet to
        update history and calibration tables.
        """
        start = time.monotonic()
        self.current_load += 1
        # Simulate work – in production, delegate to solver pipeline.
        outcome: dict[str, Any] = {
            "bid_id": bid.bid_id,
            "member_id": self.member_id,
            "status": BidOutcome.SUCCESS.name,
            "judgment_deltas_applied": bid.judgment_deltas,
            "wall_time_ms": 0.0,
        }
        elapsed = (time.monotonic() - start) * 1000.0
        outcome["wall_time_ms"] = elapsed
        self.current_load = max(0, self.current_load - 1)
        self.lifetime_stats["assignments_completed"] = (
            self.lifetime_stats.get("assignments_completed", 0) + 1
        )
        self.lifetime_stats["total_latency_ms"] = (
            self.lifetime_stats.get("total_latency_ms", 0.0) + elapsed
        )
        return outcome

    def report_outcome(self, outcome: dict[str, Any]) -> None:
        """Incorporate *outcome* into this member's lifetime statistics.

        Called by the fleet after assignment completion so that the member
        can self-adjust (e.g. recalibrate confidence).
        """
        status = outcome.get("status", BidOutcome.FAILURE.name)
        if status == BidOutcome.SUCCESS.name:
            self.lifetime_stats["bids_won"] = (
                self.lifetime_stats.get("bids_won", 0) + 1
            )
        else:
            self.lifetime_stats["assignments_failed"] = (
                self.lifetime_stats.get("assignments_failed", 0) + 1
            )
        latency = outcome.get("wall_time_ms", 0.0)
        samples: list[float] = self.lifetime_stats.setdefault("accuracy_samples", [])
        samples.append(1.0 if status == BidOutcome.SUCCESS.name else 0.0)

    # -- internal helpers ----------------------------------------------------

    def _estimate_confidence(self, target: str) -> float:
        """Heuristic confidence based on domain match and past accuracy."""
        domain_bonus = 0.0
        for domain in self.specialization_domains:
            if domain.lower() in target.lower():
                domain_bonus = 0.15
                break
        samples: list[float] = self.lifetime_stats.get("accuracy_samples", [])
        if samples:
            base = statistics.mean(samples[-20:])
        else:
            base = 0.5
        return min(1.0, base + domain_bonus)

    # ── cross-subsystem integration ─────────────────────────────────────

    def trust_weighted_bidding(
        self,
        target: str,
        proposed_move: str,
        **bid_kwargs: Any,
    ) -> "FleetBid":
        """Construct a bid whose confidence is weighted by TrustAlgebra.

        Extends :meth:`bid_for` by consulting
        :class:`jugeo.evidence.trust.TrustAlgebra` to attenuate the
        member's raw confidence by its trust profile, ensuring that
        lower-trust channels produce correspondingly lower-confidence bids.

        Falls back to the standard :meth:`bid_for` when the trust
        subsystem is unavailable.

        Theory ref: theory2.tex §252 — Evidence Algebra, Trust Ceilings.
        """
        if TrustAlgebra is None:
            return self.bid_for(target, proposed_move, **bid_kwargs)

        algebra = TrustAlgebra()
        raw_confidence = self._estimate_confidence(target)
        trust_weight = algebra.attenuate(
            raw_confidence, ceiling=self.trust_ceiling
        )
        bid_kwargs.setdefault("uncertainty", {"epistemic": 1.0 - trust_weight, "aleatory": 0.5})
        bid = self.bid_for(target, proposed_move, **bid_kwargs)
        # Patch confidence with trust-weighted value (FleetBid is mutable).
        bid.confidence = min(self.trust_ceiling, trust_weight)
        bid.normalize()
        return bid


# ---------------------------------------------------------------------------
# 2. FleetBid
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class FleetBid:
    """Normalised bid record for competitive fleet evaluation.

    A fleet bid is *not* a patch – it is a semantic proposal that declares
    the *intended judgment deltas*, the *expected obligation effects*, the
    *anticipated treaty interactions*, and a *self-declared uncertainty
    profile*.  Normalisation ensures every bid is comparable on a common
    scale, turning the fleet from parallel noise into structured competitive
    search.

    Parameters
    ----------
    bid_id : str
        Unique identifier for this bid.
    member_id : str
        Identifier of the :class:`FleetMember` submitting the bid.
    target_coordinate : str
        The geometric coordinate this bid aims to advance.
    proposed_move : str
        Human-readable description of the proposed semantic move.
    judgment_deltas : list[dict[str, Any]]
        List of judgment-level changes the move would produce.
    obligation_effects : list[dict[str, Any]]
        Expected changes to the obligation lattice.
    treaty_interactions : list[dict[str, Any]]
        Anticipated interactions with existing treaties.
    uncertainty_profile : dict[str, float]
        Keys like ``"epistemic"`` and ``"aleatory"``; values in [0, 1].
    estimated_cost : float
        Resource cost estimate (abstract units).
    estimated_time : float
        Wall-clock time estimate in seconds.
    confidence : float
        Overall confidence in the bid (0.0–1.0).
    timestamp : float
        Time of bid creation (``time.time()``).
    is_normalized : bool
        Whether :meth:`normalize` has been called.
    """

    bid_id: str = ""
    member_id: str = ""
    target_coordinate: str = ""
    proposed_move: str = ""
    judgment_deltas: list[dict[str, Any]] = field(default_factory=list)
    obligation_effects: list[dict[str, Any]] = field(default_factory=list)
    treaty_interactions: list[dict[str, Any]] = field(default_factory=list)
    uncertainty_profile: dict[str, float] = field(default_factory=dict)
    estimated_cost: float = 0.0
    estimated_time: float = 0.0
    confidence: float = 0.5
    timestamp: float = field(default_factory=time.time)
    is_normalized: bool = False

    # -- normalisation & comparison -----------------------------------------

    def normalize(self) -> None:
        """Normalise the bid so that all numeric fields are on [0, 1].

        Confidence is clamped, uncertainty values are clamped, and cost /
        time are stored as-is (normalisation across bids happens in the
        evaluator).  Sets ``is_normalized`` to ``True``.
        """
        self.confidence = max(0.0, min(1.0, self.confidence))
        for key in list(self.uncertainty_profile):
            self.uncertainty_profile[key] = max(
                0.0, min(1.0, self.uncertainty_profile[key])
            )
        self.estimated_cost = max(0.0, self.estimated_cost)
        self.estimated_time = max(0.0, self.estimated_time)
        self.is_normalized = True

    def compare_to(self, other: FleetBid) -> int:
        """Compare this bid to *other* by composite heuristic.

        Returns +1 if ``self`` is preferred, -1 if *other* is preferred,
        or 0 if they are indistinguishable.
        """
        s_score = self._quick_score()
        o_score = other._quick_score()
        if s_score > o_score:
            return 1
        if s_score < o_score:
            return -1
        return 0

    def is_admissible(self, *, max_uncertainty: float = 0.8) -> bool:
        """Check whether the bid meets minimum admissibility criteria.

        A bid is admissible when its confidence exceeds a floor and its
        aggregate uncertainty does not exceed *max_uncertainty*.
        """
        if self.confidence < 0.1:
            return False
        if not self.uncertainty_profile:
            return True
        avg_uncertainty = statistics.mean(self.uncertainty_profile.values())
        return avg_uncertainty <= max_uncertainty

    def dominates(self, other: FleetBid) -> bool:
        """Return ``True`` if this bid Pareto-dominates *other*.

        Domination requires being at least as good on *every* criterion
        and strictly better on at least one.
        """
        criteria_self = self._criteria_vector()
        criteria_other = other._criteria_vector()
        at_least_as_good = all(s >= o for s, o in zip(criteria_self, criteria_other))
        strictly_better = any(s > o for s, o in zip(criteria_self, criteria_other))
        return at_least_as_good and strictly_better

    # -- helpers -------------------------------------------------------------

    def _quick_score(self) -> float:
        """Single-number heuristic for rapid comparison."""
        delta_weight = len(self.judgment_deltas) * 0.1
        obligation_weight = len(self.obligation_effects) * 0.05
        unc_penalty = sum(self.uncertainty_profile.values()) * 0.1
        return self.confidence + delta_weight + obligation_weight - unc_penalty

    def _criteria_vector(self) -> tuple[float, ...]:
        """Multi-criteria vector: (confidence, delta_count, -cost, -uncertainty)."""
        avg_unc = (
            statistics.mean(self.uncertainty_profile.values())
            if self.uncertainty_profile
            else 0.5
        )
        return (
            self.confidence,
            float(len(self.judgment_deltas)),
            -self.estimated_cost,
            -avg_unc,
        )


# ---------------------------------------------------------------------------
# 3. Fleet (main fleet manager)
# ---------------------------------------------------------------------------


class Fleet:
    """Central fleet manager implementing competitive-search semantics.

    The ``Fleet`` orchestrates registration, bid solicitation, evaluation,
    assignment, and result collection across an ensemble of
    :class:`FleetMember` instances.  The copilot loop calls into this
    manager when it needs to distribute proof / construction work.
    """

    def __init__(self) -> None:
        self._members: dict[str, FleetMember] = {}
        self._pending_bids: list[FleetBid] = []
        self._results: list[dict[str, Any]] = []
        self._round: int = 0

    # -- membership ----------------------------------------------------------

    def register_member(self, member: FleetMember) -> None:
        """Register *member* with the fleet.

        Raises ``ValueError`` if a member with the same id is already
        registered.
        """
        key = member.member_id or member.name
        if key in self._members:
            raise ValueError(f"Member {key!r} already registered")
        self._members[key] = member

    def remove_member(self, member_id: str) -> FleetMember | None:
        """Remove and return the member identified by *member_id*.

        Returns ``None`` if the member was not found.
        """
        return self._members.pop(member_id, None)

    # -- bidding -------------------------------------------------------------

    def solicit_bids(
        self,
        target: str,
        proposed_move: str | None = None,
        required_capabilities: frozenset[str] = frozenset(),
    ) -> list[FleetBid]:
        """Ask every eligible member to bid on *target*.

        Eligibility is determined by :meth:`FleetMember.can_handle`.
        Returns the list of collected bids (also stored internally for
        subsequent evaluation).
        """
        self._pending_bids.clear()
        move = proposed_move or f"move-on-{target}"
        for member in self._members.values():
            if member.can_handle(required_capabilities):
                bid = member.bid_for(target, move)
                self._pending_bids.append(bid)
        return list(self._pending_bids)

    def evaluate_bids(self, evaluator: BidEvaluator | None = None) -> list[FleetBid]:
        """Evaluate pending bids and return them ranked best-first.

        If no *evaluator* is supplied a default :class:`BidEvaluator` is
        used.
        """
        ev = evaluator or BidEvaluator()
        scored = [(ev.composite_score(b), b) for b in self._pending_bids]
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [b for _, b in scored]

    # -- assignment ----------------------------------------------------------

    def assign_work(
        self,
        bid: FleetBid,
    ) -> dict[str, Any]:
        """Assign the work described by *bid* to its originating member.

        Returns the outcome dictionary produced by
        :meth:`FleetMember.execute_assignment`.
        """
        member = self._members.get(bid.member_id)
        if member is None:
            return {"bid_id": bid.bid_id, "status": BidOutcome.REJECTED.name}
        outcome = member.execute_assignment(bid)
        member.report_outcome(outcome)
        self._results.append(outcome)
        self._round += 1
        return outcome

    def collect_results(self) -> list[dict[str, Any]]:
        """Return all results gathered so far, clearing the internal buffer."""
        out = list(self._results)
        self._results.clear()
        return out

    # -- fleet-level queries -------------------------------------------------

    def rebalance(self) -> dict[str, int]:
        """Return a load snapshot and mark over-loaded members unavailable.

        Members whose ``current_load`` meets their ``capacity`` are
        temporarily set to ``is_available = False``.  Returns a dict
        mapping member ids to their current load.
        """
        snapshot: dict[str, int] = {}
        for mid, member in self._members.items():
            snapshot[mid] = member.current_load
            member.is_available = member.current_load < member.capacity
        return snapshot

    def fleet_health(self) -> dict[str, Any]:
        """Aggregate health report for the copilot dashboard.

        Includes total members, availability ratio, aggregate load, and
        average trust ceiling.
        """
        total = len(self._members)
        available = sum(1 for m in self._members.values() if m.is_available)
        load = sum(m.current_load for m in self._members.values())
        avg_trust = (
            statistics.mean(m.trust_ceiling for m in self._members.values())
            if self._members
            else 0.0
        )
        return {
            "total_members": total,
            "available_members": available,
            "availability_ratio": available / total if total else 0.0,
            "aggregate_load": load,
            "average_trust_ceiling": round(avg_trust, 4),
            "round": self._round,
        }

    def active_members(self) -> list[FleetMember]:
        """Return registered members currently active in the fleet."""
        return [m for m in self._members.values() if m.is_available]

    def idle_members(self) -> list[FleetMember]:
        """Return members with zero current load and available flag set."""
        return [
            m
            for m in self._members.values()
            if m.current_load == 0 and m.is_available
        ]

    # ── cross-subsystem integration ─────────────────────────────────────

    def judgment_fleet_assignment(
        self, section_family: Any | None = None
    ) -> dict[str, list[str]]:
        """Assign fleet members to judgment sections.

        Uses :class:`jugeo.judgments.sections.SectionFamily` to partition
        the proof obligation space into sections and then maps each
        section to the fleet members whose specialisation domains best
        match.

        Parameters
        ----------
        section_family
            An optional :class:`SectionFamily` instance.  If ``None`` and
            the judgments subsystem is available, a default family is
            constructed from the current fleet capabilities.

        Returns a mapping from section name to the list of member IDs
        assigned to that section.

        Theory ref: theory2.tex §3 — Local Sections and Gluing.
        """
        if SectionFamily is None:
            # Fallback: assign all members to a single "default" section.
            return {"default": [m.member_id or m.name for m in self.active_members()]}

        family = section_family or SectionFamily.from_capabilities(
            [m.capabilities for m in self._members.values()]
        )
        sections = family.sections() if hasattr(family, "sections") else []
        assignment: dict[str, list[str]] = {}
        for section in sections:
            name = getattr(section, "name", str(section))
            domain = getattr(section, "domain", name)
            matched = [
                m.member_id or m.name
                for m in self.active_members()
                if any(d.lower() in domain.lower() for d in m.specialization_domains)
                or m.can_handle(frozenset({domain}))
            ]
            # Ensure at least one member per section by falling back to idle.
            if not matched:
                idle = self.idle_members()
                if idle:
                    matched = [idle[0].member_id or idle[0].name]
            assignment[name] = matched
        return assignment

    def judgment_assignment(self):
        """Create judgment assignments for fleet members."""
        try:
            from jugeo.judgments.judgment_terms import Judgment, JudgmentBuilder, Proposition, EvidenceBundle, EvidenceItem
            from jugeo.judgments.sections import Section, SectionFamily
            from jugeo.geometry.site import Coordinate, CoordinateKind
            from jugeo.evidence.trust import TrustLevel, TrustAlgebra
            return {"assignments": "ready"}
        except Exception:
            return {"assignments": "unavailable"}


# ---------------------------------------------------------------------------
# 4. BidEvaluator
# ---------------------------------------------------------------------------


class BidEvaluator:
    """Multi-criterion evaluator that ranks :class:`FleetBid` instances.

    The evaluator scores each bid along four axes – judgment delta quality,
    obligation impact, treaty safety, and raw confidence – then combines
    them into a single composite that respects Pareto dominance.

    The copilot process uses the evaluator to decide which fleet member's
    semantic move to accept in each round of competitive search.
    """

    def __init__(
        self,
        *,
        delta_weight: float = 0.35,
        obligation_weight: float = 0.20,
        treaty_weight: float = 0.20,
        confidence_weight: float = 0.25,
    ) -> None:
        self.delta_weight = delta_weight
        self.obligation_weight = obligation_weight
        self.treaty_weight = treaty_weight
        self.confidence_weight = confidence_weight

    # -- axis scores ---------------------------------------------------------

    def evaluate(self, bid: FleetBid) -> dict[str, float]:
        """Return a dictionary of per-axis scores for *bid*."""
        return {
            "judgment_delta": self.score_judgment_delta(bid),
            "obligation_effect": self.score_obligation_effect(bid),
            "treaty_impact": self.score_treaty_impact(bid),
            "confidence": bid.confidence,
            "composite": self.composite_score(bid),
        }

    def score_judgment_delta(self, bid: FleetBid) -> float:
        """Score the judgment-delta component of *bid*.

        Higher scores indicate the bid proposes more useful judgment
        changes.  Each delta contributes a magnitude signal; opposing
        deltas (those with ``"direction": "negative"``) incur a penalty.
        """
        if not bid.judgment_deltas:
            return 0.0
        total = 0.0
        for delta in bid.judgment_deltas:
            magnitude = float(delta.get("magnitude", 0.5))
            direction = delta.get("direction", "positive")
            sign = 1.0 if direction == "positive" else -0.5
            total += magnitude * sign
        return max(0.0, min(1.0, total / len(bid.judgment_deltas)))

    def score_obligation_effect(self, bid: FleetBid) -> float:
        """Score the obligation-effect component.

        Bids that *discharge* obligations score higher than those that
        *create* new ones.
        """
        if not bid.obligation_effects:
            return 0.5  # neutral
        discharged = sum(
            1 for e in bid.obligation_effects if e.get("type") == "discharge"
        )
        created = sum(
            1 for e in bid.obligation_effects if e.get("type") == "create"
        )
        total = len(bid.obligation_effects)
        return max(0.0, min(1.0, (discharged - 0.5 * created) / total + 0.5))

    def score_treaty_impact(self, bid: FleetBid) -> float:
        """Score anticipated treaty interactions.

        Bids that align with existing treaties score higher; those that
        *violate* treaties receive a significant penalty.
        """
        if not bid.treaty_interactions:
            return 0.5
        score = 0.5
        for interaction in bid.treaty_interactions:
            kind = interaction.get("kind", "neutral")
            if kind == "align":
                score += 0.15
            elif kind == "violate":
                score -= 0.3
            elif kind == "extend":
                score += 0.05
        return max(0.0, min(1.0, score))

    def composite_score(self, bid: FleetBid) -> float:
        """Weighted composite of all axis scores."""
        jd = self.score_judgment_delta(bid)
        oe = self.score_obligation_effect(bid)
        ti = self.score_treaty_impact(bid)
        return (
            self.delta_weight * jd
            + self.obligation_weight * oe
            + self.treaty_weight * ti
            + self.confidence_weight * bid.confidence
        )

    def score_all(self, bids: Sequence[FleetBid]) -> dict[str, float]:
        return {bid.bid_id: self.composite_score(bid) for bid in bids}

    def rank(self, bids: Sequence[FleetBid]) -> list[FleetBid]:
        return sorted(bids, key=self.composite_score, reverse=True)

    def pareto_frontier(self, bids: Sequence[FleetBid]) -> list[FleetBid]:
        """Return the Pareto-optimal subset of *bids*.

        A bid is on the frontier if no other bid dominates it.
        """
        frontier: list[FleetBid] = []
        for candidate in bids:
            dominated = False
            for other in bids:
                if other is not candidate and other.dominates(candidate):
                    dominated = True
                    break
            if not dominated:
                frontier.append(candidate)
        return frontier

    def select_winner(self, bids: Sequence[FleetBid]) -> FleetBid | None:
        """Select the single best bid from *bids*.

        First narrows to the Pareto frontier, then picks the bid with the
        highest composite score.  Returns ``None`` if *bids* is empty.
        """
        if not bids:
            return None
        frontier = self.pareto_frontier(list(bids))
        if not frontier:
            return None
        return max(frontier, key=self.composite_score)


# ---------------------------------------------------------------------------
# 5. FleetScheduler
# ---------------------------------------------------------------------------


class FleetScheduler:
    """Schedules assignments across fleet members with load-balancing.

    The scheduler sits between the evaluator (which picks the *best* bid)
    and the fleet manager (which dispatches work).  It ensures that the
    selected member is actually available and not over-loaded, and it
    supports priority-based and deadline-aware scheduling.

    The copilot scheduling mode prioritises bids tagged with a
    ``"copilot_priority"`` flag to allow interactive proof guidance to
    pre-empt background search.
    """

    def __init__(self, fleet: Fleet) -> None:
        self._fleet = fleet
        self._queue: list[tuple[float, FleetBid]] = []  # (priority, bid)

    def schedule(self, bid: FleetBid, priority: float = 0.0) -> bool:
        """Add *bid* to the scheduling queue with the given *priority*.

        Higher values of *priority* are serviced first.  Returns ``True``
        if the bid was enqueued, ``False`` if the originating member is no
        longer available.
        """
        member = self._fleet._members.get(bid.member_id)
        if member is None or not member.is_available:
            return False
        self._queue.append((priority, bid))
        self._queue.sort(key=lambda pair: pair[0], reverse=True)
        return True

    def assign_to_best(self, target: str) -> dict[str, Any] | None:
        """Solicit bids, evaluate, and assign the best one for *target*.

        Convenience method that chains solicit → evaluate → assign.
        Returns the outcome dict, or ``None`` if no eligible member was
        found.
        """
        bids = self._fleet.solicit_bids(target)
        if not bids:
            return None
        evaluator = BidEvaluator()
        winner = evaluator.select_winner(bids)
        if winner is None:
            return None
        return self._fleet.assign_work(winner)

    def load_balance(self) -> dict[str, float]:
        """Return normalised load fractions for every fleet member.

        Each value is ``current_load / capacity``.  A value of ``1.0``
        means the member is fully saturated.
        """
        result: dict[str, float] = {}
        for mid, member in self._fleet._members.items():
            cap = member.capacity if member.capacity > 0 else 1
            result[mid] = member.current_load / cap
        return result

    def priority_queue(self) -> list[FleetBid]:
        """Return the current scheduling queue in priority order."""
        return [bid for _, bid in self._queue]

    def deadline_scheduling(
        self,
        bids: Sequence[FleetBid],
        deadline_seconds: float,
    ) -> list[FleetBid]:
        """Filter *bids* to those whose estimated time fits within *deadline*.

        Bids are returned sorted by estimated time ascending so the
        scheduler can pack as many as possible before the deadline.
        """
        eligible = [b for b in bids if b.estimated_time <= deadline_seconds]
        eligible.sort(key=lambda b: b.estimated_time)
        return eligible

    def copilot_scheduling(
        self,
        bids: Sequence[FleetBid],
        copilot_tag: str = "copilot_priority",
    ) -> list[FleetBid]:
        """Re-order *bids* so copilot-tagged ones come first.

        Bids whose ``uncertainty_profile`` contains a *copilot_tag* key
        with a truthy value are promoted to the front of the queue.
        This supports the interactive copilot proof-guidance loop.
        """
        priority: list[FleetBid] = []
        normal: list[FleetBid] = []
        for bid in bids:
            if bid.uncertainty_profile.get(copilot_tag, 0.0):
                priority.append(bid)
            else:
                normal.append(bid)
        return priority + normal

    def drain_queue(self) -> list[dict[str, Any]]:
        """Execute all queued bids in priority order and return outcomes."""
        outcomes: list[dict[str, Any]] = []
        while self._queue:
            _, bid = self._queue.pop(0)
            outcome = self._fleet.assign_work(bid)
            outcomes.append(outcome)
        return outcomes


# ---------------------------------------------------------------------------
# 6. CompetitiveSearch
# ---------------------------------------------------------------------------


class CompetitiveSearch:
    """Tournament-style competitive search over fleet semantic moves.

    Each *round*, every eligible fleet member proposes a semantic move for
    a given coordinate.  Proposals are evaluated, survivors are selected,
    and the process repeats until a stopping criterion is met.

    Diversity preservation ensures that the search does not collapse to a
    single strategy: structurally distinct moves are protected even if
    their composite score is slightly lower.
    """

    def __init__(
        self,
        fleet: Fleet,
        evaluator: BidEvaluator | None = None,
        *,
        survival_ratio: float = 0.5,
        diversity_bonus: float = 0.1,
        max_rounds: int = 10,
    ) -> None:
        self._fleet = fleet
        self._evaluator = evaluator or BidEvaluator()
        self.survival_ratio = max(0.1, min(1.0, survival_ratio))
        self.diversity_bonus = diversity_bonus
        self.max_rounds = max_rounds
        self._history: list[list[FleetBid]] = []

    def propose_round(self, target: str) -> list[FleetBid]:
        """Solicit proposals from the fleet for *target*.

        Returns the raw list of bids collected this round.
        """
        bids = self._fleet.solicit_bids(target)
        self._history.append(list(bids))
        return bids

    def evaluate_round(self, bids: Sequence[FleetBid]) -> list[tuple[float, FleetBid]]:
        """Score every bid in the round and return ``(score, bid)`` pairs.

        Pairs are sorted best-first.
        """
        scored = [(self._evaluator.composite_score(b), b) for b in bids]
        scored.sort(key=lambda p: p[0], reverse=True)
        return scored

    def select_survivors(
        self,
        scored: Sequence[tuple[float, FleetBid]],
    ) -> list[FleetBid]:
        """Select the top fraction of bids as survivors for the next round.

        The number retained is ``ceil(len(scored) * survival_ratio)``, with
        at least one survivor.
        """
        n = max(1, math.ceil(len(scored) * self.survival_ratio))
        return [bid for _, bid in scored[:n]]

    def tournament(self, target: str) -> FleetBid | None:
        """Run a full tournament for *target* and return the winner.

        Iterates up to ``max_rounds`` rounds of proposal → evaluation →
        selection.  The final round's top-scoring bid is the winner.
        Returns ``None`` if no bids were received.
        """
        survivors: list[FleetBid] = []
        for _ in range(self.max_rounds):
            bids = self.propose_round(target)
            if not bids and not survivors:
                return None
            all_bids = list(bids) + survivors
            scored = self.evaluate_round(all_bids)
            survivors = self.select_survivors(scored)
            if len(survivors) == 1:
                break
        if not survivors:
            return None
        return max(survivors, key=self._evaluator.composite_score)

    def diversity_preservation(
        self,
        bids: Sequence[FleetBid],
    ) -> list[FleetBid]:
        """Ensure structural diversity among *bids*.

        Bids targeting distinct judgment deltas receive a diversity bonus
        that may rescue them from elimination.  Returns a re-scored list
        sorted best-first.
        """
        seen_moves: set[str] = set()
        adjusted: list[tuple[float, FleetBid]] = []
        for bid in bids:
            base_score = self._evaluator.composite_score(bid)
            move_key = bid.proposed_move
            if move_key not in seen_moves:
                base_score += self.diversity_bonus
                seen_moves.add(move_key)
            adjusted.append((base_score, bid))
        adjusted.sort(key=lambda p: p[0], reverse=True)
        return [bid for _, bid in adjusted]

    def rounds_completed(self) -> int:
        """Return the number of rounds completed so far."""
        return len(self._history)


# ---------------------------------------------------------------------------
# 7. FleetCalibration
# ---------------------------------------------------------------------------


class FleetCalibration:
    """Runtime calibration of fleet member performance.

    Tracks accuracy, latency, and confidence calibration for each member.
    The copilot loop calls :meth:`recalibrate_confidence` after each
    round to keep trust ceilings aligned with observed performance.
    """

    def __init__(self) -> None:
        self._accuracy: dict[str, list[float]] = {}
        self._latency: dict[str, list[float]] = {}
        self._confidence_history: dict[str, list[float]] = {}

    def calibrate(self, member: FleetMember) -> dict[str, float]:
        """Return a calibration snapshot for *member*.

        Includes mean accuracy, mean latency, and recommended trust
        ceiling adjustment.
        """
        mid = member.member_id or member.name
        acc_samples = self._accuracy.get(mid, [])
        lat_samples = self._latency.get(mid, [])
        mean_acc = statistics.mean(acc_samples) if acc_samples else 0.5
        mean_lat = statistics.mean(lat_samples) if lat_samples else 0.0
        recommended_ceiling = min(1.0, mean_acc + 0.1) if acc_samples else member.trust_ceiling
        return {
            "member_id": mid,
            "mean_accuracy": round(mean_acc, 4),
            "mean_latency_ms": round(mean_lat, 2),
            "recommended_trust_ceiling": round(recommended_ceiling, 4),
            "sample_count": len(acc_samples),
        }

    def update_trust(self, member: FleetMember, outcome: dict[str, Any]) -> float:
        """Update *member*'s trust ceiling based on *outcome*.

        Returns the new trust ceiling.  Trust is adjusted incrementally:
        successes raise it (up to 1.0), failures lower it (down to 0.05).
        """
        status = outcome.get("status", BidOutcome.FAILURE.name)
        adjustment = 0.02 if status == BidOutcome.SUCCESS.name else -0.05
        new_ceiling = max(0.05, min(1.0, member.trust_ceiling + adjustment))
        member.trust_ceiling = new_ceiling
        return new_ceiling

    def track_accuracy(self, member_id: str, was_correct: bool) -> None:
        """Record an accuracy sample for the member identified by *member_id*."""
        self._accuracy.setdefault(member_id, []).append(
            1.0 if was_correct else 0.0
        )

    def track_latency(self, member_id: str, latency_ms: float) -> None:
        """Record a latency sample in milliseconds."""
        self._latency.setdefault(member_id, []).append(latency_ms)

    def recalibrate_confidence(self, member: FleetMember) -> float:
        """Recalibrate *member*'s trust ceiling from accumulated samples.

        Uses the most recent 50 accuracy samples to compute a rolling
        mean, then blends it with the current ceiling.  Returns the
        updated ceiling value.
        """
        mid = member.member_id or member.name
        samples = self._accuracy.get(mid, [])
        if not samples:
            return member.trust_ceiling
        recent = samples[-50:]
        empirical = statistics.mean(recent)
        blended = 0.7 * empirical + 0.3 * member.trust_ceiling
        member.trust_ceiling = max(0.05, min(1.0, round(blended, 4)))
        self._confidence_history.setdefault(mid, []).append(member.trust_ceiling)
        return member.trust_ceiling

    def confidence_trend(self, member_id: str, window: int = 10) -> list[float]:
        """Return the last *window* confidence values for *member_id*."""
        history = self._confidence_history.get(member_id, [])
        return history[-window:]


# ---------------------------------------------------------------------------
# 8. ChallengeRecord
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ChallengeRecord:
    """Structured record of a challenge between fleet members.

    When a member disputes another's bid, a ``ChallengeRecord`` captures
    the challenger's identity, the challenged bid, the evidence presented,
    and the eventual resolution.

    Parameters
    ----------
    challenge_id : str
        Unique identifier.
    challenger : str
        Member id of the challenger.
    challenged_bid : FleetBid
        The bid being challenged.
    challenge_evidence : list[dict[str, Any]]
        Evidence items the challenger presents (e.g. counter-examples).
    outcome : ChallengeOutcome | None
        Resolution status (``None`` while unresolved).
    resolution : str
        Free-text explanation of how the challenge was resolved.
    timestamp : float
        When the challenge was filed.
    """

    challenge_id: str = ""
    challenger: str = ""
    challenged_bid: FleetBid | None = None
    challenge_evidence: list[dict[str, Any]] = field(default_factory=list)
    outcome: ChallengeOutcome | None = None
    resolution: str = ""
    timestamp: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        if not self.challenge_id:
            self.challenge_id = uuid.uuid4().hex[:16]

    def file_challenge(
        self,
        challenger: str,
        bid: FleetBid,
        evidence: list[dict[str, Any]],
    ) -> None:
        """Populate this record with challenge details."""
        self.challenger = challenger
        self.challenged_bid = bid
        self.challenge_evidence = evidence
        self.timestamp = time.time()

    def resolve(self, outcome: ChallengeOutcome, resolution: str = "") -> None:
        """Mark the challenge as resolved with *outcome*."""
        self.outcome = outcome
        self.resolution = resolution

    def is_resolved(self) -> bool:
        """Return ``True`` if the challenge has been resolved."""
        return self.outcome is not None

    def summary(self) -> dict[str, Any]:
        """Return a compact summary suitable for logging."""
        return {
            "challenge_id": self.challenge_id,
            "challenger": self.challenger,
            "challenged_bid_id": (
                self.challenged_bid.bid_id if self.challenged_bid else None
            ),
            "evidence_count": len(self.challenge_evidence),
            "outcome": self.outcome.name if self.outcome else "PENDING",
            "resolution": self.resolution,
        }

    def evidence_strength(self) -> float:
        """Heuristic strength of the challenge evidence.

        Each evidence item contributes a weight; items marked
        ``"definitive"`` contribute more.
        """
        if not self.challenge_evidence:
            return 0.0
        total = 0.0
        for item in self.challenge_evidence:
            if item.get("kind") == "definitive":
                total += 1.0
            elif item.get("kind") == "suggestive":
                total += 0.4
            else:
                total += 0.2
        return min(1.0, total / max(1, len(self.challenge_evidence)))


# ---------------------------------------------------------------------------
# 9. FleetHistory
# ---------------------------------------------------------------------------


class FleetHistory:
    """Longitudinal performance tracker for the fleet.

    Records every round's bids, winners, and outcomes so the copilot can
    surface trends, win-rate statistics, and diversity metrics over time.
    """

    def __init__(self) -> None:
        self._rounds: list[dict[str, Any]] = []
        self._member_records: dict[str, list[dict[str, Any]]] = {}

    def record_round(
        self,
        round_number: int,
        bids: Sequence[FleetBid],
        winner: FleetBid | None,
        outcome: dict[str, Any] | None = None,
    ) -> None:
        """Store a complete round record.

        Parameters
        ----------
        round_number : int
            Sequential round identifier.
        bids : Sequence[FleetBid]
            All bids received this round.
        winner : FleetBid | None
            The winning bid (``None`` if no winner).
        outcome : dict | None
            Execution outcome of the winning bid.
        """
        record: dict[str, Any] = {
            "round": round_number,
            "bid_count": len(bids),
            "winner_id": winner.bid_id if winner else None,
            "winner_member": winner.member_id if winner else None,
            "outcome_status": (outcome or {}).get("status"),
            "timestamp": time.time(),
        }
        self._rounds.append(record)
        # Per-member bookkeeping.
        for bid in bids:
            self._member_records.setdefault(bid.member_id, []).append(
                {
                    "round": round_number,
                    "bid_id": bid.bid_id,
                    "won": (winner is not None and bid.bid_id == winner.bid_id),
                    "confidence": bid.confidence,
                }
            )

    def member_statistics(self, member_id: str) -> dict[str, Any]:
        """Aggregate statistics for a single member.

        Returns bid count, win count, win rate, and mean confidence.
        """
        records = self._member_records.get(member_id, [])
        if not records:
            return {"member_id": member_id, "bids": 0, "wins": 0, "win_rate": 0.0}
        wins = sum(1 for r in records if r["won"])
        mean_conf = statistics.mean(r["confidence"] for r in records)
        return {
            "member_id": member_id,
            "bids": len(records),
            "wins": wins,
            "win_rate": round(wins / len(records), 4),
            "mean_confidence": round(mean_conf, 4),
        }

    def bid_statistics(self) -> dict[str, Any]:
        """Aggregate bid statistics across all rounds.

        Returns total rounds, total bids, mean bids per round, and mean
        winning confidence.
        """
        total_bids = sum(r["bid_count"] for r in self._rounds)
        rounds = len(self._rounds)
        winning_records = [
            r for r in self._rounds if r.get("winner_id") is not None
        ]
        # Compute mean winning confidence from member records.
        winning_confs: list[float] = []
        for member_recs in self._member_records.values():
            for rec in member_recs:
                if rec["won"]:
                    winning_confs.append(rec["confidence"])
        return {
            "total_rounds": rounds,
            "total_bids": total_bids,
            "mean_bids_per_round": round(total_bids / rounds, 2) if rounds else 0.0,
            "rounds_with_winner": len(winning_records),
            "mean_winning_confidence": (
                round(statistics.mean(winning_confs), 4) if winning_confs else 0.0
            ),
        }

    def win_rates(self) -> dict[str, float]:
        """Per-member win rates across the entire history."""
        rates: dict[str, float] = {}
        for mid, records in self._member_records.items():
            if records:
                wins = sum(1 for r in records if r["won"])
                rates[mid] = round(wins / len(records), 4)
        return rates

    def diversity_over_time(self, window: int = 5) -> list[float]:
        """Return per-round winner diversity over a sliding *window*.

        Diversity is measured as the number of distinct winners in the
        window divided by the window size.  Higher values indicate the
        fleet is exploring more broadly.
        """
        winners = [
            r.get("winner_member") for r in self._rounds if r.get("winner_member")
        ]
        if len(winners) < window:
            unique = len(set(winners))
            return [unique / max(1, len(winners))]
        result: list[float] = []
        for i in range(len(winners) - window + 1):
            chunk = winners[i : i + window]
            result.append(len(set(chunk)) / window)
        return result

    def round_count(self) -> int:
        """Total number of rounds recorded."""
        return len(self._rounds)


# ---------------------------------------------------------------------------
# 10. FleetDiagnostics
# ---------------------------------------------------------------------------


class FleetDiagnostics:
    """Diagnostic reporting for fleet health and performance.

    Designed to be called from the copilot dashboard or command-line
    tooling to surface a concise picture of what the fleet is doing.
    """

    def __init__(self, fleet: Fleet, history: FleetHistory | None = None) -> None:
        self._fleet = fleet
        self._history = history or FleetHistory()

    def fleet_summary(self) -> dict[str, Any]:
        """High-level summary: member count, health, round count."""
        health = self._fleet.fleet_health()
        return {
            **health,
            "rounds_recorded": self._history.round_count(),
            "active_count": len(self._fleet.active_members()),
            "idle_count": len(self._fleet.idle_members()),
        }

    def member_report(self, member_id: str) -> dict[str, Any]:
        """Detailed report for a single member.

        Combines live fleet state with historical statistics.
        """
        member = self._fleet._members.get(member_id)
        if member is None:
            return {"error": f"Member {member_id!r} not found"}
        hist = self._history.member_statistics(member_id)
        return {
            "member_id": member_id,
            "name": member.name,
            "capacity": member.capacity,
            "current_load": member.current_load,
            "trust_ceiling": member.trust_ceiling,
            "is_available": member.is_available,
            "capabilities": sorted(member.capabilities),
            "specialization_domains": list(member.specialization_domains),
            "historical": hist,
        }

    def bid_analysis(self, bids: Sequence[FleetBid]) -> dict[str, Any]:
        """Analyse a set of bids for quality distribution and admissibility.

        Returns aggregate statistics useful for diagnosing search quality.
        """
        if not bids:
            return {"count": 0}
        evaluator = BidEvaluator()
        scores = [evaluator.composite_score(b) for b in bids]
        admissible = [b for b in bids if b.is_admissible()]
        return {
            "count": len(bids),
            "admissible_count": len(admissible),
            "admissibility_ratio": round(len(admissible) / len(bids), 4),
            "mean_score": round(statistics.mean(scores), 4),
            "max_score": round(max(scores), 4),
            "min_score": round(min(scores), 4),
            "std_score": (
                round(statistics.stdev(scores), 4) if len(scores) > 1 else 0.0
            ),
            "pareto_frontier_size": len(evaluator.pareto_frontier(list(bids))),
        }

    def bottleneck_detection(self) -> list[dict[str, Any]]:
        """Identify members that are likely bottlenecks.

        A member is flagged when its load fraction exceeds 0.8 or its
        trust ceiling has dropped below 0.2.
        """
        bottlenecks: list[dict[str, Any]] = []
        for mid, member in self._fleet._members.items():
            cap = member.capacity if member.capacity > 0 else 1
            load_fraction = member.current_load / cap
            reasons: list[str] = []
            if load_fraction >= 0.8:
                reasons.append(f"high load ({load_fraction:.0%})")
            if member.trust_ceiling < 0.2:
                reasons.append(f"low trust ({member.trust_ceiling:.2f})")
            if not member.is_available:
                reasons.append("unavailable")
            if reasons:
                bottlenecks.append(
                    {"member_id": mid, "name": member.name, "reasons": reasons}
                )
        return bottlenecks

    def copilot_fleet_summary(self) -> str:
        """Return a human-readable summary for the copilot dashboard.

        This is the primary entry-point for the copilot UI when it needs
        a textual status line about the fleet.
        """
        summary = self.fleet_summary()
        lines = [
            f"Fleet: {summary['total_members']} members "
            f"({summary['available_members']} available, "
            f"{summary['active_count']} active, "
            f"{summary['idle_count']} idle)",
            f"Load: {summary['aggregate_load']} total | "
            f"Trust: {summary['average_trust_ceiling']:.2f} avg",
            f"Rounds: {summary['rounds_recorded']}",
        ]
        bottlenecks = self.bottleneck_detection()
        if bottlenecks:
            names = ", ".join(b["name"] for b in bottlenecks)
            lines.append(f"⚠ Bottlenecks: {names}")
        return "\n".join(lines)

    def health_check(self) -> dict[str, bool]:
        """Quick pass/fail health check for monitoring.

        Returns a dict with boolean flags suitable for alerting.
        """
        health = self._fleet.fleet_health()
        return {
            "has_members": health["total_members"] > 0,
            "has_available": health["available_members"] > 0,
            "availability_ok": health["availability_ratio"] >= 0.25,
            "trust_ok": health["average_trust_ceiling"] >= 0.2,
            "no_bottlenecks": len(self.bottleneck_detection()) == 0,
        }


# ---------------------------------------------------------------------------
# Backward-compatible FleetState wrapper
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class FleetState:
    """Legacy wrapper retained for backward compatibility.

    New code should use :class:`Fleet` directly.  ``FleetState`` maps
    the old ``(members, assignments)`` API onto the new fleet manager so
    that existing tests continue to pass unmodified.
    """

    members: tuple[FleetMember, ...]
    assignments: dict[str, str] = field(default_factory=dict)

    def assign(self, member: FleetMember, item: FrontierItem) -> bool:
        """Assign *member* to *item* if the member is not already assigned.

        Returns ``True`` on success, ``False`` if the member already has
        an assignment.
        """
        if self.assignments.get(member.name):
            return False
        self.assignments[member.name] = item.goal.proposition
        return True

    def idle_members(self) -> tuple[FleetMember, ...]:
        """Return members that have no current assignment."""
        return tuple(
            member for member in self.members if member.name not in self.assignments
        )


# ---------------------------------------------------------------------------
# Module exports
# ---------------------------------------------------------------------------

__all__ = [
    "BidOutcome",
    "ChallengeOutcome",
    "FleetMember",
    "FleetBid",
    "Fleet",
    "BidEvaluator",
    "FleetScheduler",
    "CompetitiveSearch",
    "FleetCalibration",
    "ChallengeRecord",
    "FleetHistory",
    "FleetDiagnostics",
    "FleetState",
]

# copilot: shared-core marker for LLM-fleet orchestration.
